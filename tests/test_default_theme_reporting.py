"""What the app and the editor say about the panel's own default theme: the same theme the
panel shows, from the running app where there is one."""

import threading

import pytest
from test_autostart import FakeTasks
from test_editor_server import request
from test_service import FakeRegistry, wait_for
from test_theme_defaults import BARS, NINE

from libre_panel.autostart import Autostart
from libre_panel.devices import turzx_usb
from libre_panel.devices.models import MODELS
from libre_panel.editor.server import make_server
from libre_panel.service import BackgroundApp
from libre_panel.theme.defaults import default_theme


def write_bar_config(home, theme_line=""):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        f'{theme_line}fps = 5\n[device]\nmodel = "turing-9.2-usb"\ndriver = "virtual"\n'
        f'output = "{(home / "frame.png").as_posix()}"\n',
        encoding="utf-8",
    )


@pytest.fixture
def bar_app(isolated_home):
    """The background app on a configured 9.2", with no theme in its config.toml."""
    write_bar_config(isolated_home)
    app = BackgroundApp(
        port=0, autostart=Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    )
    url = app.start()
    assert wait_for(lambda: app.panel.state()["state"] == "showing", 20)
    yield app, int(url.rsplit(":", 1)[1].strip("/"))
    app.quit()
    app.shutdown()


def test_the_app_says_which_theme_is_the_panels_default(bar_app):
    app, port = bar_app
    state = app.panel.state()
    assert state["theme"] == "lumen" and state["default_theme"] == "lumen"
    assert app.active_theme() == "lumen"  # the tray ticks it
    status, pages = request(port, "GET", "/api/pages")
    normal = next(page for page in pages["pages"] if page["id"] == "normal")
    assert status == 200 and normal["theme"] == "lumen" and pages["default"] == "lumen"
    assert pages["chosen"] is False and pages["shown"] == "lumen"  # a first start
    status, themes = request(port, "GET", "/api/plugins/control/themes")
    assert themes["normal"] == "lumen" and themes["shown"] == "lumen"
    status, panel = request(port, "GET", "/api/plugins/control/panel")
    assert panel["panel"]["default_theme"] == "lumen"


def test_a_theme_in_config_toml_is_what_the_app_and_the_editor_say(bar_app, isolated_home):
    app, port = bar_app
    write_bar_config(isolated_home, 'theme = "libre-default"\n')  # what 0.4 wrote
    assert app.active_theme() == "libre-default"
    status, pages = request(port, "GET", "/api/pages")
    assert pages["pages"][0]["theme"] == "libre-default" and pages["chosen"] is True
    assert pages["default"] == "lumen"  # what the panel would show without it
    assert request(port, "GET", "/api/plugins/control/themes")[1]["normal"] == "libre-default"
    assert wait_for(lambda: app.panel.state()["theme"] == "libre-default")


def test_the_editor_alone_works_the_default_out_from_the_config(isolated_home, monkeypatch):
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, pages = request(port, "GET", "/api/pages")  # nothing is known of the panel
        assert pages["pages"][0]["theme"] == "libre-default"
        write_bar_config(isolated_home)
        status, pages = request(port, "GET", "/api/pages")
        assert pages["pages"][0]["theme"] == "lumen" and pages["default"] == "lumen"
        assert pages["chosen"] is False and pages["shown"] is None
        monkeypatch.setattr(turzx_usb, "present_model", lambda: NINE)  # or a bar on the bus
        (isolated_home / "config.toml").write_text("fps = 5\n", encoding="utf-8")
        assert request(port, "GET", "/api/pages")[1]["pages"][0]["theme"] == "lumen"
        (isolated_home / "config.toml").write_text('theme = "slate"\n', encoding="utf-8")
        status, pages = request(port, "GET", "/api/pages")
        assert pages["pages"][0]["theme"] == "slate" and pages["default"] == "lumen"
    finally:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


def test_the_editor_lists_each_panels_default_with_the_models(isolated_home):
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, specs = request(server.server_address[1], "GET", "/api/specs")
        defaults = {model["id"]: model["default_theme"] for model in specs["models"]}
        assert defaults == {model.id: default_theme(model) for model in MODELS}
        assert {id for id, theme in defaults.items() if theme == "lumen"} == set(BARS)
    finally:
        server.shutdown()
        server.server_close()
        server.editor_state.close()

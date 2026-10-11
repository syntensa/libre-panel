"""The editor's built-in pages, Control and Autopilot: that they are listed and served, and
what their routes answer in a plain ``libre-panel editor`` and in the background app."""

import io
import threading

import pytest
from PIL import Image
from test_autostart import FakeTasks
from test_diagnostics import quiet_theme
from test_editor_server import request
from test_service import FakeRegistry, wait_for, write_config

from libre_panel import i18n
from libre_panel.autostart import Autostart
from libre_panel.config import ConfigError, load_config, log_file, set_config_value
from libre_panel.editor.server import make_server
from libre_panel.pages.autopilot import Autopilot
from libre_panel.pages.base import ConsolePage, Refused
from libre_panel.pages.control import Control, encode, service_state, with_service
from libre_panel.service import BackgroundApp


@pytest.fixture
def editor():
    """A plain ``libre-panel editor``: no panel, no services."""
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    server.editor_state.close()


@pytest.fixture
def running(isolated_home):
    """The background app with its panel (a PNG file), and the port of its editor."""
    write_config(isolated_home)
    app = BackgroundApp(
        port=0, autostart=Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    )
    url = app.start()
    assert wait_for(lambda: app.panel.state()["state"] == "showing")
    yield app, int(url.rsplit(":", 1)[1].strip("/"))
    app.quit()
    app.shutdown()


def get(port, path):
    return request(port, "GET", f"/api/plugins/{path}")


def post(port, path, body=None):
    return request(port, "POST", f"/api/plugins/{path}", body or {})


# -- they are pages like a plugin's ------------------------------------------------------------


def test_the_pages_are_built_in_and_served_without_any_plugin(editor):
    status, pages = request(editor, "GET", "/api/plugins/pages")
    titles = {page["id"]: page["title"] for page in pages}
    assert titles == {"autopilot": "Autopilot", "control": "Control"}
    status, html = request(editor, "GET", "/plugins/control/")
    assert status == 200 and b"console.js" in html and b"editor.css" in html
    for page, script in (("control", "control.js"), ("autopilot", "autopilot.js")):
        assert request(editor, "GET", f"/plugins/{page}/{script}")[0] == 200
    assert request(editor, "GET", "/plugins/autopilot/")[1] == html  # one shell for all
    for name in ("console.js", "common.js", "console.css"):
        assert request(editor, "GET", f"/plugins/control/{name}")[0] == 200
    assert request(editor, "GET", "/plugins/control/base.py")[0] == 404  # code is not served
    i18n.set_language("de")
    titles = {p["id"]: p["title"] for p in request(editor, "GET", "/api/plugins/pages")[1]}
    assert titles["control"] == "Steuerung"


def test_the_pages_use_only_files_the_editor_serves():
    """Everything the shell loads is in the folder next to the pages' code (and listed in
    pyproject.toml's package data, so a pip install has it)."""
    import re
    import tomllib
    from pathlib import Path

    folder = Control.folder()
    names = {path.name for path in folder.iterdir()}
    assert {
        "index.html",
        "console.js",
        "common.js",
        "control.js",
        "autopilot.js",
        "console.css",
    } <= names
    for page in folder.glob("*.js"):
        for imported in re.findall(r'from "\./([a-z.]+)"', page.read_text(encoding="utf-8")):
            assert imported in names
    project = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(project.read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    assert "pages/web/*" in data["libre_panel"] and "editor/static/*" in data["libre_panel"]


def test_an_editor_page_can_answer_with_a_file(plugin_folder, editor):
    source = """
from libre_panel.plugins import EditorPage


class Picture(EditorPage):
    api = 1
    title = {"en": "Picture"}

    def handle(self, method, path, query, body):
        if path == "png":
            return 200, b"\\x89PNG...", "image/png"
        if path == "raw":
            return 200, b"bytes"
        return 200, {"json": True}
"""
    files = {"page/index.html": "<!doctype html><title>x</title>"}
    plugin_folder(source, {"libre_panel.editor_pages": {"picture": "Picture"}}, files=files)
    assert get(editor, "picture/png") == (200, b"\x89PNG...")
    assert get(editor, "picture/raw") == (200, b"bytes")  # no type given: still the bytes
    assert get(editor, "picture/other") == (200, {"json": True})  # and JSON as before


def test_service_state_words():
    assert service_state(None) == ("off", "")
    assert service_state(None, False) == ("off", "")
    assert service_state(None, True) == ("pending", "")  # written to config.toml, not applied yet
    assert service_state("running") == ("running", "")  # no word from config.toml: the manager's
    assert service_state("running", True) == ("running", "")
    assert service_state("running", False) == ("pending", "")  # about to be stopped
    assert service_state("stopped", True) == ("stopped", "")
    assert service_state("not installed", True) == ("not-installed", "")
    assert service_state("failed: no such option", True) == ("failed", "no such option")


def test_a_service_goes_into_the_enabled_list_once():
    assert with_service(["notices"], "game", True) == ["notices", "game"]
    assert with_service(["game", "notices"], "game", True) == ["game", "notices"]  # as it was
    assert with_service(["game", "notices"], "game", False) == ["notices"]
    assert with_service([], "game", False) == []


def test_a_page_answers_by_its_routes_and_a_refusal_is_an_answer():
    class Demo(ConsolePage):
        routes = {("GET", "one"): "one", ("POST", "two"): "two", ("GET", "three"): "three"}

        def one(self, request):
            return {"n": request.integer("n", 5, 1, 9), "word": request.param("w", "none")}

        def two(self, request):
            return 201, {"text": request.text("text")}

        def three(self, request):
            raise Refused("nope", 409)

    page = Demo(None)
    assert page.handle("GET", "/one/", {"n": ["99"], "w": ["x"]}, None) == (
        200,
        {"n": 9, "word": "x"},
    )
    assert page.handle("GET", "one", {"n": ["many"]}, None) == (200, {"n": 5, "word": "none"})
    assert page.handle("POST", "two", {}, {"text": "hi"}) == (201, {"text": "hi"})
    status, answer = page.handle("POST", "two", {}, {"text": ""})
    assert status == 400 and "text" in answer["error"]
    assert page.handle("GET", "three", {}, None) == (409, {"error": "nope"})
    assert page.handle("GET", "four", {}, None)[0] == 404
    assert page.handle("POST", "one", {}, {})[0] == 404  # a route is for one method


def test_the_console_api_in_the_docs_is_the_one_the_pages_answer():
    """docs/ARCHITECTURE.md lists the routes a screen can call; this keeps the list true."""
    import re
    from pathlib import Path

    docs = Path(__file__).resolve().parent.parent / "docs" / "ARCHITECTURE.md"
    documented = re.findall(r"^\| `(GET|POST) ([a-z/]+)`", docs.read_text("utf-8"), re.MULTILINE)
    built = {
        (method, f"{page}/{path}")
        for page, cls in (("control", Control), ("autopilot", Autopilot))
        for method, path in cls.routes
    }
    assert len(documented) == len(set(documented)) and set(documented) == built


# -- Control in a plain editor -----------------------------------------------------------------


def test_control_in_a_plain_editor_says_what_is_missing_and_keeps_what_works(editor, isolated_home):
    write_config(isolated_home, theme="slate")
    status, everything = get(editor, "control/status")
    assert status == 200 and everything["background"] is False and everything["panel"] is None
    assert everything["sources"] == [] and everything["config_problem"] == ""
    assert set(everything) == {  # what docs/ARCHITECTURE.md promises
        "background", "panel", "config_problem", "services", "sources", "probes", "themes", "log",
    }  # fmt: skip
    services = {service["name"]: service for service in everything["services"]}
    assert {"autopilot", "devices", "game", "notices"} <= set(services)
    assert {service["state"] for service in services.values()} == {"unknown"}  # nothing runs here
    assert not any(service["enabled"] or service["restartable"] for service in services.values())
    assert all(service["installed"] for service in services.values())
    themes = everything["themes"]
    assert themes["normal"] == "slate" and "libre-default" in themes["available"]
    assert themes["game"] == "" and themes["shown"] is None and themes["mode"] is None
    assert [probe["kind"] for probe in everything["probes"]] == [
        "volume", "music", "warning", "panel", "device", "game",
    ]  # fmt: skip
    # the single routes tell the same, each in an object with names
    assert get(editor, "control/panel") == (200, {"background": False, "panel": None})
    assert get(editor, "control/sources") == (200, {"background": False, "sources": []})
    assert get(editor, "control/services")[1] == {
        "background": False, "services": everything["services"], "config_problem": "",
    }  # fmt: skip
    assert get(editor, "control/probes")[1] == {"probes": everything["probes"]}
    assert get(editor, "control/themes")[1] == {**themes, "config_problem": ""}
    status, data = get(editor, "control/live")
    assert status == 404 and "no picture" in data["error"]
    assert get(editor, "control/log?lines=5")[1] == {
        "lines": [], "path": str(log_file()), "exists": False,
    }  # fmt: skip
    assert everything["log"] == {"path": str(log_file()), "exists": False}
    status, answer = post(editor, "control/probe", {"kind": "volume"})
    assert status == 200 and answer["shown"] is False and answer["reason"] == "no-panel"
    assert set(answer) == {"kind", "name", "shown", "reason", "message"}
    assert "no panel" in answer["message"]
    status, answer = post(editor, "control/services/restart", {"service": "notices"})
    assert status == 404 and "does not run the services" in answer["error"]


def test_control_tells_a_config_it_cannot_read(editor, isolated_home):
    (isolated_home).mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text("fps = 'fast'\n", encoding="utf-8")
    status, data = get(editor, "control/status")
    assert status == 200 and "fps" in data["config_problem"] and data["themes"]["normal"] is None
    for route in ("services", "themes"):  # the parts name the problem too
        status, data = get(editor, f"control/{route}")
        assert status == 200 and "fps" in data["config_problem"], route
    assert get(editor, "control/themes")[1]["normal"] is None
    status, answer = post(
        editor, "control/services/enable", {"service": "notices", "enabled": True}
    )
    assert status == 409 and "fps" in answer["error"]  # nothing is guessed, nothing is written


def test_the_log_is_read_from_the_end(editor):
    path = log_file()
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"line {n}\n" for n in range(500)), encoding="utf-8")
    status, data = get(editor, "control/log?lines=3")
    assert data["lines"] == ["line 497", "line 498", "line 499"] and data["exists"] is True
    assert len(get(editor, "control/log?lines=nonsense")[1]["lines"]) == 60  # the default


def test_the_theme_per_mode_goes_to_config_toml(editor, isolated_home):
    write_config(isolated_home, theme="slate")
    assert post(editor, "control/theme", {"mode": "normal", "theme": "orbit"})[0] == 200
    assert post(editor, "control/theme", {"mode": "game", "theme": "column"})[0] == 200
    config = load_config()
    assert config.theme == "orbit" and config.modes["game"].theme == "column"
    assert "[modes.game]" in (isolated_home / "config.toml").read_text(encoding="utf-8")
    assert get(editor, "control/status")[1]["themes"]["game"] == "column"
    assert post(editor, "control/theme", {"mode": "game", "theme": ""})[0] == 200  # the normal one
    assert load_config().modes["game"].theme == "" and load_config().theme == "orbit"
    before = (isolated_home / "config.toml").read_text(encoding="utf-8")
    for body in (
        {"mode": "normal", "theme": "no-such-theme"},
        {"mode": "normal", "theme": ""},  # the normal mode needs one
        {"mode": "party", "theme": "orbit"},
        {"mode": "game", "theme": 7},
        {"theme": "orbit"},
    ):
        status, answer = post(editor, "control/theme", body)
        assert status == 400 and answer["error"], body
    assert (isolated_home / "config.toml").read_text(encoding="utf-8") == before


def test_the_picture_is_made_at_the_asked_width_and_format():
    frame = Image.new("RGB", (1920, 480), (10, 80, 160))
    picture = Image.open(io.BytesIO(encode(frame, 960)))
    assert picture.format == "JPEG" and picture.size == (960, 240)
    assert Image.open(io.BytesIO(encode(frame, 4000))).size == (1920, 480)  # never enlarged
    assert Image.open(io.BytesIO(encode(frame.convert("RGBA"), 480))).size == (480, 120)
    png = Image.open(io.BytesIO(encode(frame, 480, "png")))
    assert png.format == "PNG" and png.size == (480, 120)
    assert png.getpixel((5, 5)) == (10, 80, 160)  # PNG keeps the colours as they are


# -- Control in the background app ---------------------------------------------------------------


def test_control_shows_the_panel_its_sources_and_its_services(running, isolated_home):
    app, port = running
    set_config_value("services.enabled", ["notices"])
    assert wait_for(lambda: "notices" in app.services.running)
    assert wait_for(lambda: app.panel.state()["stats"], 15)  # the first numbers
    status, data = get(port, "control/status")
    assert status == 200 and data["background"] is True
    assert data["panel"]["state"] == "showing" and data["panel"]["stats"]["target_fps"] == 5
    sources = {source["name"]: source for source in data["sources"]}
    assert {"load", "psutil", "sky", "services"} <= set(sources)
    assert sources["psutil"]["readings"] > 5 and sources["psutil"]["problem"] is False
    services = {service["name"]: service for service in data["services"]}
    assert services["notices"] == {
        "name": "notices", "state": "running", "detail": "", "enabled": True,
        "installed": True, "restartable": True,
    }  # fmt: skip
    assert services["game"]["state"] == "off" and services["game"]["restartable"] is False
    assert services["game"]["enabled"] is False
    assert data["themes"]["shown"] == "libre-default" and data["themes"]["mode"] is None

    # the routes of the single parts: the same, each on its own
    status, panel = get(port, "control/panel")
    assert status == 200 and panel["background"] is True
    assert panel["panel"]["state"] == "showing" and panel["panel"]["stats"]["fps"] > 0
    assert set(panel["panel"]) == {"state", "detail", "target", "theme", "frames", "mode", "stats"}
    assert get(port, "control/services")[1]["services"] == data["services"]
    assert get(port, "control/themes")[1]["shown"] == "libre-default"
    sources = get(port, "control/sources")[1]
    assert sources["background"] is True
    assert [s["name"] for s in sources["sources"]] == [s["name"] for s in data["sources"]]
    assert all(set(s) == {"name", "readings", "status", "details", "hint", "problem"}
               for s in sources["sources"])  # fmt: skip


def test_the_live_picture_is_the_frame_on_the_panel(running):
    app, port = running
    assert wait_for(lambda: app.plugin_host.shown is not None)
    conn = request(port, "GET", "/api/plugins/control/live?w=320")
    assert conn[0] == 200 and conn[1][:3] == b"\xff\xd8\xff"  # a JPEG
    picture = Image.open(io.BytesIO(conn[1]))
    assert picture.size[0] == 320
    assert picture.size[1] == round(
        app.plugin_host.shown.height * 320 / app.plugin_host.shown.width
    )
    full = Image.open(io.BytesIO(request(port, "GET", "/api/plugins/control/live")[1]))
    assert full.size[0] == min(960, app.plugin_host.shown.width)
    status, png = get(port, "control/live?w=400&format=png")
    assert status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).size[0] == 400
    assert get(port, "control/live?format=PNG")[1][:4] == b"\x89PNG"  # in any case
    status, answer = get(port, "control/live?format=gif")
    assert status == 400 and "jpeg or png" in answer["error"]
    app.panel.pause()  # a paused panel has no picture
    assert wait_for(lambda: app.plugin_host.shown is None)
    assert get(port, "control/live")[0] == 404
    assert get(port, "control/live?format=png")[0] == 404


def test_a_live_picture_is_made_once_for_a_frame(isolated_home):
    from types import SimpleNamespace

    from libre_panel.plugins import PageContext, PluginHost

    host = PluginHost()
    host.shown = Image.new("RGB", (640, 160), "navy")
    page = Control(PageContext(SimpleNamespace(plugin_host=host)))
    first = page.handle("GET", "live", {}, None)
    assert page.handle("GET", "live", {}, None)[1] is first[1]  # the same bytes, not made again
    host.shown = Image.new("RGB", (640, 160), "navy")  # the next frame
    assert page.handle("GET", "live", {}, None)[1] is not first[1]
    assert page.handle("GET", "live", {"w": ["200"]}, None)[1] is not first[1]  # another width
    png = page.handle("GET", "live", {"format": ["png"]}, None)
    assert png[2] == "image/png" and png[1] is not first[1] and first[2] == "image/jpeg"
    assert page.handle("GET", "live", {"format": ["png"]}, None)[1] is png[1]


def test_a_test_message_says_whether_the_theme_lets_it_show(running, isolated_home):
    app, port = running
    status, answer = post(port, "control/probe", {"kind": "volume"})
    assert status == 200 and answer["shown"] is True and answer["message"] == "Shown on the panel."
    quiet_theme("quiet", off=("music",))
    set_config_value("theme", "quiet")
    assert wait_for(lambda: app.panel.state()["theme"] == "quiet")
    status, answer = post(port, "control/probe", {"kind": "music"})
    assert answer["shown"] is False and answer["reason"] == "theme"
    assert "toast.off" in answer["message"] and answer["message"].startswith("Not shown")
    for kind in ("volume", "warning", "panel", "device", "game"):
        assert post(port, "control/probe", {"kind": kind})[1]["shown"] is True, kind
    status, answer = post(port, "control/probe", {"kind": "nothing"})
    assert status == 400 and "nothing" in answer["error"]
    assert post(port, "control/probe", {})[0] == 400
    app.panel.pause()  # nothing shows a message on a paused panel
    assert post(port, "control/probe", {"kind": "volume"})[1]["reason"] == "no-panel"


def test_a_test_message_is_made_like_the_real_one(running):
    from libre_panel.plugins import Delivery

    app, port = running
    seen = []
    app.plugin_host.judge = lambda toast: seen.append(toast) or Delivery(True)  # a spy
    assert post(port, "control/probe", {"kind": "music"})[1]["shown"] is True
    (toast,) = seen
    assert (toast.kind, toast.rank, toast.service, toast.icon) == ("music", 1, "editor", "music")
    assert toast.payload["label"] == "Now playing" and toast.text == "Test message"
    assert toast.seconds == 5.0 and toast.payload["progress"] == 0.4


def test_a_service_is_restarted_from_the_page(running):
    app, port = running
    set_config_value("services.enabled", ["notices"])
    assert wait_for(lambda: "notices" in app.services.running)
    first = app.services.running["notices"][0]
    status, answer = post(port, "control/services/restart", {"service": "notices"})
    assert status == 200 and answer == {"service": "notices", "state": "running", "detail": ""}
    assert app.services.running["notices"][0] is not first
    status, answer = post(port, "control/services/restart", {"service": "game"})  # not enabled
    assert status == 404 and "not enabled" in answer["error"]
    assert post(port, "control/services/restart", {})[0] == 400


def test_a_service_is_switched_on_and_off_from_the_page(running, isolated_home):
    app, port = running
    path = isolated_home / "config.toml"

    def row(name):
        return next(s for s in get(port, "control/services")[1]["services"] if s["name"] == name)

    assert row("notices")["state"] == "off" and row("notices")["enabled"] is False
    status, answer = post(port, "control/services/enable", {"service": "notices", "enabled": True})
    assert status == 200 and answer["service"] == "notices" and answer["enabled"] is True
    assert answer["state"] in ("pending", "running") and answer["detail"] == ""
    assert load_config().services.enabled == ["notices"]
    assert wait_for(lambda: row("notices")["state"] == "running", 15)  # the app followed
    assert row("notices")["enabled"] is True and row("notices")["restartable"] is True
    # asked again: nothing is written
    before = path.read_text(encoding="utf-8")
    answer = post(port, "control/services/enable", {"service": "notices", "enabled": True})[1]
    assert answer["state"] == "running" and path.read_text(encoding="utf-8") == before
    # a second one: the first stays, in its place
    assert post(port, "control/services/enable", {"service": "game", "enabled": True})[0] == 200
    assert load_config().services.enabled == ["notices", "game"]
    assert wait_for(lambda: row("game")["state"] == "running", 15)
    # off: the service is stopped by the app, the other goes on
    answer = post(port, "control/services/enable", {"service": "notices", "enabled": False})[1]
    assert answer["enabled"] is False and answer["state"] in ("pending", "off")
    assert load_config().services.enabled == ["game"]
    assert wait_for(lambda: row("notices")["state"] == "off", 15)
    assert row("notices")["restartable"] is False and row("game")["state"] == "running"
    assert "notices" not in app.services.running


def test_a_service_that_cannot_be_switched_is_refused_and_nothing_is_written(editor, isolated_home):
    write_config(isolated_home)
    before = (isolated_home / "config.toml").read_text(encoding="utf-8")
    status, answer = post(editor, "control/services/enable", {"service": "nope", "enabled": True})
    assert status == 404 and "nope" in answer["error"]
    for body in (
        {"service": "notices"},
        {"service": "notices", "enabled": "yes"},
        {"service": "notices", "enabled": 1},
        {"enabled": True},
        {"service": "", "enabled": True},
        {"service": 7, "enabled": True},
    ):
        status, answer = post(editor, "control/services/enable", body)
        assert status == 400 and answer["error"], body
    assert (isolated_home / "config.toml").read_text(encoding="utf-8") == before
    # a name that is in the list but not installed (a plugin that was removed) can be taken out
    set_config_value("services.enabled", ["notices", "gone-plugin"])
    row = next(
        s for s in get(editor, "control/services")[1]["services"] if s["name"] == "gone-plugin"
    )
    assert row["enabled"] is True and row["installed"] is False
    assert (
        post(editor, "control/services/enable", {"service": "gone-plugin", "enabled": False})[0]
        == 200
    )
    assert load_config().services.enabled == ["notices"]
    assert "gone-plugin" not in {s["name"] for s in get(editor, "control/services")[1]["services"]}


def test_a_service_is_pending_until_the_app_follows_the_file(isolated_home):
    """The words for the time between the write and the app's reaction: the state is
    ``pending`` while config.toml and what runs disagree, in both directions."""
    from types import SimpleNamespace

    from libre_panel.plugins import PageContext

    write_config(isolated_home)
    manager = SimpleNamespace(state={}, running={})  # a manager that does nothing yet
    controls = SimpleNamespace(services=manager, config_path=isolated_home / "config.toml")
    page = Control(PageContext(controls))

    def row(name="notices"):
        return next(
            s for s in page.handle("GET", "services", {}, None)[1]["services"] if s["name"] == name
        )

    status, answer = page.handle(
        "POST", "services/enable", {}, {"service": "notices", "enabled": True}
    )
    assert (status, answer["state"]) == (200, "pending")
    assert set(answer) == {"service", "enabled", "state", "detail"}
    assert row()["state"] == "pending" and row()["restartable"] is False
    manager.state["notices"] = "running"  # the app has followed
    assert row()["state"] == "running" and row()["restartable"] is True
    answer = page.handle("POST", "services/enable", {}, {"service": "notices", "enabled": False})[1]
    assert answer["state"] == "pending"  # written, still running
    assert row()["state"] == "pending" and row()["enabled"] is False
    del manager.state["notices"]
    assert row()["state"] == "off"
    manager.state["game"] = "failed: no such option"
    assert row("game")["state"] == "pending"  # it runs, though the file does not list it
    page.handle("POST", "services/enable", {}, {"service": "game", "enabled": True})
    assert row("game")["state"] == "failed" and row("game")["detail"] == "no such option"
    assert row("game")["restartable"] is True


def test_the_services_follow_the_page_while_the_panel_is_paused(running):
    """The panel loop watches config.toml; with the panel paused the page has the app follow."""
    app, port = running
    app.panel.pause()
    assert wait_for(lambda: app.panel.state()["state"] == "paused")
    status, answer = post(port, "control/services/enable", {"service": "notices", "enabled": True})
    assert status == 200 and answer["enabled"] is True
    assert wait_for(lambda: "notices" in app.services.running, 15)
    assert app.panel.state()["state"] == "paused"
    rows = get(port, "control/services")[1]["services"]
    assert next(row for row in rows if row["name"] == "notices")["state"] == "running"
    assert post(port, "control/services/enable", {"service": "notices", "enabled": False})[0] == 200
    assert wait_for(lambda: "notices" not in app.services.running, 15)
    # the Autopilot page saves the same way
    assert post(port, "autopilot/settings", {"enabled": True, "rules": RULES[:1]})[0] == 200
    assert wait_for(lambda: "autopilot" in app.services.running, 15)
    assert get(port, "autopilot/status")[1]["state"] in ("running", "pending")


def test_a_page_context_has_the_app_follow_config_toml():
    from types import SimpleNamespace

    from libre_panel.plugins import PageContext

    asked = []
    PageContext(SimpleNamespace(follow_config=lambda: asked.append(1))).follow_config()
    assert asked == [1]
    PageContext().follow_config()  # a plain editor: no services, nothing to follow
    PageContext(SimpleNamespace()).follow_config()  # an app that cannot be asked: no failure


def test_a_source_that_says_how_it_is_read_is_explained(running):
    """LibreHardwareMonitor tells its mode, the rights and the driver; the page turns that
    into rows, and adds the hint of ``hardware_hint`` where nothing arrives (Windows)."""
    import sys

    from libre_panel.pages import control
    from libre_panel.sensors.base import SensorProvider
    from libre_panel.sensors.librehardwaremonitor import LhmStatus

    assert control.lhm_rows(
        {"mode": "inprocess", "elevated": False, "ring0": False, "pawnio": None, "error": ""}
    ) == [
        ["Mode", "inside Libre Panel"],
        ["Administrator", "no"],
        ["CPU sensors", "out of reach"],
        ["PawnIO driver", "not installed"],
    ]
    assert control.lhm_rows({"mode": "webserver", "error": "no answer"}) == [
        ["Mode", "web server"],
        ["Error", "no answer"],
    ]

    class Lhm(SensorProvider):
        name = "librehardwaremonitor"

        def read(self):
            return {}

        def status(self):
            return LhmStatus("inprocess", elevated=False, ring0=False, pawnio=None)

    app, port = running
    app.panel.status.hub.providers.insert(0, Lhm())
    sources = get(port, "control/sources")[1]["sources"]
    assert [s["name"] for s in sources] == [
        s["name"] for s in get(port, "control/status")[1]["sources"]
    ]
    lhm = next(s for s in sources if s["name"] == "librehardwaremonitor")
    assert lhm["status"] == {  # as the source says it, for a screen that words it itself
        "mode": "inprocess", "elevated": False, "ring0": False, "pawnio": None, "error": "",
    }  # fmt: skip
    # (on Windows the configured LibreHardwareMonitor is there as well, and says its own)
    assert all(s["status"] is None for s in sources if s["name"] != "librehardwaremonitor")
    assert lhm["details"][:2] == [["Mode", "inside Libre Panel"], ["Administrator", "no"]]
    assert (lhm["hint"] is not None) is (sys.platform == "win32")  # temperatures come from there
    assert lhm["problem"] is (sys.platform == "win32")


# -- Autopilot ----------------------------------------------------------------------------------

RULES = [
    {"name": "Music", "theme": "studio", "when": {"music": True}},
    {
        "name": "Busy",
        "theme": "column",
        "when": {"cpu_over": 80, "for_s": 30},
        "immediate": True,
        "hold_s": 2,
    },
    {"name": "Night", "theme": "pico", "when": {"time": "22:00-06:30"}, "enabled": False},
]


def test_the_autopilot_page_in_a_plain_editor_shows_the_defaults(editor, isolated_home):
    write_config(isolated_home)
    status, data = get(editor, "autopilot/settings")
    assert status == 200 and data["background"] is False and data["state"] == "unknown"
    assert set(data) == {  # what docs/ARCHITECTURE.md promises
        "background", "enabled", "state", "detail", "status",
        "options", "rules", "problems", "themes", "transitions",
    }  # fmt: skip
    assert data["enabled"] is False and data["rules"] == [] and data["status"] is None
    assert data["options"] == {
        "hold_s": 8.0, "min_s": 20.0, "quiet_after_boot_s": 240.0, "transition": "tube",
    }  # fmt: skip
    assert {"", "cut", "fade", "slide", "tube", "entrance"} <= set(data["transitions"])
    assert "studio" in data["themes"]
    assert get(editor, "autopilot/status")[1] == {
        "background": False, "enabled": False, "state": "unknown", "detail": "", "status": None,
    }  # fmt: skip


def test_the_rules_are_written_to_config_toml_and_read_back(editor, isolated_home):
    write_config(isolated_home)
    status, data = post(
        editor, "autopilot/settings", {"enabled": True, "options": {"hold_s": 3}, "rules": RULES}
    )
    assert status == 200 and data["enabled"] is True and data["rules"] == RULES
    assert data["options"]["hold_s"] == 3 and data["problems"] == ["", "", ""]
    config = load_config()
    assert config.services.enabled == ["autopilot"]
    assert config.services.options["autopilot"]["rules"] == RULES
    text = (isolated_home / "config.toml").read_text(encoding="utf-8")
    assert "hold_s = 3" in text and "min_s" not in text  # only what changed is written
    assert "[[services.autopilot.rules]]" in text and 'time = "22:00-06:30"' in text

    # an edit of one part leaves the others: the rules stay while the service is switched off
    status, data = post(editor, "autopilot/settings", {"enabled": False})
    assert data["enabled"] is False and data["rules"] == RULES
    assert load_config().services.enabled == []
    # the same value as before writes nothing new
    before = (isolated_home / "config.toml").read_text(encoding="utf-8")
    post(editor, "autopilot/settings", {"options": {"hold_s": 3, "min_s": 20}})
    assert (isolated_home / "config.toml").read_text(encoding="utf-8") == before
    # fewer rules, another transition
    status, data = post(
        editor, "autopilot/settings", {"rules": RULES[:1], "options": {"transition": "fade"}}
    )
    assert data["rules"] == RULES[:1] and data["options"]["transition"] == "fade"
    status, data = post(editor, "autopilot/settings", {"rules": []})
    assert data["rules"] == [] and "rules" not in (load_config().services.options["autopilot"])


def test_saving_changes_only_what_changed_in_one_write(editor, isolated_home):
    """The comments inside the rules survive a save that leaves the rules as they are, and
    everything that changed reaches the file in one write (one restart of the service)."""
    write_config(isolated_home)
    path = isolated_home / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + """
[services]
enabled = ["notices"]

[services.autopilot]
hold_s = 5

# my favourite
[[services.autopilot.rules]]
name = "Music"
theme = "studio"   # what plays
when = { music = true }
""",
        encoding="utf-8",
    )
    rules = get(editor, "autopilot/settings")[1]["rules"]
    writes = []
    import libre_panel.config as config_module

    real = config_module._write
    config_module._write = lambda *args: writes.append(args) or real(*args)
    try:
        body = {"enabled": True, "options": {"hold_s": 9, "min_s": 30}, "rules": rules}
        status, data = post(editor, "autopilot/settings", body)
    finally:
        config_module._write = real
    assert status == 200 and len(writes) == 1
    text = path.read_text(encoding="utf-8")
    assert "# my favourite" in text and "# what plays" in text  # the rules were not rewritten
    assert "hold_s = 9" in text and "min_s = 30" in text and "[services.autopilot]" in text
    assert load_config().services.enabled == ["notices", "autopilot"]
    assert data["options"]["hold_s"] == 9 and data["rules"] == rules


def test_a_rule_that_is_wrong_is_refused_and_nothing_is_written(editor, isolated_home):
    write_config(isolated_home)
    before = (isolated_home / "config.toml").read_text(encoding="utf-8")
    good = {"name": "A", "theme": "studio", "when": {"music": True}}
    wrong = {
        "no name": {**good, "name": " "},
        "no theme": {**good, "theme": ""},
        "no condition": {**good, "when": {}},
        "when not a table": {**good, "when": "music"},
        "unknown condition": {**good, "when": {"weather": "rain"}},
        "flag not on or off": {**good, "when": {"music": "yes"}},
        "load too high": {**good, "when": {"cpu_over": 150}},
        "load not a number": {**good, "when": {"gpu_over": True}},
        "no programs": {**good, "when": {"apps": []}},
        "programs not names": {**good, "when": {"apps": ["a.exe", 3]}},
        "time": {**good, "when": {"time": "evening"}},
        "time out of range": {**good, "when": {"time": "25:00-06:00"}},
        "window": {**good, "when": {"cpu_over": 50, "for_s": 0}},
        "enabled": {**good, "enabled": "no"},
        "hold": {**good, "hold_s": -1},
        "not a table": "music",
    }
    for what, rule in wrong.items():
        status, answer = post(editor, "autopilot/settings", {"rules": [good, rule]})
        assert status == 400 and answer["error"].startswith("Rule "), (what, answer)
    odd = {**good, "extra": None}  # a key config.toml cannot hold
    status, answer = post(editor, "autopilot/settings", {"rules": [odd]})
    assert status == 400 and "NoneType" in answer["error"]
    for body in (
        {"rules": "all"},
        {"options": {"hold_s": -3}},
        {"options": {"min_s": True}},
        {"options": {"transition": 5}},
        {"options": []},
        {"enabled": "yes"},
    ):
        assert post(editor, "autopilot/settings", body)[0] == 400, body
    assert (isolated_home / "config.toml").read_text(encoding="utf-8") == before
    status, answer = post(editor, "autopilot/settings", {"rules": [{**good, "name": "Büro"}]})
    assert status == 200  # the same checks let a good rule in
    set_config_value("fps", 7)  # and the file is still the user's: written, not replaced


def test_rules_that_cannot_work_are_marked_when_they_are_read(editor, isolated_home):
    write_config(isolated_home)
    path = isolated_home / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + """
[services]
enabled = ["autopilot"]

[[services.autopilot.rules]]
name = "Fine"
theme = "studio"
when = { music = true }

[[services.autopilot.rules]]
name = "Lost"
theme = "no-such-theme"
when = { music = true }

[[services.autopilot.rules]]
name = "Broken"
theme = "studio"
when = { time = "soon" }
""",
        encoding="utf-8",
    )
    status, data = get(editor, "autopilot/settings")
    assert data["enabled"] is True and [r["name"] for r in data["rules"]] == [
        "Fine",
        "Lost",
        "Broken",
    ]
    assert data["problems"][0] == ""
    assert "no-such-theme is not installed" in data["problems"][1]
    assert "time looks like" in data["problems"][2]


def test_the_page_refuses_a_config_it_cannot_read(editor, isolated_home):
    isolated_home.mkdir(parents=True)
    (isolated_home / "config.toml").write_text("this is not toml", encoding="utf-8")
    status, data = get(editor, "autopilot/settings")
    assert status == 409 and "config.toml" in data["error"]
    assert post(editor, "autopilot/settings", {"rules": []})[0] == 409
    with pytest.raises(ConfigError):
        load_config()


def test_the_autopilot_page_shows_what_the_service_does(running):
    app, port = running
    status, data = post(
        port, "autopilot/settings", {"enabled": True, "rules": RULES[:1], "options": {"hold_s": 1}}
    )
    assert status == 200
    assert wait_for(lambda: "autopilot" in app.services.running, 15)
    status, data = get(port, "autopilot/status")
    assert data["background"] is True and data["state"] == "running" and data["enabled"] is True
    assert wait_for(lambda: get(port, "autopilot/status")[1]["status"] is not None)
    status = get(port, "autopilot/status")[1]["status"]
    assert status["rule"] is None and status["theme"] is None  # no music: no rule holds
    assert set(status["signals"]) == {"music", "microphone", "app", "cpu", "gpu"}
    assert status["resting"] is False
    # switched off from the page: the service is gone
    assert post(port, "autopilot/settings", {"enabled": False})[0] == 200
    assert wait_for(lambda: "autopilot" not in app.services.running, 15)
    assert wait_for(lambda: get(port, "autopilot/status")[1]["state"] == "off")
    assert get(port, "autopilot/status")[1]["status"] is None
    assert get(port, "autopilot/status")[1]["enabled"] is False


def test_the_service_follows_the_rules_the_page_wrote(running):
    """What the page writes is what the service reads: a rule that holds shows its theme."""
    app, port = running
    rule = {"name": "Quiet", "theme": "slate", "when": {"music": False}}  # no music plays here
    options = {"hold_s": 0, "min_s": 0}
    post(port, "autopilot/settings", {"enabled": True, "options": options, "rules": [rule]})
    assert wait_for(lambda: app.panel.state()["theme"] == "slate", 30), app.panel.state()
    status = get(port, "autopilot/status")[1]["status"]
    assert status["rule"] == "Quiet" and status["theme"] == "slate"
    assert load_config().theme == "libre-default"  # the autopilot never writes the choice

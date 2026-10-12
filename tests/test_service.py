"""Background app: panel service, editor controls, single instance, auto driver."""

import http.client
import json
import time

import pytest
from editor_token import token_of
from PIL import Image
from test_autostart import FakeTasks

from libre_panel.autostart import Autostart
from libre_panel.config import config_dir, load_config, set_config_value
from libre_panel.editor import access
from libre_panel.instance import AlreadyRunning, InstanceLock
from libre_panel.service import BackgroundApp, PanelService, running_instance


def write_config(home, theme="libre-default", brightness=60, driver="virtual"):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        f'theme = "{theme}"\nfps = 5\n\n[device]\ndriver = "{driver}"\n'
        f'brightness = {brightness}\noutput = "{(home / "frame.png").as_posix()}"\n',
        encoding="utf-8",
    )


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_panel_service_pause_resume_stop(isolated_home):
    write_config(isolated_home)
    service = PanelService()
    service.start()
    try:
        assert wait_for(lambda: service.state()["state"] == "showing")
        state = service.state()
        assert state["theme"] == "libre-default" and state["target"].startswith("PNG file")
        assert wait_for(lambda: (isolated_home / "frame.png").exists())

        service.pause()
        assert service.paused and not service.running
        assert service.state()["state"] == "paused"

        service.resume()
        assert wait_for(lambda: service.state()["state"] == "showing")
    finally:
        service.stop()
    assert not service.running


def test_a_theme_that_is_gone_shows_the_panels_own_and_says_so(isolated_home):
    """A theme config.toml names that is not installed any more (a plugin's that is gone)
    does not stop the panel: it shows its own theme, and the tray and Start say why."""
    write_config(isolated_home, theme="does-not-exist")
    service = PanelService()
    service.start()
    try:
        assert wait_for(lambda: service.state()["state"] == "showing")
        state = service.state()
        assert state["theme"] == "libre-default" and state["missing_theme"] == "does-not-exist"
        assert "does-not-exist" in state["notice"] and "libre-default" in state["notice"]
        time.sleep(0.05)
        write_config(isolated_home, theme="slate")  # chosen in the tray or on Start
        assert wait_for(lambda: service.state()["theme"] == "slate")
        assert service.state()["missing_theme"] == "" and service.state()["notice"] == ""
    finally:
        service.stop()


def test_panel_service_waits_for_a_fixed_config(isolated_home):
    write_config(isolated_home)
    with open(isolated_home / "config.toml", "a", encoding="utf-8") as config:
        config.write("fps = = 5\n")  # not TOML
    service = PanelService()
    service.start()
    try:
        assert wait_for(lambda: service.state()["state"] == "error")
        assert "config.toml" in service.state()["detail"]
        assert service.running  # still alive, waiting for a fix
        time.sleep(0.05)
        write_config(isolated_home, theme="slate")  # e.g. fixed in the editor
        assert wait_for(lambda: service.state()["state"] == "showing")
        assert service.state()["theme"] == "slate"
    finally:
        service.stop()


def test_the_services_come_in_line_whenever_the_loop_starts(isolated_home):
    """A change made while the loop did not run (the panel was paused) reaches the services
    when it runs again: the loop sees nothing new in a file it has just read."""
    write_config(isolated_home)
    heard = []
    service = PanelService(on_config=heard.append)
    service.start()
    try:
        assert wait_for(lambda: len(heard) == 1)  # at the start
        service.pause()
        set_config_value("services.enabled", ["notices"])
        service.resume()
        assert wait_for(lambda: len(heard) == 2)
        assert heard[-1].services.enabled == ["notices"]
    finally:
        service.stop()


def test_panel_service_restarts_after_a_crash(isolated_home, monkeypatch):
    from libre_panel import service as service_module

    write_config(isolated_home)
    calls = []
    real_run = service_module.run

    def flaky_run(config, stop, status, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        real_run(config, stop=stop, status=status, **kwargs)

    monkeypatch.setattr(service_module, "run", flaky_run)
    service = PanelService(restart_delay=0.1)
    service.start()
    try:
        assert wait_for(lambda: service.state()["state"] == "showing")
        assert len(calls) == 2
    finally:
        service.stop()


class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


@pytest.fixture
def app(isolated_home):
    write_config(isolated_home)
    registry = FakeRegistry()
    background = BackgroundApp(
        port=0,
        autostart=Autostart("win32", registry=registry, tasks=FakeTasks()),  # not the real one
        autostart_command=["LibrePanel.exe", "tray", "--background"],
    )
    background.registry = registry
    yield background
    background.quit()
    background.shutdown()


def call(url, method="GET", body=None, header=True):
    host = url.split("//")[1].rstrip("/")
    conn = http.client.HTTPConnection(*host.split(":"), timeout=10)
    headers = {"Host": host, "Content-Type": "application/json"}
    if header:  # the token of the app's editor, as the app sends it
        headers[access.HEADER] = token_of(host.split(":")[1])
    conn.request(method, "/api/app", body=json.dumps(body) if body else None, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response.status, data


def test_services_start_once_a_broken_config_is_fixed(app, isolated_home):
    path = isolated_home / "config.toml"
    fixed = path.read_text(encoding="utf-8") + '\n[services]\nenabled = ["notices"]\n'
    path.write_text("fps = 'fast'\n", encoding="utf-8")
    app.start()
    assert wait_for(lambda: app.panel.state()["state"] == "error")
    assert app.services.running == {}  # nothing to start from a file that does not load
    path.write_text(fixed, encoding="utf-8")
    assert wait_for(lambda: "notices" in app.services.running, 15)
    assert wait_for(lambda: app.panel.state()["state"] == "showing")


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (b'device = "turing-5"\n', "device"),  # the model where its table belongs
        (b"weather = true\n", "weather"),
        (b'sensors = "psutil"\n', "sensors"),
        (b'[sensors]\nproviders = "psutil"\n', "sensors.providers"),
        (b"[sensors]\nproviders = 5\n", "sensors.providers"),
        ('[weather]\nplace = "Café"\n'.encode("cp1252"), "UTF-8"),  # an old editor's encoding
    ],
)
def test_the_app_starts_with_a_config_of_the_wrong_shape_and_says_what_is_wrong(
    app, isolated_home, text, said
):
    """A table or a list of the wrong kind, or a file that is not UTF-8, ended the app at its
    start: such a config.toml is an error of the config like any other, which the panel
    shows until the file is fixed."""
    (isolated_home / "config.toml").write_bytes(text)
    app.start()
    assert wait_for(lambda: app.panel.state()["state"] == "error")
    assert said in app.panel.state()["detail"]
    assert app.brightness() is None and app.active_theme() is None  # the tray's menu


def test_a_page_has_the_app_follow_config_toml_also_while_the_panel_is_paused(app, isolated_home):
    app.start()
    assert wait_for(lambda: app.panel.state()["state"] == "showing")
    app.panel.pause()  # the loop that watches the file is gone
    set_config_value("services.enabled", ["notices"])
    assert not wait_for(lambda: "notices" in app.services.running, 0.5)  # nobody told the app
    app.follow_config()
    assert wait_for(lambda: "notices" in app.services.running, 10)
    assert app.panel.state()["state"] == "paused"
    (isolated_home / "config.toml").write_text("this is not toml", encoding="utf-8")
    app.follow_config()  # a file that cannot be read leaves the services as they are
    time.sleep(0.2)
    assert "notices" in app.services.running


def test_the_editor_gets_the_numbers_of_the_panel(app):
    url = app.start()
    assert app.panel.state()["stats"] == {}  # the first second is not over
    assert wait_for(lambda: app.panel.state()["stats"], timeout=15)
    status, data = call(url)
    stats = data["panel"]["stats"]
    assert status == 200 and stats["target_fps"] == 5 and stats["streaming"] is False
    assert stats["fps"] > 0 and stats["render_ms"] > 0 and stats["uptime_s"] >= 1
    assert stats["kbps"] is None  # the PNG file counts no bytes
    assert stats["blocks_per_s"] is None  # and there is no video link
    assert (stats["usb_errors"], stats["panel_restarts"], stats["ffmpeg_restarts"]) == (0, 0, 0)
    status, data = call(url, "POST", {"action": "pause"})
    assert data["panel"]["stats"] == {}  # nothing runs, so nothing to measure


def test_the_editor_gets_no_autostart_toggle_where_there_is_no_autostart(isolated_home):
    write_config(isolated_home)
    background = BackgroundApp(port=0, autostart=Autostart("sunos5", home=isolated_home))
    snapshot = background.snapshot()
    assert snapshot["autostart"] is None  # the checkbox stays greyed out
    assert "not supported on this system" in snapshot["autostart_location"]
    with pytest.raises(OSError, match="not supported on this system"):
        background.act("autostart", True)


def test_background_app_controls_through_the_editor(app, isolated_home):
    url = app.start()
    assert running_instance() == {"pid": __import__("os").getpid(), "editor": url}
    assert wait_for(lambda: app.panel.state()["state"] == "showing")

    status, data = call(url)
    assert status == 200 and data["available"] is True
    assert data["panel"]["state"] == "showing" and data["brightness"] == 60
    assert data["autostart"] is False

    status, data = call(url, "POST", {"action": "brightness", "value": 25})
    assert status == 200 and data["brightness"] == 25
    assert load_config().device.brightness == 25

    assert data["upside_down"] is False
    status, data = call(url, "POST", {"action": "upside_down", "value": True})
    assert status == 200 and data["upside_down"] is True
    assert load_config().device.rotate == 180

    status, data = call(url, "POST", {"action": "autostart", "value": True})
    assert data["autostart"] is True
    assert app.registry.values["Libre Panel"] == "LibrePanel.exe tray --background"

    status, data = call(url, "POST", {"action": "pause"})
    assert data["panel"]["state"] == "paused"
    status, data = call(url, "POST", {"action": "resume"})
    assert data["panel"]["state"] in ("starting", "showing")

    # bad input is refused and changes nothing
    for bad in (
        {"action": "brightness", "value": 101},
        {"action": "brightness", "value": True},
        {"action": "autostart", "value": "yes"},
        {"action": "upside_down", "value": 180},
        {"action": "reboot"},
    ):
        status, data = call(url, "POST", bad)
        assert status == 400, bad
    assert load_config().device.brightness == 25
    # writes need the custom header (no cross-site requests)
    status, _ = call(url, "POST", {"action": "pause"}, header=False)
    assert status == 403 and not app.panel.paused

    status, data = call(url, "POST", {"action": "quit"})
    assert status == 200
    assert app.quit_requested.wait(5)
    app.shutdown()
    assert running_instance() is None


def test_the_app_knows_when_the_standby_clip_is_to_be_asked_for(isolated_home, monkeypatch):
    """The tray's line asks once a second, so it never looks at the USB bus: it goes by the
    panel that is named in config.toml, or the one the panel loop says it is driving."""
    from test_editor_settings import lacking_ffmpeg

    from libre_panel.devices import turzx_usb

    def on_the_bus():
        raise AssertionError("the tray looked at the USB bus")

    monkeypatch.setattr(turzx_usb, "present_model", on_the_bus)
    lacking_ffmpeg(monkeypatch, found=True)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    assert app.clip_question() is False  # no panel is known
    app.panel.status.target = "PNG file frame.png"
    assert app.clip_question() is False
    app.panel.status.target = 'Turing 8.8"'  # a panel the video mode is not proven on
    assert app.clip_question() is False
    app.panel.status.target = 'Turing 9.2"'  # the loop drives a 9.2"
    assert app.clip_question() is True
    app.panel.status.target = 'Turing 9.2", video 50 fps'  # (as it says in video mode)
    assert app.clip_question() is True
    set_config_value("video.clip_asked", True)  # answered, whichever way
    assert app.clip_question() is False
    set_config_value("video.clip_asked", False)
    set_config_value("video.local_clip", "usr/data/standby.h264")
    assert app.clip_question() is False
    set_config_value("video.local_clip", "")
    assert app.clip_question() is True
    set_config_value("device.driver", "virtual")
    assert app.clip_question() is False
    set_config_value("device.driver", "auto")
    app.panel.status.target = ""  # nothing is driven, but the 9.2" is the panel that is named
    assert app.clip_question() is False
    set_config_value("device.model", "turing-9.2-usb")
    assert app.clip_question() is True
    (isolated_home / "config.toml").write_text("fps = 'fast'\n", encoding="utf-8")
    assert app.clip_question() is False  # a config that cannot be read asks nothing


def test_plain_editor_has_no_app_controls():
    import threading

    from test_editor_server import request

    from libre_panel.editor.server import make_server

    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert request(port, "GET", "/api/app") == (200, {"available": False})
        status, _ = request(port, "POST", "/api/app", {"action": "quit"})
        assert status == 404
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()


def test_instance_lock_allows_one_holder(isolated_home):
    first = InstanceLock().acquire()
    try:
        with pytest.raises(AlreadyRunning):
            InstanceLock().acquire()
    finally:
        first.release()
    with InstanceLock():  # free again
        pass
    assert (config_dir() / "libre-panel.lock").exists()


def test_auto_driver_switches_to_a_panel_that_appears(tmp_path, monkeypatch):
    from libre_panel.config import DeviceConfig
    from libre_panel.devices import auto
    from libre_panel.devices.base import DeviceError, Display

    class Panel(Display):
        plugged = False
        shown = []

        def open(self):
            if not Panel.plugged:
                raise DeviceError("no Turing/TURZX USB panel found")

        def set_brightness(self, percent):
            Panel.brightness = percent

        def describe(self):
            return "Test panel"

        def show(self, frame, region=None):
            Panel.shown.append(region)

    now = [0.0]
    monkeypatch.setattr(auto, "TurzxDisplay", Panel)
    display = auto.AutoDisplay(
        DeviceConfig(driver="auto", output=str(tmp_path / "f.png")), clock=lambda: now[0]
    )
    display.open()
    display.set_brightness(40)
    frame = Image.new("RGB", (8, 8))
    display.show(frame, (0, 0, 4, 4))
    assert display.describe().startswith("PNG file") and not display.using_panel

    Panel.plugged = True
    now[0] = 1.0  # too early for the next look
    display.show(frame, (0, 0, 4, 4))
    assert not display.using_panel
    now[0] = auto.AutoDisplay.PROBE_INTERVAL_S + 0.1
    display.show(frame, (0, 0, 4, 4))
    assert display.using_panel and display.describe() == "Test panel"
    assert Panel.shown == [None]  # the first frame on the panel is complete
    assert Panel.brightness == 40
    display.close()


def test_relative_output_is_in_the_settings_folder(isolated_home, tmp_path, monkeypatch):
    from libre_panel.config import DeviceConfig
    from libre_panel.devices.base import DeviceError
    from libre_panel.devices.virtual import VirtualDisplay

    monkeypatch.chdir(tmp_path)  # like "/" for an app started by a double-click
    display = VirtualDisplay(DeviceConfig(driver="virtual", output="frame.png"))
    display.show(Image.new("RGB", (8, 8)))
    assert (isolated_home / "frame.png").exists() and not (tmp_path / "frame.png").exists()

    blocked = tmp_path / "file"
    blocked.write_text("")
    display = VirtualDisplay(DeviceConfig(driver="virtual", output=str(blocked / "frame.png")))
    with pytest.raises(DeviceError):  # retried by the main loop, never a crash
        display.show(Image.new("RGB", (8, 8)))

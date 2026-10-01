"""Background app: panel service, editor controls, single instance, auto driver."""

import http.client
import json
import time

import pytest
from PIL import Image

from libre_panel.autostart import Autostart
from libre_panel.config import config_dir, load_config
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


def test_panel_service_waits_for_a_fixed_config(isolated_home):
    write_config(isolated_home, theme="does-not-exist")
    service = PanelService()
    service.start()
    try:
        assert wait_for(lambda: service.state()["state"] == "error")
        assert "does-not-exist" in service.state()["detail"]
        assert service.running  # still alive, waiting for a fix
        time.sleep(0.05)
        write_config(isolated_home, theme="slate")  # e.g. fixed in the editor
        assert wait_for(lambda: service.state()["state"] == "showing")
        assert service.state()["theme"] == "slate"
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
        autostart=Autostart("win32", registry=registry),
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
    if header and method == "POST":
        headers["X-Libre-Panel"] = "1"
    conn.request(method, "/api/app", body=json.dumps(body) if body else None, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response.status, data


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

"""Tray menu and the background app's command line."""

import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

# The dummy backend imports without a display; the menu classes are the real ones.
os.environ.setdefault("PYSTRAY_BACKEND", "dummy")
pystray = pytest.importorskip("pystray")

from test_service import FakeRegistry, wait_for, write_config  # noqa: E402

from libre_panel import cli  # noqa: E402
from libre_panel.autostart import Autostart  # noqa: E402
from libre_panel.config import load_config  # noqa: E402
from libre_panel.service import BackgroundApp, running_instance  # noqa: E402
from libre_panel.tray import Tray, headline, run_app, tooltip  # noqa: E402


class FakeIcon:
    HAS_NOTIFICATION = True

    def __init__(self):
        self.icon = None
        self.title = ""
        self.menu_updates = 0
        self.stopped = False
        self.notes = []

    def update_menu(self):
        self.menu_updates += 1

    def notify(self, message, title=None):
        self.notes.append(message)

    def stop(self):
        self.stopped = True


@pytest.fixture
def tray(isolated_home):
    write_config(isolated_home)
    app = BackgroundApp(
        port=0,
        autostart=Autostart("win32", registry=FakeRegistry()),
        autostart_command=["LibrePanel.exe", "tray", "--background"],
    )
    app.start()
    tray = Tray(app, pystray, log_path=isolated_home / "logs" / "libre-panel.log")
    tray.icon = FakeIcon()
    app.on_quit(tray.icon.stop)
    yield tray
    app.quit()
    app.shutdown()


def items(menu):
    return {item.text: item for item in menu if item is not pystray.Menu.SEPARATOR}


def test_menu_layout_and_state(tray):
    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    menu = items(tray.build_menu())
    assert list(menu)[0].startswith("Libre Panel: showing on PNG file")
    assert [
        "Open theme editor",
        "Theme",
        "Brightness",
        "Pause panel",
        "Start with system",
        "Open settings folder",
        "Open log",
        "Quit Libre Panel",
    ] == list(menu)[1:]
    assert menu["Open theme editor"].default
    themes = items(menu["Theme"].submenu)
    assert {"libre-default", "spur-ii", "orbit", "slate", "column"} <= set(themes)
    assert themes["libre-default"].checked and not themes["slate"].checked
    brightness = items(menu["Brightness"].submenu)
    assert list(brightness) == ["Off", "10 %", "25 %", "50 %", "75 %", "100 %"]
    assert not any(item.checked for item in brightness.values())  # 60 is not a step


def test_menu_actions(tray):
    menu = items(tray.build_menu())
    items(menu["Theme"].submenu)["slate"](tray.icon)
    assert wait_for(lambda: load_config().theme == "slate")
    items(menu["Brightness"].submenu)["25 %"](tray.icon)
    assert wait_for(lambda: load_config().device.brightness == 25)
    assert items(menu["Brightness"].submenu)["25 %"].checked

    menu["Start with system"](tray.icon)
    assert wait_for(lambda: tray.app.snapshot()["autostart"] is True)
    assert menu["Start with system"].checked

    menu["Pause panel"](tray.icon)
    assert wait_for(lambda: tray.app.panel.paused)
    assert menu["Pause panel"].checked
    menu["Pause panel"](tray.icon)
    assert wait_for(lambda: not tray.app.panel.paused)

    menu["Quit Libre Panel"](tray.icon)
    assert tray.app.quit_requested.is_set() and tray.icon.stopped


def test_refresh_updates_icon_only_on_change(tray):
    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    tray.refresh()
    first = tray.icon.menu_updates
    assert tray.icon.icon is not None and tray.icon.title.startswith("Libre Panel")
    tray.refresh()
    assert tray.icon.menu_updates == first  # nothing changed
    tray.app.set_brightness(50)
    tray.refresh()
    assert tray.icon.menu_updates == first + 1


def test_failed_action_is_reported(tray, monkeypatch):
    def broken(*args):
        raise ValueError("cannot write config.toml")

    tray._later(broken)()
    assert wait_for(lambda: tray.icon.notes == ["cannot write config.toml"])


def test_texts():
    state = {"state": "waiting", "target": "", "detail": "no panel found " * 20}
    assert headline(state) == "Libre Panel: waiting for the panel"
    assert len(tooltip(state)) <= 120
    assert headline({"state": "showing", "target": 'TURZX 9.2"'}).endswith('TURZX 9.2"')


def test_run_app_without_icon(isolated_home):
    write_config(isolated_home)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    result = []
    thread = threading.Thread(target=lambda: result.append(run_app(app, use_icon=False)))
    thread.start()
    assert wait_for(lambda: running_instance() is not None)
    assert wait_for(lambda: app.panel.state()["state"] == "showing")
    app.quit()
    thread.join(10)
    assert result == [0] and running_instance() is None and not app.panel.running


def test_cli_autostart(monkeypatch, tmp_path, capsys):
    from libre_panel import autostart as autostart_module

    monkeypatch.setattr(
        autostart_module, "Autostart", lambda: Autostart("linux", home=tmp_path, environ={})
    )
    assert cli.main(["autostart", "status"]) == 0
    assert "disabled" in capsys.readouterr().out
    assert cli.main(["autostart", "enable"]) == 0
    entry = tmp_path / ".config" / "autostart" / "libre-panel.desktop"
    assert "tray --background" in entry.read_text()
    assert cli.main(["autostart", "status"]) == 0
    assert "enabled" in capsys.readouterr().out
    assert cli.main(["autostart", "disable"]) == 0
    assert not entry.exists()


def test_windowed_build_defaults_to_tray(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_cmd_tray", lambda args: seen.append(args) or 0)
    assert cli.main(["--config", "x.toml"], default_command="tray") == 0
    assert seen[0].command == "tray" and str(seen[0].config) == "x.toml"
    assert not seen[0].background
    assert cli.main(["-v", "--background"], default_command="tray") == 0
    assert seen[1].background and seen[1].verbose
    assert cli.main(["tray", "--no-icon"], default_command="tray") == 0
    assert seen[2].no_icon
    with pytest.raises(SystemExit):
        cli.main(["tray", "--bogus"], default_command="tray")


def test_second_start_points_to_the_running_editor(isolated_home, capsys):
    write_config(isolated_home)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    from libre_panel.instance import InstanceLock

    with InstanceLock():
        url = app.start()
        try:
            assert cli.main(["tray", "--background"]) == 0
            assert url in capsys.readouterr().out
            assert cli.main(["run", "--once"]) == 1  # would fight over the panel
            assert "already running" in capsys.readouterr().err
        finally:
            app.quit()
            app.shutdown()


@pytest.mark.skipif(
    not (
        sys.platform.startswith("linux")
        and shutil.which("xvfb-run")
        and shutil.which("stalonetray")
    ),
    reason="needs Xvfb and stalonetray (a system tray for X11)",
)
def test_real_tray_icon_on_x11(isolated_home):
    """The whole program: tray icon on a real X11 tray, controlled over the editor API."""
    write_config(isolated_home)
    env = {k: v for k, v in os.environ.items() if k != "PYSTRAY_BACKEND"}
    script = (
        "stalonetray --geometry 4x1 -i 32 >/dev/null 2>&1 & TRAY=$!; sleep 1; "
        f"{sys.executable} -m libre_panel tray --background --port 0; CODE=$?; "
        "kill $TRAY; exit $CODE"
    )
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "bash", "-c", script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        log = isolated_home / "logs" / "libre-panel.log"
        assert wait_for(lambda: log.exists() and "tray icon ready" in log.read_text(), 30)
        url = running_instance()["editor"]
        import urllib.request

        request = urllib.request.Request(
            url + "api/app",
            data=json.dumps({"action": "quit"}).encode(),
            headers={"X-Libre-Panel": "1", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert json.loads(response.read())["available"] is True
        assert proc.wait(30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    output = proc.stdout.read().decode(errors="replace")
    assert "Traceback" not in output, output
    assert running_instance() is None

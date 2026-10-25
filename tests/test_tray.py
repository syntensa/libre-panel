"""Tray menu and the background app's command line."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

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
    app.on_quit(tray.stop_icon)
    yield tray
    app.quit()
    app.shutdown()


def items(menu):
    return {item.text: item for item in menu if item is not pystray.Menu.SEPARATOR}


def test_menu_layout_and_state(tray):
    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    menu = items(tray.build_menu())
    assert list(menu)[0].startswith("Libre Panel: showing on PNG file")
    # what most people come for first, then the views and what is set once
    assert [
        "Open Libre Panel",
        "Theme",
        "Brightness",
        "Pause panel",
        "Design",
        "Settings",
        "Upside down",
        "Start with system",
        "Open settings folder",
        "Open log",
        "Quit Libre Panel",
    ] == list(menu)[1:]
    assert menu["Open Libre Panel"].default  # also a click on the icon
    themes = items(menu["Theme"].submenu)  # by name; the panel is not known: all sizes
    assert {"Libre Default", "Orbit", "Slate", "Column", "Lumen"} <= set(themes)
    assert themes["Libre Default"].checked and not themes["Slate"].checked
    assert "SPUR II" in items(themes["SPUR II"].submenu)  # a collection keeps to itself
    assert "Lumen Vertical" not in themes  # chosen as Lumen
    brightness = items(menu["Brightness"].submenu)
    assert list(brightness) == ["Off", "10 %", "25 %", "50 %", "75 %", "100 %"]
    assert not any(item.checked for item in brightness.values())  # 60 is not a step


def test_menu_actions(tray):
    menu = items(tray.build_menu())
    items(menu["Theme"].submenu)["Slate"](tray.icon)
    assert wait_for(lambda: load_config().theme == "slate")
    items(menu["Brightness"].submenu)["25 %"](tray.icon)
    assert wait_for(lambda: load_config().device.brightness == 25)
    assert items(menu["Brightness"].submenu)["25 %"].checked

    menu["Upside down"](tray.icon)
    assert wait_for(lambda: load_config().device.rotate == 180)
    assert menu["Upside down"].checked
    menu["Upside down"](tray.icon)
    assert wait_for(lambda: load_config().device.rotate == 0)

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


def test_menu_opens_the_app_on_its_views(tray, monkeypatch):
    """The window (or the browser where there is none) shows the view the item names."""
    from libre_panel import window

    opened = []
    monkeypatch.setattr(window, "open_window", lambda url, view, **kw: opened.append(view))
    menu = items(tray.build_menu())
    for name in ("Open Libre Panel", "Design", "Settings"):
        menu[name](tray.icon)
    assert wait_for(lambda: sorted(opened) == ["design", "home", "settings"])


def test_the_menu_points_to_the_standby_clip_question_while_start_asks_it(tray, monkeypatch):
    """One line, only while Start has the question about the panel's standby clip; it opens the
    app there (#/home/clip), and the menu is told when the question is answered."""
    from libre_panel import window

    line = "Smooth motion: confirm the clip name…"
    asking = [False]
    monkeypatch.setattr(tray.app, "clip_question", lambda: asking[0])
    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    assert line not in items(tray.build_menu())
    tray.refresh()
    quiet = tray.icon.menu_updates
    asking[0] = True
    menu = items(tray.build_menu())
    assert list(menu)[1:3] == ["Open Libre Panel", line]
    tray.refresh()
    assert tray.icon.menu_updates == quiet + 1  # a menu that is built when it changes hears of it
    opened = []
    monkeypatch.setattr(window, "open_window", lambda url, view, **kw: opened.append(view))
    menu[line](tray.icon)
    assert wait_for(lambda: opened == ["home/clip"])
    asking[0] = False  # answered on Start
    tray.refresh()
    assert tray.icon.menu_updates == quiet + 2 and line not in items(tray.build_menu())


def test_the_line_about_the_standby_clip_is_in_german_too(tray, monkeypatch):
    from libre_panel import i18n

    monkeypatch.setattr(tray.app, "clip_question", lambda: True)
    i18n.set_language("de")
    assert "Flüssige Bewegung: Clip-Namen bestätigen …" in items(tray.build_menu())


def test_a_menu_that_cannot_tell_whether_to_ask_asks_nothing(tray, monkeypatch):
    def broken():
        raise RuntimeError("no way to tell")

    monkeypatch.setattr(tray.app, "clip_question", broken)
    assert tray.asks_for_clip() is False
    tray.refresh()  # and the icon goes on


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


def test_the_tray_says_what_keeps_the_temperatures_away_while_it_is_the_case(tray):
    """The hint of the panel's state is a line under the headline of the menu and the second
    line of the tooltip, and goes with the cause."""
    from test_console_pages import pretend_windows

    from libre_panel.sensors.librehardwaremonitor import LhmStatus

    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    tray.app.panel.platform = "linux"  # nothing to say elsewhere, whatever the sources are (on
    # a Windows runner LibreHardwareMonitor itself, once it is ready, would have its say)
    tray.refresh()
    quiet = tray.icon.menu_updates
    assert list(items(tray.build_menu()))[1] == "Open Libre Panel"  # nothing to say
    said = "CPU temperature and power need administrator rights: run Libre Panel as administrator."
    lhm = pretend_windows(tray.app, LhmStatus("inprocess", elevated=False, ring0=False, pawnio="2"))
    menu = list(items(tray.build_menu()))
    assert menu[0].startswith("Libre Panel: showing") and menu[1] == said
    assert menu[2] == "Open Libre Panel"
    assert tooltip(tray.app.panel.state()) == f"Libre Panel\n{said}"  # the long target gives way
    tray.refresh()
    assert tray.icon.menu_updates == quiet + 1 and tray.icon.title == f"Libre Panel\n{said}"
    tray.refresh()
    assert tray.icon.menu_updates == quiet + 1  # as it was
    lhm.state = LhmStatus("inprocess", elevated=True, ring0=True, pawnio="2")  # fixed
    tray.refresh()
    assert tray.icon.menu_updates == quiet + 2 and said not in tray.icon.title
    assert list(items(tray.build_menu()))[1] == "Open Libre Panel"


def test_the_tooltip_keeps_the_hint_whole_and_the_trouble_with_the_panel_comes_first():
    state = {"state": "waiting", "target": "", "detail": "no panel found", "hint": "fix it"}
    assert tooltip(state) == "Libre Panel: waiting for the panel\nno panel found"
    assert tooltip({**state, "detail": ""}) == "Libre Panel: waiting for the panel\nfix it"
    assert tooltip({"state": "showing", "target": "P"}) == "Libre Panel: showing on P"
    long = "CPU temperature and power need administrator rights: run Libre Panel as administrator."
    shown = {"state": "showing", "target": 'Turing 9.2" USB panel', "hint": long}
    assert tooltip(shown) == f"Libre Panel\n{long}" and len(tooltip(shown)) <= 120
    endless = f"{long} {long}"  # more than a tooltip can take: cut at the end, with …
    cut = tooltip({**shown, "hint": endless})
    assert cut.startswith("Libre Panel\nCPU temperature") and cut.endswith("…") and len(cut) == 120


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
    assert headline({"state": "showing", "target": "P", "mode": "game"}).endswith("P (In game)")


def test_the_theme_menu_offers_the_panel_s_themes_by_name(tray):
    """On a 9.2" bar the themes made for it come first, by name; those for other panels and the
    round report's wait in "More themes", the screens Libre Panel grew out of in their own; a
    theme chosen on a bar on end is checked by its name, not by its version's."""
    from libre_panel.config import set_config_value

    set_config_value("device.model", "turing-9.2-usb")
    themes = items(items(tray.build_menu())["Theme"].submenu)
    first = list(themes)[: list(themes).index("More themes")]
    assert first == ["Almanac", "Almanac Analog", "Arena", "Encore", "High Contrast", "Lumen",
                     "Nimbus", "Studio"]  # fmt: skip
    more = items(themes["More themes"].submenu)
    assert {"Libre Default", "Column", "Slate", "Round report"} <= set(more)
    assert {"Lumen Companion", "Arena Companion"} <= set(more)  # for a second panel
    assert {"Agents", "Agents Claude"} <= set(more)  # for those who set up AI activity
    assert "Lumen" not in more and "SPUR II" in items(themes["SPUR II"].submenu)
    assert more["Libre Default"].checked  # config.toml names it: checked where it is
    themes["Lumen"](tray.icon)
    assert wait_for(lambda: load_config().theme == "lumen")
    assert items(items(tray.build_menu())["Theme"].submenu)["Lumen"].checked
    set_config_value("theme", "lumen-vertical")  # (an older way to choose it on end)
    assert items(items(tray.build_menu())["Theme"].submenu)["Lumen"].checked


def test_the_tray_speaks_german(tray):
    from libre_panel import i18n

    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    i18n.set_language("de")
    menu = items(tray.build_menu())
    assert list(menu)[0].startswith("Libre Panel: läuft auf ")
    assert {"Gestalten", "Panel pausieren", "Auf dem Kopf", "Libre Panel beenden"} <= set(menu)
    assert "Weitere Themes" in items(menu["Theme"].submenu)  # (the round report's)
    state = {"state": "showing", "target": "P", "mode": "game"}
    assert headline(state) == "Libre Panel: läuft auf P (Im Spiel)"
    assert headline({"state": "error", "target": ""}) == "Libre Panel: etwas stimmt nicht"


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
        if not wait_for(lambda: log.exists() and "tray icon ready" in log.read_text(), 30):
            proc.kill()  # say what happened instead of a bare "assert False"
            seen = log.read_text(errors="replace") if log.exists() else "(no log)"
            output = proc.stdout.read().decode(errors="replace")
            pytest.fail(f"no tray icon after 30 s\n--- log\n{seen}\n--- output\n{output}")
        url = running_instance()["editor"]
        import urllib.request

        from libre_panel.editor import access

        request = urllib.request.Request(
            url + "api/app",
            data=json.dumps({"action": "quit"}).encode(),
            # the token the app wrote for this user: the way `libre-panel quit` signs in
            headers={access.HEADER: access.read_token(url), "Content-Type": "application/json"},
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


def test_cli_quit_stops_the_background_app(isolated_home, capsys):
    write_config(isolated_home)
    assert cli.main(["quit"]) == 0
    assert "not running" in capsys.readouterr().out
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    result = []
    thread = threading.Thread(target=lambda: result.append(run_app(app, use_icon=False)))
    from libre_panel.instance import InstanceLock

    lock = InstanceLock().acquire()
    try:
        thread.start()
        assert wait_for(lambda: running_instance() is not None)
        # the lock belongs to "another process" here: release it when the app quits
        app.on_quit(lock.release)
        assert cli.main(["quit", "--timeout", "10"]) == 0
        assert "has quit" in capsys.readouterr().out
        thread.join(10)
        assert result == [0]
    finally:
        lock.release()


def test_cli_prints_the_udev_rule(capsys):
    assert cli.main(["udev-rules"]) == 0
    out = capsys.readouterr().out
    assert 'ATTRS{idVendor}=="1cbe"' in out and 'TAG+="uaccess"' in out


def test_open_path_starts_the_default_program_or_says_it_cannot(monkeypatch, tmp_path):
    from libre_panel import tray as tray_module

    started, opened = [], []
    monkeypatch.setattr(tray_module.subprocess, "Popen", started.append)
    monkeypatch.setattr(tray_module.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(tray_module.sys, "platform", "win32")
    tray_module.open_path(tmp_path)
    assert opened == [tmp_path] and not started

    monkeypatch.setattr(tray_module.sys, "platform", "linux")
    monkeypatch.setattr(tray_module.shutil, "which", lambda name: "/usr/bin/" + name)
    tray_module.open_path(tmp_path)
    assert started == [["xdg-open", str(tmp_path)]]

    monkeypatch.setattr(tray_module.shutil, "which", lambda name: None)  # nothing to ask
    with pytest.raises(OSError, match="not supported on this system"):
        tray_module.open_path(tmp_path)
    assert len(started) == 1


def test_no_icon_update_during_or_after_the_stop(tray):
    """On X11 an update that meets the stop waits forever: stop waits for a
    running update, and nothing updates the icon afterwards."""
    events = []

    class SlowIcon(FakeIcon):
        def __setattr__(self, name, value):
            if name == "title" and getattr(self, "stopped", False):
                events.append("update after stop")
            if name == "title" and getattr(self, "armed", False):
                events.append("update start")
                time.sleep(0.3)  # an update in progress while Quit arrives
                events.append("update end")
            object.__setattr__(self, name, value)

        def stop(self):
            events.append("stop")
            self.stopped = True

    tray.icon = SlowIcon()
    object.__setattr__(tray.icon, "armed", True)
    tray._last = None
    updater = threading.Thread(target=tray.refresh)
    updater.start()
    assert wait_for(lambda: "update start" in events)  # the update is running
    tray.stop_icon()
    updater.join()
    tray.refresh()  # the tray's poll loop may still come by once
    assert events == ["update start", "update end", "stop"]


def test_quit_always_ends_the_process():
    """Even if a GUI library never returns, the process ends after Quit."""
    code = (
        "import time\nfrom libre_panel.cli import _exit_later\n_exit_later(0.5)\ntime.sleep(60)\n"
    )
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c", code], timeout=30)
    assert result.returncode == 0 and time.monotonic() - started < 20


def test_quit_goes_on_where_an_x11_tray_s_loop_does_not_end(isolated_home):
    """pystray's loop for an X11 tray now and then misses the wake-up its stop() sends and
    waits for an X event that never comes: Quit must not wait for it (the release's smoke test
    saw the app end only through its last-resort exit, one quit in a few)."""
    import types

    write_config(isolated_home)
    app = BackgroundApp(
        port=0,
        autostart=Autostart("win32", registry=FakeRegistry()),
        autostart_command=["LibrePanel.exe", "tray", "--background"],
    )
    app.start()
    never = threading.Event()

    class Icon:  # pystray's X11 icon, as far as the tray can tell, whose loop never ends
        def __init__(self, *args):
            self.stopped = False

        def run(self, setup=None):
            never.wait()

        def stop(self):
            self.stopped = True

    Icon.__module__ = "pystray._xorg"
    backend = types.SimpleNamespace(Icon=Icon, Menu=pystray.Menu, MenuItem=pystray.MenuItem)
    tray = Tray(app, backend)
    running = threading.Thread(target=tray.run)
    running.start()
    try:
        assert wait_for(lambda: tray.stop_icon in app._on_quit)  # the tray is running
        app.quit()
        running.join(Tray.STOP_S + 3)
        assert not running.is_alive()
        assert tray.icon.stopped
    finally:
        never.set()
        app.shutdown()


def test_more_panels_are_in_the_tray_each_with_its_state_switch_and_theme(tray, isolated_home):
    from libre_panel import identify

    assert "More panels" not in items(tray.build_menu())  # none beside the first
    with (isolated_home / "config.toml").open("a", encoding="utf-8") as config:
        config.write('\n[[panels]]\ndriver = "virtual"\ntheme = "lumen"\n')
    assert wait_for(lambda: tray.app.panel.state().get("more"))
    more = items(items(tray.build_menu())["More panels"].submenu)
    assert list(more) == ["Panel 2", "Which is which?"]
    second = items(more["Panel 2"].submenu)
    assert wait_for(lambda: list(items(items(items(tray.build_menu())["More panels"].submenu)
                                       ["Panel 2"].submenu))[0].startswith("Shows"))  # fmt: skip
    assert second["Panel 2 on"].checked
    themes = items(second["Theme"].submenu)
    assert themes["Lumen"].checked
    themes["Slate"](tray.icon)
    assert wait_for(lambda: load_config().panels[0].theme == "slate")
    assert load_config().theme == "libre-default"  # the first panel's as it was
    # its brightness: as the first panel's, or its own
    shades = items(second["Brightness"].submenu)
    assert shades["As panel 1's"].checked and not shades["50 %"].checked
    shades["50 %"](tray.icon)
    assert wait_for(lambda: load_config().panels[0].follow_brightness is False)
    assert load_config().panels[0].device.brightness == 50
    again = items(items(items(items(tray.build_menu())["More panels"].submenu)["Panel 2"].submenu)
                  ["Brightness"].submenu)  # fmt: skip
    assert again["50 %"].checked and not again["As panel 1's"].checked
    again["As panel 1's"](tray.icon)
    assert wait_for(lambda: load_config().panels[0].follow_brightness is True)
    second["Panel 2 on"](tray.icon)
    assert wait_for(lambda: load_config().panels[0].on is False)
    more["Which is which?"](tray.icon)
    assert identify.showing()
    identify._until = 0.0


def test_the_timer_is_in_the_tray_while_it_runs(tray, isolated_home):
    assert "Timer" not in items(tray.build_menu())  # the service is off
    with (isolated_home / "config.toml").open("a", encoding="utf-8") as config:
        config.write('\n[services]\nenabled = ["timer"]\n\n[services.timer]\nminutes = 10\n')
    tray.app.follow_config()
    assert wait_for(lambda: "Timer" in items(tray.build_menu()))
    timer = items(items(tray.build_menu())["Timer"].submenu)
    assert list(timer)[0] == "Stopped at 10:00"
    timer["Start or pause"](tray.icon)
    assert wait_for(lambda: tray._timer().running)
    assert wait_for(lambda: list(items(items(tray.build_menu())["Timer"].submenu))[0]
                    .startswith("Runs · "))  # fmt: skip
    timer["Back to the start"](tray.icon)
    assert wait_for(lambda: not tray._timer().running)

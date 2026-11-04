"""Tray menu and the background app's command line."""

import json
import os
import shutil
import signal
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


def a_theme_in_a_set(home):
    """A theme of one's own that names a collection (as a set of themes made to go together
    may): Libre Panel's own name none."""
    folder = home / "themes" / "desk"
    folder.mkdir(parents=True)
    data = {"format": "libre-panel-theme/1", "name": "Desk", "collection": "Desk Set",
            "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
            "widgets": [{"type": "text", "id": "t", "text": "Desk"}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


def test_menu_layout_and_state(tray, isolated_home):
    a_theme_in_a_set(isolated_home)
    assert wait_for(lambda: tray.app.panel.state()["state"] == "showing")
    menu = items(tray.build_menu())
    assert list(menu)[0].startswith("Libre Panel: showing on PNG file")
    # (on Windows without LibreHardwareMonitor a line under it says what it needs, once the
    # sensors have looked for it: before or after the menu is made)
    listed = [text for text in list(menu)[1:] if "LibreHardwareMonitor" not in text]
    # what most people come for first, then the views and what is set once
    assert [
        "Open Libre Panel",
        "Theme",
        "Brightness",
        "Pause panel",
        "Restart panels",
        "Design",
        "Settings",
        "Upside down",
        "Start with system",
        "Open settings folder",
        "Open log",
        "Quit Libre Panel",
    ] == listed
    assert menu["Open Libre Panel"].default  # also a click on the icon
    themes = items(menu["Theme"].submenu)  # by name; the panel is not known: all sizes
    assert {"Libre Default", "Orbit", "Slate", "Column", "Lumen"} <= set(themes)
    assert themes["Libre Default"].checked and not themes["Slate"].checked
    assert "Desk" in items(themes["Desk Set"].submenu) and "Desk" not in themes  # (apart)
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


def test_the_theme_menu_offers_the_panel_s_themes_by_name(tray, isolated_home):
    """On a 9.2" bar the themes made for it come first, by name; those for other panels and the
    round report's wait in "More themes", a collection in its own; a theme chosen on a bar on
    end is checked by its name, not by its version's."""
    from libre_panel.config import set_config_value

    a_theme_in_a_set(isolated_home)
    set_config_value("device.model", "turing-9.2-usb")
    themes = items(items(tray.build_menu())["Theme"].submenu)
    first = list(themes)[: list(themes).index("More themes")]
    assert first == ["Almanac", "Almanac Analog", "Arena", "Encore", "High Contrast", "Lumen",
                     "Nimbus", "Studio", "Terra"]  # fmt: skip
    more = items(themes["More themes"].submenu)
    assert {"Libre Default", "Column", "Slate", "Round report"} <= set(more)
    assert {"Lumen Companion", "Arena Companion"} <= set(more)  # for a second panel
    assert {"Agents", "Agents Claude"} <= set(more)  # for those who set up AI activity
    assert "Lumen" not in more and "Desk" in items(themes["Desk Set"].submenu)
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


x11_tray = pytest.mark.skipif(
    not (
        sys.platform.startswith("linux")
        and shutil.which("xvfb-run")
        and shutil.which("stalonetray")
    ),
    reason="needs Xvfb and stalonetray (a system tray for X11)",
)


@x11_tray
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
        start_new_session=True,  # one group for the X server, the tray and the app: ended together
    )

    def kill_all():
        """Everything the script started: killing xvfb-run alone leaves the app and the tray
        holding the pipe, and reading the output below would wait for them for ever."""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        log = isolated_home / "logs" / "libre-panel.log"
        if not wait_for(lambda: log.exists() and "tray icon ready" in log.read_text(), 30):
            kill_all()  # say what happened instead of a bare "assert False"
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
        kill_all()
    output = proc.stdout.read().decode(errors="replace")
    assert "Traceback" not in output, output
    assert running_instance() is None


def test_an_x11_icon_s_messages_from_other_threads_go_apart():
    """What another thread changes reaches the X11 icon's loop through a connection of its own:
    python-xlib loses track of who sends on one that two threads use, and the loop then waits
    for ever (the icon never showed, or never went). The loop's own work stays on the loop's."""
    pytest.importorskip("Xlib")
    import Xlib.X

    from libre_panel.tray import _messages_apart

    class LoopDisplay:
        def __init__(self):
            self.flushed = []

        def flush(self):
            self.flushed.append(threading.current_thread().name)

        def screen(self):
            return "the screen"

    class Icon:  # pystray's X11 icon, as far as the messages go
        def __init__(self):
            self._display = LoopDisplay()
            self.sent = []

        def _send_message(self, window, client_type, l0=0, l1=0, l2=0, l3=0):
            self.sent.append((window, client_type, l0, l1, l2, l3))

    class Connection:
        def __init__(self):
            self.events, self.flushes = [], 0

        def send_event(self, window, event, event_mask=0):
            self.events.append((window, event, event_mask))

        def flush(self):
            self.flushes += 1

    opened = []

    def connect():
        opened.append(Connection())
        return opened[-1]

    window = type("Window", (), {"id": 0x2A00001})()
    icon = Icon()
    loop_display = icon._display
    on_loop = threading.Event()

    def loop_work():  # what the loop sends and flushes itself: the dock request, say
        icon._send_message(window, 7, 0, 0x2A00001)
        icon._display.flush()
        on_loop.set()

    loop = threading.Thread(target=loop_work, name="tray-loop")
    assert _messages_apart(icon, loop, connect)
    assert icon._thread is loop  # (before pystray's loop sets it: the setup may ask first)
    assert opened == []  # opened with the first message, not before
    for client_type in (5, 6):  # e.g. "show" and "stop", from the setup thread and Quit's
        icon._send_message(window, client_type)
        icon._display.flush()
    assert len(opened) == 1
    connection = opened[0]
    assert [
        (w, e.type, e.client_type, e.window, e.data, mask) for w, e, mask in connection.events
    ] == [
        (window, Xlib.X.ClientMessage, 5, 0x2A00001, (32, (Xlib.X.CurrentTime, 0, 0, 0, 0)), 0),
        (window, Xlib.X.ClientMessage, 6, 0x2A00001, (32, (Xlib.X.CurrentTime, 0, 0, 0, 0)), 0),
    ]
    assert connection.flushes == 2
    assert loop_display.flushed == [] and icon.sent == []  # the loop's connection: untouched
    loop.start()
    assert on_loop.wait(5)
    loop.join(5)
    assert icon.sent == [(window, 7, 0, 0x2A00001, 0, 0)]  # on the loop: through the loop's own
    assert loop_display.flushed == ["tray-loop"]
    assert len(connection.events) == 2 and len(opened) == 1
    assert icon._display.screen() == "the screen"  # all else is the loop's connection
    # a pystray built otherwise is left as it is
    other = type("Icon", (), {})()
    assert not _messages_apart(other, loop, connect)
    assert vars(other) == {}


@x11_tray
def test_an_x11_icon_shows_and_goes_every_time(tmp_path):
    """Sixty X11 icons in turn, each shown, given a picture and a title from the setup thread
    and stopped from another: before their messages went apart, about one in ten never showed
    or never went (and so did the app's icon now and then, the tray test in CI once)."""
    script = tmp_path / "icons.py"
    script.write_text(
        """
import os, sys, threading
from PIL import Image
import pystray
from libre_panel.tray import _messages_apart

runs = int(sys.argv[1])
pictures = [Image.new("RGBA", (64, 64), c) for c in ((40, 120, 220, 255), (220, 120, 40, 255))]
for i in range(runs):
    shown = threading.Event()

    def setup(icon):
        icon.visible = True
        for k in range(3):  # what the tray's refresh does, from this thread
            icon.icon = pictures[k % 2]
            icon.title = f"run {i}, {k}"
        shown.set()

    icon = pystray.Icon("test", pictures[0], "test")
    loop = threading.Thread(target=icon.run, kwargs={"setup": setup}, daemon=True)
    assert _messages_apart(icon, loop)
    loop.start()
    if not shown.wait(10):
        print(f"run {i}: no icon after 10 s", flush=True)
        os._exit(1)
    stop = threading.Thread(target=icon.stop, daemon=True)
    stop.start()
    stop.join(10)
    loop.join(10)
    if stop.is_alive() or loop.is_alive():
        print(f"run {i}: the icon did not go after 10 s", flush=True)
        os._exit(1)
print(f"{runs} icons shown and gone", flush=True)
os._exit(0)
""",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "PYSTRAY_BACKEND"}
    shell = (
        "stalonetray --geometry 4x1 -i 32 >/dev/null 2>&1 & TRAY=$!; sleep 1; "
        f"{sys.executable} {script} 60; CODE=$?; kill $TRAY; exit $CODE"
    )
    proc = subprocess.run(
        ["xvfb-run", "-a", "bash", "-c", shell],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "60 icons shown and gone" in proc.stdout


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
    """An X11 tray's loop that does not end: Quit must not wait for it (the release's smoke
    test saw the app end only through its last-resort exit, one quit in a few, before the
    icon's messages went apart: python-xlib had lost its way on the loop's connection)."""
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
    assert load_config().panels[0].follow is False  # (by name: it stays)
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

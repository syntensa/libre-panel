"""Keyboard shortcuts for the whole PC (hotkeys.py): how a combination is written and checked,
what config.toml and the settings take, what each one does in the background app, and, where
there is an X server (Xvfb) or Windows, that a pressed one arrives."""

import os
import shutil
import subprocess
import sys
import threading
import time

import pytest
from test_service import wait_for

from libre_panel import hotkeys
from libre_panel.config import ConfigError, load_config, parse_config, set_config_value
from libre_panel.hotkeys import Combo, Hotkeys, parse


def test_a_combination_is_written_one_way():
    assert str(parse("ctrl+alt+p")) == "Ctrl+Alt+P"
    assert str(parse("Shift + Strg + f5")) == "Ctrl+Shift+F5"
    assert str(parse("win+alt+pageup")) == "Alt+Win+PageUp"
    assert parse("F13") == Combo((), "F13") and parse("pause") == Combo((), "Pause")
    assert str(parse("Super+Space")) == "Win+Space"


@pytest.mark.parametrize(
    "text",
    ["", "P", "Shift+P", "Ctrl", "Ctrl+Alt", "Ctrl+P+Q", "Ctrl+Ä", "Ctrl+F25", "Ctrl+Tab", 5],
)
def test_what_is_no_shortcut_is_said(text):
    with pytest.raises(ValueError):
        parse(text)


def test_config_toml_takes_them_written_one_way_and_each_once():
    config = parse_config({"hotkeys": {"pause": "ctrl+alt+p", "open": "", "brightness_up": "F13"}})
    assert config.hotkeys == {"pause": "Ctrl+Alt+P", "brightness_up": "F13"}
    for wrong in (
        {"dance": "Ctrl+Alt+D"},
        {"pause": "P"},
        {"pause": 5},
        {"pause": "Ctrl+Alt+P", "open": "alt+ctrl+p"},
    ):
        with pytest.raises(ConfigError):
            parse_config({"hotkeys": wrong})
    assert parse_config({}).hotkeys == {}


def test_switched_off_they_are_all_let_go_and_their_keys_kept(isolated_home, monkeypatch):
    from libre_panel import service

    config = parse_config({"hotkeys": {"enabled": False, "pause": "Ctrl+Alt+P"}})
    assert config.hotkeys == {"pause": "Ctrl+Alt+P"} and not config.hotkeys_enabled
    assert service._held(config) == {}
    assert parse_config({}).hotkeys_enabled and service._held(parse_config({})) == {}
    with pytest.raises(ConfigError):
        parse_config({"hotkeys": {"enabled": "no"}})
    # the switch in Settings writes [hotkeys] enabled and leaves the keys as they were
    set_config_value("hotkeys.pause", "Ctrl+Alt+P")
    set_config_value("hotkeys.enabled", False)
    held = load_config()
    assert held.hotkeys == {"pause": "Ctrl+Alt+P"} and not held.hotkeys_enabled
    applied = []
    monkeypatch.setattr(Hotkeys, "apply", lambda self, keys: applied.append(dict(keys)))
    app = service.BackgroundApp(port=0)
    app._config_loaded(held)
    set_config_value("hotkeys.enabled", True)
    app._config_loaded(load_config())
    assert wait_for(lambda: applied == [{}, {"pause": "Ctrl+Alt+P"}])
    app.shutdown()


class FakeBackend:
    made: list = []

    def __init__(self, combos, fire):
        self.combos, self.fire = combos, fire
        self.errors = {
            action: ("taken" if combo.key == "T" else "") for action, combo in combos.items()
        }
        self.stopped = False
        FakeBackend.made.append(self)

    def start(self, wait=3.0):
        pass

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(hotkeys, "support", lambda: (True, ""))
    FakeBackend.made = []
    done = []
    keys = Hotkeys(done.append, backend=lambda: FakeBackend)
    return keys, done


def test_the_combinations_are_held_again_only_when_they_change(fake):
    keys, done = fake
    keys.apply({"pause": "Ctrl+Alt+P", "open": "Ctrl+Alt+T", "dance": "Ctrl+Alt+D"})
    first = FakeBackend.made[-1]
    assert first.combos == {"pause": parse("Ctrl+Alt+P"), "open": parse("Ctrl+Alt+T")}
    keys.apply({"pause": "ctrl+alt+p", "open": "Ctrl+Alt+T"})
    assert len(FakeBackend.made) == 1 and not first.stopped
    state = keys.state()
    assert state["supported"] and state["keys"] == {
        "pause": {"combo": "Ctrl+Alt+P", "error": ""},
        "open": {"combo": "Ctrl+Alt+T", "error": "taken"},
    }
    keys.apply({"pause": "Ctrl+Alt+P", "open": "nonsense"})
    assert first.stopped and len(FakeBackend.made) == 2
    assert keys.state()["keys"]["open"]["error"]  # what is no combination is said
    FakeBackend.made[-1].fire("pause")
    assert wait_for(lambda: done == ["pause"])
    keys.apply({})
    assert FakeBackend.made[-1].stopped and keys.state()["keys"] == {}


def test_without_support_nothing_is_held_and_it_says_why(monkeypatch):
    monkeypatch.setattr(hotkeys, "support", lambda: (False, "Wayland lets no program take keys."))
    FakeBackend.made = []
    keys = Hotkeys(lambda action: None, backend=lambda: FakeBackend)
    keys.apply({"pause": "Ctrl+Alt+P"})
    assert FakeBackend.made == []
    assert keys.state() == {
        "supported": False,
        "why": "Wayland lets no program take keys.",
        "keys": {"pause": {"combo": "Ctrl+Alt+P", "error": ""}},
    }


def test_wayland_is_told_apart(monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux's displays")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert hotkeys.support()[0] is False
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert hotkeys.support() == (False, "There is no X display.")


def test_what_each_shortcut_does_in_the_background_app(isolated_home):
    from libre_panel.service import BackgroundApp

    set_config_value("device.brightness", 95)
    app = BackgroundApp(port=0)
    opened = []
    app.editor.open = opened.append
    app.shortcut("brightness_up")
    assert load_config().device.brightness == 100  # no further than all of it
    app.shortcut("brightness_down")
    app.shortcut("brightness_down")
    assert load_config().device.brightness == 80
    app.shortcut("pause")
    assert app.panel.paused
    app.shortcut("pause")
    assert not app.panel.paused
    app.shortcut("open")
    assert opened == ["home"]
    assert app.snapshot()["hotkeys"]["keys"] == {}
    app.shutdown()  # "resume" set its panel running: left so, it draws on into the next tests


def test_the_settings_take_one_and_refuse_a_shortcut_two_actions_would_share(isolated_home):
    from libre_panel.editor.settings import Restart, Settings, SettingsError

    settings = Settings(None, None, Restart())
    settings.write({"set": {"hotkeys.pause": "alt+ctrl+p"}})
    assert load_config().hotkeys == {"pause": "Ctrl+Alt+P"}
    with pytest.raises(SettingsError):
        settings.write({"set": {"hotkeys.open": "Ctrl+Alt+P"}})
    with pytest.raises(SettingsError):
        settings.write({"set": {"hotkeys.open": "P"}})
    settings.write({"set": {"hotkeys.pause": None}})
    assert load_config().hotkeys == {}


# -- a real X server: the keys are pressed with XTest and arrive -----------------------------------


@pytest.fixture
def x_display(monkeypatch):
    if not (sys.platform.startswith("linux") and shutil.which("Xvfb")):
        pytest.skip("needs Xvfb")
    pytest.importorskip("Xlib")
    number = next(n for n in range(140, 200) if not os.path.exists(f"/tmp/.X{n}-lock"))
    server = subprocess.Popen(
        ["Xvfb", f":{number}", "-screen", "0", "320x240x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_for(lambda: os.path.exists(f"/tmp/.X11-unix/X{number}"), 10):
        server.kill()
        pytest.skip("Xvfb did not start")
    monkeypatch.setenv("DISPLAY", f":{number}")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield f":{number}"
    server.terminate()
    server.wait(5)


def press(display_name, *names):
    """Press the keys one after the other and let them go the other way round (XTest)."""
    from Xlib import XK, X, display
    from Xlib.ext import xtest

    shown = display.Display(display_name)
    codes = [shown.keysym_to_keycode(XK.string_to_keysym(name)) for name in names]
    for code in codes:
        xtest.fake_input(shown, X.KeyPress, code)
    for code in reversed(codes):
        xtest.fake_input(shown, X.KeyRelease, code)
    shown.sync()
    shown.close()


def test_on_x11_a_pressed_shortcut_arrives_and_one_taken_is_said(x_display):
    from Xlib import XK, X, display

    done = []
    keys = Hotkeys(done.append)
    # another program holds Ctrl+Alt+O already
    other = display.Display(x_display)
    code = other.keysym_to_keycode(XK.string_to_keysym("o"))
    other.screen().root.grab_key(code, X.ControlMask | X.Mod1Mask, True, X.GrabModeAsync,
                                 X.GrabModeAsync)  # fmt: skip
    other.sync()
    try:
        keys.apply({"pause": "Ctrl+Alt+P", "open": "Ctrl+Alt+O", "brightness_up": "Pause",
                    "brightness_down": "F13"})  # fmt: skip
        state = keys.state()["keys"]
        assert state["pause"]["error"] == "" and state["open"]["error"] == hotkeys.TAKEN
        assert state["brightness_down"]["error"] == "This keyboard has no F13."  # (Xvfb's)
        press(x_display, "Control_L", "Alt_L", "p")
        assert wait_for(lambda: done == ["pause"], 5)
        press(x_display, "p")  # without the modifiers it is typing, not the shortcut
        press(x_display, "Pause")
        assert wait_for(lambda: done == ["pause", "brightness_up"], 5)
        time.sleep(0.3)
        assert done == ["pause", "brightness_up"]
    finally:
        keys.close()
        other.close()
    # let go: another program may have them now
    again = display.Display(x_display)
    from Xlib import error

    caught = error.CatchError(error.BadAccess)
    code = again.keysym_to_keycode(XK.string_to_keysym("p"))
    again.screen().root.grab_key(code, X.ControlMask | X.Mod1Mask, True, X.GrabModeAsync,
                                 X.GrabModeAsync, caught)  # fmt: skip
    again.sync()
    assert caught.get_error() is None
    again.close()


# -- Windows: the system registers it, and its message comes through the loop ----------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' RegisterHotKey")
def test_on_windows_a_registered_shortcut_reaches_the_action():
    done = []
    keys = Hotkeys(done.append)
    keys.apply({"open": "Ctrl+Alt+Shift+F23"})
    try:
        backend = keys.current
        assert backend is not None and backend.ready.is_set()
        if backend.errors.get("open"):
            pytest.skip(f"this desktop takes no shortcuts: {backend.errors['open']}")
        number = next(n for n, action in backend.ids.items() if action == "open")
        backend.post(hotkeys.WM_HOTKEY, number)  # as Windows sends it when the keys are pressed
        assert wait_for(lambda: done == ["open"], 5)
    finally:
        keys.close()
    assert not keys.current
    threading.Event().wait(0.1)

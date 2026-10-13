"""Keyboard shortcuts for the whole PC: a key combination does one thing of the background app,
whatever window is in front (pause the panel, its brightness, open the app).

config.toml's ``[hotkeys]`` names them, ``pause = "Ctrl+Alt+P"`` (Settings → Start and system
sets them by pressing the keys). Windows registers them with the system (``RegisterHotKey``);
on Linux with X11 the X server grabs them for Libre Panel (python-xlib, which the tray uses
too). Wayland lets no program take keys for itself: there they are not offered. A combination
that another program has taken already is said so, not taken from it.
"""

from __future__ import annotations

import logging
import os
import select
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from libre_panel.i18n import t

log = logging.getLogger(__name__)

ACTIONS = ("pause", "brightness_up", "brightness_down", "open")
BRIGHTNESS_STEP = 10  # percent a shortcut turns the brightness up or down

ORDER = ("Ctrl", "Alt", "Shift", "Win")  # how a combination is written
MODIFIERS = {
    "ctrl": "Ctrl", "control": "Ctrl", "strg": "Ctrl", "alt": "Alt", "shift": "Shift",
    "win": "Win", "super": "Win", "meta": "Win", "cmd": "Win",
}  # fmt: skip
NAMED = {
    "space": "Space", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
    "insert": "Insert", "delete": "Delete", "pause": "Pause", "scrolllock": "ScrollLock",
}  # fmt: skip
ALONE = {"Pause", "ScrollLock", *(f"F{n}" for n in range(13, 25))}  # keys nothing types


@dataclass(frozen=True)
class Combo:
    mods: tuple[str, ...]  # in ORDER
    key: str  # "A" to "Z", "0" to "9", "F1" to "F24", or one of NAMED's

    def __str__(self) -> str:
        return "+".join((*self.mods, self.key))


def parse(text: str) -> Combo:
    """``"ctrl+alt+p"`` as a Combo (``Ctrl+Alt+P``); ValueError says what is wrong. A
    combination needs Ctrl, Alt or Win (Shift alone would take a letter from typing), but for
    the keys nothing types: F13 to F24, Pause, Scroll Lock."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(t("a key combination such as Ctrl+Alt+P"))
    mods: set[str] = set()
    key = None
    for part in (p.strip() for p in text.split("+")):
        name = part.lower()
        if name in MODIFIERS:
            mods.add(MODIFIERS[name])
        elif key is not None or not part:
            raise ValueError(t("one key with Ctrl, Alt, Shift or Win: {keys}", keys=text))
        elif len(part) == 1 and part.isascii() and part.isalnum():
            key = part.upper()
        elif name[0] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            key = f"F{int(name[1:])}"
        elif name in NAMED:
            key = NAMED[name]
        else:
            raise ValueError(t("not a key Libre Panel can take: {part}", part=part))
    if key is None:
        raise ValueError(t("one key with Ctrl, Alt, Shift or Win: {keys}", keys=text))
    if not mods & {"Ctrl", "Alt", "Win"} and key not in ALONE:
        raise ValueError(t("{keys} needs Ctrl, Alt or Win", keys=text))
    return Combo(tuple(m for m in ORDER if m in mods), key)


def support() -> tuple[bool, str]:
    """Whether this system lets Libre Panel take keys for itself, and why not."""
    if sys.platform == "win32":
        return True, ""
    if sys.platform.startswith("linux"):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or (
            os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY")
        ):
            return False, t("Wayland lets no program take keys for itself.")
        if not os.environ.get("DISPLAY"):
            return False, t("There is no X display.")
        try:
            import Xlib.display  # noqa: F401
        except ImportError:
            return False, t("python-xlib is not installed.")
        return True, ""
    return False, t("Not on this system.")


TAKEN = "taken"  # what a backend reports for a combination another program has


class _Backend:
    """Holds the combinations on a thread of its own and calls ``fire(action)``."""

    def __init__(self, combos: dict[str, Combo], fire: Callable[[str], None]) -> None:
        self.combos = combos
        self.fire = fire
        self.errors: dict[str, str] = {}
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)

    def start(self, wait: float = 3.0) -> None:
        self.thread.start()
        self.ready.wait(wait)

    def _run(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


# -- Windows ------------------------------------------------------------------------------------

WIN_MODS = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4, "Win": 0x8}
MOD_NOREPEAT = 0x4000
WIN_KEYS = {
    "Space": 0x20, "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24, "Left": 0x25,
    "Up": 0x26, "Right": 0x27, "Down": 0x28, "Insert": 0x2D, "Delete": 0x2E, "Pause": 0x13,
    "ScrollLock": 0x91,
}  # fmt: skip
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409


def win_key(key: str) -> int:
    """The virtual-key code of a key."""
    if key in WIN_KEYS:
        return WIN_KEYS[key]
    if key.startswith("F") and key[1:].isdigit():
        return 0x6F + int(key[1:])  # F1 is 0x70
    return ord(key)  # A to Z and 0 to 9 are their own codes


class _Windows(_Backend):
    def __init__(self, combos: dict[str, Combo], fire: Callable[[str], None]) -> None:
        super().__init__(combos, fire)
        self.thread_id = 0
        self.ids: dict[int, str] = {}

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        )
        user32.GetMessageW.restype = ctypes.c_int
        user32.PeekMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        )
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # the thread's queue, for stop()
        for number, (action, combo) in enumerate(self.combos.items(), start=1):
            mods = sum(WIN_MODS[m] for m in combo.mods) | MOD_NOREPEAT
            if user32.RegisterHotKey(None, number, mods, win_key(combo.key)):
                self.ids[number] = action
                self.errors[action] = ""
            else:
                code = ctypes.get_last_error()
                taken = code == ERROR_HOTKEY_ALREADY_REGISTERED
                self.errors[action] = TAKEN if taken else f"Windows error {code}"
        self.ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in self.ids:
                self.fire(self.ids[msg.wParam])
        for number in self.ids:
            user32.UnregisterHotKey(None, number)

    def post(self, message: int, wparam: int = 0) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW.argtypes = (
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.PostThreadMessageW(self.thread_id, message, wparam, 0)

    def stop(self) -> None:
        if self.thread_id:
            self.post(WM_QUIT)
        self.thread.join(2.0)


# -- X11 ----------------------------------------------------------------------------------------

X11_KEYS = {"PageUp": "Prior", "PageDown": "Next", "Space": "space", "ScrollLock": "Scroll_Lock"}


class _X11(_Backend):
    def __init__(self, combos: dict[str, Combo], fire: Callable[[str], None]) -> None:
        super().__init__(combos, fire)
        self.stopping = threading.Event()

    def _run(self) -> None:
        from Xlib import XK, X, display, error

        try:
            shown = display.Display()
        except Exception as exc:  # no X server to talk to (a display that went away)
            self.errors = {action: str(exc) for action in self.combos}
            self.ready.set()
            return
        masks = {"Shift": X.ShiftMask, "Ctrl": X.ControlMask, "Alt": X.Mod1Mask, "Win": X.Mod4Mask}
        relevant = X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask
        locks = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)  # Caps and Num Lock
        root = shown.screen().root
        grabs: dict[tuple[int, int], str] = {}
        for action, combo in self.combos.items():
            name = X11_KEYS.get(combo.key, combo.key.lower() if len(combo.key) == 1 else combo.key)
            keycode = shown.keysym_to_keycode(XK.string_to_keysym(name))
            if not keycode:
                self.errors[action] = t("This keyboard has no {key}.", key=combo.key)
                continue
            mask = sum(masks[m] for m in combo.mods)
            caught = error.CatchError(error.BadAccess)
            for lock in locks:
                root.grab_key(keycode, mask | lock, True, X.GrabModeAsync, X.GrabModeAsync, caught)
            shown.sync()
            if caught.get_error():
                for lock in locks:
                    root.ungrab_key(keycode, mask | lock)
                self.errors[action] = TAKEN
            else:
                grabs[(keycode, mask)] = action
                self.errors[action] = ""
        shown.sync()
        self.ready.set()
        try:
            while not self.stopping.is_set():
                select.select([shown.fileno()], [], [], 0.2)
                while shown.pending_events():
                    event = shown.next_event()
                    if event.type == X.KeyPress:
                        action = grabs.get((event.detail, event.state & relevant))
                        if action:
                            self.fire(action)
        finally:
            for keycode, mask in grabs:
                for lock in locks:
                    root.ungrab_key(keycode, mask | lock)
            shown.close()

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join(2.0)


def _backend() -> type[_Backend]:
    return _Windows if sys.platform == "win32" else _X11


class Hotkeys:
    """The shortcuts the background app holds: ``apply`` the ones config.toml names, and
    ``act(action)`` is called (on a thread of its own) when one is pressed."""

    def __init__(self, act: Callable[[str], None], backend: Callable[[], type] = _backend) -> None:
        self.act = act
        self.backend_of = backend
        self.wanted: dict[str, Combo] = {}
        self.wrong: dict[str, str] = {}  # what config.toml names that is no combination
        self.current: _Backend | None = None
        self.lock = threading.Lock()

    def _fire(self, action: str) -> None:
        log.info("shortcut: %s", action)
        threading.Thread(
            target=self._act, args=(action,), name="hotkey-action", daemon=True
        ).start()

    def _act(self, action: str) -> None:
        try:
            self.act(action)
        except Exception:
            log.exception("the shortcut for %s failed", action)

    def apply(self, named: dict[str, str]) -> None:
        """Hold the combinations config.toml's ``[hotkeys]`` names (the same as now: nothing)."""
        combos: dict[str, Combo] = {}
        wrong: dict[str, str] = {}
        for action, text in named.items():
            if action not in ACTIONS or not text:
                continue
            try:
                combos[action] = parse(text)
            except ValueError as exc:
                wrong[action] = str(exc)
        with self.lock:
            self.wrong = wrong
            if combos == self.wanted and (self.current is not None or not combos):
                return
            if self.current is not None:
                self.current.stop()
                self.current = None
            self.wanted = combos
            if combos and support()[0]:
                self.current = self.backend_of()(combos, self._fire)
                self.current.start()

    def state(self) -> dict[str, Any]:
        """For the app: whether shortcuts can be had here, and each one with what is wrong."""
        supported, why = support()
        with self.lock:
            errors = dict(self.current.errors) if self.current is not None else {}
            keys = {
                action: {"combo": str(combo), "error": errors.get(action, "")}
                for action, combo in self.wanted.items()
            }
            for action, text in self.wrong.items():
                keys[action] = {"combo": "", "error": text}
        return {"supported": supported, "why": why, "keys": keys}

    def close(self) -> None:
        with self.lock:
            if self.current is not None:
                self.current.stop()
                self.current = None
            self.wanted = {}

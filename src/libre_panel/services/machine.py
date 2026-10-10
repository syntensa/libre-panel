"""What the services ask the computer: which programs run, which one is in front,
whether the microphone is in use, how long the system has been up.

One implementation for every system, on psutil; the Windows parts (the window in
front, the microphone's registry keys) import ctypes and winreg only when they
run. Nothing here touches a program: it only asks for names and paths. The
services take a ``Machine`` as an argument, so tests bring a fake with the same
methods.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import time
from typing import Any, NamedTuple

import psutil

log = logging.getLogger(__name__)

MICROPHONE_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
)


class Program(NamedTuple):
    name: str  # the file name in lower case: "game.exe"
    pid: int
    path: str  # where it lies, or the folder it works in (Wine); "" if unknown


class Machine:
    """The computer the service runs on."""

    def __init__(self, platform: str | None = None) -> None:
        self.platform = platform or sys.platform
        # Only Windows can say which window is in front without a desktop toolkit.
        self.has_foreground = self.platform == "win32"

    def programs(self) -> list[Program]:
        """Every program that runs and can be read, once per process."""
        found = []
        for process in psutil.process_iter(["pid", "name", "exe"]):
            info = process.info
            exe = info.get("exe") or ""
            name, path = os.path.basename(exe) or info.get("name") or "", exe
            if name.lower().startswith("wine"):
                # A Windows program under Wine or Proton: the process is named after
                # the program, and it works in its own folder
                name = info.get("name") or name
                try:
                    path = process.cwd()
                except (psutil.Error, OSError):
                    path = ""
            if name and info.get("pid") is not None:
                found.append(Program(name.lower(), info["pid"], path))
        return found

    def foreground_pid(self) -> int | None:
        """The process of the window in front (0: none); None where that is not known."""
        return foreground_window_pid() if self.has_foreground else None

    def foreground_name(self) -> str | None:
        """The file name, in lower case, of the program whose window is in front."""
        pid = self.foreground_pid()
        if not pid:
            return None
        try:
            process = psutil.Process(pid)
            return (os.path.basename(process.exe()) or process.name()).lower()
        except (psutil.Error, OSError):
            return None

    def microphone_in_use(self) -> bool:
        """Whether a program is recording right now (Windows; elsewhere False)."""
        return microphone_in_use_windows() if self.platform == "win32" else False

    def uptime_s(self) -> float | None:
        """Seconds since the system started, None if it cannot be told."""
        try:
            return max(0.0, time.time() - psutil.boot_time())
        except (OSError, psutil.Error):
            return None


def current() -> Machine:
    """The computer this runs on (tests replace this)."""
    return Machine()


@functools.cache
def _user32() -> Any:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return user32


def foreground_window_pid(user32: Any = None) -> int:
    """The process of the window in front, 0 for none (Windows)."""
    import ctypes
    from ctypes import wintypes

    user32 = user32 or _user32()
    window = user32.GetForegroundWindow()
    if not window:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    return int(pid.value)


def microphone_in_use_windows(winreg: Any = None) -> bool:
    """Windows keeps, for every program that uses the microphone, when it began and
    ended (``LastUsedTimeStop`` is 0 while it records). Only reads."""
    if winreg is None:
        import winreg

    def subkeys(path: str) -> list[str]:
        names = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                while True:
                    try:
                        names.append(winreg.EnumKey(key, len(names)))
                    except OSError:  # no more
                        return names
        except OSError:
            return names

    def records(path: str) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                begun = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
                ended = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
        except OSError:
            return False
        return bool(begun) and ended == 0

    for name in subkeys(MICROPHONE_KEY):
        if name == "NonPackaged":  # classic programs: one key each, named after the path
            nested = f"{MICROPHONE_KEY}\\{name}"
            if any(records(f"{nested}\\{sub}") for sub in subkeys(nested)):
                return True
        elif records(f"{MICROPHONE_KEY}\\{name}"):
            return True
    return False

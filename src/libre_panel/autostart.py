"""Start Libre Panel in the background when the user logs in.

- Windows: a value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
- Linux and other XDG desktops: ``~/.config/autostart/libre-panel.desktop``
- macOS: ``~/Library/LaunchAgents/io.github.syntensa.libre-panel.plist``

Everything is per user and needs no administrator rights. Turning it off
removes exactly what turning it on created.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

APP_NAME = "Libre Panel"
DESKTOP_FILE = "libre-panel.desktop"
MAC_LABEL = "io.github.syntensa.libre-panel"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWED_EXE = "LibrePanel.exe"  # the release build's program without a console window

ICON_PATH = Path(__file__).resolve().parent / "assets" / "libre-panel.png"


def launch_command(config_path: Path | None = None) -> list[str]:
    """The command the system runs at login: the tray app, without opening the editor."""
    options = ["--config", str(Path(config_path).resolve())] if config_path else []
    if getattr(sys, "frozen", False):  # release build
        exe = Path(sys.executable)
        windowed = exe.with_name(WINDOWED_EXE)
        if sys.platform == "win32" and windowed.exists():
            exe = windowed
        return [str(exe), *options, "tray", "--background"]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")  # no console window at login
    return [str(python), "-m", "libre_panel", *options, "tray", "--background"]


class Registry(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class WindowsRegistry:
    """String values under one key in HKEY_CURRENT_USER."""

    def __init__(self, key: str = WINDOWS_RUN_KEY) -> None:
        self.key = key

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return value

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


# Desktop Entry spec: arguments with these characters must be quoted.
_RESERVED = set(" \t\"'\\><~|&;$*?#()`")


def desktop_exec(args: list[str]) -> str:
    """Quote a command for the ``Exec`` key of a .desktop file."""
    if args and "%" in args[0]:
        # GLib looks the program up before it expands %%, so pass the path through env.
        args = ["env", *args]
    quoted = []
    for arg in args:
        if "\n" in arg or "\r" in arg:
            raise ValueError("line breaks cannot be used in a desktop entry command")
        arg = arg.replace("%", "%%")
        if not arg or _RESERVED.intersection(arg):
            arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
        quoted.append(arg)
    # The string-value escaping of desktop files applies on top of the quoting.
    return " ".join(quoted).replace("\\", "\\\\")


class Autostart:
    def __init__(
        self,
        platform: str = sys.platform,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.platform = platform
        self.home = home or Path.home()
        self.environ = os.environ if environ is None else environ
        self._registry = registry

    @property
    def kind(self) -> str:
        if self.platform == "win32":
            return "windows"
        if self.platform == "darwin":
            return "macos"
        return "xdg"

    @property
    def registry(self) -> Registry:
        if self._registry is None:
            self._registry = WindowsRegistry()
        return self._registry

    def path(self) -> Path | None:
        if self.kind == "macos":
            return self.home / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        if self.kind == "xdg":
            base = self.environ.get("XDG_CONFIG_HOME") or str(self.home / ".config")
            return Path(base) / "autostart" / DESKTOP_FILE
        return None

    def location(self) -> str:
        if self.kind == "windows":
            return f"HKEY_CURRENT_USER\\{WINDOWS_RUN_KEY}\\{APP_NAME}"
        return str(self.path())

    def is_enabled(self) -> bool:
        if self.kind == "windows":
            return self.registry.get(APP_NAME) is not None
        path = self.path()
        if not path.is_file():
            return False
        if self.kind == "xdg":
            text = path.read_text(encoding="utf-8", errors="replace")
            return not re.search(r"^Hidden\s*=\s*true\s*$", text, re.MULTILINE | re.IGNORECASE)
        return True

    def enable(self, command: list[str]) -> str:
        if self.kind == "windows":
            self.registry.set(APP_NAME, subprocess.list2cmdline(command))
            return self.location()
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.kind == "macos":
            logs = self.home / "Library" / "Logs"
            agent = {
                "Label": MAC_LABEL,
                "ProgramArguments": command,
                "RunAtLoad": True,
                # Start again after a crash, but not after "Quit" in the menu.
                "KeepAlive": {"SuccessfulExit": False},
                "ProcessType": "Interactive",
                "StandardErrorPath": str(logs / "LibrePanel.log"),
            }
            path.write_bytes(plistlib.dumps(agent))
        else:
            lines = [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={APP_NAME}",
                "GenericName=System monitor panel",
                "Comment=Shows system sensors on a USB monitor panel",
                f"Exec={desktop_exec(command)}",
                f"Icon={ICON_PATH}" if ICON_PATH.exists() else "",
                "Terminal=false",
                "Categories=System;Monitor;",
                "X-GNOME-Autostart-enabled=true",
                # Give the desktop a moment to bring up its tray first.
                "X-GNOME-Autostart-Delay=5",
            ]
            path.write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")
        return self.location()

    def disable(self) -> bool:
        """Remove the entry; returns whether there was one."""
        if self.kind == "windows":
            existed = self.registry.get(APP_NAME) is not None
            self.registry.delete(APP_NAME)
            return existed
        path = self.path()
        if path.exists():
            path.unlink()
            return True
        return False

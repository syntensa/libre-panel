"""Start Libre Panel in the background when the user logs in.

- Windows: a value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
- Linux and other XDG desktops: ``~/.config/autostart/libre-panel.desktop``
- macOS: ``~/Library/LaunchAgents/io.github.syntensa.libre-panel.plist``

Everything is per user and needs no administrator rights. Turning it off
removes exactly what turning it on created.

On Windows some sensors (CPU temperature and power, RAM temperature,
mainboard fans) can be read only by a process with administrator rights.
The built-in LibreHardwareMonitor source asks LibreHardwareMonitor's web
server and needs none, but a plugin that reads the hardware itself does.
For that, ``enable(..., elevated=True)`` sets up a Task Scheduler task that
starts Libre Panel at login with the highest rights, instead of the Run
value. Setting it up and removing it needs an administrator; it is never
done without being asked.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

from libre_panel.i18n import t

APP_NAME = "Libre Panel"
DESKTOP_FILE = "libre-panel.desktop"
MAC_LABEL = "io.github.syntensa.libre-panel"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_TASK = "Libre Panel"  # the Task Scheduler task for an elevated start
# The release build's program without a console window (Windows) or Dock icon (macOS).
WINDOWED_EXE = {"win32": "LibrePanel.exe", "darwin": "LibrePanel"}

ICON_PATH = Path(__file__).resolve().parent / "assets" / "libre-panel.png"


def launch_command(config_path: Path | None = None) -> list[str]:
    """The command the system runs at login: the tray app, without opening the editor."""
    options = ["--config", str(Path(config_path).resolve())] if config_path else []
    if getattr(sys, "frozen", False):  # release build
        appimage = os.environ.get("APPIMAGE")  # the .AppImage file, not its temporary mount
        if appimage and sys.platform.startswith("linux"):
            return [appimage, *options, "tray", "--background"]
        exe = Path(sys.executable)
        windowed = exe.with_name(WINDOWED_EXE.get(sys.platform, exe.name))
        if windowed.exists():
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


class AutostartError(OSError):
    """Autostart could not be changed; the message says what to do."""


class Tasks(Protocol):
    def exists(self, name: str) -> bool: ...
    def create(self, name: str, xml: str) -> None: ...
    def delete(self, name: str) -> None: ...


class WindowsTasks:
    """Task Scheduler tasks, through schtasks.exe."""

    @staticmethod
    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def exists(self, name: str) -> bool:
        try:
            return self._run("/Query", "/TN", name).returncode == 0
        except OSError:  # no schtasks (not Windows)
            return False

    def create(self, name: str, xml: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "task.xml"
            path.write_text(xml, encoding="utf-16")  # what the XML declares
            result = self._run("/Create", "/TN", name, "/XML", str(path), "/F")
        if result.returncode:
            raise AutostartError((result.stderr or result.stdout).strip())

    def delete(self, name: str) -> None:
        result = self._run("/Delete", "/TN", name, "/F")
        if result.returncode:
            raise AutostartError((result.stderr or result.stdout).strip())


def is_admin() -> bool:
    """Whether this process runs with administrator rights (Windows)."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def task_xml(command: list[str], user: str) -> str:
    """A task that starts ``command`` when ``user`` logs in, with the highest rights,
    at normal priority and without a time limit."""
    program, arguments = escape(command[0]), escape(subprocess.list2cmdline(command[1:]))
    user = escape(user)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts {APP_NAME} at login with the rights its sensors need.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT5S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{program}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


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


def writable_by_user(program: Path, home: Path) -> bool:
    """Whether a program lies where the user (and so anything the user runs)
    can replace it: then starting it with the highest rights hands those
    rights to whatever replaced it."""
    try:
        return program.resolve().is_relative_to(home.resolve())
    except OSError:
        return True


class Autostart:
    def __init__(
        self,
        platform: str = sys.platform,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
        registry: Registry | None = None,
        tasks: Tasks | None = None,
        admin: Callable[[], bool] = is_admin,
    ) -> None:
        self.platform = platform
        self.home = home or Path.home()
        self.environ = os.environ if environ is None else environ
        self._registry = registry
        self._tasks = tasks
        self._admin = admin
        self._elevated: tuple[float, bool] | None = None

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

    @property
    def tasks(self) -> Tasks:
        if self._tasks is None:
            self._tasks = WindowsTasks()
        return self._tasks

    def elevated(self, fresh: bool = False) -> bool:
        """Whether Libre Panel starts through the elevated task (Windows). Asked
        often (tray, editor): schtasks runs at most every few seconds, unless
        ``fresh``."""
        if self.kind != "windows":
            return False
        now = time.monotonic()
        if fresh or self._elevated is None or now - self._elevated[0] > 5.0:
            self._elevated = (now, self.tasks.exists(WINDOWS_TASK))
        return self._elevated[1]

    def path(self) -> Path | None:
        if self.kind == "macos":
            return self.home / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        if self.kind == "xdg":
            base = self.environ.get("XDG_CONFIG_HOME") or str(self.home / ".config")
            return Path(base) / "autostart" / DESKTOP_FILE
        return None

    def location(self) -> str:
        if self.kind == "windows":
            if self.elevated():
                return t("Task Scheduler: {name}, with the highest rights", name=WINDOWS_TASK)
            return f"HKEY_CURRENT_USER\\{WINDOWS_RUN_KEY}\\{APP_NAME}"
        return str(self.path())

    def is_enabled(self) -> bool:
        if self.kind == "windows":
            return self.registry.get(APP_NAME) is not None or self.elevated()
        path = self.path()
        if not path.is_file():
            return False
        if self.kind == "xdg":
            text = path.read_text(encoding="utf-8", errors="replace")
            return not re.search(r"^Hidden\s*=\s*true\s*$", text, re.MULTILINE | re.IGNORECASE)
        return True

    def enable(self, command: list[str], elevated: bool = False) -> str:
        """Start ``command`` at login; ``elevated`` (Windows): with the highest rights."""
        if elevated:
            return self._enable_elevated(command)
        if self.kind == "windows":
            if self.elevated(fresh=True):
                raise AutostartError(
                    t(
                        "Libre Panel already starts with the highest rights. To start it "
                        "without, first run as administrator: libre-panel autostart disable"
                    )
                )
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
                "GenericName[de]=Systemmonitor-Panel",
                "Comment=Shows system sensors on a USB monitor panel",
                "Comment[de]=Zeigt Systemsensoren auf einem USB-Monitor-Panel",
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

    def _enable_elevated(self, command: list[str]) -> str:
        if self.kind != "windows":
            raise AutostartError(t("Starting with the highest rights is for Windows only."))
        if not self._admin():
            raise AutostartError(
                t(
                    "Setting up a start with the highest rights needs an administrator: "
                    "open a terminal with 'Run as administrator' and run the command there."
                )
            )
        user = self.environ.get("USERNAME", "")
        domain = self.environ.get("USERDOMAIN", "")
        self.tasks.create(WINDOWS_TASK, task_xml(command, f"{domain}\\{user}" if domain else user))
        self._elevated = None
        self.registry.delete(APP_NAME)  # one start at login, not two
        return self.location()

    def disable(self) -> bool:
        """Remove the entry; returns whether there was one."""
        if self.kind == "windows":
            existed = self.registry.get(APP_NAME) is not None
            self.registry.delete(APP_NAME)
            if self.elevated(fresh=True):
                try:
                    self.tasks.delete(WINDOWS_TASK)
                except AutostartError as exc:
                    raise AutostartError(
                        t(
                            "The start with the highest rights can only be removed by an "
                            "administrator ({reason}): open a terminal with 'Run as "
                            "administrator' and run: libre-panel autostart disable",
                            reason=exc,
                        )
                    ) from exc
                self._elevated = None
                existed = True
            return existed
        path = self.path()
        if path.exists():
            path.unlink()
            return True
        return False

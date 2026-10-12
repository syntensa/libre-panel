"""Start Libre Panel in the background when the user logs in.

- Windows: a value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
- Linux and other XDG desktops: ``~/.config/autostart/libre-panel.desktop``

Everything is per user and needs no administrator rights. Turning it off
removes exactly what turning it on created. On any other system there is no
autostart: asking for it raises ``AutostartError``, which says so.

On Windows some sensors (CPU temperature and power, RAM temperature,
mainboard fans) can be read only by a process with administrator rights. The
built-in LibreHardwareMonitor source reads them inside Libre Panel (so Libre Panel
itself needs the rights), or asks the web server of a LibreHardwareMonitor that has
them; a plugin that reads the hardware itself needs them as well. For these,
``enable(..., elevated=True)`` sets up a Task Scheduler task that starts Libre
Panel at login with the highest rights, instead of the Run value. Setting it up
and removing it needs an administrator; it is never done without being asked.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

from libre_panel.i18n import t

APP_NAME = "Libre Panel"
DESKTOP_FILE = "libre-panel.desktop"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_TASK = "Libre Panel"  # the Task Scheduler task for an elevated start
# The release build's program without a console window (Windows).
WINDOWED_EXE = "LibrePanel.exe"
# sys.platform of the systems with XDG desktops (a BSD's has its version after it: "freebsd14").
XDG_PLATFORMS = ("linux", "freebsd", "openbsd", "netbsd", "dragonfly")

ICON_PATH = Path(__file__).resolve().parent / "assets" / "libre-panel.png"


def launch_command(config_path: Path | None = None) -> list[str]:
    """The command the system runs at login: the tray app, without opening the editor."""
    options = ["--config", str(Path(config_path).resolve())] if config_path else []
    if getattr(sys, "frozen", False):  # release build
        appimage = os.environ.get("APPIMAGE")  # the .AppImage file, not its temporary mount
        if appimage and sys.platform.startswith("linux"):
            return [appimage, *options, "tray", "--background"]
        exe = Path(sys.executable)
        if sys.platform == "win32" and exe.with_name(WINDOWED_EXE).exists():
            exe = exe.with_name(WINDOWED_EXE)
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


def _not_supported() -> AutostartError:
    return AutostartError(t("Autostart is not supported on this system."))


@dataclass(frozen=True)
class TaskInfo:
    """What Task Scheduler says of a task: whether it is on, and the program it starts."""

    enabled: bool
    command: str


def parse_task(xml: str) -> TaskInfo:
    """A task's state from its XML (``schtasks /Query /XML``): ``Settings/Enabled`` (true
    when not given) and the program of its first ``Exec``. Unreadable XML: on, no program."""
    import xml.etree.ElementTree as ET

    text = re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>", "", xml.strip())
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return TaskInfo(enabled=True, command="")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    enabled = root.findtext("t:Settings/t:Enabled", default="true", namespaces=ns)
    command = root.findtext("t:Actions/t:Exec/t:Command", default="", namespaces=ns)
    return TaskInfo(enabled=enabled.strip().lower() != "false", command=command.strip())


def same_program(one: str, other: str) -> bool:
    """Whether two Windows program paths name the same file (case, quotes, slashes)."""
    import ntpath

    def norm(path: str) -> str:
        return ntpath.normcase(ntpath.normpath(os.path.expandvars(path.strip().strip('"'))))

    return bool(one.strip()) and norm(one) == norm(other)


class Tasks(Protocol):
    def exists(self, name: str) -> bool: ...
    def query(self, name: str) -> TaskInfo | None: ...
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

    def query(self, name: str) -> TaskInfo | None:
        """The task's state and program, or None when there is no such task."""
        try:
            result = self._run("/Query", "/TN", name, "/XML")
        except OSError:  # no schtasks (not Windows)
            return None
        return parse_task(result.stdout) if result.returncode == 0 else None

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
        program: str | None = None,
    ) -> None:
        self.platform = platform
        self.home = home or Path.home()
        self.environ = os.environ if environ is None else environ
        self._registry = registry
        self._tasks = tasks
        self._admin = admin
        self._program = program  # what Libre Panel's own task starts (launch_command's)
        self._elevated: tuple[float, str] | None = None

    @property
    def kind(self) -> str:
        """``"windows"``, ``"xdg"`` (Linux, the BSDs) or ``"unsupported"``."""
        if self.platform == "win32":
            return "windows"
        if self.platform.startswith(XDG_PLATFORMS):
            return "xdg"
        return "unsupported"

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

    def task_state(self, fresh: bool = False) -> str:
        """The task named like Libre Panel's elevated start (Windows): "none"; "ours", on and
        starting this program; "off", ours but switched off in Task Scheduler; or "other", one
        that starts another program (a copy elsewhere, a mod). Asked often (tray, editor):
        schtasks runs at most every few seconds, unless ``fresh``."""
        if self.kind != "windows":
            return "none"
        now = time.monotonic()
        if fresh or self._elevated is None or now - self._elevated[0] > 5.0:
            info = self.tasks.query(WINDOWS_TASK)
            if info is None:
                state = "none"
            elif not same_program(info.command, self._program or launch_command()[0]):
                state = "other"
            else:
                state = "ours" if info.enabled else "off"
            self._elevated = (now, state)
        return self._elevated[1]

    def elevated(self, fresh: bool = False) -> bool:
        """Whether Libre Panel starts through its elevated task (Windows): one that is on and
        starts this program. A task of the same name that is off or starts another program
        does not count: it neither shows the switch on nor keeps the normal start away."""
        return self.task_state(fresh) == "ours"

    def task_hint(self) -> str:
        """What to do about a task named like Libre Panel's that is off or not its own."""
        state = self.task_state()
        if state not in ("off", "other"):
            return ""
        return t(
            'A task "{name}" in Task Scheduler is switched off or starts another program; '
            "Libre Panel uses its normal start at login instead. To remove the task, run as "
            'administrator: schtasks /Delete /TN "{name}" /F',
            name=WINDOWS_TASK,
        )

    def can_elevate(self) -> bool:
        """Whether this process may set up the start with the highest rights: it runs as
        administrator on Windows. Anything else has to be done in a terminal opened so."""
        return self.kind == "windows" and bool(self._admin())

    def path(self) -> Path | None:
        """The desktop entry's file (XDG); None where there is no such file."""
        if self.kind != "xdg":
            return None
        base = self.environ.get("XDG_CONFIG_HOME") or str(self.home / ".config")
        return Path(base) / "autostart" / DESKTOP_FILE

    def location(self) -> str:
        """Where the entry lives, or (on an unsupported system) that there is none."""
        if self.kind == "windows":
            if self.elevated():
                return t("Task Scheduler: {name}, with the highest rights", name=WINDOWS_TASK)
            return f"HKEY_CURRENT_USER\\{WINDOWS_RUN_KEY}\\{APP_NAME}"
        if self.kind == "unsupported":
            return str(_not_supported())
        return str(self.path())

    def is_enabled(self) -> bool:
        if self.kind == "windows":
            return self.registry.get(APP_NAME) is not None or self.elevated()
        if self.kind == "unsupported":
            raise _not_supported()
        path = self.path()
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        return not re.search(r"^Hidden\s*=\s*true\s*$", text, re.MULTILINE | re.IGNORECASE)

    def enable(self, command: list[str], elevated: bool = False) -> str:
        """Start ``command`` at login; ``elevated`` (Windows): with the highest rights."""
        if self.kind == "unsupported":
            raise _not_supported()
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
        if self.kind == "unsupported":
            raise _not_supported()
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

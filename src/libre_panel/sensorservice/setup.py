"""``libre-panel system-setup``: what Windows has to give the sensors, set up by an administrator.

One code path for the Windows setup (its tasks run this command) and for the portable download
("Unlock all sensors" runs it with a UAC prompt):

- ``--service``: the sensor service's files go to ``%ProgramFiles%\\Libre Panel\\sensors``, a
  folder only administrators may change (where the setup installed Libre Panel under Program
  Files they are there already), and the service LibrePanelSensors is created or brought up to
  date, then started: LocalSystem, started by Windows a while after it starts, started again
  when it fails, and startable (not stoppable) by everyone signed in.
- ``--pawnio``: the PawnIO driver, which LibreHardwareMonitor reads the CPU and the mainboard
  through, from the official installer the download carries, unless version 2 or newer is there.
- ``--perf-log-users``: the person who started it (``--user-sid``, else the one signed in to this
  session) joins Performance Log Users (S-1-5-32-559), which game frame rates (PresentMon)
  need; it counts from their next sign-in.

``--remove`` takes away again what these did: the service (and its files, where the setup does
not own them) and the group membership it gave. PawnIO stays: other programs use it too, and it
has its own entry under Settings → Apps. Everything may run again: what is in place stays.
``--status`` says what is in place, and needs no rights.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from libre_panel.sensorservice import (
    DESCRIPTION,
    DISPLAY_NAME,
    FOLDER,
    SERVICE_EXE,
    SERVICE_NAME,
)

log = logging.getLogger(__name__)

PERF_LOG_USERS = "S-1-5-32-559"  # the group "Performance Log Users", in any language
PAWNIO_ARGUMENTS = ("-install", "-silent")
RESTART_NEEDED = 3010  # ERROR_SUCCESS_REBOOT_REQUIRED: installed, Windows wants a restart
EXIT_FAILED, EXIT_NOT_ADMIN = 1, 3
RESTARTS = "restart/5000/restart/10000/restart/60000"  # after a failure: 5 s, 10 s, a minute
STATE_SECONDS = 30.0  # how long a start or a stop of the service may take
SC_EXISTS, SC_RUNNING, SC_NOT_STARTED, SC_MISSING = 1073, 1056, 1062, 1060
SETUP_KEY = r"SOFTWARE\Libre Panel"  # HKLM: what system-setup gave away, to take it back
ADDED_VALUE = "PerfLogUsersAdded"
_SID = re.compile(r"S-1-\d+(?:-\d+){1,14}")
# The service's own security: as Windows gives it, and everyone signed in may also start it
# (RP), not stop it, so that Libre Panel can start a service whose delayed start has not come
# yet. Only the DACL; administrators keep every right.
SERVICE_SDDL = (
    "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"
    "(A;;CCLCSWRPLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)"
)
# Who may change the service's folder: the system and administrators; users may read it and
# run what is in it. Set on the folder itself, not inherited (whatever is above it).
FOLDER_GRANTS = ("*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX")


class SetupError(Exception):
    """A step that did not work; the text says what to do."""


@dataclass
class ServiceInfo:
    state: str  # "stopped", "starting", "running", "stopping", ...
    pid: int = 0
    program: str = ""  # the command line Windows starts it with


class System(Protocol):
    """What system-setup does to Windows (``WindowsSystem``); tests give a fake."""

    def program_files(self) -> Path: ...
    def tool(self, name: str) -> str: ...
    def service(self) -> ServiceInfo | None: ...
    def run(self, args: list[str], timeout: float = 120.0) -> int: ...
    def pawnio_version(self) -> str | None: ...
    def group_add(self, group: str, member: str) -> bool: ...
    def group_remove(self, group: str, member: str) -> bool: ...
    def group_members(self, group: str) -> set[str]: ...
    def session_user(self) -> str | None: ...
    def added(self) -> list[str]: ...
    def set_added(self, sids: list[str]) -> None: ...
    def alive(self, pid: int) -> bool: ...
    def sleep(self, seconds: float) -> None: ...


# -- what came with this program -------------------------------------------------------------------


def program_folder() -> Path | None:
    """The folder of the Windows download's programs; None for a pip install."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def bundled_service() -> Path | None:
    """The folder with the sensor service's files that came with this program."""
    folder = program_folder()
    if folder is None or not (folder / FOLDER / SERVICE_EXE).is_file():
        return None
    return folder / FOLDER


def pawnio_installer(folders: list[Path] | None = None) -> Path | None:
    """PawnIO's installer as the download carries it, in ``tools``, and only the file that was
    fetched (libre_panel.bundled pins its SHA-256): it runs with administrator rights."""
    from libre_panel.bundled import PAWNIO_SETUP, PAWNIO_SETUP_SHA256, TOOLS_FOLDER

    if folders is None:
        found = []
        program = program_folder()
        if program is not None:
            found.append(program / TOOLS_FOLDER)
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            found.append(Path(bundle) / TOOLS_FOLDER)
        folders = found
    for folder in folders:
        path = folder / PAWNIO_SETUP
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest == PAWNIO_SETUP_SHA256:
            return path
    return None


def unlock_offered(platform: str | None = None) -> bool:
    """Whether "Unlock all sensors" is a thing here: a Windows download that carries the
    sensor service, and the service not installed (or switched off)."""
    from libre_panel.sensors import lhm_service

    if (platform or sys.platform) != "win32" or bundled_service() is None:
        return False
    return not lhm_service.installed()


# -- the commands it runs --------------------------------------------------------------------------


def service_folder(system: System) -> Path:
    return system.program_files() / "Libre Panel" / FOLDER


def sc_create(sc: str, program: Path, verb: str = "create") -> list[str]:
    """``sc.exe create`` (or ``config``) for the service. The program's path is quoted
    inside the value, so Windows never reads a part of it as a program of its own
    (``C:\\Program.exe``); subprocess quotes the value as a whole once more."""
    return [
        sc, verb, SERVICE_NAME,
        "binPath=", f'"{program}"',
        "start=", "delayed-auto",
        "obj=", "LocalSystem",
        "DisplayName=", DISPLAY_NAME,
    ]  # fmt: skip


def sc_settings(sc: str) -> list[list[str]]:
    """What follows the creation: the description, a restart after 5 s, 10 s and a minute
    when it fails (also when it ends with an error), and who may start it."""
    return [
        [sc, "description", SERVICE_NAME, DESCRIPTION],
        [sc, "failure", SERVICE_NAME, "reset=", "86400", "actions=", RESTARTS],
        [sc, "failureflag", SERVICE_NAME, "1"],
        [sc, "sdset", SERVICE_NAME, SERVICE_SDDL],
    ]  # fmt: skip


def icacls_protect(icacls: str, folder: Path) -> list[str]:
    return [icacls, str(folder), "/inheritance:r", "/grant:r", *FOLDER_GRANTS, "/Q"]


# -- the steps -------------------------------------------------------------------------------------


@dataclass
class Outcome:
    lines: list[str] = field(default_factory=list)
    failed: bool = False
    pawnio_installed: bool = False  # newly: a running service has to start again to see it

    def say(self, text: str) -> None:
        self.lines.append(text)
        log.info("%s", text)

    def fail(self, text: str) -> None:
        self.lines.append(f"error: {text}")
        log.error("%s", text)
        self.failed = True


def _same(one: Path, other: Path) -> bool:
    try:
        return os.path.normcase(str(one.resolve())) == os.path.normcase(str(other.resolve()))
    except OSError:
        return False


def _wait_for(system: System, wanted: str | None, seconds: float = STATE_SECONDS) -> bool:
    """Wait until the service is in the state ``wanted`` (None: gone)."""
    waited = 0.0
    while True:
        info = system.service()
        state = None if info is None else info.state
        if state == wanted:
            return True
        if waited >= seconds:
            return False
        system.sleep(0.5)
        waited += 0.5


def _replace_folder(source: Path, target: Path) -> None:
    """``target`` becomes a copy of ``source``, whole: copied beside it first, then swapped."""
    target.parent.mkdir(parents=True, exist_ok=True)
    new = target.with_name(target.name + ".new")
    old = target.with_name(target.name + ".old")
    for leftover in (new, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(source, new)
    if target.exists():
        target.rename(old)
    new.rename(target)
    shutil.rmtree(old, ignore_errors=True)


def stop_service(system: System, out: Outcome) -> None:
    """Stop the service, and wait until its process has let go of its files."""
    info = system.service()
    if info is None or info.state == "stopped":
        return
    code = system.run([system.tool("sc.exe"), "stop", SERVICE_NAME])
    if code not in (0, SC_NOT_STARTED) and system.service() is not None:
        raise SetupError(f"the sensor service did not stop (sc.exe: {code})")
    if not _wait_for(system, "stopped"):
        raise SetupError("the sensor service did not stop in time")
    waited = 0.0
    while info.pid and system.alive(info.pid) and waited < STATE_SECONDS:
        system.sleep(0.5)
        waited += 0.5
    out.say("stopped the sensor service")


def install_service(system: System, out: Outcome, source: Path | None) -> None:
    """The service's files in Program Files, the service created or brought up to date, and
    started (again, after PawnIO came)."""
    target = service_folder(system)
    program = target / SERVICE_EXE
    if source is None and not program.is_file():
        raise SetupError(
            "the sensor service is not part of this program: it comes with the Windows "
            "downloads (setup or zip)"
        )
    info = system.service()
    if source is not None and not _same(source, target):
        stop_service(system, out)
        try:
            _replace_folder(source, target)
        except OSError as exc:
            raise SetupError(f"could not copy the sensor service to {target}: {exc}") from exc
        out.say(f"copied the sensor service to {target}")
    if system.run(icacls_protect(system.tool("icacls.exe"), target)):
        raise SetupError(f"could not limit who may change {target}")
    sc = system.tool("sc.exe")
    code = system.run(sc_create(sc, program, "create" if info is None else "config"))
    if code == SC_EXISTS:  # it came meanwhile
        code = system.run(sc_create(sc, program, "config"))
    if code:
        raise SetupError(f"could not register the sensor service (sc.exe: {code})")
    for args in sc_settings(sc):
        if system.run(args):
            out.say(f"warning: sc.exe {args[1]} did not work")
    out.say(("registered" if info is None else "updated") + " the sensor service")
    info = system.service()
    if out.pawnio_installed and info is not None and info.state == "running":
        stop_service(system, out)  # LibreHardwareMonitor looks for PawnIO when it starts
    code = system.run([sc, "start", SERVICE_NAME])
    if code not in (0, SC_RUNNING):
        raise SetupError(f"the sensor service did not start (sc.exe: {code})")
    if not _wait_for(system, "running"):
        raise SetupError(f"the sensor service did not start in time; its log: {target / 'logs'}")
    out.say("the sensor service runs")


def remove_service(system: System, out: Outcome) -> None:
    """The service stopped and deleted; its files removed where the setup does not own
    them (the setup's uninstaller removes those itself)."""
    if system.service() is not None:
        stop_service(system, out)
        code = system.run([system.tool("sc.exe"), "delete", SERVICE_NAME])
        if code not in (0, SC_MISSING):
            raise SetupError(f"could not remove the sensor service (sc.exe: {code})")
        out.say("removed the sensor service")
    target = service_folder(system)
    if target.exists() and not (target.parent / "unins000.exe").exists():
        for _ in range(10):  # its process may take a moment to let go of its files
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                break
            system.sleep(0.5)
        if target.exists():
            raise SetupError(f"could not remove {target}")
        out.say(f"removed {target}")
        try:
            target.parent.rmdir()  # "Libre Panel" under Program Files, if nothing else is there
        except OSError:
            pass


def _old(version: str) -> bool:
    found = re.match(r"\d+", version)
    return found is not None and int(found.group()) < 2


def install_pawnio(system: System, out: Outcome, installer: Path | None) -> None:
    version = system.pawnio_version()
    if version is not None and not _old(version):
        out.say(f"PawnIO {version} is installed" if version else "PawnIO is installed")
        return
    if installer is None:
        raise SetupError(
            "PawnIO's installer is not part of this download: install PawnIO from https://pawnio.eu"
        )
    code = system.run([str(installer), *PAWNIO_ARGUMENTS], timeout=600)
    if code not in (0, RESTART_NEEDED):
        raise SetupError(f"PawnIO's installer ended with {code}")
    out.pawnio_installed = True
    out.say("installed PawnIO" + (": Windows needs a restart to finish" if code else ""))


def join_group(system: System, out: Outcome, user: str | None) -> None:
    sid = user or system.session_user()
    if not sid:
        raise SetupError("could not tell who is signed in: give --user-sid")
    if system.group_add(PERF_LOG_USERS, sid):
        system.set_added([*system.added(), sid])
        out.say("joined Performance Log Users: game frame rates count from the next sign-in")
    else:
        out.say("already in Performance Log Users")


def leave_group(system: System, out: Outcome) -> None:
    """Only the memberships this setup gave: whoever was in the group before stays."""
    added = system.added()
    for sid in added:
        if system.group_remove(PERF_LOG_USERS, sid):
            out.say("left Performance Log Users")
    if added:
        system.set_added([])


def apply(
    system: System,
    *,
    service: bool = False,
    pawnio: bool = False,
    perf_log_users: bool = False,
    remove: bool = False,
    user: str | None = None,
    source: Path | None = None,
    installer: Path | None = None,
) -> Outcome:
    """Each step asked for, in an order that works (PawnIO before the service that uses it);
    one that fails does not keep the others from being tried."""
    out = Outcome()
    steps: list[tuple[str, Callable[[], None]]] = []
    if remove:
        if service:
            steps.append(("the sensor service", lambda: remove_service(system, out)))
        if perf_log_users:
            steps.append(("Performance Log Users", lambda: leave_group(system, out)))
        if pawnio:
            out.say("PawnIO stays: other programs may use it; remove it under Settings → Apps")
    else:
        if pawnio:
            steps.append(("PawnIO", lambda: install_pawnio(system, out, installer)))
        if service:
            steps.append(("the sensor service", lambda: install_service(system, out, source)))
        if perf_log_users:
            steps.append(("Performance Log Users", lambda: join_group(system, out, user)))
    for name, step in steps:
        try:
            step()
        except (SetupError, OSError, subprocess.SubprocessError) as exc:
            out.fail(f"{name}: {exc}")
    return out


# -- what is in place (no rights needed) -----------------------------------------------------------


def status(system: System, user: str | None = None) -> dict[str, Any]:
    info = system.service()
    sid = user or system.session_user()
    try:
        member = sid in system.group_members(PERF_LOG_USERS) if sid else None
    except OSError:
        member = None
    return {
        "service": None
        if info is None
        else {"state": info.state, "pid": info.pid, "program": info.program},
        "pawnio": system.pawnio_version(),
        "perf_log_users": member,
        "service_files": str(bundled_service() or ""),
        "pawnio_installer": str(pawnio_installer() or ""),
    }


# -- Windows ---------------------------------------------------------------------------------------


class WindowsSystem:
    """The real one."""

    def __init__(self) -> None:
        from libre_panel.sensorservice import winapi

        self.api = winapi

    def program_files(self) -> Path:
        """Program Files as Windows has it on record (HKLM, which only administrators
        change), not from the environment."""
        import winreg

        key = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
        view = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, view) as handle:
            return Path(winreg.QueryValueEx(handle, "ProgramFilesDir")[0])

    def tool(self, name: str) -> str:
        return str(Path(self.api.system_folder()) / name)

    def service(self) -> ServiceInfo | None:
        from libre_panel.sensors import lhm_service

        found = self.api.service_status(SERVICE_NAME)
        if found is None:
            return None
        state, pid = found
        program = (lhm_service.registration() or {}).get("ImagePath") or ""
        return ServiceInfo(self.api.STATE_NAMES.get(state, str(state)), pid, str(program))

    def run(self, args: list[str], timeout: float = 120.0) -> int:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        said = (result.stdout + result.stderr).strip()
        log.debug("%s: %s %s", Path(args[0]).name, result.returncode, said[-500:])
        return result.returncode

    def pawnio_version(self) -> str | None:
        from libre_panel.sensors.lhm_inprocess import pawnio_version

        return pawnio_version()

    def group_add(self, group: str, member: str) -> bool:
        return self.api.group_add(group, member)

    def group_remove(self, group: str, member: str) -> bool:
        return self.api.group_remove(group, member)

    def group_members(self, group: str) -> set[str]:
        return self.api.group_members(group)

    def session_user(self) -> str | None:
        try:
            return self.api.session_user_sid() or self.api.token_user_sid()
        except OSError:
            return None

    def added(self) -> list[str]:
        import winreg

        try:
            view = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SETUP_KEY, 0, view) as key:
                value = winreg.QueryValueEx(key, ADDED_VALUE)[0]
        except OSError:
            return []
        return [sid for sid in value if _SID.fullmatch(sid)] if isinstance(value, list) else []

    def set_added(self, sids: list[str]) -> None:
        import winreg

        view = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, SETUP_KEY, 0, view) as key:
            if sids:
                winreg.SetValueEx(
                    key, ADDED_VALUE, 0, winreg.REG_MULTI_SZ, list(dict.fromkeys(sids))
                )
            else:
                try:
                    winreg.DeleteValue(key, ADDED_VALUE)
                except OSError:
                    pass
        if not sids:
            try:
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, SETUP_KEY, winreg.KEY_WOW64_64KEY, 0)
            except OSError:
                pass  # something else is in it

    def alive(self, pid: int) -> bool:
        import psutil

        return psutil.pid_exists(pid)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# -- the command -----------------------------------------------------------------------------------


def run_command(
    args: Any,
    system: System | None = None,
    admin: Callable[[], bool] | None = None,
    platform: str | None = None,
) -> int:
    """``libre-panel system-setup``; prints what it did, returns the exit code."""
    if (platform or sys.platform) != "win32":
        print(
            "system-setup is for Windows: elsewhere the sensors need no service.", file=sys.stderr
        )
        return EXIT_FAILED
    user = args.user_sid
    if user is not None and not _SID.fullmatch(user):
        print(f"error: {user} is not a SID (S-1-5-21-...)", file=sys.stderr)
        return EXIT_FAILED
    if system is None:
        system = WindowsSystem()
    if args.status:
        print(json.dumps(status(system, user), indent=2))
        return 0
    if not (args.service or args.pawnio or args.perf_log_users):
        if not args.remove:
            print(
                "error: say what to set up: --service, --pawnio, --perf-log-users",
                file=sys.stderr,
            )
            return EXIT_FAILED
        args.service = True  # --remove alone: the service
    if admin is None:
        from libre_panel.autostart import is_admin

        admin = is_admin
    if not admin():
        print(
            "error: this needs administrator rights: run it in a terminal opened with "
            "'Run as administrator' (the app asks Windows for them by itself)",
            file=sys.stderr,
        )
        return EXIT_NOT_ADMIN
    out = apply(
        system,
        service=args.service,
        pawnio=args.pawnio,
        perf_log_users=args.perf_log_users,
        remove=args.remove,
        user=user,
        source=bundled_service(),
        installer=pawnio_installer(),
    )
    for line in out.lines:
        print(line, file=sys.stderr if line.startswith("error:") else sys.stdout)
    return EXIT_FAILED if out.failed else 0


# -- "Unlock all sensors" in the app ------------------------------------------------------------

UNLOCK = ("--service", "--pawnio", "--perf-log-users")
ACTIONS = {"unlock": UNLOCK, "remove": ("--remove", "--service", "--perf-log-users")}


@dataclass
class Job:
    action: str
    running: bool = True
    code: int | None = None  # the exit code; None while it runs, or when it did not start
    cancelled: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "running": self.running,
            "code": self.code,
            "cancelled": self.cancelled,
            "error": self.error,
        }


class Elevator:
    """Runs ``libre-panel system-setup`` with a UAC prompt, one at a time, in a thread, and
    keeps how the last one ended. ``launch(program, parameters)`` is ``winapi.run_elevated``
    (tests give their own)."""

    def __init__(self, launch: Callable[[str, str], int] | None = None) -> None:
        self._launch = launch
        self._lock = threading.Lock()
        self.job: Job | None = None

    def start(self, action: str, program: Path, user: str | None) -> Job:
        if action not in ACTIONS:
            raise ValueError(f"action is one of {', '.join(ACTIONS)}")
        with self._lock:
            if self.job is not None and self.job.running:
                return self.job
            arguments = ["system-setup", *ACTIONS[action]]
            if user:
                arguments += ["--user-sid", user]
            self.job = job = Job(action)
        thread = threading.Thread(
            target=self._run, args=(job, str(program), subprocess.list2cmdline(arguments)),
            name="system-setup", daemon=True,
        )  # fmt: skip
        thread.start()
        return job

    def _run(self, job: Job, program: str, parameters: str) -> None:
        from libre_panel.sensorservice.winapi import Cancelled

        try:
            job.code = (self._launch or _run_elevated)(program, parameters)
        except Cancelled:
            job.cancelled = True
        except Exception as exc:  # noqa: BLE001 - said in the app
            job.error = str(exc) or type(exc).__name__
            log.warning("system-setup could not be started: %s", exc)
        finally:
            job.running = False


def _run_elevated(program: str, parameters: str) -> int:
    """With a limit: PawnIO's installer and the service's start take seconds, not minutes."""
    from libre_panel.sensorservice import winapi

    return winapi.run_elevated(program, parameters, timeout=900)


ELEVATOR = Elevator()


def current_user() -> str | None:
    """The SID of the account this program runs as (who unlocks); None off Windows."""
    if sys.platform != "win32":
        return None
    try:
        from libre_panel.sensorservice import winapi

        return winapi.token_user_sid()
    except OSError:
        return None  # system-setup asks Windows who is signed in


def cli_program() -> Path | None:
    """The download's command-line program, which the UAC prompt starts."""
    folder = program_folder()
    if folder is None or not (folder / "libre-panel.exe").is_file():
        return None
    return folder / "libre-panel.exe"


def service_state() -> dict[str, Any] | None:
    """What the app shows of the service: None where it is not installed."""
    from libre_panel.sensors import lhm_service

    found = lhm_service.registration()
    if found is None:
        return None
    try:
        info = WindowsSystem().service()
    except OSError:
        info = None
    return {
        "state": info.state if info is not None else "unknown",
        "disabled": found.get("Start") == lhm_service.DISABLED,
    }


def frame_rate_rights() -> str | None:
    """Whether this account may record game frame rates: "yes" (in Performance Log Users,
    or an administrator), "sign-in" (in the group from the next sign-in), "no"; None where
    it cannot be told."""
    try:
        from libre_panel.sensorservice import winapi

        if winapi.token_has(PERF_LOG_USERS):
            return "yes"
        return (
            "sign-in" if winapi.token_user_sid() in winapi.group_members(PERF_LOG_USERS) else "no"
        )
    except (OSError, AttributeError):
        return None


def unlock_state(platform: str | None = None) -> dict[str, Any]:
    """What "Unlock all sensors" would set up, what is in place, and the last run of it."""
    windows = (platform or sys.platform) == "win32"
    pawnio = None
    if windows:
        from libre_panel.sensors.lhm_inprocess import pawnio_version

        pawnio = pawnio_version()
    job = ELEVATOR.job
    return {
        "platform": platform or sys.platform,
        "available": windows and bundled_service() is not None and cli_program() is not None,
        "service": service_state() if windows else None,
        "pawnio": pawnio,
        "frame_rates": frame_rate_rights() if windows else None,
        "job": job.as_dict() if job is not None else None,
    }

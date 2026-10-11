"""Asking for the rights the setup needs: pkexec (polkit's own password dialog), or sudo.

``linux_setup`` says what is to be done; this runs it as root. The app, the AppImage and a
terminal all start the same command, ``<program> system-setup --linux``, through ``pkexec``
(which wants the program by its absolute path, and runs it in an environment of its own) or
``sudo``. What the person sees in the dialog is that path, so it is the AppImage file or the
Python of the installation, nothing else.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libre_panel.linux_setup import OWN_FILES, RAPL_RULES, Facts, Options, offers

log = logging.getLogger(__name__)


def arguments(options: Options, json_output: bool = False, dry_run: bool = False) -> list[str]:
    """The command line of the setup for these options."""
    found = ["system-setup", "--linux"]
    if options.rapl is True:
        found.append("--rapl")
    elif options.rapl is False:
        found.append("--no-rapl")
    if options.remove:
        found.append("--remove")
    if json_output:
        found.append("--json")
    if dry_run:
        found.append("--dry-run")
    return found


_isolated_import: dict[str, bool] = {}


def imports_isolated(python: str) -> bool:
    """Whether ``python -I -m libre_panel`` finds Libre Panel: root runs it that way (isolated
    from the person's own folders), so a copy that lives in the person's site-packages or is
    found through PYTHONPATH is out of its reach."""
    if python not in _isolated_import:
        try:
            done = subprocess.run(
                [python, "-B", "-I", "-c", "import libre_panel"],
                capture_output=True,
                timeout=15,
                check=False,
            )
            _isolated_import[python] = done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _isolated_import[python] = False
    return _isolated_import[python]


def program(
    environ: Mapping[str, str] | None = None,
    frozen: bool | None = None,
    executable: str | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
    importable: Callable[[str], bool] | None = None,
) -> list[str] | None:
    """The command that starts this Libre Panel, as root can run it: the AppImage file (its own
    mount is closed to every other user, root included), the program of a download, or this Python
    with Libre Panel's package (isolated, and writing no bytecode into a folder that is the
    person's). None where there is no such command (an AppImage that has lost its file, a copy
    only the person's own Python finds)."""
    environ = os.environ if environ is None else environ
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    if frozen:
        appimage = environ.get("APPIMAGE")
        if appimage:
            return [appimage] if os.path.isabs(appimage) and exists(appimage) else None
        return [executable] if os.path.isabs(executable) else None
    if not os.path.isabs(executable) or not (importable or imports_isolated)(executable):
        return None
    return [executable, "-B", "-I", "-m", "libre_panel"]


def terminal_command(options: Options, command: list[str] | None = None) -> str:
    """What a person types in a terminal for the same: ``sudo`` and the command. A pip install
    has a ``libre-panel`` beside its Python, which reads better than ``python -m``."""
    if command is None:
        return shlex.join(["sudo", "libre-panel", *arguments(options)])
    if "-m" in command:
        script = shutil.which("libre-panel")
        if script and Path(script).parent == Path(command[0]).parent:
            command = [script]
    return shlex.join(["sudo", *command, *arguments(options)])


@dataclass(frozen=True)
class Elevation:
    """How the setup is started with the rights it needs. ``argv`` is empty where there is no
    way to ask (the person then gets ``terminal``, which works anywhere)."""

    method: str  # "pkexec", "sudo" or ""
    argv: tuple[str, ...]
    terminal: str


def elevation(
    options: Options,
    command: list[str] | None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    terminal: bool = False,
    json_output: bool = True,
) -> Elevation:
    """How ``command`` (``program()``) is run with the rights: pkexec where there is a desktop
    to show polkit's dialog, in a ``terminal`` also sudo. The program is given by its absolute
    path, which pkexec insists on."""
    environ = os.environ if environ is None else environ
    which = which or shutil.which
    text = terminal_command(options, command)
    if command is None:
        return Elevation("", (), text)
    tail = [*command, *arguments(options, json_output=json_output)]
    graphical = bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    pkexec = which("pkexec")
    if pkexec and graphical:
        return Elevation("pkexec", (pkexec, "--disable-internal-agent", *tail), text)
    sudo = which("sudo")
    if terminal and sudo:
        return Elevation("sudo", (sudo, *tail), text)
    if terminal and pkexec:  # no desktop: pkexec asks in the terminal
        return Elevation("pkexec", (pkexec, *tail), text)
    return Elevation("", (), text)


_STRIPPED = ("LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "_MEIPASS2")
_APP_MARKS = ("_PYI_", "PYI_", "APPDIR", "APPIMAGE", "ARGV0", "OWD")


def clean_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment pkexec is started in: what a program from the system needs and none of
    what the bundle (PyInstaller, the AppImage) put there for its own libraries. The libraries
    of the bundle are the first the loader would take; the original path comes back."""
    env = {
        key: value
        for key, value in environ.items()
        if key not in _STRIPPED and not key.startswith(_APP_MARKS) and key != "LD_LIBRARY_PATH"
    }
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    return env


def run_elevated(argv: Sequence[str], timeout: float = 900.0) -> tuple[int, str, str]:
    """Run the command (pkexec or sudo with the setup); ``(exit code, stdout, stderr)``. The
    dialog may stay open as long as the person likes, up to ``timeout``."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            env=clean_environment(os.environ),
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 126, "", "nobody answered the dialog"
    return done.returncode, done.stdout, done.stderr


def parse_result(stdout: str) -> dict[str, Any] | None:
    """The report the setup prints with ``--json``: the last line that is one."""
    for line in reversed(stdout.strip().splitlines()):
        if line.startswith("{"):
            try:
                found = json.loads(line)
            except ValueError:
                continue
            return found if isinstance(found, dict) else None
    return None


class Job:
    """One run of the setup started by the app. pkexec waits for the person at its dialog, so
    the run is a thread; the app asks how it is going. ``state``: idle, running, done,
    cancelled (the dialog was closed), unavailable (no polkit agent, no pkexec) or failed."""

    def __init__(self, runner: Callable[[Sequence[str]], tuple[int, str, str]] = run_elevated):
        self._runner = runner
        self._lock = threading.Lock()
        self.state = "idle"
        self.result: dict[str, Any] | None = None
        self.detail = ""
        self.method = ""

    def start(self, elevation: Elevation, after: Callable[[], None] | None = None) -> bool:
        """Begin; False if a run is going on already or there is no way to ask for rights."""
        with self._lock:
            if self.state == "running" or not elevation.argv:
                return False
            self.state, self.result, self.detail = "running", None, ""
            self.method = elevation.method
        threading.Thread(
            target=self._run, args=(elevation, after), name="linux-setup", daemon=True
        ).start()
        return True

    def _run(self, elevation: Elevation, after: Callable[[], None] | None) -> None:
        try:
            code, out, err = self._runner(elevation.argv)
        except Exception as exc:  # noqa: BLE001 - a run that cannot start is a failed run
            code, out, err = 1, "", f"{type(exc).__name__}: {exc}"
        report = parse_result(out)
        with self._lock:
            self.result = report
            if report is not None:
                self.state = "done" if report.get("ok") else "failed"
            elif code == 126:
                self.state = "cancelled"
            elif code == 127:
                self.state = "unavailable"
            else:
                self.state = "failed"
            self.detail = err.strip()[-2000:] if self.state in ("failed", "unavailable") else ""
        log.info("linux setup: %s (exit %s)", self.state, code)
        if after is not None and self.state == "done":
            try:
                after()
            except Exception:  # noqa: BLE001 - the report stands
                log.debug("linux setup: following up failed", exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "result": self.result,
                "detail": self.detail,
                "method": self.method,
            }


JOB = Job()


def status(facts: Facts, job: Job | None = None) -> dict[str, Any]:
    """What the app shows about the setup: what it would bring this computer, what it has put
    in, how it is started, and how a run is going."""
    way = elevation(Options(), program())
    return {
        "supported": True,
        "offers": [dataclasses.asdict(offer) for offer in offers(facts)],
        "installed": [path for path in OWN_FILES if facts.files.get(path) is not None],
        "granted": facts.files.get(RAPL_RULES) is not None,
        "method": way.method,  # pkexec, or "": the terminal command is all there is
        "command": way.terminal,
        "removal": terminal_command(Options(remove=True), program()),
        "job": (job or JOB).snapshot(),
    }

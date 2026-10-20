"""Linux: the panel sleeps with the computer and dims while the session is locked, as
winsession.py does on Windows.

systemd-logind says on the system bus when the computer is about to sleep and when it woke
(``PrepareForSleep``), and when a session is locked or unlocked (``Lock``/``Unlock``, and its
``LockedHint`` as the desktop sets it). ``gdbus monitor`` (GLib) or ``dbus-monitor`` prints
these signals, one a line; no D-Bus library is needed. A delay lock (``systemd-inhibit
--mode=delay``) gives the panel the moment it needs to go dark before the computer sleeps:
it is let go once the panel is dark, and taken again after the computer woke.

Without logind, or without either program, nothing of this happens, and the panel stays as
it is, as before.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable

from libre_panel import presence

log = logging.getLogger(__name__)

LOGIN1 = "org.freedesktop.login1"
SESSIONS = "/org/freedesktop/login1/session/"
# gdbus monitor: one line a signal, its arguments on it
_SLEEP = re.compile(r"PrepareForSleep \((true|false),\)")
_LOCK = re.compile(r"^(/org/freedesktop/login1/session/[^:\s]+): \S+Session\.(Lock|Unlock) ")
_HINT = re.compile(r"^(/org/freedesktop/login1/session/[^:\s]+): .*'LockedHint': <(true|false)>")
# dbus-monitor: a line for the signal, a line for each argument below it
_SIGNAL = re.compile(r"^signal .*path=([^;\s]+); interface=([^;\s]+); member=(\w+)")
_BOOLEAN = re.compile(r"^\s*(?:variant\s+)?boolean (true|false)")


def session_path(session_id: str | None = None) -> str | None:
    """The object path of this session on logind's bus (``XDG_SESSION_ID``; "2" is
    ``…/session/_32``, as systemd escapes it); None where it is not known."""
    session_id = session_id if session_id is not None else os.environ.get("XDG_SESSION_ID", "")
    if not session_id:
        return None
    escaped = "".join(
        char
        if char.isascii() and (char.isalpha() or (char.isdigit() and i))
        else f"_{ord(char):02x}"
        for i, char in enumerate(session_id)
    )
    return f"/org/freedesktop/login1/session/{escaped}"


def monitor_command() -> list[str] | None:
    """The program that prints logind's signals, one a line; None without one."""
    if shutil.which("gdbus"):
        return ["gdbus", "monitor", "--system", "--dest", LOGIN1]
    if shutil.which("dbus-monitor"):
        return ["dbus-monitor", "--system", f"type='signal',sender='{LOGIN1}'"]
    return None


class LogindWatcher:
    """Hears logind's signals in a thread of its own: ``on_sleep`` before the computer sleeps
    (the delay lock is let go after it), ``on_wake`` after it woke; locking and unlocking of
    this session go to presence.py. The callbacks may block for a moment."""

    def __init__(
        self,
        on_sleep: Callable[[], None] | None = None,
        on_wake: Callable[[], None] | None = None,
        session: str | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.on_sleep, self.on_wake = on_sleep, on_wake
        self.session = session if session is not None else session_path()
        self.command = command if command is not None else monitor_command()
        self._monitor: subprocess.Popen | None = None
        self._inhibitor: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="logind", daemon=True)
        # dbus-monitor: the signal whose arguments follow ("sleep", or the path of a session
        # whose properties changed), and whether the next truth is its LockedHint
        self._pending: str | None = None
        self._hint_next = False

    def start(self) -> LogindWatcher:
        if self.command is None:
            raise OSError("neither gdbus nor dbus-monitor is installed")
        self._monitor = subprocess.Popen(
            self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            bufsize=1,
        )  # fmt: skip
        self._inhibit()
        self._thread.start()
        return self

    def stop(self) -> None:
        for process in (self._monitor, self._inhibitor):
            if process is not None and process.poll() is None:
                process.terminate()
        self._monitor = self._inhibitor = None

    def _inhibit(self) -> None:
        """Take the delay lock: the computer waits (up to InhibitDelayMaxSec, 5 s) for the
        panel before it sleeps."""
        if self.on_sleep is None or not shutil.which("systemd-inhibit"):
            return
        with self._lock:
            if self._inhibitor is not None and self._inhibitor.poll() is None:
                return
            try:
                self._inhibitor = subprocess.Popen(
                    ["systemd-inhibit", "--what=sleep", "--mode=delay", "--who=Libre Panel",
                     "--why=The panel goes dark before the computer sleeps", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )  # fmt: skip
            except OSError as exc:
                log.debug("no delay lock before sleep: %s", exc)

    def _let_go(self) -> None:
        with self._lock:
            if self._inhibitor is not None and self._inhibitor.poll() is None:
                self._inhibitor.terminate()
            self._inhibitor = None

    def _run(self) -> None:
        monitor = self._monitor
        if monitor is None or monitor.stdout is None:
            return
        for line in monitor.stdout:
            self.heard(line)

    def heard(self, line: str) -> None:
        """One line of the monitor's (gdbus's, or dbus-monitor's)."""
        found = _SLEEP.search(line)
        if found:
            self._sleep(found[1] == "true")
            return
        found = _LOCK.search(line)
        if found:
            self._locked(found[1], found[2] == "Lock")
            return
        found = _HINT.search(line)
        if found:
            self._locked(found[1], found[2] == "true")
            return
        self._monitor_line(line)

    def _monitor_line(self, line: str) -> None:
        """dbus-monitor's lines: the signal, then its arguments."""
        found = _SIGNAL.search(line)
        if found:
            path, interface, member = found.groups()
            self._pending, self._hint_next = None, False
            if member == "PrepareForSleep":
                self._pending = "sleep"
            elif interface == f"{LOGIN1}.Session" and member in ("Lock", "Unlock"):
                self._locked(path, member == "Lock")
            elif member == "PropertiesChanged" and path.startswith(SESSIONS):
                self._pending = path
            return
        if self._pending is None:
            return
        if '"LockedHint"' in line:
            self._hint_next = True
            return
        truth = _BOOLEAN.search(line)
        if truth is None:
            return
        if self._pending == "sleep":
            self._pending = None
            self._sleep(truth[1] == "true")
        elif self._hint_next:
            path, self._pending, self._hint_next = self._pending, None, False
            self._locked(path, truth[1] == "true")

    def _sleep(self, going: bool) -> None:
        if going:
            self._call(self.on_sleep)
            self._let_go()  # the panel is dark: the computer may sleep
        else:
            self._inhibit()
            self._call(self.on_wake)

    def _locked(self, path: str, locked: bool) -> None:
        if self._ours(path):
            presence.set_away(presence.LOCKED, locked)

    def _ours(self, path: str) -> bool:
        return self.session is None or path == self.session

    @staticmethod
    def _call(callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:  # the computer is going to sleep, or woke: go on in any case
            log.exception("while the computer went to sleep or woke")

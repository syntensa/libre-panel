"""Only one Libre Panel may drive the panel at a time.

Autostart, a second click on the program and `libre-panel run` in a terminal
would otherwise fight over the USB device. The lock is an OS file lock, so it
disappears with the process, even after a crash.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO

from libre_panel.config import config_dir

LOCK_FILENAME = "libre-panel.lock"


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / LOCK_FILENAME
        self._file: IO[bytes] | None = None

    def acquire(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - held until release()
        try:
            _lock(handle)
        except OSError as exc:
            handle.close()
            raise AlreadyRunning(
                "Libre Panel is already running and drives the panel (look for its tray "
                "icon, or stop `libre-panel run`/`start` in the other terminal)"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode())
        handle.flush()
        self._file = handle
        return self

    def release(self) -> None:
        if self._file is not None:
            try:
                _unlock(self._file)
            finally:
                self._file.close()
                self._file = None

    def __enter__(self) -> InstanceLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

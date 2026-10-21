"""Frames per second of the game you play on Linux, from MangoHud's log.

MangoHud (MIT license) is the overlay many Linux games run with (``mangohud %command%`` in
Steam). It writes what it measures to a CSV file while it logs; this source reads the newest
of those files as it grows. MangoHud logs when its logging key is pressed (Shift+F2), or from
the start with this in ``~/.config/MangoHud/MangoHud.conf``::

    output_folder=/home/you/mangologs
    autostart_log=1
    log_interval=100

The folder is the one MangoHud.conf names (``output_folder``), else the home folder; or name
it in ``[sensors.mangohud]``::

    folder = "/home/you/mangologs"

Readings, as PresentMon's on Windows: ``game.fps`` (the last second), ``game.frametime``
(ms), ``game.low`` (the 1% lows of the last 30 s), ``game.frametime_peak`` (the longest frame
time since the last reading) and ``game.app`` (the program, from the file's name). MangoHud
logs a frame every ``log_interval`` milliseconds, so the lows are those of its samples.

The file is read only while a theme or a service shows ``game.*`` readings. Nothing runs
beside the game and nothing touches it: only the file MangoHud writes is read.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider, wanted
from libre_panel.sensors.presentmon import FrameCounter

log = logging.getLogger(__name__)

POLL_SECONDS = 0.25
FRESH_SECONDS = 10.0  # a log not written for this long is of a game that ended
LOOK_SECONDS = 2.0  # how often the folder is looked at for a newer log
IDLE_SECONDS = 60.0  # nobody looked for this long: the thread ends, and starts on demand
MAX_LINE = 4096
# "Cyberpunk2077_2026-10-21_19-04-33.csv"; its summary ("..._summary.csv") is no log
_LOG_NAME = re.compile(r"^(?P<app>.+?)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.csv$")


def config_folder(home: Path | None = None) -> Path:
    """The folder MangoHud.conf names (``output_folder``), else the home folder."""
    home = home or Path.home()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        text = (base / "MangoHud" / "MangoHud.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "output_folder" and value.strip():
            return Path(os.path.expandvars(value.strip())).expanduser()
    return home


def newest_log(folder: Path, now: float | None = None) -> Path | None:
    """The log MangoHud writes now: the newest one written in the last few seconds."""
    now = time.time() if now is None else now
    best: tuple[float, Path] | None = None
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return None
    for entry in entries:
        if not _LOG_NAME.match(entry.name):
            continue
        try:
            changed = entry.stat().st_mtime
        except OSError:
            continue
        if now - changed <= FRESH_SECONDS and (best is None or changed > best[0]):
            best = (changed, Path(entry.path))
    return best[1] if best else None


def app_of(path: Path) -> str:
    found = _LOG_NAME.match(path.name)
    return found.group("app") if found else path.stem


class LogReader:
    """The rows of one log as they are written: the header line that names ``frametime``
    first (MangoHud puts what it knows of the system above it), then a frame per row."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.app = app_of(path)
        self._at = 0  # how far the file is read
        self._rest = b""  # a line not ended yet
        self._column: int | None = None  # where the frame time is in a row

    def frames(self) -> list[float]:
        """The frame times (ms) of the rows written since the last call."""
        try:
            with self.path.open("rb") as file:
                file.seek(self._at)
                data = file.read(1024 * 1024)
        except OSError:
            return []
        self._at += len(data)
        lines = (self._rest + data).split(b"\n")
        self._rest = lines.pop()[-MAX_LINE:]
        found = []
        for raw in lines:
            row = raw.decode("utf-8", "replace").strip().split(",")
            if self._column is None:
                names = [name.strip().lower() for name in row]
                if "frametime" in names:
                    self._column = names.index("frametime")
                continue
            try:
                ms = float(row[self._column])
            except (IndexError, ValueError):
                continue
            found.append(ms)
        return found


class MangoHudProvider(SensorProvider):
    """``game.*`` from MangoHud's log, while a theme or a service shows them."""

    name = "mangohud"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        folder = self.options.get("folder")
        self.folder = Path(str(folder)).expanduser() if folder else None
        self._counter = FrameCounter()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _folder(self) -> Path:
        return self.folder or config_folder()

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="sensors-mangohud", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        reader: LogReader | None = None
        looked = time.monotonic()
        checked = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if wanted("game."):
                looked = now
            elif now - looked > IDLE_SECONDS:
                return
            if now - checked >= LOOK_SECONDS:
                checked = now
                path = newest_log(self._folder())
                if path is None:
                    reader = None
                elif reader is None or reader.path != path:
                    log.info("mangohud: reading the log of %s", app_of(path))
                    reader = LogReader(path)
                    reader.frames()  # what was logged before is not now
            if reader is not None:
                frames = reader.frames()
                with self._lock:
                    for ms in frames:
                        self._counter.add(reader.app, ms, now)
            self._stop.wait(POLL_SECONDS)

    def read(self) -> dict[str, Reading]:
        thread = self._thread
        if thread is None or not thread.is_alive():
            if self._stop.is_set() or not wanted("game."):
                return {}
            self._start()
        with self._lock:
            return self._counter.readings(time.monotonic())

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)

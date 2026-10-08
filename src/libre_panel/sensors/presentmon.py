"""Frames per second of the game you play, through Intel's PresentMon (Windows).

PresentMon (MIT license) is a separate program: download the console
version from https://github.com/GameTechDev/PresentMon/releases and name it
in ``[sensors.presentmon]`` (or put it on the PATH)::

    path = "C:/Tools/PresentMon-2.3.0-x64.exe"

It reads the frames Windows shows, which needs administrator rights or
membership in the "Performance Log Users" group. Libre Panel starts it only
while a theme shows ``game.*`` readings and stops it a minute after.

Readings: ``game.fps`` (the last second), ``game.frametime`` (ms),
``game.low`` (the 1% lows of the last 30 s, in fps), ``game.app`` (the program).
The game is the program with the most frames; the desktop compositor and
known overlays do not count.
"""

from __future__ import annotations

import csv
import logging
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

WINDOW = 1.0  # seconds the frame rate is taken over
LOWS_WINDOW = 30.0  # seconds the 1% lows are taken over
IGNORED = {
    "dwm.exe", "explorer.exe", "<unknown>", "steamwebhelper.exe", "discord.exe",
    "nvidia overlay.exe", "gamebar.exe", "librepanel.exe", "libre-panel.exe",
}  # fmt: skip
# the time from one frame to the next, as PresentMon 2 (and 1) names it
_FRAME_COLUMNS = ("MsBetweenPresents", "msBetweenPresents", "FrameTime", "MsBetweenAppStart")


def find_presentmon(path: str = "") -> str | None:
    if path:
        return path if Path(path).is_file() else shutil.which(path)
    for name in ("PresentMon", "PresentMon-x64", "presentmon"):
        found = shutil.which(name)
        if found:
            return found
    for folder in (Path("C:/Program Files/Intel/PresentMon"), Path.home() / "Downloads"):
        if folder.is_dir():
            matches = sorted(folder.glob("PresentMon*.exe"), reverse=True)
            if matches:
                return str(matches[0])
    return None


class FrameCounter:
    """Frames per program as they come; the rate of the busiest one."""

    def __init__(self) -> None:
        self._frames: dict[str, deque[tuple[float, float]]] = {}  # app -> (time, ms)

    def add(self, app: str, ms: float, at: float) -> None:
        if not app or app.lower() in IGNORED or not 0 < ms < 5000:
            return
        frames = self._frames.setdefault(app, deque())
        frames.append((at, ms))
        while frames and at - frames[0][0] > LOWS_WINDOW:
            frames.popleft()

    def readings(self, now: float) -> dict[str, Reading]:
        best, best_count = None, 0
        for app, frames in list(self._frames.items()):
            while frames and now - frames[0][0] > LOWS_WINDOW:
                frames.popleft()
            if not frames:
                del self._frames[app]
                continue
            count = sum(1 for at, _ms in frames if now - at <= WINDOW)
            if count > best_count:
                best, best_count = app, count
        if best is None or best_count < 2:
            return {}
        frames = self._frames[best]
        recent = [ms for at, ms in frames if now - at <= WINDOW]
        mean = sum(recent) / len(recent)
        slowest = sorted((ms for _at, ms in frames), reverse=True)
        low = slowest[: max(1, len(slowest) // 100)]  # the slowest 1 %
        name = best[:-4] if best.lower().endswith(".exe") else best
        return {
            "game.fps": Reading("game.fps", round(1000 / mean, 1), "fps", "Frames per second"),
            "game.frametime": Reading("game.frametime", round(mean, 2), "ms", "Frame time"),
            "game.low": Reading(
                "game.low", round(1000 / (sum(low) / len(low)), 1), "fps", "1% low"
            ),  # fmt: skip
            "game.app": Reading("game.app", name, "", "Game"),
        }


def frame_column(header: list[str]) -> str | None:
    return next((c for c in _FRAME_COLUMNS if c in header), None)


class PresentMonProvider(SensorProvider):
    name = "presentmon"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.path = str(self.options.get("path", ""))
        self._counter = FrameCounter()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._warned = False

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="sensors-presentmon", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        program = find_presentmon(self.path)
        if program is None:
            if not self._warned:
                log.warning("PresentMon not found: set path in [sensors.presentmon]")
                self._warned = True
            return
        command = [program, "--output_stdout", "--stop_existing_session",
                   "--session_name", "LibrePanel", "--no_console_stats"]  # fmt: skip
        try:
            self._process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )  # fmt: skip
        except OSError as exc:
            log.warning("PresentMon could not start: %s", exc)
            return
        watcher = threading.Thread(target=self._watch, name="presentmon-idle", daemon=True)
        watcher.start()
        reader = csv.reader(self._process.stdout)
        header: list[str] = []
        app_at = frame_at = -1
        for row in reader:
            if self._stop.is_set():
                break
            if not header:
                if "Application" not in row:
                    continue
                header = row
                column = frame_column(header)
                if column is None:
                    log.warning("PresentMon: no frame time column in %s", header[:12])
                    break
                app_at, frame_at = header.index("Application"), header.index(column)
                continue
            try:
                ms = float(row[frame_at])
            except (IndexError, ValueError):
                continue
            with self._lock:
                self._counter.add(row[app_at], ms, time.monotonic())
        self._end()
        if self._process is not None and self._process.poll() not in (None, 0) and not header:
            log.warning("PresentMon ended at once: it needs administrator rights or the "
                        '"Performance Log Users" group')  # fmt: skip

    def _watch(self) -> None:
        """End PresentMon when nobody looked for a minute."""
        idle = 0.0
        while not self._stop.is_set() and self._process and self._process.poll() is None:
            idle = 0.0 if wanted("game.") else idle + 1.0
            if idle > 60:
                break
            self._stop.wait(1.0)
        self._end()

    def _end(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def read(self) -> dict[str, Reading]:
        if not wanted("game."):
            return {}
        self._start()
        with self._lock:
            return self._counter.readings(time.monotonic())

    def close(self) -> None:
        self._stop.set()
        self._end()

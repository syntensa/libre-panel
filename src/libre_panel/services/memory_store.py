"""The memory's database: one SQLite file in WAL mode, written by a single thread from a
bounded queue, so that nothing that samples ever waits for the disk.

Readers (the cooling analysis, a console page) open the file read only, next to the writer;
WAL mode lets them. Everything stored here stays on this computer.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DATABASE = "memory.db"
QUEUE_SIZE = 5000  # jobs; at about one a second this is more than an hour of a stuck disk
RETRY_S = 30.0  # a database that cannot be opened is tried again after this long

# The numbers kept per minute (means), and of those the highest value too.
MEANS = (
    "cpu_load", "gpu_load", "cpu_temp", "gpu_temp", "gpu_hotspot", "cpu_power", "gpu_power",
    "cpu_clock", "gpu_clock", "mem_used", "vram_used", "net_down", "net_up", "volume",
)  # fmt: skip
PEAKS = ("cpu_temp", "gpu_temp", "gpu_hotspot", "cpu_power", "gpu_power")
SESSION_PEAKS = (*PEAKS, "vram_used")
# What the cooling state holds as numbers; the other temperatures are in a column of their own.
COOLING_VALUES = (
    "cpu_temp", "cpu_power", "cpu_load", "gpu_temp", "gpu_hotspot", "gpu_power", "gpu_load",
)  # fmt: skip


def _columns(names: tuple[str, ...], suffix: str = "") -> str:
    return ", ".join(f"{name}{suffix} REAL" for name in names)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS minutes (
    ts INTEGER PRIMARY KEY,          -- start of the minute, Unix seconds
    samples INTEGER, mode TEXT, game TEXT,
    {_columns(MEANS)},
    {_columns(PEAKS, "_max")},
    fps REAL, fps_low REAL,
    music_s INTEGER,                 -- how long music played: never what
    outside_temp REAL,
    fans TEXT                        -- {{"name": rpm, ...}}
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,          -- start in milliseconds, given when the game began
    game TEXT, program TEXT, started INTEGER, ended INTEGER, duration_s INTEGER,
    fps_s INTEGER, fps_mean REAL, fps_p01 REAL, fps_low_mean REAL, frametime_mean REAL,
    levels TEXT,                     -- {{"edges": [...], "seconds": [...]}}
    limit_cpu_s INTEGER, limit_gpu_s INTEGER, limit_cap_s INTEGER,
    {_columns(SESSION_PEAKS, "_max")},
    outside_temp REAL, closed INTEGER
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started);
CREATE TABLE IF NOT EXISTS cooling (
    ts INTEGER PRIMARY KEY,          -- since when this state held, Unix seconds
    mode TEXT, game TEXT,            -- "normal" or "game"; which game
    {_columns(COOLING_VALUES)},
    outside_temp REAL,
    temps TEXT,                      -- more temperatures: {{"reading key": degrees}}
    fans TEXT,                       -- {{"name": [rpm, duty %]}}
    sources TEXT                     -- {{"curve": degrees}}: what each FanControl curve reads
);
CREATE TABLE IF NOT EXISTS fancontrol (
    ts INTEGER PRIMARY KEY,          -- since when this setting held
    fingerprint TEXT,                -- of the curves and fans
    state TEXT                       -- fancontrol.state_of(): curves and fans
);
"""


def read_only(path: str | Path) -> sqlite3.Connection:
    """The database for reading only (the writer may be at work; WAL lets both)."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


class Store:
    """Runs what it is given on one thread, in order: SQL statements, or functions that get
    the connection (the round report reads the session it has just seen written).

    ``put`` and ``run`` return at once; when the queue is full (the disk is stuck) the job is
    dropped and ``error`` says so, since the memory must never hold the panel back.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        size: int = QUEUE_SIZE,
        retry_s: float = RETRY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self.retry_s, self.clock = retry_s, clock
        self.written = 0
        self.error: str | None = None
        self._jobs: queue.Queue[Any] = queue.Queue(maxsize=size)
        self._closing = threading.Event()
        self._retry_at = float("-inf")
        self._thread = threading.Thread(target=self._run, name="memory-store", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def put(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> None:
        self._enqueue((sql, params))

    def run(self, job: Callable[[sqlite3.Connection], None]) -> None:
        self._enqueue(job)

    def _enqueue(self, job: Any) -> None:
        try:
            self._jobs.put_nowait(job)
        except queue.Full:
            self.error = "the queue is full: the disk is too slow or the file is locked"

    def stop(self, timeout: float = 3.0) -> None:
        """Write what is queued, then end; gives up waiting after ``timeout``."""
        self._closing.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    # -- the thread ----------------------------------------------------------------------

    def _open(self) -> sqlite3.Connection | None:
        if self.clock() < self._retry_at:
            return None
        try:
            connection = sqlite3.connect(self.path)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")  # safe against a crash
                connection.executescript(SCHEMA)
                connection.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
                )
                connection.commit()
            except Exception:
                connection.close()
                raise
        except Exception as exc:
            self._retry_at = self.clock() + self.retry_s
            self._failed(f"the database cannot be used: {type(exc).__name__}: {exc}")
            return None
        return connection

    def _failed(self, text: str) -> None:
        if text != self.error:
            log.warning("memory: %s", text)
        self.error = text

    def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        while True:
            try:
                job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                if self._closing.is_set():
                    break
                continue
            if connection is None:
                connection = self._open()
                if connection is None:
                    continue  # the job is lost; a stuck database must not fill the memory
            try:
                if callable(job):
                    job(connection)
                else:
                    connection.execute(*job)
                    connection.commit()
                    self.written += 1
                self.error = None
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
        if connection is not None:
            connection.close()


# -- reading --------------------------------------------------------------------------------------


def last_fingerprint(path: str | Path) -> str:
    """The mark of the FanControl state stored last ("" for none yet, or no database)."""
    try:
        connection = read_only(path)
        try:
            row = connection.execute(
                "SELECT fingerprint FROM fancontrol ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return ""
    return row[0] if row else ""

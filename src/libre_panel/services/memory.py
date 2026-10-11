"""Memory: what happens at the desk over weeks and months, and the round report after a game.

Once a second the service takes a sample of the readings and keeps, in a database on this
computer (``memory_store``): a row per minute (means and highest values, how long music
played, never what), a row per game played (frame rates, what slowed it, the highest
temperatures), the cooling state whenever it moves (temperatures, fan speeds and duty, power,
load, the temperature each FanControl curve reads) and every change to FanControl's curves.
Titles of tracks, windows and files are never read, so none can be kept; of a game only the
name and the program's name are.

When a game has been played long enough, the service builds a round report: the round
against the last ones of the same program. It publishes it (``report`` and ``report.*``) and
shows a theme for a while, which a new game ends at once. The game itself comes from the
``game`` service (``game.name``) and the frame rates from PresentMon (``game.*``).
"""

from __future__ import annotations

import bisect
import fnmatch
import json
import logging
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from libre_panel.i18n import t
from libre_panel.plugins import Service
from libre_panel.sensors.base import Snapshot
from libre_panel.services import fancontrol, round_report
from libre_panel.services.memory_store import (
    COOLING_VALUES,
    DATABASE,
    MEANS,
    PEAKS,
    SESSION_PEAKS,
    Store,
    last_fingerprint,
)
from libre_panel.services.readings import is_playing, number

log = logging.getLogger(__name__)

SAMPLE_S = 1.0  # one sample a second, however often the main loop takes readings
GAP_S = 5.0  # after a pause longer than this the grid starts afresh instead of catching up
SESSION_WRITE_S = 60.0  # a game in progress is written this often
SENSORS_EVERY_S = 60.0  # the names of the sensors are looked at this often
REPORT_GRACE_S = 3.0  # the readings of a report outlive it: its theme leaves with a transition
DAY_S = 86400
# The cooling state is written when something moves, not on a clock (it all stands still at
# the desk): at least COOLING_MIN_S apart (less in a game, where it changes fast) and at
# least every COOLING_MAX_S, so a larger gap always means the service did not run.
COOLING_MIN_S = 12.0
COOLING_MIN_GAME_S = 5.0
COOLING_MAX_S = 60.0
# How far a value must move to count; below that it is noise.
MOVE_TEMP = 2.0  # K
MOVE_RPM = 40.0  # a fan's speed jitters by about 25
MOVE_DUTY = 1.0  # percentage points: what the fan is told, the thing that matters
MOVE_POWER = 15.0  # W
MOVE_LOAD = 20.0  # percent
DEFAULT_LEVELS = (60, 90, 120, 144, 165)
# Where each number comes from. Core keys only: whatever source supplies them.
SOURCES = {
    "cpu_load": "cpu.load", "gpu_load": "gpu.load", "cpu_temp": "cpu.temp",
    "gpu_temp": "gpu.temp", "cpu_power": "cpu.power", "gpu_power": "gpu.power",
    "cpu_clock": "cpu.freq", "gpu_clock": "gpu.freq", "mem_used": "mem.used",
    "vram_used": "gpu.mem.used", "net_down": "net.down", "net_up": "net.up",
    "volume": "audio.volume",
}  # fmt: skip
# What the service asks the sources for, so that those that work on demand run for it.
WANTED = (
    "cpu.load", "gpu.load", "cpu.temp", "gpu.temp", "cpu.power", "gpu.power", "cpu.freq",
    "gpu.freq", "gpu.mem.used", "fan.*", "game.*",
)  # fmt: skip
_GLOB = re.compile(r"[*?\[]")
GIGABYTES = {"": 1.0, "GB": 1.0, "GiB": 1.0, "MB": 1 / 1024, "MiB": 1 / 1024, "B": 2.0**-30}


@dataclass(frozen=True)
class Settings:
    """The service's options, checked."""

    keep_days: float = 730.0
    cooling: bool = True
    cooling_days: float = 180.0
    music: bool = True
    report: bool = True
    report_min_s: float = 180.0
    report_compare: int = 10
    report_seconds: float = 30.0
    report_theme: str = "round-report"
    report_transition: str = "tube"
    fps_levels: tuple[float, ...] = DEFAULT_LEVELS
    temperatures: tuple[str, ...] = ()

    @classmethod
    def from_options(cls, options: dict[str, Any], report: Callable[[str], None]) -> Settings:
        levels = sorted({float(n) for n in options["fps_levels"] if number(n) and n > 0})
        if len(levels) != len(options["fps_levels"]):
            report("fps_levels: only positive numbers count, once each")
        temperatures = [
            k.strip() for k in options["temperatures"] if isinstance(k, str) and k.strip()
        ]
        if len(temperatures) != len(options["temperatures"]):
            report("temperatures: expected readings such as 'temp.board.system' or 'temp.nvme.*'")
        return cls(
            keep_days=float(options["keep_days"]),
            cooling=options["cooling"],
            cooling_days=float(options["cooling_days"]),
            music=options["music"],
            report=options["report"],
            report_min_s=max(0.0, float(options["report_minutes"])) * 60,
            report_compare=max(1, options["report_compare"]),
            report_seconds=max(1.0, float(options["report_seconds"])),
            report_theme=options["report_theme"],
            report_transition=options["report_transition"],
            fps_levels=tuple(levels) or DEFAULT_LEVELS,
            temperatures=tuple(temperatures),
        )


# -- one sample ----------------------------------------------------------------------------------


@dataclass
class Sample:
    """What one second of the readings says. ``fans`` map a name to (rpm, duty percent)."""

    at: float  # Unix time
    values: dict[str, float | None] = field(default_factory=dict)  # by column of MEANS
    game: str | None = None
    program: str = ""
    fps: float | None = None
    low: float | None = None
    frametime: float | None = None
    limiter: str | None = None
    music: bool = False
    outside: float | None = None
    fans: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    temps: dict[str, float] = field(default_factory=dict)  # more temperatures, by reading key
    sources: dict[str, float] = field(default_factory=dict)  # what each curve reads, by curve


def gigabytes(reading: Any) -> float | None:
    """A memory size in GB: LibreHardwareMonitor gives the graphics memory in MB."""
    value = number(reading.value)
    factor = GIGABYTES.get(reading.unit)
    return None if value is None or factor is None else value * factor


class Reader:
    """Takes the numbers the memory keeps out of a snapshot. Knows the names FanControl gave
    the fans and the sensor each curve reads, once the service has read its configuration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.names: dict[str, str] = {}  # fan sensor id -> the name in FanControl
        self.hidden: set[str] = set()  # fans FanControl hides: connectors with nothing on them
        self.sources: dict[str, str] = {}  # curve -> id of the sensor it reads
        self._hotspot: str | None = None

    def read(self, snapshot: Snapshot, at: float) -> Sample:
        readings = snapshot.readings
        values: dict[str, float | None] = {}
        for column, key in SOURCES.items():
            reading = readings.get(key)
            if reading is None:
                values[column] = None
            elif column == "vram_used":
                values[column] = gigabytes(reading)
            else:
                values[column] = number(reading.value)
        values["gpu_hotspot"] = self._gpu_hotspot(readings)
        name = snapshot.value("game.name")
        sample = Sample(at, values, game=name if isinstance(name, str) and name else None)
        if sample.game:
            self._game(snapshot, sample)
        sample.music = self.settings.music and is_playing(snapshot)
        sample.outside = self._outside(readings.get("weather.temperature"))
        sample.fans = self._fans(readings)
        for entry in self.settings.temperatures:
            for key in fnmatch.filter(readings, entry) if _GLOB.search(entry) else [entry]:
                reading = readings.get(key)
                if reading is not None and number(reading.value) is not None:
                    sample.temps[key] = float(reading.value)
        for curve, sensor in self.sources.items():
            reading = readings.get(f"lhm:{sensor}")
            if reading is not None and number(reading.value) is not None:
                sample.sources[curve] = float(reading.value)
        return sample

    @staticmethod
    def _game(snapshot: Snapshot, sample: Sample) -> None:
        sample.fps = _positive(snapshot.value("game.fps"))
        sample.low = _positive(snapshot.value("game.low"))
        sample.frametime = _positive(snapshot.value("game.frametime"))
        limiter = snapshot.value("game.limiter")
        sample.limiter = limiter if limiter in ("cpu", "gpu", "cap") else None
        program = snapshot.value("game.app")
        if isinstance(program, str):
            sample.program = program.strip().lower().removesuffix(".exe")

    @staticmethod
    def _outside(reading: Any) -> float | None:
        value = number(reading.value) if reading is not None else None
        if value is not None and reading.unit == "°F":
            return (value - 32) * 5 / 9
        return value

    def _gpu_hotspot(self, readings: dict[str, Any]) -> float | None:
        """The graphics card's hot spot: the one of the card ``gpu.temp`` is read from, found
        by its name once and kept as long as it is there."""
        reading = readings.get(self._hotspot) if self._hotspot else None
        if reading is None:
            self._hotspot = self._find_hotspot(readings)
            reading = readings.get(self._hotspot) if self._hotspot else None
        return number(reading.value) if reading is not None else None

    @staticmethod
    def _find_hotspot(readings: dict[str, Any]) -> str | None:
        main = readings.get("gpu.temp")
        card = main.origin.rpartition("/temperature/")[0] if main is not None else ""
        found = []
        for key, reading in readings.items():
            if not key.startswith("temp.gpu"):
                continue
            words = f"{key} {reading.label}".lower().replace("_", " ")
            if "hot spot" in words or "hotspot" in words or "junction" in words:
                found.append((not (card and reading.origin.startswith(card + "/")), key))
        return min(found)[1] if found else None  # the main card's first

    def sensors(self, readings: dict[str, Any]) -> dict[str, Any]:
        """The names of the hardware and of every temperature sensor, for the cooling analysis
        to speak of them by name: ``cpu`` and ``gpu`` as the readings call them, and for each
        ``temp.*`` reading its id (where LibreHardwareMonitor gave one), its label, and
        ``panel``: whether it is the sensor behind ``cpu_temp``, ``gpu_temp`` or ``gpu_hotspot``."""
        behind = {}
        hotspot = self._hotspot if self._hotspot in readings else self._find_hotspot(readings)
        for column, key in (
            ("cpu_temp", "cpu.temp"),
            ("gpu_temp", "gpu.temp"),
            ("gpu_hotspot", hotspot),
        ):
            reading = readings.get(key) if key else None
            if reading is not None and reading.origin:
                behind[reading.origin] = column
        names = {}
        for kind in ("cpu", "gpu"):
            reading = readings.get(f"{kind}.name")
            names[kind] = (
                reading.value if reading is not None and isinstance(reading.value, str) else None
            )
        found = []
        for key, reading in sorted(readings.items()):
            if key.startswith("temp.") and number(reading.value) is not None:
                ident = (
                    reading.origin.removeprefix("lhm:")
                    if reading.origin.startswith("lhm:")
                    else None
                )
                found.append(
                    {
                        "key": key,
                        "id": ident,
                        "label": reading.label,
                        "panel": behind.get(reading.origin),
                    }
                )
        return {**names, "sensors": found}

    def _fans(self, readings: dict[str, Any]) -> dict[str, tuple[float | None, float | None]]:
        """Every fan: its speed and, where LibreHardwareMonitor also has the output that
        drives it, the duty. Named as FanControl names it, else as the sensor does."""
        fans: dict[str, tuple[float | None, float | None]] = {}
        for key, reading in readings.items():
            if not key.startswith("fan.") or number(reading.value) is None:
                continue
            sensor = (
                reading.origin.removeprefix("lhm:") if reading.origin.startswith("lhm:") else ""
            )
            if sensor in self.hidden:
                continue
            duty = None
            if sensor:
                output = readings.get("lhm:" + sensor.replace("/fan/", "/control/"))
                duty = number(output.value) if output is not None else None
            name = self.names.get(sensor) or reading.label or key
            if name in fans:
                name = key  # two fans with one name
            fans[name] = (float(reading.value), duty)
        return fans


def _positive(value: Any) -> float | None:
    found = number(value)
    return found if found is not None and found > 0 else None


# -- turning samples into rows -------------------------------------------------------------------

MINUTE_COLUMNS = (
    "ts", "samples", "mode", "game", *MEANS, *(f"{k}_max" for k in PEAKS), "fps", "fps_low",
    "music_s", "outside_temp", "fans",
)  # fmt: skip
SESSION_COLUMNS = (
    "id", "game", "program", "started", "ended", "duration_s", "fps_s", "fps_mean", "fps_p01",
    "fps_low_mean", "frametime_mean", "levels", "limit_cpu_s", "limit_gpu_s", "limit_cap_s",
    *(f"{k}_max" for k in SESSION_PEAKS), "outside_temp", "closed",
)  # fmt: skip
COOLING_COLUMNS = (
    "ts", "mode", "game", *COOLING_VALUES, "outside_temp", "temps", "fans", "sources",
)  # fmt: skip


def insert(table: str, columns: tuple[str, ...]) -> str:
    marks = ", ".join("?" * len(columns))
    return f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({marks})"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class _Minute:
    """The samples of one minute."""

    def __init__(self, ts: int) -> None:
        self.ts, self.n = ts, 0
        self.total: Counter[str] = Counter()
        self.count: Counter[str] = Counter()
        self.peak: dict[str, float] = {}
        self.fan_total: Counter[str] = Counter()
        self.fan_count: Counter[str] = Counter()
        self.music_s = 0
        self.fps: list[float] = []
        self.low: list[float] = []
        self.game: str | None = None
        self.mode = "normal"
        self.outside: float | None = None

    def add(self, sample: Sample) -> None:
        self.n += 1
        for key, value in sample.values.items():
            if value is None:
                continue
            self.total[key] += value
            self.count[key] += 1
            if key in PEAKS and value > self.peak.get(key, -math.inf):
                self.peak[key] = value
        for name, (rpm, _duty) in sample.fans.items():
            if rpm is not None:
                self.fan_total[name] += rpm
                self.fan_count[name] += 1
        self.music_s += sample.music
        if sample.game:
            self.mode, self.game = "game", sample.game
            if sample.fps is not None:
                self.fps.append(sample.fps)
                if sample.low is not None:
                    self.low.append(sample.low)
        if sample.outside is not None:
            self.outside = sample.outside

    def row(self) -> list[Any]:
        fans = {n: round(self.fan_total[n] / self.fan_count[n]) for n in self.fan_count}
        return [
            self.ts, self.n, self.mode, self.game,
            *(self.total[k] / self.count[k] if self.count[k] else None for k in MEANS),
            *(self.peak.get(k) for k in PEAKS),
            _mean(self.fps), _mean(self.low), self.music_s, self.outside,
            json.dumps(fans) if fans else None,
        ]  # fmt: skip


class _Session:
    """A game being played, from its first sample to its last."""

    def __init__(self, sample: Sample, edges: tuple[float, ...]) -> None:
        self.id = int(sample.at * 1000)
        self.game, self.program = sample.game, sample.program
        self.started = self.written = sample.at
        self.edges = edges
        self.fps: list[float] = []
        self.low: list[float] = []
        self.frametime: list[float] = []
        self.levels: Counter[int] = Counter()
        self.limits: Counter[str] = Counter()
        self.peaks: dict[str, float] = {}
        self.outside = sample.outside

    def add(self, sample: Sample) -> None:
        if not self.program and sample.program:
            self.program = sample.program
        if sample.fps is not None:
            self.fps.append(sample.fps)
            self.levels[bisect.bisect_right(self.edges, sample.fps)] += 1
            if sample.low is not None:
                self.low.append(sample.low)
            if sample.frametime is not None:
                self.frametime.append(sample.frametime)
            if sample.limiter:
                self.limits[sample.limiter] += 1
        for key in SESSION_PEAKS:
            value = sample.values.get(key)
            if value is not None and value > self.peaks.get(key, -math.inf):
                self.peaks[key] = value
        if self.outside is None:
            self.outside = sample.outside

    def row(self, at: float, closed: bool) -> list[Any]:
        ranked = sorted(self.fps)
        levels = {
            "edges": list(self.edges),
            "seconds": [self.levels[i] for i in range(len(self.edges) + 1)],
        }
        return [
            self.id, self.game, self.program, round(self.started), round(at),
            round(at - self.started), len(ranked), _mean(ranked),
            ranked[int(len(ranked) * 0.01)] if ranked else None,
            _mean(self.low), _mean(self.frametime), json.dumps(levels),
            self.limits["cpu"], self.limits["gpu"], self.limits["cap"],
            *(self.peaks.get(k) for k in SESSION_PEAKS), self.outside, 1 if closed else 0,
        ]  # fmt: skip


@dataclass
class _Cooling:
    at: float
    mode: str
    game: str | None
    values: dict[str, float | None]
    fans: dict[str, tuple[float | None, float | None]]
    temps: dict[str, float]
    sources: dict[str, float]


def moved(last: _Cooling, now: _Cooling) -> bool:
    """Whether the cooling state has changed by more than noise since the row written last."""
    for key, value in now.values.items():
        before = last.values.get(key)
        if (before is None) != (value is None):
            return True
        if value is None or before is None:
            continue
        if "temp" in key or "hotspot" in key:
            step = MOVE_TEMP
        else:
            step = MOVE_POWER if key.endswith("power") else MOVE_LOAD
        if abs(value - before) >= step:
            return True
    if set(now.fans) != set(last.fans):
        return True
    for name, (rpm, duty) in now.fans.items():
        before_rpm, before_duty = last.fans[name]
        if (rpm is None) != (before_rpm is None) or (duty is None) != (before_duty is None):
            return True
        if rpm is not None and before_rpm is not None and abs(rpm - before_rpm) >= MOVE_RPM:
            return True
        if duty is not None and before_duty is not None and abs(duty - before_duty) >= MOVE_DUTY:
            return True
    for pool, before in ((now.temps, last.temps), (now.sources, last.sources)):
        if any(abs(v - before.get(k, -1e9)) >= MOVE_TEMP for k, v in pool.items()):
            return True
    return False


class Recorder:
    """Turns samples, at most one a second, into rows for the store: the minutes, the games
    and the cooling state. Time is whatever the samples say, so a test runs a day in a moment.

    ``on_report(report, stamp)`` is called on the store's thread with the round report of a
    game that has just ended (when it was long enough and had frame rates), and ``stamp`` as
    ``stamp()`` was when the game ended: what the service uses to tell a report is out of date.
    """

    def __init__(
        self,
        store: Store,
        settings: Settings,
        on_report: Callable[[dict[str, Any], Any], None] | None = None,
        stamp: Callable[[], Any] = lambda: None,
    ) -> None:
        self.store, self.settings, self.on_report, self.stamp = store, settings, on_report, stamp
        self._due = 0.0
        self._minute: _Minute | None = None
        self._session: _Session | None = None
        self._cooling: _Cooling | None = None
        self._cleaned = 0.0

    def due(self, at: float) -> bool:
        return at >= self._due

    def add(self, sample: Sample) -> None:
        if not self.due(sample.at):
            return
        self._due = self._due + SAMPLE_S if sample.at - self._due < GAP_S else sample.at + SAMPLE_S
        self._add_minute(sample)
        if self.settings.cooling:
            self._add_cooling(sample)
        self._add_session(sample)

    def close(self, at: float) -> None:
        """Keep what is under way: the minute and the game (which counts as ended, but
        shows no report)."""
        if self._minute is not None:
            self._finish_minute(at)
        if self._session is not None:
            self._write_session(at, closed=True, report=False)
            self._session = None

    # -- minutes --------------------------------------------------------------------------------

    def _add_minute(self, sample: Sample) -> None:
        ts = int(sample.at // 60 * 60)
        if self._minute is not None and self._minute.ts != ts:
            self._finish_minute(sample.at)
        if self._minute is None:
            self._minute = _Minute(ts)
        self._minute.add(sample)

    def _finish_minute(self, now: float) -> None:
        minute, self._minute = self._minute, None
        if minute is None or not minute.n:
            return
        self.store.put(insert("minutes", MINUTE_COLUMNS), minute.row())
        if now - self._cleaned > DAY_S:
            self._cleaned = now
            self._clean(now)

    def _clean(self, now: float) -> None:
        """Forget what is older than the settings keep (zero: keep everything)."""
        if self.settings.keep_days > 0:
            cutoff = int(now - self.settings.keep_days * DAY_S)
            self.store.put("DELETE FROM minutes WHERE ts < ?", [cutoff])
            self.store.put("DELETE FROM sessions WHERE started < ?", [cutoff])
        if self.settings.cooling_days > 0:
            self.store.put(
                "DELETE FROM cooling WHERE ts < ?", [int(now - self.settings.cooling_days * DAY_S)]
            )

    # -- cooling --------------------------------------------------------------------------------

    def _add_cooling(self, sample: Sample) -> None:
        mode = "game" if sample.game else "normal"
        now = _Cooling(
            sample.at, mode, sample.game,
            {key: sample.values.get(key) for key in COOLING_VALUES},
            dict(sample.fans), dict(sample.temps), dict(sample.sources),
        )  # fmt: skip
        last = self._cooling
        if last is not None and (last.mode, last.game) == (mode, sample.game):
            since = sample.at - last.at
            if since < (COOLING_MIN_GAME_S if mode == "game" else COOLING_MIN_S):
                return
            if since < COOLING_MAX_S and not moved(last, now):
                return
        self._cooling = now
        fans = {name: [rpm, duty] for name, (rpm, duty) in now.fans.items()}
        self.store.put(
            insert("cooling", COOLING_COLUMNS),
            [
                int(sample.at), mode, sample.game,
                *(now.values[k] for k in COOLING_VALUES), sample.outside,
                json.dumps(now.temps) if now.temps else None,
                json.dumps(fans) if fans else None,
                json.dumps({k: round(v, 1) for k, v in now.sources.items()})
                if now.sources
                else None,
            ],
        )  # fmt: skip

    # -- games ----------------------------------------------------------------------------------

    def _add_session(self, sample: Sample) -> None:
        session = self._session
        if session is not None and sample.game != session.game:
            self._write_session(sample.at, closed=True, report=True)
            self._session = session = None
        if sample.game is None:
            return
        if session is None:
            session = self._session = _Session(sample, tuple(self.settings.fps_levels))
            self._write_session(sample.at, closed=False)
        session.add(sample)
        if sample.at - session.written >= SESSION_WRITE_S:
            self._write_session(sample.at, closed=False)

    def _write_session(self, at: float, closed: bool, report: bool = False) -> None:
        session = self._session
        if session is None:
            return
        session.written = at
        self.store.put(insert("sessions", SESSION_COLUMNS), session.row(at, closed))
        if closed and report and self.on_report is not None and self.settings.report:
            # on the store's thread, after the row above: the report reads it from the file
            id_, stamp = session.id, self.stamp()
            self.store.run(lambda con: self._report(con, id_, stamp))

    def _report(self, con: sqlite3.Connection, session_id: int | None, stamp: Any) -> None:
        found = round_report.build(
            con, session_id, self.settings.report_min_s, self.settings.report_compare
        )
        if found is not None and self.on_report is not None:
            self.on_report(found, stamp)


# -- the service -----------------------------------------------------------------------------------


class Memory(Service):
    """Keeps what happens over time and shows a report after a game."""

    name = "memory"
    api = 1
    options = {
        "keep_days": ("number", 730.0),
        "cooling": ("bool", True),
        "cooling_days": ("number", 180.0),
        "music": ("bool", True),
        "temperatures": ("list", []),
        "fps_levels": ("list", list(DEFAULT_LEVELS)),
        "report": ("bool", True),
        "report_minutes": ("number", 3.0),
        "report_compare": ("int", 10),
        "report_seconds": ("number", 30.0),
        "report_theme": ("string", "round-report"),
        "report_transition": ("string", "tube"),
        "fancontrol": ("string", ""),
        "database": ("string", ""),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(host, options)
        self.clock, self.wall = clock, wall
        self.settings = Settings.from_options(options, lambda text: host.log.warning("%s", text))
        self.reader = Reader(self.settings)
        self.watch = fancontrol.Watch(options["fancontrol"])
        self.store: Store | None = None
        self.recorder: Recorder | None = None
        self.database: Path | None = None
        self._lock = threading.RLock()
        self._stopped = False
        self._mark: str | None = None  # the fingerprint of the FanControl state stored last
        self._sensors = ""  # the names of the sensors, as stored last
        self._sensors_due = 0.0
        self._game = ""  # the game now under way, for the status
        self._shown = False  # a report is on the panel
        self._keys: set[str] = set()  # the readings it published
        self._until = 0.0  # on `clock`: when it goes
        self._gone_at = 0.0  # on `clock`: when its readings go, after it has ended
        self._games = 0  # how many times a mode began: a report is stale when a game came since

    def start(self) -> None:
        given = self.options["database"]
        self.database = Path(given).expanduser() if given else self.host.data_dir / DATABASE
        self.store = Store(self.database)
        self.recorder = Recorder(self.store, self.settings, self._report_ready, lambda: self._games)
        self.store.start()
        self._want()
        self.host.on("sample", self.on_sample)
        self.host.on("mode-changed", self.on_mode)
        self.host.log.info("memory: %s", self.database)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True  # a sample under way, or a report being built, then does nothing
            if self.recorder is not None:
                self.recorder.close(self.wall())
        if self.store is not None:
            self.store.stop()

    def _want(self) -> None:
        keys = [*WANTED, *self.settings.temperatures]
        keys += [f"lhm:{sensor}" for sensor in sorted(set(self.reader.sources.values()))]
        if self.settings.music:
            keys += ["media.state", "media.playing"]  # not the title: nothing here reads it
        self.host.want(*keys)

    # -- every sample ----------------------------------------------------------------------------

    def on_sample(self, snapshot: Snapshot) -> None:
        with self._lock:
            if self._stopped or self.recorder is None:
                return
            now = self.wall()
            self._expire()
            self._tidy()
            self._look_at_fancontrol(now)
            if self.recorder.due(now):
                sample = self.reader.read(snapshot, now)
                self._game = sample.game or ""
                self.recorder.add(sample)
                self._note_sensors(snapshot, now)

    def _note_sensors(self, snapshot: Snapshot, now: float) -> None:
        """Keep the names of the sensors, for the cooling analysis; written when they change."""
        if not self.settings.cooling or self.store is None or now < self._sensors_due:
            return
        self._sensors_due = now + SENSORS_EVERY_S
        found = self.reader.sensors(snapshot.readings)
        if not found["sensors"] and not (found["cpu"] or found["gpu"]):
            return
        text = json.dumps(found, sort_keys=True, ensure_ascii=False)
        if text != self._sensors:
            self._sensors = text
            self.store.put("INSERT OR REPLACE INTO meta VALUES ('sensors', ?)", [text])

    def _look_at_fancontrol(self, now: float) -> None:
        """Note a changed FanControl configuration: the names of the fans and what each curve
        reads for the samples, and the state itself for the analysis, which looks at the data
        since the last change."""
        section = self.watch.poll(now)
        if section is None:
            return
        state = fancontrol.state_of(section)
        self.reader.names, self.reader.hidden = fancontrol.names_and_hidden(section)
        curves, _fans = fancontrol.curves_and_fans(state)
        self.reader.sources = {c.name: c.source for c in curves.values() if c.source}
        self._want()
        if not self.settings.cooling or self.store is None or self.database is None:
            return
        mark = fancontrol.fingerprint(state)
        if self._mark is None:
            self._mark = last_fingerprint(self.database)
        if mark != self._mark:
            self._mark = mark
            text = json.dumps(state, sort_keys=True, ensure_ascii=False)
            self.store.put(
                "INSERT OR REPLACE INTO fancontrol VALUES (?, ?, ?)", [int(now), mark, text]
            )

    # -- the report ------------------------------------------------------------------------------

    def _report_ready(self, report: dict[str, Any], games: int) -> None:
        """A round report has been built (on the store's thread): show it, unless a game has
        begun since, which the report would only be in the way of."""
        with self._lock:
            if self._stopped or self.host.mode is not None or games != self._games:
                return
            self.show(report)

    def show(self, report: dict[str, Any]) -> None:
        """Publish the report and put its theme on the panel for ``report_seconds``. A second
        report while the first is on show only replaces its content."""
        s = self.settings
        report = {**report, "seconds": s.report_seconds, "until": self.wall() + s.report_seconds}
        readings = round_report.readings(report)
        with self._lock:
            again, self._shown = self._shown, True
            self._until = self.clock() + s.report_seconds
            self.host.publish_state("report", report, label=t("Round report"))
            for key in self._keys - set(readings):  # levels of another range
                self.host.unpublish(key)
            for key, (value, unit, label) in readings.items():
                self.host.publish(key, value, unit, label)
            self._keys = set(readings)
            if not again and s.report_theme:
                self.host.show_theme(
                    s.report_theme, transition=s.report_transition or None, priority=10, mode=None
                )
        session = report["session"]
        self.host.log.info(
            "round report: %s, %d min, %s fps, against %d rounds",
            session["game"],
            (session["duration_s"] or 0) // 60,
            f"{session['fps_mean']:.0f}" if session["fps_mean"] else "-",
            report["comparison"]["count"],
        )

    def _expire(self) -> None:
        if self._shown and self.clock() >= self._until:
            self._end_report()

    def _end_report(self) -> None:
        """The report is over: its state goes at once, its theme leaves, and the readings the
        theme shows stay a moment longer, for as long as it takes to leave."""
        with self._lock:
            if not self._shown:
                return
            self._shown = False
            self.host.publish_state("report", None)
            grace = REPORT_GRACE_S if self.settings.report_theme else 0.0
            self._gone_at = self.clock() + grace
            if self.settings.report_theme:
                self.host.restore_theme(transition=self.settings.report_transition or None)
            self._tidy()

    def _tidy(self) -> None:
        """Take the readings of a report that is over back, once the grace is up."""
        with self._lock:
            if self._keys and not self._shown and self.clock() >= self._gone_at:
                for key in self._keys:
                    self.host.unpublish(key)
                self._keys = set()

    def on_mode(self, mode: str | None = None, **_: Any) -> None:
        """A game began: the report goes at once, and none that was being built is shown."""
        if mode is None:
            return
        with self._lock:
            self._games += 1
            self._end_report()

    def show_last_report(self) -> bool:
        """Build and show the report of the latest game that was long enough again (for a
        console page: "show the last round report"). False where nothing is recorded."""
        if self.store is None or self._stopped:
            return False
        games = self._games
        settings = self.settings

        def job(con: sqlite3.Connection) -> None:
            found = round_report.build(con, None, settings.report_min_s, settings.report_compare)
            if found is not None:
                self._report_ready(found, games)

        self.store.run(job)
        return True

    def status(self) -> dict[str, Any]:
        """What the service is doing, for a console page."""
        store = self.store
        return {
            "recording": store is not None and not self._stopped,
            "database": str(self.database) if self.database else None,
            "written": store.written if store else 0,
            "error": store.error if store else None,
            "game": self._game or None,
            "report": self._shown,
        }

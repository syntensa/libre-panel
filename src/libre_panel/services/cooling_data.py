"""What the cooling analysis stands on: the memory's cooling rows, weighted by the time they
hold, and what the fan curves say about them.

A row of the ``cooling`` table holds until the next one, but no longer than the memory's
heartbeat: half an hour of the desktop standing still is one row, a minute of load changes is
twelve, so everything is weighed by time, never by rows. From the duty a fan really had one can
calculate back the temperature its curve must have seen, which tells which sensor a curve
follows.
"""

from __future__ import annotations

import json
import sqlite3
import time
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path
from typing import Any

from libre_panel.services.fancontrol import Curve
from libre_panel.services.memory import COOLING_MAX_S
from libre_panel.services.memory_store import read_only

DAY_S = 86400
HEARTBEAT_S = COOLING_MAX_S  # a row holds this long at most
TEMPS = ("cpu_temp", "gpu_temp", "gpu_hotspot")  # the temperatures that have a column
DEVICE_SENSOR = {"cpu": "cpu_temp", "gpu": "gpu_temp"}  # what the panel shows for a device
SPAN_NEEDED = 5.0  # K a back-calculated course must have moved before a sensor is judged
MIN_SAMPLES = 20  # fewer than this many rows in the rising part of a curve judge nothing
DECODED_MAX = 50_000  # distinct JSON texts of a column that are kept while the rows are read


@dataclass
class Row:
    """The cooling state from ``ts`` on, for ``duration`` seconds. ``fans`` map a name to
    (rpm, duty percent), ``sources`` a curve to the temperature it read."""

    ts: int
    mode: str
    game: str | None
    duration: float = 0.0
    cpu_temp: float | None = None
    cpu_power: float | None = None
    cpu_load: float | None = None
    gpu_temp: float | None = None
    gpu_hotspot: float | None = None
    gpu_power: float | None = None
    gpu_load: float | None = None
    outside: float | None = None
    temps: dict[str, float] = field(default_factory=dict)
    fans: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    sources: dict[str, float] = field(default_factory=dict)

    @property
    def in_game(self) -> bool:
        return self.mode == "game"

    def temp(self, sensor: str) -> float | None:
        """A temperature by column (``cpu_temp``) or by the key of the reading it was kept as."""
        return getattr(self, sensor) if sensor in TEMPS else self.temps.get(sensor)

    def duty(self, fan: str) -> float | None:
        found = self.fans.get(fan)
        return found[1] if found else None

    def rpm(self, fan: str) -> float | None:
        found = self.fans.get(fan)
        return found[0] if found else None


def _json(text: Any) -> dict[str, Any]:
    try:
        found = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def load_rows(
    path: str | Path, days: float | None = None, mode: str | None = None, now: float | None = None
) -> list[Row]:
    """The cooling rows of the last ``days`` days (None: all), each with the time it holds.
    ``mode`` ``"game"`` or ``"desktop"`` keeps only those; the time each holds is worked out
    first, so a row beside a dropped one keeps its own. Raises ``sqlite3.Error`` where the
    file is missing or has no such table."""
    connection = read_only(path)
    connection.row_factory = sqlite3.Row
    query, args = "SELECT * FROM cooling", []
    if days:
        query += " WHERE ts >= ?"
        args.append(int((now if now is not None else time.time()) - days * DAY_S))
    try:
        raw = connection.execute(query + " ORDER BY ts", args).fetchall()
    finally:
        connection.close()
    fans_of, numbers_of = _once(_fans), _once(_numbers)
    rows = []
    for i, r in enumerate(raw):
        held = raw[i + 1]["ts"] - r["ts"] if i + 1 < len(raw) else HEARTBEAT_S
        rows.append(
            Row(
                r["ts"], r["mode"] or "normal", r["game"], min(float(held), HEARTBEAT_S),
                r["cpu_temp"], r["cpu_power"], r["cpu_load"], r["gpu_temp"], r["gpu_hotspot"],
                r["gpu_power"], r["gpu_load"], r["outside_temp"],
                numbers_of(r["temps"]), fans_of(r["fans"]), numbers_of(r["sources"]),
            )
        )  # fmt: skip
    if mode == "game":
        rows = [r for r in rows if r.in_game]
    elif mode == "desktop":
        rows = [r for r in rows if not r.in_game]
    return rows


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _numbers(raw: dict[str, Any]) -> dict[str, float]:
    return {k: v for k, v in raw.items() if _number(v) is not None}


def _fans(raw: dict[str, Any]) -> dict[str, tuple[float | None, float | None]]:
    return {
        name: (_number(pair[0]), _number(pair[1]))
        for name, pair in raw.items()
        if isinstance(pair, list) and len(pair) == 2
    }


def _once(make: Callable[[dict[str, Any]], Any]) -> Callable[[Any], Any]:
    """Decode a column's JSON text with ``make``, once for each distinct text: rows are written
    when something moves, and then most of what they hold is what the row before held. The rows
    share what comes back, so it must not be changed."""
    seen: dict[str, Any] = {}

    def decode(text: Any) -> Any:
        if not text:
            return {}
        found = seen.get(text)
        if found is None:
            found = make(_json(text))
            if len(seen) < DECODED_MAX:
                seen[text] = found
        return found

    return decode


def fancontrol_states(path: str | Path) -> list[dict[str, Any]]:
    """The FanControl states the memory kept, the oldest first: ``ts``, ``fingerprint`` and
    ``state``. Empty without a database."""
    try:
        connection = read_only(path)
        try:
            found = connection.execute(
                "SELECT ts, fingerprint, state FROM fancontrol ORDER BY ts"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    return [{"ts": ts, "fingerprint": mark, "state": _json(state)} for ts, mark, state in found]


def catalogue(path: str | Path) -> dict[str, Any]:
    """The names of the hardware and the sensors the memory noted (``meta`` ``sensors``):
    ``cpu`` and ``gpu`` as the readings name them, and ``sensors``, every temperature sensor
    with its id and label and which one the panel shows for the CPU and the GPU."""
    try:
        connection = read_only(path)
        try:
            found = connection.execute("SELECT value FROM meta WHERE key = 'sensors'").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return _json(found[0]) if found else {}


# -- statistics ---------------------------------------------------------------------------------


def weighted(pairs: list[tuple[float | None, float]], share: float = 0.5) -> float | None:
    """The quantile (the median by default) of [(value, seconds), ...], by time: the value
    below which ``share`` of the time lay."""
    kept = [(v, d) for v, d in pairs if v is not None and d > 0]
    if not kept:
        return None
    values = [v for v, _ in kept]
    weights = [d for _, d in kept]
    order = sorted(range(len(kept)), key=values.__getitem__)
    reached = list(accumulate(map(weights.__getitem__, order)))  # the time up to each value
    return values[order[min(bisect_left(reached, reached[-1] * share), len(order) - 1)]]


def correlation(pairs: list[tuple[float, float]]) -> float | None:
    """Pearson's correlation of two series: does one follow the other?"""
    n = len(pairs)
    if n < MIN_SAMPLES:
        return None
    mean_x = sum(a for a, _ in pairs) / n
    mean_y = sum(b for _, b in pairs) / n
    sxy = sum((a - mean_x) * (b - mean_y) for a, b in pairs)
    sxx = sum((a - mean_x) ** 2 for a, _ in pairs)
    syy = sum((b - mean_y) ** 2 for _, b in pairs)
    if sxx <= 1e-9 or syy <= 1e-9:
        return None
    return sxy / (sxx * syy) ** 0.5


def total(rows: list[Row]) -> float:
    return sum(r.duration for r in rows)


def rounds(rows: list[Row]) -> int:
    """How many separate games the rows hold (a gap longer than the heartbeat ends one)."""
    count, last = 0, None
    for r in rows:
        if not r.in_game:
            continue
        if last is None or r.ts - last > HEARTBEAT_S + 1:
            count += 1
        last = r.ts + r.duration
    return count


# -- curves against what happened ------------------------------------------------------------------

Data = list[tuple[Row, tuple[float | None, float | None]]]


def fan_names(rows: list[Row]) -> list[str]:
    """Every fan the rows know, in the order they first show up."""
    names: list[str] = []
    for r in rows:
        for name in r.fans:
            if name not in names:
                names.append(name)
    return names


def data_of(rows: list[Row], fan: str) -> Data:
    return [(r, r.fans[fan]) for r in rows if fan in r.fans]


def back_calculated(data: Data, curve: Curve) -> list[tuple[Row, float]]:
    """For each row the temperature the curve must have seen to command the duty the fan had."""
    found = []
    for row, (_rpm, duty) in data:
        if duty is None:
            continue
        temp = curve.temp_at(duty)
        if temp is not None:
            found.append((row, temp))
    return found


def candidates(rows: list[Row]) -> list[str]:
    """The sensors a curve might read, among those the memory keeps: the CPU, the GPU, its hot
    spot and the other temperatures it was asked to keep."""
    found = [name for name in TEMPS if any(r.temp(name) is not None for r in rows)]
    extra = sorted({key for r in rows for key in r.temps})
    return found + extra


def find_source(data: Data, curve: Curve, sensors: list[str]) -> dict[str, Any]:
    """Which recorded sensor does this curve really follow?

    Judged only once the back-calculated course has moved (``SPAN_NEEDED``): while nothing
    happens any sensor fits with a constant offset. The order is: first the one that lies on top
    of it, then the one that correlates best, last the one that scatters least. The verdict is
    ``too_few`` (not enough rows), ``too_quiet`` (nothing moved), ``follows`` or ``none``.
    """
    back = back_calculated(data, curve)
    if len(back) < MIN_SAMPLES:
        return {"verdict": "too_few", "samples": len(back)}
    span = (min(t for _, t in back), max(t for _, t in back))
    if span[1] - span[0] < SPAN_NEEDED:
        return {"verdict": "too_quiet", "span": span}
    found = []
    for sensor in sensors:
        pairs = [(t, row.temp(sensor)) for row, t in back if row.temp(sensor) is not None]
        if len(pairs) < MIN_SAMPLES:
            continue
        offset = weighted([(t - v, 1.0) for t, v in pairs])
        spread = weighted([(abs((t - v) - offset), 1.0) for t, v in pairs])
        found.append(
            {"sensor": sensor, "spread": spread, "offset": offset, "r": correlation(pairs)}
        )
    if not found:
        return {"verdict": "too_few", "span": span, "samples": len(back)}
    found.sort(key=lambda x: (abs(x["offset"]) > 2.0, -(x["r"] or 0.0), x["spread"]))
    best = found[0]
    follows = best["spread"] <= 2.0 and (best["r"] or 0) >= 0.9
    return {
        "verdict": "follows" if follows else "none",
        "span": span,
        "best": best,
        "others": found[1:],
        "source": curve.source,
    }


def deviation(data: Data, curve: Curve) -> tuple[float, float, float] | None:
    """(median, 95th percentile, seconds) of how far the duty is from what the curve commands
    for the temperature it read, where that was measured. Never zero: FanControl answers with
    hysteresis and a delay."""
    pairs = []
    for row, (_rpm, duty) in data:
        seen = row.sources.get(curve.name)
        if seen is None or duty is None:
            continue
        pairs.append((abs(duty - curve.duty_at(seen)), row.duration))
    median, high = weighted(pairs), weighted(pairs, 0.95)
    if len(pairs) < MIN_SAMPLES or median is None or high is None:
        return None
    return median, high, sum(seconds for _, seconds in pairs)

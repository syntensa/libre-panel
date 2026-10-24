"""What the memory keeps, for the app's History (views/history): the readings of a day, a week
or a month as a course, and the rounds of the games played in that time.

Only read, from the memory service's database (services/memory_store.py) beside its writer: WAL
lets both. The minutes are put together in steps that make a course of a few hundred points
(five minutes for a day, an hour for a week, four hours for a month); a step without a row is
time the PC was off, and the course has a gap there. Everything stays on this computer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from libre_panel.config import Config
from libre_panel.services.cooling import default_database
from libre_panel.services.memory_store import read_only

DAY_S = 86400
# how far back, and the step of the course
SPANS = {"day": (DAY_S, 300), "week": (7 * DAY_S, 3600), "month": (30 * DAY_S, 4 * 3600)}
SERIES = ("cpu_temp", "gpu_temp", "cpu_load", "gpu_load", "cpu_power", "gpu_power")
PEAKED = ("cpu_temp", "gpu_temp", "cpu_power", "gpu_power")  # with a highest value per minute
ROUNDS = 8  # the last rounds listed one by one
GAMES = 12  # the games listed, the most played first


def database(config: Config) -> Path:
    """Where the memory service keeps its file (its ``database`` option, else the default)."""
    given = config.services.options.get("memory", {}).get("database") or ""
    return Path(str(given)).expanduser() if given else default_database()


def _round(value: Any, digits: int = 1) -> float | None:
    return round(float(value), digits) if isinstance(value, int | float) else None


def _course(con: sqlite3.Connection, since: int, step: int) -> dict[str, list[Any]]:
    means = ", ".join(f"AVG({name})" for name in SERIES)
    peaks = ", ".join(f"MAX({name}_max)" for name in PEAKED)
    rows = con.execute(
        f"SELECT ts / {step} * {step} AS at, {means}, {peaks},"
        " SUM(CASE WHEN mode = 'game' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)"
        " FROM minutes WHERE ts >= ? GROUP BY at ORDER BY at",
        [since],
    ).fetchall()
    course: dict[str, list[Any]] = {"t": [row[0] for row in rows]}
    for i, name in enumerate(SERIES, start=1):
        course[name] = [_round(row[i]) for row in rows]
    for i, name in enumerate(PEAKED, start=1 + len(SERIES)):
        course[f"{name}_max"] = [_round(row[i]) for row in rows]
    course["game"] = [_round(row[-1], 2) for row in rows]
    return course


def _summary(con: sqlite3.Connection, since: int) -> dict[str, Any]:
    row = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN mode = 'game' THEN 1 ELSE 0 END),"
        " AVG(cpu_temp), MAX(cpu_temp_max), AVG(gpu_temp), MAX(gpu_temp_max),"
        " AVG(cpu_load), AVG(gpu_load), MAX(cpu_power_max), MAX(gpu_power_max),"
        " SUM(cpu_power), SUM(gpu_power)"
        " FROM minutes WHERE ts >= ?",
        [since],
    ).fetchone()
    names = (
        "cpu_temp", "cpu_temp_max", "gpu_temp", "gpu_temp_max", "cpu_load", "gpu_load",
        "cpu_power_max", "gpu_power_max", "cpu_watt_minutes", "gpu_watt_minutes",
    )  # fmt: skip
    return {
        "minutes": row[0] or 0,
        "game_minutes": row[1] or 0,
        **{name: _round(value) for name, value in zip(names, row[2:], strict=True)},
    }


def _rounds(con: sqlite3.Connection, since: int) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT game, started, duration_s, fps_mean, COALESCE(fps_low_mean, fps_p01),"
        " cpu_temp_max, gpu_temp_max FROM sessions"
        " WHERE started >= ? AND duration_s > 0 ORDER BY started DESC LIMIT ?",
        [since, ROUNDS],
    ).fetchall()
    keys = ("game", "started", "seconds", "fps", "fps_low", "cpu_temp_max", "gpu_temp_max")
    return [
        {key: value if key in ("game", "started", "seconds") else _round(value) for key, value in
         zip(keys, row, strict=True)}
        for row in rows
    ]  # fmt: skip


def _games(con: sqlite3.Connection, since: int) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT game, COUNT(*), SUM(duration_s),"
        " SUM(fps_mean * fps_s) / NULLIF(SUM(CASE WHEN fps_mean IS NULL THEN 0 ELSE fps_s END), 0),"
        " AVG(COALESCE(fps_low_mean, fps_p01)), MAX(cpu_temp_max), MAX(gpu_temp_max)"
        " FROM sessions WHERE started >= ? AND duration_s > 0 AND game IS NOT NULL"
        " GROUP BY game ORDER BY SUM(duration_s) DESC LIMIT ?",
        [since, GAMES],
    ).fetchall()
    keys = ("game", "rounds", "seconds", "fps", "fps_low", "cpu_temp_max", "gpu_temp_max")
    return [
        {key: value if key in ("game", "rounds", "seconds") else _round(value) for key, value in
         zip(keys, row, strict=True)}
        for row in rows
    ]  # fmt: skip


def energy(summary: dict[str, Any], options: dict[str, Any]) -> dict[str, Any] | None:
    """The energy the span's minutes add up to and what it cost, as the power service
    counts it ([services.power]: the CPU's and the GPU's watts a minute at a time, and
    ``rest_w`` for the rest of the PC, at ``price`` per kWh): ``{kwh, cost, price, currency,
    rest_w}``; None without a price (the cost is not switched on) or without minutes."""
    try:
        price = float(options.get("price", 0) or 0)
        rest = float(options.get("rest_w", 0) or 0)
    except (TypeError, ValueError):
        return None
    minutes = summary.get("minutes") or 0
    if price <= 0 or not minutes:
        return None
    watt_minutes = (summary.get("cpu_watt_minutes") or 0) + (summary.get("gpu_watt_minutes") or 0)
    kwh = (watt_minutes + rest * minutes) / 60 / 1000
    return {
        "kwh": round(kwh, 2),
        "cost": round(kwh * price, 2),
        "price": price,
        "currency": str(options.get("currency", "€") or "€"),
        "rest_w": rest,
    }


def history(path: Path, span: str, now: float) -> dict[str, Any]:
    """``{"span", "since", "step", "found", "course", "summary", "rounds", "games"}``: what the
    memory kept in the ``span`` ("day", "week", "month") up to ``now``; ``found`` is false
    where there is no database (yet), and the rest is then empty."""
    if span not in SPANS:
        raise ValueError("span is day, week or month")
    length, step = SPANS[span]
    since = int(now) - length
    answer: dict[str, Any] = {
        "span": span,
        "since": since,
        "step": step,
        "found": path.is_file(),
        "course": {"t": []},
        "summary": {"minutes": 0, "game_minutes": 0},
        "rounds": [],
        "games": [],
    }
    if not answer["found"]:
        return answer
    con = read_only(path)
    try:
        answer["course"] = _course(con, since, step)
        answer["summary"] = _summary(con, since)
        answer["rounds"] = _rounds(con, since)
        answer["games"] = _games(con, since)
    except sqlite3.Error as exc:
        # a database of another make, or one the writer is setting up: nothing to show yet
        answer["error"] = str(exc)
    finally:
        con.close()
    return answer

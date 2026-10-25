"""What the memory keeps, for the app's History (views/history): the readings of a day, a week
or a month as a course, and the rounds of the games played in that time, and the report of one
of them again (``round_report``), for a picture of it.

Only read, from the memory service's database (services/memory_store.py) beside its writer: WAL
lets both. The minutes are put together in steps that make a course of a few hundred points
(five minutes for a day, an hour for a week, four hours for a month); a step without a row is
time the PC was off, and the course has a gap there. Everything stays on this computer.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from libre_panel.config import Config
from libre_panel.services import round_report as report
from libre_panel.services.cooling import default_database
from libre_panel.services.memory_store import read_only

DAY_S = 86400
# how far back, and the step of the course
SPANS = {"day": (DAY_S, 300), "week": (7 * DAY_S, 3600), "month": (30 * DAY_S, 4 * 3600)}
SERIES = ("cpu_temp", "gpu_temp", "cpu_load", "gpu_load", "cpu_power", "gpu_power")
PEAKED = ("cpu_temp", "gpu_temp", "cpu_power", "gpu_power")  # with a highest value per minute
ROUNDS = 8  # the last rounds listed one by one
GAMES = 12  # the games listed, the most played first
NOT_IN_NAMES = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')  # what no file name may hold (Windows)


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
        " AVG(cpu_load), AVG(gpu_load), MAX(cpu_power_max), MAX(gpu_power_max)"
        " FROM minutes WHERE ts >= ?",
        [since],
    ).fetchone()
    names = (
        "cpu_temp", "cpu_temp_max", "gpu_temp", "gpu_temp_max", "cpu_load", "gpu_load",
        "cpu_power_max", "gpu_power_max",
    )  # fmt: skip
    return {
        "minutes": row[0] or 0,
        "game_minutes": row[1] or 0,
        **{name: _round(value) for name, value in zip(names, row[2:], strict=True)},
    }


def _rounds(con: sqlite3.Connection, since: int) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT game, started, duration_s, fps_mean, COALESCE(fps_low_mean, fps_p01),"
        " cpu_temp_max, gpu_temp_max, id FROM sessions"
        " WHERE started >= ? AND duration_s > 0 ORDER BY started DESC LIMIT ?",
        [since, ROUNDS],
    ).fetchall()
    keys = ("game", "started", "seconds", "fps", "fps_low", "cpu_temp_max", "gpu_temp_max", "id")
    return [
        {key: value if key in ("game", "started", "seconds", "id") else _round(value)
         for key, value in zip(keys, row, strict=True)}
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


def round_report(path: Path, ident: int, compare: int) -> tuple[dict[str, Any], bool] | None:
    """The report of the round ``ident`` (a ``rounds`` entry's ``id``) against the last
    ``compare`` rounds of its program before it, as the memory service built it when the round
    ended (services/round_report.py), and whether it is the last round kept (the game's logo
    the panel still has is its). None for a round that is not kept or had no frame rates."""
    if not path.is_file():
        return None
    con = read_only(path)
    try:
        found = report.build(con, ident, 0, compare)
        last = con.execute("SELECT id FROM sessions ORDER BY started DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return (found, last is not None and last[0] == ident) if found is not None else None


def picture_name(game: Any, ended: float) -> str:
    """A file name for the picture of a round: the game's name, without what a file name cannot
    hold, and when the round ended ("Starfall 2026-10-24 21-48.png")."""
    words = NOT_IN_NAMES.sub(" ", game if isinstance(game, str) else "").split()
    name = " ".join(words).strip(" .")[:60].strip()
    return f"{name or 'Libre Panel'} {time.strftime('%Y-%m-%d %H-%M', time.localtime(ended))}.png"


def free_name(folder: Path, name: str) -> Path:
    """``name`` in ``folder``, or "name (2).png" and so on where a file has it already."""
    path, n = folder / name, 2
    while path.exists():
        path, n = folder / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
    return path

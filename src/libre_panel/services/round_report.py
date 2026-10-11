"""The round report: a game just played, set against the last ones of the same program.

``build`` reads the memory's database; ``readings`` turns the report into plain readings
(``report.fps``, ``report.level.0``, ...) for a theme made of the usual widgets. A screen that
draws more reads the whole report from the state ``report`` (docs/PLUGINS.md has its shape).
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from libre_panel.i18n import t
from libre_panel.services.memory_store import SESSION_PEAKS

# What is averaged over the earlier games.
VALUES = (
    "fps_mean", "fps_p01", "fps_low_mean", "frametime_mean", *(f"{k}_max" for k in SESSION_PEAKS),
)  # fmt: skip
PEAK_UNITS = {
    "cpu_temp": "°C", "gpu_temp": "°C", "gpu_hotspot": "°C",
    "cpu_power": "W", "gpu_power": "W", "vram_used": "GB",
}  # fmt: skip


def build(
    con: sqlite3.Connection, session_id: int | None, min_s: float, compare: int
) -> dict[str, Any] | None:
    """The report of a game (``session_id``; None: the latest that was long enough) against
    the last ``compare`` games of the same program that were long enough too. None when the
    game was shorter than ``min_s`` seconds or had no frame rates. Reads the file: call it on
    the store's thread, after the game's row was written."""
    cursor = con.cursor()
    cursor.row_factory = sqlite3.Row
    if session_id is None:
        row = cursor.execute(
            "SELECT * FROM sessions WHERE closed = 1 AND duration_s >= ? AND fps_s > 0 "
            "ORDER BY started DESC LIMIT 1",
            (min_s,),
        ).fetchone()
    else:
        row = cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None or (row["duration_s"] or 0) < min_s or not row["fps_s"]:
        return None
    session = dict(row)
    session["levels"] = _levels(session["levels"])
    column, value = (
        ("program", session["program"]) if session["program"] else ("game", session["game"])
    )
    earlier = cursor.execute(
        f"SELECT * FROM sessions WHERE {column} = ? AND id <> ? AND started < ? AND closed = 1 "
        "AND duration_s >= ? AND fps_s > 0 ORDER BY started DESC LIMIT ?",
        (value, session["id"], session["started"], min_s, compare),
    ).fetchall()
    means = {}
    for name in VALUES:
        found = [r[name] for r in earlier if r[name] is not None]
        means[name] = sum(found) / len(found) if found else None
    shares = []
    for r in earlier:
        levels = _levels(r["levels"])
        total = sum(levels["seconds"])
        if levels["edges"] == session["levels"]["edges"] and total:
            shares.append([seconds / total for seconds in levels["seconds"]])
    average = [sum(share) / len(shares) for share in zip(*shares, strict=True)] if shares else None
    return {
        "session": session,
        "comparison": {"count": len(earlier), "mean": means, "levels": average},
    }


def _levels(text: Any) -> dict[str, list[float]]:
    try:
        found = json.loads(text or "{}")
    except (TypeError, ValueError):
        found = {}
    return {"edges": found.get("edges") or [], "seconds": found.get("seconds") or []}


def level_names(edges: list[float]) -> list[str]:
    """The frame-rate ranges the levels stand for: "< 60", "60–90", ..., "165+"."""
    if not edges:
        return []
    names = [f"< {edges[0]:g}"]
    names += [f"{a:g}–{b:g}" for a, b in zip(edges, edges[1:], strict=False)]
    return [*names, f"{edges[-1]:g}+"]


def readings(report: dict[str, Any]) -> dict[str, tuple[Any, str, str]]:
    """The report as plain readings: key -> (value, unit, label). The labels, and the texts
    that are values, are in the language of the panel. A value may be None (not known)."""
    session, comparison = report["session"], report["comparison"]
    means = comparison["mean"] if comparison["count"] else {}

    def against(key: str) -> float | None:
        value, mean = session.get(key), means.get(key)
        return None if value is None or mean is None else value - mean

    count = comparison["count"]
    if count > 1:
        versus = t("Compared with the last {n} rounds", n=count)
    elif count == 1:
        versus = t("Compared with the last round")
    else:
        versus = t("First round of this game")
    played = " – ".join(
        time.strftime("%H:%M", time.localtime(session[key])) for key in ("started", "ended")
    )
    out: dict[str, tuple[Any, str, str]] = {
        "report.title": (t("Round report"), "", t("Round report")),
        "report.game": (session["game"], "", t("Game")),
        "report.when": (played, "", t("Played")),
        "report.length": (session["duration_s"], "s", t("Length")),
        "report.outside": (session["outside_temp"], "°C", t("Outside")),
        "report.versus": (versus, "", t("Compared with")),
        "report.fps": (session["fps_mean"], "fps", t("Average FPS")),
        "report.fps_delta": (against("fps_mean"), "fps", t("Against the average")),
        "report.low": (session["fps_low_mean"], "fps", t("1% low")),
        "report.low_delta": (against("fps_low_mean"), "fps", t("Against the average")),
        "report.frametime": (session["frametime_mean"], "ms", t("Frame time")),
        "report.frametime_delta": (against("frametime_mean"), "ms", t("Against the average")),
        "report.count": (count, "", t("Earlier rounds")),
        "report.limit": (_held_back(session), "", t("Held back by")),
        "report.head.levels": (t("Share of the round at each frame rate"), "", ""),
        "report.head.peaks": (t("Highest values of the round"), "", ""),
    }  # fmt: skip
    held = {name: session.get(f"limit_{name}_s") or 0 for name in ("gpu", "cpu", "cap")}
    total_held = sum(held.values())
    for name, seconds in held.items():  # each one's share, for a bar in up to three colours
        share = 100 * seconds / total_held if total_held else 0.0
        out[f"report.limit.{name}"] = (share if share >= 0.5 else None, "%", name.upper())
    labels = {
        "cpu_temp": t("CPU temp"), "gpu_temp": t("GPU temp"), "gpu_hotspot": t("GPU hot spot"),
        "cpu_power": t("CPU power"), "gpu_power": t("GPU power"), "vram_used": t("Video memory"),
    }  # fmt: skip
    for name in SESSION_PEAKS:
        unit = PEAK_UNITS[name]
        out[f"report.{name}"] = (session.get(f"{name}_max"), unit, labels[name])
        usual = means.get(f"{name}_max")
        out[f"report.{name}_mean"] = (usual, unit, t("Average of the earlier rounds"))
        out[f"report.{name}_delta"] = (against(f"{name}_max"), unit, t("Against the average"))
    edges, seconds = session["levels"]["edges"], session["levels"]["seconds"]
    total = sum(seconds)
    earlier = comparison["levels"] if count else None
    for i, name in enumerate(level_names(edges)):
        share = 100 * seconds[i] / total if total and i < len(seconds) else None
        out[f"report.level.{i}"] = (share, "%", name)
        before = 100 * earlier[i] if earlier and i < len(earlier) else None
        out[f"report.level.{i}.mean"] = (before, "%", name)
    return out


def _held_back(session: dict[str, Any]) -> str | None:
    """Who held the frame rate down, by share of the time: "GPU 94 %  CPU 6 %"."""
    held = sorted(
        ((session.get(f"limit_{name}_s") or 0, name) for name in ("gpu", "cpu", "cap")),
        reverse=True,
    )
    total = sum(seconds for seconds, _ in held)
    parts = [
        f"{name.upper()} {100 * s / total:.0f} %"
        for s, name in held
        if total and s / total >= 0.005
    ]
    return "  ".join(parts) or None

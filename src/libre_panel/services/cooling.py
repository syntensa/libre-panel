"""Cooling analysis: the fan curves checked against what the PC really did, and what to do.

The data is the memory's ``cooling`` table (opened read only: the ``memory`` service writes
it, with ``cooling`` on) and FanControl's own configuration (read only too). The service does
nothing on its own: an editor page or a script asks it, ``analysis()`` for everything a page
draws (and the same as a plain-text report), ``recommendations()`` for what to change.
Nothing here changes FanControl; the user decides.

Everything is weighed by the time a row holds, not by rows; the recommendations look only at
the data since FanControl was last changed and name the data they rest on; what a curve really
reads is found from the duty the fan had (the reverse question). Device names come from the
readings, limits from the options.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import i18n
from libre_panel.config import config_dir
from libre_panel.i18n import t
from libre_panel.plugins import Service
from libre_panel.services import cooling_advice as advice
from libre_panel.services import cooling_data as data
from libre_panel.services import cooling_text as text
from libre_panel.services import fancontrol
from libre_panel.services.cooling_advice import Limit, Series
from libre_panel.services.cooling_data import DEVICE_SENSOR, Row, weighted
from libre_panel.services.fancontrol import Curve, Fan
from libre_panel.services.memory_store import DATABASE, last_fingerprint

log = logging.getLogger(__name__)

CACHE_S = 300.0  # an answer holds this long, and until FanControl's settings change
CACHE_ENTRIES = 8  # how many answers (periods, modes) are kept at a time
BAD_DEVIATION = 8.0  # points: the 95th percentile of the duty's distance from its curve
Setup = tuple[dict[str, Curve], dict[str, Fan]]


def default_database() -> Path:
    """Where the ``memory`` service keeps its file unless told otherwise."""
    return config_dir() / "plugins-data" / "memory" / DATABASE


def _share_over(pairs: list[tuple[float, float]], limit: float) -> float:
    held = sum(d for _, d in pairs)
    return sum(d for gap, d in pairs if gap > limit) / held if held else 0.0


def _reference(curves: dict[str, Curve], series: Series) -> tuple[Curve | None, str | None]:
    """The curve a series is drawn against: its own, or for a mix the first part that reads a
    CPU or a GPU (the CPU's first)."""
    curve = curves.get(series.curve)
    if curve is None or series.device != "mix":
        return curve, series.curve
    parts = [curves[p] for p in curve.mix if p in curves]
    for kind in ("cpu", "gpu"):
        found = next((p for p in parts if p.device == kind), None)
        if found is not None:
            return found, found.name
    return (parts[0], parts[0].name) if parts else (None, None)


def analysis(
    rows: list[Row],
    curves: dict[str, Curve],
    fans: dict[str, Fan],
    names: dict[str, Any],
    recommended: dict[str, Any],
    points: int = 480,
) -> dict[str, Any]:
    """Everything an editor page draws, as plain data (see docs/PLUGINS.md for the shape)."""
    seconds = data.total(rows)
    start, end = rows[0].ts, rows[-1].ts + rows[-1].duration
    states: dict[str, float] = {}
    games: list[dict[str, Any]] = []
    for r in rows:
        name = r.game or (t("Game (unnamed)") if r.in_game else t("Desktop"))
        states[name] = states.get(name, 0.0) + r.duration
        if not r.in_game:
            continue
        last = games[-1] if games else None
        if last and last["name"] == name and r.ts - last["to"] <= data.HEARTBEAT_S + 1:
            last["to"] = r.ts + r.duration
        else:
            games.append({"name": name, "from": r.ts, "to": r.ts + r.duration})
    series = advice.series_of(rows, fans, curves)
    temps = data.candidates(rows)
    findings: list[dict[str, Any]] = []
    plots = [_plot(rows, series_, curves, fans, names, findings) for series_ in series]
    findings += _mix_findings(rows, series, curves)
    if seconds < text.ENOUGH_S:
        findings.append(
            {
                "level": "info",
                "text": t(
                    "Unused curve points and oscillation are judged from {needed} of "
                    "recording: so far {have}.",
                    needed=text.duration(text.ENOUGH_S), have=text.duration(seconds),
                ),
            }
        )  # fmt: skip
    covered = {e["id"] for e in recommended.get("list", [])}
    findings = [f for f in findings if f.get("id") not in covered]
    for plot in plots:  # a proposal is drawn only where its axis is the proposal's input
        proposal = (recommended.get("proposals") or {}).get(plot["curve"])
        plot["proposal"] = (
            proposal if proposal and not plot["measured"] and plot["device"] != "mix" else None
        )
    return {
        "empty": False,
        "from": start,
        "to": end,
        "seconds": round(seconds),
        "rows": len(rows),
        "advice": recommended,
        "states": [
            {"name": n, "seconds": round(s)}
            for n, s in sorted(states.items(), key=lambda x: (x[0] != t("Desktop"), -x[1]))
        ],
        "sessions": games,
        "series": [
            {"curve": s.curve, "name": s.name, "fans": s.fans, "device": s.device} for s in series
        ],
        "history": _history(rows, series, temps, points, start, end),
        "plots": plots,
        "table": [_fan_row(rows, name, fans) for name in data.fan_names(rows)],
        "findings": findings,
    }


def _history(
    rows: list[Row], series: list[Series], temps: list[str], points: int, start: float, end: float
) -> dict[str, Any]:
    """The course in ``points`` bins, each the mean over the time it covers; a bin that is
    less than a third covered stays empty (a gap stays a gap)."""
    n = max(10, int(points))
    width = max(1.0, (end - start) / n)
    fields = len(temps) + len(series)
    sums = [[0.0] * n for _ in range(fields)]
    weights = [[0.0] * n for _ in range(fields)]
    covered = [0.0] * n
    for r in rows:
        values = [r.temp(sensor) for sensor in temps] + [r.duty(s.fans[0]) for s in series]
        a, e = r.ts, r.ts + r.duration
        i = int((a - start) // width)
        while i < n and start + i * width < e:
            share = min(e, start + (i + 1) * width) - max(a, start + i * width)
            if share > 0:
                covered[i] += share
                for k, value in enumerate(values):
                    if value is not None:
                        sums[k][i] += value * share
                        weights[k][i] += share
            i += 1

    def course(k: int) -> list[float | None]:
        return [
            round(sums[k][i] / weights[k][i], 1)
            if weights[k][i] > 0 and covered[i] >= width / 3
            else None
            for i in range(n)
        ]

    return {
        "t": [round(start + (i + 0.5) * width) for i in range(n)],
        "temps": {sensor: course(k) for k, sensor in enumerate(temps)},
        "duty": {s.curve: course(len(temps) + k) for k, s in enumerate(series)},
    }


def _plot(
    rows: list[Row],
    series: Series,
    curves: dict[str, Curve],
    fans: dict[str, Fan],
    names: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """One curve against the duty its fans had, and what that says: the cloud of (input, duty,
    seconds), the curve, and a finding if the fans do not follow it."""
    curve = curves.get(series.curve)
    fan = series.fans[0]
    kind = series.device
    ref_curve, ref_name = _reference(curves, series)
    ref_kind = ref_curve.device if ref_curve is not None else kind
    sensor = DEVICE_SENSOR.get(ref_kind)
    note = None
    if kind == "mix" and curve is not None:
        note = t(
            "Mix curve of {parts}: the fan follows what they give together. "
            "The line shows only the part {part}.",
            parts=t(" and ").join(curve.mix), part=ref_name or "?",
        )  # fmt: skip
    cloud: dict[tuple[float, float], float] = {}
    measured = 0
    for r in rows:
        duty = r.duty(fan)
        if duty is None:
            continue
        x = r.sources.get(ref_name) if ref_name else None
        if x is not None:
            measured += 1
        elif sensor:
            x = r.temp(sensor)
        if x is not None:
            key = (round(x * 2) / 2, round(duty * 2) / 2)
            cloud[key] = cloud.get(key, 0.0) + r.duration
    real_input = measured > 0.5 * len(rows)
    plot = {
        "curve": series.curve,
        "name": series.name,
        "device": kind,
        "target": [list(p) for p in ref_curve.points] if ref_curve is not None else [],
        "x_name": t("Curve input (measured)") if real_input else text.device_name(ref_kind, names),
        "measured": real_input,
        "cloud": sorted(([x, y, round(d)] for (x, y), d in cloud.items()), key=lambda p: -p[2])[
            :800
        ],
        "note": note,
        "proposal": None,
    }
    if kind == "mix" or curve is None or not curve.points:
        return plot
    pairs = data.data_of(rows, fan)
    found = data.deviation(pairs, curve) if real_input else None
    if found:
        ok = found[1] <= BAD_DEVIATION
        findings.append(
            {
                "level": "ok" if ok else "warn",
                "text": t(
                    "{fans} follows the curve {curve}: {median} points off on average, p95 {high}: "
                    "that is hysteresis and response time of FanControl.",
                    fans=series.name, curve=curve.name, median=text.num(found[0], 1),
                    high=text.num(found[1], 1),
                )
                if ok
                else t(
                    "{fans} deviates from the curve {curve}: p95 {high} points.",
                    fans=series.name, curve=curve.name, high=text.num(found[1], 1),
                ),
            }
        )  # fmt: skip
    elif not real_input and sensor:
        _source_finding(rows, series, curve, sensor, ref_kind, names, plot, findings)
    return plot


def _source_finding(
    rows: list[Row],
    series: Series,
    curve: Curve,
    sensor: str,
    kind: str,
    names: dict[str, Any],
    plot: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    """The input is not measured: work out what the curve must have seen and compare it with
    the sensor the curve should hang on."""
    pairs = data.data_of(rows, series.fans[0])
    back = [
        (r, calc, r.temp(sensor), r.duration)
        for r, calc in data.back_calculated(pairs, curve)
        if r.temp(sensor) is not None
    ]
    if len(back) < data.MIN_SAMPLES:
        return
    seen = weighted([(calc, d) for _, calc, _, d in back])
    real = weighted([(x, d) for _, _, x, d in back])
    offset = weighted([(calc - x, d) for _, calc, x, d in back]) or 0.0
    where = text.device_name(kind, names)
    if abs(offset) <= 2:
        findings.append(
            {
                "level": "ok",
                "text": t(
                    "{fans} follows the curve {curve} at the {where} (worked back {offset} K off).",
                    fans=series.name, curve=curve.name, where=where,
                    offset=text.num(offset, 1, True),
                ),
            }
        )  # fmt: skip
    if abs(offset) > advice.OFFSET_WRONG:
        findings.append(
            {
                "level": "warn",
                "id": "source:" + curve.name,
                "text": t(
                    "{fans} is not tied to the {where}: the curve {curve} sees {seen} °C on "
                    "average, the {where} was at {real} °C. FanControl reads {source} for this "
                    "curve, which the panel does not know. The points therefore lie beside the "
                    "line.",
                    fans=series.name, where=where, curve=curve.name, seen=text.num(seen or 0),
                    real=text.num(real or 0), source=curve.source or "?",
                ),
            }
        )  # fmt: skip
        plot["note"] = t(
            "Drawn over the {where}, but the curve reads {source}.",
            where=where,
            source=curve.source or "?",
        )


def _mix_findings(
    rows: list[Row], series: list[Series], curves: dict[str, Curve]
) -> list[dict[str, Any]]:
    """Does a mix take the higher of its parts? Measured from the duty of fans on each."""
    found = []
    for s in series:
        if s.device != "mix":
            continue
        for part in curves[s.curve].mix:
            direct = next((x for x in series if x.curve == part), None)
            if direct is None:
                continue
            gaps = []
            for r in rows:
                mixed, own = r.duty(s.fans[0]), r.duty(direct.fans[0])
                if mixed is not None and own is not None:
                    gaps.append((own - mixed, r.duration, own, mixed))
            if sum(g[1] for g in gaps) < advice.MIN_MIX_S:
                continue
            share = _share_over([(g[0], g[1]) for g in gaps], advice.JUMP)
            if share >= 0.5:
                found.append(
                    {
                        "level": "warn",
                        "id": "mix:" + s.curve,
                        "text": t(
                            "{fans} ({curve}) does not follow the higher part: {part} was more "
                            "than {jump} points above for {share} % of the time ({own} % against "
                            "{mixed} % on average). The mix does not pass the value of {part} "
                            "on: if it should take the maximum, the mix function in FanControl "
                            "is wrong.",
                            fans=s.name, curve=s.curve, part=part, jump=text.num(advice.JUMP),
                            share=text.num(100 * share),
                            own=text.num(weighted([(g[2], g[1]) for g in gaps]) or 0),
                            mixed=text.num(weighted([(g[3], g[1]) for g in gaps]) or 0),
                        ),
                    }
                )  # fmt: skip
    return found


def _fan_row(rows: list[Row], name: str, fans: dict[str, Fan]) -> dict[str, Any]:
    """The numbers of one fan: duty and speed (median, 95th percentile, highest), how much of
    the time at its minimum, and the speed at each duty."""
    fan = fans.get(name) or fans.get(name.replace(" ", "_"))
    pairs = data.data_of(rows, name)
    duty = [(d, r.duration) for r, (_rpm, d) in pairs if d is not None]
    speed = [(rpm, r.duration) for r, (rpm, _d) in pairs if rpm is not None]
    held = sum(r.duration for r, _ in pairs) or 1.0
    minimum = (fan.minimum if fan else None) or 20
    steps: dict[int, list[tuple[float, float]]] = {}
    for r, (rpm, d) in pairs:
        if rpm is not None and d is not None:
            steps.setdefault(int(d // 10) * 10, []).append((rpm, r.duration))
    return {
        "fan": name,
        "curve": fan.curve if fan else None,
        "p50": weighted(duty), "p95": weighted(duty, 0.95),
        "max": max((d for d, _ in duty), default=None),
        "minimum_share": round(100 * sum(s for d, s in duty if d <= minimum + 2) / held, 1)
        if duty
        else None,
        "rpm50": weighted(speed), "rpm_max": max((s for s, _ in speed), default=None),
        "characteristic": [
            [step, round(weighted(v) or 0)] for step, v in sorted(steps.items()) if weighted(v)
        ],
    }  # fmt: skip


# -- the analyst: the work, and what it keeps -----------------------------------------------------


class Analyst:
    """Reads the file and FanControl, and keeps what it worked out: each answer for ``CACHE_S``
    seconds, and until FanControl's settings change (a new state in the file, or the
    configuration itself), per language and per question (period, mode)."""

    def __init__(
        self,
        database: Callable[[], Path],
        setup: Callable[[], Setup],
        limits: tuple[Limit, ...] = advice.DEFAULT_LIMITS,
        stamp: Callable[[], Any] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database, self.setup, self.limits = database, setup, limits
        self.stamp, self.clock = stamp, clock
        self._lock = threading.Lock()
        self._kept: dict[tuple[Any, ...], tuple[float, Any]] = {}

    def _names(self) -> dict[str, Any]:
        return data.catalogue(self.database())

    def _key(self, question: tuple[Any, ...]) -> tuple[Any, ...]:
        path = self.database()
        return (question, str(path), last_fingerprint(path), self.stamp(), i18n.language())

    def _recall(self, key: tuple[Any, ...]) -> Any:
        with self._lock:
            kept = self._kept.get(key)
            if kept is not None and self.clock() - kept[0] < CACHE_S:
                return kept[1]
        return None

    def _keep(self, key: tuple[Any, ...], value: Any) -> Any:
        with self._lock:
            self._kept.pop(key, None)
            self._kept[key] = (self.clock(), value)
            while len(self._kept) > CACHE_ENTRIES:
                del self._kept[next(iter(self._kept))]  # the oldest goes
        return value

    def recommendations(self) -> dict[str, Any]:
        """What to change, from the data since FanControl was last changed (see ``advise``)."""
        path = self.database()
        if not os.path.exists(path):
            return {"empty": True, "reason": t("no database")}
        key = self._key(("recommendations",))
        found = self._recall(key)
        if found is not None:
            return found
        try:
            rows = data.load_rows(path, advice.LOOKBACK_DAYS)
        except sqlite3.Error:
            return {"empty": True, "reason": t("no cooling data yet")}
        curves, fans = self.setup()
        states = data.fancontrol_states(path)
        value = advice.advise(rows, states, curves, fans, self._names(), self.limits)
        return self._keep(key, value)

    def _recommended(self) -> dict[str, Any]:
        try:
            return self.recommendations()
        except Exception as exc:  # what is asked for must not fail for that
            log.warning("cooling: recommendations failed: %s: %s", type(exc).__name__, exc)
            return {"empty": True, "error": f"{type(exc).__name__}: {exc}"}

    def analysis(
        self, days: float | None = 1.0, mode: str | None = None, points: int = 480
    ) -> dict[str, Any]:
        path = self.database()
        if not os.path.exists(path):
            return {"empty": True, "reason": t("no database")}
        key = self._key(("analysis", days, mode, points))
        found = self._recall(key)
        if found is not None:
            return found
        try:
            rows = data.load_rows(path, days, mode)
        except sqlite3.Error:
            rows = []
        if not rows:
            return {"empty": True, "reason": t("no cooling data in the chosen period yet")}
        recommended = self._recommended()
        curves, fans = self.setup()
        names = self._names()
        result = analysis(rows, curves, fans, names, recommended, points)
        result["text"] = text.report(rows, fans, curves, recommended, names)
        return self._keep(key, result)

    def report(self, days: float | None = None, mode: str | None = None) -> list[str]:
        """The plain-text report: what was recorded, each fan, what is notable, the trend and
        the recommendations, as lines in the language of the panel."""
        path = self.database()
        if not os.path.exists(path):
            return [t("No database: {path}", path=str(path))]
        key = self._key(("report", days, mode))
        found = self._recall(key)
        if found is not None:
            return list(found)
        try:
            rows = data.load_rows(path, days, mode)
        except sqlite3.Error:
            rows = []
        if not rows:
            return [t("No cooling data yet. The memory service writes it while `cooling` is on.")]
        curves, fans = self.setup()
        lines = text.report(rows, fans, curves, self._recommended(), self._names())
        return list(self._keep(key, lines))


# -- the service ----------------------------------------------------------------------------------


class Cooling(Service):
    """Checks the fan curves against what the PC did, for an editor page or a script.

    An editor page gets it with ``context.service("cooling")``. ``analysis(days=1.0,
    mode=None, points=480)`` returns everything a page draws as data, and ``text``, the
    plain-text report in the language of the panel; ``recommendations()`` what to change;
    ``report(days=None, mode=None)`` only the text. ``mode`` is ``"game"`` or ``"desktop"``
    (None: both). The work is done when asked and kept for 5 minutes, and until FanControl's
    settings change. The shapes are in docs/PLUGINS.md.
    """

    name = "cooling"
    api = 1
    options = {
        "limits": ("list", []),
        "fancontrol": ("string", ""),
        "database": ("string", ""),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(host, options)
        self.limits = advice.parse_limits(
            options["limits"], lambda message: host.log.warning("%s", message)
        )
        self.watch = fancontrol.Watch(options["fancontrol"])
        given = options["database"]
        self.database = Path(given).expanduser() if given else default_database()
        self.setup_cache: tuple[Any, Setup] | None = None
        self.analyst = Analyst(
            lambda: self.database, self.read_setup, self.limits, self.watch.stamp, clock
        )

    def read_setup(self) -> Setup:
        """The curves and the fans in FanControl's configuration now (empty without one). Read
        again only when the file changed."""
        stamp = self.watch.stamp()
        cached = self.setup_cache
        if cached is not None and cached[0] == stamp:
            return cached[1]
        section = fancontrol.read_section(self.watch.path) if stamp and self.watch.path else None
        found = fancontrol.curves_and_fans(fancontrol.state_of(section)) if section else ({}, {})
        self.setup_cache = (stamp, found)
        return found

    def start(self) -> None:
        self.host.log.info("cooling analysis: reads %s", self.database)

    def recommendations(self) -> dict[str, Any]:
        return self.analyst.recommendations()

    def analysis(
        self, days: float | None = 1.0, mode: str | None = None, points: int = 480
    ) -> dict[str, Any]:
        return self.analyst.analysis(days, mode, points)

    def report(self, days: float | None = None, mode: str | None = None) -> list[str]:
        return self.analyst.report(days, mode)

    def status(self) -> dict[str, Any]:
        """What the service reads, for a console page."""
        curves, fans = self.read_setup()
        return {
            "database": str(self.database),
            "database_found": self.database.is_file(),
            "fancontrol": str(self.watch.path) if self.watch.path else None,
            "curves": len(curves),
            "fans": len(fans),
        }

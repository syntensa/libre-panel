"""What to do about the cooling, from what was measured: the recommendations.

Each rule looks at the data since the last change in FanControl (what was fixed leaves the
list at once) and names the data it rests on. Nothing here changes FanControl: the user
decides. A recommendation is a dict: ``id`` (``source:<curve>``, ``mix:<curve>``,
``warm:<sensor>``, ``oscillation:<curve>``, ``quieter:<curve>`` or ``trend``), ``curve``,
``proposal`` (new curve points, or None), ``title``, ``why``, ``do`` (steps), ``effect``
(lines), ``note``, ``confidence``, ``basis`` and its ``number``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from libre_panel.i18n import t
from libre_panel.services import cooling_data as data
from libre_panel.services import fancontrol
from libre_panel.services.cooling_data import (
    DEVICE_SENSOR,
    MIN_SAMPLES,
    Row,
    back_calculated,
    data_of,
    total,
    weighted,
)
from libre_panel.services.cooling_text import (
    basis,
    change_lines,
    characteristic_of,
    compare,
    duration,
    num,
    percent_rpm,
    points_text,
    rpm_at,
    sensor_name,
)
from libre_panel.services.fancontrol import Curve, Fan

MIN_DESKTOP_S = 30 * 60  # this much desktop for a statement about the desktop
MIN_GAME_S = 20 * 60  # ... and about a game
RELIABLE_GAME_S = 3 * 3600  # from here "reliable" instead of "preliminary" ...
RELIABLE_ROUNDS = 3  # ... and from this many rounds
JUMP = 5.0  # points: from here a step in the duty is heard
OSCILLATION_PER_H = 20  # this many jumps an hour are oscillation
OSCILLATION_MIN_S = 6 * 3600
QUIETER_MIN_RPM = 60  # below that a change of the curve is not worth it
TREND_DAYS = 14
TREND_K = 3.0  # K warmer than at the start, at the same load and outside temperature
LOOKBACK_DAYS = 60  # older rows are not read for the recommendations
MIN_MIX_S = 600
OFFSET_WRONG = 8.0  # K: a curve whose sensor is this far from its device's follows another one
WEIGHT = {"source": 0, "warm": 1, "mix": 2, "oscillation": 3, "quieter": 4, "trend": 5}


@dataclass(frozen=True)
class Limit:
    """A temperature that is too warm in a game when it stays above ``max`` for 5 % of the
    time. ``sensor`` is a column of the memory (``cpu_temp``, ``gpu_temp``, ``gpu_hotspot``) or
    the key of a reading it keeps."""

    sensor: str
    max: float
    name: str = ""


# Only a starting point: what is too warm depends on the hardware, so the options say.
DEFAULT_LIMITS = (Limit("cpu_temp", 90.0), Limit("gpu_temp", 85.0), Limit("gpu_hotspot", 100.0))
SENSOR_OF = {"cpu.temp": "cpu_temp", "gpu.temp": "gpu_temp", "gpu.hotspot": "gpu_hotspot"}


def parse_limits(raw: list[Any], report: Callable[[str], None]) -> tuple[Limit, ...]:
    """The ``limits`` option: ``{ key = "cpu.temp", max = 90 }`` each (``gpu.hotspot`` and the
    keys of the readings the memory keeps are known too). None given: the defaults."""
    found = []
    for n, entry in enumerate(raw, 1):
        key = entry.get("key") if isinstance(entry, dict) else None
        limit = entry.get("max") if isinstance(entry, dict) else None
        if (
            not isinstance(key, str)
            or not key
            or isinstance(limit, bool)
            or not isinstance(limit, int | float)
        ):
            report(
                f"limits[{n}]: needs a reading and a maximum, e.g. {{ key = 'cpu.temp', max = 90 }}"
            )
            continue
        name = entry.get("name")
        found.append(
            Limit(SENSOR_OF.get(key, key), float(limit), name if isinstance(name, str) else "")
        )
    return tuple(found) or DEFAULT_LIMITS


@dataclass
class Series:
    """The fans that one curve drives: one line in a chart."""

    curve: str
    fans: list[str]
    device: str

    @property
    def name(self) -> str:
        return " + ".join(self.fans)


def series_of(rows: list[Row], fans: dict[str, Fan], curves: dict[str, Curve]) -> list[Series]:
    """The fans the rows know, grouped by the curve that drives them (cpu, gpu, mix, other)."""
    groups: dict[str, list[str]] = {}
    for name in data.fan_names(rows):
        fan = fans.get(name) or fans.get(name.replace(" ", "_"))
        if fan:
            groups.setdefault(fan.curve, []).append(name)
    order = {"cpu": 0, "gpu": 1, "mix": 2, "other": 3}
    found = [
        Series(curve, names, curves[curve].device if curve in curves else "other")
        for curve, names in groups.items()
    ]
    return sorted(found, key=lambda s: order[s.device])


def median(pairs: list[tuple[float | None, float]], share: float = 0.5) -> float | None:
    return weighted([(v, d) for v, d in pairs if v is not None], share)


def duties(rows: list[Row], fan: str) -> list[tuple[float | None, float]]:
    return [(r.duty(fan), r.duration) for r in rows]


class Situation:
    """Everything the rules need, worked out once."""

    def __init__(
        self,
        rows: list[Row],
        curves: dict[str, Curve],
        fans: dict[str, Fan],
        names: dict[str, Any],
        limits: tuple[Limit, ...],
    ) -> None:
        self.rows, self.curves, self.fans, self.names, self.limits = (
            rows,
            curves,
            fans,
            names,
            limits,
        )
        self.desk = [r for r in rows if not r.in_game]
        self.game = [r for r in rows if r.in_game]
        self.series = series_of(rows, fans, curves)
        self.found: list[dict[str, Any]] = []
        self.checked: list[str] = []
        self.proposals: dict[str, dict[str, Any]] = {}  # curve -> {"points": ..., "sensor": ...}

    def confidence(self) -> str:
        reliable = total(self.game) >= RELIABLE_GAME_S and data.rounds(self.game) >= RELIABLE_ROUNDS
        return t("reliable") if reliable else t("preliminary")

    def output(self, row: Row, name: str, with_proposals: bool = True) -> float | None:
        """What the curve ``name`` commands in this row (percent), as far as that is known: a
        mix by its parts' highest, a proposed curve by the sensor it was proposed for, a
        measured input by the curve, else the duty of a fan on it, else the device's sensor."""
        curve = self.curves.get(name)
        if curve is not None and curve.mix:
            parts = [self.output(row, part, with_proposals) for part in curve.mix]
            found = [p for p in parts if p is not None]
            return max(found) if found else None  # as a mix is meant to work
        if with_proposals and name in self.proposals:
            proposal = self.proposals[name]
            x = row.temp(proposal["sensor"])
            return (
                None if x is None else Curve(name, tuple(map(tuple, proposal["points"]))).duty_at(x)
            )
        if curve is not None and name in row.sources:
            return curve.duty_at(row.sources[name])
        direct = next((s for s in self.series if s.curve == name), None)
        if direct is not None:
            return row.duty(direct.fans[0])
        sensor = DEVICE_SENSOR.get(curve.device) if curve is not None else None
        x = row.temp(sensor) if sensor else None
        return None if x is None or curve is None else curve.duty_at(x)

    def panel_sensor(self, kind: str) -> dict[str, Any] | None:
        """The catalogue entry of the sensor the panel shows for the CPU or the GPU."""
        wanted = DEVICE_SENSOR.get(kind)
        found = next(
            (
                e
                for e in self.names.get("sensors") or []
                if isinstance(e, dict) and e.get("panel") == wanted
            ),
            None,
        )
        return found


def propose_curve(
    curve: Curve,
    desk: list[tuple[float | None, float]],
    game: list[tuple[float | None, float]],
    game_duty: list[tuple[float | None, float]],
) -> list[list[int]] | None:
    """Curve points for a new input, from what was measured. Careful: on the desktop the fan
    stays at the base value (the first point lies 3 K above what the input reached 95 % of the
    time there); in a game it gets the cooling it had (the second point at the input's 90 %
    value of the game with the duty it ran at); the upper points of the old curve stay."""
    old = curve.points
    base = old[0][1] if old else 20.0
    quiet = median(desk, 0.95)
    if quiet is None:
        return None
    new = [(math.ceil(quiet + 3), base)]
    during, duty = median(game, 0.9), median(game_duty)
    if sum(d for _, d in game) >= MIN_GAME_S and during is not None and duty is not None:
        temp, level = round(during), round(duty)
        if temp > new[-1][0] + 3 and level > base + 2:
            new.append((temp, level))
    new += [(temp, level) for temp, level in old if temp > new[-1][0] + 2 and level > new[-1][1]]
    return [[int(temp), int(round(level))] for temp, level in new]


def target_name(curves: dict[str, Curve], part: str) -> str:
    kind = curves[part].device if part in curves else "other"
    if kind == "gpu":
        return t("the graphics card")
    return t("the CPU") if kind == "cpu" else part


# -- the rules ----------------------------------------------------------------------------------


def rule_source(s: Situation) -> None:
    """Does a curve read another sensor than the device it cools? What it reads is known where
    the sensor can be read (it was kept), else it is worked out from the duty the fan had."""
    for series in s.series:
        curve = s.curves.get(series.curve)
        if series.device not in DEVICE_SENSOR or curve is None or not curve.points:
            continue
        if total(s.desk) < MIN_DESKTOP_S:
            continue
        sensor, fan = DEVICE_SENSOR[series.device], series.fans[0]
        pairs = data_of(s.desk, fan)
        measured = [
            (r.sources[curve.name], r.temp(sensor), r.duration)
            for r, _ in pairs
            if curve.name in r.sources and r.temp(sensor) is not None
        ]
        if len(measured) >= MIN_SAMPLES:
            seen_pairs = measured
        else:
            seen_pairs = [
                (calc, r.temp(sensor), r.duration)
                for r, calc in back_calculated(pairs, curve)
                if r.temp(sensor) is not None
            ]
        if len(seen_pairs) < MIN_SAMPLES:
            continue
        offset = weighted([(a - b, d) for a, b, d in seen_pairs]) or 0.0
        seen = weighted([(a, d) for a, _, d in seen_pairs]) or 0.0
        real = weighted([(b, d) for _, b, d in seen_pairs]) or 0.0
        if abs(offset) <= OFFSET_WRONG:
            s.checked.append(
                t(
                    "{curve} follows the right device ({offset} K).",
                    curve=curve.name,
                    offset=num(offset, 0, True),
                )
            )
            continue
        _add_source(s, series, curve, sensor, seen, real)


def _add_source(
    s: Situation, series: Series, curve: Curve, sensor: str, seen: float, real: float
) -> None:
    fan = series.fans[0]
    target = s.panel_sensor(series.device)
    characteristic = characteristic_of(s.rows, fan)
    desk_x = [(r.temp(sensor), r.duration) for r in s.desk]
    game_x = [(r.temp(sensor), r.duration) for r in s.game]
    game_duty = [p for p in duties(s.game, fan) if p[0] is not None]
    points = propose_curve(curve, desk_x, game_x, game_duty)
    if points:
        s.proposals[curve.name] = {"points": points, "sensor": sensor}
    now = median(duties(s.desk, fan))
    proposed = Curve(curve.name, tuple(map(tuple, points or [])))
    new = median([(proposed.duty_at(x), d) for x, d in desk_x if x is not None]) if points else None
    effect = (
        [compare(t("Desktop"), new, now, characteristic)]
        if new is not None and now is not None
        else []
    )
    if total(s.game) >= MIN_GAME_S and points:
        before = median(duties(s.game, fan))
        after = median([(proposed.duty_at(x), d) for x, d in game_x if x is not None])
        if before is not None and after is not None:
            effect.append(
                compare(t("Game"), after, before, characteristic)
                + " — "
                + t("the same cooling as today, but steered by the right device")
            )
    device = "gpu" if series.device == "gpu" else "cpu"
    place = t("the graphics card") if device == "gpu" else t("the CPU")
    where = (
        t("“{sensor}” of “{device}”", sensor=target["label"], device=s.names.get(device) or place)
        if target and target.get("label")
        else t("the sensor of the device")
    )
    steps = [
        t("Open the curve “{curve}” in FanControl.", curve=curve.name),
        t(
            "Choose {where} as the temperature source: the sensor the panel shows as {device}{id}.",
            where=where,
            device=device.upper(),
            id=f" ({target['id']})" if target and target.get("id") else "",
        ),  # fmt: skip
    ]
    if points:
        steps.append(t("Set the curve points: {points}.", points=points_text(points)))
    common = {
        "curve": curve.name, "fans": series.name, "device": place, "seen": num(seen),
        "real": num(real), "duty": num(now or 0), "source": curve.source or "?",
    }  # fmt: skip
    if seen > real:
        why = t(
            "The curve {curve} reads {source}. On the desktop it sees {seen} °C, {device} is at "
            "{real} °C: it follows another device. {fans} therefore runs at {duty} % although "
            "{device} is cool.",
            **common,
        )
    else:
        why = t(
            "The curve {curve} reads {source}. On the desktop it sees {seen} °C, {device} is at "
            "{real} °C: it follows another device. {fans} therefore stays at {duty} % however "
            "warm {device} gets.",
            **common,
        )
    s.found.append(
        {
            "id": "source:" + curve.name, "curve": curve.name, "proposal": points,
            "title": t("Tie {fans} to {device}", fans=series.name, device=place),
            "why": why, "do": steps, "effect": effect,
            "note": t(
                "The points leave the fan at its base value on the desktop and give it, in a "
                "game, the cooling it had. The upper points stay as a reserve."
            ) if points else None,
            "confidence": t("certain (source) · {confidence} (points)", confidence=s.confidence()),
            "basis": basis(s.desk, s.game),
        }
    )  # fmt: skip


def rule_mix(s: Situation) -> None:
    """Does a mix curve take the highest of its parts?"""
    for series in s.series:
        if series.device != "mix":
            continue
        fan = series.fans[0]
        curve = s.curves[series.curve]
        for part in curve.mix:
            direct = next((x for x in s.series if x.curve == part), None)
            if direct is None:
                continue
            gaps = []
            for r in s.rows:
                mixed, own = r.duty(fan), r.duty(direct.fans[0])
                if mixed is not None and own is not None:
                    gaps.append((own - mixed, r.duration))
            seconds = sum(d for _, d in gaps)
            if seconds < MIN_MIX_S:
                continue
            share = sum(d for gap, d in gaps if gap > JUMP) / seconds
            if share < 0.5:
                s.checked.append(
                    t("{curve} takes the higher value of {part}.", curve=series.curve, part=part)
                )
                continue
            _add_mix(s, series, curve, direct, part, share)


def _add_mix(
    s: Situation, series: Series, curve: Curve, direct: Series, part: str, share: float
) -> None:
    fan = series.fans[0]
    characteristic = characteristic_of(s.rows, fan)
    effect, depends = [], None
    for title, rows, needed in (
        (t("Game"), s.game, MIN_GAME_S),
        (t("Desktop"), s.desk, MIN_DESKTOP_S),
    ):
        if total(rows) < needed:
            continue
        now = median(duties(rows, fan))
        new = median([(s.output(r, series.curve), r.duration) for r in rows])
        if now is None or new is None:
            continue
        effect.append(compare(title, new, now, characteristic))
        if (
            rows is s.desk and part in s.proposals
        ):  # would the change alone make the desktop louder?
            alone = median(
                [(s.output(r, series.curve, with_proposals=False), r.duration) for r in rows]
            )
            if alone is not None and alone > new + 3:
                depends = (alone, new)
    target = target_name(s.curves, part)
    now_game = median(duties(s.game, direct.fans[0])) or 0.0
    mixed_game = median(duties(s.game, fan)) or 0.0
    found = {
        "id": "mix:" + series.curve, "curve": series.curve, "proposal": None,
        "title": t("Let {fans} react to {target} too", fans=series.name, target=target),
        "why": t(
            "{part} was above the mix by more than {jump} points {share} % of the time: in a "
            "game {a} % against {b} %. So {fans} does not react to {target}.",
            part=part, jump=num(JUMP), share=num(100 * share), a=num(now_game), b=num(mixed_game),
            fans=series.name, target=target,
        ),
        "do": [
            t("Open the curve “{curve}” in FanControl.", curve=series.curve),
            t(
                "Choose “Max” as the mix function: the higher value of {parts} counts.",
                parts=t(" and ").join(curve.mix),
            ),
        ],
        "effect": effect, "note": None, "confidence": s.confidence(),
        "basis": basis(s.desk, s.game),
    }  # fmt: skip
    if depends:
        found["after"] = "source:" + part
        found["note"] = t(
            "Change the source of {part} first: alone, {fans} would run at {alone} % on the "
            "desktop instead of {new} %, because {part} sees too much there today.",
            part=part, fans=series.name, alone=num(depends[0]), new=num(depends[1]),
        )  # fmt: skip
    s.found.append(found)


def _raise_step(s: Situation, sensor: str, name: str, high: float) -> str:
    device = {"cpu_temp": "cpu", "gpu_temp": "gpu", "gpu_hotspot": "gpu"}.get(sensor)
    curves = [c.curve for c in s.series if device and c.device == device]
    if curves:
        return t(
            "Raise the curve {curves} around {temp} °C.", curves=", ".join(curves), temp=num(high)
        )
    if device == "gpu":
        return t(
            "Raise the fan curve of the graphics card, in its driver software or in FanControl."
        )
    if device == "cpu":
        return t(
            "Let the fans cool the CPU more in a game: raise its fan curve around {temp} °C.",
            temp=num(high),
        )
    return t("Let the fans cool {name} more in a game.", name=name)


def rule_warm(s: Situation) -> None:
    """Is anything too warm in a game (over its limit for 5 % of the time)?"""
    if total(s.game) < MIN_GAME_S:
        s.checked.append(
            t("Temperatures in a game: judged from {time} of game time.", time=duration(MIN_GAME_S))
        )
        return
    fine = []
    for limit in s.limits:
        high = median([(r.temp(limit.sensor), r.duration) for r in s.game], 0.95)
        if high is None:
            continue
        name = limit.name or sensor_name(limit.sensor, s.names)
        if high <= limit.max:
            fine.append(f"{name} {num(high)} °C")
            continue
        s.found.append(
            {
                "id": "warm:" + limit.sensor, "curve": None, "proposal": None,
                "title": t("{name} gets too warm in a game", name=name),
                "why": t(
                    "In 5 % of the game time {name} was above {temp} °C (limit {limit} °C).",
                    name=name, temp=num(high), limit=num(limit.max),
                ),
                "do": [_raise_step(s, limit.sensor, name, high)],
                "effect": [], "note": None, "confidence": s.confidence(),
                "basis": basis([], s.game),
            }
        )  # fmt: skip
    if fine:
        s.checked.append(
            t(
                "Temperatures in a game are fine (95 % of the time below the limit): {list}.",
                list=", ".join(fine),
            )
        )


def rule_oscillation(s: Situation) -> None:
    """Does a duty keep jumping, which one hears as up and down?"""
    if total(s.rows) < OSCILLATION_MIN_S:
        s.checked.append(
            t(
                "Oscillation: judged from {time} of recording (so far {have}).",
                time=duration(OSCILLATION_MIN_S), have=duration(total(s.rows)),
            )
        )  # fmt: skip
        return
    calm = []
    for series in s.series:
        jumps, before = 0, None
        for r in s.rows:
            duty = r.duty(series.fans[0])
            if duty is None:
                continue
            if before is not None and abs(duty - before) >= JUMP:
                jumps += 1
            before = duty
        per_hour = jumps / (total(s.rows) / 3600)
        if per_hour < OSCILLATION_PER_H:
            calm.append(f"{series.name} {num(per_hour)}/h")
            continue
        h = (s.curves.get(series.curve) or Curve(series.curve)).hysteresis
        up, down, delay = (
            h.get("HysteresisValueUp", 0),
            h.get("HysteresisValueDown", 0),
            h.get("ResponseTimeDown", 0),
        )
        s.found.append(
            {
                "id": "oscillation:" + series.curve, "curve": series.curve, "proposal": None,
                "title": t("{fans} oscillates", fans=series.name),
                "why": t(
                    "The duty jumps {n} times an hour by more than {jump} points: you hear that "
                    "as up and down.",
                    n=num(per_hour), jump=num(JUMP),
                ),
                "do": [
                    t("Open the curve “{curve}” in FanControl.", curve=series.curve),
                    t(
                        "Raise the hysteresis from {up}/{down} K to {new_up}/{new_down} K and "
                        "the response time downwards from {delay} s to {new_delay} s.",
                        up=up, down=down, new_up=up + 2, new_down=down + 2,
                        delay=delay, new_delay=delay + 8,
                    ),
                ],
                "effect": [t("fewer audible changes; the cooling reacts a little slower")],
                "note": None, "confidence": t("reliable"), "basis": duration(total(s.rows)),
            }
        )  # fmt: skip
    if calm:
        s.checked.append(
            t(
                "No oscillation (jumps over {jump} points): {list}.",
                jump=num(JUMP),
                list=", ".join(calm),
            )
        )


def rule_quieter(s: Situation) -> None:
    """Does a fan run higher on the desktop than it needs to?"""
    if total(s.desk) < MIN_DESKTOP_S:
        return
    for series in s.series:
        curve = s.curves.get(series.curve)
        if (
            series.device == "mix"
            or series.curve in s.proposals
            or curve is None
            or not curve.points
        ):
            continue
        fan = series.fans[0]
        base = curve.points[0][1]
        now = median(duties(s.desk, fan))
        characteristic = characteristic_of(s.rows, fan)
        if now is None or now < base + 4:
            s.checked.append(
                t(
                    "{fans} runs at its base value on the desktop: {now}.",
                    fans=series.name, now=percent_rpm(now, characteristic) if now else "?",
                )
            )  # fmt: skip
            continue
        sensor = DEVICE_SENSOR.get(series.device)
        reads = [(r.sources.get(curve.name), r.duration) for r in s.desk if curve.name in r.sources]
        if sum(d for _, d in reads) < MIN_DESKTOP_S and sensor:
            reads = [(r.temp(sensor), r.duration) for r in s.desk if r.temp(sensor) is not None]
        quiet = median(reads, 0.9)
        if quiet is None:
            continue
        limit = math.ceil(quiet + 2)
        new = [[limit, base]] + [[temp, level] for temp, level in curve.points if temp > limit + 2]
        saved = (rpm_at(characteristic, now) or 0) - (rpm_at(characteristic, base) or 0)
        if saved < QUIETER_MIN_RPM:
            s.checked.append(
                t(
                    "{fans}: on the desktop {now}: quieter would save only {saved} RPM, which is "
                    "not worth it.",
                    fans=series.name, now=percent_rpm(now, characteristic),
                    saved=num(max(0, saved)),
                )
            )  # fmt: skip
            continue
        s.proposals[curve.name] = {"points": new, "sensor": sensor or ""}
        reliable = total(s.desk) >= 6 * 3600
        s.found.append(
            {
                "id": "quieter:" + curve.name, "curve": curve.name, "proposal": new,
                "title": t("{fans} quieter on the desktop", fans=series.name),
                "why": t(
                    "On the desktop {fans} runs at {now}; the curve starts to rise at {start} "
                    "°C, but its input is below {limit} °C for 90 % of the desktop time.",
                    fans=series.name, now=percent_rpm(now, characteristic),
                    start=int(curve.points[0][0]), limit=limit - 2,
                ),
                "do": [
                    t("Open the curve “{curve}” in FanControl.", curve=curve.name),
                    t("Set the curve points: {points}.", points=points_text(new)),
                ],
                "effect": [compare(t("Desktop"), base, now, characteristic)],
                "note": t("Nothing changes in a game: the points above stay."),
                "confidence": t("reliable") if reliable else t("preliminary"),
                "basis": basis(s.desk, []),
            }
        )  # fmt: skip


def rule_trend(s: Situation) -> None:
    """Does the PC cool worse than two weeks ago, at the same load and outside temperature?"""
    if not s.rows or s.rows[-1].ts - s.rows[0].ts < TREND_DAYS * 86400:
        s.checked.append(t("Cooling over time: compared from {n} days of recording.", n=TREND_DAYS))
        return
    band = [
        (r, r.cpu_temp - r.outside)
        for r in s.desk
        if r.cpu_load is not None
        and 5 <= r.cpu_load <= 25
        and r.cpu_temp is not None
        and r.outside is not None
    ]
    if not band:
        return
    first, last = band[0][0].ts + 7 * 86400, band[-1][0].ts - 7 * 86400
    early = median([(gap, r.duration) for r, gap in band if r.ts < first])
    late = median([(gap, r.duration) for r, gap in band if r.ts > last])
    if early is None or late is None:
        return
    if late - early < TREND_K:
        s.checked.append(
            t(
                "Cooling is stable: same load, same outside air, {k} K against the first seven "
                "days.",
                k=num(late - early, 1, True),
            )
        )
        return
    s.found.append(
        {
            "id": "trend", "curve": None, "proposal": None,
            "title": t("The cooling is getting worse"),
            "why": t(
                "With the same load and the same outside temperature the CPU is {k} K warmer "
                "than in the first seven days.",
                k=num(late - early, 1),
            ),
            "do": [t("Clean the dust filters and the cooler's fins.")],
            "effect": [], "note": None, "confidence": t("reliable"),
            "basis": t("{time} in the comparison band", time=duration(total([r for r, _ in band]))),
        }
    )  # fmt: skip


RULES = (rule_source, rule_mix, rule_warm, rule_oscillation, rule_quieter, rule_trend)


# -- before and after ---------------------------------------------------------------------------


def _tenth(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def before_after(
    before: list[Row], after: list[Row], series: list[Series], names: dict[str, Any]
) -> list[dict[str, Any]]:
    """The cooling before and after the last change, per state (game, desktop): the duty of
    each curve's fans and the temperatures (medians, by time), where both sides have enough
    data."""
    found = []
    for mode, enough in (("game", MIN_GAME_S / 2), ("desktop", MIN_DESKTOP_S / 2)):
        a = [r for r in before if r.in_game == (mode == "game")]
        b = [r for r in after if r.in_game == (mode == "game")]
        if total(a) < enough or total(b) < enough:
            continue
        rows = []
        for s in series:
            rows.append(
                {
                    "quantity": s.name, "unit": "%",
                    "before": _tenth(median(duties(a, s.fans[0]))),
                    "after": _tenth(median(duties(b, s.fans[0]))),
                }
            )  # fmt: skip
        for sensor in data.candidates(before + after):
            rows.append(
                {
                    "quantity": sensor_name(sensor, names), "unit": "°C",
                    "before": _tenth(median([(r.temp(sensor), r.duration) for r in a])),
                    "after": _tenth(median([(r.temp(sensor), r.duration) for r in b])),
                }
            )  # fmt: skip
        found.append(
            {
                "mode": mode, "before_s": round(total(a)), "after_s": round(total(b)),
                "rows": [r for r in rows if r["before"] is not None and r["after"] is not None],
            }
        )  # fmt: skip
    return found


def advise(
    rows: list[Row],
    states: list[dict[str, Any]],
    curves: dict[str, Curve],
    fans: dict[str, Fan],
    names: dict[str, Any],
    limits: tuple[Limit, ...] = DEFAULT_LIMITS,
    now: float | None = None,
) -> dict[str, Any]:
    """All recommendations for the current FanControl setting, from the data since it was
    last changed (the first stored state is no change: it was noted when recording began).
    ``rows`` are the cooling rows (the last ``LOOKBACK_DAYS`` days), ``states`` those of
    FanControl, the oldest first."""
    since, before, change = None, [], None
    if len(states) >= 2:
        since = states[-1]["ts"]
        before = [
            r for r in rows
            if states[-2]["ts"] <= r.ts < since or (len(states) == 2 and r.ts < since)
        ]  # fmt: skip
        change = {
            "ts": since,
            "what": change_lines(fancontrol.changes(states[-2]["state"], states[-1]["state"])),
        }
    current = [r for r in rows if since is None or r.ts >= since]
    situation = Situation(current, curves, fans, names, limits)
    for rule in RULES:
        rule(situation)
    ranked = sorted(situation.found, key=lambda e: WEIGHT.get(e["id"].split(":")[0], 9))
    number = {e["id"]: i + 1 for i, e in enumerate(ranked)}
    for e in ranked:
        e["number"] = number[e["id"]]
        if e.get("after") in number:
            e["after_number"] = number[e["after"]]
    result: dict[str, Any] = {
        "empty": False,
        "since": since or (current[0].ts if current else None),
        "changed": since is not None,
        "basis": basis(situation.desk, situation.game),
        "list": ranked,
        "checked": situation.checked,
        "proposals": {k: v["points"] for k, v in situation.proposals.items()},
        "change": change,
        "effect": before_after(before, current, situation.series, names) if since else [],
        "computed": int(now if now is not None else time.time()),
        "changes": [s["ts"] for s in states[1:]],
    }
    if (
        since is not None
        and total(situation.desk) < MIN_DESKTOP_S
        and total(situation.game) < MIN_GAME_S
    ):
        result["waiting"] = t(
            "Since the change at {time} only {have} of data: the recommendations are worked out "
            "again as soon as {desktop} of desktop or {game} of game time are there.",
            time=time.strftime("%Y-%m-%d %H:%M", time.localtime(since)),
            have=duration(total(current)),
            desktop=duration(MIN_DESKTOP_S), game=duration(MIN_GAME_S),
        )  # fmt: skip
    return result

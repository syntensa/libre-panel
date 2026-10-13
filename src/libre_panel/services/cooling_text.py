"""The cooling analysis in words: the plain-text report and the sentences the recommendations
and findings share. Everything a person reads goes through ``t()``, so it comes in the language
of the panel (English or German).
"""

from __future__ import annotations

import time
from typing import Any

from libre_panel import i18n
from libre_panel.i18n import t
from libre_panel.services import cooling_data as data
from libre_panel.services.cooling_data import Row, weighted
from libre_panel.services.fancontrol import Curve, Fan

ENOUGH_S = 6 * 3600  # recorded so long, the analysis draws conclusions
LABEL = 14  # width of a line's label in the text report


def num(value: float, digits: int = 0, sign: bool = False) -> str:
    """A number as the language writes it: 26.7 or 26,7."""
    text = f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"
    return text.replace(".", ",") if i18n.language() in i18n.DECIMAL_COMMA else text


def _short(value: float) -> str:
    """One decimal, none where it would be zero: "1.8", "6"."""
    text = num(value, 1)
    return text[:-2] if text.endswith((".0", ",0")) else text


def duration(seconds: float) -> str:
    """ "45 s", "30 min", "1.8 h", "6 h", "3.5 days"."""
    if seconds < 90:
        return t("{n} s", n=num(seconds))
    if seconds < 5400:
        return t("{n} min", n=num(seconds / 60))
    if seconds < 2 * 86400:
        return t("{n} h", n=_short(seconds / 3600))
    return t("{n} days", n=_short(seconds / 86400))


def clock(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def device_name(kind: str, names: dict[str, Any]) -> str:
    """ "CPU temperature (name of the CPU)": the name is what the readings say, never fixed."""
    name = names.get(kind)
    base = t("CPU temperature") if kind == "cpu" else t("GPU temperature")
    return f"{base} ({name})" if name else base


def sensor_name(sensor: str, names: dict[str, Any]) -> str:
    """What to call a recorded temperature: the CPU, the GPU, its hot spot, or its reading."""
    if sensor == "cpu_temp":
        return t("CPU")
    if sensor == "gpu_temp":
        return t("GPU")
    if sensor == "gpu_hotspot":
        return t("GPU hot spot")
    for entry in names.get("sensors") or []:
        if isinstance(entry, dict) and entry.get("key") == sensor and entry.get("label"):
            return str(entry["label"])
    return sensor


def basis(desk: list[Row], game: list[Row]) -> str:
    """What the data is: how long a game (how many rounds) and how long the desktop."""
    parts = []
    if game:
        n = data.rounds(game)
        time_ = duration(data.total(game))
        parts.append(
            t("{time} game ({n} round)", time=time_, n=n)
            if n == 1
            else t("{time} game ({n} rounds)", time=time_, n=n)
        )
    if desk:
        parts.append(t("{time} desktop", time=duration(data.total(desk))))
    return ", ".join(parts) or t("no data yet")


# -- what the curves do -------------------------------------------------------------------------


def source_lines(verdict: dict[str, Any], names: dict[str, Any] | None = None) -> list[str]:
    """``find_source`` in words; the sensors are named as ``sensor_name`` does."""
    names = names or {}
    kind = verdict["verdict"]
    if kind == "too_few" and "span" not in verdict:
        return [
            t(
                "not enough samples yet in the rising part of the curve ({n})",
                n=verdict["samples"],
            )
        ]
    low, high = verdict["span"]
    lines = [
        t("the measured duty corresponds to {low}..{high} degrees", low=num(low), high=num(high))
    ]
    if kind == "too_quiet":
        lines.append(
            t(
                "too quiet for a verdict ({span} K range, {needed} needed): the comparison needs "
                "load changes.",
                span=num(high - low, 1),
                needed=num(data.SPAN_NEEDED),
            )
        )
        return lines
    if kind == "too_few":
        return lines
    best = verdict["best"]
    r = num(best["r"] or 0, 2)
    if kind == "follows":
        if abs(best["offset"]) <= 2.0:
            lines.append(
                t(
                    "follows {sensor} exactly (spread {spread} K, r={r})",
                    sensor=sensor_name(best["sensor"], names), spread=num(best["spread"], 1), r=r,
                )
            )  # fmt: skip
        else:
            lines.append(
                t(
                    "follows {sensor} with a fixed offset of {offset} K (spread {spread} K, r={r})",
                    sensor=sensor_name(best["sensor"], names),
                    offset=num(best["offset"], 1, True), spread=num(best["spread"], 1), r=r,
                )
            )  # fmt: skip
            lines.append(t("so another sensor of the same part."))
    else:
        lines.append(
            t(
                "NO recorded sensor explains it (the nearest, {sensor}: spread {spread} K, r={r}).",
                sensor=sensor_name(best["sensor"], names), spread=num(best["spread"], 1), r=r,
            )
        )  # fmt: skip
        lines.append(t("Sensor in FanControl: {id}", id=verdict.get("source") or "?"))
    for other in verdict["others"]:
        lines.append(
            t(
                "for comparison {sensor}: offset {offset} K, spread {spread} K, r={r}",
                sensor=sensor_name(other["sensor"], names), offset=num(other["offset"], 1, True),
                spread=num(other["spread"], 1),
                r=num(other["r"], 2) if other["r"] is not None else "--",
            )
        )  # fmt: skip
    return lines


def change_lines(changes: list[dict[str, Any]]) -> list[str]:
    """What changed between two FanControl states, in words."""
    lines = []
    for c in changes:
        kind = c["kind"]
        if kind == "curve-added":
            lines.append(t("Curve {curve} created", curve=c["curve"]))
        elif kind == "curve-removed":
            lines.append(t("Curve {curve} deleted", curve=c["curve"]))
        elif kind == "source":
            lines.append(
                t("{curve}: sensor {old} → {new}", curve=c["curve"], old=c["old"], new=c["new"])
            )
        elif kind == "points":
            lines.append(
                t(
                    "{curve}: points {old} → {new}",
                    curve=c["curve"], old=" ".join(c["old"]), new=" ".join(c["new"]),
                )
            )  # fmt: skip
        elif kind == "mix-function":
            lines.append(
                t(
                    "{curve}: mix function {old} → {new}",
                    curve=c["curve"],
                    old=c["old"],
                    new=c["new"],
                )
            )
        elif kind == "hysteresis":
            lines.append(t("{curve}: hysteresis changed", curve=c["curve"]))
        elif kind == "fan":
            lines.append(t("Fan {fan}: {fields}", fan=c["fan"], fields=", ".join(c["fields"])))
    return lines or [t("only small things that do not touch the curves")]


def percent_rpm(duty: float, characteristic: list[tuple[float, float]]) -> str:
    """ "52 % (≈ 782 RPM)" , or honestly that it was never measured there."""
    speed = rpm_at(characteristic, duty)
    if speed is not None:
        return t("{duty} % (≈ {rpm} RPM)", duty=num(duty), rpm=num(speed))
    if characteristic:
        if duty < characteristic[0][0]:
            return t(
                "{duty} % (below everything measured so far, speed not known yet)", duty=num(duty)
            )
        return t("{duty} % (above everything measured so far, speed not known yet)", duty=num(duty))
    return t("{duty} %", duty=num(duty))


def rpm_at(characteristic: list[tuple[float, float]], duty: float | None) -> float | None:
    """The speed at this duty, from the measured characteristic, and only inside what was
    measured."""
    if not characteristic or duty is None:
        return None
    if duty < characteristic[0][0] - 1.5 or duty > characteristic[-1][0] + 1.5:
        return None
    if duty <= characteristic[0][0]:
        return characteristic[0][1]
    for (d0, r0), (d1, r1) in zip(characteristic, characteristic[1:], strict=False):
        if duty <= d1:
            return r0 + (r1 - r0) * (duty - d0) / (d1 - d0)
    return characteristic[-1][1]


def characteristic_of(rows: list[Row], fan: str) -> list[tuple[float, float]]:
    """Duty -> speed, as measured: the duties in steps of two points are averaged, and only
    steps in which the fan ran for a while count (not a start-up)."""
    steps: dict[int, list[tuple[float, float, float]]] = {}
    for r in rows:
        rpm, duty = r.fans.get(fan, (None, None))
        if rpm is not None and duty is not None:
            steps.setdefault(int(duty // 2), []).append((duty, rpm, r.duration))
    found = []
    for _step, items in sorted(steps.items()):
        seconds = sum(held for _, _, held in items)
        if seconds >= 30:
            duty = sum(d * held for d, _, held in items) / seconds
            found.append((duty, weighted([(rpm, held) for _, rpm, held in items]) or 0.0))
    return found


def compare(title: str, new: float, now: float, characteristic: list[tuple[float, float]]) -> str:
    """ "Game: 52 % ... instead of 33 % ...", or that it stays about the same."""
    if abs(new - now) < 2:
        return t(
            "{title}: practically unchanged ({now} % → {new} %)",
            title=title, now=num(now), new=num(new),
        )  # fmt: skip
    return t(
        "{title}: {new} instead of {now}",
        title=title, new=percent_rpm(new, characteristic), now=percent_rpm(now, characteristic),
    )  # fmt: skip


def points_text(points: list[list[int]] | list[tuple[float, float]]) -> str:
    return ", ".join(t("{temp} °C → {duty} %", temp=int(p[0]), duty=int(p[1])) for p in points)


# -- the plain-text report ------------------------------------------------------------------------


def _row(label: str, text: str) -> str:
    return f"  {label:<{LABEL}}{text}"


def scope_lines(rows: list[Row]) -> list[str]:
    """How much was recorded, and how it divides between the desktop and each game."""
    seconds = data.total(rows)
    lines = [
        t(
            "RECORDED  {time} in {rows} rows, from {first} to {last}",
            time=duration(seconds),
            rows=len(rows),
            first=clock(rows[0].ts),
            last=clock(rows[-1].ts),
        )  # fmt: skip
    ]
    by_state: dict[str, float] = {}
    for r in rows:
        name = r.game or (t("Game (unnamed)") if r.in_game else t("Desktop"))
        by_state[name] = by_state.get(name, 0.0) + r.duration
    for name, held in sorted(by_state.items(), key=lambda x: -x[1]):
        lines.append(f"         {name:<28s} {duration(held):>8s}  {100 * held / seconds:4.1f} %")
    return lines


def fan_lines(
    rows: list[Row],
    name: str,
    fans: dict[str, Fan],
    curves: dict[str, Curve],
    names: dict[str, Any] | None = None,
) -> list[str]:
    """What one fan did, and how that fits its curve."""
    pairs = data.data_of(rows, name)
    if not pairs:
        return []
    fan = fans.get(name) or fans.get(name.replace(" ", "_"))
    curve = curves.get(fan.curve) if fan else None
    lines = ["", name.upper()]
    if curve is not None:
        source = curve.source or t("a mix of {parts}", parts=t(" and ").join(curve.mix))
        lines.append(_row(t("Curve"), t("{curve} on {source}", curve=curve.name, source=source)))
        if curve.points:
            lines.append(_row("", ", ".join(f"{p[0]:g}->{p[1]:g}%" for p in curve.points)))
    else:
        lines.append(_row(t("Curve"), t("none (FanControl does not drive this fan)")))
    held = sum(r.duration for r, _ in pairs)
    duty = [(d, r.duration) for r, (_rpm, d) in pairs if d is not None]
    speed = [(rpm, r.duration) for r, (rpm, _d) in pairs if rpm is not None]
    if duty:
        lines.append(
            _row(
                t("Duty"),
                t(
                    "median {median} %, p95 {high} %, highest {top} %",
                    median=num(weighted(duty) or 0, 1), high=num(weighted(duty, 0.95) or 0, 1),
                    top=num(max(d for d, _ in duty), 1),
                ),
            )
        )  # fmt: skip
        minimum = (fan.minimum if fan else None) or 20
        at_limit = sum(s for d, s in duty if d >= 95) / held * 100
        at_minimum = sum(s for d, s in duty if d <= minimum + 2) / held * 100
        lines.append(
            _row(
                "",
                t(
                    "{top} % of the time at the limit (>=95 %), {low} % at the minimum",
                    top=num(at_limit, 1), low=num(at_minimum, 1),
                ),
            )
        )  # fmt: skip
    if speed:
        lines.append(
            _row(
                t("Speed"),
                t(
                    "median {median} RPM, p95 {high} RPM, highest {top} RPM",
                    median=num(weighted(speed) or 0), high=num(weighted(speed, 0.95) or 0),
                    top=num(max(s for s, _ in speed)),
                ),
            )
        )  # fmt: skip
    steps: dict[int, list[tuple[float, float]]] = {}
    for r, (rpm, d) in pairs:
        if rpm is not None and d is not None:
            steps.setdefault(int(d // 10) * 10, []).append((rpm, r.duration))
    if steps:
        table = "  ".join(f"{k}%: {num(weighted(v) or 0)}" for k, v in sorted(steps.items()))
        lines.append(_row(t("Duty to speed"), t("{table} RPM", table=table)))
    if curve is not None and not curve.mix and duty:
        found = data.deviation(pairs, curve)
        if found:
            lines.append(
                _row(
                    t("Input"),
                    t(
                        "measured: the duty is off the curve by {median} on average, p95 {high} "
                        "points ({time})",
                        median=num(found[0], 1), high=num(found[1], 1), time=duration(found[2]),
                    ),
                )
            )  # fmt: skip
        else:
            verdict = source_lines(data.find_source(pairs, curve, data.candidates(rows)), names)
            lines.append(_row(t("Sensor"), verdict[0]))
            lines.extend(_row("", line) for line in verdict[1:])
    return lines


def notable_lines(rows: list[Row], fans: dict[str, Fan], curves: dict[str, Curve]) -> list[str]:
    """What the data says by itself, with no advice into the blue."""
    seconds = data.total(rows)
    if seconds < ENOUGH_S:
        return [
            "",
            t(
                "No conclusions yet: they need at least {needed} of recording (here {have}).",
                needed=duration(ENOUGH_S), have=duration(seconds),
            ),
        ]  # fmt: skip
    lines = ["", t("NOTABLE")]
    said = False
    names = data.fan_names(rows)
    for name in names:  # curve points that were never needed (by the duty reached)
        fan = fans.get(name) or fans.get(name.replace(" ", "_"))
        curve = curves.get(fan.curve) if fan else None
        if curve is None or curve.mix or not curve.points:
            continue
        duties = [r.duty(name) for r in rows if r.duty(name) is not None]
        if not duties:
            continue
        top = max(duties)
        unused = [temp for temp, duty in curve.points if duty > top + 1]
        if len(unused) >= 2:
            lines.append(
                "  "
                + t(
                    "{fan} never went above {top} %: the points from {temp} degrees of {curve} "
                    "were never needed.",
                    fan=name, top=num(top), temp=num(min(unused)), curve=curve.name,
                )
            )  # fmt: skip
            said = True
    for name in names:  # oscillation: how often the duty jumps
        jumps, before = 0, None
        for r in rows:
            duty = r.duty(name)
            if duty is None:
                continue
            if before is not None and abs(duty - before) >= 5:
                jumps += 1
            before = duty
        per_hour = jumps / (seconds / 3600)
        if per_hour >= 20:
            lines.append(
                "  "
                + t(
                    "{fan} changes its duty {n} times an hour by more than 5 points: audible "
                    "oscillation, check hysteresis or response time.",
                    fan=name, n=num(per_hour),
                )
            )  # fmt: skip
            said = True
    desk = [r for r in rows if not r.in_game]
    if data.total(desk) > 3600:  # high on the desktop: noise without a reason
        for name in data.fan_names(desk):
            median = weighted([(r.duty(name), r.duration) for r in desk])
            if median is not None and median >= 40:
                lines.append(
                    "  "
                    + t(
                        "{fan} runs at {median} % on the desktop on average: that is a lot for "
                        "idling.",
                        fan=name, median=num(median),
                    )
                )  # fmt: skip
                said = True
    if not said:
        lines.append("  " + t("nothing the data gives."))
    return lines


def trend_lines(rows: list[Row]) -> list[str]:
    """Is the cooling getting worse, or was it just warmer outside? The median CPU temperature
    per day in one load band (5..25 %), next to the outside temperature: a day with a lot of
    gaming would look like a dusty cooler otherwise."""
    days: dict[str, list[Row]] = {}
    for r in rows:
        if r.cpu_load is None or not 5 <= r.cpu_load <= 25:
            continue
        days.setdefault(time.strftime("%Y-%m-%d", time.localtime(r.ts)), []).append(r)
    if len(days) < 2 or data.total(rows) < ENOUGH_S:
        return []
    lines = ["", t("TREND  (CPU temperature at 5..25 % load, per day)")]
    for day, found in list(days.items())[-21:]:
        inside = weighted([(r.cpu_temp, r.duration) for r in found])
        outside = weighted([(r.outside, r.duration) for r in found if r.outside is not None])
        lines.append(
            "  "
            + t(
                "{day}  {inside} °C   outside {outside}   ({time})",
                day=day,
                inside=num(inside, 1) if inside is not None else "?",
                outside=f"{num(outside, 1)} °C" if outside is not None else "?",
                time=duration(sum(r.duration for r in found)),
            )
        )  # fmt: skip
    return lines


def advice_lines(advice: dict[str, Any]) -> list[str]:
    """The recommendations as text: what to do, why, what it changes, and what it rests on."""
    found = advice.get("list") or []
    if not found:
        return []
    lines = [
        "",
        t(
            "RECOMMENDATIONS  (since the last change in FanControl, from {basis})",
            basis=advice["basis"],
        ),
    ]
    for rec in found:
        lines.append("")
        lines.append(f"  {rec['number']}. {rec['title']}")
        lines.append(f"     {rec['why']}")
        lines.extend(f"     - {step}" for step in rec["do"])
        lines.extend(f"     = {effect}" for effect in rec["effect"])
        if rec.get("note"):
            lines.append(f"     ({rec['note']})")
        lines.append(
            "     "
            + t("Basis: {basis} · {confidence}", basis=rec["basis"], confidence=rec["confidence"])
        )
    return lines


def report(
    rows: list[Row],
    fans: dict[str, Fan],
    curves: dict[str, Curve],
    advice: dict[str, Any] | None = None,
    names: dict[str, Any] | None = None,
) -> list[str]:
    """The whole report as lines: what was recorded, each fan, what is notable, the trend and
    the recommendations. ``names`` are the hardware and sensor names the memory noted."""
    lines = scope_lines(rows)
    for name in data.fan_names(rows):
        lines += fan_lines(rows, name, fans, curves, names)
    lines += notable_lines(rows, fans, curves)
    lines += trend_lines(rows)
    lines += advice_lines(advice or {})
    return lines

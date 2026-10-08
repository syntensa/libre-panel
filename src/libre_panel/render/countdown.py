"""Counting down to a moment: "2026-12-24", "2026-12-24 18:00" or "12-24" (every year)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from libre_panel import i18n
from libre_panel.i18n import t

_TARGET = re.compile(r"^\s*(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?\s*$")


@dataclass(frozen=True)
class Countdown:
    target: datetime
    left: timedelta  # zero once it is there
    done: bool  # the day has come (a date) or the moment has passed (a time)
    timed: bool  # the target has a time of day
    over: bool = False  # a day (not every year's) that has gone by


def parse_target(text: str) -> tuple[int | None, int, int, int | None, int | None] | None:
    match = _TARGET.match(text or "")
    if not match:
        return None
    year, month, day, hour, minute = (int(g) if g else None for g in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31) or (hour is not None and hour > 23):
        return None
    if minute is not None and minute > 59:
        return None
    return year, month, day, hour, minute


def _moment(year: int, month: int, day: int, hour: int | None, minute: int | None) -> datetime:
    try:
        return datetime(year, month, day, hour or 0, minute or 0)
    except ValueError:  # 29 February in a common year: the 28th
        return datetime(year, month, min(day, 28), hour or 0, minute or 0)


def countdown(text: str, now: datetime) -> Countdown | None:
    """Where ``text`` stands from ``now``; None if it is no date."""
    parts = parse_target(text)
    if parts is None:
        return None
    year, month, day, hour, minute = parts
    timed = hour is not None
    target = _moment(year or now.year, month, day, hour, minute)
    if year is None and target.date() < now.date():  # every year: the next one
        target = _moment(now.year + 1, month, day, hour, minute)
    if target.date() == now.date() and (not timed or now >= target):
        return Countdown(target, timedelta(0), True, timed)
    if target < now:
        if year is None:
            target = _moment(now.year + 1, month, day, hour, minute)
        else:
            return Countdown(target, timedelta(0), True, timed, over=True)
    return Countdown(target, target - now, False, timed)


def _clock(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    return f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"


def countdown_text(text: str, part: str, now: datetime, done: str = "") -> str:
    """One part of a countdown: the number, its unit, the time left in the day,
    the whole in words, the days, or the date it counts to."""
    state = countdown(text, now)
    if state is None:
        return "--"
    if part == "date":
        fmt = "%A, %d %B %Y %H:%M" if state.timed else "%A, %d %B %Y"
        return i18n.format_date(state.target, fmt)
    if state.over:  # nothing left to count
        return {"unit": t("days"), "clock": "", "text": done or "0"}.get(part, "0")
    if state.done:
        if part in ("unit", "clock"):
            return ""
        return done or (t("Now") if state.timed else t("Today"))
    seconds = int(state.left.total_seconds())
    days, rest = divmod(seconds, 86400)
    hours, minutes = rest // 3600, rest % 3600 // 60
    if not state.timed:  # a date: the days still to sleep, today not counted
        days = (state.target.date() - now.date()).days
    if part == "days":
        return str(days)
    if part == "clock":
        return _clock(rest)
    if part == "unit":
        if days:
            return t("day") if days == 1 else t("days")
        return t("hours") if hours else t("minutes")
    if part == "number":
        if days:
            return str(days)
        return f"{hours}:{minutes:02d}" if hours else f"{minutes}:{rest % 60:02d}"
    # text
    if days:
        unit = t("day") if days == 1 else t("days")
        return f"{days} {unit}" + (f" {hours} h" if state.timed and days < 3 else "")
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {rest % 60} s"

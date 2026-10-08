"""Calendar events from iCalendar (.ics): the secret address of a Google,
Outlook, iCloud or Nextcloud calendar, or a file.

``[sensors.calendar]``::

    sources = ["https://calendar.google.com/calendar/ical/.../basic.ics", "C:/Calendars/work.ics"]
    days = 14                # how far ahead
    update_minutes = 15

Readings for the next events, ``calendar.<n>.*`` (1 = the next or the one
going on): ``title``, ``when`` ("Today 14:00", "Tomorrow", "Fri 16 Oct
09:30"), ``day``, ``time``, ``location``, ``start`` (Unix time), ``minutes``
(until it starts), ``now`` (1, only while it is going on); ``calendar.events`` and
``calendar.today`` count them.

Repeating events follow their rule (daily, weekly on given days, monthly on a
day or "the second Tuesday", yearly; with an end, a count, left-out days and
moved single days). Times keep their time zone.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from libre_panel import __version__, i18n
from libre_panel.i18n import t
from libre_panel.sensors.base import Reading, SensorProvider
from libre_panel.timezones import zone

log = logging.getLogger(__name__)

MAX_EVENTS = 20
MAX_BYTES = 8 * 1024 * 1024
MAX_STEPS = 5000  # occurrences looked at per repeating event
_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
# Outlook names its time zones the Windows way.
_WINDOWS_ZONES = {
    "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris", "Central European Standard Time": "Europe/Warsaw",
    "GMT Standard Time": "Europe/London", "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev", "Russian Standard Time": "Europe/Moscow",
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles",
    "Tokyo Standard Time": "Asia/Tokyo", "China Standard Time": "Asia/Shanghai",
    "India Standard Time": "Asia/Kolkata", "AUS Eastern Standard Time": "Australia/Sydney",
    "UTC": "UTC", "Coordinated Universal Time": "UTC",
}  # fmt: skip


@dataclass
class Event:
    title: str
    start: datetime  # local time, without a zone
    end: datetime
    all_day: bool = False
    location: str = ""
    calendar: str = ""


@dataclass
class _Raw:
    props: dict[str, list[tuple[dict[str, str], str]]] = field(default_factory=dict)

    def get(self, name: str) -> tuple[dict[str, str], str] | None:
        found = self.props.get(name)
        return found[0] if found else None

    def all(self, name: str) -> list[tuple[dict[str, str], str]]:
        return self.props.get(name, [])


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1 :]
            break
    else:
        return line.upper(), {}, ""
    name, *params = head.split(";")
    found = {}
    for param in params:
        key, _, val = param.partition("=")
        found[key.upper()] = val.strip('"')
    return name.upper(), found, value


def _text(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _zone_of(params: dict[str, str]) -> tzinfo | None:
    name = params.get("TZID", "")
    if not name:
        return None
    # "/mozilla.org/20050126_1/Europe/Berlin": the last two parts name it
    return zone(_WINDOWS_ZONES.get(name, name)) or zone("/".join(name.strip("/").split("/")[-2:]))


def _moment(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    """A start or end as local time, and whether it is a whole day."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d"), True
    utc = value.endswith("Z")
    moment = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    place = UTC if utc else _zone_of(params)
    if place is not None:
        moment = moment.replace(tzinfo=place).astimezone().replace(tzinfo=None)
    return moment, False


def _duration(value: str) -> timedelta:
    match = re.fullmatch(
        r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip()
    )
    if not match:
        return timedelta(0)
    sign, weeks, days, hours, minutes, seconds = match.groups()
    span = timedelta(
        weeks=int(weeks or 0), days=int(days or 0), hours=int(hours or 0),
        minutes=int(minutes or 0), seconds=int(seconds or 0),
    )  # fmt: skip
    return -span if sign == "-" else span


def _raw_events(text: str) -> tuple[list[_Raw], str]:
    events: list[_Raw] = []
    name = ""
    current: _Raw | None = None
    depth = 0  # VALARM and the like inside an event
    for line in _unfold(text):
        key, params, value = _split(line)
        if key == "BEGIN":
            if value.upper() == "VEVENT" and current is None:
                current = _Raw()
            elif current is not None:
                depth += 1
            continue
        if key == "END":
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.upper() == "VEVENT":
                events.append(current)
                current = None
            continue
        if current is not None and not depth:
            current.props.setdefault(key, []).append((params, value))
        elif key == "X-WR-CALNAME":
            name = _text(value)
    return events, name


def _rule(value: str) -> dict[str, str]:
    return {k.upper(): v for k, _, v in (p.partition("=") for p in value.split(";")) if k}


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date | None:
    if nth > 0:
        first = date(year, month, 1)
        day = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    else:
        following = date(year + (month == 12), month % 12 + 1, 1)
        last = following - timedelta(days=1)
        day = last - timedelta(days=(last.weekday() - weekday) % 7 + 7 * (-nth - 1))
    return day if day.month == month else None


def _occurrences(start: datetime, rule: dict[str, str], until_window: datetime) -> list[datetime]:
    """The starts a rule gives, from ``start`` until the window's end."""
    freq = rule.get("FREQ", "")
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        try:
            until = _moment(rule["UNTIL"], {})[0]
            if len(rule["UNTIL"]) == 8:
                until += timedelta(days=1) - timedelta(seconds=1)  # the whole last day
        except ValueError:
            until = None
    by_day = [d for d in rule.get("BYDAY", "").split(",") if d]
    by_month_day = [
        int(d) for d in rule.get("BYMONTHDAY", "").split(",") if d.lstrip("-").isdigit()
    ]
    clock = start.time()
    found: list[datetime] = []

    def take(moment: datetime) -> bool:
        """Add one; False once the rule or the window ends."""
        if moment < start:
            return True
        if (until and moment > until) or (count is not None and len(found) >= count):
            return False
        if moment > until_window:
            return False
        found.append(moment)
        return True

    for step in range(MAX_STEPS):
        if freq == "DAILY":
            if not take(start + timedelta(days=step * interval)):
                break
        elif freq == "WEEKLY":
            week = start.date() - timedelta(days=start.weekday()) + timedelta(weeks=step * interval)
            days = sorted(_DAYS.index(d[-2:]) for d in by_day if d[-2:] in _DAYS)
            days = days or [start.weekday()]
            if not all(take(datetime.combine(week + timedelta(days=d), clock)) for d in days):
                break
        elif freq == "MONTHLY":
            month0 = start.month - 1 + step * interval
            year, month = start.year + month0 // 12, month0 % 12 + 1
            days: list[date] = []
            for spec in by_day:
                match = re.fullmatch(r"([+-]?\d*)([A-Z]{2})", spec)
                if match and match.group(2) in _DAYS:
                    nth = int(match.group(1) or 1)
                    found_day = _nth_weekday(year, month, _DAYS.index(match.group(2)), nth)
                    if found_day:
                        days.append(found_day)
            for number in by_month_day or ([] if by_day else [start.day]):
                following = date(year + (month == 12), month % 12 + 1, 1)
                length = (following - date(year, month, 1)).days
                day_no = number if number > 0 else length + number + 1
                if 1 <= day_no <= length:
                    days.append(date(year, month, day_no))
            if not all(take(datetime.combine(d, clock)) for d in sorted(days)):
                break
        elif freq == "YEARLY":
            year = start.year + step * interval
            try:
                moment = start.replace(year=year)
            except ValueError:  # 29 February
                continue
            if not take(moment):
                break
        else:
            take(start)
            break
    return found


def events_from_ics(text: str, now: datetime, days: int = 14, calendar: str = "") -> list[Event]:
    """The events of an .ics text that end after ``now - 1 day`` and start
    before ``now + days``, repeating ones unfolded."""
    raws, name = _raw_events(text)
    window_start, window_end = now - timedelta(days=1), now + timedelta(days=days)
    moved: dict[tuple[str, datetime], _Raw] = {}
    plain: list[_Raw] = []
    for raw in raws:
        recurrence = raw.get("RECURRENCE-ID")
        uid = (raw.get("UID") or ({}, ""))[1]
        if recurrence:
            try:
                moved[(uid, _moment(recurrence[1], recurrence[0])[0])] = raw
            except ValueError:
                continue
        plain.append(raw)
    found: list[Event] = []
    for raw in plain:
        status = (raw.get("STATUS") or ({}, ""))[1].upper()
        start_prop = raw.get("DTSTART")
        if status == "CANCELLED" or start_prop is None:
            continue
        try:
            start, all_day = _moment(start_prop[1], start_prop[0])
            if raw.get("DTEND"):
                end = _moment(raw.get("DTEND")[1], raw.get("DTEND")[0])[0]
            elif raw.get("DURATION"):
                end = start + _duration(raw.get("DURATION")[1])
            else:
                end = start + (timedelta(days=1) if all_day else timedelta(0))
        except ValueError:
            continue
        length = max(timedelta(0), end - start)
        title = _text((raw.get("SUMMARY") or ({}, ""))[1]) or t("(no title)")
        location = _text((raw.get("LOCATION") or ({}, ""))[1])
        uid = (raw.get("UID") or ({}, ""))[1]
        starts = [start]
        rule = raw.get("RRULE")
        if rule and not raw.get("RECURRENCE-ID"):
            starts = _occurrences(start, _rule(rule[1]), window_end)
            left_out = set()
            for params, value in raw.all("EXDATE"):
                for part in value.split(","):
                    try:
                        left_out.add(_moment(part, params)[0])
                    except ValueError:
                        continue
            starts = [s for s in starts if s not in left_out and (uid, s) not in moved]
        for begin in starts:
            finish = begin + length
            if finish >= window_start and begin < window_end:
                found.append(Event(title, begin, finish, all_day, location, calendar or name))
    found.sort(key=lambda e: (e.start, not e.all_day, e.title))
    return found


def _when(event: Event, now: datetime) -> tuple[str, str]:
    """The day ("Today", "Tomorrow", "Fri 16 Oct") and the time ("14:00", "All day")."""
    day = event.start.date()
    if event.all_day and event.start <= now < event.end:
        day = now.date()
    if day == now.date():
        name = t("Today")
    elif day == now.date() + timedelta(days=1):
        name = t("Tomorrow")
    elif day < now.date() + timedelta(days=7):
        name = i18n.format_date(datetime.combine(day, datetime.min.time()), "%A")
    else:
        name = i18n.format_date(datetime.combine(day, datetime.min.time()), "%a %d %b")
    return name, t("All day") if event.all_day else event.start.strftime("%H:%M")


def calendar_readings(events: list[Event], now: datetime) -> dict[str, Reading]:
    upcoming = [e for e in events if e.end > now or (e.end == e.start and e.start >= now)]
    out: dict[str, Reading] = {}
    values: dict[str, tuple[Any, str]] = {
        "calendar.events": (float(len(upcoming)), ""),
        "calendar.today": (float(sum(1 for e in upcoming if e.start.date() <= now.date())), ""),
    }
    for n, event in enumerate(upcoming[:MAX_EVENTS], start=1):
        day, clock = _when(event, now)
        going = event.start <= now < event.end
        values.update({
            f"calendar.{n}.title": (event.title, ""),
            f"calendar.{n}.day": (day, ""),
            f"calendar.{n}.time": (clock, ""),
            f"calendar.{n}.when": (f"{day} · {clock}" if event.all_day else f"{day} {clock}", ""),
            f"calendar.{n}.location": (event.location or None, ""),
            f"calendar.{n}.calendar": (event.calendar or None, ""),
            f"calendar.{n}.start": (event.start.timestamp(), "s"),
            f"calendar.{n}.minutes": ((event.start - now).total_seconds() / 60, "min"),
            f"calendar.{n}.now": (1 if going else None, ""),  # only while it is on
        })  # fmt: skip
    for key, (value, unit) in values.items():
        if value is not None:
            out[key] = Reading(key, value, unit, key.split(".")[-1])
    return out


def _load(source: str) -> str:
    source = source.strip()
    if source.startswith("webcal://"):
        source = "https://" + source[len("webcal://") :]
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(
            source, headers={"User-Agent": f"libre-panel/{__version__}"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(MAX_BYTES + 1)
    else:
        data = Path(source).expanduser().read_bytes()[: MAX_BYTES + 1]
    if len(data) > MAX_BYTES:
        raise ValueError("calendar larger than 8 MB")
    return data.decode("utf-8", "replace")


class CalendarProvider(SensorProvider):
    name = "calendar"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        sources = self.options.get("sources", [])
        self.sources = [sources] if isinstance(sources, str) else [str(s) for s in sources]
        self.days = max(1, min(366, int(self.options.get("days", 14))))
        self.update_seconds = max(60.0, float(self.options.get("update_minutes", 15)) * 60)
        self._texts: dict[str, str] = {}
        self._events: list[Event] = []
        self._built_for: tuple[int, date] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if not self.sources:
            log.warning("calendar: no sources in [sensors.calendar]")
        self._thread = threading.Thread(target=self._poll, name="sensors-calendar", daemon=True)
        self._thread.start()

    def _poll(self) -> None:
        failed: set[str] = set()
        while not self._stop.is_set():
            for source in self.sources:
                try:
                    text = _load(source)
                    with self._lock:
                        self._texts[source] = text
                        self._built_for = None
                    failed.discard(source)
                except Exception as exc:  # noqa: BLE001 - a calendar may be away for a while
                    if source not in failed:  # the address is a secret: not in the log
                        log.warning("calendar %d not loaded: %s", self.sources.index(source) + 1,
                                    type(exc).__name__)  # fmt: skip
                        failed.add(source)
            self._stop.wait(self.update_seconds)

    def read(self) -> dict[str, Reading]:
        now = datetime.now()
        with self._lock:
            stamp = (len(self._texts), now.date())
            if self._built_for != stamp or now.minute % 10 == 0:
                events: list[Event] = []
                for text in self._texts.values():
                    try:
                        events.extend(events_from_ics(text, now, self.days))
                    except Exception:  # noqa: BLE001 - one odd calendar must not stop the others
                        log.exception("calendar could not be read")
                events.sort(key=lambda e: (e.start, not e.all_day, e.title))
                self._events, self._built_for = events, stamp
            events = self._events
        return calendar_readings(events, now)

    def close(self) -> None:
        self._stop.set()

"""The sun and the moon, computed: no internet, no extra package.

Sunrise and sunset follow the sunrise equation (as NOAA uses it, good to a
minute or two) for the place set in ``[weather]`` (latitude, longitude); the
moon's phase needs no place. Times are the computer's local time.
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider

SYNODIC_MONTH = 29.530588853  # days from new moon to new moon
_NEW_MOON = 2451550.1  # a new moon (6 January 2000), as a Julian date
_UNIX_EPOCH = 2440587.5  # 1 January 1970 as a Julian date
# The moon's phases in order, an eighth of a month each, the named ones centred.
MOON_PHASES = (
    "New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
    "Full moon", "Waning gibbous", "Last quarter", "Waning crescent",
)  # fmt: skip


def _julian(t: float) -> float:
    return t / 86400 + _UNIX_EPOCH


def _unix(julian: float) -> float:
    return (julian - _UNIX_EPOCH) * 86400


def _sun_path(day: date, longitude: float) -> tuple[float, float, float]:
    """Solar noon (Julian date), the sun's declination and its mean anomaly, in radians."""
    n = day.toordinal() - date(2000, 1, 1).toordinal()
    mean = n - longitude / 360
    anomaly = math.radians((357.5291 + 0.98560028 * mean) % 360)
    centre = 1.9148 * math.sin(anomaly) + 0.02 * math.sin(2 * anomaly)
    centre += 0.0003 * math.sin(3 * anomaly)
    ecliptic = math.radians((math.degrees(anomaly) + centre + 180 + 102.9372) % 360)
    noon = 2451545.0 + mean + 0.0053 * math.sin(anomaly) - 0.0069 * math.sin(2 * ecliptic)
    declination = math.asin(math.sin(ecliptic) * math.sin(math.radians(23.4397)))
    return noon, declination, anomaly


def sun_times(day: date, latitude: float, longitude: float) -> dict[str, Any]:
    """Sunrise, solar noon and sunset of ``day`` (Unix times); rise and set are
    None in a polar night or a midnight sun (``"always"`` says which)."""
    noon, declination, _ = _sun_path(day, longitude)
    phi = math.radians(latitude)
    cos_hour = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(declination)) / (
        math.cos(phi) * math.cos(declination) or 1e-9
    )
    out: dict[str, Any] = {"noon": _unix(noon), "rise": None, "set": None, "always": None}
    if cos_hour > 1:
        out["always"] = "night"
    elif cos_hour < -1:
        out["always"] = "day"
    else:
        half = math.degrees(math.acos(cos_hour)) / 360
        out["rise"], out["set"] = _unix(noon - half), _unix(noon + half)
    return out


def sun_elevation(t: float, latitude: float, longitude: float) -> float:
    """How high the sun stands, in degrees (below the horizon: negative)."""
    day = datetime.fromtimestamp(t).date()
    noon, declination, _ = _sun_path(day, longitude)
    hour_angle = math.radians(360 * (_julian(t) - noon))
    phi = math.radians(latitude)
    sine = math.sin(phi) * math.sin(declination)
    sine += math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sine))))


def moon_phase(t: float) -> float:
    """Where the moon is in its month: 0 new, 0.25 first quarter, 0.5 full, 0.75 last quarter."""
    return ((_julian(t) - _NEW_MOON) / SYNODIC_MONTH) % 1.0


def moon_name(phase: float) -> str:
    return MOON_PHASES[int(phase * 8 + 0.5) % 8]


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")


def sky_readings(
    t: float, latitude: float | None = None, longitude: float | None = None
) -> dict[str, Reading]:
    out: dict[str, Reading] = {}
    phase = moon_phase(t)
    lit = (1 - math.cos(2 * math.pi * phase)) / 2 * 100
    values: dict[str, tuple[Any, str, str]] = {
        "moon.phase": (round(phase, 4), "", "Moon phase"),
        "moon.illumination": (round(lit, 1), "%", "Moon lit"),
        "moon.name": (moon_name(phase), "", "Moon"),
        "moon.age": (round(phase * SYNODIC_MONTH, 1), "d", "Moon age"),
        "moon.full_in": (round(((0.5 - phase) % 1) * SYNODIC_MONTH, 1), "d", "Full moon in"),
        "moon.new_in": (round(((1 - phase) % 1) * SYNODIC_MONTH, 1), "d", "New moon in"),
    }
    if latitude is not None and longitude is not None:
        today = datetime.fromtimestamp(t).date()
        sun = sun_times(today, latitude, longitude)
        values["sun.noon"] = (_clock(sun["noon"]), "", "Solar noon")
        values["sun.elevation"] = (round(sun_elevation(t, latitude, longitude), 1), "°", "Sun")
        if sun["rise"] is not None:
            rise, down = sun["rise"], sun["set"]
            values["sun.rise"] = (_clock(rise), "", "Sunrise")
            values["sun.set"] = (_clock(down), "", "Sunset")
            values["sun.daylight"] = (round(down - rise), "s", "Daylight")
            up = rise <= t <= down
            values["sun.up"] = (1 if up else 0, "", "Sun up")
            values["sun.progress"] = (
                round((t - rise) / (down - rise), 4) if up else None,
                "",
                "Day passed",
            )
            tomorrow = sun_times(today + timedelta(days=1), latitude, longitude)
            if tomorrow["rise"] is not None:
                values["sun.next_rise"] = (_clock(tomorrow["rise"]), "", "Next sunrise")
        else:  # polar night or midnight sun
            values["sun.up"] = (1 if sun["always"] == "day" else 0, "", "Sun up")
            values["sun.daylight"] = (86400 if sun["always"] == "day" else 0, "s", "Daylight")
    for key, (value, unit, label) in values.items():
        out[key] = Reading(key, value, unit, label)
    return out


class SkyProvider(SensorProvider):
    """sun.* for the configured place, moon.* everywhere."""

    name = "sky"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.latitude = self.options.get("latitude")
        self.longitude = self.options.get("longitude")
        self._cache: tuple[float, dict[str, Reading]] = (-1e9, {})

    def read(self) -> dict[str, Reading]:
        now = time.time()
        if now - self._cache[0] >= 20:  # the sky moves slowly
            self._cache = (now, sky_readings(now, self.latitude, self.longitude))
        return dict(self._cache[1])

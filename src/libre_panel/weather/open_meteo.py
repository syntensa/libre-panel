"""Weather from Open-Meteo (https://open-meteo.com).

No API key needed. Open-Meteo data is CC BY 4.0 and free for non-commercial
use; themes showing weather should keep the attribution in the docs/about page.
The location always comes from the user's config; nothing is hard-coded.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from libre_panel import __version__, i18n
from libre_panel.config import WeatherConfig
from libre_panel.i18n import t
from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

_CURRENT_FIELDS = {
    "temperature_2m": ("temperature", "Temperature"),
    "apparent_temperature": ("apparent_temperature", "Feels like"),
    "relative_humidity_2m": ("humidity", "Humidity"),
    "wind_speed_10m": ("wind_speed", "Wind"),
    "weather_code": ("code", "Weather code"),
    "is_day": ("is_day", "Daylight"),
}
_HOURLY_FIELDS = {
    "temperature_2m": "temperature",
    "weather_code": "code",
    "precipitation_probability": "rain",
    "is_day": "day",
}
_DAILY_FIELDS = {
    "temperature_2m_max": "high",
    "temperature_2m_min": "low",
    "weather_code": "code",
    "precipitation_probability_max": "rain",
}
HOURS = 24  # weather.hour.1 ... weather.hour.24: the next hours
DAYS = 7  # weather.day.0 (today) ... weather.day.6
RAIN_STEP = timedelta(minutes=15)  # the steps of Open-Meteo's minutely_15 answer
RAIN_STEPS = 8  # the steps after the current one: the next two hours
RAIN_MM = 0.1  # a step with at least this much precipitation is rain

# WMO weather interpretation codes as used by Open-Meteo.
_WMO = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm, hail",
    99: "Thunderstorm, hail",
}


class WeatherError(RuntimeError):
    pass


def describe_weather_code(code: int | float | None) -> str:
    if code is None:
        return ""
    return _WMO.get(int(code), "Unknown")


def _get_json(url: str, params: dict[str, Any], timeout: float = 10) -> Any:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": f"libre-panel/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise WeatherError(str(exc)) from exc


def geocode(name: str, count: int = 5, language: str = "en") -> list[dict[str, Any]]:
    """Look up coordinates for a place name (used by ``libre-panel location``)."""
    data = _get_json(
        GEOCODING_URL, {"name": name, "count": count, "language": language, "format": "json"}
    )
    return [
        {
            "name": r.get("name", ""),
            "region": r.get("admin1", ""),
            "country": r.get("country", ""),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
        }
        for r in (data or {}).get("results", []) or []
    ]


def build_params(cfg: WeatherConfig) -> dict[str, Any]:
    params: dict[str, Any] = {
        "latitude": cfg.latitude,
        "longitude": cfg.longitude,
        "current": ",".join(_CURRENT_FIELDS),
        "hourly": ",".join(_HOURLY_FIELDS),
        "daily": ",".join(_DAILY_FIELDS),
        "minutely_15": "precipitation",  # the rain of the next hours, in 15-minute steps
        "forecast_days": DAYS,
        "timezone": "auto",
    }
    if cfg.units == "imperial":
        params["temperature_unit"] = "fahrenheit"
        params["wind_speed_unit"] = "mph"
    return params


def parse_current(data: dict[str, Any]) -> dict[str, Reading]:
    current = data.get("current") or {}
    units = data.get("current_units") or {}
    out: dict[str, Reading] = {}
    for api_key, (name, label) in _CURRENT_FIELDS.items():
        if api_key not in current:
            continue
        key = f"weather.{name}"
        unit = "" if name == "code" else units.get(api_key, "")
        out[key] = Reading(key, current[api_key], unit, label)
    if "weather.code" in out:
        text = describe_weather_code(out["weather.code"].value)
        out["weather.description"] = Reading("weather.description", text, "", "Weather")
    return out


def forecast_readings(data: dict[str, Any], now: float | None = None) -> dict[str, Reading]:
    """The forecast as readings relative to ``now``.

    ``weather.hour.<n>.temperature|code|rain|day|time`` for the next hours
    (1 = the coming full hour) and ``weather.day.<n>.high|low|code|rain|day|name``
    for today (0) and the next days, plus the rain of the next two hours (see
    ``_rain_readings``). Times and day names are local to the weather's place;
    names follow the language.
    """
    out: dict[str, Reading] = {}
    offset = timedelta(seconds=int(data.get("utc_offset_seconds") or 0))
    now_utc = datetime.fromtimestamp(time.time() if now is None else now, UTC)
    local_now = (now_utc + offset).replace(tzinfo=None)

    hourly = data.get("hourly") or {}
    hourly_units = data.get("hourly_units") or {}
    times = hourly.get("time") or []
    if times:
        try:
            first = datetime.fromisoformat(times[0])
        except ValueError:
            first = None
        if first is not None:
            current = int((local_now - first).total_seconds() // 3600)
            for n in range(1, HOURS + 1):
                index = current + n
                if not 0 <= index < len(times):
                    break
                moment = first + timedelta(hours=index)
                for api_key, name in _HOURLY_FIELDS.items():
                    values = hourly.get(api_key) or []
                    if index < len(values) and values[index] is not None:
                        key = f"weather.hour.{n}.{name}"
                        unit = (
                            hourly_units.get(api_key, "") if name in ("temperature", "rain") else ""
                        )
                        out[key] = Reading(key, values[index], unit, f"+{n} h")
                key = f"weather.hour.{n}.time"
                out[key] = Reading(key, moment.strftime("%H:%M"), "", f"+{n} h")

    daily = data.get("daily") or {}
    daily_units = data.get("daily_units") or {}
    days = daily.get("time") or []
    today = local_now.date()
    for index, day in enumerate(days):
        try:
            date = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        n = (date - today).days
        if not 0 <= n < DAYS:
            continue
        for api_key, name in _DAILY_FIELDS.items():
            values = daily.get(api_key) or []
            if index < len(values) and values[index] is not None:
                key = f"weather.day.{n}.{name}"
                unit = daily_units.get(api_key, "") if name != "code" else ""
                out[key] = Reading(key, values[index], unit, day)
        out[f"weather.day.{n}.day"] = Reading(f"weather.day.{n}.day", 1, "", day)
        label = (
            t("Today")
            if n == 0
            else i18n.format_date(datetime.combine(date, datetime.min.time()), "%a")
        )
        out[f"weather.day.{n}.name"] = Reading(f"weather.day.{n}.name", label, "", day)
    out.update(_rain_readings(data, local_now))
    return out


def _rain_readings(data: dict[str, Any], local_now: datetime) -> dict[str, Reading]:
    """The rain of the next two hours, from the 15-minute precipitation.

    ``weather.rain.minutes`` counts to the moment the rain starts (dry now) or
    stops (raining now); it is missing when neither happens in those two hours.
    ``weather.rain.text`` says it in words, and also when the rain stays or
    stays away. Nothing is said while the answer ends before the two hours do.

    A step is the rain of the 15 minutes *up to* its time (Open-Meteo: "preceding
    15 minutes sum"): the step of 14:45 is the rain from 14:30 to 14:45.
    """
    minutely = data.get("minutely_15") or {}
    times = minutely.get("time") or []
    amounts = minutely.get("precipitation") or []
    if not times:
        return {}
    try:
        first = datetime.fromisoformat(times[0])
    except ValueError:
        return {}
    # the step that holds the present moment: the first whose time is not past
    now_index = -((first - local_now) // RAIN_STEP)
    end = min(now_index + RAIN_STEPS + 1, len(times), len(amounts))
    if not 0 <= now_index < end:
        return {}
    wet = [(amounts[i] or 0) >= RAIN_MM for i in range(now_index, end)]
    raining = wet[0]
    change = next((i for i in range(1, len(wet)) if wet[i] != raining), None)
    if change is None and len(wet) <= RAIN_STEPS:
        return {}  # nothing changes yet, and the answer ends before the two hours do

    out: dict[str, Reading] = {}
    if change is not None:
        moment = first + RAIN_STEP * (now_index + change - 1)  # where the changed step begins
        minutes = math.ceil((moment - local_now) / timedelta(minutes=1))
        key = "weather.rain.minutes"
        out[key] = Reading(key, minutes, "min", "Rain starts or stops")
        if raining:
            text = t("Rain stops in {minutes} min", minutes=minutes)
        else:
            text = t("Rain in {minutes} min", minutes=minutes)
    elif raining:
        text = t("Rain for the next 2 h")
    else:
        text = t("No rain for the next 2 h")
    out["weather.rain.text"] = Reading("weather.rain.text", text, "", "Rain")
    return out


class OpenMeteoProvider(SensorProvider):
    """Fetches current weather in the background; ``read`` never blocks."""

    name = "open-meteo"

    def __init__(self, cfg: WeatherConfig) -> None:
        super().__init__({})
        if cfg.latitude is None or cfg.longitude is None:
            raise WeatherError("weather location is not configured")
        self.cfg = cfg
        self._latest: dict[str, Reading] = {}
        self._data: dict[str, Any] = {}  # the last answer, for the forecast
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="weather", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        interval = max(5, self.cfg.update_minutes) * 60
        while not self._stop.is_set():
            try:
                data = _get_json(FORECAST_URL, build_params(self.cfg))
                readings = parse_current(data)
                with self._lock:
                    self._latest = readings
                    self._data = data
                wait = interval
            except WeatherError as exc:
                log.warning("weather update failed: %s", exc)
                wait = 120  # retry sooner after an error
            self._stop.wait(wait)

    def read(self) -> dict[str, Reading]:
        with self._lock:
            out = dict(self._latest)
            data = self._data
        if data:  # relative to now, so "in 1 hour" stays true between updates
            out.update(forecast_readings(data))
        return out

    def close(self) -> None:
        self._stop.set()

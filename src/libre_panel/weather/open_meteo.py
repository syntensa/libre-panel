"""Weather from Open-Meteo (https://open-meteo.com).

No API key needed. Open-Meteo data is CC BY 4.0 and free for non-commercial
use; themes showing weather should keep the attribution in the docs/about page.
The location always comes from the user's config; nothing is hard-coded.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
from typing import Any

from libre_panel import __version__
from libre_panel.config import WeatherConfig
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
}

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


class OpenMeteoProvider(SensorProvider):
    """Fetches current weather in the background; ``read`` never blocks."""

    name = "open-meteo"

    def __init__(self, cfg: WeatherConfig) -> None:
        super().__init__({})
        if cfg.latitude is None or cfg.longitude is None:
            raise WeatherError("weather location is not configured")
        self.cfg = cfg
        self._latest: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="weather", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        interval = max(5, self.cfg.update_minutes) * 60
        while not self._stop.is_set():
            try:
                readings = parse_current(_get_json(FORECAST_URL, build_params(self.cfg)))
                with self._lock:
                    self._latest = readings
                wait = interval
            except WeatherError as exc:
                log.warning("weather update failed: %s", exc)
                wait = 120  # retry sooner after an error
            self._stop.wait(wait)

    def read(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._latest)

    def close(self) -> None:
        self._stop.set()

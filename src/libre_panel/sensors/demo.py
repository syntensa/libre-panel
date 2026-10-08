"""Deterministic fake sensors for the theme editor, previews and tests."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider, Snapshot

# key: (label, unit, base, amplitude, period in seconds)
_WAVES: dict[str, tuple[str, str, float, float, float]] = {
    "cpu.load": ("CPU load", "%", 38, 30, 17),
    "cpu.temp": ("CPU temperature", "°C", 58, 14, 29),
    "cpu.freq": ("CPU clock", "MHz", 4200, 500, 11),
    "cpu.power": ("CPU power", "W", 65, 30, 19),
    "gpu.load": ("GPU load", "%", 55, 40, 23),
    "gpu.temp": ("GPU temperature", "°C", 62, 12, 31),
    "gpu.power": ("GPU power", "W", 180, 90, 21),
    "gpu.freq": ("GPU clock", "MHz", 2400, 300, 17),
    "gpu.mem.load": ("GPU memory", "%", 40, 15, 37),
    "gpu.fan": ("GPU fan", "RPM", 1400, 400, 41),
    "mem.load": ("Memory usage", "%", 47, 6, 43),
    "mem.used": ("Memory used", "GiB", 15, 2, 43),
    "mem.total": ("Memory total", "GiB", 32, 0, 1),
    "swap.load": ("Swap usage", "%", 3, 2, 53),
    "disk.load": ("Disk usage", "%", 63, 0, 1),
    "disk.used": ("Disk used", "GiB", 600, 0, 1),
    "disk.total": ("Disk total", "GiB", 953, 0, 1),
    "disk.read": ("Disk read", "B/s", 3e6, 3e6, 13),
    "disk.write": ("Disk write", "B/s", 1.5e6, 1.5e6, 7),
    "net.down": ("Network download", "B/s", 2.4e6, 2.2e6, 9),
    "net.up": ("Network upload", "B/s", 3.1e5, 2.9e5, 12),
    "fan.cpu": ("CPU fan", "RPM", 1100, 250, 27),
    "battery.load": ("Battery", "%", 80, 0, 1),
}

_WEATHER = {
    "weather.temperature": ("Temperature", "°C", 14.0),
    "weather.apparent_temperature": ("Feels like", "°C", 12.0),
    "weather.humidity": ("Humidity", "%", 71.0),
    "weather.wind_speed": ("Wind", "km/h", 18.0),
    "weather.code": ("Weather code", "", 3.0),
    "weather.description": ("Weather", "", "Overcast"),
}


class DemoProvider(SensorProvider):
    name = "demo"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        # A fixed time gives identical frames, which tests and screenshots want.
        self.fixed_time = self.options.get("fixed_time")
        self.include_weather = self.options.get("weather", True)

    def read(self) -> dict[str, Reading]:
        t = self.fixed_time if self.fixed_time is not None else time.time()
        out = {}
        for key, (label, unit, base, amp, period) in _WAVES.items():
            value = base + amp * math.sin(2 * math.pi * t / period)
            if unit == "%":
                value = min(100.0, max(0.0, value))
            out[key] = Reading(key, value, unit, label)
        out["sys.uptime"] = Reading("sys.uptime", 3 * 86400 + 5 * 3600 + 42 * 60, "s", "Uptime")
        out["cpu.name"] = Reading("cpu.name", "8-Core Processor", "", "CPU")
        out["gpu.name"] = Reading("gpu.name", "Graphics Card", "", "GPU")
        if self.include_weather:
            for key, (label, unit, value) in _WEATHER.items():
                out[key] = Reading(key, value, unit, label)
            out.update(demo_forecast(t))
        return out


_DAILY = {  # high, low, code, rain: a week of autumn
    "temperature_2m_max": [17, 15, 13, 16, 18, 19, 14],
    "temperature_2m_min": [9, 8, 6, 7, 10, 11, 8],
    "weather_code": [2, 61, 63, 3, 1, 0, 80],
    "precipitation_probability_max": [10, 70, 80, 30, 10, 0, 60],
}
_HOURLY_CODES = [0, 0, 1, 1, 2, 2, 3, 3, 61, 61, 3, 2]


def demo_forecast(t: float) -> dict[str, Reading]:
    """A made-up forecast around ``t``, in the shape Open-Meteo sends."""
    from libre_panel.weather.open_meteo import forecast_readings

    local = datetime.fromtimestamp(t).astimezone()
    offset = local.utcoffset()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    hours = range(48)
    data = {
        "utc_offset_seconds": int(offset.total_seconds()) if offset else 0,
        "hourly": {
            "time": [(midnight + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in hours],
            "temperature_2m": [
                round(11 + 6 * math.sin(2 * math.pi * (h % 24 - 9) / 24), 1) for h in hours
            ],
            "weather_code": [_HOURLY_CODES[h % len(_HOURLY_CODES)] for h in hours],
            "precipitation_probability": [(h * 7) % 60 for h in hours],
            "is_day": [1 if 7 <= h % 24 < 19 else 0 for h in hours],
        },
        "hourly_units": {"temperature_2m": "°C", "precipitation_probability": "%"},
        "daily": {
            "time": [(midnight + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(7)],
            **_DAILY,
        },
        "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C",
                        "precipitation_probability_max": "%"},
    }  # fmt: skip
    return forecast_readings(data, t)


def demo_snapshot(fixed_time: float | None = None, samples: int = 600) -> Snapshot:
    """A complete snapshot with filled graph history, for previews and the editor."""
    provider = DemoProvider({"fixed_time": fixed_time})
    readings = provider.read()
    history = {key: demo_history(provider, key, samples) for key in _WAVES}
    now = datetime.fromtimestamp(fixed_time) if fixed_time is not None else datetime.now()
    return Snapshot(readings=readings, history=history, now=now)


def demo_history(provider: DemoProvider, key: str, samples: int, step: float = 1.0) -> list[float]:
    """Synthetic history so graphs are not empty in previews."""
    if key not in _WAVES:
        return []
    label, unit, base, amp, period = _WAVES[key]
    t0 = provider.fixed_time if provider.fixed_time is not None else time.time()
    values = []
    for i in range(samples):
        t = t0 - (samples - 1 - i) * step
        # Incommensurate slow waves plus a little flutter read like real load.
        v = (
            base
            + amp * 0.6 * math.sin(2 * math.pi * t / (period * 3.7))
            + amp * 0.35 * math.sin(2 * math.pi * t / (period * 1.3) + 1.1)
            + amp * 0.08 * math.sin(t * 0.9) * math.sin(t / 3.1)
        )
        if unit == "%":
            v = min(100.0, max(0.0, v))
        values.append(v)
    return values

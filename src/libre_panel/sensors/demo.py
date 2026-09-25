"""Deterministic fake sensors for the theme editor, previews and tests."""

from __future__ import annotations

import math
import time
from datetime import datetime
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
        if self.include_weather:
            for key, (label, unit, value) in _WEATHER.items():
                out[key] = Reading(key, value, unit, label)
        return out


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
        # Incommensurate waves read like real load instead of a clean sine.
        v = (
            base
            + amp * 0.55 * math.sin(2 * math.pi * t / (period * 3.7))
            + amp * 0.35 * math.sin(2 * math.pi * t / (period * 1.3) + 1.1)
            + amp * 0.25 * math.sin(t * 1.7) * math.sin(t / 5.3)
        )
        if unit == "%":
            v = min(100.0, max(0.0, v))
        values.append(v)
    return values

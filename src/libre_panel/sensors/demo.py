"""Deterministic fake sensors for the theme editor, previews and tests."""

from __future__ import annotations

import math
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from PIL import Image

from libre_panel.sensors.base import Reading, SensorProvider, Snapshot
from libre_panel.sensors.sky import sky_readings

DEMO_PLACE = (50.0, 10.0)  # somewhere in the middle of Europe, for the sun's times

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
    "net.ping": ("Ping", "ms", 16, 5, 23),
    "game.fps": ("Frames per second", "fps", 141, 12, 7),
    "game.frametime": ("Frame time", "ms", 7.1, 0.6, 7),
    "game.low": ("1% low", "fps", 104, 4, 19),
    "temp.cpu.ccd1": ("CPU CCD1", "°C", 52, 12, 29),
    "temp.gpu.hot_spot": ("GPU Hot Spot", "°C", 71, 12, 31),
    "temp.nvme.composite": ("NVMe SSD", "°C", 41, 3, 61),
    "temp.board.system": ("Motherboard", "°C", 34, 2, 71),
    "fan.board.case_1": ("Case fan 1", "RPM", 820, 120, 47),
    "fan.board.case_2": ("Case fan 2", "RPM", 760, 90, 53),
    "fan.board.pump": ("Pump", "RPM", 2100, 150, 59),
}
# Eight cores, each busy in its own rhythm.
for _n, (_base, _amp, _period) in enumerate(
    ((62, 30, 7), (35, 25, 11), (48, 30, 13), (20, 15, 17),
     (71, 22, 5), (28, 20, 19), (40, 35, 9), (15, 12, 23)), start=1,
):  # fmt: skip
    _WAVES[f"cpu.core.{_n}.load"] = (f"Core {_n}", "%", _base, _amp, _period)

# Copies under friendlier names: the reading they repeat (Reading.origin).
_COPIES = {
    "temp.cpu.package": ("cpu.temp", "CPU Package"),
    "temp.gpu.core": ("gpu.temp", "GPU Core"),
    "fan.gpu.fan_1": ("gpu.fan", "GPU Fan"),
    "fan.cpu.fan": ("fan.cpu", "CPU Fan"),
}
_DRIVES = (("C:", 953.0, 0.63), ("D:", 1863.0, 0.41), ("E:", 931.0, 0.87))  # name, GiB, used
_PROGRAMS = (  # name, CPU %, memory %
    ("blender", 18.4, 9.1), ("firefox", 6.2, 7.4), ("code", 3.1, 4.2), ("obs64", 2.6, 2.1),
    ("steam", 1.2, 1.9), ("discord", 0.9, 2.6), ("explorer", 0.6, 0.8), ("python", 0.4, 1.1),
)  # fmt: skip

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

    def images(self) -> dict[str, Any]:
        return {"media.cover": _cover()}

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
        out.update(demo_details(t, out))
        out.update(sky_readings(t, *DEMO_PLACE))
        out.update(demo_media(t))
        out.update(demo_calendar(t))
        out["cpu.name"] = Reading("cpu.name", "8-Core Processor", "", "CPU")
        out["gpu.name"] = Reading("gpu.name", "Graphics Card", "", "GPU")
        if self.include_weather:
            for key, (label, unit, value) in _WEATHER.items():
                out[key] = Reading(key, value, unit, label)
            out.update(demo_forecast(t))
        return out


def demo_details(t: float, waves: dict[str, Reading]) -> dict[str, Reading]:
    """Drives, processes, battery and network details, made up."""
    out: dict[str, Reading] = {}
    for key, (alias, label) in _COPIES.items():
        source = waves[alias]
        out[key] = Reading(key, source.value, source.unit, label)
        out[alias] = replace(source, origin=key)
    for n, (name, total, used) in enumerate(_DRIVES, start=1):
        values = {"name": (name, ""), "load": (used * 100, "%"), "used": (total * used, "GiB"),
                  "free": (total * (1 - used), "GiB"), "total": (total, "GiB")}  # fmt: skip
        for field, (value, unit) in values.items():
            out[f"disk.{n}.{field}"] = Reading(f"disk.{n}.{field}", value, unit, name)
    wobble = math.sin(2 * math.pi * t / 13)
    for sort, index in (("cpu", 1), ("mem", 2)):
        ranked = sorted(_PROGRAMS, key=lambda p: p[index], reverse=True)
        for n, program in enumerate(ranked, start=1):
            value = round(program[index] * (1 + 0.15 * wobble), 1)
            out[f"proc.{sort}.{n}.name"] = Reading(f"proc.{sort}.{n}.name", program[0], "", "")
            out[f"proc.{sort}.{n}.value"] = Reading(f"proc.{sort}.{n}.value", value, "%", "")
    out["battery.plugged"] = Reading("battery.plugged", 0, "", "Plugged in")
    out["battery.state"] = Reading("battery.state", "On battery", "", "Battery")
    out["battery.left"] = Reading("battery.left", 2 * 3600 + 40 * 60, "s", "Battery time left")
    out["net.ip"] = Reading("net.ip", "192.168.1.20", "", "IP address")
    out["game.app"] = Reading("game.app", "Starfall", "", "Game")
    out["net.today.down"] = Reading("net.today.down", 3.42e9, "B", "Received today")
    out["net.today.up"] = Reading("net.today.up", 6.1e8, "B", "Sent today")
    return out


def demo_media(t: float) -> dict[str, Reading]:
    """A song playing (made up)."""
    from libre_panel.sensors.media import Track

    track = Track("playing", "Midnight Drive", "The Night Owls", "Neon Roads", 83 + t % 60,
                  227.0, None, "Music", at=0.0)  # fmt: skip
    return track.readings(0.0)


def demo_calendar(t: float) -> dict[str, Reading]:
    """A few days of made-up appointments around ``t``."""
    from libre_panel.sensors.calendar import Event, calendar_readings

    now = datetime.fromtimestamp(t)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def at(days: int, hour: float, length: float) -> tuple[datetime, datetime]:
        start = today + timedelta(days=days, hours=hour)
        return start, start + timedelta(hours=length)

    events = [
        Event("Team meeting", *at(0, now.hour + 1, 1), location="Room 4"),
        Event("Pick up parcel", *at(0, 17.5, 0.5)),
        Event("Dentist", *at(1, 9.5, 1), location="Main Street 12"),
        Event("Birthday: Sam", today + timedelta(days=2), today + timedelta(days=3), True),
        Event("Football", *at(3, 18, 2)),
        Event("Dinner with friends", *at(5, 19.5, 3)),
    ]
    return calendar_readings(events, now)


def demo_cover(size: int = 320) -> Image.Image:
    """An album cover for previews: soft light on a dark gradient."""
    from PIL import ImageDraw, ImageFilter

    cover = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(cover)
    for y in range(size):
        k = y / size
        draw.line([(0, y), (size, y)], fill=(int(40 + 90 * k), int(18 + 30 * k), int(90 - 40 * k)))
    glow = Image.new("RGB", (size, size), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([size * 0.18, size * 0.5, size * 0.82, size * 1.14],
                                 fill=(255, 120, 90))  # fmt: skip
    cover = Image.blend(cover, glow.filter(ImageFilter.GaussianBlur(size / 10)), 0.45)
    draw = ImageDraw.Draw(cover)
    for i in range(6):
        y = size * (0.62 + i * 0.065)
        draw.line([(0, y), (size, y)], fill=(255, 200, 170), width=max(1, size // 120))
    return cover.convert("RGBA")


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


_COVER: list[Image.Image] = []


def _cover() -> Image.Image:
    if not _COVER:
        _COVER.append(demo_cover())
    return _COVER[0]


def demo_snapshot(fixed_time: float | None = None, samples: int = 600) -> Snapshot:
    """A complete snapshot with filled graph history, for previews and the editor."""
    provider = DemoProvider({"fixed_time": fixed_time})
    readings = provider.read()
    history = {key: demo_history(provider, key, samples) for key in _WAVES}
    now = datetime.fromtimestamp(fixed_time) if fixed_time is not None else datetime.now()
    return Snapshot(readings=readings, history=history, now=now, images=provider.images())


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

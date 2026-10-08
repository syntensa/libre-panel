"""User configuration.

Everything that is specific to one person or one machine (location, units,
which display, which sensors) lives here and nothing of it is hard-coded.
Optional features such as weather are off until the user turns them on.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"

DEFAULT_CONFIG_TOML = """\
# Libre Panel configuration
# Docs: https://github.com/syntensa/libre-panel/blob/main/docs/CONFIGURATION.md

# Name of a built-in theme or of a folder in the user themes directory.
theme = "libre-default"

# "auto" (the system language), "en" or "de": editor, tray and the date and
# weather texts on the panel.
language = "auto"

# Optional: override how often sensors are read (milliseconds); the theme sets it.
# refresh_ms = 1000

# Frames per second: values glide smoothly between sensor readings.
# 1 turns animation off (least CPU and USB traffic).
fps = 10

[device]
# Your panel. "auto" detects it; list all models with:  libre-panel models
# e.g. "turing-3.5", "turing-5", "turing-8.8-usb", "turing-9.2-usb", "turing-2.1"
model = "auto"
# "auto" uses a connected panel and otherwise writes PNG frames to `output`;
# "turzx" insists on the panel, "virtual" never touches hardware.
driver = "auto"
# 0-100
brightness = 60
# Where frames go when no panel is used (relative = in this settings folder).
output = "libre-panel-frame.png"
# Serial panels (3.5", 5", 2.1" ...) are found by their USB ids. Only if that
# picks the wrong port, name it, e.g. "COM5" or "/dev/ttyACM0".
port = ""

[sensors]
# Sensor sources, queried in this order. Available: psutil, librehardwaremonitor,
# calendar, homeassistant, mqtt, presentmon, demo, plus any installed sensor plugin (see the
# configuration guide for their settings). The sun, the moon and what is playing
# come by themselves.
providers = ["psutil"]

[sensors.librehardwaremonitor]
# Windows: enable "Remote Web Server" in LibreHardwareMonitor.
url = "http://127.0.0.1:8085/data.json"

[sensors.psutil]
# What a theme's ping connects to (host:port); only while a theme shows the ping.
# ping = "1.1.1.1:443"

[weather]
# Weather is off by default. Turn it on and set your own location.
enabled = false
provider = "open-meteo"
# Find coordinates with:  libre-panel location "Your City"
# latitude = 0.0
# longitude = 0.0
# "metric" (°C, km/h) or "imperial" (°F, mph)
units = "metric"
update_minutes = 15
"""


class ConfigError(ValueError):
    """Raised when the configuration file is invalid."""


def config_dir() -> Path:
    """Per-user directory holding config.toml and user themes.

    LIBRE_PANEL_HOME overrides it (portable installs, tests).
    """
    override = os.environ.get("LIBRE_PANEL_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "LibrePanel"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LibrePanel"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "libre-panel"


def user_themes_dir() -> Path:
    return config_dir() / "themes"


@dataclass
class VideoConfig:
    """Video mode for panels with an H.264 decoder (TURZX USB): smooth frame rates."""

    mode: str = "off"  # "off" | "on"
    fps: int = 50
    # Reported to the panel. Its player shows pictures strictly at this rate and
    # never catches up, so it must be above ``fps`` (SPUR II: 60 for 50).
    device_fps: int = 60
    crf: int = 25
    preset: str = "superfast"
    keyframe_s: float = 10.0
    maxrate: str = "2M"
    ffmpeg: str = ""  # path to ffmpeg; empty = search PATH
    # The name the video start command (110) needs: the panel's own standby
    # clip, so its standby behaviour stays as it was.
    local_clip: str = ""


@dataclass
class DeviceConfig:
    model: str = "auto"
    driver: str = "auto"
    brightness: int = 60
    output: str = "libre-panel-frame.png"
    port: str = ""  # serial panels: the port; empty = found by its USB ids
    video: VideoConfig = field(default_factory=VideoConfig)


@dataclass
class SensorsConfig:
    providers: list[str] = field(default_factory=lambda: ["psutil"])
    # Provider-specific tables, e.g. {"librehardwaremonitor": {"url": "..."}}
    options: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class WeatherConfig:
    enabled: bool = False
    provider: str = "open-meteo"
    latitude: float | None = None
    longitude: float | None = None
    units: str = "metric"
    update_minutes: int = 15


@dataclass
class ServicesConfig:
    """Service plugins to run, and their options ([services.<name>])."""

    enabled: list[str] = field(default_factory=list)
    options: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ModeConfig:
    """A mode a service can switch to, e.g. [modes.game] with fewer frames."""

    fps: int | None = None
    theme: str | None = None


@dataclass
class Config:
    theme: str = "libre-default"
    language: str = "auto"
    refresh_ms: int | None = None
    fps: int = 10
    transition: str = "fade"  # between themes: cut, fade, slide, or one from a plugin
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    path: Path | None = None
    # config.toml's modification time when it was read: a change made while
    # the program starts up is noticed too.
    stamp: int | None = None


def _expect(value: Any, kind: type | tuple[type, ...], name: str) -> Any:
    # bool is an int subclass; never accept it where a number is expected.
    if isinstance(value, bool) and kind is not bool:
        raise ConfigError(f"{name}: expected {kind}, got a boolean")
    if not isinstance(value, kind):
        raise ConfigError(f"{name}: expected {kind}, got {type(value).__name__}")
    return value


def parse_config(data: dict[str, Any], path: Path | None = None) -> Config:
    cfg = Config(path=path)
    if "theme" in data:
        cfg.theme = _expect(data["theme"], str, "theme")
    if "language" in data:
        cfg.language = _expect(data["language"], str, "language")
        if cfg.language not in ("auto", "en", "de"):
            raise ConfigError('language: must be "auto", "en" or "de"')
    if "fps" in data:
        cfg.fps = _expect(data["fps"], int, "fps")
        if not 1 <= cfg.fps <= 60:
            raise ConfigError("fps: must be between 1 and 60")
    if "transition" in data:
        cfg.transition = _expect(data["transition"], str, "transition")
    if "refresh_ms" in data:
        cfg.refresh_ms = _expect(data["refresh_ms"], int, "refresh_ms")
        if cfg.refresh_ms < 100:
            raise ConfigError("refresh_ms: must be at least 100")

    dev = data.get("device", {})
    cfg.device.model = _expect(dev.get("model", cfg.device.model), str, "device.model")
    if cfg.device.model != "auto":
        from libre_panel.devices.models import find_model

        if find_model(cfg.device.model) is None:
            raise ConfigError(
                f"device.model: unknown panel {cfg.device.model!r} (see: libre-panel models)"
            )
    cfg.device.driver = _expect(dev.get("driver", cfg.device.driver), str, "device.driver")
    cfg.device.brightness = _expect(
        dev.get("brightness", cfg.device.brightness), int, "device.brightness"
    )
    if not 0 <= cfg.device.brightness <= 100:
        raise ConfigError("device.brightness: must be between 0 and 100")
    cfg.device.output = _expect(dev.get("output", cfg.device.output), str, "device.output")
    cfg.device.port = _expect(dev.get("port", cfg.device.port), str, "device.port").strip()
    cfg.device.video = _parse_video(_expect(data.get("video", {}), dict, "video"))

    sensors = dict(data.get("sensors", {}))
    providers = sensors.pop("providers", cfg.sensors.providers)
    cfg.sensors.providers = [_expect(p, str, "sensors.providers[]") for p in providers]
    cfg.sensors.options = {k: v for k, v in sensors.items() if isinstance(v, dict)}

    w = data.get("weather", {})
    cfg.weather.enabled = _expect(w.get("enabled", False), bool, "weather.enabled")
    cfg.weather.provider = _expect(w.get("provider", "open-meteo"), str, "weather.provider")
    for key in ("latitude", "longitude"):
        if key in w:
            setattr(cfg.weather, key, float(_expect(w[key], (int, float), f"weather.{key}")))
    cfg.weather.units = _expect(w.get("units", "metric"), str, "weather.units")
    if cfg.weather.units not in ("metric", "imperial"):
        raise ConfigError('weather.units: must be "metric" or "imperial"')
    cfg.weather.update_minutes = _expect(w.get("update_minutes", 15), int, "weather.update_minutes")
    services = dict(_expect(data.get("services", {}), dict, "services"))
    enabled = _expect(services.pop("enabled", []), list, "services.enabled")
    cfg.services.enabled = [_expect(name, str, "services.enabled[]") for name in enabled]
    cfg.services.options = {k: v for k, v in services.items() if isinstance(v, dict)}

    for name, table in _expect(data.get("modes", {}), dict, "modes").items():
        table = _expect(table, dict, f"modes.{name}")
        mode = ModeConfig()
        if "fps" in table:
            mode.fps = _expect(table["fps"], int, f"modes.{name}.fps")
            if not 1 <= mode.fps <= 60:
                raise ConfigError(f"modes.{name}.fps: must be between 1 and 60")
        if "theme" in table:
            mode.theme = _expect(table["theme"], str, f"modes.{name}.theme")
        cfg.modes[name] = mode

    if cfg.weather.enabled and (cfg.weather.latitude is None or cfg.weather.longitude is None):
        raise ConfigError(
            "weather is enabled but no location is set: add weather.latitude and "
            'weather.longitude (find them with: libre-panel location "Your City")'
        )
    return cfg


_MAXRATE = re.compile(r"^[0-9]+(\.[0-9]+)?[kKM]?$")


def _parse_video(v: dict[str, Any]) -> VideoConfig:
    from libre_panel.devices.h264 import X264_PRESETS

    video = VideoConfig()
    video.mode = _expect(v.get("mode", video.mode), str, "video.mode")
    if video.mode not in ("off", "on"):
        raise ConfigError('video.mode: must be "off" or "on"')
    video.fps = _expect(v.get("fps", video.fps), int, "video.fps")
    if not 1 <= video.fps <= 60:
        raise ConfigError("video.fps: must be between 1 and 60")
    video.device_fps = _expect(v.get("device_fps", video.device_fps), int, "video.device_fps")
    if not video.fps <= video.device_fps <= 120:
        raise ConfigError("video.device_fps: must be at least video.fps and at most 120")
    video.crf = _expect(v.get("crf", video.crf), int, "video.crf")
    if not 0 <= video.crf <= 51:
        raise ConfigError("video.crf: must be between 0 and 51")
    video.preset = _expect(v.get("preset", video.preset), str, "video.preset")
    if video.preset not in X264_PRESETS:
        raise ConfigError(f"video.preset: must be one of {', '.join(X264_PRESETS)}")
    video.keyframe_s = float(
        _expect(v.get("keyframe_s", video.keyframe_s), (int, float), "video.keyframe_s")
    )
    if not 1 <= video.keyframe_s <= 60:
        raise ConfigError("video.keyframe_s: must be between 1 and 60 seconds")
    video.maxrate = _expect(v.get("maxrate", video.maxrate), str, "video.maxrate")
    if not _MAXRATE.match(video.maxrate):
        raise ConfigError('video.maxrate: a bit rate such as "2M" or "1500k"')
    video.ffmpeg = _expect(v.get("ffmpeg", video.ffmpeg), str, "video.ffmpeg")
    video.local_clip = _expect(v.get("local_clip", video.local_clip), str, "video.local_clip")
    if len(video.local_clip.encode("utf-8")) > 200:
        raise ConfigError("video.local_clip: at most 200 bytes")
    return video


def load_config(path: Path | None = None) -> Config:
    """Load config.toml; a missing file means all defaults."""
    path = path or config_dir() / CONFIG_FILENAME
    try:
        stamp = path.stat().st_mtime_ns  # before reading: a later change is newer
    except OSError:
        return Config(path=path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    config = parse_config(data, path)
    config.stamp = stamp
    return config


def write_default_config(path: Path | None = None, overwrite: bool = False) -> Path:
    path = path or config_dir() / CONFIG_FILENAME
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # a JSON string is a valid TOML basic string
    raise TypeError(f"cannot write {type(value).__name__} to config.toml")


_HEADER = re.compile(r"^[ \t]*\[", re.MULTILINE)
# [table] or [table.sub] (not [[array]]); the name comes back without spaces around dots
_TABLE = re.compile(
    r"^[ \t]*\[[ \t]*([A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*)[ \t]*\][ \t]*(?:#.*)?$",
    re.MULTILINE,
)


def _trailing_comment(line: str) -> str:
    """The ``  # ...`` after a ``key = value`` line's value, or ""."""
    quote = None
    i = line.index("=") + 1
    while i < len(line):
        char = line[i]
        if quote:
            if char == "\\" and quote == '"':
                i += 1  # an escaped character, maybe a quote
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[len(line[:i].rstrip()) :]
        i += 1
    return ""


def _with_key(text: str, table: str | None, key: str, literal: str) -> str:
    line = f"{key} = {literal}"
    if table is None:  # top-level keys come before the first [table] header
        first = _HEADER.search(text)
        start, end = 0, first.start() if first else len(text)
    else:
        header = next(
            (m for m in _TABLE.finditer(text) if re.sub(r"[ \t]", "", m.group(1)) == table), None
        )
        if header is None:
            return text.rstrip("\n") + f"\n\n[{table}]\n{line}\n"
        start = text.find("\n", header.end()) + 1 or len(text)
        following = _HEADER.search(text, start)
        end = following.start() if following else len(text)
    body = text[start:end]
    pattern = re.compile(rf"^([ \t]*){re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    body, count = pattern.subn(
        lambda m: m.group(1) + line + _trailing_comment(m.group(0)), body, count=1
    )
    if not count:
        body = line + "\n" + body
    return text[:start] + body + text[end:]


def set_config_value(name: str, value: Any, path: Path | None = None) -> Path:
    """Change one setting (``"theme"``, ``"device.brightness"``,
    ``"modes.game.theme"``, ...) in config.toml.

    The rest of the file, comments included, stays as it is. The new file is
    checked before it is written (atomically), so a mistake never leaves a
    config behind that would not load.
    """
    path = path or config_dir() / CONFIG_FILENAME
    if not path.exists():
        write_default_config(path)
    table, _, key = name.rpartition(".")
    text = _with_key(path.read_text(encoding="utf-8"), table or None, key, _toml_literal(value))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: could not set {name} ({exc}); please edit it by hand") from exc
    written: Any = data
    for part in table.split(".") if table else ():
        written = written.get(part, {}) if isinstance(written, dict) else {}
    if not isinstance(written, dict) or written.get(key) != value:
        raise ConfigError(f"{path}: could not set {name}; please edit it by hand")
    parse_config(data, path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def set_active_theme(name: str, path: Path | None = None) -> Path:
    """Point config.toml at another theme, keeping the rest of the file (and comments)."""
    return set_config_value("theme", name, path)


def set_brightness(percent: int, path: Path | None = None) -> Path:
    return set_config_value("device.brightness", int(percent), path)

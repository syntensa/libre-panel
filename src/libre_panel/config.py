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

# Optional: override the theme's refresh interval (milliseconds).
# refresh_ms = 1000

[device]
# Your panel. "auto" detects it; list all models with:  libre-panel models
# e.g. "turing-3.5", "turing-5", "turing-8.8-usb", "turing-9.2-usb", "turing-2.1"
model = "auto"
# "auto" uses a connected panel and otherwise writes PNG frames to `output`;
# "turzx" insists on the panel, "virtual" never touches hardware.
driver = "auto"
# 0-100
brightness = 60
# Where frames go when no panel is used.
output = "libre-panel-frame.png"

[sensors]
# Sensor sources, queried in this order. Available: psutil, librehardwaremonitor, demo,
# plus any installed sensor plugin.
providers = ["psutil"]

[sensors.librehardwaremonitor]
# Windows: enable "Remote Web Server" in LibreHardwareMonitor.
url = "http://127.0.0.1:8085/data.json"

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
class DeviceConfig:
    model: str = "auto"
    driver: str = "auto"
    brightness: int = 60
    output: str = "libre-panel-frame.png"


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
class Config:
    theme: str = "libre-default"
    refresh_ms: int | None = None
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    path: Path | None = None


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
    if cfg.weather.enabled and (cfg.weather.latitude is None or cfg.weather.longitude is None):
        raise ConfigError(
            "weather is enabled but no location is set: add weather.latitude and "
            'weather.longitude (find them with: libre-panel location "Your City")'
        )
    return cfg


def load_config(path: Path | None = None) -> Config:
    """Load config.toml; a missing file means all defaults."""
    path = path or config_dir() / CONFIG_FILENAME
    if not path.exists():
        return Config(path=path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse_config(data, path)


def write_default_config(path: Path | None = None, overwrite: bool = False) -> Path:
    path = path or config_dir() / CONFIG_FILENAME
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path


_THEME_LINE = re.compile(r"^theme\s*=.*$", re.MULTILINE)


def set_active_theme(name: str, path: Path | None = None) -> Path:
    """Point config.toml at another theme, keeping the rest of the file (and comments)."""
    path = path or config_dir() / CONFIG_FILENAME
    if not path.exists():
        write_default_config(path)
    text = path.read_text(encoding="utf-8")
    line = f"theme = {json.dumps(name)}"  # a JSON string is a valid TOML basic string
    # Only top-level keys count: everything before the first [table] header.
    table = re.search(r"^\s*\[", text, re.MULTILINE)
    head, tail = (text[: table.start()], text[table.start() :]) if table else (text, "")
    head, count = _THEME_LINE.subn(line, head, count=1)
    text = (head if count else line + "\n" + head) + tail
    parse_config(tomllib.loads(text), path)  # never write a config that would not load
    path.write_text(text, encoding="utf-8")
    return path

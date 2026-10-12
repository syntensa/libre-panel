"""User configuration.

Everything that is specific to one person or one machine (location, units,
which display, which sensors) lives here and nothing of it is hard-coded.
Optional features such as weather are off until the user turns them on.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import time
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from libre_panel.sequence import Step, StepError, parse_steps

CONFIG_FILENAME = "config.toml"
# The theme of every panel but the bars, and what ``Config.theme`` is where nothing better is
# known (libre_panel.theme.defaults has the panel's own default).
DEFAULT_THEME = "libre-default"

_DEFAULT_CONFIG_TEMPLATE = """\
# Libre Panel configuration
# Docs: https://github.com/syntensa/libre-panel/blob/main/docs/CONFIGURATION.md

# The theme on the panel: a built-in theme or a folder in the user themes directory.
# Without a theme line each panel shows its own default: Lumen on the 1920x480 bars (the
# 8.8" and 9.2"), Libre Default on every other panel. To pick another, take out the # and
# put its name there (or choose one in the app, on Start).
# theme = "slate"

# Screens in turn: themes one after another, each for its seconds, minutes or hours, and
# from the top again after the last (or set them up in the app: Start, Screens in turn).
# While it is on it stands in for the theme above; a game's theme and the autopilot go first.
# [sequence]
# on = true
# [[sequence.steps]]
# theme = "almanac"
# minutes = 5
# [[sequence.steps]]
# theme = "studio"
# minutes = 10

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
# 180 shows the picture upside down, for a panel mounted the other way round.
rotate = 0
# Where frames go when no panel is used (relative = in this settings folder).
output = "libre-panel-frame.png"
# Serial panels (3.5", 5", 2.1" ...) are found by their USB ids. Only if that
# picks the wrong port, name it, e.g. "COM5" or "/dev/ttyACM0".
port = ""

[video]
# TURZX USB panels: the whole picture as H.264 video, up to 50 frames a second
# instead of about 9. "auto" turns it on where it is proven (the 9.2") once ffmpeg
# is found and local_clip names the panel's standby clip (see the configuration
# guide, Video); "on" and "off" decide by themselves.
# mode = "auto"
# local_clip = ""
# The app asks for that name once, on Start (the tray points to it), and sets this to true when
# it was answered, whichever way. Put false to be asked again.
# clip_asked = false

[sensors]
# Sensor sources, queried in this order: for each reading the first source that
# has one wins. Available: load (CPU and GPU load ten times a second), audio (the
# volume and mute of the default output: Windows, Linux with wpctl or pactl),
# psutil, librehardwaremonitor (Windows: GPU, temperatures, power, fans), linux
# (Linux: temperatures, fans, power and the graphics card, read from the kernel
# and NVIDIA's driver), calendar, homeassistant, mqtt, presentmon, demo, plus any
# installed sensor plugin (see the configuration guide for their settings). load
# and audio work only while a theme or a service shows their readings. The sun,
# the moon and what is playing come by themselves.
providers = @PROVIDERS@

[sensors.librehardwaremonitor]
# Windows: the GPU, temperatures, power and fans come from LibreHardwareMonitor. The
# Windows downloads read it inside Libre Panel, no other program is needed. The CPU's
# temperature and power also need Libre Panel to run as administrator
# (libre-panel autostart enable --elevated) and the PawnIO driver (https://pawnio.eu).
# "auto" reads it inside Libre Panel where it can and else asks the web server of a
# LibreHardwareMonitor that you run yourself; "inprocess" and "webserver" choose. With a
# pip install, inside needs pip install "libre-panel[lhm]" and dll_dir: the folder with
# LibreHardwareMonitor's .NET libraries.
# mode = "auto"
# dll_dir = ""
# The web server of LibreHardwareMonitor, at url:
# Run it as administrator and turn on Options -> Remote Web Server -> Run.
# While it does not run those readings show "--" and nothing else is lost.
url = "http://127.0.0.1:8085/data.json"
# Without an answer for this many seconds its readings disappear ("--" on the
# panel) instead of staying as they were; 0 keeps the last ones.
# stale_seconds = 5

[sensors.linux]
# Linux: with several graphics cards, the one whose name has this in it gives the gpu.*
# readings ("RX 7900", "RTX 4070"); by default the one gpu.name names. Libre Panel reads
# NVIDIA cards through the driver's library (NVML); "off" never loads it.
# gpu = ""
# nvidia = "auto"

[sensors.psutil]
# What a theme's ping connects to (host:port); only while a theme shows the ping.
# ping = "1.1.1.1:443"

[hardware]
# The names a theme shows for the processor (cpu.name), the graphics card (gpu.name) and
# the memory (mem.name). Libre Panel finds them itself and shortens them: "Ryzen 5 7600",
# "GeForce RTX 4070", "Kingston DDR5-5600 32 GB". Text of your own is shown instead, as
# you write it; empty means as found. The names stay on this computer.
# cpu_name = ""
# gpu_name = ""
# memory_name = ""
# How the fans turn on the panel, where a theme leaves it to you: the look of their rotor,
# "noctua-g2" (Noctua's NF-A12x25 / NF-A14x25 G2, the default), "noctua-a12x25" (the first
# NF-A12x25), "noctua-a14" (the first 140 mm, NF-A14) or "libre" (Libre Panel's own), and
# their colours, "theme" (the theme's) or "own" (the look's: Noctua's brown and beige).
# fan_look = "noctua-g2"
# fan_paint = "theme"
# Names of your own for the fans, by the sensor that measures each (the app sets them in
# Settings -> Sensors); without one a fan goes by the name FanControl gives it, else by
# the sensor's own ("Fan #1"). A fan may have a look and colours of its own too.
# [[hardware.fans]]
# sensor = "fan.lpc.fan_1"
# name = "Intake"
# look = "noctua-a12x25"
# paint = "own"

[weather]
# Weather is off by default. Turn it on and set your own location.
enabled = false
provider = "open-meteo"
# Find coordinates with:  libre-panel location "Your City"  (or in the app: Settings)
# latitude = 0.0
# longitude = 0.0
# What the app calls the place (the app writes it when you pick one).
# place = ""
# "metric" (°C, km/h) or "imperial" (°F, mph)
units = "metric"
update_minutes = 15
"""


def default_providers(platform: str | None = None) -> list[str]:
    """The sensor sources of a config that names none, in order of priority: the fast
    load source; the volume where it can be read (Windows, and Linux through ``wpctl`` or
    ``pactl``; it reads only while a theme or a service shows it); on Windows
    LibreHardwareMonitor, which alone has the GPU, temperatures, power and fans there (and
    costs nothing while it is not running), and PresentMon for a game's frame rate; on Linux
    ``linux``, which reads them from the kernel and NVIDIA's driver; psutil."""
    where = platform or sys.platform
    sources = ["load"]
    if where == "win32" or where.startswith("linux"):
        sources.append("audio")
    if where == "win32":
        # PresentMon (in the downloads) runs only while a theme shows a game's frame rate
        sources += ["librehardwaremonitor", "presentmon"]
    elif where.startswith("linux"):
        sources.append("linux")
    return [*sources, "psutil"]


def default_config_toml(platform: str | None = None) -> str:
    """The commented config.toml that ``config init`` and the first change write."""
    return _DEFAULT_CONFIG_TEMPLATE.replace("@PROVIDERS@", json.dumps(default_providers(platform)))


DEFAULT_CONFIG_TOML = default_config_toml()

# What every config.toml written up to 0.4 named: the default then, not a choice,
# so it means today's default (a Windows panel gets its GPU and temperatures).
_OLD_DEFAULT_PROVIDERS = ["psutil"]
# ... and what the builds of 0.5 wrote on Linux before it had a source of its own.
_EARLIER_LINUX_PROVIDERS = ["load", "audio", "psutil"]


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
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "libre-panel"


def user_themes_dir() -> Path:
    return config_dir() / "themes"


LOG_FILENAME = "libre-panel.log"


def log_file() -> Path:
    """The log of the tray app (its "Open log"); the editor's pages read it too."""
    return config_dir() / "logs" / LOG_FILENAME


@dataclass
class VideoConfig:
    """Video mode for panels with an H.264 decoder (TURZX USB): smooth frame rates."""

    # "auto": on where it is proven (the 9.2"), ffmpeg is found and local_clip is set
    mode: str = "auto"  # "auto" | "off" | "on"
    fps: int = 50
    # Reported to the panel. Its player shows pictures strictly at this rate and
    # never catches up, so it must be above ``fps`` (SPUR II: 60 for 50).
    device_fps: int = 60
    crf: int = 25
    preset: str = "superfast"
    keyframe_s: float = 10.0
    maxrate: str = "2M"
    ffmpeg: str = ""  # path to ffmpeg; empty = the download's own (bundled.py), else PATH
    # The name the video start command (110) needs: the panel's own standby
    # clip, so its standby behaviour stays as it was.
    local_clip: str = ""
    # Whether the app has asked for ``local_clip`` and been answered, yes or no. A note for the
    # app, not a setting of the panel: a change of it leaves the open panel as it is.
    clip_asked: bool = field(default=False, compare=False)


@dataclass
class DeviceConfig:
    model: str = "auto"
    driver: str = "auto"
    brightness: int = 60
    rotate: int = 0  # 180: upside down, for a panel mounted the other way round
    output: str = "libre-panel-frame.png"
    port: str = ""  # serial panels: the port; empty = found by its USB ids
    video: VideoConfig = field(default_factory=VideoConfig)


@dataclass
class SensorsConfig:
    # looked up when a config is made, not once at import
    providers: list[str] = field(default_factory=lambda: default_providers())
    # Provider-specific tables, e.g. {"librehardwaremonitor": {"url": "..."}}
    options: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class HardwareConfig:
    """The user's own names for the hardware (``[hardware]``); empty: the one found."""

    cpu_name: str = ""
    gpu_name: str = ""
    memory_name: str = ""
    # the fans' own names, by the key of the sensor that measures each ([[hardware.fans]])
    fans: dict[str, str] = field(default_factory=dict)
    # how the fans turn on the panel (render/fan_looks.py): a look and colours for all ("":
    # the default), and for some fans by their sensor ([[hardware.fans]] look, paint)
    fan_look: str = ""
    fan_paint: str = ""
    fan_looks: dict[str, str] = field(default_factory=dict)
    fan_paints: dict[str, str] = field(default_factory=dict)


@dataclass
class WeatherConfig:
    enabled: bool = False
    provider: str = "open-meteo"
    latitude: float | None = None
    longitude: float | None = None
    place: str = ""  # what the app calls the place; the coordinates are what counts
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
class SequenceConfig:
    """Screens in turn (libre_panel.sequence): the steps, and whether they show."""

    on: bool = True
    steps: list[Step] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.on and bool(self.steps)


@dataclass
class Config:
    # The theme config.toml names; without one the panel's own default shows, and this is
    # only the fallback (see ``theme_given``).
    theme: str = DEFAULT_THEME
    language: str = "auto"
    refresh_ms: int | None = None
    fps: int = 10
    transition: str = "fade"  # between themes: cut, fade, slide, tube, entrance, or a plugin's
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    sequence: SequenceConfig = field(default_factory=SequenceConfig)
    path: Path | None = None
    # config.toml's modification time when it was read: a change made while
    # the program starts up is noticed too.
    stamp: int | None = None
    # Whether config.toml (or --theme) names a theme. A config made in code that is left to
    # say (None) names one when it passes a theme of its own.
    theme_given: bool | None = None

    def __post_init__(self) -> None:
        if self.theme_given is None:
            self.theme_given = self.theme != DEFAULT_THEME


def _expect(value: Any, kind: type | tuple[type, ...], name: str) -> Any:
    # bool is an int subclass; never accept it where a number is expected.
    if isinstance(value, bool) and kind is not bool:
        raise ConfigError(f"{name}: expected {kind}, got a boolean")
    if not isinstance(value, kind):
        raise ConfigError(f"{name}: expected {kind}, got {type(value).__name__}")
    return value


HARDWARE_NAME_LENGTH = 100


def hardware_name(value: Any, name: str) -> str:
    """The text of a ``[hardware]`` setting as a heading shows it: on one line, without the
    spaces around it (empty means "as found")."""
    text = " ".join(_expect(value, str, name).split())
    text = "".join(char for char in text if unicodedata.category(char) != "Cc")
    if len(text) > HARDWARE_NAME_LENGTH:
        raise ConfigError(f"{name}: at most {HARDWARE_NAME_LENGTH} characters")
    return text


# The looks of a fan's rotor (render/fan_looks.py has them) and the colours it may take
FAN_LOOKS = ("noctua-g2", "noctua-a12x25", "noctua-a14", "libre")
FAN_PAINTS = ("theme", "own")


def _choice(value: Any, choices: tuple[str, ...], name: str) -> str:
    """One of ``choices``, or "" (as the theme or the default says)."""
    text = _expect(value, str, name).strip()
    if text and text not in choices:
        raise ConfigError(f"{name}: one of {', '.join(choices)} (or empty)")
    return text


def _fans(value: Any) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """``[[hardware.fans]]``: each ``sensor`` (a key such as ``fan.lpc.fan_1``) with its
    ``name``, ``look`` and ``paint``, each by itself (one that is empty is left out)."""
    names: dict[str, str] = {}
    looks: dict[str, str] = {}
    paints: dict[str, str] = {}
    for n, table in enumerate(_expect(value, list, "hardware.fans")):
        where = f"hardware.fans[{n}]"
        table = _expect(table, dict, where)
        sensor = _expect(table.get("sensor", ""), str, f"{where}.sensor").strip()
        if not sensor:
            raise ConfigError(f"{where}.sensor: the key of the fan's sensor, e.g. fan.lpc.fan_1")
        name = hardware_name(table.get("name", ""), f"{where}.name")
        look = _choice(table.get("look", ""), FAN_LOOKS, f"{where}.look")
        paint = _choice(table.get("paint", ""), FAN_PAINTS, f"{where}.paint")
        for kept, given in ((names, name), (looks, look), (paints, paint)):
            if given:
                kept[sensor] = given
    return names, looks, paints


def parse_config(data: dict[str, Any], path: Path | None = None) -> Config:
    cfg = Config(path=path)
    if "theme" in data:
        cfg.theme = _expect(data["theme"], str, "theme")
        cfg.theme_given = True
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

    dev = _expect(data.get("device", {}), dict, "device")
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
    cfg.device.rotate = _expect(dev.get("rotate", cfg.device.rotate), int, "device.rotate")
    if cfg.device.rotate not in (0, 180):
        raise ConfigError("device.rotate: must be 0, or 180 for upside down")
    cfg.device.output = _expect(dev.get("output", cfg.device.output), str, "device.output")
    cfg.device.port = _expect(dev.get("port", cfg.device.port), str, "device.port").strip()
    cfg.device.video = _parse_video(_expect(data.get("video", {}), dict, "video"))

    sensors = dict(_expect(data.get("sensors", {}), dict, "sensors"))
    providers = _expect(sensors.pop("providers", cfg.sensors.providers), list, "sensors.providers")
    cfg.sensors.providers = [_expect(p, str, "sensors.providers[]") for p in providers]
    if cfg.sensors.providers == _OLD_DEFAULT_PROVIDERS or (
        sys.platform.startswith("linux") and cfg.sensors.providers == _EARLIER_LINUX_PROVIDERS
    ):
        cfg.sensors.providers = default_providers()
    cfg.sensors.options = {k: v for k, v in sensors.items() if isinstance(v, dict)}

    hw = _expect(data.get("hardware", {}), dict, "hardware")
    names, looks, paints = _fans(hw.get("fans", []))
    cfg.hardware = HardwareConfig(
        cpu_name=hardware_name(hw.get("cpu_name", ""), "hardware.cpu_name"),
        gpu_name=hardware_name(hw.get("gpu_name", ""), "hardware.gpu_name"),
        memory_name=hardware_name(hw.get("memory_name", ""), "hardware.memory_name"),
        fans=names,
        fan_look=_choice(hw.get("fan_look", ""), FAN_LOOKS, "hardware.fan_look"),
        fan_paint=_choice(hw.get("fan_paint", ""), FAN_PAINTS, "hardware.fan_paint"),
        fan_looks=looks,
        fan_paints=paints,
    )

    w = _expect(data.get("weather", {}), dict, "weather")
    cfg.weather.enabled = _expect(w.get("enabled", False), bool, "weather.enabled")
    cfg.weather.provider = _expect(w.get("provider", "open-meteo"), str, "weather.provider")
    for key in ("latitude", "longitude"):
        if key in w:
            setattr(cfg.weather, key, float(_expect(w[key], (int, float), f"weather.{key}")))
    cfg.weather.place = _expect(w.get("place", ""), str, "weather.place")
    if len(cfg.weather.place) > 120:
        raise ConfigError("weather.place: at most 120 characters")
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

    sequence = _expect(data.get("sequence", {}), dict, "sequence")
    cfg.sequence.on = _expect(sequence.get("on", True), bool, "sequence.on")
    try:
        cfg.sequence.steps = parse_steps(sequence.get("steps", []))
    except StepError as exc:
        raise ConfigError(str(exc)) from exc

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
    if video.mode not in ("auto", "off", "on"):
        raise ConfigError('video.mode: must be "auto", "off" or "on"')
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
    video.clip_asked = _expect(v.get("clip_asked", video.clip_asked), bool, "video.clip_asked")
    return video


def _read(path: Path) -> str:
    """config.toml's text. One saved in another encoding (an old editor's) is said as an
    error of the config, which the app shows, like any other."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not UTF-8 text (byte {exc.start}); save it as UTF-8") from exc


def load_config(path: Path | None = None) -> Config:
    """Load config.toml; a missing file means all defaults."""
    path = path or config_dir() / CONFIG_FILENAME
    try:
        stamp = path.stat().st_mtime_ns  # before reading: a later change is newer
    except OSError:
        return Config(path=path)
    try:
        data = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    config = parse_config(data, path)
    config.stamp = stamp
    return config


def theme_chosen(path: Path | None = None) -> bool:
    """Whether config.toml names a theme itself: a first start has no file, or none with a
    ``theme`` line yet, and the app asks which look the panel should have."""
    path = path or config_dir() / CONFIG_FILENAME
    try:
        return "theme" in tomllib.loads(_read(path))
    except (OSError, ConfigError, tomllib.TOMLDecodeError):
        return False


def write_default_config(path: Path | None = None, overwrite: bool = False) -> Path:
    path = path or config_dir() / CONFIG_FILENAME
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_config_toml(), encoding="utf-8")
    return path


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_string(text: str) -> str:
    # a JSON string is a valid TOML basic string (DEL is the one character it leaves bare)
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(key: Any) -> str:
    if not isinstance(key, str):
        raise TypeError(f"keys in config.toml are text, not {type(key).__name__}")
    return key if _BARE_KEY.match(key) else _toml_string(key)


def _toml_literal(value: Any) -> str:
    """``value`` as TOML: a boolean, number or string, a list of such values, or an inline
    table (``{ music = true, apps = ["a.exe"] }``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = (f"{_toml_key(key)} = {_toml_literal(item)}" for key, item in value.items())
        return "{ " + ", ".join(pairs) + " }" if value else "{}"
    raise TypeError(f"cannot write {type(value).__name__} to config.toml")


_HEADER = re.compile(r"^[ \t]*\[", re.MULTILINE)
# [table] or [table.sub] (not [[array]]); the name comes back without spaces around dots
_TABLE = re.compile(
    r"^[ \t]*\[[ \t]*([A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*)[ \t]*\][ \t]*(?:#.*)?$",
    re.MULTILINE,
)
# [table] or [[array.of.tables]]: the brackets come back with the name
_ANY_HEADER = re.compile(
    r"^[ \t]*(\[\[?)[ \t]*([A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*)[ \t]*\]\]?"
    r"[ \t]*(?:#.*)?$",
    re.MULTILINE,
)


def _string_end(text: str, i: int) -> int:
    """Just past the string that opens at ``i``: on one line, or over several when it
    opens with three quotes."""
    quote = text[i]
    if text.startswith(quote * 3, i):
        j = i + 3
        while j < len(text) and not text.startswith(quote * 3, j):
            j += 2 if quote == '"' and text[j] == "\\" else 1
        return min(j + 3, len(text))
    j = i + 1
    while j < len(text) and text[j] != quote and text[j] != "\n":
        j += 2 if quote == '"' and text[j] == "\\" else 1
    return j + 1 if j < len(text) and text[j] == quote else j


def _value_end(text: str, i: int) -> int:
    """Where the TOML value that begins at ``i`` ends: just past its last character, over
    as many lines as an array or an inline table takes. What follows on the line (spaces
    and a comment) is not part of it."""
    while i < len(text) and text[i] in " \t":
        i += 1
    depth = 0
    while i < len(text):
        char = text[i]
        if char in "\"'":
            i = _string_end(text, i)
            if depth == 0:
                return i
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth <= 0:
                return i + 1
        elif char == "#" and depth:  # a comment inside an array runs to the end of its line
            i = text.find("\n", i)
            if i < 0:
                return len(text)
            continue
        elif depth == 0 and char in " \t\n#":
            return i
        i += 1
    return i


def _table_body(text: str, table: str | None) -> tuple[int, int] | None:
    """Where the keys of ``table`` are (``None``: the top level, before the first
    header): the start of their first line and of the header that follows. None when
    there is no such table."""
    if table is None:
        first = _HEADER.search(text)
        return 0, first.start() if first else len(text)
    header = next(
        (m for m in _TABLE.finditer(text) if re.sub(r"[ \t]", "", m.group(1)) == table), None
    )
    if header is None:
        return None
    start = text.find("\n", header.end()) + 1 or len(text)
    following = _HEADER.search(text, start)
    return start, following.start() if following else len(text)


def _key_span(text: str, body: tuple[int, int], key: str) -> tuple[int, int, int] | None:
    """Where ``key = value`` is in a table body: the start of its line, the end of its
    indentation, and the end of the value (after as many lines as it takes)."""
    found = re.compile(rf"^([ \t]*){re.escape(key)}[ \t]*=", re.MULTILINE).search(text, *body)
    if found is None:
        return None
    return found.start(), found.end(1), _value_end(text, found.end())


def _placeholder_end(text: str, body: tuple[int, int], key: str) -> int | None:
    """Where the line ends that holds ``# key = ...`` last in a table body (the commented
    setting that the default config.toml shows), just past its line break; None if none."""
    pattern = re.compile(rf"^[ \t]*#[ \t]*{re.escape(key)}[ \t]*=.*$\n?", re.MULTILINE)
    last = None
    for found in pattern.finditer(text, *body):
        last = found.end()
    return last


def _with_key(text: str, table: str | None, key: str, literal: str) -> str:
    line = f"{key} = {literal}"
    body = _table_body(text, table)
    if body is None:
        return text.rstrip("\n") + f"\n\n[{table}]\n{line}\n"
    found = _key_span(text, body, key)
    if found is None:
        # a new setting goes under its commented line (the explanation it belongs to),
        # else first in its table
        after = _placeholder_end(text, body, key)
        if after is not None:
            lead = "" if text[:after].endswith("\n") else "\n"
            return text[:after] + lead + line + "\n" + text[after:]
        return text[: body[0]] + line + "\n" + text[body[0] :]
    _, indent_end, value_end = found
    return text[:indent_end] + line + text[value_end:]  # what follows the value stays


REPLACE_TRIES = 40  # Windows: a file another reader holds open cannot be replaced, a moment
REPLACE_WAIT_S = 0.025  # later it can (the panel reads config.toml when it changed)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # (Windows: "access denied" while it is open for reading)
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_S)


def _comments_above(text: str, pos: int) -> int:
    """Where the comment lines directly above the line at ``pos`` begin (``pos`` when
    there are none): they belong to the header on that line."""
    while pos > 0:
        previous = text.rfind("\n", 0, pos - 1) + 1
        if not text[previous:pos].lstrip().startswith("#"):
            break
        pos = previous
    return pos


def _without_key(text: str, table: str | None, key: str) -> str:
    """``text`` without the setting ``key`` of ``table`` (None: the top level), over as many
    lines as its value takes; the comments above it stay. Unchanged where it is not set."""
    body = _table_body(text, table)
    found = _key_span(text, body, key) if body is not None else None
    if found is None:
        return text
    first, _, value_end = found
    return text[:first] + text[text.find("\n", value_end) + 1 or len(text) :]


def _without_tables(text: str, name: str) -> tuple[str, int | None]:
    """``text`` without the ``[[name]]`` blocks (and the tables inside them), and without
    a ``name = [...]`` in the parent table; and where the first block was (None when
    there were none). A block takes the comments directly above its header along."""
    headers = [m.start() for m in _HEADER.finditer(text)]
    starts = [_comments_above(text, header) for header in headers] + [len(text)]
    named = {
        m.start(): (m.group(1), re.sub(r"[ \t]", "", m.group(2)))
        for m in _ANY_HEADER.finditer(text)
    }
    kept, cut, at = [], 0, None
    for header, begin, stop in zip(headers, starts, starts[1:], strict=False):
        brackets, found = named.get(header, ("", ""))
        if (found == name and brackets == "[[") or found.startswith(name + "."):
            kept.append(text[cut:begin])
            at = sum(map(len, kept)) if at is None else at
            cut = stop
    kept.append(text[cut:])
    text = "".join(kept)
    head, _, last = name.rpartition(".")
    return _without_key(text, head or None, last), at  # a list in the parent table goes too


def _pruned(value: Any) -> Any:
    """``value`` without the empty tables in it: a table that only the headers of the
    array made is no difference."""
    if not isinstance(value, dict):
        return value
    kept = {key: _pruned(item) for key, item in value.items()}
    return {key: item for key, item in kept.items() if item != {}}


def _with_tables(text: str, name: str, tables: list[dict[str, Any]]) -> str:
    """``text`` with the array of tables ``name`` replaced by ``tables``."""
    text, at = _without_tables(text, name)
    head = name.rpartition(".")[0]
    blocks = "\n".join(
        f"[[{name}]]\n"
        + "".join(f"{_toml_key(key)} = {_toml_literal(value)}\n" for key, value in table.items())
        for table in tables
    )
    if not blocks:
        return text
    if at is None:  # new: after the parent table's keys, else at the end
        body = _table_body(text, head) if head else None
        at = body[1] if body is not None else len(text)
    ahead, behind = text[:at], text[at:]
    if not ahead.strip() or ahead.endswith("\n\n"):
        gap = ""
    else:
        gap = "\n" if ahead.endswith("\n") else "\n\n"
    return ahead + gap + blocks + ("\n" if behind.strip() else "") + behind


def _table_of(document: dict[str, Any], parents: list[str], name: str) -> dict[str, Any]:
    """The table at ``parents`` in ``document``, made where it is not there yet."""
    node = document
    for part in parents:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise ConfigError(f"{name}: {part} is not a table")
    return node


def edit_config(*changes: tuple[str, str, Any], path: Path | None = None) -> Path:
    """Change config.toml in one write. A change is ``("value", name, value)``, one setting
    (``"theme"``, ``"device.brightness"``, ``"modes.game.theme"``, ``"services.enabled"``) set
    to a boolean, a number, a string or a list of them; ``("unset", name, None)``, a setting
    taken out, so that its default holds again (nothing happens where it is not set); or
    ``("tables", name, tables)``, an array of tables (``"services.autopilot.rules"``)
    replaced by ``tables``, each written as a ``[[name]]`` block.

    The rest of the file, comments included, stays as it is (the comments inside the blocks
    of a replaced array go with them). The new file is compared with the old one: only what
    was asked for may differ, and it has to load as a configuration. Else nothing is written
    and a :class:`ConfigError` says to edit it by hand, so a layout this cannot handle is
    refused, never mangled. The write is atomic, and it waits for one under way in this
    program (the tray and each request of the app write from threads of their own): else
    both would start from the same file, and the last to finish would undo the other.
    """
    path = path or config_dir() / CONFIG_FILENAME
    with _EDITING:
        return _edit(path, changes)


_EDITING = threading.Lock()


def _edit(path: Path, changes: tuple[tuple[str, str, Any], ...]) -> Path:
    if not path.exists():
        write_default_config(path)
    original = _read(path)
    try:
        before = tomllib.loads(original)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    names = ", ".join(name for _, name, _ in changes)
    text, expected = original, copy.deepcopy(before)
    for kind, name, value in sorted(changes, key=lambda change: change[0] != "value"):
        *parents, key = name.split(".")
        node = _table_of(expected, parents, name)
        if kind == "tables":  # after the values: its blocks go behind the table's own keys
            text = _with_tables(text, name, value)
            if value:
                node[key] = [dict(table) for table in value]
            else:
                node.pop(key, None)  # no key at all
        elif kind == "value":
            text = _with_key(text, ".".join(parents) or None, key, _toml_literal(value))
            node[key] = list(value) if isinstance(value, tuple) else value
        elif kind == "unset":
            text = _without_key(text, ".".join(parents) or None, key)
            node.pop(key, None)
        else:
            raise ValueError(f"unknown kind of change: {kind!r}")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: could not set {names} ({exc}); please edit it by hand") from exc
    if _pruned(data) != _pruned(expected):
        raise ConfigError(f"{path}: could not set {names}; please edit it by hand")
    parse_config(data, path)
    _write(path, text)
    return path


def set_config_value(name: str, value: Any, path: Path | None = None) -> Path:
    """Change one setting in config.toml (see :func:`edit_config`)."""
    return edit_config(("value", name, value), path=path)


def set_config_tables(name: str, tables: list[dict[str, Any]], path: Path | None = None) -> Path:
    """Replace an array of tables in config.toml (see :func:`edit_config`)."""
    return edit_config(("tables", name, tables), path=path)


def set_active_theme(name: str, path: Path | None = None) -> Path:
    """Point config.toml at another theme, keeping the rest of the file (and comments). One
    theme chosen stops the screens in turn ([sequence]); their steps are kept."""
    path = path or config_dir() / CONFIG_FILENAME
    changes: list[tuple[str, str, Any]] = [("value", "theme", name)]
    try:
        if load_config(path).sequence.active:
            changes.append(("value", "sequence.on", False))
    except ConfigError:
        pass  # (the write below says what is wrong with the file)
    return edit_config(*changes, path=path)


def set_brightness(percent: int, path: Path | None = None) -> Path:
    return set_config_value("device.brightness", int(percent), path)

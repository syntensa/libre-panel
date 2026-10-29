"""User configuration.

Everything that is specific to one person or one machine (location, units,
which display, which sensors) lives here and nothing of it is hard-coded.
Optional features such as weather are off until the user turns them on.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import tomllib
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from libre_panel.sequence import Step, StepError, parse_steps

log = logging.getLogger(__name__)

CONFIG_FILENAME = "config.toml"
# The theme of every panel but the bars, and what ``Config.theme`` is where nothing better is
# known (libre_panel.theme.defaults has the panel's own default).
DEFAULT_THEME = "libre-default"
# The looks of the weather symbols and the moon (render/symbols.py, render/moon.py): the first,
# and the second with more to them
SYMBOL_PACKS = ("classic", "vivid")
# How much moves on the panel (app.calm): calm where the panel is slow, full, or calm everywhere
MOTIONS = ("auto", "full", "calm")
# How a TURZX USB panel takes its stills: "auto" (JPEG on the 5.2", PNG elsewhere), "png", "jpeg"
STILLS = ("auto", "png", "jpeg")
# The range of the video's values on a TURZX USB panel ([device] video_range): "auto" as the model
# is known to show them (PanelModel.video_range), "limited" (16-235), "full" (0-255)
VIDEO_RANGES = ("auto", "limited", "full")
# How a udisp USB display's JPEGs carry their colours ([device] jpeg): at every pixel, as the
# protocol's drivers send them, or at every second pixel each way, as most JPEGs do
JPEG_LAYOUTS = ("4:4:4", "4:2:0")
JPEG_QUALITY = (50, 100)  # [device] jpeg_quality: the least and the most
DEFAULT_SYMBOLS = "vivid"
# The cards a card slot that leaves them to the person holds (Lumen's, in each of its versions:
# config.toml ``cards``, theme.modules with_cards), in the order they are asked: the first
# that has something to show is on show, or with ``card_order = "turns"`` each in turn for its
# time ("forecast for 5m"; theme.modules CARD_ORDERS, CARD_TRANSITIONS)
DEFAULT_CARDS = ("playback", "ai", "forecast", "date")

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
# steps = [
#   { theme = "almanac", minutes = 5 },
#   { theme = "nimbus", minutes = 5 },
#   { theme = "studio", minutes = 10 },
# ]

# "auto" (the system language), "en", "de", "fr", "es", "it", "pl", "pt" (Brazil), "ru", "ja" or
# "zh": the app, the tray and the date and weather texts on the panel.
language = "auto"

# Optional: override how often sensors are read (milliseconds); the theme sets it.
# refresh_ms = 1000

# Whether Libre Panel looks once a day for a newer version (a request to GitHub; Start then
# says so). Not set, Start asks once.
# check_updates = true

# Whether other programs may show a notice on the panel (libre-panel notify, a script, a
# Stream Deck button), with the key in notify.json; see Settings → Game mode and more.
# allow_notify = false

# Light mode: while the app's window is closed, Libre Panel lets go of what only the app needs
# (its pictures of the themes), so it takes less memory; the app opens a moment slower.
# light_mode = false

# Keyboard shortcuts for the whole PC (Windows; Linux with X11), set in Settings → Start and
# system by pressing the keys. The actions: pause, brightness_up, brightness_down, open,
# next_scene, timer (start or pause the timer service), timer_reset, mic_mute (the microphone);
# enabled = false switches all of them off and keeps the keys written here.
# [hotkeys]
# pause = "Ctrl+Alt+P"

# Frames per second: values glide smoothly between sensor readings.
# 1 turns animation off (least CPU and USB traffic).
fps = 10

# The look of the weather symbols and the moon (or choose it in the app: Settings, Weather
# and place): "vivid", sharp and with much to them (the sun in its corona, rain near and
# far, snow crystals that turn, the moon as it looks), or "classic", the first, calmer one.
# A theme may give a symbol a look of its own.
# symbols = "vivid"

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
# How the panel is mounted: "landscape", or "portrait" for a bar that stands on end. Each
# theme is then shown in its version for it where it has one (Lumen as Lumen Vertical).
# Empty: as each theme is made.
orientation = ""
# Motion: "auto" keeps it calm on the older USB serial panels, which are slower (symbols stand,
# numbers jump, a picture a second, no fades between themes) and full on the others; "full"
# or "calm" for every panel.
motion = "auto"
# Smooth motion (video mode): the range of the video's values as the panel shows them, "auto"
# (as the model is known to: full on the 12.3", limited elsewhere), "limited" or "full". If
# black looks grey in video mode and right as stills, the panel wants "full" (Settings →
# Panel → Check the colours compares them on the panel).
# video_range = "auto"
# true: the panel goes dark when Libre Panel quits (Windows shutting down too), and lights up
# again when it starts; false: it keeps the last picture.
dark_on_quit = false
# true: dark while the computer sleeps (Windows), lit again when it wakes; false: the panel
# keeps the last picture through the night where its USB port stays powered.
dark_on_sleep = true
# true: a brightness of its own at night (0: dark), from night_from to night_to (over
# midnight too); the brightness above holds the rest of the day.
night = false
night_from = "22:00"
night_to = "07:00"
night_brightness = 10
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
# Who turns the frames' colours into video: "auto" the graphics card where a colour check found
# it converting exactly as the processor does (it runs once by itself in the background, again
# for a new ffmpeg or graphics driver), else ffmpeg on the processor (BT.601: the colours as
# drawn); "card" the card always (AMD's, NVIDIA's and Intel's encoders; an AMD card was
# measured shifting the colours); "rgb" the processor always, the way of before (three bytes a
# pixel). Settings → Panel → Check the colours says who converts and why.
# pixels = "auto"

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


_TIME_OF_DAY = re.compile(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*")


def time_of_day(text: str) -> int | None:
    """``"22:00"`` as minutes after midnight; None for what is no time of day."""
    found = _TIME_OF_DAY.fullmatch(text)
    return int(found[1]) * 60 + int(found[2]) if found else None


# The night may begin at sunset and end at sunrise (night_from, night_to) at the weather's
# place; without one at these times
SUN_TIMES = {"sunset": "19:00", "sunrise": "07:00"}


def night_time(text: str) -> str | None:
    """A beginning or end of the night as config.toml keeps it: ``"07:05"`` for ``"7:05"``,
    ``"sunset"`` or ``"sunrise"``; None for anything else."""
    word = text.strip().lower()
    if word in SUN_TIMES:
        return word
    minutes = time_of_day(text)
    return None if minutes is None else "{:02d}:{:02d}".format(*divmod(minutes, 60))


def night_now(
    device: DeviceConfig, now: datetime | None = None, place: tuple[float, float] | None = None
) -> bool:
    """Whether it is night for the panel (``[device] night``, from ``night_from`` to
    ``night_to``; a night that goes over midnight too). Sunset and sunrise are those of
    ``place`` (latitude, longitude) on that day; in a polar night it is night all day, under
    the midnight sun never."""
    if not device.night:
        return False
    now = now or datetime.now()
    sun = None
    if place is not None and {device.night_from, device.night_to} & set(SUN_TIMES):
        from libre_panel.sensors.sky import sun_times

        sun = sun_times(now.date(), *place)
        if sun["always"] is not None:
            return sun["always"] == "night"

    def minute(text: str) -> int | None:
        if text in SUN_TIMES:
            if sun is None:
                return time_of_day(SUN_TIMES[text])
            at = datetime.fromtimestamp(sun["set" if text == "sunset" else "rise"])
            return at.hour * 60 + at.minute
        return time_of_day(text)

    start, end = minute(device.night_from), minute(device.night_to)
    if start is None or end is None or start == end:
        return False
    minute_now = now.hour * 60 + now.minute
    return start <= minute_now < end if start < end else minute_now >= start or minute_now < end


def brightness_now(
    device: DeviceConfig,
    now: datetime | None = None,
    away: bool | None = None,
    place: tuple[float, float] | None = None,
) -> int:
    """The brightness the panel shows now: while the person is away (``away``; None: as
    presence.py has it) the one for then, at night the night's (its sunset and sunrise at
    ``place``), else its own."""
    if away is None:
        from libre_panel import presence

        away = presence.away()
    if away and device.dark_when_away:
        return device.away_brightness
    return device.night_brightness if night_now(device, now, place) else device.brightness


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


PORTABLE_MARK = "portable.txt"  # beside libre-panel.exe: a portable copy (portable_dir)


def portable_dir(program: str | None = None) -> Path | None:
    """For a portable copy (on a USB stick, in a folder of its own): the folder ``data``
    beside the program when a file ``portable.txt`` is there too; None for an installed one
    and for one run from Python."""
    if program is None:
        if not getattr(sys, "frozen", False):
            return None
        program = sys.executable
    folder = Path(program).resolve().parent
    return folder / "data" if (folder / PORTABLE_MARK).is_file() else None


def config_dir() -> Path:
    """Per-user directory holding config.toml and user themes.

    LIBRE_PANEL_HOME overrides it (tests, a folder of one's own), then a portable copy's
    ``data`` folder (:func:`portable_dir`).
    """
    override = os.environ.get("LIBRE_PANEL_HOME")
    if override:
        return Path(override)
    portable = portable_dir()
    if portable is not None:
        return portable
    return usual_dir()


def usual_dir() -> Path:
    """Where an installed Libre Panel keeps its settings: the user's own folder."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "LibrePanel"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "libre-panel"


def take_over_into_portable() -> Path | None:
    """On the first start of a portable copy: the settings in the user's own folder (where an
    installed Libre Panel, and the portable copies before 0.7, keep theirs) are copied into its
    ``data`` folder, once; the logs stay behind. The folder they came from, or None."""
    portable = None if os.environ.get("LIBRE_PANEL_HOME") else portable_dir()
    if portable is None or portable.exists():
        return None
    usual = usual_dir()
    if not (usual / "config.toml").is_file():
        return None
    part = portable.with_name(portable.name + ".part")
    try:
        shutil.rmtree(part, ignore_errors=True)
        shutil.copytree(usual, part, ignore=shutil.ignore_patterns("logs"))
        part.rename(portable)
    except OSError as exc:
        log.warning("portable: the settings of %s were not taken over: %s", usual, exc)
        shutil.rmtree(part, ignore_errors=True)
        return None
    return usual


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
    # 30 by default: smooth on the panels, and far less of the processor than 50 (measured with
    # two panels: 1.2 cores at 30, 2.0 at 50)
    fps: int = 30
    # Reported to the panel. Its player shows pictures strictly at this rate and
    # never catches up, so it must be above ``fps`` (SPUR II: 60 for 50).
    device_fps: int = 60
    # quality: 20 is sharp at little cost (measured on the 12.3" and the 9.2": about 3-4 Mbit/s
    # at 50 fps on a screen that moves), 25 was the first measurement's
    crf: int = 20
    preset: str = "superfast"
    keyframe_s: float = 10.0
    # the highest bit rate: "auto" starts at 2 Mbit/s for the 9.2" (1920x480 at 50 fps), more
    # for a panel with more pixels or frames (the 12.3": 3 Mbit/s), then follows the panel: a
    # step down while it takes less than it is sent (never below the start), a step up after a
    # calm minute (up to 32 Mbit/s);
    # or a rate such as "1500k", "4M"
    maxrate: str = "auto"
    # The profile: "auto" is high on the 9.2" and the 12.3" (measured to show there; it
    # compresses far better), baseline elsewhere. The encoder: "auto" is the graphics card's
    # where it was measured to show and ffmpeg can start it (AMD's h264_amf), else x264 on the
    # processor; h264_nvenc (NVIDIA) and h264_qsv (Intel) are an experiment to set by hand
    profile: str = "auto"  # "auto" | "baseline" | "main" | "high"
    encoder: str = "auto"  # "auto" | "libx264" | "h264_nvenc" | "h264_amf" | "h264_qsv"
    # How the frames reach ffmpeg and who converts their colours: "auto" four bytes a pixel as
    # they are drawn (rgb0), converted on the graphics card where a colour check found the card
    # converting as the processor does (devices/card_choice.py), else by ffmpeg on the processor
    # (BT.601); "rgb" the processor always, the way of before (rgb24); "card" the card always
    # (AMD's, NVIDIA's and Intel's encoders; the colour check measured AMD's shifting the
    # colours when left to itself)
    pixels: str = "auto"  # "auto" | "rgb" | "card"
    # A theme that is one video across the panel goes to it straight from its file: ffmpeg
    # reads and encodes it in one, instead of Libre Panel drawing its frames and ffmpeg
    # encoding those (less of the processor). Frames again as soon as anything else shows: a
    # notice, a transition, the alarm's edge. An experiment, off by default
    direct: bool = False
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
    # how the panel is mounted: "landscape" or "portrait" (on end) shows each theme in its
    # version for it ("lumen-vertical" for "lumen"); "": as the theme is made
    orientation: str = ""
    # "auto": calm on the slow USB serial panels, full elsewhere; "full"; "calm" (MOTIONS)
    motion: str = "auto"
    # TURZX USB panels: each still as "png" (102, the layer over the video) or "jpeg" (101, the
    # layer under it, decoded in hardware: faster); "auto" is JPEG on the 5.2", which shows no
    # PNG, and PNG on the others, where it is proven (STILLS)
    still: str = "auto"
    # TURZX USB panels in video mode: the range of the video's values, as the panel's decoder
    # shows them: "limited" (16-235), "full" (0-255), "auto" as the model is known to (VIDEO_RANGES;
    # the 12.3"'s decoder shows every stream as full range). Each panel's own ([[panels]] too)
    video_range: str = "auto"
    # udisp USB displays that take JPEG: its colours at every pixel ("4:4:4", the protocol's
    # drivers') or at every second pixel each way ("4:2:0", most JPEGs'), for a decoder that
    # shows only that one (JPEG_LAYOUTS; the panel test tries it where the first stays black)
    jpeg: str = "4:4:4"
    # ... and how much of each picture's detail its JPEG keeps (JPEG_QUALITY; Settings → Panel →
    # Picture quality): 90, or more for finer detail at more bytes a picture; lower only where
    # the display says its JPEGs may have fewer
    jpeg_quality: int = 90
    dark_on_quit: bool = False  # a black picture and its light off when Libre Panel quits
    dark_on_sleep: bool = True  # so too while the computer sleeps (Windows), lit when it wakes
    # a brightness of its own at night (night_now): from night_from to night_to, over midnight
    # too, "HH:MM"; 0 is dark
    night: bool = False
    night_from: str = "22:00"
    night_to: str = "07:00"
    night_brightness: int = 10
    # while the person is away (the computer locked, its monitor off: presence.py), this
    # brightness; 0 is dark
    dark_when_away: bool = True
    away_brightness: int = 0
    output: str = "libre-panel-frame.png"
    port: str = ""  # serial panels: the port; empty = found by its USB ids
    # where the panel stands beside the others (Settings → Panels): the top left
    # corner of its picture, in millimetres, as mounted; None: not placed
    at: tuple[int, int] | None = None
    video: VideoConfig = field(default_factory=VideoConfig)
    # the models the other panels name (not "auto"), worked out from [[panels]]: with model
    # "auto" a panel of such a model is that panel's, unless there are more of it on the bus
    beside: tuple[str, ...] = field(default=(), compare=False)


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
    """A mode a service can switch to, e.g. [modes.game] with fewer frames; or one of the
    pages Libre Panel switches to itself (libre_panel.pages: "media", "idle"), with ``on``
    and, for "idle", its ``minutes``."""

    fps: int | None = None
    theme: str | None = None
    on: bool = False
    minutes: float | None = None


# Where a switch of the screen comes from ([transitions], app.py works it out): a game, music, the
# PC at rest, the screens in turn, the autopilot, the round report, or a theme or scene chosen
TRANSITION_OCCASIONS = ("game", "media", "idle", "sequence", "autopilot", "report", "manual")
# each occasion's own at first; "" is what the service asks for (the game's entrance with its
# logo, the autopilot's and the round report's tube), else the default
OCCASION_TRANSITIONS = {
    "game": "",
    "media": "entrance",
    "idle": "fade",
    "sequence": "light",
    "autopilot": "",
    "report": "",
    "manual": "",
}
TRANSITION_SPEEDS = {"fast": 0.6, "normal": 1.0, "slow": 1.6}  # times the durations
RANDOM_TRANSITION = "random"  # one of [transitions] random, another each time
RANDOM_TRANSITIONS = ("fade", "wipe", "zoom", "iris", "blinds", "dissolve", "light", "tiles")
_TRANSITION_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass
class TransitionsConfig:
    """How the panel goes from one screen to the next ([transitions]): the default, how fast,
    each occasion's own (TRANSITION_OCCASIONS; "" the default) and what "random" picks from."""

    default: str = "fade"
    speed: str = "normal"
    occasions: dict[str, str] = field(default_factory=lambda: dict(OCCASION_TRANSITIONS))
    random: list[str] = field(default_factory=lambda: list(RANDOM_TRANSITIONS))


@dataclass
class SequenceConfig:
    """Screens in turn (libre_panel.sequence): the steps, and whether they show."""

    on: bool = True
    steps: list[Step] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.on and bool(self.steps)


# How many panels may run beside the one [device] names ([[panels]])
MORE_PANELS = 3
# The theme a panel beside the first shows while a sensor is at its critical limit
ALARM_THEME = "lumen-companion"


@dataclass
class PanelConfig:
    """A panel beside the one ``[device]`` names (``[[panels]]``, two panels at once): the keys
    of ``[device]`` for its own, its theme ("": the panel's own) and whether it runs. The
    readings, the language and the rest of config.toml are the first panel's."""

    theme: str = ""
    on: bool = True
    device: DeviceConfig = field(default_factory=DeviceConfig)
    # its brightness as the first panel's, at night and while the person is away too, with
    # brightness_offset (-50 to 50) added to look as bright beside it; off: its own
    follow_brightness: bool = True
    brightness_offset: int = 0
    # its theme while the first panel shows a game's page or music's ("": its own as ever)
    game_theme: str = ""
    media_theme: str = ""
    # its theme while a sensor is at its critical limit (the notices service's alarm): the
    # temperatures and what keeps the PC busy ("": its own as ever)
    alarm_theme: str = ALARM_THEME
    # the look of the first panel's theme as it shows (its colours, fonts and module frames)
    # on this panel's own theme; a change in Design follows (theme.model.with_look)
    same_look: bool = False
    # smooth motion on this panel too (``video = { mode = "auto", local_clip = "..." }``):
    # its own mode, off to begin with, and the name of its own standby clip, never another
    # panel's; the rate and the encoder's settings are the first panel's ([video])
    video_mode: str = "off"
    local_clip: str = ""
    clip_asked: bool = False


# How many scenes config.toml may hold, and how long a scene's name may be
MAX_SCENES = 12
SCENE_NAME_LENGTH = 40


@dataclass
class SceneConfig:
    """A scene (``[[scenes]]``): a theme for every panel at once, chosen with one click (Start),
    a keyboard shortcut or the tray. ``themes``: the first panel's, then those of
    ``[[panels]]`` in their order; "" leaves that panel's own."""

    name: str
    themes: list[str] = field(default_factory=list)


@dataclass
class Config:
    # The theme config.toml names; without one the panel's own default shows, and this is
    # only the fallback (see ``theme_given``).
    theme: str = DEFAULT_THEME
    language: str = "auto"
    refresh_ms: int | None = None
    fps: int = 10
    # between themes: the default of [transitions], which config.toml may still name here
    transition: str = "fade"
    transitions: TransitionsConfig = field(default_factory=TransitionsConfig)
    check_updates: bool | None = None  # a look for a newer version once a day; None: not asked
    allow_notify: bool = False  # other programs may show a notice (notify.py)
    light_mode: bool = False  # less memory while the app is closed (light.py)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    sequence: SequenceConfig = field(default_factory=SequenceConfig)
    panels: list[PanelConfig] = field(default_factory=list)  # [[panels]]: more panels at once
    scenes: list[SceneConfig] = field(default_factory=list)  # [[scenes]]
    # the scene that is on ("": none): its themes take the place of theme and the panels' own
    scene: str = ""
    hotkeys: dict[str, str] = field(default_factory=dict)  # action: combination (hotkeys.py)
    hotkeys_enabled: bool = True  # [hotkeys] enabled: off holds no key, the combinations kept
    symbols: str = DEFAULT_SYMBOLS  # the look of the weather symbols and the moon
    # the cards of the card slots that leave them to the person (Lumen's), in the order they
    # are asked; the first that has something to show is on show (DEFAULT_CARDS)
    cards: list[str] = field(default_factory=lambda: list(DEFAULT_CARDS))
    # which of them is on show: the first that has something to show ("first"), or each in
    # turn for its time ("turns": a card's own "for 5m", else 5 minutes); and how one card
    # goes to the next ("light", "fade", "slide", "rise", "wipe", "cut", "random")
    card_order: str = "first"
    card_transition: str = "light"
    # an accent colour for every theme instead of its own (accent.py): "" the themes' own,
    # "system" Windows', or "#RRGGBB"
    accent: str = ""
    # one picture across all panels that are placed (arrangement.py): a file in the settings
    # folder (or a path of its own); "" none
    across: str = ""
    # the person's choice of the themes' switches (Theme.switches; Look shows them): for every
    # theme ([switches] frames = false, kept under "*") and by theme, all its versions
    # ([switches.lumen] frames = true), which goes before
    switches: dict[str, dict[str, bool]] = field(default_factory=dict)
    # for a panel beside the first (for_panel): its theme in the first panel's look
    same_look: bool = False
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

    def for_panel(self, index: int) -> Config:
        """The config the panel ``panels[index]`` runs with: its device and theme and the rest
        of this one; no screens in turn and no more panels (the pages a service switches to are
        the first panel's: its loop has no host). Smooth motion (the video mode) is each
        panel's own: its mode (off to begin with) and the name of its own standby clip, never
        the first panel's, with the first panel's rate and encoder settings."""
        panel = self.panels[index]
        video = replace(self.device.video, mode=panel.video_mode, local_clip=panel.local_clip,
                        clip_asked=panel.clip_asked)  # fmt: skip
        named = [self.device, *(p.device for n, p in enumerate(self.panels) if n != index)]
        beside = tuple(other.model for other in named if other.model != "auto")
        device = replace(panel.device, video=video, beside=beside)
        if panel.follow_brightness:
            first = self.device

            def shifted(level: int) -> int:  # (dark stays dark)
                return level if level == 0 else max(1, min(100, level + panel.brightness_offset))

            device = replace(
                device,
                brightness=shifted(first.brightness),
                night=first.night,
                night_from=first.night_from,
                night_to=first.night_to,
                night_brightness=shifted(first.night_brightness),
                dark_when_away=first.dark_when_away,
                away_brightness=shifted(first.away_brightness),
            )
        pages = {mode: ModeConfig(theme=theme, on=True) for mode, theme in
                 (("game", panel.game_theme), ("media", panel.media_theme),
                  ("alarm", panel.alarm_theme)) if theme}  # fmt: skip
        return replace(
            self,
            device=device,
            theme=panel.theme or DEFAULT_THEME,
            theme_given=bool(panel.theme),
            sequence=SequenceConfig(on=False),
            panels=[],
            modes=pages,  # (those of a game and of music, while the first panel shows them)
            same_look=panel.same_look,
        )


def _transition_name(value: Any, name: str, empty: bool = False) -> str:
    text = _expect(value, str, name).strip()
    if (text or not empty) and not _TRANSITION_NAME.match(text):
        raise ConfigError(f"{name}: the name of a transition, such as fade, wipe or entrance")
    return text


def _transitions(cfg: Config, table: dict[str, Any]) -> None:
    """``[transitions]``: ``default``, ``speed``, each occasion's own and ``random``."""
    chosen = cfg.transitions
    for key, value in table.items():
        name = f"transitions.{key}"
        if key == "default":
            cfg.transition = chosen.default = _transition_name(value, name)
        elif key == "speed":
            chosen.speed = _expect(value, str, name)
            if chosen.speed not in TRANSITION_SPEEDS:
                raise ConfigError(f"{name}: one of {', '.join(TRANSITION_SPEEDS)}")
        elif key in TRANSITION_OCCASIONS:
            chosen.occasions[key] = _transition_name(value, name, empty=True)
        elif key == "random":
            names = _expect(value, list, name)
            if not names or len(names) > 32:
                raise ConfigError(f"{name}: one to 32 transitions")
            chosen.random = [_transition_name(item, name) for item in names]
            if RANDOM_TRANSITION in chosen.random:
                raise ConfigError(f"{name}: random cannot pick itself")
        else:
            raise ConfigError(f"unknown setting {name!r}")


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


_SWITCH_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


EVERY_THEME = "*"  # Config.switches: what holds for every theme


def _switches(value: Any) -> dict[str, dict[str, bool]]:
    """``[switches]``: a switch on (true) or off (false) for every theme, and in
    ``[switches.<theme>]`` for that theme."""
    chosen: dict[str, dict[str, bool]] = {}
    for theme, table in _expect(value, dict, "switches").items():
        if not _SWITCH_NAME.match(theme):
            raise ConfigError(f"switches: {theme!r} is not the name of a theme or a switch")
        if isinstance(table, bool):  # [switches] frames = false
            chosen.setdefault(EVERY_THEME, {})[theme] = table
            continue
        where = f"switches.{theme}"
        for name, on in _expect(table, dict, where).items():
            if not _SWITCH_NAME.match(name):
                raise ConfigError(f"{where}: {name!r} is not the name of a switch")
            chosen.setdefault(theme, {})[name] = _expect(on, bool, f"{where}.{name}")
    return chosen


def _hotkeys(table: dict[str, Any]) -> dict[str, str]:
    """``[hotkeys]``: each action's combination, written the one way (``Ctrl+Alt+P``); two
    actions cannot share one."""
    from libre_panel.hotkeys import ACTIONS, parse

    found: dict[str, str] = {}
    for action, text in table.items():
        if action not in ACTIONS:
            raise ConfigError(f"hotkeys.{action}: none of {', '.join(ACTIONS)}")
        text = _expect(text, str, f"hotkeys.{action}")
        if not text:
            continue
        try:
            combo = str(parse(text))
        except ValueError as exc:
            raise ConfigError(f"hotkeys.{action}: {exc}") from None
        twin = next((other for other, given in found.items() if given == combo), None)
        if twin:
            raise ConfigError(f"hotkeys.{action}: {combo} is the shortcut of {twin} already")
        found[action] = combo
    return found


def parse_config(data: dict[str, Any], path: Path | None = None) -> Config:
    cfg = Config(path=path)
    if "theme" in data:
        cfg.theme = _expect(data["theme"], str, "theme")
        cfg.theme_given = True
    if "language" in data:
        cfg.language = _expect(data["language"], str, "language")
        from libre_panel.i18n import LANGUAGES

        if cfg.language not in ("auto", *LANGUAGES):
            raise ConfigError(f"language: must be auto or one of {', '.join(LANGUAGES)}")
    if "fps" in data:
        cfg.fps = _expect(data["fps"], int, "fps")
        if not 1 <= cfg.fps <= 60:
            raise ConfigError("fps: must be between 1 and 60")
    if "transition" in data:
        cfg.transition = cfg.transitions.default = _transition_name(
            data["transition"], "transition"
        )
    _transitions(cfg, _expect(data.get("transitions", {}), dict, "transitions"))
    if "symbols" in data:
        cfg.symbols = _expect(data["symbols"], str, "symbols").strip()
        if cfg.symbols not in SYMBOL_PACKS:
            raise ConfigError(f"symbols: must be one of {', '.join(SYMBOL_PACKS)}")
    if "cards" in data:
        cfg.cards = card_choice(data["cards"], "cards")
    if "card_order" in data or "card_transition" in data:
        from libre_panel.theme.modules import CARD_ORDERS, CARD_TRANSITIONS

        for key, allowed in (("card_order", CARD_ORDERS), ("card_transition", CARD_TRANSITIONS)):
            if key in data:
                value = _expect(data[key], str, key).strip()
                if value not in allowed:
                    raise ConfigError(f"{key}: must be one of {', '.join(allowed)}")
                setattr(cfg, key, value)
    if "accent" in data:
        from libre_panel.accent import HEX, SYSTEM

        cfg.accent = _expect(data["accent"], str, "accent").strip()
        if cfg.accent and cfg.accent != SYSTEM and not HEX.fullmatch(cfg.accent):
            raise ConfigError('accent: "system", a colour such as "#E0457B", or empty')
    if "across" in data:
        cfg.across = _expect(data["across"], str, "across").strip()
        if len(cfg.across) > 300:
            raise ConfigError("across: at most 300 characters")
    if "refresh_ms" in data:
        cfg.refresh_ms = _expect(data["refresh_ms"], int, "refresh_ms")
        if cfg.refresh_ms < 100:
            raise ConfigError("refresh_ms: must be at least 100")
    if "check_updates" in data:
        cfg.check_updates = _expect(data["check_updates"], bool, "check_updates")
    if "allow_notify" in data:
        cfg.allow_notify = _expect(data["allow_notify"], bool, "allow_notify")
    if "light_mode" in data:
        cfg.light_mode = _expect(data["light_mode"], bool, "light_mode")
    if "switches" in data:
        cfg.switches = _switches(data["switches"])
    if "hotkeys" in data:
        table = dict(_expect(data["hotkeys"], dict, "hotkeys"))
        if "enabled" in table:
            cfg.hotkeys_enabled = _expect(table.pop("enabled"), bool, "hotkeys.enabled")
        cfg.hotkeys = _hotkeys(table)

    cfg.device = _parse_device(_expect(data.get("device", {}), dict, "device"), "device")
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
        if "on" in table:
            mode.on = _expect(table["on"], bool, f"modes.{name}.on")
        if "minutes" in table:
            minutes = table["minutes"]
            if isinstance(minutes, bool) or not isinstance(minutes, int | float):
                raise ConfigError(f"modes.{name}.minutes: expected a number of minutes")
            if not 0.5 <= minutes <= 1440:
                raise ConfigError(f"modes.{name}.minutes: must be between 0.5 and 1440")
            mode.minutes = float(minutes)
        cfg.modes[name] = mode

    sequence = _expect(data.get("sequence", {}), dict, "sequence")
    cfg.sequence.on = _expect(sequence.get("on", True), bool, "sequence.on")
    try:
        cfg.sequence.steps = parse_steps(sequence.get("steps", []))
    except StepError as exc:
        raise ConfigError(str(exc)) from exc

    cfg.panels = _panels(data.get("panels", []))
    cfg.device.beside = tuple(p.device.model for p in cfg.panels if p.device.model != "auto")
    cfg.scenes = _scenes(data.get("scenes", []))
    cfg.scene = _expect(data.get("scene", ""), str, "scene").strip()
    _put_on_scene(cfg)

    if cfg.weather.enabled and (cfg.weather.latitude is None or cfg.weather.longitude is None):
        raise ConfigError(
            "weather is enabled but no location is set: add weather.latitude and "
            'weather.longitude (find them with: libre-panel location "Your City")'
        )
    return cfg


def _parse_device(dev: dict[str, Any], where: str) -> DeviceConfig:
    """The settings of a panel: ``[device]``, or a ``[[panels]]`` table (``where`` names it)."""
    device = DeviceConfig()
    device.model = _expect(dev.get("model", device.model), str, f"{where}.model")
    if device.model != "auto":
        from libre_panel.devices.models import find_model

        if find_model(device.model) is None:
            raise ConfigError(
                f"{where}.model: unknown panel {device.model!r} (see: libre-panel models)"
            )
    device.driver = _expect(dev.get("driver", device.driver), str, f"{where}.driver")
    device.brightness = _expect(dev.get("brightness", device.brightness), int,
                                f"{where}.brightness")  # fmt: skip
    if not 0 <= device.brightness <= 100:
        raise ConfigError(f"{where}.brightness: must be between 0 and 100")
    device.rotate = _expect(dev.get("rotate", device.rotate), int, f"{where}.rotate")
    if device.rotate not in (0, 180):
        raise ConfigError(f"{where}.rotate: must be 0, or 180 for upside down")
    orientation = _expect(dev.get("orientation", device.orientation), str, f"{where}.orientation")
    if orientation not in ("", "landscape", "portrait"):
        raise ConfigError(f'{where}.orientation: "landscape", "portrait" or ""')
    device.orientation = orientation
    motion = _expect(dev.get("motion", device.motion), str, f"{where}.motion")
    if motion not in MOTIONS:
        raise ConfigError(f'{where}.motion: "auto", "full" or "calm"')
    device.motion = motion
    still = _expect(dev.get("still", device.still), str, f"{where}.still")
    if still not in STILLS:
        raise ConfigError(f'{where}.still: "auto", "png" or "jpeg"')
    device.still = still
    video_range = _expect(dev.get("video_range", device.video_range), str, f"{where}.video_range")
    if video_range not in VIDEO_RANGES:
        raise ConfigError(f'{where}.video_range: "auto", "limited" or "full"')
    device.video_range = video_range
    jpeg = _expect(dev.get("jpeg", device.jpeg), str, f"{where}.jpeg")
    if jpeg not in JPEG_LAYOUTS:
        raise ConfigError(f'{where}.jpeg: "4:4:4" or "4:2:0"')
    device.jpeg = jpeg
    quality = _expect(dev.get("jpeg_quality", device.jpeg_quality), int, f"{where}.jpeg_quality")
    if not JPEG_QUALITY[0] <= quality <= JPEG_QUALITY[1]:
        raise ConfigError(f"{where}.jpeg_quality: must be between {JPEG_QUALITY[0]} and "
                          f"{JPEG_QUALITY[1]}")  # fmt: skip
    device.jpeg_quality = quality
    for name in ("dark_on_quit", "dark_on_sleep", "night", "dark_when_away"):
        given = dev.get(name, getattr(device, name))
        setattr(device, name, _expect(given, bool, f"{where}.{name}"))
    for name in ("night_from", "night_to"):
        text = night_time(_expect(dev.get(name, getattr(device, name)), str, f"{where}.{name}"))
        if text is None:
            raise ConfigError(f'{where}.{name}: a time of day such as "22:00", or "sunset"')
        setattr(device, name, text)
    level = _expect(dev.get("night_brightness", device.night_brightness), int,
                    f"{where}.night_brightness")  # fmt: skip
    if isinstance(level, bool) or not 0 <= level <= 100:
        raise ConfigError(f"{where}.night_brightness: 0 to 100")
    device.night_brightness = level
    level = _expect(dev.get("away_brightness", device.away_brightness), int,
                    f"{where}.away_brightness")  # fmt: skip
    if not 0 <= level <= 100:
        raise ConfigError(f"{where}.away_brightness: 0 to 100")
    device.away_brightness = level
    device.output = _expect(dev.get("output", device.output), str, f"{where}.output")
    device.port = _expect(dev.get("port", device.port), str, f"{where}.port").strip()
    device.at = place_at(dev.get("at"), f"{where}.at")
    return device


ARRANGEMENT_MM = 5000  # how far from the first panel's place one may stand, in millimetres


def place_at(value: Any, name: str) -> tuple[int, int] | None:
    """``at = [x, y]``: where a panel stands, in whole millimetres (None, or [], when it is not
    placed)."""
    if value is None or value == []:
        return None
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        or not all(-ARRANGEMENT_MM <= v <= ARRANGEMENT_MM for v in value)
    ):
        raise ConfigError(
            f"{name}: [x, y] in whole millimetres, -{ARRANGEMENT_MM} to {ARRANGEMENT_MM}"
        )
    return (value[0], value[1])


def _scenes(value: Any) -> list[SceneConfig]:
    """``[[scenes]]``: each a ``name`` and the ``themes`` of the panels, the first panel's
    first."""
    scenes: list[SceneConfig] = []
    tables = _expect(value, list, "scenes")
    if len(tables) > MAX_SCENES:
        raise ConfigError(f"scenes: at most {MAX_SCENES}")
    for n, table in enumerate(tables):
        where = f"scenes[{n}]"
        table = _expect(table, dict, where)
        name = " ".join(_expect(table.get("name", ""), str, f"{where}.name").split())
        if not name or len(name) > SCENE_NAME_LENGTH:
            raise ConfigError(f"{where}.name: 1 to {SCENE_NAME_LENGTH} characters")
        if any(scene.name == name for scene in scenes):
            raise ConfigError(f"{where}.name: {name!r} is there twice")
        themes = _expect(table.get("themes", []), list, f"{where}.themes")
        if not 1 <= len(themes) <= 1 + MORE_PANELS:
            raise ConfigError(f"{where}.themes: 1 to {1 + MORE_PANELS} themes")
        themes = [_expect(theme, str, f"{where}.themes[]").strip() for theme in themes]
        scenes.append(SceneConfig(name=name, themes=themes))
    return scenes


def _put_on_scene(cfg: Config) -> None:
    """The scene that is on (``scene``) gives the panels its themes; one that is not there
    (taken out) changes nothing."""
    scene = next((scene for scene in cfg.scenes if scene.name == cfg.scene), None)
    if scene is None:
        return
    first, rest = scene.themes[0], scene.themes[1:]
    if first:
        cfg.theme, cfg.theme_given = first, True
    for panel, theme in zip(cfg.panels, rest, strict=False):
        if theme:
            panel.theme = theme


def panel_defaults(n: int) -> dict[str, str]:
    """What a panel beside the first (``[[panels]]`` at ``n``) has by default where it differs
    from ``[device]``: one that is not there is waited for ("auto" would draw into a file
    meanwhile), and its file is its own."""
    return {"driver": "turzx", "output": f"libre-panel-frame-{n + 2}.png"}


def _panel_video(value: Any, where: str) -> dict[str, Any]:
    """A panel's ``video = { mode, local_clip, clip_asked }`` (its own smooth motion)."""
    table = dict(_expect(value, dict, where))
    mode = _expect(table.pop("mode", "off"), str, f"{where}.mode")
    if mode not in ("auto", "off", "on"):
        raise ConfigError(f'{where}.mode: must be "auto", "off" or "on"')
    clip = _expect(table.pop("local_clip", ""), str, f"{where}.local_clip")
    if len(clip.encode("utf-8")) > 200:
        raise ConfigError(f"{where}.local_clip: at most 200 bytes")
    asked = _expect(table.pop("clip_asked", False), bool, f"{where}.clip_asked")
    if table:
        known = "mode, local_clip, clip_asked"
        raise ConfigError(f"{where}: unknown key {sorted(table)[0]!r} ({known})")
    return {"video_mode": mode, "local_clip": clip, "clip_asked": asked}


def _panels(value: Any) -> list[PanelConfig]:
    """``[[panels]]``: the panels beside ``[device]``'s, each with the keys of ``[device]``, a
    ``theme`` and ``on``. Without a driver one is the panel found ("turzx"), and one that
    writes its frames to a file (``driver = "virtual"``) writes to one of its own."""
    panels = []
    tables = _expect(value, list, "panels")
    if len(tables) > MORE_PANELS:
        raise ConfigError(f"panels: at most {MORE_PANELS} panels beside [device]")
    for n, table in enumerate(tables):
        where = f"panels[{n}]"
        table = dict(_expect(table, dict, where))
        theme = _expect(table.pop("theme", ""), str, f"{where}.theme").strip()
        on = _expect(table.pop("on", True), bool, f"{where}.on")
        follow = _expect(table.pop("follow_brightness", True), bool, f"{where}.follow_brightness")
        game = _expect(table.pop("game_theme", ""), str, f"{where}.game_theme").strip()
        media = _expect(table.pop("media_theme", ""), str, f"{where}.media_theme").strip()
        alarm = _expect(table.pop("alarm_theme", ALARM_THEME), str, f"{where}.alarm_theme").strip()
        look = _expect(table.pop("same_look", False), bool, f"{where}.same_look")
        video = _panel_video(table.pop("video", {}), f"{where}.video")
        offset = _expect(table.pop("brightness_offset", 0), int, f"{where}.brightness_offset")
        if not -50 <= offset <= 50:
            raise ConfigError(f"{where}.brightness_offset: -50 to 50")
        table = {**panel_defaults(n), **table}
        panels.append(PanelConfig(theme=theme, on=on, device=_parse_device(table, where),
                                  follow_brightness=follow, brightness_offset=offset,
                                  game_theme=game, media_theme=media, alarm_theme=alarm,
                                  same_look=look, **video))  # fmt: skip
    return panels


_MAXRATE = re.compile(r"^(auto|[0-9]+(\.[0-9]+)?[kKM]?)$")


def _parse_video(v: dict[str, Any]) -> VideoConfig:
    from libre_panel.devices.h264 import ENCODERS, PIXELS, PROFILES, X264_PRESETS

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
    video.profile = _expect(v.get("profile", video.profile), str, "video.profile")
    if video.profile not in PROFILES:
        raise ConfigError(f"video.profile: must be one of {', '.join(PROFILES)}")
    video.encoder = _expect(v.get("encoder", video.encoder), str, "video.encoder")
    if video.encoder not in ENCODERS:
        raise ConfigError(f"video.encoder: must be one of {', '.join(ENCODERS)}")
    video.direct = _expect(v.get("direct", video.direct), bool, "video.direct")
    video.pixels = _expect(v.get("pixels", video.pixels), str, "video.pixels")
    if video.pixels not in PIXELS:
        raise ConfigError(f"video.pixels: must be one of {', '.join(PIXELS)}")
    video.keyframe_s = float(
        _expect(v.get("keyframe_s", video.keyframe_s), (int, float), "video.keyframe_s")
    )
    if not 1 <= video.keyframe_s <= 60:
        raise ConfigError("video.keyframe_s: must be between 1 and 60 seconds")
    video.maxrate = _expect(v.get("maxrate", video.maxrate), str, "video.maxrate")
    if not _MAXRATE.match(video.maxrate):
        raise ConfigError('video.maxrate: "auto", or a bit rate such as "2M" or "1500k"')
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


def card_choice(value: Any, where: str) -> list[str]:
    """The cards of the card slots, as config.toml's ``cards`` has them: a list of the cards'
    names (theme.modules CARD_NAMES), each once, one at least, each with the time of its turn
    if it has one of its own (``"forecast for 5m"``, theme.modules card_line)."""
    from libre_panel.theme.modules import TURN_S, card_line

    lines = _expect(value, list, where)
    cards: list[str] = []
    names: list[str] = []
    for line in lines:
        line = " ".join(_expect(line, str, f"{where}[]").split())
        card = card_line(line)
        if card is None or "#" in line or card.rule:
            name = line.partition(" for ")[0]
            raise ConfigError(f"{where}: {name!r} is no card (e.g. {', '.join(DEFAULT_CARDS)})")
        if " for " in line and card.seconds is None:
            raise ConfigError(
                f"{where}: {line!r}: a time such as 5m, 90s or 1h, "
                f"{TURN_S[0]} s to {TURN_S[1] // 3600} h"
            )
        if card.name in names:
            raise ConfigError(f"{where}: {card.name!r} is named twice")
        cards.append(line)
        names.append(card.name)
    if not cards:
        raise ConfigError(f"{where}: one card at least (e.g. {', '.join(DEFAULT_CARDS)})")
    return cards


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
        config = load_config(path)
        if config.sequence.active:
            changes.append(("value", "sequence.on", False))
        if config.scene:  # a theme chosen: the scene is no longer on
            changes.append(("unset", "scene", None))
    except ConfigError:
        pass  # (the write below says what is wrong with the file)
    return edit_config(*changes, path=path)


def set_brightness(percent: int, path: Path | None = None) -> Path:
    return set_config_value("device.brightness", int(percent), path)

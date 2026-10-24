"""What the app's Settings read and write, apart from HTTP.

The routes in server.py hand a request to :class:`Settings` and turn its answer, or its
:class:`SettingsError`, into JSON. Everything goes through config.toml (``edit_config``: the
comments stay, a change that would break the file is refused), so the running Libre Panel
follows, and nothing here has to be done by hand in that file.
"""

from __future__ import annotations

import functools
import logging
import math
import os
import platform
import re
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from libre_panel import __version__, i18n
from libre_panel.config import (
    ALARM_THEME,
    MORE_PANELS,
    MOTIONS,
    SYMBOL_PACKS,
    Config,
    ConfigError,
    DeviceConfig,
    config_dir,
    default_providers,
    edit_config,
    load_config,
    log_file,
    panel_defaults,
    place_at,
    user_themes_dir,
)
from libre_panel.devices.h264 import ENCODERS, PROFILES, X264_PRESETS
from libre_panel.editor import access
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/syntensa/libre-panel"
REPORT_FORM = f"{PROJECT_URL}/issues/new?template=panel_support.yml"
LICENSE = {"id": "GPL-3.0-or-later", "url": "https://www.gnu.org/licenses/gpl-3.0.html"}
MAX_NOTICE = 200_000
# The built-in services in the order the app lists them (game mode and its round report first,
# the section is named after it); plugins' come after them.
SERVICE_ORDER = (
    "game", "memory", "notices", "autopilot", "devices", "cooling", "downloads", "timer", "power",
    "agents",
)  # fmt: skip
# Sources that start by themselves; they are listed only where config.toml names them.
# hardware: the names of this PC; mangohud: a game's frame rate on Linux
IMPLICIT_SOURCES = ("media", "sky", "hardware", "mangohud")
# The sensors and the weather are made once when the panel loop starts.
RESTART_PREFIXES = ("sensors.", "weather.")
FOLDERS = ("config", "themes", "logs")
PLACES = 6
_WRITING = threading.Lock()  # two writes at once would each start from the same file
# What a setting names that Libre Panel starts, loads or writes: a program (ffmpeg, PresentMon), the
# libraries of LibreHardwareMonitor, the file a virtual panel draws to. With administrator rights
# (root on Linux) it does that with them, so while it runs so these are not changed through the
# server, whoever asks: they are set in config.toml, which it reads from the user's own folder.
LOCKED_WHILE_ELEVATED = (
    "video.ffmpeg",
    "device.output",
    "sensors.presentmon.path",
    "sensors.librehardwaremonitor.dll_dir",
)
# An option of a service or a source is one of them when its name says it is a file or a folder.
PATH_WORDS = (
    "path", "dir", "folder", "file", "database", "fancontrol", "program", "exe", "command",
    "script", "dll", "library", "output",
)  # fmt: skip


class SettingsError(ValueError):
    """A request the settings refuse; ``status`` is the HTTP status that goes with it."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Restart:
    """The written settings the running panel loop has not taken in yet.

    A new start of the loop (pause and resume, or Libre Panel started again) makes the sensors
    and the weather from config.toml anew; until then a change to them waits. The loop's
    ``RunStatus`` is made anew with each start, so it tells which run a change was written in.
    """

    def __init__(self) -> None:
        self.keys: list[str] = []
        self._run: Any = None
        self._lock = threading.Lock()  # the requests come in threads of their own

    @staticmethod
    def _run_of(controls: Any) -> Any:
        return getattr(getattr(controls, "panel", None), "status", None)

    def written(self, names: list[str], controls: Any) -> None:
        wanted = [name for name in names if name.startswith(RESTART_PREFIXES)]
        if wanted:
            self.pending(controls)  # a restart since the last change clears that one first
            with self._lock:
                self._run = self._run_of(controls)
                self.keys += [name for name in wanted if name not in self.keys]

    def pending(self, controls: Any) -> list[str]:
        with self._lock:
            if self.keys and self._run is not None and self._run_of(controls) is not self._run:
                self.keys = []
            return list(self.keys)


# -- what a setting may be -----------------------------------------------------------------------


def _whole(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{name}: expected a whole number")
    if not low <= value <= high:
        raise SettingsError(f"{name}: must be between {low} and {high}")
    return value


# a panel's own smooth motion as it begins: off, and no clip named (the first panel's is its own)
PANEL_VIDEO = {"mode": "off", "local_clip": "", "clip_asked": False}


def _panel_video(name: str, value: Any) -> dict[str, Any]:
    """A panel's ``video``: ``{mode, local_clip, clip_asked}``, what differs from it to begin
    with kept (``{}`` for none)."""
    if not isinstance(value, dict) or set(value) - set(PANEL_VIDEO):
        raise SettingsError(f"{name}: expected {{mode, local_clip, clip_asked}}")
    mode = value.get("mode", "off")
    if mode not in ("auto", "off", "on"):
        raise SettingsError(f'{name}.mode: "auto", "off" or "on"')
    clip = _text(f"{name}.local_clip", value.get("local_clip", ""), 200)
    asked = _flag(f"{name}.clip_asked", value.get("clip_asked", False))
    given = {"mode": mode, "local_clip": clip, "clip_asked": asked}
    return {key: item for key, item in given.items() if item != PANEL_VIDEO[key]}


def _place_at(name: str, value: Any) -> list[int] | None:
    """Where a panel stands beside the others (``at``): [x, y] in millimetres, or None."""
    try:
        at = place_at(value, name)
    except ConfigError as exc:
        raise SettingsError(str(exc)) from exc
    return list(at) if at is not None else None


def _number(name: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SettingsError(f"{name}: expected a number")
    if not low <= value <= high:
        raise SettingsError(f"{name}: must be between {low:g} and {high:g}")
    return float(value)


def _text(name: str, value: Any, limit: int, required: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise SettingsError(f"{name}: expected text")
    value = value.strip()
    if required and not value:
        raise SettingsError(f"{name}: must not be empty")
    if len(value.encode("utf-8")) > limit:
        raise SettingsError(f"{name}: at most {limit} bytes")
    return value


_RATE = re.compile(r"^(auto|[0-9]+(\.[0-9]+)?[kKM]?)$")  # as config.toml takes video.maxrate


def _rate(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _RATE.match(value.strip()):
        raise SettingsError(f'{name}: "auto", or a bit rate such as "2M" or "1500k"')
    return value.strip()


def _choice(name: str, value: Any, allowed: tuple[Any, ...]) -> Any:
    if isinstance(value, bool) or value not in allowed or type(value) is not type(allowed[0]):
        raise SettingsError(f"{name}: must be one of {', '.join(str(a) for a in allowed)}")
    return value


def _time_of_day(name: str, value: Any) -> str:
    """``"7:30"`` as ``"07:30"``, or ``"sunset"`` and ``"sunrise"`` (the night's); anything
    else is refused."""
    from libre_panel.config import night_time

    text = night_time(value) if isinstance(value, str) else None
    if text is None:
        raise SettingsError(f'{name}: a time of day such as "22:00", or "sunset"')
    return text


def _flag(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise SettingsError(f"{name}: expected true or false")
    return value


def _option(name: str, value: Any, kind: str) -> Any:
    """A service's option as its spec says (the types of ``plugins.host.apply_options``)."""
    if kind == "int":
        return _whole(name, value, -(2**53), 2**53)
    if kind == "number":
        return _number(name, value, -1e18, 1e18)
    if kind == "bool":
        return _flag(name, value)
    if kind == "string":
        return _text(name, value, 500)
    if kind == "list":
        scalar = (str, int, float, bool)
        if (
            not isinstance(value, list)
            or len(value) > 200
            or not all(
                isinstance(item, scalar) and (not isinstance(item, float) or math.isfinite(item))
                for item in value
            )
        ):
            raise SettingsError(f"{name}: expected a list of texts or numbers")
        return value
    raise SettingsError(f"{name}: an option of a kind the settings do not know ({kind})")


def names_a_path(name: str) -> bool:
    """Whether the setting ``name`` ("video.ffmpeg", "services.cooling.database") names a
    program, a library, a file or a folder that Libre Panel starts, loads, reads or writes."""
    if name in LOCKED_WHILE_ELEVATED:
        return True
    parts = name.split(".")
    if len(parts) == 3 and parts[0] in ("sensors", "services"):
        return any(word in parts[2].lower() for word in PATH_WORDS)
    return False


def _plain(value: Any) -> Any:
    """``value`` as JSON can say it: a date in config.toml becomes its text, nan nothing."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)


# -- the facts about the panel and the system ----------------------------------------------------


def _screens(model: Any) -> int:
    from libre_panel.devices import jonsbo_zc360

    return jonsbo_zc360.SCREENS if model.id == jonsbo_zc360.MODEL_ID else 1


def _model_info(model: Any, port: str | None = None, sure: bool = True) -> dict[str, Any]:
    """A panel model as the app shows it: its name, its size the way round it is mounted,
    how it is connected."""
    return {
        "id": model.id,
        "label": model.label,
        "size": list(model.size("landscape")),
        "shape": model.shape,
        "connection": "usb" if model.protocol.startswith("usb") else "serial",
        "port": port,
        "sure": sure,
        "video": model.video,
        "limits": t(model.limits) if model.limits else "",
        # screens that show it together (the ZC-360: three), each told its number by "Which is
        # which?" as panels are
        "screens": _screens(model),
    }


def _declared(cls: Any) -> list[dict[str, Any]]:
    """The options a service class declares, as ``{key, type, default}``; one that is not
    ``(type, default)`` is left out, so a plugin's slip does not take the settings down."""
    declared = getattr(cls, "options", None)
    if not isinstance(declared, dict):
        return []
    return [
        {"key": str(key), "type": spec[0], "default": _plain(spec[1])}
        for key, spec in declared.items()
        if isinstance(spec, tuple | list) and len(spec) == 2 and isinstance(spec[0], str)
    ]


def cli_command(arguments: str) -> str:
    """The command-line program as one types it in a terminal: where a download is not on
    the PATH its own (``libre-panel.exe`` of the Windows download, the AppImage file),
    else ``libre-panel``."""
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            beside = Path(sys.executable).with_name("libre-panel.exe")
            if beside.is_file():
                return f'"{beside}" {arguments}'
        appimage = os.environ.get("APPIMAGE")  # the .AppImage file, not its temporary mount
        if appimage and sys.platform.startswith("linux"):
            return f'"{appimage}" {arguments}'
    return f"libre-panel {arguments}"


def connected_devices() -> list[str]:
    """What is plugged in that could be a panel, a line each, as ``libre-panel devices`` lists
    it but without serial numbers: the USB serial ports with their ids and the plain USB panels,
    and the models each could be. Only the system's lists are read; no port is opened."""
    from libre_panel.devices.base import DeviceError, list_serial_ports, list_usb_devices
    from libre_panel.devices.models import models_for_usb

    def models(vid: str | None, pid: str | None) -> str:
        found = models_for_usb(int(vid, 16), int(pid, 16)) if vid and pid else []
        return f"  -> {' / '.join(model.id for model in found)}" if found else ""

    lines = []
    try:
        for port in list_serial_ports():
            vid, pid = port["vid"], port["pid"]
            if vid:  # a port without USB ids (a mainboard's, Bluetooth) is no panel
                about = f"{port['description']}{models(vid, pid)}"
                lines.append(f"{port['device']}  {vid}:{pid}  {about}")
    except (DeviceError, OSError) as exc:
        lines.append(f"serial: {exc}")
    try:
        for device in list_usb_devices():
            vid, pid = device["vid"], device["pid"]
            lines.append(f"usb  {vid}:{pid}{models(vid, pid)}")
    except (DeviceError, OSError) as exc:
        lines.append(f"usb: {exc}")
    return lines or ["Nothing plugged in looks like a panel."]


def folders() -> dict[str, str]:
    """The folders the About page shows and opens: the settings, the user's themes, the log."""
    return {
        "config": str(config_dir()),
        "themes": str(user_themes_dir()),
        "logs": str(log_file().parent),
    }


@functools.lru_cache(maxsize=32)
def _reads(folder: Path, changed: float) -> frozenset[str]:
    from libre_panel.render.renderer import _reads as widget_reads
    from libre_panel.theme.model import load_theme
    from libre_panel.theme.modules import expand

    return frozenset(widget_reads(expand(load_theme(folder))[0]))


def theme_reads(theme_id: str) -> frozenset[str]:
    """The sensor keys (and patterns) the theme ``theme_id`` reads, its modules taken apart as
    the panel draws them; none for a theme that cannot be read. Kept until its file changes."""
    from libre_panel.theme.model import THEME_FILENAME, ThemeError, find_theme

    try:
        folder = find_theme(theme_id)
        return _reads(folder, (folder / THEME_FILENAME).stat().st_mtime)
    except (ThemeError, OSError, ValueError, KeyError, TypeError):
        log.debug("what %s reads is not known", theme_id, exc_info=True)
        return frozenset()


def notice_text() -> str:
    """NOTICE.md: the copy in the package (installed, bundled), else the project's own."""
    here = Path(__file__).resolve()
    for path in (here.parents[1] / "assets" / "NOTICE.md", here.parents[3] / "NOTICE.md"):
        try:
            return path.read_text(encoding="utf-8")[:MAX_NOTICE]
        except OSError:
            continue
    return ""


class Settings:
    """One request's view of the settings: config.toml, and the background app if there is one."""

    def __init__(self, config_path: Path | None, controls: Any, restart: Restart) -> None:
        self.config_path = config_path
        self.controls = controls
        self.restart = restart

    def _config(self) -> Config:
        try:
            return load_config(self.config_path)
        except ConfigError as exc:
            raise SettingsError(str(exc), 409) from exc

    def _follow(self) -> None:
        """Have the running app take in what was written (its services restart with their new
        options even while the panel is paused). Nothing happens in a plain editor."""
        follow = getattr(self.controls, "follow_config", None)
        if callable(follow):
            follow()

    # -- the values -------------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """config.toml as the app needs it, with the defaults filled in, and what it has
        to know to offer choices (the sources, the drivers, the services and their options)."""
        from libre_panel.sensors.librehardwaremonitor import DEFAULT_URL, MODES

        config = self._config()
        lhm = config.sensors.options.get("librehardwaremonitor", {})
        mode = str(lhm.get("mode", "auto")).lower()
        url = lhm.get("url")
        device, video, weather = config.device, config.device.video, config.weather
        return {
            "config": {
                "device": {
                    "model": device.model,
                    "driver": device.driver,
                    "brightness": device.brightness,
                    "rotate": device.rotate,
                    "orientation": device.orientation,
                    "motion": device.motion,
                    "dark_on_quit": device.dark_on_quit,
                    "dark_on_sleep": device.dark_on_sleep,
                    "night": device.night,
                    "night_from": device.night_from,
                    "night_to": device.night_to,
                    "night_brightness": device.night_brightness,
                    "dark_when_away": device.dark_when_away,
                    "away_brightness": device.away_brightness,
                    "output": device.output,
                    "port": device.port,
                    "at": list(device.at) if device.at else None,
                },
                "video": {
                    "mode": video.mode,
                    "fps": video.fps,
                    "device_fps": video.device_fps,
                    "crf": video.crf,
                    "preset": video.preset,
                    "keyframe_s": video.keyframe_s,
                    "maxrate": video.maxrate,
                    "profile": video.profile,
                    "encoder": video.encoder,
                    "local_clip": video.local_clip,
                    "clip_asked": video.clip_asked,
                    "ffmpeg": video.ffmpeg,
                },
                "sensors": {
                    "providers": list(config.sensors.providers),
                    "librehardwaremonitor": {
                        "mode": mode if mode in MODES else "auto",
                        "url": url if isinstance(url, str) and url else DEFAULT_URL,
                    },
                    "json": _json_sources(config),
                },
                "refresh_ms": config.refresh_ms,
                "weather": {
                    "enabled": weather.enabled,
                    "latitude": weather.latitude,
                    "longitude": weather.longitude,
                    "place": weather.place,
                    "units": weather.units,
                    "update_minutes": weather.update_minutes,
                },
                "services": {
                    "enabled": list(config.services.enabled),
                    "options": _plain(config.services.options),
                },
                "language": config.language,
                "check_updates": config.check_updates,
                "allow_notify": config.allow_notify,
                "light_mode": config.light_mode,
                "hotkeys": dict(config.hotkeys),
                "hotkeys_enabled": config.hotkeys_enabled,
                "symbols": config.symbols,
                "accent": config.accent,
                "across": config.across,
                "switches": {theme: dict(chosen) for theme, chosen in config.switches.items()},
                # the panels beside this one ([[panels]]): written back whole ("panels")
                "panels": [
                    {
                        "theme": panel.theme,
                        "on": panel.on,
                        "follow_brightness": panel.follow_brightness,
                        "brightness_offset": panel.brightness_offset,
                        "game_theme": panel.game_theme,
                        "media_theme": panel.media_theme,
                        "alarm_theme": panel.alarm_theme,
                        "same_look": panel.same_look,
                        "video": {
                            "mode": panel.video_mode,
                            "local_clip": panel.local_clip,
                            "clip_asked": panel.clip_asked,
                        },  # fmt: skip
                        **_device_table(panel.device),
                    }
                    for panel in config.panels
                ],  # fmt: skip
            },
            "catalog": self._catalog(),
            "restart": self.restart.pending(self.controls),
        }

    def _catalog(self) -> dict[str, Any]:
        from libre_panel.devices.base import available_drivers
        from libre_panel.plugins.loader import BUILT_IN, registry
        from libre_panel.sensors.base import available_providers
        from libre_panel.sensors.librehardwaremonitor import MODES

        found = registry().parts.get("services", {})
        rank = {name: place for place, name in enumerate(SERVICE_ORDER)}
        names = sorted(found, key=lambda name: (rank.get(name, len(rank)), name))
        services = []
        for name in names:
            cls = registry().get("services", name)
            services.append(
                {
                    "name": name,
                    "source": found[name].source,
                    "builtin": found[name].source == BUILT_IN,
                    "error": found[name].error if cls is None else "",
                    "options": _declared(cls),
                }
            )
        return {
            "platform": sys.platform,
            "providers": [
                {"name": name, "source": source, "builtin": source == BUILT_IN}
                for name, source in available_providers().items()
            ],
            "implicit_providers": list(IMPLICIT_SOURCES),
            "default_providers": default_providers(),
            "lhm_modes": list(MODES),
            "drivers": sorted(available_drivers()),
            "services": services,
        }

    # -- writing ----------------------------------------------------------------------------

    def write(self, payload: Any) -> dict[str, Any]:
        """``{"set": {"device.brightness": 70, "refresh_ms": null}}``: any number of settings
        in one write to config.toml, each as ``null`` (to take it out, where that is a thing)
        or a value its rule accepts. Answers like :meth:`read`."""
        changes = payload.get("set") if isinstance(payload, dict) else None
        if not isinstance(changes, dict) or not changes or len(changes) > 60:
            raise SettingsError('expected {"set": {"device.brightness": 70, ...}}')
        with _WRITING:
            config = self._config()
            edits = []
            for name, value in changes.items():
                kind, cleaned = self._check(str(name), value, config)
                edits.append((kind, str(name), cleaned))
            self._check_together(changes, config)
            if "panels" in changes and config.scene:  # a panel's own theme: the scene is off
                edits.append(("unset", "scene", None))
            try:
                edit_config(*edits, path=self.config_path)
            except (ConfigError, OSError) as exc:
                raise SettingsError(str(exc), 409) from exc
        self.restart.written([name for _, name, _ in edits], self.controls)
        self._follow()
        return self.read()

    @staticmethod
    def _current(name: str, config: Config) -> Any:
        """What config.toml makes of the setting ``name`` now, as far as a rule that locks it
        needs to know: None for a setting that is not there."""
        parts = name.split(".")
        if name == "video.ffmpeg":
            return config.device.video.ffmpeg
        if name == "device.output":
            return config.device.output
        if len(parts) == 3 and parts[0] in ("sensors", "services"):
            tables = config.sensors.options if parts[0] == "sensors" else config.services.options
            return tables.get(parts[1], {}).get(parts[2])
        return None

    def _check_rights(self, name: str, value: Any, config: Config, default: Any = None) -> None:
        """Refuse a change of what names a program or a file while Libre Panel runs with
        administrator rights: it would start or write it with them. What config.toml says
        already, said again, is no change. (A request cannot be told from the person's, so the
        person makes such a change in config.toml, in their own folder.)"""
        if not names_a_path(name) or not access.elevated():
            return
        current = self._current(name, config)
        before = default if current is None else current
        after = default if value is None else value
        if after == before:
            return
        raise _refused_with_rights(name)

    def _check(self, name: str, value: Any, config: Config) -> tuple[str, Any]:
        """The change ``("value" or "unset", cleaned value)`` for a setting, or the error."""
        if name.startswith("services."):
            return self._check_option(name, value, config)
        if name.startswith("switches."):
            return _check_switch(name, value)
        if name == "panels":
            return "tables", self._check_panels(value, config)
        if name == JSON_SOURCES:
            return "tables", _check_json_sources(value, config)
        self._check_rights(name, value, config)
        if value is None:
            if name not in UNSETTABLE:
                raise SettingsError(f"{name}: cannot be taken out")
            return "unset", None
        rule = RULES.get(name)
        if rule is None:
            raise SettingsError(f"unknown setting {name!r}")
        return "value", rule(name, value, config)

    def _check_panels(self, value: Any, config: Config) -> list[dict[str, Any]]:
        """``panels``: the panels beside this one, each a table of ``theme``, ``on`` and the
        keys of ``[device]``, as :meth:`read` hands them; written as ``[[panels]]`` with what
        differs from the defaults."""
        if not isinstance(value, list) or len(value) > MORE_PANELS:
            raise SettingsError(f"panels: a list of at most {MORE_PANELS} panels")
        tables = []
        for n, table in enumerate(value):
            if not isinstance(table, dict):
                raise SettingsError(f"panels[{n}]: expected a table")
            default = {"theme": "", "on": True, "follow_brightness": True, "brightness_offset": 0,
                       "game_theme": "", "media_theme": "", "alarm_theme": ALARM_THEME,
                       "same_look": False,
                       "video": {}, **_device_table(_panel_device(n))}  # fmt: skip
            now = config.panels[n].device.output if n < len(config.panels) else default["output"]
            kept = {}
            for key, item in table.items():
                name = f"panels[{n}].{key}"
                if key in ("theme", "game_theme", "media_theme", "alarm_theme"):
                    cleaned = _text(name, item, 64)
                elif key in ("on", "follow_brightness", "same_look"):
                    cleaned = _flag(name, item)
                elif key == "brightness_offset":
                    cleaned = _whole(name, item, -50, 50)
                elif key == "video":
                    cleaned = _panel_video(name, item)
                elif key in DeviceConfig.__dataclass_fields__ and key not in _NOT_IN_TABLE:
                    cleaned = RULES[f"device.{key}"](name, item, config)
                else:
                    raise SettingsError(f"unknown setting {name!r}")
                if key == "output" and cleaned != now and access.elevated():
                    raise _refused_with_rights(name)  # (as device.output)
                if cleaned != default[key]:
                    kept[key] = cleaned
            tables.append(kept)
        return tables

    def _check_option(self, name: str, value: Any, config: Config) -> tuple[str, Any]:
        """``services.<name>.<option>``, as the service's ``options`` spec says. ``null`` takes
        it out: the service's default holds again."""
        from libre_panel.plugins.loader import registry

        parts = name.split(".")
        if len(parts) != 3 or not all(parts):
            raise SettingsError(f"unknown setting {name!r}")
        _, service, option = parts
        cls = registry().get("services", service)
        if cls is None:
            raise SettingsError(f"{service} is not an installed service", 404)
        spec = next((o for o in _declared(cls) if o["key"] == option), None)
        if spec is None:
            raise SettingsError(f"{service} has no option {option!r}")
        if spec["type"] == "string":  # (a file or a folder is a text)
            self._check_rights(name, value, config, default=spec["default"])
        if value is None:
            return "unset", None
        return "value", _option(name, value, spec["type"])

    def _check_together(self, changes: dict[str, Any], config: Config) -> None:
        """What only holds across settings: the place is both coordinates or none, and the
        weather needs one."""
        weather = config.weather

        def after(name: str, current: Any) -> Any:
            return changes[name] if name in changes else current

        lat = after("weather.latitude", weather.latitude)
        lon = after("weather.longitude", weather.longitude)
        if ("weather.latitude" in changes or "weather.longitude" in changes) and (
            (lat is None) != (lon is None)
        ):
            raise SettingsError("weather.latitude and weather.longitude go together")
        if after("weather.enabled", weather.enabled) is True and (lat is None or lon is None):
            raise SettingsError(t("Choose a place first: the weather needs it."))

    # -- scenes ---------------------------------------------------------------------------

    def scenes(self) -> dict[str, Any]:
        """The scenes and the one that is on (scenes.py)."""
        from libre_panel import scenes

        try:
            return scenes.state(self.config_path)
        except ConfigError as exc:
            raise SettingsError(str(exc), 409) from exc

    def set_scene(self, payload: Any) -> dict[str, Any]:
        """``{"action": "on" | "save" | "delete", "name": ...}``: put a scene on ("": none),
        keep what the panels show as one, or take one out. Answers like :meth:`scenes`."""
        from libre_panel import scenes

        action = payload.get("action") if isinstance(payload, dict) else None
        name = payload.get("name", "") if isinstance(payload, dict) else None
        if action not in ("on", "save", "delete") or not isinstance(name, str):
            raise SettingsError('expected {"action": "on" | "save" | "delete", "name": ...}')
        with _WRITING:
            try:
                done = {"on": scenes.put_on, "save": scenes.save, "delete": scenes.delete}[action](
                    name, self.config_path
                )
            except ConfigError as exc:
                raise SettingsError(str(exc)) from exc
        self._follow()
        return done

    # -- the panel --------------------------------------------------------------------------

    def panel(self) -> dict[str, Any]:
        """The panel that is found (looked for on the bus, without opening it, so a running
        panel is not disturbed) and what the video mode makes of it."""
        from libre_panel.devices.models import find_model
        from libre_panel.devices.turzx import video_gaps, video_mode

        config = self._config()
        found = self._find_panel(config)
        configured = find_model(config.device.model)
        model = configured or (found["model"] if found else None)
        video = config.device.video
        usb = model is None or model.protocol == "usb-turing"
        if video.mode == "off" or not usb:
            gaps: list[str] = []
        elif video.mode == "auto":
            gaps = video_gaps(video, model) if model is not None else []
        else:
            gaps = video_gaps(video, model, proven=False)
        running = None
        shown = None
        spare: list[dict[str, Any]] = []
        panel = getattr(self.controls, "panel", None)
        if panel is not None:
            state = panel.state()
            spare = self._spare_panels(state)
            running = {"state": state["state"], "detail": state["detail"],
                       "size": state.get("size"), "more": state.get("more", [])}  # fmt: skip
            shown = state.get("theme") or None
        return {
            "configured": config.device.model,
            "found": found["info"] if found else None,
            # each panel beside this one: whether smooth motion runs on it, what it lacks
            "more_video": self._more_video(config, running),
            # a port with the ids other USB devices have too: the person says what it is
            "maybe": self._maybe_panels() if found is None and configured is None else [],
            "model": _model_info(model) if model is not None else None,
            "running": running,
            # panels plugged in that no panel of the app shows anything on: one more to set up
            "spare": spare,
            "video": {
                "mode": video.mode,
                "fps": video.fps,
                "on": video_mode(video, model)[0] if usb else False,
                "usb": usb,
                "gaps": gaps,
            },
            "weather_theme": self._weather_theme(config, shown),
        }

    @staticmethod
    def _weather_theme(config: Config, shown: str | None) -> str | None:
        """The theme on the panel (else the normal one) when it shows the weather, which the
        app then asks for while the weather is off: its id, else None."""
        from libre_panel.theme.defaults import normal_theme

        for theme_id in dict.fromkeys(filter(None, (shown, normal_theme(config)))):
            if any(key.startswith("weather.") for key in theme_reads(theme_id)):
                return theme_id
        return None

    def clip_question(self) -> dict[str, Any]:
        """Whether Start is to ask for the name of the panel's standby clip, and the name the
        vendor app keeps it under, which the question offers. Nothing is written by asking: the
        person's answer is a write of ``video.local_clip`` and ``video.clip_asked``."""
        from libre_panel.devices import turzx
        from libre_panel.devices.turzx_usb import present_model

        def on_the_bus() -> Any:
            try:
                return present_model(self._config().device.beside)
            except Exception:  # no USB library is no panel
                log.debug("looking for a USB panel failed", exc_info=True)
                return None

        ask = turzx.clip_question(self._config().device, on_the_bus)
        return {"ask": ask, "name": turzx.VENDOR_CLIP}

    @staticmethod
    def _more_video(config: Config, running: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Smooth motion on each panel of ``[[panels]]``: ``{index, usb, on, gaps}``, as for the
        first panel (its own mode and clip, the first panel's ffmpeg), the model as set or as
        the running panel said it is."""
        from libre_panel.devices.models import find_model
        from libre_panel.devices.turzx import video_gaps, video_mode

        told = {item["index"]: item.get("model") or "" for item in (running or {}).get("more", [])}
        result = []
        for n in range(len(config.panels)):
            video = config.for_panel(n).device.video
            model = find_model(config.panels[n].device.model) or find_model(told.get(n, ""))
            usb = model is None or model.protocol == "usb-turing"
            if video.mode == "off" or not usb:
                gaps: list[str] = []
            elif video.mode == "auto":
                gaps = video_gaps(video, model) if model is not None else []
            else:
                gaps = video_gaps(video, model, proven=False)
            on = video_mode(video, model)[0] if usb else False
            result.append({"index": n, "usb": usb, "on": on, "gaps": gaps})
        return result

    @staticmethod
    def _find_panel(config: Config) -> dict[str, Any] | None:
        """A panel on the bus: a TURZX USB one, a Lian Li screen, a ZC-360, else a serial one by
        its USB ids."""
        from libre_panel.devices import jonsbo_zc360, lianli_usb
        from libre_panel.devices.models import get_model
        from libre_panel.devices.serial_link import find_port
        from libre_panel.devices.turzx_usb import present_model

        try:
            model = present_model(config.device.beside) or lianli_usb.present_model()
            if model is None and jonsbo_zc360.present():
                model = get_model(jonsbo_zc360.MODEL_ID)
        except Exception:  # no USB library is no panel
            log.debug("looking for a USB panel failed", exc_info=True)
            model = None
        if model is not None:
            return {"model": model, "info": _model_info(model)}
        try:
            port = find_port(None, config.device.port)
        except Exception:  # no pyserial, or a port that is not a panel's
            log.debug("looking for a serial panel failed", exc_info=True)
            port = None
        if port is not None:
            return {"model": port.model, "info": _model_info(port.model, port.device, port.sure)}
        return None

    @staticmethod
    def _spare_panels(state: dict[str, Any]) -> list[dict[str, Any]]:
        """The panels on the bus that no loop of the app has open, while every one runs (one
        that is still looking for its panel may be about to take it): each with its model
        and, for a serial one, its port."""
        from libre_panel.devices.serial_link import free_panels
        from libre_panel.devices.turzx_usb import free_models

        if state.get("state") != "showing":
            return []
        if any(more.get("state") != "showing" for more in state.get("more", [])):
            return []
        found = []
        try:
            found += [_model_info(model) for model in free_models()]
        except Exception:  # no USB library is no panel
            log.debug("looking for more USB panels failed", exc_info=True)
        try:
            found += [_model_info(p.model, p.device, p.sure) for p in free_panels()]
        except Exception:  # no pyserial
            log.debug("looking for more serial panels failed", exc_info=True)
        return found

    @staticmethod
    def _maybe_panels() -> list[dict[str, Any]]:
        """The ports with the generic ids of an awake Turing rev. C panel, which Libre Panel
        does not talk to unasked (serial_link.gadget_ports): each with the panels it may be."""
        from libre_panel.devices.serial_link import gadget_ports

        try:
            ports = gadget_ports()
        except Exception:  # no pyserial
            log.debug("looking for gadget ports failed", exc_info=True)
            return []
        return [
            {"port": p.device, "models": [_model_info(m, p.device, False) for m in p.candidates]}
            for p in ports
        ]

    # -- the system -------------------------------------------------------------------------

    def _autostart(self) -> tuple[Any, list[str]]:
        from libre_panel.autostart import Autostart, launch_command

        autostart = getattr(self.controls, "autostart", None) or Autostart()
        command = getattr(self.controls, "autostart_command", None) or launch_command(
            self.config_path
        )
        return autostart, command

    def system(self) -> dict[str, Any]:
        """Starting with the system, and the language."""
        from libre_panel.autostart import writable_by_user

        autostart, command = self._autostart()
        config = self._config()
        kind = autostart.kind
        try:
            enabled = autostart.is_enabled() if kind != "unsupported" else None
        except OSError:
            enabled = None
        return {
            "kind": kind,  # windows, xdg or unsupported
            "enabled": enabled,
            "elevated": autostart.elevated() if kind == "windows" else False,
            # a task of that name that is off or starts another program (a leftover)
            "task_hint": autostart.task_hint() if kind == "windows" else "",
            "can_elevate": autostart.can_elevate(),
            "location": autostart.location() if kind != "unsupported" else "",
            "command": cli_command("autostart enable --elevated"),
            "program": command[0] if command else "",
            "risky": bool(command) and writable_by_user(Path(command[0]), Path.home()),
            "language": {
                "setting": config.language,
                "active": i18n.language(),
                "languages": dict(i18n.LANGUAGES),
            },
        }

    def set_system(self, payload: Any) -> dict[str, Any]:
        """``{"autostart": true}`` or ``{"elevated": true}``: what the command line's
        ``autostart enable`` and ``disable`` do. Starting with the highest rights (Windows) is
        a thing of its own and needs an administrator; it is never done unasked."""
        from libre_panel.autostart import AutostartError

        if not isinstance(payload, dict) or not payload or set(payload) - {"autostart", "elevated"}:
            raise SettingsError('expected {"autostart": true} or {"elevated": true}')
        for name, value in payload.items():
            _flag(name, value)
        autostart, command = self._autostart()
        try:
            if "elevated" in payload:
                if payload["elevated"]:
                    autostart.enable(command, elevated=True)
                elif autostart.elevated(fresh=True):  # back to the plain start
                    autostart.disable()
                    autostart.enable(command)
            elif payload["autostart"]:
                autostart.enable(command)
            else:
                autostart.disable()
        except AutostartError as exc:
            raise SettingsError(str(exc), 409) from exc
        except OSError as exc:
            raise SettingsError(str(exc), 500) from exc
        return self.system()

    # -- the one-click setup of the system (Windows' sensor service, Linux' rights) -------------

    def unlock(self) -> dict[str, Any]:
        """What the setup of this system would bring and how a run of it is going. Windows: the
        sensor service and what else "Unlock all sensors" sets up (whether this download can,
        what is in place). Linux: the panel's permission, the mainboard's sensor driver and the
        processor's power (``libre-panel system-setup --linux``). Both have ``platform``,
        ``available``, ``command`` and ``job``."""
        from libre_panel import linux_rights, linux_setup

        if linux_setup.supported():
            return linux_rights.status(linux_setup.gather())
        from libre_panel.sensorservice import setup

        state = setup.unlock_state()
        if state.get("platform") == "win32":
            state["presentmon"] = self._presentmon()
        return {
            **state,
            "command": cli_command("system-setup --service --pawnio --perf-log-users"),
        }

    def _presentmon(self) -> bool | None:
        """Whether the program that reads the frame rates of games is there (the Windows
        downloads carry it); None while config.toml cannot be read."""
        from libre_panel.sensors.presentmon import find_presentmon

        try:
            options = self._config().sensors.options.get("presentmon", {})
        except SettingsError:
            return None
        path = options.get("path")
        return find_presentmon(path if isinstance(path, str) else "") is not None

    def set_unlock(self, payload: Any) -> dict[str, Any]:
        """``{"action": "unlock"}`` (or ``"remove"``): ``libre-panel system-setup`` with the
        system's own question for administrator rights (UAC on Windows, polkit's password window
        on Linux, where ``"rapl": true`` or ``false`` also opens or closes the processor's energy
        counters). The answer comes at once; the run goes on in the background, and ``job`` of
        :meth:`unlock` says how it ended."""
        from libre_panel import linux_setup

        if linux_setup.supported():
            return self._unlock_linux(payload)
        from libre_panel.sensorservice import setup

        action = payload.get("action") if isinstance(payload, dict) else None
        if action not in setup.ACTIONS:
            raise SettingsError('expected {"action": "unlock"} or {"action": "remove"}')
        program = setup.cli_program()
        if not self.unlock()["available"] or program is None:
            raise SettingsError(
                t("The sensor service comes with the Windows downloads (setup or zip)."), 409
            )
        setup.ELEVATOR.start(action, program, setup.current_user())
        return self.unlock()

    def _unlock_linux(self, payload: Any) -> dict[str, Any]:
        """The Linux side of :meth:`set_unlock`: pkexec shows the password dialog, and what the
        run is to do is only ever the two switches of ``system-setup --linux``."""
        from libre_panel import linux_rights, linux_setup

        valid = isinstance(payload, dict) and not set(payload) - {"action", "rapl"}
        action = payload.get("action") if valid else None
        if action not in ("unlock", "remove"):
            raise SettingsError('expected {"action": "unlock"} or {"action": "remove"}')
        rapl = payload.get("rapl")
        if rapl is not None:
            _flag("rapl", rapl)
            if action == "remove":
                raise SettingsError('expected {"action": "unlock", "rapl": true}')
        options = linux_setup.Options(rapl=rapl, remove=action == "remove")
        way = linux_rights.elevation(options, linux_rights.program())
        if access.elevated():  # a request cannot be told from the person's: no rights are used
            raise SettingsError(
                t(
                    "Libre Panel runs with administrator rights. Run this in a terminal: {command}",
                    command=way.terminal,
                ),
                403,
            )
        if not way.argv:
            raise SettingsError(
                t(
                    "Libre Panel cannot ask for the administrator's rights from here. Run this "
                    "in a terminal: {command}",
                    command=way.terminal,
                ),
                409,
            )
        if not linux_rights.JOB.start(way, action=action):
            raise SettingsError(t("The setup is running already."), 409)
        return self.unlock()

    # -- the place --------------------------------------------------------------------------

    def search_place(self, payload: Any) -> dict[str, Any]:
        """``{"query": "name"}``: places of that name from Open-Meteo's search. The text typed
        goes there, nothing else, and only when the person asks."""
        from libre_panel.weather import open_meteo

        query = payload.get("query") if isinstance(payload, dict) else None
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 100:
            raise SettingsError("expected a name of 2 to 100 characters")
        try:
            found = open_meteo.geocode(query.strip(), count=PLACES, language=i18n.language())
        except open_meteo.WeatherError as exc:
            raise SettingsError(
                t("Could not reach the place search ({error}).", error=exc), 502
            ) from exc
        places = []
        for place in found:
            lat, lon = place.get("latitude"), place.get("longitude")
            if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (lat, lon)):
                continue
            label = ", ".join(p for p in (place["name"], place["region"], place["country"]) if p)
            # a kilometre is plenty for the weather and the sun, and it is all that is kept
            places.append({"name": label, "latitude": round(lat, 2), "longitude": round(lon, 2)})
        return {"places": places}

    # -- about ------------------------------------------------------------------------------

    def about(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "license": LICENSE,
            "notice": notice_text(),
            "links": {
                "project": PROJECT_URL,
                "issues": f"{PROJECT_URL}/issues",
                "hardware_report": REPORT_FORM,
            },
            "doctor": cli_command("doctor"),
            "devices": cli_command("devices"),
            "folders": folders(),
        }

    def report(self) -> dict[str, Any]:
        """What a report of a panel says of this computer, for the form on GitHub: Libre Panel's
        version, the system, the panel found and what is plugged in that could be one, by its USB
        ids (what ``libre-panel devices`` prints, without serial numbers, names or folders). The
        bus is only looked at, as for :meth:`panel`: nothing is sent to a panel. ``form`` is the
        form's address with the product and this text filled in."""
        config = self._config()
        found = self._find_panel(config)
        product = found["model"].label if found else ""
        lines = [
            f"Libre Panel {__version__}, {platform.system()} {platform.release()} "
            f"({platform.machine()})",
            f"Panel found: {product or 'none'}",
            *connected_devices(),
        ]
        text = "\n".join(lines)
        fields = {"product": product, "devices": text} if product else {"devices": text}
        return {
            "text": text,
            "product": product,
            "form": f"{REPORT_FORM}&{urlencode(fields)}",
            "platform": sys.platform,  # how a terminal is opened, for the doctor
        }

    def open_folder(self, payload: Any) -> dict[str, Any]:
        """``{"folder": "config"}`` (or ``themes``, ``logs``): show it in the file manager."""
        from libre_panel.tray import open_path

        name = payload.get("folder") if isinstance(payload, dict) else None
        if name not in FOLDERS:
            raise SettingsError(f"folder is one of {', '.join(FOLDERS)}")
        folder = Path(folders()[name])
        try:
            folder.mkdir(parents=True, exist_ok=True)
            open_path(folder)
        except OSError as exc:
            raise SettingsError(str(exc), 501) from exc
        return {"opened": name}


# -- the rules, by the name of the setting -------------------------------------------------------


def _refused_with_rights(name: str) -> SettingsError:
    return SettingsError(
        t(
            "While Libre Panel runs with administrator rights, {name} is not changed here: it "
            "names a program or a file that would then be started or written with those "
            "rights. Set it in config.toml in your settings folder; Libre Panel reads it "
            "from there.",
            name=name,
        ),
        403,
    )


# what [device] has that a panel's table does not: the video's settings are their own, and
# ``beside`` is worked out from [[panels]]
_NOT_IN_TABLE = ("video", "beside")


def _device_table(device: DeviceConfig) -> dict[str, Any]:
    """A panel's settings, as ``[device]`` has them."""
    return {
        key: getattr(device, key)
        for key in DeviceConfig.__dataclass_fields__
        if key not in _NOT_IN_TABLE
    }


def _panel_device(n: int) -> DeviceConfig:
    """What a panel beside the first, ``[[panels]]`` at ``n``, has by default."""
    return DeviceConfig(**panel_defaults(n))


def _rule_accent(name: str, value: Any, config: Config) -> str:
    """Empty (the themes' own colours), "system" (Windows') or a colour such as "#E0457B"."""
    from libre_panel.accent import HEX, SYSTEM

    text = _text(name, value, 7).strip()
    if text and text != SYSTEM and not HEX.fullmatch(text):
        raise SettingsError(f'{name}: "system", a colour such as "#E0457B", or empty')
    return text.upper() if text.startswith("#") else text


JSON_SOURCES = "sensors.json.sources"
JSON_MOST = 16  # sources (sensors.json_source.MAX_SOURCES)


def _json_sources(config: Config) -> dict[str, Any]:
    """``[sensors.json]`` for the app: how often, and each source as the form edits it. The
    headers (a token, a password) are not handed out: the form says whether there are some,
    and keeps them as they are."""
    options = config.sensors.options.get("json", {})
    sources = []
    for entry in options.get("sources") or []:
        if not isinstance(entry, dict):
            continue
        fields, units = entry.get("fields"), entry.get("units")
        sources.append({
            "name": str(entry.get("name") or ""),
            "where": str(entry.get("url") or entry.get("file") or ""),
            "fields": {str(k): str(v) for k, v in (fields or {}).items()}
            if isinstance(fields, dict) else {},
            "units": {str(k): str(v) for k, v in (units or {}).items()}
            if isinstance(units, dict) else {},
            "headers": bool(entry.get("headers")),
        })  # fmt: skip
    every = options.get("update_s", 30)
    return {"update_s": every if isinstance(every, int | float) else 30, "sources": sources}


def _pairs(name: str, value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 100:
        raise SettingsError(f"{name}: a table of at most 100 names")
    found = {}
    for key, item in value.items():
        key = _text(f"{name}.{key}", key, 40, required=True)
        found[key] = _text(f"{name}.{key}", item, 200)
    return found


def _check_json_sources(value: Any, config: Config) -> list[dict[str, Any]]:
    """``sensors.json.sources`` as the app's form sends it (each ``{name, where, fields,
    units}``): written as ``[[sensors.json.sources]]``, ``where`` as ``url`` (http:// or
    https://) or ``file``, the headers of a source of the same name kept. A file not named
    yet is refused while Libre Panel runs with administrator rights."""
    if not isinstance(value, list) or len(value) > JSON_MOST:
        raise SettingsError(f"{JSON_SOURCES}: a list of at most {JSON_MOST} sources")
    before = {
        str(entry.get("name")): entry.get("headers")
        for entry in config.sensors.options.get("json", {}).get("sources") or []
        if isinstance(entry, dict) and entry.get("headers")
    }
    files = {  # read with the rights Libre Panel has: a new one only in config.toml then
        str(entry.get("file"))
        for entry in config.sensors.options.get("json", {}).get("sources") or []
        if isinstance(entry, dict) and entry.get("file")
    }
    tables, names = [], set()
    for n, source in enumerate(value):
        where = f"{JSON_SOURCES}[{n}]"
        if not isinstance(source, dict):
            raise SettingsError(f"{where}: expected a table")
        name = _text(f"{where}.name", source.get("name"), 40, required=True)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise SettingsError(f"{where}.name: letters, digits, - and _ only")
        if name in names:
            raise SettingsError(f"{where}.name: {name!r} is there twice")
        names.add(name)
        place = _text(f"{where}.where", source.get("where"), 300, required=True)
        table: dict[str, Any] = {"name": name}
        table["url" if place.startswith(("http://", "https://")) else "file"] = place
        if "file" in table and place not in files and access.elevated():
            raise _refused_with_rights(f"{where}.where")
        fields = _pairs(f"{where}.fields", source.get("fields") or {})
        units = _pairs(f"{where}.units", source.get("units") or {})
        if fields:
            table["fields"] = fields
        if units:
            table["units"] = units
        if name in before:
            table["headers"] = before[name]
        tables.append(table)
    return tables


def _rule_providers(name: str, value: Any, config: Config) -> list[str]:
    from libre_panel.sensors.base import available_providers

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SettingsError(f"{name}: expected a list of source names")
    known = set(available_providers()) | set(config.sensors.providers)
    unknown = [item for item in value if item not in known]
    if unknown:
        raise SettingsError(f"{name}: unknown source {unknown[0]!r}")
    chosen = list(dict.fromkeys(value))
    if not chosen:
        raise SettingsError(f"{name}: at least one source")
    if chosen == [
        "psutil"
    ]:  # what the files of 0.4 named alone: config.py reads it as the usual list
        raise SettingsError(f"{name}: psutil alone stands for the usual list; keep another source")
    return chosen


def _rule_driver(name: str, value: Any, config: Config) -> str:
    from libre_panel.devices.base import available_drivers

    return _choice(name, value, tuple(sorted(available_drivers())))


def _rule_model(name: str, value: Any, config: Config) -> str:
    from libre_panel.devices.models import find_model

    if not isinstance(value, str) or (value != "auto" and find_model(value) is None):
        raise SettingsError(f"{name}: auto, or a model from: libre-panel models")
    return value


def _rule_url(name: str, value: Any, config: Config) -> str:
    value = _text(name, value, 300, required=True)
    if not value.startswith(("http://", "https://")):
        raise SettingsError(f"{name}: an address that starts with http:// or https://")
    return value


def _rule_hotkey(name: str, value: Any, config: Config) -> str:
    """A shortcut, written the one way (``Ctrl+Alt+P``), that no other action has."""
    from libre_panel.hotkeys import parse

    try:
        combo = str(parse(_text(name, value, 40, required=True)))
    except ValueError as exc:
        raise SettingsError(f"{name}: {exc}") from None
    action = name.split(".", 1)[1]
    twin = next((other for other, given in config.hotkeys.items() if given == combo), None)
    if twin and twin != action:
        raise SettingsError(t("{combo} is the shortcut of another action already", combo=combo))
    return combo


def _check_switch(name: str, value: Any) -> tuple[str, Any]:
    """``switches.<switch>``: on or off for every theme that has it; ``switches.<theme>.<switch>``
    for that theme and all its versions. ``null`` takes the choice out: what holds for every
    theme, or the theme's own, holds again."""
    parts = name.split(".")
    if len(parts) not in (2, 3) or not all(_SWITCH_PART.match(part) for part in parts[1:]):
        raise SettingsError(f"unknown setting {name!r}")
    if value is None:
        return "unset", None
    return "value", _flag(name, value)


_SWITCH_PART = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


RULES: dict[str, Any] = {
    "device.model": _rule_model,
    "device.driver": _rule_driver,
    "device.brightness": lambda n, v, c: _whole(n, v, 0, 100),
    "device.rotate": lambda n, v, c: _choice(n, v, (0, 180)),
    "device.orientation": lambda n, v, c: _choice(n, v, ("", "landscape", "portrait")),
    "device.motion": lambda n, v, c: _choice(n, v, MOTIONS),
    "device.dark_on_quit": lambda n, v, c: _flag(n, v),
    "device.dark_on_sleep": lambda n, v, c: _flag(n, v),
    "device.night": lambda n, v, c: _flag(n, v),
    "device.night_from": lambda n, v, c: _time_of_day(n, v),
    "device.night_to": lambda n, v, c: _time_of_day(n, v),
    "device.night_brightness": lambda n, v, c: _whole(n, v, 0, 100),
    "device.dark_when_away": lambda n, v, c: _flag(n, v),
    "device.away_brightness": lambda n, v, c: _whole(n, v, 0, 100),
    "device.at": lambda n, v, c: _place_at(n, v),
    "across": lambda n, v, c: _text(n, v, 300).strip(),
    "device.output": lambda n, v, c: _text(n, v, 260, required=True),
    "device.port": lambda n, v, c: _text(n, v, 64),
    "video.mode": lambda n, v, c: _choice(n, v, ("auto", "on", "off")),
    "video.fps": lambda n, v, c: _whole(n, v, 1, 60),
    "video.device_fps": lambda n, v, c: _whole(n, v, 1, 120),
    "video.crf": lambda n, v, c: _whole(n, v, 0, 51),
    "video.preset": lambda n, v, c: _choice(n, v, X264_PRESETS),
    "video.keyframe_s": lambda n, v, c: _number(n, v, 1, 60),
    "video.maxrate": lambda n, v, c: _rate(n, v),
    "video.profile": lambda n, v, c: _choice(n, v, PROFILES),
    "video.encoder": lambda n, v, c: _choice(n, v, ENCODERS),
    "video.local_clip": lambda n, v, c: _text(n, v, 200),
    "video.clip_asked": lambda n, v, c: _flag(n, v),
    "video.ffmpeg": lambda n, v, c: _text(n, v, 260),
    "refresh_ms": lambda n, v, c: _whole(n, v, 100, 3_600_000),
    "check_updates": lambda n, v, c: _flag(n, v),
    "allow_notify": lambda n, v, c: _flag(n, v),
    "light_mode": lambda n, v, c: _flag(n, v),
    "symbols": lambda n, v, c: _choice(n, v, SYMBOL_PACKS),
    "accent": _rule_accent,
    "hotkeys.enabled": lambda n, v, c: _flag(n, v),
    "hotkeys.pause": _rule_hotkey,
    "hotkeys.brightness_up": _rule_hotkey,
    "hotkeys.brightness_down": _rule_hotkey,
    "hotkeys.open": _rule_hotkey,
    "hotkeys.next_scene": _rule_hotkey,
    "hotkeys.timer": _rule_hotkey,
    "hotkeys.timer_reset": _rule_hotkey,
    "hotkeys.mic_mute": _rule_hotkey,
    "sensors.providers": _rule_providers,
    "sensors.librehardwaremonitor.mode": lambda n, v, c: _choice(
        n, v, ("auto", "service", "inprocess", "webserver")
    ),
    "sensors.librehardwaremonitor.url": _rule_url,
    "sensors.json.update_s": lambda n, v, c: _whole(n, v, 2, 3600),
    "weather.enabled": lambda n, v, c: _flag(n, v),
    "weather.units": lambda n, v, c: _choice(n, v, ("metric", "imperial")),
    "weather.update_minutes": lambda n, v, c: _whole(n, v, 5, 1440),
    "weather.latitude": lambda n, v, c: _number(n, v, -90, 90),
    "weather.longitude": lambda n, v, c: _number(n, v, -180, 180),
    "weather.place": lambda n, v, c: _text(n, v, 120),
}
# What can be taken out of config.toml, so that its default holds.
UNSETTABLE = {
    "refresh_ms", "weather.latitude", "weather.longitude", "weather.place",
    "hotkeys.pause", "hotkeys.brightness_up", "hotkeys.brightness_down", "hotkeys.open",
    "hotkeys.next_scene", "hotkeys.timer", "hotkeys.timer_reset", "hotkeys.mic_mute",
    "device.at", "across",
}  # fmt: skip

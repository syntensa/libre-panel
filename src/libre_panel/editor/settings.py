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
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from libre_panel import __version__, i18n
from libre_panel.config import (
    MOTIONS,
    SYMBOL_PACKS,
    Config,
    ConfigError,
    config_dir,
    default_providers,
    edit_config,
    load_config,
    log_file,
    user_themes_dir,
)
from libre_panel.editor import access
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/syntensa/libre-panel"
REPORT_FORM = f"{PROJECT_URL}/issues/new?template=panel_support.yml"
LICENSE = {"id": "GPL-3.0-or-later", "url": "https://www.gnu.org/licenses/gpl-3.0.html"}
MAX_NOTICE = 200_000
# The built-in services in the order the app lists them (game mode and its round report first,
# the section is named after it); plugins' come after them.
SERVICE_ORDER = ("game", "memory", "notices", "autopilot", "devices", "cooling", "downloads")
# Sources that start by themselves; they are listed only where config.toml names them.
IMPLICIT_SOURCES = ("media", "sky", "hardware")  # hardware: the names of this PC
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


def _choice(name: str, value: Any, allowed: tuple[Any, ...]) -> Any:
    if isinstance(value, bool) or value not in allowed or type(value) is not type(allowed[0]):
        raise SettingsError(f"{name}: must be one of {', '.join(str(a) for a in allowed)}")
    return value


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


def _model_info(model: Any, port: str | None = None, sure: bool = True) -> dict[str, Any]:
    """A panel model as the app shows it: its name, its size the way round it is mounted,
    how it is connected."""
    return {
        "id": model.id,
        "label": model.label,
        "size": list(model.size("landscape")),
        "shape": model.shape,
        "connection": "usb" if model.protocol == "usb-turing" else "serial",
        "port": port,
        "sure": sure,
        "video": model.video,
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
                    "output": device.output,
                    "port": device.port,
                },
                "video": {
                    "mode": video.mode,
                    "fps": video.fps,
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
                "hotkeys": dict(config.hotkeys),
                "hotkeys_enabled": config.hotkeys_enabled,
                "symbols": config.symbols,
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
        raise SettingsError(
            t(
                "While Libre Panel runs with administrator rights, {name} is not changed here: it "
                "names a program or a file that would then be started or written with those "
                "rights. Set it in config.toml in your settings folder; Libre Panel reads it "
                "from there.",
                name=name,
            ),
            403,
        )

    def _check(self, name: str, value: Any, config: Config) -> tuple[str, Any]:
        """The change ``("value" or "unset", cleaned value)`` for a setting, or the error."""
        if name.startswith("services."):
            return self._check_option(name, value, config)
        self._check_rights(name, value, config)
        if value is None:
            if name not in UNSETTABLE:
                raise SettingsError(f"{name}: cannot be taken out")
            return "unset", None
        rule = RULES.get(name)
        if rule is None:
            raise SettingsError(f"unknown setting {name!r}")
        return "value", rule(name, value, config)

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
        panel = getattr(self.controls, "panel", None)
        if panel is not None:
            state = panel.state()
            running = {"state": state["state"], "detail": state["detail"]}
            shown = state.get("theme") or None
        return {
            "configured": config.device.model,
            "found": found["info"] if found else None,
            "model": _model_info(model) if model is not None else None,
            "running": running,
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
                return present_model()
            except Exception:  # no USB library is no panel
                log.debug("looking for a USB panel failed", exc_info=True)
                return None

        ask = turzx.clip_question(self._config().device, on_the_bus)
        return {"ask": ask, "name": turzx.VENDOR_CLIP}

    @staticmethod
    def _find_panel(config: Config) -> dict[str, Any] | None:
        """A panel on the bus: a TURZX USB one, else a serial one by its USB ids."""
        from libre_panel.devices.serial_link import find_port
        from libre_panel.devices.turzx_usb import present_model

        try:
            model = present_model()
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


RULES: dict[str, Any] = {
    "device.model": _rule_model,
    "device.driver": _rule_driver,
    "device.brightness": lambda n, v, c: _whole(n, v, 0, 100),
    "device.rotate": lambda n, v, c: _choice(n, v, (0, 180)),
    "device.orientation": lambda n, v, c: _choice(n, v, ("", "landscape", "portrait")),
    "device.motion": lambda n, v, c: _choice(n, v, MOTIONS),
    "device.output": lambda n, v, c: _text(n, v, 260, required=True),
    "device.port": lambda n, v, c: _text(n, v, 64),
    "video.mode": lambda n, v, c: _choice(n, v, ("auto", "on", "off")),
    "video.fps": lambda n, v, c: _whole(n, v, 1, 60),
    "video.local_clip": lambda n, v, c: _text(n, v, 200),
    "video.clip_asked": lambda n, v, c: _flag(n, v),
    "video.ffmpeg": lambda n, v, c: _text(n, v, 260),
    "refresh_ms": lambda n, v, c: _whole(n, v, 100, 3_600_000),
    "check_updates": lambda n, v, c: _flag(n, v),
    "allow_notify": lambda n, v, c: _flag(n, v),
    "symbols": lambda n, v, c: _choice(n, v, SYMBOL_PACKS),
    "hotkeys.enabled": lambda n, v, c: _flag(n, v),
    "hotkeys.pause": _rule_hotkey,
    "hotkeys.brightness_up": _rule_hotkey,
    "hotkeys.brightness_down": _rule_hotkey,
    "hotkeys.open": _rule_hotkey,
    "sensors.providers": _rule_providers,
    "sensors.librehardwaremonitor.mode": lambda n, v, c: _choice(
        n, v, ("auto", "service", "inprocess", "webserver")
    ),
    "sensors.librehardwaremonitor.url": _rule_url,
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
}  # fmt: skip

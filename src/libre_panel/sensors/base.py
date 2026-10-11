"""Sensor data model and the hub that merges all sources."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

HISTORY_LENGTH = 600


def as_float(value: int | float) -> float:
    """``value`` as a float; an integer too large for one (a payload of 400 digits) is infinity
    with its sign, as the text "1e400" reads. No widget draws that (the renderer takes it for
    no reading), where the conversion would raise in the thread that reads the source."""
    try:
        return float(value)
    except OverflowError:
        return -math.inf if value < 0 else math.inf


# Sensor keys the themes on show read, by whoever shows them (a renderer).
# Providers ask ``wanted("proc.")`` before costly work nobody looks at.
_WANTED: dict[int, frozenset[str]] = {}


def want(owner: object, keys: set[str] | frozenset[str]) -> None:
    """Record the keys ``owner`` reads (globs allowed); forgotten with ``owner``."""
    import weakref

    ident = id(owner)
    if ident not in _WANTED:
        weakref.finalize(owner, _WANTED.pop, ident, None)
    _WANTED[ident] = frozenset(keys)


def wanted(prefix: str) -> bool:
    """Whether a theme on show reads a key starting with ``prefix`` (or a
    pattern such as ``net.*`` that takes it in)."""
    for keys in list(_WANTED.values()):
        for key in keys:
            if key.startswith(prefix):
                return True
            head = re.split(r"[*?\[]", key, maxsplit=1)[0]
            if head != key and prefix.startswith(head):
                return True
    return False


def wanted_keys() -> frozenset[str]:
    """Every key (or pattern) that themes on show read; compare two to see whether
    that changed, instead of asking ``wanted`` for each key again."""
    return frozenset().union(*list(_WANTED.values()))


@dataclass(frozen=True)
class Reading:
    """One sensor value. ``key`` uses dotted names such as ``cpu.load``."""

    key: str
    value: float | str | None
    unit: str = ""
    label: str = ""
    # The key of the reading this one repeats under a friendlier name
    # (cpu.temp is one of the temp.* readings), so lists show it once.
    origin: str = ""
    # A state (``publish_state``): when it was published, on the main loop's clock.
    stamp: float | None = None


@dataclass
class Snapshot:
    readings: dict[str, Reading] = field(default_factory=dict)
    history: dict[str, list[float]] = field(default_factory=dict)
    now: datetime = field(default_factory=datetime.now)
    images: dict[str, Any] = field(default_factory=dict)  # published by services

    def value(self, key: str) -> Any:
        reading = self.readings.get(key)
        return reading.value if reading else None

    def state(self, key: str, default: Any = None) -> Any:
        """The dict or list a service published with ``publish_state``, parsed anew
        on every call; ``default`` when there is none, it is ``null`` or the
        text is no JSON."""
        text = self.value(key)
        if not isinstance(text, str):
            return default
        try:
            found = json.loads(text)
        except ValueError:
            return default
        return found if isinstance(found, dict | list) else default

    def state_stamp(self, key: str) -> float | None:
        """When the state was published, on the main loop's clock (the ``now`` a
        screen is drawn with): ``now - snapshot.state_stamp(key)`` is its age."""
        reading = self.readings.get(key)
        return reading.stamp if reading else None


class SensorProvider:
    """Base class for sensor sources.

    Plugins register subclasses under the ``libre_panel.sensors`` entry point
    group. ``read`` must be fast; slow sources should poll in the background.
    """

    name = "base"
    # True when ``read`` is cheap and its values must follow at once (a volume):
    # it is then read on every frame too, not only every ``refresh_ms``. Graphs
    # still keep one value per ``refresh_ms``. "video": on every frame only in
    # video mode; without it each changed value would be one more picture sent.
    every_frame: bool | str = False
    # Keys this source gives even when a source earlier in the list has them too (the hub
    # takes the first otherwise): for what the user said outright, or what is surely right.
    overrides: tuple[str, ...] = ()

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    def read(self) -> dict[str, Reading]:
        raise NotImplementedError

    def follow_config(self, config: Any) -> None:
        """config.toml was loaded (the :class:`~libre_panel.config.Config`), when the source
        was made and each time it changes while the panel runs: take the settings that are
        not in the source's own ``[sensors.<name>]`` table."""

    def images(self) -> dict[str, Any]:
        """Pictures this source has (an album cover), by name; image widgets
        show one with ``"src": "@media.cover"``."""
        return {}

    def close(self) -> None:
        pass


# Built in; plugins bring more (``libre_panel.sensors``, see docs/PLUGINS.md).
_BUILTIN_PROVIDERS = {
    "psutil": "libre_panel.sensors.psutil_provider:PsutilProvider",
    "librehardwaremonitor": "libre_panel.sensors.librehardwaremonitor:LibreHardwareMonitorProvider",
    "load": "libre_panel.sensors.load:LoadProvider",
    "audio": "libre_panel.sensors.audio:AudioProvider",
    "demo": "libre_panel.sensors.demo:DemoProvider",
    "media": "libre_panel.sensors.media:MediaProvider",
    "calendar": "libre_panel.sensors.calendar:CalendarProvider",
    "homeassistant": "libre_panel.sensors.homeassistant:HomeAssistantProvider",
    "mqtt": "libre_panel.sensors.mqtt:MqttProvider",
    "sky": "libre_panel.sensors.sky:SkyProvider",
    "presentmon": "libre_panel.sensors.presentmon:PresentMonProvider",
    "hardware": "libre_panel.sensors.hardware:HardwareProvider",
}


def _load_object(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)


def available_providers() -> dict[str, str]:
    """Sensor sources by name: where each comes from."""
    from libre_panel.plugins.loader import registry

    found = dict.fromkeys(_BUILTIN_PROVIDERS, "built in")
    for name in registry().names("sensors"):
        found.setdefault(name, registry().parts["sensors"][name].source)
    return found


def create_provider(name: str, options: dict[str, Any] | None = None) -> SensorProvider:
    from libre_panel.plugins.loader import registry

    if name in _BUILTIN_PROVIDERS:
        return _load_object(_BUILTIN_PROVIDERS[name])(options)
    found = registry().parts.get("sensors", {}).get(name)
    if found is None:
        raise ValueError(
            f"unknown sensor provider {name!r} "
            f"(available: {', '.join(sorted(available_providers()))})"
        )
    cls = registry().get("sensors", name)
    if cls is None:
        raise ValueError(f"sensor provider {name!r} did not load: {found.error}")
    return cls(options)


class SensorHub:
    """Collects readings from all providers and keeps a short history for graphs.

    Providers earlier in the list win when two supply the same key, unless a later one
    lists the key in its ``overrides``.
    """

    def __init__(self, providers: list[SensorProvider]) -> None:
        self.providers = providers
        self._history: dict[str, deque[float]] = {}
        self._owners: dict[str, SensorProvider] = {}  # which provider a key came from

    @property
    def every_frame(self) -> bool:
        """Whether any provider wants to be read on every frame."""
        return any(getattr(p, "every_frame", False) for p in self.providers)

    def _read(self, provider: SensorProvider) -> dict[str, Reading]:
        try:
            return provider.read()
        except Exception:  # a broken sensor must not stop the display
            log.exception("sensor provider %s failed", provider.name)
            return {}

    def snapshot(self) -> Snapshot:
        readings: dict[str, Reading] = {}
        owners: dict[str, SensorProvider] = {}
        for provider in self.providers:
            overrides = getattr(provider, "overrides", ())
            for key, reading in self._read(provider).items():
                if key not in readings or key in overrides:
                    readings[key] = reading
                    owners[key] = provider
        self._owners = owners
        for key, reading in readings.items():
            if isinstance(reading.value, (int, float)) and not isinstance(reading.value, bool):
                self._history.setdefault(key, deque(maxlen=HISTORY_LENGTH)).append(
                    float(reading.value)
                )
        history = {k: list(v) for k, v in self._history.items()}
        images: dict[str, Any] = {}
        for provider in self.providers:
            try:
                found = provider.images() if hasattr(provider, "images") else {}
            except Exception:  # a broken sensor must not stop the display
                log.exception("sensor provider %s failed", provider.name)
                found = {}
            for key, image in (found or {}).items():
                images.setdefault(key, image)
        return Snapshot(readings=readings, history=history, images=images)

    def follow_config(self, config: Any) -> None:
        """Tell the sources about the config.toml that was loaded (see
        :meth:`SensorProvider.follow_config`)."""
        for provider in self.providers:
            follow = getattr(provider, "follow_config", None)
            if follow is None:  # a source written without the base class
                continue
            try:
                follow(config)
            except Exception:  # a broken sensor must not stop the display
                log.exception("sensor provider %s failed", provider.name)

    def readings_from(self, provider: SensorProvider) -> int:
        """How many readings of the last snapshot came from ``provider``."""
        return sum(1 for owner in self._owners.values() if owner is provider)

    def fresh(self, video: bool = True) -> dict[str, Reading]:
        """Between snapshots: the latest values of ``every_frame`` providers, for
        the keys they supplied (or that nobody supplied); no history. Without
        ``video`` those that follow on every frame only in video mode are left out."""
        readings: dict[str, Reading] = {}
        for provider in self.providers:
            every = getattr(provider, "every_frame", False)
            if not every or (every == "video" and not video):
                continue
            for key, reading in self._read(provider).items():
                if self._owners.get(key, provider) is provider and key not in readings:
                    readings[key] = reading
        return readings

    def close(self) -> None:
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                log.exception("closing sensor provider %s failed", provider.name)

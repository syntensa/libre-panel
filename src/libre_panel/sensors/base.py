"""Sensor data model and the hub that merges all sources."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
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


# The seconds of the minute with their fraction (0 to 60), at the time of the frame, not
# of a reading: a bar on it (0 to 60) runs once a minute, smoothly (Studio's seconds run)
TIME_SECOND = "time.second"


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
        if reading is not None:
            return reading.value
        if key == TIME_SECOND:  # the clock's own, at the time of the frame
            return self.now.second + self.now.microsecond / 1e6
        return None

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
    "linux": "libre_panel.sensors.linux:LinuxProvider",
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
    "mangohud": "libre_panel.sensors.mangohud:MangoHudProvider",
    "rtss": "libre_panel.sensors.rtss:RTSSProvider",
    "json": "libre_panel.sensors.json_source:JsonProvider",
    "nut": "libre_panel.sensors.nut:NutProvider",
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

    def __init__(self, providers: list[SensorProvider], names: Any = None) -> None:
        self.providers = providers
        # puts the names readings go by into them (the fans': sensors.fan_names.FanNames)
        self.names = names
        self._history: dict[str, deque[float]] = {}
        self._owners: dict[str, SensorProvider] = {}  # which provider a key came from
        self._fresh_images: set[str] = set()  # the pictures fresh_images last handed back
        # more panels at once read the sources through it (HubView), each loop in its thread:
        # a snapshot at a time, and the readings it took last, with when (on time.monotonic);
        # each source read by one thread at a time, so that a slow one (the sensors of the
        # hardware, the processes) holds up no other: the readings of every frame go on
        self._lock = threading.Lock()
        self._taken: tuple[float, dict[str, Reading], dict[str, Any]] | None = None
        self._sources: dict[int, threading.Lock] = {}

    def _named(self, readings: dict[str, Reading]) -> dict[str, Reading]:
        if self.names is None:
            return readings
        try:
            return self.names.apply(readings)
        except Exception:  # a name is not worth a reading
            log.exception("the names of the readings could not be given")
            return readings

    @property
    def every_frame(self) -> bool:
        """Whether any provider wants to be read on every frame."""
        return any(getattr(p, "every_frame", False) for p in self.providers)

    def _source(self, provider: SensorProvider) -> threading.Lock:
        return self._sources.setdefault(id(provider), threading.Lock())

    def _read(self, provider: SensorProvider) -> dict[str, Reading]:
        try:
            with self._source(provider):
                return provider.read()
        except Exception:  # a broken sensor must not stop the display
            log.exception("sensor provider %s failed", provider.name)
            return {}

    def _images(self, provider: SensorProvider) -> dict[str, Any] | None:
        if not hasattr(provider, "images"):
            return {}
        with self._source(provider):
            return provider.images()

    def _take(self) -> tuple[dict[str, Reading], dict[str, Any]]:
        """Every source read once: the readings by their names, and the pictures."""
        readings: dict[str, Reading] = {}
        owners: dict[str, SensorProvider] = {}
        for provider in self.providers:
            overrides = getattr(provider, "overrides", ())
            for key, reading in self._read(provider).items():
                if key not in readings or key in overrides:
                    readings[key] = reading
                    owners[key] = provider
        self._owners = owners
        readings = self._named(readings)
        images: dict[str, Any] = {}
        for provider in self.providers:
            try:
                found = self._images(provider)
            except Exception:  # a broken sensor must not stop the display
                log.exception("sensor provider %s failed", provider.name)
                found = {}
            for key, image in (found or {}).items():
                images.setdefault(key, image)
        self._taken = (time.monotonic(), readings, images)
        return readings, images

    def snapshot(self) -> Snapshot:
        with self._lock:
            readings, images = self._take()
            return Snapshot(readings=readings, history=remember(self._history, readings),
                            images=images)  # fmt: skip

    def age(self) -> float | None:
        """How long ago the readings were last taken, in seconds (None: not yet)."""
        taken = self._taken
        return None if taken is None else max(0.0, time.monotonic() - taken[0])

    def shared(self, max_age: float) -> tuple[dict[str, Reading], dict[str, Any]]:
        """For another panel (:class:`HubView`): the readings and pictures taken last, when
        they are younger than ``max_age`` seconds, else new ones; the sources are not read
        twice for two panels that draw at the same pace. (Younger: Windows' clock moves in
        steps of about 16 ms before Python 3.13, so readings of a moment ago may be 0 s old.)"""
        with self._lock:
            taken = self._taken
            if taken is not None and time.monotonic() - taken[0] < max_age:
                return dict(taken[1]), dict(taken[2])
            readings, images = self._take()
            return dict(readings), dict(images)

    def follow_config(self, config: Any) -> None:
        """Tell the sources about the config.toml that was loaded (see
        :meth:`SensorProvider.follow_config`), and the names."""
        if self.names is not None:
            try:
                self.names.follow_config(config)
            except Exception:  # a name is not worth a reading
                log.exception("the names of the readings could not follow the config")
        for provider in self.providers:
            follow = getattr(provider, "follow_config", None)
            if follow is None:  # a source written without the base class
                continue
            try:
                with self._source(provider):
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
        owners = self._owners  # (the last snapshot's; a new one replaces it whole)
        for provider in self.providers:
            every = getattr(provider, "every_frame", False)
            if not every or (every == "video" and not video):
                continue
            for key, reading in self._read(provider).items():
                if owners.get(key, provider) is provider and key not in readings:
                    readings[key] = reading
        return self._named(readings)

    def frame_images(self, video: bool = True) -> dict[str, Any]:
        """The pictures ``every_frame`` providers hold now (see :meth:`fresh_images`)."""
        images: dict[str, Any] = {}
        for provider in self.providers:
            every = getattr(provider, "every_frame", False)
            if not every or (every == "video" and not video):
                continue
            try:
                found = self._images(provider) or {}
            except Exception:  # a broken sensor must not stop the display
                log.exception("sensor provider %s failed", provider.name)
                continue
            for key, image in found.items():
                images.setdefault(key, image)
        return images

    def fresh_images(self, video: bool = True) -> dict[str, Any]:
        """Between snapshots: the pictures of ``every_frame`` providers (a new cover with its
        new title, not a second later), and None for one such a provider had and has no more.
        Cheap: a provider hands back the pictures it holds."""
        images = self.frame_images(video)
        gone = self._fresh_images - set(images)
        self._fresh_images = set(images)
        return {**images, **dict.fromkeys(gone)}

    def close(self) -> None:
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                log.exception("closing sensor provider %s failed", provider.name)


def remember(history: dict[str, deque[float]], readings: dict[str, Reading]) -> dict[str, list]:
    """The numbers among ``readings`` added to ``history``, and the history as a snapshot
    carries it."""
    for key, reading in readings.items():
        if isinstance(reading.value, (int, float)) and not isinstance(reading.value, bool):
            history.setdefault(key, deque(maxlen=HISTORY_LENGTH)).append(float(reading.value))
    return {k: list(v) for k, v in history.items()}


class HubView:
    """Another panel's way to the readings (two panels at once, ``[[panels]]``): the first
    panel's :class:`SensorHub` reads the sources, and this panel takes what it read last when
    it is new enough (:attr:`SHARE_S`), else has them read anew. Its history is its own, at the
    pace its theme takes readings, so its graphs run as they would with a hub of their own.
    The sources follow the first panel's config and close with its loop."""

    SHARE_S = 1.0  # readings this old are new enough: a panel drawing at the same pace reads none

    def __init__(self, hub: SensorHub) -> None:
        self.hub = hub
        self._history: dict[str, deque[float]] = {}
        self._fresh_images: set[str] = set()

    @property
    def providers(self) -> list[SensorProvider]:
        return self.hub.providers

    @property
    def every_frame(self) -> bool:
        return self.hub.every_frame

    def readings_from(self, provider: SensorProvider) -> int:
        return self.hub.readings_from(provider)

    def snapshot(self) -> Snapshot:
        readings, images = self.hub.shared(self.SHARE_S)
        # what follows on every frame (the services' readings: an alarm) as it is now, not as
        # it was when the shared ones were taken
        readings.update(self.hub.fresh())
        return Snapshot(readings=readings, history=remember(self._history, readings),
                        images=images)  # fmt: skip

    def age(self) -> float | None:
        """How long ago the first panel's loop took the readings this panel shares: its loop
        takes them a moment after, so a value glides on both at once (MainLoop.step)."""
        return self.hub.age()

    def fresh(self, video: bool = True) -> dict[str, Reading]:
        return self.hub.fresh(video)

    def fresh_images(self, video: bool = True) -> dict[str, Any]:
        images = self.hub.frame_images(video)
        gone = self._fresh_images - set(images)
        self._fresh_images = set(images)
        return {**images, **dict.fromkeys(gone)}

    def follow_config(self, config: Any) -> None:
        """Nothing: the first panel's loop tells the sources (its config has the same
        readings)."""

    def close(self) -> None:
        """Nothing: the sources close with the first panel's loop."""

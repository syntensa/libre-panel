"""Sensor data model and the hub that merges all sources."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

HISTORY_LENGTH = 600


@dataclass(frozen=True)
class Reading:
    """One sensor value. ``key`` uses dotted names such as ``cpu.load``."""

    key: str
    value: float | str | None
    unit: str = ""
    label: str = ""


@dataclass
class Snapshot:
    readings: dict[str, Reading] = field(default_factory=dict)
    history: dict[str, list[float]] = field(default_factory=dict)
    now: datetime = field(default_factory=datetime.now)
    images: dict[str, Any] = field(default_factory=dict)  # published by services

    def value(self, key: str) -> Any:
        reading = self.readings.get(key)
        return reading.value if reading else None


class SensorProvider:
    """Base class for sensor sources.

    Plugins register subclasses under the ``libre_panel.sensors`` entry point
    group. ``read`` must be fast; slow sources should poll in the background.
    """

    name = "base"
    # True when ``read`` is cheap and its values must follow at once (a volume):
    # it is then read on every frame too, not only every ``refresh_ms``. Graphs
    # still keep one value per ``refresh_ms``.
    every_frame = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    def read(self) -> dict[str, Reading]:
        raise NotImplementedError

    def close(self) -> None:
        pass


# Built in; plugins bring more (``libre_panel.sensors``, see docs/PLUGINS.md).
_BUILTIN_PROVIDERS = {
    "psutil": "libre_panel.sensors.psutil_provider:PsutilProvider",
    "librehardwaremonitor": "libre_panel.sensors.librehardwaremonitor:LibreHardwareMonitorProvider",
    "demo": "libre_panel.sensors.demo:DemoProvider",
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

    Providers earlier in the list win when two supply the same key.
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
            for key, reading in self._read(provider).items():
                if key not in readings:
                    readings[key] = reading
                    owners[key] = provider
        self._owners = owners
        for key, reading in readings.items():
            if isinstance(reading.value, (int, float)) and not isinstance(reading.value, bool):
                self._history.setdefault(key, deque(maxlen=HISTORY_LENGTH)).append(
                    float(reading.value)
                )
        history = {k: list(v) for k, v in self._history.items()}
        return Snapshot(readings=readings, history=history)

    def fresh(self) -> dict[str, Reading]:
        """Between snapshots: the latest values of ``every_frame`` providers, for
        the keys they supplied (or that nobody supplied); no history."""
        readings: dict[str, Reading] = {}
        for provider in self.providers:
            if not getattr(provider, "every_frame", False):
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

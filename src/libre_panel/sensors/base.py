"""Sensor data model and the hub that merges all sources."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import entry_points
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

    def value(self, key: str) -> Any:
        reading = self.readings.get(key)
        return reading.value if reading else None


class SensorProvider:
    """Base class for sensor sources.

    Plugins register subclasses under the ``libre_panel.sensors`` entry point
    group. ``read`` must be fast; slow sources should poll in the background.
    """

    name = "base"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    def read(self) -> dict[str, Reading]:
        raise NotImplementedError

    def close(self) -> None:
        pass


# Used when the package is run from a source checkout without installed
# entry points; installed plugins are found through entry points.
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
    found = dict(_BUILTIN_PROVIDERS)
    for ep in entry_points(group="libre_panel.sensors"):
        found[ep.name] = ep.value
    return found


def create_provider(name: str, options: dict[str, Any] | None = None) -> SensorProvider:
    providers = available_providers()
    if name not in providers:
        raise ValueError(
            f"unknown sensor provider {name!r} (available: {', '.join(sorted(providers))})"
        )
    cls = _load_object(providers[name])
    return cls(options)


class SensorHub:
    """Collects readings from all providers and keeps a short history for graphs.

    Providers earlier in the list win when two supply the same key.
    """

    def __init__(self, providers: list[SensorProvider]) -> None:
        self.providers = providers
        self._history: dict[str, deque[float]] = {}

    def snapshot(self) -> Snapshot:
        readings: dict[str, Reading] = {}
        for provider in self.providers:
            try:
                values = provider.read()
            except Exception:  # a broken sensor must not stop the display
                log.exception("sensor provider %s failed", provider.name)
                continue
            for key, reading in values.items():
                readings.setdefault(key, reading)
        for key, reading in readings.items():
            if isinstance(reading.value, (int, float)) and not isinstance(reading.value, bool):
                self._history.setdefault(key, deque(maxlen=HISTORY_LENGTH)).append(
                    float(reading.value)
                )
        history = {k: list(v) for k, v in self._history.items()}
        return Snapshot(readings=readings, history=history)

    def close(self) -> None:
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                log.exception("closing sensor provider %s failed", provider.name)

"""The names fans go by on the panel: the user's own (``[[hardware.fans]]``), else the one
FanControl gives the fan, else the sensor's own ("Fan #1").

A mainboard wires its fan headers its own way, so a theme cannot know which header is the
intake and which the radiator: it shows a fan's name (``{label}``), and the name comes from
here. FanControl's configuration is only read (and only now and then, when it changed); the
names stay on this computer.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from libre_panel.sensors.base import Reading

log = logging.getLogger(__name__)

LHM_PREFIX = "lhm:"
CHECK_S = 30.0  # how often FanControl's file is looked at (its modification time)


def is_fan(key: str, reading: Reading) -> bool:
    """A fan's speed under a key a theme uses (``fan.lpc.fan_1``, ``gpu.fan``), not a raw
    ``lhm:`` key: those keep the sensor's own name."""
    return reading.unit.upper() == "RPM" and not key.startswith(LHM_PREFIX)


def _sensor_id(reading: Reading) -> str:
    """The LibreHardwareMonitor id of the sensor a friendly reading repeats, or ""."""
    origin = reading.origin
    return origin[len(LHM_PREFIX) :] if origin.startswith(LHM_PREFIX) else ""


class FanControlNames:
    """FanControl's names for the fans, by the id of the sensor that measures each: read
    again when its file changed, looked at every ``CHECK_S`` at most."""

    def __init__(
        self,
        given: str = "",
        clock: Callable[[], float] = time.monotonic,
        find: Callable[[str], Path | None] | None = None,
    ) -> None:
        self.given = given
        self._clock = clock
        self._find = find
        self._checked: float | None = None
        self._stamp: tuple[str, int, int] | None = None
        self._names: dict[str, str] = {}

    def names(self) -> dict[str, str]:
        now = self._clock()
        if self._checked is not None and now - self._checked < CHECK_S:
            return self._names
        self._checked = now
        from libre_panel.services import fancontrol

        try:
            path = (self._find or fancontrol.find)(self.given)
            if path is None:
                self._stamp, self._names = None, {}
                return self._names
            info = path.stat()
            stamp = (str(path), info.st_mtime_ns, info.st_size)
            if stamp != self._stamp:
                section = fancontrol.read_section(path)
                if section is not None:  # FanControl may be writing it just now: next time
                    self._names = fancontrol.names_and_hidden(section)[0]
                    self._stamp = stamp
        except OSError as exc:
            log.debug("FanControl's names could not be read: %s", exc)
        return self._names


class FanNames:
    """Puts the fans' names into the readings (see the module)."""

    def __init__(self, fancontrol: FanControlNames | None = None) -> None:
        self.custom: dict[str, str] = {}
        self.fancontrol = fancontrol or FanControlNames()

    def follow_config(self, config: Any) -> None:
        self.custom = dict(config.hardware.fans)
        # where FanControl's file is, if the cooling analysis or the memory were told
        given = ""
        for service in ("cooling", "memory"):
            options = config.services.options.get(service, {})
            path = options.get("fancontrol", "") if isinstance(options, dict) else ""
            if isinstance(path, str) and path:
                given = path
                break
        self.fancontrol.given = given

    def detected(self, reading: Reading, readings: Mapping[str, Reading]) -> str:
        """The name a fan goes by without one of the user's own: FanControl's, else the
        sensor's (the raw reading it repeats keeps that)."""
        sensor = _sensor_id(reading)
        given = self.fancontrol.names().get(sensor, "") if sensor else ""
        if given:
            return given
        raw = readings.get(reading.origin) if reading.origin else None
        return raw.label if raw is not None else reading.label

    def apply(self, readings: dict[str, Reading]) -> dict[str, Reading]:
        """``readings`` with each fan under the name it goes by (changed in place)."""
        known = self.fancontrol.names() if any(_sensor_id(r) for r in readings.values()) else {}
        for key, reading in list(readings.items()):
            if not is_fan(key, reading):
                continue
            sensor = _sensor_id(reading)
            name = self.custom.get(key) or (known.get(sensor, "") if sensor else "")
            if name and name != reading.label:
                readings[key] = dataclasses.replace(reading, label=name)
        return readings

"""FanControl's configuration, read only: its curves, the fans they drive and the
temperature each curve reads.

FanControl (Windows) keeps all of it in one JSON file. The cooling analysis compares what
the fans really did with what the curves say they should do, so it has to know the curves;
the memory notes when they change, so that the analysis looks at the data since the last
change. Libre Panel only ever opens the file for reading: how the fans run is the user's
decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_NAME = "userConfig.json"
CHECK_S = 30.0  # how often the file's modification time is looked at
# What of a fan output decides how it cools. Its window position, whether it is hidden and the
# like do not: or every move of the window would look like a new setting.
CONTROL_FIELDS = (
    "NickName", "Identifier", "Enable", "SelectedFanCurve", "SelectedOffset", "SelectedStart",
    "SelectedStop", "MinimumPercent", "SelectedCommandStepUp", "SelectedCommandStepDown",
)  # fmt: skip
_CONTROL_ID = re.compile(r"/control/(\d+)$")


def default_paths(env: Mapping[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """Where FanControl keeps its configuration unless told otherwise: next to the program
    (installed for everyone, or in the user's folders) or in Documents."""
    env = os.environ if env is None else env
    folders = [
        Path(env[name]) / "FanControl"
        for name in ("ProgramFiles(x86)", "ProgramFiles")
        if env.get(name)
    ]
    if env.get("LOCALAPPDATA"):
        folders.append(Path(env["LOCALAPPDATA"]) / "FanControl")
    folders.append((home or Path.home()) / "Documents" / "FanControl")
    return [folder / "Configurations" / CONFIG_NAME for folder in folders]


def find(given: str = "", env: Mapping[str, str] | None = None) -> Path | None:
    """The configuration file: ``given`` (the user's setting), else the first usual place
    that has one. None where FanControl is not installed."""
    if given:
        path = Path(given).expanduser()
        return path if path.is_file() else None
    return next((path for path in default_paths(env) if path.is_file()), None)


def read_section(path: Path) -> dict[str, Any] | None:
    """The ``FanControl`` part of the file, or None when it cannot be read: it is gone,
    FanControl is writing it just now, or it is not what we expect."""
    try:
        with open(path, encoding="utf-8-sig") as file:
            section = json.load(file)["FanControl"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return section if isinstance(section, dict) else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def state_of(section: Mapping[str, Any]) -> dict[str, Any]:
    """What of the configuration decides the cooling: the curves and the fan outputs."""
    return {
        "curves": _dicts(section.get("FanCurves")),
        "controls": [
            {name: control.get(name) for name in CONTROL_FIELDS}
            for control in _dicts(section.get("Controls"))
        ],
    }


def fingerprint(state: Mapping[str, Any]) -> str:
    """A short mark of a state: the same cooling settings, the same mark."""
    text = json.dumps(state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


# -- curves and fans ------------------------------------------------------------------------


@dataclass(frozen=True)
class Curve:
    """One fan curve: duty (percent) over temperature, read from a sensor, or a mix of curves."""

    name: str
    points: tuple[tuple[float, float], ...] = ()  # (°C, duty %), by temperature
    source: str | None = None  # the sensor it reads (LibreHardwareMonitor's id), None for a mix
    mix: tuple[str, ...] = ()  # the curves a mix takes together
    hysteresis: Mapping[str, Any] = field(default_factory=dict)

    @property
    def device(self) -> str:
        """What the curve cools by: ``cpu``, ``gpu``, ``mix`` (other curves together) or
        ``other`` (some other sensor). LibreHardwareMonitor's ids start with the hardware."""
        if self.mix:
            return "mix"
        source = self.source or ""
        if source.startswith(("/amdcpu", "/intelcpu")):
            return "cpu"
        return "gpu" if source.startswith("/gpu") else "other"

    def duty_at(self, temp: float) -> float:
        """The duty the curve commands at this temperature (percent)."""
        points = self.points
        if not points:
            return 0.0
        if temp <= points[0][0]:
            return points[0][1]
        for (t0, d0), (t1, d1) in zip(points, points[1:], strict=False):
            if temp <= t1:
                return d0 + (d1 - d0) * (temp - t0) / (t1 - t0) if t1 > t0 else d1
        return points[-1][1]

    def temp_at(self, duty: float) -> float | None:
        """The temperature a curve must have seen to command this duty: the other way round
        from :meth:`duty_at`. Only the rising part tells; on the flat start or end there is
        no single temperature, and the answer is None."""
        points = self.points
        if len(points) < 2 or duty <= points[0][1] + 1e-9 or duty >= points[-1][1] - 1e-9:
            return None
        for (t0, d0), (t1, d1) in zip(points, points[1:], strict=False):
            if d0 <= duty <= d1 and d1 > d0:
                return t0 + (t1 - t0) * (duty - d0) / (d1 - d0)
        return None


@dataclass(frozen=True)
class Fan:
    """A fan output of FanControl and the curve that drives it."""

    name: str  # the name the user gave it
    curve: str
    minimum: float | None = None  # the lowest duty FanControl gives it
    sensor: str | None = None  # the id of the sensor that measures its speed


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _points(raw: Any) -> tuple[tuple[float, float], ...]:
    found = []
    for item in raw if isinstance(raw, list) else ():
        temp, _, duty = str(item).partition(",")
        try:
            found.append((float(temp), float(duty)))
        except ValueError:
            continue  # a point that is no pair is left out, the others still tell
    return tuple(sorted(found))


def fan_sensor(control_id: str | None) -> str | None:
    """The id of the sensor that measures the speed of the fan an output drives: LibreHardware-
    Monitor names both alike, ``/lpc/chip/control/1`` and ``/lpc/chip/fan/1``."""
    found = _CONTROL_ID.search(control_id or "")
    return f"{control_id[: found.start()]}/fan/{found.group(1)}" if found and control_id else None


def curves_and_fans(state: Mapping[str, Any]) -> tuple[dict[str, Curve], dict[str, Fan]]:
    """The curves by name and the fans (enabled, with a curve) by the name they were given."""
    curves: dict[str, Curve] = {}
    for raw in _dicts(state.get("curves")):
        name = raw.get("Name")
        if not isinstance(name, str) or not name:
            continue
        source = raw.get("SelectedTempSource")
        identifier = source.get("Identifier") if isinstance(source, dict) else None
        parts = tuple(
            str(part["Name"]) for part in _dicts(raw.get("SelectedFanCurves")) if part.get("Name")
        )
        hysteresis = raw.get("HysteresisConfig")
        curves[name] = Curve(
            name,
            _points(raw.get("Points")),
            identifier if isinstance(identifier, str) and identifier else None,
            parts,
            hysteresis if isinstance(hysteresis, dict) else {},
        )
    fans: dict[str, Fan] = {}
    for raw in _dicts(state.get("controls")):
        selected = raw.get("SelectedFanCurve")
        curve = selected.get("Name") if isinstance(selected, dict) else None
        if not raw.get("Enable") or not curve:
            continue
        name = str(raw.get("NickName") or raw.get("Identifier") or "")
        if name:
            identifier = raw.get("Identifier")
            fans[name] = Fan(
                name, str(curve), _number(raw.get("MinimumPercent")),
                fan_sensor(identifier if isinstance(identifier, str) else None),
            )  # fmt: skip
    return curves, fans


def names_and_hidden(section: Mapping[str, Any]) -> tuple[dict[str, str], set[str]]:
    """The names the user gave the fans, by the id of the sensor that measures each, and
    the fans FanControl hides (usually connectors with nothing on them)."""
    names: dict[str, str] = {}
    hidden: set[str] = set()
    for control in _dicts(section.get("Controls")):
        identifier = control.get("Identifier")
        sensor = fan_sensor(identifier if isinstance(identifier, str) else None)
        if sensor is None:
            continue
        nick = str(control.get("NickName") or "").strip()
        if nick:
            names[sensor] = nick
        if control.get("IsHidden"):
            hidden.add(sensor)
    return names, hidden


# -- what changed ------------------------------------------------------------------------------


def changes(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What differs between two states, as records the report words (``kind``: ``curve-added``,
    ``curve-removed``, ``source``, ``points``, ``mix-function``, ``hysteresis``, ``fan``)."""
    found: list[dict[str, Any]] = []
    before = {c["Name"]: c for c in _dicts(old.get("curves")) if "Name" in c}
    after = {c["Name"]: c for c in _dicts(new.get("curves")) if "Name" in c}
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name), after.get(name)
        if a is None:
            found.append({"kind": "curve-added", "curve": name})
        elif b is None:
            found.append({"kind": "curve-removed", "curve": name})
        else:
            found.extend(_curve_changes(name, a, b))
    outputs = {c.get("NickName"): c for c in _dicts(old.get("controls"))}
    for control in _dicts(new.get("controls")):
        was = outputs.get(control.get("NickName"))
        if was is not None and was != control:
            fields = [key for key in control if control.get(key) != was.get(key)]
            found.append({"kind": "fan", "fan": control.get("NickName"), "fields": fields})
    return found


def _source(curve: Mapping[str, Any]) -> Any:
    source = curve.get("SelectedTempSource")
    return source.get("Identifier") if isinstance(source, dict) else None


def _curve_changes(name: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> list[dict[str, Any]]:
    found = []
    if _source(a) != _source(b):
        found.append({"kind": "source", "curve": name, "old": _source(a), "new": _source(b)})
    if a.get("Points") != b.get("Points"):
        found.append(
            {
                "kind": "points",
                "curve": name,
                "old": a.get("Points") or [],
                "new": b.get("Points") or [],
            }
        )
    if a.get("SelectedMixFunction") != b.get("SelectedMixFunction"):
        found.append(
            {
                "kind": "mix-function", "curve": name,
                "old": a.get("SelectedMixFunction"), "new": b.get("SelectedMixFunction"),
            }
        )  # fmt: skip
    if a.get("HysteresisConfig") != b.get("HysteresisConfig"):
        found.append({"kind": "hysteresis", "curve": name})
    return found


# -- looking for changes -------------------------------------------------------------------------


class Watch:
    """Tells when the configuration file changed. Its modification time is looked at every
    ``every_s``; the file is read only when that has moved (and again if it could not be read,
    since FanControl may be writing it just then)."""

    def __init__(
        self,
        given: str = "",
        every_s: float = CHECK_S,
        finder: Callable[[str], Path | None] = find,
        reader: Callable[[Path], dict[str, Any] | None] = read_section,
    ) -> None:
        self.given, self.every_s = given, every_s
        self._find, self._read = finder, reader
        self._due = float("-inf")
        self._seen: tuple[str, int, int] | None = None  # path, modification time, size
        self._warned = False
        self.path: Path | None = None

    def stamp(self) -> tuple[str, int, int] | None:
        """The file as it is now (path, modification time, size); None without one."""
        path = self._find(self.given)
        if path is None and self.given and not self._warned:
            self._warned = True
            log.warning("FanControl: %s is no file", self.given)
        self.path = path
        if path is None:
            return None
        try:
            info = path.stat()
        except OSError:
            return None
        return str(path), info.st_mtime_ns, info.st_size

    def poll(self, now: float) -> dict[str, Any] | None:
        """The ``FanControl`` section if the file is new or changed since the last time."""
        if now < self._due:
            return None
        self._due = now + self.every_s
        stamp = self.stamp()
        if stamp is None or stamp == self._seen or self.path is None:
            return None
        section = self._read(self.path)
        if section is None:
            return None  # not readable now: the next look tries again
        self._seen = stamp
        return section

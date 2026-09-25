"""Windows sensors via LibreHardwareMonitor's "Remote Web Server" (experimental).

LibreHardwareMonitor (MPL-2.0) runs as a separate program; enable
Options -> Remote Web Server and Libre Panel reads its ``data.json``.
Every sensor is exposed under its raw id (``lhm:/amdcpu/0/temperature/2``) and
the common ones also get friendly aliases such as ``cpu.temp`` or ``gpu.load``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from typing import Any

from libre_panel import __version__
from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(.*)$")

# (alias, hardware kind, sensor type, preferred sensor names in order)
_ALIASES = [
    ("cpu.temp", "cpu", "temperature", ("CPU Package", "Core (Tctl/Tdie)", "Tctl", "Tdie")),
    ("cpu.load", "cpu", "load", ("CPU Total",)),
    ("cpu.power", "cpu", "power", ("CPU Package", "Package")),
    ("gpu.temp", "gpu", "temperature", ("GPU Core", "GPU Hot Spot")),
    ("gpu.load", "gpu", "load", ("GPU Core", "D3D 3D")),
    ("gpu.power", "gpu", "power", ("GPU Package", "GPU Power", "GPU Core")),
    ("gpu.mem.load", "gpu", "load", ("GPU Memory",)),
    ("gpu.fan", "gpu", "fan", ("GPU Fan", "GPU Fan 1")),
]


def parse_value(text: Any) -> tuple[float | None, str]:
    """Parse strings like ``'45,0 °C'`` or ``'1234 RPM'``."""
    if not isinstance(text, str):
        return None, ""
    match = _NUMBER.match(text)
    if not match:
        return None, ""
    return float(match.group(1).replace(",", ".")), match.group(2).strip()


def _hardware_kind(sensor_id: str) -> str:
    head = sensor_id.strip("/").split("/", 1)[0].lower()
    if "cpu" in head:
        return "cpu"
    if head.startswith("gpu"):
        return "gpu"
    return head


def flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all leaf sensors that carry a SensorId."""
    leaves = []
    stack = [node]
    while stack:
        current = stack.pop()
        children = current.get("Children") or []
        if current.get("SensorId"):
            leaves.append(current)
        stack.extend(children)
    return leaves


def readings_from_tree(tree: dict[str, Any]) -> dict[str, Reading]:
    out: dict[str, Reading] = {}
    by_kind: dict[tuple[str, str], dict[str, Reading]] = {}
    for leaf in flatten(tree):
        sensor_id = str(leaf["SensorId"])
        value, unit = parse_value(leaf.get("Value"))
        if value is None:
            continue
        key = f"lhm:{sensor_id}"
        name = str(leaf.get("Text", sensor_id))
        reading = Reading(key, value, unit, name)
        out[key] = reading
        parts = sensor_id.strip("/").split("/")
        sensor_type = (leaf.get("Type") or (parts[2] if len(parts) > 2 else "")).lower()
        bucket = by_kind.setdefault((_hardware_kind(sensor_id), sensor_type), {})
        bucket.setdefault(name, reading)
    for alias, kind, sensor_type, names in _ALIASES:
        bucket = by_kind.get((kind, sensor_type), {})
        for name in names:
            if name in bucket:
                src = bucket[name]
                out[alias] = Reading(alias, src.value, src.unit, src.label)
                break
    return out


class LibreHardwareMonitorProvider(SensorProvider):
    name = "librehardwaremonitor"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.url = self.options.get("url", "http://127.0.0.1:8085/data.json")
        self.poll_seconds = float(self.options.get("poll_seconds", 1.0))
        self._latest: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, name="lhm-poll", daemon=True)
        self._thread.start()

    def _fetch(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url, headers={"User-Agent": f"libre-panel/{__version__}"}
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def _poll(self) -> None:
        warned = False
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                readings = readings_from_tree(self._fetch())
                with self._lock:
                    self._latest = readings
                warned = False
            except Exception as exc:
                if not warned:
                    log.warning("LibreHardwareMonitor not reachable at %s: %s", self.url, exc)
                    warned = True
            self._stop.wait(max(0.1, self.poll_seconds - (time.monotonic() - started)))

    def read(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._latest)

    def close(self) -> None:
        self._stop.set()

"""Windows sensors via LibreHardwareMonitor's "Remote Web Server".

LibreHardwareMonitor (MPL-2.0) runs as a separate program; enable
Options -> Remote Web Server and Libre Panel reads its ``data.json``
(format checked against real output, see tests/data/lhm_data.json).
Every sensor is exposed under its raw id (``lhm:/amdcpu/0/temperature/2``) and
the common ones also get friendly aliases such as ``cpu.temp`` or ``gpu.load``.
With several GPUs the dedicated one is used; ``gpu = "RTX 4080"`` in
``[sensors.librehardwaremonitor]`` picks one by name.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from libre_panel import __version__
from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(.*)$")
_CORE_CLOCK = re.compile(r"^(CPU )?Core #\d+$")

# alias -> (sensor type, preferred sensor names in order), per device kind.
# Names as LibreHardwareMonitor reports them for Intel, AMD and NVIDIA.
_CPU_ALIASES = {
    "cpu.temp": ("temperature", ("CPU Package", "Core (Tctl/Tdie)", "Tctl", "Tdie", "Package")),
    "cpu.load": ("load", ("CPU Total",)),
    "cpu.power": ("power", ("CPU Package", "Package")),
    "cpu.freq": ("clock", ("Cores (Average)",)),
}
_GPU_ALIASES = {
    "gpu.temp": ("temperature", ("GPU Core", "GPU Hot Spot")),
    "gpu.load": ("load", ("GPU Core", "D3D 3D")),
    # Board power (TBP), as SPUR II verified against HWiNFO.
    "gpu.power": ("power", ("GPU Package", "GPU Power", "GPU Core")),
    "gpu.freq": ("clock", ("GPU Core",)),
    "gpu.mem.load": ("load", ("GPU Memory",)),
    "gpu.mem.used": ("smalldata", ("GPU Memory Used",)),
    "gpu.fan": ("fan", ("GPU Fan", "GPU Fan 1")),
}


def parse_value(text: Any) -> tuple[float | None, str]:
    """Parse strings like ``'45,0 °C'`` or ``'1234 RPM'``; ``NaN`` and ``-`` give None."""
    if not isinstance(text, str):
        return None, ""
    match = _NUMBER.match(text)
    if not match:
        return None, ""
    return float(match.group(1).replace(",", ".")), match.group(2).strip()


@dataclass
class _Device:
    hardware_id: str
    name: str
    kind: str  # "cpu", "gpu" or the id prefix (motherboard, battery, ...)
    sensors: dict[tuple[str, str], Reading] = field(default_factory=dict)

    def get(self, sensor_type: str, names: tuple[str, ...]) -> Reading | None:
        for name in names:
            reading = self.sensors.get((sensor_type, name))
            if reading is not None:
                return reading
        return None


def _kind(hardware_id: str) -> str:
    head = hardware_id.strip("/").split("/", 1)[0].lower()
    if "cpu" in head:
        return "cpu"
    if head.startswith("gpu"):
        return "gpu"
    return head


def _walk(node: dict[str, Any], device: dict[str, Any] | None = None) -> Iterator[tuple]:
    """Yield (sensor node, device node) in document order."""
    if node.get("HardwareId"):
        device = node
    if node.get("SensorId"):
        yield node, device
    for child in node.get("Children") or []:
        yield from _walk(child, device)


def _reading(leaf: dict[str, Any], key: str) -> Reading | None:
    raw = leaf.get("RawValue")
    if str(leaf.get("Type", "")).lower() == "throughput" and isinstance(raw, (int, float)):
        return Reading(key, float(raw), "B/s", str(leaf.get("Text", "")))
    value, unit = parse_value(leaf.get("Value"))
    if value is None:
        return None
    return Reading(key, value, unit, str(leaf.get("Text", "")))


def _gpu_rank(device: _Device) -> tuple[int, float]:
    """Dedicated GPUs first: NVIDIA, then AMD/Intel with more than 2 GB of own memory."""
    total = device.sensors.get(("smalldata", "GPU Memory Total"))
    memory = total.value if total and isinstance(total.value, float) else 0.0
    vendor = 2 if "nvidia" in device.hardware_id else 1 if memory > 2048 else 0
    return vendor, memory


def readings_from_tree(tree: dict[str, Any], gpu: str | None = None) -> dict[str, Reading]:
    out: dict[str, Reading] = {}
    devices: dict[str, _Device] = {}
    for leaf, device_node in _walk(tree):
        sensor_id = str(leaf["SensorId"])
        reading = _reading(leaf, f"lhm:{sensor_id}")
        if reading is None:
            continue
        out[reading.key] = reading
        hardware_id = (device_node or {}).get("HardwareId") or "/".join(sensor_id.split("/")[:3])
        device = devices.get(hardware_id)
        if device is None:
            name = str((device_node or {}).get("Text", hardware_id))
            device = devices[hardware_id] = _Device(hardware_id, name, _kind(sensor_id))
        sensor_type = str(leaf.get("Type") or sensor_id.strip("/").split("/")[2]).lower()
        device.sensors.setdefault((sensor_type, reading.label), reading)

    cpus = [d for d in devices.values() if d.kind == "cpu"]
    gpus = [d for d in devices.values() if d.kind == "gpu"]
    if gpu:
        gpus = [d for d in gpus if gpu.lower() in d.name.lower()] or gpus
    gpus.sort(key=_gpu_rank, reverse=True)

    for device, aliases in (
        (cpus[0] if cpus else None, _CPU_ALIASES),
        (gpus[0] if gpus else None, _GPU_ALIASES),
    ):
        if device is None:
            continue
        for alias, (sensor_type, names) in aliases.items():
            src = device.get(sensor_type, names)
            if src is not None:
                out[alias] = Reading(alias, src.value, src.unit, src.label)
    if cpus and "cpu.freq" not in out:
        # Older LibreHardwareMonitor versions have no average: use the mean of the cores.
        clocks = [
            r.value
            for (sensor_type, name), r in cpus[0].sensors.items()
            if sensor_type == "clock" and _CORE_CLOCK.match(name) and isinstance(r.value, float)
        ]
        if clocks:
            out["cpu.freq"] = Reading("cpu.freq", sum(clocks) / len(clocks), "MHz", "CPU clock")
    if cpus:
        out["cpu.name"] = Reading("cpu.name", cpus[0].name, "", "CPU")
    if gpus:
        out["gpu.name"] = Reading("gpu.name", gpus[0].name, "", "GPU")
    return out


class LibreHardwareMonitorProvider(SensorProvider):
    name = "librehardwaremonitor"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.url = self.options.get("url", "http://127.0.0.1:8085/data.json")
        self.poll_seconds = float(self.options.get("poll_seconds", 1.0))
        self.gpu = self.options.get("gpu")
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
                readings = readings_from_tree(self._fetch(), self.gpu)
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

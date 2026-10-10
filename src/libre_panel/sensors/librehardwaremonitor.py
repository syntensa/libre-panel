"""Windows sensors via LibreHardwareMonitor's "Remote Web Server".

LibreHardwareMonitor (MPL-2.0) runs as a separate program; enable
Options -> Remote Web Server and Libre Panel reads its ``data.json``
(format checked against real output, see tests/data/lhm_data.json).
Every sensor is exposed under its raw id (``lhm:/amdcpu/0/temperature/2``) and
the common ones also get friendly aliases such as ``cpu.temp`` or ``gpu.load``.
With several GPUs the dedicated one is used; ``gpu = "RTX 4080"`` in
``[sensors.librehardwaremonitor]`` picks one by name.

When the web server stops answering, its readings are kept for
``stale_seconds`` (5 by default) and then dropped, so the panel shows "--"
instead of numbers that stand still; they return with the first answer.

On Windows this source is in the default list, so it must cost nothing where
LibreHardwareMonitor is not running: a request that is given up after a second,
waits that grow to half a minute while nothing answers, and one line in the log.
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
DEFAULT_URL = "http://127.0.0.1:8085/data.json"
STALE_SECONDS = 5.0  # without an answer for this long the readings are dropped
REQUEST_SECONDS = 1.0  # one request, connecting included: it answers in milliseconds or not at all
BACKOFF_SECONDS = 30.0  # the longest wait between two requests while nothing answers
# What on Windows only LibreHardwareMonitor gives (psutil has no sensors there).
HARDWARE_KEYS = ("cpu.temp", "gpu.temp", "cpu.power", "gpu.power")

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
                out[alias] = Reading(alias, src.value, src.unit, src.label, src.key)
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
    _temperatures_and_fans(list(devices.values()), out)
    return out


_STORAGE = ("nvme", "hdd", "ssd", "storage")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _temperatures_and_fans(devices: list[_Device], out: dict[str, Reading]) -> None:
    """Every temperature and fan as temp.<device>.<name> and fan.<device>.<name>."""
    count: dict[str, int] = {}
    for device in devices:
        count[device.kind] = count.get(device.kind, 0) + 1
    seen: dict[str, int] = {}
    for device in devices:
        seen[device.kind] = seen.get(device.kind, 0) + 1
        place = _slug(device.kind) + (str(seen[device.kind]) if count[device.kind] > 1 else "")
        for (sensor_type, label), reading in device.sensors.items():
            prefix = {"temperature": "temp", "fan": "fan"}.get(sensor_type)
            if prefix is None:
                continue
            key = f"{prefix}.{place}.{_slug(label)}"
            if device.kind in _STORAGE:
                name = device.name
            elif device.kind in ("cpu", "gpu") and not label.lower().startswith(device.kind):
                name = f"{device.kind.upper()} {label}"
            else:
                name = label
            out.setdefault(key, Reading(key, reading.value, reading.unit, name, reading.key))


def fetch_tree(url: str, timeout: float = REQUEST_SECONDS) -> dict[str, Any]:
    """The web server's ``data.json``; raises OSError (or ValueError) if it does not answer."""
    request = urllib.request.Request(url, headers={"User-Agent": f"libre-panel/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def hardware_hint(
    readings: dict[str, Reading], providers: list[str], url: str, platform: str
) -> str | None:
    """What to do on Windows when no CPU or GPU temperature or power arrives; None
    when it does, and elsewhere (psutil has temperatures and fans on Linux)."""
    if platform != "win32" or any(key in readings for key in HARDWARE_KEYS):
        return None
    text = (
        "No CPU or GPU temperature or power readings. On Windows these come from "
        "LibreHardwareMonitor: run it as administrator and turn on "
        f"Options -> Remote Web Server -> Run (Libre Panel then reads {url})."
    )
    if "librehardwaremonitor" not in providers:
        text += ' Add "librehardwaremonitor" to providers under [sensors] in config.toml.'
    return text


class LibreHardwareMonitorProvider(SensorProvider):
    name = "librehardwaremonitor"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.url = self.options.get("url", DEFAULT_URL)
        self.poll_seconds = float(self.options.get("poll_seconds", 1.0))
        # 0 (or less) keeps the last readings however long the web server is silent.
        self.stale_seconds = float(self.options.get("stale_seconds", STALE_SECONDS))
        self.gpu = self.options.get("gpu")
        self._latest: dict[str, Reading] = {}
        self._answered_at: float | None = None  # when the web server last answered
        self._misses = 0  # requests in a row that nobody answered while no readings were shown
        self._warned = False  # said that it is silent; says so when it answers again
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, name="lhm-poll", daemon=True)
        self._thread.start()

    def _fetch(self) -> dict[str, Any]:
        return fetch_tree(self.url)

    def _poll(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            delay = self._poll_once()
            self._stop.wait(max(0.1, delay - (time.monotonic() - started)))

    def _poll_once(self, now: float | None = None) -> float:
        """One request; returns the seconds to wait before the next. ``now``
        (``time.monotonic()``) is for tests."""
        try:
            readings = readings_from_tree(self._fetch(), self.gpu)
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, it did not answer
            return self._unanswered(exc, time.monotonic() if now is None else now)
        with self._lock:
            self._latest = readings
            self._answered_at = time.monotonic() if now is None else now
        self._misses = 0
        if self._warned:
            self._warned = False
            log.info("LibreHardwareMonitor answers again at %s: its readings are back", self.url)
        return self.poll_seconds

    def _unanswered(self, exc: Exception, now: float) -> float:
        """A short silence keeps the last readings (one slow answer is no reason to
        blank the panel); a long one drops them, so numbers never stand still unseen.
        With nothing to show, the requests come less and less often."""
        with self._lock:
            shown = bool(self._latest)
            silent = now - self._answered_at if self._answered_at is not None else 0.0
            expired = shown and 0 < self.stale_seconds <= silent
            if expired:
                self._latest = {}
        if not self._warned:
            self._warned = self._say_silent(exc, expired, silent)
        if shown and not expired:
            return self.poll_seconds  # their time is not up: ask as usual, they go in time
        self._misses += 1
        wait = self.poll_seconds * 2 ** min(self._misses - 1, 10)
        return max(self.poll_seconds, min(BACKOFF_SECONDS, wait))

    def _say_silent(self, exc: Exception, expired: bool, silent: float) -> bool:
        """Log that nothing answers, once. False where a short silence says nothing yet."""
        if self._answered_at is None:  # not running, most likely: not worth more than a line
            log.info(
                "LibreHardwareMonitor does not answer at %s (%s): temperature, power and GPU "
                "readings need it, run as administrator with Options -> Remote Web Server -> Run",
                self.url,
                exc,
            )
        elif expired:
            log.warning(
                "LibreHardwareMonitor has not answered at %s for %.0f s (%s): "
                "its readings are hidden until it does",
                self.url,
                silent,
                exc,
            )
        elif self.stale_seconds <= 0:
            log.warning("LibreHardwareMonitor not reachable at %s: %s", self.url, exc)
        else:
            return False
        return True

    def read(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._latest)

    def close(self) -> None:
        self._stop.set()

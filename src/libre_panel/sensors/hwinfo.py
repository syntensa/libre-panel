"""Temperatures, power, clocks, loads and fans from HWiNFO on Windows.

For those who run HWiNFO anyway: switched on in *Settings → Sensors* (``hwinfo`` in
``providers``), it reads what HWiNFO shares while its *Shared Memory Support* is on (in HWiNFO's
settings; the free version keeps it on for twelve hours at a time). It starts no program and
needs no rights: only the memory HWiNFO shares, ``Global\\HWiNFO_SENS_SM2``, is read, once a
second at most and only when HWiNFO has polled anew. Without HWiNFO it reads nothing and looks
again every few seconds.

The memory, as HWiNFO's own description of it has it (``hwisenssm2.h``, packed): a header that
begins with the signature ``HWiS`` (``DEAD`` while sharing is off), its version and revision,
the time of the last poll, then where the sensors begin, how large each is and how many there
are, and the same for the readings. A sensor is a device as HWiNFO groups it ("CPU [#0]: AMD
Ryzen 9 7950X: Enhanced", "GPU [#0]: NVIDIA GeForce RTX 4090", a mainboard's chip, a drive),
with its id, instance and names; a reading has its type (temperature, voltage, fan, current,
power, clock, usage, other), the sensor it belongs to, its id, its label as HWiNFO has it and
as the person renamed it, its unit and its value with the lowest, highest and mean. The names
are read as the person named them (HWiNFO's UTF-8 ones from version 2 on).

Readings: every one under ``hwinfo:<sensor>/<reading>``, and the common ones under the keys
LibreHardwareMonitor's give (``cpu.temp``, ``cpu.load``, ``cpu.power``, ``cpu.freq``,
``gpu.temp``, ``gpu.hotspot``, ``gpu.load``, ``gpu.power``, ``gpu.freq``, ``gpu.mem.*``,
``gpu.fan``, ``cpu.name``, ``gpu.name``), every temperature and fan by device and name
(``temp.gpu.gpu_temperature``, ``fan.motherboard.chassis1``): a theme made for one shows the same
with the other. Where both run, the source asked first gives a key.
"""

from __future__ import annotations

import logging
import re
import struct
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from libre_panel.sensors import rtss
from libre_panel.sensors.base import Reading, SensorProvider
from libre_panel.sensors.librehardwaremonitor import LhmSensor, readings_from_sensors

log = logging.getLogger(__name__)

MAPPING = "Global\\HWiNFO_SENS_SM2"
SIGNATURE = 0x53695748  # "HWiS", as a DWORD
STOPPED = 0x44414544  # "DEAD": sharing is off (the free version's twelve hours are over)
# signature, version, revision, poll time, then offset, size and count of the sensors and of
# the readings
HEADER = struct.Struct("<IIIqIIIIII")
SENSOR = struct.Struct("<II128s128s")  # id, instance, original name, the person's name
SENSOR_UTF = struct.Struct("<128s")  # (version 2 on) the person's name in UTF-8, after it
# type, sensor index, id, original label, the person's label, unit, value, lowest, highest, mean
READING = struct.Struct("<III128s128s16s4d")
READING_UTF = struct.Struct("<128s16s")  # (version 2 on) the label and unit in UTF-8, after it
MOST = 16384  # (a header that says more sensors or readings than this is not HWiNFO's)
LOOK_SECONDS = 5.0  # without HWiNFO: how often it is looked for again
FRESH_SECONDS = 1.0  # the memory read at most this often

# HWiNFO's reading types, as LibreHardwareMonitor's sensor types (whose keys these become)
_TYPES = {1: "Temperature", 2: "Voltage", 3: "Fan", 4: "Current", 5: "Power", 6: "Clock",
          7: "Load", 8: "Other"}  # fmt: skip
# what HWiNFO calls what LibreHardwareMonitor's keys are made from, per device kind: (its type,
# its label) -> LibreHardwareMonitor's label (librehardwaremonitor._CPU_ALIASES, _GPU_ALIASES)
_LABELS = {
    "cpu": {
        ("Temperature", "CPU (Tctl/Tdie)"): "Core (Tctl/Tdie)",
        ("Temperature", "CPU Package"): "CPU Package",
        ("Load", "Total CPU Usage"): "CPU Total",
        ("Power", "CPU Package Power"): "CPU Package",
        ("Power", "CPU PPT"): "Package",
        ("Clock", "Core Clocks (avg)"): "Cores (Average)",
        ("Clock", "Average Effective Clock"): "Cores (Average)",
    },
    "gpu": {
        ("Temperature", "GPU Temperature"): "GPU Core",
        ("Temperature", "GPU Hot Spot Temperature"): "GPU Hot Spot",
        ("Load", "GPU Core Load"): "GPU Core",
        ("Load", "GPU D3D Usage"): "D3D 3D",
        ("Load", "GPU Memory Usage"): "GPU Memory",
        ("Power", "Total Board Power (TBP)"): "GPU Package",
        ("Power", "GPU Power"): "GPU Power",
        ("Clock", "GPU Clock"): "GPU Core",
        ("Fan", "GPU Fan1"): "GPU Fan 1",
        ("Fan", "GPU Fan"): "GPU Fan",
    },
}
_DEVICE = re.compile(r"^(CPU|GPU) \[#(\d+)\]:\s*(.*?)(?::\s*[^:]*)?$")
_MEMORY_USED = "GPU Memory Allocated"  # MB, as LibreHardwareMonitor's "GPU Memory Used"


def _text(raw: bytes, utf: bool = False) -> str:
    raw = raw.split(b"\0", 1)[0]
    return raw.decode("utf-8" if utf else "cp1252", errors="replace").strip()


def _kind(name: str) -> tuple[str, str, str]:
    """A sensor's kind as LibreHardwareMonitor's ids have it ("cpu", "gpu", "storage", "lpc"
    for the mainboard's chips, "memory", ...), the id of its device and the device's name: the
    processor's and the graphics card's sensors are one device each, by their number in HWiNFO
    ("CPU [#0]: ..." and "CPU [#0]: ...: Enhanced")."""
    found = _DEVICE.match(name)
    if found:
        kind = found[1].lower()
        device = found[3].split(":")[0].strip() or name
        return kind, f"/{kind}/{found[2]}", device
    lower = name.lower()
    if lower.startswith(("s.m.a.r.t", "drive")):
        return "storage", f"/storage/{name}", name.split(":", 1)[-1].strip() or name
    if lower.startswith("system") or "memory" in lower:
        return "memory", f"/memory/{name}", name
    if lower.startswith(("network", "windows hardware errors", "battery")):
        return lower.split()[0], f"/{lower.split()[0]}/{name}", name
    return "lpc", f"/lpc/{name}", name  # the mainboard's chips: fans as LibreHardwareMonitor's


def _unit(kind: str, unit: str, value: float) -> tuple[str, float]:
    """A reading's type and value as LibreHardwareMonitor's keys need them: an amount of
    memory in MB as "SmallData", one in GB as "Data", a rate as "Throughput" in bytes."""
    lower = unit.strip().lower()
    if kind == "Other":
        if lower == "mb":
            return "SmallData", value
        if lower == "gb":
            return "Data", value
        if lower.endswith("/s") and lower[:-2] in ("kb", "mb", "gb"):
            return "Throughput", value * 1024 ** ("kmg".index(lower[0]) + 1)
    return kind, value


def sensors_of(memory: rtss.Memory) -> list[LhmSensor] | None:
    """The readings in HWiNFO's memory as sensors (LibreHardwareMonitor's kind of record, so
    that they are named the same); None where it is not HWiNFO's, or sharing is off."""
    header = HEADER.unpack(memory.read(0, HEADER.size))
    signature, version, _revision, _polled, s_at, s_size, s_count, r_at, r_size, r_count = header
    if signature != SIGNATURE or not 0 < s_count <= MOST or not 0 <= r_count <= MOST:
        return None
    if s_size < SENSOR.size or r_size < READING.size:
        return None
    devices = []
    sensor_utf = version >= 2 and s_size >= SENSOR.size + SENSOR_UTF.size
    block = memory.read(s_at, s_size * s_count)
    for n in range(s_count):
        at = n * s_size
        _id, _instance, original, own = SENSOR.unpack_from(block, at)
        name = _text(own) or _text(original)
        if sensor_utf:
            name = _text(SENSOR_UTF.unpack_from(block, at + SENSOR.size)[0], utf=True) or name
        devices.append((name, *_kind(name)))
    reading_utf = version >= 2 and r_size >= READING.size + READING_UTF.size
    block = memory.read(r_at, r_size * r_count)
    out = []
    for n in range(r_count):
        at = n * r_size
        kind_no, index, reading_id, original, own, unit_raw, value, *_ = READING.unpack_from(
            block, at
        )
        if kind_no not in _TYPES or index >= len(devices) or value != value:  # (NaN)
            continue
        label, unit = _text(own) or _text(original), _text(unit_raw)
        if reading_utf:
            utf_label, utf_unit = READING_UTF.unpack_from(block, at + READING.size)
            label, unit = _text(utf_label, utf=True) or label, _text(utf_unit, utf=True) or unit
        sensor_name, kind, device_id, device_name = devices[index]
        sensor_type, value = _unit(_TYPES[kind_no], unit, value)
        if kind == "gpu" and sensor_type == "SmallData" and label == _MEMORY_USED:
            label = "GPU Memory Used"
        label = _LABELS.get(kind, {}).get((sensor_type, label), label)
        unit = {"Throughput": "B/s", "Fan": "RPM"}.get(sensor_type, unit)
        out.append(LhmSensor(f"{device_id}/{index}/{reading_id}", device_id, device_name,
                             sensor_type, label, float(value), unit))  # fmt: skip
    return out


def readings_of(sensors: Iterable[LhmSensor]) -> dict[str, Reading]:
    """Every reading under ``hwinfo:<device>/<sensor>/<reading>``, and the common ones under
    the keys LibreHardwareMonitor's give."""
    out = {}
    for key, reading in readings_from_sensors(sensors).items():
        if key.startswith("lhm:"):
            key = "hwinfo:" + key[4:]
        origin = reading.origin
        if origin.startswith("lhm:"):
            origin = "hwinfo:" + origin[4:]
        out[key] = Reading(key, reading.value, reading.unit, reading.label, origin,
                           device=reading.device)  # fmt: skip
    return out


class HWiNFOProvider(SensorProvider):
    """Readings from HWiNFO's shared memory, while HWiNFO shares it."""

    name = "hwinfo"

    def __init__(self, options: dict[str, Any] | None = None,
                 open_memory: Callable[[], rtss.Memory | None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        super().__init__(options)
        self._open = open_memory or (lambda: rtss.open_memory(MAPPING))
        self._clock = clock
        self._memory: rtss.Memory | None = None
        self._looked = -LOOK_SECONDS
        self._read_at = -FRESH_SECONDS
        self._polled: int | None = None
        self._readings: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self.state = "looking"  # "reading", "looking" (no HWiNFO), "stopped" (sharing is off)

    def read(self) -> dict[str, Reading]:
        with self._lock:
            now = self._clock()
            if now - self._read_at < FRESH_SECONDS:
                return dict(self._readings)
            self._read_at = now
            if self._memory is None:
                if now - self._looked < LOOK_SECONDS:
                    return {}
                self._looked = now
                self._memory = self._open()
                if self._memory is None:
                    self.state, self._readings = "looking", {}
                    return {}
            try:
                header = HEADER.unpack(self._memory.read(0, HEADER.size))
                if header[0] == STOPPED:
                    self.state, self._readings = "stopped", {}
                    return {}
                if header[3] != self._polled:  # polled anew since: read again
                    found = sensors_of(self._memory)
                    if found is None:
                        raise ValueError("not HWiNFO's memory")
                    self._readings, self._polled = readings_of(found), header[3]
                    self.state = "reading"
            except (OSError, ValueError, struct.error) as exc:
                log.debug("HWiNFO's memory cannot be read: %s", exc)
                self._close()
                self.state, self._readings = "looking", {}
            return dict(self._readings)

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "readings": len(self._readings)}

    def _close(self) -> None:
        if self._memory is not None:
            try:
                self._memory.close()
            except OSError:
                pass
            self._memory = None

    def close(self) -> None:
        with self._lock:
            self._close()

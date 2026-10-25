"""Windows sensors from LibreHardwareMonitor: the GPU, temperatures, power and fans.

LibreHardwareMonitor (MPL-2.0) is read one of three ways, chosen by ``mode`` in
``[sensors.librehardwaremonitor]``:

- **The sensor service** (``lhm_service``): Libre Panel's Windows service reads it with the
  rights that the CPU's, the mainboard's, the memory's and the drives' sensors need, and
  hands the readings over a local pipe. The Windows setup installs it; the portable download
  offers "Unlock all sensors" (``libre-panel system-setup``).
- **Inside Libre Panel** (``lhm_inprocess``): its .NET library is loaded through pythonnet
  and read in a thread. The Windows downloads carry it; with pip it needs
  ``pip install "libre-panel[lhm]"`` and a ``dll_dir``. CPU temperature and power then need
  Libre Panel to run as administrator (and the PawnIO driver).
- **Its web server**: LibreHardwareMonitor runs as a separate program, as administrator,
  with Options -> Remote Web Server; Libre Panel reads its ``data.json`` (format checked
  against real output, see tests/data/lhm_data.json).

``auto`` (the default) takes the service where it is installed, else the second way where it
is possible and the third otherwise. A service that stops answering is replaced by the other
ways after a while (``lhm_service.WAIT_SECONDS``), and takes over again when it answers.
Either way every sensor is exposed under its raw id (``lhm:/amdcpu/0/temperature/2``) and
the common ones also get friendly aliases such as ``cpu.temp``, ``gpu.load`` or ``mem.temp``
(the hottest memory module), made by the same code. With several GPUs the dedicated one is
used; ``gpu = "RTX 4070"`` picks one by name.

When the web server stops answering, its readings are kept for
``stale_seconds`` (5 by default) and then dropped, so the panel shows "--"
instead of numbers that stand still; they return with the first answer.

On Windows this source is in the default list, so it must cost nothing where
LibreHardwareMonitor is not running: a request that is given up after a second,
waits that grow to half a minute while nothing answers, and one line in the log.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import logging
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from libre_panel import __version__
from libre_panel.i18n import t
from libre_panel.sensors.base import Reading, SensorProvider
from libre_panel.sensors.hwnames import tidy_cpu, tidy_gpu

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(.*)$")
_CORE_CLOCK = re.compile(r"^(CPU )?Core #\d+$")
DEFAULT_URL = "http://127.0.0.1:8085/data.json"
STALE_SECONDS = 5.0  # without an answer for this long the readings are dropped
REQUEST_SECONDS = 1.0  # one request, connecting included: it answers in milliseconds or not at all
BACKOFF_SECONDS = 30.0  # the longest wait between two requests while nothing answers
SERVICE_LOOK_SECONDS = 10.0  # auto without the sensor service: how often to look for it
# What on Windows only LibreHardwareMonitor gives (psutil has no sensors there).
HARDWARE_KEYS = ("cpu.temp", "gpu.temp", "cpu.power", "gpu.power")
MODES = ("auto", "service", "inprocess", "webserver")
PAWNIO_URL = "https://pawnio.eu"  # the driver LibreHardwareMonitor reads the CPU through

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
    "gpu.hotspot": ("temperature", ("GPU Hot Spot",)),  # the hottest spot of the chip
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


@dataclass(frozen=True)
class LhmSensor:
    """One sensor with a value, whichever way it was read: from the web server's
    tree or from LibreHardwareMonitor running inside Libre Panel. Both ways end in
    ``readings_from_sensors``, so they name and group readings the same."""

    sensor_id: str  # "/amdcpu/0/temperature/2"
    hardware_id: str  # "/amdcpu/0"
    hardware_name: str  # as LibreHardwareMonitor shows it
    sensor_type: str  # LibreHardwareMonitor's SensorType: "Temperature", "Load", ...
    label: str  # "Core (Tctl/Tdie)"
    value: float
    unit: str  # "°C"; "" for a plain number


def _leaf_value(leaf: dict[str, Any]) -> tuple[float, str] | None:
    raw = leaf.get("RawValue")
    if str(leaf.get("Type", "")).lower() == "throughput" and isinstance(raw, (int, float)):
        return float(raw), "B/s"
    value, unit = parse_value(leaf.get("Value"))
    if value is None:
        return None
    return value, unit


def sensors_from_tree(tree: dict[str, Any]) -> Iterator[LhmSensor]:
    """The sensors of the web server's ``data.json`` that have a value."""
    for leaf, device_node in _walk(tree):
        found = _leaf_value(leaf)
        if found is None:
            continue
        sensor_id = str(leaf["SensorId"])
        hardware_id = (device_node or {}).get("HardwareId") or "/".join(sensor_id.split("/")[:3])
        yield LhmSensor(
            sensor_id,
            hardware_id,
            str((device_node or {}).get("Text", hardware_id)),
            str(leaf.get("Type") or sensor_id.strip("/").split("/")[2]),
            str(leaf.get("Text", "")),
            *found,
        )


# An amount of data in bytes, by the unit LibreHardwareMonitor tells it in
_SIZES = {"B": 1, "KB": 2**10, "MB": 2**20, "GB": 2**30, "TB": 2**40}


def _as_before(sensor: LhmSensor) -> LhmSensor:
    """An amount of data as LibreHardwareMonitor 0.9.6 tells it, whichever tells it: 0.9.7 has
    one type for every amount, in bytes (its web server: in a unit that reads well), where 0.9.6
    told GB ("Data") and a graphics card's memory in MB ("SmallData"), which is what the keys
    here are made from (``gpu.mem.used``, the memory's GB)."""
    kind = sensor.sensor_type.lower()
    if kind not in ("data", "smalldata"):
        return sensor
    size = _SIZES.get(sensor.unit.strip().upper().replace("IB", "B"))
    if size is None:
        return sensor
    small = kind == "smalldata" or _kind(sensor.sensor_id) == "gpu"
    kind, unit, per = ("SmallData", "MB", 2**20) if small else ("Data", "GB", 2**30)
    if sensor.sensor_type == kind and sensor.unit == unit:
        return sensor
    return dataclasses.replace(sensor, sensor_type=kind, value=sensor.value * size / per, unit=unit)


def _gpu_rank(device: _Device) -> tuple[int, float]:
    """Dedicated GPUs first: NVIDIA, then AMD/Intel with more than 2 GB of own memory."""
    total = device.sensors.get(("smalldata", "GPU Memory Total"))
    memory = total.value if total and isinstance(total.value, float) else 0.0
    vendor = 2 if "nvidia" in device.hardware_id else 1 if memory > 2048 else 0
    return vendor, memory


def readings_from_tree(tree: dict[str, Any], gpu: str | None = None) -> dict[str, Reading]:
    return readings_from_sensors(sensors_from_tree(tree), gpu)


def readings_from_sensors(
    sensors: Iterable[LhmSensor],
    gpu: str | None = None,
    sources: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Reading]:
    """Every sensor under its raw key (``lhm:<id>``), the common ones under friendly
    keys (``cpu.temp``, ``gpu.load``), every temperature and fan by device and name.

    ``sources``, if given, is filled with the ids of the sensors each key is made
    from (none for a name), so that a caller can tell which sensors a key needs."""
    out: dict[str, Reading] = {}
    devices: dict[str, _Device] = {}
    for sensor in map(_as_before, sensors):
        reading = Reading(f"lhm:{sensor.sensor_id}", sensor.value, sensor.unit, sensor.label)
        out[reading.key] = reading
        device = devices.get(sensor.hardware_id)
        if device is None:
            device = devices[sensor.hardware_id] = _Device(
                sensor.hardware_id, sensor.hardware_name, _kind(sensor.sensor_id)
            )
        device.sensors.setdefault((sensor.sensor_type.lower(), reading.label), reading)

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
    averaged: tuple[str, ...] = ()
    if cpus and "cpu.freq" not in out:
        # No average clock (Intel, and older versions): use the mean of the cores.
        cores = [
            r
            for (sensor_type, name), r in cpus[0].sensors.items()
            if sensor_type == "clock" and _CORE_CLOCK.match(name) and isinstance(r.value, float)
        ]
        if cores:
            mean = sum(r.value for r in cores) / len(cores)
            out["cpu.freq"] = Reading("cpu.freq", mean, "MHz", "CPU clock")
            averaged = tuple(r.key[4:] for r in cores)
    # tidied as the hardware source tidies the names it finds, so they read alike
    if cpus:
        out["cpu.name"] = Reading("cpu.name", tidy_cpu(cpus[0].name), "", "CPU")
    if gpus:
        out["gpu.name"] = Reading("gpu.name", tidy_gpu(gpus[0].name), "", "GPU")
    _temperatures_and_fans(list(devices.values()), out)
    modules = _memory_temperature(list(devices.values()), out)
    if sources is not None:
        for key, reading in out.items():
            origin = reading.origin or (key if key.startswith("lhm:") else "")
            sources[key] = (origin[4:],) if origin else ()
        if averaged:
            sources["cpu.freq"] = averaged
        if modules:  # whichever module is the hottest: all of them are read
            sources["mem.temp"] = modules
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


def _memory_temperature(devices: list[_Device], out: dict[str, Reading]) -> tuple[str, ...]:
    """``mem.temp``: the temperature of the hottest memory module (DDR4 and DDR5 have a thermal
    sensor on the module). LibreHardwareMonitor puts each module under ``/memory/dimm/<n>``
    and names its sensor "DIMM #<n>"; the module's limits are temperatures too ("Thermal
    Sensor High Limit"), but they never change, so only the "DIMM" ones count. Returns the ids
    of the sensors it was made from."""
    found = [
        reading
        for device in devices
        if device.kind in ("memory", "ram")
        for (sensor_type, label), reading in device.sensors.items()
        if sensor_type == "temperature"
        and label.upper().startswith("DIMM")
        and isinstance(reading.value, float)
    ]
    if not found:
        return ()
    hottest = max(found, key=lambda reading: reading.value)
    out["mem.temp"] = Reading(
        "mem.temp", hottest.value, hottest.unit or "°C", "Memory temperature", hottest.key
    )
    return tuple(reading.key[4:] for reading in found)


# This PC's own web server is asked directly: a system proxy would not reach it.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def fetch_tree(url: str, timeout: float = REQUEST_SECONDS) -> dict[str, Any]:
    """The web server's ``data.json``; raises OSError (or ValueError) if it does not answer."""
    request = urllib.request.Request(url, headers={"User-Agent": f"libre-panel/{__version__}"})
    host = urllib.parse.urlsplit(url).hostname or ""
    opener = _DIRECT.open if host in _LOOPBACK else urllib.request.urlopen
    with opener(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass(frozen=True)
class PartTime:
    """How long one piece of hardware took in a round: its ``Update()`` and the reading of its
    sensors after it (what holds Python's lock in Libre Panel, and the CPU in the service)."""

    hardware_id: str
    name: str
    kind: str  # LibreHardwareMonitor's HardwareType: Cpu, GpuNvidia, SuperIO, Network ...
    seconds: float


@dataclass(frozen=True)
class RoundTime:
    """One round of updates, the parts in the order they were updated. ``full``: it updated
    all hardware (the survey now and then), not only what the themes on show read."""

    parts: tuple[PartTime, ...] = ()
    full: bool = False

    @property
    def seconds(self) -> float:
        return sum(part.seconds for part in self.parts)


@dataclass(frozen=True)
class Timing:
    """What the rounds of a source cost: the last one, and the last that updated all
    hardware (None: none yet)."""

    last: RoundTime | None = None
    full: RoundTime | None = None

    def after(self, done: RoundTime) -> Timing:
        """These times once ``done`` is over."""
        return Timing(done, done if done.full else self.full)


def time_round(
    nodes: Iterable[Any],
    update: Callable[[Any], None],
    pause: Callable[[], None],
    full: bool,
    timer: Callable[[], float] = time.perf_counter,
) -> RoundTime:
    """Update each of ``nodes`` (``update(node)``, ``pause()`` between two) and time each:
    the round of the sensor service and of the source inside Libre Panel alike. A node is
    one of lhm_inprocess's: its id, name and type are what the times say."""
    parts = []
    for position, node in enumerate(nodes):
        if position:
            pause()
        started = timer()
        update(node)
        took = max(0.0, timer() - started)
        parts.append(PartTime(node.hardware_id, node.name, node.hardware_type, took))
    return RoundTime(tuple(parts), full)


@dataclass(frozen=True)
class LhmStatus:
    """How LibreHardwareMonitor is read, and what keeps its CPU sensors away."""

    mode: str  # "service" (the sensor service), "inprocess" (inside Libre Panel) or "webserver"
    elevated: bool | None = None  # it runs with administrator rights (service, inprocess)
    ring0: bool | None = None  # the CPU's sensors give real values; None: no such sensors / unknown
    pawnio: str | None = None  # version of the installed PawnIO driver ("" unknown), None: none
    error: str = ""  # why it could not start, or does not answer (service, inprocess)
    service: str = ""  # why the installed sensor service is not what is read now
    # What its rounds cost (Live -> Diagnose -> Load, /api/load); None: not reported, as by a
    # sensor service older than this, or the web server, whose rounds are its own program's.
    # Neither this nor the version is in the status the pages show (plugins/pages.py).
    timing: Timing | None = field(default=None, compare=False, metadata={"page": False})
    # the sensor service's version, once it said hello
    version: str = field(default="", compare=False, metadata={"page": False})


# What sets up the sensor service, which reads the CPU with the rights it needs (and PawnIO).
SERVICE_STEP = (
    "set up the sensor service (Unlock all sensors in the app, or as administrator: "
    "libre-panel system-setup --service --pawnio)"
)


def ring0_steps(status: LhmStatus, unlock: bool = False) -> list[str]:
    """What stands between the CPU's sensors and Libre Panel, from what is known: it has
    to run as administrator, and the PawnIO driver (2.0 or newer) has to be installed.
    ``unlock``: this download can set up the sensor service, which brings both."""
    if unlock and status.elevated is False:
        return [SERVICE_STEP]
    steps = []
    if status.elevated is False:
        steps.append("run Libre Panel as administrator (libre-panel autostart enable --elevated)")
    if status.pawnio is None:
        steps.append(f"install the PawnIO driver ({PAWNIO_URL})")
    elif (old := re.match(r"\d+", status.pawnio)) and int(old.group()) < 2:
        steps.append(f"update the PawnIO driver to version 2 or newer ({PAWNIO_URL})")
    return steps


def hardware_hint(
    readings: dict[str, Reading],
    providers: list[str],
    url: str,
    platform: str,
    status: LhmStatus | None = None,
    unlock: bool = False,
) -> str | None:
    """What to do on Windows when no CPU or GPU temperature or power arrives; None
    when it does, and elsewhere (psutil has temperatures and fans on Linux).
    ``status`` says how LibreHardwareMonitor is read, when Libre Panel reads it itself;
    ``unlock``, whether this download can set up the sensor service."""
    if platform != "win32" or any(key in readings for key in HARDWARE_KEYS):
        return None
    text = "No CPU or GPU temperature or power readings. "
    if status is not None and status.service:
        return f"{text}The sensor service does not answer: {status.service}."
    if status is not None and status.mode == "service":
        if status.error:
            return f"{text}The sensor service does not answer: {status.error}."
        steps = ring0_steps(status)
        if not steps:
            return (
                f"{text}The sensor service runs, but LibreHardwareMonitor finds no sensors "
                "it supports."
            )
        return (
            f"{text}The sensor service runs, but the CPU's sensors are out of reach: "
            f"{' and '.join(steps)}."
        )
    if status is not None and status.mode == "inprocess":
        if status.error:
            said = f"{text}LibreHardwareMonitor could not start inside Libre Panel: {status.error}"
            return f"{said}. Instead: {SERVICE_STEP}." if unlock else said
        steps = ring0_steps(status, unlock)
        if not steps:
            return (
                f"{text}LibreHardwareMonitor runs inside Libre Panel, "
                "but finds no sensors it supports."
            )
        return (
            f"{text}LibreHardwareMonitor runs inside Libre Panel, but the CPU's sensors are out "
            f"of reach: {' and '.join(steps)}."
        )
    text += (
        "On Windows these come from LibreHardwareMonitor: run it as administrator and turn on "
        f"Options -> Remote Web Server -> Run (Libre Panel then reads {url})."
    )
    if "librehardwaremonitor" not in providers:
        text += ' Add "librehardwaremonitor" to providers under [sensors] in config.toml.'
    if status is not None and status.error:  # it tried to start inside Libre Panel first
        text += f" (Inside Libre Panel it could not start: {status.error}.)"
        if unlock:
            text += f" Or {SERVICE_STEP}."
    return text


def _old_pawnio(version: str | None) -> bool:
    """Whether the installed PawnIO driver is older than 2 (a version that is not said is not)."""
    found = re.match(r"\d+", version or "")
    return found is not None and int(found.group()) < 2


def user_hint(
    readings: dict[str, Reading],
    platform: str,
    status: LhmStatus | None = None,
    ready: bool = True,
    unlock: bool = False,
) -> str:
    """One sentence for a person who sees "--" where Windows' temperatures and power should
    be: what is the matter and what to do about it, in their language. Empty when all is
    well, elsewhere, while LibreHardwareMonitor is still starting (``ready`` False) and for
    what nobody can change (a PC with no sensors it supports). ``unlock``: this download can
    set up the sensor service ("Unlock all sensors"), which is then what to do.

    ``hardware_hint`` is the same question for the command line, with the technical words;
    this one is for the tray and the editor, and speaks of what a person can do.
    """
    if platform != "win32" or not ready:
        return ""
    nothing = not any(key in readings for key in HARDWARE_KEYS)
    unlocks = t(
        "CPU temperature, power and fans need the sensor service: "
        "choose “Unlock all sensors” on Start."
    )
    if status is not None and (status.service or (status.mode == "service" and status.error)):
        if "cpu.temp" in readings:
            return ""
        return t(
            "CPU temperature and power are missing: the sensor service does not answer. "
            "Set it up again in Settings → Sensors, or restart Windows."
        )
    if status is not None and status.error:  # it did not start inside Libre Panel
        if not nothing:
            return ""  # the web server has them
        if unlock:
            return unlocks
        return t(
            "Temperatures and power are missing: their reader did not start. "
            "Reinstall Libre Panel; the log says why."
        )
    if status is None or status.mode not in ("inprocess", "service"):
        if not nothing:
            return ""
        return t(
            "Temperatures and power need LibreHardwareMonitor: start it as administrator "
            "and turn on Options > Remote Web Server > Run."
        )
    if status.ring0 is not False or "cpu.temp" in readings:  # in reach, unknown, or filled in
        return ""
    admin, missing, old = (
        status.elevated is False,
        status.pawnio is None,
        _old_pawnio(status.pawnio),
    )
    if admin and unlock:
        return unlocks
    if admin and (missing or old):
        return t(
            "CPU temperature and power need administrator rights and the PawnIO driver (pawnio.eu)."
        )
    if admin:
        return t(
            "CPU temperature and power need administrator rights: run Libre Panel as administrator."
        )
    if missing:
        return t("CPU temperature and power need the PawnIO driver: install it from pawnio.eu.")
    if old:
        return t("CPU temperature and power need PawnIO 2 or newer: update it from pawnio.eu.")
    return ""


def dll_dir_of(options: dict[str, Any]) -> str | None:
    """``dll_dir`` of ``[sensors.librehardwaremonitor]``: the folder with the .NET libraries."""
    value = options.get("dll_dir")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        log.warning("[sensors.librehardwaremonitor] dll_dir must be a folder name, not %r", value)
        return None
    return value


def _service_installed() -> bool:
    """Whether Libre Panel's sensor service is installed (and not switched off) here."""
    from libre_panel.sensors import lhm_service

    return lhm_service.installed()


def _mode(value: Any) -> str:
    if isinstance(value, str) and value.lower() in MODES:
        return value.lower()
    log.warning(
        '[sensors.librehardwaremonitor] mode must be "auto", "service", "inprocess" or '
        '"webserver", not %r: using "auto"',
        value,
    )
    return "auto"


class LibreHardwareMonitorProvider(SensorProvider):
    name = "librehardwaremonitor"
    # A new reading shows in the next frame, not at the next snapshot: three clocks of a
    # second each (the service, its client, the snapshot) made it late and uneven. Reading
    # costs a copy of the latest, whichever way they come (the web server is asked by a
    # thread of its own).
    every_frame = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.url = self.options.get("url", DEFAULT_URL)
        self.poll_seconds = float(self.options.get("poll_seconds", 1.0))
        # 0 (or less) keeps the last readings however long the web server is silent.
        self.stale_seconds = float(self.options.get("stale_seconds", STALE_SECONDS))
        self.gpu = self.options.get("gpu")
        self.mode = _mode(self.options.get("mode", "auto"))
        self._latest: dict[str, Reading] = {}
        self._answered_at: float | None = None  # when the web server last answered
        self._misses = 0  # requests in a row that nobody answered while no readings were shown
        self._warned = False  # said that it is silent; says so when it answers again
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._polled = threading.Event()  # the web server was asked once, answer or not
        self._web = threading.Lock()  # starting and stopping the poller
        self._thread: threading.Thread | None = None  # the poller of the web server
        self._web_off: threading.Event | None = None  # that poller is no longer wanted
        self._source: Any = None  # LibreHardwareMonitor inside Libre Panel (lhm_inprocess)
        self._why = ""  # why it is not inside Libre Panel
        self._service: Any = None  # the sensor service (lhm_service)
        self._fallback = False  # auto: the service does not answer, the other ways read meanwhile
        self._silent = ""  # why the service does not answer, once it was given up for now
        self._looked = time.monotonic()  # auto: when it was last looked whether it is installed
        if self.mode == "service" or (self.mode == "auto" and _service_installed()):
            self._start_service()
        else:
            self._start_others()

    def _start_others(self) -> None:
        """Inside Libre Panel where that is possible (and asked for), else the web server."""
        if self.mode != "webserver" and self._source is None:
            self._start_inprocess()
        if self._source is None and self.mode != "inprocess":
            self._start_web()

    # -- the sensor service -------------------------------------------------------------------

    def _start_service(self) -> None:
        from libre_panel.sensors import lhm_service

        source = lhm_service.ServiceSource(
            # twice a round of the service: no reading is skipped, none is late by more
            poll_seconds=self.poll_seconds / 2,
            stale_seconds=self.stale_seconds,
            gpu=self.gpu,
            on_ready=self._service_ready,
            on_failure=self._service_failed,
        )
        with self._web:
            if self._stop.is_set():  # closed meanwhile: nothing more to start
                return
            self._service = source
        source.start()

    def _look_for_service(self) -> None:
        """``auto`` without the service: was it installed meanwhile ("Unlock all sensors", the
        setup)? Then it takes over as soon as it answers. A look at the registry, now and then."""
        now = time.monotonic()
        if now - self._looked < SERVICE_LOOK_SECONDS:
            return
        self._looked = now
        if not _service_installed():
            return
        with self._web:
            if self._stop.is_set() or self._service is not None:
                return
            self._fallback = True  # what reads now goes on until the service answers
        log.info("the sensor service is installed now: it takes over as soon as it answers")
        self._start_service()

    def _service_failed(self, why: str) -> None:
        """The service has not answered for a while: in ``auto``, read the other ways until
        it does (its status says why, so the hints speak of the service). One that was
        removed is let go, as if it had never been there (and looked for again)."""
        if self.mode != "auto":
            return
        removed = not _service_installed()
        with self._web:
            if self._stop.is_set():
                return
            service = self._service
            if removed:
                self._service, self._fallback, self._silent = None, False, ""
                self._looked = time.monotonic()
            else:
                self._silent = why
                if self._fallback:
                    return
                self._fallback = True
        if removed:
            log.info("the sensor service was removed: LibreHardwareMonitor is read another way")
            service.close()  # (from its own thread: it ends when this returns)
        else:
            log.info(
                "the sensor service does not answer (%s): LibreHardwareMonitor is read another "
                "way until it does",
                why,
            )
        self._start_others()

    def _service_ready(self, status: LhmStatus) -> None:
        """The service answers (again): what read meanwhile stops."""
        with self._web:
            self._silent = ""
            if not self._fallback:
                return
            self._fallback = False
            source, self._source = self._source, None
        if source is not None:
            source.close()
        self._stop_web()
        log.info("the sensor service answers again: LibreHardwareMonitor is read through it")

    # -- inside Libre Panel -------------------------------------------------------------------

    def _start_inprocess(self) -> None:
        from libre_panel.sensors import lhm_inprocess

        folder, why = lhm_inprocess.availability(dll_dir_of(self.options))
        if folder is None:
            self._why = why
            if self.mode == "inprocess":
                log.warning("LibreHardwareMonitor cannot run inside Libre Panel: %s", why)
            else:  # not a fault: the web server is the way where it cannot be inside
                log.debug("LibreHardwareMonitor is read through its web server: %s", why)
            return
        self._source = lhm_inprocess.InProcessSource(
            functools.partial(lhm_inprocess.open_computer, folder),
            poll_seconds=self.poll_seconds,
            stale_seconds=self.stale_seconds,
            gpu=self.gpu,
            on_ready=self._source_ready,
            on_failure=self._source_failed,
        )
        self._source.start()

    def _source_ready(self, status: LhmStatus) -> None:
        """Without administrator rights (or PawnIO) the CPU's sensors stay out of reach. A
        LibreHardwareMonitor that runs as administrator with its web server has them: if
        that answers, its readings fill in what is missing."""
        if self.mode != "auto" or status.ring0 is not False:
            return
        try:
            self._fetch()
        except Exception as exc:  # noqa: BLE001 - not running, or not what it should be
            log.debug("no LibreHardwareMonitor web server at %s: %s", self.url, exc)
            return
        log.info(
            "LibreHardwareMonitor's web server answers at %s: its readings fill in "
            "what Libre Panel cannot read itself",
            self.url,
        )
        self._start_web()

    def _source_failed(self, why: str) -> None:
        self._why = why
        if self.mode == "auto":
            self._start_web()

    # -- the web server -----------------------------------------------------------------------

    def _start_web(self) -> None:
        with self._web:
            if self._thread is not None or self._stop.is_set():
                return
            self._web_off = threading.Event()
            self._thread = threading.Thread(
                target=self._poll, args=(self._web_off,), name="lhm-poll", daemon=True
            )
            self._thread.start()

    def _stop_web(self) -> None:
        """The poller ends at its next turn, and what it read is forgotten."""
        with self._web:
            if self._thread is None:
                return
            if self._web_off is not None:
                self._web_off.set()
            self._thread = None
        with self._lock:
            self._latest, self._answered_at = {}, None

    def _fetch(self) -> dict[str, Any]:
        return fetch_tree(self.url)

    def _poll(self, off: threading.Event | None = None) -> None:
        while not self._stop.is_set() and not (off is not None and off.is_set()):
            started = time.monotonic()
            delay = self._poll_once()
            self._polled.set()
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
        if self._service is not None:
            readings = self._service.read()
            if readings or not self._fallback:
                return readings
        elif self.mode == "auto":
            self._look_for_service()
        with self._lock:
            readings = dict(self._latest)
        if self._source is None:
            return readings
        try:
            inside = self._source.read()
        except Exception:  # noqa: BLE001 - a broken source must not stop the panel
            log.exception("LibreHardwareMonitor inside Libre Panel failed")
            return readings
        for key, reading in readings.items():  # the web server only fills in
            inside.setdefault(key, reading)
        return inside

    def status(self) -> LhmStatus:
        """How the readings come, and what keeps the CPU's from coming."""
        if self._service is not None:
            service = self._service.status()
            if not self._fallback or self._service.live():
                return service
            if self._silent:  # given up for now: the other ways read, and the hints say why
                return dataclasses.replace(self._other_status(), service=self._silent)
        return self._other_status()

    def _other_status(self) -> LhmStatus:
        if self._source is not None:
            status = self._source.status()
            if status.error and self.mode == "auto":  # it did not start: the web server it is
                return LhmStatus("webserver", error=status.error)
            return status
        if self.mode == "inprocess":  # inside only: it is not there, and that is the problem
            return LhmStatus("inprocess", error=self._why)
        # The Windows download carries what is needed: where it cannot be used the install is
        # broken. With pip the web server is the way (nothing to report).
        return LhmStatus("webserver", error=self._why if getattr(sys, "frozen", False) else "")

    def wait_ready(self, timeout: float) -> bool:
        """Whether the first readings are in (or it has given up) within ``timeout`` seconds.
        For a command that asks once and must not ask too early."""
        end = time.monotonic() + timeout
        if self._service is not None:
            if not self._service.wait_ready(timeout):
                return False
            if not self._fallback:
                return True
            timeout = max(0.0, end - time.monotonic())  # it gave up: the other ways get the rest
        source = self._source
        if source is not None and not source.wait_ready(timeout):
            return False
        if self._thread is None:
            return True
        return self._polled.wait(max(0.0, end - time.monotonic()))

    def close(self) -> None:
        with self._web:
            self._stop.set()
            if self._web_off is not None:
                self._web_off.set()
        if self._service is not None:
            self._service.close()  # its thread is the one that starts the other ways
        if self._source is not None:
            self._source.close()

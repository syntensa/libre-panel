"""LibreHardwareMonitor inside Libre Panel's own process (Windows).

On Windows the GPU, the temperatures, the power and the fans come only from
LibreHardwareMonitor. Instead of another program with its web server, Libre Panel loads
LibreHardwareMonitor's .NET library itself, through pythonnet, and reads its sensors in a
thread of its own. ``LibreHardwareMonitorProvider`` takes this way when it can
(``mode = "auto"``) and keeps asking the web server otherwise.

- **.NET Framework only.** LibreHardwareMonitor (0.9.6, 0.9.7) on CoreCLR stops in its mutex code
  (seen as ``ArgumentNullException: identity``), so pythonnet is told to start ``netfx``.
  Where pythonnet already runs on another runtime (a plugin loaded it first) this source
  steps aside.
- **The libraries** are the .NET Framework builds from NuGet that
  ``packaging/windows/fetch_lhm.py`` collects. The Windows downloads carry them in a folder
  ``lhm``; a pip install names its own folder as ``dll_dir``.
- **Administrator rights and PawnIO.** CPU temperature, power and clock, RAM temperature
  and the mainboard's fans are read through PawnIO, a ring0 driver that is installed
  separately and that only an administrator can talk to. Without them LibreHardwareMonitor
  gives 0.0 for these sensors; here they are left out ("--" on the panel), never shown as
  zero. The GPU, the loads, memory use and the network need neither.
- **Cost.** Updating every hardware node can take over ten milliseconds, and .NET holds
  Python's lock all the while. So only the nodes with a sensor that a theme on show reads
  (``wanted``) are updated, with a short pause between two of them for the renderer.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from libre_panel.sensors.base import Reading, wanted, wanted_keys
from libre_panel.sensors.librehardwaremonitor import (
    LhmSensor,
    LhmStatus,
    readings_from_sensors,
    ring0_steps,
)

log = logging.getLogger(__name__)

LIBRARY = "LibreHardwareMonitorLib"
# In the order they are loaded. pythonnet runs on the .NET Framework of this program, which
# has no binding redirects: a library has to find the very version it was built against
# already loaded, so the small System.* ones come first.
LIBRARIES = (
    "System.Memory",
    "System.Buffers",
    "System.Numerics.Vectors",
    "System.Runtime.CompilerServices.Unsafe",
    "BlackSharp.Core",  # without it no memory module is found, and no RAM temperature
    "RAMSPDToolkit-NDD",
    "DiskInfoToolkit",  # only for drives, which the sensor service opens (not this source)
    "HidSharp",  # only for USB controllers and power supplies, which the sensor service opens
    LIBRARY,
)
OPTIONAL = {"DiskInfoToolkit", "HidSharp"}
BUNDLED_FOLDER = "lhm"  # in the Windows downloads, next to the program's own files
PAUSE_SECONDS = 0.002  # between two hardware nodes of a round: the renderer gets its turn
# every so often a round reads all hardware, also what no theme reads now: sensors that
# LibreHardwareMonitor lists only once they have a value (a fan that starts) are found so
SURVEY_SECONDS = 30.0
READY_SECONDS = 20.0  # how long `sensors` and `doctor` wait for the first readings
PAWNIO_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"

# What LibreHardwareMonitor's web server writes after a value, by sensor type
_UNITS = {
    "voltage": "V",
    "current": "A",
    "power": "W",
    "clock": "MHz",
    "temperature": "°C",
    "load": "%",
    "frequency": "Hz",
    "fan": "RPM",
    "flow": "L/h",
    "control": "%",
    "level": "%",
    "factor": "",
    "data": "GB",
    "smalldata": "MB",
    "throughput": "B/s",
    "timespan": "s",
    "timing": "ns",
    "energy": "mWh",
    "noise": "dBA",
    "conductivity": "µS/cm",
    "humidity": "%",
}
# A working ring0 driver shows in one of these being above zero: a fan may stand still,
# a core's power may be nothing, but a temperature, a clock or a voltage are not
_PROBES = {"temperature", "clock", "power", "voltage"}

_LOAD_LOCK = threading.Lock()


class LhmUnavailable(Exception):
    """LibreHardwareMonitor cannot run inside Libre Panel here; the message says why."""


def needs_ring0(hardware_type: str, sensor_type: str) -> bool:
    """Whether LibreHardwareMonitor reads this sensor through its ring0 driver only:
    everything of a CPU but its load, the Super I/O chips and embedded controllers of the
    mainboard (fans, temperatures, voltages) and the temperature of a memory module."""
    hardware_type, sensor_type = hardware_type.lower(), sensor_type.lower()
    if hardware_type == "cpu":
        return sensor_type != "load"
    if hardware_type in ("superio", "embeddedcontroller"):
        return True
    return hardware_type == "memory" and sensor_type == "temperature"


# -- where the libraries are, and loading them -------------------------------------


def find_dll_dir(configured: str | None = None) -> Path | None:
    """The folder with LibreHardwareMonitorLib.dll: the ``dll_dir`` of the config, else the
    one that comes with the Windows download."""
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / BUNDLED_FOLDER)
    if getattr(sys, "frozen", False):
        program = Path(sys.executable).resolve().parent
        candidates += [program / BUNDLED_FOLDER, program / "_internal" / BUNDLED_FOLDER]
    for folder in candidates:
        if (folder / f"{LIBRARY}.dll").is_file():
            return folder
    return None


def _has_pythonnet() -> bool:
    try:
        return importlib.util.find_spec("pythonnet") is not None
    except (ImportError, ValueError):  # imported already, with no spec to speak of
        return sys.modules.get("pythonnet") is not None


def availability(
    dll_dir: str | None = None, platform: str | None = None
) -> tuple[Path | None, str]:
    """(the folder with the libraries, "") when LibreHardwareMonitor can be loaded here,
    else (None, why not). Cheap: nothing is loaded yet."""
    if (platform or sys.platform) != "win32":
        return None, "it runs on Windows only"
    if not _has_pythonnet():
        return None, 'pythonnet is not installed (pip install "libre-panel[lhm]")'
    folder = find_dll_dir(dll_dir)
    if folder is None:
        where = f" in {dll_dir}" if dll_dir else ""
        return None, (
            f"{LIBRARY}.dll was not found{where}; the Windows download has it, with pip "
            "set dll_dir under [sensors.librehardwaremonitor] to a folder with the libraries"
        )
    if dll_dir and folder != Path(dll_dir).expanduser():
        log.warning("dll_dir %s has no %s.dll: using %s", dll_dir, LIBRARY, folder)
    return folder, ""


def load_library(dll_dir: Path) -> Any:
    """Start pythonnet on the .NET Framework, load the libraries from ``dll_dir`` and give
    back LibreHardwareMonitor's ``Computer`` class. Raises LhmUnavailable."""
    with _LOAD_LOCK:
        try:
            import pythonnet
        except ImportError as exc:
            raise LhmUnavailable(
                'pythonnet is not installed (pip install "libre-panel[lhm]")'
            ) from exc
        try:
            pythonnet.load("netfx")  # returns at once if some runtime is loaded already
        except Exception as exc:  # noqa: BLE001 - whatever stopped the .NET Framework from starting
            raise LhmUnavailable(f"pythonnet could not start the .NET Framework: {exc}") from exc
        info = getattr(pythonnet, "get_runtime_info", lambda: None)()
        kind = str(getattr(info, "kind", "") or "")
        if info is not None and "framework" not in kind.lower():
            raise LhmUnavailable(
                f"pythonnet already runs on {kind or 'another runtime'}, "
                "but LibreHardwareMonitor needs the .NET Framework"
            )
        missing = [n for n in LIBRARIES if not (dll_dir / f"{n}.dll").is_file()]
        if LIBRARY in missing:
            raise LhmUnavailable(f"{LIBRARY}.dll is not in {dll_dir}")
        if wrong := [n for n in missing if n not in OPTIONAL]:
            log.warning("%s lacks %s: some sensors may be missing", dll_dir, ", ".join(wrong))
        if str(dll_dir) not in sys.path:
            sys.path.append(str(dll_dir))  # where pythonnet looks for what a library asks for
        import clr

        for name in LIBRARIES:
            path = dll_dir / f"{name}.dll"
            if path.is_file():
                try:
                    clr.AddReference(str(path))
                except Exception as exc:  # noqa: BLE001 - a .NET exception: wrong build, damaged
                    raise LhmUnavailable(f"{name}.dll did not load: {exc}") from exc
        try:
            from LibreHardwareMonitor.Hardware import Computer
        except ImportError as exc:
            raise LhmUnavailable(f"{LIBRARY}.dll has no Computer to open: {exc}") from exc
        try:  # 0.9.7 tells every amount of data in bytes; 0.9.6 told GB, and MB as SmallData
            from LibreHardwareMonitor.Hardware import SensorType
        except ImportError:
            pass
        else:
            _UNITS["data"] = "GB" if hasattr(SensorType, "SmallData") else "B"
        return Computer


def open_computer(dll_dir: Path) -> Any:
    """LibreHardwareMonitor's ``Computer``, open on CPU, GPU, memory, mainboard and network.
    Drives (they need one more library), USB controllers, power supplies and batteries are
    not opened."""
    computer = load_library(dll_dir)()
    computer.IsCpuEnabled = True
    computer.IsGpuEnabled = True
    computer.IsMemoryEnabled = True
    computer.IsMotherboardEnabled = True
    computer.IsNetworkEnabled = True
    computer.Open()
    return computer


def pawnio_version() -> str | None:
    """The version of the installed PawnIO driver ("" if it does not say), None where it
    is not installed. LibreHardwareMonitor looks in the same place."""
    try:
        import winreg
    except ImportError:
        return None
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, PAWNIO_KEY, 0, winreg.KEY_READ | view
            ) as key:
                try:
                    return str(winreg.QueryValueEx(key, "DisplayVersion")[0])
                except OSError:
                    return ""
        except OSError:
            continue
    return None


# -- what LibreHardwareMonitor has ------------------------------------------------------


@dataclass
class _Entry:
    """One sensor: the .NET object, and what it was last read as."""

    handle: Any
    sensor_id: str
    label: str
    sensor_type: str
    unit: str
    ring0: bool
    value: float | None = None


@dataclass
class _Node:
    """One piece of hardware (a CPU, a graphics card, a Super I/O chip): what is updated."""

    index: int
    handle: Any
    hardware_id: str
    name: str
    hardware_type: str
    entries: list[_Entry]
    keys: set[str] = field(default_factory=set)  # the friendly keys its sensors make
    failed: bool = False  # its Update() raised, and the log said so
    seen: int = -1  # how many sensors the hardware listed when they were last looked at

    @property
    def prefix(self) -> str:
        """Every raw key of this node starts with it."""
        return f"lhm:{self.hardware_id}/"

    def update(self) -> list[_Entry]:
        """Read the hardware; the sensors it lists now that it did not before come along and
        are returned. LibreHardwareMonitor lists some only once they have read a value: a
        Super I/O chip its fans, temperatures and voltages after its first update, a fan
        only once it turns (a graphics card's in its zero-RPM mode, a pump that starts late)."""
        self.handle.Update()
        return self.read()

    def read(self) -> list[_Entry]:
        """The values as the last update left them, with the sensors listed since."""
        new = self._grow()
        _forget_entries(new)
        for entry in self.entries:
            entry.value = _number(entry.handle.Value)
        return new

    def _grow(self) -> list[_Entry]:
        sensors = self.handle.Sensors
        try:
            count = len(sensors)
        except TypeError:  # not a sized list: look at it every time
            count = -2
        if count == self.seen:
            return []
        self.seen = count
        known = {entry.sensor_id for entry in self.entries}
        new = [
            entry
            for entry in (_entry(sensor, self.hardware_type) for sensor in sensors)
            if entry.sensor_id not in known
        ]
        self.entries.extend(new)
        return new


def _number(value: Any) -> float | None:
    """A sensor's value as a float; None where it has none (or no number)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _walk(hardware: Iterable[Any]) -> Iterator[Any]:
    for item in hardware:
        yield item
        yield from _walk(item.SubHardware)


def _entry(sensor: Any, hardware_type: str) -> _Entry:
    sensor_type = str(sensor.SensorType)
    return _Entry(
        sensor,
        str(sensor.Identifier),
        str(sensor.Name),
        sensor_type,
        _UNITS.get(sensor_type.lower(), ""),
        needs_ring0(hardware_type, sensor_type),
    )


def build_nodes(computer: Any) -> list[_Node]:
    """Every piece of hardware of an open ``Computer``, sub-hardware (the chips of a
    mainboard) included, with its sensors. Each is updated once first: LibreHardwareMonitor
    lists some sensors only after an update has read them (a Super I/O chip lists only its
    fan controls before, not its fans, temperatures and voltages). Asked once: it is slow,
    and pythonnet makes a new wrapper for every access; later ones come with
    :meth:`_Node.update`."""
    nodes: list[_Node] = []
    for hardware in _walk(computer.Hardware):
        hardware_type = str(hardware.HardwareType)
        try:
            hardware.Update()
        except Exception as exc:  # noqa: BLE001 - .NET exceptions; its update says so again
            log.debug("LibreHardwareMonitor: a first update failed (%s): %s", hardware_type, exc)
        node = _Node(
            len(nodes), hardware, str(hardware.Identifier), str(hardware.Name), hardware_type, []
        )
        node._grow()
        nodes.append(node)
    return nodes


def ring0_state(nodes: Iterable[_Node]) -> bool | None:
    """Whether the ring0 sensors give real values: True if one of them is above zero, False
    if they exist and all read zero (no administrator rights, no PawnIO), None if there
    are none (a virtual machine) or they have not been read."""
    probes = [e for n in nodes for e in n.entries if e.ring0 and e.sensor_type.lower() in _PROBES]
    if not probes or all(e.value is None for e in probes):
        return None
    return any(e.value is not None and e.value > 0 for e in probes)


def live_sensors(nodes: Iterable[_Node], ring0: bool | None) -> Iterator[LhmSensor]:
    """The sensors that have a value now. Where the ring0 driver does not work, what it
    should have read is zero: those sensors are left out, so the panel shows "--". The
    same goes for a chip temperature of zero or less when it does (a memory module whose
    sensor cannot be read says so): that is not a measurement. A fan may stand still."""
    for node in nodes:
        for e in node.entries:
            if e.value is None:
                continue
            if e.ring0 and e.value <= 0:
                if ring0 is False or e.sensor_type.lower() == "temperature":
                    continue
            yield LhmSensor(
                e.sensor_id, node.hardware_id, node.name, e.sensor_type, e.label, e.value, e.unit
            )


def _forget_history(nodes: Iterable[_Node]) -> None:
    """LibreHardwareMonitor keeps a day of values for every sensor (tens of megabytes for a
    few hundred sensors read once a second). Libre Panel keeps its own history."""
    _forget_entries([entry for node in nodes for entry in node.entries])


def _forget_entries(entries: list[_Entry]) -> None:
    """The same for these sensors (also those a node lists later)."""
    if not entries:
        return
    try:
        from System import TimeSpan
    except ImportError:
        return
    for entry in entries:
        try:
            entry.handle.ValuesTimeWindow = TimeSpan.Zero
        except Exception:  # noqa: BLE001 - not worth more than a line
            log.debug("could not switch a sensor's history off", exc_info=True)
            return


# -- the source --------------------------------------------------------------------------


class InProcessSource:
    """LibreHardwareMonitor's ``Computer``, opened and read by a thread of its own.

    ``opener`` returns the open computer (``open_computer`` for a folder with the libraries;
    tests give a stand-in). ``on_ready(status)`` runs once the first readings are in,
    ``on_failure(why)`` if the computer cannot be opened; both on the thread of this source.
    """

    def __init__(
        self,
        opener: Callable[[], Any],
        *,
        poll_seconds: float = 1.0,
        stale_seconds: float = 5.0,
        gpu: str | None = None,
        on_ready: Callable[[LhmStatus], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.poll_seconds = max(0.1, poll_seconds)
        # 0 or less keeps the last readings; a source slower than the limit must not hide
        self.stale_seconds = max(stale_seconds, 3 * self.poll_seconds) if stale_seconds > 0 else 0
        self.gpu = gpu
        self._opener = opener
        self._on_ready = on_ready
        self._on_failure = on_failure
        self._clock = clock
        self._nodes: list[_Node] = []
        self._node_of: dict[str, _Node] = {}  # sensor id -> node
        self._needed: tuple[Any, list[_Node]] | None = None  # for what was read, who was needed
        self._surveyed: float | None = None  # when a round last read all hardware
        self._learned = 0  # how many keys the nodes know: it grows when one is learned
        self._broken = False  # the last round failed, and the log said so
        self._latest: dict[str, Reading] = {}
        self._beat: float | None = None  # when the last round ended
        self._ring0: bool | None = None
        self._elevated: bool | None = None
        self._pawnio: str | None = None
        self._error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()  # the first readings are in, or it gave up
        self._thread = threading.Thread(target=self._run, name="lhm-inprocess", daemon=True)

    def start(self) -> None:
        self._thread.start()

    # -- the thread -------------------------------------------------------------------

    def _run(self) -> None:
        computer = None
        try:
            computer = self._opener()
            self._begin(computer)
        except LhmUnavailable as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - .NET exceptions come as all kinds
            log.debug("LibreHardwareMonitor did not start", exc_info=True)
            self._fail(f"{type(exc).__name__}: {exc}")
        else:
            self._announce()
            delay = self.poll_seconds
            while not self._stop.wait(delay):
                started = self._clock()
                self._refresh_guarded()
                delay = max(0.05, self.poll_seconds - (self._clock() - started))
        self._release(computer)

    def _fail(self, why: str) -> None:
        log.warning("LibreHardwareMonitor cannot run inside Libre Panel: %s", why)
        with self._lock:
            self._error = why
        self._call(self._on_failure, why)
        self._done.set()

    def _announce(self) -> None:
        status = self.status()
        count = sum(len(n.entries) for n in self._nodes)
        log.info("LibreHardwareMonitor runs inside Libre Panel (%d sensors)", count)
        if status.ring0 is False:
            log.info(
                "LibreHardwareMonitor: CPU temperature, power and clock are not available: %s",
                " and ".join(ring0_steps(status)) or "it finds nothing it supports",
            )
        self._call(self._on_ready, status)
        self._done.set()

    @staticmethod
    def _call(callback: Callable[[Any], None] | None, argument: Any) -> None:
        if callback is None:
            return
        try:
            callback(argument)
        except Exception:  # noqa: BLE001 - the provider's business; the thread goes on
            log.exception("LibreHardwareMonitor: a callback failed")

    def _release(self, computer: Any) -> None:
        if computer is None:
            return
        try:
            computer.Close()
        except Exception:  # noqa: BLE001 - .NET exceptions while closing change nothing now
            log.debug("LibreHardwareMonitor did not close cleanly", exc_info=True)

    def _refresh_guarded(self) -> None:
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 - one bad round must not end the thread
            level = (
                logging.DEBUG if self._broken else logging.WARNING
            )  # said once, not every second
            log.log(level, "LibreHardwareMonitor could not be read", exc_info=True)
            self._broken = True
        else:
            self._broken = False

    # -- reading ------------------------------------------------------------------------

    def _begin(self, computer: Any) -> None:
        """What the open computer has, read once in full (so that every key is known)."""
        from libre_panel.autostart import is_admin

        self._nodes = build_nodes(computer)
        self._node_of = {e.sensor_id: n for n in self._nodes for e in n.entries}
        # which node makes which key, from all sensors, also those without a value yet
        self._learn_keys(
            LhmSensor(e.sensor_id, n.hardware_id, n.name, e.sensor_type, e.label, 0.0, e.unit)
            for n in self._nodes
            for e in n.entries
        )
        _forget_history(self._nodes)
        for node in self._nodes:
            self._update(node, fresh=False)  # build_nodes has just updated it
        self._surveyed = self._clock()
        with self._lock:
            self._elevated, self._pawnio = is_admin(), pawnio_version()
        self._publish()

    def _learn_keys(self, sensors: Iterable[LhmSensor]) -> dict[str, Reading]:
        """The readings of these sensors; each node learns the friendly keys its sensors
        make. It never forgets one: a sensor without a value now still belongs to it."""
        sources: dict[str, tuple[str, ...]] = {}
        readings = readings_from_sensors(sensors, self.gpu, sources)
        for key, ids in sources.items():
            if not key.startswith("lhm:"):
                for sensor_id in ids:
                    node = self._node_of.get(sensor_id)
                    if node is not None and key not in node.keys:
                        node.keys.add(key)
                        self._learned += 1
        return readings

    def _update(self, node: _Node, fresh: bool = True) -> None:
        """Update ``node`` and read it (``fresh`` False: only read what build_nodes' update
        left, as the first round after opening does)."""
        try:
            new = node.update() if fresh else node.read()
        except Exception as exc:  # noqa: BLE001 - LibreHardwareMonitor raises .NET exceptions
            if not node.failed:
                node.failed = True
                # the kind of part, not its name (the log goes into issues)
                log.warning(
                    "LibreHardwareMonitor could not update a part (%s): %s", node.hardware_type, exc
                )
            return
        node.failed = False
        for entry in new:  # sensors it lists now: their keys are learned when published
            self._node_of[entry.sensor_id] = node

    def needed(self) -> list[_Node]:
        """The nodes with a sensor that a theme on show reads: by raw key, or by the
        friendly key (``cpu.temp``, ``temp.gpu.gpu_core``) one of its sensors makes. Asking
        ``wanted`` for every key takes milliseconds, so it is done again only when what
        themes read, or what the nodes make, has changed."""
        state = (wanted_keys(), self._learned)
        if self._needed is None or self._needed[0] != state:
            nodes = [
                n
                for n in self._nodes
                if n.entries and (wanted(n.prefix) or any(wanted(k) for k in n.keys))
            ]
            self._needed = (state, nodes)
        return self._needed[1]

    def refresh(self) -> None:
        """One round: update what is read, then make the readings. Now and then a round reads
        all hardware (SURVEY_SECONDS), so that sensors that appear later are found."""
        now = self._clock()
        if self._surveyed is None or now - self._surveyed >= SURVEY_SECONDS:
            self._surveyed = now
            nodes = list(self._nodes)
        else:
            nodes = self.needed()
        for position, node in enumerate(nodes):
            if position:
                self._stop.wait(PAUSE_SECONDS)  # the renderer gets the GIL between two
            self._update(node)
        if nodes:
            self._publish()
        else:  # nothing is read: the source is alive, its readings are what they were
            with self._lock:
                self._beat = self._clock()

    def _publish(self) -> None:
        ring0 = ring0_state(self._nodes)
        readings = self._learn_keys(live_sensors(self._nodes, ring0))
        with self._lock:
            self._latest = readings
            self._ring0 = ring0
            self._beat = self._clock()

    # -- for the provider ---------------------------------------------------------------

    def read(self) -> dict[str, Reading]:
        with self._lock:
            if self._beat is None or (
                self.stale_seconds > 0 and self._clock() - self._beat > self.stale_seconds
            ):
                return {}  # not read yet, or the thread stopped: not numbers that stand still
            return dict(self._latest)

    def status(self) -> LhmStatus:
        with self._lock:
            return LhmStatus("inprocess", self._elevated, self._ring0, self._pawnio, self._error)

    def wait_ready(self, timeout: float) -> bool:
        """Whether the first readings are in (or it gave up) within ``timeout`` seconds."""
        return self._done.wait(timeout)

    def close(self) -> None:
        """Stop; the thread closes the computer, so nothing is closed while it reads."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(10)
        self._done.set()


# -- `libre-panel sensors --check-lhm` --------------------------------------------------


def check(
    dll_dir: str | None = None,
    platform: str | None = None,
    opener: Callable[[Path], Any] | None = None,
) -> tuple[bool, list[str]]:
    """Whether LibreHardwareMonitor loads and opens inside Libre Panel, and what it says
    on the way: (worked, lines to print). The CPU's ring0 sensors being out of reach for
    want of rights is no failure here."""
    folder, why = availability(dll_dir, platform)
    if folder is None:
        return False, [f"LibreHardwareMonitor cannot run inside Libre Panel: {why}"]
    lines = [f"LibreHardwareMonitor libraries: {folder}"]
    have = [n for n in LIBRARIES if (folder / f"{n}.dll").is_file()]
    lines.append(f"libraries found: {len(have)} of {len(LIBRARIES)}")
    computer = None
    try:
        computer = (opener or open_computer)(folder)
        nodes = build_nodes(computer)
        for node in nodes:
            try:
                node.update()
            except Exception:  # noqa: BLE001 - one chip failing is not the question
                log.debug("could not update a part (%s)", node.hardware_type, exc_info=True)
        state = ring0_state(nodes)
    except Exception as exc:  # noqa: BLE001 - .NET exceptions come as all kinds
        lines.append(f"could not be opened: {type(exc).__name__}: {exc}")
        return False, lines
    finally:
        if computer is not None:
            try:
                computer.Close()
            except Exception:  # noqa: BLE001
                log.debug("LibreHardwareMonitor did not close cleanly", exc_info=True)
    from libre_panel.autostart import is_admin

    pawnio = pawnio_version()
    lines += [
        f"opened: {len(nodes)} devices with {sum(len(n.entries) for n in nodes)} sensors",
        f"administrator rights: {'yes' if is_admin() else 'no'}",
        f"PawnIO driver: {'not installed' if pawnio is None else pawnio or 'installed'}",
        "CPU temperature, power and clock: "
        + {True: "available", False: "not available", None: "no such sensors"}[state],
    ]
    return True, lines

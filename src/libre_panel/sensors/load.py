"""CPU, core and GPU load ten times a second, with no LibreHardwareMonitor and no admin rights.

Other sources give the load once a second (LibreHardwareMonitor) or only when
the panel asks (psutil), so bars and graphs run behind what the computer does.
This one measures in a background thread ten times a second and is read on
every frame (``every_frame``). Put it before the other sources in
``[sensors] providers``: the first source with a value for a key wins.

- ``cpu.load``: on Windows ``GetSystemTimes`` (idle time against total time
  between two samples, about 60 µs a sample), elsewhere ``psutil.cpu_percent``.
- ``cpu.core.N.load``: every core's load over the last 0.3 s (one sample alone
  flickers), from psutil's times per core, under the names psutil's source gives.
- ``cpu.pcore.N.load``: every physical core's, the mean of its threads (where a core has
  two, and Windows or Linux tells which: sensors/topology.py).
- ``gpu.load`` (Windows, any vendor): the performance counters that Task
  Manager shows, read through PDH. There is one 3D engine counter per process
  and engine; they are summed per engine, and the card's load is its busiest
  engine. The card is the one with the most dedicated memory in use (the
  discrete one, even when idle), chosen again every minute.
- ``gpu.load`` (Linux): AMD's own counter in sysfs (``gpu_busy_percent``) and NVIDIA's
  library (NVML), for the card ``gpu.name`` names. Intel's drivers tell no load.

The thread runs only while a theme on show reads one of the keys, and ends
when the provider closes. Where a source cannot measure (no counters, a
virtual machine) the key is simply missing and the next source supplies it; the
log says so once, and the thread tries again every minute.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from libre_panel.sensors import gpus, nvml, topology
from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 0.1  # ten samples a second
ADAPTER_SECONDS = 60.0  # the graphics card is chosen again every minute
STALE_SECONDS = 2.0  # a reading older than this is dropped, not shown frozen
IDLE_SECONDS = 60.0  # nobody looked for this long: the thread ends, and starts on demand
RETRY_SECONDS = 60.0  # a sampler that could not be made is tried again after this long
CORE_SECONDS = 0.3  # a core's load is its load over this long

# The labels PsutilProvider gives cpu.load, so lists and themes look the same.
_LABELS = {"cpu.load": "CPU load", "gpu.load": "GPU load"}
CORES = "cpu.core."  # every core's load: cpu.core.1.load, cpu.core.2.load ...
PCORES = "cpu.pcore."  # every physical core's (its threads'), measured with them
_KEYS = (*_LABELS, CORES)
_NO_SAMPLER = {
    "cpu.load": "no CPU load from psutil",
    "gpu.load": "no GPU load from the performance counters",
    CORES: "no load per core from psutil",
}

_ENGINE_COUNTER = r"\GPU Engine(*engtype_3D)\Utilization Percentage"
_MEMORY_COUNTER = r"\GPU Adapter Memory(*)\Dedicated Usage"
_PDH_FMT_DOUBLE = 0x200
_PDH_MORE_DATA = 0x800007D2


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _CounterValue(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE: a status, then a union that starts on an 8-byte boundary
    _fields_ = [("status", ctypes.c_uint32), ("pad", ctypes.c_uint32), ("value", ctypes.c_double)]


class _CounterItem(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE_ITEM_W: the instance's name and its value
    _fields_ = [("name", ctypes.c_wchar_p), ("counter", _CounterValue)]


# -- what the numbers mean (no Windows needed) -----------------------------------


def cpu_load(
    before: tuple[int, int, int] | None, after: tuple[int, int, int] | None
) -> float | None:
    """The load in percent between two ``GetSystemTimes`` samples (idle, kernel, user)."""
    if before is None or after is None:
        return None
    idle, kernel, user = (new - old for old, new in zip(before, after, strict=True))
    total = kernel + user  # the kernel time includes the idle time
    if total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1.0 - idle / total)))


def _luid(name: str) -> str | None:
    """``luid_0x00000000_0x0000D2E4``: what names the card in a PDH instance name."""
    start = name.find("luid_")
    return name[start:].split("_phys")[0] if start >= 0 else None


def pick_adapter(memory: list[tuple[str, float]]) -> str | None:
    """The card with the most dedicated memory in use, from ``Dedicated Usage`` instances."""
    used: dict[str, float] = {}
    for name, value in memory:
        luid = _luid(name)
        if luid:
            used[luid] = used.get(luid, 0.0) + value
    return max(used, key=used.__getitem__) if used else None


def adapter_load(adapter: str, engines: list[tuple[str, float]]) -> float:
    """A card's load: its busiest engine, each engine summed over the processes using it."""
    per_engine: dict[str, float] = {}
    for name, value in engines:
        if _luid(name) == adapter:
            engine = name[name.index("luid_") :]  # without the process: ..._phys_0_eng_0_...
            per_engine[engine] = per_engine.get(engine, 0.0) + value
    return max(0.0, min(100.0, max(per_engine.values(), default=0.0)))


# -- the samplers ------------------------------------------------------------------


class _Sampler(Protocol):
    def read(self) -> float | list[float] | None: ...
    def close(self) -> None: ...


class _PsutilCpu:
    """``psutil.cpu_percent`` since its last call. psutil (5.9.6 and later) keeps that
    call per thread, so this thread does not disturb PsutilProvider's reading."""

    def __init__(self) -> None:
        import psutil

        self._cpu_percent = psutil.cpu_percent
        self._cpu_percent(interval=None)  # the first call has nothing to compare with

    def read(self) -> float | None:
        return self._cpu_percent(interval=None)

    def close(self) -> None:
        pass


def _busy(times: Any) -> tuple[float, float]:
    """A core's busy and total time from psutil's times of it (as ``cpu_percent`` counts)."""
    total = sum(times) - getattr(times, "guest", 0.0) - getattr(times, "guest_nice", 0.0)
    return total - times.idle - getattr(times, "iowait", 0.0), total


class _Cores:
    """Every core's load over the last :data:`CORE_SECONDS`, from psutil's times per core
    (``cpu_times`` keeps nothing between calls: no other reading is disturbed)."""

    def __init__(self, times: Callable[[], list[Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        if times is None:
            import psutil

            def times() -> list[Any]:
                return psutil.cpu_times(percpu=True)

        self._times, self._clock = times, clock
        self._past: deque[tuple[float, list[tuple[float, float]]]] = deque()
        self._past.append((clock(), [_busy(t) for t in times()]))

    def read(self) -> list[float] | None:
        now, sample = self._clock(), [_busy(t) for t in self._times()]
        past = self._past
        past.append((now, sample))
        while len(past) > 2 and now - past[1][0] >= CORE_SECONDS:
            past.popleft()  # (the oldest sample that is at least CORE_SECONDS old stays)
        before = past[0][1]
        if len(before) != len(sample):  # a core more or less: start again
            past.clear()
            past.append((now, sample))
            return None
        loads = []
        for (busy0, total0), (busy1, total1) in zip(before, sample, strict=True):
            spent = total1 - total0
            loads.append(100.0 * (busy1 - busy0) / spent if spent > 0 else 0.0)
        return loads

    def close(self) -> None:
        pass


def _system_times(kernel32: Any) -> tuple[int, int, int] | None:
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return (
        (idle.high << 32) | idle.low,
        (kernel.high << 32) | kernel.low,
        (user.high << 32) | user.low,
    )


class _WindowsCpu:
    """The load between two calls, from ``GetSystemTimes``."""

    def __init__(self, kernel32: Any) -> None:
        self._kernel32 = kernel32
        self._last = _system_times(kernel32)

    def read(self) -> float | None:
        now = _system_times(self._kernel32)
        last, self._last = self._last, now
        return cpu_load(last, now)

    def close(self) -> None:
        pass


class _PdhGpu:
    """The load of the graphics card in use, from the counters Task Manager shows.

    ``pdh`` is the loaded pdh.dll. Raises OSError when the counters do not exist
    (older Windows, some virtual machines).
    """

    def __init__(self, pdh: Any, clock: Callable[[], float] = time.monotonic) -> None:
        self._pdh = pdh
        self._clock = clock
        self._adapter: str | None = None
        self._chosen_at = 0.0
        self._query: ctypes.c_void_p | None = None
        query = ctypes.c_void_p()
        status = pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
        if status:
            raise OSError(f"PdhOpenQuery failed ({status:#x})")
        counters = []
        for path in (_ENGINE_COUNTER, _MEMORY_COUNTER):
            counter = ctypes.c_void_p()
            status = pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter))
            if status:
                pdh.PdhCloseQuery(query)
                raise OSError(f"PdhAddEnglishCounter {path} failed ({status:#x})")
            counters.append(counter)
        pdh.PdhCollectQueryData(query)  # a load needs two samples
        self._query = query
        self._engines, self._memory = counters

    def _instances(self, counter: ctypes.c_void_p) -> list[tuple[str, float]] | None:
        """Every instance of a wildcard counter with its value; None if PDH has none now."""
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None
        )
        if status != _PDH_MORE_DATA or not size.value:
            return None
        buffer = (ctypes.c_byte * size.value)()
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buffer
        )
        if status:
            return None  # the processes changed between the two calls: the next sample
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))
        # Status 0 and 1 are valid values; a process that is just ending reports another.
        return [
            (items[i].name or "", items[i].counter.value)
            for i in range(count.value)
            if items[i].counter.status in (0, 1)
        ]

    def read(self) -> float | None:
        if self._query is None or self._pdh.PdhCollectQueryData(self._query):
            return None
        now = self._clock()
        if self._adapter is None or now - self._chosen_at > ADAPTER_SECONDS:
            adapter = pick_adapter(self._instances(self._memory) or [])
            if adapter:
                self._adapter, self._chosen_at = adapter, now
        if self._adapter is None:
            return None
        engines = self._instances(self._engines)
        return None if engines is None else adapter_load(self._adapter, engines)

    def close(self) -> None:
        if self._query is not None:
            self._pdh.PdhCloseQuery(self._query)
            self._query = None


class _LinuxGpu:
    """The load of the graphics card in use: AMD's counter, or NVIDIA's library for the card.
    A card that is powered down (a laptop's second one) is not asked: that would switch it on."""

    def __init__(self, card: gpus.Card, device: nvml.Device | None = None) -> None:
        self._card = card
        self._device = device

    def read(self) -> float | None:
        if gpus.suspended(self._card):
            return None
        if self._device is not None:
            return self._device.utilization()
        return gpus.busy_percent(self._card)

    def close(self) -> None:
        pass


def _linux_gpu(root: Path = Path("/"), prefer: str = "") -> _Sampler | None:
    """The sampler for the card ``gpu.name`` names (``prefer``: a part of the name picks
    another), or None where no card tells its load: Intel's, the open drivers, a PC without."""
    from libre_panel.sensors import hardware

    cards = gpus.find_cards(root)
    card = gpus.primary(cards, hardware.scan().adapters, prefer)
    if card is None or gpus.suspended(card):
        return None
    if card.vendor == gpus.NVIDIA and card.driver == "nvidia":
        library = nvml.shared()
        device = None
        if library is not None:
            device = library.by_slot(card.slot) if card.slot else None
            device = device or library.by_index(0)
        return None if device is None else _LinuxGpu(card, device)
    if card.driver == "amdgpu" and (card.device / "gpu_busy_percent").exists():
        return _LinuxGpu(card)
    return None


def _cpu_sampler(platform: str = sys.platform) -> _Sampler:
    """The CPU sampler of this machine; raises if it has none."""
    if platform == "win32":
        try:
            return _WindowsCpu(ctypes.WinDLL("kernel32"))
        except Exception as exc:  # noqa: BLE001 - psutil does it as well, a little slower
            log.debug("load: GetSystemTimes is not available (%s)", exc)
    return _PsutilCpu()


def _load_pdh() -> Any:
    pdh = ctypes.WinDLL("pdh")
    for name in (
        "PdhOpenQueryW",
        "PdhAddEnglishCounterW",
        "PdhCollectQueryData",
        "PdhGetFormattedCounterArrayW",
        "PdhCloseQuery",
    ):
        getattr(pdh, name).restype = ctypes.c_uint32  # PDH_STATUS, compared as unsigned
    return pdh


def _gpu_sampler(platform: str = sys.platform, prefer: str = "") -> _Sampler | None:
    """The GPU sampler of this machine. None where it cannot have one (macOS, a Linux card
    that tells no load); raises where it should and does not (Windows: no counters yet)."""
    if platform.startswith("linux"):
        return _linux_gpu(prefer=prefer)
    if platform != "win32":
        return None
    return _PdhGpu(_load_pdh())


def _open(key: str, prefer: str = "") -> _Sampler | None:
    if key == "cpu.load":
        return _cpu_sampler()
    if key == CORES:
        return _Cores()
    return _gpu_sampler(prefer=prefer) if prefer else _gpu_sampler()


def _measure(sampler: _Sampler) -> float | list[float] | None:
    try:
        value = sampler.read()
    except Exception:  # noqa: BLE001 - one failed sample is no reason to stop
        log.debug("load: a sample failed", exc_info=True)
        return None
    if isinstance(value, list):  # one per core
        return [max(0.0, min(100.0, float(v))) for v in value]
    return None if value is None else max(0.0, min(100.0, float(value)))


def _family(key: str) -> str:
    """The key a reading is measured under (every core's under :data:`CORES`)."""
    return CORES if key.startswith((CORES, PCORES)) else key


def _wanted(key: str) -> bool:
    """Whether a theme on show reads ``key``'s readings (the cores' with their threads')."""
    return wanted(key) or (key == CORES and wanted(PCORES))


# -- the provider ------------------------------------------------------------------


class LoadProvider(SensorProvider):
    """``cpu.load``, ``cpu.core.N.load`` and ``gpu.load``, measured ten times a second while a
    theme shows them."""

    name = "load"
    # In video mode the bars follow the load on every frame; on the PNG path each
    # change would be one more picture to send, so it keeps to the readings there.
    every_frame = "video"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._values: dict[str, tuple[float, Reading]] = {}  # key -> (when measured, reading)
        self._said: set[str] = set()  # keys that were said to have no sampler
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prefer = ""  # a part of the name of the graphics card to measure (Linux)
        self._chose_again = threading.Event()  # the card to measure was changed
        self._cores: tuple[int, list[list[int]] | None] | None = None  # (threads, their cores)

    def follow_config(self, config: Any) -> None:
        """The card the ``gpu.*`` readings come from is the one ``[sensors.linux] gpu`` (or, as
        everywhere, ``[sensors.librehardwaremonitor] gpu``) names; the load is that card's."""
        tables = config.sensors.options
        prefer = ""
        for table in ("linux", "librehardwaremonitor"):
            found = tables.get(table, {}).get("gpu")
            prefer = prefer or (found.strip() if isinstance(found, str) else "")
        if prefer != self._prefer:
            self._prefer = prefer
            self._chose_again.set()

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="sensors-load", daemon=True)
            self._thread.start()

    def _store(self, key: str, value: float | list[float], at: float) -> None:
        with self._lock:
            if isinstance(value, list):  # every core's, named as PsutilProvider names them
                for n, load in enumerate(value, start=1):
                    core = f"{CORES}{n}.load"
                    self._values[core] = (at, Reading(core, load, "%", f"Core {n}"))
                for n, threads in enumerate(self._physical(len(value)) or [], start=1):
                    core = f"{PCORES}{n}.load"
                    mean = sum(value[t] for t in threads) / len(threads)
                    self._values[core] = (at, Reading(core, mean, "%", f"Core {n}"))
            else:
                self._values[key] = (at, Reading(key, value, "%", _LABELS[key]))

    def _physical(self, threads: int) -> list[list[int]] | None:
        """The physical cores of the ``threads`` measured (asked once)."""
        if self._cores is None or self._cores[0] != threads:
            self._cores = (threads, topology.physical_cores(threads))
        return self._cores[1]

    def _make(self, key: str) -> _Sampler | None:
        """A new sampler for ``key``; None if this machine has none (said once)."""
        try:
            return _open(key, self._prefer) if key == "gpu.load" else _open(key)
        except Exception as exc:  # noqa: BLE001 - never raise
            what = _NO_SAMPLER[key]
            if key in self._said:
                log.debug("load: %s (%s)", what, exc)
            else:
                self._said.add(key)
                log.info("load: %s (%s)", what, exc)
            return None

    def _run(self) -> None:
        samplers: dict[str, _Sampler] = {}
        retry_at: dict[str, float] = {}  # keys without a sampler, and when to try again
        looked = time.monotonic()
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                shown = [key for key in _KEYS if _wanted(key)]
                if shown:
                    looked = now
                elif now - looked > IDLE_SECONDS:
                    return
                if self._chose_again.is_set():  # another card is to be measured
                    self._chose_again.clear()
                    gone = samplers.pop("gpu.load", None)
                    retry_at.pop("gpu.load", None)
                    if gone is not None:
                        gone.close()
                for key in shown:
                    if now < retry_at.get(key, 0.0):
                        continue
                    sampler = samplers.get(key)
                    if sampler is None:
                        sampler = self._make(key)
                        if sampler is None:
                            retry_at[key] = now + RETRY_SECONDS
                        else:
                            samplers[key] = sampler  # it has nothing to compare with yet
                        continue
                    value = _measure(sampler)
                    if value is not None:
                        self._store(key, value, now)
                with self._lock:  # what no theme shows any more goes
                    for key in [key for key in self._values if _family(key) not in shown]:
                        del self._values[key]
                self._stop.wait(SAMPLE_SECONDS)
        finally:
            with self._lock:
                self._values.clear()
            for sampler in samplers.values():
                try:
                    sampler.close()
                except Exception:  # noqa: BLE001 - nothing left to do about it
                    log.debug("load: closing a sampler failed", exc_info=True)

    def read(self) -> dict[str, Reading]:
        thread = self._thread
        if thread is None or not thread.is_alive():
            if self._stop.is_set() or not any(_wanted(key) for key in _KEYS):
                return {}
            self._start()
        now = time.monotonic()
        with self._lock:
            return {
                key: reading
                for key, (at, reading) in self._values.items()
                if now - at <= STALE_SECONDS
            }

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)

"""CPU and GPU load ten times a second, with no LibreHardwareMonitor and no admin rights.

Other sources give the load once a second (LibreHardwareMonitor) or only when
the panel asks (psutil), so bars and graphs run behind what the computer does.
This one measures in a background thread ten times a second and is read on
every frame (``every_frame``). Put it before the other sources in
``[sensors] providers``: the first source with a value for a key wins.

- ``cpu.load``: on Windows ``GetSystemTimes`` (idle time against total time
  between two samples, about 60 µs a sample), elsewhere ``psutil.cpu_percent``.
- ``gpu.load`` (Windows only, any vendor): the performance counters that Task
  Manager shows, read through PDH. There is one 3D engine counter per process
  and engine; they are summed per engine, and the card's load is its busiest
  engine. The card is the one with the most dedicated memory in use (the
  discrete one, even when idle), chosen again every minute.

The thread runs only while a theme on show reads one of the keys, and ends
when the provider closes. Where a source cannot measure (no counters, a
virtual machine) the key is simply missing and the next source supplies it.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 0.1  # ten samples a second
ADAPTER_SECONDS = 60.0  # the graphics card is chosen again every minute
STALE_SECONDS = 2.0  # a reading older than this is dropped, not shown frozen
IDLE_SECONDS = 60.0  # nobody looked for this long: the thread ends, and starts on demand

# The labels PsutilProvider gives cpu.load, so lists and themes look the same.
_LABELS = {"cpu.load": "CPU load", "gpu.load": "GPU load"}

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
    def read(self) -> float | None: ...
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


def _cpu_sampler(platform: str = sys.platform) -> _Sampler | None:
    """The CPU sampler of this machine."""
    if platform == "win32":
        try:
            return _WindowsCpu(ctypes.WinDLL("kernel32"))
        except Exception as exc:  # noqa: BLE001 - psutil does it as well, a little slower
            log.debug("load: GetSystemTimes is not available (%s)", exc)
    try:
        return _PsutilCpu()
    except Exception as exc:  # noqa: BLE001 - no load from here; the next source has it
        log.info("load: no CPU load from psutil (%s)", exc)
        return None


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


def _gpu_sampler(platform: str = sys.platform) -> _Sampler | None:
    """The GPU sampler of this machine; only Windows has the counters."""
    if platform != "win32":
        return None
    try:
        return _PdhGpu(_load_pdh())
    except Exception as exc:  # noqa: BLE001 - no GPU load from here; the next source has it
        log.info("load: no GPU load from the performance counters (%s)", exc)
        return None


def _open(key: str) -> _Sampler | None:
    return _cpu_sampler() if key == "cpu.load" else _gpu_sampler()


def _measure(sampler: _Sampler) -> float | None:
    try:
        value = sampler.read()
    except Exception:  # noqa: BLE001 - one failed sample is no reason to stop
        log.debug("load: a sample failed", exc_info=True)
        return None
    return None if value is None else max(0.0, min(100.0, float(value)))


# -- the provider ------------------------------------------------------------------


class LoadProvider(SensorProvider):
    """``cpu.load`` and ``gpu.load``, measured ten times a second while a theme shows them."""

    name = "load"
    every_frame = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._values: dict[str, tuple[float, Reading]] = {}  # key -> (when measured, reading)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="sensors-load", daemon=True)
            self._thread.start()

    def _store(self, key: str, value: float, at: float) -> None:
        with self._lock:
            self._values[key] = (at, Reading(key, value, "%", _LABELS[key]))

    def _run(self) -> None:
        samplers: dict[str, _Sampler] = {}
        missing: set[str] = set()  # keys this machine cannot measure
        looked = time.monotonic()
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                shown = [key for key in _LABELS if wanted(key)]
                if shown:
                    looked = now
                elif now - looked > IDLE_SECONDS:
                    return
                for key in shown:
                    if key in missing:
                        continue
                    sampler = samplers.get(key)
                    if sampler is None:
                        try:
                            sampler = _open(key)
                        except Exception:  # noqa: BLE001 - never raise
                            log.debug("load: %s could not be opened", key, exc_info=True)
                        if sampler is None:
                            missing.add(key)
                        else:
                            samplers[key] = sampler  # it has nothing to compare with yet
                        continue
                    value = _measure(sampler)
                    if value is not None:
                        self._store(key, value, now)
                with self._lock:  # what no theme shows any more goes
                    for key in [key for key in self._values if key not in shown]:
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
            if self._stop.is_set() or not (wanted("cpu.load") or wanted("gpu.load")):
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

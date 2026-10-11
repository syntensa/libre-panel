"""NVIDIA's management library (NVML), loaded from the driver with ctypes.

The proprietary driver registers no hwmon device and keeps its numbers to itself, but it brings
``libnvidia-ml.so.1``: temperature, fan, power, load, memory and clocks of the card, to anyone,
without rights. This is the same few calls as pynvml makes, without that package. The library
is found where the loader looks and in the usual folders; where there is none (a PC without an
NVIDIA card, the open nouveau driver) there is simply nothing.

``Nvml`` takes the loaded library, so that a test can give it an object with the same functions.
Every call that fails (a card that has no fan, an older driver without a function) leaves its
reading out. Nothing here starts a thread or keeps a card awake: asking a card that is powered
down wakes it, so the caller asks only cards that are on (``gpus.suspended``).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

LIBRARY = "libnvidia-ml.so.1"
# Where a driver that is not in the loader's cache keeps it (NixOS, WSL, 64-bit Debian, Fedora).
FOLDERS = (
    "/run/opengl-driver/lib",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib64",
    "/usr/lib",
    "/usr/lib/wsl/lib",
)
RETRY_SECONDS = 60.0  # a library that did not start is tried again after this long
MIB = 1024 * 1024

SUCCESS = 0
TEMPERATURE_GPU = 0  # nvmlTemperatureSensors_t
CLOCK_GRAPHICS, CLOCK_MEMORY = 0, 2  # nvmlClockType_t


class _Utilization(ctypes.Structure):  # nvmlUtilization_t
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Memory(ctypes.Structure):  # nvmlMemory_t
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class _FanSpeed(ctypes.Structure):  # nvmlFanSpeedInfo_t: asked with its version (size | 1 << 24)
    _fields_ = [("version", ctypes.c_uint), ("fan", ctypes.c_uint), ("speed", ctypes.c_uint)]


_FAN_SPEED_VERSION = ctypes.sizeof(_FanSpeed) | (1 << 24)


class Device:
    """One NVIDIA card. ``read`` gives what it can tell, in the units people use."""

    def __init__(self, nvml: Nvml, handle: Any) -> None:
        self._nvml = nvml
        self._handle = handle

    def _get(self, function: str, kind: Callable[[], Any], *before: Any) -> Any | None:
        """The value ``function`` writes into a new ``kind()``, or None when it says no."""
        value = kind()
        if self._nvml.call(function, self._handle, *before, ctypes.byref(value)) != SUCCESS:
            return None
        return value

    def utilization(self) -> float | None:
        """The card's load in percent: the share of the last sample period it was busy."""
        found = self._get("nvmlDeviceGetUtilizationRates", _Utilization)
        return None if found is None else float(found.gpu)

    def read(self) -> dict[str, float]:
        """``temp`` (°C), ``fan_percent`` and ``fan_rpm``, ``power`` (W), ``load`` (%),
        ``mem_used`` and ``mem_total`` (MB), ``mem_load`` (%), ``freq`` and ``mem_freq`` (MHz);
        those the card or the driver does not give are not there."""
        values: dict[str, float] = {}
        temp = self._get("nvmlDeviceGetTemperature", ctypes.c_uint, TEMPERATURE_GPU)
        if temp is not None:
            values["temp"] = float(temp.value)
        fan = self._get("nvmlDeviceGetFanSpeed", ctypes.c_uint)
        if fan is not None:
            values["fan_percent"] = float(fan.value)
        rpm = self._rpm()
        if rpm is not None:
            values["fan_rpm"] = rpm
        power = self._get("nvmlDeviceGetPowerUsage", ctypes.c_uint)  # milliwatts
        if power is not None:
            values["power"] = power.value / 1000.0
        load = self.utilization()
        if load is not None:
            values["load"] = load
        memory = self._get("nvmlDeviceGetMemoryInfo", _Memory)
        if memory is not None and memory.total:
            values["mem_used"] = memory.used / MIB
            values["mem_total"] = memory.total / MIB
            values["mem_load"] = 100.0 * memory.used / memory.total
        for name, clock in (("freq", CLOCK_GRAPHICS), ("mem_freq", CLOCK_MEMORY)):
            found = self._get("nvmlDeviceGetClockInfo", ctypes.c_uint, clock)
            if found is not None:
                values[name] = float(found.value)
        return values

    def _rpm(self) -> float | None:
        """The fan's speed in RPM, from drivers that tell it (555 and later); else only the
        percentage is known."""
        info = _FanSpeed(_FAN_SPEED_VERSION, 0, 0)
        if self._nvml.call("nvmlDeviceGetFanSpeedRPM", self._handle, ctypes.byref(info)) != SUCCESS:
            return None
        return float(info.speed)


class Nvml:
    """A started NVML. ``Nvml.open()`` loads the driver's library; ``Nvml(library)`` takes any
    object with its functions."""

    def __init__(self, library: Any) -> None:
        self._library = library
        self._devices: dict[str, Device] = {}

    @classmethod
    def open(
        cls,
        loader: Callable[[str], Any] | None = None,
        folders: tuple[str, ...] = FOLDERS,
    ) -> Nvml | None:
        """The driver's library, started; None where there is none or it does not start (no
        NVIDIA driver, a library of another version than the driver)."""
        if not sys.platform.startswith("linux"):
            return None
        load = loader or ctypes.CDLL
        library = None
        for name in (LIBRARY, *(f"{folder}/{LIBRARY}" for folder in folders)):
            try:
                library = load(name)
                break
            except OSError:
                continue
        if library is None:
            return None
        nvml = cls(library)
        for init in ("nvmlInit_v2", "nvmlInit"):
            code = nvml.call(init)
            if code is not None:
                break
        else:
            return None
        if code != SUCCESS:
            log.info("the NVIDIA library did not start (code %s)", code)
            return None
        return nvml

    def call(self, name: str, *arguments: Any) -> int | None:
        """The result code of the NVML function ``name`` (None if the driver has none)."""
        function = getattr(self._library, name, None)
        if function is None:
            return None
        try:
            return int(function(*arguments))
        except (OSError, ctypes.ArgumentError, TypeError, ValueError):
            return None

    def _handle(self, function: str, *before: Any) -> Any | None:
        handle = ctypes.c_void_p()
        for name in (f"{function}_v2", function):
            code = self.call(name, *before, ctypes.byref(handle))
            if code is not None:
                return handle if code == SUCCESS else None
        return None

    def count(self) -> int:
        number = ctypes.c_uint()
        for name in ("nvmlDeviceGetCount_v2", "nvmlDeviceGetCount"):
            code = self.call(name, ctypes.byref(number))
            if code is not None:
                return number.value if code == SUCCESS else 0
        return 0

    def by_slot(self, slot: str) -> Device | None:
        """The card at a PCI address ("0000:01:00.0"), the way the kernel lists it."""
        if slot not in self._devices:
            handle = self._handle("nvmlDeviceGetHandleByPciBusId", slot.encode("ascii"))
            if handle is None:
                return None
            self._devices[slot] = Device(self, handle)
        return self._devices[slot]

    def by_index(self, index: int) -> Device | None:
        """The card with that number (a PC with one NVIDIA card has only the first)."""
        key = f"#{index}"
        if key not in self._devices:
            handle = self._handle("nvmlDeviceGetHandleByIndex", ctypes.c_uint(index))
            if handle is None:
                return None
            self._devices[key] = Device(self, handle)
        return self._devices[key]

    def close(self) -> None:
        self.call("nvmlShutdown")
        self._devices.clear()


_lock = threading.Lock()
_shared: Nvml | None = None
_tried_at: float | None = None


def shared(
    opener: Callable[[], Nvml | None] = Nvml.open, clock: Callable[[], float] = time.monotonic
) -> Nvml | None:
    """The one NVML of the program (the readings and the load source both ask it). A library
    that did not start is tried again after a minute: the driver may load after the login."""
    global _shared, _tried_at
    with _lock:
        now = clock()
        if _shared is None and (_tried_at is None or now - _tried_at >= RETRY_SECONDS):
            _shared, _tried_at = opener(), now
        return _shared


def forget() -> None:
    """Drop the shared NVML (for tests)."""
    global _shared, _tried_at
    with _lock:
        _shared, _tried_at = None, None

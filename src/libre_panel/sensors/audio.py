"""Volume and mute of the default output device, while a theme shows them.

Readings: ``audio.volume`` (0-100 %, 0 while muted) and ``audio.muted`` (1 or 0).
They are read on every frame (``every_frame``), so a volume bar follows the
volume keys at once.

- Windows: Core Audio through ctypes, no package and no admin rights. Windows
  reports every change itself (a hand-built ``IAudioEndpointVolumeCallback``);
  a slower poll is the safety net, and every two seconds the default device is
  looked at again, so a headset plugged in or speakers chosen are followed.
- Linux: ``wpctl`` (PipeWire), else ``pactl`` (PulseAudio), asked four times a
  second. Without either tool there are no readings.

The thread runs only while a theme on show reads ``audio.*``, and ends when
the provider closes. Nothing here raises: where the volume cannot be read
there are simply no readings, and the log says why once.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

EVENT_POLL_SECONDS = 0.25  # Windows tells about changes: the poll is only a safety net
POLL_SECONDS = 0.02  # without events
DEVICE_CHECK_SECONDS = 2.0  # has the default output device changed?
LINUX_POLL_SECONDS = 0.25
SLOW_POLL_SECONDS = 5.0  # a tool that keeps failing is asked less often
FAILURES_BEFORE_SLOW = 5
RETRY_SECONDS = 5.0  # no device or tool: look again now and then
STALE_SECONDS = 2.0  # a reading older than this is dropped, not shown frozen
IDLE_SECONDS = 60.0  # nobody looked for this long: the thread ends, and starts on demand
COMMAND_TIMEOUT = 2.0


# -- the tools of Linux (no Windows needed) ----------------------------------------

_WPCTL = re.compile(r"Volume:\s*(\d*\.?\d+)")
_PERCENT = re.compile(r"(\d+)%")
_PACTL_MUTE = re.compile(r"Mute:\s*(yes|no)")


def parse_wpctl(text: str) -> tuple[float, bool] | None:
    """``Volume: 0.37`` or ``Volume: 0.37 [MUTED]``, as ``wpctl get-volume`` prints it."""
    found = _WPCTL.search(text)
    return (float(found.group(1)) * 100, "[MUTED]" in text) if found else None


def parse_pactl_volume(text: str) -> float | None:
    """The first percentage of ``Volume: front-left: 24248 /  37% / -26.45 dB, ...``."""
    found = _PERCENT.search(text)
    return float(found.group(1)) if found else None


def parse_pactl_mute(text: str) -> bool | None:
    """``Mute: yes`` or ``Mute: no``."""
    found = _PACTL_MUTE.search(text)
    return found.group(1) == "yes" if found else None


def _run(command: list[str]) -> str | None:
    """What a command prints, or None if it cannot run or fails. Its texts are English."""
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _sample_wpctl(path: str) -> tuple[float, bool] | None:
    out = _run([path, "get-volume", "@DEFAULT_AUDIO_SINK@"])
    return None if out is None else parse_wpctl(out)


def _sample_pactl(path: str) -> tuple[float, bool] | None:
    out = _run([path, "get-sink-volume", "@DEFAULT_SINK@"])
    level = None if out is None else parse_pactl_volume(out)
    if level is None:
        return None
    out = _run([path, "get-sink-mute", "@DEFAULT_SINK@"])
    return level, bool(out is not None and parse_pactl_mute(out))


_LINUX_TOOLS = (("wpctl", _sample_wpctl), ("pactl", _sample_pactl))


class _Source(Protocol):
    """One way to read the volume; made, used and closed in the provider's thread."""

    events: bool  # changes are reported to ``on_change`` as they happen
    interval: float  # seconds until the next ``sample``

    def sample(self) -> tuple[float, bool] | None: ...
    def check_device(self) -> bool: ...
    def close(self) -> None: ...


class _Commands:
    """The volume from a tool on the PATH, asked again and again. Raises OSError if
    none of the tools is installed."""

    events = False

    def __init__(self, tools: tuple[tuple[str, Callable[[str], Any]], ...], poll: float) -> None:
        self._tools = []
        for name, sample in tools:
            path = shutil.which(name)
            if path:
                self._tools.append((path, sample))
        if not self._tools:
            raise OSError("needs " + " or ".join(name for name, _ in tools))
        self._poll = poll
        self._failures = 0

    @property
    def interval(self) -> float:
        return self._poll if self._failures < FAILURES_BEFORE_SLOW else SLOW_POLL_SECONDS

    def sample(self) -> tuple[float, bool] | None:
        for position, (path, sample) in enumerate(self._tools):
            state = sample(path)
            if state is not None:
                self._tools.insert(0, self._tools.pop(position))  # the one that works goes first
                self._failures = 0
                return state
        self._failures += 1
        return None

    def check_device(self) -> bool:
        return False  # the tools ask for "the default" every time

    def close(self) -> None:
        pass


# -- Windows: Core Audio through ctypes ---------------------------------------------

CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
E_RENDER, E_CONSOLE = 0, 0  # EDataFlow and ERole: the default device for playing sound
E_NOINTERFACE = -2147467262  # 0x80004002 as a signed HRESULT

_CLSID_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"  # MMDeviceEnumerator
_IID_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"  # IMMDeviceEnumerator
_IID_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"  # IAudioEndpointVolume
_IID_UNKNOWN = "{00000000-0000-0000-C000-000000000046}"
_IID_VOLUME_CALLBACK = "{657804FA-D6AD-4496-8A60-352752AF4F89}"  # IAudioEndpointVolumeCallback

# Where a method sits in the table of a COM interface (after IUnknown's three).
_ENUMERATOR_GET_DEFAULT_ENDPOINT = 4
_DEVICE_ACTIVATE = 3
_DEVICE_GET_ID = 5
_VOLUME_REGISTER = 3
_VOLUME_UNREGISTER = 4
_VOLUME_GET_LEVEL = 9  # GetMasterVolumeLevelScalar
_VOLUME_GET_MUTE = 15

# WINFUNCTYPE exists only on Windows, where it is stdcall; elsewhere (the tests) cdecl does.
_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class _Guid(ctypes.Structure):
    _fields_ = [
        ("d1", ctypes.c_uint32),
        ("d2", ctypes.c_uint16),
        ("d3", ctypes.c_uint16),
        ("d4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def of(cls, text: str) -> _Guid:
        a, b, c, d, e = text.strip("{}").split("-")
        return cls(int(a, 16), int(b, 16), int(c, 16), (ctypes.c_ubyte * 8)(*bytes.fromhex(d + e)))


class _NotificationData(ctypes.Structure):
    # AUDIO_VOLUME_NOTIFICATION_DATA
    _fields_ = [
        ("event_context", _Guid),
        ("muted", ctypes.c_int32),
        ("master_volume", ctypes.c_float),
        ("channels", ctypes.c_uint32),
        ("channel_volumes", ctypes.c_float * 1),
    ]


def _method(pointer: Any, slot: int, *argtypes: Any, restype: Any = ctypes.c_int32) -> Any:
    """Method ``slot`` of the COM object at ``pointer``, as a function whose first
    argument is that object. The argument types must be given: ``byref(x)`` is no
    valid type to infer from."""
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return _FUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[slot])


def _check(hr: int) -> int:
    if hr < 0:
        raise OSError(f"COM call failed (HRESULT {hr & 0xFFFFFFFF:#010x})")
    return hr


def _release(pointer: Any) -> None:
    """IUnknown::Release (slot 2). A pointer that is never released is a leak: SPUR II
    lost one IMMDevice every two seconds."""
    if pointer:
        try:
            _method(pointer, 2, restype=ctypes.c_uint32)(pointer)
        except OSError:
            pass


class _VolumeCallback:
    """An IAudioEndpointVolumeCallback as a raw COM object, so that Windows can tell
    about changes. In memory it is a pointer to a table of four function pointers:
    QueryInterface, AddRef, Release, OnNotify. The reference counting is a
    pretence: the object lives as long as this Python object, which the source
    keeps until it has unregistered. Every ctypes object must stay referenced, or
    Windows calls freed memory.

    OnNotify runs in a thread of the audio service (ctypes takes the GIL itself)
    and must never let an exception through to Windows.
    """

    def __init__(self, on_change: Callable[[float, bool], None]) -> None:
        self._on_change = on_change
        self._known = (bytes(_Guid.of(_IID_UNKNOWN)), bytes(_Guid.of(_IID_VOLUME_CALLBACK)))
        # Windows reads what these return: plain integers, not checked by ctypes.
        query = _FUNCTYPE(
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        )
        count = _FUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)
        notify = _FUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(_NotificationData))
        self._functions = (
            query(self._query_interface),
            count(self._count),
            count(self._count),
            notify(self._notify),
        )
        addresses = [ctypes.cast(f, ctypes.c_void_p).value for f in self._functions]
        self._table = (ctypes.c_void_p * 4)(*addresses)
        self._object = (ctypes.c_void_p * 1)(ctypes.addressof(self._table))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._object))

    @staticmethod
    def _count(this: int) -> int:
        return 1

    def _query_interface(self, this: int, iid: Any, out: Any) -> int:
        try:
            if bytes(iid.contents) in self._known:
                out[0] = this
                return 0
        except Exception:  # noqa: BLE001 - nothing may reach Windows
            pass
        out[0] = None
        return E_NOINTERFACE

    def _notify(self, this: int, data: Any) -> int:
        try:
            note = data.contents
            self._on_change(max(0.0, min(100.0, note.master_volume * 100.0)), bool(note.muted))
        except Exception:  # noqa: BLE001 - nothing may reach Windows
            pass
        return 0


class _CoreAudio:
    """The volume of the default output device through Core Audio, with bare ctypes.

    COM belongs to the thread that starts it: make it, ``sample``, ``check_device``
    and ``close`` it in one thread, which joins the multi-threaded apartment (so
    Windows calls back without a message loop). Every interface pointer it gets
    is released again, and COM is ended. Raises OSError if there is no output
    device.
    """

    def __init__(self, on_change: Callable[[float, bool], None], ole32: Any = None) -> None:
        self.events = False  # Windows tells about changes by itself
        self._on_change = on_change
        self._ole32 = ole32 if ole32 is not None else ctypes.WinDLL("ole32")
        self._com = False  # CoInitializeEx worked, so CoUninitialize is due
        self._enumerator: ctypes.c_void_p | None = None
        self._volume: ctypes.c_void_p | None = None
        self._device_id: str | None = None
        self._callback: _VolumeCallback | None = None
        self._broken = False  # the last read failed: reconnect even to the same device
        self._get_level: Any = None
        self._get_mute: Any = None
        try:
            hr = self._ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
            self._com = hr in (0, 1)  # S_OK, S_FALSE: one CoUninitialize each
            enumerator = ctypes.c_void_p()
            _check(
                self._ole32.CoCreateInstance(
                    ctypes.byref(_Guid.of(_CLSID_ENUMERATOR)),
                    None,
                    CLSCTX_ALL,
                    ctypes.byref(_Guid.of(_IID_ENUMERATOR)),
                    ctypes.byref(enumerator),
                )
            )
            self._enumerator = enumerator
            self._connect()
        except BaseException:
            self.close()
            raise

    @property
    def interval(self) -> float:
        return EVENT_POLL_SECONDS if self.events else POLL_SECONDS

    def _default_device(self) -> tuple[ctypes.c_void_p, str | None]:
        """(IMMDevice, its id) of the default output device; the caller releases the
        device. Raises OSError if there is none."""
        device = ctypes.c_void_p()
        get = _method(
            self._enumerator,
            _ENUMERATOR_GET_DEFAULT_ENDPOINT,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check(get(self._enumerator, E_RENDER, E_CONSOLE, ctypes.byref(device)))
        if not device:
            raise OSError("no output device")
        device_id = None
        text = ctypes.c_wchar_p()
        try:
            get_id = _method(device, _DEVICE_GET_ID, ctypes.POINTER(ctypes.c_wchar_p))
            if get_id(device, ctypes.byref(text)) >= 0 and text:
                device_id = text.value
                self._ole32.CoTaskMemFree(text)  # the id is Windows' memory
        except OSError:
            pass
        return device, device_id

    def _connect(self) -> None:
        """(Re)connect to the default output device; raises OSError if that fails."""
        device, device_id = self._default_device()
        volume = ctypes.c_void_p()
        try:
            activate = _method(
                device,
                _DEVICE_ACTIVATE,
                ctypes.POINTER(_Guid),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            _check(
                activate(
                    device,
                    ctypes.byref(_Guid.of(_IID_ENDPOINT_VOLUME)),
                    CLSCTX_ALL,
                    None,
                    ctypes.byref(volume),
                )
            )
        finally:
            _release(device)
        if not volume:
            raise OSError("the output device has no volume control")
        self._unregister()
        _release(self._volume)
        self._volume, self._device_id, self._broken = volume, device_id, False
        self._get_mute = _method(volume, _VOLUME_GET_MUTE, ctypes.POINTER(ctypes.c_int32))
        self._get_level = _method(volume, _VOLUME_GET_LEVEL, ctypes.POINTER(ctypes.c_float))
        self._register()

    def _register(self) -> None:
        if self._callback is None:
            self._callback = _VolumeCallback(self._on_change)
        try:
            register = _method(self._volume, _VOLUME_REGISTER, ctypes.c_void_p)
            _check(register(self._volume, self._callback.pointer))
            self.events = True
        except OSError:
            self.events = False

    def _unregister(self) -> None:
        if self._volume is not None and self.events and self._callback is not None:
            try:
                unregister = _method(self._volume, _VOLUME_UNREGISTER, ctypes.c_void_p)
                unregister(self._volume, self._callback.pointer)
            except OSError:
                pass
        self.events = False

    def sample(self) -> tuple[float, bool] | None:
        """``(level 0-100, muted)``, or None if the device cannot be asked now.

        The level is the one that is set, also while muted: "muted" says more
        than "0", and after unmuting the old level is there again.
        """
        if self._volume is None:
            return None
        muted, level = ctypes.c_int32(0), ctypes.c_float(0.0)
        try:
            _check(self._get_mute(self._volume, ctypes.byref(muted)))
            _check(self._get_level(self._volume, ctypes.byref(level)))
        except OSError:
            self._broken = True  # the device may be gone, or back
            return None
        self._broken = False
        return max(0.0, min(100.0, level.value * 100.0)), bool(muted.value)

    def check_device(self) -> bool:
        """Has Windows chosen another default output device (a headset plugged in,
        speakers picked)? Then connect to it: the callback and the reads hang on
        the old one otherwise. Also reconnects to the same device after a failed
        read (unplugged and back). True if it connected anew."""
        if self._enumerator is None:
            return False
        try:
            device, device_id = self._default_device()
            _release(device)
            if device_id is None or (device_id == self._device_id and not self._broken):
                return False
            self._connect()
        except OSError:
            return False  # no device at the moment: look again in two seconds
        return True

    def close(self) -> None:
        self._unregister()
        _release(self._volume)
        _release(self._enumerator)
        self._volume = self._enumerator = None
        self._get_level = self._get_mute = None
        if self._com:
            self._com = False
            self._ole32.CoUninitialize()


# -- the provider ------------------------------------------------------------------


def _open_source(on_change: Callable[[float, bool], None], platform: str = sys.platform) -> _Source:
    """The way to read the volume on this machine; raises OSError if there is none."""
    if platform == "win32":
        return _CoreAudio(on_change)
    return _Commands(_LINUX_TOOLS, LINUX_POLL_SECONDS)


class AudioProvider(SensorProvider):
    """``audio.volume`` and ``audio.muted`` while a theme shows them."""

    name = "audio"
    every_frame = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._state: tuple[float, dict[str, Reading]] | None = None  # (when, readings)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._warned = False  # said that the volume cannot be read; says so when it can

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="sensors-audio", daemon=True)
            self._thread.start()

    def _publish(self, level: float, muted: bool) -> None:
        """New values: from the thread's reads, or from Windows' own notice."""
        level = round(max(0.0, min(100.0, level)), 1)
        readings = {
            "audio.volume": Reading("audio.volume", 0.0 if muted else level, "%", "Volume"),
            "audio.muted": Reading("audio.muted", 1 if muted else 0, "", "Muted"),
        }
        with self._lock:
            self._state = (time.monotonic(), readings)

    def _open(self) -> _Source | None:
        try:
            source = _open_source(self._publish)
        except Exception as exc:  # noqa: BLE001 - no way to read the volume, or just now
            if not self._warned:
                self._warned = True
                log.warning("audio: the volume cannot be read: %s", exc)
            return None
        return source

    @staticmethod
    def _sample(source: _Source) -> tuple[float, bool] | None:
        try:
            return source.sample()
        except Exception:  # noqa: BLE001 - one failed read is no reason to stop
            log.debug("audio: a read failed", exc_info=True)
            return None

    def _run(self) -> None:
        source: _Source | None = None
        looked = time.monotonic()
        device_at = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if wanted("audio."):
                    looked = now
                elif now - looked > IDLE_SECONDS:
                    return
                if source is None:
                    source = self._open()
                    if source is None:
                        self._stop.wait(RETRY_SECONDS)
                        continue
                    device_at = now + DEVICE_CHECK_SECONDS
                    # how it comes in, at the start (SPUR II's log proved the callback so)
                    way = "events" if source.events else "polling"
                    if self._warned:  # after a warning that it could not be read
                        self._warned = False
                        log.info("audio: the volume can be read now, by %s", way)
                    else:
                        log.info("audio: the volume is read by %s", way)
                state = self._sample(source)
                if state is not None:
                    self._publish(*state)
                if now >= device_at:
                    device_at = now + DEVICE_CHECK_SECONDS
                    try:
                        changed = source.check_device()
                    except Exception:  # noqa: BLE001 - looked at again in two seconds
                        log.debug("audio: the device check failed", exc_info=True)
                        changed = False
                    if changed:
                        log.info(
                            "audio: the output device changed; the volume follows it (by %s)",
                            "events" if source.events else "polling",
                        )
                        state = self._sample(source)
                        if state is not None:
                            self._publish(*state)
                self._stop.wait(source.interval)
        finally:
            with self._lock:
                self._state = None
            if source is not None:
                try:
                    source.close()  # in this thread: COM belongs to it
                except Exception:  # noqa: BLE001 - nothing left to do about it
                    log.debug("audio: closing failed", exc_info=True)

    def read(self) -> dict[str, Reading]:
        thread = self._thread
        if thread is None or not thread.is_alive():
            if self._stop.is_set() or not wanted("audio."):
                return {}
            self._start()
        with self._lock:
            state = self._state
        if state is None or time.monotonic() - state[0] > STALE_SECONDS:
            return {}
        return dict(state[1])

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)

"""Frames per second of the game you play on Windows, from RivaTuner Statistics Server (RTSS).

An alternative to PresentMon, switched on in *Settings → Sensors* (``rtss`` in ``providers``),
for those who run RTSS anyway (alone, or with MSI Afterburner): it measures the games it shows
its overlay in and shares what it measures in a memory of its own, ``RTSSSharedMemoryV2``.
This source reads that memory, ten times a second, while RTSS runs and a theme or a service
shows ``game.*`` readings. It starts no program, needs no rights and touches no game: only
the memory RTSS shares is read. Without RTSS it reads nothing, and looks again every few
seconds.

The memory, as RTSS's SDK describes it (RTSSSharedMemory.h): a header that begins with the
signature ``RTSS`` and its version, then the size of an entry of a program, where the
entries begin and how many there are. Each entry begins with the program's process id, its
path (``MAX_PATH`` characters), its flags, when the last period of RTSS's measuring began and
ended (``dwTime0``, ``dwTime1``, in ms), its frames in it (``dwFrames``) and the time of the
last frame (``dwFrameTime``, in µs). Only these first fields are read, which every version
since 2.0 has where they are.

Readings, as PresentMon's: ``game.fps`` (the last second), ``game.frametime`` (ms),
``game.low`` (the 1% lows of the last 30 s), ``game.frametime_peak`` (the longest frame time
since the last reading), ``game.app`` (the program) and what the round's frames come to
(``game.fps_avg``, ``game.fps_1low`` ...). RTSS shares the time of the last frame, not every
frame: the frame times are those of its samples, ten a second, so the lows are those of the
samples (as MangoHud's are). There is no ``game.limiter``: who holds the frame rate down,
and the round report's share of the round the GPU, the CPU or a frame cap held it back, need
what PresentMon sees of every frame. Where both run, PresentMon's readings come first (it is
asked before this one); this one's fill in where PresentMon has none (no rights for it, or
switched off). The game is the one game mode found (PresentMon's ``set_target``), else the
program with the most frames.
"""

from __future__ import annotations

import logging
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from libre_panel.sensors import presentmon
from libre_panel.sensors.base import Reading, SensorProvider, wanted
from libre_panel.sensors.presentmon import FrameCounter

log = logging.getLogger(__name__)

MAPPING = "RTSSSharedMemoryV2"
SIGNATURE = 0x52545353  # "RTSS", as a DWORD
FIRST_VERSION = 0x00020000
HEADER = struct.Struct("<5I")  # signature, version, entry size, entries' offset, entries
ENTRY = struct.Struct("<I260s5I")  # pid, path, flags, time0, time1, frames, frame time (µs)
MOST_ENTRIES = 1024  # (a header that says more is not RTSS's)
POLL_SECONDS = 0.1
ACTIVE_SECONDS = 2.0  # a program whose entry changed this long ago still draws (RTSS's period
# is a second: a game capped at an even frame time keeps its frame time, but not its period)
LOOK_SECONDS = 5.0  # without RTSS: how often it is looked for again
IDLE_SECONDS = 60.0  # nobody looked for this long: the thread ends, and starts on demand


class Memory(Protocol):
    """RTSS's shared memory, open: its bytes from ``offset``."""

    def read(self, offset: int, size: int) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Entry:
    """A program RTSS measures, as its entry has it."""

    pid: int
    app: str  # its file's name ("game.exe")
    time0: int  # when its last period began and ended, ms (GetTickCount)
    time1: int
    frames: int  # the frames in it
    frame_us: int  # the last frame's time, µs


def entries(memory: Memory) -> list[Entry]:
    """The programs RTSS measures now; [] where the memory is no RTSS's (or RTSS is ending)."""
    signature, version, size, offset, count = HEADER.unpack(memory.read(0, HEADER.size))
    if signature != SIGNATURE or version < FIRST_VERSION or size < ENTRY.size:
        return []
    found = []
    for n in range(min(count, MOST_ENTRIES)):
        pid, path, _flags, time0, time1, frames, frame_us = ENTRY.unpack(
            memory.read(offset + n * size, ENTRY.size)
        )
        if not pid:
            continue  # (a free entry)
        name = path.split(b"\0", 1)[0].decode("mbcs" if sys.platform == "win32" else "latin-1",
                                                "replace")  # fmt: skip
        app = name.replace("/", "\\").rsplit("\\", 1)[-1]
        if app:
            found.append(Entry(pid, app, time0, time1, frames, frame_us))
    return found


class _Mapping:
    """RTSS's memory through Windows' own calls: OpenFileMapping, then MapViewOfFile (a
    mapping that is not there is not made: RTSS makes it)."""

    def __init__(self, name: str) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenFileMappingW.restype = wintypes.HANDLE
        kernel32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD, ctypes.c_size_t)  # fmt: skip
        kernel32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.VirtualQuery.restype = ctypes.c_size_t
        kernel32.VirtualQuery.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)
        self._ctypes, self._kernel32 = ctypes, kernel32
        file_map_read = 0x0004
        self._handle = kernel32.OpenFileMappingW(file_map_read, False, name)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), f"no {name}")
        self._view = kernel32.MapViewOfFile(self._handle, file_map_read, 0, 0, 0)
        if not self._view:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(self._handle)
            raise OSError(error, f"{name} cannot be read")

        class Info(ctypes.Structure):  # MEMORY_BASIC_INFORMATION
            _fields_ = [("base", ctypes.c_void_p), ("allocation", ctypes.c_void_p),
                        ("protect", wintypes.DWORD), ("partition", wintypes.WORD),
                        ("size", ctypes.c_size_t), ("state", wintypes.DWORD),
                        ("protection", wintypes.DWORD), ("kind", wintypes.DWORD)]  # fmt: skip

        info = Info()
        got = kernel32.VirtualQuery(self._view, ctypes.byref(info), ctypes.sizeof(info))
        self.size = info.size if got else HEADER.size

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or offset + size > self.size:
            raise ValueError("beyond RTSS's memory")
        return self._ctypes.string_at(self._view + offset, size)

    def close(self) -> None:
        if self._view:
            self._kernel32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def open_memory(name: str = MAPPING) -> Memory | None:
    """RTSS's memory, open; None where RTSS does not run (or this is no Windows)."""
    if sys.platform != "win32":
        return None
    try:
        return _Mapping(name)
    except OSError:
        return None


class RTSSProvider(SensorProvider):
    """``game.*`` from RTSS's shared memory, while a theme or a service shows them."""

    name = "rtss"

    def __init__(self, options: dict[str, Any] | None = None,
                 opener: Callable[[str], Memory | None] = open_memory,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        super().__init__(options)
        self.mapping = str(self.options.get("mapping") or MAPPING)  # (another, for the tests)
        self._opener, self._clock = opener, clock
        self._counter = FrameCounter()
        # pid -> (time1, frame time) last read, and when they last changed (None: not seen yet)
        self._seen: dict[int, tuple[tuple[int, int], float | None]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False  # whether RTSS's memory was there at the last look

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="sensors-rtss", daemon=True)
            self._thread.start()

    def poll(self, memory: Memory, now: float) -> None:
        """One look at RTSS's memory: the last frame time of each program that still draws (its
        entry changed in the last :data:`ACTIVE_SECONDS`), as a sample of its frames."""
        found = entries(memory)
        seen: dict[int, tuple[tuple[int, int], float | None]] = {}
        with self._lock:
            self._counter.set_target(presentmon.target())
            for entry in found:
                mark = (entry.time1, entry.frame_us)
                before = self._seen.get(entry.pid)
                changed = None if before is None else (now if before[0] != mark else before[1])
                seen[entry.pid] = (mark, changed)
                drawing = changed is not None and now - changed <= ACTIVE_SECONDS
                if drawing and entry.frame_us > 0:
                    self._counter.add(entry.app, entry.frame_us / 1000, now)
        self._seen = seen

    def _run(self) -> None:
        memory: Memory | None = None
        looked = self._clock()
        tried = -LOOK_SECONDS
        try:
            while not self._stop.is_set():
                now = self._clock()
                if wanted("game."):
                    looked = now
                elif now - looked > IDLE_SECONDS:
                    return
                if memory is None and now - tried >= LOOK_SECONDS:
                    tried = now
                    memory = self._opener(self.mapping)
                    if memory is not None and not self.running:
                        log.info("rtss: reading RivaTuner's frame rates")
                    self.running = memory is not None
                if memory is not None:
                    try:
                        self.poll(memory, now)
                    except (OSError, ValueError, struct.error) as exc:
                        log.info("rtss: its memory is gone (%s)", exc)
                        memory.close()
                        memory, self.running, self._seen = None, False, {}
                    else:
                        if now - tried >= LOOK_SECONDS:  # (RTSS ended: let its memory go)
                            memory.close()
                            memory, tried = None, -LOOK_SECONDS
                self._stop.wait(POLL_SECONDS)
        finally:
            if memory is not None:
                memory.close()

    def status(self) -> dict[str, Any]:
        """Whether RTSS runs, as the last look found it (the app's Sensors and the console)."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return {"rivatuner": None}  # (not asked yet: nothing shows game.*)
        return {"rivatuner": "runs" if self.running else "not running"}

    def read(self) -> dict[str, Reading]:
        thread = self._thread
        if thread is None or not thread.is_alive():
            if self._stop.is_set() or not wanted("game."):
                return {}
            self._start()
        with self._lock:
            return self._counter.readings(self._clock())

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)

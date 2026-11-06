"""Light mode (always on since 0.8.1; 0.8's ``light_mode`` in config.toml is let be): what only
the app needs is let go of while no page of it is open, and the memory goes back to the system.

The app's pictures of every theme (Start, Design) fill the renderer's caches with the letters,
symbols and pieces of all of them: well over 100 MB. Once no page of the app has asked for
anything for a minute (its window closed, or hidden, or never opened since Libre Panel started,
as at login), its own pictures go (of every theme, the
previews of the community's themes), and the caches note for a while what the panel draws with
(render/kept.py: :func:`~libre_panel.render.kept.mark_all`); then they let go of the rest
(:func:`~libre_panel.render.kept.shed_all`). So the panel keeps what it uses and draws on as it
did, and the app draws its pictures again when it asks.

Then the caches that cannot tell what was used (symbols, the moon) are emptied and the memory
goes back to the system; Windows also takes back the pages the panel does not touch (the
"Memory" Task Manager shows), which come back as the panel touches them. The C heap's free
pages (on Windows its heaps') go back again at each look while the app stays closed
(:func:`tidy`). A switch for it (0.8) had no cost worth one: the app takes a moment longer to
open after a minute closed, the panel draws on as it did.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

IDLE_S = 60.0  # no page of the app asked anything for this long: it is closed
LOOK_S = 15.0  # how often the watch looks (and how long the caches note what the panel uses)
SAID_S = 180.0  # this long after its memory went back, the log says what the process holds


def empty_caches() -> None:
    """The renderer's caches that do not note what is used (render/kept.py's do, and keep the
    panel's): the symbols' sets, the covers' colours, solid colours, the moon's pictures. They
    fill again with what is drawn."""
    from libre_panel.render import cover, lit, moon, symbols

    with symbols._lock:
        symbols._sets.clear()
    cover._kept.clear()
    with lit._SOLIDS_LOCK:
        lit._SOLIDS.clear()
    for cached in vars(moon).values():
        if hasattr(cached, "cache_clear"):
            cached.cache_clear()


# a block this large or larger is the system's own (mapped), and goes back to it as it is freed
MAPPED_FROM = 1 << 20


def mapped_blocks(platform: str = sys.platform) -> dict[str, str]:
    """What a program Libre Panel starts (its engine) is given in its environment for blocks of
    :data:`MAPPED_FROM` and more, as :func:`fewer_heaps` sets them here (Linux): nothing where
    the environment says otherwise."""
    if not platform.startswith("linux") or "GLIBC_TUNABLES" in os.environ:
        return {}
    return {"GLIBC_TUNABLES": f"glibc.malloc.mmap_threshold={MAPPED_FROM}"}


def fewer_heaps(platform: str = sys.platform) -> bool:
    """Linux (glibc): two heaps for all threads instead of up to eight per core, set when the
    app starts (before its threads; whether it was set). Each thread that frees a frame's
    pictures kept their memory in a heap of its own, for its next ones: some 50 MB more of the
    system's memory held, and a frame no faster (measured on the 12.3", rather a little faster:
    8.6 instead of 9.1 ms). ``MALLOC_ARENA_MAX`` set in the environment holds.

    And a block of :data:`MAPPED_FROM` or more (a card's picture, a cover) is the system's own
    from then on, and goes back to it as it is freed: glibc took such blocks from the heap once
    one of them was freed, and the pictures freed in the middle of it stayed with the program
    (Lumen on the 12.3": its main process 113 MB instead of 120 to 125 after the first change of
    track). ``MALLOC_MMAP_THRESHOLD_`` or ``GLIBC_TUNABLES`` set in the environment hold."""
    if not platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None)
        if not hasattr(libc, "gnu_get_libc_version"):  # (musl has heaps of its own)
            return False
        if not {"MALLOC_MMAP_THRESHOLD_", "GLIBC_TUNABLES"} & set(os.environ):
            libc.mallopt(-3, MAPPED_FROM)  # M_MMAP_THRESHOLD (fixed: no longer raised)
        if "MALLOC_ARENA_MAX" in os.environ:
            return False
        return bool(libc.mallopt(-8, 2))  # M_ARENA_MAX
    except (OSError, AttributeError, TypeError):  # (TypeError: Windows' ctypes takes no None)
        return False


def give_back(platform: str = sys.platform) -> None:
    """The memory no longer used back to the system: the C heap's free pages (Linux; on Windows
    its heaps', :func:`windows_heaps`), and on Windows then the pages of the program that are not
    touched (its working set)."""
    try:
        if platform == "win32":
            windows_heaps()
            empty_working_set()
        elif platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError, ctypes.ArgumentError) as exc:
        log.debug("the memory stays with the program: %s", exc)


def empty_working_set() -> None:
    """Windows: the pages of the program leave its working set; those it touches come back as
    it touches them. With a kernel32 of its own and its arguments declared: the shared one's
    GetCurrentProcess may have been declared to give a HANDLE by other code (the sensor
    service's own_memory), whose value an undeclared argument cannot take."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetProcessWorkingSetSize.argtypes = [
        wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t,
    ]  # fmt: skip
    kernel32.SetProcessWorkingSetSize.restype = wintypes.BOOL
    every = ctypes.c_size_t(-1).value  # (both -1: as many pages as can go)
    kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), every, every)


def tidy(platform: str = sys.platform) -> None:
    """The C heap's free pages back to the system, at each look while the app is closed: a
    frame's work frees pictures and buffers between those that stay, and the heap
    keeps those pages for the program. Linux (glibc's trim): Lumen on the 12.3" and the 9.2",
    the main process 90 MB instead of 100, each time some milliseconds, no picture later for it.
    Windows: its heaps' free memory (:func:`windows_heaps`). Elsewhere nothing (musl has no
    such call)."""
    try:
        if platform == "win32":
            windows_heaps()
            return
        if not platform.startswith("linux"):
            return
        libc = ctypes.CDLL(None)
        if hasattr(libc, "gnu_get_libc_version"):
            libc.malloc_trim(0)
    except (OSError, AttributeError, TypeError) as exc:
        log.debug("the heap stays as it is: %s", exc)


HEAP_OPTIMIZE_RESOURCES = 3  # HeapSetInformation's HeapOptimizeResources (Windows 8.1 and later)


class _HeapOptimize(ctypes.Structure):
    """HEAP_OPTIMIZE_RESOURCES_INFORMATION: version 1, no flags."""

    _fields_ = [("version", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


def windows_heaps() -> None:
    """Windows: the free memory of the process's heaps back to the system. First what the heaps'
    low-fragmentation caches hold and what can be decommitted (HeapOptimizeResources, for all of
    them), then the process heap's free blocks merged and the large ones decommitted
    (HeapCompact). The process heap is the C heap of Python, Pillow and numpy (the UCRT's
    malloc): the pictures and buffers made and freed between those that stay left its pages
    committed to the program, the app's window's as well (Lumen on a 12.3" and a 9.2", with the
    window open: 184 MB free in it, which stayed after the window closed). Safe beside the other
    threads: both take the heaps' locks."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.HeapSetInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
    ]  # fmt: skip
    kernel32.HeapSetInformation.restype = wintypes.BOOL
    kernel32.GetProcessHeap.restype = wintypes.HANDLE
    kernel32.HeapCompact.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.HeapCompact.restype = ctypes.c_size_t
    info = _HeapOptimize(1, 0)
    kernel32.HeapSetInformation(
        None, HEAP_OPTIMIZE_RESOURCES, ctypes.byref(info), ctypes.sizeof(info)
    )
    kernel32.HeapCompact(kernel32.GetProcessHeap(), 0)


def release(also: Callable[[], None] | None = None) -> None:
    """Let go of what only the app needs (``also``: the app's own, its pictures of the themes)."""
    empty_caches()
    if also is not None:
        also()
    gc.collect()
    give_back()


def say_held(processes: Callable[[], dict[str, Any] | None] | None = None) -> None:
    """The log's line of what Libre Panel holds, some minutes after the app closed: this
    process's memory as the Task Manager's Memory column shows it (on Linux what is its own
    alone) and what Windows has committed to it, each of its processes' (``processes()``:
    load_report's LoadSampler.memory_now; the engines, the sensor service, PresentMon), and
    what this one holds as the load report counts it (load_report.py's inside): what a person
    can read later in Live → Diagnose → Log, with the window open again, in one line."""
    import psutil

    from libre_panel import load_report

    info = psutil.Process().memory_full_info()
    said = f"{info.uss / 2**20:.0f} MB"
    commit = getattr(info, "private", None)  # (Windows: what is committed to it)
    if commit is not None:
        said += f" (committed {commit / 2**20:.0f} MB)"
    every = ""
    if processes is not None:
        try:
            every = load_report.memory_text(processes())
        except Exception:  # noqa: BLE001 - the line goes on without them
            log.debug("Libre Panel's processes could not be measured", exc_info=True)
    if every:
        said += f"; {every}"
    held = load_report.inside_text(load_report.inside())
    log.info("light mode: %.0f minutes after the app closed, %s; %s", SAID_S / 60,
             said, " ".join(line.strip() for line in held))  # fmt: skip


def mark() -> None:
    from libre_panel.render import kept

    kept.mark_all()


def unmark() -> None:
    from libre_panel.render import kept

    kept.unmark_all()


def shed() -> None:
    from libre_panel.render import kept

    gone = kept.shed_all()
    log.debug("the app is closed: %.1f MB of pictures only it drew with went", gone / kept.MB)


class Watch:
    """Once the app has been closed for :data:`IDLE_S` seconds, lets go of the app's own
    pictures (``closed``) and has the caches note what the panel uses (``mark``); a look later
    they let go of the rest (``shed``) and the memory goes (``release``). Each once per time the
    app was open (``last_request()``: when a page of it last asked something, monotonic
    seconds; None: never), and once for an app never opened since the watch began (Libre Panel
    started at login: what only its start touched went on holding the memory, on Windows the
    pages of its start too). After that each look, while the app stays closed, gives back what
    the panel's frames freed meanwhile (``tidy``)."""

    def __init__(
        self,
        last_request: Callable[[], float | None],
        release: Callable[[], None] = release,
        clock: Callable[[], float] = time.monotonic,
        closed: Callable[[], None] = lambda: None,
        mark: Callable[[], None] = mark,
        shed: Callable[[], None] = shed,
        unmark: Callable[[], None] = unmark,
        tidy: Callable[[], None] = tidy,
        say: Callable[[], None] = say_held,
    ) -> None:
        self.last_request = last_request
        self.release, self.clock, self.closed = release, clock, closed
        self.mark, self.shed, self.unmark = mark, shed, unmark
        self.tidy, self.say = tidy, say
        self.released_at = 0.0  # when it released last
        self.said_for: Any = object()  # the request it said what the process holds after
        self.began = clock()  # (an app never opened is closed since then)
        self.marking = False  # the caches note what is used (between closed and shed)
        self.released_for: Any = object()  # the request it released after (none yet)
        self.closed_for: Any = object()  # ... it let the app's pictures go after
        self.shed_for: Any = object()  # ... and the caches let go of what the panel did not use
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def look(self) -> bool:
        """One look: whether it released now."""
        last = self.last_request()
        if last is None:
            last = self.began
        if self.clock() - last < IDLE_S:
            if self.marking:  # opened again before the caches let go: they keep all
                self.marking = False
                self.unmark()
            return False
        if last != self.closed_for:
            self.closed_for = last
            self.closed()
            self.marking = True
            self.mark()  # (what the panel draws with until the next look stays)
            log.debug("the app is closed: its pictures of the themes went")
            return False
        if last != self.shed_for:
            self.shed_for = last
            self.marking = False
            self.shed()
        if last == self.released_for:  # released: what the panel's frames freed since goes too
            self.tidy()
            if last != self.said_for and self.clock() - self.released_at >= SAID_S:
                self.said_for = last  # (once a while after: what stays with the app closed)
                self.say()
            return False
        self.released_for = last
        self.released_at = self.clock()
        self.release()
        log.info("light mode: the app is closed, its memory went back to the system")
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="light-mode", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(LOOK_S):
            try:
                self.look()
            except Exception:  # noqa: BLE001 - a look that failed: the next one tries again
                log.debug("light mode: no release", exc_info=True)

    def stop(self) -> None:
        self._stop.set()

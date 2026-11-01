"""Light mode (``light_mode`` in config.toml, on unless set since 0.8): what only the app needs
is let go of while no page of it is open.

The app's pictures of every theme (Start, Design) fill the renderer's caches with the letters,
symbols and pieces of all of them: well over 100 MB. Once no page of the app has asked for
anything for a minute (its window closed, or hidden), its own pictures go (of every theme, the
previews of the community's themes), and the caches note for a while what the panel draws with
(render/kept.py: :func:`~libre_panel.render.kept.mark_all`); then they let go of the rest
(:func:`~libre_panel.render.kept.shed_all`). So the panel keeps what it uses and draws on as it
did, and the app draws its pictures again when it asks. That happens light mode or not.

Light mode also empties the caches that cannot tell what was used (symbols, the moon) and gives
the memory back to the system; Windows also takes back the pages the panel does not touch (the
"Memory" Task Manager shows), which come back as the panel touches them.
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


def fewer_heaps(platform: str = sys.platform) -> bool:
    """Linux (glibc): two heaps for all threads instead of up to eight per core, set when the
    app starts (before its threads; whether it was set). Each thread that frees a frame's
    pictures kept their memory in a heap of its own, for its next ones: some 50 MB more of the
    system's memory held, and a frame no faster (measured on the 12.3", rather a little faster:
    8.6 instead of 9.1 ms). ``MALLOC_ARENA_MAX`` set in the environment holds."""
    if not platform.startswith("linux") or "MALLOC_ARENA_MAX" in os.environ:
        return False
    try:
        libc = ctypes.CDLL(None)
        if not hasattr(libc, "gnu_get_libc_version"):  # (musl has heaps of its own)
            return False
        return bool(libc.mallopt(-8, 2))  # M_ARENA_MAX
    except (OSError, AttributeError):
        return False


def give_back(platform: str = sys.platform) -> None:
    """The memory no longer used back to the system: the C heap's free pages (Linux), and on
    Windows the pages of the program that are not touched (its working set)."""
    try:
        if platform == "win32":
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetProcessWorkingSetSize(
                kernel32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1)
            )
        elif platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError) as exc:
        log.debug("the memory stays with the program: %s", exc)


def release(also: Callable[[], None] | None = None) -> None:
    """Let go of what only the app needs (``also``: the app's own, its pictures of the themes)."""
    empty_caches()
    if also is not None:
        also()
    gc.collect()
    give_back()


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
    they let go of the rest (``shed``), and while ``light()`` says light mode is on the memory
    goes (``release``). Each once per time the app was open (``last_request()``: when a page of
    it last asked something, monotonic seconds; None: never)."""

    def __init__(
        self,
        light: Callable[[], bool],
        last_request: Callable[[], float | None],
        release: Callable[[], None] = release,
        clock: Callable[[], float] = time.monotonic,
        closed: Callable[[], None] = lambda: None,
        mark: Callable[[], None] = mark,
        shed: Callable[[], None] = shed,
        unmark: Callable[[], None] = unmark,
    ) -> None:
        self.light, self.last_request = light, last_request
        self.release, self.clock, self.closed = release, clock, closed
        self.mark, self.shed, self.unmark = mark, shed, unmark
        self.marking = False  # the caches note what is used (between closed and shed)
        self.released_for: Any = object()  # the request it released after (none yet)
        self.closed_for: Any = object()  # ... it let the app's pictures go after
        self.shed_for: Any = object()  # ... and the caches let go of what the panel did not use
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def look(self) -> bool:
        """One look: whether it released now (light mode)."""
        last = self.last_request()
        if last is None:
            return False
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
        if last == self.released_for or not self.light():
            return False
        self.released_for = last
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

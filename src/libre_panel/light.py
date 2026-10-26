"""Light mode (``light_mode`` in config.toml): what only the app needs is let go of while no page
of it is open.

The app's pictures of every theme (Start, Design) fill the renderer's caches with the letters,
symbols and pieces of all of them, and the memory they took stays with the program: well over
100 MB. With light mode on, once no page of the app has asked for anything for a minute (its
window closed, or hidden), those caches are emptied and the memory goes back to the system;
Windows also takes back the pages the panel does not touch (the "Memory" Task Manager shows).
The panel draws on as it did: what it needs comes back within a frame or two, and the app takes
a moment longer when it opens again.

Without light mode the app's own pictures go all the same once it has been closed for a minute
(its pictures of every theme, the previews of the community's themes): nothing but the app
uses them, and it draws them again when it asks. The renderer's caches the panel shares stay.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

IDLE_S = 60.0  # no page of the app asked anything for this long: it is closed
LOOK_S = 15.0  # how often the watch looks


def empty_caches() -> None:
    """The renderer's caches of letters, pieces, symbols, fans and covers: they fill again with
    what is drawn."""
    from libre_panel.render import cover, facets, fan, lit, moon, symbols

    for kept in (facets._glyphs, facets._patches, facets._windows, facets._images,
                 facets._rings, facets._tracks, facets._grooves):  # fmt: skip
        kept.clear()
    with symbols._lock:
        symbols._sets.clear()
    for kept in (fan._images, fan._layers, fan._frames, lit._PICTURES):
        kept.clear()
    cover._kept.clear()
    with lit._SOLIDS_LOCK:
        lit._SOLIDS.clear()
    for cached in vars(moon).values():
        if hasattr(cached, "cache_clear"):
            cached.cache_clear()


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


class Watch:
    """Once the app has been closed for :data:`IDLE_S` seconds, lets go of the app's own
    pictures (``closed``), and while ``light()`` says light mode is on releases the memory
    (``release``); each once per time the app was open (``last_request()``: when a page of it
    last asked something, monotonic seconds; None: never)."""

    def __init__(
        self,
        light: Callable[[], bool],
        last_request: Callable[[], float | None],
        release: Callable[[], None] = release,
        clock: Callable[[], float] = time.monotonic,
        closed: Callable[[], None] = lambda: None,
    ) -> None:
        self.light, self.last_request = light, last_request
        self.release, self.clock, self.closed = release, clock, closed
        self.released_for: Any = object()  # the request it released after (none yet)
        self.closed_for: Any = object()  # ... and the one it let the app's pictures go after
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def look(self) -> bool:
        """One look: whether it released now (light mode)."""
        last = self.last_request()
        if last is None or self.clock() - last < IDLE_S:
            return False
        if last != self.closed_for:
            self.closed_for = last
            self.closed()
            log.debug("the app is closed: its pictures of the themes went")
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

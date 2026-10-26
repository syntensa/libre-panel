"""What the renderer keeps from frame to frame, held to a number of bytes.

A frame is made of pieces that mostly stay the same: the letters of a number, a fan's rotor at
each position, the bars of a list, what lies under a graph that moves. Each is kept, made once,
in a :class:`Kept`: the most recently used first to stay, the least recently used the first to
go once the pictures kept take more than its ``budget`` bytes. A count would not do: a glyph is
some hundred bytes, a region of the 12.3" up to five megabytes, so a count that holds enough
glyphs would hold a gigabyte of regions, and one that holds the regions too few glyphs.

Every :class:`Kept` counts its hits and misses and the bytes it holds; :func:`stats` sums them
by name over all of them (for the tests, and for measuring a theme), so that a budget can be
set to what the themes need: as small as keeps the hit rate, no smaller.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

MB = 1 << 20
_NONE = object()  # (a key kept with None as its value: a picture that could not be read)
_MISSING = object()
_ALL: weakref.WeakSet[Kept] = weakref.WeakSet()
_ALL_LOCK = threading.Lock()


def size_of(value: Any) -> int:
    """The bytes ``value`` holds, as far as pictures go: an image's pixels (Pillow keeps RGB
    in four bytes a pixel, as RGBA), an array's, and those of the pieces, tuples, lists and
    dicts made of them; anything else a little."""
    if value is None:
        return 0
    if isinstance(value, Image.Image):
        per = 1 if value.mode in ("1", "L", "P") else 2 if value.mode.startswith("I;16") else 4
        return value.width * value.height * per + 128
    nbytes = getattr(value, "nbytes", None)  # numpy's arrays, and what says its own size
    if isinstance(nbytes, int):
        return nbytes + 112
    if isinstance(value, tuple | list):
        return sum(size_of(item) for item in value) + 56 + 8 * len(value)
    if isinstance(value, dict):
        return sum(size_of(item) for item in value.values()) + 64 + 32 * len(value)
    layer = getattr(value, "layer", None)  # a piece: its layer, its light and its backdrop
    if isinstance(layer, Image.Image):
        return (
            size_of(layer) + size_of(getattr(value, "light", None))
            + size_of(getattr(value, "backdrop", None)) + 96
        )  # fmt: skip
    return 64


class Kept:
    """Values by key, the most recently used kept while all of them hold at most ``budget``
    bytes (:func:`size_of`, or the size given to :meth:`put`); the newest ``least`` always
    stay, also when one alone is larger. Thread-safe: the render thread and the helper
    threads ask at once. :meth:`get` and :meth:`make` count a hit or a miss; reading it as a
    mapping (``in``, ``[]``, iterating) counts nothing and changes no order."""

    def __init__(self, name: str, budget: int, least: int = 1) -> None:
        self.name = name
        self.budget = int(budget)
        self.least = max(0, least)
        self.size = 0  # the bytes it holds now
        self.peak = 0  # the most it held
        self.hits = self.misses = self.dropped = 0
        self._items: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._lock = threading.Lock()
        with _ALL_LOCK:
            _ALL.add(self)

    # -- as a cache ------------------------------------------------------------------------------

    def get(
        self, key: Hashable, default: Any = None, valid: Callable[[Any], bool] | None = None
    ) -> Any:
        """The value of ``key`` (now the most recently used), counted as a hit; else
        ``default``, counted as a miss. ``valid``: whether the value kept serves (one that
        does not is a miss, and stays where it is)."""
        with self._lock:
            hit = self._items.get(key)
            value = None if hit is None or hit[0] is _NONE else hit[0]
            if hit is None or (valid is not None and not valid(value)):
                self.misses += 1
                return default
            self._items.move_to_end(key)
            self.hits += 1
        return value

    def put(self, key: Hashable, value: Any, size: int | None = None) -> Any:
        """Keep ``value`` as the newest (``size``: its bytes, where :func:`size_of` cannot
        tell); the least recently used go while more than the budget is kept."""
        n = size_of(value) if size is None else int(size)
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self.size -= old[1]
            if self._too_big(n):
                return value
            self._items[key] = (_NONE if value is None else value, n)
            self.size += n
            self._trim()
        return value

    def make(self, key: Hashable, build: Callable[[], Any]) -> Any:
        """The value of ``key``, made by ``build`` and kept where it is not kept yet (made
        outside the lock: one another thread made meanwhile is the one kept and returned)."""
        found = self.get(key, _MISSING)
        if found is not _MISSING:
            return found
        made = build()
        n = size_of(made)
        with self._lock:
            there = self._items.get(key)
            if there is not None:
                value = there[0]
                return None if value is _NONE else value
            if self._too_big(n):
                return made
            self._items[key] = (_NONE if made is None else made, n)
            self.size += n
            self._trim()
        return made

    def pop(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            hit = self._items.pop(key, None)
            if hit is None:
                return default
            self.size -= hit[1]
        return None if hit[0] is _NONE else hit[0]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.size = 0

    def resize(self, budget: int) -> None:
        """Another budget (the tests): what no longer fits goes at once."""
        with self._lock:
            self.budget = int(budget)
            self._trim()

    def _too_big(self, n: int) -> bool:
        """Whether a value of ``n`` bytes is not kept at all: where none need stay (``least``
        0) and it alone is over the budget (it would only push the others out first)."""
        return self.least == 0 and n > self.budget

    def _trim(self) -> None:
        self.peak = max(self.peak, self.size)
        while self.size > self.budget and len(self._items) > self.least:
            _key, (_value, n) = self._items.popitem(last=False)
            self.size -= n
            self.dropped += 1

    # -- as a mapping (nothing counted, no order changed) ----------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __getitem__(self, key: Hashable) -> Any:
        value = self._items[key][0]
        return None if value is _NONE else value

    def __iter__(self) -> Iterator[Hashable]:
        with self._lock:
            return iter(list(self._items))

    def keys(self) -> list[Hashable]:
        with self._lock:
            return list(self._items)

    def values(self) -> list[Any]:
        with self._lock:
            return [None if v is _NONE else v for v, _n in self._items.values()]

    def items(self) -> list[tuple[Hashable, Any]]:
        with self._lock:
            return [(k, None if v is _NONE else v) for k, (v, _n) in self._items.items()]

    def copy(self) -> dict[Hashable, Any]:
        return dict(self.items())

    def __repr__(self) -> str:
        held, budget = self.size / MB, self.budget / MB
        return f"<Kept {self.name}: {len(self)} items, {held:.1f} of {budget:.1f} MB>"


_COUNTED = ("bytes", "budget", "peak", "items", "hits", "misses", "dropped", "count")


def stats() -> dict[str, dict[str, Any]]:
    """By name, over every :class:`Kept` there is: the bytes held, the budget, the most held,
    the items, the hits, the misses, those let go and the hit rate."""
    with _ALL_LOCK:
        everyone = list(_ALL)
    out: dict[str, dict[str, Any]] = {}
    for kept in everyone:
        row = out.setdefault(kept.name, dict.fromkeys(_COUNTED, 0))
        row["bytes"] += kept.size
        row["budget"] = max(row["budget"], kept.budget)
        row["peak"] = max(row["peak"], kept.peak)
        row["items"] += len(kept)
        row["hits"] += kept.hits
        row["misses"] += kept.misses
        row["dropped"] += kept.dropped
        row["count"] += 1
    for row in out.values():
        asked = row["hits"] + row["misses"]
        row["rate"] = round(row["hits"] / asked, 4) if asked else None
    return dict(sorted(out.items()))


def reset_counts() -> None:
    """Every :class:`Kept`'s hits, misses and most held from now (the bench, between runs)."""
    with _ALL_LOCK:
        everyone = list(_ALL)
    for kept in everyone:
        kept.hits = kept.misses = kept.dropped = 0
        kept.peak = kept.size


def cached(name: str, budget: int, least: int = 0) -> Callable[[Callable[..., Any]], Any]:
    """``functools.lru_cache`` for a function that makes pictures, held by bytes: the result of
    each set of (hashable) arguments kept in a :class:`Kept` of ``budget`` bytes (callers must
    not change it). ``least`` 0: a picture larger than the budget alone is made each time it
    is asked for (a gradient the size of the panel is asked for once, for a card that is
    then kept as a piece)."""

    def wrap(make: Callable[..., Any]) -> Any:
        kept = Kept(name, budget, least)

        @functools.wraps(make)
        def get(*args: Any) -> Any:
            return kept.make(args, lambda: make(*args))

        get.kept = kept  # type: ignore[attr-defined]
        get.cache_clear = kept.clear  # type: ignore[attr-defined]
        return get

    return wrap


# -- what is let go when it has not been used for a while ------------------------------------

TICK_S = 5.0  # how often :func:`tick` looks
_idle: list[Callable[[float], None]] = []
_ticked = [0.0]


def on_idle(forget: Callable[[float], None]) -> None:
    """Have ``forget(now)`` called now and then (:func:`tick`): it lets go of what nothing asked
    for for a while (the weather bodies, render/light3d.py)."""
    _idle.append(forget)


def tick(now: float | None = None) -> None:
    """Called by every frame (Renderer.render): every ``TICK_S`` it calls those of
    :func:`on_idle` (a look at the clock otherwise)."""
    now = time.monotonic() if now is None else now
    if now - _ticked[0] < TICK_S:
        return
    _ticked[0] = now
    for forget in list(_idle):
        try:
            forget(now)
        except Exception:  # noqa: BLE001 - what was not let go stays: the next look tries
            log.debug("not let go", exc_info=True)

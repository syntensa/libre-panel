"""What moves on the panel, from frame to frame and from theme to theme.

Values glide from reading to reading (a spring per sensor), threshold colours
fade instead of jumping, and in video mode graphs draw from a history of the
gliding values, 25 points a second. The main loop keeps one :class:`Motion`
for the panel and hands it to every renderer it makes, so a theme that comes
on shows settled numbers and full graphs in its first frame (SPUR II keeps one
engine per process for the same reason). Editor previews get their own.

Everything here runs on the planned time of the frame being drawn, never the
clock: the same look at 25, 30 and 50 fps, and no stutter from a frame that
started late.
"""

from __future__ import annotations

import math
from collections import deque
from itertools import islice
from typing import Any

RATE = 25.0  # history points per second, whatever the frame rate
PAUSE_S = 0.25  # a value off the panel this long starts again where it is
IDLE_S = 1.0  # ... but not over the pauses of the PNG path (up to 0.5 s)
FADE_S = 0.18  # a threshold colour takes this long per step
KEEP_S = 600.0  # a history no graph asked for this long is dropped
MAX_POINTS = round(600 * RATE)  # the longest history: ten minutes


def grid(t: float) -> int:
    """The history point at or just before ``t``."""
    return math.floor(t * RATE + 1e-6)


def number(value: Any) -> float | None:
    """A reading's value as a number for gliding and graphs, None if it is none."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class Spring:
    """A value gliding from reading to reading: a critically damped spring,
    kept piece by piece (one per new reading), so its value is known at any
    moment and, until the next reading, also in the future (graph strips draw
    ahead with it).

    A piece is (number, start, target, error, speed, omega): from ``start`` on
    the value is ``target + (error + (speed + omega * error) * dt) * exp(-omega
    * dt)``; omega 0 means it stands at ``target``, a target of None means
    there is no reading (a gap).
    """

    __slots__ = ("pieces", "target", "seen", "made")

    def __init__(self) -> None:
        self.pieces: list[tuple[int, float, float | None, float, float, float]] = []
        self.target: float | None = None
        self.seen: float | None = None  # the frame it was last drawn in
        self.made = 0  # pieces so far: a strip knows which ones it drew

    def piece(self, t: float) -> tuple[int, float, float | None, float, float, float] | None:
        for piece in reversed(self.pieces):  # a handful: no bisect needed
            if piece[1] <= t:
                return piece
        return None

    def state(self, t: float) -> tuple[float, float] | None:
        """(value, speed) at ``t``; None without a reading then."""
        piece = self.piece(t)
        if piece is None or piece[2] is None:
            return None
        _, start, target, error, speed, omega = piece
        if omega <= 0:
            return target, 0.0
        dt = t - start
        decay = math.exp(-omega * dt)
        curve = speed + omega * error
        return target + (error + curve * dt) * decay, (speed - omega * curve * dt) * decay

    def at(self, t: float) -> float | None:
        state = self.state(t)
        return None if state is None else state[0]

    def add(
        self, start: float, target: float | None, error: float = 0.0, speed: float = 0.0,
        omega: float = 0.0,
    ) -> None:  # fmt: skip
        self.pieces.append((self.made, start, target, error, speed, omega))
        self.made += 1

    def restart(self, now: float, target: float | None) -> None:
        """Stand at ``target`` from ``now`` on, without gliding there."""
        self.pieces.clear()
        self.add(now, target)

    def forget_before(self, t: float) -> None:
        """Drop pieces that end before ``t`` (the one still in force at ``t`` stays)."""
        keep = 0
        for i, piece in enumerate(self.pieces):
            if piece[1] <= t:
                keep = i
        if keep:
            del self.pieces[:keep]


class History:
    """A sensor's gliding value ``RATE`` times a second (None: no reading then),
    newest last; ``last`` is the grid number of the newest point."""

    __slots__ = ("points", "last", "used")

    def __init__(self, size: int) -> None:
        self.points: deque[float | None] = deque(maxlen=size)
        self.last: int | None = None
        self.used = 0.0  # when a graph last asked for it

    def since(self, first: int) -> list[float | None]:
        """The points from grid number ``first`` on (None before the oldest)."""
        if self.last is None:
            return []
        oldest = self.last - len(self.points) + 1
        missing = max(0, min(oldest, self.last + 1) - first)
        skip = max(0, first - oldest)
        return [None] * missing + list(islice(self.points, skip, None))


class Motion:
    """Springs, threshold fades and histories, by sensor key.

    A renderer calls :meth:`begin` once per frame; then its widgets ask for
    gliding values (:meth:`glide`), colour steps (:meth:`fade`) and, for
    graphs in video mode, histories (:meth:`history`). All of it is keyed by
    sensor, so a new theme's widgets find the values where the last theme
    left them. One thread at a time: the main loop's render thread, or a
    copy (:meth:`copy`) for warming up the next theme elsewhere.
    """

    def __init__(self) -> None:
        self.springs: dict[str, Spring] = {}
        self.fades: dict[Any, tuple[float, float]] = {}  # key -> (step, when)
        self.histories: dict[str, History] = {}
        self.previous: float | None = None  # the frame before this one
        self.now: float | None = None  # the frame being drawn
        # The panel light's sweeps and the moments that start them (render.look
        # makes it): they go on from theme to theme too. A copy starts without.
        self.sweeps: Any = None

    # -- frames --------------------------------------------------------------

    def begin(self, readings: dict[str, Any], now: float, glide: float, lead: float) -> None:
        """A frame at ``now``: the histories take their points up to it.

        ``glide`` is how long a value takes to a new reading (0: it jumps),
        ``lead`` the time between two planned frames (0: unknown).
        """
        if now != self.now:
            self.previous, self.now = self.now, now
        last = grid(now)
        for key in list(self.histories):
            history = self.histories[key]
            if now - history.used > KEEP_S:
                del self.histories[key]
                continue
            reading = readings.get(key)
            self.glide(key, number(getattr(reading, "value", None)), now, glide, lead)
            self._record(key, history, last)
        for key, spring in list(self.springs.items()):  # old pieces, and values long gone
            if spring.seen is not None and now - spring.seen > KEEP_S:
                del self.springs[key]
            elif spring.seen is not None:
                spring.forget_before(spring.seen - 1.0)

    def _record(self, key: str, history: History, last: int) -> None:
        spring = self.springs[key]
        if history.last is None or last <= history.last:
            return
        first = max(history.last + 1, last - history.points.maxlen + 1)
        history.points.extend(spring.at(k / RATE) for k in range(first, last + 1))
        history.last = last

    def _fresh(self, seen: float | None, now: float) -> bool:
        """Whether something last drawn at ``seen`` starts again at ``now``: it
        was off the panel for a while (another theme did not show it), so it
        must not catch up a glide nobody saw. A pause of the PNG path between
        frames does not count: the value stood on the panel all along."""
        if seen is None or now < seen:
            return True
        if now - seen <= PAUSE_S:
            return False
        return not (seen == self.previous and now - seen <= IDLE_S)

    # -- values --------------------------------------------------------------

    def glide(
        self, key: str, target: float | None, now: float, glide: float, lead: float = 0.0
    ) -> tuple[float, float] | None:
        """(value, speed) of ``key`` at ``now``, gliding towards ``target``.

        A new reading glides from the frame before (``lead`` earlier, or when
        the value was last drawn), so it moves in the frame it arrives in.
        None stays None: a value without a reading shows as missing.
        """
        spring = self.springs.get(key)
        if spring is None:
            spring = self.springs[key] = Spring()
        if self._fresh(spring.seen, now) or not spring.pieces:
            spring.restart(now, target)
        elif target != spring.target:
            start = spring.seen if lead <= 0 else max(spring.seen, now - lead)
            state = spring.state(start) if target is not None else None
            if target is None:
                spring.add(start, None)
            elif state is None:  # back after a gap: no glide from nothing
                spring.add(now, target)
            else:
                omega = 4.74 / glide if glide > 0 else 0.0  # 95 % of a step after ``glide`` s
                spring.add(start, target, state[0] - target, state[1], omega)
        spring.target, spring.seen = target, now
        return spring.state(now)

    def value(self, key: str, t: float) -> float | None:
        """The gliding value of ``key`` at ``t`` (None if it was not drawn then)."""
        spring = self.springs.get(key)
        return None if spring is None else spring.at(t)

    def start(self, key: str) -> float:
        """When the oldest piece of ``key``'s glide that is kept begins."""
        spring = self.springs.get(key)
        return spring.pieces[0][1] if spring is not None and spring.pieces else -math.inf

    def fade(self, key: Any, step: int, now: float) -> float:
        """The colour step a value shows (0: its own colour, 1: the first
        threshold's, ...), on its way to ``step`` one step per ``FADE_S``:
        crossing a threshold fades the colour, and a bar and a number of the
        same value (the same ``key``) change together."""
        state = self.fades.get(key)
        if state is None or self._fresh(state[1], now):
            shown = float(step)
        else:
            shown, then = state
            move = (now - then) / FADE_S
            if abs(step - shown) <= move:
                shown = float(step)
            else:
                shown += math.copysign(move, step - shown)
        self.fades[key] = (shown, now)
        return shown

    # -- histories -----------------------------------------------------------

    def history(
        self, key: str, size: int, now: float, readings: list[float], last_at: float,
        interval: float, glide: float,
    ) -> History:  # fmt: skip
        """At least ``size`` points of ``key`` up to ``now``.

        A history that is new (or too short) is filled from ``readings``, the
        hub's one value per reading, the last taken at ``last_at``, one each
        ``interval``: as the spring would have glided through them. A theme
        that comes on draws its graphs full at once.
        """
        size = max(2, min(MAX_POINTS, size))
        history = self.histories.get(key)
        if history is not None and history.points.maxlen >= size:
            history.used = now
            return history
        last = grid(now)
        older = None if history is None else history
        history = self.histories[key] = History(size)
        history.used = now
        replay = _replay(readings, last_at, interval, glide)
        spring = self.springs.get(key)
        first = last - size + 1
        known = older.last - len(older.points) + 1 if older and older.last is not None else last + 1
        for k in range(first, min(known, last + 1)):
            t = k / RATE
            live = spring.at(t) if spring is not None and spring.piece(t) is not None else None
            history.points.append(live if live is not None else replay.at(t))
        if older is not None and older.last is not None:
            history.points.extend(older.since(max(first, known)))
            for k in range(older.last + 1, last + 1):
                history.points.append(spring.at(k / RATE) if spring is not None else None)
        history.last = last
        return history

    # -- warming up the next theme -------------------------------------------

    def copy(self) -> Motion:
        """A copy to draw with in another thread (the next theme, warming up)."""
        other = Motion()
        for key, spring in self.springs.items():
            twin = other.springs[key] = Spring()
            twin.pieces, twin.target = list(spring.pieces), spring.target
            twin.seen, twin.made = spring.seen, spring.made
        other.fades = dict(self.fades)
        for key, history in self.histories.items():
            twin = other.histories[key] = History(history.points.maxlen)
            twin.points.extend(history.points)
            twin.last, twin.used = history.last, history.used
        other.previous, other.now = self.previous, self.now
        return other

    def adopt(self, other: Motion) -> None:
        """Take the histories (and their springs) that ``other`` made and this lacks."""
        for key, history in other.histories.items():
            if key not in self.histories:
                self.histories[key] = history
                if key not in self.springs and key in other.springs:
                    self.springs[key] = other.springs[key]


def _replay(readings: list[float], last_at: float, interval: float, glide: float) -> Spring:
    """A spring that went through ``readings``, one each ``interval``, the last at ``last_at``."""
    spring = Spring()
    omega = 4.74 / glide if glide > 0 else 0.0
    start = last_at - (len(readings) - 1) * interval
    for i, reading in enumerate(readings):
        value = number(reading)  # NaN and infinity are a gap, as no reading is
        at = start + i * interval
        state = spring.state(at)
        if value is None or state is None:
            spring.add(at, value)
        else:
            spring.add(at, value, state[0] - value, state[1], omega)
    return spring

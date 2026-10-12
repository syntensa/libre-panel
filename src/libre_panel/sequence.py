"""Screens in turn: themes the panel shows one after another, each for as long as it says,
and from the top again after the last.

``config.toml``::

    [sequence]
    on = true
    [[sequence.steps]]
    theme = "almanac"
    minutes = 5
    [[sequence.steps]]
    theme = "nimbus"
    minutes = 5
    [[sequence.steps]]
    theme = "studio"
    minutes = 10

A step lasts its ``seconds``, ``minutes`` and ``hours`` together (``minutes = 1, seconds =
30``), at least :data:`SHORTEST_S`. The sequence is the panel's normal theme: a game's
mode, the autopilot or a service that shows a theme of its own go first, and the sequence
goes on underneath. It begins with its first step when the panel starts and whenever the
steps change.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

SHORTEST_S = 5  # a step shorter than this would be little more than its transition
LONGEST_S = 7 * 24 * 3600
UNITS = (("hours", 3600), ("minutes", 60), ("seconds", 1))


class StepError(ValueError):
    """A step that cannot be shown: says which and why."""


@dataclass(frozen=True)
class Step:
    """A theme and how long it shows, in seconds."""

    theme: str
    seconds: int

    def to_table(self) -> dict[str, Any]:
        """As config.toml has it, in the largest unit that takes it whole: ``minutes = 5``."""
        for unit, size in UNITS:
            if self.seconds % size == 0:
                return {"theme": self.theme, unit: self.seconds // size}
        return {"theme": self.theme, "seconds": self.seconds}  # (never reached: size 1)


def parse_step(raw: Any, where: str) -> Step:
    """A step of ``[[sequence.steps]]``, checked."""
    if not isinstance(raw, dict):
        raise StepError(f"{where}: expected a table with a theme and a time")
    theme = raw.get("theme")
    if not isinstance(theme, str) or not theme.strip():
        raise StepError(f"{where}: needs a theme")
    unknown = sorted(set(raw) - {"theme", *(unit for unit, _ in UNITS)})
    if unknown:
        raise StepError(f"{where}: unknown key {unknown[0]!r} (theme, seconds, minutes, hours)")
    total = 0.0
    for unit, size in UNITS:
        if unit not in raw:
            continue
        value = raw[unit]
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise StepError(f"{where}.{unit}: expected a number of {unit}")
        total += value * size
    seconds = round(total)
    if not SHORTEST_S <= seconds <= LONGEST_S:
        raise StepError(
            f"{where}: shows {seconds} s; a step lasts {SHORTEST_S} s to {LONGEST_S // 86400} days"
        )
    return Step(theme.strip(), seconds)


def parse_steps(raw: Any, where: str = "sequence.steps") -> list[Step]:
    if not isinstance(raw, list):
        raise StepError(f"{where}: expected a list of steps ([[{where}]])")
    return [parse_step(entry, f"{where}[{n}]") for n, entry in enumerate(raw)]


@dataclass(frozen=True)
class Position:
    """Where a sequence stands: its step (by index), that step's theme and the seconds left
    of it."""

    index: int
    theme: str
    left_s: float


def position(steps: Sequence[Step], elapsed: float) -> Position:
    """The step ``elapsed`` seconds after the sequence began, going round."""
    total = sum(step.seconds for step in steps)
    at = elapsed % total
    for index, step in enumerate(steps):
        if at < step.seconds:
            return Position(index, step.theme, step.seconds - at)
        at -= step.seconds
    last = len(steps) - 1  # (rounding at the very end of a round)
    return Position(last, steps[last].theme, 0.0)


class Rotation:
    """Follows a sequence on the clock: it begins with the first step when it is first asked,
    and again whenever the steps change (a change of anything else in config.toml leaves it
    where it is). While a switch to new steps is made ready, the steps before are asked too:
    they go on where they were, until they have not been asked for a while."""

    FORGET_S = 60.0

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.steps: tuple[Step, ...] = ()
        self.began = 0.0
        self.before: tuple[tuple[Step, ...], float, float] | None = None  # steps, began, asked

    def where(self, steps: Sequence[Step], now: float | None = None) -> Position | None:
        """The step to show now, or None without steps."""
        now = self.clock() if now is None else now
        steps = tuple(steps)
        if not steps:
            return None
        if steps == self.steps:
            began = self.began
        elif self.before and self.before[0] == steps and now - self.before[2] < self.FORGET_S:
            began = self.before[1]
            self.before = (steps, began, now)
        else:
            self.before = (self.steps, self.began, now) if self.steps else None
            self.steps, self.began = steps, now
            began = now
        return position(steps, max(0.0, now - began))

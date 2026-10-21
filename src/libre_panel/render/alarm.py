"""The alarm on every panel: a red edge for a moment when a sensor reaches its critical limit.

The notices service publishes ``alarm`` (1 while a limit of its ``warnings`` is at ``crit``,
and a few seconds after; 0 else). Each panel's loop tells its :class:`AlarmEdge` the reading
on every frame, and the edge pulses round the picture for :data:`SECONDS` from the moment it
turns 1: on all panels at once, as they share the readings. A calm panel (a picture a second)
shows it still, as one picture with the edge and one without.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

SECONDS = 3.0
PULSE_S = 1.0  # one fall and rise of the edge
LEVELS = 12  # strengths the edge is kept in, made once each
COLOUR = "#FF3B5C"  # for a theme without a "crit" colour
WIDTH = 0.035  # of the shorter side
STRONGEST = 235  # the edge's opacity at the rim


class AlarmEdge:
    def __init__(self) -> None:
        self.on = False
        self.began: float | None = None
        self._made: dict[tuple[tuple[int, int], int], Image.Image] = {}

    def follow(self, on: bool, now: float) -> None:
        """The alarm reading on this frame: the edge begins when it turns on."""
        if on and not self.on:
            self.began = now
        self.on = on

    def showing(self, now: float) -> bool:
        return self.began is not None and 0.0 <= now - self.began < SECONDS

    def draw(self, frame: Image.Image, now: float, colour: str | None, still: bool) -> Image.Image:
        """``frame`` with the edge while it shows (a new picture), else ``frame`` itself."""
        if not self.showing(now):
            if self.began is not None and now - self.began >= SECONDS:
                self.began, self._made = None, {}  # (the masks go with it)
            return frame
        assert self.began is not None
        if still:
            strength = 1.0
        else:  # strong at once, then softer and strong again, once a pulse
            strength = 0.6 + 0.4 * math.cos(2 * math.pi * (now - self.began) / PULSE_S)
        level = max(1, round(strength * LEVELS))
        mask = self._mask(frame.size, level)
        out = frame.copy()
        out.paste(colour or COLOUR, (0, 0, *frame.size), mask)
        return out

    def _mask(self, size: tuple[int, int], level: int) -> Image.Image:
        key = (size, level)
        mask = self._made.get(key)
        if mask is None:
            mask = self._made[key] = _edge(size, STRONGEST * level / LEVELS)
        return mask


def _edge(size: tuple[int, int], strongest: float) -> Image.Image:
    """A mask that is ``strongest`` at the rim and fades to nothing inwards."""
    width = max(3, round(min(size) * WIDTH))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    w, h = size
    for inset in range(width):
        if inset * 2 >= min(w, h):
            break
        fade = round(strongest * (1.0 - inset / width) ** 1.6)
        draw.rectangle((inset, inset, w - 1 - inset, h - 1 - inset), outline=fade)
    return mask

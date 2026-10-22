"""The sign of an AI agent at work (the ``agent`` widget): drawn in Libre Panel's own look, no
maker's logo. Working: three lights run round a faint ring, the first bright, the others
fading behind it. Waiting for you: the ring in the waiting colour breathes, a light in its
middle with it. Idle: the ring, calm, one light resting at its top. Off: the ring, faint.

``phase`` (0 to 1) is where the motion is; a working sign goes round once a ``ROUND_S``,
a waiting one breathes once a ``BREATH_S``. Pictures are kept by what they show, so a sign
that goes round costs a lookup after its first round.
"""

from __future__ import annotations

import math
from collections import OrderedDict

from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]

ROUND_S = 1.6
BREATH_S = 2.4
STEPS = 48  # pictures a round (or a breath)
SUPERSAMPLE = 4
KEEP = 384
_CACHE: OrderedDict[tuple, Image.Image] = OrderedDict()


def step(state: str, now: float) -> int | None:
    """The picture of the motion at ``now`` (seconds), None for a sign that stands still."""
    if state == "working":
        return int(now / ROUND_S * STEPS) % STEPS
    if state == "waiting":
        return int(now / BREATH_S * STEPS) % STEPS
    return None


def _alpha(color: RGBA, share: float) -> RGBA:
    return (*color[:3], max(0, min(255, round(color[3] * share))))


def picture(size: int, state: str, at: int | None, color: RGBA, waiting: RGBA,
            muted: RGBA) -> Image.Image:  # fmt: skip
    """The sign, ``size`` square, for ``state`` (working, waiting, idle, off) at step ``at``."""
    key = (size, state, at, color, waiting, muted)
    found = _CACHE.get(key)
    if found is not None:
        _CACHE.move_to_end(key)
        return found
    image = _draw(size, state, (at or 0) / STEPS, color, waiting, muted)
    _CACHE[key] = image
    while len(_CACHE) > KEEP:
        _CACHE.popitem(last=False)
    return image


def _draw(size: int, state: str, phase: float, color: RGBA, waiting: RGBA,
          muted: RGBA) -> Image.Image:  # fmt: skip
    s = SUPERSAMPLE
    k = max(8, size) * s
    layer = Image.new("RGBA", (k, k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    c = k / 2
    r = k * 0.36
    width = max(s, round(k * 0.055))

    def ring(fill: RGBA, w: float = width) -> None:
        draw.ellipse([c - r, c - r, c + r, c + r], outline=fill, width=max(1, round(w)))

    def light(angle: float, radius: float, fill: RGBA, at: float = r) -> None:
        x, y = c + at * math.sin(angle), c - at * math.cos(angle)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)

    dot = k * 0.075
    if state == "working":
        ring(_alpha(color, 0.22))
        head = 2 * math.pi * phase
        for i, share in enumerate((1.0, 0.55, 0.28)):
            light(head - i * 0.42, dot * (1 - 0.18 * i), _alpha(color, share))
        light(0, k * 0.05 * (0.8 + 0.2 * math.cos(2 * math.pi * phase)), _alpha(color, 0.5), 0)
    elif state == "waiting":
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * phase)  # 0 to 1 and back
        ring(_alpha(waiting, 0.55 + 0.45 * breath), width * (1 + 0.6 * breath))
        light(0, k * (0.07 + 0.05 * breath), _alpha(waiting, 0.6 + 0.4 * breath), 0)
    elif state == "idle":
        ring(_alpha(color, 0.6))
        light(0, dot * 0.85, _alpha(color, 0.9))
    else:
        ring(_alpha(muted, 0.45), width * 0.8)
    return layer.resize((max(8, size), max(8, size)), Image.Resampling.LANCZOS)

"""The sun's way over the sky today (the ``sunpath`` widget): an arc over the horizon from
sunrise to sunset, the part of the day gone in the sun's light and the part to come dotted,
the sun where it stands, glowing. By night the arc lies dim and the moon's crescent stands
over it.

Drawn four times the size and reduced; the horizon, the dotted way and the night are kept by
size and colours, the day gone and the sun drawn for each of :data:`STEPS` steps of the day
(about a minute and a half each), so a panel's frame mostly finds its picture kept.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OVER = 4
STEPS = 500  # the day in steps: the sun moves one about every minute and a half


def step(progress: Any) -> int | None:
    """The step of the day ``progress`` (0 at sunrise, 1 at sunset) is in; None by night."""
    try:
        p = float(progress)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p) or p < 0 or p > 1:
        return None
    return round(p * STEPS)


class _Way:
    """Where the arc lies on a picture of w x h (px, :data:`OVER` times them when drawn)."""

    def __init__(self, w: int, h: int, thickness: float) -> None:
        self.w, self.h = w, h
        self.sun = max(2.5, min(h * 0.13, w * 0.05))  # the sun's radius
        self.line = max(1.0, thickness)
        self.base = h - max(self.line, 1.0) - 0.5  # the horizon
        self.a = w / 2 - self.sun - 1  # half the arc's width
        self.b = self.base - self.sun * 1.6  # its height over the horizon
        self.cx = w / 2

    def at(self, p: float) -> tuple[float, float]:
        """The point of the way at ``p`` of the day (0: sunrise, at the left)."""
        angle = math.pi * (1 - p)
        return self.cx + self.a * math.cos(angle), self.base - self.b * math.sin(angle)

    def points(self, p0: float, p1: float, n: int) -> list[tuple[float, float]]:
        return [self.at(p0 + (p1 - p0) * i / n) for i in range(n + 1)]


def _rgba(colour: Any, alpha: float = 1.0) -> tuple[int, int, int, int]:
    return (int(colour[0]), int(colour[1]), int(colour[2]), round(255 * alpha * colour[3] / 255))


def _lighter(colour: Any, share: float) -> tuple[int, int, int, int]:
    return (*(round(c + (255 - c) * share) for c in colour[:3]), int(colour[3]))  # type: ignore[return-value]


def _reduced(image: Image.Image) -> Image.Image:
    return image.convert("RGBa").reduce(OVER).convert("RGBA")


def ground(w: int, h: int, thickness: float, track: Any) -> Image.Image:
    """The horizon (fading out at its ends) and the day's way, dotted, in ``track``."""
    way = _Way(w, h, thickness)
    image = Image.new("RGBA", (w * OVER, h * OVER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # the horizon: a hairline that fades towards its ends
    y = way.base * OVER
    line = max(1, round(way.line * 0.7 * OVER))
    steps = 24
    for i in range(steps):
        u0, u1 = i / steps, (i + 1) / steps
        fade = math.sin(math.pi * (u0 + u1) / 2) ** 0.6
        draw.line([(u0 * w * OVER, y), (u1 * w * OVER, y)], fill=_rgba(track, 0.9 * fade),
                  width=line)  # fmt: skip
    # the way: dots a few line widths apart
    length = math.pi * (way.a + way.b) / 2  # (about: an ellipse's half)
    count = max(6, round(length / (way.line * 3.2)))
    r = way.line * 0.62 * OVER
    for i in range(count + 1):
        px, py = way.at(i / count)
        draw.ellipse([px * OVER - r, py * OVER - r, px * OVER + r, py * OVER + r],
                     fill=_rgba(track))  # fmt: skip
    return _reduced(image)


def day(w: int, h: int, thickness: float, progress: float, colour: Any,
        deep: Any) -> Image.Image:  # fmt: skip
    """The day gone (sunrise to ``progress``) as a line from ``deep`` to ``colour`` in its own
    soft light, and the sun where it stands, glowing."""
    way = _Way(w, h, thickness)
    big = Image.new("RGBA", (w * OVER, h * OVER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    n = max(2, round(48 * progress))
    pts = way.points(0.0, progress, n)
    width = max(1, round(way.line * OVER))
    for i in range(n):
        t = (i + 0.5) / n
        c = tuple(round(deep[j] + (colour[j] - deep[j]) * t) for j in range(3))
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        draw.line([(x0 * OVER, y0 * OVER), (x1 * OVER, y1 * OVER)], fill=(*c, 255), width=width)
        draw.ellipse([x1 * OVER - width / 2, y1 * OVER - width / 2, x1 * OVER + width / 2,
                      y1 * OVER + width / 2], fill=(*c, 255))  # fmt: skip
    x0, y0 = pts[0]
    draw.ellipse([x0 * OVER - width / 2, y0 * OVER - width / 2, x0 * OVER + width / 2,
                  y0 * OVER + width / 2], fill=_rgba(deep))  # fmt: skip
    line = _reduced(big)
    # its light: the line blurred, under it
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    glow = line.filter(ImageFilter.GaussianBlur(max(1.0, way.line * 1.6)))
    alpha = glow.getchannel("A").point(lambda v: v * 3 // 5)
    glow.putalpha(alpha)
    out.alpha_composite(glow)
    out.alpha_composite(line)
    # the sun: a soft corona, a disc lighter at its top left
    sx, sy = way.at(progress)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    d = np.hypot(xs - sx, ys - sy)
    corona = 0.55 * np.clip(1 - (d - way.sun) / (way.sun * 2.4), 0, 1) ** 2
    disc = np.clip(way.sun + 0.5 - d, 0, 1)
    light = np.array(_lighter(colour, 0.35)[:3], np.float32) / 255
    base = np.array(colour[:3], np.float32) / 255
    t = np.clip((ys - (sy - way.sun)) / (2 * way.sun), 0, 1)[..., None]
    rgb = (light * (1 - t) + base * t) * disc[..., None] + light * (1 - disc[..., None])
    alpha = np.maximum(disc, corona)
    sun = np.zeros((h, w, 4), np.float32)
    sun[..., :3] = rgb
    sun[..., 3] = alpha
    out.alpha_composite(Image.fromarray(np.clip(sun * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA"))
    return out


def night(w: int, h: int, thickness: float, colour: Any) -> Image.Image:
    """The moon's crescent over the middle of the way, in ``colour``, softly lit."""
    way = _Way(w, h, thickness)
    x, y = way.at(0.5)
    r = way.sun * 1.05
    big = Image.new("L", (w * OVER, h * OVER), 0)
    draw = ImageDraw.Draw(big)
    R, X, Y = r * OVER, x * OVER, y * OVER
    draw.ellipse([X - R, Y - R, X + R, Y + R], fill=255)
    draw.ellipse([X - 0.35 * R, Y - 1.15 * R, X + 1.65 * R, Y + 0.85 * R], fill=0)
    mask = big.reduce(OVER)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    halo = Image.new("RGBA", (w, h), _rgba(colour, 0.35))
    halo.putalpha(mask.filter(ImageFilter.GaussianBlur(max(1.0, r * 0.8))).point(lambda v: v // 3))
    out.alpha_composite(halo)
    moon = Image.new("RGBA", (w, h), _rgba(colour))
    moon.putalpha(mask)
    out.alpha_composite(moon)
    return out

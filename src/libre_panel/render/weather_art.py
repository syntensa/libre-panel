"""The shapes of the weather symbols and their two finishes.

The outlines of the cloud, the cloud behind it, the bolt and the moon's crescent are
Meteocons' ("fill", by Bas Milius, MIT licence: see below and assets/NOTICE.md), drawn here at
any size from their paths in Meteocons' view box of :data:`UNIT` units. The sun, its rays, the
drops, the snow crystals, the bands of fog and the stars are drawn from their measures, in the
same units.

``flat`` gives each shape its colour. ``glass`` finishes it as frosted glass lit from above:
its fill lighter at the top, a fine edge of light along the top of its outline and a breath of
shade in its floor; a sun's core lighter towards the top left, its rays lighter at their roots;
a bolt that glows. A cloud is milky, not clear: what lies behind it shows through it only as
frosted glass lets light through, its glow and never its shape (symbols.compose lays that glow
on the cloud where the cloud is at each step).

Polygons are drawn four times the size and reduced (a sixteenth of a pixel's coverage); the
rest is worked out on the pixels in floating point. A whole set takes a few milliseconds.

Meteocons: MIT License, Copyright (c) 2020-present Bas Milius. Permission is hereby granted,
free of charge, to any person obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without restriction, including without
limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions: The above copyright notice and this permission notice
shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS
PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

from __future__ import annotations

import math
import re
from functools import cache
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

UNIT = 128.0  # Meteocons' view box: every measure here is in its units
OVER = 4  # polygons are drawn this much larger and reduced
# the shapes a seventh larger than the view box holds them, about its middle: Meteocons leaves
# room round its icons, a panel's symbol is filled as the earlier ones filled it
ZOOM = 1.14

# Meteocons' outlines (fill set, 3.0): the cloud, the one behind it (overcast), the bolt
# (thunderstorms), the moon of a clear night and the smaller one of a cloudy night
PATHS = {
    "cloud": "M55.262 48.475c4.86-7.864 15.035-11.095 23.553-7.532 8.506 3.56 13.323 13.06 "
    "11.088 22.022l-.161.65.669-.03c7.01-.306 13.089 5.407 13.089 12.443 0 6.811-5.728 12.472"
    "-12.523 12.472H37.954c-6.826.002-12.751-5.33-13.395-12.14-.643-6.808 4.178-13.148 10.884"
    "-14.415l.483-.092-.084-.484c-.816-4.745 1.284-9.652 5.263-12.356 3.99-2.712 9.34-2.86 "
    "13.475-.373l.423.255z",
    "back": "M83.84 48.693c2.404-3.735 7.375-5.164 11.478-3.516 4.043 1.624 6.496 6.012 5.392 "
    "10.26l-.17.653.675-.029c3.281-.137 6.285 2.404 6.285 5.713 0 3.202-2.831 5.726-6.011 "
    "5.726H74.977c-3.21 0-6.132-2.382-6.448-5.593-.315-3.2 2.088-6.066 5.235-6.636l.491-.09"
    "-.088-.49c-.394-2.198.645-4.442 2.518-5.664 1.925-1.256 4.492-1.32 6.483-.17l.413.237z",
    "bolt": "m71.173 68.5-7.616 14.541-.384.731h11.829l-18.05 24.12 3.537-16.88.127-.603h-7.912"
    "L60.355 68.5z",
    "moon": "M60.302 32.582C55.282 53.7 73.6 74.348 95.325 72.515 91.52 85.77 79.2 95.5 64.536 "
    "95.5 46.837 95.5 32.5 81.344 32.5 63.898c0-16.03 12.107-29.27 27.802-31.316",
    "moon_small": "M35.115 34.595c-1.737 8.568 5.638 16.62 14.198 16.188-1.64 5.05-6.424 8.717"
    "-12.095 8.717-7.03 0-12.718-5.621-12.718-12.541 0-6.214 4.588-11.375 10.615-12.364",
}

_TOKEN = re.compile(r"[MmLlHhVvCcSsZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
STEPS = 20  # straight pieces a curve is cut into: under a tenth of a pixel off at 400 px


@cache
def outline(name: str) -> tuple[np.ndarray, ...]:
    """The subpaths of path ``name`` (absolute and relative M, L, H, V, C, S and Z, all that
    Meteocons' outlines here use), each a polygon in units, its curves cut into :data:`STEPS`
    pieces."""
    tokens = _TOKEN.findall(PATHS[name])
    out: list[np.ndarray] = []
    points: list[tuple[float, float]] = []
    x = y = 0.0
    start = (0.0, 0.0)
    control: tuple[float, float] | None = None  # the last curve's second control point
    i, command = 0, "M"

    def number() -> float:
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
        relative, c = command.islower(), command.upper()
        ox, oy = (x, y) if relative else (0.0, 0.0)
        if c == "Z":
            if points:
                out.append(np.array(points, np.float64))
                points = []
            (x, y), control = start, None
        elif c == "M":
            if points:
                out.append(np.array(points, np.float64))
            x, y = ox + number(), oy + number()
            points, start, control = [(x, y)], (x, y), None
            command = "l" if relative else "L"  # pairs after a move are lines
        elif c in "LHV":
            if c == "L":
                x, y = ox + number(), oy + number()
            elif c == "H":
                x = ox + number()
            else:
                y = oy + number()
            points.append((x, y))
            control = None
        else:  # C, S: a cubic curve
            if c == "C":
                c1 = (ox + number(), oy + number())
            else:
                c1 = (2 * x - control[0], 2 * y - control[1]) if control else (x, y)
            c2 = (ox + number(), oy + number())
            end = (ox + number(), oy + number())
            t = np.linspace(0, 1, STEPS + 1)[1:, None]
            u = 1 - t
            curve = u**3 * (x, y) + 3 * u * u * t * c1 + 3 * u * t * t * c2 + t**3 * end
            points += [tuple(p) for p in curve]
            control, (x, y) = c2, end
    if points:
        out.append(np.array(points, np.float64))
    return tuple(out)


# -- coverage -----------------------------------------------------------------------------------


def px(k: int) -> float:
    """Pixels a unit, on a ``k`` px symbol."""
    return k * ZOOM / UNIT


def at(k: int, v: float) -> float:
    """Where ``v`` units along (or down) lies on a ``k`` px symbol, in pixels."""
    return k / 2 + (v - UNIT / 2) * px(k)


def _canvas(k: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A clear canvas :data:`OVER` times ``k`` px for coverage."""
    image = Image.new("L", (k * OVER, k * OVER), 0)
    return image, ImageDraw.Draw(image)


def _reduced(image: Image.Image) -> np.ndarray:
    return np.asarray(image.reduce(OVER), np.float32) * (1 / 255)


def cover(name: str, k: int, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    """How much of each pixel of a ``k`` px picture path ``name`` covers (0 to 1), drawn
    ``scale`` times its size about the middle of the view box and moved by (dx, dy) units."""
    image, draw = _canvas(k)
    mid, s = UNIT / 2, px(k)
    for poly in outline(name):
        q = (k / 2 + ((poly - mid) * scale + (dx, dy)) * s) * OVER
        draw.polygon([tuple(p) for p in q], fill=255)
    return _reduced(image)


def strokes(k: int, lines: list[tuple[float, float, float, float]], width: float) -> np.ndarray:
    """The coverage of straight strokes ``width`` units wide with round ends, each from
    (x0, y0) to (x1, y1) units."""
    image, draw = _canvas(k)
    w = width * px(k) * OVER
    for line in lines:
        x0, y0, x1, y1 = (at(k, v) * OVER for v in line)
        draw.line([(x0, y0), (x1, y1)], fill=255, width=max(1, round(w)))
        for x, y in ((x0, y0), (x1, y1)):
            draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=255)
    return _reduced(image)


def disc(k: int, cx: float, cy: float, r: float) -> tuple[np.ndarray, np.ndarray]:
    """A disc's coverage, worked out exactly for each pixel, and each pixel's distance from its
    outline (px, less than 0 inside)."""
    ys, xs = np.mgrid[0:k, 0:k].astype(np.float32) + 0.5
    d = np.hypot(xs - at(k, cx), ys - at(k, cy)) - r * px(k)
    return np.clip(0.5 - d, 0, 1), d


def blurred(cov: np.ndarray, radius: float) -> np.ndarray:
    """``cov`` blurred (Pillow's Gaussian of ``radius`` px)."""
    image = Image.fromarray(np.clip(cov * 255 + 0.5, 0, 255).astype(np.uint8), "L")
    return np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), np.float32) * (1 / 255)


def _shifted(cov: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """``cov`` moved by (dx, dy) px (0 or more each, in fractions), clear where it came from."""
    out = cov
    for axis, by in ((0, dy), (1, dx)):
        whole, part = int(by), by - int(by)
        moved = np.zeros_like(out)
        for step, share in ((whole, 1 - part), (whole + 1, part)):
            if share <= 0 or step >= out.shape[axis]:
                continue
            if axis == 0:
                moved[step:] += share * out[: out.shape[0] - step]
            else:
                moved[:, step:] += share * out[:, : out.shape[1] - step]
        out = moved
    return out


def _ring(cov: np.ndarray) -> np.ndarray:
    """The outline's own pixels: what a pixel's step inwards takes away."""
    p = np.pad(cov, 1)
    inner = np.minimum.reduce([p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2],
                               p[1:-1, 2:]])  # fmt: skip
    return np.clip(cov - inner, 0, 1)


# -- colour -------------------------------------------------------------------------------------


def unit(colour: Any) -> np.ndarray:
    """A colour's red, green and blue as 0 to 1."""
    return np.asarray(colour[:3], np.float32) / 255


def lighter(colour: Any, share: float) -> np.ndarray:
    """A colour ``share`` of the way to white (0 to 1 a channel)."""
    c = unit(colour)
    return c + (1 - c) * share


def picture(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    """A picture (RGBA) of colours (0 to 1, any shape that broadcasts to alpha's with three
    channels) and coverage."""
    out = np.empty((*alpha.shape, 4), np.float32)
    out[..., :3] = rgb
    out[..., 3] = alpha
    return Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")


def flat(cov: np.ndarray, colour: Any) -> Image.Image:
    """``cov`` in a colour."""
    return picture(unit(colour), cov)


def _rows(cov: np.ndarray) -> tuple[float, float]:
    """The first and the last row (px, the bottom one past it) where ``cov`` covers anything."""
    rows = np.flatnonzero(cov.max(axis=1) > 0.01)
    return (0.0, float(len(cov))) if rows.size == 0 else (float(rows[0]), float(rows[-1] + 1))


def glass(cov: np.ndarray, colour: Any, k: int, lift: float = 0.78, deep: float = 1.0,
          rim: float = 0.9) -> Image.Image:  # fmt: skip
    """``cov`` as frosted glass in a colour, lit from above: ``lift`` of the way to white at its
    top, the colour times ``deep`` at its bottom, a breath of shade inside its floor and an edge
    of light (``rim``) just inside the top of its outline, faintly all round."""
    base = unit(colour)
    top, bottom = _rows(cov)
    t = np.clip((np.arange(len(cov), dtype=np.float32) + 0.5 - top) / max(1.0, bottom - top),
                0, 1)[:, None, None]  # fmt: skip
    rgb = (base + (1 - base) * lift) * (1 - t) + base * deep * t
    # the floor: a breath of shade where its outline is near, in its lower half
    near = np.clip(2 * (1 - blurred(cov, max(1.0, k * 0.03))), 0, 1) * cov
    floor = np.clip((t[..., 0] - 0.55) / 0.45, 0, 1) * near
    rgb = rgb * (1 - 0.05 * floor[..., None])
    # the edge of light: the outline's upper pixels, a fine line (thinner where it slopes)
    w = max(0.7, k * 0.008)
    edge = np.maximum(np.clip(cov - _shifted(cov, 0, w), 0, 1), 0.18 * _ring(cov)) * rim
    rgb = rgb * (1 - edge[..., None]) + (base + (1 - base) * 0.92) * edge[..., None]
    return picture(rgb, cov)


# -- the sky --------------------------------------------------------------------------------------


def sun_core(k: int, cx: float, cy: float, r: float, colour: Any, lit: bool) -> Image.Image:
    """The sun's disc at (cx, cy) units, ``r`` units round: lit, lighter at its top and towards
    its top left, a fine deeper edge; else in its colour."""
    cov, d = disc(k, cx, cy, r)
    if not lit:
        return flat(cov, colour)
    s = px(k)
    base = unit(colour)
    ys, xs = np.mgrid[0:k, 0:k].astype(np.float32) + 0.5
    t = np.clip((ys - at(k, cy - r)) / (2 * r * s), 0, 1)[..., None]
    rgb = (base + (1 - base) * 0.38) * (1 - t) + base * t
    spot = np.exp(-((xs - at(k, cx - 0.35 * r)) ** 2 + (ys - at(k, cy - 0.4 * r)) ** 2)
                  / (2 * (0.55 * r * s) ** 2))  # fmt: skip
    rgb = rgb + (1 - rgb) * (0.28 * spot)[..., None]
    edge = np.clip(1 - np.abs(d + 0.6) / max(0.6, k * 0.006), 0, 1)
    return picture(rgb * (1 - 0.12 * edge)[..., None], cov)


def rays(k: int, cx: float, cy: float, inner: float, outer: float, width: float, colour: Any,
         lit: bool) -> Image.Image:  # fmt: skip
    """The sun's eight rays round (cx, cy), from ``inner`` to ``outer`` units off its middle,
    ``width`` units wide with round ends; lit, a little lighter at their roots."""
    lines = []
    for i in range(8):
        a = i * math.pi / 4 - math.pi / 2
        c, s_ = math.cos(a), math.sin(a)
        lines.append((cx + inner * c, cy + inner * s_, cx + outer * c, cy + outer * s_))
    cov = strokes(k, lines, width)
    if not lit:
        return flat(cov, colour)
    ys, xs = np.mgrid[0:k, 0:k].astype(np.float32) + 0.5
    away = np.hypot(xs - at(k, cx), ys - at(k, cy)) / px(k)
    away = np.clip((away - inner) / max(1.0, outer - inner), 0, 1)[..., None]
    base = unit(colour)
    return picture((base + (1 - base) * 0.22) * (1 - away) + base * away, cov)


def corona(k: int, cx: float, cy: float, r: float, colour: Any) -> Image.Image:
    """The light round the sun: soft, from under its disc out to over three times it."""
    _cov, d = disc(k, cx, cy, r)
    away = d / (r * px(k))
    return picture(lighter(colour, 0.15), 0.32 * np.clip(1 - away / 2.2, 0, 1) ** 2.2)


def glow(cov: np.ndarray, colour: Any, radius: float, strength: float) -> Image.Image:
    """Light round ``cov``: its coverage blurred by ``radius`` px, ``strength`` at most."""
    return picture(unit(colour), blurred(cov, radius) * strength)


def moon(k: int, name: str, colour: Any, lit: bool, scale: float = 1.0, dx: float = 0.0,
         dy: float = 0.0) -> tuple[Image.Image, np.ndarray]:  # fmt: skip
    """The moon's crescent (path ``name``) and its coverage: lit, lighter at its top, with an
    edge of light along its outer side and a soft light round it; else in its colour."""
    cov = cover(name, k, scale, dx, dy)
    if not lit:
        return flat(cov, colour), cov
    base = unit(colour)
    top, bottom = _rows(cov)
    t = np.clip((np.arange(k, dtype=np.float32) + 0.5 - top) / max(1.0, bottom - top),
                0, 1)[:, None, None]  # fmt: skip
    rgb = (base + (1 - base) * 0.55) * (1 - t) + base * 0.92 * t
    w = max(0.7, k * 0.007)
    edge = np.clip(cov - _shifted(cov, 0.6 * w, 0.8 * w), 0, 1) * 0.8  # (lit from the top left)
    rgb = rgb * (1 - edge[..., None]) + edge[..., None]
    out = glow(cov, lighter(colour, 0.5), max(1.0, k * 0.05), 0.35)
    out.alpha_composite(picture(rgb, cov))
    return out, cov


def star(k: int, x: float, y: float, r: float, colour: Any, lit: bool) -> Image.Image:
    """A star of four points at (x, y) units, ``r`` units from its middle to a point: lit, its
    points fine and its middle soft; else a plain one."""
    if not lit:
        image, draw = _canvas(k)
        a, q = 0.28 * r * px(k) * OVER, r * px(k) * OVER
        cx, cy = at(k, x) * OVER, at(k, y) * OVER
        draw.polygon([(cx, cy - q), (cx + a, cy - a), (cx + q, cy), (cx + a, cy + a),
                      (cx, cy + q), (cx - a, cy + a), (cx - q, cy), (cx - a, cy - a)],
                     fill=255)  # fmt: skip
        return flat(_reduced(image), colour)
    ys, xs = np.mgrid[0:k, 0:k].astype(np.float32) + 0.5
    ax, ay = np.abs(xs - at(k, x)), np.abs(ys - at(k, y))
    rr = max(r * px(k), 1.2)
    d = np.minimum(ax * 3.2 + ay * 0.55, ay * 3.2 + ax * 0.55) - rr
    cov = np.clip(0.5 - d / 1.2, 0, 1) * np.exp(-np.hypot(ax, ay) / (rr * 1.4))
    return picture(lighter(colour, 0.4), np.clip(cov * 1.6, 0, 1))


# -- what falls -----------------------------------------------------------------------------------


def drop(k: int, length: float, top: float, bottom: float, colour: Any, lit: bool,
         faint: float = 0.1) -> Image.Image:  # fmt: skip
    """A drop on a picture of its own, its top ``top`` units wide and its foot ``bottom``, its
    length (and its foot's middle at the picture's) in units: lit, a streak that tapers upwards,
    lighter and ``faint`` at its top; else a plain stroke of its foot's width."""
    s = px(k)
    w = max(3, math.ceil((bottom + 2) * s))
    h = max(4, math.ceil((length + bottom + 2) * s))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    x0, y0, y1 = w / 2, (1 + bottom / 2) * s, (1 + bottom / 2 + length) * s
    t = np.clip((ys - y0) / max(y1 - y0, 1e-6), 0, 1)
    half = ((top + (bottom - top) * t) if lit else np.full_like(t, bottom)) * s / 2
    d = np.hypot(xs - x0, ys - (y0 + t * (y1 - y0))) - np.maximum(half, 0.35)
    cov = np.clip(0.5 - d, 0, 1)
    if not lit:
        return flat(cov, colour)
    base = unit(colour)
    rgb = (base + (1 - base) * 0.35) * (1 - t[..., None]) + base * t[..., None]
    return picture(rgb, cov * (faint + (1 - faint) * t**0.8))


def flake(side: float, size: float, width: float, colour: Any, branched: bool) -> Image.Image:
    """A snow crystal of six arms ``size`` px across, its arms ``width`` px wide, in the middle
    of a picture ``side`` px (it turns as it falls): branched, two pairs of branches an arm;
    else three plain strokes crossing."""
    n = max(3, math.ceil(side))
    image = Image.new("L", (n * OVER, n * OVER), 0)
    draw = ImageDraw.Draw(image)
    mid, arm = n * OVER / 2, size * OVER / 2
    thick = max(1, round(width * OVER))

    def stroke(x0: float, y0: float, x1: float, y1: float, w: int) -> None:
        draw.line([(x0, y0), (x1, y1)], fill=255, width=w)
        for x, y in ((x0, y0), (x1, y1)):
            draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=255)

    for i in range(6):
        a = i * math.pi / 3 - math.pi / 2
        tip = (mid + arm * math.cos(a), mid + arm * math.sin(a))
        stroke(mid, mid, *tip, thick)
        if not branched:
            continue
        for at, length in ((0.45, 0.32), (0.72, 0.22)):
            bx, by = mid + arm * at * math.cos(a), mid + arm * at * math.sin(a)
            for turn in (-math.pi / 3, math.pi / 3):
                ex, ey = (
                    bx + arm * length * math.cos(a + turn),
                    by + arm * length * math.sin(a + turn),
                )
                stroke(bx, by, ex, ey, max(1, round(thick * 0.8)))
    return flat(_reduced(image), colour)


def band(k: int, x0: float, x1: float, y: float, width: float, colour: Any, opacity: float,
         lit: bool) -> Image.Image:  # fmt: skip
    """A band of fog from x0 to x1 at y (units), ``width`` units thick: lit, a veil a little
    lighter than its colour, soft at its edges and fading out towards its ends; else a plain
    stroke with round ends."""
    if not lit:
        return flat(strokes(k, [(x0, y, x1, y)], width) * opacity, colour)
    s = px(k)
    ys, xs = np.mgrid[0:k, 0:k].astype(np.float32) + 0.5
    left, right = at(k, x0), at(k, x1)
    d = np.hypot(xs - np.clip(xs, left, right), ys - at(k, y)) - width * s / 2
    soft = np.clip(0.5 - d / max(1.0, width * s * 0.45), 0, 1)
    u = np.clip((xs - left) / (right - left), 0, 1)
    ends = np.clip(np.minimum(u, 1 - u) / 0.34, 0, 1) ** 1.6
    return picture(lighter(colour, 0.2), soft * ends * opacity)


def bolt(k: int, colour: Any, lit: bool, flash: bool = True, dy: float = 0.0) -> Image.Image:
    """The bolt (moved down by ``dy`` units) as it flashes (``flash``), or between flashes, a
    little deeper: lit, lighter at its top, glowing as it flashes; else in its colour, lighter
    as it flashes."""
    cov = cover("bolt", k, dy=dy)
    base = unit(colour)
    if not lit:
        return picture(lighter(colour, 0.35) if flash else base * 0.85, cov)
    rows = np.arange(k, dtype=np.float32) + 0.5
    t = np.clip((rows - at(k, 68 + dy)) / (40 * px(k)), 0, 1)[:, None, None]
    if not flash:
        return picture((base + (1 - base) * 0.15) * (1 - t) + base * 0.9 * t, cov)
    rgb = (base + (1 - base) * 0.65) * (1 - t) + (base + (1 - base) * 0.3) * t
    out = glow(cov, lighter(colour, 0.4), max(1.0, k * 0.035), 0.75)
    out.alpha_composite(picture(rgb, cov))
    return out

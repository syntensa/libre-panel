"""Facets: shapes with a flat top in their own colour and a sharp 45-degree bevel
along their edges, lit from the top left (after SPUR II's "SPUR II" wordmark).

A letter keeps its outline exactly (the same pixels as the flat text); the
bevel lies inside it, and where it faces the light it is lighter and cooler,
where it faces away darker and warmer. The flat top is the exact colour.

Everything is built to be cached. What costs is the geometry of a glyph (a
distance transform of its mask), and that depends on the font, its size and
the character only; colouring it is a few array operations on a few hundred
pixels. A bar is its two ends and one column repeated, a ring its band of
pixels, a curve one column of shading per pixel of width.

The shading is affine in the colour: ``rgb = colour * A + B``, with ``A`` and
``B`` set by the direction a pixel faces and nothing else. So the geometry is
kept without a colour, and a gradient or a threshold colour costs no more
than a plain one.
"""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from libre_panel.render import light

RGB = tuple[int, int, int]

# The light: from the top left and in front, as SPUR II's guide light (image axes: x to
# the right, y down, z to the viewer). A flat top faces the viewer: ``LIGHT_TOP``.
LIGHT = np.array([-16.0, -16.0, 24.0], np.float32)
LIGHT /= np.linalg.norm(LIGHT)
LIGHT_TOP = float(LIGHT[2])
SIN45 = math.sin(math.radians(45))

# How far a facet lightens towards white where it faces the light (the first number)
# and how far it darkens where it faces away (the second), per kind of shape
TEXT = (0.85, 0.88)  # lettering: a light touch, the letters are nearly white anyway
SOLID = (1.35, 0.16)  # bars and rings: narrow, so a hard contrast reads as an edge
GROOVE = (0.20, 0.55)  # a milled channel is background: it hints at an edge, no more
RIDGE = (0.85, 0.55)  # the bright flank of a curve's ridge ...
RIDGE_FAR = (0.85, 0.04)  # ... the flank away from the light, nearly black ...
RIDGE_BAND = (0.85, 0.30)  # ... and the band beneath

# Lettering: the bevel's width grows with the font, but never eats a thin stroke
MIN_SIZE = 20  # smaller text stays flat: a bevel there is only mush
BEVEL_SCALE, BEVEL_MIN, BEVEL_MAX = 0.06, 1.2, 5.0
STROKE_SHARE = 0.12  # at most this much of the local half-stroke becomes bevel
TINT_STRENGTH = 0.60  # how far a bevel is tinted cool (to the light) or warm (away)
SUPER = 4  # glyph geometry is worked out this much larger

GLYPHS = 1500  # glyph geometries kept
PATCHES = 3000  # coloured glyphs kept

_lock = threading.Lock()
builds = {"glyph": 0, "patch": 0}  # how often each was built (tests, the bench)


class Lru:
    """A small thread-safe least-recently-used cache (several threads draw texts)."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            item = self._items.get(key)
            if item is not None:
                self._items.move_to_end(key)
            return item

    def put(self, key: Any, value: Any) -> Any:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


# -- distance, filters, normals ---------------------------------------------------------

_FAR = 1 << 20  # a "no pixel there" distance, small enough to square in int32 after clipping


def edt(inside: np.ndarray) -> np.ndarray:
    """The Euclidean distance from each pixel of ``inside`` to the nearest pixel outside
    it (0 outside), exact, in two one-dimensional passes (columns, then rows).

    The rows pass takes the minimum over horizontal offsets of the column
    distance squared plus the offset squared, and stops as soon as no pixel can
    gain: after as many offsets as the thickest shape is wide. A mask without
    a pixel outside has no distance; it gets ``h + w``.
    """
    inside = np.asarray(inside, bool)
    h, w = inside.shape
    if not inside.any():
        return np.zeros((h, w), np.float32)
    if inside.all():
        return np.full((h, w), float(h + w), np.float32)
    rows = np.arange(h, dtype=np.int32)[:, None]
    outside = ~inside
    above = np.maximum.accumulate(np.where(outside, rows, -_FAR), axis=0)
    below = np.minimum.accumulate(np.where(outside, rows, _FAR)[::-1], axis=0)[::-1]
    nearest = np.minimum(rows - above, below - rows)  # to the nearest outside pixel, same column
    vertical = np.minimum(nearest, h + w).astype(np.int32) ** 2  # (a column without one: far)
    best = vertical.copy()
    limit = int(best[inside].max())
    step = 1
    while step * step < limit and step < w:
        cost = step * step
        np.minimum(best[:, step:], vertical[:, :-step] + cost, out=best[:, step:])
        np.minimum(best[:, :-step], vertical[:, step:] + cost, out=best[:, :-step])
        if step % 4 == 0:
            limit = int(best[inside].max())
        step += 1
    return np.sqrt(best).astype(np.float32)


def _window_max(a: np.ndarray, size: int) -> np.ndarray:
    """Maximum over ``size`` (odd) neighbours along the last axis, the edge extended.
    Doubling: windows of 1, 2, 4, ... and two of them for the rest."""
    pad = size // 2
    a = np.pad(a, [(0, 0)] * (a.ndim - 1) + [(pad, pad)], mode="edge")
    span, m = 1, a
    while span * 2 <= size:
        m = np.maximum(m[..., :-span], m[..., span:])
        span *= 2
    rest = size - span
    return np.maximum(m[..., : m.shape[-1] - rest], m[..., rest:])


def max_filter(a: np.ndarray, size: int) -> np.ndarray:
    """The maximum over the square of ``size`` (odd) pixels around each one."""
    return _window_max(_window_max(a, size).T, size).T


def sobel(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The Sobel gradient ``(gx, gy)``: a central difference smoothed across, the edge
    extended. Its length is eight times the slope; only its direction is used."""
    p = np.pad(a, 1, mode="edge")
    dx = p[:, 2:] - p[:, :-2]
    dy = p[2:, :] - p[:-2, :]
    gx = dx[:-2] + 2 * dx[1:-1] + dx[2:]
    gy = dy[:, :-2] + 2 * dy[:, 1:-1] + dy[:, 2:]
    return gx, gy


def bevel_width(size: float) -> float:
    """The bevel of lettering at a font size, in px (0: too small, stays flat)."""
    if size < MIN_SIZE:
        return 0.0
    return min(BEVEL_MAX, max(BEVEL_MIN, BEVEL_SCALE * size))


def bevel_normals(mask: np.ndarray, bevel: float, stroke: float = STROKE_SHARE) -> np.ndarray:
    """Normals ``(h, w, 3)`` of a bevel running inwards along the edge of ``mask``
    (coverage 0..1, more than a half is inside), ``bevel`` px wide.

    A pixel within the bevel of the edge tilts 45 degrees away from it; the
    rest is flat. The bevel is held to ``stroke`` of the local half-stroke
    (the largest distance within its own width): a fixed width eats a thin
    stroke whole, while a thick curve may keep all of it. The direction comes
    from a Sobel filter of the distance, which stays steady along the ridge of
    a thin stroke where the exact gradient jumps.
    """
    inside = np.asarray(mask, np.float32) > 0.5
    n = np.zeros(inside.shape + (3,), np.float32)
    n[..., 2] = 1.0
    if bevel <= 0 or not inside.any():
        return n
    d = edt(inside)
    reach = max(1, round(bevel))
    width = np.minimum(bevel, max_filter(d, 2 * reach + 1) * stroke)
    if not (width > 0).any():
        return n
    gx, gy = sobel(d)
    length = np.hypot(gx, gy)
    steady = length > 1e-4
    safe = np.where(steady, length, 1.0)
    ux, uy = np.where(steady, -gx / safe, 0.0), np.where(steady, -gy / safe, 0.0)
    bevelled = inside & (d <= width)
    n[..., 0] = np.where(bevelled, ux * SIN45, 0.0)
    n[..., 1] = np.where(bevelled, uy * SIN45, 0.0)
    n[..., 2] = np.where(bevelled, SIN45, 1.0)
    return n


def shrink(n: np.ndarray, weight: np.ndarray, by: int) -> np.ndarray:
    """Normals averaged ``by`` x ``by`` down, weighted by coverage and made unit again."""
    h, w = weight.shape[0] // by, weight.shape[1] // by
    cover = weight.reshape(h, by, w, by).mean(axis=(1, 3))
    mean = (n * weight[..., None]).reshape(h, by, w, by, 3).mean(axis=(1, 3))
    mean /= np.maximum(cover, 1e-6)[..., None]
    length = np.linalg.norm(mean, axis=-1, keepdims=True)
    up = np.zeros_like(mean)
    up[..., 2] = 1.0
    return np.where(length > 1e-6, mean / np.maximum(length, 1e-9), up).astype(np.float32)


# -- shading ----------------------------------------------------------------------------


def facing(normals: np.ndarray) -> np.ndarray:
    """How much light a surface gets, as a share of what a flat top gets (1 for the top,
    above 1 facing the light, below 1 away; never negative)."""
    return np.clip((normals * LIGHT).sum(-1), 0, 1) / LIGHT_TOP


def shade(k: np.ndarray, style: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """``(A, B)`` with ``rgb = colour * A + B`` (0..255) for light ``k`` (see :func:`facing`):
    a surface facing the light goes towards white, one facing away towards black."""
    lighten, darken = style
    lit = k >= 1
    m = np.clip((k - 1) * lighten, 0, 1)
    a = np.where(lit, 1 - m, darken + (1 - darken) * k).astype(np.float32)
    b = np.where(lit, 255 * m, 0).astype(np.float32)
    return a, b


def toward_light(normals: np.ndarray) -> np.ndarray:
    """-1 (a bevel facing away from the light) through 0 (the flat top) to 1 (facing it)."""
    k = facing(normals)
    top = (np.hypot(LIGHT[0], LIGHT[1]) * SIN45 + SIN45 * LIGHT[2]) / LIGHT_TOP  # most light
    low = max(0.0, SIN45 * LIGHT[2] - SIN45 * np.hypot(LIGHT[0], LIGHT[1])) / LIGHT_TOP
    lit = np.clip((k - 1) / max(top - 1, 1e-6), 0, 1)
    dark = np.clip((k - 1) / max(1 - low, 1e-6), -1, 0)
    return np.where(k >= 1, lit, dark)


def tints_for(palette: dict[str, str] | None) -> tuple[RGB, RGB]:
    """The cool tint (the theme's accent, a third of the way to white) and the warm one
    (its warning colour) a bevel takes on towards and away from the light."""
    context = SimpleNamespace(palette=palette or {})
    cool = light.mix((255, 255, 255), light.role_color(context, "info"), 0.35)
    return cool, light.role_color(context, "warning")


# -- lettering --------------------------------------------------------------------------


class Glyph(NamedTuple):
    """One character at one font size and quarter pixel: where its ink is, its coverage
    (exactly the flat text's), and the shading terms of its bevel (``A``, ``B`` and the
    tint, as bytes) without a colour."""

    left: int
    top: int
    cover: np.ndarray  # (h, w) uint8
    terms: np.ndarray  # (h, w, 3) uint8: A * 255, B, tint * 127 / TINT_STRENGTH + 128


_glyphs = Lru(GLYPHS)
_patches = Lru(PATCHES)
_blank = object()


def usable(font: Any) -> bool:
    """Whether lettering in ``font`` can have facets: a font file (not Pillow's built-in
    bitmap font) of a size where a bevel is not mush."""
    return isinstance(getattr(font, "path", None), (str, os.PathLike)) and font.size >= MIN_SIZE


def _glyph_geometry(font: Any, ch: str, quarter: int) -> Glyph | None:
    """The glyph's coverage as the flat text draws it at ``quarter`` / 4 px right of its
    pixel, and its bevel. None for a blank (a space).

    The normals come from that same mask enlarged, not from the font at four
    times the size: hinting rounds differently at different sizes, and on a
    thin stem the silhouette would part from the coverage actually drawn.
    """
    left, top, right, bottom = font.getbbox(ch, anchor="la")
    if right <= left or bottom <= top:
        return None
    l0, t0 = math.floor(left), math.floor(top)
    w1, h1 = max(1, math.ceil(right) - l0 + 2), max(1, math.ceil(bottom) - t0 + 2)
    mask = Image.new("L", (w1, h1), 0)
    ImageDraw.Draw(mask).text((-l0 + quarter / 4, -t0), ch, font=font, fill=255, anchor="la")
    cover = np.asarray(mask, np.uint8)
    if cover.max() <= 127:
        return None
    big = np.asarray(mask.resize((w1 * SUPER, h1 * SUPER), Image.Resampling.BILINEAR), np.float32)
    big /= 255
    normals = shrink(bevel_normals(big, bevel_width(font.size) * SUPER), big, SUPER)
    a, b = shade(facing(normals), TEXT)
    flat = normals[..., 2] >= 0.999
    way = toward_light(normals)
    tint = np.where(flat, 0.0, np.sign(way) * np.sqrt(np.abs(way)) * TINT_STRENGTH)
    terms = np.stack(
        [
            np.clip(a * 255 + 0.5, 0, 255),
            np.clip(b + 0.5, 0, 255),
            np.clip(tint / TINT_STRENGTH * 127 + 128.5, 0, 255),
        ],
        axis=-1,
    ).astype(np.uint8)
    builds["glyph"] += 1
    return Glyph(l0, t0, cover, terms)


def glyph_geometry(font: Any, ch: str, quarter: int) -> Glyph | None:
    key = (str(font.path), font.size, ch, quarter)
    hit = _glyphs.get(key)
    if hit is None:
        hit = _glyphs.put(key, _glyph_geometry(font, ch, quarter) or _blank)
    return None if hit is _blank else hit


def _coloured(g: Glyph, fill: tuple[int, ...], tints: tuple[RGB, RGB]) -> Image.Image:
    c = np.asarray(fill[:3], np.float32)
    rgb = c * (g.terms[..., 0:1] / 255.0) + g.terms[..., 1:2]
    tint = (g.terms[..., 2:3].astype(np.float32) - 128) / 127 * TINT_STRENGTH
    target = np.where(tint > 0, np.asarray(tints[0], np.float32), np.asarray(tints[1], np.float32))
    rgb = rgb + (target - rgb) * np.abs(tint)
    alpha = g.cover.astype(np.float32) * (fill[3] / 255 if len(fill) > 3 else 1.0)
    out = np.concatenate([np.clip(rgb + 0.5, 0, 255), alpha[..., None] + 0.5], axis=-1)
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA")


def glyph(
    font: Any,
    ch: str,
    fill: tuple[int, ...],
    frac: float = 0.0,
    tints: tuple[RGB, RGB] | None = None,
) -> tuple[Image.Image, int, int] | None:
    """The character as a coloured patch and where it goes: ``(patch, dx, dy)``, from the
    pen's pixel (``frac``, its fraction of a pixel, is rounded to a quarter) and the top
    of the line (anchor ``la``). None for a blank. Built once per font, size,
    character and quarter; once more per colour."""
    quarter = round(frac * 4)
    shift = 0
    if quarter == 4:
        quarter, shift = 0, 1
    g = glyph_geometry(font, ch, quarter)
    if g is None:
        return None
    tints = tints or tints_for(None)
    key = (str(font.path), font.size, ch, quarter, tuple(fill), tints)
    patch = _patches.get(key)
    if patch is None:
        builds["patch"] += 1
        patch = _patches.put(key, _coloured(g, fill, tints))
    return patch, g.left + shift, g.top


def facet_text(
    text: str,
    font: Any,
    fill: tuple[int, ...],
    tracking: float = 0.0,
    tints: tuple[RGB, RGB] | None = None,
) -> Image.Image | None:
    """``text`` in one line as an RGBA image, its facets lit; the pen starts at the
    left edge, which is the image's, and the top of the line is its top. None where the
    font cannot have facets (see :func:`usable`)."""
    if not usable(font):
        return None
    ascent, descent = font.getmetrics()
    advances = [font.getlength(ch) + tracking for ch in text]
    margin = max(2, font.size // 6)
    width = math.ceil(sum(advances) - (tracking if text else 0)) + 2 * margin
    layer = Image.new("RGBA", (max(1, width), ascent + descent), (0, 0, 0, 0))
    pen = float(margin)
    for ch, advance in zip(text, advances, strict=True):
        placed = glyph(font, ch, fill, pen % 1.0, tints)
        if placed is not None:
            patch, dx, dy = placed
            put(layer, patch, math.floor(pen) + dx, dy)
        pen += advance
    return layer


def put(layer: Image.Image, patch: Image.Image, x: int, y: int) -> None:
    """``patch`` over ``layer`` with its corner at (x, y), a part of it off the layer's
    top or left edge (``alpha_composite`` takes no such corner) cut away."""
    left, top = max(0, -x), max(0, -y)
    if left < patch.width and top < patch.height:
        if left or top:
            patch = patch.crop((left, top, patch.width, patch.height))
        layer.alpha_composite(patch, (x + left, y + top))


# -- bars -------------------------------------------------------------------------------

MIN_BAR, MAX_BAR = 6, 400  # thinner bars stay flat (a bevel does not read there), thicker too
SUB = 4  # a window is worked out this much larger
_windows = Lru(512)


def box_window(
    width: int,
    height: int,
    edges: tuple[float, float, float, float],
    bevel: float,
    style: tuple[float, float],
    groove: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Shading of a window ``width`` x ``height`` px on a box whose edges lie at
    ``(left, right, top, bottom)`` px of the window (``inf`` for one beyond it): the
    maps ``(A, B, coverage)`` of ``rgb = colour * A + B``, each ``(height, width)``.

    A point of the box within ``bevel`` of an edge is bevelled, tilting 45 degrees
    away from the nearest edge; where two edges are as near, the corner is a
    mitre. A ``groove`` is the same milled into the ground: the tilt mirrored.
    """
    key = (
        width,
        height,
        tuple(round(e * SUB) if math.isfinite(e) else e for e in edges),
        round(bevel * SUB),
        style,
        groove,
    )
    hit = _windows.get(key)
    if hit is not None:
        return hit
    left, right, top, bottom = edges
    xs = (np.arange(width * SUB, dtype=np.float32) + 0.5) / SUB
    ys = (np.arange(height * SUB, dtype=np.float32) + 0.5) / SUB
    to = np.stack(
        [
            np.broadcast_to(xs - left, (len(ys), len(xs))),
            np.broadcast_to(right - xs, (len(ys), len(xs))),
            np.broadcast_to((ys - top)[:, None], (len(ys), len(xs))),
            np.broadcast_to((bottom - ys)[:, None], (len(ys), len(xs))),
        ]
    )
    nearest = to.argmin(axis=0)
    inside = to.min(axis=0) >= 0
    sides = np.array(
        [(-SIN45, 0), (SIN45, 0), (0, -SIN45), (0, SIN45)], np.float32
    )  # out of the box
    tilt = sides[nearest] * (-1 if groove else 1)
    flat = to.min(axis=0) >= bevel
    normals = np.zeros(nearest.shape + (3,), np.float32)
    normals[..., :2] = np.where(flat[..., None], 0.0, tilt)
    normals[..., 2] = np.where(flat, 1.0, SIN45)
    a, b = shade(facing(normals), style)
    cover = inside.astype(np.float32)

    def small(x: np.ndarray) -> np.ndarray:
        return x.reshape(height, SUB, width, SUB).mean(axis=(1, 3))

    alpha = small(cover)
    safe = np.maximum(alpha, 1e-6)
    return _windows.put(key, (small(a * cover) / safe, small(b * cover) / safe, alpha))


RGBA = tuple[int, int, int, int]
_images = Lru(2048)  # coloured boxes, by what they are made of


def _mix(a: RGBA, b: RGBA | None, t: np.ndarray) -> np.ndarray:
    """Colours ``(n, 4)`` along ``a`` to ``b`` at ``t`` (0..1); ``a`` alone without ``b``."""
    first = np.asarray(a, np.float32)
    if b is None:
        return np.broadcast_to(first, (len(t), 4))
    return first + (np.asarray(b, np.float32) - first) * np.clip(t, 0, 1)[:, None]


def _body(
    horizontal: bool, thick: int, start: float, end: float, groove: bool, style: tuple[float, float]
) -> tuple[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The shading maps of a bar's body from ``start`` to ``end`` px along its length, over
    the pixels from ``floor(start)`` to ``ceil(end)``: that first pixel, and ``(A, B, coverage)``
    shaped ``(thick, n)`` (a horizontal bar) or ``(n, thick)``.

    Only the two ends and one column in between are worked out; the column is
    repeated. A long bar costs what a short one does.
    """
    bevel = thick / 6
    first, last = math.floor(start), math.ceil(end)
    n = last - first
    cap = math.ceil(bevel) + 2

    def window(px: int, low: float, high: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if horizontal:
            return box_window(px, thick, (low, high, 0.0, float(thick)), bevel, style, groove)
        return box_window(thick, px, (0.0, float(thick), low, high), bevel, style, groove)

    if n <= 2 * cap + 2:
        return first, window(n, start - first, end - first)
    axis = 1 if horizontal else 0
    ends = (window(cap, start - first, math.inf), window(cap, -math.inf, end - (last - cap)))
    middle = window(1, -math.inf, math.inf)
    return first, tuple(  # type: ignore[return-value]
        np.concatenate([lo, np.repeat(mid, n - 2 * cap, axis=axis), hi], axis=axis)
        for lo, mid, hi in zip(ends[0], middle, ends[1], strict=True)
    )


def _box_image(
    horizontal: bool,
    thick: int,
    start: float,
    end: float,
    colours: Callable[[np.ndarray], np.ndarray],
    groove: bool,
    style: tuple[float, float],
) -> tuple[Image.Image, int]:
    """A bar's body as an image, and the pixel it starts at along the bar. ``colours``
    gives the colour (RGBA) of each pixel along the bar from its position."""
    first, (a, b, alpha) = _body(horizontal, thick, start, end, groove, style)
    n = alpha.shape[1 if horizontal else 0]
    c = colours(first + np.arange(n, dtype=np.float32) + 0.5)
    c = c[None, :, :] if horizontal else c[:, None, :]
    rgb = c[..., :3] * a[..., None] + b[..., None]
    out = np.concatenate(
        [np.clip(rgb + 0.5, 0, 255), (alpha * c[..., 3] + 0.5)[..., None]], axis=-1
    )
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA"), first


def bar_layer(
    w: int,
    h: int,
    frac: float,
    direction: str,
    paint: tuple[RGBA, RGBA | None],
    track: RGBA | None,
    segments: int = 0,
    gap: int = 0,
) -> Image.Image | None:
    """A bar as RGBA, ``w`` x ``h``: a raised body in ``paint`` (its colour, or a gradient
    along the whole track to the second one) filled ``frac`` of the way from the side
    ``direction`` says, with straight, bevelled ends, in a milled track of colour ``track``.
    Or ``segments`` such blocks with ``gap`` px between. None where a bevel does not read
    (a bar thinner than 6 px) or the blocks do not fit: the caller draws it flat.
    """
    horizontal = direction in ("right", "left")
    span, thick = (w, h) if horizontal else (h, w)
    reverse = direction in ("left", "up")
    if not MIN_BAR <= thick <= MAX_BAR or span < 2:
        return None
    a, b = paint
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    def put_at(image: Image.Image, first: int) -> None:
        layer.alpha_composite(image, (first, 0) if horizontal else (0, first))

    def along(start: float, end: float) -> tuple[float, float]:
        """A stretch from the start of the track, as the bar's side puts it on the box."""
        return (span - end, span - start) if reverse else (start, end)

    def gradient(pos: np.ndarray) -> np.ndarray:
        # the colour is a place on the track, so it means the same value at any fill
        return _mix(a, b, (span - pos if reverse else pos) / span)

    def solid(colour: RGBA, share: float = 1.0) -> Callable[[np.ndarray], np.ndarray]:
        c = np.array([*colour[:3], colour[3] * share], np.float32)
        return lambda pos: np.broadcast_to(c, (len(pos), 4))

    def box(start: float, end: float, colours: Any, groove: bool, key: Any) -> None:
        start, end = round(start * SUB) / SUB, round(end * SUB) / SUB
        k = (horizontal, thick, span, start, end, groove, key)
        hit = _images.get(k)
        if hit is None:
            style = GROOVE if groove else SOLID
            hit = _images.put(k, _box_image(horizontal, thick, start, end, colours, groove, style))
        put_at(*hit)

    if segments > 0:
        cell = (span - gap * (segments - 1)) / segments
        if cell < 1:
            return None
        cells = [along(i * (cell + gap), i * (cell + gap) + cell) for i in range(segments)]
        if track is not None:
            for start, end in cells:
                box(start, end, solid(track), True, track)
        for i, (start, end) in enumerate(cells):
            amount = min(1.0, max(0.0, frac * segments - i))
            if amount <= 0:
                continue
            colour = tuple(_mix(a, b, np.array([i / max(1, segments - 1)]))[0].round().astype(int))
            box(start, end, solid(colour, amount), False, (colour, round(amount * 64)))  # type: ignore[arg-type]
        return layer
    if track is not None:
        box(0, span, solid(track), True, track)
    length = min(float(span), max(0.0, frac * span))
    if length > 0:
        start, end = along(0.0, max(length, 1.0))
        box(start, end, gradient, False, (a, b, reverse))
    return layer


# -- rings ------------------------------------------------------------------------------

RING_SUB = 2  # the band of a ring is worked out this much larger
_rings = Lru(16)
_tracks = Lru(16)  # a ring's milled groove, coloured: it does not change with the fill
_grooves = Lru(64)  # a bar's milled groove, coloured


class RingBand(NamedTuple):
    """The pixels a ring covers, and per sample (``RING_SUB`` squared of them to a pixel)
    where it lies: what a ring of any fill needs, and nothing of its colour."""

    rows: np.ndarray  # (n,) of the pixels
    cols: np.ndarray
    along: np.ndarray  # (samples, n): radians from the start of the sweep
    radius: np.ndarray
    inner: np.ndarray  # px to the inner edge (negative: outside the ring)
    outer: np.ndarray
    tangent: np.ndarray  # the light along the way round, and along the radius (2D parts)
    radial: np.ndarray
    place: np.ndarray  # (n,) 0..1: how far along the sweep the pixel's middle is
    sweep: float  # radians
    thickness: float
    inside: np.ndarray  # (samples, n) 1.0 where the sample lies within both edges
    kept: dict  # shading without ends, by (groove, style): worked out once


def ring_band(w: int, h: int, thickness: int, start: float, end: float) -> RingBand:
    """The band of a ring in a ``w`` x ``h`` box (its circle the largest that fits), its
    ``thickness`` px wide, from angle ``start`` to ``end`` (degrees, clockwise from 3 o'clock)."""
    key = (w, h, thickness, start, end)
    hit = _rings.get(key)
    if hit is not None:
        return hit
    outer_r = min(w, h) / 2
    inner_r = max(0.0, outer_r - max(1, thickness))
    sign = 1.0 if end >= start else -1.0
    sweep = min(2 * math.pi, math.radians(abs(end - start)))
    yy, xx = np.mgrid[:h, :w]
    centre = np.hypot(xx + 0.5 - w / 2, yy + 0.5 - h / 2)
    rows, cols = np.nonzero((centre >= inner_r - 1.5) & (centre <= outer_r + 1.5))
    offsets = (np.arange(RING_SUB, dtype=np.float32) + 0.5) / RING_SUB
    sx = (cols[:, None, None] + offsets[None, None, :]).repeat(RING_SUB, axis=1)
    sy = (rows[:, None, None] + offsets[None, :, None]).repeat(RING_SUB, axis=2)
    # samples first, pixels last: the mean over a pixel's samples is a sum of rows
    dx = (sx - w / 2).reshape(len(rows), -1).T.astype(np.float32).copy()
    dy = (sy - h / 2).reshape(len(rows), -1).T.astype(np.float32).copy()
    radius = np.hypot(dx, dy)
    safe = np.maximum(radius, 1e-6)
    angle = np.arctan2(dy, dx)
    along = np.mod(sign * (angle - math.radians(start)), 2 * math.pi).astype(np.float32)
    tangent = (sign * (-dy * LIGHT[0] + dx * LIGHT[1]) / safe).astype(np.float32)
    radial = ((dx * LIGHT[0] + dy * LIGHT[1]) / safe).astype(np.float32)
    mid = np.arctan2(rows + 0.5 - h / 2, cols + 0.5 - w / 2)
    place = np.mod(sign * (mid - math.radians(start)), 2 * math.pi) / max(sweep, 1e-6)
    inner, outer = (radius - inner_r).astype(np.float32), (outer_r - radius).astype(np.float32)
    band = RingBand(
        rows, cols, along, radius, inner, outer, tangent, radial,
        np.clip(place, 0, 1).astype(np.float32), sweep, outer_r - inner_r,
        ((inner >= 0) & (outer >= 0)).astype(np.float32), {},
    )  # fmt: skip
    return _rings.put(key, band)


def _ring_shading(
    band: RingBand, at: Any, u: Any, left: Any, groove: bool, style: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray]:
    """``(A, B)`` for the samples ``at`` (an index, or ``...``) of a ring whose cuts are ``u``
    px from the start of the sweep and ``left`` px from its end (``inf``: none)."""
    inner, outer = band.inner[at], band.outer[at]
    bevel = band.thickness / 6
    nearest = np.minimum(np.minimum(inner, outer), np.minimum(u, left))
    flat = nearest >= bevel
    at_start = (u <= left) & (u <= inner) & (u <= outer)
    at_end = ~at_start & (left <= inner) & (left <= outer)
    at_inner = ~at_start & ~at_end & (inner <= outer)
    n_way = np.where(flat, 0.0, np.where(at_start, -SIN45, np.where(at_end, SIN45, 0.0)))
    n_out = np.where(flat | at_start | at_end, 0.0, np.where(at_inner, -SIN45, SIN45))
    if groove:  # milled in: the tilt the other way, the top still facing us
        n_way, n_out = -n_way, -n_out
    top = np.where(flat, 1.0, SIN45)
    k = np.clip(n_way * band.tangent[at] + n_out * band.radial[at] + top * LIGHT[2], 0, 1)
    return shade(k / LIGHT_TOP, style)


def ring_maps(
    band: RingBand, frac: float, groove: bool, style: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(A, B, coverage)`` per pixel of the band, for the ring filled ``frac`` of its sweep.

    The edges are the inner and the outer circle and the two straight cuts at the
    ends of the sweep. A point within the bevel of the nearest one tilts 45 degrees
    away from it; the shading is that of :func:`box_window`, on a curve. What the
    long edges make does not depend on the fill and is kept; each fill works out
    only the samples near its ends.
    """
    arc = band.sweep * min(1.0, max(0.0, frac))
    closed = band.sweep >= 2 * math.pi - 1e-6 and frac >= 0.999
    no_ends = band.kept.get((groove, style))
    if no_ends is None:
        far = np.float32(np.inf)
        no_ends = band.kept[(groove, style)] = _ring_shading(band, ..., far, far, groove, style)
    a, b = no_ends
    u = band.radius * band.along  # px from the start cut
    if not closed:
        left = band.radius * arc - u  # px to the end cut
        near = np.nonzero(((u < band.thickness / 6) | (left < band.thickness / 6)) & (left >= 0))
        a, b = a.copy(), b.copy()
        a[near], b[near] = _ring_shading(band, near, u[near], left[near], groove, style)
    cover = band.inside * (band.along <= arc)
    share = 1 / cover.shape[0]
    alpha = cover.sum(axis=0) * share
    safe = np.maximum(alpha, 1e-6)
    return (a * cover).sum(axis=0) * share / safe, (b * cover).sum(axis=0) * share / safe, alpha


def ring_layer(
    w: int,
    h: int,
    thickness: int,
    start: float,
    end: float,
    frac: float,
    paint: tuple[RGBA, RGBA | None],
    track: RGBA | None,
) -> Image.Image:
    """A ring as RGBA, ``w`` x ``h``: a raised arc in ``paint`` (a gradient along the
    sweep to the second colour), ``frac`` of the sweep from its start, with straight,
    bevelled ends, in a milled track of colour ``track``. The ends are straight cuts,
    whatever the gauge's ``cap``."""
    band = ring_band(w, h, thickness, start, end)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if track is not None:
        layer.paste(groove_ring(w, h, thickness, start, end, track))
    if frac > 0:
        out = np.zeros((h, w, 4), np.uint8)
        out[band.rows, band.cols] = _band_image(band, frac, False, SOLID, paint)
        layer.alpha_composite(Image.fromarray(out, "RGBA"))
    return layer


# -- grooves ----------------------------------------------------------------------------
# A groove is the channel a value lies in: milled into the ground, so the light from the
# top left leaves its upper and left edges in shade and catches the lower and right ones.
# It is background, not a lit body: its bevel is kept faint (GROOVE), no groove point is
# brighter than a faint hint of an edge. Other finishes use these as the track they put
# their value in.


def _rgba(color: tuple[int, ...]) -> RGBA:
    return (int(color[0]), int(color[1]), int(color[2]), int(color[3]) if len(color) > 3 else 255)


def groove_bar(
    w: int, h: int, color: tuple[int, ...], vertical: bool = False
) -> Image.Image | None:
    """The milled groove of a bar, RGBA ``w`` x ``h``, lying along ``w`` (``vertical``: along
    ``h``): a floor of exactly ``color`` and a sharp 45-degree bevel all round, ``thickness
    / 6`` px wide (``thickness`` the smaller side), straight at the ends, mitred at the
    corners. ``color`` is RGB or RGBA, the colour of the floor.

    None where a bevel does not read (a thickness under ``MIN_BAR`` px, or over ``MAX_BAR``):
    the caller draws a flat track. The image is kept by size and colour and is the same
    object on every call: copy it before drawing on it.
    """
    horizontal = not vertical
    thick, span = (h, w) if horizontal else (w, h)
    if not MIN_BAR <= thick <= MAX_BAR or span < 2:
        return None
    key = ("bar", w, h, vertical, _rgba(color))
    hit = _grooves.get(key)
    if hit is None:
        layer = bar_layer(w, h, 0.0, "right" if horizontal else "down", (_rgba(color), None),
                          _rgba(color))  # fmt: skip
        assert layer is not None
        hit = _grooves.put(key, layer)
    return hit


def groove_ring(
    w: int, h: int, thickness: int, start: float = 0.0, end: float = 360.0,
    color: tuple[int, ...] = (22, 35, 50),
) -> Image.Image:  # fmt: skip
    """The milled groove of a ring, RGBA ``w`` x ``h``: a band ``thickness`` px wide on the
    largest circle the box holds, from angle ``start`` to ``end`` (degrees, clockwise from
    3 o'clock; a sweep of 360 is closed), with a floor of exactly ``color`` (RGB or RGBA)
    and a sharp 45-degree bevel ``thickness / 6`` px wide along both edges and, in an open
    ring, at its two straight ends. Kept by what it is made of and the same object on
    every call: copy it before drawing on it."""
    colour = _rgba(color)
    key = ("track", w, h, thickness, start, end, colour)
    hit = _tracks.get(key)
    if hit is None:
        band = ring_band(w, h, thickness, start, end)
        out = np.zeros((h, w, 4), np.uint8)
        out[band.rows, band.cols] = _band_image(band, 1.0, True, GROOVE, (colour, None))
        hit = _tracks.put(key, Image.fromarray(out, "RGBA"))
    return hit


def _band_image(
    band: RingBand,
    frac: float,
    groove: bool,
    style: tuple[float, float],
    paint: tuple[RGBA, RGBA | None],
) -> np.ndarray:
    """RGBA bytes ``(n, 4)`` of the band's pixels, coloured by ``paint`` along the sweep."""
    a, b, alpha = ring_maps(band, frac, groove, style)
    c = _mix(paint[0], paint[1], band.place)
    rgb = c[:, :3] * a[:, None] + b[:, None]
    out = np.concatenate([rgb + 0.5, (alpha * c[:, 3])[:, None] + 0.5], axis=1)
    return out.clip(0, 255).astype(np.uint8)


# -- curves -----------------------------------------------------------------------------

CURVE_WIDE = 1200  # a graph wider than this gets the slimmer ridge: the full one costs ~5 ms there
HALO = 0.9  # how strong the halo around a curve's line stands (0: none)
HALO_DIVIDER = 4  # it is worked out at this share of the size (final pixels per pixel) ...
HALO_BLUR = 2.0  # ... with this blur there: about eight pixels in the picture
_halo_table = [min(255, int(v * HALO + 0.5)) for v in range(256)]
RIDGE_TILT = math.radians(62)  # steeper than the bevels elsewhere: more contrast on a thin line
RIDGE_STEEP = 1.8  # how strongly the slope of the curve turns the ridge towards or from the light
RIDGE_SHARE = 0.8  # each flank of the ridge, as a share of the line's width
BAND_SHARE = 2.6  # the band below the ridge reaches this many flanks down at most
BAND_DEPTH = 3.0  # ... and a third of the way to the bottom of the graph


def _over(
    acc: np.ndarray, alpha: np.ndarray, rows0: int, lo: np.ndarray, hi: np.ndarray,
    colour: np.ndarray, weight: np.ndarray | None,
) -> None:  # fmt: skip
    """Lay the band between rows ``lo`` and ``hi`` (floats, one pair per column) over ``acc``
    (premultiplied RGB, the strip starting at row ``rows0``) and ``alpha``: each row
    takes the share of it the band covers, so a curve moving by a fraction of a pixel
    does not jump. ``colour`` is one RGB per column; ``weight`` (the strip's shape) scales it."""
    n_rows, n_cols = alpha.shape
    first = np.floor(lo).astype(np.int64)
    depth = int(np.ceil((hi - lo).max())) + 1 if lo.size else 0
    cols = np.arange(n_cols)
    for j in range(depth):
        row = first + j
        ok = (row - rows0 >= 0) & (row - rows0 < n_rows)
        if not ok.any():
            continue
        r, c = row[ok], cols[ok]
        cover = np.clip(
            np.minimum(r + 1.0, hi[ok]) - np.maximum(r.astype(np.float32), lo[ok]), 0, 1
        )
        if weight is not None:
            cover = cover * weight[r - rows0, c]
        at = (r - rows0, c)
        acc[at] = colour[ok] * cover[:, None] + acc[at] * (1 - cover[:, None])
        alpha[at] = cover + alpha[at] * (1 - cover)


def curve_halo(layer: Image.Image, line: Image.Image, color: RGBA, scale: int = 1) -> None:
    """A soft halo of ``color`` around a curve's line, over ``layer`` (RGBA): ``line`` is
    the line's coverage ("L", as large as the layer), ``scale`` the layer's pixels to a
    pixel of the picture it will be. The halo lies under what is drawn next (the ridge)
    and is worked out small: the line at a quarter of the size, blurred, and raised again."""
    w, h = layer.size
    divider = HALO_DIVIDER * scale
    if w <= 2 * divider or h <= 2 * divider or HALO <= 0:
        return
    soft = (
        line.resize((max(1, w // divider), max(1, h // divider)), Image.Resampling.BOX)
        .filter(ImageFilter.GaussianBlur(HALO_BLUR))
        .point(_halo_table)
        .resize((w, h), Image.Resampling.BILINEAR)
    )
    if color[3] < 255:
        soft = soft.point([v * color[3] // 255 for v in range(256)])
    halo = Image.new("RGBA", (w, h), (*color[:3], 0))
    halo.putalpha(soft)
    layer.alpha_composite(halo)


def curve_ridge(
    layer: Image.Image,
    run: list[tuple[float, float]],
    thick: float,
    color: RGBA,
    wide: bool = False,
) -> bool:
    """Replace the line of a curve by a ridge, on ``layer`` (RGBA, the fill already
    drawn): a flank lit from the top left on the side away from the fill, a dark one
    on the side towards it, and a faint band beneath, each tilted by how the curve
    rises or falls there. ``run`` is the curve's points in the layer's pixels,
    ``thick`` the line's width in them. A ``wide`` graph gets one band only.
    False if the points do not run from left to right: the caller draws a line.
    """
    xs = np.array([p[0] for p in run], np.float32)
    ys = np.array([p[1] for p in run], np.float32)
    width, height = layer.size
    if len(xs) < 2 or np.any(np.diff(xs) < -1e-3):
        return False
    x0, x1 = max(0, math.floor(xs[0])), min(width, math.ceil(xs[-1]))
    if x1 - x0 < 2:
        return False
    y = np.interp(np.arange(x0, x1, dtype=np.float32) + 0.5, xs, ys).astype(np.float32)
    flank = RIDGE_SHARE * thick
    steep = np.gradient(y) * (1.0 if wide else RIDGE_STEEP)
    length = np.sqrt(1 + steep * steep)
    tilt_sin, tilt_cos = (SIN45, SIN45) if wide else (math.sin(RIDGE_TILT), math.cos(RIDGE_TILT))
    out = np.stack(
        [steep / length * tilt_sin, -1 / length * tilt_sin, np.full_like(y, tilt_cos)], -1
    )
    base = np.asarray(color[:3], np.float32)

    def paint(normals: np.ndarray, style: tuple[float, float]) -> np.ndarray:
        a, b = shade(facing(normals), style)
        return base * a[:, None] + b[:, None]

    scale = color[3] / 255
    if wide:  # the line itself, shaded by the slope
        rows0, rows1 = math.floor((y - thick / 2).min()) - 1, math.ceil((y + thick).max()) + 2
        bands = [(y - thick / 2, y + thick, paint(out, RIDGE_BAND), None)]
    else:
        avail = (height - 1) - y
        end = np.clip(avail / BAND_DEPTH, flank, BAND_SHARE * flank) + flank
        inner = out * np.array([-1, -1, 1], np.float32)  # tilted the other way
        rows0, rows1 = math.floor((y - flank).min()) - 1, math.ceil((y + end).max()) + 2
        bands = []
        if (end > 2 * flank + 1e-3).any():  # a graph that is high enough has a band below
            fill = np.asarray(
                layer.crop((x0, max(0, rows0), x1, min(height, rows1))).getchannel("A")
            )
            bands.append((y + flank, end + y - flank, paint(out, RIDGE_BAND), fill / 255.0))
        bands += [
            (y, y + flank, paint(inner, RIDGE_FAR), None),
            (y - flank, y, paint(out, RIDGE), None),
        ]
    rows0, rows1 = max(0, rows0), min(height, rows1)
    if rows1 <= rows0:
        return False
    acc = np.zeros((rows1 - rows0, x1 - x0, 3), np.float32)
    alpha = np.zeros((rows1 - rows0, x1 - x0), np.float32)
    for lo, hi, colour, weight in bands:
        _over(acc, alpha, rows0, lo, hi, np.clip(colour, 0, 255), weight)
    rgba = np.concatenate(
        [acc / np.maximum(alpha, 1e-6)[..., None], (alpha * 255 * scale)[..., None]], -1
    )
    layer.alpha_composite(
        Image.fromarray(np.clip(rgba + 0.5, 0, 255).astype(np.uint8), "RGBA"), (x0, rows0)
    )
    return True

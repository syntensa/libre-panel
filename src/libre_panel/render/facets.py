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
from types import SimpleNamespace
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageDraw

from libre_panel.render import light

RGB = tuple[int, int, int]

# The light: from the top left and in front, as SPUR II's guide light (image axes: x to
# the right, y down, z to the viewer). A flat top faces the viewer: ``LIGHT_TOP``.
LIGHT = np.array([-16.0, -16.0, 24.0], np.float32)
LIGHT /= np.linalg.norm(LIGHT)
LIGHT_TOP = float(LIGHT[2])
SIN45 = math.sin(math.radians(45))

# How far a facet lightens towards white where it faces the light (``hell``) and how
# far it darkens where it faces away (``tief``), per kind of shape
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
    hell, tief = style
    lit = k >= 1
    m = np.clip((k - 1) * hell, 0, 1)
    a = np.where(lit, 1 - m, tief + (1 - tief) * k).astype(np.float32)
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

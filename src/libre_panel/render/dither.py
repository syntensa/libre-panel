"""Soft light without bands: blurs worked out in floating point, and pictures made 8 bits with a
level's worth of noise.

A glow or a gradient that fades over many pixels passes through every level of its colour; held
to whole levels, a dark one steps a level every few dozen pixels, and the steps show as bands on
a large panel (the 12.3" most of all), sharper still once the panel's H.264 has made YUV of them.
Worked out in floating point and made 8 bits with triangular noise of a level each way (TPDF),
each pixel is a little above or below, and together they are the colour in between: what the
start screens do since 0.7.9 (render.bootscreen).

The noise is the same for every picture, a tile of :data:`TILE` pixels laid from the panel's top
left (or a piece's), so that frames differ only where something moves: the panel's video does not
carry new noise every frame. Black stays black: the noise is never added where there is nothing
(a fully transparent pixel, a colour of 0), nor where anything is full. It is for the panel's
video alone (Renderer.dither): there it costs a tenth more of the stream and shows, as its YUV
made the bands sharper; a PNG of noise is twice the size (a still frame over USB, the app's
pictures), and JPEG loses it.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

TILE = 128  # the noise's tile, laid side by side: no pattern shows at this size


@lru_cache(maxsize=1)
def _tile() -> np.ndarray:
    rng = np.random.default_rng(0x11B7E)
    return rng.random((TILE, TILE), np.float32) + rng.random((TILE, TILE), np.float32) - 1.0


@lru_cache(maxsize=16)
def noise(w: int, h: int, x: int = 0, y: int = 0) -> np.ndarray:
    """The noise (h, w), -1 to 1, for a picture whose top left is at (``x``, ``y``): the same
    numbers for the same place on the panel. Read only."""
    tile = _tile()
    reps = (-(-(h + y % TILE) // TILE), -(-(w + x % TILE) // TILE))
    out = np.tile(tile, reps)[y % TILE : y % TILE + h, x % TILE : x % TILE + w]
    out.setflags(write=False)
    return out


def to_u8(values: np.ndarray, x: int = 0, y: int = 0, dither: bool = True) -> np.ndarray:
    """``values`` (h, w) or (h, w, c), floating point 0 to 255, as uint8 with the dither; 0 and
    255 stay as they are. Without (``dither`` False) each value rounded."""
    out = np.add(values, 0.5, dtype=np.float32)
    if dither:
        h, w = values.shape[:2]
        n = noise(w, h, x, y)
        out += n[..., None] if values.ndim == 3 else n
        np.floor(out, out=out)
        np.copyto(out, values, where=(values <= 0) | (values >= 255))  # nothing, or full
    else:
        np.floor(out, out=out)
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


def ramp_u8(line: np.ndarray, length: int, across: bool, dither: bool = True) -> np.ndarray:
    """A straight gradient as uint8 (h, w, c): ``line`` (n, c), the colours along it in floating
    point, ``length`` px across it; ``across`` False: ``line`` runs down (n rows), else across
    (n columns). With the dither its noise repeats every :data:`TILE` px across the gradient,
    so a tile's width of it is worked out and laid side by side."""
    n, c = line.shape
    part = min(length, TILE) if dither else 1
    if across:
        block = to_u8(np.ascontiguousarray(np.broadcast_to(line[None], (part, n, c))), 0, 0, dither)
        return np.tile(block, (-(-length // part), 1, 1))[:length]
    block = to_u8(np.ascontiguousarray(np.broadcast_to(line[:, None], (n, part, c))), 0, 0, dither)
    return np.tile(block, (1, -(-length // part), 1))[:, :length]


def _box_radius(radius: float, passes: int = 3) -> tuple[int, float]:
    """The box of each of ``passes`` passes that together blur as a Gaussian of ``radius``, as
    Pillow works it out (Gwosdek et al. 2011): ``side`` whole pixels each side and a fraction
    ``a`` of the next."""
    sigma2 = radius * radius / passes
    big = math.sqrt(12.0 * sigma2 + 1.0)
    side = math.floor((big - 1.0) / 2.0)
    a = (2 * side + 1) * (side * (side + 1) - 3 * sigma2) / (6 * (sigma2 - (side + 1) * (side + 1)))
    return side, a


def _rows(a: np.ndarray, side: int, frac: float) -> np.ndarray:
    """One pass along each row of ``a`` (h, w, float32, contiguous): the mean over ``side``
    pixels each way and ``frac`` of the next, the edges held (as Pillow's box blur)."""
    h, w = a.shape
    p = np.empty((h, w + 2 * side + 2), np.float32)
    p[:, side + 1 : side + 1 + w] = a
    p[:, : side + 1] = a[:, :1]
    p[:, side + 1 + w :] = a[:, -1:]
    c = np.empty((h, w + 2 * side + 3), np.float32)
    c[:, 0] = 0
    np.cumsum(p, axis=1, out=c[:, 1:])
    # the 2 side + 1 around each pixel, and the two beside them a fraction each
    out = c[:, 2 * side + 2 : 2 * side + 2 + w] - c[:, 1 : 1 + w]
    if frac:
        out += frac * (p[:, 0:w] + p[:, 2 * side + 2 : 2 * side + 2 + w])
    out *= 1.0 / (2 * side + 1 + 2 * frac)
    return out


def reach(radius: float) -> int:
    """How far (px) :func:`blur` of ``radius`` carries a pixel: what lies further off a part
    changes nothing in it, so a part can be blurred with this much round it alone."""
    if radius <= 0:
        return 0
    side, _frac = _box_radius(radius)
    return 3 * (side + 1) + 1


def blur(a: np.ndarray, radius: float) -> np.ndarray:
    """A Gaussian blur of ``a`` (h, w) in floating point, as Pillow's GaussianBlur of ``radius``
    blurs whole levels: three box passes each way."""
    out = np.ascontiguousarray(a, np.float32)
    if radius <= 0 or out.size == 0:
        return out.copy() if out is a else out
    side, frac = _box_radius(radius)
    for _ in range(3):
        out = _rows(out, side, frac)
    out = np.ascontiguousarray(out.T)
    for _ in range(3):
        out = _rows(out, side, frac)
    return np.ascontiguousarray(out.T)

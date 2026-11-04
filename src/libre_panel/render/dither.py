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
import threading
from functools import lru_cache

import numpy as np

from libre_panel.render.kept import MB, cached

TILE = 128  # the noise's tile, laid side by side: no pattern shows at this size
# A side this long at most is blurred as one product with its blur as a matrix (_operator): a
# glow's few hundred pixels in a tenth of the time the passes take (a clock's rolling seconds,
# whose glow is made anew in every frame of the roll), and longer ones in half of it
PRODUCT_MOST = 640
# ... its sides rounded up to steps of this, where what is blurred has room enough round it that
# a longer line changes nothing in it: one matrix serves a glow whose box grows and shrinks (a
# clock's rolling digits made fifty of them a pixel apart, 16 MB on the 12.3")
PRODUCT_STEP = 32


@lru_cache(maxsize=1)
def _tile() -> np.ndarray:
    rng = np.random.default_rng(0x11B7E)
    return rng.random((TILE, TILE), np.float32) + rng.random((TILE, TILE), np.float32) - 1.0


def noise(w: int, h: int, x: int = 0, y: int = 0) -> np.ndarray:
    """The noise (h, w), -1 to 1, for a picture whose top left is at (``x``, ``y``): the same
    numbers for the same place on the panel. Read only."""
    return _noise(w, h, x % TILE, y % TILE)


# the noise of the pictures made again and again (a glow, a ring's light) kept, by bytes: one
# of the panel's size (a background, made once) is not, it was 5.5 MB on the 12.3" and sixteen
# of them were kept, each with the tiles it was cut from
@cached("dither.noise", 2 * MB)
def _noise(w: int, h: int, dx: int, dy: int) -> np.ndarray:
    reps = (-(-(h + dy) // TILE), -(-(w + dx) // TILE))
    out = np.ascontiguousarray(np.tile(_tile(), reps)[dy : dy + h, dx : dx + w])
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


@cached("dither.operators", 16 * MB, least=2)
def _operator(n: int, radius: float) -> np.ndarray:
    """The three passes along a line of ``n`` pixels as a matrix: row i is what they make of a
    single pixel at i, so a line times it is the line blurred (each pass is linear, its edges
    held too)."""
    side, frac = _box_radius(radius)
    out = np.eye(n, dtype=np.float32)
    for _ in range(3):
        out = _rows(out, side, frac)
    return out


def _stepped(n: int) -> int:
    return min(PRODUCT_MOST, -(-n // PRODUCT_STEP) * PRODUCT_STEP)


# the sides asked for so far (by radius), and how often: a side's matrix is made when it is asked
# for the second time, so one asked for once (a lit bar gliding to a new length, every frame of
# its glide: fifty of them, 16 MB) is blurred by the passes, and nothing is kept for it
_ASKED_MOST = 4096
_asked: dict[tuple[int, float], int] = {}
_asked_lock = threading.Lock()


def _again(n: int, radius: float) -> bool:
    """Whether the matrix of a side of ``n`` serves: kept, or asked for once before."""
    key = (n, radius)
    if key in _operator.kept:  # (looked at, not counted)
        return True
    with _asked_lock:
        before = _asked.get(key, 0)
        if not before and len(_asked) >= _ASKED_MOST:
            _asked.clear()
        _asked[key] = before + 1
    return before > 0


def blur(a: np.ndarray, radius: float) -> np.ndarray:
    """A Gaussian blur of ``a`` (h, w) in floating point, as Pillow's GaussianBlur of ``radius``
    blurs whole levels: three box passes each way (as a product with their matrices, where
    neither side is longer than :data:`PRODUCT_MOST` and both were asked for before: the same
    to a thousandth of a level)."""
    out = np.ascontiguousarray(a, np.float32)
    if radius <= 0 or out.size == 0:
        return out.copy() if out is a else out
    h, w = out.shape
    if max(h, w) <= PRODUCT_MOST:
        far = reach(radius)  # (nothing within this of an end: the line may go on beyond it)
        rows = _stepped(h) if h > far and not out[h - far :].any() else h
        cols = _stepped(w) if w > far and not out[:, w - far :].any() else w
        if _again(rows, radius) & _again(cols, radius):  # (both counted)
            if (rows, cols) == (h, w):
                return _operator(h, radius).T @ out @ _operator(w, radius)
            room = np.zeros((rows, cols), np.float32)
            room[:h, :w] = out
            blurred = _operator(rows, radius).T @ room @ _operator(cols, radius)
            return np.ascontiguousarray(blurred[:h, :w])
    side, frac = _box_radius(radius)
    for _ in range(3):
        out = _rows(out, side, frac)
    out = np.ascontiguousarray(out.T)
    for _ in range(3):
        out = _rows(out, side, frac)
    return np.ascontiguousarray(out.T)

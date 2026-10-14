"""The moon as it looks: a sphere with the near side's seas, craters and rays, lit by the sun
at its phase.

The face is made once per process, at :data:`FACE` px, from what anyone sees on a clear night:
the dark seas where they lie (Imbrium at the top left, Tranquillitatis and Crisium on the
right, the Ocean of Storms down the left), bright highlands with craters, and Tycho's rays in
the south. A height map of the craters gives the surface its relief, so the terminator is
ragged with the shadows of crater walls at a half moon and smooth at full moon, as on the sky.

A picture is lit for its phase: the sun's light falls from the side (``phase`` 0 new, 0.25
first quarter, lit on the right; 0.5 full; 0.75 last quarter, lit on the left; ``mirror`` as
seen south of the equator), the brightness follows the Lommel-Seeliger law of dusty ground
(a full moon is not darker at its rim, a crescent is bright along it), and the dark side keeps
the faint earthshine. Its colours are the widget's: the lit side in ``color``, the night side
in ``color2``. Around it a halo, as bright as the moon is full.
"""

from __future__ import annotations

import math
import threading
from functools import lru_cache

import numpy as np
from PIL import Image

FACE = 512  # px of the face (albedo and relief), made once
DISC = 0.92  # the disc's share of the picture: the rest is the halo's
SS = 2  # pictures are lit at this many times their size and reduced

# The seas: centre (x, y on the disc, -1 to 1, y down), radii, how dark (0 to 1)
_SEAS = (
    ((-0.30, -0.44), (0.30, 0.25), 0.95),  # Imbrium
    ((0.24, -0.40), (0.17, 0.15), 0.90),  # Serenitatis
    ((0.38, -0.06), (0.22, 0.17), 0.90),  # Tranquillitatis
    ((0.73, -0.29), (0.10, 0.12), 1.00),  # Crisium
    ((0.62, 0.16), (0.12, 0.16), 0.80),  # Fecunditatis
    ((0.40, 0.31), (0.09, 0.08), 0.75),  # Nectaris
    ((-0.62, -0.08), (0.24, 0.40), 0.85),  # Oceanus Procellarum
    ((-0.42, 0.22), (0.20, 0.16), 0.70),  # its southern reach
    ((-0.16, 0.38), (0.16, 0.12), 0.75),  # Nubium
    ((-0.53, 0.43), (0.09, 0.08), 0.85),  # Humorum
    ((-0.05, -0.74), (0.34, 0.07), 0.28),  # Frigoris
    ((0.02, -0.17), (0.08, 0.06), 0.65),  # Vaporum
    ((0.0, 0.0), (0.05, 0.05), 0.40),  # Sinus Medii
)
# Bright young craters: centre, radius, rays (how far, 0: none)
_BRIGHT = (
    ((-0.12, 0.70), 0.035, 1.0),  # Tycho
    ((-0.31, -0.13), 0.035, 0.25),  # Copernicus
    ((-0.56, -0.13), 0.022, 0.30),  # Kepler
    ((-0.78, -0.33), 0.020, 0.20),  # Aristarchus
)

_lock = threading.Lock()


def _noise(n: int, rng: np.random.Generator, octaves: int = 5) -> np.ndarray:
    """Smooth noise (n, n), about -1 to 1: octaves of random grids, enlarged."""
    out = np.zeros((n, n), np.float32)
    amp, total = 1.0, 0.0
    for octave in range(octaves):
        cells = 4 * 2**octave
        grid = rng.standard_normal((cells, cells)).astype(np.float32)
        image = Image.fromarray(grid, "F").resize((n, n), Image.Resampling.BICUBIC)
        out += amp * np.asarray(image)
        total += amp
        amp *= 0.55
    return out / total


@lru_cache(maxsize=1)
def face() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The near side at :data:`FACE` px: albedo (0.4 to 1.1) and the slopes of its relief
    (d height / dx, d height / dy), in disc coordinates."""
    with _lock:
        n = FACE
        rng = np.random.default_rng(1969)  # the same moon on every run
        ys, xs = (np.mgrid[0:n, 0:n].astype(np.float32) + 0.5) / n * 2 - 1
        rough = _noise(n, rng)
        sea = np.zeros((n, n), np.float32)
        for (cx, cy), (rx, ry), depth in _SEAS:
            # a ragged edge: the noise moves the rim in and out
            d = np.sqrt(((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2) + 0.38 * rough
            sea = np.maximum(sea, depth * np.clip((1.25 - d) / 0.65, 0, 1) ** 1.1)
        fine = _noise(n, rng, 3)
        patches = _noise(n, rng, 4)  # the seas are not of one shade either
        grain = _noise(n, rng, 6) - _noise(n, rng, 2) * 0.5  # the highlands' fine mottling
        albedo = 1.0 - 0.40 * sea * (0.85 + 0.30 * patches) + 0.05 * rough + 0.04 * fine
        albedo += 0.07 * grain * (1 - sea)
        height = np.zeros((n, n), np.float32)
        # craters: many small, few large, most in the highlands (seas are younger)
        count = 320
        radii = 0.004 + 0.045 * rng.power(0.22, count)
        for r in radii:
            for _try in range(4):
                cx, cy = rng.uniform(-0.98, 0.98, 2)
                if cx * cx + cy * cy > 0.97:
                    continue
                i, j = int((cy + 1) / 2 * n), int((cx + 1) / 2 * n)
                if sea[min(n - 1, i), min(n - 1, j)] < rng.uniform(0.0, 1.4):
                    break
            else:
                continue
            _crater(height, albedo, xs, ys, cx, cy, r, n)
        for (cx, cy), r, rays in _BRIGHT:
            _crater(height, albedo, xs, ys, cx, cy, r, n, bright=0.22)
            if rays:
                _rays(albedo, xs, ys, cx, cy, r, rays, rng, n)
        height = _blurred(height, 2)  # no crater wall is a cliff of one pixel
        gy, gx = np.gradient(height, 2.0 / n)
        return (
            np.clip(albedo, 0.35, 1.15).astype(np.float32),
            gx.astype(np.float32),
            gy.astype(np.float32),
        )


def _crater(height: np.ndarray, albedo: np.ndarray, xs: np.ndarray, ys: np.ndarray, cx: float,
            cy: float, r: float, n: int, bright: float = 0.0) -> None:  # fmt: skip
    """A bowl with a raised rim at (cx, cy), radius r, into ``height`` (and a lighter floor
    and rim into ``albedo``), worked on the window around it only."""
    reach = r * 1.8
    i0, i1 = max(0, int((cy - reach + 1) / 2 * n)), min(n, int((cy + reach + 1) / 2 * n) + 2)
    j0, j1 = max(0, int((cx - reach + 1) / 2 * n)), min(n, int((cx + reach + 1) / 2 * n) + 2)
    if i1 <= i0 or j1 <= j0:
        return
    d = np.hypot(xs[i0:i1, j0:j1] - cx, ys[i0:i1, j0:j1] - cy) / r
    bowl = np.where(d < 1.0, (d * d - 1.0) * 0.55, 0.0)  # deepest in the middle
    rim = 0.22 * np.exp(-(((d - 1.0) / 0.25) ** 2))  # the wall, standing above the ground
    height[i0:i1, j0:j1] += r * (bowl + rim)
    ring = np.exp(-(((d - 1.0) / 0.3) ** 2))
    albedo[i0:i1, j0:j1] += 0.015 * ring + bright * np.exp(-((d / 1.1) ** 2))


def _rays(albedo: np.ndarray, xs: np.ndarray, ys: np.ndarray, cx: float, cy: float, r: float,
          reach: float, rng: np.random.Generator, n: int) -> None:  # fmt: skip
    """Bright rays thrown out from a young crater: faint streaks that fade with distance,
    worked on the window they reach only."""
    i0, i1 = max(0, int((cy - reach + 1) / 2 * n)), min(n, int((cy + reach + 1) / 2 * n) + 1)
    j0, j1 = max(0, int((cx - reach + 1) / 2 * n)), min(n, int((cx + reach + 1) / 2 * n) + 1)
    wx, wy = xs[i0:i1, j0:j1] - cx, ys[i0:i1, j0:j1] - cy
    angle, dist = np.arctan2(wy, wx), np.hypot(wx, wy)
    streaks = np.zeros_like(dist)
    for a in rng.uniform(0, 2 * math.pi, 26):
        width = rng.uniform(0.006, 0.016) + 0.05 * dist  # they widen and blur outwards
        turn = np.abs((angle - a + math.pi) % (2 * math.pi) - math.pi)
        off = turn * dist  # how far from the ray's line
        length = reach * rng.uniform(0.35, 1.0)
        streaks += np.exp(-((off / width) ** 2)) * np.clip(1 - dist / length, 0, 1)
    streaks *= np.clip((dist - r) / r, 0, 1)
    nimbus = np.exp(-((dist / (4 * r)) ** 2))  # the light ground thrown out close by
    albedo[i0:i1, j0:j1] += 0.03 * np.clip(streaks, 0, 1) + 0.035 * nimbus


def _blurred(table: np.ndarray, radius: int) -> np.ndarray:
    """``table`` blurred by a box of ``2 * radius + 1`` pixels, twice (near a Gaussian)."""
    size = 2 * radius + 1
    kernel = np.ones(size, np.float32) / size
    out = table
    for _ in range(2):
        out = np.apply_along_axis(lambda row: np.convolve(row, kernel, "same"), 0, out)
        out = np.apply_along_axis(lambda row: np.convolve(row, kernel, "same"), 1, out)
    return out.astype(np.float32)


def _smooth(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def _sampled(table: np.ndarray, size: int) -> np.ndarray:
    return np.asarray(Image.fromarray(table, "F").resize((size, size), Image.Resampling.BOX))


def lit_share(phase: float) -> float:
    """How much of the disc is lit at ``phase`` (0 new, 0.5 full), 0 to 1."""
    return (1 - math.cos(2 * math.pi * phase)) / 2


def disc(size: int, phase: float, color: tuple[int, ...], night: tuple[int, ...] | None,
         mirror: bool = False, share: float = DISC) -> Image.Image:  # fmt: skip
    """The moon at ``phase`` on a clear picture ``size`` px square, its disc ``share`` of it
    wide, lit in ``color`` and its night side in ``night`` (None: a faint earthshine in
    ``color``, on nothing)."""
    m = size * SS
    r = m * share / 2
    albedo, gx, gy = face()
    span = max(2, round(2 * r))
    a, sx, sy = (_sampled(t, span) for t in (albedo, gx, gy))
    x0 = (m - span) / 2
    ys, xs = (np.mgrid[0:span, 0:span].astype(np.float32) + 0.5 - span / 2) / (span / 2)
    rho2 = xs * xs + ys * ys
    z = np.sqrt(np.clip(1.0 - rho2, 0.0, 1.0))
    theta = 2 * math.pi * phase
    lx, lz = math.sin(theta), -math.cos(theta)
    if mirror:
        lx = -lx
    # the sphere's own light: day and night, the terminator a soft line between them
    mu0 = xs * lx + z * lz
    mu = np.clip(z, 0.02, 1.0)  # the ground towards us
    lommel = 2 * np.clip(mu0, 0, None) / (np.clip(mu0, 0, None) + mu)
    # the relief: the slopes of the craters turn the ground to the sun or away from it, which
    # shows where the light comes in low, along the terminator, and hardly at full moon
    graze = 1.0 - np.clip(mu0, 0, 1)  # 1 where the light is grazing
    tilt = -(sx * lx) * z * 4.0 * graze
    relief = np.clip(1.0 + tilt, 0.15, 1.9)
    day = _smooth(-0.05, 0.07, mu0 + 0.03 * np.tanh(tilt)) * np.clip(lommel, 0, 1.25) * relief
    lit = np.asarray(color[:3], np.float32) / 255
    tone = np.clip(a, 0, 1.2)
    rgb_day = lit[None, None, :] * (tone * np.clip(day, 0, 1.25))[..., None]
    if night is not None:
        dark = np.asarray(night[:3], np.float32) / 255
        rgb_night = dark[None, None, :] * (0.80 + 0.35 * (tone - 0.8))[..., None]
        rgb_night += lit[None, None, :] * (0.05 * tone)[..., None]  # the earthshine
        night_alpha = 1.0
    else:
        rgb_night = lit[None, None, :] * (0.40 * tone)[..., None]  # a faint ghost of the disc
        night_alpha = 0.10
    w = np.clip(day, 0, 1)[..., None]
    rgb = rgb_night * (1 - w) + rgb_day
    peak = np.max(rgb, axis=-1, keepdims=True)
    rgb = rgb / np.maximum(1.0, peak)  # bright ground goes towards white, not past it
    # the disc's edge, smooth; the night side as opaque as ``night`` allows
    edge = np.clip((1.0 - np.sqrt(rho2)) * span / 2 + 0.5, 0, 1)
    alpha = edge * (night_alpha + (1 - night_alpha) * w[..., 0])
    body = np.zeros((m, m, 4), np.float32)
    i0 = int(round(x0))
    body[i0 : i0 + span, i0 : i0 + span, :3] = rgb * alpha[..., None]
    body[i0 : i0 + span, i0 : i0 + span, 3] = alpha
    out = Image.fromarray(np.clip(body * 255 + 0.5, 0, 255).astype(np.uint8), "RGBa")
    return out.reduce(SS).convert("RGBA") if SS > 1 else out.convert("RGBA")


@lru_cache(maxsize=32)
def _halo_alpha(size: int, share: float) -> np.ndarray:
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5 - size / 2
    d = np.hypot(xs, ys) / (size * share / 2)  # 1 at the disc's edge
    glow = np.exp(-np.clip(d - 1.0, 0, None) / 0.03) * (d >= 0.985)
    glow += 0.30 * np.exp(-np.clip(d - 1.0, 0, None) / 0.09) * (d >= 0.985)
    fade = np.clip((size / 2 - np.hypot(xs, ys)) / (size * 0.02), 0, 1)  # none at the box
    return (glow * fade).astype(np.float32)


def halo(size: int, color: tuple[int, ...], strength: float, share: float = DISC) -> Image.Image:
    """A soft glow around the disc, in ``color``, ``strength`` 0 to 1 at its brightest."""
    alpha = np.clip(_halo_alpha(size, share) * strength, 0, 1)
    out = np.zeros((size, size, 4), np.uint8)
    out[..., :3] = color[:3]
    out[..., 3] = (alpha * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def picture(size: int, phase: float, color: tuple[int, ...], night: tuple[int, ...] | None,
            mirror: bool = False, t: float | None = None) -> Image.Image:  # fmt: skip
    """The moon with its halo; ``t`` (seconds) lets the halo breathe, slowly (None: still).
    The disc is made once per phase and kept, the halo in a few strengths."""
    breath = 1.0 if t is None else 0.80 + 0.20 * (0.5 + 0.5 * math.sin(2 * math.pi * t / BREATH))
    level = round(HALO_LEVELS * 0.40 * lit_share(phase) * breath)
    out = _halo_kept(size, tuple(color[:3]), level).copy()
    out.alpha_composite(_disc_kept(size, round(phase, 3), tuple(color), night, mirror))
    return out


BREATH = 7.0  # seconds the halo takes to swell and settle
HALO_LEVELS = 48


@lru_cache(maxsize=8)
def _disc_kept(size: int, phase: float, color: tuple[int, ...], night: tuple[int, ...] | None,
               mirror: bool) -> Image.Image:  # fmt: skip
    return disc(size, phase, color, night, mirror)


@lru_cache(maxsize=96)
def _halo_kept(size: int, color: tuple[int, ...], level: int) -> Image.Image:
    return halo(size, color, level / HALO_LEVELS)


def preload() -> None:
    """Make the face now (a third of a second or so), away from the frames."""
    face()

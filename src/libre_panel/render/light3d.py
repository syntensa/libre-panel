"""Light baked in Blender, colour from the theme: SPUR II's 3D look.

Blender rendered real bodies once, without colour (``assets/light3d``): a
ring (a torus, with a sphere for its ends), a tube with a sphere for bars,
and the bodies of the weather symbols. Each comes as three grey layers lit
from the top left: ``shape`` (the diffuse light), ``core`` (bright in the
middle of a tube, dark at its rims) and ``gloss`` (the reflection). Here
they are coloured with a widget's colour and fitted to its size, so nothing
3D runs while drawing and a new colour needs no new render.

Rings and bars are tubes with the same cross-section all along, so their
layers become small tables: the shading by angle around the ring and by the
place across the tube. A ring or bar of any size and thickness is then
drawn by looking each pixel up, with exact antialiasing at its rims and
ends that move in fractions of a pixel. The weather bodies are scaled as
pictures.

Preparing the tables from the PNGs takes a few hundred milliseconds, far
too long for a frame (SPUR II froze for 118 ms on its first weather change):
:class:`Part` keeps them as ``.npz`` in the cache folder and prepares them
again when a source PNG is newer, and :func:`preload` loads them in a thread
of its own before the first frame.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from libre_panel.config import config_dir

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "light3d"
LAYERS = ("shape", "core", "gloss")
CORE_MIN = 0.596  # the core layer at the rim of a tube (measured on the ring)

# How the layers mix, the same for every part (measured on the ring, so a
# straight bar looks like the ring at twelve o'clock): the colour times
# (ambient + diffuse * shape) * (1 - core + core * core layer), held to the
# colour's hue, then the gloss in white on top.
BODY = {"ambient": 0.5, "diffuse": 0.5, "core": 0.30, "gloss": 0.55}
CAP_GLOSS = 0.7  # a sphere's gloss is a point: a little less of it
# The groove: the dark channel a value runs in, its scale.
GROOVE = {"ambient": 0.55, "diffuse": 1.0, "gloss": 0.22}
# Weather bodies by kind; ``glow`` lights the middle towards white (sun, bolt).
WEATHER = {
    "sun": {"ambient": 0.75, "diffuse": 0.25, "core": 0.25, "glow": 0.35, "gloss": 0.30},
    "cloud": {"ambient": 0.45, "diffuse": 0.65, "core": 0.20, "glow": 0.0, "gloss": 0.12},
    "moon": {"ambient": 0.50, "diffuse": 0.50, "core": 0.30, "glow": 0.0, "gloss": 0.45},
    "glass": {"ambient": 0.45, "diffuse": 0.45, "core": 0.35, "glow": 0.0, "gloss": 0.90},
    "bolt": {"ambient": 0.70, "diffuse": 0.30, "core": 0.20, "glow": 0.30, "gloss": 0.50},
}
BODIES = ("sphere", "rays", "cloud", "drop", "bolt")

REACH = 0.97  # how far across a tube the tables reach (the rest is its antialiased rim)
ANGLES = 720  # a ring's table: half a degree apart
ACROSS = 33  # samples across a tube
SPHERE = 49  # a sphere's table: samples along each axis
MIN_BAR = 6  # thinner bars stay flat: depth does not read there
MIN_RING = 4  # the same for a ring's tube


# -- preparing the layers ------------------------------------------------------------


def _read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as image:
        data = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255
    return data[..., :3].mean(-1), data[..., 3]


def _layers(folder: Path, stem: str) -> np.ndarray:
    """``stem``'s shape, core and gloss (normalised, premultiplied by its
    coverage) and the coverage: (4, h, w)."""
    shape, alpha = _read(folder / f"{stem}_shape.png")
    core, _ = _read(folder / f"{stem}_core.png")
    gloss, _ = _read(folder / f"{stem}_gloss.png")
    core = np.clip((core - CORE_MIN) / (1 - CORE_MIN), 0, 1)
    return np.stack([shape * alpha, core * alpha, gloss * alpha, alpha]).astype(np.float32)


def _sample(layers: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """``layers`` (c, h, w) at the points (x, y), in pixels (centres at i + 0.5),
    bilinear."""
    _, h, w = layers.shape
    x = np.clip(np.asarray(x, np.float64) - 0.5, 0, w - 1.001)
    y = np.clip(np.asarray(y, np.float64) - 0.5, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
    fx, fy = (x - x0).astype(np.float32), (y - y0).astype(np.float32)
    top = layers[:, y0, x0] * (1 - fx) + layers[:, y0, x0 + 1] * fx
    bottom = layers[:, y0 + 1, x0] * (1 - fx) + layers[:, y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def _plain(samples: np.ndarray) -> np.ndarray:
    """Shape, core and gloss without the coverage they were premultiplied with."""
    return np.clip(samples[:3] / np.maximum(samples[3], 1e-4), 0, 1).astype(np.float32)


def _ring_table(ring: np.ndarray) -> np.ndarray:
    """The ring's shading by angle (0 at twelve o'clock, clockwise) and across
    its tube (-REACH inside to REACH outside): (3, ANGLES, ACROSS)."""
    alpha = ring[3]
    h, w = alpha.shape
    ys, xs = np.nonzero(alpha > 0.5)
    rho = np.hypot(xs + 0.5 - w / 2, ys + 0.5 - h / 2)
    inner, outer = rho.min() - 0.5, rho.max() + 0.5
    radius, half = (inner + outer) / 2, (outer - inner) / 2
    theta = np.arange(ANGLES) * (2 * np.pi / ANGLES)
    across = np.linspace(-REACH, REACH, ACROSS)
    distance = radius + across[None, :] * half
    x = w / 2 + distance * np.sin(theta)[:, None]
    y = h / 2 - distance * np.cos(theta)[:, None]
    return _plain(_sample(ring, x, y))


def _sphere_table(layers: np.ndarray) -> np.ndarray:
    """A sphere's shading over its disc, from -1 to 1 (its rim) along x and y:
    (3, SPHERE, SPHERE), indexed [y, x]."""
    ys, xs = np.nonzero(layers[3] > 0.5)
    cx, cy = (xs.min() + xs.max() + 1) / 2, (ys.min() + ys.max() + 1) / 2
    radius = (xs.max() + 1 - xs.min()) / 2
    steps = np.linspace(-1, 1, SPHERE)
    u, v = np.meshgrid(steps, steps)
    rho = np.hypot(u, v)
    inside = np.where(rho > REACH, REACH / np.maximum(rho, 1e-6), 1.0)  # beyond: the rim's
    return _plain(_sample(layers, cx + u * inside * radius, cy + v * inside * radius))


def _tube_table(tube: np.ndarray) -> np.ndarray:
    """A straight tube's shading across it, from -REACH (top) to REACH: (3, ACROSS)."""
    w = tube.shape[2]
    rows = np.nonzero(tube[3][:, w // 2] > 0.5)[0]
    top, bottom = rows.min() - 0.5, rows.max() + 1.5
    profile = tube[:, :, w // 4 : 3 * w // 4].mean(-1)  # the same all along: a mean against noise
    at = (top + bottom) / 2 + np.linspace(-REACH, REACH, ACROSS) * (bottom - top) / 2
    centres = np.arange(profile.shape[1]) + 0.5
    return _plain(np.stack([np.interp(at, centres, channel) for channel in profile]))


def _ring_build(folder: Path) -> dict[str, np.ndarray]:
    return {
        "ring": _ring_table(_layers(folder, "ring")),
        "cap": _sphere_table(_layers(folder, "cap")),
    }


def _bar_build(folder: Path) -> dict[str, np.ndarray]:
    return {"tube": _tube_table(_layers(folder, "tube")),
            "sphere": _sphere_table(_layers(folder, "sphere"))}  # fmt: skip


def _weather_build(folder: Path) -> dict[str, np.ndarray]:
    bodies = {}
    for name in BODIES:
        layers = _layers(folder, name)
        c, h, w = layers.shape  # at half size: plenty for icons, a quarter of the memory
        bodies[name] = layers.reshape(c, h // 2, 2, w // 2, 2).mean(axis=(2, 4)).astype(np.float32)
    return bodies


class Part:
    """The prepared layers of one part (``ring``, ``bar``, ``weather``), loaded
    once per process; thread-safe.

    The tables are kept as ``<name>.npz`` in ``cache/light3d`` of the config
    folder and prepared again when a source PNG is newer (or the format
    changed). ``get`` returns None when the files are missing or broken: the
    caller draws flat.
    """

    VERSION = 1

    def __init__(
        self,
        name: str,
        stems: tuple[str, ...],
        build: Callable[[Path], dict[str, np.ndarray]],
        folder: Path = ASSETS,
    ) -> None:
        self.name, self.build = name, build
        self.folder = Path(folder) / name
        self.sources = [self.folder / f"{stem}_{layer}.png" for stem in stems for layer in LAYERS]
        self._data: dict[str, np.ndarray] | None = None
        self._missing = False
        self._lock = threading.Lock()

    @property
    def cache(self) -> Path:
        return config_dir() / "cache" / "light3d" / f"{self.name}.npz"

    @property
    def loaded(self) -> bool:
        return self._data is not None or self._missing

    def get(self) -> dict[str, np.ndarray] | None:
        if not self.loaded:
            with self._lock:
                if not self.loaded:
                    self._data = self._load()
                    self._missing = self._data is None
        return self._data

    def reset(self) -> None:
        """Forget what was loaded (tests; the next ``get`` loads again)."""
        with self._lock:
            self._data, self._missing = None, False

    def _load(self) -> dict[str, np.ndarray] | None:
        try:
            newest = max(path.stat().st_mtime for path in self.sources)
        except OSError:
            log.info("light layers %r are missing: drawn flat", self.name)
            return None
        data = self._read_cache(newest)
        if data is not None:
            return data
        try:
            data = self.build(self.folder)
        except Exception as exc:  # drawn flat
            log.warning("light layers %r could not be prepared: %s", self.name, exc)
            return None
        self._write_cache(data)
        return data

    def _read_cache(self, newest: float) -> dict[str, np.ndarray] | None:
        try:
            if self.cache.stat().st_mtime < newest:
                return None  # a source is newer
            with np.load(self.cache) as stored:
                if int(stored["version"]) != self.VERSION:
                    return None
                return {key: stored[key] for key in stored.files if key != "version"}
        except (OSError, ValueError, KeyError):
            return None

    def _write_cache(self, data: dict[str, np.ndarray]) -> None:
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            part = self.cache.with_name(f"{self.name}.{os.getpid()}.tmp")
            with open(part, "wb") as file:
                np.savez(file, version=np.array(self.VERSION), **data)
            os.replace(part, self.cache)  # whole or not at all, also for another process
        except OSError as exc:
            log.info("light layers %r not cached: %s", self.name, exc)


RING = Part("ring", ("ring", "cap"), _ring_build)
BAR = Part("bar", ("tube", "sphere"), _bar_build)
WEATHER_BODIES = Part("weather", BODIES, _weather_build)
PARTS = {"ring": RING, "bar": BAR, "weather": WEATHER_BODIES}


def preload(names: tuple[str, ...] | None = None) -> None:
    """Load the parts (all, or ``names``) now: in a thread of its own before
    the first frame, so no frame waits for them."""
    for name in names or tuple(PARTS):
        PARTS[name].get()


# -- looking up and colouring ----------------------------------------------------------


def _bilinear(table: np.ndarray, i: np.ndarray, j: np.ndarray, wrap: bool = False) -> np.ndarray:
    """``table`` (c, n, m) at the fractional indices (i, j); ``wrap`` joins i's ends."""
    n, m = table.shape[1:]
    if wrap:
        i = np.mod(i, n)
    else:
        i = np.clip(i, 0, n - 1.0001)
    j = np.clip(j, 0, m - 1.0001)
    i0, j0 = np.floor(i).astype(np.intp), np.floor(j).astype(np.intp)
    fi, fj = (i - i0).astype(np.float32), (j - j0).astype(np.float32)
    i1 = (i0 + 1) % n if wrap else i0 + 1
    top = table[:, i0, j0] * (1 - fj) + table[:, i0, j0 + 1] * fj
    bottom = table[:, i1, j0] * (1 - fj) + table[:, i1, j0 + 1] * fj
    return top * (1 - fi) + bottom * fi


def _across(position: np.ndarray) -> np.ndarray:
    """A place across a tube (-1 to 1) as an index into a table's ACROSS samples."""
    return (np.clip(position, -REACH, REACH) + REACH) / (2 * REACH) * (ACROSS - 1)


def _sphere_at(table: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A sphere's shading at (u, v) on its disc (beyond the rim: the rim's)."""
    rho = np.hypot(u, v)
    inside = np.where(rho > REACH, REACH / np.maximum(rho, 1e-6), 1.0)
    scale = (SPHERE - 1) / 2
    return _bilinear(table, (v * inside + 1) * scale, (u * inside + 1) * scale)


def tint(shade: np.ndarray, colour: Any, mix: dict[str, float], groove: bool = False) -> np.ndarray:
    """RGB (n, 3, 0 to 1) of the shading ``shade`` (3, n) in ``colour``: RGB 0 to
    1, one (3) or one per point (n, 3). A groove takes the light of
    :data:`GROOVE`, everything else ``mix``. Worked out channel by channel:
    a maximum over three values per pixel is slow as an axis of its own."""
    shape, core, gloss = shade
    c = np.asarray(colour, np.float32)
    c = c[:, None] if c.ndim == 1 else c.T  # (3, 1) or (3, n)
    if groove:
        rgb = c * (GROOVE["ambient"] + GROOVE["diffuse"] * shape) + GROOVE["gloss"] * gloss
    else:
        light = (mix["ambient"] + mix["diffuse"] * shape) * ((1 - mix["core"]) + mix["core"] * core)
        rgb = c * light
        peak = np.maximum(np.maximum(rgb[0], rgb[1]), rgb[2])
        rgb = rgb / np.maximum(1.0, peak)  # pale colours would run to white
        if mix.get("glow"):
            rgb = rgb + mix["glow"] * core**3 * (1 - rgb)
        rgb = rgb + mix["gloss"] * gloss
    return np.clip(rgb, 0, 1).T


def rgba(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """RGB and coverage (0 to 1) as uint8 RGBA."""
    out = np.concatenate([rgb * 255 + 0.5, alpha[..., None] * 255 + 0.5], -1)
    return np.clip(out, 0, 255).astype(np.uint8)


def unit_colour(color: tuple[int, ...]) -> np.ndarray:
    return np.asarray(color[:3], np.float32) / 255


class Kept:
    """Finished pictures by key, the most recently used kept up to ``budget``
    bytes; thread-safe (the render thread and the helper thread draw)."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self._items: OrderedDict[Hashable, Any] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable, make: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return hit
        made = make()
        with self._lock:
            if key not in self._items:
                self._items[key] = made
                self._size += _bytes(made)
            while self._size > self.budget and len(self._items) > 1:
                _key, old = self._items.popitem(last=False)
                self._size -= _bytes(old)
        return made

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0


def _bytes(value: Any) -> int:
    if isinstance(value, Image.Image):
        return value.width * value.height * len(value.getbands())
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, tuple):
        return sum(_bytes(v) for v in value) + 64
    return 1024  # tables of arrays and the like: small next to pictures


# -- rings ----------------------------------------------------------------------------


class Ring:
    """A ring gauge's tube at its size (``w`` x ``h``, ``thickness``) from
    ``start`` to ``end`` (degrees clockwise from three o'clock, as the flat
    gauge's): which pixels it covers and their shading, worked out once.
    :meth:`image` colours an arc of it. An oval gauge's centre line is the
    ellipse the flat one draws."""

    def __init__(self, w: int, h: int, thickness: float, start: float, end: float,
                 round_caps: bool, tables: dict[str, np.ndarray]) -> None:  # fmt: skip
        self.size = (w, h)
        self.half = half = max(1.0, thickness / 2)
        self.cx, self.cy = w / 2, h / 2
        self.rx, self.ry = max(1.0, (w - thickness) / 2), max(1.0, (h - thickness) / 2)
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
        dx, dy = xs - self.cx, ys - self.cy
        rho = np.hypot(dx, dy)
        centre = rho / np.maximum(np.hypot(dx / self.rx, dy / self.ry), 1e-6)
        off = rho - centre  # across the tube, in pixels, outwards
        band = np.abs(off) <= half + 1
        self.index = np.flatnonzero(band)
        self.x, self.y, off = xs[band], ys[band], off[band]
        self.radius = centre[band]
        self.theta = np.mod(np.arctan2(dx[band], -dy[band]), 2 * np.pi)  # 0 at twelve o'clock
        self.cover = np.clip(half + 0.5 - np.abs(off), 0, 1)
        self.shade = _bilinear(
            tables["ring"], self.theta * (ANGLES / (2 * np.pi)), _across(off / half), wrap=True
        )
        self.sphere = tables["cap"]
        self.start = math.radians(start + 90)  # from three to twelve o'clock
        self.sweep = math.radians(end - start)
        self.whole = abs(self.sweep) >= 2 * math.pi - 1e-4
        self.round = round_caps

    def _end(self, angle: float) -> tuple[float, float]:
        """Where the centre line is at ``angle`` (radians from twelve o'clock)."""
        sx, sy = math.sin(angle), -math.cos(angle)
        r = 1 / math.hypot(sx / self.rx, sy / self.ry)
        return self.cx + r * sx, self.cy + r * sy

    def _cap(self, angle: float) -> tuple[np.ndarray, np.ndarray]:
        """Coverage and shading of the sphere that rounds the end at ``angle``."""
        ex, ey = self._end(angle)
        u, v = (self.x - ex) / self.half, (self.y - ey) / self.half
        cover = np.clip(self.half + 0.5 - np.hypot(u, v) * self.half, 0, 1)
        shade = np.zeros_like(self.shade)
        near = cover > 0
        shade[:, near] = _sphere_at(self.sphere, u[near], v[near])
        return cover, shade

    def image(self, fraction: float, colours: tuple[Any, Any | None],
              groove: bool = False) -> Image.Image:  # fmt: skip
        """The tube over ``fraction`` of the arc (1: all of it, a groove's) in
        ``colours``: (colour, None) or a gradient (at the start, at the end of the
        whole arc), RGB 0 to 1."""
        w, h = self.size
        out = np.zeros((w * h, 4), np.uint8)
        if fraction <= 0:
            return Image.fromarray(out.reshape(h, w, 4), "RGBA")
        span = abs(self.sweep) * min(1.0, fraction)
        u = np.mod(self.theta - self.start, 2 * np.pi)
        if self.sweep < 0:
            u = np.mod(self.start - self.theta, 2 * np.pi)
        if self.whole and fraction >= 1:
            along = np.ones_like(u)
        else:  # soft ends, a pixel wide (the tube is cut, a sphere rounds it)
            inside = u <= span
            gap = np.where(inside, np.minimum(u, span - u), -np.minimum(u - span, 2 * np.pi - u))
            along = np.clip(0.5 + gap * self.radius, 0, 1)
        alpha = self.cover * along
        shade = self.shade * alpha
        if self.round and not (self.whole and fraction >= 1):  # a closed ring has no ends
            direction = 1 if self.sweep >= 0 else -1
            for angle in (self.start, self.start + direction * span):
                cover, cap = self._cap(angle)
                if not groove:
                    cap[2] *= CAP_GLOSS
                under = cover * (1 - alpha)  # the sphere lies under the tube: its outer half shows
                shade += cap * under
                alpha = alpha + under
        shade = shade / np.maximum(alpha, 1e-4)
        start, end = colours
        if end is None:
            colour = start
        else:  # a colour per place on the scale; before the start, the start's
            before = u > span + (2 * np.pi - span) / 2
            t = np.where(before, 0.0, np.clip(u / abs(self.sweep), 0, 1))[:, None]
            colour = np.asarray(start) + (np.asarray(end) - np.asarray(start)) * t
        rgb = tint(shade, colour, BODY, groove)
        out[self.index] = rgba(rgb, np.clip(alpha, 0, 1))
        return Image.fromarray(out.reshape(h, w, 4), "RGBA")


# -- bars -----------------------------------------------------------------------------


class Bar:
    """A bar's tube at a ``thickness``: its shading across, worked out once.
    :meth:`image` draws spans of it along a bar, lying (an upright bar is
    drawn lying and turned)."""

    def __init__(self, thickness: int, tables: dict[str, np.ndarray]) -> None:
        self.thick = thickness
        self.half = thickness / 2
        across = _across((np.arange(thickness, dtype=np.float32) + 0.5 - self.half) / self.half)
        samples = np.arange(ACROSS)
        self.profile = np.stack([np.interp(across, samples, row) for row in tables["tube"]])
        self.profile = self.profile.astype(np.float32)  # (3, thickness)
        self.sphere = tables["sphere"]
        self._caps: dict[tuple[int, int, bool], tuple[np.ndarray, np.ndarray]] = {}

    PHASES = 8  # a sphere's centre in eighths of a pixel: its pictures are worked out once

    def _cap(self, phase: int, side: int, groove: bool) -> tuple[np.ndarray, np.ndarray]:
        """The coverage and shading of the sphere that rounds a span's ``side``
        end (-1 start, 1 end), its centre ``phase`` eighths into a pixel: over
        the columns from ``-ceil(r) - 1`` to ``ceil(r) + 1`` around the pixel."""
        key = (phase, side, groove)
        kept = self._caps.get(key)
        if kept is None:
            r = self.half
            reach = math.ceil(r) + 1
            near = np.arange(-reach, reach + 1, dtype=np.float32) + 0.5 - phase / self.PHASES
            u = (near / r)[None, :]
            v = ((np.arange(self.thick, dtype=np.float32) + 0.5 - r) / r)[:, None]
            cover = np.clip(r + 0.5 - np.hypot(u, v) * r, 0, 1)
            # only beyond the tube, on its own side (a pixel the tube's end cuts
            # takes the rest from it)
            cover = (cover * ((near + side * 0.5) * side > 0)).astype(np.float32)
            shade = _sphere_at(self.sphere, *np.broadcast_arrays(u, v)).astype(np.float32)
            if not groove:
                shade[2] *= CAP_GLOSS
            kept = self._caps[key] = (cover, shade)
        return kept

    def image(self, length: int, spans: list[tuple[float, float, float]], colour: np.ndarray,
              groove: bool = False) -> np.ndarray:  # fmt: skip
        """RGBA (thickness, length, 4) of the tube along the ``spans`` (start,
        end in pixels along the bar, and how much of it shows, 0 to 1; segments
        are several) in ``colour``: RGB 0 to 1, one (3) or one per column
        (length, 3). Each span has round ends, a sphere's."""
        t, r = self.thick, self.half
        shade = np.zeros((3, t, length), np.float32)
        alpha = np.zeros((t, length), np.float32)
        x = np.arange(length, dtype=np.float32)
        ys = (np.arange(t, dtype=np.float32) + 0.5)[:, None]
        for a, b, amount in spans:
            a, b = max(0.0, a), min(float(length), b)
            if b - a < 1:  # the smallest value stays visible as a sliver
                b = min(float(length), a + 1)
                a = b - 1
            if b <= a or amount <= 0:
                continue
            left, right = max(0, math.floor(a) - 1), min(length, math.ceil(b) + 1)
            cols = x[left:right][None, :] + 0.5
            piece_alpha = np.zeros((t, right - left), np.float32)
            piece_shade = np.zeros((3, t, right - left), np.float32)
            if b - a >= t:
                body = np.clip(np.minimum(cols + 0.5, b - r) - np.maximum(cols - 0.5, a + r), 0, 1)
                body = np.broadcast_to(body, piece_alpha.shape)
                piece_alpha += body
                piece_shade += self.profile[:, :, None] * body
                for centre, side in ((a + r, -1), (b - r, 1)):
                    whole = math.floor(centre)
                    phase = round((centre - whole) * self.PHASES)
                    if phase == self.PHASES:
                        whole, phase = whole + 1, 0
                    cover, cap = self._cap(phase, side, groove)
                    reach = math.ceil(r) + 1
                    first = whole - reach  # the column of the cap's first
                    c0, c1 = max(left, first), min(right, first + cover.shape[1])
                    if c1 <= c0:
                        continue
                    part = slice(c0 - first, c1 - first)
                    under = cover[:, part] * (1 - piece_alpha[:, c0 - left : c1 - left])
                    piece_shade[:, :, c0 - left : c1 - left] += cap[:, :, part] * under
                    piece_alpha[:, c0 - left : c1 - left] += under
            else:  # shorter than thick: a sphere squeezed to its length
                half_len = (b - a) / 2
                u, v = (cols - (a + b) / 2) / half_len, (ys - r) / r
                q = np.hypot(u, v)
                cover = np.clip((1 - q) * min(half_len, r) + 0.5, 0, 1)
                cap = _sphere_at(self.sphere, *np.broadcast_arrays(u, v))
                if not groove:
                    cap[2] *= CAP_GLOSS
                piece_alpha += cover
                piece_shade += cap * cover
            piece_alpha *= amount
            piece_shade *= amount
            # spans do not overlap (segments have gaps): side by side
            shade[:, :, left:right] += piece_shade * (1 - alpha[:, left:right])
            alpha[:, left:right] += piece_alpha * (1 - alpha[:, left:right])
        plain = shade / np.maximum(alpha, 1e-4)
        c = np.asarray(colour, np.float32)
        per_pixel = np.broadcast_to(c if c.ndim == 1 else c[None, :, :], (t, length, 3))
        rgb = tint(plain.reshape(3, -1), per_pixel.reshape(-1, 3), BODY, groove)
        return rgba(rgb.reshape(t, length, 3), np.clip(alpha, 0, 1))


# -- weather bodies ------------------------------------------------------------------------


def body(name: str, kind: str, colour: np.ndarray, size: tuple[int, int],
         centre: tuple[float, float], width: float) -> Image.Image | None:  # fmt: skip
    """Weather body ``name`` lit as ``kind`` (:data:`WEATHER`) in ``colour``
    (RGB 0 to 1) on a clear picture of ``size``: its square picture centred
    at ``centre`` (fractions allowed), ``width`` pixels wide. Averaged as an
    area, so small symbols have no steps. None without the layers."""
    data = WEATHER_BODIES.get()
    if data is None:
        return None
    w, h = int(size[0]), int(size[1])
    source = data[name]
    q = source.shape[1] / width  # source pixels per pixel
    fx0, fy0 = centre[0] - width / 2, centre[1] - width / 2
    x0, y0 = max(0, math.floor(fx0)), max(0, math.floor(fy0))
    x1, y1 = min(w, math.ceil(fx0 + width)), min(h, math.ceil(fy0 + width))
    out = np.zeros((h, w, 4), np.uint8)
    if x1 <= x0 or y1 <= y0:
        return Image.fromarray(out, "RGBA")
    margin = math.ceil(q) + 2
    box = (margin + (x0 - fx0) * q, margin + (y0 - fy0) * q,
           margin + (x1 - fx0) * q, margin + (y1 - fy0) * q)  # fmt: skip
    layers = np.stack([
        np.asarray(Image.fromarray(np.pad(source[i], margin), "F").resize(
            (x1 - x0, y1 - y0), Image.Resampling.BOX, box=box))
        for i in range(4)
    ])  # fmt: skip
    rgb = tint(_plain(layers).reshape(3, -1), colour, WEATHER[kind])
    out[y0:y1, x0:x1] = rgba(rgb.reshape(y1 - y0, x1 - x0, 3), np.clip(layers[3], 0, 1))
    return Image.fromarray(out, "RGBA")


def crescent(image: Image.Image, cx: float, cy: float, r: float) -> Image.Image:
    """``image`` (a lit disc of radius ``r`` at cx, cy) cut to the moon's
    crescent, as the flat moon icon: an offset disc takes the rest."""
    s = 4
    w, h = image.size
    mask = Image.new("L", (w * s, h * s), 0)
    draw = ImageDraw.Draw(mask)
    R, X, Y = r * s, cx * s, cy * s
    draw.ellipse([X - R, Y - R, X + R, Y + R], fill=255)
    draw.ellipse([X - 0.45 * R, Y - 1.15 * R, X + 1.55 * R, Y + 0.85 * R], fill=0)
    mask = mask.reduce(s)
    out = image.copy()
    out.putalpha(Image.fromarray(
        (np.asarray(image.getchannel("A"), np.float32) * np.asarray(mask, np.float32) / 255)
        .astype(np.uint8)))  # fmt: skip
    return out

"""Dust that falls into the depth behind the panel (after SPUR II's depth space).

Fine grains are born at the bright things on the panel (letters, rails, rings,
bars) and fall away from them towards a vanishing point behind the glass: their
paths contract, they shrink, fade and sharpen, and the near ones are soft bokeh
discs. Near grains cover their way faster than far ones, so the eye reads depth
instead of motion.

The sources are the elements themselves. Rather than asking a theme for a list
of boxes, the dust reads a finished frame (the one the panel showed): every
pixel over a brightness threshold is a source, and a grain takes the colour of
its source, so a cyan rail gives off cyan dust and white text white. Any
theme, any layout, nothing to declare. Every few seconds the stock of sources
is taken from the frame again; a grain remembers where and what colour its
source was, so a new stock never makes a living grain jump.

It is cheap enough for every frame of a video panel. The grains move ``rate``
times a second (numpy places them all in one pass: each of the small ones is a
3 x 3 pixel B-spline, and only the few near ones are discs pasted through a
mask), and each frame only lays that onto a copy of the composed frame. A
bright pixel hides the dust over it, as if it lay behind the element.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageChops

from libre_panel.theme.model import DUST_SPEC

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "light3d" / "particle"

REFERENCE_AREA = 1920 * 480  # the panel ``count`` is given for
MIN_COUNT = 24  # even a tiny panel gets a few grains
SEED = 11
STAGES = 8  # sprite steps, from small and sharp (far) to large and soft (near)
THRESHOLD = 80  # an element is a pixel whose brightest channel is this much or more
MAX_SOURCES = 4000  # the stock: this many source pixels are drawn
SOURCES_EVERY_S = 4.0  # the stock is taken from the shown frame this often
SOURCES_RETRY_S = 0.5  # ... and this often while there is none (a blank frame)
TOAST_SETTLE_S = 0.5  # a toast has rolled in by now: the stock is taken once more
LIFE = (9.0, 20.0)  # seconds from the front to the back
VANISH = (0.5, 0.44)  # where the paths meet, as a share of the panel
SHRINK = 0.2  # the share of the way to the vanishing point at ``depth`` 1
FALL = (2.0, 13.0)  # drift over a life, px on a panel 480 px high: slightly down
WOBBLE = 1.6  # sideways sway, px on a panel 480 px high
SPEED = 1.28  # grains age this much faster than the clock
OPACITY = 0.70  # the brightest grain, as a share of its source's colour
NEAR_SHARE = 0.05  # this many grains are soft discs
DISC_OPACITY = 0.38  # a disc is weaker than a fine grain: it covers far more area
LEVELS = 24  # brightness steps of the disc masks
HIDE = (60, 150)  # a pixel this bright hides a grain fully, this dark not at all
STEP = 4  # sub-pixel positions of a disc, per pixel and axis
MIN_LEVEL = 2  # a disc fainter than this is a haze nobody sees: not drawn
MIN_ADD = 3  # nor a pixel of a fine grain that adds less than this

# how much of a grain shows over a pixel of each brightness: a bright element covers
# it, as if the dust lay behind
_SHOWN = np.clip((HIDE[1] - np.arange(256, dtype=np.float32)) / (HIDE[1] - HIDE[0]), 0.0, 1.0)

_lock = threading.Lock()
_sprites: list[tuple[int, np.ndarray, float]] = []  # per stage: radius, kernel (peak 1), peak ratio
_masks: dict[tuple[int, int, int, int], Image.Image] = {}
_STEPS: list[tuple[np.ndarray, np.ndarray]] = []


def sprites() -> list[tuple[int, np.ndarray, float]]:
    """Per stage ``(radius, kernel, peak)``: the disc as brightness 0..1 around its
    centre (``2 * radius`` px square, brightest 1), and how bright its centre is
    next to the smallest stage's when both carry the same light."""
    if _sprites:
        return _sprites
    with _lock:
        if _sprites:
            return _sprites
        built = []
        for n in range(STAGES):
            with Image.open(ASSETS / f"step_{n}.png") as image:
                a = np.asarray(image.convert("RGBA"), np.float32) / 255
            light = a[..., :3].mean(-1) * a[..., 3]
            ys, xs = np.nonzero(light > light.max() * 0.01)
            c = light.shape[0] // 2
            r = max(3, int(max(abs(xs - c).max(), abs(ys - c).max())) + 2)
            kernel = light[c - r : c + r, c - r : c + r]
            built.append((r, kernel / kernel.max(), float(kernel.max() / kernel.sum())))
        base = built[0][2]
        _sprites.extend((r, kernel, peak / base) for r, kernel, peak in built)
    return _sprites


def _steps() -> tuple[np.ndarray, np.ndarray]:
    """Per step of the sprites, the peak ratio and the radius, as arrays (read once)."""
    if _STEPS:
        return _STEPS[0]
    found = sprites()
    arrays = (
        np.array([s[2] for s in found], np.float32),
        np.array([s[0] for s in found], np.int32),
    )
    _STEPS.append(arrays)
    return arrays


def disc_mask(stage: int, qx: int, qy: int, level: int) -> Image.Image:
    """The mask (L) of a disc of ``stage``, placed ``qx`` / ``qy`` quarter pixels off its
    pixel, at brightness ``level`` of :data:`LEVELS`. Kept: a few hundred at most."""
    key = (stage, qx, qy, level)
    mask = _masks.get(key)
    if mask is None:
        _r, kernel, _peak = sprites()[stage]
        fx, fy = qx / STEP, qy / STEP
        p = np.pad(kernel, 1)  # a shift by a fraction of a pixel: the four neighbours' mix
        moved = (
            (1 - fx) * (1 - fy) * p[1:-1, 1:-1]
            + fx * (1 - fy) * p[1:-1, :-2]
            + (1 - fx) * fy * p[:-2, 1:-1]
            + fx * fy * p[:-2, :-2]
        )
        # each position has its own peak after the shift: level out, or a disc flickers
        values = moved / max(float(moved.max()), 1e-9) * (level / LEVELS) * DISC_OPACITY * 255
        mask = Image.fromarray(np.clip(values + 0.5, 0, 255).astype(np.uint8), "L")
        if len(_masks) > 6000:
            _masks.clear()
        _masks[key] = mask
    return mask


def _brightest(rgba: np.ndarray) -> np.ndarray:
    """The brightest of the first three channels of each row of ``(n, 3)`` or ``(n, 4)``
    (a reduction over so short an axis is several times slower than two ``maximum``s)."""
    return np.maximum(np.maximum(rgba[:, 0], rgba[:, 1]), rgba[:, 2])


class Discs(NamedTuple):
    """The near grains of one update: where each disc's corner goes, its colour and
    mask, a pixel of its centre (what hides it), and what is needed to make a fainter mask."""

    x0: list[int]
    y0: list[int]
    rgb: list[tuple[int, int, int]]
    masks: list[Image.Image]
    inside: np.ndarray
    stage: np.ndarray
    qx: np.ndarray
    qy: np.ndarray
    level: np.ndarray
    clips: list[Image.Image | None]  # where an area cuts the disc (L, as large as its mask)


class _Taps(dict):
    """The 3 x 3 pixel offsets of a fine grain, for a row ``width`` pixels long."""

    def __missing__(self, width: int) -> np.ndarray:
        taps = self[width] = np.array(
            [dy * width + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1)], np.int64
        )
        return taps


_TAPS = _Taps()


class Canvas:
    """RGBA memory that numpy and Pillow both write in place.

    Pillow keeps an image made from a buffer read-only and copies it on the first
    change; lifting that makes ``paste`` write into the array. The dust draws
    discs through Pillow and the small grains through numpy on the same pixels,
    without a copy of the whole frame in between. Should a Pillow ever not allow
    it (checked once), ``linked`` is False and :class:`Dust` takes the slower way.
    """

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.array = np.zeros((size[1], size[0], 4), np.uint8)
        self.image = Image.frombuffer("RGBA", size, self.array, "raw", "RGBA", 0, 1)
        self.image.readonly = 0
        probe = Image.new("RGBA", (1, 1), (1, 2, 3, 4))
        self.image.paste(probe, (0, 0))
        self.linked = tuple(self.array[0, 0]) == (1, 2, 3, 4)

    def load(self, canvas: Image.Image) -> None:
        self.image.paste(canvas)


class Dust:
    """The dust of one panel: its grains, the stock of sources, and the drawing.

    ``config`` is the theme's validated ``look.dust`` (see ``DUST_SPEC``);
    ``colors`` its colours as RGB (none: the colour of the source). Everything
    random comes from one generator, so a seed and a sequence of times give the
    same dust.
    """

    def __init__(
        self,
        size: tuple[int, int],
        config: dict[str, Any] | None = None,
        colors: list[tuple[int, int, int]] | None = None,
        seed: int = SEED,
    ) -> None:
        self.width, self.height = size
        cfg = {**{k: v[1] for k, v in DUST_SPEC.items()}, "areas": [], **(config or {})}
        area = self.width * self.height
        self.count = (
            0 if cfg["count"] <= 0 else max(MIN_COUNT, round(cfg["count"] * area / REFERENCE_AREA))
        )
        self.rate = float(cfg["rate"])
        self.depth = float(cfg["depth"])
        self.strength = float(cfg["strength"])
        self.palette = np.asarray(colors, np.float32) if colors else None
        self.allowed = self._allowed(cfg["areas"])
        self.table: np.ndarray | None = None  # a sum over any box of ``allowed``, in O(1)
        self.cut: Image.Image | None = None  # ``allowed`` as a mask, to clip discs with
        if self.allowed is not None:
            self.table = np.zeros((self.height + 1, self.width + 1), np.int32)
            self.table[1:, 1:] = self.allowed.cumsum(axis=0).cumsum(axis=1)
            self.cut = Image.fromarray(self.allowed.astype(np.uint8) * 255, "L")
        self.unit = min(self.width, self.height) / 480  # sizes in px that go with the panel
        self.rng = np.random.default_rng(seed)
        self.sources: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None  # x, y, colour
        self.renewed: float | None = None  # when the stock was taken
        self.renew_at: float | None = None  # a stock to take at this time (a toast came)
        self.toast: Any = None  # the toast whose beginning was seen
        self.updated: float | None = None  # when the grains last moved
        self.grains: dict[str, np.ndarray] | None = None
        self.taps: tuple[np.ndarray, np.ndarray] | None = None  # pixels the small grains cover
        self.discs: Discs | None = None
        self.canvas = Canvas(size)
        self.renewals = 0  # how often the stock was taken (tests, the bench)

    # -- the frame ------------------------------------------------------------

    def apply(
        self,
        canvas: Image.Image,
        now: float,
        shown: Image.Image | None = None,
        toast: tuple[Any, float] | None = None,
    ) -> Image.Image:
        """``canvas`` (RGBA, the composed frame) with the dust over it, at ``now``.

        ``shown`` is the frame the panel showed last (the stock is taken from it,
        else from ``canvas``), ``toast`` the toast on show and its age. The image
        returned may be this dust's own memory: convert or copy it before the next call.
        """
        if self.count <= 0 or canvas.size != (self.width, self.height):
            return canvas
        self._watch(
            now, shown if shown is not None and shown.size == canvas.size else canvas, toast
        )
        if self.sources is None:
            return canvas
        self._move(now)
        return self._draw(canvas)

    # -- sources ----------------------------------------------------------------

    def _watch(self, now: float, frame: Image.Image, toast: tuple[Any, float] | None) -> None:
        """Take the stock of sources when it is due: the first time, every few
        seconds, when a toast begins, and again once it has rolled in."""
        if toast is None:
            self.toast = None
        elif toast[0] is not self.toast:
            self.toast = toast[0]
            self.renew_at = now + TOAST_SETTLE_S
            self._renew(frame, now)
            return
        due = self.renewed is None or not 0 <= now - self.renewed < self._interval()
        if self.renew_at is not None and now >= self.renew_at:
            self.renew_at, due = None, True
        if due:
            self._renew(frame, now)

    def _interval(self) -> float:
        return SOURCES_EVERY_S if self.sources is not None else SOURCES_RETRY_S

    def _renew(self, frame: Image.Image, now: float) -> None:
        self.renewed = now
        stock = self._stock(frame)
        if stock is None:
            return  # nothing bright: the grains go on from the stock they have
        self.renewals += 1
        first = self.sources is None
        self.sources = stock
        if first:
            self._birth()

    def _stock(self, frame: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Source points ``(x, y, colour)`` drawn from the bright pixels of ``frame``.

        Read at half size (a thin rail still counts) and over all channels (a
        red theme is as much a source as a cyan one); a point lies anywhere in
        its half-size pixel.
        """
        small = frame.reduce(2).convert("RGB")
        r, g, b = small.split()  # the brightest channel in Pillow: numpy takes 6 ms to reduce
        peak = ImageChops.lighter(ImageChops.lighter(r, g), b)  # over so short an axis
        bright = np.asarray(peak) >= THRESHOLD
        if self.allowed is not None:
            bright &= self.allowed[::2, ::2]
        ys, xs = np.nonzero(bright)
        if xs.size == 0:
            return None
        if xs.size > MAX_SOURCES:
            # drawn with replacement, evenly: weighing by brightness (without) costs
            # 14 ms and a bright wordmark brings plenty of points anyway
            pick = self.rng.integers(0, xs.size, MAX_SOURCES)
            ys, xs = ys[pick], xs[pick]
        x = (xs * 2 + self.rng.random(xs.size) * 2).astype(np.float32)
        y = (ys * 2 + self.rng.random(ys.size) * 2).astype(np.float32)
        return x, y, np.asarray(small, np.uint8)[ys, xs].astype(np.float32)

    # -- grains -------------------------------------------------------------------

    def _birth(self) -> None:
        n, rng = self.count, self.rng
        life = rng.uniform(LIFE[0], LIFE[1], n).astype(np.float32)
        self.grains = {
            "life": life,
            "age": (rng.random(n) * life).astype(np.float32),  # spread over their lives
            "sway": rng.uniform(-1.0, 1.0, n).astype(np.float32),
            "phase": rng.uniform(0, 2 * math.pi, n).astype(np.float32),
            "near": rng.random(n) < NEAR_SHARE,
            "x": np.zeros(n, np.float32),
            "y": np.zeros(n, np.float32),
            "color": np.zeros((n, 3), np.float32),
        }
        self._rehome(np.ones(n, bool))

    def _rehome(self, which: np.ndarray) -> None:
        """Give the grains in ``which`` a new source. Its place and colour go into the
        grain, not a reference into the stock: the stock may be replaced any time."""
        n = int(which.sum())
        if not n or self.sources is None or self.grains is None:
            return
        sx, sy, sc = self.sources
        i = self.rng.integers(0, sx.size, n)
        g = self.grains
        g["x"][which], g["y"][which] = sx[i], sy[i]
        if self.palette is None:
            g["color"][which] = sc[i]
        else:
            g["color"][which] = self.palette[self.rng.integers(0, len(self.palette), n)]

    def _move(self, now: float) -> None:
        """Age the grains (``rate`` times a second at most) and work out what they cover."""
        if self.updated is not None and 0 <= now - self.updated < 1 / self.rate - 1e-6:
            return
        dt = 0.0 if self.updated is None else min(0.25, max(0.0, now - self.updated))
        self.updated = now
        g = self.grains
        assert g is not None
        g["age"] += dt * SPEED
        done = g["age"] >= g["life"]
        if done.any():
            n, rng = int(done.sum()), self.rng
            g["life"][done] = rng.uniform(LIFE[0], LIFE[1], n)
            g["age"][done] = 0.0
            g["sway"][done] = rng.uniform(-1.0, 1.0, n)
            g["phase"][done] = rng.uniform(0, 2 * math.pi, n)
            g["near"][done] = rng.random(n) < NEAR_SHARE
            self._rehome(done)
        x, y, z, light = self._layout(now)
        self.taps = self._small(x, y, light, ~g["near"])
        self.discs = self._near(x, y, z, light, g["near"])

    def _layout(self, now: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Where each grain is, how deep (1 front, 0 back) and how bright (0..1)."""
        g, u = self.grains, self.unit
        assert g is not None
        z = np.clip(1.0 - g["age"] / g["life"], 0.0, 1.0)
        shrink = max(0.0, 1.0 - SHRINK * self.depth)
        s = shrink + (1.0 - shrink) * z  # the way left to the vanishing point
        fx, fy = VANISH[0] * self.width, VANISH[1] * self.height
        gone = 1.0 - z
        sway = WOBBLE * u * g["sway"] * np.sin(g["phase"] + (0.5 * now) % (2 * math.pi))
        x = fx + (g["x"] - fx) * s + FALL[0] * u * gone + sway
        y = fy + (g["y"] - fy) * s + FALL[1] * u * gone
        # fading in at the source and out in the depth; in between dimmer with distance
        light = (
            np.clip(gone / 0.08, 0, 1) * np.clip(z / 0.22, 0, 1) * (0.42 + 0.58 * z**1.1)
        ).astype(np.float32)
        return x.astype(np.float32), y.astype(np.float32), z, light

    def _small(
        self, x: np.ndarray, y: np.ndarray, light: np.ndarray, small: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """The pixels the fine grains cover and what each adds (before it is hidden).

        A grain is a quadratic B-spline on 3 x 3 pixels, not a bilinear one on 4: a
        grain of one pixel flickered as it moved, its peak swinging between a
        quarter and full strength with where it lay; the spline's lies between a
        half and three quarters, and the grain is softer and looks finer.
        """
        w, h = self.width, self.height
        g = self.grains
        assert g is not None
        i = np.nonzero(small & (x > 2) & (x < w - 3) & (y > 2) & (y < h - 3) & (light > 0.015))[0]
        if i.size == 0:
            return None
        xi, yi = x[i].astype(np.int32), y[i].astype(np.int32)
        t = np.stack([x[i] - xi, y[i] - yi])  # (2, m): the position inside the pixel
        spline = np.stack([0.5 * (1 - t) ** 2, 0.75 - (t - 0.5) ** 2, 0.5 * t**2], axis=-1)
        weight = (spline[1][:, :, None] * spline[0][:, None, :]).reshape(i.size, 9)  # y, then x
        value = g["color"][i] * (light[i] * OPACITY * self.strength)[:, None]
        # only the pixels that add something to see: the spline's corners mostly do not
        peak = weight * _brightest(value)[:, None]
        hit = np.flatnonzero(peak.reshape(-1) >= MIN_ADD)
        rows = hit // 9
        add = (weight.reshape(-1)[hit][:, None] * value[rows]).astype(np.int16)
        return (yi * w + xi)[rows] + _TAPS[w][hit - rows * 9], add

    def _near(
        self, x: np.ndarray, y: np.ndarray, z: np.ndarray, light: np.ndarray, near: np.ndarray
    ) -> Discs | None:
        """The soft discs: where, which step, how bright (a level) and what colour."""
        w, h = self.width, self.height
        g = self.grains
        assert g is not None
        i = np.nonzero(near & (light > 0.02) & (x > -20) & (x < w + 20) & (y > -20) & (y < h + 20))[
            0
        ]
        if i.size == 0:
            return None
        stage = np.clip((z[i] * STAGES).astype(np.int32), 0, STAGES - 1)
        peaks, radii = _steps()
        # the same energy on more area: the peak falls with the step, which is the blur
        spike = light[i] * peaks[stage]
        level = (np.minimum(1.0, spike * 1.8 * self.strength) * LEVELS + 0.5).astype(np.int32)
        ix, iy = np.floor(x[i]).astype(np.int32), np.floor(y[i]).astype(np.int32)
        qx = ((x[i] - ix) * STEP).astype(np.int32)
        qy = ((y[i] - iy) * STEP).astype(np.int32)
        radius = radii[stage]
        keep = level >= MIN_LEVEL
        ix, iy, stage, qx, qy, level, radius = (
            c[keep] for c in (ix, iy, stage, qx, qy, level, radius)
        )
        rgb = g["color"][i][keep].astype(np.int32)
        if not ix.size:
            return None
        keys = zip(stage.tolist(), qx.tolist(), qy.tolist(), level.tolist(), strict=True)
        masks = [disc_mask(*key) for key in keys]
        x0, y0 = ix - radius, iy - radius
        clips = self._clips(x0, y0, 2 * radius)
        for j, clip in enumerate(clips):
            if clip is not None:
                masks[j] = ImageChops.multiply(masks[j], clip)
        inside = (np.clip(iy, 0, h - 1) * w + np.clip(ix, 0, w - 1)).astype(np.int64)
        return Discs(
            x0.tolist(), y0.tolist(), list(map(tuple, rgb.tolist())), masks, inside,
            stage, qx, qy, level, clips,
        )  # fmt: skip

    def _clips(self, x0: np.ndarray, y0: np.ndarray, size: np.ndarray) -> list[Image.Image | None]:
        """For each disc box (corner and size) the part of ``allowed`` under it, or None
        where the whole box is allowed (almost all of them)."""
        if self.table is None or self.cut is None:
            return [None] * len(x0)
        w, h = self.width, self.height
        ax0, ay0 = np.clip(x0, 0, w), np.clip(y0, 0, h)
        ax1, ay1 = np.clip(x0 + size, 0, w), np.clip(y0 + size, 0, h)
        t = self.table
        allowed = t[ay1, ax1] - t[ay0, ax1] - t[ay1, ax0] + t[ay0, ax0]
        partly = allowed < (ax1 - ax0) * (ay1 - ay0)
        boxes = zip(
            x0.tolist(), y0.tolist(), (x0 + size).tolist(), (y0 + size).tolist(), strict=True
        )
        return [
            self.cut.crop(box) if cut else None
            for box, cut in zip(boxes, partly.tolist(), strict=True)
        ]

    # -- drawing ------------------------------------------------------------------

    def _draw(self, canvas: Image.Image) -> Image.Image:
        c = self.canvas
        if not c.linked:
            return self._draw_copy(canvas)
        c.load(canvas)
        self._lay_discs(c.image, c.array)
        self._lay_small(c.array)
        return c.image

    def _lay_small(self, array: np.ndarray) -> None:
        if self.taps is None:
            return
        index, add = self.taps
        flat = array.view(np.uint32).reshape(-1)  # a pixel in one word: a gather, not row copies
        pixels = flat[index]
        rgb = pixels.view(np.uint8).reshape(-1, 4)  # the gathered copy, by channel
        show = _SHOWN[_brightest(rgb)]
        if self.allowed is not None:
            show = show * self.allowed.reshape(-1)[index]
        covered = np.flatnonzero(show < 1.0)  # over an element, or out of bounds: fainter
        lit = rgb[:, :3] + add
        if covered.size:
            lit[covered] = rgb[covered, :3] + (add[covered] * show[covered, None]).astype(np.int16)
        rgb[:, :3] = np.minimum(lit, 255)
        flat[index] = pixels

    def _lay_discs(self, image: Image.Image, array: np.ndarray) -> None:
        d = self.discs
        if d is None:
            return
        words = array.view(np.uint32).reshape(-1)[d.inside]
        show = _SHOWN[_brightest(words.view(np.uint8).reshape(-1, 4))]
        masks = d.masks
        dim = np.nonzero(show < 0.999)[0]
        if dim.size:  # over an element: a fainter disc, or none
            masks = list(masks)
            for j in dim.tolist():
                level = int(d.level[j] * show[j] + 0.5)
                mask = (
                    disc_mask(int(d.stage[j]), int(d.qx[j]), int(d.qy[j]), level) if level else None
                )
                clip = d.clips[j]
                masks[j] = (
                    ImageChops.multiply(mask, clip)
                    if mask is not None and clip is not None
                    else mask
                )
        for x0, y0, rgb, mask in zip(d.x0, d.y0, d.rgb, masks, strict=True):
            if mask is not None:
                image.paste(rgb, (x0, y0), mask)

    def _draw_copy(self, canvas: Image.Image) -> Image.Image:
        """The same through copies, when the canvas cannot be written in place."""
        size = (self.width, self.height)
        image = canvas.copy()
        words = np.frombuffer(image.tobytes(), np.uint32).copy().reshape(self.height, self.width)
        self._lay_discs(image, words)  # discs through Pillow on the image itself ...
        words = np.frombuffer(image.tobytes(), np.uint32).copy().reshape(self.height, self.width)
        self._lay_small(words)  # ... then the small grains on its pixels
        return Image.frombytes("RGBA", size, words.tobytes())

    # -- areas ----------------------------------------------------------------

    def _allowed(self, areas: list[dict[str, Any]]) -> np.ndarray | None:
        """Where dust may be: with areas that say ``dust: true``, only there, else
        everywhere; areas that say ``dust: false`` are cut out in both cases."""
        if not areas:
            return None
        on = [a for a in areas if a.get("dust", True)]
        mask = (
            np.zeros((self.height, self.width), bool)
            if on
            else np.ones((self.height, self.width), bool)
        )
        for area in on:
            self._rect(mask, area, True)
        for area in areas:
            if not area.get("dust", True):
                self._rect(mask, area, False)
        return mask

    def _rect(self, mask: np.ndarray, area: dict[str, Any], value: bool) -> None:
        x0, y0 = max(0, area["x"]), max(0, area["y"])
        x1, y1 = min(self.width, area["x"] + area["w"]), min(self.height, area["y"] + area["h"])
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = value

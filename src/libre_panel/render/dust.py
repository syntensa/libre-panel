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
times a second, and numpy works out every pixel they cover in one pass: a fine
grain is a 3 x 3 pixel B-spline, a near one the sprite of a disc. A frame then
only lays that list onto a copy of the composed frame, in one gather, one blend
and one scatter, however many grains there are. That matters beyond speed:
every numpy or Pillow call on a big array gives up the interpreter lock, and
with the renderer's helper thread at work each call can wait for it, so a
hundred small pastes cost many times what one big blend does. A bright pixel
hides the dust over it, as if it lay behind the element.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops

from libre_panel.theme.model import DUST_SPEC

log = logging.getLogger(__name__)

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
MIN_DISC = 0.0625  # a disc fainter than this share of full is a haze nobody sees
CUT = 0.03  # the tail of a disc below this share of its peak is left off
HIDE = (60, 150)  # a pixel this bright hides a grain fully, this dark not at all
STEP = 4  # sub-pixel positions of a disc, per pixel and axis
SUB = 16  # ... and of a fine grain (a power of two)
SHIFT = SUB.bit_length() - 1
MIN_ADD = 3  # a pixel of a grain that adds less than this is left out
MAX_ADD = 255 - HIDE[0]  # ... and one adds at most this: where a grain shows, a pixel is at
# most HIDE[0] bright, and the brighter it is the less shows, so the sum never passes 255
CENTRE = 0.5625  # the heaviest tap of a fine grain: 0.75 x 0.75
CURVE = 1024  # steps of the brightness along a grain's way

# how much of a grain shows over a pixel of each brightness: a bright element covers
# it, as if the dust lay behind
_SHOWN = np.clip((HIDE[1] - np.arange(256, dtype=np.float32)) / (HIDE[1] - HIDE[0]), 0.0, 1.0)

_lock = threading.Lock()
_sprites: list[tuple[int, np.ndarray, float]] = []  # per stage: radius, kernel (peak 1), peak ratio
_stages: list[Stage] = []
_PEAKS: list[np.ndarray] = []


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


def _peaks() -> np.ndarray:
    """Per step of the sprites, how bright its centre is next to the first step's (read once)."""
    if not _PEAKS:
        _PEAKS.append(np.array([s[2] for s in sprites()], np.float32))
    return _PEAKS[0]


class Stage:
    """One step of the sprites as the taps that count: where each lies from the disc's
    pixel (``dy``, ``dx``), and for each quarter-pixel position (``qx * STEP + qy``) how
    high the disc is there (peak 1)."""

    def __init__(self, radius: int, dy: np.ndarray, dx: np.ndarray, heights: np.ndarray) -> None:
        self.radius, self.dy, self.dx, self.heights = radius, dy, dx, heights
        self._offsets: dict[int, np.ndarray] = {}

    def offsets(self, width: int) -> np.ndarray:
        """The taps as offsets into a flat frame ``width`` pixels wide."""
        found = self._offsets.get(width)
        if found is None:
            found = self._offsets[width] = (self.dy * width + self.dx).astype(np.int64)
        return found


def stages() -> list[Stage]:
    """The discs of every step, shifted by every quarter pixel (a few hundred taps each)."""
    if _stages:
        return _stages
    with _lock:
        if _stages:
            return _stages
        built = []
        for radius, kernel, _peak in sprites():
            p = np.pad(kernel, 1)  # a shift by a fraction of a pixel: the four neighbours' mix
            shifted = []
            for qx in range(STEP):
                for qy in range(STEP):
                    fx, fy = qx / STEP, qy / STEP
                    moved = (
                        (1 - fx) * (1 - fy) * p[1:-1, 1:-1]
                        + fx * (1 - fy) * p[1:-1, :-2]
                        + (1 - fx) * fy * p[:-2, 1:-1]
                        + fx * fy * p[:-2, :-2]
                    )
                    # each position has its own peak after the shift: level out, or a disc flickers
                    shifted.append(moved / max(float(moved.max()), 1e-9))
            stack = np.stack(shifted)
            ys, xs = np.nonzero((stack >= CUT).any(axis=0))
            built.append(
                Stage(radius, ys - radius, xs - radius, stack[:, ys, xs].astype(np.float32))
            )
        _stages.extend(built)
    return _stages


def _spline() -> np.ndarray:
    """The nine weights of a fine grain at each of ``SUB`` x ``SUB`` positions inside its
    pixel, row ``qy * SUB + qx``, rows of the grid first. A quadratic B-spline on 3 x 3
    pixels, not a bilinear one on 4: a grain of one pixel flickered as it moved, its
    peak swinging between a quarter and full strength with where it lay; the spline's
    lies between a half and three quarters, and the grain is softer and looks finer."""
    t = (np.arange(SUB, dtype=np.float32) + 0.5) / SUB  # the middle of each step
    taps = np.stack([0.5 * (1 - t) ** 2, 0.75 - (t - 0.5) ** 2, 0.5 * t**2], axis=-1)
    return (taps[:, None, :, None] * taps[None, :, None, :]).reshape(SUB * SUB, 9)


def _curve() -> np.ndarray:
    """How bright a grain is as a function of its depth ``z`` (1 front, 0 back), in
    ``CURVE`` steps: faded in at the source and out in the depth, in between dimmer with
    distance."""
    z = np.linspace(0.0, 1.0, CURVE + 1, dtype=np.float32)
    rise = np.clip((1 - z) / 0.08, 0, 1) * np.clip(z / 0.22, 0, 1)
    return (rise * (0.42 + 0.58 * z**1.1)).astype(np.float32)


_SPLINE = _spline()
_CURVE = _curve()


class _Taps(dict):
    """The 3 x 3 pixel offsets of a fine grain, for a row ``width`` pixels long."""

    def __missing__(self, width: int) -> np.ndarray:
        taps = self[width] = np.array(
            [dy * width + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1)], np.int64
        )
        return taps


_TAPS = _Taps()


def _brightest(rgba: np.ndarray) -> np.ndarray:
    """The brightest of the first three channels of each row of ``(n, 3)`` or ``(n, 4)``
    (a reduction over so short an axis is several times slower than two ``maximum``s)."""
    return np.maximum(np.maximum(rgba[:, 0], rgba[:, 1]), rgba[:, 2])


class Canvas:
    """RGBA memory that numpy and Pillow both write in place.

    Pillow keeps an image made from a buffer read-only and copies it on the first
    change; lifting that makes ``paste`` write into the array. The dust takes the
    composed frame in through Pillow and lays its grains through numpy on the same
    pixels, without a second copy of the whole frame in between. Should a Pillow ever
    not allow it (checked once), ``linked`` is False and :class:`Dust` takes the
    slower way.
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
    random comes from generators seeded from ``seed``, so a seed and a sequence of
    times give the same dust.

    ``submit`` (set or cleared at any time) runs a job in a helper thread and gives
    back its future. With it, the grains are moved there, ahead of the frame that
    shows them, and a frame lays what was moved last; without it they move in the
    frame itself. The work is the same either way, and so is the dust.
    """

    def __init__(
        self,
        size: tuple[int, int],
        config: dict[str, Any] | None = None,
        colors: list[tuple[int, int, int]] | None = None,
        seed: int = SEED,
        submit: Callable[[Callable[[], Any]], Future] | None = None,
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
        self.unit = min(self.width, self.height) / 480  # sizes in px that go with the panel
        self.vanish = VANISH[0] * self.width, VANISH[1] * self.height
        self.shrink = max(0.0, 1.0 - SHRINK * self.depth)
        grains, stock = np.random.SeedSequence(seed).spawn(2)  # a helper thread moves the one
        self.rng, self.stock_rng = np.random.default_rng(grains), np.random.default_rng(stock)
        self.submit = submit
        self.pending: Future | None = None  # the grains being moved in the helper thread
        self.sources: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None  # x, y, colour
        self.renewed: float | None = None  # when the stock was taken
        self.renew_at: float | None = None  # a stock to take at this time (a toast came)
        self.toast: Any = None  # the toast whose beginning was seen
        self.updated: float | None = None  # when the grains were last asked to move
        self.grains: dict[str, np.ndarray] | None = None
        self.near: np.ndarray = np.zeros(0, np.int64)  # which grains are discs
        # what the grains cover: the frame's pixel of each tap, what it adds, and how much of
        # the pixel it covers (a disc: its colour comes over it; a fine grain only adds), both
        # as RGBA lanes with the alpha lane at 0, to work on whole pixels
        self.taps: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
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
        """``canvas`` (RGBA, the composed frame) with the dust over it, at ``now``: a new
        RGB image, or ``canvas`` itself while there is no dust to draw yet.

        ``shown`` is the frame the panel showed last (the stock is taken from it,
        else from ``canvas``), ``toast`` the toast on show and its age.
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
            pick = self.stock_rng.integers(0, xs.size, MAX_SOURCES)
            ys, xs = ys[pick], xs[pick]
        x = (xs * 2 + self.stock_rng.random(xs.size) * 2).astype(np.float32)
        y = (ys * 2 + self.stock_rng.random(ys.size) * 2).astype(np.float32)
        return x, y, np.asarray(small, np.uint8)[ys, xs].astype(np.float32)

    # -- grains -------------------------------------------------------------------

    def _birth(self) -> None:
        n = self.count
        floats = ("life", "inv", "age", "sway", "phase", "dx", "dy", "peak")
        self.grains = {name: np.zeros(n, np.float32) for name in floats}
        self.grains["color"] = np.zeros((n, 3), np.float32)
        self.grains["near"] = np.zeros(n, bool)
        self.grains["fine"] = np.zeros(n, bool)
        self._spawn(np.arange(n), spread=True)

    def _spawn(self, which: np.ndarray, spread: bool = False) -> None:
        """Give the grains ``which`` (their numbers) a new life: how long, how it sways,
        whether it is near, and a source. Its place and colour go into the grain, not a
        reference into the stock: the stock may be replaced any time. ``spread`` puts the
        ages anywhere in the life (the first time), else they start at the front."""
        assert self.grains is not None and self.sources is not None
        g, sx, sy, sc = self.grains, *self.sources
        r = self.rng.random((7, which.size))
        life = LIFE[0] + r[0] * (LIFE[1] - LIFE[0])
        g["life"][which] = life
        g["inv"][which] = 1.0 / life
        g["age"][which] = r[1] * life if spread else 0.0
        g["sway"][which] = (r[2] * 2.0 - 1.0) * (WOBBLE * self.unit)
        g["phase"][which] = r[3] * (2 * math.pi)
        g["near"][which] = r[4] < NEAR_SHARE
        g["fine"][which] = r[4] >= NEAR_SHARE
        source = np.minimum((r[5] * sx.size).astype(np.int64), sx.size - 1)
        g["dx"][which] = sx[source] - self.vanish[0]
        g["dy"][which] = sy[source] - self.vanish[1]
        if self.palette is None:
            color = sc[source]
        else:
            color = self.palette[
                np.minimum((r[6] * len(self.palette)).astype(np.int64), len(self.palette) - 1)
            ]
        g["color"][which] = color
        g["peak"][which] = _brightest(color)
        self.near = np.flatnonzero(g["near"])

    def _move(self, now: float) -> None:
        """Move the grains when ``rate`` says it is time: here, or in the helper thread,
        where the frames go on with the grains they have until the new ones are ready."""
        self._collect()
        if self.pending is not None:
            return  # the last move is not done: one at a time, the grains are in its hands
        if self.updated is not None and 0 <= now - self.updated < 1 / self.rate - 1e-6:
            return
        since, self.updated = self.updated, now
        if self.submit is None:
            self.taps = self._advance(now, since)
            return
        self.pending = self.submit(lambda: self._advance(now, since))
        self._collect()  # done already, if the helper runs it in the same breath

    def _collect(self) -> None:
        """Take the grains the helper thread has moved."""
        if self.pending is None or not self.pending.done():
            return
        pending, self.pending = self.pending, None
        try:
            self.taps = pending.result()
        except Exception:
            log.exception(
                "dust: moving the grains in the helper thread failed; moving them in the frame"
            )
            self.submit = None

    def _advance(
        self, now: float, since: float | None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Age the grains to ``now`` (``since`` the last time) and work out what they cover."""
        dt = 0.0 if since is None else min(0.25, max(0.0, now - since))
        g = self.grains
        assert g is not None
        g["age"] += dt * SPEED
        done = np.flatnonzero(g["age"] >= g["life"])
        if done.size:
            self._spawn(done)
        x, y, z, light = self._layout(now)
        parts = [self._small(x, y, light), self._discs(x, y, z, light)]
        return self._cover([p for p in parts if p is not None])

    def _layout(self, now: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Where each grain is, how deep (1 front, 0 back) and how bright (0..1)."""
        g, u = self.grains, self.unit
        assert g is not None
        z = np.clip(1.0 - g["age"] * g["inv"], 0.0, 1.0)
        s = self.shrink + (1.0 - self.shrink) * z  # the way left to the vanishing point
        gone = 1.0 - z
        sway = g["sway"] * np.sin(g["phase"] + (0.5 * now) % (2 * math.pi))
        x = self.vanish[0] + g["dx"] * s + FALL[0] * u * gone + sway
        y = self.vanish[1] + g["dy"] * s + FALL[1] * u * gone
        return x, y, z, _CURVE[(z * CURVE + 0.5).astype(np.int32)]

    def _small(
        self, x: np.ndarray, y: np.ndarray, light: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """The pixels the fine grains cover and what each adds (before it is hidden):
        only those that add something to see, the spline's corners mostly do not."""
        w, h = self.width, self.height
        g = self.grains
        assert g is not None
        scale = light * (OPACITY * self.strength)
        peak = g["peak"] * scale  # the brightest channel of a grain, as it shines
        ok = g["fine"] & (peak >= MIN_ADD / CENTRE) & (x > 2) & (x < w - 3) & (y > 2) & (y < h - 3)
        i = np.flatnonzero(ok)
        if i.size == 0:
            return None
        xq = (x[i] * SUB).astype(np.int32)  # where, in 1 / SUB of a pixel
        yq = (y[i] * SUB).astype(np.int32)
        weights = _SPLINE[((yq & (SUB - 1)) << SHIFT) | (xq & (SUB - 1))]  # (grains, 9)
        hit = np.flatnonzero(weights * peak[i][:, None] >= MIN_ADD)
        rows, tap = np.divmod(hit, 9)
        add = (g["color"][i] * scale[i][:, None])[rows] * weights.reshape(-1)[hit][:, None]
        base = (yq >> SHIFT) * w + (xq >> SHIFT)
        index = base[rows] + _TAPS[w][tap]
        return index, add, np.zeros(index.size, np.float32)

    def _discs(
        self, x: np.ndarray, y: np.ndarray, z: np.ndarray, light: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """The pixels the near grains cover: for each disc the sprite of its step, at its
        quarter pixel, as tall as its brightness says and in its colour. Discs of a step
        are worked out together, as boxes of the same shape."""
        w, h = self.width, self.height
        g = self.grains
        assert g is not None
        i = self.near
        i = i[(light[i] > 0.02) & (x[i] > -20) & (x[i] < w + 20) & (y[i] > -20) & (y[i] < h + 20)]
        if i.size == 0:
            return None
        peaks = _peaks()
        step = np.minimum((z[i] * STAGES).astype(np.int32), STAGES - 1)
        # the same energy on more area: the peak falls with the step, which is the blur
        level = np.minimum(1.0, light[i] * peaks[step] * (1.8 * self.strength))
        keep = level >= MIN_DISC
        i, step, level = i[keep], step[keep], level[keep]
        if i.size == 0:
            return None
        ix, iy = np.floor(x[i]).astype(np.int32), np.floor(y[i]).astype(np.int32)
        quarter = ((x[i] - ix) * STEP).astype(np.int32) * STEP + ((y[i] - iy) * STEP).astype(
            np.int32
        )
        alpha = level * DISC_OPACITY
        color = g["color"][i]
        order = np.argsort(step, kind="stable")
        edges = np.searchsorted(step[order], np.arange(STAGES + 1))
        found: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for n, stage in enumerate(stages()):
            which = order[edges[n] : edges[n + 1]]
            if which.size == 0:
                continue
            r = stage.radius
            cx, cy = ix[which], iy[which]
            inside = (cx >= r) & (cx < w - r) & (cy >= r) & (cy < h - r)
            if not inside.all():  # a disc over the edge of the panel: a tap at a time
                found.extend(self._clipped(stage, which[~inside], ix, iy, quarter, alpha, color))
                which, cx, cy = which[inside], cx[inside], cy[inside]
                if which.size == 0:
                    continue
            height = stage.heights[quarter[which]] * alpha[which][:, None]  # (discs, taps)
            index = (cy * w + cx)[:, None] + stage.offsets(w)
            add = height[:, :, None] * color[which][:, None, :]
            found.append((index.reshape(-1), add.reshape(-1, 3), height.reshape(-1)))
        if not found:
            return None
        return (
            np.concatenate([f[0] for f in found]),
            np.concatenate([f[1] for f in found]),
            np.concatenate([f[2] for f in found]),
        )

    def _clipped(
        self,
        stage: Stage,
        which: np.ndarray,
        ix: np.ndarray,
        iy: np.ndarray,
        quarter: np.ndarray,
        alpha: np.ndarray,
        color: np.ndarray,
    ) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """The discs ``which`` of a step that reach over the panel's edge: the taps that
        fall on it, a disc at a time (there are only ever a few)."""
        w, h = self.width, self.height
        found = []
        for d in which.tolist():
            px, py = ix[d] + stage.dx, iy[d] + stage.dy
            inside = (px >= 0) & (px < w) & (py >= 0) & (py < h)
            height = stage.heights[quarter[d]][inside] * alpha[d]
            found.append(((py * w + px)[inside], height[:, None] * color[d], height))
        return found

    def _cover(
        self, parts: list[tuple[np.ndarray, np.ndarray, np.ndarray]]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """The taps of all the grains as one list, without those outside the areas, with
        what they add and cover as RGBA lanes (alpha 0: a frame's alpha is left alone)."""
        if not parts:
            return None
        index, add, cover = (np.concatenate([p[n] for p in parts]) for n in range(3))
        if self.allowed is not None:
            inside = self.allowed.reshape(-1)[index]
            index, add, cover = index[inside], add[inside], cover[inside]
        if not index.size:
            return None
        add4 = np.zeros((index.size, 4), np.float32)
        np.minimum(add, MAX_ADD, out=add4[:, :3])
        cover4 = np.zeros((index.size, 4), np.float32)
        cover4[:, :3] = cover[:, None]
        return index, add4, cover4

    # -- drawing ------------------------------------------------------------------

    def _draw(self, canvas: Image.Image) -> Image.Image:
        if self.taps is None:
            return canvas
        c = self.canvas
        if not c.linked:
            array = np.frombuffer(canvas.convert("RGBA").tobytes(), np.uint8).copy()
            array = array.reshape(self.height, self.width, 4)
            self._lay(array)
            return Image.fromarray(array).convert("RGB")
        c.load(canvas)
        self._lay(c.array)
        # pasting RGBA into an RGB image keeps the pixels as they are: a third of the
        # time Image.convert takes at this size
        out = Image.new("RGB", (self.width, self.height), None)  # all of it is pasted over
        out.paste(c.image)
        return out

    def _lay(self, array: np.ndarray) -> None:
        """Lay the taps onto the frame ``array`` (RGBA): one gather, one blend, one scatter.

        A tap blends towards what it brings by how much it covers: ``add`` is the colour a
        disc brings (times its opacity), a fine grain only adds; an element in front lets
        by ``show`` of either. Whole pixels at a time, as four float lanes: numpy is far
        slower on the three of four bytes of a pixel, and the alpha lane does not change
        (its ``add`` and ``cover`` are 0). There is nothing to clamp: ``add`` is at most
        ``MAX_ADD`` and a pixel it shows on is at most ``HIDE[0]`` bright."""
        assert self.taps is not None
        index, add, cover = self.taps
        flat = array.view(np.uint32).reshape(-1)  # a pixel in one word: a gather, not row copies
        pixels = flat[index]
        rgb = pixels.view(np.uint8).reshape(-1, 4)  # the gathered copy, by channel
        bright = np.maximum(rgb[:, 0], rgb[:, 1])
        np.maximum(bright, rgb[:, 2], out=bright)
        body = rgb.astype(np.float32)
        lit = body * cover
        np.subtract(add, lit, out=lit)
        lit *= _SHOWN[bright][:, None]
        lit += body
        rgb[:] = lit.astype(np.uint8)
        flat[index] = pixels

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

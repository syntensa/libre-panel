"""A theme's look at runtime (theme ``"look"``): SPUR II's grid and its panel light.

``look.grid`` lays thin lines over the background, every ``spacing`` px and
every ``major``-th one stronger, in the palette's colours: under every
widget, but over backdrops (full-panel rects at the bottom of the list), as
SPUR II's grid lies on its gradient. It is drawn once, like a background
picture.

``look.light`` is one light for the whole panel, as in SPUR II:

- A low light over the panel where the sun stands at the place in
  ``config.toml`` (``[weather]`` latitude and longitude; without it, noon):
  from the left in the morning, steep at noon, from the right in the
  evening, warm near the horizon, a cool weak light at night. Because it is
  a point, a straight edge does not shine along its whole length but where
  the reflection fits.
- At moments a sweep of light runs across the panel once: when the panel
  starts, on the full hour, on a new track (``media.title`` changes) and
  when a tube or entrance transition ends.

The light falls only on **light edges**: points with a direction (a normal)
that widgets register for the frame (:class:`LightEdge`,
``Renderer.add_light_edge``; plugins ``context.light_edge``). Outlines of
rects become ridges and register theirs. The gloss is worked out only on
those points, in 96 px tiles. The sun's gloss changes when the sun has moved
a step and lies in the frame as pieces of light that stay where nothing
moves (baked into the panel's background); a sweep's is added on top per
frame, only around the points it lights. Everything runs on the frame's planned time
(``now``, and ``snapshot.now`` for the sun), never on the clock mid-frame.
"""

from __future__ import annotations

import math
import weakref
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from libre_panel.render.finish import Finish
from libre_panel.render.light import role_color, smooth, sun_position
from libre_panel.render.renderer import Piece

# The guide light: Blender's sun, top left in front, fixed. It shades facets;
# a face turned to the viewer keeps its colour exactly.
GUIDE = np.array([-16.0, -16.0, 24.0], np.float32) / np.float32(math.sqrt(16**2 * 2 + 24**2))
GUIDE_TOP = float(GUIDE[2])
FACET_LIGHT = 0.85  # facets towards the guide light lighten this far towards white
RIDGE_SHADOW = 0.50  # ... and away from it darken to this (frames: background, kept quiet)
RIDGE_GLOW = 0.45  # how much of its colour a ridge's lit facets glow with
RIDGE_RADIUS = 7.0  # and how soft (480 px high panels)
RIDGE_TILT = 45.0  # degrees

SOFT_SHARE = 0.10  # the gloss: a short hard highlight (** 70) on a soft one (** 9)
TOP_GLOSS = 0.25  # faces turned to the viewer shine less than facets
SWEEP_REACH = 520.0  # a sweep is near: its gloss fades with distance (1920 px wide) ...
SWEEP_CUT = 1.6  # ... and is gone at this many reaches (nothing beyond is worked out)
TILE = 96  # edges are lit tile by tile
MARGIN = 14  # room around a tile's points for the halo of the gloss
HALO = 0.55  # the soft halo around a gloss ...
HALO_FROM = 30  # ... where it shines this much (of 255)
SHOWS = 5  # gloss below this (of 255) does not show: it is left out
TRANSITIONS = ("tube", "entrance")  # a sweep follows when one of these ends

Light = tuple[
    np.ndarray, tuple[float, float, float], float, str
]  # position, colour, strength, kind


# -- light edges ---------------------------------------------------------------------


def _runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every covered point of ``mask`` (h, w; bool): its index in the
    flattened mask and how far it is from the start and the end of its run
    in the row (both counted from 1). Only the covered points are looked at,
    not the whole picture."""
    h, w = mask.shape
    padded = np.zeros((h, w + 2), np.int8)
    padded[:, 1:-1] = mask
    flat = padded.ravel()
    step = np.diff(flat)
    starts = np.flatnonzero(step == 1) + 1
    ends = np.flatnonzero(step == -1) + 1  # the first point after a run
    index = np.flatnonzero(flat)
    run = np.searchsorted(starts, index, side="right") - 1
    row, column = np.divmod(index, w + 2)
    return row * w + (column - 1), index - starts[run] + 1, ends[run] - index


def ridge_normals(mask: Any, tilt: float = RIDGE_TILT) -> np.ndarray:
    """Normals (h, w, 3) that make every line of ``mask`` a ridge: each point
    tilts towards its nearest edge across the line (up, down, left, right),
    mitred where two are as near (corners). The middle of a line an odd
    number of points wide, and a line one point wide, stay flat."""
    covered = np.asarray(mask) > 0
    h, w = covered.shape
    normals = np.zeros((h, w, 3), np.float32)
    if not covered.any():
        return normals
    index, left, right = _runs(covered)
    across, up, down = _runs(covered.T)
    x, y = np.divmod(across, h)  # the transposed picture's rows are columns
    order = np.empty(h * w, np.int64)
    order[index] = np.arange(index.size)
    place = order[y * w + x]
    above, below = np.empty(index.size, np.float32), np.empty(index.size, np.float32)
    above[place], below[place] = up, down
    distance = np.stack([above, below, left.astype(np.float32), right.astype(np.float32)])
    nearest = (distance <= distance.min(0) + 1e-3).astype(np.float32)
    vx, vy = nearest[3] - nearest[2], nearest[1] - nearest[0]
    length = np.hypot(vx, vy)
    slanted = length > 1e-3
    s, c = math.sin(math.radians(tilt)), math.cos(math.radians(tilt))
    points = np.zeros((index.size, 3), np.float32)
    points[:, 0] = np.where(slanted, vx / np.maximum(length, 1e-6) * s, 0)
    points[:, 1] = np.where(slanted, vy / np.maximum(length, 1e-6) * s, 0)
    points[:, 2] = np.where(slanted, c, 1.0)
    normals.reshape(-1, 3)[index] = points
    return normals


def facets(normals: np.ndarray, colour: Any, shadow: float = RIDGE_SHADOW) -> np.ndarray:
    """RGB (…, 3, 0 to 255) of faces with ``normals`` under the guide light:
    turned to the viewer they keep ``colour`` (one, or one per face), towards
    the light they lighten to white, away from it they darken."""
    c = np.asarray(colour, np.float32)[..., :3] / 255
    k = (np.clip((normals * GUIDE).sum(-1), 0, 1) / GUIDE_TOP)[..., None]
    rgb = np.where(k >= 1, c + (1 - c) * np.minimum(1.0, (k - 1) * FACET_LIGHT),
                   c * (shadow + (1 - shadow) * k))  # fmt: skip
    return rgb * 255


class LightEdge:
    """Points the panel light glints on (``look.light``): where they are on
    the panel, which way they face and how much of each is there.

    ``normals`` (h, w, 3): x to the right, y down, z towards the viewer
    (``(0, 0, 1)`` faces the viewer: it shines least); ``coverage`` (h, w),
    0 to 1, with the top left at (``x``, ``y``) on the panel; ``strength``
    scales its gloss. :meth:`ridge` makes the lines of a mask ridges, as
    frames are.

    Register an edge on every frame it shows, on the render thread
    (``Renderer.add_light_edge``; plugins: ``context.light_edge``), and keep
    the object while nothing about it changes: its gloss is worked out
    again only when the light has moved.
    """

    def __init__(self, normals: Any, coverage: Any, x: int = 0, y: int = 0,
                 strength: float = 1.0) -> None:  # fmt: skip
        coverage = np.asarray(coverage, np.float32)
        if coverage.max(initial=0) > 1.0:  # given as 0 to 255
            coverage = coverage / 255
        ys, xs = np.nonzero(coverage > 0.02)
        n = np.asarray(normals, np.float32)[ys, xs]
        factor = np.where(n[:, 2] > 0.995, TOP_GLOSS, 1.0) * coverage[ys, xs]
        self.strength = strength
        self._place(xs + int(x), ys + int(y), n, factor, overlap=False)

    @classmethod
    def ridge(cls, mask: Any, x: int = 0, y: int = 0, strength: float = 1.0) -> LightEdge:
        """The lines of ``mask`` (coverage, 0 to 1 or 0 to 255) as ridges."""
        coverage = np.asarray(mask, np.float32)
        return cls(ridge_normals(coverage > 0), coverage, x, y, strength)

    @classmethod
    def joined(cls, edges: list[LightEdge]) -> LightEdge:
        """Several edges as one, each point with its edge's strength: a sweep
        is worked out over all of them at once."""
        edge = cls.__new__(cls)
        edge.strength = 1.0
        parts = [e for e in edges if not e.empty]
        if not parts:
            nothing = np.zeros(0, np.intp)
            edge._place(nothing, nothing, np.zeros((0, 3), np.float32), nothing, overlap=False)
            return edge
        n = np.concatenate([np.stack(e._points[2:5], -1) for e in parts])
        factor = np.concatenate([e._points[5] * np.float32(e.strength) for e in parts])
        gx = np.concatenate([e._x for e in parts])
        gy = np.concatenate([e._y for e in parts])
        place = gy.astype(np.int64) * 1_000_003 + gx
        edge._place(gx, gy, n, factor, overlap=np.unique(place).size < place.size)
        return edge

    def _place(self, gx: np.ndarray, gy: np.ndarray, n: np.ndarray, factor: np.ndarray,
               overlap: bool) -> None:  # fmt: skip
        """Keep the points tile by tile, so a tile is a range of them, with
        each tile's box: only as large as its points and the halo's room (a
        frame's rail fills a strip of its 96 x 96, and every pixel costs)."""
        self.empty = gx.size == 0
        self._overlap = overlap  # points of joined edges may lie on one another
        self._kept: dict[str, tuple[Any, Any]] = {}
        if self.empty:
            return
        key = (gy // TILE) * 1_000_000 + gx // TILE
        order = np.argsort(key, kind="stable")
        gx, gy, key, n, factor = gx[order], gy[order], key[order], n[order], factor[order]
        columns = (gx + 0.5, gy + 0.5, n[:, 0], n[:, 1], n[:, 2], factor)
        self._points = tuple(np.ascontiguousarray(a, np.float32) for a in columns)
        self._x, self._y = gx.astype(np.intp), gy.astype(np.intp)
        self._all = np.arange(gx.size)
        starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        self._counts = np.diff(np.r_[starts, key.size])
        self._rows, self._columns = gy[starts] // TILE, gx[starts] // TILE
        self._boxes = np.stack([
            np.minimum.reduceat(self._x, starts) - MARGIN,
            np.minimum.reduceat(self._y, starts) - MARGIN,
            np.maximum.reduceat(self._x, starts) + 1 + MARGIN,
            np.maximum.reduceat(self._y, starts) + 1 + MARGIN,
        ], -1)  # fmt: skip

    def _gloss(self, lights: list[Light], reach: float, index: np.ndarray) -> np.ndarray:
        """The gloss of the points ``index``, (k, 3) RGB 0 to 1: Blinn-Phong with
        the viewer straight in front and the points in the panel's plane, so
        the half vector reduces to sums of 1-D arrays."""
        if index is self._all:
            x, y, nx, ny, nz, factor = self._points
        else:
            x, y, nx, ny, nz, factor = (a[index] for a in self._points)
        total = np.zeros((x.shape[0], 3), np.float32)
        for position, colour, strength, kind in lights:
            if strength <= 0.01:
                continue
            lx = np.float32(position[0]) - x
            ly = np.float32(position[1]) - y
            lz = np.float32(position[2])
            distance = np.sqrt(lx * lx + ly * ly + lz * lz)
            half = (nx * lx + ny * ly + nz * lz) / distance + nz
            half /= np.sqrt(np.float32(2.0) * (np.float32(1.0) + lz / distance))
            np.clip(half, 0, 1, out=half)
            if kind == "sweep":  # near: it fades with distance, softly to nothing at the cut
                window = np.clip(1.0 - (distance / np.float32(reach * SWEEP_CUT)) ** 2, 0, 1) ** 2
                strength = strength / (1.0 + (distance / np.float32(reach)) ** 2) * 2.0 * window
            # half ** 70 and half ** 9 by multiplying: pow is many times slower
            h2 = half * half
            h4 = h2 * h2
            h8 = h4 * h4
            h64 = h8 * h8
            h64 *= h64
            h64 *= h64
            shine = (h64 * h4 * h2 + SOFT_SHARE * h8 * half) * factor * strength * self.strength
            total += shine[:, None] * np.asarray(colour, np.float32)
        return total

    def _near(self, lights: list[Light], reach: float) -> np.ndarray:
        """Which tiles ``lights`` reach: a sweep fades out with distance, the sun
        and the night light reach everywhere."""
        if not any(kind == "sweep" for *_rest, kind in lights):
            return np.ones(len(self._counts), bool)
        far, boxes = reach * SWEEP_CUT, self._boxes
        near = np.zeros(len(self._counts), bool)
        for position, _colour, strength, _kind in lights:
            if strength > 0.01:
                dx = np.maximum(np.maximum(boxes[:, 0] - position[0], 0), position[0] - boxes[:, 2])
                dy = np.maximum(np.maximum(boxes[:, 1] - position[1], 0), position[1] - boxes[:, 3])
                near |= dx * dx + dy * dy < far * far
        return near

    def _shining(self, lights: list[Light], reach: float):
        """The gloss of the points in the tiles ``lights`` reach (uint16, k x 3,
        0 to 255), those points' indices, and per tile reached: its number and
        where its points begin among them."""
        reached = self._near(lights, reach)
        if reached.all():
            near, index = np.arange(reached.size), self._all
        else:
            near = np.flatnonzero(reached)
            index = np.flatnonzero(np.repeat(reached, self._counts))
            if index.size == 0:
                return None
        values = np.minimum(self._gloss(lights, reach, index) * 255 + 0.5, 255).astype(np.uint16)
        firsts = np.r_[0, np.cumsum(self._counts[near])[:-1]]
        return values, index, near, firsts

    def strips(
        self, lights: list[Light], reach: float, halo: float = HALO
    ) -> list[tuple[tuple[int, int, int, int], Image.Image]]:
        """Where ``lights`` make it shine: (box on the panel, RGB light). For each
        run of neighbouring tiles in a row: its points whose gloss shows, and
        the soft halo around those that shine clearly (``halo``: how much of
        their gloss), each in a box only as large as it needs."""
        if self.empty or not lights:
            return []
        found = self._shining(lights, reach)
        if found is None:
            return []
        values, index, near, firsts = found
        peaks = _peaks(values, firsts)
        shown = np.flatnonzero(peaks >= SHOWS)
        if shown.size == 0:
            return []
        tiles = near[shown]
        rows, columns = self._rows[tiles], self._columns[tiles]
        apart = (rows[1:] != rows[:-1]) | (columns[1:] != columns[:-1] + 1)
        runs = np.flatnonzero(np.r_[True, apart])
        out = []
        for a, b in zip(runs.tolist(), np.r_[runs[1:], tiles.size].tolist(), strict=True):
            first = int(firsts[shown[a]])
            last = int(firsts[shown[b - 1]] + self._counts[tiles[b - 1]])
            part, gloss = index[first:last], values[first:last]
            brightest = np.maximum(np.maximum(gloss[:, 0], gloss[:, 1]), gloss[:, 2])
            keep = brightest >= SHOWS
            out.append(self._light(part[keep], gloss[keep]))
            if halo > 0 and peaks[shown[a:b]].max() >= HALO_FROM:  # a faint one's would not show
                clear = brightest >= HALO_FROM
                out.append(self._halo(part[clear], gloss[clear], halo))
        return out

    def _light(self, index: np.ndarray, values: np.ndarray):
        """The points ``index`` with their gloss ``values``: (box, RGB light)."""
        x, y = self._x[index], self._y[index]
        x0, y0 = int(x.min()), int(y.min())
        w, h = int(x.max()) + 1 - x0, int(y.max()) + 1 - y0
        px, py = x - x0, y - y0
        if self._overlap:  # where points lie on one another, their light adds up
            light = np.zeros((h, w, 3), np.uint16)
            np.add.at(light, (py, px), values)
            light = np.minimum(light, 255).astype(np.uint8)
        else:
            light = np.zeros((h, w, 3), np.uint8)
            light[py, px] = values
        return (x0, y0, x0 + w, y0 + h), Image.fromarray(light, "RGB")

    def _halo(self, index: np.ndarray, values: np.ndarray, share: float):
        """The soft halo around the points ``index`` (their gloss: ``values``):
        dimmed to ``share`` and blurred, worked out at a third of the size."""
        x, y = self._x[index], self._y[index]
        x0, y0 = int(x.min()) - MARGIN, int(y.min()) - MARGIN
        w, h = int(x.max()) + 1 + MARGIN - x0, int(y.max()) + 1 + MARGIN - y0
        sw, sh = -(-w // 3), -(-h // 3)
        cells = (((y - y0) // 3) * sw + (x - x0) // 3)[:, None] * 3 + _RGB
        sums = np.bincount(cells.ravel(), values.ravel(), sh * sw * 3)
        small = np.minimum(sums * (share / 9) + 0.5, 255).astype(np.uint8).reshape(sh, sw, 3)
        glow = Image.fromarray(small, "RGB").filter(ImageFilter.GaussianBlur(2.2))
        glow = glow.resize((w, h), Image.Resampling.BILINEAR, box=(0, 0, w / 3, h / 3))
        return (x0, y0, x0 + w, y0 + h), glow

    def gloss(
        self, lights: list[Light], kind: str, reach: float, halo: float = HALO
    ) -> list[tuple[tuple[int, int, int, int], Image.Image]]:
        """:meth:`strips`, kept per ``kind`` until the lights move (by 2 px) or
        change: the sun's gloss is worked out a few times an hour."""
        if self.empty or not lights:
            return []
        key = (_light_key(lights), halo, reach)
        kept = self._kept.get(kind)
        if kept is not None and kept[0] == key:
            return kept[1]
        out = self.strips(lights, reach, halo)
        self._kept[kind] = (key, out)
        return out


_RGB = np.arange(3)


def _peaks(values: np.ndarray, firsts: np.ndarray) -> np.ndarray:
    """The brightest channel of each tile's points (the points tile by tile)."""
    brightest = np.maximum(np.maximum(values[:, 0], values[:, 1]), values[:, 2])
    return np.maximum.reduceat(brightest, firsts)


def _light_key(lights: list[Light]) -> tuple[Any, ...]:
    """What the gloss of ``lights`` depends on: places to 2 px, colours and
    strengths to a hundredth (the sun moves a step every minute or two)."""
    return tuple(
        (
            tuple(int(v) for v in np.round(position / 2)),
            tuple(round(c, 2) for c in colour),
            round(strength, 2),
        )
        for position, colour, strength, _kind in lights
    )


def _light_piece(strips: list[tuple[tuple[int, int, int, int], Image.Image]]) -> Piece | None:
    """Strips of light as one piece that adds its light (alpha: its brightest channel)."""
    if not strips:
        return None
    x0, y0 = min(box[0] for box, _ in strips), min(box[1] for box, _ in strips)
    x1, y1 = max(box[2] for box, _ in strips), max(box[3] for box, _ in strips)
    light = Image.new("RGB", (x1 - x0, y1 - y0))
    for (bx0, by0, bx1, by1), image in strips:
        region = (bx0 - x0, by0 - y0, bx1 - x0, by1 - y0)
        light.paste(ImageChops.add(light.crop(region), image), region[:2])
    r, g, b = light.split()
    light.putalpha(ImageChops.lighter(ImageChops.lighter(r, g), b))
    return Piece(light, x0, y0, add=True)


# -- sweeps ----------------------------------------------------------------------------


class Sweeps:
    """Sweeps of light across the panel and the moments that start them.

    Kept with the panel's motion (``Motion.sweeps``), so they go on from theme
    to theme: a new theme does not count as a start, and its first full hour
    is the next one. Editor previews have no sweeps.
    """

    def __init__(self) -> None:
        self.runs: list[tuple[float, float]] = []  # (start, seconds)
        self.started = False
        self.hour: tuple[int, int, int, int] | None = None
        self.title: str | None = None

    def start(self, now: float, seconds: float) -> None:
        self.runs = [(t, s) for t, s in self.runs if now - t < s]
        self.runs.append((now, seconds))

    def watch(self, snapshot: Any, now: float, settings: dict[str, Any]) -> None:
        """Start one where the frame at ``now`` brings a moment the theme asks for."""
        seconds = settings["sweep_seconds"]
        if not self.started:
            self.started = True
            if settings["on_start"]:
                self.start(now, seconds)
        stamp = snapshot.now  # the frame's own wall-clock time
        hour = (stamp.year, stamp.month, stamp.day, stamp.hour)
        if self.hour is not None and hour != self.hour and settings["on_hour"]:
            self.start(now, seconds)
        self.hour = hour
        title = snapshot.value("media.title")
        if isinstance(title, str) and title.strip():
            if self.title is not None and title != self.title and settings["on_track"]:
                self.start(now, seconds)
            self.title = title

    def active(self, now: float) -> list[float]:
        """How far each sweep on the panel at ``now`` has come (0 to 1)."""
        return [(now - t) / s for t, s in self.runs if 0 <= (now - t) / s <= 1]


# -- the panel light -------------------------------------------------------------------


class PanelLight:
    """The light of one renderer's frames (``look.light``)."""

    def __init__(self, renderer: Any, settings: dict[str, Any]) -> None:
        self.renderer = weakref.proxy(renderer)  # (it holds the light: no cycle)
        self.settings = settings
        theme = renderer.theme
        self.size = w, h = theme.width, theme.height
        self.unit = min(w, h) / 480
        self.reach = SWEEP_REACH * max(w, h) / 1920
        tint = renderer.color(settings["color"]) if settings["color"] else None
        accent = tint[:3] if tint else role_color(theme, "info")
        self.sweep_colour = tuple(a / 255 + (1 - a / 255) * 0.55 for a in accent)
        self._pieces: dict[int, tuple[LightEdge, list[Light], Any, Piece | None]] = {}
        self._standing_at: tuple[int, list[Light]] | None = None
        self._joined: tuple[tuple[int, ...], list[LightEdge], LightEdge] | None = None

    @property
    def sweeps(self) -> Sweeps:
        motion = self.renderer.motion
        if getattr(motion, "sweeps", None) is None:
            motion.sweeps = Sweeps()
        return motion.sweeps

    def fixed(self, wall: float) -> list[Light]:
        """The lights that stand (the sun, or the night light) at Unix time ``wall``."""
        w, h = self.size
        place = self.renderer.place if self.settings["sun"] else None
        azimuth, elevation = sun_position(wall, *place) if place else (180.0, 35.0)
        lights: list[Light] = []
        strength = self.settings["strength"]
        if elevation > -6:  # day and dusk: east on the left, west on the right
            high = max(0.0, elevation)
            steep = math.sin(math.radians(min(high, 60)))
            x = w * (0.5 + (azimuth - 180) / 180 * 0.62)
            z = (30 + 80 * steep) * self.unit  # low: a 45° facet reflects only grazing light
            warm = min(1.0, high / 30)
            colour = (1.0, 0.76 + 0.21 * warm, 0.52 + 0.40 * warm)
            shine = (0.45 + 0.35 * steep) * min(1.0, (elevation + 6) / 6) * strength
            lights.append((np.array([x, -0.6 * h, z], np.float32), colour, shine, "fixed"))
        if elevation < 0:  # night: cool and weak, opposite the sun, where the sky is lightest
            x = w * (0.5 + (((azimuth + 180) % 360) - 180) / 180 * 0.62)
            shine = 0.26 * min(1.0, -elevation / 6) * strength
            lights.append((np.array([x, -0.6 * h, 50 * self.unit], np.float32),
                           (0.70, 0.83, 1.0), shine, "fixed"))  # fmt: skip
        return lights

    def running(self, now: float) -> list[Light]:
        """The sweeps on the panel at ``now``: a light running from left to right."""
        w, h = self.size
        lights: list[Light] = []
        for u in self.sweeps.active(now) if not self.renderer.preview else ():
            x = -0.25 * w + 1.5 * w * smooth(u)
            shine = 1.7 * math.sin(math.pi * u) ** 0.7 * self.settings["strength"]
            lights.append((np.array([x, -0.3 * h, 36 * self.unit], np.float32),
                           self.sweep_colour, shine, "sweep"))  # fmt: skip
        return lights

    def frame(self, pieces: list[tuple[str, Piece]], snapshot: Any, now: float) -> None:
        """Before the frame is composed: its moments, and the sun's gloss on its
        edges as pieces of light. They stay the same objects until the sun
        moves a step, and each lies right on the piece that drew its edge,
        under what comes later (SPUR II bakes it into the background): where
        a bar or a graph moves over it, the composition starts from beneath
        the moving piece and does not lay the light again."""
        renderer = self.renderer
        if not renderer.preview:
            self.sweeps.watch(snapshot, now, self.settings)
        edges = renderer.light_edges
        lights = self._standing(snapshot.now.timestamp()) if edges else []
        on: dict[str, list[tuple[str, Piece]]] = {}
        for edge in edges:
            piece = self._gloss_piece(edge, lights)
            if piece is not None:
                owner = renderer.edge_owners.get(id(edge), "")
                on.setdefault(owner, []).append((f"\0light{id(edge)}", piece))
        for gone in set(self._pieces) - {id(edge) for edge in edges}:  # no longer on the panel
            del self._pieces[gone]
        if not on:
            return
        laid = []
        for wid, piece in pieces:
            laid.append((wid, piece))
            laid.extend(on.pop(wid, ()))
        laid.extend(on.pop("", ()))  # edges not drawn by a piece: on top
        pieces[:] = laid

    def _standing(self, wall: float) -> list[Light]:
        """:meth:`fixed`, worked out once a minute (the sun moves about a pixel)."""
        minute = int(wall // 60)
        if self._standing_at is None or self._standing_at[0] != minute:
            self._standing_at = (minute, self.fixed(minute * 60.0))
        return self._standing_at[1]

    def _gloss_piece(self, edge: LightEdge, lights: list[Light]) -> Piece | None:
        """The standing light's gloss on ``edge``: one piece of light, the same
        object while the gloss stays the same."""
        kept = self._pieces.get(id(edge))
        if kept is not None and kept[0] is not edge:
            kept = None  # another edge where a gone one was: ids come again
        if kept is not None and kept[1] is lights:
            return kept[3]
        strips = edge.gloss(lights, "fixed", self.reach)
        piece = kept[3] if kept is not None and kept[2] is strips else _light_piece(strips)
        self._pieces[id(edge)] = (edge, lights, strips, piece)
        return piece

    def sweep(self, frame: Image.Image, now: float) -> Image.Image:
        """After the frame is composed: the sweeps on it, in place (``frame`` is
        RGB and the frame's own), only in the strips where they shine."""
        edges = self.renderer.light_edges
        lights = self.running(now) if edges else []  # (a sweep only lights edges)
        if not lights:
            return frame
        self.renderer.moving = True  # the PNG path draws at full rate meanwhile
        key = tuple(id(edge) for edge in edges)
        if self._joined is None or self._joined[0] != key:  # the edges kept: their ids stay
            self._joined = (key, list(edges), LightEdge.joined(edges))
        for box, light in self._joined[2].strips(lights, self.reach):
            self.renderer._add_light(frame, light, box[0], box[1])
        return frame

    def transition_ended(self, kind: Any, now: float) -> None:
        if self.settings["on_transition"] and getattr(kind, "name", "") in TRANSITIONS:
            self.sweeps.start(now, self.settings["sweep_seconds"])


# -- ridges ----------------------------------------------------------------------------


def outline(w: int, h: int, radius: int, width: int) -> np.ndarray:
    """A rect's outline as coverage (h, w; 0 to 1), as the flat rect draws it."""
    s = 3 if radius > 0 else 1  # round corners: drawn larger and reduced
    mask = Image.new("L", (max(1, w * s), max(1, h * s)), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=radius * s,
                                           outline=255, width=width * s)  # fmt: skip
    return np.asarray(mask.reduce(s) if s > 1 else mask, np.float32) / 255


def ridges(renderer: Any) -> dict[str, Finish]:
    """The rects with a ``ridge`` name, by id: rects of the same name are one
    ridge (render.finish attaches them; nothing else is a ridge)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for widget in renderer.widgets:
        if widget["type"] == "rect" and widget.get("ridge"):
            groups.setdefault(widget["ridge"], []).append(widget)
    found: dict[str, Finish] = {}
    for members in groups.values():
        ridge = Ridge(members)
        found.update(dict.fromkeys((w["id"] for w in members), ridge))
    return found


class Ridge(Finish):
    """Rects that share a ``ridge`` name as one ridge, as SPUR II's frames:
    every point of their union tilts towards its nearest edge (mitred where
    bands meet), so two facets catch the guide light or lie in its shadow.
    The lit facets add a little light to the ridge itself, and the ridge is
    a light edge the panel light glints on. An outlined rect gives its
    outline, a filled one its fill. The first rect of the name draws the
    whole ridge; the others only keep their place for the editor."""

    name = "ridge"
    types = ("rect",)

    def __init__(self, members: list[dict[str, Any]]) -> None:
        self.members = members
        self.first = members[0]["id"]
        self._built: tuple[Piece, LightEdge] | None = None
        self._marks: dict[str, Piece] = {}

    def draw(self, renderer, widget, snapshot, now):
        if widget["id"] != self.first:
            return self._mark(widget)
        piece, edge = self._ridge(renderer)
        renderer.add_light_edge(edge)
        return piece

    def prepare(self, renderer, widget):
        if widget["id"] == self.first:
            self._ridge(renderer)

    def _mark(self, widget: dict[str, Any]) -> Piece:
        mark = self._marks.get(widget["id"])
        if mark is None:
            box = [widget["x"], widget["y"], widget["w"], widget["h"]]
            mark = Piece(Image.new("RGBA", (1, 1)), widget["x"], widget["y"], box)
            self._marks[widget["id"]] = mark
        return mark

    def _ridge(self, renderer: Any) -> tuple[Piece, LightEdge]:
        if self._built is None:
            piece, edge = self._build(renderer)
            self._built = renderer._effects(self.members[0], piece), edge
        return self._built

    def _build(self, renderer: Any) -> tuple[Piece, LightEdge]:
        x0 = min(m["x"] for m in self.members)
        y0 = min(m["y"] for m in self.members)
        x1 = max(m["x"] + m["w"] for m in self.members)
        y1 = max(m["y"] + m["h"] for m in self.members)
        base = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))  # fills under outlines
        ridge = np.zeros((y1 - y0, x1 - x0, 4), np.float32)  # straight colour and alpha
        for m in self.members:  # a later rect lies over an earlier one, as drawn
            ox, oy, w, h = m["x"] - x0, m["y"] - y0, m["w"], m["h"]
            if m["outline"]:
                if m["color"]:
                    base.alpha_composite(renderer._rect_piece({**m, "outline": None}).layer,
                                         (ox, oy))  # fmt: skip
                colour = renderer.color(m["outline"])
                layer = np.zeros((h, w, 4), np.float32)
                layer[..., :3] = colour[:3]
                layer[..., 3] = outline(w, h, min(m["radius"], w // 2, h // 2),
                                        m["outline_width"]) * colour[3]  # fmt: skip
            else:
                layer = np.asarray(renderer._rect_piece(m).layer, np.float32)
            region = ridge[oy : oy + h, ox : ox + w]
            on = layer[..., 3] > 0
            region[on] = layer[on]
        alpha = ridge[..., 3] / 255
        normals = ridge_normals(alpha > 0)
        rgb = facets(normals, ridge)
        # SPUR II's frames: light from the lit facets, blurred, a share of their
        # colour, and only on the ridge itself (beside it stays dark)
        lit = np.clip((normals * GUIDE).sum(-1), 0, 1) * alpha
        unit = min(renderer.theme.width, renderer.theme.height) / 480
        radius = max(2.0, RIDGE_RADIUS * unit)
        pad = math.ceil(2 * radius)
        soft = Image.new("L", (lit.shape[1] + 2 * pad, lit.shape[0] + 2 * pad), 0)
        soft.paste(Image.fromarray((lit * 255).astype(np.uint8), "L"), (pad, pad))
        soft = soft.filter(ImageFilter.GaussianBlur(radius)).crop(
            (pad, pad, pad + lit.shape[1], pad + lit.shape[0])
        )
        glow = np.asarray(soft, np.float32)[..., None] / 255 * ridge[..., :3] * RIDGE_GLOW
        glow = np.clip(glow, 0, 255).astype(np.uint8)  # whole levels, as SPUR II's
        out = np.concatenate([rgb, ridge[..., 3:4]], -1) + 0.5
        base.alpha_composite(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA"))
        light = None
        if glow.any():  # added over the ridge (light adds up, also where a colour is full)
            rgba = np.concatenate([glow, glow.max(-1, keepdims=True)], -1)
            light = Piece(Image.fromarray(rgba, "RGBA"), x0, y0)
        first = self.members[0]
        box = [first["x"], first["y"], first["w"], first["h"]]
        return Piece(base, x0, y0, box, light=light), LightEdge(normals, alpha, x0, y0)


# -- the grid --------------------------------------------------------------------------


def grid_image(size: tuple[int, int], spacing: int, major: int, minor_colour: tuple[int, ...],
               major_colour: tuple[int, ...]) -> Image.Image:  # fmt: skip
    """Lines every ``spacing`` px from the top left, every ``major``-th one
    (0: none) in ``major_colour``, on a clear picture of ``size``."""
    w, h = size
    out = np.zeros((h, w, 4), np.uint8)
    spacing = max(1, spacing)
    every = spacing * major

    def lines(length: int, strong: bool) -> np.ndarray:
        at = np.arange(0, length, spacing)
        return at[(at % every == 0) == strong] if major > 0 else (at[:0] if strong else at)

    for colour, strong in ((minor_colour, False), (major_colour, True)):
        out[:, lines(w, strong)] = colour
    for colour, strong in ((minor_colour, False), (major_colour, True)):  # rows over columns
        out[lines(h, strong), :] = colour
    return Image.fromarray(out, "RGBA")


def _named(renderer: Any, names: tuple[str, ...]) -> tuple[int, ...] | None:
    palette = renderer.theme.palette
    name = next((n for n in names if n in palette), None)
    return renderer.color("@" + name) if name else None


def grid_colours(
    renderer: Any, settings: dict[str, Any]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The grid's colours: as set, else the palette's ``grid`` / ``grid_major``,
    else a breath away from the background (the strong lines twice as far)."""
    ground = renderer.color(renderer.theme.background_color) or (0, 0, 0, 255)
    text = role_color(renderer.theme, "text")
    minor = renderer.color(settings["color"]) if settings["color"] else None
    minor = minor or _named(renderer, ("grid", "grid_min"))
    if minor is None:
        minor = (*(round(g + (t - g) * 0.05) for g, t in zip(ground[:3], text, strict=True)), 255)
    strong = renderer.color(settings["major_color"]) if settings["major_color"] else None
    strong = strong or _named(renderer, ("grid_major", "grid_maj"))
    if strong is None:
        twice = (min(255, max(0, 2 * m - g)) for m, g in zip(minor[:3], ground[:3], strict=True))
        strong = (*twice, minor[3])
    alpha = settings["alpha"]
    return (*minor[:3], round(minor[3] * alpha)), (*strong[:3], round(strong[3] * alpha))


# -- the look of a renderer ------------------------------------------------------------


class Look:
    """What a theme's ``look`` adds to one renderer's frames."""

    def __init__(self, renderer: Any) -> None:
        self.renderer = weakref.proxy(renderer)  # (it holds the look: no cycle)
        theme = renderer.theme
        settings = theme.look
        self.grid = settings.get("grid")
        light = settings.get("light")
        self.light = PanelLight(renderer, light) if isinstance(light, dict) else None
        self._grid: Piece | None = None
        # Backdrops: full-panel rects at the bottom of the list, which the grid lies on.
        self._backdrops = set()
        for widget in renderer.widgets:
            covers = (
                widget["type"] == "rect"
                and widget["x"] <= 0
                and widget["y"] <= 0
                and (
                    widget["x"] + widget["w"] >= theme.width
                    and widget["y"] + widget["h"] >= theme.height
                )
            )
            if not covers:
                break
            self._backdrops.add(widget["id"])

    def _grid_piece(self) -> Piece:
        if self._grid is None:
            grid = self.grid
            minor, major = grid_colours(self.renderer, grid)
            size = (self.renderer.theme.width, self.renderer.theme.height)
            self._grid = Piece(grid_image(size, grid["spacing"], grid["major"], minor, major), 0, 0)
        return self._grid

    def prepare(self) -> None:
        if self.grid is not None:
            self._grid_piece()

    def compose(self, pieces: list[tuple[str, Piece]], snapshot: Any, now: float) -> None:
        """Before compositing: the grid over the backdrops, the light on top."""
        if self.grid is not None:
            at = 0
            while at < len(pieces) and (
                pieces[at][0] in self._backdrops or pieces[at][0] == "\0screen"
            ):
                at += 1
            pieces.insert(at, ("\0grid", self._grid_piece()))
        if self.light is not None:
            self.light.frame(pieces, snapshot, now)

    def finish(self, frame: Image.Image, now: float) -> Image.Image:
        """After compositing: the sweeps."""
        return frame if self.light is None else self.light.sweep(frame, now)

    def transition_ended(self, kind: Any, now: float) -> None:
        if self.light is not None:
            self.light.transition_ended(kind, now)

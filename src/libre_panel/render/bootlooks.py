"""More looks for the panel's own screens (render/bootscreen.py has what they share).

Each is drawn for its room, whatever its shape: a bar, a broad panel, a square, a round one, or
a bar on end. Their clips move only in brightness at fixed places, and each of their slow
changes comes round a whole number of times a loop, so the loop has no seam.

- **Aurora**: curtains of light over a dark horizon, green at their foot, violet up their rays;
  in the clip the rays shimmer, each on its own.
- **Pulse**: a heartbeat across the panel on a monitor's grid (more under each other on a bar
  on end); in the clip a soft light sweeps along it and leaves a trail.
- **Circuit**: traces that run in from both sides to a chip with the app's mark; in the clip
  light runs along them into the chip.
- **Night**: a sky of stars over a glow at the horizon, a crescent moon; in the clip the stars
  twinkle.
- **Levels**: an equaliser's bars from a line in the middle, teal to violet; in the clip they
  rise and fall.
- **Dark**: black, with the app's mark faintly in the middle; in the clip it breathes. For a
  panel that should stay dark.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from libre_panel.render import moon
from libre_panel.render.bootscreen import (
    BG,
    BG2,
    Canvas,
    Design,
    Room,
    blur,
    coverage,
    glow,
    mark_into,
    paint,
    shine,
    tinted,
    vertical,
)

TAU = 2 * math.pi


def _rng(where: Room, salt: int) -> np.random.Generator:
    """The same chance for the same room: a look is drawn alike every time."""
    return np.random.default_rng(where.w * 100003 + where.h * 101 + salt)


def _grid(where: Room) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0 : where.h, 0 : where.w].astype(np.float32)
    return xs, ys


def _long(where: Room) -> tuple[float, float, float, float]:
    """The room along its long side and across it: (start, end) along, (start, end) across, in
    picture coordinates of the long axis (x on a wide panel, y on a tall one)."""
    if where.w >= where.h:
        return where.x0, where.x1, where.y0, where.y1
    return where.y0, where.y1, where.x0, where.x1


# -- Aurora ---------------------------------------------------------------------------------------


AURORA_FOOT = (60, 255, 170)  # green where the curtain hangs
AURORA_TOP = (150, 80, 255)  # violet up its rays
AURORA_SKY = ((2, 4, 12), (6, 14, 24))


class Aurora:
    """Curtains of light (see the module's text). Drawn across the picture's width with the
    sky's height above them, whatever the panel's shape."""

    RAYS = 9  # waves of brightness along a curtain, each shimmering on its own

    def __init__(self, where: Room) -> None:
        self.where = where
        w, h = where.w, where.h
        xs, ys = _grid(where)
        rng = _rng(where, 1)
        self.sky = vertical(h, w, *AURORA_SKY)
        self._stars(rng)
        self.curtains: list[np.ndarray] = []  # each: its light, rows x columns x 3
        self.rays: list[np.ndarray] = []  # each: (rays, columns) the waves along it
        u = xs / w
        for level, height, gain in ((0.62, 0.55, 1.0), (0.70, 0.42, 0.75), (0.55, 0.65, 0.45)):
            phase = rng.uniform(0, TAU, 3)
            foot = h * (level + 0.05 * np.sin(TAU * (u * 0.9) + phase[0])
                        + 0.03 * np.sin(TAU * (u * 2.3) + phase[1])
                        + 0.015 * np.sin(TAU * (u * 5.1) + phase[2]))  # fmt: skip
            above = np.clip(foot - ys, 0, None) / (h * height)  # 0 at its foot, up its rays
            below = np.clip(ys - foot, 0, None) / (h * 0.025)
            light = np.exp(-above * 2.2) * np.exp(-(below**2))
            light *= np.clip(1.0 - above, 0, 1) ** 0.6  # the rays end
            mix = np.clip(above * 1.4, 0, 1)[..., None]  # green at the foot, violet above
            colour = (1 - mix) * np.asarray(AURORA_FOOT, np.float32) + mix * np.asarray(
                AURORA_TOP, np.float32)  # fmt: skip
            self.curtains.append(light[..., None] * colour * (0.55 * gain))
            # the rays: soft streaks across, a few octaves of waves along the curtain
            streaks = np.zeros((self.RAYS, w), np.float32)
            x = np.arange(w, dtype=np.float32) / w
            for k in range(self.RAYS):
                f = rng.uniform(6, 30) * (1 + k / 3)
                streaks[k] = 0.5 + 0.5 * np.sin(TAU * f * x + rng.uniform(0, TAU))
            self.rays.append(streaks / self.RAYS)
        self.beats = rng.integers(1, 3, (3, self.RAYS))  # how often each wave shimmers a loop
        self.offsets = rng.uniform(0, TAU, (3, self.RAYS))

    def _stars(self, rng: np.random.Generator) -> None:
        w, h = self.where.w, self.where.h
        n = int(w * h / 2600)
        sx, sy = rng.uniform(0, w, n), rng.uniform(0, h * 0.75, n)
        level = rng.uniform(0.15, 0.6, n) ** 2
        star = np.zeros((h, w), np.float32)
        np.add.at(star, (sy.astype(int), sx.astype(int)), level * 255)
        self.sky += blur(star, 0.6 * max(1.0, self.where.unit))[..., None] * 0.9

    def _drawn(self, phase: float | None) -> Canvas:
        out = self.sky.copy()
        for j, (curtain, rays) in enumerate(zip(self.curtains, self.rays, strict=True)):
            if phase is None:
                weights = np.ones(self.RAYS, np.float32)
            else:
                weights = 1 + 0.6 * np.sin(TAU * self.beats[j] * phase + self.offsets[j])
            streak = 0.45 + 0.9 * (weights[:, None] * rays).sum(axis=0)  # along the curtain
            out += curtain * streak[None, :, None]
        return out

    def still(self) -> Canvas:
        return self._drawn(None)

    def frame(self, phase: float) -> Canvas:
        return self._drawn(phase)


# -- Pulse ----------------------------------------------------------------------------------------


PULSE = (70, 255, 160)
PULSE_GRID = (14, 30, 26)
PULSE_SKY = ((6, 12, 12), (3, 7, 8))


def _beat(t: np.ndarray) -> np.ndarray:
    """A heartbeat's trace over one beat (t 0 to 1), -1 to 1: flat, a small P, the sharp QRS,
    the round T."""
    p = 0.10 * np.exp(-(((t - 0.18) / 0.035) ** 2))
    q = -0.12 * np.exp(-(((t - 0.30) / 0.010) ** 2))
    r = 1.0 * np.exp(-(((t - 0.33) / 0.012) ** 2))
    s = -0.30 * np.exp(-(((t - 0.36) / 0.012) ** 2))
    tw = 0.22 * np.exp(-(((t - 0.58) / 0.06) ** 2))
    return p + q + r + s + tw


class Pulse:
    """A heartbeat across the panel (see the module's text)."""

    def __init__(self, where: Room) -> None:
        self.where = where
        w, h = where.w, where.h
        self.base = vertical(h, w, *PULSE_SKY)
        u = where.unit
        step = max(8, round(24 * u))  # the monitor's grid, every fifth line stronger

        def grid(draw: ImageDraw.ImageDraw, s: int) -> None:
            for k, x in enumerate(range(0, w, step)):
                draw.line([(x * s, 0), (x * s, h * s)], fill=255 if k % 5 == 0 else 110,
                          width=max(1, round(s * 0.8)))  # fmt: skip
            for k, y in enumerate(range(round(where.cy) % step, h, step)):
                draw.line([(0, y * s), (w * s, y * s)], fill=255 if k % 5 == 0 else 110,
                          width=max(1, round(s * 0.8)))  # fmt: skip

        paint(self.base, coverage((w, h), grid, 2), PULSE_GRID, alpha=0.8)
        # the traces across, as a monitor shows them: one through the middle of a broad panel,
        # more under each other on a tall one (a bar on end), each its own beats
        tall = where.rh > where.rw * 1.25  # (a square or round one is broad enough)
        rows = max(2, min(4, round(where.rh / (where.rw * 0.9)))) if tall else 1
        band = where.rh / rows
        beats = max(2 if rows == 1 else 1, round(where.rw / (band * 1.7)))
        n = int(where.rw * 2)
        xs = np.linspace(where.x0, where.x1, n)
        self.s = (xs - where.x0) / where.rw  # where along a trace each point is (0 to 1)
        lines = []
        for row in range(rows):
            middle = where.y0 + band * (row + 0.5)
            shift = 0.37 * row  # (the rows' beats not one over the other)
            t = (self.s * beats + shift) % 1.0
            lines.append(list(zip(xs, middle - band * 0.36 * _beat(t), strict=True)))
        width = max(2.0, 3.2 * u)

        def line(draw: ImageDraw.ImageDraw, s: int) -> None:
            for points in lines:
                draw.line([(x * s, y * s) for x, y in points], fill=255,
                          width=round(width * s), joint="curve")  # fmt: skip

        self.cover = coverage((w, h), line)
        # how far along its trace each lit pixel is (for the light that sweeps along them)
        place = Image.new("F", (w, h), 0.0)
        draw = ImageDraw.Draw(place)
        for points in lines:
            for (x0, y0), (x1, y1), s0 in zip(points, points[1:], self.s, strict=False):
                draw.line([(x0, y0), (x1, y1)], fill=float(s0), width=max(3, round(width + 4)))
        self.along = np.asarray(place, np.float32)
        self.width = width

    def _drawn(self, level: np.ndarray) -> Canvas:
        out = self.base.copy()
        light = self.cover * level
        u = self.where.unit
        glow(out, light, PULSE, ((6 * u, 1.2), (22 * u, 0.8)))
        np.maximum(out, tinted(light, PULSE), out=out)
        return out

    def still(self) -> Canvas:
        return self._drawn(np.float32(0.9))

    def frame(self, phase: float) -> Canvas:
        behind = (phase * 1.25 - self.along) % 1.25  # how far the sweep has gone past
        trail = np.exp(-behind / 0.22) * (behind < 1.0)
        head = np.exp(-((behind / 0.012) ** 2))
        return self._drawn(0.22 + 0.7 * trail + 0.3 * head)


# -- Circuit --------------------------------------------------------------------------------------


TRACE = (40, 200, 190)
BOARD = ((5, 14, 16), (2, 7, 9))
COPPER = (24, 58, 60)


class Circuit:
    """Traces from both sides into a chip with the app's mark (see the module's text)."""

    def __init__(self, where: Room) -> None:
        self.where = where
        w, h = where.w, where.h
        wide = w >= h
        u = where.unit
        self.base = vertical(h, w, *BOARD)
        a0, a1, b0, b1 = _long(where)
        ca, cb = (a0 + a1) / 2, (b0 + b1) / 2
        chip = min(b1 - b0, a1 - a0) * (0.42 if not where.round else 0.34)
        pitch = max(10.0 * min(1.0, u * 2), chip / 9)  # (finer on a very small picture)
        count = max(2, int(chip / pitch) - 1)
        pins = [cb - chip / 2 + chip * (k + 1) / (count + 1) for k in range(count)]
        rng = _rng(where, 3)
        paths: list[list[tuple[float, float]]] = []  # each in (along, across)
        for side in (-1, 1):
            edge = a0 if side < 0 else a1
            entry = ca + side * (chip / 2 + pitch * 1.2)  # where the traces turn into the chip
            spread = [b0 + (b1 - b0) * (k + 0.5) / len(pins) for k in range(len(pins))]
            for k, (pin, start) in enumerate(zip(pins, spread, strict=True)):
                dy = start - pin
                bend = entry + side * abs(dy)  # the diagonal ends at ``entry``
                begin = edge + side * -rng.uniform(0, (abs(edge - bend)) * 0.45)  # some end early
                if k % 3 == 1:
                    begin = edge  # some run from the edge
                path = [(begin, start), (bend, start), (entry, pin), (ca + side * chip / 2, pin)]
                paths.append(path)
        self.chip = (ca, cb, chip)

        def xy(p: tuple[float, float]) -> tuple[float, float]:
            return p if wide else (p[1], p[0])

        width = max(1.6, 2.4 * u)
        lengths = [sum(math.dist(p, q) for p, q in zip(path, path[1:], strict=False))
                   for path in paths]  # fmt: skip

        def traces(draw: ImageDraw.ImageDraw, s: int) -> None:
            for path in paths:
                draw.line([(xy(p)[0] * s, xy(p)[1] * s) for p in path], fill=255,
                          width=round(width * s), joint="curve")  # fmt: skip
                x, y = xy(path[0])
                r = width * 1.9
                draw.ellipse([(x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s], fill=255)

        self.cover = coverage((w, h), traces)
        paint(self.base, self.cover, COPPER)
        # how far along its trace each pixel is (0 at its start, 1 at the chip), and which trace
        place = Image.new("F", (w, h), -1.0)
        which = Image.new("F", (w, h), -1.0)
        dp, dw = ImageDraw.Draw(place), ImageDraw.Draw(which)
        for n, (path, length) in enumerate(zip(paths, lengths, strict=True)):
            done = 0.0
            for p, q in zip(path, path[1:], strict=False):
                pieces = max(1, int(math.dist(p, q) / 2))
                for i in range(pieces):
                    a = (p[0] + (q[0] - p[0]) * i / pieces, p[1] + (q[1] - p[1]) * i / pieces)
                    b = (p[0] + (q[0] - p[0]) * (i + 1) / pieces,
                         p[1] + (q[1] - p[1]) * (i + 1) / pieces)  # fmt: skip
                    s = (done + math.dist(p, q) * i / pieces) / length
                    line = [xy(a), xy(b)]
                    dp.line(line, fill=float(s), width=max(3, round(width * 3.2)))
                    dw.line(line, fill=float(n), width=max(3, round(width * 3.2)))
                done += math.dist(p, q)
            x, y = xy(path[0])
            r = width * 2.6
            dp.ellipse([x - r, y - r, x + r, y + r], fill=0.0)
            dw.ellipse([x - r, y - r, x + r, y + r], fill=float(n))
        self.along = np.asarray(place, np.float32)
        self.which = np.asarray(which, np.float32).astype(np.int32)
        self.offsets = rng.uniform(0, 1, len(paths)).astype(np.float32)
        self._chip_into(self.base, xy((ca, cb)), chip, u)

    def _chip_into(self, out: Canvas, centre: tuple[float, float], chip: float, u: float) -> None:
        cx, cy = centre
        w, h = self.where.w, self.where.h
        half = chip / 2
        radius = chip * 0.12

        def body(draw: ImageDraw.ImageDraw, s: int) -> None:
            draw.rounded_rectangle([(cx - half) * s, (cy - half) * s, (cx + half) * s,
                                    (cy + half) * s], radius=radius * s, fill=255)  # fmt: skip

        def rim(draw: ImageDraw.ImageDraw, s: int) -> None:
            draw.rounded_rectangle([(cx - half) * s, (cy - half) * s, (cx + half) * s,
                                    (cy + half) * s], radius=radius * s, outline=255,
                                   width=round(max(1.5, 2.4 * u) * s))  # fmt: skip

        shine(out, blur(tinted(coverage((w, h), rim, 2), TRACE, 0.5), 14 * u))
        paint(out, coverage((w, h), body), (9, 20, 23))
        paint(out, coverage((w, h), rim), (34, 92, 92))
        mark_into(out, cx, cy, chip * 0.55)

    def _drawn(self, phase: float | None) -> Canvas:
        out = self.base.copy()
        on = self.which >= 0
        if phase is None:
            level = np.where(on, 0.55 + 0.45 * np.clip(self.along, 0, 1), 0.0)
        else:
            front = (phase + self.offsets[np.clip(self.which, 0, None)]) % 1.0
            behind = (front * 1.4 - self.along) % 1.4
            level = np.where(on, 0.25 + 0.75 * np.exp(-behind / 0.12) * (behind < 1.0), 0.0)
        light = self.cover * level.astype(np.float32)
        u = self.where.unit
        glow(out, light, TRACE, ((4 * u, 1.0), (16 * u, 0.6)))
        np.maximum(out, tinted(light, TRACE), out=out)
        return out

    def still(self) -> Canvas:
        return self._drawn(None)

    def frame(self, phase: float) -> Canvas:
        return self._drawn(phase)


# -- Night ----------------------------------------------------------------------------------------


NIGHT_SKY = ((1, 2, 8), (10, 16, 36))
HORIZON = (30, 70, 120)
MOONLIGHT = (238, 232, 214)


class Night:
    """A sky of stars that twinkle (see the module's text)."""

    def __init__(self, where: Room) -> None:
        self.where = where
        w, h = where.w, where.h
        u = where.unit
        xs, ys = _grid(where)
        rng = _rng(where, 4)
        self.base = vertical(h, w, *NIGHT_SKY)
        glow = np.exp(-(((h - ys) / (h * 0.28)) ** 1.6))  # the horizon's glow, low
        self.base += glow[..., None] * np.asarray(HORIZON, np.float32) * 0.55
        # a band of haze across, the milky way
        angle = -0.35
        d = (ys - h * 0.45) * math.cos(angle) - (xs - w * 0.5) * math.sin(angle)
        band = np.exp(-((d / (min(w, h) * 0.22)) ** 2))
        haze = blur(rng.random((h // 4 + 1, w // 4 + 1)).astype(np.float32), 2.5)
        haze = np.asarray(Image.fromarray(haze, "F").resize((w, h), Image.Resampling.BILINEAR))
        self.base += (band * (0.4 + haze))[..., None] * np.asarray((10, 14, 26), np.float32)
        # stars: many faint, few bright, more in the haze
        n = int(w * h / 900)
        sx, sy = rng.uniform(0, w, n), rng.uniform(0, h, n)
        keep = rng.random(n) < 0.35 + 0.65 * band[sy.astype(int), sx.astype(int)]
        sx, sy = sx[keep], sy[keep]
        bright = rng.pareto(2.6, len(sx)) * 0.25 + 0.12
        tint = rng.choice(3, len(sx), p=(0.6, 0.25, 0.15))
        colours = np.asarray([(235, 240, 255), (190, 210, 255), (255, 228, 200)], np.float32)
        beats = rng.integers(1, 4, len(sx))
        offsets = rng.uniform(0, TAU, len(sx)).astype(np.float32)
        depth = rng.uniform(0.1, 0.5, len(sx)).astype(np.float32)  # how much each twinkles
        sigma = 0.55 * max(1.0, u)
        x, y = sx.astype(np.int32), sy.astype(np.int32)
        level = np.clip(bright, 0.05, 1.4).astype(np.float32)

        def field(weights: np.ndarray) -> np.ndarray:
            """The stars' light with these weights, blurred as a star shines."""
            out = np.zeros((h, w, 3), np.float32)
            np.add.at(out, (y, x), colours[tint] * weights[:, None])
            return blur(out, sigma) * 4.0 + blur(out, sigma * 4) * 2.0

        # a star's light, level * (1 + depth * sin(2 pi beats t + offset)), is the still field
        # and, for each number of beats, a field each for the sine and the cosine of the time:
        # a picture of the clip is these few fields added, not every star drawn again
        self.still_field = field(level)
        self.waves: list[tuple[int, np.ndarray, np.ndarray]] = []
        for b in (1, 2, 3):
            mine = (beats == b).astype(np.float32) * level * depth
            self.waves.append((b, field(mine * np.cos(offsets)), field(mine * np.sin(offsets))))
        # the moon, a crescent, high on the side (within the circle of a round panel)
        bar = max(where.rw, where.rh) >= 2.4 * min(where.rw, where.rh)
        size = round(min(where.rw, where.rh) * (0.34 if bar else 0.22))
        self.moon = moon.picture(size, 0.18, MOONLIGHT, (40, 46, 60))
        if where.round:
            mx, my = where.cx + where.rw * 0.16, where.cy - where.rh * 0.2
        elif w >= h:
            mx, my = where.x0 + where.rw * (0.8 if bar else 0.76), where.y0 + where.rh * 0.3
        else:
            mx, my = where.x0 + where.rw * 0.62, where.y0 + where.rh * (0.12 if bar else 0.24)
        self.moon_at = (round(mx - size / 2), round(my - size / 2))

    def _drawn(self, phase: float | None) -> Canvas:
        out = self.base + self.still_field
        if phase is not None:
            for b, sine, cosine in self.waves:
                out += sine * math.sin(TAU * b * phase) + cosine * math.cos(TAU * b * phase)
        rgba = np.asarray(self.moon, np.float32)
        mx, my = self.moon_at
        region = out[max(0, my) : my + rgba.shape[0], max(0, mx) : mx + rgba.shape[1]]
        part = rgba[: region.shape[0], : region.shape[1]]
        region += (part[..., :3] - region) * (part[..., 3:] / 255)
        return out

    def still(self) -> Canvas:
        return self._drawn(None)

    def frame(self, phase: float) -> Canvas:
        return self._drawn(phase)


# -- Levels ---------------------------------------------------------------------------------------


LEVELS_LOW = (46, 230, 214)
LEVELS_HIGH = (180, 90, 255)


class Levels:
    """An equaliser's bars from a line in the middle (see the module's text)."""

    def __init__(self, where: Room) -> None:
        self.where = where
        w, h = where.w, where.h
        u = where.unit
        self.base = vertical(h, w, BG, BG2)
        a0, a1, b0, b1 = _long(where)
        if where.round:  # within the circle
            a0, a1 = where.cx - where.rw * 0.36, where.cx + where.rw * 0.36
        self.wide = w >= h
        span = a1 - a0
        self.count = count = max(12, int(span / (22 * u)))
        self.half = (b1 - b0) * 0.42  # the longest bar's reach from the middle
        self.segments = segments = max(6, int(self.half / (11 * u)))
        pitch = span / count
        self.bars = [(a0 + pitch * (k + 0.16), a0 + pitch * (k + 0.84)) for k in range(count)]
        self.middle = (b0 + b1) / 2
        seg = self.half / segments
        rng = _rng(where, 5)
        # every segment's box, and its colour (teal near the middle, violet at the ends)
        self.cells: list[tuple[int, int, np.ndarray]] = []  # (bar, step, coverage box)
        boxes = []
        for k, (p, q) in enumerate(self.bars):
            for i in range(segments):
                for side in (-1, 1):
                    near = self.middle + side * (seg * i + seg * 0.18)
                    far = self.middle + side * (seg * (i + 1) - seg * 0.18)
                    lo, hi = min(near, far), max(near, far)
                    boxes.append((k, i, (p, lo, q, hi)))
        self.cover = np.zeros((h, w), np.float32)
        self.bar_of = np.full((h, w), -1, np.int32)
        self.step_of = np.zeros((h, w), np.float32)

        def flip(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
            p, lo, q, hi = box
            return (p, lo, q, hi) if self.wide else (lo, p, hi, q)

        def cells(draw: ImageDraw.ImageDraw, s: int) -> None:
            for _k, _i, box in boxes:
                x0, y0, x1, y1 = flip(box)
                draw.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s],
                                       radius=max(1.0, 1.6 * u) * s, fill=255)  # fmt: skip

        self.cover = coverage((w, h), cells)
        label = Image.new("F", (w, h), -1.0)
        step = Image.new("F", (w, h), 0.0)
        dl, ds = ImageDraw.Draw(label), ImageDraw.Draw(step)
        for k, i, box in boxes:
            x0, y0, x1, y1 = flip(box)
            dl.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], fill=float(k))
            ds.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], fill=float(i + 0.5) / segments)
        self.bar_of = np.asarray(label, np.float32).astype(np.int32)
        self.step_of = np.asarray(step, np.float32)
        mix = np.clip(self.step_of, 0, 1)[..., None] ** 1.2
        self.colour = (1 - mix) * np.asarray(LEVELS_LOW, np.float32) + mix * np.asarray(
            LEVELS_HIGH, np.float32)  # fmt: skip
        paint(self.base, self.cover, (16, 22, 30))  # the cells unlit
        x = np.linspace(-1, 1, count)
        self.shape = (0.35 + 0.55 * np.exp(-((x / 0.75) ** 2))).astype(np.float32)
        self.beats = rng.integers(1, 4, (3, count))
        self.offsets = rng.uniform(0, TAU, (3, count))
        self.depth = np.asarray((0.30, 0.18, 0.10), np.float32)

    def _heights(self, phase: float | None) -> np.ndarray:
        if phase is None:
            k = np.arange(self.count)
            wobble = 0.12 * np.sin(k * 1.7) + 0.08 * np.sin(k * 0.6 + 1)
            return np.clip(self.shape + wobble, 0.08, 1.0)
        swing = sum(self.depth[j] * np.sin(TAU * self.beats[j] * phase + self.offsets[j])
                    for j in range(3))  # fmt: skip
        return np.clip(self.shape * (0.75 + swing), 0.06, 1.0)

    def _drawn(self, phase: float | None) -> Canvas:
        out = self.base.copy()
        heights = self._heights(phase).astype(np.float32)
        reach = heights[np.clip(self.bar_of, 0, None)]
        # a cell is lit below the bar's height, the last one in part (it glides)
        part = np.clip((reach - self.step_of) * self.segments + 0.5, 0, 1)
        level = np.where(self.bar_of >= 0, 0.06 + 0.94 * part, 0.0).astype(np.float32)
        lit = self.colour * (self.cover * level)[..., None]
        u = self.where.unit
        glow(out, lit, None, ((5 * u, 0.7), (18 * u, 0.5)))
        np.maximum(out, lit, out=out)
        return out

    def still(self) -> Canvas:
        return self._drawn(None)

    def frame(self, phase: float) -> Canvas:
        return self._drawn(phase)


# -- Dark -----------------------------------------------------------------------------------------


class Dark:
    """Black, with the app's mark faintly (see the module's text)."""

    def __init__(self, where: Room) -> None:
        self.where = where
        self.d = min(where.rw, where.rh) * 0.18

    def _drawn(self, light: float) -> Canvas:
        where = self.where
        out = np.zeros((where.h, where.w, 3), np.float32)
        mark_into(out, where.cx, where.cy, self.d, light, glow=0.05)
        return out * (0.18 + 0.12 * light)

    def still(self) -> Canvas:
        return self._drawn(1.0)

    def frame(self, phase: float) -> Canvas:
        return self._drawn(0.5 + 0.5 * math.cos(TAU * phase))


DESIGNS: dict[str, Design] = {
    "aurora": Design("aurora", 88, Aurora),
    "pulse": Design("pulse", 66, Pulse),
    "circuit": Design("circuit", 66, Circuit),
    "night": Design("night", 88, Night),
    "levels": Design("levels", 44, Levels),  # (it moves everywhere: a short loop fits)
    "dark": Design("dark", 88, Dark),
}

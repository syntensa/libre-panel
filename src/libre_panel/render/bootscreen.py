"""The panel's own two screens: the boot logo it shows when it is powered on, and the standby
clip it plays when the PC's USB comes up, until Libre Panel streams (devices/panel_files.py
puts them on a TURZX USB panel). Each comes in a choice of looks (:func:`designs`): Libre
Panel's own (this module), and those of render/bootlooks.py.

Every look is drawn for the panel's size, in landscape (a round panel square), or upright for a
bar that stands on end (``orientation``), in floating point, and only made 8 bits at the very
end, with a dither of a level's worth (:func:`finish`): a dark gradient across 720 px spans a
handful of levels, whose steps showed as bands, and a glow blurred in 8 bits as rings in its
dark end. Nothing that matters is near the short ends: the panel draws its boot JPEG into a
smaller area that leaves about a thirtieth of the long side at both ends as it was (measured
on the 9.2"), and they fade out to black. The strip the bezel hides (``hidden`` in the
catalog) is left out of the middle (:class:`Room`).

A clip moves only in brightness at fixed places, which the panel's uneven playback forgives (a
moving dot would stutter). It has a multiple of the 11 pictures the panel's decoder takes per
sequence (:attr:`Design.frames`) and plays at :data:`CLIP_FPS`, the rate the panel itself sets
at a cold start.

Libre Panel's own look: the app's mark (a gauge's arc round three bars, as its icon), the name,
and the line of light under them, on the dark backdrop of Lumen with its dot grid. In the clip
the line of light is a row of dashes along which a soft wave of light runs outwards from the
middle, and the mark's arc breathes with it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from libre_panel import branding
from libre_panel.devices.models import PanelModel
from libre_panel.fonts import builtin_font_path

CLIP_FRAMES = 44  # Libre Panel's own clip: four sequences of 11 pictures
CLIP_FPS = 30  # the panel's own rate after a cold start: 44 pictures are about 1.5 s
SEQUENCE = 11  # the pictures the panel's decoder takes per sequence
QUIET = 64 / 1920  # of the long side, at both ends: the boot JPEG leaves it as it was

BG = (10, 14, 19)
BG2 = (6, 9, 13)
DOTS = (22, 30, 40)
TEXT = (232, 238, 244)
ACCENT = (46, 230, 214)
SS = 3  # supersampling of the shapes

Canvas = np.ndarray  # a picture in floating point: rows x columns x 3, 0 to 255


# -- where a picture's parts go -------------------------------------------------------------------


@dataclass(frozen=True)
class Room:
    """A picture ``w`` x ``h`` and the box its parts may take (x0, y0 to x1, y1): without the
    strip the bezel hides and the quiet ends of the long side; ``round``: a round panel, whose
    parts keep within the circle."""

    w: int
    h: int
    x0: float
    y0: float
    x1: float
    y1: float
    round: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def rw(self) -> float:
        return self.x1 - self.x0

    @property
    def rh(self) -> float:
        return self.y1 - self.y0

    @property
    def unit(self) -> float:
        """px per px of a picture 480 px across its short side."""
        return min(self.rw, self.rh) / 480

    @property
    def quiet(self) -> int:
        return round(QUIET * max(self.w, self.h))


def design_size(model: PanelModel, orientation: str = "landscape") -> tuple[int, int]:
    """The picture's size before it is turned for the panel: landscape (a round one square),
    or upright ("portrait") for a bar on end."""
    return model.size(orientation)


def room(model: PanelModel, orientation: str = "landscape", scale: float = 1.0) -> Room:
    """The room of ``model``'s picture in ``orientation`` (``scale``: smaller, for the app's
    pictures of it)."""
    w, h = design_size(model, orientation)
    edges = model.hidden_edges(orientation) if hasattr(model, "hidden_edges") else {}
    w, h = max(8, round(w * scale)), max(8, round(h * scale))
    edges = {side: px * scale for side, px in edges.items()}
    quiet = round(QUIET * max(w, h))  # at both ends of the long side
    across, along = (quiet, 0) if w >= h else (0, quiet)
    return Room(w, h, edges.get("left", 0) + across, edges.get("top", 0) + along,
                w - edges.get("right", 0) - across, h - edges.get("bottom", 0) - along,
                getattr(model, "shape", "rect") == "round")  # fmt: skip


# -- light in floating point ----------------------------------------------------------------------


def _boxes(sigma: float, n: int = 3) -> list[int]:
    """The widths of ``n`` box blurs that together blur as a Gaussian of ``sigma``."""
    ideal = math.sqrt(12 * sigma * sigma / n + 1)
    low = int(ideal) - (1 - int(ideal) % 2)  # odd
    m = round((12 * sigma * sigma - n * low * low - 4 * n * low - 3 * n) / (-4 * low - 4))
    return [low if i < m else low + 2 for i in range(n)]


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """The mean over 2r + 1 along ``axis``; beyond the picture is dark."""
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad), axis=axis)
    high = [slice(None)] * a.ndim
    low = [slice(None)] * a.ndim
    high[axis], low[axis] = slice(2 * r + 1, 2 * r + 1 + n), slice(0, n)
    return (c[tuple(high)] - c[tuple(low)]) / (2 * r + 1)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """``a`` (rows x columns, with channels or without) blurred as with a Gaussian of ``sigma``
    px, in floating point: three box blurs each way. A wide one is blurred at a fraction of the
    size and brought back (its light has no detail to lose)."""
    if sigma < 0.4:
        return a.astype(np.float32, copy=True)
    shrink = max(1, int(sigma / 3))
    if shrink > 1:
        h, w = a.shape[:2]
        small = _resized(a, (max(1, round(w / shrink)), max(1, round(h / shrink))))
        return _resized(blur(small, sigma / shrink), (w, h))
    out = a.astype(np.float64)
    for width in _boxes(sigma):
        for axis in (0, 1):
            out = _box(out, width // 2, axis)
    return out.astype(np.float32)


def _resized(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """A float array brought to ``size`` (w, h), channel by channel."""
    if a.ndim == 2:
        image = Image.fromarray(np.ascontiguousarray(a, np.float32), "F")
        return np.asarray(image.resize(size, Image.Resampling.BILINEAR), np.float32)
    return np.stack([_resized(a[..., c], size) for c in range(a.shape[2])], axis=2)


def coverage(size: tuple[int, int], draw: Callable[[ImageDraw.ImageDraw, int], None],
             ss: int = SS) -> np.ndarray:  # fmt: skip
    """How much of each pixel (0 to 1) what ``draw(d, s)`` draws covers: it draws at ``s``
    times the size (``s`` is its coordinates' scale), smooth at its edges."""
    w, h = size
    image = Image.new("L", (w * ss, h * ss), 0)
    draw(ImageDraw.Draw(image), ss)
    return np.asarray(image.reduce(ss) if ss > 1 else image, np.float32) / 255


def paint(canvas: Canvas, cover: np.ndarray, colour: tuple[float, ...],
          at: tuple[int, int] = (0, 0), alpha: float = 1.0) -> None:  # fmt: skip
    """``colour`` over the canvas where ``cover`` (placed at ``at``) covers it."""
    x, y = at
    h, w = cover.shape
    part = canvas[y : y + h, x : x + w]
    part += (np.asarray(colour, np.float32) - part) * (cover[: part.shape[0], : part.shape[1],
                                                             None] * alpha)  # fmt: skip


def shine(canvas: Canvas, light: np.ndarray, at: tuple[int, int] = (0, 0)) -> None:
    """Light added (rows x columns x 3, placed at ``at``), as a glow adds to what is under it."""
    x, y = at
    h, w = light.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.shape[1], x + w), min(canvas.shape[0], y + h)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] += light[y0 - y : y1 - y, x0 - x : x1 - x]


def tinted(cover: np.ndarray, colour: tuple[float, ...], gain: float = 1.0) -> np.ndarray:
    """``cover`` as light of ``colour``."""
    return cover[..., None] * (np.asarray(colour, np.float32) * gain)


def glow(canvas: Canvas, light: np.ndarray, colour: np.ndarray | tuple[float, ...] | None,
         parts: tuple[tuple[float, float], ...]) -> None:  # fmt: skip
    """The glow of ``light`` added to the canvas: blurred by each (sigma, gain) of ``parts``,
    only in the box round what is lit. ``light`` is rows x columns, of ``colour``; or rows x
    columns x 3 with ``colour`` None."""
    lit = light if light.ndim == 2 else light.max(axis=2)
    rows, cols = np.nonzero(lit > 1e-3)
    if not len(rows):
        return
    reach = math.ceil(3 * max(sigma for sigma, _ in parts)) + 1
    h, w = lit.shape
    y0, y1 = max(0, rows.min() - reach), min(h, rows.max() + 1 + reach)
    x0, x1 = max(0, cols.min() - reach), min(w, cols.max() + 1 + reach)
    part = light[y0:y1, x0:x1]
    out = sum(blur(part, sigma) * gain for sigma, gain in parts)
    if colour is not None:
        out = out[..., None] * np.asarray(colour, np.float32)
    canvas[y0:y1, x0:x1] += out


def vertical(h: int, w: int, top: tuple[int, ...], bottom: tuple[int, ...]) -> Canvas:
    """A canvas from ``top`` to ``bottom``, evenly."""
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    a, b = np.asarray(top, np.float32), np.asarray(bottom, np.float32)
    return np.broadcast_to(a + (b - a) * ys, (h, w, 3)).copy()


@lru_cache(maxsize=8)
def _dither(w: int, h: int) -> np.ndarray:
    """A level's worth of noise (triangular, -1 to 1), the same for every picture of a size:
    a clip's pictures differ only where they move."""
    rng = np.random.default_rng(w * 7919 + h)
    return rng.random((h, w), np.float32) + rng.random((h, w), np.float32) - 1.0


@lru_cache(maxsize=8)
def _ends(w: int, h: int, quiet: int, round_panel: bool) -> np.ndarray:
    """How much of a picture shows (0 to 1): the short ends fade to black over a little more
    than the quiet ends; a round panel's corners are dark."""
    long_axis = 1 if w >= h else 0
    n = w if long_axis else h
    ramp = max(1, round(quiet * 2.2))
    fade = np.clip(np.arange(n, dtype=np.float32) / ramp, 0, 1) ** 1.6
    edge = np.minimum(fade, fade[::-1])
    out = np.broadcast_to(edge[None, :] if long_axis else edge[:, None], (h, w)).copy()
    if round_panel:
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.hypot(xs - (w - 1) / 2, ys - (h - 1) / 2) / (min(w, h) / 2)
        out *= np.clip((1.0 - r) / 0.04, 0, 1)
    return out


def finish(canvas: Canvas, where: Room, dither: bool = True) -> Image.Image:
    """The canvas as the RGB picture the panel gets: its ends faded, and made 8 bits with the
    dither where anything is lit (black stays black); without it (``dither`` False) for the
    app's small pictures, whose PNG it would make large."""
    out = canvas * _ends(where.w, where.h, where.quiet, where.round)[..., None]
    if dither:
        lit = np.clip(out.max(axis=2), 0, 1)
        out += (_dither(where.w, where.h) * lit)[..., None]
    return Image.fromarray(np.clip(np.floor(out + 0.5), 0, 255).astype(np.uint8), "RGB")


# -- the looks ------------------------------------------------------------------------------------


class Look(Protocol):
    """A look drawn for a room: ``still()`` the boot logo, ``frame(phase)`` the standby clip at
    that point of its loop (0 to 1), each a canvas of its own."""

    def still(self) -> Canvas: ...

    def frame(self, phase: float) -> Canvas: ...


@dataclass(frozen=True)
class Design:
    """A look the panel's own screens can have: its id, how many pictures its clip has (a
    multiple of :data:`SEQUENCE`), and what draws it for a room."""

    id: str
    frames: int
    make: Callable[[Room], Look]


DEFAULT = "libre-panel"


def designs() -> dict[str, Design]:
    """Every look, Libre Panel's own first (render/bootlooks.py has the others)."""
    from libre_panel.render import bootlooks

    return {DEFAULT: Design(DEFAULT, CLIP_FRAMES, Classic), **bootlooks.DESIGNS}


def design_of(name: str | None) -> Design:
    """The look called ``name`` (Libre Panel's own for None or ""); KeyError for no such look."""
    return designs()[name or DEFAULT]


@lru_cache(maxsize=2)
def _look(model: PanelModel, orientation: str, design: str,
          scale: float = 1.0) -> tuple[Look, Room]:  # fmt: skip
    where = room(model, orientation, scale)
    return design_of(design).make(where), where


def boot_logo(model: PanelModel, orientation: str = "landscape", design: str = DEFAULT,
              scale: float = 1.0) -> Image.Image:  # fmt: skip
    """The boot logo for ``model``, in ``orientation`` (a round one square); ``scale``: smaller,
    for the app (without the dither)."""
    look, where = _look(model, orientation, design, scale)
    return finish(look.still(), where, dither=scale == 1.0)


def standby_frames(model: PanelModel, count: int | None = None, orientation: str = "landscape",
                   design: str = DEFAULT, scale: float = 1.0) -> list[Image.Image]:  # fmt: skip
    """The standby clip's pictures for ``model``, one loop (``count`` of them, else the look's
    own number), in ``orientation``; ``scale``: smaller, for the app (without the dither)."""
    count = count or design_of(design).frames
    look, where = _look(model, orientation, design, scale)
    return [finish(look.frame(i / count), where, dither=scale == 1.0) for i in range(count)]


def picture(model: PanelModel, phase: float | None = None, orientation: str = "landscape",
            design: str = DEFAULT) -> Image.Image:  # fmt: skip
    """The picture for ``model``, RGB, in ``orientation``: the boot logo with ``phase`` None,
    else the standby clip at that point of its loop (0 to 1)."""
    look, where = _look(model, orientation, design)
    return finish(look.still() if phase is None else look.frame(phase), where)


# -- what the looks draw with ---------------------------------------------------------------------


def font(size: float, face: str = "Barlow-SemiBold") -> ImageFont.FreeTypeFont:
    path = builtin_font_path(f"builtin:{face}")
    return ImageFont.truetype(str(path), max(6, round(size)))


def mark_into(canvas: Canvas, cx: float, cy: float, d: float, light: float = 1.0,
              glow: float = 0.06) -> None:  # fmt: skip
    """The app's mark (branding.py), its ring ``d`` px across round (cx, cy), with its glow
    in floating point; ``light`` dims what is lit."""
    shape = branding.mark(d, light, glow=0)
    rgba = np.asarray(shape, np.float32)
    alpha = rgba[..., 3] / 255
    x, y = round(cx - shape.width / 2), round(cy - shape.height / 2)
    if glow > 0:
        lit = rgba[..., :3] * alpha[..., None]
        pad = math.ceil(d * glow * 3)
        lit = np.pad(lit, ((pad, pad), (pad, pad), (0, 0)))
        shine(canvas, blur(lit, max(1.0, d * glow)) * (0.5 + 0.35 * light), (x - pad, y - pad))
    region = canvas[max(0, y) : y + shape.height, max(0, x) : x + shape.width]
    dy, dx = max(0, -y), max(0, -x)
    cut = alpha[dy : dy + region.shape[0], dx : dx + region.shape[1], None]
    region += (rgba[dy : dy + region.shape[0], dx : dx + region.shape[1], :3] - region) * cut


def text_into(
    canvas: Canvas, parts: list[tuple[str, tuple[int, ...]]], x: float, baseline: float,
    face: ImageFont.FreeTypeFont, alpha: float = 1.0,
) -> None:  # fmt: skip
    """Words in their colours, one after the other from ``x`` on ``baseline``, smooth."""
    h, w = canvas.shape[:2]
    big = face.font_variant(size=face.size * SS)
    at = x
    for words, colour in parts:

        def draw(d: ImageDraw.ImageDraw, s: int, words: str = words, here: float = at) -> None:
            d.text((here * s, baseline * s), words, font=big, fill=255, anchor="ls")

        paint(canvas, coverage((w, h), draw), colour, alpha=alpha)
        at += face.getlength(words)


# -- Libre Panel's own look -----------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Where the parts go on a picture ``size``: the mark's box (x, y, d), the name's left
    and the top of its capitals and its size, the line of light (x0, x1, y) and its dashes."""

    size: tuple[int, int]
    mark: tuple[float, float, float]
    name: tuple[float, float, float]
    line: tuple[float, float, float]
    dashes: int
    unit: float  # px per px of the 1920 x 480 design


def _name_width(size: float) -> float:
    return font(size).getlength("Libre Panel")


def layout(model: PanelModel, orientation: str = "landscape") -> Layout:
    """The parts of Libre Panel's own picture for ``model``: side by side on a bar, the name
    under the mark on a broad panel or a bar on end, and smaller within the circle of a round
    one."""
    return layout_in(room(model, orientation))


def layout_in(where: Room) -> Layout:
    w, h = where.w, where.h
    room_w, room_h = where.rw, where.rh
    cx, cy = where.cx, where.cy
    aspect = room_w / max(1, room_h)
    if aspect >= 2.4:  # a bar: the mark beside the name, the line under both
        d = room_h * 0.42
        size = room_h * 0.2
        gap = d * 0.28
        group = d + gap + _name_width(size)
        scale = min(1.0, room_w * 0.8 / group)
        d, size, gap, group = d * scale, size * scale, gap * scale, group * scale
        x = cx - group / 2
        block = d + room_h * 0.16
        y = cy - block / 2
        name_top = y + d / 2 - 0.7 * size / 2
        line_y = y + d + room_h * 0.16
        half = max(group * 0.62, room_w * 0.28)
        return Layout((w, h), (x, y, d), (x + d + gap, name_top, size),
                      (cx - half, cx + half, line_y), 31, room_h / 480)  # fmt: skip
    # broad, square, round or a bar on end: the name under the mark
    fit = 0.62 if where.round else 0.86
    tall = aspect <= 0.5
    d = room_w * 0.56 if tall else min(room_h * 0.4, room_w * 0.3) * (0.78 if where.round else 1.0)
    size = d * 0.42
    name_w = _name_width(size)
    if name_w > room_w * fit:
        size *= room_w * fit / name_w
        name_w = _name_width(size)
    block = d + d * 0.24 + 0.7 * size + d * 0.3
    y = cy - block / 2
    name_top = y + d + d * 0.24
    line_y = name_top + 0.7 * size + d * 0.3
    half = min(max(name_w * 0.62, d * 0.9), room_w * fit / 2)
    unit = min(room_w, room_h) / 480 if tall else min(room_w / 1920, room_h / 480) * 1.6
    return Layout((w, h), (cx - d / 2, y, d), (cx - name_w / 2, name_top, size),
                  (cx - half, cx + half, line_y), 21, unit)  # fmt: skip


def backdrop(where: Room) -> Canvas:
    """Lumen's backdrop: a dark gradient with a dot every 22 px (at 480 px high)."""
    w, h = where.w, where.h
    out = vertical(h, w, BG, BG2)
    unit = min(w, h) / 480
    step = max(6, round(22 * unit))
    r = max(0.6, 1.1 * unit)

    def dots(draw: ImageDraw.ImageDraw, s: int) -> None:
        for y in range(step // 2, h, step):
            for x in range(step // 2, w, step):
                draw.ellipse([(x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s], fill=255)

    paint(out, coverage((w, h), dots), DOTS, alpha=0.9)
    return out


def _wave(i: int, count: int, phase: float) -> float:
    """How lit dash ``i`` of ``count`` is at ``phase`` (0 to 1): a soft wave running out from
    the middle to both ends, over a light that never goes out."""
    middle = (count - 1) / 2
    distance = abs(i - middle) / max(1.0, middle)  # 0 in the middle, 1 at the ends
    front = phase * 1.35 - 0.15
    crest = math.exp(-(((distance - front) / 0.16) ** 2))
    return 0.28 + 0.72 * crest * (1 - 0.35 * distance)


class Classic:
    """Libre Panel's own look (see the module's text)."""

    def __init__(self, where: Room) -> None:
        self.lay = lay = layout_in(where)
        self.base = base = backdrop(where)
        nx, top, size = lay.name
        text_into(base, [("Libre ", TEXT), ("Panel", ACCENT)], nx, top + 0.7 * size, font(size))

    def still(self) -> Canvas:
        return self._drawn(None)

    def frame(self, phase: float) -> Canvas:
        return self._drawn(phase)

    def _drawn(self, phase: float | None) -> Canvas:
        out = self.base.copy()
        x, y, d = self.lay.mark
        breathe = 1.0 if phase is None else 0.75 + 0.25 * math.cos(2 * math.pi * phase)
        mark_into(out, x + d / 2, y + d / 2, d, round(breathe, 3))
        self._line(out, phase)
        return out

    def _line(self, out: Canvas, phase: float | None) -> None:
        """The line of light: one soft line in the boot logo, a row of dashes the light runs
        along in the clip; with its glow. Drawn in a band round it."""
        lay = self.lay
        x0, x1, y = lay.line
        w, h = lay.size
        thick = max(2.0, 4.0 * lay.unit)
        reach = math.ceil(max(12.0, 70 * lay.unit))  # how far its glow goes
        bx0, by0 = max(0, math.floor(x0) - reach), max(0, math.floor(y) - reach)
        bx1, by1 = min(w, math.ceil(x1) + reach), min(h, math.ceil(y) + reach)
        bw, bh = bx1 - bx0, by1 - by0
        pieces: list[tuple[tuple[float, float, float, float], float, float]] = []
        if phase is None:  # a line, brightest in the middle
            steps = 64
            for i in range(steps):
                a, b = x0 + (x1 - x0) * i / steps, x0 + (x1 - x0) * (i + 1) / steps
                middle = abs((a + b) / 2 - (x0 + x1) / 2) / ((x1 - x0) / 2)
                level = (1 - middle**1.8) ** 1.2
                pieces.append(((a, y - thick / 2, b + 1 / SS, y + thick / 2), 0.0, level))
        else:  # dashes the light runs along
            count = lay.dashes
            step = (x1 - x0) / count
            for i in range(count):
                a, b = x0 + i * step + step * 0.18, x0 + (i + 1) * step - step * 0.18
                level = _wave(i, count, phase)
                pieces.append(((a, y - thick / 2, b, y + thick / 2), thick / 2, level))
        lit = np.zeros((bh, bw), np.float32)
        for box, radius, level in pieces:  # each in a box of its own, a pixel round it
            px0, py0 = math.floor(box[0]) - 1, math.floor(box[1]) - 1
            px1, py1 = math.ceil(box[2]) + 1, math.ceil(box[3]) + 1

            def draw(d: ImageDraw.ImageDraw, s: int, box: tuple[float, ...] = box,
                     radius: float = radius, px0: int = px0, py0: int = py0) -> None:  # fmt: skip
                corners = ((box[0] - px0) * s, (box[1] - py0) * s, (box[2] - px0) * s,
                           (box[3] - py0) * s)  # fmt: skip
                if radius:
                    d.rounded_rectangle(corners, radius=radius * s, fill=255)
                else:
                    d.rectangle(corners, fill=255)

            piece = coverage((px1 - px0, py1 - py0), draw) * level
            here = lit[py0 - by0 : py1 - by0, px0 - bx0 : px1 - bx0]
            np.maximum(here, piece[: here.shape[0], : here.shape[1]], out=here)
        near = blur(lit, max(2.0, 10 * lay.unit))
        light = tinted(near + blur(near, max(4.0, 26 * lay.unit)), ACCENT)
        band = out[by0:by1, bx0:bx1]
        band[...] = np.maximum(band + light, tinted(lit, ACCENT))

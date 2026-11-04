"""The ``"lit"`` finish: SPUR II's light on bars, rings and graphs, and the
weather icons as lit weather symbols.

- A bar's value is light (SPUR II since 2026-09-20): a pill with round ends
  in the middle of its groove, its core running towards white, a soft halo
  in its colour (or gradient, or a threshold's colour); segments are pills
  of their own. Its ``glow`` is SPUR II's bar glow, light added on top:
  brightest at the head and fading back along the bar, so a full bar reads
  as light, not as a block that glows; worked out over its ``glow_area`` as
  SPUR II's glow zones are, and nothing of it outside.
- A ring gauge's value is an arc of the same light on the middle of its
  groove, from ``start_angle`` with square ends: the glow is the arc.
- A graph keeps its fill; its line is light (its core runs towards white)
  in a soft halo of its colour.
- A text's glow is light: its colours blurred and added over it and around
  it, as SPUR II's headings glow.
- A weather icon is its weather's symbol (render.symbols), lit and standing
  still: clouds of frosted glass, a sun that glows, drops, a bolt.

The grooves are the bars' and rings' ``background``, milled into the panel
as the facet finish mills them (render.facets). A bar or ring too thin
for light, or an icon under 16 px, is drawn flat. Coloured pictures are kept
by colour and step, so a value gliding back and forth colours each step once.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from libre_panel.render import dither, facets, symbols
from libre_panel.render import scene as scenes
from libre_panel.render.finish import Finish, register
from libre_panel.render.kept import MB, Kept, size_of
from libre_panel.render.light import mix
from libre_panel.render.renderer import Piece, _fraction, _number, _steps

GLOW_TAIL = 0.55 * 0.34  # a bar's glow along its length, of its colour ...
GLOW_WHITE = 0.65  # ... the head runs this far towards white
GLOW_STEPS = 7  # steps of the rise: more do not show once blurred
# each step of the rise: the share of the colour it lights, how far it runs towards white
_RISE = tuple(
    (GLOW_TAIL + (1 - GLOW_TAIL) * t**1.4, GLOW_WHITE * t**2)
    for t in ((i + 1) / GLOW_STEPS for i in range(GLOW_STEPS))
)
HALO = 0.9  # a lit graph's halo at glow 1 ...
LINE_WHITE = 0.45  # ... and how far its line runs towards white
MIN_ICON = 16

# masks, coloured lights and symbols: Lumen's on the 12.3" take 2.5 MB. Encore's light at each
# value of its bars, each made once (two in three of them asked for again in the same moment):
# within 48 MB they took 12 MB after a minute and grew on, within these as many are found.
PICTURE_BYTES = 4 * MB
_PICTURES = Kept("lit.pictures", PICTURE_BYTES)


def _unit(color: tuple[int, ...] | None) -> np.ndarray | None:
    return None if color is None else np.asarray(color[:3], np.float32) / 255


def _key(color: tuple[int, ...] | None) -> tuple[int, ...] | None:
    return None if color is None else tuple(color)


@lru_cache(maxsize=256)
def _scale(factor: float) -> tuple[int, ...]:
    """A table that scales a channel by ``factor`` (for ``Image.point``)."""
    return tuple(min(255, round(v * factor)) for v in range(256))


def _alpha_scaled(image: Image.Image, factor: float) -> Image.Image:
    if factor >= 0.999:
        return image
    out = image.copy()
    out.putalpha(image.getchannel("A").point(_scale(factor)))
    return out


# -- light: bars and rings ---------------------------------------------------
#
# SPUR II's value is light, not a body (2026-09-20): a core that runs towards
# white and a soft halo beside it, in a groove milled into the panel (its
# scale). Nothing lags behind the shape, the glow is the shape.

LIGHT_WHITE = 0.75  # the core runs this far towards white
LIGHT_HALO = 1.45  # how strongly the halo stands beside the core
BAR_CORE = 0.62  # a bar's light is this share of its groove's thickness ...
BAR_CORE_MIN = 3  # ... and at least this many pixels
BAR_HALO = 4.5  # the halo's softness (px): a bar is flatter than a ring ...
BAR_ROOM = 9  # ... and where it ends, around the light (px)
RING_CORE = 0.6  # a ring's light: this share of its groove (6 of 10 px)
RING_HALO = 6.0
GLOW_HEAD = 30  # the bar glow rises over this many px (on a panel 480 high) to the head


def light(mask: np.ndarray, colour: Any, halo: float,
          box: tuple[int, int, int, int] | None = None) -> np.ndarray:  # fmt: skip
    """SPUR II's light from a coverage ``mask`` (h, w; 0 to 1) in ``colour``
    (RGB 0 to 255, one or one per column): straight RGBA uint8, the core
    towards white, the halo in the colour. With ``box`` (x0, y0, x1, y1) the
    light in that part alone, the same pixels: its halo is still worked out
    from all of ``mask``."""
    # the coverage cut to whole levels, as SPUR II's, and blurred in floating point: the halo,
    # made LIGHT_HALO times stronger, steps a level at a time (blurred to whole levels, its
    # faint edge stood in steps of two, as rings round a ring)
    cut = (mask * 255).astype(np.uint8).astype(np.float32)
    soft = dither.blur(cut, halo) * np.float32(1 / 255)
    c = np.broadcast_to(np.asarray(colour, np.float32)[..., :3], (*mask.shape, 3))
    if box is not None:
        x0, y0, x1, y1 = box
        mask = np.ascontiguousarray(mask[y0:y1, x0:x1])
        soft, c = np.ascontiguousarray(soft[y0:y1, x0:x1]), c[y0:y1, x0:x1]
    core = mask**0.7
    out = np.empty((*mask.shape, 4), np.float32)
    out[..., :3] = c + (255 - c) * (LIGHT_WHITE * core)[..., None]
    out[..., 3] = np.clip(core + LIGHT_HALO * np.clip(soft - mask, 0, 1), 0, 1) * 255
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _pill(length: int, thick: int, room: int) -> np.ndarray:
    """A pill ``length`` x ``thick`` with ``room`` around it, as coverage."""

    def make() -> np.ndarray:
        u = 2
        w, h = length + 2 * room, thick + 2 * room
        mask = Image.new("L", (w * u, h * u), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [room * u, room * u, (room + length) * u - 1, (room + thick) * u - 1],
            radius=thick * u / 2, fill=255,
        )  # fmt: skip
        return np.asarray(mask.resize((w, h), Image.Resampling.LANCZOS), np.float32) / 255

    return _PICTURES.make(("pill", length, thick, room), make)


def _layout(widget: dict[str, Any]) -> tuple[int, int, bool, bool]:
    """(length, thickness, upright, from the far end) of a bar."""
    w, h, direction = widget["w"], widget["h"], widget["direction"]
    upright = direction in ("up", "down")
    length, thick = (h, w) if upright else (w, h)
    return max(1, length), max(1, thick), upright, direction in ("left", "up")


def _spans(widget: dict[str, Any], length: int, frac: float, reverse: bool):
    """The lit spans (start, end, how much) along a bar, from its start, and
    the whole cells for the groove."""
    n = widget["segments"]
    if n <= 0:
        cells = [(0.0, float(length))]
        lit = [(0.0, frac * length, 1.0)] if frac > 0 else []
    else:
        gap = max(0.0, widget["segment_gap"])
        cell = max(1.0, (length - gap * (n - 1)) / n)
        cells = [(i * (cell + gap), i * (cell + gap) + cell) for i in range(n)]
        amounts = [min(1.0, max(0.0, frac * n - i)) for i in range(n)]
        lit = [(a, b, amount) for (a, b), amount in zip(cells, amounts, strict=True) if amount > 0]
    if reverse:
        cells = [(length - b, length - a) for a, b in cells]
        lit = [(length - b, length - a, amount) for a, b, amount in lit]
    return lit, cells


def _bar_colours(widget, length, paint, reverse) -> np.ndarray:
    """The colour of every column (length, 3), or one colour (3), 0 to 1. A
    gradient spans the whole track, so a colour always means the same value."""
    a, b = paint
    if b is None:
        return _unit(a)
    t = (np.arange(length, dtype=np.float32) + 0.5) / length  # along the bar, lying
    n = widget["segments"]
    if n > 0:  # a colour per segment, as the flat bar
        t = np.minimum(np.floor(t * n), n - 1) / max(1, n - 1)
    if reverse:
        t = 1 - t  # it starts at the far end
    start, end = _unit(a), _unit(b)
    return start + (end - start) * t[:, None]


def _groove(renderer: Any, widget: dict[str, Any], length: int, thick: int,
            track: tuple[int, ...]) -> Image.Image:  # fmt: skip
    """A bar's groove, lying (``length`` x ``thick``): milled into the panel
    in the track colour (render.facets), plain where a bevel does not read."""
    milled = facets.groove_bar(length, thick, track)
    if milled is not None:
        return milled
    key = ("bar-groove", length, thick, tuple(track))
    return _PICTURES.make(key, lambda: Image.new("RGBA", (length, thick), tuple(track)))


def _glow_source(length: int, thick: int, spans, colours: np.ndarray, head_px: float,
                 reverse: bool) -> np.ndarray:  # fmt: skip
    """SPUR II's bar glow before it is blurred, lying (thick, length, RGB 0
    to 255), as schein_balken draws it: on the bar a pixel in from each edge,
    dim along the lit part and rising in steps over ``head_px`` to the head,
    which runs towards white; whole pixels and whole colour levels."""
    if reverse:  # filling from the far end: worked out mirrored
        spans = [(length - b, length - a, amount) for a, b, amount in spans]
        colours = colours[::-1] if np.ndim(colours) == 2 else colours
        return _glow_source(length, thick, spans, colours, head_px, False)[:, ::-1]
    start = round(min(a for a, _b, _amount in spans))
    head = max(b for _a, b, _amount in spans)
    first, last = start + 1, math.floor((length - 2) * head / length)  # both lit
    out = np.zeros((thick, length, 3), np.uint8)
    if last < first:
        return out
    c = np.round(np.asarray(colours, np.float64) * 255)  # whole levels, as the theme gave them
    columns = np.arange(length)
    if c.ndim == 1:
        c = np.broadcast_to(c, (length, 3))
    source = np.zeros((length, 3), np.float64)
    rise = min(head_px, last - first)
    if last - rise > first:  # the tail
        tail = columns[first : math.floor(last - rise) + 1]
        source[tail] = np.floor(c[tail] * GLOW_TAIL)
    for i, (level, white) in enumerate(_RISE):  # a later step covers the column they share
        a = math.floor(last - rise + rise * i / GLOW_STEPS)
        b = math.floor(last - rise + rise * (i + 1) / GLOW_STEPS)
        step = np.floor(c[a : b + 1] * level)
        source[a : b + 1] = np.floor(step + (255 - step) * white)
    rows = slice(1, thick - 1) if thick > 2 else slice(0, thick)
    out[rows] = source[None]
    return out


def _glow_piece(renderer: Any, widget: dict[str, Any], frac: float, paint, lit,
                strength: float) -> Piece | None:  # fmt: skip
    """SPUR II's bar glow as light to add over the bar: its source blurred
    over the widget's ``glow_area`` (else around the bar), worked out at a
    fraction of the size as SPUR II's glow zones are, nothing outside it."""
    x, y = widget["x"], widget["y"]
    length, thick, upright, reverse = _layout(widget)
    unit = min(renderer.theme.width, renderer.theme.height) / 480
    radius = max(1.0, float(widget.get("glow_radius", 10)))
    colours = _bar_colours(widget, length, paint, reverse)
    source = _glow_source(length, thick, lit, colours, GLOW_HEAD * unit, reverse)
    if upright:
        source = source.transpose(1, 0, 2)
    if not source.any():
        return None
    ax, ay, aw, ah = _glow_area(widget, radius)
    plane = np.zeros((ah, aw, 3), np.uint8)  # the source where it lies inside the area
    x0, y0 = max(x, ax), max(y, ay)
    x1, y1 = min(x + source.shape[1], ax + aw), min(y + source.shape[0], ay + ah)
    if x1 <= x0 or y1 <= y0:
        return None
    plane[y0 - ay : y1 - ay, x0 - ax : x1 - ax] = source[y0 - y : y1 - y, x0 - x : x1 - x]
    scale = _glow_scale(radius)
    small = Image.fromarray(plane, "RGB")
    if scale > 1:
        small = small.reduce(scale)
    glow = small.filter(ImageFilter.GaussianBlur(radius / scale))
    if scale > 1:
        glow = glow.resize((aw, ah), Image.Resampling.BILINEAR)
    if strength < 0.999:
        glow = glow.point(_scale(strength) * 3)  # a table for each band
    red, green, blue = glow.split()
    out = glow.convert("RGBA")  # its alpha its strongest channel (it is added, not laid)
    out.putalpha(ImageChops.lighter(ImageChops.lighter(red, green), blue))
    return Piece(out, ax, ay)


def _glow_area(widget: dict[str, Any], radius: float) -> tuple[int, int, int, int]:
    """Where a bar's glow is worked out (x, y, w, h on the panel): its ``glow_area``, else round
    the bar as far as the glow reaches."""
    area = widget.get("glow_area") or []
    if len(area) == 4 and area[2] > 0 and area[3] > 0:
        ax, ay, aw, ah = (int(v) for v in area)
        return ax, ay, aw, ah
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    pad = math.ceil(2.5 * radius)
    return x - pad, y - pad, w + 2 * pad, h + 2 * pad


def _glow_scale(radius: float) -> int:
    """The fraction of its area's size a bar's glow is worked out at (SPUR II: a quarter)."""
    return max(1, min(4, math.floor(radius / 3)))


def _bar_core(widget: dict[str, Any], thick: int, upright: bool) -> tuple[int, int]:
    """A bar's light: its thickness, and its top on the canvas below the room (whole pixels on
    the panel, the light centred in the groove)."""
    core = min(thick, max(BAR_CORE_MIN, round(thick * BAR_CORE)))
    at = widget["y"] if not upright else widget["x"]
    return core, round(at + (thick - core) / 2) - at


def _bar_ground(renderer: Any, widget: dict[str, Any], length: int, thick: int,
                pad: int) -> Image.Image:  # fmt: skip
    """A bar's groove on its canvas, ``pad`` px of room round it, lying (a new picture)."""
    canvas = Image.new("RGBA", (length + 2 * pad, thick + 2 * pad), (0, 0, 0, 0))
    track = renderer.color(widget["background"])
    if track:
        canvas.paste(_groove(renderer, widget, length, thick, track), (pad, pad))
    return canvas


def _bar_canvas(renderer: Any, widget: dict[str, Any], lit, paint, pad: int,
                unit: float) -> Image.Image:  # fmt: skip
    """The lit bar's picture: its groove, the light of each lit span over it (a pill centred
    in the groove), turned where it stands."""
    length, thick, upright, reverse = _layout(widget)
    canvas = _bar_ground(renderer, widget, length, thick, pad)
    if lit:
        colours = _bar_colours(widget, length, paint, reverse)
        alpha = paint[0][3] / 255
        core, offset = _bar_core(widget, thick, upright)
        for a, b, _amount in lit:
            start = round(a)
            span = max(1, round(b - a))
            mask = _pill(span, core, pad)
            columns = np.clip(np.arange(start - pad, start + span + pad), 0, length - 1)
            c = (colours[columns] if np.ndim(colours) == 2 else colours) * 255
            # (a gradient's colours: those of the columns it lies over, along the whole bar)
            along = (start, length, reverse, widget["segments"]) if np.ndim(colours) == 2 else None
            key = ("bar-light", span, core, pad, BAR_HALO * unit, _key(paint[0]), _key(paint[1]),
                   along)  # fmt: skip
            rgba = _PICTURES.make(key, lambda m=mask, c=c: light(m, c, BAR_HALO * unit))
            image = Image.fromarray(rgba, "RGBA")
            if alpha < 0.999:
                image = _alpha_scaled(image, alpha)
            canvas.alpha_composite(image, (start, offset))  # the pill's room is the pad
    if upright:  # drawn lying: its start at the top, its light from the left
        canvas = canvas.transpose(Image.Transpose.TRANSPOSE)
    return canvas


# A pill keeps its first and last columns this far in from its room for every length (engine/
# src/bar.rs): its cap's half disc and Lanczos's reach beyond it; the columns between are alike
# (tests/test_lit_bars.py holds the pills of every length made of them to _pill's pixels).
PILL_REACH = 4
# the most a bar of an engine's is long or a glow's area wide (with its room, px), and the
# most spans (segments) it lights
NATIVE_SIDE = 8192
NATIVE_SPANS = 4096


def _levels(mask: np.ndarray) -> np.ndarray:
    """A pill's coverage as the levels it was made of (it is them over 255, to the bit)."""
    return np.round(mask * 255).astype(np.uint8)


def _bar_base(renderer: Any, widget: dict[str, Any], shape: tuple, glow: tuple | None):
    """What a lit bar is made of at every value (render/scene.py's BarBase), kept: its groove on
    its canvas and its pill in parts, of ``shape`` (bar.rs's: length, thickness, room, light's
    thickness and top, upright, from the far end, segments, halo); ``glow`` its glow's
    (_glow_shape), or None."""
    length, thick, pad, core = shape[:4]
    key = ("bar-base", shape, renderer.color(widget["background"]), glow)

    def make() -> scenes.BarBase:
        side = pad + core + PILL_REACH  # (the pill of 2 core + 9: one middle column)
        reference = _levels(_pill(2 * (side - pad) + 1, core, pad))
        short = tuple(_levels(_pill(span, core, pad)) for span in range(1, 2 * (side - pad)))
        return scenes.BarBase(
            _bar_ground(renderer, widget, length, thick, pad),
            np.ascontiguousarray(reference[:, :side]),
            np.ascontiguousarray(reference[:, side]),
            np.ascontiguousarray(reference[:, -side:]),
            short, shape, glow,
        )  # fmt: skip

    return _PICTURES.make(key, make)


def _glow_shape(widget: dict[str, Any], strength: float, unit: float) -> tuple:
    """How a bar's glow is worked out, as bar.rs takes it: the bar's x, y, its area, the scale
    it is worked out at, its radius, strength, rise and tail, and its steps (_RISE)."""
    radius = max(1.0, float(widget.get("glow_radius", 10)))
    ax, ay, aw, ah = _glow_area(widget, radius)
    return (widget["x"], widget["y"], ax, ay, aw, ah, _glow_scale(radius), radius, strength,
            GLOW_HEAD * unit, GLOW_TAIL, _RISE)  # fmt: skip


def _native_bar(widget: dict[str, Any], length: int, thick: int, pad: int) -> bool:
    """Whether an engine makes the bar as :func:`bar_piece` does: drawn as it is (no shadow,
    fade, cut sides or turn over it), of a size and as many segments as it takes."""
    if widget.get("shadow") or widget.get("clip") or widget.get("rotate"):
        return False
    if widget.get("opacity", 1.0) < 0.999 or widget["segments"] > NATIVE_SPANS:
        return False
    return max(length, thick) + 2 * pad <= NATIVE_SIDE


def bar_piece(renderer: Any, widget: dict[str, Any], frac: float, paint,
              native: bool | None = None) -> Piece:  # fmt: skip
    """The lit bar, lying: the groove, the light of each lit span over it (a pill centred in
    the groove); its glow is light added over it. Where the panel's engine makes bars itself
    (``native``; None: Renderer.native_bars now), its :class:`scenes.Bar`: its picture and
    glow made here only if asked for."""
    native = renderer.native_bars if native is None else native
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    length, thick, upright, reverse = _layout(widget)
    unit = min(renderer.theme.width, renderer.theme.height) / 480
    lit, _cells = _spans(widget, length, frac, reverse)
    pad = math.ceil(BAR_ROOM * unit)  # the halo's room around the light
    strength = widget.get("glow", 0.0)
    if native and renderer.keep_static and _native_bar(widget, length, thick, pad):
        glow = _glow_shape(widget, min(1.0, strength), unit) if strength > 0 else None
        if glow is None or max(glow[4], glow[5]) <= NATIVE_SIDE:
            shape = (length, thick, pad, *_bar_core(widget, thick, upright), upright, reverse,
                     widget["segments"], BAR_HALO * unit)  # fmt: skip
            base = _bar_base(renderer, widget, shape, glow)
            size = (length + 2 * pad, thick + 2 * pad)

            def picture() -> Image.Image:
                return _bar_canvas(renderer, widget, lit, paint, pad, unit)

            layer = scenes.Lazy(size[::-1] if upright else size, "RGBA", picture)
            light = None
            if lit and glow is not None:
                ax, ay, aw, ah = glow[2:6]

                def made() -> Image.Image:  # (a glow of nothing: black, which adds nothing)
                    piece = _glow_piece(renderer, widget, frac, paint, lit, min(1.0, strength))
                    return Image.new("RGBA", (aw, ah)) if piece is None else piece.layer

                light = Piece(scenes.Lazy((aw, ah), "RGBA", made), ax, ay)
            spans = tuple((a, b) for a, b, _amount in lit)
            bar = scenes.Bar(layer, None if light is None else light.layer, base, spans, paint)
            return Piece(layer, x - pad, y - pad, [x, y, w, h], light=light, native=bar)
    canvas = _bar_canvas(renderer, widget, lit, paint, pad, unit)
    glow_piece = None
    if lit and strength > 0:
        glow_piece = _glow_piece(renderer, widget, frac, paint, lit, min(1.0, strength))
    return Piece(canvas, x - pad, y - pad, [x, y, w, h], light=glow_piece)


class LitBar(Finish):
    name = "lit"
    types = ("bar",)

    def draw(self, renderer, widget, snapshot, now):
        length, thick, _upright_, _reverse = _layout(widget)
        if thick < 2 or length < 2:
            return renderer._draw_bar(widget, snapshot, now)  # too thin for light
        value = renderer._eased(widget, _number(snapshot.value(widget["sensor"])), now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        frac = _steps(frac, length)
        paint = renderer._rule_paint(widget, value, now)
        plain = {**widget, "glow": 0.0}  # its glow is drawn with it
        native = renderer.native_bars  # (its pictures made later, if at all: here)
        return renderer._cached(
            plain, ("lit", frac, paint, native),
            lambda: bar_piece(renderer, widget, frac, paint, native), inline=native,
        )  # fmt: skip


# -- rings --------------------------------------------------------------------


def _groove_ring(renderer: Any, widget: dict[str, Any]) -> Image.Image | None:
    """A ring's groove, milled into the panel in its ``background``
    (render.facets), with its ticks flat on it; the gauge's size."""
    track = renderer.color(widget["background"])
    if not track and widget["ticks"] <= 0:
        return None
    out = Image.new("RGBA", (widget["w"], widget["h"]), (0, 0, 0, 0))
    if track:
        w, h, thickness = widget["w"], widget["h"], widget["thickness"]
        start, end = widget["start_angle"], widget["end_angle"]
        out.paste(facets.groove_ring(w, h, thickness, start, end, track))
    if widget["ticks"] > 0:
        ticks = {**widget, "background": None,
                 "tick_color": widget["tick_color"] or widget["background"]}  # fmt: skip
        out.alpha_composite(renderer._gauge_static(ticks))
    return out


def _round(widget: dict[str, Any]) -> bool:
    """Whether a gauge's arc is worked out (:func:`_cover`): round, turning clockwise."""
    return widget["w"] == widget["h"] and widget["end_angle"] > widget["start_angle"]


def _field(widget: dict[str, Any], room: int) -> tuple[np.ndarray, np.ndarray]:
    """Of each pixel of a round gauge's canvas (``room`` px around it): how far its middle is
    from the gauge's (px), and at what angle from the gauge's start, clockwise (0 to 2 pi).
    Kept: a value's arc is cut from them."""

    def make() -> tuple[np.ndarray, np.ndarray]:
        side = widget["w"] + 2 * room
        at = np.arange(side, dtype=np.float32) + np.float32(0.5 - side / 2)
        dx, dy = at[None, :], at[:, None]
        far = np.sqrt(dx * dx + dy * dy)
        angle = np.arctan2(dy, dx) - np.float32(math.radians(widget["start_angle"]))
        return far, angle % np.float32(2 * math.pi)

    return _PICTURES.make(("ring-field", widget["w"], widget["start_angle"], room), make)


def _cover(widget: dict[str, Any], frac: float, stroke: int, room: int,
           box: tuple[int, int, int, int]) -> np.ndarray:  # fmt: skip
    """How much of each pixel of ``box`` (x0, y0, x1, y1) of a round gauge's canvas the arc of
    ``frac`` covers, ``stroke`` px wide on the middle of its groove, its ends cut along the
    radius: worked out from each pixel's distance to its edges (a pixel an edge runs through
    the middle of is half covered). As finely as a drawing four times as large made smaller,
    without its steps along the curve, and in a fraction of the time."""
    far, angle = _field(widget, room)
    x0, y0, x1, y1 = box
    far, angle = far[y0:y1, x0:x1], angle[y0:y1, x0:x1]
    middle = np.float32((widget["w"] - widget["thickness"]) / 2)  # (the groove's, exactly)
    cover = np.clip(np.float32(stroke / 2 + 0.5) - np.abs(far - middle), 0, 1)
    sweep = math.radians((widget["end_angle"] - widget["start_angle"]) * frac)
    if sweep >= 2 * math.pi:
        return cover
    sweep = np.float32(sweep)
    within = np.minimum(angle, sweep - angle)  # (in the arc: how far from its nearer end)
    beyond = -np.minimum(angle - sweep, np.float32(2 * math.pi) - angle)
    ends = np.where(angle <= sweep, within, beyond)  # in radians: times the distance, in px
    return cover * np.clip(ends * far + np.float32(0.5), 0, 1)


def _arc(widget: dict[str, Any], frac: float, stroke: int, room: int) -> np.ndarray:
    """The arc of ``frac`` of a gauge, ``stroke`` px wide on the middle of its
    groove, with ``room`` around the gauge: coverage."""
    size = (widget["w"] + 2 * room, widget["h"] + 2 * room)
    if _round(widget):
        key = ("cover", widget["w"], widget["thickness"], widget["start_angle"],
               widget["end_angle"], frac, stroke, room)  # fmt: skip
        return _PICTURES.make(key, lambda: _cover(widget, frac, stroke, room, (0, 0, *size)))

    def make() -> np.ndarray:  # (an oval: drawn twice as large, made smaller)
        u = 2
        w, h, th = widget["w"], widget["h"], widget["thickness"]
        mask = Image.new("L", (size[0] * u, size[1] * u), 0)
        cx, cy = size[0] * u / 2, size[1] * u / 2
        rx, ry = (w - th) / 2 + stroke / 2, (h - th) / 2 + stroke / 2  # PIL draws inwards
        start, end = widget["start_angle"], widget["end_angle"]
        ImageDraw.Draw(mask).arc(
            [cx - rx * u, cy - ry * u, cx + rx * u, cy + ry * u], start,
            start + (end - start) * frac, fill=255, width=max(1, stroke * u),
        )  # fmt: skip
        return np.asarray(mask.resize(size, Image.Resampling.LANCZOS), np.float32) / 255

    key = ("arc", widget["w"], widget["h"], widget["thickness"], widget["start_angle"],
           widget["end_angle"], frac, stroke, room)  # fmt: skip
    return _PICTURES.make(key, make)


def _arc_part(widget: dict[str, Any], frac: float, stroke: int, room: int,
              box: tuple[int, int, int, int]) -> np.ndarray:  # fmt: skip
    """:func:`_arc`'s coverage in ``box`` (x0, y0, x1, y1) of its canvas alone, drawn on the
    same grid: the same pixels there."""
    if _round(widget):
        return _cover(widget, frac, stroke, room, box)
    u = 2
    w, h, th = widget["w"], widget["h"], widget["thickness"]
    x0, y0, x1, y1 = box
    mask = Image.new("L", ((x1 - x0) * u, (y1 - y0) * u), 0)
    cx, cy = (w + 2 * room) * u / 2 - x0 * u, (h + 2 * room) * u / 2 - y0 * u
    rx, ry = (w - th) / 2 + stroke / 2, (h - th) / 2 + stroke / 2
    start, end = widget["start_angle"], widget["end_angle"]
    ImageDraw.Draw(mask).arc(
        [cx - rx * u, cy - ry * u, cx + rx * u, cy + ry * u], start,
        start + (end - start) * frac, fill=255, width=max(1, stroke * u),
    )  # fmt: skip
    return np.asarray(mask.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS), np.float32) / 255


def _sweep_light(widget: dict[str, Any], stroke: int, room: int, halo: float):
    """The light of a ring's whole sweep without its colour: how opaque (L) and how far
    towards white (L) each pixel is (:func:`light`'s, which is linear in the colour)."""

    def make() -> tuple[Image.Image, Image.Image]:
        rgba = light(_arc(widget, 1.0, stroke, room), np.zeros(3, np.float32), halo)
        return Image.fromarray(rgba[..., 3]), Image.fromarray(rgba[..., 0])  # (black: white share)

    key = ("sweep-light", widget["w"], widget["h"], widget["thickness"], widget["start_angle"],
           widget["end_angle"], stroke, room, halo)  # fmt: skip
    return _PICTURES.make(key, make)


def _ring_light(widget: dict[str, Any], frac: float, colour: Any, stroke: int, room: int,
                halo: float) -> Image.Image:  # fmt: skip
    """The light of a ring's arc of ``frac`` in ``colour``, as :func:`light` makes it of
    :func:`_arc`, pixel for pixel: the light of the whole sweep (made once) up to the arc's
    tip, and around the tip, and the start of a closed ring, the light made anew in a small
    window. Made whole for every value, a gliding ring of the 12.3" took ten milliseconds
    a frame and came late (Arena's); this takes a few."""
    sector, patches = _ring_parts(widget, frac, colour, stroke, room, halo)
    out = Image.new("RGBA", sector.size, (0, 0, 0, 0))
    out.paste(_ring_sweep(widget, colour, stroke, room, halo), (0, 0), sector)
    for left, top, patch in patches:
        out.paste(patch, (left, top))
    return out


def _ring_sweep(widget: dict[str, Any], colour: Any, stroke: int, room: int,
                halo: float) -> Image.Image:  # fmt: skip
    """The light of a ring's whole sweep in ``colour`` (:func:`_sweep_light` coloured)."""
    opaque, white = _sweep_light(widget, stroke, room, halo)
    rgb = Image.composite(
        Image.new("RGB", opaque.size, (255, 255, 255)),
        Image.new("RGB", opaque.size, tuple(int(v) for v in colour[:3])), white,
    )  # fmt: skip
    sweep = rgb.convert("RGBA")
    sweep.putalpha(opaque)
    return sweep


def _ring_parts(widget: dict[str, Any], frac: float, colour: Any, stroke: int, room: int,
                halo: float) -> tuple[Image.Image, list[tuple[int, int, Image.Image]]]:  # fmt: skip
    """What the light of a ring's arc of ``frac`` is made of: where it is the whole sweep's
    (a mask of 0 and 255, the size of the ring's canvas), and the light made anew around the
    arc's tip and the start of a closed ring, laid over that in turn (left, top, picture)."""
    w, h = widget["w"], widget["h"]
    width, height = w + 2 * room, h + 2 * room
    start, end = widget["start_angle"], widget["end_angle"]
    tip = start + (end - start) * frac
    c = np.asarray(colour[:3], np.float32)
    gap = 360 - abs(end - start)
    sector = Image.new("L", (width, height), 0)
    far = width + height
    ImageDraw.Draw(sector).pieslice(
        [width / 2 - far, height / 2 - far, width / 2 + far, height / 2 + far],
        min(start, end) - gap / 2, max(tip, min(start, end) - gap / 2), fill=255,
    )  # fmt: skip
    radius = (min(w, h) - widget["thickness"]) / 2
    reach = math.ceil(stroke / 2 + 3 * halo) + 3  # how far the tip changes the light
    margin = math.ceil(3 * halo) + 4  # the coverage it is made of around that

    def window(angle: float) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        x = width / 2 + (w - widget["thickness"]) / 2 * math.cos(math.radians(angle))
        y = height / 2 + (h - widget["thickness"]) / 2 * math.sin(math.radians(angle))
        inner = (max(0, math.floor(x - reach)), max(0, math.floor(y - reach)),
                 min(width, math.ceil(x + reach)), min(height, math.ceil(y + reach)))  # fmt: skip
        outer = (max(0, inner[0] - margin), max(0, inner[1] - margin),
                 min(width, inner[2] + margin), min(height, inner[3] + margin))  # fmt: skip
        return inner, outer

    def patch(inner, outer) -> Image.Image:  # (the light worked out in the inner window only)
        x0, y0 = inner[0] - outer[0], inner[1] - outer[1]
        box = (x0, y0, x0 + inner[2] - inner[0], y0 + inner[3] - inner[1])
        local = light(_arc_part(widget, frac, stroke, room, outer), c, halo, box)
        return Image.fromarray(local, "RGBA")

    patches = []
    tip_inner, tip_outer = window(tip)
    if gap * math.pi / 180 * radius < 2 * reach + 2:  # closed: the sweep's light has no start
        inner, outer = window(start)
        apart = (tip_outer[2] <= outer[0] or outer[2] <= tip_outer[0]
                 or tip_outer[3] <= outer[1] or outer[3] <= tip_outer[1])  # fmt: skip
        if apart:  # its start the same at every value but these: kept
            key = ("ring-start", w, h, widget["thickness"], start, end, tuple(c), stroke, room)
            start_patch = _PICTURES.make(key, lambda: patch(inner, outer))
        else:
            start_patch = patch(inner, outer)
        patches.append((*inner[:2], start_patch))
    patches.append((*tip_inner[:2], patch(tip_inner, tip_outer)))
    return sector, patches


def _ring_ground(renderer: Any, widget: dict[str, Any], room: int) -> tuple[tuple, Image.Image]:
    """A ring's groove on its canvas, ``room`` px larger on every side, and what it is kept
    by: what it is made of (callers must not change it)."""
    track = renderer.color(widget["background"])
    ticks = widget["ticks"]
    tick = None
    if ticks > 0:
        tick = renderer.color(widget["tick_color"] or widget["background"] or widget["color"])
    key = ("ring-ground", widget["w"], widget["h"], widget["thickness"], widget["start_angle"],
           widget["end_angle"], track, ticks, tick, room)  # fmt: skip

    def make() -> Image.Image:
        canvas = Image.new("RGBA", (widget["w"] + 2 * room, widget["h"] + 2 * room), (0, 0, 0, 0))
        groove = _groove_ring(renderer, widget)
        if groove is not None:
            canvas.paste(groove, (room, room))
        return canvas

    return key, _PICTURES.make(key, make) if renderer.keep_static else make()


def _light_ring(canvas: Image.Image, ground: tuple[tuple, Image.Image], widget: dict[str, Any],
                frac: float, colour: tuple[int, ...], stroke: int, room: int,
                halo: float) -> None:  # fmt: skip
    """Lay the light of a ring's arc of ``frac`` in ``colour`` (RGBA) on ``canvas``, a copy of
    the groove ``ground`` (:func:`_ring_ground`): as :func:`_ring_light` laid over the groove,
    pixel for pixel. The groove with the whole sweep lit on it is kept by colour; each value
    cuts it at the arc's tip and lays the light around the tip on the groove anew, where the
    whole light of the arc was made and laid over the groove before (Arena's two rings on the
    12.3", a few milliseconds a frame while they glided)."""
    groove = ground[1]
    fade = colour[3] / 255
    lit = _ring_lit(ground, widget, colour, stroke, room, halo)
    sector, patches = _ring_parts(widget, frac, colour, stroke, room, halo)
    canvas.paste(lit, (0, 0), sector)  # (where the sector is clear, the groove stays)
    for left, top, patch in patches:
        tile = groove.crop((left, top, left + patch.width, top + patch.height))
        tile.alpha_composite(_alpha_scaled(patch, fade))
        canvas.paste(tile, (left, top))


def _ring_lit(ground: tuple[tuple, Image.Image], widget: dict[str, Any], colour: tuple[int, ...],
              stroke: int, room: int, halo: float) -> Image.Image:  # fmt: skip
    """The groove ``ground`` (:func:`_ring_ground`) with a ring's whole sweep lit in ``colour``
    (RGBA) on it, kept by colour (callers must not change it)."""
    key, groove = ground

    def make() -> Image.Image:
        lit = groove.copy()
        sweep = _ring_sweep(widget, colour, stroke, room, halo)
        lit.alpha_composite(_alpha_scaled(sweep, colour[3] / 255))
        return lit

    return _PICTURES.make(("ring-lit", key, tuple(colour), stroke, halo), make)


@dataclass(frozen=True, eq=False)
class Ring:
    """How an engine makes a lit ring of one colour at a value itself (engine/src/ring.rs), as
    :func:`_light_ring` makes it: of ``groove`` and ``lit`` (the groove with the whole sweep lit:
    :func:`_ring_lit`), both the size of ``layer``, the piece's picture (which this is of: one
    turned or faded has another), its pixels made here only if a frame is composed here."""

    layer: Image.Image
    groove: Image.Image
    lit: Image.Image
    frac: float
    # the gauge's width and groove, its start and end (degrees), the core's stroke (px), the
    # halo and the colour (RGBA)
    shape: tuple[float, float, float, float, int, float, tuple[int, int, int, int]]


def _native_ring(widget: dict[str, Any], stroke: int, room: int, halo: float) -> bool:
    """Whether an engine makes the ring as :func:`_light_ring` does: round, its arc worked out
    (_cover), the light around it not reaching its middle (where all of the sweep's edges
    meet), drawn as it is (no shadow, glow, fade, cut sides or turn over it)."""
    if not _round(widget) or widget.get("glow") or widget.get("shadow") or widget.get("clip"):
        return False
    if widget.get("rotate") or widget.get("opacity", 1.0) < 0.999:
        return False
    reach = math.ceil(stroke / 2 + 3 * halo) + 3
    return (widget["w"] - widget["thickness"]) / 2 - reach > 2 and widget["w"] + 2 * room <= 4096


def gauge_piece(renderer: Any, widget: dict[str, Any], frac: float, paint,
                native: bool | None = None) -> Piece:  # fmt: skip
    """The lit ring: its groove, and the value's arc of light on it. Where the panel's engine
    makes rings itself (``native``; None: Renderer.native_rings now), a ring of one colour is
    its :class:`Ring`, its picture made here only if asked for."""
    native = renderer.native_rings if native is None else native
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    unit = min(renderer.theme.width, renderer.theme.height) / 480
    room = math.ceil(3 * RING_HALO * unit)
    ground = _ring_ground(renderer, widget, room)
    if frac <= 0:
        return Piece(ground[1].copy(), x - room, y - room, [x, y, w, h])
    a, b = paint
    stroke = max(2, round(widget["thickness"] * RING_CORE))
    halo = RING_HALO * unit
    if b is None and renderer.keep_static:  # one colour: its lit sweep is kept
        if native and _native_ring(widget, stroke, room, halo):

            def picture() -> Image.Image:
                canvas = ground[1].copy()
                _light_ring(canvas, ground, widget, frac, a, stroke, room, halo)
                return canvas

            layer = scenes.Lazy(ground[1].size, "RGBA", picture)
            lit = _ring_lit(ground, widget, a, stroke, room, halo)
            shape = (float(w), float(widget["thickness"]), float(widget["start_angle"]),
                     float(widget["end_angle"]), stroke, float(halo), tuple(a[:4]))  # fmt: skip
            ring = Ring(layer, ground[1], lit, float(frac), shape)
            return Piece(layer, x - room, y - room, [x, y, w, h], native=ring)
        canvas = ground[1].copy()
        _light_ring(canvas, ground, widget, frac, a, stroke, room, halo)
        return Piece(canvas, x - room, y - room, [x, y, w, h])
    canvas = ground[1].copy()
    colour = a if b is None else mix(b[:3], a[:3], frac)  # a gradient: where the arc ends
    image = _ring_light(widget, frac, colour, stroke, room, halo)
    if a[3] < 255:
        image = _alpha_scaled(image, a[3] / 255)
    canvas.alpha_composite(image)
    return Piece(canvas, x - room, y - room, [x, y, w, h])


class LitGauge(Finish):
    name = "lit"
    types = ("gauge",)

    def draw(self, renderer, widget, snapshot, now):
        if min(widget["w"], widget["h"]) < 8 or widget["thickness"] < 2:
            return renderer._draw_gauge(widget, snapshot, now)
        value = renderer._eased(widget, _number(snapshot.value(widget["sensor"])), now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        sweep = abs(widget["end_angle"] - widget["start_angle"])
        frac = _steps(frac, math.radians(sweep) * max(widget["w"], widget["h"]) / 2)
        paint = renderer._rule_paint(widget, value, now)
        native = renderer.native_rings  # (its picture made later, if at all: here)
        return renderer._cached(
            widget, ("lit", frac, paint, native),
            lambda: gauge_piece(renderer, widget, frac, paint, native), inline=native,
        )  # fmt: skip


# -- texts --------------------------------------------------------------------


def text_light(piece: Piece, radius: float, strength: float, area=None) -> Piece | None:
    """A text's glow as light (SPUR II's headings): the text in its colours,
    blurred, ``strength`` of it, to add over the text and around it; inside
    ``area`` ([x, y, w, h] on the panel) only, if given."""
    layer = piece.layer
    pad = math.ceil(3 * radius) + 2
    rgba = np.asarray(layer, np.float32)
    shown = rgba[..., :3] * (rgba[..., 3:4] / 255)  # the text on black, as SPUR II draws it
    plane = Image.new("RGB", (layer.width + 2 * pad, layer.height + 2 * pad))
    plane.paste(Image.fromarray(np.round(shown).astype(np.uint8), "RGB"), (pad, pad))
    blurred = np.asarray(plane.filter(ImageFilter.GaussianBlur(radius)), np.float32)
    rgb = np.floor(blurred * np.float32(strength))  # whole levels, as SPUR II's blend
    x, y = piece.x - pad, piece.y - pad
    if area:
        ax, ay, aw, ah = area
        x0, y0 = max(x, ax), max(y, ay)
        x1, y1 = min(x + rgb.shape[1], ax + aw), min(y + rgb.shape[0], ay + ah)
        if x1 <= x0 or y1 <= y0:
            return None
        rgb, x, y = rgb[y0 - y : y1 - y, x0 - x : x1 - x], x0, y0
    if not rgb.any():
        return None
    out = np.concatenate([rgb, rgb.max(-1, keepdims=True)], -1).astype(np.uint8)
    return Piece(Image.fromarray(out, "RGBA"), x, y)


class LitText(Finish):
    """A text's glow is light, as SPUR II's headings and labels glow: its
    colours blurred and added over the text and around it (the renderer's
    own glow lies beneath the text). ``opacity`` fades the letters, not
    their light: at 0 only the light is left, which lets a copy of a word
    glow over it (SPUR II's wordmark)."""

    name = "lit"
    types = ("text", "metric")
    KEPT_BYTES = 4 * MB  # (a heading's light on the 12.3" is some 100 kB)

    def __init__(self) -> None:
        # by text piece: the piece with light (the text piece kept with it: an id is unique only
        # while its object lives)
        self._lit = Kept("lit.texts", self.KEPT_BYTES)

    def widget(self, widget):
        # the renderer draws the letters whole and without its glow
        return {**widget, "glow": 0.0, "opacity": 1.0, "\0light": widget.get("glow", 0.0),
                "\0opacity": widget.get("opacity", 1.0)}  # fmt: skip

    def draw(self, renderer, widget, snapshot, now):
        drawn = renderer._draw_text if widget["type"] == "text" else renderer._draw_metric
        piece = drawn(widget, snapshot, now)
        strength = widget.get("\0light", 0.0)
        opacity = widget.get("\0opacity", 1.0)
        if piece is None or (strength <= 0 and opacity >= 0.999):
            return piece
        hit = self._lit.get(id(piece), valid=lambda kept: kept[0] is piece)
        if hit is not None:
            return hit[1]
        radius = max(1.0, float(widget.get("glow_radius", 10)))
        light_ = text_light(piece, radius, strength, widget.get("glow_area")) if strength else None
        lit = replace(piece, layer=_alpha_scaled(piece.layer, opacity), light=light_)
        # (its bytes: the light, and the letters where they were faded: the rest is the text's)
        own = size_of(light_) + (size_of(lit.layer) if lit.layer is not piece.layer else 0)
        self._lit.put(id(piece), (piece, lit), own)
        return lit


# -- graphs -------------------------------------------------------------------


class LitGraph(Finish):
    """The renderer draws the graph; its line is drawn here."""

    name = "lit"
    types = ("graph",)

    def widget(self, widget):
        # the glow goes into the line's halo, not around the whole graph
        return {**widget, "glow": 0.0, "\0halo": widget.get("glow", 0.0)}

    def curve(self, renderer, widget, layer, runs, color, scale):
        joint = "curve" if widget.get("\0joints", True) else None
        width = widget["line_width"] * scale
        core = (*mix((255, 255, 255), color[:3], LINE_WHITE), color[3])
        strength = widget.get("\0halo", 0.0)
        mask = None
        if strength > 0 or color[3] < 255:
            mask = Image.new("L", layer.size, 0)
            draw = ImageDraw.Draw(mask)
            for run in runs:
                draw.line(run, fill=255, width=width, joint=joint)
        if strength > 0:  # the halo lies under the line, worked out at a quarter of the size
            radius = max(0.5, widget.get("glow_radius", 10) / 4)
            _lay_halo(layer, mask, 4 * scale, radius, min(1.0, HALO * strength), color)
        if color[3] < 255:
            layer.paste(_solid(layer.size, core), (0, 0), mask.point(_scale(color[3] / 255)))
        else:  # opaque: drawn straight on, as the flat line
            draw = ImageDraw.Draw(layer)
            for run in runs:
                draw.line(run, fill=core, width=width, joint=joint)
        return True


def _lay_halo(layer: Image.Image, mask: Image.Image, q: int, radius: float, share: float,
              color: tuple[int, ...]) -> None:  # fmt: skip
    """Lay ``color`` through the soft halo of ``mask`` onto ``layer``: worked
    out at 1/``q`` of the size, and laid only where it shows. Laid over what
    is there (the fill, or nothing above the curve), as SPUR II lays it over
    its picture: where the layer is empty, the halo is the colour at its share."""
    halo = mask.reduce(q).filter(ImageFilter.GaussianBlur(radius)).point(_scale(share))
    box = halo.getbbox()
    if box is None:
        return
    x0, y0 = max(0, box[0] - 1), max(0, box[1] - 1)  # a pixel more: the scaling reaches it
    x1, y1 = min(halo.width, box[2] + 1), min(halo.height, box[3] + 1)
    left, top = x0 * q, y0 * q
    size = min(layer.width, x1 * q) - left, min(layer.height, y1 * q) - top
    big = halo.resize(size, Image.Resampling.BILINEAR,
                      box=(x0, y0, x0 + size[0] / q, y0 + size[1] / q))  # fmt: skip
    glow = Image.new("RGBA", size, (*color[:3], 0))
    glow.putalpha(big)
    layer.alpha_composite(glow, (left, top))


_SOLIDS: dict[tuple[int, ...], Image.Image] = {}
_SOLIDS_LOCK = threading.Lock()


def _solid(size: tuple[int, int], colour: tuple[int, ...]) -> Image.Image:
    """A picture all of ``colour`` (RGBA): kept per colour at the largest size
    asked for, as filling a large one costs more than laying it. Callers must
    not change it."""
    with _SOLIDS_LOCK:
        have = _SOLIDS.pop(colour, None)
        if have is None or have.width < size[0] or have.height < size[1]:
            w, h = size if have is None else (max(size[0], have.width), max(size[1], have.height))
            have = Image.new("RGBA", (w, h), colour)
        _SOLIDS[colour] = have
        while len(_SOLIDS) > 4:
            del _SOLIDS[next(iter(_SOLIDS))]
    return have if have.size == size else have.crop((0, 0, *size))


# -- weather icons -------------------------------------------------------------

# A weather icon is its weather's symbol (render.symbols), lit, standing still.
SYMBOLS = ("sun", "moon", "partly", "cloud", "fog", "rain", "snow", "storm")
WEATHER_ICONS = ("weather", *SYMBOLS)
_KINDS = {"sun": ("clear", False), "moon": ("clear", True), "partly": ("partly", False),
          "cloud": ("overcast", False), "fog": ("fog", False), "rain": ("rain", False),
          "snow": ("snow", False), "storm": ("storm", False)}  # fmt: skip


class LitIcon(Finish):
    """Weather icons only; other icons stay line icons."""

    name = "lit"
    types = ("icon",)

    def accepts(self, widget):
        return widget["icon"] in WEATHER_ICONS

    def draw(self, renderer, widget, snapshot, now):
        name = renderer._icon_name(widget, snapshot)
        size = widget["size"]
        if name not in SYMBOLS or size < MIN_ICON:
            return renderer._draw_icon(widget, snapshot, now)
        colours, pack = symbols.colours(renderer, widget), renderer.pack(widget)
        kind, night = _KINDS[name]
        key = ("symbol", name, size, tuple(sorted(colours.items())), pack)
        x, y = widget["x"], widget["y"]

        def make() -> Image.Image:
            layers, _style = symbols.layers_for(kind, night, size, "lit", colours, pack)
            return symbols.compose(layers, kind, night, None)

        def build() -> Piece:
            return Piece(_PICTURES.make(key, make), x, y)

        return renderer._cached(widget, ("lit", name, colours["cloud"], pack), build)

    def prepare(self, renderer, widget):
        """Every weather's layers at this size, so that a change of weather costs a frame no
        more than any other (a set takes a few dozen milliseconds to make)."""
        if widget["size"] < MIN_ICON:
            return
        colours, pack = symbols.colours(renderer, widget), renderer.pack(widget)
        for kind, night in _KINDS.values():
            symbols.layers_for(kind, night, widget["size"], "lit", colours, pack)


for _finish in (LitBar(), LitGauge(), LitText(), LitGraph(), LitIcon()):
    register(_finish)

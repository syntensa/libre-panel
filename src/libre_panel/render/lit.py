"""The ``"lit"`` finish: SPUR II's light on bars, rings and graphs, and its
weather symbols built of lit bodies.

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
- A weather icon is built of lit bodies (render.light3d): a glowing sun with
  rays, clouds, glass drops, a bolt.

The grooves are the bars' and rings' ``background``. A bar or ring too thin
for light, or an icon under 16 px, is drawn flat. Coloured pictures are kept
by colour and step, so a value gliding back and forth colours each step once.
"""

from __future__ import annotations

import math
import threading
from dataclasses import replace
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from libre_panel.render import light3d
from libre_panel.render.finish import Finish, register
from libre_panel.render.light import mix, role_color
from libre_panel.render.renderer import Piece, _fraction, _number, _steps

GLOW_TAIL = 0.55 * 0.34  # a bar's glow along its length, of its colour ...
GLOW_WHITE = 0.65  # ... the head runs this far towards white
GLOW_STEPS = 7  # steps of the rise: more do not show once blurred
HALO = 0.9  # a lit graph's halo at glow 1 ...
LINE_WHITE = 0.45  # ... and how far its line runs towards white
MIN_ICON = 16

_PICTURES = light3d.Kept(48 << 20)  # masks, coloured lights and symbols


def _unit(color: tuple[int, ...] | None) -> np.ndarray | None:
    return None if color is None else light3d.unit_colour(color)


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


def light(mask: np.ndarray, colour: Any, halo: float) -> np.ndarray:
    """SPUR II's light from a coverage ``mask`` (h, w; 0 to 1) in ``colour``
    (RGB 0 to 255, one or one per column): straight RGBA uint8, the core
    towards white, the halo in the colour."""
    soft = Image.fromarray((mask * 255).astype(np.uint8), "L")  # cut to whole levels, as SPUR II
    soft = np.asarray(soft.filter(ImageFilter.GaussianBlur(halo)), np.float32) / 255
    core = mask**0.7
    c = np.broadcast_to(np.asarray(colour, np.float32)[..., :3], (*mask.shape, 3))
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

    return _PICTURES.get(("pill", length, thick, room), make)


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
    """A bar's groove, lying (``length`` x ``thick``), in the track colour.
    SPUR II mills it into the panel; this is the one place that draws it."""
    key = ("bar-groove", length, thick, tuple(track))
    return _PICTURES.get(key, lambda: Image.new("RGBA", (length, thick), tuple(track)))


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
    for i in range(GLOW_STEPS):  # the rise; a later step covers the column they share
        a = math.floor(last - rise + rise * i / GLOW_STEPS)
        b = math.floor(last - rise + rise * (i + 1) / GLOW_STEPS)
        t = (i + 1) / GLOW_STEPS
        level = GLOW_TAIL + (1 - GLOW_TAIL) * t**1.4
        step = np.floor(c[a : b + 1] * level)
        source[a : b + 1] = np.floor(step + (255 - step) * (GLOW_WHITE * t**2))
    rows = slice(1, thick - 1) if thick > 2 else slice(0, thick)
    out[rows] = source[None]
    return out


def _glow_piece(renderer: Any, widget: dict[str, Any], frac: float, paint, lit,
                strength: float) -> Piece | None:  # fmt: skip
    """SPUR II's bar glow as light to add over the bar: its source blurred
    over the widget's ``glow_area`` (else around the bar), worked out at a
    fraction of the size as SPUR II's glow zones are, nothing outside it."""
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    length, thick, upright, reverse = _layout(widget)
    unit = min(renderer.theme.width, renderer.theme.height) / 480
    radius = max(1.0, float(widget.get("glow_radius", 10)))
    colours = _bar_colours(widget, length, paint, reverse)
    source = _glow_source(length, thick, lit, colours, GLOW_HEAD * unit, reverse)
    if upright:
        source = source.transpose(1, 0, 2)
    if not source.any():
        return None
    area = widget.get("glow_area") or []
    if len(area) == 4 and area[2] > 0 and area[3] > 0:
        ax, ay, aw, ah = (int(v) for v in area)
    else:
        pad = math.ceil(2.5 * radius)
        ax, ay, aw, ah = x - pad, y - pad, w + 2 * pad, h + 2 * pad
    plane = np.zeros((ah, aw, 3), np.uint8)  # the source where it lies inside the area
    x0, y0 = max(x, ax), max(y, ay)
    x1, y1 = min(x + source.shape[1], ax + aw), min(y + source.shape[0], ay + ah)
    if x1 <= x0 or y1 <= y0:
        return None
    plane[y0 - ay : y1 - ay, x0 - ax : x1 - ax] = source[y0 - y : y1 - y, x0 - x : x1 - x]
    scale = max(1, min(4, math.floor(radius / 3)))  # SPUR II: a quarter of the size
    small = Image.fromarray(plane, "RGB")
    if scale > 1:
        small = small.reduce(scale)
    glow = small.filter(ImageFilter.GaussianBlur(radius / scale))
    if scale > 1:
        glow = glow.resize((aw, ah), Image.Resampling.BILINEAR)
    rgb = np.asarray(glow, np.float32)
    if strength < 0.999:
        rgb = rgb * np.float32(strength)
    alpha = rgb.max(-1, keepdims=True)
    out = np.concatenate([rgb, alpha], -1).astype(np.uint8)
    return Piece(Image.fromarray(out, "RGBA"), ax, ay)


def bar_piece(renderer: Any, widget: dict[str, Any], frac: float, paint) -> Piece:
    """The lit bar, lying: the groove, the light of each lit span over it
    (a pill centred in the groove); its glow is light added over it."""
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    length, thick, upright, reverse = _layout(widget)
    unit = min(renderer.theme.width, renderer.theme.height) / 480
    lit, _cells = _spans(widget, length, frac, reverse)
    pad = math.ceil(BAR_ROOM * unit)  # the halo's room around the light
    canvas = Image.new("RGBA", (length + 2 * pad, thick + 2 * pad), (0, 0, 0, 0))
    track = renderer.color(widget["background"])
    if track:
        canvas.paste(_groove(renderer, widget, length, thick, track), (pad, pad))
    glow = None
    if lit:
        colours = _bar_colours(widget, length, paint, reverse)
        alpha = paint[0][3] / 255
        core = min(thick, max(BAR_CORE_MIN, round(thick * BAR_CORE)))
        offset = round((y if not upright else x) + (thick - core) / 2) - (y if not upright else x)
        for a, b, _amount in lit:
            start = round(a)
            span = max(1, round(b - a))
            mask = _pill(span, core, pad)
            columns = np.clip(np.arange(start - pad, start + span + pad), 0, length - 1)
            c = (colours[columns] if np.ndim(colours) == 2 else colours) * 255
            key = ("bar-light", span, core, pad, _key(paint[0]), _key(paint[1]),
                   start if np.ndim(colours) == 2 else 0)  # fmt: skip
            rgba = _PICTURES.get(key, lambda m=mask, c=c: light(m, c, BAR_HALO * unit))
            image = Image.fromarray(rgba, "RGBA")
            if alpha < 0.999:
                image = _alpha_scaled(image, alpha)
            canvas.alpha_composite(image, (start, offset))  # the pill's room is the pad
        strength = widget.get("glow", 0.0)
        if strength > 0:
            glow = _glow_piece(renderer, widget, frac, paint, lit, min(1.0, strength))
    if upright:  # drawn lying: its start at the top, its light from the left
        canvas = canvas.transpose(Image.Transpose.TRANSPOSE)
    return Piece(canvas, x - pad, y - pad, [x, y, w, h], light=glow)


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
        return renderer._cached(
            plain, ("lit", frac, paint), lambda: bar_piece(renderer, widget, frac, paint)
        )


# -- rings --------------------------------------------------------------------


def _groove_ring(renderer: Any, widget: dict[str, Any]) -> Image.Image | None:
    """A ring's groove (with its ticks), the gauge's size: the one place that
    draws it."""
    if not widget["background"] and widget["ticks"] <= 0:
        return None
    return renderer._gauge_static(widget)


def _arc(widget: dict[str, Any], frac: float, stroke: int, room: int) -> np.ndarray:
    """The arc of ``frac`` of a gauge, ``stroke`` px wide on the middle of its
    groove, with ``room`` around the gauge: coverage."""

    def make() -> np.ndarray:
        u = 2
        w, h, th = widget["w"], widget["h"], widget["thickness"]
        size = (w + 2 * room, h + 2 * room)
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
    return _PICTURES.get(key, make)


def gauge_piece(renderer: Any, widget: dict[str, Any], frac: float, paint) -> Piece:
    """The lit ring: its groove, and the value's arc of light on it."""
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    unit = min(renderer.theme.width, renderer.theme.height) / 480
    room = math.ceil(3 * RING_HALO * unit)
    canvas = Image.new("RGBA", (w + 2 * room, h + 2 * room), (0, 0, 0, 0))
    groove = _groove_ring(renderer, widget)
    if groove is not None:
        canvas.paste(groove, (room, room))
    if frac > 0:
        a, b = paint
        stroke = max(2, round(widget["thickness"] * RING_CORE))
        mask = _arc(widget, frac, stroke, room)
        colour = a if b is None else mix(b[:3], a[:3], frac)  # a gradient: where the arc ends
        key = ("ring-light", w, h, widget["thickness"], widget["start_angle"],
               widget["end_angle"], frac, tuple(colour), stroke, room)  # fmt: skip
        rgba = _PICTURES.get(key, lambda: light(mask, np.asarray(colour[:3], np.float32),
                                                RING_HALO * unit))  # fmt: skip
        image = Image.fromarray(rgba, "RGBA")
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
        return renderer._cached(
            widget, ("lit", frac, paint), lambda: gauge_piece(renderer, widget, frac, paint)
        )


# -- texts --------------------------------------------------------------------


def text_light(piece: Piece, radius: float, strength: float) -> Piece | None:
    """A text's glow as light (SPUR II's headings): the text in its colours,
    blurred, ``strength`` of it, to add over the text and around it."""
    layer = piece.layer
    pad = math.ceil(3 * radius) + 2
    rgba = np.asarray(layer, np.float32)
    shown = rgba[..., :3] * (rgba[..., 3:4] / 255)  # the text on black, as SPUR II draws it
    plane = Image.new("RGB", (layer.width + 2 * pad, layer.height + 2 * pad))
    plane.paste(Image.fromarray(np.round(shown).astype(np.uint8), "RGB"), (pad, pad))
    blurred = np.asarray(plane.filter(ImageFilter.GaussianBlur(radius)), np.float32)
    rgb = np.floor(blurred * np.float32(strength))  # whole levels, as SPUR II's blend
    if not rgb.any():
        return None
    out = np.concatenate([rgb, rgb.max(-1, keepdims=True)], -1).astype(np.uint8)
    return Piece(Image.fromarray(out, "RGBA"), piece.x - pad, piece.y - pad)


class LitText(Finish):
    """A text's glow is light, as SPUR II's headings and labels glow: its
    colours blurred and added over the text and around it (the renderer's
    own glow lies beneath the text)."""

    name = "lit"
    types = ("text", "metric")
    KEPT = 512

    def __init__(self) -> None:
        self._lit: dict[int, tuple[Piece, Piece]] = {}  # by text piece: the piece with light
        self._lock = threading.Lock()

    def widget(self, widget):
        return {**widget, "glow": 0.0, "\0light": widget.get("glow", 0.0)}

    def draw(self, renderer, widget, snapshot, now):
        drawn = renderer._draw_text if widget["type"] == "text" else renderer._draw_metric
        piece = drawn(widget, snapshot, now)
        strength = widget.get("\0light", 0.0)
        if piece is None or strength <= 0:
            return piece
        with self._lock:
            hit = self._lit.get(id(piece))
            if hit is not None and hit[0] is piece:
                return hit[1]
        radius = max(1.0, float(widget.get("glow_radius", 10)))
        lit = replace(piece, light=text_light(piece, radius, strength))
        with self._lock:
            self._lit[id(piece)] = (piece, lit)
            while len(self._lit) > self.KEPT:
                del self._lit[next(iter(self._lit))]
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
    out at 1/``q`` of the size, and laid only where it shows."""
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
    layer.paste(_solid(size, (*color[:3], 255)), (left, top), big)


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


# -- weather symbols ----------------------------------------------------------

# Built from lit bodies, at the places of SPUR II's weather symbols (fractions
# of the icon's size).
SYMBOLS = ("sun", "moon", "partly", "cloud", "fog", "rain", "snow", "storm")
WEATHER_ICONS = ("weather", *SYMBOLS)


def _flat(size: int, draw_it) -> Image.Image:
    """Something small drawn flat, four times larger and reduced."""
    s = 4
    image = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    draw_it(ImageDraw.Draw(image), s)
    return image.reduce(s)


def _star(cx: float, cy: float, r: float, color) -> Any:
    def draw_it(draw: ImageDraw.ImageDraw, s: int) -> None:
        x, y, q = cx * s, cy * s, r * s
        draw.polygon([(x, y - q), (x + 0.28 * q, y - 0.28 * q), (x + q, y),
                      (x + 0.28 * q, y + 0.28 * q), (x, y + q), (x - 0.28 * q, y + 0.28 * q),
                      (x - q, y), (x - 0.28 * q, y - 0.28 * q)], fill=color)  # fmt: skip

    return draw_it


def _strip(x0: float, x1: float, y: float, thick: float, color) -> Any:
    def draw_it(draw: ImageDraw.ImageDraw, s: int) -> None:
        draw.rounded_rectangle([x0 * s, y * s, x1 * s, (y + thick) * s], radius=thick * s / 2,
                               fill=color)  # fmt: skip

    return draw_it


def _flake(cx: float, cy: float, r: float, width: float, color) -> Any:
    def draw_it(draw: ImageDraw.ImageDraw, s: int) -> None:
        for turn in (0, math.pi / 3, 2 * math.pi / 3):
            dx, dy = r * s * math.cos(turn), r * s * math.sin(turn)
            draw.line([(cx * s - dx, cy * s - dy), (cx * s + dx, cy * s + dy)], fill=color,
                      width=max(1, round(width * s)))  # fmt: skip

    return draw_it


def symbol(name: str, k: int, colours: dict[str, tuple[int, ...]]) -> Image.Image:
    """Weather symbol ``name`` (an icon name of SYMBOLS) ``k`` px square, lit."""
    out = Image.new("RGBA", (k, k), (0, 0, 0, 0))
    unit = {role: _unit(color) for role, color in colours.items()}

    def put(name_: str, kind: str, role: str, cx: float, cy: float, width: float) -> None:
        part = light3d.body(name_, kind, unit[role], (k, k), (cx * k, cy * k), width * k)
        if part is not None:
            out.alpha_composite(part)

    def sun(cx: float, cy: float, r: float) -> None:
        put("rays", "sun", "sun", cx, cy, 3.9 * r)
        put("sphere", "sun", "sun", cx, cy, 2.2 * r)

    def cloud(cx: float, cy: float, s: float, role: str) -> None:
        put("cloud", "cloud", role, cx, cy, s * 1.12)  # the body's picture is a little wider

    if name == "sun":
        sun(0.5, 0.5, 0.21)
    elif name == "moon":
        cx = cy = 0.5 * k
        disc = light3d.body("sphere", "moon", unit["moon"], (k, k), (cx, cy), 2.2 * 0.21 * k)
        if disc is not None:
            out.alpha_composite(light3d.crescent(disc, cx, cy, 0.21 * k))
        for sx, sy, sr in ((0.18, 0.22, 0.032), (0.82, 0.24, 0.024), (0.76, 0.80, 0.028)):
            out.alpha_composite(_flat(k, _star(sx * k, sy * k, max(1.0, sr * k), colours["moon"])))
    elif name == "partly":
        sun(0.36, 0.36, 0.16)
        cloud(0.56, 0.58, 0.64, "cloud")
    elif name == "fog":  # bands of mist, flat as SPUR II draws them
        for i, (x0, x1) in enumerate(((0.18, 0.78), (0.26, 0.86), (0.14, 0.70), (0.30, 0.82))):
            y = round(k * (0.34 + i * 0.12))
            out.alpha_composite(_flat(k, _strip(x0 * k, x1 * k, y, 0.05 * k, colours["cloud"])))
    else:  # cloud, rain, snow, storm
        if name in ("cloud", "storm"):
            cloud(0.40, 0.38, 0.52, "dark")
        if name == "rain":
            for i in range(4):
                put("drop", "glass", "rain", 0.34 + i * 0.13 - 0.07 + 0.045, 0.70 + 0.10, 0.2)
        elif name == "snow":
            for i in range(3):
                cx, cy = (0.36 + i * 0.16) * k, 0.80 * k
                out.alpha_composite(_flat(k, _flake(cx, cy, 0.05 * k, 0.022 * k, colours["snow"])))
        cloud(0.54, 0.46, 0.70, "dark" if name == "storm" else "cloud")
        if name == "storm":
            put("bolt", "bolt", "bolt", 0.54, 0.79, 0.4)
    return out


class LitIcon(Finish):
    """Weather icons only; other icons stay line icons."""

    name = "lit"
    types = ("icon",)

    def accepts(self, widget):
        return widget["icon"] in WEATHER_ICONS

    def draw(self, renderer, widget, snapshot, now):
        name = renderer._icon_name(widget, snapshot)
        size = widget["size"]
        if name not in SYMBOLS or size < MIN_ICON or light3d.WEATHER_BODIES.get() is None:
            return renderer._draw_icon(widget, snapshot, now)
        colours = self._colours(renderer, widget)
        key = ("symbol", name, size, tuple(sorted(colours.items())))
        x, y = widget["x"], widget["y"]

        def build() -> Piece:
            return Piece(_PICTURES.get(key, lambda: symbol(name, size, colours)), x, y)

        return renderer._cached(widget, ("lit", name, colours["cloud"]), build)

    @staticmethod
    def _colours(renderer, widget) -> dict[str, tuple[int, ...]]:
        """Clouds and the moon in the icon's colour; the sun, rain, snow and
        bolts in the theme's colours for them."""
        own = renderer.color(widget["color"]) or (255, 255, 255, 255)
        theme = renderer.theme
        dark = (*mix(own[:3], role_color(theme, "ground"), 0.55), own[3])
        warm = (*role_color(theme, "warning"), 255)
        return {
            "cloud": own, "moon": own, "dark": dark, "sun": warm, "bolt": warm,
            "rain": (*role_color(theme, "info"), 255), "snow": (*role_color(theme, "text"), 255),
        }  # fmt: skip

    def prepare(self, renderer, widget):
        light3d.WEATHER_BODIES.get()


for _finish in (LitBar(), LitGauge(), LitText(), LitGraph(), LitIcon()):
    register(_finish)

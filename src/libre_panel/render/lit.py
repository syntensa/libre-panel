"""The ``"lit"`` finish: SPUR II's 3D light on rings, bars, graphs and weather symbols.

- A bar is a tube with round ends in its groove (render.light3d), coloured
  with its colour (or gradient, or a threshold's colour); segments are
  tubes of their own. Its glow is SPUR II's bar glow: brightest at the head
  and fading back along the bar, so a full bar reads as light, not as a
  block that glows.
- A ring gauge is the same tube bent round, from ``start_angle`` to
  ``end_angle``; its glow lies around the arc as its light.
- A graph keeps its fill; its line is light (its core runs towards white)
  in a soft halo of its colour.
- A weather icon is built of lit bodies: a glowing sun with rays, clouds,
  glass drops, a bolt.

``glow`` and ``glow_radius`` set those glows (SPUR II's glow zones) in place of
the plain halo of the flat finish. Sizes come from the widget: a bar thinner
than 6 px, a tube under 4 px or an icon under 16 px is drawn flat, where
depth does not read. Coloured pictures are kept by colour and step, so a
value gliding back and forth colours each step once.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from libre_panel.render import light3d
from libre_panel.render.finish import Finish, register
from libre_panel.render.light import mix, role_color
from libre_panel.render.renderer import Piece, _blur, _fraction, _number, _steps

GLOW_TAIL = 0.55 * 0.34  # a bar's glow along its length, of its colour ...
GLOW_HEAD = 2.5  # ... rising over this many thicknesses to the head
GLOW_WHITE = 0.65  # the head runs this far towards white
GLOW_STEPS = 7  # steps of the rise: more do not show once blurred
RING_GLOW = 0.8  # a ring's light around its arc, at glow 1
HALO = 0.9  # a lit graph's halo at glow 1 ...
LINE_WHITE = 0.45  # ... and how far its line runs towards white
MIN_ICON = 16

_RINGS = light3d.Kept(24 << 20)  # ring geometries, by size and arc
_PICTURES = light3d.Kept(48 << 20)  # coloured grooves, values and symbols


def _unit(color: tuple[int, ...] | None) -> np.ndarray | None:
    return None if color is None else light3d.unit_colour(color)


def _key(color: tuple[int, ...] | None) -> tuple[int, ...] | None:
    return None if color is None else tuple(color)


def _over(under: Image.Image | None, top: Image.Image) -> Image.Image:
    if under is None:
        return top
    out = under.copy()
    out.alpha_composite(top)
    return out


def _alpha_scaled(image: Image.Image, factor: float) -> Image.Image:
    if factor >= 0.999:
        return image
    out = image.copy()
    out.putalpha(image.getchannel("A").point(lambda a: round(a * factor)))
    return out


# -- bars ---------------------------------------------------------------------


def _bar_tables() -> dict[str, np.ndarray] | None:
    return light3d.BAR.get()


def _tube(thick: int) -> light3d.Bar:
    tables = _bar_tables()
    return _PICTURES.get(("tube", thick), lambda: light3d.Bar(thick, tables))


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
    """The colour of every column (length, 3), or one colour (3). A gradient
    spans the whole track, so a colour always means the same value."""
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


def _groove_bar(widget: dict[str, Any], track: tuple[int, ...]) -> np.ndarray:
    """The groove of a bar, lying: premultiplied RGBA, 0 to 1 (thick, length, 4)."""
    length, thick, _upright_, reverse = _layout(widget)
    key = ("bar-groove", length, thick, reverse, widget["segments"], widget["segment_gap"],
           tuple(track))  # fmt: skip

    def make() -> np.ndarray:
        _lit, cells = _spans(widget, length, 1.0, reverse)
        rgba = _tube(thick).image(length, [(a, b, 1.0) for a, b in cells], _unit(track), True)
        return _premultiplied(rgba, track[3] / 255)

    return _PICTURES.get(key, make)


def _premultiplied(rgba: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    out = rgba.astype(np.float32) / 255
    out[..., 3] *= alpha
    out[..., :3] *= out[..., 3:4]
    return out


def _gauss(sigma: float) -> np.ndarray:
    """A Gaussian kernel of standard deviation ``sigma`` (as PIL's GaussianBlur)."""
    n = max(1, math.ceil(3 * sigma))
    x = np.arange(-n, n + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    return kernel / kernel.sum()


def _blurred(values: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    n = (len(kernel) - 1) // 2
    return np.convolve(values, kernel, mode="full")[n : n + len(values)]


def _bar_glow(length: int, thick: int, spans, colours: np.ndarray, pad: int, radius: float,
              strength: float, reverse: bool = False) -> np.ndarray:  # fmt: skip
    """SPUR II's bar glow, lying, as light (thick + 2 pad, length + 2 pad, 3; 0 to 1):
    dim along the lit part, rising to the head, the head running towards white.
    Its source is a band across the bar times a level along it, so its blur
    is the blur along times the blur across: two short convolutions."""
    if reverse:  # filling from the far end, its head is on the left: worked out mirrored
        spans = [(length - b, length - a, amount) for a, b, amount in spans]
        colours = colours[::-1] if np.ndim(colours) == 2 else colours
        return _bar_glow(length, thick, spans, colours, pad, radius, strength)[:, ::-1]
    start = min(a for a, _b, _amount in spans)
    head = max(b for _a, b, _amount in spans)
    rise = max(2.0, min(head - start, GLOW_HEAD * thick))
    xs = np.arange(length + 2 * pad, dtype=np.float32) + 0.5 - pad  # along the bar
    t = np.ceil(np.clip((xs - (head - rise)) / rise, 0, 1) * GLOW_STEPS) / GLOW_STEPS  # in steps
    level = np.where(xs >= head - rise, GLOW_TAIL + (1 - GLOW_TAIL) * t**1.4, GLOW_TAIL)
    level = np.where((xs >= start) & (xs <= head), level, 0.0)
    white = np.where(level > 0, GLOW_WHITE * t**2, 0.0)
    c = np.asarray(colours, np.float32)
    if c.ndim == 2:  # a colour per column
        c = c[np.clip(xs.astype(np.intp), 0, length - 1)]
    colour = c * level[:, None]
    colour = colour + (1 - colour) * white[:, None]
    kernel = _gauss(radius)
    along = np.stack([_blurred(colour[:, i], kernel) for i in range(3)], -1)
    rows = np.arange(thick + 2 * pad, dtype=np.float32) + 0.5 - pad
    across = _blurred(((rows >= 1) & (rows <= thick - 1)).astype(np.float32), kernel)
    return across[:, None, None] * along[None, :, :] * strength


def bar_piece(renderer: Any, widget: dict[str, Any], frac: float, paint) -> Piece:
    """The lit bar, composed in premultiplied light: groove, tube, then its
    glow added (light over the tube adds up, beside it shows as a glow)."""
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    length, thick, upright, reverse = _layout(widget)
    track = renderer.color(widget["background"])
    layer = _groove_bar(widget, track) if track else np.zeros((thick, length, 4), np.float32)
    lit, _cells = _spans(widget, length, frac, reverse)
    glow = widget.get("glow", 0.0)
    pad = 0
    if lit:
        colours = _bar_colours(widget, length, paint, reverse)
        key = ("bar", length, thick, tuple(lit), _key(paint[0]), _key(paint[1]))
        value = _PICTURES.get(
            key, lambda: _premultiplied(_tube(thick).image(length, lit, colours), paint[0][3] / 255)
        )
        layer = value + layer * (1 - value[..., 3:4])
        if glow > 0:
            radius = max(1.0, float(widget.get("glow_radius", 10)))
            pad = math.ceil(2 * radius)
            light = _bar_glow(length, thick, lit, colours, pad, radius, glow, reverse)
            grown = np.zeros((thick + 2 * pad, length + 2 * pad, 4), np.float32)
            grown[pad : pad + thick, pad : pad + length] = layer
            grown[..., :3] += light
            grown[..., 3] += np.maximum(np.maximum(light[..., 0], light[..., 1]), light[..., 2])
            layer = np.minimum(grown, 1.0)
    alpha = layer[..., 3:4]
    rgb = layer[..., :3] / np.maximum(alpha, 1e-4)
    out = np.concatenate([rgb, alpha], -1) * 255 + 0.5
    out = np.clip(out, 0, 255).astype(np.uint8)
    if upright:  # drawn lying: its start at the top, its light from the left
        out = np.ascontiguousarray(out.swapaxes(0, 1))
    return Piece(Image.fromarray(out, "RGBA"), x - pad, y - pad, [x, y, w, h])


class LitBar(Finish):
    name = "lit"
    types = ("bar",)

    def draw(self, renderer, widget, snapshot, now):
        length, thick, _upright_, _reverse = _layout(widget)
        if thick < light3d.MIN_BAR or length < 2 or _bar_tables() is None:
            return renderer._draw_bar(widget, snapshot, now)  # too thin to look lit
        value = renderer._eased(widget, _number(snapshot.value(widget["sensor"])), now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        frac = _steps(frac, length)
        paint = renderer._rule_paint(widget, value, now)
        plain = {**widget, "glow": 0.0}  # its glow is drawn with it
        return renderer._cached(
            plain, ("lit", frac, paint), lambda: bar_piece(renderer, widget, frac, paint)
        )

    def prepare(self, renderer, widget):
        length, thick, _upright_, _reverse = _layout(widget)
        if thick < light3d.MIN_BAR or _bar_tables() is None:
            return
        _tube(thick)
        track = renderer.color(widget["background"])
        if track:
            _groove_bar(widget, track)


# -- rings --------------------------------------------------------------------


def _ring(widget: dict[str, Any]) -> tuple[light3d.Ring, tuple[Any, ...]]:
    """The widget's ring (shared by every widget of that size and arc) and its key."""
    key = (widget["w"], widget["h"], widget["thickness"], widget["start_angle"],
           widget["end_angle"], widget["cap"] == "round")  # fmt: skip
    tables = light3d.RING.get()
    return _RINGS.get(key, lambda: light3d.Ring(*key, tables)), key


def _groove_ring(widget: dict[str, Any], track: tuple[int, ...]) -> Image.Image:
    ring, key = _ring(widget)

    def make() -> Image.Image:
        groove = ring.image(1.0, (_unit(track), None), groove=True)
        return _alpha_scaled(groove, track[3] / 255)

    return _PICTURES.get(("ring-groove", key, tuple(track)), make)


def gauge_piece(renderer: Any, widget: dict[str, Any], frac: float, paint) -> Piece:
    x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
    track = renderer.color(widget["background"])
    layer = _groove_ring(widget, track) if track else None
    if widget["ticks"] > 0:  # the scale's ticks, flat, on the groove
        ticks = renderer._gauge_static({**widget, "background": None})
        layer = _over(layer, ticks)
    pad = 0
    if frac > 0:
        ring, key = _ring(widget)
        a, b = paint

        def make() -> Image.Image:
            return _alpha_scaled(ring.image(frac, (_unit(a), _unit(b))), a[3] / 255)

        value = _PICTURES.get(("ring", key, frac, _key(a), _key(b)), make)
        glow = widget.get("glow", 0.0)
        if glow > 0:  # its light around the arc (under it: the tube covers its own)
            radius = max(1.0, float(widget.get("glow_radius", 10)))
            pad = math.ceil(2 * radius)
            base = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
            base.paste(value, (pad, pad))
            if b is None:  # the arc's own colour, as SPUR II's ring light
                tint = Image.new("RGBA", base.size, (*a[:3], 0))
                tint.putalpha(base.getchannel("A"))
                base = tint
            halo = _blur(base, radius)
            halo.putalpha(
                halo.getchannel("A").point(lambda v: min(255, round(v * RING_GLOW * glow)))
            )
            if layer is not None:
                grown = Image.new("RGBA", halo.size, (0, 0, 0, 0))
                grown.paste(layer, (pad, pad))
                grown.alpha_composite(halo)
                layer = grown
            else:
                layer = halo
            layer.alpha_composite(value, (pad, pad))
        else:
            layer = _over(layer, value)
    if layer is None:
        layer = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    return Piece(layer, x - pad, y - pad, [x, y, w, h])


class LitGauge(Finish):
    name = "lit"
    types = ("gauge",)

    def draw(self, renderer, widget, snapshot, now):
        if (
            min(widget["w"], widget["h"]) < 8
            or widget["thickness"] < light3d.MIN_RING
            or (light3d.RING.get() is None)
        ):
            return renderer._draw_gauge(widget, snapshot, now)
        value = renderer._eased(widget, _number(snapshot.value(widget["sensor"])), now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        sweep = abs(widget["end_angle"] - widget["start_angle"])
        frac = _steps(frac, math.radians(sweep) * max(widget["w"], widget["h"]) / 2)
        paint = renderer._rule_paint(widget, value, now)
        plain = {**widget, "glow": 0.0}
        return renderer._cached(
            plain, ("lit", frac, paint), lambda: gauge_piece(renderer, widget, frac, paint)
        )

    def prepare(self, renderer, widget):
        if widget["thickness"] < light3d.MIN_RING or light3d.RING.get() is None:
            return
        _ring(widget)
        track = renderer.color(widget["background"])
        if track:
            _groove_ring(widget, track)


# -- graphs -------------------------------------------------------------------


class LitGraph(Finish):
    """The renderer draws the graph; its line is drawn here."""

    name = "lit"
    types = ("graph",)

    def widget(self, widget):
        # the glow goes into the line's halo, not around the whole graph
        return {**widget, "glow": 0.0, "\0halo": widget.get("glow", 0.0)}

    def curve(self, renderer, widget, layer, runs, color, scale):
        mask = Image.new("L", layer.size, 0)
        draw = ImageDraw.Draw(mask)
        joint = "curve" if widget.get("\0joints", True) else None
        for run in runs:
            draw.line(run, fill=255, width=widget["line_width"] * scale, joint=joint)
        strength = widget.get("\0halo", 0.0)
        if strength > 0:  # the halo lies under the line, worked out at a quarter of the size
            q = 4 * scale
            w, h = layer.size
            small = mask.resize((max(1, w // q), max(1, h // q)), Image.Resampling.BOX)
            radius = max(0.5, widget.get("glow_radius", 10) / 4)
            share = min(1.0, HALO * strength)
            halo = small.filter(ImageFilter.GaussianBlur(radius)).point(
                lambda v: min(255, round(v * share))
            )
            halo = halo.resize(layer.size, Image.Resampling.BILINEAR)
            layer.paste(Image.new("RGBA", layer.size, (*color[:3], 255)), (0, 0), halo)
        core = mix((255, 255, 255), color[:3], LINE_WHITE)
        line = Image.new("RGBA", layer.size, (*core, color[3]))
        if color[3] < 255:
            mask = mask.point(lambda v: v * color[3] // 255)
        layer.paste(line, (0, 0), mask)
        return True


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
    elif name == "fog":
        thick = round(0.05 * k)
        for i, (x0, x1) in enumerate(((0.18, 0.78), (0.26, 0.86), (0.14, 0.70), (0.30, 0.82))):
            y = round(k * (0.34 + i * 0.12))
            if thick >= light3d.MIN_BAR and _bar_tables() is not None:
                lying = _tube(thick).image(k, [(x0 * k, x1 * k, 1.0)], unit["cloud"])
                out.alpha_composite(Image.fromarray(lying, "RGBA"), (0, y))
            else:  # too thin for depth
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


for _finish in (LitBar(), LitGauge(), LitGraph(), LitIcon()):
    register(_finish)

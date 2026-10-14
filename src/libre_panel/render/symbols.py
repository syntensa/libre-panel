"""Weather symbols that move, after the weather card of SPUR II's Studio screen: the
sun's rays turn, rain and snow fall, clouds drift and breathe, stars twinkle, fog sways
and a bolt flashes every few seconds.

They come in two looks (:data:`PACKS`, ``symbols`` in config.toml, a widget's ``pack``).
The first, "classic", is the one above. In the second, "vivid", the sun glows in a corona
that breathes, a bolt lights its cloud from within, snow falls as crystals that turn, fog
drifts in soft bands, the lit bodies keep every pixel Blender made and the moon is the
moon of ``render.moon``; a big one (:data:`SMALL` px and more) has more still: the rays
pulse as they turn, rain falls near and far and splashes where it lands, crystals sink
near and far, a storm pours, and on a clear night a shooting star crosses now and then.

A symbol is a set of layers, drawn once per kind of weather, size and colours and kept:
lit from the Blender bodies of ``render.light3d`` (the ``lit`` finish) or flat. A frame
only moves, turns and fades them over each other. The time is the planned time of the
frame, never the clock, in steps of 1/25 s: the picture of a step is kept, so a panel at
50 frames a second draws every second frame for free.

Hourly symbols are small and get a quicker tempo (the paths are the same share of the
size, but a third of the pixels); each gets its own time shift (``offset``), or six
clouds would drift in step and look like a machine.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from libre_panel.config import DEFAULT_SYMBOLS, SYMBOL_PACKS
from libre_panel.render.finish import Finish, register
from libre_panel.render.light import mix, role_color
from libre_panel.render.renderer import Piece

PACKS = SYMBOL_PACKS  # the looks: the first, and the second with more to it
KINDS = ("clear", "mostly", "partly", "overcast", "fog", "drizzle", "rain", "snow", "storm")
STEP = 25.0  # how often the picture changes, per second
QUICK = 1.8  # the tempo of a symbol under SMALL px, as a multiple of a big one's
SMALL = 120
TURN = 7.0  # degrees a second the sun's rays turn
BUDGET = 40 << 20  # bytes of kept layer sets

# WMO weather code -> kind (Open-Meteo); a code that is not here is overcast
_CODES = {
    0: "clear", 1: "mostly", 2: "partly", 3: "overcast", 45: "fog", 48: "fog",
    **dict.fromkeys((51, 53, 55, 56, 57), "drizzle"),
    **dict.fromkeys((61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    **dict.fromkeys((71, 73, 75, 77, 85, 86), "snow"),
    **dict.fromkeys((95, 96, 99), "storm"),
}  # fmt: skip

# Where the sun (or the moon) and the front cloud are, as shares of the picture: x, y, size
_SKY = {"clear": ((0.50, 0.50, 0.21), None),
        "mostly": ((0.40, 0.40, 0.17), (0.62, 0.62, 0.46)),
        "partly": ((0.36, 0.36, 0.16), (0.56, 0.58, 0.64))}  # fmt: skip
_STARS_BIG = ((0.32, 0.10, 0.018), (0.62, 0.08, 0.020), (0.90, 0.54, 0.018), (0.08, 0.50, 0.016))
_STARS = {"clear": ((0.18, 0.22, 0.032), (0.82, 0.24, 0.024), (0.76, 0.80, 0.028)),
          "mostly": ((0.80, 0.16, 0.026), (0.14, 0.70, 0.022)),
          "partly": ((0.80, 0.16, 0.026), (0.14, 0.70, 0.022))}  # fmt: skip
_BARS = ((0.18, 0.78), (0.26, 0.86), (0.14, 0.70), (0.30, 0.82))  # fog: from, to
_BAR_BIG = (0.20, 0.74)  # a fifth band, under the others, on a big symbol
_PHASE = 0.21  # the night symbol's moon: a waxing crescent
_BOLT = ((0.54, 0.62), (0.42, 0.80), (0.52, 0.80), (0.46, 0.96), (0.66, 0.74), (0.55, 0.74),
         (0.62, 0.62))  # fmt: skip
_FRONT, _BACK = (0.54, 0.46, 0.70), (0.40, 0.38, 0.52)  # the clouds of rain, snow, storm


def kind_of(code: Any) -> str:
    """The kind of weather a WMO code stands for (overcast for a code that is none)."""
    try:
        return _CODES.get(int(code), "overcast")
    except (TypeError, ValueError, OverflowError):
        return "overcast"


class Part(NamedTuple):
    """A layer (premultiplied, ``RGBa``) cut to what is drawn on it, and where its corner lies
    on the picture it belongs to."""

    image: Image.Image
    x: int
    y: int


class Layers:
    """What a symbol is made of; the animation moves these about."""

    def __init__(self, k: int, rich: bool = False) -> None:
        self.k = k
        self.rich = rich  # the second look ("vivid")
        self.disc: Part | None = None  # the sun, or the moon's crescent
        self.rays: Part | None = None
        self.centre = (k / 2, k / 2)  # what the rays turn about, in picture pixels
        self.stars: list[Part] = []
        self.cloud: Part | None = None  # in front
        self.back: Part | None = None  # behind it
        self.drop: Part | None = None  # rain, or the dots of drizzle (on a picture of its own)
        self.flake: Part | None = None
        self.bolt: Part | None = None
        self.bars: list[Part] = []
        self.corona: Part | None = None  # the sun's glow (it breathes)
        self.flash: Part | None = None  # the front cloud lit by its bolt
        self.streak: Part | None = None  # a shooting star (a big clear night)
        self.splash: Part | None = None  # the ring where a drop lands (big rain)
        self.turned: OrderedDict[int, Image.Image] = OrderedDict()  # rays by angle
        self.spun: dict[int, Image.Image] = {}  # snow crystals by angle

    @property
    def size(self) -> int:
        """Bytes the layers take (the budget of the kept sets counts them)."""
        parts = [self.disc, self.rays, self.cloud, self.back, self.drop, self.flake, self.bolt,
                 self.corona, self.flash, self.streak, self.splash]  # fmt: skip
        parts += self.stars + self.bars
        own = sum(p.image.width * p.image.height * 4 for p in parts if p is not None)
        kept = list(self.turned.values()) + list(self.spun.values())
        return own + sum(i.width * i.height * 4 for i in kept)

    @property
    def big(self) -> bool:
        """The second look at :data:`SMALL` px and more: it has the most to it."""
        return self.rich and self.k >= SMALL


def _part(image: Image.Image, margin: int = 2) -> Part | None:
    """``image`` cut to its content plus a little room for shifting by fractions; None
    for a layer with nothing on it."""
    box = image.getbbox()
    if box is None:
        return None
    w, h = image.size
    left, top = max(0, box[0] - margin), max(0, box[1] - margin)
    right, bottom = min(w, box[2] + margin), min(h, box[3] + margin)
    return Part(image.crop((left, top, right, bottom)).convert("RGBa"), left, top)


# -- flat layers ---------------------------------------------------------------------------

OVER = 4  # flat layers are drawn this much larger and reduced


def _canvas(w: float, h: float) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A clear picture ``OVER`` times ``w`` x ``h`` px to draw on."""
    image = Image.new("RGBA", (max(1, round(w * OVER)), max(1, round(h * OVER))), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def _reduced(image: Image.Image) -> Image.Image:
    return image.convert("RGBa").reduce(OVER).convert("RGBA")


def _flat_cloud(draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float, fill: Any) -> None:
    draw.ellipse([cx - 0.50 * s, cy - 0.02 * s, cx - 0.08 * s, cy + 0.32 * s], fill=fill)
    draw.ellipse([cx - 0.30 * s, cy - 0.30 * s, cx + 0.16 * s, cy + 0.20 * s], fill=fill)
    draw.ellipse([cx - 0.02 * s, cy - 0.16 * s, cx + 0.44 * s, cy + 0.28 * s], fill=fill)
    draw.rounded_rectangle([cx - 0.40 * s, cy + 0.04 * s, cx + 0.38 * s, cy + 0.32 * s],
                           radius=int(0.13 * s), fill=fill)  # fmt: skip


def _flat_star(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: Any) -> None:
    a, b = 0.28 * r, 0.28 * r  # the points of the star, and the notches between them
    draw.polygon([(x, y - r), (x + a, y - b), (x + r, y), (x + a, y + b), (x, y + r),
                  (x - a, y + b), (x - r, y), (x - a, y - b)], fill=fill)  # fmt: skip


def _flake_classic(side: float, r: float, width: float, fill: Any) -> Part | None:
    """A snowflake of three crossing lines of half-length ``r``, on a picture ``side`` px."""
    image, draw = _canvas(side, side)
    mid = side * OVER / 2
    for a in (0, math.pi / 3, 2 * math.pi / 3):
        draw.line([(mid - r * OVER * math.cos(a), mid - r * OVER * math.sin(a)),
                   (mid + r * OVER * math.cos(a), mid + r * OVER * math.sin(a))],
                  fill=fill, width=max(1, int(width * OVER)))  # fmt: skip
    return _part(_reduced(image))


def _snow(layers: Layers, k: int, fill: Any) -> Part | None:
    """The snow of a look: the first's flake, or the second's crystal."""
    if layers.rich:
        return _flake(0.14 * k, 0.05 * k, 0.016 * k, fill)
    return _flake_classic(0.14 * k, 0.05 * k, 0.022 * k, fill)


def _flake(side: float, r: float, width: float, fill: Any) -> Part | None:
    """A snow crystal of six arms of length ``r``, each with two pairs of side branches, in
    the middle of a picture ``side`` px (not cut to it: it turns as it falls)."""
    image, draw = _canvas(side, side)
    mid, arm = side * OVER / 2, r * OVER
    thick, thin = max(1, int(width * OVER)), max(1, int(width * OVER * 0.7))
    for i in range(6):
        a = i * math.pi / 3
        draw.line([(mid, mid), (mid + arm * math.cos(a), mid + arm * math.sin(a))], fill=fill,
                  width=thick)  # fmt: skip
        for at, length in ((0.42, 0.34), (0.70, 0.24)):
            bx, by = mid + arm * at * math.cos(a), mid + arm * at * math.sin(a)
            for turn in (-math.pi / 3, math.pi / 3):
                draw.line([(bx, by), (bx + arm * length * math.cos(a + turn),
                                      by + arm * length * math.sin(a + turn))],
                          fill=fill, width=thin)  # fmt: skip
    dot = arm * 0.16
    draw.ellipse([mid - dot, mid - dot, mid + dot, mid + dot], fill=fill)
    reduced = _reduced(image)
    return None if reduced.getbbox() is None else Part(reduced.convert("RGBa"), 0, 0)


def _corona(k: int, cx: float, cy: float, r: float, color: tuple[int, ...]) -> Part | None:
    """The sun's glow: light that fades outwards from under its disc."""
    ys, xs = np.mgrid[0:k, 0:k].astype(np.float32) + 0.5
    d = np.hypot(xs - cx, ys - cy) / max(r, 1.0)
    alpha = 0.62 * np.clip(1.0 - (d - 0.75) / 1.9, 0, 1) ** 2.0
    out = np.zeros((k, k, 4), np.uint8)
    out[..., :3] = color[:3]
    out[..., 3] = np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8)
    return _part(Image.fromarray(out, "RGBA"))


def _splash(k: int, color: Any) -> Part | None:
    """The ring a raindrop leaves where it lands, flat, seen from the side."""
    w, h = 0.16 * k, 0.05 * k
    image, draw = _canvas(w, h)
    pad = 0.012 * k * OVER
    draw.ellipse([pad, pad, w * OVER - pad, h * OVER - pad], outline=color,
                 width=max(1, int(0.009 * k * OVER)))  # fmt: skip
    reduced = _reduced(image)
    return None if reduced.getbbox() is None else Part(reduced.convert("RGBa"), 0, 0)


def _streak(k: int, color: tuple[int, ...]) -> Part | None:
    """A shooting star: a bright head at the lower left, its tail fading up to the right."""
    length = 0.34 * k
    w, h = length, 0.5 * length
    image, draw = _canvas(w, h)
    head, tail = (0.06 * w * OVER, 0.86 * h * OVER), (0.96 * w * OVER, 0.08 * h * OVER)
    steps = 28
    for i in range(steps):
        f0, f1 = i / steps, (i + 1) / steps
        a = (tail[0] + (head[0] - tail[0]) * f0, tail[1] + (head[1] - tail[1]) * f0)
        b = (tail[0] + (head[0] - tail[0]) * f1, tail[1] + (head[1] - tail[1]) * f1)
        alpha = int(255 * f1**2)
        draw.line([a, b], fill=(*color[:3], alpha), width=max(1, int((0.4 + 1.6 * f1) * OVER)))
    dot = 0.022 * k * OVER
    draw.ellipse([head[0] - dot, head[1] - dot, head[0] + dot, head[1] + dot], fill=color)
    reduced = _reduced(image)
    return None if reduced.getbbox() is None else Part(reduced.convert("RGBa"), 0, 0)


def _brightened(part: Part, light: float = 0.78) -> Part:
    """``part`` lit from within, as a cloud by its own bolt: towards a cool white."""
    a = np.asarray(part.image.convert("RGBA"), np.float32)
    white = np.array([236.0, 242.0, 255.0], np.float32)
    a[..., :3] = a[..., :3] + (white - a[..., :3]) * light
    return Part(Image.fromarray(a.astype(np.uint8), "RGBA").convert("RGBa"), part.x, part.y)


def _soft_bar(k: int, x0: float, x1: float, y: float, color: tuple[int, ...]) -> Part | None:
    """A band of fog, soft at its edges: drawn, then blurred (on its own colour, so that the
    blur fades it to nothing, not to black)."""
    g = k * OVER
    image = Image.new("RGBA", (g, g), (*color[:3], 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([g * x0, y, g * x1, y + g * 0.055], radius=int(g * 0.0275), fill=color)
    image = image.filter(ImageFilter.GaussianBlur(g * 0.012))
    return _part(_reduced(image))


def _star_part(k: int, x: float, y: float, r: float, fill: Any) -> Part | None:
    image, draw = _canvas(k, k)
    _flat_star(draw, k * OVER * x, k * OVER * y, max(1.0, r * k) * OVER, fill)
    return _part(_reduced(image))


def _flat(kind: str, night: bool, k: int, c: dict[str, tuple[int, ...]],
          rich: bool = False) -> Layers:  # fmt: skip
    """The layers drawn flat, as the mod's flat symbols."""
    g = k * OVER
    layers = Layers(k, rich)
    if kind in _SKY:
        fx, fy, fr = _SKY[kind][0]
        cx, cy, r = g * fx, g * fy, g * fr
        if night:
            image, draw = _canvas(k, k)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c["moon"])
            draw.ellipse([cx - 0.45 * r, cy - 1.15 * r, cx + 1.55 * r, cy + 0.85 * r],
                         fill=(0, 0, 0, 0))  # fmt: skip
            layers.disc = _part(_reduced(image))
            layers.stars = [
                p for p in (_star_part(k, x, y, r_, c["moon"]) for x, y, r_ in _STARS[kind]) if p
            ]
        else:
            image, draw = _canvas(k, k)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c["sun"])
            layers.disc = _part(_reduced(image))
            image, draw = _canvas(k, k)
            for i in range(8):
                a = i * math.pi / 4
                draw.line([(cx + 1.35 * r * math.cos(a), cy + 1.35 * r * math.sin(a)),
                           (cx + 1.75 * r * math.cos(a), cy + 1.75 * r * math.sin(a))],
                          fill=c["sun"], width=max(2, int(r * 0.16)))  # fmt: skip
            layers.rays = _part(_reduced(image))
            layers.centre = (cx / OVER, cy / OVER)
        cloud = _SKY[kind][1]
        if cloud is not None:
            image, draw = _canvas(k, k)
            _flat_cloud(draw, g * cloud[0], g * cloud[1], g * cloud[2], c["cloud"])
            layers.cloud = _part(_reduced(image))
    elif kind == "fog" and rich:
        bands = list(_BARS) + ([_BAR_BIG] if layers.big else [])
        for i, (x0, x1) in enumerate(bands):
            part = _soft_bar(k, x0, x1, g * (0.30 + i * (0.48 / len(bands))), c["cloud"])
            if part is not None:
                layers.bars.append(part)
    elif kind == "fog":
        for i, (x0, x1) in enumerate(_BARS):
            image, draw = _canvas(k, k)
            y = g * (0.34 + i * 0.12)
            draw.rounded_rectangle([g * x0, y, g * x1, y + g * 0.05], radius=int(g * 0.025),
                                   fill=c["cloud"])  # fmt: skip
            part = _part(_reduced(image))
            if part is not None:
                layers.bars.append(part)
    else:
        if kind in ("overcast", "storm"):
            image, draw = _canvas(k, k)
            _flat_cloud(draw, g * _BACK[0], g * _BACK[1], g * _BACK[2], c["dark"])
            layers.back = _part(_reduced(image))
        front = c["dark"] if kind == "storm" else c["cloud"]
        image, draw = _canvas(k, k)
        _flat_cloud(draw, g * _FRONT[0], g * _FRONT[1], g * _FRONT[2], front)
        layers.cloud = _part(_reduced(image))
        if kind == "rain" or (kind == "storm" and layers.big):  # a big storm pours too
            image, draw = _canvas(0.10 * k, 0.20 * k)
            draw.line([(g * 0.07, g * 0.02), (g * 0.02, g * 0.18)], fill=c["rain"],
                      width=int(g * 0.035))  # fmt: skip
            layers.drop = _part(_reduced(image))
        elif kind == "drizzle":
            image, draw = _canvas(0.06 * k, 0.06 * k)
            draw.ellipse([g * 0.01, g * 0.01, g * 0.05, g * 0.05], fill=c["rain"])
            layers.drop = _part(_reduced(image))
        elif kind == "snow":
            layers.flake = _snow(layers, k, c["snow"])
        if kind == "storm":
            image, draw = _canvas(k, k)
            draw.polygon([(g * x, g * y) for x, y in _BOLT], fill=c["bolt"])
            layers.bolt = _part(_reduced(image))
    return layers


# -- lit layers --------------------------------------------------------------------------------


def _lit(kind: str, night: bool, k: int, c: dict[str, tuple[int, ...]],
         rich: bool = False) -> Layers | None:  # fmt: skip
    """The layers as lit bodies from Blender's renders (``render.light3d``), at the places
    of the flat ones; None while those layers are missing."""
    from libre_panel.render import light3d

    if light3d.WEATHER_BODIES.get() is None:
        return None
    unit = {role: light3d.unit_colour(color) for role, color in c.items()}
    size = (k, k)

    def body(name: str, shape: str, role: str, cx: float, cy: float, width: float,
             canvas: tuple[int, int] = size) -> Part | None:  # fmt: skip
        image = light3d.body(name, shape, unit[role], canvas, (cx, cy), width, fine=rich)
        return None if image is None else _part(image)

    layers = Layers(k, rich)
    if kind in _SKY:
        fx, fy, fr = _SKY[kind][0]
        cx, cy, r = k * fx, k * fy, k * fr
        if night and not rich:
            moon_ = light3d.body("sphere", "moon", unit["moon"], size, (cx, cy), 2.2 * r, False)
            layers.disc = None if moon_ is None else _part(light3d.crescent(moon_, cx, cy, r))
            layers.stars = [
                p for p in (_star_part(k, x, y, r_, c["moon"]) for x, y, r_ in _STARS[kind]) if p
            ]
        elif night:  # the moon itself, a waxing crescent with the earthshine on the rest
            from libre_panel.render import moon

            d = max(4, round(2.2 * r))
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.alpha_composite(moon.disc(d, _PHASE, c["moon"], None, share=1.0),
                                   (round(cx - d / 2), round(cy - d / 2)))  # fmt: skip
            layers.disc = _part(canvas)
            layers.stars = [
                p for p in (_star_part(k, x, y, r_, c["moon"]) for x, y, r_ in _STARS[kind]) if p
            ]
        else:
            layers.disc = body("sphere", "sun", "sun", cx, cy, 2.2 * r)
            layers.rays = body("rays", "sun", "sun", cx, cy, 3.9 * r)
            layers.centre = (cx, cy)
        cloud = _SKY[kind][1]
        if cloud is not None:  # the body's picture is a little wider than its cloud
            layers.cloud = body("cloud", "cloud", "cloud", cloud[0] * k, cloud[1] * k,
                                cloud[2] * k * 1.12)  # fmt: skip
        return layers
    if kind == "fog":  # bands of mist, flat as SPUR II draws them
        return _flat(kind, night, k, c)
    if kind in ("overcast", "storm"):
        layers.back = body("cloud", "cloud", "dark", _BACK[0] * k, _BACK[1] * k,
                           _BACK[2] * k * 1.12)  # fmt: skip
    layers.cloud = body("cloud", "cloud", "dark" if kind == "storm" else "cloud",
                        _FRONT[0] * k, _FRONT[1] * k, _FRONT[2] * k * 1.12)  # fmt: skip
    if kind == "rain" or (kind == "storm" and layers.big):  # a big storm pours too
        w, h = max(4, round(0.10 * k)), max(8, round(0.20 * k))
        layers.drop = body("drop", "glass", "rain", 0.045 * k, 0.10 * k, 0.2 * k, (w, h))
    elif kind == "drizzle":
        w = max(3, round(0.06 * k))
        layers.drop = body("sphere", "glass", "rain", 0.03 * k, 0.03 * k, 2.2 * 0.02 * k, (w, w))
    elif kind == "snow":  # flakes are too small for depth to read
        layers.flake = _snow(layers, k, c["snow"])
    if kind == "storm":
        layers.bolt = body("bolt", "bolt", "bolt", 0.54 * k, 0.79 * k, 0.4 * k)
    return layers


def _extras(layers: Layers, kind: str, night: bool, k: int,
            c: dict[str, tuple[int, ...]]) -> None:  # fmt: skip
    """What the second look adds in both styles: the sun's corona, the flash of a storm's
    cloud, and a big symbol's more stars, shooting star and splashes."""
    if not layers.rich:
        return
    if kind in _SKY and not night:
        fx, fy, fr = _SKY[kind][0]
        layers.corona = _corona(k, k * fx, k * fy, k * fr, c["sun"])
    if kind == "storm" and layers.cloud is not None:
        layers.flash = _brightened(layers.cloud)
    if not layers.big:
        return
    if night and kind == "clear":
        layers.stars += [
            p for p in (_star_part(k, x, y, r, c["moon"]) for x, y, r in _STARS_BIG) if p
        ]
        layers.streak = _streak(k, c["moon"])
    if layers.drop is not None and kind in ("rain", "storm"):
        layers.splash = _splash(k, c["rain"])


# -- kept sets ----------------------------------------------------------------------------------

_sets: OrderedDict[tuple[Any, ...], Layers] = OrderedDict()
_lock = threading.Lock()  # the render thread and the helper thread both ask for sets


def layers_for(kind: str, night: bool, k: int, style: str, colours: dict[str, tuple[int, ...]],
               pack: str = DEFAULT_SYMBOLS) -> tuple[Layers, str]:  # fmt: skip
    """The layers of a kind of weather at size ``k`` in a look (:data:`PACKS`), and the style
    they are in (``lit``, or ``flat`` where the lit layers are missing). Kept: one set serves
    every symbol that looks like it."""
    shade = tuple(sorted(colours.items()))
    rich = pack == "vivid"
    for how in ("lit", "flat") if style == "lit" else ("flat",):
        key = (kind, night, k, how, shade, rich)
        with _lock:
            hit = _sets.get(key)
            if hit is not None:
                _sets.move_to_end(key)
        made = hit
        if made is None:
            make = _lit if how == "lit" else _flat
            made = make(kind, night, k, colours, rich)
            if made is not None:
                _extras(made, kind, night, k, colours)
        if made is None:
            continue
        with _lock:
            _sets[key] = made
            _sets.move_to_end(key)
            total = sum(layers.size for layers in _sets.values())
            while total > BUDGET and len(_sets) > 1:
                _, gone = _sets.popitem(last=False)
                total -= gone.size
        return made, how
    raise AssertionError("flat layers are always there")


def forget() -> None:
    """Drop every kept set (tests)."""
    with _lock:
        _sets.clear()


# -- moving them ---------------------------------------------------------------------------------

_tables: dict[int, list[int]] = {}


def _fade_table(share: float) -> list[int]:
    """A table for ``Image.point`` on a premultiplied picture that scales all four bands by
    ``share``, in steps of 1/64 (``point`` with a function runs it 256 times in Python, at
    every frame)."""
    q = max(0, min(64, int(share * 64 + 0.5)))
    table = _tables.get(q)
    if table is None:
        table = _tables[q] = [(v * q) // 64 for v in range(256)] * 4
    return table


def _moved(image: Image.Image, dx: float, dy: float, stretch: float = 1.0) -> Image.Image:
    """``image`` shifted by (dx, dy) and stretched by ``stretch`` about its middle, in one
    step (two would cost double and soften the edges twice)."""
    if abs(dx) < 0.02 and abs(dy) < 0.02 and abs(stretch - 1.0) < 0.002:
        return image
    w, h = image.size
    s = 1.0 / stretch
    return image.transform(image.size, Image.Transform.AFFINE,
                           (s, 0, (1 - s) * w / 2 - dx, 0, s, (1 - s) * h / 2 - dy),
                           Image.Resampling.BILINEAR)  # fmt: skip


def _fall(p: float, rise: float = 0.15, fall: float = 0.25) -> float:
    """How much of a falling thing shows at ``p`` (0 to 1) of its way: it fades in at the
    top and out at the end."""
    return min(1.0, p / rise, (1.0 - p) / fall)


_turn_lock = threading.Lock()


def _rays_turned(layers: Layers, angle: float) -> Image.Image:
    """The rays turned by ``angle`` degrees, in steps of a quarter degree and kept for the
    last few angles: every symbol of the kind asks for its own. Eight rays repeat after
    45 degrees."""
    key = round(angle * 4)
    with _turn_lock:
        hit = layers.turned.get(key)
        if hit is not None:
            layers.turned.move_to_end(key)
            return hit
    rays = layers.rays
    assert rays is not None
    centre = (layers.centre[0] - rays.x, layers.centre[1] - rays.y)
    hit = rays.image.rotate(-key / 4, resample=Image.Resampling.BICUBIC, center=centre)
    hit = hit.convert("RGBA")
    with _turn_lock:
        layers.turned[key] = hit
        while len(layers.turned) > 16:
            layers.turned.popitem(last=False)
    return hit


def compose(layers: Layers, kind: str, night: bool, t: float | None) -> Image.Image:
    """The symbol at time ``t`` (seconds; None: standing, in its resting places) on a clear
    picture of ``layers.k`` px square."""
    k = layers.k
    out = Image.new("RGBA", (k, k), (0, 0, 0, 0))
    tau = 2 * math.pi
    moving = t is not None
    s = t or 0.0
    quick = s * (1.0 if k >= SMALL else QUICK)  # the tempo of a small symbol

    def put(part: Part | None, x: float = 0.0, y: float = 0.0, share: float = 1.0,
            stretch: float = 1.0) -> None:  # fmt: skip
        """A layer on a picture whose corner lies at (x, y) (fractions are shifted in), faded
        to ``share``, stretched about its middle."""
        if part is None or share <= 0.01:
            return
        px, py = x + part.x, y + part.y
        xi, yi = math.floor(px), math.floor(py)
        w, h = part.image.size
        if xi >= k or yi >= k or xi + w <= 0 or yi + h <= 0:
            return
        image = _moved(part.image, px - xi, py - yi, stretch)
        if share < 0.999:
            image = image.point(_fade_table(share))
        out.alpha_composite(image.convert("RGBA"), dest=(max(0, xi), max(0, yi)),
                            source=(max(0, -xi), max(0, -yi)))  # fmt: skip

    big = layers.big and moving

    # the corona breathes, the stars twinkle and the rays turn (and on a big sun pulse)
    if layers.corona is not None:
        breath = 0.5 + 0.5 * math.sin(tau * s / 4.3) if moving else 0.5
        put(layers.corona, share=0.6 + 0.4 * breath)
    if layers.rays is not None:
        angle = (round(s * STEP) / STEP * TURN) % 45.0 if moving else 0.0
        rays = _rays_turned(layers, angle)
        if big:
            rays = _moved(rays, 0.0, 0.0, 1.0 + 0.035 * math.sin(tau * s / 3.1))
        out.alpha_composite(rays, dest=(layers.rays.x, layers.rays.y))
    for i, star in enumerate(layers.stars):
        glint = 0.5 + 0.5 * math.sin(tau * (s / (1.9 + 0.7 * i) + i * 0.37)) if moving else 1.0
        put(star, share=0.25 + 0.75 * glint)
    if layers.streak is not None and moving:  # now and then a shooting star, behind the moon
        u = (s % 9.3) / 0.8
        if u < 1.0:
            put(layers.streak, k * (0.62 - 0.42 * u), k * (0.02 + 0.20 * u),
                share=math.sin(math.pi * u))  # fmt: skip
    put(layers.disc)
    for i, bar in enumerate(layers.bars):  # fog: the bands pass each other and thicken a little
        if moving:
            way = 1.0 if i % 2 == 0 else -1.0
            put(
                bar,
                way * 0.045 * k * math.sin(tau * quick / (8.0 + i) + i * 1.3),
                share=0.75 + 0.25 * (0.5 + 0.5 * math.sin(tau * quick / (6.0 + 1.7 * i) + i)),
            )
        else:
            put(bar)

    # what falls comes out from under the clouds, so it goes in before them
    drop = layers.drop
    if drop is not None and kind in ("rain", "storm"):
        if big:  # far drops first: smaller, fainter, slower
            for j in range(3):
                p = (s / 1.15 + j * 0.33 + 0.12) % 1.0
                y = k * (0.62 + 0.24 * p)
                x = k * (0.40 + j * 0.15) - 0.3125 * (y - 0.62 * k)
                put(drop, x - 0.07 * k, y - 0.02 * k, 0.5 * _fall(p), stretch=0.68)
        for i in range(4 if kind == "rain" else 3):
            if moving:
                p = (s / 0.85 + i * 0.27) % 1.0
                y = k * (0.64 + 0.26 * p)
                x = k * (0.34 + i * 0.13) - 0.3125 * (y - 0.64 * k)
                put(drop, x - 0.07 * k, y - 0.02 * k, _fall(p))
                if big and layers.splash is not None and p < 0.22:
                    # where the last drop of this lane landed, a ring opens and fades
                    q = p / 0.22
                    land = k * (0.34 + i * 0.13) - 0.3125 * (0.26 * k) - 0.01 * k
                    put(layers.splash, land - 0.08 * k, k * 0.93 - 0.025 * k,
                        share=0.75 * (1 - q), stretch=0.55 + 0.75 * q)  # fmt: skip
            else:
                put(drop, k * (0.34 + i * 0.13) - 0.07 * k, 0.72 * k - 0.02 * k)
    elif drop is not None:  # drizzle: two rows of dots (three on a big symbol)
        for i in range(4):
            for j in range(3 if big else 2):
                if moving:
                    p = (s / 1.6 + i * 0.23 + j * (0.5 if j < 2 else 0.25)) % 1.0
                    y = k * (0.66 + 0.28 * p)
                    x = k * (0.34 + i * 0.13 - 0.03 * j) - 0.1 * (y - 0.66 * k)
                    put(drop, x - 0.03 * k, y - 0.03 * k, _fall(p) * (0.6 if j == 2 else 1.0))
                else:
                    put(drop, k * (0.34 + i * 0.13 - j * 0.03) - 0.03 * k,
                        k * (0.74 + j * 0.10) - 0.03 * k)  # fmt: skip
    if layers.flake is not None:
        lanes = [(i, 1.0, 3.4, 0.0) for i in range(3)]
        if big:  # crystals further back: smaller, fainter, slower, between the near ones
            lanes += [(i + 0.5, 0.62, 4.6, 0.17) for i in range(3)]
        for lane, scale, period, shift in lanes:
            if moving:
                p = (s / period + lane * 0.33 + shift) % 1.0
                y = k * (0.60 + 0.34 * p)
                x = k * (0.36 + lane * 0.16) + 0.03 * k * math.sin(tau * (p * 1.5 + lane * 0.4))
                turn = (s * (38.0 if int(lane * 2) % 2 == 0 else -31.0) + lane * 17) % 60.0
                flake = _spun(layers, turn) if layers.rich else layers.flake
                put(flake, x - 0.07 * k, y - 0.07 * k, _fall(p) * (0.55 if scale < 1 else 1.0),
                    stretch=scale)  # fmt: skip
            elif scale == 1.0:
                put(layers.flake, k * (0.36 + lane * 0.16) - 0.07 * k, 0.80 * k - 0.07 * k)

    # Clouds: three small motions, each with its own period so that nothing runs in step:
    # they drift across (the front one further than the back one: depth), rise and sink
    # more slowly, and breathe a little (clouds swell, they do not slide).
    if moving:
        put(layers.back, 0.030 * k * math.sin(tau * quick / 17.0),
            0.008 * k * math.sin(tau * quick / 12.5 + 0.8),
            stretch=1.0 + 0.008 * math.sin(tau * quick / 15.0 + 2.1))  # fmt: skip
        front = (0.055 * k * math.sin(tau * quick / 13.0 + 1.0),
                 0.014 * k * math.sin(tau * quick / 9.5 + 2.2),
                 1.0 + 0.014 * math.sin(tau * quick / 11.0))  # fmt: skip
        put(layers.cloud, front[0], front[1], stretch=front[2])
    else:
        front = (0.0, 0.0, 1.0)
        put(layers.back)
        put(layers.cloud)
    if layers.bolt is not None:  # a flash now and then: two quick ones, then dim for a while
        beat = s % 3.7
        lit = not moving or beat < 0.07 or 0.14 < beat < 0.22
        if layers.flash is not None and moving:  # the cloud lights up from within, and fades
            glow = max((math.exp(-(beat - at) / 0.09) for at in (0.0, 0.14) if beat >= at),
                       default=0.0)  # fmt: skip
            put(layers.flash, front[0], front[1], share=glow, stretch=front[2])
        put(layers.bolt, share=1.0 if lit else 0.3)
    return out


def _spun(layers: Layers, angle: float) -> Part:
    """The snow crystal turned by ``angle`` degrees (six arms: it repeats after 60), in steps
    of three degrees, kept."""
    flake = layers.flake
    assert flake is not None
    key = int(angle // 3) % 20
    hit = layers.spun.get(key)
    if hit is None:
        hit = layers.spun[key] = flake.image.rotate(key * 3, resample=Image.Resampling.BICUBIC)
    return Part(hit, flake.x, flake.y)


# -- in a theme ------------------------------------------------------------------------------------


def night_at(snapshot: Any, sensor: str) -> bool:
    """Whether it is night for the weather read from ``sensor``: the forecast says so for
    its hours (``weather.hour.3.day``), Open-Meteo for now (``weather.is_day``), else the
    clock does (as the weather icon decides)."""
    key = sensor or "weather.code"
    daylight = snapshot.value(key[: -len(".code")] + ".day" if key.endswith(".code") else "")
    if daylight is None and key == "weather.code":
        daylight = snapshot.value("weather.is_day")
    if daylight is None:
        return not 6 <= snapshot.now.hour < 20
    return not daylight


def colours(renderer: Any, widget: dict[str, Any]) -> dict[str, tuple[int, ...]]:
    """Clouds and the moon in the widget's colour; the sun and bolts in the palette's
    warning colour, rain in its accent and snow in its text colour (as the lit weather icon)."""
    own = renderer.color(widget["color"]) or (255, 255, 255, 255)
    theme = renderer.theme
    dark = (*mix(own[:3], role_color(theme, "ground"), 0.55), own[3])
    warm = (*role_color(theme, "warning"), 255)
    return {
        "cloud": own, "moon": own, "dark": dark, "sun": warm, "bolt": warm,
        "rain": (*role_color(theme, "info"), 255), "snow": (*role_color(theme, "text"), 255),
    }  # fmt: skip


def draw(renderer: Any, widget: dict[str, Any], snapshot: Any, now: float,
         style: str = "flat") -> Piece | None:  # fmt: skip
    """A ``symbol`` widget: the weather of its code sensor, moving with the planned time of
    the frame where the panel is driven faster than once a second."""
    k = max(8, widget["size"])
    kind = kind_of(snapshot.value(widget["sensor"]))
    night = night_at(snapshot, widget["sensor"])
    layers, shown = layers_for(kind, night, k, style, colours(renderer, widget),
                               renderer.pack(widget))  # fmt: skip
    live = widget["moving"] and renderer.animate
    step = round((now + widget["offset"]) * STEP) if live else None
    if live:
        renderer.moving = True
    x, y = widget["x"], widget["y"]

    def build() -> Piece:
        return Piece(compose(layers, kind, night, None if step is None else step / STEP), x, y)

    return renderer._cached(widget, (kind, night, shown, step, id(layers)), build)


class LitSymbol(Finish):
    """The ``lit`` finish of a symbol: its bodies are lit, from Blender's renders."""

    name = "lit"
    types = ("symbol",)

    def draw(self, renderer: Any, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        return draw(renderer, widget, snapshot, now, "lit")

    def prepare(self, renderer: Any, widget: dict[str, Any]) -> None:
        """Every kind of weather at this size, so that the first change of weather costs no
        more than any other frame (a lit set takes ten milliseconds or so to make)."""
        k, shade, pack = max(8, widget["size"]), colours(renderer, widget), renderer.pack(widget)
        for kind in KINDS:
            for night in (False, True) if kind in _SKY else (False,):
                layers_for(kind, night, k, "lit", shade, pack)


register(LitSymbol())

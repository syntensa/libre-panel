"""Weather symbols that move: the sun's rays turn, rain and snow fall, clouds drift and breathe,
stars twinkle, fog drifts and a bolt flashes every few seconds.

Their shapes are Meteocons' (render.weather_art): a cloud of one clean outline, a sun with
eight round rays, a crescent moon. The ``lit`` finish makes them frosted glass lit from above
(a fill lighter at the top, a fine edge of light along the top of each outline, a sun that
glows), and a cloud milky glass: the sun or the moon behind it shows through only as its glow
does, where the cloud is at that moment, and the cloud in front casts a soft shade on the one
behind it. The ``flat`` finish gives each shape its colour.

They come in two looks (:data:`PACKS`, ``symbols`` in config.toml, a widget's ``pack``). The
first, "classic", is the one above. In the second, "vivid", the sun glows in a corona that
breathes, a bolt lights its cloud from within and snow falls as crystals that turn; a big one
(:data:`SMALL` px and more) has more still: the rays pulse as they turn, rain falls near and
far and splashes where it lands, crystals sink near and far, a storm pours, fog has a fifth
band, and on a clear night more stars shine and a shooting star crosses now and then.

A symbol is a set of layers, drawn once per kind of weather, size and colours and kept. A frame
only moves, turns and fades them over each other. The time is the planned time of the frame,
never the clock, in steps of 1/25 s: the picture of a step is kept, so a panel at 50 frames a
second draws every second frame for free.

Hourly symbols are small and get a quicker tempo (the paths are the same share of the size,
but a third of the pixels); each gets its own time shift (``offset``), or six clouds would
drift in step and look like a machine.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from libre_panel.config import DEFAULT_SYMBOLS, SYMBOL_PACKS
from libre_panel.render import weather_art as art
from libre_panel.render.finish import Finish, register
from libre_panel.render.light import mix, role_color
from libre_panel.render.renderer import Piece

PACKS = SYMBOL_PACKS  # the looks: the first, and the second with more to it
KINDS = ("clear", "mostly", "partly", "overcast", "fog", "drizzle", "rain", "snow", "storm")
STEP = 25.0  # how often the picture changes, per second
QUICK = 1.8  # the tempo of a symbol under SMALL px, as a multiple of a big one's
SMALL = 120
TURN = 7.0  # degrees a second the sun's rays turn
BUDGET = 16 << 20  # bytes of kept layer sets (a panel's weather takes well under one)

# WMO weather code -> kind (Open-Meteo); a code that is not here is overcast
_CODES = {
    0: "clear", 1: "mostly", 2: "partly", 3: "overcast", 45: "fog", 48: "fog",
    **dict.fromkeys((51, 53, 55, 56, 57), "drizzle"),
    **dict.fromkeys((61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    **dict.fromkeys((71, 73, 75, 77, 85, 86), "snow"),
    **dict.fromkeys((95, 96, 99), "storm"),
}  # fmt: skip

# Where things are, in Meteocons' units (render.weather_art.UNIT, 128 a side)
# the sun: its middle, its disc's radius, and its rays' inner and outer ends and width
_SUN = {"clear": (64, 64, 19.5, 31, 45, 6.0),
        "mostly": (46, 46, 14.5, 22.5, 32.0, 4.4),
        "partly": (41, 46, 11.0, 16.5, 24.0, 3.3)}  # fmt: skip
# the moon: its outline, and its size and shift; the front cloud's size and shift by the sun
_MOON = {"clear": ("moon", 1.0, 0, 0), "mostly": ("moon", 0.69, -18, -17),
         "partly": ("moon_small", 1.0, 0, 0)}  # fmt: skip
_SKY_CLOUD = {"mostly": (0.74, 17, 16), "partly": (0.94, 4, 6)}
_SKY = _SUN  # (the kinds with a sun or a moon)
_STARS = {"clear": ((94, 30, 3.4), (106, 54, 2.4), (28, 100, 2.6)),
          "mostly": ((98, 26, 2.8), (18, 92, 2.2)),
          "partly": ((64, 24, 2.8), (16, 72, 2.2))}  # fmt: skip
_STARS_BIG = ((18, 28, 2.0), (112, 94, 2.2), (78, 14, 1.7), (52, 114, 1.8))
# fog: its cloud raised, its bands (from, to, how dense) under it, a fifth on a big symbol
_FOG_LIFT = -6
_BANDS = ((30, 90, 0.70), (38, 100, 0.88), (26, 84, 0.62), (44, 96, 0.42))
_BAND_BIG = (34, 92, 0.36)
_BAND_TOP, _BAND_FOOT, _BAND_WIDTH = 89.0, 113.0, 4.2
# what falls: its cloud (and the storm's) raised a little over it, rain, drizzle and snow in
# lanes under it (x), from under its floor (_FALL: a drop's top at the start of its fall)
_LIFT = -4.0
_FALL = 76.0
_LANES = (52.0, 64.0, 76.0)
_FAR_LANES = (58.0, 70.0)
_SNOW_LANES = ((49.0, 89.0, 0.0), (64.0, 97.0, 18.0), (79.0, 88.0, 33.0))  # x, y at rest, turn
_RAIN = (15.0, 0.9, 3.9)  # length, width at the top and at the foot (lit)
_DRIZZLE = (5.5, 1.6, 3.6)
_RAIN_FLAT, _DRIZZLE_FLAT = (12.0, 4.0, 4.0), (3.0, 4.0, 4.0)  # Meteocons' strokes
_FLAKE = 13.0  # a snow crystal's size


def kind_of(code: Any) -> str:
    """The kind of weather a WMO code stands for (overcast for a code that is none)."""
    try:
        return _CODES.get(int(code), "overcast")
    except (TypeError, ValueError, OverflowError):
        return "overcast"


class Part(NamedTuple):
    """A layer (premultiplied, ``RGBa``) cut to what is drawn on it, and where its corner lies
    on the picture it belongs to (for what falls: off the point it is put at)."""

    image: Image.Image
    x: int
    y: int


class Glow(NamedTuple):
    """Light behind a cloud, as much of it as shows through the cloud where it covers all
    (``L``), its colour, and where its corner lies on the picture."""

    alpha: Image.Image
    colour: tuple[int, int, int]
    x: int
    y: int


class Layers:
    """What a symbol is made of; the animation moves these about."""

    def __init__(self, k: int, rich: bool = False, lit: bool = False) -> None:
        self.k = k
        self.rich = rich  # the second look ("vivid")
        self.lit = lit  # frosted glass (else flat)
        self.disc: Part | None = None  # the sun, or the moon's crescent
        self.rays: Part | None = None
        self.centre = (k / 2, k / 2)  # what the rays turn about, in picture pixels
        self.stars: list[Part] = []
        self.cloud: Part | None = None  # in front
        self.back: Part | None = None  # behind it
        self.through: Glow | None = None  # the sun's or the moon's glow, through the cloud
        self.drop: Part | None = None  # rain, or drizzle (on a picture of its own)
        self.flake: Part | None = None
        self.bolt: Part | None = None  # as it flashes
        self.bolt_rest: Part | None = None  # between flashes
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
                 self.bolt_rest, self.corona, self.flash, self.streak, self.splash]  # fmt: skip
        parts += self.stars + self.bars
        own = sum(p.image.width * p.image.height * 4 for p in parts if p is not None)
        if self.through is not None:
            own += self.through.alpha.width * self.through.alpha.height
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


def _anchored(image: Image.Image, ax: float, ay: float) -> Part | None:
    """A picture of its own (a drop, a crystal, a splash) put by the point (ax, ay) on it."""
    if image.getbbox() is None:
        return None
    return Part(image.convert("RGBa"), -round(ax), -round(ay))


# -- the layers ------------------------------------------------------------------------------


def _brightened(part: Part, light: float = 0.78) -> Part:
    """``part`` lit from within, as a cloud by its own bolt: towards a cool white."""
    a = np.asarray(part.image.convert("RGBA"), np.float32)
    white = np.array([236.0, 242.0, 255.0], np.float32)
    a[..., :3] = a[..., :3] + (white - a[..., :3]) * light
    return Part(Image.fromarray(a.astype(np.uint8), "RGBA").convert("RGBa"), part.x, part.y)


def _streak(k: int, color: tuple[int, ...]) -> Part | None:
    """A shooting star: a bright head at the lower left, its tail fading up to the right."""
    over = art.OVER
    length = 0.34 * k
    w, h = length, 0.5 * length
    image = Image.new("RGBA", (max(1, round(w * over)), max(1, round(h * over))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    head, tail = (0.06 * w * over, 0.86 * h * over), (0.96 * w * over, 0.08 * h * over)
    steps = 28
    for i in range(steps):
        f0, f1 = i / steps, (i + 1) / steps
        a = (tail[0] + (head[0] - tail[0]) * f0, tail[1] + (head[1] - tail[1]) * f0)
        b = (tail[0] + (head[0] - tail[0]) * f1, tail[1] + (head[1] - tail[1]) * f1)
        alpha = int(255 * f1**2)
        draw.line([a, b], fill=(*color[:3], alpha), width=max(1, int((0.4 + 1.6 * f1) * over)))
    dot = 0.022 * k * over
    draw.ellipse([head[0] - dot, head[1] - dot, head[0] + dot, head[1] + dot], fill=color)
    reduced = image.convert("RGBa").reduce(over)
    return None if reduced.getbbox() is None else Part(reduced, 0, 0)


def _splash(k: int, color: Any) -> Part | None:
    """The ring a raindrop leaves where it lands, flat, seen from the side (put by its middle)."""
    s = art.px(k)
    w, h = max(4, round(11 * s)), max(2, round(3.6 * s))
    over = art.OVER
    image = Image.new("L", (w * over, h * over), 0)
    pad = 0.8 * s * over
    ImageDraw.Draw(image).ellipse([pad, pad, w * over - pad, h * over - pad], outline=255,
                                  width=max(1, round(0.7 * s * over)))  # fmt: skip
    ring = art.flat(np.asarray(image.reduce(over), np.float32) / 255, color)
    return _anchored(ring, w / 2, h / 2)


def _drop(k: int, kind: str, colour: Any, lit: bool) -> Part | None:
    """Rain's drop or drizzle's, put by the middle of its top."""
    if lit:
        length, top, foot = _RAIN if kind != "drizzle" else _DRIZZLE
    else:
        length, top, foot = _RAIN_FLAT if kind != "drizzle" else _DRIZZLE_FLAT
    s = art.px(k)
    top, foot = max(top, 0.8 / s), max(foot, 2.2 / s)  # (on a small symbol: still a drop)
    image = art.drop(k, length, top, foot, colour, lit, 0.1 if kind != "drizzle" else 0.35)
    return _anchored(image, image.width / 2, (1 + foot / 2) * s)


def _flake(k: int, colour: Any, branched: bool) -> Part | None:
    """A snow crystal, put by its middle: branched (the second look, where it is big enough to
    show) or three plain strokes crossing."""
    s = art.px(k)
    size = _FLAKE * s
    side = size + 4
    fine = branched and size >= 11  # (smaller, the branches would only blur its arms)
    width = max(0.9, size * (0.10 if fine else 0.13))
    image = art.flake(side, size, width, colour, fine)
    return _anchored(image, image.width / 2, image.height / 2)


def _band_rows(count: int) -> list[float]:
    step = (_BAND_FOOT - _BAND_TOP) / max(1, count - 1)
    return [_BAND_TOP + i * step for i in range(count)]


def _through(light: Image.Image, colour: Any, k: int, strength: float) -> Glow | None:
    """What of ``light`` (behind a cloud) shows through the cloud: its glow, wide and near, as
    much of it as frosted glass lets through."""
    a = np.asarray(light.getchannel("A"), np.float32) / 255
    radius = max(1.0, k * 0.035)
    seen = 0.6 * art.blurred(a, radius) + 0.4 * art.blurred(a, max(0.6, radius * 0.28))
    alpha = Image.fromarray(np.clip(seen * strength * 255 + 0.5, 0, 255).astype(np.uint8), "L")
    box = alpha.getbbox()
    if box is None:
        return None
    rgb = tuple(int(v * 255 + 0.5) for v in art.lighter(colour, 0.25))
    return Glow(alpha.crop(box), (rgb[0], rgb[1], rgb[2]), box[0], box[1])


def _made(kind: str, night: bool, k: int, c: dict[str, tuple[int, ...]], rich: bool,
          lit: bool) -> Layers:  # fmt: skip
    """The layers of a kind of weather, as frosted glass (``lit``) or flat."""
    layers = Layers(k, rich, lit)

    def cloud(role: str, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> Part | None:
        cov = art.cover("cloud", k, scale, dx, dy)
        if not lit:
            return _part(art.flat(cov, c[role]))
        return _part(art.glass(cov, c[role], k, lift=0.45 if role == "dark" else 0.78))

    if kind in _SKY:
        if night:
            name, scale, dx, dy = _MOON[kind]
            image, _cov = art.moon(k, name, c["moon"], lit, scale, dx, dy)
            layers.disc = _part(image)
            stars = _STARS[kind] + (_STARS_BIG if rich and k >= SMALL and kind == "clear" else ())
            layers.stars = [p for p in (_part(art.star(k, x, y, r, c["moon"], lit))
                                        for x, y, r in stars) if p]  # fmt: skip
            light, colour, strength = image, c["moon"], 0.42
        else:
            cx, cy, r, inner, outer, width = _SUN[kind]
            core = art.sun_core(k, cx, cy, r, c["sun"], lit)
            layers.disc = _part(core)
            layers.rays = _part(art.rays(k, cx, cy, inner, outer, width, c["sun"], lit))
            layers.centre = (art.at(k, cx), art.at(k, cy))
            light, colour, strength = core, c["sun"], 0.78
            if rich:
                halo = art.corona(k, cx, cy, r, c["sun"])
                layers.corona = _part(halo)
                light = halo.copy()
                light.alpha_composite(core)
        if kind in _SKY_CLOUD:
            layers.cloud = cloud("cloud", *_SKY_CLOUD[kind])
            if lit and layers.cloud is not None:
                layers.through = _through(light, colour, k, strength)
        return layers
    if kind == "fog":
        layers.cloud = cloud("cloud", dy=_FOG_LIFT)
        bands = list(_BANDS) + ([_BAND_BIG] if rich and k >= SMALL else [])
        for (x0, x1, dense), y in zip(bands, _band_rows(len(bands)), strict=True):
            # (soft veils in the second look; the first's are crisp, as its fog always was)
            part = _part(art.band(k, x0, x1, y, _BAND_WIDTH, c["cloud"], dense, lit and rich))
            if part is not None:
                layers.bars.append(part)
        return layers
    if kind in ("overcast", "storm"):
        # behind a storm's dark cloud a lighter one, that the two are told apart
        back = c["dark"] if kind == "overcast" else mix(c["dark"][:3], c["cloud"][:3], 0.78)
        cov = art.cover("back", k, dy=_LIFT if kind == "storm" else 0.0)
        layers.back = _part(art.glass(cov, back, k, lift=0.42, deep=0.96) if lit
                            else art.flat(cov, back))  # fmt: skip
    layers.cloud = cloud(
        "dark" if kind == "storm" else "cloud", dy=0.0 if kind == "overcast" else _LIFT
    )
    if kind == "rain" or (kind == "storm" and rich and k >= SMALL):  # a big storm pours too
        layers.drop = _drop(k, "rain", c["rain"], lit)
    elif kind == "drizzle":
        layers.drop = _drop(k, "drizzle", c["rain"], lit)
    elif kind == "snow":
        layers.flake = _flake(k, c["snow"], rich)
    if kind == "storm":
        layers.bolt = _part(art.bolt(k, c["bolt"], lit, dy=_LIFT))
        layers.bolt_rest = _part(art.bolt(k, c["bolt"], lit, flash=False, dy=_LIFT))
        if rich and layers.cloud is not None:
            layers.flash = _brightened(layers.cloud)
    if rich and k >= SMALL and layers.drop is not None and kind in ("rain", "storm"):
        layers.splash = _splash(k, c["rain"])
    return layers


def _night_streak(layers: Layers, kind: str, night: bool, c: dict[str, tuple[int, ...]]) -> None:
    if layers.big and night and kind == "clear":
        layers.streak = _streak(layers.k, c["moon"])


# -- kept sets ----------------------------------------------------------------------------------

_sets: OrderedDict[tuple[Any, ...], Layers] = OrderedDict()
_lock = threading.Lock()  # the render thread and the helper thread both ask for sets


def layers_for(kind: str, night: bool, k: int, style: str, colours: dict[str, tuple[int, ...]],
               pack: str = DEFAULT_SYMBOLS) -> tuple[Layers, str]:  # fmt: skip
    """The layers of a kind of weather at size ``k`` in a look (:data:`PACKS`), and the style
    they are in (``lit``, else ``flat``). Kept: one set serves every symbol that looks like
    it."""
    how = "lit" if style == "lit" else "flat"
    rich = pack == "vivid"
    key = (kind, night, k, how, tuple(sorted(colours.items())), rich)
    with _lock:
        hit = _sets.get(key)
        if hit is not None:
            _sets.move_to_end(key)
            return hit, how
    made = _made(kind, night, k, colours, rich, how == "lit")
    _night_streak(made, kind, night, colours)
    with _lock:  # (a kept one is counted already: every symbol asks on every frame)
        _sets[key] = made
        total = sum(layers.size for layers in _sets.values())
        while total > BUDGET and len(_sets) > 1:
            _, gone = _sets.popitem(last=False)
            total -= gone.size
    return made, how


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


_SHADE = [v * 30 // 100 for v in range(256)]  # the front cloud's shade: 30 % at most


class _Placed(NamedTuple):
    """A layer as it lies on the picture at this step (``RGBA``), and its corner there."""

    image: Image.Image
    x: int
    y: int


def compose(layers: Layers, kind: str, night: bool, t: float | None) -> Image.Image:
    """The symbol at time ``t`` (seconds; None: standing, in its resting places) on a clear
    picture of ``layers.k`` px square."""
    k = layers.k
    u = art.px(k)  # pixels a unit

    def at(v: float) -> float:  # where v units along (or down) lies, in pixels
        return art.at(k, v)

    out = Image.new("RGBA", (k, k), (0, 0, 0, 0))
    tau = 2 * math.pi
    moving = t is not None
    s = t or 0.0
    quick = s * (1.0 if k >= SMALL else QUICK)  # the tempo of a small symbol

    def place(part: Part | None, x: float = 0.0, y: float = 0.0, share: float = 1.0,
              stretch: float = 1.0) -> _Placed | None:  # fmt: skip
        """A layer whose corner lies at (x, y) (fractions are shifted in), faded to
        ``share``, stretched about its middle; None where it is not on the picture."""
        if part is None or share <= 0.01:
            return None
        px, py = x + part.x, y + part.y
        xi, yi = math.floor(px), math.floor(py)
        w, h = part.image.size
        if xi >= k or yi >= k or xi + w <= 0 or yi + h <= 0:
            return None
        image = _moved(part.image, px - xi, py - yi, stretch)
        if share < 0.999:
            image = image.point(_fade_table(share))
        return _Placed(image.convert("RGBA"), xi, yi)

    def lay(placed: _Placed | None) -> None:
        if placed is not None:
            out.alpha_composite(placed.image, dest=(max(0, placed.x), max(0, placed.y)),
                                source=(max(0, -placed.x), max(0, -placed.y)))  # fmt: skip

    def put(part: Part | None, x: float = 0.0, y: float = 0.0, share: float = 1.0,
            stretch: float = 1.0) -> _Placed | None:  # fmt: skip
        placed = place(part, x, y, share, stretch)
        lay(placed)
        return placed

    def where(placed: _Placed, x: int, y: int, w: int, h: int) -> Image.Image:
        """How much ``placed`` covers of the box (x, y, w, h) of the picture (``L``)."""
        return placed.image.getchannel("A").crop((x - placed.x, y - placed.y,
                                                  x - placed.x + w, y - placed.y + h))  # fmt: skip

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
        p = (s % 9.3) / 0.8
        if p < 1.0:
            put(layers.streak, k * (0.62 - 0.42 * p), k * (0.02 + 0.20 * p),
                share=math.sin(math.pi * p))  # fmt: skip
    put(layers.disc)
    for i, bar in enumerate(layers.bars):  # fog: the bands pass each other and thicken a little
        if moving:
            way = 1.0 if i % 2 == 0 else -1.0
            put(
                bar,
                way * 4.0 * u * math.sin(tau * quick / (8.0 + i) + i * 1.3),
                share=0.75 + 0.25 * (0.5 + 0.5 * math.sin(tau * quick / (6.0 + 1.7 * i) + i)),
            )
        else:
            put(bar)

    # what falls comes out from under the clouds, so it goes in before them
    drop = layers.drop
    if drop is not None and kind in ("rain", "storm"):
        if big:  # far drops first: smaller, fainter, slower
            for j, x in enumerate(_FAR_LANES):
                p = (s / 1.35 + j * 0.5 + 0.3) % 1.0
                put(drop, at(x), at(_FALL + 2 + 16 * p**1.5), 0.5 * _fall(p), stretch=0.68)
        for i, x in enumerate(_LANES):
            if moving:
                p = (s / 1.0 + (0.0, 0.6, 0.2)[i]) % 1.0  # (Meteocons' lanes, a second a fall)
                put(drop, at(x), at(_FALL + 20 * p**1.5), _fall(p))
                if big and layers.splash is not None and p < 0.22:
                    # where the last drop of this lane landed, a ring opens and fades
                    q = p / 0.22
                    put(layers.splash, at(x), at(_FALL + 20 + _RAIN[0] + 1.5),
                        share=0.75 * (1 - q), stretch=0.55 + 0.75 * q)  # fmt: skip
            else:
                put(drop, at(x), at(_FALL + (5.0, 11.0, 6.0)[i]))
    elif drop is not None:  # drizzle: two drops a lane (three on a big symbol)
        for i, x in enumerate(_LANES):
            for j in range(3 if big else 2):
                if moving:
                    p = (s / 1.6 + i * 0.29 + j * (0.5 if j < 2 else 0.25)) % 1.0
                    put(drop, at(x), at(_FALL + 5 + 18 * p**1.3),
                        _fall(p) * (0.6 if j == 2 else 1.0))  # fmt: skip
                else:
                    put(drop, at(x), at(_FALL + 7 + 9 * j))
    if layers.flake is not None:
        lanes = [(x, y, turn, 1.0, 3.4, 0.0) for x, y, turn in _SNOW_LANES]
        if big:  # crystals further back: smaller, fainter, slower, between the near ones
            lanes += [(x + 7.5, y, turn, 0.62, 4.6, 0.17) for x, y, turn in _SNOW_LANES[:2]]
        for n, (x, y, turn0, scale, period, shift) in enumerate(lanes):
            if moving:
                p = (s / period + n * 0.33 + shift) % 1.0
                yy = _FALL + 6 + 26 * p
                xx = x + 3.0 * math.sin(tau * (p * 1.5 + n * 0.4))
                turn = (s * (38.0 if n % 2 == 0 else -31.0) + turn0) % 60.0
                flake = _spun(layers, turn) if layers.rich else layers.flake
                put(flake, at(xx), at(yy), _fall(p) * (0.55 if scale < 1 else 1.0),
                    stretch=scale)  # fmt: skip
            elif scale == 1.0:
                put(_spun(layers, turn0) if layers.rich else layers.flake, at(x), at(y))

    # Clouds: three small motions, each with its own period so that nothing runs in step:
    # they drift across (the front one further than the back one: depth), rise and sink
    # more slowly, and breathe a little (clouds swell, they do not slide).
    if moving:
        back = (0.030 * k * math.sin(tau * quick / 17.0),
                0.008 * k * math.sin(tau * quick / 12.5 + 0.8),
                1.0 + 0.008 * math.sin(tau * quick / 15.0 + 2.1))  # fmt: skip
        front = (0.055 * k * math.sin(tau * quick / 13.0 + 1.0),
                 0.014 * k * math.sin(tau * quick / 9.5 + 2.2),
                 1.0 + 0.014 * math.sin(tau * quick / 11.0))  # fmt: skip
    else:
        back = front = (0.0, 0.0, 1.0)
    cloud = place(layers.cloud, front[0], front[1], stretch=front[2])
    behind = put(layers.back, back[0], back[1], stretch=back[2])
    if layers.lit and behind is not None and cloud is not None:
        # the cloud in front shades the one behind it, softly, a little below and to the right
        reach = max(1, round(k * 0.03))
        pad = 3 * reach
        w, h = cloud.image.width + 2 * pad, cloud.image.height + 2 * pad
        shade = Image.new("L", (w, h), 0)
        shade.paste(cloud.image.getchannel("A"), (pad, pad))
        shade = shade.filter(ImageFilter.GaussianBlur(reach))
        x, y = cloud.x - pad + round(0.006 * k), cloud.y - pad + round(0.012 * k)
        shade = ImageChops.multiply(shade, where(behind, x, y, w, h)).point(_SHADE)
        dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dark.putalpha(shade)
        lay(_Placed(dark, x, y))
    lay(cloud)
    if layers.through is not None and cloud is not None:
        # the sun (or the moon) behind the cloud: its glow shows through where the cloud is
        glow = layers.through
        w, h = glow.alpha.size
        light = Image.new("RGBA", (w, h), glow.colour)
        light.putalpha(ImageChops.multiply(glow.alpha, where(cloud, glow.x, glow.y, w, h)))
        out.alpha_composite(light, dest=(glow.x, glow.y))
    if layers.bolt is not None:  # a flash now and then: two quick ones, then it rests a while
        beat = s % 3.7
        lit = not moving or beat < 0.07 or 0.14 < beat < 0.22
        if layers.flash is not None and moving:  # the cloud lights up from within, and fades
            flare = max((math.exp(-(beat - at) / 0.09) for at in (0.0, 0.14) if beat >= at),
                        default=0.0)  # fmt: skip
            put(layers.flash, front[0], front[1], share=flare, stretch=front[2])
        put(layers.bolt if lit or layers.bolt_rest is None else layers.bolt_rest)
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
    """The ``lit`` finish of a symbol: frosted glass lit from above."""

    name = "lit"
    types = ("symbol",)
    later = True  # the first frame makes the kind it shows; the others come after it

    def draw(self, renderer: Any, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        return draw(renderer, widget, snapshot, now, "lit")

    def prepare(self, renderer: Any, widget: dict[str, Any]) -> None:
        """Every kind of weather at this size, so that the first change of weather costs no
        more than any other frame (a lit set takes a few dozen milliseconds to make)."""
        for job in self.later_jobs(renderer, widget):
            job()

    def later_jobs(self, renderer: Any, widget: dict[str, Any]) -> list[Any]:
        k, shade, pack = max(8, widget["size"]), colours(renderer, widget), renderer.pack(widget)
        return [
            lambda kind=kind, night=night: layers_for(kind, night, k, "lit", shade, pack)
            for kind in KINDS
            for night in ((False, True) if kind in _SKY else (False,))
        ]


register(LitSymbol())

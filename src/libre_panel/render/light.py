"""Light over a frame, after SPUR II's light edges: glowing lines, the curves
that time them, and the theme colours they take.

Used by the band toast style, the tube and entrance transitions, the lit
finish and the panel light (render.lit, render.look). Light is added
(``ImageChops.add``), not blended, and only the rows a line covers are
touched: cheap to draw, and cheap for a panel's video encoder.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from libre_panel.fonts import builtin_font_path

RGB = tuple[int, int, int]

# Palette names themes use for these roles (the first one a theme has wins),
# and a fallback colour.
ROLES: dict[str, tuple[tuple[str, ...], str]] = {
    "card": (("card", "panel", "surface", "surf_1", "bg2"), "#101722"),
    "ground": (("bg", "base"), "#0A1119"),  # the screen itself: a band is one of its sections
    "text": (("text", "tx_1"), "#F1F5F9"),
    "muted": (("text3", "tx_3", "muted", "dim"), "#8CA0B8"),
    "track": (("track", "edge", "line", "rule_soft"), "#162332"),
    "rule": (("rule", "line", "edge", "dim"), "#32475E"),
    "void": (("void",), "#000000"),
    "info": (("accent", "primary", "cpu"), "#22D3EE"),
    "warning": (("warn", "warning"), "#FBBF24"),
    "error": (("crit", "error", "danger", "krit"), "#F87171"),
}
WHITE: RGB = (234, 240, 248)


def role_value(palette: dict[str, str], role: str) -> str:
    """The theme's colour for ``role``, as the palette writes it."""
    names, fallback = ROLES[role]
    return next((palette[n] for n in names if palette.get(n)), fallback)


def role_color(context: Any, role: str) -> RGB:
    """The theme's colour for ``role`` as RGB (``context`` may be None)."""
    value = role_value(getattr(context, "palette", None) or {}, role)
    try:
        return ImageColor.getrgb(value)[:3]
    except ValueError:  # a palette entry Pillow cannot read
        return ImageColor.getrgb(ROLES[role][1])[:3]


def accent_color(context: Any, toast: Any) -> RGB:
    """A toast's accent: ``payload["color"]`` (``"#rrggbb"``, ``"@name"`` or a tuple), else the
    level's, which is also what a colour nobody can read is taken for."""
    color = toast.payload.get("color")
    try:
        if isinstance(color, str) and color:
            if context is not None:  # knows the palette's names
                return tuple(context.color(color)[:3])  # type: ignore[return-value]
            return ImageColor.getrgb(color)[:3]
        if isinstance(color, tuple | list) and len(color) >= 3:
            return tuple(max(0, min(255, int(c))) for c in color[:3])  # type: ignore[return-value]
    except (TypeError, ValueError, OverflowError):
        pass
    level = toast.level if toast.level in ("warning", "error") else "info"
    return role_color(context, level)


def mix(a: RGB, b: RGB, share: float) -> RGB:
    """``a`` over ``b`` by ``share`` (0: all ``b``, 1: all ``a``)."""
    return tuple(round(b[i] + (a[i] - b[i]) * share) for i in range(3))  # type: ignore[return-value]


# -- timing --------------------------------------------------------------------


def clamp(u: float) -> float:
    return 0.0 if u < 0 else 1.0 if u > 1 else u


def phase(s: float, start: float, end: float) -> float:
    """0 before ``start``, 1 after ``end``, linear in between."""
    return clamp((s - start) / (end - start))


def smooth(u: float) -> float:
    u = clamp(u)
    return u * u * (3 - 2 * u)


def ease_out(u: float) -> float:
    """Fast, then settling (cubic)."""
    return 1.0 - (1.0 - clamp(u)) ** 3


def ease_in(u: float) -> float:
    """Slow, then faster (cubic)."""
    return clamp(u) ** 3


# -- light ---------------------------------------------------------------------


@lru_cache(maxsize=64)
def _profile(height: int, core: float, halo: float, share: float) -> Image.Image:
    """How a horizontal line's light falls off above and below it: a bright
    core in a wide halo (one column, L)."""
    mid = (height - 1) / 2
    values = []
    for y in range(height):
        d = y - mid
        light = math.exp(-((d / core) ** 2))
        if share:
            light += share * math.exp(-((d / halo) ** 2))
        values.append(min(255, round(255 * light)))
    return Image.frombytes("L", (1, height), bytes(values))


def add_light(image: Image.Image, xy: tuple[float, float], mask: Image.Image, color: RGB) -> None:
    """Add ``color`` through ``mask`` (L, 255 = all of it) at ``xy`` onto ``image``
    (RGB), only where the mask lies."""
    x, y = int(xy[0]), int(xy[1])
    box = (
        max(0, x),
        max(0, y),
        min(image.width, x + mask.width),
        min(image.height, y + mask.height),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    part = mask.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
    light = ImageChops.multiply(Image.new("RGB", part.size, color), Image.merge("RGB", [part] * 3))
    image.paste(ImageChops.add(image.crop(box), light), box[:2])


def scaled(mask: Image.Image, strength: float) -> Image.Image:
    return mask if strength >= 0.999 else mask.point(lambda v: round(v * strength))


def add_line(
    image: Image.Image,
    x0: float,
    x1: float,
    y: float,
    color: RGB,
    strength: float,
    unit: float = 1.0,
    soft: float = 40.0,
    halo: float = 13.0,
    share: float = 0.55,
    white: RGB = WHITE,
) -> None:
    """A horizontal line of light from ``x0`` to ``x1`` around row ``y``: a core
    burning towards ``white`` in a halo of ``color``, at ``strength`` (0 to 1).
    Its ends fade over ``soft`` px; ``halo`` is the halo's width for a panel
    480 px high, times ``unit``."""
    if strength <= 0.004 or x1 - x0 < 1:
        return
    half = max(3, round(32 * unit))
    top = round(y) - half
    a, b = max(0, top), min(image.height, top + 2 * half)
    xa, xb = max(0, math.floor(x0)), min(image.width, math.ceil(x1))
    if b <= a or xb <= xa:
        return
    look = (unit, soft, halo, share, color, white)
    light = _strip(xb - xa, 2 * half, x0 - xa, x1 - xa, look).crop((0, a - top, xb - xa, b - top))
    if strength < 0.999:
        light = light.point(lambda v: round(v * strength))
    image.paste(ImageChops.add(image.crop((xa, a, xb, b)), light), (xa, a))


@lru_cache(maxsize=16)
def _strip(width: int, height: int, x0: float, x1: float, look: tuple[Any, ...]) -> Image.Image:
    """The light of :func:`add_line` at full strength (RGB). A line that stays
    as it is (most do, most of the time) is worked out once."""
    unit, soft, halo, share, color, white = look
    ramp = max(1.0, min(soft, (x1 - x0) / 2))
    row = bytes(round(255 * clamp(min(x + 0.5 - x0, x1 - x - 0.5) / ramp)) for x in range(width))
    window = Image.frombytes("L", (width, 1), row).resize((width, height), Image.Resampling.NEAREST)
    core = max(0.7, 2.0 * unit)
    light = Image.new("RGB", (width, height))
    for tint, glow in ((color, share), (mix(white, (0, 0, 0), 0.45), 0.0)):
        column = _profile(height, core, max(1.5, halo * unit), glow)
        mask = ImageChops.multiply(window, column.resize((width, height), Image.Resampling.NEAREST))
        add_light(light, (0, 0), mask, tint)
    return light


def bloom(layer: Image.Image, radius: float, strength: float, scale: int = 4) -> Image.Image:
    """``layer`` blurred into a soft glow, worked out at 1/``scale`` of its size
    (much cheaper, and it looks the same)."""
    w, h = layer.size
    small = layer.resize((max(1, w // scale), max(1, h // scale)), Image.Resampling.BILINEAR)
    soft = small.filter(ImageFilter.GaussianBlur(max(0.3, radius)))
    return scaled(soft.resize((w, h), Image.Resampling.BILINEAR), strength)


def sun_position(t: float, latitude: float, longitude: float) -> tuple[float, float]:
    """Where the sun stands at Unix time ``t`` seen from a place: (azimuth from
    north, clockwise; elevation) in degrees. The Astronomical Almanac's
    approximation, good to about a degree: plenty for the direction of a light."""
    d = t / 86400.0 + 2440587.5 - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    ecliptic = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    tilt = math.radians(23.439 - 0.00000036 * d)
    ascension = math.atan2(math.cos(tilt) * math.sin(ecliptic), math.cos(ecliptic))
    declination = math.asin(math.sin(tilt) * math.sin(ecliptic))
    sidereal = (18.697374558 + 24.06570982441908 * d) % 24
    hour = math.radians((sidereal * 15 + longitude) % 360) - ascension
    phi = math.radians(latitude)
    elevation = math.asin(
        math.sin(phi) * math.sin(declination)
        + math.cos(phi) * math.cos(declination) * math.cos(hour)
    )
    azimuth = math.atan2(
        -math.sin(hour), math.tan(declination) * math.cos(phi) - math.sin(phi) * math.cos(hour)
    )
    return math.degrees(azimuth) % 360, math.degrees(elevation)


def paste_top(image: Image.Image, layer: Image.Image, x: int, y: int, rows: float) -> None:
    """The top ``rows`` rows of ``layer`` at (x, y); a part row is blended in by
    its share, so an edge that moves slowly glides instead of stepping."""
    rows = max(0.0, min(float(layer.height), rows))
    whole = int(rows)
    if whole > 0:
        image.paste(layer.crop((0, 0, layer.width, whole)), (x, y))
    part = rows - whole
    if part > 1 / 64 and whole < layer.height:
        under = image.crop((x, y + whole, x + layer.width, y + whole + 1))
        line = layer.crop((0, whole, layer.width, whole + 1))
        image.paste(Image.blend(under, line.convert(under.mode), part), (x, y + whole))


# -- text ----------------------------------------------------------------------


@lru_cache(maxsize=48)
def face(name: str, size: int) -> ImageFont.FreeTypeFont:
    """A built-in face (``"Barlow-Bold"``) of its own: the renderer's fonts may
    be in use by its helper thread."""
    return ImageFont.truetype(str(builtin_font_path("builtin:" + name)), max(6, int(size)))


def tracked_width(font: ImageFont.FreeTypeFont, text: str, tracking: float) -> float:
    return sum(font.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)


def draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Any,
    tracking: float,
) -> None:
    """Letter-spaced text with its baseline at ``xy``."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + tracking


def fitted_text(text: str, name: str, sizes: tuple[int, ...], width: float) -> tuple[str, Any]:
    """``text`` in the largest of ``sizes`` that fits ``width``, else cut with "…"
    at the smallest."""
    font = face(name, sizes[-1])
    for size in sizes:
        font = face(name, size)
        if font.getlength(text) <= width:
            return text, font
    while len(text) > 1 and font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…", font

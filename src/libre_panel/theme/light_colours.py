"""A theme in light colours (config.toml ``light_colours``, since 0.8): for a white PC, the
themes made for a dark ground turned light, each in its own colours.

Every colour of a theme is worked out anew in OKLab (lightness, chroma, hue), by the job it
does:

- **The ground** (the palette's ``bg``, ``bg2`` and ``surface``, and any colour darker than
  :data:`GROUND_L`): dark and close together becomes light and close together, in the same
  order, so a card stays lighter than what lies behind it (white cards on a light grey);
- **ink** (lines, tracks, texts, and the dim, nearly grey colours): light on dark becomes dark
  on light, the brightest text the darkest, a faint line a faint line;
- **colours** (the accents, the colours of the CPU, the GPU, warnings): their hue kept, as
  light as reads on a light ground, a little more saturated (pastels on black are vivid
  colours on white), yellows and greens a little lighter, or they turn brown.

What light does on a dark ground does not carry over: the ``lit`` finish runs its rings and
bars towards white, which on a light ground is no light at all, so they are drawn ``flat``;
glows are a third as strong; a background picture made for a dark ground goes (its colours'
gradient stays). The theme as made is not changed: this is how it is drawn.

Measured against the Paper look (theme.modules.LOOKS), which is made by hand for a light ground:
Arctic's colours turned light come within 0.05 of its lightness, role by role.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from functools import lru_cache
from typing import Any

from PIL import ImageColor

# the palette's roles by their job (Libre Panel's own families name them so; older themes,
# SPUR's among them, have names of their own); a role of neither is placed by its lightness
GROUND_ROLES = frozenset(
    {"bg", "bg2", "bg3", "bg_low", "surface", "surface2", "panel", "panel2", "card", "card2",
     "base", "base_low", "void", "surf_1", "surf_2"}
)  # fmt: skip
INK_ROLES = frozenset(
    {"line", "edge", "rule", "rule_soft", "rule_strong", "grid", "grid_major", "track", "dim",
     "muted", "text", "text2", "text3", "tx_1", "tx_2", "tx_3"}
)  # fmt: skip
INK_KEYS = frozenset({"outline", "background", "tick_color", "grid_color", "mark_color", "marks"})
GROUND_L = 0.26  # an OKLab lightness below which a colour is ground (its role aside)
NEUTRAL_C = 0.045  # a chroma below which a colour is ink, not a colour
DARK_INK_L = 0.45  # ... and a lightness below which a tinted one is ink too (a blue line)
# ink: its lightness on the dark ground -> on the light one (the brightest text the darkest)
INK = ((0.17, 0.96), (0.40, 0.80), (0.70, 0.56), (0.97, 0.24), (1.0, 0.21))
# the ground: a lightness on the dark ground -> on the light one, the gaps between its colours
# wider (a card a shade lighter than the ground on black is white on light grey), within
# GROUND_LIGHT; black, the ground of the plainest themes (High Contrast, Mono), is white
GROUND_AT = (0.165, 0.95)
GROUND_GAP = 2.5
GROUND_LIGHT = (0.90, 0.995)
BLACK_L = 0.03
COLOUR_L = 0.56  # a colour's lightness on the light ground ...
YELLOW_L = 0.07  # ... and how much lighter a yellow (the hue below) is
YELLOW_HUE = math.radians(95)
CHROMA = 1.3  # how much more saturated a colour is on a light ground (at most MAX_CHROMA)
MAX_CHROMA = 0.26
GLOW = 0.35  # how strong glows are on a light ground
FINISHES = {"lit": "flat"}  # finishes that are light on a dark ground, and what they become
LIGHT = "light"  # in a theme's style: its modules are drawn for a light ground

BODY_L = 0.80  # the weather's clouds and the moon: light grey bodies, lit, on a light ground


def _weather_icon(widget: dict[str, Any]) -> bool:
    """Whether ``widget`` is a picture of the weather (a symbol: the sky, the moon)."""
    return widget.get("type") == "symbol" or (
        widget.get("type") == "icon" and widget.get("icon") == "weather"
    )


def light_part(widget: dict[str, Any], finish: str) -> str:
    """The finish a module's part ``widget`` is drawn with in light colours (its ``finish``
    as the module gives it): the light of rings and bars is drawn flat, the weather's bodies
    stay lit (their clouds a light grey); the part's colour is set here where it changes."""
    if _weather_icon(widget):
        widget["color"] = body_colour(widget.get("color"))
        return finish
    return FINISHES.get(finish, finish)


def body_colour(colour: Any) -> str:
    """The clouds' and the moon's colour on a light ground: a light grey of the colour's hue."""
    try:
        found = ImageColor.getrgb(colour) if isinstance(colour, str) and colour[:1] == "#" else None
    except ValueError:
        found = None
    hue = lch(found[:3])[2] if found else 0.0
    chroma = min(lch(found[:3])[1], 0.015) if found else 0.0
    return lch_hex(BODY_L, chroma, hue)


# a widget's fields (and a look block's) that hold colours
COLOUR_KEYS = frozenset(
    {"color", "color2", "colors", "background", "done", "face", "grid_color", "mark_color",
     "marks", "muted", "night", "outline", "tick_color"}
)  # fmt: skip


# -- OKLab ----------------------------------------------------------------------------------------


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB (0 to 255) as OKLab (Björn Ottosson's)."""
    r, g, b = (_linear(v / 255) for v in rgb)
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _linear_rgb(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _shown(lightness: float, a: float, b: float) -> bool:
    return all(-1e-4 <= v <= 1 + 1e-4 for v in _linear_rgb(lightness, a, b))


def lch_hex(lightness: float, chroma: float, hue: float) -> str:
    """ "#RRGGBB" of an OKLab lightness, chroma and hue; a chroma the screen cannot show is
    taken down until it can (the hue and lightness kept)."""
    lightness = min(1.0, max(0.0, lightness))
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    if not _shown(lightness, a, b):
        low, high = 0.0, chroma
        for _ in range(24):
            mid = (low + high) / 2
            if _shown(lightness, mid * math.cos(hue), mid * math.sin(hue)):
                low = mid
            else:
                high = mid
        a, b = low * math.cos(hue), low * math.sin(hue)
    channels = (round(min(1.0, max(0.0, _gamma(min(1.0, max(0.0, v))))) * 255)
                for v in _linear_rgb(lightness, a, b))  # fmt: skip
    return "#" + "".join(f"{v:02X}" for v in channels)


def lch(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """OKLab lightness, chroma and hue (radians) of an sRGB colour."""
    lightness, a, b = oklab(rgb)
    return lightness, math.hypot(a, b), math.atan2(b, a)


# -- colours --------------------------------------------------------------------------------------


def _along(x: float, points: tuple[tuple[float, float], ...]) -> float:
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return y0 + (y1 - y0) * (max(x, x0) - x0) / (x1 - x0)
    return points[-1][1]


@lru_cache(maxsize=4096)
def light_colour(colour: str, role: str | None = None) -> str:
    """``colour`` (any a theme may hold: "#0b8", "#0B8C84CC", "teal") for a light ground, by its
    job (its palette ``role`` where it has one); its opacity kept. A reference to the palette
    ("@cpu") or anything that is not a colour is returned as it is."""
    if not isinstance(colour, str) or not colour or colour.startswith("@"):
        return colour
    try:
        found = ImageColor.getrgb(colour)
    except ValueError:
        return colour
    alpha = f"{found[3]:02X}" if len(found) > 3 and found[3] < 255 else ""
    lightness, chroma, hue = lch(found[:3])
    # ground by its role; without one by its lightness ("" : a line's or a track's colour, ink)
    ground = role in GROUND_ROLES if role else role is None and lightness < GROUND_L
    if ground:
        # the ground: light, in the same order
        low, high = GROUND_LIGHT
        light = GROUND_AT[1] + (lightness - GROUND_AT[0]) * GROUND_GAP
        light = 0.99 if lightness < BLACK_L else min(high, max(low, light))
        made = lch_hex(light, min(chroma * 0.4, 0.02), hue)
    elif role in INK_ROLES or chroma < NEUTRAL_C or lightness < DARK_INK_L:
        made = lch_hex(_along(lightness, INK), min(chroma * 0.8, 0.05), hue)  # ink
    else:
        yellow = max(0.0, math.cos(hue - YELLOW_HUE))
        made = lch_hex(COLOUR_L + YELLOW_L * yellow, min(MAX_CHROMA, chroma * CHROMA), hue)
    return made + alpha


def light_palette(palette: dict[str, str]) -> dict[str, str]:
    """A theme's palette for a light ground, each colour by its role."""
    return {name: light_colour(value, name) for name, value in palette.items()}


def _light(value: Any, key: str = "") -> Any:
    """A widget's (or a look block's) fields for a light ground: its colours, its finish and
    its glow; the rest as it is."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        body = _weather_icon(value)
        for name, field in value.items():
            if body and name in ("finish", "color"):
                out[name] = body_colour(field) if name == "color" else field
            elif name == "finish" and isinstance(field, str):
                out[name] = FINISHES.get(field, field)
            elif name == "glow" and isinstance(field, int | float) and not isinstance(field, bool):
                out[name] = field * GLOW
            else:
                out[name] = _light(field, name)
        return out
    if isinstance(value, list):
        return [_light(item, key) for item in value]
    if isinstance(value, str) and key in COLOUR_KEYS:
        return light_colour(value, "" if key in INK_KEYS else None)  # ("": ink, not ground)
    return value


def light_theme(theme: Any) -> Any:
    """``theme`` (a theme.model.Theme) in light colours; the theme itself is not changed."""
    style = theme.style
    if style is not None or any(widget.get("type") == "module" for widget in theme.widgets):
        from libre_panel.theme.modules import STYLE_DEFAULTS

        glow = float((style or {}).get("glow", STYLE_DEFAULTS["glow"]))
        # (the modules' parts, made as they are drawn, take their finish by it: build_module)
        style = {**(style or {}), "glow": glow * GLOW, LIGHT: True}
    gradient = theme.background_gradient
    return replace(
        theme,
        palette=light_palette(theme.palette),
        background_color=light_colour(theme.background_color, "bg"),
        background_gradient=(
            None if gradient is None else (light_colour(gradient[0]), light_colour(gradient[1]))
        ),
        background_image=None,
        widgets=_light(copy.deepcopy(theme.widgets)),
        look=_light(copy.deepcopy(theme.look)),
        style=style,
    )

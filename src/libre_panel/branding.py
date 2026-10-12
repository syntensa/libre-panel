"""The Libre Panel logo, drawn in code.

The mark is a gauge's ring, open at the bottom, lit from the lower left over the top to the
right and dark for its last part, round three bars that rise to the right, the last one lit.
Its geometry is here once: the app's icon and the tray (:func:`logo`), the panel's start
screen (:func:`mark`) and the SVG of the app's header and of the website (:func:`mark_svg`)
are all drawn from it. ``packaging/make_icons.py`` writes the static files (app icon,
favicon, Windows .ico).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

ACCENT = (46, 230, 214)  # the app's accent (#2ee6d6)
TEXT = (232, 238, 244)
TRACK = (42, 52, 64)
TEAL = ACCENT  # (the name the tray and older code use)

# The mark, in units of the ring's radius (to the middle of its stroke), its centre at (0, 0)
# and y down. Angles are degrees clockwise from three o'clock.
STROKE = 0.26  # the ring's width
OPEN_FROM, LIT_TO, OPEN_TO = 135.0, 345.0, 405.0  # the ring, and where its light ends
BAR_W = 0.24
BARS = ((-0.51, 0.42), (-0.12, 0.66), (0.27, 0.90))  # each bar's left edge and height
BASE = 0.40  # the line the bars stand on
BAR_ROUND = 0.08  # the radius of a bar's corners
EXTENT = 1 + STROKE / 2  # the ring's outer radius
# The ring is open below: the mark is drawn this much lower than the middle of its box, so
# that it sits in the middle (from the top of the ring to the ends of its opening).
LIFT = (EXTENT - (math.sin(math.radians(OPEN_FROM)) + STROKE / 2)) / 2

# Status dot colours for the tray icon.
STATUS_COLORS = {
    "showing": (34, 197, 94),
    "waiting": (245, 158, 11),
    "starting": (245, 158, 11),
    "paused": (100, 116, 139),
    "error": (239, 68, 68),
}


def clearance() -> float:
    """The least room between a bar and the ring's inner edge (in ring radii)."""
    inner = 1 - STROKE / 2
    room = inner
    for left, height in BARS:
        top = BASE - height
        for x in (left + BAR_ROUND, left + BAR_W - BAR_ROUND):  # the centres of the roundings
            room = min(room, inner - (math.hypot(x, top + BAR_ROUND) + BAR_ROUND))
    return room


def _paint(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    radius: float,
    accent: tuple[int, ...],
    bars: tuple[int, ...] = TEXT,
    track: tuple[int, ...] = TRACK,
) -> None:
    """The mark on ``draw``, its ring ``radius`` px (to the middle of its stroke) round
    (``cx``, ``cy``): the dark end of the ring first, then the lit part over it, then the bars;
    every end of the ring is round."""
    width = STROKE * radius
    outer = radius + width / 2
    box = (cx - outer, cy - outer, cx + outer, cy + outer)  # (a wide arc is drawn inward)

    def cap(angle: float, colour: tuple[int, ...]) -> None:
        a = math.radians(angle)
        x, y = cx + radius * math.cos(a), cy + radius * math.sin(a)
        draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=colour)

    draw.arc(box, LIT_TO, OPEN_TO, fill=track, width=round(width))
    cap(OPEN_TO, track)
    draw.arc(box, OPEN_FROM, LIT_TO, fill=accent, width=round(width))
    cap(OPEN_FROM, accent)
    cap(LIT_TO, accent)
    for i, (left, height) in enumerate(BARS):
        x0, y1 = cx + left * radius, cy + BASE * radius
        draw.rounded_rectangle(
            (x0, y1 - height * radius, x0 + BAR_W * radius, y1),
            radius=BAR_ROUND * radius,
            fill=accent if i == len(BARS) - 1 else bars,
        )


def mark(diameter: float, light: float = 1.0, glow: float = 0.06) -> Image.Image:
    """The mark alone, its ring ``diameter`` px across, in the middle of a transparent square
    half as large again (room for its glow). ``light`` (0 to 1) dims what is lit, as the
    panel's start screen breathes; ``glow`` is how far the light reaches (of ``diameter``)."""
    scale = 4
    size = max(8, math.ceil(diameter * 1.5))
    shape = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    radius = diameter / 2 / EXTENT * scale
    accent = tuple(round(c * (0.55 + 0.45 * light)) for c in ACCENT) + (255,)
    middle = size * scale / 2
    _paint(ImageDraw.Draw(shape), middle, middle + LIFT * radius, radius, accent,
           TEXT + (255,), TRACK + (255,))  # fmt: skip
    shape = shape.convert("RGBa").reduce(scale).convert("RGBA")
    if glow <= 0:
        return shape
    # its glow: what is drawn, blurred, as light round it
    lit = Image.new("RGB", shape.size, (0, 0, 0))
    lit.paste(shape.convert("RGB"), (0, 0), shape.getchannel("A"))
    halo = lit.filter(ImageFilter.GaussianBlur(max(1.0, diameter * glow)))
    halo = halo.point(lambda v: round(v * (0.5 + 0.35 * light)))
    out = Image.new("RGBA", shape.size, (0, 0, 0, 0))
    out.paste(halo.convert("RGBA"), (0, 0))
    out.putalpha(halo.convert("L"))
    out.alpha_composite(shape)
    return out


def logo(size: int = 256, status: str | None = None) -> Image.Image:
    """The app's icon: the mark on a rounded tile; optional status dot (the tray)."""
    s = size * 4  # supersampled, then reduced for clean edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    tile = Image.new("L", (s, s), 0)
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.22, fill=255)
    top, bottom = (22, 32, 48), (9, 13, 20)
    gradient = Image.linear_gradient("L").resize((s, s))
    body = Image.composite(
        Image.new("RGB", (s, s), bottom), Image.new("RGB", (s, s), top), gradient
    )
    img.paste(body, (0, 0), tile)

    radius = s * 0.335 / EXTENT  # the ring is two thirds of the tile across
    cx, cy = s / 2, s / 2 + LIFT * radius
    none = (0, 0, 0, 0)
    lit = Image.new("RGBA", (s, s), none)
    _paint(ImageDraw.Draw(lit), cx, cy, radius, ACCENT + (255,), none, none)
    img.alpha_composite(lit.filter(ImageFilter.GaussianBlur(s * 0.03)))  # the light's glow
    _paint(ImageDraw.Draw(img), cx, cy, radius, ACCENT + (255,), TEXT + (255,), TRACK + (255,))

    if status in STATUS_COLORS:
        draw = ImageDraw.Draw(img)
        r = s * 0.17
        x, y = s - r - s * 0.02, s - r - s * 0.02
        draw.ellipse(
            (x - r - s * 0.03, y - r - s * 0.03, x + r + s * 0.03, y + r + s * 0.03),
            fill=(9, 13, 20, 255),
        )
        draw.ellipse((x - r, y - r, x + r, y + r), fill=STATUS_COLORS[status] + (255,))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def _hex(colour: tuple[int, ...]) -> str:
    return "#" + "".join(f"{c:02x}" for c in colour[:3])


def mark_svg(box: float = 24.0, margin: float = 0.7) -> str:
    """The mark as SVG elements for a ``box`` × ``box`` viewBox: what the app's header
    (ui/icons.js, ``logo``) and the website (``#i-logo``) show. Each element has its own
    colours and stroke, so it looks the same whatever the icon's own are."""
    radius = (box / 2 - margin) / EXTENT
    cx, cy = box / 2, box / 2 + LIFT * radius

    def n(v: float) -> str:
        return f"{v:.2f}".rstrip("0").rstrip(".")

    def point(angle: float) -> str:
        a = math.radians(angle)
        return f"{n(cx + radius * math.cos(a))} {n(cy + radius * math.sin(a))}"

    def arc(start: float, end: float, colour: tuple[int, ...]) -> str:
        large = 1 if end - start > 180 else 0
        return (
            f'<path d="M{point(start)}A{n(radius)} {n(radius)} 0 {large} 1 {point(end)}" '
            f'fill="none" stroke="{_hex(colour)}" stroke-width="{n(STROKE * radius)}" '
            'stroke-linecap="round"/>'
        )

    parts = [arc(LIT_TO, OPEN_TO, TRACK), arc(OPEN_FROM, LIT_TO, ACCENT)]
    for i, (left, height) in enumerate(BARS):
        colour = ACCENT if i == len(BARS) - 1 else TEXT
        parts.append(
            f'<rect x="{n(cx + left * radius)}" y="{n(cy + (BASE - height) * radius)}" '
            f'width="{n(BAR_W * radius)}" height="{n(height * radius)}" '
            f'rx="{n(BAR_ROUND * radius)}" fill="{_hex(colour)}" stroke="none"/>'
        )
    return "".join(parts)

"""The Libre Panel logo, drawn in code.

The tray draws it at runtime with a status dot; ``packaging/make_icons.py``
writes the static files (app icon, favicon, Windows .ico) from the same code.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

TEAL = (34, 211, 238)

# Status dot colours for the tray icon.
STATUS_COLORS = {
    "showing": (34, 197, 94),
    "waiting": (245, 158, 11),
    "starting": (245, 158, 11),
    "paused": (100, 116, 139),
    "error": (239, 68, 68),
}


def logo(size: int = 256, status: str | None = None) -> Image.Image:
    """Rounded tile with a ring gauge and three level bars; optional status dot."""
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

    draw = ImageDraw.Draw(img)
    pad, width = s * 0.17, s * 0.085
    box = (pad, pad, s - pad, s - pad)
    draw.arc(box, 135, 405, fill=(40, 52, 70, 255), width=int(width))

    ring = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(ring).arc(box, 135, 345, fill=TEAL + (255,), width=int(width))
    glow = ring.filter(ImageFilter.GaussianBlur(s * 0.03))
    img.alpha_composite(glow)
    img.alpha_composite(ring)
    # round caps on the lit part of the ring
    cx = cy = s / 2
    radius = (s - 2 * pad) / 2 - width / 2

    for angle in (135, 345):
        x = cx + radius * math.cos(math.radians(angle))
        y = cy + radius * math.sin(math.radians(angle))
        draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=TEAL)

    bar_w, gap = s * 0.075, s * 0.05
    base = s * 0.64
    heights = (0.16, 0.25, 0.34)
    left = cx - (3 * bar_w + 2 * gap) / 2
    for i, h in enumerate(heights):
        x0 = left + i * (bar_w + gap)
        color = (230, 237, 245, 255) if i < 2 else TEAL + (255,)
        draw.rounded_rectangle(
            (x0, base - s * h, x0 + bar_w, base), radius=bar_w * 0.35, fill=color
        )

    if status in STATUS_COLORS:
        r = s * 0.17
        x, y = s - r - s * 0.02, s - r - s * 0.02
        draw.ellipse(
            (x - r - s * 0.03, y - r - s * 0.03, x + r + s * 0.03, y + r + s * 0.03),
            fill=(9, 13, 20, 255),
        )
        draw.ellipse((x - r, y - r, x + r, y + r), fill=STATUS_COLORS[status] + (255,))

    return img.resize((size, size), Image.Resampling.LANCZOS)

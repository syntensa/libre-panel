"""Which panel is which: for a few seconds each panel shows its number, large, instead of its
theme (Settings → Panel → More panels). The first panel ([device]) is 1, those of
``[[panels]]`` 2, 3 and 4 in their order there.
"""

from __future__ import annotations

import time
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from libre_panel.fonts import builtin_font_path

SHOW_S = 6.0
FONT = "builtin:Barlow-Medium"

_until = 0.0


def show(seconds: float = SHOW_S) -> None:
    """Every panel shows its number from now on, for ``seconds``."""
    global _until
    _until = time.monotonic() + seconds


def showing() -> bool:
    return time.monotonic() < _until


@lru_cache(maxsize=8)
def picture(number: int, size: tuple[int, int], colour: str = "#2EE6D6") -> Image.Image:
    """The panel's number on black, as large as it fits, in ``colour``."""
    width, height = size
    image = Image.new("RGB", size, "black")
    draw = ImageDraw.Draw(image)
    text = str(number)
    path = builtin_font_path(FONT)
    side = int(min(width, height) * 0.8)
    font = ImageFont.truetype(str(path), side) if path else ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    x = (width - (box[2] - box[0])) / 2 - box[0]
    y = (height - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), text, font=font, fill=colour)
    margin = max(2, min(width, height) // 30)
    draw.rectangle([margin, margin, width - 1 - margin, height - 1 - margin], outline=colour,
                   width=max(2, margin // 2))  # fmt: skip
    return image

"""The accent colour every theme may take instead of its own (config.toml ``accent``): the one
Windows shows (``"system"``) or one of the person's (``"#e0457b"``).

Windows keeps its accent colour in the registry (DWM's AccentColor, as 0xAABBGGRR). One that
is too dark to stand out on a panel's dark ground is lightened as far as it needs.
"""

from __future__ import annotations

import colorsys
import logging
import re
import sys

log = logging.getLogger(__name__)

SYSTEM = "system"
HEX = re.compile(r"#[0-9a-fA-F]{6}")
LIGHTEST_NEEDED = 0.5  # the lightness (HLS) a colour on a dark ground needs at least


def readable(colour: str) -> str:
    """``colour`` light enough for a dark panel: its hue kept, its lightness raised to
    :data:`LIGHTEST_NEEDED` where it is below."""
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, light, saturation = colorsys.rgb_to_hls(r, g, b)
    if light >= LIGHTEST_NEEDED:
        return colour.upper()
    r, g, b = colorsys.hls_to_rgb(hue, LIGHTEST_NEEDED, saturation)
    return "#{:02X}{:02X}{:02X}".format(*(round(v * 255) for v in (r, g, b)))


def system_accent() -> str | None:
    """Windows' accent colour, made readable on a dark panel; None elsewhere, or where
    Windows does not say."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            value, _ = winreg.QueryValueEx(key, "AccentColor")
    except OSError as exc:
        log.debug("no accent colour from Windows: %s", exc)
        return None
    if not isinstance(value, int):
        return None
    red, green, blue = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
    return readable(f"#{red:02X}{green:02X}{blue:02X}")


def accent_of(choice: str) -> str | None:
    """The colour config.toml's ``accent`` stands for now: None for the themes' own."""
    if choice == SYSTEM:
        return system_accent()
    return choice.upper() if HEX.fullmatch(choice) else None

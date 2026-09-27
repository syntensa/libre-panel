"""Fonts shipped with Libre Panel (SIL Open Font License, see README.md here)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent
BUILTIN_PREFIX = "builtin:"
DEFAULT_FONT = "builtin:Barlow-Medium"


@cache
def builtin_fonts() -> dict[str, Path]:
    """``{"Barlow-SemiBold": path, ...}`` for every bundled font file."""
    return {p.stem: p for p in sorted(FONT_DIR.glob("*.ttf"))}


def builtin_font_path(ref: str) -> Path | None:
    if not ref.startswith(BUILTIN_PREFIX):
        return None
    return builtin_fonts().get(ref[len(BUILTIN_PREFIX) :])

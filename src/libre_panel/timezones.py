"""Time zones by name ("Asia/Tokyo"), for clocks that show another place's time."""

from __future__ import annotations

from datetime import tzinfo
from functools import lru_cache


@lru_cache(maxsize=64)
def zone(name: str) -> tzinfo | None:
    """The time zone called ``name``; None if there is none of that name."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except (ValueError, KeyError, OSError, ImportError):  # KeyError: ZoneInfoNotFoundError
        return None

"""Small helpers for services that look at readings."""

from __future__ import annotations

import math
from typing import Any


def number(value: Any) -> float | None:
    """A reading's value as a finite number, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def readable(value: float) -> str:
    """A value as it reads on a panel: 92, 4.2."""
    return f"{value:.0f}" if abs(value) >= 10 or value == round(value) else f"{value:.1f}"

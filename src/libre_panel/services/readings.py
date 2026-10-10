"""Small helpers for services that look at readings."""

from __future__ import annotations

import math
from typing import Any

from libre_panel.sensors.base import Snapshot


def number(value: Any) -> float | None:
    """A reading's value as a finite number, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def is_playing(snapshot: Snapshot) -> bool:
    """Whether something plays: ``media.state`` says "Playing" (else ``media.playing`` is 1)."""
    state = snapshot.value("media.state")
    if isinstance(state, str):
        return state.lower() == "playing"
    return bool(snapshot.value("media.playing"))


def readable(value: float) -> str:
    """A value as it reads on a panel: 92, 4.2."""
    return f"{value:.0f}" if abs(value) >= 10 or value == round(value) else f"{value:.1f}"

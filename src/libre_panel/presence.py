"""Whether the person is away from the computer: it is locked, or its monitor is off.

Windows tells the session window (winsession.py). The panels then take the brightness of
``[device] away_brightness`` (0: dark) where ``dark_when_away`` says so (config.brightness_now),
and their own again when the person is back. A reason is said by whoever notices it; the
person is away while one of them holds.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

LOCKED = "locked"  # the session is locked (Win+L, or by itself)
DISPLAY_OFF = "display-off"  # the monitor went off (its power plan, or by hand)

_reasons: set[str] = set()
_lock = threading.Lock()
_changes = 0  # how often the person left or came back: the panel loop compares it


def set_away(reason: str, on: bool) -> None:
    """Say that ``reason`` holds now (``on``) or no longer does."""
    global _changes
    with _lock:
        before = bool(_reasons)
        if on:
            _reasons.add(reason)
        else:
            _reasons.discard(reason)
        if bool(_reasons) != before:
            _changes += 1
            log.info("the person is %s (%s)", "away" if _reasons else "back", reason)


def away() -> bool:
    return bool(_reasons)


def changes() -> int:
    """A number that changes when the person leaves or comes back."""
    return _changes


def reset() -> None:
    """Nobody away (a new start; the tests)."""
    global _changes
    with _lock:
        if _reasons:
            _changes += 1
        _reasons.clear()

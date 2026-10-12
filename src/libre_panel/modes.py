"""The pages of the panel that Libre Panel switches to by itself: "media" while music plays
(Encore by default) and "idle" when nobody has touched the PC for some minutes (Almanac).

They are modes as game mode is one ([modes.<name>] in config.toml: theme, frame rate), set by
the main loop instead of a service, and only while no service has a mode of its own: a game
goes first. Each is off until it is switched on (``on = true``); "media" goes before "idle".

    [modes.media]
    on = true
    theme = "encore"      # (the default)

    [modes.idle]
    on = true
    minutes = 5           # without a key or a click
    theme = "almanac"     # (the default)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from libre_panel.sensors.base import want

log = logging.getLogger(__name__)

GAME, MEDIA, IDLE, REPORT = "game", "media", "idle", "report"
OWNER = "pages"  # who sets the mode of a page, for the host (a service goes by its name)
DEFAULT_THEMES = {MEDIA: "encore", IDLE: "almanac"}
# The screens made for a page, offered first where its theme is chosen
SUGGESTED = {
    GAME: ("spur-session", "spur-focus", "spur-header"),
    MEDIA: ("encore",),
    IDLE: ("almanac", "almanac-analog"),
    REPORT: ("round-report", "spur-round-report"),
}
REPORTS = SUGGESTED[REPORT]  # shown after a game only: not offered for the other pages
IDLE_MINUTES = 5.0
MEDIA_LEAVE_S = 8.0  # a pause this long ends the music's page (not the gap between two tracks)
IDLE_EVERY_S = 1.0  # how often the time without input is asked for


def page_on(config: Any, name: str) -> bool:
    """Whether the page of ``name`` (a mode Libre Panel switches to itself) is switched on."""
    mode = config.modes.get(name)
    return bool(mode is not None and mode.on)


def page_theme(config: Any, name: str | None) -> str | None:
    """The theme a mode's page shows: config.toml's, else the page's own (Encore for music)."""
    mode = config.modes.get(name or "")
    if mode is not None and mode.theme:
        return str(mode.theme)
    return DEFAULT_THEMES.get(name or "")


def idle_minutes(config: Any) -> float:
    mode = config.modes.get(IDLE)
    return float(mode.minutes) if mode is not None and mode.minutes else IDLE_MINUTES


# -- the time without input ---------------------------------------------------------------------


def _windows_idle() -> float | None:
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    kernel32.GetTickCount.restype = ctypes.c_uint
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000  # (both wrap at 2³²)


def _logind_idle(run: Callable[..., Any] = subprocess.run) -> float | None:
    """How long the desktop has called this login session idle (logind's IdleHint, which
    GNOME, KDE and others set after their own delay); None where it is not known."""
    session = os.environ.get("XDG_SESSION_ID")
    if not session:
        return None
    try:
        out = run(
            ["loginctl", "show-session", session, "-p", "IdleHint", "-p",
             "IdleSinceHintMonotonic"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if values.get("IdleHint") != "yes":
        return 0.0 if "IdleHint" in values else None
    try:
        since = int(values.get("IdleSinceHintMonotonic", "0")) / 1e6
    except ValueError:
        return None
    # logind counts on CLOCK_MONOTONIC, the clock time.monotonic reads on Linux
    return max(0.0, time.monotonic() - since) if since else 0.0


def idle_seconds() -> float | None:
    """Seconds since the last key or click on this PC; None where it cannot be known."""
    try:
        return _windows_idle() if sys.platform == "win32" else _logind_idle()
    except Exception:  # noqa: BLE001 - a page that cannot know stays where it is
        log.debug("the time without input is not known", exc_info=True)
        return None


# -- which page, now ----------------------------------------------------------------------------


class PageSwitch:
    """Which of the panel's own pages is due: music while a track plays (and for a short
    pause after it), else the clock once the PC has been idle for the page's minutes."""

    def __init__(self, clock: Callable[[], float], idle: Callable[[], float | None] = idle_seconds):
        self.clock, self.idle = clock, idle
        self.leave_s = MEDIA_LEAVE_S  # how long music's page stays after a track stops
        self.played_at: float | None = None
        self.idle_s: float | None = None
        self.asked_at: float | None = None
        self.reads: frozenset[str] = frozenset()

    def _read(self, keys: frozenset[str]) -> None:
        if keys != self.reads:  # the media source runs only while somebody reads it
            self.reads = keys
            want(self, set(keys))

    def due(self, config: Any, readings: dict[str, Any]) -> str | None:
        now = self.clock()
        media, idle = page_on(config, MEDIA), page_on(config, IDLE)
        self._read(frozenset({"media.playing", "media.title"}) if media else frozenset())
        if media:
            playing = readings.get("media.playing")
            if playing is not None and getattr(playing, "value", playing) == 1:
                self.played_at = now
            if self.played_at is not None and now - self.played_at < self.leave_s:
                return MEDIA
        if idle:
            if self.asked_at is None or now - self.asked_at >= IDLE_EVERY_S:
                self.asked_at, self.idle_s = now, self.idle()
            if self.idle_s is not None and self.idle_s >= idle_minutes(config) * 60:
                return IDLE
        return None

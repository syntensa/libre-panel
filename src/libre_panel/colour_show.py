"""The colour check on a panel, for the eye (Settings → Panel → Check the colours): for
:data:`SHOW_S` the panel chosen shows the check's test picture instead of its theme, and every
:data:`SWITCH_S` it is made the other way, the letter large in a corner:

- "feed" (*Show it on the panel*): "A" its colours converted by the graphics card
  (video.pixels "card": AMD's, NVIDIA's and Intel's encoders), "B" by ffmpeg on the processor
  ("processor": as video.pixels "auto" has it until a check found the card right);
- "range" (*Is black too light on the panel? Compare*): "A" the video in limited range
  (16-235), "B" in full range (0-255), as [device] video_range would have it; the person says
  which looks right, and that is written.

The display starts ffmpeg anew at each switch (a moment's still picture; the panel stays open).
Nothing of it is written into config.toml: the picture and the way are what each panel's loop
asks here on every frame (:func:`now`), so once it ends, by its time, by :func:`end` or with
the app, every panel shows its theme and makes its video as before at its next frame. A panel
in a process of its own is told by the first panel's process (:func:`export`, :func:`adopt`).
While it runs a marker lies beside config.toml; one found at the next start (:func:`recover`)
says that the app ended during a check.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

SHOW_S = 30.0
SWITCH_S = 3.0
# what each kind compares: the letter on the panel and the value for it (video.pixels, or
# [device] video_range)
WAYS = {
    "feed": (("A", "card"), ("B", "processor")),
    "range": (("A", "limited"), ("B", "full")),
}
FEEDS = WAYS["feed"]
MARKER = "colour-check.json"

clock = time.monotonic
_lock = threading.Lock()
_own: tuple[int, float, float, str] | None = None  # (panel number, since, until, kind)
_told: tuple[int, str, str, str, float] | None = None  # from the first panel's process


def _marker() -> Path:
    from libre_panel.config import config_dir

    return config_dir() / MARKER


def begin(number: int, seconds: float | None = None, kind: str = "feed") -> None:
    """Panel ``number`` (1 the first, 2 the first beside it, ...) shows the test picture from
    now on, for ``seconds`` (:data:`SHOW_S`), A and B of ``kind`` (:data:`WAYS`) in turn."""
    global _own
    if kind not in WAYS:
        raise ValueError(f"kind: one of {', '.join(WAYS)}")
    seconds = SHOW_S if seconds is None else seconds
    path = _marker()
    if path.parent.is_dir():  # (no config.toml's folder: nothing to come back to)
        path.write_text(json.dumps({"panel": number, "kind": kind, "began": int(time.time())})
                        + "\n", encoding="utf-8")  # fmt: skip
    now = clock()
    with _lock:
        _own = (number, now, now + seconds, kind)
    log.info("colour check: panel %d shows the test picture, A and B in turn (%s)", number, kind)


def end() -> None:
    """The test picture goes: every panel shows and makes its video as before at its next
    frame."""
    global _own
    with _lock:
        was, _own = _own, None
    with contextlib.suppress(OSError):
        _marker().unlink(missing_ok=True)
    if was is not None:
        log.info("colour check: panel %d shows its theme again", was[0])


def _phase(kind: str, since: float, now: float) -> tuple[str, str]:
    ways = WAYS[kind]
    return ways[int((now - since) // SWITCH_S) % len(ways)]


def now(number: int) -> tuple[str, str, str] | None:
    """What panel ``number`` shows now: (the letter, the kind, the value for it), or None: its
    theme."""
    moment = clock()
    own, told = _own, _told
    if own is not None and own[0] == number and moment < own[2]:
        letter, value = _phase(own[3], own[1], moment)
        return letter, own[3], value
    if told is not None and told[0] == number and moment < told[4]:
        return told[1], told[2], told[3]
    return None


def state() -> dict[str, Any] | None:
    """The check under way here: its panel, kind, letter, value and the seconds left; None for
    none."""
    own = _own
    moment = clock()
    if own is None or moment >= own[2]:
        return None
    letter, value = _phase(own[3], own[1], moment)
    return {"panel": own[0], "kind": own[3], "letter": letter, "value": value,
            "left_s": own[2] - moment}  # fmt: skip


def export() -> dict[str, Any] | None:
    """What a panel's own process needs to show it: :func:`state` with the seconds left
    rounded up (so that it changes once a second, and at each switch)."""
    found = state()
    if found is not None:
        found["left_s"] = math.ceil(found["left_s"])
    return found


def adopt(told: dict[str, Any] | None) -> None:
    """In a panel's own process: what the first panel's process said (:func:`export`)."""
    global _told
    if not isinstance(told, dict):
        _told = None
        return
    try:
        _told = (int(told["panel"]), str(told["letter"]), str(told["kind"]), str(told["value"]),
                 clock() + float(told["left_s"]))  # fmt: skip
    except (KeyError, TypeError, ValueError):
        _told = None


def picture(size: tuple[int, int], letter: str) -> Image.Image:
    """The check's test picture at ``size`` with ``letter`` in its corner."""
    from libre_panel.devices.colour_check import check_picture

    return check_picture(size, letter)


def recover() -> dict[str, Any] | None:
    """At a start: a marker of a check the app ended during. Nothing of the check stays (it
    wrote nothing but the marker); the marker goes, and what it said comes back for the log."""
    path = _marker()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    try:
        found = json.loads(text)
    except ValueError:
        found = {}
    found = found if isinstance(found, dict) else {}
    log.info(
        "colour check: the app ended while panel %s showed the test picture; it shows its "
        "theme and feeds ffmpeg as set",
        found.get("panel", "?"),
    )
    return found

"""A transition shown on the panel, to see what it looks like (Settings → Panel → Transitions →
Show on the panel): the screen goes over to a card with the transition's name, stays a moment,
and comes back, with the transition both ways, at the speed set. Every panel shows it at once;
a panel's own process takes the request with the state the first panel's sends it (as
identify.py does), and a calm panel (USB serial) just shows the card.
"""

from __future__ import annotations

import itertools
import threading
import time
from functools import lru_cache
from typing import Any

from PIL import Image

HOLD_S = 1.2  # the card stays this long between the two ways
GIVE_UP_S = 30.0  # a request not taken up by then is forgotten

_lock = threading.Lock()
_numbers = itertools.count(1)
_asked: tuple[int, str, str, float] | None = None  # (number, name, label, when, monotonic)


def show(name: str, label: str = "", age: float = 0.0, number: int | None = None) -> int:
    """Show the transition ``name`` (its words for the card: ``label``) on the panels; a panel's
    own process passes on the first's request as it came (``number``, ``age``)."""
    global _asked
    with _lock:
        number = number if number is not None else next(_numbers)
        _asked = (number, name, label or name, time.monotonic() - age)
        return number


def asked() -> tuple[int, str, str] | None:
    """The request on now: ``(number, name, label)``; None for none."""
    with _lock:
        if _asked is None or time.monotonic() - _asked[3] > GIVE_UP_S:
            return None
        return _asked[:3]


def done(number: int) -> None:
    """The panel has shown it: the request ends (a newer one stays)."""
    global _asked
    with _lock:
        if _asked is not None and _asked[0] == number:
            _asked = None


def export() -> tuple[int, str, str, float] | None:
    """The request, for a panel's own process: ``(number, name, label, age)``."""
    with _lock:
        if _asked is None:
            return None
        return (*_asked[:3], time.monotonic() - _asked[3])


def adopt(state: Any) -> None:
    """In a panel's own process: the first panel's request (:func:`export`), once each."""
    if not isinstance(state, (list, tuple)) or len(state) != 4:
        return
    number, name, label, age = state
    with _lock:
        if _asked is not None and _asked[0] == number:
            return
    show(str(name), str(label), float(age), int(number))


@lru_cache(maxsize=4)
def card(label: str, size: tuple[int, int], void: tuple[int, int, int],
         ink: tuple[int, int, int]) -> Image.Image:  # fmt: skip
    """The card in the middle of the show: the transition's name, large, on the theme's dark."""
    from libre_panel.render.transitions import _lettering

    w, h = size
    u = min(w, h) / 480
    out = Image.new("RGB", size, void)
    lettering = _lettering(label.upper(), max(16, w - round(120 * u)), round(110 * u), u, ink)
    out.paste(lettering, ((w - lettering.width) // 2, (h - lettering.height) // 2), lettering)
    return out


class Show:
    """A show on one panel: the frame it began on, held, the card, and the transition each way
    (``make()`` makes one: None for a cut)."""

    def __init__(self, number: int, frame: Image.Image, card_image: Image.Image, make: Any) -> None:
        self.number = number
        self.held = frame.copy()
        self.card = card_image
        self.out, self.back = make(), make()
        self.began: float | None = None

    @property
    def seconds(self) -> float:
        return 2 * self._duration() + HOLD_S

    def _duration(self) -> float:
        return float(getattr(self.out, "duration", 0.0) or 0.0)

    def frame(self, live: Image.Image, now: float) -> Image.Image | None:
        """What the panel shows at ``now`` while the show goes on; None once it is over."""
        if self.began is None:
            self.began = now
        s, d = now - self.began, self._duration()
        if self.card.size != live.size:
            return None
        if s < d:
            return self.out.frame(self.held, self.card, s / d)
        if s < d + HOLD_S:
            return self.card
        if d and s < 2 * d + HOLD_S:
            return self.back.frame(self.card, live, (s - d - HOLD_S) / d)
        return None

"""What the main loop draws over a finished frame: toasts, and transitions between themes.

Toasts are short messages from services (``host.notify``). They are drawn
into the frame, not sent as a separate PNG layer: on the TURZX video path
more than about two overlays a second make the video judder.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import replace
from typing import Any

from PIL import Image, ImageDraw

from libre_panel.fonts.fallback import truetype
from libre_panel.icons import ICON_NAMES, draw_icon
from libre_panel.plugins.render import RenderContext, ToastStyle, Transition
from libre_panel.render.band import BandStyle
from libre_panel.render.light import accent_color, ellipsize, role_value
from libre_panel.render.transitions import Entrance, Tube

log = logging.getLogger(__name__)

SS = 3  # supersampling for smooth corners
SWITCH_POLICIES = ("wait", "follow", "restart")


class ToastLayer:
    """Shows toasts in the theme's toast style (the built-in card unless the
    theme names another).

    - A toast with the key of the one on show (``key``, else its ``kind``)
      replaces it in place, whatever its rank: turning the volume refreshes
      one toast instead of queueing three. Waiting ones with that key collapse
      to the newest.
    - With the theme's ``toast.queue`` on (the default) a higher rank takes
      over at once and the others wait, by rank, then in order. With it off,
      toasts are decided one by one as they come: the same or a higher rank
      takes over at once, a lower one is dropped.
    - Kinds the theme switches off (``toast.off``) or the screen shows anyway
      (``Screen.suppresses``) are left out when they come. One on show when
      such a screen comes goes on unseen (it still holds back lower ranks)
      and shows again after it.
    - While a transition plays (``hold``) no new toast starts; the one on show
      stays, unless the transition set it aside to come again afterwards. One
      that hides toasts (``draw(..., hidden=True)``) lets them be decided as
      usual: the one on show starts again on the frame after it.

    The style learns which toast one replaced (``previous``), so it can change
    the content in place instead of rolling in again, and says how long each
    toast stays (``ToastStyle.lifetime``).
    """

    MAX_QUEUE = 20

    def __init__(self, renderer: Any) -> None:
        self.inbox: list[Any] = []  # arrived, not yet sorted in
        self.queue: list[Any] = []
        # (toast, when it began, (the toast it replaced, its age then) or None)
        self.current: tuple[Any, float, tuple[Any, float] | None] | None = None
        self.ends = 0.0  # when the current toast is gone (ToastStyle.lifetime)
        self.style: Any = None
        self.settings: dict[str, Any] = {}
        self._size: tuple[int, int] | None = None
        self._failed: str | None = None  # the last failure logged (not 50 times a second)
        self.set_renderer(renderer)

    def set_renderer(self, renderer: Any) -> None:
        """A new theme (and its renderer): its style, hold time and switched-off
        kinds. The style goes on, with what it keeps per toast, while the theme
        names the same one with the same options at the same size."""
        theme = renderer.theme
        settings, size = theme.toast, (theme.width, theme.height)
        screen = getattr(renderer, "screen", None)
        self.off = frozenset(settings["off"])  # the theme switches these kinds off
        self.left_out = frozenset(getattr(screen, "suppresses", ()) or ())  # the screen shows them
        self.suppressed = set(self.off | self.left_out)
        same = (settings["style"], settings["options"], size) == (
            self.settings.get("style"),
            self.settings.get("options"),
            self._size,
        )
        self.settings, self._size = settings, size
        if self.style is not None and same:
            self.style.context = RenderContext(renderer, type(self.style))
            self.style.anchor = settings["anchor"]
            return
        self.close()
        self.style = self._make_style(renderer)

    def _make_style(self, renderer: Any) -> Any:
        name = self.settings["style"]
        cls = toast_style(name)
        if cls is None:
            log.warning("toast style %r is not installed; using the built-in card", name)
            cls = CardStyle
        try:
            style = cls(RenderContext(renderer, cls), dict(self.settings["options"]))
        except Exception:
            log.exception("toast style %r failed to start; using the built-in card", name)
            style = CardStyle(RenderContext(renderer, CardStyle), {})
        style.anchor = self.settings["anchor"]
        try:  # styles written before ``previous`` existed still work
            parameters = inspect.signature(style.draw).parameters.values()
            self._previous = any(
                p.name == "previous" or p.kind is p.VAR_KEYWORD for p in parameters
            )
        except (TypeError, ValueError):
            self._previous = False
        return style

    @staticmethod
    def key_of(toast: Any) -> str:
        return toast.key or toast.kind

    def add(self, toasts: list[Any]) -> None:
        self.inbox.extend(toasts)

    @property
    def active(self) -> bool:
        """True while a toast shows or waits: the frame keeps changing."""
        return self.current is not None or bool(self.queue) or bool(self.inbox)

    def set_aside(self) -> None:
        """A transition begins that the toast on show must not cover: it leaves
        now and comes again, from the start, once the transition is over."""
        if self.current is not None:
            self.queue.insert(0, self.current[0])
            self.current = None

    def showing(self, now: float) -> tuple[Any, float] | None:
        """The toast on show and its age (for screens: ``context.toast``); None
        while the screen leaves its kind out."""
        if self.current is None or self.current[0].kind in self.suppressed:
            return None
        return self.current[0], now - self.current[1]

    def advance(self, now: float, hold: bool = False) -> None:
        """Sort in what arrived and decide which toast shows at ``now``. With
        ``hold`` (a transition plays) no new toast starts."""
        if self.current is not None and now >= self.ends:
            self.current = None
        arrived, self.inbox = self.inbox, []
        if hold or self.settings.get("queue", True):
            for toast in arrived:
                self._arrive(toast, now)
            arrived = []
        if not hold and self.queue:
            self._from_queue(now)
        for toast in arrived:  # no queue: one by one, in the order they came
            self._take(toast, now)

    def _prepared(self, toast: Any) -> Any:
        """The toast as it will show, or None when this screen leaves its kind out."""
        if toast.kind in self.suppressed:
            return None
        if toast.seconds is None:
            theme_seconds = self.settings["seconds"]
            seconds = self._ask("hold_s", theme_seconds, toast, theme_seconds)
            toast = replace(toast, seconds=max(0.0, seconds))
        return toast

    def _arrive(self, toast: Any, now: float) -> None:
        toast = self._prepared(toast)
        if toast is None:
            return
        key = self.key_of(toast)
        if key and self.current is not None and self.key_of(self.current[0]) == key:
            self._show(toast, now)  # the same thing again: in place, whatever its rank
            return
        if key:
            same = [i for i, q in enumerate(self.queue) if self.key_of(q) == key]
            if same:  # waiting ones with this key collapse to the newest
                self.queue[same[0]] = toast
                for i in reversed(same[1:]):
                    del self.queue[i]
                return
        # after those of the same or a higher rank
        at = next((i for i, q in enumerate(self.queue) if q.rank < toast.rank), len(self.queue))
        self.queue.insert(at, toast)
        del self.queue[self.MAX_QUEUE :]  # the lowest ranks, newest first, go

    def _from_queue(self, now: float) -> None:
        shown = self.current[0] if self.current is not None else None
        if self.settings.get("queue", True):
            if shown is None or self.queue[0].rank > shown.rank:
                self._show(self.queue.pop(0), now)
            return
        # no queue, after a hold: the newest of the highest rank, if it is not lower
        best = max(reversed(self.queue), key=lambda q: q.rank)
        self.queue.clear()
        if shown is None or best.rank >= shown.rank:
            self._show(best, now)

    def _take(self, toast: Any, now: float) -> None:
        """No queue: the same key replaces the one on show, the same or a higher
        rank takes over from it, a lower one is dropped."""
        toast = self._prepared(toast)
        if toast is None:
            return
        shown = self.current[0] if self.current is not None else None
        key = self.key_of(toast)
        if shown is None or (key and key == self.key_of(shown)) or toast.rank >= shown.rank:
            self._show(toast, now)

    def _show(self, toast: Any, now: float) -> None:
        current = self.current
        self._start(toast, now, (current[0], now - current[1]) if current is not None else None)

    def _start(self, toast: Any, now: float, previous: tuple[Any, float] | None) -> None:
        self.current = (toast, now, previous)
        fallback = toast.seconds + getattr(self.style, "leave_s", 0.25)
        self.ends = now + self._ask("lifetime", fallback, toast, previous)

    def _ask(self, method: str, fallback: float, *args: Any) -> float:
        """A number from the style (``lifetime``, ``hold_s``); a style that fails
        must not stop the toasts."""
        try:
            return float(getattr(self.style, method)(*args))
        except Exception as exc:
            message = f"{method}: {type(exc).__name__}: {exc}"
            if message != self._failed:
                self._failed = message
                log.error("toast style %r failed in %s", getattr(self.style, "name", "?"), message)
            return fallback

    def draw(self, frame: Image.Image, now: float, hidden: bool = False) -> Image.Image:
        """The frame with the toast on show drawn on it. ``hidden``: a transition
        that hides toasts plays; the toast on show starts again after it."""
        if self.current is None:
            return frame
        toast, began, previous = self.current
        if toast.kind in self.suppressed:  # the screen shows it anyway: it goes on unseen
            return frame
        if hidden:
            self._start(toast, now, None)
            return frame
        try:
            if not self._previous:
                return self.style.draw(frame, toast, now - began)
            return self.style.draw(frame, toast, now - began, previous=previous)
        except Exception as exc:  # a broken style must not blank the panel
            message = f"{type(exc).__name__}: {exc}"
            if message != self._failed:
                self._failed = message
                log.exception("toast style %r failed", self.style.name)
            return frame

    def apply(self, frame: Image.Image, now: float, hold: bool = False) -> Image.Image:
        """:meth:`advance`, then :meth:`draw`."""
        self.advance(now, hold)
        return self.draw(frame, now)

    def close(self) -> None:
        if self.style is not None:
            try:
                self.style.close()
            except Exception:
                log.exception("toast style %r failed while closing", self.style.name)
            self.style = None


class CardStyle(ToastStyle):
    """The built-in look: a card with an accent stripe, an icon and the text,
    fading and sliding in next to the anchor, clear of what the bezel hides."""

    name = "card"
    api = 1
    label = {"en": "Card", "de": "Karte"}
    FADE_IN_S = 0.18
    leave_s = 0.25
    anchor = "top-right"

    def __init__(self, context: Any, options: dict[str, Any]) -> None:
        super().__init__(context, options)
        self._card: tuple[Any, Any, Image.Image] | None = None  # toast, theme, its card

    def draw(self, frame: Image.Image, toast: Any, age: float, previous: Any = None) -> Image.Image:
        leaving = (toast.seconds + self.leave_s - age) / self.leave_s
        arriving = 1.0 if previous is not None else age / self.FADE_IN_S  # replaced in place
        fade = max(0.0, min(1.0, arriving, leaving))
        if self._card is None or self._card[0] is not toast or self._card[1] is not self.context:
            self._card = (toast, self.context, self._draw(toast, frame.size))
        card = self._card[2]
        x, y = self._position(frame.size, card.size, 1.0 - fade)
        alpha = card.getchannel("A").point(lambda a: int(a * fade))
        out = frame.copy()
        out.paste(card.convert(frame.mode), (x, y), alpha)
        return out

    def _role(self, role: str) -> str:
        return role_value(self.context.palette, role)

    def _draw(self, toast: Any, size: tuple[int, int]) -> Image.Image:
        unit = max(12, min(size) // 16)  # 30 px on a 480 px high panel
        font = self.context.font("", unit * SS)
        if getattr(font, "path", None):  # a face of its own: the renderer's helper thread
            font = truetype(font.path, unit * SS)  # may use the shared one
        text = toast.text if len(toast.text) <= 120 else toast.text[:119] + "…"
        pad, gap, bar = unit * SS * 2 // 3, unit * SS // 2, max(3, unit // 8) * SS
        icon_size = unit * SS if toast.icon in ICON_NAMES else 0
        max_width = size[0] * SS * 2 // 3
        room = max_width - icon_size - 2 * pad - gap - bar  # what the text may take of the card
        text = ellipsize(text, font, room)  # (it was cut off at the edge of the card)
        left, top, right, bottom = font.getbbox(text)
        text_w = max(1, min(right - left, room))
        width = bar + pad + (icon_size + gap if icon_size else 0) + text_w + pad
        height = max(icon_size, unit * SS) + 2 * pad
        card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        radius = unit * SS // 2
        background = self._role("card")
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius, fill=background[:7])
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius, outline="#ffffff1c", width=max(1, SS)
        )
        accent = accent_color(self.context, toast)
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, height - 1], radius, fill=255)
        stripe = Image.new("RGBA", (bar, height), accent)
        card.paste(stripe, (0, 0), mask.crop((0, 0, bar, height)))
        x = bar + pad
        if icon_size:
            icon = draw_icon(toast.icon, icon_size, accent)  # stroke is in grid units
            card.alpha_composite(icon, (x, (height - icon_size) // 2))
            x += icon_size + gap
        color = self._role("text")
        text_layer = Image.new("RGBA", (text_w, height), (0, 0, 0, 0))
        ImageDraw.Draw(text_layer).text(
            (-left, height // 2), text, font=font, fill=color, anchor="lm"
        )
        card.alpha_composite(text_layer, (x, 0))
        return card.resize((width // SS, height // SS), Image.Resampling.LANCZOS)

    def _position(
        self, frame: tuple[int, int], card: tuple[int, int], slide: float
    ) -> tuple[int, int]:
        (fw, fh), (cw, ch) = frame, card
        margin = max(10, min(frame) // 24)
        hidden = dict.fromkeys(("top", "right", "bottom", "left"), 0)
        if self.context.model is not None:  # keep clear of what the bezel hides
            hidden = self.context.model.hidden_edges("landscape" if fw > fh else "portrait")
        shift = round(slide * margin)
        if self.anchor.startswith("top"):
            y = hidden["top"] + margin - shift
        else:
            y = fh - hidden["bottom"] - margin - ch + shift
        if self.anchor.endswith("right"):
            x = fw - hidden["right"] - margin - cw
        elif self.anchor.endswith("left"):
            x = hidden["left"] + margin
        else:
            x = (fw - cw) // 2
        return x, y


# -- transitions -----------------------------------------------------------------


class _Cut(Transition):
    name = "cut"
    duration = 0.0

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        return new


class _Fade(Transition):
    name = "fade"
    duration = 0.4

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        return Image.blend(old, new, t * t * (3 - 2 * t))  # smoothstep


class _Slide(Transition):
    name = "slide"
    duration = 0.5

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        eased = 1 - (1 - t) ** 3
        shift = round(new.width * eased)
        out = Image.new(new.mode, new.size)
        out.paste(old, (-shift, 0))
        out.paste(new, (new.width - shift, 0))
        return out


BUILT_IN_TRANSITIONS = {
    "cut": _Cut,
    "fade": _Fade,
    "slide": _Slide,
    "tube": Tube,
    "entrance": Entrance,
}
BUILT_IN_STYLES = {"card": CardStyle, "band": BandStyle}


def toast_style(name: str) -> Any:
    """The toast style class a theme names (built-in or from a plugin), or None."""
    if not name:
        return CardStyle
    if name in BUILT_IN_STYLES:
        return BUILT_IN_STYLES[name]
    from libre_panel.plugins.loader import registry

    return registry().get("toasts", name)


_failed_switches: set[str] = set()  # logged once each


def switch_policy(kind: Any, theme: str, mode: str | None, began_in: str | None) -> str:
    """What a switch does while ``kind`` plays (``Transition.on_switch``):
    ``"wait"``, ``"follow"`` or ``"restart"``. ``began_in`` is the mode it began in."""
    policy = getattr(kind, "switch", "wait")
    on_switch = getattr(kind, "on_switch", None)
    if on_switch is not None:
        try:
            policy = on_switch(theme, mode, mode != began_in)
        except Exception as exc:  # a broken transition must not hold up the panel
            message = f"{getattr(kind, 'name', kind)!r}: {type(exc).__name__}: {exc}"
            if message not in _failed_switches:
                _failed_switches.add(message)
                log.error("transition %s failed to say how to switch", message)
    return policy if policy in SWITCH_POLICIES else "wait"


def transition(spec: Any, context: Any = None) -> Any:
    """A transition (built-in or from a plugin) for ``spec``: a name or
    ``(name, parameters)``; None for none."""
    name, params = spec if isinstance(spec, tuple) else (spec, {})
    if not name or name == "cut":
        return None
    cls = BUILT_IN_TRANSITIONS.get(name)
    if cls is None:
        from libre_panel.plugins.loader import registry

        cls = registry().get("transitions", name)
    if cls is None:
        log.warning("unknown transition %r; switching without one", name)
        return None
    try:
        return cls(context, dict(params or {}))
    except Exception:
        log.exception("transition %r failed to start; switching without one", name)
        return None

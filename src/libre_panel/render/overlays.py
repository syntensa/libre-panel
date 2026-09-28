"""What the main loop draws over a finished frame: toasts, and transitions between themes.

Toasts are short messages from services (``host.notify``). They are drawn
into the frame, not sent as a separate PNG layer: on the TURZX video path
more than about two overlays a second make the video judder.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from libre_panel.icons import ICON_NAMES, draw_icon
from libre_panel.plugins.render import ToastStyle, Transition

log = logging.getLogger(__name__)

# Palette names themes use for these roles, and a fallback colour.
ROLES = {
    "card": (("card", "panel", "surface", "bg2"), "#101722"),
    "text": (("text",), "#F1F5F9"),
    "info": (("accent", "primary"), "#22D3EE"),
    "warning": (("warn", "warning"), "#FBBF24"),
    "error": (("crit", "error", "danger"), "#F87171"),
}
SS = 3  # supersampling for smooth corners


class ToastLayer:
    """Shows queued toasts one after the other, each for its own time, in the
    theme's toast style (the built-in card unless the theme names a plugin's).

    A toast with a higher rank replaces the one on show; the others queue by
    rank, then in order. Kinds the theme switches off (``toast.off``) or the
    screen shows anyway (``Screen.suppresses``) are left out.
    """

    MAX_QUEUE = 20

    def __init__(self, renderer: Any) -> None:
        self.queue: list[Any] = []
        self.current: tuple[Any, float] | None = None  # (toast, when it arrived)
        self.style: Any = None
        self._failed: str | None = None
        self.set_renderer(renderer)

    def set_renderer(self, renderer: Any) -> None:
        """A new theme (and its renderer): its style, hold time and switched-off kinds."""
        self.close()
        theme = renderer.theme
        self.settings = theme.toast
        screen = getattr(renderer, "screen", None)
        self.suppressed = set(self.settings["off"]) | set(getattr(screen, "suppresses", ()) or ())
        self.style = self._make_style(renderer)
        if self.current is not None and self.current[0].kind in self.suppressed:
            self.current = None

    def _make_style(self, renderer: Any) -> Any:
        from libre_panel.plugins.render import RenderContext

        name = self.settings["style"]
        cls: Any = CardStyle
        if name and name != CardStyle.name:
            from libre_panel.plugins.loader import registry

            cls = registry().get("toasts", name) or CardStyle
            if cls is CardStyle:
                log.warning("toast style %r is not installed; using the built-in card", name)
        try:
            style = cls(RenderContext(renderer, cls), dict(self.settings["options"]))
        except Exception:
            log.exception("toast style %r failed to start; using the built-in card", name)
            style = CardStyle(RenderContext(renderer, CardStyle), {})
        style.anchor = self.settings["anchor"]
        return style

    def add(self, toasts: list[Any]) -> None:
        for toast in toasts:
            # after those of the same or a higher rank
            at = next((i for i, q in enumerate(self.queue) if q.rank < toast.rank), len(self.queue))
            self.queue.insert(at, toast)
        del self.queue[self.MAX_QUEUE :]  # the lowest ranks, newest first, go

    @property
    def active(self) -> bool:
        """True while a toast shows or waits: the frame keeps changing."""
        return self.current is not None or bool(self.queue)

    def apply(self, frame: Image.Image, now: float, hold: bool = False) -> Image.Image:
        """The frame with the current toast on it. With ``hold`` (a transition
        plays) no new toast starts."""
        waiting = next((q for q in self.queue if q.kind not in self.suppressed), None)
        if self.current is not None and waiting is not None:
            if waiting.rank > self.current[0].rank:
                self.current = None  # a higher rank takes over at once
        while True:
            if self.current is None:
                if hold or not self.queue:
                    return frame
                toast = self.queue.pop(0)
                if toast.kind in self.suppressed:
                    continue
                if toast.seconds is None:
                    toast = replace(toast, seconds=self.settings["seconds"])
                self.current = (toast, now)
            toast, began = self.current
            age = now - began
            if age < toast.seconds + self.style.leave_s:
                break
            self.current = None
        try:
            return self.style.draw(frame, toast, age)
        except Exception as exc:  # a broken style must not blank the panel
            message = f"{type(exc).__name__}: {exc}"
            if message != self._failed:
                self._failed = message
                log.exception("toast style %r failed", self.style.name)
            return frame

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
        self._card: tuple[Any, Image.Image] | None = None

    def draw(self, frame: Image.Image, toast: Any, age: float) -> Image.Image:
        leaving = (toast.seconds + self.leave_s - age) / self.leave_s
        fade = max(0.0, min(1.0, age / self.FADE_IN_S, leaving))
        if self._card is None or self._card[0] is not toast:
            self._card = (toast, self._draw(toast, frame.size))
        card = self._card[1]
        x, y = self._position(frame.size, card.size, 1.0 - fade)
        alpha = card.getchannel("A").point(lambda a: int(a * fade))
        out = frame.copy()
        out.paste(card.convert(frame.mode), (x, y), alpha)
        return out

    def _role(self, role: str) -> str:
        names, fallback = ROLES[role]
        palette = self.context.palette
        return next((palette[n] for n in names if palette.get(n)), fallback)

    def _draw(self, toast: Any, size: tuple[int, int]) -> Image.Image:
        unit = max(12, min(size) // 16)  # 30 px on a 480 px high panel
        font = self.context.font("", unit * SS)
        if getattr(font, "path", None):  # a face of its own: the renderer's helper thread
            font = ImageFont.truetype(font.path, unit * SS)  # may use the shared one
        text = toast.text if len(toast.text) <= 120 else toast.text[:119] + "…"
        pad, gap, bar = unit * SS * 2 // 3, unit * SS // 2, max(3, unit // 8) * SS
        icon_size = unit * SS if toast.icon in ICON_NAMES else 0
        left, top, right, bottom = font.getbbox(text)
        max_width = size[0] * SS * 2 // 3
        text_w = min(right - left, max_width - icon_size - 2 * pad - gap - bar)
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
        accent = toast.payload.get("color") or self._role(
            toast.level if toast.level in ROLES else "info"
        )
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


BUILT_IN_TRANSITIONS = {"cut": _Cut, "fade": _Fade, "slide": _Slide}


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

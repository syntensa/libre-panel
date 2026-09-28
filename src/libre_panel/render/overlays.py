"""What the main loop draws over a finished frame: toasts, and transitions between themes.

Toasts are short messages from services (``host.notify``). They are drawn
into the frame, not sent as a separate PNG layer: on the TURZX video path
more than about two overlays a second make the video judder.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from libre_panel.devices.models import find_model
from libre_panel.fonts import DEFAULT_FONT, builtin_font_path
from libre_panel.icons import ICON_NAMES, draw_icon

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


def _role(theme: Any, role: str) -> str:
    names, fallback = ROLES[role]
    return next((theme.palette[n] for n in names if theme.palette.get(n)), fallback)


class ToastLayer:
    """Shows queued toasts one after the other, each for its own time."""

    FADE_IN_S, FADE_OUT_S = 0.18, 0.25
    MAX_QUEUE = 20

    def __init__(self, theme: Any, anchor: str = "top-right") -> None:
        self.theme = theme
        self.anchor = anchor
        self.queue: deque[Any] = deque(maxlen=self.MAX_QUEUE)
        self.current: tuple[Any, float] | None = None
        self._card: tuple[Any, Image.Image] | None = None

    def set_theme(self, theme: Any, anchor: str = "top-right") -> None:
        self.theme, self.anchor, self._card = theme, anchor, None

    def add(self, toasts: list[Any]) -> None:
        self.queue.extend(toasts)

    @property
    def active(self) -> bool:
        """True while a toast shows or waits: the frame keeps changing."""
        return self.current is not None or bool(self.queue)

    def apply(self, frame: Image.Image, now: float) -> Image.Image:
        while True:
            if self.current is None:
                if not self.queue:
                    return frame
                self.current = (self.queue.popleft(), now)
            toast, began = self.current
            age = now - began
            if age < toast.seconds + self.FADE_OUT_S:
                break
            self.current = None
        leaving = (toast.seconds + self.FADE_OUT_S - age) / self.FADE_OUT_S
        fade = max(0.0, min(1.0, age / self.FADE_IN_S, leaving))
        card = self._card_for(toast, frame.size)
        x, y = self._position(frame.size, card.size, 1.0 - fade)
        alpha = card.getchannel("A").point(lambda a: int(a * fade))
        out = frame.copy()
        out.paste(card.convert(frame.mode), (x, y), alpha)
        return out

    # -- drawing ---------------------------------------------------------------

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        ref = self.theme.font or DEFAULT_FONT
        path = builtin_font_path(ref) or builtin_font_path(DEFAULT_FONT)
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            return ImageFont.load_default()

    def _card_for(self, toast: Any, size: tuple[int, int]) -> Image.Image:
        if self._card is not None and self._card[0] == (toast, size):
            return self._card[1]
        card = self._draw(toast, size)
        self._card = ((toast, size), card)
        return card

    def _draw(self, toast: Any, size: tuple[int, int]) -> Image.Image:
        theme = self.theme
        unit = max(12, min(size) // 16)  # 30 px on a 480 px high panel
        font = self._font(unit * SS)
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
        background = _role(theme, "card")
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius, fill=background[:7])
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius, outline="#ffffff1c", width=max(1, SS)
        )
        accent = _role(theme, toast.level if toast.level in ROLES else "info")
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, height - 1], radius, fill=255)
        stripe = Image.new("RGBA", (bar, height), accent)
        card.paste(stripe, (0, 0), mask.crop((0, 0, bar, height)))
        x = bar + pad
        if icon_size:
            icon = draw_icon(toast.icon, icon_size, accent)  # stroke is in grid units
            card.alpha_composite(icon, (x, (height - icon_size) // 2))
            x += icon_size + gap
        color = _role(theme, "text")
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
        model = find_model(getattr(self.theme, "model", ""))
        if model is not None:  # keep clear of what the bezel hides
            hidden = model.hidden_edges("landscape" if fw > fh else "portrait")
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


class _Cut:
    name = "cut"
    duration = 0.0

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        return new


class _Fade:
    name = "fade"
    duration = 0.4

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        return Image.blend(old, new, t * t * (3 - 2 * t))  # smoothstep


class _Slide:
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


def transition(name: str | None) -> Any:
    """A transition by name (built-in or from a plugin), or None for none."""
    if not name or name == "cut":
        return None
    cls = BUILT_IN_TRANSITIONS.get(name)
    if cls is None:
        from libre_panel.plugins.loader import registry

        cls = registry().get("transitions", name)
    if cls is None:
        log.warning("unknown transition %r; switching without one", name)
        return None
    return cls()

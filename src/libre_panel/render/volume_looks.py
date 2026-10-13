"""The looks of the volume message, chosen for every screen at once (``[services.notices]``
``volume_look``): a bar along the bottom edge, levels as on a mixer, a ring, and a large
number in the middle. The notices service puts the look in the volume message's payload
(``"look"``); the toast layer draws that message in it and every other message in the theme's
toast style. A screen that shows the volume itself (Encore) leaves the message out, whatever
its look.

Each look fades and slides in, changes in place while the volume turns (its bar glides to the
new value), and keeps clear of what the panel's frame hides. Sizes are for a panel 480 px high
and scale with the shorter side of the frame, so the looks fit a 3.5" panel and a bar on end.
"""

from __future__ import annotations

import time
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from libre_panel.icons import draw_icon
from libre_panel.plugins.render import ToastStyle
from libre_panel.render.light import RGB, accent_color, ease_out, face, mix, role_color

SS = 3  # drawn this much larger, then made smaller: smooth edges
NUMBER_FACE, LABEL_FACE = "Barlow-Bold", "Barlow-SemiBold"
GLIDE_S = 0.16  # the bar's time to reach a new value


class VolumeLook(ToastStyle):
    """What the looks share: the fade, the glide of the level, the place on the panel and the
    picture of one message, made again only when what it shows changes."""

    api = 1
    leave_s = 0.3
    FADE_IN_S = 0.18
    anchor = "top-right"

    def __init__(self, context: Any, options: dict[str, Any]) -> None:
        super().__init__(context, options)
        self._level: float | None = None  # the share shown, gliding to the message's
        self._from = 0.0
        self._to = 0.0
        self._since = 0.0
        self._made: tuple[Any, Image.Image] | None = None  # (what it shows, its picture)

    # -- what a message says -------------------------------------------------------------

    @staticmethod
    def share(toast: Any) -> float:
        value = toast.payload.get("progress", 0.0)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def muted(toast: Any) -> bool:
        return bool(toast.payload.get("muted"))

    def unit(self, size: tuple[int, int]) -> float:
        return max(0.5, min(size) / 480)

    def colors(self, toast: Any) -> dict[str, RGB]:
        accent = accent_color(self.context, toast)
        return {
            "accent": role_color(self.context, "muted") if self.muted(toast) else accent,
            "card": role_color(self.context, "card"),
            "text": role_color(self.context, "text"),
            "muted": role_color(self.context, "muted"),
            "track": role_color(self.context, "track"),
            "rule": role_color(self.context, "rule"),
        }

    # -- drawing ---------------------------------------------------------------------------

    def draw(self, frame: Image.Image, toast: Any, age: float, previous: Any = None) -> Image.Image:
        level = self._glide(toast, previous)
        leaving = (toast.seconds + self.leave_s - age) / self.leave_s
        arriving = 1.0 if previous is not None else age / self.FADE_IN_S  # replaced in place
        shown = max(0.0, min(1.0, arriving, leaving))
        if shown <= 0.0:
            return frame
        key = (toast.text, self.muted(toast), round(level, 3), frame.size, id(self.context))
        if self._made is None or self._made[0] != key:
            self._made = (key, self.picture(toast, level, frame.size))
        picture = self._made[1]
        x, y = self.place(frame.size, picture.size, 1.0 - ease_out(shown))
        alpha = picture.getchannel("A").point(lambda a: int(a * shown))
        out = frame.copy()
        out.paste(picture.convert(frame.mode), (x, y), alpha)
        return out

    def _glide(self, toast: Any, previous: Any) -> float:
        """The level to show now: the message's, reached in GLIDE_S from where it was."""
        target = 0.0 if self.muted(toast) else self.share(toast)
        now = time.monotonic()
        if self.context is not None and self.context.preview:
            self._level = target  # a still picture shows where it ends
            return target
        if self._level is None or previous is None:
            self._level, self._from, self._to, self._since = target, target, target, now
        elif target != self._to:
            self._from, self._to, self._since = self._level, target, now
        u = ease_out((now - self._since) / GLIDE_S)
        self._level = self._from + (self._to - self._from) * u
        return self._level

    def hidden(self, size: tuple[int, int]) -> dict[str, int]:
        hidden = dict.fromkeys(("top", "right", "bottom", "left"), 0)
        model = getattr(self.context, "model", None)
        if model is not None:  # keep clear of what the frame of the panel hides
            hidden = model.hidden_edges("landscape" if size[0] > size[1] else "portrait")
        return hidden

    def place(self, frame: tuple[int, int], size: tuple[int, int], slide: float) -> tuple[int, int]:
        """Where the picture goes, ``slide`` (0 to 1) of a short way from it while it comes
        and goes: at the theme's toast position."""
        (fw, fh), (w, h) = frame, size
        hidden = self.hidden(frame)
        margin = max(10, min(frame) // 24)
        shift = round(slide * margin)
        if self.anchor.startswith("top"):
            y = hidden["top"] + margin - shift
        else:
            y = fh - hidden["bottom"] - margin - h + shift
        if self.anchor.endswith("right"):
            x = fw - hidden["right"] - margin - w
        elif self.anchor.endswith("left"):
            x = hidden["left"] + margin
        else:
            x = (fw - w) // 2
        return x, y

    def picture(self, toast: Any, level: float, size: tuple[int, int]) -> Image.Image:
        raise NotImplementedError

    # -- parts -----------------------------------------------------------------------------

    def panel(self, w: int, h: int, colors: dict[str, RGB], radius: int) -> Image.Image:
        """A card of ``w`` x ``h`` (drawn sizes) with a fine light edge."""
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=(*colors["card"], 238))
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius, outline=(255, 255, 255, 30), width=SS)
        return card

    def speaker(self, toast: Any, size: int, color: RGB) -> Image.Image:
        return draw_icon("mute" if self.muted(toast) else "volume", size, color)

    def speaker_at(self, card: Image.Image, toast: Any, size: int, x: int, color: RGB) -> None:
        """The speaker at ``x``, in the middle of the card's height."""
        card.alpha_composite(self.speaker(toast, size, color), (x, (card.height - size) // 2))

    def bar(self, card: Image.Image, span: tuple[int, int], y: int, thick: int, level: float,
            colors: dict[str, RGB], glow: float) -> None:  # fmt: skip
        """The groove from ``span[0]`` to ``span[1]`` at ``y``, filled to ``level``."""
        x0, x1 = span
        box = [x0, y - thick // 2, x1, y + thick // 2]
        ImageDraw.Draw(card).rounded_rectangle(box, thick // 2, fill=colors["track"])
        if level > 0.0:
            fill = Image.new("RGBA", card.size, (0, 0, 0, 0))
            box[2] = x0 + max(thick, round((x1 - x0) * level))
            ImageDraw.Draw(fill).rounded_rectangle(box, thick // 2, fill=colors["accent"])
            card.alpha_composite(self.glow(fill, glow))

    def number(self, toast: Any) -> str:
        return toast.text  # "42", or "Muted"

    @staticmethod
    def glow(layer: Image.Image, radius: float) -> Image.Image:
        """``layer`` with a soft glow of itself beneath it."""
        soft = layer.filter(ImageFilter.GaussianBlur(max(1.0, radius)))
        out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        out.alpha_composite(soft)
        out.alpha_composite(layer)
        return out

    @staticmethod
    def small(picture: Image.Image) -> Image.Image:
        w, h = picture.size
        return picture.resize((max(1, w // SS), max(1, h // SS)), Image.Resampling.LANCZOS)


class BarLook(VolumeLook):
    """A slim bar along the bottom edge, the speaker before it and the number after it: it
    covers least of the screen."""

    name = "bar"
    label = {
        "en": "Bar",
        "de": "Leiste",
        "fr": "Barre",
        "es": "Barra",
        "pl": "Pasek",
        "pt": "Barra",
    }

    def place(self, frame: tuple[int, int], size: tuple[int, int], slide: float) -> tuple[int, int]:
        fw, fh = frame
        hidden = self.hidden(frame)
        margin = max(8, min(frame) // 40)
        x = hidden["left"] + (fw - hidden["left"] - hidden["right"] - size[0]) // 2
        return x, fh - hidden["bottom"] - margin - size[1] + round(slide * size[1])

    def picture(self, toast: Any, level: float, size: tuple[int, int]) -> Image.Image:
        u, c = self.unit(size) * SS, self.colors(toast)
        hidden = self.hidden(size)
        room = size[0] - hidden["left"] - hidden["right"] - 2 * max(8, min(size) // 40)
        w, h = round(room * SS), round(56 * u)
        card = self.panel(w, h, c, round(h / 2))
        draw = ImageDraw.Draw(card)
        icon = round(28 * u)
        self.speaker_at(card, toast, icon, round(18 * u), c["accent"])
        font = face(NUMBER_FACE, round(30 * u))
        text = self.number(toast)
        text_w = round(font.getlength(text))
        draw.text((w - round(22 * u), h // 2), text, font=font, fill=c["text"], anchor="rm")
        x0, x1 = round(18 * u) + icon + round(16 * u), w - round(22 * u) - text_w - round(18 * u)
        self.bar(card, (x0, x1), h // 2, round(8 * u), level, c, 6 * u)
        return self.small(card)


class LevelsLook(VolumeLook):
    """Levels as on a mixer: bars that rise from left to right and light up to the volume,
    the loudest in the warning colour."""

    name = "levels"
    label = {
        "en": "Levels",
        "de": "Pegel",
        "fr": "Niveaux",
        "es": "Niveles",
        "pl": "Poziomy",
        "pt": "Níveis",
    }
    BARS = 16

    def picture(self, toast: Any, level: float, size: tuple[int, int]) -> Image.Image:
        u, c = self.unit(size) * SS, self.colors(toast)
        w, h = round(min(430 * u, size[0] * SS * 0.8)), round(118 * u)
        card = self.panel(w, h, c, round(18 * u))
        draw = ImageDraw.Draw(card)
        icon = round(30 * u)
        self.speaker_at(card, toast, icon, round(22 * u), c["accent"])
        font = face(NUMBER_FACE, round(38 * u))
        text = self.number(toast)
        right = w - round(22 * u)
        draw.text((right, h // 2), text, font=font, fill=c["text"], anchor="rm")
        x0 = round(22 * u) + icon + round(20 * u)
        x1 = right - round(font.getlength(text)) - round(20 * u)
        bottom, top = h - round(30 * u), round(30 * u)
        step = (x1 - x0) / self.BARS
        bar_w = max(SS, round(step * 0.62))
        warn = role_color(self.context, "warning") if not self.muted(toast) else c["accent"]
        lit = Image.new("RGBA", card.size, (0, 0, 0, 0))
        lit_draw = ImageDraw.Draw(lit)
        for i in range(self.BARS):
            share = (i + 1) / self.BARS
            bar_h = round((bottom - top) * (0.28 + 0.72 * share))
            box = [round(x0 + i * step), bottom - bar_h, round(x0 + i * step) + bar_w, bottom]
            if level >= share - 0.5 / self.BARS and level > 0:
                color = mix(warn, c["accent"], max(0.0, (share - 0.75) / 0.25))
                lit_draw.rounded_rectangle(box, bar_w // 3, fill=color)
            else:
                draw.rounded_rectangle(box, bar_w // 3, fill=c["track"])
        card.alpha_composite(self.glow(lit, 5 * u))
        return self.small(card)


class RingLook(VolumeLook):
    """A ring that fills with the volume, the number in its middle."""

    name = "ring"
    label = {
        "en": "Ring",
        "de": "Ring",
        "fr": "Anneau",
        "es": "Anillo",
        "pl": "Pierścień",
        "pt": "Anel",
    }

    def picture(self, toast: Any, level: float, size: tuple[int, int]) -> Image.Image:
        u, c = self.unit(size) * SS, self.colors(toast)
        d = round(min(170 * u, min(size) * SS * 0.8))
        card = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        edge = (255, 255, 255, 30)
        draw.ellipse([0, 0, d - 1, d - 1], fill=(*c["card"], 238), outline=edge, width=SS)
        pad, width = round(d * 0.11), max(SS, round(d * 0.07))
        box = [pad, pad, d - 1 - pad, d - 1 - pad]
        draw.arc(box, 135, 405, fill=c["track"], width=width)  # three quarters, open at the bottom
        if level > 0.0:
            arc = Image.new("RGBA", card.size, (0, 0, 0, 0))
            ImageDraw.Draw(arc).arc(box, 135, 135 + 270 * level, fill=c["accent"], width=width)
            card.alpha_composite(self.glow(arc, 6 * u))
        text = self.number(toast)
        font = face(NUMBER_FACE, round(d * (0.3 if len(text) <= 3 else 0.16)))
        draw.text((d // 2, round(d * 0.47)), text, font=font, fill=c["text"], anchor="mm")
        icon = round(d * 0.16)
        speaker = self.speaker(toast, icon, c["muted"])
        card.alpha_composite(speaker, ((d - icon) // 2, round(d * 0.66)))
        return self.small(card)


class BigLook(VolumeLook):
    """A large number in the middle of the panel with a long bar under it, as a computer's
    own volume shows: seen from across the room."""

    name = "big"
    label = {
        "en": "Large",
        "de": "Groß",
        "fr": "Grand",
        "es": "Grande",
        "pl": "Duży",
        "pt": "Grande",
    }

    def place(self, frame: tuple[int, int], size: tuple[int, int], slide: float) -> tuple[int, int]:
        fw, fh = frame
        hidden = self.hidden(frame)
        x = hidden["left"] + (fw - hidden["left"] - hidden["right"] - size[0]) // 2
        y = hidden["top"] + (fh - hidden["top"] - hidden["bottom"] - size[1]) // 2
        return x, y + round(slide * min(frame) / 24)

    def picture(self, toast: Any, level: float, size: tuple[int, int]) -> Image.Image:
        u, c = self.unit(size) * SS, self.colors(toast)
        w = round(min(560 * u, size[0] * SS * 0.9))
        h = round(min(236 * u, size[1] * SS * 0.8))
        card = self.panel(w, h, c, round(28 * u))
        draw = ImageDraw.Draw(card)
        text = self.number(toast)
        font = face(NUMBER_FACE, round(h * (0.46 if len(text) <= 3 else 0.24)))
        icon = round(h * 0.26)
        text_w = round(font.getlength(text))
        gap = round(18 * u)
        left = (w - icon - gap - text_w) // 2
        mid = round(h * 0.42)
        card.alpha_composite(self.speaker(toast, icon, c["accent"]), (left, mid - icon // 2))
        draw.text((left + icon + gap, mid), text, font=font, fill=c["text"], anchor="lm")
        span = (round(w * 0.1), round(w * 0.9))
        self.bar(card, span, round(h * 0.78), max(SS, round(12 * u)), level, c, 8 * u)
        return self.small(card)


VOLUME_LOOKS: dict[str, type[VolumeLook]] = {
    look.name: look for look in (BarLook, LevelsLook, RingLook, BigLook)
}

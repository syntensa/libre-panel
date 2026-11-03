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

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from libre_panel.icons import draw_icon
from libre_panel.plugins.render import ToastStyle
from libre_panel.render.light import RGB, accent_color, ease_out, face, mix, role_color

SS = 3  # drawn this much larger, then made smaller: smooth edges
NUMBER_FACE, LABEL_FACE = "Barlow-Bold", "Barlow-SemiBold"
GLIDE_S = 0.16  # the bar's time to reach a new value


@dataclass
class _Drawn:
    """A look's picture as it was made last (:meth:`VolumeLook.barred`): what it showed but
    the level (its key), that drawn large, with the bar filled on it, and made small."""

    key: Any
    base: Image.Image
    card: Image.Image
    small: Image.Image
    fill: tuple[int, int, int, int] | None  # the bar's filled box on the card (None: empty)
    tint: Any = None  # the colour it was filled in


def made_smaller(picture: Image.Image, size: tuple[int, int]) -> Image.Image:
    """``picture`` (RGBA) made ``size`` with LANCZOS as Pillow up to 12 makes it in one: its
    colours premultiplied by their alpha, first across, then down. Pillow 13 goes down first
    where that is quicker, by the picture's shape; a part of a picture made smaller alone
    (:meth:`VolumeLook._smaller`) then came out a level apart from that part of the whole."""
    out = picture.convert("RGBa")
    if out.width != size[0]:
        out = out.resize((size[0], out.height), Image.Resampling.LANCZOS)
    if out.height != size[1]:
        out = out.resize(size, Image.Resampling.LANCZOS)
    return out.convert("RGBA")


def faded(picture: Image.Image, shown: float) -> Image.Image:
    """``picture`` (RGBA) with the alpha a toast style lays it through while it shows
    ``shown`` (0 to 1) of itself, as its ``draw`` does (``int(a * shown)``): the picture
    itself once it shows whole."""
    if shown >= 1.0:
        return picture
    out = picture.copy()
    out.putalpha(picture.getchannel("A").point(lambda a: int(a * shown)))
    return out


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
        # (what it shows but the level, and that drawn large: :meth:`base`)
        self._base: tuple[Any, Image.Image] | None = None
        self._drawn: _Drawn | None = None  # (its picture as made last: :meth:`barred`)
        self._smalled: tuple[Image.Image, Image.Image] | None = None  # (:meth:`made_small`)

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
        laid = self._laid(frame.size, toast, age, previous)
        if laid is None:
            return frame
        picture, shown, x, y = laid
        alpha = picture.getchannel("A").point(lambda a: int(a * shown))
        out = frame.copy()
        out.paste(picture.convert(frame.mode), (x, y), alpha)
        # where it drew (ToastLayer.box): a video panel's frame is made anew there alone
        self.last_box = (x, y, x + picture.width, y + picture.height)
        return out

    def layer(
        self, size: tuple[int, int], toast: Any, age: float, previous: Any = None
    ) -> tuple[Image.Image, int, int] | None:
        """The message as :meth:`draw` lays it over a frame of ``size``: one picture (RGBA) at
        (x, y), laid through its alpha, for a frame the engine composes (ToastLayer.draw);
        None while it does not show."""
        laid = self._laid(size, toast, age, previous)
        if laid is None:
            return None
        picture, shown, x, y = laid
        self.last_box = (x, y, x + picture.width, y + picture.height)
        return faded(picture, shown), x, y

    def _laid(
        self, size: tuple[int, int], toast: Any, age: float, previous: Any
    ) -> tuple[Image.Image, float, int, int] | None:
        """Its picture, how far it shows (0 to 1) and where it goes, at ``age``; None while
        it does not show."""
        level = self._glide(toast, previous)
        leaving = (toast.seconds + self.leave_s - age) / self.leave_s
        arriving = 1.0 if previous is not None else age / self.FADE_IN_S  # replaced in place
        shown = max(0.0, min(1.0, arriving, leaving))
        if shown <= 0.0:
            return None
        key = (toast.text, self.muted(toast), self.steps(level), size, id(self.context))
        if self._made is None or self._made[0] != key:
            self._made = (key, self.picture(toast, level, size))
        picture = self._made[1]
        x, y = self.place(size, picture.size, 1.0 - ease_out(shown))
        return picture, shown, x, y

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

    def forget(self) -> None:
        """The message is gone: what was kept to make its next picture quickly (pictures drawn
        large, some megabytes) goes too."""
        self._made = self._base = self._drawn = self._smalled = None

    def steps(self, level: float) -> Any:
        """What of ``level`` the picture shows: a new picture where that changes."""
        return round(level, 3)

    def base(
        self, toast: Any, size: tuple[int, int], make: Callable[[], Image.Image]
    ) -> Image.Image:
        """What the picture shows but the level, drawn large (``make``): made again only where
        that changes (the number, muted, the size, the colours), not with each step of the
        glide; a copy of it to draw the level on."""
        return self._base_of(toast, size, make)[1].copy()

    def _base_of(
        self, toast: Any, size: tuple[int, int], make: Callable[[], Image.Image]
    ) -> tuple[Any, Image.Image]:
        """(its key, what the picture shows but the level drawn large): kept, not to draw on."""
        key = (self.number(toast), self.muted(toast), size, id(self.context),
               tuple(self.colors(toast).values()))  # fmt: skip
        if self._base is None or self._base[0] != key:
            self._base = (key, make())
        return self._base

    def barred(
        self, toast: Any, size: tuple[int, int], make: Callable[[], Image.Image],
        bar: tuple[tuple[int, int], int, int], level: float, glow: float,
    ) -> Image.Image:  # fmt: skip
        """The picture of a look whose level is a bar: what else it shows drawn large
        (``make``), the bar (``bar``: its span, middle and thickness, as for :meth:`fill`)
        filled to ``level`` with its glow, made small (:meth:`small`). Made anew only where it
        changed since the picture before (the end of the bar as it glides, the number), where
        the picture drawn large is whole steps of :data:`SS` wide and high: Pillow makes each
        pixel of the small picture from the large one's around it alone, the glow each pixel
        from the bar within its reach, so the pixels are those of a picture made whole."""
        key, base = self._base_of(toast, size, make)
        span, y, thick = bar
        colors = self.colors(toast)
        box = None
        if level > 0.0:
            end = span[0] + max(thick, round((span[1] - span[0]) * level))
            box = (span[0], y - thick // 2, end, y + thick // 2)
        last = self._drawn
        w, h = base.size
        tint = colors["accent"]
        if last is None or last.card.size != base.size or w % SS or h % SS:
            card = base.copy()
            self.fill(card, span, y, thick, level, colors, glow)
            self._drawn = _Drawn(key, base, card, self.small(card), box, tint)
            return self._drawn.small
        regions = []
        if base is not last.base:  # (the number, or another colour: where the large ones differ)
            changed = ImageChops.difference(base, last.base).getbbox(alpha_only=False)
            if changed is not None:
                regions.append(changed)
        reach = self.reach(glow)
        if box != last.fill or (box is not None and tint != last.tint):
            ends = [b for b in (box, last.fill) if b is not None]
            same = len(ends) == 2 and ends[0][:2] == ends[1][:2] and ends[0][3] == ends[1][3]
            if same and tint == last.tint:
                low, high = sorted((ends[0][2], ends[1][2]))  # (the same bar: its end moved)
                moved = (max(ends[0][0], low - thick), ends[0][1], high + 1, ends[0][3] + 1)
            else:
                moved = (min(b[0] for b in ends), min(b[1] for b in ends),
                         max(b[2] for b in ends) + 1, max(b[3] for b in ends) + 1)  # fmt: skip
            x0, y0, x1, y1 = moved
            regions.append((x0 - reach, y0 - reach, x1 + reach, y1 + reach))
        if not regions:
            last.key = key
            return last.small
        card = last.card  # (its own: only the small picture goes out)
        parts = []  # what changed, on the card: apart, as the number and the bar's end are
        for x0, y0, x1, y1 in sorted(regions):
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            if parts and x0 <= parts[-1][2] + 2 * 12 and y0 <= parts[-1][3] and y1 >= parts[-1][1]:
                px0, py0, px1, py1 = parts.pop()  # (near or over each other: one)
                x0, y0, x1, y1 = min(x0, px0), min(y0, py0), max(x1, px1), max(y1, py1)
            parts.append((x0, y0, x1, y1))
        for x0, y0, x1, y1 in parts:
            part = base.crop((x0, y0, x1, y1))
            near = box is not None and (
                box[0] - reach < x1 and box[2] + reach > x0
                and box[1] - reach < y1 and box[3] + reach > y0
            )  # fmt: skip
            if near:  # the bar's glow there: worked out from the bar within its reach
                gx0, gy0 = max(0, x0 - reach), max(0, y0 - reach)
                gx1, gy1 = min(w, x1 + reach), min(h, y1 + reach)
                layer = Image.new("RGBA", (gx1 - gx0, gy1 - gy0), (0, 0, 0, 0))
                at = [box[0] - gx0, box[1] - gy0, box[2] - gx0, box[3] - gy0]
                ImageDraw.Draw(layer).rounded_rectangle(at, thick // 2, fill=colors["accent"])
                lit = self.glow(layer, glow).crop((x0 - gx0, y0 - gy0, x1 - gx0, y1 - gy0))
                part.alpha_composite(lit)
            card.paste(part, (x0, y0))
        small = self._smaller(card, last.small, parts)
        self._drawn = _Drawn(key, base, card, small, box, tint)
        return small

    @staticmethod
    def reach(radius: float) -> int:
        """How far the glow of :meth:`glow` reaches: Pillow's three passes of a box blur,
        each its radius and a pixel wide."""
        return 3 * (math.ceil(max(1.0, radius)) + 2)

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
        self.groove(card, span, y, thick, colors)
        self.fill(card, span, y, thick, level, colors, glow)

    @staticmethod
    def groove(card: Image.Image, span: tuple[int, int], y: int, thick: int,
               colors: dict[str, RGB]) -> None:  # fmt: skip
        """The bar's groove from ``span[0]`` to ``span[1]`` at ``y``."""
        box = [span[0], y - thick // 2, span[1], y + thick // 2]
        ImageDraw.Draw(card).rounded_rectangle(box, thick // 2, fill=colors["track"])

    def fill(self, card: Image.Image, span: tuple[int, int], y: int, thick: int, level: float,
             colors: dict[str, RGB], glow: float) -> None:  # fmt: skip
        """The groove (:meth:`groove`) filled to ``level``, with its glow."""
        if level <= 0.0:
            return
        x0, x1 = span
        box = [x0, y - thick // 2, x0 + max(thick, round((x1 - x0) * level)), y + thick // 2]

        def paint(draw: ImageDraw.ImageDraw, dx: int, dy: int) -> None:
            at = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
            draw.rounded_rectangle(at, thick // 2, fill=colors["accent"])

        self.glowing(card, box, glow, paint)

    def number(self, toast: Any) -> str:
        return toast.text  # "42", or "Muted"

    @classmethod
    def glowing(
        cls, card: Image.Image, bounds: list[int] | tuple[int, int, int, int], radius: float,
        paint: Callable[[ImageDraw.ImageDraw, int, int], None],
    ) -> None:  # fmt: skip
        """What ``paint(draw, dx, dy)`` draws within ``bounds`` (x0, y0, x1, y1 on ``card``;
        shifted by dx, dy onto the part it gets), with its glow (:meth:`glow`), laid onto
        ``card``. Worked out in the part of the card that the glow can reach alone (Pillow's
        three passes of a box blur, each its radius and a pixel wide): around it the glow of a
        layer the card's size is nothing, so the pixels are the same."""
        reach = cls.reach(radius)
        x0, y0 = max(0, bounds[0] - reach), max(0, bounds[1] - reach)
        x1 = min(card.width, bounds[2] + 1 + reach)
        y1 = min(card.height, bounds[3] + 1 + reach)
        if x1 <= x0 or y1 <= y0:
            return
        layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        paint(ImageDraw.Draw(layer), -x0, -y0)
        card.alpha_composite(cls.glow(layer, radius), (x0, y0))

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
        return made_smaller(picture, (max(1, w // SS), max(1, h // SS)))

    GRID = (8, 4)  # what changed, looked for in these many parts of the picture across and down

    def made_small(self, card: Image.Image) -> Image.Image:
        """:meth:`small` of ``card`` (drawn whole, large), made anew only where it differs from
        the one made small before, where both are the same size in whole steps of :data:`SS`
        (the same pixels: :meth:`barred` says why)."""
        last = self._smalled
        w, h = card.size
        if last is None or last[0].size != card.size or w % SS or h % SS:
            small = self.small(card)
        else:
            diff = ImageChops.difference(card, last[0])
            columns, rows = self.GRID
            regions = []
            for i in range(columns):
                for j in range(rows):
                    cell = (w * i // columns, h * j // rows, w * (i + 1) // columns,
                            h * (j + 1) // rows)  # fmt: skip
                    found = diff.crop(cell).getbbox(alpha_only=False)
                    if found is not None:
                        regions.append((cell[0] + found[0], cell[1] + found[1],
                                        cell[0] + found[2], cell[1] + found[3]))  # fmt: skip
            small = last[1] if not regions else self._smaller(card, last[1], regions)
        self._smalled = (card, small)
        return small

    @staticmethod
    def _smaller(card: Image.Image, small: Image.Image, regions: list[tuple[int, int, int, int]]
                 ) -> Image.Image:  # fmt: skip
        """``small`` (of the large picture before) made anew from ``card`` where its pixels come
        from ``regions`` of it (LANCZOS: three pixels of the large picture each way, a pixel
        of the small one for three): each part made smaller with four pixels of the small
        picture round it, in whole steps of :data:`SS`, so that its pixels are those of the
        whole (:func:`made_smaller`); a new picture, as the one before may still be on show."""
        sw, sh = small.size
        out = small.copy()
        for x0, y0, x1, y1 in regions:
            ox0, oy0 = max(0, (x0 - 12) // SS), max(0, (y0 - 12) // SS)
            ox1, oy1 = min(sw, (x1 + 12) // SS + 1), min(sh, (y1 + 12) // SS + 1)
            px0, py0, px1, py1 = (
                max(0, ox0 - 4),
                max(0, oy0 - 4),
                min(sw, ox1 + 4),
                min(sh, oy1 + 4),
            )
            part = card.crop((px0 * SS, py0 * SS, px1 * SS, py1 * SS))
            fresh = made_smaller(part, (px1 - px0, py1 - py0))
            out.paste(fresh.crop((ox0 - px0, oy0 - py0, ox1 - px0, oy1 - py0)), (ox0, oy0))
        return out


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
        "it": "Barra",
        "ru": "Полоса",
        "ja": "バー",
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
        icon = round(28 * u)
        font = face(NUMBER_FACE, round(30 * u))
        text = self.number(toast)
        text_w = round(font.getlength(text))
        x0, x1 = round(18 * u) + icon + round(16 * u), w - round(22 * u) - text_w - round(18 * u)

        def base() -> Image.Image:
            card = self.panel(w, h, c, round(h / 2))
            self.speaker_at(card, toast, icon, round(18 * u), c["accent"])
            ImageDraw.Draw(card).text((w - round(22 * u), h // 2), text, font=font,
                                      fill=c["text"], anchor="rm")  # fmt: skip
            self.groove(card, (x0, x1), h // 2, round(8 * u), c)
            return card

        return self.barred(toast, size, base, ((x0, x1), h // 2, round(8 * u)), level, 6 * u)


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
        "it": "Livelli",
        "ru": "Уровни",
        "ja": "レベル",
    }
    BARS = 16

    def steps(self, level: float) -> Any:
        """How many bars are lit: the picture is the same between."""
        return sum(1 for i in range(self.BARS) if self.lit(level, i))

    def lit(self, level: float, i: int) -> bool:
        return level >= (i + 1) / self.BARS - 0.5 / self.BARS and level > 0

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
        lit: list[tuple[list[int], RGB]] = []
        for i in range(self.BARS):
            share = (i + 1) / self.BARS
            bar_h = round((bottom - top) * (0.28 + 0.72 * share))
            box = [round(x0 + i * step), bottom - bar_h, round(x0 + i * step) + bar_w, bottom]
            if self.lit(level, i):
                lit.append((box, mix(warn, c["accent"], max(0.0, (share - 0.75) / 0.25))))
            else:
                draw.rounded_rectangle(box, bar_w // 3, fill=c["track"])
        if lit:
            bounds = (min(b[0] for b, _ in lit), min(b[1] for b, _ in lit),
                      max(b[2] for b, _ in lit), max(b[3] for b, _ in lit))  # fmt: skip

            def paint(lit_draw: ImageDraw.ImageDraw, dx: int, dy: int) -> None:
                for box, color in lit:
                    at = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
                    lit_draw.rounded_rectangle(at, bar_w // 3, fill=color)

            self.glowing(card, bounds, 5 * u, paint)
        return self.made_small(card)


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
        "it": "Anello",
        "ru": "Кольцо",
        "ja": "リング",
    }

    def __init__(self, context: Any, options: dict[str, Any]) -> None:
        super().__init__(context, options)
        # (the ring drawn large without its number: :meth:`ringed`)
        self._ring: tuple[Any, Image.Image, Image.Image, Image.Image] | None = None

    def forget(self) -> None:
        super().forget()
        self._ring = None

    def picture(self, toast: Any, level: float, size: tuple[int, int]) -> Image.Image:
        u, c = self.unit(size) * SS, self.colors(toast)
        d = round(min(170 * u, min(size) * SS * 0.8))
        card = self.ringed(d, level, c, 6 * u).copy()
        draw = ImageDraw.Draw(card)
        text = self.number(toast)
        font = face(NUMBER_FACE, round(d * (0.3 if len(text) <= 3 else 0.16)))
        draw.text((d // 2, round(d * 0.47)), text, font=font, fill=c["text"], anchor="mm")
        icon = round(d * 0.16)
        speaker = self.speaker(toast, icon, c["muted"])
        card.alpha_composite(speaker, ((d - icon) // 2, round(d * 0.66)))
        return self.made_small(card)

    def ringed(self, d: int, level: float, c: dict[str, RGB], glow: float) -> Image.Image:
        """The ring ``d`` wide, drawn large, without its number: the card, the groove and the
        sweep lit to ``level`` with its glow (kept, not to draw on). The glow over the whole
        card took two thirds of a picture's time; it is made anew only around where the
        sweep changed since the ring before, from the sweep within its reach (Pillow's three
        passes of a box blur): the same pixels as made whole."""
        pad, width = round(d * 0.11), max(SS, round(d * 0.07))
        box = [pad, pad, d - 1 - pad, d - 1 - pad]
        sweep = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        if level > 0.0:
            ImageDraw.Draw(sweep).arc(box, 135, 135 + 270 * level, fill=c["accent"], width=width)
        key = (d, glow, tuple(c.values()))
        last = self._ring
        if last is None or last[0] != key:
            card = Image.new("RGBA", (d, d), (0, 0, 0, 0))
            draw = ImageDraw.Draw(card)
            edge = (255, 255, 255, 30)
            draw.ellipse([0, 0, d - 1, d - 1], fill=(*c["card"], 238), outline=edge, width=SS)
            draw.arc(box, 135, 405, fill=c["track"], width=width)  # three quarters, open below
            lit = card.copy()
            if level > 0.0:
                lit.alpha_composite(self.glow(sweep, glow))
            self._ring = (key, card, sweep, lit)
            return lit
        _, card, before, lit = last
        changed = ImageChops.difference(sweep, before).getbbox(alpha_only=False)
        if changed is not None:
            reach = self.reach(glow)
            x0, y0 = max(0, changed[0] - reach), max(0, changed[1] - reach)
            x1, y1 = min(d, changed[2] + reach), min(d, changed[3] + reach)
            gx0, gy0, gx1, gy1 = (
                max(0, x0 - reach),
                max(0, y0 - reach),
                min(d, x1 + reach),
                min(d, y1 + reach),
            )
            part = card.crop((x0, y0, x1, y1))
            soft = self.glow(sweep.crop((gx0, gy0, gx1, gy1)), glow)
            part.alpha_composite(soft.crop((x0 - gx0, y0 - gy0, x1 - gx0, y1 - gy0)))
            lit.paste(part, (x0, y0))
        self._ring = (key, card, sweep, lit)
        return lit


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
        "it": "Grande",
        "ru": "Крупный",
        "ja": "大",
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
        span, y, thick = (round(w * 0.1), round(w * 0.9)), round(h * 0.78), max(SS, round(12 * u))

        def base() -> Image.Image:
            card = self.panel(w, h, c, round(28 * u))
            text = self.number(toast)
            font = face(NUMBER_FACE, round(h * (0.46 if len(text) <= 3 else 0.24)))
            icon = round(h * 0.26)
            text_w = round(font.getlength(text))
            gap = round(18 * u)
            left = (w - icon - gap - text_w) // 2
            mid = round(h * 0.42)
            card.alpha_composite(self.speaker(toast, icon, c["accent"]), (left, mid - icon // 2))
            ImageDraw.Draw(card).text((left + icon + gap, mid), text, font=font, fill=c["text"],
                                      anchor="lm")  # fmt: skip
            self.groove(card, span, y, thick, c)
            return card

        return self.barred(toast, size, base, (span, y, thick), level, 8 * u)


VOLUME_LOOKS: dict[str, type[VolumeLook]] = {
    look.name: look for look in (BarLook, LevelsLook, RingLook, BigLook)
}

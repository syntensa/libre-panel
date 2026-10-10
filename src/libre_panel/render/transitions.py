"""Transitions drawn with light, after SPUR II: the tube and the entrance.

From one frame to the next only the rows under a moving edge and the rows
just revealed change, never the whole frame as in a crossfade: cheap to
draw, and cheap for a panel's video encoder. The new screen is uncovered
live, not as a still. Sizes are for a panel 480 px high and scale with the
shorter side, so both work on every panel either way round.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from libre_panel import i18n
from libre_panel.plugins.render import Transition
from libre_panel.render.light import (
    add_light,
    add_line,
    draw_tracked,
    ease_out,
    face,
    mix,
    phase,
    role_color,
    scaled,
    smooth,
    tracked_width,
)


class Tube(Transition):
    """A line ignites at mid-height and opens into two edges; between them the
    new screen is already live."""

    name = "tube"
    api = 1
    duration = 0.9
    switch = "restart"  # a switch on the way starts a new tube from the frame on show
    toasts = "over"
    from_shown = True  # the old frame as it showed, its toast included

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        out = old.convert("RGB") if old.mode != "RGB" else old.copy()
        middle = h / 2
        wide = ease_out(phase(t, 0.0, 0.3))
        opening = smooth(phase(t, 0.15, 1.0))
        beyond = 40 * u  # the edges leave the frame before they go out
        top = middle - (middle + beyond) * opening
        bottom = middle + (h - middle + beyond) * opening
        if opening > 0:
            a, b = max(0, int(top)), min(h, math.ceil(bottom))
            if b > a:
                out.paste(new.crop((0, a, w, b)).convert("RGB"), (0, a))
        bright = 1.0 - phase(t, 0.7, 1.0)
        if wide > 0 and bright > 0:
            accent, white = role_color(self.context, "info"), role_color(self.context, "text")
            reach = wide * w / 2
            for y in (middle,) if opening <= 0 else (top, bottom):
                add_line(out, w / 2 - reach, w / 2 + reach, y, accent, bright, u, 140 * w / 1920,
                         white=white)  # fmt: skip
        return out


class Entrance(Transition):
    """Into a mode and out of it, e.g. a game: the old frame closes to a line, a
    band opens with a logo or a title while a text types in below it, then
    the band closes and the line opens onto the new screen.

    ``params``: ``title``, ``text``, ``logo`` (a PIL image, or the key of one a
    service published, e.g. ``"game.logo"``) and ``kind``: ``"start"`` or
    ``"end"`` for a text of its own ("Game mode", "Game ended") when there
    is no ``text``. Without a logo the title stands in big letters.
    """

    name = "entrance"
    api = 1
    duration = 3.2
    switch = "follow"  # a screen asked for meanwhile (a round report) is revealed live
    toasts = "hide"  # it says itself what happens; the toast on show rolls in after it
    from_shown = True
    TIMELINE_S = 3.2  # the times below are seconds of it

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        kind = self.params.get("kind")
        default = {"start": i18n.t("Game mode"), "end": i18n.t("Game ended")}.get(kind, "")
        self.title = str(self.params.get("title") or "")
        self.text = str(self.params.get("text") or default).upper()
        self._figure: tuple[Any, ...] | None = None  # (frame size, picture, its halo, margin)

    def on_switch(self, theme: str, mode: str | None, mode_changed: bool) -> str:
        # the mode turned back (the game ended during its own entrance): a new
        # one starts from the frame on show; a screen in the same mode is revealed
        return "restart" if mode_changed else "follow"

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        s = t * self.TIMELINE_S
        void, accent = role_color(self.context, "void"), role_color(self.context, "info")
        white, grey = role_color(self.context, "text"), role_color(self.context, "muted")
        out = Image.new("RGB", (w, h), void)
        middle = round(h / 2 - 20 * u)
        beyond = 40 * u
        line = {"unit": u, "soft": 1.0, "white": white}

        # The old frame closes as a tube: two edges run onto the middle line.
        closing = smooth(phase(s, 0.0, 0.6))
        if closing < 1.0:
            top = -beyond + (middle + beyond) * closing
            bottom = h + beyond - (h + beyond - middle) * closing
            a, b = max(0, int(top)), min(h, math.ceil(bottom))
            if b > a:
                out.paste(old.crop((0, a, w, b)).convert("RGB"), (0, a))
            for y in (top, bottom):
                add_line(out, 0, w, y, accent, 0.9 * min(1.0, 4 * closing), **line)

        # The line opens to a band around the logo or title, which lights up;
        # later the band closes again, and out of that line the new screen opens.
        shut = smooth(phase(s, 2.30, 2.65))
        opening = smooth(phase(s, 0.80, 1.30)) * (1.0 - shut)
        half = 0.0
        if s >= 0.80:
            picture, halo, margin = self._picture(w, h, u, white)
            half = picture.height // 2 + round(34 * u)
            a, b = int(middle - half * opening), math.ceil(middle + half * opening)
            if opening > 0 and b > a:
                stage = Image.new("RGB", (w, b - a), void)
                px, py = (w - picture.width) // 2, middle - picture.height // 2 - a
                glow = ease_out(phase(s, 0.90, 1.30))
                if s >= 1.30:  # it dims to a steady glow
                    glow *= 0.35 + 0.65 * math.exp(-(s - 1.30) * 3)
                if glow > 0.004:
                    add_light(stage, (px - margin, py - margin), scaled(halo, glow), accent)
                stage.paste(picture, (px, py), picture)
                out.paste(stage, (0, a))
            if self.text and 1.25 < s and shut < 1.0:  # the text types in below
                shown = self.text[: int(len(self.text) * phase(s, 1.30, 1.70) + 0.001)]
                font, track = face("Barlow-Medium", max(7, round(26 * u))), 12 * u
                while track > 0 and tracked_width(font, self.text, track) > w - 20 * u:
                    track -= u  # tighter on a narrow panel
                x = (w - tracked_width(font, self.text, track)) / 2
                draw_tracked(ImageDraw.Draw(out), (x, middle + half + 46 * u), shown, font,
                             mix(grey, void, 1.0 - shut), track)  # fmt: skip

        # The new screen opens, live, out of the closed line.
        reveal = smooth(phase(s, 2.65, self.TIMELINE_S))
        top = bottom = float(middle)
        if reveal > 0:
            top = middle - (middle + beyond) * reveal
            bottom = middle + (h - middle + beyond) * reveal
            a, b = max(0, math.ceil(top)), min(h, int(bottom))
            if b > a:
                out.paste(new.crop((0, a, w, b)).convert("RGB"), (0, a))

        # One light over all of it: bright where edges meet, calm while the band
        # stands, and it goes out as it moves out. Nothing ignites a second time.
        if closing >= 1.0:
            if reveal > 0:
                bright = 1.0 - smooth(phase(s, 2.85, self.TIMELINE_S))
            elif shut > 0:
                bright = 0.3 + 0.7 * shut
            else:
                bright = 1.0 - 0.7 * smooth(phase(s, 0.80, 1.30))
            edges: tuple[float, ...] = (middle,)
            if reveal > 0:
                edges = (top, bottom)
            elif opening > 0:
                edges = (middle - half * opening, middle + half * opening)
            for y in edges:
                add_line(out, 0, w, y, accent, bright, **line)
        return out

    def _logo(self) -> Image.Image | None:
        logo = self.params.get("logo")
        if isinstance(logo, str) and logo and self.context is not None:
            logo = self.context.image(logo)  # published by a service
        return logo if isinstance(logo, Image.Image) else None

    def _picture(self, w: int, h: int, u: float, white: tuple[int, int, int]):
        """The logo (or the title in big letters) and its halo, made when the
        band first opens: a logo published with the switch is there by then."""
        if self._figure is not None and self._figure[0] == (w, h):
            return self._figure[1:]
        room_w, room_h = max(16, min(round(900 * u), w - round(80 * u))), max(8, round(150 * u))
        logo = self._logo()
        if logo is not None:
            picture = logo.convert("RGBA")
            scale = min(room_w / picture.width, room_h / picture.height, 2.0)  # no blur
            size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
            picture = picture.resize(size, Image.Resampling.LANCZOS)
        else:
            title = (self.title or self.text).upper()
            if not self.title:
                self.text = ""  # the text stands as the title; nothing to type
            picture = _lettering(title, room_w, round(130 * u), u, white)
        margin = round(60 * u)
        halo = Image.new("L", (picture.width + 2 * margin, picture.height + 2 * margin), 0)
        halo.paste(picture.getchannel("A"), (margin, margin))
        halo = halo.filter(ImageFilter.GaussianBlur(max(1.0, 18 * u)))
        self._figure = ((w, h), picture, halo, margin)
        return picture, halo, margin


def _lettering(text: str, width: int, height: int, u: float, color) -> Image.Image:
    """``text`` in big, spaced letters on a clear picture ``height`` high, as
    narrow as it is (it gets smaller to fit ``width``)."""
    size, track = round(96 * u), 10 * u
    font = face("Barlow-Bold", size)
    while size > 8 and tracked_width(font, text, track) + 20 * u > width:
        size = max(8, round(size * 0.92))
        font, track = face("Barlow-Bold", size), track * 0.92
    span = min(width, max(1, round(tracked_width(font, text, track) + 20 * u)))
    mask = Image.new("L", (span, max(1, height)), 0)
    draw_tracked(ImageDraw.Draw(mask), (10 * u, round(height * 100 / 130)), text, font, 255, track)
    picture = Image.new("RGBA", mask.size, (*color, 0))
    picture.putalpha(mask)
    return picture

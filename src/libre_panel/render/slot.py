"""A slot that shows one card of several, and moves from one to the next as SPUR II's Studio
screen does: a line of light ignites at the top edge, the card on show rolls up into it, and the
new card rolls down out of it.

Which card is on show is a rule: the first of the slot's cards that has something to show wins
(a track that plays or has been paused a short while, the weather, a download that runs), and
where none has, the first one shows in its empty look. A line of the slot's ``items`` may name
its own rule (``forecast if weather.temperature``, conditions as a ``needs`` has them).

The cards are parts of the module (``_card`` is their number in the slot); only the parts of the
card on show are drawn. A change freezes the pieces the old card showed last and cuts them, and
the live pieces of the new card, to the rows the rolling has reached: the time is the planned
time of the frame, so a change takes as long on a panel driven at 50 frames a second as on one
that is driven at 5, and a test can set the clock. The frame of the slot is a thin flat line in
the colour of the card on show (the colour a cover carries, the weather's, the network's); it
changes its colour over half a second where the card, or the track, changes.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import replace
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from libre_panel.render import cover, light
from libre_panel.render.renderer import Piece, need_holds
from libre_panel.theme import modules as M

ROLL_UP = 0.42  # the card on show has rolled up into the top edge by this share of a change
ROLL_DOWN = 0.36  # from this share on, the new card rolls down out of it
GLOW = 0.9  # how bright the line of light gets
FADE = 0.55  # seconds the frame takes to change its colour
MIXED = 0.72  # the frame's colour: this much of the cover's or the weather's over the ground
PLAYBACK = ("playback", "music")
WEATHER = ("forecast", "weather")
SUPER = 3  # the frame's mask is drawn at this many times its size


def _rgb(colour: str) -> tuple[int, int, int]:
    return M._rgb(colour)


# -- cutting a piece to rows -------------------------------------------------------------------


def _shade(part: Image.Image, first: float, last: float, additive: bool) -> Image.Image:
    """``part`` with its first row at ``first`` of its strength and its last at ``last``."""
    n = part.height
    values = [255] * n
    if n == 1:  # both cuts in one row: what is left of it
        values[0] = round(255 * max(0.0, first + last - 1.0))
    else:
        values[0], values[-1] = round(255 * first), round(255 * last)
    mask = Image.frombytes("L", (1, n), bytes(values)).resize(part.size, Image.Resampling.NEAREST)
    part = part if part.mode == "RGBA" else part.convert("RGBA")
    if additive:  # light: its colour is what is added
        red, green, blue, alpha = part.split()
        return Image.merge(
            "RGBA", [ImageChops.multiply(c, mask) for c in (red, green, blue, alpha)]
        )
    out = part.copy()
    out.putalpha(ImageChops.multiply(part.getchannel("A"), mask))
    return out


def window(piece: Piece | None, top: float, bottom: float) -> Piece | None:
    """``piece`` cut to the panel rows from ``top`` to ``bottom``; a row that is cut shows its
    share, so an edge that moves slowly glides instead of stepping. None: nothing of it shows."""
    if piece is None or bottom <= top:
        return None
    layer, y0 = piece.layer, piece.y
    a, b = max(top, y0), min(bottom, y0 + layer.height)
    glow = window(piece.light, top, bottom) if piece.light is not None else None
    if b <= a:
        if glow is None:
            return None
        return Piece(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), glow.x, glow.y, piece.box, light=glow)
    first, last = math.floor(a) - y0, math.ceil(b) - y0
    part = layer.crop((0, first, layer.width, last))
    share_first = 1.0 - (a - (y0 + first))
    share_last = 1.0 - ((y0 + last) - b)
    if share_first < 0.999 or share_last < 0.999:
        part = _shade(part, share_first, share_last, piece.add)
    return replace(piece, layer=part, y=y0 + first, light=glow)


# -- the slot ----------------------------------------------------------------------------------


class Slot:
    """One slot module: which card is on show, and a change from one to the next."""

    def __init__(self, renderer: Any, module: dict[str, Any], items: list[tuple[str, str]],
                 box: list[int]) -> None:  # fmt: skip
        self.renderer = renderer
        self.id = module["id"]
        self.module = module
        self.kinds = [kind for kind, _ in items]
        self.rules = [rule for _, rule in items]
        self.change = max(0.0, float(module["change"]))  # seconds
        self.hold = max(0.0, float(module["hold"])) * 60  # seconds a paused track stays on show
        self.box = box
        self.colors = M.colors_of(renderer.theme.palette)
        self.shown = 0  # the card on show (or, in a change, the one that comes)
        self.leaving: int | None = None  # the card that rolls up
        self.since = 0.0  # the planned time the change began
        self.frozen: dict[str, Piece] = {}  # what the leaving card showed last, by part
        self.last: dict[str, Piece] = {}  # what the card on show shows, by part
        self.started = False
        self.pinned: int | None = None  # a card drawn for the warm-up, whatever the rules say
        self.paused_at: float | None = None
        self.paused_for: Any = None
        self.rows: tuple[float, float] | None = None  # in a change: how far each has rolled
        self.line = 0.0  # in a change: the light's strength
        self._colour: tuple[int, int, int] | None = None  # the frame's colour on show now
        self._from: tuple[int, int, int] | None = None
        self._to: tuple[int, int, int] | None = None
        self._at = 0.0
        self._frames: OrderedDict[tuple[int, int, int], Piece] = OrderedDict()
        self._mask: Image.Image | None = None
        self.width = self._line_width(module)

    # -- which card ----------------------------------------------------------------------------

    def _line_width(self, module: dict[str, Any]) -> int:
        """The frame's thickness in px; none where the style has no outline."""
        card = {**M.STYLE_DEFAULTS, **(self.renderer.theme.style or {})}["card"]
        if module["card"] == "off" or card in ("none", "flat"):
            return 0
        return max(1, round(min(self.box[2], self.box[3]) / 140))

    def _playing(self, snapshot: Any, now: float) -> bool:
        """A track that plays, or that has been paused for less than ``hold`` seconds."""
        title = snapshot.value("media.title")
        if not title:
            self.paused_at = None
            return False
        playing = snapshot.value("media.playing")
        if isinstance(playing, bool):
            playing = float(playing)
        if isinstance(playing, int | float) and playing != 0:
            self.paused_at = None
            return True
        track = (title, snapshot.value("media.artist"))
        if self.paused_at is None or self.paused_for != track:
            self.paused_at, self.paused_for = now, track
        return now - self.paused_at < self.hold

    def _has(self, i: int, snapshot: Any, now: float) -> bool:
        """Whether card ``i`` has something to show."""
        rule = self.rules[i]
        kind = self.kinds[i]
        if rule:
            return all(
                need_holds(need.strip(), snapshot) for need in rule.split(",") if need.strip()
            )
        if kind in PLAYBACK:
            return self._playing(snapshot, now)
        if kind in WEATHER:
            return snapshot.value("weather.temperature") is not None
        if kind == "download":
            return isinstance(snapshot.state("downloads"), dict)
        return True

    def wants(self, snapshot: Any, now: float) -> int:
        """The card that has something to show: the first, else the first of all."""
        for i in range(len(self.kinds)):
            if self._has(i, snapshot, now):
                return i
        return 0

    def begin(self, snapshot: Any, now: float) -> None:
        """Decide the card for this frame, and where a change has got to."""
        renderer = self.renderer
        if self.pinned is not None:  # (the warm-up draws each card once, without a change)
            self.shown, self.leaving, self.rows, self.line = self.pinned, None, None, 0.0
            return
        if self.leaving is not None and now - self.since >= self.change:
            self.leaving, self.frozen, self.rows = None, {}, None
        want = self.wants(snapshot, now)
        if want != self.shown and self.leaving is None:  # (a change on its way finishes first)
            roll = self.started and renderer.animate and self.change > 0
            if roll:
                self.leaving, self.since, self.frozen = self.shown, now, self.last
            self.last = {}
            self.shown = want
        self.started = True
        self.rows, self.line = None, 0.0
        if self.leaving is not None:
            renderer.moving = True
            p = (now - self.since) / self.change
            height = self._inner()[3]
            rolled_up = height * (1.0 - light.ease_in(p / ROLL_UP)) if p < ROLL_UP else 0.0
            rolled_down = (
                height * light.ease_out((p - ROLL_DOWN) / (1.0 - ROLL_DOWN))
                if p > ROLL_DOWN
                else 0.0
            )
            self.rows = (rolled_up, rolled_down)
            if p < ROLL_UP:
                self.line = GLOW * light.ease_out(p / ROLL_UP)
            else:
                self.line = GLOW * (1.0 - light.ease_out((p - ROLL_UP) / (1.0 - ROLL_UP)))
        self._accent(snapshot, now)

    def others(self) -> list[int]:
        """The cards that are not on show."""
        return [i for i in range(len(self.kinds)) if i != self.shown]

    def settle(self) -> None:
        """Forget what the warm-up showed: the first frame decides the card, with no change."""
        self.pinned = None
        self.shown, self.started, self.leaving = 0, False, None
        self.last, self.frozen, self.rows, self.line = {}, {}, None, 0.0
        self._to = self._from = self._colour = None

    def _inner(self) -> list[float]:
        """What rolls: the module's box inside its frame."""
        x, y, w, h = self.box
        f = self.width
        return [x + f, y + f, w - 2 * f, h - 2 * f]

    # -- the parts -----------------------------------------------------------------------------

    def shows(self, card: int) -> str | None:
        """How part of ``card`` goes on the panel: ``"live"`` (drawn now), ``"frozen"`` (what
        the card showed before it rolled up), or None."""
        if card == self.shown:
            return "live"
        if self.leaving is not None and card == self.leaving:
            return "frozen"
        return None

    def live(self, part: str, piece: Piece | None) -> Piece | None:
        """A part of the card on show, as drawn: kept for when the card rolls up, and cut to
        the rows the new card has rolled down to during a change."""
        if piece is not None:
            self.last[part] = piece
        if self.rows is None or piece is None:
            return piece
        top = self._inner()[1]
        return window(piece, top, top + self.rows[1])

    def leave(self, part: str) -> Piece | None:
        """A part of the card that rolls up: what it showed last, from the rows below the
        new card's to the ones the rolling up has left."""
        if self.rows is None:
            return None
        top = self._inner()[1]
        return window(self.frozen.get(part), top + self.rows[1], top + self.rows[0])

    # -- the frame and the line of light -------------------------------------------------------

    def _target(self, snapshot: Any) -> tuple[int, int, int]:
        """The colour of the card on show, over the ground."""
        colors = self.colors
        ground = _rgb(colors["bg"])
        kind = self.kinds[self.shown]
        own = self.module.get("color")
        if own:  # (a colour, or an entry of the palette, or the cover's)
            return light.mix(self.renderer.color(own)[:3], ground, MIXED)
        if kind in PLAYBACK:
            found = cover.dominant(snapshot.images.get("media.cover"))
            if found is not None:
                return light.mix(found, ground, MIXED)
            return light.mix(_rgb(colors["text3"]), ground, 0.5)
        if kind in WEATHER:
            return light.mix(self._weather(snapshot), ground, MIXED)
        if kind in ("download", "net", "network"):
            return light.mix(_rgb(colors["net"]), ground, MIXED)
        return light.mix(_rgb(colors["accent"]), ground, MIXED)

    def _weather(self, snapshot: Any) -> tuple[int, int, int]:
        """The weather's colour as the Studio sets it: the sun's (or the clouds' by night),
        rain's blue, snow's white, a storm's red, grey for overcast and fog."""
        from libre_panel.render import symbols

        colors = self.colors
        kind = symbols.kind_of(snapshot.value("weather.code"))
        night = symbols.night_at(snapshot, "weather.code")
        if kind in ("clear", "mostly", "partly"):
            return _rgb(colors["text2"] if night else colors["warn"])
        if kind in ("rain", "drizzle"):
            return _rgb(colors["gpu"])
        if kind == "snow":
            return _rgb(colors["text"])
        if kind == "storm":
            return _rgb(colors["crit"])
        return _rgb(colors["text3"])

    def _accent(self, snapshot: Any, now: float) -> None:
        """The frame's colour for this frame: the card's, reached over half a second."""
        target = self._target(snapshot)
        if self._to is None or not self.renderer.animate:
            self._from = self._to = self._colour = target
            self._at = now
            return
        if target != self._to:
            self._from, self._to, self._at = self._colour, target, now
        u = light.clamp((now - self._at) / FADE)
        self._colour = light.mix(self._to, self._from, light.smooth(u))  # type: ignore[arg-type]
        if u < 1.0:
            self.renderer.moving = True

    def _frame(self, colour: tuple[int, int, int]) -> Piece:
        """The frame, a thin flat line round the module in ``colour``: drawn once per colour."""
        piece = self._frames.get(colour)
        if piece is not None:
            self._frames.move_to_end(colour)
            return piece
        x, y, w, h = self.box
        if self._mask is None:
            self._mask = self._outline(w, h)
        layer = Image.new("RGBA", (w, h), (*colour, 255))
        layer.putalpha(self._mask)
        piece = Piece(layer, x, y, [x, y, w, h])
        self._frames[colour] = piece
        while len(self._frames) > 6:
            self._frames.popitem(last=False)
        return piece

    def _outline(self, w: int, h: int) -> Image.Image:
        style = {**M.STYLE_DEFAULTS, **(self.renderer.theme.style or {})}
        radius = round(style["radius"] * min(w, h) / 200)  # as the module's card has it
        big = Image.new("L", (w * SUPER, h * SUPER), 0)
        ImageDraw.Draw(big).rounded_rectangle(
            [0, 0, w * SUPER - 1, h * SUPER - 1], radius=radius * SUPER, outline=255,
            width=self.width * SUPER,
        )  # fmt: skip
        return big.resize((w, h), Image.Resampling.BOX)  # (an area average: no ringing)

    def _ignition(self) -> Piece | None:
        """The line of light at the top edge, over the module's width."""
        if self.line <= 0.004:
            return None
        x, y, w, h = self.box
        unit = min(self.renderer.theme.width, self.renderer.theme.height) / 480
        half = max(3, round(32 * unit))
        row = y + self.width / 2
        strip = Image.new("RGB", (w, 2 * half))
        colour = self._to or self._colour or (255, 255, 255)  # (the card's that comes: one strip)
        light.add_line(strip, 0, w, half, colour, self.line, unit, 40 * unit, 7.0, 0.8,
                       light.role_color(self.renderer.theme, "text"))  # fmt: skip
        red, green, blue = strip.split()
        strip.putalpha(ImageChops.lighter(ImageChops.lighter(red, green), blue))
        return Piece(strip, x, round(row) - half, add=True)

    def overlay(self, pieces: list[tuple[str, Piece]]) -> None:
        """Put the frame, and during a change the line of light, over the module's pieces."""
        prefix = self.id + "/"
        at = max((i for i, (wid, _) in enumerate(pieces) if wid.startswith(prefix)), default=-1)
        if at < 0:
            return
        extra: list[tuple[str, Piece]] = []
        if self.width and self._colour is not None:
            extra.append((f"{prefix}\0frame", self._frame(self._colour)))
        line = self._ignition()
        if line is not None:
            extra.append((f"{prefix}\0line", line))
        pieces[at + 1 : at + 1] = extra

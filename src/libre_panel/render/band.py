"""The band, a toast style after SPUR II's notices (built in as ``"band"``).

A band across the panel, drawn like one of its sections: a glowing edge
ignites from the centre, the band rolls down from it, and the edge glows on
a little. The same message again (the volume while it turns) changes in
place without rolling in again; another one rolls down inside the open band;
a band already leaving rolls back up from where it was. A progress bar
glides to its value instead of jumping.

Sizes are for a panel 480 px high (the 9.2" bar) and scale with the shorter
side of the frame, so the band also fits a 3.5" panel either way round.
"""

from __future__ import annotations

import math
import weakref
from collections import OrderedDict
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageOps

from libre_panel.icons import ICON_NAMES, draw_icon
from libre_panel.plugins.render import ToastStyle
from libre_panel.render.light import (
    RGB,
    add_line,
    bloom,
    draw_tracked,
    ease_in,
    ease_out,
    face,
    fitted_text,
    mix,
    paste_top,
    role_color,
    tracked_width,
)

TITLE_FACE, TEXT_FACE = "Barlow-SemiBold", "Barlow-Bold"
GLOW = 0.6  # how strongly lit parts shine into their surroundings


class _Run:
    """One toast in the band: where in its life it was when it came (the one
    it continues may have begun earlier), and what it carries over."""

    __slots__ = ("bar", "bar_at", "change", "glow", "start")

    def __init__(self, start: float = 0.0, glow: float = 0.0) -> None:
        self.start = start  # s into its life (roll in, hold, roll out) when it came
        self.glow = glow  # s its edge had glowed when it came
        self.bar: float | None = None  # the progress shown: it glides to the toast's own
        self.bar_at = 0.0  # its age when the bar last moved
        self.change: tuple[Any, float | None] | None = None  # rolls down over (toast, bar)


class _Layout:
    """Where the band lies in a frame of ``size``, and its parts, in band pixels."""

    def __init__(self, size: tuple[int, int], hidden: dict[str, int], top: bool) -> None:
        width, height = size
        self.u = u = min(width, height) / 480  # 1 on the 9.2" bar
        self.x, self.w = hidden["left"], max(8, width - hidden["left"] - hidden["right"])
        self.h = max(16, min(height, round(184 * u)))
        self.y = hidden["top"] if top else height - hidden["bottom"] - self.h
        self.edge = round(14 * u)  # top of the frame line, where the light runs
        self.thick = max(2, round(5 * u))
        self.above = round(6 * u)  # the line's glow above it rolls in with the band
        self.pad = round(23 * u)
        self.right = self.w - round(26 * u)
        self.tile = (self.pad, round(66 * u), round(104 * u))
        self.key = (size, tuple(hidden.values()), top)

    def text_room(self, tile: bool, bar: str | None) -> tuple[int, float, int, tuple[int, ...]]:
        """(x, width, baseline, font sizes) of the main text; a bar beside it
        leaves it less room, a line under it less height."""
        u = self.u
        x = self.pad + (self.tile[2] + round(33 * u) if tile else 0)
        sizes = tuple(max(8, round(s * u)) for s in (80, 66, 54, 44))
        if bar == "line":
            return x, self.right - x, round(146 * u), sizes[1:]
        if bar is not None:
            return x, max(200 * u, self.right - x - 620 * u), round(160 * u), sizes
        return x, self.right - x, round(160 * u), sizes

    def bar_beside(self, tile: bool) -> bool:
        """Whether a bar fits beside the text (a wide bar), else it goes under it."""
        x = self.text_room(tile, None)[0]
        return self.right - x - 620 * self.u >= 200 * self.u


class BandStyle(ToastStyle):
    """A band across the panel that rolls down from a glowing edge.

    ``payload`` keys it draws: ``label`` (a title above the text),
    ``progress`` (0 to 1: a bar), ``color`` (instead of the level's),
    ``image`` (a picture in a tile, e.g. a cover) and ``badge`` (a small
    picture on its corner, e.g. the app's icon); pictures are PIL images or
    the keys of published ones. ``toast.icon`` goes in the tile when there is
    no picture.
    """

    name = "band"
    api = 1
    label = {"en": "Band", "de": "Band"}
    options = {"roll_in": ("number", 0.45), "roll_out": ("number", 0.32)}
    CHANGE_S = 0.26  # another message rolls down inside the open band
    GLIDE_S = 0.09  # the bar is two thirds of the way after 90 ms, there after 270 ms
    AFTERGLOW_S = 0.25  # the edge glows on after the band is down
    # shares of the roll-in: the edge is full width by WIDE; the band rolls from FROM for FOR
    WIDE, FROM, FOR = 0.36, 0.22, 0.67

    def __init__(self, context: Any, options: dict[str, Any]) -> None:
        super().__init__(context, options)
        given = {**{k: d for k, (_kind, d) in type(self).options.items()}, **(options or {})}
        self.roll_in = _seconds(given["roll_in"], 0.45)
        self.roll_out = _seconds(given["roll_out"], 0.32)
        self.leave_s = self.roll_out
        self._runs: weakref.WeakKeyDictionary[Any, _Run] = weakref.WeakKeyDictionary()
        self._layout_for: tuple[Any, ...] | None = None  # (size, at the top, context, layout)
        # Built once and reused (each keeps what its key names by id, so an id
        # that comes again for another object is never mistaken for it): the
        # ground with frame, title and tile; the text on it; the bar on that.
        self._grounds: OrderedDict[Any, tuple[Any, ...]] = OrderedDict()
        self._contents: OrderedDict[Any, tuple[Any, ...]] = OrderedDict()
        self._bars: OrderedDict[Any, tuple[Image.Image, Image.Image]] = OrderedDict()

    # -- timing ---------------------------------------------------------------

    def lifetime(self, toast: Any, previous: Any) -> float:
        run = self._runs[toast] = self._arrival(toast, previous)
        return self.roll_in + (toast.seconds or 0.0) + self.roll_out - run.start

    def _arrival(self, toast: Any, previous: Any) -> _Run:
        """How ``toast`` begins when it replaces ``previous`` (that toast and its age)."""
        if previous is None:
            return _Run()
        before, age = previous
        known = self._runs.get(before) or _Run()  # never drawn here: as if it had just come
        s, glow = known.start + age, known.glow + age
        hold = before.seconds or 0.0
        if s >= self.roll_in + hold + self.roll_out:  # it is gone: roll in afresh
            return _Run()
        height = self._phases(s, glow, hold)[2]
        if s < self.roll_in:  # still rolling in: roll on
            run = _Run(s, glow)
        elif s < self.roll_in + hold:  # standing: stay open, the edge does not ignite again
            run = _Run(self.roll_in, glow)
        else:  # leaving: roll back up from the height it reached, the edge ignites again
            back = self._roll_in_time(height)
            run = _Run(back, back)
        if _key(before) == _key(toast):  # the same thing again: the bar goes on from there
            run.bar, run.bar_at = known.bar, known.bar_at - age
        elif height > 0:  # another one: it rolls down inside the open band
            run.change = (before, known.bar)
        return run

    def _phases(self, s: float, glow: float, hold: float) -> tuple[float, float, float, float]:
        """(band width, line width, band height, edge light), shares from 0 to 1,
        ``s`` seconds into its life, the edge lit ``glow`` seconds."""
        if s < self.roll_in:
            p = s / max(1e-3, self.roll_in)
            wide = ease_out(p / self.WIDE)
            return wide, wide, ease_out((p - self.FROM) / self.FOR), self._afterglow(glow)
        if s < self.roll_in + hold:
            return 1.0, 1.0, 1.0, self._afterglow(glow)
        p = (s - self.roll_in - hold) / self.roll_out if self.roll_out > 0 else 1.0
        # leaving, it rolls up into the edge, which brightens, then draws in to the centre
        return (
            1.0,
            1.0 - ease_in((p - 0.5) / 0.5),
            1.0 - ease_in(p / 0.69),
            0.85 * ease_out(p / 0.5),
        )

    def _afterglow(self, g: float) -> float:
        roll_in = max(1e-3, self.roll_in)
        if g < roll_in:
            return 1.0 - 0.75 * ease_out((g - 0.44 * roll_in) / (0.67 * roll_in))
        return 0.25 * (1.0 - ease_out((g - roll_in) / self.AFTERGLOW_S))

    def _roll_in_time(self, height: float) -> float:
        """When, rolling in, the band reaches ``height``: the inverse of its curve."""
        if height <= 0.0:
            return 0.0
        q = 1.0 - (1.0 - min(1.0, height)) ** (1 / 3)
        return self.roll_in * (self.FROM + self.FOR * q)

    # -- drawing --------------------------------------------------------------

    def draw(self, frame: Image.Image, toast: Any, age: float, previous: Any = None) -> Image.Image:
        run = self._runs.get(toast)
        if run is None:  # drawn without lifetime() first: as it comes now
            run = self._runs[toast] = self._arrival(toast, previous)
        wide, line, height, glow = self._phases(
            run.start + age, run.glow + age, toast.seconds or 0.0
        )
        if height <= 0 and (line <= 0 or glow <= 0.01):
            return frame
        layout = self._layout(frame.size)
        accent = self._accent(toast)
        out = frame.convert("RGB") if frame.mode != "RGB" else frame.copy()
        middle = layout.w / 2
        if height > 0:
            band = self._band(toast, run, age, layout, accent)
            xa, xb = round(middle - layout.w * wide / 2), round(middle + layout.w * wide / 2)
            top = layout.edge - layout.above
            if xb > xa:
                rows = layout.above + (layout.h - layout.edge) * height
                paste_top(
                    out, band.crop((xa, top, xb, layout.h)), layout.x + xa, layout.y + top, rows
                )
        reach = layout.w * line / 2
        y = layout.y + layout.edge + layout.thick / 2
        x = layout.x + middle
        add_line(out, x - reach, x + reach, y, accent, glow, layout.u, 40 * layout.u, 7.0, 0.8)
        return out

    def _layout(self, size: tuple[int, int]) -> _Layout:
        top = self.anchor.startswith("top")
        known = self._layout_for
        if known is not None and known[:2] == (size, top) and known[2] is self.context:
            return known[3]
        hidden = dict.fromkeys(("top", "right", "bottom", "left"), 0)
        model = getattr(self.context, "model", None)
        if model is not None:  # keep clear of what the bezel hides
            hidden = model.hidden_edges("landscape" if size[0] > size[1] else "portrait")
        layout = _Layout(size, hidden, top)
        self._layout_for = (size, top, self.context, layout)
        return layout

    def _accent(self, toast: Any) -> RGB:
        """``payload["color"]`` (``"#rrggbb"``, ``"@name"`` or a tuple), else the level's."""
        color = toast.payload.get("color")
        try:
            if isinstance(color, str) and color:
                if self.context is not None:  # knows the palette's names
                    return tuple(self.context.color(color)[:3])  # type: ignore[return-value]
                return ImageColor.getrgb(color)[:3]
            if isinstance(color, tuple | list) and len(color) >= 3:
                return tuple(int(c) for c in color[:3])  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass  # a colour nobody can read: the level's
        level = toast.level if toast.level in ("warning", "error") else "info"
        return role_color(self.context, level)

    def _band(self, toast: Any, run: _Run, age: float, layout: _Layout, accent: RGB) -> Image.Image:
        """The band of ``toast`` now: its content and bar, and while it takes
        over, rolling down over the one before."""
        band = self._with_bar(toast, layout, accent, self._bar(toast, run, age))
        if run.change is None:
            return band
        p = age / self.CHANGE_S
        if not 0 <= p < 1:
            run.change = None
            return band
        before, bar = run.change
        old = self._with_bar(before, layout, self._accent(before), bar).copy()
        rows = (layout.h - layout.edge) * ease_out(p)
        paste_top(old, band.crop((0, layout.edge, layout.w, layout.h)), 0, layout.edge, rows)
        u = layout.u
        add_line(old, 0, layout.w, layout.edge + rows, accent, 0.85 * (1 - p), u, 40 * u, 7.0, 0.8)
        return old

    def _bar(self, toast: Any, run: _Run, age: float) -> float | None:
        """The progress shown at ``age``: it glides to the toast's own, on time
        (not by frames), and starts where the one it continues stood."""
        target = _share(toast.payload.get("progress"))
        if target is None:
            return None
        if run.bar is None:
            run.bar = target
        else:
            dt = max(0.0, age - run.bar_at)
            run.bar += (target - run.bar) * (1.0 - math.exp(-dt / self.GLIDE_S))
        run.bar_at = age
        return run.bar

    # -- the band's pictures --------------------------------------------------

    def _picture(self, value: Any) -> Image.Image | None:
        if isinstance(value, Image.Image):
            return value
        if isinstance(value, str) and value and self.context is not None:
            return self.context.image(value)
        return None

    def _ground(self, toast: Any, layout: _Layout, accent: RGB) -> tuple[Image.Image, bool]:
        """What messages with the same title and pictures share: the band's
        ground and frame, the title, the tile. Whether there is a tile."""
        payload = toast.payload
        image, badge = self._picture(payload.get("image")), self._picture(payload.get("badge"))
        icon = toast.icon if toast.icon in ICON_NAMES else None
        label = str(payload.get("label") or "").upper()
        refs = (self.context, image, badge)
        key = (layout.key, accent, label, icon, *map(id, refs))
        cached = self._grounds.get(key)
        if cached is not None and all(a is b for a, b in zip(cached[2], refs, strict=True)):
            self._grounds.move_to_end(key)
            return cached[0], cached[1]
        u, w, h = layout.u, layout.w, layout.h
        ground, text = role_color(self.context, "ground"), role_color(self.context, "text")
        band = Image.new("RGB", (w, h), ground)
        draw = ImageDraw.Draw(band)
        grid, step = mix(text, ground, 0.045), max(6, round(40 * u))  # SPUR's faint raster
        for gx in range(step // 2, w, step):
            draw.line([(gx, 0), (gx, h)], fill=grid)
        for gy in range(step // 2, h, step):
            draw.line([(0, gy), (w, gy)], fill=grid)
        # the frame: across the top and down both sides, lit in the accent
        e, t = layout.edge, layout.thick
        x0, x1 = round(10 * u), w - 1 - round(10 * u)
        lit = Image.new("RGB", (w, h))
        lit_draw = ImageDraw.Draw(lit)
        for box in ((x0, e, x1, e + t - 1), (x0, e, x0 + t - 1, h - 1), (x1 - t + 1, e, x1, h - 1)):
            draw.rectangle(box, fill=mix(accent, ground, 0.73))
            lit_draw.rectangle(box, fill=accent)
        band = ImageChops.add(band, bloom(lit, 2.0 * u, GLOW))
        if label:
            self._title(band, label, layout, accent, ground)
        tile = self._tile(band, image, badge, icon, layout, accent, ground)
        self._grounds[key] = (band, tile, refs)
        while len(self._grounds) > 4:
            self._grounds.popitem(last=False)
        return band, tile

    def _title(self, band: Image.Image, label: str, layout: _Layout, accent: RGB, ground: RGB):
        u = layout.u
        font, track = face(TITLE_FACE, max(7, round(26 * u))), 3 * u
        x, base = layout.pad, round(45 * u)
        if tracked_width(font, label, track) > layout.right - x:
            while len(label) > 1 and tracked_width(font, label + "…", track) > layout.right - x:
                label = label[:-1]
            label = label.rstrip() + "…"
        halo = Image.new("L", band.size, 0)
        draw_tracked(ImageDraw.Draw(halo), (x, base), label, font, 255, track)
        band.paste(accent, (0, 0), bloom(halo, 4.0 * u, 0.9 * GLOW, scale=2))
        draw = ImageDraw.Draw(band)
        draw_tracked(draw, (x, base), label, font, accent, track)
        width = tracked_width(font, label, track)
        rule = max(1, round(2 * u))
        y = base + round(6 * u)
        draw.rectangle(
            [x, y, x + round(width + 6 * u), y + rule - 1], fill=mix(accent, ground, 0.3)
        )

    def _tile(self, band, image, badge, icon, layout: _Layout, accent: RGB, ground: RGB) -> bool:
        """A picture (with the badge on its corner) or the badge or icon on a tile."""
        u = layout.u
        x, y, size = layout.tile
        draw = ImageDraw.Draw(band)
        rim, deep = mix(accent, ground, 0.8), mix(ground, (0, 0, 0), 0.55)
        line = max(1, round(2 * u))
        if image is not None:
            picture = ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
            band.paste(picture, (x, y))
            draw.rectangle([x - 1, y - 1, x + size, y + size], outline=rim, width=line)
            if badge is not None:  # the app's icon hangs over the lower corner
                side = round(40 * u)
                bx, by = x + size - side + round(10 * u), y + size - side + round(6 * u)
                rule, pad = role_color(self.context, "rule"), round(4 * u)
                draw.rectangle([bx - pad, by - pad, bx + side + pad - 1, by + side + pad - 1],
                               fill=deep, outline=rule)  # fmt: skip
                _paste_rgba(band, badge, (bx, by), side)
            return True
        if badge is None and icon is None:
            return False
        draw.rectangle([x, y, x + size - 1, y + size - 1], fill=deep, outline=rim, width=line)
        if badge is not None:
            _paste_rgba(band, badge, (x + round(16 * u), y + round(16 * u)), round(72 * u))
        else:
            side = round(64 * u)
            band.paste(accent, (x + round(20 * u), y + round(20 * u)),
                       draw_icon(icon, side, accent).getchannel("A"))  # fmt: skip
        return True

    def _content(self, toast: Any, layout: _Layout, accent: RGB) -> tuple[Image.Image, Any]:
        """The band of ``toast`` without its bar's value, and where the bar goes."""
        key = (id(toast), layout.key, accent, id(self.context))
        cached = self._contents.get(key)
        if cached is not None and cached[0] is toast and cached[1] is self.context:
            return cached[2], cached[3]
        ground_layer, tile = self._ground(toast, layout, accent)
        band = ground_layer.copy()
        draw = ImageDraw.Draw(band)
        u = layout.u
        progress = _share(toast.payload.get("progress"))
        form = None
        if progress is not None:
            beside = layout.bar_beside(tile) and toast.kind != "music"
            form = ("levels" if toast.kind == "volume" else "segments") if beside else "line"
        x, width, base, sizes = layout.text_room(tile, form)
        text, font = fitted_text(str(toast.text), TEXT_FACE, sizes, width)
        draw.text((x, base), text, font=font, fill=role_color(self.context, "text"), anchor="ls")
        bar = None
        if form == "line":
            bar = (form, x, round(170 * u), layout.right)
        elif form is not None:
            end = x + font.getlength(text)
            start = min(max(end + 60 * u, x + 540 * u), layout.right - 560 * u)
            bar = (form, round(start), 0, layout.right)
        self._contents[key] = (toast, self.context, band, bar)
        while len(self._contents) > 4:
            self._contents.popitem(last=False)
        return band, bar

    def _with_bar(self, toast: Any, layout: _Layout, accent: RGB, value: float | None):
        band, bar = self._content(toast, layout, accent)
        if bar is None or value is None:
            return band
        form, x0, y0, x1 = bar
        steps = {"levels": 36, "segments": 50}.get(form, 0)
        if steps:
            steps = max(4, min(steps, int((x1 - x0) / max(3.0, 8 * layout.u))))
            state = round(value * steps)
        else:
            state = round(x0 + (x1 - x0) * value)
        key = (id(band), state)
        cached = self._bars.get(key)
        if cached is not None and cached[0] is band:
            return cached[1]
        out = band.copy()
        self._draw_bar(out, form, x0, y0, x1, steps, state, layout, accent)
        self._bars[key] = (band, out)
        while len(self._bars) > 6:
            self._bars.popitem(last=False)
        return out

    def _draw_bar(self, band, form, x0, y0, x1, steps, state, layout: _Layout, accent: RGB):
        """Lit parts get a glow under them, then everything is drawn sharp on top
        (glow added over a colour would wash it out)."""
        u = layout.u
        track, rule = role_color(self.context, "track"), role_color(self.context, "rule")
        empty: list[list[float]] = []
        full: list[list[float]] = []
        if form == "levels":  # rising bars, as on a mixer
            bottom, step = round(168 * u), (x1 - x0) / steps
            gap = max(1, round(9 * u * step / ((x1 - x0) / 36)))
            for i in range(steps):
                tall = (22 + 82 * i / max(1, steps - 1)) * u
                box = [x0 + i * step, bottom - tall, x0 + i * step + step - gap, bottom]
                (full if i < state else empty).append(box)
            zone = (x0 - round(16 * u), bottom - round(124 * u), x1 + round(16 * u),
                    bottom + round(14 * u))  # fmt: skip
        elif form == "segments":
            gap, y, tall = max(1, round(5 * u)), round(104 * u), round(34 * u)
            width = (x1 - x0 - gap * (steps - 1)) / steps
            for i in range(steps):
                left = x0 + i * (width + gap)
                (full if i < state else empty).append([left, y, left + width, y + tall])
            zone = (
                x0 - round(16 * u),
                y - round(16 * u),
                x1 + round(16 * u),
                y + tall + round(16 * u),
            )
        else:  # a thin line under the text, with a knob at its end
            thick = max(2, round(4 * u))
            empty.append([x0, y0, x1, y0 + thick - 1])
            if state > x0:
                full.append([x0, y0, state, y0 + thick - 1])
            zone = (x0 - round(12 * u), y0 - round(14 * u), x1 + round(12 * u), y0 + round(18 * u))
        zone = (
            max(0, zone[0]),
            max(0, zone[1]),
            min(band.width, zone[2]),
            min(band.height, zone[3]),
        )
        draw = ImageDraw.Draw(band)
        if full and zone[2] > zone[0] and zone[3] > zone[1]:
            lit = Image.new("RGB", (zone[2] - zone[0], zone[3] - zone[1]))
            lit_draw = ImageDraw.Draw(lit)
            for box in full:
                lit_draw.rectangle([box[0] - zone[0], box[1] - zone[1],
                                    box[2] - zone[0], box[3] - zone[1]], fill=accent)  # fmt: skip
            band.paste(ImageChops.add(band.crop(zone), bloom(lit, 2.5 * u, GLOW)), zone[:2])
        outline = rule if form != "line" else None
        for box in empty:
            draw.rectangle(box, fill=track, outline=outline)
        for box in full:
            draw.rectangle(box, fill=accent)
        if form == "line":
            knob = _knob(max(3, round(8.5 * u)), role_color(self.context, "text"), accent, u)
            band.paste(knob, (round(max(x0, state) - knob.width / 2),
                              round(y0 + 2 * u - knob.height / 2)), knob)  # fmt: skip


def _key(toast: Any) -> str:
    return toast.key or toast.kind


def _seconds(value: Any, default: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return min(10.0, seconds) if seconds >= 0 else default


def _share(value: Any) -> float | None:
    """A progress value as 0 to 1, or None for none."""
    if value is None or isinstance(value, bool):
        return None
    try:
        share = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(share) else min(1.0, max(0.0, share))


def _paste_rgba(band: Image.Image, picture: Image.Image, xy: tuple[int, int], side: int) -> None:
    picture = picture.convert("RGBA").resize((side, side), Image.Resampling.LANCZOS)
    band.paste(picture, xy, picture)


def _knob(radius: int, fill: RGB, ring: RGB, u: float) -> Image.Image:
    """A round knob, smooth at the edge (drawn four times larger)."""
    s = 4
    size = 2 * radius + 2
    big = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse([s, s, (size - 1) * s, (size - 1) * s], fill=fill, outline=ring,
                                width=max(1, round(2 * u * s)))  # fmt: skip
    return big.resize((size, size), Image.Resampling.LANCZOS)

"""More ways from one screen to the next: plain ones (wipe, zoom, iris, blinds, dissolve, a
slide from each side) and elaborate ones (a line of light, a glitch, tiles that turn over, a
screen that falls apart). Settings → Panel → Transitions chooses them for each occasion
(render/overlays.py has the list, app.py the choice).

Each draws frames of any size, either way round, sizes being for a panel 480 px high and
scaled with the shorter side, and keeps the work of a frame small at 30 to 50 frames a second
on a 1920x480 bar: a map made once per switch and a threshold over it (``point`` and
``composite``), a few crops and pastes, at most a scaled copy of the frame.
"""

from __future__ import annotations

import math
import random
from typing import Any

from PIL import Image, ImageChops

from libre_panel.plugins.render import Transition
from libre_panel.render.light import add_line, ease_in, ease_out, phase, role_color, smooth


def _rgb(image: Image.Image) -> Image.Image:
    return image if image.mode == "RGB" else image.convert("RGB")


# -- the plain ones: the new screen where a map lies below a rising threshold ------------------


class _Masked(Transition):
    """The new screen shows where the map (L, made once a switch for its size) is below a level
    that rises from 0 to 255 over the transition; ``soft`` grey levels blend at the edge."""

    api = 1
    soft = 24
    toasts = "over"

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._made: tuple[tuple[int, int], Image.Image] | None = None

    def _map(self, w: int, h: int) -> Image.Image:
        raise NotImplementedError

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        if self._made is None or self._made[0] != new.size:
            self._made = (new.size, self._map(*new.size))
        level = smooth(t) * (255 + self.soft)
        lut = [255 if v <= level - self.soft else 0 if v > level else
               round(255 * (level - v) / self.soft) for v in range(256)]  # fmt: skip
        return Image.composite(_rgb(new), _rgb(old), self._made[1].point(lut))


def _row(values: bytes, w: int, h: int) -> Image.Image:
    """A map that changes across, the same down every column."""
    return Image.frombytes("L", (len(values), 1), values).resize((w, h), Image.Resampling.BILINEAR)


class Wipe(_Masked):
    """A soft edge goes across, from the left, and leaves the new screen behind it."""

    name = "wipe"
    duration = 0.7
    soft = 40

    def _map(self, w: int, h: int) -> Image.Image:
        return _row(bytes(range(256)), w, h)


class Iris(_Masked):
    """A circle opens from the middle onto the new screen."""

    name = "iris"
    duration = 0.8
    soft = 18

    def _map(self, w: int, h: int) -> Image.Image:
        side = math.ceil(math.hypot(w, h))  # the circle reaches the corners at 255
        circle = Image.radial_gradient("L").resize((side, side), Image.Resampling.BILINEAR)
        left, top = (side - w) // 2, (side - h) // 2
        return circle.crop((left, top, left + w, top + h))


class Blinds(_Masked):
    """Upright slats turn one after another, from the left, each from its own left edge."""

    name = "blinds"
    duration = 0.9
    soft = 30

    def _map(self, w: int, h: int) -> Image.Image:
        u = min(w, h) / 480
        slats = max(4, round(w / (150 * u)))
        width = w / slats
        values = bytearray()
        for x in range(w):
            k, inside = divmod(x, width)
            values.append(
                min(255, round(255 * (0.45 * k / max(1, slats - 1) + 0.55 * inside / width)))
            )
        return Image.frombytes("L", (w, 1), bytes(values)).resize((w, h), Image.Resampling.NEAREST)


class Dissolve(_Masked):
    """The new screen comes through in small squares, here and there."""

    name = "dissolve"
    duration = 0.8
    soft = 6

    def _map(self, w: int, h: int) -> Image.Image:
        u = min(w, h) / 480
        block = max(2, round(8 * u))
        cols, rows = math.ceil(w / block), math.ceil(h / block)
        chance = random.Random(0x5EED)  # the same squares every time: calm, not noise
        noise = Image.frombytes(
            "L", (cols, rows), bytes(chance.randrange(256) for _ in range(cols * rows))
        )
        return noise.resize((cols * block, rows * block), Image.Resampling.NEAREST).crop(
            (0, 0, w, h)
        )


class _SlideFrom(Transition):
    """The new screen pushes the old one out, from one side (``side``: where it comes from)."""

    api = 1
    duration = 0.6
    side = "right"

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        eased = 1 - (1 - t) ** 3
        dx, dy = {"right": (-1, 0), "left": (1, 0), "bottom": (0, -1), "top": (0, 1)}[self.side]
        sx, sy = round(dx * w * eased), round(dy * h * eased)
        out = Image.new("RGB", (w, h))
        out.paste(_rgb(old), (sx, sy))
        out.paste(_rgb(new), (sx - dx * w, sy - dy * h))
        return out


class SlideRight(_SlideFrom):
    name = "slide-right"
    side = "left"


class SlideUp(_SlideFrom):
    name = "slide-up"
    side = "bottom"


class SlideDown(_SlideFrom):
    name = "slide-down"
    side = "top"


class Zoom(Transition):
    """The old screen draws back into the dark, the new one comes forward out of it."""

    name = "zoom"
    api = 1
    duration = 0.8
    toasts = "over"
    DEPTH = 0.12  # how far back a screen stands at its farthest

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        going = t < 0.5
        u = smooth(t / 0.5) if going else 1.0 - smooth((t - 0.5) / 0.5)  # 0 near, 1 far
        scale = 1.0 - self.DEPTH * u
        picture = _rgb(old if going else new)
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        dark = role_color(self.context, "void")
        out = Image.new("RGB", (w, h), dark)
        small = picture.resize(size, Image.Resampling.BILINEAR)
        small = Image.blend(small, Image.new("RGB", size, dark), min(1.0, 0.9 * u))
        out.paste(small, ((w - size[0]) // 2, (h - size[1]) // 2))
        return out


# -- the elaborate ones -------------------------------------------------------------------------


class LightLine(Transition):
    """A line of light runs across, the old screen ahead of it and the new one behind, and
    goes out as it leaves (after Lumen's lit rings)."""

    name = "light"
    api = 1
    duration = 1.1
    toasts = "over"
    from_shown = True

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._glow: tuple[tuple[int, int], Image.Image] | None = None

    def _strip(self, w: int, h: int) -> Image.Image:
        """The line's light, upright, made once: a horizontal line drawn along a strip as long
        as the frame is high, turned."""
        if self._glow is None or self._glow[0] != (w, h):
            u = min(w, h) / 480
            half = max(3, round(32 * u))
            strip = Image.new("RGB", (h + 2 * half, 2 * half))
            accent, white = role_color(self.context, "info"), role_color(self.context, "text")
            add_line(strip, -half, h + 3 * half, half, accent, 1.0, u, 1.0, white=white)
            self._glow = (
                (w, h),
                strip.crop((half, 0, half + h, 2 * half)).transpose(Image.Transpose.ROTATE_90),
            )
        return self._glow[1]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        glow = self._strip(w, h)
        x = round(-glow.width + (w + 2 * glow.width) * smooth(t))
        out = _rgb(old).copy()
        if x > 0:
            out.paste(_rgb(new).crop((0, 0, min(w, x), h)), (0, 0))
        bright = ease_out(phase(t, 0.0, 0.15)) * (1.0 - ease_in(phase(t, 0.8, 1.0)))
        a, b = max(0, x - glow.width // 2), min(w, x + glow.width // 2)
        if b > a and bright > 0.004:
            light = glow.crop((a - (x - glow.width // 2), 0, b - (x - glow.width // 2), h))
            if bright < 0.999:
                light = light.point(lambda v: round(v * bright))
            out.paste(ImageChops.add(out.crop((a, 0, b, h)), light), (a, 0))
        return out


class Glitch(Transition):
    """A short digital fault: rows slip sideways, the colours split, bits of the other screen
    flash in; at its height the new screen takes over and the fault fades."""

    name = "glitch"
    api = 1
    duration = 0.7
    toasts = "over"

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        strength = 1.0 - abs(t - 0.5) * 2  # strongest in the middle
        base, other = (_rgb(old), _rgb(new)) if t < 0.5 else (_rgb(new), _rgb(old))
        chance = random.Random(int(t * 40))  # a new fault a few times a second, not every frame
        out = base.copy()
        for _ in range(round(10 * strength) + 1):  # bands of rows, slipped
            top = chance.randrange(h)
            tall = max(2, round(chance.uniform(4, 40) * u))
            shift = round(chance.uniform(-80, 80) * u * strength)
            band = (other if chance.random() < 0.25 else base).crop((0, top, w, min(h, top + tall)))
            out.paste(ImageChops.offset(band, shift, 0), (0, top))
        split = round(12 * u * strength)
        if split:
            red, green, blue = out.split()
            out = Image.merge(
                "RGB", (ImageChops.offset(red, split, 0), green, ImageChops.offset(blue, -split, 0))
            )
        return out


class Tiles(Transition):
    """The screen turns over tile by tile, from one corner across, the new one on their backs."""

    name = "tiles"
    api = 1
    duration = 1.2
    toasts = "over"
    SPREAD = 0.55  # the share of the time over which the tiles begin, one after the other

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        side = max(16, round(120 * u))
        cols, rows = math.ceil(w / side), math.ceil(h / side)
        dark = role_color(self.context, "void")
        out = Image.new("RGB", (w, h), dark)
        old, new = _rgb(old), _rgb(new)
        last = max(1, cols + rows - 2)
        for row in range(rows):
            for col in range(cols):
                box = (col * side, row * side, min(w, (col + 1) * side), min(h, (row + 1) * side))
                start = self.SPREAD * (col + row) / last
                turn = smooth(phase(t, start, start + 1.0 - self.SPREAD))
                width = abs(math.cos(math.pi * turn))
                face = (old if turn < 0.5 else new).crop(box)
                if width >= 0.999:
                    out.paste(face, box[:2])
                    continue
                size = (max(1, round(face.width * width)), face.height)
                if size[0] <= 1:
                    continue
                edge = face.resize(size, Image.Resampling.BILINEAR)
                edge = Image.blend(edge, Image.new("RGB", size, dark), 0.5 * (1.0 - width))
                out.paste(edge, (box[0] + (face.width - size[0]) // 2, box[1]))
        return out


class Shatter(Transition):
    """The old screen breaks into pieces that fly apart from the middle and fall, the new one
    already there behind them."""

    name = "shatter"
    api = 1
    duration = 1.3
    toasts = "over"

    def __init__(self, context: Any = None, params: dict[str, Any] | None = None) -> None:
        super().__init__(context, params)
        self._pieces: tuple[int, list[tuple[Image.Image, int, int, float, float, float]]] | None = (
            None
        )

    def _broken(self, old: Image.Image) -> list[tuple[Image.Image, int, int, float, float, float]]:
        """The pieces of the old frame, once: each with its place, its speed and its start."""
        if self._pieces is None or self._pieces[0] != id(old):
            w, h = old.size
            u = min(w, h) / 480
            side = max(12, round(60 * u))
            chance = random.Random(0xB10C)
            pieces = []
            cx, cy = w / 2, h / 2
            reach = math.hypot(cx, cy)
            picture = _rgb(old)
            for y in range(0, h, side):
                for x in range(0, w, side):
                    box = (x, y, min(w, x + side), min(h, y + side))
                    dx, dy = box[0] + side / 2 - cx, box[1] + side / 2 - cy
                    far = math.hypot(dx, dy) / reach
                    speed = (0.6 + chance.random()) * 900 * u
                    angle = math.atan2(dy, dx) + chance.uniform(-0.4, 0.4)
                    vx, vy = math.cos(angle) * speed, math.sin(angle) * speed - 300 * u
                    pieces.append((picture.crop(box), box[0], box[1], vx, vy, 0.45 * far))
            self._pieces = (id(old), pieces)
        return self._pieces[1]

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        if t >= 1.0:
            return new
        w, h = new.size
        u = min(w, h) / 480
        out = _rgb(new).copy()
        seconds = t * self.duration
        for piece, x, y, vx, vy, start in self._broken(old):
            s = max(0.0, seconds - start * self.duration)
            px = round(x + vx * s)
            py = round(y + vy * s + 0.5 * 2400 * u * s * s)  # and it falls
            if -piece.width < px < w and -piece.height < py < h:
                out.paste(piece, (px, py))
        return out


# the transitions this module brings, by name (render/overlays.py adds them to the built-in ones)
HANDOVERS = {cls.name: cls for cls in (Wipe, Iris, Blinds, Dissolve, SlideRight, SlideUp, SlideDown,
                                       Zoom, LightLine, Glitch, Tiles, Shatter)}  # fmt: skip

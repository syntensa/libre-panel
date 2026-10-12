"""The panel's own two screens, in Libre Panel's look: the boot logo it shows when it is powered
on, and the standby clip it plays when the PC's USB comes up, until Libre Panel streams
(devices/panel_files.py puts them on a TURZX USB panel).

Both are drawn for the panel's size, in landscape (a round panel square): the app's mark (a
gauge's arc round three bars, as its icon), the name, and the line of light under them, on
the dark backdrop of Lumen with its dot grid. Nothing that matters is near the short ends:
the panel draws its boot JPEG into a smaller area that leaves about a thirtieth of the long
side at both ends as it was (measured on the 9.2"), and they fade out to black. The strip the
bezel hides (``hidden`` in the catalog) is left out of the middle.

The clip moves only in brightness at fixed places, which the panel's uneven playback forgives
(a moving dot would stutter): the line of light is a row of dashes along which a soft wave of
light runs outwards from the middle, and the mark's arc breathes with it. It has
:data:`CLIP_FRAMES` pictures, a multiple of the 11 the panel's decoder takes per sequence, and
plays at :data:`CLIP_FPS`, the rate the panel itself sets at a cold start.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from libre_panel.devices.models import PanelModel
from libre_panel.fonts import builtin_font_path

CLIP_FRAMES = 44  # four sequences of 11 pictures
CLIP_FPS = 30  # the panel's own rate after a cold start: the loop is about 1.5 s
QUIET = 64 / 1920  # of the long side, at both ends: the boot JPEG leaves it as it was

BG = (10, 14, 19)
BG2 = (6, 9, 13)
DOTS = (22, 30, 40)
TEXT = (232, 238, 244)
ACCENT = (46, 230, 214)
TRACK = (42, 52, 64)
SS = 3  # supersampling of the shapes


@dataclass(frozen=True)
class Layout:
    """Where the parts go on a picture ``size``: the mark's box (x, y, d), the name's left
    and the top of its capitals and its size, the line of light (x0, x1, y) and its dashes."""

    size: tuple[int, int]
    mark: tuple[float, float, float]
    name: tuple[float, float, float]
    line: tuple[float, float, float]
    dashes: int
    unit: float  # px per px of the 1920 x 480 design


def design_size(model: PanelModel) -> tuple[int, int]:
    """The picture's size before it is turned for the panel: landscape, a round one square."""
    return model.size("landscape")


def _font(size: float) -> ImageFont.FreeTypeFont:
    path = builtin_font_path("builtin:Barlow-SemiBold")
    return ImageFont.truetype(str(path), max(6, round(size)))


def _name_width(size: float) -> float:
    return _font(size).getlength("Libre Panel")


def layout(model: PanelModel) -> Layout:
    """The parts of the picture for ``model``: side by side on a bar, the name under the mark
    on a broad panel, and smaller within the circle of a round one."""
    w, h = design_size(model)
    left, top, right, bottom = getattr(model, "hidden", (0, 0, 0, 0)) or (0, 0, 0, 0)
    quiet = round(QUIET * max(w, h))
    room_x0, room_x1 = left + quiet, w - right - quiet
    room_y0, room_y1 = top, h - bottom
    room_w, room_h = room_x1 - room_x0, room_y1 - room_y0
    cx, cy = (room_x0 + room_x1) / 2, (room_y0 + room_y1) / 2
    aspect = room_w / max(1, room_h)
    round_panel = getattr(model, "shape", "rect") == "round"
    if aspect >= 2.4:  # a bar: the mark beside the name, the line under both
        d = room_h * 0.42
        size = room_h * 0.2
        gap = d * 0.28
        group = d + gap + _name_width(size)
        scale = min(1.0, room_w * 0.8 / group)
        d, size, gap, group = d * scale, size * scale, gap * scale, group * scale
        x = cx - group / 2
        block = d + room_h * 0.16
        y = cy - block / 2
        name_top = y + d / 2 - 0.7 * size / 2
        line_y = y + d + room_h * 0.16
        half = max(group * 0.62, room_w * 0.28)
        return Layout((w, h), (x, y, d), (x + d + gap, name_top, size),
                      (cx - half, cx + half, line_y), 31, room_h / 480)  # fmt: skip
    # broad, square or round: the name under the mark
    fit = 0.62 if round_panel else 0.86
    d = min(room_h * 0.4, room_w * 0.3) * (0.78 if round_panel else 1.0)
    size = d * 0.42
    name_w = _name_width(size)
    if name_w > room_w * fit:
        size *= room_w * fit / name_w
        name_w = _name_width(size)
    block = d + d * 0.24 + 0.7 * size + d * 0.3
    y = cy - block / 2
    name_top = y + d + d * 0.24
    line_y = name_top + 0.7 * size + d * 0.3
    half = min(max(name_w * 0.62, d * 0.9), room_w * fit / 2)
    unit = min(room_w / 1920, room_h / 480) * 1.6
    return Layout((w, h), (cx - d / 2, y, d), (cx - name_w / 2, name_top, size),
                  (cx - half, cx + half, line_y), 21, unit)  # fmt: skip


@lru_cache(maxsize=8)
def _backdrop(w: int, h: int, quiet: int) -> Image.Image:
    """Lumen's backdrop: a dark gradient with a dot every 22 px (at 480 px high), fading to
    black at the short ends."""
    unit = min(w, h) / 480
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    bg, bg2 = np.array(BG, np.float32), np.array(BG2, np.float32)
    pixels = np.broadcast_to(bg + (bg2 - bg) * ys, (h, w, 3)).copy()
    step = max(6, round(22 * unit))
    r = max(0.6, 1.1 * unit)
    dots = Image.new("L", (w * SS, h * SS), 0)
    draw = ImageDraw.Draw(dots)
    for y in range(step // 2, h, step):
        for x in range(step // 2, w, step):
            draw.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=255)
    dots = np.asarray(dots.reduce(SS), np.float32)[..., None] / 255
    pixels += (np.array(DOTS, np.float32) - pixels) * dots * 0.9
    # the short ends fade to black (the long axis is the picture's long side)
    long_axis = 1 if w >= h else 0
    n = w if long_axis else h
    edge = np.ones(n, np.float32)
    ramp = max(1, round(quiet * 2.2))
    fade = np.clip(np.arange(n, dtype=np.float32) / ramp, 0, 1) ** 1.6
    edge = np.minimum(fade, fade[::-1])
    pixels *= edge[None, :, None] if long_axis else edge[:, None, None]
    return Image.fromarray(np.clip(pixels + 0.5, 0, 255).astype(np.uint8), "RGB")


def _mark(d: float, arc_light: float = 1.0) -> Image.Image:
    """The app's mark at ``d`` px: a gauge's arc (lit from bottom left over the top to the
    right, its last part dark) round three bars, the last one lit; with its glow."""
    size = max(8, math.ceil(d * 1.5))
    s = SS
    shape = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shape)
    r = d / 2
    cx = cy = size / 2
    t = d * 0.1
    box = [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s]  # (a wide arc is drawn inward)
    width = max(1, round(t * s))
    accent = tuple(round(c * (0.55 + 0.45 * arc_light)) for c in ACCENT)

    def cap(angle: float, colour: tuple[int, ...]) -> None:
        a = math.radians(angle)
        px, py = cx + (r - t / 2) * math.cos(a), cy + (r - t / 2) * math.sin(a)
        draw.ellipse([(px - t / 2) * s, (py - t / 2) * s, (px + t / 2) * s, (py + t / 2) * s],
                     fill=(*colour, 255))  # fmt: skip

    # the dark end of the gauge first, then the lit arc over its beginning
    draw.arc(box, 330, 412, fill=(*TRACK, 255), width=width)
    cap(412, TRACK)
    draw.arc(box, 135, 350, fill=(*accent, 255), width=width)
    cap(135, accent)
    cap(350, accent)
    base = cy + r * 0.36
    bar_w = r * 0.21
    for x, tall, colour in ((-0.47, 0.34, TEXT), (-0.14, 0.54, TEXT), (0.19, 0.78, accent)):
        x0 = cx + x * r
        draw.rounded_rectangle([x0 * s, (base - tall * r) * s, (x0 + bar_w) * s, base * s],
                               radius=bar_w * 0.32 * s, fill=(*colour, 255))  # fmt: skip
    shape = shape.convert("RGBa").reduce(s).convert("RGBA")
    # its glow: the lit parts, blurred, as light around them
    lit = Image.new("RGB", shape.size, (0, 0, 0))
    lit.paste(shape.convert("RGB"), (0, 0), shape.getchannel("A"))
    glow = lit.filter(ImageFilter.GaussianBlur(max(1.0, d * 0.06)))
    glow = glow.point(lambda v: round(v * (0.5 + 0.35 * arc_light)))
    out = Image.new("RGBA", shape.size, (0, 0, 0, 0))
    out.paste(glow.convert("RGBA"), (0, 0))
    out.putalpha(glow.convert("L"))
    out.alpha_composite(shape)
    return out


def _wave(i: int, count: int, phase: float) -> float:
    """How lit dash ``i`` of ``count`` is at ``phase`` (0 to 1): a soft wave running out from
    the middle to both ends, over a light that never goes out."""
    middle = (count - 1) / 2
    distance = abs(i - middle) / max(1.0, middle)  # 0 in the middle, 1 at the ends
    front = phase * 1.35 - 0.15
    crest = math.exp(-(((distance - front) / 0.16) ** 2))
    return 0.28 + 0.72 * crest * (1 - 0.35 * distance)


@lru_cache(maxsize=4)
def _base(model: PanelModel) -> Image.Image:
    """What does not move: the backdrop and the name."""
    lay = layout(model)
    w, h = lay.size
    out = _backdrop(w, h, round(QUIET * max(w, h))).copy().convert("RGBA")
    nx, top, size = lay.name
    font = _font(size)
    draw = ImageDraw.Draw(out)
    baseline = top + 0.7 * size
    draw.text((nx, baseline), "Libre ", font=font, fill=(*TEXT, 255), anchor="ls")
    draw.text((nx + font.getlength("Libre "), baseline), "Panel", font=font, fill=(*ACCENT, 255),
              anchor="ls")  # fmt: skip
    return out


def picture(model: PanelModel, phase: float | None = None) -> Image.Image:
    """The picture for ``model``, RGB, in landscape (a round one square): the boot logo with
    ``phase`` None, else the standby clip at that point of its loop (0 to 1)."""
    lay = layout(model)
    out = _base(model).copy()
    x, y, d = lay.mark
    breathe = 1.0 if phase is None else 0.75 + 0.25 * math.cos(2 * math.pi * phase)
    mark = _mark(d, round(breathe, 3))
    at = (round(x + d / 2 - mark.width / 2), round(y + d / 2 - mark.height / 2))
    out.alpha_composite(mark, at)
    _line(out, lay, phase)
    return out.convert("RGB")


def _line(out: Image.Image, lay: Layout, phase: float | None) -> None:
    """The line of light: one soft line in the boot logo, a row of dashes the light runs
    along in the clip; with its glow. Drawn in a band round it."""
    x0, x1, y = lay.line
    w, h = lay.size
    thick = max(2.0, 4.0 * lay.unit)
    reach = math.ceil(max(12.0, 70 * lay.unit))  # how far its glow goes
    bx0, by0 = max(0, math.floor(x0) - reach), max(0, math.floor(y) - reach)
    bx1, by1 = min(w, math.ceil(x1) + reach), min(h, math.ceil(y) + reach)
    bw, bh = bx1 - bx0, by1 - by0
    lit = Image.new("RGB", (bw * SS, bh * SS), (0, 0, 0))
    draw = ImageDraw.Draw(lit)

    def at(px: float, py: float) -> tuple[float, float]:
        return (px - bx0) * SS, (py - by0) * SS

    if phase is None:  # a line, brightest in the middle
        steps = 64
        for i in range(steps):
            a, b = x0 + (x1 - x0) * i / steps, x0 + (x1 - x0) * (i + 1) / steps
            middle = abs((a + b) / 2 - (x0 + x1) / 2) / ((x1 - x0) / 2)
            level = (1 - middle**1.8) ** 1.2
            colour = tuple(round(c * level) for c in ACCENT)
            draw.rectangle([*at(a, y - thick / 2), at(b, y + thick / 2)[0] + SS,
                            at(b, y + thick / 2)[1]], fill=colour)  # fmt: skip
    else:
        count = lay.dashes
        step = (x1 - x0) / count
        for i in range(count):
            level = _wave(i, count, phase)
            colour = tuple(round(c * level) for c in ACCENT)
            a = x0 + i * step + step * 0.18
            b = x0 + (i + 1) * step - step * 0.18
            draw.rounded_rectangle([*at(a, y - thick / 2), *at(b, y + thick / 2)],
                                   radius=thick / 2 * SS, fill=colour)  # fmt: skip
    lit = lit.reduce(SS)
    glow = lit.filter(ImageFilter.GaussianBlur(max(2.0, 10 * lay.unit)))
    glow = ImageChops.add(glow, glow.filter(ImageFilter.GaussianBlur(max(4.0, 26 * lay.unit))))
    box = (bx0, by0, bx1, by1)
    band = out.crop(box).convert("RGB")
    band = ImageChops.lighter(ImageChops.add(band, glow), lit)
    out.paste(band.convert("RGBA"), box[:2])


def boot_logo(model: PanelModel) -> Image.Image:
    """The boot logo for ``model``, in landscape (a round one square)."""
    return picture(model)


def standby_frames(model: PanelModel, count: int = CLIP_FRAMES) -> list[Image.Image]:
    """The standby clip's pictures for ``model``, one loop, in landscape."""
    return [picture(model, i / count) for i in range(count)]

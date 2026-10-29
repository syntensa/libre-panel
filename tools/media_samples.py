"""Made-up videos and pictures for the Player's clip (tools/media_app.py): drawn here, frame by
frame, so that the clip shows nothing of anybody else's (no film, no photo) and is the same on
every run.

Each sample is a function of the time and the size it is wanted at: :func:`frame` draws one
picture of it (``t`` seconds into it), :data:`SAMPLES` says what the library calls it, what
kind of file it is and how long it plays. The pictures are drawn with NumPy and Pillow, a few
milliseconds each at the size the app shows them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

Draw = Callable[[float, int, int], Image.Image]


@dataclass(frozen=True)
class Sample:
    """A file of the made-up library: its kind (player.KINDS), how long it plays, and how a
    picture of it is drawn."""

    kind: str  # "video", "animation" or "picture"
    seconds: float  # a round of it (0: a picture)
    draw: Draw
    fps: float | None = None


def _gradient(h: int, w: int, stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    """A top-to-bottom gradient through ``stops`` (position 0..1, colour), as float RGB."""
    y = np.linspace(0.0, 1.0, h)
    out = np.zeros((h, 3))
    positions = [p for p, _ in stops]
    for c in range(3):
        out[:, c] = np.interp(y, positions, [colour[c] for _, colour in stops])
    return np.repeat(out[:, None, :], w, axis=1)


def _stars(h: int, w: int, t: float, seed: int, count: int, top: float = 1.0) -> np.ndarray:
    """Twinkling stars in the upper ``top`` of the picture, as a float layer to add."""
    rng = np.random.default_rng(seed)
    layer = np.zeros((h, w))
    xs = rng.integers(0, w, count)
    ys = (rng.random(count) ** 1.6 * h * top).astype(int)
    phase = rng.random(count) * 2 * math.pi
    bright = 120 + 120 * rng.random(count)
    twinkle = 0.6 + 0.4 * np.sin(t * (1.5 + 2 * rng.random(count)) + phase)
    layer[ys.clip(0, h - 1), xs] = bright * twinkle
    return layer


def _ridge(w: int, seed: int, base: float, amplitude: float) -> np.ndarray:
    """The top of a range of hills across the width, in rows (a sum of slow sines)."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, w)
    line = np.full(w, base)
    for k in range(1, 6):
        line += amplitude / k * np.sin(2 * math.pi * (k * 1.7 * x + rng.random()))
    return line


def _picture(pixels: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


# -- the samples -------------------------------------------------------------------------------


def night_drive(t: float, w: int, h: int) -> Image.Image:
    """A road into a striped sun at dusk: the grid of the ground runs towards you."""
    horizon = round(h * 0.6)
    sky = _gradient(horizon, w, [(0.0, (9, 5, 30)), (0.55, (44, 12, 74)), (1.0, (196, 46, 120))])
    sky += _stars(horizon, w, t, 3, max(40, w * h // 9000), 0.7)[..., None]
    img = _picture(np.concatenate([sky, np.zeros((h - horizon, w, 3))], axis=0))
    draw = ImageDraw.Draw(img)
    # the sun: yellow at the top, pink at the bottom, cut by bands that sink
    r = h * 0.3
    cx, cy = w / 2, horizon - r * 0.62
    sun_px = _gradient(h, w, [(0.0, (255, 214, 102)), ((cy - r) / h, (255, 214, 102)),
                              ((cy + r) / h, (255, 60, 140)), (1.0, (255, 60, 140))])  # fmt: skip
    sun = _picture(sun_px)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    step = r * 0.17
    top = cy - r * 0.25  # the bands begin a little above the middle
    for k in range(-1, 10):
        y = top + k * step + (t * step * 0.5) % step
        thick = (y - top) / r * step * 0.55
        if thick >= 1:
            md.rectangle([cx - r, y, cx + r, y + thick], fill=0)
    glow = mask.filter(ImageFilter.GaussianBlur(r * 0.25))
    img.paste(Image.new("RGB", (w, h), (255, 70, 150)), (0, 0),
              glow.point(lambda v: v * 0.45))  # fmt: skip
    img.paste(sun, (0, 0), mask)
    # the hills, two ranges, in front of the sun's foot
    for seed, base, amp, colour in ((5, 0.92, 0.05, (52, 14, 70)), (8, 0.97, 0.03, (24, 8, 40))):
        ridge = _ridge(w, seed, horizon * base, h * amp)
        points = [(x, ridge[x]) for x in range(0, w, max(1, w // 240))] + [
            (w, horizon),
            (0, horizon),
        ]
        draw.polygon(points, fill=colour)
    # the ground and its grid, which runs towards you
    ground = _gradient(h - horizon, w, [(0.0, (30, 6, 46)), (1.0, (8, 3, 16))])
    img.paste(_picture(ground), (0, horizon))
    lines = Image.new("RGB", (w, h))
    ld = ImageDraw.Draw(lines)
    depth = h - horizon
    for k in range(18):
        z = (k + 1 - (t * 1.6) % 1.0) / 18  # 0 near, 1 at the horizon
        y = horizon + depth * (1 - z) ** 2.2
        ld.line([(0, y), (w, y)], fill=(255, 64, 196), width=max(1, round(h / 300)))
    for k in range(-14, 15):
        ld.line([(cx + k * w * 0.012, horizon), (cx + k * w * 0.16, h)], fill=(80, 220, 255),
                width=max(1, round(h / 360)))  # fmt: skip
    ld.line([(0, horizon), (w, horizon)], fill=(255, 140, 220), width=max(1, round(h / 260)))
    halo = lines.filter(ImageFilter.GaussianBlur(h / 90))
    base = np.asarray(img, np.float32)
    base += np.asarray(lines, np.float32) * 0.85 + np.asarray(halo, np.float32) * 1.4
    return _picture(base)


def aurora(t: float, w: int, h: int) -> Image.Image:
    """Northern lights over dark hills: green curtains that sway, purple at their tops."""
    sky = _gradient(h, w, [(0.0, (3, 6, 18)), (0.7, (8, 20, 38)), (1.0, (10, 28, 44))])
    sky += _stars(h, w, t, 11, max(60, w * h // 6000), 0.8)[..., None]
    small_w, small_h = max(64, w // 4), max(24, h // 4)
    x = np.linspace(0, 1, small_w)[None, :]
    y = np.linspace(0, 1, small_h)[:, None]
    light = np.zeros((small_h, small_w))
    for k, (speed, width, height) in enumerate(((0.22, 0.09, 0.32), (0.17, 0.07, 0.42),
                                                (0.13, 0.11, 0.25))):  # fmt: skip
        centre = 0.42 + 0.12 * np.sin(2 * math.pi * (x * (1.1 + k * 0.4) + t * speed + k))
        centre += 0.05 * np.sin(2 * math.pi * (x * 4.3 - t * speed * 1.7))
        curtain = np.exp(-(((y - centre) / width) ** 2)) * (y > centre - height)
        rays = 0.6 + 0.4 * np.sin(2 * math.pi * (x * 38 + t * 0.4 + k)) ** 2
        light += curtain * rays * (0.9 - 0.2 * k)
    light = np.asarray(Image.fromarray((np.clip(light, 0, 1.4) / 1.4 * 255).astype(np.uint8))
                       .resize((w, h), Image.BICUBIC), np.float32) / 255  # fmt: skip
    height = np.linspace(0, 1, h)[:, None]
    green = np.stack([40 + 0 * height, 255 - 60 * (1 - height), 160 - 40 * height], axis=-1)
    purple = np.array([170.0, 70.0, 230.0])
    tint = green * (height[..., None] ** 0.6) + purple * (1 - height[..., None] ** 0.6)
    pixels = sky + light[..., None] * tint * 1.05
    img = _picture(pixels)
    draw = ImageDraw.Draw(img)
    for seed, base, amp, colour in ((21, 0.8, 0.07, (6, 12, 20)), (24, 0.9, 0.05, (2, 5, 9))):
        ridge = _ridge(w, seed, h * base, h * amp)
        draw.polygon([(x_, ridge[x_]) for x_ in range(0, w, max(1, w // 240))] + [(w, h), (0, h)],
                     fill=colour)  # fmt: skip
    return img


def lava_lamp(t: float, w: int, h: int) -> Image.Image:
    """Warm blobs that rise, meet and part again."""
    small_w, small_h = max(80, w // 5), max(30, h // 5)
    x = np.linspace(0, w / h, small_w)[None, :]
    y = np.linspace(0, 1, small_h)[:, None]
    field = np.zeros((small_h, small_w))
    for k in range(7):
        bx = (0.18 + 0.64 * ((k * 0.381) % 1)) * w / h + 0.12 * math.sin(t * 0.7 + k * 1.9)
        by = 0.5 + 0.42 * math.sin(t * (0.35 + 0.07 * k) + k * 2.3)
        r = 0.13 + 0.05 * math.sin(k * 1.3 + t * 0.5)
        field += r * r / ((x - bx) ** 2 + (y - by) ** 2 + 1e-4)
    field = np.asarray(Image.fromarray((np.clip(field, 0, 4) * 63).astype(np.uint8))
                       .resize((w, h), Image.BICUBIC), np.float32) / 63  # fmt: skip
    inside = np.clip((field - 0.9) * 3.0, 0, 1)
    rim = np.clip(1 - np.abs(field - 1.0) * 4, 0, 1)
    back = _gradient(h, w, [(0.0, (60, 6, 24)), (1.0, (120, 22, 18))])
    blob = _gradient(h, w, [(0.0, (255, 196, 70)), (1.0, (255, 96, 40))])
    pixels = back * (1 - inside[..., None]) + blob * inside[..., None]
    pixels += rim[..., None] * np.array([90.0, 40.0, 10.0])
    return _picture(pixels)


def starfield(t: float, w: int, h: int) -> Image.Image:
    """Through the stars: they come from the middle and pass you, faster as they come near."""
    rng = np.random.default_rng(31)
    count = 900
    angle = rng.random(count) * 2 * math.pi
    speed = 0.25 + rng.random(count) * 0.35
    offset = rng.random(count)
    img = Image.new("RGB", (w, h), (2, 4, 12))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([w * 0.3, h * 0.2, w * 0.7, h * 0.8], fill=60)
    glow = glow.filter(ImageFilter.GaussianBlur(h * 0.25))
    img.paste(Image.new("RGB", (w, h), (40, 70, 160)), (0, 0), glow)
    draw = ImageDraw.Draw(img)
    cx, cy, reach = w / 2, h / 2, math.hypot(w, h) / 2
    for a, s, o in zip(angle, speed, offset, strict=True):
        d = ((t * s + o) % 1.0) ** 2.4
        tail = max(0.0, d - 0.02 * s * (1 + 6 * d))
        x0, y0 = cx + math.cos(a) * reach * tail, cy + math.sin(a) * reach * tail * 0.9
        x1, y1 = cx + math.cos(a) * reach * d, cy + math.sin(a) * reach * d * 0.9
        level = round(90 + 165 * min(1.0, d * 2.2))
        draw.line([(x0, y0), (x1, y1)], fill=(level, level, min(255, level + 30)),
                  width=max(1, round(1 + d * h / 240)))  # fmt: skip
    return img


def city_lights(t: float, w: int, h: int) -> Image.Image:
    """A city at night: windows light up and go out, cars draw lines of light below."""
    rng = np.random.default_rng(41)
    img = _picture(_gradient(h, w, [(0.0, (8, 10, 26)), (0.75, (34, 26, 60)), (1.0, (70, 40, 70))]))
    draw = ImageDraw.Draw(img)
    moon = (w * 0.82, h * 0.2, h * 0.07)
    draw.ellipse([moon[0] - moon[2], moon[1] - moon[2], moon[0] + moon[2], moon[1] + moon[2]],
                 fill=(236, 232, 214))  # fmt: skip
    ground = h * 0.84
    x = 0.0
    k = 0
    while x < w:
        bw = w * (0.035 + 0.05 * rng.random())
        bh = h * (0.25 + 0.5 * rng.random())
        shade = 14 + int(16 * rng.random())
        draw.rectangle([x, ground - bh, x + bw - 2, ground], fill=(shade, shade + 2, shade + 10))
        cols, rows = max(2, int(bw / (h * 0.022))), max(3, int(bh / (h * 0.035)))
        for c in range(cols):
            for r in range(rows):
                seed = (k * 997 + c * 31 + r * 7) % 1000 / 1000
                on = math.sin(t * (0.3 + seed) + seed * 40) > 0.15 - 0.6 * seed
                if not on:
                    continue
                wx = x + (c + 0.5) * bw / cols
                wy = ground - bh + (r + 0.6) * bh / (rows + 0.4)
                size = h * 0.007
                warm = (255, 206, 120) if seed > 0.3 else (160, 210, 255)
                draw.rectangle([wx - size, wy - size, wx + size, wy + size], fill=warm)
        x += bw
        k += 1
    draw.rectangle([0, ground, w, h], fill=(10, 10, 16))
    lights = Image.new("RGB", (w, h))
    ld = ImageDraw.Draw(lights)
    for lane, (colour, speed) in enumerate((((255, 230, 180), 0.21), ((255, 60, 60), -0.17))):
        y = ground + (h - ground) * (0.35 + 0.35 * lane)
        for n in range(6):
            pos = ((t * speed + n / 6) % 1.0) * (w * 1.2) - w * 0.1
            ld.line([(pos, y), (pos + w * 0.06, y)], fill=colour, width=max(1, round(h / 160)))
    halo = lights.filter(ImageFilter.GaussianBlur(h / 70))
    pixels = np.asarray(img, np.float32) + np.asarray(lights, np.float32)
    return _picture(pixels + np.asarray(halo, np.float32) * 1.6)


def dusk_at_sea(t: float, w: int, h: int) -> Image.Image:
    """The sun on the sea at dusk: a picture, it does not move."""
    horizon = round(h * 0.58)
    sky = _gradient(horizon, w, [(0.0, (30, 34, 84)), (0.6, (196, 96, 120)), (1.0, (255, 170, 96))])
    sea = _gradient(h - horizon, w, [(0.0, (120, 70, 96)), (1.0, (20, 22, 48))])
    img = _picture(np.concatenate([sky, sea], axis=0))
    draw = ImageDraw.Draw(img)
    r = h * 0.11
    cx = w * 0.5
    draw.ellipse([cx - r, horizon - r * 1.2, cx + r, horizon + r * 0.8], fill=(255, 214, 140))
    img.paste(_picture(sea), (0, horizon))
    for k in range(26):
        y = horizon + (h - horizon) * (k / 26) ** 1.4 + 2
        half = r * (1.2 - k / 30) * (0.6 + 0.4 * math.sin(k * 2.1))
        draw.line([(cx - half, y), (cx + half, y)], fill=(255, 190, 120),
                  width=max(1, round(h / 300 + k / 12)))  # fmt: skip
    return img


SAMPLES: dict[str, Sample] = {
    "Night drive.mp4": Sample("video", 214.0, night_drive, 30.0),
    "Northern lights.mp4": Sample("video", 187.0, aurora, 30.0),
    "Lava lamp.gif": Sample("animation", 12.0, lava_lamp, 25.0),
    "Through the stars.mp4": Sample("video", 95.0, starfield, 30.0),
    "City at night.webm": Sample("video", 142.0, city_lights, 30.0),
    "Dusk at sea.jpg": Sample("picture", 0.0, dusk_at_sea),
}


def frame(name: str, t: float, size: tuple[int, int]) -> Image.Image:
    """A picture of sample ``name`` ``t`` seconds into it, at ``size``."""
    sample = SAMPLES[name]
    if sample.seconds:
        t %= sample.seconds
    return sample.draw(t, *size)

"""Built-in line icons, drawn as vectors so they stay sharp at any size.

Coordinates use a 24x24 grid (like common icon sets); everything is drawn at
4x and downsampled. ``weather`` is resolved by the renderer from the WMO code.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 4  # supersampling

ICON_NAMES = (
    "cpu",
    "gpu",
    "ram",
    "disk",
    "network",
    "download",
    "upload",
    "temperature",
    "fan",
    "power",
    "clock",
    "sun",
    "moon",
    "cloud",
    "partly",
    "rain",
    "snow",
    "storm",
    "fog",
    "humidity",
    "wind",
    "battery",
    "plug",
    "list",
    "grid",
    "signal",
    "weather",
)

Color = tuple[int, int, int, int]


class _Pen:
    def __init__(self, size: int, color: Color, stroke: float) -> None:
        self.s = size * SS / 24
        self.img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.img)
        self.color = color
        self.w = max(1, round(stroke * SS * size / 24))

    def p(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.s, y * self.s)

    def _cap(self, x: float, y: float) -> None:
        cx, cy = self.p(x, y)
        r = self.w / 2
        self.draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=self.color)

    def line(self, *points: tuple[float, float]) -> None:
        self.draw.line([self.p(*pt) for pt in points], fill=self.color, width=self.w, joint="curve")
        self._cap(*points[0])
        self._cap(*points[-1])

    def rect(self, x0: float, y0: float, x1: float, y1: float, r: float = 0) -> None:
        box = [*self.p(x0, y0), *self.p(x1, y1)]
        self.draw.rounded_rectangle(box, radius=r * self.s, outline=self.color, width=self.w)

    def circle(self, cx: float, cy: float, r: float, fill: bool = False) -> None:
        box = [*self.p(cx - r, cy - r), *self.p(cx + r, cy + r)]
        if fill:
            self.draw.ellipse(box, fill=self.color)
        else:
            self.draw.ellipse(box, outline=self.color, width=self.w)

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float) -> None:
        box = [*self.p(cx - r, cy - r), *self.p(cx + r, cy + r)]
        self.draw.arc(box, a0, a1, fill=self.color, width=self.w)
        for a in (a0, a1):
            self._cap(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))

    def polygon(self, points: list[tuple[float, float]]) -> None:
        pts = [self.p(*pt) for pt in points]
        self.draw.line([*pts, pts[0]], fill=self.color, width=self.w, joint="curve")

    def shape(self, paint: Callable[[ImageDraw.ImageDraw, Callable], None], erase=None) -> None:
        """Outline of an arbitrary filled shape (union of circles, rects, ...)."""
        mask = Image.new("L", self.img.size, 0)
        paint(ImageDraw.Draw(mask), self.p)
        if erase is not None:
            hole = Image.new("L", self.img.size, 0)
            erase(ImageDraw.Draw(hole), self.p)
            mask = ImageChops.subtract(mask, hole)
        # Erode by the stroke width: a Gaussian blur is O(1) per pixel whatever the
        # radius, and thresholding it at 2 sigma gives a round structuring element.
        inner = mask.filter(ImageFilter.GaussianBlur(self.w / 2)).point(
            lambda v: 255 if v >= 249 else 0
        )
        ring = ImageChops.subtract(mask, inner)
        self.img.paste(self.color, (0, 0), ring)

    def clear(self, paint: Callable[[ImageDraw.ImageDraw, Callable], None]) -> None:
        """Erase a shape plus a gap of one stroke around it."""
        mask = Image.new("L", self.img.size, 0)
        paint(ImageDraw.Draw(mask), self.p)
        grown = mask.filter(ImageFilter.GaussianBlur(self.w / 2)).point(
            lambda v: 255 if v >= 6 else 0
        )
        self.img.paste((0, 0, 0, 0), (0, 0), grown)


def _cloud(draw: ImageDraw.ImageDraw, p: Callable, dx: float = 0, dy: float = 0) -> None:
    for cx, cy, r in ((8, 14.5, 4), (13, 11, 5.5), (17.5, 14.5, 3.8)):
        draw.ellipse([*p(cx - r + dx, cy - r + dy), *p(cx + r + dx, cy + r + dy)], fill=255)
    draw.rectangle([*p(8 + dx, 13 + dy), *p(17.5 + dx, 18.3 + dy)], fill=255)


def _cloud_top(pen: _Pen, dy: float = -3) -> None:
    pen.shape(lambda d, p: _cloud(d, p, 0, dy))


def _sun(pen: _Pen, cx: float, cy: float, r: float, ray: float) -> None:
    pen.circle(cx, cy, r)
    for i in range(8):
        a = math.radians(i * 45)
        pen.line(
            (cx + (r + 2) * math.cos(a), cy + (r + 2) * math.sin(a)),
            (cx + (r + 2 + ray) * math.cos(a), cy + (r + 2 + ray) * math.sin(a)),
        )


def _cpu(pen: _Pen) -> None:
    pen.rect(5, 5, 19, 19, 2)
    pen.rect(9, 9, 15, 15, 1)
    for v in (9, 12, 15):
        pen.line((v, 2), (v, 5))
        pen.line((v, 19), (v, 22))
        pen.line((2, v), (5, v))
        pen.line((19, v), (22, v))


def _gpu(pen: _Pen) -> None:
    pen.rect(2, 6, 22, 17, 2)
    pen.circle(9, 11.5, 3)
    pen.line((15, 9.5), (19, 9.5))
    pen.line((15, 13.5), (19, 13.5))
    for x in (6, 10, 14):
        pen.line((x, 17), (x, 20))


def _ram(pen: _Pen) -> None:
    pen.rect(2, 7, 22, 15, 1.5)
    for x in (5, 10.5, 16):
        pen.rect(x, 9.5, x + 3, 12.5, 0.5)
    for x in (6, 10, 14, 18):
        pen.line((x, 15), (x, 18))


def _disk(pen: _Pen) -> None:
    pen.rect(3, 4, 21, 20, 2.5)
    pen.circle(12, 11, 4)
    pen.circle(12, 11, 0.9, fill=True)
    pen.line((7, 17), (9.5, 17))


def _download(pen: _Pen) -> None:
    pen.line((12, 3), (12, 15))
    pen.line((7, 10), (12, 15), (17, 10))
    pen.line((5, 20), (19, 20))


def _upload(pen: _Pen) -> None:
    pen.line((12, 15), (12, 3))
    pen.line((7, 8), (12, 3), (17, 8))
    pen.line((5, 20), (19, 20))


def _network(pen: _Pen) -> None:
    pen.line((8, 3), (8, 19))
    pen.line((4, 15), (8, 19), (12, 15))
    pen.line((16, 21), (16, 5))
    pen.line((12, 9), (16, 5), (20, 9))


def _temperature(pen: _Pen) -> None:
    pen.shape(
        lambda d, p: (
            d.rounded_rectangle([*p(8.5, 1.5), *p(15.5, 15)], radius=3.5 * pen.s, fill=255),
            d.ellipse([*p(6.8, 12.3), *p(17.2, 22.7)], fill=255),
        )
    )
    pen.circle(12, 17.5, 2.2, fill=True)
    pen.line((12, 6.5), (12, 17))


def _fan(pen: _Pen) -> None:
    pen.circle(12, 12, 10)
    pen.circle(12, 12, 1.6, fill=True)
    for a in (0, 120, 240):
        pen.arc(
            12 + 4 * math.cos(math.radians(a + 60)),
            12 + 4 * math.sin(math.radians(a + 60)),
            4,
            a + 150,
            a + 290,
        )


def _power(pen: _Pen) -> None:
    pen.polygon([(13, 2), (4, 14), (11, 14), (10, 22), (20, 9.5), (13, 9.5)])


def _clock(pen: _Pen) -> None:
    pen.circle(12, 12, 9.5)
    pen.line((12, 6.5), (12, 12), (15.5, 14))


def _sun_icon(pen: _Pen) -> None:
    _sun(pen, 12, 12, 4, 2.5)


def _moon(pen: _Pen) -> None:
    pen.shape(
        lambda d, p: d.ellipse([*p(3.5, 3.5), *p(20.5, 20.5)], fill=255),
        erase=lambda d, p: d.ellipse([*p(10, 1), *p(24, 15)], fill=255),
    )


def _battery(pen: _Pen) -> None:
    pen.rect(2, 7, 19.5, 17, 2)
    pen.line((22, 10.5), (22, 13.5))
    pen.draw.rounded_rectangle([*pen.p(5, 10), *pen.p(12, 14)], radius=pen.s, fill=pen.color)


def _plug(pen: _Pen) -> None:
    pen.line((9, 2.5), (9, 7))
    pen.line((15, 2.5), (15, 7))
    pen.shape(
        lambda d, p: d.rounded_rectangle([*p(5.5, 7), *p(18.5, 15.5)], radius=4 * pen.s, fill=255)
    )
    pen.line((12, 15.5), (12, 21.5))


def _list(pen: _Pen) -> None:
    for y in (6, 12, 18):
        pen.circle(4.5, y, 1.4, fill=True)
        pen.line((9, y), (20, y))


def _grid(pen: _Pen) -> None:
    for x, y in ((3, 3), (13.5, 3), (3, 13.5), (13.5, 13.5)):
        pen.rect(x, y, x + 7.5, y + 7.5, 1.5)


def _signal(pen: _Pen) -> None:
    for i, x in enumerate((5, 10, 15, 20)):
        pen.line((x, 20), (x, 16 - i * 4))


def _cloud_icon(pen: _Pen) -> None:
    pen.shape(lambda d, p: _cloud(d, p))


def _partly(pen: _Pen) -> None:
    _sun(pen, 8.5, 8.5, 3, 1.8)
    pen.clear(lambda d, p: _cloud(d, p, 1.5, 3))
    pen.shape(lambda d, p: _cloud(d, p, 1.5, 3))


def _rain(pen: _Pen) -> None:
    _cloud_top(pen)
    for x in (8, 12, 16):
        pen.line((x, 17.5), (x - 1.5, 21.5))


def _snow(pen: _Pen) -> None:
    _cloud_top(pen)
    for x, y in ((8, 18.5), (12, 21), (16, 18.5)):
        pen.circle(x, y, 0.9, fill=True)


def _storm(pen: _Pen) -> None:
    _cloud_top(pen)
    pen.line((12.5, 16.5), (10, 20), (13.5, 20), (11, 23))


def _fog(pen: _Pen) -> None:
    _cloud_top(pen, -4)
    pen.line((4, 17.5), (20, 17.5))
    pen.line((6, 21), (18, 21))


def _humidity(pen: _Pen) -> None:
    pen.shape(
        lambda d, p: (
            d.polygon([p(12, 2.5), p(5.8, 13), p(18.2, 13)], fill=255),
            d.ellipse([*p(5.5, 8.5), *p(18.5, 21.5)], fill=255),
        )
    )


def _wind(pen: _Pen) -> None:
    pen.line((3, 8), (14, 8))
    pen.arc(14, 5.5, 2.5, -90, 90)
    pen.line((3, 12), (18, 12))
    pen.arc(18, 14.5, 2.5, -90, 90)
    pen.line((3, 16), (10, 16))


_ICONS: dict[str, Callable[[_Pen], None]] = {
    "cpu": _cpu,
    "gpu": _gpu,
    "ram": _ram,
    "disk": _disk,
    "network": _network,
    "download": _download,
    "upload": _upload,
    "temperature": _temperature,
    "fan": _fan,
    "power": _power,
    "clock": _clock,
    "sun": _sun_icon,
    "moon": _moon,
    "cloud": _cloud_icon,
    "partly": _partly,
    "rain": _rain,
    "snow": _snow,
    "storm": _storm,
    "fog": _fog,
    "humidity": _humidity,
    "wind": _wind,
    "battery": _battery,
    "plug": _plug,
    "list": _list,
    "grid": _grid,
    "signal": _signal,
}


def weather_icon_name(code: float | int | None, night: bool = False) -> str:
    """Icon for a WMO weather code as reported by Open-Meteo."""
    if code is None:
        return "cloud"
    code = int(code)
    if code in (0, 1):
        return "moon" if night else "sun"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    if 51 <= code <= 82:
        return "rain"
    return "cloud"


@lru_cache(maxsize=256)
def draw_icon(name: str, size: int, color: Color, stroke: float = 2.0) -> Image.Image:
    """RGBA icon of ``size`` x ``size`` pixels; ``stroke`` is in 24-grid units.

    Cached: callers must not modify the returned image.
    """
    size = max(8, min(int(size), 512))
    pen = _Pen(size, color, stroke)
    _ICONS.get(name, _cloud_icon)(pen)
    return pen.img.resize((size, size), Image.Resampling.LANCZOS)

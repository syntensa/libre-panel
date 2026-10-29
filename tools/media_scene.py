"""Scene clips for tools/readme_media.py: what the panel does beside the rest of the desk, drawn.

Some of what Libre Panel does happens on the desktop of the computer as much as on the panel: the
panel as a monitor of its own, which the desktop extends to. No recorder sees both, so a scene
clip draws the desk itself: a monitor, the panel in its device frame (tools/readme_images.py),
the desktop across the two, real pictures of the app and of the website in its windows, and a
mouse pointer that moves them, frame by frame on a clock of its own (the same clip on every run).

Each recipe is a :class:`SceneClip`. The pictures in its windows are taken once, in a browser
(Playwright, as the app clips; without it a scene is skipped, with a word about it).
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import media_app
import media_panel
import readme_images as images
from media_encode import FrameSink, Recorded, Settings
from PIL import Image, ImageChops, ImageDraw, ImageFilter

FPS = 25
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SceneClip:
    """One scene: what it shows, about how long, and the function that draws its frames."""

    about: str
    seconds: float
    draw: Callable[[Settings, FrameSink], int]  # returns the number of the poster's frame
    themes: tuple[str, ...] = ("lumen",)  # (the app opens with one; a scene shows none of it)

    @property
    def kind(self) -> str:
        return "scene"


def record(
    name: str,
    clip: SceneClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """Draw ``clip`` into ``folder``."""
    sink = FrameSink(folder, FPS)
    poster = clip.draw(settings, sink)
    return Recorded(sink.frames(), poster)


# -- pictures for the windows ------------------------------------------------------------------


@contextlib.contextmanager
def browser() -> Iterator[Any]:
    """A headless Chromium (Playwright), or :class:`media_app.Unavailable`."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise media_app.Unavailable(
            "Playwright is not installed (pip install playwright; playwright install chromium)"
        ) from error
    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as error:  # the browser is not installed
            raise media_app.Unavailable(
                f"Chromium is not available: {str(error).splitlines()[0]}"
            ) from error
        try:
            yield chromium
        finally:
            chromium.close()


def website(chromium: Any, width: int, height: int, scale: float) -> tuple[Image.Image, int]:
    """The project's website (site/, put together as it is published), as long as it is, at
    ``width`` CSS pixels drawn ``scale`` times as large (its clips show their posters: a still
    of the page), and where the head of its features begins in that picture."""
    import check_pages

    with tempfile.TemporaryDirectory(prefix="readme-scene-site-") as scratch:
        site = Path(scratch) / "site"
        check_pages.build(ROOT, site)
        page = chromium.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=scale, bypass_csp=True
        )
        page.goto((site / "index.html").as_uri())
        page.add_style_tag(content="*{transition:none!important;animation:none!important}")
        page.wait_for_timeout(600)
        features = page.evaluate(
            "document.querySelector('#features .head').getBoundingClientRect().top + scrollY"
        )
        png = page.screenshot(full_page=True)
        page.close()
    return Image.open(io.BytesIO(png)).convert("RGB"), round(features * scale)


# What the Screen page of the app says on Windows while the panel shows its own monitor: the
# monitors, the Virtual Display Driver set up, the panel's monitor on show.
SCREEN_STATE = {
    "available": True,
    "platform": "windows",
    "monitors": [
        {"id": "\\\\.\\DISPLAY1", "name": "DISPLAY1", "x": 0, "y": 0, "width": 2560,
         "height": 1440, "primary": True, "virtual": False},
        {"id": "\\\\.\\DISPLAY2", "name": "Libre Panel", "x": 320, "y": 1440, "width": 1920,
         "height": 720, "primary": False, "virtual": True},
    ],
    "windows": None,
    "showing": {"source": {"kind": "virtual", "id": "", "name": "", "region": None,
                           "fit": "contain", "cursor": True},
                "panels": [1], "since": 0.0},
    "panels": [{"number": 1, "name": 'Turing 12.3"'}],
    "fits": ["contain", "cover", "stretch"],
    "size": [1920, 720],
    "extend": {"how": "vdd", "missing": None, "running": False, "error": None},
}  # fmt: skip


def screen_page(chromium: Any, size: tuple[int, int], scale: float) -> Image.Image:
    """The app's Screen page as it is on Windows with the panel's own monitor on show."""
    theme, folder = media_app.theme_data("lumen")
    with media_app.running(theme, folder) as url:
        page = chromium.new_page(
            viewport={"width": size[0], "height": size[1]},
            device_scale_factor=scale,
            bypass_csp=True,
        )
        page.route(
            "**/api/mirror**",
            lambda route, request: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(SCREEN_STATE)
            ),
        )
        page.goto(url)  # (the app takes its token from the address, then goes to the page)
        page.wait_for_selector(".view > *")
        page.goto(f"{url.split('#')[0]}#/screen")
        page.wait_for_selector("[data-mirror-showing]")
        page.add_style_tag(content="*{transition:none!important;animation:none!important}")
        page.wait_for_timeout(400)
        png = page.screenshot()
        page.close()
    return Image.open(io.BytesIO(png)).convert("RGB")


# -- drawing the desk ----------------------------------------------------------------------------

MONITOR = (2560, 1440)  # the desktop's main monitor, in its own pixels
PANEL = (1920, 720)  # the 12.3" panel: its own monitor, below the main one on the desktop
PANEL_AT = ((MONITOR[0] - PANEL[0]) // 2, MONITOR[1])  # where it is on the desktop
K = 0.45  # a pixel of the desktop, in pixels of the picture
TITLE = 44  # a window's title bar, in pixels of the desktop
WALL_TOP, WALL_BOTTOM = (20, 34, 56), (8, 12, 22)


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def lerp(a: float, b: float, x: float) -> float:
    return a + (b - a) * x


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    def towards(self, other: Box, x: float) -> Box:
        return Box(*(lerp(a, b, x) for a, b in zip(self.astuple(), other.astuple(), strict=True)))

    def astuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


class Desk:
    """The picture: a monitor on its stand, the panel in front of it, the desktop on both."""

    def __init__(self) -> None:
        self.screen_m = (round(MONITOR[0] * K), round(MONITOR[1] * K))
        self.screen_p = (round(PANEL[0] * K), round(PANEL[1] * K))
        border = 14
        self.panel_frame = images.device(Image.new("RGB", self.screen_p), border)
        margin = images.shadow_margin(border)
        bezel = 16
        self.width = self.screen_m[0] + 2 * bezel + 2 * 90
        self.mx = (self.width - self.screen_m[0]) // 2  # the monitor's screen, in the picture
        self.my = 50
        self.px = (self.width - self.screen_p[0]) // 2
        self.py = self.my + self.screen_m[1] + bezel + 90
        self.height = self.py + self.screen_p[1] + 90
        self.panel_offset = (self.px - border - margin, self.py - border - margin)
        self.base = self._base(bezel)

    def _base(self, bezel: int) -> Image.Image:
        """The room, the monitor (its stand behind the panel) and the panel's frame."""
        img = images.background(self.width, self.height, glow=(0.5, 1.05)).convert("RGBA")
        d = ImageDraw.Draw(img)
        cx = self.width / 2
        top = self.my + self.screen_m[1] + bezel
        d.rounded_rectangle([cx - 46, top - 4, cx + 46, self.height - 58], 10, fill=(22, 28, 36))
        d.rounded_rectangle([cx - 230, self.height - 66, cx + 230, self.height - 44], 11,
                            fill=(26, 33, 42))  # fmt: skip
        shadow = Image.new("L", img.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            [self.mx - bezel, self.my - bezel + 16, self.mx + self.screen_m[0] + bezel,
             self.my + self.screen_m[1] + bezel + 16], 20, fill=190)  # fmt: skip
        img.paste((0, 0, 0, 255), (0, 0), shadow.filter(ImageFilter.GaussianBlur(26)))
        d.rounded_rectangle(
            [self.mx - bezel, self.my - bezel, self.mx + self.screen_m[0] + bezel,
             self.my + self.screen_m[1] + bezel], 18, fill=(52, 64, 80))  # fmt: skip
        d.rounded_rectangle(
            [self.mx - bezel + 2, self.my - bezel + 2, self.mx + self.screen_m[0] + bezel - 2,
             self.my + self.screen_m[1] + bezel - 2], 16, fill=(11, 14, 19))  # fmt: skip
        img.alpha_composite(self.panel_frame, self.panel_offset)
        return img

    def to_picture(self, x: float, y: float) -> tuple[float, float]:
        """A point of the desktop, in the picture (on the monitor, or on the panel below it)."""
        if y < MONITOR[1]:
            return self.mx + x * K, self.my + y * K
        return self.px + (x - PANEL_AT[0]) * K, self.py + (y - PANEL_AT[1]) * K


def wallpaper(size: tuple[int, int], hue: tuple[int, int, int]) -> Image.Image:
    """A desktop's background: dark, with a glow of ``hue`` low in it."""
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    for y in range(h):
        f = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(lerp(a, b, f)) for a, b in
                                            zip(WALL_TOP, WALL_BOTTOM, strict=True)))  # fmt: skip
    glow = Image.new("L", size, 0)
    ImageDraw.Draw(glow).ellipse([w * 0.1, h * 0.45, w * 0.9, h * 1.45], fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(min(w, h) * 0.25))
    return Image.composite(Image.new("RGB", size, hue), img, glow)


def window(
    content: Image.Image, size: tuple[float, float], title: str, address: str = ""
) -> Image.Image:
    """A window of the desktop ``size`` pixels large (the title bar in them) with ``content``
    filling the rest: a neutral title bar with its name, an address where it is a browser, and
    the three buttons on the right."""
    w, h = (round(v) for v in size)
    img = Image.new("RGB", (w, h), (16, 21, 28))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, TITLE], fill=(24, 30, 39))
    font = images.font("Barlow-Medium", 19)
    d.text((22, TITLE / 2), title, font=font, fill=(214, 224, 236), anchor="lm")
    if address:
        left = 22 + d.textlength(title, font=font) + 36
        pill = [left, 8, min(left + 560, w - 200), TITLE - 8]
        if pill[2] > pill[0] + 80:
            d.rounded_rectangle(pill, 14, fill=(13, 17, 23), outline=(44, 54, 68))
            d.text((pill[0] + 16, TITLE / 2), address, font=images.font("Barlow-Regular", 17),
                   fill=(150, 166, 186), anchor="lm")  # fmt: skip
    for n, mark in enumerate(("min", "max", "close")):
        cx, cy = w - 30 - n * 46, TITLE / 2
        colour = (170, 182, 198)
        if mark == "close":
            d.line([(cx - 7, cy - 7), (cx + 7, cy + 7)], fill=colour, width=2)
            d.line([(cx - 7, cy + 7), (cx + 7, cy - 7)], fill=colour, width=2)
        elif mark == "max":
            d.rectangle([cx - 7, cy - 7, cx + 7, cy + 7], outline=colour, width=2)
        else:
            d.line([(cx - 7, cy), (cx + 7, cy)], fill=colour, width=2)
    body = content.resize((w, max(1, h - TITLE)), Image.LANCZOS)
    img.paste(body, (0, TITLE))
    d.rectangle([0, 0, w - 1, h - 1], outline=(52, 64, 80))
    return img


CURSOR = [(0, 0), (0, 19), (4.3, 15), (7.2, 21.6), (10, 20.4), (7.1, 13.9), (13, 13.6)]


def pointer(scale: float = 1.7) -> Image.Image:
    """The mouse pointer: a white arrow with a dark edge, drawn large and made small."""
    big = 4
    size = (round(18 * scale * big), round(26 * scale * big))
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    points = [((x + 1.5) * scale * big, (y + 1.5) * scale * big) for x, y in CURSOR]
    d.polygon(points, fill=(255, 255, 255, 255), outline=(10, 17, 25, 255), width=round(1.5 * big))
    small = img.resize((size[0] // big, size[1] // big), Image.LANCZOS)
    shadow = Image.new("RGBA", small.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 120), (0, 0), small.split()[3])
    out = Image.new("RGBA", (small.width + 4, small.height + 4), (0, 0, 0, 0))
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(1.5)), (2, 3))
    out.alpha_composite(small, (0, 0))
    return out


# -- the panel as a monitor of its own -----------------------------------------------------------

# The browser window: where it is on the main monitor, and the moments of the scene (seconds)
BROWSER = Box(1010, 360, 1440, 860)
APP = Box(90, 120, 1400, 880)
ON_PANEL = Box(PANEL_AT[0], PANEL_AT[1], PANEL[0], PANEL[1])
REST = (1880.0, 1260.0)  # where the pointer rests, on the monitor


SCENE_S = 9.0
GRAB = 520  # where the pointer holds a window's title bar, from its left edge


def _along(a: tuple[float, float], b: tuple[float, float], x: float) -> tuple[float, float]:
    e = ease(x)
    return (lerp(a[0], b[0], e), lerp(a[1], b[1], e))


def moment(t: float, scroll_to: float) -> tuple[Box, float, tuple[float, float], bool]:
    """Where the browser window is at ``t`` (a box of the desktop), how far its page is
    scrolled, where the pointer is and whether its button is down. The scene: the pointer takes
    the window by its title bar (0.6 s) and drags it down onto the panel (1.35 s), lets go, and
    the window is made as large as the panel (2.6 s); its page scrolls to the features (3.1 s)
    while the pointer goes back; then the pointer drags it back (6.5 s), where it began."""
    grab = (BROWSER.x + GRAB, BROWSER.y + TITLE / 2)
    dropped = Box(ON_PANEL.x + (ON_PANEL.w - BROWSER.w) / 2, ON_PANEL.y + 110, BROWSER.w,
                  BROWSER.h)  # fmt: skip
    held = (dropped.x + GRAB, dropped.y + TITLE / 2)
    top = (ON_PANEL.x + 900, ON_PANEL.y + TITLE / 2)
    restored = Box(top[0] - GRAB, top[1] - TITLE / 2, BROWSER.w, BROWSER.h)
    if t < 0.6:
        return BROWSER, 0.0, REST, False
    if t < 1.2:
        return BROWSER, 0.0, _along(REST, grab, (t - 0.6) / 0.6), False
    if t < 2.55:
        box = BROWSER.towards(dropped, ease((t - 1.35) / 1.2))
        return box, 0.0, (box.x + GRAB, box.y + TITLE / 2), True
    if t < 6.5:
        box = dropped.towards(ON_PANEL, ease((t - 2.6) / 0.35))
        scroll = scroll_to * ease((t - 3.1) / 1.3)
        if t < 2.9:
            cursor = held
        elif t < 3.9:
            cursor = _along(held, REST, (t - 2.9) / 1.0)
        elif t < 5.8:
            cursor = REST
        else:
            cursor = _along(REST, top, (t - 5.8) / 0.7)
        return box, scroll, cursor, False
    if t < 7.85:
        box = ON_PANEL if t < 6.65 else restored.towards(BROWSER, ease((t - 6.65) / 1.2))
        scroll = scroll_to * (1 - ease((t - 6.65) / 0.7))
        cursor = top if t < 6.65 else (box.x + GRAB, box.y + TITLE / 2)
        return box, scroll, cursor, True
    return BROWSER, 0.0, _along(grab, REST, (t - 7.85) / 0.8), False


def screen_monitor(settings: Settings, sink: FrameSink) -> int:
    """A window of the desktop dragged onto the panel, which is a monitor of its own below the
    main one: it is there at once, as on any monitor, is made as large as the panel, and goes
    back again."""
    scale = max(0.5, settings.scale)
    with browser() as chromium:
        # the page at 150 %, as large as on a laptop: it fills the panel's width
        site, features = website(chromium, PANEL[0] * 2 // 3, (PANEL[1] - TITLE) * 2 // 3, 1.5)
        app = screen_page(chromium, (1120, 700), 1.5)
    desk = Desk()
    wall_m = wallpaper(desk.screen_m, (40, 120, 160))
    wall_p = wallpaper(desk.screen_p, (70, 60, 170))
    arrow = pointer()
    browser_title, address = "Libre Panel", "syntensa.github.io/libre-panel"
    app_window = window(app, (APP.w * K, APP.h * K), "Libre Panel")
    # (the head of the features a little below the window's top, their first tiles under it)
    scroll_to = min(max(0, site.height - (PANEL[1] - TITLE)), max(0, features - 60))
    glass = Image.new("L", desk.screen_p, 0)  # (the panel's screen has round corners)
    ImageDraw.Draw(glass).rounded_rectangle([0, 0, desk.screen_p[0] - 1, desk.screen_p[1] - 1],
                                            4, fill=255)  # fmt: skip
    total = round(SCENE_S * FPS)
    poster = round(4.6 * FPS)
    for n in range(total):
        box, scroll, cursor, pressed = moment(n / FPS, scroll_to)
        frame = desk.base.copy()
        on_m, on_p = wall_m.copy(), wall_p.copy()
        # the app on the main monitor, behind the browser
        on_m.paste(app_window, (round(APP.x * K), round(APP.y * K)))
        page = site.crop((0, round(scroll), site.width, round(scroll) + PANEL[1] - TITLE))
        drawn = window(page, (box.w * K, box.h * K), browser_title, address)
        for screen, origin, room in ((on_m, (0, 0), MONITOR), (on_p, PANEL_AT, PANEL)):
            x0, y0 = (box.x - origin[0]) * K, (box.y - origin[1]) * K
            if x0 + drawn.width <= 0 or y0 + drawn.height <= 0:
                continue
            if x0 >= room[0] * K or y0 >= room[1] * K:
                continue
            shadow = Image.new("L", screen.size, 0)
            ImageDraw.Draw(shadow).rectangle([x0 + 4, y0 + 10, x0 + drawn.width + 4,
                                              y0 + drawn.height + 10], fill=150)  # fmt: skip
            screen.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(14)))
            screen.paste(drawn, (round(x0), round(y0)))
        frame.paste(on_m, (desk.mx, desk.my))
        frame.paste(on_p, (desk.px, desk.py), glass)
        cx, cy = desk.to_picture(*cursor)
        if pressed:
            ring = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse([4, 4, 35, 35], outline=(19, 229, 215, 255), width=3,
                                         fill=(19, 229, 215, 60))  # fmt: skip
            frame.alpha_composite(ring, (round(cx - 20), round(cy - 20)))
        frame.alpha_composite(arrow, (round(cx), round(cy)))
        picture = frame.convert("RGB")
        if scale != 1.0:
            picture = picture.resize(
                (round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS
            )
        sink.add(picture)
    return poster


# -- the top of the README: the headline over Lumen in video mode --------------------------------

# The headline of the website (site/index.html), in each language of the README: the words
# before the lit ones, the lit ones, the end; the line under it.
HEADLINES = {
    "en": ("Your PC's vitals as ", "moving light", ".",
           "Free and open source, for the small USB screens in your PC's case."),
    "de": ("Die Werte deines PCs als ", "bewegtes Licht", ".",
           "Frei und quelloffen, für die kleinen USB-Displays im Gehäuse deines PCs."),
}  # fmt: skip
TEXT, MUTED, LIT = images.TX_1, (150, 168, 192), images.ACCENT


def _bare_lumen(settings: Settings) -> tuple[Any, int]:
    """Lumen in video mode (the README's video-mode clip, without its frame): its frames, and
    the poster's number."""
    folder = Path(tempfile.mkdtemp(prefix="readme-scene-lumen-"))
    clip = media_panel.CLIPS["video-mode"]
    recorded = media_panel.record("video-mode", clip, replace(settings, bare=True), folder)
    return recorded.frames, recorded.poster


def _headline(width: int, language: str, scale: float) -> Image.Image:
    """The mark and name, the headline with its lit words, and the line under it, centred on a
    transparent picture ``width`` wide."""
    before, lit, after, line = HEADLINES[language]
    u = scale
    height = round(400 * u)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    mark = Image.open(ROOT / "src/libre_panel/assets/libre-panel.png").convert("RGBA")
    mark = mark.resize((round(64 * u), round(64 * u)), Image.LANCZOS)
    name_font = images.font("Barlow-SemiBold", round(44 * u))
    name_w = d.textlength("Libre Panel", font=name_font)
    left = (width - mark.width - 18 * u - name_w) / 2
    img.alpha_composite(mark, (round(left), round(40 * u)))
    d.text((left + mark.width + 18 * u, 40 * u + mark.height / 2), "Libre Panel", font=name_font,
           fill=(*TEXT, 255), anchor="lm")  # fmt: skip
    size = 104 * u
    while True:
        big = images.font("Barlow-Bold", round(size))
        widths = [d.textlength(part, font=big) for part in (before, lit, after)]
        if sum(widths) <= width * 0.92 or size < 40 * u:
            break
        size *= 0.95
    x = (width - sum(widths)) / 2
    y = 150 * u
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((x + widths[0], y), lit, font=big, fill=(*LIT, 200))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * u)))
    for part, colour, dx in ((before, TEXT, 0.0), (lit, LIT, widths[0]),
                             (after, TEXT, widths[0] + widths[1])):  # fmt: skip
        d.text((x + dx, y), part, font=big, fill=(*colour, 255))
    small = images.font("Barlow-Regular", round(36 * u))
    d.text((width / 2, y + size * 1.32), line, font=small, fill=(*MUTED, 255), anchor="ma")
    return img


def hero(language: str) -> Callable[[Settings, FrameSink], int]:
    """The headline in ``language`` over Lumen on its 9.2" bar in video mode: one picture, so
    that the top of the README is one thing, as the website's is."""

    def draw(settings: Settings, sink: FrameSink) -> int:
        frames, poster = _bare_lumen(settings)
        try:
            screen = frames.size
            border = max(4, round(0.0375 * screen[1]))
            u = screen[1] / 480
            margin = images.shadow_margin(border)
            dark = images.device(Image.new("RGB", screen, (0, 0, 0)), border)
            white = images.device(Image.new("RGB", screen, (255, 255, 255)), border)
            glass = (margin + border, margin + border, margin + border + screen[0],
                     margin + border + screen[1])  # fmt: skip
            black_glass, white_glass = (d.crop(glass).getchannel("R") for d in (dark, white))
            mask = ImageChops.subtract(white_glass, black_glass).point(lambda v: 255 if v else 0)
            sheen = Image.composite(black_glass, Image.new("L", screen, 0), mask)
            lit_white = Image.new("RGB", screen, (255, 255, 255))
            width = screen[0] + 2 * border + round(2 * 3.2 * border)
            head = _headline(width, language, u)
            top = head.height
            height = top + screen[1] + 2 * border + round(2.4 * border)
            canvas = images.background(width, height, glow=(0.5, 1.0)).convert("RGBA")
            canvas.alpha_composite(head)
            at = ((width - screen[0]) // 2 - border - margin, top + round(0.6 * border) - margin)
            canvas.alpha_composite(dark, at)
            base = canvas.convert("RGB")
            origin = (at[0] + margin + border, at[1] + margin + border)
            step = max(1, round(frames.fps / FPS))
            for number in range(0, frames.count, step):
                picture = frames.image(number).convert("RGB")
                out = base.copy()
                out.paste(Image.composite(lit_white, picture, sheen), origin, mask)
                sink.add(out)
            return min(sink.count - 1, poster // step)
        finally:
            shutil.rmtree(frames.folder, ignore_errors=True)

    return draw


CLIPS: dict[str, SceneClip] = {
    "hero": SceneClip(
        "the top of the README: the headline over Lumen in video mode on its 9.2\" bar",
        media_panel.CLIPS["video-mode"].seconds, hero("en"),
    ),
    "hero-de": SceneClip(
        "the top of the German README: the headline in German over Lumen in video mode",
        media_panel.CLIPS["video-mode"].seconds, hero("de"),
    ),
    "screen-monitor": SceneClip(
        "the panel as a monitor of its own: a window dragged onto it, made as large, and back",
        SCENE_S, screen_monitor,
    ),
}  # fmt: skip

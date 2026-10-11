"""Turns a theme plus a sensor snapshot into a frame.

The theme editor uses this same renderer for its preview, so what the editor
shows is exactly what the panel shows.

Every widget is drawn into its own layer (shapes at 3x for smooth edges),
effects (shadow, glow, opacity) are applied to that layer and the result is
composited. Layers that did not change are reused from a cache, so a frame
where only a few values moved costs little.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import queue
import re
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from libre_panel import clock, i18n, timezones
from libre_panel.fonts import DEFAULT_FONT, builtin_font_path
from libre_panel.fonts.fallback import truetype
from libre_panel.icons import draw_icon, weather_icon_name
from libre_panel.render import cover, fan, lists
from libre_panel.render import finish as finishes
from libre_panel.render.countdown import countdown_text
from libre_panel.render.dust import Dust
from libre_panel.render.formatting import FormatError, auto_format, safe_format
from libre_panel.render.motion import MAX_POINTS, RATE, History, Motion, Spring, grid
from libre_panel.render.motion import number as motion_number
from libre_panel.sensors.base import Snapshot, want
from libre_panel.theme.model import COVER_COLOR, Theme, ThemeError, resolve_asset
from libre_panel.theme.modules import SLOT_KINDS, slot_items
from libre_panel.theme.modules import expand as expand_modules

log = logging.getLogger(__name__)

# Sensor texts shown in the user's language on the panel.
TRANSLATED_READINGS = {"weather.description", "battery.state", "moon.name", "media.state"}

# Shapes are drawn at this scale and downsampled for smooth edges.
SUPERSAMPLE = 3
_ANCHORS = {"left": "la", "center": "ma", "right": "ra"}
_DIGITS = "0123456789"

Box = list[int]  # [x, y, w, h]
RGBA = tuple[int, int, int, int]


@dataclass
class Piece:
    """A rendered widget: an RGBA layer placed at (x, y)."""

    layer: Image.Image
    x: int
    y: int
    box: Box | None = None  # hit box for the editor; defaults to the layer bounds
    backdrop: Image.Image | None = None  # "L" mask: blur the canvas beneath first
    backdrop_radius: int = 0
    add: bool = False  # light: added onto what lies beneath (the panel light's gloss)
    light: Piece | None = None  # light added onto the panel after the layer: a glow
    # what it can change on the canvas, worked out once (Renderer._extent): a frame
    # asks that of every piece for every region it composes
    span: tuple[int, int, int, int] | None = field(default=None, init=False, compare=False)


class _Builder:
    """One daemon thread that builds pieces for the renderer.

    One thread, so fonts (FreeType faces are not thread-safe) are never used
    twice at once; a daemon, so it can never keep the program alive.
    """

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Future, Callable[[], Any]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="render-builder", daemon=True)
        self._thread.start()

    def submit(self, job: Callable[[], Any]) -> Future:
        future: Future = Future()
        self._jobs.put((future, job))
        return future

    def _work(self) -> None:
        while (item := self._jobs.get()) is not None:
            future, job = item
            try:
                future.set_result(job())
            except Exception as exc:
                future.set_exception(exc)

    def close(self) -> None:
        self._jobs.put(None)


def changed_region(
    previous: Image.Image | None, current: Image.Image
) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of pixels that differ, or None if identical."""
    if previous is None or previous.size != current.size or previous.mode != current.mode:
        return (0, 0, current.width, current.height)
    return ImageChops.difference(previous, current).getbbox()


def _drawing(finish: Any, renderer: Renderer) -> Callable[..., Any]:
    """``finish.draw`` as the renderer calls it for a widget, without the reference to the
    renderer that a ``partial`` would keep: the renderer holds it, and a cycle is freed only
    when the collector runs, which the main loop's guards (timing.Guards) put off for hours."""
    ref = weakref.ref(renderer)

    def draw(widget: dict[str, Any], snapshot: Snapshot, now: float) -> Any:
        return finish.draw(ref(), widget, snapshot, now)

    return draw


def _weakly(method: Callable[..., Any]) -> Callable[..., Any]:
    """A bound method that does not keep its object alive (for what the renderer hands out)."""
    ref = weakref.WeakMethod(method)

    def call(*args: Any) -> Any:
        found = ref()
        if found is None:
            raise RuntimeError("the renderer is gone")
        return found(*args)

    return call


def _number(value: Any) -> float | None:
    """A reading's value as a number; None for none, a text, and NaN or infinity (which are
    no reading: one would poison the value that glides)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return number if math.isfinite(number) else None


def _undrawable(value: Any) -> bool:
    """Whether ``value`` is a number that cannot be drawn: NaN, infinity, or an integer too large
    for a float (over 2**1023: a JSON payload of 400 digits)."""
    if isinstance(value, float):
        return not math.isfinite(value)
    return isinstance(value, int) and value.bit_length() > 1023


def _finite(snapshot: Snapshot) -> Snapshot:
    """``snapshot`` with every reading that is NaN, infinite or too large for a float taken for
    none: a source can send one (a payload "nan"), and no widget, card or list must draw it or
    glide to it."""
    bad = []
    for key, reading in snapshot.readings.items():  # (every frame: no call per reading)
        value = reading.value
        if isinstance(value, float):
            if not math.isfinite(value):
                bad.append(key)
        elif isinstance(value, int) and value.bit_length() > 1023:
            bad.append(key)
    if not bad:
        return snapshot
    readings = {**snapshot.readings}
    for key in bad:
        readings[key] = replace(readings[key], value=None)
    return replace(snapshot, readings=readings)


def _gaps(values: list[float]) -> list[float | None]:
    """A history with a gap (None) where a value is NaN, infinite or too large for a float,
    else the history itself."""
    try:
        if math.isfinite(sum(values)):  # (at C speed: one NaN or infinity makes the sum one)
            return values
    except (TypeError, OverflowError):  # a gap in it, or an integer beyond a float
        pass
    return [
        value if isinstance(value, (int, float)) and not _undrawable(value) else None
        for value in values
    ]


def _fraction(value: float | None, lo: float, hi: float, scale: str = "linear") -> float:
    """Position of ``value`` between ``lo`` and ``hi`` (0..1).

    ``sqrt`` and ``log`` keep small values visible on huge ranges such as
    network rates (SPUR II used a square-root scale for exactly that).
    """
    if value is None or hi == lo:
        return 0.0
    linear = min(1.0, max(0.0, (value - lo) / (hi - lo)))
    if scale == "sqrt":
        return math.sqrt(linear)
    if scale == "log":
        span = abs(hi - lo)
        return math.log1p(linear * span) / math.log1p(span)
    return linear


def _rule_color(widget: dict[str, Any], value: float | None) -> str | None:
    """The color of the highest threshold the value exceeds, or None."""
    color = None
    if value is None:
        return None
    for rule in sorted(widget.get("color_rules", []), key=lambda r: r["above"]):
        if value > rule["above"]:
            color = rule["color"]
    return color


def _steps(frac: float, length: float) -> float:
    """``frac`` of ``length`` pixels in steps of the supersampled grid: a finer
    change draws the same pixels, so a gliding bar is drawn again only when
    it moves visibly."""
    steps = max(1, round(length * SUPERSAMPLE))
    return round(frac * steps) / steps


def _smoothstep(u: float) -> float:
    return u * u * (3 - 2 * u)


def _lerp(a: RGBA, b: RGBA, t: float) -> RGBA:
    t = min(1.0, max(0.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))  # type: ignore[return-value]


def _blur(img: Image.Image, radius: float, exact: bool = False) -> Image.Image:
    """Colour-true Gaussian blur of an RGBA image (premultiplied alpha). ``exact``
    blurs at full size: the same pixels wherever a part is cut from (graph strips)."""
    if radius <= 0:
        return img
    pre = img.convert("RGBa")
    if max(img.size) > 320 and radius >= 4 and not exact:  # half size: 4x cheaper, same look
        small = pre.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.BILINEAR)
        pre = small.filter(ImageFilter.GaussianBlur(radius / 2)).resize(img.size, Image.BILINEAR)
    else:
        pre = pre.filter(ImageFilter.GaussianBlur(radius))
    return pre.convert("RGBA")


def _scale_alpha(img: Image.Image, factor: float) -> Image.Image:
    if factor >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda a: int(a * factor)))
    return out


def _dimmed(light: Piece | None, factor: float) -> Piece | None:
    """A light piece ``factor`` as bright (light is added: all its channels scale)."""
    if light is None or factor >= 0.999:
        return light
    table = [int(v * factor) for v in range(256)] * 4
    return replace(light, layer=light.layer.point(table))


@lru_cache(maxsize=128)
def _gradient(size: tuple[int, int], a: RGBA, b: RGBA, horizontal: bool) -> Image.Image:
    """Linear gradient from ``a`` to ``b`` (cached: callers must not modify it)."""
    steps = size[0] if horizontal else size[1]
    strip = Image.new("RGBA", (steps, 1) if horizontal else (1, steps))
    strip.putdata([_lerp(a, b, i / max(1, steps - 1)) for i in range(steps)])
    return strip.resize(size, Image.NEAREST)


@lru_cache(maxsize=64)
def _gradient_down(size: tuple[int, int], a: RGBA, b: RGBA, horizontal: bool) -> Image.Image:
    """The colours a gradient three times the size has once reduced (cached:
    callers must not modify it)."""
    steps = size[0] if horizontal else size[1]
    last = max(1, steps * SUPERSAMPLE - 1)
    colours = []
    for i in range(steps):
        parts = [_lerp(a, b, (SUPERSAMPLE * i + k) / last) for k in range(SUPERSAMPLE)]
        colours.append(tuple(round(sum(p[c] for p in parts) / SUPERSAMPLE) for c in range(4)))
    strip = Image.new("RGBA", (steps, 1) if horizontal else (1, steps))
    strip.putdata(colours)
    return strip.resize(size, Image.NEAREST)


@lru_cache(maxsize=64)
def _card_corners(r: int, outline: int) -> tuple[Image.Image, Image.Image | None, int]:
    """How much of each pixel a card with corners of radius ``r`` (and an
    outline ``outline`` px wide) covers near its corners: a small card drawn
    three times larger and reduced, ``2 * c + 2`` px square, whose ``c`` px
    corners are those of any larger card (Pillow draws the corners in boxes at
    the card's corners, the rest as rectangles)."""
    s, c = SUPERSAMPLE, r + 2
    m = 2 * c + 2
    box = [0, 0, m * s - 1, m * s - 1]
    shape = Image.new("L", (m * s, m * s), 0)
    ImageDraw.Draw(shape).rounded_rectangle(box, radius=r * s, fill=255)
    ring = None
    if outline:
        ring = Image.new("L", (m * s, m * s), 0)
        ImageDraw.Draw(ring).rounded_rectangle(box, radius=r * s, outline=255, width=outline * s)
        ring = ring.reduce(s)
    return shape.reduce(s), ring, c


def _fitted(image: Image.Image, w: int, h: int, fit: str, align: str = "center") -> Image.Image:
    """``image`` at w x h: stretched, inside with room left (contain, at the left, in the middle
    or at the right: ``align``) or filling it (cover)."""
    if fit == "cover":
        return ImageOps.fit(image, (w, h), Image.Resampling.LANCZOS)
    if fit == "contain":
        inner = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        left = {"left": 0, "right": w - inner.width}.get(align, (w - inner.width) // 2)
        out.paste(inner, (left, (h - inner.height) // 2))
        return out
    return image.resize((w, h), Image.Resampling.LANCZOS)


def _rounded(image: Image.Image, radius: int) -> Image.Image:
    """``image`` with round corners (drawn larger, then reduced: smooth edges)."""
    w, h = image.size
    mask = Image.new("L", (w * SUPERSAMPLE, h * SUPERSAMPLE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w * SUPERSAMPLE - 1, h * SUPERSAMPLE - 1], radius=radius * SUPERSAMPLE, fill=255
    )
    mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    out = image.copy()
    out.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    return out


def _catmull_rom(points: list[tuple[float, float]], samples: int = 6) -> list[tuple[float, float]]:
    """Smooth curve through all points (centripetal-free, uniform Catmull-Rom)."""
    if len(points) < 3:
        return points
    out = [points[0]]
    padded = [points[0], *points, points[-1]]
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1], padded[i], padded[i + 1], padded[i + 2]
        for step in range(1, samples + 1):
            t = step / samples
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(
                    0.5
                    * (
                        2 * p1[k]
                        + (-p0[k] + p2[k]) * t
                        + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
                    )
                    for k in (0, 1)
                )
            )
    return out


# "media.playing=1", "sun.up!=0", "cpu.temp>80", "game.limiter=gpu": a ``needs`` that compares a
# reading with a number, or (``=``, ``!=``) with a word
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_COMPARE = re.compile(
    r"^(?P<key>[^=<>!]+?)\s*(?P<op>==|=|!=|>=|<=|>|<)\s*"
    r"(?P<value>-?\d+(?:\.\d+)?|[A-Za-z_][\w .-]*?)$"
)
_OPERATORS = {
    "=": lambda a, b: a == b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}  # fmt: skip


def need_key(need: str) -> str:
    """The sensor a condition of ``needs`` reads: ``"!gpu.load"`` and ``"media.playing=1"``
    read ``gpu.load`` and ``media.playing``; ``"@media.cover"`` (a picture) ``media.cover``."""
    need = need.strip().lstrip("!").strip()
    found = _COMPARE.match(need)
    return (found["key"].strip() if found else need).lstrip("@")


def need_holds(need: str, snapshot: Snapshot) -> bool:
    """Whether one condition of ``needs`` holds: a reading that has a value (``"gpu.load"``),
    none (``"!gpu.load"``; a state that is ``null`` has none), a picture that a source has
    (``"@media.app_icon"``), or compares so (``"media.playing=1"``: ``=``, ``!=``, ``>``, ``>=``,
    ``<``, ``<=`` with a number; a reading without a number never does; ``"game.limiter=gpu"``:
    ``=`` and ``!=`` with a word, a text in any case; a ``!`` in front says the opposite)."""
    need = need.strip()
    body = need.lstrip("!").strip()
    found = _COMPARE.match(body)
    if body.startswith("@"):
        holds = snapshot.images.get(body[1:]) is not None
    elif found is None:  # (a state a service has taken back is the text "null": none)
        value = snapshot.value(body)
        holds = value is not None and value != "null"
    else:
        value = snapshot.value(found["key"].strip())
        if _NUMBER.fullmatch(found["value"]):
            number = _number(float(value) if isinstance(value, bool) else value)  # (on is 1)
            holds = number is not None and _OPERATORS[found["op"]](number, float(found["value"]))
        elif isinstance(value, str) and found["op"] in ("=", "==", "!="):  # a word
            same = value.strip().casefold() == found["value"].strip().casefold()
            holds = same if found["op"] != "!=" else not same
        else:  # a number is no word, and words have no order
            holds = False
    return holds != need.startswith("!")


def _reads(widgets: list[dict[str, Any]]) -> set[str]:
    """The sensor keys (and patterns) the widgets read."""
    keys: set[str] = set()
    for widget in widgets:
        if widget.get("sensor"):
            keys.add(widget["sensor"])
        if widget.get("mark"):
            keys.add(widget["mark"])
        for need in (widget.get("needs") or "").split(","):
            need = need_key(need)
            if need:
                keys.add(need)
        if widget["type"] == "weather":
            keys.add(f"weather.{widget['field']}")
        elif widget["type"] == "list":
            keys |= lists.read_keys(widget["items"])
        elif widget["type"] == "image":
            keys |= _live_pictures(widget)
    return keys


def _live_pictures(widget: dict[str, Any]) -> set[str]:
    """The keys of the pictures an image widget takes from a source, not from the theme's
    folder: ``"@media.cover"`` as its ``src`` or on a line of its ``slides``."""
    pictures = [widget.get("src") or "", *(widget.get("slides") or "").splitlines()]
    return {name[1:] for name in map(str.strip, pictures) if name.startswith("@") and len(name) > 1}


class Renderer:
    def __init__(
        self,
        theme: Theme,
        animate: bool = False,
        preview: bool = False,
        motion: Motion | None = None,
    ) -> None:
        self.theme = theme
        self.animate = animate
        self.preview = preview  # an editor preview or a picture, not the panel
        # Gliding values, fading colours and graph histories: the main loop's,
        # kept from theme to theme, or this renderer's own (a preview).
        self.motion = motion if motion is not None else Motion()
        self.shown: Image.Image | None = None  # the frame the panel shows (set by the loop)
        self.toast: tuple[Any, float] | None = None  # the toast on show, its age (the loop)
        self.snapshot: Snapshot | None = None  # the readings it drew last (context.image)
        self.warnings: list[str] = []
        self.moving = False  # True while an animation has not settled yet
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._digit_width: dict[int, float] = {}
        self._images: OrderedDict[tuple[str, int, int, str, str], Image.Image | None] = (
            OrderedDict()
        )
        self._slide_lists: dict[str, tuple[float, list[str]]] = {}  # widget id -> pictures
        self._gauge_parts: dict[tuple[Any, ...], Image.Image] = {}  # static rings, colour fields
        # Video mode (the main loop sets these): every frame goes out, so graphs
        # scroll between readings and values glide until the next one.
        self.continuous = False
        self.sample: tuple[float, float] = (0.0, 1.0)  # (when, seconds to the next)
        self._strips: dict[str, _Strips] = {}  # graph id -> its strips (video mode)
        self._cache: dict[str, tuple[Any, Piece]] = {}
        self._texts: OrderedDict[tuple[str, str, str], Piece] = OrderedDict()
        self._glyphs: OrderedDict[tuple[Any, ...], Image.Image] = OrderedDict()
        self._glyph_lock = threading.Lock()
        # Modules become plain widgets laid out for their cells; the editor
        # gets one box per module.
        self.widgets, self.module_boxes = expand_modules(theme)
        # Finishes other than flat, and the panel's look (render.finish,
        # render.look); a theme with neither pays nothing for them.
        self.light_edges: list[Any] = []  # this frame's, for the panel light (add_light_edge)
        self.edge_owners: dict[int, str] = {}  # edge id -> the piece that drew it this frame
        self._drawing = ""  # the widget being drawn ("\0screen": the screen)
        self.place: tuple[float, float] | None = None  # where the sun is seen from (the loop)
        self._finishes = finishes.attach(self)  # widget id -> finish
        self.look: Any = None
        if theme.look:
            from libre_panel.render.look import Look

            self.look = Look(self)
        self._drawers = {wid: _drawing(f, self) for wid, f in self._finishes.items()
                         if finishes.draws(f)}  # fmt: skip
        self._modules = {w["id"]: w for w in theme.widgets if w["type"] == "module"}
        self._slots = self._make_slots(theme)  # modules that show one card of several
        self._slots_warm = False  # whether their cards have been drawn once
        self._keys = {w["id"]: json.dumps(w, sort_keys=True) for w in self.widgets}
        if not preview:  # costly sensors work only while a panel shows them
            want(self, _reads(self.widgets))
        self._background = self._load_background()
        # Frames start from an opaque colour: pieces go on with a paste in place
        self._opaque = (self.color(theme.background_color) or (0, 0, 0, 0))[3] == 255
        # Incremental compositing: the last frame and the pieces it was made of.
        # Only regions whose pieces changed are composed again, so a mostly
        # static screen costs little at 50 fps. The result is pixel-identical.
        self.incremental = True
        self._last: tuple[list[tuple[str, Piece]], Image.Image] | None = None
        # Per region, what lies under the first piece that changes (a graph that
        # moves every frame over a card): the same pieces need not be laid again.
        self._underlays: dict[tuple[int, int, int, int], tuple[list[Piece], Image.Image]] = {}
        # Video panels take a frame every 20 ms. With background builds a
        # changed piece is built in a helper thread while frames keep going out
        # with the previous piece; the change shows a frame or two later.
        self.background_builds = False
        self._builder: _Builder | None = None
        self._pending: dict[str, tuple[Any, Future]] = {}
        # Plugins (docs/PLUGINS.md): a screen draws the frame under the widgets,
        # plugin widget types draw themselves.
        self.fps = 0  # the panel's frame rate, for screens (set by the main loop)
        self._breathe: Callable[[], None] | None = None  # between widgets (warming up)
        self._plugin_types: dict[str, Any] = {}
        self._screen_last: tuple[Any, Piece] | None = None
        self._dust: Dust | None = None  # the theme's look.dust, once it is drawn (render/dust.py)
        self._dust_submit: Callable[..., Any] | None = None  # its way to the helper thread
        self._dots: dict[tuple[Any, ...], tuple[Image.Image, int]] = {}  # end dots, by look
        self._rotors: dict[str, fan.Rotor] = {}  # fan widget id -> how it turns (render/fan.py)
        self._fans: OrderedDict[tuple[Any, ...], Piece] = OrderedDict()  # fan pieces, by look
        self._downloads: dict[str, tuple[Any, Any]] = {}  # download widget id -> what it shows
        self.screen = self._make_screen()

    def _make_slots(self, theme: Theme) -> dict[str, Any]:
        """The slot of every module that shows one card of several (render/slot.py)."""
        slots: dict[str, Any] = {}
        for module in self._modules.values():
            if module["module"] in SLOT_KINDS and module["id"] in self.module_boxes:
                from libre_panel.render.slot import Slot

                items = slot_items(module)
                slots[module["id"]] = Slot(self, module, items, self.module_boxes[module["id"]])
        return slots

    # -- resources ---------------------------------------------------------

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            log.warning(message)

    def color(self, value: str | None, alpha_scale: float = 1.0) -> RGBA | None:
        if value is None:
            return None
        if value == COVER_COLOR:  # the colour the cover on show carries, else the accent
            carried = (
                cover.dominant(self.snapshot.images.get("media.cover")) if self.snapshot else None
            )
            if carried is not None:
                return (*carried, int(255 * alpha_scale))
            value = self.theme.palette.get("accent", "#22d3ee")
        elif value.startswith("@"):
            value = self.theme.palette.get(value[1:], "#ff00ff")
        rgb = ImageColor.getrgb(value)
        alpha = rgb[3] if len(rgb) == 4 else 255
        return (rgb[0], rgb[1], rgb[2], int(alpha * alpha_scale))

    def font(self, ref: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        ref = ref or self.theme.font or DEFAULT_FONT
        size = max(4, min(int(size), 512))
        key = (ref, size)
        if key not in self._fonts:
            font = None
            try:
                builtin = builtin_font_path(ref)
                if builtin is not None:
                    font = truetype(str(builtin), size)
                elif self.theme.root is not None:
                    font = truetype(str(resolve_asset(self.theme.root, ref)), size)
            except (OSError, ThemeError) as exc:
                self._warn(f"font {ref!r} could not be loaded ({exc}); using the default font")
            if font is None and ref != DEFAULT_FONT:
                font = truetype(str(builtin_font_path(DEFAULT_FONT)), size)
            self._fonts[key] = font or ImageFont.load_default(size)
        return self._fonts[key]

    IMAGE_BYTES = 64 << 20  # pictures kept fitted: a slideshow of a folder reads the rest again

    def _asset_image(
        self, rel: str, w: int, h: int, fit: str = "stretch", align: str = "center"
    ) -> Image.Image | None:
        key = (rel, w, h, fit, align)
        if key in self._images:
            self._images.move_to_end(key)
            return self._images[key]
        image = None
        if rel and self.theme.root is not None:
            try:
                with Image.open(resolve_asset(self.theme.root, rel)) as src:
                    image = ImageOps.exif_transpose(src).convert("RGBA")
                if w > 0 and h > 0:
                    image = _fitted(image, w, h, fit, align)
            except (OSError, ThemeError) as exc:
                self._warn(f"image {rel!r} could not be loaded ({exc})")
        self._images[key] = image
        self._forget_images()
        return image

    def _forget_images(self) -> None:
        """Let the oldest pictures go beyond ``IMAGE_BYTES`` (the newest stays): a folder of
        photos would else be kept whole, each as fitted, for as long as the theme shows."""

        def size(image: Image.Image | None) -> int:  # (a picture that is not there counts too)
            return 4096 if image is None else max(4096, image.width * image.height * 4)

        total = sum(size(image) for image in self._images.values())
        while total > self.IMAGE_BYTES and len(self._images) > 1:
            total -= size(self._images.popitem(last=False)[1])

    def _load_background(self) -> Image.Image | None:
        rel = self.theme.background_image
        if not rel:
            return None
        image = self._asset_image(rel, 0, 0)
        if image is None:
            return None
        return ImageOps.fit(image, (self.theme.width, self.theme.height))

    # -- animation ---------------------------------------------------------

    def new_sample(self, at: float, interval: float) -> None:
        """The main loop took fresh readings at ``at``; the next follow after ``interval`` s."""
        self.sample = (at, max(0.001, interval))

    def progress(self, now: float, delay: float = 0.0) -> float:
        """How far ``now`` is from the last reading to the next, 0 to 1."""
        at, interval = self.sample
        return min(1.0, max(0.0, (now - at - delay) / interval))

    def _glide_s(self) -> float:
        """How long a value takes to a new reading (theme animation.smoothing_ms;
        0: it jumps). In video mode the glide lasts until the next reading, so
        values never stand still between readings."""
        if not self.animate or self.theme.smoothing_ms <= 0:
            return 0.0
        glide = self.theme.smoothing_ms / 1000
        return max(glide, self.sample[1]) if self.continuous else glide

    def _lead(self) -> float:
        """The time between two planned frames (0: not known)."""
        return 1 / self.fps if self.fps > 0 else 0.0

    def _eased(self, widget: dict[str, Any], target: float | None, now: float) -> float | None:
        """Glide towards new values instead of jumping (widgets with ``smooth``).

        A critically damped spring per sensor (:class:`~libre_panel.render.motion.Motion`):
        it starts gently, a new reading changes its course without a kink, and
        all widgets of a sensor glide together, also into the next theme.
        """
        glide = self._glide_s()
        if glide <= 0 or not widget.get("smooth", False):
            return target
        state = self.motion.glide(widget["sensor"], target, now, glide, self._lead())
        if state is None or target is None:
            return target
        value, speed = state
        span = abs(widget.get("max", 100) - widget.get("min", 0)) or 1.0
        if abs(target - value) < span * 0.002 and abs(speed) * glide < span * 0.002:
            return target
        self.moving = True
        return value

    # -- effects and compositing ------------------------------------------

    def _effects(self, widget: dict[str, Any], piece: Piece) -> Piece:
        opacity = widget.get("opacity", 1.0)
        layer = _scale_alpha(piece.layer, opacity)
        box = piece.box or [piece.x, piece.y, piece.layer.width, piece.layer.height]
        light = _dimmed(piece.light, opacity)
        out, pad = self._halo(widget, layer)
        if out is None:
            return replace(piece, layer=layer, box=box, light=light)
        out.alpha_composite(layer, (pad, pad))
        return replace(piece, layer=out, x=piece.x - pad, y=piece.y - pad, box=box, light=light)

    def _halo(
        self, widget: dict[str, Any], layer: Image.Image, exact: bool = False
    ) -> tuple[Image.Image | None, int]:
        """Shadow and glow of ``layer`` (without the layer), ``pad`` pixels larger
        on every side; None without effects. ``exact``: see :func:`_blur`."""
        glow = widget.get("glow", 0.0)
        shadow = widget.get("shadow")
        if not glow and not shadow:
            return None, 0
        pad = 0
        if glow:
            pad = max(pad, widget.get("glow_radius", 10) * 2)
        if shadow:
            pad = max(pad, widget.get("shadow_blur", 6) * 2 + abs(widget.get("shadow_offset", 3)))
        size = (layer.width + 2 * pad, layer.height + 2 * pad)
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        if shadow:
            tint = self.color(shadow)
            silhouette = Image.new("RGBA", layer.size, (*tint[:3], 0))
            silhouette.putalpha(layer.getchannel("A").point(lambda a: a * tint[3] // 255))
            offset = widget.get("shadow_offset", 3)
            base = Image.new("RGBA", size, (0, 0, 0, 0))
            base.paste(silhouette, (pad + offset, pad + offset))
            out.alpha_composite(_blur(base, widget.get("shadow_blur", 6), exact))
        if glow:
            base = Image.new("RGBA", size, (0, 0, 0, 0))
            base.paste(layer, (pad, pad))
            halo = _blur(base, widget.get("glow_radius", 10), exact)
            boost = 1.0 + glow * 2.5  # light spills: brighter than the shape's own alpha
            halo.putalpha(halo.getchannel("A").point(lambda a: min(255, int(a * boost * glow))))
            out.alpha_composite(halo)
        return out, pad

    @staticmethod
    def _paste(
        canvas: Image.Image, layer: Image.Image, x: int, y: int, opaque: bool = False
    ) -> None:
        """alpha_composite that tolerates layers hanging off the canvas. Onto an
        ``opaque`` canvas, a paste through the layer's alpha gives the same
        colours in place (the canvas's own alpha is not used afterwards)."""
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + layer.width, canvas.width), min(y + layer.height, canvas.height)
        if x1 <= x0 or y1 <= y0:
            return
        if (x0, y0, x1, y1) != (x, y, x + layer.width, y + layer.height):
            layer = layer.crop((x0 - x, y0 - y, x1 - x, y1 - y))
        if opaque:
            canvas.paste(layer, (x0, y0), layer)
        else:
            canvas.alpha_composite(layer, (x0, y0))

    def _composite(
        self,
        canvas: Image.Image,
        piece: Piece,
        origin: tuple[int, int] = (0, 0),
        opaque: bool = False,
    ) -> None:
        """Draw ``piece`` onto ``canvas``, whose top left corner is ``origin`` on
        the panel (``opaque``: see :meth:`_paste`)."""
        ox, oy = origin
        if piece.backdrop is not None and piece.box is not None:
            bx, by, bw, bh = piece.box
            bx, by = bx - ox, by - oy
            region = (
                max(bx, 0),
                max(by, 0),
                min(bx + bw, canvas.width),
                min(by + bh, canvas.height),
            )
            if region[2] > region[0] and region[3] > region[1]:
                blurred = canvas.crop(region).filter(
                    ImageFilter.GaussianBlur(piece.backdrop_radius)
                )
                mask = piece.backdrop.crop(
                    (region[0] - bx, region[1] - by, region[2] - bx, region[3] - by)
                )
                canvas.paste(blurred, region[:2], mask)
        if piece.add:
            self._add_light(canvas, piece.layer, piece.x - ox, piece.y - oy)
            return
        self._paste(canvas, piece.layer, piece.x - ox, piece.y - oy, opaque)
        if piece.light is not None:
            light = piece.light
            self._add_light(canvas, light.layer, light.x - ox, light.y - oy)

    @staticmethod
    def _add_light(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
        """Light (a layer whose colour is the light) added onto the canvas, as
        light adds up; the part on the canvas only."""
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + layer.width, canvas.width), min(y + layer.height, canvas.height)
        if x1 <= x0 or y1 <= y0:
            return
        if (x0, y0, x1, y1) != (x, y, x + layer.width, y + layer.height):
            layer = layer.crop((x0 - x, y0 - y, x1 - x, y1 - y))
        canvas.paste(ImageChops.add(canvas.crop((x0, y0, x1, y1)), layer), (x0, y0))

    @staticmethod
    def _extent(piece: Piece) -> tuple[int, int, int, int]:
        """Everything a piece can change on the canvas: its layer, a backdrop
        box and its light."""
        if piece.span is not None:
            return piece.span
        x0, y0 = piece.x, piece.y
        x1, y1 = x0 + piece.layer.width, y0 + piece.layer.height
        if piece.backdrop is not None and piece.box is not None:
            bx, by, bw, bh = piece.box
            x0, y0, x1, y1 = min(x0, bx), min(y0, by), max(x1, bx + bw), max(y1, by + bh)
        if piece.light is not None:
            light = piece.light
            x0, y0 = min(x0, light.x), min(y0, light.y)
            x1, y1 = max(x1, light.x + light.layer.width), max(y1, light.y + light.layer.height)
        piece.span = (x0, y0, x1, y1)
        return piece.span

    @staticmethod
    def _merge(rects: list[list[int]], gap: int = 8) -> list[list[int]]:
        """Merge rectangles that overlap or nearly touch, until none do."""
        rects = [r[:] for r in rects]
        merged = True
        while merged:
            merged = False
            for i in range(len(rects)):
                for j in range(i + 1, len(rects)):
                    a, b = rects[i], rects[j]
                    near_x = a[0] - gap < b[2] and b[0] - gap < a[2]
                    near_y = a[1] - gap < b[3] and b[1] - gap < a[3]
                    if near_x and near_y:
                        rects[i] = [
                            min(a[0], b[0]),
                            min(a[1], b[1]),
                            max(a[2], b[2]),
                            max(a[3], b[3]),
                        ]
                        del rects[j]
                        merged = True
                        break
                if merged:
                    break
        return rects

    def _dirty(
        self, before: list[tuple[str, Piece]], after: list[tuple[str, Piece]]
    ) -> list[tuple[int, int, int, int]]:
        """The separate regions to compose again (none if nothing changed)."""
        width, height = self.theme.width, self.theme.height
        full = [(0, 0, width, height)]
        old, new = dict(before), dict(after)
        if [w for w, _ in before if w in new] != [w for w, _ in after if w in old]:
            return full  # the stacking order changed
        rects = [
            list(self._extent(piece))
            for wid in old.keys() | new.keys()
            if old.get(wid) is not new.get(wid)
            for piece in (old.get(wid), new.get(wid))
            if piece is not None
        ]
        backdrops = [
            self._extent(piece) for _, piece in after if piece.backdrop is not None and piece.box
        ]
        rects = self._merge(rects)
        # A frosted-glass piece blurs what lies beneath its whole box.
        grown = True
        while grown and backdrops:
            grown = False
            for r in rects:
                for bx0, by0, bx1, by1 in backdrops:
                    if bx0 < r[2] and bx1 > r[0] and by0 < r[3] and by1 > r[1]:
                        wider = [min(r[0], bx0), min(r[1], by0), max(r[2], bx1), max(r[3], by1)]
                        if wider != r:
                            r[:] = wider
                            grown = True
            if grown:
                rects = self._merge(rects)
        clipped = []
        for x0, y0, x1, y1 in rects:
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
            if x1 > x0 and y1 > y0:
                clipped.append((x0, y0, x1, y1))
        return clipped

    UNDERLAYS = 16  # regions whose underlay is kept

    def _compose_region(
        self,
        pieces: list[tuple[str, Piece]],
        region: tuple[int, int, int, int],
        changed: frozenset[str] | set[str] = frozenset(),
    ) -> Image.Image:
        """The region, laid piece by piece. What lies under the first ``changed``
        piece is kept: while those pieces stay the same, the next frame starts
        from there (the same steps in the same order, so the same pixels)."""
        x0, y0, x1, y1 = region
        inside = []
        for wid, piece in pieces:
            px0, py0, px1, py1 = self._extent(piece)
            if px0 < x1 and px1 > x0 and py0 < y1 and py1 > y0:
                inside.append((wid, piece))  # outside the region: its pixels are unchanged
        below = next((i for i, (wid, _) in enumerate(inside) if wid in changed), len(inside))
        hit = self._underlays.get(region)
        reused = (
            hit is not None
            and len(hit[0]) <= below
            and all(a is b for a, (_, b) in zip(hit[0], inside, strict=False))
        )
        if reused:
            done, part = len(hit[0]), hit[1].copy()
            self._underlays[region] = self._underlays.pop(region)  # used: the last to go
        else:
            done = 0
            part = Image.new("RGBA", (x1 - x0, y1 - y0), self.color(self.theme.background_color))
            if self._background is not None:
                part.alpha_composite(self._background.crop(region))
        for i, (wid, piece) in enumerate(inside[done:], done):
            if i == below and not (reused and done == below):
                self._keep_underlay(region, [p for _, p in inside[:below]], part)
            try:
                self._composite(part, piece, (x0, y0), self._opaque)
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {wid!r} failed: {exc}")
        return part

    def _keep_underlay(self, region: tuple[int, int, int, int], under: list[Piece], part) -> None:
        self._underlays.pop(region, None)
        self._underlays[region] = (under, part.copy())
        while len(self._underlays) > self.UNDERLAYS:
            self._underlays.pop(next(iter(self._underlays)))

    def _compose(self, pieces: list[tuple[str, Piece]]) -> Image.Image:
        theme = self.theme
        full = (0, 0, theme.width, theme.height)
        last = self._last if self.incremental else None
        regions = [full] if last is None else self._dirty(last[0], pieces)
        if last is not None and not regions:
            self._last = (pieces, last[1])
            return last[1]
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in regions)
        changed: set[str] = set()
        if last is not None:
            before = dict(last[0])
            changed = {wid for wid, piece in pieces if before.get(wid) is not piece}
        if last is None or area > 0.6 * theme.width * theme.height:
            canvas = self._compose_region(pieces, full, changed)
        else:
            canvas = last[1]
            for region in regions:
                canvas.paste(self._compose_region(pieces, region, changed), region[:2])
        self._last = (pieces, canvas)
        return canvas

    def _cached(
        self, widget: dict[str, Any], content: Any, build: Callable[[], Piece | None]
    ) -> Piece | None:
        """Reuse the finished layer while the widget and its content stay the same."""
        wid = widget["id"]
        hit = self._cache.get(wid)
        if hit is not None and hit[0] == content:
            return hit[1]
        if self.background_builds and hit is not None:
            return self._cached_later(widget, content, build, hit)

        piece = build()
        if piece is not None:
            piece = self._effects(widget, piece)
            self._cache[wid] = (content, piece)
        return piece

    def _cached_later(
        self,
        widget: dict[str, Any],
        content: Any,
        build: Callable[[], Piece | None],
        hit: tuple[Any, Piece],
    ) -> Piece | None:
        """Keep showing ``hit`` while the new piece is built in the helper thread."""
        wid = widget["id"]
        pending = self._pending.get(wid)
        if pending is not None:
            built_for, future = pending
            if not future.done():
                return hit[1]  # the newest content follows once this build is done
            del self._pending[wid]
            try:
                piece = future.result()
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {wid!r} failed: {exc}")
                piece = None
            if piece is not None:
                hit = self._cache[wid] = (built_for, piece)
            if built_for == content:
                return hit[1]
        if self._builder is None:
            self._builder = _Builder()

        def job() -> Piece | None:
            built = build()
            return None if built is None else self._effects(widget, built)

        self._pending[wid] = (content, self._builder.submit(job))
        return hit[1]

    def close(self) -> None:
        """Stop the helper thread and the screen, and let go of the contexts of plugin widget types
        (a replaced renderer should call this; with the back references of its slots, look and
        drawing weak, it is then freed with its last reference)."""
        if self._builder is not None:
            self._builder.close()
            self._builder = None
        if self.screen is not None:
            try:
                self.screen.close()
            except Exception as exc:
                log.warning("screen %s: %s", self.theme.screen["name"], exc)
            self.screen = None
        self._plugin_types.clear()  # (their contexts refer to this renderer)

    # -- plugins -----------------------------------------------------------

    def _make_screen(self) -> Any:
        if self.theme.screen is None:
            return None
        from libre_panel.plugins.loader import registry
        from libre_panel.plugins.render import RenderContext

        name = self.theme.screen["name"]
        cls = registry().get("screens", name)
        if cls is None:
            return None  # the theme loader has warned
        try:
            return cls(RenderContext(self, cls), dict(self.theme.screen["options"]))
        except Exception as exc:  # a broken screen must not blank the panel
            self._warn(f"screen {name!r} failed to start: {exc}")
            return None

    def _screen_image(self, snapshot: Snapshot, now: float) -> Image.Image | None:
        try:
            image = self.screen.render(snapshot, now)
        except Exception as exc:
            self._warn(f"screen {self.theme.screen['name']!r} failed: {exc}")
            return None
        if getattr(self.screen, "moving", False):
            self.moving = True
        return image

    def _screen_piece(self, image: Image.Image) -> Piece:
        name = self.theme.screen["name"]
        if self._screen_last is not None and self._screen_last[0] is image:
            return self._screen_last[1]  # the same picture: nothing to compose again
        size = (self.theme.width, self.theme.height)
        layer = image if image.mode == "RGBA" else image.convert("RGBA")
        if layer.size != size:
            self._warn(f"screen {name!r} draws {layer.size[0]}x{layer.size[1]}, not {size}")
            layer = layer.resize(size)
        piece = Piece(layer, 0, 0)
        self._screen_last = (image, piece)
        return piece

    def _draw_plugin(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        wtype = widget["type"]
        if wtype not in self._plugin_types:
            from libre_panel.plugins.loader import registry
            from libre_panel.plugins.render import RenderContext

            cls = registry().get("widgets", wtype)
            self._plugin_types[wtype] = (cls(), RenderContext(self, cls)) if cls else None
        entry = self._plugin_types[wtype]
        if entry is None:
            return None  # not installed; the theme loader has warned
        kind, ctx = entry

        def build() -> Piece | None:
            result = kind.draw(widget, ctx, snapshot, now)
            if result is None:
                return None
            at = (widget["x"], widget["y"])
            image, (x, y) = result if isinstance(result, tuple) else (result, at)
            return Piece(image if image.mode == "RGBA" else image.convert("RGBA"), int(x), int(y))

        piece = self._cached(widget, ("plugin", kind.key(widget, snapshot, now)), build)
        edges = getattr(kind, "edges", None)  # where the panel light glints (WidgetType.edges)
        if edges is not None and self.look is not None and self.look.light is not None:
            for edge in edges(widget, ctx, snapshot, now) or ():
                self.add_light_edge(edge)
        return piece

    # -- the look ----------------------------------------------------------

    def add_light_edge(self, edge: Any) -> None:
        """Let the panel light (theme ``look.light``) glint on ``edge`` (a
        render.look.LightEdge) in this frame. Call it on every frame, on the
        render thread (while drawing a widget, not in a build that may run in
        the helper thread), with the same object while nothing changes."""
        self.light_edges.append(edge)
        self.edge_owners[id(edge)] = self._drawing  # its gloss lies on that piece

    def transition_ended(self, kind: Any, now: float) -> None:
        """A transition between themes ended at ``now`` (the main loop says so):
        after a tube or an entrance, the panel light sweeps across."""
        if self.look is not None:
            self.look.transition_ended(kind, now)

    @property
    def prepares(self) -> bool:
        """Whether :meth:`warm` has something to load before the first frame
        (a screen's ``prepare()``, light layers, the rotors of fans)."""
        return (
            self.screen is not None
            or self.look is not None
            or bool(self._finishes)
            or any(w["type"] == "fan" for w in self.widgets)
        )

    def _prepare_fans(self) -> None:
        """Every position of the theme's fans: the first frame would read their rotor's
        144 pictures (render/fan.py), about 200 ms."""
        for widget in self.widgets:
            if widget["type"] == "fan":
                color = self.color(widget["color"])
                if color is not None:
                    fan.preload(color[:3], widget["w"], widget["h"])

    def _prepare_look(self) -> None:
        """Load the light layers and build what the finishes and the look draw
        from, so the first frame loads nothing (SPUR II stalled 118 ms on a
        layer loaded in a frame)."""
        for widget in self.widgets:
            finish = self._finishes.get(widget["id"])
            if finish is None:
                continue
            try:
                finish.prepare(self, widget)
            except Exception as exc:  # it is drawn when it comes, or flat
                self._warn(f"widget {widget['id']!r} failed to prepare: {exc}")
        if self.look is not None:
            self.look.prepare()

    def warm(
        self, snapshot: Snapshot | None, now: float, pause: Callable[[], Any] | None = None
    ) -> None:
        """Build what the first frames need, away from the render thread: the
        screen's ``prepare()``, the light layers of the look and the finishes,
        the fans' rotors, and one frame of the widgets (cards, texts, icons,
        graph strips). The screen does not draw here: its frames belong to the
        render thread. ``pause`` runs between widgets (to wait while the render
        thread draws)."""
        screen = self.screen
        if screen is not None:
            try:
                screen.prepare()
            except Exception as exc:  # a broken plugin must not keep the theme away
                self._warn(f"screen {self.theme.screen['name']!r} failed to prepare: {exc}")
        if self.look is not None or self._finishes:
            self._prepare_look()
        self._prepare_fans()
        if snapshot is None:
            return
        self.screen, self._breathe = None, pause
        try:
            self.render(snapshot, now)
        finally:
            self.screen, self._breathe = screen, None

    def _warm_slots(self, snapshot: Snapshot, now: float) -> None:
        """Draw every card of every slot once, whichever is on show, so that the first change
        finds its pieces made (a card's texts, icons and symbols take a few frames' time)."""
        for slot in self._slots.values():
            for card in range(len(slot.kinds)):
                slot.pinned = card
                self.render(snapshot, now)
            slot.settle()

    # -- rendering ---------------------------------------------------------

    def render(
        self, snapshot: Snapshot, now: float | None = None
    ) -> tuple[Image.Image, dict[str, Box]]:
        now = clock() if now is None else now
        snapshot = _finite(snapshot)
        if self._slots and self.animate and not self.preview and not self._slots_warm:
            self._slots_warm = True  # (once, with the first frame, or with the warm-up's)
            self._warm_slots(snapshot, now)
        theme = self.theme
        self.moving = False
        self.snapshot = snapshot
        self.light_edges, self.edge_owners = [], {}
        self.motion.begin(snapshot.readings, now, self._glide_s(), self._lead())
        boxes: dict[str, Box] = {}
        pieces: list[tuple[str, Piece]] = []
        self._drawing = "\0screen"
        screen = self._screen_image(snapshot, now) if self.screen is not None else None
        for slot in self._slots.values():  # which card each slot shows, and how far a change is
            slot.begin(snapshot, now)
        for widget in self.widgets:
            if not widget.get("visible", True):
                continue
            module = self._modules.get(widget.get("_module", ""))
            slot = how = None
            if "_card" in widget and module is not None:  # a part of a card in a slot
                slot = self._slots.get(module["id"])
                how = slot.shows(widget["_card"]) if slot is not None else None
                if slot is not None and how is None:
                    continue  # the card is not on show
            # (what a card that rolls up shows is what it showed: its conditions held then)
            if how != "frozen" and self._missing(widget, snapshot):
                continue
            if module is not None and (not module["visible"] or self._missing(module, snapshot)):
                continue
            draw = (
                self._drawers.get(widget["id"])
                or getattr(self, f"_draw_{widget['type']}", None)
                or self._draw_plugin
            )
            self._drawing = widget["id"]
            try:
                if how == "frozen":  # a card rolling up: what it showed before
                    piece = slot.leave(widget["id"])
                else:
                    piece = draw(widget, snapshot, now)
                    if how == "live":
                        piece = slot.live(widget["id"], piece)
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {widget['id']!r} failed: {exc}")
                continue
            finally:
                if self._breathe is not None:
                    self._breathe()
            if piece is None:
                continue
            pieces.append((widget["id"], piece))
            if slot is None:  # what lies over a bar or a graph: its mark, its end dot
                if widget["type"] == "bar" and widget.get("mark"):
                    over = self._draw_mark(widget, snapshot), "mark"
                elif widget["type"] == "graph" and widget.get("end_dot"):
                    over = self._draw_end_dot(widget, snapshot, now), "dot"
                else:
                    over = None, ""
                if over[0] is not None:
                    pieces.append((f"{widget['id']}\0{over[1]}", over[0]))
            if module is not None:
                boxes[module["id"]] = self.module_boxes[module["id"]]
                continue
            if widget["id"].startswith("\0"):  # drawn for the theme (the modules' backdrop)
                continue
            boxes[widget["id"]] = piece.box or [
                piece.x,
                piece.y,
                piece.layer.width,
                piece.layer.height,
            ]
        for slot in self._slots.values():  # frame and line of light, over the cards
            slot.overlay(pieces)
        dust = self._dust_on()
        if screen is not None:
            alone = not pieces and self.look is None and not dust
            if alone and screen.mode == "RGB" and screen.size == (theme.width, theme.height):
                # an opaque screen alone is the frame: nothing to compose
                self._last = None
                return screen, boxes
            pieces.insert(0, ("\0screen", self._screen_piece(screen)))  # ids are never empty
        if self.look is not None:
            self.look.compose(pieces, snapshot, now)  # the grid beneath, the sun's gloss on top
        canvas = self._compose(pieces)
        if dust:
            canvas = self._dusted(canvas, now)  # RGB once there is dust on it
        frame = canvas if canvas.mode == "RGB" else canvas.convert("RGB")
        return (frame if self.look is None else self.look.finish(frame, now)), boxes

    # -- dust --------------------------------------------------------------

    def _dust_on(self) -> bool:
        """Whether the theme's dust is drawn: on a video panel (where every frame
        goes out) and in previews. On the PNG path it would change the whole frame at
        every update, and the link sends what changed."""
        dust = (self.theme.look or {}).get("dust")
        return bool(dust and (self.continuous or self.preview))

    def _dusted(self, canvas: Image.Image, now: float) -> Image.Image:
        if self._dust is None:
            config = self.theme.look["dust"]  # type: ignore[index]
            colors = [self.color(c)[:3] for c in config["colors"]]  # type: ignore[index]
            self._dust = Dust(canvas.size, config, colors)
            self._dust_submit = _weakly(self._submit)  # (the dust is ours: no cycle back)
        # where a slow piece must not stall the video, the grains move in the helper thread too
        self._dust.submit = self._dust_submit if self.background_builds else None
        return self._dust.apply(canvas, now, self.shown, self.toast)

    def _submit(self, job: Callable[[], Any]) -> Future:
        if self._builder is None:
            self._builder = _Builder()
        return self._builder.submit(job)

    @staticmethod
    def _missing(widget: dict[str, Any], snapshot: Snapshot) -> bool:
        for need in (widget.get("needs") or "").split(","):  # all of them must hold
            need = need.strip()
            if need and not need_holds(need, snapshot):
                return True
        if widget["type"] == "module":
            return False
        if not widget.get("hide_if_missing"):
            return False
        if widget["type"] == "weather":
            key = f"weather.{widget['field']}"
        elif widget["type"] == "icon" and widget["icon"] == "weather":
            key = widget.get("sensor") or "weather.code"
        elif widget["type"] == "graph":
            return len(snapshot.history.get(widget["sensor"], [])) < 2
        else:
            key = widget.get("sensor") or None  # an icon has an empty one
        return key is not None and snapshot.value(key) is None

    # -- text --------------------------------------------------------------

    def _digit_cell(self, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
        key = id(font)
        if key not in self._digit_width:
            self._digit_width[key] = max(font.getlength(d) for d in _DIGITS)
        return self._digit_width[key]

    def _fit(self, widget: dict[str, Any], text: str) -> tuple[str, int]:
        """The text and font size that keep within ``max_width``."""
        size = widget.get("font_size", 24)
        limit = widget.get("max_width", 0)
        if limit <= 0 or not text or "\n" in text:
            return text, size
        spacing = widget.get("letter_spacing", 0)
        tabular = widget.get("tabular", False)

        def width(candidate: str, font: Any) -> float:
            cell = self._digit_cell(font) if tabular else 0
            advances = [
                cell if tabular and ch in _DIGITS else font.getlength(ch) for ch in candidate
            ]
            return sum(advances) + spacing * max(0, len(candidate) - 1)

        font = self.font(widget.get("font", ""), size)
        measured = width(text, font)
        if measured <= limit:
            return text, size
        if widget.get("fit") == "ellipsis":
            while len(text) > 1 and width(text.rstrip() + "…", font) > limit:
                text = text[:-1]
            return text.rstrip() + "…", size
        return text, max(6, int(size * limit / measured))

    def _text_piece(self, widget: dict[str, Any], text: str, color: str) -> Piece:
        text, size = self._fit(widget, text)
        if size != widget.get("font_size", 24):
            widget = {**widget, "font_size": size}
        font = self.font(widget.get("font", ""), size)
        fill = self.color(color)
        x, y = widget["x"], widget["y"]
        align = widget.get("align", "left")
        spacing = widget.get("letter_spacing", 0)
        tabular = widget.get("tabular", False)
        finish = self._finishes.get(widget.get("id", ""))  # a finish may letter its own way
        lettered = finish is not None and finish.lettering(self, widget, font, text)
        if "\n" in text or not (
            spacing or lettered or (tabular and any(c in _DIGITS for c in text))
        ):
            anchor = _ANCHORS.get(align, "la")
            measure = ImageDraw.Draw(Image.new("L", (1, 1)))
            x0, y0, x1, y1 = measure.textbbox((x, y), text, font=font, anchor=anchor)
            x0, y0 = int(math.floor(x0)), int(math.floor(y0))
            w, h = max(1, math.ceil(x1) - x0 + 1), max(1, math.ceil(y1) - y0 + 1)
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((x - x0, y - y0), text, font=font, anchor=anchor, fill=fill)
            return Piece(layer, x0, y0)
        # Hand layout: letter spacing and/or equal-width digits.
        cell = self._digit_cell(font)
        advances = []
        for ch in text:
            advance = cell if tabular and ch in _DIGITS else font.getlength(ch)
            advances.append(advance + spacing)
        total = max(1.0, sum(advances) - spacing)
        ascent, descent = font.getmetrics()
        start = {"left": x, "center": x - total / 2, "right": x - total}.get(align, x)
        margin = max(2, widget.get("font_size", 24) // 6)  # room for overhanging glyphs
        w, h = math.ceil(total) + 2 * margin, ascent + descent
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pen = float(margin)
        for ch, advance in zip(text, advances, strict=True):
            at = pen + (cell - font.getlength(ch)) / 2 if tabular and ch in _DIGITS else pen
            if lettered:  # the finish's letter: (image, left, top), or None for a blank
                placed = finish.glyph(self, widget, font, ch, fill, at % 1.0)
                if placed is not None:
                    self._paste(layer, placed[0], math.floor(at) + placed[1], placed[2])
            else:
                glyph, gx = self._glyph(font, ch, fill, at, margin, h)
                self._paste(layer, glyph, gx, 0)
            pen += advance
        left = math.floor(start) - margin
        return Piece(layer, left, y, box=[left + margin, y, math.ceil(total), h])

    GLYPHS = 4096  # glyph images kept: a gliding number changes a digit or two at a time

    def _glyph(
        self, font: Any, ch: str, fill: RGBA, at: float, pad: int, h: int
    ) -> tuple[Image.Image, int]:
        """``ch`` drawn at ``at`` (its fraction of a pixel counts) on an image
        ``pad`` px wider on each side, and where its left edge goes: kept, so
        the next text with that character in that place costs no rendering."""
        whole = math.floor(at)
        key = (id(font), ch, fill, round(at - whole, 4), pad, h)
        with self._glyph_lock:  # the helper thread draws texts too
            image = self._glyphs.get(key)
            if image is not None:
                self._glyphs.move_to_end(key)
                return image, whole - pad
        image = Image.new("RGBA", (math.ceil(font.getlength(ch)) + 2 * pad + 1, h), (0, 0, 0, 0))
        ImageDraw.Draw(image).text((at - whole + pad, 0), ch, font=font, fill=fill, anchor="la")
        with self._glyph_lock:
            self._glyphs[key] = image
            while len(self._glyphs) > self.GLYPHS:
                self._glyphs.popitem(last=False)
        return image, whole - pad

    def _format(self, widget: dict[str, Any], reading: Any) -> tuple[str, float | None]:
        if reading is None or reading.value is None:
            return widget.get("fallback", "--"), None
        value = reading.value
        if reading.key in TRANSLATED_READINGS and isinstance(value, str):
            value = i18n.t(value)  # e.g. "Overcast" -> "Bedeckt"
        try:
            text = safe_format(widget["format"], value, reading.unit, reading.label)
        except FormatError:
            text = value if isinstance(value, str) else widget.get("fallback", "--")
        return text, _number(value)

    TEXTS = 256  # text pieces kept: gliding numbers come back to the same texts

    def _text_widget(self, widget: dict[str, Any], text: str, color: str) -> Piece | None:
        """The text as a piece, kept by (text, colour): a number gliding back
        and forth, or a seconds clock, draws each text once."""
        wid = widget["id"]
        piece = self._texts.get((wid, text, color))
        if piece is not None:
            self._texts.move_to_end((wid, text, color))
            self._cache[wid] = ((text, color), piece)
            return piece
        piece = self._cached(widget, (text, color), lambda: self._text_piece(widget, text, color))
        hit = self._cache.get(wid)
        if hit is not None and (wid, *hit[0]) not in self._texts:  # built now, or earlier
            self._texts[(wid, *hit[0])] = hit[1]
            while len(self._texts) > self.TEXTS:
                self._texts.popitem(last=False)
        return piece

    def _draw_text(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        return self._text_widget(widget, widget["text"], widget["color"])

    def _draw_metric(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        reading = snapshot.readings.get(widget["sensor"])
        text, value = self._format(widget, reading)
        glide = self._glide_s()
        if widget.get("smooth") and glide > 0:  # the gliding value, with bars and rings
            state = self.motion.glide(widget["sensor"], value, now, glide, self._lead())
            # 25 new texts a second at most, at the graphs' points: as fluid, half the work
            at = self.motion.value(
                widget["sensor"], max(grid(now) / RATE, self.motion.start(widget["sensor"]))
            )
            if state is not None and value is not None and at is not None:
                gliding, shown = self._format(widget, replace(reading, value=at))
                if gliding != text:  # settled once it reads the same
                    text, value = gliding, shown
                    self.moving = True
        return self._text_widget(widget, text, self._rule_text(widget, value, now))

    # -- thresholds --------------------------------------------------------

    def _rule_step(self, widget: dict[str, Any], value: float | None, now: float) -> float:
        """Which colour a value shows: 0 the widget's own, i the i-th lowest
        threshold's. Crossing a threshold fades the colour in a fraction of a
        second (Motion.fade), the same for every widget of the sensor with the
        same rules: a bar and its number change together."""
        rules = sorted(widget.get("color_rules", []), key=lambda r: r["above"])
        if not rules:
            return 0.0
        step = 0 if value is None else sum(1 for rule in rules if value > rule["above"])
        if not self.animate:
            return float(step)
        key = (widget.get("sensor"), tuple((rule["above"], rule["color"]) for rule in rules))
        shown = self.motion.fade(key, step, now)
        if shown != step:
            self.moving = True
        return shown

    def _rule_mix(self, step: float) -> tuple[int, float]:
        """The colour step below ``step`` and how far on to the next (eighths)."""
        low = math.floor(step)
        return low, round(_smoothstep(step - low) * 8) / 8

    def _rule_text(self, widget: dict[str, Any], value: float | None, now: float) -> str:
        """The text colour for ``value``: its own, a threshold's, or on the way."""
        step = self._rule_step(widget, value, now)
        rules = sorted(widget.get("color_rules", []), key=lambda r: r["above"])
        colors = [widget["color"], *(rule["color"] for rule in rules)]
        low, u = self._rule_mix(step)
        if u <= 0 or low + 1 >= len(colors):
            return colors[min(low, len(colors) - 1)]
        mixed = _lerp(self.color(colors[low]), self.color(colors[low + 1]), u)
        return "#{:02x}{:02x}{:02x}{:02x}".format(*mixed)

    def _rule_paint(
        self, widget: dict[str, Any], value: float | None, now: float
    ) -> tuple[RGBA, RGBA | None]:
        """A bar's or ring's colours for ``value``: its own (``color``, and
        ``color2`` for a gradient along it), a threshold's (plain), or on the way."""
        rules = sorted(widget.get("color_rules", []), key=lambda r: r["above"])
        step = self._rule_step(widget, value, now)
        low, u = self._rule_mix(step)
        paints = [self._paint(widget, None), *(self._paint(widget, r["color"]) for r in rules)]
        if u <= 0 or low + 1 >= len(paints):
            return paints[min(low, len(paints) - 1)]
        (a0, b0), (a1, b1) = paints[low], paints[low + 1]
        if b0 is None and b1 is None:
            return _lerp(a0, a1, u), None
        return _lerp(a0, a1, u), _lerp(b0 or a0, b1 or a1, u)

    def _paint(self, widget: dict[str, Any], rule: str | None) -> tuple[RGBA, RGBA | None]:
        """A bar's colours: a threshold's colour plain, else ``color`` (to ``color2``)."""
        if rule:
            return self.color(rule), None
        return self.color(widget["color"]), self.color(widget["color2"])

    def _draw_weather(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        text, _ = self._format(widget, snapshot.readings.get(f"weather.{widget['field']}"))
        return self._text_widget(widget, text, widget["color"])

    def _draw_clock(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        return self._text_widget(widget, self._clock_text(widget, snapshot), widget["color"])

    def _clock_text(self, widget: dict[str, Any], snapshot: Snapshot) -> str:
        try:
            text = i18n.format_date(self._time(widget, snapshot), widget["format"])[:100]
        except ValueError:
            text = "--:--"
        return text.upper() if widget.get("uppercase") else text

    def _time(self, widget: dict[str, Any], snapshot: Snapshot) -> datetime:
        """The time in the widget's time zone (empty: the computer's)."""
        name = widget.get("timezone") or ""
        if not name:
            return snapshot.now
        zone = timezones.zone(name)
        if zone is None:
            self._warn(f"unknown time zone {name!r} (widget {widget['id']!r})")
            return snapshot.now
        return snapshot.now.astimezone(zone).replace(tzinfo=None)

    def _draw_countdown(
        self, widget: dict[str, Any], snapshot: Snapshot, now: float
    ) -> Piece | None:
        text = countdown_text(widget["target"], widget["part"], snapshot.now, widget["done"])
        return self._text_widget(widget, text, widget["color"])

    def _draw_analog(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        moment = self._time(widget, snapshot)
        hands = (moment.hour % 12, moment.minute, moment.second if widget["seconds"] else -1)
        return self._cached(widget, hands, lambda: self._analog_piece(widget, *hands))

    def _analog_piece(self, widget: dict[str, Any], hour: int, minute: int, second: int) -> Piece:
        x, y, w, h = widget["x"], widget["y"], max(8, widget["w"]), max(8, widget["h"])
        s = SUPERSAMPLE
        layer, draw = self._canvas(w, h)
        r = min(w, h) * s / 2
        cx, cy = w * s / 2, h * s / 2

        def at(angle: float, length: float) -> tuple[float, float]:
            a = math.radians(angle - 90)
            return cx + length * math.cos(a), cy + length * math.sin(a)

        def hand(angle: float, length: float, width: float, color: RGBA, tail: float = 0) -> None:
            start, end = at(angle + 180, tail), at(angle, length)
            draw.line([start, end], fill=color, width=max(1, round(width)))
            for px, py in (start, end):
                draw.ellipse([px - width / 2, py - width / 2, px + width / 2, py + width / 2],
                             fill=color)  # fmt: skip

        if widget["face"]:
            draw.ellipse([cx - r, cy - r, cx + r - 1, cy + r - 1], fill=self.color(widget["face"]))
        if widget["marks"]:
            marks = self.color(widget["marks"])
            fine = min(w, h) >= 110
            for tick in range(60):
                hourly = tick % 5 == 0
                if not hourly and not fine:
                    continue
                inner = r * (0.8 if tick % 15 == 0 else 0.84 if hourly else 0.9)
                width = r * (0.055 if tick % 15 == 0 else 0.04 if hourly else 0.014)
                hand(tick * 6, r * 0.93, width, marks, -inner)
        color = self.color(widget["color"])
        accent = self.color(widget["color2"])
        hand((hour + minute / 60) * 30, r * 0.5, r * 0.085, color, r * 0.08)
        hand((minute + max(0, second) / 60) * 6, r * 0.76, r * 0.055, color, r * 0.08)
        if second >= 0:
            hand(second * 6, r * 0.84, r * 0.022, accent, r * 0.18)
        dot = r * 0.065
        draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=accent)
        return Piece(self._down(layer, w, h), x, y, [x, y, w, h])

    def _draw_moon(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        phase = _number(snapshot.value(widget["sensor"]))
        if phase is None:
            return None
        phase = round(phase % 1.0, 3)
        return self._cached(widget, phase, lambda: self._moon_piece(widget, phase))

    def _moon_piece(self, widget: dict[str, Any], phase: float) -> Piece:
        """The moon lit as at ``phase`` (0 new, 0.5 full): a disc, its lit side
        bounded by the terminator, an ellipse."""
        size = max(8, widget["size"])
        s = SUPERSAMPLE
        layer, draw = self._canvas(size, size)
        r = size * s / 2 - s
        c = size * s / 2
        if widget["color2"]:
            draw.ellipse([c - r, c - r, c + r, c + r], fill=self.color(widget["color2"]))
        turn = math.cos(2 * math.pi * phase)
        waxing = phase <= 0.5
        if widget["mirror"]:
            waxing = not waxing
        right, left = [], []
        steps = 90
        for i in range(steps + 1):
            dy = -r + 2 * r * i / steps
            half = math.sqrt(max(0.0, r * r - dy * dy))
            if waxing:  # lit on the right, from the terminator to the edge
                right.append((c + half, c + dy))
                left.append((c + half * turn, c + dy))
            else:
                left.append((c - half, c + dy))
                right.append((c - half * turn, c + dy))
        if 0.004 < phase < 0.996:
            draw.polygon(right + left[::-1], fill=self.color(widget["color"]))
        x, y = widget["x"], widget["y"]
        return Piece(self._down(layer, size, size), x, y, [x, y, size, size])

    # -- fans --------------------------------------------------------------

    FANS = 512  # fan pieces kept: a fan that has turned once round costs a lookup

    def _draw_fan(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        """A rotor that turns with the sensor's RPM, in a ring that shows the load. Its
        turn is worked out from the planned time of the frame (render/fan.py)."""
        wid, x, y, w, h = widget["id"], widget["x"], widget["y"], widget["w"], widget["h"]
        rpm = _number(snapshot.value(widget["sensor"]))
        rotor = self._rotors.get(wid)
        if rotor is None:
            rotor = self._rotors[wid] = fan.Rotor(wid)
        if self.animate:
            rotor.advance(rpm, widget["max_rpm"], now)
            self.moving = self.moving or rotor.turning
        else:  # a preview or a picture: how it looks at this speed, standing
            rotor.settle(rpm, widget["max_rpm"])
        color = self.color(widget["color"])[:3]
        key = (self._keys[wid], rotor.phase, rotor.blur, rotor.dim, round(rotor.ring * 120))
        piece = self._fans.get(key)
        if piece is None:
            image = fan.fan_image(color, w, h, rotor, widget["frame"])
            at = (x + (w - image.width) // 2, y + (h - image.height) // 2)
            piece = self._effects(widget, Piece(image, at[0], at[1], [x, y, w, h]))
            self._fans[key] = piece
            while len(self._fans) > self.FANS:
                self._fans.popitem(last=False)
        else:
            self._fans.move_to_end(key)
        return piece

    # -- weather symbols ---------------------------------------------------

    def _draw_symbol(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        """A weather symbol drawn flat; the ``lit`` finish draws it from Blender's bodies.
        Both are layers that move with the planned time of the frame (render/symbols.py)."""
        from libre_panel.render import symbols

        return symbols.draw(self, widget, snapshot, now)

    # -- downloads ---------------------------------------------------------

    def _draw_downloads(
        self, widget: dict[str, Any], snapshot: Snapshot, now: float
    ) -> Piece | None:
        """A download card (render/download.py), its progress a flat bar; the ``lit``
        finish makes it light in its groove."""
        from libre_panel.render import download

        return download.draw(self, widget, snapshot, now)

    # -- pictures ----------------------------------------------------------

    def _draw_image(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        pictures = self._slides(widget)
        rel = ""
        if pictures:
            seconds = max(1, widget["seconds"])
            rel = pictures[int(snapshot.now.timestamp() // seconds) % len(pictures)]
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        if rel.startswith("@"):  # a picture a sensor source or service has (an album cover)
            return self._live_image(widget, snapshot.images.get(rel[1:]))

        def build() -> Piece:
            image = self._asset_image(rel, w, h, widget["fit"], widget.get("align", "center"))
            if image is None:
                empty = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
                return Piece(empty, x, y)
            if widget["radius"] > 0:
                image = _rounded(image, widget["radius"])
            return Piece(image, x, y)

        return self._cached(widget, rel, build)

    def _live_image(self, widget: dict[str, Any], image: Any) -> Piece | None:
        if image is None:
            return None
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]

        def build() -> Piece:
            picture = image.convert("RGBA")
            if w > 0 and h > 0:
                picture = _fitted(picture, w, h, widget["fit"], widget.get("align", "center"))
            if widget["radius"] > 0:
                picture = _rounded(picture, widget["radius"])
            return Piece(picture, x, y)

        # (the picture is part of what the piece is made of, so it is kept as long as the piece is:
        # another picture could else be made where it was, and be taken for it by its id)
        return self._cached(widget, (id(image), image), build)

    def _slides(self, widget: dict[str, Any]) -> list[str]:
        """The picture and the slideshow's others, folders read every half minute."""
        key = widget["id"]
        hit = self._slide_lists.get(key)
        if hit is not None and time.monotonic() - hit[0] < 30:
            return hit[1]
        found = [widget["src"]] if widget["src"] else []
        root = self.theme.root
        for line in (widget.get("slides") or "").splitlines():
            line = line.strip()
            if line.startswith("@"):
                found.append(line)
            elif not line or root is None:
                continue
            elif any(ch in line for ch in "*?["):
                matches = sorted(p for p in root.glob(line) if p.is_file())
                for path in matches:
                    try:
                        resolve_asset(root, path.relative_to(root).as_posix())
                    except (ThemeError, ValueError):
                        continue  # nothing outside the theme folder
                    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                        found.append(path.relative_to(root).as_posix())
            else:
                found.append(line)
        self._slide_lists[key] = (time.monotonic(), found)
        return found

    def _icon_name(self, widget: dict[str, Any], snapshot: Snapshot) -> str:
        """The icon to show: the widget's, or for ``weather`` the weather's now."""
        name = widget["icon"]
        if name == "weather":
            key = widget.get("sensor") or "weather.code"
            # day or night: the forecast says so for its hours (weather.hour.3.day),
            # Open-Meteo for now (weather.is_day); else the clock decides
            daylight = snapshot.value(
                key[: -len(".code")] + ".day" if key.endswith(".code") else ""
            )
            if daylight is None and key == "weather.code":
                daylight = snapshot.value("weather.is_day")
            night = not 6 <= snapshot.now.hour < 20 if daylight is None else not daylight
            name = weather_icon_name(snapshot.value(key), night)
        return name

    def _draw_icon(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        name = self._icon_name(widget, snapshot)
        color = self.color(widget["color"])

        def build() -> Piece:
            icon = draw_icon(name, widget["size"], color, float(widget["stroke"]))
            return Piece(icon, widget["x"], widget["y"])

        return self._cached(widget, (name, color), build)

    def _draw_calendar(
        self, widget: dict[str, Any], snapshot: Snapshot, now: float
    ) -> Piece | None:
        today = snapshot.now.date()
        colors = (widget["color"], widget["color2"], widget["muted"])
        language = i18n.language()
        return self._cached(
            widget, (today, colors, language), lambda: self._calendar_piece(widget, today)
        )

    def _calendar_piece(self, widget: dict[str, Any], today: Any) -> Piece:
        w, h = max(14, widget["w"]), max(14, widget["h"])
        first = today.replace(day=1)
        start = 0 if widget["first_day"] == "monday" else 6  # weekday() of the first column
        lead = (first.weekday() - start) % 7
        days = calendar.monthrange(today.year, today.month)[1]
        weeks = (lead + days + 6) // 7
        cell_w, cell_h = w / 7, h / (weeks + 1)
        size = max(6, min(widget["font_size"], int(cell_h * 0.62), int(cell_w * 0.5)))
        font = self.font(widget.get("font", ""), size)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        fill, mark, muted = (
            self.color(c) for c in (widget["color"], widget["color2"], widget["muted"])
        )
        monday = today - timedelta(days=today.weekday())
        for column in range(7):
            day = monday + timedelta(days=(start + column) % 7)
            name = i18n.format_date(datetime.combine(day, datetime.min.time()), "%a")[:2]
            draw.text(
                ((column + 0.5) * cell_w, cell_h / 2), name, font=font, fill=muted, anchor="mm"
            )
        r, g, b, _a = mark
        light = 0.299 * r + 0.587 * g + 0.114 * b > 150
        for number in range(1, days + 1):
            slot = lead + number - 1
            cx = (slot % 7 + 0.5) * cell_w
            cy = (slot // 7 + 1.5) * cell_h
            color = fill
            if number == today.day:  # a smooth dot: drawn larger, then reduced
                radius = min(cell_w, cell_h) * 0.46
                side = max(2, round(radius * 2))
                dot = Image.new("L", (side * 4, side * 4), 0)
                ImageDraw.Draw(dot).ellipse([0, 0, side * 4 - 1, side * 4 - 1], fill=255)
                dot = dot.resize((side, side), Image.Resampling.LANCZOS)
                corner = (round(cx - side / 2), round(cy - side / 2))
                layer.paste(Image.new("RGBA", (side, side), mark), corner, dot)
                color = (12, 16, 22, 255) if light else (255, 255, 255, 255)
            draw.text((cx, cy), str(number), font=font, fill=color, anchor="mm")
        return Piece(layer, widget["x"], widget["y"])

    # -- lists -------------------------------------------------------------

    def _draw_list(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        items = lists.pick(widget, snapshot.readings)
        items = items[: self._list_room(widget, len(items))]
        lo, hi = widget["min"], widget["max"]
        if hi <= lo:  # the largest value shown fills the bar
            hi = max([lo + 1e-9] + [i.number for i in items if i.number is not None])
        rows = []
        for item in items:
            reading = item.reading
            try:
                if widget["format"] in ("", "auto"):
                    text = auto_format(reading.value, reading.unit)
                else:
                    text = safe_format(widget["format"], reading.value, reading.unit, item.name)
            except FormatError:
                text = reading.value if isinstance(reading.value, str) else "--"
            detail = ""
            if item.detail is not None and item.detail.value is not None:
                try:
                    detail = safe_format(
                        widget["detail_format"], item.detail.value, item.detail.unit, item.name
                    )
                except FormatError:
                    detail = ""
            frac = _fraction(item.number, lo, hi, widget["scale"])
            rows.append((item.name, detail, text, round(frac, 3), _rule_color(widget, item.number)))
        content = tuple(rows)
        return self._cached(widget, content, lambda: self._list_piece(widget, rows))

    def _list_room(self, widget: dict[str, Any], count: int) -> int:
        """How many items fit."""
        w, h, fs = max(1, widget["w"]), max(1, widget["h"]), max(6, widget["font_size"])
        limit = widget["max_items"] or lists.MAX_ITEMS
        style = widget["style"]
        if style == "columns":
            room = int(w // max(4, fs * 0.45))
        elif style == "cells":
            room = int((w // 12) * (h // 12))
        else:
            columns, _w, per_column, _h, _beside = self._list_layout(widget, count)
            room = columns * per_column
        return max(0, min(count, limit, room))

    @staticmethod
    def _list_layout(widget: dict[str, Any], count: int) -> tuple[int, float, int, float, bool]:
        """Rows and bars: columns, their width, rows per column, row height and
        whether bars stand beside the names (else below them)."""
        w, h, fs = max(1, widget["w"]), max(1, widget["h"]), max(6, widget["font_size"])
        gap = fs * 1.2
        bars = widget["style"] == "bars" and widget["levels"]
        if widget["columns"] > 0:
            choices = [widget["columns"]]
        else:  # as few as hold them all, each at least ten font sizes wide
            choices = list(range(1, max(1, int((w + gap) // (fs * 10 + gap))) + 1))
        for columns in choices:
            column_w = (w - (columns - 1) * gap) / columns
            beside = bars and not widget["detail"] and column_w >= fs * 15
            row_h = fs * (1.8 if beside else 2.5 if bars else 1.75)
            per_column = int(h // row_h)
            if per_column * columns >= count or columns == choices[-1]:
                break
        return columns, column_w, per_column, row_h, beside

    def _list_text(self, layer: Image.Image, text: str, x: float, cap_top: float, size: float,
                   color: str, font: str, align: str = "left", width: float = 0,
                   label: bool = False) -> None:  # fmt: skip
        """Text on a list's layer, its capitals' top at ``cap_top``."""
        if not text:
            return
        size = max(6, round(size))
        part = {
            "x": round(x), "y": round(cap_top - 0.3 * size), "font": font, "font_size": size,
            "align": align, "max_width": max(0, round(width)), "fit": "ellipsis",
            "letter_spacing": max(1, round(size * 0.12)) if label else 0, "tabular": not label,
        }  # fmt: skip
        piece = self._text_piece(part, text, color)
        self._paste(layer, piece.layer, piece.x, piece.y)

    def _list_bar(self, layer: Image.Image, widget: dict[str, Any], frac: float,
                  rule: str | None, box: tuple[float, float, float, float],
                  direction: str = "right") -> None:  # fmt: skip
        x, y, w, h = (round(v) for v in box)
        if w < 2 or h < 2:
            return
        accent = self.color(widget["color2"])
        track = self.color(widget["background"])
        deep = _lerp(accent, track, 0.55) if track else accent
        bar = {
            "radius": min(w, h) // 2, "direction": direction, "segments": 0, "segment_gap": 0,
            "color": "#{:02x}{:02x}{:02x}{:02x}".format(*deep), "color2": widget["color2"],
            "background": widget["background"],
        }  # fmt: skip
        piece = self._bar_piece(bar, frac, self._paint(bar, rule), x, y, w, h)
        self._paste(layer, piece.layer, x, y)

    def _list_piece(self, widget: dict[str, Any], rows: list[tuple]) -> Piece:
        w, h = max(1, widget["w"]), max(1, widget["h"])
        margin = max(2, widget["font_size"] // 3)  # room for glyphs that overhang
        layer = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
        inner = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if not rows:
            if widget["empty"]:
                size = max(7, widget["font_size"] * 0.7)
                self._list_text(inner, widget["empty"], w / 2, h / 2 - 0.35 * size, size,
                                widget["muted"], widget["label_font"], "center", w)  # fmt: skip
        elif widget["style"] == "columns":
            self._list_columns(inner, widget, rows)
        elif widget["style"] == "cells":
            self._list_cells(inner, widget, rows)
        else:
            self._list_rows(inner, widget, rows)
        layer.alpha_composite(inner, (margin, margin))
        x, y = widget["x"], widget["y"]
        return Piece(layer, x - margin, y - margin, [x, y, w, h])

    def _list_name(self, widget: dict[str, Any], name: str) -> str:
        return name.upper() if widget["uppercase"] else name

    def _list_rows(self, layer: Image.Image, widget: dict[str, Any], rows: list[tuple]) -> None:
        h, fs = widget["h"], max(6, widget["font_size"])
        columns, column_w, _per, row_h, beside = self._list_layout(widget, len(rows))
        columns = max(1, min(columns, len(rows)))
        gap = fs * 1.2
        if widget["columns"] <= 0:  # automatic: the columns share the width
            column_w = (widget["w"] - (columns - 1) * gap) / columns
        per_column = -(-len(rows) // columns)
        pitch = min(h / per_column, row_h * 1.45)
        ls = max(7, fs * 0.62)  # names
        bars = widget["style"] == "bars" and widget["levels"]
        bar_h = max(3, round(fs * 0.26))
        font = self.font(widget["font"], fs)
        cell = self._digit_cell(font)
        widest = max(
            sum(cell if ch in _DIGITS else font.getlength(ch) for ch in row[2]) for row in rows
        )
        value_w = min(widest + fs * 0.2, column_w * (0.5 if bars else 0.62))
        for i, (name, detail, text, frac, rule) in enumerate(rows):
            col, row = divmod(i, per_column)
            x0 = col * (column_w + gap)
            top = row * pitch + (pitch - row_h) / 2
            color = rule or widget["color"]
            if beside:  # name | bar | value
                mid = top + row_h / 2
                name_w = column_w * 0.3
                self._list_text(layer, self._list_name(widget, name), x0, mid - 0.35 * ls, ls,
                                widget["muted"], widget["label_font"], width=name_w - fs * 0.4,
                                label=widget["uppercase"])  # fmt: skip
                self._list_bar(layer, widget, frac, rule,
                               (x0 + name_w, mid - bar_h / 2,
                                column_w - name_w - value_w - fs * 0.5, bar_h))  # fmt: skip
                self._list_text(layer, text, x0 + column_w, mid - 0.35 * fs, fs, color,
                                widget["font"], "right", value_w)  # fmt: skip
                continue
            cap = top + (row_h - (0.7 * fs + (fs * 0.45 + bar_h if bars else 0))) / 2
            name_text = self._list_name(widget, name)
            name_w = column_w - value_w - fs * 0.6
            name_cap = cap + 0.35 * (fs - ls)
            muted, label_font = widget["muted"], widget["label_font"]
            self._list_text(layer, name_text, x0, name_cap, ls, muted, label_font, width=name_w,
                            label=widget["uppercase"])  # fmt: skip
            if detail:
                font = self.font(widget["label_font"], round(ls))
                spacing = max(1, round(ls * 0.12)) if widget["uppercase"] else 0
                used = font.getlength(name_text) + spacing * len(name_text) + ls * 0.7
                if used < name_w - ls * 3:
                    self._list_text(layer, detail, x0 + used, name_cap, ls,
                                    widget["muted"], widget["label_font"],
                                    width=name_w - used)  # fmt: skip
            self._list_text(layer, text, x0 + column_w, cap, fs, color, widget["font"], "right",
                            column_w * 0.6)  # fmt: skip
            if bars:
                self._list_bar(layer, widget, frac, rule,
                               (x0, cap + 0.7 * fs + fs * 0.45, column_w, bar_h))  # fmt: skip

    def _list_columns(self, layer: Image.Image, widget: dict[str, Any], rows: list[tuple]) -> None:
        """Upright bars side by side (the cores of a processor)."""
        w, h, fs = widget["w"], widget["h"], max(6, widget["font_size"])
        slot = w / len(rows)
        ls = max(6, min(fs * 0.62, slot * 0.5))
        names = lists.short_names([r[0] for r in rows])
        show_names = slot >= ls * 1.4 and max(len(n) for n in names) * ls * 0.62 <= slot
        vs = min(fs, slot / 2.3)  # the values over the columns
        show_values = vs >= 7
        top = vs * 0.7 + vs * 0.6 if show_values else 0
        bottom = h - (ls * 0.7 + ls * 0.6 if show_names else 0)
        bar_w = max(2, min(slot * 0.62, slot - 2, fs * 1.3))
        for i, (_name, _detail, text, frac, rule) in enumerate(rows):
            cx = slot * i + slot / 2
            self._list_bar(layer, widget, frac, rule,
                           (cx - bar_w / 2, top, bar_w, bottom - top), "up")  # fmt: skip
            if show_values:
                self._list_text(layer, text, cx, 0, vs, rule or widget["color"], widget["font"],
                                "center", slot)  # fmt: skip
            if show_names:
                self._list_text(layer, names[i], cx, h - ls * 0.7, ls, widget["muted"],
                                widget["label_font"], "center", slot)  # fmt: skip

    def _list_cells(self, layer: Image.Image, widget: dict[str, Any], rows: list[tuple]) -> None:
        """A grid of tiles, each filled as high as its value (or just its number)."""
        w, h, fs = widget["w"], widget["h"], max(6, widget["font_size"])
        n = len(rows)
        gap = max(2, round(min(w, h) * 0.03))
        best = (0.0, 1, n)
        for columns in range(1, n + 1) if widget["columns"] <= 0 else [widget["columns"]]:
            lines = -(-n // columns)
            cell_w = (w - (columns - 1) * gap) / columns
            cell_h = (h - (lines - 1) * gap) / lines
            size = min(cell_w, cell_h * 1.6)  # wide tiles read better than tall ones
            if size > best[0]:
                best = (size, columns, lines)
        _size, columns, lines = best
        cell_w = (w - (columns - 1) * gap) / columns
        cell_h = (h - (lines - 1) * gap) / lines
        s = SUPERSAMPLE
        track = self.color(widget["background"])
        accent = self.color(widget["color2"])
        names = [r[0] for r in rows]
        if widget["levels"]:
            names = lists.short_names(names)
        for i, (_name, _detail, text, frac, rule) in enumerate(rows):
            row, col = divmod(i, columns)
            x0, y0 = col * (cell_w + gap), row * (cell_h + gap)
            cw, ch = round(x0 + cell_w) - round(x0), round(y0 + cell_h) - round(y0)
            radius = min(cw, ch) * 0.16
            tile, draw = self._canvas(cw, ch)
            box = [0, 0, cw * s - 1, ch * s - 1]
            if track:
                draw.rounded_rectangle(box, radius=radius * s, fill=track)
            if widget["levels"] and frac > 0:
                fill = self.color(rule) if rule else accent
                mask = Image.new("L", tile.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius * s, fill=255)
                cut = Image.new("L", tile.size, 0)
                ImageDraw.Draw(cut).rectangle([0, (1 - frac) * ch * s, cw * s, ch * s], fill=170)
                mask = ImageChops.multiply(mask, cut)
                tile.paste(Image.new("RGBA", tile.size, fill), (0, 0), mask)
            tile = self._down(tile, cw, ch)
            self._paste(layer, tile, round(x0), round(y0))
            vs = min(fs, ch * 0.34, cw / max(2.2, len(text) * 0.62))
            ls = max(6, min(vs * 0.62, ch * 0.2))
            with_name = ch >= vs * 0.7 + ls * 0.7 + ls * 1.6 and cw >= ls * 2 and ls >= 7.5
            block = 0.7 * vs + (ls * 0.7 + ls * 0.7 if with_name else 0)
            cap = y0 + (ch - block) / 2
            cx = x0 + cw / 2
            if with_name:
                self._list_text(layer, self._list_name(widget, names[i]), cx, cap, ls,
                                widget["muted"], widget["label_font"], "center", cw * 0.9,
                                label=widget["uppercase"])  # fmt: skip
                cap += ls * 0.7 + ls * 0.7
            self._list_text(layer, text, cx, cap, vs, rule or widget["color"], widget["font"],
                            "center", cw * 0.9)  # fmt: skip

    # -- shapes ------------------------------------------------------------

    def _canvas(self, w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        layer = Image.new("RGBA", (max(1, w * SUPERSAMPLE), max(1, h * SUPERSAMPLE)), (0, 0, 0, 0))
        return layer, ImageDraw.Draw(layer)

    @staticmethod
    def _down(layer: Image.Image, w: int, h: int, s: int = SUPERSAMPLE) -> Image.Image:
        """Reduce a layer drawn ``s`` times larger to its size on the panel.

        An exact 3x reduction averages each 3x3 block (with premultiplied
        alpha, so edges do not darken): classic supersampling, four times
        faster than Lanczos and visually the same (mean difference below a
        quarter of a level, only on antialiased edges).
        """
        w, h = max(1, w), max(1, h)
        if layer.size == (w * s, h * s):
            if layer.mode == "RGBA":
                return layer.convert("RGBa").reduce(s).convert("RGBA")
            return layer.reduce(s)  # masks ("L")
        return layer.resize((w, h), Image.Resampling.LANCZOS)

    def _fill_shape(
        self,
        layer: Image.Image,
        box: list[float],
        radius: float,
        a: RGBA,
        b: RGBA | None,
        horizontal: bool,
    ) -> None:
        """Rounded rectangle filled with a colour or a gradient running across the layer."""
        if b is None:
            ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=a)
            return
        mask = Image.new("L", layer.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
        layer.paste(_gradient(layer.size, a, b, horizontal), (0, 0), mask)

    def _draw_rect(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        return self._cached(widget, None, lambda: self._rect_piece(widget))

    def _rect_piece(self, widget: dict[str, Any]) -> Piece:
        """A rect as it is drawn flat (the panel light's frames start from it)."""
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE
        card = self._card(widget, x, y, w, h)
        if card is not None:
            return card
        layer, draw = self._canvas(w, h)
        radius = min(widget["radius"], w // 2, h // 2) * s
        box = [0, 0, w * s - 1, h * s - 1]
        if widget["color"]:
            self._fill_shape(
                layer,
                box,
                radius,
                self.color(widget["color"]),
                self.color(widget["color2"]),
                widget["gradient"] == "horizontal",
            )
        if widget["outline"]:
            draw.rounded_rectangle(
                box,
                radius=radius,
                outline=self.color(widget["outline"]),
                width=widget["outline_width"] * s,
            )
        backdrop = None
        if widget["backdrop_blur"]:
            mask = Image.new("L", layer.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
            backdrop = self._down(mask, w, h)
        return Piece(self._down(layer, w, h), x, y, [x, y, w, h], backdrop, widget["backdrop_blur"])

    def _card(self, widget: dict[str, Any], x: int, y: int, w: int, h: int) -> Piece | None:
        """A rect drawn at its size with only its round corners supersampled:
        the pixels of drawing it three times larger, at a fraction of the
        cost (a 1920x480 backdrop took 100 ms). None where the corners meet (a
        pill, a circle) or the outline fills it: those are drawn whole."""
        r = min(widget["radius"], w // 2, h // 2)
        ring = widget["outline_width"] if widget["outline"] else 0
        if min(w, h) < 2 * (r + 2) + 2 or 2 * ring >= min(w, h):
            return None
        shape, corners, c = _card_corners(r, ring)
        small, _ = shape.size
        full = Image.new("L", (w, h), 255)
        edge = Image.new("L", (w, h), 0) if ring else None
        if edge is not None:
            draw = ImageDraw.Draw(edge)
            for band in ([0, 0, w - 1, ring - 1], [0, h - ring, w - 1, h - 1],
                         [0, 0, ring - 1, h - 1], [w - ring, 0, w - 1, h - 1]):  # fmt: skip
                draw.rectangle(band, fill=255)
        for (sx, sy), (bx, by) in zip(
            ((0, 0), (small - c, 0), (0, small - c), (small - c, small - c)),
            ((0, 0), (w - c, 0), (0, h - c), (w - c, h - c)),
            strict=True,
        ):
            full.paste(shape.crop((sx, sy, sx + c, sy + c)), (bx, by))
            if edge is not None:
                edge.paste(corners.crop((sx, sy, sx + c, sy + c)), (bx, by))
        layer = None
        if widget["color"]:
            a, b = self.color(widget["color"]), self.color(widget["color2"])
            if b is None:
                layer = Image.new("RGBA", (w, h), a)
            else:
                layer = _gradient_down((w, h), a, b, widget["gradient"] == "horizontal").copy()
            inside = full if edge is None else ImageChops.subtract(full, edge)
            alpha = layer.getchannel("A")
            layer.putalpha(
                inside if alpha.getextrema() == (255, 255) else ImageChops.multiply(alpha, inside)
            )
        if edge is not None:
            color = self.color(widget["outline"])
            line = Image.new("RGBA", (w, h), color)
            line.putalpha(
                edge
                if color[3] == 255
                else ImageChops.multiply(edge, Image.new("L", (w, h), color[3]))
            )
            layer = (
                line
                if layer is None
                else ImageChops.add(layer.convert("RGBa"), line.convert("RGBa")).convert("RGBA")
            )
        if layer is None:
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        backdrop = full if widget["backdrop_blur"] else None
        return Piece(layer, x, y, [x, y, w, h], backdrop, widget["backdrop_blur"])

    def _draw_bar(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        target = _number(snapshot.value(widget["sensor"]))
        value = self._eased(widget, target, now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        frac = _steps(frac, w if widget["direction"] in ("right", "left") else h)
        paint = self._rule_paint(widget, value, now)
        key = (frac, paint)
        return self._cached(widget, key, lambda: self._bar_piece(widget, frac, paint, x, y, w, h))

    def _bar_piece(
        self,
        widget: dict[str, Any],
        frac: float,
        paint: tuple[RGBA, RGBA | None],
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> Piece:
        """``paint``: the colour where the bar starts, and where a full bar
        ends (None: one colour)."""
        s = SUPERSAMPLE
        layer, draw = self._canvas(w, h)
        W, H = w * s, h * s
        radius = min(widget["radius"], w // 2, h // 2) * s
        direction = widget["direction"]
        horizontal = direction in ("right", "left")
        a, b = paint
        track = self.color(widget["background"])

        def span(p0: float, p1: float) -> list[float]:
            """Rectangle covering the part p0..p1 (0 = start, 1 = end) of the bar."""
            if direction == "right":
                box = [p0 * W, 0, p1 * W - 1, H - 1]
            elif direction == "left":
                box = [(1 - p1) * W, 0, (1 - p0) * W - 1, H - 1]
            elif direction == "down":
                box = [0, p0 * H, W - 1, p1 * H - 1]
            else:  # up
                box = [0, (1 - p1) * H, W - 1, (1 - p0) * H - 1]
            # Even the smallest value stays visible as a sliver.
            box[2], box[3] = max(box[2], box[0]), max(box[3], box[1])
            return box

        n = widget["segments"]
        if n > 0:
            gap = widget["segment_gap"] * s / (W if horizontal else H)
            cell = (1 - gap * (n - 1)) / n
            colors = self._gradient_colors(a, b, n)
            for i in range(n):
                box = span(i * (cell + gap), i * (cell + gap) + cell)
                seg_r = min(radius, (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
                amount = min(1.0, max(0.0, frac * n - i))  # the last lit segment may be partial
                if track and amount < 1:
                    draw.rounded_rectangle(box, radius=seg_r, fill=track)
                if amount > 0:
                    color = colors[i]
                    draw.rounded_rectangle(
                        box, radius=seg_r, fill=(*color[:3], int(color[3] * amount))
                    )
        else:
            if track:
                draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=radius, fill=track)
            if frac > 0:
                box = span(0, frac)
                fill_r = min(radius, (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
                if b is None:
                    draw.rounded_rectangle(box, radius=fill_r, fill=a)
                else:
                    # The gradient spans the whole track, so a colour always means the same value.
                    first, last = (b, a) if direction in ("left", "up") else (a, b)
                    self._fill_shape(layer, box, fill_r, first, last, horizontal)
        return Piece(self._down(layer, w, h), x, y, [x, y, w, h])

    def _draw_mark(self, widget: dict[str, Any], snapshot: Snapshot) -> Piece | None:
        """A bar's mark: a thin line across the bar where the reading ``mark`` stands on the bar's
        own scale, reaching a little beyond it on both sides (the average of earlier rounds, a
        limit). A reading that is not there draws none."""
        value = _number(snapshot.value(widget["mark"]))
        if value is None:
            return None
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        direction = widget["direction"]
        horizontal = direction in ("right", "left")
        length, across = (w, h) if horizontal else (h, w)
        thick = max(1, min(widget["mark_width"], length))
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        along = int((length - thick) * frac)  # from the bar's start, in whole pixels
        if direction in ("left", "up"):  # these start at the far end
            along = length - thick - along
        reach = round(across * 0.22)
        color = self.color(widget["mark_color"])
        at = (x + along, y - reach) if horizontal else (x - reach, y + along)
        size = (thick, h + 2 * reach) if horizontal else (w + 2 * reach, thick)
        marked = {"id": f"{widget['id']}\0mark", "opacity": widget.get("opacity", 1.0)}
        return self._cached(
            marked, (at, size, color), lambda: Piece(Image.new("RGBA", size, color), *at)
        )

    @staticmethod
    def _gradient_colors(a: RGBA, b: RGBA | None, n: int) -> list[RGBA]:
        if b is None:
            return [a] * n
        return [_lerp(a, b, i / max(1, n - 1)) for i in range(n)]

    def _draw_gauge(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        target = _number(snapshot.value(widget["sensor"]))
        value = self._eased(widget, target, now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        sweep = abs(widget["end_angle"] - widget["start_angle"])
        frac = _steps(frac, math.radians(sweep) * max(widget["w"], widget["h"]) / 2)
        paint = self._rule_paint(widget, value, now)
        return self._cached(widget, (frac, paint), lambda: self._gauge_piece(widget, frac, paint))

    def _gauge_piece(
        self, widget: dict[str, Any], frac: float, paint: tuple[RGBA, RGBA | None]
    ) -> Piece:
        """Ring gauge: a cached static part (track, ticks) plus the value arc.

        The arc is one antialiased mask filled from a cached conic gradient, so
        a gliding ring costs a few milliseconds per frame, not tens.
        """
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        layer = self._gauge_static(widget).copy()
        if frac > 0:
            a, b = paint
            mask = self._gauge_mask(widget, frac)
            if b is None:
                arc = Image.new("RGBA", (w, h), a)
            else:
                arc = self._gauge_colors(widget, a, b).copy()
            alpha = arc.getchannel("A")
            arc.putalpha(
                mask if alpha.getextrema() == (255, 255) else ImageChops.multiply(alpha, mask)
            )
            layer.alpha_composite(arc)
        return Piece(layer, x, y, [x, y, w, h])

    def _gauge_geometry(self, widget: dict[str, Any], scale: int) -> dict[str, Any]:
        W, H = widget["w"] * scale, widget["h"] * scale
        th = max(1, widget["thickness"]) * scale
        return {
            "box": [0, 0, W - 1, H - 1],
            "c": ((W - 1) / 2, (H - 1) / 2),
            "r": ((W - th) / 2, (H - th) / 2),  # centre line of the ring
            "th": th,
        }

    def _gauge_static(self, widget: dict[str, Any]) -> Image.Image:
        key = ("gauge-static", json.dumps(widget, sort_keys=True))
        hit = self._gauge_parts.get(key)
        if hit is not None:
            return hit
        s = SUPERSAMPLE
        layer, draw = self._canvas(widget["w"], widget["h"])
        g = self._gauge_geometry(widget, s)
        start, end = widget["start_angle"], widget["end_angle"]
        sweep = end - start

        def point(angle: float, inset: float = 0.0) -> tuple[float, float]:
            rad = math.radians(angle)
            return (
                g["c"][0] + (g["r"][0] - inset) * math.cos(rad),
                g["c"][1] + (g["r"][1] - inset) * math.sin(rad),
            )

        track = self.color(widget["background"])
        th = g["th"]
        if track:
            draw.arc(g["box"], start, end, fill=track, width=th)
            if widget["cap"] == "round":
                for angle in (start, end):
                    px, py = point(angle)
                    draw.ellipse([px - th / 2, py - th / 2, px + th / 2, py + th / 2], fill=track)
        ticks = widget["ticks"]
        if ticks > 0:
            tick = self.color(widget["tick_color"] or widget["background"] or widget["color"])
            length = th * 0.6
            for i in range(ticks + 1):
                angle = start + sweep * i / ticks
                p0 = point(angle, th / 2 + 2 * s)
                p1 = point(angle, th / 2 + 2 * s + length)
                draw.line([p0, p1], fill=tick, width=max(1, s))
        image = self._down(layer, widget["w"], widget["h"])
        self._gauge_parts[key] = image
        return image

    def _gauge_mask(self, widget: dict[str, Any], frac: float) -> Image.Image:
        s = SUPERSAMPLE
        g = self._gauge_geometry(widget, s)
        start, sweep = widget["start_angle"], widget["end_angle"] - widget["start_angle"]
        stop = start + sweep * frac
        mask = Image.new("L", (widget["w"] * s, widget["h"] * s), 0)
        draw = ImageDraw.Draw(mask)
        th = g["th"]
        draw.arc(g["box"], start, stop, fill=255, width=th)
        if widget["cap"] == "round":
            for angle in (start, stop):
                rad = math.radians(angle)
                px = g["c"][0] + g["r"][0] * math.cos(rad)
                py = g["c"][1] + g["r"][1] * math.sin(rad)
                draw.ellipse([px - th / 2, py - th / 2, px + th / 2, py + th / 2], fill=255)
        return mask.reduce(s)

    def _gauge_colors(self, widget: dict[str, Any], a: RGBA, b: RGBA) -> Image.Image:
        """Colour along the scale: angle ``start`` is ``a``, ``end`` is ``b``."""
        key = (
            "gauge-colors",
            widget["id"],
            widget["w"],
            widget["h"],
            a,
            b,
            widget["start_angle"],
            widget["end_angle"],
        )
        hit = self._gauge_parts.get(key)
        if hit is not None:
            return hit
        w, h = widget["w"], widget["h"]
        start, end = widget["start_angle"], widget["end_angle"]
        sweep = end - start
        field = Image.new("RGBA", (w, h), a)
        draw = ImageDraw.Draw(field)
        box = [-w, -h, 2 * w, 2 * h]  # wedges reach past the corners
        steps = max(2, int(abs(sweep) / 1.5))
        for i in range(steps):
            a0 = start + sweep * i / steps
            a1 = start + sweep * (i + 1) / steps
            draw.pieslice(box, min(a0, a1), max(a0, a1) + 0.8, fill=_lerp(a, b, i / steps))
        # Past the end (the round cap) the colour stays the end colour; before
        # the start it is the start colour the field was filled with.
        tail = (end, end + 30) if sweep > 0 else (end - 30, end)
        draw.pieslice(box, *tail, fill=b)
        self._gauge_parts[key] = field
        return field

    DOTS = 256  # end dots kept (a dot moves in quarter pixels: a handful of looks at a time)

    def _draw_end_dot(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        """A graph's end dot: a dot of ``end_dot`` px radius in the line's colour, with the
        widget's glow around it, where the line ends: at the right edge, at the newest value.
        Drawn in quarter pixels, as SPUR II draws it, so it glides with the value. None where
        the line has no end (too few points, no value)."""
        radius = float(widget["end_dot"])
        x, y, w = widget["x"], widget["y"], widget["w"]
        key = widget["sensor"]
        if self.continuous:  # the curve is drawn from the gliding value: so is its end
            value, scale = self.motion.value(key, now), self.STRIP_SCALE
            strips = self._strips.get(widget["id"])
            strip = strips.ready if strips is not None else None
            bounds = strip.bounds if strip is not None else None
        else:
            values = _gaps(snapshot.history.get(key, [])[-max(2, widget["history"]) :])
            value, scale = (values[-1] if len(values) > 1 else None), SUPERSAMPLE
            known = [v for v in values if v is not None]
            bounds = self._graph_bounds(widget, known) if known and len(values) > 1 else None
        if value is None or bounds is None:
            return None
        first, last = self._range_rows(widget, scale)
        frac = _fraction(value, *bounds, widget.get("scale", "linear"))
        row = first + (last - first) * (1 - frac)
        if widget.get("direction") == "down":  # it hangs from the top edge
            row = first + last - row
        # the curve's end in pixels (the last point is a quarter of a pixel inside the edge)
        cx, cy = x + w - 0.5 / scale, y + (row + 0.5) / scale
        left, top = cx - radius - 1.0, cy - radius - 1.0
        ix, iy = math.floor(left), math.floor(top)
        px, py = int((left - ix) * 4) % 4, int((top - iy) * 4) % 4
        color = self.color(widget["color"])
        glow, glow_radius = widget.get("glow", 0.0), widget.get("glow_radius", 10)
        look = (radius, color, px, py, glow, glow_radius)
        if look not in self._dots:
            if len(self._dots) >= self.DOTS:
                self._dots.clear()
            size = math.ceil(2 * radius + 3)
            big = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
            mx, my = radius + 1.0 + px / 4, radius + 1.0 + py / 4
            ImageDraw.Draw(big).ellipse(
                [(mx - radius) * 4, (my - radius) * 4, (mx + radius) * 4, (my + radius) * 4],
                fill=color,
            )
            dot = self._down(big, size, size, 4)
            halo, pad = self._halo({"glow": glow, "glow_radius": glow_radius}, dot)
            if halo is not None:
                halo.alpha_composite(dot, (pad, pad))
                dot = halo
            self._dots[look] = (dot, pad)
        dot, pad = self._dots[look]
        plain = {"id": f"{widget['id']}\0dot", "opacity": widget.get("opacity", 1.0)}
        return self._cached(plain, (ix, iy, look), lambda: Piece(dot, ix - pad, iy - pad))

    def _draw_graph(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        if self.continuous:
            return self._timeline(widget, snapshot, now)
        slots = max(2, widget["history"])
        values = _gaps(snapshot.history.get(widget["sensor"], [])[-slots:])
        key = (len(values), tuple(values[-3:]), values[0] if values else None)
        return self._cached(widget, key, lambda: self._graph_piece(widget, values, slots))

    def _graph_piece(self, widget: dict[str, Any], values: list[float], slots: int) -> Piece:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        layer = self._graph_layer(widget, values, slots, w, slots - len(values))
        return Piece(self._down(layer, w, h), x, y, [x, y, w, h])

    # -- graphs in video mode ------------------------------------------------
    #
    # Every frame reaches the panel, so a graph draws the gliding value of its
    # sensor 25 times a second (the motion's history), and its newest point is
    # the value the numbers show now. Drawing that every frame costs too much:
    # the curve goes onto a strip in the helper thread, and each frame shows a
    # window into it, in half-pixel steps. Until the next reading the glide is
    # known, so a strip reaches that far ahead; a reading that comes before
    # the next strip is drawn over the end of the window until it is in.

    STRIP_SCALE = 2  # drawn twice as large: half-pixel steps, as SPUR II's curves
    STRIP_AHEAD_S = 0.5  # a strip reaches this far past the next reading
    STRIP_EVERY_S = 0.25  # a graph asks for a new strip at most this often

    def _graph_seconds(self, widget: dict[str, Any]) -> float:
        """The time a graph shows in video mode: ``seconds``, a pixel per point
        for ``per_frame`` (older themes), else ``history`` readings' worth."""
        seconds = float(widget.get("seconds") or 0)
        if seconds <= 0 and widget.get("per_frame"):
            seconds = (widget["w"] - 1) / RATE
        if seconds <= 0:
            seconds = (max(2, widget["history"]) - 1) * self.sample[1]
        return min(max(seconds, 2 / RATE), (MAX_POINTS - 4 * RATE) / RATE)

    def _reach(self, widget: dict[str, Any]) -> int:
        """How far the curve's glow and shadow reach, in pixels."""
        glow = 3 * widget.get("glow_radius", 10) if widget.get("glow") else 0
        shadow = 0
        if widget.get("shadow"):
            shadow = 3 * widget.get("shadow_blur", 6) + abs(widget.get("shadow_offset", 3))
        return max(glow, shadow) + widget["line_width"] + 2

    def _timeline(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        wid, key, w, h = widget["id"], widget["sensor"], widget["w"], widget["h"]
        s = self.STRIP_SCALE
        glide = self._glide_s()
        self.motion.glide(key, motion_number(snapshot.value(key)), now, glide, self._lead())
        span = self._graph_seconds(widget) * RATE  # points across the window
        per_point = (w * s - 1) / span  # their distance, in pixels at ``s``
        every = max(1, int(s / per_point))  # long windows: about a point per pixel
        margin = math.ceil(s * (self._pad(widget) + self._reach(widget) + 1) / per_point)
        history = self.motion.history(
            key, math.ceil(span) + margin + 2 * every, now,
            snapshot.history.get(key, []), self.sample[0], self.sample[1], glide,
        )  # fmt: skip
        self.moving = True
        strips = self._strips.get(wid)
        if strips is None or strips.per_point != per_point:  # new, or another window
            strips = self._strips[wid] = _Strips(per_point)
        strips.collect()
        # The window's left edge at ``s``, point k sitting at k * per_point on
        # every strip: the newest point at the right edge, now.
        left = round(now * RATE * per_point - (w * s - 1))
        strip = strips.ready
        if strip is None or not strip.covers(left, w, s):  # the first, or after a long pause
            strip = strips.ready = self._strip_job(widget, history, now, per_point, every, margin)()
        valid = self._valid_until(strip, key)
        due = valid < strip.until or now >= strip.until - self.STRIP_EVERY_S
        if due and strips.building is None and now - strips.asked >= self.STRIP_EVERY_S:
            strips.asked = now
            job = self._strip_job(widget, history, now, per_point, every, margin)
            if self.background_builds:
                if self._builder is None:
                    self._builder = _Builder()
                strips.building = self._builder.submit(job)
            else:
                strip = strips.ready = job()
                valid = strip.until
        offset, phase = divmod(left - strip.origin, s)
        pad = strip.pad
        # A reading the strip did not know: draw the end again once it shows,
        # at the newest point or in the glow just past it (a big change).
        beyond = now + s * (pad + self._reach(widget)) / (per_point * RATE)
        patch = valid < now and (
            self._apart(widget, strip, now) >= 0.4 or self._apart(widget, strip, beyond) >= 4
        )
        content = ("timeline", id(strip), offset, phase)
        hit = self._cache.get(wid)
        if not patch and hit is not None and hit[0] == content:
            return hit[1]
        layer = strip.phases[phase].crop((offset, 0, offset + w + 2 * pad, h + 2 * pad))
        if strip.halo is not None:  # beside the window only the glow: the curve ends at its edges
            for x0 in (0, w + pad):
                side = strip.halo.crop((offset + x0, 0, offset + x0 + pad, h + 2 * pad))
                layer.paste(side, (x0, 0))
        if patch:
            self._patch(widget, layer, strip, history, valid, left)
        x, y = widget["x"], widget["y"]
        piece = Piece(layer, x - pad, y - pad, [x, y, w, h])
        self._cache[wid] = (None if patch else content, piece)
        return piece

    def _pad(self, widget: dict[str, Any]) -> int:
        """The room :meth:`_halo` leaves around a layer for its effects."""
        pad = 0
        if widget.get("glow"):
            pad = max(pad, widget.get("glow_radius", 10) * 2)
        if widget.get("shadow"):
            pad = max(pad, widget.get("shadow_blur", 6) * 2 + abs(widget.get("shadow_offset", 3)))
        return pad

    def _apart(self, widget: dict[str, Any], strip: _Strip, t: float) -> float:
        """How many pixels the strip's curve is off the sensor's at ``t``. A
        glide starts gently: a new reading shows only after a frame or two,
        often after the next strip is in."""
        live = self.motion.value(widget["sensor"], t)
        drawn = Spring()
        drawn.pieces = list(strip.pieces)
        old = drawn.at(t)
        if live is None or old is None:
            return 0.0 if live is None and old is None else math.inf
        scale = widget.get("scale", "linear")
        apart = _fraction(live, *strip.bounds, scale) - _fraction(old, *strip.bounds, scale)
        return abs(apart) * (widget["h"] - 2 * widget["line_width"])

    def _valid_until(self, strip: _Strip, key: str) -> float:
        """Until when the strip's curve is the sensor's: the end of what it
        drew ahead, or when the value took a course the strip did not know."""
        spring = self.motion.springs.get(key)
        if spring is None:
            return strip.taken
        if spring.made == strip.made:
            return strip.until
        later = [piece for piece in spring.pieces if piece[0] >= strip.made]
        if not later or later[0][0] != strip.made:  # gone already: right until it was taken
            return strip.taken
        return later[0][1]

    def _values(
        self, history: History, key: str, first: int, last: int, every: int
    ) -> tuple[list[float | None], int]:
        """The values of the points ``first`` .. ``last`` that are drawn (every
        ``every``-th): recorded ones from the history, later ones ahead of time
        from the spring. Returns them and the first point's number."""
        first -= first % every
        values = history.since(first)[: max(0, history.last - first + 1)][::every]
        spring = self.motion.springs.get(key)
        future = Spring()
        future.pieces = list(spring.pieces) if spring is not None else []
        start = first + len(values) * every
        values += [future.at(k / RATE) for k in range(start, last + 1, every)]
        return values, first

    def _strip_job(
        self, widget: dict[str, Any], history: History, now: float, per_point: float,
        every: int, margin: int,
    ) -> Callable[[], _Strip]:  # fmt: skip
        """A strip of the curve to draw (in the helper thread): its values are
        taken now, on the render thread; the drawing happens when it runs."""
        key = widget["sensor"]
        last = grid(now)
        ahead = math.ceil((self.sample[1] + self.STRIP_AHEAD_S) * RATE)
        span = (widget["w"] * self.STRIP_SCALE - 1) / per_point
        values, first = self._values(
            history, key, last - math.ceil(span) - margin - every, last + ahead + margin, every
        )
        spring = self.motion.springs.get(key)
        made = spring.made if spring is not None else 0
        pieces = tuple(spring.pieces) if spring is not None else ()
        until = (last + ahead) / RATE

        def job() -> _Strip:
            return self._draw_strip(
                widget, values, first, per_point, every, _Strip(made, now, until, pieces)
            )

        return job

    def _draw_strip(
        self, widget: dict[str, Any], values: list[float | None], first: int, per_point: float,
        every: int, strip: _Strip,
    ) -> _Strip:  # fmt: skip
        """Draw the curve through ``values`` (point ``first`` on, every
        ``every``-th) onto ``strip``: an image per half-pixel step, glow and all."""
        s, h = self.STRIP_SCALE, widget["h"]
        widget = self._dense(widget, every * per_point)
        numbers = [v for v in values if v is not None]
        bounds = self._graph_bounds(widget, numbers) if numbers else (0.0, 1.0)
        top = self._fade_top(widget, numbers, bounds, s)
        origin = math.floor(first * per_point) - 1  # the strip's left edge, at ``s``
        last = first + (len(values) - 1) * every
        width = math.ceil((last * per_point - origin + 1) / s) + 1
        layer = self._graph_layer(
            widget, values, 0, width + 1, first // every, scale=s, step=every * per_point,
            origin=origin, bounds=bounds, top=top,
        )  # fmt: skip
        opacity = widget.get("opacity", 1.0)
        lines = [
            _scale_alpha(self._down(layer.crop((p, 0, p + s * width, s * h)), width, h, s), opacity)
            for p in range(s)
        ]
        halo, pad = self._halo(widget, lines[0], exact=True)
        phases = []
        for line in lines:
            if halo is None:
                phases.append(line)
                continue
            out = halo.copy()
            out.alpha_composite(line, (pad, pad))
            phases.append(out)
        strip.per_point, strip.every, strip.origin, strip.width = per_point, every, origin, width
        strip.bounds, strip.top, strip.pad, strip.phases = bounds, top, pad, phases
        strip.halo = halo
        return strip

    def _patch(
        self, widget: dict[str, Any], layer: Image.Image, strip: _Strip, history: History,
        valid: float, left: int,
    ) -> None:  # fmt: skip
        """Draw the end of the window ``layer`` again from where the strip no
        longer holds (a reading it did not know), as the next strip will."""
        s, w, h, pad = self.STRIP_SCALE, widget["w"], widget["h"], strip.pad
        widget = self._dense(widget, strip.every * strip.per_point)
        reach = self._reach(widget)
        # Window columns from ``start`` on change (and the glow that reaches
        # them); from ``begin`` on, the patch draws what that glow sees.
        changed = (valid * RATE * strip.per_point - left) / s
        start = max(-pad, math.floor(changed) - reach)
        begin, end = start - reach, w + pad + reach
        x0 = left + s * begin  # the patch's left edge, at ``s``
        first = math.floor(x0 / strip.per_point) - strip.every
        last = math.ceil((x0 + s * (end - begin)) / strip.per_point) + strip.every
        values, first = self._values(history, widget["sensor"], first, last, strip.every)
        step = strip.every * strip.per_point
        points = [((first // strip.every + i) * step - x0, v) for i, v in enumerate(values)]
        part = self._curve(widget, points, end - begin, s, strip.bounds, strip.top)
        line = _scale_alpha(self._down(part, end - begin, h, s), widget.get("opacity", 1.0))
        halo, _ = self._halo(widget, line, exact=True)
        if halo is not None:  # the curve inside the window, its glow also beside it
            inside = line.crop((max(0, -begin), 0, w - begin, h))
            halo.alpha_composite(inside, (pad + max(0, -begin), pad))
            line = halo
        cut = start - begin + pad  # the patch's column of window column ``start``
        layer.paste(line.crop((cut, 0, cut + w + pad - start, line.height)), (start + pad, 0))

    def _dense(self, widget: dict[str, Any], step: float) -> dict[str, Any]:
        """Points this close (``step`` pixels at the strip's scale) are a curve
        already: no spline through them, and no round joints (a slice drawn in
        Python for every point, holding up the render thread)."""
        if step < 3 * self.STRIP_SCALE:
            return {**widget, "smooth": False, "\0joints": False}
        return widget

    def _graph_bounds(self, widget: dict[str, Any], numbers: list[float]) -> tuple[float, float]:
        lo = widget["min"] if widget["min"] is not None else min(numbers)
        hi = widget["max"] if widget["max"] is not None else max(numbers)
        return (lo, lo + 1) if hi == lo else (lo, hi)

    @staticmethod
    def _range_rows(widget: dict[str, Any], s: int) -> tuple[float, float]:
        """The rows (at ``s``) a graph's top and bottom value lie on: a line's
        width in from its edges, so the line stays inside, or without
        ``inset`` the edges themselves, as SPUR II's charts."""
        if widget.get("inset", True):
            pad = widget["line_width"] * s
            return pad, widget["h"] * s - 1 - pad
        return 0.0, (widget["h"] - 1) * s

    def _fade_top(
        self, widget: dict[str, Any], numbers: list[float], bounds: tuple[float, float], s: int
    ) -> int:
        """Where a strip's fill starts to fade: its highest point."""
        if not numbers:
            return 0
        first, last = self._range_rows(widget, s)
        scale = widget.get("scale", "linear")
        highest = max(_fraction(v, *bounds, scale) for v in numbers)
        return int(first + (last - first) * (1 - highest))

    def _graph_layer(
        self, widget: dict[str, Any], values: list[float | None], slots: int, width: int,
        first: int, *, scale: int = SUPERSAMPLE, step: float | None = None, origin: int = 0,
        bounds: tuple[float, float] | None = None, top: int | None = None,
    ) -> Image.Image:  # fmt: skip
        """The graph at ``scale``, on a canvas ``width`` px wide: value ``i`` sits
        at sample position ``first + i`` of the widget's ``slots`` (or ``step``
        pixels apart at ``scale``, the canvas starting at ``origin``); None
        leaves a gap."""
        if step is None:
            step = (widget["w"] * scale - 1) / (slots - 1)
        points = [((first + i) * step - origin, v) for i, v in enumerate(values)]
        return self._curve(widget, points, width, scale, bounds, top)

    def _curve(
        self, widget: dict[str, Any], points: list[tuple[float, float | None]], width: int,
        scale: int, bounds: tuple[float, float] | None = None, top: int | None = None,
    ) -> Image.Image:  # fmt: skip
        """Background, grid and the curve through ``points`` (x in pixels at
        ``scale``; a value None breaks the curve) on a canvas ``width`` px wide.
        ``bounds`` is the value range (default: the widget's, else the
        points'), ``top`` where the fill's fade starts (default: its peak)."""
        s = scale
        W, H = max(1, width * s), max(1, widget["h"] * s)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if widget["background"]:
            draw.rectangle([0, 0, W - 1, H - 1], fill=self.color(widget["background"]))
        if widget["grid"] > 0 and widget["grid_color"]:
            grid_color = self.color(widget["grid_color"])
            for i in range(1, widget["grid"] + 1):
                gy = round(H * i / (widget["grid"] + 1))
                draw.line([(0, gy), (W - 1, gy)], fill=grid_color, width=max(1, s // 2))
        numbers = [v for _, v in points if v is not None]
        if len(numbers) < 2:
            return layer
        lo, hi = bounds or self._graph_bounds(widget, numbers)
        first, last = self._range_rows(widget, s)
        scale_kind = widget.get("scale", "linear")
        down = widget.get("direction") == "down"  # hangs from the top edge
        runs: list[list[tuple[float, float]]] = [[]]
        for px, v in points:
            if v is None:
                if runs[-1]:
                    runs.append([])
                continue
            py = first + (last - first) * (1 - _fraction(v, lo, hi, scale_kind))
            runs[-1].append((px, first + last - py if down else py))
        runs = [run for run in runs if len(run) >= 2]
        if widget["smooth"]:
            runs = [
                [(px, min(last, max(first, py))) for px, py in _catmull_rom(run)] for run in runs
            ]
        color = self.color(widget["color"])
        if widget["fill"] and runs:
            area = Image.new("L", layer.size, 0)
            base = 0 if down else H - 1 if widget.get("inset", True) else last
            for run in runs:
                polygon = [*run, (run[-1][0], base), (run[0][0], base)]
                ImageDraw.Draw(area).polygon(polygon, fill=255)
            solid = min(1.0, max(0.0, widget.get("fill_alpha", 0)))
            if widget["fill_fade"]:
                peak = round(255 * solid) if solid else 115
                if top is None:  # the peak, counted from where the fill starts
                    ys = [py for run in runs for _, py in run]
                    top = int(first + last - max(ys) if down else min(ys))
                rows = [
                    int(peak * min(1.0, max(0.0, 1 - (row - top) / max(1, H - top))))
                    for row in range(H)
                ]
                fade = Image.new("L", (1, H), 0)
                fade.putdata(rows[::-1] if down else rows)
                alpha = ImageChops.multiply(area, fade.resize(layer.size))
            else:
                flat = round(255 * solid) if solid else 64
                alpha = area.point([v * flat // 255 for v in range(256)])
            tint = Image.new("RGBA", layer.size, (*color[:3], 255))
            if color[3] < 255:
                alpha = ImageChops.multiply(alpha, Image.new("L", layer.size, color[3]))
            tint.putalpha(alpha)
            layer.alpha_composite(tint)
        finish = self._finishes.get(widget["id"])  # a finish may draw the line its own way
        if finish is not None and finish.curve(self, widget, layer, runs, color, s):
            return layer
        joint = "curve" if widget.get("\0joints", True) else None
        for run in runs:
            draw.line(run, fill=color, width=widget["line_width"] * s, joint=joint)
        return layer


class _Strip:
    """A graph's curve drawn ahead (see :meth:`Renderer._timeline`): one image
    per half-pixel step, glow and all, ``pad`` pixels larger on every side."""

    def __init__(self, made: int, taken: float, until: float, pieces: tuple) -> None:
        self.made = made  # the sensor's spring pieces it knew (Spring.made) ...
        self.pieces = pieces  # ... and those pieces: what it drew ahead with
        self.taken = taken  # when its values were taken
        self.until = until  # the curve is right until here, if no reading comes in between
        self.per_point = 0.0  # pixels between two history points, at the strip's scale
        self.every = 1  # every how many history points it draws
        self.origin = 0  # its left edge: point k sits at k * per_point - origin
        self.width = 0  # in pixels, without the pad
        self.bounds = (0.0, 1.0)  # the value range it was drawn with
        self.top = 0  # where its fill's fade starts
        self.pad = 0
        self.phases: list[Image.Image] = []
        self.halo: Image.Image | None = None  # the glow alone, for beside the window

    def covers(self, left: int, w: int, s: int) -> bool:
        """Whether a window of ``w`` pixels from ``left`` (at scale ``s``) lies on it."""
        offset = left - self.origin
        return offset >= 0 and offset // s + w <= self.width


class _Strips:
    """A graph's strip on show and the one being drawn."""

    def __init__(self, per_point: float) -> None:
        self.per_point = per_point
        self.ready: _Strip | None = None
        self.building: Future | None = None
        self.asked = -math.inf

    def collect(self) -> None:
        """Take the strip the helper thread has finished, if it is newer."""
        if self.building is None or not self.building.done():
            return
        try:
            strip = self.building.result()
        except Exception as exc:  # the strip on show stays
            log.warning("graph strip failed: %s", exc)
        else:
            if self.ready is None or strip.taken >= self.ready.taken:
                self.ready = strip
        self.building = None

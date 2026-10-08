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
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from libre_panel import i18n, timezones
from libre_panel.fonts import DEFAULT_FONT, builtin_font_path
from libre_panel.icons import draw_icon, weather_icon_name
from libre_panel.render import lists
from libre_panel.render.countdown import countdown_text
from libre_panel.render.formatting import FormatError, auto_format, safe_format
from libre_panel.sensors.base import Snapshot, want
from libre_panel.theme.model import Theme, ThemeError, resolve_asset
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


class _Glide:
    """A value gliding from reading to reading, as a function of time.

    The same critically damped spring as :meth:`Renderer._eased`, kept piece by
    piece (one per reading), so the value is known at any moment: in the past
    and, until the next reading, in the future.
    """

    def __init__(self) -> None:
        self.pieces: list[tuple[float, float, float, float, float]] = []
        # (from, target, error, speed, omega); omega 0 = the value jumps
        self.sample_no = -1

    def _piece(self, t: float) -> tuple[float, float, float, float, float] | None:
        found = None
        for piece in self.pieces:  # a handful of pieces: no bisect needed
            if piece[0] > t:
                break
            found = piece
        return found

    def state(self, t: float) -> tuple[float, float] | None:
        """(value, speed) at ``t``; None before the first reading."""
        piece = self._piece(t)
        if piece is None:
            return None
        start, target, error, speed, omega = piece
        if omega <= 0:
            return target, 0.0
        dt = t - start
        decay = math.exp(-omega * dt)
        curve = speed + omega * error
        return target + (error + curve * dt) * decay, (speed - omega * curve * dt) * decay

    def at(self, t: float) -> float | None:
        state = self.state(t)
        return None if state is None else state[0]

    def follow(self, t: float, target: float, glide: float) -> None:
        """A new reading at ``t``: glide towards ``target`` for about ``glide`` s."""
        now = self.state(t)
        if now is None or glide <= 0:
            self.pieces.append((t, target, 0.0, 0.0, 0.0))
        else:
            self.pieces.append((t, target, now[0] - target, now[1], 4.74 / glide))

    def forget_before(self, t: float) -> None:
        """Drop pieces that end before ``t`` (the oldest one still in view stays)."""
        while len(self.pieces) > 1 and self.pieces[1][0] <= t:
            self.pieces.pop(0)


def changed_region(
    previous: Image.Image | None, current: Image.Image
) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of pixels that differ, or None if identical."""
    if previous is None or previous.size != current.size or previous.mode != current.mode:
        return (0, 0, current.width, current.height)
    return ImageChops.difference(previous, current).getbbox()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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


def _lerp(a: RGBA, b: RGBA, t: float) -> RGBA:
    t = min(1.0, max(0.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))  # type: ignore[return-value]


def _blur(img: Image.Image, radius: float) -> Image.Image:
    """Colour-true Gaussian blur of an RGBA image (premultiplied alpha)."""
    if radius <= 0:
        return img
    pre = img.convert("RGBa")
    if max(img.size) > 320 and radius >= 4:  # blur at half size: 4x cheaper, same look
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


@lru_cache(maxsize=128)
def _gradient(size: tuple[int, int], a: RGBA, b: RGBA, horizontal: bool) -> Image.Image:
    """Linear gradient from ``a`` to ``b`` (cached: callers must not modify it)."""
    steps = size[0] if horizontal else size[1]
    strip = Image.new("RGBA", (steps, 1) if horizontal else (1, steps))
    strip.putdata([_lerp(a, b, i / max(1, steps - 1)) for i in range(steps)])
    return strip.resize(size, Image.NEAREST)


def _fitted(image: Image.Image, w: int, h: int, fit: str) -> Image.Image:
    """``image`` at w x h: stretched, inside with room left (contain) or filling it (cover)."""
    if fit == "cover":
        return ImageOps.fit(image, (w, h), Image.Resampling.LANCZOS)
    if fit == "contain":
        inner = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out.paste(inner, ((w - inner.width) // 2, (h - inner.height) // 2))
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


def _reads(widgets: list[dict[str, Any]]) -> set[str]:
    """The sensor keys (and patterns) the widgets read."""
    keys: set[str] = set()
    for widget in widgets:
        if widget.get("sensor"):
            keys.add(widget["sensor"])
        for need in (widget.get("needs") or "").split(","):
            need = need.strip().lstrip("!").strip()
            if need:
                keys.add(need)
        if widget["type"] == "weather":
            keys.add(f"weather.{widget['field']}")
        elif widget["type"] == "list":
            keys |= lists.read_keys(widget["items"])
    return keys


class Renderer:
    def __init__(self, theme: Theme, animate: bool = False, preview: bool = False) -> None:
        self.theme = theme
        self.animate = animate
        self.preview = preview  # an editor preview or a picture, not the panel
        self.shown: Image.Image | None = None  # the frame the panel shows (set by the loop)
        self.toast: tuple[Any, float] | None = None  # the toast on show, its age (the loop)
        self.warnings: list[str] = []
        self.moving = False  # True while an animation has not settled yet
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._digit_width: dict[int, float] = {}
        self._images: dict[tuple[str, int, int, str], Image.Image | None] = {}
        self._slide_lists: dict[str, tuple[float, list[str]]] = {}  # widget id -> pictures
        self._gauge_parts: dict[tuple[Any, ...], Image.Image] = {}  # static rings, colour fields
        self._anim: dict[str, tuple[float, float, float]] = {}  # value, speed, time
        # Video mode (the main loop sets these): every frame goes out, so graphs
        # scroll between readings and values glide until the next one.
        self.continuous = False
        self.sample: tuple[float, float] = (0.0, 1.0)  # (when, seconds to the next)
        self._sample_no = 0
        self._strips: dict[str, Any] = {}  # graph id -> ready strip, strip being built
        self._frame_strips: dict[str, Any] = {}  # the same for graphs that move per frame
        self._glides: dict[str, _Glide] = {}  # graph id -> the value its curve follows
        self._cache: dict[str, tuple[Any, Piece]] = {}
        # Modules become plain widgets laid out for their cells; the editor
        # gets one box per module.
        self.widgets, self.module_boxes = expand_modules(theme)
        self._modules = {w["id"]: w for w in theme.widgets if w["type"] == "module"}
        self._keys = {w["id"]: json.dumps(w, sort_keys=True) for w in self.widgets}
        if not preview:  # costly sensors work only while a panel shows them
            want(self, _reads(self.widgets))
        self._background = self._load_background()
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
        self._plugin_types: dict[str, Any] = {}
        self._screen_last: tuple[Any, Piece] | None = None
        self.screen = self._make_screen()

    # -- resources ---------------------------------------------------------

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            log.warning(message)

    def color(self, value: str | None, alpha_scale: float = 1.0) -> RGBA | None:
        if value is None:
            return None
        if value.startswith("@"):
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
                    font = ImageFont.truetype(str(builtin), size)
                elif self.theme.root is not None:
                    font = ImageFont.truetype(str(resolve_asset(self.theme.root, ref)), size)
            except (OSError, ThemeError) as exc:
                self._warn(f"font {ref!r} could not be loaded ({exc}); using the default font")
            if font is None and ref != DEFAULT_FONT:
                font = ImageFont.truetype(str(builtin_font_path(DEFAULT_FONT)), size)
            self._fonts[key] = font or ImageFont.load_default(size)
        return self._fonts[key]

    def _asset_image(self, rel: str, w: int, h: int, fit: str = "stretch") -> Image.Image | None:
        key = (rel, w, h, fit)
        if key not in self._images:
            image = None
            if rel and self.theme.root is not None:
                try:
                    with Image.open(resolve_asset(self.theme.root, rel)) as src:
                        image = ImageOps.exif_transpose(src).convert("RGBA")
                    if w > 0 and h > 0:
                        image = _fitted(image, w, h, fit)
                except (OSError, ThemeError) as exc:
                    self._warn(f"image {rel!r} could not be loaded ({exc})")
            self._images[key] = image
        return self._images[key]

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
        self._sample_no += 1

    def progress(self, now: float, delay: float = 0.0) -> float:
        """How far ``now`` is from the last reading to the next, 0 to 1."""
        at, interval = self.sample
        return min(1.0, max(0.0, (now - at - delay) / interval))

    def _eased(self, widget: dict[str, Any], target: float | None, now: float) -> float | None:
        """Glide towards new values instead of jumping (theme animation.smoothing_ms).

        A critically damped spring: it starts gently and a new reading changes
        its course without a kink. In video mode the glide lasts until the next
        reading, so values never stand still between readings.
        """
        wid = widget["id"]
        if (
            target is None
            or not self.animate
            or not widget.get("smooth", False)
            or self.theme.smoothing_ms <= 0
        ):
            if target is not None:
                self._anim[wid] = (target, 0.0, now)
            return target
        previous = self._anim.get(wid)
        if previous is None:
            self._anim[wid] = (target, 0.0, now)
            return target
        value, speed, then = previous
        glide = self.theme.smoothing_ms / 1000
        if self.continuous:
            glide = max(glide, self.sample[1])
        omega = 4.74 / glide  # 95 % of a step after ``glide`` seconds
        dt = max(0.0, now - then)
        decay = math.exp(-omega * dt)
        error = value - target
        curve = speed + omega * error
        value = target + (error + curve * dt) * decay
        speed = (speed - omega * curve * dt) * decay
        span = abs(widget.get("max", 100) - widget.get("min", 0)) or 1.0
        if abs(target - value) < span * 0.002 and abs(speed) * glide < span * 0.002:
            value, speed = target, 0.0
        else:
            self.moving = True
        self._anim[wid] = (value, speed, now)
        return value

    # -- effects and compositing ------------------------------------------

    def _effects(self, widget: dict[str, Any], piece: Piece) -> Piece:
        layer = _scale_alpha(piece.layer, widget.get("opacity", 1.0))
        box = piece.box or [piece.x, piece.y, piece.layer.width, piece.layer.height]
        out, pad = self._halo(widget, layer)
        if out is None:
            return Piece(layer, piece.x, piece.y, box, piece.backdrop, piece.backdrop_radius)
        out.alpha_composite(layer, (pad, pad))
        return Piece(out, piece.x - pad, piece.y - pad, box, piece.backdrop, piece.backdrop_radius)

    def _halo(self, widget: dict[str, Any], layer: Image.Image) -> tuple[Image.Image | None, int]:
        """Shadow and glow of ``layer`` (without the layer), ``pad`` pixels larger
        on every side; None without effects."""
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
            out.alpha_composite(_blur(base, widget.get("shadow_blur", 6)))
        if glow:
            base = Image.new("RGBA", size, (0, 0, 0, 0))
            base.paste(layer, (pad, pad))
            halo = _blur(base, widget.get("glow_radius", 10))
            boost = 1.0 + glow * 2.5  # light spills: brighter than the shape's own alpha
            halo.putalpha(halo.getchannel("A").point(lambda a: min(255, int(a * boost * glow))))
            out.alpha_composite(halo)
        return out, pad

    @staticmethod
    def _paste(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
        """alpha_composite that tolerates layers hanging off the canvas."""
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + layer.width, canvas.width), min(y + layer.height, canvas.height)
        if x1 <= x0 or y1 <= y0:
            return
        crop = layer.crop((x0 - x, y0 - y, x1 - x, y1 - y))
        canvas.alpha_composite(crop, (x0, y0))

    def _composite(
        self, canvas: Image.Image, piece: Piece, origin: tuple[int, int] = (0, 0)
    ) -> None:
        """Draw ``piece`` onto ``canvas``, whose top left corner is ``origin`` on the panel."""
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
        self._paste(canvas, piece.layer, piece.x - ox, piece.y - oy)

    @staticmethod
    def _extent(piece: Piece) -> tuple[int, int, int, int]:
        """Everything a piece can change on the canvas: its layer, and a backdrop box."""
        x0, y0 = piece.x, piece.y
        x1, y1 = x0 + piece.layer.width, y0 + piece.layer.height
        if piece.backdrop is not None and piece.box is not None:
            bx, by, bw, bh = piece.box
            x0, y0, x1, y1 = min(x0, bx), min(y0, by), max(x1, bx + bw), max(y1, by + bh)
        return x0, y0, x1, y1

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
        else:
            done = 0
            part = Image.new("RGBA", (x1 - x0, y1 - y0), self.color(self.theme.background_color))
            if self._background is not None:
                part.alpha_composite(self._background.crop(region))
        for i, (wid, piece) in enumerate(inside[done:], done):
            if i == below and not (reused and done == below):
                self._keep_underlay(region, [p for _, p in inside[:below]], part)
            try:
                self._composite(part, piece, (x0, y0))
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
        """Stop the helper thread and the screen (a replaced renderer should call this)."""
        if self._builder is not None:
            self._builder.close()
            self._builder = None
        if self.screen is not None:
            try:
                self.screen.close()
            except Exception as exc:
                log.warning("screen %s: %s", self.theme.screen["name"], exc)
            self.screen = None

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

        return self._cached(widget, ("plugin", kind.key(widget, snapshot, now)), build)

    # -- rendering ---------------------------------------------------------

    def render(
        self, snapshot: Snapshot, now: float | None = None
    ) -> tuple[Image.Image, dict[str, Box]]:
        now = time.monotonic() if now is None else now
        theme = self.theme
        self.moving = False
        boxes: dict[str, Box] = {}
        pieces: list[tuple[str, Piece]] = []
        screen = self._screen_image(snapshot, now) if self.screen is not None else None
        for widget in self.widgets:
            if not widget.get("visible", True) or self._missing(widget, snapshot):
                continue
            module = self._modules.get(widget.get("_module", ""))
            if module is not None and (not module["visible"] or self._missing(module, snapshot)):
                continue
            draw = getattr(self, f"_draw_{widget['type']}", None) or self._draw_plugin
            try:
                piece = draw(widget, snapshot, now)
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {widget['id']!r} failed: {exc}")
                continue
            if piece is None:
                continue
            pieces.append((widget["id"], piece))
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
        if screen is not None:
            if not pieces and screen.mode == "RGB" and screen.size == (theme.width, theme.height):
                # an opaque screen alone is the frame: nothing to compose
                self._last = None
                return screen, boxes
            pieces.insert(0, ("\0screen", self._screen_piece(screen)))  # ids are never empty
        return self._compose(pieces).convert("RGB"), boxes

    @staticmethod
    def _missing(widget: dict[str, Any], snapshot: Snapshot) -> bool:
        for need in (widget.get("needs") or "").split(","):  # all of them must hold
            need = need.strip()
            if not need:
                continue
            has = snapshot.value(need.lstrip("!").strip()) is not None
            if has == need.startswith("!"):
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
        if "\n" in text or not (spacing or (tabular and any(c in _DIGITS for c in text))):
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
        draw = ImageDraw.Draw(layer)
        pen = float(margin)
        for ch, advance in zip(text, advances, strict=True):
            if tabular and ch in _DIGITS:
                draw.text(
                    (pen + (cell - font.getlength(ch)) / 2, 0),
                    ch,
                    font=font,
                    fill=fill,
                    anchor="la",
                )
            else:
                draw.text((pen, 0), ch, font=font, fill=fill, anchor="la")
            pen += advance
        left = math.floor(start) - margin
        return Piece(layer, left, y, box=[left + margin, y, math.ceil(total), h])

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

    def _text_widget(self, widget: dict[str, Any], text: str, color: str) -> Piece | None:
        return self._cached(widget, (text, color), lambda: self._text_piece(widget, text, color))

    def _draw_text(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        return self._text_widget(widget, widget["text"], widget["color"])

    def _draw_metric(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        text, value = self._format(widget, snapshot.readings.get(widget["sensor"]))
        return self._text_widget(widget, text, _rule_color(widget, value) or widget["color"])

    def _draw_weather(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        text, _ = self._format(widget, snapshot.readings.get(f"weather.{widget['field']}"))
        return self._text_widget(widget, text, widget["color"])

    def _draw_clock(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        try:
            text = i18n.format_date(self._time(widget, snapshot), widget["format"])[:100]
        except ValueError:
            text = "--:--"
        return self._text_widget(widget, text, widget["color"])

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
            image = self._asset_image(rel, w, h, widget["fit"])
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
                picture = _fitted(picture, w, h, widget["fit"])
            if widget["radius"] > 0:
                picture = _rounded(picture, widget["radius"])
            return Piece(picture, x, y)

        return self._cached(widget, id(image), build)

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

    def _draw_icon(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
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
        piece = self._bar_piece(bar, frac, rule, x, y, w, h)
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
    def _down(layer: Image.Image, w: int, h: int) -> Image.Image:
        """Reduce a supersampled layer to its size on the panel.

        An exact 3x reduction averages each 3x3 block (with premultiplied
        alpha, so edges do not darken): classic supersampling, four times
        faster than Lanczos and visually the same (mean difference below a
        quarter of a level, only on antialiased edges).
        """
        w, h = max(1, w), max(1, h)
        s = SUPERSAMPLE
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
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE

        def build() -> Piece:
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
            return Piece(
                self._down(layer, w, h), x, y, [x, y, w, h], backdrop, widget["backdrop_blur"]
            )

        return self._cached(widget, None, build)

    def _draw_bar(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        target = _number(snapshot.value(widget["sensor"]))
        value = self._eased(widget, target, now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        rule = _rule_color(widget, target)
        key = (round(frac, 4), rule)
        return self._cached(widget, key, lambda: self._bar_piece(widget, frac, rule, x, y, w, h))

    def _bar_piece(
        self, widget: dict[str, Any], frac: float, rule: str | None, x: int, y: int, w: int, h: int
    ) -> Piece:
        s = SUPERSAMPLE
        layer, draw = self._canvas(w, h)
        W, H = w * s, h * s
        radius = min(widget["radius"], w // 2, h // 2) * s
        direction = widget["direction"]
        horizontal = direction in ("right", "left")
        a = self.color(rule or widget["color"])  # colour where the bar starts
        b = None if rule else self.color(widget["color2"])  # colour where a full bar ends
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

    @staticmethod
    def _gradient_colors(a: RGBA, b: RGBA | None, n: int) -> list[RGBA]:
        if b is None:
            return [a] * n
        return [_lerp(a, b, i / max(1, n - 1)) for i in range(n)]

    def _draw_gauge(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        target = _number(snapshot.value(widget["sensor"]))
        value = self._eased(widget, target, now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        rule = _rule_color(widget, target)
        return self._cached(
            widget, (round(frac, 4), rule), lambda: self._gauge_piece(widget, frac, rule)
        )

    def _gauge_piece(self, widget: dict[str, Any], frac: float, rule: str | None) -> Piece:
        """Ring gauge: a cached static part (track, ticks) plus the value arc.

        The arc is one antialiased mask filled from a cached conic gradient, so
        a gliding ring costs a few milliseconds per frame, not tens.
        """
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        layer = self._gauge_static(widget).copy()
        if frac > 0:
            a = self.color(rule or widget["color"])
            b = None if rule else self.color(widget["color2"])
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

    def _draw_graph(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        slots = max(2, widget["history"])
        if self.continuous and widget.get("per_frame") and self.fps > 0:
            return self._frame_graph(widget, snapshot, now)
        if self.continuous:
            return self._scrolling_graph(widget, snapshot, now, slots)
        values = snapshot.history.get(widget["sensor"], [])[-slots:]
        key = (len(values), tuple(values[-3:]), values[0] if values else None)
        return self._cached(widget, key, lambda: self._graph_piece(widget, values, slots))

    def _graph_piece(self, widget: dict[str, Any], values: list[float], slots: int) -> Piece:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        layer = self._graph_layer(widget, values, slots, w, slots - len(values))
        return Piece(self._down(layer, w, h), x, y, [x, y, w, h])

    # Video mode: a strip may take this long to draw before the graph needs it
    # (it is drawn in the helper thread while the previous one keeps scrolling).
    STRIP_DELAY_S = 0.1

    def _scrolling_graph(
        self, widget: dict[str, Any], snapshot: Snapshot, now: float, slots: int
    ) -> Piece | None:
        """The curve moves on every frame instead of jumping once per reading.

        Once per reading the curve is drawn onto a strip two sample widths wider
        than the graph; every frame shows a window into it that travels one
        sample width until the next reading. The newest segment enters at the
        right edge, so the curve runs one reading (smooth curves: two) plus
        ``STRIP_DELAY_S`` behind the numbers.
        """
        wid = widget["id"]
        ready, building = self._strips.get(wid, ([], None))
        if building is not None and building[1].done():
            try:
                ready = [*ready, building[1].result()][-2:]
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {wid!r} failed: {exc}")
            building = None
        if (not ready or ready[-1][0] != self._sample_no) and building is None:
            lag = 2 if widget["smooth"] else 1
            values = snapshot.history.get(widget["sensor"], [])[-(slots + lag + 1) :]
            number, at = self._sample_no, self.sample[0]

            def job() -> tuple[Any, ...]:
                return (number, at, *self._graph_strip(widget, values, slots, lag))

            if ready and self.background_builds:
                if self._builder is None:
                    self._builder = _Builder()
                building = (number, self._builder.submit(job))
            else:
                ready = [*ready, job()][-2:]
        self._strips[wid] = (ready, building)
        if not ready:
            return None
        # the newest strip that is due; before that, the one that is still travelling
        strip = next((s for s in reversed(ready) if now >= s[1] + self.STRIP_DELAY_S), ready[0])
        number, _at, halo, line, pad, step = strip
        travelled = (now - strip[1] - self.STRIP_DELAY_S) / self.sample[1]
        off = round((1 + min(1.0, max(0.0, travelled))) * step)
        self.moving = True
        content = ("scroll", number, off)
        hit = self._cache.get(wid)
        if hit is not None and hit[0] == content:
            return hit[1]
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        window = line.crop((off, 0, off + w, h))
        if halo is None:
            piece = Piece(window, x, y, [x, y, w, h])
        else:
            layer = halo.crop((off, 0, off + w + 2 * pad, h + 2 * pad))
            layer.alpha_composite(window, (pad, pad))
            piece = Piece(layer, x - pad, y - pad, [x, y, w, h])
        self._cache[wid] = (content, piece)
        return piece

    def _graph_strip(
        self, widget: dict[str, Any], values: list[float], slots: int, lag: int
    ) -> tuple[Image.Image | None, Image.Image, int, float]:
        """The curve on a strip for :meth:`_scrolling_graph`, with its effects:
        (halo or None, line, halo padding, sample width in pixels)."""
        w, h = widget["w"], widget["h"]
        step = (w * SUPERSAMPLE - 1) / (slots - 1) / SUPERSAMPLE
        width = w + math.ceil(2 * step) + 1
        # value i at strip x = graph width + (i - (last - lag) + 1) sample widths
        layer = self._graph_layer(widget, values, slots, width, slots + lag + 1 - len(values))
        line = _scale_alpha(self._down(layer, width, h), widget.get("opacity", 1.0))
        halo, pad = self._halo(widget, line)
        return halo, line, pad, step

    # A graph that moves per frame keeps this many readings of the future on its
    # strip; the next reading's strip replaces it after one (and STRIP_DELAY_S).
    FRAME_HORIZON = 3

    def _frame_graph(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        """One point per frame, a pixel apart, as SPUR II draws its graphs (theme
        ``per_frame``): the curve is the gliding value, so it moves visibly on
        every frame even when readings come once a second, and shows the last
        ``w`` frames.

        Drawing it every frame costs too much. The glide is a spring with a
        closed form, so once a reading is in, the curve is known until the next
        one: once per reading the past and the predicted rest go onto a strip
        (in the helper thread), and every frame shows the window that ends
        ``STRIP_DELAY_S`` ago, when that strip is ready.
        """
        wid, w, fps = widget["id"], widget["w"], self.fps
        glide = self._glides.setdefault(wid, _Glide())
        if glide.sample_no != self._sample_no:  # each reading once, also while a strip builds
            glide.sample_no = self._sample_no
            values = snapshot.history.get(widget["sensor"], [])
            if values:
                smoothing = self.theme.smoothing_ms / 1000 if self.animate else 0.0
                seconds = max(smoothing, self.sample[1]) if smoothing > 0 else 0.0
                glide.follow(self.sample[0], values[-1], seconds)
        ready, building = self._frame_strips.get(wid, ([], None))
        if building is not None and building[1].done():
            try:
                ready = [*ready, building[1].result()][-2:]
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {wid!r} failed: {exc}")
            building = None
        if (not ready or ready[-1][0] != self._sample_no) and building is None:
            at, interval = self.sample
            begin = at - self.STRIP_DELAY_S - (w + 1) / fps  # the oldest point in view
            ahead = self.FRAME_HORIZON * interval + self.STRIP_DELAY_S
            count = w + 1 + math.ceil(ahead * fps)
            glide.forget_before(begin)
            points = [glide.at(begin + j / fps) for j in range(count)]
            skip = next((j for j, v in enumerate(points) if v is not None), count)
            known = [v for v in points[skip:] if v is not None]
            number = self._sample_no

            def job() -> tuple[Any, ...]:
                return (number, at, begin, fps, *self._frame_strip(widget, known, skip, count))

            if ready and self.background_builds:
                if self._builder is None:
                    self._builder = _Builder()
                building = (number, self._builder.submit(job))
            else:
                ready = [*ready, job()][-2:]
        self._frame_strips[wid] = (ready, building)
        if not ready:
            return None
        strip = next((s for s in reversed(ready) if now >= s[1] + self.STRIP_DELAY_S), ready[0])
        number, _at, begin, rate, halo, line, pad, step = strip
        right = (now - self.STRIP_DELAY_S - begin) * rate  # the newest point in view
        off = min(max(0, round((right - (w - 1)) * step)), line.width - w)
        self.moving = True
        content = ("frame", number, off)
        hit = self._cache.get(wid)
        if hit is not None and hit[0] == content:
            return hit[1]
        x, y, h = widget["x"], widget["y"], widget["h"]
        window = line.crop((off, 0, off + w, h))
        if halo is None:
            piece = Piece(window, x, y, [x, y, w, h])
        else:
            layer = halo.crop((off, 0, off + w + 2 * pad, h + 2 * pad))
            layer.alpha_composite(window, (pad, pad))
            piece = Piece(layer, x - pad, y - pad, [x, y, w, h])
        self._cache[wid] = (content, piece)
        return piece

    def _frame_strip(
        self, widget: dict[str, Any], values: list[float], first: int, count: int
    ) -> tuple[Image.Image | None, Image.Image, int, float]:
        """The strip for :meth:`_frame_graph`: ``count`` points a pixel apart, the
        known ones from point ``first`` on. No spline: points that close are a
        curve already."""
        w, h = widget["w"], widget["h"]
        step = (w * SUPERSAMPLE - 1) / (w - 1) / SUPERSAMPLE
        width = max(w, math.ceil(count * step) + 1)
        flat = {**widget, "smooth": False}
        layer = self._graph_layer(flat, values, w, width, first)
        line = _scale_alpha(self._down(layer, width, h), widget.get("opacity", 1.0))
        halo, pad = self._halo(widget, line)
        return halo, line, pad, step

    def _graph_layer(
        self, widget: dict[str, Any], values: list[float], slots: int, width: int, first: int
    ) -> Image.Image:
        """The graph, supersampled, on a canvas ``width`` px wide: value ``i`` sits
        at sample position ``first + i`` of the widget's ``slots``."""
        s = SUPERSAMPLE
        W, H = width * s, widget["h"] * s
        step = (widget["w"] * s - 1) / (slots - 1)
        layer, draw = self._canvas(width, widget["h"])
        if widget["background"]:
            draw.rectangle([0, 0, W - 1, H - 1], fill=self.color(widget["background"]))
        if widget["grid"] > 0 and widget["grid_color"]:
            grid = self.color(widget["grid_color"])
            for i in range(1, widget["grid"] + 1):
                gy = round(H * i / (widget["grid"] + 1))
                draw.line([(0, gy), (W - 1, gy)], fill=grid, width=max(1, s // 2))
        if len(values) >= 2:
            lo = widget["min"] if widget["min"] is not None else min(values)
            hi = widget["max"] if widget["max"] is not None else max(values)
            if hi == lo:
                hi = lo + 1
            pad = widget["line_width"] * s
            points = [
                (
                    (first + i) * step,
                    pad
                    + (H - 1 - 2 * pad) * (1 - _fraction(v, lo, hi, widget.get("scale", "linear"))),
                )
                for i, v in enumerate(values)
            ]
            if widget["smooth"]:
                points = [(px, min(H - 1 - pad, max(pad, py))) for px, py in _catmull_rom(points)]
            color = self.color(widget["color"])
            if widget["fill"]:
                area = Image.new("L", layer.size, 0)
                polygon = [*points, (points[-1][0], H - 1), (points[0][0], H - 1)]
                ImageDraw.Draw(area).polygon(polygon, fill=255)
                if widget["fill_fade"]:
                    top = int(min(py for _, py in points))
                    fade = Image.new("L", (1, H), 0)
                    fade.putdata(
                        [int(115 * max(0.0, 1 - (row - top) / max(1, H - top))) for row in range(H)]
                    )
                    alpha = ImageChops.multiply(area, fade.resize(layer.size))
                else:
                    alpha = area.point(lambda v: v * 64 // 255)
                tint = Image.new("RGBA", layer.size, (*color[:3], 255))
                tint.putalpha(ImageChops.multiply(alpha, Image.new("L", layer.size, color[3])))
                layer.alpha_composite(tint)
            draw.line(points, fill=color, width=widget["line_width"] * s, joint="curve")
        return layer

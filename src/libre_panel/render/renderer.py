"""Turns a theme plus a sensor snapshot into a frame.

The theme editor uses this same renderer for its preview, so what the editor
shows is exactly what the panel shows.

Every widget is drawn into its own layer (shapes at 3x for smooth edges),
effects (shadow, glow, opacity) are applied to that layer and the result is
composited. Layers that did not change are reused from a cache, so a frame
where only a few values moved costs little.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from libre_panel import i18n
from libre_panel.fonts import DEFAULT_FONT, builtin_font_path
from libre_panel.icons import draw_icon, weather_icon_name
from libre_panel.render.formatting import FormatError, safe_format
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import Theme, ThemeError, resolve_asset

log = logging.getLogger(__name__)

# Sensor texts shown in the user's language on the panel.
TRANSLATED_READINGS = {"weather.description"}

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


class Renderer:
    def __init__(self, theme: Theme, animate: bool = False) -> None:
        self.theme = theme
        self.animate = animate
        self.warnings: list[str] = []
        self.moving = False  # True while an animation has not settled yet
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._digit_width: dict[int, float] = {}
        self._images: dict[tuple[str, int, int], Image.Image | None] = {}
        self._gauge_parts: dict[tuple[Any, ...], Image.Image] = {}  # static rings, colour fields
        self._anim: dict[str, tuple[float, float]] = {}
        self._cache: dict[str, tuple[Any, Piece]] = {}
        self._keys = {w["id"]: json.dumps(w, sort_keys=True) for w in theme.widgets}
        self._background = self._load_background()
        # Incremental compositing: the last frame and the pieces it was made of.
        # Only regions whose pieces changed are composed again, so a mostly
        # static screen costs little at 50 fps. The result is pixel-identical.
        self.incremental = True
        self._last: tuple[list[tuple[str, Piece]], Image.Image] | None = None
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

    def _asset_image(self, rel: str, w: int, h: int) -> Image.Image | None:
        key = (rel, w, h)
        if key not in self._images:
            image = None
            if rel and self.theme.root is not None:
                try:
                    with Image.open(resolve_asset(self.theme.root, rel)) as src:
                        image = src.convert("RGBA")
                    if w > 0 and h > 0:
                        image = image.resize((w, h), Image.Resampling.LANCZOS)
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

    def _eased(self, widget: dict[str, Any], target: float | None, now: float) -> float | None:
        """Glide towards new values instead of jumping (theme animation.smoothing_ms)."""
        wid = widget["id"]
        if (
            target is None
            or not self.animate
            or not widget.get("smooth", False)
            or self.theme.smoothing_ms <= 0
        ):
            if target is not None:
                self._anim[wid] = (target, now)
            return target
        previous = self._anim.get(wid)
        if previous is None:
            self._anim[wid] = (target, now)
            return target
        value, then = previous
        tau = self.theme.smoothing_ms / 1000 / 3  # ~95 % of the way after smoothing_ms
        value += (target - value) * (1 - math.exp(-max(0.0, now - then) / tau))
        span = abs(widget.get("max", 100) - widget.get("min", 0)) or 1.0
        if abs(target - value) < span * 0.002:
            value = target
        else:
            self.moving = True
        self._anim[wid] = (value, now)
        return value

    # -- effects and compositing ------------------------------------------

    def _effects(self, widget: dict[str, Any], piece: Piece) -> Piece:
        layer = _scale_alpha(piece.layer, widget.get("opacity", 1.0))
        glow = widget.get("glow", 0.0)
        shadow = widget.get("shadow")
        box = piece.box or [piece.x, piece.y, piece.layer.width, piece.layer.height]
        if not glow and not shadow:
            return Piece(layer, piece.x, piece.y, box, piece.backdrop, piece.backdrop_radius)
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
        out.alpha_composite(layer, (pad, pad))
        return Piece(out, piece.x - pad, piece.y - pad, box, piece.backdrop, piece.backdrop_radius)

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

    def _compose_region(
        self, pieces: list[tuple[str, Piece]], region: tuple[int, int, int, int]
    ) -> Image.Image:
        x0, y0, x1, y1 = region
        part = Image.new("RGBA", (x1 - x0, y1 - y0), self.color(self.theme.background_color))
        if self._background is not None:
            part.alpha_composite(self._background.crop(region))
        for wid, piece in pieces:
            px0, py0, px1, py1 = self._extent(piece)
            if px0 >= x1 or px1 <= x0 or py0 >= y1 or py1 <= y0:
                continue  # outside the region: its pixels there are unchanged
            try:
                self._composite(part, piece, (x0, y0))
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {wid!r} failed: {exc}")
        return part

    def _compose(self, pieces: list[tuple[str, Piece]]) -> Image.Image:
        theme = self.theme
        full = (0, 0, theme.width, theme.height)
        last = self._last if self.incremental else None
        regions = [full] if last is None else self._dirty(last[0], pieces)
        if last is not None and not regions:
            self._last = (pieces, last[1])
            return last[1]
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in regions)
        if last is None or area > 0.6 * theme.width * theme.height:
            canvas = self._compose_region(pieces, full)
        else:
            canvas = last[1]
            for region in regions:
                canvas.paste(self._compose_region(pieces, region), region[:2])
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

    def _screen_piece(self, snapshot: Snapshot, now: float) -> Piece | None:
        name = self.theme.screen["name"]
        try:
            image = self.screen.render(snapshot, now)
        except Exception as exc:
            self._warn(f"screen {name!r} failed: {exc}")
            return None
        if getattr(self.screen, "moving", False):
            self.moving = True
        if image is None:
            return None
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
        if self.screen is not None:
            screen = self._screen_piece(snapshot, now)
            if screen is not None:
                pieces.append(("\0screen", screen))  # not a widget id: ids are never empty
        for widget in theme.widgets:
            if not widget.get("visible", True) or self._missing(widget, snapshot):
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
            boxes[widget["id"]] = piece.box or [
                piece.x,
                piece.y,
                piece.layer.width,
                piece.layer.height,
            ]
        return self._compose(pieces).convert("RGB"), boxes

    @staticmethod
    def _missing(widget: dict[str, Any], snapshot: Snapshot) -> bool:
        if not widget.get("hide_if_missing"):
            return False
        if widget["type"] == "weather":
            key = f"weather.{widget['field']}"
        elif widget["type"] == "icon" and widget["icon"] == "weather":
            key = "weather.code"
        elif widget["type"] == "graph":
            return len(snapshot.history.get(widget["sensor"], [])) < 2
        else:
            key = widget.get("sensor")
        return key is not None and snapshot.value(key) is None

    # -- text --------------------------------------------------------------

    def _digit_cell(self, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
        key = id(font)
        if key not in self._digit_width:
            self._digit_width[key] = max(font.getlength(d) for d in _DIGITS)
        return self._digit_width[key]

    def _text_piece(self, widget: dict[str, Any], text: str, color: str) -> Piece:
        font = self.font(widget.get("font", ""), widget.get("font_size", 24))
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
            text = i18n.format_date(snapshot.now, widget["format"])[:100]
        except ValueError:
            text = "--:--"
        return self._text_widget(widget, text, widget["color"])

    # -- pictures ----------------------------------------------------------

    def _draw_image(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        def build() -> Piece:
            image = self._asset_image(widget["src"], widget["w"], widget["h"])
            if image is None:
                empty = Image.new("RGBA", (max(widget["w"], 1), max(widget["h"], 1)), (0, 0, 0, 0))
                return Piece(empty, widget["x"], widget["y"])
            return Piece(image, widget["x"], widget["y"])

        return self._cached(widget, None, build)

    def _draw_icon(self, widget: dict[str, Any], snapshot: Snapshot, now: float) -> Piece | None:
        name = widget["icon"]
        if name == "weather":
            night = not 6 <= snapshot.now.hour < 20
            name = weather_icon_name(snapshot.value("weather.code"), night)
        color = self.color(widget["color"])

        def build() -> Piece:
            icon = draw_icon(name, widget["size"], color, float(widget["stroke"]))
            return Piece(icon, widget["x"], widget["y"])

        return self._cached(widget, (name, color), build)

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
        values = snapshot.history.get(widget["sensor"], [])[-slots:]
        key = (len(values), tuple(values[-3:]), values[0] if values else None)
        return self._cached(widget, key, lambda: self._graph_piece(widget, values, slots))

    def _graph_piece(self, widget: dict[str, Any], values: list[float], slots: int) -> Piece:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE
        W, H = w * s, h * s
        layer, draw = self._canvas(w, h)
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
            offset = slots - len(values)
            points = [
                (
                    (offset + i) * (W - 1) / (slots - 1),
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
        return Piece(self._down(layer, w, h), x, y, [x, y, w, h])

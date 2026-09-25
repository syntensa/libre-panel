"""Turns a theme plus a sensor snapshot into a frame.

The theme editor uses this same renderer for its preview, so what the editor
shows is exactly what the panel shows.
"""

from __future__ import annotations

import logging
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps

from libre_panel.render.formatting import FormatError, safe_format
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import Theme, ThemeError, resolve_asset

log = logging.getLogger(__name__)

# Shapes are drawn at this scale and downsampled for smooth edges.
SUPERSAMPLE = 3
_ANCHORS = {"left": "la", "center": "ma", "right": "ra"}

Box = list[int]  # [x, y, w, h]


def rgba(color: str | None, alpha_scale: float = 1.0) -> tuple[int, int, int, int] | None:
    if color is None:
        return None
    rgb = ImageColor.getrgb(color)
    alpha = rgb[3] if len(rgb) == 4 else 255
    return (rgb[0], rgb[1], rgb[2], int(alpha * alpha_scale))


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


def _fraction(value: float | None, lo: float, hi: float) -> float:
    if value is None or hi == lo:
        return 0.0
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def _rule_color(widget: dict[str, Any], value: float | None) -> str:
    color = widget["color"]
    if value is None:
        return color
    for rule in sorted(widget.get("color_rules", []), key=lambda r: r["above"]):
        if value > rule["above"]:
            color = rule["color"]
    return color


class Renderer:
    def __init__(self, theme: Theme) -> None:
        self.theme = theme
        self.warnings: list[str] = []
        self._fonts: dict[tuple[str, int], ImageFont.ImageFont | ImageFont.FreeTypeFont] = {}
        self._images: dict[tuple[str, int, int], Image.Image | None] = {}
        self._background = self._load_background()

    # -- resources ---------------------------------------------------------

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            log.warning(message)

    def font(self, path: str, size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        size = max(4, min(int(size), 512))
        key = (path, size)
        if key not in self._fonts:
            font = None
            if path and self.theme.root is not None:
                try:
                    font = ImageFont.truetype(str(resolve_asset(self.theme.root, path)), size)
                except (OSError, ThemeError) as exc:
                    self._warn(f"font {path!r} could not be loaded ({exc}); using default")
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

    # -- compositing helpers ----------------------------------------------

    @staticmethod
    def _paste(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
        """alpha_composite that tolerates layers hanging off the canvas."""
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + layer.width, canvas.width), min(y + layer.height, canvas.height)
        if x1 <= x0 or y1 <= y0:
            return
        crop = layer.crop((x0 - x, y0 - y, x1 - x, y1 - y))
        canvas.alpha_composite(crop, (x0, y0))

    def _shape_layer(self, w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        layer = Image.new("RGBA", (max(1, w * SUPERSAMPLE), max(1, h * SUPERSAMPLE)), (0, 0, 0, 0))
        return layer, ImageDraw.Draw(layer)

    def _finish_shape(
        self, canvas: Image.Image, layer: Image.Image, x: int, y: int, w: int, h: int
    ) -> None:
        small = layer.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
        self._paste(canvas, small, x, y)

    # -- rendering ---------------------------------------------------------

    def render(self, snapshot: Snapshot) -> tuple[Image.Image, dict[str, Box]]:
        theme = self.theme
        canvas = Image.new("RGBA", (theme.width, theme.height), rgba(theme.background_color))
        if self._background is not None:
            canvas.alpha_composite(self._background)
        boxes: dict[str, Box] = {}
        for widget in theme.widgets:
            if not widget.get("visible", True):
                continue
            draw = getattr(self, f"_draw_{widget['type']}")
            try:
                box = draw(canvas, widget, snapshot)
            except Exception as exc:  # one broken widget must not blank the panel
                self._warn(f"widget {widget['id']!r} failed: {exc}")
                continue
            if box:
                boxes[widget["id"]] = box
        return canvas.convert("RGB"), boxes

    def _text(self, canvas: Image.Image, widget: dict[str, Any], text: str, color: str) -> Box:
        font = self.font(widget.get("font", ""), widget.get("font_size", 24))
        anchor = _ANCHORS.get(widget.get("align", "left"), "la")
        x, y = widget["x"], widget["y"]
        measure = ImageDraw.Draw(canvas)
        x0, y0, x1, y1 = measure.textbbox((x, y), text, font=font, anchor=anchor)
        x0, y0 = int(x0), int(y0)
        w, h = max(1, int(x1) - x0 + 1), max(1, int(y1) - y0 + 1)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (x - x0, y - y0), text, font=font, anchor=anchor, fill=rgba(color)
        )
        self._paste(canvas, layer, x0, y0)
        return [x0, y0, w, h]

    def _format(self, widget: dict[str, Any], reading: Any) -> tuple[str, float | None]:
        if reading is None or reading.value is None:
            return widget.get("fallback", "--"), None
        try:
            text = safe_format(widget["format"], reading.value, reading.unit, reading.label)
        except FormatError:
            text = reading.value if isinstance(reading.value, str) else widget.get("fallback", "--")
        return text, _number(reading.value)

    def _draw_text(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        return self._text(canvas, widget, widget["text"], widget["color"])

    def _draw_metric(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        text, value = self._format(widget, snapshot.readings.get(widget["sensor"]))
        return self._text(canvas, widget, text, _rule_color(widget, value))

    def _draw_weather(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        text, _ = self._format(widget, snapshot.readings.get(f"weather.{widget['field']}"))
        return self._text(canvas, widget, text, widget["color"])

    def _draw_clock(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        try:
            text = snapshot.now.strftime(widget["format"])[:100]
        except ValueError:
            text = "--:--"
        return self._text(canvas, widget, text, widget["color"])

    def _draw_image(
        self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot
    ) -> Box | None:
        image = self._asset_image(widget["src"], widget["w"], widget["h"])
        if image is None:
            return [widget["x"], widget["y"], max(widget["w"], 1), max(widget["h"], 1)]
        self._paste(canvas, image, widget["x"], widget["y"])
        return [widget["x"], widget["y"], image.width, image.height]

    def _draw_rect(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE
        layer, draw = self._shape_layer(w, h)
        radius = min(widget["radius"], w // 2, h // 2) * s
        draw.rounded_rectangle(
            [0, 0, w * s - 1, h * s - 1],
            radius=radius,
            fill=rgba(widget["color"]),
            outline=rgba(widget["outline"]),
            width=widget["outline_width"] * s if widget["outline"] else 0,
        )
        self._finish_shape(canvas, layer, x, y, w, h)
        return [x, y, w, h]

    def _draw_bar(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE
        value = _number(snapshot.value(widget["sensor"]))
        frac = _fraction(value, widget["min"], widget["max"])
        layer, draw = self._shape_layer(w, h)
        radius = min(widget["radius"], w // 2, h // 2) * s
        W, H = w * s, h * s
        if widget["background"]:
            draw.rounded_rectangle(
                [0, 0, W - 1, H - 1], radius=radius, fill=rgba(widget["background"])
            )
        if frac > 0:
            direction = widget["direction"]
            if direction == "right":
                rect = [0, 0, max(1, int(W * frac)) - 1, H - 1]
            elif direction == "left":
                rect = [W - max(1, int(W * frac)), 0, W - 1, H - 1]
            elif direction == "up":
                rect = [0, H - max(1, int(H * frac)), W - 1, H - 1]
            else:
                rect = [0, 0, W - 1, max(1, int(H * frac)) - 1]
            fill_r = min(radius, (rect[2] - rect[0]) // 2, (rect[3] - rect[1]) // 2)
            draw.rounded_rectangle(rect, radius=fill_r, fill=rgba(_rule_color(widget, value)))
        self._finish_shape(canvas, layer, x, y, w, h)
        return [x, y, w, h]

    def _draw_gauge(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE
        value = _number(snapshot.value(widget["sensor"]))
        frac = _fraction(value, widget["min"], widget["max"])
        start, end = widget["start_angle"], widget["end_angle"]
        thickness = max(1, widget["thickness"]) * s
        layer, draw = self._shape_layer(w, h)
        bbox = [0, 0, w * s - 1, h * s - 1]
        if widget["background"]:
            draw.arc(bbox, start, end, fill=rgba(widget["background"]), width=thickness)
        if frac > 0:
            draw.arc(
                bbox,
                start,
                start + (end - start) * frac,
                fill=rgba(_rule_color(widget, value)),
                width=thickness,
            )
        self._finish_shape(canvas, layer, x, y, w, h)
        return [x, y, w, h]

    def _draw_graph(self, canvas: Image.Image, widget: dict[str, Any], snapshot: Snapshot) -> Box:
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        s = SUPERSAMPLE
        W, H = w * s, h * s
        layer, draw = self._shape_layer(w, h)
        if widget["background"]:
            draw.rectangle([0, 0, W - 1, H - 1], fill=rgba(widget["background"]))
        slots = max(2, widget["history"])
        values = snapshot.history.get(widget["sensor"], [])[-slots:]
        if len(values) >= 2:
            lo = widget["min"] if widget["min"] is not None else min(values)
            hi = widget["max"] if widget["max"] is not None else max(values)
            if hi == lo:
                hi = lo + 1
            offset = slots - len(values)
            pad = widget["line_width"] * s
            points = []
            for i, v in enumerate(values):
                px = (offset + i) * (W - 1) / (slots - 1)
                py = pad + (H - 1 - 2 * pad) * (1 - _fraction(v, lo, hi))
                points.append((px, py))
            if widget["fill"]:
                polygon = points + [(points[-1][0], H - 1), (points[0][0], H - 1)]
                draw.polygon(polygon, fill=rgba(widget["color"], 0.25))
            draw.line(
                points, fill=rgba(widget["color"]), width=widget["line_width"] * s, joint="curve"
            )
        self._finish_shape(canvas, layer, x, y, w, h)
        return [x, y, w, h]

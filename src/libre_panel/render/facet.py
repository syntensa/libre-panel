"""The facet finish (``"finish": "facet"``): shapes with a flat top in their own colour
and a sharp 45-degree bevel, lit from the top left, as SPUR II's lettering and its bars,
rings and curves. The drawing is render.facets; this registers it with render.finish.

Lettering is laid out by the renderer (alignment, spacing, fitting) and only the
letters come from here; a bar, a ring and a graph's line are drawn here, and fall
back to the renderer's flat drawing where a bevel would not read (a bar under 6 px).
"""

from __future__ import annotations

import math
from typing import Any
from weakref import WeakKeyDictionary

from PIL import Image, ImageDraw

from libre_panel.render import facets
from libre_panel.render.finish import Finish, register
from libre_panel.render.renderer import Piece, _fraction, _number, _steps


class FacetText(Finish):
    """Lettering with a bevel, from 20 px on and on one line."""

    name = "facet"
    types = ("text", "metric", "clock", "weather", "countdown")

    def __init__(self) -> None:
        self._tints: WeakKeyDictionary[Any, Any] = WeakKeyDictionary()  # per renderer's palette

    def lettering(self, renderer, widget, font, text):
        return "\n" not in text and facets.usable(font)

    def glyph(self, renderer, widget, font, ch, fill, at):
        tints = self._tints.get(renderer)
        if tints is None:  # the cool and the warm tint a bevel takes on, from the palette
            tints = self._tints[renderer] = facets.tints_for(renderer.theme.palette)
        return facets.glyph(font, ch, fill, at, tints)


class FacetBar(Finish):
    """A raised bar with straight, bevelled ends in a milled track."""

    name = "facet"
    types = ("bar",)

    def draw(self, renderer, widget, snapshot, now):
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        value = renderer._eased(widget, _number(snapshot.value(widget["sensor"])), now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        frac = _steps(frac, w if widget["direction"] in ("right", "left") else h)
        paint = renderer._rule_paint(widget, value, now)

        def build() -> Piece:
            raised = facets.bar_layer(
                w, h, frac, widget["direction"], paint, renderer.color(widget["background"]),
                widget["segments"], widget["segment_gap"],
            )  # fmt: skip
            if raised is None:  # too thin for a bevel to read: flat
                return renderer._bar_piece(widget, frac, paint, x, y, w, h)
            return Piece(raised, x, y, [x, y, w, h])

        return renderer._cached(widget, (frac, paint), build)


class FacetGauge(Finish):
    """A raised ring with straight, bevelled ends in a milled track."""

    name = "facet"
    types = ("gauge",)

    def draw(self, renderer, widget, snapshot, now):
        x, y, w, h = widget["x"], widget["y"], widget["w"], widget["h"]
        value = renderer._eased(widget, _number(snapshot.value(widget["sensor"])), now)
        frac = _fraction(value, widget["min"], widget["max"], widget.get("scale", "linear"))
        sweep = abs(widget["end_angle"] - widget["start_angle"])
        frac = _steps(frac, math.radians(sweep) * max(w, h) / 2)
        paint = renderer._rule_paint(widget, value, now)
        return renderer._cached(
            widget, (frac, paint),
            lambda: Piece(self._ring(renderer, widget, frac, paint), x, y, [x, y, w, h]),
        )  # fmt: skip

    @staticmethod
    def _ring(renderer: Any, widget: dict[str, Any], frac: float, paint: Any) -> Image.Image:
        """The ring as a raised, bevelled arc in a milled track; the ticks, if any, flat."""
        layer = facets.ring_layer(
            widget["w"], widget["h"], widget["thickness"], widget["start_angle"],
            widget["end_angle"], frac, paint, renderer.color(widget["background"]),
        )  # fmt: skip
        if widget["ticks"] > 0:
            ticks = {
                **widget,
                "background": None,
                "tick_color": widget["tick_color"] or widget["background"],
            }
            layer.alpha_composite(renderer._gauge_static(ticks))
        return layer


class FacetGraph(Finish):
    """The renderer draws the graph; its line is drawn here, as a bevelled ridge (a
    slimmer one on a wide graph)."""

    name = "facet"
    types = ("graph",)

    def curve(self, renderer, widget, layer, runs, color, scale):
        wide = widget["w"] > facets.CURVE_WIDE
        width = widget["line_width"] * scale
        joint = "curve" if widget.get("\0joints", True) else None
        draw = ImageDraw.Draw(layer)
        for run in runs:
            if not facets.curve_ridge(layer, run, width, color, wide):
                draw.line(run, fill=color, width=width, joint=joint)  # not left to right: a line
        return True


for _finish in (FacetText(), FacetBar(), FacetGauge(), FacetGraph()):
    register(_finish)

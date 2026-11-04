"""A theme's look at runtime (theme ``"look"``): SPUR II's grid.

``look.grid`` lays thin lines over the background, every ``spacing`` px and
every ``major``-th one stronger, in the palette's colours: under every
widget, but over backdrops (full-panel rects at the bottom of the list), as
SPUR II's grid lies on its gradient. It is drawn once, like a background
picture.
"""

from __future__ import annotations

import weakref
from typing import Any

import numpy as np
from PIL import Image

from libre_panel.render.light import role_color
from libre_panel.render.renderer import Piece

# -- the grid --------------------------------------------------------------------------


def grid_image(size: tuple[int, int], spacing: int, major: int, minor_colour: tuple[int, ...],
               major_colour: tuple[int, ...]) -> Image.Image:  # fmt: skip
    """Lines every ``spacing`` px from the top left, every ``major``-th one
    (0: none) in ``major_colour``, on a clear picture of ``size``."""
    w, h = size
    out = np.zeros((h, w, 4), np.uint8)
    spacing = max(1, spacing)
    every = spacing * major

    def lines(length: int, strong: bool) -> np.ndarray:
        at = np.arange(0, length, spacing)
        return at[(at % every == 0) == strong] if major > 0 else (at[:0] if strong else at)

    for colour, strong in ((minor_colour, False), (major_colour, True)):
        out[:, lines(w, strong)] = colour
    for colour, strong in ((minor_colour, False), (major_colour, True)):  # rows over columns
        out[lines(h, strong), :] = colour
    return Image.fromarray(out, "RGBA")


def _named(renderer: Any, names: tuple[str, ...]) -> tuple[int, ...] | None:
    palette = renderer.theme.palette
    name = next((n for n in names if n in palette), None)
    return renderer.color("@" + name) if name else None


def grid_colours(
    renderer: Any, settings: dict[str, Any]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The grid's colours: as set, else the palette's ``grid`` / ``grid_major``,
    else a breath away from the background (the strong lines twice as far)."""
    ground = renderer.color(renderer.theme.background_color) or (0, 0, 0, 255)
    text = role_color(renderer.theme, "text")
    minor = renderer.color(settings["color"]) if settings["color"] else None
    minor = minor or _named(renderer, ("grid", "grid_min"))
    if minor is None:
        minor = (*(round(g + (t - g) * 0.05) for g, t in zip(ground[:3], text, strict=True)), 255)
    strong = renderer.color(settings["major_color"]) if settings["major_color"] else None
    strong = strong or _named(renderer, ("grid_major", "grid_maj"))
    if strong is None:
        twice = (min(255, max(0, 2 * m - g)) for m, g in zip(minor[:3], ground[:3], strict=True))
        strong = (*twice, minor[3])
    alpha = settings["alpha"]
    return (*minor[:3], round(minor[3] * alpha)), (*strong[:3], round(strong[3] * alpha))


# -- the look of a renderer ------------------------------------------------------------


class Look:
    """What a theme's ``look`` adds to one renderer's frames: its grid."""

    def __init__(self, renderer: Any) -> None:
        self.renderer = weakref.proxy(renderer)  # (it holds the look: no cycle)
        theme = renderer.theme
        self.grid = theme.look.get("grid")
        self._grid: Piece | None = None
        # Backdrops: full-panel rects at the bottom of the list, which the grid lies on.
        self._backdrops = set()
        for widget in renderer.widgets:
            covers = (
                widget["type"] == "rect"
                and widget["x"] <= 0
                and widget["y"] <= 0
                and (
                    widget["x"] + widget["w"] >= theme.width
                    and widget["y"] + widget["h"] >= theme.height
                )
            )
            if not covers:
                break
            self._backdrops.add(widget["id"])

    def _grid_piece(self) -> Piece:
        if self._grid is None:
            grid = self.grid
            minor, major = grid_colours(self.renderer, grid)
            size = (self.renderer.theme.width, self.renderer.theme.height)
            self._grid = Piece(grid_image(size, grid["spacing"], grid["major"], minor, major), 0, 0)
        return self._grid

    def prepare(self) -> None:
        if self.grid is not None:
            self._grid_piece()

    def compose(self, pieces: list[tuple[str, Piece]]) -> None:
        """Before compositing: the grid over the backdrops."""
        if self.grid is None:
            return
        at = 0
        while at < len(pieces) and (
            pieces[at][0] in self._backdrops or pieces[at][0] == "\0screen"
        ):
            at += 1
        pieces.insert(at, ("\0grid", self._grid_piece()))

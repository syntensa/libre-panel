"""Move a theme to another panel model or orientation.

``scale`` stretches positions to the new size while keeping round things
round and text proportional; ``keep`` only changes the canvas. Either way the
result is a starting point the user finishes in the editor.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from libre_panel.devices.models import get_model
from libre_panel.theme.model import parse_theme
from libre_panel.theme.modules import Grid, make_grid

# Sizes that should grow with the smaller scale factor (not stretched).
_UNIFORM = (
    "font_size",
    "thickness",
    "radius",
    "outline_width",
    "line_width",
    "glow_radius",
    "shadow_offset",
    "shadow_blur",
    "letter_spacing",
    "segment_gap",
    "backdrop_blur",
)
# Widgets whose box must keep its aspect ratio.
_KEEP_ASPECT = ("gauge", "image")


def _scale_widget(widget: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    s = min(sx, sy)
    out = dict(widget)
    for key in _UNIFORM:
        if key in out and isinstance(out[key], (int, float)):
            if key == "letter_spacing":  # may be negative (tight tracking)
                out[key] = round(out[key] * s)
                continue
            minimum = {"font_size": 6, "thickness": 1, "line_width": 1, "outline_width": 1}
            out[key] = max(minimum.get(key, 0), round(out[key] * s))
    if widget["type"] == "icon":  # square: keep it square and centred
        size = max(8, round(widget["size"] * s))
        cx = (widget["x"] + widget["size"] / 2) * sx
        cy = (widget["y"] + widget["size"] / 2) * sy
        out["size"], out["x"], out["y"] = size, round(cx - size / 2), round(cy - size / 2)
        return out
    has_box = "w" in out and "h" in out
    if has_box and widget["type"] in _KEEP_ASPECT and out["w"] > 0 and out["h"] > 0:
        cx = (widget["x"] + widget["w"] / 2) * sx
        cy = (widget["y"] + widget["h"] / 2) * sy
        out["w"], out["h"] = max(1, round(widget["w"] * s)), max(1, round(widget["h"] * s))
        out["x"], out["y"] = round(cx - out["w"] / 2), round(cy - out["h"] / 2)
        return out
    out["x"], out["y"] = round(widget["x"] * sx), round(widget["y"] * sy)
    if has_box:
        out["w"], out["h"] = max(1, round(widget["w"] * sx)), max(1, round(widget["h"] * sy))
    return out


def _move_modules(widgets: list[dict[str, Any]], old: Grid, new: Grid) -> list[dict[str, Any]]:
    """Modules onto another grid: the same share of it, without overlapping."""
    if (old.columns, old.rows) == (new.columns, new.rows):
        return widgets
    sx, sy = new.columns / old.columns, new.rows / old.rows
    taken: set[tuple[int, int]] = set()

    def free(col: int, row: int, cols: int, rows: int) -> bool:
        return all(
            (c, r) not in taken for c in range(col, col + cols) for r in range(row, row + rows)
        )

    out = []
    for widget in widgets:
        if widget["type"] != "module":
            out.append(widget)
            continue
        cols = max(1, min(new.columns, round(widget["cols"] * sx)))
        rows = max(1, min(new.rows, round(widget["rows"] * sy)))
        col = min(max(0, round(widget["col"] * sx)), new.columns - cols)
        row = min(max(0, round(widget["row"] * sy)), new.rows - rows)
        spot = None
        # where it was, else the nearest free place, smaller if it must be
        for c_, r_ in sorted(
            {(c, r) for c in range(cols, 0, -1) for r in range(rows, 0, -1)},
            key=lambda s: -s[0] * s[1],
        ):
            places = [(x, y) for y in range(new.rows - r_ + 1) for x in range(new.columns - c_ + 1)]
            places.sort(key=lambda p: abs(p[0] - col) + abs(p[1] - row))
            spot = next(((x, y, c_, r_) for x, y in places if free(x, y, c_, r_)), None)
            if spot:
                break
        moved = dict(widget)
        if spot is None:  # no room left: kept, but hidden
            moved["visible"] = False
        else:
            col, row, cols, rows = spot
            taken.update((c, r) for c in range(col, col + cols) for r in range(row, row + rows))
            moved.update(col=col, row=row, cols=cols, rows=rows)
        out.append(moved)
    return out


def adapt_theme(
    data: dict[str, Any],
    model: str,
    orientation: str,
    mode: str = "scale",
    width: int = 0,
    height: int = 0,
) -> dict[str, Any]:
    """Return a copy of theme ``data`` targeting ``model`` in ``orientation``.

    ``model`` may be ``custom``, then ``width``/``height`` give the size.
    """
    theme = parse_theme(data)
    old_w, old_h = theme.width, theme.height
    if model == "custom":
        if width <= 0 or height <= 0:
            raise ValueError("custom size needs width and height")
        new_w, new_h = width, height
    else:
        new_w, new_h = get_model(model).size(orientation)
    result = theme.to_dict()
    result["display"] = {
        "model": model,
        "orientation": orientation,
        "width": new_w,
        "height": new_h,
    }
    if mode == "scale":
        sx, sy = new_w / old_w, new_h / old_h
        result["widgets"] = [
            w if w["type"] == "module" else _scale_widget(w, sx, sy) for w in theme.widgets
        ]
    elif mode != "keep":
        raise ValueError('mode must be "scale" or "keep"')
    if any(w["type"] == "module" for w in theme.widgets):  # modules follow the grid either way
        old = make_grid(old_w, old_h, theme.grid, theme.model)
        new = make_grid(new_w, new_h, theme.grid, model)
        result["widgets"] = _move_modules(result["widgets"], old, new)
    parse_theme(deepcopy(result))  # the adapted theme must still be valid
    return result

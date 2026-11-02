"""Move a theme to another panel model or orientation.

``scale`` stretches positions to the new size while keeping round things
round and text proportional; ``keep`` only changes the canvas. Either way the
result is a starting point the user finishes in the editor.
"""

from __future__ import annotations

import functools
import glob
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from libre_panel.devices.models import get_model
from libre_panel.theme.model import (
    BLUR_MAX,
    THEME_FILENAME,
    VERTICAL,
    Theme,
    ThemeError,
    builtin_themes_dir,
    parse_theme,
    version_stem,
)
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
    "blur",
    "mark_width",
    "end_dot",
)
# Widgets whose box must keep its aspect ratio.
_KEEP_ASPECT = ("gauge", "image", "fan")


def _scale_widget(widget: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    s = min(sx, sy)
    out = dict(widget)
    for key in _UNIFORM:
        if key in out and isinstance(out[key], (int, float)):
            if key == "letter_spacing":  # may be negative (tight tracking), and a fraction
                out[key] = round(out[key] * s, 2)
                continue
            if key == "end_dot":  # a radius, a fraction of a px too; 0 is no dot at all
                out[key] = max(1.0, round(out[key] * s, 1)) if out[key] > 0 else out[key]
                continue
            minimum = {
                "font_size": 6,
                "thickness": 1,
                "line_width": 1,
                "outline_width": 1,
                "mark_width": 1,
            }
            out[key] = max(minimum.get(key, 0), round(out[key] * s))
            if key in ("glow_radius", "shadow_blur", "backdrop_blur", "blur"):
                out[key] = min(out[key], BLUR_MAX)  # (a theme holds no more than that)
    if widget["type"] in ("icon", "symbol", "moon", "agent", "earth"):  # square: kept so, centred
        size = max(8, round(widget["size"] * s))
        cx = (widget["x"] + widget["size"] / 2) * sx
        cy = (widget["y"] + widget["size"] / 2) * sy
        out["size"], out["x"], out["y"] = size, round(cx - size / 2), round(cy - size / 2)
        return out
    has_box = "w" in out and "h" in out
    # a blurred picture is a backdrop of its colours: it fills its box on any panel
    keep = widget["type"] in _KEEP_ASPECT and not (widget["type"] == "image" and widget.get("blur"))
    if has_box and keep and out["w"] > 0 and out["h"] > 0:
        cx = (widget["x"] + widget["w"] / 2) * sx
        cy = (widget["y"] + widget["h"] / 2) * sy
        out["w"], out["h"] = max(1, round(widget["w"] * s)), max(1, round(widget["h"] * s))
        out["x"], out["y"] = round(cx - out["w"] / 2), round(cy - out["h"] / 2)
        return out
    out["x"], out["y"] = round(widget["x"] * sx), round(widget["y"] * sy)
    if out.get("max_width"):  # the room a text has across: it narrows with the panel
        out["max_width"] = max(1, round(widget["max_width"] * sx))
    area = widget.get("glow_area")
    if isinstance(area, list) and len(area) == 4:  # a box on the panel, as the widget's
        ax, ay, aw, ah = area
        out["glow_area"] = [round(ax * sx), round(ay * sy), max(1, round(aw * sx)),
                            max(1, round(ah * sy))]  # fmt: skip
    if has_box:
        out["w"], out["h"] = max(1, round(widget["w"] * sx)), max(1, round(widget["h"] * sy))
    return out


def _on_frame(widget: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """A rule in the last column or row of the old frame rounds onto the first one past a much
    smaller frame, where nothing shows of it: its origin keeps to the last column and row."""
    widget["x"] = min(widget["x"], width - 1)
    widget["y"] = min(widget["y"], height - 1)
    return widget


def _scale_look(look: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    """The look on a panel of another size: the dust's areas are places on the panel, and
    move with it (its count follows the panel's area by itself)."""
    out = deepcopy(look)
    for area in out.get("dust", {}).get("areas", []):
        area["x"], area["y"] = round(area["x"] * sx), round(area["y"] * sy)
        area["w"], area["h"] = max(1, round(area["w"] * sx)), max(1, round(area["h"] * sy))
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
            w if w["type"] == "module" else _on_frame(_scale_widget(w, sx, sy), new_w, new_h)
            for w in theme.widgets
        ]
        if theme.look:
            result["look"] = _scale_look(theme.look, sx, sy)
    elif mode != "keep":
        raise ValueError('mode must be "scale" or "keep"')
    if any(w["type"] == "module" for w in theme.widgets):  # modules follow the grid either way
        old = make_grid(old_w, old_h, theme.grid, theme.model)
        if mode == "scale" and theme.grid:  # its margin and gap are pixels too (0: automatic)
            s = min(new_w / old_w, new_h / old_h)
            result["grid"] = {
                key: max(1, round(value * s)) if key in ("margin", "gap") and value else value
                for key, value in theme.grid.items()
            }
        new = make_grid(new_w, new_h, result.get("grid"), model)
        result["widgets"] = _move_modules(result["widgets"], old, new)
    parse_theme(deepcopy(result))  # the adapted theme must still be valid
    return result


# -- a theme laid out for a panel it has no version for -----------------------------------------
#
# Libre Panel's own themes come in versions made by hand for some sizes ("lumen-1920x720", the
# 12.3"), for a panel on end ("lumen-vertical") and for the round panels ("lumen-480x480"). A
# panel without its own (the 4.6", the 7", the 8", NZXT's round screens, most of BeadaPanel's,
# Thermalright's and JONSBO's) showed the theme made for another shape scaled into its frame,
# with bars round it. Now it shows the version made for the shape nearest to its own, laid out
# on its size (:func:`adapt_theme`, "scale"): Lumen of the 12.3" on Thermalright's 6.86"
# (1280x480, the same shape), Lumen of the 5.2" on the 5" and 7" and the 8", Lumen of the round
# 2.1" on NZXT's round screens. A version made for a size goes before it, as it did.

TINY_SIDE = 300  # a version made for a shorter side (the ZC-360's screens) is for such panels
# a version whose shape is further from the panel's than this (a factor of 1.5) is not laid out
# on it: a round screen on a bar, a bar on a square would be pulled apart (scaled into the
# frame with bars round it, as before)
SHAPE_MOST = math.log(1.5)
ROUND_ON_RECT = 0.2  # what a square version's shape counts more on a panel that is not square


def _shape_gap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """How far apart two sizes' shapes are: their aspect ratios, as a factor (log)."""
    return abs(math.log((a[0] / a[1]) / (b[0] / b[1])))


@functools.lru_cache(maxsize=256)
def _made_size(theme_file: str, stamp: int) -> tuple[int, int] | None:
    try:
        display = json.loads(Path(theme_file).read_text(encoding="utf-8"))["display"]
        return int(display["width"]), int(display["height"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def versions_of(folder: Path) -> dict[str, tuple[int, int]]:
    """The versions of the theme in ``folder`` beside it (itself among them), by name: those of
    the same stem, on end and for a size ("lumen", "lumen-vertical", "lumen-1920x720"), with
    the size each is made for."""
    stem = version_stem(folder.name)
    found = {}
    for other in folder.parent.glob(f"{glob.escape(stem)}*"):
        tail = other.name[len(stem) :]
        if tail and tail != VERTICAL and not re.fullmatch(r"-\d{2,5}x\d{2,5}", tail):
            continue  # another theme of that stem ("lumen-pair", "lumen-companion")
        theme_file = other / THEME_FILENAME
        try:
            stamp = theme_file.stat().st_mtime_ns
        except OSError:
            continue
        size = _made_size(str(theme_file), stamp)
        if size is not None:
            found[other.name] = size
    return found


def nearest_version(folder: Path, size: tuple[int, int]) -> str | None:
    """The version (:func:`versions_of`) whose shape is nearest to ``size``'s, no further from it
    than :data:`SHAPE_MOST`: one made for a side shorter than :data:`TINY_SIDE` only for such a
    panel (a small round panel takes the 2.1"'s, smaller). None where there is none."""
    tiny = min(size) < TINY_SIDE
    fits = {n: s for n, s in versions_of(folder).items()
            if (tiny or min(s) >= TINY_SIDE) and _shape_gap(size, s) <= SHAPE_MOST}  # fmt: skip
    if not fits:
        return None

    # the shape first; between two alike, the one made nearest to the size (a little); a
    # square version (made for a round panel: its rings round the rim) only where the panel is
    # square too (on the 4:3 pump's 640x480 it was pulled into an oval, the 16:9's fits it)
    def gap(n: str) -> float:
        round_on_rect = fits[n][0] == fits[n][1] and size[0] != size[1]
        return _shape_gap(size, fits[n]) + (ROUND_ON_RECT if round_on_rect else 0.0)

    return min(fits, key=lambda n: (gap(n) + 0.15 * abs(math.log(min(size) / min(fits[n]))), n))


@functools.lru_cache(maxsize=32)
def _laid_out_data(theme_file: str, stamp: int, width: int, height: int, model: str) -> str:
    data = json.loads(Path(theme_file).read_text(encoding="utf-8"))
    orientation = "portrait" if height > width else "landscape"
    if any(w.get("type") == "module" for w in data.get("widgets", [])):
        # its modules keep their cells: the grid it was made on, its columns and rows, on the
        # new size (a grid worked out anew for the size moved a module where another stood)
        made = data.get("display") or {}
        grid = make_grid(int(made["width"]), int(made["height"]), data.get("grid"),
                         str(made.get("model") or "custom"))  # fmt: skip
        data["grid"] = {**(data.get("grid") or {}), "columns": grid.columns, "rows": grid.rows}
    if model == "custom" or get_model(model).size(orientation) != (width, height):
        model = "custom"
    return json.dumps(adapt_theme(data, model, orientation, "scale", width, height))


def laid_out(theme: Theme, size: tuple[int, int] | None, model: str = "custom") -> Theme:
    """``theme`` for a panel whose frames are ``size``: as it is where it is made for that size
    (or the size is not known), else its version made for the nearest shape laid out on
    ``size`` (the pictures of the folder it is made in; ``model``: the panel's, whose bezel it
    keeps clear of). So for Libre Panel's own themes and for a theme of one's own with
    versions for more than one size (Every panel size → *Make a version for this size*); a
    theme of one's own made for one size stays as it is (its frames scaled into the panel's,
    as before)."""
    if size is None or (theme.width, theme.height) == tuple(size) or theme.root is None:
        return theme
    folder = Path(theme.root)
    try:
        own = folder.resolve().parent != builtin_themes_dir().resolve()
    except OSError:
        return theme
    if own and len(versions_of(folder)) < 2:
        return theme
    name = nearest_version(folder, tuple(size))
    if name is None:
        return theme
    made = folder.parent / name
    theme_file = made / THEME_FILENAME
    try:
        data = _laid_out_data(str(theme_file), theme_file.stat().st_mtime_ns, *size, model)
        return parse_theme(json.loads(data), root=made)
    except (OSError, ValueError, ThemeError):
        return theme

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

# Sizes that should grow with the smaller scale factor (not stretched).
_UNIFORM = ("font_size", "thickness", "radius", "outline_width", "line_width")
# Widgets whose box must keep its aspect ratio.
_KEEP_ASPECT = ("gauge", "image")


def _scale_widget(widget: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    s = min(sx, sy)
    out = dict(widget)
    for key in _UNIFORM:
        if key in out and isinstance(out[key], (int, float)):
            minimum = 6 if key == "font_size" else (0 if key == "radius" else 1)
            out[key] = max(minimum, round(out[key] * s))
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
        result["widgets"] = [_scale_widget(w, sx, sy) for w in theme.widgets]
    elif mode != "keep":
        raise ValueError('mode must be "scale" or "keep"')
    parse_theme(deepcopy(result))  # the adapted theme must still be valid
    return result

"""Turned widgets (``rotate``, degrees clockwise; discussion #23): the renderer turns a widget's
piece about its middle, a text about the point it is aligned at, and what lies over a bar with
it; the editor's box is where it now lies."""

import numpy as np
import pytest

from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import THEME_FORMAT, ThemeError, parse_theme

RED = "#ff0000"


def panel(*widgets, width=140, height=80):
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": width, "height": height},
            "background": {"color": "#000000"},
            "widgets": list(widgets),
        }
    )
    return Renderer(theme, preview=True)


def readings(**values):
    return Snapshot(readings={k: Reading(k, v) for k, v in values.items()})


def lit(frame, red=False):
    """The columns and rows of the frame that are not black (``red``: pure red)."""
    pixels = np.asarray(frame.convert("RGB")).astype(int)
    if red:
        mask = (pixels[..., 0] > 200) & (pixels[..., 1] < 60) & (pixels[..., 2] < 60)
    else:
        mask = pixels.sum(axis=2) > 60
    found = np.argwhere(mask)
    return {int(x) for _, x in found}, {int(y) for y, _ in found}


def rect(**fields):
    return {"type": "rect", "id": "r", "x": 40, "y": 30, "w": 60, "h": 20, "color": RED,
            "radius": 0, **fields}  # fmt: skip


def test_a_quarter_turn_is_exact_about_the_middle_and_the_box_goes_with_it():
    frame, boxes = panel(rect(rotate=90)).render(Snapshot())
    columns, rows = lit(frame, red=True)
    # the middle stays at (70, 40): 20 wide and 60 high now, pixel for pixel
    assert columns == set(range(60, 80)) and rows == set(range(10, 70))
    assert boxes["r"] == [60, 10, 20, 60]
    for angle in (-270, 450 - 360, 90.0):  # (the same turn)
        assert lit(panel(rect(rotate=angle)).render(Snapshot())[0], red=True) == (columns, rows)
    frame, boxes = panel(rect(rotate=180)).render(Snapshot())
    assert lit(frame, red=True) == (set(range(40, 100)), set(range(30, 50)))
    assert boxes["r"] == [40, 30, 60, 20]


def test_any_other_turn_is_smooth_and_the_box_holds_it():
    frame, boxes = panel(rect(rotate=45)).render(Snapshot())
    columns, rows = lit(frame)
    reach = (30 + 10) / 2**0.5  # (half of each side, at 45°)
    assert abs(min(columns) - (70 - reach)) <= 1.5 and abs(max(columns) - (70 + reach)) <= 1.5
    assert abs(min(rows) - (40 - reach)) <= 1.5 and abs(max(rows) - (40 + reach)) <= 1.5
    x, y, w, h = boxes["r"]
    assert x <= min(columns) and y <= min(rows)
    assert x + w >= max(columns) and y + h >= max(rows)
    pixels = np.asarray(frame.convert("RGB"))
    assert tuple(pixels[40, 70]) == (255, 0, 0)  # (the middle stays)
    assert tuple(pixels[40 - 14, 70 - 14]) == (255, 0, 0)  # up the long side, to the left
    assert tuple(pixels[40 - 14, 70 + 14]).count(0) == 3  # not across the short one


def test_what_lies_over_a_bar_turns_with_it():
    """A bar's mark turns about the bar's middle, not its own."""
    bar = {"type": "bar", "id": "b", "x": 20, "y": 30, "w": 100, "h": 20, "sensor": "share",
           "mark": "usual", "mark_color": RED, "color": "#00ff00", "background": None,
           "radius": 0, "rotate": 90}  # fmt: skip
    frame, boxes = panel(bar).render(readings(share=30.0, usual=60.0))
    columns, rows = lit(frame, red=True)
    # flat, the mark stood at x 20 + 60 % across, 4 px above and below; turned a quarter
    # about (70, 40), it lies across the turned bar, 60 % of the way down
    assert columns == set(range(60 - 4, 80 + 4))
    assert len(rows) == 2 and min(rows) == 40 - 50 + int(98 * 0.6)
    assert boxes["b"] == [60, -10, 20, 100]


def test_a_text_turns_about_the_point_it_is_aligned_at():
    """A changing value would move a text turned about the middle of its letters: it turns
    about its aligned point, halfway down its line, so where it starts stays."""
    metric = {"type": "metric", "id": "m", "x": 70, "y": 20, "sensor": "load", "font_size": 20,
              "format": "{value:.0f}", "rotate": 90, "color": "#ffffff"}  # fmt: skip
    short = lit(panel(metric, height=140).render(readings(load=1.0))[0])
    long = lit(panel(metric, height=140).render(readings(load=1888.0))[0])
    assert min(short[1]) == min(long[1]) and min(short[1]) >= 20  # (from x 70 down)
    assert max(long[1]) > max(short[1]) + 20  # the longer one reaches further down
    assert abs(min(short[0]) - min(long[0])) <= 1  # (the same line)


def test_a_turned_piece_is_turned_once_while_it_stays():
    renderer = panel(rect(rotate=30))
    renderer.render(Snapshot())
    first = renderer._turned["r"][2]
    renderer.render(Snapshot())
    assert renderer._turned["r"][2] is first
    renderer.theme.widgets[0]["rotate"] = 0  # (not turned any more: forgotten)
    renderer.render(Snapshot())
    assert renderer._turned == {}


def test_the_theme_keeps_a_turn_within_a_whole_turn_either_way():
    def theme(rotate):
        return parse_theme({"format": THEME_FORMAT, "display": {"width": 20, "height": 20},
                            "widgets": [rect(rotate=rotate)]})  # fmt: skip

    assert theme(-360).widgets[0]["rotate"] == -360
    assert parse_theme({"format": THEME_FORMAT, "display": {"width": 20, "height": 20},
                        "widgets": [{"type": "rect"}]}).widgets[0]["rotate"] == 0  # fmt: skip
    for bad in (361, -400, "90", True):
        with pytest.raises(ThemeError, match="rotate"):
            theme(bad)

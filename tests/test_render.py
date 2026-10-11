import pytest
from PIL import Image

from libre_panel.render.renderer import Renderer, _rule_color, changed_region
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import (
    THEME_FORMAT,
    builtin_themes_dir,
    find_theme,
    load_theme,
    parse_theme,
)

BUILTIN = sorted(p.parent.name for p in builtin_themes_dir().glob("*/theme.json"))


def test_all_themes_are_covered():
    assert {"libre-default", "spur-ii", "studio", "orbit", "slate", "column", "pico"} <= set(
        BUILTIN
    )


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_themes_render(name):
    theme = load_theme(find_theme(name))
    renderer = Renderer(theme)
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    frame, boxes = renderer.render(snapshot)
    assert frame.size == (theme.width, theme.height)
    assert frame.mode == "RGB"
    assert renderer.warnings == []
    # every visible widget, but those whose needs say "not now" (Studio's calendar sheet)
    shown = {w["id"] for w in theme.widgets if w["visible"] and not Renderer._missing(w, snapshot)}
    assert set(boxes) == shown
    assert len(shown) >= 0.8 * len(theme.widgets)


def test_rendering_is_deterministic_with_fixed_time():
    theme = load_theme(find_theme("libre-default"))
    a, _ = Renderer(theme).render(demo_snapshot(fixed_time=1_700_000_000))
    b, _ = Renderer(theme).render(demo_snapshot(fixed_time=1_700_000_000))
    assert changed_region(a, b) is None


def test_missing_sensors_show_fallback_instead_of_crashing():
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 200, "height": 100},
            "widgets": [
                {"type": "metric", "id": "m", "sensor": "gpu.temp", "fallback": "n/a"},
                {"type": "bar", "id": "b", "sensor": "gpu.temp"},
                {"type": "gauge", "id": "g", "sensor": "gpu.temp"},
                {"type": "graph", "id": "h", "sensor": "gpu.temp"},
                {"type": "weather", "id": "w"},
                {"type": "image", "id": "i", "src": "missing.png"},
            ],
        }
    )
    renderer = Renderer(theme)
    frame, boxes = renderer.render(Snapshot())
    assert set(boxes) == {"m", "b", "g", "h", "w", "i"}
    assert frame.size == (200, 100)


def test_string_values_fall_back_to_raw_text():
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 300, "height": 60},
            "widgets": [{"type": "weather", "id": "w", "field": "description"}],
        }
    )
    snap = Snapshot(readings={"weather.description": Reading("weather.description", "Overcast")})
    _, boxes = Renderer(theme).render(snap)
    assert boxes["w"][2] > 40  # rendered the word, not the 2-char fallback


def test_color_rules():
    widget = {
        "color": "#000000",
        "color_rules": [{"above": 90, "color": "#ff0000"}, {"above": 70, "color": "#ffff00"}],
    }
    assert _rule_color(widget, 50) is None  # no threshold reached: widget colour applies
    assert _rule_color(widget, 75) == "#ffff00"
    assert _rule_color(widget, 95) == "#ff0000"
    assert _rule_color(widget, None) is None


def test_bar_fill_color_follows_value():
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 100, "height": 20},
            "background": {"color": "#000000"},
            "widgets": [
                {
                    "type": "bar",
                    "id": "b",
                    "w": 100,
                    "h": 20,
                    "sensor": "x",
                    "radius": 0,
                    "background": None,
                    "color": "#00ff00",
                    "color_rules": [{"above": 80, "color": "#ff0000"}],
                }
            ],
        }
    )
    frame, _ = Renderer(theme).render(Snapshot(readings={"x": Reading("x", 90.0)}))
    assert frame.getpixel((10, 10)) == (255, 0, 0)
    assert frame.getpixel((97, 10)) == (0, 0, 0)  # 90 % filled


def test_changed_region():
    a = Image.new("RGB", (10, 10))
    b = a.copy()
    assert changed_region(a, b) is None
    b.putpixel((3, 4), (255, 255, 255))
    assert changed_region(a, b) == (3, 4, 4, 5)
    assert changed_region(None, b) == (0, 0, 10, 10)


def test_a_pieces_extent_is_worked_out_once_and_follows_a_copy():
    from dataclasses import replace

    from libre_panel.render.renderer import Piece

    light = Piece(Image.new("RGBA", (30, 10)), -5, 40)
    piece = Piece(Image.new("RGBA", (20, 20)), 10, 30, light=light)
    assert Renderer._extent(piece) == (-5, 30, 30, 50)  # the layer and its light
    assert piece.span == (-5, 30, 30, 50) and Renderer._extent(piece) is piece.span
    moved = replace(piece, x=100, light=None)
    assert moved.span is None and Renderer._extent(moved) == (100, 30, 120, 50)
    assert moved == replace(piece, x=100, light=None)  # the kept extent is no part of a piece


# -- a bar's mark ---------------------------------------------------------------------------------


def marked(snapshot, **bar):
    """The frame of a bar with a red mark (a 20 px bar at x 10, y 20 in a 220 x 60 panel)."""
    widget = {"type": "bar", "id": "b", "x": 10, "y": 20, "w": 200, "h": 20, "sensor": "share",
              "mark": "usual", "mark_color": "#ff0000", "radius": 0, **bar}  # fmt: skip
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 220, "height": 60},
            "background": {"color": "#000000"},
            "widgets": [widget],
        }
    )
    renderer = Renderer(theme, preview=True)
    return renderer, renderer.render(snapshot)[0]


def red(frame):
    """Where the frame is pure red: its columns and rows."""
    import numpy as np

    pixels = np.asarray(frame.convert("RGB")).astype(int)
    found = np.argwhere((pixels[..., 0] > 240) & (pixels[..., 1] < 20) & (pixels[..., 2] < 20))
    return {int(x) for _, x in found}, {int(y) for y, _ in found}


def readings(**values):
    return Snapshot(readings={k: Reading(k, v) for k, v in values.items()})


def test_a_bar_shows_a_second_reading_as_a_line_across_it():
    _, frame = marked(readings(share=30.0, usual=60.0))
    columns, rows = red(frame)
    # 60 % of the way, as the bar's own end would be: two pixels wide, ending where a full bar
    # would end for 100 %; it reaches 4 px (a fifth of the bar) above and below the bar
    assert columns == {10 + int(198 * 0.6) + n for n in (0, 1)}
    assert rows == set(range(20 - 4, 20 + 20 + 4))
    _, full = marked(readings(share=30.0, usual=100.0))
    assert red(full)[0] == {208, 209}  # the last two columns of the bar (x 10 to 209)
    _, empty = marked(readings(share=30.0, usual=0.0))
    assert red(empty)[0] == {10, 11}


def test_the_mark_follows_the_bars_scale_range_and_direction():
    _, frame = marked(readings(share=30.0, usual=50.0), min=40, max=60)  # halfway of 40 to 60
    assert red(frame)[0] == {10 + 99, 10 + 100}
    _, frame = marked(readings(share=30.0, usual=60.0), direction="left")
    assert red(frame)[0] == {10 + 200 - 2 - 118, 10 + 200 - 1 - 118}  # from the right end
    _, frame = marked(readings(share=30.0, usual=25.0), scale="sqrt")
    assert red(frame)[0] == {10 + 99, 10 + 100}  # a quarter is half the way on a root scale
    # a line is still a line past the ends, and on a bar that rises
    _, frame = marked(readings(share=30.0, usual=500.0))
    assert max(red(frame)[0]) == 209
    _, frame = marked(readings(share=30.0, usual=75.0), direction="up", x=100, y=5, w=20, h=50,
                      mark_width=4)  # fmt: skip
    columns, rows = red(frame)
    assert rows == {5 + 50 - 4 - int(46 * 0.75) + n for n in range(4)}  # 4 px thick, up from below
    assert columns == set(range(100 - 4, 100 + 20 + 4))


def test_a_mark_needs_its_reading_and_asks_for_it_and_never_stands_alone():
    from libre_panel.render.renderer import _reads

    renderer, frame = marked(readings(share=30.0))  # no reading to mark
    assert red(frame) == (set(), set())
    assert {"share", "usual"} <= _reads(renderer.widgets)  # a source that works on demand is asked
    _, frame = marked(readings(usual=60.0), needs="share")  # a bar that is not shown, no mark
    assert red(frame) == (set(), set())
    _, frame = marked(readings(usual=60.0))  # (a bar without its value is an empty bar)
    assert red(frame)[0] == {10 + int(198 * 0.6) + n for n in (0, 1)}
    _, frame = marked(readings(share=30.0, usual="text"))  # no number
    assert red(frame) == (set(), set())
    renderer, _ = marked(readings(share=30.0, usual=60.0), opacity=0.5)
    piece = dict(renderer._last[0])["b\0mark"]
    assert abs(piece.layer.getpixel((0, 8))[3] - 128) <= 1  # it fades with the bar


def test_the_mark_lies_over_a_lit_bar_too_and_moves_with_its_reading():
    renderer, lit = marked(readings(share=30.0, usual=60.0), finish="lit", background="#101010")
    assert red(lit)[0] == {10 + int(198 * 0.6) + n for n in (0, 1)}
    first = dict(renderer._last[0])["b\0mark"]
    renderer.render(readings(share=30.0, usual=60.0))
    assert dict(renderer._last[0])["b\0mark"] is first  # kept while it stands
    renderer.render(readings(share=30.0, usual=70.0))
    assert dict(renderer._last[0])["b\0mark"] is not first
    names = [wid for wid, _ in renderer._last[0]]
    assert names.index("b\0mark") == names.index("b") + 1  # right over its bar


def test_a_picture_inside_its_box_stands_left_in_the_middle_or_right():
    import numpy as np

    picture = Image.new("RGBA", (100, 50), (255, 0, 0, 255))  # twice as wide as high

    def columns(**fields):
        theme = parse_theme(
            {
                "format": THEME_FORMAT,
                "display": {"width": 220, "height": 60},
                "background": {"color": "#000000"},
                "widgets": [{"type": "image", "id": "i", "x": 10, "y": 5, "w": 200, "h": 50,
                             "src": "@game.logo", "fit": "contain", **fields}],
            }
        )  # fmt: skip
        frame, _ = Renderer(theme, preview=True).render(Snapshot(images={"game.logo": picture}))
        found = np.argwhere(np.asarray(frame.convert("RGB"))[..., 0] > 200)
        return int(found[:, 1].min()), int(found[:, 1].max())

    assert columns() == (60, 159)  # in the middle by default, as it was
    assert columns(align="center") == (60, 159)
    assert columns(align="left") == (10, 109)
    assert columns(align="right") == (110, 209)
    assert columns(align="right", fit="stretch") == (10, 209)  # only a picture with room moves

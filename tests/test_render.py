import math

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


# -- a graph's end dot ----------------------------------------------------------------------------


def dotted(snapshot, at=0.0, continuous=False, **graph):
    """The renderer of a graph with a red dot at its end (200 x 60 px at x 10, y 20 in a 220 x 100
    panel, from 0 to 100), its frame drawn at ``at``; ``continuous``: as in video mode."""
    widget = {"type": "graph", "id": "g", "x": 10, "y": 20, "w": 200, "h": 60, "sensor": "v",
              "min": 0, "max": 100, "history": 11, "color": "#ff0000", "end_dot": 5,
              "smooth": False, **graph}  # fmt: skip
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 220, "height": 100},
            "background": {"color": "#000000"},
            "widgets": [widget],
        }
    )
    renderer = Renderer(theme, animate=continuous, preview=True)
    renderer.continuous = continuous
    if continuous:
        renderer.new_sample(at, 1.0)
    return renderer, renderer.render(snapshot, at)[0]


def course(*values):
    """The snapshot of a graph's sensor that has read ``values`` (the last one is the newest)."""
    return Snapshot(readings={"v": Reading("v", values[-1])}, history={"v": list(values)})


def dot_of(renderer):
    """Where the end dot's piece has its weight on the panel (x, y in pixels, a pixel's centre at
    half a pixel) and how much it covers (the alpha added up, in pixels), None without a dot."""
    import numpy as np

    piece = dict(renderer._last[0]).get("g\0dot")
    if piece is None:
        return None
    alpha = np.asarray(piece.layer.getchannel("A")).astype(float) / 255
    rows, columns = np.mgrid[0 : alpha.shape[0], 0 : alpha.shape[1]]
    area = float(alpha.sum())
    return (
        piece.x + float((columns * alpha).sum()) / area + 0.5,
        piece.y + float((rows * alpha).sum()) / area + 0.5,
        area,
    )


def test_a_graph_ends_in_a_dot_at_its_newest_value():
    import math

    renderer, frame = dotted(course(10.0, 90.0, 60.0, 25.0))
    x, y, area = dot_of(renderer)
    # the line's newest point is the last pixel of the box, a quarter of a pixel in from its
    # edge; a line is 2 px wide, so 25 % of the range lies 6 + 0.75 * 167 rows (at 3 times the
    # size) below the top
    assert x == pytest.approx(10 + 200 - 1 / 6, abs=0.15)
    assert y == pytest.approx(20 + (6 + 0.75 * 167 + 0.5) / 3, abs=0.15)
    # a dot of 5 px radius, drawn in quarter pixels with the outer quarter inside (as SPUR II's)
    assert area == pytest.approx(math.pi * 5.125**2, abs=1.5)
    # it is on the frame, in the line's colour, and wider than the line itself
    column = [frame.getpixel((208, row))[:3] for row in range(100)]
    assert sum(1 for pixel in column if pixel == (255, 0, 0)) >= 8
    # it follows the newest value, and a larger one is higher
    higher, _ = dotted(course(10.0, 90.0, 60.0, 75.0))
    assert dot_of(higher)[1] < y - 20


def test_a_graph_that_hangs_from_the_top_ends_in_its_dot_below_its_newest_value_too():
    renderer, _ = dotted(course(10.0, 90.0, 60.0, 25.0), direction="down")
    _, y, _ = dot_of(renderer)
    assert y == pytest.approx(20 + (6 + 173 - (6 + 0.75 * 167) + 0.5) / 3, abs=0.15)
    renderer, _ = dotted(course(10.0, 90.0, 60.0, 25.0), inset=False, scale="sqrt")
    # no room for the line's width, and a root scale: half the way up at a quarter of the range
    assert dot_of(renderer)[1] == pytest.approx(20 + (0.5 * (60 - 1) * 3 + 0.5) / 3, abs=0.15)


def test_a_graph_has_no_dot_without_a_line_to_end_or_a_radius():
    for snapshot in (course(25.0), Snapshot(), Snapshot(readings={"v": Reading("v", 25.0)})):
        renderer, _ = dotted(snapshot)  # (one point is no line)
        assert dot_of(renderer) is None
    renderer, _ = dotted(course(10.0, 25.0), end_dot=0)
    assert dot_of(renderer) is None and [wid for wid, _ in renderer._last[0]] == ["g"]
    renderer, _ = dotted(course(10.0, 25.0), needs="gone")  # a graph that is not shown
    assert dot_of(renderer) is None
    assert dot_of(dotted(course(10.0, 25.0))[0]) is not None


def test_the_dot_takes_the_graphs_glow_and_opacity():
    plain, _ = dotted(course(10.0, 25.0))
    glowing, _ = dotted(course(10.0, 25.0), glow=0.6, glow_radius=6)
    assert dot_of(glowing)[2] > dot_of(plain)[2] + 20  # light around it
    assert dot_of(glowing)[:2] == pytest.approx(dot_of(plain)[:2], abs=0.1)  # the same centre
    faded, _ = dotted(course(10.0, 25.0), opacity=0.5)
    assert abs(dict(faded._last[0])["g\0dot"].layer.getchannel("A").getextrema()[1] - 128) <= 1


def test_the_dot_stays_while_the_value_stands_and_lies_right_over_its_graph():
    renderer, _ = dotted(course(10.0, 90.0, 25.0))
    first = dict(renderer._last[0])["g\0dot"]
    renderer.render(course(10.0, 90.0, 25.0), 0.0)
    assert dict(renderer._last[0])["g\0dot"] is first  # kept
    renderer.render(course(90.0, 25.0, 60.0), 0.0)
    assert dict(renderer._last[0])["g\0dot"] is not first
    names = [wid for wid, _ in renderer._last[0]]
    assert names.index("g\0dot") == names.index("g") + 1


def test_in_video_mode_the_dot_glides_with_the_value_at_the_end_of_the_curve():
    scale = Renderer.STRIP_SCALE
    renderer, _ = dotted(course(20.0, 20.0, 20.0), 100.0, continuous=True)
    start = dot_of(renderer)
    assert start[0] == pytest.approx(10 + 200 - 0.5 / scale, abs=0.15)
    assert start[1] == pytest.approx(20 + (4 + 0.8 * 111 + 0.5) / scale, abs=0.15)  # at 20 %
    renderer.new_sample(101.0, 1.0)
    seen = []
    for at in (101.0, 101.5, 102.0, 103.0):
        renderer.render(course(20.0, 20.0, 80.0), at)
        seen.append(dot_of(renderer))
    rows = [dot[1] for dot in seen]
    assert rows[0] > rows[1] > rows[2] >= rows[3]  # it rises with the value (in quarter pixels)
    assert all(dot[0] == pytest.approx(start[0], abs=0.15) for dot in seen)  # at the edge
    assert rows[3] == pytest.approx(20 + (4 + 0.2 * 111 + 0.5) / scale, abs=0.2)  # arrived at 80 %


# -- readings that are not numbers ----------------------------------------------------------------


def gliding(**metric):
    """A bar and a number of the sensor ``v``, a number that hides without it, and a text that
    needs it."""
    widgets = [
        {"type": "bar", "id": "b", "x": 0, "y": 0, "w": 200, "h": 20, "sensor": "v", "radius": 0},
        {"type": "metric", "id": "m", "x": 0, "y": 30, "sensor": "v", "format": "{value:.0f}",
         "font_size": 24, "smooth": True, **metric},
        {"type": "metric", "id": "h", "x": 0, "y": 60, "sensor": "v", "hide_if_missing": True},
        {"type": "text", "id": "t", "x": 0, "y": 80, "text": "needs v", "needs": "v"},
    ]  # fmt: skip
    theme = parse_theme(
        {"format": THEME_FORMAT, "display": {"width": 200, "height": 120}, "widgets": widgets}
    )
    renderer = Renderer(theme, animate=True, motion=None)
    renderer.fps = 50
    return renderer


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_reading_that_is_not_a_number_is_no_reading_and_poisons_nothing(value):
    """A source can send one ("nan" from a payload): it is a reading that is not there, and the
    value that glides, the bars and the numbers of its sensor come back with the next one."""
    renderer = gliding()
    renderer.render(readings(v=10.0), 0.0)
    _, boxes = renderer.render(readings(v=value), 0.02)
    assert renderer.motion.value("v", 0.02) is None  # missing, not a value that is not a number
    assert renderer._cache["m"][0][0] == "--"  # the fallback, not "nan"
    assert "h" not in boxes and "t" not in boxes  # what hides without the reading hides
    assert renderer.warnings == []
    for k in range(2, 100):
        renderer.render(readings(v=50.0), k / 50)
    assert renderer.motion.value("v", 99 / 50) == 50.0  # the glide was not poisoned
    assert renderer._cache["m"][0][0] == "50" and not renderer.moving


def test_a_value_that_is_not_a_number_is_a_gap_in_a_graphs_history():
    """In the PNG path (a graph of ``history`` readings) and in video mode (the motion's
    history): a gap in the curve, and no range worked out from NaN."""
    from libre_panel.render.motion import Motion

    history = [10.0, math.nan, 30.0, math.inf, 50.0]
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 100, "height": 40},
            "widgets": [{"type": "graph", "id": "g", "w": 100, "h": 40, "sensor": "v", "min": None,
                         "max": None, "history": 5}],
        }
    )  # fmt: skip
    renderer = Renderer(theme)
    drawn = []
    made = renderer._graph_piece

    def spy(widget, values, slots):
        drawn.append(values)
        return made(widget, values, slots)

    renderer._graph_piece = spy
    renderer.render(Snapshot(readings={"v": Reading("v", 50.0)}, history={"v": history}))
    assert drawn == [[10.0, None, 30.0, None, 50.0]]
    kept = Motion().history("v", 10, 100.0, history, 100.0, 1.0, 0.4)
    assert all(point is None or math.isfinite(point) for point in kept.points)


def test_a_weather_code_that_is_no_number_is_a_cloud_not_a_missing_icon():
    from libre_panel.icons import weather_icon_name
    from libre_panel.render.symbols import kind_of

    for code in (None, "n/a", "", math.nan, math.inf):
        assert weather_icon_name(code) == "cloud" and kind_of(code) == "overcast"
    assert weather_icon_name("63") == "rain" and kind_of(95.0) == "storm"

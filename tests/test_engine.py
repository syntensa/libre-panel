"""Rendering features: palette, fonts, effects, gradients, icons, animation."""

import pytest

from libre_panel.config import ConfigError, parse_config
from libre_panel.icons import ICON_NAMES, draw_icon, weather_icon_name
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import THEME_FORMAT, ThemeError, parse_theme

BG = (0, 0, 0)


def theme(*widgets, palette=None, size=(200, 100), **extra):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#000000"},
            "palette": palette or {},
            "widgets": list(widgets),
            **extra,
        }
    )


def render(t, readings=None, **kwargs):
    snap = Snapshot(readings={k: Reading(k, v) for k, v in (readings or {}).items()})
    frame, boxes = Renderer(t, **kwargs).render(snap)
    return frame, boxes


def rect(**kw):
    return {
        "type": "rect",
        "id": "r",
        "x": 50,
        "y": 25,
        "w": 100,
        "h": 50,
        "color": "#ff0000",
        **kw,
    }


def test_palette_references():
    t = theme(rect(color="@accent"), palette={"accent": "#00ff00"})
    frame, _ = render(t)
    assert frame.getpixel((100, 50)) == (0, 255, 0)
    assert t.to_dict()["palette"] == {"accent": "#00ff00"}


@pytest.mark.parametrize(
    "palette, widget, message",
    [
        ({}, rect(color="@missing"), "not in the theme palette"),
        ({"Bad Name": "#fff"}, rect(), "invalid name"),
        ({"a": "@b"}, rect(), "references are not allowed"),
    ],
)
def test_palette_errors(palette, widget, message):
    with pytest.raises(ThemeError, match=message):
        theme(widget, palette=palette)


def test_builtin_fonts():
    t = theme({"type": "text", "id": "t", "text": "Hi", "font": "builtin:Barlow-Bold"})
    _, boxes = render(t)
    assert boxes["t"][2] > 10
    with pytest.raises(ThemeError, match="unknown built-in font"):
        theme({"type": "text", "id": "t", "font": "builtin:Comic-Sans"})
    with pytest.raises(ThemeError, match="unknown built-in font"):
        theme({"type": "text", "id": "t"}, font="builtin:Nope")


def test_opacity_glow_and_shadow():
    plain, _ = render(theme(rect()))
    assert plain.getpixel((100, 50)) == (255, 0, 0)
    assert plain.getpixel((45, 50)) == BG

    half, _ = render(theme(rect(opacity=0.5)))
    assert 120 <= half.getpixel((100, 50))[0] <= 135

    glowing, _ = render(theme(rect(glow=1.0, glow_radius=8)))
    assert glowing.getpixel((45, 50))[0] > 20  # light spills beyond the shape
    assert glowing.getpixel((45, 50))[1:] == (0, 0)  # and keeps its colour

    shadowed, _ = render(theme(rect(shadow="#0000ff", shadow_offset=6, shadow_blur=2)))
    assert shadowed.getpixel((153, 78))[2] > 100  # below right
    assert shadowed.getpixel((47, 22)) == BG  # not above left


@pytest.mark.parametrize("key", ["opacity", "glow"])
def test_effect_ranges(key):
    with pytest.raises(ThemeError, match="between 0 and 1"):
        theme(rect(**{key: 1.5}))


def test_rect_gradient_and_backdrop():
    frame, _ = render(theme(rect(color="#ff0000", color2="#0000ff", gradient="horizontal")))
    left, right = frame.getpixel((52, 50)), frame.getpixel((147, 50))
    assert left[0] > 200 and left[2] < 60
    assert right[2] > 200 and right[0] < 60

    stripes = [
        {"type": "rect", "id": f"s{i}", "x": i * 4, "y": 0, "w": 2, "h": 100, "color": "#ffffff"}
        for i in range(50)
    ]
    glass = rect(id="glass", color="#00000000", backdrop_blur=6)
    sharp, _ = render(theme(*stripes))
    frosted, _ = render(theme(*stripes, glass))
    assert sharp.getpixel((100, 50)) in ((255, 255, 255), BG)
    assert 40 < frosted.getpixel((100, 50))[0] < 220  # blurred to grey
    assert frosted.getpixel((10, 10)) == sharp.getpixel((10, 10))  # outside untouched


def bar(**kw):
    return {"type": "bar", "id": "b", "x": 0, "y": 0, "w": 200, "h": 20, "sensor": "v", "radius": 0,
            "background": "#202020", "color": "#ff0000", **kw}  # fmt: skip


def test_bar_gradient_follows_the_track():
    frame, _ = render(theme(bar(color2="#0000ff")), {"v": 100.0})
    assert frame.getpixel((2, 10))[0] > 200 and frame.getpixel((197, 10))[2] > 200
    frame, _ = render(theme(bar(color2="#0000ff", direction="left")), {"v": 100.0})
    assert frame.getpixel((197, 10))[0] > 200  # starts (red) on the right


@pytest.mark.parametrize("direction", ["right", "left", "up", "down"])
def test_tiny_values_stay_visible(direction):
    size = {"w": 20, "h": 200} if direction in ("up", "down") else {}
    frame, _ = render(theme(bar(direction=direction, **size), size=(200, 200)), {"v": 0.01})
    assert frame  # no exception for sub-pixel fills


def test_segmented_bar():
    frame, _ = render(theme(bar(segments=10, segment_gap=4)), {"v": 50.0})
    lit = sum(1 for x in range(5, 200, 20) if frame.getpixel((x, 10))[0] > 200)
    assert lit == 5


def test_gauge_caps_gradient_rules_and_ticks():
    gauge = {"type": "gauge", "id": "g", "x": 0, "y": 0, "w": 100, "h": 100, "sensor": "v",
             "color": "#00ff00", "color2": "#0000ff", "ticks": 10, "tick_color": "#ffffff",
             "color_rules": [{"above": 90, "color": "#ff0000"}]}  # fmt: skip
    frame, _ = render(theme(gauge, size=(100, 100)), {"v": 50.0})
    assert max(frame.getpixel((x, y))[1] for x in range(0, 30) for y in range(60, 100)) > 150
    hot, _ = render(theme(gauge, size=(100, 100)), {"v": 95.0})
    reds = [hot.getpixel((x, y)) for x in range(100) for y in range(100)]
    assert any(p[0] > 200 and p[1] < 60 for p in reds)  # rule colour replaces the gradient


def test_graph_options_render_cleanly():
    graph = {"type": "graph", "id": "h", "x": 0, "y": 0, "w": 200, "h": 80, "sensor": "v",
             "smooth": True, "fill_fade": True, "grid": 3, "history": 20}  # fmt: skip
    snap = Snapshot(history={"v": [10, 80, 30, 90, 20, 60, 40, 100, 0, 50]})
    renderer = Renderer(theme(graph, size=(200, 80)))
    renderer.render(snap)
    assert renderer.warnings == []


def text(t, **kw):
    return {"type": "metric", "id": "m", "x": 0, "y": 0, "sensor": "v", "format": t,
            "font_size": 30, **kw}  # fmt: skip


def test_tabular_digits_do_not_jitter():
    widths = []
    for value in ("1111", "8888"):
        _, boxes = render(theme(text(value, tabular=True)), {"v": 1.0})
        widths.append(boxes["m"][2])
    assert widths[0] == widths[1]
    proportional = [
        render(theme(text(v, tabular=False)), {"v": 1.0})[1]["m"][2] for v in ("1111", "8888")
    ]
    assert proportional[0] < proportional[1]


def test_letter_spacing():
    tight = render(theme(text("CPU", tabular=False)), {"v": 1.0})[1]["m"][2]
    wide = render(theme(text("CPU", tabular=False, letter_spacing=6)), {"v": 1.0})[1]["m"][2]
    assert wide >= tight + 10


def test_icons():
    assert "weather" in ICON_NAMES
    for name in ICON_NAMES:
        if name != "weather":
            icon = draw_icon(name, 24, (255, 255, 255, 255), 2)
            assert icon.size == (24, 24) and icon.getbbox() is not None
    assert weather_icon_name(0) == "sun" and weather_icon_name(0, night=True) == "moon"
    assert (weather_icon_name(63), weather_icon_name(75), weather_icon_name(95)) == (
        "rain",
        "snow",
        "storm",
    )
    t = theme({"type": "icon", "id": "i", "icon": "weather", "size": 48, "color": "#ffffff"})
    frame, boxes = render(t, {"weather.code": 3.0})
    assert boxes["i"] == [0, 0, 48, 48]
    with pytest.raises(ThemeError, match="unknown icon"):
        theme({"type": "icon", "id": "i", "icon": "unicorn"})


def test_values_glide_when_animated():
    t = theme(bar(smooth=True), animation={"smoothing_ms": 400})
    renderer = Renderer(t, animate=True)

    def filled(value, now):
        frame, _ = renderer.render(Snapshot(readings={"v": Reading("v", value)}), now)
        return sum(1 for x in range(0, 200, 2) if frame.getpixel((x, 10))[0] > 200) * 2

    assert filled(0.0, 0.0) == 0
    middle = filled(100.0, 0.1)
    assert 0 < middle < 200 and renderer.moving
    assert filled(100.0, 2.0) == 200 and not renderer.moving
    still = Renderer(t, animate=False)
    frame, _ = still.render(Snapshot(readings={"v": Reading("v", 100.0)}), 0.0)
    assert frame.getpixel((198, 10))[0] > 200


def test_adapt_scales_new_fields():
    data = theme(
        {"type": "icon", "id": "i", "x": 10, "y": 10, "size": 40},
        {"type": "text", "id": "t", "letter_spacing": 4, "glow": 0.5, "glow_radius": 10},
        size=(480, 320),
    ).to_dict()
    out = adapt_theme(data, "turing-5", "landscape")  # 800x480: s = 1.5
    widgets = {w["id"]: w for w in out["widgets"]}
    assert widgets["i"]["size"] == 60
    assert widgets["t"]["letter_spacing"] == 6 and widgets["t"]["glow_radius"] == 15


def test_fps_setting():
    assert parse_config({"fps": 25}).fps == 25
    with pytest.raises(ConfigError):
        parse_config({"fps": 0})


def test_same_frame_twice_is_identical():
    t = theme(rect(glow=0.8), text("{value:.0f}"), bar())
    renderer = Renderer(t)
    snap = Snapshot(readings={"v": Reading("v", 42.0)})
    a, _ = renderer.render(snap)
    b, _ = renderer.render(snap)
    assert a.tobytes() == b.tobytes()


def test_hide_if_missing():
    widgets = [
        {"type": "metric", "id": "m", "sensor": "gpu.temp", "hide_if_missing": True},
        {"type": "icon", "id": "i", "icon": "weather", "hide_if_missing": True},
        {"type": "weather", "id": "w", "hide_if_missing": True},
        {"type": "metric", "id": "shown", "sensor": "gpu.temp"},
    ]
    _, boxes = render(theme(*widgets))
    assert set(boxes) == {"shown"}
    _, boxes = render(
        theme(*widgets), {"gpu.temp": 50.0, "weather.code": 1.0, "weather.temperature": 9.0}
    )
    assert set(boxes) == {"m", "i", "w", "shown"}


def test_scales_keep_small_values_visible():
    from libre_panel.render.renderer import _fraction

    assert _fraction(1, 0, 100) == 0.01
    assert round(_fraction(1, 0, 100, "sqrt"), 2) == 0.1
    assert 0.1 < _fraction(1, 0, 100, "log") < 0.5
    assert _fraction(100, 0, 100, "log") == 1.0
    frame, _ = render(theme(bar(scale="sqrt")), {"v": 4.0})  # 4 % -> 20 % of the track
    assert frame.getpixel((35, 10))[0] > 200 and frame.getpixel((45, 10))[0] < 100

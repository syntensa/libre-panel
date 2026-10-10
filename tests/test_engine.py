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


def spike_graph(**kw):
    return {"type": "graph", "id": "g", "x": 0, "y": 0, "w": 200, "h": 60, "sensor": "v",
            "history": 11, "min": 0, "max": 100, "fill": False, "color": "#ffffff",
            "line_width": 1, "smooth": False, **kw}  # fmt: skip


def spike_x(frame):
    """Where the curve comes nearest to the top: the spike's x on the panel."""
    tops = {}
    for x in range(frame.width):
        rows = [y for y in range(frame.height) if frame.getpixel((x, y))[0] > 120]
        if rows:
            tops[x] = min(rows)
    best = min(tops.values())
    xs = [x for x, top in tops.items() if top == best]
    return sum(xs) / len(xs)


SPIKE = [10.0] * 8 + [90.0, 10.0, 10.0]  # the spike is the third reading from the end
STEP = 599 / 30  # 11 samples on 200 px: 19.97 px apart


def test_graphs_scroll_between_readings_in_video_mode():
    renderer = Renderer(theme(spike_graph(), size=(200, 60)))
    renderer.continuous = True
    delay = renderer.STRIP_DELAY_S
    renderer.new_sample(100.0, 1.0)
    snap = Snapshot(history={"v": SPIKE})
    at_start = spike_x(renderer.render(snap, 100.0 + delay)[0])
    halfway = spike_x(renderer.render(snap, 100.5 + delay)[0])
    at_end = spike_x(renderer.render(snap, 101.0 + delay)[0])
    assert renderer.moving
    # one reading late: the second reading from the end is at the right edge
    assert abs(at_start - (199 - STEP)) <= 1.5, at_start
    assert abs(at_start - halfway - STEP / 2) <= 1.5 and abs(at_start - at_end - STEP) <= 1.5
    # the next reading takes over exactly where the last strip stopped
    renderer.new_sample(101.0, 1.0)
    snap = Snapshot(history={"v": [*SPIKE, 10.0]})
    assert abs(spike_x(renderer.render(snap, 101.0 + delay)[0]) - at_end) <= 1
    # without video mode the curve stands still between readings, the newest at the edge
    still = Renderer(theme(spike_graph(), size=(200, 60)))
    snap = Snapshot(history={"v": SPIKE})
    first = spike_x(still.render(snap, 100.0)[0])
    assert first == spike_x(still.render(snap, 100.7)[0])
    assert abs(first - (199 - 2 * STEP)) <= 1.5, first


def test_scrolling_graphs_draw_their_curve_once_per_reading(monkeypatch):
    renderer = Renderer(theme(spike_graph(smooth=True, glow=0.6), size=(200, 60)))
    renderer.continuous = True
    calls = []
    real = renderer._graph_layer
    monkeypatch.setattr(renderer, "_graph_layer", lambda *a: calls.append(1) or real(*a))
    snap = Snapshot(history={"v": [10.0, 50.0, 30.0, 90.0, 20.0]})
    renderer.new_sample(0.0, 1.0)
    for frame in range(50):  # a second of video: 50 windows into one strip
        renderer.render(snap, frame / 50)
    assert len(calls) == 1
    assert renderer.warnings == []


def plateau_end(frame):
    """The x where the curve comes down from a high plateau (its right end)."""
    high = [x for x in range(frame.width) if frame.getpixel((x, 15))[0] > 120]
    return max(high) if high else None


def feed(renderer, readings, start=100.0, interval=1.0):
    """Readings once per ``interval``, a frame rendered at each, as the main loop does."""
    for i in range(1, len(readings) + 1):
        at = start + (i - 1) * interval
        renderer.new_sample(at, interval)
        renderer.render(Snapshot(history={"v": readings[:i]}), at)
    return Snapshot(history={"v": readings})


def test_per_frame_graphs_move_a_pixel_per_frame():
    """As SPUR II: one point per frame, so the curve moves 50 px a second, not a
    few pixels per reading."""
    renderer = Renderer(theme(spike_graph(per_frame=True, h=60), size=(200, 60)))
    renderer.continuous, renderer.fps = True, 50
    delay = renderer.STRIP_DELAY_S
    snap = feed(renderer, [10.0, 90.0, 10.0])  # high from 101 to 102 (no glide)
    ends = [plateau_end(renderer.render(snap, 102.0 + delay + k / 50)[0]) for k in range(11)]
    assert abs(ends[0] - 199) <= 1.5, ends  # the fall at 102 enters at the right edge
    assert all(1 - 0.6 <= a - b <= 1 + 0.6 for a, b in zip(ends, ends[1:], strict=False)), ends
    assert abs(ends[0] - ends[-1] - 10) <= 1, ends
    assert renderer.warnings == []
    # without video mode it is an ordinary graph of the readings
    still = Renderer(theme(spike_graph(per_frame=True), size=(200, 60)))
    plain = Renderer(theme(spike_graph(), size=(200, 60)))
    assert still.render(snap, 102.0)[0].tobytes() == plain.render(snap, 102.0)[0].tobytes()


def test_per_frame_graphs_draw_their_curve_once_per_reading(monkeypatch):
    renderer = Renderer(theme(spike_graph(per_frame=True, glow=0.6), size=(200, 60)))
    renderer.continuous, renderer.fps = True, 50
    calls = []
    real = renderer._graph_layer
    monkeypatch.setattr(renderer, "_graph_layer", lambda *a: calls.append(1) or real(*a))
    snap = feed(renderer, [10.0, 50.0])
    for frame in range(50):  # a second of video: windows into one strip
        renderer.render(snap, 101.0 + frame / 50)
    assert len(calls) == 2  # one strip per reading
    assert renderer.warnings == []


def test_a_per_frame_graph_follows_the_value_bars_and_numbers_glide_along():
    graph = spike_graph(per_frame=True)
    t = theme(graph, bar(smooth=True), animation={"smoothing_ms": 400}, size=(200, 60))
    renderer = Renderer(t, animate=True)
    renderer.continuous, renderer.fps = True, 50
    shown = []
    for i, value in enumerate([10.0, 80.0, 30.0]):
        renderer.new_sample(100.0 + i, 1.0)
        for k in range(50):
            now = 100.0 + i + k / 50
            reading = {"v": Reading("v", value)}
            renderer.render(Snapshot(readings=reading, history={"v": [value]}), now)
            shown.append((now, renderer._anim["b"][0]))
    glide = renderer._glides["g"]
    # The same spring: a bar takes a new reading from the frame before (it advances
    # from its last frame), the graph from the reading's time, so one frame apart.
    for now, value in shown[50:]:  # from the second reading on (the first one starts both)
        assert abs(glide.at(now + 1 / 50) - value) < 0.01, (now, glide.at(now + 1 / 50), value)


def test_a_late_strip_keeps_the_old_one_moving():
    """Video mode draws a new strip in the helper thread; until it is due the
    old strip keeps travelling, so the curve never jumps back."""
    import time as clock

    renderer = Renderer(theme(spike_graph(), size=(200, 60)))
    renderer.continuous = renderer.background_builds = True
    delay = renderer.STRIP_DELAY_S
    renderer.new_sample(100.0, 1.0)
    first = spike_x(renderer.render(Snapshot(history={"v": SPIKE}), 100.9)[0])  # 0.8 of the way
    renderer.new_sample(101.0, 1.0)
    snap = Snapshot(history={"v": [*SPIKE, 10.0]})
    early = spike_x(renderer.render(snap, 101.0)[0])  # the new strip is not due yet
    assert 1 <= first - early <= 3  # still the old strip, 0.9 of the way
    deadline = clock.monotonic() + 5
    while renderer._strips["g"][1] is not None and clock.monotonic() < deadline:
        clock.sleep(0.01)
        renderer.render(snap, 101.0)
    due = spike_x(renderer.render(snap, 101.0 + delay)[0])  # the new strip, where the old ended
    assert abs(due - (first - 0.2 * STEP)) <= 1.5, (first, early, due)
    renderer.close()


class LateBuilder:
    """Runs a strip's job only after the render call that made it returned, as
    the helper thread does in video mode."""

    def __init__(self):
        self.jobs = []

    def submit(self, job):
        from concurrent.futures import Future

        future = Future()
        self.jobs.append((future, job))
        return future

    def run(self):
        jobs, self.jobs = self.jobs, []
        for future, job in jobs:
            future.set_result(job())

    def close(self):
        pass


@pytest.mark.parametrize("per_frame", [True, False])
def test_graphs_keep_moving_when_their_strips_come_from_the_helper_thread(monkeypatch, per_frame):
    """A strip built after its render call returned is still the one asked for:
    the curve goes on moving (per-frame graphs froze after the first strips) and
    each reading draws one strip (scrolling ones were drawn on every frame)."""
    renderer = Renderer(theme(spike_graph(per_frame=per_frame), size=(200, 60)))
    renderer.continuous = renderer.background_builds = True
    renderer.fps = 50
    renderer._builder = builder = LateBuilder()
    calls = []
    real = renderer._graph_layer
    monkeypatch.setattr(renderer, "_graph_layer", lambda *a: calls.append(1) or real(*a))
    readings, previous, changed = [], None, []
    for second in range(8):
        at = 100.0 + second
        readings.append(90.0 if second % 2 else 10.0)
        renderer.new_sample(at, 1.0)
        snap = Snapshot(history={"v": list(readings)})
        for k in range(50):
            frame = renderer.render(snap, at + k / 50)[0].tobytes()
            builder.run()
            if frame != previous:
                changed.append(at + k / 50)
            previous = frame
    assert changed[-1] > 107.5, changed[-5:]  # still moving in the last second
    assert len(calls) <= 8 + 1, len(calls)  # one strip per reading
    assert renderer.warnings == []


def test_values_glide_until_the_next_reading_in_video_mode():
    t = theme(bar(smooth=True), animation={"smoothing_ms": 400})

    def glide(continuous):
        renderer = Renderer(t, animate=True)
        renderer.continuous = continuous
        renderer.new_sample(0.0, 2.0)  # readings every 2 s
        renderer.render(Snapshot(readings={"v": Reading("v", 0.0)}), 0.0)
        values = []
        for i in range(1, 21):
            renderer.render(Snapshot(readings={"v": Reading("v", 100.0)}), i / 10)
            values.append(renderer._anim["b"][0])
        return values

    quick, slow = glide(False), glide(True)
    assert quick[5] > 99 and slow[5] < 90  # 0.6 s in: there, still on its way
    assert slow[-1] > 90  # nearly there when the next reading comes
    speeds = [b - a for a, b in zip(slow, slow[1:], strict=False)]
    assert speeds[0] < speeds[2]  # a spring starts gently: no kink when a reading comes


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


@pytest.mark.parametrize("mode", ["png", "video", "per-frame"])
@pytest.mark.parametrize(
    "theme_id", ["spur-ii", "studio", "libre-default", "orbit", "slate", "column", "pico"]
)
def test_incremental_frames_equal_full_renders(theme_id, mode):
    """Composing only changed regions gives exactly the frame a full render gives
    (in video mode too, where graphs scroll on every frame, also when the layers
    under a graph that moves per frame are kept)."""
    from datetime import datetime, timedelta

    from PIL import ImageChops

    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider
    from libre_panel.theme.model import find_theme, load_theme

    theme = load_theme(find_theme(theme_id))
    if mode == "per-frame":
        for widget in theme.widgets:
            if widget["type"] == "graph":
                widget["per_frame"] = True
    fast, full = Renderer(theme, animate=True), Renderer(theme, animate=True)
    full.incremental = False
    fast.continuous = full.continuous = mode != "png"
    fast.fps = full.fps = 25
    hub = SensorHub([DemoProvider({})])
    snapshot = hub.snapshot()
    start = datetime(2026, 9, 27, 23, 59, 58)
    for i in range(120):
        if i % 25 == 0:
            snapshot = hub.snapshot()  # new readings: bars glide, graphs move
            for renderer in (fast, full):
                renderer.new_sample(1000.0 + i / 25, 1.0)
        snapshot.now = start + timedelta(seconds=i / 25)  # seconds tick, the date changes
        now = 1000.0 + i / 25
        a, boxes_a = fast.render(snapshot, now)
        b, boxes_b = full.render(snapshot, now)
        assert boxes_a == boxes_b
        assert ImageChops.difference(a, b).getbbox() is None, f"frame {i} differs"


def test_background_builds_keep_frames_coming():
    """In video mode a slow piece is built in the helper thread: the frame goes
    out at once with the previous piece, and the new one follows."""
    import threading
    import time as clock
    from datetime import datetime

    from PIL import ImageChops

    from libre_panel.sensors.base import Reading
    from libre_panel.sensors.demo import demo_snapshot

    theme = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "name": "t",
            "display": {"width": 120, "height": 40},
            "widgets": [{"type": "metric", "id": "v", "x": 0, "y": 0, "sensor": "cpu.load"}],
        }
    )
    renderer = Renderer(theme)
    renderer.background_builds = True
    snapshot = demo_snapshot()
    snapshot.now = datetime(2026, 9, 27, 12, 0)
    snapshot.readings["cpu.load"] = Reading("cpu.load", 10.0, "%", "CPU")
    first, _ = renderer.render(snapshot, 1.0)  # first frame: built right away

    gate = threading.Event()
    slow = renderer._text_piece

    def blocked_text_piece(*args):
        gate.wait(5)
        return slow(*args)

    renderer._text_piece = blocked_text_piece
    snapshot.readings["cpu.load"] = Reading("cpu.load", 99.0, "%", "CPU")
    started = clock.perf_counter()
    during, _ = renderer.render(snapshot, 2.0)
    assert clock.perf_counter() - started < 0.5  # did not wait for the build
    assert ImageChops.difference(first, during).getbbox() is None  # still the old value
    gate.set()
    for _ in range(100):
        after, _ = renderer.render(snapshot, 3.0)
        if ImageChops.difference(first, after).getbbox() is not None:
            break
        clock.sleep(0.02)
    assert ImageChops.difference(first, after).getbbox() is not None  # the new value arrived
    renderer.close()


def test_needs_shows_a_widget_only_with_its_sensor():
    """A card has no sensor of its own; needs ties it to one (! turns it round)."""
    card = {"type": "rect", "id": "card", "x": 0, "y": 0, "w": 40, "h": 40,
            "color": "#ff0000", "needs": "weather.temperature"}  # fmt: skip
    other = {"type": "rect", "id": "other", "x": 50, "y": 0, "w": 40, "h": 40,
             "color": "#00ff00", "needs": "!weather.temperature"}  # fmt: skip
    t = theme(card, other, size=(100, 50))
    off, boxes = Renderer(t).render(Snapshot())
    assert set(boxes) == {"other"} and off.getpixel((20, 20))[:3] != (255, 0, 0)
    on, boxes = Renderer(t).render(
        Snapshot(readings={"weather.temperature": Reading("weather.temperature", 12.0)})
    )
    assert set(boxes) == {"card"} and on.getpixel((20, 20))[:3] == (255, 0, 0)

"""Rendering features: palette, fonts, effects, gradients, icons, animation."""

import pytest

from libre_panel.config import ConfigError, parse_config
from libre_panel.icons import ICON_NAMES, draw_icon, weather_icon_name
from libre_panel.render.renderer import Renderer
from libre_panel.render.renderer import _Strip as Strip_
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


@pytest.mark.parametrize("radius", [0, 3, 12])
@pytest.mark.parametrize("outline", [None, "#ff000080"])
@pytest.mark.parametrize("color2", [None, "#0000ff"])
def test_cards_drawn_at_their_size_look_as_supersampled(radius, outline, color2):
    """Only a card's round corners are drawn three times larger: the same look
    (within two levels) as drawing it all three times larger, far faster."""
    from PIL import ImageChops

    card = rect(radius=radius, outline=outline, outline_width=2, color="#20304080",
                color2=color2, backdrop_blur=4)  # fmt: skip
    renderer = Renderer(theme(card))
    widget = renderer.widgets[0]
    fast = renderer._card(widget, 50, 25, 100, 50)
    renderer._card = lambda *args: None
    whole = renderer._draw_rect(widget, Snapshot(), 0.0)
    assert fast is not None and fast.layer.size == whole.layer.size
    shown = ImageChops.difference(fast.layer.convert("RGBa"), whole.layer.convert("RGBa"))
    assert max(band[1] for band in shown.getextrema()) <= 2
    assert ImageChops.difference(fast.backdrop, whole.backdrop).getextrema()[1] <= 2


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


def flat_graph(**kw):
    return {"type": "graph", "id": "g", "x": 0, "y": 0, "w": 100, "h": 60, "sensor": "v",
            "history": 10, "min": 0, "max": 100, "color": "#ffffff", "smooth": False,
            "fill_fade": False, "line_width": 1, **kw}  # fmt: skip


def test_without_inset_a_graphs_range_spans_its_box_as_spur_iis_charts():
    def line(**kw):  # the middle of the line, weighed by its light, at 0, 50 and 100
        rows = []
        for value in (0.0, 50.0, 100.0):
            snap = Snapshot(history={"v": [value] * 10})
            graph = flat_graph(fill=False, line_width=2, **kw)
            frame, _ = Renderer(theme(graph, size=(100, 60))).render(snap)
            column = [(y, frame.getpixel((50, y))[0]) for y in range(60)]
            lit = [(y + 0.5, v) for y, v in column if v > 0]
            rows.append(sum(y * v for y, v in lit) / sum(v for _, v in lit))
        return rows

    bottom, middle, top = line(inset=False)  # values to rows: 59 at 0, 0 at 100
    assert bottom == pytest.approx(59, abs=0.25) and top == pytest.approx(0.5, abs=0.25)
    assert middle == pytest.approx(29.5, abs=0.25)  # half of it cut at the edges
    inside = line()  # the line a line's width inside the box
    assert inside[0] < bottom - 1 and inside[2] > top + 1
    frame, _ = Renderer(theme(flat_graph(inset=False), size=(100, 60))).render(
        Snapshot(history={"v": [50.0] * 10})
    )
    assert frame.getpixel((50, 59))[0] < frame.getpixel((50, 50))[0]  # filled down to row 59


def test_a_downward_graph_hangs_from_its_top_and_is_filled_from_there():
    snap = Snapshot(history={"v": [25.0] * 10})
    up, _ = Renderer(theme(flat_graph(), size=(100, 60))).render(snap)
    down, _ = Renderer(theme(flat_graph(direction="down"), size=(100, 60))).render(snap)

    def line(frame):  # the middle of the line, weighed by its light
        rows = [(y, frame.getpixel((50, y))[0]) for y in range(60)]
        rows = [(y, v) for y, v in rows if v > 100]
        return sum(y * v for y, v in rows) / sum(v for _, v in rows)

    assert line(down) < 20 and line(up) + line(down) == pytest.approx(59, abs=0.5)  # mirrored
    assert down.getpixel((50, 5)) == (64,) * 3 and down.getpixel((50, 40)) == BG
    faded = flat_graph(direction="down", fill_fade=True)
    frame, _ = Renderer(theme(faded, size=(100, 60))).render(snap)
    assert 0 < frame.getpixel((50, 1))[0] < frame.getpixel((50, 12))[0]  # it fades upwards


def spike_graph(**kw):
    """A graph of 11 readings that spans their 10 seconds in video mode too (without its own
    ``seconds`` a graph moves a pixel per point, 25 a second, as SPUR II's)."""
    span = {} if kw.get("per_frame") else {"seconds": 10}  # (per_frame: a pixel per point)
    return {"type": "graph", "id": "g", "x": 0, "y": 0, "w": 200, "h": 60, "sensor": "v",
            "history": 11, **span, "min": 0, "max": 100, "fill": False, "color": "#ffffff",
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


def reading(value, history):
    """A snapshot as the hub makes it: the reading, and the readings so far."""
    return Snapshot(readings={"v": Reading("v", value)}, history={"v": list(history)})


def test_graphs_scroll_between_readings_in_video_mode():
    """In video mode the curve moves on every frame, a reading's width a
    second, and its newest point at the right edge is the value now."""
    renderer = Renderer(theme(spike_graph(), size=(200, 60)))
    renderer.continuous = True
    renderer.new_sample(100.0, 1.0)
    snap = reading(10.0, SPIKE)
    at_start = spike_x(renderer.render(snap, 100.0)[0])
    halfway = spike_x(renderer.render(snap, 100.5)[0])
    at_end = spike_x(renderer.render(snap, 101.0)[0])
    assert renderer.moving
    # the spike was read from 98 to 99 s: at 100 s, one and a half readings from the edge
    assert abs(at_start - (199 - 1.5 * STEP)) <= 1.5, at_start
    assert abs(at_start - halfway - STEP / 2) <= 1 and abs(at_start - at_end - STEP) <= 1
    # the next reading goes on from there
    renderer.new_sample(101.0, 1.0)
    assert abs(spike_x(renderer.render(reading(10.0, [*SPIKE, 10.0]), 101.0)[0]) - at_end) <= 1
    # without video mode the curve stands still between readings, the newest at the edge
    still = Renderer(theme(spike_graph(), size=(200, 60)))
    snap = Snapshot(history={"v": SPIKE})
    first = spike_x(still.render(snap, 100.0)[0])
    assert first == spike_x(still.render(snap, 100.7)[0])
    assert abs(first - (199 - 2 * STEP)) <= 1.5, first


def test_a_slow_graph_moves_on_evenly_from_frame_to_frame():
    """Lumen's graphs, a minute across, move a sixth of a pixel a frame: about that on
    every frame, not half a pixel on every third or fourth (the curve stood in between)."""
    import numpy

    renderer = Renderer(theme(spike_graph(w=470, seconds=60), size=(470, 60)))
    renderer.continuous, renderer.fps = True, 50
    renderer.new_sample(100.0, 1.0)
    snap = reading(10.0, SPIKE)
    where = []
    for k in range(40):
        frame = numpy.asarray(renderer.render(snap, 100.0 + k / 50)[0].convert("L"), float)
        top = frame[:30]  # only the spike reaches up here
        where.append((top.sum(axis=0) * numpy.arange(470)).sum() / top.sum())
    steps = numpy.diff(where)
    speed = 469 / 60 / 50  # pixels a frame
    assert numpy.all(steps < 0)  # it moves left on every frame
    assert numpy.allclose(steps, -speed, atol=speed * 0.4), steps
    assert numpy.mean(steps) == pytest.approx(-speed, rel=0.05)


def test_scrolling_graphs_draw_their_curve_once_per_reading(monkeypatch):
    renderer = Renderer(theme(spike_graph(smooth=True, glow=0.6), size=(200, 60)))
    renderer.continuous = True
    calls = []
    real = renderer._graph_layer
    monkeypatch.setattr(renderer, "_graph_layer", lambda *a, **k: calls.append(1) or real(*a, **k))
    snap = reading(20.0, [10.0, 50.0, 30.0, 90.0, 20.0])
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
        renderer.render(reading(readings[i - 1], readings[:i]), at)
    return reading(readings[-1], readings)


@pytest.mark.parametrize("fps", [25, 50])
def test_per_frame_graphs_move_a_pixel_per_point(fps):
    """As SPUR II: a point every 1/25 s, a pixel apart, so the curve moves 25 px
    a second at any frame rate (half a pixel per frame at 50 fps), and its
    newest point is the value now."""
    renderer = Renderer(theme(spike_graph(per_frame=True, h=60), size=(200, 60)))
    renderer.continuous, renderer.fps = True, fps
    snap = feed(renderer, [10.0, 90.0, 10.0])  # high from 101 to 102 (no glide)
    ends = [plateau_end(renderer.render(snap, 102.0 + k / fps)[0]) for k in range(fps // 2 + 1)]
    # the fall at 102 s is at the right edge (it glides from the frame before)
    assert 199 - 25 / fps - 1.5 <= ends[0] <= 199, ends
    assert all(0 <= a - b <= 1.5 for a, b in zip(ends, ends[1:], strict=False)), ends
    assert abs(ends[0] - ends[-1] - 25 * (fps // 2) / fps) <= 1.5, ends
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
    monkeypatch.setattr(renderer, "_graph_layer", lambda *a, **k: calls.append(1) or real(*a, **k))
    snap = feed(renderer, [10.0, 50.0])
    for frame in range(50):  # a second of video: windows into one strip
        renderer.render(snap, 101.0 + frame / 50)
    assert len(calls) == 2  # one strip per reading
    assert renderer.warnings == []


def head_y(frame, x):
    """The middle of the curve's bright line in column ``x``."""
    rows = [y for y in range(60) if frame.getpixel((x, y))[0] > 120]
    return (rows[0] + rows[-1] + 1) / 2


def test_a_graphs_newest_point_is_the_value_bars_and_numbers_show():
    """The curve's head is the gliding value of its sensor, the same spring
    the bar follows: it meets the number instead of trailing it."""
    graph = spike_graph(per_frame=True, line_width=2)
    t = theme(graph, bar(smooth=True, y=70), animation={"smoothing_ms": 400}, size=(200, 90))
    renderer = Renderer(t, animate=True)
    renderer.continuous, renderer.fps = True, 50
    for i, value in enumerate([10.0, 80.0, 30.0]):
        renderer.new_sample(100.0 + i, 1.0)
        for k in range(50):
            now = 100.0 + i + k / 50
            frame = renderer.render(reading(value, [value]), now)[0]
            if now - 0.08 < 100.0:
                continue  # nothing read yet two pixels in from the edge
            # two pixels in from the newest point (the right edge), about 0.08 s ago: a
            # reading late, the curve would be up to 39 px off there. Where the spring was
            # in the frames around then (its points lie on a 25 Hz grid, and the start of
            # a 0.4 s glide is steep), a pixel or two either way (a 2 px line on a steep
            # stretch spans a few rows)
            values = [
                renderer.motion.value("v", now - ago) for ago in (0.04, 0.06, 0.08, 0.1, 0.12)
            ]
            rows = [(4 + 111 * (1 - v / 100)) / 2 for v in values if v is not None]  # 2x drawn
            head = head_y(frame, 197)
            assert min(rows) - 3 <= head <= max(rows) + 3, (now, head, rows)
            filled = sum(1 for x in range(0, 200, 2) if frame.getpixel((x, 80))[0] > 200) * 2
            assert abs(filled - 2 * renderer.motion.value("v", now)) <= 4  # the bar's value


def test_the_curve_ends_at_the_graphs_edges():
    """In video mode a strip reaches past the window; beside the graph only
    its glow shows, not the curve."""
    graph = spike_graph(per_frame=True, x=20, line_width=2, glow=0.6, glow_radius=6)
    renderer = Renderer(theme(graph, size=(240, 60)))
    renderer.continuous, renderer.fps = True, 50
    frame = feed(renderer, [90.0, 90.0]) and renderer.render(reading(90.0, [90.0]), 101.5)[0]
    curve_y = round((2 + 115 * 0.1) / 2)  # 90 %: the line's row
    assert frame.getpixel((210, curve_y))[0] > 200  # inside: the curve
    beside = [frame.getpixel((x, curve_y))[0] for x in (*range(4, 18), *range(222, 236))]
    assert 0 < max(beside) < 200, beside  # beside: some glow, no curve


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


def test_a_late_strip_keeps_the_curve_where_it_is():
    """Video mode draws strips in the helper thread. Until a late one comes,
    the end of the curve is drawn again over the old one once a new reading
    shows: frame by frame the curve is what strips drawn on time show."""
    from PIL import ImageChops

    graph = spike_graph(per_frame=True, line_width=2, glow=0.5, glow_radius=6)
    late = Renderer(theme(graph, animation={"smoothing_ms": 400}, size=(200, 60)), animate=True)
    late.continuous = late.background_builds = True
    late._strip_builder = builder = LateBuilder()
    on_time = Renderer(theme(graph, animation={"smoothing_ms": 400}, size=(200, 60)), animate=True)
    on_time.continuous = True
    late.fps = on_time.fps = 50
    for i, value in enumerate([20.0, 85.0, 40.0, 70.0]):
        for renderer in (late, on_time):
            renderer.new_sample(100.0 + i, 1.0)
        for k in range(50):
            now = 100.0 + i + k / 50
            a = late.render(reading(value, [value]), now)[0]
            b = on_time.render(reading(value, [value]), now)[0]
            difference = max(band[1] for band in ImageChops.difference(a, b).getextrema())
            assert difference <= 8, (now, difference)
            if k == 30:  # the strips asked for come more than half a second late
                builder.run()
    assert late.warnings == [] and on_time.warnings == []


@pytest.mark.parametrize("per_frame", [True, False])
def test_graphs_keep_moving_when_their_strips_come_from_the_helper_thread(monkeypatch, per_frame):
    """A strip built after its render call returned is still the one asked for:
    the curve goes on moving (per-frame graphs froze after the first strips) and
    each reading draws one strip (scrolling ones were drawn on every frame)."""
    renderer = Renderer(theme(spike_graph(per_frame=per_frame), size=(200, 60)))
    renderer.continuous = renderer.background_builds = True
    renderer.fps = 50
    renderer._strip_builder = builder = LateBuilder()
    calls = []
    real = renderer._graph_layer
    monkeypatch.setattr(renderer, "_graph_layer", lambda *a, **k: calls.append(1) or real(*a, **k))
    readings, previous, changed = [], None, []
    for second in range(8):
        at = 100.0 + second
        readings.append(90.0 if second % 2 else 10.0)
        renderer.new_sample(at, 1.0)
        snap = reading(readings[-1], readings)
        for k in range(50):
            frame = renderer.render(snap, at + k / 50)[0].tobytes()
            builder.run()
            if frame != previous:
                changed.append(at + k / 50)
            previous = frame
    assert changed[-1] > 107.5, changed[-5:]  # still moving in the last second
    assert len(calls) <= 8 + 1, len(calls)  # one strip per reading
    assert renderer.warnings == []


def test_values_glide_as_long_as_the_theme_says_in_video_mode_too():
    """A reading is there after the theme's smoothing_ms and then holds, in video mode as on
    still frames, also when readings come seldom: stretched to the next reading, values
    followed half as fast as the SPUR II mod's."""
    t = theme(bar(smooth=True), animation={"smoothing_ms": 400})

    def glide(continuous):
        renderer = Renderer(t, animate=True)
        renderer.continuous = continuous
        renderer.new_sample(0.0, 2.0)  # readings every 2 s
        renderer.render(Snapshot(readings={"v": Reading("v", 0.0)}), 0.0)
        values = []
        for i in range(1, 21):
            renderer.render(Snapshot(readings={"v": Reading("v", 100.0)}), i / 20)
            values.append(renderer.motion.value("v", i / 20))
        return values

    still, video = glide(False), glide(True)
    assert video == still
    assert 50 < video[3] < 95 and video[7] > 94  # 0.2 s in: on its way; 0.4 s: there
    steps = [b - a for a, b in zip([0.0, *video], video, strict=False)]
    assert steps[0] < steps[1]  # a spring starts gently: no kink when a reading comes


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
    # in fractions of a pixel too: a word set to a width (SPUR II's wordmark)
    exact = theme(text("SPUR", tabular=False, letter_spacing=3.62))
    assert exact.widgets[0]["letter_spacing"] == 3.62
    spaced = [render(theme(text("SPUR", tabular=False, letter_spacing=gap)), {"v": 1.0})[1]["m"][2]
              for gap in (3, 3.62, 4)]  # fmt: skip
    assert spaced[0] < spaced[1] < spaced[2]


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
        {"type": "bar", "id": "b", "x": 20, "y": 30, "glow_area": [10, 20, 220, 40]},
        {"type": "metric", "id": "name", "x": 240, "y": 300, "max_width": 120},
        size=(480, 320),
    ).to_dict()
    out = adapt_theme(data, "turing-5", "landscape")  # 800x480: s = 1.5
    widgets = {w["id"]: w for w in out["widgets"]}
    assert widgets["i"]["size"] == 60
    assert widgets["t"]["letter_spacing"] == 6 and widgets["t"]["glow_radius"] == 15
    assert widgets["b"]["glow_area"] == [17, 30, 367, 60]  # a box on the panel, as x and w
    assert widgets["name"]["max_width"] == 200 and widgets["t"]["max_width"] == 0  # 0: any
    narrow = adapt_theme(data, "turing-3.5", "portrait")  # 320x480: the room narrows too
    assert {w["id"]: w for w in narrow["widgets"]}["name"]["max_width"] == 80


def test_adapt_scales_the_end_dot_the_mark_and_the_moon():
    """Pixel sizes that came with 0.5 were left as they were: a dot of 9 px on a graph a sixth
    as big, a mark as wide as the bar was thin, a moon as big as before."""
    data = theme(
        {"type": "graph", "id": "dot", "x": 20, "y": 20, "w": 300, "h": 100, "end_dot": 6},
        {"type": "graph", "id": "small", "x": 20, "y": 140, "w": 300, "h": 100, "end_dot": 2},
        {"type": "graph", "id": "none", "x": 20, "y": 260, "w": 300, "h": 40},
        {"type": "bar", "id": "bar", "x": 20, "y": 300, "w": 200, "h": 16, "mark_width": 4},
        {"type": "moon", "id": "moon", "x": 300, "y": 20, "size": 60},
        size=(480, 320),
    ).to_dict()
    out = adapt_theme(data, "turing-5", "landscape")  # 800x480: s = 1.5
    widgets = {w["id"]: w for w in out["widgets"]}
    assert widgets["dot"]["end_dot"] == 9.0 and widgets["bar"]["mark_width"] == 6
    assert widgets["none"]["end_dot"] == 0  # none stays none
    moon = widgets["moon"]
    assert moon["size"] == 90 and moon["x"] + 45 == round((300 + 30) * 800 / 480)
    tiny = {w["id"]: w for w in adapt_theme(data, "weact-0.96", "landscape")["widgets"]}  # s = 0.25
    assert tiny["small"]["end_dot"] == 1.0  # a dot is never rounded away
    assert tiny["bar"]["mark_width"] == 1 and tiny["moon"]["size"] == 15


def test_adapt_keeps_a_rule_at_the_far_edge_on_the_frame():
    """A box 4 px from the right edge of 1920 px was at 80 on a 160 px panel's 80 px: past it,
    and nothing of the frame line showed."""
    data = theme(
        {"type": "rect", "id": "right", "x": 1911, "y": 20, "w": 5, "h": 100},
        {"type": "rect", "id": "floor", "x": 20, "y": 478, "w": 1000, "h": 2},
        size=(1920, 480),
    ).to_dict()
    for orientation in ("landscape", "portrait"):
        out = adapt_theme(data, "weact-0.96", orientation)
        for widget in out["widgets"]:
            assert widget["x"] < out["display"]["width"], (orientation, widget["id"])
            assert widget["y"] < out["display"]["height"], (orientation, widget["id"])


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


def test_needs_can_ask_for_what_a_reading_is_or_for_a_picture():
    """``key=text`` holds while the reading is that (numbers by value, texts in any case),
    ``@name`` while a source or service has the picture; ``!`` turns each round."""
    from PIL import Image

    def card(wid, needs):
        return rect(id=wid, x=0, y=0, w=10, h=10, needs=needs)

    t = theme(
        card("gpu", "game.limiter=gpu"), card("cpu", "game.limiter = CPU"),
        card("not-gpu", "!game.limiter=gpu"), card("playing", "media.playing=1"),
        card("paused", "media.playing=0"), card("both", "game.limiter=gpu, media.playing=1"),
        card("logo", "@game.logo"), card("no-logo", "!@game.logo"),
    )  # fmt: skip

    def shown(readings, images=None):
        found = {k: Reading(k, v) for k, v in readings.items()}
        snap = Snapshot(readings=found, images=images or {})
        return set(Renderer(t, preview=True).render(snap)[1])

    assert shown({}) == {"not-gpu", "no-logo"}  # no reading is not "gpu"
    assert shown({"game.limiter": "GPU", "media.playing": 1.0}) == {
        "gpu", "playing", "both", "no-logo"
    }  # fmt: skip
    assert shown({"game.limiter": "cpu ", "media.playing": 0}) == {
        "cpu", "not-gpu", "paused", "no-logo"
    }  # fmt: skip
    assert shown({"game.limiter": 3}) == {"not-gpu", "no-logo"}  # a number is no text to match
    assert shown({}, {"game.logo": Image.new("RGBA", (4, 4))}) == {"not-gpu", "logo"}
    # what a theme asks for is what the sources are told to work on, a picture's source too
    from libre_panel.render.renderer import _reads

    assert _reads(t.widgets) == {"game.limiter", "media.playing", "game.logo"}


def own_range_graph(**kw):
    """A graph on its own range (no ``min``, no ``max``), as the game's frame rate."""
    return spike_graph(min=None, max=None, **kw)


def test_a_graph_on_its_own_range_glides_instead_of_jumping():
    """Arena's frame rate on the 12.3" scaled up and down in jumps: its range was the values'
    of every picture, so a peak coming into the window or leaving it moved the whole curve at
    once. The range keeps room around the values, widens at once where they leave it (they
    glide themselves), narrows only once they used little of it for a while, and glides from
    one range to the next. No value is ever outside it."""
    renderer = Renderer(theme(own_range_graph(), size=(200, 60)), animate=True)
    widget = renderer.widgets[0]
    bounds, moving = renderer._glided_bounds(widget, [100.0, 140.0], 0.0)
    assert bounds == pytest.approx((96.0, 144.0)) and not moving  # a tenth of the span as room
    bounds, moving = renderer._glided_bounds(widget, [100.0, 200.0], 0.1)  # a peak comes in
    assert bounds[1] >= 200.0 and moving  # the peak inside at once ...
    highs = [
        renderer._glided_bounds(widget, [100.0, 200.0], 0.1 + k / 10)[0][1] for k in range(1, 30)
    ]
    assert highs[-1] == pytest.approx(210.0, abs=0.5)  # ... and room above it soon after
    assert all(b >= a for a, b in zip(highs, highs[1:], strict=False))
    # the peak leaves the window: the range stays for a while, then narrows, gliding
    shown = []
    for k in range(1, 100):
        (low, high), _ = renderer._glided_bounds(widget, [100.0, 140.0], 3.0 + k / 10)
        assert low <= 100.0 and high >= 140.0
        shown.append(high)
    assert shown[10] == pytest.approx(210.0, abs=0.5)  # a second later: as it was
    assert shown[-1] == pytest.approx(144.0, abs=0.5)  # in the end: the values', with room
    assert max(a - b for a, b in zip(shown, shown[1:], strict=False)) < 0.2 * (210 - 90)
    fixed = Renderer(theme(spike_graph(), size=(200, 60)), animate=True)  # a range of its own
    assert fixed._glided_bounds(fixed.widgets[0], [10.0, 20.0], 0.0) == ((0, 100), False)


def test_between_two_strips_the_window_is_stretched_to_the_range_of_the_moment():
    """In video mode the curve goes onto strips, each drawn in one range; while the range
    glides, every frame stretches the window it shows to the range of that moment, so it
    moves on every frame, not in the strips' steps: as if drawn in that range."""
    import numpy

    renderer = Renderer(theme(own_range_graph(line_width=2), size=(200, 60)))
    widget = renderer.widgets[0]
    values = [100.0 + 40.0 * ((k % 20) / 19) for k in range(400)]

    def drawn(bounds):
        strip = renderer._draw_strip(widget, values, 0, 1.0, 1, Strip_(0, 0.0, 99.0, ()), bounds)
        return strip.phases[0].crop((20, 0, 180, 60))

    def rows(layer):  # the line's row in every column
        alpha = numpy.asarray(layer.getchannel("A"), float)
        return (alpha * numpy.arange(60)[:, None]).sum(axis=0) / alpha.sum(axis=0)

    first, now = (95.0, 145.0), (92.0, 150.0)
    stretched = renderer._restretch(widget, drawn(first), 0, first, now)
    assert numpy.abs(rows(stretched) - rows(drawn(now))).max() < 0.6
    assert numpy.abs(rows(drawn(first)) - rows(drawn(now))).max() > 2  # (it does move)


def test_while_its_range_glides_a_graph_asks_for_its_strips_more_often():
    renderer = Renderer(theme(own_range_graph(), size=(200, 60)), animate=True)
    renderer.continuous, renderer.fps = True, 50
    renderer.new_sample(100.0, 1.0)
    renderer.render(reading(10.0, SPIKE), 100.0)
    strips = renderer._strips["g"]
    assert renderer.STRIP_GLIDE_S < renderer.STRIP_EVERY_S
    strips.ready.gliding = True
    strips.asked = 100.0
    renderer.render(reading(10.0, SPIKE), 100.0 + renderer.STRIP_GLIDE_S + 0.01)
    assert strips.asked > 100.0  # asked again, before STRIP_EVERY_S


def test_a_clipped_graph_keeps_its_glow_between_its_sides():
    """The game's graph runs from one edge of its card to the other: its glow must not
    shine past them (``clip``); above and below it stays."""
    glowing = own_range_graph(x=40, w=120, glow=0.6, glow_radius=6, fill=True)
    for clip in (False, True):
        renderer = Renderer(theme({**glowing, "clip": clip}, size=(200, 60)))
        frame, _ = renderer.render(Snapshot(history={"v": SPIKE}))
        lit = [x for x in range(200) if any(frame.getpixel((x, y))[0] > 8 for y in range(60))]
        if clip:
            assert min(lit) >= 40 and max(lit) <= 159
        else:
            assert min(lit) < 40 or max(lit) > 159  # (it reaches past them otherwise)

"""Motion kept by the main loop: gliding values, fading colours, graph histories."""

import math

import pytest

from libre_panel.render.motion import PAUSE_S, Motion
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import THEME_FORMAT, parse_theme


def theme(*widgets, size=(200, 100), smoothing_ms=400):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#000000"},
            "animation": {"smoothing_ms": smoothing_ms},
            "widgets": list(widgets),
        }
    )


def bar(wid="b", sensor="v", **kw):
    return {"type": "bar", "id": wid, "x": 0, "y": 0, "w": 200, "h": 20, "sensor": sensor,
            "radius": 0, "background": "#202020", "color": "#ff0000", "smooth": True,
            **kw}  # fmt: skip


def snap(**values):
    return Snapshot(readings={k: Reading(k, v) for k, v in values.items()})


def test_widgets_of_one_sensor_glide_together_and_none_stays_none():
    renderer = Renderer(theme(bar("a"), bar("b", y=40, max=200)), animate=True)
    renderer.render(snap(v=0.0), 0.0)
    renderer.render(snap(v=100.0), 0.1)
    value = renderer.motion.value("v", 0.1)
    assert 0 < value < 100 and renderer.moving  # one spring for both bars
    assert renderer._eased(bar("a"), 100.0, 0.1) == renderer._eased(bar("b"), 100.0, 0.1)
    renderer.render(snap(v=None), 0.2)
    assert renderer.motion.value("v", 0.2) is None  # no reading: shown as missing, not as 0
    assert renderer._eased(bar("a"), None, 0.2) is None


def test_a_value_off_the_panel_starts_again_where_it_is():
    """A value no frame drew for a while (another theme did not show it) does
    not catch up a glide nobody saw: it starts at its reading."""
    motion = Motion()
    motion.begin({}, 0.0, 0.5, 0.02)
    motion.glide("v", 0.0, 0.0, 0.5, 0.02)
    for k in range(1, 30):  # frames without "v", 20 ms apart
        motion.begin({}, k / 50, 0.5, 0.02)
    now = 30 / 50
    motion.begin({}, now, 0.5, 0.02)
    assert now > PAUSE_S
    assert motion.glide("v", 100.0, now, 0.5, 0.02) == (100.0, 0.0)
    # drawn on every frame, it glides
    motion.glide("w", 0.0, now, 0.5, 0.02)
    motion.begin({}, now + 0.02, 0.5, 0.02)
    assert motion.glide("w", 100.0, now + 0.02, 0.5, 0.02)[0] < 50


def test_the_png_paths_pauses_do_not_restart_a_glide():
    """Without video the loop sleeps up to half a second while nothing moves;
    the value stood on the panel all the while, so a new reading glides."""
    motion = Motion()
    motion.begin({}, 0.0, 0.45, 0.1)
    motion.glide("v", 0.0, 0.0, 0.45, 0.1)
    motion.begin({}, 0.5, 0.45, 0.1)  # the next frame, half a second later
    value, speed = motion.glide("v", 100.0, 0.5, 0.45, 0.1)
    assert 0 < value < 50 and speed > 0


def test_values_glide_the_same_at_25_30_and_50_fps():
    halfway = {}
    for fps in (25, 30, 50):
        renderer = Renderer(theme(bar()), animate=True)
        renderer.fps = fps
        for n in range(round(1.5 * fps)):
            now = 10.0 + n / fps
            renderer.render(snap(v=0.0 if now < 10.5 else 100.0), now)
        halfway[fps] = renderer.motion.value("v", 10.7)  # 0.2 s into a 0.4 s glide
    assert all(60 < value < 85 for value in halfway.values()), halfway
    assert max(halfway.values()) - min(halfway.values()) < 5, halfway


def test_a_new_theme_goes_on_from_where_the_last_one_was():
    """The main loop hands its motion to the next renderer: a value mid-glide
    goes on gliding in the new theme instead of starting over."""
    motion = Motion()
    first = Renderer(theme(bar()), animate=True, motion=motion)
    first.fps = 50
    first.render(snap(v=0.0), 0.0)
    for k in range(1, 6):
        first.render(snap(v=100.0), k / 50)
    before = motion.value("v", 5 / 50)
    second = Renderer(theme(bar("other", y=40)), animate=True, motion=motion)
    second.fps = 50
    second.render(snap(v=100.0), 6 / 50)
    after = motion.value("v", 6 / 50)
    assert 0 < before < after < 100  # one frame further on, not back at 0, not jumped to 100


def test_previews_keep_their_own_motion():
    loop = Motion()
    panel = Renderer(theme(bar()), animate=True, motion=loop)
    panel.render(snap(v=40.0), 0.0)
    preview = Renderer(theme(bar()), animate=True, preview=True)
    preview.render(snap(v=90.0), 0.05)
    assert preview.motion is not loop and loop.value("v", 0.05) == 40.0


def metric(wid="m", **kw):
    return {"type": "metric", "id": wid, "x": 0, "y": 40, "sensor": "v", "format": "{value:.0f}",
            "font_size": 30, **kw}  # fmt: skip


def shown_text(renderer, wid):
    return renderer._cache[wid][0][0]


def test_numbers_glide_with_smooth_and_read_the_reading_without():
    renderer = Renderer(theme(bar(), metric(smooth=True), metric("raw", y=70)), animate=True)
    renderer.fps = 50
    renderer.render(snap(v=0.0), 0.0)
    texts = []
    for k in range(1, 26):  # half a second after a jump to 100
        renderer.render(snap(v=100.0), k / 50)
        texts.append(int(shown_text(renderer, "m")))
    assert shown_text(renderer, "raw") == "100"
    # it counts up, a new number 25 times a second (as the graphs take their points)
    assert texts[0] < 20 and texts == sorted(texts) and len(set(texts)) >= 12
    changes = [i for i in range(1, len(texts)) if texts[i] != texts[i - 1]]
    assert all(b - a >= 2 for a, b in zip(changes, changes[1:], strict=False)), texts
    assert abs(texts[4] - renderer.motion.value("v", 4 / 50)) <= 0.5  # the bar's value
    for k in range(26, 100):  # settled: the reading itself, nothing moves
        renderer.render(snap(v=100.0), k / 50)
    assert shown_text(renderer, "m") == "100" and not renderer.moving


def test_gliding_numbers_draw_each_text_once():
    renderer = Renderer(theme(metric(smooth=True)), animate=True)
    renderer.fps = 50
    drawn = []
    real = renderer._text_piece

    def counted(widget, text, color):
        drawn.append(text)
        return real(widget, text, color)

    renderer._text_piece = counted
    for k in range(200):  # 0 -> 30 -> 0 -> 30, two seconds each way
        value = 30.0 if (k // 50) % 2 else 0.0
        renderer.render(snap(v=value), k / 50)
    assert len(drawn) == len(set(drawn)) <= 31  # 0..30, each once


def rules():
    return [{"above": 80, "color": "#ffff00"}, {"above": 90, "color": "#ff0000"}]


def test_threshold_colours_fade_and_a_bar_and_its_number_change_together():
    widgets = (
        bar(color="#00ff00", color_rules=rules()),
        metric(color="#00ff00", color_rules=rules()),
    )
    renderer = Renderer(theme(*widgets, smoothing_ms=0), animate=True)
    renderer.fps = 50
    renderer.render(snap(v=50.0), 0.0)
    assert renderer._cache["m"][0][1] == "#00ff00"
    bars, texts = [], []
    for k in range(1, 15):  # 50 -> 85 at once: past the first threshold
        renderer.render(snap(v=85.0), k / 50)
        bars.append(renderer._cache["b"][0][1][0])
        texts.append(renderer._cache["m"][0][1])
    assert any(b not in ((0, 255, 0, 255), (255, 255, 0, 255)) for b in bars)  # on its way
    assert bars[-1] == (255, 255, 0, 255) and texts[-1] == "#ffff00"  # there within 0.3 s
    for colour, text in zip(bars, texts, strict=True):  # the same colour at every step
        assert text.lower() in ("#ffff00", "#00ff00") or text == "#{:02x}{:02x}{:02x}{:02x}".format(
            *colour
        )
    still = Renderer(theme(*widgets, smoothing_ms=0))  # no animation: at once
    still.render(snap(v=50.0), 0.0)
    still.render(snap(v=85.0), 0.02)
    assert still._cache["m"][0][1] == "#ffff00"


def graph(wid="g", **kw):
    return {"type": "graph", "id": wid, "x": 0, "y": 0, "w": 200, "h": 60, "sensor": "v",
            "min": 0, "max": 100, "fill": False, "color": "#ffffff", "line_width": 1,
            "smooth": False, "seconds": 8, **kw}  # fmt: skip


def video(renderer, fps=50):
    renderer.continuous, renderer.fps = True, fps
    return renderer


def wave(t):
    return 50 + 40 * math.sin(t)


def run(renderer, start, seconds, fps, value=wave):
    """Readings once a second, frames at ``fps``, as the main loop draws them."""
    frames = {}
    readings = []
    for n in range(round(seconds * fps)):
        now = start + n / fps
        if n % fps == 0:
            readings.append(value(now))
            renderer.new_sample(now, 1.0)
        snapshot = Snapshot(readings={"v": Reading("v", readings[-1])}, history={"v": readings})
        frames[round(now, 6)] = renderer.render(snapshot, now)[0]
    return frames


def curve_rows(frame):
    """Per column, the topmost bright row of the curve (None where there is none)."""
    rows = []
    for x in range(frame.width):
        bright = [y for y in range(frame.height) if frame.getpixel((x, y))[0] > 120]
        rows.append(bright[0] if bright else None)
    return rows


def test_graphs_take_25_points_a_second_at_any_frame_rate():
    for fps in (25, 30, 50):
        renderer = video(Renderer(theme(graph(), smoothing_ms=400), animate=True), fps)
        run(renderer, 100.0, 3, fps)
        history = renderer.motion.histories["v"]
        before = history.last
        run(renderer, 103.0, 2, fps)
        assert history.last - before == 50, fps  # two seconds: 50 points


def test_a_graphs_window_is_its_seconds_whatever_the_frame_rate():
    """``seconds`` across the width at 25 and at 50 fps: the same curve in the
    same place, moving w / seconds pixels a second (25 here)."""
    curves = {}
    for fps in (25, 50):
        renderer = video(Renderer(theme(graph(), smoothing_ms=400), animate=True), fps)
        frames = run(renderer, 100.0, 10, fps)
        curves[fps] = {t: curve_rows(frames[t]) for t in (108.0, 109.0)}
    for t in (108.0, 109.0):
        pairs = [(a, b) for a, b in zip(curves[25][t], curves[50][t], strict=True) if a and b]
        assert len(pairs) > 180 and sum(abs(a - b) <= 1 for a, b in pairs) > 0.95 * len(pairs)
    # one second later the curve is 25 px further left
    later, earlier = curves[50][109.0], curves[50][108.0]
    pairs = [(later[x], earlier[x + 25]) for x in range(10, 170) if later[x] and earlier[x + 25]]
    assert len(pairs) > 150 and sum(abs(a - b) <= 1 for a, b in pairs) > 0.9 * len(pairs)


def test_graphs_move_on_every_frame_by_half_a_pixel():
    """25 px a second at 50 fps: each frame shifts the curve by half a pixel
    (a second image of the strip, drawn half a pixel on), never standing still."""
    renderer = video(Renderer(theme(graph(seconds=8), smoothing_ms=400), animate=True))
    frames = list(run(renderer, 100.0, 3, 50).values())[50:]
    assert all(a.tobytes() != b.tobytes() for a, b in zip(frames, frames[1:], strict=False))


def test_a_new_theme_shows_its_graphs_full_at_once():
    motion = Motion()
    # the panel showed a theme with a bar of "v" for a while: the graph's history
    # comes from the hub's readings
    before = video(Renderer(theme(bar()), animate=True, motion=motion))
    run(before, 100.0, 10, 50)
    after = video(Renderer(theme(graph(seconds=8)), animate=True, motion=motion))
    readings = [wave(100.0 + s) for s in range(10)]
    after.new_sample(109.0, 1.0)
    snapshot = Snapshot(readings={"v": Reading("v", readings[-1])}, history={"v": readings})
    rows = curve_rows(after.render(snapshot, 110.0)[0])
    assert sum(r is not None for r in rows) >= 195  # across the whole width
    # and a theme after that one takes the history on as it is
    again = video(Renderer(theme(graph("other", seconds=8)), animate=True, motion=motion))
    again.new_sample(109.0, 1.0)
    assert curve_rows(again.render(snapshot, 110.02)[0])[:190] == pytest.approx(
        curve_rows(after.render(snapshot, 110.02)[0])[:190], abs=1
    )


def test_a_graph_moves_on_with_a_gap_while_its_sensor_has_no_value():
    """A sensor that goes missing (or reads None) leaves a gap: the curve moves
    on, it does not stand still or run on as a flat line, and it comes back."""
    renderer = video(Renderer(theme(graph(seconds=8)), animate=True))
    frames = []
    for n in range(8 * 50):
        now, t = 100.0 + n / 50, n / 50
        if n % 50 == 0:
            renderer.new_sample(now, 1.0)
        if t < 3:
            readings = {"v": Reading("v", 60.0)}
        elif t < 5:
            readings = {}  # gone
        elif t < 6:
            readings = {"v": Reading("v", None)}  # there, without a value
        else:
            readings = {"v": Reading("v", 30.0)}
        snapshot = Snapshot(readings=readings, history={"v": [60.0, 60.0, 60.0]})
        frames.append(renderer.render(snapshot, now)[0])
    assert all(a.tobytes() != b.tobytes() for a, b in zip(frames[60:], frames[61:], strict=False))
    rows = curve_rows(frames[-1])  # 8 s across 200 px: the gap from 3 to 6 s is 75 px wide
    gap = [x for x, row in enumerate(rows) if row is None]
    assert 65 < len(gap) < 85 and min(gap) > 60 and max(gap) < 160, gap
    assert rows[40] is not None and rows[190] is not None  # before and after it
    assert rows[40] < rows[190]  # 60 before, 30 after (higher up is a smaller y)


# -- a fine course -----------------------------------------------------------------


def course(values):
    import json

    return json.dumps(values)


def fed(motion, key, values, end, now):
    text = course(values)
    motion.trace(key, text, tuple(values), end, now)


def test_a_fine_course_stands_point_by_point_and_only_what_is_new_is_taken():
    motion = Motion()
    fed(motion, "t", [10.0, 20.0, 30.0, None], end=10.0, now=9.0)  # the last: not known yet
    assert [motion.value("t", 9.88 + k / 25) for k in range(3)] == [10.0, 20.0, 30.0]
    assert motion.value("t", 9.96 + 0.039) == 30.0  # (each until the next)
    assert motion.fed["t"][1] == round(9.96 * 25)
    made = motion.springs["t"].made
    fed(motion, "t", [99.0, 40.0, 50.0], end=10.04, now=9.02)  # 9.96 again: kept as it was
    assert motion.springs["t"].made == made + 2
    assert [motion.value("t", 9.96 + k / 25) for k in range(3)] == [30.0, 40.0, 50.0]
    fed(motion, "t", [99.0, 40.0, 50.0], end=10.04, now=9.04)  # the same course: nothing
    assert motion.springs["t"].made == made + 2
    fed(motion, "t", [1.0, None, 3.0], end=10.16, now=9.06)  # a gap in it stays a gap
    assert motion.value("t", 10.12) is None and motion.value("t", 10.16) == 3.0
    fed(motion, "t", [7.0, 8.0], end=20.0, now=19.0)  # after a while off the panel: anew
    assert motion.springs["t"].made == made + 5 + 2 and motion.value("t", 19.98) == 7.0


def test_a_fine_course_does_not_glide_with_its_readings():
    motion = Motion()
    renderer = video(Renderer(theme(graph(trace="t")), animate=True, motion=motion))
    renderer.new_sample(100.0, 1.0)
    text = course([40.0] * 125)
    snapshot = Snapshot(readings={"v": Reading("v", 40.0), "t": Reading("t", text)})
    renderer.render(snapshot, 100.0)
    renderer.render(snapshot, 100.02)  # (the history's frame records it)
    assert "t" in motion.fed and motion.springs["t"].pieces[-1][2] == 40.0  # no gap from it


def square(t):
    """30 and 70 by turns, 0.4 s each: a glide from reading to reading (a second apart) has
    none of it."""
    return 30.0 if math.floor(t * 2.5) % 2 == 0 else 70.0


def traced(renderer, start, seconds, fps, value=square):
    """Frames at ``fps``; a reading once a second with the course of the last five seconds
    (25 points a second up to a quarter of a second before it), as PresentMon's."""
    frames, readings = {}, []
    text = None
    for n in range(round(seconds * fps)):
        now = start + n / fps
        if n % fps == 0:
            renderer.new_sample(now, 1.0)
            end = round((now - 0.25) * 25)
            text = course([value(k / 25) for k in range(end - 124, end + 1)])
            readings.append(50.0)
        snapshot = Snapshot(readings={"v": Reading("v", 50.0), "t": Reading("t", text)},
                            history={"v": readings})  # fmt: skip
        frames[round(now, 6)] = renderer.render(snapshot, now)[0]
    return frames


def test_a_graph_draws_its_fine_course_as_measured_and_moves_on_evenly():
    """The course's points one by one, 25 a second: 0.4 s of 30, 0.4 s of 70, as 10 px each
    way at 25 px a second; shown later than the reading by the time to the next one and a
    little, its newest end is always one that came, and it moves on as a graph does, 25 px a
    second."""
    renderer = video(Renderer(theme(graph(trace="t", seconds=8, min=0, max=100)),
                              animate=True))  # fmt: skip
    frames = traced(renderer, 100.0, 12, 50)
    rows = curve_rows(frames[111.0])
    assert all(row is not None for row in rows[2:198])  # no flat end, no gap at the right
    low, high = 60 - 1 - round(58 * 0.3), 60 - 1 - round(58 * 0.7)
    levels = [row for row in rows[20:180]]
    assert (
        sum(abs(r - low) <= 2 for r in levels) > 50 and sum(abs(r - high) <= 2 for r in levels) > 50
    )
    later = curve_rows(frames[111.5])  # half a second on: 12.5 px further left
    pairs = [(later[x], rows[x + 12]) for x in range(20, 170)]
    assert sum(abs(a - b) <= 6 for a, b in pairs) > 0.85 * len(pairs)


def test_without_its_fine_course_a_graph_draws_its_values_glide():
    renderer = video(Renderer(theme(graph(trace="t")), animate=True))
    run(renderer, 100.0, 3, 50)  # no "t" among the readings
    assert "v" in renderer.motion.histories and "t" not in renderer.motion.fed


def test_a_range_in_whole_steps_changes_seldom_and_its_top_starts_the_fade():
    renderer = Renderer(theme(graph(min=None, max=None, range_step=30)), animate=False)
    widget = renderer.widgets[0]
    assert renderer._glided_bounds(widget, [121.0, 133.0, 147.5], 1.0)[0] == (90.0, 180.0)
    assert renderer._glided_bounds(widget, [8.0, 11.0], 2.0)[0] == (0.0, 30.0)  # never below 0
    assert renderer._glided_bounds(widget, [-12.0, 11.0], 3.0)[0] == (-30.0, 30.0)
    first, _ = renderer._range_rows(widget, 2)
    assert renderer._fade_top(widget, [121.0], (90.0, 180.0), 2) == int(first)
    plain = Renderer(theme(graph(min=None, max=None)), animate=False)
    assert plain._glided_bounds(plain.widgets[0], [121.0, 147.5], 1.0)[0] == pytest.approx(
        (118.35, 150.15)
    )


def test_a_games_frame_rate_from_0_keeps_its_range_through_dips_and_is_quick_to_a_new_top():
    """The game's graph: from 0, its top in steps of 30. A frame that took long (a dip to 25
    fps) comes into the window and leaves it again: the range stays as it is, and the graph is
    not drawn anew for a range on its way. A peak past the top: the next step, there at once
    (a range on its way draws its graph anew ten times a second)."""
    renderer = Renderer(theme(graph(min=0, max=None, range_step=30)), animate=True)
    widget = renderer.widgets[0]
    steady = [138.0, 141.5, 144.0]
    assert renderer._glided_bounds(widget, steady, 0.0) == ((0, 180.0), False)
    for k in range(1, 40):  # a dip in the window and out of it again: nothing moves
        dip = [*steady, 25.0] if k % 10 < 5 else steady
        assert renderer._glided_bounds(widget, dip, k / 10) == ((0, 180.0), False), k
    t = 4.0
    bounds, moving = renderer._glided_bounds(widget, [*steady, 171.0], t)  # past 180 with room
    assert moving and bounds[1] >= 171.0
    while moving and t < 6.0:
        t += 0.1
        bounds, moving = renderer._glided_bounds(widget, [*steady, 171.0], t)
    assert bounds == (0, 210.0) and t <= 4.0 + 0.6  # there within half a second


# -- warming up the next theme ---------------------------------------------------

PREPARED = """
import threading

from PIL import Image

from libre_panel.plugins import Screen

events = []


class Layers(Screen):
    name = "layers"
    api = 1

    def prepare(self):
        events.append(("prepare", threading.current_thread().name))

    def render(self, snapshot, now):
        events.append(("render", threading.current_thread().name))
        return Image.new("RGB", self.context.size, "#203040")
"""


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Stream:
    """A streaming display in memory."""

    streaming = True
    stream_fps = 50
    restarted = False

    def __init__(self):
        self.frames = []

    def open(self):
        pass

    def set_brightness(self, percent):
        pass

    def describe(self):
        return "memory"

    def show(self, frame, region=None):
        self.frames.append(frame)

    def close(self):
        pass


def steps(loop, clock, until, limit=200):
    """Frames 20 ms apart until ``until()`` holds; the warm-up works in between."""
    import time

    for _ in range(limit):
        clock.now += 0.02
        loop.step(clock.now)
        if until():
            return True
        time.sleep(0.002)
    return False


def layered_theme(home):
    import json

    folder = home / "themes" / "layered"
    folder.mkdir(parents=True)
    data = {"format": "libre-panel-theme/1", "name": "layered",
            "display": {"model": "custom", "width": 160, "height": 80},
            "screen": {"name": "layers"}, "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


def loop_for(theme_name, clock, host=None):
    from libre_panel.app import MainLoop
    from libre_panel.config import Config, SensorsConfig
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    config = Config(theme=theme_name, sensors=SensorsConfig(providers=["demo"]))
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    return MainLoop(config, host=host, hub=hub, display=Stream(), clock=clock)


def test_a_screen_prepares_in_a_thread_of_its_own_before_its_first_frame(
    plugin_folder, isolated_home
):
    import sys
    import threading

    from libre_panel.plugins import PluginHost

    package = plugin_folder(PREPARED, {"libre_panel.screens": {"layers": "Layers"}})
    layered_theme(isolated_home)
    clock = Clock(1000.0)
    loop = loop_for("layered", clock)  # at start
    try:
        loop.step(clock.now)
    finally:
        loop.close()
    events = sys.modules[package].events
    here = threading.current_thread().name
    assert events[0][0] == "prepare" and events[0][1] != here and events[1] == ("render", here)

    events.clear()
    host = PluginHost()
    loop = loop_for("pico", clock, host)  # at a switch
    try:
        loop.step(clock.now)
        host.request_theme("layered", by="test", transition="cut")
        assert steps(loop, clock, lambda: loop.status.theme == "layered")
    finally:
        loop.close()
        host.close()
    assert events[0][0] == "prepare" and events[0][1] != here
    assert ("render", here) in events and all(
        name == here for kind, name in events if kind == "render"
    )


def test_a_theme_comes_on_warmed_up(monkeypatch):
    """The next theme is built and drawn once off the render thread while the
    current one goes on; its first frame on the panel builds no cards."""
    import threading

    from libre_panel.plugins import PluginHost

    built = []
    real = Renderer._card

    def card(self, *args):
        built.append(threading.current_thread().name)
        return real(self, *args)

    monkeypatch.setattr(Renderer, "_card", card)
    clock, host = Clock(1000.0), PluginHost()
    loop = loop_for("pico", clock, host)
    here = threading.current_thread().name
    try:
        loop.step(clock.now)
        built.clear()
        host.request_theme("spur-ii", by="test", transition="cut")
        assert steps(loop, clock, lambda: loop.status.theme == "spur-ii")
        assert loop.display.frames[-1].size == (1920, 480)
        assert built and here not in built  # the cards were drawn in the warm-up
    finally:
        loop.close()
        host.close()


# -- the built-in video themes, frame by frame ---------------------------------------


class Instant:
    """The helper thread, run at once: frames that do not depend on timing."""

    def submit(self, job):
        from concurrent.futures import Future

        future = Future()
        future.set_result(job())
        return future

    def close(self):
        pass


def demo_at(clock, epoch):
    """Demo readings for the planned time of each frame: they move as they would."""
    from libre_panel.sensors.demo import DemoProvider

    class Planned(DemoProvider):
        def read(self):
            self.fixed_time = epoch + clock()
            return super().read()

    return Planned({})


def video_frames(theme_name, seconds, monkeypatch, boxes):
    """The theme through MainLoop.step at 50 fps for ``seconds`` of planned
    time: per frame, what it showed in each of ``boxes`` (whole frames would
    take hundreds of megabytes)."""
    from libre_panel.app import MainLoop
    from libre_panel.config import Config, SensorsConfig
    from libre_panel.render import renderer as renderer_module
    from libre_panel.sensors.base import SensorHub

    monkeypatch.setattr(renderer_module, "_Builder", Instant)
    clock = Clock(1000.0)
    epoch = 17 * 10**8 - 1000.0  # the demo CPU load at its steepest
    hub = SensorHub([demo_at(clock, epoch)])
    config = Config(theme=theme_name, sensors=SensorsConfig(providers=["demo"]))
    loop = MainLoop(config, hub=hub, display=Stream(), clock=clock)
    frames = []
    try:
        for n in range(round(seconds * 50)):
            clock.now = 1000.0 + n / 50
            frame = loop.step(clock.now)
            frames.append({name: frame.crop(box) for name, box in boxes.items()})
        every = round(loop.theme.refresh_ms / 20)  # frames per reading
    finally:
        loop.close()
    return frames, every


def changes(frames, name):
    """Which frames differ from the one before in box ``name``."""
    crops = [frame[name].tobytes() for frame in frames]
    return [n for n in range(1, len(crops)) if crops[n] != crops[n - 1]]


def curve_profile(region):
    """Per column, the row of its brightest pixel: the curve's line."""
    region = region.convert("L")
    data, (w, h) = region.load(), region.size
    rows = []
    for x in range(w):
        column = [data[x, y] for y in range(h)]
        rows.append(column.index(max(column)))
    return rows


def moved(before, after, most=60):
    """How many pixels to the left the curve moved between two profiles."""
    span = len(after) - most
    return min(range(most), key=lambda s: sum(abs(before[x + s] - after[x]) for x in range(span)))


def test_spur_ii_moves_on_every_frame_in_video_mode(monkeypatch):
    """The built-in SPUR II in video mode: its graphs change on (nearly) every
    frame and move 25 px a second, and its numbers change between readings."""
    boxes = {
        "cpu": (43, 150, 945, 276),
        "gpu": (1003, 150, 1877, 276),
        "number": (820, 85, 925, 145),
    }
    frames, every = video_frames("spur-ii", 4, monkeypatch, boxes)
    for graph in ("cpu", "gpu"):
        # nearly every frame: at 0.49 px a frame, a frame now and then lands
        # on the same half pixel as the one before
        late = [n for n in changes(frames, graph) if n > 50]
        assert len(late) >= 0.9 * (len(frames) - 51), (graph, len(late))
    speed = moved(curve_profile(frames[100]["cpu"]), curve_profile(frames[150]["cpu"]))
    assert speed >= 20, speed  # in a second
    between = [n for n in changes(frames, "number") if n % every]  # the CPU load
    assert len(between) >= 8, (between, every)  # it glides: also between readings


def test_studio_glides_between_readings_in_video_mode(monkeypatch):
    boxes = {"ring": (1215, 34, 1415, 234), "number": (1245, 92, 1385, 150)}  # CPU
    frames, every = video_frames("studio", 3, monkeypatch, boxes)
    assert len([n for n in changes(frames, "ring") if n % every]) >= 20
    assert len([n for n in changes(frames, "number") if n % every]) >= 5


def test_lumen_moves_on_every_frame_in_video_mode(monkeypatch):
    """Libre Panel's signature screen in video mode: the load of the last minute scrolls,
    the fans turn and the rings glide, also between readings."""
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import find_theme, load_theme

    widgets = {w["id"]: w for w in Renderer(load_theme(find_theme("lumen"))).widgets}

    def box(wid):
        w = widgets[wid]
        return w["x"], w["y"], w["x"] + w["w"], w["y"] + w["h"]

    boxes = {"graph": box("cpu-graph"), "fan": box("fan-1"), "ring": box("cpu-ring/ring")}
    frames, every = video_frames("lumen", 3, monkeypatch, boxes)
    late = range(51, len(frames))  # after the first second
    graph = [n for n in changes(frames, "graph") if n in late]
    assert len(graph) >= 0.25 * len(late)  # 60 s across 470 px: a half pixel every 3 frames
    assert len([n for n in changes(frames, "fan") if n in late]) >= 0.8 * len(late)  # it turns
    assert len([n for n in changes(frames, "ring") if n % every]) >= 20

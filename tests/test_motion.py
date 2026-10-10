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
    assert texts[0] < 20 and texts == sorted(texts) and len(set(texts)) > 15  # counts up
    assert abs(texts[4] - renderer.motion.value("v", 5 / 50)) <= 0.5  # the bar's value
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

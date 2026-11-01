"""Graphs that step from reading to reading (config.toml ``graph_steps``, Renderer._graph_view)
and a video panel that rests while nothing on it moves (MainLoop.rest): a graph glides a moment
when a reading comes and then shows what a scrolling one showed at that time, still until the
next reading, its strips grown once a reading."""

import json

import numpy as np
import pytest

from libre_panel import app as app_module
from libre_panel.config import Config, SensorsConfig, parse_config
from libre_panel.render import renderer as R
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme


def video_renderer(name, steps, animate=False):
    renderer = R.Renderer(load_theme(find_theme(name)), animate=animate)
    renderer.continuous = renderer.dither = True
    renderer.graph_steps = steps
    renderer.fps = 30
    return renderer


def test_the_options_are_read_graphs_scroll_and_panels_rest_unless_asked_otherwise():
    assert Config().graph_steps is False and Config().device.video.rest is True
    assert parse_config({"graph_steps": True}).graph_steps is True
    assert parse_config({"video": {"rest": False}}).device.video.rest is False
    with pytest.raises(Exception, match="graph_steps"):
        parse_config({"graph_steps": "yes"})


def test_a_graph_that_steps_glides_to_the_reading_and_rests_until_the_next():
    renderer = video_renderer("lumen-1920x720", True)
    try:
        renderer.new_sample(1000.0, 1.0)
        assert renderer._graph_view(1000.2, False, 0.25) == (1000.25, False)  # (the first: there)
        renderer.new_sample(1001.0, 1.0)
        view, moves = renderer._graph_view(1001.0, False, 0.25)
        assert (view, moves) == (1000.25, True)  # from where it rested ...
        half = renderer._graph_view(1001.0 + renderer.STEP_S / 2, False, 0.25)
        assert half == (pytest.approx(1000.75), True)  # ... gently (half the way at half the time)
        assert renderer._graph_view(1001.0 + renderer.STEP_S, False, 0.25) == (1001.25, False)
        assert renderer._graph_view(1001.9, False, 0.25) == (1001.25, False)  # still
        assert renderer._graph_view(1001.1, True, 0.25) == (1001.1, True)  # its own fine course
        renderer.new_sample(1009.0, 1.0)  # long after the one before (a pause): it jumps there
        assert renderer._graph_view(1009.0, False, 0.25) == (1009.25, False)
        renderer.graph_steps = False
        assert renderer._graph_view(1009.1, False, 0.25) == (1009.1, True)  # scrolling on
    finally:
        renderer.close()


@pytest.mark.parametrize("animate", [False, True])
def test_a_resting_graph_shows_what_a_scrolling_one_showed_then_and_stays_the_same_piece(animate):
    """Two renderers, one with graphs that step: between readings its graph is the piece the
    other drew at the time it shows (the reading's, and its glide after: 0.6 s where values
    glide), and the same piece from frame to frame."""
    steps = video_renderer("lumen-1920x720", True, animate)
    scroll = video_renderer("lumen-1920x720", False, animate)
    glide = steps._glide_s()
    assert glide == (0.6 if animate else 0.0)
    then = round(glide * 30)  # the frame of the time it shows, after its reading
    shown, rest, compared = {}, None, 0
    try:
        for i in range(6 * 30):
            now = 1000 + i / 30
            if i % 30 == 0:
                snap = demo_snapshot(fixed_time=1_700_000_000 + i / 30)
                for renderer in (steps, scroll):
                    renderer.new_sample(now, 1.0)
            steps.render(snap, now)
            scroll.render(snap, now)
            k = i % 30
            if k == then:
                shown[i - k] = dict(scroll._last[0])["gpu-graph"]
            piece = dict(steps._last[0])["gpu-graph"]
            if i >= 60 and k >= max(12, then):  # (after the step)
                assert rest is None or piece is rest, i
                rest = piece
                there = shown[i - k]
                assert (piece.x, piece.y) == (there.x, there.y)
                assert np.array_equal(np.asarray(piece.layer), np.asarray(there.layer)), i
                compared += 1
            elif k == 0:
                rest = None
        assert compared >= 4 * (30 - max(12, then))
    finally:
        steps.close()
        scroll.close()


def test_a_graph_that_steps_grows_its_strip_once_a_reading(monkeypatch):
    """Its strips reach from the window before what it shows to past the next reading: none is
    drawn on the render thread because the window lies beyond it (that is, whole, at every
    frame), and one is grown for each reading."""
    renderer = video_renderer("lumen-1920x720", True)
    jobs, whole = [], []
    job, draw = R.Renderer._strip_job, R.Renderer._draw_strip

    def counted_job(self, widget, *args, **kwargs):
        jobs.append(widget["id"])
        return job(self, widget, *args, **kwargs)

    def counted_draw(self, widget, *args):
        whole.append(widget["id"])
        return draw(self, widget, *args)

    monkeypatch.setattr(R.Renderer, "_strip_job", counted_job)
    monkeypatch.setattr(R.Renderer, "_draw_strip", counted_draw)
    try:
        for i in range(10 * 30):
            now = 1000 + i / 30
            if i % 30 == 0:
                snap = demo_snapshot(fixed_time=1_700_000_000 + i / 30)
                renderer.new_sample(now, 1.0)
            renderer.render(snap, now)
        assert jobs.count("gpu-graph") <= 10 + 1  # (the first, then one a reading)
        assert whole.count("gpu-graph") == 1  # (the GPU's range stays: grown from then on)
        assert renderer.warnings == []
    finally:
        renderer.close()


# -- a video panel that rests -------------------------------------------------------------------


class Stream:
    streaming = True
    stream_fps = 30

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


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Instant:
    """The helper thread, run at once."""

    def __init__(self, name="render-builder", gate=None):
        pass

    def submit(self, job):
        from concurrent.futures import Future

        future = Future()
        future.set_result(job())
        return future

    def close(self):
        pass


def graph_theme(home):
    widgets = [
        {"type": "text", "id": "title", "x": 10, "y": 8, "text": "Processor", "font_size": 24},
        {"type": "graph", "id": "graph", "x": 10, "y": 60, "w": 300, "h": 100,
         "sensor": "cpu.load", "glow": 0.5},
    ]  # fmt: skip
    data = {"format": "libre-panel-theme/1", "name": "steps",
            "display": {"model": "custom", "width": 320, "height": 170},
            "background": {"color": "#0a1119"}, "widgets": widgets}  # fmt: skip
    folder = home / "themes" / "steps"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("steps", [False, True])
def test_a_video_panel_rests_while_nothing_moves(isolated_home, monkeypatch, steps):
    """A graph that scrolls moves at every frame: no rest. One that steps moves while it glides
    to a reading; then the panel rests until the next reading, at most half a second and to the
    clock's next second (a clock may show it)."""
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    monkeypatch.setattr(R, "_Builder", Instant)
    graph_theme(isolated_home)
    clock = Clock(1000.0)
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = Config(theme="steps", graph_steps=steps, sensors=SensorsConfig(providers=["demo"]))
    loop = app_module.MainLoop(config, hub=hub, display=Stream(), clock=clock)
    rests = []
    try:
        for n in range(4 * 30):
            clock.now = 1000.0 + n / 30
            loop.step(clock.now)
            rests.append(loop.rest())
        assert loop.streaming
        late = rests[45:]  # (the theme's first readings in)
        if not steps:
            assert all(rest is None for rest in late)
            return
        resting = [rest for rest in late if rest is not None]
        assert len(resting) >= 0.5 * len(late)  # most of the time
        assert all(0.0 <= rest <= app_module.REST_S for rest in resting)
        assert any(rest is None for rest in late)  # (the steps)
        # until the next reading: no frame is due before
        n = next(i for i, rest in enumerate(rests) if i > 45 and rest is not None)
        assert rests[n] <= loop.next_sample - (1000.0 + n / 30) + 1e-9
    finally:
        loop.close()


def test_a_panel_with_something_on_its_way_does_not_rest(isolated_home, monkeypatch):
    monkeypatch.setattr(R, "_Builder", Instant)
    graph_theme(isolated_home)
    clock = Clock(1000.0)
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = Config(theme="steps", graph_steps=True, sensors=SensorsConfig(providers=["demo"]))
    loop = app_module.MainLoop(config, hub=hub, display=Stream(), clock=clock)
    try:
        for n in range(60):
            clock.now = 1000.0 + n / 30
            loop.step(clock.now)
        loop.renderer.moving = False
        assert loop.rest() is not None
        loop.config.device.video.rest = False  # ([video] rest off: every frame)
        assert loop.rest() is None
        loop.config.device.video.rest = True
        loop.blend = object()  # (a transition)
        assert loop.rest() is None
        loop.blend = None
        loop.renderer.moving = True
        assert loop.rest() is None
    finally:
        loop.blend = None
        loop.close()


def test_a_rest_leaves_frames_out_on_the_same_frame_clock():
    """At rest the pacer leaves out the frames until the next one due, on its clock: the frame
    after a rest is planned where the clock had it (no new start), at most at the rest's end."""
    import threading

    class Time:
        now = 100.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    class Stop(threading.Event):
        def wait(self, timeout=None):
            clock.sleep(timeout)
            return False

    clock, stop = Time(), Stop()
    pacer = app_module._Pacer(clock=clock, sleep=clock.sleep)
    first = pacer.wait(30, stop)
    planned = [pacer.wait(30, stop) for _ in range(3)]
    assert planned == [pytest.approx(first + n / 30) for n in (1, 2, 3)]
    after = pacer.rest(0.5, 30, stop)  # (half a second)
    assert after == pytest.approx(first + 18 / 30) and clock.now == pytest.approx(after)
    assert pacer.rest(0.01, 30, stop) == pytest.approx(first + 19 / 30)  # (a frame at least)
    assert pacer.wait(30, stop) == pytest.approx(first + 20 / 30)
    assert pacer.reanchored == 0

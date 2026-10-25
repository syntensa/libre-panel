"""The video experiment's run (editor/experiment.py): its steps, how a panel's numbers are
judged, the run itself on fakes, and that the settings from before come back."""

from types import SimpleNamespace

import pytest

from libre_panel.editor.experiment import (
    MEASURED,
    Experiment,
    ExperimentError,
    gpu_vendor,
    judge,
    steps,
)


def test_the_steps_try_each_thing_apart_and_the_graphics_card_s_encoder_where_there_is_one():
    ids = [s.id for s in steps()]
    assert ids == ["auto", "crf-18", "ultrafast", "main", "high", "keyframe-2", "fps-60", "flood"]
    assert [s.id for s in steps("amd")][6] == "h264_amf"
    assert [s.id for s in steps("nvidia")][6] == "h264_nvenc"
    assert [s.id for s in steps("intel")][6] == "h264_qsv"
    assert steps("amd")[6].changes == {"video.encoder": "h264_amf"}
    assert steps()[-1].changes == {"video.maxrate": "24M", "video.crf": 16}
    assert steps()[0].seconds == 240.0  # the ladder climbs a step a minute
    assert gpu_vendor("Radeon RX 0000") == "amd" and gpu_vendor("GeForce RTX 0000") == "nvidia"
    assert gpu_vendor("Intel Arc A000") == "intel" and gpu_vendor("Something else") == ""


def stats(**changes):
    base = {"fps": 49.8, "target_fps": 50, "streaming": True, "kbps": 380.0, "blocks_per_s": 50.0,
            "max_depth": 2, "waits": 3, "drops": 0, "usb_errors": 0, "panel_restarts": 0,
            "ffmpeg_restarts": 0, "bit_rate": 3.0, "encoder": "h264_amf, rgb0"}  # fmt: skip
    return {**base, **changes}


def test_a_panel_s_numbers_are_judged():
    first, last = stats(), stats(waits=5)
    ok = judge([stats(), stats(fps=50.2)], first, last)
    assert (ok["verdict"], ok["fps"], ok["kbps"], ok["waits"], ok["bit_rate"]) == (
        "ok",
        50.0,
        380.0,
        2,
        3.0,
    )
    assert ok["encoder"] == "h264_amf, rgb0"  # what the encoder was fed, said with the numbers
    assert "h264_amf, rgb0" in Experiment._summary({"panels": [{"panel": 1, **ok}]})
    assert "encoder -" in Experiment._summary({"panels": [{"panel": 1, **ok, "encoder": None}]})
    assert judge([stats(fps=40.0)], first, last)["verdict"] == "stutter"
    assert judge([stats()], first, stats(waits=20))["verdict"] == "stutter"  # kept waiting
    assert judge([stats(blocks_per_s=0.0)], first, last)["verdict"] == "failed"  # no pictures
    assert judge([stats()], first, stats(panel_restarts=1))["verdict"] == "failed"
    assert judge([stats()], first, stats(ffmpeg_restarts=2))["verdict"] == "failed"
    assert judge([stats()], first, stats(ffmpeg_restarts=1))["verdict"] == "ok"  # one is a hiccup
    # the frame rate fell while the drawing took most of a frame's time: the PC, not the panel
    busy = judge([stats(fps=22.0, render_ms=19.0)], first, last)
    assert (busy["verdict"], busy["render_ms"]) == ("pc", 19.0)
    assert judge([stats(fps=22.0, render_ms=4.0)], first, last)["verdict"] == "stutter"
    none = {"fps": 2.0, "target_fps": 10, "max_depth": None, "blocks_per_s": None}
    assert judge([none], none, none)["verdict"] == "no video"
    assert judge([], {}, {})["verdict"] == "no video"


class FakeApp:
    """The running app as the run sees it: the settings written, every panel's numbers."""

    def __init__(self, panels):
        self.written = []
        self.video = {key.split(".", 1)[1]: value for key, value in MEASURED.items()}
        self.video["crf"] = 22  # the person's own, from before the run
        self.panels = panels  # panel number -> a function of the written settings -> stats
        self.now = 0.0

    def write(self, changes):
        self.written.append(dict(changes))
        for key, value in changes.items():
            self.video[key.split(".", 1)[1]] = value

    def read(self):
        return dict(self.video)

    def status(self):
        more = {
            n - 2: SimpleNamespace(stats=f(self.video)) for n, f in self.panels.items() if n > 1
        }
        return SimpleNamespace(stats=self.panels[1](self.video), more=more, hub=None)

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now


def test_the_run_tries_every_step_judges_every_panel_and_brings_the_settings_back():
    """Two panels: the first keeps up with everything, the one beside it (the companion) stops
    taking pictures at profile high and stutters at 60 fps, which the step's verdict says."""

    def first(video):
        return stats()

    def companion(video):
        if video["profile"] == "high":
            return stats(blocks_per_s=0.0)
        if video["fps"] == 60:
            return stats(fps=44.0, target_fps=60)
        return stats()

    app = FakeApp({1: first, 2: companion})
    run = Experiment(app.write, app.read, app.status, gpu=lambda: "Radeon RX 0000",
                     clock=app.clock, sleep=app.sleep)  # fmt: skip
    run.scale = 0.001
    run.start()
    run._thread.join(30)
    assert not run.running and run.ended == "done" and run.error == ""
    assert [r["id"] for r in run.results] == [s.id for s in steps("amd")]
    assert app.written[0] == {**MEASURED}  # each step starts from the measured settings
    assert app.written[1] == {**MEASURED, "video.crf": 18}
    assert app.written[-1] == {**MEASURED, "video.crf": 22}  # the person's own, back
    assert app.video["crf"] == 22 and app.video["profile"] == "auto"
    by_id = {r["id"]: r for r in run.results}
    assert [p["panel"] for p in by_id["auto"]["panels"]] == [1, 2]
    assert by_id["auto"]["verdict"] == "ok"
    assert by_id["high"]["verdict"] == "failed"
    assert [p["verdict"] for p in by_id["high"]["panels"]] == ["ok", "failed"]
    assert by_id["fps-60"]["verdict"] == "stutter"
    assert by_id["h264_amf"]["verdict"] == "ok"
    state = run.state()
    assert state["of"] == 9 and len(state["results"]) == 9 and state["ended"] == "done"
    with pytest.raises(ExperimentError):
        run.running = True
        run.start()


def test_a_stopped_run_brings_the_settings_back_at_once():
    app = FakeApp({1: lambda video: stats()})
    run = Experiment(app.write, app.read, app.status, clock=app.clock, sleep=app.sleep)
    run.scale = 0.001
    run.sleep = lambda seconds: (app.sleep(seconds), run.stop() if len(app.written) >= 2 else None)
    run.start()
    run._thread.join(30)
    assert run.ended == "stopped" and not run.running
    assert len(run.results) <= 2 and app.written[-1] == {**MEASURED, "video.crf": 22}


def test_without_a_panel_loop_every_step_says_no_video():
    app = FakeApp({})
    run = Experiment(app.write, app.read, lambda: None, clock=app.clock, sleep=app.sleep)
    run.scale = 0.001
    run.start()
    run._thread.join(30)
    assert all(r["verdict"] == "no video" and r["panels"] == [] for r in run.results)

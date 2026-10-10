"""SPUR II's timing guards while the main loop drives a panel, tested with fakes."""

import threading
import time

from libre_panel import timing


class Winmm:
    def __init__(self):
        self.calls = []

    def timeBeginPeriod(self, ms):  # noqa: N802 - the Windows name
        self.calls.append(("begin", ms))
        return 0

    def timeEndPeriod(self, ms):  # noqa: N802
        self.calls.append(("end", ms))
        return 0


class Collector:
    def __init__(self):
        self.calls = []
        self.thresholds = (700, 10, 10)

    def get_threshold(self):
        return self.thresholds

    def set_threshold(self, *thresholds):
        self.calls.append(("threshold", thresholds))
        self.thresholds = thresholds

    def freeze(self):
        self.calls.append(("freeze",))

    def unfreeze(self):
        self.calls.append(("unfreeze",))


class Kernel32:
    def __init__(self):
        self.calls = []

    def GetCurrentThread(self):  # noqa: N802
        return -2  # the pseudo handle

    def SetThreadPriority(self, handle, priority):  # noqa: N802
        self.calls.append((handle, priority))
        return 1


def test_windows_timer_at_one_millisecond_while_the_loop_runs():
    winmm = Winmm()
    guards = timing.Guards("win32", winmm, Collector())
    guards.start()
    guards.start()
    guards.stop()
    guards.stop()
    assert winmm.calls == [("begin", 1), ("end", 1)]
    elsewhere = Winmm()
    other = timing.Guards("linux", elsewhere, Collector())
    other.start()
    other.stop()
    assert elsewhere.calls == []


def test_the_collector_leaves_out_what_lives_from_start_up(monkeypatch):
    monkeypatch.setattr(timing, "_settled", 0)
    monkeypatch.setattr(timing, "_thresholds", None)
    collector = Collector()
    first, second = (timing.Guards("linux", None, collector) for _ in range(2))
    first.settled()
    first.settled()
    second.settled()  # a second main loop meanwhile: frozen once
    assert collector.calls == [("freeze",), ("threshold", (50000, 200, 200))]
    first.stop()
    assert collector.calls[-1] == ("threshold", (50000, 200, 200))  # the other one still runs
    second.stop()
    assert collector.calls[-2:] == [("unfreeze",), ("threshold", (700, 10, 10))]


def test_threads_that_feed_the_panel_go_above_normal_on_windows():
    kernel32 = Kernel32()
    assert timing.raise_priority("win32", kernel32)
    assert kernel32.calls == [(-2, timing.THREAD_PRIORITY_ABOVE_NORMAL)]
    assert not timing.raise_priority("linux", kernel32)
    assert len(kernel32.calls) == 1


def test_nothing_happens_at_import():
    import subprocess
    import sys

    code = (
        "import gc, sys; before = gc.get_threshold(); import libre_panel.timing; "
        "print(gc.get_threshold() == before, gc.get_freeze_count() == 0, 'ctypes' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.split() == ["True", "True", "False"], result.stdout + result.stderr


def test_run_guards_the_loop_only_while_it_drives_the_panel(monkeypatch, tmp_path):
    from libre_panel import app
    from libre_panel.config import Config, DeviceConfig, SensorsConfig

    events = []

    class Recorder:
        def start(self):
            events.append("start")

        def settled(self):
            if "settled" not in events:
                events.append("settled")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(timing, "Guards", Recorder)
    output = tmp_path / "frame.png"
    config = Config(
        device=DeviceConfig(driver="virtual", output=str(output)),
        sensors=SensorsConfig(providers=["demo"]),
    )
    app.run(config, once=True)  # one picture: nothing to guard
    assert "start" not in events and "settled" not in events
    events.clear()
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    thread.start()
    deadline = time.monotonic() + 30
    while "settled" not in events and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(10)
    assert events == ["start", "settled", "stop"]


def test_the_video_sender_and_ffmpegs_forwarder_ask_for_priority(monkeypatch):
    from libre_panel.config import DeviceConfig, VideoConfig
    from libre_panel.devices import turzx_video

    raised = []
    monkeypatch.setattr(
        timing, "raise_priority", lambda: raised.append(threading.current_thread().name)
    )
    display = turzx_video.TurzxVideoDisplay(
        DeviceConfig(driver="turzx", model="turing-9.2-usb", video=VideoConfig(mode="on"))
    )
    stop = threading.Event()
    stop.set()
    sender = threading.Thread(target=display._send_loop, args=(None, stop), name="panel-video")
    sender.start()
    sender.join(5)
    assert raised == ["panel-video"]

    class Encoder:  # stands in for ffmpeg: the picture comes from its reader thread
        def __init__(self, command, size, deliver):
            self.size, self.deliver = size, deliver

        def close(self):
            pass

    monkeypatch.setattr(turzx_video, "Encoder", Encoder)
    display._ffmpeg = "ffmpeg"
    display._start_encoder((1920, 480))
    for _ in range(2):
        reader = threading.Thread(
            target=display.encoder.deliver, args=(b"picture",), name="ffmpeg-reader"
        )
        reader.start()
        reader.join(5)
    assert raised == ["panel-video", "ffmpeg-reader"]  # once per encoder
    assert display._pictures.qsize() == 2

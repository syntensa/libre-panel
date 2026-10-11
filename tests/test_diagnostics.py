"""Diagnostics for the editor's pages: what a page can tell about the app (the picture on
the panel, the log, whether it runs here), whether a message will show, and the counters
of the main loop and the video link."""

import os
import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops
from test_app_cli import MemoryStream, StepClock

from libre_panel import app as app_module
from libre_panel.config import Config, SensorsConfig, log_file
from libre_panel.devices.base import Counters, DeviceError
from libre_panel.meter import FRAMES, Meter
from libre_panel.plugins import Delivery, PageContext, PluginHost, Toast
from libre_panel.plugins.host import ServiceHost
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider
from libre_panel.sensors.demo import DemoProvider
from libre_panel.theme.model import save_theme


def demo_loop(clock, host, display=None, theme="pico"):
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = Config(theme=theme, sensors=SensorsConfig(providers=["demo"]))
    return app_module.MainLoop(
        config, host=host, hub=hub, display=display or MemoryStream(), clock=clock
    )


# -- what PageContext tells -----------------------------------------------------------


def test_page_context_knows_whether_the_background_app_serves_the_editor():
    plain = PageContext(None)
    assert plain.background is False and plain.shown_frame() is None
    host = PluginHost()
    picture = Image.new("RGB", (8, 4), "red")
    host.shown = picture
    context = PageContext(SimpleNamespace(plugin_host=host))
    assert context.background is True and context.shown_frame() is picture
    host.close()


def test_page_context_tells_how_the_panel_runs_and_which_sources_it_reads():
    @dataclass
    class Mode:
        mode: str = "inprocess"
        error: str = ""

    class Rich(SensorProvider):
        name = "rich"

        def read(self):
            return {"a.one": Reading("a.one", 1.0), "a.two": Reading("a.two", 2.0)}

        def status(self):
            return Mode()

    class Shy(SensorProvider):
        name = "shy"

        def read(self):
            return {"a.one": Reading("a.one", 9.0), "b": Reading("b", 3.0)}

        def status(self):
            raise RuntimeError("cannot say")

    plain = PageContext(None)
    assert plain.status() is None and plain.sources() == []
    status = app_module.RunStatus()
    controls = SimpleNamespace(panel=SimpleNamespace(status=status, state=lambda: {"state": "x"}))
    context = PageContext(controls)
    assert context.status() == {"state": "x"} and context.sources() == []  # no loop yet
    status.hub = hub = SensorHub([Rich(), Shy(), DemoProvider({"fixed_time": 1_700_000_000})])
    hub.snapshot()
    sources = context.sources()
    assert [(s["name"], s["status"]) for s in sources[:2]] == [
        ("rich", {"mode": "inprocess", "error": ""}),
        ("shy", None),  # a status that fails is no reason to fail
    ]
    assert [s["readings"] for s in sources[:2]] == [2, 1]  # a.one came from the first
    assert (
        sources[2]["name"] == "demo" and sources[2]["readings"] > 3 and sources[2]["status"] is None
    )


def test_the_log_tail_is_read_from_the_end_of_the_log(isolated_home):
    context = PageContext(None)
    assert context.log_tail() == []  # no log yet
    path = log_file()
    assert path == isolated_home / "logs" / "libre-panel.log"  # where the tray app writes it
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"line {n}\n" for n in range(1, 5001)), encoding="utf-8")
    assert context.log_tail(3) == ["line 4998", "line 4999", "line 5000"]
    assert len(context.log_tail(40)) == 40 and len(context.log_tail(99999)) == 1000
    assert context.log_tail(0) == ["line 5000"]  # at least one
    path.write_text("one\ntwo\n", encoding="utf-8")
    assert context.log_tail() == ["one", "two"]  # shorter than asked: all of it
    path.write_text("".join(f"{n} {'x' * 3000}\n" for n in range(50)), encoding="utf-8")
    assert [row.split()[0] for row in context.log_tail(3)] == ["47", "48", "49"]  # long lines
    path.write_bytes(b"caf\xe9 \xff\nnext\n")  # not UTF-8: still readable
    assert context.log_tail(2)[1] == "next" and len(context.log_tail(2)) == 2


def test_the_loop_hands_out_the_frame_on_the_panel():
    """What the panel shows now, toast and all, is for the editor's pages; it is gone
    when the loop ends (a paused panel has no live picture)."""
    clock = StepClock(1000.0)
    host, display = PluginHost(), MemoryStream()
    loop = demo_loop(clock, host, display)
    try:
        assert PageContext(SimpleNamespace(plugin_host=host)).shown_frame() is None
        loop.step(clock.now)
        assert host.shown is display.frames[-1][0]  # the frame that went to the panel
        host.notify(Toast("Now playing", kind="music", seconds=3.0))
        for _ in range(25):  # the card fades in over a fifth of a second
            clock.now += 0.02
            loop.step(clock.now)
        assert host.shown is display.frames[-1][0]
        diff = ImageChops.difference(host.shown.convert("RGB"), loop.last_frame.convert("RGB"))
        assert diff.getbbox() is not None  # the toast is on it, the frame under it is not
    finally:
        loop.close()
        host.close()
    assert host.shown is None


# -- will a message show? ----------------------------------------------------------------


def quiet_theme(name="quiet", off=("music",)):
    """A user theme that switches some kinds of messages off."""
    save_theme(
        name,
        {
            "format": "libre-panel-theme/1",
            "name": name,
            "display": {"model": "custom", "width": 320, "height": 240},
            "background": {"color": "#000000"},
            "widgets": [],
            "toast": {"off": list(off)},
        },
    )
    return name


def test_a_delivery_is_true_when_the_message_will_show():
    assert Delivery(True) and not Delivery(False, "theme")
    assert Delivery(False, "screen").reason == "screen" and Delivery(True).reason == ""


def test_notify_says_whether_a_message_will_show(isolated_home):
    clock, host = StepClock(1000.0), PluginHost()
    service = ServiceHost(host, "test")
    assert service.notify("hello", kind="volume") == Delivery(False, "no-panel")  # no loop yet
    loop = demo_loop(clock, host, theme=quiet_theme())
    try:
        assert service.notify("hello", kind="volume") == Delivery(False, "no-panel")  # not open yet
        loop.step(clock.now)
        assert service.notify("hello", kind="volume") == Delivery(True)
        assert service.notify("hello") == Delivery(True)  # no kind: nothing to switch off
        refused = service.notify("a song", kind="music")
        assert not refused and refused.reason == "theme"  # the theme's toast.off
        # a screen that shows some kinds itself
        screen = SimpleNamespace(suppresses={"warning", "music"}, close=lambda: None)
        loop.renderer.screen = screen
        loop.toasts.set_renderer(loop.renderer)
        assert service.notify("hot", kind="warning") == Delivery(False, "screen")
        assert service.notify("a song", kind="music").reason == "theme"  # the theme's word first
        assert service.notify("hello", kind="volume").shown
    finally:
        loop.close()
    assert service.notify("hello", kind="volume") == Delivery(False, "no-panel")  # the loop ended
    # whatever was told after the first frame was handed over, shown or not (the layer
    # decides when a message is due)
    assert len(host.take_toasts()) == 7
    host.close()


def test_a_panel_that_is_not_connected_shows_nothing_yet():
    class Unplugged(MemoryStream):
        plugged = False

        def open(self):
            if not self.plugged:
                raise DeviceError("no panel found")

    clock, host, display = StepClock(1000.0), PluginHost(), Unplugged()
    service = ServiceHost(host, "test")
    loop = demo_loop(clock, host, display)
    try:
        loop.step(clock.now)
        assert not loop.link.connected
        assert service.notify("hello", kind="volume") == Delivery(False, "no-panel")
        display.plugged = True
        loop.link.retry_at = 0.0
        loop.step(clock.now)
        assert loop.link.connected and service.notify("hello", kind="volume").shown
    finally:
        loop.close()
        host.close()


def test_a_page_can_send_a_message_and_learn_whether_it_shows(isolated_home):
    plain = PageContext(None)
    assert plain.notify("test", kind="volume") == Delivery(False, "no-panel")
    clock, host = StepClock(1000.0), PluginHost()
    loop = demo_loop(clock, host, theme=quiet_theme())
    context = PageContext(SimpleNamespace(plugin_host=host))
    try:
        loop.step(clock.now)
        assert context.notify("Volume", kind="volume", rank=0, key="volume").shown
        assert context.notify("A song", kind="music", icon="music").reason == "theme"
        sent = host.take_toasts()
        assert [(t.text, t.kind, t.service, t.key) for t in sent] == [
            ("Volume", "volume", "editor", "volume"),
            ("A song", "music", "editor", ""),
        ]
    finally:
        loop.close()
        host.close()


# -- the meter: numbers about the loop, on a fake clock ----------------------------------


def run_meter(frames, fps=50, draw_s=0.004, slow=None, bytes_per_frame=5000, status=None):
    """``frames`` frames at ``fps`` through a meter; frame ``slow`` takes longer to draw."""
    status = status or app_module.RunStatus()
    meter = Meter(status, 100.0)
    for n in range(frames):
        meter.drew(0.030 if n == slow else draw_s)
        status.counters.bytes += bytes_per_frame
        meter.tick(100.0 + n / fps, fps, True)
    return status, meter


def test_the_meter_measures_rates_and_drawing_times_on_its_own_clock():
    status, _ = run_meter(150, slow=70)
    stats = status.stats
    assert stats["fps"] == pytest.approx(50, abs=1) and stats["target_fps"] == 50
    assert stats["streaming"] is True
    assert stats["render_ms"] == 4.0 and stats["render_peak_ms"] == 30.0  # median and slowest
    assert stats["kbps"] == pytest.approx(250, abs=5)  # 5000 bytes, 50 times a second
    assert stats["uptime_s"] == 2


def test_the_meter_says_nothing_before_its_first_second_and_what_nothing_counted_is_unknown():
    status, meter = run_meter(40)  # 0.8 s
    assert status.stats == {}
    status, _ = run_meter(60, bytes_per_frame=0)
    stats = status.stats
    assert stats["kbps"] is None  # no driver counted bytes
    assert stats["blocks_per_s"] is stats["max_depth"] is stats["waits"] is stats["drops"] is None
    assert (stats["usb_errors"], stats["panel_restarts"], stats["ffmpeg_restarts"]) == (0, 0, 0)


def test_the_meter_reads_the_video_link_and_the_faults_from_the_counters():
    status = app_module.RunStatus()
    meter = Meter(status, 0.0)
    counters = status.counters
    for n in range(1, 151):  # three seconds at 50 pictures a second
        meter.drew(0.004)
        counters.blocks += 1
        counters.bytes += 4000
        counters.max_depth = max(counters.max_depth, n % 3)
        if n == 120:
            counters.waits, counters.drops = 3, 2
            counters.usb_errors, counters.panel_restarts, counters.ffmpeg_restarts = 4, 1, 2
        meter.tick(n / 50, 50, True)
    stats = status.stats
    assert stats["blocks_per_s"] == pytest.approx(50, abs=1) and stats["max_depth"] == 2
    assert (stats["waits"], stats["drops"]) == (3, 2)
    assert (stats["usb_errors"], stats["panel_restarts"], stats["ffmpeg_restarts"]) == (4, 1, 2)


def test_the_meter_keeps_only_the_last_frames_and_the_last_seconds():
    status, meter = run_meter(60, slow=10)
    assert status.stats["render_peak_ms"] == 30.0
    for _ in range(FRAMES):  # the slow frame leaves the ring
        meter.drew(0.004)
    meter.tick(100.0 + 3.0, 50, True)
    assert status.stats["render_peak_ms"] == 4.0
    assert len(meter.times) == FRAMES
    for second in range(4, 12):  # the panel is gone: nothing is drawn, the loop looks every second
        meter.tick(100.0 + second, 50, False)
    assert status.stats["fps"] == 0.0 and status.stats["streaming"] is False
    assert len(meter.samples) == 6  # a ring, it does not grow


def test_a_loop_that_stalls_makes_no_burst_of_numbers():
    status, meter = run_meter(60)
    first = status.stats
    meter.tick(130.0, 50, True)  # thirty seconds later
    second = status.stats
    assert second is not first and meter.next_at == 131.0
    status.stats = first
    meter.tick(130.5, 50, True)
    assert status.stats is first  # not due


class CountingStream(MemoryStream):
    """A video panel in memory that counts like the drivers do."""

    def show(self, frame, region=None):
        super().show(frame, region)
        self.counters.blocks += 1
        self.counters.bytes += 4000


def test_the_main_loop_publishes_its_numbers_in_the_status():
    clock, host, display = StepClock(1000.0), PluginHost(), CountingStream()
    status = app_module.RunStatus()
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = Config(theme="pico", sensors=SensorsConfig(providers=["demo"]))
    loop = app_module.MainLoop(
        config, status=status, host=host, hub=hub, display=display, clock=clock
    )
    real = loop.renderer.render

    def slow(snapshot, now=None):  # drawing takes 5 ms on this clock
        clock.now += 0.005
        return real(snapshot, now)

    loop.renderer.render = slow
    try:
        assert status.stats == {} and display.counters is status.counters and status.hub is hub
        for n in range(150):  # three seconds at 50 fps
            clock.now = 1000.0 + n / 50
            loop.step(clock.now)
        stats = status.stats
        assert stats["fps"] == pytest.approx(50, abs=1) and stats["target_fps"] == 50
        assert stats["streaming"] is True and stats["render_ms"] == pytest.approx(5.0, abs=0.01)
        assert stats["kbps"] == pytest.approx(200, abs=5)
        assert stats["blocks_per_s"] == pytest.approx(50, abs=1)
    finally:
        loop.close()
        host.close()
    assert status.hub is None  # the sources are closed with the loop


def test_every_display_the_loop_makes_counts_into_the_same_numbers(isolated_home, monkeypatch):
    """When the device settings change the loop makes a new display; what was counted
    so far stays."""
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('theme = "pico"\n[device]\ndriver = "virtual"\noutput = "a.png"\n')
    made = []
    monkeypatch.setattr(
        app_module, "create_display", lambda c: made.append(CountingStream()) or made[-1]
    )
    clock, host, status = StepClock(1000.0), PluginHost(), app_module.RunStatus()
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = app_module.load_config(path)
    config.sensors = SensorsConfig(providers=["demo"])
    loop = app_module.MainLoop(config, status=status, host=host, hub=hub, clock=clock)
    try:
        assert len(made) == 1 and made[0].counters is status.counters
        status.counters.usb_errors = 2
        path.write_text('theme = "pico"\n[device]\ndriver = "virtual"\noutput = "b.png"\n')
        os.utime(path, (time.time() + 5, time.time() + 5))  # surely newer
        for _ in range(300):  # the change waits for the theme to be warmed up
            clock.now += 0.02
            loop.step(clock.now)
            if len(made) == 2:
                break
            time.sleep(0.002)
        assert len(made) == 2 and made[1].counters is status.counters
        before = status.counters.blocks
        loop.step(clock.now + 0.02)
        assert made[1].counters is status.counters and status.counters.blocks == before + 1
        assert status.counters.usb_errors == 2  # what was counted stays
    finally:
        loop.close()
        host.close()


def test_wrapping_displays_hand_their_counters_to_the_drivers_they_make():
    from libre_panel.config import DeviceConfig
    from libre_panel.devices.auto import AutoDisplay
    from libre_panel.devices.turzx import TurzxDisplay
    from libre_panel.devices.virtual import VirtualDisplay

    counters = Counters()
    auto = AutoDisplay(DeviceConfig(driver="auto", output="unused.png"))
    auto.counters = counters
    auto.open()  # no panel here: the PNG file stands in
    try:
        assert isinstance(auto._impl, VirtualDisplay) and auto._impl.counters is counters
    finally:
        auto.close()
    turzx = TurzxDisplay(DeviceConfig(driver="turzx"))
    turzx.counters = counters
    assert turzx._driver(None).counters is counters  # the driver for a USB panel
    assert Counters() != Counters(bytes=1)  # a plain record

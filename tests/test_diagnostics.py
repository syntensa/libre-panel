"""Diagnostics for the editor's pages: what a page can tell about the app (the picture on
the panel, the log, whether it runs here), whether a message will show, and the counters
of the main loop and the video link."""

from types import SimpleNamespace

from PIL import Image, ImageChops
from test_app_cli import MemoryStream, StepClock

from libre_panel import app as app_module
from libre_panel.config import Config, SensorsConfig, log_file
from libre_panel.plugins import PageContext, PluginHost, Toast
from libre_panel.sensors.base import SensorHub
from libre_panel.sensors.demo import DemoProvider


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

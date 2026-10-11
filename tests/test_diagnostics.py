"""Diagnostics for the editor's pages: what a page can tell about the app (the picture on
the panel, the log, whether it runs here), whether a message will show, and the counters
of the main loop and the video link."""

from types import SimpleNamespace

from PIL import Image, ImageChops
from test_app_cli import MemoryStream, StepClock

from libre_panel import app as app_module
from libre_panel.config import Config, SensorsConfig, log_file
from libre_panel.devices.base import DeviceError
from libre_panel.plugins import Delivery, PageContext, PluginHost, Toast
from libre_panel.plugins.host import ServiceHost
from libre_panel.sensors.base import SensorHub
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

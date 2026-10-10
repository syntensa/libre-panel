"""Toasts and transitions over the frame: the toast layer's rules (how long a
toast stays, what a transition or a screen does to it, several at once)."""

from types import SimpleNamespace

import pytest
from PIL import Image

from libre_panel import app
from libre_panel.plugins import Toast, ToastStyle, Transition
from libre_panel.plugins.render import RenderContext
from libre_panel.render import overlays
from libre_panel.render.overlays import ToastLayer, switch_policy
from libre_panel.render.renderer import Renderer
from libre_panel.theme.model import parse_theme

FRAME = Image.new("RGB", (320, 240), "black")


def renderer(width=320, height=240, screen=None, palette=None, **toast):
    data = {
        "format": "libre-panel-theme/1",
        "name": "overlay test",
        "display": {"model": "custom", "width": width, "height": height},
        "background": {"color": "#000000"},
        "palette": palette or {"accent": "#00ffff", "bg": "#000000"},
        "widgets": [],
        "toast": toast,
    }
    made = Renderer(parse_theme(data))
    if screen is not None:  # a screen that shows some kinds itself
        made.screen = SimpleNamespace(suppresses=screen)
    return made


class Timed(ToastStyle):
    """Records what the layer asks and draws; a refresh in place ends sooner."""

    name = "test.timed"
    api = 1
    options = {"hold": ("number", 3.0)}
    leave_s = 0.5
    asked: list = []

    def __init__(self, context, options):
        super().__init__(context, options)
        self.drawn = []
        self.closed = False

    def hold_s(self, toast, theme_seconds):
        Timed.asked.append(("hold", toast.text, theme_seconds))
        return self.options["hold"]

    def lifetime(self, toast, previous):
        Timed.asked.append(("lifetime", toast.text, previous and previous[0].text))
        return toast.seconds + self.leave_s - (0.25 if previous else 0.0)

    def draw(self, frame, toast, age, previous=None):
        self.drawn.append((toast.text, round(age, 3), previous and previous[0].text))
        out = frame.copy()
        out.putpixel((0, 0), (255, 255, 255))
        return out

    def close(self):
        self.closed = True


@pytest.fixture
def timed(monkeypatch):
    """The test style, installed like a built-in one."""
    monkeypatch.setitem(overlays.BUILT_IN_STYLES, "test.timed", Timed)
    Timed.asked = []
    return Timed


# -- T1: how long a toast stays -----------------------------------------------------


def test_the_style_says_how_long_a_toast_stays(timed):
    layer = ToastLayer(renderer(style="test.timed", seconds=2))
    layer.add([Toast("30", kind="volume", seconds=1.0)])
    layer.advance(0.0)
    assert layer.ends == 1.5  # its own 1 s + leave_s, asked once when it starts
    layer.add([Toast("32", kind="volume", seconds=1.0)])
    layer.advance(1.0)
    assert layer.current[0].text == "32" and layer.ends == pytest.approx(2.25)  # in place
    assert [a for a in Timed.asked if a[0] == "lifetime"] == [
        ("lifetime", "30", None),
        ("lifetime", "32", "30"),
    ]
    layer.advance(2.24)
    assert layer.current is not None
    layer.advance(2.25)
    assert layer.current is None  # gone when the style said, not 1 + 0.5 s after it came


def test_styles_written_before_lifetime_still_work():
    class Old(ToastStyle):  # 0.4: no lifetime, no hold_s, no previous
        name = "test.old"
        api = 1
        leave_s = 0.1

        def draw(self, frame, toast, age):
            return frame

    layer = ToastLayer(renderer(seconds=2))
    layer.style = Old(RenderContext(renderer()), {})
    layer._previous = False
    layer.add([Toast("old")])
    layer.advance(0.0)
    assert layer.current[0].seconds == 2 and layer.ends == pytest.approx(2.1)
    assert layer.draw(FRAME, 1.0) is FRAME


def test_the_style_goes_on_across_theme_switches(timed):
    layer = ToastLayer(renderer(style="test.timed", options={"hold": 2.5}))
    style = layer.style
    layer.add([Toast("song", kind="music")])
    layer.advance(0.0)
    other = renderer(style="test.timed", options={"hold": 2.5}, palette={"accent": "#ff0000"})
    layer.set_renderer(other)
    assert layer.style is style and not style.closed  # what it keeps per toast goes on
    assert style.context.palette["accent"] == "#ff0000"  # drawn with the new theme's colours
    layer.set_renderer(renderer(style="test.timed", options={"hold": 1.0}))
    assert layer.style is not style and style.closed  # other options: a new one
    kept = layer.style
    layer.set_renderer(renderer(480, 320, style="test.timed", options={"hold": 1.0}))
    assert layer.style is not kept  # another size: a new one too


# -- T2: the hold time of a toast without its own -------------------------------------


def test_the_style_gives_the_hold_time_of_a_toast_without_its_own(timed):
    layer = ToastLayer(renderer(style="test.timed", seconds=2, options={"hold": 3.0}))
    layer.add([Toast("no time of its own"), Toast("own", kind="own", rank=1, seconds=2.0)])
    layer.advance(0.0)
    assert ("hold", "no time of its own", 2) in Timed.asked  # with the theme's hold time
    assert not any(a[:2] == ("hold", "own") for a in Timed.asked)  # 2.0 s of its own stays
    assert layer.current[0].text == "own" and layer.current[0].seconds == 2.0
    assert layer.queue[0].seconds == 3.0  # decided before anything else saw it


def test_a_failing_style_does_not_stop_the_toasts(caplog):
    class Failing(ToastStyle):
        name = "test.failing"
        api = 1

        def hold_s(self, toast, theme_seconds):
            raise RuntimeError("no design")

        def lifetime(self, toast, previous):
            raise RuntimeError("no clock")

        def draw(self, frame, toast, age, previous=None):
            return frame

    layer = ToastLayer(renderer(seconds=2))
    layer.style = Failing(RenderContext(renderer()), {})
    layer.add([Toast("still shown")])
    layer.advance(0.0)
    layer.advance(0.1)
    assert layer.current[0].seconds == 2 and layer.ends == pytest.approx(2.25)  # the defaults
    assert caplog.text.count("no design") == 1 and "no clock" in caplog.text


# -- T3: a transition that hides toasts -----------------------------------------------


def test_a_transition_that_hides_toasts(timed):
    layer = ToastLayer(renderer(style="test.timed", seconds=2, queue=False))
    layer.add([Toast("volume", kind="volume")])
    layer.advance(0.0)
    assert layer.draw(FRAME, 0.0) is not FRAME
    # the transition plays: toasts are decided as usual, but none is drawn
    layer.add([Toast("device", kind="device", rank=1), Toast("low", kind="low", rank=-1)])
    layer.advance(1.0)
    assert layer.draw(FRAME, 1.0, hidden=True) is FRAME
    assert layer.showing(1.02) == (layer.current[0], pytest.approx(0.02))  # screens see it
    assert layer.current[0].text == "device" and layer.current[2] is None  # it starts again
    for i in range(1, 201):  # each hidden frame starts it again: it does not run out meanwhile
        layer.advance(1.0 + i * 0.02)
        assert layer.draw(FRAME, 1.0 + i * 0.02, hidden=True) is FRAME
    assert layer.current[0].text == "device"
    # the frame the transition ends draws it, fresh: one frame old, nothing before it
    layer.advance(5.02)
    assert layer.draw(FRAME, 5.02) is not FRAME
    assert layer.style.drawn[-1] == ("device", 0.02, None)
    assert layer.ends == pytest.approx(5.0 + 3.0 + 0.5)


def test_hide_is_decided_after_the_transition_ends():
    """In the main loop the frame a hiding transition ends already shows the toast."""

    class Hiding(Transition):
        name = "test.hiding"
        duration = 1.0
        toasts = "hide"

        def frame(self, old, new, t):
            return Image.new("RGB", new.size, "magenta")

    blend = (Hiding(), FRAME, 0.0)
    frame, blend = app._blend(blend, FRAME, 0.5)
    assert blend is not None and frame.getpixel((0, 0)) == (255, 0, 255)
    frame, blend = app._blend(blend, FRAME, 1.0)
    assert blend is None and frame is FRAME  # over: nothing hides the toast any more


# -- T4: what a switch does while a transition plays ------------------------------------


def test_a_transition_decides_how_a_switch_goes_on():
    class Plain(Transition):
        switch = "follow"

    asked = []

    class Asking(Transition):
        def on_switch(self, theme, mode, mode_changed):
            asked.append((theme, mode, mode_changed))
            return "restart" if mode_changed else "nonsense"

    class Broken(Transition):
        switch = "restart"

        def on_switch(self, theme, mode, mode_changed):
            raise RuntimeError("no idea")

    assert switch_policy(Plain(), "report", None, None) == "follow"  # the default: switch
    assert switch_policy(Asking(), "report", "game", "game") == "wait"  # not a policy: wait
    assert switch_policy(Asking(), "normal", None, "game") == "restart"
    assert asked == [("report", "game", False), ("normal", None, True)]
    assert switch_policy(Broken(), "x", None, None) == "restart"  # its switch, logged


# -- T5: a screen that shows a toast's kind itself ---------------------------------------


def test_a_toast_on_show_goes_on_unseen_on_a_screen_that_shows_its_kind(timed):
    plain = renderer(style="test.timed", seconds=2, queue=False)
    studio = renderer(screen={"volume"}, style="test.timed", seconds=2, queue=False)
    layer = ToastLayer(plain)
    layer.add([Toast("volume 30", kind="volume", seconds=2.0)])
    layer.advance(0.0)
    layer.set_renderer(studio)
    assert layer.current[0].text == "volume 30"  # alive ...
    assert layer.draw(FRAME, 0.5) is FRAME and layer.showing(0.5) is None  # ... unseen
    layer.add([Toast("volume 32", kind="volume"), Toast("quiet", kind="quiet", rank=-1)])
    layer.advance(0.6)
    assert layer.current[0].text == "volume 30"  # a volume toast that comes here is left out
    assert not layer.queue  # and it still holds back a lower rank
    layer.set_renderer(plain)
    layer.advance(1.0)
    assert layer.showing(1.0) == (layer.current[0], 1.0)  # back, on its own clock
    assert layer.draw(FRAME, 1.0) is not FRAME
    assert layer.style.drawn[-1] == ("volume 30", 1.0, None)


def test_a_switched_off_kind_is_left_out_and_holds_back_with_a_queue(timed):
    layer = ToastLayer(renderer(style="test.timed", seconds=2))
    layer.add([Toast("song", kind="music", seconds=1.0)])
    layer.advance(0.0)
    layer.set_renderer(renderer(style="test.timed", seconds=2, off=["music"]))
    layer.add([Toast("mail", kind="mail")])
    layer.advance(0.5)
    assert layer.current[0].text == "song" and [q.text for q in layer.queue] == ["mail"]
    layer.advance(1.5)  # the hidden one ran out on its own clock: the next one shows
    assert layer.current[0].text == "mail"


# -- T6: several toasts at once without a queue ------------------------------------------


def test_without_a_queue_toasts_are_decided_one_by_one(timed):
    layer = ToastLayer(renderer(style="test.timed", seconds=2, queue=False))
    layer.add(
        [
            Toast("volume 30", kind="volume"),
            Toast("CPU 95 °C", kind="warning", rank=2),  # takes over the volume at once
            Toast("device", kind="device", rank=1),  # lower: dropped
            Toast("CPU 96 °C", kind="warning", rank=1),  # the same key: in place, any rank
        ]
    )
    layer.advance(0.0)
    toast, _began, previous = layer.current
    assert toast.text == "CPU 96 °C" and previous[0].text == "CPU 95 °C" and previous[1] == 0
    assert not layer.queue
    assert [a[1:] for a in Timed.asked if a[0] == "lifetime"] == [
        ("volume 30", None),
        ("CPU 95 °C", "volume 30"),
        ("CPU 96 °C", "CPU 95 °C"),
    ]
    layer.add([Toast("song", kind="music", rank=0)])
    layer.advance(0.5)
    assert layer.current[0].text == "CPU 96 °C"  # a lower rank arriving later is dropped too


def test_without_a_queue_what_waited_for_a_transition_comes_first(timed):
    layer = ToastLayer(renderer(style="test.timed", seconds=2, queue=False))
    layer.add([Toast("a", kind="a", rank=1), Toast("b", kind="b", rank=1)])
    layer.advance(0.0, hold=True)  # a transition holds them back
    assert layer.current is None and [q.text for q in layer.queue] == ["a", "b"]
    layer.add([Toast("c", kind="c", rank=0)])
    layer.advance(1.0)  # the newest of the highest rank that waited, then the one that came
    assert layer.current[0].text == "b" and not layer.queue

"""Toasts and transitions over the frame: the toast layer's rules (how long a
toast stays, what a transition or a screen does to it, several at once), the
band toast style, and the tube and entrance transitions."""

import math
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from libre_panel import app
from libre_panel.plugins import Toast, ToastStyle, Transition
from libre_panel.plugins.render import RenderContext
from libre_panel.render import overlays
from libre_panel.render.overlays import ToastLayer, switch_policy, transition
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
    entrance = transition("entrance")
    assert switch_policy(entrance, "report", None, None) == "follow"  # a report is revealed
    assert switch_policy(entrance, "clock", None, "game") == "restart"  # the game ended


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


# -- the card ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("color", "edge"),
    [
        ("#ff8000", (255, 128, 0)),
        ("@net", (10, 200, 30)),  # a name from the theme's palette, as the plugin docs have it
        ((12, 34, 56), (12, 34, 56)),
        ([300, -4, 56.7], (255, 0, 56)),
        ("not-a-colour", (0, 255, 255)),  # a colour nobody can read: the level's (the accent)
        (7, (0, 255, 255)),
        (math.inf, (0, 255, 255)),
    ],
)
def test_the_card_takes_the_accent_a_toast_names_or_the_levels(color, edge):
    """``payload["color"]`` was handed to Pillow as it came: "@net" and a colour nobody can
    read raised, and the toast was not shown at all."""
    made = renderer(480, 320, palette={"accent": "#00ffff", "bg": "#000000", "net": "#0AC81E"})
    layer = ToastLayer(made)
    layer.add([Toast("hello", seconds=3, payload={"color": color})])
    layer.advance(0.0)
    frame = layer.apply(Image.new("RGB", (480, 320)), 1.0)
    assert layer._failed is None
    box = frame.getbbox()
    assert box is not None
    shown = frame.getpixel((box[0] + 1, (box[1] + box[3]) // 2))  # on the card's coloured edge
    assert max(abs(a - b) for a, b in zip(shown, edge, strict=True)) <= 8  # (resampled)


# -- the band ----------------------------------------------------------------------


def band_layer(width, height, **toast):
    return ToastLayer(renderer(width, height, style="band", seconds=2, queue=False, **toast))


@pytest.mark.parametrize("size", [(1920, 480), (480, 320), (320, 480)])
def test_the_band_rolls_in_stays_and_leaves(size):
    layer = band_layer(*size, anchor="bottom")
    plain = Image.new("RGB", size, (10, 20, 30))
    payload = {"label": "Volume", "progress": 0.3, "color": "#13E5D7"}
    layer.add([Toast("30 %", kind="volume", payload=payload)])
    layer.advance(0.0)
    assert layer.ends == pytest.approx(0.45 + 2.0 + 0.32)  # the defaults of its options
    reach = []  # how far down the band reaches, frame by frame
    for i in range(0, 140):
        frame = layer.apply(plain, i * 0.02)
        assert frame.size == size
        reach.append(_reach(frame, plain))
    assert reach[0] == 0  # nothing on the first frame: the edge starts in the centre
    assert 0 < reach[5] < reach[20]  # the edge, then the band rolling down
    assert all(a <= b for a, b in zip(reach[:22], reach[1:23], strict=True))
    assert all(r == size[1] for r in reach[30:120])  # down to the bottom: it stands
    assert reach[-1] < size[1]  # and leaves
    assert layer.apply(plain, 3.0) is plain and not layer.active  # gone: the frame as it was


def test_the_band_refreshes_in_place_without_blinking():
    layer = band_layer(480, 320, anchor="bottom")
    alone = band_layer(480, 320, anchor="bottom")  # gets no "36": the band it continues
    plain = Image.new("RGB", (480, 320), (10, 20, 30))

    def shown(now, text=None, value=None):
        if text is not None:
            layer.add([Toast(text, kind="volume", payload={"progress": value})])
            if text != "36":
                alone.add(layer.inbox[-1:])
        alone.apply(plain, now)
        return _reach(layer.apply(plain, now), plain)

    shown(0.0, "30", 0.30)
    rolling = [shown(0.02 * i) for i in range(1, 8)]
    # the same key while rolling in: it rolls on, not from the start
    assert shown(0.16, "32", 0.32) >= rolling[-1]
    assert layer.ends == pytest.approx(0.45 + 2.0 + 0.32)  # its life began with "30"
    standing = shown(1.0)
    assert shown(1.5, "34", 0.34) == standing  # standing: stays as it is
    assert layer.ends == pytest.approx(1.5 + 2.0 + 0.32)
    leaving = [shown(3.5 + 0.02 * i) for i in range(1, 9)]
    assert leaving[-1] < standing  # it is leaving ...
    back = shown(3.68, "36", 0.36)
    assert back == _reach(alone.apply(plain, 3.68), plain)  # ... and goes on from where it is
    assert back < shown(3.7) < shown(3.8) and shown(4.3) == standing  # rolling back up


def test_the_band_bar_glides_to_its_value():
    from libre_panel.render.band import BandStyle

    style = BandStyle(RenderContext(renderer(480, 320)), {})
    style.anchor = "bottom"
    toast = Toast("30", kind="volume", seconds=2.0, payload={"progress": 0.3})
    style.lifetime(toast, None)
    run = style._runs[toast]
    assert style._bar(toast, run, 0.0) == pytest.approx(0.3)  # the first value is taken
    assert style._bar(toast, run, 0.48) == pytest.approx(0.3)
    louder = Toast("80", kind="volume", seconds=2.0, payload={"progress": 0.8})
    style.lifetime(louder, (toast, 0.5))  # the same key: it goes on from where it stood
    run = style._runs[louder]
    first = 0.3 + 0.5 * (1 - math.exp(-0.02 / 0.09))  # on time since it last moved
    assert style._bar(louder, run, 0.0) == pytest.approx(first)
    assert style._bar(louder, run, 0.09) == pytest.approx(0.8 - (0.8 - first) / math.e)
    assert style._bar(louder, run, 1.0) == pytest.approx(0.8, abs=1e-3)


def test_another_message_rolls_down_inside_the_open_band():
    layer = band_layer(480, 320, anchor="bottom")
    plain = Image.new("RGB", (480, 320), (10, 20, 30))
    layer.add([Toast("music", kind="music", payload={"color": "#00ff00"})])
    for i in range(60):
        standing = layer.apply(plain, i * 0.02)
    layer.add([Toast("warning", kind="warning", rank=1, payload={"color": "#ff0000"})])
    first = layer.apply(plain, 1.2)
    assert layer.current[2][0].text == "music"  # it took over, with the one before
    assert layer.ends == pytest.approx(1.2 + 2.0 + 0.32)  # the band was open: it stays open
    box = ImageChops.difference(first, plain).getbbox()
    assert box == ImageChops.difference(standing, plain).getbbox()  # the band stays as large
    middle = layer.apply(plain, 1.32)
    later = layer.apply(plain, 1.5)
    assert _differs(first, middle) and _differs(middle, later)  # the new content rolls down
    assert layer.apply(plain, 1.6).tobytes() == later.tobytes()  # in 0.26 s, then it stands


@pytest.mark.parametrize("anchor", ["top", "bottom-left"])
def test_the_band_keeps_clear_of_the_bezel(anchor):
    data = {
        "format": "libre-panel-theme/1",
        "name": "bar",
        "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
        "widgets": [],
        "toast": {"style": "band", "anchor": anchor},
    }
    layer = ToastLayer(Renderer(parse_theme(data)))
    plain = Image.new("RGB", (1920, 480))
    layer.add([Toast("hello", icon="fan", payload={"label": "Notice", "progress": 0.5})])
    layer.advance(0.0)
    box = ImageChops.difference(layer.apply(plain, 1.0), plain).getbbox()
    assert box is not None and box[1] >= 18 and box[3] <= 480  # the 9.2" hides 18 px on top
    assert (box[1] < 240) == (anchor == "top")


def test_the_band_takes_published_pictures(tmp_path):
    from libre_panel.sensors.base import Snapshot

    made = renderer(480, 320, style="band", anchor="bottom")
    cover = Image.new("RGB", (64, 64), (255, 128, 0))
    made.render(Snapshot(images={"media.cover": cover}), 0.0)
    context = RenderContext(made)
    assert context.image("media.cover") is cover and context.image("nothing") is None
    layer = ToastLayer(made)
    layer.add([Toast("song", kind="music", payload={"image": "media.cover", "badge": cover})])
    layer.advance(0.0)
    frame = layer.apply(Image.new("RGB", (480, 320)), 1.0)
    assert (255, 128, 0) in {frame.getpixel((x, y)) for x in range(10, 90) for y in range(200, 300)}


# -- the tube and the entrance ----------------------------------------------------------


def frames_of(kind, size, steps=24):
    old = Image.new("RGB", size, (200, 40, 40))
    new = Image.new("RGB", size, (30, 60, 200))
    return old, new, [kind.frame(old, new, i / steps) for i in range(steps + 1)]


@pytest.mark.parametrize("size", [(1920, 480), (480, 320), (320, 480)])
@pytest.mark.parametrize("name", ["tube", "entrance"])
def test_light_transitions_at_every_size(name, size):
    context = RenderContext(renderer(*size))
    kind = transition((name, {"title": "Space Game", "kind": "start"}), context)
    old, new, frames = frames_of(kind, size)
    assert all(f.size == size and f.mode == "RGB" for f in frames)
    assert not _differs(frames[0], old)  # it starts from the old frame,
    moving = [_differs(a, b) for a, b in zip(frames[:-1], frames[1:], strict=True)]
    assert sum(moving) >= len(moving) - 3  # changes on the way
    assert frames[-1] is new  # and ends on the new screen
    blend = (kind, old, 0.0)  # in the main loop it ends on the new frame too
    assert app._blend(blend, new, kind.duration) == (new, None)


def test_the_tube_changes_only_rows_near_its_edges():
    size = (1920, 480)
    kind = transition("tube", RenderContext(renderer(*size)))
    old, new, frames = frames_of(kind, size, steps=45)
    for before, after in zip(frames[5:40], frames[6:41], strict=True):
        rows = _rows_changed(before, after)
        assert rows and len(rows) < 240  # never the whole frame, as a crossfade would


def test_the_tube_plays_as_a_switch_wants_it():
    tube = transition("tube")
    assert (tube.duration, tube.switch, tube.toasts, tube.from_shown) == (
        0.9,
        "restart",
        "over",
        True,
    )
    entrance = transition("entrance")
    assert (entrance.duration, entrance.switch, entrance.toasts) == (3.2, "follow", "hide")
    assert entrance.from_shown


def test_the_entrance_shows_a_logo_or_a_title_and_a_text():
    from libre_panel import i18n
    from libre_panel.sensors.base import Snapshot

    i18n.set_language("de")
    assert transition(("entrance", {"kind": "end"})).text == "SPIEL BEENDET"
    i18n.set_language("en")
    assert transition(("entrance", {"kind": "start"})).text == "GAME MODE"
    assert transition(("entrance", {"text": "Round over"})).text == "ROUND OVER"
    made = renderer(1920, 480)
    logo = Image.new("RGBA", (300, 100), (255, 255, 0, 255))
    made.render(Snapshot(images={"game.logo": logo}), 0.0)
    old, new = Image.new("RGB", (1920, 480)), Image.new("RGB", (1920, 480), "blue")
    for given in ("game.logo", logo):
        kind = transition(("entrance", {"logo": given, "kind": "start"}), RenderContext(made))
        frame = kind.frame(old, new, 1.6 / 3.2)
        assert (255, 255, 0) in {frame.getpixel((x, 220)) for x in range(800, 1120)}
    titled = transition(("entrance", {"title": "Doom"}), RenderContext(made))
    frame = titled.frame(old, new, 1.6 / 3.2)
    assert frame.getbbox() is not None and titled.text == ""  # no text: nothing types in


def _differs(a, b):
    return ImageChops.difference(a, b).getbbox() is not None


def _rows_changed(a, b):
    """The rows in which ``b`` differs from ``a``."""
    channels = ImageChops.difference(a, b).split()
    most = channels[0]
    for channel in channels[1:]:
        most = ImageChops.lighter(most, channel)
    return [y for y, changed in enumerate(most.getprojection()[1]) if changed]


def _reach(frame, plain):
    """How far down the frame differs from ``plain``: where a band at the bottom ends."""
    box = ImageChops.difference(frame, plain).getbbox()
    return 0 if box is None else box[3]

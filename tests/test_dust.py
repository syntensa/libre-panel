"""Depth dust: the theme's look.dust, its determinism, its sources, and what it costs when off."""

import numpy as np
import pytest
from PIL import Image

from libre_panel.render import dust as dust_module
from libre_panel.render.dust import Dust
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import THEME_FORMAT, ThemeError, parse_theme

SIZE = (480, 160)


def theme(look=None, size=SIZE, widgets=None, palette=None):
    data = {
        "format": THEME_FORMAT,
        "display": {"width": size[0], "height": size[1]},
        "background": {"color": "#0a1119"},
        "palette": palette or {},
        "widgets": widgets
        if widgets is not None
        else [
            {
                "type": "text",
                "id": "t",
                "x": 20,
                "y": 10,
                "text": "SPUR",
                "font_size": 60,
                "color": "#e8f6ff",
            },
            {"type": "rect", "id": "rail", "x": 20, "y": 90, "w": 300, "h": 4, "color": "#22d3ee"},
        ],  # fmt: skip
    }
    if look is not None:
        data["look"] = look
    return parse_theme(data)


def frame(size=SIZE, bars=((20, 60, 200, 6),), color=(230, 250, 255)):
    """A dark RGBA frame with bright bars: the elements the dust falls from."""
    a = np.zeros((size[1], size[0], 4), np.uint8)
    a[..., :3], a[..., 3] = (10, 17, 25), 255
    for x, y, w, h in bars:
        a[y : y + h, x : x + w, :3] = color
    return Image.fromarray(a, "RGBA")


def run(dust, canvas, seconds=12, fps=50, start=100.0, **kw):
    out = None
    for n in range(round(seconds * fps)):
        out = dust.apply(canvas, start + n / fps, **kw).copy()
    return out


def pixels(image):
    return np.asarray(image.convert("RGB"), np.int16)


# -- the theme block -------------------------------------------------------------------


def test_a_theme_without_look_has_no_dust():
    assert theme().look is None
    assert theme(look={}).look is None
    assert theme(look={"dust": False}).look is None


def test_dust_true_is_all_defaults_and_round_trips():
    t = theme(look={"dust": True})
    assert t.look == {
        "dust": {
            "enabled": True,
            "count": 1800,
            "rate": 25,
            "depth": 1.0,
            "strength": 1.0,
            "colors": [],
            "areas": [],
        }  # fmt: skip
    }
    assert parse_theme(t.to_dict()).look == t.look


def test_dust_settings_are_checked():
    ok = theme(
        look={
            "dust": {
                "count": 900,
                "rate": 12,
                "depth": 1.5,
                "strength": 0.5,
                "colors": ["#ffffff", "@accent"],
                "areas": [{"x": 0, "y": 0, "w": 100, "h": 50, "dust": False}],
            }
        },
        palette={"accent": "#22d3ee"},
    )
    assert ok.look["dust"]["areas"] == [{"x": 0, "y": 0, "w": 100, "h": 50, "dust": False}]
    bad = [
        {"count": -1}, {"count": 99999}, {"rate": 0}, {"depth": 9}, {"strength": "a lot"},
        {"colors": "red"}, {"colors": ["nope"]}, {"colors": ["@missing"]}, {"areas": {}},
        {"areas": [3]}, {"areas": [{"x": 0, "y": 0, "w": 0, "h": 5}]},
        {"areas": [{"x": 0, "y": 0, "w": 5, "h": 5, "dust": "yes"}]}, {"enabled": 1},
    ]  # fmt: skip
    for dust in bad:
        with pytest.raises(ThemeError, match="look.dust"):
            theme(look={"dust": dust})
    with pytest.raises(ThemeError, match="look must be"):
        theme(look=[])
    with pytest.raises(ThemeError, match="look.dust"):
        theme(look={"dust": 3})


def test_unknown_look_fields_are_warned_about():
    assert any("look" in w and "sparkle" in w for w in theme(look={"sparkle": 1}).warnings)


# -- off costs nothing ---------------------------------------------------------------------


def test_without_dust_nothing_of_it_runs(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the dust was used")

    monkeypatch.setattr(Dust, "__init__", boom)
    monkeypatch.setattr(Dust, "apply", boom)
    for look in (None, {"dust": False}, {"dust": {"enabled": False}}):
        renderer = Renderer(theme(look=look), animate=True, preview=True)
        renderer.continuous = True
        frame_, _ = renderer.render(demo_snapshot(fixed_time=1_700_000_000), 1.0)
        assert frame_.size == SIZE
        assert renderer._dust is None


def test_dust_is_drawn_in_video_mode_and_previews_only():
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    plain = Renderer(theme(), animate=True)
    plain.continuous = True
    base, _ = plain.render(snapshot, 1.0)
    look = {"dust": {"count": 6000}}
    kinds = {"video": (True, False), "preview": (False, True), "png path": (False, False)}
    for name, (continuous, preview) in kinds.items():
        renderer = Renderer(theme(look=look), animate=True, preview=preview)
        renderer.continuous = continuous
        out = None
        for n in range(150):
            out, _ = renderer.render(snapshot, 1.0 + n / 50)
            renderer.shown = out
        assert (np.asarray(out) != np.asarray(base)).any() == (continuous or preview), name
        assert (renderer._dust is not None) == (continuous or preview), name


def test_dust_is_the_same_for_the_same_frames_and_times():
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    frames = []
    for _ in range(2):
        renderer = Renderer(theme(look={"dust": True}), animate=True)
        renderer.continuous = True
        for n in range(200):
            out, _ = renderer.render(snapshot, 5.0 + n / 50)
            renderer.shown = out
        frames.append(out.tobytes())
    assert frames[0] == frames[1]


# -- the dust itself -----------------------------------------------------------------------


def test_seeded_dust_with_a_fake_clock_is_deterministic():
    canvas = frame()
    a, b = Dust(SIZE, {}, seed=3), Dust(SIZE, {}, seed=3)
    assert run(a, canvas).tobytes() == run(b, canvas).tobytes()
    assert (
        run(Dust(SIZE, {}, seed=4), canvas).tobytes()
        != run(Dust(SIZE, {}, seed=3), canvas).tobytes()
    )
    # the same times, however they are reached, give the same dust
    one, two = Dust(SIZE, {}, seed=9), Dust(SIZE, {}, seed=9)
    for n in range(300):
        one.apply(canvas, 10 + n / 50)
    for n in range(300):
        out = two.apply(canvas, 10 + n / 50)
    assert np.array_equal(
        np.asarray(one.apply(canvas, 10 + 300 / 50)), np.asarray(two.apply(canvas, 16.0))
    )
    assert out is not None


def test_grains_fall_away_from_the_elements_and_carry_their_colour():
    canvas = frame(bars=((100, 60, 200, 6),), color=(0, 255, 255))
    base = pixels(canvas)
    out = pixels(run(Dust(SIZE, {"count": 2000}), canvas, seconds=15))
    added = out - base
    changed = np.nonzero(added.any(axis=-1))
    assert changed[0].size > 100
    # cyan source, cyan dust: red stays out, and the bar itself (bright) shows none
    assert added[..., 0].max() <= 2 and added[..., 1].max() > 20
    assert not added[60:66, 100:300].any()
    # the grains drift towards the vanishing point (the middle, a bit above centre)
    assert abs(changed[1].mean() - SIZE[0] / 2) < abs(200 - SIZE[0] / 2) + 60


def test_the_colours_of_the_theme_replace_the_sources():
    canvas = frame(color=(0, 255, 255))
    out = pixels(run(Dust(SIZE, {"count": 1500}, colors=[(255, 0, 0)]), canvas, seconds=12))
    added = out - pixels(canvas)
    assert added[..., 0].max() > 20 and added[..., 2].max() <= 2


def test_a_bright_element_hides_the_dust_behind_it():
    canvas = frame(bars=((0, 0, 480, 160),), color=(250, 250, 250))  # all bright
    out = run(Dust(SIZE, {"count": 3000}), canvas, seconds=10)
    assert np.array_equal(pixels(out), pixels(canvas))


def test_areas_say_where_dust_is():
    canvas = frame(bars=((20, 60, 200, 6), (300, 100, 140, 6)))  # an element in each half

    def added(areas):
        dust = Dust(SIZE, {"count": 3000, "areas": areas})
        return pixels(run(dust, canvas)) - pixels(canvas)

    box = {"x": 0, "y": 0, "w": 240, "h": 160}
    off = added([{**box, "dust": False}])
    assert not off[:, :240].any() and off[:, 240:].any()
    only = added([{**box, "dust": True}])
    assert not only[:, 240:].any() and only[:, :240].any()
    cut = added([{**box, "dust": True}, {"x": 100, "y": 0, "w": 50, "h": 160, "dust": False}])
    assert not cut[:, 100:150].any() and not cut[:, 240:].any() and cut[:, :100].any()


def test_a_blank_frame_has_no_dust_until_there_is_something_to_fall_from():
    dust = Dust(SIZE, {})
    blank = frame(bars=())
    assert dust.apply(blank, 1.0) is blank and dust.sources is None
    assert dust.apply(blank, 1.2) is blank  # not asked again every frame
    dust.apply(frame(), 2.0)
    assert dust.sources is not None


def test_the_stock_of_sources_is_taken_every_four_seconds():
    dust = Dust(SIZE, {})
    canvas = frame()
    times = [n / 50 for n in range(50 * 13)]  # 13 s
    for t in times:
        dust.apply(canvas, 100 + t)
    assert dust.renewals == 4  # at 0, 4, 8 and 12 s


def test_a_toast_that_begins_takes_the_stock_again_and_once_more_when_it_is_in():
    dust = Dust(SIZE, {})
    canvas, shown = frame(), frame(bars=((20, 60, 200, 6), (20, 100, 200, 6)))
    dust.apply(canvas, 100.0, shown)
    first = dust.renewals
    toast = object()
    dust.apply(canvas, 101.0, shown, (toast, 0.0))  # it begins: age 0
    assert dust.renewals == first + 1
    dust.apply(canvas, 101.2, shown, (toast, 0.2))  # on show: nothing new
    assert dust.renewals == first + 1
    dust.apply(canvas, 101.6, shown, (toast, 0.6))  # rolled in: once more
    assert dust.renewals == first + 2
    dust.apply(canvas, 101.8, shown, (toast, 0.8))
    assert dust.renewals == first + 2
    dust.apply(canvas, 102.0, shown, None)
    dust.apply(canvas, 102.1, shown, (object(), 0.0))  # another one: begins again
    assert dust.renewals == first + 3


def test_the_stock_comes_from_the_shown_frame():
    """What the panel showed (a toast on it) is what the dust falls from."""
    dust = Dust(SIZE, {"count": 800})
    canvas = frame(bars=())  # nothing to fall from in the frame being drawn ...
    shown = frame(bars=((300, 100, 100, 8),))  # ... but the panel showed a bar
    for n in range(100):
        out = dust.apply(canvas, 100 + n / 50, shown)
    assert dust.sources is not None
    assert (dust.sources[0] >= 300).all() and (dust.sources[0] < 400).all()
    assert out is not None


def test_the_grains_move_at_the_given_rate():
    canvas = frame()
    dust = Dust(SIZE, {"rate": 10})
    dust.apply(canvas, 10.0)
    taps = dust.taps
    dust.apply(canvas, 10.03)  # less than 100 ms later: the same grains
    assert dust.taps is taps
    dust.apply(canvas, 10.11)
    assert dust.taps is not taps


def test_the_count_follows_the_area_of_the_panel():
    assert Dust((1920, 480), {"count": 1800}).count == 1800
    assert Dust((960, 240), {"count": 1800}).count == 450
    assert Dust((16, 16), {"count": 1800}).count == dust_module.MIN_COUNT
    assert Dust((1920, 480), {"count": 0}).count == 0


@pytest.mark.parametrize("size", [(1920, 480), (320, 240), (321, 241), (64, 48)])
def test_dust_on_panels_of_every_size(size):
    canvas = frame(size, bars=((8, size[1] // 2, size[0] // 2, 4),))
    dust = Dust(size, {"count": 2400})
    out = run(dust, canvas, seconds=6)
    assert out.size == size
    assert (pixels(out) != pixels(canvas)).any()


def test_a_canvas_that_cannot_be_written_in_place_gives_the_same_picture():
    canvas = frame()
    fast, slow = Dust(SIZE, {"count": 2400}, seed=5), Dust(SIZE, {"count": 2400}, seed=5)
    assert fast.canvas.linked  # this Pillow allows it
    slow.canvas.linked = False
    for n in range(120):
        a = fast.apply(canvas, 50 + n / 50)
        b = slow.apply(canvas, 50 + n / 50)
    assert a.tobytes() == b.tobytes()


def test_the_dust_leaves_the_frame_it_was_given_alone():
    canvas = frame()
    before = canvas.tobytes()
    run(Dust(SIZE, {"count": 3000}), canvas, seconds=6)
    assert canvas.tobytes() == before


def test_a_frame_of_another_size_is_passed_through():
    dust = Dust(SIZE, {})
    other = frame((200, 100))
    assert dust.apply(other, 1.0) is other


# -- with the renderer ------------------------------------------------------------------


@pytest.mark.parametrize("size", [(1920, 480), (320, 240)])
def test_a_themes_dust_renders_at_both_sizes(size):
    widgets = [
        {"type": "text", "id": "t", "x": size[0] // 20, "y": size[1] // 10, "text": "SPUR II",
         "font_size": size[1] // 5, "color": "#e8f6ff"},
        {"type": "bar", "id": "b", "x": size[0] // 20, "y": size[1] // 2, "w": size[0] // 2, "h": 8,
         "sensor": "cpu.load", "color": "#22d3ee"},
    ]  # fmt: skip
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    plain = Renderer(theme(size=size, widgets=widgets), animate=True)
    dusty = Renderer(
        theme(look={"dust": {"count": 4000}}, size=size, widgets=widgets), animate=True
    )
    plain.continuous = dusty.continuous = True
    for n in range(8 * 50):
        base, _ = plain.render(snapshot, 1.0 + n / 50)
        out, _ = dusty.render(snapshot, 1.0 + n / 50)
        dusty.shown = out
    assert out.size == size and out.mode == "RGB"
    assert dusty.warnings == []
    diff = np.abs(pixels(out) - pixels(base))
    assert 0 < (diff.sum(axis=-1) > 0).sum() < 0.2 * diff.shape[0] * diff.shape[1]


def test_the_dust_goes_under_the_toast_the_loop_adds():
    """The dust is part of the frame the renderer returns: the toast comes after it."""
    renderer = Renderer(theme(look={"dust": True}), animate=True)
    renderer.continuous = True
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    toast = object()
    for n in range(120):
        renderer.toast = (toast, n / 50) if n >= 10 else None
        out, _ = renderer.render(snapshot, 1.0 + n / 50)
        renderer.shown = out
    assert renderer._dust.renewals >= 3  # the first, the toast's two, and one after four seconds


FLAT_SCREEN = """
from PIL import Image

from libre_panel.plugins import Screen


class Flat(Screen):
    name = "flat"
    api = 1

    def __init__(self, context, options):
        super().__init__(context, options)
        self.frame = Image.new("RGB", context.size, (3, 4, 5))

    def render(self, snapshot, now):
        return self.frame
"""


def test_an_opaque_screen_alone_is_the_frame_unless_there_is_dust(plugin_folder):
    plugin_folder(FLAT_SCREEN, {"libre_panel.screens": {"flat": "Flat"}})
    data = theme().to_dict()
    data["widgets"] = []
    data["screen"] = {"name": "flat"}
    plain = Renderer(parse_theme(data))
    assert plain.render(demo_snapshot(), 1.0)[0] is plain.screen.frame  # the fast path
    data["look"] = {"dust": True}
    dusty = Renderer(parse_theme(data))
    dusty.continuous = True
    out, _ = dusty.render(demo_snapshot(), 1.0)
    assert out is not dusty.screen.frame and out.size == (480, 160)

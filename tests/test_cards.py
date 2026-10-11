"""The cards after SPUR II's Studio screen: the weather card, the playback card, the ring
instrument, the download card and the slot that shows one of several cards."""

import pytest
from PIL import Image, ImageChops

from libre_panel import icons
from libre_panel.render import finish
from libre_panel.render.renderer import Renderer, _reads, need_holds, need_key
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import parse_theme


def reading_snapshot(**values):
    """A snapshot of just these readings (names with ``__`` for ``.``)."""
    keys = {k.replace("__", "."): v for k, v in values.items()}
    return Snapshot(readings={k: Reading(k, v) for k, v in keys.items() if v is not None})


def theme_of(*widgets, size=(400, 120), **extra):
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#0a1119"},
            "widgets": list(widgets),
            **extra,
        }
    )


def differs(a: Image.Image, b: Image.Image) -> bool:
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


# -- needs that compare ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("need", "expected"),
    [
        ("media.playing", True), ("!media.playing", False), ("gone", False), ("!gone", True),
        ("media.playing=1", True), ("media.playing==1", True), ("media.playing = 1", True),
        ("media.playing=0", False), ("media.playing!=0", True), ("media.playing!=1", False),
        ("media.progress>0.4", True), ("media.progress>0.5", False), ("media.progress>=0.5", True),
        ("media.progress<0.6", True), ("media.progress<=0.4", False), ("cpu.temp>-5", True),
        ("gone=1", False), ("gone!=1", False), ("gone>0", False),  # no reading: nothing holds
        ("media.title=1", False), ("media.title!=1", False),  # not a number: nothing holds
        ("!media.playing=1", False), ("!media.playing=0", True), ("!gone=1", True),
    ],
)  # fmt: skip
def test_a_need_compares_a_reading_with_a_number(need, expected):
    snap = reading_snapshot(
        media__playing=1, media__progress=0.5, media__title="A song", cpu__temp=40.0
    )
    assert need_holds(need, snap) is expected


def test_a_state_that_is_taken_back_has_no_value():
    running = reading_snapshot(downloads='{"phase":"downloading"}')
    taken_back = reading_snapshot(downloads="null")
    assert need_holds("downloads", running) and not need_holds("!downloads", running)
    assert not need_holds("downloads", taken_back) and need_holds("!downloads", taken_back)
    assert not need_holds("downloads", reading_snapshot()) and need_holds(
        "!downloads", reading_snapshot()
    )


def test_a_need_reads_the_sensor_of_its_condition():
    assert need_key("media.playing=1") == "media.playing"
    assert need_key("!cpu.temp >= 80") == "cpu.temp"
    assert need_key(" gpu.load ") == "gpu.load"
    assert need_key("!gpu.load") == "gpu.load"


def test_on_and_off_readings_compare_as_one_and_zero():
    assert need_holds("flag=1", reading_snapshot(flag=True))
    assert need_holds("flag=0", reading_snapshot(flag=False))
    assert not need_holds("flag=0", reading_snapshot(flag=True))


def test_a_part_with_a_comparison_follows_the_reading():
    widget = {"type": "text", "id": "t", "x": 4, "y": 4, "text": "PLAYS", "font_size": 40,
              "needs": "media.playing=1"}  # fmt: skip
    renderer = Renderer(theme_of(widget), preview=True)
    paused = renderer.render(reading_snapshot(media__playing=0))[0]
    plays = renderer.render(reading_snapshot(media__playing=1))[0]
    nothing = renderer.render(reading_snapshot())[0]
    assert differs(paused, plays)
    assert not differs(paused, nothing)  # no reading is not "1" either


def test_a_comparison_asks_the_sensors_it_reads():
    # the readings a theme reads decide which sensors are polled: a comparison must count
    widget = {"type": "text", "id": "t", "text": "x", "needs": "media.playing=1,!cpu.temp>80"}
    assert {"media.playing", "cpu.temp"} <= _reads([widget])


# -- a module's finish: auto -----------------------------------------------------------------


def module_theme(kind, finish_name=None, **more):
    module = {"type": "module", "id": "m", "module": kind, "cols": 3, "rows": 2, **more}
    if finish_name:
        module["finish"] = finish_name
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
            "widgets": [module],
        }
    )


def finishes(kind, finish_name=None):
    renderer = Renderer(module_theme(kind, finish_name), preview=True)
    return {w.get("finish", "flat") for w in renderer.widgets if w["type"] != "rect"}


def test_a_cards_finish_is_lit_unless_the_theme_says_otherwise():
    assert "lit" in finishes("forecast") and finishes("forecast") <= {"lit", "flat"}
    assert finishes("forecast", "flat") == {"flat"}
    assert finishes("instrument") >= {"lit"}
    assert finishes("instrument", "facet") >= {"facet"}


def test_the_older_modules_stay_flat_by_themselves():
    assert finishes("ring") == {"flat"}
    assert finishes("weather") == {"flat"}
    assert finishes("ring", "lit") >= {"lit"}


def test_the_default_finish_of_a_module_is_auto():
    assert module_theme("forecast").widgets[0]["finish"] == "auto"
    assert finish.supports({"type": "module", "finish": "auto"})


# -- the new icons ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["play", "pause", "up", "down"])
def test_the_new_icons_draw(name):
    assert name in icons.ICON_NAMES
    image = icons.draw_icon(name, 48, (255, 255, 255), 2.0)
    assert image.size[0] >= 40 and image.getchannel("A").getbbox() is not None


def test_play_and_pause_and_the_arrows_differ():
    shapes = {n: icons.draw_icon(n, 48, (255, 255, 255), 2.0).tobytes()
              for n in ("play", "pause", "up", "down")}  # fmt: skip
    assert len(set(shapes.values())) == 4


def test_demo_snapshot_still_has_the_readings_the_cards_use():
    snap = demo_snapshot(fixed_time=1_700_000_000)
    assert snap.value("cpu.load") is not None

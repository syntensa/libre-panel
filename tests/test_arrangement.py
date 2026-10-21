"""Where the panels stand, and one picture across all of them (arrangement.py)."""

import io
import time

import pytest
from PIL import Image

from libre_panel import app, arrangement
from libre_panel.devices.models import find_model


def halves(size=(1000, 400)):
    """A picture red on the left half, blue on the right."""
    picture = Image.new("RGB", size, (0, 0, 255))
    picture.paste((255, 0, 0), (0, 0, size[0] // 2, size[1]))
    return picture


def test_a_panel_s_size_comes_from_its_model_s_diagonal_and_pixels():
    wide = arrangement.millimetres(find_model("turing-12.3-usb"))
    assert wide == pytest.approx((292.5, 109.7), abs=0.1)
    bar = arrangement.millimetres(find_model("turing-9.2-usb"), (480, 1920))  # on end
    assert bar == pytest.approx((56.7, 226.7), abs=0.1)
    assert arrangement.millimetres(None) is None
    assert arrangement.rect(None, find_model("turing-9.2-usb"), None) is None  # not placed
    assert arrangement.rect((10, -5), None, None) is None  # its size not known


def test_each_panel_shows_the_part_of_the_picture_where_it_stands():
    picture = halves()
    left, right = (0.0, 0.0, 200.0, 100.0), (300.0, 0.0, 100.0, 100.0)
    # the panels together are 400 × 100 mm: the picture covers them, its middle at theirs
    one = arrangement.part(picture, [left, right], 0)
    two = arrangement.part(picture, [left, right], 1)
    assert one.size == (500, 250) and two.size == (250, 250)
    assert one.getpixel((10, 10)) == (255, 0, 0) and two.getpixel((10, 10)) == (0, 0, 255)
    assert arrangement.part(picture, [left, None], 1) is None  # not placed: no part
    alone = arrangement.part(picture, [None, right], 1)  # the only one placed: all of its height
    assert alone.size == (400, 400)


def test_a_picture_given_is_kept_smaller_and_without_its_metadata(tmp_path):
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"  # Make
    exif[0x0110] = "Camera model"  # Model
    buffer = io.BytesIO()
    Image.new("RGB", (5000, 1000), (20, 40, 60)).save(buffer, "JPEG", exif=exif.tobytes())
    name = arrangement.save_picture(buffer.getvalue(), tmp_path)
    assert name == arrangement.ACROSS_FILE
    with Image.open(tmp_path / name) as kept:
        assert kept.size == (4096, 819) and not kept.getexif()
    with pytest.raises(arrangement.PictureError, match="not a picture"):
        arrangement.save_picture(b"not a picture", tmp_path)
    loaded = arrangement.load(tmp_path / name)
    assert loaded is not None and loaded[0].size == (4096, 819)
    assert arrangement.load(tmp_path / "gone.jpg") is None


def test_a_panel_under_a_picture_across_the_panels_shows_its_part_and_follows_a_move(tmp_path):
    from test_app_cli import MemoryStream, StepClock

    from libre_panel.config import Config, SensorsConfig
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    halves((2000, 500)).save(tmp_path / "wide.png")
    places = [(0.0, 0.0, 292.5, 109.7), (320.0, 0.0, 226.7, 56.7)]
    clock = StepClock(1000.0)
    config = Config(theme="slate", across=str(tmp_path / "wide.png"),
                    sensors=SensorsConfig(providers=["demo"]))  # fmt: skip
    loop = app.MainLoop(config, display=MemoryStream(), clock=clock, panel=0,
                        places_of=lambda: list(places),
                        hub=SensorHub([DemoProvider({"fixed_time": 1_700_000_000})]))  # fmt: skip
    try:
        loop.step()
        backdrop = loop.renderer.backdrop
        assert backdrop is not None and backdrop.getpixel((backdrop.width - 5, 5)) == (0, 0, 255)
        places[1] = (0.0, 120.0, 226.7, 56.7)  # moved under the first: the left half
        give_up = time.monotonic() + 20
        while time.monotonic() < give_up and loop.renderer.backdrop.getpixel((5, 5))[0] != 255:
            clock.now += 3.0
            loop.step()
            time.sleep(0.05)
        assert loop.renderer.backdrop.getpixel((5, 5)) == (255, 0, 0)
    finally:
        loop.close()
    plain = app.MainLoop(Config(theme="slate", sensors=SensorsConfig(providers=["demo"])),
                         display=MemoryStream(), clock=StepClock(0.0), panel=0,
                         places_of=lambda: list(places),
                         hub=SensorHub([DemoProvider({})]))  # fmt: skip
    try:
        assert plain.renderer.backdrop is None  # no picture across them
    finally:
        plain.close()


def test_the_places_of_the_panels_come_from_config_and_what_each_panel_said():
    from libre_panel.config import parse_config

    more = app._MorePanels(None, app.RunStatus(model="turing-12.3-usb", size=(1920, 720)), None)
    assert more.places() == []  # nothing followed yet
    bar = {"at": [320, 0], "model": "turing-9.2-usb"}
    config = parse_config({"device": {"at": [0, 0]}, "panels": [bar, {}]})
    more._seen = config
    first, second, third = more.places()
    assert first == pytest.approx((0, 0, 292.5, 109.7), abs=0.1)  # the size the panel said
    assert second == pytest.approx((320, 0, 226.7, 56.7), abs=0.1)  # its model's, as set
    assert third is None  # not placed


def test_a_scene_s_change_goes_across_the_panels_from_the_left(monkeypatch):
    from dataclasses import replace

    from libre_panel.config import Config

    places = [(0.0, 0.0, 292.5, 109.7), (320.0, 0.0, 226.7, 56.7), None]
    loop = object.__new__(app.MainLoop)
    loop.places_of = lambda: places
    written = 1_000.0
    monkeypatch.setattr(app.time, "time", lambda: written)  # noticed at once
    before = Config(scene="Work")
    after = replace(before, scene="Gaming", stamp=int(written * 1e9))
    loop.panel = None  # the first panel, on the left: after the lead only
    assert loop._sweep(before, after) == pytest.approx(app.SWEEP_LEAD_S)
    loop.panel = 0  # the one on the right: the whole sweep later
    assert loop._sweep(before, after) == pytest.approx(app.SWEEP_LEAD_S + app.SWEEP_S)
    loop.panel = 1  # not placed: at once
    assert loop._sweep(before, after) == 0.0
    loop.panel = 0
    assert loop._sweep(after, after) == 0.0  # no scene changed: at once
    monkeypatch.setattr(app.time, "time", lambda: written + 5)  # noticed late: at once
    assert loop._sweep(before, after) == 0.0


def test_a_transition_that_begins_later_shows_the_old_frame_until_then():
    from libre_panel.render.overlays import transition

    old, new = Image.new("RGB", (40, 20), "red"), Image.new("RGB", (40, 20), "blue")
    kind = transition("fade", None)
    shown, blend = app._blend((kind, old, 10.0), new, 9.5)
    assert blend is not None and shown.getpixel((5, 5)) == (255, 0, 0)


def test_the_panels_not_placed_stand_in_a_row_beside_those_that_are():
    from libre_panel.config import parse_config

    loose = parse_config({"device": {"model": "turing-12.3-usb"},
                          "panels": [{"model": "turing-9.2-usb"}, {}]})  # fmt: skip
    first, second, third = arrangement.laid_out(loose, [None, None, None])
    assert first[:2] == (-275, 0.0) and second[0] == pytest.approx(-275 + 292.5 + 30, abs=0.1)
    assert third is None  # neither a model nor a size it said
    told = app.RunStatus(size=(480, 320))  # a panel of a model not known says its size
    assert arrangement.laid_out(loose, [None, None, told])[2][2] == pytest.approx(480 * 0.118)
    placed = parse_config({"device": {"model": "turing-12.3-usb", "at": [0, 0]},
                           "panels": [{"model": "turing-9.2-usb"}]})  # fmt: skip
    first, second = arrangement.laid_out(placed, [None, None])
    assert first[:2] == (0.0, 0.0) and second[:2] == pytest.approx((322.5, 0.0), abs=0.1)


def test_a_picture_of_all_panels_shows_each_where_it_stands():
    wide, bar = (
        Image.new("RGB", (1920, 720), (200, 0, 0)),
        Image.new("RGB", (1920, 480), (0, 0, 200)),
    )
    panels = [
        ((0.0, 0.0, 292.5, 109.7), wide, False, 1),
        ((33.0, 125.0, 226.7, 56.7), bar, False, 2),
    ]
    picture = arrangement.photo(panels)
    per = 1920 / 226.7  # as dense as the densest: the 9.2" bar at its own size
    assert picture.width == pytest.approx((292.5 + 2 * (292.5 * 0.08 + 12)) * per, abs=2)
    margin = (292.5 * 0.08 + 12) * per
    assert picture.getpixel((round(margin + 50), round(margin + 50))) == (200, 0, 0)
    assert picture.getpixel((round(margin + 40 * per), round(margin + 150 * per))) == (0, 0, 200)
    assert max(picture.size) <= arrangement.PHOTO_MAX_SIDE
    dark = arrangement.photo([((0.0, 0.0, 50.0, 50.0), None, True, 2)])  # nothing shown yet
    assert dark.size[0] == dark.size[1] and arrangement.photo([]) is None

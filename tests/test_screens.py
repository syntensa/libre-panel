"""Libre Panel's screens for one thing each, on the 8.8" and 9.2" bars: Encore (what plays, the
volume and the time), Nimbus (the weather, the moon and a small clock) and Almanac (the time and
the month, in digits or on a face), and what they are made of: the volume, moon and outlook
modules, levels that rise, a cover blurred behind everything, Libre Panel's own words in the
panel's language, and a second hand that glides to the second."""

import gc
import json
from datetime import datetime, timedelta

import numpy
import pytest
from PIL import Image
from test_libre_themes import PANELS, data_of, drawn, overlapping
from test_slot import demo

from libre_panel import i18n
from libre_panel.icons import ICON_NAMES, draw_icon
from libre_panel.render.renderer import Renderer, _blurred
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import builtin_themes_dir, find_theme, load_theme, parse_theme

SCREENS = ["encore", "nimbus", "almanac", "almanac-analog"]


@pytest.fixture(scope="module", autouse=True)
def renderers_let_go():
    yield
    gc.collect()  # a renderer tells the sensors what it reads until it is collected


@pytest.fixture
def german():
    i18n.set_language("de")
    yield
    i18n.set_language("en")


def texts(renderer):
    return {text for _wid, text, _colour in renderer._texts}


def drawn_ids(renderer):
    return {wid for wid, _piece in renderer._last[0]}


def tiny(width=400, height=200, widgets=()):
    return parse_theme({"format": "libre-panel-theme/1", "display": {"width": width,
                        "height": height}, "background": {"color": "#000000"},
                        "widgets": list(widgets)})  # fmt: skip


# -- the screens -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCREENS)
def test_a_screen_is_libre_panels_own_for_the_bars_and_draws_whole(name):
    data = data_of(name)
    theme, renderer, frame, boxes = drawn(data, builtin_themes_dir() / name)
    assert theme.warnings == [] and renderer.warnings == []
    assert (theme.width, theme.height) == (1920, 480) and theme.collection == ""
    assert theme.palette["accent"] == "#2EE6D6" and theme.background_image == "assets/backdrop.png"
    assert frame.size == (1920, 480) and boxes
    nothing = Renderer(load_theme(find_theme(name)))
    assert nothing.render(Snapshot(), 0.0)[0].size == (1920, 480) and nothing.warnings == []


@pytest.mark.parametrize("name", SCREENS)
@pytest.mark.parametrize("model, orientation", PANELS)
def test_a_screen_moves_to_another_panel_without_parts_running_together(name, model, orientation):
    data = data_of(name)
    root = builtin_themes_dir() / name
    if (data["display"]["model"], data["display"]["orientation"]) == (model, orientation):
        return
    theme, _, _, own = drawn(data, root)
    adapted, renderer, frame, boxes = drawn(adapt_theme(data, model, orientation), root)
    assert adapted.warnings == [] and renderer.warnings == []
    assert frame.size == (adapted.width, adapted.height)
    touch = 4 if adapted.width < 0.6 * theme.width else 0
    new = overlapping(adapted, boxes, touch) - overlapping(theme, own)
    assert not new, sorted(tuple(sorted(pair)) for pair in new)


@pytest.mark.parametrize("name", ["encore", "nimbus", "almanac", "almanac-analog"])
def test_the_clocks_of_the_screens_move_as_spur_iis(name):
    by = {w["id"]: w for w in load_theme(find_theme(name)).widgets}
    timed = [w for w in by.values() if w["type"] == "clock" and any(
        f"%{c}" in w["format"] for c in "HMS")]  # fmt: skip
    assert timed and all(w["digits"] == "roll" for w in timed)
    faces = [w for w in by.values() if w["type"] == "analog"]
    assert all(face["hand"] == "glide" for face in faces)
    assert bool(faces) == (name == "almanac-analog")


# -- Encore ------------------------------------------------------------------------------------


def test_encore_lays_the_covers_colours_behind_everything_while_a_track_plays():
    theme = load_theme(find_theme("encore"))
    by = {w["id"]: w for w in theme.widgets}
    ambient = by["ambient"]
    assert (ambient["src"], ambient["fit"], ambient["w"], ambient["h"]) == (
        "@media.cover", "cover", 1920, 480)  # fmt: skip
    assert ambient["blur"] > 30 and ambient["needs"] == "media.title"
    assert theme.widgets.index(ambient) == 0  # under everything
    playing, idle = Renderer(theme, preview=True), Renderer(theme, preview=True)
    lit = numpy.asarray(playing.render(demo(), 0.0)[0].convert("RGB"), float)
    dark = numpy.asarray(idle.render(demo(media=False), 0.0)[0].convert("RGB"), float)
    corner = (slice(452, 480), slice(470, 900))  # under the words, left of the clock
    assert lit[corner].mean() > dark[corner].mean() + 8
    assert "ambient" not in drawn_ids(idle) and "veil" not in drawn_ids(idle)


def test_encore_says_what_plays_and_that_nothing_does_in_the_panels_language(german):
    theme = load_theme(find_theme("encore"))
    playing = Renderer(theme, preview=True)
    playing.render(demo(), 0.0)
    assert {"LÄUFT", "Midnight Drive"} <= texts(playing) and "Es läuft nichts" not in texts(playing)
    paused = Renderer(theme, preview=True)
    snapshot = demo(playing=0)
    snapshot.readings["media.state"] = Reading("media.state", "Paused")
    paused.render(snapshot, 0.0)
    assert "PAUSIERT" in texts(paused) and "paused-sign" in drawn_ids(paused)
    idle = Renderer(theme, preview=True)
    idle.render(demo(media=False), 0.0)
    assert "Es läuft nichts" in texts(idle) and "cover-frame" in drawn_ids(idle)


def test_encore_shows_the_volume_as_levels_and_a_muted_speaker_crossed():
    theme = load_theme(find_theme("encore"))
    renderer = Renderer(theme, preview=True)
    renderer.render(demo(), 0.0)
    shown = drawn_ids(renderer)
    assert {"volume/levels", "volume/icon", "volume/value"} <= shown
    assert "volume/muted-icon" not in shown and "30" in texts(renderer)
    levels = next(w for w in renderer.widgets if w["id"] == "volume/levels")
    assert levels["sensor"] == "audio.volume" and levels["rising"] > 0 and levels["segments"] >= 8
    muted = demo()
    muted.readings["audio.muted"] = Reading("audio.muted", 1)
    muted.readings["audio.volume"] = Reading("audio.volume", 0.0, "%")
    renderer = Renderer(theme, preview=True)
    renderer.render(muted, 0.0)
    shown = drawn_ids(renderer)
    assert {"volume/muted-icon", "volume/muted"} <= shown and "volume/value" not in shown
    assert "MUTED" in texts(renderer)


# -- Nimbus ------------------------------------------------------------------------------------


def test_nimbus_shows_the_weather_the_days_ahead_the_moon_and_a_small_clock():
    theme = load_theme(find_theme("nimbus"))
    kinds = {w["id"]: w.get("module") for w in theme.widgets if w["type"] == "module"}
    assert kinds == {"weather": "forecast", "days": "outlook", "moon": "moon"}
    renderer = Renderer(theme, preview=True)
    renderer.render(demo(), 0.0)
    shown = drawn_ids(renderer)
    assert {f"days/d{i}-symbol" for i in range(7)} <= shown  # a week, a row a day
    assert "days/d6-rule" in shown and "days/idle-0" not in shown
    assert {"moon/moon", "moon/name", "moon/full", "moon/new"} <= shown
    clock = next(w for w in theme.widgets if w["id"] == "clock")
    assert clock["font_size"] < 100 and clock["digits"] == "roll"  # small, and it rolls


def test_nimbus_says_where_the_weather_is_set_while_there_is_none(german):
    renderer = Renderer(load_theme(find_theme("nimbus")), preview=True)
    snapshot = demo(weather=False)
    for key in [k for k in snapshot.readings if k.startswith("sun.")]:
        del snapshot.readings[key]
    renderer.render(snapshot, 0.0)
    shown = drawn_ids(renderer)
    assert {"days/idle-0", "days/idle-1", "days/idle-icon", "moon/moon"} <= shown
    assert not any(wid.startswith("days/d") for wid in shown)
    assert "Einstellungen, Wetter und Ort" in texts(renderer)


# -- Almanac -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["almanac", "almanac-analog"])
def test_almanac_shows_the_month_with_today_and_the_week_in_the_panels_language(name, german):
    theme = load_theme(find_theme(name))
    by = {w["id"]: w for w in theme.widgets}
    assert by["days"]["type"] == "calendar" and by["days"]["color2"] == "@accent"
    assert by["week"]["format"] == "%V" and by["week-label"]["translate"]
    renderer = Renderer(theme, preview=True)
    renderer.render(Snapshot(now=datetime(2026, 10, 12, 17, 24, 30)), 0.0)
    assert {"KW", "42", "OKTOBER 2026"} <= texts(renderer)


# -- the modules -------------------------------------------------------------------------------


def module_parts(kind, cols, rows, snapshot, **options):
    widget = {"type": "module", "id": "m", "module": kind, "col": 0, "row": 0, "cols": cols,
              "rows": rows, **options}  # fmt: skip
    theme = parse_theme({**{k: v for k, v in data_of("nimbus").items() if k != "widgets"},
                         "background": {"color": "@bg"}, "widgets": [widget]})  # fmt: skip
    renderer = Renderer(theme, preview=True)
    renderer.render(snapshot, 0.0)
    assert renderer.warnings == []
    return renderer, {w["id"].split("/", 1)[1]: w for w in renderer.widgets}


def test_the_volume_stands_upright_in_a_tall_module():
    renderer, parts = module_parts("volume", 4, 40, demo())
    levels = parts["levels"]
    assert levels["direction"] == "up" and levels["rising"] == 0 and levels["h"] > levels["w"]
    wide, parts = module_parts("volume", 20, 16, demo())
    assert parts["levels"]["direction"] == "right" and parts["levels"]["w"] > parts["levels"]["h"]


def test_the_days_ahead_stand_side_by_side_in_a_long_module():
    renderer, parts = module_parts("outlook", 30, 14, demo())
    names = [parts[f"d{i}-name"] for i in range(7) if f"d{i}-name" in parts]
    assert len(names) >= 5 and len({n["y"] for n in names}) == 1  # one row of columns
    symbols = [parts[f"d{i}-symbol"]["sensor"] for i in range(len(names))]
    assert symbols == [f"weather.day.{i}.code" for i in range(len(names))]
    assert all("weather.day.0.code" in parts[f"d0-{p}"]["needs"] for p in ("high", "low"))


def test_the_moon_needs_no_place():
    snapshot = demo(weather=False)
    for key in [k for k in snapshot.readings if k.startswith("sun.")]:
        del snapshot.readings[key]
    renderer, parts = module_parts("moon", 12, 30, snapshot)
    assert parts["moon"]["sensor"] == "moon.phase"
    assert {"m/moon", "m/name", "m/lit", "m/full", "m/new"} <= drawn_ids(renderer)


# -- what they are made of ---------------------------------------------------------------------


def test_levels_rise_from_a_share_of_the_bar_to_all_of_it():
    bar = {"type": "bar", "id": "b", "x": 0, "y": 0, "w": 200, "h": 50, "sensor": "s", "min": 0,
           "max": 100, "color": "#ffffff", "background": None, "radius": 0, "segments": 10,
           "segment_gap": 4, "rising": 0.2, "smooth": False}  # fmt: skip
    renderer = Renderer(tiny(widgets=[bar]), preview=True)
    frame = renderer.render(Snapshot(readings={"s": Reading("s", 100.0)}), 0.0)[0]
    alpha = numpy.asarray(frame.convert("L"), float)[:50, :200] > 128
    heights = [alpha[:, x].sum() for x in range(8, 200, 20)]  # the middle of each segment
    assert heights[0] == pytest.approx(10, abs=1) and heights[-1] == pytest.approx(50, abs=1)
    assert all(b > a for a, b in zip(heights, heights[1:], strict=False))
    assert alpha[-1, 8::20].all()  # they stand on the bar's lower edge
    with pytest.raises(Exception, match="rising"):
        tiny(widgets=[{**bar, "rising": 1.5}])


def test_a_blurred_picture_is_soft_and_fills_its_box_on_any_panel():
    sharp = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
    sharp.paste((255, 255, 255, 255), (0, 0, 32, 64))
    soft = numpy.asarray(_blurred(sharp, 24, 400, 100, "cover").convert("L"), float)
    assert soft.shape == (100, 400)
    assert numpy.abs(numpy.diff(soft, axis=1)).max() < 12  # no edge left, only a passage
    image = {"type": "image", "id": "i", "x": 0, "y": 0, "w": 1920, "h": 480,
             "src": "@media.cover", "fit": "cover"}  # fmt: skip
    data = {"format": "libre-panel-theme/1", "display": {"model": "turing-9.2-usb",
            "orientation": "landscape", "width": 1920, "height": 480},
            "widgets": [{**image, "blur": 40}, {**image, "id": "j"}]}  # fmt: skip
    blurred, plain = adapt_theme(data, "turing-12.3-usb", "landscape")["widgets"]
    assert (blurred["w"], blurred["h"]) == (1920, 720)  # a backdrop: the whole panel
    assert plain["w"] / plain["h"] == pytest.approx(4)  # a picture keeps its shape
    assert blurred["blur"] == 40


def test_a_text_may_be_in_the_panels_language_and_in_capitals(german):
    widgets = [
        {"type": "text", "id": "a", "text": "Nothing playing", "translate": True},
        {"type": "text", "id": "b", "text": "Nothing playing", "y": 50},
        {"type": "text", "id": "c", "text": "Week", "translate": True, "uppercase": True,
         "y": 100},
        {"type": "text", "id": "d", "text": "my own words", "translate": True, "y": 150},
    ]  # fmt: skip
    renderer = Renderer(tiny(widgets=widgets), preview=True)
    renderer.render(Snapshot(), 0.0)
    assert {"Es läuft nichts", "Nothing playing", "KW", "my own words"} <= texts(renderer)


def test_the_speaker_icons():
    assert {"volume", "mute"} <= set(ICON_NAMES)
    for name in ("volume", "mute"):
        icon = draw_icon(name, 48, (255, 255, 255, 255))
        assert icon.getchannel("A").getbbox() is not None


# -- the second hand ---------------------------------------------------------------------------


def face(hand, size=240):
    widget = {"type": "analog", "id": "a", "x": 0, "y": 0, "w": size, "h": size, "hand": hand,
              "face": "#101010", "color": "#ffffff", "color2": "#00ffff"}  # fmt: skip
    renderer = Renderer(tiny(size, size, [widget]), animate=True)
    renderer.fps = 50
    return renderer


START = datetime(2026, 10, 12, 14, 30, 7)


def tip(frame, size=240):
    """Where the second hand's cyan tip is, as an angle on the face (0 at twelve)."""
    rgb = numpy.asarray(frame.convert("RGB"), float)
    cyan = (rgb[..., 1] > 150) & (rgb[..., 2] > 150) & (rgb[..., 0] < 100)
    ys, xs = numpy.nonzero(cyan)
    far = numpy.hypot(xs - size / 2, ys - size / 2) > size * 0.3  # the hand, not the dot
    angle = numpy.degrees(numpy.arctan2(xs[far] - size / 2, size / 2 - ys[far]).mean())
    return angle % 360


def test_a_gliding_second_hand_moves_to_the_second_and_then_rests():
    renderer = face("glide")
    angles, moving = [], []
    for ms in (0, 40, 100, 200, 300, 500, 800):
        frame = renderer.render(Snapshot(now=START + timedelta(milliseconds=ms)), ms / 1000)[0]
        angles.append(tip(frame))
        moving.append(renderer.moving)
    assert angles[0] == pytest.approx(36, abs=1.5)  # still at the 6th second
    assert angles[1] < angles[2] < angles[3] < angles[4] <= 42.5  # on its way to the 7th
    assert angles[5] == pytest.approx(42, abs=1) and angles[6] == angles[5]
    assert moving[:5] == [True] * 5 and moving[5:] == [False, False]


def test_a_sweeping_hand_goes_round_and_a_ticking_one_jumps():
    sweep = face("sweep")
    before = tip(sweep.render(Snapshot(now=START + timedelta(milliseconds=500)), 0.5)[0])
    assert sweep.moving and before == pytest.approx(45, abs=1)  # half way to the 8th second
    tick = face("tick")
    for ms in (0, 100, 900):
        frame = tick.render(Snapshot(now=START + timedelta(milliseconds=ms)), ms / 1000)[0]
        assert tip(frame) == pytest.approx(42, abs=1) and not tick.moving


def test_a_gliding_hand_costs_the_panel_little():
    import time

    renderer = face("glide", 432)
    renderer.render(Snapshot(now=START), 0.0)
    took = []
    for step in range(1, 21):
        began = time.perf_counter()
        renderer.render(Snapshot(now=START + timedelta(seconds=0.02 * step)), 0.02 * step)
        took.append(time.perf_counter() - began)
    assert sorted(took)[len(took) // 2] < 0.012  # the face is kept, the hand alone is drawn


def test_a_screens_parts_stay_in_json_as_written():
    for name in SCREENS:
        text = (builtin_themes_dir() / name / "theme.json").read_text("utf-8")
        data = json.loads(text)
        assert text == json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        for brand in ("Ryzen", "Radeon", "GeForce", "Intel", "Crucial", "Kingston"):
            assert brand not in text

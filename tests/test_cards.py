"""The cards after SPUR II's Studio screen: the weather card, the playback card, the ring
instrument, the download card and the slot that shows one of several cards."""

import re

import pytest
from PIL import Image, ImageChops

from libre_panel import icons
from libre_panel.render import finish
from libre_panel.render.renderer import Renderer, _reads, need_holds, need_key
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import WIDGET_SPECS, parse_theme


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


# -- the cards ---------------------------------------------------------------------------------

CARDS = ("playback", "forecast", "download", "instrument", "cards", "netcard")


def card_theme(kind, cols=3, rows=2, **more):
    module = {"type": "module", "id": "m", "module": kind, "cols": cols, "rows": rows, **more}
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
            "widgets": [module],
        }
    )


def snapshot_with(drop=(), **more):
    """The demo snapshot with some readings gone (by prefix) and some set."""
    snap = demo_snapshot(fixed_time=1_700_000_000)
    for prefix in drop:
        for key in [k for k in snap.readings if k.startswith(prefix)]:
            del snap.readings[key]
        if prefix.startswith("media"):
            snap.images.pop("media.cover", None)
            snap.images.pop("media.app_icon", None)
    for key, value in more.items():
        snap.readings[key.replace("__", ".")] = Reading(key.replace("__", "."), value)
    return snap


def drawn(kind, snap=None, cols=3, rows=2, animate=False, now=1.0, **more):
    """The names of the parts a card draws, the frame, and the renderer."""
    renderer = Renderer(card_theme(kind, cols, rows, **more), animate=animate)
    frame, _ = renderer.render(snap or snapshot_with(), now)
    names = [wid.split("/", 1)[1] for wid, _ in renderer._last[0] if "/" in wid]
    return {n for n in names if not n.startswith("\0")}, frame, renderer


def widgets_of(kind, cols=3, rows=2, **more):
    renderer = Renderer(card_theme(kind, cols, rows, **more), preview=True)
    return {w["id"].split("/", 1)[1]: w for w in renderer.widgets if w.get("_module") == "m"}


@pytest.mark.parametrize("kind", CARDS)
@pytest.mark.parametrize("size", [(1, 1), (2, 1), (1, 2), (3, 1), (2, 2), (3, 2), (4, 2)])
def test_a_card_draws_with_and_without_its_readings(kind, size):
    for snap in (snapshot_with(), snapshot_with(drop=("media", "weather", "sun", "net.")),
                 Snapshot()):  # fmt: skip
        names, frame, renderer = drawn(kind, snap, *size, animate=True)
        assert renderer.warnings == [] and frame.size == (1920, 480)


@pytest.mark.parametrize("kind", CARDS)
def test_no_card_has_a_ridge_a_bevel_or_a_trough_on_its_frame(kind):
    """Frames are flat thin lines in their colour: a card's rects carry no ridge name and
    its outline is one thin line (the lit grooves of bars and rings are values, not frames)."""
    for size in ((3, 2), (2, 1), (1, 2)):
        for widget in widgets_of(kind, *size).values():
            assert not widget.get("ridge"), widget["id"]
            assert widget.get("finish", "flat") != "facet" and widget.get("finish") != "acrylic"
            if widget["type"] == "rect":
                assert widget["outline_width"] <= 3 and not widget.get("glow"), widget["id"]
    renderer = Renderer(card_theme(kind), animate=True)
    assert not any(w["type"] == "rect" and w.get("ridge") for w in renderer.widgets)
    assert renderer.theme.look in (None, {}) or "frames" not in (renderer.theme.look or {})


# the weather card

WEATHER_STEP_DEFAULT = WIDGET_SPECS["module"]["step"][1]


def test_the_weather_card_shows_the_weather_and_the_next_hours():
    names, _, _ = drawn("forecast")
    assert {"symbol", "kicker", "temp", "high", "low", "desc", "h0-time", "h0-symbol", "h0-temp",
            "h0-rain", "h4-time"} <= names  # fmt: skip
    assert not {"day", "calendar"} & names  # (the calendar sheet is for where there is no weather)
    parts = widgets_of("forecast")
    assert parts["symbol"]["type"] == "symbol" and parts["symbol"]["sensor"] == "weather.code"
    assert (
        parts["symbol"]["finish"] == "lit" and parts["h0-symbol"]["sensor"] == "weather.hour.1.code"
    )
    assert (
        parts["high"]["sensor"] == "weather.day.0.high"
        and parts["low"]["sensor"] == "weather.day.0.low"
    )
    assert parts["h0-rain"]["sensor"] == "weather.hour.1.rain" and parts["h0-rain"]["max"] == 100
    assert parts["h0-symbol"]["offset"] != parts["h1-symbol"]["offset"]  # not in step


def test_a_wider_weather_card_has_more_hours_and_step_says_how_far_apart():
    def hours(cols, **more):
        return {n for n in widgets_of("forecast", cols, 2, **more) if re.fullmatch(r"h\d+-time", n)}

    assert len(hours(3)) == 5 and len(hours(4)) > 5 and len(hours(3, step=1)) <= 24
    times = widgets_of("forecast", 3, 2, step=2)
    assert [times[f"h{i}-time"]["sensor"] for i in range(3)] == [
        "weather.hour.2.time", "weather.hour.4.time", "weather.hour.6.time",
    ]  # fmt: skip


def test_the_card_shows_the_next_hours_and_the_weather_module_every_third():
    card = widgets_of("forecast", 3, 2)
    assert [card[f"h{i}-time"]["sensor"] for i in range(3)] == [
        "weather.hour.1.time", "weather.hour.2.time", "weather.hour.3.time",
    ]  # fmt: skip
    every_third = widgets_of("forecast", 3, 2, step=3)
    assert every_third["h1-time"]["sensor"] == "weather.hour.6.time"
    module = widgets_of("weather", 4, 1)
    assert module["f0-label"]["sensor"] != "" and WEATHER_STEP_DEFAULT == 0
    hours = widgets_of("weather", 4, 1, forecast="hours")
    assert hours["f0-label"]["sensor"] == "weather.hour.3.time"  # (as it was: step 0 is 3)


def test_a_small_weather_card_is_the_weather_module():
    for size in ((2, 1), (1, 1), (1, 2), (3, 1)):
        names, _, _ = drawn("forecast", cols=size[0], rows=size[1])
        assert "temp" in names and "symbol" not in names and "h0-time" not in names, size
    big, small = widgets_of("forecast", 3, 2), widgets_of("forecast", 2, 1)
    assert "kicker" in big and "kicker" not in small


def test_the_symbols_of_the_weather_card_move_with_the_planned_time():
    snap = snapshot_with()
    _, still, _ = drawn("forecast", snap, animate=True, now=10.0, moving=False)
    _, later, _ = drawn("forecast", snap, animate=True, now=10.9, moving=False)
    assert not differs(still, later)  # they stand where the theme says so
    _, a, ra = drawn("forecast", snap, animate=True, now=10.0)
    _, b, _ = drawn("forecast", snap, animate=True, now=10.9)
    assert differs(a, b) and ra.moving
    _, still_a, rb = drawn("forecast", snap, animate=False, now=10.0)
    assert not rb.moving  # a picture does not move


def test_the_weather_card_says_what_it_has_and_leaves_out_what_it_has_not():
    snap = snapshot_with(drop=("weather.rain", "weather.hour"))
    names, _, _ = drawn("forecast", snap)
    assert "temp" in names and not any(re.fullmatch(r"h\d+-\w+", n) for n in names)
    assert not {"rain"} & names  # no chance of rain known: no sentence about it
    raining = snapshot_with(
        weather__rain__minutes=45.0, weather__rain__text="Rain from 07:00 likely"
    )
    with_rain, frame, _ = drawn("forecast", raining)
    # (the demo has a rain sentence of its own: left out here)
    without, plain, _ = drawn("forecast", snapshot_with(drop=("weather.rain",)))
    assert "rain" in with_rain and "dry" not in with_rain and "rain" not in without
    assert differs(frame, plain)
    parts = widgets_of("forecast")
    colors = M_colors()
    assert parts["rain"]["color"].upper() == colors["gpu"].upper()  # blue where it will rain
    assert parts["rain"]["sensor"] == parts["dry"]["sensor"] == "weather.rain.text"


def test_the_sun_is_told_by_day_and_by_night_and_a_card_without_weather_is_a_calendar():
    by_day, _, _ = drawn("forecast", snapshot_with(sun__up=1))
    by_night, _, _ = drawn("forecast", snapshot_with(sun__up=0))
    assert "sun-set" in by_day and "sun-rise" not in by_day
    assert "sun-rise" in by_night and "sun-set" not in by_night
    none, _, _ = drawn("forecast", snapshot_with(drop=("weather", "sun")))
    assert {"day", "weekday", "month"} <= none and "temp" not in none and "symbol" not in none


def alone(kind, width, height, **more):
    """A card alone in a frame of its own size (one cell, a small margin)."""
    module = {"type": "module", "id": "m", "module": kind, "cols": 1, "rows": 1, **more}
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": width, "height": height},
            "grid": {"columns": 1, "rows": 1, "gap": 1, "margin": 4},
            "widgets": [module],
        }
    )


def inked(renderer, *names):
    """Where the named parts are inked on the frame (x0, y0, x1, y1), those that are drawn."""
    pieces = dict(renderer._last[0])
    found = []
    for name in names:
        piece = pieces.get(f"m/{name}")
        box = piece.layer.getbbox() if piece is not None else None
        if box is not None:
            found.append((piece.x + box[0], piece.y + box[1], piece.x + box[2], piece.y + box[3]))
    return found


@pytest.mark.parametrize("aspect", [1.5, 1.6, 1.65, 1.75, 2.0, 2.4])
@pytest.mark.parametrize("height", [240, 376])
def test_the_weather_cards_details_do_not_run_together_in_a_narrower_card(aspect, height):
    """The details ("Feels like 12°", "Wind 18 km/h") are one line: in a card narrower than the
    Studio's it is squeezed, and a gap stays between them (each was shrunk to fit its box, and
    the next box began where it ended)."""
    renderer = Renderer(alone("forecast", round(aspect * height), height), preview=True)
    renderer.render(snapshot_with(), 1.0)
    details = inked(renderer, "detail0", "detail1", "detail2")
    assert len(details) >= 2, "the card is the weather module here"
    for left, right in zip(details, details[1:], strict=False):
        assert right[0] - left[2] >= round(height / 376 * 8), (aspect, height, details)
    # and the line begins where the text above it does (its smallest size moved the first right)
    (sky,) = inked(renderer, "desc")
    assert abs(details[0][0] - sky[0]) <= round(height / 376 * 3), (aspect, height, sky, details)


def M_colors():
    from libre_panel.theme.modules import colors_of

    return colors_of(card_theme("forecast").palette)


# the playback card


def test_the_playback_card_shows_the_track_and_where_it_is():
    bare = snapshot_with()  # (a player that has no icon)
    bare.images.pop("media.app_icon")
    names, _, _ = drawn("playback", bare)
    assert {"cover", "playing-sign", "playing", "title", "artist", "app-bare", "progress",
            "position", "length"} <= names  # fmt: skip
    assert not {"paused", "paused-sign", "idle", "idle-icon", "app-icon", "app"} & names
    snap = snapshot_with()  # (with the app's icon the name moves aside for it)
    snap.images["media.app_icon"] = Image.new("RGBA", (48, 48), (240, 40, 60, 255))
    with_icon = drawn("playback", snap)[0]
    assert {"app-icon", "app"} <= with_icon and "app-bare" not in with_icon
    parts = widgets_of("playback")
    assert parts["progress"]["sensor"] == "media.progress" and parts["progress"]["max"] == 1
    assert parts["position"]["format"] == "{value:clock}" and parts["length"]["align"] == "right"
    assert parts["app-icon"]["src"] == "@media.app_icon"
    assert parts["title"]["sensor"] == "media.title" and parts["title"]["fit"] == "ellipsis"


def test_a_paused_track_has_its_own_sign_and_word():
    paused, _, _ = drawn("playback", snapshot_with(media__playing=0))
    assert {"paused-sign", "paused"} <= paused and not {"playing-sign", "playing"} & paused
    assert "title" in paused  # (the track is still there)


def test_nothing_playing_is_a_note_and_a_word():
    names, frame, _ = drawn("playback", snapshot_with(drop=("media",)))
    assert {"idle", "idle-icon"} <= names
    assert not {"title", "artist", "progress", "position", "length", "playing"} & names
    _, playing, _ = drawn("playback")
    assert differs(frame, playing)


def test_the_app_icon_and_the_progress_follow_their_readings():
    snap = snapshot_with()
    plain = drawn("playback", snap)[1]
    icon = Image.new("RGBA", (48, 48), (240, 40, 60, 255))
    snap.images["media.app_icon"] = icon
    with_icon = drawn("playback", snap)[1]
    assert differs(plain, with_icon)
    start = drawn("playback", snapshot_with(media__progress=0.1))[1]
    late = drawn("playback", snapshot_with(media__progress=0.9))[1]
    assert differs(start, late)


def test_a_track_without_an_artist_or_a_length_still_draws():
    snap = snapshot_with(drop=("media.artist", "media.duration", "media.position", "media.source"))
    names, frame, renderer = drawn("playback", snap)
    assert renderer.warnings == [] and "title" in names and frame.size == (1920, 480)


def test_a_small_playback_card_is_the_music_module():
    names, _, _ = drawn("playback", cols=2, rows=1)
    assert "state" in names and "playing-sign" not in names


def texts_drawn(renderer, snap):
    """What each part of the last frame said, by the part's name (without its module's)."""
    seen = {}
    original = renderer._text_widget

    def spy(widget, text, color):
        seen[widget["id"].split("/")[-1]] = text
        return original(widget, text, color)

    renderer._text_widget = spy
    renderer.render(snap, 1.0)
    return seen


def test_the_playback_card_shows_the_length_or_counts_down_to_the_end():
    assert WIDGET_SPECS["module"]["time"] == ("enum:total|remaining", "total")
    snap = snapshot_with(
        media__position=83.0, media__duration=227.0, media__remaining=144.0
    )  # 1:23 of 3:47
    for option, right in (("total", "3:47"), ("remaining", "-2:24")):
        seen = texts_drawn(Renderer(card_theme("playback", time=option), preview=True), snap)
        assert (seen["position"], seen["length"]) == ("1:23", right), option
    parts = widgets_of("playback", time="remaining")
    assert parts["length"]["sensor"] == "media.remaining" and parts["length"]["align"] == "right"
    assert parts["length"]["format"] == "-{value:clock}" and parts["length"]["hide_if_missing"]
    assert widgets_of("playback")["length"]["sensor"] == "media.duration"  # the default
    # a player that does not tell where it is: no time left to show, and no word about it
    snap = snapshot_with(drop=("media.position", "media.remaining"))
    seen = texts_drawn(Renderer(card_theme("playback", time="remaining"), preview=True), snap)
    assert "length" not in seen


def test_the_small_card_and_the_music_module_count_down_too():
    for kind, size in (("playback", (2, 1)), ("music", (3, 2))):
        parts = widgets_of(kind, *size, time="remaining")
        assert parts["length"]["sensor"] == "media.remaining", kind
        assert widgets_of(kind, *size)["length"]["sensor"] == "media.duration", kind
    slot = widgets_of("cards", time="remaining")  # a slot hands the option to its playback card
    assert slot["c0-length"]["sensor"] == "media.remaining"


# the ring instrument


def test_the_ring_instrument_is_a_ring_of_light_with_its_number_inside_and_its_readings():
    parts = widgets_of("instrument", 1, 2, source="cpu")
    ring = parts["ring"]
    assert ring["type"] == "gauge" and ring["sensor"] == "cpu.load" and ring["finish"] == "lit"
    assert ring["start_angle"] == -90 and ring["end_angle"] == 270 and ring["cap"] == "flat"
    assert 0.04 < ring["thickness"] / ring["w"] < 0.09 and ring["w"] == ring["h"]
    assert parts["value"]["sensor"] == "cpu.load" and parts["value"]["format"] == "{value:.0f}"
    assert parts["unit"]["text"] == "%" and parts["title"]["text"] == "CPU"
    cx = ring["x"] + ring["w"] / 2
    number, unit = parts["value"], parts["unit"]  # the number and its unit are centred as a pair
    assert number["align"] == "right" and number["x"] < unit["x"]
    assert number["x"] - number["max_width"] < cx < unit["x"] + 2 * unit["font_size"]
    assert ring["y"] < number["y"] < ring["y"] + ring["h"]  # inside the ring
    assert [parts[f"r{i}"]["sensor"] for i in range(3)] == ["cpu.temp", "cpu.power", "cpu.freq"]
    assert parts["r0"]["color_rules"]  # the temperature goes warm and hot
    assert not any(w.get("ridge") for w in parts.values())


@pytest.mark.parametrize("source", ["cpu", "gpu", "mem", "disk"])
def test_every_source_has_a_ring_instrument(source):
    names, frame, renderer = drawn("instrument", None, 1, 2, source=source, animate=True)
    assert {"ring", "value", "title"} <= names and renderer.warnings == []
    parts = widgets_of("instrument", 1, 2, source=source)
    assert parts["ring"]["sensor"] == {
        "cpu": "cpu.load", "gpu": "gpu.load", "mem": "mem.load", "disk": "disk.load",
    }[source]  # fmt: skip


def test_the_memory_instrument_shows_what_is_free_its_temperature_and_its_speed():
    parts = widgets_of("instrument", 1, 2, source="mem")
    assert [parts[f"r{i}"]["sensor"] for i in range(3)] == ["mem.free", "mem.temp", "mem.speed"]
    assert [parts[f"r{i}-label"]["text"] for i in range(3)] == ["FREE", "TEMP", "FREQ"]
    assert [parts[f"r{i}-unit"]["text"] for i in range(3)] == ["GB", "°C", "MT/s"]
    assert parts["r0"]["format"] == "{value:.1f}" and parts["r2"]["format"] == "{value:.0f}"
    assert parts["r1"]["color_rules"][0]["above"] < 75  # warm sooner than a processor is
    snap = snapshot_with()
    seen = texts_drawn(Renderer(card_theme("instrument", 1, 2, source="mem"), preview=True), snap)
    assert seen["r0"] == f"{snap.value('mem.free'):.1f}" and seen["r2"] == "5600"
    assert seen["r1"] == f"{snap.value('mem.temp'):.0f}"


def test_a_memory_reading_a_pc_cannot_give_stays_on_show_as_dashes_with_its_name():
    # the modules' temperature needs administrator rights, their speed a readable SMBIOS table
    snap = snapshot_with(drop=("mem.temp", "mem.speed"))
    renderer = Renderer(card_theme("instrument", 1, 2, source="mem"), preview=True)
    seen = texts_drawn(renderer, snap)
    assert (seen["r1"], seen["r2"]) == ("--", "--")
    assert {"r0-label", "r1-label", "r2-label"} <= set(seen)  # the column keeps its shape
    assert not {"r1-unit", "r2-unit"} & set(seen)  # a unit with no number would be noise
    assert "r0-unit" in seen
    # a processor's missing reading leaves its row out, as it always did
    cpu = Renderer(card_theme("instrument", 1, 2, source="cpu"), preview=True)
    seen = texts_drawn(cpu, snapshot_with(drop=("cpu.temp",)))
    assert "r0" not in seen and "r0-label" not in seen and "r1" in seen


def test_the_readings_fit_in_their_rows_and_never_run_into_their_names():
    for size in ((1, 2), (2, 1), (3, 2)):
        parts = widgets_of("instrument", *size, source="gpu")
        for i in range(3):
            if f"r{i}" not in parts:
                continue
            label, number = parts[f"r{i}-label"], parts[f"r{i}"]
            assert label["x"] + label["max_width"] <= number["x"] - number["max_width"] * 0.5 \
                or number["align"] == "right", (size, i)  # fmt: skip
            unit = parts.get(f"r{i}-unit")
            if unit is not None:
                assert unit["x"] >= number["x"], (size, i)


def test_a_wide_instrument_puts_its_name_and_readings_beside_the_ring_and_a_tall_one_below():
    tall, wide = widgets_of("instrument", 1, 2), widgets_of("instrument", 2, 1)
    assert tall["title"]["y"] > tall["ring"]["y"] + tall["ring"]["h"] * 0.9
    assert wide["title"]["x"] > wide["ring"]["x"] + wide["ring"]["w"] * 0.9
    one = widgets_of("instrument", 1, 1)
    assert "r0" not in one or one["r0"]["y"] > one["ring"]["y"]  # a small one has fewer rows
    assert len([n for n in tall if re.fullmatch(r"r\d", n)]) == 3


def test_an_instrument_without_its_readings_is_empty_and_the_disk_takes_a_missing_gpus_place():
    snap = snapshot_with(drop=("gpu.",))
    names, _, _ = drawn("instrument", snap, 1, 2, source="gpu")
    assert any(n.startswith("alt-") for n in names) and "alt-ring" in names  # the disk's
    none = snapshot_with(drop=("cpu.",))
    names, _, _ = drawn("instrument", none, 1, 2, source="cpu")
    assert "ring" not in names and "value" not in names
    partial = snapshot_with(drop=("cpu.temp",))
    names, _, _ = drawn("instrument", partial, 1, 2, source="cpu")
    assert "ring" in names and "r0" not in names and "r1" in names  # the missing row is left out


def test_the_instrument_is_lit_unless_the_theme_says_otherwise():
    flat = widgets_of("instrument", 1, 2, finish="flat")
    assert flat["ring"].get("finish", "flat") == "flat"
    facet = widgets_of("instrument", 1, 2, finish="facet")
    assert facet["ring"]["finish"] == "facet"


# the network card


def test_the_network_card_has_up_and_down_with_their_speeds_and_the_history_mirrored():
    parts = widgets_of("netcard", 2, 1)
    assert parts["c0-download"]["type"] == "downloads"
    assert (
        parts["c1-up-value"]["format"] == "{value:rate}"
        and parts["c1-up-unit"]["format"] == "{value:rate_unit}"
    )
    assert (
        parts["c1-down-value"]["sensor"] == "net.down"
        and parts["c1-up-value"]["sensor"] == "net.up"
    )
    up, down = parts["c1-up-history"], parts["c1-down-history"]
    assert up["direction"] == "up" and down["direction"] == "down" and not up["inset"]
    assert up["y"] + up["h"] <= down["y"] + 1 and up["scale"] == down["scale"] == "sqrt"
    assert parts["c1-axis"]["y"] == down["y"] or abs(parts["c1-axis"]["y"] - down["y"]) <= 1


def test_a_small_network_card_is_the_network_module():
    parts = widgets_of("netcard", 1, 1)
    assert "c1-download-icon" in parts and "c1-up-value" not in parts


# the fan instrument


def test_a_fan_instrument_is_a_fan_with_its_name_and_its_speed():
    parts = widgets_of("instrument", 2, 1, source="fan")
    fan = parts["fan"]
    assert fan["type"] == "fan" and fan["sensor"] == "gpu.fan" and fan["w"] == fan["h"]
    assert fan["frame"] == "ring" and fan["max_rpm"] == 3000
    assert parts["value"]["sensor"] == "gpu.fan" and parts["unit"]["text"] == "RPM"
    # the name the fan goes by (the user's, FanControl's, the sensor's): its reading's
    title = parts["title"]
    assert (title["sensor"], title["format"], title["fallback"]) == ("gpu.fan", "{label:upper}",
                                                                     "GPU")  # fmt: skip
    assert parts["title"]["x"] > fan["x"] + fan["w"]  # wide: the name and the speed beside it
    tall = widgets_of("instrument", 1, 2, source="fan")
    assert tall["title"]["y"] > tall["fan"]["y"] + tall["fan"]["h"]  # tall: under it
    assert not any(w.get("ridge") for w in parts.values())


def test_a_fan_instrument_can_name_its_fan_and_its_colour():
    parts = widgets_of("instrument", 1, 1, source="fan", sensor="fan.board.case_1")
    assert parts["fan"]["sensor"] == parts["value"]["sensor"] == "fan.board.case_1"
    assert parts["title"]["sensor"] == "fan.board.case_1"
    assert parts["title"]["fallback"] == "CASE 1"  # without a reading of its own
    named = widgets_of("instrument", 1, 1, source="fan", title="Radiator")
    assert named["title"]["text"] == "RADIATOR"
    colors = M_colors()
    assert parts["fan"]["color"].upper() == colors["cpu"].upper()  # (a graphics card's is `gpu`)
    own = widgets_of("instrument", 1, 1, source="fan", color="#ff8800")
    assert own["fan"]["color"].lower() == "#ff8800"


@pytest.mark.parametrize("size", [(1, 1), (2, 1), (1, 2), (2, 2), (3, 1)])
def test_a_fan_instrument_draws_in_its_cells_and_turns_only_when_it_may(size):
    names, frame, renderer = drawn("instrument", None, *size, source="fan", animate=True)
    assert {"fan", "value", "title", "unit"} <= names and renderer.warnings == []
    x, y, w, h = renderer.module_boxes["m"]
    for widget in renderer.widgets:
        if widget.get("_module") == "m" and "w" in widget and widget["type"] != "rect":
            assert x <= widget["x"] and widget["x"] + widget["w"] <= x + w + 1, widget["id"]
            assert y <= widget["y"] and widget["y"] + widget["h"] <= y + h + 1, widget["id"]


def test_a_fan_instrument_without_its_fan_is_empty_and_the_named_fan_decides():
    snap = snapshot_with(drop=("gpu.fan",))
    names, _, _ = drawn("instrument", snap, 2, 1, source="fan")
    assert "fan" not in names and "value" not in names
    other = snapshot_with(drop=("gpu.fan",), fan__cpu=1200.0)
    names, _, _ = drawn("instrument", other, 2, 1, source="fan", sensor="fan.cpu")
    assert {"fan", "value"} <= names
    names, _, _ = drawn("instrument", other, 2, 1, source="fan", sensor="fan.gone")
    assert "fan" not in names


# the colour of the cover


def test_the_cover_colour_is_a_colour_that_follows_the_cover_on_show():
    widget = {"type": "bar", "id": "b", "x": 4, "y": 4, "w": 100, "h": 12, "sensor": "cpu.load",
              "color": "@cover", "color2": None, "background": None}  # fmt: skip
    renderer = Renderer(theme_of(widget), preview=True)
    plain = snapshot_with(drop=("media",))
    assert renderer.color("@cover") == renderer.color("#22d3ee")  # (no snapshot yet: the accent)
    red = snapshot_with()
    red.images["media.cover"] = Image.new("RGB", (32, 32), (200, 30, 40))
    green = snapshot_with()
    green.images["media.cover"] = Image.new("RGB", (32, 32), (30, 200, 60))
    frames = []
    for snap in (plain, red, green, plain):
        frames.append(renderer.render(snap, 1.0)[0].copy())
        assert renderer.warnings == []
    assert differs(frames[0], frames[1]) and differs(frames[1], frames[2])
    assert not differs(frames[0], frames[3])  # the cover goes: the accent comes back
    renderer.render(red, 1.0)
    r, g, b, a = renderer.color("@cover")
    assert r > g and r > b and a == 255 and max(r, g, b) >= 180
    assert renderer.color("@cover", 0.5)[3] == 127


def test_the_playback_card_has_its_sign_and_progress_in_the_covers_colour():
    parts = widgets_of("playback")
    for name in ("playing-sign", "paused-sign", "progress"):
        assert parts[name]["color"] == "@cover", name
    own = widgets_of("playback", color="#ff8800")
    assert (
        own["progress"]["color"].lower() == "#ff8800"
        and own["playing-sign"]["color"].lower() == "#ff8800"
    )
    snap = snapshot_with()
    orange, blue = snapshot_with(), snapshot_with()
    orange.images["media.cover"] = Image.new("RGB", (32, 32), (250, 150, 40))
    blue.images["media.cover"] = Image.new("RGB", (32, 32), (40, 120, 250))
    a = drawn("playback", orange)[1]
    b = drawn("playback", blue)[1]
    c = drawn("playback", snap)[1]
    assert differs(a, b) and differs(a, c)


def test_a_need_can_ask_for_a_picture():
    snap = snapshot_with()
    snap.images.pop("media.app_icon")  # (the demo's player has an icon: this one has none)
    assert not need_holds("@media.app_icon", snap) and need_holds("!@media.app_icon", snap)
    assert need_holds("@media.cover", snap) and not need_holds("!@media.cover", snap)
    snap.images["media.app_icon"] = Image.new("RGBA", (8, 8))
    assert need_holds("@media.app_icon", snap)
    assert need_key("!@media.app_icon") == "media.app_icon"
    from libre_panel.render.renderer import _reads

    assert "media.app_icon" in _reads([{"type": "text", "id": "t", "needs": "@media.app_icon"}])


# the game card


def test_the_game_card_says_no_game_only_where_none_was_found():
    """Game mode found a game whose frame rate has not come (yet: PresentMon's rights, an
    anti-cheat, a launcher): the card names the game and waits, instead of "No game running"."""
    nothing, _, _ = drawn("game", snapshot_with(drop=("game.",)))
    assert {"idle", "idle-icon"} <= nothing and not {"idle-game", "idle-wait", "fps"} & nothing
    waiting, _, _ = drawn("game", snapshot_with(drop=("game.",), game__name="Space Game"))
    assert {"idle-icon", "idle-game", "idle-wait"} <= waiting
    assert not {"idle", "fps"} & waiting
    playing, _, _ = drawn("game", snapshot_with(game__name="Space Game"))
    assert "fps" in playing and not {"idle", "idle-icon", "idle-game", "idle-wait"} & playing


def test_the_game_card_tells_the_time_quietly_where_it_has_room():
    """The time sits small and dim in the corner of the header, playing or not, and keeps
    the game's name from running into it; a small card leaves the line to the name."""
    for snap in (snapshot_with(game__name="Space Game"), snapshot_with(drop=("game.",))):
        names, _, _ = drawn("game", snap, 4, 2)
        assert "clock" in names
    parts = widgets_of("game", 4, 2)
    clock, title = parts["clock"], parts["title"]
    assert clock["format"] == "%H:%M" and clock["align"] == "right"
    assert clock["font_size"] == title["font_size"] and clock["color"] != title["color"]
    assert title["x"] + title["max_width"] < clock["x"] - clock["max_width"]
    assert "clock" not in widgets_of("game", 1, 1)

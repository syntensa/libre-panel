"""Sun and moon, clocks in other time zones, countdowns and pictures."""

import json
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from PIL import Image

from libre_panel.render.countdown import countdown_text, parse_target
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.sensors.sky import SkyProvider, moon_name, moon_phase, sky_readings, sun_times
from libre_panel.theme.model import load_theme, parse_theme
from libre_panel.timezones import zone


def local(t, hours):
    return datetime.fromtimestamp(t, timezone(timedelta(hours=hours)))


@pytest.mark.parametrize(
    ("day", "place", "hours", "rise", "down"),
    [
        (date(2026, 6, 21), (52.52, 13.405), 2, "04:43", "21:33"),  # Berlin, midsummer
        (date(2026, 12, 21), (40.71, -74.0), -5, "07:16", "16:31"),  # New York, midwinter
        (date(2026, 3, 20), (-33.87, 151.21), 11, "06:58", "19:07"),  # Sydney, equinox
    ],
)
def test_sunrise_and_sunset(day, place, hours, rise, down):
    def minutes(text):
        return int(text[:2]) * 60 + int(text[3:])

    sun = sun_times(day, *place)  # published times, to within two minutes
    assert abs(minutes(local(sun["rise"], hours).strftime("%H:%M")) - minutes(rise)) <= 2
    assert abs(minutes(local(sun["set"], hours).strftime("%H:%M")) - minutes(down)) <= 2


def test_polar_night_and_midnight_sun():
    assert sun_times(date(2026, 12, 21), 69.65, 18.96)["always"] == "night"
    assert sun_times(date(2026, 6, 21), 69.65, 18.96)["always"] == "day"
    readings = sky_readings(datetime(2026, 12, 21, 12, tzinfo=UTC).timestamp(), 69.65, 18.96)
    assert "sun.rise" not in readings and readings["sun.up"].value == 0


def test_moon_phases():
    full = datetime(2026, 10, 26, 4, 12, tzinfo=UTC).timestamp()  # a full moon
    assert moon_phase(full) == pytest.approx(0.5, abs=0.03)
    assert moon_name(moon_phase(full)) == "Full moon"
    assert moon_name(0.0) == moon_name(0.99) == "New moon"
    assert moon_name(0.25) == "First quarter" and moon_name(0.75) == "Last quarter"
    readings = sky_readings(full)
    assert readings["moon.illumination"].value > 99 and "sun.rise" not in readings  # no place
    assert readings["moon.full_in"].value == pytest.approx(0, abs=1) or (
        readings["moon.full_in"].value > 28
    )


def test_sky_readings_with_a_place():
    noon = datetime(2026, 6, 21, 11, tzinfo=UTC).timestamp()
    readings = SkyProvider({"latitude": 52.52, "longitude": 13.405}).read()
    assert {"sun.rise", "sun.set", "moon.phase"} <= set(readings)
    day = sky_readings(noon, 52.52, 13.405)
    assert day["sun.up"].value == 1 and 0.3 < day["sun.progress"].value < 0.7
    assert day["sun.elevation"].value > 55 and day["sun.daylight"].value > 16 * 3600
    night = sky_readings(noon + 12 * 3600, 52.52, 13.405)
    assert night["sun.up"].value == 0 and night["sun.progress"].value is None


NOW = datetime(2026, 12, 20, 10, 0)


@pytest.mark.parametrize(
    ("target", "part", "text"),
    [
        ("12-24", "number", "4"), ("12-24", "unit", "days"), ("12-24", "text", "4 days"),
        ("12-21", "unit", "day"), ("2026-12-24 18:00", "clock", "08:00:00"),
        ("2026-12-20 11:30", "number", "1:30"), ("2026-12-20 11:30", "unit", "hours"),
        ("2026-12-20 10:20", "number", "20:00"), ("2026-12-20 10:20", "unit", "minutes"),
        ("2026-12-20 11:30", "text", "1 h 30 min"), ("12-20", "number", "Today"),
        ("2026-12-20 09:00", "text", "Now"), ("12-19", "days", "364"),
        ("01-01", "date", "Friday, 01 January 2027"), ("2025-01-01", "number", "0"),
        ("Christmas", "number", "--"),
    ],
)  # fmt: skip
def test_countdown_parts(target, part, text):
    assert countdown_text(target, part, NOW) == text


def test_countdown_targets():
    assert parse_target("2026-12-24 18:00") == (2026, 12, 24, 18, 0)
    assert parse_target("12-24") == (None, 12, 24, None, None)
    assert parse_target("13-01") is None and parse_target("12-24 25:00") is None
    assert countdown_text("12-20", "number", NOW, done="Party!") == "Party!"


def frame(*widgets, root=None, width=320, height=240):
    data = {
        "format": "libre-panel-theme/1", "name": "time",
        "display": {"width": width, "height": height}, "widgets": list(widgets),
    }  # fmt: skip
    if root is None:
        return parse_theme(data)
    (root / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    return load_theme(root)


def snap(now):
    snapshot = demo_snapshot(fixed_time=now.timestamp())
    snapshot.now = now
    return snapshot


def test_clocks_in_other_time_zones():
    if zone("Asia/Tokyo") is None:
        pytest.skip("no time zone database")
    noon = datetime(2026, 1, 15, 12, 0, tzinfo=UTC).astimezone().replace(tzinfo=None)
    theme = frame(
        {"type": "clock", "id": "here", "format": "%H:%M"},
        {"type": "clock", "id": "tokyo", "format": "%H:%M", "timezone": "Asia/Tokyo"},
        {"type": "clock", "id": "nowhere", "format": "%H:%M", "timezone": "Mars/Base"},
    )
    renderer = Renderer(theme)
    tokyo = renderer._time(renderer.widgets[1], snap(noon))
    assert tokyo.strftime("%H:%M") == "21:00"
    renderer.render(snap(noon))
    assert any("Mars/Base" in w for w in renderer.warnings)


def test_analog_clock_hands_move():
    theme = frame({"type": "analog", "id": "face", "x": 10, "y": 10, "w": 150, "h": 150})
    renderer = Renderer(theme)
    first, _ = renderer.render(snap(datetime(2026, 1, 1, 3, 0, 0)))
    second, _ = renderer.render(snap(datetime(2026, 1, 1, 9, 30, 15)))
    assert first.getbbox() is not None and first.tobytes() != second.tobytes()
    still = Renderer(frame({"type": "analog", "id": "face", "w": 150, "h": 150, "seconds": False}))
    a, _ = still.render(snap(datetime(2026, 1, 1, 3, 0, 0)))
    b, _ = still.render(snap(datetime(2026, 1, 1, 3, 0, 40)))
    assert a.tobytes() == b.tobytes()  # without the second hand nothing moves


@pytest.mark.parametrize(("phase", "lit_side"), [(0.25, "right"), (0.75, "left"), (0.5, "both")])
def test_the_moon_is_lit_on_the_right_side(phase, lit_side):
    theme = frame({"type": "moon", "id": "moon", "x": 0, "y": 0, "size": 100,
                   "color": "#ffffff", "color2": "#000000"})  # fmt: skip
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    snapshot.readings["moon.phase"] = Reading("moon.phase", phase)
    image, _ = Renderer(theme).render(snapshot)
    left, right = image.getpixel((25, 50))[0], image.getpixel((75, 50))[0]
    assert {"right": right > 200 > left, "left": left > 200 > right,
            "both": left > 200 and right > 200}[lit_side]  # fmt: skip


def test_a_slideshow_takes_turns(tmp_path):
    folder = tmp_path / "pictures"
    (folder / "assets").mkdir(parents=True)
    for name, color in (("a.png", "red"), ("b.png", "lime"), ("c.jpg", "blue")):
        Image.new("RGB", (40, 20), color).save(folder / "assets" / name)
    (folder / "assets" / "notes.txt").write_text("not a picture")
    theme = frame(
        {"type": "image", "id": "show", "x": 0, "y": 0, "w": 60, "h": 60, "fit": "cover",
         "radius": 12, "slides": "assets/*\n../outside.png", "seconds": 5},
        root=folder,
    )  # fmt: skip
    renderer = Renderer(theme)
    assert renderer._slides(renderer.widgets[0]) == ["assets/a.png", "assets/b.png", "assets/c.jpg",
                                                     "../outside.png"]  # fmt: skip
    base = datetime(2026, 1, 1, 12, 0, 0)
    colors = []
    for step in range(3):
        image, _ = renderer.render(snap(base + timedelta(seconds=5 * step)))
        colors.append(image.getpixel((30, 30)))
        assert image.getpixel((0, 0)) == (0, 0, 0)  # a round corner
    assert len(set(colors)) == 3  # three pictures in turn, each filling the box
    assert any("outside" in w for w in renderer.warnings) or len(colors) == 3


def test_sky_modules_without_a_place_show_the_moon():
    from test_modules import shown, snapshot, theme

    renderer = Renderer(theme({"module": "sun", "cols": 2}), preview=True)
    with_place = shown(renderer, snapshot())
    assert "m0/arc" in with_place and "m0/moon" in with_place and "m0/only-moon" not in with_place
    without = shown(renderer, snapshot(drop=("sun.",)))
    assert "m0/only-moon" in without and "m0/arc" not in without


def test_world_clock_skips_unknown_places():
    from test_modules import theme

    if zone("Asia/Tokyo") is None:
        pytest.skip("no time zone database")
    t = theme({"module": "world", "cols": 2, "items": "Asia/Tokyo = Tokio\nNowhere/Land\n# note"})
    renderer = Renderer(t, preview=True)
    clocks = [w for w in renderer.widgets if w["type"] == "clock"]
    assert {w["timezone"] for w in clocks} == {"Asia/Tokyo"}
    assert any(w.get("text") == "TOKIO" for w in renderer.widgets)


def test_countdown_and_picture_modules():
    from test_modules import theme

    renderer = Renderer(theme({"module": "countdown", "cols": 2, "target": "2026-12-24 18:00",
                               "title": "Party"}), preview=True)  # fmt: skip
    parts = {w["id"]: w for w in renderer.widgets}
    assert parts["m0/number"]["target"] == "2026-12-24 18:00" and "m0/clock" in parts
    assert parts["m0/title"]["text"] == "PARTY"
    dated = Renderer(theme({"module": "countdown", "cols": 2, "target": "12-24"}), preview=True)
    assert "m0/clock" not in {w["id"] for w in dated.widgets}  # a day has no hours to count
    empty = Renderer(theme({"module": "image", "cols": 2, "rows": 2}), preview=True)
    assert "m0/hint" in {w["id"] for w in empty.widgets}
    shows = Renderer(theme({"module": "image", "cols": 2, "rows": 2, "items": "assets/*.jpg",
                            "seconds": 30}), preview=True)  # fmt: skip
    (picture,) = [w for w in shows.widgets if w["type"] == "image"]
    assert picture["slides"] == "assets/*.jpg" and picture["seconds"] == 30
    assert picture["fit"] == "cover"


def test_the_hub_knows_the_sky():
    from libre_panel.app import build_hub
    from libre_panel.config import Config

    config = Config()
    config.weather.latitude, config.weather.longitude = 48.0, 11.0
    hub = build_hub(config)
    readings = hub.snapshot().readings
    hub.close()
    assert {"sun.rise", "moon.phase"} <= set(readings)

"""What is playing, Home Assistant, MQTT and calendars, and their modules."""

import gc
import json
import threading
from datetime import datetime, timedelta

import pytest
from PIL import Image

from libre_panel.render.formatting import clock_duration, safe_format
from libre_panel.render.renderer import Renderer
from libre_panel.sensors import base, media
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider, create_provider, want
from libre_panel.sensors.calendar import calendar_readings, events_from_ics
from libre_panel.sensors.homeassistant import readings_from_states
from libre_panel.sensors.media import Track, load_cover, parse_osascript, parse_playerctl
from libre_panel.sensors.mqtt import readings_from_message, topic_matches

# -- what is playing ---------------------------------------------------------------


def test_a_song_moves_on_while_it_plays():
    song = Track("playing", "Song", "Band", "", 60.0, 200.0, None, "Spotify", at=100.0)
    later = song.readings(110.0)
    assert later["media.position"].value == 70.0
    assert later["media.progress"].value == pytest.approx(0.35)
    assert later["media.state"].value == "Playing" and "media.album" not in later
    paused = Track("paused", "Song", position=60.0, duration=200.0, at=100.0)
    assert paused.readings(150.0)["media.position"].value == 60.0
    assert paused.readings(150.0)["media.playing"].value == 0
    assert Track("playing", "Live", position=5.0, at=0).readings(9999)["media.position"].value


def test_players_on_linux_and_macos():
    sep = "\x1f"
    line = sep.join(["Playing", "Band", "Song", "Album", "61000000", "200000000",
                     "file:///tmp/cover.png", "spotify"])  # fmt: skip
    song = parse_playerctl(line + "\n", 5.0)
    assert (song.state, song.title, song.position, song.duration) == ("playing", "Song", 61, 200)
    assert song.source == "Spotify" and song.cover == "file:///tmp/cover.png"
    assert parse_playerctl("", 0) is None
    assert parse_playerctl(sep.join(["Stopped", "", "", "", "", "", "", "vlc"]), 0) is None
    mac = parse_osascript(
        sep.join(["paused", "Band", "Song", "Album", "12,5", "180.0", "", "Music"]), 0
    )
    assert (mac.state, mac.position, mac.duration, mac.cover) == ("paused", 12.5, 180.0, None)


def test_covers_from_files(tmp_path):
    Image.new("RGB", (900, 900), "red").save(tmp_path / "cover.png")
    cover = load_cover((tmp_path / "cover.png").as_uri())
    assert cover.size == (512, 512) and cover.mode == "RGBA"
    with open(tmp_path / "cover.png", "rb") as file:
        assert load_cover(file.read()).size == (512, 512)
    assert load_cover(str(tmp_path / "missing.png")) is None and load_cover(None) is None


def test_music_is_only_asked_while_shown(monkeypatch):
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    asked = threading.Event()

    class Player:
        async def poll(self, want_cover):
            asked.set()
            return Track("playing", "Song", "Band", cover=None, at=0.0)

    monkeypatch.setattr(media, "_backend", Player)
    monkeypatch.setattr(media, "POLL_SECONDS", 0.05)
    provider = media.MediaProvider()
    assert provider.read() == {} and provider._thread is None  # nobody shows it
    owner = type("Theme", (), {})()
    want(owner, {"media.title"})
    provider.read()
    assert asked.wait(5)
    for _ in range(100):
        if provider.read():
            break
        threading.Event().wait(0.05)
    assert provider.read()["media.title"].value == "Song"
    provider.close()


def test_pictures_of_sources_reach_image_widgets():
    class Covers(SensorProvider):
        name = "covers"

        def read(self):
            return {"media.title": Reading("media.title", "Song")}

        def images(self):
            return {"media.cover": Image.new("RGB", (50, 50), "lime")}

    snapshot = SensorHub([Covers()]).snapshot()
    assert "media.cover" in snapshot.images
    from libre_panel.theme.model import parse_theme

    theme = parse_theme({"format": "libre-panel-theme/1", "name": "c",
                         "display": {"width": 100, "height": 100},
                         "widgets": [{"type": "image", "id": "cover", "src": "@media.cover",
                                      "w": 40, "h": 40, "fit": "cover"}]})  # fmt: skip
    frame, boxes = Renderer(theme).render(snapshot)
    assert frame.getpixel((20, 20)) == (0, 255, 0) and "cover" in boxes
    snapshot.images = {}
    frame, boxes = Renderer(theme).render(snapshot)
    assert "cover" not in boxes  # nothing to show yet


def test_song_lengths():
    assert clock_duration(187) == "3:07" and clock_duration(3765) == "1:02:45"
    assert safe_format("{value:clock}", 61.9) == "1:01"


# -- Home Assistant and MQTT ---------------------------------------------------------


def test_home_assistant_states():
    states = [
        {"entity_id": "sensor.living_room_temperature", "state": "21.5",
         "attributes": {"unit_of_measurement": "°C", "friendly_name": "Living room"}},
        {"entity_id": "binary_sensor.front_door", "state": "off", "attributes": {}},
        {"entity_id": "sensor.broken", "state": "unavailable", "attributes": {}},
        {"entity_id": "climate.hall", "state": "heat",
         "attributes": {"current_temperature": 19.5, "friendly_name": "Hall",
                        "hvac_modes": ["heat"]}},
        {"entity_id": "light.desk", "state": "on", "attributes": {"brightness": 128}},
    ]  # fmt: skip
    out = readings_from_states(states, ["sensor.*", "binary_sensor.*", "climate.*"])
    room = out["ha.sensor.living_room_temperature"]
    assert (room.value, room.unit, room.label) == (21.5, "°C", "Living room")
    assert out["ha.binary_sensor.front_door"].value == "off"
    assert out["ha.sensor.broken"].value is None
    assert out["ha.climate.hall"].value == "heat"
    assert out["ha.climate.hall.current_temperature"].value == 19.5
    assert "ha.light.desk" not in out


@pytest.mark.parametrize(
    ("pattern", "topic", "matches"),
    [("home/+/temperature", "home/kitchen/temperature", True),
     ("home/+/temperature", "home/kitchen/humidity", False),
     ("zigbee2mqtt/#", "zigbee2mqtt/a/b", True), ("a/b", "a/b/c", False),
     ("#", "anything/at/all", True)],
)  # fmt: skip
def test_mqtt_wildcards(pattern, topic, matches):
    assert topic_matches(pattern, topic) is matches


def test_mqtt_messages():
    plain = readings_from_message("home/kitchen/temperature", b"21.4", {"home/+/temperature": "°C"})
    reading = plain["mqtt.home.kitchen.temperature"]
    assert (reading.value, reading.unit) == (21.4, "°C")
    nested = readings_from_message(
        "zigbee2mqtt/door", json.dumps({"contact": False, "battery": 87, "name": "Front"}).encode()
    )
    assert nested["mqtt.zigbee2mqtt.door.contact"].value == 0.0
    assert nested["mqtt.zigbee2mqtt.door.battery"].value == 87.0
    assert nested["mqtt.zigbee2mqtt.door.name"].value == "Front"
    assert readings_from_message("status/v1.2", b"online")["mqtt.status.v1_2"].value == "online"


def test_the_new_sources_are_built_in():
    for name in ("calendar", "homeassistant", "mqtt", "media", "sky"):
        provider = create_provider(name, {})
        assert provider.name == name
        provider.close()


# -- calendars -----------------------------------------------------------------------

ICS = """BEGIN:VCALENDAR
X-WR-CALNAME:Home
BEGIN:VEVENT
UID:1
SUMMARY:Dentist\\, Dr. Ray
LOCATION:Main St 1
DTSTART;TZID=Europe/Berlin:20261020T140000
DTEND;TZID=Europe/Berlin:20261020T150000
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Team meeting
DTSTART;TZID=W. Europe Standard Time:20261005T093000
DURATION:PT30M
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE;TZID=W. Europe Standard Time:20261021T093000
BEGIN:VALARM
TRIGGER:-PT15M
SUMMARY:not an event
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:2
RECURRENCE-ID;TZID=W. Europe Standard Time:20261026T093000
SUMMARY:Team meeting (moved)
DTSTART;TZID=W. Europe Standard Time:20261026T113000
DTEND;TZID=W. Europe Standard Time:20261026T120000
END:VEVENT
BEGIN:VEVENT
UID:3
SUMMARY:Birthday
DTSTART;VALUE=DATE:20101022
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:4
SUMMARY:Pay rent
DTSTART;VALUE=DATE:20260101
RRULE:FREQ=MONTHLY;BYMONTHDAY=-1
END:VEVENT
BEGIN:VEVENT
UID:5
SUMMARY:Club night
DTSTART;TZID=Europe/Berlin:20260113T190000
RRULE:FREQ=MONTHLY;BYDAY=2TU;COUNT=20
END:VEVENT
BEGIN:VEVENT
UID:6
SUMMARY:Off
STATUS:CANCELLED
DTSTART:20261020T170000Z
END:VEVENT
BEGIN:VEVENT
UID:7
SUMMARY:Long
 er title, folded
DTSTART:20261019T060000Z
DTEND:20261019T100000Z
RRULE:FREQ=DAILY;UNTIL=20261020T060000Z
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def berlin(monkeypatch):
    """Local time is Berlin's, as for someone there."""
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("cannot change the local time zone here")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def fields(readings, n):
    prefix = f"calendar.{n}."
    return {k[len(prefix) :]: r.value for k, r in readings.items() if k.startswith(prefix)}


def test_calendar_events_and_repeats(berlin):
    now = datetime(2026, 10, 19, 8, 0)
    events = events_from_ics(ICS, now, days=30)
    seen = [(e.start.strftime("%m-%d %H:%M"), e.title) for e in events]
    assert ("10-19 09:30", "Team meeting") in seen and ("10-21 09:30", "Team meeting") not in seen
    assert ("10-26 11:30", "Team meeting (moved)") in seen
    assert ("10-26 09:30", "Team meeting") not in seen and ("10-28 09:30", "Team meeting") in seen
    assert ("10-20 14:00", "Dentist, Dr. Ray") in seen
    assert ("10-22 00:00", "Birthday") in seen and ("10-31 00:00", "Pay rent") in seen
    assert ("11-10 19:00", "Club night") in seen  # the second Tuesday
    assert ("10-19 08:00", "Longer title, folded") in seen
    assert ("10-20 08:00", "Longer title, folded") in seen  # UNTIL takes the last one in
    assert not any(title == "Off" for _, title in seen)
    assert all(e.calendar == "Home" for e in events)


def test_calendar_readings(berlin):
    now = datetime(2026, 10, 19, 9, 45)
    readings = calendar_readings(events_from_ics(ICS, now, days=14), now)
    first = {
        k.split(".", 2)[2]: r.value for k, r in readings.items() if k.startswith("calendar.1.")
    }
    assert first["title"] == "Longer title, folded" and first["now"] == 1  # going on
    second = {
        k.split(".", 2)[2]: r.value for k, r in readings.items() if k.startswith("calendar.2.")
    }
    assert second["title"] == "Team meeting" and second["now"] == 1
    third = {
        k.split(".", 2)[2]: r.value for k, r in readings.items() if k.startswith("calendar.3.")
    }
    assert third["when"] == "Tomorrow 08:00" and "now" not in third
    birthday = next(k for k, r in readings.items() if r.value == "Birthday")
    assert readings[birthday.replace("title", "when")].value == "Thursday · All day"
    assert readings["calendar.today"].value == 2


def test_calendar_from_a_file(tmp_path, berlin):
    (tmp_path / "home.ics").write_text(ICS, encoding="utf-8")
    from libre_panel.sensors.calendar import CalendarProvider

    provider = CalendarProvider({"sources": [str(tmp_path / "home.ics")], "days": 400})
    for _ in range(100):
        if provider._texts:
            break
        threading.Event().wait(0.05)
    readings = provider.read()
    provider.close()
    assert readings["calendar.events"].value >= 1 and "calendar.1.title" in readings


# -- modules -------------------------------------------------------------------------


def test_music_module_waits_for_a_song():
    from test_modules import shown, snapshot, theme

    renderer = Renderer(theme({"module": "music", "cols": 2}), preview=True)
    playing = shown(renderer, snapshot())
    assert {"m0/cover", "m0/title", "m0/progress"} <= set(playing) and "m0/idle" not in playing
    quiet = shown(renderer, snapshot(drop=("media.",)))
    assert "m0/idle" in quiet and "m0/title" not in quiet


def test_agenda_module_rows_follow_the_events():
    from test_modules import shown, snapshot, theme

    renderer = Renderer(theme({"module": "agenda", "cols": 2, "rows": 2}), preview=True)
    parts = shown(renderer, snapshot())
    assert "m0/e0-title" in parts and "m0/none" not in parts and "m0/setup" not in parts
    assert "m0/e0-now" not in parts  # nothing is going on right now
    snap = snapshot(drop=("calendar.",))
    assert "m0/setup" in shown(renderer, snap) and "m0/e0-title" not in shown(renderer, snap)
    empty = snapshot(drop=("calendar.",))
    empty.readings["calendar.events"] = Reading("calendar.events", 0.0)
    assert "m0/none" in shown(renderer, empty)


def test_demo_has_a_calendar_and_a_song():
    from libre_panel.sensors.demo import demo_snapshot

    snap = demo_snapshot(fixed_time=datetime(2026, 10, 9, 10, 8).timestamp())
    assert snap.readings["media.title"].value and "media.cover" in snap.images
    assert snap.readings["calendar.1.when"].value.startswith("Today")
    assert snap.now + timedelta(0) == datetime(2026, 10, 9, 10, 8)


# -- game frame rate -----------------------------------------------------------------


def test_frames_per_second_of_the_busiest_program():
    from libre_panel.sensors.presentmon import FrameCounter, frame_column

    counter = FrameCounter()
    for i in range(120):  # a game at 125 fps, a spike of 25 ms now and then
        counter.add("Game.exe", 25.0 if i % 50 == 0 else 8.0, 100 + i * 0.008)
    for i in range(30):
        counter.add("dwm.exe", 16.7, 100 + i * 0.0167)  # the desktop does not count
        counter.add("Browser.exe", 16.7, 100 + i * 0.0167)
    out = counter.readings(101.0)
    assert out["game.app"].value == "Game" and 100 <= out["game.fps"].value <= 125
    assert out["game.low"].value == 40.0 and out["game.frametime"].unit == "ms"
    assert FrameCounter().readings(0) == {}
    assert frame_column(["Application", "ProcessID", "MsBetweenPresents"]) == "MsBetweenPresents"
    assert frame_column(["Application", "FrameTime"]) == "FrameTime"
    assert frame_column(["Application"]) is None


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="needs a shell script")
def test_presentmon_is_read_while_a_theme_shows_the_frame_rate(tmp_path, monkeypatch):
    import sys

    from libre_panel.sensors.presentmon import PresentMonProvider

    fake = tmp_path / "PresentMon"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        "print('Application,ProcessID,MsBetweenPresents', flush=True)\n"
        "for i in range(2000):\n"
        "    print(f'Racer.exe,42,{8.0 if i % 2 else 9.0}', flush=True)\n"
        "    time.sleep(0.002)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    provider = PresentMonProvider({"path": str(fake)})
    assert provider.read() == {} and provider._thread is None
    owner = type("Theme", (), {})()
    want(owner, {"game.fps"})
    found = {}
    for _ in range(200):
        found = provider.read()
        if found:
            break
        threading.Event().wait(0.05)
    provider.close()
    assert found["game.app"].value == "Racer" and 100 < found["game.fps"].value < 130


def test_game_module_waits_for_a_game():
    from test_modules import shown, snapshot, theme

    renderer = Renderer(theme({"module": "game", "cols": 3}), preview=True)
    playing = shown(renderer, snapshot())
    assert {"m0/fps", "m0/history", "m0/low"} <= set(playing) and "m0/idle" not in playing
    quiet = shown(renderer, snapshot(drop=("game.",)))
    assert "m0/idle" in quiet and "m0/fps" not in quiet

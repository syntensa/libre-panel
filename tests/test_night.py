"""[device] night: a brightness of its own at night, from night_from to night_to."""

from datetime import datetime, timedelta

import pytest

from libre_panel import app, presence
from libre_panel.config import ConfigError, DeviceConfig, brightness_now, night_now, parse_config


def at(hour, minute=0):
    return datetime(2026, 10, 20, hour, minute)


def test_a_night_over_midnight_and_one_within_the_day():
    over = DeviceConfig(night=True, night_from="22:00", night_to="07:00", brightness=60,
                        night_brightness=5)  # fmt: skip
    assert [night_now(over, at(h)) for h in (21, 22, 0, 6)] == [False, True, True, True]
    assert not night_now(over, at(7)) and night_now(over, at(6, 59))
    assert brightness_now(over, at(23)) == 5 and brightness_now(over, at(12)) == 60
    day = DeviceConfig(night=True, night_from="13:00", night_to="14:30")  # an afternoon nap
    assert night_now(day, at(14, 29)) and not night_now(day, at(14, 30))
    assert not night_now(DeviceConfig(night=False), at(23))  # off
    assert not night_now(DeviceConfig(night=True, night_from="22:00", night_to="22:00"), at(22))


def test_the_settings_are_read_and_checked():
    device = parse_config({}).device
    assert (device.night, device.night_from, device.night_to, device.night_brightness) == (
        False, "22:00", "07:00", 10)  # fmt: skip
    found = {"night": True, "night_from": "7:05", "night_to": " 6:30 "}
    device = parse_config({"device": found}).device
    assert (device.night_from, device.night_to) == ("07:05", "06:30")
    for bad in ({"night_from": "25:00"}, {"night_to": "late"}, {"night_brightness": 101},
                {"night_brightness": True}, {"night": "yes"}):  # fmt: skip
        with pytest.raises(ConfigError):
            parse_config({"device": bad})


def test_the_night_changes_nothing_that_opens_the_panel_again():
    a = DeviceConfig(brightness=60)
    b = DeviceConfig(brightness=20, night=True, night_from="21:00", night_brightness=0)
    assert app._same_device(a, b) and not app._same_device(a, DeviceConfig(port="COM9"))


class Link:
    brightness = 60

    def set_brightness(self, percent):
        self.brightness = percent


def test_the_loop_takes_the_night_s_brightness_when_it_begins_and_ends(monkeypatch):
    clock = [at(21, 59)]
    monkeypatch.setattr(app, "brightness_now",
                        lambda device, **kw: brightness_now(device, clock[0], **kw))  # fmt: skip
    loop = object.__new__(app.MainLoop)  # (only what _follow_night needs)
    loop.config = parse_config({"device": {"night": True, "brightness": 60,
                                           "night_brightness": 0}})  # fmt: skip
    loop.link, loop.night_at, loop.presence = Link(), 0.0, presence.changes()
    loop._follow_night()
    assert loop.link.brightness == 60
    clock[0], loop.night_at = at(22, 1), 0.0
    loop._follow_night()
    assert loop.link.brightness == 0  # dark at night
    clock[0] = at(7, 1)
    loop._follow_night()
    assert loop.link.brightness == 0  # (looked at twice a minute only)
    loop.night_at = 0.0
    loop._follow_night()
    assert loop.link.brightness == 60


def test_the_night_may_go_from_sunset_to_sunrise_at_the_weather_s_place():
    """At 52° north in October the sun sets near 18:00 and rises near 7:40 (local time of
    the test's machine: the place is on its meridian, so a clock in UTC finds the same)."""
    from libre_panel.sensors.sky import sun_times

    device = parse_config({"device": {"night": True, "night_from": " Sunset ",
                                      "night_to": "sunrise"}}).device  # fmt: skip
    assert (device.night_from, device.night_to) == ("sunset", "sunrise")
    place = (52.0, 0.0)
    times = sun_times(at(12).date(), *place)
    sunset, sunrise = (datetime.fromtimestamp(times[k]) for k in ("set", "rise"))
    before, after = sunset.replace(second=0) - timedelta(minutes=2), sunset + timedelta(minutes=2)
    assert not night_now(device, before, place) and night_now(device, after, place)
    assert night_now(device, sunrise - timedelta(minutes=2), place)
    assert not night_now(device, sunrise + timedelta(minutes=2), place)
    # without a place: from 19:00 to 7:00
    assert night_now(device, at(19, 1)) and not night_now(device, at(18, 59))
    assert night_now(device, at(6, 59)) and not night_now(device, at(7, 1))
    # the polar night: night all day; the midnight sun: never
    december, june = datetime(2026, 12, 21, 12), datetime(2026, 6, 21, 0, 30)
    assert night_now(device, december, (78.0, 15.0))
    assert not night_now(device, june, (78.0, 15.0))
    # only where it is asked for: times of day stay times of day
    plain = DeviceConfig(night=True, night_from="22:00", night_to="07:00")
    assert night_now(plain, at(23), (78.0, 15.0)) and not night_now(plain, at(12), (78.0, 15.0))

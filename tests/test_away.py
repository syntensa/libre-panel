"""[device] dark_when_away: the panels dim while the computer is locked or its monitor is off
(Windows tells the session window), and light up when the person is back."""

import ctypes
import uuid

import pytest
from test_night import Link

from libre_panel import app, presence, winsession
from libre_panel.config import ConfigError, DeviceConfig, brightness_now, parse_config


@pytest.fixture(autouse=True)
def nobody_away():
    presence.reset()
    yield
    presence.reset()


def test_the_person_is_away_while_one_reason_holds():
    before = presence.changes()
    presence.set_away(presence.LOCKED, True)
    presence.set_away(presence.DISPLAY_OFF, True)
    assert presence.away() and presence.changes() == before + 1  # (away once)
    presence.set_away(presence.LOCKED, False)
    assert presence.away()  # the monitor is still off
    presence.set_away(presence.DISPLAY_OFF, False)
    assert not presence.away() and presence.changes() == before + 2


def test_away_the_panel_takes_the_brightness_for_then_unless_told_not_to():
    device = DeviceConfig(brightness=60, away_brightness=0)
    assert brightness_now(device, away=True) == 0 and brightness_now(device, away=False) == 60
    assert brightness_now(DeviceConfig(brightness=60, dark_when_away=False), away=True) == 60
    presence.set_away(presence.LOCKED, True)
    assert brightness_now(DeviceConfig(brightness=60, away_brightness=15)) == 15  # as said


def test_the_settings_are_read_and_checked_for_every_panel():
    config = parse_config(
        {"device": {"away_brightness": 20}, "panels": [{"dark_when_away": False}]}
    )
    assert (config.device.dark_when_away, config.device.away_brightness) == (True, 20)
    assert config.panels[0].device.dark_when_away is False
    for bad in ({"away_brightness": 101}, {"dark_when_away": "yes"}):
        with pytest.raises(ConfigError):
            parse_config({"device": bad})
    assert app._same_device(DeviceConfig(), DeviceConfig(away_brightness=30, dark_when_away=False))


def test_the_loop_follows_the_person_at_once():
    loop = object.__new__(app.MainLoop)  # (only what _follow_night needs)
    loop.config = parse_config({"device": {"brightness": 60, "away_brightness": 5}})
    loop.link, loop.night_at, loop.presence = Link(), 0.0, presence.changes()
    loop._follow_night()
    assert loop.link.brightness == 60
    presence.set_away(presence.DISPLAY_OFF, True)
    loop._follow_night()  # not half a minute later, as the night
    assert loop.link.brightness == 5
    presence.set_away(presence.DISPLAY_OFF, False)
    loop._follow_night()
    assert loop.link.brightness == 60


def test_the_session_window_tells_locking_and_the_monitor():
    winsession.SessionEndWatcher._session(winsession.WTS_SESSION_LOCK)
    assert presence.away()
    winsession.SessionEndWatcher._session(0x5)  # (a logon: nothing to say)
    winsession.SessionEndWatcher._session(winsession.WTS_SESSION_UNLOCK)
    assert not presence.away()

    def setting(guid, state):  # a POWERBROADCAST_SETTING, as Windows hands it
        data = uuid.UUID(guid).bytes_le + (4).to_bytes(4, "little") + state.to_bytes(4, "little")
        return ctypes.create_string_buffer(data)

    watcher = object.__new__(winsession.SessionEndWatcher)  # (no window: not on Windows)
    for state, away in ((0, True), (2, False), (0, True), (1, False)):  # off, dimmed, off, on
        buffer = setting(winsession.CONSOLE_DISPLAY_STATE, state)
        watcher._setting(ctypes.addressof(buffer))
        assert presence.away() is away
    other = setting("00000000-0000-0000-0000-000000000001", 0)  # another setting: not this
    watcher._setting(ctypes.addressof(other))
    assert not presence.away()

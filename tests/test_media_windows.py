"""What is playing on Windows: the media controls by their events, the cover read when the
track changes, and the icon of the player. WinRT is faked (fake_winrt); no Windows is needed."""

import asyncio
import gc
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fake_sensors import show, wait_for
from fake_winrt import FakeSession, FakeThumbnail, FakeWinrt
from PIL import Image

from libre_panel.config import Config, ServicesConfig
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.sensors import appicon, base, media
from libre_panel.sensors.base import SensorHub


@pytest.fixture
def winrt(monkeypatch):
    """The media controls of a made-up Windows; nothing on show, quick paces."""
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    fake = FakeWinrt()
    monkeypatch.setattr(media, "_winrt", lambda: fake)
    monkeypatch.setattr(media, "_backend", media._WindowsBackend)
    monkeypatch.setattr(media, "MIN_GAP_SECONDS", 0.001)
    return fake


@pytest.fixture
def backend(winrt):
    made = media._WindowsBackend()
    yield made
    made.close()


def poll(backend, want_cover=False):
    return asyncio.run(backend.poll(want_cover))


# -- a session becomes a track ---------------------------------------------------------------


def test_a_session_becomes_a_track(winrt, backend):
    updated = datetime.now(UTC) - timedelta(seconds=5)
    winrt.manager.current = FakeSession(
        "Spotify.exe", "Song", "Band", "Album", position=60, length=200, updated=updated
    )
    track = poll(backend)
    assert (track.state, track.title, track.artist, track.album) == (
        "playing", "Song", "Band", "Album",
    )  # fmt: skip
    assert (track.position, track.duration) == (60.0, 200.0)
    assert (track.source, track.app_id) == ("Spotify", "Spotify.exe")
    assert track.at == pytest.approx(
        time.monotonic() - 5, abs=2
    )  # the position is as of the update
    assert winrt.apartments == ["mta"]  # this thread joined the multi-threaded apartment
    assert track.readings(track.at + 10)["media.position"].value == pytest.approx(70.0)


@pytest.mark.parametrize(
    ("status", "state"), [(0, "stopped"), (3, "stopped"), (4, "playing"), (5, "paused")]
)
def test_the_playback_status_is_a_state(winrt, backend, status, state):
    winrt.manager.current = FakeSession(status=status)
    assert poll(backend).state == state


def test_the_position_counts_from_the_start_of_the_timeline(winrt, backend):
    winrt.manager.current = FakeSession(start=30, position=40, length=200)
    track = poll(backend)
    assert (track.position, track.duration) == (40.0, 200.0)


def test_a_paused_position_stands_still_and_a_stream_has_none(winrt, backend):
    old = datetime.now(UTC) - timedelta(seconds=50)
    winrt.manager.current = FakeSession(status=5, position=60, updated=old)
    paused = poll(backend)
    assert paused.position == 60.0 and abs(paused.at - time.monotonic()) < 2  # not as of the update
    winrt.manager.current = FakeSession(length=0)
    live = poll(backend)
    assert (live.position, live.duration) == (None, None)


def test_nothing_plays_without_a_session_or_a_title(winrt, backend):
    assert poll(backend) is None
    winrt.manager.current = FakeSession(title="")
    assert poll(backend) is None


# -- what it listens to ------------------------------------------------------------------------


def test_it_listens_to_the_session_the_track_the_playback_and_the_position(winrt, backend):
    session = winrt.manager.current = FakeSession()
    poll(backend)
    assert winrt.manager.listening("current_session_changed") == 1
    assert session.listening("media_properties_changed") == 1
    assert session.listening("playback_info_changed") == 1
    assert session.listening("timeline_properties_changed") == 1  # (a seek)
    poll(backend)
    assert len(session.history) == 3  # and it does not listen again at every look
    assert backend.interval == media.SAFETY_SECONDS > media.POLL_SECONDS


def test_a_timeline_event_reads_the_timeline_alone_and_follows_a_seek(winrt, backend):
    """Players send a timeline event every few seconds; a seek sends one too. Only the
    timeline is read then, and the track changes only where the position jumped."""
    session = winrt.manager.current = FakeSession(position=60.0)
    first = poll(backend)
    reads = session.reads
    session.fire("timeline_properties_changed")  # the position where it was expected
    assert poll(backend) is first and backend.timeline_only
    assert session.reads == reads  # the track's properties were not asked for
    session.position, session.updated = 150.0, datetime.now(UTC)  # dragged ahead
    session.fire("timeline_properties_changed")
    moved = poll(backend)
    assert moved is not first and moved.title == first.title and moved.position == 150.0
    assert session.reads == reads
    session.fire("timeline_properties_changed")
    session.fire("playback_info_changed")  # with other news: a whole look
    poll(backend)
    assert session.reads == reads + 1 and not backend.timeline_only


def test_the_events_wake_the_reader_from_any_thread(winrt, backend):
    woken = []
    backend.bind(lambda: woken.append(threading.current_thread()))
    session = winrt.manager.current = FakeSession()
    poll(backend)
    elsewhere = threading.Thread(target=session.fire, args=("playback_info_changed",))
    elsewhere.start()
    elsewhere.join()
    winrt.manager.fire("current_session_changed")
    assert len(woken) == 2 and woken[0] is elsewhere


def test_another_player_is_listened_to_instead_of_the_first(winrt, backend):
    first = winrt.manager.current = FakeSession("Spotify.exe")
    poll(backend)
    second = winrt.manager.current = FakeSession("MSEdge", title="Video")
    winrt.manager.fire("current_session_changed")
    assert poll(backend).title == "Video"
    assert (
        first.listening("media_properties_changed") == first.listening("playback_info_changed") == 0
    )
    assert (
        second.listening("media_properties_changed")
        == second.listening("playback_info_changed")
        == 1
    )


def test_the_same_player_started_again_is_listened_to_anew(winrt, backend):
    first = winrt.manager.current = FakeSession("Spotify.exe")
    poll(backend)
    again = winrt.manager.current = FakeSession("Spotify.exe", title="After restart")
    winrt.manager.fire("current_session_changed")  # a new session object, the same player
    assert poll(backend).title == "After restart"
    assert first.listening("media_properties_changed") == 0
    assert again.listening("media_properties_changed") == 1


def test_it_lets_go_of_a_session_that_is_gone_and_of_everything_at_the_end(winrt, backend):
    session = winrt.manager.current = FakeSession()
    poll(backend)
    winrt.manager.current = None
    assert poll(backend) is None
    assert session.listening("media_properties_changed") == 0
    winrt.manager.current = session
    poll(backend)
    backend.close()
    assert (
        session.listening("media_properties_changed")
        == session.listening("playback_info_changed")
        == 0
    )
    assert winrt.manager.listening("current_session_changed") == 0


def test_a_failed_look_starts_everything_anew(winrt, backend):
    session = winrt.manager.current = FakeSession()
    poll(backend)
    session.fail_next = True
    with pytest.raises(OSError, match="hang"):
        poll(backend)
    assert (
        session.listening("media_properties_changed")
        == 0
        == winrt.manager.listening("current_session_changed")
    )
    assert poll(backend).title == "Song"
    assert winrt.requests == 2  # a manager asked for again, and listened to again
    assert session.listening("media_properties_changed") == 1


@pytest.mark.parametrize(
    ("whose", "event"),
    [("manager", "current_session_changed"), ("session", "playback_info_changed")],
)
def test_where_events_cannot_be_had_it_looks_every_second(winrt, backend, whose, event):
    session = winrt.manager.current = FakeSession()
    (winrt.manager if whose == "manager" else session).refused = {event}
    assert poll(backend).title == "Song"
    assert backend.interval == media.POLL_SECONDS < media.SAFETY_SECONDS


# -- the cover ---------------------------------------------------------------------------------


def look_again(backend, times, want_cover=True):
    async def go():
        track = None
        for _ in range(times):
            track = await backend.poll(want_cover)
        return track

    return asyncio.run(go())


def test_the_cover_is_read_when_the_track_changes_and_not_at_every_look(winrt, backend):
    one, two = FakeThumbnail(b"cover one"), FakeThumbnail(b"cover two")
    session = winrt.manager.current = FakeSession(thumbnail=one)
    track = look_again(backend, 5)
    assert track.cover == b"cover one" and one.opened == 1
    session.title, session.thumbnail = "Next song", two
    track = look_again(backend, 5)
    assert track.cover == b"cover two" and (one.opened, two.opened) == (1, 1)


def test_the_same_bytes_every_look_so_that_nothing_downstream_loads_them_again(winrt, backend):
    session = winrt.manager.current = FakeSession(thumbnail=FakeThumbnail(b"cover"))
    first = look_again(backend, 1).cover
    assert look_again(backend, 1).cover is first
    assert session.thumbnail.opened == 1


def test_no_cover_is_read_until_somebody_shows_it(winrt, backend):
    session = winrt.manager.current = FakeSession(thumbnail=FakeThumbnail(b"cover"))
    assert look_again(backend, 3, want_cover=False).cover is None
    assert session.thumbnail.opened == 0
    assert look_again(backend, 3).cover == b"cover"  # shown from now on: read once
    assert session.thumbnail.opened == 1
    assert look_again(backend, 1, want_cover=False).cover is None  # and not shown any more


def test_a_cover_that_is_shown_again_after_a_pause_is_read_again(winrt, backend):
    session = winrt.manager.current = FakeSession(thumbnail=FakeThumbnail(b"old"))
    assert look_again(backend, 1).cover == b"old"
    look_again(backend, 2, want_cover=False)  # nobody shows it for a while
    session.thumbnail = FakeThumbnail(b"new")  # the picture changed meanwhile, the track did not
    assert look_again(backend, 1).cover == b"new"


def test_a_cover_that_comes_after_the_title_is_read_when_it_is_there(winrt, backend):
    session = winrt.manager.current = FakeSession(thumbnail=None)
    assert look_again(backend, 2).cover is None
    session.thumbnail = FakeThumbnail(b"late")
    assert look_again(backend, 1).cover == b"late"


def test_the_player_saying_the_picture_changed_brings_it_again(winrt, backend):
    old = FakeThumbnail(b"old")
    session = winrt.manager.current = FakeSession(thumbnail=old)
    assert look_again(backend, 1).cover == b"old"
    session.thumbnail = FakeThumbnail(b"new")  # same title and artist
    assert look_again(backend, 1).cover == b"old"  # nothing said so
    session.fire("media_properties_changed")
    assert look_again(backend, 1).cover == b"new"
    assert look_again(backend, 3).cover == b"new" and session.thumbnail.opened == 1


def test_a_cover_that_cannot_be_read_is_not_asked_for_at_every_look(winrt, backend, monkeypatch):
    broken = FakeThumbnail(b"", fails=True)
    winrt.manager.current = FakeSession(thumbnail=broken)
    assert look_again(backend, 4).cover is None
    assert broken.opened == 1
    monkeypatch.setattr(media, "COVER_RETRY_SECONDS", 0.0)
    broken.fails = False
    broken.data = b"now it works"
    assert look_again(backend, 1).cover == b"now it works" and broken.opened == 2


# -- the provider: news bring the look ---------------------------------------------------------


@pytest.fixture
def provider(winrt, monkeypatch):
    # only news can bring the next look in these tests: the safety poll is far away
    monkeypatch.setattr(media, "SAFETY_SECONDS", 30.0)
    monkeypatch.setattr(media, "SETTLE_SECONDS", 0.05)
    made = media.MediaProvider()
    yield made
    made.close()


def title_of(provider):
    found = provider.read().get("media.title")
    return found.value if found else None


def test_a_new_track_is_seen_at_once_not_at_the_next_look(winrt, provider):
    theme = show("media.title")
    session = winrt.manager.current = FakeSession(title="One")
    assert provider.read() == {} or True  # starts the thread
    assert wait_for(lambda: title_of(provider) == "One")
    assert wait_for(lambda: session.listening("media_properties_changed") == 1)
    session.title = "Two"
    session.fire("media_properties_changed")
    assert wait_for(lambda: title_of(provider) == "Two")  # the safety poll is 30 s away
    assert theme is not None


def test_a_pause_is_seen_at_once(winrt, provider):
    theme = show("media.state")
    session = winrt.manager.current = FakeSession(status=4)
    provider.read()
    assert wait_for(lambda: provider.read().get("media.state") is not None)
    assert provider.read()["media.state"].value == "Playing"
    session.status = 5
    session.fire("playback_info_changed")
    assert wait_for(lambda: provider.read()["media.state"].value == "Paused")
    assert theme is not None


def test_after_news_it_looks_once_more_a_moment_later_and_then_rests(winrt, provider):
    """The cover and the position come in a moment after the title."""
    theme = show("media.title")
    session = winrt.manager.current = FakeSession()
    provider.read()
    assert wait_for(lambda: session.reads == 1)  # the first look: no news, no second one
    threading.Event().wait(0.15)
    assert session.reads == 1
    session.fire("media_properties_changed")
    assert wait_for(lambda: session.reads == 3)  # at once, and once more after the settle time
    threading.Event().wait(0.2)
    assert session.reads == 3  # then the safety poll, 30 s away
    assert theme is not None


def test_the_cover_follows_the_track_to_the_picture_of_the_provider(winrt, provider):
    theme = show("media.title", "media.cover")
    cover = Image.new("RGB", (40, 40), "red")
    import io

    data = io.BytesIO()
    cover.save(data, "PNG")
    session = winrt.manager.current = FakeSession(thumbnail=FakeThumbnail(data.getvalue()))
    provider.read()
    assert wait_for(lambda: "media.cover" in provider.images())
    picture = provider.images()["media.cover"]
    assert picture.size == (40, 40) and picture.mode == "RGBA"
    assert provider.images()["media.cover"] is picture  # the same object while the track plays
    session.thumbnail = None
    session.title = "No cover here"
    session.fire("media_properties_changed")
    assert wait_for(lambda: "media.cover" not in provider.images())
    assert theme is not None


def test_the_icon_of_the_player_is_found_once_and_is_a_picture_of_its_own(winrt, provider):
    theme = show("media.title", "media.app_icon")
    icon = Image.new("RGBA", (96, 96), "red")
    asked = []
    provider._icons = appicon.AppIcons(lambda app: asked.append(app) or icon)
    session = winrt.manager.current = FakeSession("Spotify.exe")
    provider.read()
    assert wait_for(lambda: "media.app_icon" in provider.images())
    session.title = "Two"
    session.fire("media_properties_changed")
    assert wait_for(lambda: title_of(provider) == "Two")
    assert provider.images()["media.app_icon"] is icon
    assert asked == ["Spotify.exe"]  # per player, not per track
    assert theme is not None


def test_the_icon_is_not_looked_for_while_nobody_reads_it(winrt, provider):
    theme = show("media.title")
    asked = []
    provider._icons = appicon.AppIcons(lambda app: asked.append(app))
    winrt.manager.current = FakeSession()
    provider.read()
    assert wait_for(lambda: title_of(provider) == "Song")
    assert asked == [] and "media.app_icon" not in provider.images()
    assert theme is not None


def test_the_notices_service_puts_the_cover_and_the_icon_on_a_new_track(winrt, provider):
    """Through the hub and the sample signal, as in the app: media.* is asked for by the
    service alone, and what the source brings reaches the message."""
    import io

    picture = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(picture, "PNG")
    icon = Image.new("RGBA", (96, 96), "blue")
    provider._icons = appicon.AppIcons(lambda app: icon)
    session = winrt.manager.current = FakeSession(
        title="One", artist="Band", thumbnail=FakeThumbnail(picture.getvalue())
    )
    host = PluginHost()
    manager = ServiceManager(host)
    options = {"volume": False, "restarted": False}
    manager.apply(Config(services=ServicesConfig(["notices"], {"notices": options})))
    hub = SensorHub([provider])
    try:
        assert manager.state == {"notices": "running"}
        hub.snapshot()  # starts the source: the service wants media.*
        assert wait_for(lambda: "media.cover" in hub.snapshot().images)
        host.emit("sample", wait=True, snapshot=hub.snapshot())
        assert host.take_toasts() == []  # what plays when the service starts is no news
        session.title = "Two"
        session.fire("media_properties_changed")
        assert wait_for(lambda: hub.snapshot().value("media.title") == "Two")
        host.emit("sample", wait=True, snapshot=hub.snapshot())
        [toast] = host.take_toasts()
        assert toast.text == "Two – Band" and toast.kind == "music"
        assert toast.payload["badge"] is icon  # the player's icon, as found once
        assert toast.payload["image"].size == (40, 40)  # and the cover
    finally:
        manager.stop()
        host.close()
        hub.close()


def test_close_ends_the_thread_and_lets_go_of_windows_events(winrt, provider):
    theme = show("media.title")
    session = winrt.manager.current = FakeSession()
    provider.read()
    assert wait_for(lambda: session.listening("playback_info_changed") == 1)
    thread = provider._thread
    began = time.monotonic()
    provider.close()
    assert not thread.is_alive() and time.monotonic() - began < 5  # at once, not at the safety poll
    assert session.listening("playback_info_changed") == 0
    assert winrt.manager.listening("current_session_changed") == 0
    assert provider.read() == {}  # and it stays closed
    assert theme is not None


def test_nobody_looking_for_a_minute_ends_the_thread_and_the_events(winrt, provider, monkeypatch):
    monkeypatch.setattr(media, "IDLE_SECONDS", 0.05)
    monkeypatch.setattr(media, "IDLE_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(media, "SAFETY_SECONDS", 0.05)  # the wait that was under way is short
    session = winrt.manager.current = FakeSession()
    theme = show("media.title")
    provider.read()
    assert wait_for(lambda: title_of(provider) == "Song")
    del theme
    gc.collect()
    thread = provider._thread
    assert wait_for(lambda: not thread.is_alive())
    assert session.listening("playback_info_changed") == 0
    assert provider.read() == {}  # nothing is shown now
    theme = show("media.title")  # and somebody looks again: it starts again
    provider.read()
    assert wait_for(lambda: title_of(provider) == "Song")
    assert theme is not None


def test_a_look_that_fails_is_no_reason_to_stop(winrt, provider, monkeypatch):
    monkeypatch.setattr(media, "POLL_SECONDS", 0.05)  # after a failure it looks again soon
    theme = show("media.title")
    session = winrt.manager.current = FakeSession()
    session.fail_next = True
    provider.read()
    assert wait_for(lambda: title_of(provider) == "Song")  # not 30 s later, at the safety poll
    assert session.reads == 2 and session.listening("playback_info_changed") == 1
    assert theme is not None


# -- Linux keeps its players ---------------------------------------------------------------------


def test_the_players_of_linux_come_with_the_name_of_their_app():
    sep = "\x1f"
    line = sep.join(["Playing", "Band", "Song", "Album", "1000000", "200000000", "", "Spotify"])
    assert media.parse_playerctl(line, 0).app_id == "spotify"


def test_each_system_has_its_backend(winrt, monkeypatch):
    monkeypatch.undo()  # the real choice; WinRT stays made up for Windows
    monkeypatch.setattr(media, "_winrt", lambda: winrt)
    monkeypatch.setattr(media.sys, "platform", "win32")
    assert isinstance(media._backend(), media._WindowsBackend)
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr(media.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert isinstance(media._backend(), media._PlayerctlBackend)
    # no playerctl (CachyOS has none by default, #33): straight from D-Bus with busctl
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/busctl"
                        if name == "busctl" else None)  # fmt: skip
    assert isinstance(media._backend(), media._BusctlBackend)
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="neither playerctl nor busctl"):
        media._backend()


# -- a new cover with its title, in the frame that brings it ---------------------------------------


class Player(base.SensorProvider):
    """A source that follows on every frame, as the media source does, with its cover."""

    name = "player"
    every_frame = True

    def __init__(self):
        super().__init__({})
        self.title, self.cover = "Song A", Image.new("RGB", (4, 4), "red")

    def read(self):
        return {"media.title": base.Reading("media.title", self.title)}

    def images(self):
        return {"media.cover": self.cover} if self.cover is not None else {}


def test_the_hub_hands_on_the_pictures_of_the_sources_that_follow_every_frame():
    player = Player()
    hub = SensorHub([player])
    assert hub.fresh_images() == {"media.cover": player.cover}
    player.cover = None  # a track without one: gone, not the old one
    assert hub.fresh_images() == {"media.cover": None}
    assert hub.fresh_images() == {}


def test_the_panel_shows_the_new_cover_in_the_frame_of_the_new_title(isolated_home):
    """The cover came with the next sample, up to a second after its title."""
    from test_default_theme_loop import Reporting, demo_config

    from libre_panel import app as app_module

    player = Player()
    clock = SimpleClock(1000.0)
    loop = app_module.MainLoop(demo_config(theme="encore"), hub=SensorHub([player]),
                               display=Reporting(), clock=clock)  # fmt: skip
    try:
        loop.step(clock.now)  # a sample
        assert loop.snapshot.images["media.cover"] is player.cover
        player.title, player.cover = "Song B", Image.new("RGB", (4, 4), "blue")
        clock.now += 0.02
        loop.step(clock.now)  # a frame between two samples
        snapshot = loop.snapshot
        assert snapshot.readings["media.title"].value == "Song B"
        assert snapshot.images["media.cover"] is player.cover
    finally:
        loop.close()


class SimpleClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_each_change_is_one_line_in_the_log_with_what_brought_it(winrt, provider, caplog):
    """As SPUR II logged them (with --verbose): the change, its event, and how long after it
    the source had it; the chain to the panel is proved so without a camera."""
    import logging

    caplog.set_level(logging.DEBUG, logger="libre_panel.sensors.media")
    theme = show("media.title")
    session = winrt.manager.current = FakeSession()
    provider.read()
    assert wait_for(lambda: provider.read().get("media.state"))
    session.status = 5  # PAUSED
    session.fire("playback_info_changed")
    assert wait_for(lambda: provider.read()["media.state"].value == "Paused")
    assert wait_for(lambda: any("by playback_info_changed" in r.getMessage()
                                for r in caplog.records))  # fmt: skip
    line = next(r.getMessage() for r in caplog.records if "playback_info" in r.getMessage())
    assert line.startswith("media: paused, by playback_info_changed") and "ms after it" in line
    assert theme is not None

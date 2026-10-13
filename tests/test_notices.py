"""The notices service, through the host and the service manager: what is worth
a message, and the toast each one becomes."""

import tomllib
from datetime import datetime

import pytest
from fake_sensors import wait_for
from PIL import Image, ImageChops

from libre_panel import i18n
from libre_panel.config import Config, ServicesConfig, parse_config
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.render.overlays import ToastLayer
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot, wanted
from libre_panel.theme.model import parse_theme


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Desk:
    """A host with the notices service running, a clock by hand, and samples."""

    def __init__(self, config):
        self.host = PluginHost()
        self.manager = ServiceManager(self.host)  # the registry: Libre Panel's own services
        self.clock = Clock()
        self.manager.apply(config)
        assert self.manager.state == {"notices": "running"}, self.manager.state
        self.service = self.manager.running["notices"][0]
        self.service.clock = self.clock
        self.service._began = self.clock()

    def sample(self, readings=(), images=None, seconds=1.0):
        """Time passes, a snapshot is taken; the toasts the service sent for it."""
        self.clock.now += seconds
        snapshot = Snapshot(readings={r.key: r for r in readings}, images=images or {})
        self.host.emit("sample", wait=True, snapshot=snapshot)
        return self.host.take_toasts()

    def fresh(self, readings, keys=None, seconds=0.02):
        """A frame between two samples brings these readings; the toasts the service sent."""
        self.clock.now += seconds
        snapshot = Snapshot(readings={r.key: r for r in readings})
        found = frozenset(r.key for r in readings) if keys is None else frozenset(keys)
        self.host.emit("fresh", wait=True, snapshot=snapshot, keys=found)
        return self.host.take_toasts()

    def close(self):
        self.manager.stop()
        self.host.close()


@pytest.fixture
def desk():
    made = []

    def make(**options):
        options = {"volume": False, "music": False, "restarted": False, **options}
        config = Config(services=ServicesConfig(["notices"], {"notices": options}))
        made.append(Desk(config))
        return made[-1]

    yield make
    for each in made:
        each.close()


def volume(level, muted=False):
    return [
        Reading("audio.volume", 0.0 if muted else level, "%", "Volume"),
        Reading("audio.muted", 1 if muted else 0),
    ]


def playing(title, artist="Artist", on=1, progress=None):
    found = {
        "media.title": title,
        "media.state": "Playing" if on else "Paused",
        "media.playing": on,
    }
    if artist:
        found["media.artist"] = artist
    if progress is not None:
        found["media.progress"] = progress
    return [Reading(key, value) for key, value in found.items()]


def temperature(value, key="cpu.temp"):
    return [Reading(key, value, "°C", "CPU temperature")]


# -- the volume -----------------------------------------------------------------------------------


def test_the_volume_is_told_when_it_changes_by_a_step(desk):
    d = desk(volume=True)
    assert d.sample(volume(30)) == []  # the first value is no change
    assert d.sample(volume(30.4)) == []  # less than a step
    [toast] = d.sample(volume(34))
    assert (toast.kind, toast.key, toast.rank, toast.level) == ("volume", "volume", 0, "info")
    assert toast.text == "34" and toast.seconds is None  # the theme's hold time
    assert toast.payload == {"label": "Volume", "progress": pytest.approx(0.34), "muted": False}
    assert toast.service == "notices"
    [toast] = d.sample(volume(35))  # one more
    assert toast.text == "35"
    assert d.sample(volume(35)) == []


def test_muting_and_unmuting_are_told(desk):
    d = desk(volume=True)
    d.sample(volume(35))
    [muted] = d.sample(volume(35, muted=True))  # the source reports 0 while muted
    assert muted.text == "Muted" and muted.payload["progress"] == 0.0 and muted.key == "volume"
    assert d.sample(volume(35, muted=True)) == []
    [back] = d.sample(volume(35))
    assert back.text == "35" and back.payload["progress"] == pytest.approx(0.35)


def test_the_volume_is_told_when_it_moves_between_two_samples(desk):
    """The key is turned between readings: the message comes with that frame, and a sample
    that finds the same volume afterwards says nothing more."""
    d = desk(volume=True)
    assert d.sample(volume(30)) == []
    [toast] = d.fresh(volume(36))
    assert (toast.kind, toast.key, toast.text) == ("volume", "volume", "36")
    assert toast.payload["progress"] == pytest.approx(0.36)
    assert d.sample(volume(36)) == []  # told already
    [muted] = d.fresh(volume(36, muted=True))
    assert muted.text == "Muted"
    assert d.sample(volume(36, muted=True)) == []
    [back] = d.fresh(volume(36))
    assert back.text == "36"


def test_only_a_change_of_the_volume_is_looked_at_between_samples(desk):
    d = desk(volume=True, music=True, warnings=[{"key": "cpu.temp", "crit": 90}])
    d.sample(volume(30) + temperature(50))
    # the load moves ten times a second, and the volume is not among what moved
    assert d.fresh([Reading("cpu.load", 40.0, "%")] + volume(60), keys={"cpu.load"}) == []
    assert d.fresh(temperature(95), keys={"cpu.temp"}) == []  # warnings wait for the sample
    assert [t.text for t in d.fresh(volume(60), keys={"audio.volume"})] == ["60"]


def test_a_slow_turn_adds_up_to_a_step_whatever_the_rate_of_the_readings(desk):
    """Looked at on every frame, the volume moves a fraction of a step at a time: the
    message comes when it has moved a step from the one told, not from the frame before."""
    d = desk(volume=True)
    d.sample(volume(30))
    assert d.fresh(volume(30.4)) == [] and d.fresh(volume(30.8)) == []
    [toast] = d.fresh(volume(31.2))
    assert toast.text == "31"
    assert d.fresh(volume(31.5)) == [] and [t.text for t in d.fresh(volume(32.3))] == ["32"]


def test_volume_changes_between_samples_are_not_looked_at_when_the_volume_is_off(desk):
    d = desk(volume=False, music=True)
    assert d.fresh(volume(30)) == [] and d.fresh(volume(60)) == []
    quiet = desk(volume=False, music=False)
    assert "fresh" not in quiet.host._listeners or not quiet.host._listeners["fresh"]


def test_a_stopped_service_does_not_tell_a_volume_that_moved(desk):
    d = desk(volume=True)
    d.sample(volume(30))
    d.service.stop()
    assert d.fresh(volume(50)) == []


def test_without_a_volume_source_there_is_nothing_to_tell_and_one_hint(desk, caplog):
    d = desk(volume=True)
    with caplog.at_level("INFO", logger="libre_panel.service.notices"):
        assert d.sample([], seconds=10) == [] and "audio" not in caplog.text
        d.sample([], seconds=25)  # past half a minute without a reading
        d.sample([], seconds=25)
    assert caplog.text.count('add "audio" to [sensors] providers') == 1
    assert d.sample(volume(30)) == [] and d.sample(volume(40))[0].text == "40"  # it may come later


def test_notices_follow_the_language(desk):
    i18n.set_language("de")
    d = desk(volume=True, restarted=True)
    d.service.wall = lambda: datetime(2026, 10, 10, 21, 5)
    d.sample(volume(30))
    [toast] = d.sample(volume(35, muted=True))
    assert (toast.text, toast.payload["label"]) == ("Stumm", "Lautstärke")
    d.host.emit("panel-restarted", wait=True)
    [restart] = d.host.take_toasts()
    assert (restart.text, restart.payload["label"]) == ("Panel neu gestartet 21:05", "Panel")


def test_the_volume_message_comes_within_a_frame_of_the_key_not_with_the_next_reading():
    """Through the real main loop: a volume source that follows on every frame, a theme that
    takes a reading once a minute. The message is sent from the frame the volume moved in."""
    from test_app_cli import MemoryStream, StepClock

    from libre_panel import app
    from libre_panel.sensors.base import SensorHub, SensorProvider

    class Audio(SensorProvider):
        name = "audio"
        every_frame = True
        level = 30.0

        def read(self):
            return {k: r for r in volume(self.level) for k in (r.key,)}

    clock, audio, host = StepClock(1000.0), Audio(), PluginHost()
    config = Config(theme="pico", services=ServicesConfig(["notices"], {"notices": {
        "music": False, "restarted": False}}))  # fmt: skip
    config.refresh_ms = 60_000
    manager = ServiceManager(host)
    manager.apply(config)
    loop = app.MainLoop(config, host=host, hub=SensorHub([audio]), display=MemoryStream(),
                        clock=clock)  # fmt: skip
    try:
        assert manager.state == {"notices": "running"}
        for _ in range(5):  # the first frame is the one sample; the others see no change
            clock.now += 0.02
            loop.step(clock.now)
        assert wait_for(lambda: manager.running["notices"][0]._volume is not None)
        assert host.take_toasts() == []  # the first value is no change
        audio.level = 38.0
        clock.now += 0.02
        loop.step(clock.now)  # the frame the volume shows in...
        assert wait_for(lambda: host.latest.value("audio.volume") == 38.0)
        assert wait_for(lambda: len(host._toasts) == 1)  # ...sends it
        assert [(t.kind, t.text) for t in host._toasts] == [("volume", "38")]
        clock.now += 0.02
        loop.step(clock.now)  # and the next frame has it on the panel
        assert loop.toasts.active and not host._toasts
    finally:
        loop.close()
        manager.stop()
        host.close()


# -- what plays -----------------------------------------------------------------------------------


def test_a_new_track_is_told_with_its_cover_and_the_players_icon(desk):
    d = desk(music=True)
    cover, icon = Image.new("RGB", (4, 4), "red"), Image.new("RGBA", (4, 4), "blue")
    images = {"media.cover": cover, "media.app_icon": icon}
    assert d.sample(playing("Song A"), images) == []  # what plays when the service starts
    assert d.sample(playing("Song A"), images) == []
    [toast] = d.sample(playing("Song B", progress=0.02), images)
    assert (toast.kind, toast.key, toast.rank, toast.seconds) == ("music", "music", 1, 5.0)
    assert (toast.text, toast.icon, toast.level) == ("Song B – Artist", "music", "info")
    assert toast.payload["label"] == "Now playing" and toast.payload["progress"] == 0.02
    assert toast.payload["image"] is cover and toast.payload["badge"] is icon


def test_a_new_track_is_told_in_the_frame_it_comes_not_with_the_next_sample(desk):
    """The media source knows a new track within 50 ms; the message waited for the next
    sample, up to a second. It is told on the frame that brings it now, and once."""
    d = desk(music=True)
    d.sample(playing("Song A"))
    [toast] = d.fresh(playing("Song B"))
    assert toast.text == "Song B – Artist"
    assert d.fresh(playing("Song B")) == [] and d.sample(playing("Song B")) == []
    assert d.fresh(playing("Song B", on=0)) == []  # a pause between samples says nothing


def test_a_track_without_an_artist_or_pictures(desk):
    d = desk(music=True)
    d.sample(playing("Song A"))
    [toast] = d.sample(playing("Song B", artist=None))
    assert toast.text == "Song B" and set(toast.payload) == {"label"}


def test_pausing_and_resuming_say_nothing_but_a_new_track_does(desk):
    d = desk(music=True)
    d.sample(playing("Song A"))
    assert d.sample(playing("Song A", on=0)) == []  # paused
    assert d.sample(playing("Song A")) == []  # resumed: the same track
    assert d.sample(playing("Song B", on=0)) == []  # skipped while paused
    [toast] = d.sample(playing("Song B"))  # told when it plays
    assert toast.text == "Song B – Artist"
    assert d.sample([]) == []  # nothing plays any more


def test_a_cover_that_comes_late_joins_the_message_in_place(desk):
    d = desk(music=True)
    d.sample(playing("Song A"))
    [first] = d.sample(playing("Song B"))  # no cover yet
    assert "image" not in first.payload
    cover = Image.new("RGB", (4, 4))
    [late] = d.sample(playing("Song B"), {"media.cover": cover}, seconds=1.5)
    assert late.key == first.key and late.text == first.text and late.payload["image"] is cover
    assert d.sample(playing("Song B"), {"media.cover": cover}) == []  # told once more, not again


def test_a_cover_that_comes_much_too_late_is_not_told(desk):
    d = desk(music=True)
    d.sample(playing("Song A"))
    d.sample(playing("Song B"))
    assert d.sample(playing("Song B"), {"media.cover": Image.new("RGB", (4, 4))}, seconds=5) == []


# -- sensors past a limit -------------------------------------------------------------------------


def test_a_sensor_past_a_limit_is_told_once_until_it_is_armed_again(desk):
    d = desk(warnings=[{"key": "cpu.temp", "warn": 80, "crit": 90}], quiet_s=120)
    assert d.sample(temperature(70)) == []
    [warn] = d.sample(temperature(82))
    assert (warn.kind, warn.key, warn.rank, warn.level) == (
        "warning",
        "warning:cpu.temp",
        2,
        "warning",
    )
    assert (warn.text, warn.icon, warn.payload) == (
        "CPU temperature 82 °C",
        "cpu",
        {"label": "Warning"},
    )
    assert d.sample(temperature(85)) == []  # the same level
    [crit] = d.sample(temperature(93))  # worse: told also in the quiet time
    assert crit.level == "error" and crit.text == "CPU temperature 93 °C"
    assert d.sample(temperature(95)) == [] and d.sample(temperature(85)) == []
    assert d.sample(temperature(79)) == []  # below warn: armed again
    assert d.sample(temperature(95)) == []  # but quiet: told 3 s ago
    d.clock.now += 200
    assert d.sample(temperature(60)) == []
    [again] = d.sample(temperature(95))  # the quiet time is over
    assert again.level == "error"


def test_a_limit_with_only_crit_and_a_bar_for_percentages(desk):
    d = desk(warnings=[{"key": "ram.load", "crit": 90, "name": "Memory"}])
    assert d.sample([Reading("ram.load", 80.0, "%")]) == []
    [toast] = d.sample([Reading("ram.load", 91.0, "%")])
    assert toast.text == "Memory 91 %" and toast.level == "error"
    assert toast.payload["progress"] == pytest.approx(0.91) and toast.icon == "ram"
    assert d.sample([Reading("ram.load", 89.9, "%")]) == []  # below crit: armed again
    assert d.sample([Reading("ram.load", 95.0, "%")]) == []  # quiet


def test_limits_have_their_own_scale_icon_and_name(desk):
    d = desk(
        warnings=[
            {"key": "disk.1.temp", "crit": 60, "max": 80, "icon": "network"},
            {"key": "x.speed", "crit": 5},
            {"key": "fan.rear", "crit": 1000, "icon": "no-such-icon"},
        ]
    )
    readings = [Reading("disk.1.temp", 70.0, "°C"), Reading("x.speed", 6.25, "m/s")]
    toasts = {t.key: t for t in d.sample(readings + [Reading("fan.rear", 1200.0, "RPM")])}
    disk, speed = toasts["warning:disk.1.temp"], toasts["warning:x.speed"]
    assert disk.payload["progress"] == pytest.approx(70 / 80) and disk.icon == "network"
    assert disk.text == "disk.1.temp 70 °C"  # no label: the key
    assert speed.text == "x.speed 6.2 m/s" and "progress" not in speed.payload
    assert speed.icon is None
    assert toasts["warning:fan.rear"].icon == "no-such-icon"  # a style ignores what it lacks


def test_a_value_that_is_there_when_the_service_starts_is_told(desk):
    d = desk(warnings=[{"key": "cpu.temp", "warn": 80}])
    [toast] = d.sample(temperature(95))
    assert toast.level == "warning"


def test_missing_and_broken_readings_change_nothing(desk):
    d = desk(warnings=[{"key": "cpu.temp", "warn": 80}])
    d.sample(temperature(90))
    assert d.sample([]) == []  # gone: the key stays told
    assert d.sample([Reading("cpu.temp", None), Reading("cpu.temp2", "hot")]) == []
    assert d.sample(temperature(90)) == []


def test_wrong_entries_are_reported_and_left_out(desk, caplog):
    with caplog.at_level("WARNING", logger="libre_panel.service.notices"):
        d = desk(
            warnings=[
                "cpu.temp",
                {"warn": 80},
                {"key": "a", "warn": "high"},
                {"key": "b"},
                {"key": "c", "warn": 90, "crit": 80},
                {"key": "fine", "crit": 1},
            ]
        )
    assert [limit.key for limit in d.service.limits] == ["fine"]
    for message in ("warnings[1]", "warnings[2]", "warnings[3].warn", "warnings[4]", "warnings[5]"):
        assert message in caplog.text


# -- the panel ------------------------------------------------------------------------------------


def test_a_restarted_panel_is_told(desk):
    d = desk(restarted=True)
    d.service.wall = lambda: datetime(2026, 10, 10, 9, 7)
    d.host.emit("panel-restarted", wait=True)
    [toast] = d.host.take_toasts()
    assert (toast.text, toast.kind, toast.rank, toast.seconds) == (
        "Panel restarted 09:07",
        "panel",
        2,
        8.0,
    )
    assert toast.payload == {"label": "Panel"}


def test_a_sample_that_comes_after_the_service_stopped_says_nothing(desk):
    d = desk(volume=True, restarted=True)
    d.sample(volume(30))
    d.service.stop()
    assert d.sample(volume(50)) == []
    d.host.emit("panel-restarted", wait=True)
    assert d.host.take_toasts() == []


def test_a_kind_that_is_switched_off_is_not_told(desk):
    d = desk(volume=False, restarted=False, music=True)
    d.host.emit("panel-restarted", wait=True)  # nobody listens
    assert d.sample(volume(30)) == [] and d.sample(volume(60)) == []
    d.sample(playing("Song A"))
    assert [t.kind for t in d.sample(playing("Song B"))] == ["music"]  # what is on still is


# -- what the service asks for and how it is switched on ------------------------------------------


def test_the_service_asks_for_the_readings_it_reads(desk):
    d = desk(volume=True, music=True, warnings=[{"key": "zz.notices.key", "warn": 1}])
    assert wanted("audio.volume") and wanted("media.cover") and wanted("zz.notices.key")
    d.manager.stop()
    assert not wanted("zz.notices.key")


def test_notices_come_from_the_config():
    text = """
[services]
enabled = ["notices"]

[services.notices]
volume = false
quiet_s = 60

[[services.notices.warnings]]
key = "cpu.temp"
warn = 80
crit = 90
"""
    config = parse_config(tomllib.loads(text))
    host = PluginHost()
    manager = ServiceManager(host)
    manager.apply(config)
    try:
        service = manager.running["notices"][0]
        assert service.options["volume"] is False and service.options["quiet_s"] == 60
        assert [(x.key, x.warn, x.crit) for x in service.limits] == [("cpu.temp", 80, 90)]
    finally:
        manager.stop()
        host.close()


# -- the toasts, drawn ----------------------------------------------------------------------------


def layer(**toast):
    data = {
        "format": "libre-panel-theme/1",
        "name": "notices",
        "display": {"model": "custom", "width": 480, "height": 320},
        "background": {"color": "#000000"},
        "palette": {"accent": "#00ffff", "bg": "#000000"},
        "widgets": [],
        "toast": toast,
    }
    return ToastLayer(Renderer(parse_theme(data)))


@pytest.mark.parametrize("style", ["card", "band"])
def test_the_theme_decides_what_the_notices_look_like_and_what_shows(desk, style):
    d = desk(volume=True, music=True, warnings=[{"key": "cpu.temp", "crit": 90}])
    cover, icon = Image.new("RGB", (8, 8), (255, 128, 0)), Image.new("RGBA", (8, 8), "blue")
    d.sample(volume(30) + playing("Song A"))
    toasts = d.sample(
        volume(40) + playing("Song B", progress=0.1) + temperature(95),
        {"media.cover": cover, "media.app_icon": icon},
    )
    assert {t.kind for t in toasts} == {"volume", "music", "warning"}
    shown = layer(style=style, anchor="bottom", off=["music"], queue=False)
    shown.add(toasts)
    frame = Image.new("RGB", (480, 320), (10, 20, 30))
    shown.advance(0.0)
    assert shown.current[0].kind == "warning"  # the highest rank; music is switched off
    assert shown.queue == []
    assert ImageChops.difference(shown.apply(frame, 1.0), frame).getbbox() is not None
    quiet = layer(style=style, off=["music", "volume", "warning"])
    quiet.add(toasts)
    quiet.advance(0.0)
    assert quiet.current is None


def test_a_track_with_its_cover_and_icon_is_drawn_in_the_band(desk):
    d = desk(music=True)
    cover, icon = Image.new("RGB", (8, 8), (255, 128, 0)), Image.new("RGBA", (8, 8), "blue")
    d.sample(playing("Song A"))
    [toast] = d.sample(
        playing("Song B", progress=0.5), {"media.cover": cover, "media.app_icon": icon}
    )
    shown = layer(style="band", anchor="bottom", seconds=2, queue=False)
    shown.add([toast])
    shown.advance(0.0)
    frame = shown.apply(Image.new("RGB", (480, 320)), 1.0)
    assert (255, 128, 0) in {frame.getpixel((x, y)) for x in range(10, 90) for y in range(200, 300)}


def test_the_volume_is_told_in_the_look_chosen_for_every_screen(desk):
    """``volume_look`` goes with the message, so it shows so on every screen; a look that does
    not exist is said in the log and the theme's notices draw the volume."""
    d = desk(volume=True, volume_look="ring")
    d.sample(volume(30))
    [toast] = d.sample(volume(40))
    assert toast.payload["look"] == "ring" and toast.payload["muted"] is False
    [toast] = d.sample(volume(40, muted=True))
    assert toast.payload == {"label": "Volume", "progress": 0.0, "muted": True, "look": "ring"}
    other = desk(volume=True, volume_look="sparkles")
    assert other.service.look == ""
    other.sample(volume(30))
    [toast] = other.sample(volume(40))
    assert "look" not in toast.payload

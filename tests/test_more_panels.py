"""More panels at once ([[panels]]): each beside the first in a loop of its own, on the first
panel's readings, never on the panel another loop has open."""

import re
import threading
import time

import pytest
from fake_serial import FakeRevA
from test_serial import display, picture, ports  # noqa: F401 - the fixture
from test_service import wait_for

from libre_panel import app
from libre_panel.config import ConfigError, parse_config
from libre_panel.devices import serial_link
from libre_panel.devices.base import DeviceError, place_taken
from libre_panel.sensors.base import HubView, Reading, SensorHub, SensorProvider


def test_more_panels_are_read_with_the_keys_of_device_and_a_theme():
    config = parse_config(
        {
            "theme": "studio",
            "device": {"model": "turing-12.3-usb", "brightness": 70},
            "video": {"mode": "on", "local_clip": "usr/data/clip.h264"},
            "sequence": {"steps": [{"theme": "lumen", "seconds": 30}]},
            "panels": [
                {"theme": "lumen", "model": "turing-3.5", "port": " COM5 ", "brightness": 40},
                {"on": False},
            ],
        }
    )
    first, second = config.panels
    assert (first.theme, first.on, first.device.model) == ("lumen", True, "turing-3.5")
    assert (first.device.port, first.device.brightness) == ("COM5", 40)
    # found as a panel, never drawn into a file meanwhile; a file of its own where it is
    assert first.device.driver == "turzx" and first.device.output == "libre-panel-frame-2.png"
    assert (second.on, second.theme, second.device.output) == (False, "", "libre-panel-frame-3.png")

    own = config.for_panel(0)
    assert own.device.model == "turing-3.5"
    # smooth motion is the first panel's: its clip's name goes to no other panel
    assert own.device.video.mode == "off" and config.device.video.mode == "on"
    assert (own.theme, own.theme_given) == ("lumen", True)
    assert not own.sequence.active and own.panels == []  # no screens in turn, no more panels
    assert config.sequence.active and config.device.brightness == 70  # the first's as they were
    assert config.for_panel(1).theme_given is False  # no theme: the panel's own


@pytest.mark.parametrize(
    "panels, said",
    [
        ([{"brightness": 101}], "panels[0].brightness"),
        ([{}, {"rotate": 90}], "panels[1].rotate"),
        ([{"theme": 3}], "panels[0].theme"),
        ([{}] * 4, "at most 3"),
        ({"theme": "lumen"}, "panels"),
    ],
)
def test_a_wrong_panel_is_said_by_its_place(panels, said):
    with pytest.raises(ConfigError, match=re.escape(said)):
        parse_config({"panels": panels})


def test_a_loop_for_a_panel_follows_its_own_table():
    config = parse_config({"panels": [{"theme": "lumen"}, {"on": False}]})
    assert app.panel_config(config, None) is config
    assert app.panel_config(config, 0).theme == "lumen"
    for gone in (1, 2):  # off, or taken out
        with pytest.raises(ConfigError, match=f"panels\\[{gone}\\]"):
            app.panel_config(config, gone)


class Counting(SensorProvider):
    name = "counting"

    def __init__(self):
        super().__init__()
        self.reads = 0

    def read(self):
        self.reads += 1
        return {"cpu.temp": Reading("cpu.temp", 40.0 + self.reads, "°C")}


def test_another_panel_takes_the_readings_the_first_took_and_keeps_its_own_history(monkeypatch):
    source = Counting()
    hub = SensorHub([source])
    view = HubView(hub)
    hub.snapshot()
    snap = view.snapshot()  # just read: taken as they are
    assert source.reads == 1 and snap.value("cpu.temp") == 41.0
    assert snap.history["cpu.temp"] == [41.0]
    monkeypatch.setattr(HubView, "SHARE_S", 0.0)
    time.sleep(0.01)
    snap = view.snapshot()  # too old: read anew, for both
    assert source.reads == 2 and snap.history["cpu.temp"] == [41.0, 42.0]
    assert hub.snapshot().history["cpu.temp"] == [41.0, 43.0]  # the first's history, its own
    view.follow_config(None)
    view.close()  # nothing: the sources are the first panel's
    assert hub.providers == [source] and view.providers is hub.providers


def test_two_panels_on_serial_ports_find_one_each_and_never_the_same(ports):  # noqa: F811
    one = ports(FakeRevA(), device="/dev/ttyACM0", serial_number="USB35INCHIPSV2")
    two = ports(FakeRevA(), device="/dev/ttyACM1", serial_number="USB35INCHIPSV2")
    with display() as first, display() as second:
        first.show(picture((480, 320)))
        second.show(picture((480, 320), seed=40))
        assert place_taken("/dev/ttyACM0") and place_taken("/dev/ttyACM1")
        with pytest.raises(DeviceError, match="in use by another panel"):
            display(port="/dev/ttyACM1").open()
        with pytest.raises(DeviceError, match="no "):
            display().open()  # both taken: none left to find
    assert one.closed and two.closed and one.commands and two.commands
    assert not place_taken("/dev/ttyACM0") and not place_taken("/dev/ttyACM1")
    with display(port="/dev/ttyACM1"):  # let go: free again
        assert place_taken("/dev/ttyACM1")


def test_a_link_that_cannot_open_leaves_its_port_free(ports):  # noqa: F811
    with pytest.raises(DeviceError):
        serial_link.SerialLink("/dev/ttyUSB9")  # nothing on it
    assert not place_taken("/dev/ttyUSB9")


def test_the_panels_run_beside_the_first_and_end_with_it(monkeypatch, tmp_path):
    """Three panels drawing into files (driver "virtual"): the first panel's loop reads the
    sources, the second runs its own theme, the third is off; all end as the first does, each
    going dark as its own table says."""
    built, dark = [], []
    real_build = app.build_hub

    def build_hub(config, demo=False):
        built.append(config)
        return real_build(config, demo)

    monkeypatch.setattr(app, "build_hub", build_hub)
    monkeypatch.setattr(app.MainLoop, "go_dark", lambda loop: dark.append(loop.panel))
    config = parse_config(
        {
            "theme": "slate",
            "fps": 5,
            "device": {"driver": "virtual", "output": str(tmp_path / "one.png")},
            "sensors": {"providers": ["demo"]},
            "panels": [
                {"driver": "virtual", "theme": "pico", "output": str(tmp_path / "two.png"),
                 "dark_on_quit": True},
                {"driver": "virtual", "on": False, "output": str(tmp_path / "three.png")},
            ],
        }
    )  # fmt: skip
    status, stop = app.RunStatus(), threading.Event()
    told = {"stop": stop, "status": status, "quitting": lambda: True}
    loop = threading.Thread(target=app.run, args=(config,), kwargs=told)
    loop.start()
    try:
        assert wait_for(lambda: 0 in status.more and status.more[0].frames > 1, timeout=60)
        assert wait_for(lambda: status.frames > 1, timeout=60)
        assert list(status.more) == [0]  # the third is off
        assert status.more[0].theme == "pico" and status.theme == "slate"
        assert len(built) == 1  # one set of sources for both
        names = {thread.name for thread in threading.enumerate()}
        assert "panel-2" in names and "panel-3" not in names
    finally:
        stop.set()
        loop.join(timeout=30)
    assert not loop.is_alive()
    assert (tmp_path / "one.png").exists() and (tmp_path / "two.png").exists()
    assert not (tmp_path / "three.png").exists()
    assert status.more == {}  # ended with the first
    assert sorted(dark, key=str) == [0]  # the second's table says so; [device]'s does not
    assert "panel-2" not in {thread.name for thread in threading.enumerate()}


def test_a_panel_added_while_the_first_runs_starts_and_one_taken_out_ends(tmp_path):
    hub = SensorHub([])
    status = app.RunStatus()
    more = app._MorePanels(hub, status, None)
    started = threading.Event()

    def main(n, config, stop, run_status):
        started.set()
        stop.wait(10)

    more._main = main
    path = tmp_path / "config.toml"
    with_one = parse_config({"panels": [{"driver": "virtual"}]}, path)
    more.follow(parse_config({}, path))
    assert status.more == {}
    more.follow(with_one)
    assert started.wait(5) and list(status.more) == [0]
    more.follow(with_one)  # the same config: nothing
    more.follow(parse_config({"panels": [{"driver": "virtual", "on": False}]}, path))
    assert status.more == {} and more._runs == {}
    more.close()


def test_the_settings_write_the_panels_back_with_what_differs_from_the_defaults(tmp_path):
    from libre_panel.config import load_config
    from libre_panel.editor.settings import Restart, Settings, SettingsError

    path = tmp_path / "config.toml"
    path.write_text('theme = "studio"\n\n[device]\nbrightness = 70\n', encoding="utf-8")
    settings = Settings(path, None, Restart())
    read = settings.write({"set": {"panels": [{}]}})["config"]["panels"]
    assert read[0]["driver"] == "turzx" and read[0]["output"] == "libre-panel-frame-2.png"
    # the table as it was read, changed: only what differs is written
    changed = {**read[0], "theme": "lumen", "model": "turing-3.5", "rotate": 180}
    settings.write({"set": {"panels": [changed, {"port": "COM6", "on": False}]}})
    text = path.read_text(encoding="utf-8")
    assert text.count("[[panels]]") == 2 and "output" not in text and "driver" not in text
    first, second = load_config(path).panels
    assert (first.theme, first.device.model, first.device.rotate) == ("lumen", "turing-3.5", 180)
    assert (second.on, second.device.port) == (False, "COM6")
    assert load_config(path).device.brightness == 70  # [device] as it was
    for wrong, said in (
        ([{"brightness": 120}], "panels[0].brightness"),
        ([{"video": {}}], "unknown setting 'panels[0].video'"),
        ([{}] * 4, "at most 3"),
        ({"theme": "lumen"}, "at most 3"),
    ):
        with pytest.raises(SettingsError, match=re.escape(said)):
            settings.write({"set": {"panels": wrong}})
    settings.write({"set": {"panels": []}})
    assert "[[panels]]" not in path.read_text(encoding="utf-8") and load_config(path).panels == []


def test_a_slow_source_holds_up_no_reading_of_every_frame():
    """The first panel takes a snapshot while a slow source (the hardware's) is read; the
    other panel's readings of every frame come from a quick one meanwhile, without waiting."""
    entered, release = threading.Event(), threading.Event()

    class Slow(SensorProvider):
        name = "slow"

        def read(self):
            entered.set()
            release.wait(5)
            return {"cpu.temp": Reading("cpu.temp", 50.0, "°C")}

    class Quick(SensorProvider):
        name = "quick"
        every_frame = True

        def read(self):
            return {"cpu.load": Reading("cpu.load", 12.0, "%")}

    hub = SensorHub([Slow(), Quick()])
    taking = threading.Thread(target=hub.snapshot)
    taking.start()
    try:
        assert entered.wait(5)
        began = time.monotonic()
        assert HubView(hub).fresh()["cpu.load"].value == 12.0
        assert time.monotonic() - began < 1.0
    finally:
        release.set()
        taking.join(5)


def test_a_panel_follows_the_first_one_s_brightness_with_a_difference_of_its_own():
    config = parse_config(
        {
            "device": {
                "brightness": 60,
                "night": True,
                "night_brightness": 0,
                "away_brightness": 20,
            },
            "panels": [{"brightness_offset": 15}, {"follow_brightness": False, "brightness": 30}],
        }
    )
    follows, own = config.for_panel(0).device, config.for_panel(1).device
    assert (follows.brightness, follows.night, follows.away_brightness) == (75, True, 35)
    assert follows.night_brightness == 0  # dark at night stays dark
    assert (own.brightness, own.night) == (30, False)  # its own
    with pytest.raises(ConfigError, match=re.escape("panels[0].brightness_offset")):
        parse_config({"panels": [{"brightness_offset": 70}]})
    assert parse_config({"device": {"brightness": 95}, "panels": [{"brightness_offset": 20}]}
                        ).for_panel(0).device.brightness == 100  # fmt: skip


def test_each_panel_shows_its_number_for_a_moment(monkeypatch):
    from libre_panel import identify

    monkeypatch.setattr(identify, "_until", 0.0)  # (put back after the test: shown no longer)
    picture = identify.picture(2, (480, 320), "#E0457B")
    assert picture.size == (480, 320) and picture.getbbox() is not None
    assert picture.getpixel((240, 160)) != (0, 0, 0) or picture.getcolors(256000)  # drawn
    assert not identify.showing()
    identify.show(5)
    assert identify.showing()
    monkeypatch.setattr(identify.time, "monotonic", lambda: 10**9)  # later
    assert not identify.showing()


def test_the_loop_shows_the_panel_s_number_while_it_is_asked_for(monkeypatch):
    from test_app_cli import MemoryStream, StepClock

    from libre_panel import identify
    from libre_panel.config import Config, SensorsConfig
    from libre_panel.sensors.demo import DemoProvider

    clock = StepClock(1000.0)
    loop = app.MainLoop(Config(theme="slate", sensors=SensorsConfig(providers=["demo"])),
                        display=MemoryStream(), clock=clock, panel=1,
                        hub=SensorHub([DemoProvider({"fixed_time": 1_700_000_000})]))  # fmt: skip
    try:
        themed = loop.step()
        monkeypatch.setattr(identify, "_until", identify.time.monotonic() + 60)
        clock.now += 0.1
        numbered = loop.step()
        accent = loop.renderer.theme.palette.get("accent") or "#2EE6D6"
        assert numbered.tobytes() == identify.picture(3, themed.size, accent).tobytes()  # panel 3
    finally:
        loop.close()


def test_a_panel_that_none_shows_anything_on_is_offered_while_all_run(monkeypatch):
    from libre_panel.devices import serial_link, turzx_usb
    from libre_panel.devices.models import find_model
    from libre_panel.editor.settings import Settings

    nine = find_model("turing-9.2-usb")
    monkeypatch.setattr(turzx_usb, "free_models", lambda: [nine])
    monkeypatch.setattr(serial_link, "free_panels", lambda: [])
    running = {"state": "showing", "more": [{"index": 0, "state": "showing"}]}
    assert [spare["id"] for spare in Settings._spare_panels(running)] == ["turing-9.2-usb"]
    looking = {"state": "showing", "more": [{"index": 0, "state": "waiting"}]}
    assert Settings._spare_panels(looking) == []  # it may be the one that panel looks for
    assert Settings._spare_panels({"state": "paused", "more": []}) == []  # nothing is open then


def test_a_panel_shows_a_theme_of_its_own_while_the_first_shows_a_game_or_music():
    from types import SimpleNamespace

    config = parse_config({"panels": [{"theme": "lumen", "game_theme": "arena",
                                       "media_theme": "encore"}, {"theme": "slate"}]})  # fmt: skip
    own, plain = config.for_panel(0), config.for_panel(1)
    assert app._theme_name(own, None, None, None, None, "game") == "arena"
    assert app._theme_name(own, None, None, None, None, "media") == "encore"
    assert app._theme_name(own, None, None, None, None, "idle") == "lumen"  # none given: its own
    assert app._theme_name(own, None, None, None, None, None) == "lumen"
    assert app._theme_name(plain, None, None, None, None, "game") == "slate"  # it was given none
    more = app._MorePanels(SensorHub([]), app.RunStatus(), None, SimpleNamespace(mode="game"))
    assert more._mode() == "game"  # the first panel's mode, for the others
    assert app._MorePanels(SensorHub([]), app.RunStatus(), None)._mode() is None


def test_a_theme_in_another_s_look_takes_its_colours_fonts_and_frames():
    from libre_panel.theme.model import find_theme, load_theme, with_look

    own, first = load_theme(find_theme("lumen-companion")), load_theme(find_theme("arena"))
    looked = with_look(own, first)
    assert looked.palette["accent"] == first.palette["accent"] != own.palette["accent"]
    assert looked.style["radius"] == first.style["radius"] == 18
    assert looked.background_color == first.background_color
    assert looked.widgets == own.widgets and looked.background_image == own.background_image
    assert with_look(own, None) is own and with_look(own, own) is own
    slate = load_theme(find_theme("slate"))  # a palette of other names: those it has
    assert with_look(own, slate).palette["text2"] == own.palette["text2"]
    assert with_look(own, slate).palette["cpu"] == slate.palette["cpu"]
    assert with_look(own, slate).style == own.style  # (slate frames no modules of its own)


def test_a_panel_in_the_first_one_s_look_follows_its_theme_and_a_change_in_design(tmp_path):
    import os
    import shutil

    from test_app_cli import MemoryStream, StepClock

    from libre_panel.config import Config, SensorsConfig, user_themes_dir
    from libre_panel.sensors.demo import DemoProvider
    from libre_panel.theme.model import find_theme, load_theme

    mine = user_themes_dir() / "mine"
    shutil.copytree(find_theme("arena"), mine)
    first = ["mine"]
    clock = StepClock(1000.0)
    loop = app.MainLoop(Config(theme="lumen-companion", same_look=True,
                               sensors=SensorsConfig(providers=["demo"])),
                        display=MemoryStream(), clock=clock, panel=0, look_of=lambda: first[0],
                        hub=SensorHub([DemoProvider({"fixed_time": 1_700_000_000})]))  # fmt: skip

    def accent_after_a_while(expected):
        give_up = time.monotonic() + 20
        while time.monotonic() < give_up:  # the switch is warmed up first, between frames
            clock.now += 3.0
            loop.step()
            if loop.renderer.theme.palette["accent"].upper() == expected.upper():
                break
            time.sleep(0.05)
        return loop.renderer.theme.palette["accent"].upper()

    try:
        loop.step()
        assert loop.shown == "lumen-companion"
        assert loop.renderer.theme.palette["accent"] == load_theme(mine).palette["accent"]
        path = mine / "theme.json"
        path.write_text(path.read_text(encoding="utf-8").replace(
            load_theme(mine).palette["accent"], "#FF3366"), encoding="utf-8")  # fmt: skip
        later = path.stat().st_mtime_ns + 2_000_000_000
        os.utime(path, ns=(later, later))
        assert accent_after_a_while("#FF3366") == "#FF3366"  # saved in Design: followed
        first[0] = "lumen"
        lumen = load_theme(find_theme("lumen")).palette["accent"]
        assert accent_after_a_while(lumen) == lumen.upper() and loop.shown == "lumen-companion"
    finally:
        loop.close()
    plain = app.MainLoop(Config(theme="lumen-companion", sensors=SensorsConfig(providers=["demo"])),
                         display=MemoryStream(), clock=StepClock(0.0), panel=0,
                         look_of=lambda: "arena", hub=SensorHub([DemoProvider({})]))  # fmt: skip
    try:
        assert plain.looked is None  # not asked for: its own look
    finally:
        plain.close()


def test_same_look_is_read_from_the_panel_s_table():
    config = parse_config({"panels": [{"theme": "lumen-companion", "same_look": True}, {}]})
    assert config.panels[0].same_look and config.for_panel(0).same_look
    assert not config.for_panel(1).same_look and not config.same_look
    with pytest.raises(ConfigError, match="same_look"):
        parse_config({"panels": [{"same_look": "yes"}]})


def test_where_a_panel_stands_is_read_and_written_and_moves_no_panel(tmp_path):
    from libre_panel.config import DeviceConfig, load_config
    from libre_panel.editor.settings import Restart, Settings, SettingsError

    config = parse_config({"device": {"at": [-275, 0]}, "panels": [{"at": [48, 20]}, {}]})
    assert config.device.at == (-275, 0) and config.panels[0].device.at == (48, 20)
    assert config.panels[1].device.at is None and config.for_panel(0).device.at == (48, 20)
    for bad in ([1], [1.5, 2], [True, 0], [9000, 0], "here"):
        with pytest.raises(ConfigError, match="at"):
            parse_config({"device": {"at": bad}})
    assert app._same_device(DeviceConfig(at=(0, 0)), DeviceConfig(at=(5, 5)))  # (opened as it is)
    path = tmp_path / "config.toml"
    path.write_text('[[panels]]\ntheme = "lumen"\n', encoding="utf-8")
    settings = Settings(path, None, Restart())
    panels = settings.read()["config"]["panels"]
    settings.write({"set": {"device.at": [10, -40], "panels": [{**panels[0], "at": [250, 0]}]}})
    config = load_config(path)
    assert config.device.at == (10, -40) and config.panels[0].device.at == (250, 0)
    settings.write({"set": {"device.at": None, "panels": [{**panels[0], "at": None}]}})
    assert "at =" not in path.read_text(encoding="utf-8")
    with pytest.raises(SettingsError, match="millimetres"):
        settings.write({"set": {"device.at": [0.5, 0]}})

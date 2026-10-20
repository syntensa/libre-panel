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

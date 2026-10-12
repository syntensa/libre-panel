"""The names fans go by: the user's own, else FanControl's, else the sensor's."""

import json
import os
import tomllib

import pytest

from libre_panel.config import ConfigError, parse_config
from libre_panel.sensors import fan_names
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider
from libre_panel.sensors.fan_names import FanControlNames, FanNames, is_fan

CHIP = "/lpc/chip0000/0"


def fan(key, label, sensor=None, value=900.0):
    """A fan's reading under ``key``, repeating the raw LibreHardwareMonitor ``sensor``."""
    return Reading(key, value, "RPM", label, origin=f"lhm:{sensor}" if sensor else "")


def readings():
    return {
        f"lhm:{CHIP}/fan/0": Reading(f"lhm:{CHIP}/fan/0", 900.0, "RPM", "Fan #1"),
        "fan.lpc.fan_1": fan("fan.lpc.fan_1", "Fan #1", f"{CHIP}/fan/0"),
        "fan.lpc.fan_2": fan("fan.lpc.fan_2", "Fan #2", f"{CHIP}/fan/1"),
        "fan.linux.fan1": fan("fan.linux.fan1", "fan1"),  # the kernel's: no FanControl
        "cpu.temp": Reading("cpu.temp", 50.0, "°C", "CPU"),
    }


def fancontrol_file(tmp_path, nicknames):
    """FanControl's userConfig.json with an output per chip header and the names given."""
    controls = [
        {"Identifier": f"{CHIP}/control/{n}", "NickName": name, "Enable": True}
        for n, name in nicknames.items()
    ]
    path = tmp_path / "userConfig.json"
    path.write_text(json.dumps({"FanControl": {"Controls": controls}}), encoding="utf-8")
    return path


def config(toml=""):
    return parse_config(tomllib.loads(toml))


def test_a_fan_goes_by_its_own_name_else_fancontrols_else_the_sensors(tmp_path):
    path = fancontrol_file(tmp_path, {0: "Intake", 1: "CPU"})
    names = FanNames(FanControlNames(find=lambda given: path))
    names.follow_config(
        config('[[hardware.fans]]\nsensor = "fan.lpc.fan_2"\nname = "Radiator"\n'
               '[[hardware.fans]]\nsensor = "fan.linux.fan1"\nname = "Rear"\n')
    )  # fmt: skip
    named = names.apply(readings())
    assert named["fan.lpc.fan_1"].label == "Intake"  # FanControl's
    assert named["fan.lpc.fan_2"].label == "Radiator"  # the user's own first
    assert named["fan.linux.fan1"].label == "Rear"
    assert named[f"lhm:{CHIP}/fan/0"].label == "Fan #1"  # a raw key keeps the sensor's
    assert named["cpu.temp"].label == "CPU" and named["fan.lpc.fan_1"].value == 900.0
    all_of = readings()
    assert names.detected(all_of["fan.lpc.fan_2"], all_of) == "CPU"  # without the user's
    assert names.detected(all_of["fan.linux.fan1"], all_of) == "fan1"


def test_without_fancontrol_a_fan_keeps_the_sensors_name():
    names = FanNames(FanControlNames(find=lambda given: None))
    names.follow_config(config())
    assert names.apply(readings()) == readings()
    raw = readings()
    assert names.detected(raw["fan.lpc.fan_1"], raw) == "Fan #1"


def test_fancontrols_file_is_read_again_only_when_it_changed(tmp_path):
    now = [0.0]
    path = fancontrol_file(tmp_path, {0: "Intake"})
    found = FanControlNames(clock=lambda: now[0], find=lambda given: path)
    assert found.names() == {f"{CHIP}/fan/0": "Intake"}
    fancontrol_file(tmp_path, {0: "Front"})
    os.utime(path, ns=(10**18, 10**18))
    assert found.names()[f"{CHIP}/fan/0"] == "Intake"  # looked at now and then
    now[0] += fan_names.CHECK_S
    assert found.names() == {f"{CHIP}/fan/0": "Front"}
    path.write_text("{ half written", encoding="utf-8")
    os.utime(path, ns=(2 * 10**18, 2 * 10**18))
    now[0] += fan_names.CHECK_S
    assert found.names() == {f"{CHIP}/fan/0": "Front"}  # FanControl writing: the last ones


def test_the_cooling_analysis_tells_where_fancontrols_file_is():
    where = "C:/Tools/FanControl/userConfig.json"
    names = FanNames()
    names.follow_config(config(f'[services.cooling]\nfancontrol = "{where}"\n'))
    assert names.fancontrol.given == where


def test_only_a_fans_speed_under_a_themes_key_is_named():
    assert is_fan("fan.lpc.fan_1", fan("fan.lpc.fan_1", "Fan #1"))
    assert is_fan("gpu.fan", fan("gpu.fan", "GPU Fan"))
    assert not is_fan("lhm:/x/fan/0", fan("lhm:/x/fan/0", "Fan #1"))
    assert not is_fan("fan.lpc.control_1", Reading("fan.lpc.control_1", 40.0, "%", "Fan #1"))


def test_the_hub_gives_the_readings_their_names():
    class Fans(SensorProvider):
        name = "fans"

        def read(self):
            return readings()

    hub = SensorHub([Fans()], names=FanNames(FanControlNames(find=lambda given: None)))
    hub.follow_config(config('[[hardware.fans]]\nsensor = "fan.lpc.fan_1"\nname = "Intake"\n'))
    assert hub.snapshot().readings["fan.lpc.fan_1"].label == "Intake"


@pytest.mark.parametrize(
    "toml",
    [
        '[[hardware.fans]]\nname = "Intake"\n',  # no sensor
        '[[hardware.fans]]\nsensor = 1\nname = "Intake"\n',
        '[hardware]\nfans = "Intake"\n',
        '[[hardware.fans]]\nsensor = "fan.lpc.fan_1"\nname = "' + "x" * 101 + '"\n',
    ],
)
def test_a_fans_name_in_config_toml_is_checked(toml):
    with pytest.raises(ConfigError):
        config(toml)


def test_every_theme_shows_a_fans_own_name_not_a_fixed_one():
    """A mainboard wires its headers its own way: "Intake" on the first one is a guess."""
    from libre_panel.theme.model import builtin_themes_dir

    for path in sorted(builtin_themes_dir().glob("*/theme.json")):
        for widget in json.loads(path.read_text(encoding="utf-8"))["widgets"]:
            if widget.get("id", "").endswith("-name") and str(widget.get("needs", "")).startswith(
                "fan."
            ):
                assert widget["type"] == "metric" and "{label" in widget["format"], (path, widget)
                assert widget["sensor"] == widget["needs"]

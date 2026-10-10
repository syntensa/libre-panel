"""LibreHardwareMonitor inside Libre Panel: tested with stand-ins for its .NET objects.

Nothing here needs Windows, pythonnet or the libraries: ``fake_lhm`` builds objects
that look like the ones pythonnet hands over.
"""

import json
from pathlib import Path

from libre_panel.sensors import librehardwaremonitor as lhm

TREE = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))


def test_the_two_steps_make_what_readings_from_tree_makes():
    sensors = list(lhm.sensors_from_tree(TREE))
    assert lhm.readings_from_sensors(sensors) == lhm.readings_from_tree(TREE)
    first = sensors[0]
    assert (first.sensor_id, first.hardware_id) == ("/amdcpu/0/voltage/2", "/amdcpu/0")
    assert (first.hardware_name, first.sensor_type) == ("AMD Ryzen 7 7800X3D", "Voltage")
    assert (first.label, first.value, first.unit) == ("VDDCR", 0.643, "V")


def test_every_reading_says_which_sensors_it_is_made_from():
    sources: dict[str, tuple[str, ...]] = {}
    out = lhm.readings_from_sensors(lhm.sensors_from_tree(TREE), sources=sources)
    assert set(sources) == set(out)
    assert sources["lhm:/amdcpu/0/temperature/2"] == ("/amdcpu/0/temperature/2",)
    assert sources["cpu.temp"] == ("/amdcpu/0/temperature/2",)  # the alias: the sensor behind it
    assert sources["temp.cpu.package"] == ("/amdcpu/0/temperature/3",)
    assert sources["gpu.load"] == ("/gpu-nvidia/0/load/0",)
    assert sources["cpu.name"] == () and sources["gpu.name"] == ()  # no sensor to read
    # this version has no average clock: the mean of the cores (the one that says
    # "NaN" has no value, so it is not one of them)
    cores = sources["cpu.freq"]
    assert len(cores) == 7 and all(s.startswith("/amdcpu/0/clock/") for s in cores)
    assert "/amdcpu/0/clock/11" not in cores

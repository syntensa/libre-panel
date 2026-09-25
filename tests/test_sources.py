import json
from pathlib import Path

from libre_panel.config import WeatherConfig
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider
from libre_panel.sensors.librehardwaremonitor import parse_value, readings_from_tree
from libre_panel.sensors.psutil_provider import PsutilProvider
from libre_panel.weather.open_meteo import build_params, describe_weather_code, parse_current


class Fixed(SensorProvider):
    def __init__(self, values):
        super().__init__()
        self.values = values

    def read(self):
        return {k: Reading(k, v) for k, v in self.values.items()}


class Broken(SensorProvider):
    name = "broken"

    def read(self):
        raise RuntimeError("sensor died")


def test_hub_priority_history_and_resilience():
    hub = SensorHub(
        [Broken(), Fixed({"cpu.temp": 60.0}), Fixed({"cpu.temp": 99.0, "gpu.temp": 50.0})]
    )
    snap = hub.snapshot()
    assert snap.value("cpu.temp") == 60.0  # earlier provider wins
    assert snap.value("gpu.temp") == 50.0
    hub.snapshot()
    assert snap.value("missing") is None
    assert hub.snapshot().history["cpu.temp"] == [60.0, 60.0, 60.0]


def test_psutil_provider_reads_basics():
    provider = PsutilProvider()
    provider.read()
    values = provider.read()
    for key in ("cpu.load", "mem.load", "sys.uptime", "net.down"):
        assert key in values


def test_lhm_value_parsing():
    assert parse_value("45,5 °C") == (45.5, "°C")
    assert parse_value("1234 RPM") == (1234.0, "RPM")
    assert parse_value("NaN MHz") == (None, "")
    assert parse_value("-") == (None, "")


def lhm_fixture():
    # Real LibreHardwareMonitor output, see tests/data/README.md
    return json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))


def test_lhm_real_output():
    out = readings_from_tree(lhm_fixture())
    expected = {
        "cpu.temp": (40.0, "°C"),  # "Core (Tctl/Tdie)" on AMD
        "cpu.load": (0.4, "%"),
        "cpu.power": (25.6, "W"),
        "gpu.temp": (27.0, "°C"),
        "gpu.load": (0.0, "%"),
        "gpu.power": (10.9, "W"),  # "GPU Package" = board power
        "gpu.freq": (210.0, "MHz"),
        "gpu.mem.load": (3.5, "%"),
        "gpu.mem.used": (577.0, "MB"),
        "gpu.fan": (0.0, "RPM"),  # a stopped fan is a valid reading
    }
    for key, (value, unit) in expected.items():
        assert (out[key].value, out[key].unit) == (value, unit), key
    # No average clock in this version: mean of the cores that report one ("NaN" skipped).
    assert round(out["cpu.freq"].value, 1) == round((4724.9 * 5 + 3149.9 * 2) / 7, 1)
    assert out["cpu.name"].value == "AMD Ryzen 7 7800X3D"
    assert out["gpu.name"].value == "NVIDIA GeForce RTX 4080 SUPER"
    assert out["lhm:/gpu-nvidia/0/throughput/0"].unit == "MB/s"  # no raw value: as displayed
    raw = out["lhm:/gpu-nvidia/0/throughput/1"]  # raw value present: bytes per second
    assert (raw.value, raw.unit) == (307200.5, "B/s")
    assert "lhm:/gpu-nvidia/0/throughput/2" not in out  # value "-"


def test_lhm_prefers_the_dedicated_gpu():
    tree = lhm_fixture()
    computer = tree["Children"][0]
    igpu = {
        "Text": "AMD Radeon(TM) Graphics",
        "HardwareId": "/gpu-amd/1",
        "ImageURL": "images_icon/ati.png",
        "Children": [
            {
                "Text": "Temperatures",
                "Children": [
                    {
                        "Text": "GPU Core",
                        "Value": "45,0 °C",
                        "SensorId": "/gpu-amd/1/temperature/0",
                        "Type": "Temperature",
                        "Children": [],
                    }
                ],
            }
        ],
    }
    computer["Children"].insert(0, igpu)  # listed first, still not chosen
    assert readings_from_tree(tree)["gpu.temp"].value == 27.0
    assert readings_from_tree(tree, gpu="radeon")["gpu.temp"].value == 45.0
    assert "lhm:/gpu-amd/1/temperature/0" in readings_from_tree(tree)


def test_lhm_throughput_uses_raw_bytes():
    leaf = {
        "Text": "Download Speed",
        "Type": "Throughput",
        "Value": "1,2 MB/s",
        "RawValue": 1234567.0,
        "SensorId": "/nic/0/throughput/1",
    }
    out = readings_from_tree({"Children": [leaf]})
    assert (out["lhm:/nic/0/throughput/1"].value, out["lhm:/nic/0/throughput/1"].unit) == (
        1234567.0,
        "B/s",
    )


def test_weather_parsing_and_params():
    cfg = WeatherConfig(enabled=True, latitude=1.5, longitude=2.5, units="imperial")
    params = build_params(cfg)
    assert params["temperature_unit"] == "fahrenheit" and params["latitude"] == 1.5
    data = {
        "current_units": {"temperature_2m": "°F", "wind_speed_10m": "mp/h"},
        "current": {"temperature_2m": 57.1, "weather_code": 61, "wind_speed_10m": 9.0},
    }
    out = parse_current(data)
    assert out["weather.temperature"].unit == "°F"
    assert out["weather.description"].value == "Light rain"
    assert describe_weather_code(None) == ""


def test_psutil_provider_without_cpu_freq(monkeypatch):
    import psutil

    monkeypatch.delattr(psutil, "cpu_freq", raising=False)
    values = PsutilProvider().read()
    assert "cpu.freq" not in values and "cpu.load" in values

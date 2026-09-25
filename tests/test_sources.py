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


def test_lhm_parsing():
    assert parse_value("45,5 °C") == (45.5, "°C")
    assert parse_value("1234 RPM") == (1234.0, "RPM")
    assert parse_value("-") == (None, "")
    tree = {
        "Text": "Sensor",
        "Children": [
            {
                "Text": "AMD Ryzen",
                "Children": [
                    {
                        "Text": "Core (Tctl/Tdie)",
                        "Value": "61,2 °C",
                        "SensorId": "/amdcpu/0/temperature/2",
                        "Type": "Temperature",
                    },
                    {
                        "Text": "CPU Total",
                        "Value": "12,0 %",
                        "SensorId": "/amdcpu/0/load/0",
                        "Type": "Load",
                    },
                ],
            },
            {
                "Text": "Radeon",
                "Children": [
                    {
                        "Text": "GPU Core",
                        "Value": "48 °C",
                        "SensorId": "/gpu-amd/0/temperature/0",
                        "Type": "Temperature",
                    },
                    {
                        "Text": "GPU Package",
                        "Value": "212,4 W",
                        "SensorId": "/gpu-amd/0/power/3",
                        "Type": "Power",
                    },
                    {
                        "Text": "GPU Fan",
                        "Value": "0 RPM",
                        "SensorId": "/gpu-amd/0/fan/0",
                        "Type": "Fan",
                    },
                ],
            },
        ],
    }
    out = readings_from_tree(tree)
    assert out["cpu.temp"].value == 61.2
    assert out["cpu.load"].value == 12.0
    assert out["gpu.temp"].value == 48.0
    assert out["gpu.power"].value == 212.4  # board power (TBP), as SPUR II verified
    assert out["gpu.fan"].value == 0.0  # a stopped fan is a valid reading
    assert "lhm:/amdcpu/0/load/0" in out


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

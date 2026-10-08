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


def test_every_frame_providers_follow_between_snapshots():
    class Volume(Fixed):
        every_frame = True

    slow = Fixed({"cpu.temp": 60.0, "audio.volume": 10.0})  # first: its volume wins
    volume = Volume({"audio.volume": 50.0, "audio.muted": 0.0, "cpu.load": 5.0})
    hub = SensorHub([slow, volume])
    assert hub.every_frame and not SensorHub([slow]).every_frame
    snap = hub.snapshot()
    volume.values.update({"audio.volume": 80.0, "audio.muted": 1.0, "cpu.load": 7.0})
    slow.values["cpu.temp"] = 70.0
    fresh = hub.fresh()
    assert fresh == {"audio.muted": fresh["audio.muted"], "cpu.load": fresh["cpu.load"]}
    assert fresh["audio.muted"].value == 1.0  # its own keys follow at once
    assert "audio.volume" not in fresh  # the earlier provider's key stays its own
    assert snap.history["cpu.load"] == [5.0]  # no history between snapshots
    assert hub.snapshot().history["cpu.load"] == [5.0, 7.0]


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


def _forecast_data():
    hours = [f"2026-10-08T{h:02d}:00" for h in range(24)] + [
        f"2026-10-09T{h:02d}:00" for h in range(24)
    ]
    return {
        "utc_offset_seconds": 7200,
        "hourly": {
            "time": hours,
            "temperature_2m": list(range(48)),
            "weather_code": [61] * 48,
            "precipitation_probability": [40] * 48,
            "is_day": [0] * 7 + [1] * 12 + [0] * 29,
        },
        "hourly_units": {"temperature_2m": "°C", "precipitation_probability": "%"},
        "daily": {
            "time": ["2026-10-08", "2026-10-09"],
            "temperature_2m_max": [17, 18],
            "temperature_2m_min": [9, None],
            "weather_code": [3, 0],
            "precipitation_probability_max": [20, 0],
        },
        "daily_units": {"temperature_2m_max": "°C"},
    }


def test_weather_forecast_follows_the_clock():
    from datetime import UTC, datetime

    from libre_panel.weather.open_meteo import forecast_readings

    params = build_params(WeatherConfig(enabled=True, latitude=1, longitude=2))
    assert "temperature_2m" in params["hourly"] and "temperature_2m_max" in params["daily"]
    data = _forecast_data()
    at_1430 = datetime(2026, 10, 8, 12, 30, tzinfo=UTC).timestamp()  # 14:30 at the place
    out = forecast_readings(data, at_1430)
    assert out["weather.hour.1.time"].value == "15:00"
    assert out["weather.hour.1.temperature"].value == 15
    assert out["weather.hour.1.temperature"].unit == "°C"
    assert out["weather.hour.5.day"].value == 0  # 19:00: night
    assert out["weather.day.0.name"].value == "Today"
    assert out["weather.day.1.high"].value == 18 and "weather.day.1.low" not in out
    # an hour later the same answer says "in 1 hour" about 16:00
    later = forecast_readings(data, at_1430 + 3600)
    assert later["weather.hour.1.time"].value == "16:00"
    # past the data: nothing left to say
    assert forecast_readings(data, at_1430 + 3 * 86400) == {}
    assert forecast_readings({}, at_1430) == {}


def test_weather_forecast_day_names_in_german():
    from datetime import UTC, datetime

    from libre_panel import i18n
    from libre_panel.weather.open_meteo import forecast_readings

    i18n.set_language("de")
    try:
        out = forecast_readings(_forecast_data(), datetime(2026, 10, 8, 12, tzinfo=UTC).timestamp())
    finally:
        i18n.set_language("en")
    assert out["weather.day.0.name"].value == "Heute"
    assert out["weather.day.1.name"].value == "Fr"

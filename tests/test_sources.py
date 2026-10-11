import json
from pathlib import Path

import pytest

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
        "gpu.hotspot": (35.3, "°C"),  # "GPU Hot Spot"
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
    # the names, tidied as the hardware source tidies the ones it finds (sensors/hwnames.py)
    assert out["cpu.name"].value == "Ryzen 5 7600"
    assert out["gpu.name"].value == "GeForce RTX 4070"
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


def memory_modules(*temperatures):
    """Memory modules as LibreHardwareMonitor lists them (its DimmMemory): each is a piece of
    hardware under /memory/dimm/<n>, its thermal sensor is "DIMM #<n>" and the limits it tells
    are temperatures too (which never change). The Total Memory beside them has none."""

    def sensor(n, index, label, value):
        return {
            "Text": label,
            "Value": f"{value:.1f} °C".replace(".", ","),
            "SensorId": f"/memory/dimm/{n}/temperature/{index}",
            "Type": "Temperature",
            "Children": [],
        }

    return [
        {
            "Text": f"Maker - PART-{n} (#{n})",
            "HardwareId": f"/memory/dimm/{n}",
            "Children": [
                {
                    "Text": "Temperatures",
                    "Children": [
                        sensor(n, 0, f"DIMM #{n}", value),
                        sensor(n, 3, "Thermal Sensor High Limit", 85.0),
                        sensor(n, 5, "Thermal Sensor Critical High Limit", 95.0),
                    ],
                }
            ],
        }
        for n, value in enumerate(temperatures, start=1)
    ]


def test_lhm_memory_temperature_is_the_hottest_module_and_never_a_limit():
    tree = lhm_fixture()
    tree["Children"][0]["Children"] += memory_modules(41.5, 44.0, 43.0)
    out = readings_from_tree(tree)
    hottest = out["mem.temp"]
    assert (hottest.value, hottest.unit, hottest.label) == (44.0, "°C", "Memory temperature")
    assert hottest.origin == "lhm:/memory/dimm/2/temperature/0"
    assert out["temp.memory1.dimm_1"].value == 41.5  # (the modules are told apart by number)
    assert out["temp.memory2.thermal_sensor_high_limit"].value == 85.0  # a limit, not heat
    one = lhm_fixture()
    one["Children"][0]["Children"] += memory_modules(39.0)
    alone = readings_from_tree(one)
    assert alone["mem.temp"].value == 39.0
    assert alone["temp.memory.dimm_1"].origin == "lhm:/memory/dimm/1/temperature/0"
    # no module with a thermal sensor (DDR3, no driver, a laptop): no reading, not a zero
    assert "mem.temp" not in readings_from_tree(lhm_fixture())


def test_lhm_memory_temperature_is_made_from_every_module_so_all_are_read():
    from libre_panel.sensors.librehardwaremonitor import readings_from_sensors, sensors_from_tree

    tree = lhm_fixture()
    tree["Children"][0]["Children"] += memory_modules(41.5, 44.0)
    sources = {}
    readings_from_sensors(sensors_from_tree(tree), None, sources)
    assert sources["mem.temp"] == (
        "/memory/dimm/1/temperature/0",
        "/memory/dimm/2/temperature/0",
    )  # whichever is the hottest next time: both have to be updated
    assert sources["cpu.temp"] == ("/amdcpu/0/temperature/2",)  # the others are as they were


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


def test_psutil_memory_used_free_and_total_add_up():
    values = PsutilProvider().read()
    used, free, total = (values[f"mem.{part}"] for part in ("used", "free", "total"))
    assert (free.unit, free.label) == ("GiB", "Memory free") and 0 < free.value <= total.value
    assert used.value + free.value == pytest.approx(total.value)


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


def _minutely(amounts):
    """Open-Meteo's 15-minute precipitation (mm) from 14:00 local time on."""
    from datetime import datetime, timedelta

    start = datetime(2026, 10, 8, 14)
    times = [
        (start + timedelta(minutes=15 * i)).isoformat(timespec="minutes")
        for i in range(len(amounts))
    ]
    return {"minutely_15": {"time": times, "precipitation": amounts}}


def test_weather_params_ask_for_the_rain_and_the_hours():
    params = build_params(WeatherConfig(enabled=True, latitude=1, longitude=2))
    assert params["minutely_15"] == "precipitation"
    assert {"weather_code", "precipitation_probability", "is_day"} <= set(
        params["hourly"].split(",")
    )


def test_weather_rain_starts_and_stops():
    from datetime import UTC, datetime

    from libre_panel.weather.open_meteo import forecast_readings

    data = {**_forecast_data(), **_minutely([0, 0, 0, 0.4, 1.2, 0.8, 0.5, 0, 0, 0, 0, 0])}
    # 14:32 local (the place is UTC+2): dry, rain from 14:45, counted from now
    out = forecast_readings(data, datetime(2026, 10, 8, 12, 32, tzinfo=UTC).timestamp())
    assert out["weather.rain.minutes"].value == 13 and out["weather.rain.minutes"].unit == "min"
    assert out["weather.rain.text"].value == "Rain in 13 min"
    # 15:10 local: raining, dry from 15:45
    out = forecast_readings(data, datetime(2026, 10, 8, 13, 10, tzinfo=UTC).timestamp())
    assert out["weather.rain.minutes"].value == 35
    assert out["weather.rain.text"].value == "Rain stops in 35 min"
    # the hours keep their code, chance of rain and day or night
    assert out["weather.hour.1.code"].value == 61
    assert out["weather.hour.1.rain"].value == 40
    assert out["weather.hour.1.day"].value == 1  # 16:00: day


def test_weather_rain_when_nothing_changes():
    from datetime import UTC, datetime

    from libre_panel.weather.open_meteo import forecast_readings

    at_1432 = datetime(2026, 10, 8, 12, 32, tzinfo=UTC).timestamp()
    dry = forecast_readings({**_forecast_data(), **_minutely([0] * 12)}, at_1432)
    assert "weather.rain.minutes" not in dry
    assert dry["weather.rain.text"].value == "No rain for the next 2 h"
    wet = forecast_readings({**_forecast_data(), **_minutely([1.0] * 12)}, at_1432)
    assert "weather.rain.minutes" not in wet
    assert wet["weather.rain.text"].value == "Rain for the next 2 h"
    # the window ends at 16:30: rain at 16:30 counts, rain at 16:45 does not
    edge = forecast_readings({**_forecast_data(), **_minutely([0] * 10 + [1.0])}, at_1432)
    assert edge["weather.rain.minutes"].value == 118
    assert edge["weather.rain.text"].value == "Rain in 118 min"
    late = forecast_readings({**_forecast_data(), **_minutely([0] * 11 + [1.0])}, at_1432)
    assert "weather.rain.minutes" not in late
    assert late["weather.rain.text"].value == "No rain for the next 2 h"
    # an answer that ends before the two hours do says nothing about the rain
    short = forecast_readings({**_forecast_data(), **_minutely([0, 0, 0])}, at_1432)
    assert "weather.rain.text" not in short
    assert "weather.rain.text" not in forecast_readings(_forecast_data(), at_1432)


def test_weather_rain_text_in_german():
    from datetime import UTC, datetime

    from libre_panel import i18n
    from libre_panel.weather.open_meteo import forecast_readings

    at_1432 = datetime(2026, 10, 8, 12, 32, tzinfo=UTC).timestamp()
    i18n.set_language("de")
    try:
        stops = forecast_readings({**_forecast_data(), **_minutely([0.5] * 3 + [0] * 9)}, at_1432)
        starts = forecast_readings({**_forecast_data(), **_minutely([0] * 3 + [0.4] * 9)}, at_1432)
        dry = forecast_readings({**_forecast_data(), **_minutely([0] * 12)}, at_1432)
    finally:
        i18n.set_language("en")
    assert stops["weather.rain.text"].value == "Regen endet in 13 min"
    assert starts["weather.rain.text"].value == "Regen in 13 min"
    assert dry["weather.rain.text"].value == "Kein Regen in den nächsten 2 Std."

import tomllib

import pytest

from libre_panel.config import (
    DEFAULT_CONFIG_TOML,
    ConfigError,
    config_dir,
    load_config,
    parse_config,
    write_default_config,
)


def test_defaults_have_nothing_personal():
    cfg = load_config()
    assert cfg.weather.enabled is False
    assert cfg.weather.latitude is None and cfg.weather.longitude is None
    assert cfg.device.model == "auto"


def test_default_template_parses_to_defaults():
    cfg = parse_config(tomllib.loads(DEFAULT_CONFIG_TOML))
    assert cfg.weather.enabled is False
    assert cfg.sensors.providers == ["psutil"]
    assert cfg.sensors.options["librehardwaremonitor"]["url"].startswith("http://127.0.0.1")


def test_weather_needs_a_location():
    with pytest.raises(ConfigError, match="no location"):
        parse_config({"weather": {"enabled": True}})


def test_weather_with_location():
    cfg = parse_config(
        {"weather": {"enabled": True, "latitude": 52.5, "longitude": 13, "units": "imperial"}}
    )
    assert (cfg.weather.latitude, cfg.weather.longitude, cfg.weather.units) == (
        52.5,
        13.0,
        "imperial",
    )


@pytest.mark.parametrize(
    "data",
    [
        {"device": {"model": "no-such-panel"}},
        {"device": {"brightness": 150}},
        {"device": {"brightness": True}},
        {"weather": {"units": "kelvin"}},
        {"refresh_ms": 10},
    ],
)
def test_invalid_values(data):
    with pytest.raises(ConfigError):
        parse_config(data)


def test_write_default_config_roundtrip(isolated_home):
    path = write_default_config()
    assert path == config_dir() / "config.toml"
    assert load_config(path).theme == "libre-default"
    with pytest.raises(FileExistsError):
        write_default_config()

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


def test_set_config_value_keeps_comments_and_other_keys(isolated_home):
    from libre_panel.config import set_brightness, set_config_value

    path = write_default_config()
    set_brightness(25)
    set_config_value("theme", "orbit")
    text = path.read_text(encoding="utf-8")
    assert "brightness = 25" in text and 'theme = "orbit"' in text
    assert "# Libre Panel configuration" in text and "[sensors.librehardwaremonitor]" in text
    cfg = load_config()
    assert (cfg.device.brightness, cfg.theme, cfg.device.model) == (25, "orbit", "auto")


def test_set_config_value_adds_missing_keys_and_tables(isolated_home):
    from libre_panel.config import set_brightness

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('theme = "slate"\n\n[weather]\nenabled = false\n', encoding="utf-8")
    set_brightness(70)
    assert load_config().device.brightness == 70
    path.write_text('theme = "slate"\n[ device ]  # the panel\nmodel = "auto"\n', encoding="utf-8")
    set_brightness(40)
    text = path.read_text(encoding="utf-8")
    assert text.count("[ device ]") == 1 and "brightness = 40" in text
    assert load_config().device.brightness == 40


def test_set_config_value_never_writes_a_broken_config(isolated_home):
    from libre_panel.config import set_brightness, set_config_value

    path = write_default_config()
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        set_brightness(150)
    with pytest.raises(ConfigError):
        set_config_value("device.model", "no-such-panel")
    assert path.read_text(encoding="utf-8") == before
    # a layout the line editor does not handle is refused, not mangled
    path.write_text("device = { brightness = 10 }\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        set_brightness(20)
    assert load_config().device.brightness == 10

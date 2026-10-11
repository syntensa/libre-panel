import tomllib

import pytest

from libre_panel.config import (
    DEFAULT_CONFIG_TOML,
    ConfigError,
    config_dir,
    default_providers,
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
    assert cfg.sensors.providers == default_providers()
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
        {"device": {"rotate": 90}},
        {"device": {"rotate": "180"}},
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
    assert load_config(path).device.rotate == 0
    assert parse_config({"device": {"rotate": 180}}).device.rotate == 180
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


def test_set_config_value_keeps_a_comment_after_the_value(isolated_home):
    from libre_panel.config import set_brightness, set_config_value

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'theme = "a#b"   # which theme\n\n[device]\n  brightness = 40  # 0-100, "dim"\n',
        encoding="utf-8",
    )
    set_brightness(55)
    set_config_value("theme", "slate")
    text = path.read_text(encoding="utf-8")
    assert 'theme = "slate"   # which theme\n' in text  # the # inside the old string is no comment
    assert '  brightness = 55  # 0-100, "dim"\n' in text
    assert load_config().device.brightness == 55


def test_set_config_value_in_nested_tables(isolated_home):
    from libre_panel.config import set_config_value

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'theme = "slate"\n\n[ modes . game ]  # while a game runs\nfps = 30\n\n[weather]\n',
        encoding="utf-8",
    )
    set_config_value("modes.game.theme", "column")
    set_config_value("modes.work.fps", 20)  # a table that is not there yet
    text = path.read_text(encoding="utf-8")
    assert '[ modes . game ]  # while a game runs\ntheme = "column"\nfps = 30' in text
    config = load_config()
    assert config.modes["game"].theme == "column" and config.modes["game"].fps == 30
    assert config.modes["work"].fps == 20


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


AUTOPILOT = """\
# my panel
theme = "slate"

[services]
enabled = [
    "notices",   # messages
    "autopilot",
]

# the autopilot's settings
[services.autopilot]
hold_s = 8             # a new choice must hold this long
min_s = 20

# music first
[[services.autopilot.rules]]     # checked from the top
name = "Music"
theme = "studio"
when = { music = true }

# at night
[[services.autopilot.rules]]
name = "Night"
theme = "pico"
when = { time = "22:00-06:30" }

# while a game runs
[modes.game]
fps = 30
"""


def autopilot_rules():
    return load_config().services.options["autopilot"]["rules"]


def test_set_config_value_writes_lists_and_replaces_a_list_over_several_lines(isolated_home):
    from libre_panel.config import set_config_value

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(AUTOPILOT, encoding="utf-8")
    set_config_value("services.enabled", ["autopilot"])
    text = path.read_text(encoding="utf-8")
    assert 'enabled = ["autopilot"]\n\n# the autopilot' in text  # the whole list was replaced
    assert load_config().services.enabled == ["autopilot"]
    set_config_value("services.enabled", ["notices", "game"])
    assert load_config().services.enabled == ["notices", "game"]
    # a comment after a list stays, whether the list is on one line or several
    path.write_text('[services]\nenabled = [\n  "a",  # one\n  "b",\n]   # all of them\n')
    set_config_value("services.enabled", ["c"])
    assert path.read_text(encoding="utf-8") == '[services]\nenabled = ["c"]   # all of them\n'
    # a list in a table that is not there yet, and an empty one
    set_config_value("sensors.providers", ["psutil", "load"])
    set_config_value("services.enabled", [])
    assert load_config().sensors.providers == ["psutil", "load"]
    assert load_config().services.enabled == []


def test_set_config_tables_replaces_the_rules_and_keeps_the_rest(isolated_home):
    from libre_panel.config import set_config_tables

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(AUTOPILOT, encoding="utf-8")
    rules = [
        {"name": "Call", "theme": "slate", "when": {"microphone": True}, "immediate": True},
        {
            "name": 'Büro "A"',  # not ASCII, a quote
            "theme": "orbit",
            "when": {"apps": ["code.exe", "x.exe"], "cpu_over": 80, "for_s": 30.5},
            "hold_s": 2,
        },
    ]
    set_config_tables("services.autopilot.rules", rules)
    text = path.read_text(encoding="utf-8")
    assert autopilot_rules() == rules
    assert "# my panel" in text and "# the autopilot's settings" in text
    assert "hold_s = 8             # a new choice must hold this long" in text  # untouched
    assert "# while a game runs\n[modes.game]\nfps = 30\n" in text  # the next table's comment stays
    assert "# music first" not in text and "# checked from the top" not in text  # went with them
    assert 'when = { apps = ["code.exe", "x.exe"], cpu_over = 80, for_s = 30.5 }' in text
    assert 'name = "Büro \\"A\\""' in text
    assert text.count("[[services.autopilot.rules]]") == 2
    config = load_config()
    assert config.services.enabled == ["notices", "autopilot"] and config.modes["game"].fps == 30

    set_config_tables("services.autopilot.rules", rules[:1])  # fewer
    assert autopilot_rules() == rules[:1]
    set_config_tables("services.autopilot.rules", [])  # none: the key is gone, the table stays
    text = path.read_text(encoding="utf-8")
    assert "rules" not in text and "[services.autopilot]" in text and "fps = 30" in text
    assert "rules" not in load_config().services.options["autopilot"]


def test_set_config_tables_puts_new_rules_after_the_tables_keys(isolated_home):
    from libre_panel.config import set_config_tables

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    rules = [{"name": "Night", "theme": "pico", "when": {"time": "22:00-06:30"}}]
    # the table is there, with other tables after it
    path.write_text("[services.autopilot]\nhold_s = 5\n[modes.game]\nfps = 30\n", encoding="utf-8")
    set_config_tables("services.autopilot.rules", rules)
    text = path.read_text(encoding="utf-8")
    assert "hold_s = 5\n\n[[services.autopilot.rules]]\nname" in text and "\n\n[modes.game]" in text
    assert autopilot_rules() == rules and load_config().modes["game"].fps == 30
    # no such table, no file: the end of the file; the default config is written first
    path.write_text('theme = "slate"', encoding="utf-8")  # without a final newline
    set_config_tables("services.autopilot.rules", rules)
    assert path.read_text(encoding="utf-8").startswith('theme = "slate"\n\n[[services.autopilot')
    path.unlink()
    set_config_tables("services.autopilot.rules", rules)
    assert autopilot_rules() == rules and load_config().theme == "libre-default"


def test_set_config_tables_takes_over_rules_written_as_a_list(isolated_home):
    from libre_panel.config import set_config_tables

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "[services.autopilot]\nrules = [\n"
        '  { name = "Old", theme = "slate", when = { music = true } },\n'
        "]  # inline\nhold_s = 5\n\n[modes.game]\nfps = 30\n",
        encoding="utf-8",
    )
    rules = [{"name": "New", "theme": "orbit", "when": {"music": False}}]
    set_config_tables("services.autopilot.rules", rules)
    text = path.read_text(encoding="utf-8")
    assert "Old" not in text and "hold_s = 5" in text  # the keys after it still belong to the table
    assert autopilot_rules() == rules
    assert load_config().services.options["autopilot"]["hold_s"] == 5


def test_set_config_tables_never_writes_a_broken_config(isolated_home):
    from libre_panel.config import set_config_tables

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    rule = {"name": "x", "theme": "slate", "when": {"music": True}}
    # a layout the line editor does not handle is refused, not mangled
    for text in (
        "services = { autopilot = { hold_s = 5 } }\n",
        "[services.autopilot.rules]\nname = 'a table, not an array'\n",
    ):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError):
            set_config_tables("services.autopilot.rules", [rule])
        assert path.read_text(encoding="utf-8") == text
    path.write_text("this is not toml", encoding="utf-8")
    with pytest.raises(ConfigError):
        set_config_tables("services.autopilot.rules", [rule])
    assert path.read_text(encoding="utf-8") == "this is not toml"
    with pytest.raises(TypeError):  # what config.toml cannot hold
        path.write_text("", encoding="utf-8")
        set_config_tables("services.autopilot.rules", [{"name": object()}])
    assert path.read_text(encoding="utf-8") == ""


def test_edit_config_takes_a_setting_out(isolated_home):
    from libre_panel.config import edit_config

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'theme = "slate"\nrefresh_ms = 500  # twice a second\n\n[weather]\nenabled = false\n'
        '# the place\nlatitude = 1.5\nlongitude = 2.5\nunits = "imperial"\n',
        encoding="utf-8",
    )
    edit_config(("unset", "refresh_ms", None), ("unset", "weather.latitude", None), path=path)
    text = path.read_text(encoding="utf-8")
    assert "refresh_ms" not in text and "latitude" not in text
    assert "# the place\nlongitude = 2.5" in text and 'units = "imperial"' in text
    config = load_config()
    assert config.refresh_ms is None and config.weather.latitude is None
    assert (config.weather.longitude, config.weather.units) == (2.5, "imperial")

    # where it is not set, nothing happens; a table that is not there is not made
    before = path.read_text(encoding="utf-8")
    edit_config(("unset", "refresh_ms", None), ("unset", "modes.game.fps", None), path=path)
    assert path.read_text(encoding="utf-8") == before

    # a value that takes several lines goes whole; set and unset go in one write
    path.write_text('[sensors]\nproviders = [\n  "load",\n  "psutil",\n]\nurl = "x"\n', "utf-8")
    edit_config(("unset", "sensors.providers", None), ("value", "refresh_ms", 250), path=path)
    assert load_config().sensors.providers == default_providers()  # the default again
    assert 'url = "x"' in path.read_text(encoding="utf-8")
    assert load_config().refresh_ms == 250

    # taking out what makes the rest invalid is refused like any other change
    path.write_text("[weather]\nenabled = true\nlatitude = 1.0\nlongitude = 2.0\n", "utf-8")
    with pytest.raises(ConfigError, match="no location"):
        edit_config(("unset", "weather.latitude", None), path=path)
    assert "latitude" in path.read_text(encoding="utf-8")


def test_the_place_has_a_name(isolated_home):
    from libre_panel.config import set_config_value

    assert load_config().weather.place == ""
    set_config_value("weather.place", "Nowhere, Country")
    assert load_config().weather.place == "Nowhere, Country"
    with pytest.raises(ConfigError):
        parse_config({"weather": {"place": 5}})
    with pytest.raises(ConfigError, match="at most 120"):
        parse_config({"weather": {"place": "x" * 121}})


def test_edit_config_makes_several_changes_in_one_write(isolated_home):
    from libre_panel.config import edit_config

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('theme = "slate"\n\n[weather]\nenabled = false\n', encoding="utf-8")
    rules = [{"name": "Night", "theme": "pico", "when": {"time": "22:00-06:30"}}]
    edit_config(  # the rules first: the settings of their table still come before them
        ("tables", "services.autopilot.rules", rules),
        ("value", "services.autopilot.hold_s", 3),
        ("value", "services.enabled", ["autopilot"]),
        ("value", "theme", "orbit"),
        path=path,
    )
    config = load_config()
    assert config.theme == "orbit" and config.services.enabled == ["autopilot"]
    assert config.services.options["autopilot"] == {"hold_s": 3, "rules": rules}
    text = path.read_text(encoding="utf-8")
    assert text.index("[services.autopilot]") < text.index("[[services.autopilot.rules]]")

    before = path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):  # one part is wrong: none is written
        edit_config(("value", "theme", "slate"), ("value", "device.brightness", 500), path=path)
    with pytest.raises(ConfigError, match="not a table"):
        edit_config(("value", "theme.deeper", 1), path=path)
    with pytest.raises(ValueError, match="kind of change"):
        edit_config(("rename", "theme", "x"), path=path)
    assert path.read_text(encoding="utf-8") == before

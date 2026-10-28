import tomllib

import pytest

from libre_panel.config import (
    DEFAULT_CONFIG_TOML,
    ConfigError,
    config_dir,
    default_providers,
    hardware_name,
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


def test_the_look_of_the_weather_symbols_is_vivid_unless_the_user_chooses():
    assert load_config().symbols == parse_config({}).symbols == "vivid"
    assert parse_config({"symbols": "classic"}).symbols == "classic"
    with pytest.raises(ConfigError, match="symbols"):
        parse_config({"symbols": "retro"})
    assert '# symbols = "vivid"' in DEFAULT_CONFIG_TOML


def hardware_names(config):
    hardware = config.hardware
    return hardware.cpu_name, hardware.gpu_name, hardware.memory_name


def test_the_hardware_names_are_the_found_ones_unless_the_user_writes_them():
    assert hardware_names(load_config()) == ("", "", "")
    assert hardware_names(parse_config({})) == ("", "", "")
    assert hardware_names(parse_config({"hardware": {}})) == ("", "", "")
    cfg = parse_config({"hardware": {"gpu_name": "My card"}})
    assert hardware_names(cfg) == ("", "My card", "")


def test_the_default_template_has_the_hardware_names_as_comments():
    assert "[hardware]" in DEFAULT_CONFIG_TOML
    for key in ("cpu_name", "gpu_name", "memory_name"):
        assert f'# {key} = ""' in DEFAULT_CONFIG_TOML
    assert tomllib.loads(DEFAULT_CONFIG_TOML)["hardware"] == {}  # nothing set, nothing written
    assert hardware_names(parse_config(tomllib.loads(DEFAULT_CONFIG_TOML))) == ("", "", "")


def test_the_users_hardware_names_are_text_on_one_line():
    cfg = parse_config(
        {"hardware": {"cpu_name": "  My   CPU ", "gpu_name": "Two\nlines\there", "memory_name": ""}}
    )
    assert hardware_names(cfg) == ("My CPU", "Two lines here", "")
    assert hardware_name("a\x07b\x00c", "x") == "abc"  # control characters cannot be drawn
    assert hardware_name("Ünïcode ✓ 32 GB", "x") == "Ünïcode ✓ 32 GB"
    assert hardware_name("x" * 100, "x") == "x" * 100


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"hardware": {"cpu_name": 5}}, "hardware.cpu_name"),
        ({"hardware": {"gpu_name": True}}, "hardware.gpu_name"),
        ({"hardware": {"memory_name": ["a"]}}, "hardware.memory_name"),
        ({"hardware": {"cpu_name": "x" * 101}}, "hardware.cpu_name: at most 100 characters"),
        ({"hardware": "My CPU"}, "hardware"),
    ],
)
def test_hardware_names_that_are_not_text_or_are_too_long_are_refused(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_a_usb_display_s_picture_quality_is_read_and_checked():
    """[device] jpeg_quality: 90 by default, 50 to 100, for [[panels]] too (#33)."""
    assert parse_config({}).device.jpeg_quality == 90
    assert parse_config({"device": {"jpeg_quality": 100}}).device.jpeg_quality == 100
    cfg = parse_config({"panels": [{"model": "waveshare-2.8-udisp", "jpeg_quality": 95}]})
    assert cfg.panels[0].device.jpeg_quality == 95
    for wrong, message in ((101, "between 50 and 100"), (49, "between 50 and 100"),
                           ("95", "jpeg_quality")):  # fmt: skip
        with pytest.raises(ConfigError, match=message):
            parse_config({"device": {"jpeg_quality": wrong}})


def test_set_config_value_writes_a_hardware_name_into_the_template_and_keeps_its_notes(
    isolated_home,
):
    from libre_panel.config import edit_config, set_config_value

    path = write_default_config()
    set_config_value("hardware.cpu_name", "My CPU")
    edit_config(("value", "hardware.memory_name", "2x16 GB"), ("value", "hardware.gpu_name", ""))
    text = path.read_text(encoding="utf-8")
    assert tomllib.loads(text)["hardware"] == {
        "cpu_name": "My CPU", "memory_name": "2x16 GB", "gpu_name": ""
    }  # fmt: skip
    assert "The names stay on this computer." in text and "[weather]" in text
    assert hardware_names(load_config()) == ("My CPU", "", "2x16 GB")
    set_config_value("hardware.cpu_name", "")  # back to the found one
    assert hardware_names(load_config()) == ("", "", "2x16 GB")


def test_a_hardware_name_is_added_to_a_config_that_has_no_such_section(isolated_home):
    from libre_panel.config import set_config_value

    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('theme = "slate"  # mine\n\n[device]\nbrightness = 40\n', encoding="utf-8")
    set_config_value("hardware.gpu_name", "My card")
    assert path.read_text(encoding="utf-8").startswith('theme = "slate"  # mine\n')
    assert hardware_names(load_config()) == ("", "My card", "")
    assert load_config().device.brightness == 40


def test_the_standby_clip_is_asked_for_until_it_is_answered(isolated_home):
    """``clip_asked`` is a note of the app's (it asked, and was answered either way), not a
    setting of the panel: it is false until the app writes it, and the template only mentions it."""
    from libre_panel.config import DeviceConfig, edit_config

    assert load_config().device.video.clip_asked is False
    assert parse_config(tomllib.loads(DEFAULT_CONFIG_TOML)).device.video.clip_asked is False
    assert "# clip_asked = false" in DEFAULT_CONFIG_TOML
    assert parse_config({"video": {"clip_asked": True}}).device.video.clip_asked is True
    for bad in (1, "yes", None):
        with pytest.raises(ConfigError, match="video.clip_asked"):
            parse_config({"video": {"clip_asked": bad}})
    # it goes under its note in the template, and the rest of the file stays the person's
    path = write_default_config()
    edit_config(("value", "video.clip_asked", True))
    text = path.read_text(encoding="utf-8")
    assert tomllib.loads(text)["video"] == {"clip_asked": True}
    assert text.index("# clip_asked = false") < text.index("clip_asked = true")
    assert "# Libre Panel configuration" in text and load_config().device.video.clip_asked is True
    # a note is no difference to the open panel: only the settings of the panel are compared
    shown = DeviceConfig(video=load_config().device.video)
    assert shown == DeviceConfig(video=parse_config({}).device.video)


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
        {"device": {"still": "gif"}},
        {"device": {"still": True}},
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


def test_a_setting_is_written_though_config_toml_is_open_for_a_moment(isolated_home, monkeypatch):
    """Windows cannot replace a file another reader holds open ("access denied"): the panel
    reads config.toml when it changed, and a setting changed just then was refused. The
    write tries again a moment later."""
    import libre_panel.config as config_module
    from libre_panel.config import load_config, set_config_value, write_default_config

    path = write_default_config()
    real, refused = config_module.os.replace, []

    def busy(source, target):
        if len(refused) < 3:  # open for reading the first three times
            refused.append(target)
            raise PermissionError(13, "Access is denied", str(target))
        return real(source, target)

    monkeypatch.setattr(config_module.os, "replace", busy)
    monkeypatch.setattr(config_module, "REPLACE_WAIT_S", 0)
    set_config_value("services.enabled", ["notices"], path)
    assert len(refused) == 3 and load_config(path).services.enabled == ["notices"]
    refused.clear()
    monkeypatch.setattr(config_module, "REPLACE_TRIES", 2)  # held longer than it waits: refused
    with pytest.raises(PermissionError):
        set_config_value("services.enabled", [], path)


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


def test_a_write_that_comes_while_another_is_under_way_keeps_both(isolated_home, monkeypatch):
    """The tray and each request of the app write config.toml from threads of their own. A
    write that came while another was under way started from the file as it was, and the
    one that finished last undid the other's change (and both used one temporary file)."""
    import threading

    from libre_panel import config as config_module
    from libre_panel.config import set_config_value

    path = write_default_config()
    real_write, other = config_module._write, []

    def write(target, text):
        if not other:  # the tray sets the brightness while the app writes the rotation
            other.append(threading.Thread(target=set_config_value, args=("device.brightness", 30)))
            other[0].start()
            other[0].join(0.5)  # it finishes first, unless it has to wait for this write
        real_write(target, text)

    monkeypatch.setattr(config_module, "_write", write)
    set_config_value("device.rotate", 180)
    other[0].join(5)
    config = load_config(path)
    assert (config.device.rotate, config.device.brightness) == (180, 30)


def test_a_portable_copy_keeps_its_settings_beside_the_program(tmp_path, monkeypatch):
    from libre_panel import config as config_module

    program = tmp_path / "stick" / "libre-panel.exe"
    program.parent.mkdir()
    program.write_bytes(b"")
    assert config_module.portable_dir(str(program)) is None  # no portable.txt: installed
    (program.parent / "portable.txt").write_text("portable", encoding="utf-8")
    assert config_module.portable_dir(str(program)) == program.parent / "data"
    assert config_module.portable_dir() is None  # (run from Python: never)

    monkeypatch.delenv("LIBRE_PANEL_HOME", raising=False)
    monkeypatch.setattr(config_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_module.sys, "executable", str(program))
    usual = tmp_path / "usual"
    monkeypatch.setattr(config_module, "usual_dir", lambda: usual)
    assert config_module.config_dir() == program.parent / "data"
    assert config_module.take_over_into_portable() is None  # nothing to take over
    (usual / "themes" / "mine").mkdir(parents=True)
    (usual / "config.toml").write_text('theme = "mine"\n', encoding="utf-8")
    (usual / "logs").mkdir()
    (usual / "logs" / "libre-panel.log").write_text("old", encoding="utf-8")
    assert config_module.take_over_into_portable() == usual
    data = program.parent / "data"
    assert (data / "config.toml").read_text(encoding="utf-8") == 'theme = "mine"\n'
    assert (data / "themes" / "mine").is_dir() and not (data / "logs").exists()
    (usual / "config.toml").write_text('theme = "other"\n', encoding="utf-8")
    assert config_module.take_over_into_portable() is None  # once: then each keeps its own
    assert "mine" in (data / "config.toml").read_text(encoding="utf-8")
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(tmp_path / "own"))
    assert config_module.config_dir() == tmp_path / "own"  # still first

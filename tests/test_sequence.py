"""Screens in turn: themes the panel shows one after another, each for its seconds, minutes or
hours, and from the top again after the last ([sequence] in config.toml)."""

import tomllib
from types import SimpleNamespace

import pytest
from test_default_theme_loop import demo_config, loop_for, step_until
from test_editor_server import request, server  # noqa: F401 (a fixture)

from libre_panel import app as app_module
from libre_panel.config import (
    ConfigError,
    ModeConfig,
    SequenceConfig,
    edit_config,
    load_config,
    parse_config,
    write_default_config,
)
from libre_panel.sequence import SHORTEST_S, Rotation, Step, StepError, parse_steps, position

STEPS = [Step("almanac", 300), Step("nimbus", 300), Step("studio", 600)]


# -- the steps ---------------------------------------------------------------------------------


def test_a_step_lasts_its_seconds_minutes_and_hours_together():
    steps = parse_steps([{"theme": "almanac", "minutes": 5}, {"theme": "nimbus", "seconds": 30},
                         {"theme": "studio", "hours": 1, "minutes": 30},
                         {"theme": "lumen", "minutes": 1.5}])  # fmt: skip
    assert [s.seconds for s in steps] == [300, 30, 5400, 90]
    # written back in the largest unit that takes it whole
    assert [s.to_table() for s in steps] == [
        {"theme": "almanac", "minutes": 5}, {"theme": "nimbus", "seconds": 30},
        {"theme": "studio", "minutes": 90}, {"theme": "lumen", "seconds": 90},
    ]  # fmt: skip
    assert Step("pico", 7200).to_table() == {"theme": "pico", "hours": 2}


@pytest.mark.parametrize(
    "raw, says",
    [
        ({"minutes": 5}, "needs a theme"),
        ({"theme": "", "minutes": 5}, "needs a theme"),
        ({"theme": "pico"}, f"a step lasts {SHORTEST_S} s"),
        ({"theme": "pico", "seconds": 2}, f"a step lasts {SHORTEST_S} s"),
        ({"theme": "pico", "days": 2}, "unknown key 'days'"),
        ({"theme": "pico", "minutes": "5"}, "expected a number of minutes"),
        ({"theme": "pico", "minutes": True}, "expected a number of minutes"),
        ({"theme": "pico", "hours": 200}, "a step lasts"),
        ("pico", "expected a table"),
    ],
)
def test_a_step_that_cannot_be_shown_says_why(raw, says):
    with pytest.raises(StepError, match=says):
        parse_steps([raw])


def test_the_sequence_goes_round():
    assert position(STEPS, 0) == position(STEPS, 1200)  # a round is 20 minutes
    assert position(STEPS, 299).theme == "almanac" and position(STEPS, 300).theme == "nimbus"
    place = position(STEPS, 700)
    assert (place.index, place.theme, place.left_s) == (2, "studio", 500)
    assert position(STEPS, 1199.5).theme == "studio"


def test_it_begins_with_the_first_step_and_again_when_the_steps_change():
    clock = SimpleNamespace(now=1000.0)
    rotation = Rotation(lambda: clock.now)
    assert rotation.where(STEPS).theme == "almanac"
    clock.now += 450
    assert rotation.where(STEPS).theme == "nimbus"
    changed = [Step("pico", 20), *STEPS]
    assert rotation.where(changed).theme == "pico"  # new steps: from the top
    clock.now += 21
    assert rotation.where(changed).theme == "almanac"
    # the steps before, asked while the new ones are made ready, go on where they were
    assert rotation.where(STEPS).theme == "nimbus"
    assert rotation.where(changed).theme == "almanac"
    clock.now += Rotation.FORGET_S  # not asked for a minute: they begin afresh
    assert rotation.where(STEPS).theme == "almanac"
    assert rotation.where([]) is None


# -- config.toml -------------------------------------------------------------------------------


def test_config_toml_has_the_screens_in_turn_on_or_off():
    data = tomllib.loads("""
[sequence]
[[sequence.steps]]
theme = "almanac"
minutes = 5
[[sequence.steps]]
theme = "studio"
minutes = 10
""")
    config = parse_config(data)
    assert config.sequence.on and config.sequence.active
    assert config.sequence.steps == [Step("almanac", 300), Step("studio", 600)]
    data["sequence"]["on"] = False
    assert not parse_config(data).sequence.active
    assert not parse_config({}).sequence.active  # none: the theme of config.toml
    with pytest.raises(ConfigError, match=r"sequence.steps\[0\]: needs a theme"):
        parse_config({"sequence": {"steps": [{"minutes": 5}]}})
    with pytest.raises(ConfigError, match="sequence.on"):
        parse_config({"sequence": {"on": "yes"}})


def test_the_app_writes_them_into_the_file_the_user_has(isolated_home):
    path = isolated_home / "config.toml"
    isolated_home.mkdir(parents=True)
    write_default_config(path)
    assert "# [sequence]" in path.read_text("utf-8")  # the default file says how
    tables = [step.to_table() for step in STEPS]
    edit_config(("value", "sequence.on", True), ("tables", "sequence.steps", tables), path=path)
    assert load_config(path).sequence == SequenceConfig(True, STEPS)
    edit_config(("value", "sequence.on", False), ("tables", "sequence.steps", []), path=path)
    assert load_config(path).sequence == SequenceConfig(False, [])


# -- on the panel ------------------------------------------------------------------------------


def test_the_theme_a_service_or_a_mode_shows_goes_first():
    config = demo_config(theme="slate", sequence=SequenceConfig(True, STEPS))
    rotation = Rotation(lambda: 0.0)
    nobody = SimpleNamespace(theme=None, mode=None)
    assert app_module._theme_name(config, nobody, None, rotation) == "almanac"
    assert app_module._theme_name(config, nobody, None, None) == "slate"
    service = SimpleNamespace(theme="orbit", mode=None)
    assert app_module._theme_name(config, service, None, rotation) == "orbit"
    config.modes["game"] = ModeConfig(theme="pico")
    game = SimpleNamespace(theme=None, mode="game")
    assert app_module._theme_name(config, game, None, rotation) == "pico"
    config.sequence.on = False
    assert app_module._theme_name(config, nobody, None, rotation) == "slate"


def test_the_panel_shows_the_screens_in_turn_on_its_clock(isolated_home):
    steps = [Step("pico", 5), Step("orbit", 8)]
    loop, clock = loop_for(demo_config(theme="slate", sequence=SequenceConfig(True, steps)))
    try:
        assert loop.status.theme == "pico"
        loop.step(clock.now)
        assert loop.status.sequence == {"index": 0, "theme": "pico", "left_s": 5.0}
        clock.now += 4.8
        loop.step(clock.now)
        assert loop.status.theme == "pico"  # still its five seconds
        assert step_until(loop, clock, lambda: loop.status.theme == "orbit")
        assert loop.status.sequence["index"] == 1
        clock.now += 7.5
        assert step_until(loop, clock, lambda: loop.status.theme == "pico")  # from the top
    finally:
        loop.close()


# -- in the app --------------------------------------------------------------------------------


def test_the_app_reads_and_writes_the_screens_in_turn(server, isolated_home):  # noqa: F811
    status, data = request(server, "GET", "/api/sequence")
    assert status == 200 and data["on"] is True and data["steps"] == [] and data["now"] is None
    assert data["shortest_s"] == SHORTEST_S
    steps = [{"theme": "almanac", "seconds": 300}, {"theme": "nimbus", "seconds": 45}]
    status, data = request(server, "POST", "/api/sequence", {"on": True, "steps": steps})
    assert status == 200 and data["on"] is True and data["steps"] == steps
    text = (isolated_home / "config.toml").read_text("utf-8")
    assert 'theme = "almanac"\nminutes = 5' in text and 'theme = "nimbus"\nseconds = 45' in text
    pages = request(server, "GET", "/api/pages")[1]
    assert pages["pages"][0]["sequence"] is True
    # one theme chosen on Start: the screens in turn stop, and are kept for later
    status, _ = request(server, "POST", "/api/pages", {"page": "normal", "theme": "lumen"})
    assert status == 200
    config = load_config(isolated_home / "config.toml")
    assert config.theme == "lumen" and not config.sequence.on and len(config.sequence.steps) == 2
    # without steps they are off
    status, data = request(server, "POST", "/api/sequence", {"on": True, "steps": []})
    assert status == 200 and data["on"] is False and data["steps"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"steps": []},
        {"on": "yes", "steps": []},
        {"on": True, "steps": "almanac"},
        {"on": True, "steps": ["almanac"]},
        {"on": True, "steps": [{"theme": "almanac", "seconds": 2}]},
        {"on": True, "steps": [{"theme": "no-such-theme", "seconds": 60}]},
    ],
)
def test_the_app_refuses_steps_that_cannot_be_shown(server, isolated_home, body):  # noqa: F811
    status, data = request(server, "POST", "/api/sequence", body)
    assert status == 400, data


def test_a_theme_chosen_in_the_tray_stops_the_screens_in_turn_and_keeps_them(isolated_home):
    from libre_panel.config import set_active_theme

    path = isolated_home / "config.toml"
    isolated_home.mkdir(parents=True)
    write_default_config(path)
    tables = [step.to_table() for step in STEPS]
    edit_config(("value", "sequence.on", True), ("tables", "sequence.steps", tables), path=path)
    set_active_theme("orbit", path)
    config = load_config(path)
    assert config.theme == "orbit" and not config.sequence.on and config.sequence.steps == STEPS

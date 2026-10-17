"""A theme's switches: what the person may turn on or off in it ("switches" in theme.json,
Look shows them), kept in config.toml for the theme and all its versions ([switches.<theme>]);
the widgets that need ``switch.<name>`` show by it. Lumen on the 12.3" has frames around every
part, on to begin with."""

import pytest

from libre_panel.app import MainLoop
from libre_panel.config import ConfigError, parse_config
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import (
    ThemeError,
    builtin_themes_dir,
    find_theme,
    load_theme,
    parse_theme,
    switches_for,
)

SNAP = demo_snapshot(fixed_time=1_700_000_000)


def theme_with(switches, *widgets):
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": 400, "height": 200},
            "switches": switches,
            "widgets": list(widgets),
        }
    )


def rect(name, needs):
    return {"type": "rect", "id": name, "x": 10, "y": 10, "w": 50, "h": 50, "needs": needs}


def drawn(theme, **chosen):
    renderer = Renderer(theme, preview=True)
    renderer.switches = chosen
    try:
        return set(renderer.render(SNAP)[1])
    finally:
        renderer.close()


def test_a_theme_says_its_switches_and_keeps_them():
    theme = theme_with({"frames": {"label": "Frames around every part", "on": True},
                        "dots": {"label": "Dots", "on": False}})  # fmt: skip
    assert theme.switches["frames"] == {"label": "Frames around every part", "on": True}
    assert theme.switches["dots"]["on"] is False
    assert parse_theme(theme.to_dict()).switches == theme.switches
    assert "switches" not in theme_with(None).to_dict()


@pytest.mark.parametrize(
    "bad",
    [["frames"], {"Frames!": {"on": True}}, {"frames": True}, {"frames": {"on": "yes"}},
     {f"s{i}": {"on": True} for i in range(9)}],
)  # fmt: skip
def test_a_switch_that_is_not_one_is_refused(bad):
    with pytest.raises(ThemeError):
        theme_with(bad)


def test_a_widget_shows_by_the_switch_as_the_theme_begins_or_as_the_person_chose():
    theme = theme_with({"frames": {"label": "Frames", "on": True}},
                       rect("frame", "switch.frames"), rect("bare", "!switch.frames"),
                       rect("other", "switch.unknown"))  # fmt: skip
    assert drawn(theme) == {"frame"}  # (a switch the theme does not have is off)
    assert drawn(theme, frames=False) == {"bare"}
    assert drawn(theme, frames=True) == {"frame"}


def test_a_switch_is_no_sensor_the_renderer_asks_for():
    from libre_panel.render.renderer import _reads

    assert _reads([rect("frame", "switch.frames, cpu.load")]) == {"cpu.load"}


def test_config_keeps_the_choice_by_theme():
    config = parse_config({"switches": {"lumen": {"frames": False}}})
    assert config.switches == {"lumen": {"frames": False}}
    assert parse_config({}).switches == {}
    for bad in ({"lumen": {"frames": "no"}}, {"lumen": True}, {"Lumen!": {"frames": True}}):
        with pytest.raises(ConfigError):
            parse_config({"switches": bad})


def test_the_choice_holds_for_every_version_of_the_theme():
    chosen = {"lumen": {"frames": False}, "round-report": {"x": True}}
    for name in ("lumen", "lumen-1920x720", "lumen-vertical"):
        assert switches_for(load_theme(find_theme(name)), chosen) == {"frames": False}
    assert switches_for(load_theme(find_theme("round-report-1920x720")), chosen) == {"x": True}
    assert switches_for(parse_theme(load_theme(find_theme("lumen")).to_dict()), chosen) == {}


def test_a_change_of_a_switch_makes_the_panel_draw_the_theme_anew():
    theme = load_theme(find_theme("lumen-1920x720"))
    on, off = parse_config({}), parse_config({"switches": {"lumen": {"frames": False}}})
    assert MainLoop._warm_key(theme, on) != MainLoop._warm_key(theme, off)


def test_lumen_on_the_12_3_inch_has_frames_around_every_part_to_begin_with():
    theme = load_theme(builtin_themes_dir() / "lumen-1920x720")
    assert theme.switches == {"frames": {"label": "Frames around every part", "on": True}}
    frames = {w["id"] for w in theme.widgets if w.get("needs") == "switch.frames"}
    assert frames == {"frame-mid", "frame-rings", "frame-net"}
    assert all(w["locked"] for w in theme.widgets if w["id"] in frames)  # backdrops
    assert frames <= drawn(theme) and not frames & drawn(theme, frames=False)

"""A panel that stands on end (device.orientation): each theme in its version for it."""

import pytest
from test_default_theme_loop import demo_config, loop_for, step_until

from libre_panel.config import ConfigError, ModeConfig, parse_config
from libre_panel.plugins import PluginHost
from libre_panel.theme.model import find_theme, list_themes, load_theme, theme_for


def test_config_toml_says_how_the_panel_is_mounted():
    assert parse_config({}).device.orientation == ""  # as each theme is made
    for way in ("landscape", "portrait"):
        assert parse_config({"device": {"orientation": way}}).device.orientation == way
    with pytest.raises(ConfigError, match="device.orientation"):
        parse_config({"device": {"orientation": "upright"}})


def test_each_screen_of_the_bars_has_its_version_for_a_bar_on_end():
    ids = {item["id"] for item in list_themes()}
    for name in ("lumen", "encore", "nimbus", "almanac", "almanac-analog", "arena"):
        assert theme_for(name, "portrait") == f"{name}-vertical"
        assert theme_for(f"{name}-vertical", "landscape") == name
        assert (
            theme_for(name, "") == name and theme_for(f"{name}-vertical", "") == f"{name}-vertical"
        )
        upright = load_theme(find_theme(f"{name}-vertical"))
        assert (upright.width, upright.height, upright.orientation) == (480, 1920, "portrait")
        assert f"{name}-vertical" in ids
    assert theme_for("slate", "portrait") == "slate"  # none of its own: the theme as it is


def test_a_panel_on_end_shows_the_versions_for_it(isolated_home):
    host = PluginHost()
    config = demo_config(theme="encore", modes={"idle": ModeConfig(on=False)})
    config.device.orientation = "portrait"
    loop, clock = loop_for(config, host=host)
    try:
        assert step_until(loop, clock, lambda: loop.status.theme == "encore-vertical")
        host.set_mode("game", by="game")  # a page's theme too (game mode names Arena)
        config.modes["game"] = ModeConfig(theme="arena")
        assert step_until(loop, clock, lambda: loop.status.theme == "arena-vertical")
    finally:
        loop.close()
        host.close()

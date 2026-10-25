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
    names = ("lumen", "studio", "encore", "nimbus", "almanac", "almanac-analog", "arena",
             "round-report")  # fmt: skip
    for name in names:
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


TWELVE = ("lumen", "arena", "encore", "almanac", "round-report")  # their 12.3″ and 5.2″ versions
SIZES = {"1920x720": ((1920, 720), "turing-12.3-usb"), "1280x720": ((1280, 720), "turing-5.2-usb")}


def test_a_panel_of_another_size_shows_the_versions_for_its_size():
    """The 12.3″ (1920x720) shows Lumen, Arena, Encore, Almanac and the round report in their
    versions for it, laid out for its height with larger type; any other panel the theme
    itself, and a panel on end its version for that."""
    for name in TWELVE:
        for suffix, (size, model) in SIZES.items():
            own = f"{name}-{suffix}"
            assert theme_for(name, "landscape", size) == own
            assert theme_for(name, "", size) == own
            assert theme_for(own, "landscape", (1920, 480)) == name  # back on a bar
            assert theme_for(own, "portrait", (480, 1920)) == f"{name}-vertical"
            assert theme_for(own, "") == own  # the panel not known yet: as chosen
            version = load_theme(find_theme(own))
            assert ((version.width, version.height), version.model) == (size, model)
        # one size's version on a panel of the other size: that size's own
        assert theme_for(f"{name}-1920x720", "landscape", (1280, 720)) == f"{name}-1280x720"
    assert theme_for("slate", "landscape", (1920, 720)) == "slate"  # none of its own


def test_a_12_3_inch_panel_gets_its_versions_in_the_loop(isolated_home):
    host = PluginHost()
    config = demo_config(theme="lumen", modes={"idle": ModeConfig(on=False)})
    config.device.model = "turing-12.3-usb"
    loop, clock = loop_for(config, host=host)
    try:
        assert step_until(loop, clock, lambda: loop.status.theme == "lumen-1920x720")
        assert loop.status.size == (1920, 720)
        host.set_mode("game", by="game")
        config.modes["game"] = ModeConfig(theme="arena")
        assert step_until(loop, clock, lambda: loop.status.theme == "arena-1920x720")
    finally:
        loop.close()
        host.close()


@pytest.mark.parametrize("suffix", SIZES)
@pytest.mark.parametrize("name", TWELVE)
def test_the_versions_for_a_size_draw_whole_with_nothing_running_together(name, suffix):
    """Larger type in the same width: nothing runs into anything it did not touch on the bar
    (the frame rate's figure ran into the words beside it), and nothing past the frame. A card
    that holds a part is no part running into it: a version may set its cards out otherwise
    (the round report's has who held the rate down in the round's card, the bar beside the
    share of each frame rate)."""
    import json

    from test_libre_themes import drawn, overlapping

    from libre_panel.theme.model import builtin_themes_dir

    def draw(theme_id):
        folder = builtin_themes_dir() / theme_id
        return drawn(json.loads((folder / "theme.json").read_text("utf-8")), folder)

    (width, height), _ = SIZES[suffix]
    theme, renderer, frame, boxes = draw(f"{name}-{suffix}")
    assert frame.size == (width, height) and theme.warnings == [] and renderer.warnings == []
    for key, (x, y, w, h) in boxes.items():
        assert x >= 0 and y >= 0 and x + w <= width and y + h <= height, (key, (x, y, w, h))
    bar, _, _, bar_boxes = draw(name)
    kinds = {w["id"]: w for w in theme.widgets}

    def held(pair):
        outer, inner = sorted(pair, key=lambda wid: boxes[wid][2] * boxes[wid][3], reverse=True)
        (ax, ay, aw, ah), (bx, by, bw, bh) = boxes[outer], boxes[inner]
        card = kinds[outer]["type"] == "rect" and outer.endswith("-card")
        return card and ax <= bx and ay <= by and bx + bw <= ax + aw and by + bh <= ay + ah

    new = {
        pair for pair in overlapping(theme, boxes) - overlapping(bar, bar_boxes) if not held(pair)
    }
    assert not new, sorted(tuple(sorted(pair)) for pair in new)

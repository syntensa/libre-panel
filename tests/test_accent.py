"""config.toml accent: every theme in Windows' accent colour or one of the person's."""

import pytest

from libre_panel import accent, app
from libre_panel.config import Config, ConfigError, parse_config
from libre_panel.theme.model import builtin_themes_dir, load_theme, with_accent


def test_a_dark_accent_is_lightened_for_a_dark_panel_and_a_light_one_kept():
    assert accent.readable("#2EE6D6") == "#2EE6D6"
    dark = accent.readable("#0063B1")  # Windows' default blue
    assert dark != "#0063B1" and dark.startswith("#")
    import colorsys

    r, g, b = (int(dark[i : i + 2], 16) / 255 for i in (1, 3, 5))
    assert colorsys.rgb_to_hls(r, g, b)[1] >= accent.LIGHTEST_NEEDED - 0.01


def test_the_choice_stands_for_a_colour_or_for_the_theme_s_own(monkeypatch):
    assert accent.accent_of("") is None and accent.accent_of("#e0457b") == "#E0457B"
    monkeypatch.setattr(accent, "system_accent", lambda: "#FF8800")
    assert accent.accent_of("system") == "#FF8800"
    monkeypatch.setattr(accent, "system_accent", lambda: None)  # not Windows, or it says none
    assert accent.accent_of("system") is None


def test_windows_accent_colour_is_read_as_its_registry_keeps_it(monkeypatch):
    import sys
    import types

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    registry = types.SimpleNamespace(
        HKEY_CURRENT_USER=0,
        OpenKey=lambda root, path: Key(),
        QueryValueEx=lambda key, name: (0xFFD67800, 4),  # 0xAABBGGRR: red 00, green 78, blue D6
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(accent.sys, "platform", "win32")
    assert accent.system_accent() == accent.readable("#0078D6")


def test_the_theme_s_accent_and_what_is_in_its_colour_take_the_choice():
    theme = load_theme(builtin_themes_dir() / "lumen")
    painted = with_accent(theme, "#E0457B")
    assert painted.palette["accent"] == "#E0457B" and painted.palette["cpu"] == "#E0457B"
    assert painted.palette["gpu"] == theme.palette["gpu"]  # another colour: as it is
    assert theme.palette["accent"] != "#E0457B"  # the theme itself is left as it was
    assert with_accent(theme, None) is theme


def test_the_setting_is_read_and_checked():
    assert parse_config({}).accent == "" and parse_config({"accent": "system"}).accent == "system"
    assert parse_config({"accent": " #E0457B "}).accent == "#E0457B"
    for bad in ("red", "#12345", 3):
        with pytest.raises(ConfigError):
            parse_config({"accent": bad})


def test_the_panel_draws_its_theme_in_the_chosen_accent():
    theme = load_theme(builtin_themes_dir() / "lumen")
    loop = object.__new__(app.MainLoop)  # (only what _renderer needs)
    loop.warmup, loop.motion, loop.beats = app._Warmup(), app.Motion(), app.fan.Beats()
    renderer = loop._renderer(theme, Config(theme="lumen", accent="#E0457B"))
    try:
        assert renderer.theme.palette["accent"] == "#E0457B"
    finally:
        renderer.close()
    assert app.MainLoop._warm_key(theme, Config(accent="#E0457B")) != app.MainLoop._warm_key(
        theme, Config()
    )  # a theme warmed in other colours is not taken

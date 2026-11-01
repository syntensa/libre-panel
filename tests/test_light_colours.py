"""Every theme in light colours, for a white PC (config.toml ``light_colours``,
theme.light_colours): each colour by its job, the light of dark grounds drawn for a light one."""

import pytest
from PIL import ImageColor

from libre_panel.config import Config, ConfigError, parse_config
from libre_panel.theme import light_colours as lc
from libre_panel.theme.model import find_theme, list_themes, load_theme, with_light_colours
from libre_panel.theme.modules import LOOKS, expand


def lightness(colour):
    return lc.lch(ImageColor.getrgb(colour)[:3])[0]


def contrast(a, b):
    """WCAG's contrast of two colours (1 to 21)."""

    def luminance(colour):
        def linear(v):
            v /= 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

        r, g, b = ImageColor.getrgb(colour)[:3]
        return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_a_dark_look_turned_light_comes_near_the_one_made_by_hand_for_a_light_ground():
    """Arctic (dark) turned light against Paper, made for a light ground: role by role within
    0.05 of its lightness, so the light colours are made as a designer makes them."""
    turned = lc.light_palette(LOOKS["arctic"]["palette"])
    paper = LOOKS["paper"]["palette"]
    for role, made in paper.items():
        assert abs(lightness(turned[role]) - lightness(made)) <= 0.05, role
    assert lightness(turned["surface"]) > lightness(turned["bg"]) > lightness(turned["bg2"])


def test_every_built_in_theme_reads_in_light_colours():
    """Over every built-in theme: the texts at least 4:1 against the ground and the cards, the
    colours of the readings at least 2.5:1 (and a reading always has its number beside it)."""
    texts, colours = ("text", "text2", "text3"), ("accent", "cpu", "gpu", "mem", "disk", "net",
                                                   "warn", "crit")  # fmt: skip
    seen = 0
    for item in list_themes():
        if not item.get("builtin"):
            continue
        palette = lc.light_palette(load_theme(find_theme(item["id"])).palette)
        if "text" not in palette or "bg" not in palette:
            continue
        seen += 1
        grounds = (palette["bg"], palette.get("surface", palette["bg"]))
        for role in texts + colours:
            if role in palette:
                least = 4.0 if role in texts else 2.5
                worst = min(contrast(palette[role], ground) for ground in grounds)
                assert worst >= least, (item["id"], role, palette[role], round(worst, 2))
    assert seen > 40


def test_colours_by_their_job_their_opacity_kept():
    assert lightness(lc.light_colour("#0A0E13", "bg")) > 0.9  # the ground: light
    assert lightness(lc.light_colour("#000000")) > 0.98  # black, the plainest ground: white
    assert lightness(lc.light_colour("#FFFFFF")) < 0.3  # the brightest text: the darkest
    teal = lc.lch(ImageColor.getrgb(lc.light_colour("#2EE6D6"))[:3])
    assert 0.5 < teal[0] < 0.65 and abs(teal[2] - lc.lch((46, 230, 214))[2]) < 0.05  # its hue
    assert lc.light_colour("#FFFFFF80").endswith("80")  # half see-through stays so
    assert lc.light_colour("@cpu") == "@cpu" and lc.light_colour("not a colour") == "not a colour"
    assert lightness(lc.light_colour("white")) < 0.3  # a colour by name too


def test_a_theme_in_light_colours_is_drawn_for_a_light_ground_and_stays_as_made():
    theme = load_theme(find_theme("lumen"))
    light = with_light_colours(theme, True)
    assert with_light_colours(theme, False) is theme
    assert theme.palette["bg"] == "#0A0E13" and lightness(light.palette["bg"]) > 0.9
    assert light.background_image is None and theme.background_image is not None
    assert light.style["glow"] == pytest.approx(theme.style["glow"] * lc.GLOW)
    parts, _boxes = expand(light)
    finishes = {part.get("finish") for part in parts if part["type"] != "symbol"}
    assert "lit" not in finishes  # the light of rings and bars: drawn flat on a light ground
    assert "lit" in {part.get("finish") for part in expand(theme)[0]}  # (as made: lit)


def test_the_weathers_bodies_stay_lit_in_a_light_grey():
    light = with_light_colours(load_theme(find_theme("nimbus")), True)
    symbols = [part for part in expand(light)[0] if part["type"] == "symbol"]
    assert symbols
    for symbol in symbols:
        assert symbol["finish"] == "lit" and abs(lightness(symbol["color"]) - lc.BODY_L) < 0.02


def test_the_setting_and_the_panels_loop():
    from libre_panel.app import MainLoop

    assert Config().light_colours is False  # as made unless asked
    assert parse_config({"light_colours": True}).light_colours is True
    with pytest.raises(ConfigError, match="light_colours"):
        parse_config({"light_colours": "yes"})
    config = Config()
    config.light_colours = True
    dressed = MainLoop._dressed(load_theme(find_theme("arena")), config, None)
    assert lightness(dressed.palette["bg"]) > 0.9 and lightness(dressed.palette["text"]) < 0.3

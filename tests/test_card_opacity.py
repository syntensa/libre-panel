"""Cards that let the background through (config.toml ``card_opacity``, a theme's style
``card_opacity``): their surface, not what is on them."""

import numpy as np
import pytest

from libre_panel.config import Config, ConfigError, parse_config
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import (
    THEME_FORMAT,
    ThemeError,
    find_theme,
    load_theme,
    parse_theme,
    with_card_opacity,
)


def card_theme(style=None):
    data = {
        "format": THEME_FORMAT,
        "display": {"width": 200, "height": 100},
        "palette": {"surface": "#202830", "text": "#FFFFFF"},
        "background": {"color": "#FF0000"},
        "widgets": [
            {"type": "rect", "id": "card", "x": 0, "y": 0, "w": 200, "h": 100,
             "color": "@surface", "outline": None},
            {"type": "rect", "id": "plate", "x": 150, "y": 10, "w": 40, "h": 30,
             "color": "#202830", "outline": None},
            {"type": "rect", "id": "mark", "x": 10, "y": 10, "w": 20, "h": 20,
             "color": "@text", "outline": None},
        ],
    }  # fmt: skip
    if style is not None:
        data["style"] = style
    return parse_theme(data)


def drawn(theme):
    frame, _ = Renderer(theme).render(Snapshot(readings={}))
    return np.asarray(frame.convert("RGB"), np.int16)


def test_cards_cover_the_background_as_the_theme_has_them_by_default():
    theme = card_theme()
    assert with_card_opacity(theme, 0) is theme  # 0: each theme's own
    frame = drawn(theme)
    assert tuple(frame[60, 60]) == (0x20, 0x28, 0x30)  # the card, all of it
    seen = with_card_opacity(card_theme({"card_opacity": 0.7}), 100)
    assert seen.style["card_opacity"] == 1.0  # the person's 100 %: all of it again
    assert tuple(drawn(seen)[60, 60]) == (0x20, 0x28, 0x30)


def test_a_card_lets_the_background_through_but_not_what_is_on_it():
    frame = drawn(with_card_opacity(card_theme(), 60))
    expected = np.round(np.array([0x20, 0x28, 0x30]) * 0.6 + np.array([255, 0, 0]) * 0.4)
    assert np.abs(frame[60, 60] - expected).max() <= 1  # the red background through the card
    assert tuple(frame[20, 20]) == (255, 255, 255)  # what is on the card: as it is
    assert tuple(frame[20, 170]) == (0x20, 0x28, 0x30)  # a rect in its own colour: not a card


def test_a_theme_can_ask_for_it_in_its_style_and_the_person_has_the_last_word():
    theme = card_theme({"card_opacity": 0.8})
    assert theme.style["card_opacity"] == 0.8
    see = drawn(theme)[60, 60]
    assert see[0] > 0x20  # some red through it
    assert with_card_opacity(theme, 50).style["card_opacity"] == 0.5
    assert with_card_opacity(theme, 10).style["card_opacity"] == 0.4  # at least 40 %
    with pytest.raises(ThemeError, match="card_opacity"):
        card_theme({"card_opacity": 0.1})


def test_module_cards_let_the_background_through_too():
    from libre_panel.theme.modules import expand

    theme = with_card_opacity(load_theme(find_theme("nimbus")), 100)  # cards of modules (glass)
    card = next(w for w in expand(theme)[0] if w["id"].endswith("/card"))
    lighter = next(w for w in expand(with_card_opacity(theme, 50))[0] if w["id"] == card["id"])
    alpha = int(card["color"][7:9], 16)
    assert int(lighter["color"][7:9], 16) == round(alpha * 0.5)
    assert lighter["outline"] == card["outline"]  # its outline as it was


def test_libre_panels_own_themes_let_a_little_of_the_background_through():
    """Just enough to be seen, not so far that reading suffers (ROADMAP, 3.)."""
    own = {"lumen-1920x720": 0.7, "almanac": 0.7, "nimbus": 0.7, "arena-1920x720": 0.8,
           "studio": 0.85, "agents": 0.85, "encore-1920x720": 0.9}  # fmt: skip
    for name, value in own.items():
        assert load_theme(find_theme(name)).style["card_opacity"] == value, name
    for name in ("contrast", "round-report"):
        assert (load_theme(find_theme(name)).style or {}).get("card_opacity", 1.0) == 1.0, name


def test_config_card_opacity():
    assert Config().card_opacity == 0  # each theme's own
    assert parse_config({"card_opacity": 70}).card_opacity == 70
    assert parse_config({"card_opacity": 0}).card_opacity == 0
    for wrong in (20, 101, -5):
        with pytest.raises(ConfigError, match="card_opacity"):
            parse_config({"card_opacity": wrong})


def test_the_panels_loop_dresses_every_theme_in_it():
    from libre_panel.app import MainLoop

    config = Config()
    config.card_opacity = 70
    dressed = MainLoop._dressed(card_theme(), config, None)
    assert dressed.style["card_opacity"] == 0.7

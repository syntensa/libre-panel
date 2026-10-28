"""The cards of the card slots (config.toml ``cards``): one choice for the slots that leave their
cards to the person (Lumen's, in each of its versions), and the AI activity card, on show by
itself while Claude Code or Codex works or waits for you."""

import pytest

from libre_panel.app import MainLoop
from libre_panel.config import DEFAULT_CARDS, ConfigError, load_config
from libre_panel.render.renderer import Renderer
from libre_panel.render.slot import AI_HOLD
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import THEME_FORMAT, builtin_themes_dir, load_theme, parse_theme
from libre_panel.theme.modules import follows_cards, slot_items, with_cards

OWN = ("lumen", "lumen-1280x720", "lumen-1920x180", "lumen-1920x720", "lumen-media-640x180",
       "lumen-pair", "lumen-pair-1280x720", "lumen-pair-1920x720", "lumen-pair-vertical",
       "lumen-vertical")  # fmt: skip


def configured(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def test_the_cards_are_a_list_of_cards_each_once(tmp_path):
    assert configured(tmp_path, "").cards == list(DEFAULT_CARDS) == [
        "playback", "ai", "forecast", "date",
    ]  # fmt: skip
    assert configured(tmp_path, 'cards = ["ai", "download"]\n').cards == ["ai", "download"]
    for bad in ('cards = ["nothing"]', 'cards = ["ai", "ai"]', "cards = []", 'cards = "ai"'):
        with pytest.raises(ConfigError, match="cards"):
            configured(tmp_path, bad + "\n")


def test_libre_panels_own_slots_take_the_persons_cards_and_one_of_its_own_keeps_them():
    for name in OWN:
        theme = load_theme(builtin_themes_dir() / name)
        assert follows_cards(theme), name
        slot = next(
            w for w in with_cards(theme, ["ai", "date"]).widgets if w.get("module") == "cards"
        )
        assert slot_items(slot) == [("ai", ""), ("date", "")], name
    for name in ("studio", "libre-default", "spur-studio"):  # (their date steps aside for one)
        own = load_theme(builtin_themes_dir() / name)
        assert not follows_cards(own) and with_cards(own, ["ai"]) is own


def slotted(cards, size=(640, 360)):
    theme = parse_theme({
        "format": THEME_FORMAT,
        "display": {"width": size[0], "height": size[1]},
        "widgets": [{"type": "module", "id": "card", "module": "cards", "col": 0, "row": 0,
                     "cols": 4, "rows": 3}],
    })  # fmt: skip
    renderer = Renderer(with_cards(theme, cards))
    return renderer, renderer._slots["card"]


def readings(**values):
    return Snapshot(
        readings={key.replace("_", "."): Reading(key, value) for key, value in values.items()}
    )


def test_the_ai_card_is_on_show_while_an_agent_works_or_waits_and_a_while_after():
    """Between two prompts an agent is idle for a moment: the card stays ``AI_HOLD`` seconds
    after the last one stopped, and then the next card that has something to show comes."""
    renderer, slot = slotted(["ai", "date"])
    renderer.render(readings(ai_claude_busy=0, ai_codex_busy=-1), 100.0)
    assert slot.shown == 1  # nobody at work: the calendar
    renderer.render(readings(ai_claude_busy=1, ai_codex_busy=-1), 101.0)
    assert slot.wants(readings(ai_claude_busy=1), 101.0) == 0  # working
    assert slot.wants(readings(ai_codex_busy=2), 102.0) == 0  # waiting for you
    assert slot.wants(readings(ai_claude_busy=0, ai_codex_busy=0), 102.0 + AI_HOLD - 1) == 0
    assert slot.wants(readings(ai_claude_busy=0, ai_codex_busy=0), 102.0 + AI_HOLD + 1) == 1
    assert slot.wants(Snapshot(), 500.0) == 1  # (no AI activity set up: never)


def test_music_comes_first_where_it_is_first():
    _renderer, slot = slotted(["playback", "ai", "date"])
    playing = readings(media_title="Song", media_playing=1, ai_claude_busy=1)
    assert slot.wants(playing, 10.0) == 0
    assert slot.wants(readings(ai_claude_busy=1), 11.0) == 1


def test_the_panel_draws_its_theme_with_the_persons_cards(tmp_path):
    config = configured(tmp_path, 'cards = ["forecast", "ai"]\n')
    theme = load_theme(builtin_themes_dir() / "lumen")
    dressed = MainLoop._dressed(theme, config, None)
    slot = next(w for w in dressed.widgets if w.get("module") == "cards")
    assert slot["items"] == "forecast\nai"
    other = configured(tmp_path, 'cards = ["ai"]\n')
    assert MainLoop._warm_key(theme, config) != MainLoop._warm_key(theme, other)  # (drawn anew)

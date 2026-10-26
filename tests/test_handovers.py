"""Transitions for every occasion (Settings → Panel → Transitions): the new ones draw every frame
in its size and in time, each switch takes the one set for where it comes from, and a
transition can be shown on the panel to see it."""

import time
from types import SimpleNamespace

import pytest
from PIL import Image

from libre_panel import app, transition_show
from libre_panel.config import ConfigError, parse_config
from libre_panel.render.handovers import HANDOVERS
from libre_panel.render.overlays import (
    BUILT_IN_TRANSITIONS,
    ELABORATE_TRANSITIONS,
    PLAIN_TRANSITIONS,
    handover,
    occasion_params,
    transition_choices,
)
from libre_panel.render.transitions import Entrance

OLD, NEW = (20, 40, 60), (220, 180, 40)


def frames(size):
    return Image.new("RGB", size, OLD), Image.new("RGB", size, NEW)


@pytest.mark.parametrize("name", sorted(HANDOVERS))
def test_each_new_transition_draws_every_frame_in_its_size(name):
    for size in ((1920, 480), (480, 1920), (64, 32)):
        old, new = frames(size)
        kind = HANDOVERS[name]()
        assert kind.name == name and kind.duration > 0
        drawn = [kind.frame(old, new, t / 10) for t in range(11)]
        assert all(out.size == size and out.mode == "RGB" for out in drawn), name
        assert drawn[-1].getpixel((size[0] // 2, size[1] // 2)) == NEW  # it ends on the new
        assert any(
            NEW in [c for _, c in out.getcolors(1 << 16) or []] for out in drawn[1:-1]
        ) or name in ("zoom", "glitch"), name  # fmt: skip (on the way, the new shows somewhere)


@pytest.mark.parametrize("name", sorted(HANDOVERS))
def test_a_frame_of_a_new_transition_is_quick_on_a_bar(name):
    old, new = frames((1920, 480))
    kind = HANDOVERS[name]()
    kind.frame(old, new, 0.1)  # (what is made once a switch)
    began = time.perf_counter()
    for step in range(1, 11):
        kind.frame(old, new, step / 11)
    assert (time.perf_counter() - began) / 10 < 0.05  # far below a frame at 30 fps, even on CI


def test_the_list_offers_plain_and_elaborate_ones_and_each_is_built_in():
    choices = transition_choices()
    assert [c["id"] for c in choices if c["group"] == "plain"] == list(PLAIN_TRANSITIONS)
    assert [c["id"] for c in choices if c["group"] == "elaborate"] == list(ELABORATE_TRANSITIONS)
    assert all(c["id"] in BUILT_IN_TRANSITIONS for c in choices)
    assert len(choices) >= 17


def test_transitions_in_config_toml():
    config = parse_config({"transition": "wipe"})  # the old place still names the default
    assert config.transition == config.transitions.default == "wipe"
    config = parse_config({"transitions": {"default": "iris", "speed": "slow", "media": "glitch",
                                           "game": "", "random": ["fade", "tiles"]}})  # fmt: skip
    assert config.transition == "iris" and config.transitions.speed == "slow"
    assert config.transitions.occasions["media"] == "glitch"
    assert config.transitions.occasions["game"] == ""  # its own: the game's entrance
    assert config.transitions.occasions["sequence"] == "light"  # as it comes
    assert config.transitions.random == ["fade", "tiles"]
    for bad in ({"speed": "warp"}, {"lightning": "fade"}, {"random": ["random"]},
                {"random": []}, {"media": "Not A Name"}):  # fmt: skip
        with pytest.raises(ConfigError):
            parse_config({"transitions": bad})


class Reading(SimpleNamespace):
    pass


def made():
    """What :func:`handover` asks to be made, kept, and the transition it would be."""
    asked = []

    def make(spec, context=None):
        asked.append(spec)
        name = spec[0] if isinstance(spec, tuple) else spec
        return (
            None
            if name == "cut"
            else BUILT_IN_TRANSITIONS[name](context, (spec[1] if isinstance(spec, tuple) else {}))
        )

    return asked, make


def test_each_switch_takes_the_transition_of_where_it_comes_from():
    config = parse_config({})
    asked, make = made()
    readings = {"media.artist": Reading(value="The Night Owls"),
                "media.title": Reading(value="Midnight Drive")}  # fmt: skip
    # music: the entrance with the cover and the track, and a word when it ends
    handover(config, "media", False, None, None, readings, "Encore", make=make)
    handover(config, "media", True, None, None, readings, "Lumen", make=make)
    assert asked == [
        (
            "entrance",
            {"kind": "media", "title": "The Night Owls – Midnight Drive", "logo": "media.cover"},
        ),
        ("entrance", {"kind": "media-end"}),
    ]
    asked.clear()
    # a game: what its service asked for (the entrance with its logo), unless one is set
    game = ("entrance", {"kind": "start", "title": "Doom"})
    handover(config, "game", False, game, None, make=make)
    config.transitions.occasions["game"] = "glitch"
    handover(config, "game", False, game, None, make=make)
    # the screens in turn: the line of light; the autopilot its own; a theme chosen: the default
    handover(config, "sequence", False, None, None, make=make)
    handover(config, "autopilot", False, "tube", None, make=make)
    handover(config, "manual", False, None, None, make=make)
    assert asked == [game, "glitch", "light", "tube", "fade"]
    asked.clear()
    # random picks from its list; the pace stretches every one
    config.transitions.occasions["manual"] = "random"
    config.transitions.random = ["iris", "tiles"]
    config.transitions.speed = "slow"
    kind = handover(config, "manual", False, None, None, pick=lambda names: names[-1], make=make)
    assert asked == ["tiles"] and kind.duration == pytest.approx(1.2 * 1.6)
    config.transitions.occasions["idle"] = "cut"
    assert handover(config, "idle", False, None, None, make=make) is None  # at once


def test_the_entrance_says_what_the_occasion_is():
    assert occasion_params("entrance", "idle", False, None, "")["kind"] == "idle"
    assert occasion_params("entrance", "sequence", False, None, "Nimbus") == {"title": "Nimbus"}
    assert occasion_params("wipe", "media", False, None, "") == {}  # only the entrance says things
    assert Entrance(None, {"kind": "media"}).text == "NOW PLAYING"
    assert Entrance(None, {"kind": "idle-end"}).text == "WELCOME BACK"


def test_the_occasion_of_a_switch_is_where_its_theme_comes_from():
    config = parse_config({"modes": {"media": {"on": True}}, "sequence": {"steps": []}})
    host = SimpleNamespace(theme=None, theme_owner=None, mode=None)
    assert app._occasion(config, host, None) == "manual"
    host.mode = "media"
    assert app._occasion(config, host, None) == "media"  # music's page: Encore
    host.theme, host.theme_owner = "nimbus", "autopilot"
    assert app._occasion(config, host, None) == "autopilot"
    host.theme_owner = "memory"
    assert app._occasion(config, host, None) == "report"
    # into music it is music's; back from it too, leaving it; a theme after a theme: chosen
    assert app._switch_occasion("manual", "media") == ("media", False)
    assert app._switch_occasion("media", "manual") == ("media", True)
    assert app._switch_occasion("sequence", "manual") == ("manual", False)
    assert app._switch_occasion("game", "report") == ("report", False)


def test_a_transition_shown_on_the_panel_goes_out_to_its_card_and_back():
    transition_show.done(transition_show.asked()[0]) if transition_show.asked() else None
    number = transition_show.show("wipe", "Wipe")
    assert transition_show.asked() == (number, "wipe", "Wipe")
    exported = transition_show.export()
    assert exported[:3] == (number, "wipe", "Wipe")
    old, live = frames((320, 80))
    card = transition_show.card("Wipe", (320, 80), (0, 0, 0), (255, 255, 255))
    show = transition_show.Show(number, old, card, lambda: HANDOVERS["wipe"]())
    d = show.out.duration
    assert show.frame(live, 100.0).getpixel((300, 40)) == OLD  # it begins on the frame it had
    assert show.frame(live, 100.0 + d + 0.1) is card  # the card stands
    assert show.frame(live, 100.0 + d + transition_show.HOLD_S + 0.99 * d).getpixel((5, 40)) == NEW
    assert show.frame(live, 100.0 + show.seconds + 0.01) is None  # over
    transition_show.done(number)
    assert transition_show.asked() is None
    # a panel's own process takes the request once, as it came
    transition_show.adopt((number + 5, "tiles", "Tiles", 0.2))
    assert transition_show.asked() == (number + 5, "tiles", "Tiles")
    transition_show.done(number + 5)


def test_music_and_its_end_play_their_entrance_in_the_main_loop(isolated_home, monkeypatch):
    """The page music brings (Encore) comes with the entrance and the track, and the way back
    says that it ended: switches that used to fall back to a faint fade."""
    from test_toast_scenarios import NoWarmup, Panel, Script, write_theme

    from libre_panel.config import Config, ModeConfig, SensorsConfig
    from libre_panel.plugins.host import PluginHost

    for name, color in (("classic", "#102030"), ("stage", "#302010")):
        write_theme(isolated_home, name, color, style="card")
    made, make = [], app.transition

    def transition(spec, context=None):
        made.append(spec)
        return make(spec, context)

    monkeypatch.setattr(app, "transition", transition)
    monkeypatch.setattr(app, "create_display", Panel)
    monkeypatch.setattr(app, "_Warmup", NoWarmup)
    host = PluginHost()
    # (music's page is off: the demo's music would bring it back at once; it is set here)
    config = Config(theme="classic", fps=50, refresh_ms=100,
                    sensors=SensorsConfig(providers=["demo"]),
                    modes={"media": ModeConfig(theme="stage", on=False)})  # fmt: skip
    events = {
        20: [lambda: host.set_mode("media", by="test")],
        300: [lambda: host.set_mode(None, by="test")],
    }
    monkeypatch.setattr(app, "_Pacer", lambda: Script(600, events))
    try:
        app.run(config, host=host)
    finally:
        host.close()
    kinds = [spec[1]["kind"] for spec in made if isinstance(spec, tuple) and spec[0] == "entrance"]
    assert kinds == ["media", "media-end"], made

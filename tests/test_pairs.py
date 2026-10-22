"""A panel with a companion beside it: the first panel shows the pair version of its theme,
which leaves out what the companion shows, and the companion follows the first panel's page
(Arena Companion beside Arena, Encore's beside Encore)."""

import json
from types import SimpleNamespace

from libre_panel import app
from libre_panel.app import RunStatus
from libre_panel.config import parse_config
from libre_panel.theme.model import (
    builtin_themes_dir,
    companion_for,
    is_companion,
    pair_version,
    version_base,
)

PAIRED = ["lumen", "arena", "encore"]
COMPANIONS = ["lumen", "arena", "encore", "almanac"]


def test_a_pair_version_is_a_version_of_its_theme():
    assert (
        version_base("lumen-pair") == "lumen"
        and version_base("lumen-pair-1920x720") == "lumen-pair"
    )
    for name in PAIRED:
        assert pair_version(name) == f"{name}-pair"
        assert pair_version(f"{name}-1920x720") == f"{name}-pair-1920x720"
    assert pair_version("lumen-vertical") is None  # none made: the theme as it is
    assert pair_version("almanac") is None  # Almanac has nothing its companion shows
    assert pair_version("nimbus") is None and pair_version("lumen-companion") is None
    assert pair_version("lumen-pair-1920x720") is None


def test_a_companion_follows_the_theme_the_first_panel_shows():
    for name in COMPANIONS:
        assert companion_for(name) == f"{name}-companion"
        assert companion_for(f"{name}-1920x720") == f"{name}-companion"
    assert companion_for("arena-pair-1920x720") == "arena-companion"
    assert companion_for("nimbus") is None and companion_for("round-report") is None
    assert companion_for("lumen-companion") is None


def shown(config, paired=False, first=None, mode=None):
    return app._theme_name(config, None, None, None, None, mode, paired=paired, first=first)


def test_the_first_panel_shows_the_pair_version_while_a_companion_runs():
    config = parse_config({"theme": "lumen", "device": {"model": "turing-12.3-usb"}})
    assert shown(config) == "lumen-1920x720"
    assert shown(config, paired=True) == "lumen-pair-1920x720"
    nimbus = parse_config({"theme": "nimbus", "device": {"model": "turing-12.3-usb"}})
    assert shown(nimbus, paired=True) == shown(nimbus)  # one without a pair version: as ever


def test_a_companion_beside_the_first_panel_follows_its_page():
    config = parse_config({
        "theme": "lumen", "device": {"model": "turing-12.3-usb"},
        "panels": [{"model": "turing-9.2-usb", "orientation": "portrait",
                    "theme": "lumen-companion", "game_theme": "arena-companion"}],
    }).for_panel(0)  # fmt: skip
    assert shown(config, first="lumen-pair-1920x720") == "lumen-companion-vertical"
    assert shown(config, first="encore-pair-1920x720", mode="media") == "encore-companion-vertical"
    assert shown(config, first="almanac-1920x720", mode="idle") == "almanac-companion-vertical"
    assert shown(config, first="nimbus-1920x720") == "lumen-companion-vertical"  # none: its own
    assert shown(config, first="arena-pair-1920x720", mode="game") == "arena-companion-vertical"
    beside = {"model": "turing-9.2-usb", "theme": "nimbus"}
    own = parse_config({"device": {"model": "turing-12.3-usb"}, "panels": [beside]}).for_panel(0)
    assert shown(own, first="encore-1920x720", mode="media") == "nimbus"  # no companion: its own


def test_the_pair_holds_a_moment_while_the_companion_reconnects():
    clock = [100.0]
    more = {0: RunStatus(state="showing", theme="lumen-companion-vertical")}
    loop = SimpleNamespace(panel=None, status=SimpleNamespace(more=more), clock=lambda: clock[0])
    pairing = lambda: app.MainLoop._pairing(loop)  # noqa: E731
    assert pairing() == {"paired": True}
    more[0].state = "waiting"
    clock[0] += app.PAIR_HOLD_S - 1
    assert pairing() == {"paired": True}  # a reconnect: no switch back and forth
    clock[0] += 2
    assert pairing() == {"paired": False}
    more[0] = RunStatus(state="showing", theme="nimbus")
    assert pairing() == {"paired": False}  # a panel beside it, but no companion
    beside = SimpleNamespace(panel=0, look_of=lambda: "arena-pair-1920x720")
    assert app.MainLoop._pairing(beside) == {"first": "arena-pair-1920x720"}


def test_a_pair_version_leaves_out_what_its_companion_shows():
    def widgets(name):
        theme = json.loads((builtin_themes_dir() / name / "theme.json").read_text(encoding="utf-8"))
        return theme["widgets"]

    for size in ("", "-1920x720"):
        assert not [w for w in widgets(f"lumen-pair{size}") if w["type"] == "fan"]
        assert {w.get("module") for w in widgets(f"arena-pair{size}")} == {"game", "instrument"}
        assert not [w for w in widgets(f"encore-pair{size}") if w["id"] == "cover"]
    for name in COMPANIONS:
        for version in ("", "-vertical", "-1920x720"):
            companion = f"{name}-companion{version}"
            assert is_companion(companion)
            for w in widgets(companion):
                if w.get("module") == "temps":  # the first panel shows these two
                    assert "!cpu.temp" in w["items"] and "!gpu.temp" in w["items"]


def test_a_pair_version_has_its_version_for_the_12_3():
    from libre_panel.theme.model import theme_for

    assert theme_for("lumen-pair", "landscape", (1920, 720)) == "lumen-pair-1920x720"
    assert theme_for("lumen-pair-1920x720", "landscape", (1920, 462)) == "lumen-pair"
    assert theme_for("lumen-vertical", "landscape", (1920, 720)) == "lumen-1920x720"
    assert theme_for("lumen-1920x720", "landscape", (1920, 720)) == "lumen-1920x720"

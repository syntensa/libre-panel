"""One design for the built-in themes (libre_panel.design): every family wears its look, and the
pictures the app draws itself are in the Libre look."""

import json
from pathlib import Path

import pytest

from libre_panel import branding
from libre_panel.design import (
    BLOCK_COLOURS,
    FAMILIES,
    LOOKS,
    STYLE_DEFAULTS,
    family_of,
    look_colour,
    rgb,
)
from libre_panel.editor.presets import TOKEN_DEFAULTS
from libre_panel.render import bootscreen
from libre_panel.render.light import ROLES as LIGHT_ROLES
from libre_panel.theme.model import builtin_themes_dir, load_theme
from libre_panel.theme.modules import BACKDROPS, CARDS, ROLES, module_info

ROOT = Path(__file__).resolve().parent.parent
THEMES = sorted(builtin_themes_dir().glob("*/theme.json"))


@pytest.fixture(scope="module")
def tool():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT / "tools"))
        import design

        yield design


def test_every_look_has_a_colour_for_every_role_and_cards_the_modules_can_make():
    for key, look in LOOKS.items():
        assert set(ROLES) <= set(look["palette"]), key
        style = {**STYLE_DEFAULTS, **look["style"]}
        assert set(style) == set(STYLE_DEFAULTS), key
        assert style["card"] in CARDS and style["backdrop"] in BACKDROPS, key
        assert all(c.startswith("#") and len(c) == 7 for c in look["palette"].values()), key


def test_every_built_in_theme_belongs_to_a_family_and_every_family_has_themes():
    names = [path.parent.name for path in THEMES]
    assert [n for n in names if family_of(n) is None] == []
    worn = {family_of(n)[0] for n in names}
    assert set(FAMILIES) == worn
    assert family_of("spur-studio")[0] == "spur-studio"  # the longest name that fits
    assert family_of("spur-cockpit")[0] == "spur"
    assert family_of("lumen-1920x720")[0] == "lumen"
    assert family_of("lumenous") is None


def test_every_built_in_theme_wears_its_family_s_look(tool):
    """What tools/design.py would change: nothing. (A colour changed in the design: run the
    tool with --write.)"""
    differs = []
    for path in THEMES:
        _, changes = tool.designed(path.parent.name, json.loads(path.read_text("utf-8")))
        differs += changes
    assert differs == []


def test_the_round_report_is_in_arena_s_design():
    """It follows a game on Arena: the same colours and cards, not the app's."""
    arena = load_theme(builtin_themes_dir() / "arena")
    for name in ("round-report", "round-report-1920x720", "round-report-480x480"):
        report = load_theme(builtin_themes_dir() / name)
        for role in ("bg", "surface", "text", "accent", "cpu", "gpu", "warn", "crit"):
            assert report.palette[role].upper() == arena.palette[role].upper(), (name, role)
        for key in ("card", "radius", "glow", "backdrop", "card_opacity"):
            assert report.style[key] == arena.style[key], (name, key)


def test_the_tool_writes_a_look_into_a_theme_and_leaves_the_theme_s_own_colours(tool):
    data = {
        "palette": {"bg": "#000000", "bg2": "#111111", "cpu": "#123456", "base": "#000000",
                    "glow_cpu": "#ABCDEF"},
        "background": {"gradient": ["#000000", "#111111"]},
        "style": {"card": "flat", "radius": 3, "glow": 0.1, "backdrop": "flat"},
    }  # fmt: skip
    out, changes = tool.designed("arena-640x480", data)
    arctic = LOOKS["arctic"]["palette"]
    assert out["background"]["gradient"] == ["@bg", "@bg2"]  # it follows the palette
    assert out["palette"] == {"bg": arctic["bg"], "bg2": arctic["bg2"], "cpu": arctic["cpu"],
                              "base": arctic["bg"], "glow_cpu": "#ABCDEF"}  # fmt: skip
    assert out["style"]["card_opacity"] == 0.8 and out["style"]["radius"] == 18
    assert out["style"]["backdrop"] == "gradient"
    assert data["palette"]["bg"] == "#000000"  # (a copy)
    assert len(changes) == 2 + 4 + 5  # background, palette, cards
    assert tool.designed("spur-ii", data) == (data, [])  # its own colours, as SPUR II has them
    _, changes = tool.designed("tilt", {"palette": {}})
    assert changes == ["tilt: of no family in design.FAMILIES"]


def test_the_app_s_own_pictures_are_in_the_libre_look():
    libre = LOOKS["libre"]["palette"]
    assert bootscreen.BG == rgb(libre["bg"]) and bootscreen.BG2 == rgb(libre["bg2"])
    assert bootscreen.TEXT == rgb(libre["text"]) and bootscreen.ACCENT == rgb(libre["accent"])
    assert branding.ACCENT == rgb(look_colour("libre", "accent")) == (46, 230, 214)
    assert TOKEN_DEFAULTS == BLOCK_COLOURS
    assert TOKEN_DEFAULTS["accent"] == libre["accent"] and TOKEN_DEFAULTS["crit"] == libre["crit"]
    for role, theme_role in (("ground", "bg"), ("text", "text"), ("info", "accent"),
                             ("warning", "warn"), ("error", "crit")):  # fmt: skip
        assert LIGHT_ROLES[role][1] == libre[theme_role], role


def test_the_editor_offers_every_look_for_a_new_theme():
    looks = module_info()["looks"]
    assert list(looks) == list(LOOKS)
    assert looks["libre"]["palette"]["bg"] == "#0A0E13"
    assert looks["libre"]["style"]["radius"] == 14
    assert looks["contrast"]["name"] == "High Contrast"

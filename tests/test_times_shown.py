"""How long the PC has been on in Lumen (under the seconds, at the foot of the clock's digits)
and how long the game has been played in Arena (on the clock's line, else a reading beside the
frame rate)."""

import json

import pytest

from libre_panel.render.renderer import Renderer
from libre_panel.theme.model import builtin_themes_dir, find_theme, load_theme


def _lumen_with_seconds(path):
    data = json.loads((path / "theme.json").read_text())
    if min(data["display"]["width"], data["display"]["height"]) <= 80:
        return False  # the 0.96": the time, its seconds and the CPU, no room for more
    return any(w.get("id") == "seconds" for w in data["widgets"])


LUMENS = sorted(
    path.name for path in builtin_themes_dir().glob("lumen*") if _lumen_with_seconds(path)
)
ARENAS = ["arena", "arena-1920x720", "arena-1280x720", "arena-pair", "arena-pair-1920x720",
          "arena-pair-1280x720", "arena-vertical", "arena-pair-vertical"]  # fmt: skip


def laid_out(name):
    renderer = Renderer(load_theme(find_theme(name)), preview=True)
    try:
        return {w["id"]: w for w in renderer.widgets if "id" in w}
    finally:
        renderer.close()


def test_every_lumen_with_seconds_has_the_uptime():
    assert len(LUMENS) >= 8
    for name in LUMENS:
        parts = laid_out(name)
        seconds, clock, date = parts["seconds"], parts["clock"], parts["date"]
        icon, value = parts["uptime-icon"], parts["uptime"]
        assert icon["icon"] == "uptime" and "sys.uptime" in icon["needs"], name
        assert value["sensor"] == "sys.uptime" and value["hide_if_missing"], name
        assert value["format"] == "{value:duration}", name
        # under the seconds, at the foot of the clock's digits, clear of the date
        assert icon["x"] >= seconds["x"] and value["x"] > icon["x"] + icon["size"] / 2, name
        foot = clock["y"] + clock["font_size"]
        assert abs(value["y"] + value["font_size"] - foot) <= 1, name
        top, bottom = value["y"], value["y"] + value["font_size"]
        assert bottom <= date["y"] + 0.3 * date["font_size"] or top >= date["y"] + date["font_size"]
        assert value["max_width"] > 2.3 * value["font_size"], name  # ("3d 5h" at the least)


@pytest.mark.parametrize("name", ARENAS)
def test_every_arena_shows_how_long_the_game_has_been_played(name):
    parts = laid_out(name)
    played = [w for key, w in parts.items() if key.endswith("/played")]
    labels = [w for key, w in parts.items() if key.endswith("/played-label")]
    assert len(played) == 1 and len(labels) == 1, name
    value, label = played[0], labels[0]
    assert value["sensor"] == "game.time" and value["format"] == "{value:clock}"
    assert value["hide_if_missing"] and "game.time" in label["needs"]
    clock = next((w for key, w in parts.items() if key.endswith("/clock")), None)
    if clock is not None and abs(value["y"] - clock["y"]) <= 1:  # on the clock's line
        assert label["x"] < value["x"] < clock["x"] - clock["max_width"]
    else:  # a reading beside the frame rate, the last
        others = [w for key, w in parts.items() if key.endswith("/peak")]
        assert others and value["y"] > others[0]["y"]


def test_the_uptime_icon_is_drawn():
    from libre_panel.icons import ICON_NAMES, draw_icon

    assert "uptime" in ICON_NAMES
    assert draw_icon("uptime", 24, (255, 255, 255, 255)).getbbox() is not None

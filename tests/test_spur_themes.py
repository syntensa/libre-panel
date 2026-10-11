"""The SPUR II screens as built-in themes: they load, draw with the demo's readings and with none,
keep their look, and move to other panels."""

import json

import pytest

from libre_panel.devices.models import find_model
from libre_panel.render.renderer import Renderer, _needs
from libre_panel.sensors.base import Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import (
    THEME_FILENAME,
    builtin_themes_dir,
    find_theme,
    load_theme,
    parse_theme,
)

SPUR = ["spur-horizon", "spur-header"]
# readings the demo has not got yet: they come with the merge of the sources that add them
LATER = {"mem.name"}


def drawn(theme_name):
    theme = load_theme(find_theme(theme_name))
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    renderer = Renderer(theme)
    frame, boxes = renderer.render(snapshot, 0.0)
    return theme, snapshot, renderer, frame, boxes


def keys_read(theme):
    """The readings a theme asks for, by widgets and by their needs."""
    keys = {w["sensor"] for w in theme.widgets if w.get("sensor")}
    for widget in theme.widgets:
        keys |= {key for _, key, _ in _needs(widget) if key and not key.startswith("@")}
    return keys


@pytest.mark.parametrize("name", SPUR)
def test_a_spur_screen_draws_whole_with_the_demo(name):
    theme, snapshot, renderer, frame, boxes = drawn(name)
    assert theme.warnings == [] and renderer.warnings == []
    assert (theme.width, theme.height) == (1920, 480) and frame.size == (1920, 480)
    assert set(theme.look) == {"grid", "light", "dust"}  # SPUR II's look, as SPUR Classic has it
    shown = {w["id"] for w in theme.widgets if w["visible"] and not Renderer._missing(w, snapshot)}
    assert set(boxes) == shown and len(shown) > 0.8 * len(theme.widgets)
    # flat frames and no ridges (the user's wish): nothing on the screen catches the panel light
    assert not any(w.get("ridge") for w in theme.widgets) and renderer.light_edges == []
    absent = {key for key in keys_read(theme) if snapshot.value(key) is None}
    assert absent <= LATER, absent  # the demo has every reading a screen shows


@pytest.mark.parametrize("name", SPUR)
def test_a_spur_screen_is_made_for_the_8_inch_bar_and_names_no_hardware(name):
    data = json.loads((builtin_themes_dir() / name / THEME_FILENAME).read_text(encoding="utf-8"))
    assert data["display"]["model"] == "turing-8.8-usb"  # the 9.2" strip test does not apply
    assert not any(find_model("turing-8.8-usb").hidden_edges("landscape").values())
    text = json.dumps(data)
    for model in ("Ryzen", "Radeon", "GeForce", "Crucial", "Kingston", "9850", "9070"):
        assert model not in text  # names of hardware come from the sensors, never the theme


@pytest.mark.parametrize("name", SPUR)
def test_a_spur_screen_draws_without_any_reading(name):
    theme = load_theme(find_theme(name))
    renderer = Renderer(theme)
    frame, boxes = renderer.render(Snapshot(), 0.0)
    assert frame.size == (1920, 480) and renderer.warnings == []
    fans = {w["id"] for w in theme.widgets if w["type"] == "fan"}
    assert fans and not fans & set(boxes)  # a fan that nothing reports is not drawn


@pytest.mark.parametrize("name", SPUR)
@pytest.mark.parametrize("model, orientation", [("turing-3.5", "landscape"),
                                                 ("turing-5", "landscape"),
                                                 ("turing-8.8-usb", "portrait")])  # fmt: skip
def test_a_spur_screen_moves_to_another_panel(name, model, orientation):
    data = json.loads((builtin_themes_dir() / name / THEME_FILENAME).read_text(encoding="utf-8"))
    adapted = adapt_theme(data, model, orientation)
    theme = parse_theme(adapted)
    frame, _ = Renderer(theme).render(demo_snapshot(fixed_time=1_700_000_000), 0.0)
    assert frame.size == (theme.width, theme.height) != (1920, 480)
    assert theme.warnings == []


# -- Horizon -----------------------------------------------------------------------------------


def shown_ids(theme, readings=None, images=None):
    from libre_panel.sensors.base import Reading

    found = {k: Reading(k, v) for k, v in (readings or {}).items()}
    snapshot = Snapshot(readings=found, images=images or {})
    return {w["id"] for w in theme.widgets if not Renderer._missing(w, snapshot)}


def test_horizon_grows_the_cpu_up_and_the_gpu_down_from_one_line():
    theme = load_theme(find_theme("spur-horizon"))
    by = {w["id"]: w for w in theme.widgets}
    cpu, gpu = by["cpu-graph"], by["gpu-graph"]
    assert (cpu["sensor"], gpu["sensor"]) == ("cpu.load", "gpu.load")
    assert cpu["direction"] == "up" and gpu["direction"] == "down"
    assert (cpu["x"], cpu["w"]) == (gpu["x"], gpu["w"]) and cpu["inset"] is False
    assert cpu["y"] + cpu["h"] - 1 == gpu["y"]  # they meet on the horizon
    # a point a pixel and 25 a second: a mark every 10 s is 250 px
    marks = sorted(
        w["x"] for w in theme.widgets if w["id"].startswith("mark-") and w["type"] == "rect"
    )
    assert len(marks) == 6 and {b - a for a, b in zip(marks, marks[1:], strict=False)} == {250}
    assert marks[-1] == cpu["x"] + cpu["w"] - 1 - 250  # counted back from the newest point


def test_horizon_music_tile_follows_the_player():
    from PIL import Image

    theme = load_theme(find_theme("spur-horizon"))
    cover = {"media.cover": Image.new("RGBA", (8, 8))}
    playing = {"media.title": "Song", "media.playing": 1}
    ids = shown_ids(theme, playing, cover)
    assert {"cover", "media-playing-cover", "media-title-cover"} <= ids
    assert not ids & {"media-playing-plain", "media-paused-cover", "media-idle-cover"}
    ids = shown_ids(theme, {"media.title": "Song", "media.playing": 0})  # paused, no cover
    assert "media-paused-plain" in ids and "cover" not in ids
    ids = shown_ids(theme)  # nothing plays
    assert {"media-idle-plain"} <= ids and not ids & {"media-title-plain", "media-title-cover"}


# -- Header (the game screen with the familiar frame) --------------------------------------------


def test_header_shows_the_frame_rate_the_limit_and_the_game():
    from PIL import Image

    theme = load_theme(find_theme("spur-header"))
    by = {w["id"]: w for w in theme.widgets}
    assert by["fps-value"]["sensor"] == "game.fps" and by["low-value"]["sensor"] == "game.low"
    words = {"limit-gpu", "limit-cpu", "limit-cap", "limit-none"}
    assert shown_ids(theme, {"game.limiter": "gpu"}) & words == {"limit-gpu"}
    assert shown_ids(theme, {"game.limiter": "CAP"}) & words == {"limit-cap"}
    assert shown_ids(theme, {}) & words == {"limit-none"}  # PresentMon has not seen a game
    # the logo where the game has one, its name where it has not
    assert {"game-name"} <= shown_ids(theme, {"game.name": "Starfall"})
    ids = shown_ids(theme, {"game.name": "Starfall"}, {"game.logo": Image.new("RGBA", (4, 4))})
    assert "game-logo" in ids and "game-name" not in ids
    # the fans keep their place after the game's values, without names once they are narrow
    fans = [w for w in theme.widgets if w["type"] == "fan"]
    assert fans[0]["x"] > by["low-value"]["x"] and not any("-name" in w["id"] and "fan" in w["id"]
                                                          for w in theme.widgets)  # fmt: skip


def test_header_charts_the_longest_frame_of_each_point_with_a_line_at_60_fps():
    theme = load_theme(find_theme("spur-header"))
    by = {w["id"]: w for w in theme.widgets}
    graph = by["frames-graph"]
    assert graph["sensor"] == "game.frametime_peak" and (graph["min"], graph["max"]) == (0, 33.3)
    floor = graph["y"] + graph["h"] - 1
    assert by["frames-floor"]["y"] == floor
    assert by["frames-60fps"]["y"] == floor - int(16.667 / 33.3 * (graph["h"] - 1))
    assert by["frames-60fps"]["x"] == graph["x"] and by["frames-60fps"]["w"] == graph["w"] - 1

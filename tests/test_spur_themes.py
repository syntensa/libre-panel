"""The SPUR II screens as built-in themes (and the round report, in their look): they load, draw
with the demo's readings and with none, keep their look, and move to other panels."""

import gc
import json

import pytest

from libre_panel.devices.models import find_model
from libre_panel.render.renderer import Renderer, need_key
from libre_panel.sensors.base import Snapshot
from libre_panel.sensors.demo import demo_report, demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import (
    THEME_FILENAME,
    builtin_themes_dir,
    find_theme,
    load_theme,
    parse_theme,
)

SPUR = ["spur-horizon", "spur-header", "spur-focus", "spur-session", "spur-cockpit",
        "round-report", "spur-studio"]  # fmt: skip
# the game service publishes this one, and a demo value would hide it: the program's name
# (game.app) stands in on the screens, so the demo leaves it out
LEFT_OUT = {"game.name"}


@pytest.fixture(scope="module", autouse=True)
def renderers_let_go():
    yield
    gc.collect()  # a renderer tells the sensors what it reads until it is collected


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
        for need in (widget.get("needs") or "").split(","):
            need = need.strip().lstrip("!").strip()
            if need and not need.startswith("@"):  # (a picture is no reading)
                keys.add(need_key(need))
    return keys


def glow_areas_hold_their_bars(theme, widest=420):
    """Each lit bar's glow is worked out over its own zone: it holds the bar and stays narrow."""
    bars = [w for w in theme.widgets if w["type"] == "bar" and w["glow_area"]]
    assert bars
    for bar in bars:
        x, y, w, h = bar["glow_area"]
        assert x <= bar["x"] and bar["x"] + bar["w"] <= x + w, bar["id"]
        assert y <= bar["y"] and bar["y"] + bar["h"] <= y + h, bar["id"]
        assert w <= widest, bar["id"]  # a zone as wide as the panel, once for every bar, is slow


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
    assert absent <= LEFT_OUT, absent  # the demo has every reading a screen shows


@pytest.mark.parametrize("name", SPUR)
def test_a_spur_screen_is_made_for_the_8_inch_bar_and_names_no_hardware(name):
    data = json.loads((builtin_themes_dir() / name / THEME_FILENAME).read_text(encoding="utf-8"))
    assert data["display"]["model"] == "turing-8.8-usb"  # the 9.2" strip test does not apply
    assert not any(find_model("turing-8.8-usb").hidden_edges("landscape").values())
    text = json.dumps(data)
    for brand in ("Ryzen", "Radeon", "GeForce", "Crucial", "Kingston"):
        assert brand not in text  # names of hardware come from the sensors, never the theme


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


@pytest.mark.parametrize(
    "name, name_id, app_id, logo_id",
    [("spur-header", "game-name", "game-app", "game-logo"),
     ("spur-focus", "game-name", "game-app", "game-logo"),
     ("spur-session", "header-game-name", "header-game-app", "game-logo")],
)  # fmt: skip
def test_a_game_screen_shows_the_logo_else_the_games_name_else_the_programs(
    name, name_id, app_id, logo_id
):
    from PIL import Image

    theme = load_theme(find_theme(name))
    both = {name_id, app_id}
    named = {"game.name": "Space Game", "game.app": "spacegame"}
    assert shown_ids(theme, named) & both == {name_id}  # the game service knows the game
    assert shown_ids(theme, {"game.app": "spacegame"}) & both == {app_id}  # only PresentMon does
    assert name_id not in shown_ids(theme, {})  # (the program's draws nothing without a reading)
    logo = {"game.logo": Image.new("RGBA", (4, 4))}
    ids = shown_ids(theme, named, logo)  # the logo says it all
    assert logo_id in ids and not ids & both
    by = {w["id"]: w for w in theme.widgets}
    assert (by[name_id]["sensor"], by[app_id]["sensor"]) == ("game.name", "game.app")
    assert by[name_id]["max_width"] == by[app_id]["max_width"]  # they stand in one place
    assert (by[name_id]["x"], by[name_id]["y"]) == (by[app_id]["x"], by[app_id]["y"])


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
    glow_areas_hold_their_bars(theme)
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


# -- Focus (the frame rate first) ----------------------------------------------------------------


def test_focus_makes_the_frame_rate_the_largest_thing_with_its_course_beside_it():
    theme = load_theme(find_theme("spur-focus"))
    by = {w["id"]: w for w in theme.widgets}
    number, graph = by["fps-value"], by["fps-graph"]
    assert number["sensor"] == "game.fps" and number["font_size"] == 150
    assert max(w["font_size"] for w in theme.widgets if "font_size" in w) == 150
    assert (graph["sensor"], graph["min"], graph["max"]) == ("game.fps", 0, 175)
    assert graph["x"] > number["x"] + 250 and graph["w"] == 867  # 35 s at a point a pixel
    # the guide lines stand where 120 and 60 fps are on that scale (0 below, 175 above)
    for fps in (120, 60):
        y = graph["y"] + graph["h"] - 1 - round(fps / 175 * (graph["h"] - 1))
        assert abs(by[f"fps-rule-{fps}"]["y"] - y) <= 1


def test_focus_has_four_sections_whose_light_stays_in_them():
    theme = load_theme(find_theme("spur-focus"))
    by = {w["id"]: w for w in theme.widgets}
    assert [by[f"{n}-title"]["text"] for n in ("frames", "cpu", "gpu", "sys")] == [
        "FRAMES", "CPU", "GPU", "SYSTEM"
    ]  # fmt: skip
    assert by["last-top"]["color"] == "@rule_strong"  # the load block holds two channels: neutral
    assert by["held-top"]["color"] == "@net_frame" and "held-bottom" in by
    assert "cpu-box-bottom" not in by  # the boxes below are open at the bottom
    glow_areas_hold_their_bars(theme)
    assert shown_ids(theme, {"game.limiter": "cpu"}) & {"limit-gpu", "limit-cpu"} == {"limit-cpu"}


# -- Session (the last ten minutes of what a game does) ------------------------------------------


def test_session_shows_the_last_ten_minutes_of_the_frame_rate_the_frames_and_the_peaks():
    from libre_panel.render.motion import MAX_POINTS, RATE

    theme = load_theme(find_theme("spur-session"))
    by = {w["id"]: w for w in theme.widgets}
    graphs = [w for w in theme.widgets if w["type"] == "graph"]
    assert {g["sensor"] for g in graphs} == {
        "game.fps", "game.frametime_peak", "cpu.temp", "cpu.power", "gpu.temp",
        "temp.gpu.hot_spot", "gpu.power", "gpu.mem.used",
    }  # fmt: skip
    # the longest window the core keeps in video mode: nothing here claims a whole session
    assert {g["seconds"] for g in graphs} == {596} and 596 <= (MAX_POINTS - 4 * RATE) / RATE
    assert by["peaks-box-top"]["color"] == "@rule_strong"  # two channels: neutral
    rows = [by[f"peak-{n}-graph"]["y"] for n in range(6)]
    assert [b - a for a, b in zip(rows, rows[1:], strict=False)] == [56] * 5


# -- Cockpit (building blocks on a 40 px grid) ---------------------------------------------------


def test_cockpit_lays_its_blocks_on_the_grid_4_px_in_from_the_cells():
    theme = load_theme(find_theme("spur-cockpit"))
    by = {w["id"]: w for w in theme.widgets}
    # two charts of 20 cells, the ring in the 8 cells that are left
    assert (by["cpu-frame-top"]["x"], by["cpu-frame-top"]["y"], by["cpu-frame-top"]["w"]) == (
        4,
        84,
        792,
    )
    assert (by["gpu-frame-top"]["x"], by["gpu-frame-top"]["w"]) == (804, 792)
    # four columns of 12 cells, 8 px apart, open at the bottom
    lefts = [by[f"{n}-box-left"]["x"] for n in ("cpu", "gpu", "memory", "net")]
    assert lefts == [4, 484, 964, 1444]
    assert all(f"{n}-box-bottom" not in by for n in ("cpu", "gpu", "memory", "net"))
    assert "cpu-frame-bottom" in by  # the charts are closed
    glow_areas_hold_their_bars(theme)


def test_cockpit_shows_the_ram_as_a_ring_and_the_network_mirrored_on_a_root_scale():
    theme = load_theme(find_theme("spur-cockpit"))
    by = {w["id"]: w for w in theme.widgets}
    ring = by["ram-ring"]
    assert (ring["sensor"], ring["min"], ring["max"], ring["finish"]) == (
        "mem.used",
        0,
        32,
        "facet",
    )
    assert (ring["start_angle"], ring["end_angle"]) == (270, 630)  # from twelve o'clock, round
    up, down = by["net-up-graph"], by["net-down-graph"]
    assert (up["direction"], down["direction"]) == ("up", "down") and up["scale"] == "sqrt"
    assert up["y"] + up["h"] - 1 == down["y"] == by["net-mid"]["y"]  # they meet on the middle line


# -- Round report (what the memory service shows after a game) -----------------------------------


def report_ids(theme, earlier):
    """The widgets that are drawn for a round set against ``earlier`` rounds of the game."""
    readings = {key: reading.value for key, reading in demo_report(1_700_000_000, earlier).items()}
    return shown_ids(theme, readings)


def test_the_round_report_reads_what_the_memory_service_publishes():
    theme = load_theme(find_theme("round-report"))
    published = set(demo_report(0.0))  # the service's own readings (round_report.readings)
    read = keys_read(theme)
    ours = {key for key in read if key.startswith("report.")}
    assert ours <= published, ours - published
    fans = {w["sensor"] for w in theme.widgets if w["type"] == "fan"}
    assert read - ours == fans  # nothing else but what the fans in the header report
    # all of it is on the screen: the round, how it was spread over the frame rates, the peaks
    round_itself = ("title", "game", "when", "length", "outside", "fps", "low", "frametime")
    assert {f"report.{name}" for name in round_itself} <= ours
    assert {f"report.level.{n}" for n in range(6)} <= ours
    assert {f"report.{peak}" for peak in ("cpu_temp", "gpu_temp", "gpu_hotspot", "cpu_power",
                                         "gpu_power", "vram_used")} <= ours  # fmt: skip
    # the box titles that name something are the panel's language, not the theme's
    by = {w["id"]: w for w in theme.widgets}
    assert by["round-title"]["format"] == "{value:upper}" and by["game"]["fit"] == "ellipsis"
    assert by["game"]["x"] + by["game"]["max_width"] <= by["round-box-right"]["x"]


def test_the_round_report_sets_the_round_against_the_earlier_ones_when_there_were_some():
    theme = load_theme(find_theme("round-report"))
    heads = {"mean-head", "delta-head"}
    compared = {  # what only has a meaning next to earlier rounds
        w["id"]
        for w in theme.widgets
        if w["id"].endswith(("-mean", "-delta", "-delta-label")) or w["id"] in heads
    }
    assert len(compared) == 24  # six rows of the levels, six of the peaks (two each), four, two
    ids = report_ids(theme, 7)
    assert compared <= ids and "first-round" not in ids
    ids = report_ids(theme, 0)  # the first round of a game: the round alone, and it says so
    assert not ids & compared and "first-round" in ids
    assert {"fps", "low-value", "frame-value", "limit-gpu-bar", "round-head"} <= ids
    assert {f"level-{n}-bar" for n in range(6)} <= ids  # the spread of the round is still there
    assert {f"{peak}-value" for peak in ("cpu_temp", "gpu_hotspot", "vram_used")} <= ids


def test_the_round_report_names_who_held_the_rate_down_as_the_service_counts_it():
    theme = load_theme(find_theme("round-report"))
    words = {"limit-gpu", "limit-cpu", "limit-cap"}
    bars = {"limit-gpu-bar", "limit-cpu-bar", "limit-cap-bar"}
    mixed = {"report.limit": "GPU 56 %", "report.limit.gpu": 56.0, "report.limit.cpu": 11.0,
             "report.limit.cap": 33.0}  # fmt: skip
    ids = shown_ids(theme, mixed)
    assert words <= ids and bars <= ids and {"limit-label", "limit-groove"} <= ids
    only_cap = {"report.limit": "CAP 100 %", "report.limit.cap": 100.0}  # the others are left out
    ids = shown_ids(theme, only_cap)
    assert ids & words == {"limit-cap"} and ids & bars == {"limit-cap-bar"}
    ids = shown_ids(theme, {})  # nothing was counted
    assert not ids & (words | bars | {"limit-label", "limit-groove"})
    by = {w["id"]: w for w in theme.widgets}  # the GPU's share grows from the left, the CPU's right
    assert (by["limit-gpu-bar"]["direction"], by["limit-cpu-bar"]["direction"]) == ("right", "left")
    assert {by["limit-gpu-bar"]["x"], by["limit-cpu-bar"]["x"], by["limit-cap-bar"]["x"]} == {
        by["limit-groove"]["x"] + 1
    }


def test_the_round_report_keeps_the_frames_of_the_screens_it_sits_with():
    theme = load_theme(find_theme("round-report"))
    by = {w["id"]: w for w in theme.widgets}
    # three boxes of flat bands: the round and the spread in the colour of the frames, the
    # comparison neutral (it holds the CPU's and the GPU's colours)
    assert [by[f"{n}-box-top"]["color"] for n in ("round", "levels", "compare")] == [
        "@net_frame",
        "@net_frame",
        "@rule_strong",
    ]
    assert not any(w["id"].endswith("-box-bottom") for w in theme.widgets)  # open at the bottom
    glow = [w for w in theme.widgets if w["type"] == "bar" and w["finish"] == "lit"]
    assert len(glow) == 6 and all(w["glow"] == 0.0 for w in glow)  # a bar's own light is enough


# -- Studio (the cards of the Studio screen on one grid) -----------------------------------------


def studio():
    theme = load_theme(find_theme("spur-studio"))
    return theme, Renderer(theme, preview=True)


def test_studio_is_made_of_the_cards_on_one_grid_near_where_spur_ii_has_them():
    theme, renderer = studio()
    modules = {w["id"]: w for w in theme.widgets if w["type"] == "module"}
    assert {m["module"] for m in modules.values()} == {"cards", "instrument", "netcard"}
    assert [modules[f"{part}-ring"]["source"] for part in ("cpu", "gpu", "mem")] == [
        "cpu", "gpu", "mem",
    ]  # fmt: skip
    rings_and_net = ("cpu-ring", "gpu-ring", "mem-ring", "net")  # no face, no frame line
    assert all(modules[name]["card"] == "off" for name in rings_and_net)
    assert modules["card"]["items"].split() == ["playback", "forecast"]
    assert theme.style["card"] == "outline"  # the slot's frame is a thin flat line, no card face
    # the boxes of the modules (x, y, w, h): SPUR II's card, rings and NET section
    box = renderer.module_boxes
    near = lambda got, want, by: all(abs(a - b) <= by for a, b in zip(got, want, strict=True))  # noqa: E731
    assert near(box["card"], (620, 96, 706, 376), 14)
    assert near(box["net"], (10, 300, 596, 131), 24)
    rings = [box[f"{part}-ring"] for part in ("cpu", "gpu", "mem")]
    assert [r[0] for r in rings] == sorted(r[0] for r in rings)
    assert len({(r[1], r[3]) for r in rings}) == 1  # one height, side by side
    assert max(r[2] for r in rings) - min(r[2] for r in rings) <= 2  # and as wide as each other
    assert near(rings[0], (1340, 96, 190, 376), 4) and near(rings[2], (1720, 96, 190, 376), 4)
    # nothing overlaps: the NET section, the card, the rings stand in a row
    spans = sorted((b[0], b[0] + b[2]) for b in (box["card"], box["net"], *rings))
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_studio_has_the_time_large_the_date_in_capitals_and_the_volume_below_the_network():
    theme, _ = studio()
    by = {w["id"]: w for w in theme.widgets}
    assert (by["clock"]["format"], by["clock"]["font_size"]) == ("%H:%M", 176)
    assert (by["seconds"]["format"], by["seconds"]["font_size"]) == ("%S", 56)
    assert by["seconds"]["x"] > by["clock"]["x"] + 400 and by["seconds"]["color"] == "@accent"
    assert by["date"]["uppercase"] is True and "%A" in by["date"]["format"]
    assert by["vol-bar"]["sensor"] == "audio.volume" and by["vol-bar"]["finish"] == "lit"
    assert by["vol-bar"]["y"] > 440 and by["vol-rule"]["y"] < by["vol-bar"]["y"]  # its own row
    glow_areas_hold_their_bars(theme)
    # the header has the wordmark and the fans only: the clock is the large one below
    assert {w["id"] for w in theme.widgets if w["type"] == "clock"} == {"clock", "seconds", "date"}
    fans = [w for w in theme.widgets if w["type"] == "fan"]
    assert len(fans) == 5 and fans[0]["x"] == 286 and fans[-1]["x"] > 1600  # across the width


def test_studio_shows_what_plays_else_the_weather_and_the_network_else_a_download():
    from test_slot import RUNNING, demo, downloading

    _, renderer = studio()
    card, net = renderer._slots["card"], renderer._slots["net"]
    assert [kind for kind in card.kinds] == ["playback", "forecast"]
    assert [kind for kind in net.kinds] == ["download", "net"]
    renderer.render(demo(), 0.0)
    assert card.shown == 0 and net.shown == 1  # a track plays, and nothing downloads
    renderer.render(demo(media=False), 1.0)
    assert card.shown == 1  # the weather, as nothing plays
    renderer.render(downloading(demo(media=False)), 2.0)
    assert net.shown == 0 and RUNNING["name"]  # a download runs: it takes the NET section


def test_studio_moves_from_one_card_to_the_next_with_a_line_of_light():
    from test_slot import demo

    theme, _ = studio()
    assert (find_module(theme, "card")["change"], find_module(theme, "card")["hold"]) == (0.6, 2.0)
    renderer = Renderer(theme, animate=True, preview=True)
    card = renderer._slots["card"]
    for step in range(10):  # a track plays
        renderer.render(demo(), step * 0.02)
    assert card.shown == 0 and card.leaving is None
    renderer.render(demo(media=False), 0.26)  # it stops: the change begins
    assert card.shown == 1 and card.leaving == 0 and renderer.moving
    renderer.render(demo(media=False), 0.36)  # the old card rolls up into a line of light
    height = card.rows[0]
    assert 0 < height < card._inner()[3] and card.line > 0
    renderer.render(demo(media=False), 0.9)  # 0.6 s of the frame's time later it is done
    renderer.render(demo(media=False), 0.92)
    assert card.leaving is None and card.rows is None


def find_module(theme, wid):
    return next(w for w in theme.widgets if w["id"] == wid)


def test_studio_reads_what_the_demo_has_and_a_picture_it_has_none_of():
    from libre_panel.render.renderer import _reads

    _, renderer = studio()
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    absent = {
        key for key in _reads(renderer.widgets)
        if snapshot.value(key) is None and key not in snapshot.images
    }  # fmt: skip
    assert absent == {"media.app_icon"}  # the player's icon is a picture: its name stands in

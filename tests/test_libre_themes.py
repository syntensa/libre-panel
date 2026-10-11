"""Libre Panel's own screens: Studio and Libre Default. They load, draw with the demo's
readings and with none, keep flat frames, show what plays (else the weather, else the day) in
their card, and move to other panels without parts running into each other."""

import gc
import itertools
import json

import pytest

from libre_panel.render.renderer import Renderer
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
from libre_panel.theme.modules import make_grid

OWN = ["studio", "libre-default"]
# the panels of the other built-in themes, and the bars'
PANELS = [("turing-3.5", "landscape"), ("turing-3.5", "portrait"), ("turing-5", "landscape"),
          ("turing-5", "portrait"), ("turing-2.1", "landscape"), ("turing-8.8-usb", "landscape"),
          ("turing-9.2-usb", "landscape"), ("turing-12.3-usb", "landscape")]  # fmt: skip


@pytest.fixture(scope="module", autouse=True)
def renderers_let_go():
    yield
    gc.collect()  # a renderer tells the sensors what it reads until it is collected


def data_of(name):
    return json.loads((builtin_themes_dir() / name / THEME_FILENAME).read_text(encoding="utf-8"))


def drawn(data, root, snapshot=None):
    """The theme's frame and the boxes of what it drew, with the demo's readings."""
    theme = parse_theme(data, root)
    renderer = Renderer(theme, preview=True)
    frame, boxes = renderer.render(snapshot or demo_snapshot(fixed_time=1_700_000_000), 0.0)
    return theme, renderer, frame, boxes


def overlapping(theme, boxes, touch=0):
    """The pairs of drawn parts whose boxes overlap (by more than ``touch`` px either way),
    backdrops aside."""
    kinds = {w["id"]: w for w in theme.widgets}
    parts = {
        wid: box
        for wid, box in boxes.items()
        if not (kinds[wid]["type"] == "rect" and kinds[wid].get("locked"))
    }
    found = set()
    for (a, (ax, ay, aw, ah)), (b, (bx, by, bw, bh)) in itertools.combinations(parts.items(), 2):
        across = min(ax + aw, bx + bw) - max(ax, bx)
        down = min(ay + ah, by + bh) - max(ay, by)
        if across > touch and down > touch:
            found.add(frozenset((a, b)))
    return found


@pytest.mark.parametrize("name", OWN)
def test_an_own_screen_draws_whole_with_the_demo(name):
    data = data_of(name)
    theme, renderer, frame, boxes = drawn(data, builtin_themes_dir() / name)
    assert theme.warnings == [] and renderer.warnings == []
    assert frame.size == (theme.width, theme.height) and theme.collection == ""
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    shown = {w["id"] for w in theme.widgets if w["visible"] and not Renderer._missing(w, snapshot)}
    assert set(boxes) == shown
    # flat frames: no ridges, nothing the panel light would glint on
    assert not any(w.get("ridge") for w in theme.widgets) and renderer.light_edges == []
    text = json.dumps(data)
    for brand in ("Ryzen", "Radeon", "GeForce", "Intel", "Crucial", "Kingston"):
        assert brand not in text  # the names of the hardware come from the sensors


@pytest.mark.parametrize("name", OWN)
def test_an_own_screen_draws_without_any_reading(name):
    theme = load_theme(find_theme(name))
    renderer = Renderer(theme)
    frame, boxes = renderer.render(Snapshot(), 0.0)
    assert frame.size == (theme.width, theme.height) and renderer.warnings == []
    assert boxes  # the time, the day
    assert "--" in {text for _wid, text, _colour in renderer._texts}  # a number, unread
    fans = {w["id"] for w in renderer.widgets if w["type"] == "fan"}
    assert not fans & set(boxes)  # a fan that nothing reports is not drawn


@pytest.mark.parametrize("name", OWN)
@pytest.mark.parametrize("model, orientation", PANELS)
def test_an_own_screen_moves_to_another_panel_without_parts_running_together(
    name, model, orientation
):
    data = data_of(name)
    root = builtin_themes_dir() / name
    if (data["display"]["model"], data["display"]["orientation"]) == (model, orientation):
        return
    theme, _, _, own = drawn(data, root)
    adapted, renderer, frame, boxes = drawn(adapt_theme(data, model, orientation), root)
    assert adapted.warnings == [] and renderer.warnings == []
    assert frame.size == (adapted.width, adapted.height)
    # the modules keep to cells of their own
    grid = make_grid(adapted.width, adapted.height, adapted.grid, adapted.model)
    cells = set()
    for module in (w for w in adapted.widgets if w["type"] == "module" and w["visible"]):
        assert module["col"] + module["cols"] <= grid.columns
        assert module["row"] + module["rows"] <= grid.rows
        mine = {(c, r) for c in range(module["col"], module["col"] + module["cols"])
                for r in range(module["row"], module["row"] + module["rows"])}  # fmt: skip
        assert not mine & cells, module["id"]
        cells |= mine
    # and nothing runs into anything it did not touch on its own panel (on a narrow one a
    # text at the 6 px a font keeps may touch its neighbour)
    touch = 4 if adapted.width < 0.6 * theme.width else 0
    new = overlapping(adapted, boxes, touch) - overlapping(theme, own)
    assert not new, sorted(tuple(sorted(pair)) for pair in new)


def slot_of(name, card="card"):
    theme = load_theme(find_theme(name))
    renderer = Renderer(theme, preview=True)
    return theme, renderer, renderer._slots[card]


@pytest.mark.parametrize("name, card", [("studio", "card"), ("libre-default", "corner")])
def test_the_card_shows_what_plays_else_the_weather_else_the_day(name, card):
    from test_slot import demo

    _, renderer, slot = slot_of(name, card)
    assert slot.kinds == ["playback", "forecast", "date"]
    renderer.render(demo(), 0.0)
    assert slot.shown == 0  # a track plays
    renderer.render(demo(media=False), 1.0)
    assert slot.shown == 1  # nothing plays: the weather
    renderer.render(demo(media=False, weather=False), 2.0)
    assert slot.shown == 2  # neither: the day


@pytest.mark.parametrize("name", OWN)
def test_the_day_under_the_clock_steps_aside_while_the_card_shows_it(name):
    from test_slot import demo

    theme = load_theme(find_theme(name))
    dates = [w for w in theme.widgets if w["type"] == "clock" and "%A" in w["format"]]

    def shown(snapshot):
        return [w["id"] for w in dates if not Renderer._missing(w, snapshot)]

    assert len(shown(demo())) == 1  # the weather, and a track
    assert len(shown(demo(media=False))) == 1  # the weather alone
    assert len(shown(demo(weather=False))) == 1  # a track alone
    assert shown(demo(media=False, weather=False)) == []  # the card shows the day


# -- Studio and Libre Default keep their layout -------------------------------------------------


def test_studio_keeps_its_layout_with_the_card_where_the_weather_was():
    theme = load_theme(find_theme("studio"))
    by = {w["id"]: w for w in theme.widgets}
    assert (by["clock"]["x"], by["clock"]["y"], by["clock"]["font_size"]) == (44, 30, 180)
    assert by["seconds"]["color"] == "@accent" and by["signature"]["w"] == 120
    rings = [by[f"{part}-ring"] for part in ("cpu", "mem", "gpu")]
    assert [r["w"] for r in rings] == [200] * 3 and len({r["y"] for r in rings}) == 1
    assert all(r["finish"] == "lit" for r in rings) and by["disk-ring"]["finish"] == "lit"
    card = Renderer(theme, preview=True).module_boxes["card"]
    assert by["date"]["x"] + 520 < card[0] and card[0] + card[2] < rings[0]["x"]  # between
    assert card[2] >= 1.6 * card[3]  # wide enough for the weather card's details in a row


def test_libre_default_keeps_its_layout_with_the_corner_showing_what_plays():
    theme = load_theme(find_theme("libre-default"))
    by = {w["id"]: w for w in theme.widgets}
    for part in ("cpu-ring", "ram-ring", "side-card", "history-card", "history"):
        assert part in by
    assert by["cpu-ring"]["finish"] == by["ram-ring"]["finish"] == "lit"
    assert by["history"]["finish"] == "lit" and by["history"]["seconds"] == 30
    corner = by["corner"]
    assert (corner["cols"], corner["rows"]) == (1, 1)  # one cell: the modules' compact looks
    box = Renderer(theme, preview=True).module_boxes["corner"]
    ram, side = by["ram-ring"], by["side-card"]
    assert box[0] > ram["x"] + ram["w"] and box[1] + box[3] < side["y"]  # where the weather was

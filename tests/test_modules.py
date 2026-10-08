"""Modules: the grid, layouts by size, fallbacks, looks and fitting text."""

import pytest

from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import ThemeError, parse_theme
from libre_panel.theme.modules import (
    LOOKS,
    MODULE_KINDS,
    ROLES,
    look_theme_parts,
    make_grid,
    module_info,
)

WEATHER = {
    "weather.temperature": 17.0,
    "weather.apparent_temperature": 16.0,
    "weather.humidity": 58.0,
    "weather.wind_speed": 12.0,
    "weather.code": 2.0,
    "weather.description": "Partly cloudy",
}


def snapshot(drop=(), weather=True):
    snap = demo_snapshot(fixed_time=1_700_000_000)
    for key, value in WEATHER.items():
        if weather:
            snap.readings[key] = Reading(key, value, "", key)
        else:
            snap.readings.pop(key, None)
    for prefix in drop:
        for key in [k for k in snap.readings if k.startswith(prefix)]:
            del snap.readings[key]
    return snap


def theme(*modules, model="turing-9.2-usb", look=None, grid=None, **extra):
    data = {
        "format": "libre-panel-theme/1",
        "name": "modules",
        "display": {"model": model, "orientation": "landscape"},
        "widgets": [
            {"type": "module", "id": f"m{i}", **module} for i, module in enumerate(modules)
        ],
        **extra,
    }
    if look:
        palette, style = look_theme_parts(look)
        data["palette"], data["style"] = palette, style
    if grid is not None:
        data["grid"] = grid
    return parse_theme(data)


def shown(renderer, snap):
    """Ids of the parts that are drawn."""
    renderer.render(snap)
    return [pid for pid, _ in renderer._last[0]]


def test_grid_for_a_bar_keeps_clear_of_the_hidden_strip():
    grid = make_grid(1920, 480, None, "turing-9.2-usb")
    assert (grid.columns, grid.rows) == (8, 2)
    assert grid.y == grid.x + 18  # the 9.2" hides 18 px at the top
    x, y, w, h = grid.box(7, 1, 1, 1)
    assert x + w == 1920 - grid.x and y + h == 480 - grid.x


@pytest.mark.parametrize(
    ("size", "cells"),
    [((480, 320), (3, 2)), ((320, 480), (2, 3)), ((800, 480), (3, 2)), ((160, 80), (2, 1))],
)
def test_grid_has_cells_of_about_half_the_short_side(size, cells):
    grid = make_grid(*size)
    assert (grid.columns, grid.rows) == cells


def test_grid_settings_are_kept():
    grid = make_grid(1920, 480, {"columns": 12, "rows": 3, "gap": 10, "margin": 20})
    assert (grid.columns, grid.rows, grid.gap, grid.x) == (12, 3, 10, 20)
    x, _, w, _ = grid.box(0, 0, 12, 1)
    assert (x, w) == (20, 1880)


@pytest.mark.parametrize("kind", MODULE_KINDS)
@pytest.mark.parametrize("span", [(1, 1), (2, 1), (1, 2), (2, 2), (3, 1), (4, 2)])
@pytest.mark.parametrize("model", ["turing-9.2-usb", "turing-3.5"])
def test_every_module_draws_at_every_size(kind, span, model):
    t = theme({"module": kind, "cols": span[0], "rows": span[1]}, model=model)
    renderer = Renderer(t, preview=True)
    frame, boxes = renderer.render(snapshot())
    assert renderer.warnings == [] and t.warnings == []
    assert boxes["m0"] == renderer.module_boxes["m0"]
    x, y, w, h = boxes["m0"]
    assert x >= 0 and y >= 0 and x + w <= frame.width and y + h <= frame.height
    assert len(shown(renderer, snapshot())) >= 2  # the backdrop and more


def test_parts_stay_in_their_cells():
    t = theme(
        {"module": "ring", "cols": 2, "rows": 1},
        {"module": "weather", "col": 2, "cols": 2, "rows": 2},
        {"module": "bars", "col": 4, "cols": 1, "rows": 2},
        {"module": "network", "col": 5, "cols": 3, "rows": 1},
    )
    renderer = Renderer(t, preview=True)
    for part in renderer.widgets:
        module = part.get("_module")
        if not module or "w" not in part:
            continue
        x, y, w, h = renderer.module_boxes[module]
        assert x <= part["x"] and part["x"] + part["w"] <= x + w + 1, part["id"]
        assert y <= part["y"] and part["y"] + part["h"] <= y + h + 1, part["id"]


def test_larger_modules_show_more():
    def parts(cols, rows):
        renderer = Renderer(theme({"module": "ring", "cols": cols, "rows": rows}), preview=True)
        return {pid.split("/", 1)[1] for pid in shown(renderer, snapshot()) if "/" in pid}

    small, wide, long = parts(1, 1), parts(2, 1), parts(4, 1)
    assert {"ring", "value", "label"} <= small and "d0" not in small
    assert {"title", "name", "d0", "d1"} <= wide and "history" not in wide
    assert "history" in long
    assert "history" in parts(2, 2) and "history" in parts(1, 2)


def test_the_disk_takes_the_place_of_a_missing_gpu():
    renderer = Renderer(theme({"module": "ring", "source": "gpu", "cols": 2}), preview=True)
    with_gpu = shown(renderer, snapshot())
    assert "m0/ring" in with_gpu and "m0/alt-ring" not in with_gpu
    without = shown(Renderer(renderer.theme, preview=True), snapshot(drop=("gpu.",)))
    assert "m0/alt-ring" in without and "m0/ring" not in without


def test_no_fallback_leaves_the_cells_empty():
    t = theme({"module": "ring", "source": "gpu", "fallback": "none"})
    drawn = shown(Renderer(t, preview=True), snapshot(drop=("gpu.",)))
    assert [p for p in drawn if p.startswith("m0/")] == []


def test_weather_turns_into_a_calendar_sheet_without_weather():
    t = theme({"module": "weather", "cols": 2})
    with_weather = shown(Renderer(t, preview=True), snapshot())
    assert "m0/temp" in with_weather and "m0/day" not in with_weather
    without = shown(Renderer(t, preview=True), snapshot(weather=False))
    assert "m0/day" in without and "m0/temp" not in without


def test_a_hidden_module_hides_its_parts():
    t = theme(
        {"module": "clock", "visible": False},
        {"module": "stat", "col": 1, "needs": "gpu.load"},
    )
    renderer = Renderer(t, preview=True)
    drawn = shown(renderer, snapshot(drop=("gpu.",)))
    assert not any(p.startswith(("m0/", "m1/")) for p in drawn)
    _, boxes = renderer.render(snapshot(drop=("gpu.",)))
    assert "m0" not in boxes and "m1" not in boxes


def test_modules_outside_the_grid_are_moved_in():
    t = theme({"module": "stat", "col": 7, "cols": 4, "row": 5})
    renderer = Renderer(t, preview=True)
    grid = make_grid(1920, 480, None, "turing-9.2-usb")
    assert renderer.module_boxes["m0"] == grid.box(7, 1, 1, 1)


def test_modules_follow_the_look():
    neon = Renderer(theme({"module": "ring"}, look="neon"), preview=True)
    ring = next(w for w in neon.widgets if w["id"] == "m0/ring")
    assert ring["color2"] == LOOKS["neon"]["palette"]["cpu"]
    card = next(w for w in neon.widgets if w["id"] == "m0/card")
    assert card["color"] is None and card["outline"]  # neon: outlined cards
    mono = Renderer(theme({"module": "stat"}, look="mono"), preview=True)
    value = next(w for w in mono.widgets if w["id"] == "m0/value")
    assert value["font"] == "builtin:JetBrainsMono-Bold"


def test_a_module_colour_and_title_win():
    t = theme(
        {"module": "ring", "color": "@hot", "title": "Main"},
        palette={"hot": "#FF0000"},
    )
    renderer = Renderer(t, preview=True)
    parts = {w["id"]: w for w in renderer.widgets}
    assert parts["m0/ring"]["color2"] == "#FF0000"
    assert parts["m0/label"]["text"] == "MAIN"


def test_every_look_has_every_role():
    for key in LOOKS:
        palette, style = look_theme_parts(key)
        assert set(ROLES) <= set(palette), key
        assert set(style) == set(module_info()["style"])
    info = module_info()
    assert set(info["kinds"]) == set(MODULE_KINDS)
    assert info["kinds"]["ring"]["sources"] == ["cpu", "gpu", "mem", "disk"]


def test_grid_style_and_modules_survive_a_round_trip():
    t = theme(
        {"module": "graph", "source": "sensor", "sensor": "fan.cpu", "cols": 2},
        look="paper",
        grid={"columns": 6, "rows": 2},
    )
    again = parse_theme(t.to_dict())
    assert again.grid == {"columns": 6, "rows": 2, "gap": 0, "margin": 0}
    assert again.style == t.style and again.style["card"] == "flat"
    assert again.widgets == t.widgets


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"style": {"glow": 2}}, "style.glow"),
        ({"style": {"card": "fancy"}}, "style.card"),
        ({"grid": {"columns": 99}}, "grid.columns"),
        ({"widgets": [{"type": "module", "module": "toaster"}]}, "module"),
    ],
)
def test_bad_module_settings_are_refused(change, message):
    data = theme({"module": "clock"}).to_dict()
    data.update(change)
    with pytest.raises(ThemeError, match=message):
        parse_theme(data)


def test_themes_without_modules_draw_as_before():
    t = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": 100, "height": 50},
            "widgets": [{"type": "text", "id": "a", "text": "x"}],
        }
    )
    renderer = Renderer(t, preview=True)
    assert renderer.widgets == t.widgets and renderer.module_boxes == {}


def test_needs_takes_several_conditions():
    t = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": 100, "height": 50},
            "widgets": [
                {"type": "text", "id": "both", "text": "x", "needs": "cpu.load, !gpu.load"},
            ],
        }
    )
    renderer = Renderer(t, preview=True)
    assert shown(renderer, snapshot()) == []
    assert shown(renderer, snapshot(drop=("gpu.",))) == ["both"]


@pytest.mark.parametrize("fit", ["shrink", "ellipsis"])
def test_text_is_made_to_fit(fit):
    def width(**extra):
        t = parse_theme(
            {
                "format": "libre-panel-theme/1",
                "display": {"width": 400, "height": 100},
                "widgets": [
                    {"type": "text", "id": "t", "text": "A rather long processor name",
                     "font_size": 30, **extra},
                ],
            }
        )  # fmt: skip
        _, boxes = Renderer(t, preview=True).render(snapshot())
        return boxes["t"][2]

    free = width()
    assert free > 200
    assert width(max_width=150, fit=fit) <= 152


@pytest.mark.parametrize("model", ["turing-3.5", "turing-12.3-usb", "turing-2.1"])
def test_modules_move_onto_another_panel_without_overlapping(model):
    from libre_panel.theme.adapt import adapt_theme
    from libre_panel.theme.modules import templates

    modules = templates(8, 2)[0]["modules"]
    data = theme(*modules).to_dict()
    adapted = parse_theme(adapt_theme(data, model, "landscape"))
    grid = make_grid(adapted.width, adapted.height, adapted.grid, adapted.model)
    shown = [w for w in adapted.widgets if w["type"] == "module" and w["visible"]]
    assert shown
    cells = set()
    for w in shown:
        assert w["col"] + w["cols"] <= grid.columns and w["row"] + w["rows"] <= grid.rows
        mine = {(c, r) for c in range(w["col"], w["col"] + w["cols"])
                for r in range(w["row"], w["row"] + w["rows"])}  # fmt: skip
        assert not mine & cells
        cells |= mine


def parts(t, snap=None):
    renderer = Renderer(t, preview=True)
    return {pid.split("/", 1)[1] for pid in shown(renderer, snap or snapshot()) if "/" in pid}


def test_weather_shows_a_forecast_when_there_is_room():
    small = parts(theme({"module": "weather", "cols": 2}))
    assert "temp" in small and not any(p.startswith("f0") for p in small)
    days = parts(theme({"module": "weather", "cols": 4}))
    assert {"f0-label", "f0-icon", "f0-high", "f0-low"} <= days
    renderer = Renderer(theme({"module": "weather", "cols": 4}), preview=True)
    icon = next(w for w in renderer.widgets if w["id"] == "m0/f1-icon")
    assert icon["sensor"] == "weather.day.1.code"
    hours = Renderer(
        theme({"module": "weather", "cols": 4, "forecast": "hours", "step": 2}), preview=True
    )
    labels = [w["sensor"] for w in hours.widgets if w["id"].endswith("-label") and "/f" in w["id"]]
    assert labels[:3] == ["weather.hour.2.time", "weather.hour.4.time", "weather.hour.6.time"]
    off = parts(theme({"module": "weather", "cols": 4, "forecast": "off"}))
    assert not any(p.startswith("f0") for p in off)


def test_a_big_weather_module_shows_days_and_hours():
    both = parts(theme({"module": "weather", "cols": 4, "rows": 2}))
    assert "f0-high" in both and "h0-temp" in both
    days_only = parts(theme({"module": "weather", "cols": 4, "rows": 2, "forecast": "days"}))
    assert "f0-high" in days_only and "h0-temp" not in days_only


def test_a_tall_weather_module_lists_the_forecast():
    tall = Renderer(theme({"module": "weather", "rows": 2}), preview=True)
    rows = [w for w in tall.widgets if w["id"].endswith("-temp") and "/f" in w["id"]]
    assert len(rows) >= 3 and rows[0]["align"] == "right"


def test_forecast_steps_aside_without_its_data():
    snap = snapshot()
    for key in [k for k in snap.readings if k.startswith("weather.day.")]:
        del snap.readings[key]
    drawn = parts(theme({"module": "weather", "cols": 4}), snap)
    assert "temp" in drawn and not any(p.startswith("f0") for p in drawn)


def test_large_calendars_show_the_month():
    assert "calendar" not in parts(theme({"module": "date"}))
    assert "calendar" in parts(theme({"module": "date", "cols": 2, "rows": 2}))
    assert "calendar" in parts(theme({"module": "date", "cols": 4}))
    # a big weather module without weather: the calendar with its month
    big = theme({"module": "weather", "cols": 2, "rows": 2})
    assert "calendar" in parts(big, snapshot(weather=False))


@pytest.mark.parametrize("first_day", ["monday", "sunday"])
def test_month_calendar_widget(first_day):
    t = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": 300, "height": 220},
            "widgets": [{"type": "calendar", "id": "c", "x": 5, "y": 5, "w": 280, "h": 200,
                         "color2": "#ff0000", "first_day": first_day}],
        }
    )  # fmt: skip
    frame, boxes = Renderer(t, preview=True).render(snapshot())
    assert boxes["c"] == [5, 5, 280, 200]
    data = frame.tobytes()
    red = sum(
        1 for i in range(0, len(data), 3) if data[i] > 200 and data[i + 1] < 60 and data[i + 2] < 60
    )
    assert red > 100  # today is marked


def test_weather_icons_follow_their_reading_and_the_night():
    from libre_panel.icons import weather_icon_name

    snap = snapshot()
    snap.readings["weather.hour.3.code"] = Reading("weather.hour.3.code", 0, "", "")
    snap.readings["weather.hour.3.day"] = Reading("weather.hour.3.day", 0, "", "")
    t = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": 100, "height": 100},
            "widgets": [{"type": "icon", "id": "i", "icon": "weather", "size": 40,
                         "sensor": "weather.hour.3.code"}],
        }
    )  # fmt: skip
    renderer = Renderer(t, preview=True)
    renderer.render(snap)
    assert renderer._cache["i"][0][0] == weather_icon_name(0, night=True) == "moon"

"""Modules: the grid, layouts by size, fallbacks, looks and fitting text."""

import pytest

from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import ThemeError, parse_theme
from libre_panel.theme.modules import (
    LOOKS,
    MIN_CELL,
    MODULE_KINDS,
    ROLES,
    expand,
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


def test_a_modules_numbers_count_to_a_new_reading_as_its_rings_move():
    """The numbers glide as the SPUR II mod's did, not jump once a second; a name does not."""
    parts, _ = expand(theme({"kind": "cpu", "col": 0, "row": 0, "cols": 2, "rows": 2}))
    metrics = {w["format"]: w.get("smooth") for w in parts if w["type"] == "metric"}
    assert metrics["{value:.0f}°C"] and metrics["{value:.0f}%"]
    assert metrics["{value}"] is False  # the processor's name


REPORTS = ("round-report", "round-report-vertical", "round-report-1920x720",
           "round-report-1280x720", "round-report-480x480", "spur-round-report")  # fmt: skip


def test_every_number_of_a_built_in_theme_counts_to_a_new_reading():
    import json

    from libre_panel.render.formatting import counts
    from libre_panel.theme.model import builtin_themes_dir

    for path in sorted(builtin_themes_dir().glob("*/theme.json")):
        if path.parent.name in REPORTS:  # a round's figures, fixed
            continue
        for widget in json.loads(path.read_text(encoding="utf-8"))["widgets"]:
            if widget["type"] == "metric" and counts(widget["format"]):
                assert widget.get("smooth"), (path.parent.name, widget["id"])


def test_the_fan_instrument_shows_the_fan_it_is_given_by_the_name_it_goes_by():
    parts, _ = expand(theme({"module": "instrument", "source": "fan", "sensor": "fan.lpc.fan_2",
                             "col": 0, "row": 0, "cols": 1, "rows": 2}))  # fmt: skip
    title = next(w for w in parts if w["id"].endswith("/title"))
    assert (title["type"], title["sensor"], title["format"]) == ("metric", "fan.lpc.fan_2",
                                                                   "{label:upper}")  # fmt: skip
    assert {w["sensor"] for w in parts if w.get("sensor")} == {"fan.lpc.fan_2"}
    named, _ = expand(theme({"module": "instrument", "source": "fan", "title": "Radiator",
                             "col": 0, "row": 0, "cols": 1, "rows": 2}))  # fmt: skip
    assert next(w for w in named if w["id"].endswith("/title"))["text"] == "RADIATOR"


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


def test_the_memory_module_shows_the_memorys_name_like_the_others_show_theirs():
    t = theme({"module": "ring", "source": "mem", "cols": 2, "rows": 2})
    assert "m0/name" in shown(Renderer(t, preview=True), snapshot())
    without = snapshot(drop=("mem.name",))
    assert "m0/name" not in shown(Renderer(t, preview=True), without)  # a missing name is hidden
    name = [w for w in Renderer(t, preview=True).widgets if w["id"] == "m0/name"]
    assert [w["sensor"] for w in name] == ["mem.name"]


def test_the_system_module_has_the_memory_where_a_fourth_row_fits():
    def sensors(rows):
        renderer = Renderer(theme({"module": "system", "cols": 2, "rows": rows}), preview=True)
        return [w["sensor"] for w in renderer.widgets if w.get("sensor")]

    # as before in a box for three rows (uptime stays), the names together in a taller one
    assert sensors(1) == ["cpu.name", "gpu.name", "sys.uptime"]
    assert sensors(2) == ["cpu.name", "gpu.name", "mem.name", "sys.uptime"]


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


@pytest.mark.parametrize("size", [(160, 80), (80, 160), (320, 480), (480, 320), (1920, 480)])
def test_a_grid_has_no_more_cells_than_fit_in_the_frame(size):
    """Lumen's grid of 48 x 48 cells with a gap of 1 px and a margin of 16 asks a 0.96" panel for
    cells of a fraction of a pixel; a cell is at least a few pixels, and the grid keeps within
    the margins (it ran 40 px past the frame)."""
    grid = make_grid(*size, {"columns": 48, "rows": 48, "gap": 1, "margin": 16})
    assert grid.cell_w >= MIN_CELL and grid.cell_h >= MIN_CELL
    x, y, w, h = grid.box(0, 0, grid.columns, grid.rows)
    assert (x, y) == (grid.x, grid.y) and x + w <= size[0] - 16 and y + h <= size[1] - 16
    assert grid.columns <= 48 and grid.rows <= 48


def test_a_finer_grid_than_48_halves_where_its_cells_would_be_too_small():
    """Lumen's 96 columns place its card half a cell in; on a 3.5" panel they are its 48 again,
    so its modules keep whole cells."""
    assert make_grid(1920, 480, {"columns": 96, "rows": 48, "gap": 1, "margin": 16}).columns == 96
    assert make_grid(480, 320, {"columns": 96, "rows": 48, "gap": 1, "margin": 16}).columns == 48


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


@pytest.mark.parametrize(
    ("palette", "color"),
    [({"surface": "#fff", "accent": "teal"}, "#f80"), ({"bg": "rgb(10, 20, 30)"}, "#FF880080")],
)
def test_modules_take_every_colour_a_theme_may_hold(palette, color):
    t = theme({"module": "ring", "cols": 2}, {"module": "stat", "color": color}, palette=palette)
    renderer = Renderer(t, preview=True)
    renderer.render(snapshot())
    assert renderer.warnings == []
    parts = {w["id"]: w for w in renderer.widgets}
    # written out in full, with the alpha it has
    assert parts["m1/icon"]["color"] == {"#f80": "#FF8800", "#FF880080": "#FF880080"}[color]
    assert parts["m1/value"]["color"].startswith("#")


def test_a_role_that_has_alpha_keeps_it_in_the_modules():
    """A translucent track or card (``#FFFFFF14``) is what the theme says: it was made opaque
    (a white track), and a card's glass came with the 90 % it adds to a colour."""
    palette = {"surface": "#14202E80", "line": "#FFFFFF22", "track": "#FFFFFF14",
               "bg2": "#06090F80"}  # fmt: skip

    def parts(*modules, colours=palette, **extra):
        renderer = Renderer(theme(*modules, palette=colours, **extra), preview=True)
        return {w["id"]: w for w in renderer.widgets}

    glass = parts({"module": "ring"})
    assert glass["m0/card"]["color"] == "#14202E73"  # the glass's 90 % of the palette's 50 %
    assert glass["m0/card"]["outline"] == "#FFFFFF22"
    assert glass["m0/ring"]["background"] == "#FFFFFF14"
    assert parts({"module": "ring"}, style={"card": "flat"})["m0/card"]["color"] == "#14202E80"
    picture = parts({"module": "image", "items": "a.png", "title": "A"})
    assert picture["m0/shade"]["color2"] == "#06090F66"  # a shade of the ground, 80 % of it
    plain = parts({"module": "ring"}, colours={"surface": "#14202E", "track": "teal"})
    assert plain["m0/card"]["color"] == "#14202EE6" and plain["m0/ring"]["background"] == "#008080"


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


def test_list_modules_stay_in_their_cells():
    t = theme(
        {"module": "temps", "cols": 1, "rows": 2},
        {"module": "cores", "col": 1, "cols": 2},
        {"module": "drives", "col": 1, "row": 1, "cols": 2},
        {"module": "processes", "col": 3, "cols": 1, "rows": 2},
        {"module": "netinfo", "col": 4, "cols": 2, "rows": 2},
        {"module": "battery", "col": 6},
        {"module": "values", "col": 6, "row": 1, "cols": 2},
    )
    renderer = Renderer(t, preview=True)
    renderer.render(snapshot())
    assert renderer.warnings == [] and t.warnings == []
    for part in renderer.widgets:
        module = part.get("_module")
        if not module or "w" not in part:
            continue
        x, y, w, h = renderer.module_boxes[module]
        assert x <= part["x"] and part["x"] + part["w"] <= x + w + 1, part["id"]
        assert y <= part["y"] and part["y"] + part["h"] <= y + h + 1, part["id"]


def test_without_a_battery_the_module_says_so():
    renderer = Renderer(theme({"module": "battery", "cols": 2}), preview=True)
    with_one = shown(renderer, snapshot())
    assert {"m0/value", "m0/charge", "m0/state"} <= set(with_one) and "m0/none" not in with_one
    without = shown(renderer, snapshot(drop=("battery.",)))
    assert "m0/none" in without and "m0/value" not in without and "m0/card" in without


def test_list_modules_take_their_readings_and_sorting():
    def lists_of(**module):
        renderer = Renderer(theme({"cols": 2, "rows": 2, **module}), preview=True)
        return {w["id"]: w for w in renderer.widgets if w["type"] == "list"}

    assert lists_of(module="processes")["m0/processes"]["items"] == "proc.cpu.*.value"
    assert lists_of(module="processes", sort="memory")["m0/processes"]["items"] == (
        "proc.mem.*.value"
    )
    temps = lists_of(module="temps")
    assert temps["m0/temps"]["items"].startswith("cpu.temp") and "m0/fans" in temps
    assert temps["m0/fans"]["style"] == "fans" and temps["m0/fans"]["max"] == 3000  # turning
    assert temps["m0/fans"]["max_items"] == 6 and temps["m0/fans"]["items"].startswith("gpu.fan")
    assert "m0/fans" not in lists_of(module="temps", fans=0)  # the temperatures alone
    mine = lists_of(module="temps", fans=2, fan_items="fan.lpc.fan_2 = Front")["m0/fans"]
    assert mine["max_items"] == 2 and mine["items"] == "fan.lpc.fan_2 = Front"
    mine = lists_of(module="values", items="cpu.load = CPU\nfan.*")
    assert mine["m0/values"]["items"] == "cpu.load = CPU\nfan.*"
    assert module_info()["kinds"]["drives"]["items"] == "disk.*.load"
    assert module_info()["kinds"]["ring"]["items"] == ""


def test_a_wide_or_long_temperature_module_has_its_fans_turning_beside():
    """Arena on the 9.2" and the 5.2": as many fans as are readable (one column there)."""
    from libre_panel.theme.model import builtin_themes_dir, load_theme

    for name in ("arena", "arena-1280x720"):
        renderer = Renderer(load_theme(builtin_themes_dir() / name), preview=True)
        widgets = {w["id"]: w for w in renderer.widgets}
        fans, temps = widgets["m4/fans"], widgets["m4/temps"]
        assert fans["style"] == "fans" and fans["x"] > temps["x"] + temps["w"]
        assert fans["font_size"] >= 14 and renderer._list_room(fans, 6) >= 3, name


@pytest.mark.parametrize("span", [(1, 1), (2, 1), (2, 2)])
def test_cores_show_every_core(span):
    renderer = Renderer(theme({"module": "cores", "cols": span[0], "rows": span[1]}), preview=True)
    (cores,) = [w for w in renderer.widgets if w["id"] == "m0/cores"]
    assert cores["style"] == ("cells" if span == (1, 1) else "columns")
    assert renderer._list_room(cores, 8) == 8
    (threads,) = [w for w in renderer.widgets if w["id"] == "m0/threads"]
    assert (cores["items"], threads["items"]) == ("cpu.pcore.*.load", "cpu.core.*.load")
    assert "!cpu.pcore.1.load" in threads["needs"]  # (the threads where no core is told)


def test_cores_show_every_thread_where_asked():
    theme_ = theme({"module": "cores", "cols": 2, "rows": 1, "threads": True})
    lists = [w for w in Renderer(theme_, preview=True).widgets if w["type"] == "list"]
    assert [(w["id"], w["items"]) for w in lists] == [("m0/cores", "cpu.core.*.load")]


def test_a_big_game_module_says_what_holds_the_frame_rate_down_and_its_longest_frame():
    renderer = Renderer(theme({"module": "game", "cols": 3, "rows": 3}, model="turing-12.3-usb"),
                        preview=True)  # fmt: skip
    snap = snapshot()  # the demo's game: held by the GPU
    drawn = shown(renderer, snap)
    assert {"m0/limiter-label", "m0/limiter-gpu", "m0/peak"} <= set(drawn)
    assert "m0/limiter-cpu" not in drawn and "m0/limiter-cap" not in drawn
    snap.readings["game.limiter"] = Reading("game.limiter", "cap", "", "Limited by")
    drawn = shown(renderer, snap)
    assert "m0/limiter-cap" in drawn and "m0/limiter-gpu" not in drawn
    del snap.readings["game.limiter"]  # not known (PresentMon 1): no row
    assert not {"m0/limiter-label", "m0/limiter-cap"} & set(shown(renderer, snap))


@pytest.mark.parametrize("box", [[0, 0, 400, 30], [0, 0, 120, 25], [0, 0, 300, 40]])
def test_the_sun_in_a_box_too_low_for_its_arc_keeps_the_times(box):
    """Moved freely in the editor to a low, wide place, the arc's size came out below none."""
    from libre_panel.theme import modules as M

    t = theme({"module": "sun", "cols": 2, "rows": 1})
    style = {**M.STYLE_DEFAULTS, **(t.style or {}), M.FRAME_U: min(t.width, t.height) / 2}
    out = M.build_module(t.widgets[0], box, M.colors_of(t.palette), style)
    assert all(w["w"] >= M.ARC_MIN for w in out if w["id"].endswith("arc"))
    assert {w["id"].split("/")[-1] for w in out} >= {"rise", "set"}


def test_a_label_in_chinese_is_measured_in_the_font_that_draws_it(tmp_path):
    """Barlow has no Chinese: a label is measured, as it is drawn, in the system's font for it,
    so the rows under a ring are laid out for it and the label is not shrunk to a speck."""
    from fake_fonts import make_font
    from PIL import ImageFont

    from libre_panel.fonts import builtin_font_path, fallback
    from libre_panel.theme import modules

    square = make_font(tmp_path / "han.ttf", "温度功率频率")  # each a square, as a Han character
    before = fallback.set_fallbacks(fallback.Fallbacks([(str(square), None, 0)]))
    modules._width_per_size.cache_clear()
    try:
        assert modules._width_per_size("builtin:Barlow-Medium", "温度") == pytest.approx(2.0)
        barlow = ImageFont.truetype(str(builtin_font_path("builtin:Barlow-Medium")), 100)
        assert modules._width_per_size("builtin:Barlow-Medium", "TEMP") == pytest.approx(
            barlow.getlength("TEMP") / 100
        )  # what Barlow has, as before
        assert modules._width_per_size("my-own.ttf", "温度") == 2.0  # a font of the theme's own
    finally:
        fallback.set_fallbacks(before)
        modules._width_per_size.cache_clear()


def test_cores_without_the_total_leave_out_the_processors_load_and_its_history():
    """A companion's cores: the screen beside it shows the processor's load already."""
    for model, cols, rows in (("turing-12.3-usb", 3, 3), ("turing-9.2-usb", 3, 2)):
        on, _ = expand(theme({"module": "cores", "cols": cols, "rows": rows}, model=model))
        off, _ = expand(theme({"module": "cores", "cols": cols, "rows": rows, "total": False},
                              model=model))  # fmt: skip
        assert "m0/value" in {w["id"] for w in on}
        assert not {w.get("sensor") for w in off} & {"cpu.load"}
        assert not [w for w in off if w["type"] == "graph"]
        cores = [w for w in off if w["type"] == "list"]
        assert cores and {w["style"] for w in cores} == {"columns"}  # tall: no tiles
    big, _ = expand(theme({"module": "cores", "cols": 3, "rows": 3}, model="turing-12.3-usb"))
    assert [w for w in big if w["type"] == "graph" and w["sensor"] == "cpu.load"]


def test_the_weather_without_weather_says_so_where_it_is_not_to_show_the_date():
    """Beside a screen with the date (a companion), the weather card shows no calendar while
    no weather is set up: it says that none is."""
    auto, _ = expand(theme({"module": "weather", "cols": 4, "rows": 2}))
    assert {w["type"] for w in auto if w.get("needs") == "!weather.temperature"} >= {"clock"}
    none, _ = expand(theme({"module": "weather", "cols": 4, "rows": 2, "fallback": "none"}))
    without = [w for w in none if w.get("needs") == "!weather.temperature"]
    assert [w["id"] for w in without] == ["m0/setup"] and without[0]["text"] == "NO WEATHER SET UP"
    assert not [w for w in none if w["type"] in ("clock", "calendar")]


def test_the_night_over_the_suns_arc_needs_the_suns_times():
    """Without a place there are no sun times: the moon alone, no night symbol over it."""
    parts, _ = expand(theme({"module": "sun", "cols": 3, "rows": 2}, model="turing-12.3-usb"))
    night = next(w for w in parts if w["id"].endswith("night-icon"))
    assert set(night["needs"].split(",")) == {"sun.rise", "!sun.progress"}


@pytest.mark.parametrize(
    ("model", "cols", "rows"), [("turing-12.3-usb", 5, 3), ("turing-9.2-usb", 4, 2)]
)
def test_the_games_graph_runs_from_one_edge_of_its_card_to_the_other(model, cols, rows):
    """Arena's frame rate on the 12.3" stopped short of the card's edges: under the numbers
    it spans the card inside its line, its glow kept between them (``clip``), and ends above
    the rounded corners, where its fill has faded out."""
    parts, _ = expand(theme({"module": "game", "cols": cols, "rows": rows}, model=model))
    card = next(w for w in parts if w["id"] == "m0/card")
    graph = next(w for w in parts if w["type"] == "graph")
    line = card["outline_width"]
    assert graph["x"] == card["x"] + line and graph["w"] == card["w"] - 2 * line
    assert graph["clip"] is True and graph["min"] is None and graph["max"] is None
    bottom = graph["y"] + graph["h"]
    assert card["y"] + card["h"] - card["radius"] <= bottom <= card["y"] + card["h"] - line

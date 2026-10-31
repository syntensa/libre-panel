"""The look of a theme: finishes (render.finish, render.lit, render.light3d)
and the panel's look block (render.look: grid, panel light)."""

import math
import os
import sys
import time
from datetime import UTC, datetime

import numpy as np
import pytest
from PIL import Image, ImageChops

from libre_panel.config import config_dir
from libre_panel.render import finish as finishes
from libre_panel.render import light3d, lit
from libre_panel.render.finish import Finish, register
from libre_panel.render.light import sun_position
from libre_panel.render.look import (
    LightEdge,
    Sweeps,
    grid_image,
    outline,
    ridge_normals,
    rounded_ridge_normals,
)
from libre_panel.render.renderer import Piece, Renderer, changed_region
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import (
    LOOK_SPECS,
    THEME_FORMAT,
    ThemeError,
    builtin_themes_dir,
    load_theme,
    parse_theme,
)

NOON = datetime(2024, 3, 20, 12, 0, tzinfo=UTC)


def theme_of(widgets, look=None, size=(480, 320), **extra):
    data = {
        "format": THEME_FORMAT,
        "name": "look test",
        "display": {"width": size[0], "height": size[1]},
        "background": {"color": "#0A1119"},
        "widgets": widgets,
        **extra,
    }
    if look is not None:
        data["look"] = look
    return parse_theme(data)


def snap(load=60.0, now=NOON, **readings):
    found = {"cpu.load": Reading("cpu.load", load, "%")}
    found.update({key: Reading(key, value) for key, value in readings.items()})
    return Snapshot(readings=found, now=now)


def lit_widgets(finish="lit"):
    track = {"color": "#22D3EE", "background": "#162332", "sensor": "cpu.load", "finish": finish}
    return [
        {"type": "gauge", "id": "ring", "x": 10, "y": 10, "w": 80, "h": 80, "thickness": 10,
         **track},
        {"type": "bar", "id": "bar", "x": 110, "y": 20, "w": 200, "h": 14, "glow": 1.0, **track},
        {"type": "bar", "id": "up", "x": 330, "y": 10, "w": 14, "h": 120, "direction": "up",
         **track},
        {"type": "graph", "id": "graph", "x": 10, "y": 150, "w": 300, "h": 100,
         "sensor": "cpu.load", "color": "#22D3EE", "glow": 1.0, "finish": finish},
        {"type": "icon", "id": "sky", "x": 380, "y": 170, "icon": "weather", "size": 64,
         "color": "#D8E2F0", "finish": finish},
    ]  # fmt: skip


def history(load=60.0):
    found = snap(load)
    found.history["cpu.load"] = [20 + 30 * np.sin(i / 9) ** 2 for i in range(120)]
    found.readings["weather.code"] = Reading("weather.code", 3)  # overcast: clouds
    return found


# -- finishes ---------------------------------------------------------------------------


def test_lit_widgets_keep_their_place_and_size_but_not_their_look():
    flat, lit_ = theme_of(lit_widgets("flat")), theme_of(lit_widgets())
    assert lit_.warnings == []
    a, flat_boxes = Renderer(flat).render(history(), 0.0)
    b, lit_boxes = Renderer(lit_).render(history(), 0.0)
    assert lit_boxes == flat_boxes
    for wid in ("ring", "bar", "up", "graph", "sky"):
        x, y, w, h = lit_boxes[wid]
        box = (x, y, x + w, y + h)
        assert changed_region(a.crop(box), b.crop(box)) is not None, wid


def test_a_lit_bar_is_brightest_at_its_head():
    theme = theme_of([lit_widgets()[1]])  # 200 px from x 110, half full: its head at x 210
    frame, _ = Renderer(theme).render(snap(50.0), 0.0)
    light = np.asarray(frame.convert("L"), np.int16)
    bar, beside = light[27], light[15]  # its middle, and 5 px above it
    assert bar[120:150].mean() > 200  # the value is light
    assert bar[195:210].max() > bar[120:150].mean() + 5  # the head runs whiter still
    assert beside[195:210].max() > 1.6 * beside[120:150].mean()  # its glow rises to the head
    assert bar[230:300].max() < bar[120:150].mean() / 3  # past the head: the dark groove


def schein_balken(length, thick, frac, colour):
    """SPUR II's glow source for a bar (licht.schein_balken in its frame), drawn
    as SPUR II draws it: the reference for lit.bar_piece's glow."""
    from PIL import ImageDraw

    layer = Image.new("RGB", (length, thick))
    draw = ImageDraw.Draw(layer)
    x0, x1, y0, y1 = 1, int((length - 2) * frac), 1, thick - 2
    tail = 0.55 * 0.34

    def shade(share, white=0.0):
        rgb = [int(c * share) for c in colour]
        return tuple(int(c + (255 - c) * white) for c in rgb) if white > 0 else tuple(rgb)

    head = min(30, x1 - x0)
    if x1 - head > x0:
        draw.rectangle([x0, y0, x1 - head, y1], fill=shade(tail))
    for i in range(7):
        a0, a1, t = x1 - head + head * i / 7, x1 - head + head * (i + 1) / 7, (i + 1) / 7
        draw.rectangle([a0, y0, a1, y1], fill=shade(tail + (1 - tail) * t**1.4, 0.65 * t**2))
    return np.asarray(layer)


@pytest.mark.parametrize("frac", [0.615, 0.08, 1.0])
def test_a_lit_bars_glow_comes_from_spur_iis_source(frac):
    colour = (19, 229, 215)
    unit = np.asarray(colour, np.float32) / 255
    spans = [(0.0, frac * 194, 1.0)]
    ours = lit._glow_source(194, 12, spans, unit, 30.0, False)
    assert np.array_equal(ours, schein_balken(194, 12, frac, colour))
    mirrored = lit._glow_source(194, 12, [(194 - frac * 194, 194.0, 1.0)], unit, 30.0, True)
    assert np.array_equal(mirrored, ours[:, ::-1])  # filling from the far end


def test_a_lit_bars_glow_is_light_added_over_it():
    bar = lit_widgets()[1]
    theme = theme_of([bar])
    renderer = Renderer(theme)
    frame, _ = renderer.render(snap(50.0), 0.0)
    piece = dict(renderer._last[0])["bar"]
    light = piece.light
    assert light is not None and light.layer.mode == "RGBA"
    plain, _ = Renderer(theme_of([{**bar, "glow": 0.0}])).render(snap(50.0), 0.0)
    for x, y in ((200, 14), (205, 27), (150, 40)):  # above, on and below the bar
        added = light.layer.getpixel((x - light.x, y - light.y))[:3]
        under = plain.getpixel((x, y))
        expected = tuple(min(255, a + b) for a, b in zip(under, added, strict=True))
        assert frame.getpixel((x, y)) == expected  # light adds up


def test_a_glow_area_keeps_a_bars_glow_inside_it():
    bar = {**lit_widgets()[1], "glow_radius": 12}
    area = [100, 24, 230, 30]  # from below the bar's top: none of its glow above that
    inside, _ = Renderer(theme_of([{**bar, "glow_area": area}])).render(snap(50.0), 0.0)
    around, _ = Renderer(theme_of([bar])).render(snap(50.0), 0.0)
    plain, _ = Renderer(theme_of([{**bar, "glow": 0.0}])).render(snap(50.0), 0.0)
    a, b, c = (np.asarray(f, np.int16) for f in (inside, around, plain))
    assert (a[:24] == c[:24]).all() and (b[10:20, 150:210] > c[10:20, 150:210]).any()
    assert (a[24:54, 150:210] > c[24:54, 150:210]).any()  # inside: it glows
    assert (a[54:] == c[54:]).all()  # below the area, nothing either


@pytest.mark.parametrize(
    ("area", "message"),
    [([1, 2, 3], r"expected \[x, y, w, h\]"), ([0, 0, True, 4], r"expected \[x, y, w, h\]"),
     ([0, 0, 0, 10], "its width and height must be above 0")],
)  # fmt: skip
def test_a_glow_area_is_a_box_on_the_panel(area, message):
    with pytest.raises(ThemeError, match=message):
        theme_of([{**lit_widgets()[1], "glow_area": area}])
    assert theme_of([{**lit_widgets()[1], "glow_area": [1.0, 2, 3, 4]}]).widgets[0][
        "glow_area"
    ] == [1, 2, 3, 4]


def test_lit_values_lie_in_a_milled_groove():
    bar = {**lit_widgets()[1], "glow": 0.0, "y": 20, "h": 12}  # a 12 px groove: 2 px bevels
    frame, _ = Renderer(theme_of([bar])).render(snap(0.0), 0.0)
    column = np.asarray(frame, np.int16)[20:32, 250]
    floor = (0x16, 0x23, 0x32)
    assert tuple(column[6]) == floor  # the floor: exactly its colour
    assert column[0].sum() < sum(floor) < column[-1].sum()  # in shade above, lit below
    ring = {**lit_widgets()[0], "x": 20, "y": 20, "w": 120, "h": 120, "start_angle": -90,
            "end_angle": 270}  # fmt: skip
    frame, _ = Renderer(theme_of([ring], size=(160, 160))).render(snap(0.0), 0.0)
    across = np.asarray(frame, np.int16)[80, 20:30]  # nine o'clock, from the outer edge in
    assert tuple(across[5]) == floor and across[0].sum() != across[9].sum()


def test_a_lit_text_without_opacity_is_only_its_light():
    word = {"type": "text", "id": "word", "x": 20, "y": 20, "text": "II", "font_size": 58,
            "font": "builtin:Barlow-Bold", "color": "#13E5D7"}  # fmt: skip
    glow = {**word, "id": "glow", "finish": "lit", "glow": 0.6, "glow_radius": 16, "opacity": 0}
    over, _ = Renderer(theme_of([word, glow])).render(snap(), 0.0)
    bare, _ = Renderer(theme_of([word])).render(snap(), 0.0)
    alone, _ = Renderer(theme_of([glow])).render(snap(), 0.0)
    a, b, c = (np.asarray(f, np.int16) for f in (over, bare, alone))
    letters = (b == (0x13, 0xE5, 0xD7)).all(-1)
    assert letters.sum() > 100 and (a[letters] > b[letters]).any()  # the light lies on them
    ground = np.asarray(Renderer(theme_of([])).render(snap(), 0.0)[0], np.int16)
    assert (c[letters] != (0x13, 0xE5, 0xD7)).any(-1).all()  # alone: no letters, only light
    assert (c >= ground).all() and (c > ground).any()


def test_a_lit_texts_glow_area_keeps_its_light_inside():
    text = {"type": "text", "id": "t", "x": 20, "y": 20, "text": "CPU", "font_size": 40,
            "color": "#13E5D7", "glow": 0.6, "glow_radius": 8, "finish": "lit",
            "glow_area": [0, 0, 480, 40]}  # fmt: skip
    cut, _ = Renderer(theme_of([text])).render(snap(), 0.0)
    whole, _ = Renderer(theme_of([{**text, "glow_area": None}])).render(snap(), 0.0)
    a, b = np.asarray(cut, np.int16), np.asarray(whole, np.int16)
    assert (a[:40] == b[:40]).all()  # inside the area, the same light
    bare, _ = Renderer(theme_of([{**text, "glow": 0.0}])).render(snap(), 0.0)
    assert (a[40:] == np.asarray(bare, np.int16)[40:]).all()  # below it, none
    assert (b[40:] != a[40:]).any()


def test_a_lit_texts_glow_is_light_over_it_and_around_it():
    text = {"type": "text", "id": "t", "x": 20, "y": 20, "text": "CPU", "font_size": 40,
            "color": "#13E5D7", "glow": 0.6, "glow_radius": 8}  # fmt: skip
    lit_theme = theme_of([{**text, "finish": "lit"}])
    assert lit_theme.warnings == []
    shine, _ = Renderer(lit_theme).render(snap(), 0.0)
    halo, _ = Renderer(theme_of([text])).render(snap(), 0.0)  # the renderer's glow
    bare, _ = Renderer(theme_of([{**text, "glow": 0.0}])).render(snap(), 0.0)
    a, b, c = (np.asarray(f, np.int16) for f in (shine, halo, bare))
    letters = (c == (0x13, 0xE5, 0xD7)).all(-1)  # where the letters cover all
    assert letters.sum() > 100
    assert (a[letters] >= c[letters]).all() and (a[letters] > c[letters]).any()  # over it
    assert (b[letters] == c[letters]).all()  # the renderer's glow lies beneath the letters
    top = np.flatnonzero(letters.any(1))[0]
    assert a[top - 4, 20:100].sum() > c[top - 4, 20:100].sum()  # and around it


def test_too_small_for_light_is_drawn_flat():
    thin = [{**lit_widgets()[1], "h": 1}, {**lit_widgets()[0], "thickness": 1},
            {**lit_widgets()[4], "size": 12}]  # fmt: skip
    flat = [{**widget, "finish": "flat"} for widget in thin]
    a, _ = Renderer(theme_of(thin)).render(history(), 0.0)
    b, _ = Renderer(theme_of(flat)).render(history(), 0.0)
    assert changed_region(a, b) is None


def test_a_finish_a_type_does_not_have_is_drawn_flat_with_one_warning():
    widgets = [
        {"type": "icon", "id": "a", "x": 10, "y": 10, "icon": "cpu", "finish": "lit"},
        {"type": "icon", "id": "b", "x": 10, "y": 60, "icon": "cpu", "finish": "lit"},
        {"type": "icon", "id": "i", "x": 100, "y": 10, "icon": "gpu", "finish": "lit"},
    ]
    theme = theme_of(widgets)
    assert theme.warnings == [
        "finish 'lit' is not available for the icon 'cpu'; drawn flat",
        "finish 'lit' is not available for the icon 'gpu'; drawn flat",
    ]
    flat = theme_of([{**widget, "finish": "flat"} for widget in widgets])
    a, _ = Renderer(theme).render(snap(), 0.0)
    b, _ = Renderer(flat).render(snap(), 0.0)
    assert changed_region(a, b) is None
    assert Renderer(theme)._finishes == {}


def test_a_registered_finish_draws_its_types(monkeypatch):
    monkeypatch.setattr(finishes, "_REGISTERED", dict(finishes._REGISTERED))
    edge = LightEdge.ridge(np.ones((4, 40)), 10, 60)

    class Stamp(Finish):
        name = "stamp"
        types = ("text",)

        def draw(self, renderer, widget, snapshot, now):
            renderer.add_light_edge(edge)
            return Piece(Image.new("RGBA", (40, 20), (255, 0, 0, 255)), widget["x"], widget["y"])

    register(Stamp())
    theme = theme_of(
        [{"type": "text", "id": "t", "x": 10, "y": 10, "text": "CPU"}], look={"light": {}}
    )
    theme.widgets[0]["finish"] = "stamp"  # names outside the theme format: plugins' code
    renderer = Renderer(theme)
    frame, boxes = renderer.render(snap(), 0.0)
    assert frame.getpixel((30, 20)) == (255, 0, 0)
    assert boxes["t"] == [10, 10, 40, 20]
    assert renderer.light_edges == [edge] and renderer.edge_owners[id(edge)] == "t"


def test_modules_hand_their_finish_to_their_parts():
    data = {
        "format": THEME_FORMAT,
        "name": "modules",
        "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
        "widgets": [{"type": "module", "id": "w", "module": "weather", "finish": "lit"}],
    }
    renderer = Renderer(parse_theme(data))
    icons = [w for w in renderer.widgets if w["type"] == "icon"]
    assert icons and all(w["finish"] == "lit" for w in icons)
    assert all(isinstance(renderer._finishes[w["id"]], lit.LitIcon) for w in icons)
    texts = [w for w in renderer.widgets if w["type"] == "weather"]
    assert texts and all(w["id"] not in renderer._finishes for w in texts)


def test_a_theme_without_a_look_pays_nothing_for_it():
    theme = theme_of(lit_widgets("flat"))
    renderer = Renderer(theme)
    assert renderer.look is None and renderer._finishes == {} and not renderer.prepares


# -- light layers ---------------------------------------------------------------------


def test_a_lit_graphs_halo_glows_above_its_line_as_below_it():
    graph = {"type": "graph", "id": "g", "x": 0, "y": 0, "w": 200, "h": 100, "sensor": "cpu.load",
             "min": 0, "max": 100, "color": "#00FF00", "fill": False, "smooth": False,
             "line_width": 2, "history": 20, "glow": 1.0, "glow_radius": 8,
             "finish": "lit"}  # fmt: skip
    found = snap(50.0)
    found.history["cpu.load"] = [50.0] * 20
    frame, _ = Renderer(theme_of([graph], size=(200, 100))).render(found, 0.0)
    column = np.asarray(frame, np.int16)[:, 100, 1]
    line = np.flatnonzero(column == 255)
    above, below = column[line[0] - 2], column[line[-1] + 2]
    assert above > 12 + 0x11 and abs(above - below) <= 2  # over nothing, the halo is as strong


def test_a_colour_is_lit_once_per_step(monkeypatch):
    lit._PICTURES.clear()
    built = []
    real = lit.light

    def light(*args, **kwargs):
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(lit, "light", light)
    bar = lit_widgets()[1]
    theme = theme_of([bar, {**bar, "id": "twin", "y": 60}])
    renderer = Renderer(theme)
    renderer.render(snap(40.0), 0.0)
    assert len(built) == 1  # the value's light: once for both bars
    Renderer(theme).render(snap(40.0), 0.0)  # another renderer, the same colour and step
    assert len(built) == 1
    renderer.render(snap(70.0), 1.0)
    assert len(built) == 2  # a new step


def test_light_runs_towards_white_in_a_halo_of_its_colour():
    mask = np.zeros((20, 40), np.float32)
    mask[8:12, 5:35] = 1
    rgba = lit.light(mask, (19, 229, 215), 4.5).astype(np.int16)
    core, halo, away = rgba[10, 20], rgba[6, 20], rgba[0, 20]
    assert core[3] == 255 and core[0] > 19 + 150  # the core: opaque, most of the way to white
    assert 0 < halo[3] < 255 and tuple(halo[:3]) == (19, 229, 215)  # the halo: its colour
    assert away[3] < halo[3]


def test_the_light_in_a_part_of_a_mask_is_that_part_of_its_whole_light():
    """A ring's tip is lit in a small window of the coverage around it: worked out there alone,
    its halo still from all of the coverage, it is that part of the light of all of it."""
    rng = np.random.default_rng(7)
    mask = np.clip(rng.normal(0.4, 0.4, (130, 120)), 0, 1).astype(np.float32)
    per_column = np.linspace(0, 255, 120)[:, None] * np.ones(3)
    for colour in (np.asarray((19, 229, 215), np.float32), per_column):
        whole = lit.light(mask, colour, 9.0)
        for x0, y0, x1, y1 in ((31, 31, 99, 99), (0, 0, 120, 130), (5, 60, 17, 129)):
            part = lit.light(mask, colour, 9.0, (x0, y0, x1, y1))
            assert np.array_equal(part, whole[y0:y1, x0:x1])


def test_a_lit_ring_is_a_thin_arc_of_light_on_the_middle_of_its_groove():
    ring = {**lit_widgets()[0], "x": 20, "y": 20, "w": 120, "h": 120, "start_angle": -90,
            "end_angle": 270}  # fmt: skip
    frame, _ = Renderer(theme_of([ring], size=(160, 160))).render(snap(50.0), 0.0)
    pixels = np.asarray(frame, np.int16)
    across = pixels[80, 128:142].sum(1)  # three o'clock, half way along the arc: the groove
    bright = np.flatnonzero(across > across.max() / 2)  # is 130 to 139, the light ...
    assert 133 <= 128 + bright.mean() <= 136  # ... lies on its middle
    assert bright.size <= 7  # a thin arc (6 of the groove's 10 px), not a tube
    assert pixels[80, 128 + bright[bright.size // 2], 0] > 0x22 + 100  # its core runs to white
    assert tuple(pixels[80, 25]) == (0x16, 0x23, 0x32)  # nine o'clock: past its end, the groove


def fake_layers(folder, stems):
    folder.mkdir(parents=True)
    for stem in stems:
        for layer in light3d.LAYERS:
            Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(folder / f"{stem}_{layer}.png")


def test_layers_are_kept_as_npz_and_prepared_again_when_a_source_is_newer(tmp_path):
    fake_layers(tmp_path / "probe", ("a", "b"))
    builds = []

    def build(folder):
        builds.append(folder)
        return {"table": np.arange(len(builds) * 4, dtype=np.float32)}

    part = light3d.Part("probe", ("a", "b"), build, tmp_path)
    first = part.get()
    assert builds == [tmp_path / "probe"]
    assert part.cache == config_dir() / "cache" / "light3d" / "probe.npz" and part.cache.is_file()
    again = light3d.Part("probe", ("a", "b"), build, tmp_path).get()  # the next start
    assert len(builds) == 1 and np.array_equal(again["table"], first["table"])

    source = tmp_path / "probe" / "b_gloss.png"
    older = source.stat().st_mtime - 100  # the cache from before a source changed
    os.utime(part.cache, (older, older))
    rebuilt = light3d.Part("probe", ("a", "b"), build, tmp_path).get()
    assert len(builds) == 2 and rebuilt["table"].size == 8
    assert light3d.Part("probe", ("a", "b"), build, tmp_path).get()["table"].size == 8

    stale = light3d.Part("probe", ("a", "b"), build, tmp_path)
    stale.VERSION = light3d.Part.VERSION + 1  # an older format in the cache: prepared again
    stale.get()
    assert len(builds) == 3


def test_layers_nothing_asked_for_go_and_come_back_from_the_cache(tmp_path):
    """The weather bodies are ten megabytes, and a symbol is made from them once: once nothing
    asked for them for a minute they go, and the next that asks reads them again (from the
    cache, as drawn). Whether they can be had is known without reading them again."""
    fake_layers(tmp_path / "probe", ("a",))
    builds = []

    def build(folder):
        builds.append(folder)
        return {"table": np.arange(4, dtype=np.float32)}

    part = light3d.Part("probe", ("a",), build, tmp_path)
    assert part.get() is not None and part.available()
    used = time.monotonic()
    assert not part.forget_idle(used + part.IDLE_S / 2)  # asked for a moment ago: kept
    assert part.forget_idle(used + part.IDLE_S + 1) and part._data is None
    assert part.available() and part._data is None  # (known: not read for that)
    again = part.get()
    assert np.array_equal(again["table"], np.arange(4)) and builds == [tmp_path / "probe"]
    light3d._halved["probe"] = (again["table"], again["table"])
    light3d.PARTS["probe"] = part
    try:
        light3d.forget_idle(time.monotonic() + part.IDLE_S + 1)
        assert part._data is None and "probe" not in light3d._halved  # (made of them)
    finally:
        del light3d.PARTS["probe"]


def test_missing_layers_draw_flat(tmp_path):
    part = light3d.Part("gone", ("a",), lambda folder: {"x": np.zeros(1)}, tmp_path)
    assert part.get() is None and part.loaded


def test_a_warmed_up_renderer_loads_no_layers_in_its_first_frame(monkeypatch):
    loads = []
    real = light3d.Part._load

    def load(self):
        loads.append(self.name)
        return real(self)

    monkeypatch.setattr(light3d.Part, "_load", load)
    for part in light3d.PARTS.values():
        part.reset()
    lit._PICTURES.clear()
    renderer = Renderer(theme_of(lit_widgets(), look={"grid": {}, "light": {}}))
    assert renderer.prepares
    renderer.warm(None, 0.0)
    assert loads == ["weather"]  # bars and rings are light worked out as they come
    loads.clear()
    renderer.render(history(), 0.0)
    assert loads == []

    for part in light3d.PARTS.values():
        part.reset()
    Renderer(theme_of(lit_widgets())).render(history(), 0.0)  # not warmed up: in the frame
    assert loads == ["weather"]


# -- the look block ---------------------------------------------------------------------


def test_look_blocks_are_off_unless_named_and_take_their_defaults():
    assert theme_of([]).look == {}
    theme = theme_of([], look={"grid": True, "light": {"strength": 1.5}})
    assert theme.look["grid"] == {key: spec[1] for key, spec in LOOK_SPECS["grid"].items()}
    assert theme.look["light"]["strength"] == 1.5
    assert theme.look["light"]["sweep_seconds"] == LOOK_SPECS["light"]["sweep_seconds"][1]
    assert theme_of([], look={"grid": False, "light": None}).look == {}
    assert theme.warnings == []


def test_unknown_look_blocks_and_fields_warn_and_are_kept():
    theme = theme_of([], look={"mist": {"amount": 3}, "grid": {"spacing": 10, "glitter": 1}})
    assert theme.warnings == [
        "look: ignoring unknown block 'mist'",
        "look.grid: ignoring unknown field 'glitter'",
    ]
    assert theme.look["mist"] == {"amount": 3}  # a later version's: saved as it came
    assert theme.to_dict()["look"]["mist"] == {"amount": 3}
    again = parse_theme(theme.to_dict())
    assert again.look == theme.look


@pytest.mark.parametrize(
    ("look", "message"),
    [
        ({"grid": {"spacing": 1}}, "look.grid.spacing: must be between 2 and 400"),
        ({"grid": {"alpha": 1.5}}, "look.grid.alpha: must be between 0 and 1"),
        ({"light": {"sweep_seconds": 0.1}}, "look.light.sweep_seconds: must be between"),
        ({"light": {"strength": -1}}, "look.light.strength: must be between 0 and 2"),
        ({"light": {"color": "@nowhere"}}, "look.light.color"),
        ({"grid": "on"}, "look.grid must be an object"),
        ("grid", "look must be an object"),
    ],
)
def test_bad_look_settings_are_refused(look, message):
    with pytest.raises(ThemeError, match=message):
        theme_of([], look=look)


def test_the_grid_lies_on_the_backdrop_under_the_widgets():
    backdrop = {"type": "rect", "id": "back", "w": 480, "h": 320, "color": "#101820"}
    card = {"type": "rect", "id": "card", "x": 200, "y": 100, "w": 100, "h": 100}
    card["color"] = "#405060"
    theme = theme_of([backdrop, card], look={"grid": {"spacing": 10, "major": 4}},
                     palette={"grid": "#182430", "grid_major": "#24384C"})  # fmt: skip
    frame, _ = Renderer(theme).render(snap(), 0.0)
    assert frame.getpixel((10, 5)) == (0x18, 0x24, 0x30)  # a line
    assert frame.getpixel((40, 5)) == (0x24, 0x38, 0x4C)  # every fourth: stronger
    assert frame.getpixel((15, 15)) == (0x10, 0x18, 0x20)  # between the lines: the backdrop
    assert frame.getpixel((210, 110)) == (0x40, 0x50, 0x60)  # the card covers it


def test_grid_colours_come_from_the_background_without_a_palette():
    image = grid_image((40, 40), 8, 2, (20, 20, 20, 255), (40, 40, 40, 255))
    pixels = np.asarray(image)
    assert pixels[3, 8, 0] == 20 and pixels[3, 16, 0] == 40 and pixels[3, 3, 3] == 0
    theme = theme_of([], look={"grid": {}})
    frame, _ = Renderer(theme).render(snap(), 0.0)
    line, ground = frame.getpixel((8, 3)), frame.getpixel((3, 3))
    assert line != ground and max(abs(a - b) for a, b in zip(line, ground, strict=True)) < 40


# -- the panel light --------------------------------------------------------------------


FRAME = {"type": "rect", "id": "frame", "x": 40, "y": 40, "w": 400, "h": 240, "color": None,
         "outline": "#3A4A5C", "outline_width": 4, "ridge": "frame"}  # fmt: skip


def test_ridges_are_mitred_and_flat_in_their_middle():
    mask = np.zeros((9, 9))
    mask[0:3, :] = 1  # a rail three points thick
    normals = ridge_normals(mask)
    assert normals[0, 4, 1] < 0 and normals[2, 4, 1] > 0  # up and down from the middle
    assert tuple(normals[1, 4]) == (0.0, 0.0, 1.0)  # the middle faces the viewer
    assert not normals[5:].any()


def test_a_round_outline_is_a_ridge_all_round():
    ring = rounded_ridge_normals(100, 100, 50, 10)  # a ring 10 px wide
    out, inner = ring[16, 16], ring[20, 20]  # on the diagonal: its outer half, its inner one
    assert out[0] == pytest.approx(out[1], abs=0.02) and out[0] < -0.45  # out, to the top left
    assert inner[0] == pytest.approx(inner[1], abs=0.02) and inner[0] > 0.45  # in
    # along the straight sides of a rect with round corners, as the runs make them
    band = outline(200, 60, 10, 4) > 0
    assert np.allclose(rounded_ridge_normals(200, 60, 10, 4)[:, 20:180][band[:, 20:180]],
                       ridge_normals(band)[:, 20:180][band[:, 20:180]], atol=1e-5)  # fmt: skip


def test_a_ring_with_a_ridge_name_is_lit_without_steps_round_it():
    """Gold Time Vertical's rings (#36): a ridge worked out from runs across rows and columns
    turned at the diagonals in steps of light and shade."""
    ring = {"type": "rect", "id": "rim", "x": 40, "y": 40, "w": 176, "h": 176, "radius": 88,
            "color": None, "outline": "#B8860B", "outline_width": 18, "ridge": "rim"}  # fmt: skip
    frame, _ = Renderer(theme_of([ring])).render(snap(), 0.0)
    a = np.asarray(frame.convert("L"), np.float32)
    turn = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    for across in (84.0, 74.0):  # its outer half and its inner one
        x, y = 128 + across * np.cos(turn), 128 + across * np.sin(turn)
        round_it = a[np.round(y).astype(int), np.round(x).astype(int)]
        assert np.abs(np.diff(round_it)).max() < 24, across
    assert a[128 - 60, 128 - 60] > a[128 + 60, 128 + 60]  # its outer half lit at the top left


def test_rects_with_a_ridge_name_are_ridges_whose_gloss_lies_on_them():
    flat = {**FRAME, "id": "flat", "x": 60, "y": 60, "w": 360, "h": 200, "ridge": ""}
    theme = theme_of([FRAME, flat], look={"light": {}})
    renderer = Renderer(theme)
    frame, _ = renderer.render(snap(), 0.0)
    assert [renderer.edge_owners[id(edge)] for edge in renderer.light_edges] == ["frame"]
    ids = [wid for wid, _ in renderer._last[0]]
    assert ids[ids.index("frame") + 1].startswith("\0light")  # right on its frame
    dark, _ = Renderer(theme_of([FRAME, flat])).render(snap(), 0.0)  # a ridge without the light
    plain, _ = Renderer(theme_of([{**FRAME, "ridge": ""}, flat])).render(snap(), 0.0)
    assert changed_region(dark, plain) is not None and changed_region(frame, dark) is not None
    inner = (62, 62, 418, 258)  # the rect without a name stays flat, light or not
    assert changed_region(frame.crop(inner), plain.crop(inner)) is None


def test_rects_are_flat_unless_a_theme_asks_for_a_ridge():
    rects = [{**FRAME, "ridge": ""}, {**FRAME, "id": "band", "y": 300, "h": 8, "ridge": "",
                                      "color": "#3A4A5C", "outline": None}]  # fmt: skip
    lit_theme = theme_of(rects, look={"light": {}})
    renderer = Renderer(lit_theme)
    frame, _ = renderer.render(snap(), 0.0)
    assert renderer.light_edges == [] and renderer._finishes == {}
    plain, _ = Renderer(theme_of(rects)).render(snap(), 0.0)
    assert changed_region(frame, plain) is None


def test_bands_of_one_ridge_meet_at_a_mitre():
    band = {"type": "rect", "color": "#13E5D7B9", "ridge": "cpu-frame"}
    top = {**band, "id": "top", "x": 40, "y": 40, "w": 200, "h": 5}
    left = {**band, "id": "left", "x": 40, "y": 40, "w": 5, "h": 120}
    renderer = Renderer(theme_of([top, left], look={"light": {}}))
    frame, boxes = renderer.render(snap(), 0.0)
    [edge] = renderer.light_edges  # one ridge: the first band draws it, glossed once
    assert renderer.edge_owners[id(edge)] == "top"
    assert boxes["left"] == [40, 40, 5, 120] and boxes["top"] == [40, 40, 200, 5]
    pieces = dict(renderer._last[0])
    assert pieces["left"].layer.size == (1, 1)  # it only keeps its place for the editor
    apart = Renderer(theme_of([top, {**left, "ridge": "other"}], look={"light": {}}))
    apart.render(snap(), 0.0)  # two names: two ridges
    assert sorted(apart.edge_owners[id(edge)] for edge in apart.light_edges) == ["left", "top"]

    one, _ = Renderer(theme_of([top, left])).render(snap(), 0.0)  # (without the sun's gloss)
    two, _ = Renderer(theme_of([top, {**left, "ridge": "other"}])).render(snap(), 0.0)
    one, two = np.asarray(one, np.int16), np.asarray(two, np.int16)
    assert one[41, 120].sum() > one[43, 120].sum()  # the facet towards the light, and away
    assert (one[38:48, 100:200] == two[38:48, 100:200]).all()  # along a band: the same
    assert (one[40:45, 40:45] != two[40:45, 40:45]).any()  # where they meet: one ridge


def test_a_ridges_glow_is_light_added_also_where_its_colour_is_full():
    band = {"type": "rect", "id": "band", "x": 40, "y": 40, "w": 200, "h": 5,
            "color": "#A3AEFFB9", "ridge": "frame"}  # fmt: skip
    renderer = Renderer(theme_of([band]))
    frame, _ = renderer.render(snap(), 0.0)
    piece = dict(renderer._last[0])["band"]
    assert piece.light is not None
    under = Image.new("RGBA", frame.size, "#0A1119")
    under.alpha_composite(piece.layer, (piece.x, piece.y))
    lit = np.asarray(under.convert("RGB"), np.int16)
    light = np.zeros_like(lit)
    glow = np.asarray(piece.light.layer, np.int16)[..., :3]
    light[piece.light.y : piece.light.y + glow.shape[0], piece.light.x :][:, : glow.shape[1]] = glow
    assert np.array_equal(np.asarray(frame, np.int16), np.minimum(255, lit + light))
    assert frame.getpixel((120, 41))[2] > lit[41, 120, 2]  # blue is full, and still lit up


def test_the_sun_comes_from_where_it_stands():
    theme = theme_of([FRAME], look={"light": {}})
    renderer = Renderer(theme)
    light = renderer.look.light
    assert light.fixed(NOON.timestamp())[0][0][0] == 240  # no place: noon, in the middle
    renderer.place = (0.0, 0.0)
    morning = datetime(2024, 3, 20, 8, 0, tzinfo=UTC).timestamp()
    evening = datetime(2024, 3, 20, 16, 0, tzinfo=UTC).timestamp()
    night = datetime(2024, 3, 20, 0, 0, tzinfo=UTC).timestamp()
    assert sun_position(morning, 0.0, 0.0)[0] < 180 < sun_position(evening, 0.0, 0.0)[0]
    assert light.fixed(morning)[0][0][0] < 240 < light.fixed(evening)[0][0][0]
    [(_position, colour, _strength, _kind)] = light.fixed(night)
    assert colour[2] > colour[0]  # the night light is cool


def test_sweeps_run_at_start_on_the_hour_on_a_new_track():
    settings = {key: default for key, (_kind, default) in LOOK_SPECS["light"].items()}
    sweeps = Sweeps()
    at = datetime(2024, 5, 1, 9, 59, 58)
    sweeps.watch(Snapshot(now=at), 100.0, settings)
    assert sweeps.runs == [(100.0, 1.6)]  # the panel started
    sweeps.watch(Snapshot(now=at), 101.0, settings)
    sweeps.watch(Snapshot(now=at.replace(hour=10, minute=0, second=0)), 102.0, settings)
    assert sweeps.runs == [(102.0, 1.6)]  # the full hour (the first one has run)
    title = {"media.title": Reading("media.title", "A")}
    playing = Snapshot(now=at.replace(hour=10), readings=title)
    sweeps.watch(playing, 103.0, settings)
    assert sweeps.runs == [(102.0, 1.6)]  # the first title known is not a new one
    playing.readings["media.title"] = Reading("media.title", "B")
    sweeps.watch(playing, 104.0, settings)
    assert sweeps.runs[-1] == (104.0, 1.6)
    assert sweeps.active(104.8) == [pytest.approx(0.5)]

    quiet = Sweeps()
    off = {**settings, "on_start": False, "on_hour": False, "on_track": False}
    quiet.watch(Snapshot(now=at), 0.0, off)
    quiet.watch(Snapshot(now=at.replace(hour=10, minute=0)), 1.0, off)
    assert quiet.runs == []


class Kind:
    def __init__(self, name):
        self.name = name


def test_a_sweep_runs_on_the_frames_planned_time():
    theme = theme_of([FRAME], look={"light": {}})
    a, b = Renderer(theme), Renderer(theme)
    for now in (0.0, 0.8):  # the start's sweep, half way at 0.8 s
        frame_a, _ = a.render(snap(), now)
        frame_b, _ = b.render(snap(), now)
        assert changed_region(frame_a, frame_b) is None  # the same time, the same light
    lit_frame = frame_a
    assert a.moving  # the PNG path draws at full rate while it runs
    after, _ = a.render(snap(), 2.0)
    assert a.look.light.running(2.0) == [] and changed_region(lit_frame, after) is not None
    still, _ = a.render(snap(), 3.0)
    assert changed_region(after, still) is None

    a.transition_ended(Kind("cut"), 4.0)
    assert a.look.light.running(4.5) == []
    a.transition_ended(Kind("tube"), 5.0)
    assert len(a.look.light.running(5.5)) == 1
    preview = Renderer(theme, preview=True)
    preview.render(snap(), 0.0)
    assert preview.look.light.running(0.5) == []  # previews have no sweeps


def test_a_sweep_over_no_edge_does_not_keep_the_png_path_drawing_at_full_rate():
    """A sweep only lights edges: with none on the frame it draws nothing, and the PNG path must
    not run at full rate for it (the start's sweep, the hour's, a new track's, a tube's)."""
    plain = {"type": "text", "id": "t", "x": 10, "y": 10, "text": "no edge here"}
    renderer = Renderer(theme_of([plain], look={"light": {}}))
    frame, _ = renderer.render(snap(), 0.0)  # the panel starts: a sweep begins
    assert renderer.look.light.running(0.5) and renderer.light_edges == []
    assert not renderer.moving
    again, _ = renderer.render(snap(), 0.5)
    assert changed_region(frame, again) is None  # (and the sweep drew nothing)
    edged = Renderer(theme_of([FRAME], look={"light": {}}))
    edged.render(snap(), 0.0)
    assert edged.moving  # over a ridge it does


def test_sweeps_carry_on_into_the_next_theme():
    theme = theme_of([FRAME], look={"light": {}})
    first = Renderer(theme)
    first.render(snap(), 0.0)
    second = Renderer(theme, motion=first.motion)
    second.render(snap(), 0.5)
    assert second.look.light.sweeps is first.look.light.sweeps
    assert len(second.look.light.sweeps.runs) == 1  # a new theme is not a new start


EDGY = """
import numpy as np
from PIL import Image

from libre_panel.plugins import WidgetType
from libre_panel.render.look import LightEdge


class Edgy(WidgetType):
    type = "demo.edgy"
    api = 1
    spec = {"w": ("int", 120), "h": ("int", 40)}
    edge = None

    def draw(self, widget, ctx, snapshot, now):
        return Image.new("RGBA", (widget["w"], widget["h"]), (30, 30, 30, 255))

    def edges(self, widget, ctx, snapshot, now):
        if Edgy.edge is None:
            mask = np.zeros((widget["h"], widget["w"]))
            mask[:3, :] = 1
            Edgy.edge = LightEdge.ridge(mask, widget["x"], widget["y"])
        return [Edgy.edge] if ctx.light else []
"""


def test_plugin_widgets_give_the_light_their_edges(plugin_folder):
    package = plugin_folder(EDGY, {"libre_panel.widgets": {"demo.edgy": "Edgy"}})
    widget = {"type": "demo.edgy", "id": "edgy", "x": 100, "y": 100}
    lit_theme = theme_of([widget], look={"light": {}})
    renderer = Renderer(lit_theme)
    renderer.render(snap(), 0.0)
    edge = sys.modules[package].Edgy.edge
    assert renderer.light_edges == [edge] and renderer.edge_owners[id(edge)] == "edgy"
    dark = Renderer(theme_of([widget]))
    dark.render(snap(), 0.0)
    assert dark.light_edges == []


# -- sizes ----------------------------------------------------------------------------


BUILTIN = sorted(path.parent.name for path in builtin_themes_dir().glob("*/theme.json"))


@pytest.mark.parametrize("name", BUILTIN)
def test_a_built_in_theme_has_a_panel_light_only_where_it_has_edges_to_glint_on(name):
    """The light falls on light edges (ridges, a plugin's): the frames of the built-in themes are
    flat lines, so a light there would run its sweeps over nothing."""
    theme = load_theme(builtin_themes_dir() / name)
    renderer = Renderer(theme)
    renderer.render(demo_snapshot(fixed_time=1_700_000_000), 0.0)
    assert "light" not in theme.look or renderer.light_edges, name


def test_spur_classic_is_spur_iis_classic_screen_on_the_bar():
    theme = load_theme(builtin_themes_dir() / "spur-classic")
    assert (theme.width, theme.height) == (1920, 480) and theme.warnings == []
    assert set(theme.look) == {"grid", "dust"}  # (no panel light: its frames are flat)
    renderer = Renderer(theme)
    found = demo_snapshot(fixed_time=1_700_000_000)
    frame, _ = renderer.render(found, 0.0)
    assert renderer.warnings == [] and frame.size == (1920, 480)
    assert {type(f).__name__ for f in renderer._finishes.values()} == {
        "LitBar", "LitText", "FacetGraph", "FacetText", "AcrylicText"
    }  # fmt: skip
    assert sum(w["type"] == "fan" for w in theme.widgets) == 5
    # its frames are flat bands, no ridges (the user's wish): nothing on them catches light
    assert not any(w.get("ridge") for w in theme.widgets) and renderer.light_edges == []
    shown = {w["sensor"] for w in theme.widgets if w["type"] in ("metric", "bar", "graph")}
    assert {key for key in shown if found.value(key) is None} == set()  # the demo has them all


def test_spur_classics_ram_heading_is_the_memorys_name_or_else_its_size():
    theme = load_theme(builtin_themes_dir() / "spur-classic")
    heading = next(w for w in theme.widgets if w["id"] == "mem-name")
    assert (heading["sensor"], heading["format"]) == ("mem.name", "{value}")
    frames = {}
    for name in ("Kingston DDR5-5600 32 GB", "32 GB"):  # a found name, or the size alone
        found = demo_snapshot(fixed_time=1_700_000_000)
        found.readings["mem.name"] = Reading("mem.name", name, "", "Memory")
        frames[name] = Renderer(theme).render(found, 0.0)[0].convert("RGB")
    left, top = heading["x"], heading["y"]
    box = (left, top, left + heading["max_width"], top + 2 * heading["font_size"])
    changed = ImageChops.difference(*frames.values()).getbbox()
    assert changed is not None and box[0] <= changed[0] and changed[2] <= box[2]  # only there
    assert box[1] <= changed[1] and changed[3] <= box[3]


@pytest.mark.parametrize("size", [(480, 320), (320, 480), (1920, 480)])
def test_lit_widgets_and_the_look_at_panel_sizes(size):
    theme = theme_of(lit_widgets() + [FRAME], look={"grid": {}, "light": {}}, size=size)
    a, b = Renderer(theme), Renderer(theme)
    frame, _ = a.render(history(), 0.8)
    assert frame.size == size and a.warnings == []
    assert changed_region(frame, b.render(history(), 0.8)[0]) is None


@pytest.mark.parametrize(
    ("size", "thickness", "sweep", "halo"),
    [(278, 15, (-90, 270), 9.0), (115, 6, (-90, 270), 6.0), (182, 10, (135, 405), 6.0)],
)
def test_a_rings_light_is_its_whole_sweeps_up_to_the_tip_pixel_for_pixel(
    size, thickness, sweep, halo
):
    """A gliding ring's light was made whole for every value: on the 12.3" (Arena's) ten
    milliseconds a frame, so its pictures came late and it moved in jerks. It is the light
    of the whole sweep up to the tip, made anew only around the tip (and the start of a
    closed ring): as the light made whole, at every value and in every colour."""
    widget = {"w": size, "h": size, "thickness": thickness, "start_angle": sweep[0],
              "end_angle": sweep[1]}  # fmt: skip
    stroke, room = max(2, round(thickness * lit.RING_CORE)), math.ceil(3 * halo)
    for colour in ((19, 229, 215), (255, 120, 40)):
        for frac in (0.001, 0.02, 0.3, 0.5, 0.9, 0.996, 1.0):
            whole = lit.light(lit._arc(widget, frac, stroke, room), np.asarray(colour, np.float32),
                              halo).astype(np.float32)  # fmt: skip
            made = np.asarray(lit._ring_light(widget, frac, colour, stroke, room, halo), np.float32)
            shown = [a[..., :3] * a[..., 3:4] / 255 for a in (whole, made)]
            assert np.abs(shown[0] - shown[1]).max() <= 1.5, (frac, colour)


@pytest.mark.parametrize(
    ("sweep", "ticks", "paint"),
    [((-90, 270), 0, "#13E5D7"), ((135, 405), 0, "#FF784080"), ((-90, 270), 12, "#A3AEFF")],
)
def test_a_rings_kept_lit_sweep_gives_the_pixels_of_its_light_made_anew(sweep, ticks, paint):
    """A gliding ring's groove with its whole sweep lit on it is kept by colour, and each
    value only cuts it at the tip and lights the tip on the groove anew: the pixels of the
    arc's light made and laid on the groove whole, at every value, on a closed ring and an
    open one, with ticks, in a colour that lets the groove show through too."""
    ring = {**lit_widgets()[0], "w": 174, "h": 174, "thickness": 11, "start_angle": sweep[0],
            "end_angle": sweep[1], "ticks": ticks, "color": paint}  # fmt: skip
    theme = theme_of([ring])
    kept, plain = Renderer(theme), Renderer(theme)
    plain.keep_static = False
    widget = kept.widgets[0]
    colours = kept._paint(widget, None)
    for frac in (0.0, 0.001, 0.02, 0.3, 0.5, 0.9, 0.996, 1.0):
        a = lit.gauge_piece(kept, widget, frac, colours)
        b = lit.gauge_piece(plain, widget, frac, colours)
        assert (a.x, a.y, a.box) == (b.x, b.y, b.box), frac
        assert a.layer.tobytes() == b.layer.tobytes(), frac

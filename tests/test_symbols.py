"""Weather symbols: which weather draws which symbol, what moves, and how they are kept."""

from datetime import datetime

import numpy as np
import pytest
from PIL import Image, ImageChops

from libre_panel.render import finish, light3d, symbols
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import THEME_FORMAT, parse_theme
from libre_panel.weather.open_meteo import _WMO

NOON = datetime(2026, 10, 11, 12, 0)
MIDNIGHT = datetime(2026, 10, 11, 0, 30)
COLOURS = {
    "cloud": (200, 212, 232, 255), "moon": (200, 212, 232, 255), "dark": (90, 98, 110, 255),
    "sun": (251, 191, 36, 255), "bolt": (251, 191, 36, 255), "rain": (34, 211, 238, 255),
    "snow": (241, 245, 249, 255),
}  # fmt: skip


def snapshot(code=3, now=NOON, **more):
    readings = {"weather.code": Reading("weather.code", code)}
    readings.update({k: Reading(k, v) for k, v in more.items()})
    return Snapshot(readings=readings, now=now)


def theme(*widgets, size=(300, 100)):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#0a1119"},
            "palette": {"warn": "#fbbf24", "accent": "#22d3ee", "text": "#f1f5f9"},
            "widgets": list(widgets),
        }
    )


def symbol(**kw):
    return {"type": "symbol", "id": "s", "x": 10, "y": 10, "size": 64, "color": "#c8d4e8", **kw}


def picture(widget, snap=None, now=100.0, animate=True):
    renderer = Renderer(theme(widget), animate=animate)
    image, _ = renderer.render(snap or snapshot(), now)
    return renderer, image


def same(a, b):
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


# -- which weather is which symbol -------------------------------------------------------------


def test_every_weather_code_open_meteo_sends_has_a_symbol():
    assert {symbols.kind_of(code) for code in _WMO} <= set(symbols.KINDS)
    expected = {
        0: "clear", 1: "mostly", 2: "partly", 3: "overcast", 45: "fog", 48: "fog",
        51: "drizzle", 57: "drizzle", 61: "rain", 67: "rain", 71: "snow", 77: "snow",
        80: "rain", 82: "rain", 85: "snow", 86: "snow", 95: "storm", 99: "storm",
    }  # fmt: skip
    for code, kind in expected.items():
        assert symbols.kind_of(code) == kind, code
    for unknown in (None, "x", 1234, 4.0):
        assert symbols.kind_of(unknown) == "overcast"
    assert symbols.kind_of(61.0) == "rain"  # readings are floats


@pytest.mark.parametrize("night", [False, True])
def test_each_kind_has_the_layers_that_move(night):
    def layers(kind):
        return symbols.layers_for(kind, night, 64, "flat", COLOURS)[0]

    clear = layers("clear")
    assert clear.disc is not None and (clear.rays is None) == night
    assert len(clear.stars) == (3 if night else 0)
    for kind in ("mostly", "partly"):
        sky = layers(kind)
        assert sky.disc is not None and sky.cloud is not None and (sky.rays is None) == night
        assert len(sky.stars) == (2 if night else 0)
    assert layers("overcast").back is not None and layers("overcast").cloud is not None
    assert len(layers("fog").bars) == 4 and layers("fog").cloud is None
    assert layers("drizzle").drop is not None and layers("rain").drop is not None
    assert layers("snow").flake is not None and layers("snow").drop is None
    storm = layers("storm")
    assert storm.bolt is not None and storm.back is not None and storm.cloud is not None


def test_a_set_is_made_once_for_every_symbol_that_looks_like_it():
    symbols.forget()
    one, style = symbols.layers_for("rain", False, 64, "flat", COLOURS)
    two, _ = symbols.layers_for("rain", False, 64, "flat", dict(COLOURS))
    assert one is two and style == "flat"
    other, _ = symbols.layers_for("rain", False, 64, "flat", {**COLOURS, "rain": (255, 0, 0, 255)})
    assert other is not one  # another colour is another set
    third, _ = symbols.layers_for("rain", False, 48, "flat", COLOURS)
    assert third is not one  # and another size


def test_kept_sets_stay_within_their_budget(monkeypatch):
    sizes = (64, 66, 68, 70, 72, 74)
    symbols.forget()
    largest = max(symbols.layers_for("rain", False, k, "flat", COLOURS)[0].size for k in sizes)
    symbols.forget()
    monkeypatch.setattr(symbols, "BUDGET", round(largest * 2.5))
    for k in sizes:
        symbols.layers_for("rain", False, k, "flat", COLOURS)
    kept = list(symbols._sets.values())
    assert 1 <= len(kept) < len(sizes)  # the least recent went
    assert sum(layers.size for layers in kept) <= symbols.BUDGET
    assert kept[-1].k == 74  # the newest stays


# -- how they move -------------------------------------------------------------------------------


def test_a_symbol_moves_with_the_time_of_the_frame():
    renderer = Renderer(theme(symbol(sensor="weather.code")), animate=True)
    snap = snapshot(63)
    frames = [renderer.render(snap, 100.0 + n * 0.2)[0].tobytes() for n in range(8)]
    assert len(set(frames)) >= 6 and renderer.moving


def test_the_same_time_is_the_same_picture_and_is_made_once_a_step():
    renderer = Renderer(theme(symbol()), animate=True)
    snap = snapshot(63)
    first = renderer.render(snap, 10.0)[0].tobytes()
    piece = renderer._cache["s"][1]
    assert renderer.render(snap, 10.0)[0].tobytes() == first
    renderer.render(snap, 10.01)  # in the same step of 1/25 s: the kept picture
    assert renderer._cache["s"][1] is piece
    renderer.render(snap, 10.05)  # the next step
    assert renderer._cache["s"][1] is not piece


@pytest.mark.parametrize("code", [0, 2, 3, 45, 51, 63, 73, 95])
def test_a_symbol_that_does_not_move_stands_the_same_at_any_time(code):
    renderer = Renderer(theme(symbol(moving=False)), animate=True)
    frames = {renderer.render(snapshot(code), 100.0 + n * 0.37)[0].tobytes() for n in range(6)}
    assert len(frames) == 1 and not renderer.moving


def test_in_a_preview_and_where_the_panel_gets_one_picture_a_second_it_stands():
    renderer = Renderer(theme(symbol()), animate=False, preview=True)
    frames = {renderer.render(snapshot(63), 100.0 + n * 0.37)[0].tobytes() for n in range(6)}
    assert len(frames) == 1 and not renderer.moving


def test_symbols_with_another_offset_are_not_in_step():
    t = theme(symbol(id="a", x=0), symbol(id="b", x=100, offset=1.7), size=(200, 100))
    renderer = Renderer(t, animate=True)
    image, _ = renderer.render(snapshot(63), 50.0)
    a, b = image.crop((0, 0, 100, 100)), image.crop((100, 0, 200, 100))
    assert not same(a, b)
    again = Renderer(
        theme(symbol(id="a", x=0), symbol(id="b", x=100), size=(200, 100)), animate=True
    )
    image, _ = again.render(snapshot(63), 50.0)
    assert same(image.crop((0, 0, 100, 100)), image.crop((100, 0, 200, 100)))  # no offset: in step


def test_the_rays_turn_and_the_disc_stays():
    renderer = Renderer(theme(symbol(size=128)), animate=True)
    snap = snapshot(0, **{"weather.is_day": 1})
    a = renderer.render(snap, 100.0)[0].crop((10, 10, 138, 138))
    b = renderer.render(snap, 100.6)[0].crop((10, 10, 138, 138))
    assert not same(a, b)
    middle = (a.width // 2 - 20, a.height // 2 - 20, a.width // 2 + 20, a.height // 2 + 20)
    assert same(a.crop(middle), b.crop(middle))  # the disc does not turn


def test_rain_falls_and_the_bolt_flashes_now_and_then():
    k = 96
    layers = symbols.layers_for("rain", False, k, "flat", COLOURS)[0]

    def drops(t):
        a = np.asarray(symbols.compose(layers, "rain", False, t), np.float32)
        low = a[int(0.74 * k) :]  # below the cloud: only the rain
        weight = low[..., 3]
        rows = np.arange(low.shape[0])[:, None]
        return (weight * rows).sum() / max(weight.sum(), 1.0)

    assert drops(0.05) != drops(0.2)
    storm = symbols.layers_for("storm", False, k, "flat", COLOURS)[0]

    def bolt(t):  # below the clouds only the bolt is
        a = np.asarray(symbols.compose(storm, "storm", False, t), np.float32)
        return a[int(0.75 * k) :, :, 3].sum()

    assert bolt(0.05) > 2 * bolt(1.0)  # a flash, then dim
    assert bolt(0.18) > 2 * bolt(1.0)  # and a second one a moment later
    assert bolt(None) == pytest.approx(bolt(0.05), rel=0.01)  # standing: lit


def test_stars_twinkle_and_fog_sways():
    for kind, night in (("clear", True), ("fog", False)):
        layers = symbols.layers_for(kind, night, 96, "flat", COLOURS)[0]
        images = [symbols.compose(layers, kind, night, t).tobytes() for t in (0.0, 0.7, 1.9)]
        assert len(set(images)) == 3, kind


def test_a_small_symbol_goes_quicker_than_a_big_one():
    small = symbols.layers_for("overcast", False, 64, "flat", COLOURS)[0]
    big = symbols.layers_for("overcast", False, 160, "flat", COLOURS)[0]

    def drift(layers, t):
        a = np.asarray(symbols.compose(layers, "overcast", False, t), np.float32)[..., 3]
        columns = a.sum(axis=0)
        return (columns * np.arange(len(columns))).sum() / columns.sum() / layers.k

    moved_small = abs(drift(small, 3.0) - drift(small, 0.0))
    moved_big = abs(drift(big, 3.0) - drift(big, 0.0))
    assert moved_small > moved_big


# -- what the weather is --------------------------------------------------------------------------


def test_night_and_day():
    def warmth(image):
        a = np.asarray(image.convert("RGB"), np.float32)
        return float((a[..., 0] - a[..., 2]).mean())

    day = picture(symbol(), snapshot(0, **{"weather.is_day": 1}))[1]
    night = picture(symbol(), snapshot(0, **{"weather.is_day": 0}))[1]
    assert warmth(day) > warmth(night) + 3  # a warm sun by day, a cool moon by night
    # the forecast says it for its hours; without a word the clock does
    hour = symbol(sensor="weather.hour.3.code")
    snap = Snapshot(
        readings={
            "weather.hour.3.code": Reading("weather.hour.3.code", 0),
            "weather.hour.3.day": Reading("weather.hour.3.day", 0),
        },
        now=NOON,
    )
    assert same(picture(hour, snap)[1], night)
    clock_day = picture(symbol(), Snapshot(readings={"weather.code": Reading("weather.code", 0)},
                                           now=NOON))[1]  # fmt: skip
    clock_night = picture(symbol(), Snapshot(readings={"weather.code": Reading("weather.code", 0)},
                                             now=MIDNIGHT))[1]  # fmt: skip
    assert same(clock_day, day) and same(clock_night, night)


def test_without_a_reading_it_is_a_cloud_and_can_hide():
    shown = picture(symbol(), Snapshot(now=NOON))[1]
    assert shown.getbbox() is not None and not same(shown, Image.new("RGB", shown.size, "#0a1119"))
    renderer = Renderer(theme(symbol(hide_if_missing=True)), animate=True)
    assert renderer.render(Snapshot(now=NOON), 1.0)[1] == {}
    assert "s" in renderer.render(snapshot(2), 1.1)[1]


# -- lit, flat and in a theme ----------------------------------------------------------------


def test_the_lit_finish_draws_the_symbols_from_blenders_bodies():
    flat = picture(symbol(size=96), snapshot(3))[1]
    t = theme(symbol(size=96, finish="lit"))
    assert t.warnings == [] and finish.supports(t.widgets[0])
    lit = Renderer(t, animate=True).render(snapshot(3), 100.0)[0]
    assert not same(flat, lit)

    # lit bodies are shaded: more different colours than the flat cloud
    def colours(image):
        return len(image.convert("RGB").getcolors(1 << 20) or [])

    assert colours(lit) > colours(flat)


def test_without_the_light_layers_a_lit_symbol_is_drawn_flat(monkeypatch):
    symbols.forget()
    monkeypatch.setattr(light3d.WEATHER_BODIES, "get", lambda: None)
    layers, style = symbols.layers_for("rain", False, 64, "lit", COLOURS)
    assert style == "flat" and layers.drop is not None
    t = theme(symbol(finish="lit"))
    image, _ = Renderer(t, animate=True).render(snapshot(63), 100.0)
    assert image.getbbox() is not None


def test_a_finish_the_symbol_does_not_have_is_said_and_drawn_flat():
    t = theme(symbol(finish="facet"))
    assert any("facet" in w and "symbol" in w for w in t.warnings)
    image, _ = Renderer(t, animate=True).render(snapshot(3), 100.0)
    assert same(image, picture(symbol(), snapshot(3))[1])


def test_warming_up_makes_every_kind_of_weather(monkeypatch):
    symbols.forget()
    renderer = Renderer(theme(symbol(size=48, finish="lit")), animate=True)
    renderer.warm(snapshot(0), 1.0)
    kept = {key[:3] for key in symbols._sets if key[3] == "lit"}
    assert {(kind, False, 48) for kind in symbols.KINDS} <= kept
    assert {("clear", True, 48), ("partly", True, 48)} <= kept


def test_the_spec_and_the_theme_loader():
    t = theme(symbol())
    widget = t.widgets[0]
    assert (widget["sensor"], widget["size"], widget["moving"], widget["offset"]) == (
        "weather.code",
        64,
        True,
        0.0,
    )
    assert widget["finish"] == "flat"
    assert [w for w in t.warnings if "symbol" in w] == []


def test_a_symbol_stays_square_when_the_theme_is_adapted():
    data = theme(symbol(x=100, y=20, size=60), size=(480, 320)).to_dict()
    out = adapt_theme(data, "custom", "landscape", "scale", width=1920, height=480)
    big = next(w for w in out["widgets"] if w["type"] == "symbol")
    assert big["size"] == 90  # the smaller scale (480 / 320 = 1.5), not 4 x
    cx = (100 + 30) * 4
    assert abs(big["x"] + big["size"] / 2 - cx) <= 1

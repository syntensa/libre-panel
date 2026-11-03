"""The earth as it is now (render/earth.py): the sun where it stands, a globe lit by it that
faces your place, one that turns a step a pixel with frames only for its steps (the panel rests
between), lit in a thread of its own; the earth module beside its lines, as a card, and Terra,
the screen around it."""

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from libre_panel import app as app_module
from libre_panel.config import Config, SensorsConfig, card_choice
from libre_panel.render import earth
from libre_panel.render import renderer as R
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.sensors.sky import sky_readings
from libre_panel.theme.model import find_theme, load_theme, parse_theme
from libre_panel.theme.modules import CARD_NAMES, MODULE_KINDS, build_module

BERLIN = (52.52, 13.40)
SOLSTICE_NOON = datetime(2026, 6, 21, 12, 0, tzinfo=UTC).timestamp()  # (noon at Greenwich)


def theme_of(*widgets, width=800, height=420):
    return parse_theme({"format": "libre-panel-theme/1", "name": "e",
                        "display": {"model": "custom", "width": width, "height": height},
                        "background": {"color": "#05080c"}, "widgets": list(widgets)})  # fmt: skip


def snap_at(t):
    snapshot = demo_snapshot(fixed_time=t)
    snapshot.readings.update(sky_readings(t, *BERLIN))
    return snapshot


def test_the_sun_stands_over_the_tropic_at_midsummer_noon_and_over_the_far_side_at_midnight():
    lat, lon = earth.subsolar(SOLSTICE_NOON)
    assert lat == pytest.approx(23.44, abs=0.1)
    assert lon == pytest.approx(0.0, abs=1.0)  # (the equation of time: a minute or two)
    lat, lon = earth.subsolar(SOLSTICE_NOON - 12 * 3600)
    assert abs(abs(lon) - 180) < 1.5
    lat, _ = earth.subsolar(datetime(2026, 3, 20, 14, 0, tzinfo=UTC).timestamp())
    assert abs(lat) < 0.5  # (the equinox)


def test_a_globe_is_drawn_again_when_its_day_has_moved_a_pixel_at_its_rim():
    assert earth.redraw_s(640) < earth.redraw_s(160)  # a large one sooner
    r = 640 * earth.SHARE / 2
    assert earth.redraw_s(640) * 2 * math.pi / earth.DAY_S * r == pytest.approx(1.0)
    assert 20 <= earth.redraw_s(4000) and earth.redraw_s(8) <= 600


def test_the_maps_are_read_no_larger_than_the_globe_needs():
    small, large = earth.maps(400), earth.maps(4000)
    assert small["day"].shape == (256, 512, 3) and small["clouds"].shape == (256, 512)
    assert large["day"].shape == (1024, 2048, 3) and large["night"].shape == (1024, 2048, 3)
    assert large["water"].shape == (1024, 2048)
    assert large["water"].max() == 255 and large["water"].min() == 0


def brightness(image, lat, lon, centre, size):
    """The globe's brightness at (lat, lon) as seen from above ``centre``."""
    right, up, toward = earth._basis(*centre)
    p = earth._point(lat, lon)
    r = size * earth.SHARE / 2
    x = round(size / 2 + float(np.dot(p, right)) * r)
    y = round(size / 2 - float(np.dot(p, up)) * r)
    return float(np.asarray(image.convert("RGB"), np.float32)[y - 3 : y + 4, x - 3 : x + 4].mean())


def test_a_standing_globe_is_lit_where_the_sun_stands_and_dark_on_the_far_side():
    centre = (0.0, 45.0)  # the noon side at 0, the night at 90 east
    image = earth.picture(300, SOLSTICE_NOON, centre, None)
    assert image.size == (300, 300) and image.mode == "RGBA"
    corners = np.asarray(image)[[0, 0, -1, -1], [0, -1, 0, -1], 3]
    assert corners.max() == 0  # (a disc, and its air, on nothing)
    assert brightness(image, 10, 5, centre, 300) > 3 * brightness(image, 10, 85, centre, 300)


def test_the_cities_light_the_night_side_unless_asked_otherwise():
    centre = (48.0, 10.0)
    night = SOLSTICE_NOON - 12 * 3600 + 86400 * 180  # (December, midnight in Europe)
    lit = earth.picture(240, night, centre, None)
    dark = earth.picture(240, night, centre, None, lights=False)

    def warm(image):  # the bright, warm pixels: the lights
        rgb = np.asarray(image.convert("RGB"), np.int32)
        return int(((rgb[..., 0] > 110) & (rgb[..., 0] > rgb[..., 2] + 40)).sum())

    assert warm(lit) > 50 and warm(dark) == 0


def test_your_place_is_marked_in_its_colour_where_it_faces_us():
    mark = (255, 0, 255)
    image = earth.picture(300, SOLSTICE_NOON, BERLIN, BERLIN, mark=mark)
    pixels = np.asarray(image.convert("RGB")).reshape(-1, 3).astype(int)
    assert (np.abs(pixels - mark).sum(axis=1) < 30).sum() > 4  # the dot, in the middle
    behind = earth.picture(300, SOLSTICE_NOON, (-52.52, 193.4), BERLIN, mark=mark)
    pixels = np.asarray(behind.convert("RGB")).reshape(-1, 3).astype(int)
    assert (np.abs(pixels - mark).sum(axis=1) < 30).sum() == 0  # on the far side: none


def test_a_turning_globe_steps_a_pixel_at_its_middle_and_its_ground_goes_east():
    globe = earth.Turning(200, 25.0)
    assert globe.width == 2 * math.ceil(math.pi * 200 * earth.SHARE / 2)
    for lon in (-170.0, -45.5, 0.0, 13.4, 120.0):
        assert globe.longitude(globe.step_at(lon)) == pytest.approx(lon, abs=360 / globe.width)
    # the view goes west as the steps go on: the ground moves from left to right
    a, b = globe.longitude(100), globe.longitude(101)
    assert (a - b) % 360 == pytest.approx(360 / globe.width)
    step, due = globe.step_of(1000.0, 60.0)
    rate = globe.width / 60.0
    assert step == math.floor(1000.0 * rate) % globe.width
    assert due == pytest.approx((math.floor(1000.0 * rate) + 1) / rate) and due > 1000.0
    assert globe.step_of(1000.0, 60.0, 5)[0] == (step + 5) % globe.width


def test_a_turning_globe_shows_what_a_standing_one_shows_from_there():
    globe = earth.Turning(240, 20.0, mark=(255, 0, 255))
    globe.light(SOLSTICE_NOON)
    step = globe.step_at(30.0)
    turned = np.asarray(globe.frame(step, None).convert("RGB"), np.float32)
    stood = np.asarray(earth.picture(240, SOLSTICE_NOON, (20.0, globe.longitude(step)), None)
                       .convert("RGB"), np.float32)  # fmt: skip
    assert np.abs(turned - stood).mean() < 8  # (no glint on its sea, and a pixel a sample)
    with pytest.raises(RuntimeError):
        earth.Turning(64, 0.0).frame(0, None)  # (lit first)


def reference_air(size, view, sun, color):
    """The air over a globe as it was worked out whole each time (before what stays of it a
    size was kept): render/earth.py's _air gives these pixels, bit for bit."""
    right, up, toward = view
    r = size * earth.SHARE / 2
    idx = earth._air_pixels(size)
    ys, xs = np.divmod(idx, size)
    x = (xs.astype(np.float32) + (0.5 - size / 2)) * (1 / r)
    y = ((size / 2 - 0.5) - ys.astype(np.float32)) * (1 / r)
    rho = np.sqrt(x * x + y * y)
    z = np.sqrt(np.clip(1 - rho * rho, 0, 1))
    sx, sy, sz = (float(np.dot(axis, sun)) for axis in (right, up, toward))
    inner = rho < 1
    mu = np.where(inner, x * sx + y * sy + z * sz, (x * sx + y * sy) / np.maximum(rho, 1e-6))
    fres = np.where(inner, (1 - z) ** 2.5, 0.0)
    width = 0.05
    halo = np.exp(-np.clip(rho - 1, 0, None) * (1 / (width * 0.30)))
    halo *= np.clip((1 + width * 2.2 - rho) * (1 / (width * 0.6)), 0, 1)
    edge = np.clip((1.0 - rho) * r + 0.5, 0, 1)
    alpha = 0.75 * fres * earth._smooth(-0.35, 0.55, mu) * edge
    alpha += np.clip(halo * earth._smooth(-0.30, 0.45, mu) * 0.85, 0, 1) * (1 - edge)
    out = np.zeros((size * size, 4), np.uint8)
    out[idx, :3] = np.minimum(255, np.array(color, np.float32) * 1.13).astype(np.uint8)
    out[idx, 3] = np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8)
    return out.reshape(size, size, 4)


@pytest.mark.parametrize("size", [64, 101, 333, 600])
def test_the_air_worked_out_from_what_stays_of_it_a_size_is_the_same(size):
    """A turning globe lays its air anew every two degrees (8 ms on Terra's 12.3" globe): only
    the light on each pixel is worked out for the view and the sun, where the pixels lie and
    what they weigh once a size (3 ms). The same pixels, bit for bit, and each air a picture
    of its own."""
    rng = np.random.default_rng(size)
    made = []
    for _ in range(6):
        view = earth._basis(rng.uniform(-60, 60), rng.uniform(-180, 180))
        sun = rng.normal(size=3)
        sun /= np.linalg.norm(sun)
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        air = earth._air(size, view, sun, color)
        want = reference_air(size, view, sun, color)
        assert np.array_equal(np.asarray(air), want)
        made.append((air, want))
    assert all(np.array_equal(np.asarray(air), want) for air, want in made)  # (none shared)
    assert earth._air_geometry(size) is earth._air_geometry(size)


# -- in the renderer ----------------------------------------------------------------------


class Instant:
    """The helper thread, run when asked (``held``: not until :meth:`run`)."""

    def __init__(self, name="render-builder", gate=None):
        self.jobs = []

    def submit(self, job):
        from concurrent.futures import Future

        future = Future()
        self.jobs.append((future, job))
        return future

    def run(self):
        for future, job in self.jobs:
            future.set_result(job())
        self.jobs = []

    def close(self):
        pass


def test_a_turning_globe_has_a_frame_for_each_step_and_says_when_the_next_is_due(monkeypatch):
    theme = theme_of({"type": "earth", "id": "globe", "x": 10, "y": 10, "size": 200, "spin": 60})
    renderer = R.Renderer(theme, animate=True)
    renderer.place = BERLIN
    snapshot = snap_at(SOLSTICE_NOON)
    try:
        renderer.render(snapshot, 1000.0)
        held = renderer._earths["globe"]
        globe = held.turning
        assert renderer.due is not None and not renderer.moving
        # it begins with your place in the middle
        assert globe.longitude(held.step) == pytest.approx(BERLIN[1], abs=360 / globe.width)
        first = held.piece
        renderer.render(snapshot, 1000.0 + 0.2 * (renderer.due - 1000.0))
        assert held.piece is first  # (no step yet: the same piece)
        due = renderer.due
        renderer.render(snapshot, due + 1e-6)
        assert held.piece is not first and renderer.due > due
    finally:
        renderer.close()


def test_a_standing_globe_follows_the_wall_clock_and_is_lit_aside_once_it_shows(monkeypatch):
    builders = []
    monkeypatch.setattr(R, "_Builder", lambda *a, **k: builders.append(Instant()) or builders[-1])
    theme = theme_of({"type": "earth", "id": "globe", "x": 10, "y": 10, "size": 160})
    renderer = R.Renderer(theme, animate=True)
    renderer.background_builds = True
    renderer.place = BERLIN
    try:
        renderer.render(snap_at(SOLSTICE_NOON), 1000.0)
        first = renderer._earths["globe"].piece
        assert first is not None and renderer.due is None and not renderer.moving
        renderer.render(snap_at(SOLSTICE_NOON), 5000.0)  # (the frame clock is not the day's)
        assert renderer._earths["globe"].piece is first and not builders
        later = SOLSTICE_NOON + 3 * earth.redraw_s(160)
        renderer.render(snap_at(later), 5001.0)
        assert renderer._earths["globe"].piece is first  # being lit aside: the one before stays
        (builder,) = builders
        builder.run()
        renderer.render(snap_at(later), 5002.0)
        assert renderer._earths["globe"].piece is not first
    finally:
        renderer.close()


def test_a_globe_where_the_panel_does_not_move_stands():
    theme = theme_of({"type": "earth", "id": "globe", "x": 10, "y": 10, "size": 120, "spin": 60})
    renderer = R.Renderer(theme, animate=False)
    try:
        renderer.render(snap_at(SOLSTICE_NOON), 1000.0)
        assert renderer._earths["globe"].turning is None and renderer.due is None
    finally:
        renderer.close()


def test_a_panel_rests_until_the_globes_next_step(isolated_home, monkeypatch):
    import json

    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    folder = isolated_home / "themes" / "globe"
    folder.mkdir(parents=True)
    data = {"format": "libre-panel-theme/1", "name": "globe",
            "display": {"model": "custom", "width": 320, "height": 240},
            "background": {"color": "#05080c"},
            "widgets": [{"type": "earth", "id": "globe", "x": 40, "y": 0, "size": 240,
                         "spin": 600}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")

    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

    class Stream:
        streaming, stream_fps = True, 30

        def open(self):
            pass

        def set_brightness(self, percent):
            pass

        def describe(self):
            return "memory"

        def show(self, frame, region=None):
            pass

        def close(self):
            pass

    clock = Clock()
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = Config(theme="globe", sensors=SensorsConfig(providers=["demo"]))
    loop = app_module.MainLoop(config, hub=hub, display=Stream(), clock=clock)
    try:
        for n in range(60):
            clock.now = 1000.0 + n / 30
            loop.step(clock.now)
        loop.renderer.moving = False
        rest = loop.rest()
        assert rest is not None and rest <= loop.renderer.due - clock.now + 1e-9
    finally:
        loop.close()


# -- the module, the card and the screen -----------------------------------------------------


def module_parts(cols, rows, box, moving=True):
    from libre_panel.theme import modules as M

    t = parse_theme({"format": "libre-panel-theme/1", "name": "m",
                     "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
                     "palette": {"accent": "#5FC8FF"},
                     "widgets": [{"type": "module", "id": "m0", "module": "earth", "cols": cols,
                                  "rows": rows, "moving": moving}]})  # fmt: skip
    style = {**M.STYLE_DEFAULTS, **(t.style or {}), M.FRAME_U: min(t.width, t.height) / 2}
    return build_module(t.widgets[0], box, M.colors_of(t.palette), style)


def test_the_earth_module_has_its_globe_beside_its_lines_and_alone_without_a_place():
    assert "earth" in MODULE_KINDS and "earth" in CARD_NAMES
    parts = module_parts(20, 10, [0, 0, 800, 400])
    globes = [w for w in parts if w["type"] == "earth"]
    assert [w["needs"] for w in globes] == ["sun.up", "!sun.up"]  # with a place, and without
    beside, alone = globes
    assert beside["x"] < 100 and beside["spin"] > 0 and beside["color2"] == "#5FC8FF"
    assert alone["x"] + alone["size"] / 2 == pytest.approx(400, abs=2)  # alone: in the middle
    words = {w["id"]: w for w in parts if w["type"] in ("text", "metric")}
    assert words["day"]["needs"].startswith("sun.up=1") and words["rise"]["sensor"] == "sun.rise"
    assert all(w["x"] > beside["x"] + beside["size"] for w in words.values())
    still = [
        w for w in module_parts(20, 10, [0, 0, 800, 400], moving=False) if w["type"] == "earth"
    ]
    assert all(w["spin"] == 0 for w in still)
    tall = [w for w in module_parts(6, 20, [0, 0, 300, 700]) if w["type"] == "earth"][0]
    assert tall["size"] <= 300  # (narrow and tall: the lines under it)


def test_the_earth_is_a_card_of_the_slots():
    assert card_choice(["earth", "forecast for 5m"], "cards")[0] == "earth"


@pytest.mark.parametrize("name", ["terra", "terra-1920x720", "terra-vertical", "terra-480x480"])
def test_terra_draws_the_earth_with_and_without_a_place(name):
    theme = load_theme(find_theme(name))
    assert theme.warnings == []
    for place in (BERLIN, None):
        renderer = R.Renderer(theme, animate=False)
        renderer.place = place
        snapshot = snap_at(SOLSTICE_NOON) if place else demo_snapshot(fixed_time=SOLSTICE_NOON)
        try:
            renderer.render(snapshot, 1000.0)
            assert renderer.warnings == [] and renderer._earths
        finally:
            renderer.close()

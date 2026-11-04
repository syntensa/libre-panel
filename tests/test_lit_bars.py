"""A lit bar made by the engine (render/scene.py's Bar, engine/src/bar.rs): its pill of every
length from its parts, its picture and glow made here only when asked for and then as before,
sent once and each value as its lit spans; the engine's frames as the renderer's, its glow to
the bit and its light to a level."""

import struct
import subprocess
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageChops

from libre_panel.devices import engine as engines
from libre_panel.devices import h264
from libre_panel.render import lit
from libre_panel.render import scene as scenes
from libre_panel.render.renderer import Renderer
from libre_panel.theme.model import parse_theme

LUMEN = {"type": "bar", "id": "b", "x": 47, "y": 597, "w": 494, "h": 7, "sensor": "cpu.load",
         "finish": "lit", "color": "#1C716E", "color2": "#2EE6D6", "background": "#18212C",
         "glow": 0.18, "glow_radius": 7}  # fmt: skip
BARS = [  # (what, the widget's changes from Lumen's progress bar on the 12.3", the panel)
    ("Lumen's progress", {}, (1920, 720)),
    ("one colour", {"w": 580, "h": 10, "color": "#B35C54", "color2": None, "glow": 0.5,
                    "glow_radius": 10}, (1920, 720)),
    ("from the far end", {"direction": "left", "glow": 1.0, "glow_radius": 13}, (1920, 720)),
    ("standing", {"x": 900, "y": 200, "w": 10, "h": 300, "direction": "up", "glow": 0.3,
                  "glow_radius": 9, "glow_area": [870, 180, 80, 340]}, (1920, 720)),
    ("standing, segments", {"x": 1200, "y": 150, "w": 12, "h": 260, "direction": "down",
                            "segments": 8, "segment_gap": 4, "color2": None, "glow": 0.25,
                            "glow_radius": 6}, (1920, 720)),
    ("faded", {"color": "#2EE6D680", "color2": None, "glow": 0.4}, (1920, 720)),
    ("no glow", {"glow": 0.0}, (1920, 720)),
    ("thick, at the edge", {"x": 0, "y": 690, "w": 600, "h": 24, "glow": 0.6,
                            "glow_radius": 11}, (1920, 720)),
    ("segments, small", {"x": 312, "y": 185, "w": 140, "h": 8, "segments": 14, "segment_gap": 3,
                         "color": "#34D399", "color2": None, "background": "#ffffff12",
                         "glow": 0.4, "glow_radius": 5}, (480, 320)),
    ("thin, small", {"x": 360, "y": 49, "w": 107, "h": 3, "color": "#20797A", "glow": 0.15,
                     "glow_radius": 3}, (480, 320)),
]  # fmt: skip
FRACS = (0.0, 0.0005, 0.03, 0.2, 0.5, 0.77, 0.95, 0.9995, 1.0)


def bar(change, size):
    """A renderer of a theme of that one lit bar, its widget as drawn, and its paint."""
    theme = parse_theme({"format": "libre-panel-theme/1",
                         "display": {"width": size[0], "height": size[1]},
                         "background": {"color": "#000000"},
                         "widgets": [{**LUMEN, **change}]})  # fmt: skip
    renderer = Renderer(theme, animate=True)
    widget = theme.widgets[0]
    return renderer, widget, renderer._rule_paint(widget, 50.0, 0.0)


@pytest.mark.parametrize("core", [3, 4, 5, 6, 7, 9, 12, 15])
@pytest.mark.parametrize("pad", [5, 9, 14, 27])
def test_a_pill_of_any_length_is_a_reference_s_ends_with_its_middle_between(core, pad):
    """What bar.rs makes a long pill of (lit.py's _bar_base): its first and last columns, the
    column between them as often as its length asks: _pill's pixels."""
    side = pad + core + lit.PILL_REACH
    reference = lit._levels(lit._pill(2 * (side - pad) + 1, core, pad))
    left, middle, right = reference[:, :side], reference[:, side : side + 1], reference[:, -side:]
    for span in range(2 * (side - pad), 2 * (side - pad) + 40):
        between = span + 2 * pad - 2 * side
        made = np.concatenate([left, np.repeat(middle, between, 1), right], 1)
        assert (made == lit._levels(lit._pill(span, core, pad))).all(), span
    # (the pills are their levels over 255, to the bit: those are what go)
    pill = lit._pill(9, core, pad)
    assert (lit._levels(pill).astype(np.float32) / 255 == pill).all()


@pytest.mark.parametrize(("what", "change", "size"), BARS, ids=[b[0] for b in BARS])
def test_a_bar_the_engine_makes_is_made_here_only_when_asked_for_and_then_as_before(
    what, change, size
):
    renderer, widget, paint = bar(change, size)
    for frac in (0.0, 0.03, 0.6, 1.0):
        native = lit.bar_piece(renderer, widget, frac, paint, native=True)
        plain = lit.bar_piece(renderer, widget, frac, paint, native=False)
        assert isinstance(native.native, scenes.Bar) and plain.native is None
        assert not native.layer.composed
        assert (native.x, native.y, native.box) == (plain.x, plain.y, plain.box)
        assert native.layer.tobytes() == plain.layer.tobytes()  # (made here: the same pixels)
        light = native.light
        if plain.light is None:  # (a glow of nothing: black, which adds nothing)
            assert light is None or light.layer.getbbox() is None, frac
        else:
            assert (light.x, light.y) == (plain.light.x, plain.light.y)
            assert light.layer.tobytes() == plain.light.layer.tobytes()
        assert (native.light is None) == (not native.native.spans or not widget["glow"])
    renderer.native_bars = True
    try:
        # drawn otherwise than as it is (turned, faded): its pixels, made here
        for other in ({"rotate": 10.0}, {"opacity": 0.5}, {"shadow": "#000000"}):
            assert lit.bar_piece(renderer, {**widget, **other}, 0.5, paint).native is None
    finally:
        renderer.native_bars = False


@pytest.mark.parametrize(
    "other",
    [{"direction": "left"}, {"w": 520}, {"segments": 9}, {"x": 60}],
    ids=["from the far end", "longer", "segments", "elsewhere"],
)
def test_bars_alike_but_for_how_their_gradient_runs_each_get_their_own_light(other):
    """The light kept of a span is a gradient's along all of its bar (once a bar filling from
    its far end got the light of a bar like it filling from its start)."""
    renderer, widget, paint = bar({}, (1920, 720))
    for frac in (1.0, 0.5):
        lit.bar_piece(renderer, widget, frac, paint, native=False)  # (its light kept)
        drawn = lit.bar_piece(renderer, {**widget, **other}, frac, paint, native=False)
        lit._PICTURES.clear()
        fresh = lit.bar_piece(renderer, {**widget, **other}, frac, paint, native=False)
        assert drawn.layer.tobytes() == fresh.layer.tobytes(), frac


def test_a_bar_goes_to_the_engine_once_and_each_value_as_its_lit_spans():
    renderer, widget, paint = bar({}, (1920, 720))
    pieces = [lit.bar_piece(renderer, widget, f, paint, native=True) for f in (0.2, 0.3)]
    sender = engines.SceneSender(bars=True)
    first = engines_heads(sender.records(scene_of(pieces[0]), 0, None))
    assert [k for k, *_ in first] == [engines.GROUND, engines.BAR, engines.BAR_LAYER,
                                      engines.SCENE]  # fmt: skip
    base = pieces[0].native.base
    shape = struct.unpack("<IIIIiIIIIIIiiiiII", first[1][3][:68])
    length, thick, pad, core = base.shape[:4]
    assert shape[:4] == (length, thick, pad, core) and shape[10] == lit._glow_scale(7.0)
    side, high = base.left.shape[1], core + 2 * pad
    pills = high * (2 * side + 1) + sum(high * (s + 2 * pad) for s in range(1, len(base.short) + 1))
    assert first[1][2] == 224 + (length + 2 * pad) * (thick + 2 * pad) * 4 + pills
    number, x, y, glow, *ends, gradient, n = struct.unpack("<IiiI4B4BII", first[2][3][:32])
    assert (number, x, y, gradient, n) == (first[1][1], pieces[0].x, pieces[0].y, 1, 1)
    assert glow != 0 and tuple(ends) == (*paint[0], *paint[1])
    assert struct.unpack("<dd", first[2][3][32:]) == pieces[0].native.spans[0]
    order = struct.unpack("<2I", first[3][3][12:])
    assert order == (first[2][1], glow)  # (the bar, then its glow)
    second = engines_heads(sender.records(scene_of(pieces[1]), 1, None))
    assert [k for k, *_ in second] == [engines.BAR_LAYER, engines.SCENE]  # (the bar it has)
    assert struct.unpack("<I", second[0][3][:4])[0] == first[1][1]
    # a piece whose picture is not the bar's any more (faded) goes as its pixels
    faded = replace(pieces[1], layer=pieces[1].layer.copy())
    third = engines_heads(sender.records(scene_of(faded), 2, None))
    assert [k for k, *_ in third] == [engines.LAYER, engines.LAYER, engines.SCENE]
    # an engine that makes no bars gets every bar as its pixels, made in its writer
    plain = engines_heads(engines.SceneSender().records(scene_of(pieces[0]), 0, None))
    assert [k for k, *_ in plain] == [engines.GROUND, engines.LAYER, engines.LAYER,
                                      engines.SCENE]  # fmt: skip
    assert pieces[0].layer.composed and pieces[0].light.layer.composed


def scene_of(*pieces, size=(1920, 720), ground=(40, 44, 52)):
    return scenes.Scene(size, ground, tuple((f"w{n}", p) for n, p in enumerate(pieces)))


def engines_heads(parts):
    """The records in ``parts``: (kind, a, b, what follows the head)."""
    data, out = b"".join(parts), []
    while data:
        assert data[:4] == b"LPE1"
        kind, a, b = data[4], *struct.unpack("<II", data[8:16])
        length = 12 + 4 * b if kind == engines.SCENE else b  # (the others: b bytes follow)
        out.append((kind, a, b, data[16 : 16 + length]))
        data = data[16 + length :]
    return out


@pytest.fixture
def bar_engine():
    found = h264.find_engine()
    if not found or not engines.makes_bars(found):
        pytest.skip("needs libre-panel-engine that makes bars (cargo build --release)")
    return found


def engine_frames(engine, tmp_path, size, scenes_):
    """The frames the engine composes of ``scenes_``, as the stream sends them (RGB each)."""
    out = tmp_path / "bars.rgb"
    proc = subprocess.Popen(
        [engine, "encode", "--size", f"{size[0]}x{size[1]}", "--turn", "0", "--fps", "25",
         "--crf", "20", "--kbit", "4000", "--keyint", "250", "--dump-rgb", str(out)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )  # fmt: skip
    sender = engines.SceneSender(bars=True)
    try:
        for n, scene in enumerate(scenes_):
            for part in sender.records(scene, n, None):
                proc.stdin.write(part)
    finally:
        proc.stdin.close()
        proc.wait(60)
    made, whole = out.read_bytes(), size[0] * size[1] * 3
    assert len(made) == whole * len(scenes_)
    return [made[k * whole : (k + 1) * whole] for k in range(len(scenes_))]


def runs(faded):
    """Each bar of :data:`BARS` at each of :data:`FRACS`: its scene as the engine gets it and
    the frame the renderer composes of it, grouped by panel; ``faded``: its light see-through
    (alpha 0: the groove and its glow alone)."""
    out = {}
    for _what, change, size in BARS:
        if faded:
            change = {**change, "color": (change.get("color") or LUMEN["color"])[:7] + "00"}
        renderer, widget, paint = bar(change, size)
        for frac in FRACS:
            native = lit.bar_piece(renderer, widget, frac, paint, native=True)
            plain = lit.bar_piece(renderer, widget, frac, paint, native=False)
            assert native.native is not None
            own = scenes.compose(scene_of(plain, size=size)).tobytes()
            out.setdefault(size, []).append((scene_of(native, size=size), own, plain))
    return out


def test_the_engine_makes_a_lit_bar_s_glow_as_the_renderer_does_to_the_bit(bar_engine, tmp_path):
    """Its glow worked out as Pillow works it out (reduce, GaussianBlur, resize, point), in its
    fixed point: the same pixels (its light see-through, the groove alone under it)."""
    for size, frames in runs(faded=True).items():
        made = engine_frames(bar_engine, tmp_path, size, [s for s, _own, _plain in frames])
        for got, (_scene, own, _plain) in zip(made, frames, strict=True):
            assert got == own, size


def test_the_engine_makes_a_lit_bar_as_the_renderer_does_to_a_level(bar_engine, tmp_path):
    """Its light worked out in floating point as there (ring.rs's blur): the same picture to a
    level, at few pixels."""
    for size, frames in runs(faded=False).items():
        made = engine_frames(bar_engine, tmp_path, size, [s for s, _own, _plain in frames])
        for got, (_scene, own, plain) in zip(made, frames, strict=True):
            diff = ImageChops.difference(Image.frombytes("RGB", size, got),
                                         Image.frombytes("RGB", size, own))  # fmt: skip
            assert max(high for _, high in diff.getextrema()) <= 1  # (to a level)
            changed = sum(diff.convert("L").point(lambda v: v > 0 and 255).histogram()[1:])
            assert changed <= 0.002 * plain.layer.width * plain.layer.height

"""Facets: the distance transform, the bevel of letters, bars, rings and curves, their caches."""

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from libre_panel.fonts import builtin_font_path
from libre_panel.render import facets
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import THEME_FORMAT, ThemeError, parse_theme

WHITE = (238, 246, 255, 255)


def font(size=60, name="Barlow-SemiBold"):
    return ImageFont.truetype(str(builtin_font_path("builtin:" + name)), size)


def brute_edt(inside):
    """The distance to the nearest pixel outside, by looking at every one of them."""
    h, w = inside.shape
    ys, xs = np.nonzero(~inside)
    out = np.zeros((h, w))
    for y in range(h):
        for x in range(w):
            if inside[y, x]:
                out[y, x] = np.sqrt(((ys - y) ** 2 + (xs - x) ** 2).min())
    return out


# -- the distance transform and the filters ----------------------------------------------------


@pytest.mark.parametrize("seed", range(12))
def test_the_distance_transform_is_exact(seed):
    rng = np.random.default_rng(seed)
    h, w = rng.integers(3, 28, 2)
    mask = rng.random((h, w)) < rng.uniform(0.3, 0.97)
    mask[rng.integers(0, h), rng.integers(0, w)] = False  # there is always an outside
    assert np.abs(facets.edt(mask) - brute_edt(mask)).max() < 1e-5


def test_the_distance_transform_on_shapes():
    yy, xx = np.mgrid[:41, :41]
    disc = np.hypot(yy - 20, xx - 20) <= 15
    assert np.abs(facets.edt(disc) - brute_edt(disc)).max() < 1e-5
    ring = disc & (np.hypot(yy - 20, xx - 20) >= 6)  # a hole inside
    assert np.abs(facets.edt(ring) - brute_edt(ring)).max() < 1e-5
    bar = np.zeros((9, 60), bool)
    bar[2:7, 1:59] = True  # a long bar touching nothing: the narrow way is the distance
    assert facets.edt(bar).max() == 3.0
    stem = np.ones((70, 5), bool)  # touching the border does not make it background
    stem[:, 0] = stem[:, -1] = False
    assert facets.edt(stem).max() == 2.0


def test_the_distance_transform_edge_cases():
    assert facets.edt(np.zeros((4, 5), bool)).max() == 0
    assert (facets.edt(np.ones((4, 5), bool)) == 9).all()  # no outside: h + w
    one = np.zeros((5, 5), bool)
    one[2, 2] = True
    assert facets.edt(one)[2, 2] == 1.0
    assert facets.edt(np.zeros((0, 3), bool)).shape == (0, 3)


@pytest.mark.parametrize("size", [1, 3, 7, 13])
def test_the_maximum_filter_is_the_maximum_of_the_window(size):
    rng = np.random.default_rng(size)
    a = rng.random((13, 17)).astype(np.float32)
    padded = np.pad(a, size // 2, mode="edge")
    want = np.array(
        [[padded[y : y + size, x : x + size].max() for x in range(17)] for y in range(13)]
    )
    assert np.array_equal(facets.max_filter(a, size), want)


def test_sobel_is_a_smoothed_central_difference():
    ramp = np.tile(np.arange(8, dtype=np.float32), (6, 1))  # rises by 1 to the right
    gx, gy = facets.sobel(ramp)
    assert np.allclose(gx[:, 1:-1], 8.0) and np.allclose(gy[:, 1:-1], 0.0)
    gx, gy = facets.sobel(ramp.T)
    assert np.allclose(gy[1:-1], 8.0) and np.allclose(gx[1:-1], 0.0)


# -- the bevel ---------------------------------------------------------------------------------


def test_the_bevel_tilts_away_from_the_edge_and_leaves_the_top_flat():
    mask = np.zeros((40, 60), np.float32)
    mask[8:32, 8:52] = 1
    n = facets.bevel_normals(mask, 3.0, stroke=1.0)
    assert np.allclose(np.linalg.norm(n, axis=-1), 1, atol=1e-5)
    assert np.allclose(n[20, 30], (0, 0, 1))  # the middle is flat
    assert np.allclose(n[0, 0], (0, 0, 1))  # outside: nothing
    s = facets.SIN45
    # each side's bevel faces outwards, tilted 45 degrees
    assert np.allclose(n[20, 9], (-s, 0, s), atol=0.02)  # left
    assert np.allclose(n[20, 50], (s, 0, s), atol=0.02)  # right
    assert np.allclose(n[9, 30], (0, -s, s), atol=0.02)  # top
    assert np.allclose(n[30, 30], (0, s, s), atol=0.02)  # bottom
    assert (n[..., 2] < 0.999).sum() > 0.1 * mask.sum() * 0.2


def test_the_bevel_is_held_to_a_share_of_the_stroke():
    """A fixed width would eat a thin stroke whole; a thick one gets more, but never all."""

    def across(thickness, length):
        mask = np.zeros((thickness + 40, length + 40), np.float32)
        mask[20 : 20 + thickness, 20 : 20 + length] = 1
        n = facets.bevel_normals(mask, 24.0)  # asked for 24 px, at any thickness
        return n, (n[:, mask.shape[1] // 2, 2] < 0.999).sum()

    thin, thin_deep = across(20, 200)
    fat, fat_deep = across(160, 400)
    assert (
        0 < thin_deep <= 2 < fat_deep < 20
    )  # a pixel from each side of a thin one, a few of a fat
    assert thin[30, 120, 2] == 1.0 and fat[100, 220, 2] == 1.0  # the middle stays flat


def test_no_bevel_for_small_text_or_an_empty_mask():
    assert facets.bevel_width(12) == 0 and facets.bevel_width(19.9) == 0
    assert facets.bevel_width(20) == pytest.approx(1.2)
    assert facets.bevel_width(1000) == 5.0
    flat = facets.bevel_normals(np.ones((6, 6), np.float32), 0.0)
    assert (flat[..., 2] == 1).all() and (flat[..., :2] == 0).all()
    assert (facets.bevel_normals(np.zeros((6, 6), np.float32), 3.0)[..., 2] == 1).all()


def test_the_flat_top_is_the_exact_colour_and_the_bevel_follows_the_light():
    a, b = facets.shade(np.array([1.0]), facets.TEXT)
    assert a[0] == 1 and b[0] == 0  # facing the viewer: unchanged
    toward, away = (
        facets.facing(np.array([[-facets.SIN45, -facets.SIN45, facets.SIN45]]))[0],
        facets.facing(np.array([[facets.SIN45, facets.SIN45, facets.SIN45]]))[0],
    )
    assert toward > 1 > away >= 0
    lit_a, lit_b = facets.shade(np.array([toward]), facets.SOLID)
    dark_a, dark_b = facets.shade(np.array([away]), facets.SOLID)
    colour = np.array([100.0])
    assert (colour * lit_a + lit_b)[0] > 100 > (colour * dark_a + dark_b)[0]
    assert facets.toward_light(np.array([[0.0, 0.0, 1.0]]))[0] == 0
    assert facets.toward_light(np.array([[-facets.SIN45, -facets.SIN45, facets.SIN45]]))[0] > 0.9
    assert facets.toward_light(np.array([[facets.SIN45, facets.SIN45, facets.SIN45]]))[0] < -0.9


def test_the_tints_come_from_the_palette():
    cool, warm = facets.tints_for({"accent": "#ff0000", "warn": "#00ff00"})
    assert cool[0] > cool[1] and warm == (0, 255, 0)
    assert facets.tints_for(None) == facets.tints_for({})
    cool, warm = facets.tints_for({"accent": "nonsense"})  # a palette entry that is no colour
    assert len(cool) == len(warm) == 3


# -- lettering ---------------------------------------------------------------------------------


def test_a_letters_outline_is_the_flat_texts_and_its_top_the_exact_colour():
    f = font(60)
    placed = facets.glyph(f, "8", WHITE, 0.0)
    assert placed is not None
    patch, dx, dy = placed
    flat = Image.new("L", (patch.width + 8, patch.height + 8), 0)
    ImageDraw.Draw(flat).text((-dx, -dy), "8", font=f, fill=255, anchor="la")
    covered = np.asarray(patch.getchannel("A"), np.int16)
    drawn = np.asarray(flat, np.int16)[: patch.height, : patch.width]
    assert np.abs(covered - drawn).max() == 0  # the same pixels, silhouette and edge
    rgb = np.asarray(patch, np.int16)[..., :3]
    on_top = covered == 255
    flat_top = (np.abs(rgb - np.array(WHITE[:3])).max(-1) <= 1) & on_top
    assert flat_top.sum() > 0.5 * on_top.sum()
    assert not flat_top.all()  # and there is a bevel


def test_the_bevel_is_cooler_towards_the_light_and_warmer_away():
    patch, _, _ = facets.glyph(font(120, "Barlow-Bold"), "I", (200, 200, 200, 255), 0.0)
    a = np.asarray(patch, np.float32)
    inked = a[..., 3] > 0
    cool = inked & (a[..., 2] > a[..., 0] + 2)  # bluer than the grey it is made of
    warm = inked & (a[..., 0] > a[..., 2] + 2)
    assert cool.sum() > 20 and warm.sum() > 20
    middle = np.nonzero(inked.any(axis=0))[0][[0, -1]].mean()
    assert np.nonzero(cool)[1].mean() < middle < np.nonzero(warm)[1].mean()  # cool left, warm right


def test_the_same_text_twice_builds_each_glyph_once():
    f = font(44)
    facets.builds.update(glyph=0, patch=0)
    first = facets.facet_text("1337", f, WHITE)
    built = dict(facets.builds)
    assert built["glyph"] >= 3 and built["patch"] == built["glyph"]  # 1, 3, 7 (a 3 per quarter)
    again = facets.facet_text("1337", f, WHITE)
    assert facets.builds == built  # nothing built the second time
    assert first.tobytes() == again.tobytes()
    facets.facet_text("1337", f, (255, 128, 0, 255))  # another colour: the geometry stays
    assert facets.builds["glyph"] == built["glyph"] and facets.builds["patch"] == 2 * built["patch"]
    facets.facet_text("1337", font(45), WHITE)  # another size: new geometry
    assert facets.builds["glyph"] == 2 * built["glyph"]


def test_a_quarter_pixel_position_is_a_glyph_of_its_own():
    f = font(50)
    facets.builds.update(glyph=0, patch=0)
    for frac in (0.0, 0.1, 0.2, 0.26, 0.49, 0.5, 0.74, 0.76, 0.9):
        facets.glyph(f, "A", WHITE, frac)
    assert facets.builds["glyph"] == 4  # quarters: 0, 1/4, 1/2, 3/4 (and 0.9 is the next pixel)
    patch, dx, dy = facets.glyph(f, "A", WHITE, 0.9)
    assert dx == facets.glyph(f, "A", WHITE, 0.0)[1] + 1  # a pixel further right


def test_a_blank_has_no_glyph_and_small_text_is_left_flat():
    f = font(40)
    assert facets.glyph(f, " ", WHITE) is None
    assert facets.usable(f) and not facets.usable(font(19)) and facets.usable(font(20))
    assert not facets.usable(ImageFont.load_default())  # a bitmap font has no outline
    assert facets.facet_text("tiny", font(12), WHITE) is None


def test_text_with_spacing_and_the_edge_of_the_layer():
    f = font(40)
    tight = facets.facet_text("AVA", f, WHITE)
    spaced = facets.facet_text("AVA", f, WHITE, tracking=8)
    assert spaced.width == tight.width + 16
    assert facets.facet_text("", f, WHITE).size[0] >= 1
    layer = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    patch = Image.new("RGBA", (6, 6), (255, 0, 0, 255))
    facets.put(layer, patch, -3, -2)  # hanging off the top left: cut, not an error
    assert layer.getpixel((0, 0)) == (255, 0, 0, 255) and layer.getpixel((3, 4)) == (0, 0, 0, 0)
    facets.put(layer, patch, -20, 0)  # not on it at all
    facets.put(layer, patch, 8, 8)  # hanging off the other side: composited, cut by Pillow


def test_lru_keeps_the_newest():
    cache = facets.Lru(3)
    for n in range(5):
        cache.put(n, n)
    assert len(cache) == 3 and cache.get(0) is None and cache.get(4) == 4
    cache.get(2)
    cache.put(5, 5)  # 3 was the least recently used
    assert cache.get(3) is None and cache.get(2) == 2
    cache.clear()
    assert len(cache) == 0


# -- in a theme --------------------------------------------------------------------------------


def theme(*widgets, size=(480, 160), palette=None):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#0a1119"},
            "palette": palette or {},
            "widgets": list(widgets),
        }
    )


def test_finish_is_a_field_of_the_shapes_that_have_one():
    from libre_panel.theme.model import WIDGET_SPECS

    for kind in ("text", "metric", "clock", "weather", "countdown", "bar", "gauge", "graph"):
        assert WIDGET_SPECS[kind]["finish"] == ("enum:flat|facet", "flat")
    assert "finish" not in WIDGET_SPECS["rect"]
    assert theme({"type": "text", "id": "t"}).widgets[0]["finish"] == "flat"
    with pytest.raises(ThemeError, match="finish"):
        theme({"type": "text", "id": "t", "finish": "shiny"})


def text_widget(**kw):
    return {"type": "text", "id": "t", "x": 10, "y": 10, "text": "SPUR 4059", "font_size": 50,
            "font": "builtin:Barlow-SemiBold", "color": "#eef6ff", **kw}  # fmt: skip


def test_facet_text_in_a_theme_differs_from_flat_but_keeps_its_box_and_outline():
    plain = Renderer(theme(text_widget()))
    faceted = Renderer(theme(text_widget(finish="facet")))
    a, boxes_a = plain.render(Snapshot())
    b, boxes_b = faceted.render(Snapshot())
    assert plain.warnings == faceted.warnings == []
    assert np.asarray(a).tobytes() != np.asarray(b).tobytes()
    ink_a = np.asarray(a, np.int16).max(-1) > 60
    ink_b = np.asarray(b, np.int16).max(-1) > 60
    assert abs(int(ink_a.sum()) - int(ink_b.sum())) < 0.05 * ink_a.sum()  # the same letters
    assert boxes_b["t"][0] == boxes_a["t"][0] and set(boxes_b) == set(boxes_a)


def test_facet_text_below_twenty_pixels_is_the_flat_text():
    for size in (12, 19):
        a, _ = Renderer(theme(text_widget(font_size=size))).render(Snapshot())
        b, _ = Renderer(theme(text_widget(font_size=size, finish="facet"))).render(Snapshot())
        assert a.tobytes() == b.tobytes()


def test_a_changing_number_costs_no_new_glyphs_once_all_digits_were_seen():
    metric = {"type": "metric", "id": "m", "x": 10, "y": 10, "sensor": "v", "font_size": 60,
              "format": "{value:.0f}", "finish": "facet", "color": "#eef6ff"}  # fmt: skip
    renderer = Renderer(theme(metric))
    for value in (1234567890, 9876543210, 1122334455, 6677889900):  # every digit, in places
        renderer.render(Snapshot(readings={"v": Reading("v", value)}))
    facets.builds.update(glyph=0, patch=0)
    for value in (1234567890, 9876543210, 6677889900):
        renderer.render(Snapshot(readings={"v": Reading("v", value)}))
    assert facets.builds == {"glyph": 0, "patch": 0}


def test_facet_text_with_a_palette_takes_its_tints_from_it():
    widget = text_widget(finish="facet", font_size=90, font="builtin:Barlow-Bold", text="I")
    a, _ = Renderer(theme(widget, palette={"accent": "#ff0000", "warn": "#00ff00"})).render(
        Snapshot()
    )
    b, _ = Renderer(theme(widget, palette={"accent": "#0000ff", "warn": "#ff00ff"})).render(
        Snapshot()
    )
    assert a.tobytes() != b.tobytes()


@pytest.mark.parametrize("size", [(1920, 480), (320, 240)])
def test_facet_text_at_both_panel_sizes(size):
    widgets = [
        text_widget(finish="facet", font_size=size[1] // 4, x=size[0] // 30, y=4),
        {"type": "clock", "id": "c", "x": size[0] // 2, "y": size[1] // 2, "format": "%H:%M:%S",
         "font_size": size[1] // 5, "finish": "facet", "align": "center"},
    ]  # fmt: skip
    renderer = Renderer(theme(*widgets, size=size))
    image, boxes = renderer.render(demo_snapshot(fixed_time=1_700_000_000))
    assert image.size == size and renderer.warnings == [] and set(boxes) == {"t", "c"}

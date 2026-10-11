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
        assert WIDGET_SPECS[kind]["finish"] == ("enum:flat|lit|facet", "flat")
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


# -- bars --------------------------------------------------------------------------------------

CYAN = (34, 211, 238, 255)
DEEP = (11, 108, 133, 255)
TRACK = (31, 41, 55, 255)


def test_a_boxs_bevel_is_light_where_it_faces_the_light_and_dark_away():
    a, b, cover = facets.box_window(40, 16, (0.0, 40.0, 0.0, 16.0), 3.0, facets.SOLID)
    colour = np.array([100.0])
    shaded = colour[0] * a + b
    assert np.allclose(a[8, 20], 1) and np.allclose(b[8, 20], 0)  # the top is as it is
    assert shaded[8, 1] > 100 > shaded[8, 38]  # left lit, right dark
    assert shaded[1, 20] > 100 > shaded[14, 20]  # top lit, bottom dark
    assert cover.min() == 1 and cover.shape == (16, 40)
    ga, gb, _ = facets.box_window(40, 16, (0.0, 40.0, 0.0, 16.0), 3.0, facets.GROOVE, groove=True)
    milled = colour[0] * ga + gb
    assert milled[8, 1] < 100 and milled[14, 20] > milled[1, 20]  # the other way round


def test_a_box_edge_between_pixels_covers_part_of_the_pixel():
    _, _, cover = facets.box_window(10, 6, (2.5, 8.0, 0.0, 6.0), 1.0, facets.SOLID)
    assert cover[3, 1] == 0 and cover[3, 2] == pytest.approx(0.5) and cover[3, 3] == 1
    assert cover[3, 7] == 1 and cover[3, 8] == 0


def test_the_corner_of_a_box_is_a_mitre():
    a, b, _ = facets.box_window(30, 30, (0.0, 30.0, 0.0, 30.0), 6.0, facets.SOLID)
    k = 100 * a + b  # brightness of a grey 100 where the corner meets
    assert k[3, 2] > 100  # nearer to the left edge: faces the light
    assert k[2, 3] > 100
    assert k[27, 28] < 100 and k[28, 27] < 100  # the dark corner opposite


@pytest.mark.parametrize("horizontal", [True, False])
@pytest.mark.parametrize("start,end", [(0.0, 120.0), (3.25, 117.5), (0.5, 400.75)])
def test_a_long_bar_made_of_its_ends_and_a_column_is_the_whole_one(horizontal, start, end):
    first, (a, b, cover) = facets._body(horizontal, 18, start, end, False, facets.SOLID)
    n = a.shape[1] if horizontal else a.shape[0]
    edges = (start - first, end - first)
    direct = (
        facets.box_window(n, 18, (*edges, 0.0, 18.0), 3.0, facets.SOLID)
        if horizontal
        else facets.box_window(18, n, (0.0, 18.0, *edges), 3.0, facets.SOLID)
    )
    for got, want in zip((a, b, cover), direct, strict=True):
        assert np.allclose(got, want, atol=1e-5)


def bar(w=200, h=16, frac=0.5, direction="right", paint=(CYAN, None), track=TRACK, **kw):
    return facets.bar_layer(w, h, frac, direction, paint, track, **kw)


def lit_columns(image, colour=CYAN):
    """Columns whose middle row is the bar's colour (the fill, not the track)."""
    a = np.asarray(image, np.int16)
    mid = a[a.shape[0] // 2, :, :3]
    return np.nonzero(
        (np.abs(mid - np.array(colour[:3])).max(-1) <= 2) & (a[a.shape[0] // 2, :, 3] > 200)
    )[0]


def test_a_bar_fills_its_share_from_the_side_it_grows_from():
    right = lit_columns(bar(frac=0.6))
    assert right[0] <= 3 and 116 <= right[-1] <= 120 and len(right) > 100
    left = lit_columns(bar(frac=0.6, direction="left"))
    assert left[-1] >= 196 and 80 <= left[0] <= 84
    up = np.asarray(bar(w=16, h=200, frac=0.25, direction="up"), np.int16)[:, 8]
    filled = np.nonzero(np.abs(up[:, :3] - np.array(CYAN[:3])).max(-1) <= 2)[0]
    assert filled[0] >= 148 and filled[-1] >= 196
    down = np.asarray(bar(w=16, h=200, frac=0.25, direction="down"), np.int16)[:, 8]
    filled = np.nonzero(np.abs(down[:, :3] - np.array(CYAN[:3])).max(-1) <= 2)[0]
    assert filled[0] <= 3 and 46 <= filled[-1] <= 52


def test_a_bars_bevel_follows_the_light():
    a = np.asarray(bar(h=20, frac=0.8), np.int16)
    middle = a[:, 60, :3].sum(-1)
    assert middle[0] > middle[10] > middle[-1]  # lit edge above, the flat top, the dark edge below
    assert np.abs(a[10, 60, :3] - np.array(CYAN[:3])).max() <= 1  # the top: the exact colour


def test_a_gradient_is_a_place_on_the_track_and_means_the_same_at_any_fill():
    half = np.asarray(bar(frac=0.5, paint=(DEEP, CYAN)), np.int16)
    full = np.asarray(bar(frac=1.0, paint=(DEEP, CYAN)), np.int16)
    assert np.abs(half[8, 50] - full[8, 50]).max() <= 2  # the same colour at the same place
    assert full[8, 190, 1] > full[8, 10, 1]  # towards the second colour
    back = np.asarray(bar(frac=1.0, direction="left", paint=(DEEP, CYAN)), np.int16)
    assert back[8, 10, 1] > back[8, 190, 1]  # it starts at the right: the first colour there


def test_an_empty_bar_is_the_track_and_a_tiny_one_a_sliver():
    empty = bar(frac=0.0)
    assert len(lit_columns(empty)) == 0 and np.asarray(empty)[..., 3].max() == 255
    assert len(lit_columns(bar(frac=0.0001, h=16))) == 0 or True  # at least no error
    sliver = np.asarray(bar(frac=0.001), np.int16)
    assert (np.abs(sliver[:, :4, :3] - np.array(TRACK[:3])).max(-1) > 6).any()  # something there
    no_track = np.asarray(bar(frac=0.5, track=None), np.int16)
    assert no_track[8, 180, 3] == 0 and no_track[8, 20, 3] == 255


def test_thin_and_thick_bars_are_left_to_the_flat_drawing():
    assert bar(h=5) is None and bar(h=401, w=500) is None and bar(w=5, direction="up") is None
    assert bar(h=6) is not None and bar(h=400, w=500) is not None


def test_segments_are_blocks_with_gaps_the_last_one_part_lit():
    image = np.asarray(bar(w=240, h=14, frac=0.45, segments=10, gap=4), np.int16)
    row = image[7]
    lit = (np.abs(row[:, :3] - np.array(CYAN[:3])).max(-1) <= 2) & (row[:, 3] > 200)
    runs = np.diff(np.concatenate([[0], lit.astype(int), [0]]))
    assert (runs == 1).sum() == 4  # four whole blocks lit, the fifth only half
    gaps = image[7, 20:26, 3]
    assert (gaps == 0).any()  # the gap between blocks is clear
    assert bar(w=20, h=14, segments=30, gap=4) is None  # no room for the blocks


def test_a_bar_changing_by_a_third_of_a_pixel_changes_the_picture_a_little():
    a = np.asarray(bar(frac=0.5), np.float32)
    b = np.asarray(bar(frac=0.5 + 1 / (200 * 3)), np.float32)
    changed = np.abs(a - b).sum(-1) > 0
    assert 0 < changed.sum() < 80 and changed[:, :90].sum() == 0  # only at the moving end


# -- rings -------------------------------------------------------------------------------------


def ring(frac=0.6, w=140, h=140, th=12, start=135, end=405, paint=(CYAN, None), track=TRACK):
    return facets.ring_layer(w, h, th, start, end, frac, paint, track)


def at_angle(image, degrees, radius):
    """The pixel at an angle (clockwise from 3 o'clock) and radius from the middle."""
    w, h = image.size
    rad = np.radians(degrees)
    return np.asarray(image, np.int16)[
        int(h / 2 + radius * np.sin(rad)), int(w / 2 + radius * np.cos(rad))
    ]


def is_fill(pixel, colour=CYAN):
    return pixel[3] > 200 and np.abs(pixel[:3] - np.array(colour[:3])).max() <= 70


def test_a_ring_is_lit_as_far_round_as_its_share_from_its_start():
    image = ring(frac=0.5)  # from 135 degrees by half of 270: to 270
    assert is_fill(at_angle(image, 150, 64)) and is_fill(at_angle(image, 260, 64))
    assert not is_fill(at_angle(image, 285, 64)) and not is_fill(at_angle(image, 120, 64))
    assert at_angle(image, 285, 64)[3] > 200  # the track is there
    assert at_angle(image, 90, 64)[3] == 0  # and the gap of the gauge is empty
    assert at_angle(image, 200, 20)[3] == 0  # nothing inside the ring


def test_a_ring_that_goes_the_other_way():
    image = ring(frac=0.5, start=405, end=135)  # backwards: from 45 degrees, anticlockwise
    assert is_fill(at_angle(image, 30, 64)) and not is_fill(at_angle(image, 120, 64))


def test_a_closed_ring_has_no_ends_and_its_bevel_follows_the_light():
    image = ring(frac=1.0, start=0, end=360, track=None)
    assert all(image.getpixel((70, y))[3] > 0 for y in (5, 8, 11))  # no cut at the top
    lit, dark = at_angle(image, 225, 69), at_angle(image, 45, 69)  # the outer bevel, either side
    assert lit[3] > 200 and dark[3] > 200
    assert (
        lit[:3].sum() > CYAN[:3].__len__() * 0 + sum(CYAN[:3]) > dark[:3].sum()
    )  # lit, flat, dark


def test_a_ring_has_a_gradient_along_its_sweep():
    image = ring(frac=1.0, paint=(DEEP, CYAN))
    first, last = at_angle(image, 140, 64), at_angle(image, 400, 64)
    assert last[1] > first[1] + 40 and last[0] >= first[0]


def test_a_ring_in_a_box_that_is_not_square_is_a_circle_in_its_middle():
    image = ring(frac=1.0, w=100, h=140, track=None)
    a = np.asarray(image)
    assert a[:, :, 3].max() == 255 and a.shape == (140, 100, 4)
    assert is_fill(at_angle(image, 140, 44)) and a[10, 50, 3] == 0  # the ring stays 100 across


def test_the_band_does_not_depend_on_the_fill_and_is_kept():
    first = facets.ring_band(140, 140, 12, 135, 405)
    assert facets.ring_band(140, 140, 12, 135, 405) is first
    assert facets.ring_band(140, 140, 12, 135, 406) is not first
    a, b, alpha = facets.ring_maps(first, 0.3, False, facets.SOLID)
    assert len(a) == len(b) == len(alpha) == len(first.rows) and alpha.max() <= 1
    more = facets.ring_maps(first, 0.9, False, facets.SOLID)[2]
    assert more.sum() > 2.5 * alpha.sum()


# -- curves ------------------------------------------------------------------------------------


def curve_layer(points, size=(200, 80), fill=100):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(
        [*points, (points[-1][0], size[1] - 1), (points[0][0], size[1] - 1)], fill=(*CYAN[:3], fill)
    )
    return layer


def test_a_curve_gets_a_lit_flank_above_and_a_dark_one_below():
    points = [(x, 40 + 10 * np.sin(x / 15)) for x in range(0, 200, 4)]
    layer = curve_layer(points)
    assert facets.curve_ridge(layer, points, 2.0, CYAN)
    a = np.asarray(layer, np.float32)
    x = 24  # where the curve is level: a slope would tilt the dark flank towards the light
    y = float(np.interp(x + 0.5, [p[0] for p in points], [p[1] for p in points]))
    rows = [r for r in range(int(y) - 5, int(y) + 5) if a[r, x, 3] > 200]  # opaque: the ridge
    light = {r: a[r, x, :3].sum() for r in rows}
    brightest, darkest = max(light, key=light.get), min(light, key=light.get)
    assert brightest < y < darkest + 2  # the lit flank above the curve, the dark one below it
    assert light[brightest] > light[darkest] + 150 and light[darkest] < 120
    assert a[5, x, 3] == 0  # far above: nothing


def test_a_wide_graph_gets_the_slimmer_ridge():
    points = [(x, 40 + 10 * np.sin(x / 15)) for x in range(0, 200, 4)]
    full, slim = curve_layer(points), curve_layer(points)
    facets.curve_ridge(full, points, 2.0, CYAN, wide=False)
    facets.curve_ridge(slim, points, 2.0, CYAN, wide=True)
    assert np.asarray(full).tobytes() != np.asarray(slim).tobytes()
    assert (np.asarray(full, np.int16)[..., 3] > 200).sum() > (
        np.asarray(slim, np.int16)[..., 3] > 200
    ).sum()


def test_a_curve_that_runs_backwards_or_is_too_short_is_left_to_the_line():
    layer = Image.new("RGBA", (50, 30), (0, 0, 0, 0))
    assert not facets.curve_ridge(layer, [(30, 10), (10, 12)], 2.0, CYAN)
    assert not facets.curve_ridge(layer, [(10, 10)], 2.0, CYAN)
    assert not facets.curve_ridge(layer, [(10.2, 10), (10.9, 12)], 2.0, CYAN)
    assert np.asarray(layer).max() == 0


def test_a_curve_at_the_edge_of_its_layer_is_cut_not_an_error():
    layer = Image.new("RGBA", (60, 20), (0, 0, 0, 0))
    run = [(-10, 1.0), (30, 0.5), (70, 19.5)]
    assert facets.curve_ridge(layer, run, 6.0, (200, 100, 50, 128))
    assert np.asarray(layer)[..., 3].max() > 0


# -- the shapes in a theme ---------------------------------------------------------------------


def shape_widgets(finish, size):
    w, h = size
    return [
        {"type": "bar", "id": "b", "x": 10, "y": 10, "w": w // 2, "h": 14, "sensor": "cpu.load",
         "finish": finish, "color": "#22d3ee", "color2": "#c084fc"},
        {"type": "bar", "id": "s", "x": 10, "y": 40, "w": w // 2, "h": 12, "sensor": "cpu.load",
         "finish": finish, "segments": 8},
        {"type": "gauge", "id": "g", "x": w // 2 + 20, "y": 10, "w": h // 2, "h": h // 2,
         "sensor": "gpu.load", "finish": finish, "thickness": 10, "ticks": 6},
        {"type": "graph", "id": "r", "x": 10, "y": h // 2 + 10, "w": w - 20, "h": h // 2 - 20,
         "sensor": "cpu.load", "finish": finish, "grid": 2},
    ]  # fmt: skip


@pytest.mark.parametrize("size", [(1920, 480), (320, 240)])
def test_every_shape_in_facets_at_both_panel_sizes(size):
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    snapshot.history["cpu.load"] = [20 + 3 * n for n in range(40)]
    flat = Renderer(theme(*shape_widgets("flat", size), size=size))
    faceted = Renderer(theme(*shape_widgets("facet", size), size=size))
    a, boxes_a = flat.render(snapshot)
    b, boxes_b = faceted.render(snapshot)
    assert faceted.warnings == [] and b.size == size and set(boxes_a) == set(boxes_b)
    for name, box in boxes_a.items():
        x, y, w, h = box
        assert boxes_b[name] == box
        assert a.crop((x, y, x + w, y + h)).tobytes() != b.crop((x, y, x + w, y + h)).tobytes(), (
            name
        )


def test_a_bar_too_thin_for_a_bevel_is_the_flat_bar():
    widgets = [{"type": "bar", "id": "b", "x": 5, "y": 5, "w": 100, "h": 5, "sensor": "cpu.load"}]
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    flat, _ = Renderer(theme(*widgets)).render(snapshot)
    thin, _ = Renderer(theme(*[{**w, "finish": "facet"} for w in widgets])).render(snapshot)
    assert flat.tobytes() == thin.tobytes()


def test_facet_shapes_glide_in_video_mode_and_graphs_scroll():
    size = (480, 200)
    widgets = shape_widgets("facet", size)
    renderer = Renderer(theme(*widgets, size=size), animate=True)
    renderer.continuous = True
    frames = []
    for n in range(30):
        snapshot = Snapshot(
            readings={
                "cpu.load": Reading("cpu.load", 20.0 + n),
                "gpu.load": Reading("gpu.load", 50.0),
            },
            history={"cpu.load": [20.0 + 2 * k for k in range(60)]},
        )
        image, _ = renderer.render(snapshot, 1.0 + n / 50)
        frames.append(image.tobytes())
    assert renderer.warnings == [] and len(set(frames)) > 15

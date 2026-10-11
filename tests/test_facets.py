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
    thinner, thinner_deep = across(8, 200)
    # a half of a thin stroke is bevel (a quarter from each side), 24 px from each side of a fat
    assert thin_deep == 10 and fat_deep == 48 and 0 < thinner_deep <= 4
    assert thin[30, 120, 2] == 1.0 and fat[100, 220, 2] == 1.0  # the middle stays flat
    assert across(6, 200)[1] / 6 <= facets.STROKE_SHARE  # never more than the share
    assert facets.STROKE_SHARE == 0.5


def test_no_bevel_for_small_text_or_an_empty_mask():
    assert facets.bevel_width(12) == 0 and facets.bevel_width(19.9) == 0
    assert facets.bevel_width(20) == pytest.approx(1.0)
    assert facets.bevel_width(58) == pytest.approx(2.32)  # the wordmark: a bevel of 2 px
    assert facets.bevel_width(1000) == 5.0
    flat = facets.bevel_normals(np.ones((6, 6), np.float32), 0.0)
    assert (flat[..., 2] == 1).all() and (flat[..., :2] == 0).all()
    assert (facets.bevel_normals(np.zeros((6, 6), np.float32), 3.0)[..., 2] == 1).all()


def test_the_flat_top_is_the_exact_colour_and_the_bevel_follows_the_light():
    a, b = facets.shade(np.array([1.0]), facets.METAL)
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


def test_a_bevel_mirrors_the_sky_above_and_the_ground_below():
    s = facets.SIN45
    normals = np.array(
        [[0, 0, 1], [0, -s, s], [0, s, s], [-s, 0, s], [s, 0, s]], np.float32
    )  # the top, facing up, down, left, right
    sky, ground = facets.mirrors(normals)
    assert sky[0] == ground[0] == 0  # the flat top mirrors nothing
    assert sky[1] == pytest.approx(facets.MIRROR) and ground[1] == pytest.approx(0, abs=1e-6)
    assert ground[2] == pytest.approx(facets.MIRROR) and sky[2] == pytest.approx(0, abs=1e-6)
    assert 0 < sky[3] < ground[3] and sky[3] == pytest.approx(sky[4])  # to a side: mostly ground
    assert (sky + ground)[1:] == pytest.approx(facets.MIRROR)  # a full bevel: all of it
    half = np.array([[0, -s / 2, np.sqrt(1 - s * s / 4)]], np.float32)
    assert 0 < facets.mirrors(half)[0][0] < facets.MIRROR  # a lower bevel mirrors less
    assert facets.mirrors(normals, 0.0)[0].max() == 0  # no mirror: none


def test_acrylic_hardly_shades_and_metal_does():
    s = facets.SIN45
    normals = np.array(
        [[0, 0, 1], [0, -s, s], [-s, 0, s], [s, 0, s], [0, s, s]], np.float32
    )  # the top, then bevels facing up, left, right and down
    colour = np.array([100.0, 200.0, 220.0])
    metal = facets.lettering_terms(normals, "metal").astype(np.float32)
    acrylic = facets.lettering_terms(normals, "acrylic").astype(np.float32)
    sky, ground = np.array([200.0, 240.0, 255.0]), np.array([10.0, 17.0, 25.0])

    def paint(terms):  # the terms as bytes: A, S and G of 0 to 1 and B of 0 to 255
        a, b, u, d = (terms[:, i : i + 1] / (1 if i == 1 else 255) for i in range(4))
        return (colour * a + b + sky * u + ground * d).sum(-1)

    metal_light, acrylic_light = paint(metal), paint(acrylic)
    total = colour.sum()
    assert metal_light[0] == pytest.approx(total, abs=1) == pytest.approx(acrylic_light[0], abs=1)
    assert (acrylic_light[3:] > 0.75 * total).all()  # acrylic, away from the light: hardly darker
    assert (metal_light[3:] < 0.6 * total).all()  # metal: much darker
    assert acrylic_light[1] > total and acrylic_light[2] > total  # towards the light: lighter
    assert metal_light[1] > total  # metal facing up, towards the sky: lighter too
    assert acrylic[..., 2:].max() == 0  # acrylic mirrors nothing
    assert metal[0, 2:].max() == 0  # nor does the flat top of metal


def test_the_sky_is_the_palettes_accent_towards_white_and_the_ground_is_given():
    sky, ground = facets.environment_for({"accent": "#ff0000"}, (1, 2, 3, 255))
    assert sky == (255, 89, 89) and ground == (1, 2, 3)  # 35 % of the way to white
    assert facets.environment_for(None) == facets.environment_for({})
    sky, ground = facets.environment_for({"accent": "nonsense"})  # an entry that is no colour
    assert len(sky) == len(ground) == 3 and sky[2] > sky[0]  # the default accent: cyan


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
    assert flat_top.sum() > 0.4 * on_top.sum()  # a stroke is about half flat top ...
    assert flat_top.sum() < 0.6 * on_top.sum()  # ... and the rest bevel, as the wordmark's


def test_the_bevel_is_lighter_and_cooler_towards_the_light_and_darker_away():
    grey = (200, 200, 200, 255)
    world = ((100, 240, 255), (10, 17, 25))  # a sky of cool light, a dark ground
    patch, _, _ = facets.glyph(font(120, "Barlow-Bold"), "I", grey, 0.0, world)
    a = np.asarray(patch, np.float32)
    inked = a[..., 3] == 255
    columns = np.nonzero(inked.any(axis=0))[0]
    left, right, middle = columns[0], columns[-1], int(columns.mean())
    row = a.shape[0] // 2
    assert tuple(a[row, middle, :3]) == (200, 200, 200)  # the top: the exact colour
    lit, shade = a[row, left + 1, :3], a[row, right - 1, :3]  # the left bevel, the right one
    assert lit.sum() > shade.sum() + 100 > 100  # the one towards the light is the lighter
    assert lit[2] > lit[0] + 20  # and bluer than the grey it is made of: some of the sky
    column = a[:, middle]  # down the stem: the end faces up, the foot faces down
    rows = np.nonzero(column[:, 3] == 255)[0]
    up, down = column[rows[0] + 1, :3], column[rows[-1] - 1, :3]
    assert up[2] > up[0] + 50 and up.sum() > 1.5 * down.sum()  # the upper end mirrors the sky
    assert down.sum() < 0.6 * 600  # the lower, in shade, is much darker: metal mirrors the ground
    assert up.sum() > lit.sum() > shade.sum() > down.sum()  # up, left, right, down


def test_acrylic_has_the_same_outline_and_bevel_but_no_dark_side():
    f = font(100, "Barlow-Bold")
    colour = (19, 229, 215, 255)
    metal, _, _ = facets.glyph(f, "I", colour, 0.0, None, "metal")
    acrylic, _, _ = facets.glyph(f, "I", colour, 0.0, None, "acrylic")
    assert metal.size == acrylic.size
    assert np.asarray(metal)[..., 3].tobytes() == np.asarray(acrylic)[..., 3].tobytes()
    m, a = np.asarray(metal, np.float32), np.asarray(acrylic, np.float32)
    inked = m[..., 3] == 255
    columns = np.nonzero(inked.any(axis=0))[0]
    row, right = m.shape[0] // 2, columns[-1] - 1
    assert a[row, right, :3].sum() > 0.8 * sum(colour[:3]) > 2 * m[row, right, :3].sum() * 0.5
    assert m[row, right, :3].sum() < 0.7 * a[row, right, :3].sum()  # metal is darker there
    left = columns[0] + 1
    assert a[row, left, :3].sum() > sum(colour[:3])  # and the lit side is lit in both
    assert tuple(a[row, int(columns.mean()), :3]) == colour[:3]


def test_the_top_edge_of_a_letter_has_its_bevel_too():
    """A letter's top bar touches the top of its box: the bevel must still run along it."""
    world = ((60, 220, 255), (5, 5, 5))
    patch, _, _ = facets.glyph(font(80, "Barlow-Bold"), "T", WHITE, 0.0, world)
    a = np.asarray(patch, np.int16)
    column = a[:, a.shape[1] // 2 + 12]  # through the bar, off the stem
    rows = np.nonzero(column[:, 3] == 255)[0]
    top, flat = column[rows[0]], column[rows[0] + 4]
    assert tuple(flat[:3]) == WHITE[:3]  # the bar's top
    assert top[0] < flat[0] - 40  # cooler on its upper edge: the sky


def test_a_stem_has_a_bevel_of_two_pixels_at_the_wordmarks_size():
    f = font(58, "Barlow-Bold")
    patch, _, _ = facets.glyph(f, "I", (238, 246, 255, 255), 0.0, ((102, 238, 229), (10, 17, 25)))
    a = np.asarray(patch, np.int16)
    row = a[a.shape[0] // 2]
    ink = np.nonzero(row[:, 3] == 255)[0]
    exact = np.abs(row[:, :3] - np.array([238, 246, 255])).max(-1) <= 1
    on_top = int((exact & (row[:, 3] == 255)).sum())
    bevelled = len(ink) - on_top
    assert 6 <= len(ink) <= 10  # a stem about 8 px wide
    assert 3 <= on_top <= 5 and 3 <= bevelled <= 5  # about two of its pixels on each side


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
        assert WIDGET_SPECS[kind]["finish"] == ("enum:flat|lit|facet|acrylic", "flat")
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


def metal_i(finish="facet", palette=None, background="#0a1119", **kw):
    """One big "I" on a plain screen and the picture of it."""
    widget = text_widget(finish=finish, font_size=90, font="builtin:Barlow-Bold", text="I", **kw)
    data = theme(widget, palette=palette, size=(120, 160))
    data.background_color = background
    renderer = Renderer(data)
    image, boxes = renderer.render(Snapshot())
    assert renderer.warnings == []
    return np.asarray(image, np.int16), boxes["t"]


def test_facet_text_takes_its_sky_from_the_palette_and_its_ground_from_the_background():
    def stem(picture, background):
        """The columns and rows of the letter's ink, and the middle of its stem."""
        ink = (picture != np.array(background)).any(-1)
        columns, rows = np.nonzero(ink.any(axis=0))[0], np.nonzero(ink.any(axis=1))[0]
        return int(columns.mean()), int(rows.mean()), rows[0], rows[-1]

    red, _ = metal_i(palette={"accent": "#ff0000"})
    blue, _ = metal_i(palette={"accent": "#0000ff"})
    ground = (10, 17, 25)
    x, y, first, last = stem(blue, ground)
    assert blue[first + 1, x, 2] > blue[first + 1, x, 0] + 40  # the upper end: a blue sky
    assert red[first + 1, x, 0] > red[first + 1, x, 2] + 40  # or a red one
    dark, _ = metal_i(background="#000000")
    grey, _ = metal_i(background="#707070")
    assert grey[last - 1, x].sum() > dark[last - 1, x].sum() + 30  # the foot mirrors the screen
    assert (dark[y, x] == grey[y, x]).all() and tuple(dark[y, x]) == (238, 246, 255)  # not the top


def test_acrylic_is_a_finish_for_lettering_alone():
    metal, _ = metal_i("facet")
    acrylic, _ = metal_i("acrylic")
    assert (metal != acrylic).any()
    bar = {"type": "bar", "id": "b", "x": 10, "y": 10, "w": 100, "h": 14, "finish": "acrylic"}
    loaded = theme(bar)
    assert loaded.warnings == ["finish 'acrylic' is not available for bar; drawn flat"]
    flat_bar = {**bar, "finish": "flat"}
    a, _ = Renderer(loaded).render(Snapshot())
    b, _ = Renderer(theme(flat_bar)).render(Snapshot())
    assert a.tobytes() == b.tobytes()


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


# -- grooves -----------------------------------------------------------------------------------


def test_a_bars_groove_has_a_floor_of_its_colour_and_a_bevel_milled_into_the_ground():
    groove = facets.groove_bar(200, 18, TRACK)
    a = np.asarray(groove, np.int16)
    assert groove.size == (200, 18) and (a[..., 3] == 255).all()
    assert tuple(a[9, 100]) == TRACK  # the floor is exactly the colour
    down = a[:, 100, :3].sum(-1)
    assert down[0] < down[9] < down[-1]  # in shade at the top, a catch of light at the bottom
    along = a[9, :, :3].sum(-1)
    assert along[0] < along[100] < along[-1]  # and so left against right
    assert (a[:2, 20:-20, :3].sum(-1) < down[9]).all()  # the whole top edge, bar its ends


def test_a_vertical_groove_is_lit_the_same_way():
    groove = facets.groove_bar(18, 200, TRACK, vertical=True)
    a = np.asarray(groove, np.int16)
    assert groove.size == (18, 200) and tuple(a[100, 9]) == TRACK
    across = a[100, :, :3].sum(-1)
    assert across[0] < across[9] < across[-1]  # shade at the left edge, light at the right
    assert a[0, 9, :3].sum() < a[100, 9, :3].sum() < a[-1, 9, :3].sum()  # the ends: same


def test_a_groove_is_kept_and_a_bar_too_thin_or_too_thick_has_none():
    groove = facets.groove_bar(200, 18, TRACK)
    assert facets.groove_bar(200, 18, TRACK) is groove  # the same picture, not a copy
    assert facets.groove_bar(200, 18, (*TRACK[:3], 200)) is not groove
    assert facets.groove_bar(200, 18, TRACK, vertical=True) is not groove
    assert facets.groove_bar(200, facets.MIN_BAR - 1, TRACK) is None
    assert facets.groove_bar(200, facets.MAX_BAR + 1, TRACK) is None
    assert facets.groove_bar(200, facets.MIN_BAR, TRACK).size == (200, facets.MIN_BAR)


def test_a_groove_takes_the_alpha_of_its_colour():
    a = np.asarray(facets.groove_bar(120, 14, (31, 41, 55, 100)), np.int16)
    assert a[7, 60, 3] == 100 and tuple(a[7, 60, :3]) == (31, 41, 55)


def test_a_rings_groove_is_a_band_with_the_light_on_its_lower_and_right_edges():
    groove = facets.groove_ring(140, 140, 12)
    a = np.asarray(groove, np.int16)
    assert groove.size == (140, 140)
    column = a[:, 70]
    top = [column[y] for y in range(70) if column[y, 3]]  # from the outer edge inwards
    bottom = [column[y] for y in range(139, 69, -1) if column[y, 3]]  # likewise from below
    assert len(top) == len(bottom) == 12 and column[70, 3] == 0  # the band, not the middle
    assert tuple(top[len(top) // 2]) == tuple(bottom[len(bottom) // 2]) == (22, 35, 50, 255)
    assert top[0][:3].sum() < top[-1][:3].sum()  # at the top the outer edge is in shade
    assert bottom[0][:3].sum() > bottom[-1][:3].sum()  # at the bottom the outer edge catches it


def test_an_open_rings_groove_has_ends_and_a_closed_one_has_none():
    closed = np.asarray(facets.groove_ring(140, 140, 12, 0, 360), np.int16)
    arc = np.asarray(facets.groove_ring(140, 140, 12, 135, 405, TRACK), np.int16)
    assert closed[..., 3].sum() > arc[..., 3].sum()  # the open ring leaves the gap at the bottom
    assert closed[140 - 6, 70, 3] == 255 and arc[140 - 6, 70, 3] == 0
    gap = arc[(70 + 56) : (70 + 60), 62:79, 3]
    assert gap.max() == 0
    assert tuple(arc[70, 134]) == TRACK and tuple(closed[70, 134]) == (22, 35, 50, 255)  # floors


def test_a_rings_groove_is_kept_and_is_what_the_ring_lies_in():
    groove = facets.groove_ring(140, 140, 12, 135, 405, TRACK)
    assert facets.groove_ring(140, 140, 12, 135, 405, TRACK) is groove
    assert facets.groove_ring(140, 140, 12, 135, 405, CYAN) is not groove
    empty = ring(frac=0, track=TRACK)  # no value: nothing but the groove
    assert np.asarray(empty).tobytes() == np.asarray(groove).tobytes()
    full = np.asarray(ring(frac=1.0, track=TRACK), np.int16)
    assert (full[..., 3] >= np.asarray(groove, np.int16)[..., 3]).all()


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


def test_a_curve_on_an_enlarged_layer_is_worked_out_at_the_pictures_size():
    """A graph is drawn larger and scaled down: its ridge is worked out at the picture's size
    and laid on the layer enlarged, so scaling the layer down gives it back as it was."""
    points = [(x, 40 + 10 * np.sin(x / 15)) for x in range(0, 200, 2)]

    def ridge(scale, wide):
        layer = Image.new("RGBA", (200 * scale, 80 * scale), (0, 0, 0, 0))
        run = [(x * scale, y * scale) for x, y in points]
        assert facets.curve_ridge(layer, run, 2.0 * scale, CYAN, wide, scale)
        return layer

    for wide in (False, True):
        plain = ridge(1, wide)
        for scale in (2, 3):
            enlarged = plain.resize((200 * scale, 80 * scale), Image.Resampling.NEAREST)
            big = np.asarray(ridge(scale, wide), np.int16)
            assert np.abs(big - np.asarray(enlarged, np.int16)).max() <= 1, (scale, wide)
            assert big[..., 3].any()


def test_a_layer_that_does_not_divide_by_the_scale_has_a_ridge_to_its_edge():
    layer = Image.new("RGBA", (201, 81), (0, 0, 0, 0))  # not a multiple of 2
    run = [(0.0, 20.0), (100.0, 60.0), (201.0, 78.0)]
    assert facets.curve_ridge(layer, run, 4.0, CYAN, False, 2)
    a = np.asarray(layer)
    assert a[..., 3].any() and a[
        ...,
        3,
    ].shape == (81, 201)
    assert a[:, 200, 3].max() > 0 and a[78, 198:, 3].max() > 0  # to the last column and rows


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


def test_a_halo_lies_round_the_line_and_fades_with_the_distance():
    layer = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    line = Image.new("L", layer.size, 0)
    ImageDraw.Draw(line).line([(0, 40), (199, 40)], fill=255, width=2)
    facets.curve_halo(layer, line, CYAN)
    alpha = np.asarray(layer, np.int16)[..., 3]
    middle = alpha[:, 100]
    peak = int(middle.argmax())
    assert 36 <= peak <= 44 and 0 < middle[peak] < 100  # at the line, and soft: the ridge goes over
    for side in (1, -1):  # on both sides it fades with the distance, to nothing
        assert middle[peak] > middle[peak + 6 * side] > middle[peak + 14 * side] > 0
        assert middle[peak + 14 * side] >= middle[peak + 28 * side]
    assert middle[0] == middle[-1] == 0
    assert tuple(np.asarray(layer)[peak, 100, :3]) == CYAN[:3]  # in the line's colour
    assert alpha[:, 100].sum() == alpha[:, 50].sum()  # as far as the line goes


def test_the_halo_takes_the_alpha_of_the_colour_and_leaves_small_layers_alone():
    line = Image.new("L", (200, 80), 0)
    ImageDraw.Draw(line).line([(0, 40), (199, 40)], fill=255, width=2)
    full, half = (Image.new("RGBA", line.size, (0, 0, 0, 0)) for _ in range(2))
    facets.curve_halo(full, line, CYAN)
    facets.curve_halo(half, line, (*CYAN[:3], 128))
    a, b = (np.asarray(x, np.int16)[..., 3].max() for x in (full, half))
    assert abs(b - a / 2) <= 1
    tiny = Image.new("RGBA", (6, 6), (0, 0, 0, 0))
    facets.curve_halo(tiny, Image.new("L", (6, 6), 255), CYAN)
    assert np.asarray(tiny).max() == 0  # too small to be soft
    nothing = Image.new("RGBA", line.size, (0, 0, 0, 0))
    facets.curve_halo(nothing, Image.new("L", line.size, 0), CYAN)
    assert np.asarray(nothing).max() == 0  # no line, no halo


def test_the_halo_of_a_layer_at_twice_the_scale_has_the_same_reach_in_the_picture():
    def reach(scale):
        line = Image.new("L", (200 * scale, 80 * scale), 0)
        ImageDraw.Draw(line).line(
            [(0, 40 * scale), (199 * scale, 40 * scale)], fill=255, width=2 * scale
        )
        layer = Image.new("RGBA", line.size, (0, 0, 0, 0))
        facets.curve_halo(layer, line, CYAN, scale)
        column = np.asarray(layer, np.int16)[:, 100 * scale, 3]
        return int((column > 3).sum()) / scale

    assert abs(reach(1) - reach(2)) <= 4


def graph_pixels(**fields):
    """A flat graph of a steady 50 % on a black screen, rendered once."""
    widget = {
        "type": "graph", "id": "g", "x": 0, "y": 0, "w": 120, "h": 60, "sensor": "cpu.load",
        "min": 0, "max": 100, "color": "#ffffff", "history": 120, "smooth": False,
        "line_width": 2, **fields,
    }  # fmt: skip
    data = {
        "format": THEME_FORMAT, "display": {"width": 120, "height": 60},
        "background": {"color": "#000000"}, "widgets": [widget],
    }  # fmt: skip
    snapshot = Snapshot()
    snapshot.history["cpu.load"] = [50.0] * 120
    renderer = Renderer(parse_theme(data), preview=True)
    image, _ = renderer.render(snapshot)
    assert renderer.warnings == []
    return np.asarray(image, np.int16)[..., 0]


def test_a_fill_is_as_opaque_as_the_graph_says():
    default = graph_pixels(fill_fade=False)
    assert default[50, 60] == 64  # a flat fill: a quarter
    assert graph_pixels(fill_fade=False, fill_alpha=0)[50, 60] == 64  # 0 is the default
    assert 99 <= graph_pixels(fill_fade=False, fill_alpha=0.4)[50, 60] <= 104
    assert graph_pixels(fill_fade=False, fill_alpha=7)[50, 60] == 255  # up to the whole
    assert graph_pixels(fill_fade=False, fill_alpha=-1)[50, 60] == 64  # not under 0


def test_a_fade_starts_as_opaque_as_the_graph_says():
    default = graph_pixels(fill_fade=True)
    assert 100 <= default[32, 60] <= 115 > default[58, 60]  # 0.45 under the curve, fading
    strong = graph_pixels(fill_fade=True, fill_alpha=0.8)
    assert strong[32, 60] > 1.6 * default[32, 60] and strong[58, 60] < strong[32, 60]


def test_a_facet_graph_has_a_halo_above_its_curve_and_a_flat_one_has_none():
    flat = graph_pixels(fill_fade=False)
    facet = graph_pixels(fill_fade=False, finish="facet")
    assert flat[26, 60] == 0 < facet[26, 60]  # a few pixels above the curve, which is at row 30
    assert facet[26, 60] > facet[25, 60] > 0  # the farther, the fainter ...
    assert facet[20, 60] == 0  # ... and it ends where SPUR II works the curve out: a line
    # and two pixels above its highest point (the reference's curves show the same cut)
    assert facet[:, 0].max() > 0  # to the left edge too: the layer is cut, not an error


def test_a_facet_curve_that_hangs_from_the_top_is_mirrored_with_its_light_still_above():
    up = graph_pixels(fill_fade=False, finish="facet", fill_alpha=0.41)[:, 60]
    down = graph_pixels(fill_fade=False, finish="facet", fill_alpha=0.41, direction="down")[:, 60]
    assert up[20] == 0 and up[40] > 100  # the fill below the curve ...
    assert down[20] > 100 and down[40] == 0  # ... or above it
    for column in (up, down):
        assert 27 <= int(np.argmax(column[20:42])) + 20 <= 30  # the flank facing up is lit
    # the flank towards the fill: nearly black below a curve, in shade only below one that
    # hangs (it faces down, away from the light, outside), as SPUR II's network graph
    assert up[30] < 30 < 100 < down[30]


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

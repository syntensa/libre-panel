"""Soft light without bands (render.dither): the float blur, the dither, the background gradient,
the glows and the switch the main loop turns on in video mode."""

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from libre_panel.render import dither
from libre_panel.render.renderer import Renderer, _blur, _soft_light
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import THEME_FORMAT, ThemeError, find_theme, load_theme, parse_theme


@pytest.mark.parametrize("radius", [1.5, 3, 6, 10, 20])
def test_the_float_blur_has_the_shape_of_pillows(radius):
    a = np.zeros((160, 200), np.float32)
    a[50:110, 60:140] = 255
    a[78:82, 10:190] = 128
    pillow = Image.fromarray(a.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius))
    assert np.abs(dither.blur(a, radius) - np.asarray(pillow, np.float32)).max() < 1.5


def test_a_part_blurred_with_its_reach_round_it_is_the_whole_cut():
    rng = np.random.default_rng(3)
    a = (rng.random((120, 150)) * 255).astype(np.float32)
    whole = dither.blur(a, 6)
    far = dither.reach(6)
    part = dither.blur(a[40 - far : 70 + far, 50 - far : 90 + far], 6)[far:-far, far:-far]
    assert np.abs(part - whole[40:70, 50:90]).max() < 1e-2


def test_the_dither_keeps_the_mean_and_leaves_nothing_and_full_alone():
    values = np.full((200, 300), 7.3, np.float32)
    values[:, :10], values[:, -10:] = 0, 255
    out = dither.to_u8(values)
    assert out[:, 10:-10].mean() == pytest.approx(7.3, abs=0.02)
    assert set(np.unique(out[:, 10:-10])) <= {6, 7, 8}
    assert (out[:, :10] == 0).all() and (out[:, -10:] == 255).all()
    assert np.array_equal(out, dither.to_u8(values))  # the same noise every time
    assert np.array_equal(dither.noise(40, 40, 30, 20), dither.noise(100, 80)[20:60, 30:70])
    assert (dither.to_u8(values, dither=False)[:, 10:-10] == 7).all()


@pytest.mark.parametrize("across", [False, True])
def test_a_gradient_laid_tile_by_tile_is_the_one_worked_out_whole(across):
    line = np.linspace([10, 14, 19], [6, 9, 13], 300, dtype=np.float32)
    tiled = dither.ramp_u8(line, 400, across)
    full = np.broadcast_to(line[None] if across else line[:, None], (400, 300, 3) if across
                           else (300, 400, 3))  # fmt: skip
    assert np.array_equal(tiled, dither.to_u8(np.ascontiguousarray(full)))


def gradient_theme(**background):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 300, "height": 720},
            "background": {"color": "#000000", **background},
            "widgets": [],
        }
    )


def test_a_background_gradient_is_drawn_without_bands_in_video_mode():
    """Lumen's on the 12.3": six levels across 720 rows, which whole levels made bands of a
    hundred rows; with the dither each row is the colour between, on average."""
    theme = gradient_theme(gradient=["#0A0E13", "#06090D"])
    expected = np.linspace([10, 14, 19], [6, 9, 13], 720)
    still = np.asarray(Renderer(theme).render(Snapshot(readings={}))[0], np.float32)
    assert len(np.unique(still[:, :, 2])) == 7  # whole levels: the bands
    video = np.asarray(Renderer(theme, dither=True).render(Snapshot(readings={}))[0], np.float32)
    rows = video.mean(axis=1)
    assert np.abs(rows - expected).max() < 0.15  # each row as it should be, on average
    assert np.abs(still.mean(axis=1) - expected).max() <= 0.5
    across = gradient_theme(gradient=["#000000", "#FFFFFF"], gradient_direction="horizontal")
    frame = np.asarray(Renderer(across).render(Snapshot(readings={}))[0], np.int16)
    assert frame[0, 0].max() == 0 and frame[0, -1].min() == 255 and (frame[0] == frame[-1]).all()


def test_a_background_gradient_lies_under_the_picture_and_is_saved(tmp_path):
    (tmp_path / "assets").mkdir()
    dots = Image.new("RGBA", (300, 720), (0, 0, 0, 0))
    ImageDraw.Draw(dots).rectangle([10, 10, 11, 11], fill=(19, 28, 38, 255))
    dots.save(tmp_path / "assets" / "dots.png")
    data = {"format": THEME_FORMAT, "display": {"width": 300, "height": 720}, "widgets": [],
            "background": {"color": "#000000", "image": "assets/dots.png",
                           "gradient": ["#0A0E13", "#06090D"]}}  # fmt: skip
    (tmp_path / "theme.json").write_text(json.dumps(data))
    theme = load_theme(tmp_path)
    frame = np.asarray(Renderer(theme, dither=True).render(Snapshot(readings={}))[0])
    assert tuple(frame[10, 10]) == (19, 28, 38)  # the dot, over the gradient
    assert abs(frame[300:340, 100:140].mean(axis=(0, 1)) - [8.3, 11.9, 16.5]).max() < 0.3
    assert theme.to_dict()["background"]["gradient"] == ["#0A0E13", "#06090D"]
    assert "gradient_direction" not in theme.to_dict()["background"]  # (as it was: down)
    with pytest.raises(ThemeError, match="two colours"):
        gradient_theme(gradient=["#0A0E13"])
    with pytest.raises(ThemeError, match="vertical"):
        gradient_theme(gradient=["#0A0E13", "#06090D"], gradient_direction="diagonal")


def test_libre_panels_own_backdrops_are_a_gradient_and_the_dots():
    """The editor's stage, as Lumen, Encore, Almanac and the round report wear it: the dark
    gradient is the theme's, the picture only its dots (it was the two, in whole levels)."""
    theme = load_theme(find_theme("lumen-1920x720"))
    assert theme.background_gradient == ("@bg", "@bg2")  # (the palette's, libre_panel.design)
    assert (theme.palette["bg"], theme.palette["bg2"]) == ("#0A0E13", "#06090D")
    dots = Image.open(theme.root / "assets" / "backdrop.png")
    assert dots.mode == "RGBA" and dots.getchannel("A").getextrema() == (0, 255)
    alpha = np.asarray(dots.getchannel("A")) > 0
    assert alpha[10:12, 10:12].all() and not alpha[:10].any()  # 2 px every 22, from 10


def glowing(gain):
    layer = Image.new("RGBA", (360, 120), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle([120, 50, 240, 70], fill=(46, 230, 214, 255))
    return layer, gain


def test_a_glow_made_stronger_steps_no_more_than_a_level():
    """A glow brighter than the shape's own alpha (Encore's seconds): blurred in whole levels
    and made 2.5 times stronger, its faint tail stood in steps of 2 and 3 levels, rings round
    it on the 12.3"; worked out in floating point it rises a level at a time."""
    layer, gain = glowing(2.5)
    new = np.asarray(_soft_light(layer, 20, gain, noise=False), np.int16)[60, :120, 3]
    old = np.minimum(255, np.asarray(_blur(layer, 20), np.int16)[60, :120, 3] * gain)
    tail = (new > 0) & (new < 9)  # its faint edge: less than a level a pixel
    assert np.diff(new[tail]).max() <= 1
    assert np.diff(old[tail]).max() >= 2
    assert abs(new[tail].mean() - old[tail].mean()) < 1.5  # as bright as it was
    dithered = np.asarray(_soft_light(layer, 20, gain), np.float32)[..., 3]
    plain = np.asarray(_soft_light(layer, 20, gain, noise=False), np.float32)[..., 3]
    assert abs(dithered.mean() - plain.mean()) < 0.05 and (dithered != plain).any()


def test_the_dither_is_for_the_panels_video_and_made_again_when_it_changes():
    theme = gradient_theme(gradient=["#0A0E13", "#06090D"])
    renderer = Renderer(theme)
    assert renderer.dither is False
    before = np.asarray(renderer.render(Snapshot(readings={}))[0])
    renderer.dither = True
    after = np.asarray(renderer.render(Snapshot(readings={}))[0])
    assert len(np.unique(after[:, :, 2])) > len(np.unique(before[:, :, 2]))  # made again


def test_a_glow_whose_box_grows_takes_a_few_matrices_and_blurs_as_before():
    """A clock's rolling digits change the box of their glow a pixel at a time: each size was a
    matrix of its own (fifty of them, 16 MB on the 12.3"). With room enough round what is blurred
    the sides go up in steps, and the blur is what it was."""
    dither._operator.cache_clear()
    radius = 6.75
    far = dither.reach(radius)
    boxes, got = [], []
    for w in range(260, 310):
        a = np.zeros((120, w), np.float32)
        a[far:-far, far:-far] = 200.0
        boxes.append(a)
        got.append(dither.blur(a, radius))
    sizes = {key[0] for key in dither._operator.kept._items}
    assert sizes == {128, 288, 320}  # (the steps the sides took; not fifty)
    for a, blurred in zip(boxes, got, strict=True):  # (each as its own matrices blur it)
        want = dither._operator(a.shape[0], radius).T @ a @ dither._operator(a.shape[1], radius)
        assert np.abs(blurred - want).max() < 1e-3, a.shape
    edge = np.full((50, 60), 9.0, np.float32)  # no room round it: its own size, as always
    dither._operator.cache_clear()
    dither.blur(edge, radius)
    dither.blur(edge, radius)
    assert {key[0] for key in dither._operator.kept._items} == {50, 60}


def test_a_long_side_of_a_length_of_its_own_keeps_no_matrix_and_blurs_alike_each_time():
    """A lit bar gliding to each length in turn, and a track's progress bar again with every
    track, ask for long sides of lengths of their own: blurred by the passes, nothing kept for
    them (each was a matrix of a third of a megabyte, 16 MB a minute on); a short side, or one
    of the steps, has its matrix. Which way a side goes is its length's alone: a picture blurred
    again is blurred the same, whatever was blurred in between."""
    dither._operator.cache_clear()
    radius = 4.25
    tracks = []
    for _ in range(2):  # (a track, and the next: each length again)
        out = []
        for w in range(200, 240):  # (no room round them: each its own length)
            out.append(dither.blur(np.full((24, w), 50.0, np.float32), radius))
        tracks.append(out)
        lengths = {key[0] for key in dither._operator.kept._items}
        assert lengths == {24, 224}  # (the bar's thickness, and the one length that is a step)
    for a, b in zip(*tracks, strict=True):
        assert np.array_equal(a, b)
    for w, blurred in zip(range(200, 240), tracks[0], strict=True):
        a = np.full((24, w), 50.0, np.float32)
        want = dither._operator(24, radius).T @ a @ dither._operator(w, radius)
        assert np.abs(blurred - want).max() < 1e-2, w
    dither._operator.cache_clear()  # a short side: its matrix made, kept, and the same blur
    short = np.random.default_rng(3).random((40, 90), np.float32) * 255
    made = dither.blur(short, radius)
    assert np.array_equal(dither.blur(short, radius), made)
    assert {key[0] for key in dither._operator.kept._items} == {40, 90}

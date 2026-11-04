"""A list's gliding bars laid in one array (render.renderer._Levels) from the bars kept as
arrays (_Bar): the pixels the pictures of the bars give laid one by one on a transparent
picture, for Arena's columns and rows of bars at every frame of a glide; frames as those of a
renderer that draws the whole list anew; and a bar that lies on another."""

import numpy as np
import pytest
from PIL import Image

from libre_panel.render import renderer as R
from libre_panel.render import scene as scenes
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme


def laid_one_by_one(renderer, widget, rows):
    """The levels as they were laid before: each bar's picture on a picture of the place."""
    w, h = max(1, widget["w"]), max(1, widget["h"])
    margin = max(2, widget["font_size"] // 3)
    inner = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if widget["style"] == "columns":
        renderer._list_columns(None, inner, widget, rows)
    else:
        renderer._list_rows(None, inner, widget, rows)
    layer = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    layer.alpha_composite(inner, (margin, margin))
    return layer


@pytest.mark.parametrize("cut", [False, True])  # (cut by an engine from the bars' parts)
@pytest.mark.parametrize("name", ["arena-1920x720", "arena"])
def test_the_bars_laid_in_one_array_are_the_bars_laid_one_by_one(name, cut, monkeypatch):
    renderer = R.Renderer(load_theme(find_theme(name)), animate=True)
    renderer.continuous = renderer.dither = True
    renderer.native_views = cut
    checked, views = [], 0
    piece = R.Renderer._list_piece

    def compared(self, widget, rows, part="all"):
        nonlocal views
        got = piece(self, widget, rows, part)
        if part == "levels":
            if isinstance(got.native, scenes.View):  # (its pixels not made: made now)
                assert got.native.layer is got.layer and not got.layer.composed
                assert got.layer.info["bars"]  # (where its bars lie: as for the picture)
                views += 1
            want = laid_one_by_one(self, widget, rows)
            assert got.layer.size == want.size
            assert np.array_equal(np.asarray(got.layer), np.asarray(want)), widget["id"]
            checked.append(widget["style"])
        return got

    monkeypatch.setattr(R.Renderer, "_list_piece", compared)
    try:
        for i in range(3 * 24):
            now = 1000 + i / 24
            if i % 24 == 0:
                snap = demo_snapshot(fixed_time=1_700_000_000 + i // 24)
                renderer.new_sample(now, 1.0)
            renderer.render(snap, now)
        assert {"columns", "bars"} <= set(checked) and len(checked) >= 40
        assert renderer.warnings == []
        assert views >= 40 if cut else views == 0  # (all cut so: no bar lies on another)
    finally:
        renderer.close()


def test_a_list_drawn_whole_is_the_list_laid_in_parts():
    """A renderer that keeps nothing draws each list whole, its bars as pictures: the frames
    are the same."""
    theme = load_theme(find_theme("arena-1920x720"))
    kept, anew = R.Renderer(theme, animate=True), R.Renderer(theme, animate=True)
    anew.keep_static = anew.incremental = False
    try:
        for renderer in (kept, anew):
            renderer.continuous = renderer.dither = True
        for i in range(36):
            now = 1000 + i / 24
            if i % 24 == 0:
                snap = demo_snapshot(fixed_time=1_700_000_000 + i // 24)
                for renderer in (kept, anew):
                    renderer.new_sample(now, 1.0)
            a, _ = kept.render(snap, now)
            b, _ = anew.render(snap, now)
            box = (820, 380, 1880, 700)  # (the processor's columns, the temperatures' rows)
            assert np.array_equal(np.asarray(a.crop(box)), np.asarray(b.crop(box))), i
    finally:
        kept.close()
        anew.close()


def test_a_bar_on_another_is_laid_on_it():
    levels = R._Levels((20, 10), (2, 2), (16, 6))
    red = np.zeros((6, 8, 4), np.uint8)
    red[..., 0], red[..., 3] = 255, 128
    blue = np.zeros((6, 8, 4), np.uint8)
    blue[..., 2], blue[..., 3] = 255, 128
    levels.put(red, 0, 0)
    levels.put(blue, 4, 0)  # half of it on the red one
    levels.put(blue, 14, 4)  # cut to the place
    want = Image.new("RGBA", (16, 6), (0, 0, 0, 0))
    for pixels, at in ((red, (0, 0)), (blue, (4, 0))):
        want.alpha_composite(Image.fromarray(pixels, "RGBA"), at)
    want.alpha_composite(Image.fromarray(blue, "RGBA").crop((0, 0, 2, 2)), (14, 4))
    got = levels.image().crop((2, 2, 18, 8))
    assert np.array_equal(np.asarray(got), np.asarray(want))
    assert not np.asarray(levels.image())[:2].any()  # (the margin stays empty)

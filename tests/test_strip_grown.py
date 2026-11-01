"""A graph's strip made of the one before (Renderer._grown_strip): drawn anew only from where the
curve changes, it is the strip drawn whole, pixel for pixel, but in its first columns (the
margin of its glow, where the one before saw further back)."""

import numpy as np
import pytest

from libre_panel.render import renderer as R
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme


@pytest.mark.parametrize("name", ["lumen-1920x720", "arena"])
def test_a_strip_grown_from_the_one_before_is_the_strip_drawn_whole(name, monkeypatch):
    renderer = R.Renderer(load_theme(find_theme(name)))
    renderer.continuous = renderer.dither = True
    seen = {"grown": 0, "whole": 0}
    grow = R.Renderer._grown_strip

    def checked(self, widget, before, values, first, per_point, every, strip, bounds):
        got = grow(self, widget, before, values, first, per_point, every, strip, bounds)
        if got is None:
            seen["whole"] += 1
            return None
        seen["grown"] += 1
        whole = R._Strip(got.made, got.taken, got.until, got.pieces)
        whole.parts = got.parts
        whole = self._draw_strip(widget, values, first, per_point, every, whole, bounds)
        assert (whole.origin, whole.width, whole.pad) == (got.origin, got.width, got.pad)
        reach = self._reach(self._dense(widget, every * per_point))
        margin = reach + 3 * widget.get("glow_radius", 10) + 8 + got.pad + 4
        for a, b in zip([*got.phases, got.halo], [*whole.phases, whole.halo], strict=True):
            if a is not None:
                a, b = np.asarray(a, int)[:, margin:], np.asarray(b, int)[:, margin:]
                assert np.array_equal(a, b), widget["id"]
        return got

    monkeypatch.setattr(R.Renderer, "_grown_strip", checked)
    snap = None
    for i in range(6 * 30):  # six seconds at 30 frames: a reading each
        if i % 30 == 0:
            snap = demo_snapshot(fixed_time=1_700_000_000 + i / 30)
            renderer.new_sample(1000 + i / 30, 1.0)
        renderer.render(snap, 1000 + i / 30)
    assert seen["grown"] >= 4 and renderer.warnings == []
    renderer.close()


def test_a_strip_in_another_range_is_drawn_whole():
    renderer = R.Renderer(load_theme(find_theme("lumen-1920x720")))
    widget = next(w for w in renderer.widgets if w.get("type") == "graph")
    before = R._Strip(0, 0.0, 0.0, ())
    values = [10.0 + (i % 7) for i in range(400)]
    before = renderer._draw_strip(widget, values, 1000, 2.0, 1, before, (0.0, 100.0))
    strip = R._Strip(0, 0.0, 0.0, ())
    assert renderer._grown_strip(widget, before, values, 1000, 2.0, 1, strip, (0.0, 50.0)) is None
    renderer.close()

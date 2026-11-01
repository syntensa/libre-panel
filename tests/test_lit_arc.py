"""A lit ring's arc worked out from each pixel's distance to its edges (render.lit._cover): on
the middle of the groove, as much light as the arc's length and width, the same in a part as
in the whole, and an oval still drawn."""

import math

import numpy as np
import pytest

from libre_panel.render import lit

RING = {"w": 174, "h": 174, "thickness": 11, "start_angle": -90, "end_angle": 270}
STROKE, ROOM = 7, 27


def test_a_closed_ring_is_the_same_all_round_and_on_the_middle_of_its_groove():
    cover = lit._arc(RING, 1.0, STROKE, ROOM)
    assert np.allclose(cover, cover[:, ::-1]) and np.allclose(cover, cover[::-1, :])
    assert np.allclose(cover, cover.T)
    side = cover.shape[0]
    at = np.arange(side) + 0.5 - side / 2
    far = np.hypot(at[None, :], at[:, None])
    middle = (far * cover).sum() / cover.sum()
    assert middle == pytest.approx((RING["w"] - RING["thickness"]) / 2, abs=0.05)


@pytest.mark.parametrize("frac", [0.05, 0.25, 0.5, 0.875, 1.0])
def test_as_much_light_as_the_arc_is_long_and_wide(frac):
    cover = lit._arc(RING, frac, STROKE, ROOM)
    middle = (RING["w"] - RING["thickness"]) / 2
    area = STROKE * 2 * math.pi * middle * frac
    assert cover.sum() == pytest.approx(area, rel=0.01)


def test_the_arc_ends_where_its_value_says():
    cover = lit._arc(RING, 0.25, STROKE, ROOM)  # from the top a quarter clockwise: to the right
    side = cover.shape[0]
    c = side // 2
    right = cover[c - 2 : c + 2, c + 75 : c + 90].sum()
    left = cover[c - 2 : c + 2, c - 90 : c - 75].sum()
    assert left == 0 and right > 0
    assert cover[c + 75 : c + 90, c - 2 : c + 2].sum() == 0  # (nothing at the bottom)


def test_a_part_is_the_same_pixels_as_the_whole():
    whole = lit._arc(RING, 0.62, STROKE, ROOM)
    box = (20, 90, 110, 200)
    part = lit._arc_part(RING, 0.62, STROKE, ROOM, box)
    assert np.array_equal(part, whole[box[1] : box[3], box[0] : box[2]])


def test_an_oval_is_still_drawn():
    oval = {**RING, "h": 120}
    cover = lit._arc(oval, 0.5, STROKE, ROOM)
    assert cover.shape == (120 + 2 * ROOM, 174 + 2 * ROOM) and cover.max() > 0.9
    assert not lit._round(oval) and lit._round(RING)

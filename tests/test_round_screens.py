"""The round versions of Libre Panel's screens (480x480: the Turing 2.1" and 2.8", Waveshare's USB
monitor as a USB display): each comes up where its screen does, draws whole with the demo's
readings and without them, keeps every word and number inside the circle, and no two of them
run into each other."""

import itertools
import math

import pytest
from test_libre_themes import data_of, drawn

from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import builtin_themes_dir, theme_for

ROUND = ["lumen", "encore", "nimbus", "almanac", "arena", "round-report"]
WORDS = ("text", "metric", "clock", "weather")  # what is read: inside the circle, apart
RADIUS, CENTRE = 240, (240, 240)
ROOM = 4  # px a word keeps from the rim at least


def without(*prefixes):
    """The demo's readings without those of ``prefixes`` (no music, no weather)."""
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    for key in [k for k in snapshot.readings if k.startswith(prefixes)]:
        del snapshot.readings[key]
    for key in [k for k in snapshot.images if k.startswith(prefixes)]:
        del snapshot.images[key]
    return snapshot


def outside(box):
    """How far the box's farthest corner lies past the circle (0: inside)."""
    x, y, w, h = box
    far = max(math.dist(CENTRE, corner) for corner in
              ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))  # fmt: skip
    return max(0.0, far - (RADIUS - ROOM))


@pytest.mark.parametrize("name", ROUND)
def test_each_screen_has_its_round_version(name):
    assert theme_for(name, "landscape", (480, 480)) == f"{name}-480x480"
    data = data_of(f"{name}-480x480")
    assert (data["display"]["width"], data["display"]["height"]) == (480, 480)


@pytest.mark.parametrize("readings", ["demo", "nothing playing, no weather"])
@pytest.mark.parametrize("name", [f"{n}-480x480" for n in ROUND] + ["contrast-480x480"])
def test_a_round_screen_keeps_its_words_inside_the_circle_and_apart(name, readings):
    snapshot = None if readings == "demo" else without("media.", "weather.")
    theme, renderer, frame, boxes = drawn(data_of(name), builtin_themes_dir() / name, snapshot)
    assert theme.warnings == [] and renderer.warnings == []
    assert frame.size == (480, 480)
    kinds = {w["id"]: w for w in theme.widgets}
    words = {wid: box for wid, box in boxes.items()
             if kinds.get(wid, {}).get("type") in WORDS or "/" in wid}  # fmt: skip
    for wid, box in words.items():
        assert outside(box) == 0, (name, wid, box)
    if name == "contrast-480x480":  # (its modules lay their words out themselves)
        return
    for (a, (ax, ay, aw, ah)), (b, (bx, by, bw, bh)) in itertools.combinations(words.items(), 2):
        across = min(ax + aw, bx + bw) - max(ax, bx)
        down = min(ay + ah, by + bh) - max(ay, by)
        assert not (across > 2 and down > 2), (name, a, b)

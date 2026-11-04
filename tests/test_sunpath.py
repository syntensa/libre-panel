"""The sun's way over the sky (the ``sunpath`` widget, render/sunpath.py)."""

import numpy as np
from PIL import Image

from libre_panel.render import sunpath
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import THEME_FORMAT, parse_theme

WARM = (245, 196, 107)


def theme(**fields):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 240, "height": 80},
            "background": {"color": "#0a0e13"},
            "widgets": [{"type": "sunpath", "id": "s", "x": 10, "y": 10, "w": 200, "h": 50,
                         "color": "#f5c46b", "night": "#c9d3de", **fields}],
        }
    )  # fmt: skip


def snap(**readings):
    return Snapshot(readings={k.replace("__", "."): Reading(k.replace("__", "."), v)
                              for k, v in readings.items()})  # fmt: skip


def warmth(image):
    """Where the warm light is (its middle, across) and how much of it there is."""
    a = np.asarray(image.convert("RGB"), np.float32)
    warm = np.clip(a[..., 0] - a[..., 2] - 30, 0, None) * (a[..., 0] > 120)  # (the moon is cool)
    total = float(warm.sum())
    across = float((warm.sum(axis=0) * np.arange(a.shape[1])).sum() / max(total, 1.0))
    return across, total


def test_the_sun_goes_its_way_from_sunrise_to_sunset():
    renderer = Renderer(theme())
    morning = renderer.render(snap(sun__up=1, sun__progress=0.15, sun__rise="07:00"))[0]
    evening = renderer.render(snap(sun__up=1, sun__progress=0.85, sun__rise="07:00"))[0]
    (x_morning, light_morning), (x_evening, light_evening) = warmth(morning), warmth(evening)
    assert x_evening > x_morning + 40  # from the left (east) to the right (west)
    assert light_evening > light_morning  # (more of the day gone: more of the way in light)


def test_by_night_the_moon_stands_over_the_way_and_without_the_suns_times_it_is_not_there():
    renderer = Renderer(theme())
    night = renderer.render(snap(sun__up=0, sun__rise="07:00"))[0]
    assert warmth(night)[1] == 0  # no sun
    a = np.asarray(night.convert("L"), np.float32)
    assert a[:, 100:120].max() > 150  # the moon over the middle of the way
    nothing = renderer.render(snap(weather__code=3))[0]
    assert nothing.getbbox() == Image.new("RGB", nothing.size, "#0a0e13").getbbox()
    assert np.asarray(nothing.convert("L"), np.float32)[10:60, 10:210].max() < 20
    midnight_sun = renderer.render(snap(sun__up=1))[0]  # up all day: at its height
    a = np.asarray(midnight_sun.convert("RGB"), np.float32)
    warm = np.clip(a[..., 0] - a[..., 2] - 30, 0, None).sum(axis=0)
    assert abs(int(warm.argmax()) - (10 + 100)) < 8  # (the sun's disc: where most light is)


def test_the_picture_of_a_step_of_the_day_is_kept():
    renderer = Renderer(theme())
    renderer.render(snap(sun__up=1, sun__progress=0.40))
    piece = renderer._cache["s"][1]
    renderer.render(snap(sun__up=1, sun__progress=0.4001))  # (the same step: kept)
    assert renderer._cache["s"][1] is piece
    renderer.render(snap(sun__up=1, sun__progress=0.45))
    assert renderer._cache["s"][1] is not piece
    assert sunpath.step(None) is None and sunpath.step("x") is None and sunpath.step(1.5) is None
    assert sunpath.step(0) == 0 and sunpath.step(1) == sunpath.STEPS

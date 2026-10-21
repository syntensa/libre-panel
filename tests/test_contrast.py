"""High Contrast: very large digits in white on black, a version for every panel size, and the
temperatures orange when warm and red when hot (the ``huge`` module)."""

from datetime import datetime

import pytest
from PIL import Image, ImageChops

from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import find_theme, load_theme, theme_for

VERSIONS = ["contrast", "contrast-vertical", "contrast-480x320", "contrast-320x480",
            "contrast-480x480", "contrast-800x480", "contrast-960x320", "contrast-1024x600",
            "contrast-1280x720", "contrast-1280x800", "contrast-1920x720"]  # fmt: skip


def frame(name, temp):
    theme = load_theme(find_theme(name))
    renderer = Renderer(theme, animate=False, preview=True)
    snapshot = Snapshot(readings={"cpu.temp": Reading("cpu.temp", temp, "°C"),
                                  "gpu.temp": Reading("gpu.temp", 55, "°C")})  # fmt: skip
    snapshot.now = datetime(2026, 10, 21, 21, 37)
    image, boxes = renderer.render(snapshot, 1.0)
    assert theme.warnings == [] and renderer.warnings == []
    return theme, image, boxes


def digits(image, box, colour):
    """The box round the pixels of ``colour`` inside a module (its outline left out)."""
    x, y, w, h = box
    inner = image.crop((x + 4, y + h // 4, x + w - 4, y + h - 4)).convert("RGB")
    mask = inner.point(lambda v: 255 if v > 200 else 0).convert("L") if colour == "white" else None
    if mask is None:
        target = Image.new("RGB", inner.size, colour)
        near = ImageChops.difference(inner, target).convert("L")
        mask = near.point(lambda v: 255 if v < 40 else 0)
    return mask.getbbox()


@pytest.mark.parametrize("name", VERSIONS)
def test_every_version_draws_its_numbers_large_on_black(name):
    theme, image, boxes = frame(name, 62)
    assert image.size == (theme.width, theme.height)
    x, y, w, h = boxes["m-cpu"]
    left, top, right, bottom = digits(image, boxes["m-cpu"], "white")
    assert bottom - top >= min(w, h) * 0.25  # very large digits
    assert image.getpixel((x + w // 2, y + h - 8))[:3] == (0, 0, 0)  # on black


def test_a_warm_processor_is_orange_and_a_hot_one_red():
    for temp, colour in ((84, "#FF9F0A"), (93, "#FF453A")):
        _, image, boxes = frame("contrast-480x320", temp)
        found = digits(image, boxes["m-cpu"], colour)
        assert found is not None and found[3] - found[1] >= boxes["m-cpu"][3] * 0.35
        assert digits(image, boxes["m-gpu"], colour) is None  # the graphics card: as it is


def test_the_version_for_the_panel_is_chosen():
    assert theme_for("contrast", "landscape", (1920, 480)) == "contrast"
    assert theme_for("contrast", "portrait", (480, 1920)) == "contrast-vertical"
    assert theme_for("contrast", "landscape", (480, 320)) == "contrast-480x320"
    assert theme_for("contrast", "portrait", (320, 480)) == "contrast-320x480"
    assert theme_for("contrast-1920x720", "landscape", (800, 480)) == "contrast-800x480"

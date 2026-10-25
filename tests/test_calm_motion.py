"""A slow panel (the USB serial ones) gets calm motion and the parts that changed one by one:
app.calm, renderer.changed_regions and the main loop with a slow display."""

import threading
import time

import pytest
from PIL import Image, ImageDraw

from libre_panel import app
from libre_panel.config import Config, ConfigError, DeviceConfig, SensorsConfig, parse_config
from libre_panel.devices.base import Display
from libre_panel.render.renderer import changed_region, changed_regions


def picture(*boxes, size=(480, 320)):
    image = Image.new("RGB", size, (8, 10, 14))
    draw = ImageDraw.Draw(image)
    for box in boxes:
        draw.rectangle(box, fill=(240, 240, 240))
    return image


def test_the_parts_that_changed_are_a_few_boxes_not_one_around_them():
    before = picture()
    corners = picture((4, 4, 40, 20), (430, 290, 470, 310))  # a clock and a value, far apart
    assert changed_region(before, corners) == (4, 4, 471, 311)  # most of the screen
    assert changed_regions(before, corners) == [(4, 4, 41, 21), (430, 290, 471, 311)]
    near = picture((4, 4, 40, 20), (50, 6, 60, 18))  # side by side: one box
    assert changed_regions(before, near) == [(4, 4, 61, 21)]
    many = picture(*[(10 + 60 * i, 10 + 40 * (i % 2) * 6, 20 + 60 * i, 20 + 40 * (i % 2) * 6)
                     for i in range(7)])  # fmt: skip
    found = changed_regions(before, many, most=4)
    assert len(found) == 4
    changed = [p for p in range(480 * 320) if before.getpixel((p % 480, p // 480))
               != many.getpixel((p % 480, p // 480))][::97]  # fmt: skip
    for p in changed:  # every changed pixel is in one of them
        x, y = p % 480, p // 480
        assert any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in found)
    dots = picture(*[(x, y, x + 1, y + 1) for x in range(0, 480, 30) for y in range(0, 320, 30)])
    assert changed_regions(before, dots) == [changed_region(before, dots)]  # all over: one box
    assert changed_regions(before, before) == []
    assert changed_regions(None, before) == [(0, 0, 480, 320)]


class Slow(Display):
    slow = True

    def __init__(self, config):
        super().__init__(config)
        self.parts = []

    def show(self, frame, region=None):
        self.parts.append(region)


class Fast(Slow):
    slow = False


@pytest.mark.parametrize(("motion", "slow", "is_calm"), [
    ("auto", True, True), ("auto", False, False), ("full", True, False), ("calm", False, True),
])  # fmt: skip
def test_motion_is_calm_on_a_slow_panel_unless_it_is_set(motion, slow, is_calm):
    config = Config(device=DeviceConfig(motion=motion))
    display = (Slow if slow else Fast)(config.device)
    assert app.calm(config, display) is is_calm
    assert app._fps(config, None, display) == (1 if is_calm else config.fps)


def test_the_setting_is_read_and_checked():
    assert parse_config({}).device.motion == "auto"
    assert parse_config({"device": {"motion": "calm"}}).device.motion == "calm"
    with pytest.raises(ConfigError, match="device.motion"):
        parse_config({"device": {"motion": "wild"}})


def test_the_loop_draws_a_slow_panel_calmly_and_sends_it_parts(monkeypatch):
    made = {}

    def create(cfg):
        made["display"] = Slow(cfg)
        return made["display"]

    monkeypatch.setattr(app, "create_display", create)
    config = Config(device=DeviceConfig(driver="turzx"), sensors=SensorsConfig(providers=["demo"]))
    config.refresh_ms = 100
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while time.time() < deadline and len(made.get("display", Slow(None)).parts) < 3:
        time.sleep(0.05)
    stop.set()
    thread.join(5)
    parts = made["display"].parts
    assert parts[0] == (0, 0, 480, 320)  # the whole picture first
    assert all(isinstance(part, tuple) and len(part) == 4 for part in parts)
    assert any((x1 - x0) * (y1 - y0) < 480 * 320 // 4 for x0, y0, x1, y1 in parts[1:])

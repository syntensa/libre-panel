"""[device] dark_on_quit: the panel goes dark when Libre Panel quits (a black picture and its
light off, as turing-smart-screen-python turns these panels off), and only then."""

import threading
import time

import pytest

from libre_panel import app
from libre_panel.config import Config, DeviceConfig, SensorsConfig, parse_config
from libre_panel.devices.base import Display


class Panel(Display):
    slow = False

    def __init__(self, config):
        super().__init__(config)
        self.frames, self.levels = [], []

    def show(self, frame, region=None):
        self.frames.append(frame.copy())

    def set_brightness(self, percent):
        self.levels.append(percent)


def run_until_stopped(monkeypatch, dark, quitting, slow=False):
    made = {}

    def create(cfg):
        made["panel"] = Panel(cfg)
        made["panel"].slow = slow
        return made["panel"]

    monkeypatch.setattr(app, "create_display", create)
    config = Config(device=DeviceConfig(driver="turzx", dark_on_quit=dark),
                    sensors=SensorsConfig(providers=["demo"]))  # fmt: skip
    stop = threading.Event()
    options = {"stop": stop, "quitting": lambda: quitting}
    thread = threading.Thread(target=app.run, args=(config,), kwargs=options, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not made.get("panel", Panel(None)).frames:
        time.sleep(0.05)
    stop.set()
    thread.join(5)
    return made["panel"]


def test_the_setting_is_read_and_off_unless_set():
    assert parse_config({}).device.dark_on_quit is False
    assert parse_config({"device": {"dark_on_quit": True}}).device.dark_on_quit is True


def test_quitting_turns_the_panel_dark(monkeypatch):
    panel = run_until_stopped(monkeypatch, dark=True, quitting=True)
    assert panel.levels[-1] == 0  # its light off, after the last picture...
    assert panel.frames[-1].convert("L").getbbox() is None  # ...a black one


@pytest.mark.parametrize(("dark", "quitting"), [(False, True), (True, False)])
def test_the_panel_keeps_its_picture_otherwise(monkeypatch, dark, quitting):
    """The setting off, or the loop stopped for a pause (or a panel test): as it was."""
    panel = run_until_stopped(monkeypatch, dark=dark, quitting=quitting)
    assert 0 not in panel.levels
    assert panel.frames[-1].convert("L").getbbox() is not None


def test_a_slow_panel_only_has_its_light_turned_off(monkeypatch):
    panel = run_until_stopped(monkeypatch, dark=True, quitting=True, slow=True)
    assert panel.levels[-1] == 0
    assert panel.frames[-1].convert("L").getbbox() is not None  # no black picture: too slow

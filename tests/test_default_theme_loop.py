"""The main loop and the panel's own default theme: which theme it starts with, what a reload
or a display that says which panel it is does to it."""

import logging
import os

from PIL import Image
from test_app_cli import MemoryStream, StepClock
from test_theme_defaults import NINE, THREE, nothing_on_the_bus

from libre_panel import app as app_module
from libre_panel.config import Config, DeviceConfig, ModeConfig, SensorsConfig, load_config
from libre_panel.devices import turzx_usb
from libre_panel.devices.virtual import VirtualDisplay
from libre_panel.sensors.base import SensorHub
from libre_panel.sensors.demo import DemoProvider


class Reporting(MemoryStream):
    """A streaming display that says which panel it is, once it has connected."""

    def __init__(self, model=None):
        super().__init__()
        self.model = model


def loop_for(config, display=None, **kwargs):
    clock = StepClock(1000.0)
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    loop = app_module.MainLoop(
        config, hub=hub, display=display or Reporting(), clock=clock, **kwargs
    )
    return loop, clock


def demo_config(device=None, **kwargs):
    return Config(
        device=device or DeviceConfig(driver="virtual"),
        sensors=SensorsConfig(providers=["demo"]),
        fps=5,
        **kwargs,
    )


def step_until(loop, clock, condition, steps=200):
    """Frames until ``condition()`` (a switch is warmed up in a thread first)."""
    import time

    for _ in range(steps):
        clock.now += 0.02
        loop.step(clock.now)
        if condition():
            return True
        time.sleep(0.002)
    return False


def test_the_loop_shows_lumen_on_a_configured_bar_and_libre_default_elsewhere(isolated_home):
    for model, theme, size in (
        ("turing-9.2-usb", "lumen", (1920, 480)),
        ("turing-8.8-usb", "lumen", (1920, 480)),
        ("turing-3.5", "libre-default", (480, 320)),
        ("turing-12.3-usb", "libre-default", (1920, 720)),
    ):
        loop, clock = loop_for(demo_config(DeviceConfig(model=model, driver="virtual")))
        try:
            assert loop.status.theme == theme and loop.status.default_theme == theme, model
            assert loop.size == size
            out = loop.step(clock.now)
            assert out is not None and out.size == size, model
        finally:
            loop.close()


def test_the_loop_asks_the_usb_bus_for_an_automatic_model(monkeypatch, isolated_home):
    monkeypatch.setattr(turzx_usb, "present_model", lambda: NINE)
    loop, _ = loop_for(demo_config(DeviceConfig()))  # driver "auto", model "auto"
    try:
        assert loop.status.theme == "lumen" and loop.status.default_theme == "lumen"
    finally:
        loop.close()
    monkeypatch.setattr(turzx_usb, "present_model", lambda: None)
    loop, _ = loop_for(demo_config(DeviceConfig()))
    try:
        assert loop.status.theme == "libre-default"  # no panel known yet
    finally:
        loop.close()


def renderers_built(monkeypatch):
    """The folders of the themes that get a renderer, in the order they are built (the first
    theme is drawn ahead in a thread of its own, a switch draws the next one the same way)."""
    built = []
    real = app_module.Renderer

    class Recording(real):
        def __init__(self, theme, *args, **kwargs):
            built.append(theme.root.name)
            super().__init__(theme, *args, **kwargs)

    monkeypatch.setattr(app_module, "Renderer", Recording)
    return built


def test_the_theme_drawn_ahead_is_the_one_worked_out_for_the_panel(monkeypatch, isolated_home):
    """The first theme is made ready before the first frame: on a bar it is Lumen, whether the
    config names the panel or the USB bus says which it is, and no other is built to be switched
    away from."""
    built = renderers_built(monkeypatch)
    monkeypatch.setattr(turzx_usb, "present_model", lambda: NINE)
    for device in (
        DeviceConfig(model="turing-9.2-usb", driver="virtual"),  # named in config.toml
        DeviceConfig(),  # found on the bus
    ):
        built.clear()
        loop, clock = loop_for(demo_config(device))
        try:
            assert built == ["lumen"] and loop.warmup.key is None  # drawn, and taken
            assert loop.renderer.theme.root.name == "lumen" and loop.shown == "lumen"
            for _ in range(10):
                clock.now += 0.02
                loop.step(clock.now)
            assert built == ["lumen"] and loop.status.theme == "lumen"  # nothing switched
        finally:
            loop.close()


def test_a_display_that_is_open_already_says_which_panel_it_is(monkeypatch, isolated_home):
    nothing_on_the_bus(monkeypatch)
    loop, _ = loop_for(demo_config(), Reporting(NINE))
    try:
        assert loop.status.theme == "lumen" and loop.status.default_theme == "lumen"
    finally:
        loop.close()


def test_the_bus_is_asked_once_for_a_changed_driver(monkeypatch, isolated_home):
    """A switch looks at the new config at every frame while its theme is warmed up: the
    panel the change asks for is looked for once, not at each of those frames."""
    asked = []
    monkeypatch.setattr(turzx_usb, "present_model", lambda: asked.append("bus") or NINE)
    loop, _ = loop_for(demo_config(DeviceConfig(driver="virtual")))
    try:
        fresh = demo_config(DeviceConfig(driver="auto"))
        assert [loop._default_for(fresh) for _ in range(30)] == ["lumen"] * 30
        assert asked == ["bus"]
        same = demo_config(DeviceConfig(driver="virtual", brightness=10))
        assert loop._default_for(same) == "libre-default" and asked == ["bus"]  # nothing asked
    finally:
        loop.close()


def test_a_driver_without_a_panel_never_asks_the_bus(monkeypatch, isolated_home):
    nothing_on_the_bus(monkeypatch)
    loop, _ = loop_for(demo_config(DeviceConfig(driver="virtual")))
    try:
        assert loop.status.theme == "libre-default"
    finally:
        loop.close()


def test_a_theme_that_is_named_wins_over_the_panels_default(tmp_path, isolated_home):
    bar = DeviceConfig(model="turing-9.2-usb", driver="virtual")
    loop, _ = loop_for(demo_config(bar, theme="slate"))  # a config made in code
    try:
        assert loop.status.theme == "slate" and loop.status.default_theme == "lumen"
    finally:
        loop.close()
    isolated_home.mkdir(parents=True)
    path = isolated_home / "config.toml"
    # what 0.4 wrote: libre-default, named, on a panel whose default is another
    path.write_text(
        'theme = "libre-default"\n[device]\nmodel = "turing-9.2-usb"\ndriver = "virtual"\n'
        '[sensors]\nproviders = ["demo"]\n',
        encoding="utf-8",
    )
    loop, _ = loop_for(load_config(path))
    try:
        assert loop.status.theme == "libre-default" and loop.status.default_theme == "lumen"
    finally:
        loop.close()


def test_a_single_frame_shows_the_panels_default_too(tmp_path, isolated_home):
    for model, size in (("turing-9.2-usb", (1920, 480)), ("turing-3.5", (480, 320))):
        out = tmp_path / f"{model}.png"
        device = DeviceConfig(model=model, driver="virtual", output=str(out))
        app_module.run(demo_config(device), once=True)
        with Image.open(out) as frame:
            assert frame.size == size


def test_the_default_is_the_last_thing_the_loop_asks_for_a_theme(isolated_home):
    """A service's theme, then the mode's, then config.toml's, then the panel's own."""
    from libre_panel.plugins import PluginHost

    mode = {"game": ModeConfig(fps=10), "party": ModeConfig(theme="orbit")}
    config = demo_config(modes=mode)
    assert app_module._theme_name(config, None) == "libre-default"  # (the fallback alone)
    assert app_module._theme_name(config, None, "lumen") == "lumen"
    assert app_module._theme_name(demo_config(theme="slate"), None, "lumen") == "slate"
    host = PluginHost()
    try:
        assert app_module._theme_name(config, host, "lumen") == "lumen"
        host.set_mode("game")  # a mode that names no theme shows the normal one's
        assert app_module._theme_name(config, host, "lumen") == "lumen"
        host.set_mode("party")
        assert app_module._theme_name(config, host, "lumen") == "orbit"
        host.request_theme("pico", by="test", priority=1)
        assert app_module._theme_name(config, host, "lumen") == "pico"
    finally:
        host.close()


def test_the_default_follows_a_reload_of_config_toml(isolated_home):
    isolated_home.mkdir(parents=True)
    path = isolated_home / "config.toml"

    def save(model, theme_line, minute):
        path.write_text(
            f'fps = 5\n{theme_line}[sensors]\nproviders = ["demo"]\n[device]\n'
            f'driver = "virtual"\nmodel = "{model}"\n',
            encoding="utf-8",
        )
        os.utime(path, (1_700_000_000 + minute, 1_700_000_000 + minute))  # a stamp of its own

    save("turing-9.2-usb", "", 0)
    loop, clock = loop_for(load_config(path))
    try:
        assert loop.status.theme == "lumen"
        save("turing-5", "", 1)  # another panel: its default
        assert step_until(loop, clock, lambda: loop.status.theme == "libre-default")
        assert loop.status.default_theme == "libre-default" and loop.size == (800, 480)
        save("turing-9.2-usb", "", 2)  # and back
        assert step_until(loop, clock, lambda: loop.status.theme == "lumen")
        save("turing-9.2-usb", 'theme = "orbit"\n', 3)  # a theme is named
        assert step_until(loop, clock, lambda: loop.status.theme == "orbit")
        assert loop.status.default_theme == "lumen"  # still what the panel would show
        save("turing-5", "", 4)  # the line taken out, on another panel
        assert step_until(loop, clock, lambda: loop.status.theme == "libre-default")
    finally:
        loop.close()


def test_a_display_that_reports_another_panel_switches_once(caplog, isolated_home):
    caplog.set_level(logging.INFO, logger="libre_panel.app")
    display = Reporting()  # not known yet: a bar that connects later, a serial panel
    loop, clock = loop_for(demo_config(), display)

    def shown():
        messages = (record.getMessage() for record in caplog.records)
        return [message for message in messages if message.startswith("showing theme")]

    try:
        for _ in range(5):
            clock.now += 0.02
            loop.step(clock.now)
        assert loop.status.theme == "libre-default" and shown() == []
        display.model = NINE  # it connected, and says which panel it is
        assert step_until(loop, clock, lambda: loop.status.theme == "lumen")
        assert loop.status.default_theme == "lumen"
        assert shown() == ["showing theme 'lumen'"]
        for _ in range(20):  # the same report again, and the panel unplugged: nothing more
            clock.now += 0.02
            loop.step(clock.now)
        display.model = None
        for _ in range(20):
            clock.now += 0.02
            loop.step(clock.now)
        assert loop.status.theme == "lumen" and len(shown()) == 1
        display.model = THREE  # a different panel plugged in: its default
        assert step_until(loop, clock, lambda: loop.status.theme == "libre-default")
        assert len(shown()) == 2
    finally:
        loop.close()


def test_a_reported_panel_changes_nothing_where_the_theme_or_the_model_is_named(isolated_home):
    display = Reporting()
    loop, clock = loop_for(demo_config(theme="slate"), display)  # a theme is named
    try:
        display.model = NINE
        for _ in range(60):
            clock.now += 0.02
            loop.step(clock.now)
        assert loop.status.theme == "slate" and loop.status.default_theme == "lumen"
        assert loop.shown == "slate"
    finally:
        loop.close()
    display = Reporting()
    configured = DeviceConfig(model="turing-5", driver="virtual")  # the model is named
    loop, clock = loop_for(demo_config(configured), display)
    try:
        display.model = NINE
        for _ in range(60):
            clock.now += 0.02
            loop.step(clock.now)
        assert loop.status.theme == "libre-default" and loop.status.default_theme == "libre-default"
    finally:
        loop.close()


def test_the_automatic_driver_reports_the_panel_it_found(monkeypatch, isolated_home):
    from libre_panel.devices.auto import AutoDisplay

    class Found(MemoryStream):
        model = NINE

    display = AutoDisplay(DeviceConfig())
    assert display.model is None  # nothing open
    display._impl = Found()
    assert display.model is NINE and display.using_panel
    display._impl = VirtualDisplay(DeviceConfig(output=""))
    assert display.model is None  # frames go to a file
    assert VirtualDisplay(DeviceConfig(output="")).model is None

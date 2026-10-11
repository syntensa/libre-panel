import re

import pytest
from PIL import Image, ImageChops

from libre_panel import app as app_module
from libre_panel.app import fit_frame, run, target_size
from libre_panel.cli import main
from libre_panel.config import Config, DeviceConfig, SensorsConfig
from libre_panel.devices.base import Display
from libre_panel.theme.model import find_theme, load_theme


def test_run_once_with_virtual_display(tmp_path):
    out = tmp_path / "frame.png"
    config = Config(
        device=DeviceConfig(driver="virtual", output=str(out)),
        sensors=SensorsConfig(providers=["demo"]),
    )
    run(config, once=True)
    with Image.open(out) as frame:
        assert frame.size == (480, 320)


def test_theme_is_fitted_to_a_different_panel():
    theme = load_theme(find_theme("libre-default"))
    config = Config(device=DeviceConfig(model="turing-9.2-usb"))
    size = target_size(config, theme)
    assert size == (1920, 480)
    frame = fit_frame(Image.new("RGB", (480, 320)), size, "#000000")
    assert frame.size == (1920, 480)


def test_a_theme_with_a_palette_background_fits_another_panel(tmp_path):
    # libre-default's background is "@bg", a palette name (issue #3: it crashed)
    out = tmp_path / "frame.png"
    config = Config(
        device=DeviceConfig(model="turing-5", driver="virtual", output=str(out)),
        sensors=SensorsConfig(providers=["demo"]),
    )
    theme = load_theme(find_theme(config.theme))
    assert theme.background_color.startswith("@")
    run(config, once=True)
    with Image.open(out) as frame:
        assert frame.size == (800, 480)
        bg = theme.palette[theme.background_color[1:]].lstrip("#")
        assert frame.convert("RGB").getpixel((2, 240)) == tuple(bytes.fromhex(bg[:6]))


def static_theme():
    """A theme that draws the same frame every time, and not symmetrically."""
    from libre_panel.theme.model import save_theme

    save_theme("still", {
        "format": "libre-panel-theme/1", "name": "still",
        "display": {"width": 480, "height": 320}, "background": {"color": "#102030"},
        "widgets": [
            {"type": "rect", "id": "a", "x": 10, "y": 10, "w": 120, "h": 40, "color": "#ff8800"},
            {"type": "text", "id": "b", "x": 200, "y": 200, "text": "Libre", "color": "#ffffff"},
        ],
    })  # fmt: skip
    return "still"


class RecordingPanel(Display):
    """A panel that keeps what it was sent."""

    opened = 0

    def __init__(self, config):
        super().__init__(config)
        self.shown = []

    def open(self):
        RecordingPanel.opened += 1

    def show(self, frame, region=None):
        self.shown.append((frame.copy(), region))


def test_an_upside_down_panel_gets_the_frame_turned(tmp_path, isolated_home, monkeypatch):
    panels = []
    monkeypatch.setattr(app_module, "create_display", lambda device: panels.append(
        RecordingPanel(device)) or panels[-1])  # fmt: skip
    for rotate in (0, 180):
        config = Config(theme=static_theme(), device=DeviceConfig(rotate=rotate),
                        sensors=SensorsConfig(providers=["demo"]))  # fmt: skip
        run(config, once=True)
    upright, turned = (panel.shown[0][0] for panel in panels)
    assert ImageChops.difference(upright, turned).getbbox() is not None
    assert ImageChops.difference(upright.rotate(180), turned).getbbox() is None


def test_the_png_file_is_never_turned(tmp_path, isolated_home):
    frames = []
    for rotate in (0, 180):
        out = tmp_path / f"frame-{rotate}.png"
        config = Config(
            theme=static_theme(),
            device=DeviceConfig(driver="virtual", output=str(out), rotate=rotate),
            sensors=SensorsConfig(providers=["demo"]),
        )
        run(config, once=True)
        with Image.open(out) as frame:
            frames.append(frame.convert("RGB"))
    assert ImageChops.difference(*frames).getbbox() is None  # a picture, not a mounted panel


def test_turning_the_panel_follows_the_config_without_reopening_it(
    tmp_path, isolated_home, monkeypatch
):
    import threading
    import time

    from libre_panel.config import load_config, set_config_value, write_default_config

    path = write_default_config()
    path.write_text(re.sub(r"(?m)^providers = .*$", 'providers = ["demo"]', path.read_text()))
    set_config_value("theme", static_theme(), path)
    panels = []
    monkeypatch.setattr(app_module, "create_display", lambda device: panels.append(
        RecordingPanel(device)) or panels[-1])  # fmt: skip
    RecordingPanel.opened = 0
    config = load_config(path)
    config.refresh_ms = 100
    stop = threading.Event()
    thread = threading.Thread(target=run, args=(config,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 30  # slow CI machines
        while not (panels and panels[0].shown) and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.05)  # make sure the next write gets a new mtime
        shown = panels[0].shown
        before = len(shown)
        set_config_value("device.rotate", 180, path)
        deadline = time.time() + 30
        while len(shown) == before and time.time() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(5)
    first, after = shown[0][0], shown[before][0]
    assert shown[before][1] == (0, 0, 480, 320)  # all of it, turned
    assert ImageChops.difference(first.rotate(180), after).getbbox() is None
    assert len(panels) == 1 and RecordingPanel.opened == 1  # the panel stayed open


def test_a_broken_sensor_source_does_not_stop_the_panel(tmp_path, caplog):
    out = tmp_path / "frame.png"
    config = Config(
        device=DeviceConfig(driver="virtual", output=str(out)),
        sensors=SensorsConfig(
            providers=["demo", "lhm", "calendar"],  # a typo, and a calendar set up wrong
            options={"calendar": {"days": "two weeks"}},
        ),
    )
    run(config, once=True)
    assert out.exists()
    assert "'lhm' not started" in caplog.text and "'calendar' not started" in caplog.text


def test_cli_preview_and_models(tmp_path, capsys):
    out = tmp_path / "p.png"
    assert main(["preview", "spur-ii", "-o", str(out)]) == 0
    assert out.exists()
    assert main(["models"]) == 0
    assert "turing-9.2-usb" in capsys.readouterr().out


def test_a_signal_shows_where_the_threads_are(monkeypatch):
    import faulthandler
    import signal

    if not hasattr(signal, "SIGUSR1"):
        pytest.skip("no SIGUSR1 on this system")
    registered = []
    monkeypatch.setattr(faulthandler, "register", lambda *a, **kw: registered.append((a, kw)))
    assert main(["models"]) == 0
    assert registered == [((signal.SIGUSR1,), {"all_threads": True})]


def test_cli_reports_errors(capsys):
    assert main(["preview", "does-not-exist"]) == 2
    assert "not found" in capsys.readouterr().err


def test_config_init(isolated_home):
    assert main(["config", "init"]) == 0
    assert (isolated_home / "config.toml").exists()
    assert main(["config", "init"]) == 1


def test_set_active_theme_keeps_comments(isolated_home):
    from libre_panel.config import load_config, set_active_theme, write_default_config

    path = write_default_config()
    set_active_theme("spur-ii")
    text = path.read_text()
    assert 'theme = "spur-ii"' in text and "# Libre Panel configuration" in text
    assert load_config(path).theme == "spur-ii"
    assert [line for line in text.splitlines() if line.startswith("theme =")] == [
        'theme = "spur-ii"'
    ]  # one, under the note that explains it


def test_running_panel_picks_up_theme_changes(tmp_path, isolated_home):
    import threading
    import time

    from libre_panel.config import load_config, set_active_theme, write_default_config

    path = write_default_config()
    out = tmp_path / "frame.png"
    text = re.sub(r"(?m)^providers = .*$", 'providers = ["demo"]', path.read_text())
    path.write_text(
        text.replace('output = "libre-panel-frame.png"', f'output = "{out.as_posix()}"')
    )
    config = load_config(path)
    config.refresh_ms = 100
    stop = threading.Event()
    thread = threading.Thread(target=run, args=(config,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not out.exists() and time.time() < deadline:
            time.sleep(0.05)
        with Image.open(out) as first:
            assert first.size == (480, 320)
        time.sleep(0.05)  # make sure the next write gets a new mtime
        set_active_theme("spur-ii")
        while time.time() < deadline:
            with Image.open(out) as frame:
                if frame.size == (1920, 480):
                    break
            time.sleep(0.05)
        with Image.open(out) as frame:
            assert frame.size == (1920, 480)
    finally:
        stop.set()
        thread.join(5)


def test_auto_driver_falls_back_to_png(tmp_path):
    from libre_panel.devices.base import create_display

    out = tmp_path / "auto.png"
    display = create_display(DeviceConfig(driver="auto", output=str(out)))
    display.open()
    display.show(Image.new("RGB", (32, 16)))
    display.close()
    assert out.exists()


class StepClock:
    """The main loop's clock, set by the test."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class MemoryStream(Display):
    """A streaming display in memory: every frame is kept."""

    streaming = True
    stream_fps = 50

    def __init__(self, config=None):
        super().__init__(config or DeviceConfig())
        self.frames = []

    def show(self, frame, region=None):
        self.frames.append((frame, region))


def test_the_main_loop_goes_one_frame_at_a_time_on_its_own_clock():
    """MainLoop.step draws one frame on the clock it is given: readings come by
    that clock, each frame reaches the streaming display, a requested theme
    shows once it is warmed up."""
    import time

    from libre_panel.plugins import PluginHost
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    clock = StepClock(1000.0)
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    taken = []
    real_snapshot = hub.snapshot
    hub.snapshot = lambda: taken.append(clock.now) or real_snapshot()
    display, host = MemoryStream(), PluginHost()
    config = Config(theme="slate", sensors=SensorsConfig(providers=["demo"]))
    loop = app_module.MainLoop(config, host=host, hub=hub, display=display, clock=clock)
    try:
        for n in range(100):  # two seconds at 50 fps
            clock.now = 1000.0 + n / 50
            out = loop.step(clock.now)
            assert out is not None and out.size == (800, 480)
        assert loop.streaming and loop.fps == 50
        assert len(display.frames) == 100 and all(region is None for _, region in display.frames)
        assert taken == [1000.0, 1001.0]  # slate reads its sensors once a second
        host.request_theme("libre-default", by="test", transition="cut")
        for _ in range(150):  # it is warmed up first (at most 2 s) while slate goes on
            clock.now += 1 / 50
            out = loop.step(clock.now)
            if loop.status.theme == "libre-default":
                break
            assert out.size == (800, 480)
            time.sleep(0.002)
        assert loop.status.theme == "libre-default" and out.size == (480, 320)
    finally:
        loop.close()
        host.close()


def test_services_follow_config_toml_although_its_theme_does_not_load(isolated_home):
    """A change of ``[services]`` saved together with a theme that is not there: the file
    loaded, so the services follow it now, and not only with the next save. A file that does
    not load at all changes nothing."""
    import os
    import time

    from libre_panel.config import load_config
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider

    path = isolated_home / "config.toml"
    isolated_home.mkdir(parents=True, exist_ok=True)

    def save(text, minute):
        path.write_text(text, encoding="utf-8")
        os.utime(path, (1_700_000_000 + minute, 1_700_000_000 + minute))  # a stamp of its own

    save('theme = "pico"\nfps = 5\n', 0)
    clock, heard = StepClock(1000.0), []
    config = load_config(path)
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    loop = app_module.MainLoop(
        config, hub=hub, display=MemoryStream(), clock=clock, on_config=heard.append
    )

    def step(times=1):
        for _ in range(times):
            clock.now += 0.02
            loop.step(clock.now)

    try:
        step(3)
        assert heard == [] and loop.status.theme == "pico"  # the first load is the caller's
        save('theme = "pico"\n[services]\nenabled = ["notices"\n', 1)  # broken: no list closes
        step(3)
        assert heard == [] and loop.status.theme == "pico"  # nothing to follow yet
        save('theme = "no-such-theme"\n[services]\nenabled = ["notices"]\n', 2)
        step(3)
        assert len(heard) == 1 and heard[0].services.enabled == ["notices"]
        assert loop.status.theme == "pico" and loop.refused == "no-such-theme"  # kept
        step(5)
        assert len(heard) == 1  # told once, not at every frame
        save('theme = "no-such-theme"\n[services]\nenabled = []\n', 3)  # and the next save
        step(3)
        assert [c.services.enabled for c in heard] == [["notices"], []]
        save('theme = "slate"\n[services]\nenabled = []\n', 4)  # the theme fixed
        for _ in range(150):
            step()
            if loop.status.theme == "slate":
                break
            time.sleep(0.002)  # the next theme is warmed up in a thread first
        assert loop.status.theme == "slate" and len(heard) == 3
    finally:
        loop.close()


def test_the_services_of_the_app_follow_a_config_whose_theme_is_not_there(isolated_home):
    """Through the background app: one write switches a service on and names a theme that does
    not exist. The panel keeps its theme, the service starts."""
    from test_autostart import FakeTasks
    from test_service import FakeRegistry, wait_for, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.config import edit_config
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    app = BackgroundApp(
        port=0, autostart=Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    )
    app.start()
    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        assert app.services.running == {}
        edit_config(("value", "theme", "no-such-theme"), ("value", "services.enabled", ["notices"]))
        assert wait_for(lambda: "notices" in app.services.running, 15)
        assert app.panel.state()["theme"] == "libre-default"  # the panel is as it was
    finally:
        app.quit()
        app.shutdown()


def test_one_clock_for_the_loop_the_renderer_and_plugins():
    """``now`` everywhere is on libre_panel.clock() (time.perf_counter), and
    snapshot.now is the wall time of the frame as planned: a seconds clock
    ticks on the frame that is due, not on the one that happens to be drawn."""
    import time
    from datetime import datetime, timedelta

    import libre_panel
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.base import SensorHub, Snapshot
    from libre_panel.sensors.demo import DemoProvider

    before = time.perf_counter()
    assert before <= libre_panel.clock() <= time.perf_counter()
    assert app_module._Pacer().clock is libre_panel.clock
    renderer = Renderer(load_theme(find_theme("pico")))
    renderer.render(Snapshot())
    assert before <= renderer.motion.now <= time.perf_counter()

    clock = StepClock(1000.5)  # drawn half a second after it was due
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    config = Config(theme="pico", sensors=SensorsConfig(providers=["demo"]))
    loop = app_module.MainLoop(config, hub=hub, display=MemoryStream(), clock=clock)
    try:
        assert loop.clock is clock
        loop.step(1000.0)
        due = datetime.now() - timedelta(seconds=0.5)
        assert abs((loop.snapshot.now - due).total_seconds()) < 0.2
    finally:
        loop.close()

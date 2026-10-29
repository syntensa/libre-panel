"""A panel as a monitor (mirror.py): what the app asks for, on which panels (a panel's own
process told as well), one or the other of the Player and the screen, the panel's own monitor
on the desktop while it shows and off after; the newest picture of a capture, fitted; and the
panel's loop drawing it."""

import shutil
import threading
import time

import pytest
from PIL import Image
from test_capture import xvfb  # noqa: F401  (Xvfb for a test of the app on X11)

from libre_panel import app, capture, extend, mirror, player
from libre_panel.config import Config, SensorsConfig
from libre_panel.devices.base import Display

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")


@pytest.fixture(autouse=True)
def nothing_shown():
    mirror.stop()
    mirror.adopt(None)
    yield
    mirror.stop()
    mirror.adopt(None)


def test_a_source_from_the_apps_json():
    raw = {"kind": "monitor", "id": r"\\.\DISPLAY2", "name": "Display 2",
           "region": [0, 0, 1920, 720], "fit": "cover", "cursor": False}  # fmt: skip
    found = mirror.source_of(raw)
    assert found == mirror.Source("monitor", r"\\.\DISPLAY2", "Display 2", (0, 0, 1920, 720),
                                  "cover", False)  # fmt: skip
    assert mirror.source_of({"kind": "virtual"}).fit == "contain"  # (its own: no id)
    for wrong in ({"kind": "tv"}, {"kind": "monitor"}, {"kind": "window", "id": 5},
                  {"kind": "monitor", "id": "a", "fit": "zoom"},
                  {"kind": "monitor", "id": "a", "region": [0, 0, 5, 5]}, "monitor"):  # fmt: skip
        with pytest.raises(mirror.MirrorError):
            mirror.source_of(wrong)


def test_shown_on_its_panels_and_told_to_a_panels_own_process(monkeypatch):
    source = mirror.Source("window", "0x1a", "Discord")
    mirror.show(source, [2, 1, 2])
    assert mirror.now(1) == source and mirror.now(2) == source and mirror.now(3) is None
    assert mirror.status()["panels"] == [1, 2]
    told = mirror.export()
    mirror.stop()
    assert mirror.now(1) is None and mirror.export() is None
    mirror.adopt(told)  # (a panel's own process: what the first one said)
    assert mirror.now(2) == source
    mirror.adopt({"nonsense": True})
    assert mirror.now(2) is None
    with pytest.raises(mirror.MirrorError):
        mirror.show(source, [])


def test_the_player_or_the_screen_on_a_panel(monkeypatch):
    stopped = []
    monkeypatch.setattr(player, "stop", lambda: stopped.append("player"))
    mirror.show(mirror.Source("monitor", "DP-1"), [1])
    assert stopped == ["player"]  # the screen: the Player stops
    monkeypatch.setattr(mirror, "stop", lambda: stopped.append("mirror"))
    monkeypatch.setattr(player, "info", lambda path: None)  # (nothing to play: an error)
    with pytest.raises(player.PlayerError):
        player.play(["clip.mp4"], [1])
    assert stopped == ["player"]  # (nothing played: the screen stays)


def test_the_panels_own_monitor_goes_on_the_desktop_and_off_again(monkeypatch):
    done = []
    monkeypatch.setattr(capture, "platform", lambda: "x11")
    monkeypatch.setattr(extend, "attach", lambda size, where=None: done.append(("on", size)))
    monkeypatch.setattr(extend, "detach", lambda where=None: done.append(("off",)))
    monkeypatch.setattr(mirror, "_detach_on_exit", lambda: None)
    with pytest.raises(mirror.MirrorError, match="size"):
        mirror.show(mirror.Source("virtual"), [1])
    mirror.show(mirror.Source("virtual"), [1], size=(1920, 720))
    mirror.show(mirror.Source("monitor", "DP-1"), [1])  # another: its own goes off
    mirror.show(mirror.Source("virtual"), [1], size=(1920, 720))
    mirror.stop()
    assert done == [("on", (1920, 720)), ("off",), ("on", (1920, 720)), ("off",)]

    def refused(size, where=None):
        raise extend.ExtendError("the Virtual Display Driver is not set up")

    monkeypatch.setattr(extend, "attach", refused)
    with pytest.raises(mirror.MirrorError, match="not set up"):
        mirror.show(mirror.Source("virtual"), [1], size=(1920, 720))
    assert mirror.now(1) is None
    monkeypatch.setattr(capture, "platform", lambda: "wayland")  # (the capture makes it there)
    mirror.show(mirror.Source("virtual"), [1])
    assert mirror.now(1).kind == "virtual"


def lavfi(colour="red", size="320x180"):
    return capture.Capture(("-re", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:r=25"))


@needs_ffmpeg
def test_the_newest_picture_fitted_into_the_panels_frame():
    command = mirror.frames_command(FFMPEG, lavfi("red", "320x180"), (200, 200), "contain", 25)
    assert command[command.index("-vf") + 1].startswith("scale=200:200:force_original_aspect")
    feed = mirror.CaptureFeed(command, (200, 200))
    try:
        end = time.monotonic() + 20
        first, picture = feed.frame()
        while picture is None and time.monotonic() < end:
            time.sleep(0.05)
            first, picture = feed.frame()
        assert picture.size == (200, 200)
        assert picture.getpixel((100, 100))[0] > 200  # the screen, whole ...
        assert picture.getpixel((100, 5)) == (0, 0, 0)  # ... on black above and below
        time.sleep(0.3)
        assert feed.frame()[0] > first  # (new pictures as they come)
    finally:
        feed.close()


@needs_ffmpeg
def test_a_panels_screen_draws_what_the_capture_gives(monkeypatch):
    monkeypatch.setattr(mirror, "capture_of", lambda ffmpeg, source, fps, size=None: lavfi("blue"))
    screen = mirror.Screen()
    source = mirror.Source("monitor", "DP-1", fit="stretch")
    try:
        end = time.monotonic() + 20
        frame = screen.frame(source, (64, 32), 25)
        while frame.getpixel((32, 16))[2] < 200 and time.monotonic() < end:
            time.sleep(0.05)
            frame = screen.frame(source, (64, 32), 25)
        assert frame.size == (64, 32) and frame.getpixel((0, 0))[2] > 200
        # on Wayland the panel's stream shows it straight: the app's picture keeps the last one
        monkeypatch.setattr(capture, "platform", lambda: "wayland")
        kept = screen.frame(source, (64, 32), 4, still=True)
        assert kept.getpixel((0, 0))[2] > 200 and screen._feed is None
    finally:
        screen.close()

    def refused(ffmpeg, source, fps, size=None):
        raise mirror.MirrorError("the window Discord is gone")

    monkeypatch.setattr(capture, "platform", lambda: "x11")
    monkeypatch.setattr(mirror, "capture_of", refused)
    gone = mirror.Screen()
    assert gone.frame(source, (64, 32), 25).getpixel((0, 0)) == (0, 0, 0)  # (black)
    assert gone.error == "the window Discord is gone"


class FramesDisplay(Display):
    """A panel that takes pictures (not a video stream)."""

    def __init__(self, config):
        super().__init__(config)
        self.shown = []

    def show(self, frame, region=None):
        self.shown.append(frame.copy())


@needs_ffmpeg
def test_the_main_loop_draws_the_screen_on_a_panel_of_pictures(monkeypatch, isolated_home):
    displays = []
    monkeypatch.setattr(app, "create_display", lambda config: displays.append(
        FramesDisplay(config)) or displays[-1])  # fmt: skip
    monkeypatch.setattr(mirror, "capture_of", lambda ffmpeg, source, fps, size=None: lavfi("lime"))
    mirror.show(mirror.Source("monitor", "DP-1", "Display 1", fit="stretch"), [1])
    config = Config(theme="pico", fps=10, sensors=SensorsConfig(providers=["demo"]))
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    try:
        thread.start()
        end = time.monotonic() + 60

        def green() -> bool:
            frames = displays[0].shown if displays else []
            return bool(frames) and frames[-1].convert("RGB").getpixel((5, 5))[1] > 200

        while time.monotonic() < end and not green():
            time.sleep(0.05)
        assert green()
        mirror.stop()  # the theme again
        end = time.monotonic() + 30
        while time.monotonic() < end and green():
            time.sleep(0.05)
        assert not green()
    finally:
        stop.set()
        thread.join(10)
    assert isinstance(displays[0].shown[0], Image.Image)


# -- the app's routes, and the app ---------------------------------------------------------


@pytest.fixture
def served(isolated_home):
    """The app's server, with a background app (``True``) or as a plain editor."""
    from types import SimpleNamespace

    from test_editor_server import request

    from libre_panel.editor.server import make_server

    servers = []

    def start(running=True):
        controls = SimpleNamespace(panel=SimpleNamespace(status=None)) if running else None
        server = make_server(port=0, controls=controls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        port = server.server_address[1]
        return lambda method, path, body=None, **k: request(port, method, path, body, **k)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


def test_the_app_lists_what_a_panel_can_show_and_shows_it(served, monkeypatch):
    monkeypatch.setattr(capture, "platform", lambda: "x11")
    monkeypatch.setattr(capture, "monitors", lambda where=None: [
        capture.Monitor("DP-1", "DP-1", 0, 0, 2560, 1440, True)])  # fmt: skip
    monkeypatch.setattr(capture, "windows", lambda where=None: [
        capture.Window("0x400007", "Discord", "discord")])  # fmt: skip
    ask = served()
    status, data = ask("GET", "/api/mirror")
    assert status == 200 and data["platform"] == "x11" and data["showing"] is None
    assert data["monitors"][0]["id"] == "DP-1" and data["windows"] is None  # (listed when asked)
    assert data["fits"] == ["contain", "cover", "stretch"] and data["extend"]["how"] == "xrandr"
    status, data = ask("GET", "/api/mirror?windows=1")
    assert data["windows"] == [{"id": "0x400007", "title": "Discord", "program": "discord"}]
    status, data = ask("POST", "/api/mirror", {"action": "show", "panels": [1], "source": {
        "kind": "window", "id": "0x400007", "name": "Discord", "fit": "cover"}})  # fmt: skip
    assert status == 200 and data["showing"]["source"]["name"] == "Discord"
    assert mirror.now(1).fit == "cover"
    status, data = ask("POST", "/api/mirror", {"action": "stop"})
    assert status == 200 and data["showing"] is None and mirror.now(1) is None
    for wrong in ({"action": "show", "source": {"kind": "tv"}}, {"action": "dance"},
                  {"action": "show", "source": {"kind": "virtual"}, "panels": "all"}):  # fmt: skip
        assert ask("POST", "/api/mirror", wrong)[0] == 400
    plain = served(running=False)
    status, data = plain("POST", "/api/mirror", {"action": "show", "source": {"kind": "virtual"}})
    assert status == 409  # (a panel shows it in the running app)


def test_the_panels_own_monitor_needs_the_panels_size(served, monkeypatch):
    monkeypatch.setattr(capture, "platform", lambda: "x11")
    shown = []
    monkeypatch.setattr(extend, "attach", lambda size, where=None: shown.append(size))
    monkeypatch.setattr(extend, "detach", lambda where=None: shown.append(None))
    monkeypatch.setattr(mirror, "_detach_on_exit", lambda: None)
    ask = served()
    from libre_panel.config import set_config_value

    set_config_value("device.model", "turing-12.3-usb")
    status, data = ask("GET", "/api/mirror")
    assert data["size"] == [1920, 720]
    status, data = ask("POST", "/api/mirror", {"action": "show", "source": {"kind": "virtual"}})
    assert status == 200 and shown == [(1920, 720)]  # its own monitor at the panel's size
    ask("POST", "/api/mirror", {"action": "stop"})
    assert shown == [(1920, 720), None]


needs_x11 = pytest.mark.skipif(
    not (shutil.which("Xvfb") and shutil.which("xrandr") and shutil.which("xmessage") and FFMPEG),
    reason="Xvfb, xrandr, xmessage and ffmpeg",
)


@needs_x11
def test_the_app_shows_a_monitor_on_the_panel_and_the_theme_again(isolated_home, xvfb):  # noqa: F811
    """Screen → As a monitor, on X11 (Xvfb, a magenta window across its screen): the monitor
    chosen, Show it on the panel: the panel shows the screen; The theme again: the theme."""
    import subprocess

    from test_app_browser import browser_page, settings_app
    from test_service import wait_for

    window = subprocess.Popen(["xmessage", "-bg", "#ff00ff", "-geometry", "1280x720+0+0", " "],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    running = settings_app(isolated_home)
    url = running.start()
    frame = isolated_home / "frame.png"

    def magenta():
        try:
            with Image.open(frame) as shown:  # (left of the middle: xmessage's button is there)
                r, g, b = shown.convert("RGB").getpixel((shown.width // 8, shown.height // 2))
                return r > 200 and b > 200 and g < 60
        except (OSError, ValueError):
            return False

    try:
        assert wait_for(lambda: running.panel.state()["state"] == "showing")
        with browser_page(url, "screen") as page:
            section = page.locator("[data-mirror]")
            section.wait_for()
            section.locator("[data-mirror-source='screen']").click()
            assert not magenta()
            section.locator("[data-mirror-start]").click()
            section.locator("[data-mirror-showing]").wait_for()
            assert wait_for(magenta, timeout=30)  # the screen on the panel
            section.get_by_role("button", name="The theme again").click()
            assert wait_for(lambda: not magenta(), timeout=30)
            assert mirror.status() is None
    finally:
        running.quit()
        running.shutdown()
        window.kill()

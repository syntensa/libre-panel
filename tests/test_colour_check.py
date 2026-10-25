"""Settings → Panel → Check the colours: the test picture, how a stream's patches are measured
and judged, the check through ffmpeg (x264 here; the card's encoders where they run), its route,
the setting it writes and how the running panel follows it, the A/B on a panel and that it
ends as it began, and the card's encoder that took no four bytes a pixel, kept for a week."""

import json
import shutil
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from libre_panel import app, colour_show
from libre_panel.config import (
    Config,
    DeviceConfig,
    SensorsConfig,
    VideoConfig,
    config_dir,
    load_config,
    parse_config,
)
from libre_panel.devices import colour_check, h264, video_state
from libre_panel.devices.base import Display
from libre_panel.devices.colour_check import (
    PATCHES,
    centre,
    check,
    check_picture,
    grid,
    judge,
    layout,
    measure,
    planes,
    to_rgb,
)
from libre_panel.devices.turzx_video import resolved, rotation_of, video_settings
from libre_panel.editor.colours import ColourCheck, ColourError, advice, in_use_of
from libre_panel.theme.model import find_theme, load_theme

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg with libx264")
NINE = "turing-9.2-usb"


@pytest.fixture(autouse=True)
def no_check_showing():
    yield
    colour_show.end()
    colour_show.adopt(None)


def settings_for(video=None, model=NINE):
    base = video_settings(video or VideoConfig())
    return lambda encoder, pixels: resolved(base, model, encoder, pixels)


def shifted(extra, encoder=None):
    """The display's ffmpeg with ``extra`` filters on the new feed (rgb0) alone; ``encoder``
    stands in for x264 (a card's encoder that is not here)."""

    def command(ffmpeg, size, rotate, settings):
        new = settings.pixels == "rgb0" and settings.encoder == (encoder or settings.encoder)
        if encoder is not None and settings.encoder == encoder:
            settings = replace(settings, encoder="libx264")
        found = h264.ffmpeg_command(ffmpeg, size, rotate, settings)
        if new and extra:
            found[found.index("-vf") + 1] += f",{extra}"
        return found

    return command


# -- the picture and the measurement -----------------------------------------------------------


def yuv_of(rgb):
    """BT.601 in limited range, as swscale converts (rounded)."""
    r, g, b = rgb
    y = 16 + (65.481 * r + 128.553 * g + 24.966 * b) / 255
    u = 128 + (-37.797 * r - 74.203 * g + 112.0 * b) / 255
    v = 128 + (112.0 * r - 93.786 * g - 18.214 * b) / 255
    return round(y), round(u), round(v)


def framebuffer(picture, rotate):
    """``picture`` as 4:2:0 planes of the panel's framebuffer, turned as ffmpeg turns it
    (transpose=1: a quarter turn clockwise; hflip,vflip: half a turn), as raw bytes."""
    pixels = np.asarray(picture.convert("RGB"), dtype=np.float64)
    y, u, v = (np.zeros(pixels.shape[:2]) for _ in range(3))
    for rgb in {tuple(c) for c in pixels.reshape(-1, 3).astype(int)}:
        mask = np.all(pixels == rgb, axis=2)
        y[mask], u[mask], v[mask] = yuv_of(rgb)
    turns = {270: -1, 180: 2}[rotate]
    y, u, v = (np.rot90(plane, turns) for plane in (y, u, v))
    u, v = (plane.reshape(plane.shape[0] // 2, 2, plane.shape[1] // 2, 2).mean(axis=(1, 3))
            for plane in (u, v))  # fmt: skip
    data = b"".join(np.round(p).astype(np.uint8).tobytes() for p in (y, u, v))
    return data, (y.shape[1], y.shape[0])


def test_the_test_picture_has_every_patch_large_and_flat():
    for size in ((1920, 480), (480, 1920), (1920, 720), (480, 480), (1280, 720), (960, 320)):
        picture = check_picture(size)
        assert picture.size == size and picture.mode == "RGB"
        cells = layout(size)
        assert [name for name, _, _ in cells] == [name for name, _ in PATCHES]
        for _name, rgb, box in cells:
            x0, y0, x1, y1 = centre(box)
            assert box[0] + 8 <= x0 < x1 <= box[2] - 8 and box[1] + 8 <= y0 < y1 <= box[3] - 8
            assert (x1 - x0) * 2 >= (box[2] - box[0]) - 2  # half the patch, at the least
            middle = picture.crop((x0, y0, x1, y1))
            assert middle.getcolors() == [((x1 - x0) * (y1 - y0), rgb)]  # flat
    assert (
        grid((1920, 480)) == (7, 2) and grid((480, 1920)) == (2, 7) and grid((480, 480)) == (4, 4)
    )
    names = dict(PATCHES)
    assert names["accent"] == (0x2E, 0xE6, 0xD6)  # the themes' accent and violet
    palette = load_theme(find_theme("libre-default")).palette
    for name in ("accent", "violet"):
        assert "#{:02X}{:02X}{:02X}".format(*names[name]) == palette[name].upper()
    lettered = check_picture((1920, 480), "A")
    first = layout((1920, 480))[0][2]
    assert (
        lettered.crop(first).getcolors(1 << 20)
        != check_picture((1920, 480)).crop(first).getcolors()
    )
    assert lettered.crop(layout((1920, 480))[3][2]) == check_picture((1920, 480)).crop(
        layout((1920, 480))[3][2]
    )  # the letter stays in its corner


@pytest.mark.parametrize("size", [(1920, 480), (480, 1920)])
def test_the_patches_are_measured_where_they_are_after_the_turn(size):
    """A framebuffer made by hand from the picture (BT.601, turned as ffmpeg turns it) gives
    back each patch's own colour: the turn is undone, chroma is read where its patch is."""
    rotate = rotation_of(size)
    data, native = framebuffer(check_picture(size), rotate)
    found = measure(planes(data * 2, native), size, rotate)
    for name, rgb in PATCHES:
        assert found[name] == pytest.approx(yuv_of(rgb), abs=0.01), name
        assert max(abs(a - b) for a, b in zip(to_rgb(found[name]), rgb, strict=True)) <= 2
    with pytest.raises(colour_check.CheckError):
        planes(data[:-1], native)  # no whole pictures


def test_two_feeds_are_judged_identical_a_level_shift_a_matrix_shift_or_other():
    limited = {name: yuv_of(rgb) for name, rgb in PATCHES}
    assert judge(limited, limited)["verdict"] == "identical"
    near = {name: (y + 1.5, u - 1, v) for name, (y, u, v) in limited.items()}
    assert judge(near, limited)["verdict"] == "identical"  # within two code values
    full = {name: (round(0.299 * r + 0.587 * g + 0.114 * b), *limited[name][1:])
            for name, (r, g, b) in PATCHES}  # fmt: skip
    level = judge(full, limited)
    assert level["verdict"] == "level"
    assert level["worst"]["channel"] == "Y" and level["worst"]["difference"] >= 16
    bt709 = dict(limited)
    for name in ("red", "green", "blue", "accent", "violet", "orange", "skin"):
        y, u, v = limited[name]
        bt709[name] = (y + 6, u - 4, v + 3)
    matrix = judge(bt709, limited)
    assert matrix["verdict"] == "matrix" and matrix["worst"]["patch"] in dict(PATCHES)
    tinted = dict(limited, **{"grey-128": (126, 135, 128)})
    assert judge(tinted, limited)["verdict"] == "other"
    assert to_rgb((16, 128, 128)) == (0, 0, 0) and to_rgb((235, 128, 128)) == (255, 255, 255)
    assert to_rgb((81, 90, 240)) == (254, 0, 0)


# -- the check through ffmpeg --------------------------------------------------------------------


@needs_ffmpeg
def test_x264_gives_the_same_colours_fed_either_way():
    """x264 takes YUV only: ffmpeg converts both feeds on the processor, the same."""
    size = (1920, 480)
    results = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["libx264"])
    (x264,) = results
    assert (x264["state"], x264["verdict"]) == ("done", "identical")
    assert x264["worst"]["difference"] <= colour_check.TOLERANCE
    assert {feed["pixels"] for feed in x264["feeds"].values()} == {"rgb0", "rgb24"}
    assert all(feed["decoded"] == "yuv420p" and not feed["card"] for feed in x264["feeds"].values())
    red = next(p for p in x264["patches"] if p["id"] == "red")
    assert red["new"]["yuv"] == pytest.approx([81, 90, 240], abs=1.5)
    assert max(abs(a - b) for a, b in zip(red["new"]["rgb"], (255, 0, 0), strict=True)) <= 3


@needs_ffmpeg
def test_a_conversion_to_full_range_is_a_level_shift_and_one_with_bt709_a_matrix_shift():
    size = (1920, 480)

    def verdict(extra):
        (result,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["libx264"],
                          command=shifted(extra))  # fmt: skip
        return result

    level = verdict("scale=out_range=full")
    assert level["verdict"] == "level", level["worst"]
    assert level["feeds"]["auto"]["decoded"] in ("yuvj420p", "yuv420p")  # named, measured
    black = next(p for p in level["patches"] if p["id"] == "black")
    assert black["new"]["yuv"][0] < 5 and black["old"]["yuv"][0] == pytest.approx(16, abs=1)
    matrix = verdict("scale=out_color_matrix=bt709")
    assert matrix["verdict"] == "matrix", matrix["worst"]
    assert matrix["worst"]["difference"] > 10


@needs_ffmpeg
def test_an_encoder_of_the_card_that_is_not_here_is_not_available_and_nothing_fails():
    """NVIDIA's encoder is listed by many an ffmpeg without a card to run it: "not available
    here". One that ffmpeg is said to start but that fails is "failed", with ffmpeg's words;
    one of the card's that takes three bytes a pixel but not four is "refused"."""
    size = (960, 320)
    if h264.encoder_works(FFMPEG, "h264_nvenc"):
        pytest.skip("NVIDIA's encoder runs here")
    (nvenc,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_nvenc"])
    assert nvenc["state"] == "unavailable" and nvenc["verdict"] is None
    (failed,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_nvenc"],
                      works=lambda ffmpeg, encoder: True)  # fmt: skip
    assert failed["state"] == "failed" and failed["error"]

    def no_rgb0(ffmpeg, size, rotate, settings):
        if settings.pixels == "rgb0":
            return [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "nonexistent", "-i", "-"]
        return shifted("", "h264_amf")(ffmpeg, size, rotate, settings)

    (refused,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_amf"],
                       works=lambda ffmpeg, encoder: True, command=no_rgb0)  # fmt: skip
    assert refused["state"] == "refused" and refused["feeds"]["auto"]["error"]
    assert all("old" in p and "new" not in p for p in refused["patches"])


def test_a_run_that_outlasts_its_time_is_killed(monkeypatch):
    python = shutil.which("python3") or shutil.which("python")
    began = time.monotonic()
    with pytest.raises(colour_check.CheckError, match="longer than"):
        colour_check.run([python, "-c", "import time; time.sleep(30)"], b"", timeout=0.5)
    assert time.monotonic() - began < 10


def test_a_check_that_is_stopped_ends_its_ffmpeg_at_once(isolated_home, monkeypatch):
    """The app closes during a check: the ffmpeg under way is ended (and what it started), no
    other encoder is begun."""
    python = shutil.which("python3") or shutil.which("python")
    started = []

    def slow(ffmpeg, size, rotate, settings):
        started.append(settings.encoder)
        return [python, "-c", "import sys, time; sys.stdin.buffer.read(); time.sleep(60)"]

    monkeypatch.setattr(h264, "find_ffmpeg", lambda configured="": python)
    runner = ColourCheck(None, works=lambda ffmpeg, encoder: True, command=slow)
    runner.start_check(1)
    assert wait_for(lambda: started and colour_check._running, timeout=30)
    began = time.monotonic()
    runner.close()
    assert not runner.running and time.monotonic() - began < 10
    assert started == ["h264_amf"] and runner.ended == "stopped"
    assert runner.state(1)["check"]["results"] == [] and not colour_check._running


# -- what it means for the panel ------------------------------------------------------------------


def test_the_advice_follows_the_verdict_on_the_encoder_in_use():
    amf = {"encoder": "h264_amf", "feed": "rgb0"}
    results = [{"encoder": "h264_amf", "state": "done", "verdict": "matrix"}]
    assert advice(amf, results, "auto") == "shift"
    assert advice(amf, [{**results[0], "verdict": "identical"}], "auto") == "nothing"
    assert advice(amf, [{**results[0], "state": "refused", "verdict": None}], "auto") == "refused"
    assert advice({**amf, "feed": "rgb24"}, results, "auto") == "refused"  # it fell back
    assert advice(amf, results, "rgb") == "back"
    assert advice({"encoder": "libx264", "feed": "rgb0"}, results, "auto") == "processor"
    assert advice(amf, [], "auto") == "" and advice(None, results, "auto") == ""
    assert in_use_of("h264_amf, rgb0") == ("h264_amf", "rgb0")
    assert in_use_of(None) is None and in_use_of("libx264") is None


def running_app(stats=None, more=None):
    status = SimpleNamespace(stats=stats or {}, model=NINE, size=(1920, 480), more=more or {})
    return SimpleNamespace(panel=SimpleNamespace(status=status))


def wait_for(predicate, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@needs_ffmpeg
def test_the_check_runs_with_the_panel_s_size_and_says_what_to_do(isolated_home):
    """Panel 1 runs h264_amf fed rgb0; the card's encoder (x264 with BT.709 here) shifts the
    colours: the check says so, and what to do is the processor's conversion."""
    stats = {"streaming": True, "encoder": "h264_amf, rgb0"}
    runner = ColourCheck(None, running_app(stats), works=lambda f, e: e == "h264_amf",
                         command=shifted("scale=out_color_matrix=bt709", "h264_amf"))  # fmt: skip
    runner.start_check(1)
    with pytest.raises(ColourError):
        runner.start_check(1)  # once at a time
    assert wait_for(lambda: not runner.running)
    state = runner.state(1)
    assert state["check"]["picture"] == {"panel": 1, "model": NINE, "size": [1920, 480]}
    verdicts = {r["encoder"]: r["verdict"] or r["state"] for r in state["check"]["results"]}
    assert verdicts == {"h264_amf": "matrix", "h264_nvenc": "unavailable",
                        "h264_qsv": "unavailable", "libx264": "identical"}  # fmt: skip
    assert state["in_use"] == {"encoder": "h264_amf", "feed": "rgb0", "running": True, "card": True}
    assert state["advice"] == "shift" and state["video"] and state["app"]


def test_without_ffmpeg_the_check_says_why(isolated_home, monkeypatch):
    from libre_panel.devices.base import DeviceError

    def nowhere(configured=""):
        raise DeviceError("the video mode needs ffmpeg")

    monkeypatch.setattr(h264, "find_ffmpeg", nowhere)
    runner = ColourCheck(None)
    runner.start_check(1)
    state = runner.state(1)
    assert (
        state["check"]["error"] == "the video mode needs ffmpeg" and not state["check"]["running"]
    )
    with pytest.raises(ColourError):
        runner.state(3)  # no such panel


def test_the_route(isolated_home, monkeypatch):
    from test_editor_server import request

    from libre_panel.editor.server import make_server

    monkeypatch.setattr(h264, "encoder_works", lambda ffmpeg, encoder: False)
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, state = request(port, "GET", "/api/colour-check?panel=1")
        assert status == 200 and state["panel"] == 1 and not state["app"]
        assert state["check"]["results"] == [] and state["pixels"] == "auto"
        assert request(port, "GET", "/api/colour-check?panel=5")[0] == 400
        assert request(port, "POST", "/api/colour-check", {"action": "dance"})[0] == 400
        status, answer = request(port, "POST", "/api/colour-check", {"action": "show", "panel": 1})
        assert status == 409 and "running app" in answer["error"]  # a plain editor has no panel
        if FFMPEG is not None:
            status, state = request(port, "POST", "/api/colour-check", {"action": "check"})
            assert status == 200
            assert wait_for(
                lambda: not request(port, "GET", "/api/colour-check")[1]["check"]["running"]
            )
            state = request(port, "GET", "/api/colour-check")[1]
            assert [r["state"] for r in state["check"]["results"]] == [
                "unavailable", "unavailable", "unavailable", "done"]  # fmt: skip
            assert state["advice"] == "processor"  # x264 is what "auto" starts here
    finally:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


# -- the setting and the one click ----------------------------------------------------------------


def test_the_pixels_setting_is_read_and_written_like_the_other_video_keys(isolated_home):
    from libre_panel.editor.settings import Restart, Settings, SettingsError

    isolated_home.mkdir(parents=True, exist_ok=True)
    path = config_dir() / "config.toml"
    path.write_text('[[panels]]\nmodel = "turing-9.2-usb"\nvideo = { mode = "on" }\n',
                    encoding="utf-8")  # fmt: skip
    settings = Settings(path, None, Restart())
    assert settings.read()["config"]["video"]["pixels"] == "auto"
    for wrong in ("rgb24", "rgb0", True, 1):
        with pytest.raises(SettingsError, match="video.pixels"):
            settings.write({"set": {"video.pixels": wrong}})
    settings.write({"set": {"video.pixels": "rgb"}})  # what "Use the processor's conversion" does
    assert 'pixels = "rgb"' in path.read_text(encoding="utf-8")
    config = load_config(path)
    assert config.device.video.pixels == "rgb"
    assert config.for_panel(0).device.video.pixels == "rgb"  # the companion follows
    assert settings.read()["config"]["video"]["pixels"] == "rgb"
    settings.write({"set": {"video.pixels": "auto"}})  # "Back to the card's conversion"
    assert load_config(path).for_panel(0).device.video.pixels == "auto"


def test_a_new_feed_is_followed_on_the_open_panel():
    """video.pixels changes how the frames go into ffmpeg, not the panel: the loops follow it
    without opening the panel again (the video display starts a new ffmpeg)."""
    a = DeviceConfig(video=VideoConfig(mode="on", local_clip="x.h264"))
    b = DeviceConfig(video=VideoConfig(mode="on", local_clip="x.h264", pixels="rgb"))
    assert app._same_device(a, b)
    assert not app._same_device(a, replace(b, video=replace(b.video, crf=18)))
    beside = parse_config({"video": {"pixels": "rgb"}, "panels": [{"model": NINE}]})
    assert beside.for_panel(0).device.video.pixels == "rgb"


class FeedPanel(Display):
    """A streaming panel that keeps each frame and the feed it was to go in with."""

    streaming = True
    stream_fps = 50

    def __init__(self, config):
        super().__init__(config)
        self.shown = []
        self.feeds = []

    def show(self, frame, region=None):
        self.shown.append(frame)
        self.feeds.append((self.feed, self.config.video.pixels))


def loop_on(config, panel=None):
    from test_app_cli import StepClock

    displays = []
    clock = StepClock(1000.0)

    def make(device):
        displays.append(FeedPanel(device))
        return displays[-1]

    loop = app.MainLoop(config, display=make(config.device), clock=clock, panel=panel)
    return loop, displays, clock


def test_the_one_click_reaches_the_running_loop_of_a_panel_beside_the_first(
    isolated_home, monkeypatch
):
    from libre_panel.config import edit_config

    isolated_home.mkdir(parents=True, exist_ok=True)
    path = config_dir() / "config.toml"
    path.write_text('theme = "pico"\n[sensors]\nproviders = ["demo"]\n\n'
                    '[[panels]]\ntheme = "pico"\nvideo = { mode = "on" }\n',
                    encoding="utf-8")  # fmt: skip
    config = app.panel_config(load_config(path), 0)
    loop, displays, clock = loop_on(config, panel=0)
    loop.step()
    assert displays[-1].feeds[-1] == (None, "auto")
    edit_config(("value", "video.pixels", "rgb"), path=path)
    end = time.monotonic() + 30  # (the loop warms the theme up again, in a thread)
    while displays[-1].feeds[-1][1] != "rgb" and time.monotonic() < end:
        clock.now += 0.1
        loop.step()
        time.sleep(0.01)
    assert len(displays) == 1  # the same panel, not opened again
    assert displays[0].feeds[-1] == (None, "rgb")
    loop.close()


# -- the A/B on the panel --------------------------------------------------------------------------


def test_the_a_b_shows_the_picture_fed_both_ways_and_then_the_theme_again(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(colour_show, "clock", lambda: now[0])
    config = Config(theme="pico", sensors=SensorsConfig(providers=["demo"]))
    loop, displays, _clock = loop_on(config)
    panel = displays[0]
    loop.step()
    theme = panel.shown[-1]
    colour_show.begin(2)  # another panel's: this one goes on
    loop.step()
    assert panel.feeds[-1] == (None, "auto") and panel.shown[-1].tobytes() == theme.tobytes()
    colour_show.begin(1, seconds=30)
    loop.step()
    assert panel.feeds[-1][0] == "auto"
    assert panel.shown[-1] is check_picture(theme.size, "A")
    now[0] += colour_show.SWITCH_S
    loop.step()
    assert panel.feeds[-1][0] == "rgb" and panel.shown[-1] is check_picture(theme.size, "B")
    now[0] += colour_show.SWITCH_S
    loop.step()
    assert panel.feeds[-1][0] == "auto"
    now[0] += 30  # over by its time
    loop.step()
    assert panel.feeds[-1] == (None, "auto") and panel.shown[-1].size == theme.size
    assert panel.shown[-1] is not check_picture(theme.size, "A")
    loop.close()


def test_a_panel_in_a_process_of_its_own_is_told_of_the_a_b(monkeypatch):
    now = [50.0]
    monkeypatch.setattr(colour_show, "clock", lambda: now[0])
    colour_show.begin(2, seconds=10)
    told = colour_show.export()
    assert told == {"panel": 2, "letter": "A", "pixels": "auto", "left_s": 10}
    colour_show.end()
    assert colour_show.now(2) is None and colour_show.export() is None
    colour_show.adopt(told)  # (in the panel's process)
    assert colour_show.now(2) == ("A", "auto") and colour_show.now(1) is None
    now[0] += 11  # the first panel's process said nothing more: it ends by itself
    assert colour_show.now(2) is None
    colour_show.adopt({"panel": 2, "letter": "B", "pixels": "rgb", "left_s": 4})
    assert colour_show.now(2) == ("B", "rgb")
    colour_show.adopt(None)
    assert colour_show.now(2) is None
    colour_show.adopt({"panel": "two"})
    assert colour_show.now(2) is None


def test_the_a_b_ends_as_it_began_after_a_fault_and_after_the_app_ended(isolated_home, monkeypatch):
    isolated_home.mkdir(parents=True, exist_ok=True)
    marker = config_dir() / colour_show.MARKER
    stats = {"streaming": True, "encoder": "h264_amf, rgb0"}
    runner = ColourCheck(None, running_app(stats))
    seen = []

    def broken(stop):
        seen.append((colour_show.now(1), marker.exists()))
        raise RuntimeError("the panel went away")

    monkeypatch.setattr(runner, "_watch", broken)
    runner.start_show(1)
    runner._show_thread.join(10)
    assert seen == [(("A", "auto"), True)]  # it showed, the marker lay beside config.toml
    assert colour_show.now(1) is None and colour_show.state() is None  # as before
    assert not marker.exists()
    state = runner.state(1)
    assert state["show"]["error"] == "the panel went away" and not state["show"]["running"]
    # the app ended while it showed (no finally ran): the next start finds the marker
    colour_show.begin(1)
    assert marker.exists()
    colour_show._own = None  # (what a process that ended keeps of it)
    assert colour_show.recover() == {"panel": 1, "began": pytest.approx(time.time(), abs=60)}
    assert not marker.exists() and colour_show.recover() is None


def test_the_a_b_is_only_for_a_panel_that_shows_video(isolated_home, monkeypatch):
    runner = ColourCheck(None, running_app({"streaming": False}))
    with pytest.raises(ColourError, match="no video"):
        runner.start_show(1)
    assert not runner.state(1)["video"]
    with pytest.raises(ColourError, match="running app"):
        ColourCheck(None, None).start_show(1)
    monkeypatch.setattr(ColourCheck, "TICK_S", 0.01)
    runner = ColourCheck(None, running_app({"streaming": True, "encoder": "libx264, rgb0"}))
    monkeypatch.setattr(colour_show, "SHOW_S", 0.3)
    runner.start_show(1)
    assert runner.state(1)["show"]["panel"] == 1
    with pytest.raises(ColourError, match="already"):
        runner.start_show(1)
    runner._show_thread.join(10)
    assert runner.state(1)["show"]["ended"] == "done" and colour_show.state() is None
    runner.start_show(1)
    runner.stop_show()
    runner._show_thread.join(10)
    assert runner.state(1)["show"]["ended"] == "stopped"


# -- the card's encoder that took no four bytes a pixel --------------------------------------------


def test_a_refused_feed_is_kept_a_week_per_encoder_and_ffmpeg(isolated_home):
    isolated_home.mkdir(parents=True, exist_ok=True)
    now = 1_800_000_000.0
    assert not video_state.refused("/a/ffmpeg", "h264_amf", now=now)
    video_state.keep_refusal("/a/ffmpeg", "h264_amf", now=now)
    assert video_state.refused("/a/ffmpeg", "h264_amf", now=now + 3600)
    assert not video_state.refused("/b/ffmpeg", "h264_amf", now=now)  # another ffmpeg
    assert not video_state.refused("/a/ffmpeg", "h264_nvenc", now=now)
    assert not video_state.refused("/a/ffmpeg", "h264_amf", now=now + video_state.PROBE_S + 1)
    assert not video_state.refused("/a/ffmpeg", "h264_amf", now=now - 10)  # the clock went back
    video_state.keep_probe("/a/ffmpeg", "h264_amf", True, now=now)  # the rest stays
    assert video_state.refused("/a/ffmpeg", "h264_amf", now=now)
    data = json.loads((config_dir() / video_state.STATE_FILENAME).read_text(encoding="utf-8"))
    assert data["feeds"] == {"h264_amf /a/ffmpeg": {"refused": "rgb0", "when": int(now)}}


def test_the_next_connection_starts_a_card_that_refused_with_the_feed_that_works(
    isolated_home, monkeypatch
):
    pytest.importorskip("usb")
    from libre_panel.devices import turzx_video

    isolated_home.mkdir(parents=True, exist_ok=True)
    started = []

    class FakeEncoder:
        def __init__(self, command, size, deliver):
            started.append(command)
            self.size, self.pictures, self.alive = size, 0, False

    monkeypatch.setattr(turzx_video, "Encoder", FakeEncoder)
    video = VideoConfig(mode="on", local_clip="x.h264", encoder="h264_amf")
    device = DeviceConfig(driver="turzx", model=NINE, video=video)

    def connection():
        display = turzx_video.TurzxVideoDisplay(device)
        display._ffmpeg = "/a/ffmpeg"
        return display

    first = connection()
    first._start_encoder((1920, 480))
    assert started[-1][started[-1].index("-pix_fmt") + 1] == "rgb0"
    assert first._gave_up("exit code 1") is True  # ended before a picture, fed rgb0
    assert video_state.refused("/a/ffmpeg", "h264_amf")
    second = connection()  # the next connection: no try with rgb0 first
    second._start_encoder((1920, 480))
    assert started[-1][started[-1].index("-pix_fmt") + 1] == "rgb24"
    assert second._feeds_failed == {"h264_amf"}
    # a colour check's feed or video.pixels do not change that: the card refused rgb0
    second.set_feed("auto")
    assert not second._feed_changed()


def test_a_new_feed_starts_a_new_ffmpeg_and_the_check_s_picture_does_not_stay(monkeypatch):
    pytest.importorskip("usb")
    from libre_panel.devices import turzx_video

    video = VideoConfig(mode="on", local_clip="x.h264", encoder="h264_amf")
    display = turzx_video.TurzxVideoDisplay(DeviceConfig(driver="turzx", model=NINE, video=video))
    display._ffmpeg = "/a/ffmpeg"
    monkeypatch.setattr(video_state, "refused", lambda ffmpeg, encoder: False)
    display.encoder = SimpleNamespace(settings=h264.EncoderSettings(encoder="h264_amf",
                                                                    pixels="rgb0"))  # fmt: skip
    assert not display._feed_changed()
    display.set_feed("rgb")  # B of the A/B
    assert display._feed_changed()
    display.set_feed(None)
    video.pixels = "rgb"  # the one click, followed by the loop
    assert display._feed_changed()
    video.pixels = "auto"
    assert not display._feed_changed()
    shown = []
    display.transport, display.session = object(), object()
    monkeypatch.setattr(display, "_hand_over", lambda: None)
    monkeypatch.setattr(display, "_follow_the_panel", lambda: None)
    monkeypatch.setattr(display, "_feed", shown.append)
    display.encoder = SimpleNamespace(size=(1920, 480), alive=True, pictures=1,
                                      settings=display.encoder.settings)  # fmt: skip
    theme = Image.new("RGB", (1920, 480), "navy")
    display.show(theme)
    display.set_feed("auto")
    display.show(check_picture((1920, 480), "A"))
    assert display._last_frame is theme  # what stays on the panel when it closes

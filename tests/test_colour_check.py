"""Settings → Panel → Check the colours and the automatic choice of who converts them: the test
picture, how a stream's patches are measured and judged (in limited and in full range), the
check through ffmpeg (x264 here, standing in for the card's encoders where they do not run), one
check at a time, the verdicts kept for video.pixels "auto" and the check in the background, what
the card says of who converts and why, its route, the settings it writes and how the running
panel follows them, the A/B on a panel (the card's conversion against the processor's, limited
against full range) and that it ends as it began, and the card's encoder that gave no picture
converting itself, kept for a week."""

import json
import os
import shutil
import subprocess
import sys
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
from libre_panel.devices import card_choice, colour_check, h264, video_state
from libre_panel.devices.base import Display
from libre_panel.devices.colour_check import (
    CARD_FEEDS,
    PATCHES,
    PROCESSOR_FEEDS,
    centre,
    check,
    check_picture,
    feeds_of,
    grid,
    judge,
    layout,
    measure,
    planes,
    to_rgb,
)
from libre_panel.devices.turzx_video import resolved, rotation_of, video_settings
from libre_panel.editor.colours import ColourCheck, ColourError, in_use_of, way_of, why_of
from libre_panel.theme.model import find_theme, load_theme

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg with libx264")
NINE, TWELVE = "turing-9.2-usb", "turing-12.3-usb"
EIGHT = "turing-8.8-usb"  # a panel whose video is limited range by itself (the 9.2" is full)


@pytest.fixture(autouse=True)
def no_check_showing():
    yield
    colour_show.end()
    colour_show.adopt(None)


def settings_for(video=None, model=NINE, full=False):
    base = video_settings(video or VideoConfig())
    return lambda encoder, pixels: resolved(base, model, encoder, pixels, full)


def stand_in(extra="", encoder="h264_amf", fail=False):
    """The display's ffmpeg, with x264 standing in for the card's ``encoder`` (not here): its
    card's way the processor's conversion with ``extra`` filters after it (``fail``: an ffmpeg
    that gives nothing, as an encoder that takes no RGB)."""

    def command(ffmpeg, size, rotate, settings):
        card = h264.passes_through(settings)
        if settings.encoder == encoder:
            settings = replace(settings, encoder="libx264", card=False)
        if card and fail:
            return [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "nonexistent", "-i", "-"]
        found = h264.ffmpeg_command(ffmpeg, size, rotate, settings)
        if card and extra:
            # (said to be what the stream says, so that an ffmpeg that converts to what its
            # output is told, as 8.x does, leaves the card's own conversion as it is)
            told = f"setparams=range={after(found, '-color_range')}:colorspace=smpte170m"
            found[found.index("-vf") + 1] += f",{extra},{told}"
        return found

    return command


def after(command, flag):
    return command[command.index(flag) + 1]


def yuv_of(rgb):
    """BT.601 in limited range, as swscale converts (rounded)."""
    r, g, b = rgb
    y = 16 + (65.481 * r + 128.553 * g + 24.966 * b) / 255
    u = 128 + (-37.797 * r - 74.203 * g + 112.0 * b) / 255
    v = 128 + (112.0 * r - 93.786 * g - 18.214 * b) / 255
    return round(y), round(u), round(v)


def full_yuv_of(rgb):
    """BT.601 in full range (rounded)."""
    r, g, b = rgb
    y = 0.299 * r + 0.587 * g + 0.114 * b
    u = 128 + (-0.168736 * r - 0.331264 * g + 0.5 * b)
    v = 128 + (0.5 * r - 0.418688 * g - 0.081312 * b)
    return tuple(min(255, round(c)) for c in (y, u, v))


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


def wait_for(predicate, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- the picture and the measurement -----------------------------------------------------------


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


def test_two_ways_are_judged_identical_a_level_shift_a_matrix_shift_or_other():
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


def test_the_colours_are_shown_as_the_panel_s_range_has_them():
    """In full range the panel shows 0 as black and 255 as white: the swatches and the
    differences in RGB go so; a limited-range black there is dark grey."""
    assert to_rgb((0, 128, 128), full=True) == (0, 0, 0)
    assert to_rgb((255, 128, 128), full=True) == (255, 255, 255)
    assert to_rgb((16, 128, 128), full=True) == (16, 16, 16)  # black too light
    assert to_rgb(full_yuv_of((255, 0, 0)), full=True) == pytest.approx((255, 0, 0), abs=1)
    for _name, rgb in PATCHES:
        shown = to_rgb(full_yuv_of(rgb), full=True)
        assert max(abs(a - b) for a, b in zip(shown, rgb, strict=True)) <= 2
    full = {name: full_yuv_of(rgb) for name, rgb in PATCHES}
    assert judge(full, full, full=True)["verdict"] == "identical"
    dull = {name: (16 + y * 219 / 255, u, v) for name, (y, u, v) in full.items()}
    level = judge(dull, full, full=True)
    assert level["verdict"] == "level" and level["worst"]["rgb"] >= 16


def test_each_encoder_is_checked_the_card_s_way_against_the_processor_s_where_the_card_can():
    assert CARD_FEEDS == ("card", "processor") and PROCESSOR_FEEDS == ("processor", "rgb")
    for encoder in ("h264_amf", "h264_nvenc", "h264_qsv"):
        assert feeds_of(encoder) == CARD_FEEDS
    assert feeds_of("libx264") == feeds_of("libx264", full=True) == PROCESSOR_FEEDS
    assert feeds_of("h264_amf", full=True) == feeds_of("h264_qsv", full=True) == CARD_FEEDS
    assert feeds_of("h264_nvenc", full=True) == PROCESSOR_FEEDS  # limited range only


# -- the check through ffmpeg --------------------------------------------------------------------


@needs_ffmpeg
def test_x264_gives_the_same_colours_fed_either_way():
    """x264 takes YUV only: ffmpeg converts both feeds on the processor, the same."""
    size = (1920, 480)
    results = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["libx264"])
    (x264,) = results
    assert (x264["state"], x264["verdict"]) == ("done", "identical")
    assert x264["compared"] == ["processor", "rgb"] and x264["full"] is False
    assert x264["worst"]["difference"] <= colour_check.TOLERANCE
    assert {way: feed["pixels"] for way, feed in x264["feeds"].items()} == {
        "processor": "rgb0", "rgb": "rgb24"}  # fmt: skip
    assert all(feed["decoded"] == "yuv420p" and not feed["card"] for feed in x264["feeds"].values())
    red = next(p for p in x264["patches"] if p["id"] == "red")
    assert red["new"]["yuv"] == pytest.approx([81, 90, 240], abs=1.5)
    assert max(abs(a - b) for a, b in zip(red["new"]["rgb"], (255, 0, 0), strict=True)) <= 3


@needs_ffmpeg
def test_a_panel_in_full_range_is_measured_in_full_range():
    """The check makes the video as the display does for a panel in full range: black at 0,
    red as BT.601 in full range has it, and the swatches as that panel shows them."""
    size = (1920, 720)
    (x264,) = check(FFMPEG, settings_for(model=TWELVE, full=True), size, rotation_of(size),
                    encoders=["libx264"], full=True)  # fmt: skip
    assert (x264["state"], x264["verdict"], x264["full"]) == ("done", "identical", True)
    patches = {p["id"]: p for p in x264["patches"]}
    assert patches["black"]["new"]["yuv"][0] == pytest.approx(0, abs=1.5)
    assert patches["white"]["new"]["yuv"][0] == pytest.approx(255, abs=1.5)
    assert patches["red"]["new"]["yuv"] == pytest.approx(full_yuv_of((255, 0, 0)), abs=1.5)
    assert max(patches["black"]["new"]["rgb"]) <= 1  # (ffmpeg 8 rounds the chroma a step off)
    red = patches["red"]["new"]["rgb"]
    assert max(abs(a - b) for a, b in zip(red, (255, 0, 0), strict=True)) <= 3


@needs_ffmpeg
def test_a_card_converting_to_full_range_is_a_level_shift_and_one_with_bt709_a_matrix_shift():
    """AMD's encoder (x264 in its place here) converting the card's way: as the processor
    does, identical; into full range, a level shift; with BT.709, a matrix shift (what AMD's
    own converter was measured doing)."""
    size = (1920, 480)

    def verdict(extra):
        (result,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_amf"],
                          works=lambda ffmpeg, encoder: True,
                          command=stand_in(extra))  # fmt: skip
        return result

    same = verdict("")
    assert same["verdict"] == "identical" and same["compared"] == ["card", "processor"]
    assert same["feeds"]["card"]["card"] and not same["feeds"]["processor"]["card"]
    level = verdict("scale=out_range=full")
    assert level["verdict"] == "level", level["worst"]
    assert level["feeds"]["card"]["decoded"] in ("yuvj420p", "yuv420p")  # named, measured
    black = next(p for p in level["patches"] if p["id"] == "black")
    assert black["new"]["yuv"][0] < 5 and black["old"]["yuv"][0] == pytest.approx(16, abs=1)
    matrix = verdict("scale=out_color_matrix=bt709")
    assert matrix["verdict"] == "matrix", matrix["worst"]
    assert matrix["worst"]["difference"] > 10


@needs_ffmpeg
def test_an_encoder_of_the_card_that_is_not_here_is_not_available_and_nothing_fails():
    """NVIDIA's encoder is listed by many an ffmpeg without a card to run it: "not available
    here". One that ffmpeg is said to start but that fails is "failed", with ffmpeg's words;
    one of the card's that gives nothing converting itself but does with the processor's
    conversion is "refused"."""
    size = (960, 320)
    if h264.encoder_works(FFMPEG, "h264_nvenc"):
        pytest.skip("NVIDIA's encoder runs here")
    (nvenc,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_nvenc"])
    assert nvenc["state"] == "unavailable" and nvenc["verdict"] is None
    (failed,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_nvenc"],
                      works=lambda ffmpeg, encoder: True)  # fmt: skip
    assert failed["state"] == "failed" and failed["error"]
    (refused,) = check(FFMPEG, settings_for(), size, rotation_of(size), encoders=["h264_amf"],
                       works=lambda ffmpeg, encoder: True,
                       command=stand_in(fail=True))  # fmt: skip
    assert refused["state"] == "refused" and refused["feeds"]["card"]["error"]
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


# -- one check at a time ---------------------------------------------------------------------------


def test_one_check_at_a_time_in_a_process_and_across_the_app_s_processes(isolated_home):
    """While one check runs no other begins: in this process the lock, across the app's
    processes the file beside config.toml naming the process. A file of a process that is gone,
    or an old one, is taken over; without config.toml's folder the lock alone holds."""
    with colour_check.exclusive() as free:  # (no folder yet: the lock alone)
        assert free and colour_check.busy()
        with colour_check.exclusive() as again:
            assert not again
    assert not colour_check.busy()
    isolated_home.mkdir(parents=True)
    marker = config_dir() / colour_check.BUSY_FILE
    with colour_check.exclusive() as free:
        assert free and marker.read_text(encoding="utf-8").split() == [str(os.getpid())]
        began = time.monotonic()
        with colour_check.exclusive(wait=0.5) as again:
            assert not again and time.monotonic() - began >= 0.4
    assert not marker.exists()
    other = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)  # fmt: skip
    try:
        marker.write_text(f"{other.pid}\n", encoding="utf-8")  # another process of the app
        assert colour_check.busy()
        with colour_check.exclusive() as free:
            assert not free
        assert marker.exists()  # (not its own: left alone)
        old = time.time() - colour_check.STALE_S - 10
        os.utime(marker, (old, old))
        assert not colour_check.busy()
        with colour_check.exclusive() as free:
            assert free
    finally:
        other.stdin.close()
        other.wait(10)
    marker.write_text(f"{other.pid}\n", encoding="utf-8")  # the process is gone
    with colour_check.exclusive() as free:
        assert free
    assert not marker.exists()


# -- the verdicts the automatic choice goes by -----------------------------------------------------


def result_of(encoder="h264_amf", verdict="identical", state="done", compared=CARD_FEEDS):
    return {"encoder": encoder, "state": state, "verdict": verdict, "compared": list(compared),
            "worst": {"patch": "green", "channel": "Y", "difference": 27.0}}  # fmt: skip


def test_a_verdict_is_kept_per_encoder_range_ffmpeg_and_versions_a_week_long(
    isolated_home, monkeypatch
):
    """What a check says for the choice: identical, the card; any shift or no picture
    converting itself, the processor; a failed or stopped check, and x264's (the processor's
    two feeds), nothing. Kept in video-state.json with ffmpeg's version and the driver's:
    another of either, another ffmpeg, the other range, or a week later, and it is to be found
    out anew."""
    isolated_home.mkdir(parents=True)
    versions = {"/a/ffmpeg": "ffmpeg version 8.1 | driver 31.0", "/b/ffmpeg": "ffmpeg version 7"}
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: versions[ffmpeg])
    assert card_choice.verdict("/a/ffmpeg", "h264_amf", False) is None
    assert not card_choice.chosen("/a/ffmpeg", "h264_amf", False)
    kept = card_choice.record("/a/ffmpeg", result_of(), False)
    assert kept["card"] is True and kept["by"] == "check" and kept["verdict"] == "identical"
    assert kept["version"] == versions["/a/ffmpeg"]
    assert kept["when"] == pytest.approx(time.time(), abs=5)
    assert card_choice.chosen("/a/ffmpeg", "h264_amf", False)
    assert not card_choice.chosen("/a/ffmpeg", "h264_amf", True)  # the other range
    assert not card_choice.chosen("/b/ffmpeg", "h264_amf", False)  # another ffmpeg
    assert not card_choice.chosen(None, "h264_amf", False)
    shifted = card_choice.record("/a/ffmpeg", result_of(verdict="matrix"), True, by="background")
    assert shifted["card"] is False and shifted["by"] == "background"
    assert shifted["worst"]["difference"] == 27.0
    refused = card_choice.record("/a/ffmpeg", result_of("h264_qsv", None, "refused"), False)
    assert refused["card"] is False and refused["state"] == "refused"
    assert card_choice.record("/a/ffmpeg", result_of(state="failed", verdict=None), False) is None
    assert card_choice.record("/a/ffmpeg", result_of(state="stopped", verdict=None), False) is None
    x264 = result_of("libx264", compared=PROCESSOR_FEEDS)
    assert card_choice.record("/a/ffmpeg", x264, False) is None
    assert card_choice.record("/a/ffmpeg", result_of("h264_nvenc"), True) is None  # can't
    data = json.loads((config_dir() / video_state.STATE_FILENAME).read_text(encoding="utf-8"))
    assert sorted(data["cards"]) == ["h264_amf full /a/ffmpeg", "h264_amf limited /a/ffmpeg",
                                     "h264_qsv limited /a/ffmpeg"]  # fmt: skip
    card_choice.forget()  # the next run: from video-state.json
    assert card_choice.chosen("/a/ffmpeg", "h264_amf", False)
    versions["/a/ffmpeg"] = "ffmpeg version 8.1 | driver 32.0"  # a new driver
    card_choice.forget()
    assert card_choice.verdict("/a/ffmpeg", "h264_amf", False) is None
    version = "ffmpeg version 8.1 | driver 31.0"
    week = time.time() + video_state.PROBE_S + 60
    assert video_state.card_choice("/a/ffmpeg", "h264_amf", False, version, now=week) is None
    back = time.time() - 3600  # the clock went back
    assert video_state.card_choice("/a/ffmpeg", "h264_amf", False, version, now=back) is None
    assert video_state.card_choice("/a/ffmpeg", "h264_amf", False, version)["card"] is True


def test_the_version_a_verdict_holds_for_is_ffmpeg_s_and_the_driver_s(monkeypatch):
    monkeypatch.setattr(card_choice, "_driver", None)
    monkeypatch.setattr(card_choice, "_read_driver", lambda: "31.0.1")
    card_choice.forget()
    assert card_choice.version("/nowhere/ffmpeg") == " | driver 31.0.1"
    monkeypatch.setattr(card_choice, "_read_driver", lambda: 1 / 0)
    monkeypatch.setattr(card_choice, "_driver", None)
    assert card_choice.driver_version() == ""  # nothing known is no fault
    if FFMPEG is not None:
        card_choice.forget()
        assert card_choice.version(FFMPEG).startswith("ffmpeg version ")
        assert card_choice.version(FFMPEG).endswith(" | driver -")


@needs_ffmpeg
@pytest.mark.parametrize("extra, card", [("", True), ("scale=out_color_matrix=bt709", False)])
def test_the_check_in_the_background_runs_once_and_its_verdict_is_followed(
    isolated_home, monkeypatch, extra, card
):
    """The first start of a card's encoder without a verdict: a check of five frames each way in
    the background (AMD's here, x264 in its place), its verdict kept for the next start; begun
    once a run, not again while known; none for x264 or for NVIDIA's on a full-range panel."""
    isolated_home.mkdir(parents=True)
    size = (1920, 480)
    frames = []
    real = colour_check.encode

    def counted(command, picture, pixels, count=colour_check.FRAMES, timeout=30.0):
        frames.append((count, timeout))
        return real(command, picture, pixels, count, timeout)

    monkeypatch.setattr(colour_check, "encode", counted)
    thread = card_choice.ensure(FFMPEG, "h264_amf", False, settings_for(), size, 270,
                                command=stand_in(extra))  # fmt: skip
    assert thread is not None
    assert card_choice.ensure(FFMPEG, "h264_amf", False, settings_for(), size, 270) is None
    thread.join(60)
    assert frames == [(card_choice.FRAMES, card_choice.TIMEOUT_S)] * 2 and card_choice.FRAMES == 5
    kept = card_choice.verdict(FFMPEG, "h264_amf", False)
    assert kept["card"] is card and kept["by"] == "background"
    assert kept["verdict"] == ("identical" if card else "matrix")
    assert card_choice.chosen(FFMPEG, "h264_amf", False) is card
    assert not card_choice.checking(FFMPEG, "h264_amf", False)
    card_choice.forget()  # the next start: known, no check
    assert card_choice.ensure(FFMPEG, "h264_amf", False, settings_for(), size, 270) is None
    assert card_choice.ensure(FFMPEG, "libx264", False, settings_for(), size, 270) is None
    assert card_choice.ensure(FFMPEG, "h264_nvenc", True, settings_for(), size, 270) is None
    assert len(frames) == 2


@needs_ffmpeg
def test_the_check_in_the_background_waits_while_another_runs_and_says_nothing_when_it_fails(
    isolated_home, caplog
):
    """Another check runs (the person's, another panel's process): the one in the background
    begins none and is free to be begun again later; one that fails says nothing, the processor
    goes on converting, and it is not begun again in this run; one of the card's that gives
    nothing converting itself is a verdict: the processor."""
    isolated_home.mkdir(parents=True)
    size = (960, 320)
    with colour_check.exclusive() as free:
        assert free
        with caplog.at_level("INFO", logger="libre_panel.devices.card_choice"):
            thread = card_choice.ensure(FFMPEG, "h264_amf", False, settings_for(), size, 270,
                                        command=stand_in())  # fmt: skip
            thread.join(30)
    assert any("another check runs" in r.getMessage() for r in caplog.records)
    assert card_choice.verdict(FFMPEG, "h264_amf", False) is None
    python = shutil.which("python3") or shutil.which("python")

    def broken(ffmpeg, size, rotate, settings):
        return [python, "-c", "import sys; sys.stdin.buffer.read(); sys.exit(1)"]

    thread = card_choice.ensure(FFMPEG, "h264_amf", False, settings_for(), size, 270,
                                command=broken)  # fmt: skip
    assert thread is not None  # (free again: begun)
    thread.join(30)
    assert card_choice.verdict(FFMPEG, "h264_amf", False) is None  # failed: nothing kept
    assert card_choice.ensure(FFMPEG, "h264_amf", False, settings_for(), size, 270) is None
    thread = card_choice.ensure(FFMPEG, "h264_qsv", False, settings_for(), size, 270,
                                command=stand_in(fail=True, encoder="h264_qsv"))  # fmt: skip
    thread.join(30)
    refused = card_choice.verdict(FFMPEG, "h264_qsv", False)
    assert refused["card"] is False and refused["state"] == "refused"


# -- what the card says of who converts ----------------------------------------------------------


def test_why_the_card_converts_or_not():
    identical, shifted = {"card": True, "state": "done"}, {"card": False, "state": "done"}
    refused = {"card": False, "state": "refused"}
    assert why_of("libx264", "auto", False, None, False) == "x264"
    assert why_of("h264_nvenc", "auto", True, identical, False) == "limited-only"
    assert why_of("h264_nvenc", "card", True, None, False) == "limited-only"
    assert why_of("h264_amf", "auto", False, None, True) == "refused"
    assert why_of("h264_amf", "auto", False, refused, False) == "refused"
    assert why_of("h264_amf", "card", False, None, False) == "card"
    assert why_of("h264_amf", "rgb", False, identical, False) == "rgb"
    assert why_of("h264_amf", "auto", False, None, False) == "unchecked"
    assert why_of("h264_amf", "auto", True, identical, False) == "identical"
    assert why_of("h264_qsv", "auto", False, shifted, False) == "differs"
    assert [way_of(why, "auto") for why in ("identical", "card", "differs", "x264", "refused")] == [
        "card", "card", "processor", "processor", "processor"]  # fmt: skip
    assert way_of("rgb", "rgb") == way_of("x264", "rgb") == "rgb"
    assert in_use_of("h264_amf, rgb0") == ("h264_amf", "rgb0", False)
    assert in_use_of("h264_amf, rgb0, card") == ("h264_amf", "rgb0", True)
    assert in_use_of(None) is None and in_use_of("libx264") is None


def running_app(stats=None, more=None, model=NINE, size=(1920, 480)):
    status = SimpleNamespace(stats=stats or {}, model=model, size=size, more=more or {})
    return SimpleNamespace(panel=SimpleNamespace(status=status))


def test_the_card_says_who_converts_for_the_panel_and_why(isolated_home, monkeypatch):
    """For the encoder panel n runs (else would start), who converts and why: the processor until
    a check has spoken (and while one runs in the background), the card where it found it
    identical (with the day and who checked), the processor where it differs or gave nothing,
    for x264 and for NVIDIA's on a panel in full range; a way video.pixels forces."""
    isolated_home.mkdir(parents=True)
    monkeypatch.setattr(h264, "find_ffmpeg", lambda configured="": "/a/ffmpeg")
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: "8.1")
    stats = {"streaming": True, "encoder": "h264_amf, rgb0"}
    runner = ColourCheck(None, running_app(stats, model=EIGHT))

    def choice(**fields):
        state = runner.state(1)
        return state, {k: state["choice"][k] for k in fields} == fields

    state, same = choice(encoder="h264_amf", why="unchecked", way="processor", full=False,
                         range="auto", model_range="limited", when=None,
                         checking=False)  # fmt: skip
    assert same and state["in_use"] == {"encoder": "h264_amf", "feed": "rgb0", "card": False,
                                        "running": True}  # fmt: skip
    assert state["ab"] == "" and state["pixels"] == "auto"
    card_choice._running.add(("/a/ffmpeg", "h264_amf", False))
    try:
        assert choice(checking=True)[1]
    finally:
        card_choice._running.discard(("/a/ffmpeg", "h264_amf", False))
    card_choice.record("/a/ffmpeg", result_of(), False, by="background")
    state, same = choice(why="identical", way="card", verdict="identical", by="background")
    assert same and state["choice"]["when"] == pytest.approx(time.time(), abs=5)
    card_choice.record("/a/ffmpeg", result_of(verdict="matrix"), False)
    state, same = choice(why="differs", way="processor", verdict="matrix", by="check")
    assert same and state["choice"]["worst"]["patch"] == "green"
    path = config_dir() / "config.toml"
    for pixels, way in (("card", "card"), ("rgb", "rgb")):
        path.write_text(f'[video]\npixels = "{pixels}"\n', encoding="utf-8")
        assert choice(why=pixels, way=way, pixels=pixels)[1]
    path.write_text('[device]\nvideo_range = "full"\n', encoding="utf-8")
    assert choice(full=True, range="full", why="unchecked")[1]  # (no verdict in full range yet)
    path.unlink()
    video_state.keep_refusal("/a/ffmpeg", "h264_amf")
    assert choice(why="refused", way="processor")[1]
    x264 = ColourCheck(None, running_app({"streaming": True, "encoder": "libx264, rgb0"}))
    assert x264.state(1)["choice"]["why"] == "x264" and x264.state(1)["ab"] == "x264"
    nvenc = {"streaming": True, "encoder": "h264_nvenc, rgb0"}
    twelve = ColourCheck(None, running_app(nvenc, model=TWELVE, size=(1920, 720)))
    state = twelve.state(1)
    assert state["choice"]["why"] == "limited-only" and state["choice"]["full"]
    assert state["choice"]["model_range"] == "full" and state["ab"] == "limited-only"


@needs_ffmpeg
def test_the_check_runs_with_the_panel_s_size_and_range_and_the_choice_follows_it(isolated_home):
    """Panel 1 runs h264_amf; its card's way (x264 with BT.709 here) shifts the colours: the
    check says so and keeps it for the automatic choice (the processor converts); the next
    check, finding it the same, lets the card convert. A 12.3" is checked in full range."""
    isolated_home.mkdir(parents=True)
    ffmpeg = h264.find_ffmpeg("")
    stats = {"streaming": True, "encoder": "h264_amf, rgb0"}
    runner = ColourCheck(None, running_app(stats, model=EIGHT), works=lambda f, e: e == "h264_amf",
                         command=stand_in("scale=out_color_matrix=bt709"))  # fmt: skip
    runner.start_check(1)
    with pytest.raises(ColourError):
        runner.start_check(1)  # once at a time
    assert wait_for(lambda: not runner.running)
    state = runner.state(1)
    assert state["check"]["picture"] == {"panel": 1, "model": EIGHT, "size": [1920, 480],
                                         "full": False}  # fmt: skip
    verdicts = {r["encoder"]: r["verdict"] or r["state"] for r in state["check"]["results"]}
    assert verdicts == {"h264_amf": "matrix", "h264_nvenc": "unavailable",
                        "h264_qsv": "unavailable", "libx264": "identical"}  # fmt: skip
    assert state["choice"]["why"] == "differs" and state["choice"]["by"] == "check"
    assert "saving_ms" in state["check"] and state["video"] and state["app"]
    runner.command = stand_in()  # Check again: the card's the same now (a new driver, say)
    runner.start_check(1)
    assert wait_for(lambda: not runner.running)
    assert runner.state(1)["choice"]["why"] == "identical"
    assert card_choice.chosen(ffmpeg, "h264_amf", False)
    twelve = ColourCheck(None, running_app(stats, model=TWELVE, size=(1920, 720)),
                         works=lambda f, e: e == "h264_amf", command=stand_in())  # fmt: skip
    twelve.start_check(1)
    assert wait_for(lambda: not twelve.running)
    state = twelve.state(1)
    done = [r for r in state["check"]["results"] if r["state"] == "done"]
    assert state["check"]["picture"]["full"] and len(done) == 2 and all(r["full"] for r in done)
    assert state["choice"]["why"] == "identical" and state["choice"]["full"]
    assert card_choice.chosen(ffmpeg, "h264_amf", True)


def test_the_person_s_check_waits_for_one_in_the_background(isolated_home, monkeypatch):
    monkeypatch.setattr(h264, "find_ffmpeg", lambda configured="": "/a/ffmpeg")
    monkeypatch.setattr("libre_panel.editor.colours.WAIT_S", 0.3)
    runner = ColourCheck(None, works=lambda f, e: False)
    with colour_check.exclusive() as free:
        assert free
        runner.start_check(1)
        assert wait_for(lambda: not runner.running, timeout=10)
    assert "another colour check runs" in runner.state(1)["check"]["error"]


def test_the_card_says_what_auto_picks_and_why_a_card_s_encoder_was_passed_over(
    isolated_home, monkeypatch
):
    """The probe (the video mode's own command) keeps the last line ffmpeg said, in this run
    and a week long in video-state.json; the card shows "auto"'s pick and that line for each of
    the card's encoders it passed over, from the answers known, without asking ffmpeg itself."""
    isolated_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(h264, "_PROBED", {})
    monkeypatch.setattr(h264, "_SAID", {})
    monkeypatch.setattr(h264, "find_ffmpeg", lambda configured="": "/a/ffmpeg")
    asked = []

    def run(command, **kwargs):
        asked.append(command[command.index("-c:v") + 1])
        said = b"[h264_amf @ 0x1] AMF failed to initialise\nConversion failed!\n"
        return SimpleNamespace(returncode=1, stdout=b"", stderr=said)

    monkeypatch.setattr(subprocess, "run", run)
    runner = ColourCheck(None)
    assert runner.auto(VideoConfig())["picked"] is None and asked == []  # not asked: not known
    assert h264.encoder_works("/a/ffmpeg", "h264_amf") is False
    assert asked == ["h264_amf"]  # with the processor's conversion, as the display starts it
    auto = runner.auto(VideoConfig())
    amf = "[h264_amf @ 0x1] AMF failed to initialise"  # what tells why, not ffmpeg's last words
    assert auto == {"set": "auto", "picked": "libx264",
                    "passed": [{"encoder": "h264_amf", "said": amf}]}  # fmt: skip
    monkeypatch.setattr(h264, "_PROBED", {})  # the next run: from video-state.json
    monkeypatch.setattr(h264, "_SAID", {})
    assert runner.auto(VideoConfig())["passed"][0]["said"].endswith("AMF failed to initialise")
    said = video_state.probe_said("/a/ffmpeg", "h264_amf")
    assert said == amf
    week = time.time() + video_state.PROBE_S + 60
    assert video_state.probe_said("/a/ffmpeg", "h264_amf", now=week) == ""
    assert runner.auto(VideoConfig(encoder="h264_nvenc"))["picked"] == "h264_nvenc"  # by hand
    state = runner.state(1)
    assert state["auto"]["picked"] == "libx264" and state["in_use"]["encoder"] == "libx264"
    assert state["choice"]["why"] == "x264" and asked == ["h264_amf"]  # (not again)
    works = ColourCheck(None, works=lambda ffmpeg, encoder: encoder == "h264_amf")
    assert works.auto(VideoConfig())["picked"] == "h264_amf"
    (unavailable,) = check("/a/ffmpeg", settings_for(), (480, 480), 180, encoders=["h264_amf"])
    assert unavailable["state"] == "unavailable" and unavailable["said"] == said
    assert h264.reason("x\n[out#0/h264 @ 0x2] Nothing was written into output file\n") == "x"
    assert h264.reason("Conversion failed!\n") == "Conversion failed!" and h264.reason("") == ""


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

    monkeypatch.setattr(h264, "_probe", lambda ffmpeg, encoder: (False, "no such card here"))
    monkeypatch.setattr(h264, "_PROBED", {})
    monkeypatch.setattr(h264, "_SAID", {})
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, state = request(port, "GET", "/api/colour-check?panel=1")
        assert status == 200 and state["panel"] == 1 and not state["app"]
        assert state["check"]["results"] == [] and state["pixels"] == "auto"
        # (no panel and none chosen: the check's own, the 9.2", which shows video as full range)
        assert state["choice"]["range"] == "auto" and state["choice"]["full"] is True
        assert request(port, "GET", "/api/colour-check?panel=5")[0] == 400
        assert request(port, "POST", "/api/colour-check", {"action": "dance"})[0] == 400
        for action in ("show", "range"):
            status, answer = request(port, "POST", "/api/colour-check",
                                     {"action": action, "panel": 1})  # fmt: skip
            assert status == 409 and "running app" in answer["error"]  # a plain editor: no panel
        if FFMPEG is not None:
            status, state = request(port, "POST", "/api/colour-check", {"action": "check"})
            assert status == 200
            assert wait_for(
                lambda: not request(port, "GET", "/api/colour-check")[1]["check"]["running"]
            )
            state = request(port, "GET", "/api/colour-check")[1]
            assert [r["state"] for r in state["check"]["results"]] == [
                "unavailable", "unavailable", "unavailable", "done"]  # fmt: skip
            assert state["choice"]["why"] == "x264"  # x264 is what "auto" starts here
            assert state["auto"]["picked"] == "libx264"
            assert state["auto"]["passed"] == [{"encoder": "h264_amf", "said": "no such card here"}]
            assert state["check"]["results"][1]["said"] == "no such card here"  # NVIDIA's
    finally:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


# -- the settings and the one click ---------------------------------------------------------------


def test_the_pixels_setting_is_read_and_written_like_the_other_video_keys(isolated_home):
    from libre_panel.editor.settings import Restart, Settings, SettingsError

    isolated_home.mkdir(parents=True, exist_ok=True)
    path = config_dir() / "config.toml"
    path.write_text('[[panels]]\nmodel = "turing-9.2-usb"\nvideo = { mode = "on" }\n',
                    encoding="utf-8")  # fmt: skip
    settings = Settings(path, None, Restart())
    assert settings.read()["config"]["video"]["pixels"] == "auto"
    for wrong in ("rgb24", "rgb0", "processor", True, 1):
        with pytest.raises(SettingsError, match="video.pixels"):
            settings.write({"set": {"video.pixels": wrong}})
    settings.write({"set": {"video.pixels": "card"}})
    assert 'pixels = "card"' in path.read_text(encoding="utf-8")
    config = load_config(path)
    assert config.device.video.pixels == "card"
    assert config.for_panel(0).device.video.pixels == "card"  # the companion follows
    assert settings.read()["config"]["video"]["pixels"] == "card"
    settings.write({"set": {"video.pixels": "auto"}})  # "Back to the automatic choice"
    assert load_config(path).for_panel(0).device.video.pixels == "auto"


def test_the_video_range_is_written_for_the_panel_it_was_compared_on(isolated_home):
    """ "A looks right" / "B looks right": the first panel's video_range in [device], a panel
    beside it its own in its [[panels]] table (the first one's stays as it was)."""
    from libre_panel.editor.settings import Restart, Settings, SettingsError

    isolated_home.mkdir(parents=True, exist_ok=True)
    path = config_dir() / "config.toml"
    path.write_text('[[panels]]\nmodel = "turing-12.3-usb"\nvideo = { mode = "on" }\n',
                    encoding="utf-8")  # fmt: skip
    settings = Settings(path, None, Restart())
    read = settings.read()["config"]
    assert read["device"]["video_range"] == "auto" and read["panels"][0]["video_range"] == "auto"
    for wrong in ("tv", "pc", "", 1):
        with pytest.raises(SettingsError, match="video_range"):
            settings.write({"set": {"device.video_range": wrong}})
    settings.write({"set": {"device.video_range": "full"}})  # panel 1: "B looks right"
    assert load_config(path).device.video_range == "full"
    assert load_config(path).for_panel(0).device.video_range == "auto"  # its own
    panels = settings.read()["config"]["panels"]
    panels[0] = {**panels[0], "video_range": "limited"}  # panel 2: "A looks right"
    settings.write({"set": {"panels": panels}})
    config = load_config(path)
    assert config.for_panel(0).device.video_range == "limited"
    assert config.device.video_range == "full"
    assert 'video_range = "limited"' in path.read_text(encoding="utf-8")
    with pytest.raises(SettingsError, match=r"panels\[0\]\.video_range"):
        settings.write({"set": {"panels": [{**panels[0], "video_range": "pc"}]}})


def test_a_new_feed_or_range_is_followed_on_the_open_panel():
    """video.pixels and video_range change how ffmpeg makes the video, not the panel: the loops
    follow them without opening the panel again (the video display starts a new ffmpeg)."""
    a = DeviceConfig(video=VideoConfig(mode="on", local_clip="x.h264"))
    b = DeviceConfig(video=VideoConfig(mode="on", local_clip="x.h264", pixels="rgb"))
    assert app._same_device(a, b)
    assert app._same_device(a, replace(a, video_range="full"))
    assert not app._same_device(a, replace(b, video=replace(b.video, crf=18)))
    beside = parse_config({"video": {"pixels": "rgb"}, "panels": [{"model": NINE}]})
    assert beside.for_panel(0).device.video.pixels == "rgb"


class FeedPanel(Display):
    """A streaming panel that keeps each frame, and the feed and range it was to go in with."""

    streaming = True
    stream_fps = 50

    def __init__(self, config):
        super().__init__(config)
        self.shown = []
        self.feeds = []

    def show(self, frame, region=None):
        self.shown.append(frame)
        self.feeds.append((self.feed, self.range, self.config.video.pixels,
                           self.config.video_range))  # fmt: skip


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
    assert displays[-1].feeds[-1] == (None, None, "auto", "auto")

    def until(index, value):
        end = time.monotonic() + 30  # (the loop warms the theme up again, in a thread)
        while displays[-1].feeds[-1][index] != value and time.monotonic() < end:
            clock.now += 0.1
            loop.step()
            time.sleep(0.01)

    edit_config(("value", "video.pixels", "rgb"), path=path)
    until(2, "rgb")
    assert displays[0].feeds[-1] == (None, None, "rgb", "auto")
    text = path.read_text(encoding="utf-8")  # its own range (Settings writes the table whole)
    path.write_text(text.replace('video = { mode = "on" }',
                                 'video = { mode = "on" }\nvideo_range = "full"'),
                    encoding="utf-8")  # fmt: skip
    until(3, "full")
    assert len(displays) == 1  # the same panel, not opened again
    assert displays[0].feeds[-1] == (None, None, "rgb", "full")
    loop.close()


# -- the A/B on the panel --------------------------------------------------------------------------


def test_the_a_b_shows_the_picture_both_ways_and_then_the_theme_again(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(colour_show, "clock", lambda: now[0])
    config = Config(theme="pico", sensors=SensorsConfig(providers=["demo"]))
    loop, displays, _clock = loop_on(config)
    panel = displays[0]
    loop.step()
    theme = panel.shown[-1]
    colour_show.begin(2)  # another panel's: this one goes on
    loop.step()
    assert panel.feeds[-1][:2] == (None, None) and panel.shown[-1].tobytes() == theme.tobytes()
    colour_show.begin(1, seconds=30)
    loop.step()
    assert panel.feeds[-1][:2] == ("card", None)
    assert panel.shown[-1] is check_picture(theme.size, "A")
    now[0] += colour_show.SWITCH_S
    loop.step()
    assert panel.feeds[-1][:2] == ("processor", None)
    assert panel.shown[-1] is check_picture(theme.size, "B")
    now[0] += colour_show.SWITCH_S
    loop.step()
    assert panel.feeds[-1][:2] == ("card", None)
    colour_show.begin(1, seconds=30, kind="range")  # Is black too light? Compare
    loop.step()
    assert panel.feeds[-1][:2] == (None, "limited")
    now[0] += colour_show.SWITCH_S
    loop.step()
    assert panel.feeds[-1][:2] == (None, "full")
    assert panel.shown[-1] is check_picture(theme.size, "B")
    now[0] += 30  # over by its time
    loop.step()
    assert panel.feeds[-1][:2] == (None, None) and panel.shown[-1].size == theme.size
    assert panel.shown[-1] is not check_picture(theme.size, "A")
    with pytest.raises(ValueError, match="kind"):
        colour_show.begin(1, kind="gamma")
    loop.close()


def test_a_display_of_a_plugin_without_a_feed_shows_the_picture_all_the_same(monkeypatch):
    """A streaming display that is no Display (a plugin's) has no set_feed nor set_range: it
    gets the picture, and the loop goes on."""

    class Plain:
        streaming, stream_fps, restarted = True, 50, False

        def __init__(self):
            self.frames = []

        def open(self):
            pass

        def set_brightness(self, percent):
            pass

        def describe(self):
            return "memory"

        def show(self, frame, region=None):
            self.frames.append(frame)

        def close(self):
            pass

    from test_app_cli import StepClock

    display = Plain()
    config = Config(theme="pico", sensors=SensorsConfig(providers=["demo"]))
    loop = app.MainLoop(config, display=display, clock=StepClock(1000.0))
    for kind in ("feed", "range"):
        colour_show.begin(1, seconds=30, kind=kind)
        loop.step()
        assert display.frames[-1] is check_picture(display.frames[-1].size, "A")
    loop.close()


def test_a_panel_in_a_process_of_its_own_is_told_of_the_a_b(monkeypatch):
    now = [50.0]
    monkeypatch.setattr(colour_show, "clock", lambda: now[0])
    colour_show.begin(2, seconds=10)
    told = colour_show.export()
    assert told == {"panel": 2, "kind": "feed", "letter": "A", "value": "card", "left_s": 10}
    colour_show.end()
    assert colour_show.now(2) is None and colour_show.export() is None
    colour_show.adopt(told)  # (in the panel's process)
    assert colour_show.now(2) == ("A", "feed", "card") and colour_show.now(1) is None
    now[0] += 11  # the first panel's process said nothing more: it ends by itself
    assert colour_show.now(2) is None
    colour_show.adopt({"panel": 2, "kind": "range", "letter": "B", "value": "full", "left_s": 4})
    assert colour_show.now(2) == ("B", "range", "full")
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
    assert seen == [(("A", "feed", "card"), True)]  # it showed, the marker lay there
    assert colour_show.now(1) is None and colour_show.state() is None  # as before
    assert not marker.exists()
    state = runner.state(1)
    assert state["show"]["error"] == "the panel went away" and not state["show"]["running"]
    assert state["show"]["kind"] == "feed" and state["show"]["of"] == 1
    # the app ended while it showed (no finally ran): the next start finds the marker
    colour_show.begin(1, kind="range")
    assert marker.exists()
    colour_show._own = None  # (what a process that ended keeps of it)
    assert colour_show.recover() == {"panel": 1, "kind": "range",
                                     "began": pytest.approx(time.time(), abs=60)}  # fmt: skip
    assert not marker.exists() and colour_show.recover() is None


def test_the_a_b_is_only_for_a_panel_that_shows_video_and_where_it_differs(
    isolated_home, monkeypatch
):
    """No A/B without video, or without the running app; the card's conversion against the
    processor's only for an encoder the card converts for (not x264, not NVIDIA's on a panel in
    full range); limited against full range for any encoder."""
    runner = ColourCheck(None, running_app({"streaming": False}))
    for kind in ("feed", "range"):
        with pytest.raises(ColourError, match="no video"):
            runner.start_show(1, kind)
    assert not runner.state(1)["video"]
    with pytest.raises(ColourError, match="running app"):
        ColourCheck(None, None).start_show(1)
    monkeypatch.setattr(ColourCheck, "TICK_S", 0.01)
    runner = ColourCheck(None, running_app({"streaming": True, "encoder": "libx264, rgb0"}))
    with pytest.raises(ColourError, match="processor converts"):
        runner.start_show(1)  # (A and B would be the same)
    with pytest.raises(ColourError, match="kind"):
        runner.start_show(1, "gamma")
    monkeypatch.setattr(colour_show, "SHOW_S", 0.3)
    runner.start_show(1, "range")
    state = runner.state(1)
    assert state["show"]["panel"] == 1 and state["show"]["kind"] == "range"
    assert state["show"]["letter"] == "A" and state["show"]["value"] == "limited"
    with pytest.raises(ColourError, match="already"):
        runner.start_show(1, "range")
    runner._show_thread.join(10)
    assert runner.state(1)["show"]["ended"] == "done" and colour_show.state() is None
    runner.start_show(1, "range")
    runner.stop_show()
    runner._show_thread.join(10)
    assert runner.state(1)["show"]["ended"] == "stopped"
    nvenc = {"streaming": True, "encoder": "h264_nvenc, rgb0"}
    twelve = ColourCheck(None, running_app(nvenc, model=TWELVE, size=(1920, 720)))
    with pytest.raises(ColourError, match="processor converts"):
        twelve.start_show(1)  # full range: NVIDIA's makes it on the processor only
    twelve.start_show(1, "range")
    twelve.stop_show()
    twelve._show_thread.join(10)


# -- the card's encoder that gave no picture converting itself -------------------------------------


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


@pytest.mark.parametrize("pixels", ["card", "auto"])
def test_the_next_connection_starts_a_card_that_refused_with_the_processor_s_conversion(
    isolated_home, monkeypatch, pixels
):
    """A card's encoder that ended before a picture converting the frames itself: kept, and the
    next connection starts it with the processor's conversion at once, whether video.pixels
    forces the card or "auto" had chosen it; a colour check's A does not change that."""
    pytest.importorskip("usb")
    from libre_panel.devices import turzx_video

    isolated_home.mkdir(parents=True, exist_ok=True)
    started = []

    class FakeEncoder:
        def __init__(self, command, size, deliver):
            started.append(command)
            self.size, self.pictures, self.alive = size, 0, False

    monkeypatch.setattr(turzx_video, "Encoder", FakeEncoder)
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: "8.1")
    card_choice.record("/a/ffmpeg", result_of(), False)  # (found identical: "auto" lets it)
    video = VideoConfig(mode="on", local_clip="x.h264", encoder="h264_amf", pixels=pixels)
    device = DeviceConfig(driver="turzx", model=NINE, video=video)

    def connection():
        display = turzx_video.TurzxVideoDisplay(device)
        display._ffmpeg = "/a/ffmpeg"
        return display

    def by_card(command):
        return "-init_hw_device" in command and "nv12" not in command[command.index("-c:v") :]

    first = connection()
    first._start_encoder((1920, 480))
    assert by_card(started[-1]) and after(started[-1], "-pix_fmt") == "rgb0"
    assert first._gave_up("exit code 1") is True  # ended before a picture, converting itself
    assert video_state.refused("/a/ffmpeg", "h264_amf")
    second = connection()  # the next connection: no try of the card's way first
    second._start_encoder((1920, 480))
    assert not by_card(started[-1]) and after(started[-1], "-pix_fmt") == "rgb0"
    assert second._feeds_failed == {"h264_amf"}
    second.set_feed("card")  # a colour check's A: the card refused it
    assert not second._feed_changed()


def test_a_new_way_starts_a_new_ffmpeg_and_the_check_s_picture_does_not_stay(monkeypatch):
    pytest.importorskip("usb")
    from libre_panel.devices import turzx_video

    video = VideoConfig(mode="on", local_clip="x.h264", encoder="h264_amf", pixels="card")
    device = DeviceConfig(driver="turzx", model=NINE, video=video)
    display = turzx_video.TurzxVideoDisplay(device)
    display._ffmpeg = "/a/ffmpeg"
    monkeypatch.setattr(video_state, "refused", lambda ffmpeg, encoder: False)
    settings = h264.EncoderSettings(encoder="h264_amf", pixels="rgb0", card=True)
    display.encoder = SimpleNamespace(settings=settings)
    assert not display._feed_changed()
    display.set_feed("processor")  # B of the A/B
    assert display._feed_changed()
    display.set_feed(None)
    display.set_range("full")  # B of the range's A/B
    assert display._feed_changed()
    display.set_range(None)
    video.pixels = "rgb"  # a click, followed by the loop
    assert display._feed_changed()
    video.pixels = "card"
    assert not display._feed_changed()
    device.video_range = "full"  # "B looks right", followed by the loop
    assert display._feed_changed()
    device.video_range = "auto"
    shown = []
    display.transport, display.session = object(), object()
    monkeypatch.setattr(display, "_hand_over", lambda: None)
    monkeypatch.setattr(display, "_follow_the_panel", lambda: None)
    monkeypatch.setattr(display, "_feed", shown.append)
    display.encoder = SimpleNamespace(size=(1920, 480), alive=True, pictures=1, settings=settings)
    theme = Image.new("RGB", (1920, 480), "navy")
    display.show(theme)
    display.set_feed("card")
    display.show(check_picture((1920, 480), "A"))
    display.set_feed(None)
    display.set_range("limited")
    display.show(check_picture((1920, 480), "A"))
    assert display._last_frame is theme  # what stays on the panel when it closes
    assert len(shown) == 3

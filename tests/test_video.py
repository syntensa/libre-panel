"""Video mode: H.264 pictures, the video session, the display and the main loop's pacing."""

import itertools
import json
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

pytest.importorskip("Crypto")
pytest.importorskip("usb")

from fake_panel import FakePanel, transport_for  # noqa: E402

from libre_panel import app  # noqa: E402
from libre_panel.config import (  # noqa: E402
    Config,
    ConfigError,
    DeviceConfig,
    SensorsConfig,
    VideoConfig,
    parse_config,
)
from libre_panel.devices import h264, turzx_usb, turzx_video, video_state  # noqa: E402
from libre_panel.devices.base import Counters, DeviceError, Display  # noqa: E402
from libre_panel.devices.h264 import (  # noqa: E402
    NAL_IDR,
    EncoderSettings,
    EncoderStopped,
    PictureSplitter,
    RateControl,
    ffmpeg_command,
    find_ffmpeg,
    mbit,
    nal_units,
    slice_count,
)
from libre_panel.devices.turzx_video import (  # noqa: E402
    DecoderHung,
    PanelRestarting,
    TurzxVideoDisplay,
    VideoSession,
    transparent_layer,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg with libx264")

# Commands that store something on the panel, restart or halt it: never in video mode.
NEVER = {11, 12, 13, 38, 39, 40, 42, 125}


def nal(kind, body=b"\x88\x84", four=False):
    return (b"\x00\x00\x00\x01" if four else b"\x00\x00\x01") + bytes([0x60 | kind]) + body


def synthetic_stream():
    """keyframe with SPS/PPS/SEI, then two plain pictures (first_mb_in_slice = 0: 0x80)."""
    first = nal(7, b"\x42\xc0\x1f", True) + nal(8, b"\xce\x3c", True) + nal(6, b"\x05\x10")
    first += nal(5, b"\x88\x84\x00\x00\x03\x00\x80")  # emulation prevention inside
    return [first, nal(1, b"\x9a\x00\x10"), nal(1, b"\x9a\x00\x20", True)]


def feed_in_pieces(stream, sizes):
    splitter, pictures, pos = PictureSplitter(), [], 0
    for size in itertools.cycle(sizes):
        if pos >= len(stream):
            break
        pictures += splitter.feed(stream[pos : pos + size])
        pos += size
    return pictures + splitter.flush()


@pytest.mark.parametrize("sizes", [[1], [2], [3], [5, 1], [4096], [1, 7, 2]])
def test_splitter_cuts_pictures_however_the_pipe_splits_bytes(sizes):
    pictures = synthetic_stream()
    assert feed_in_pieces(b"".join(pictures), sizes) == pictures


def test_splitter_notices_pictures_in_several_slices():
    splitter = PictureSplitter()
    splitter.feed(nal(5) + nal(1, b"\x40\x00") + nal(1))  # 0x40: first_mb_in_slice = 1
    assert splitter.multi_slice


def test_nal_units_and_slices():
    stream = b"".join(synthetic_stream())
    assert [kind for _, kind in nal_units(stream)] == [7, 8, 6, 5, 1, 1]
    assert slice_count(stream) == 3


def test_ffmpeg_command_follows_the_measured_settings():
    cmd = ffmpeg_command("ffmpeg", (1920, 480), 270, EncoderSettings(fps=50, keyframe_s=10))
    text = " ".join(cmd)
    assert "-s 1920x480" in text and "-vf transpose=1" in text
    assert "sliced-threads=0" in text and "keyint=500:min-keyint=500" in text
    assert "-profile:v high" in text and "repeat-headers=1" in text and "-crf 20" in text
    assert "hflip,vflip" in " ".join(ffmpeg_command("ffmpeg", (480, 1920), 180, EncoderSettings()))


def test_the_bit_rate_grows_with_the_pixels_and_the_frame_rate():
    from libre_panel.devices.h264 import bit_rate

    auto = EncoderSettings()
    assert bit_rate(auto, (1920, 480)) == "2.0M"  # the 9.2", as measured
    assert bit_rate(auto, (1920, 720)) == "3.0M"  # the 12.3": half as many pixels again
    assert bit_rate(auto, (480, 480)) == "2.0M"  # a small panel keeps the measured rate
    assert bit_rate(EncoderSettings(fps=25), (1920, 720)) == "2.0M"  # half the frames
    assert bit_rate(EncoderSettings(maxrate="1500k"), (1920, 720)) == "1500k"  # your own
    assert "-maxrate 3.0M -bufsize 3.0M" in " ".join(
        ffmpeg_command("ffmpeg", (1920, 720), 270, auto)
    )


def test_auto_rate_follows_the_panel_s_queue():
    """ "auto" steps down while the sender keeps waiting for room in the panel's queue, up
    again after a calm minute, and holds a failed step off: ten minutes, then twenty."""
    control, c = RateControl(2.0), Counters()
    assert control.judge(c, 0.0) is None  # the first window begins
    assert all(control.judge(c, float(second)) is None for second in range(1, 60))
    assert control.judge(c, 60.0) == 3.0  # a calm minute: a step up
    c.waits += RateControl.TROUBLE_WAITS  # within the window: the panel's decoder lags
    assert control.judge(c, 61.0) == 2.0  # back down, and 3.0 is held off
    assert all(control.judge(c, float(second)) is None for second in range(62, 661))
    assert control.rate == 2.0  # a calm while, but 3.0 failed lately
    assert control.judge(c, 661.0) == 3.0  # ten minutes later it is tried again
    c.blocks += 100
    c.slow_blocks += RateControl.TROUBLE_SLOW  # a quarter of the blocks slow: a busy PC
    assert control.judge(c, 662.0) is None
    c.slow_blocks += RateControl.TROUBLE_SLOW  # half of them: the panel's link lags
    assert control.judge(c, 664.0) == 2.0
    assert control.judge(c, 1000.0) is None  # now held off for twenty minutes
    assert control.judge(c, 1900.0) == 3.0
    assert control.changes == 5

    climbing, calm = RateControl(2.0), Counters()  # calm all along: up the steps to the top
    assert climbing.judge(calm, 0.0) is None
    seen = [climbing.judge(calm, float(second)) for second in range(1, 600)]
    assert [rate for rate in seen if rate] == [3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0]

    floor = RateControl(3.0)  # the start is the floor: below it the frames are the trouble
    assert floor.steps[0] == 3.0 and floor.judge(calm, 0.0) is None
    calm.waits += 9
    assert floor.judge(calm, 1.0) is None and floor.rate == 3.0 and floor.floored

    odd = RateControl(2.4)  # a start between the steps is a step of its own
    assert odd.steps == [2.4, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0]

    assert mbit("auto") == 0.0 and mbit("1500k") == 1.5 and mbit("4M") == 4.0
    assert mbit("2000000") == 2.0 and mbit("2.5M") == 2.5


def test_the_profile_and_the_encoder_of_auto_are_resolved_per_panel_and_pc(monkeypatch):
    from libre_panel.devices.h264 import encoder_works, resolve_encoder, resolve_profile

    assert resolve_profile("auto", "turing-12.3-usb") == "high"
    assert resolve_profile("auto", "turing-9.2-usb") == "high"
    assert resolve_profile("auto", "turing-8.8-usb") == "baseline"  # not measured there
    assert resolve_profile("main", "turing-12.3-usb") == "main"
    probed = []

    def works(ffmpeg, encoder):
        probed.append(encoder)
        return encoder == "h264_amf" and ffmpeg == "with-card"

    monkeypatch.setattr(h264, "encoder_works", works)
    assert resolve_encoder("auto", "with-card") == "h264_amf"
    assert resolve_encoder("auto", "without") == "libx264"
    assert resolve_encoder("auto", "with-card", failed={"h264_amf"}) == "libx264"  # it stopped
    assert resolve_encoder("h264_nvenc", "without") == "h264_nvenc" and probed  # as set
    monkeypatch.setattr(h264, "_PROBED", {})
    assert encoder_works("/no/such/ffmpeg", "h264_amf") is False  # no ffmpeg: no
    assert h264._PROBED == {("/no/such/ffmpeg", "h264_amf"): False}  # asked once


def test_a_new_run_starts_at_the_rate_the_last_one_got_to(isolated_home):
    """video-state.json keeps the rate "auto" got to per panel; a new run starts there, on the
    steps: never below its start, never above the top, and between two steps at the lower."""
    assert RateControl(2.0, 8.0).rate == 8.0
    assert RateControl(3.0, 2.0).rate == 3.0  # below the start: the start
    assert RateControl(2.0, 64.0).rate == 32.0  # above the top: the top
    assert RateControl(2.0, 5.0).rate == 4.0  # between the steps: the one below
    assert RateControl(2.4, 2.5).rate == 2.4 and RateControl(2.0).rate == 2.0
    key = ("turing-9.2-usb", "usb:1.9", (1920, 480))
    video_state.keep_rate(key, 6.0)  # no folder of config.toml yet: nothing is kept
    assert video_state.rate(key) == 0.0 and not isolated_home.exists()
    isolated_home.mkdir()
    other = ("turing-12.3-usb", "usb:1.4", (1920, 720))  # another panel, each for itself
    video_state.keep_rate(key, 6.0)
    video_state.keep_rate(other, 8.0)
    assert video_state.rate(key) == 6.0 and video_state.rate(other) == 8.0
    assert video_state.rate((*key[:2], (480, 1920))) == 0.0  # another picture size
    (isolated_home / video_state.STATE_FILENAME).write_text("{not json", encoding="utf-8")
    assert video_state.rate(key) == 0.0  # a file that cannot be read counts as empty


def test_a_probe_feeds_what_the_video_mode_feeds_and_counts_the_pictures(
    isolated_home, monkeypatch
):
    """The probe runs the video mode's own command (four bytes a pixel, a size every card's
    encoder takes) and counts H.264 coming out, not ffmpeg's exit code: a card's encoder may end
    with an error after it encoded. An answer kept by the way of asking of before is asked again.
    """
    from libre_panel.devices.h264 import encoder_works

    isolated_home.mkdir()
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        size = command[command.index("-s") + 1]
        assert command[command.index("-pix_fmt") + 1] == "rgb0" and size == "480x480"
        assert len(kwargs["input"]) == 480 * 480 * 4 * h264.PROBE_FRAMES
        return SimpleNamespace(returncode=1, stdout=b"\x00\x00\x00\x01\x67\x64", stderr=b"x\n")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(h264, "_PROBED", {})
    video_state.keep_probe("ffmpeg-here", "h264_amf", False)  # an answer of the old way
    state = json.loads((isolated_home / video_state.STATE_FILENAME).read_text(encoding="utf-8"))
    del state["encoders"]["h264_amf ffmpeg-here"]["way"]
    (isolated_home / video_state.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    assert encoder_works("ffmpeg-here", "h264_amf") is True and len(seen) == 1  # asked anew

    def silent(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 20, output=b"", stderr=b"no device\n")

    monkeypatch.setattr(subprocess, "run", silent)
    monkeypatch.setattr(h264, "_PROBED", {})
    assert encoder_works("ffmpeg-there", "h264_nvenc") is False  # nothing came: it does not


def test_a_card_s_encoder_is_probed_with_the_processor_s_conversion(isolated_home, monkeypatch):
    """The probe runs what the video mode runs by default: four bytes a pixel that ffmpeg turns
    into NV12 for a card's encoder too, as it is told BT.601 in limited range. A card that would
    give nothing converting them itself is "auto"'s all the same (that is "card", which the
    colour check tries), and nothing about its own conversion is kept by the probe."""
    from libre_panel.devices.h264 import encoder_works

    isolated_home.mkdir()
    fed = []

    def run(command, **kwargs):
        fed.append(command)
        return SimpleNamespace(returncode=0, stdout=b"\x00\x00\x00\x01\x67", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(h264, "_PROBED", {})
    assert encoder_works("ffmpeg-here", "h264_amf") is True and len(fed) == 1
    (command,) = fed
    assert after(command, "-pix_fmt") == "rgb0" and "nv12" in command
    assert after(command, "-colorspace") == "smpte170m" and after(command, "-color_range") == "tv"
    assert video_state.refused("ffmpeg-here", "h264_amf") is False


def test_the_answer_of_a_probe_is_kept_for_a_week(isolated_home, monkeypatch):
    """Whether ffmpeg can start a card's encoder is asked once, kept in video-state.json, and
    taken by the next runs for a week; then it is asked again (a new driver may change it)."""
    from libre_panel.devices.h264 import encoder_works

    isolated_home.mkdir()
    asked = []

    def run(command, **kwargs):
        asked.append((command[0], command[command.index("-c:v") + 1]))
        return SimpleNamespace(returncode=0, stdout=b"\x00\x00\x00\x01\x67", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(h264, "_PROBED", {})
    assert encoder_works("ffmpeg-here", "h264_amf") is True
    assert asked == [("ffmpeg-here", "h264_amf")]
    monkeypatch.setattr(h264, "_PROBED", {})  # the next run
    assert encoder_works("ffmpeg-here", "h264_amf") is True and len(asked) == 1  # not asked
    eight_days = time.time() - video_state.PROBE_S - 24 * 3600
    video_state.keep_probe("ffmpeg-here", "h264_amf", False, now=eight_days)
    monkeypatch.setattr(h264, "_PROBED", {})
    assert encoder_works("ffmpeg-here", "h264_amf") is True and len(asked) == 2  # asked again
    assert video_state.probed("ffmpeg-here", "h264_amf") is True  # and kept anew
    video_state.keep_probe("ffmpeg-here", "h264_amf", False, now=time.time() - 3600)
    monkeypatch.setattr(h264, "_PROBED", {})
    assert encoder_works("ffmpeg-here", "h264_amf") is False and len(asked) == 2  # within the week
    assert encoder_works("ffmpeg-elsewhere", "h264_amf") and len(asked) == 3  # each for itself
    future = time.time() + 3600  # the clock went back since: asked again
    video_state.keep_probe("ffmpeg-here", "h264_amf", False, now=future)
    assert video_state.probed("ffmpeg-here", "h264_amf") is None


@needs_ffmpeg
def test_auto_takes_x264_where_no_card_encodes_and_leaves_an_encoder_that_stops(panel, monkeypatch):
    """ffmpeg here has no card: "auto" probes AMD's encoder once and encodes with x264. An
    encoder "auto" chose that stops before its first picture is left out from then on,
    uncounted as a restart."""
    monkeypatch.setattr(h264, "_PROBED", {})
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        frames = moving_frames(4)
        display.show(frames[0])
        assert "libx264" in display.encoder.command and display.encoder.name == "libx264"
        assert h264._PROBED == {(display._ffmpeg, "h264_amf"): False} or h264._PROBED == {}
        # as if "auto" had chosen a card's encoder that then stopped at once
        display.encoder.name = "h264_amf"
        display.encoder.pictures = 0
        assert display._gave_up(EncoderStopped("no device")) is True
        assert display._encoders_failed == {"h264_amf"} and display.counters.ffmpeg_restarts == 0
        display.encoder.pictures = 3  # one that showed pictures and then stopped is a restart
        assert display._gave_up(EncoderStopped("gone")) is False
    finally:
        display.close()


def test_find_ffmpeg(tmp_path, monkeypatch):
    with pytest.raises(DeviceError, match="ffmpeg not found"):
        find_ffmpeg(str(tmp_path / "missing"))
    program = tmp_path / "ffmpeg"
    program.write_text("")
    assert find_ffmpeg(str(program)) == str(program)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("sys.executable", str(tmp_path / "python"))
    assert find_ffmpeg() == str(program)  # next to Libre Panel (release downloads)
    monkeypatch.setattr("sys.executable", str(tmp_path / "elsewhere" / "python"))
    with pytest.raises(DeviceError, match="needs ffmpeg"):
        find_ffmpeg()


def test_video_config():
    video = parse_config({"video": {"mode": "on", "fps": 30, "device_fps": 40}}).device.video
    assert (video.mode, video.fps, video.device_fps, video.crf) == ("on", 30, 40, 20)
    assert parse_config({}).device.video.mode == "auto"
    assert parse_config({"video": {"mode": "off"}}).device.video.mode == "off"
    assert parse_config({"video": {"maxrate": "auto"}}).device.video.maxrate == "auto"
    for bad in ({"mode": "always"}, {"fps": 50, "device_fps": 40}, {"maxrate": "fast"}):
        with pytest.raises(ConfigError):
            parse_config({"video": bad})
    assert (video.profile, video.encoder) == ("auto", "auto")
    tried = parse_config({"video": {"profile": "high", "encoder": "h264_amf"}}).device.video
    assert (tried.profile, tried.encoder) == ("high", "h264_amf")
    for bad in ({"profile": "main10"}, {"encoder": "h264_videotoolbox"}):
        with pytest.raises(ConfigError):
            parse_config({"video": bad})


def test_profiles_and_hardware_encoders_are_an_experiment():
    """The command takes the profile and the encoder of the settings; a hardware encoder
    gets the nearest of x264's: no B-frames, the keyframe spacing, the cap on the rate."""
    size, keyint = (1920, 720), 500

    def command(**changes):
        return ffmpeg_command("ffmpeg", size, 270, EncoderSettings(**changes))

    high = command(profile="high")
    assert high[high.index("-profile:v") + 1] == "high" and "libx264" in high
    for encoder in ("h264_nvenc", "h264_amf", "h264_qsv"):
        got = command(encoder=encoder, profile="main")
        assert got[got.index("-c:v") + 1] == encoder and "-x264-params" not in got
        assert got[got.index("-profile:v") + 1] == "main"
        assert got[got.index("-bf") + 1] == "0" and got[got.index("-g") + 1] == str(keyint)
        assert got[got.index("-maxrate") + 1] == "3.0M" and got[-3:] == ["-f", "h264", "-"]
    amf = command(encoder="h264_amf", profile="baseline")  # AMD names baseline differently
    assert amf[amf.index("-profile:v") + 1] == "constrained_baseline"
    with pytest.raises(ValueError):
        command(encoder="h264_videotoolbox")


def test_auto_video_mode_needs_a_proven_panel_the_clip_name_and_ffmpeg(tmp_path):
    """``auto`` turns the video mode on only where all is there; it never makes
    up the clip name, which the start command copies into the panel's settings."""
    from libre_panel.devices.models import get_model
    from libre_panel.devices.turzx import video_mode

    nine, eight = get_model("turing-9.2-usb"), get_model("turing-8.8-usb")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("")
    ready = VideoConfig(local_clip="usr/data/clip.h264", ffmpeg=str(ffmpeg))
    assert ready.mode == "auto"
    assert video_mode(ready, nine) == (True, [])
    assert video_mode(ready, None) == (False, [])  # no panel found: nothing to tell
    on, why = video_mode(ready, eight)
    assert not on and why == ['the video mode is not proven on the Turing 8.8" yet']
    on, why = video_mode(VideoConfig(ffmpeg=str(tmp_path / "missing")), nine)
    assert not on and "local_clip is not set" in why[0] and "video.ffmpeg" in why[1]
    assert str(tmp_path) not in why[1]  # doctor reports are shared: no paths
    # "on" and "off" decide alone
    assert video_mode(VideoConfig(mode="on"), eight) == (True, [])
    assert video_mode(VideoConfig(mode="off", local_clip="x", ffmpeg=str(ffmpeg)), nine) == (
        False,
        [],
    )


def test_what_the_video_mode_lacks_is_told_in_codes_too(tmp_path):
    """The app's settings word the gaps themselves; the texts of ``video_mode`` come from them."""
    from libre_panel.devices.models import get_model
    from libre_panel.devices.turzx import video_gaps, video_mode

    nine, eight = get_model("turing-9.2-usb"), get_model("turing-8.8-usb")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("")
    missing = VideoConfig(ffmpeg=str(tmp_path / "missing"))
    assert video_gaps(VideoConfig(local_clip="x", ffmpeg=str(ffmpeg)), nine) == []
    assert video_gaps(missing, nine) == ["clip", "ffmpeg-path"]
    assert video_gaps(missing, eight) == ["model"]  # the panel decides before anything else
    assert video_gaps(missing, eight, proven=False) == ["clip", "ffmpeg-path"]  # "on" asks for no
    assert video_gaps(missing, None) == ["clip", "ffmpeg-path"]  # proof: there is no panel yet
    assert len(video_mode(missing, nine)[1]) == len(video_gaps(missing, nine))


def test_the_clip_name_is_asked_for_only_where_it_is_all_that_auto_lacks(tmp_path):
    """The app asks once for the standby clip's name, which is never made up: where the video
    mode is proven, ffmpeg is there and only the name is missing, in ``auto``, and the person
    has not answered. The USB bus is looked at last, and only then."""
    from libre_panel.devices.models import get_model
    from libre_panel.devices.turzx import VENDOR_CLIP, clip_question

    nine, eight = get_model("turing-9.2-usb"), get_model("turing-8.8-usb")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("")
    looked = []

    def asked(model="auto", found=nine, driver="auto", **video):
        video = {"ffmpeg": str(ffmpeg), **video}
        device = DeviceConfig(model=model, driver=driver, video=VideoConfig(**video))

        def on_the_bus():
            looked.append(1)
            return found

        return clip_question(device, on_the_bus)

    assert VENDOR_CLIP == "usr/data/standby.h264"
    assert asked() is True and asked(driver="turzx") is True  # the panel on the bus
    assert asked(model="turing-9.2-usb", found=None) is True  # or the one that is named
    assert asked(model="turing-8.8-usb") is False  # the named panel counts, not the bus
    assert asked(found=eight) is False and asked(found=None) is False  # not proven, or none
    assert asked(ffmpeg=str(tmp_path / "missing")) is False  # ffmpeg first
    assert asked(mode="on") is False and asked(mode="off") is False  # they decide alone
    assert asked(driver="virtual") is False and asked(driver="plugin") is False
    assert asked(local_clip="usr/data/clip.h264") is False  # a name is there
    assert asked(clip_asked=True) is False  # answered, whichever way
    # the bus is looked at only when nothing else says no
    looked.clear()
    assert not asked(mode="off") and not asked(local_clip="x") and not asked(clip_asked=True)
    assert not asked(driver="virtual") and not asked(model="turing-9.2-usb", mode="on")
    assert looked == []
    assert asked() is True and looked == [1]


def test_why_the_video_mode_is_off_is_logged_once_while_auto_looks_for_the_panel(
    tmp_path, monkeypatch, caplog
):
    """The default driver looks for a panel every 5 s, with a new display each time. A panel
    that is there but held by another program (the TURZX app) leaves the video mode off for
    the same reason at every look: the log says it once, not every 5 s for hours."""
    import logging

    from libre_panel.devices import auto, serial_link, turzx
    from libre_panel.devices.models import get_model

    caplog.set_level(logging.INFO, logger="libre_panel.devices")
    monkeypatch.setattr(turzx, "_told", "")  # nothing said before this test
    nine = get_model("turing-9.2-usb")

    def busy(pid=None, beside=()):
        raise DeviceError("the panel is in use by another program")

    monkeypatch.setattr(turzx_usb, "present_model", lambda *_: nine)
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(busy))
    monkeypatch.setattr(serial_link, "find_port", lambda *args, **kwargs: None)
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("")  # found: only the clip's name is missing
    device = DeviceConfig(output=str(tmp_path / "frame.png"), video=VideoConfig(ffmpeg=str(ffmpeg)))
    now = [0.0]
    display = auto.AutoDisplay(device, clock=lambda: now[0])
    display.open()
    for _ in range(4):
        now[0] += auto.AutoDisplay.PROBE_INTERVAL_S + 0.1
        display.show(Image.new("RGB", (8, 8)))
    display.close()
    told = [r.getMessage() for r in caplog.records if r.getMessage().startswith("video mode off")]
    why = turzx.video_mode(device.video, nine)[1]
    assert told == [f"video mode off: {why[0]}"] and "local_clip" in why[0]


# -- the session against the simulated panel ------------------------------------


@pytest.fixture
def panel(monkeypatch):
    fake = FakePanel()

    def open_transport(pid=None, beside=()):
        if not fake.present:
            raise DeviceError("no Turing/TURZX USB panel found")
        return transport_for(fake)

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(open_transport))
    monkeypatch.setattr(turzx_usb.UsbTransport, "present", staticmethod(lambda pid: fake.present))
    monkeypatch.setattr(turzx_video, "RESTART", turzx_video._Restart())  # no count from before
    monkeypatch.setattr(turzx_video, "RATES", {})  # no rate found before (calm since long ago)
    return fake


def session_for(panel, **kwargs):
    options = dict(device_fps=60, brightness=40, local_clip="usr/data/clip.h264")
    options.update(kwargs)
    return VideoSession(transport_for(panel), panel.native, **options)


def test_start_sends_only_display_commands(panel):
    session = session_for(panel)
    session.start()
    assert panel.commands == [110, 111, 112, 14, 102, 15, 17]
    assert panel.clip_names == ["usr/data/clip.h264"]
    assert panel.fps_log == [60] and panel.brightness == [40]
    layer = panel.frames[0]
    assert layer.mode == "RGBA" and layer.getextrema()[3] == (0, 0)  # fully transparent
    assert session.block_size == 202752  # the panel answers 17 with 0


def test_start_refuses_an_empty_clip_name(panel):
    with pytest.raises(DeviceError, match="local_clip"):
        session_for(panel, local_clip="").start()
    assert panel.commands == []


def test_stop_leaves_the_power_on_rate_and_a_last_picture(panel):
    session = session_for(panel)
    session.start()
    session.stop(turzx_usb.encode_png_rgba(Image.new("RGB", panel.native, "teal")))
    assert panel.commands[-3:] == [123, 15, 102] and panel.fps_log[-1] == 30


def test_flow_control_never_overflows_the_ring(panel):
    session = session_for(panel, device_fps=20)
    session.start()
    for _ in range(40):  # far faster than the player takes them
        session.send(nal(1))
    assert session.waits > 0 and panel.overwritten == 0
    # polling every second block, the depth can reach 4 before a wait: one below the ring
    assert session.max_depth <= FakePanel.RING - 1
    assert panel.commands.count(122) >= 20  # every second block asks


def test_a_late_status_answer_is_skipped(panel):
    session = session_for(panel)
    session.start()
    panel.late_status = 1
    assert session.depth() is None
    assert session.depth() == 0  # the late answer was read and dropped first
    session.send(nal(1))


def test_large_pictures_take_several_blocks(panel):
    session = session_for(panel)
    session.start()
    session.block_size = 1000
    session.send(nal(5, bytes(2500)))
    assert [len(b) for b in panel.blocks] == [1000, 1000, 504]  # 2504 bytes


class Clock:
    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def test_hung_decoder_is_detected(panel):
    # each block takes 0.75 s instead of 1 ms; the queue stands at 24 and does not move
    session = session_for(panel, clock=Clock(0.4), sleep=lambda s: None)
    session.start()
    panel.hung_depths = itertools.repeat(24)
    with pytest.raises(DecoderHung, match="hangs"):
        for _ in range(VideoSession.HANG_WINDOW):
            session.send(nal(1))


def test_slow_but_moving_queue_is_not_a_hang(panel):
    session = session_for(panel, clock=Clock(0.4), sleep=lambda s: None)
    session.start()
    panel.hung_depths = itertools.cycle([24, 18, 12, 6])  # high, but draining
    for _ in range(VideoSession.HANG_WINDOW * 2):
        session.send(nal(1))


def test_transparent_layer_is_small():
    assert len(transparent_layer((480, 1920))) < 10_000


# -- the display with a real encoder -----------------------------------------------


def moving_frames(count, size=(1920, 480)):
    frames = []
    for i in range(count):
        frame = Image.new("RGB", size, (12, 18, 30))
        draw = ImageDraw.Draw(frame)
        draw.rectangle([40 + i * 12, 120, 160 + i * 12, 360], fill=(40, 200, 180))
        draw.rectangle([0, 0, size[0] - 1, 30], fill=(230, 230, 230))
        frames.append(frame)
    return frames


def noisy_frames(count, size=(1920, 480)):
    import random

    rng = random.Random(1)
    frames = []
    for _ in range(count):
        frame = Image.new("RGB", size, (12, 18, 30))
        patch = Image.frombytes("RGB", (400, 400), rng.randbytes(400 * 400 * 3))
        frame.paste(patch.resize((800, 400)), (500, 40))
        frames.append(frame)
    return frames


def decode(stream, size=(480, 1920)):
    raw = subprocess.run(
        [FFMPEG, "-loglevel", "error", "-f", "h264", "-i", "-"]
        + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=stream,
        capture_output=True,
        check=True,
    ).stdout
    n = size[0] * size[1] * 3
    return [Image.frombytes("RGB", size, raw[i : i + n]) for i in range(0, len(raw), n)]


def video_config(video_range="limited", **kwargs):
    """A 9.2" in video mode, in limited range unless asked (as these tests were written for:
    the 9.2"'s own range, full, is test_the_video_range_setting_per_panel_and_by_model's)."""
    video = VideoConfig(mode="on", local_clip="usr/data/clip.h264", **kwargs)
    return DeviceConfig(driver="turzx", model="turing-9.2-usb", video=video,
                        video_range=video_range)  # fmt: skip


def stream_frames(display, frames, fps=50):
    for frame in frames:
        display.show(frame)
        time.sleep(1 / fps)


def settle(panel, quiet=0.3, timeout=5.0, at_least=0):
    """Wait until no picture has reached the panel for ``quiet`` seconds, and at
    least ``at_least`` have (a busy machine can pause ffmpeg longer than that)."""
    end = time.monotonic() + timeout
    count, since = len(panel.blocks), time.monotonic()
    while time.monotonic() < end:
        time.sleep(0.05)
        if len(panel.blocks) != count:
            count, since = len(panel.blocks), time.monotonic()
        elif time.monotonic() - since >= quiet and count >= at_least:
            return


def arrive(panel, count, timeout=60.0):
    """Wait until ``count`` pictures have reached the panel: ffmpeg, started and paused by a
    busy machine, is as slow as it is, and a test of what comes out of it waits for that rather
    than for a number of frames sent (the ceiling is for a pipeline that has really stopped)."""
    end = time.monotonic() + timeout
    while len(panel.blocks) < count and time.monotonic() < end:
        time.sleep(0.02)
    assert len(panel.blocks) >= count, f"{len(panel.blocks)} of {count} pictures after {timeout} s"


@needs_ffmpeg
def test_a_frame_goes_to_ffmpeg_without_a_copy_of_it(panel, monkeypatch):
    """The RGB bytes are taken from the frame as the renderer made it (RGBA): a copy of the
    picture on every frame was a good part of a frame's time, on two panels at once more so."""
    display = TurzxVideoDisplay(video_config())
    display.open()
    frames = [frame.convert("RGBA") for frame in moving_frames(20)]
    real, converted = Image.Image.convert, []

    def convert(self, *args, **kwargs):
        converted.append(self.mode)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "convert", convert)
    stream_frames(display, frames)
    assert converted == []
    monkeypatch.undo()
    settle(panel, timeout=15, at_least=15)
    display.close()
    for source, picture in zip(frames, decode(panel.stream()), strict=False):
        native = source.convert("RGB").transpose(Image.Transpose.ROTATE_270)
        assert max(ImageStat.Stat(ImageChops.difference(native, picture)).mean) < 6


@needs_ffmpeg
def test_video_display_end_to_end(panel):
    display = TurzxVideoDisplay(video_config())
    display.open()
    frames = moving_frames(60)
    stream_frames(display, frames)
    settle(panel, timeout=15, at_least=55)  # the last pictures reach the panel
    display.close()

    assert not NEVER & set(panel.commands)
    assert panel.commands[:8] == [10, 110, 111, 112, 14, 102, 15, 17]  # sync, then start
    assert panel.commands[-3:] == [123, 15, 102]  # stop, power-on rate, last picture
    assert panel.fps_log == [60, 30] and panel.overwritten == 0
    assert all(slice_count(block) == 1 for block in panel.blocks)  # one picture per block
    assert len(panel.blocks) >= 55  # the last one or two go down with ffmpeg at close

    decoded = decode(panel.stream())
    assert len(decoded) == len(panel.blocks)
    for source, picture in zip(frames, decoded, strict=False):
        native = source.transpose(Image.Transpose.ROTATE_270)
        error = ImageStat.Stat(ImageChops.difference(native, picture)).mean
        assert max(error) < 6, error  # same picture, within compression noise
    last = panel.frames[-1]
    assert last.size == (480, 1920) and last.getextrema()[3] == (255, 255)  # opaque


@needs_ffmpeg
def test_video_display_restarts_a_stopped_ffmpeg(panel):
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        frames = moving_frames(20)
        stream_frames(display, frames[:10])
        display.encoder.proc.kill()
        display.encoder.proc.wait()
        time.sleep(0.2)
        stream_frames(display, frames[10:])
        settle(panel)
        kinds = [kind for _, kind in nal_units(panel.stream())]
        assert kinds.count(5) == 2  # the new ffmpeg starts with a keyframe
        assert kinds.count(7) == 2  # and its parameter sets
    finally:
        display.close()


@needs_ffmpeg
def test_an_ffmpeg_that_starts_slowly_is_waited_for(panel, monkeypatch):
    """A cold start (a virus scanner, a busy machine) is no stall: restarting ffmpeg
    would drop the frames already handed to it and start the wait again."""
    import sys

    real = turzx_video.ffmpeg_command
    late = "import subprocess, sys, time; time.sleep(1.5); sys.exit(subprocess.call(sys.argv[1:]))"
    monkeypatch.setattr(
        turzx_video, "ffmpeg_command", lambda *a: [sys.executable, "-c", late, *real(*a)]
    )
    monkeypatch.setattr(TurzxVideoDisplay, "STALL_S", 0.5)
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        stream_frames(display, moving_frames(30))
        settle(panel, timeout=10, at_least=25)
        kinds = [kind for _, kind in nal_units(panel.stream())]
        assert kinds.count(5) == 1 and len(panel.blocks) >= 25  # one ffmpeg, no frames lost
    finally:
        display.close()


@needs_ffmpeg
def test_an_ffmpeg_that_freezes_is_restarted(panel, monkeypatch):
    """Once it has produced pictures, an ffmpeg that takes no frames is stalled."""
    import psutil

    monkeypatch.setattr(TurzxVideoDisplay, "STALL_S", 0.5)
    display = TurzxVideoDisplay(video_config())
    display.open()
    frozen = []
    try:
        frames = moving_frames(40)
        stream_frames(display, frames[:15])
        settle(panel, quiet=0.2)
        # frozen, not dead; the whole tree, as the ffmpeg of a package manager
        # (a Chocolatey shim) starts the real one as a child
        root = psutil.Process(display.encoder.proc.pid)
        frozen = [root, *root.children(recursive=True)]
        for process in frozen:
            process.suspend()
        stream_frames(display, frames[15:])
        settle(panel, timeout=10, at_least=30)
        kinds = [kind for _, kind in nal_units(panel.stream())]
        assert kinds.count(5) == 2  # the new ffmpeg starts with a keyframe
    finally:
        for process in frozen:  # nothing stays frozen after the test
            try:
                process.resume()
            except psutil.Error:
                pass
        display.close()


@needs_ffmpeg
def test_closing_ends_a_frozen_ffmpeg_behind_a_shim(panel, monkeypatch):
    """ffmpeg from a package manager (Chocolatey, scoop) is a shim that starts the
    real one as a child. Closing must end the child too: a frozen one would keep
    the pipes open, and closing them while a thread reads from one blocks on Windows."""
    import sys

    import psutil

    real = turzx_video.ffmpeg_command
    shim = "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))"
    monkeypatch.setattr(
        turzx_video, "ffmpeg_command", lambda *a: [sys.executable, "-c", shim, *real(*a)]
    )
    display = TurzxVideoDisplay(video_config())
    display.open()
    children = []
    try:
        stream_frames(display, moving_frames(10))
        children = psutil.Process(display.encoder.proc.pid).children(recursive=True)
        assert children, "the shim started no child"
        for child in children:
            child.suspend()
    finally:
        started = time.monotonic()
        display.close()
        assert time.monotonic() - started < 15  # and it does not hang

    def running(process):
        try:
            return process.status() != psutil.STATUS_ZOMBIE  # ended, not yet collected
        except psutil.NoSuchProcess:
            return False

    end = time.monotonic() + 30
    while any(running(child) for child in children) and time.monotonic() < end:
        time.sleep(0.05)
    alive = [child for child in children if running(child)]
    for child in alive:  # do not leave it behind if the test fails
        child.kill()
    assert not alive, "a frozen ffmpeg survived the close"


@needs_ffmpeg
def test_an_ffmpeg_that_keeps_stopping_gives_up_but_reconnects_do_not_count(panel):
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        frames = moving_frames(10)
        for starts in range(2, 6):  # reconnecting starts ffmpeg again; that is no ffmpeg fault
            panel.fail_next_writes = 1
            end = time.monotonic() + 20
            while panel.commands.count(110) < starts and time.monotonic() < end:
                stream_frames(display, frames[:3])  # the block that fails, then the reconnect
        assert panel.commands.count(110) == 5 and display._error is None
        for _ in range(display.RESTARTS_PER_MINUTE):
            display.encoder.proc.kill()
            display.encoder.proc.wait()
            stream_frames(display, frames[:3])
        display.encoder.proc.kill()
        display.encoder.proc.wait()
        with pytest.raises(DeviceError, match="ffmpeg keeps stopping"):
            stream_frames(display, frames[:3])
    finally:
        display.close()


@needs_ffmpeg
def test_auto_rate_steps_down_on_a_slow_panel_and_the_counters_say_the_cap(panel, monkeypatch):
    monkeypatch.setattr(turzx_video, "RATES", {})
    display = TurzxVideoDisplay(video_config())  # maxrate "auto": 2 Mbit/s to start with
    display.open()
    try:
        panel.block_delay = 0.05  # 20 blocks a second: every block takes longer than a frame
        frames = noisy_frames(20)
        display.show(frames[0])
        assert display.rate is not None and display.counters.bit_rate == 2.0
        assert "2.0M" in display.encoder.command and "libx264" in display.encoder.command
        display.rate.rate = 3.0  # as after a calm minute: a step up, and ffmpeg anew at it
        display._stop_encoder()
        display.show(frames[0])
        assert display.counters.bit_rate == 3.0 and "3.0M" in display.encoder.command
        deadline = time.monotonic() + 20.0  # ffmpeg and the pipes hold some frames first
        for frame in itertools.cycle(frames[1:]):
            display.show(frame)
            if display.rate.rate < 3.0 or time.monotonic() > deadline:
                break
        assert display.rate.rate == 2.0  # one step down, with ffmpeg started anew
        assert display.counters.bit_rate == 2.0 and "2.0M" in display.encoder.command
        assert list(turzx_video.RATES.values()) == [display.rate]
        assert next(iter(turzx_video.RATES))[::2] == ("turing-9.2-usb", (1920, 480))
    finally:
        panel.block_delay = 0.0
        display.close()
    fixed = TurzxVideoDisplay(video_config(maxrate="1500k"))
    fixed.open()
    try:
        fixed.show(frames[0])
        assert fixed.rate is None and fixed.counters.bit_rate == 1.5
    finally:
        fixed.close()


def frame_number(picture):
    """Which of :func:`moving_frames` a decoded picture (turned as the panel holds it) shows:
    where its rectangle begins."""
    row = picture.transpose(Image.Transpose.ROTATE_90).crop((0, 240, 1920, 241)).tobytes()
    left = next(x for x in range(1920) if row[3 * x + 1] > 110)  # its green, not the background's
    return round((left - 40) / 12)


def kinds(block):
    return [kind for _, kind in nal_units(block)]


@needs_ffmpeg
def test_a_step_of_the_bit_rate_leaves_no_gap(panel, monkeypatch):
    """A step of "auto" starts the new ffmpeg beside the old one: the panel goes on showing the
    old one's pictures, frame by frame, until the new one's first (a keyframe), and nothing is
    counted as dropped or restarted for it."""
    import sys

    monkeypatch.setattr(turzx_video, "RATES", {})
    # only the step made here: ffmpeg's start-up sends a few pictures at once, which can
    # make the sender wait for room often enough for a step down within the first window
    monkeypatch.setattr(RateControl, "TROUBLE_WAITS", 10**6)
    display = TurzxVideoDisplay(video_config())
    display.open()
    frames = moving_frames(140)
    try:
        stream_frames(display, frames[:30])
        old = display.encoder
        assert "2.0M" in old.command
        # the new ffmpeg takes half a second to start, as on a busy PC: what the panel shows
        # meanwhile can be counted on any machine
        real = turzx_video.ffmpeg_command
        late = (
            "import subprocess, sys, time; time.sleep(0.5); sys.exit(subprocess.call(sys.argv[1:]))"
        )
        monkeypatch.setattr(
            turzx_video, "ffmpeg_command", lambda *a: [sys.executable, "-c", late, *real(*a)]
        )
        display.rate.rate = 3.0  # as after a calm minute: the next frame starts the switch
        after = 0
        for frame in frames[30:]:
            display.show(frame)
            time.sleep(0.02)
            after += display._leaving is None
            if after > 15:  # the switch is done, and the new one has shown a while
                break
        settle(panel)
        assert display.encoder is not old and "3.0M" in display.encoder.command
        assert display._leaving is None and old.proc.poll() is not None  # the old one is closed
        for encoder in (old, display.encoder):  # both fed rgb0, each as its own settings say
            assert encoder.command[encoder.command.index("-pix_fmt") + 1] == "rgb0"
            assert encoder.settings.pixels == "rgb0"
        assert display.counters.bit_rate == 3.0 and display.counters.drops == 0
        assert display.counters.ffmpeg_restarts == 0
    finally:
        display.close()
    keyframes = [i for i, block in enumerate(panel.blocks) if NAL_IDR in kinds(block)]
    assert len(keyframes) == 2 and keyframes[0] == 0
    switch = keyframes[1]
    assert kinds(panel.blocks[switch])[:2] == [7, 8]  # the new stream's parameter sets with it
    assert all(slice_count(block) == 1 for block in panel.blocks) and panel.overwritten == 0
    shown = [frame_number(picture) for picture in decode(panel.stream())]
    assert len(shown) == len(panel.blocks)
    assert shown[:switch] == list(range(switch))  # the old one, frame by frame, to the end
    assert shown[switch - 1] > 30  # and on while the new one started
    # the new one starts with the first frame it took, and no frame was left out before it
    assert 30 <= shown[switch] <= shown[switch - 1] + 1
    assert all(a < b for a, b in itertools.pairwise(shown[switch:]))  # then the new one, on
    assert len(shown) - switch > 5
    assert not NEVER & set(panel.commands) and panel.commands.count(110) == 1


@needs_ffmpeg
def test_a_new_ffmpeg_that_shows_nothing_is_given_up_and_the_old_one_goes_on(
    panel, monkeypatch, caplog
):
    """A switch whose new ffmpeg has no picture in time is given up: the new one is closed,
    the old one never stopped showing, and the switch waits a minute before it is tried again."""
    monkeypatch.setattr(turzx_video, "RATES", {})
    monkeypatch.setattr(RateControl, "TROUBLE_WAITS", 10**6)  # only the step made here
    monkeypatch.setattr(TurzxVideoDisplay, "SWITCH_S", 1.0)
    made = []

    class Silent(h264.Encoder):
        """Takes the frames and never hands a picture on."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

        def _hand_on(self, picture):
            pass

    display = TurzxVideoDisplay(video_config())
    display.open()
    frames = moving_frames(140)
    try:
        stream_frames(display, frames[:20])
        old = display.encoder
        monkeypatch.setattr(turzx_video, "Encoder", Silent)
        display.rate.rate = 3.0
        given_up = 20
        with caplog.at_level("WARNING", logger="libre_panel.devices.turzx_video"):
            for given_up in range(20, 110):
                display.show(frames[given_up])
                time.sleep(0.02)
                if made and display._leaving is None:
                    break
        assert len(made) == 1 and made[0].proc.poll() is not None  # the new one is closed
        assert display.encoder is old and old.alive and display.counters.bit_rate == 2.0
        assert any("no picture after" in r.getMessage() for r in caplog.records)
        stream_frames(display, frames[given_up + 1 : given_up + 6])
        assert len(made) == 1 and display.encoder is old  # not tried again within the minute
        assert display.counters.drops == 0 and display.counters.ffmpeg_restarts == 0
        settle(panel)
        assert sum(NAL_IDR in kinds(block) for block in panel.blocks) == 1  # the old one's
        shown = [frame_number(picture) for picture in decode(panel.stream())]
        assert shown == list(range(len(shown))) and shown[-1] >= given_up  # the old one, on

        monkeypatch.setattr(turzx_video, "Encoder", h264.Encoder)
        monkeypatch.setattr(TurzxVideoDisplay, "SWITCH_S", 3.0)
        display._switch_held = 0.0  # as a minute later: tried again, now with a real one
        deadline = time.monotonic() + 20
        for frame in itertools.cycle(frames[given_up + 6 :]):
            display.show(frame)
            time.sleep(0.02)
            if display.encoder is not old and display._leaving is None:
                break
            assert time.monotonic() < deadline
        settle(panel)
        assert len(made) == 1 and "3.0M" in display.encoder.command and old.proc.poll() is not None
        assert display.counters.drops == 0
        assert sum(NAL_IDR in kinds(block) for block in panel.blocks) == 2
    finally:
        display.close()


@needs_ffmpeg
def test_the_rate_auto_got_to_is_where_the_next_run_starts(panel, monkeypatch, isolated_home):
    """The rate goes into video-state.json when it changes (at most every 10 s) and when the
    panel is closed; the next run's display starts there."""
    isolated_home.mkdir()
    monkeypatch.setattr(turzx_video, "RATES", {})
    frame = moving_frames(1)[0]
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        display.show(frame)
        key = display._rate_key
        assert key[::2] == ("turing-9.2-usb", (1920, 480)) and video_state.rate(key) == 0.0
        display._keep_rate(100.0)
        assert video_state.rate(key) == 0.0  # nothing found yet: nothing written
        display.rate.rate = 3.0
        display._keep_rate(100.0)
        assert video_state.rate(key) == 3.0  # a change: written
        display.rate.rate = 4.0
        display._keep_rate(105.0)
        assert video_state.rate(key) == 3.0  # the next within 10 s waits
        display._keep_rate(110.0)
        assert video_state.rate(key) == 4.0
        display.rate.rate = 6.0
    finally:
        display.close()
    assert video_state.rate(key) == 6.0  # on close, whenever the last write was
    monkeypatch.setattr(turzx_video, "RATES", {})  # the next run
    again = TurzxVideoDisplay(video_config())
    again.open()
    try:
        again.show(frame)
        assert again.rate.rate == 6.0 and "6.0M" in again.encoder.command
        assert again.counters.bit_rate == 6.0 and again.rate.steps[0] == 2.0  # its floor stays
    finally:
        again.close()


@needs_ffmpeg
def test_video_display_heals_after_a_usb_error(panel):
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        frames = moving_frames(30)
        stream_frames(display, frames[:10])
        time.sleep(0.2)
        panel.fail_next_writes = 1  # the next block fails
        stream_frames(display, frames[10:20])
        # (the block goes when ffmpeg's picture is there: late on a busy machine; the frames
        # that follow are the display's chance to see the error and reconnect)
        assert wait_until(lambda: display.counters.usb_errors == 1, 60)
        stream_frames(display, frames[20:])
        settle(panel)
        assert panel.commands.count(110) == 2  # a full start after reconnecting
        assert not NEVER & set(panel.commands)
        assert display._error is None
    finally:
        display.close()


@needs_ffmpeg
def test_a_slow_panel_slows_the_frames_down_instead_of_restarting_ffmpeg(panel):
    display = TurzxVideoDisplay(video_config(maxrate="40M"))  # big pictures fill the pipes fast
    display.open()
    try:
        panel.block_delay = 0.05  # 20 blocks a second
        durations = []
        # large pictures; ffmpeg and the pipes hold some 15 to 30 frames before they push back
        # (ffmpeg 8 more than 6), so the panel's pace shows in the frames after them
        for frame in noisy_frames(60):
            started = time.monotonic()
            display.show(frame)  # as fast as it goes
            durations.append(time.monotonic() - started)
        assert sum(durations) > 0.8, [len(b) for b in panel.blocks]  # the panel set the pace
        settle(panel, quiet=0.5)
        kinds = [kind for _, kind in nal_units(panel.stream())]
        assert kinds.count(5) == 1 and panel.commands.count(110) == 1  # no restart
        assert panel.overwritten == 0
    finally:
        panel.block_delay = 0.0
        display.close()


@needs_ffmpeg
def hang_quickly(monkeypatch):
    monkeypatch.setattr(VideoSession, "HANG_WINDOW", 4)
    monkeypatch.setattr(VideoSession, "HANG_MEDIAN_S", 0.0)  # every block counts as slow
    monkeypatch.setattr(VideoSession, "GIVE_UP_S", 0.1)
    monkeypatch.setattr(turzx_video._Restart, "SETTLE_S", 0.05)


def show_until(display, error):
    with pytest.raises(error) as raised:
        for frame in moving_frames(120):
            display.show(frame)
            time.sleep(0.02)
    return raised.value


@needs_ffmpeg
def test_a_hung_decoder_restarts_the_panel(panel, monkeypatch):
    """As SPUR II: 11, wait until the panel has gone and come back, a full start."""
    hang_quickly(monkeypatch)
    display = TurzxVideoDisplay(video_config())
    display.open()
    panel.hung_depths = itertools.repeat(24)
    show_until(display, PanelRestarting)
    display.close()  # as the main loop does
    assert panel.commands.count(11) == 1 and panel.restarts == 1
    assert 123 not in panel.commands  # nothing more is sent to a hung decoder

    again = TurzxVideoDisplay(video_config())  # the main loop opens it again, soon
    tries, deadline = 0, time.monotonic() + 60
    while True:
        tries += 1
        try:
            again.open()
            break
        except PanelRestarting:
            assert time.monotonic() < deadline
            time.sleep(0.05)
    try:
        assert tries > 1 and again.restarted  # it waited for the panel to go and come back
        assert panel.commands.count(110) == 2  # a full start
        stream_frames(again, moving_frames(20))
        settle(panel)
    finally:
        again.close()
    assert not (NEVER - {11}) & set(panel.commands)
    assert panel.commands.count(11) == 1


@needs_ffmpeg
def test_after_three_restarts_in_a_row_it_asks_to_replug(panel, monkeypatch):
    hang_quickly(monkeypatch)
    for _ in range(3):
        turzx_video.RESTART.begin(panel.pid)
    turzx_video.RESTART.pid = None  # those are over
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        panel.hung_depths = itertools.repeat(24)
        error = show_until(display, DecoderHung)
        assert "Unplug the panel" in str(error)
    finally:
        display.close()
    assert 11 not in panel.commands and 123 not in panel.commands


def test_a_restart_waits_for_the_panel_to_go_come_back_and_settle():
    clock = Clock2()
    restart = turzx_video._Restart(clock=clock)
    here = {"now": True}

    def present(pid):
        return here["now"]

    assert restart.allowed()
    restart.wait(present)  # nothing under way: nothing to wait for
    restart.begin(0x0092)
    with pytest.raises(PanelRestarting):
        restart.wait(present)  # still on the bus
    clock.now += 1
    here["now"] = False
    with pytest.raises(PanelRestarting):
        restart.wait(present)  # gone
    clock.now += 5
    here["now"] = True
    with pytest.raises(PanelRestarting):
        restart.wait(present)  # back, settling
    clock.now += 2.1
    restart.wait(present)  # ready to open
    assert restart.pid is None

    restart.begin(0x0092)  # a panel that never seems to leave: go on after 20 s
    clock.now += 21
    with pytest.raises(PanelRestarting):
        restart.wait(present)
    clock.now += 2.1
    restart.wait(present)

    restart.begin(0x0092)  # three in a row; after 300 s healthy the count starts again
    assert not restart.allowed()
    clock.now += 301
    assert restart.allowed()

    restart.begin(0x0092)  # a panel that does not come back: replug
    here["now"] = False
    with pytest.raises(PanelRestarting):
        restart.wait(present)
    clock.now += 91
    with pytest.raises(DeviceError, match="did not come back") as raised:
        restart.wait(present)
    assert not isinstance(raised.value, PanelRestarting)


@needs_ffmpeg
def test_main_loop_in_video_mode(panel, monkeypatch):
    device = video_config()
    config = Config(theme="spur-ii", device=device, sensors=SensorsConfig(providers=["demo"]))
    stop = threading.Event()
    status = app.RunStatus()
    options = {"stop": stop, "status": status}
    thread = threading.Thread(target=app.run, args=(config,), kwargs=options)
    thread.start()
    time.sleep(2.0)
    end = time.monotonic() + 30  # a busy machine needs longer for the same frames
    while (status.frames <= 40 or len(panel.blocks) <= 30) and time.monotonic() < end:
        time.sleep(0.1)
    stop.set()
    thread.join(20)
    assert not thread.is_alive()
    assert status.target == 'Turing 9.2", video 30 fps'
    # about 50 a second after ffmpeg and the panel started; the rate itself is
    # checked by test_main_loop_sends_every_frame_at_the_stream_rate, this one
    # also passes on a machine busy with other work
    assert status.frames > 40
    assert len(panel.blocks) > 30 and panel.overwritten == 0
    assert all(slice_count(block) == 1 for block in panel.blocks)
    assert panel.commands[-3:] == [123, 15, 102] and not NEVER & set(panel.commands)


@needs_ffmpeg
def test_video_mode_through_the_turzx_driver(panel):
    from libre_panel.devices.turzx import TurzxDisplay

    display = TurzxDisplay(video_config(fps=25))
    display.open()
    try:
        assert display.streaming and display.stream_fps == 25
        assert display.describe() == 'Turing 9.2", video 25 fps'
    finally:
        display.close()
    png = TurzxDisplay(DeviceConfig(driver="turzx", model="turing-9.2-usb"))
    png.open()
    assert not png.streaming  # "auto", but no clip name
    png.close()


@needs_ffmpeg
def test_auto_video_mode_on_a_detected_panel(panel, monkeypatch, caplog):
    """``model = "auto"``: the panel on the bus is looked up without opening it,
    and the 9.2" streams; what is missing is logged once, not on every reconnect."""
    import usb.core

    from libre_panel.devices.turzx import TurzxDisplay

    def find(find_all, idVendor, idProduct, backend):
        return [SimpleNamespace(bus=1, address=9)] if idProduct == panel.pid else []

    monkeypatch.setattr(usb.core, "find", find)
    monkeypatch.setattr(turzx_usb, "_backend", lambda: None)
    found = TurzxDisplay(
        DeviceConfig(
            driver="turzx", model="auto", video=VideoConfig(local_clip="usr/data/clip.h264")
        )
    )
    found.open()
    try:
        assert found.streaming and found.model.id == "turing-9.2-usb"
    finally:
        found.close()
    assert 110 in panel.commands
    plain = TurzxDisplay(DeviceConfig(driver="turzx", model="auto"))
    with caplog.at_level("INFO", logger="libre_panel.devices.turzx"):
        for _ in range(2):
            plain.open()
            assert not plain.streaming
            plain.close()
    told = [r.getMessage() for r in caplog.records if "video mode off" in r.getMessage()]
    assert len(told) == 1 and "local_clip is not set" in told[0]


# -- the main loop -----------------------------------------------------------------


class Clock2:
    """perf_counter stand-in: sleeping advances it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_pacer_keeps_the_rate_without_drift():
    clock = Clock2()
    pacer = app._Pacer(clock=clock, sleep=clock.sleep)
    stop = threading.Event()
    for _ in range(500):
        clock.now += 0.007  # render time varies around 7 ms
        pacer.wait(50, stop)
    assert clock.now == pytest.approx(500 / 50, abs=0.021)
    assert pacer.reanchored == 0


def test_pacer_starts_over_after_falling_behind():
    clock = Clock2()
    pacer = app._Pacer(clock=clock, sleep=clock.sleep)
    stop = threading.Event()
    pacer.wait(50, stop)
    clock.now += 0.5  # a hiccup of 25 frames
    pacer.wait(50, stop)
    started = clock.now
    for _ in range(10):
        pacer.wait(50, stop)
    assert pacer.reanchored == 1
    assert clock.now - started == pytest.approx(10 / 50)  # no sprint to catch up


def test_pacer_plans_each_frame_on_a_steady_grid():
    """A frame that starts a little late is still drawn at its planned time,
    so an animation steps 20 ms, not 41 and then 13 (SPUR II)."""
    clock = Clock2()
    pacer = app._Pacer(clock=clock, sleep=clock.sleep)
    stop = threading.Event()
    planned = [pacer.wait(50, stop)]
    for late in (0.0, 0.019, 0.0, 0.004, 0.0):  # render time beyond the frame's own
        clock.now += 0.005 + late
        planned.append(pacer.wait(50, stop))
    steps = [round(b - a, 9) for a, b in zip(planned, planned[1:], strict=False)]
    assert steps == [0.02] * 5
    clock.now += 0.3  # a hiccup of 15 frames: a new grid from now
    assert pacer.wait(50, stop) == clock.now and pacer.reanchored == 1
    assert pacer.wait(50, stop) == pytest.approx(clock.now)


# The main loop's timing on a small theme: a loaded Windows runner drew an 800x480 frame in more
# than its 20 ms and so fell off the grid, which says nothing of the loop.
SMALL = "pico"


def test_the_main_loop_draws_streaming_frames_at_their_planned_time(monkeypatch):
    times = []
    real_renderer = app.Renderer

    class Recording(real_renderer):
        def render(self, snapshot, now=None):
            times.append(now)
            return super().render(snapshot, now)

    monkeypatch.setattr(app, "Renderer", Recording)
    monkeypatch.setattr(app, "create_display", StreamingDisplay)
    config = Config(theme=SMALL, fps=5, sensors=SensorsConfig(providers=["demo"]))
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    thread.start()

    def steps_of(planned):
        return [b - a for a, b in zip(planned[1:], planned[2:], strict=False)]

    def on_the_grid(planned):
        return sum(abs(step - 0.02) < 1e-6 for step in steps_of(planned))

    # Until it has run on its grid for a while: a slow start or a busy moment (a runner
    # whose timer ticks every 15 ms) is waited out, not counted as the loop's fault.
    end = time.monotonic() + 60
    while on_the_grid(list(times)) < 25 and time.monotonic() < end:
        time.sleep(0.05)
    stop.set()
    thread.join(10)
    steps = steps_of(times)
    # never a short step after a late one; on the grid unless the loop fell a
    # whole frame behind (slow runners) and started a new one
    assert min(steps) > 0.02 - 1e-6, min(steps)
    assert on_the_grid(times) >= 5


class StreamingDisplay(Display):
    streaming = True
    stream_fps = 50

    def __init__(self, config):
        super().__init__(config)
        self.shown = []

    def show(self, frame, region=None):
        self.shown.append((time.perf_counter(), region))


def test_main_loop_sends_every_frame_at_the_stream_rate(monkeypatch, tmp_path):
    displays = []

    def create(config):
        displays.append(StreamingDisplay(config))
        return displays[-1]

    monkeypatch.setattr(app, "create_display", create)
    renderers = []
    real_renderer = app.Renderer

    def renderer(*args, **kwargs):
        renderers.append(real_renderer(*args, **kwargs))
        return renderers[-1]

    monkeypatch.setattr(app, "Renderer", renderer)
    config = Config(theme=SMALL, fps=5, sensors=SensorsConfig(providers=["demo"]))
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    thread.start()
    end = time.monotonic() + 60  # frames enough to tell a rate from, however long they take
    while (not displays or len(displays[0].shown) < 60) and time.monotonic() < end:
        time.sleep(0.05)
    stop.set()
    thread.join(10)
    shown = displays[0].shown
    assert all(region is None for _, region in shown)  # whole frames, changed or not
    rate = (len(shown) - 1) / (shown[-1][0] - shown[0][0])
    # The display's 50 fps, not config.fps = 5, as far as this machine's timer allows
    # (virtual runners coalesce short sleeps).
    started = time.perf_counter()
    for _ in range(10):
        time.sleep(0.018)
    timer_rate = min(50, 10 / (time.perf_counter() - started))
    # Well above config.fps, as close to the timer as a busy machine manages (a
    # loaded runner drew 20 fps with a timer good for 26).
    assert max(2 * config.fps, 0.6 * timer_rate) < rate < 55, (rate, timer_rate)
    assert renderers[0].background_builds


def test_device_changes_open_the_panel_again():
    a = DeviceConfig(brightness=10)
    assert app._same_device(a, DeviceConfig(brightness=90))
    assert not app._same_device(a, DeviceConfig(video=VideoConfig(mode="on")))
    # the name of the clip is what the video start sends; that the app has asked for it is not
    assert not app._same_device(a, DeviceConfig(video=VideoConfig(local_clip="usr/x.h264")))
    assert app._same_device(a, DeviceConfig(video=VideoConfig(clip_asked=True)))


def running_panel(home, monkeypatch, panel, video=""):
    """The main loop on a 9.2" of the simulated bus, with a config.toml in ``home`` that has
    no clip name (or ``video``'s lines in [video]), as the background app runs it; returns what
    a test needs to watch and stop it."""
    from libre_panel.config import load_config

    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'theme = "slate"\nfps = 5\n\n[device]\ndriver = "turzx"\nmodel = "turing-9.2-usb"\n'
        'video_range = "limited"\n\n'  # (the range these tests were written for)
        f'[sensors]\nproviders = ["demo"]\n\n[video]\n{video}',
        encoding="utf-8",
    )
    opened = []
    real = turzx_usb.UsbTransport.open

    def counted(pid=None, beside=()):
        opened.append(pid)
        return real(pid)

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(counted))
    stop, status = threading.Event(), app.RunStatus()
    options = {"stop": stop, "status": status}
    thread = threading.Thread(target=app.run, args=(load_config(),), kwargs=options)
    thread.start()
    return opened, status, stop, thread


def wait_until(predicate, timeout=30.0):
    end = time.monotonic() + timeout  # a busy machine needs longer for the same frames
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@needs_ffmpeg
def test_a_clip_name_written_while_the_panel_runs_starts_the_video_mode_without_a_restart(
    panel, monkeypatch, isolated_home
):
    """What "Turn on" on Start does: the name goes into config.toml, and the panel loop, which
    follows that file, opens the panel again in video mode with exactly that name. Nothing is
    sent to the panel for the clip before."""
    from libre_panel.config import edit_config

    opened, status, stop, thread = running_panel(isolated_home, monkeypatch, panel)
    try:
        assert wait_until(lambda: status.target == 'Turing 9.2"' and status.frames > 2)
        assert 110 not in panel.commands and panel.clip_names == []  # single pictures, no name
        assert len(opened) == 1
        edit_config(
            ("value", "video.local_clip", "usr/data/standby.h264"),
            ("value", "video.clip_asked", True),
        )
        assert wait_until(lambda: status.target == 'Turing 9.2", video 30 fps')
        assert panel.clip_names == ["usr/data/standby.h264"]
        assert len(opened) == 2 and thread.is_alive()  # the same loop, the panel opened again
    finally:
        stop.set()
        thread.join(20)
    assert not thread.is_alive() and not NEVER & set(panel.commands)


@needs_ffmpeg
def test_saying_not_now_leaves_the_running_panel_as_it_is(panel, monkeypatch, isolated_home):
    """ "Not now" writes only that the question was asked: the panel is not opened again for it,
    and no name goes to it. (A theme changed in the same write shows the loop has taken it in.)"""
    from libre_panel.config import edit_config

    opened, status, stop, thread = running_panel(isolated_home, monkeypatch, panel)
    try:
        assert wait_until(lambda: status.target == 'Turing 9.2"' and status.frames > 2)
        edit_config(("value", "video.clip_asked", True), ("value", "theme", "orbit"))
        assert wait_until(lambda: status.theme == "orbit")
        assert len(opened) == 1 and status.target == 'Turing 9.2"'
        assert 110 not in panel.commands and panel.clip_names == []
    finally:
        stop.set()
        thread.join(20)
    assert not thread.is_alive()


@needs_ffmpeg
def test_the_colour_check_s_a_and_b_and_its_one_click_start_ffmpeg_anew_on_the_open_panel(
    panel, monkeypatch, isolated_home
):
    """Settings → Panel → Check the colours: its A and B on the panel are converted by the card
    and by the processor in turn, each a new ffmpeg on the open panel (no second start
    sequence), and afterwards it goes as video.pixels says ("auto": the processor, as a check
    found the card shifting the colours); the range's A and B so too (limited, full); a click
    that writes video.pixels is followed so too, without opening the panel again. x264 stands in
    for the card's encoder."""
    from libre_panel import colour_show
    from libre_panel.config import edit_config
    from libre_panel.devices import card_choice

    card_stand_in(monkeypatch)
    ffmpeg = find_ffmpeg("")
    card_choice.record(ffmpeg, {"encoder": "libx264", "compared": ["card", "processor"],
                                "state": "done", "verdict": "matrix", "worst": None},
                       full=False)  # fmt: skip
    commands = []
    real = turzx_video.ffmpeg_command

    def command(*args):
        commands.append(real(*args))
        return commands[-1]

    def ways():
        def way(c):
            full = " full" if "out_range=full" in after(c, "-vf") else ""
            return ("card" if by_card(c) else after(c, "-pix_fmt")) + full

        return [way(c) for c in commands]

    monkeypatch.setattr(turzx_video, "ffmpeg_command", command)
    monkeypatch.setattr(colour_show, "SWITCH_S", 0.8)
    video = 'mode = "on"\nlocal_clip = "usr/data/clip.h264"\n'
    opened, status, stop, thread = running_panel(isolated_home, monkeypatch, panel, video)
    try:
        assert wait_until(
            lambda: status.target == 'Turing 9.2", video 30 fps' and status.frames > 5
        )
        assert ways() == ["rgb0"]  # the processor's: the check found the card's shifted
        colour_show.begin(1, seconds=3.0)
        try:
            assert wait_until(lambda: "card" in ways())  # A: the card's conversion
            assert wait_until(lambda: ways().count("rgb0") >= 2)  # B: the processor's
            assert wait_until(lambda: colour_show.state() is None)
        finally:
            colour_show.end()
        assert wait_until(lambda: ways()[-1] == "rgb0" and status.stats.get("encoder", "")
                          .endswith("rgb0"))  # fmt: skip
        assert ways()[:3] == ["rgb0", "card", "rgb0"]
        before = len(commands)
        colour_show.begin(1, seconds=2.4, kind="range")
        try:
            assert wait_until(lambda: "rgb0 full" in ways()[before:])  # B: full range
            assert wait_until(lambda: colour_show.state() is None)
        finally:
            colour_show.end()
        assert wait_until(lambda: ways()[-1] == "rgb0")  # limited again, as the 9.2" has it
        edit_config(("value", "video.pixels", "card"))  # forced: the card converts
        assert wait_until(lambda: ways()[-1] == "card")
        assert wait_until(lambda: status.stats.get("encoder", "") == "libx264, rgb0, card")
        edit_config(("value", "video.pixels", "rgb"))
        assert wait_until(lambda: ways()[-1] == "rgb24")
        edit_config(("value", "device.video_range", "full"))  # "B looks right"
        assert wait_until(lambda: ways()[-1] == "rgb24 full")
        assert len(opened) == 1 and panel.commands.count(110) == 1  # the panel stayed open
        arrived = len(panel.blocks)
        assert wait_until(lambda: len(panel.blocks) > arrived + 10)  # and shows the pictures
    finally:
        stop.set()
        thread.join(20)
    assert not thread.is_alive() and not NEVER & set(panel.commands)


def test_a_mode_sets_the_stream_rate(monkeypatch):
    """A mode's fps replaces the display's 50 (SPUR II runs slower in game mode).

    10 fps, not a realistic 25-30: virtual runners coalesce short sleeps,
    so "50" may come out near 30 there, while 10 is far from both 50 and
    config.fps = 5 on any machine.
    """
    from libre_panel.config import ModeConfig
    from libre_panel.plugins import PluginHost

    displays = []
    monkeypatch.setattr(
        app, "create_display", lambda c: displays.append(StreamingDisplay(c)) or displays[-1]
    )
    host = PluginHost()
    config = Config(
        theme="slate",
        fps=5,
        sensors=SensorsConfig(providers=["demo"]),
        modes={"game": ModeConfig(fps=10)},
    )
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop, "host": host})
    thread.start()

    def wait_for_frames(count, pace=None):
        """Wait until ``count`` frames have been shown from now on and, if ``pace`` is given,
        the last two were more than that many seconds apart (the loop has slowed down)."""
        end = time.monotonic() + 60  # a busy machine needs longer for the same frames
        begin = len(displays[0].shown) if displays else 0
        while time.monotonic() < end:
            shown = displays[0].shown[begin:] if displays else []
            if len(shown) >= count and (pace is None or shown[-1][0] - shown[-2][0] > pace):
                return
            time.sleep(0.02)
        raise AssertionError(f"{count} frames at a pace over {pace} s were not drawn in 60 s")

    def rate(seconds):
        begin = len(displays[0].shown) if displays else 0
        time.sleep(seconds)
        shown = displays[0].shown[begin:]
        return (len(shown) - 1) / (shown[-1][0] - shown[0][0])

    try:
        wait_for_frames(10)  # started, and drawing
        normal = rate(1.0)
        host.set_mode("game")
        wait_for_frames(2, pace=0.06)  # the 10 fps of the mode (100 ms) have begun
        game = rate(1.5)
    finally:
        stop.set()
        thread.join(10)
        host.close()
    assert 8 < game < 12, (normal, game)  # the mode's 10
    # and before it, faster than 12: neither the mode's rate nor config.fps (a
    # loaded runner managed only 15 of the display's 50)
    assert normal > 12, (normal, game)


# -- what the video driver counts for the editor's diagnostics ----------------------------
# One Counters is shared by every display and session of a run (the main loop hands it out).


def test_the_session_counts_what_it_sends(panel):
    counters = Counters()
    session = session_for(panel, device_fps=20, counters=counters)
    session.start()
    for _ in range(40):  # far faster than the player takes them
        session.send(nal(1))
    assert counters.blocks == session.blocks == 40
    assert counters.bytes == session.bytes == sum(len(block) for block in panel.blocks)
    assert counters.waits == session.waits > 0
    assert counters.max_depth == session.max_depth and 0 < counters.max_depth < FakePanel.RING
    again = session_for(panel, counters=counters)  # the next session after a reconnect
    again.start()
    again.send(nal(1))
    assert counters.blocks == 41 and again.blocks == 1  # the run's numbers go on


@needs_ffmpeg
def test_a_stopped_ffmpeg_is_one_restart_in_the_counters(panel):
    counters = Counters()
    display = TurzxVideoDisplay(video_config())
    display.counters = counters
    display.open()
    try:
        frames = moving_frames(20)
        stream_frames(display, frames[:10])
        # Of ten frames nine pictures are out: the splitter holds the last one back until
        # another starts. Killed earlier, a slow machine loses what ffmpeg has yet to write,
        # which is the kill's doing and no loss of the restart.
        arrive(panel, 9)
        display.encoder.proc.kill()
        display.encoder.proc.wait()
        time.sleep(0.2)
        stream_frames(display, frames[10:])
        arrive(panel, 18)  # the nine of the first ffmpeg and the nine of the second
        settle(panel)
        assert counters.ffmpeg_restarts == 1 and counters.panel_restarts == 0
        assert counters.usb_errors == 0
        assert counters.blocks == len(panel.blocks) >= 18 and counters.bytes > 0
    finally:
        display.close()


@needs_ffmpeg
def test_a_usb_error_is_counted_once_and_the_video_comes_back(panel):
    counters = Counters()
    display = TurzxVideoDisplay(video_config())
    display.counters = counters
    display.open()
    try:
        frames = moving_frames(30)
        stream_frames(display, frames[:10])
        time.sleep(0.2)
        panel.fail_next_writes = 1  # the next block fails
        stream_frames(display, frames[10:20])
        assert wait_until(lambda: counters.usb_errors == 1, 60)  # (see the test above)
        stream_frames(display, frames[20:])
        settle(panel)
        assert counters.usb_errors == 1 and counters.ffmpeg_restarts == 0
        assert counters.blocks == len(panel.blocks) and display._error is None
    finally:
        display.close()


@needs_ffmpeg
def test_a_restart_of_the_panel_is_counted_across_displays(panel, monkeypatch):
    """The main loop opens a new display after the panel restarted (``auto`` makes one
    each time): the count, and the blocks sent before, stay."""
    hang_quickly(monkeypatch)
    counters = Counters()
    display = TurzxVideoDisplay(video_config())
    display.counters = counters
    display.open()
    panel.hung_depths = itertools.repeat(24)
    show_until(display, PanelRestarting)
    display.close()
    assert counters.panel_restarts == 1
    assert counters.usb_errors == 0  # a decoder that hangs is no fault of the link
    sent = counters.blocks
    assert sent > 0

    again = TurzxVideoDisplay(video_config())
    again.counters = counters
    deadline = time.monotonic() + 60
    while True:
        try:
            again.open()
            break
        except PanelRestarting:
            assert time.monotonic() < deadline
            time.sleep(0.05)
    try:
        stream_frames(again, moving_frames(20))
        settle(panel)
    finally:
        again.close()
    assert counters.panel_restarts == 1 and counters.blocks > sent
    assert counters.bytes == sum(len(block) for block in panel.blocks)


def test_pictures_waiting_for_the_sender_when_a_stream_ends_are_counted_as_dropped():
    display = TurzxVideoDisplay(video_config())
    display._pictures.put(b"one")
    display._pictures.put(b"two")
    display._discard_pictures()
    assert display.counters.drops == 2 and display._pictures.empty()
    display._discard_pictures()
    assert display.counters.drops == 2


def test_the_png_path_counts_bytes_and_a_reconnect(panel):
    counters = Counters()
    display = turzx_usb.TurzxUsbDisplay(DeviceConfig(driver="turzx", model="turing-9.2-usb"))
    display.counters = counters
    display.open()
    try:
        frame = Image.new("RGB", (1920, 480), (10, 40, 90))
        size = len(turzx_usb.encode_frame(turzx_usb.to_native(frame)))
        display.show(frame)
        display.show(frame)
        assert counters.bytes == 2 * size and counters.usb_errors == 0
        panel.fail_next_writes = 1
        display.show(frame)  # reconnects and sends it again
        assert counters.usb_errors == 1 and counters.bytes == 3 * size
    finally:
        display.close()


def test_a_frame_s_bytes_come_in_one_piece_and_as_tobytes_makes_them(monkeypatch):
    from libre_panel.devices import turzx_video

    for mode in ("RGB", "RGBA"):
        frame = Image.effect_noise((64, 24), 60).convert(mode)
        assert turzx_video.rgb_bytes(frame) == frame.tobytes("raw", "RGB")

    real, calls = Image._getencoder, []

    def other_pillow(*args):  # one whose encoders take other arguments: tobytes still works
        calls.append(args)
        if len(calls) == 1:
            raise TypeError("setimage() takes other arguments")
        return real(*args)

    monkeypatch.setattr(Image, "_getencoder", other_pillow)
    frame = Image.new("RGBA", (5, 3), (10, 20, 30, 40))
    assert turzx_video.rgb_bytes(frame) == bytes((10, 20, 30)) * 15 and len(calls) == 2


# -- how the frames reach ffmpeg (video.pixels) ----------------------------------------------


def command_for(encoder, pixels, size=(1920, 720), rotate=270, card=False):
    return ffmpeg_command(
        "ffmpeg",
        size,
        rotate,
        EncoderSettings(encoder=encoder, pixels=pixels, maxrate="3.0M", card=card),
    )


def after(command, flag):
    return command[command.index(flag) + 1]


def test_four_bytes_a_pixel_go_in_and_the_processor_converts_them_unless_the_card_may():
    """The processor's way feeds rgb0 (Pillow's four bytes a pixel, the fourth left aside) to
    every encoder and ffmpeg turns them into the encoder's YUV (NV12 for the cards', yuv420p for
    x264: swscale, BT.601 in limited range); every encoder is told so, and every stream is
    stamped so after it (h264_metadata). The card's way lets AMD's, Intel's and NVIDIA's convert
    themselves (card_chain: no NV12 made by ffmpeg on the processor); x264 has none. ffmpeg
    turns the frames for all of them. "rgb" is the feed of before, to the letter."""
    from libre_panel.devices.h264 import (
        COLOUR_TAGS,
        STAMP,
        feed_text,
        passes_through,
        resolve_feed,
    )
    from libre_panel.devices.turzx_video import resolved, video_settings

    def tags(command):
        at = command.index(COLOUR_TAGS[0])
        return command[at : at + len(COLOUR_TAGS)]

    def stamped(command):  # the bitstream filter, last before the output
        return command[-len(STAMP) - 3 : -3] == STAMP and command[-3:] == ["-f", "h264", "-"]

    assert COLOUR_TAGS == ["-colorspace", "smpte170m", "-color_primaries", "smpte170m",
                           "-color_trc", "smpte170m", "-color_range", "tv"]  # fmt: skip
    assert STAMP == ["-bsf:v", "h264_metadata=video_full_range_flag=0:colour_primaries=6"
                     ":transfer_characteristics=6:matrix_coefficients=6"]  # fmt: skip
    assert [resolve_feed(way) for way in ("auto", "processor", "card", "rgb")] == [
        "rgb0", "rgb0", "rgb0", "rgb24"]  # fmt: skip
    for encoder in ("h264_amf", "h264_nvenc", "h264_qsv"):
        own = command_for(encoder, "rgb0", card=True)  # the card's way
        assert after(own, "-pix_fmt") == "rgb0" and "nv12" not in own[own.index("-c:v") :]
        assert after(own, "-vf").startswith("transpose=1") and after(own, "-s") == "1920x720"
        assert tags(own) == COLOUR_TAGS and stamped(own)
        assert passes_through(EncoderSettings(encoder=encoder, pixels="rgb0", card=True))
        for pixels in ("rgb0", "rgb24"):  # the processor's way and "rgb": ffmpeg makes NV12
            fed = command_for(encoder, pixels)
            assert after(fed, "-pix_fmt") == pixels and fed.count("-pix_fmt") == 2
            assert fed[fed.index("-pix_fmt", fed.index("-c:v")) + 1] == "nv12"
            assert after(fed, "-vf") == "transpose=1" and "-init_hw_device" not in fed
            assert tags(fed) == COLOUR_TAGS and stamped(fed)
            assert not passes_through(EncoderSettings(encoder=encoder, pixels=pixels))
        assert not passes_through(EncoderSettings(encoder=encoder, pixels="rgb24", card=True))
    base = video_settings(VideoConfig())
    for encoder in ("h264_amf", "h264_nvenc", "h264_qsv"):
        assert resolved(base, "turing-12.3-usb", encoder, "card").card
        for way in ("auto", "processor", "rgb"):  # ("auto" is decided by the display)
            assert not resolved(base, "turing-12.3-usb", encoder, way).card
    assert not resolved(base, "turing-12.3-usb", "libx264", "card").card  # x264 has no card
    full = [e for e in ("h264_amf", "h264_nvenc", "h264_qsv")
            if resolved(base, "turing-12.3-usb", e, "card", full=True).card]  # fmt: skip
    assert full == ["h264_amf", "h264_qsv"]  # NVIDIA's makes limited range only
    x264 = command_for("libx264", "rgb0", card=True)  # (told to: there is nothing to tell)
    assert after(x264, "-pix_fmt") == "rgb0" and "yuv420p" in x264 and tags(x264) == COLOUR_TAGS
    assert stamped(x264) and after(x264, "-vf") == "transpose=1"
    for encoder in ("libx264", "h264_qsv", "h264_amf", "h264_nvenc"):  # the same but what goes in
        old, new = command_for(encoder, "rgb24"), command_for(encoder, "rgb0")
        assert new == [("rgb0" if word == "rgb24" else word) for word in old]
    for encoder in ("libx264", "h264_qsv", "h264_amf", "h264_nvenc"):
        portrait = command_for(encoder, "rgb0", (480, 1920), 180)
        assert after(portrait, "-vf") == "hflip,vflip" and after(portrait, "-s") == "480x1920"
    assert EncoderSettings().pixels == "rgb24"  # (the command of before, as the tools check it)
    assert feed_text(EncoderSettings(encoder="h264_amf", pixels="rgb0")) == "h264_amf, rgb0"
    card = EncoderSettings(encoder="h264_amf", pixels="rgb0", card=True)
    assert feed_text(card) == "h264_amf, rgb0, card"


def test_the_pixels_setting():
    assert parse_config({}).device.video.pixels == "auto"
    assert parse_config({"video": {"pixels": "rgb"}}).device.video.pixels == "rgb"
    assert parse_config({"video": {"pixels": "card"}}).device.video.pixels == "card"
    for bad in ("rgba", 1, "", "processor"):
        with pytest.raises(ConfigError, match="video.pixels"):
            parse_config({"video": {"pixels": bad}})
    two = parse_config({"video": {"pixels": "rgb"}, "panels": [{"device": {"model": "auto"}}]})
    assert two.for_panel(0).device.video.pixels == "rgb"  # a panel beside: the first one's


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("size, rotate, turn", [((1920, 720), 270, "transpose=1"),
                                                ((480, 1920), 180, "hflip,vflip")])  # fmt: skip
def test_the_card_s_chain_for_each_encoder_range_and_turn(full, size, rotate, turn):
    """The card's way, exactly: AMD's frames turned on the processor, made RGBA (AMF takes no
    RGB0), uploaded to an AMF device and converted by vpp_amf with BT.601 and the panel's range;
    Intel's made BGRA, said to be full-range RGB and converted by vpp_qsv with the matrix,
    primaries, transfer and range outright; NVIDIA's taken as they are (its own converter, no
    filter can tell it a matrix): limited range only, so on a full-range panel the processor
    converts for it. Each stream told and stamped the range it has. The processor's way in full
    range: swscale's BT.601 into 0-255."""
    from libre_panel.devices.h264 import FULL_RANGE, colour_tags, stamp

    range_ = "pc" if full else "tv"

    def make(encoder, card):
        settings = EncoderSettings(encoder=encoder, pixels="rgb0", maxrate="3.0M", card=card,
                                   full=full)  # fmt: skip
        return ffmpeg_command("ffmpeg", size, rotate, settings)

    def head(command):  # what comes before the input
        return command[: command.index("-f")]

    amf = make("h264_amf", True)
    assert head(amf) == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-init_hw_device",
                         "amf=amf", "-filter_hw_device", "amf"]  # fmt: skip
    assert after(amf, "-vf") == (
        f"{turn},format=rgba,hwupload,vpp_amf=format=nv12:color_profile=bt601"
        f":color_range={'full' if full else 'studio'}:primaries=smpte170m:trc=smpte170m"
    )
    qsv = make("h264_qsv", True)
    assert head(qsv) == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-init_hw_device",
                         "qsv=qsv", "-filter_hw_device", "qsv"]  # fmt: skip
    assert after(qsv, "-vf") == (
        f"{turn},format=bgra,setrange=full,vpp_qsv=format=nv12:out_range={range_}"
        ":out_color_matrix=smpte170m:out_color_primaries=smpte170m:out_color_transfer=smpte170m"
    )
    nvenc = make("h264_nvenc", True)
    assert head(nvenc) == ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    assert after(nvenc, "-vf") == turn and nvenc.count("-pix_fmt") == 1
    for command in (amf, qsv, nvenc):
        assert command.count("-pix_fmt") == 1 and after(command, "-pix_fmt") == "rgb0"
        assert after(command, "-color_range") == range_
        assert command[-5:] == [*stamp(full), "-f", "h264", "-"]
        assert all(word in command for word in colour_tags(full))
    for encoder, made in (("h264_amf", "nv12"), ("h264_nvenc", "nv12"), ("libx264", "yuv420p")):
        processor = make(encoder, False)
        assert "-init_hw_device" not in processor
        assert after(processor, "-vf") == (f"{turn},{FULL_RANGE},format={made}" if full else turn)
        assert after(processor, "-color_range") == range_
        assert processor[-5:] == [*stamp(full), "-f", "h264", "-"]
    assert FULL_RANGE == "scale=out_range=full:out_color_matrix=bt601"


@needs_ffmpeg
@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("pixels", ["auto", "rgb", "card"])
def test_every_stream_says_it_is_bt601_in_its_range(pixels, full):
    """Whichever way the colours were converted, the stream says BT.601 in the panel's range
    (limited: tv; full: pc), as ffprobe reads it: x264's as told; and one an encoder tagged
    otherwise (as AMD's did: full range and BT.709) is stamped so after it, its SPS and PPS
    before every keyframe as before, one slice a picture. In full range black is 0, else 16."""
    from libre_panel.devices.h264 import COLOUR_TAGS, PictureSplitter, colour_tags, stamp
    from libre_panel.devices.turzx_video import frame_bytes, resolved, video_settings

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("needs ffprobe")
    settings = resolved(video_settings(VideoConfig(keyframe_s=1.0)), "turing-9.2-usb",
                        "libx264", pixels, full=full)  # fmt: skip
    picture = Image.new("RGB", (1920, 480), (200, 40, 60))
    picture.paste((0, 0, 0), (0, 0, 960, 480))
    frames = frame_bytes(picture, settings.pixels) * 60
    plain = ffmpeg_command(FFMPEG, (1920, 480), 270, settings)
    tags = colour_tags(full)
    assert tags[:-1] == COLOUR_TAGS[:-1]
    wrong = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
             "-color_range", "tv" if full else "pc"]  # fmt: skip
    at = plain.index(tags[0])
    mistagged = plain[:at] + wrong + plain[at + len(tags) :]  # (the encoder's own tags)
    unstamped = [word for word in plain if word not in stamp(full)]
    seen = {}
    for name, command in (("plain", plain), ("mistagged", mistagged), ("unstamped", unstamped)):
        stream = subprocess.run(command, input=frames, capture_output=True, check=True).stdout
        probed = subprocess.run(
            [ffprobe, "-v", "error", "-f", "h264", "-i", "-", "-show_entries",
             "stream=color_range,color_space,color_primaries,color_transfer", "-of", "json"],
            input=stream, capture_output=True, check=True,
        ).stdout  # fmt: skip
        splitter = PictureSplitter()
        pictures = splitter.feed(stream) + splitter.flush()
        kinds = [tuple(kind for _, kind in nal_units(p)) for p in pictures]
        seen[name] = (json.loads(probed)["streams"][0], kinds, splitter.multi_slice, stream)
    bt601 = {"color_range": "pc" if full else "tv", "color_space": "smpte170m",
             "color_primaries": "smpte170m", "color_transfer": "smpte170m"}  # fmt: skip
    assert seen["plain"][0] == bt601 and seen["mistagged"][0] == bt601  # stamped over
    # (x264 as told: the range and the matrix; the rest an ffmpeg 8 leaves to the stamp)
    assert {key: seen["unstamped"][0].get(key) for key in ("color_range", "color_space")} == {
        "color_range": bt601["color_range"], "color_space": "smpte170m"}  # fmt: skip
    kinds = seen["plain"][1]
    assert len(kinds) == 60 and kinds == seen["unstamped"][1]  # the stamp moves no picture
    assert kinds[0][:2] == (7, 8) and kinds[0][-1] == NAL_IDR and kinds[1] == (1,)
    assert sum(1 for k in kinds if k[:2] == (7, 8)) == sum(1 for k in kinds if NAL_IDR in k) >= 2
    assert not any(multi for _, _, multi, _ in seen.values())
    luma = subprocess.run(  # the values themselves, as the panel's decoder takes them: the
        [FFMPEG, "-v", "error", "-f", "h264", "-i", "-", "-frames:v", "1", "-f", "rawvideo",
         "-"],  # decoder's own 4:2:0, converted to nothing (its Y plane first)
        input=seen["plain"][3], capture_output=True, check=True,
    ).stdout  # fmt: skip
    black = Image.frombytes("L", (480, 1920), luma[: 480 * 1920]).crop((100, 200, 380, 700))
    assert round(ImageStat.Stat(black).mean[0]) == (0 if full else 16)


def test_the_video_range_setting_per_panel_and_by_model():
    """[device] video_range: "auto" (the default) as the model is known to show video, full on
    the 9.2" and the 12.3" (their owner saw both washed out in limited range) and limited
    elsewhere; "limited" or "full" for that panel; each [[panels]] table its own."""
    from libre_panel.devices.models import get_model
    from libre_panel.devices.turzx_video import full_range

    assert parse_config({}).device.video_range == "auto"
    assert get_model("turing-12.3-usb").video_range == get_model("turing-9.2-usb").video_range
    assert get_model("turing-12.3-usb").video_range == "full"
    assert get_model("turing-8.8-usb").video_range == "limited"
    assert full_range("auto", get_model("turing-12.3-usb"))
    assert full_range("auto", get_model("turing-9.2-usb"))
    assert not full_range("auto", get_model("turing-8.8-usb")) and not full_range("auto", None)
    assert full_range("full", get_model("turing-8.8-usb"))
    assert not full_range("limited", get_model("turing-12.3-usb"))
    panels = [{"model": "auto", "video_range": "limited"}, {"model": "auto"}]
    two = parse_config({"device": {"video_range": "full"}, "panels": panels})
    assert two.device.video_range == "full"
    assert two.for_panel(0).device.video_range == "limited"
    assert two.for_panel(1).device.video_range == "auto"  # its own, not the first one's
    for bad in ("tv", "pc", 1, ""):
        with pytest.raises(ConfigError, match="video_range"):
            parse_config({"device": {"video_range": bad}})
    with pytest.raises(ConfigError, match=r"panels\[0\]\.video_range"):
        parse_config({"panels": [{"video_range": "pc"}]})


def card_stand_in(monkeypatch, shift=""):
    """x264 as one of the card's encoders, for the machine without a graphics card: its "card"
    way a software chain behind the same function (h264.card_chain), with a ``null`` filter to
    tell it by; ``shift`` its own conversion's options (``out_color_matrix=bt709``: a matrix
    shift, as AMD's own converter made), else swscale's BT.601 as the processor's."""
    from libre_panel.devices.h264 import FULL_RANGE

    real = h264.card_chain

    def chain(encoder, full=False):
        if encoder != "libx264":
            return real(encoder, full)
        # (said to be BT.601 after it, so that an ffmpeg that converts to what its output is
        # told, as 8.x does, leaves the shift as it is)
        told = f"setparams=range={'pc' if full else 'tv'}:colorspace=smpte170m"
        own = f"scale={shift},format=yuv420p,{told}," if shift else ""
        return [], ["null", (f"{FULL_RANGE}," if full and not shift else own) + "format=yuv420p"]

    monkeypatch.setattr(h264, "card_chain", chain)
    monkeypatch.setattr(h264, "CARD_ENCODERS", h264.CARD_ENCODERS | {"libx264"})
    monkeypatch.setattr(h264, "FULL_RANGE_CARDS", h264.FULL_RANGE_CARDS | {"libx264"})


def by_card(command):
    """Whether the card's way made ``command`` (the stand-in's ``null`` filter)."""
    return "-vf" in command and "null" in after(command, "-vf").split(",")


@needs_ffmpeg
@pytest.mark.parametrize("shift, card", [("", True), ("out_color_matrix=bt709", False)])
def test_auto_lets_the_card_convert_once_a_check_in_the_background_found_it_right(
    panel, monkeypatch, isolated_home, shift, card
):
    """video.pixels "auto" with one of the card's encoders and no verdict yet: the processor
    converts, and a check runs once in the background (never on the panel: the panel gets the
    processor's pictures meanwhile); where it finds the card's conversion identical the panel
    goes over to it at its next frame (a new ffmpeg, the panel stays open), where it finds a
    shift the processor stays. The verdict is kept for the next start. x264 stands in."""
    from libre_panel.devices import card_choice

    card_stand_in(monkeypatch, shift)
    commands = []
    real = turzx_video.ffmpeg_command

    def command(*args):
        commands.append(real(*args))
        return commands[-1]

    monkeypatch.setattr(turzx_video, "ffmpeg_command", command)
    began = []
    real_ensure = card_choice.ensure
    monkeypatch.setattr(card_choice, "ensure",
                        lambda *a, **k: began.append(a[:3]) or real_ensure(*a, **k))  # fmt: skip
    video = 'mode = "on"\nlocal_clip = "usr/data/clip.h264"\n'
    opened, status, stop, thread = running_panel(isolated_home, monkeypatch, panel, video)
    ffmpeg = find_ffmpeg("")
    try:
        assert wait_until(lambda: status.frames > 5 and commands)
        assert not by_card(commands[0])  # the processor's until the check has spoken
        assert wait_until(lambda: card_choice.verdict(ffmpeg, "libx264", False) is not None)
        kept = card_choice.verdict(ffmpeg, "libx264", False)
        assert kept["card"] is card and kept["by"] == "background"
        assert kept["verdict"] == ("identical" if card else "matrix")
        if card:
            assert wait_until(lambda: by_card(commands[-1]))
            assert wait_until(lambda: status.stats.get("encoder", "").endswith(", card"))
        else:
            time.sleep(1.5)
            assert not any(by_card(c) for c in commands)
        assert len(opened) == 1 and panel.commands.count(110) == 1
        arrived = len(panel.blocks)
        assert wait_until(lambda: len(panel.blocks) > arrived + 10)
    finally:
        stop.set()
        thread.join(20)
    assert not thread.is_alive() and not NEVER & set(panel.commands)
    assert began and all(b == (ffmpeg, "libx264", False) for b in began)
    entry = video_state._part("cards")[f"libx264 limited {ffmpeg}"]
    assert entry["card"] is card and entry["version"] == card_choice.version(ffmpeg)


def test_who_converts_under_auto_and_when_a_way_is_forced(monkeypatch, isolated_home):
    """The display's choice (:meth:`_pixels`): "auto" the card only where a verdict for that
    encoder, range, ffmpeg and version says identical; never for x264, for NVIDIA's on a panel
    in full range, or for an encoder that gave up converting itself; "card" and "rgb" force a
    way (the card only where it can); a colour check's A or B overrides them all."""
    from libre_panel.devices import card_choice

    isolated_home.mkdir(parents=True)  # (config.toml's folder: video-state.json is kept there)
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: "ffmpeg version 8.1 | driver 1")

    def result(encoder, verdict="identical", state="done"):
        return {"encoder": encoder, "compared": ["card", "processor"], "state": state,
                "verdict": verdict, "worst": None}  # fmt: skip

    def way(encoder, pixels="auto", video_range="limited", model="turing-9.2-usb"):
        config = video_config(pixels=pixels)
        config.video_range, config.model = video_range, model
        from libre_panel.devices.models import get_model

        display = TurzxVideoDisplay(config, get_model(model))
        display._ffmpeg = "/ffmpeg"
        return display, display._pixels(encoder)

    assert way("h264_amf")[1] == "processor"  # no verdict yet
    card_choice.record("/ffmpeg", result("h264_amf"), full=False)
    assert way("h264_amf")[1] == "card"
    assert way("h264_amf", video_range="full")[1] == "processor"  # (no verdict in full range)
    assert way("h264_amf", video_range="auto")[1] == "processor"  # the 9.2" is full by itself
    assert way("h264_amf", "auto", "auto", "turing-12.3-usb")[1] == "processor"  # full by itself
    card_choice.record("/ffmpeg", result("h264_amf"), full=True)
    assert way("h264_amf", "auto", "auto", "turing-12.3-usb")[1] == "card"
    assert way("h264_amf", video_range="auto")[1] == "card"  # and the 9.2" too
    assert way("h264_amf", model="turing-12.3-usb", video_range="limited")[1] == "card"
    card_choice.record("/ffmpeg", result("h264_amf", "matrix"), full=False)
    assert way("h264_amf")[1] == "processor"
    card_choice.record("/ffmpeg", result("h264_nvenc"), full=False)
    assert way("h264_nvenc")[1] == "card"
    assert card_choice.record("/ffmpeg", result("h264_nvenc"), full=True) is None  # can't
    assert way("h264_nvenc", video_range="full")[1] == "processor"
    assert way("h264_nvenc", "card", video_range="full")[1] == "processor"  # limited only
    assert way("libx264")[1] == way("libx264", "card")[1] == "processor"
    assert way("h264_qsv", "card")[1] == "card" and way("h264_qsv", "rgb")[1] == "rgb"
    display, _ = way("h264_nvenc")
    display._feeds_failed.add("h264_nvenc")  # it gave no picture converting itself
    assert display._pixels("h264_nvenc") == "processor"
    display.feed = "card"
    assert display._pixels("h264_nvenc") == "processor"
    display, _ = way("h264_amf", "card")
    display.feed = "processor"  # B of a check's A/B
    assert display._pixels("h264_amf") == "processor"
    card_choice.forget()
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: "ffmpeg version 8.2 | driver 1")
    assert way("h264_nvenc")[1] == "processor"  # a new ffmpeg: no verdict until checked again
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: "ffmpeg version 8.1 | driver 2")
    card_choice.forget()
    assert way("h264_nvenc")[1] == "processor"  # a new driver too
    monkeypatch.setattr(card_choice, "version", lambda ffmpeg: "ffmpeg version 8.1 | driver 1")
    card_choice.forget()
    assert way("h264_nvenc")[1] == "card"
    week = time.time() + video_state.PROBE_S + 60
    monkeypatch.setattr(time, "time", lambda: week)
    card_choice.forget()
    assert way("h264_nvenc")[1] == "processor"  # a week later: checked again


@needs_ffmpeg
def test_a_card_that_gives_no_picture_converting_itself_gets_the_processor_then_x264(
    panel, monkeypatch, caplog
):
    """One of the card's encoders that ends before its first picture converting the frames
    itself ("card") is started again with the processor's conversion (NV12 made by ffmpeg: a
    driver may take no RGB); when that ends too, "auto" leaves it out and x264 takes over, fed
    rgb0. None of it counts as a restart, and the panel shows the frames. (NVIDIA's encoder
    ends here at once: no card.)"""
    monkeypatch.setattr(h264, "AUTO_ENCODERS", ("h264_nvenc",))
    monkeypatch.setattr(h264, "encoder_works", lambda ffmpeg, encoder: True)
    commands = []
    real = turzx_video.ffmpeg_command

    def command(*args):
        commands.append(real(*args))
        return commands[-1]

    monkeypatch.setattr(turzx_video, "ffmpeg_command", command)
    display = TurzxVideoDisplay(video_config(pixels="card"))
    display.open()
    frames = moving_frames(80)
    try:
        with caplog.at_level("WARNING", logger="libre_panel.devices.turzx_video"):
            for frame in frames:
                display.show(frame)
                time.sleep(0.02)
                if display.encoder.pictures >= 20:
                    break
        assert display.encoder.name == "libx264", [c[c.index("-c:v") + 1] for c in commands]
        fed = [(c[c.index("-c:v") + 1], "nv12" in c[c.index("-c:v") :]) for c in commands]
        assert fed == [("h264_nvenc", False), ("h264_nvenc", True), ("libx264", False)]
        assert all(after(c, "-pix_fmt") == "rgb0" for c in commands)
        assert display._feeds_failed == {"h264_nvenc"} == display._encoders_failed
        assert display.counters.ffmpeg_restarts == 0
        assert display.counters.encoder == "libx264, rgb0"
        said = [r.getMessage() for r in caplog.records]
        assert any("ffmpeg converts the frames" in s for s in said)
        assert any("x264 takes over" in s for s in said)
        settle(panel, timeout=15, at_least=10)
    finally:
        display.close()
    assert len(panel.blocks) >= 10
    shown = [frame_number(picture) for picture in decode(panel.stream())]
    assert all(a < b for a, b in itertools.pairwise(shown))  # x264's frames, in their order


def test_only_a_card_that_ended_converting_itself_gets_the_processor_next(monkeypatch):
    """The step back to the processor's conversion is for an encoder of the card that ended
    (exited) before its first picture converting the frames itself, once; one that hangs, that
    showed pictures, the processor's way or x264, does not take it; a card's encoder set by hand
    then counts as a restart, as before."""
    display = TurzxVideoDisplay(video_config(encoder="h264_nvenc", pixels="card"))

    def stopped(card=True, alive=False, pictures=0, name="h264_nvenc", pixels="rgb0"):
        settings = EncoderSettings(encoder=name, pixels=pixels, card=card)
        display.encoder = SimpleNamespace(name=name, pictures=pictures, alive=alive,
                                          settings=settings)  # fmt: skip
        return display._gave_up("exit code 1")

    assert stopped(alive=True) is False  # it hangs: no matter of the format
    assert stopped(pictures=3) is False  # it showed pictures: a restart
    assert stopped(name="libx264") is False and display._feeds_failed == set()
    assert stopped(pixels="rgb24") is False and display._feeds_failed == set()
    assert stopped() is True and display._feeds_failed == {"h264_nvenc"}
    assert stopped() is False  # (once)
    assert stopped(card=False) is False  # set by hand: then a restart, counted
    assert display._encoders_failed == set()


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L"])
@pytest.mark.parametrize("pixels", ["rgb24", "rgb0"])
def test_a_frame_goes_into_the_pipe_as_its_bytes(mode, pixels):
    """write_frame: Pillow packs the frame into the pipe itself, the bytes frame_bytes makes;
    without a file descriptor (or with another Pillow) the bytes are written, all of them."""
    import io
    import os

    from libre_panel.devices.turzx_video import frame_bytes, write_frame

    frame = Image.effect_noise((64, 24), 60).convert("RGB").convert(mode)
    wanted = frame_bytes(frame, pixels)
    assert len(wanted) == 64 * 24 * (4 if pixels == "rgb0" else 3)
    if mode != "L":
        raw = "RGBA" if pixels == "rgb0" else "RGB"
        assert wanted == frame.tobytes("raw", raw)
    read, write = os.pipe()
    got = []  # read while it is written: Windows' pipe holds 4 KB, less than the frame
    reader = threading.Thread(target=lambda: got.append(open(read, "rb").read()), daemon=True)
    reader.start()
    with open(write, "wb", buffering=0) as out:
        write_frame(out, frame, pixels)
    reader.join(10)
    assert got == [wanted]
    kept = io.BytesIO()  # no file descriptor
    write_frame(kept, frame, pixels)
    assert kept.getvalue() == wanted


@needs_ffmpeg
@pytest.mark.parametrize("size, rotate", [((1920, 480), 270), ((480, 1920), 180)])
def test_x264_makes_the_same_stream_of_four_bytes_a_pixel(size, rotate):
    """swscale makes the same YUV of rgb0 as of rgb24 (the fourth byte, the canvas's alpha as
    the renderer's RGB frames keep it, is left aside): x264's stream is the same, bit for bit,
    landscape (turned) and portrait (upside down)."""
    from libre_panel.devices.turzx_video import frame_bytes

    frames = [frame.resize(size) for frame in noisy_frames(6)]
    see_through = Image.new("RGBA", size, (200, 30, 90, 7))  # an alpha that is no colour's
    for frame in frames:
        frame.paste(see_through, (0, 0), Image.new("L", size, 0))  # (pixels kept, alpha in)
    streams = []
    for pixels in ("rgb24", "rgb0"):
        command = ffmpeg_command(FFMPEG, size, rotate, EncoderSettings(pixels=pixels, fps=25))
        data = b"".join(frame_bytes(frame, pixels) for frame in frames)
        streams.append(subprocess.run(command, input=data, capture_output=True, check=True).stdout)
    assert streams[0] and streams[0] == streams[1]


@needs_ffmpeg
@pytest.mark.parametrize("pixels", ["auto", "rgb"])
def test_a_portrait_theme_comes_out_upside_down_as_before(panel, pixels):
    """A theme on end goes into the 9.2"'s framebuffer turned by 180 degrees (as to_native),
    with either feed; what reaches the panel decodes to that."""
    display = TurzxVideoDisplay(video_config(pixels=pixels))
    display.open()
    frames = [frame.transpose(Image.Transpose.ROTATE_90) for frame in moving_frames(30)]
    try:
        stream_frames(display, frames)
        fed = "rgb0" if pixels == "auto" else "rgb24"
        assert after(display.encoder.command, "-pix_fmt") == fed
        assert "hflip,vflip" in display.encoder.command
        assert display.counters.encoder == f"libx264, {fed}"
        settle(panel, timeout=15, at_least=25)
    finally:
        display.close()
    decoded = decode(panel.stream())
    assert len(decoded) >= 25
    for source, picture in zip(frames, decoded, strict=False):
        native = turzx_usb.to_native(source)
        assert max(ImageStat.Stat(ImageChops.difference(native, picture)).mean) < 6


# -- a theme that is one video alone, straight from its file ([video] direct) ---------------------


def clip(path, colour="red", size="320x240", seconds=2):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:size={size}:rate=30", "-t", str(seconds),
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=60)  # fmt: skip
    return path


def test_a_video_alone_is_encoded_from_its_file_as_frames_are():
    """ffmpeg reads the file round and round at its pace (times its speed), fits it as the
    picture widget does, turns it into the framebuffer and encodes it with the frames' own
    settings: the panel gets the stream it was measured with."""
    settings = EncoderSettings(fps=30, encoder="libx264", profile="high", maxrate="3M")
    source = h264.VideoSource("/videos/loop.mp4", "cover", 1.0, "#0a0e13")
    command = h264.ffmpeg_file_command("ffmpeg", source, (1920, 480), 270, settings)
    assert command[command.index("-i") - 3 : command.index("-i") + 2] == [
        "-re", "-stream_loop", "-1", "-i", "/videos/loop.mp4"]  # fmt: skip
    assert "-nostdin" in command and "-an" in command
    assert command[command.index("-vf") + 1] == (
        "scale=1920:480:force_original_aspect_ratio=increase,crop=1920:480,fps=30,transpose=1"
    )
    frames = ffmpeg_command("ffmpeg", (1920, 480), 270, settings)
    assert command[command.index("-c:v") :] == frames[frames.index("-c:v") :]  # (the same)
    faster = h264.ffmpeg_file_command(
        "ffmpeg", h264.VideoSource("/v.mp4", "contain", 2.0, "#0a0e13"), (1920, 480), 270,
        EncoderSettings(fps=30, encoder="libx264", profile="high", full=True),
    )  # fmt: skip
    assert faster[faster.index("-readrate") + 1] == "2" and "-re" not in faster
    shown = faster[faster.index("-vf") + 1]
    assert shown.startswith("setpts=PTS/2,scale=1920:480:force_original_aspect_ratio=decrease,")
    assert "pad=1920:480:(ow-iw)/2:(oh-ih)/2:color=#0a0e13" in shown
    assert shown.endswith(f"transpose=1,{h264.FULL_RANGE},format=yuv420p")  # (its range)


@needs_ffmpeg
def test_a_video_alone_goes_from_its_file_and_frames_take_over_again(panel, tmp_path):
    display = TurzxVideoDisplay(video_config())
    display.open()
    source = h264.VideoSource(str(clip(tmp_path / "red.mp4")), "cover", 1.0, "#000000")
    try:
        end = time.monotonic() + 60
        while len(panel.blocks) < 20 and time.monotonic() < end:
            assert display.show_video(source, (1920, 480))  # (each frame: it goes on as it is)
            time.sleep(1 / 30)
        arrive(panel, 20)
        assert display.encoder.source == source and display.encoder.command[-1] == "-"
        from_file = len(panel.blocks)
        stream_frames(display, [Image.new("RGB", (1920, 480), "blue")] * 40, fps=30)
        assert display.encoder.source is None  # frames again
        settle(panel, timeout=20, at_least=from_file + 20)
    finally:
        display.close()
    assert all(slice_count(block) == 1 for block in panel.blocks)  # one picture per block
    decoded = decode(panel.stream())
    red, blue = decoded[2].getpixel((240, 960)), decoded[-1].getpixel((240, 960))
    assert red[0] > 200 and red[2] < 60, red  # the video, turned into the framebuffer
    assert blue[2] > 200 and blue[0] < 60, blue  # then the frames


@needs_ffmpeg
def test_a_video_that_ffmpeg_cannot_play_from_its_file_has_frames(panel, tmp_path):
    (tmp_path / "broken.mp4").write_bytes(b"no video in here")
    display = TurzxVideoDisplay(video_config())
    display.open()
    source = h264.VideoSource(str(tmp_path / "broken.mp4"), "cover", 1.0, "#000000")
    try:
        assert display.show_video(source, (1920, 480))  # (started: ffmpeg says soon)
        end = time.monotonic() + 30
        while display.encoder.alive and time.monotonic() < end:
            time.sleep(0.05)
        assert not display.show_video(source, (1920, 480))  # it never played: frames for it
        assert not display.show_video(source, (1920, 480)) and display.encoder.source is None
        stream_frames(display, moving_frames(20), fps=30)
        arrive(panel, 5)
    finally:
        display.close()


class VideoDisplay(StreamingDisplay):
    """A panel that takes a video alone from its file."""

    def __init__(self, config):
        super().__init__(config)
        self.videos = []

    def show_video(self, source, size):
        self.videos.append((source, size))
        return True


@needs_ffmpeg
@pytest.mark.parametrize("direct", [True, False])
def test_the_main_loop_hands_a_video_alone_to_the_panel(monkeypatch, isolated_home, direct):
    from libre_panel.config import user_themes_dir

    folder = user_themes_dir() / "loop"
    (folder / "assets").mkdir(parents=True)
    clip(folder / "assets" / "loop.mp4", size="480x320", seconds=1)
    (folder / "theme.json").write_text(json.dumps({
        "format": "libre-panel-theme/1", "name": "Loop",
        "display": {"model": "custom", "width": 480, "height": 320},
        "widgets": [{"type": "image", "id": "v", "x": 0, "y": 0, "w": 480, "h": 320,
                     "src": "assets/loop.mp4", "fit": "cover"}],
    }), encoding="utf-8")  # fmt: skip
    displays, renderers = [], []
    monkeypatch.setattr(app, "create_display", lambda config: displays.append(
        VideoDisplay(config)) or displays[-1])  # fmt: skip
    real_renderer = app.Renderer
    monkeypatch.setattr(app, "Renderer", lambda *a, **k: renderers.append(
        real_renderer(*a, **k)) or renderers[-1])  # fmt: skip
    config = Config(theme="loop", sensors=SensorsConfig(providers=["demo"]),
                    device=DeviceConfig(video=VideoConfig(direct=direct)))  # fmt: skip
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    thread.start()
    end = time.monotonic() + 60
    while time.monotonic() < end and not (displays and (displays[0].videos or
                                                         len(displays[0].shown) > 30)):  # fmt: skip
        time.sleep(0.05)
    stop.set()
    thread.join(10)
    if direct:
        source, size = displays[0].videos[-1]
        assert source.path.endswith("loop.mp4") and source.fit == "cover" and size == (480, 320)
        assert renderers[0].video_fps == app.DIRECT_PICTURE_FPS  # (for the app's picture)
    else:
        assert not displays[0].videos and renderers[0].video_fps is None


def test_direct_videos_change_on_the_open_panel():
    assert app._same_device(DeviceConfig(), DeviceConfig(video=VideoConfig(direct=True)))
    assert parse_config({"video": {"direct": True}}).device.video.direct
    with pytest.raises(ConfigError, match="video.direct"):
        parse_config({"video": {"direct": "yes"}})

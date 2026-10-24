"""Video mode: H.264 pictures, the video session, the display and the main loop's pacing."""

import itertools
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
from libre_panel.devices import turzx_usb, turzx_video  # noqa: E402
from libre_panel.devices.base import Counters, DeviceError, Display  # noqa: E402
from libre_panel.devices.h264 import (  # noqa: E402
    EncoderSettings,
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
    assert "-profile:v baseline" in text and "repeat-headers=1" in text
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
    control, c = RateControl(3.0), Counters()
    assert control.judge(c, 0.0) is None  # the first window begins
    c.waits += RateControl.TROUBLE_WAITS  # within the window: the panel's decoder lags
    assert control.judge(c, 1.0) == 2.0
    assert all(control.judge(c, float(second)) is None for second in range(2, 300))
    assert control.rate == 2.0  # a calm while, but 3.0 failed lately
    assert control.judge(c, 602.0) == 3.0  # ten minutes later it is tried again
    c.blocks += 100
    c.slow_blocks += RateControl.TROUBLE_SLOW  # a quarter of the blocks slow: a busy PC
    assert control.judge(c, 603.0) is None
    c.slow_blocks += RateControl.TROUBLE_SLOW  # half of them: the panel's link lags
    assert control.judge(c, 605.0) == 2.0
    assert control.judge(c, 1000.0) is None  # now held off for twenty minutes
    assert control.judge(c, 1900.0) == 3.0
    assert control.changes == 4

    climbing, calm = RateControl(2.0), Counters()  # calm all along: up the steps to the top
    assert climbing.judge(calm, 0.0) is None
    seen = [climbing.judge(calm, float(second)) for second in range(1, 400)]
    assert [rate for rate in seen if rate] == [3.0, 4.0, 6.0, 8.0, 12.0, 16.0]

    floor = RateControl(1.0)
    assert floor.judge(calm, 0.0) is None
    calm.waits += 9
    assert floor.judge(calm, 1.0) is None and floor.rate == 1.0  # nothing below

    odd = RateControl(2.4)  # a start between the steps is a step of its own
    assert odd.steps == [1.0, 1.5, 2.0, 2.4, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0]

    assert mbit("auto") == 0.0 and mbit("1500k") == 1.5 and mbit("4M") == 4.0
    assert mbit("2000000") == 2.0 and mbit("2.5M") == 2.5


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
    assert (video.mode, video.fps, video.device_fps, video.crf) == ("on", 30, 40, 25)
    assert parse_config({}).device.video.mode == "auto"
    assert parse_config({"video": {"mode": "off"}}).device.video.mode == "off"
    assert parse_config({"video": {"maxrate": "auto"}}).device.video.maxrate == "auto"
    for bad in ({"mode": "always"}, {"fps": 50, "device_fps": 40}, {"maxrate": "fast"}):
        with pytest.raises(ConfigError):
            parse_config({"video": bad})


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


def video_config(**kwargs):
    video = VideoConfig(mode="on", local_clip="usr/data/clip.h264", **kwargs)
    return DeviceConfig(driver="turzx", model="turing-9.2-usb", video=video)


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

    end = time.monotonic() + 5
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
    display = TurzxVideoDisplay(video_config())  # maxrate "auto": 2 Mbit/s on the 9.2"
    display.open()
    try:
        panel.block_delay = 0.05  # 20 blocks a second: every block takes longer than a frame
        frames = noisy_frames(60)
        display.show(frames[0])
        assert display.rate is not None and display.counters.bit_rate == 2.0
        assert "2.0M" in display.encoder.command
        for frame in frames[1:]:
            display.show(frame)
            if display.rate.rate < 2.0:
                break
        assert display.rate.rate == 1.5  # one step down, with ffmpeg started anew
        assert display.counters.bit_rate == 1.5 and "1.5M" in display.encoder.command
        assert turzx_video.RATES[("turing-9.2-usb", (1920, 480))] is display.rate
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
        time.sleep(0.3)
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
    tries, deadline = 0, time.monotonic() + 10
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
    assert status.target == 'Turing 9.2", video 50 fps'
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
    time.sleep(1.0)
    stop.set()
    thread.join(10)
    steps = [b - a for a, b in zip(times[1:], times[2:], strict=False)]
    # never a short step after a late one; on the grid unless the loop fell a
    # whole frame behind (slow runners) and started a new one
    assert min(steps) > 0.02 - 1e-6, min(steps)
    assert sum(abs(step - 0.02) < 1e-6 for step in steps) >= 5


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
    time.sleep(1.2)
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


def running_panel(home, monkeypatch, panel):
    """The main loop on a 9.2" of the simulated bus, with a config.toml in ``home`` that has
    no clip name, as the background app runs it; returns what a test needs to watch and stop it."""
    from libre_panel.config import load_config

    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'theme = "slate"\nfps = 5\n\n[device]\ndriver = "turzx"\nmodel = "turing-9.2-usb"\n\n'
        '[sensors]\nproviders = ["demo"]\n\n[video]\n',
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
        assert wait_until(lambda: status.target == 'Turing 9.2", video 50 fps')
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

    def rate(seconds):
        begin = len(displays[0].shown) if displays else 0
        time.sleep(seconds)
        shown = displays[0].shown[begin:]
        return (len(shown) - 1) / (shown[-1][0] - shown[0][0])

    try:
        time.sleep(0.5)
        normal = rate(1.0)
        host.set_mode("game")
        time.sleep(0.2)
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
        display.encoder.proc.kill()
        display.encoder.proc.wait()
        time.sleep(0.2)
        stream_frames(display, frames[10:])
        settle(panel)
        assert counters.ffmpeg_restarts == 1 and counters.panel_restarts == 0
        assert counters.usb_errors == 0
        assert counters.blocks == len(panel.blocks) >= 15 and counters.bytes > 0
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
        time.sleep(0.3)
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
    deadline = time.monotonic() + 10
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

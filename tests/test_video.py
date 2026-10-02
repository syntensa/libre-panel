"""Video mode: H.264 pictures, the video session, the display and the main loop's pacing."""

import itertools
import shutil
import subprocess
import threading
import time

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
from libre_panel.devices.base import DeviceError, Display  # noqa: E402
from libre_panel.devices.h264 import (  # noqa: E402
    EncoderSettings,
    PictureSplitter,
    ffmpeg_command,
    find_ffmpeg,
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
    assert parse_config({}).device.video.mode == "off"
    for bad in ({"mode": "auto"}, {"fps": 50, "device_fps": 40}, {"maxrate": "fast"}):
        with pytest.raises(ConfigError):
            parse_config({"video": bad})


# -- the session against the simulated panel ------------------------------------


@pytest.fixture
def panel(monkeypatch):
    fake = FakePanel()

    def open_transport(pid=None):
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
def test_an_ffmpeg_that_keeps_stopping_gives_up_but_reconnects_do_not_count(panel):
    display = TurzxVideoDisplay(video_config())
    display.open()
    try:
        frames = moving_frames(10)
        for _ in range(4):  # reconnecting starts ffmpeg again; that is no ffmpeg fault
            stream_frames(display, frames[:3])
            panel.fail_next_writes = 1
            time.sleep(0.2)
        stream_frames(display, frames[:3])
        assert panel.commands.count(110) >= 4
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
        # large pictures; ffmpeg itself holds about 15 frames before it pushes back
        for frame in noisy_frames(45):
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
    assert not png.streaming
    png.close()


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


def test_the_main_loop_draws_streaming_frames_at_their_planned_time(monkeypatch):
    times = []
    real_renderer = app.Renderer

    class Recording(real_renderer):
        def render(self, snapshot, now=None):
            times.append(now)
            return super().render(snapshot, now)

    monkeypatch.setattr(app, "Renderer", Recording)
    monkeypatch.setattr(app, "create_display", StreamingDisplay)
    config = Config(theme="slate", fps=5, sensors=SensorsConfig(providers=["demo"]))
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
    config = Config(theme="slate", fps=5, sensors=SensorsConfig(providers=["demo"]))
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
    # (virtual macOS runners coalesce short sleeps).
    started = time.perf_counter()
    for _ in range(10):
        time.sleep(0.018)
    timer_rate = min(50, 10 / (time.perf_counter() - started))
    assert 0.8 * timer_rate < rate < 55, (rate, timer_rate)
    assert renderers[0].background_builds


def test_device_changes_open_the_panel_again():
    a = DeviceConfig(brightness=10)
    assert app._same_device(a, DeviceConfig(brightness=90))
    assert not app._same_device(a, DeviceConfig(video=VideoConfig(mode="on")))


def test_a_mode_sets_the_stream_rate(monkeypatch):
    """A mode's fps replaces the display's 50 (SPUR II runs slower in game mode).

    10 fps, not a realistic 25-30: virtual macOS runners coalesce short sleeps,
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
    assert 8 < game < 12, (normal, game)
    assert game < normal * 0.6, (normal, game)

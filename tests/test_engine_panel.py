"""Libre Panel's engine with a panel of its own (devices/engine.py, engine/src/panel.rs; stage 1b
of the engine): the simulated panel of fake_panel.py, behind a socket (panel_socket.py), gets
from the engine what it gets from the Python driver, packet for packet; a video panel's
pictures come from the engine, each on its time; a hung decoder, a link that fails and an
engine that cannot open the panel are dealt with as with the Python driver."""

import itertools
import statistics
import struct
import subprocess
import sys
import threading
import time

import pytest
from fake_panel import FakePanel, transport_for
from panel_socket import PanelSocket
from PIL import Image, ImageChops, ImageStat
from test_video import (  # noqa: F401
    NEVER,
    decode,
    hang_quickly,
    needs_ffmpeg,
    panel,
    settle,
    video_config,
)
from test_yuv import moving_square

from libre_panel import light
from libre_panel.devices import h264, turzx_usb, turzx_video
from libre_panel.devices.base import DeviceError
from libre_panel.devices.engine import (
    EngineFailed,
    EngineLink,
    EngineStream,
    EngineTransport,
    can_send,
)
from libre_panel.devices.turzx_usb import brightness_arg, decode_packet, transparent_layer
from libre_panel.devices.turzx_video import (
    PanelRestarting,
    TurzxVideoDisplay,
    VideoSession,
)


@pytest.fixture
def engine():
    """The engine's program, as the app finds it, if it can have a panel; else a skip."""
    found = h264.find_engine()
    if not found or not can_send(found):
        pytest.skip("needs libre-panel-engine with its panel mode (cargo build --release)")
    return found


class Wire:
    """What a panel was sent: each packet's plain text without its clock, with its payload;
    and when each video block came."""

    def __init__(self, fake):
        self.sent, self.blocks_at = [], []
        write = fake.write

        def recorded(data, timeout):
            data = bytes(data)
            plain = decode_packet(data[:512])
            self.sent.append((plain[:4] + plain[8:], data[512:]))
            if plain[0] == 121:
                self.blocks_at.append(time.perf_counter())
            return write(data, timeout)

        fake.write = recorded


def engine_on(engine, fake, *extra):
    """The engine with the simulated panel ``fake`` (behind a socket), as the app starts it."""
    sock = PanelSocket(fake)
    link = EngineLink([engine, "panel", "--pid", f"{fake.pid:04x}", "--link", sock.link, *extra])
    return EngineTransport(link, fake.pid), sock


def arrived(fake, count, timeout=15.0):
    end = time.monotonic() + timeout
    while fake.commands.count(121) < count and time.monotonic() < end:
        time.sleep(0.01)
    assert fake.commands.count(121) >= count


def test_the_engine_sends_the_panel_what_the_python_driver_sends(engine):
    """The commands of a video's start, a brightness, pictures with their flow control and the
    stop: the same packets (but their clock) and the same payloads, in the same order. The
    pictures a frame apart, slower than the panel's player takes them: how often a wait for room
    asks for the queue depends on the machine's timers (video.rs's tests have the waits)."""
    pictures = [b"\x00\x00\x00\x01\x65" + bytes(range(n, n + 40)) for n in range(5)]

    def apart(send):
        for picture in pictures:
            send(picture)
            time.sleep(0.02)

    def run(transport, send):
        transport.sync()
        session = VideoSession(transport, (480, 1920), 60, 40, "usr/data/clip.h264")
        session.start()
        transport.command(14, bytes([brightness_arg(70)]))
        send(session, transport)
        session.stop(transparent_layer((480, 1920)))

    python = FakePanel()
    by_python = Wire(python)
    run(transport_for(python), lambda session, _t: apart(session.send))

    engines = FakePanel()
    by_engine = Wire(engines)
    transport, sock = engine_on(engine, engines)
    try:

        def through_engine(session, t):
            apart(lambda picture: t.link.picture(picture, session.block_size))
            arrived(engines, len(pictures))

        run(transport, through_engine)
    finally:
        transport.close()
        sock.close()
    assert sock.errors == []
    assert [plain[0] for plain, _ in by_engine.sent] == [plain[0] for plain, _ in by_python.sent]
    assert by_engine.sent == by_python.sent
    assert transport.link.proc.returncode == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc's (Linux)")
def test_the_engine_starts_with_its_large_blocks_mapped_as_the_app_has_them(engine, monkeypatch):
    """Started by the app, the engine is told (GLIBC_TUNABLES) to take its blocks of a megabyte
    and more from the system, as the app's own process does (light.fewer_heaps)."""
    monkeypatch.delenv("GLIBC_TUNABLES", raising=False)
    transport, sock = engine_on(engine, FakePanel())
    try:
        with open(f"/proc/{transport.link.proc.pid}/environ", "rb") as f:
            told = f.read().split(b"\0")
    finally:
        transport.close()
        sock.close()
    assert f"GLIBC_TUNABLES=glibc.malloc.mmap_threshold={light.MAPPED_FROM}".encode() in told


def test_a_query_answered_late_is_skipped_by_the_engine_as_by_python(engine):
    fake = FakePanel()
    transport, sock = engine_on(engine, fake)
    try:
        transport.sync()
        session = VideoSession(transport, (480, 1920), 60, 40, "clip")
        session.start()
        fake.late_status = 1  # the next 122 is answered only after the next command
        assert transport.query(122) is None
        assert transport.command(14, bytes([10]))[0] == 14  # (its late answer passed over)
        assert transport.query(122)[0] == 122
    finally:
        transport.close()
        sock.close()


@pytest.fixture
def wired(engine, monkeypatch):
    """The simulated panel for a display whose engine has it; pyusb never opens it."""
    fake = FakePanel()
    sock = PanelSocket(fake)
    fake.engine_args = []

    def open_by_engine(path, pid=None, beside=(), skip=0, popen=subprocess.Popen):
        if not fake.present:
            raise DeviceError("no Turing/TURZX USB panel found")
        command = [path, "panel", "--pid", f"{fake.pid:04x}", "--link", sock.link]
        return EngineTransport(EngineLink(command + fake.engine_args), fake.pid)

    def no_pyusb(*args, **kwargs):
        raise AssertionError("pyusb opened the panel the engine is to have")

    monkeypatch.setattr(EngineTransport, "open", staticmethod(open_by_engine))
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_pyusb))
    monkeypatch.setattr(turzx_usb.UsbTransport, "present", staticmethod(lambda pid: fake.present))
    monkeypatch.setattr(turzx_video, "RESTART", turzx_video._Restart())
    monkeypatch.setattr(turzx_video, "RATES", {})
    fake.socket = sock
    yield fake
    sock.close()


def planned(frames, fps=50):
    """``frames`` with the times the main loop drew them for, from now on."""
    start = time.perf_counter()
    for n, frame in enumerate(frames):
        frame.info["planned"] = start + n / fps
    return frames


def shown_in_time(display, frames, fps=50):
    for frame in frames:
        wait = frame.info["planned"] - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        display.show(frame)


@needs_ffmpeg
def test_the_engine_has_the_video_panel_and_sends_it_the_pictures(wired):
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    assert display.link is not None and display._sender is None  # (no sender of Python's)
    frames = planned(moving_square(40))
    shown_in_time(display, frames)
    assert isinstance(display.encoder, EngineStream)
    assert display.counters.encoder == "native, boxes"
    display.set_brightness(70)  # (on the way, between the pictures)
    settle(wired, timeout=15, at_least=30)
    display.close()
    assert wired.socket.errors == []
    assert wired.commands[:8] == [10, 110, 111, 112, 14, 102, 15, 17]
    assert wired.commands[-3:] == [123, 15, 102]  # the stop, and the last frame as a PNG
    assert wired.brightness[-1] == brightness_arg(70)
    assert not NEVER & set(wired.commands)
    assert display.counters.blocks >= 30 and display.counters.bytes > 0
    for source, shown in zip(frames, decode(wired.stream()), strict=False):
        native = source.transpose(Image.Transpose.ROTATE_270)
        assert max(ImageStat.Stat(ImageChops.difference(native, shown)).mean) < 6


@needs_ffmpeg
def test_pictures_reach_the_panel_on_their_time_however_long_a_frame_took(engine):
    """Frames drawn in 0 to 15 ms (as a busy theme's are) reach the engine unevenly; the engine
    sends each a fixed lead after the time it was drawn for: evenly."""
    import random

    fake = FakePanel()
    wire = Wire(fake)
    transport, sock = engine_on(engine, fake)
    rng = random.Random(3)
    handed = []
    try:
        transport.sync()
        session = VideoSession(transport, (480, 1920), 60, 40, "clip")
        session.start()
        settings = h264.EncoderSettings(fps=50, encoder="native", pixels="boxes", profile="high")
        options = h264.engine_options((1920, 480), 270, settings)
        stream = EngineStream(transport.link, (1920, 480), options, session.block_size)
        frames = moving_square(100)
        start = time.perf_counter() + 0.05
        for n, frame in enumerate(frames):
            due = start + n / 50
            while time.perf_counter() < due:
                time.sleep(0.0005)
            time.sleep(rng.uniform(0, 0.015))  # the drawing
            handed.append(time.perf_counter())
            stream.write(h264.engine_frame(frame, frame.info["damage"], n, due), timeout=2)
        arrived(fake, len(frames))
        stream.close()
        session.stop(None)
    finally:
        transport.close()
        sock.close()

    def spread(times):  # (between the quartiles: a hiccup of the test machine does not count)
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        q = statistics.quantiles(gaps, n=4)
        return (q[2] - q[0]) * 1000, statistics.median(gaps) * 1000

    came, _ = spread(handed)
    went, gap = spread(wire.blocks_at[2:])  # (from the third: the engine has its clock then)
    assert came > 4  # (the frames came unevenly)
    assert went < 2.5 and went < came / 3, (went, came)
    assert 18 < gap < 22
    # (a machine busy with more than this hands some frames over later than the lead: those go
    # at once, late; the pace above is the engine's)
    assert transport.link.counts["late"] < len(frames) // 2


def test_the_log_says_why_the_engines_pictures_went_late():
    """At the end of a stream: how many went late, and of those how many were made after their
    time (the frame or its encoding, not the sender), and the latest; an engine of before
    counts no causes."""
    from libre_panel.devices.turzx_video import lateness_said

    on_time = {"pictures": 9286, "late": 0, "came_late": 0, "late_most_ms": 0.0}
    assert lateness_said(on_time) == "video: the engine sent 9286 pictures, 0 of them late"
    late = {"pictures": 3080, "late": 1808, "came_late": 12, "late_most_ms": 12.6}
    assert lateness_said(late) == (
        "video: the engine sent 3080 pictures, 1808 of them late (12 made after their time; "
        "the latest 13 ms past it)"
    )
    assert lateness_said({"pictures": 3080, "late": 1808}) == (
        "video: the engine sent 3080 pictures, 1808 of them late"
    )


@needs_ffmpeg
def test_the_engine_says_which_late_pictures_came_late_and_how_late(engine):
    """A picture past its time is late (counts' "late"); one made after its time came so
    (``came_late``: the frame or its encoding was late, not the sender), and the latest is
    told in milliseconds (``late_most_ms``): the log says why pictures went late."""
    fake = FakePanel()
    transport, sock = engine_on(engine, fake)
    try:
        transport.sync()
        session = VideoSession(transport, (480, 1920), 60, 40, "clip")
        session.start()
        settings = h264.EncoderSettings(fps=50, encoder="native", pixels="boxes", profile="high")
        options = h264.engine_options((1920, 480), 270, settings)
        stream = EngineStream(transport.link, (1920, 480), options, session.block_size)
        frames = moving_square(30)
        start = time.perf_counter() + 0.05
        for n, frame in enumerate(frames):
            due = start + n / 50
            while time.perf_counter() < due:
                time.sleep(0.0005)
            if n >= 20:  # the last ten drawn a fifth of a second late: past the lead
                time.sleep(0.2)
            stream.write(h264.engine_frame(frame, frame.info["damage"], n, due), timeout=2)
        arrived(fake, len(frames))
        stream.close()
        session.stop(None)
    finally:
        transport.close()
        sock.close()
    counts = transport.link.counts
    assert counts["late"] >= 10 and counts["came_late"] >= 10
    assert counts["came_late"] <= counts["late"]
    assert counts["late_most_ms"] >= 150


@needs_ffmpeg
def test_a_hung_decoder_found_by_the_engine_restarts_the_panel(wired, monkeypatch):
    """The engine finds it as VideoSession.check_hang does (slow blocks, a queue stuck high);
    Libre Panel restarts the panel as SPUR II does (11), through the engine still."""
    hang_quickly(monkeypatch)
    wired.engine_args = ["--hang-blocks", "4"]
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    wired.block_delay = 0.25  # (a hung decoder takes each block slowly)
    wired.hung_depths = itertools.repeat(24)
    with pytest.raises(PanelRestarting):
        for frame in planned(moving_square(200), fps=25):
            display.show(frame)
            time.sleep(0.04)
    display.close()
    assert wired.commands.count(11) == 1 and wired.restarts == 1
    assert 123 not in wired.commands  # nothing more is sent to a hung decoder
    assert display.counters.panel_restarts == 1


@needs_ffmpeg
def test_a_failing_link_reconnects_through_a_new_engine(wired):
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    first = display.link
    shown_in_time(display, planned(moving_square(10)))
    settle(wired, timeout=10, at_least=5)
    wired.fail_next_writes = 1  # one write the panel does not take: the engine says so
    deadline = time.monotonic() + 15
    while display.link is first and time.monotonic() < deadline:
        for frame in planned(moving_square(5)):
            display.show(frame)
            time.sleep(0.02)
    assert display.link is not first and display.link.alive  # a new engine, the panel opened
    assert display.counters.usb_errors == 1
    assert wired.commands.count(110) == 2  # a full start
    assert not first.alive
    display.close()


@needs_ffmpeg
def test_an_engine_that_ends_unasked_is_a_link_that_failed(wired):
    """A crash of the engine's: the next frame reconnects (a new engine), as after a USB error."""
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    first = display.link
    shown_in_time(display, planned(moving_square(10)))
    first.proc.kill()
    deadline = time.monotonic() + 15
    while display.link is first and time.monotonic() < deadline:
        for frame in planned(moving_square(5)):
            display.show(frame)
            time.sleep(0.02)
    assert display.link is not first and display.link.alive
    assert display.counters.usb_errors == 1
    assert wired.commands.count(110) == 2
    display.close()


@needs_ffmpeg
def test_the_engine_with_the_panel_says_which_encoder_runs_as_a_note(engine, caplog):
    """With video.engine_encoder "card" the engine that has the panel says which encoder runs
    (here x264: no graphics card's encoder, or not Windows) as a note: logged, and no error
    the link keeps as the reason it stopped; the pictures come as from x264 alone."""
    fake = FakePanel()
    transport, sock = engine_on(engine, fake)
    link = transport.link
    with caplog.at_level("INFO", logger="libre_panel.devices.engine"):
        try:
            transport.sync()
            session = VideoSession(transport, (480, 1920), 60, 40, "clip")
            session.start()
            settings = h264.EncoderSettings(fps=50, encoder="native", pixels="boxes",
                                            profile="high", engine_encoder="card")  # fmt: skip
            options = h264.engine_options((1920, 480), 270, settings)
            assert options[options.index("--encoder") + 1] == "card"
            stream = EngineStream(link, (1920, 480), options, session.block_size)
            frames = moving_square(10)
            for n, frame in enumerate(frames):
                stream.write(h264.engine_frame(frame, frame.info["damage"], n), timeout=2)
            arrived(fake, len(frames))
            end = time.monotonic() + 5
            while not any(r.getMessage().startswith("engine: ") for r in caplog.records):
                assert time.monotonic() < end, "no note"
                time.sleep(0.01)
            stream.close()
            session.stop(None)
        finally:
            transport.close()
            sock.close()
    notes = [r for r in caplog.records if r.getMessage().startswith("engine: ")]
    assert {r.levelname for r in notes} == {"INFO"}
    said = notes[0].getMessage()
    assert "x264" in said or "(the graphics card's)" in said, said
    assert list(link.errors) == []


def test_the_log_says_where_the_engines_time_for_a_frame_goes():
    """The engine counts what each frame took it: composing it from its layers, its YUV and
    x264 (an average a frame), and the log says so as a run ends; an older engine, without
    them, gets no such line."""
    from libre_panel.devices.turzx_video import encoding_said

    counted = {"frames": 868, "encode_ms": 7.401, "compose_ms": 1.715, "yuv_ms": 1.072,
               "x264_ms": 4.613}  # fmt: skip
    assert encoding_said(counted) == (
        "video: the engine took 7.4 ms a frame: 1.7 composing it, 1.1 its YUV, 4.6 x264"
    )
    assert encoding_said({"frames": 868, "encode_ms": 7.4}) is None
    assert encoding_said({**counted, "frames": 0}) is None
    # the graphics card's encoder in the engine (video.engine_encoder "card") is named so
    assert encoding_said({**counted, "card": 1}) == (
        "video: the engine took 7.4 ms a frame: 1.7 composing it, 1.1 its YUV, "
        "4.6 the graphics card's encoder"
    )
    assert encoding_said({**counted, "card": 0}) == encoding_said(counted)


def test_an_engine_that_cannot_open_the_panel_leaves_it_to_pyusb(
    panel,  # noqa: F811
    monkeypatch,
    caplog,
    engine,
):
    def busy(*args, **kwargs):
        raise EngineFailed("cannot open the panel: device or resource busy")

    monkeypatch.setattr(EngineTransport, "open", staticmethod(busy))
    monkeypatch.setattr(turzx_video, "ENGINE_SAID", set())
    with caplog.at_level("WARNING"):
        for _ in range(2):
            display = TurzxVideoDisplay(video_config(encoder="native"))
            display.open()
            assert display.link is None and isinstance(display.transport, turzx_usb.UsbTransport)
            display.close()
    said = [r for r in caplog.records if "could not open the panel" in r.getMessage()]
    assert len(said) == 1  # (once for the same reason)
    assert panel.commands[:2] == [10, 110]


def test_the_loop_catches_up_within_the_engine_s_lead_instead_of_skipping_a_beat():
    """A frame that took 30 ms at 50 a second: without slack the loop takes a new beat (the
    animation jumps); with the engine's lead (40 ms) it draws the next frames for their own
    times, quickly, and is on its beat again. Beyond the lead it takes a new beat, as before."""
    from test_video import Clock2

    from libre_panel import app

    for slack, reanchored in ((None, 1), (h264.engine_lead(50), 0)):
        clock = Clock2()
        pacer = app._Pacer(clock=clock, sleep=clock.sleep)
        stop = threading.Event()
        planned = [pacer.wait(50, stop, slack)]
        clock.now += 0.050  # (a frame drawn in 50 ms: 30 more than its own)
        for _ in range(4):
            planned.append(pacer.wait(50, stop, slack))
            clock.now += 0.004
        assert pacer.reanchored == reanchored
        if not reanchored:  # each frame for its own beat
            steps = [round(b - a, 9) for a, b in zip(planned, planned[1:], strict=False)]
            assert steps == [0.02] * 4
    clock.now += 0.2  # far beyond the lead: a new beat
    assert pacer.wait(50, stop, h264.engine_lead(50)) == clock.now and pacer.reanchored == 1


@needs_ffmpeg
def test_the_display_s_slack_is_the_engine_s_lead_while_it_sends(wired):
    display = TurzxVideoDisplay(video_config(encoder="native", fps=25))
    display.open()
    shown_in_time(display, planned(moving_square(3), fps=25))
    assert display.slack == pytest.approx(0.08) == display.encoder.lead
    display.close()
    assert display.slack is None


def test_the_engine_says_where_it_is_and_ends_when_its_input_does(engine):
    fake = FakePanel()
    transport, sock = engine_on(engine, fake)
    try:
        assert transport.link.place.startswith("test link 127.0.0.1:")
        assert transport.drain() == 0
    finally:
        transport.close()
        sock.close()
    assert transport.link.proc.returncode == 0 and not transport.link.alive


def test_records_for_the_engine_are_its_own():
    """The command record as engine/src/wire.rs reads it."""
    from libre_panel.devices import engine as engine_module

    head = engine_module._head(4, 7, 1 | 121 << 8 | 1 << 16)
    assert head[:5] == b"LPE1\x04" and struct.unpack("<II", head[8:]) == (7, 1 | 121 << 8 | 65536)
    assert engine_module.OPS == {"command": 1, "ask": 2, "query": 3, "drain": 4, "push": 5}

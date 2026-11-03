"""Libre Panel's own engine (engine/, h264.NATIVE): x264 in a program of Libre Panel's, fed the
boxes of each frame that may have changed. Its command and its records; and, where it is built,
the YUV it makes (byte for byte yuv.py's), a video panel through it, and a new bit rate in the
running stream (no second encoder)."""

import random
import struct
import subprocess

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat
from test_video import decode, needs_ffmpeg, panel, settle, video_config  # noqa: F401
from test_yuv import moving_square, stream

from libre_panel.devices import h264
from libre_panel.devices.turzx_video import TurzxVideoDisplay
from libre_panel.devices.yuv import YuvFrame


@pytest.fixture
def engine():
    """The engine's program, found as the app finds it (with the rights conftest stands in:
    an ordinary user's), else the test is skipped."""
    found = h264.find_engine()
    if not found:
        pytest.skip("needs libre-panel-engine (cargo build --release in engine/)")
    return found


class Pipe:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data += data
        return len(data)


def test_the_engine_encodes_drawn_frames_as_x264_through_ffmpeg_would():
    settings = h264.EncoderSettings(fps=50, crf=18, preset="superfast", keyframe_s=2.0,
                                    profile="high", encoder=h264.NATIVE)  # fmt: skip
    command = h264.engine_command("engine", (1920, 720), 270, settings)
    assert command[:2] == ["engine", "encode"]
    text = " ".join(command)
    assert "--size 1920x720 --turn 270 --fps 50 --crf 18" in text
    assert "--kbit 3000 --keyint 100 --preset superfast --profile high" in text  # (3.0M: auto)
    assert f"--x264 {h264.DRAWN_X264}" in text and "--full" not in command
    settings = h264.EncoderSettings(fps=50, encoder=h264.NATIVE, full=True)
    full = h264.engine_command("engine", (1920, 480), 270, settings)
    assert full[-1] == "--full" and "--kbit 2000" in " ".join(full)
    assert "native" in h264.ENCODERS


def test_a_frame_goes_to_the_engine_as_its_changed_boxes_grown_to_blocks():
    frame = Image.new("RGB", (64, 48), (1, 2, 3))
    ImageDraw.Draw(frame).rectangle([18, 5, 20, 9], fill=(200, 100, 50))
    pipe = Pipe()
    h264.engine_frame(frame, [(18, 5, 21, 10), (70, 0, 80, 4)], 7)(pipe)
    data = bytes(pipe.data)
    assert data[:5] == b"LPE1\x01" and struct.unpack("<II", data[8:16]) == (7, 1)
    assert struct.unpack("<4H", data[16:24]) == (16, 0, 32, 16)  # (the one in the frame, grown)
    assert data[24:] == frame.crop((16, 0, 32, 16)).tobytes()
    whole = Pipe()
    h264.engine_frame(frame, None, 0)(whole)
    assert struct.unpack("<4H", bytes(whole.data)[16:24]) == (0, 0, 64, 48)
    assert h264.engine_rate(2500)[:5] == b"LPE1\x02"
    assert struct.unpack("<I", h264.engine_rate(2500)[8:12]) == (2500,)


def test_the_engine_s_tables_are_pillow_s_conversion():
    """engine/src/tables.rs (tools/engine_tables.py): Pillow's RGB to YCbCr, as yuv.py converts,
    on a million colours, and yuv.py's tables of the limited range."""
    import re
    import sys
    from pathlib import Path

    import numpy as np

    from libre_panel.devices.yuv import LIMITED_C, LIMITED_Y

    root = Path(__file__).resolve().parent.parent
    text = (root / "engine" / "src" / "tables.rs").read_text()
    found = {
        name: np.array([int(x) for x in values.split(",") if x.strip()], np.int64)
        for name, values in re.findall(r"pub const (\w+): \[\w+; 256\] = \[([^\]]*)\];", text)
    }
    found["CR_R"] = found["CB_B"]
    sys.path.insert(0, str(root / "tools"))
    import engine_tables

    tables = {name: found[name.upper()] for name in (*engine_tables.NAMES, "Cr_R")}
    rgb = np.random.default_rng(5).integers(0, 256, (1 << 20, 3), np.uint8)
    pillow = np.asarray(Image.fromarray(rgb.reshape(1024, 1024, 3), "RGB").convert("YCbCr"))
    assert np.array_equal(engine_tables.converted(tables, rgb), pillow.reshape(-1, 3))
    assert list(found["LIMITED_Y"]) == LIMITED_Y and list(found["LIMITED_C"]) == LIMITED_C


def frames(size, count, seed):
    rng = random.Random(seed)
    image = Image.new("RGB", size, (10, 14, 19))
    draw = ImageDraw.Draw(image)
    for _ in range(30):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.rectangle([x, y, x + rng.randrange(2, 90), y + rng.randrange(2, 60)],
                       fill=tuple(rng.randrange(256) for _ in range(3)))  # fmt: skip
    yield image.copy(), None
    for _ in range(count):
        boxes = []
        for _ in range(rng.randrange(0, 4)):
            x0, y0 = rng.randrange(size[0] - 1), rng.randrange(size[1] - 1)
            x1, y1 = (
                min(size[0], x0 + rng.randrange(1, 90)),
                min(size[1], y0 + rng.randrange(1, 60)),
            )
            if rng.random() < 0.8:  # (else a box said to change that did not)
                ImageDraw.Draw(image).rectangle(
                    [x0, y0, x1 - 1, y1 - 1], fill=tuple(rng.randrange(256) for _ in range(3))
                )
            boxes.append((x0, y0, x1, y1))
        yield image.copy(), boxes


@pytest.mark.parametrize(("size", "rotate", "full"), [
    ((1920, 720), 270, False), ((1920, 480), 270, True), ((480, 1920), 180, False),
    ((1024, 600), 270, False), ((320, 240), None, False),
])  # fmt: skip
def test_the_engine_makes_the_yuv_yuv_py_makes(engine, tmp_path, size, rotate, full):
    """Byte for byte, frame by frame: Pillow's YCbCr, the colours' averages, the panel's range,
    turned; blocks of 16 and of 8."""
    dump = tmp_path / "yuv"
    settings = h264.EncoderSettings(fps=25, encoder=h264.NATIVE, full=full)
    command = h264.engine_command(engine, size, rotate, settings) + ["--dump-yuv", str(dump)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    made, expected = YuvFrame(size, rotate, full), []
    for n, (frame, boxes) in enumerate(frames(size, 12, sum(size))):
        made.update(frame, boxes)
        expected.append(made.tobytes())
        h264.engine_frame(frame, boxes, n)(process.stdin)
    process.stdin.close()
    assert process.wait(timeout=60) == 0
    data = dump.read_bytes()
    each = len(expected[0])
    assert len(data) == each * len(expected)
    for n, want in enumerate(expected):
        assert data[n * each : (n + 1) * each] == want, n


@needs_ffmpeg
def test_a_video_panel_is_fed_through_the_engine(engine, panel):  # noqa: F811
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    frames = moving_square(40)
    stream(display, frames)
    assert display.encoder.name == h264.NATIVE and display.encoder.command[0] == engine
    assert display.counters.encoder == "native, boxes"
    settle(panel, timeout=15, at_least=30)
    display.close()
    for source, shown in zip(frames, decode(panel.stream()), strict=False):
        native = source.transpose(Image.Transpose.ROTATE_270)
        assert max(ImageStat.Stat(ImageChops.difference(native, shown)).mean) < 6


@needs_ffmpeg
def test_a_new_bit_rate_goes_into_the_engine_s_running_stream(engine, panel):  # noqa: F811
    """No second encoder beside the first, no handover, no keyframe to wait for: the same
    engine goes on with the new cap."""
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    frames = moving_square(60)
    stream(display, frames[:20])
    running = display.encoder
    before = display.rate.rate
    display.rate.rate = next(step for step in h264.RATE_STEPS if step > before)
    display._follow_the_panel()
    assert display.encoder is running and display._leaving is None  # (no switch)
    assert running.maxrate == h264.rate_text(display.rate.rate)
    assert display.counters.bit_rate == display.rate.rate
    stream(display, frames[20:])
    settle(panel, timeout=15, at_least=50)
    assert running.alive and display.counters.ffmpeg_restarts == 0
    display.close()
    shown = decode(panel.stream())
    assert len(shown) >= 50


@needs_ffmpeg
def test_without_the_engine_x264_through_ffmpeg_encodes(panel, monkeypatch, caplog):  # noqa: F811
    from libre_panel.devices import turzx_video

    monkeypatch.setattr(turzx_video, "find_engine", lambda: "")
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    with caplog.at_level("WARNING"):
        stream(display, moving_square(10))
    assert display.encoder.name == "libx264"
    assert display.counters.encoder == "libx264, yuv420p"
    said = [r for r in caplog.records if "Libre Panel's engine" in r.getMessage()]
    assert len(said) == 1  # (once)
    display.close()

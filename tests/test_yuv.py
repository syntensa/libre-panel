"""The video panel's YUV made in Libre Panel where the frames change (devices/yuv.py): the same
as made whole, the colours swscale made, what the renderer and the main loop say changed, and
ffmpeg fed it (fed as before where much changes)."""

import random
import time

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat
from test_video import decode, needs_ffmpeg, panel, settle, video_config  # noqa: F401

from libre_panel.devices import h264, yuv
from libre_panel.devices.turzx_video import TurzxVideoDisplay


def picture(size, seed):
    rng = random.Random(seed)
    image = Image.new("RGB", size, (10, 14, 19))
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        colour = tuple(rng.randrange(256) for _ in range(3))
        draw.rectangle([x, y, x + rng.randrange(4, 30), y + rng.randrange(4, 30)], fill=colour)
    return image


def test_the_blocks_and_where_a_box_lies_once_turned():
    assert yuv.block_of((1920, 720)) == 16 and yuv.block_of((1024, 600)) == 8
    assert yuv.block_of((466, 466)) is None
    assert yuv.turned_size((1920, 720), 270) == (720, 1920)
    assert yuv.turned_box((0, 0, 16, 16), (1920, 720), 270) == (704, 0, 720, 16)
    assert yuv.turned_box((0, 0, 16, 16), (1920, 720), 180) == (1904, 704, 1920, 720)


@pytest.mark.parametrize(("full", "white", "black", "red"),
                         [(False, (235, 128, 128), (16, 128, 128), (81, 90, 240)),
                          (True, (255, 128, 128), (0, 128, 128), (76, 85, 255))])  # fmt: skip
def test_bt601_in_the_panels_range(full, white, black, red):
    for colour, expected in (((255, 255, 255), white), ((0, 0, 0), black), ((255, 0, 0), red)):
        made = yuv.YuvFrame((32, 16), None, full)
        made.update(Image.new("RGB", (32, 16), colour), None)
        got = (int(made.y[0, 0]), int(made.u[0, 0]), int(made.v[0, 0]))
        assert all(abs(a - b) <= 1 for a, b in zip(got, expected, strict=True)), (colour, got)


@pytest.mark.parametrize("rotate", [None, 180, 270])
@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("size", [(96, 64), (40, 24)])  # (blocks of 16; of 8)
def test_made_where_it_changed_it_is_what_made_whole_would_be(rotate, full, size):
    rng = random.Random(7)
    made = yuv.YuvFrame(size, rotate, full)
    frame = picture(size, 0)
    assert made.update(frame, None) == 1.0
    for step in range(12):
        changed = frame.copy()
        x, y = rng.randrange(size[0] - 6), rng.randrange(size[1] - 6)
        box = (x, y, x + rng.randrange(1, 6), y + rng.randrange(1, 6))
        ImageDraw.Draw(changed).rectangle([box[0], box[1], box[2] - 1, box[3] - 1],
                                          fill=(step * 20, 255 - step * 9, 77))  # fmt: skip
        share = made.update(changed, [box, (0, 0, 2, 2)])  # (a box where nothing changed)
        assert 0 < share < 1
        frame = changed
        whole = yuv.YuvFrame(size, rotate, full)
        whole.update(frame, None)
        assert made.tobytes() == whole.tobytes(), step
    assert made.update(frame, [(0, 0, *size)]) == 0.0  # (the same frame again: nothing made)
    assert made.update(frame.copy(), []) == 0.0  # (nothing changed, it says)


def test_the_colours_are_swscales():
    """Against ffmpeg's own conversion (swscale, BT.601, limited): the light within one level,
    the colours far within what an encoder changes."""
    pytest.importorskip("numpy")
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg")
    frame = picture((64, 32), 3)
    made = yuv.YuvFrame(frame.size, None, False)
    made.update(frame, None)
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x32", "-i", "-",
         "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
        input=frame.tobytes(), capture_output=True, check=True,
    ).stdout  # fmt: skip
    theirs = np.frombuffer(out, np.uint8)
    ours = np.frombuffer(made.tobytes(), np.uint8)
    light = np.abs(ours[: 64 * 32].astype(int) - theirs[: 64 * 32].astype(int))
    assert light.max() <= 1
    colours = np.abs(ours[64 * 32 :].astype(int) - theirs[64 * 32 :].astype(int))
    assert colours.mean() < 1.5


def test_it_goes_into_a_pipe_that_takes_a_little_at_a_time():
    class Pipe:
        def __init__(self):
            self.got = bytearray()

        def write(self, data):
            piece = bytes(data[:1000])
            self.got += piece
            return len(piece)

    made = yuv.YuvFrame((64, 32), 270, False)
    made.update(picture((64, 32), 1), None)
    pipe = Pipe()
    made.write(pipe)
    assert bytes(pipe.got) == made.tobytes() and len(pipe.got) == made.bytes_per_frame


def test_ffmpeg_fed_yuv_neither_turns_nor_converts():
    settings = h264.EncoderSettings(fps=30, pixels=h264.YUV, full=True)
    command = h264.ffmpeg_command("ffmpeg", (1920, 720), 270, settings)
    given = command[: command.index("-i")]
    assert (
        given[given.index("-s") + 1] == "720x1920"
        and given[given.index("-pix_fmt") + 1] == "yuv420p"
    )
    assert given[given.index("-color_range") + 1] == "pc"  # (made in full range: said so)
    assert "-vf" not in command
    limited = h264.ffmpeg_command("ffmpeg", (1920, 480), 270, h264.EncoderSettings(pixels=h264.YUV))
    assert limited[limited.index("-color_range") + 1] == "tv" and "-vf" not in limited


def test_what_the_renderer_composed_anew_holds_every_pixel_that_changed():
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    renderer = Renderer(load_theme(find_theme("lumen")))
    renderer.continuous = True
    before, _ = renderer.render(demo_snapshot(fixed_time=1_700_000_000), 1000.0)
    assert renderer.damage is None  # (the first frame: all of it)
    checked = 0
    for n in range(1, 25):
        frame, _ = renderer.render(demo_snapshot(fixed_time=1_700_000_000 + n / 10), 1000 + n / 10)
        damage = renderer.damage
        if damage is not None:
            difference = ImageChops.difference(before.convert("RGB"), frame.convert("RGB"))
            changed = np.asarray(difference.point(lambda v: 255 if v else 0).convert("L")) > 0
            inside = np.zeros_like(changed)
            for x0, y0, x1, y1 in damage:
                inside[y0:y1, x0:x1] = True
            assert not (changed & ~inside).any(), n
            checked += 1
        before = frame
    assert checked > 10


def test_the_main_loop_says_what_changed_only_where_it_knows():
    from libre_panel.app import MainLoop

    class Drawn:
        rendered, damage = 1, [(0, 0, 10, 10)]

    loop = MainLoop.__new__(MainLoop)
    loop._fed, loop._fed_toast = None, None
    drawn = Drawn()
    assert loop._damage(drawn, True, None, 0, (100, 50)) is None  # (the first: all of it)
    drawn.rendered = 2
    assert loop._damage(drawn, True, (50, 5, 60, 15), 0, (100, 50)) == [
        (0, 0, 10, 10),
        (50, 5, 60, 15),
    ]
    drawn.rendered = 3  # the notice went: where it was is made anew
    assert loop._damage(drawn, True, None, 180, (100, 50)) == [(90, 40, 100, 50), (40, 35, 50, 45)]
    drawn.rendered = 5  # a frame the panel did not get: not known
    assert loop._damage(drawn, True, None, 0, (100, 50)) is None
    drawn.rendered = 6
    assert loop._damage(drawn, False, None, 0, (100, 50)) is None  # (drawn over: a transition)
    drawn.rendered = 7
    assert loop._damage(drawn, True, None, 0, (100, 50)) is None  # (the one before was not known)


def moving_square(count, size=(1920, 480), step=8):
    """Frames with a square moving over a still picture, each saying where it changed."""
    base = picture(size, 9)
    frames, last = [], None
    for n in range(count):
        frame = base.copy()
        box = (100 + n * step, 200, 100 + n * step + 40, 240)
        ImageDraw.Draw(frame).rectangle([box[0], box[1], box[2] - 1, box[3] - 1],
                                        fill=(240, 80 + n % 100, 40))  # fmt: skip
        frame.info["damage"] = None if last is None else [last, box]
        frames.append(frame)
        last = box
    return frames


def stream(display, frames, fps=30):
    for frame in frames:
        display.show(frame)
        time.sleep(1 / fps)


@needs_ffmpeg
def test_a_video_panel_is_fed_yuv_made_where_the_frames_change(panel):  # noqa: F811
    display = TurzxVideoDisplay(video_config())
    display.open()
    frames = moving_square(40)
    stream(display, frames)
    assert display.encoder.settings.pixels == h264.YUV
    assert display.counters.encoder == "libx264, yuv420p"
    settle(panel, timeout=15, at_least=30)
    display.close()
    for source, shown in zip(frames, decode(panel.stream()), strict=False):
        native = source.transpose(Image.Transpose.ROTATE_270)
        assert max(ImageStat.Stat(ImageChops.difference(native, shown)).mean) < 6


@needs_ffmpeg
def test_where_much_changes_ffmpeg_converts_again_and_yuv_comes_back_when_calm(
    panel,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(TurzxVideoDisplay, "MADE_CALM_S", 0.5)
    display = TurzxVideoDisplay(video_config())
    display.open()
    busy = moving_square(30)
    for frame in busy[1:]:
        frame.info["damage"] = None  # (all of it changed, as in a video)
    stream(display, busy)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and display.encoder.settings.pixels != "rgb0":
        stream(display, busy[:3])
    assert display.encoder.settings.pixels == "rgb0"
    calm = moving_square(60)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and display.encoder.settings.pixels != h264.YUV:
        stream(display, calm[1:])
    assert display.encoder.settings.pixels == h264.YUV
    assert display.counters.ffmpeg_restarts == 0  # (each a new ffmpeg beside the one running)
    display.close()


def test_a_graph_whose_clock_went_back_is_drawn_not_miles_wide():
    """A clock that went back (a frame planned at an earlier time than the graph's newest
    point: another clock in a test, a warm-up's) drew the curve from a time long past: a
    picture hundreds of millions of pixels wide, refused or taking gigabytes."""
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    renderer = Renderer(load_theme(find_theme("lumen")))
    renderer.continuous = True
    renderer.render(demo_snapshot(fixed_time=1_700_000_000), 50_000.0)
    renderer.render(demo_snapshot(fixed_time=1_700_000_000), 1_000.0)
    assert not [w for w in renderer.warnings if "graph" in w], renderer.warnings

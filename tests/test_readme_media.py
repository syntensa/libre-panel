"""tools/readme_media.py: the clips for the README, the website and forum posts.

The encoders and, further down, the panel and app recipes, with tiny pictures and short clips so
that they stay fast. What a clip looks like is judged by eye when it is made; here are the
things that must hold on every run: the same frames twice, the files that come out, the
fallbacks and the size check.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

TOOLS = Path(__file__).resolve().parent.parent / "tools"
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def encode():
    """tools/media_encode.py, imported by name as the tools do among themselves."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import media_encode

        yield media_encode


def picture(n: int, size=(96, 64)) -> Image.Image:
    """A frame of a made-up clip: a gradient, and a bright block that moves right."""
    w, h = size
    ramp = np.linspace(10, 60, w, dtype=np.uint8)
    pixels = np.zeros((h, w, 3), np.uint8)
    pixels[:, :] = ramp[None, :, None]
    x = 4 + n * 5
    pixels[h // 4 : h // 2, x : x + 14] = (19, 229, 215)
    return Image.fromarray(pixels)


@pytest.fixture
def frames(encode, tmp_path):
    sink = encode.FrameSink(tmp_path / "frames", fps=20)
    for n in range(16):
        sink.add(picture(n))
    return sink.frames()


def test_frames_are_kept_as_numbered_pngs(encode, frames):
    assert frames.count == 16 and frames.fps == 20 and frames.size == (96, 64)
    assert frames.path(3).name == "00003.png" and frames.pattern.endswith("%05d.png")
    assert frames.image(5).tobytes() == picture(5).tobytes()  # nothing lost on the way


def test_an_empty_clip_is_an_error(encode, tmp_path):
    with pytest.raises(encode.EncodeError):
        encode.FrameSink(tmp_path / "none", fps=25).frames()


@needs_ffmpeg
def test_one_clip_becomes_a_gif_a_video_a_webm_and_a_poster(encode, frames, tmp_path):
    settings = encode.Settings(gif_width=48, gif_fps=10)
    gif = encode.write_gif(frames, tmp_path / "out" / "clip.gif", settings)
    with Image.open(gif.path) as image:
        assert image.size == (48, 32)  # as wide as asked, in proportion
        assert image.n_frames == 8  # 16 frames at 20 a second, 10 of them a second
        assert image.info["loop"] == 0 and image.info["duration"] == 100  # forever, 10 a second
    assert gif.size == gif.path.stat().st_size and gif.within_target

    mp4 = encode.write_mp4(frames, tmp_path / "out" / "clip.mp4", settings)
    webm = encode.write_webm(frames, tmp_path / "out" / "clip.webm", settings)
    assert mp4.read_bytes()[4:8] == b"ftyp"  # (and the index comes first: faststart)
    assert mp4.read_bytes().find(b"moov") < mp4.read_bytes().find(b"mdat")
    assert webm.read_bytes()[:4] == bytes.fromhex("1a45dfa3")  # Matroska's header

    poster = encode.write_poster(frames, 7, tmp_path / "out" / "clip.png")
    with Image.open(poster) as image:
        assert image.size == (96, 64)
        assert image.tobytes() == picture(7).tobytes()


@needs_ffmpeg
def test_a_video_is_yuv420p_in_bt709_with_even_sides(encode, tmp_path):
    import subprocess

    sink = encode.FrameSink(tmp_path / "odd", fps=20)
    for n in range(6):
        sink.add(picture(n, (97, 65)))  # odd sizes: yuv420p needs even ones
    mp4 = encode.write_mp4(sink.frames(), tmp_path / "odd.mp4")
    shown = subprocess.run(
        [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt,width,height,color_space,codec_name", "-of",
         "default=nw=1", str(mp4)],
        capture_output=True, text=True,
    )  # fmt: skip
    if shown.returncode:
        pytest.skip("ffprobe is not installed")
    found = dict(line.split("=") for line in shown.stdout.split())
    assert found == {"codec_name": "h264", "width": "96", "height": "64", "pix_fmt": "yuv420p",
                     "color_space": "bt709"}  # fmt: skip


def test_without_ffmpeg_pillow_writes_the_gif(encode, frames, tmp_path, monkeypatch):
    monkeypatch.setattr(encode.shutil, "which", lambda name: None)
    assert encode.find_ffmpeg() is None
    settings = encode.Settings(gif_width=48, gif_fps=10)
    gif = encode.write_gif(frames, tmp_path / "pillow.gif", settings)
    with Image.open(gif.path) as image:
        assert image.size == (48, 32) and image.n_frames == 8
        assert image.info["loop"] == 0 and image.info["duration"] == 100
        image.seek(7)
        last = np.asarray(image.convert("RGB"), np.int16)
    reference = np.asarray(frames.image(14).resize((48, 32), Image.Resampling.LANCZOS), np.int16)
    assert np.abs(last - reference).mean() < 6  # the same picture, in at most 256 colours
    for what in (encode.write_mp4, encode.write_webm):  # video needs ffmpeg, and says so
        with pytest.raises(encode.EncodeError, match="needs ffmpeg"):
            what(frames, tmp_path / "clip.out", settings)


@needs_ffmpeg
def test_both_encoders_make_the_same_picture(encode, frames, tmp_path, monkeypatch):
    settings = encode.Settings(gif_width=96, gif_fps=20)
    with_ffmpeg = encode.write_gif(frames, tmp_path / "a.gif", settings)
    monkeypatch.setattr(encode.shutil, "which", lambda name: None)
    with_pillow = encode.write_gif(frames, tmp_path / "b.gif", settings)
    for n in (0, 9):
        shots = []
        for gif in (with_ffmpeg, with_pillow):
            with Image.open(gif.path) as image:
                image.seek(n)
                shots.append(np.asarray(image.convert("RGB"), np.int16))
        assert np.abs(shots[0] - shots[1]).mean() < 6


def test_a_gif_goes_down_the_ladder_until_it_is_small_enough(encode, tmp_path):
    rng = np.random.default_rng(3)  # noise: a GIF cannot make it small, only leaner
    sink = encode.FrameSink(tmp_path / "noise", fps=20)
    for _ in range(12):
        sink.add(Image.fromarray(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)))
    frames = sink.frames()
    ladder = (encode.GifLook(256, 3), encode.GifLook(64, 5, 10), encode.GifLook(16, 5, 5))
    best = encode.write_gif(frames, tmp_path / "a.gif", encode.Settings(target=1), ladder)
    sizes = [size for _look, size in best.tried]
    assert len(sizes) == 3 and sizes[0] > sizes[1] > sizes[2]  # leaner at every step
    assert best.look == ladder[2] and best.size == sizes[2] and not best.within_target
    assert best.fps == 5  # the last look takes fewer frames
    roomy = encode.write_gif(frames, tmp_path / "b.gif", encode.Settings(target=10**9), ladder)
    assert roomy.look == ladder[0] and len(roomy.tried) == 1 and roomy.within_target


def test_a_gif_over_the_limit_is_an_error_and_leaves_nothing(encode, frames, tmp_path):
    out = tmp_path / "out" / "big.gif"
    with pytest.raises(encode.GifTooLarge, match=r"big\.gif .* a forum takes"):
        encode.write_gif(frames, out, encode.Settings(target=100, limit=200))
    assert not out.exists()
    fine = encode.write_gif(frames, out, encode.Settings(target=100, limit=10**7))
    assert out.exists() and fine.size < 10**7 and not fine.within_target  # over the target only


def test_sizes_are_told_in_the_units_a_person_reads(encode):
    assert encode.human(3_500_000) == "3.34 MB" and encode.human(2048) == "2 kB"
    assert encode.GIF_TARGET == 4 * 2**20 and encode.GIF_LIMIT == 8 * 2**20

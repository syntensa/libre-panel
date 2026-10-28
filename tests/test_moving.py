"""Pictures that move in an image widget (render/moving.py): an animated GIF plays its frames
for as long as each says, a video plays through ffmpeg, both round and round (#33)."""

import io
import json
import shutil
import subprocess
import time
import zipfile

import pytest
from PIL import Image

from libre_panel.render import moving
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import THEME_FORMAT, parse_theme

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
COLOURS = ("red", "green", "blue")


def gif(path, durations=(100, 200, 300), size=(64, 64)):
    frames = [Image.new("RGB", size, colour) for colour in COLOURS[: len(durations)]]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=list(durations),
                   loop=0)  # fmt: skip
    return path


def video(path, size="64x48", seconds=2):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=30", "-t", str(seconds), "-pix_fmt", "yuv420p",
                    str(path)], check=True, timeout=60)  # fmt: skip
    return path


def theme(root, src, **fields):
    return parse_theme({
        "format": THEME_FORMAT, "display": {"width": 200, "height": 100},
        "widgets": [{"type": "image", "id": "p", "x": 0, "y": 0, "w": 80, "h": 60, "src": src,
                     **fields}],
    }, root=root)  # fmt: skip


def test_an_animated_picture_shows_each_frame_as_long_as_it_says(tmp_path):
    animation = moving.read_animation(gif(tmp_path / "a.gif"), lambda image: image)
    assert len(animation.frames) == 3 and animation.ends == pytest.approx([0.1, 0.3, 0.6])
    assert [animation.index(t) for t in (0.05, 0.15, 0.35, 0.65)] == [0, 1, 2, 0]  # (round)
    # a frame said to show for nothing shows for a tenth, as browsers do
    quick = moving.read_animation(gif(tmp_path / "q.gif", (0, 100)), lambda image: image)
    assert quick.ends == pytest.approx([0.1, 0.2])
    # a picture of one frame is a still
    Image.new("RGB", (8, 8), "red").save(tmp_path / "still.gif")
    assert moving.read_animation(tmp_path / "still.gif", lambda image: image) is None


def test_an_animated_picture_too_large_keeps_every_second_frame(tmp_path):
    path = gif(tmp_path / "a.gif", (100, 100, 100), size=(10, 10))
    kept = moving.read_animation(path, lambda image: image, most=2 * 10 * 10 * 4)
    # three frames in the room of two: every second kept, the last too; each as long as those
    # it stands for
    assert len(kept.frames) == 2 and kept.ends == pytest.approx([0.2, 0.3])


def test_the_image_widget_plays_an_animated_gif_in_time(tmp_path):
    gif(tmp_path / "a.gif")
    renderer = Renderer(theme(tmp_path, "a.gif", fit="cover", radius=6), animate=True)
    seen = []
    for at in (100.0, 100.15, 100.4, 100.65):  # (from its first frame: 0, 0.15, 0.4, 0.05)
        frame, _boxes = renderer.render(Snapshot(), at)
        seen.append(frame.getpixel((40, 30))[:3])
    assert seen[0] == seen[3] != seen[1] != seen[2]
    assert seen[0][0] > 200 and seen[2][2] > 200  # red, then green, then blue
    assert renderer.moving  # (a panel that is not sent every frame draws it again soon)
    assert sum(frame.getpixel((0, 0))[:3]) < 10  # (rounded: the corner stays the ground)
    renderer.close()


@needs_ffmpeg
def test_a_video_plays_round_and_round_and_a_preview_shows_its_first_frame(tmp_path):
    clip = video(tmp_path / "clip.mp4")
    first = moving.first_frame(FFMPEG, clip, 40, 30, "cover")
    assert first.size == (40, 30) and first.getpixel((20, 15))[3] == 255
    assert moving.first_frame(FFMPEG, tmp_path / "missing.mp4", 40, 30, "cover") is None
    feed = moving.VideoFeed(FFMPEG, clip, 40, 30, "contain")
    try:
        deadline, counts = time.monotonic() + 10, set()
        while time.monotonic() < deadline and len(counts) < 5:
            count, picture = feed.frame()
            if picture is not None:
                counts.add(count)
                assert picture.size == (40, 30)
            time.sleep(0.05)
        assert len(counts) >= 5  # (new frames keep coming, at the pace the video plays)
    finally:
        feed.close()
    preview = Renderer(theme(tmp_path, "clip.mp4"), preview=True)
    frame, _boxes = preview.render(Snapshot())
    assert frame.getpixel((40, 30))[:3] != (0, 0, 0)  # its first frame
    preview.close()
    panel = Renderer(theme(tmp_path, "clip.mp4"), animate=True)
    shown = set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(shown) < 3:
        frame, _boxes = panel.render(Snapshot(), time.monotonic())
        shown.add(frame.crop((0, 0, 80, 60)).tobytes())
        time.sleep(0.1)
    assert len(shown) >= 3 and panel.moving
    panel.close()
    assert panel._feeds == {}


def test_ffmpeg_fits_a_video_as_the_widget_says():
    cover = moving.video_command("ffmpeg", moving.Path("v.mp4"), 80, 60, "cover")
    assert "-re" in cover and cover[cover.index("-stream_loop") + 1] == "-1"
    assert "force_original_aspect_ratio=increase,crop=80:60" in cover[cover.index("-vf") + 1]
    contain = moving.video_command("ffmpeg", moving.Path("v.mp4"), 80, 60, "contain", once=True)
    assert "pad=80:60" in contain[contain.index("-vf") + 1] and "-re" not in contain
    assert contain[-3:] == ["-frames:v", "1", "pipe:1"]
    assert moving.is_video("assets/Clip.MP4") and not moving.is_video("a.gif")


def test_a_theme_carries_its_videos_when_shared(isolated_home):
    from libre_panel.config import user_themes_dir
    from libre_panel.theme.share import ShareError, import_theme

    def shared(name, content):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            data = {"format": "libre-panel-theme/1", "name": "V",
                    "display": {"model": "custom", "width": 100, "height": 100},
                    "widgets": [{"type": "image", "src": f"assets/{name}"}]}  # fmt: skip
            archive.writestr("t/theme.json", json.dumps(data))
            archive.writestr(f"t/assets/{name}", content)
        return buffer.getvalue()

    mp4 = b"\x00\x00\x00\x18ftypisom" + bytes(64)
    assert import_theme(shared("clip.mp4", mp4))["name"]
    assert import_theme(shared("clip.webm", b"\x1aE\xdf\xa3" + bytes(64)))["name"]
    with pytest.raises(ShareError, match="not the picture or font"):
        import_theme(shared("clip.mp4", b"<html>" + bytes(64)))
    assert any((user_themes_dir()).rglob("clip.mp4"))

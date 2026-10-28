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


def test_a_moving_picture_plays_faster_or_slower():
    assert moving.speed_of(2) == 2 and moving.speed_of("0.5") == 0.5
    assert moving.speed_of(100) == 4 and moving.speed_of(0) == 0.25  # (within reason)
    assert moving.speed_of(None) == 1 and moving.speed_of(float("nan")) == 1
    fast = moving.video_command("ffmpeg", moving.Path("v.mp4"), 80, 60, "cover", speed=2)
    assert "-re" not in fast and fast[fast.index("-readrate") + 1] == "2"
    assert fast[fast.index("-vf") + 1].startswith("setpts=PTS/2,scale=")
    slow = moving.video_command("ffmpeg", moving.Path("v.mp4"), 80, 60, "cover", speed=0.5)
    assert slow[slow.index("-readrate") + 1] == "0.5" and "setpts=PTS/0.5," in " ".join(slow)


def test_the_image_widget_plays_an_animated_gif_twice_as_fast(tmp_path):
    gif(tmp_path / "a.gif")  # (0.1, 0.2 and 0.3 seconds: twice as fast, half of each)
    renderer = Renderer(theme(tmp_path, "a.gif", speed=2), animate=True)
    seen = []
    for at in (100.0, 100.075, 100.2):  # (as 0, 0.15 and 0.4 into it)
        frame, _boxes = renderer.render(Snapshot(), at)
        seen.append(frame.getpixel((40, 30))[:3])
    assert seen[0][0] > 200 and seen[1][1] > 100 and seen[2][2] > 200  # red, green, blue
    renderer.close()


def test_a_slideshow_plays_a_moving_picture_whole_rounds_from_its_start(tmp_path):
    """A still shows its seconds; an animated picture as many whole rounds as take them at
    least (0.6 s round, 1 s: two rounds), from its first frame, then the next."""
    Image.new("RGB", (8, 8), "white").save(tmp_path / "still.png")
    gif(tmp_path / "a.gif")
    renderer = Renderer(theme(tmp_path, "still.png", slides="a.gif", seconds=1), animate=True)

    def colour(at):
        frame, _boxes = renderer.render(Snapshot(), at)
        return frame.getpixel((40, 30))[:3]

    assert colour(10.0) == (255, 255, 255)  # the still, until 11
    assert colour(10.9) == (255, 255, 255)
    red = colour(11.0)  # the GIF from its first frame, until 12.2
    assert red[0] > 200 and red[1] < 60
    assert colour(11.45)[2] > 200  # (0.45 into its first round: blue)
    assert colour(12.15)[2] > 200  # (its second round, at its end)
    assert colour(12.2) == (255, 255, 255)  # the still again
    renderer.close()
    still = Renderer(theme(tmp_path, "still.png", slides="still.png", seconds=1))
    assert still._shows == {} and colour  # (stills only: by the clock, as ever)
    still.render(Snapshot(), 1.0)
    assert still._shows == {}
    still.close()


@needs_ffmpeg
def test_a_slideshow_plays_a_video_to_its_end(tmp_path):
    clip = video(tmp_path / "clip.mp4", seconds=2)
    assert moving.video_length(FFMPEG, clip) == pytest.approx(2.0, abs=0.1)
    assert moving.video_length(FFMPEG, tmp_path / "missing.mp4") is None
    Image.new("RGB", (8, 8), "white").save(tmp_path / "still.png")
    preview = Renderer(theme(tmp_path, "still.png", slides="clip.mp4", seconds=1), preview=True)

    def white(at):
        frame, _boxes = preview.render(Snapshot(), at)
        return frame.getpixel((40, 30))[:3] == (255, 255, 255)

    assert white(0.0) and white(0.95)
    assert not white(1.0) and not white(2.9)  # the video: its two seconds, longer than one
    assert white(3.05)
    preview.close()
    faster = Renderer(theme(tmp_path, "still.png", slides="clip.mp4", seconds=1, speed=2),
                      preview=True)  # fmt: skip
    assert faster._slide_seconds(faster.widgets[0], "clip.mp4") == pytest.approx(1.0, abs=0.05)
    faster.close()


@needs_ffmpeg
def test_a_video_added_in_design_at_size_0_shows_at_its_own_size(tmp_path):
    """Design adds a picture at 0 x 0, "as it is": a still and a GIF showed, a video nothing,
    in the editor and on the panel (#33). It takes its own size, within the theme's."""
    video(tmp_path / "round.mp4", size="64x48", seconds=1)
    info = moving.video_info(FFMPEG, tmp_path / "round.mp4")
    assert info.size == (64, 48) and info.length == pytest.approx(1.0, abs=0.1)
    assert moving.video_info(FFMPEG, tmp_path / "missing.mp4") == moving.VideoInfo()
    for preview in (True, False):
        renderer = Renderer(theme(tmp_path, "round.mp4", w=0, h=0), preview=preview,
                            animate=not preview)  # fmt: skip
        deadline, frame = time.monotonic() + 10, None
        while time.monotonic() < deadline:
            frame, boxes = renderer.render(Snapshot(), time.monotonic())
            if frame.getpixel((32, 24))[:3] != (0, 0, 0):
                break
            time.sleep(0.05)
        assert frame.getpixel((32, 24))[:3] != (0, 0, 0), preview  # (drawn)
        assert frame.getpixel((100, 70))[:3] == (0, 0, 0)  # (its own 64 x 48, no more)
        renderer.close()
    # larger than the theme (200 x 100): within it, its shape kept
    video(tmp_path / "big.mp4", size="400x100", seconds=1)
    renderer = Renderer(theme(tmp_path, "big.mp4", w=0, h=0), preview=True)
    frame, _boxes = renderer.render(Snapshot())
    assert frame.getpixel((190, 40))[:3] != (0, 0, 0) and frame.getpixel((100, 60))[:3] == (0, 0, 0)
    renderer.close()


@needs_ffmpeg
def test_a_turned_video_is_read_upright(tmp_path):
    clip = video(tmp_path / "wide.mp4", size="64x48", seconds=1)
    turned = tmp_path / "turned.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", "90", "-i", str(clip),
                    "-c", "copy", str(turned)], check=True, timeout=60)  # fmt: skip
    assert moving.video_info(FFMPEG, turned).size == (48, 64)


@needs_ffmpeg
def test_a_video_starts_over_from_its_first_frame(tmp_path):
    clip = video(tmp_path / "clip.mp4")
    feed = moving.VideoFeed(FFMPEG, clip, 40, 30, "contain")
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and feed.frame()[1] is None:
            time.sleep(0.05)
        assert feed.frame()[1] is not None
        feed.from_start()
        assert feed.frame()[1] is None  # (anew: its first frame shows until a new one comes)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and feed.frame()[1] is None:
            time.sleep(0.05)
        assert feed.frame()[1] is not None
    finally:
        feed.close()


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


@needs_ffmpeg
def test_the_renderer_says_when_its_frame_is_one_video_alone(tmp_path):
    """[video] direct: the frame is the video alone, edge to edge across the theme (200 x 100),
    or not (another part, a smaller box, a rounded corner, an effect)."""
    video(tmp_path / "loop.mp4", size="200x100", seconds=1)

    def whole(**fields):
        data = {"format": THEME_FORMAT, "display": {"width": 200, "height": 100},
                "widgets": [{"type": "image", "id": "v", "x": 0, "y": 0, "w": 200, "h": 100,
                             "src": "loop.mp4", "fit": "cover", **fields}]}  # fmt: skip
        renderer = Renderer(parse_theme(data, root=tmp_path), preview=True)
        renderer.render(Snapshot())
        renderer.close()
        return renderer.full_video

    assert whole() == (str(tmp_path / "loop.mp4"), "cover", 1.0)
    assert whole(speed=2)[2] == 2.0 and whole(w=0, h=0) is not None  # (its own size: the same)
    assert whole(w=150) is None and whole(x=10) is None
    assert whole(radius=8) is None and whole(glow=0.5) is None and whole(opacity=0.5) is None
    text = {"type": "text", "id": "t", "x": 4, "y": 4, "text": "over it", "font_size": 12}
    data = {"format": THEME_FORMAT, "display": {"width": 200, "height": 100},
            "widgets": [{"type": "image", "id": "v", "x": 0, "y": 0, "w": 200, "h": 100,
                         "src": "loop.mp4", "fit": "cover"}, text]}  # fmt: skip
    renderer = Renderer(parse_theme(data, root=tmp_path), preview=True)
    renderer.render(Snapshot())
    renderer.close()
    assert renderer.full_video is None  # something else in the frame

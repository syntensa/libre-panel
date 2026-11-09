"""rust/render's videos against Python's (docs/RUST.md): render/moving.py's ffmpeg commands, what
`ffmpeg -i` says of a video read as its patterns read it, the first frames, a feed's frame of each
moment, and the renderer's frames with video widgets (fitted, blurred, rounded, of no size of
their own, faster and slower, in slideshows, in a preview), to the bit. ffmpeg's frames are its
own bytes: both ask the same ffmpeg the same. Between the frames of a feed or a renderer both
wait until the video's frames ahead are decoded, so that what a frame shows does not depend on
how fast ffmpeg is. Without ffmpeg, what needs it is skipped."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from rust_check import picture, run

from libre_panel import fonts
from libre_panel.render import moving
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import THEME_FORMAT, load_theme, resolve_asset

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def _bits(x: float | None) -> str | None:
    return None if x is None else struct.pack(">d", x).hex()


def _digest(image: Any) -> str | None:
    return None if image is None else hashlib.sha256(image.tobytes()).hexdigest()


def _info(info: moving.VideoInfo) -> dict[str, Any]:
    return {"length": _bits(info.length), "size": list(info.size) if info.size else None,
            "fps": _bits(info.fps)}  # fmt: skip


def test_ffmpeg_s_commands_as_python_makes_them() -> None:
    # (every fit, a fit there is not, rates, speeds as %g writes them, starts to the thousandth
    # and their halves, its first frame alone, to its end, its keyframes alone)
    rng = random.Random(0xF1F)
    items = []
    for _ in range(600):
        items.append({
            "path": rng.choice(["v.mp4", "a b.webm", "x.MOV", "ü.mkv"]),
            "w": rng.randrange(1, 4000), "h": rng.randrange(1, 4000),
            "fit": rng.choice(["cover", "contain", "stretch", "fill", ""]),
            "fps": rng.choice([1, 4, 24, 25, 30, 60, 144]), "once": rng.random() < 0.3,
            "speed": rng.choice([1.0, 0.25, 0.5, 2.0, 4.0, 1.5, 1 / 3, 1.25e-5, 123456789.0,
                                 rng.uniform(0.25, 4)]),
            "start": rng.choice([0.0, 0.0, -1.0, 1.5, 0.0004, 0.0005, 0.0015, 0.0025, 12.3456,
                                 rng.uniform(0, 100)]),
            "rounds": rng.random() < 0.7, "keys": rng.random() < 0.3,
        })  # fmt: skip
    want = [moving.video_command("ffmpeg", Path(i["path"]), i["w"], i["h"], i["fit"], i["fps"],
                                 once=i["once"], speed=i["speed"], start=i["start"],
                                 rounds=i["rounds"], keys=i["keys"])
            for i in items]  # fmt: skip
    [got] = run([{"op": "video", "kind": "commands", "ffmpeg": "ffmpeg", "items": items}])
    assert got["made"] == want


# what ffmpeg 7 says of videos, as the texts below are made of
SAID = [
    b"Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4':\n  Metadata:\n    major_brand     : isom\n"
    b"  Duration: 00:00:01.00, start: 0.000000, bitrate: 28 kb/s\n"
    b"  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), "
    b"64x48 [SAR 1:1 DAR 4:3], 18 kb/s, 30 fps, 30 tbr, 15360 tbn (default)\n"
    b"      Side data:\n      displaymatrix: rotation of 90.00 degrees\n",
    b"  Duration: 00:00:01.00, start: 0.000000, bitrate: 119 kb/s\n"
    b"  Stream #0:0[0x1](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, "
    b"70 kb/s (default)\n"
    b"  Stream #0:1[0x2](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), "
    b"80x60 [SAR 1:1 DAR 4:3], 28 kb/s, 29.97 fps, 29.97 tbr, 30k tbn (default)\n",
    b"  Duration: N/A, start: 0.000000\n  Stream #0:0: Video: vp9 (Profile 0), yuv420p(tv), "
    b"1920x1080, SAR 1:1 DAR 16:9, 24 tbr, 1k tbn\n    Metadata:\n      rotate          : -90\n",
    # (turns of a half degree, rounded to the even one; a turn too large to round)
    b"  Stream #0:0: Video: h264, 64x48, 30 fps\n      displaymatrix: rotation of 90.5 degrees",
    b"  Stream #0:0: Video: h264, 64x48, 30 fps\n      displaymatrix: rotation of -89.5 degrees",
    b"  Stream #0:0: Video: h264, 64x48, 30 fps\n      displaymatrix: rotation of 270.5 degrees",
    b"  Stream #0:0: Video: h264, yuv420p, 64x48, 30 fps\n      displaymatrix: rotation of "
    + b"9" * 400
    + b" degrees\n",
]
PIECES = [b"Stream #", b"Video: ", b", ", b"x", b".", b" fps", b" tbr", b"fps", b"tbr", b" ",
          b"\n", b"rotation of ", b" degrees", b"rotate", b":", b"-", b"Duration: ", b"\t",
          b"\x0b", b"\x0c", b"\r", b"\x1c", b"\x85", b"\xa0", "\u0663".encode(), b"0", b"12",
          b"99999", b"123456", b"00:", b"0" * 30, b"9" * 400, b"Audio: ", b"Stream #0:0: ",
          b"rotate: ", b"rotate \t:\x0b-", b"rotation of -"]  # fmt: skip


def _said_texts(rng: random.Random, seeds: list[bytes], count: int) -> list[bytes]:
    """Texts like ffmpeg's: its own cut and mended here and there, lines of them, and texts
    made of the patterns' pieces."""
    texts = list(seeds)
    for _ in range(count):
        kind = rng.random()
        if kind < 0.35:
            text = bytearray(rng.choice(seeds))
            for _ in range(rng.randrange(1, 12)):
                at = rng.randrange(len(text) + 1)
                way = rng.random()
                if way < 0.4:
                    text[at:at] = rng.choice(PIECES)
                elif way < 0.7 and text:
                    del text[at : at + rng.randrange(1, 6)]
                else:
                    text[at:at] = bytes([rng.randrange(256)])
            texts.append(bytes(text))
        elif kind < 0.5:
            lines = rng.choice(seeds).split(b"\n")
            texts.append(b"\n".join(rng.sample(lines, min(len(lines), rng.randrange(1, 6)))))
        else:
            numbers = [str(rng.randrange(10 ** rng.randrange(1, 7))).encode()]
            texts.append(b"".join(rng.choice(PIECES + numbers)
                                  for _ in range(rng.randrange(1, 30))))  # fmt: skip
    return texts


def _python_said(texts: list[bytes]) -> list[dict[str, Any]]:
    """moving.video_info's reading of each text, as ffmpeg's answer to it."""
    out = []
    for said in texts:
        done = subprocess.CompletedProcess([], 1, b"", said)
        with mock.patch.object(moving.subprocess, "run", return_value=done):
            try:
                out.append(_info(moving.video_info("ffmpeg", Path("v.mp4"))))
            except OverflowError as error:
                out.append({"error": str(error)})
    return out


def test_what_ffmpeg_says_of_a_video_read_as_python_reads_it() -> None:
    # (the length, the size turned upright where a turn says so, the frames a second; the
    # first of each from the left, as Python's patterns find them; a turn too large to round)
    texts = _said_texts(random.Random(0x5A1D), SAID, 30000)
    want = _python_said(texts)
    [got] = run([{"op": "video", "kind": "said",
                  "items": [t.decode("latin-1") for t in texts]}])  # fmt: skip
    assert got["made"] == want
    found = [sum(1 for w in want if w.get(k)) for k in ("length", "size", "fps", "error")]
    assert min(found) > 0 and min(found[:3]) > 2500, found
    turned = sum(1 for t, w in zip(texts, want, strict=True)
                 if w.get("size") and b"rotat" in t)  # fmt: skip
    assert turned > 500, turned


def _video(folder: Path, name: str, size: str = "64x48", rate: str = "30", seconds: float = 1,
           *more: str) -> Path:  # fmt: skip
    assert FFMPEG is not None
    path = folder / name
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate={rate}", "-t", str(seconds), *more,
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=120)  # fmt: skip
    return path


def _videos(folder: Path) -> list[Path]:
    """Videos of other sizes, shapes, rates and kinds: one turned, one with its sound first,
    one of keyframes a second apart; a file of no video and one that is not there."""
    assert FFMPEG is not None
    folder.mkdir(parents=True, exist_ok=True)
    made = [
        _video(folder, "a.mp4", "64x48", "30", 2),
        _video(folder, "b.mkv", "100x50", "30000/1001", 1),
        _video(folder, "c.mov", "48x64", "60", 1),
        _video(folder, "d.m4v", "160x90", "24", 1.5, "-g", "12"),
        _video(folder, "e.mp4", "120x68", "2502/100", 1.2),  # (25.02: no faster than 25)
    ]
    turned = folder / "turned.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", "90", "-i",
                    str(made[0]), "-c", "copy", str(turned)], check=True, timeout=120)  # fmt: skip
    sound = folder / "sound.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-f", "lavfi", "-i",
                    "testsrc=size=80x60:rate=25", "-t", "1", "-map", "0:a", "-map", "1:v",
                    "-pix_fmt", "yuv420p", str(sound)], check=True, timeout=120)  # fmt: skip
    nothing = folder / "nothing.mp4"
    nothing.write_text("no video", encoding="utf-8")
    return [*made, turned, sound, nothing, folder / "missing.mp4"]


@needs_ffmpeg
def test_videos_and_their_first_frames_as_python_reads_them(tmp_path: Path) -> None:
    videos = _videos(tmp_path)
    want_info = [_info(moving.video_info(FFMPEG, path)) for path in videos]
    rng = random.Random(0xF125)
    firsts = []
    for path in videos:
        for _ in range(6):
            firsts.append({"path": str(path), "w": rng.choice([1, 7, 40, 64, 120, 333]),
                           "h": rng.choice([1, 5, 30, 48, 90, 200]),
                           "fit": rng.choice(["cover", "contain", "stretch"]),
                           "start": rng.choice([0.0, 0.0, 0.5, 0.9])})  # fmt: skip
    want_first = [_digest(moving.first_frame(FFMPEG, Path(f["path"]), f["w"], f["h"], f["fit"],
                                             start=f["start"]))
                  for f in firsts]  # fmt: skip
    # (a theme's video as the renderer finds it: its path resolved, its first frame made while a
    # feed of it plays, as a panel's first frame of it is)
    assets, want_assets = [], []
    for rel in ("a.mp4", "b.mkv", "e.mp4"):
        for feed in (False, True):
            assets.append({"root": str(tmp_path), "rel": rel, "w": 175, "h": 99, "fit": "contain",
                           "fps": 30, "feed": feed})  # fmt: skip
            path = resolve_asset(tmp_path, rel)
            playing = moving.VideoFeed(FFMPEG, path, 175, 99, "contain", fps=30) if feed else None
            try:
                if playing is not None:
                    playing.frame()
                first = _digest(moving.first_frame(FFMPEG, path, 175, 99, "contain"))
            finally:
                if playing is not None:
                    playing.close()
            want_assets.append([str(path), first])
    got = run([
        {"op": "video", "kind": "info", "ffmpeg": FFMPEG, "items": [str(p) for p in videos]},
        {"op": "video", "kind": "first", "ffmpeg": FFMPEG, "items": firsts},
        {"op": "video", "kind": "asset", "ffmpeg": FFMPEG, "items": assets},
    ])  # fmt: skip
    assert got[0]["made"] == want_info
    assert got[1]["made"] == want_first
    found = [[a.get("path"), a.get("first")] for a in got[2]["made"]]
    assert found == want_assets, got[2]["made"]  # (what ffmpeg said where they differ)
    assert want_info[5]["size"] == [48, 64]  # (turned: upright)
    assert sum(1 for d in want_first if d) > 30 and None in want_first
    assert all(first for _path, first in want_assets)


def _settle(feed: moving.VideoFeed) -> None:
    """Until the feed's thread has its frames ahead, or no more come."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with feed._lock:
            running = feed._thread is not None and feed._thread.is_alive()
            if not running or len(feed._ready) >= moving.AHEAD:
                return
        time.sleep(0.001)


def _python_feed(path: Path, reading: dict[str, Any], steps: list[list[Any]]) -> list[Any]:
    feed = moving.VideoFeed(FFMPEG or "ffmpeg", path, reading["w"], reading["h"], reading["fit"],
                            fps=reading["fps"], speed=reading["speed"], start=reading["start"],
                            rounds=reading["rounds"], keys=reading["keys"])  # fmt: skip
    out: list[Any] = []
    try:
        for step in steps:
            if step[0] == "frame":
                count, picture = feed.frame(step[1])
                solid = None if picture is None else moving.solid(picture)
                out.append([count, _digest(picture), solid])
            elif step[0] == "close":
                feed.close()
            elif step[0] == "from_start":
                feed.from_start()
            else:
                out.append(feed.ended)
            _settle(feed)
    finally:
        feed.close()
    return out


@needs_ffmpeg
def test_a_video_s_frame_of_each_moment_as_python_s_feed_gives_it(tmp_path: Path) -> None:
    # (frames asked for at their moments, between them, late, after a pause, back in time;
    # closed and asked again, from its start; to its end, its last frame staying; faster and
    # slower, from a point in it, its keyframes alone; a box of another shape whose sides are
    # clear; a video ffmpeg cannot read)
    videos = _videos(tmp_path)
    rng = random.Random(0xFEED)
    items, want = [], []
    for n in range(36):
        path = rng.choice(videos[:7] + [videos[7]] * (n % 12 == 0))
        reading = {"w": rng.choice([16, 40, 64, 90]), "h": rng.choice([12, 30, 48, 60]),
                   "fit": rng.choice(["cover", "contain", "stretch"]),
                   "fps": rng.choice([4, 10, 24, 30, 60]),
                   "speed": rng.choice([1.0, 1.0, 0.5, 2.0, 1.5]),
                   "start": rng.choice([0.0, 0.0, 0.4]), "rounds": rng.random() < 0.7,
                   "keys": rng.random() < 0.2}  # fmt: skip
        at, steps = rng.uniform(0, 1000), []
        for _ in range(rng.randrange(4, 16)):
            way = rng.random()
            if way < 0.8:
                steps.append(["frame", at])
                at += rng.choice([0, 1 / reading["fps"], 0.5 / reading["fps"], 0.01, 0.1,
                                  0.3, 0.6, 2.0, -0.2])  # fmt: skip
            elif way < 0.88:
                steps.append(["close"])
            elif way < 0.95:
                steps.append(["from_start"])
            else:
                steps.append(["ended"])
        items.append(dict(reading, path=str(path), steps=steps))
        want.append(_python_feed(path, reading, steps))
    [got] = run([{"op": "video", "kind": "feed", "ffmpeg": FFMPEG, "items": items}])
    assert got["made"] == want
    shown = [step for steps in want for step in steps if isinstance(step, list) and step[1]]
    assert len(shown) > 150 and {s[2] for s in shown} == {True, False}, len(shown)


def _slideshow(folder: Path, rng: random.Random, videos: list[str]) -> None:
    """A theme of a slideshow of videos alone, a second or two each: each plays whole rounds,
    as many as take that long, and from its start when its turn comes again."""
    shown = rng.sample(videos, 3)
    widget = {"type": "image", "id": "s", "src": shown[0], "slides": "\n".join(shown[1:]),
              "seconds": rng.choice([1, 2]), "x": 4, "y": 4, "w": 64, "h": 48,
              "fit": rng.choice(["cover", "stretch"]),
              "speed": rng.choice([1, 2, 0.5])}  # fmt: skip
    theme = {"format": THEME_FORMAT, "display": {"width": 96, "height": 64},
             "background": {"color": "#203040"}, "widgets": [widget]}  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")


def _own_sizes(folder: Path, size: tuple[int, int], videos: list[str]) -> None:
    """A theme of each video at its own size, as large as the theme lets it be (160 x 90 in
    110 x 70: 110 x 61.875, rounded)."""
    widgets = [{"type": "image", "id": f"o{n}", "src": name, "x": 2 * n, "y": n, "w": 0, "h": 0,
                "fit": "cover"} for n, name in enumerate(videos)]  # fmt: skip
    theme = {"format": THEME_FORMAT, "display": {"width": size[0], "height": size[1]},
             "background": {"color": "#304050"}, "widgets": widgets}  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")


def _alone_then_still(folder: Path, video: str) -> None:
    """A video across the whole theme taking turns with a still picture: the frame one video
    alone while it shows, none while the picture does."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), "#806040").save(folder / "still.png")
    widget = {"type": "image", "id": "v", "src": video, "slides": "still.png", "seconds": 1,
              "x": 0, "y": 0, "w": 96, "h": 64, "fit": "cover"}  # fmt: skip
    theme = {"format": THEME_FORMAT, "display": {"width": 96, "height": 64},
             "background": {"color": "#102030"}, "widgets": [widget]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")


def _theme(folder: Path, rng: random.Random, videos: list[str]) -> list[str]:
    """A theme of image widgets showing videos (and a still picture's slideshow with them):
    fitted, blurred, rounded, of no size of their own, faster and slower; one alone across the
    whole theme now and then (see-through, with a glow or a shadow, contained over the theme's
    picture, or taking turns with a still picture: no frame of one video alone then). The
    names it shows."""
    from PIL import Image

    width, height = rng.choice([(200, 100), (160, 160), (96, 64), (110, 70), (175, 99)])
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), "#806040").save(folder / "still.png")
    background: dict[str, Any] = {"color": "#102030"}
    widgets: list[dict[str, Any]] = []
    shown = []
    alone = rng.random() < 0.4
    for n in range(1 if alone else rng.randrange(1, 4)):
        src = rng.choice(videos)
        widget: dict[str, Any] = {
            "type": "image", "id": f"v{n}", "src": src,
            "x": rng.randrange(0, 60), "y": rng.randrange(0, 40),
            "w": rng.choice([0, 30, 64, 80]), "h": rng.choice([0, 24, 48, 60]),
            "fit": rng.choice(["cover", "contain", "stretch"]),
            "speed": rng.choice([1, 1, 0.5, 2, 1.5]),
        }  # fmt: skip
        if alone:
            widget.update(x=0, y=0, w=width, h=height,
                          fit=rng.choice(["cover", "stretch", "contain"]))  # fmt: skip
            way = rng.random()
            if way < 0.12:
                widget["opacity"] = 0.6
            elif way < 0.24:
                widget["glow"] = 0.5
            elif way < 0.36:
                widget["shadow"] = "#000000"
            elif way < 0.5:
                widget.update(slides="still.png", seconds=1)
            if rng.random() < 0.4:
                background["image"] = "still.png"
        elif rng.random() < 0.3:
            widget["blur"] = rng.choice([2, 5, 8, 12])
        elif rng.random() < 0.3:
            widget["radius"] = rng.choice([4, 12])
        if not alone and rng.random() < 0.25:
            widget["slides"] = "\n".join(rng.sample(videos, 2))
            widget["seconds"] = rng.choice([1, 2])
        widgets.append(widget)
        shown.append(src)
    theme = {"format": THEME_FORMAT, "display": {"width": width, "height": height},
             "background": background, "widgets": widgets}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    return shown


def _difference(got: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
    """What differs between Rust's step and Python's: their boxes and warnings, and where both
    frames are there, the pixels that differ (how many, their box, the first few)."""
    out: dict[str, Any] = {"boxes": (got["boxes"], want["boxes"]),
                           "warnings": (got["warnings"], want["warnings"]),
                           "moving": (got["moving"], want["moving"])}  # fmt: skip
    if "frame" in got and "frame" in want:
        ours, theirs = picture(got["frame"]), want["frame"]
        if ours.size != theirs.size or ours.mode != theirs.mode:
            out["frames"] = ((ours.mode, ours.size), (theirs.mode, theirs.size))
            return out
        a, b = ours.tobytes(), theirs.tobytes()
        n = len(ours.getbands())
        places = [k // n for k in range(0, len(a), n) if a[k : k + n] != b[k : k + n]]
        xs, ys = [p % ours.width for p in places], [p // ours.width for p in places]
        out["pixels"] = len(places)
        if places:
            out["box"] = (min(xs), min(ys), max(xs), max(ys))
            out["first"] = [(p % ours.width, p // ours.width, a[p * n : p * n + n].hex(),
                             b[p * n : p * n + n].hex()) for p in places[:4]]  # fmt: skip
    return out


def _python_frames(folder: Path, case: dict[str, Any]) -> list[dict[str, Any]]:
    renderer = Renderer(load_theme(folder), preview=case["preview"], animate=case["animate"])
    renderer._ffmpeg = FFMPEG
    renderer.video_fps = case.get("video_fps")
    renderer.video_keys = case.get("video_keys", False)
    out = []
    try:
        for step in case["steps"]:
            frame, boxes = renderer.render(Snapshot(), now=step["now"])
            full = renderer.full_video
            out.append({"sha256": _digest(frame), "boxes": boxes, "moving": renderer.moving,
                        "full_video": None if full is None else [full[0], full[1], full[2]],
                        "warnings": list(renderer.warnings)})  # fmt: skip
            if case.get("bytes"):  # (the frame itself, to say where it differs)
                out[-1]["frame"] = frame.copy()
            for feed in renderer._feeds.values():
                _settle(feed)
    finally:
        renderer.close()
    return out


@needs_ffmpeg
def test_frames_with_videos_as_python_draws_them(tmp_path: Path) -> None:
    # (a renderer's frames one after another at their moments, as the panel's loop draws them,
    # and as a preview shows them: the video's first frame; the frame one video alone)
    videos = _videos(tmp_path / "theme")
    names = [p.name for p in videos[:7]]
    rng = random.Random(0xF8A3E)
    cases, want = [], []
    for n in range(40):
        folder = tmp_path / "theme" if n == 0 else tmp_path / f"t{n}"
        if n:
            folder.mkdir()
            for name in names:
                shutil.copy(tmp_path / "theme" / name, folder / name)
        at, steps = rng.uniform(0, 10**5), []
        if n < 30:
            _theme(folder, rng, names)
            for _ in range(rng.randrange(3, 12)):
                steps.append({"snapshot": {}, "now": at})
                at += rng.choice([0, 1 / 30, 1 / 24, 0.05, 0.1, 0.5, 1.1, 3.0])
        elif n < 36:  # (a slideshow, round its videos and back to the first)
            _slideshow(folder, rng, names)
            for _ in range(24):
                steps.append({"snapshot": {}, "now": at})
                at += rng.choice([0.2, 0.3, 0.45, 0.6])
        elif n < 38:  # (each video at its own size)
            _own_sizes(folder, [(110, 70), (61, 45)][n - 36], names)
            for _ in range(3):
                steps.append({"snapshot": {}, "now": at})
                at += 0.1
        else:  # (a video alone, then a still picture)
            _alone_then_still(folder, names[n - 38])
            for _ in range(12):
                steps.append({"snapshot": {}, "now": at})
                at += 0.4
        case = {"folder": str(folder), "preview": n % 7 == 3, "animate": n % 5 != 4,
                "dither": False, "steps": steps, "ffmpeg": FFMPEG,
                "video_fps": rng.choice([None, 0, 4, 24, 30, 60]),
                "video_keys": rng.random() < 0.15,
                "bytes": n < 3}  # fmt: skip
        cases.append(case)
        want.append(_python_frames(folder, case))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn, alone, differ = 0, 0, []
    for n, (got, theirs) in enumerate(zip(ours["frames"], want, strict=True)):
        for i, (g, w) in enumerate(zip(got["steps"], theirs, strict=True)):
            assert "unported" not in g, (n, i, g)
            same = [g[k] == w[k] for k in ("sha256", "boxes", "moving", "warnings", "full_video")]
            if not all(same):
                differ.append((n, i, same, _difference(g, w)))
                continue
            drawn += 1
            alone += w["full_video"] is not None
    assert not differ, (len(differ), differ[:8])
    played = len({w["sha256"] for theirs in want for w in theirs})  # (frames that differ)
    assert drawn > 150 and alone > 5 and played > 80, (drawn, alone, played)

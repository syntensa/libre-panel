"""Pictures that move, in an image widget: an animated GIF (or WebP, or PNG) plays its frames
for as long as each says, round and round; a video (MP4, WebM, MOV, ...) plays through ffmpeg,
round and round, at the pace it was made for, or faster or slower (the widget's ``speed``).

- **An animated picture** is read once, each frame fitted, rounded and blurred as the widget
  wants it, and kept with its time: the frame on show is the one the time says
  (:class:`Animation`). One that would take more than ``FRAMES_BYTES`` keeps every second frame
  (or third ...), each showing as long as those it stands for.
- **A video** is decoded by ffmpeg into frames of the widget's size, fitted as the widget wants
  (``-re``: as fast as it plays), read in a thread of its own; the newest frame is the one on
  show (:class:`VideoFeed`). ffmpeg starts when the video is first shown and stops when nobody
  has asked for a frame for a few seconds (another theme). A preview or a still picture of a
  theme shows its first frame (:func:`first_frame`).

A panel driven every frame (in video mode, or a USB display as Waveshare's 2.8") shows them as
they are; another shows the frame of the moment each time it is drawn. In a slideshow each plays
whole rounds, as many as take its seconds at least: a video longer than them to its end
(:func:`video_length`).
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v", ".mkv")
FRAMES_BYTES = 96 * 1024 * 1024  # the frames of one animated picture, at most
LEAST_FRAME_S = 0.02  # a frame said to show for less (or 0): as browsers do, 0.1 s
USUAL_FRAME_S = 0.1
VIDEO_FPS = 30  # the most frames a second a video is decoded at
IDLE_S = 5.0  # a video nobody asked a frame of for this long: ffmpeg stops
START_TIMEOUT_S = 10.0
SPEEDS = (0.25, 4.0)  # how much slower or faster a moving picture plays, at most


def speed_of(value: object) -> float:
    """The widget's ``speed`` within ``SPEEDS`` (1: as it was made)."""
    try:
        speed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    return min(SPEEDS[1], max(SPEEDS[0], speed)) if speed == speed else 1.0


def is_video(rel: str) -> bool:
    return rel.lower().endswith(VIDEO_SUFFIXES)


@dataclass
class Animation:
    """The frames of an animated picture, finished, and when each one ends (seconds)."""

    frames: list[Image.Image]
    ends: list[float]

    @property
    def length(self) -> float:
        return self.ends[-1]

    def index(self, seconds: float) -> int:
        """The frame on show ``seconds`` into the animation, round and round."""
        at = seconds % self.length
        for n, end in enumerate(self.ends):
            if at < end:
                return n
        return len(self.ends) - 1

    @property
    def size(self) -> int:
        return sum(f.width * f.height * 4 for f in self.frames)


def read_animation(path: Path, finish: Callable[[Image.Image], Image.Image],
                   most: int = FRAMES_BYTES) -> Animation | None:  # fmt: skip
    """The animated picture at ``path``, each frame passed through ``finish`` (fitted, rounded
    ...); None for a picture of one frame (a still: drawn as one)."""
    with Image.open(path) as source:
        count = getattr(source, "n_frames", 1)
        if count <= 1:
            return None
        first = finish(source.convert("RGBA"))
        every = max(1, -(-count * first.width * first.height * 4 // most))  # (rounded up)
        frames: list[Image.Image] = []
        ends: list[float] = []
        took = 0.0
        for n, frame in enumerate(ImageSequence.Iterator(source)):
            seconds = (frame.info.get("duration") or 0) / 1000
            took += seconds if seconds >= LEAST_FRAME_S else USUAL_FRAME_S
            if n % every == every - 1 or n == count - 1:  # (one for each run of ``every``)
                frames.append(first if n == 0 else finish(frame.convert("RGBA")))
                ends.append(took)
        if every > 1:
            log.info("%s: %d frames, every %d kept (%d MB at most)", path.name, count, every,
                     most // 2**20)  # fmt: skip
    return Animation(frames, ends) if len(frames) > 1 else None


def _filter(w: int, h: int, fit: str) -> str:
    """ffmpeg's filter that fits the video into w x h as the widget's ``fit`` says."""
    if fit == "cover":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "contain":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0")  # fmt: skip
    return f"scale={w}:{h}"


def video_command(ffmpeg: str, path: Path, w: int, h: int, fit: str, fps: int = VIDEO_FPS,
                  once: bool = False, speed: float = 1.0) -> list[str]:  # fmt: skip
    """ffmpeg reading the video at ``path`` into raw RGBA frames of w x h on its output: at the
    pace it plays times ``speed``, round and round (``once``: its first frame alone)."""
    command = [ffmpeg, "-v", "error", "-nostdin"]
    paced = ""
    if not once:
        # read as fast as it plays (-re), or faster or slower, its frames' times along with it
        command += ["-re"] if speed == 1 else ["-readrate", f"{speed:g}"]
        command += ["-stream_loop", "-1"]
        paced = f"setpts=PTS/{speed:g}," if speed != 1 else ""
    command += ["-i", str(path), "-an", "-sn",
                "-vf", f"{paced}{_filter(w, h, fit)},fps={fps}" if not once else _filter(w, h, fit),
                "-pix_fmt", "rgba", "-f", "rawvideo"]  # fmt: skip
    if once:
        command += ["-frames:v", "1"]
    return [*command, "pipe:1"]


def _quiet() -> dict:
    """No console window for ffmpeg on Windows."""
    import sys

    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def video_length(ffmpeg: str, path: Path) -> float | None:
    """How long the video plays (seconds), as ffmpeg reads it from the file; None where it
    cannot tell."""
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
                                capture_output=True, timeout=START_TIMEOUT_S, check=False,
                                **_quiet())  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("video %s: no length (%s)", path.name, exc)
        return None
    found = re.search(rb"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", result.stderr)
    if found is None:
        return None
    hours, minutes, seconds = (float(part) for part in found.groups())
    length = hours * 3600 + minutes * 60 + seconds
    return length if length > 0 else None


def first_frame(ffmpeg: str, path: Path, w: int, h: int, fit: str) -> Image.Image | None:
    """The video's first frame at w x h; None where ffmpeg cannot read it."""
    try:
        result = subprocess.run(video_command(ffmpeg, path, w, h, fit, once=True),
                                capture_output=True, timeout=START_TIMEOUT_S, check=False,
                                **_quiet())  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("video %s: no first frame (%s)", path.name, exc)
        return None
    if len(result.stdout) < w * h * 4:
        said = result.stderr.decode(errors="replace").strip()[:200]
        log.warning("video %s: no frame (%s)", path.name, said or "ffmpeg said nothing")
        return None
    return Image.frombytes("RGBA", (w, h), result.stdout[: w * h * 4])


class VideoFeed:
    """A video played round and round by ffmpeg into frames of w x h; :meth:`frame` is the
    newest. ffmpeg starts with the first ask and stops after ``IDLE_S`` without one."""

    def __init__(self, ffmpeg: str, path: Path, w: int, h: int, fit: str, fps: int = VIDEO_FPS,
                 clock: Callable[[], float] = time.monotonic,
                 speed: float = 1.0) -> None:  # fmt: skip
        self.command = video_command(ffmpeg, path, w, h, fit, fps, speed=speed)
        self.name, self.size, self.clock = path.name, (w, h), clock
        self._lock = threading.Lock()
        self._newest: Image.Image | None = None
        self._count = 0  # frames read: a new one is a new picture to draw
        self._asked = 0.0
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._failed = 0.0  # when ffmpeg last ended by itself (tried again after a while)

    def frame(self) -> tuple[int, Image.Image | None]:
        """The number of the newest frame and the frame (None before the first)."""
        now = self.clock()
        with self._lock:
            self._asked = now
            running = self._thread is not None and self._thread.is_alive()
            if not running and now - self._failed >= IDLE_S:
                self._start()
            return self._count, self._newest

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(self.command, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, **_quiet())  # fmt: skip
        except OSError as exc:
            log.warning("video %s: ffmpeg did not start (%s)", self.name, exc)
            self._failed = self.clock()
            return
        self._thread = threading.Thread(target=self._read, args=(self._process,),
                                        name="video-feed", daemon=True)  # fmt: skip
        self._thread.start()

    def _read(self, process: subprocess.Popen) -> None:
        size = self.size[0] * self.size[1] * 4
        stream = process.stdout
        try:
            while stream is not None:
                data = stream.read(size)
                if len(data) < size:
                    break  # (it ended: a video it cannot read, or it was stopped)
                picture = Image.frombytes("RGBA", self.size, data)
                with self._lock:
                    self._newest, self._count = picture, self._count + 1
                    idle = self.clock() - self._asked > IDLE_S
                if idle:
                    break  # nobody looks: another theme
        finally:
            process.kill()
            process.wait()
            with self._lock:
                if self._process is process:
                    self._process = None
                    if self.clock() - self._asked <= IDLE_S:  # it ended by itself
                        self._failed = self.clock()
                        log.warning("video %s: ffmpeg stopped", self.name)

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            self._asked = -IDLE_S * 2
            self._newest = None  # (shown again, it begins with its first frame)
        if process is not None:
            process.kill()

    def from_start(self) -> None:
        """Play it from its start (a slideshow comes to it again): ffmpeg starts anew with the
        next ask, and its first frame shows until the new one's first comes."""
        thread = self._thread
        self.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)
        with self._lock:
            self._failed = -IDLE_S  # (not a failure: at once)

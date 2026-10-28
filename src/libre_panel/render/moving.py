"""Pictures that move, in an image widget: an animated GIF (or WebP, or PNG) plays its frames
for as long as each says, round and round; a video (MP4, WebM, MOV, ...) plays through ffmpeg,
round and round, at the pace it was made for, or faster or slower (the widget's ``speed``).

- **An animated picture** is read once, each frame fitted, rounded and blurred as the widget
  wants it, and kept with its time: the frame on show is the one the time says
  (:class:`Animation`). One that would take more than ``FRAMES_BYTES`` keeps every second frame
  (or third ...), each showing as long as those it stands for.
- **A video** is decoded by ffmpeg into frames of the widget's size, fitted as the widget wants,
  read in a thread of its own a few frames ahead; the frame on show is the one of the moment
  the panel's frame is drawn for (:class:`VideoFeed`), so each of the video's frames lands on
  frames of the panel of its own, none shown twice or passed over where the rates are the same
  (the newest frame, as ffmpeg played it at its own pace, did that for a third of them). ffmpeg
  starts when the video is first shown and stops when nobody has asked for a frame for a few
  seconds (another theme). A preview or a still picture of a theme shows its first frame
  (:func:`first_frame`).

A panel driven every frame (in video mode, or a USB display as Waveshare's 2.8") shows them as
they are; another shows the frame of the moment each time it is drawn. In a slideshow each plays
whole rounds, as many as take its seconds at least: a video longer than them to its end
(:func:`video_length`).
"""

from __future__ import annotations

import collections
import logging
import re
import subprocess
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v", ".mkv")
FRAMES_BYTES = 96 * 1024 * 1024  # the frames of one animated picture, at most
LEAST_FRAME_S = 0.02  # a frame said to show for less (or 0): as browsers do, 0.1 s
USUAL_FRAME_S = 0.1
VIDEO_FPS = 30  # the frames a second a video is decoded at, at most, where nothing says more
IDLE_S = 5.0  # a video nobody asked a frame of for this long: ffmpeg stops
AHEAD = 3  # frames of a video decoded before their moment (ffmpeg waits for room meanwhile)
# A video's frames without transparency anywhere (its first frame says: VideoFeed): drawn over
# what lies beneath without their alpha, which changes nothing there (a full-screen frame on
# the 12.3": 0.9 ms instead of 6.5 through its alpha; renderer._paste)
_SOLID: weakref.WeakValueDictionary[int, Image.Image] = weakref.WeakValueDictionary()
BEHIND_S = 0.5  # a video further behind its moment than this (after a pause): on from there
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
                  once: bool = False, speed: float = 1.0, start: float = 0.0,
                  rounds: bool = True) -> list[str]:  # fmt: skip
    """ffmpeg reading the video at ``path`` into raw RGBA frames of w x h on its output, ``fps``
    of them to a second of it played ``speed`` times as fast, round and round (``rounds``
    False: to its end; ``once``: its first frame alone), from ``start`` seconds into it. It goes
    as fast as they are read (:class:`VideoFeed` takes each at its moment)."""
    command = [ffmpeg, "-v", "error", "-nostdin"]
    paced = ""
    if not once:
        command += ["-stream_loop", "-1"] if rounds else []
        paced = f"setpts=PTS/{speed:g}," if speed != 1 else ""  # (its frames' times so)
    if start > 0:
        command += ["-ss", f"{start:.3f}"]  # (to the picture before, decoded on from there)
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


@dataclass(frozen=True)
class VideoInfo:
    """What ffmpeg reads of a video file: how long it plays (seconds), the size its pictures
    come out at (turned as it says, as ffmpeg turns them) and its frames a second; None where
    it cannot tell."""

    length: float | None = None
    size: tuple[int, int] | None = None
    fps: float | None = None


def video_info(ffmpeg: str, path: Path) -> VideoInfo:
    """A video's length, size and frames a second, from what ``ffmpeg -i`` says of it."""
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
                                capture_output=True, timeout=START_TIMEOUT_S, check=False,
                                **_quiet())  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("video %s: not read (%s)", path.name, exc)
        return VideoInfo()
    said = result.stderr
    length = None
    found = re.search(rb"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", said)
    if found is not None:
        hours, minutes, seconds = (float(part) for part in found.groups())
        length = hours * 3600 + minutes * 60 + seconds or None
    size = None
    found = re.search(rb"Stream #[^\n]*?Video: [^\n]*?, (\d{2,5})x(\d{2,5})", said)
    if found is not None:
        width, height = int(found[1]), int(found[2])
        turned = re.search(rb"rotation of (-?\d+(?:\.\d+)?) degrees|rotate\s*:\s*(-?\d+)", said)
        if turned is not None and round(float(turned[1] or turned[2])) % 180 == 90:
            width, height = height, width  # (played upright: ffmpeg turns it)
        size = (width, height)
    fps = None  # (the stream's "fps", else its "tbr": the rate its frames' times come at)
    found = re.search(rb"Stream #[^\n]*?Video: [^\n]*?, (\d+(?:\.\d+)?) (?:fps|tbr)", said)
    if found is not None:
        fps = float(found[1]) or None
    return VideoInfo(length, size, fps)


def video_length(ffmpeg: str, path: Path) -> float | None:
    """How long the video plays (seconds), as ffmpeg reads it from the file; None where it
    cannot tell."""
    return video_info(ffmpeg, path).length


def first_frame(ffmpeg: str, path: Path, w: int, h: int, fit: str,
                start: float = 0.0) -> Image.Image | None:  # fmt: skip
    """The video's first frame at w x h (``start`` seconds into it: the one there); None where
    ffmpeg cannot read it."""
    try:
        result = subprocess.run(video_command(ffmpeg, path, w, h, fit, once=True, start=start),
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


def solid(image: Image.Image) -> bool:
    """Whether every pixel of ``image`` is known to be opaque: a video's frame, as it came."""
    return _SOLID.get(id(image)) is image


class VideoFeed:
    """A video played round and round by ffmpeg into frames of w x h, ``fps`` to a second.

    ffmpeg decodes up to :data:`AHEAD` frames before their moment and then waits; :meth:`frame`
    gives the frame of the moment it is asked for (``at``: when the panel's frame is planned to
    show): the first frame at the first ask, each next one ``1 / fps`` later. So a video of the
    panel's frames a second shows each of its frames on one frame of the panel, one of fewer
    each on as many as its time spans, at an even pace. Further behind than :data:`BEHIND_S`
    (it was not asked for a while, or ffmpeg decodes slower than it plays) it goes on from the
    frame it has. ffmpeg starts with the first ask and stops after ``IDLE_S`` without one."""

    def __init__(self, ffmpeg: str, path: Path, w: int, h: int, fit: str, fps: int = VIDEO_FPS,
                 clock: Callable[[], float] = time.monotonic, speed: float = 1.0,
                 start: float = 0.0, rounds: bool = True,
                 began: float | None = None) -> None:  # fmt: skip
        self.command = video_command(ffmpeg, path, w, h, fit, fps, speed=speed, start=start,
                                     rounds=rounds)  # fmt: skip
        self.name, self.size, self.fps, self.clock = path.name, (w, h), fps, clock
        self.rounds = rounds
        self.ended = False  # (played to its end, ``rounds`` False: its last frame stays)
        self._lock = threading.Lock()
        self._room = threading.Condition(self._lock)  # (for ffmpeg's next frame)
        self._ready: collections.deque[Image.Image] = collections.deque()  # before their moment
        self._newest: Image.Image | None = None
        self._count = 0  # frames shown: a new one is a new picture to draw
        # the moment of its first frame (``began``, else the first ask once one is there; moved
        # on after a pause): frame n shows from _began + n / fps
        self._began = began
        self._asked = 0.0
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._failed = 0.0  # when ffmpeg last ended by itself (tried again after a while)

    def frame(self, at: float | None = None) -> tuple[int, Image.Image | None]:
        """The number of the frame of the moment ``at`` (on any clock, the same each time; the
        feed's own by default) and the frame (None before the first)."""
        now = self.clock()
        at = now if at is None else at
        with self._lock:
            self._asked = now
            running = self._thread is not None and self._thread.is_alive()
            if not running and not self.ended and now - self._failed >= IDLE_S:
                self._start()
            if self._ready and self._began is None:
                self._began = at  # (the first frame: now)
            if self._began is not None:
                due = int((at - self._began) * self.fps + 1e-6)  # the frame of the moment
                while self._ready and self._count <= due:
                    self._newest, self._count = self._ready.popleft(), self._count + 1
                    self._room.notify()
                if due - self._count > BEHIND_S * self.fps:  # (a pause: from the one it has)
                    self._began = at - max(self._count - 1, 0) / self.fps
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
        opaque = None  # (its first frame says: a video with transparency has it there)
        whole = False  # (to its end, as asked: not a failure)
        try:
            while stream is not None:
                data = stream.read(size)
                if len(data) < size:  # (it ended: a video it cannot read, or it was stopped)
                    whole = not data and not self.rounds and opaque is not None
                    break
                picture = Image.frombytes("RGBA", self.size, data)
                if opaque is None:
                    opaque = picture.getchannel("A").getextrema()[0] == 255
                if opaque:
                    _SOLID[id(picture)] = picture
                with self._lock:
                    while (len(self._ready) >= AHEAD and self._process is process
                           and self.clock() - self._asked <= IDLE_S):  # fmt: skip
                        self._room.wait(0.5)  # (ffmpeg waits on its pipe meanwhile)
                    if self._process is not process:
                        break  # closed
                    self._ready.append(picture)
                    idle = self.clock() - self._asked > IDLE_S
                if idle:
                    break  # nobody looks: another theme
        finally:
            process.kill()
            process.wait()
            with self._lock:
                if self._process is process:
                    self._process = None
                    if whole:
                        self.ended = True
                    elif self.clock() - self._asked <= IDLE_S:  # it ended by itself
                        self._failed = self.clock()
                        log.warning("video %s: ffmpeg stopped", self.name)

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            self._asked = -IDLE_S * 2
            self._newest = None  # (shown again, it begins with its first frame)
            self._ready.clear()
            self._began, self.ended = None, False
            self._room.notify_all()
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

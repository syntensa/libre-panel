"""H.264 for panels with a hardware video decoder: ffmpeg encodes, pictures come out one by one.

The encoder settings were measured on the 9.2" TURZX panel by the SPUR II
project (docs/protocol/turzx-usb.md, "Video layer"):

- Constrained Baseline, no B-frames, and **one slice per picture**
  (``sliced-threads=0``): the panel acknowledges multi-slice pictures but
  shows nothing.
- A keyframe every few seconds (3-10 s). Every keyframe re-quantises static
  areas, so at 1 s and below flat backgrounds visibly pulse.
  ``repeat-headers=1`` lets the decoder join at any keyframe.
- ffmpeg rotates into the panel's framebuffer (``transpose``); the stream is
  bit-identical to rotating in Python and costs the render loop nothing.
- The bit rate was capped at 2 Mbit/s there, for 1920x480 at 50 frames a second. A panel
  with more pixels (the 12.3": 1920x720) or more frames starts with proportionally more
  (``maxrate`` "auto", :func:`auto_rate`), so its pictures and lettering stay as sharp; and
  "auto" then follows the panel (:class:`RateControl`): a step down while the panel takes
  less than it is sent, a step up again after a calm minute, up to 8 Mbit/s.

:class:`PictureSplitter` cuts ffmpeg's output into pictures by their NAL
units. That works however the pipe splits the bytes (Windows pipes deliver
small pieces).
"""

from __future__ import annotations

import collections
import logging
import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from libre_panel import bundled, winsession
from libre_panel.devices.base import Counters, DeviceError
from libre_panel.i18n import t

log = logging.getLogger(__name__)

X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")

NAL_SLICE, NAL_IDR = 1, 5
# NAL types that open a new picture when they follow a slice (H.264 7.4.1.2.3):
# SEI, SPS, PPS, access unit delimiter and the reserved 14-18.
_OPENS_PICTURE = {6, 7, 8, 9, 14, 15, 16, 17, 18}

# Windows: a console program started from a program without a console (the
# tray app) gets a black window of its own unless told not to.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class EncoderSettings:
    fps: int = 50
    crf: int = 25
    preset: str = "superfast"
    keyframe_s: float = 10.0
    maxrate: str = "auto"  # "auto": REFERENCE_RATE scaled by the pixels and the frame rate
    threads: int = 1  # frame threads add a frame of latency each


# The bit rate measured on the 9.2": 2 Mbit/s for its 1920x480 pixels at 50 frames a second.
REFERENCE_RATE = (2.0, 1920 * 480, 50)
# The steps "auto" moves along, in Mbit/s: a panel takes 170-300 KB/s on its USB link
# (docs/protocol/turzx-usb.md), the top is for one that takes more.
RATE_STEPS = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)


def auto_rate(settings: EncoderSettings, size: tuple[int, int]) -> float:
    """The bit rate "auto" starts with for pictures of ``size``, in Mbit/s: the reference
    rate scaled by the pixels and the frame rate, and never less than the reference (a
    small panel has the bandwidth to spare)."""
    rate, pixels, fps = REFERENCE_RATE
    width, height = size
    scaled = rate * (width * height / pixels) * (settings.fps / fps)
    return max(rate, round(scaled, 1))


def rate_text(mbit: float) -> str:
    """A bit rate as ffmpeg takes it: 3.0 -> "3.0M"."""
    return f"{mbit:.1f}M"


def mbit(maxrate: str) -> float:
    """A bit rate as config.toml gives it ("2M", "1500k", "2000000") in Mbit/s; 0 for "auto"."""
    text = maxrate.strip()
    if not text or text == "auto":
        return 0.0
    scale = {"k": 1e-3, "K": 1e-3, "M": 1.0}.get(text[-1])
    if scale is not None:
        text = text[:-1]
    else:
        scale = 1e-6
    try:
        return round(float(text) * scale, 3)
    except ValueError:
        return 0.0


def bit_rate(settings: EncoderSettings, size: tuple[int, int]) -> str:
    """The highest bit rate for pictures of ``size``, as ffmpeg takes it: the settings' own,
    or with "auto" the rate it starts with (:func:`auto_rate`)."""
    if settings.maxrate != "auto":
        return settings.maxrate
    return rate_text(auto_rate(settings, size))


class RateControl:
    """The bit rate of ``maxrate`` "auto" while the panel runs.

    It starts at :func:`auto_rate` and moves along :data:`RATE_STEPS`. A step down when the
    panel takes less than it is sent, seen within a window of :attr:`WINDOW_S` either as the
    sender waiting for room in the panel's queue :attr:`TROUBLE_WAITS` times (its decoder
    lags) or as half the blocks, :attr:`TROUBLE_SLOW` at least, taking longer than a frame
    (its link lags; the median, so that a busy PC's single late blocks do not count). A
    step up after :attr:`CALM_S` without that, as long as the step above did not fail
    lately: a failed step is held off for :attr:`HOLD_S`, twice as long each time it fails
    again. :meth:`judge` is called with the run's counters as often as wanted (every
    frame); it answers with the new rate when one is due, else ``None``. The driver starts
    ffmpeg anew with the new rate.
    """

    WINDOW_S = 10.0
    TROUBLE_WAITS = 5
    TROUBLE_SLOW = 25
    CALM_S = 60.0
    HOLD_S = 600.0

    def __init__(self, start: float) -> None:
        self.steps = sorted({*RATE_STEPS, start})
        self.rate = start
        self.changes = 0
        self._base: tuple[int, int, int] | None = None  # the counts when the window began
        self._window = self._calm = 0.0
        self._held: dict[float, float] = {}  # step -> until when it is not tried
        self._failed: dict[float, int] = {}  # step -> how often it failed

    def judge(self, counters: Counters, now: float) -> float | None:
        counts = (counters.waits, counters.blocks, counters.slow_blocks)
        if self._base is None:
            self._base, self._window, self._calm = counts, now, now
            return None
        waits, blocks, slow = (new - old for new, old in zip(counts, self._base, strict=True))
        trouble = waits >= self.TROUBLE_WAITS or (slow >= self.TROUBLE_SLOW and 2 * slow >= blocks)
        if not trouble and now - self._window < self.WINDOW_S:
            return None
        self._base, self._window = counts, now  # a new window
        if trouble:
            self._calm = now
            fails = self._failed.get(self.rate, 0)
            self._failed[self.rate] = fails + 1
            self._held[self.rate] = now + self.HOLD_S * 2**fails
            return self._move(-1)
        if now - self._calm < self.CALM_S:
            return None
        above = self._beside(+1)
        if above is None or self._held.get(above, 0.0) > now:
            return None
        self._calm = now
        return self._move(+1)

    def _beside(self, direction: int) -> float | None:
        i = self.steps.index(self.rate) + direction
        return self.steps[i] if 0 <= i < len(self.steps) else None

    def _move(self, direction: int) -> float | None:
        rate = self._beside(direction)
        if rate is None:
            return None
        self.rate = rate
        self.changes += 1
        return rate


# How ffmpeg turns a theme frame into the panel's framebuffer, per rotation
# (the same turns as turzx_usb.to_native).
ROTATION_FILTERS = {
    None: None,
    270: "transpose=1",  # clockwise, like PIL's ROTATE_270
    180: "hflip,vflip",
}


def find_ffmpeg(configured: str = "") -> str:
    """The ffmpeg program: the configured path, the one a download carries (its ``tools``
    folder), PATH, or next to Libre Panel."""
    exe = bundled.program_name("ffmpeg")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        raise DeviceError(t("ffmpeg not found at {path} (video.ffmpeg in config.toml)", path=path))
    own = bundled.find(exe)  # first: the build the download was tested with
    if own:
        return own
    found = shutil.which("ffmpeg")
    if found:
        return found
    beside = Path(sys.executable).resolve().parent / exe
    if beside.is_file():
        return str(beside)
    raise DeviceError(
        t(
            "the video mode needs ffmpeg: the downloads of Libre Panel carry it; "
            "with pip install it, or set video.ffmpeg in config.toml to the ffmpeg program"
        )
    )


def ffmpeg_command(
    ffmpeg: str, size: tuple[int, int], rotate: int | None, settings: EncoderSettings
) -> list[str]:
    """ffmpeg reading raw RGB frames of ``size`` on stdin, writing H.264 (Annex B) to stdout."""
    width, height = size
    keyint = max(1, round(settings.fps * settings.keyframe_s))
    maxrate = bit_rate(settings, size)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-framerate",
        str(settings.fps),
        "-i",
        "-",
        "-an",
    ]
    video_filter = ROTATION_FILTERS[rotate]
    if video_filter:
        command += ["-vf", video_filter]
    return command + [
        "-c:v",
        "libx264",
        "-profile:v",
        "baseline",
        "-preset",
        settings.preset,
        "-tune",
        "zerolatency",
        "-threads",
        str(settings.threads),
        "-crf",
        str(settings.crf),
        "-maxrate",
        maxrate,
        "-bufsize",
        maxrate,
        "-x264-params",
        # zerolatency turns slice threads on; the panel needs one slice per picture
        f"sliced-threads=0:bframes=0:scenecut=0:keyint={keyint}:min-keyint={keyint}"
        ":ipratio=2.0:repeat-headers=1",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "h264",
        "-",
    ]


def nal_units(data: bytes) -> list[tuple[int, int]]:
    """(offset, type) of every NAL unit in an Annex B stream."""
    found, pos = [], 0
    while True:
        i = data.find(b"\x00\x00\x01", pos)
        if i < 0 or i + 3 >= len(data):
            return found
        found.append((i + 3, data[i + 3] & 0x1F))
        pos = i + 3


def slice_count(data: bytes) -> int:
    return sum(1 for _, kind in nal_units(data) if kind in (NAL_SLICE, NAL_IDR))


class PictureSplitter:
    """Cuts an Annex B stream into pictures: parameter sets and SEI, then the slice.

    A picture is known to be complete when the next one begins, so each one
    is handed on when the next arrives (one frame later: 20 ms at 50 fps);
    :meth:`flush` returns the last one at the end of the stream.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0  # start codes before this offset are handled
        self._has_slice = False
        self.multi_slice = False

    def feed(self, data: bytes) -> list[bytes]:
        buf = self._buf
        buf += data
        pictures = []
        pos = self._pos
        while True:
            i = buf.find(b"\x00\x00\x01", pos)
            if i < 0:
                pos = max(pos, len(buf) - 2)  # a start code may straddle the next piece
                break
            if i + 4 >= len(buf):  # need the NAL header and the first slice header byte
                pos = i
                break
            kind = buf[i + 3] & 0x1F
            opens = False
            if kind in (NAL_SLICE, NAL_IDR):
                first_in_picture = bool(buf[i + 4] & 0x80)  # first_mb_in_slice == 0
                opens = self._has_slice and first_in_picture
                if self._has_slice and not first_in_picture:
                    self.multi_slice = True
            elif kind in _OPENS_PICTURE:
                opens = self._has_slice
            if opens:
                cut = i - 1 if i > 0 and buf[i - 1] == 0 else i  # 4-byte start code
                pictures.append(bytes(buf[:cut]))
                del buf[:cut]
                i -= cut
                self._has_slice = False
            if kind in (NAL_SLICE, NAL_IDR):
                self._has_slice = True
            pos = i + 3
        self._pos = pos
        return pictures

    def flush(self) -> list[bytes]:
        """The last picture, at the end of the stream."""
        rest = bytes(self._buf) if self._has_slice else b""
        self._buf.clear()
        self._pos, self._has_slice = 0, False
        return [rest] if rest else []


class EncoderStopped(DeviceError):
    """ffmpeg ended or stopped taking frames."""


class Encoder:
    """One ffmpeg process: raw frames in, pictures out.

    A writer thread feeds ffmpeg, so a stalled ffmpeg cannot block the render
    loop for longer than the ``write`` timeout. A reader thread cuts the output
    into pictures and hands each to ``deliver``, which may block: that is the
    back-pressure from the panel, passed through the pipe to the render loop.
    """

    def __init__(
        self,
        command: list[str],
        size: tuple[int, int],
        deliver: Callable[[bytes], None],
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.size = size
        self.command = command
        self._deliver = deliver
        self._frames: queue.Queue[bytes | None] = queue.Queue(maxsize=1)
        self._closing = threading.Event()
        self._done = threading.Event()  # ffmpeg's output ended
        self.errors: collections.deque[str] = collections.deque(maxlen=10)
        self.splitter = PictureSplitter()
        self.pictures = 0
        if winsession.ending():  # Windows refuses a new program now, with a box that names it
            raise DeviceError("Windows is ending the session: no ffmpeg now")
        try:
            self.proc = popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            raise DeviceError(t("cannot start ffmpeg: {error}", error=exc)) from exc
        self._threads = [
            threading.Thread(target=target, name=f"ffmpeg-{name}", daemon=True)
            for name, target in (
                ("writer", self._write_loop),
                ("reader", self._read_loop),
                ("errors", self._error_loop),
            )
        ]
        for thread in self._threads:
            thread.start()

    @property
    def alive(self) -> bool:
        return not self._done.is_set() and self.proc.poll() is None

    def reason(self) -> str:
        """Why ffmpeg stopped, as far as it said."""
        if self.errors:
            return self.errors[-1]
        code = self.proc.poll()
        return f"exit code {code}" if code is not None else "no output"

    def write(self, frame: bytes, timeout: float = 2.0) -> None:
        if not self.alive:
            raise EncoderStopped(f"ffmpeg stopped ({self.reason()})")
        try:
            self._frames.put(frame, timeout=timeout)
        except queue.Full:
            raise EncoderStopped(f"ffmpeg takes no frames for {timeout:.0f} s") from None

    def _write_loop(self) -> None:
        stdin = self.proc.stdin
        try:
            while True:
                frame = self._frames.get()
                if frame is None or self._closing.is_set():
                    break
                view = memoryview(frame)
                while view:  # an unbuffered pipe may take less than asked
                    view = view[stdin.write(view) :]
        except (OSError, ValueError):  # ffmpeg is gone; the reader notices the end
            pass
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        try:
            while True:
                data = stdout.read(65536)
                if not data:
                    break
                for picture in self.splitter.feed(data):
                    self._hand_on(picture)
            if not self._closing.is_set():
                for picture in self.splitter.flush():
                    self._hand_on(picture)
        except (OSError, ValueError):
            pass
        finally:
            self._done.set()

    def _hand_on(self, picture: bytes) -> None:
        if self._closing.is_set():
            return
        self.pictures += 1
        self._deliver(picture)

    def _error_loop(self) -> None:
        try:
            for line in self.proc.stderr:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self.errors.append(text)
                    log.warning("ffmpeg: %s", text)
        except (OSError, ValueError):
            pass

    def close(self, timeout: float = 3.0) -> None:
        """Stop ffmpeg; pictures still in the pipe are dropped."""
        self._closing.set()
        try:
            self._frames.put_nowait(None)
        except queue.Full:
            try:  # make room for the stop sign; the frame is not needed any more
                self._frames.get_nowait()
                self._frames.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass
        children = self._children()  # before the parent ends: they are found through it
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout)
        for child in children:
            try:
                child.kill()
            except psutil.Error:
                pass
        for thread in self._threads:
            thread.join(timeout)
        if any(thread.is_alive() for thread in self._threads):
            return  # a thread still reads a pipe; closing it under the thread can block
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def _children(self) -> list[psutil.Process]:
        """What ffmpeg started. ffmpeg from a package manager (Chocolatey, scoop) is a
        shim that starts the real one: ending the shim alone leaves that running, and
        a frozen one keeps the pipes open for good."""
        try:
            return psutil.Process(self.proc.pid).children(recursive=True)
        except (psutil.Error, AttributeError, TypeError, ValueError):
            return []

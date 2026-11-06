"""Video mode for TURZX USB panels: frames go to the panel's H.264 decoder at up to 50 fps.

The panel has two layers: an H.264 stream (command 121) underneath and an
RGBA PNG (command 102) on top. The PNG path alone manages about 9 fps on
the 9.2", because the panel takes about 60 ms per full frame. The decoder has
no such limit. Libre Panel puts the whole frame into the video and keeps the
PNG layer transparent.

Everything here was measured on the 9.2" by the SPUR II project, which has
driven the panel this way around the clock at 50 fps
(docs/protocol/turzx-usb.md, "Video layer"):

- **Start:** ``110(name)`` clears the framebuffer (without it the screen
  stays black), ``111`` stops the local playback that 110 started, ``112``
  asks whether it still plays, then brightness, a transparent PNG layer, the
  player's rate (``15``) and the block size (``17``).
- **One picture per 121 block.** The panel's queue counts blocks, so a
  keyframe cut into three blocks looks like a queue of three.
- **Flow control:** ask for the queue depth (``122``) every second block;
  above 2, wait in 30 ms steps until it is at most 1. The queue is a ring of
  five blocks that silently overwrites unread ones, so flooding breaks the
  picture.
- **The player's rate** (``15``) is reported above the rate delivered: the
  player shows each picture strictly on its own clock and never catches up.
- **Stop:** ``123`` ends the stream, ``15 = 30`` gives the panel its power-on
  rate back (its own standby clip plays at the last rate set), and the last
  frame stays on the panel as a sharp PNG.
- **A hung decoder** takes each block only after about 750 ms and its queue
  stands high without moving. Detected as SPUR II does it (see
  :meth:`VideoSession.check_hang`). Nothing but a restart of the panel (11)
  or replugging clears it, so Libre Panel restarts the panel as SPUR II
  does: ``11``, wait until it leaves the bus (at most 20 s) and comes back
  (at most 90 s), 2 s more, then a full start with a new ffmpeg. At most
  three times in a row; after 300 s without a hang the count starts again.
  After that it asks to replug the panel.

This mode never sends commands that store anything on the panel: the start
sequence without 13 (save settings), 125 and 42 shows the video, and after a
power cycle the panel behaves exactly as before (measured on the 9.2").
The name sent with 110 stays in the panel's memory until it restarts.
"""

from __future__ import annotations

import functools
import logging
import queue
import statistics
import struct
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PIL import Image

from libre_panel import timing
from libre_panel.devices import card_choice, h264, video_state
from libre_panel.devices.base import Counters, DeviceError, FrameError
from libre_panel.devices.engine import (
    EngineFailed,
    EngineHung,
    EngineLink,
    EngineStream,
    EngineTransport,
    can_send,
    cuts_views,
    encodes_on_cards,
    encodes_on_proven_cards,
    engine_scene,
    gives_shown,
    grows_sources,
    lays_laid,
    makes_bars,
    makes_fans,
    makes_fills,
    makes_globes,
    makes_masks,
    makes_rings,
    takes_scenes,
)
from libre_panel.devices.h264 import (
    BOXES,
    CARD_ENCODERS,
    NATIVE,
    YUV,
    CaptureSource,
    Encoder,
    EncoderSettings,
    EncoderStopped,
    Fed,
    RateControl,
    VideoSource,
    auto_engine,
    auto_rate,
    engine_command,
    engine_frame,
    engine_lead,
    engine_options,
    engine_rate,
    feed_text,
    ffmpeg_capture_command,
    ffmpeg_command,
    ffmpeg_file_command,
    find_engine,
    find_ffmpeg,
    mbit,
    passes_through,
    rate_text,
    resolve_encoder,
    resolve_feed,
    resolve_profile,
    source_name,
)
from libre_panel.devices.models import PanelModel, orientation_of
from libre_panel.devices.turzx_usb import (
    CMD_BRIGHTNESS,
    CMD_FRAME_RATE,
    CMD_RESTART,
    CMD_STOP_STREAM,
    CMD_UPLOAD_PNG,
    TurzxUsbDisplay,
    UsbTransport,
    brightness_arg,
    encode_frame,
    to_native,
    transparent_layer,
)
from libre_panel.devices.yuv import YuvFrame, block_of
from libre_panel.i18n import t
from libre_panel.render.scene import SceneFrame

log = logging.getLogger(__name__)

CMD_CHUNK_SIZE = 17
CMD_PLAY_LOCAL = 110  # clears the framebuffer; the name goes into the panel's settings in RAM
CMD_STOP_LOCAL = 111
CMD_LOCAL_PLAYING = 112
CMD_H264_BLOCK = 121
CMD_QUEUE_DEPTH = 122

# The panel's own block size; it answers 17 with 0, meaning this default.
DEFAULT_BLOCK = 202752
# The player's rate after power-on. Set again on exit, because the panel's own
# standby clip plays at the last rate set (at 60 it stutters).
POWER_ON_FPS = 30


# The bytes ffmpeg reads for a pixel format (EncoderSettings.pixels), as Pillow packs them:
# rgb0 is RGBA's four bytes, whose fourth ffmpeg leaves aside (as RGB never showed it).
RAW_MODES = {"rgb24": "RGB", "rgb0": "RGBA"}


def frame_bytes(frame: Image.Image, pixels: str = "rgb24") -> bytes:
    """The bytes of an RGB or RGBA ``frame`` as ffmpeg reads ``pixels``, in one piece:
    Image.tobytes makes them in pieces of 64 KB and joins them, a second copy of the picture
    on every frame (4 MB on the 12.3"). As tobytes makes them where Pillow does not take this
    way; a frame of another mode is converted."""
    raw = RAW_MODES[pixels]
    if frame.mode not in ("RGB", "RGBA"):
        return frame.convert(raw).tobytes()
    size = frame.width * frame.height * len(raw)
    try:
        frame.load()
        encoder = Image._getencoder(frame.mode, "raw", (raw,))
        encoder.setimage(frame.im, (0, 0, *frame.size))
        _, done, data = encoder.encode(size)
        if done == 1 and len(data) == size:
            return data
    except Exception:  # (another Pillow)
        pass
    return frame.tobytes("raw", raw)


def rgb_bytes(frame: Image.Image) -> bytes:
    """The RGB bytes of an RGB or RGBA ``frame`` in one piece (:func:`frame_bytes`)."""
    return frame_bytes(frame, "rgb24")


PIPE_CHUNK = 1 << 16  # what Pillow packs at a time on its way into the pipe (a pipe's buffer)


def write_frame(out: Any, frame: Image.Image, pixels: str) -> None:
    """``frame`` into ffmpeg's stdin ``out``, packed as ``pixels``. Run by the encoder's writer
    thread: Pillow packs and writes with the interpreter free for the render loop, through a
    small buffer of its own (no copy of the picture as bytes: 4 to 6 MB a frame on the 12.3",
    and for four bytes a pixel a plain copy of each row). Without a file descriptor or with
    another Pillow: its bytes, written all. An OSError when ffmpeg is gone."""
    try:
        fd = out.fileno()
        frame.load()
        encoder = Image._getencoder(frame.mode, "raw", (RAW_MODES[pixels],))
        encoder.setimage(frame.im, (0, 0, *frame.size))
        to_file = encoder.encode_to_file
    except Exception:  # (nothing written yet: the slower way)
        to_file = None
    if to_file is not None and frame.mode in ("RGB", "RGBA"):
        code = to_file(fd, PIPE_CHUNK)
        if code < 0:
            raise OSError(f"the frame did not go into the pipe (Pillow's code {code})")
        return
    view = memoryview(frame_bytes(frame, pixels))
    while view:  # an unbuffered pipe may take less than asked
        view = view[out.write(view) :]


def _frame_for(
    encoder: Encoder, frame: Image.Image, settings: EncoderSettings
) -> Callable[[Any], None]:
    """``frame`` as ``encoder`` takes it (its own settings, else ``settings``): the job its
    writer thread does (:func:`write_frame`; fed YUV, its YUV made anew where the frame changed,
    as the main loop says in ``frame.info["damage"]``, and written). Frames are not drawn on
    once shown."""
    pixels = (getattr(encoder, "settings", None) or settings).pixels
    if pixels == BOXES:  # the engine: the boxes that may have changed, numbered
        number = getattr(encoder, "numbered", 0)
        encoder.numbered = number + 1
        sender = getattr(encoder, "scenes", None)
        if sender is not None and isinstance(frame, SceneFrame):  # its layers: the engine composes
            return engine_scene(sender, frame, number, frame.info.get("planned"))
        # (and the time it was drawn for: an engine that has the panel sends it on its time)
        return engine_frame(frame, frame.info.get("damage"), number, frame.info.get("planned"))
    made = getattr(encoder, "yuv", None)
    if pixels == YUV and made is not None:
        damage = frame.info.get("damage")  # (taken now: the loop may tag the frame again)

        def made_anew(out: Any) -> None:
            made.update(frame, damage)
            made.write(out)

        return made_anew

    def write(out: Any) -> None:
        write_frame(out, frame, pixels)

    return write


def rotation_of(size: tuple[int, int]) -> int:
    """How ffmpeg turns a frame of ``size`` into the panel's framebuffer (as to_native does): a
    landscape frame a quarter turn clockwise, a portrait one half a turn."""
    return 270 if orientation_of(*size) == "landscape" else 180


def video_settings(video: Any) -> EncoderSettings:
    """The encoder settings of ``[device.video]`` as config.toml has them (the profile and the
    encoder may still be "auto": :func:`resolved`)."""
    return EncoderSettings(
        fps=video.fps,
        crf=video.crf,
        preset=video.preset,
        keyframe_s=video.keyframe_s,
        maxrate=video.maxrate,
        profile=video.profile,
        encoder=video.encoder,
        engine_encoder=getattr(video, "engine_encoder", "auto"),
    )


def resolved(
    settings: EncoderSettings, model_id: str, encoder: str, pixels: str, full: bool = False
) -> EncoderSettings:
    """``settings`` as ffmpeg is started with them: the profile for the panel's model, the
    encoder chosen, the frames fed and converted as ``pixels`` says ("processor", "rgb" or
    "card"; "auto" is the processor's here: the display decides it, :meth:`TurzxVideoDisplay.
    _pixels`), by the card itself only for "card" and one of the card's encoders that can (in
    full range for a ``full`` panel: h264.FULL_RANGE_CARDS), and the video's range."""
    return replace(
        settings,
        profile=resolve_profile(settings.profile, model_id),
        encoder=encoder,
        pixels=resolve_feed(pixels),
        card=pixels == "card" and card_choice.can(encoder, full),
        full=full,
    )


def full_range(video_range: str, model: PanelModel | None) -> bool:
    """Whether a panel gets its video in full range: [device] video_range "full", or "auto" on a
    model whose decoder shows every stream as full range (PanelModel.video_range)."""
    if video_range == "auto":
        return model is not None and model.video_range == "full"
    return video_range == "full"


def lateness_said(counted: dict[str, Any]) -> str:
    """What the engine's counts (``pictures``, ``late``) say of the pictures it sent on their
    time, for the log; and why they went late, where the engine says (``came_late``: made after
    their time, the frame or its encoding late, not the sender; ``late_most_ms``)."""
    late = int(counted.get("late", 0))
    said = f"video: the engine sent {int(counted.get('pictures', 0))} pictures, {late} of them late"
    if late and "came_late" in counted:
        made, most = int(counted.get("came_late", 0)), float(counted.get("late_most_ms", 0))
        said += f" ({made} made after their time; the latest {most:.0f} ms past it)"
    return said


def encoding_said(counted: dict[str, Any]) -> str | None:
    """What the engine's counts say of the time a frame took it, for the log: composing it from
    its layers, its YUV and its encoding (``compose_ms``, ``yuv_ms``, ``x264_ms``: of
    ``encode_ms``, each an average a frame), by x264 or the graphics card (``card``: the card's
    time is mostly waited, not the processor's); None where the engine does not count them."""
    if "x264_ms" not in counted or not counted.get("frames"):
        return None
    parts = (float(counted.get(k, 0)) for k in ("encode_ms", "compose_ms", "yuv_ms", "x264_ms"))
    whole, compose, yuv, x264 = parts
    encoder = "the graphics card's encoder" if counted.get("card") else "x264"
    return (f"video: the engine took {whole:.1f} ms a frame: {compose:.1f} composing it, "
            f"{yuv:.1f} its YUV, {x264:.1f} {encoder}")  # fmt: skip


class DecoderHung(DeviceError):
    """The panel's decoder stopped taking pictures; reconnecting does not help."""


class PanelRestarting(DeviceError):
    """The panel restarts to clear a hung decoder; it is opened again once it is back."""

    retry_s = 1.0  # the main loop looks again soon, not with its growing pauses


class _Restart:
    """A restart of the panel under way, and how many came in a row.

    Kept per process, not per display object: the drivers make a new one each
    time the main loop opens the panel again.
    """

    IN_A_ROW = 3  # then: replug
    RESET_S = 300.0  # this long without a hang and the count starts again
    LEAVE_S, RETURN_S, SETTLE_S = 20.0, 90.0, 2.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.count = 0
        self.last = -1e9
        self.pid: int | None = None  # set while a restart is under way
        self.sent = self.gone = self.back = None

    def allowed(self) -> bool:
        if self.clock() - self.last > self.RESET_S:
            self.count = 0
        return self.count < self.IN_A_ROW

    def begin(self, pid: int) -> None:
        self.count += 1
        self.last = self.sent = self.clock()
        self.pid, self.gone, self.back = pid, None, None

    def wait(self, present: Callable[[int], bool]) -> None:
        """Raise :class:`PanelRestarting` until the panel has left the bus, is back
        and has settled; a :class:`DeviceError` when it does not come back."""
        if self.pid is None:
            return
        now, here = self.clock(), present(self.pid)
        if self.gone is None:
            if here and now - self.sent < self.LEAVE_S:
                raise PanelRestarting(t("the panel restarts to clear its hung video decoder"))
            self.gone = now  # left the bus (or never seemed to: go on after 20 s)
        if self.back is None:
            if not here:
                if now - self.gone < self.RETURN_S:
                    raise PanelRestarting(t("the panel restarts to clear its hung video decoder"))
                self.pid = None
                raise DeviceError(
                    t("the panel did not come back after its restart; replug it, please")
                )
            self.back = now
        if now - self.back < self.SETTLE_S:
            raise PanelRestarting(t("the panel restarts to clear its hung video decoder"))
        self.pid = None  # back and settled: open it as usual


RESTART = _Restart()

# The bit rate "auto" found for a panel (its model, its place on the bus and the picture size),
# kept per process: a display made anew after a reconnect goes on at it instead of climbing
# again, and two panels of one model (each on a link of its own) climb each for itself. The
# next run starts where this one got to (video_state).
RATES: dict[tuple[str, str, tuple[int, int]], RateControl] = {}

# Why the engine could not open a panel, as the log was told (once each a run).
ENGINE_SAID: set[str] = set()


class EngineTrouble:
    """Libre Panel's engine ending unasked (it crashed, or was ended from outside), counted for
    this run: with video.encoder "auto", :attr:`TIMES` of them within :attr:`WITHIN_S` leave
    the engine out until Libre Panel starts again. x264 through ffmpeg encodes then, Libre Panel
    sends the pictures, and the log says why, once. "native" keeps the engine, as asked; a
    failure the engine names itself (the panel's link, a hung decoder) is not counted."""

    TIMES = 3
    WITHIN_S = 600.0

    def __init__(self) -> None:
        self.times: list[float] = []
        self.left_out = False

    def note(self, reason: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.times = [t0 for t0 in self.times if now - t0 < self.WITHIN_S] + [now]
        if len(self.times) >= self.TIMES and not self.left_out:
            self.left_out = True
            log.warning(
                "video: Libre Panel's engine stopped unasked %d times within %d minutes (the "
                "last: %s); x264 through ffmpeg encodes until Libre Panel starts again",
                len(self.times),
                round(self.WITHIN_S / 60),
                reason,
            )


ENGINE_TROUBLE = EngineTrouble()


class VideoSession:
    """Start, picture blocks with flow control, and stop, on one open transport."""

    HIGH, LOW = 2, 1  # queue depth that starts waiting, and the depth to wait for
    POLL_EVERY = 2  # blocks between depth queries
    SLOW_BLOCK_S = 0.02  # a block that takes longer than a frame at 50 fps (healthy: ~1 ms)
    WAIT_S, GIVE_UP_S = 0.03, 1.5
    # Hang detection (SPUR II): a healthy block takes about 1 ms. When the median
    # of the last 40 exceeds 0.2 s, four depth readings 150 ms apart decide.
    HANG_WINDOW, HANG_MEDIAN_S = 40, 0.2
    HANG_READINGS, HANG_READING_GAP_S, HANG_DEPTH, HANG_SPREAD = 4, 0.15, 20, 2

    def __init__(
        self,
        transport: UsbTransport,
        native_size: tuple[int, int],
        device_fps: int,
        brightness: int,
        local_clip: str,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
        counters: Counters | None = None,
    ) -> None:
        self.transport = transport
        self.native_size = native_size
        self.device_fps = device_fps
        self.brightness = brightness
        self.local_clip = local_clip
        self.sleep, self.clock = sleep, clock
        self.block_size = DEFAULT_BLOCK
        self.blocks = self.bytes = self.waits = self.max_depth = 0  # of this session
        self.counters = counters or Counters()  # of the whole run, shared with the display
        self.send_times: deque[float] = deque(maxlen=self.HANG_WINDOW)

    def start(self) -> None:
        name = self.local_clip.encode("utf-8")
        if not name:
            # An empty name would erase the panel's standby clip setting.
            raise DeviceError(t("video.local_clip is not set (see docs/CONFIGURATION.md, Video)"))
        command = self.transport.command
        command(CMD_PLAY_LOCAL, struct.pack(">I", len(name)) + bytes(4) + name)
        command(CMD_STOP_LOCAL)
        command(CMD_LOCAL_PLAYING)
        command(CMD_BRIGHTNESS, bytes([brightness_arg(self.brightness)]))
        layer = transparent_layer(self.native_size)
        command(CMD_UPLOAD_PNG, struct.pack(">I", len(layer)), layer, timeout_ms=5000)
        command(CMD_FRAME_RATE, bytes([self.device_fps]))
        reply = command(CMD_CHUNK_SIZE)
        size = int.from_bytes(reply[8:12], "big") if len(reply) >= 12 else 0
        if 0 < size <= 1024 * 1024:
            self.block_size = size

    def send(self, picture: bytes) -> None:
        """One picture; pictures larger than a block (rare keyframes) take several."""
        for start in range(0, len(picture), self.block_size):
            block = picture[start : start + self.block_size]
            began = self.clock()
            self.transport.command(
                CMD_H264_BLOCK, struct.pack(">I", len(block)), block, timeout_ms=5000
            )
            took = self.clock() - began
            self.send_times.append(took)
            self.blocks += 1
            self.bytes += len(block)
            self.counters.blocks += 1
            self.counters.bytes += len(block)
            if took > self.SLOW_BLOCK_S:
                self.counters.slow_blocks += 1
            if self.blocks % self.POLL_EVERY == 0:
                depth = self.depth()
                if depth is not None and depth > self.HIGH:
                    self._wait_for_room()
            self.check_hang()

    def depth(self) -> int | None:
        reply = self.transport.query(CMD_QUEUE_DEPTH)
        if reply is None or len(reply) <= 8:
            return None
        self.max_depth = max(self.max_depth, reply[8])
        self.counters.max_depth = max(self.counters.max_depth, reply[8])
        return reply[8]

    def _wait_for_room(self) -> None:
        self.waits += 1
        self.counters.waits += 1
        started = self.clock()
        while self.clock() - started < self.GIVE_UP_S:
            self.sleep(self.WAIT_S)
            depth = self.depth()
            if depth is None or depth <= self.LOW:
                return
        log.debug("the panel's video queue did not go down within %.1f s", self.GIVE_UP_S)

    def check_hang(self) -> None:
        """Raise :class:`DecoderHung` when blocks go slowly *and* the queue stands high
        without moving. The median, not one slow block: waiting for room legitimately
        makes single sends slow, and a high queue that moves drains by itself."""
        times = self.send_times
        if len(times) < self.HANG_WINDOW or statistics.median(times) <= self.HANG_MEDIAN_S:
            return
        times.clear()  # decide once per window
        readings = []
        for i in range(self.HANG_READINGS):
            if i:
                self.sleep(self.HANG_READING_GAP_S)
            depth = self.depth()
            if depth is not None:
                readings.append(depth)
        log.warning("video blocks go slowly; queue depth %s", readings)
        if (
            len(readings) >= 2
            and max(readings) > self.HANG_DEPTH
            and max(readings) - min(readings) <= self.HANG_SPREAD
        ):
            raise DecoderHung(
                t(
                    "the panel's video decoder hangs (queue stuck at {depth}). "
                    "Unplug the panel and plug it in again.",
                    depth=max(readings),
                )
            )

    def stop(self, last_frame: bytes | None = None) -> None:
        """End the stream; ``last_frame`` (a native PNG) stays on the panel."""
        command = self.transport.command
        command(CMD_STOP_STREAM)
        command(CMD_FRAME_RATE, bytes([POWER_ON_FPS]))
        if last_frame:
            command(CMD_UPLOAD_PNG, struct.pack(">I", len(last_frame)), last_frame, timeout_ms=5000)


class TurzxVideoDisplay(TurzxUsbDisplay):
    """Every frame goes through ffmpeg into the panel's H.264 decoder.

    Three threads keep the render loop from ever waiting on USB: ffmpeg's
    writer and reader (see :class:`~libre_panel.devices.h264.Encoder`) and a
    sender for the 121 blocks. When the panel is slow, the small queue
    between reader and sender fills and ffmpeg's pipe slows the render loop
    down: fewer frames, never torn ones. A stopped ffmpeg is restarted; a USB
    error reconnects and starts the video again. A planned change of ffmpeg (a
    step of the bit rate) starts the new one beside the old one, whose pictures
    the panel shows until the new one has its first (:meth:`_switch`).
    """

    name = "turzx-video"
    streaming = True
    RESTARTS_PER_MINUTE = 3
    STALL_S = 2.0  # ffmpeg taking no frames this long, while the panel waits for pictures
    START_S = 15.0  # the same until its first picture: a cold start (virus scanner, busy PC)
    SWITCH_S = 3.0  # a planned switch: this long for the new ffmpeg's first picture
    SWITCH_HOLD_S = 60.0  # a switch that did not come about waits this long to be tried again
    # YUV made here (devices/yuv.py): a share of the frame changing on average (over about a
    # second: each frame weighs MADE_EASE) above which ffmpeg converts the frames itself, and
    # below which, for MADE_CALM_S, YUV is made here again
    MADE_EASE = 0.1
    MADE_BUSY = 0.5
    MADE_CALM = 0.15
    MADE_CALM_S = 5.0
    RATE_KEPT_S = 10.0  # the rate "auto" found goes into video-state.json at most this often

    def __init__(self, config, model: PanelModel | None = None) -> None:
        super().__init__(config, model)
        self.video = config.video
        self.stream_fps = self.video.fps
        self.settings = video_settings(self.video)
        self.session: VideoSession | None = None
        self.encoder: Encoder | None = None
        self.rate: RateControl | None = None  # of maxrate "auto", once the picture size is known
        self._encoders_failed: set[str] = set()  # of "auto": one that stopped before a picture
        # an encoder of the card that ended before a picture converting the frames itself
        # ("card"): ffmpeg converts for it (kept a week in video-state.json, so that the next
        # connection starts so at once)
        self._feeds_failed: set[str] = set()
        self._feeds_asked: set[str] = set()  # the encoders video-state.json was asked about
        self._encoder_said = ""  # the encoder the log was told of
        self._engine_missing_said = False  # (once: video.encoder "native" without the engine)
        self._cards_missing_said = False  # (once: engine_encoder "card", an engine without it)
        # The engine, while it has the panel (devices/engine.py, video.engine_sends): it sends
        # the commands and the pictures; what it counted so far is in the counters
        self.link: EngineLink | None = None
        self._engine_counted: dict[str, int] = {}
        self._ffmpeg: str | None = None
        self._pictures: queue.Queue[bytes] = queue.Queue(maxsize=2)
        self._sender: threading.Thread | None = None
        self._stop_sender = threading.Event()
        self._error: Exception | None = None
        self._generation = 0  # the stream whose pictures go to the panel
        self._streams = 0  # the last generation given to an encoder
        # A switch under way: the old ffmpeg, which the panel shows until the new one (encoder)
        # has its first picture, and the new one's generation until then. The stream a switch
        # ended, whose last pictures are no drops. The lock decides who comes first: the new
        # one's first picture or the end of its time.
        self._leaving: Encoder | None = None
        self._coming = self._quiet = 0
        self._switch_began = self._switch_held = 0.0
        self._handing = threading.Lock()
        self._rate_key: tuple[str, str, tuple[int, int]] | None = None
        self._rate_kept, self._rate_kept_at = 0.0, -1e9  # what video-state.json has, and when
        self._restarts: list[float] = []
        self._last_frame: Image.Image | None = None
        self._layers_said = False  # (the log said that the engine composes the frames)
        # YUV made here where the frames change (devices/yuv.py), while they change little (None:
        # not known yet; frames that do not say what changed in them never): how much of a
        # frame changes, on average over the last ones, and since when it is calm
        self._made_here: bool | None = None
        self._busy = 0.0
        self._calm_since: float | None = None
        # [video] direct: the videos ffmpeg could not play from their file (frames for them)
        self._sources_failed: set[VideoSource] = set()

    # -- connection ----------------------------------------------------------

    def open(self) -> None:
        self._ffmpeg = self._ffmpeg or find_ffmpeg(self.video.ffmpeg)  # before touching the panel
        restarting = RESTART.pid is not None
        RESTART.wait(UsbTransport.present)
        super().open()
        try:
            self._start_video()
        except Exception:
            self.close()
            raise
        self.restarted = restarting  # the main loop tells the services (panel-restarted)
        if restarting:
            log.info("the panel is back after its restart")

    def describe(self) -> str:
        return t("{panel}, video {fps} fps", panel=super().describe(), fps=self.stream_fps)

    @property
    def slack(self) -> float | None:
        """While the engine sends the pictures: its lead, within which a frame drawn late still
        goes on its time, so that the main loop catches up rather than skipping a beat."""
        return getattr(self.encoder, "lead", None) if self.link is not None else None

    @property
    def takes_scenes(self) -> bool:
        """Whether a frame may come as its layers (render/scene.py): the engine composes it
        (video.engine_layers), with no other encoder to feed meanwhile."""
        encoder = self.encoder
        return (
            self._leaving is None
            and encoder is not None
            and getattr(encoder, "scenes", None) is not None
            and encoder.alive
            and self.feed is None
            and self.range is None
        )

    @property
    def makes_rings(self) -> bool:
        """Whether the engine that composes the frames also makes a lit gauge's ring itself
        for each value (render/lit.py's Ring): the renderer leaves their pictures to it."""
        sender = getattr(self.encoder, "scenes", None)
        return self.takes_scenes and bool(getattr(sender, "rings", False))

    @property
    def makes_masks(self) -> bool:
        """Whether the engine that composes the frames also makes a picture of a mask and an ink
        itself (render/scene.py's MaskRecipe): the renderer keeps texts and letters as masks."""
        sender = getattr(self.encoder, "scenes", None)
        return self.takes_scenes and bool(getattr(sender, "masks", False))

    @property
    def makes_fans(self) -> bool:
        """Whether the engine that composes the frames also makes a fan's rotor at each position
        and its ring itself (render/fan.py's recipes): the renderer leaves their pictures to it."""
        sender = getattr(self.encoder, "scenes", None)
        return self.takes_scenes and bool(getattr(sender, "fans", False))

    def shown_picture(self, timeout: float = 1.0) -> Image.Image | None:
        """The frame the panel shows as the engine composed it from its layers, whole and
        before it turns (the app's picture of the panel, its pixels not made here); None where
        the engine gives none: it does not compose the frames, or the frame it has is not one
        of layers (EngineLink.shown)."""
        link = self.link
        if link is None or not getattr(self.encoder, "shown", False) or not self.takes_scenes:
            return None
        return link.shown(timeout)

    def _as_scene(self, frame: Image.Image) -> bool:
        """Whether ``frame`` goes to the encoder as its layers, its pixels not made here."""
        return isinstance(frame, SceneFrame) and self.takes_scenes

    def _auto_engine(self) -> str:
        """The engine video.encoder "auto" takes: the one the download carries (h264.auto_engine),
        unless it stopped before its first picture here or unasked too often this run
        (:class:`EngineTrouble`); "" for every other encoder."""
        if (
            self.settings.encoder != "auto"
            or NATIVE in self._encoders_failed
            or ENGINE_TROUBLE.left_out
        ):
            return ""
        return auto_engine()

    def _sending_engine(self) -> str:
        """The engine that is to have the panel and send it the pictures, each on its time
        (stage 1b): with video.encoder "native" (or "auto", where it takes the engine:
        :meth:`_auto_engine`) and video.engine_sends; "" where Python sends them (an engine of
        stage 1a encodes only)."""
        if not getattr(self.video, "engine_sends", True):
            return ""
        if self.settings.encoder == NATIVE:
            engine = find_engine()
        else:
            engine = self._auto_engine()
        return engine if engine and can_send(engine) else ""

    def _open_transport(self, pid: int | None, beside: tuple[str, ...]) -> Any:
        """The panel's link: the engine's where it sends the pictures (:meth:`_sending_engine`),
        else pyusb's, as for every other encoder. An engine that cannot open the panel pyusb
        found leaves it to pyusb and says why, once."""
        self.link = None
        engine = self._sending_engine()
        if engine:
            try:
                transport = EngineTransport.open(engine, pid, beside)
            except EngineFailed as exc:
                if str(exc) not in ENGINE_SAID:  # (once a run: a display is made anew often)
                    ENGINE_SAID.add(str(exc))
                    log.warning(
                        "video: Libre Panel's engine could not open the panel (%s); "
                        "Libre Panel sends the pictures itself",
                        exc,
                    )
            else:
                link = self.link = transport.link
                self._engine_counted = {}
                # (bound to this link: what an earlier engine says late is not this one's)
                link.on_failed = functools.partial(self._engine_failed, link)
                link.on_counts = functools.partial(self._engine_counts, link)
                log.info("video: the engine has the panel (%s) and sends it the pictures, "
                         "each on its time", self.link.place)  # fmt: skip
                return transport
        return super()._open_transport(pid, beside)

    def _engine_failed(self, link: EngineLink, error: DeviceError) -> None:
        """The engine's video path stopped (said in its reader thread): as the sender's error
        would be, the next frame reconnects, or restarts a hung panel."""
        if link is not self.link or self._error is not None:
            return  # (an engine of before, or the failure is known already)
        if link.ended_unasked:  # (it crashed, or was ended from outside: not the panel)
            ENGINE_TROUBLE.note(str(error))
        if isinstance(error, EngineHung):
            depth = str(error)
            self._error = DecoderHung(
                t(
                    "the panel's video decoder hangs (queue stuck at {depth}). "
                    "Unplug the panel and plug it in again.",
                    depth=int(depth) if depth.isdigit() else depth,
                )
            )
            return
        self._error = error
        self.counters.usb_errors += 1

    def _engine_lost(self) -> bool:
        """Whether the engine that has the panel ended unasked (it crashed, or was ended from
        outside): then that is the error the next frame heals, a new engine, as for a link that
        failed. Asked by each frame: its reader may say so only a moment later."""
        link = self.link
        if link is None or link.alive:
            return False
        if self._error is None:
            if link.failed is None or link.ended_unasked:  # (it said no why: not the panel)
                ENGINE_TROUBLE.note(link.reason())
            self._error = link.failed or DeviceError(
                f"Libre Panel's engine stopped ({link.reason()})"
            )
            if not isinstance(self._error, DecoderHung):
                self.counters.usb_errors += 1
        return True

    def _engine_counts(self, link: EngineLink, counts: dict[str, float]) -> None:
        """What the engine counted, added to the run's counters (the bit rate "auto" judges
        its waits, blocks and slow blocks, RateControl.judge)."""
        if link is not self.link:
            return  # (the last counts of an engine of before)
        for name in ("blocks", "bytes", "waits", "slow_blocks", "drops"):
            value = int(counts.get(name, 0))
            before = self._engine_counted.get(name, 0)
            if value > before:
                setattr(self.counters, name, getattr(self.counters, name) + value - before)
                self._engine_counted[name] = value
        self.counters.max_depth = max(self.counters.max_depth, int(counts.get("max_depth", 0)))

    def _start_video(self) -> None:
        native = (self.model.native_width, self.model.native_height) if self.model else (480, 1920)
        self.session = VideoSession(
            self.transport,
            native,
            self.video.device_fps,
            self.brightness if self.brightness is not None else self.config.brightness,
            self.video.local_clip,
            counters=self.counters,
        )
        self.session.start()
        self._error = None
        self._stop_sender = threading.Event()
        if self.link is not None:
            self._sender = None  # (the engine sends the pictures)
            return
        self._sender = threading.Thread(
            target=self._send_loop,
            args=(self.session, self._stop_sender),
            name="panel-video",
            daemon=True,
        )
        self._sender.start()

    def _stop_video(self) -> None:
        self._stop_encoder()
        self._stop_sender.set()
        if self._sender is not None:
            self._sender.join(10)
            self._sender = None
        self._discard_pictures()

    def _discard_pictures(self) -> None:
        """Throw away the pictures waiting for the sender: those of a stream that ended."""
        while True:
            try:
                self._pictures.get_nowait()
            except queue.Empty:
                return
            self.counters.drops += 1

    def _send_loop(self, session: VideoSession, stop: threading.Event) -> None:
        timing.raise_priority()  # a busy PC must not starve the panel (SPUR II)
        while not stop.is_set():
            try:
                generation, picture = self._pictures.get(timeout=0.2)
            except queue.Empty:
                continue
            if generation == self._quiet:  # a switch's old stream: none after the new one's first
                continue
            try:
                session.send(picture)
            except DeviceError as exc:
                self._error = exc
                if not isinstance(exc, DecoderHung):  # that is not the link
                    self.counters.usb_errors += 1
                return

    # -- encoder ---------------------------------------------------------------

    def _start_encoder(
        self,
        size: tuple[int, int],
        beside: bool = False,
        source: VideoSource | CaptureSource | None = None,
    ) -> None:
        """A new ffmpeg for pictures of ``size``; ``beside``: next to the one running now,
        which goes on until the new one has its first picture (:meth:`_switch`); ``source``:
        a video it reads from its file, not frames (:meth:`show_video`)."""
        if not beside:
            self._stop_encoder()
        rotate = rotation_of(size)
        model_id = self.model.id if self.model else ""
        drawn = source is None and self.feed is None and self.range is None
        auto = self._auto_engine() if drawn else ""  # ("auto": the download's own engine)
        name = resolve_encoder(
            self.settings.encoder, self._ffmpeg, self._encoders_failed, engine=bool(auto)
        )
        engine = ""
        if name == NATIVE:  # Libre Panel's own engine, for the frames it draws (h264.NATIVE)
            engine = (auto or find_engine()) if drawn else ""
            if not engine and drawn and not self._engine_missing_said:
                self._engine_missing_said = True
                log.warning(
                    'video: video.encoder is "native" but Libre Panel\'s engine '
                    "(libre-panel-engine) is not here; x264 through ffmpeg encodes"
                )
            name = NATIVE if engine else "libx264"  # (a video, the screen, a check: ffmpeg)
        self._ask_refused(name)
        full = self._full()
        fed = source is None  # (frames that the card might convert)
        if fed and self.video.pixels == "auto" and self.feed is None and self.range is None:
            self._ask_the_card(name, full, size, rotate, model_id)  # (not for a check's A/B)
        settings = resolved(self.settings, model_id, name, self._pixels(name), full)
        if engine:  # (it is fed the boxes that may have changed and makes the YUV itself)
            settings = replace(settings, pixels=BOXES, card=False)
            if settings.engine_encoder == "card" and not encodes_on_cards(engine):
                settings = replace(settings, engine_encoder="x264")
                if not self._cards_missing_said:
                    self._cards_missing_said = True
                    log.info("video: this engine has no graphics card's encoder (one of "
                             "before); x264 in the engine encodes")  # fmt: skip
            elif settings.engine_encoder == "auto" and (
                sys.platform != "win32" or not encodes_on_proven_cards(engine)
            ):  # (x264 as before: elsewhere than on Windows, and in an engine of before)
                settings = replace(settings, engine_encoder="x264")
        elif self._makes_yuv(settings, size, source):
            settings = replace(settings, pixels=YUV)
        said = f"{feed_text(settings)}, profile {settings.profile}"
        if engine and settings.engine_encoder == "card":
            said += ", the graphics card's encoder where it opens"
        elif engine and settings.engine_encoder == "auto":
            said += ", the graphics card's encoder where it is AMD's or NVIDIA's"
        said += ", full range" if settings.full else ", limited range"
        if source is not None:
            said += f", straight from {source_name(source)}"
        if said != self._encoder_said:
            self._encoder_said = said
            log.info("video: encoding with %s", said)
        if settings.maxrate == "auto":
            transport = self.transport  # (None in a test that starts the encoder alone)
            place = str(getattr(transport, "place", None) or getattr(transport, "pid", "") or "")
            key = (self.model.id if self.model else "", place, size)
            self.rate = RATES.get(key)
            if self.rate is None:  # where the last run got to, if it is known
                start = auto_rate(settings, size)
                self.rate = RATES[key] = RateControl(start, video_state.rate(key))
            if key != self._rate_key:
                self._rate_key, self._rate_kept = key, self.rate.rate
            settings = replace(settings, maxrate=rate_text(self.rate.rate))
        fed: dict[str, Any] = {}
        if isinstance(source, CaptureSource):  # the screen (mirror.py)
            command = ffmpeg_capture_command(self._ffmpeg, source, size, rotate, settings)
            if source.feeder:  # (Wayland: a program hands ffmpeg the raw frames)
                fed["popen"] = functools.partial(Fed, source.feeder, source.fds)
        elif source is not None:
            command = ffmpeg_file_command(self._ffmpeg, source, size, rotate, settings)
        elif engine:
            command = engine_command(engine, size, rotate, settings)
        else:
            command = ffmpeg_command(self._ffmpeg, size, rotate, settings)
        self._streams += 1
        generation = self._streams
        raised = False
        link = self.link
        block = self.session.block_size if self.session is not None else DEFAULT_BLOCK

        def deliver(picture: bytes) -> None:
            nonlocal raised
            if not raised:  # ffmpeg's reader thread, forwarding the pictures: above normal too
                raised = True
                timing.raise_priority()
            if generation == self._coming:  # a switch: the new stream's first picture
                with self._handing:
                    if generation == self._coming:  # (not given up meanwhile)
                        self._quiet, self._generation = self._generation, generation
                        self._coming = 0
            if link is not None:  # the engine has the panel: it sends ffmpeg's pictures too
                if generation == self._generation and self._error is None:
                    try:
                        link.picture(picture, block)
                        return
                    except (OSError, ValueError):
                        pass  # (the engine is gone: its reader says so)
                if generation != self._quiet:
                    self.counters.drops += 1
                return
            while generation == self._generation and self._error is None:
                try:
                    self._pictures.put((generation, picture), timeout=0.2)
                    return
                except queue.Full:
                    continue
            if generation != self._quiet:  # what a switch leaves behind is no loss: the other shows
                self.counters.drops += 1  # the stream was replaced, or the panel failed meanwhile

        if engine and link is not None:  # (it encodes and sends: no process of its own)
            options = engine_options(size, rotate, settings)
            # frames of layers go to it as them, where it composes them (stage 2)
            layers = getattr(self.video, "engine_layers", True) and takes_scenes(engine)
            rings = layers and makes_rings(engine)
            encoder = EngineStream(
                link, size, options, block, engine_lead(settings.fps), scenes=layers,
                rings=rings, views=layers and cuts_views(engine),
                globes=layers and makes_globes(engine), fills=layers and makes_fills(engine),
                laid=layers and lays_laid(engine), grown=layers and grows_sources(engine),
                bars=layers and makes_bars(engine), fans=layers and makes_fans(engine),
                masks=layers and makes_masks(engine), shown=layers and gives_shown(engine),
            )  # fmt: skip
            if layers and not self._layers_said:  # (once: a new stream starts as often)
                self._layers_said = True
                log.info("video: the engine composes the frames from their layers%s",
                         ", and makes the lit gauges' rings" if rings else "")  # fmt: skip
        else:
            encoder = Encoder(command, size, deliver, **fed)
        encoder.name, encoder.maxrate = settings.encoder, settings.maxrate
        encoder.settings = settings  # (what it is fed: _feed gives each its own)
        # (fed YUV: the YUV it is fed, made where the frames change; its first frame whole)
        encoder.yuv = YuvFrame(size, rotate, settings.full) if settings.pixels == YUV else None
        encoder.source = source  # (a video from its file: it is fed nothing)
        if beside:
            self._leaving, self._coming = self.encoder, generation
            self._switch_began = time.monotonic()
        else:
            self._generation = generation
        self.encoder = encoder
        self.counters.bit_rate = mbit(settings.maxrate)
        self.counters.encoder = feed_text(settings)

    def _makes_yuv(
        self, settings: EncoderSettings, size: tuple[int, int],
        source: VideoSource | CaptureSource | None,
    ) -> bool:  # fmt: skip
        """Whether ffmpeg is fed YUV made here (devices/yuv.py) rather than the frames' pixels:
        where the processor converts them (x264, or a card's encoder that does not convert
        them itself: ffmpeg turned and converted each whole frame, a quarter of a core for Arena
        on the 12.3" at 24 frames, only to hand the card NV12), while the frames change little
        (:meth:`_follow_the_frames`) and no colour check compares the ways of converting."""
        return (
            source is None
            and self._made_here is True
            and (settings.encoder == "libx264" or settings.encoder in CARD_ENCODERS)
            and settings.pixels == "rgb0"
            and not settings.card
            and self.feed is None
            and self.range is None
            and block_of(size) is not None
        )

    def _follow_the_frames(self, frame: Image.Image) -> None:
        """While the frames change little (Lumen's 12.3": four in a hundred of its blocks),
        ffmpeg is fed YUV made where they changed; where much changes most frames (a video, the
        screen as a monitor, the Player), ffmpeg converts them as before,
        which costs less then: a new ffmpeg beside the one running takes over (:meth:`_switch`).
        Back after a calm while."""
        if "damage" not in frame.info:
            return  # (frames that do not say what changed: ffmpeg converts them, as before)
        damage = frame.info.get("damage")
        area = frame.width * frame.height
        share = 1.0 if damage is None else min(
            1.0, sum((b[2] - b[0]) * (b[3] - b[1]) for b in damage) / max(1, area)
        )  # fmt: skip
        self._busy += (share - self._busy) * self.MADE_EASE
        now = time.monotonic()
        if self._made_here and self._busy > self.MADE_BUSY:
            self._made_here, self._calm_since = False, None
        elif not self._made_here:
            if self._busy >= self.MADE_CALM:
                self._calm_since = None
            elif self._calm_since is None:
                self._calm_since = now
            elif now - self._calm_since >= self.MADE_CALM_S:
                self._made_here = True
        encoder = self.encoder
        if encoder is None or self._leaving is not None or not encoder.alive:
            return
        settings = getattr(encoder, "settings", None)
        if settings is None or getattr(encoder, "source", None) is not None:
            return
        fed_yuv = settings.pixels == YUV
        fed = "rgb0" if fed_yuv else settings.pixels  # (what a new ffmpeg would be fed instead)
        wanted = self._makes_yuv(replace(settings, pixels=fed), encoder.size, None)
        if fed_yuv != wanted and now >= self._switch_held:
            log.info(
                "video: %s",
                "the frames change little: YUV made here" if wanted
                else "much changes in every frame: ffmpeg converts the frames",
            )  # fmt: skip
            self._switch()
            coming = getattr(self.encoder, "settings", None)
            if coming is not None and (coming.pixels == YUV) != wanted:
                # (the new ffmpeg is fed as the old one was: not again with every frame)
                self._switch_held = now + self.SWITCH_HOLD_S

    def _pixels(self, name: str) -> str:
        """Who converts the frames' colours for the encoder ``name``: "card", "processor", or
        "rgb" (the processor, fed three bytes a pixel). As a colour check shows them on the
        panel (:attr:`feed`), else as video.pixels says: "auto" the card where a colour check
        found it converting as the processor does (card_choice.py), else the processor. The
        card only for one of its encoders that has not given up converting the frames itself
        and, for a panel in full range, that makes full range (else the processor makes it)."""
        wanted = self.feed or self.video.pixels
        full = self._full()
        able = card_choice.can(name, full) and name not in self._feeds_failed
        if wanted == "auto":
            return "card" if able and card_choice.chosen(self._ffmpeg, name, full) else "processor"
        if wanted == "card" and not able:
            return "processor"
        return wanted

    def _ask_the_card(
        self, name: str, full: bool, size: tuple[int, int], rotate: int, model_id: str
    ) -> None:
        """For "auto": the background check of whether the card converts for ``name`` as the
        processor does, where there is no verdict for it yet (card_choice.ensure); its settings
        are this display's."""
        if self._ffmpeg is None or not card_choice.can(name, full) or name in self._feeds_failed:
            return
        base = self.settings

        def settings(encoder: str, pixels: str) -> EncoderSettings:
            return resolved(base, model_id, encoder, pixels, full)

        card_choice.ensure(self._ffmpeg, name, full, settings, size, rotate)

    def _full(self) -> bool:
        """Whether this panel gets its video in full range: as a colour check compares it
        (:attr:`range`), else as [device] video_range says for this model (:func:`full_range`)."""
        return full_range(self.range or self.config.video_range, self.model)

    def _ask_refused(self, name: str) -> None:
        """Once per encoder of the card: whether it gave nothing converting four bytes a pixel
        itself within the week (video-state.json), so that this connection starts with the
        processor's conversion at once where "card" is asked for."""
        if name not in h264.CARD_ENCODERS or name in self._feeds_asked or self._ffmpeg is None:
            return
        self._feeds_asked.add(name)
        if name not in self._feeds_failed and video_state.refused(self._ffmpeg, name):
            self._feeds_failed.add(name)
            log.info(
                "video: %s gave no picture converting the frames itself lately "
                "(video-state.json); ffmpeg converts them for it",
                name,
            )

    def _feed_changed(self) -> bool:
        """Whether the frames are to go into ffmpeg otherwise than the running one takes them
        (video.pixels or video_range changed, or a colour check shows its other way): a new
        ffmpeg then."""
        encoder = self.encoder
        settings = getattr(encoder, "settings", None)
        if encoder is None or settings is None:
            return False
        wanted = self._pixels(settings.encoder)
        if settings.full != self._full():
            return True
        if settings.pixels == BOXES:  # (the engine converts as the processor does, always)
            return self.feed is not None or self.range is not None
        fed = "rgb0" if settings.pixels == YUV else settings.pixels  # (YUV: _follow_the_frames)
        if settings.pixels == YUV and (self.feed is not None or self.range is not None):
            return True  # (a colour check compares the ways of converting: ffmpeg converts)
        return fed != resolve_feed(wanted) or settings.card != (wanted == "card")

    def _stop_encoder(self) -> None:
        if self.encoder is not None:
            with self._handing:
                self._generation = self._coming = 0  # pictures of the old streams are dropped
            for encoder in (self._leaving, self.encoder):  # (both during a switch)
                if encoder is not None:
                    encoder.close()
            self.encoder = self._leaving = None
            self._discard_pictures()  # the new stream starts with a keyframe
            if self.link is not None and self.link.alive:  # (the engine's, of ffmpeg's too)
                try:
                    self.link.end()
                except (OSError, ValueError):
                    pass

    def _follow_the_panel(self) -> None:
        """``maxrate`` "auto": a step down while the panel takes less than it is sent, a step
        up again after a calm minute (:class:`RateControl`). ffmpeg cannot change its rate on
        the way, so a change starts a new one beside it (:meth:`_switch`)."""
        control, encoder = self.rate, self.encoder
        if control is None or encoder is None:
            return
        now = time.monotonic()
        before = control.rate
        rate = control.judge(self.counters, now)
        if rate is None:
            self._floor_said()
        else:
            log.info(
                "video: bit rate %s -> %s (%s)",
                rate_text(before),
                rate_text(rate),
                "the panel takes less than it is sent" if rate < before else "calm for a minute",
            )
        self._keep_rate(now)
        due = rate_text(control.rate) != encoder.maxrate and now >= self._switch_held
        if due and encoder.alive and getattr(encoder, "name", "") == NATIVE:
            self._new_rate(encoder, control.rate)  # (into the running stream: no switch)
        elif due and self._leaving is None and encoder.alive:
            self._switch()

    def _new_rate(self, encoder: Encoder, rate: float) -> None:
        """The engine's bit rate, changed in the stream it makes (h264.engine_rate): x264 goes
        on with the new cap, no new encoder and no keyframe, nothing for the panel to wait for."""
        try:
            encoder.write(engine_rate(max(1, round(rate * 1000))))
        except EncoderStopped:
            return  # (the next frame starts it anew, at the new rate)
        encoder.maxrate = rate_text(rate)
        encoder.settings = replace(encoder.settings, maxrate=encoder.maxrate)
        self.counters.bit_rate = rate

    def _switch(self) -> None:
        """A planned change of ffmpeg (a step of the bit rate) without a still picture: the
        new ffmpeg starts beside the old one, both get the frames, and the panel shows the old
        one's pictures until the new one has its first (:meth:`_hand_over`). Starting ffmpeg
        anew instead leaves the picture standing still for its start and the first keyframe."""
        try:
            source = getattr(self.encoder, "source", None)  # (a video from its file goes on so)
            self._start_encoder(self.encoder.size, beside=True, source=source)
        except DeviceError as exc:
            self._switch_held = time.monotonic() + self.SWITCH_HOLD_S
            log.warning("video: no new ffmpeg (%s); the old one goes on for a minute", exc)

    def _hand_over(self) -> None:
        """A switch under way: done once the new ffmpeg has its first picture on the way to
        the panel (the old one is closed then; the pictures it still had are not counted as
        dropped). When the new one has none within :attr:`SWITCH_S`, or stopped, it is closed
        and the old one goes on; the switch is tried again after :attr:`SWITCH_HOLD_S`."""
        leaving, coming = self._leaving, self.encoder
        if leaving is None:
            return
        waited = time.monotonic() - self._switch_began
        if not coming.pictures and coming.alive and waited < self.SWITCH_S:
            return
        with self._handing:
            taken = self._coming == 0  # its first picture is on its way to the panel
            if not taken:
                if coming.pictures:
                    return  # it is being handed on just now
                self._quiet, self._coming = self._coming, 0
        self._leaving = None
        if taken:
            leaving.close()
            return
        log.warning(
            "video: the new ffmpeg had no picture after %.1f s (%s); the old one goes on, "
            "and the switch waits a minute",
            waited,
            coming.reason(),
        )
        self._switch_held = time.monotonic() + self.SWITCH_HOLD_S
        self.encoder, self.counters.bit_rate = leaving, mbit(leaving.maxrate)
        if getattr(leaving, "settings", None) is not None:
            self.counters.encoder = feed_text(leaving.settings)
        coming.close()

    def _keep_rate(self, now: float, at_once: bool = False) -> None:
        """The bit rate "auto" got to, for the next run (video-state.json): written when it
        changed, at most every :attr:`RATE_KEPT_S`, and when the panel is closed."""
        control = self.rate
        if control is None or self._rate_key is None or control.rate == self._rate_kept:
            return
        if not at_once and now - self._rate_kept_at < self.RATE_KEPT_S:
            return
        video_state.keep_rate(self._rate_key, control.rate)
        self._rate_kept, self._rate_kept_at = control.rate, now

    def _floor_said(self) -> None:
        """Once: the panel cannot keep up at the lowest rate, so it is the pictures."""
        control = self.rate
        if control is not None and control.floored and not getattr(self, "_floor_told", False):
            self._floor_told = True
            log.warning(
                "video: the panel takes fewer pictures than it is sent at %s already; fewer "
                "frames a second (video.fps) would help, fewer bits would not",
                rate_text(control.rate),
            )

    # -- frames ------------------------------------------------------------------

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.transport is None or self.session is None:
            raise DeviceError("panel not open")
        self._engine_lost()
        if isinstance(self._error, DecoderHung):
            self._restart_panel(self._error)  # a new connection does not help
        if self._error is not None:
            self._heal(self._error)
        if self.feed is None and self.range is None:  # (a colour check's picture does not stay)
            self._last_frame = frame
        if self._made_here is None:  # (the main loop's frames say what changed: Renderer.damage)
            self._made_here = "damage" in frame.info
        self._hand_over()
        self._follow_the_panel()
        encoder = self._leaving or self.encoder  # during a switch: the one the panel shows
        if encoder is not None and getattr(encoder, "source", None) is not None:
            log.info("video: frames again, after the video from its file")
            self._start_encoder(frame.size)  # (the theme shows more than the video now)
        elif (
            encoder is not None
            and encoder.alive
            and encoder.size == frame.size
            and self._feed_changed()
        ):
            log.debug("video: the frames go in otherwise now; ffmpeg starts again")
            self._start_encoder(frame.size)  # (the panel stays open: a new ffmpeg)
        elif encoder is None or encoder.size != frame.size or not encoder.alive:
            if encoder is not None and encoder.size == frame.size:
                self._stopped(encoder.reason())
            self._start_encoder(frame.size)
        if not self._as_scene(frame):  # (a frame of layers says nothing of what changed)
            self._follow_the_frames(frame)
        try:
            self._feed(frame)
        except EncoderStopped as exc:
            if self._error is not None or self._engine_lost():
                return  # the panel failed meanwhile; the next frame reconnects
            self._stopped(str(exc))
            self._start_encoder(frame.size)
            self._feed(frame)

    def show_video(self, source: VideoSource | CaptureSource, size: tuple[int, int]) -> bool:
        """A theme that is one video across the panel ([video] direct): ffmpeg encodes it
        straight from its file (h264.ffmpeg_file_command), not from the frames drawn of it;
        the next frame (:meth:`show`) takes over again. False where it is not done so: a
        colour check under way, or a video ffmpeg could not play from its file (frames then)."""
        if self.transport is None or self.session is None:
            raise DeviceError("panel not open")
        if self.feed is not None or self.range is not None or source in self._sources_failed:
            return False
        self._engine_lost()
        if isinstance(self._error, DecoderHung):
            self._restart_panel(self._error)  # a new connection does not help
        if self._error is not None:
            self._heal(self._error)
        self._hand_over()
        self._follow_the_panel()
        encoder = self._leaving or self.encoder
        same = encoder is not None and getattr(encoder, "source", None) == source
        if same and encoder.size == size and encoder.alive:
            return True
        if same and encoder.size == size and not encoder.pictures:  # it never played
            self._sources_failed.add(source)
            log.warning("video: ffmpeg could not play %s itself (%s); frames for it",
                        source_name(source), encoder.reason())  # fmt: skip
            self._start_encoder(size)
            return False
        if same and encoder.size == size:
            self._stopped(encoder.reason())  # (it stopped by itself: counted)
        self._start_encoder(size, source=source)
        return True

    def _stopped(self, reason: str) -> None:
        """ffmpeg stopped by itself: a restart, unless it stopped before its first picture
        and the next start goes another way (:meth:`_gave_up`). The engine as a program of
        its own that only encodes is counted as one that ended unasked (:class:`EngineTrouble`;
        the engine that has the panel is counted by its link)."""
        encoder = self._leaving or self.encoder
        if getattr(encoder, "name", "") == NATIVE and not isinstance(encoder, EngineStream):
            ENGINE_TROUBLE.note(reason)
        if not self._gave_up(reason):
            self._restarting(reason)

    def _gave_up(self, reason: object) -> bool:
        """Whether the encoder stopped before its first picture, and is not started again as it
        was (uncounted as a restart): one of the card's that ended converting the frames itself
        ("card") gets them converted to NV12 by ffmpeg (its driver may take no RGB), and for a
        week the next connections too; an encoder "auto" chose that the card or the driver
        would not have is left out from here on, and x264 takes over."""
        encoder = self._leaving or self.encoder  # during a switch only the old one can stop here
        name = getattr(encoder, "name", "")
        if encoder is None or encoder.pictures:
            return False
        settings = getattr(encoder, "settings", None)
        ended = not encoder.alive  # (one that hangs is no matter of the frames' format)
        if settings is not None and passes_through(settings) and ended:
            if name not in self._feeds_failed:
                self._feeds_failed.add(name)
                if self._ffmpeg is not None:  # for the next connections too, a week long
                    video_state.keep_refusal(self._ffmpeg, name)
                log.warning(
                    "video: %s ended before a picture fed %s (%s); ffmpeg converts the frames "
                    "for it",
                    name,
                    settings.pixels,
                    reason,
                )
                return True
        if self.settings.encoder != "auto" or name == "libx264":
            return False
        self._encoders_failed.add(name)
        log.warning("video: %s stopped before a picture (%s); x264 takes over", name, reason)
        return True

    def _feed(self, frame: Image.Image) -> None:
        """Hand a frame to ffmpeg, as its encoder takes it (packed into the pipe in its writer
        thread, :func:`write_frame`). Waiting is right while the panel sets the pace (the
        pictures queue is full; USB timeouts bound that); ffmpeg taking nothing while the
        panel waits for pictures has stalled. Before its first picture ffmpeg may still be
        starting, which takes longer: restarting it then would only drop the frames it
        already has. During a switch the old ffmpeg, whose pictures the panel shows, is the
        one waited for; the new one gets the frame (as it takes it) only if it takes it at
        once (while it starts it takes none, and the panel must not wait for that)."""
        if not self._as_scene(frame):  # (its layers go: its pixels are not made)
            frame.load()  # (here: the writer thread only reads it)
        encoder = self._leaving
        if encoder is None:
            encoder = self.encoder
        else:
            try:
                self.encoder.write(_frame_for(self.encoder, frame, self.settings), timeout=0)
            except EncoderStopped:  # starting, or stopped: _hand_over sees to it
                made = getattr(self.encoder, "yuv", None)
                if made is not None:  # it missed a frame: the next is made whole
                    made.whole = True
        data = _frame_for(encoder, frame, self.settings)
        idle = 0.0
        while True:
            try:
                encoder.write(data, timeout=0.25)
                return
            except EncoderStopped:
                if not encoder.alive or self._error is not None:
                    raise
                if self._pictures.full() or getattr(encoder, "waiting", False):
                    idle = 0.0
                    continue
                idle += 0.25
                limit = self.STALL_S if encoder.pictures else self.START_S
                if idle >= limit:
                    raise EncoderStopped(f"ffmpeg took no frames for {limit:g} s") from None

    def _restarting(self, reason: str) -> None:
        """Count an ffmpeg that stopped by itself (not one closed for a reconnect)."""
        now = time.monotonic()
        self.counters.ffmpeg_restarts += 1
        self._restarts = [t0 for t0 in self._restarts if now - t0 < 60] + [now]
        if len(self._restarts) > self.RESTARTS_PER_MINUTE:
            raise DeviceError(t("ffmpeg keeps stopping: {reason}", reason=reason))
        log.warning("ffmpeg stopped (%s); starting it again", reason)

    def _restart_panel(self, hung: DecoderHung) -> None:
        """Restart the panel (11) to clear its hung decoder, as SPUR II does; the
        main loop opens it again once it is back. Raises :class:`PanelRestarting`,
        or ``hung`` itself when restarts did not help (then: replug)."""
        if not RESTART.allowed():
            raise hung
        RESTART.begin(self.transport.pid)
        self.counters.panel_restarts += 1
        log.warning(
            "the panel's video decoder hangs; restarting the panel (%d of %d in a row)",
            RESTART.count,
            RESTART.IN_A_ROW,
        )
        self._stop_video()
        try:
            self.transport.query(CMD_RESTART, timeout_ms=200)  # it may be gone before it answers
        except DeviceError:
            pass
        self.session, self._error = None, None
        super().close()
        self.link = None
        raise PanelRestarting(t("the panel restarts to clear its hung video decoder"))

    def _heal(self, exc: Exception) -> None:
        """Reconnect after a USB error and start the video again (a full start)."""
        log.warning("panel stopped answering (%s); reconnecting", exc)
        self._stop_video()
        super().close()
        self.session, self.link = None, None
        super().open()
        self._start_video()

    def close(self) -> None:
        self._keep_rate(time.monotonic(), at_once=True)
        self._stop_video()
        if self.transport is not None and self.session is not None and self._error is None:
            try:
                last = encode_frame(to_native(self._last_frame)) if self._last_frame else None
            except FrameError:
                last = None  # too detailed for a PNG: the video's last picture stays
            try:
                self.session.stop(last)
            except DeviceError as exc:
                log.debug("stopping the video: %s", exc)
        session, link = self.session, self.link
        self.session = None
        super().close()  # (the engine ends: its last counts are in)
        self.link = None
        if link is not None:
            counted = link.counts
            sent = (int(counted.get("blocks", 0)), int(counted.get("bytes", 0)),
                    int(counted.get("waits", 0)), int(counted.get("max_depth", 0)))  # fmt: skip
        elif session is not None:
            sent = (session.blocks, session.bytes, session.waits, session.max_depth)
        else:
            sent = (0, 0, 0, 0)
        if sent[0]:
            log.info("video: %d blocks, %.1f MB, waited %d times, deepest queue %d",
                     sent[0], sent[1] / 1e6, sent[2], sent[3])  # fmt: skip
        if link is not None and counted.get("pictures"):
            log.info("%s", lateness_said(counted))
            took = encoding_said(counted)
            if took:
                log.info("%s", took)

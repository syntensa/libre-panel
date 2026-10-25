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
  bit-identical to rotating in Python and costs the render loop nothing (on four bytes
  a pixel ffmpeg turns a frame for less than Pillow does, measured on the 12.3").
- The frames go in as Pillow keeps them, four bytes a pixel (``rgb0``: ffmpeg leaves the
  fourth aside, as RGB never showed it), packed into the pipe by Pillow itself
  (:func:`resolve_feed`). Who turns them into YUV (``video.pixels``): ffmpeg on the processor
  (swscale: BT.601 in limited range; the same YUV as from three bytes a pixel, for less), or
  the graphics card (:data:`CARD_ENCODERS`, :func:`card_chain`): AMD's through ``vpp_amf`` and
  Intel's through ``vpp_qsv``, each told BT.601 and the range outright, NVIDIA's with its own
  converter. "auto" lets the card convert where the colour check found its colours the same as
  the processor's on this PC (card_choice.py: AMD's own converter, which ``h264_amf`` used
  before, made BT.709 under a full-range tag), else the processor; "card" and "rgb" (three
  bytes a pixel, the way of before) force a way.
- Every stream says of itself that it is BT.601, in the range its values have, whoever made it:
  the encoder is told so (:func:`colour_tags`), and ffmpeg's ``h264_metadata`` stamps it into
  each SPS (:func:`stamp`), as AMD's encoder tagged its stream full range of limited values. The
  range is limited (16-235), or full (0-255) for a panel whose decoder shows every stream as
  full range (the 12.3": ``[device] video_range``); then swscale makes full-range YUV
  (:data:`FULL_RANGE`), and of the cards only those told the range outright convert
  (:data:`FULL_RANGE_CARDS`).
- The bit rate was capped at 2 Mbit/s there, for 1920x480 at 50 frames a second. A panel
  with more pixels (the 12.3": 1920x720) or more frames starts with proportionally more
  (``maxrate`` "auto", :func:`auto_rate`), so its pictures and lettering stay as sharp; and
  "auto" then follows the panel (:class:`RateControl`): a step down while the panel takes
  less than it is sent, a step up again after a calm minute, up to 32 Mbit/s.

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
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from libre_panel import bundled, winsession
from libre_panel.devices import video_state
from libre_panel.devices.base import Counters, DeviceError
from libre_panel.i18n import t

log = logging.getLogger(__name__)

X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")
# "auto": high where it was measured to show (HIGH_PROFILE_MODELS), baseline elsewhere; main
# and high ask more of the panel's decoder, and compress better.
PROFILES = ("auto", "baseline", "main", "high")
# "auto": the graphics card's encoder where it was measured to show and ffmpeg can start it
# (AUTO_ENCODERS, in that order), else x264 on the processor. The others are an experiment.
ENCODERS = ("auto", "libx264", "h264_nvenc", "h264_amf", "h264_qsv")
HIGH_PROFILE_MODELS = frozenset({"turing-9.2-usb", "turing-12.3-usb"})
AUTO_ENCODERS = ("h264_amf",)
# Who turns the frames into YUV (config.toml video.pixels): "auto" the graphics card where the
# colour check found it converting as the processor does on this PC (card_choice.py), else ffmpeg
# on the processor, the frames four bytes a pixel as Pillow keeps them (rgb0); "card" the card
# (CARD_ENCODERS; the others as the processor); "rgb" the processor, fed three bytes a pixel
# (rgb24), the way of before. Inside, the processor's way of "auto" is "processor".
PIXELS = ("auto", "rgb", "card")
# The encoders whose graphics card can convert the frames (four bytes a pixel) itself, so that
# ffmpeg converts nothing on the processor (card_chain says how).
CARD_ENCODERS = frozenset({"h264_amf", "h264_nvenc", "h264_qsv"})


def colour_tags(full: bool = False) -> list[str]:
    """What every encoder is told of the colours, and what a card converting itself is told to
    make: BT.601, as swscale converts on the processor (the stream of before was made so), in
    limited range or (``full``) full range, said in full, so that no decoder guesses from the
    picture's size."""
    return [
        "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
        "-color_range", "pc" if full else "tv",
    ]  # fmt: skip


def stamp(full: bool = False) -> list[str]:
    """The same stamped into every stream's SPS (its VUI) after the encoder, whatever it wrote
    there: AMD's encoder tagged limited values full range (and BT.709), and a decoder that reads
    the tag stretches them, washed out. 6: SMPTE 170M (BT.601) primaries, transfer and matrix."""
    return [
        "-bsf:v",
        f"h264_metadata=video_full_range_flag={int(full)}:colour_primaries=6"
        ":transfer_characteristics=6:matrix_coefficients=6",
    ]


COLOUR_TAGS = colour_tags()
STAMP = stamp()
# Full-range YUV for a panel whose decoder shows every stream as full range: swscale converts the
# turned frame with BT.601 into 0-255 (the encoder's pixel format follows: format=...).
FULL_RANGE = "scale=out_range=full:out_color_matrix=bt601"
# The cards that are told the range outright (vpp_amf, vpp_qsv) and so may make full-range YUV;
# NVIDIA's own converter makes limited range of RGB (FFmpeg's nvenc.c marks it so). On a
# full-range panel the processor converts for the others.
FULL_RANGE_CARDS = frozenset({"h264_amf", "h264_qsv"})


def card_chain(encoder: str, full: bool = False) -> tuple[list[str], list[str]]:
    """How the graphics card converts the frames (four bytes a pixel, turned on the processor
    as before) for ``encoder``: ffmpeg's options before the input (the device it opens) and the
    filters after the turn.

    - AMD: the frames uploaded to the card (AMF takes RGBA, not RGB0: ``format=rgba`` sets the
      fourth byte opaque on the way) and converted by ``vpp_amf``, told BT.601 and the range,
      into NV12 that ``h264_amf`` takes on the card (FFmpeg 8.1's libavfilter/vf_vpp_amf.c:
      AMF_VIDEO_CONVERTER_COLOR_PROFILE_601 or _FULL_601);
    - Intel: ``vpp_qsv`` takes BGRA (RGB32), the input said to be full-range RGB
      (``setrange``), told the matrix, primaries, transfer and range outright;
    - NVIDIA: no such filter in the build (``scale_cuda`` has no matrix): ``h264_nvenc`` takes the
      frames as they are and converts them with its own converter, BT.601 in limited range.
    """
    if encoder == "h264_amf":
        vpp = ("vpp_amf=format=nv12:color_profile=bt601:color_range="
               f"{'full' if full else 'studio'}:primaries=smpte170m:trc=smpte170m")  # fmt: skip
        return ["-init_hw_device", "amf=amf", "-filter_hw_device", "amf"], [
            "format=rgba", "hwupload", vpp]  # fmt: skip
    if encoder == "h264_qsv":
        vpp = (f"vpp_qsv=format=nv12:out_range={'pc' if full else 'tv'}:out_color_matrix=smpte170m"
               ":out_color_primaries=smpte170m:out_color_transfer=smpte170m")  # fmt: skip
        return ["-init_hw_device", "qsv=qsv", "-filter_hw_device", "qsv"], [
            "format=bgra", "setrange=full", vpp]  # fmt: skip
    return [], []


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
    crf: int = 20
    preset: str = "superfast"
    keyframe_s: float = 10.0
    maxrate: str = "auto"  # "auto": REFERENCE_RATE scaled by the pixels and the frame rate
    threads: int = 1  # frame threads add a frame of latency each
    profile: str = "high"  # PROFILES, resolved (never "auto" here: resolve_profile)
    encoder: str = "libx264"  # ENCODERS, resolved (never "auto" here: resolve_encoder)
    # the frames as they go into ffmpeg (resolve_feed): "rgb24", or "rgb0" (four bytes a pixel)
    pixels: str = "rgb24"
    # the card converts them itself (video.pixels "card": passes_through), else ffmpeg does
    card: bool = False
    # full-range YUV (0-255), for a panel whose decoder shows every stream so (video_range)
    full: bool = False


# The bit rate measured on the 9.2": 2 Mbit/s for its 1920x480 pixels at 50 frames a second.
REFERENCE_RATE = (2.0, 1920 * 480, 50)
# The steps "auto" moves along, in Mbit/s. The link itself carries far more (12-16 MB/s of
# JPEG stills were measured on the 9.2", and other hosts stream 16-24 Mbit/s of H.264 to the
# 5.2": docs/protocol/turzx-usb.md); the top is where the encoder's cost outgrows what a
# panel's dots can show.
RATE_STEPS = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0)


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


def resolve_profile(profile: str, model_id: str) -> str:
    """The H.264 profile for a panel: "auto" is high where it was measured to show (the 9.2"
    and the 12.3", whose decoder takes 1080p60 of it), baseline elsewhere."""
    if profile != "auto":
        return profile
    return "high" if model_id in HIGH_PROFILE_MODELS else "baseline"


_PROBED: dict[tuple[str, str], bool] = {}
_SAID: dict[tuple[str, str], str] = {}  # the last line ffmpeg said in this run's probe


# The pictures a probe of an encoder feeds: a size every card's encoder takes (AMD's and
# NVIDIA's refuse very small ones) and enough frames for its first picture to come out.
PROBE_SIZE = (480, 480)
PROBE_FRAMES = 10


def encoder_works(ffmpeg: str, encoder: str) -> bool:
    """Whether ``ffmpeg`` can encode with ``encoder`` on this PC (a hardware encoder needs
    its card and its driver): the command the video mode runs, fed a few pictures as it feeds
    them, must give back H.264. Asked once a week (the answer is kept in video-state.json: a
    new driver may change it) and taken once per run."""
    key = (ffmpeg, encoder)
    if key not in _PROBED:
        works = video_state.probed(ffmpeg, encoder)  # asked within the week
        if works is None:
            works, said = _probe(ffmpeg, encoder)
            video_state.keep_probe(ffmpeg, encoder, works, said)
            _SAID[key] = said
            if not works:
                log.info("video: %s cannot encode here (%s); x264 does", encoder, said or "-")
        _PROBED[key] = works
    return _PROBED[key]


def known_works(ffmpeg: str, encoder: str) -> bool | None:
    """:func:`encoder_works`'s answer as far as it is known without asking ffmpeg: this run's,
    else the week's in video-state.json; None when it was not asked."""
    key = (ffmpeg, encoder)
    if key in _PROBED:
        return _PROBED[key]
    return video_state.probed(ffmpeg, encoder)


def probe_said(ffmpeg: str, encoder: str) -> str:
    """The last line ffmpeg said when ``encoder`` was probed (in this run, else within the week:
    video-state.json): why a card's encoder was passed over; "" when nothing is known."""
    said = _SAID.get((ffmpeg, encoder))
    return said if said is not None else video_state.probe_said(ffmpeg, encoder)


def _probe(ffmpeg: str, encoder: str) -> tuple[bool, str]:
    """Whether the video mode's own command with ``encoder`` turns a few pictures into H.264,
    and the last thing ffmpeg said. A card's encoder may hold its last pictures back when the
    input ends, or end with an error code after it encoded: what counts is that H.264 came.
    Fed as the video mode feeds it by default: four bytes a pixel, which ffmpeg turns into the
    encoder's YUV (a card's own conversion, video.pixels "card", is the colour check's to try:
    one that gives nothing so is fed so no more, turzx_video)."""
    settings = EncoderSettings(encoder=encoder, profile="main", pixels="rgb0")
    command = ffmpeg_command(ffmpeg, PROBE_SIZE, None, settings)
    width, height = PROBE_SIZE
    frames = bytes(width * height * 4) * PROBE_FRAMES
    try:
        done = subprocess.run(
            command, input=frames, capture_output=True, timeout=20, creationflags=NO_WINDOW
        )
        out, err = done.stdout or b"", done.stderr or b""
    except subprocess.TimeoutExpired as exc:  # (what it gave until then counts)
        out, err = exc.stdout or b"", exc.stderr or b""
    except OSError as exc:
        return False, str(exc)
    return b"\x00\x00\x01" in out, reason(err.decode("utf-8", "replace"))


# ffmpeg's closing words after an error, which say nothing of the error itself
_GENERIC = ("Conversion failed!", "Nothing was written into output file")


def reason(said: str) -> str:
    """The line of what ffmpeg said that tells why: its last, unless that is only its closing
    words after an error (then the one before)."""
    lines = [line.strip() for line in said.splitlines() if line.strip()]
    telling = [line for line in lines if not any(words in line for words in _GENERIC)]
    return (telling or lines or [""])[-1]


def resolve_encoder(encoder: str, ffmpeg: str, failed: Collection[str] = ()) -> str:
    """The encoder to start: the one set, or with "auto" the first of AUTO_ENCODERS that
    works here and did not fail before (``failed``), else libx264."""
    if encoder != "auto":
        return encoder
    for candidate in AUTO_ENCODERS:
        if candidate not in failed and encoder_works(ffmpeg, candidate):
            return candidate
    return "libx264"


def resolve_feed(pixels: str) -> str:
    """The frames' pixel format in ffmpeg's terms for a way of converting them: "processor" and
    "card" (and "auto", either) are rgb0, four bytes a pixel as Pillow keeps them, the cheapest
    for every encoder (measured on the 12.3": less to pack, a faster ``transpose``); "rgb" is
    rgb24, the way of before."""
    return "rgb24" if pixels == "rgb" else "rgb0"


def feed_text(settings: EncoderSettings) -> str:
    """The encoder and what it is fed, as the log and the counters say it: "h264_amf, rgb0",
    and ", card" when the card converts the frames itself."""
    return f"{settings.encoder}, {settings.pixels}" + (", card" if passes_through(settings) else "")


def bit_rate(settings: EncoderSettings, size: tuple[int, int]) -> str:
    """The highest bit rate for pictures of ``size``, as ffmpeg takes it: the settings' own,
    or with "auto" the rate it starts with (:func:`auto_rate`)."""
    if settings.maxrate != "auto":
        return settings.maxrate
    return rate_text(auto_rate(settings, size))


class RateControl:
    """The bit rate of ``maxrate`` "auto" while the panel runs.

    It starts at :func:`auto_rate` and moves along :data:`RATE_STEPS` from there up: below
    the start it never goes, since a panel that cannot keep up at that rate cannot keep up
    with the pictures themselves (fewer frames a second help, fewer bits do not; the 9.2"
    at 60 fps showed it), and :attr:`floored` says when that happened. A step down when the
    panel takes less than it is sent, seen within a window of :attr:`WINDOW_S` either as the
    sender waiting for room in the panel's queue :attr:`TROUBLE_WAITS` times (its decoder
    lags) or as half the blocks, :attr:`TROUBLE_SLOW` at least, taking longer than a frame
    (its link lags; the median, so that a busy PC's single late blocks do not count). A
    step up after :attr:`CALM_S` without that, as long as the step above did not fail
    lately: a failed step is held off for :attr:`HOLD_S`, twice as long each time it fails
    again. :meth:`judge` is called with the run's counters as often as wanted (every
    frame); it answers with the new rate when one is due, else ``None``. The driver switches
    to a new ffmpeg at the new rate. ``remembered``, the rate an earlier run got to
    (video-state.json), is where a new run starts: the step at or below it.
    """

    WINDOW_S = 10.0
    TROUBLE_WAITS = 5
    TROUBLE_SLOW = 25
    CALM_S = 60.0
    HOLD_S = 600.0

    def __init__(self, start: float, remembered: float = 0.0) -> None:
        self.steps = [step for step in sorted({*RATE_STEPS, start}) if step >= start]
        self.rate = max((step for step in self.steps if step <= remembered), default=start)
        self.changes = 0
        self.floored = False  # trouble at the start rate: the frames, not the bits
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
            moved = self._move(-1)
            self.floored = moved is None
            return moved
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
    """ffmpeg reading raw frames of ``size`` on stdin (``settings.pixels``), turning them into
    the panel's framebuffer (``rotate``), writing H.264 (Annex B) to stdout."""
    width, height = size
    keyint = max(1, round(settings.fps * settings.keyframe_s))
    maxrate = bit_rate(settings, size)
    card = passes_through(settings)
    device, converts = card_chain(settings.encoder, settings.full) if card else ([], [])
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        *device,
        "-f",
        "rawvideo",
        "-pix_fmt",
        settings.pixels,
        "-s",
        f"{width}x{height}",
        "-framerate",
        str(settings.fps),
        "-i",
        "-",
        "-an",
    ]
    filters = [ROTATION_FILTERS[rotate], *converts]
    if settings.full and not card:  # full-range YUV, made on the processor
        made = "yuv420p" if settings.encoder == "libx264" else "nv12"
        filters += [FULL_RANGE, f"format={made}"]
    video_filter = ",".join(part for part in filters if part)
    if video_filter:
        command += ["-vf", video_filter]
    command += codec_args(settings, keyint, maxrate)
    return command + [*stamp(settings.full), "-f", "h264", "-"]


def passes_through(settings: EncoderSettings) -> bool:
    """Whether the graphics card converts the frames (:func:`card_chain`): one of the card's
    encoders, told to (``settings.card``), fed four bytes a pixel; ffmpeg converts nothing on
    the processor then."""
    return settings.card and settings.encoder in CARD_ENCODERS and settings.pixels == "rgb0"


def codec_args(settings: EncoderSettings, keyint: int, maxrate: str) -> list[str]:
    """The encoder's part of the command: x264 as measured on the 9.2", or (an experiment)
    a hardware encoder with the nearest settings: no B-frames, a keyframe every ``keyint``
    pictures, the rate capped at ``maxrate``; each makes one slice per picture by default."""
    encoder, profile = settings.encoder, settings.profile
    if encoder == "libx264":
        return [
            "-c:v",
            "libx264",
            "-profile:v",
            profile,
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
            *colour_tags(settings.full),
        ]
    common = ["-bf", "0", "-g", str(keyint), "-maxrate", maxrate, "-bufsize", maxrate]
    common += colour_tags(settings.full)  # (a card that converts itself is told to make them)
    if not passes_through(settings):  # ffmpeg makes the NV12, with swscale's colours
        common += ["-pix_fmt", "nv12"]
    if encoder == "h264_nvenc":  # NVIDIA: the quality of crf, within the cap
        tuned = ["-preset", "p1", "-tune", "ll", "-zerolatency", "1", "-no-scenecut", "1"]
        rate = ["-rc", "vbr", "-cq", str(settings.crf), "-b:v", "0", "-forced-idr", "1"]
        return ["-c:v", "h264_nvenc", "-profile:v", profile, *tuned, *rate, *common]
    if encoder == "h264_amf":  # AMD: the rate sets the quality
        amf_profile = "constrained_baseline" if profile == "baseline" else profile
        tuned = ["-usage", "lowlatency", "-quality", "speed", "-rc", "vbr_peak", "-b:v", maxrate]
        return ["-c:v", "h264_amf", "-profile:v", amf_profile, *tuned, *common]
    if encoder == "h264_qsv":  # Intel: the rate sets the quality
        tuned = ["-preset", "veryfast", "-look_ahead", "0", "-b:v", maxrate]
        return ["-c:v", "h264_qsv", "-profile:v", profile, *tuned, *common]
    raise ValueError(f"video.encoder {encoder!r}: one of {', '.join(ENCODERS)}")


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
    loop for longer than the ``write`` timeout. A frame is its bytes, or a callable
    that writes them itself to ffmpeg's stdin in that thread (the display packs a
    picture into the pipe so, off the render loop). A reader thread cuts the output
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
        self._frames: queue.Queue[bytes | Callable[[Any], None] | None] = queue.Queue(maxsize=1)
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

    def write(self, frame: bytes | Callable[[Any], None], timeout: float = 2.0) -> None:
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
                if callable(frame):  # it writes itself, all of it or an OSError
                    frame(stdin)
                    continue
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

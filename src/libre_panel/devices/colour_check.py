"""Whether a graphics card turns the frames' colours into video as the processor does.

Settings → Panel → Check the colours, and the automatic choice of video.pixels "auto"
(card_choice.py). ffmpeg turns the frames (four bytes a pixel, rgb0) into YUV on the processor
(swscale, BT.601, in the panel's range: :func:`~h264.ffmpeg_command`), or the card does it
(AMD's scaler, Intel's VPP, NVIDIA's encoder by itself: :func:`~h264.card_chain`), which costs
the processor less, told to convert as swscale does. Left to itself, AMD's encoder was measured
converting with BT.709 under a full-range tag instead. Only the PC with the card can show
whether the two agree, so the check measures it there, away from the panel:

- a test picture at the size of the panel's frames (:func:`check_picture`): large flat patches of
  black, five greys, white, pure red, green and blue, the themes' accent and violet, an orange
  and a skin tone;
- for every encoder of the card that ffmpeg can start here (:func:`~h264.encoder_works`) and for
  x264, ten frames of it (the background check: five) through the very command the display
  builds (:func:`~h264.ffmpeg_command` with the display's settings, its range and its turn into
  the panel's framebuffer): a card's encoder once converted by the card (``"card"``) and once by
  the processor (``"processor"``); one the card does not convert for (x264, or for a panel in
  full range NVIDIA's, which makes limited range only) fed four bytes a pixel and three
  (``"processor"`` and ``"rgb"``), the reference;
- each stream decoded by ffmpeg to raw 4:2:0 YUV without any conversion (the decoder's own pixel
  format, which the result names), turned back, and the mean Y, U and V of the middle of every
  patch (half its width and height: away from its edges, where chroma and blocks blur);
- the two compared patch by patch (:func:`judge`): **identical** when every patch is within ±2
  code values; a **level shift** when the greys stay grey in both but black and white differ
  (full against limited range); a **matrix shift** when the greys agree and the saturated colours
  differ (BT.709 against BT.601); else **other**. Both are turned into the RGB the panel shows,
  with BT.601 in the panel's range (:func:`to_rgb`): the panel's decoder applies one fixed matrix
  whatever the stream says of itself, so equal YUV is equal colours on the panel;
- what the processor's conversion costs a frame of that size (:func:`conversion_ms`), which the
  card's own saves.

Every run is an ffmpeg of its own with a time limit, one after the other (never two sessions of
the card's encoder at once from here), killed when it outlasts it or the check ends; one check
at a time, across the app's processes too (:func:`exclusive`); the running panel and its ffmpeg
are not touched.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont

from libre_panel.devices import h264
from libre_panel.devices.h264 import (
    NO_WINDOW,
    ROTATION_FILTERS,
    EncoderSettings,
    ffmpeg_command,
)

log = logging.getLogger(__name__)

# The patches, in the order of the picture (left to right, top to bottom). The accent is the one
# of Libre Default and the Lumen family, the violet Libre Default's (themes/libre-default).
GREYS = (
    ("black", (0, 0, 0)),
    ("grey-32", (32, 32, 32)),
    ("grey-80", (80, 80, 80)),
    ("grey-128", (128, 128, 128)),
    ("grey-176", (176, 176, 176)),
    ("grey-224", (224, 224, 224)),
    ("white", (255, 255, 255)),
)
COLOURS = (
    ("red", (255, 0, 0)),
    ("green", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("accent", (0x2E, 0xE6, 0xD6)),
    ("violet", (0xA7, 0x8B, 0xFA)),
    ("orange", (0xFF, 0x8C, 0x1A)),
    ("skin", (0xE0, 0xAC, 0x8A)),
)
PATCHES = GREYS + COLOURS
SPARE = (0, 0, 0)  # a cell of the grid without a patch
# What is checked: the card's encoders where ffmpeg can start them here, then x264 (in that
# order: one run at a time)
ENCODERS = ("h264_amf", "h264_nvenc", "h264_qsv", "libx264")
# The ways compared, the first against the second: a card's encoder converting the colours
# itself against the processor's conversion; one the card does not convert for (x264, or in full
# range a card that makes limited range only), four bytes a pixel against three, both converted
# by ffmpeg: the reference
CARD_FEEDS = ("card", "processor")
PROCESSOR_FEEDS = ("processor", "rgb")
# One check at a time, the person's (Settings) or the automatic choice's in the background
# (card_choice.py), across the app's processes too (a panel beside the first may run in one of
# its own): in a process a lock, between them a file beside config.toml naming the process that
# checks (:func:`exclusive`)
BUSY = threading.Lock()
BUSY_FILE = "colour-check.running"
STALE_S = 300.0  # a file older than this, or of a process that is gone, is taken over


def feeds_of(encoder: str, full: bool = False) -> tuple[str, str]:
    """The two ways ``encoder`` is checked (for a panel in ``full`` range), the first compared."""
    if encoder in h264.CARD_ENCODERS and (not full or encoder in h264.FULL_RANGE_CARDS):
        return CARD_FEEDS
    return PROCESSOR_FEEDS


FRAMES = 10
RUN_S = 30.0  # the longest an ffmpeg of the check may take
TOLERANCE = 2.0  # code values within which two patches count as the same
VERDICTS = ("identical", "level", "matrix", "other")
CHANNELS = ("Y", "U", "V")
FOUR_TWO_ZERO = ("yuv420p", "yuvj420p")  # the same planes; "j": the stream says full range
FONT = "builtin:Barlow-Medium"


class CheckError(Exception):
    """A run of the check that gave nothing to measure; the text says why."""


# -- the picture ------------------------------------------------------------------------------


def _busy_path() -> Path | None:
    from libre_panel.config import config_dir

    folder = config_dir()
    return folder / BUSY_FILE if folder.is_dir() else None


def _stale(path: Path) -> bool:
    try:
        text, age = path.read_text(encoding="utf-8"), time.time() - path.stat().st_mtime
    except OSError:
        return True  # (gone meanwhile)
    try:
        pid = int(text.split()[0])
    except (IndexError, ValueError):
        return age > 5  # (being written)
    return age > STALE_S or pid == os.getpid() or not psutil.pid_exists(pid)


def _claim() -> bool:
    path = _busy_path()
    if path is None:
        return True
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not _stale(path):
                return False
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        except OSError:
            return True  # (a folder it cannot write into: one check at a time in here only)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(f"{os.getpid()}\n")
        return True
    return False


def busy() -> bool:
    """Whether a check runs, in this process or another of the app's."""
    if BUSY.locked():
        return True
    path = _busy_path()
    return path is not None and path.exists() and not _stale(path)


@contextlib.contextmanager
def exclusive(wait: float = 0.0) -> Iterator[bool]:
    """While no other check runs: yields True, and none begins until it ends; False when one
    still runs after ``wait`` seconds (nothing held then)."""
    deadline = time.monotonic() + wait
    held = False
    while True:
        if BUSY.acquire(blocking=False):
            if _claim():
                held = True
                break
            BUSY.release()
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    try:
        yield held
    finally:
        if held:
            path = _busy_path()
            if path is not None:
                with contextlib.suppress(OSError):
                    path.unlink()
            BUSY.release()


def grid(size: tuple[int, int], count: int = len(PATCHES)) -> tuple[int, int]:
    """Columns and rows for ``count`` patches on a frame of ``size``, the cells as square as
    the frame allows (7 by 2 on 1920x480, 2 by 7 on end, 4 by 4 on a round one)."""
    width, height = size
    rows = max(1, min(count, round(math.sqrt(count * height / width))))
    cols = math.ceil(count / rows)
    return cols, math.ceil(count / cols)


def layout(size: tuple[int, int]) -> list[tuple[str, tuple[int, int, int], tuple[int, ...]]]:
    """Every patch: its id, its colour and its box (x0, y0, x1, y1) on a frame of ``size``."""
    width, height = size
    cols, rows = grid(size)
    found = []
    for i, (name, rgb) in enumerate(PATCHES):
        col, row = i % cols, i // cols
        box = (width * col // cols, height * row // rows,
               width * (col + 1) // cols, height * (row + 1) // rows)  # fmt: skip
        found.append((name, rgb, box))
    return found


def centre(box: tuple[int, ...]) -> tuple[int, int, int, int]:
    """The middle of a patch that is measured: half its width and height, and at least 8
    pixels from its edges (chroma is a pixel in two, blocks are 16)."""
    x0, y0, x1, y1 = box
    dx = min(max((x1 - x0) // 4, 8), max(1, (x1 - x0) // 2 - 1))
    dy = min(max((y1 - y0) // 4, 8), max(1, (y1 - y0) // 2 - 1))
    return x0 + dx, y0 + dy, x1 - dx, y1 - dy


@lru_cache(maxsize=8)
def check_picture(size: tuple[int, int], letter: str = "") -> Image.Image:
    """The test picture at ``size`` (RGB); ``letter`` ("A", "B") large on it, for the eye that
    compares the two feeds on the panel: in the top left corner, on black, clear of a bezel's
    hidden strip (the 9.2" hides 18 pixels at the top); in the middle of a square frame, which
    may be a round panel's."""
    image = Image.new("RGB", size, SPARE)
    draw = ImageDraw.Draw(image)
    for _, rgb, box in layout(size):
        draw.rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=rgb)
    if letter:
        from libre_panel.fonts import builtin_font_path

        _, _, box = layout(size)[0]
        side = int(min(box[2] - box[0], box[3] - box[1]) * 0.9)
        path = builtin_font_path(FONT)
        font = ImageFont.truetype(str(path), max(8, side)) if path else ImageFont.load_default()
        stroke = max(2, side // 24)
        if size[0] == size[1]:
            where, anchor = (size[0] / 2, size[1] / 2), "mm"
        else:
            margin = max(24, side // 8)
            where, anchor = (box[0] + margin, box[1] + margin), "lt"
        draw.text(where, letter, font=font, fill="white", stroke_width=stroke,
                  stroke_fill="black", anchor=anchor)  # fmt: skip
    return image


# -- the runs -----------------------------------------------------------------------------------


_running: set[subprocess.Popen] = set()  # the check's ffmpeg under way (kill_all)
_running_lock = threading.Lock()


def _kill(proc: subprocess.Popen) -> None:
    """``proc`` and what it started (a package manager's shim starts the real ffmpeg)."""
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except (psutil.Error, AttributeError, TypeError, ValueError):
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def kill_all() -> None:
    """The check's ffmpeg under way, ended (the check stops, the app closes)."""
    with _running_lock:
        running = list(_running)
    for proc in running:
        if proc.poll() is None:
            _kill(proc)


def run(command: list[str], data: bytes, timeout: float = RUN_S) -> tuple[int, bytes, str]:
    """``command`` with ``data`` on its stdin (all of it, then closed), its output read to the
    end (Python's pipes and threads, on Windows too): (exit code, stdout, stderr). Killed, and
    what it started too, when it takes longer than ``timeout``, by :func:`kill_all`, or when
    anything goes wrong on the way."""
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        raise CheckError(f"cannot start ffmpeg: {exc}") from exc
    with _running_lock:
        _running.add(proc)
    try:
        out, err = proc.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CheckError(f"ffmpeg took longer than {timeout:g} s") from None
    finally:
        if proc.poll() is None:
            _kill(proc)
            try:
                proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
        with _running_lock:
            _running.discard(proc)
    return proc.returncode, out, err.decode("utf-8", "replace")


def _last_line(text: str) -> str:
    """The line of what ffmpeg said that tells why (not its closing words after an error)."""
    return h264.reason(text)


def encode(
    command: list[str],
    picture: Image.Image,
    pixels: str,
    frames: int = FRAMES,
    timeout: float = RUN_S,
) -> bytes:
    """``frames`` frames of ``picture`` through ``command`` (an ffmpeg of the display's),
    packed as ``pixels`` ("rgb0", "rgb24") as the display packs them; its H.264 (Annex B)."""
    from libre_panel.devices.turzx_video import frame_bytes

    raw = frame_bytes(picture, pixels)
    code, out, err = run(command, raw * frames, timeout)
    if code != 0 or not out:
        raise CheckError(_last_line(err) or f"ffmpeg ended with code {code}")
    return out


_DECODED = re.compile(r"Output #0.*?Video: rawvideo[^,]*, (\w+)(?:\(([^)]*)\))?, (\d+)x(\d+)", re.S)
_TAGS = re.compile(r"Input #0.*?Video: h264[^,]*, \w+(?:\(([^)]*)\))?", re.S)


def decode(
    ffmpeg: str, stream: bytes, timeout: float = RUN_S
) -> tuple[str, str, tuple[int, int], bytes]:
    """``stream`` decoded by ffmpeg into raw video in the decoder's own pixel format (nothing
    converted): (that format, what the stream says of its colours, the picture's size, the
    bytes of every picture)."""
    command = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-f", "h264", "-i", "-",
               "-map", "0:v:0", "-f", "rawvideo", "-"]  # fmt: skip
    code, out, err = run(command, stream, timeout)
    found = _DECODED.search(err)
    if code != 0 or not out or found is None:
        raise CheckError(_last_line(err) or f"ffmpeg ended with code {code}")
    tags = _TAGS.search(err)
    said = ", ".join(
        part.strip()
        for part in ((tags.group(1) or "") if tags else "").split(",")
        if part.strip() and part.strip() != "progressive"
    )
    return found.group(1), said, (int(found.group(3)), int(found.group(4))), out


def planes(data: bytes, size: tuple[int, int]) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The Y, U and V planes of every 4:2:0 picture in ``data`` (pictures of ``size``)."""
    width, height = size
    cw, ch = (width + 1) // 2, (height + 1) // 2
    each = width * height + 2 * cw * ch
    if not data or len(data) % each:
        raise CheckError(f"{len(data)} bytes are no whole pictures of {width}x{height}")
    pixels = np.frombuffer(data, np.uint8)
    found = []
    for start in range(0, len(data), each):
        y = pixels[start : start + width * height].reshape(height, width)
        u = pixels[start + width * height : start + width * height + cw * ch].reshape(ch, cw)
        v = pixels[start + width * height + cw * ch : start + each].reshape(ch, cw)
        found.append((y, u, v))
    return found


# the quarter turns that undo the display's turn (np.rot90 turns anticlockwise)
UNDO = {None: 0, 270: 1, 180: 2}


def measure(
    pictures: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    size: tuple[int, int],
    rotate: int | None,
) -> dict[str, tuple[float, float, float]]:
    """The mean Y, U and V of the middle of every patch, over all ``pictures`` (the panel's
    framebuffer, turned by ``rotate``: turned back here into frames of ``size``)."""
    turns = UNDO[rotate]
    width, height = size
    sums: dict[str, np.ndarray] = {}
    for y, u, v in pictures:
        y, u, v = (np.rot90(plane, turns) for plane in (y, u, v))
        if y.shape != (height, width):
            raise CheckError(f"the pictures are {y.shape[1]}x{y.shape[0]}, not {width}x{height}")
        for name, _, box in layout(size):
            x0, y0, x1, y1 = centre(box)
            cx0, cy0, cx1, cy1 = (x0 + 1) // 2, (y0 + 1) // 2, x1 // 2, y1 // 2
            means = np.array([
                y[y0:y1, x0:x1].mean(),
                u[cy0:cy1, cx0:cx1].mean(),
                v[cy0:cy1, cx0:cx1].mean(),
            ])  # fmt: skip
            sums[name] = sums.get(name, 0) + means
    count = max(1, len(pictures))
    return {name: tuple(round(float(x) / count, 2) for x in total) for name, total in sums.items()}


def to_rgb(yuv: Iterable[float], full: bool = False) -> tuple[int, int, int]:
    """The colour the panel shows for ``yuv``: BT.601, the matrix its decoder applies whatever
    the stream says (and the one swscale converts for), in limited range (16-235) or, for a
    panel that shows the video in ``full`` range, 0-255."""
    y, u, v = yuv
    if full:
        cb, cr = u - 128, v - 128
        rgb = (y + 1.402 * cr, y - 0.344136 * cb - 0.714136 * cr, y + 1.772 * cb)
        return tuple(int(min(255, max(0, round(x)))) for x in rgb)  # type: ignore[return-value]
    luma, cb, cr = 1.164383 * (y - 16), u - 128, v - 128
    rgb = (luma + 1.596027 * cr, luma - 0.391762 * cb - 0.812968 * cr, luma + 2.017232 * cb)
    return tuple(int(min(255, max(0, round(x)))) for x in rgb)  # type: ignore[return-value]


def judge(
    new: dict[str, tuple[float, float, float]],
    old: dict[str, tuple[float, float, float]],
    full: bool = False,
) -> dict[str, Any]:
    """The verdict on two feeds' patches, and their largest difference.

    "identical": every patch within :data:`TOLERANCE` in Y, U and V; "level": the greys stay
    grey in both (U and V at 128) but black and white differ in Y (full against limited range);
    "matrix": black, the greys and white agree, the saturated colours differ (BT.709 against
    BT.601); "other": anything else."""
    worst = {"patch": "", "channel": "Y", "new": 0.0, "old": 0.0, "difference": 0.0}
    for name, _ in PATCHES:
        for i, channel in enumerate(CHANNELS):
            diff = abs(new[name][i] - old[name][i])
            if diff > worst["difference"]:
                worst = {"patch": name, "channel": channel, "new": new[name][i],
                         "old": old[name][i], "difference": round(diff, 2)}  # fmt: skip
    worst["rgb"] = max(
        (
            abs(a - b)
            for name, _ in PATCHES
            for a, b in zip(to_rgb(new[name], full), to_rgb(old[name], full), strict=True)
        ),
        default=0,
    )

    def same(name: str) -> bool:
        return all(abs(a - b) <= TOLERANCE for a, b in zip(new[name], old[name], strict=True))

    greys = [name for name, _ in GREYS]
    if all(same(name) for name, _ in PATCHES):
        verdict = "identical"
    elif all(
        abs(side[name][1] - 128) <= TOLERANCE and abs(side[name][2] - 128) <= TOLERANCE
        for side in (new, old)
        for name in greys
    ) and all(abs(new[name][0] - old[name][0]) > TOLERANCE for name in ("black", "white")):
        verdict = "level"
    elif all(same(name) for name in greys):
        verdict = "matrix"
    else:
        verdict = "other"
    return {"verdict": verdict, "worst": worst}


# -- the check ----------------------------------------------------------------------------------


Command = Callable[[str, tuple[int, int], int | None, EncoderSettings], list[str]]


def check_encoder(
    ffmpeg: str,
    encoder: str,
    settings: Callable[[str, str], EncoderSettings],
    size: tuple[int, int],
    rotate: int | None,
    command: Command = ffmpeg_command,
    stop: threading.Event | None = None,
    full: bool = False,
    frames: int = FRAMES,
    timeout: float = RUN_S,
) -> dict[str, Any]:
    """One encoder, both ways (:func:`feeds_of`): what was measured and the verdict.
    ``settings(encoder, pixels)`` are the display's settings for it (a panel in ``full`` range
    gets them so), ``command`` builds its ffmpeg (the display's). "state" is "done", "refused"
    (a card's encoder that gave nothing converting the colours itself, but did with the
    processor's conversion: the display then converts for it by itself), "failed" or
    "stopped"."""
    picture = check_picture(size)
    first, second = feeds_of(encoder, full)
    result: dict[str, Any] = {"encoder": encoder, "state": "done", "verdict": None,
                              "worst": None, "error": "", "feeds": {}, "full": full,
                              "compared": [first, second]}  # fmt: skip
    measured: dict[str, dict[str, tuple[float, float, float]]] = {}
    errors: dict[str, str] = {}
    for pixels in (first, second):
        if stop is not None and stop.is_set():
            return {**result, "state": "stopped"}
        chosen = settings(encoder, pixels)
        feed: dict[str, Any] = {"pixels": chosen.pixels, "card": h264.passes_through(chosen)}
        try:
            made = command(ffmpeg, size, rotate, chosen)
            stream = encode(made, picture, chosen.pixels, frames, timeout)
            decoded, said, native, data = decode(ffmpeg, stream, timeout)
            feed.update(decoded=decoded, says=said)
            if decoded not in FOUR_TWO_ZERO:
                raise CheckError(f"the stream decodes to {decoded}, which the check does not read")
            measured[pixels] = measure(planes(data, native), size, rotate)
        except CheckError as exc:
            if stop is not None and stop.is_set():  # (its ffmpeg was ended: kill_all)
                return {**result, "state": "stopped"}
            errors[pixels] = feed["error"] = str(exc)
            log.info("colour check: %s fed %s: %s", encoder, chosen.pixels, exc)
        result["feeds"][pixels] = feed
    if second not in measured:
        return {**result, "state": "failed", "error": errors.get(second, "")}
    if first not in measured:
        state = "refused" if first == "card" else "failed"
        return {**result, "state": state, "error": errors.get(first, ""),
                "patches": _patches(None, measured[second], full)}  # fmt: skip
    result.update(judge(measured[first], measured[second], full))
    result["patches"] = _patches(measured[first], measured[second], full)
    return result


def conversion_ms(ffmpeg: str, size: tuple[int, int], rotate: int | None) -> float | None:
    """What ffmpeg's conversion of a frame of ``size`` (rgb0, turned as the display turns it)
    into NV12 costs the processor, in ms: the time of a run that converts less one that does
    not, by ffmpeg's own count (``-benchmark``: user and system time), the lesser of two each;
    a source of ffmpeg's own feeds them (no pipe to count). None where it cannot say."""
    width, height = size
    frames = 100
    turn = ROTATION_FILTERS.get(rotate)
    chain = ",".join(part for part in ("format=rgb0", turn) if part)

    def seconds(convert: bool) -> float | None:
        command = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-benchmark",
                   "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=50", "-vf", chain,
                   *(["-pix_fmt", "nv12"] if convert else []), "-frames:v", str(frames),
                   "-f", "null", "-"]  # fmt: skip
        try:
            code, _, said = run(command, b"")
        except CheckError:
            return None
        found = _BENCH.search(said)
        if code != 0 or found is None:
            return None
        return float(found.group(1)) + float(found.group(2))

    best: dict[bool, float] = {}
    for convert in (True, False, True, False):
        took = seconds(convert)
        if took is None:
            return None
        best[convert] = min(took, best.get(convert, took))
    return round(max(0.0, best[True] - best[False]) / frames * 1000, 1)


_BENCH = re.compile(r"bench: utime=([\d.]+)s stime=([\d.]+)s")


def _patches(new: dict | None, old: dict, full: bool = False) -> list[dict[str, Any]]:
    found = []
    for name, rgb in PATCHES:
        entry: dict[str, Any] = {"id": name, "rgb": list(rgb)}
        for side, values in (("new", new), ("old", old)):
            if values is not None:
                shown = to_rgb(values[name], full)
                entry[side] = {"yuv": list(values[name]), "rgb": list(shown)}
        found.append(entry)
    return found


def check(
    ffmpeg: str,
    settings: Callable[[str, str], EncoderSettings],
    size: tuple[int, int],
    rotate: int | None,
    encoders: Iterable[str] = ENCODERS,
    works: Callable[[str, str], bool] | None = None,
    command: Command = ffmpeg_command,
    stop: threading.Event | None = None,
    done: Callable[[dict[str, Any]], None] | None = None,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Every encoder in turn (:func:`check_encoder`); one of the card's that ffmpeg cannot start
    here (``works``: :func:`~h264.encoder_works`, the probe the video mode's "auto" goes by) is
    "unavailable", without a run, with what ffmpeg said in the probe. ``done`` hears of each
    result as it comes."""
    works = works or h264.encoder_works
    results = []
    for encoder in encoders:
        if stop is not None and stop.is_set():
            break
        if encoder != "libx264" and not works(ffmpeg, encoder):
            # (why: the last line ffmpeg said when the video mode's own command was probed)
            said = h264.probe_said(ffmpeg, encoder)
            result = {"encoder": encoder, "state": "unavailable", "verdict": None, "worst": None,
                      "error": "", "said": said, "feeds": {}}  # fmt: skip
        else:
            result = check_encoder(ffmpeg, encoder, settings, size, rotate, command, stop, full)
        if result["state"] == "stopped":
            break
        log.info(
            "colour check: %s %s%s",
            encoder,
            result["verdict"] or result["state"],
            f" (largest difference {result['worst']['difference']:g} in "
            f"{result['worst']['channel']} of {result['worst']['patch']})"
            if result.get("worst") and result["worst"]["patch"]
            else "",
        )
        results.append(result)
        if done is not None:
            done(result)
    return results

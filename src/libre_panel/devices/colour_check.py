"""Whether a graphics card turns the frames' colours into video as the processor does.

Settings → Panel → Check the colours. Since the video mode feeds ffmpeg four bytes a pixel
(``video.pixels = "auto"``, rgb0), AMD's and NVIDIA's encoders convert RGB to YUV on the card,
told to do it as swscale does on the processor (BT.601, limited range); fed three bytes a pixel
(``"rgb"``, rgb24) ffmpeg converts on the processor for every encoder, the way of before. Only the
PC with the card can show whether the two agree, so the check measures it there, away from the
panel:

- a test picture at the size of the panel's frames (:func:`check_picture`): large flat patches of
  black, five greys, white, pure red, green and blue, the themes' accent and violet, an orange
  and a skin tone;
- for every encoder of the card that ffmpeg can start here (:func:`~h264.encoder_works`) and for
  x264, ten frames of it through the very command the display builds (:func:`~h264.ffmpeg_command`
  with the display's settings and its turn into the panel's framebuffer), once fed as
  ``"auto"`` feeds them and once as ``"rgb"`` does;
- each stream decoded by ffmpeg to raw 4:2:0 YUV without any conversion (the decoder's own pixel
  format, which the result names), turned back, and the mean Y, U and V of the middle of every
  patch (half its width and height: away from its edges, where chroma and blocks blur);
- the two feeds compared patch by patch (:func:`judge`): **identical** when every patch is within
  ±2 code values; a **level shift** when the greys stay grey in both but black and white differ
  (full against limited range); a **matrix shift** when the greys agree and the saturated colours
  differ (BT.709 against BT.601); else **other**. Both are turned into the RGB the panel shows,
  with BT.601 in limited range (:func:`to_rgb`): the panel's decoder applies one fixed matrix
  whatever the stream says of itself, so equal YUV is equal colours on the panel.

Every run is an ffmpeg of its own with a time limit, one after the other (never two sessions of
the card's encoder at once from here), killed when it outlasts it or the check ends; the
running panel and its ffmpeg are not touched.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
from collections.abc import Callable, Iterable
from functools import lru_cache
from typing import Any

import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont

from libre_panel.devices import h264
from libre_panel.devices.h264 import NO_WINDOW, PASS_THROUGH, EncoderSettings, ffmpeg_command

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
FEEDS = ("auto", "rgb")  # video.pixels: the new feed (rgb0), the old one (rgb24)
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
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def encode(command: list[str], picture: Image.Image, pixels: str, frames: int = FRAMES) -> bytes:
    """``frames`` frames of ``picture`` through ``command`` (an ffmpeg of the display's),
    packed as ``pixels`` ("rgb0", "rgb24") as the display packs them; its H.264 (Annex B)."""
    from libre_panel.devices.turzx_video import frame_bytes

    raw = frame_bytes(picture, pixels)
    code, out, err = run(command, raw * frames)
    if code != 0 or not out:
        raise CheckError(_last_line(err) or f"ffmpeg ended with code {code}")
    return out


_DECODED = re.compile(r"Output #0.*?Video: rawvideo[^,]*, (\w+)(?:\(([^)]*)\))?, (\d+)x(\d+)", re.S)
_TAGS = re.compile(r"Input #0.*?Video: h264[^,]*, \w+(?:\(([^)]*)\))?", re.S)


def decode(ffmpeg: str, stream: bytes) -> tuple[str, str, tuple[int, int], bytes]:
    """``stream`` decoded by ffmpeg into raw video in the decoder's own pixel format (nothing
    converted): (that format, what the stream says of its colours, the picture's size, the
    bytes of every picture)."""
    command = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-f", "h264", "-i", "-",
               "-map", "0:v:0", "-f", "rawvideo", "-"]  # fmt: skip
    code, out, err = run(command, stream)
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


def to_rgb(yuv: Iterable[float]) -> tuple[int, int, int]:
    """The colour the panel shows for ``yuv``: BT.601 in limited range, the matrix its decoder
    applies whatever the stream says (and the one swscale converted for before)."""
    y, u, v = yuv
    luma, cb, cr = 1.164383 * (y - 16), u - 128, v - 128
    rgb = (luma + 1.596027 * cr, luma - 0.391762 * cb - 0.812968 * cr, luma + 2.017232 * cb)
    return tuple(int(min(255, max(0, round(x)))) for x in rgb)  # type: ignore[return-value]


def judge(
    new: dict[str, tuple[float, float, float]], old: dict[str, tuple[float, float, float]]
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
            for a, b in zip(to_rgb(new[name]), to_rgb(old[name]), strict=True)
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
) -> dict[str, Any]:
    """One encoder, both feeds: what was measured and the verdict. ``settings(encoder,
    pixels)`` are the display's settings for it, ``command`` builds its ffmpeg (the display's).
    "state" is "done", "refused" (fed four bytes a pixel the encoder gave nothing, three it
    did: the display then feeds it three by itself), "failed" or "stopped"."""
    picture = check_picture(size)
    result: dict[str, Any] = {"encoder": encoder, "state": "done", "verdict": None,
                              "worst": None, "error": "", "feeds": {}}  # fmt: skip
    measured: dict[str, dict[str, tuple[float, float, float]]] = {}
    errors: dict[str, str] = {}
    for pixels in FEEDS:
        if stop is not None and stop.is_set():
            return {**result, "state": "stopped"}
        chosen = settings(encoder, pixels)
        feed: dict[str, Any] = {"pixels": chosen.pixels, "card": h264.passes_through(chosen)}
        try:
            stream = encode(command(ffmpeg, size, rotate, chosen), picture, chosen.pixels)
            decoded, said, native, data = decode(ffmpeg, stream)
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
    if "rgb" not in measured:
        return {**result, "state": "failed", "error": errors.get("rgb", "")}
    if "auto" not in measured:
        state = "refused" if encoder in PASS_THROUGH else "failed"
        return {**result, "state": state, "error": errors.get("auto", ""),
                "patches": _patches(None, measured["rgb"])}  # fmt: skip
    result.update(judge(measured["auto"], measured["rgb"]))
    result["patches"] = _patches(measured["auto"], measured["rgb"])
    return result


def _patches(new: dict | None, old: dict) -> list[dict[str, Any]]:
    found = []
    for name, rgb in PATCHES:
        entry: dict[str, Any] = {"id": name, "rgb": list(rgb)}
        for side, values in (("new", new), ("old", old)):
            if values is not None:
                entry[side] = {"yuv": list(values[name]), "rgb": list(to_rgb(values[name]))}
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
) -> list[dict[str, Any]]:
    """Every encoder in turn (:func:`check_encoder`); one of the card's that ffmpeg cannot start
    here (``works``: :func:`~h264.encoder_works`) is "unavailable", without a run. ``done`` hears
    of each result as it comes."""
    works = works or h264.encoder_works
    results = []
    for encoder in encoders:
        if stop is not None and stop.is_set():
            break
        if encoder != "libx264" and not works(ffmpeg, encoder):
            result = {"encoder": encoder, "state": "unavailable", "verdict": None,
                      "worst": None, "error": "", "feeds": {}}  # fmt: skip
        else:
            result = check_encoder(ffmpeg, encoder, settings, size, rotate, command, stop)
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

"""The panel's own screens on a TURZX USB panel: its boot logo and its standby clip.

The panel shows a JPEG when it is powered on (``usr/data/boot.jpg``, else its factory logo,
which is never written) and plays an H.264 clip in a loop when the PC's USB comes up, until a
program streams (the name kept in its settings; the vendor app's ``usr/data/standby.h264``).
render/bootscreen.py draws both in Libre Panel's look; this puts them on the panel. What it
does comes from SPUR II's reading of the 8.8"'s firmware and its measurements on the 9.2":

- **40** writes a file: ``[8:12]`` the name's length, ``[12:16]`` the file's, ``[16:]`` the
  name; the file follows in the same transfer, at most 1 MiB. **42** deletes one, **98** says
  its size (little endian at ``[8:12]``), **99** lists a folder in 20 pieces of 512 bytes (all
  of them, or the next listing does not begin afresh), **113** shows a JPEG of the panel's.
- **15** sets the player's rate (a cold start sets 30), **110** plays a clip and keeps its name
  in the panel's settings in memory, **125** saves the settings: brightness, start mode (2: the
  clip), rotation, with the name 110 gave.

Its safeguards: only on the person's own action, never while the panel shows themes (the
app pauses it first); names only below ``usr/data/``, relative, without ``..``; at most
1 MiB a file; after writing, the size is asked and compared. 12 (halt), 13 and 11 are never
sent from here, and 110 only with the clip's real name (an empty one, saved, would lose the
standby clip for good).
"""

from __future__ import annotations

import io
import logging
import struct
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image

from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import MODELS, PanelModel
from libre_panel.devices.turzx import VENDOR_CLIP
from libre_panel.devices.turzx_usb import (
    ACK,
    CMD_FRAME_RATE,
    USB_PIDS,
    UsbTransport,
    brightness_arg,
    to_native,
)
from libre_panel.i18n import t
from libre_panel.render import bootscreen

log = logging.getLogger(__name__)

FOLDER = "usr/data"
BOOT_LOGO = "usr/data/boot.jpg"
STANDBY_CLIP = VENDOR_CLIP  # the vendor app's name, which the video mode names too
MAX_FILE = 1024 * 1024
LIST_PIECES = 20
PIECE = 512
# the boot logo's JPEG: (quality, subsampling) in turn until it fits (0: a colour every pixel)
JPEG_STEPS = ((95, 0), (92, 0), (90, 2), (85, 2), (75, 2), (60, 2))
# the standby clip's x264 crf: this first, then coarser in steps of 2 up to the most where it does
# not fit in CLIP_ROOM, or the finest where it fills less than half of it
CLIP_CRF, CLIP_CRF_MIN, CLIP_CRF_MAX = 20, 17, 30
CLIP_ROOM = MAX_FILE

CMD_WRITE_FILE = 40
CMD_DELETE_FILE = 42
CMD_FILE_SIZE = 98
CMD_LIST = 99
CMD_PLAY_LOCAL = 110
CMD_SHOW_JPEG = 113
CMD_SAVE_SETTINGS = 125
START_WITH_CLIP = 2  # the settings' start mode: play the clip they name
NEVER = frozenset({11, 12, 13})  # restart, halt, and the other save of the settings


def models() -> list[PanelModel]:
    """The panels this works on: the TURZX USB ones."""
    return [model for model in MODELS if model.protocol == "usb-turing"]


def checked_name(name: str) -> bytes:
    """A file name as the panel takes it: relative, below ``usr/data/``, no ``..``."""
    if not isinstance(name, str) or not name.startswith(FOLDER + "/") or name.startswith("/"):
        raise DeviceError(f"{name!r}: only files below {FOLDER}/ are written")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts) or "\\" in name:
        raise DeviceError(f"{name!r}: not a plain file name")
    raw = name.encode("ascii", errors="strict") if name.isascii() else b""
    if not raw or len(raw) > 200 or not all(32 < byte < 127 for byte in raw):
        raise DeviceError(f"{name!r}: not a plain file name")
    return raw


def file_args(name: bytes, size: int = 0) -> bytes:
    return struct.pack(">I", len(name)) + struct.pack(">I", size) + name


# -- the pictures, as the panel takes them ------------------------------------------------------


def logo_jpeg(
    model: PanelModel, picture: Image.Image | None = None, orientation: str = "landscape",
    design: str = bootscreen.DEFAULT,
) -> bytes:  # fmt: skip
    """The boot logo for ``model`` (drawn upright for a bar on end with ``orientation``
    "portrait", in the look ``design``) as the JPEG the panel shows: turned into its
    framebuffer (480 x 1920 on the 9.2"), at quality 95 with every pixel's colour (a dark
    gradient's blocks showed at 88 with a colour for four), less where it would not fit."""
    native = to_native(picture or bootscreen.boot_logo(model, orientation, design)).convert("RGB")
    for quality, subsampling in JPEG_STEPS:
        buffer = io.BytesIO()
        native.save(buffer, format="JPEG", quality=quality, subsampling=subsampling,
                    optimize=True)  # fmt: skip
        if buffer.tell() <= MAX_FILE:
            return buffer.getvalue()
    raise DeviceError("the boot logo does not fit in 1 MiB")


def clip_command(ffmpeg: str, size: tuple[int, int], crf: int = CLIP_CRF,
                 full: bool = False) -> list[str]:  # fmt: skip
    """ffmpeg for the standby clip, as the panel's decoder plays it: one slice a picture, a
    sequence (headers and a keyframe) every 11 pictures, baseline, no B pictures; in full range
    for a panel whose decoder shows every stream so (``full``: the 9.2" and the 12.3" showed
    the clip of before under a grey veil), and with the dark parts kept as smooth as the light
    ones (aq-mode 3)."""
    from libre_panel.devices.h264 import FULL_RANGE, colour_tags, stamp

    width, height = size
    convert = f"{FULL_RANGE},format=yuv420p" if full else "format=yuv420p"
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-framerate", str(bootscreen.CLIP_FPS), "-i", "-", "-an", "-vf", convert,
        "-c:v", "libx264", "-profile:v", "baseline", "-preset", "slow", "-threads", "1",
        "-crf", str(crf), *colour_tags(full),
        "-x264-params",
        "sliced-threads=0:bframes=0:scenecut=0:keyint=11:min-keyint=11:repeat-headers=1"
        ":aq-mode=3",
        *stamp(full), "-f", "h264", "-",
    ]  # fmt: skip


def clip_h264(
    model: PanelModel,
    ffmpeg: str,
    frames: list[Image.Image] | None = None,
    orientation: str = "landscape",
    design: str = bootscreen.DEFAULT,
    full: bool = False,
) -> bytes:
    """The standby clip for ``model`` (upright with ``orientation`` "portrait", in the look
    ``design``, in full range with ``full``): its pictures turned into the framebuffer and
    encoded as finely as fits in 1 MiB: at crf 20, coarser in steps where it does not fit, and
    finer where it leaves most of the room."""
    from libre_panel.devices.h264 import slice_count

    pictures = frames or bootscreen.standby_frames(model, orientation=orientation, design=design)
    native = [to_native(frame).convert("RGB") for frame in pictures]
    raw = b"".join(frame.tobytes() for frame in native)

    def encoded(crf: int) -> bytes:
        try:
            done = subprocess.run(
                clip_command(ffmpeg, native[0].size, crf, full), input=raw,
                capture_output=True, timeout=300, check=False,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeviceError(f"ffmpeg could not make the standby clip: {exc}") from exc
        if done.returncode != 0 or not done.stdout:
            raise DeviceError(f"ffmpeg could not make the standby clip: {done.stderr[-300:]!r}")
        return done.stdout

    crf = CLIP_CRF
    data = encoded(crf)
    while len(data) > CLIP_ROOM and crf < CLIP_CRF_MAX:
        crf += 2
        data = encoded(crf)
    if len(data) > CLIP_ROOM:
        raise DeviceError("the standby clip does not fit in 1 MiB")
    if len(data) < CLIP_ROOM * 0.4 and crf == CLIP_CRF:  # room to spare: finer
        finer = encoded(CLIP_CRF_MIN)
        data = finer if len(finer) <= CLIP_ROOM else data
    if slice_count(data) != len(native):  # one slice a picture, or the panel shows nothing
        raise DeviceError("the standby clip came out with more than one slice a picture")
    return data


# -- on the panel -------------------------------------------------------------------------------


@dataclass
class PanelFiles:
    """The file commands on an open panel (``transport``: a synced UsbTransport)."""

    transport: Any

    def _command(self, cmd: int, args: bytes = b"", payload: bytes = b"",
                 timeout_ms: int = 4000) -> bytes:  # fmt: skip
        if cmd in NEVER:
            raise DeviceError(f"command {cmd} is never sent")
        return self.transport.command(cmd, args, payload, timeout_ms=timeout_ms)

    def listing(self, folder: str = FOLDER) -> list[str]:
        """The names in ``folder``, as the panel lists them (all 20 pieces are fetched)."""
        name = folder.encode("ascii")
        if folder != FOLDER:
            raise DeviceError(f"only {FOLDER} is listed")
        pieces = []
        for n in range(LIST_PIECES):
            # a piece of the listing's buffer: the 9.2" sends it as it is, without the
            # command's number in front (its first piece began with zeros); where that header
            # does come, the piece follows it
            reply = bytes(self.transport.ask(CMD_LIST, file_args(name), echo=False))
            if n == 0:
                log.debug("start screen: listing %s begins %s", folder, reply[:32].hex(" "))
            header = reply[:1] == bytes([CMD_LIST]) and reply[1:2] == bytes([ACK])
            pieces.append(reply[8 : 8 + PIECE] if header else reply[:PIECE])
        return names_in(b"".join(pieces))

    def noted_listing(self) -> list[str] | None:
        """The listing before a file is written, for the log; a panel that does not give it
        does not stop the writing (what was left unread is dropped)."""
        try:
            names = self.listing()
        except DeviceError as exc:
            log.warning("start screen: the panel's files could not be listed: %s", exc)
            self.transport.drain()
            return None
        log.info("start screen: %s holds %s", FOLDER, ", ".join(names) or "nothing listed")
        return names

    def size(self, name: str) -> int:
        """The size of a file of the panel's (0: none)."""
        reply = self.transport.ask(CMD_FILE_SIZE, file_args(checked_name(name)))
        size = struct.unpack("<I", bytes(reply[8:12]))[0]
        return 0 if size == 0xFFFFFFFF else size

    def write(self, name: str, data: bytes) -> None:
        """Write a file, and check that it has the size it should."""
        raw = checked_name(name)
        if not data or len(data) > MAX_FILE:
            raise DeviceError(f"{name}: a file is 1 byte to 1 MiB, not {len(data)}")
        self._command(CMD_WRITE_FILE, file_args(raw, len(data)), data, timeout_ms=20000)
        written = self.size(name)
        if written != len(data):
            raise DeviceError(f"{name}: the panel has {written} bytes of it, not {len(data)}")

    def delete(self, name: str) -> None:
        self._command(CMD_DELETE_FILE, file_args(checked_name(name)))

    def show_jpeg(self, name: str) -> None:
        self._command(CMD_SHOW_JPEG, file_args(checked_name(name)))

    def play(self, name: str) -> None:
        """Play a clip of the panel's at the rate of a cold start; its name goes into the
        panel's settings (in memory, until :meth:`save_start`)."""
        raw = checked_name(name)  # never empty: an empty name, saved, loses the standby clip
        self._command(CMD_FRAME_RATE, bytes([bootscreen.CLIP_FPS]))
        self._command(CMD_PLAY_LOCAL, file_args(raw))

    def save_start(self, brightness: int) -> None:
        """Save the panel's settings: its brightness, to start with the clip :meth:`play`
        named, unturned (Libre Panel turns its pictures itself), and to dim when the PC has
        been gone for 10 s."""
        self._command(CMD_SAVE_SETTINGS, bytes([brightness_arg(brightness), START_WITH_CLIP,
                                                0, 0, 0, 0]))  # fmt: skip


def names_in(listing: bytes) -> list[str]:
    """The file names in a folder's listing as the panel gives it: the plain names in the
    text, in order, once each."""
    import re

    text = listing.replace(b"\x00", b"\n").decode("latin-1")
    found = []
    for token in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*\.[A-Za-z0-9]+", text):
        if token not in found:
            found.append(token)
    return found


# -- what the app asks for ----------------------------------------------------------------------


Opener = Callable[..., tuple[Any, PanelModel]]
# Which panel: what the other panels name, and how many alike to pass over (UsbTransport.open)
Where = tuple[tuple[str, ...], int]
FIRST: Where = ((), 0)


def open_panel(
    model: PanelModel | None = None, beside: tuple[str, ...] = (), skip: int = 0
) -> tuple[UsbTransport, PanelModel]:
    """The TURZX USB panel on the bus (``model`` if given), opened and in step; ``beside`` and
    ``skip`` find a panel beside the first as its loop does."""
    pid = model.usb_ids[0][1] if model is not None and model.usb_ids else None
    transport = UsbTransport.open(pid, beside, skip)
    found = USB_PIDS.get(transport.pid) or model
    if found is None:
        transport.close()
        raise DeviceError(t("No TURZX USB panel found"))
    try:
        transport.sync()
    except DeviceError:
        transport.close()
        raise
    return transport, found


class StartScreen:
    """The jobs of the app's *Start screen*: what is on the panel, the boot logo written or
    taken away, the standby clip written and saved. Each opens the panel and closes it again;
    the app pauses its themes first. ``opener`` and ``ffmpeg`` are for the tests."""

    def __init__(self, opener: Opener = open_panel, ffmpeg: Callable[[], str] | None = None):
        self.opener = opener
        self.ffmpeg = ffmpeg

    def _with_panel(self, model: PanelModel | None, job: Callable[[PanelFiles, PanelModel], Any],
                    where: Where = FIRST):  # fmt: skip
        transport, found = self.opener(model) if where == FIRST else self.opener(model, *where)
        try:
            return job(PanelFiles(transport), found)
        finally:
            transport.close()

    def state(self, model: PanelModel | None = None, where: Where = FIRST) -> dict[str, Any]:
        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            return {
                "model": found.id,
                "files": files.noted_listing(),  # (None: the panel did not list them)
                "boot_logo": files.size(BOOT_LOGO),
                "standby_clip": files.size(STANDBY_CLIP),
            }

        return self._with_panel(model, job, where)

    def write_logo(
        self, model: PanelModel | None = None, orientation: str = "landscape",
        where: Where = FIRST, design: str = bootscreen.DEFAULT,
    ) -> dict[str, Any]:  # fmt: skip
        """The boot logo written and shown (it shows at the next cold start)."""

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            data = logo_jpeg(found, orientation=orientation, design=design)
            files.noted_listing()  # what is there before, in the log of the panel's folder
            files.write(BOOT_LOGO, data)
            files.show_jpeg(BOOT_LOGO)
            return {"model": found.id, "boot_logo": len(data), "design": design}

        return self._with_panel(model, job, where)

    def remove_logo(self, model: PanelModel | None = None, where: Where = FIRST) -> dict[str, Any]:
        """The boot logo taken away: the factory logo shows again at the next cold start."""

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            files.delete(BOOT_LOGO)
            return {"model": found.id, "boot_logo": files.size(BOOT_LOGO)}

        return self._with_panel(model, job, where)

    def write_clip(
        self, brightness: int, model: PanelModel | None = None, orientation: str = "landscape",
        where: Where = FIRST, design: str = bootscreen.DEFAULT, video_range: str = "auto",
    ) -> dict[str, Any]:  # fmt: skip
        """The standby clip written, played (as it will be) and saved as what the panel
        starts with; in the range of values its decoder shows ([device] video_range, "auto"
        as its model is known to)."""
        from libre_panel.devices.h264 import find_ffmpeg
        from libre_panel.devices.turzx_video import full_range

        ffmpeg = self.ffmpeg() if self.ffmpeg else find_ffmpeg()

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            data = clip_h264(found, ffmpeg, orientation=orientation, design=design,
                             full=full_range(video_range, found))  # fmt: skip
            files.noted_listing()
            files.write(STANDBY_CLIP, data)
            files.play(STANDBY_CLIP)
            files.save_start(brightness)
            return {"model": found.id, "standby_clip": len(data), "name": STANDBY_CLIP,
                    "design": design}  # fmt: skip

        return self._with_panel(model, job, where)


def _preview_scale(model: PanelModel, width: int, orientation: str) -> float:
    """How much smaller the app's picture is drawn than the panel's (at most as large)."""
    return min(1.0, width / bootscreen.design_size(model, orientation)[0])


def preview_png(model: PanelModel, width: int = 960, orientation: str = "landscape",
                design: str = bootscreen.DEFAULT) -> bytes:  # fmt: skip
    """The boot logo as a PNG for the app, ``width`` px wide (upright for a bar on end), drawn
    at that size."""
    scale = _preview_scale(model, width, orientation)
    picture = bootscreen.boot_logo(model, orientation, design, scale=scale)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def preview_clip(model: PanelModel, width: int = 960, orientation: str = "landscape",
                 design: str = bootscreen.DEFAULT) -> bytes:  # fmt: skip
    """The standby clip as an animated WebP for the app (an animated PNG where Pillow has no
    WebP), ``width`` px wide, at its rate, drawn at that size: a clip whose light moves all
    over (Aurora's) came to 14 MB as a PNG, and to a few hundred KB so."""
    from PIL import features

    scale = _preview_scale(model, width, orientation)
    frames = bootscreen.standby_frames(model, orientation=orientation, design=design, scale=scale)
    buffer = io.BytesIO()
    timing = {"save_all": True, "append_images": frames[1:], "loop": 0,
              "duration": round(1000 / bootscreen.CLIP_FPS)}  # fmt: skip
    if features.check("webp"):
        frames[0].save(buffer, format="WEBP", quality=90, method=4, **timing)
    else:
        frames[0].save(buffer, format="PNG", **timing)
    return buffer.getvalue()


def picture_type(data: bytes) -> str:
    """The media type of a picture of :func:`cached_preview`."""
    return "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "image/png"


def model_of(model_id: str) -> PanelModel:
    """A TURZX USB panel by its id."""
    found = next((model for model in models() if model.id == model_id), None)
    if found is None:
        raise DeviceError(f"{model_id!r} is no TURZX USB panel")
    return found


def design_of(name: str | None) -> str:
    """A look of the panel's own screens by its name (render/bootscreen.py), Libre Panel's own
    for none; DeviceError for one there is not."""
    try:
        return bootscreen.design_of(name).id
    except KeyError:
        raise DeviceError(f"{name!r} is no look of the start screen") from None


@lru_cache(maxsize=16)
def cached_preview(model_id: str, what: str, width: int, orientation: str = "landscape",
                   design: str = bootscreen.DEFAULT) -> bytes:  # fmt: skip
    """The app's picture of the boot logo (PNG) or of the standby clip (animated)."""
    model = model_of(model_id)
    if what == "clip":
        return preview_clip(model, width, orientation, design)
    return preview_png(model, width, orientation, design)


@lru_cache(maxsize=4)
def cached_file(model_id: str, what: str, orientation: str = "landscape",
                design: str = bootscreen.DEFAULT, full: bool = False) -> bytes:  # fmt: skip
    """The boot logo's JPEG or the standby clip as the panel takes them (in full range with
    ``full``), to keep or to put on the panel with another program."""
    from libre_panel.devices.h264 import find_ffmpeg

    model = model_of(model_id)
    if what == "clip":
        return clip_h264(model, find_ffmpeg(), orientation=orientation, design=design, full=full)
    return logo_jpeg(model, orientation=orientation, design=design)

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
JPEG_QUALITY = 88

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
    model: PanelModel, picture: Image.Image | None = None, orientation: str = "landscape"
) -> bytes:
    """The boot logo for ``model`` (drawn upright for a bar on end with ``orientation``
    "portrait") as the JPEG the panel shows: turned into its framebuffer (480 x 1920 on the
    9.2"), quality 88 (less where it would not fit in 1 MiB)."""
    native = to_native(picture or bootscreen.boot_logo(model, orientation)).convert("RGB")
    for quality in (JPEG_QUALITY, 80, 70, 60):
        buffer = io.BytesIO()
        native.save(buffer, format="JPEG", quality=quality, optimize=True)
        if buffer.tell() <= MAX_FILE:
            return buffer.getvalue()
    raise DeviceError("the boot logo does not fit in 1 MiB")


def clip_command(ffmpeg: str, size: tuple[int, int]) -> list[str]:
    """ffmpeg for the standby clip, as the panel's decoder plays it: one slice a picture, a
    sequence (headers and a keyframe) every 11 pictures, baseline, no B pictures."""
    width, height = size
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-framerate", str(bootscreen.CLIP_FPS), "-i", "-", "-an",
        "-c:v", "libx264", "-profile:v", "baseline", "-preset", "veryslow", "-threads", "1",
        "-crf", "23", "-pix_fmt", "yuv420p",
        "-x264-params",
        "sliced-threads=0:bframes=0:scenecut=0:keyint=11:min-keyint=11:repeat-headers=1",
        "-f", "h264", "-",
    ]  # fmt: skip


def clip_h264(
    model: PanelModel,
    ffmpeg: str,
    frames: list[Image.Image] | None = None,
    orientation: str = "landscape",
) -> bytes:
    """The standby clip for ``model`` (upright with ``orientation`` "portrait"): its pictures
    turned into the framebuffer and encoded."""
    from libre_panel.devices.h264 import slice_count

    pictures = frames or bootscreen.standby_frames(model, orientation=orientation)
    native = [to_native(frame).convert("RGB") for frame in pictures]
    raw = b"".join(frame.tobytes() for frame in native)
    try:
        done = subprocess.run(
            clip_command(ffmpeg, native[0].size), input=raw, capture_output=True, timeout=120,
            check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeviceError(f"ffmpeg could not make the standby clip: {exc}") from exc
    if done.returncode != 0 or not done.stdout:
        raise DeviceError(f"ffmpeg could not make the standby clip: {done.stderr[-300:]!r}")
    data = done.stdout
    if len(data) > MAX_FILE:
        raise DeviceError("the standby clip does not fit in 1 MiB")
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
        where: Where = FIRST,
    ) -> dict[str, Any]:  # fmt: skip
        """The boot logo written and shown (it shows at the next cold start)."""

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            data = logo_jpeg(found, orientation=orientation)
            files.noted_listing()  # what is there before, in the log of the panel's folder
            files.write(BOOT_LOGO, data)
            files.show_jpeg(BOOT_LOGO)
            return {"model": found.id, "boot_logo": len(data)}

        return self._with_panel(model, job, where)

    def remove_logo(self, model: PanelModel | None = None, where: Where = FIRST) -> dict[str, Any]:
        """The boot logo taken away: the factory logo shows again at the next cold start."""

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            files.delete(BOOT_LOGO)
            return {"model": found.id, "boot_logo": files.size(BOOT_LOGO)}

        return self._with_panel(model, job, where)

    def write_clip(
        self, brightness: int, model: PanelModel | None = None, orientation: str = "landscape",
        where: Where = FIRST,
    ) -> dict[str, Any]:  # fmt: skip
        """The standby clip written, played (as it will be) and saved as what the panel
        starts with."""
        from libre_panel.devices.h264 import find_ffmpeg

        ffmpeg = self.ffmpeg() if self.ffmpeg else find_ffmpeg()

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            data = clip_h264(found, ffmpeg, orientation=orientation)
            files.noted_listing()
            files.write(STANDBY_CLIP, data)
            files.play(STANDBY_CLIP)
            files.save_start(brightness)
            return {"model": found.id, "standby_clip": len(data), "name": STANDBY_CLIP}

        return self._with_panel(model, job, where)


def preview_png(model: PanelModel, width: int = 960, orientation: str = "landscape") -> bytes:
    """The boot logo as a PNG for the app, ``width`` px wide (upright for a bar on end)."""
    picture = bootscreen.boot_logo(model, orientation)
    scale = min(1.0, width / picture.width)
    size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
    buffer = io.BytesIO()
    picture.resize(size, Image.Resampling.LANCZOS).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def preview_clip(model: PanelModel, width: int = 960, orientation: str = "landscape") -> bytes:
    """The standby clip as an animated PNG for the app, ``width`` px wide, at its rate."""
    frames = bootscreen.standby_frames(model, orientation=orientation)
    scale = min(1.0, width / frames[0].width)
    size = (max(1, round(frames[0].width * scale)), max(1, round(frames[0].height * scale)))
    small = [frame.resize(size, Image.Resampling.LANCZOS) for frame in frames]
    buffer = io.BytesIO()
    small[0].save(buffer, format="PNG", save_all=True, append_images=small[1:],
                  duration=round(1000 / bootscreen.CLIP_FPS), loop=0)  # fmt: skip
    return buffer.getvalue()


def model_of(model_id: str) -> PanelModel:
    """A TURZX USB panel by its id."""
    found = next((model for model in models() if model.id == model_id), None)
    if found is None:
        raise DeviceError(f"{model_id!r} is no TURZX USB panel")
    return found


@lru_cache(maxsize=8)
def cached_preview(model_id: str, what: str, width: int, orientation: str = "landscape") -> bytes:
    """The app's picture of the boot logo (PNG) or of the standby clip (an animated PNG)."""
    model = model_of(model_id)
    if what == "clip":
        return preview_clip(model, width, orientation)
    return preview_png(model, width, orientation)


@lru_cache(maxsize=4)
def cached_file(model_id: str, what: str, orientation: str = "landscape") -> bytes:
    """The boot logo's JPEG or the standby clip as the panel takes them, to keep or to put
    on the panel with another program."""
    from libre_panel.devices.h264 import find_ffmpeg

    model = model_of(model_id)
    if what == "clip":
        return clip_h264(model, find_ffmpeg(), orientation=orientation)
    return logo_jpeg(model, orientation=orientation)

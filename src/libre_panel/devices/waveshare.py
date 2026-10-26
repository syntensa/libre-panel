"""Waveshare's USB monitors (the 2.8" and its family): a small Linux computer behind a serial port
over USB, which draws the themes on its own card (PCMonitorLinux) and takes from the PC only
the values to show in them. Libre Panel sends those (services/waveshare.py), with the
brightness and the screen turned upside down; it writes nothing on the card.

The protocol, as Derik-for-real24 documented it in the wsmonitor project (#21, NOTICE.md):
every message is a frame

    "AA551234 FIXEDCMDHEAD "  22 bytes
    type, command, length, CRC-32 of the payload   four little-endian uint32
    payload

with type 0 from the PC and 2 for the display's answer (it answers every command). Most
payloads are a ``QMap<QString, QString>`` as Qt's QDataStream writes it: the count, then each
key and value as a big-endian byte length and UTF-16BE text. Brightness takes the number as
plain text, 0 to 255 (as a capture of Waveshare's own program showed, #21).

The display has commands that write files on its card and delete themes; this module has none
of them. A port is the display only once it answered the handshake (``FIND_DEVICE``) with a
frame of its own: its ids (``1d6b:0104``, or ``1234:5678`` that other devices use too) only
say where to ask.
"""

from __future__ import annotations

import struct
import sys
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from libre_panel.devices.base import DeviceError, place_taken

MAGIC = b"AA551234 FIXEDCMDHEAD "
HEAD = struct.Struct("<4I")  # type, command, length, crc
TO_DISPLAY, ANSWER = 0, 2
MAX_PAYLOAD = 1 << 20  # a longer length in a head is not the display's

# the commands Libre Panel sends: none of them writes on the card
FIND_DEVICE = 0
SYSTEM_DATA = 1
SET_BRIGHTNESS = 4
GET_VERSION = 8
SET_CANVAS_FLIP = 9
GET_SCREEN_MESSAGE = 10
COMMANDS = frozenset({FIND_DEVICE, SYSTEM_DATA, SET_BRIGHTNESS, GET_VERSION, SET_CANVAS_FLIP,
                      GET_SCREEN_MESSAGE})  # fmt: skip

# where the display may be: the USB gadget's own ids (with its product's name), and the
# placeholder ids some units have, which other devices use too
GADGET_IDS = (0x1D6B, 0x0104)
GADGET_PRODUCT = "Virtual Serial Port"
PLACEHOLDER_IDS = (0x1234, 0x5678)

ANSWER_S = 3.0  # the longest the display takes to answer
BRIGHTEST = 255  # the display's brightest backlight (Waveshare's program sends 0 to 255)


def frame(command: int, payload: bytes = b"", kind: int = TO_DISPLAY) -> bytes:
    """A whole frame of ``command`` with ``payload``."""
    return MAGIC + HEAD.pack(kind, command, len(payload), zlib.crc32(payload)) + payload


def qmap(values: dict[str, str]) -> bytes:
    """``values`` as QDataStream writes a ``QMap<QString, QString>``: in the order of the keys'
    UTF-16 code units, as Qt keeps them (the display does not mind; the frames are then the
    vendor's to the byte)."""
    out = [struct.pack(">I", len(values))]
    for key in sorted(values, key=lambda k: k.encode("utf-16-be")):
        for text in (key, values[key]):
            data = str(text).encode("utf-16-be")
            out.append(struct.pack(">I", len(data)) + data)
    return b"".join(out)


def read_qmap(payload: bytes) -> dict[str, str | None]:
    """A ``QMap<QString, QString>`` read back (a null string is None); ValueError if it is not
    one."""
    try:
        (count,) = struct.unpack_from(">I", payload)
        at, out = 4, {}
        for _ in range(count):
            pair = []
            for _ in range(2):
                (size,) = struct.unpack_from(">I", payload, at)
                at += 4
                if size == 0xFFFFFFFF:
                    pair.append(None)
                    continue
                if size % 2 or at + size > len(payload):
                    raise ValueError("a string runs past the payload")
                pair.append(payload[at : at + size].decode("utf-16-be"))
                at += size
            out[pair[0] or ""] = pair[1]
    except (struct.error, UnicodeDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    if at != len(payload):
        raise ValueError("bytes after the map")
    return out


def answer_of(payload: bytes) -> dict[str, str | None] | str | None:
    """What an answer says: a map, a text (brightness echoes its number), or None (empty)."""
    if not payload:
        return None
    try:
        return read_qmap(payload)
    except ValueError:
        return payload.decode("utf-8", "replace")


@dataclass(frozen=True)
class Frame:
    kind: int
    command: int
    payload: bytes


class Frames:
    """Frames out of what the port gives, a piece at a time: bytes before a frame's head, and a
    frame whose CRC does not fit, are passed over (counted in ``skipped``)."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.skipped = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer += data
        found = []
        while True:
            at = self._buffer.find(MAGIC)
            if at < 0:
                keep = len(MAGIC) - 1  # (the head may begin at the end)
                self.skipped += max(0, len(self._buffer) - keep)
                del self._buffer[: max(0, len(self._buffer) - keep)]
                return found
            if at:
                self.skipped += at
                del self._buffer[:at]
            if len(self._buffer) < len(MAGIC) + HEAD.size:
                return found
            kind, command, length, crc = HEAD.unpack_from(self._buffer, len(MAGIC))
            if length > MAX_PAYLOAD:
                self.skipped += 1
                del self._buffer[:1]
                continue
            end = len(MAGIC) + HEAD.size + length
            if len(self._buffer) < end:
                return found
            payload = bytes(self._buffer[len(MAGIC) + HEAD.size : end])
            if zlib.crc32(payload) != crc:
                self.skipped += 1
                del self._buffer[:1]
                continue
            del self._buffer[:end]
            found.append(Frame(kind, command, payload))


class Link:
    """The display on an open port (pyserial's, or any object with ``write``, ``read`` and
    ``close``): a command and its answer at a time."""

    def __init__(self, port: Any, clock: Callable[[], float] = time.monotonic) -> None:
        self.port = port
        self.clock = clock
        self.frames = Frames()

    def ask(self, command: int, payload: bytes = b"", timeout: float = ANSWER_S) -> Frame:
        """Send ``command`` and wait for the display's answer to it; DeviceError when none
        comes in time. Answers to earlier commands that come late are passed over."""
        if command not in COMMANDS:
            raise ValueError(f"command {command} is not one Libre Panel sends")
        try:
            self.port.write(frame(command, payload))
        except Exception as exc:  # pyserial's SerialException, OSError
            raise DeviceError(f"cannot write to the display: {exc}") from exc
        end = self.clock() + timeout
        while self.clock() < end:
            try:
                data = self.port.read(4096)
            except Exception as exc:
                raise DeviceError(f"cannot read from the display: {exc}") from exc
            for found in self.frames.feed(data or b""):
                if found.kind == ANSWER and found.command == command:
                    return found
        raise DeviceError("the display did not answer")

    def hello(self) -> dict[str, str | None]:
        """The handshake: what the display says of itself (some say nothing: {}); DeviceError
        when nothing answers as the display does."""
        said = answer_of(self.ask(FIND_DEVICE).payload)
        return said if isinstance(said, dict) else {}

    def version(self) -> str:
        said = answer_of(self.ask(GET_VERSION).payload)
        if isinstance(said, dict):
            return str(said.get("versionV2") or "")
        return said or ""

    def send_values(self, values: dict[str, str]) -> None:
        self.ask(SYSTEM_DATA, qmap(values))

    def set_brightness(self, percent: int) -> None:
        """``percent`` of the backlight: the display counts 0 to 255 (Waveshare's program sends
        that range; 100, as other tools send at most, is only two fifths of it)."""
        level = round(max(0, min(100, int(percent))) * BRIGHTEST / 100)
        self.ask(SET_BRIGHTNESS, str(level).encode("ascii"))

    def set_flipped(self, flipped: bool) -> None:
        self.ask(SET_CANVAS_FLIP, qmap({"canvas_flip": "1" if flipped else "0"}))

    def close(self) -> None:
        try:
            self.port.close()
        except Exception:
            pass


def _is_display(info: Any) -> bool:
    """Whether the display may be on the port ``info`` (pyserial's): the gadget's ids with its
    product's name, where the system tells it (Windows does not: there the handshake decides),
    or the placeholder ids."""
    ids = (info.vid, info.pid)
    if ids == GADGET_IDS:
        return info.product is None or info.product.strip() == GADGET_PRODUCT
    return ids == PLACEHOLDER_IDS


def candidates(port: str = "") -> list[str]:
    """The ports the display may be on, by their ids (``port``: only that one, whatever its
    ids), without those a panel of Libre Panel has open."""
    if port:
        return [] if place_taken(port) else [port]
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise DeviceError("pyserial is not installed") from exc
    return [
        info.device
        for info in list_ports.comports()
        if _is_display(info) and not place_taken(info.device)
    ]


def open_port(device: str) -> Any:
    """``device`` opened for the display: raw, with DTR set (some firmware is silent without);
    the speed means nothing to a USB serial port."""
    try:
        import serial
    except ImportError as exc:
        raise DeviceError("pyserial is not installed") from exc
    # (Windows opens a port for one program anyway; elsewhere the lock keeps out a second
    # Libre Panel and every program that asks for it)
    only_ours = {} if sys.platform == "win32" else {"exclusive": True}
    try:
        port = serial.Serial(device, 115200, timeout=0.1, write_timeout=ANSWER_S, **only_ours)
        port.dtr = True
    except (serial.SerialException, OSError, ValueError) as exc:
        raise DeviceError(str(exc)) from exc
    return port


def connect(port: str = "", opener: Callable[[str], Any] = open_port) -> tuple[Link, str, dict]:
    """The display: the first port that answers the handshake, as ``(link, port, what it
    said)``; DeviceError (the last port's reason) when none does."""
    reason = "no Waveshare USB monitor is connected"
    for device in candidates(port):
        try:
            link = Link(opener(device))
        except DeviceError as exc:
            reason = f"{device}: {exc}"
            continue
        try:
            return link, device, link.hello()
        except DeviceError as exc:
            reason = f"{device}: {exc}"
            link.close()
    raise DeviceError(reason)

"""Hongtai / Jungle Leopard cooler screens: the JL Chill Arc 360 (480x960) and the strip of
the family (1920x462), and the white-label screens on the same firmware (MSI EZ Display,
Thermaltake, ZOTAC and more). A USB serial port (33c3:7788) that takes JPEG pictures.

The protocol as InfoPanel (GPL-3.0, habibrehmansg/infopanel, JL-PROTOCOL.md; from the
vendor's "Jungle Leopard Display" application) documents it:

- 2 000 000 baud, no flow control. A command is 55 AA, its length (16 bit, little endian,
  the whole frame), the command, its data and the sum of all bytes before (16 bit, little
  endian). The answers have the same frame; 06 is answered with JSON (its size, its model).
- Opening: FF D9 FF D9 00 00 00 00 clears the panel's reader, then 06 asks what it is.
- A picture: 11 (the live picture goes on), then its size (32 bit, little endian), the JPEG
  of the panel's size, the sum of both (16 bit), and FF D9 FF D9. The firmware takes up to
  about 80 KB.
- 11 keeps the live picture: without it for 1.5 s the panel shows its own animation again;
  it goes out every second while no picture does. 03 sets the backlight (0-100), 21 lets the
  panel go.

Never sent: 01 (restart), 0C (firmware update), 14, 15, 26 (when the screen goes off), 20
(the region preset), 23 (the serial number), 25 (the pump's motor).
"""

from __future__ import annotations

import io
import json
import logging
import struct
import time

from PIL import Image

from libre_panel.devices.base import DeviceError, KeepShowing
from libre_panel.devices.models import PanelModel, find_model
from libre_panel.devices.serial_link import FoundPort
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

log = logging.getLogger(__name__)

CMD_LIGHT = 0x03
CMD_INFO = 0x06
CMD_LIVE = 0x11
CMD_CLOSE = 0x21
NEVER = frozenset({0x01, 0x0C, 0x14, 0x15, 0x20, 0x23, 0x25, 0x26})
RESET = bytes((0xFF, 0xD9, 0xFF, 0xD9, 0, 0, 0, 0))
END = bytes((0xFF, 0xD9, 0xFF, 0xD9))
MAX_JPEG = 80 * 1024
QUALITIES = (90, 80, 70, 60, 50, 40, 30)
LIVE_EVERY_S = 1.0
BY_SIZE = {(480, 960): "hongtai-480x960", (462, 1920): "hongtai-1920x462"}


def frame(command: int, data: bytes = b"") -> bytes:
    """A command as the panel takes it: 55 AA, length, command, data, sum."""
    if command in NEVER:
        raise ValueError(f"command {command:#04x} is never sent")
    body = b"\x55\xaa" + struct.pack("<H", len(data) + 7) + bytes((command,)) + data
    return body + struct.pack("<H", sum(body) & 0xFFFF)


def picture(jpeg: bytes) -> bytes:
    """A JPEG in its envelope, with the end marker."""
    sized = struct.pack("<I", len(jpeg)) + jpeg
    return sized + struct.pack("<H", sum(sized) & 0xFFFF) + END


def jpeg(native: Image.Image) -> bytes:
    for quality in QUALITIES:
        buffer = io.BytesIO()
        native.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_JPEG:
            break
    return buffer.getvalue()


class HongtaiDisplay(SerialDisplay):
    name = "hongtai"
    baud = 2_000_000
    rtscts = False
    slow = False  # JPEG pictures at the USB port's pace

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config, model, configured)
        self.native = (model.native_width, model.native_height) if model else (480, 960)
        self._keeper = KeepShowing(LIVE_EVERY_S, self._live, name="hongtai-live")

    @property
    def family(self) -> str:
        return t("Hongtai cooler screen")

    def identify(self, found: FoundPort) -> PanelModel:
        time.sleep(0.2)
        self.link.write(RESET)
        time.sleep(0.2)
        self.link.drain_input()
        self.link.write(frame(CMD_INFO))
        info = self._answer()
        if not info:
            raise DeviceError(t("the panel on {port} does not say what it is", port=found.device))
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        if width and height:
            self.native = (width, height)
        log.info("Hongtai screen %s, %dx%d", str(info.get("model", "?"))[:40], *self.native)
        named = find_model(BY_SIZE.get((min(self.native), max(self.native)), ""))
        return named if named is not None and not self.configured else found.model

    def _answer(self) -> dict:
        """The JSON of the panel's answer to 06; {} for none."""
        for _ in range(64):  # to the frame's start
            byte = self.link.read(1)
            if not byte:
                return {}
            if byte == b"\x55" and self.link.read(1) == b"\xaa":
                break
        else:
            return {}
        head = self.link.read(3)
        if len(head) < 3:
            return {}
        length = struct.unpack("<H", head[:2])[0]
        data = self.link.read(max(0, length - 7))
        self.link.read(2)  # (the sum)
        try:
            info = json.loads(data.decode("utf-8", "replace").strip("\0"))
        except ValueError:
            return {}
        return info if isinstance(info, dict) else {}

    def open(self) -> None:
        super().open()
        self._keeper.start()

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))
        if self.link is not None:
            with self._keeper.lock:
                self._write(frame(CMD_LIGHT, bytes((self.brightness,))))

    def show(self, image: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.link is None:
            raise DeviceError("panel not open")
        width, height = self.native
        if (image.width > image.height) != (width > height):  # into the panel's way round
            image = image.transpose(Image.Transpose.ROTATE_270)
        if image.size != self.native:
            image = image.resize(self.native, Image.Resampling.LANCZOS)
        data = frame(CMD_LIVE) + picture(jpeg(image.convert("RGB")))
        with self._keeper.lock:
            self._keeper.check()
            self._write(data)

    def _write(self, data: bytes) -> None:
        link, before = self.link, self.link.written
        try:
            link.write(data)
        except DeviceError:
            self.counters.usb_errors += 1  # the main loop connects again
            raise
        finally:
            self.counters.bytes += link.written - before
        self._keeper.sent()

    def _live(self) -> None:
        if self.link is not None:
            self._write(frame(CMD_LIVE))

    def close(self) -> None:
        self._keeper.stop()
        if self.link is not None:
            try:
                self.link.write(frame(CMD_CLOSE))
            except DeviceError:
                pass
        super().close()

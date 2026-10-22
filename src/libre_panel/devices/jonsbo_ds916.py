"""JONSBO DS916: a 9.16" screen of 462x1920 for a case (a bar of 1920x462 lying down), on a
USB 2.0 header. Its controller is not a TURZX one: "HLVMAX" firmware (USB 33c3:f101) behind a
USB serial port (CDC ACM) that takes whole JPEG pictures.

The protocol as InfoPanel (GPL-3.0, habibrehmansg/infopanel; from the vendor's application
and USB captures) documents it:

- The port's speed is ignored by the firmware (the vendor's application opens it at 9600).
- F0 A5 5A 0F asks the panel what it is. It answers with 26 ASCII characters: its model (7),
  the size of its framebuffer, which stands on end ("0462*1920"), and its serial number.
- A picture is a JPEG of the framebuffer's size, written to the port as it is: no header, no
  size, no checksum. The vendor's application keeps them at about 120 KB.
- A panel that gets no picture for a while shows its own animation again: the last picture
  goes out again while nothing changes (the vendor's application every half second).
- There is no command for the backlight: the brightness goes into the pixels. Nor is there one
  that turns the screen off.
"""

from __future__ import annotations

import io
import logging
import re

from PIL import Image

from libre_panel.devices.base import DeviceError, KeepShowing, dimmed
from libre_panel.devices.models import PanelModel, get_model
from libre_panel.devices.serial_link import FoundPort
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

log = logging.getLogger(__name__)

MODEL_ID = "jonsbo-ds916"
HELLO = bytes((0xF0, 0xA5, 0x5A, 0x0F))
ANSWER = 26
# "VMAXB16" "0462*1920" "S...": the model, the framebuffer's size, the serial number
IDENTITY = re.compile(r"^(.{7})(\d{3,4})\*(\d{3,4})")
MAX_JPEG = 256 * 1024
QUALITIES = (90, 80, 65, 50, 30)  # the first whose picture fits in MAX_JPEG
AGAIN_S = 0.5  # the last picture again after this long without one


def jpeg(native: Image.Image) -> bytes:
    """``native`` as a JPEG small enough for the panel."""
    for quality in QUALITIES:
        buffer = io.BytesIO()
        native.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_JPEG:
            break
    return buffer.getvalue()


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the framebuffer, which stands on end: a landscape frame
    a quarter turn clockwise, as on the TURZX panels."""
    if frame.width > frame.height:
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


class Ds916Display(SerialDisplay):
    name = "jonsbo-ds916"
    rtscts = False
    slow = False  # whole pictures at the USB port's pace, not pixels over a serial line

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config, model or get_model(MODEL_ID), configured)
        self.native = (462, 1920)  # as the panel says when it is opened
        self.brightness = 100
        self._picture: Image.Image | None = None  # the last one, before it is dimmed
        self._jpeg = b""
        self._keeper = KeepShowing(AGAIN_S, self._again, name="ds916-keep")

    @property
    def family(self) -> str:
        return "JONSBO DS916"

    def identify(self, found: FoundPort) -> PanelModel:
        for _ in range(2):  # (the vendor's application asks again after a second)
            self.link.drain_input()
            self.link.write(HELLO)
            answer = self.link.read(ANSWER).decode("ascii", "replace").strip("\0\r\n ")
            match = IDENTITY.match(answer)
            if match:
                break
        else:
            raise DeviceError(t("the panel on {port} does not say what it is", port=found.device))
        self.link.drain_input()
        width, height = int(match[2]), int(match[3])
        if width and height:
            self.native = (width, height)
        log.info("JONSBO DS916 (%s), %dx%d", match[1].strip(), *self.native)
        return found.model

    def open(self) -> None:
        super().open()
        self._keeper.start()

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))
        if self.link is not None and self._picture is not None:
            with self._keeper.lock:
                self._send(jpeg(dimmed(self._picture, self.brightness)))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.link is None:
            raise DeviceError("panel not open")
        native = to_native(frame, self.native)
        data = jpeg(dimmed(native, self.brightness))
        with self._keeper.lock:
            self._keeper.check()
            self._picture = native
            self._send(data)

    def _send(self, data: bytes) -> None:
        link, before = self.link, self.link.written
        try:
            link.write(data)
        except DeviceError:
            self.counters.usb_errors += 1  # the main loop connects again
            raise
        finally:
            self.counters.bytes += link.written - before
        self._jpeg = data
        self._keeper.sent()

    def _again(self) -> None:
        if self.link is not None and self._jpeg:
            self._send(self._jpeg)

    def close(self) -> None:
        self._keeper.stop()
        super().close()
        self._picture, self._jpeg = None, b""

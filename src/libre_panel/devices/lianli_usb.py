"""Lian Li's USB screens with a TURZX-like protocol (VID 0x1CBE): the Universal Screen 8.8"
and 9.2" and the HydroShift II LCD and OLED Curve.

The protocol as InfoPanel (GPL-3.0, habibrehmansg/infopanel, from the MIT-licensed
lian-li-linux driver) documents it: the TURZX command packet (DES, 500 bytes in 512) with
the same commands, but

- a command and its picture go in one bulk write (sent apart, the panel takes a picture and
  then stops answering);
- the picture is a JPEG to the background layer (101); the PNG layer (102) lies over it and
  is cleared once with a transparent picture when the panel is opened;
- the answer to a picture may not come (the vendor's application does not wait for it); its
  byte 8 says how many pictures wait in the panel, and over 3 the next waits until it is 2
  (asked with 122);
- opening: 10 (sync), 123 (its own media off), 52 (its clock off), the cleared PNG layer and
  15 (30 pictures a second); 14 sets the brightness (0-100).
"""

from __future__ import annotations

import io
import logging
import struct
import time
from typing import Any

from PIL import Image

from libre_panel.devices.base import DeviceError, Display, FrameError
from libre_panel.devices.models import MODELS, PanelModel
from libre_panel.devices.turzx_usb import UsbTransport, encode_png_rgba
from libre_panel.i18n import t

log = logging.getLogger(__name__)

CMD_SYNC = 10
CMD_BRIGHTNESS = 14
CMD_FRAME_RATE = 15
CMD_STOP_CLOCK = 52
CMD_JPEG = 101
CMD_PNG = 102
CMD_LEVEL = 122
CMD_STOP_MEDIA = 123
MAX_PICTURE = 512_000
QUALITIES = (92, 85, 75, 60, 45)
PROTOCOL = "usb-lianli"


def models() -> list[PanelModel]:
    return [model for model in MODELS if model.protocol == PROTOCOL]


def present_model() -> PanelModel | None:
    """A Lian Li screen on the bus (none opened); None for none, or no pyusb."""
    for model in models():
        if UsbTransport.present(model.usb_ids[0][1]):
            return model
    return None


def jpeg(native: Image.Image) -> bytes:
    for quality in QUALITIES:
        buffer = io.BytesIO()
        native.convert("RGB").save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_PICTURE:
            return buffer.getvalue()
    raise FrameError(f"frame too detailed for the panel ({buffer.tell() // 1024} KB)")


class LianLiDisplay(Display):
    name = "lianli"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model
        self.transport: UsbTransport | None = None

    def describe(self) -> str:
        return self.model.label if self.model else "Lian Li"

    def open(self) -> None:
        model = self.model or present_model()
        if model is None:
            raise DeviceError(t("no Lian Li screen on the USB bus"))
        self.model = model
        self.transport = UsbTransport.open(model.usb_ids[0][1], getattr(self.config, "beside", ()))
        try:
            self.transport.drain()
            for command, data in ((CMD_SYNC, b""), (CMD_STOP_MEDIA, b""), (CMD_STOP_CLOCK, b"\0")):
                self.transport.ask(command, data, echo=False)
            clear = Image.new("RGBA", (model.native_width, model.native_height), (0, 0, 0, 0))
            self._push(CMD_PNG, encode_png_rgba(clear))  # the layer over the pictures
            self.transport.ask(CMD_FRAME_RATE, bytes((30,)), echo=False)
        except DeviceError:
            self.close()
            raise
        log.info("connected to %s", model.label)

    def set_brightness(self, percent: int) -> None:
        if self.transport is not None:
            self.transport.ask(CMD_BRIGHTNESS, bytes((max(0, min(100, int(percent))),)), echo=False)

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.transport is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        size = (self.model.native_width, self.model.native_height)
        native = frame
        if (frame.width > frame.height) != (size[0] > size[1]):  # into the panel's way round
            native = frame.transpose(Image.Transpose.ROTATE_270)
        if native.size != size:
            native = native.resize(size, Image.Resampling.LANCZOS)
        try:
            self._push(CMD_JPEG, jpeg(native))
        except DeviceError as exc:
            if isinstance(exc, FrameError):
                raise
            self.counters.usb_errors += 1  # the main loop connects again
            raise

    def _push(self, command: int, data: bytes) -> None:
        reply = self.transport.push(command, struct.pack(">I", len(data)), data)
        self.counters.bytes += len(data)
        if reply is not None and len(reply) > 8 and reply[8] > 3:
            self._wait_for_room()

    def _wait_for_room(self) -> None:
        """Until two pictures or fewer wait in the panel (2 s at most)."""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            reply: Any = self.transport.query(CMD_LEVEL, timeout_ms=200)
            if reply is None or len(reply) <= 8 or reply[8] <= 2:
                return
            time.sleep(0.002)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

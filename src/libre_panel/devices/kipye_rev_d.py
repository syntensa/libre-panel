"""Kipye Qiye 3.5" serial panels (Turing rev. D).

The protocol as turing-smart-screen-python (``lcd_comm_rev_d.py``, GPL-3.0)
uses it on these panels:

- 115200 baud with RTS/CTS flow control. The panel acknowledges what it
  gets; nobody needs the acknowledgements, so they are dropped after every
  write, which also keeps them from filling the line.
- 67 72 0 0 sets portrait. The panel knows no landscape: a landscape frame
  is turned in software and sent in portrait coordinates.
- 67 65, then x0, x1, y0, y1 (16 bit, big endian; in that order), announces a
  bitmap; 68 0 0 0 starts it, the pixels follow as RGB565 big endian in
  packets of 64 bytes (0x50 and 63 bytes of pixels), and 65 0 0 0 ends it.
- 67 67 and 0-500 (16 bit, big endian) sets the brightness. The reference
  sends it twice, because the panel sometimes misses it.

Never sent: 67 66 (fills the screen with one colour) and the mirroring and
upside-down orientations.
"""

from __future__ import annotations

import struct

from PIL import Image

from libre_panel.devices.serial_link import rgb565
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

SET_PORTRAIT = bytes((67, 72, 0, 0))
SET_BRIGHTNESS = bytes((67, 67))
BLOCK_WRITE = bytes((67, 65))
PICTURE_START = bytes((68, 0, 0, 0))
PICTURE_END = bytes((65, 0, 0, 0))
NEVER = frozenset({bytes((67, 66)), bytes((67, 71)), bytes((67, 68)), bytes((67, 70))})


class RevDDisplay(SerialDisplay):
    name = "kipye-rev-d"

    @property
    def family(self) -> str:
        return t("Kipye rev. D panel")

    def _send(self, data: bytes) -> None:
        self.link.write(data)
        self.link.drain_input()  # the acknowledgements: not needed, must not pile up

    def send_brightness(self, percent: int) -> None:
        level = SET_BRIGHTNESS + struct.pack(">H", percent * 5)
        self._send(level)
        self._send(level)  # the panel sometimes misses the first one

    def send_orientation(self, orientation: str, width: int, height: int) -> None:
        self._send(SET_PORTRAIT)  # landscape is turned in software

    def send_bitmap(self, x0: int, y0: int, x1: int, y1: int, image: Image.Image) -> None:
        if self.orientation == "landscape":  # to portrait: a quarter turn clockwise
            portrait_width = self.model.native_width
            image = image.transpose(Image.Transpose.ROTATE_270)
            x0, y0, x1, y1 = portrait_width - 1 - y1, x0, portrait_width - 1 - y0, x1
        self._send(BLOCK_WRITE + struct.pack(">4H", x0, x1, y0, y1))
        self._send(PICTURE_START)
        pixels = rgb565(image, "big")
        for start in range(0, len(pixels), 63):
            self._send(b"\x50" + pixels[start : start + 63])
        self._send(PICTURE_END)

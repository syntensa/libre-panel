"""WeAct Studio Display FS V1: 3.5" and 0.96" serial panels.

The protocol as turing-smart-screen-python (``lcd_comm_weact_a.py`` and
``lcd_comm_weact_b.py``, GPL-3.0) uses it on these panels:

- 115200 baud with RTS/CTS flow control; every command ends with 0x0A.
- 0x02 and an orientation (0 portrait, 2 landscape) turns the panel.
- 0x05, then x0, y0, x1, y1 (16 bit, little endian), shows a bitmap; the
  pixels follow as RGB565, little endian.
- 0x03, a level 0-255 and a fade time in ms (16 bit, little endian) sets the
  brightness.
- 0xC2 asks for the firmware version (19 bytes back).
- The serial number tells the sizes apart: "AB..." is the 3.5", "AD..." the
  0.96".

Never sent: 0x04 (fills the screen with one colour), 0x06 (temperature and
humidity reports), 0x07 (lets the panel go).
"""

from __future__ import annotations

import logging
import struct

from PIL import Image

from libre_panel.devices.models import PanelModel, find_model
from libre_panel.devices.serial_link import FoundPort, rgb565
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

log = logging.getLogger(__name__)

END = 0x0A
CMD_SET_ORIENTATION = 0x02
CMD_SET_BRIGHTNESS = 0x03
CMD_SET_BITMAP = 0x05
CMD_SYSTEM_VERSION = 0x42
READ = 0x80
NEVER = frozenset({0x04, 0x06, 0x07})
FADE_MS = 1000  # as the reference: the panel fades to a new brightness
BY_SERIAL = {"AB": "weact-3.5", "AD": "weact-0.96"}


class WeActDisplay(SerialDisplay):
    name = "weact"

    @property
    def family(self) -> str:
        return t("WeAct panel")

    def identify(self, found: FoundPort) -> PanelModel:
        self.link.drain_input()
        self.link.write(bytes([CMD_SYSTEM_VERSION | READ, END]))
        answer = self.link.read(19)
        self.link.drain_input()
        if len(answer) == 19:
            log.info("WeAct firmware %s", answer[1:9].decode("ascii", "replace").strip())
        named = find_model(BY_SERIAL.get((found.serial_number or "")[:2], ""))
        if named is not None and not self.configured:
            return named
        return found.model

    def send_brightness(self, percent: int) -> None:
        level = int(percent / 100 * 255)
        self.link.write(bytes([CMD_SET_BRIGHTNESS, level]) + struct.pack("<H", FADE_MS) + b"\n")

    def send_orientation(self, orientation: str, width: int, height: int) -> None:
        self.link.write(bytes([CMD_SET_ORIENTATION, 2 if orientation == "landscape" else 0, END]))

    def send_bitmap(self, x0: int, y0: int, x1: int, y1: int, image: Image.Image) -> None:
        self.link.write(bytes([CMD_SET_BITMAP]) + struct.pack("<4H", x0, y0, x1, y1) + b"\n")
        width = self.model.size(self.orientation)[0]
        self.write_chunks(rgb565(image, "little"), width * 4)

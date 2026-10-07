"""XuanFang rev. B and "flagship" 3.5" serial panels.

The protocol as turing-smart-screen-python (``lcd_comm_rev_b.py``, GPL-3.0)
uses it on these panels:

- 115200 baud with RTS/CTS flow control.
- Packets of ten bytes: the command, eight data bytes, the command again.
- 0xCA with "HELLO": the panel answers the same packet with its sub-revision
  after "HELLO": 0x0A then 0x01 or 0x02 (brightness only on or off; 0x02 is
  the flagship) or 0x11 or 0x12 (brightness 0-255).
- 0xCB sets the orientation: 0 portrait, 1 landscape; the panel turns itself.
- 0xCC shows a bitmap: x0, y0, x1, y1 as 16-bit big endian, then the pixels
  as RGB565, big endian. The reference waits 50 ms after each bitmap, which
  keeps the panel from garbling the next one.
- 0xCE sets the brightness: 0-255 (255 brightest), or 0 (on) and 1 (off).

Never sent: 0xCD, the flagship's backplate LED colour.
"""

from __future__ import annotations

import logging
import struct
import time

from PIL import Image

from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import PanelModel
from libre_panel.devices.serial_link import FoundPort, rgb565
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

log = logging.getLogger(__name__)

CMD_HELLO = 0xCA
CMD_SET_ORIENTATION = 0xCB
CMD_DISPLAY_BITMAP = 0xCC
CMD_SET_LIGHTING = 0xCD
CMD_SET_BRIGHTNESS = 0xCE
NEVER = frozenset({CMD_SET_LIGHTING})
FULL_RANGE = (0x11, 0x12)  # sub-revisions with brightness 0-255
COOLDOWN_S = 0.05


def packet(command: int, data: bytes = b"") -> bytes:
    return bytes([command]) + bytes(data).ljust(8, b"\0") + bytes([command])


class RevBDisplay(SerialDisplay):
    name = "xuanfang-rev-b"

    @property
    def family(self) -> str:
        return t("XuanFang rev. B panel")

    sleep = staticmethod(time.sleep)
    full_range = False  # brightness 0-255, else only on and off (HELLO tells)

    def identify(self, found: FoundPort) -> PanelModel:
        self.link.drain_input()
        self.link.write(packet(CMD_HELLO, b"HELLO"))
        answer = self.link.read(10)
        self.link.drain_input()
        if not answer:
            raise DeviceError(t("the panel on {port} did not answer", port=found.device))
        framed = len(answer) == 10 and answer[0] == answer[9] == CMD_HELLO
        if not framed or answer[1:6] != b"HELLO" or answer[6] != 0x0A:
            log.warning("unexpected answer to HELLO: %s", answer.hex(" "))
        self.full_range = framed and answer[7] in FULL_RANGE
        return found.model

    def send_brightness(self, percent: int) -> None:
        if self.full_range:
            level = int(percent / 100 * 255)
        else:
            level = 1 if percent == 0 else 0  # only off and on
        self.link.write(packet(CMD_SET_BRIGHTNESS, bytes([level])))

    def send_orientation(self, orientation: str, width: int, height: int) -> None:
        self.link.write(packet(CMD_SET_ORIENTATION, bytes([orientation == "landscape"])))

    def send_bitmap(self, x0: int, y0: int, x1: int, y1: int, image: Image.Image) -> None:
        self.link.write(packet(CMD_DISPLAY_BITMAP, struct.pack(">4H", x0, y0, x1, y1)))
        width = self.model.size(self.orientation)[0]
        self.write_chunks(rgb565(image, "big"), width * 8)
        self.sleep(COOLDOWN_S)

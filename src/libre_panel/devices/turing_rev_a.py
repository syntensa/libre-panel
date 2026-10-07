"""Turing rev. A serial panels: Turing Smart Screen 3.5" and UsbPCMonitor.

The protocol as turing-smart-screen-python (``lcd_comm_rev_a.py``, GPL-3.0)
uses it on these panels:

- 115200 baud with RTS/CTS flow control.
- A command is six bytes: a rectangle (x, y, ex, ey; ten bits each, packed)
  and the command number.
- 197 shows a bitmap: the rectangle, then its pixels row by row as RGB565,
  little endian.
- 110 sets the brightness: x = 0 (brightest) to 255 (darkest).
- 121 sets the orientation: 16 bytes, the header, then orientation + 100
  and the width and height in that orientation (big endian).
- 69, six times, asks the model: UsbPCMonitor panels answer six bytes
  (01: 3.5", 02: 5", 03: 7"), the Turing 3.5" does not answer.

Never sent: 101 (restarts the panel; its port may change), 102 (clears to
white), 103, 108 and 109 (black, screen off and on): Libre Panel draws every
pixel itself and leaves the panel as it was.
"""

from __future__ import annotations

from PIL import Image

from libre_panel.devices.models import PanelModel, find_model
from libre_panel.devices.serial_link import FoundPort, rgb565
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

CMD_HELLO = 69
CMD_SET_BRIGHTNESS = 110
CMD_SET_ORIENTATION = 121
CMD_DISPLAY_BITMAP = 197
NEVER = frozenset({101, 102, 103, 108, 109})  # reset, clear, to black, screen off/on

PORTRAIT, LANDSCAPE = 0, 2
HELLO_ANSWERS = {  # UsbPCMonitor sizes; the Turing 3.5" does not answer
    bytes([1] * 6): "usbpcmonitor-3.5",
    bytes([2] * 6): "usbpcmonitor-5",
    bytes([3] * 6): "usbpcmonitor-7",
}


def header(command: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> bytes:
    """The six bytes of a command: the rectangle packed ten bits per number."""
    return bytes(
        [
            x >> 2,
            ((x & 3) << 6) + (y >> 4),
            ((y & 15) << 4) + (ex >> 6),
            ((ex & 63) << 2) + (ey >> 8),
            ey & 255,
            command,
        ]
    )


def brightness_level(percent: int) -> int:
    """0-100 % to the panel's 0 (brightest) - 255 (darkest)."""
    return int(255 - max(0, min(100, percent)) / 100 * 255)


class RevADisplay(SerialDisplay):
    name = "turing-rev-a"

    @property
    def family(self) -> str:
        return t("Turing rev. A panel")

    def identify(self, found: FoundPort) -> PanelModel:
        """UsbPCMonitor panels say their size; the Turing 3.5" stays silent. A
        model chosen in the config stays when the panel does not say."""
        self.link.drain_input()
        self.link.write(bytes([CMD_HELLO] * 6))
        answer = self.link.read(6)
        self.link.drain_input()
        named = find_model(HELLO_ANSWERS.get(answer, ""))
        if named is not None:
            return named
        if self.configured or answer:
            return found.model
        return find_model("turing-3.5")

    def send_brightness(self, percent: int) -> None:
        self.link.write(header(CMD_SET_BRIGHTNESS, brightness_level(percent)))

    def send_orientation(self, orientation: str, width: int, height: int) -> None:
        command = bytearray(16)
        command[:6] = header(CMD_SET_ORIENTATION)
        command[6] = (LANDSCAPE if orientation == "landscape" else PORTRAIT) + 100
        command[7:11] = bytes([width >> 8, width & 255, height >> 8, height & 255])
        self.link.write(bytes(command))

    def send_bitmap(self, x0: int, y0: int, x1: int, y1: int, image: Image.Image) -> None:
        self.link.write(header(CMD_DISPLAY_BITMAP, x0, y0, x1, y1))
        width = self.model.size(self.orientation)[0]
        self.write_chunks(rgb565(image, "little"), width * 8)  # as the reference library does

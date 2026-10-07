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

import logging

from PIL import Image, ImageOps

from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import PanelModel, find_model, orientation_of
from libre_panel.devices.serial_link import SerialLink, find_port, rgb565
from libre_panel.i18n import t

log = logging.getLogger(__name__)

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


class RevADisplay(Display):
    name = "turing-rev-a"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model
        self.configured = configured and model is not None  # chosen, not detected
        self.link: SerialLink | None = None
        self.brightness: int | None = None
        self.orientation: int | None = None  # set with the first frame

    def open(self) -> None:
        found = find_port(self.model, getattr(self.config, "port", ""))
        if found is None:
            raise DeviceError(t("no Turing rev. A panel found on a serial port"))
        self.link = SerialLink(found.device)
        self.orientation = None
        try:
            self.model = self._ask_model(found.model, self.configured)
        except DeviceError:
            self.close()
            raise
        log.info("connected to %s on %s", self.model.label, found.device)

    def _ask_model(self, model: PanelModel, configured: bool) -> PanelModel:
        """UsbPCMonitor panels say their size; the Turing 3.5" stays silent. A
        model chosen in the config stays when the panel does not say."""
        self.link.drain_input()
        self.link.write(bytes([CMD_HELLO] * 6))
        answer = self.link.read(6)
        self.link.drain_input()
        named = find_model(HELLO_ANSWERS.get(answer, ""))
        if named is not None:
            return named
        if configured or answer:
            return model
        return find_model("turing-3.5")

    def describe(self) -> str:
        return self.model.label if self.model else t("Turing rev. A panel")

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent
        if self.link:
            self.link.write(header(CMD_SET_BRIGHTNESS, brightness_level(percent)))

    def _orient(self, width: int, height: int) -> None:
        orientation = LANDSCAPE if orientation_of(width, height) == "landscape" else PORTRAIT
        if orientation == self.orientation:
            return
        command = bytearray(16)
        command[:6] = header(CMD_SET_ORIENTATION)
        command[6] = orientation + 100
        command[7:11] = bytes([width >> 8, width & 255, height >> 8, height & 255])
        self.link.write(bytes(command))
        self.orientation = orientation

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.link is None:
            raise DeviceError("panel not open")
        orientation = orientation_of(*frame.size)
        size = self.model.size(orientation) if self.model else frame.size
        if frame.size != size:  # a detected panel of another size than the theme
            corner = frame.getpixel((0, 0))
            frame = ImageOps.pad(frame, size, method=Image.Resampling.LANCZOS, color=corner)
            region = None
        width, height = size
        if self.orientation != (LANDSCAPE if orientation == "landscape" else PORTRAIT):
            region = None  # a new orientation needs the whole frame
        self._orient(width, height)
        x0, y0, x1, y1 = region or (0, 0, width, height)
        if x1 <= x0 or y1 <= y0:
            return
        pixels = rgb565(frame.crop((x0, y0, x1, y1)), "little")
        self.link.write(header(CMD_DISPLAY_BITMAP, x0, y0, x1 - 1, y1 - 1))
        step = width * 8  # as the reference library sends it
        for start in range(0, len(pixels), step):
            self.link.write(pixels[start : start + step])

    def close(self) -> None:
        if self.link is not None:
            self.link.close()
            self.link = None

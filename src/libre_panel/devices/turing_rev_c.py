"""Turing rev. C serial panels: 2.1" round, 5" and 8.8" (V0.x).

The protocol as turing-smart-screen-python (``lcd_comm_rev_c.py``, GPL-3.0)
uses it on these panels:

- 115200 baud with RTS/CTS. Every message is padded to a multiple of 250
  bytes (with 0x00; the bitmap start with 0x2C). Long data is cut into 249
  bytes, each followed by a 0x00.
- A sleeping panel shows up with other USB ids (1a86:ca21, serial number
  "CT21INCH", "USB7INCH" or "CT88INCH"). Opening its port wakes it; it comes
  back as 0525:a4a7 or 1d6b:0121/0106 (serial number "20080411").
- HELLO answers 23 bytes, "chs_..." and the ROM version ("...rom1.87"). The
  size in the answer is not reliable (a 2.1" says 5inch), and neither is
  "CT21INCH" (a 5" sold as UsbPCMonitor sleeps with it too): the model comes
  from the config, from the serial numbers "USB7INCH" and "CT88INCH", or from
  the frame size; failing all of them, the 5".
- STOP_VIDEO and STOP_MEDIA stop what the panel plays by itself.
- A whole frame: PRE_UPDATE_BITMAP, START_DISPLAY_BITMAP, DISPLAY_BITMAP
  (per size), then the pixels as BGRA in the panel's own orientation, and a
  status read.
- A box: UPDATE_BITMAP with the data size and a running count, then row by
  row the row's offset in the framebuffer (3 bytes), its width (2 bytes) and
  its pixels (BGRA, or BGR on the 2.1" and on ROM 88 and older), then
  0xEF 0x69; then QUERY_STATUS and a status read ("needReSend:0|renderCnt:0").
- The panel loses a box whose data ends where the closing 0xEF 0x69 fills its
  250-byte block to the end or is split over two blocks: it waits for data
  that never comes, answers "needReSend:1" and shows no box any more (found by
  gwendal-h for turing-smart-screen-python, PR #348). Such a box goes out as
  two; should the panel still ask, a whole frame follows.
- SET_BRIGHTNESS: 0-255.

Never sent: RESTART, TURNOFF/TURNON, and OPTIONS, which the reference sends
for the orientation: it also stores the start mode and the sleep interval
the panel keeps. Rev. C turns frames in software, so it is not needed.
"""

from __future__ import annotations

import logging
import string
import time
from collections.abc import Callable

from PIL import Image

from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import MODELS, PanelModel, find_model
from libre_panel.devices.serial_link import FoundPort, SerialLink
from libre_panel.devices.serial_panel import SerialDisplay
from libre_panel.i18n import t

log = logging.getLogger(__name__)

HELLO = bytes((0x01, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0xC5, 0xD3))
SET_BRIGHTNESS = bytes((0x7B, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00))
STOP_VIDEO = bytes((0x79, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
STOP_MEDIA = bytes((0x96, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
QUERY_STATUS = bytes((0xCF, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
PRE_UPDATE_BITMAP = bytes((0x86, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
START_DISPLAY_BITMAP = bytes((0x2C,))
UPDATE_BITMAP = bytes((0xCC, 0xEF, 0x69, 0x00))
DISPLAY_BITMAP = {  # per size
    "turing-2.1": bytes((0xC8, 0xEF, 0x69, 0x00, 0x0E, 0x10)),
    "turing-5": bytes((0xC8, 0xEF, 0x69, 0x00, 0x17, 0x70)),
    "turing-8.8": bytes((0xC8, 0xEF, 0x69, 0x00, 0x38, 0x40)),
}
NEVER = frozenset({0x84, 0x83, 0x7D})  # RESTART, TURNOFF/TURNON, OPTIONS (first byte)
STATUS_SIZE = 1024

ASLEEP_SERIALS = {"CT21INCH", "USB7INCH", "CT88INCH"}
SIZE_SERIALS = {"USB7INCH": "turing-5", "CT88INCH": "turing-8.8"}  # CT21INCH: 2.1" or 5"
ASLEEP_IDS = {(0x1A86, 0xCA21)}
AWAKE_IDS = {(0x0525, 0xA4A7), (0x1D6B, 0x0121), (0x1D6B, 0x0106)}
AWAKE_SERIAL = "20080411"
WAKE_TRIES = 15
# Where a box's data may not end in its last 250-byte block (see above).
UNSAFE_ENDS = frozenset({0, 248, 249})


def padded(message: bytes, fill: int = 0x00) -> bytes:
    """``message`` padded to a multiple of 250 bytes."""
    return message + bytes([fill]) * (-len(message) % 250)


def cut(data: bytes) -> bytes:
    """``data`` in pieces of 249 bytes, each but the last followed by 0x00."""
    return b"\x00".join(data[i : i + 249] for i in range(0, len(data), 249))


def bgra(image: Image.Image) -> bytes:
    r, g, b, a = image.convert("RGBA").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def bgr(image: Image.Image) -> bytes:
    r, g, b = image.convert("RGB").split()
    return Image.merge("RGB", (b, g, r)).tobytes()


def halves(image: Image.Image, x: int, y: int) -> list[tuple[Image.Image, int, int]]:
    """``image`` at (x, y) as two boxes: its rows but the last, and the last one
    (a single row: its pixels but the last, and the last one)."""
    w, h = image.size
    if h > 1:
        return [(image.crop((0, 0, w, h - 1)), x, y), (image.crop((0, h - 1, w, h)), x, y + h - 1)]
    return [(image.crop((0, 0, w - 1, 1)), x, y), (image.crop((w - 1, 0, w, 1)), x + w - 1, y)]


def is_asleep(info) -> bool:
    return (info.vid, info.pid) in ASLEEP_IDS or info.serial_number in ASLEEP_SERIALS


def is_awake(info) -> bool:
    return (info.vid, info.pid) in AWAKE_IDS or info.serial_number == AWAKE_SERIAL


class RevCDisplay(SerialDisplay):
    name = "turing-rev-c"
    sleep: Callable[[float], None] = staticmethod(time.sleep)

    @property
    def family(self) -> str:
        return t("Turing rev. C panel")

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config, model, configured)
        if not self.configured:  # its ids do not say the size: the 5" until we know better
            self.model = None
        self.sized = self.configured  # is the model's size known (else: from the first frame)
        self.rom = 87
        self.count = 0
        # for the log (-v): what the panel last said, and the updates of the last minute
        self._said: str | None = None
        self.resync = False  # the panel lost a box: the next frame goes out whole
        self._minute = (time.monotonic(), 0, 0, 0.0)  # (since, boxes, whole frames, slowest s)

    # -- finding and waking ----------------------------------------------------

    def open(self) -> None:
        from libre_panel.devices import serial_link

        port = getattr(self.config, "port", "")
        ports = serial_link._ports()
        for info in ports:  # asleep, its serial number may say the size
            hint = find_model(SIZE_SERIALS.get(info.serial_number or "", ""))
            if is_asleep(info) and hint is not None and not self.configured:
                self.model, self.sized = hint, True
        awake = self._awake(ports, port)
        if not awake:  # an awake panel is taken as it is: only a sleeping one is woken
            for info in ports:
                if is_asleep(info) and not (port and info.device != port):
                    ports = self._wake(info)
                    port = ""  # the port named the sleeping panel; awake, it has another
                    break
            awake = self._awake(ports, port)
        if not awake and not port:
            raise DeviceError(t("no {panel} found on a serial port", panel=self.family))
        device = awake[0].device if awake else port
        model = self.model or find_model("turing-5")
        self.link = SerialLink(device, self.baud, self.rtscts)
        self.orientation = None
        try:
            self.model = self.identify(FoundPort(device, model, AWAKE_SERIAL))
            self._stop_playback()
        except DeviceError:
            self.close()
            raise
        log.info("connected to %s on %s (ROM %d)", self.model.label, device, self.rom)

    def _awake(self, ports: list, port: str) -> list:
        """Awake rev. C ports (on ``port``, if set), the panels' serial number first."""
        found = [
            p
            for p in ports
            if is_awake(p)
            and not (port and p.device != port)
            and (self.configured or port or p.serial_number == AWAKE_SERIAL)  # see GADGET_IDS
        ]
        return sorted(found, key=lambda p: p.serial_number != AWAKE_SERIAL)

    def _wake(self, info) -> list:
        """Open a sleeping panel's port until it comes back awake (up to 15 s)."""
        from libre_panel.devices import serial_link

        log.info("waking the panel on %s", info.device)
        for _ in range(WAKE_TRIES):
            try:
                serial_link.open_serial(info.device, self.baud, self.rtscts).close()
            except Exception:  # pyserial errors: it is changing its ids
                pass
            ports = serial_link._ports()
            if any(is_awake(p) for p in ports):
                self.sleep(1.0)  # it answers a moment after it shows up
                return ports
            self.sleep(1.0)
        raise DeviceError(t("the panel on {port} did not wake up", port=info.device))

    # -- the protocol --------------------------------------------------------------

    def _command(self, message: bytes, fill: int = 0x00, read: int = 0) -> bytes:
        self.link.write(padded(message, fill))
        return self.link.read(read) if read else b""

    def identify(self, found: FoundPort) -> PanelModel:
        self.link.drain_input()
        answer = self._command(HELLO, read=23)
        self.link.drain_input()
        text = "".join(c for c in answer.decode("ascii", "ignore") if c in string.printable)
        if not text.startswith("chs_"):
            raise DeviceError(t("the panel on {port} did not answer", port=found.device))
        try:
            rom = int(text.split(".")[2])
            self.rom = rom if 80 <= rom <= 100 else 87
        except (IndexError, ValueError):
            self.rom = 87
        return found.model

    def _stop_playback(self) -> None:
        self._command(STOP_VIDEO)
        self._command(STOP_MEDIA, read=STATUS_SIZE)

    def send_brightness(self, percent: int) -> None:
        self._command(SET_BRIGHTNESS + bytes([int(percent / 100 * 255)]))

    def send_orientation(self, orientation: str, width: int, height: int) -> None:
        pass  # turned in software (OPTIONS would store settings in the panel)

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if not self.sized:  # the size from the frame, as the reference does
            fits = [
                m
                for m in MODELS
                if m.protocol == "serial-c"
                and frame.size in (m.size("landscape"), m.size("portrait"))
            ]
            if fits:
                self.model = fits[0]
            self.sized = True
        if self.resync:
            region = None  # a whole frame brings the panel back in step
        super().show(frame, region)

    def send_bitmap(self, x0: int, y0: int, x1: int, y1: int, image: Image.Image) -> None:
        width, height = self.model.size(self.orientation)
        started = time.monotonic()
        whole = (x0, y0, x1, y1) == (0, 0, width - 1, height - 1)
        if whole:
            self.resync = False
            self._full(image)
        else:
            self._box(image, x0, y0)
        self._count_update(whole, time.monotonic() - started)

    def _heard(self, answer: bytes) -> None:
        """The panel's answer to a status query, in the log when it changes: where
        to look first when a panel stops showing new frames."""
        text = "".join(c for c in answer.decode("ascii", "ignore") if c in string.printable)
        said = text.strip()[:160] or (f"{len(answer)} bytes, no text" if answer else "no answer")
        changed = said != self._said
        if changed:
            log.debug("panel status (update %d): %s", self.count, said)
            self._said = said
        if "needReSend:1" in text and not self.resync:
            if changed:  # once, should it keep asking
                log.info("the panel lost an update; sending the whole frame again")
            self.resync = True

    def _count_update(self, whole: bool, seconds: float) -> None:
        since, boxes, frames, slowest = self._minute
        boxes, frames = boxes + (not whole), frames + whole
        slowest = max(slowest, seconds)
        now = time.monotonic()
        if now - since >= 60:
            log.debug(
                "%d updates (%d whole frames) in %.0f s, the slowest %.0f ms",
                boxes + frames, frames, now - since, slowest * 1000,
            )  # fmt: skip
            since, boxes, frames, slowest = now, 0, 0, 0.0
        self._minute = (since, boxes, frames, slowest)

    @property
    def _tall(self) -> bool:  # the 8.8": a portrait framebuffer
        return self.model.id == "turing-8.8"

    def _full(self, image: Image.Image) -> None:
        if self._tall:
            if self.orientation == "landscape":
                image = image.rotate(270, expand=True)
            else:
                image = image.rotate(180, expand=True)
        elif self.orientation == "portrait":
            image = image.rotate(90, expand=True)
        size = (self.model.native_width * self.model.native_width // 64).to_bytes(2, "big")
        self._command(PRE_UPDATE_BITMAP)
        self._command(START_DISPLAY_BITMAP, fill=0x2C)
        self._command(DISPLAY_BITMAP[self.model.id] + size)
        self._command(cut(bgra(image)), read=STATUS_SIZE)
        self._heard(self._command(QUERY_STATUS, read=STATUS_SIZE))

    def _box(self, image: Image.Image, x: int, y: int) -> None:
        rows = self._rows(image, x, y)
        data = cut(rows) if len(rows) > 250 else rows
        if len(data) % 250 in UNSAFE_ENDS:  # the panel would lose it: two boxes instead
            for part, px, py in halves(image, x, y):
                self._box(part, px, py)
            return
        head = UPDATE_BITMAP + (len(rows) + 2).to_bytes(3, "big") + bytes(3)
        head += self.count.to_bytes(4, "big")
        self._command(head)
        self._command(data + b"\xef\x69")
        self._heard(self._command(QUERY_STATUS, read=STATUS_SIZE))
        self.count += 1

    def _rows(self, image: Image.Image, x: int, y: int) -> bytes:
        """The box's rows in the panel's framebuffer: offset, width, pixels."""
        native_w, native_h = self.model.native_width, self.model.native_height
        width, height = self.model.size(self.orientation)  # the panel in this orientation
        x0, y0 = x, y
        if self._tall:
            if self.orientation == "landscape":
                image = image.rotate(270, expand=True)
                y0 = height - y - image.width
            else:
                image = image.rotate(180, expand=True)
                x0 = height - y - image.height
                # (the reference has the height here too, which leaves the screen;
                # the whole frame's half turn puts the box at width - x - w)
                y0 = width - x - image.width
        elif self.orientation == "portrait":
            image = image.rotate(90, expand=True)
            x0 = width - x - image.height
        else:
            x0, y0 = y, x
        stride = native_w if self._tall else native_h
        four = self.model.id != "turing-2.1" and self.rom > 88
        pixels, size = (bgra(image), 4) if four else (bgr(image), 3)
        rows = bytearray()
        line = image.width * size
        for h in range(image.height):
            rows += ((x0 + h) * stride + y0).to_bytes(3, "big")
            rows += image.width.to_bytes(2, "big")
            rows += pixels[h * line : (h + 1) * line]
        return bytes(rows)

"""JONSBO ZC-360: three 3.38" screens of 640x180, one under each fan of a frame for three
120 mm fans (and the same on the fans of the TF3-360). Each screen is a USB device of its own
(43a8:0e61, "TURZX-338inch-r") with a serial number of its own; Libre Panel drives the three
as one panel of 1920x180, the screens side by side.

The protocol, as the MIT-licensed jonsbo-zc360-linux-driver (Lucille Hon; from USB captures
and the vendor's Windows application) documents it, with the headers checked byte for byte
against a frame of the vendor's application:

- A command is 32 bytes: AA 55, the command and its parameter and six zero bytes encrypted
  with DES (ECB, a fixed key), 0x01 (a command) at byte 10 and BB at the end. The answer is
  32 bytes from endpoint 0x82; its bytes 2..10 decrypt the same way, byte 2 is the state.
- 56 starts the controller, 84 sets the brightness (0-100), 86 the rotation (0). A
  controller just powered answers its first frame with 0x60 and wants 88 then, which takes
  it to showing frames (it answers 0x59); every frame after that answers 0x62. The
  answer is read a millisecond after the write, as the reference driver does.
- A frame is the screen's framebuffer, 180x640 (a landscape picture turned a quarter
  clockwise), BGR, in 720 records of a 32-byte header (AA 55, 0x02 at byte 10, the record's
  number 1-720 big endian at bytes 11-12, BB at the end) and 480 bytes of pixels; eight
  records go in one bulk write of 4096 bytes, 90 writes a frame, then the answer is read.
- The screens are written one after the other: writing them at once (from threads) left
  parts of old frames on them in those tests. A screen whose part of the picture did not
  change is not written again, so a clock that changes on one of them costs one screen;
  only a picture the screen answered 0x62 to counts as shown (else it goes again).
- The controllers do not like being let go and taken again in one power session (parts of
  the factory logo stay): Libre Panel keeps them from open() to close().

Their order from left to right is that of their serial numbers, or ``device.port``: "2,1,3"
puts the left third of the picture on the second of them, and so on. "Which is which?"
shows each screen its number.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PIL import Image

from libre_panel import identify
from libre_panel.devices.base import DeviceError, Display, leave_place, place_taken, take_place
from libre_panel.devices.models import PanelModel, get_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

VENDOR_ID = 0x43A8
PRODUCT_ID = 0x0E61
MODEL_ID = "jonsbo-zc360"
SCREENS = 3
WIDTH, HEIGHT = 640, 180  # one screen, landscape

ENDPOINT_OUT = 0x02
ENDPOINT_IN = 0x82
DES_KEY = bytes((65, 95, 217, 250, 19, 66, 88, 183))
COMMAND, FRAME = 0x01, 0x02  # byte 10: what a packet is

CMD_INIT = 56
CMD_BRIGHTNESS = 84
CMD_ROTATION = 86
CMD_COMMIT = 88  # the first frame's: a fresh controller shows frames from then on

FIRST_FRAME = 0x60  # the answer to a fresh controller's first frame
SHOWING = 0x62  # the answer to every frame after it
COMMIT_SETTLE_S = 0.3
CLAIM_SETTLE_S = 0.3  # after taking the three, before the first command
ANSWER_GAP_S = 0.001  # between a write and the read of its answer

PAYLOAD = 480  # pixel bytes a record
RECORDS = WIDTH * HEIGHT * 3 // PAYLOAD  # 720
PER_WRITE = 8  # records a bulk write: 4096 bytes
WRITE_TIMEOUT_MS = 3000
READ_TIMEOUT_MS = 100  # a late answer is no fault: the frame went out


def _cipher() -> Any:
    try:
        from Crypto.Cipher import DES
    except ImportError as exc:
        raise DeviceError("pycryptodome is missing: pip install 'libre-panel[usb]'") from exc
    return DES.new(DES_KEY, DES.MODE_ECB)


def command_packet(command: int, parameter: int = 0) -> bytes:
    if not 0 <= command <= 0xFF or not 0 <= parameter <= 0xFF:
        raise ValueError("a command and its parameter are a byte each")
    packet = bytearray(32)
    packet[0:2] = b"\xaa\x55"
    packet[2:10] = _cipher().encrypt(bytes((command, parameter, 0, 0, 0, 0, 0, 0)))
    packet[10] = COMMAND
    packet[31] = 0xBB
    return bytes(packet)


def answer_state(answer: bytes) -> int | None:
    """The state a 32-byte answer says (its byte 2, decrypted); None for no answer."""
    if len(answer) != 32:
        return None
    return _cipher().decrypt(bytes(answer[2:10]))[0]


def _header(number: int) -> bytes:
    header = bytearray(32)
    header[0:2] = b"\xaa\x55"
    header[10] = FRAME
    header[11:13] = number.to_bytes(2, "big")
    header[31] = 0xBB
    return bytes(header)


HEADERS = tuple(_header(number) for number in range(1, RECORDS + 1))


def frame_writes(screen: Image.Image) -> list[bytes]:
    """The 90 bulk writes of one screen's landscape picture (640x180)."""
    if screen.size != (WIDTH, HEIGHT):
        screen = screen.resize((WIDTH, HEIGHT))
    native = screen.convert("RGB").transpose(Image.Transpose.ROTATE_270)  # a quarter clockwise
    pixels = memoryview(native.tobytes("raw", "BGR"))
    writes = []
    for first in range(0, RECORDS, PER_WRITE):
        parts = []
        for record in range(first, first + PER_WRITE):
            parts += (HEADERS[record], pixels[record * PAYLOAD : (record + 1) * PAYLOAD])
        writes.append(b"".join(parts))
    return writes


def screen_order(port: str) -> list[int]:
    """Which screen (by serial number, from 0) shows each third of the picture, from the left:
    ``device.port`` as "2,1,3" (or "213"); in their order where it says nothing usable."""
    digits = [int(ch) - 1 for ch in port if ch.isdigit()]
    if sorted(digits) == list(range(SCREENS)):
        return digits
    if port.strip():
        log.warning('device.port %r is no order of the three screens ("1,2,3"): ignored', port)
    return list(range(SCREENS))


def _backend() -> Any:
    from libre_panel.devices.turzx_usb import _backend as backend

    return backend()


def _devices() -> list[Any]:
    try:
        import usb.core
    except ImportError as exc:
        raise DeviceError("pyusb is not installed: pip install 'libre-panel[usb]'") from exc
    try:
        found = list(
            usb.core.find(find_all=True, idVendor=VENDOR_ID, idProduct=PRODUCT_ID,
                          backend=_backend()) or []
        )  # fmt: skip
    except usb.core.NoBackendError as exc:
        raise DeviceError("no libusb backend found (see docs/HARDWARE.md)") from exc
    return found


def _serial(device: Any) -> str:
    try:
        import usb.util

        return usb.util.get_string(device, device.iSerialNumber) or ""
    except Exception:  # noqa: BLE001 - no access to the strings before it is set up
        return ""


def _place(device: Any) -> str:
    return f"usb:{getattr(device, 'bus', None)}.{getattr(device, 'address', None)}"


def present() -> bool:
    """Whether a ZC-360 is on the USB bus (none of its screens is opened)."""
    try:
        return bool(_devices())
    except DeviceError:
        return False


class Screen:
    """One of the three: its USB device, claimed from open() to close()."""

    def __init__(self, device: Any, serial: str) -> None:
        self.device, self.serial = device, serial
        self.place = _place(device)
        self.sent: bytes | None = None  # its last picture it showed (unchanged: not again)

    def claim(self) -> None:
        import usb.core
        import usb.util

        try:
            try:
                if self.device.is_kernel_driver_active(0):
                    self.device.detach_kernel_driver(0)
            except (NotImplementedError, usb.core.USBError):
                pass  # (Windows has none to detach)
            self.device.set_configuration(1)
            usb.util.claim_interface(self.device, 0)
        except usb.core.USBError as exc:
            raise DeviceError(t("cannot open the ZC-360 screen: {error}", error=exc)) from exc

    def release(self) -> None:
        import usb.util

        try:
            usb.util.release_interface(self.device, 0)
            usb.util.dispose_resources(self.device)
        except Exception:  # noqa: BLE001 - gone already
            pass

    def command(self, command: int, parameter: int = 0) -> int | None:
        self.device.write(ENDPOINT_OUT, command_packet(command, parameter), WRITE_TIMEOUT_MS)
        return self.answer()

    def answer(self) -> int | None:
        import usb.core

        time.sleep(ANSWER_GAP_S)
        try:
            return answer_state(bytes(self.device.read(ENDPOINT_IN, 32, READ_TIMEOUT_MS)))
        except usb.core.USBError:  # (also a timeout) a missing answer does not undo a frame
            return None

    def show(self, picture: Image.Image) -> int:
        """Send its picture where it changed; the bytes sent."""
        writes = frame_writes(picture)
        body = b"".join(writes)
        if body == self.sent:
            return 0
        for chunk in writes:
            self.device.write(ENDPOINT_OUT, chunk, WRITE_TIMEOUT_MS)
        state = self.answer()
        self.sent = body if state == SHOWING else None
        if state == FIRST_FRAME:  # a controller just powered: on to showing frames
            self.command(CMD_COMMIT)
            time.sleep(COMMIT_SETTLE_S)
        return len(body)


class Zc360Display(Display):
    name = "jonsbo"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model or get_model(MODEL_ID)
        self.screens: list[Screen] = []
        self.order = screen_order(getattr(config, "port", "") or "")

    def describe(self) -> str:
        return self.model.label

    def open(self) -> None:
        free = [d for d in _devices() if not place_taken(_place(d))]
        if len(free) < SCREENS:
            raise DeviceError(
                t("the ZC-360 has {count} of its 3 screens on the USB bus", count=len(free))
            )
        screens = sorted((Screen(d, _serial(d)) for d in free),
                         key=lambda s: (s.serial, s.place))[:SCREENS]  # fmt: skip
        taken: list[Screen] = []
        try:
            for screen in screens:  # all three are claimed before any is told a thing
                if not take_place(screen.place):
                    raise DeviceError(t("another panel has a screen of the ZC-360 open"))
                taken.append(screen)
                screen.claim()
            time.sleep(CLAIM_SETTLE_S)
            for screen in screens:
                self._sent(screen.command, CMD_INIT)
                self._sent(screen.command, CMD_BRIGHTNESS, self._level(self.config.brightness))
                self._sent(screen.command, CMD_ROTATION, 0)
        except Exception:
            for screen in taken:
                screen.release()
                leave_place(screen.place)
            raise
        self.screens = screens
        log.info("ZC-360: screens %s", ", ".join(s.serial or "?" for s in screens))

    @staticmethod
    def _level(percent: int) -> int:
        return max(0, min(100, int(percent)))

    def set_brightness(self, percent: int) -> None:
        for screen in self.screens:
            self._sent(screen.command, CMD_BRIGHTNESS, self._level(percent))

    def screen_off(self) -> bool:
        if not self.screens:
            return False
        self.set_brightness(0)  # (its next open() sets it again)
        return True

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if not self.screens:
            raise DeviceError(t("the ZC-360 is not open"))
        if frame.width < frame.height:  # a portrait frame (the doctor's card): turned to fit
            frame = frame.transpose(Image.Transpose.ROTATE_270)
        if frame.size != (WIDTH * SCREENS, HEIGHT):
            frame = frame.resize((WIDTH * SCREENS, HEIGHT))
        if identify.showing():  # which is which: each screen its number
            parts = {k: identify.picture(k + 1, (WIDTH, HEIGHT)) for k in range(SCREENS)}
        else:
            parts = {
                screen: frame.crop((third * WIDTH, 0, (third + 1) * WIDTH, HEIGHT))
                for third, screen in enumerate(self.order)
            }
        for index, picture in sorted(parts.items()):
            self.counters.bytes += self._sent(self.screens[index].show, picture)

    def _sent(self, send, *args: Any) -> Any:
        import usb.core

        try:
            return send(*args)
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1
            raise DeviceError(t("the ZC-360 stopped answering: {error}", error=exc)) from exc

    def close(self) -> None:
        for screen in self.screens:
            screen.release()
            leave_place(screen.place)
        self.screens = []

"""NZXT Kraken coolers with an LCD on the pump: the Kraken Z53, Z63 and Z73 (USB 1e71:3008,
320x320), the Kraken 2023 (300e, 240x240), the Kraken Elite RGB 2024 (3012, 640x640) and the
Kraken Plus 2024 (3014, 240x240), round screens. Only the screen's reports are ever written:
the pump and the fans are none of Libre Panel's business.

The protocol, as liquidctl (GPL-3.0, liquidctl/driver/kraken3.py) drives the LCD and krakenlcd
(GPL-3.0, ossini/krakenlcd, docs/PROTOCOL.md) documents it from a Kraken Z53:

- Control goes over the HID interface in reports of 64 bytes, an id and a sub-command first.
  The answer has the id + 1 and the same sub-command, and 1 at byte 14 where the command was
  taken; the cooler sends status reports of its own in between, which are passed by. 10 01
  asks for the firmware's version (the answer's bytes 17-19), 30 01 for the screen's state
  (its brightness at byte 24, its turn in quarters at byte 26); 30 02 01 <brightness> 00 00
  01 <turn> sets the brightness, with the turn as it was; 38 01 02 00 gives the screen back to
  the firmware's liquid temperature, 38 01 04 <bucket> shows the picture in a bucket.
- A picture goes into one of 16 buckets of the cooler's 24320 KiB of picture memory, which it
  forgets when it loses power: 36 03; 32 02 <bucket> (deleted, twice where it held one; the
  bucket on screen cannot be); 32 01 <bucket> <bucket+1> <start> <size> 01 (KiB, 16 bit
  each); 36 01 <bucket>; then on bulk endpoint 0x02 of interface 0 a header of 20 bytes by
  itself (12 bytes of magic, 02 00 00 00, the pixels' length in 32 bit) and the pixels as R G
  B 0; 36 02; 38 01 04 <bucket>. Buckets 0 and 1 take turns, each with half the memory, so
  that the one on screen is never the one written (krakenlcd's way).
- A Kraken 2023 with firmware 2 takes a picture without buckets: 36 01 00 01 06, the header
  with 06 and the pixels as RGB565 (big endian), 36 02; the first one twice (liquidctl).
- The cooler shows a picture as it is: the turn it is set to goes into the pixels.
- On Linux the HID interface stays with the kernel (its nzxt-kraken3 driver reads the liquid's
  temperature through it): the reports go through its hidraw node.
"""

from __future__ import annotations

import glob
import logging
import os
import select
import sys
import time
from typing import Any

import numpy as np
from PIL import Image

from libre_panel.devices import usb_link
from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import MODELS, PanelModel
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROTOCOL = "usb-nzxt"
VENDOR_ID = 0x1E71
Z3, KRAKEN_2023 = 0x3008, 0x300E
PRODUCTS = (Z3, KRAKEN_2023, 0x3012, 0x3014)
REPORT = 64
BULK_INTERFACE, BULK_OUT = 0, 0x02
# what liquidctl writes the pixels in: the endpoint's packets on the Z, 2 MB on the others
CHUNK = {Z3: 512}
BIG_CHUNK = 2 * 1024 * 1024

MAGIC = bytes((0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10))
RGBA, RGB16 = 0x02, 0x06  # the header's kind of picture
BUCKETS = (0, 1)
MEMORY_KIB = 24320
TAKEN = 0x01  # an answer's byte 14
ANSWER_S = 3.0  # krakenlcd's wait for an answer
WRITE_TIMEOUT_MS = 5000


def models() -> list[PanelModel]:
    return [model for model in MODELS if model.protocol == PROTOCOL]


def model_for(product: int) -> PanelModel:
    return next(m for m in models() if (VENDOR_ID, product) in m.usb_ids)


def present_model() -> PanelModel | None:
    """A Kraken with an LCD on the bus (none opened); None for none, or no pyusb."""
    product = usb_link.present(VENDOR_ID, PRODUCTS)
    return model_for(product) if product is not None else None


def header(kind: int, length: int) -> bytes:
    return MAGIC + bytes((kind, 0, 0, 0)) + length.to_bytes(4, "little")


def rgba(picture: Image.Image) -> bytes:
    """The pixels as R G B 0, as liquidctl sends them."""
    pixels = np.asarray(picture.convert("RGB"), dtype=np.uint8)
    return np.dstack((pixels, np.zeros(pixels.shape[:2], np.uint8))).tobytes()


def rgb565_big(picture: Image.Image) -> bytes:
    pixels = np.asarray(picture.convert("RGB"), dtype=np.uint16)
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    return (((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)).astype(">u2").tobytes()


def turned(picture: Image.Image, quarters: int) -> Image.Image:
    """``picture`` turned clockwise by ``quarters``, as liquidctl turns it for the cooler."""
    if quarters % 4 == 1:
        return picture.transpose(Image.Transpose.ROTATE_270)
    if quarters % 4 == 2:
        return picture.transpose(Image.Transpose.ROTATE_180)
    if quarters % 4 == 3:
        return picture.transpose(Image.Transpose.ROTATE_90)
    return picture


# -- the HID interface ------------------------------------------------------------------------


class Hidraw:
    """The HID interface's reports through its hidraw node (Linux)."""

    def __init__(self, path: str) -> None:
        self.path = path
        try:
            self.fd: int | None = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            from libre_panel.devices.turzx_usb import access_hint

            raise DeviceError(access_hint(exc) or f"cannot open {path}: {exc}") from exc

    def write(self, report: bytes, timeout_ms: int = 0) -> None:
        os.write(self.fd, bytes(report).ljust(REPORT, b"\0"))

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        ready, _, _ = select.select([self.fd], [], [], timeout_ms / 1000)
        if not ready:
            return None
        try:
            return os.read(self.fd, REPORT) or None
        except BlockingIOError:
            return None

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def hidraw_node(device: Any, root: str = "/") -> str | None:
    """The hidraw node of the device's HID interface, from sysfs; None for none."""
    bus, address = str(getattr(device, "bus", "")), str(getattr(device, "address", ""))
    for folder in sorted(glob.glob(os.path.join(root, "sys/bus/usb/devices/*"))):
        try:
            with (
                open(os.path.join(folder, "busnum")) as f1,
                open(os.path.join(folder, "devnum")) as f2,
            ):
                if (f1.read().strip(), f2.read().strip()) != (bus, address):
                    continue
        except OSError:
            continue
        name = os.path.basename(folder)
        for interface in sorted(glob.glob(os.path.join(folder, f"{name}:*"))):
            try:
                with open(os.path.join(interface, "bInterfaceClass")) as f:
                    if f.read().strip() != "03":
                        continue
            except OSError:
                continue
            found = sorted(glob.glob(os.path.join(interface, "*", "hidraw", "hidraw*")))
            if found:
                return os.path.join(root, "dev", os.path.basename(found[0]))
    return None


def _linux() -> bool:
    return sys.platform.startswith("linux")


def _control(device: Any) -> tuple[Any, tuple[int, ...]]:
    """The way to the HID interface, and the interfaces claimed for it: on Linux its hidraw node
    (the kernel keeps the interface), elsewhere the interface itself over pyusb."""
    if _linux():
        node = hidraw_node(device)
        if node is None:
            raise DeviceError("the Kraken's HID interface has no hidraw node (is usbhid loaded?)")
        return Hidraw(node), ()
    interface = usb_link.hid_interface(device)
    if interface is None:
        raise DeviceError("the Kraken has no HID interface")
    usb_link.claim(device, (interface,))
    return usb_link.HidReports(device, interface, REPORT, ids=True), (interface,)


# -- the cooler -------------------------------------------------------------------------------


class KrakenDisplay(Display):
    name = "nzxt-kraken"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model
        self.device: Any = None
        self.place = ""
        self.product = 0
        self.control: Any = None
        self.claimed: tuple[int, ...] = ()
        self.firmware = (0, 0, 0)
        self.turn = 0
        self.next = 0  # the bucket's place in BUCKETS the next picture goes to
        self.first = True
        self._warned = False

    def describe(self) -> str:
        return self.model.label if self.model else "NZXT Kraken"

    @property
    def direct(self) -> bool:
        """Whether the pictures go without buckets (a Kraken 2023 with firmware 2)."""
        return self.product == KRAKEN_2023 and self.firmware[0] == 2

    def open(self) -> None:
        import usb.core

        own = tuple(pid for _vid, pid in self.model.usb_ids) if self.model else ()
        products = own + tuple(pid for pid in PRODUCTS if pid not in own)
        self.device, self.product, self.place = usb_link.take(VENDOR_ID, products, self.describe())
        self.model = model_for(self.product)
        try:
            self.control, hid = _control(self.device)
            usb_link.claim(self.device, (BULK_INTERFACE,))
            self.claimed = (BULK_INTERFACE, *hid)
            self._drain()
            version = self._ask(0x10, 0x01)
            self.firmware = (version[17], version[18], version[19])
            self.turn = self._ask(0x30, 0x01)[26] & 3
            self._brightness(self.config.brightness)
            if not self.direct:  # the buckets free: off the screen, then deleted
                self._ask(0x38, 0x01, 0x02, 0x00)
                time.sleep(0.2)
                for bucket in BUCKETS:
                    self._ask(0x32, 0x02, bucket)
        except (usb.core.USBError, OSError) as exc:  # (OSError: the hidraw node's)
            self.close()
            self.counters.usb_errors += 1
            raise DeviceError(
                t("the {panel} did not start: {error}", panel=self.describe(), error=exc)
            ) from exc
        except BaseException:
            self.close()
            raise
        self.next, self.first, self._warned = 0, True, False
        log.info("%s, firmware %d.%d.%d, turned by %d°", self.model.label, *self.firmware,
                 self.turn * 90)  # fmt: skip

    # -- reports ----------------------------------------------------------------------------

    def _drain(self) -> None:
        """Pass by the reports that wait (the cooler's own status)."""
        for _ in range(64):
            if self.control.read(1) is None:
                return

    def _ask(self, *report: int) -> bytes:
        """``report`` written, and its answer (the id + 1, the same sub-command)."""
        self.control.write(bytes(report), WRITE_TIMEOUT_MS)
        deadline = time.monotonic() + ANSWER_S
        while (left := deadline - time.monotonic()) > 0:
            answer = self.control.read(max(1, int(left * 1000)))
            if answer and answer[0] == report[0] + 1 and answer[1] == report[1]:
                return answer
        raise DeviceError(
            t(
                "the {panel} stopped answering: {error}",
                panel=self.describe(),
                error=f"no answer to {bytes(report[:2]).hex(' ')}",
            )
        )

    def _exchange(self, *report: int) -> None:
        """``report`` written and the next report read, whatever it is (as liquidctl does where
        it does not look at the answer)."""
        self.control.write(bytes(report), WRITE_TIMEOUT_MS)
        self.control.read(1000)

    def _taken(self, *report: int) -> None:
        """``report`` asked; a refusal is logged once (liquidctl logs it and goes on)."""
        if self._ask(*report)[14] != TAKEN and not self._warned:
            log.warning("%s refused %s", self.describe(), bytes(report[:3]).hex(" "))
            self._warned = True

    def _brightness(self, percent: int) -> None:
        level = max(0, min(100, int(percent)))
        self.control.write(bytes((0x30, 0x02, 0x01, level, 0, 0, 0x01, self.turn)))
        time.sleep(0.1)  # (krakenlcd's pause)

    def _bulk(self, data: bytes) -> None:
        chunk = CHUNK.get(self.product, BIG_CHUNK)
        for at in range(0, len(data), chunk):
            self.device.write(BULK_OUT, data[at : at + chunk], WRITE_TIMEOUT_MS)

    # -- pictures ---------------------------------------------------------------------------

    def set_brightness(self, percent: int) -> None:
        if self.device is not None:
            self._sent(self._brightness, percent)

    def screen_off(self) -> bool:
        if self.device is None:
            return False
        try:
            self._brightness(0)  # (its next open() sets it again)
        except Exception:  # noqa: BLE001 - gone: dark anyway
            return False
        return True

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        size = (self.model.native_width, self.model.native_height)
        picture = frame.convert("RGB")
        if picture.size != size:
            picture = picture.resize(size, Image.Resampling.LANCZOS)
        picture = turned(picture, self.turn)
        if self.direct:
            self._sent(self._direct, rgb565_big(picture))
        else:
            self._sent(self._into_bucket, rgba(picture))

    def _into_bucket(self, pixels: bytes) -> None:
        bucket = BUCKETS[self.next]
        slot = MEMORY_KIB // len(BUCKETS)
        start = self.next * slot
        size = -(-(20 + len(pixels)) // 1024)  # KiB, the header with it
        self._exchange(0x36, 0x03)
        for _ in range(2):  # (twice where it held a picture)
            if self._ask(0x32, 0x02, bucket)[14] != TAKEN:
                break
        self._taken(0x32, 0x01, bucket, bucket + 1, start & 0xFF, start >> 8, size & 0xFF,
                    size >> 8, 0x01)  # fmt: skip
        self._exchange(0x36, 0x01, bucket)
        self.device.write(BULK_OUT, header(RGBA, len(pixels)), WRITE_TIMEOUT_MS)  # by itself
        self._bulk(pixels)
        self.control.write(bytes((0x36, 0x02)))
        time.sleep(0.05)  # (krakenlcd's pause)
        self._taken(0x38, 0x01, 0x04, bucket)
        self.next = (self.next + 1) % len(BUCKETS)
        self.counters.bytes += 20 + len(pixels)

    def _direct(self, pixels: bytes) -> None:
        for _ in range(2 if self.first else 1):  # (the first one after opening twice)
            self._exchange(0x36, 0x01, 0x00, 0x01, 0x06)
            self.device.write(BULK_OUT, header(RGB16, len(pixels)), WRITE_TIMEOUT_MS)
            self._bulk(pixels)
            self._exchange(0x36, 0x02)
            self.counters.bytes += 20 + len(pixels)
        self.first = False

    def _sent(self, send, *args: Any) -> None:
        import usb.core

        try:
            send(*args)
        except (usb.core.USBError, OSError) as exc:
            self.counters.usb_errors += 1  # the main loop connects again
            raise DeviceError(
                t("the {panel} stopped answering: {error}", panel=self.describe(), error=exc)
            ) from exc

    def close(self) -> None:
        if self.device is not None and self.control is not None and self.claimed:
            try:  # the screen back to the firmware's liquid temperature
                self.control.write(bytes((0x38, 0x01, 0x02, 0x00)))
            except Exception:  # noqa: BLE001 - gone already
                pass
        if self.control is not None and hasattr(self.control, "close"):
            self.control.close()
        if self.device is not None:
            usb_link.release(self.device, self.claimed, self.place)
        self.device, self.place, self.control, self.claimed = None, "", None, ()

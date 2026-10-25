"""Thermalright's Trofeo Vision screens: the 6.86" of 1280x480 (USB 0416:5302, a HID device) and
the bars of 9.16" (1920x462) and 11.3" (1920x440) (0416:5408, bulk transfers), on a cooler or
in a case. The vendor's application is TRCC.

The protocol, as the MIT-licensed turing-smart-screen (usausa, .NET: LcdDriver.TrofeoVision
and LcdDriver.TrofeoVisionLy) drives both and InfoPanel (GPL-3.0, habibrehmansg/infopanel)
documents them from the vendor's application:

- The 6.86" takes reports of 512 bytes on its HID interface. DA DB DC DD with 1 at byte 12
  asks it what it is; the answer begins the same, with 1 at byte 12, its kind at byte 5 (0x80:
  the 6.86") and a variant at byte 4. (Other Thermalright screens answer on that id too, with
  other kinds and other pictures: they are not driven here.) A picture is a JPEG: the first
  report holds a header of 20 bytes (DA DB DC DD, 2 at byte 4, the width and height at bytes
  8 and 10, 2 at byte 12, the JPEG's length at byte 16, little endian) and the JPEG's first
  492 bytes, each next one the next 512, the last filled up with zeros. No answer comes.
- The bars take bulk transfers. 2048 bytes, 02 FF with 1 at byte 8, ask what it is; it answers
  512 bytes, 03 FF with 1 at byte 8, its kind at byte 20 (3 or less: the 9.16"; 4: a 6.86";
  5: the 11.3") and its variant at byte 22 (one less than usausa counts it). A picture is a
  JPEG in packets of 512 bytes: a header of 16 (01 FF, the JPEG's length (32 bit), the length
  of this packet's part (16 bit), 01, the number of packets and this one's number (16 bit),
  little endian) and 496 bytes of the JPEG. Empty packets fill them up to a multiple of four;
  they go 4096 bytes at a time, and the bar answers 03 FF with 1 at byte 8.
- TRCC sends the 9.16" pictures of 1920x462 (the bar calls itself 1920x480, but some have 462
  rows, and a taller picture runs over them, InfoPanel found), turned half round unless the
  variant is 2 or 4 (the same on the 11.3"), and a 6.86" a quarter clockwise where its variant
  is 2 (usausa's notes). The JPEG is 4:2:0: some firmware cannot decode 4:4:4 fast enough.
- No command for the backlight is known: the brightness goes into the pixels. A bar that gets
  no picture for a few seconds shows its logo again, so the last picture goes again every half
  second (as usausa sends it); the 6.86"'s every second.

Only these commands are sent: no other Thermalright command is built here.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image

from libre_panel.devices import usb_link
from libre_panel.devices.base import DeviceError, Display, FrameError, KeepShowing, dimmed
from libre_panel.devices.models import MODELS, PanelModel, get_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROTOCOL = "usb-thermalright"
VENDOR_ID = 0x0416
HID_PRODUCT, BULK_PRODUCT = 0x5302, 0x5408
PRODUCTS = (BULK_PRODUCT, HID_PRODUCT)

MAGIC = bytes((0xDA, 0xDB, 0xDC, 0xDD))
REPORT = 512  # the 6.86"'s HID reports
HEADER = 20  # the picture's header in the first report
HELLO = 2048  # the bars' question
PACKET, PACKET_HEADER = 512, 16
PACKET_DATA = PACKET - PACKET_HEADER  # 496
WRITE = 4096  # bytes of packets a bulk write
MAX_JPEG = 230_000  # InfoPanel's cap (TRCC drops pictures of 450 KB)
QUALITIES = (90, 80, 70, 60, 50)  # the first whose picture fits
WRITE_TIMEOUT_MS = 3000
ANSWER_TIMEOUT_MS = 1000
MISSED = 5  # pictures in a row a bar did not answer: it is gone
AGAIN_S = {HID_PRODUCT: 1.0, BULK_PRODUCT: 0.5}  # the last picture again after this long

SIX_INCH = "thermalright-trofeo-6.86"
NINE_INCH = "thermalright-trofeo-9.16"
ELEVEN_INCH = "thermalright-trofeo-11.3"
# what the screen says it is: the 6.86"'s kind byte, and the bars' kind as usausa counts it
HID_KINDS = {0x80: SIX_INCH}
BULK_KINDS = {65: NINE_INCH, 68: SIX_INCH, 69: ELEVEN_INCH}
SIZES = {SIX_INCH: (1280, 480), NINE_INCH: (1920, 462), ELEVEN_INCH: (1920, 440)}


def models() -> list[PanelModel]:
    return [model for model in MODELS if model.protocol == PROTOCOL]


def present_model() -> PanelModel | None:
    """A Trofeo Vision on the bus (none opened): the model its USB id is first of; None for
    none, or no pyusb. The screen says which it is when it is opened."""
    product = usb_link.present(VENDOR_ID, PRODUCTS)
    if product is None:
        return None
    return next(m for m in models() if (VENDOR_ID, product) in m.usb_ids)


def hid_hello() -> bytes:
    report = bytearray(REPORT)
    report[0:4] = MAGIC
    report[12] = 1
    return bytes(report)


def hid_answer(answer: bytes) -> tuple[int, int] | None:
    """The kind and the variant the 6.86" says; None for no answer of that shape."""
    if len(answer) > HEADER and answer[:4] == MAGIC and answer[12] == 1:
        return answer[5], answer[4]
    return None


def hid_reports(jpeg: bytes, width: int, height: int) -> list[bytes]:
    """A JPEG as the 6.86"'s reports: the header and the JPEG, 512 bytes a report."""
    header = bytearray(HEADER)
    header[0:4] = MAGIC
    header[4] = 2
    header[8:10] = width.to_bytes(2, "little")
    header[10:12] = height.to_bytes(2, "little")
    header[12] = 2  # a JPEG
    header[16:20] = len(jpeg).to_bytes(4, "little")
    data = bytes(header) + jpeg
    return [data[at : at + REPORT].ljust(REPORT, b"\0") for at in range(0, len(data), REPORT)]


def bulk_hello() -> bytes:
    hello = bytearray(HELLO)
    hello[0:2] = b"\x02\xff"
    hello[8] = 1
    return bytes(hello)


def bulk_answer(answer: bytes) -> tuple[int, int] | None:
    """The kind and the variant a bar says, as usausa counts them; None for no answer."""
    if len(answer) > 22 and answer[0:2] == b"\x03\xff" and answer[8] == 1:
        return (65 if answer[20] <= 3 else 64 + answer[20]), answer[22] + 1
    return None


def answered(answer: bytes | None) -> bool:
    """Whether a bar's answer to a picture says it took it."""
    return answer is not None and answer[0:2] == b"\x03\xff" and len(answer) > 8 and answer[8] == 1


def bulk_writes(jpeg: bytes) -> list[bytes]:
    """A JPEG as a bar's packets, 4096 bytes a write."""
    count = max(1, -(-len(jpeg) // PACKET_DATA))
    packets = []
    for number in range(count):
        part = jpeg[number * PACKET_DATA : (number + 1) * PACKET_DATA]
        header = bytearray(PACKET_HEADER)
        header[0:2] = b"\x01\xff"
        header[2:6] = len(jpeg).to_bytes(4, "little")
        header[6:8] = len(part).to_bytes(2, "little")
        header[8] = 1
        header[9:11] = count.to_bytes(2, "little")
        header[11:13] = number.to_bytes(2, "little")
        packets.append(bytes(header) + part.ljust(PACKET_DATA, b"\0"))
    packets += [bytes(PACKET)] * (-count % 4)  # (the empty ones that fill up to four)
    data = b"".join(packets)
    return [data[at : at + WRITE] for at in range(0, len(data), WRITE)]


def turn(model_id: str, variant: int) -> int:
    """How far TRCC turns the pictures of this screen, clockwise (degrees)."""
    if model_id == SIX_INCH:
        return 90 if variant == 2 else 0
    return 0 if variant in (2, 4) else 180


def jpeg(picture: Image.Image) -> bytes:
    for quality in QUALITIES:
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=quality, subsampling=2)  # 4:2:0
        if buffer.tell() <= MAX_JPEG:
            return buffer.getvalue()
    raise FrameError(f"frame too detailed for the panel ({buffer.tell() // 1024} KB)")


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the picture the screen takes, which lies (landscape)."""
    if (frame.width > frame.height) != (size[0] > size[1]):
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


def turned(picture: Image.Image, degrees: int) -> Image.Image:
    if degrees == 90:
        return picture.transpose(Image.Transpose.ROTATE_270)  # a quarter clockwise
    if degrees == 180:
        return picture.transpose(Image.Transpose.ROTATE_180)
    return picture


class TrofeoDisplay(Display):
    name = "thermalright"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model
        self.device: Any = None
        self.product = 0
        self.place = ""
        self.interface = 0
        self.reports: usb_link.HidReports | None = None
        self.out: int | None = None
        self.into: int | None = None
        self.size = (1280, 480)
        self.turn = 0
        self.brightness = 100
        self.missed = 0
        self._picture: Image.Image | None = None  # the last one, before it is dimmed
        self._writes: list[bytes] = []
        self._keeper = KeepShowing(AGAIN_S[BULK_PRODUCT], self._again, name="trofeo-keep")

    def describe(self) -> str:
        return self.model.label if self.model else "Thermalright Trofeo Vision"

    def open(self) -> None:
        import usb.core

        own = tuple(pid for _vid, pid in self.model.usb_ids) if self.model else ()
        products = own + tuple(pid for pid in PRODUCTS if pid not in own)  # its own id first
        self.device, self.product, self.place = usb_link.take(VENDOR_ID, products, self.describe())
        try:
            if self.product == HID_PRODUCT:
                kind, variant = self._ask_hid()
                found = HID_KINDS.get(kind)
            else:
                kind, variant = self._ask_bulk()
                found = BULK_KINDS.get(kind)
            if found is None:
                raise DeviceError(
                    t(
                        "the {panel} is a kind Libre Panel does not know yet ({kind})",
                        panel=self.describe(),
                        kind=f"0x{kind:02x}",
                    )
                )
        except usb.core.USBError as exc:
            self.close()
            self.counters.usb_errors += 1
            raise DeviceError(
                t("the {panel} did not start: {error}", panel=self.describe(), error=exc)
            ) from exc
        except BaseException:
            self.close()
            raise
        self.model = get_model(found)
        self.size, self.turn = SIZES[found], turn(found, variant)
        log.info("%s, %dx%d, turned by %d°", self.model.label, *self.size, self.turn)
        self._keeper.every = AGAIN_S[self.product]
        self._keeper.start()

    def _ask_hid(self) -> tuple[int, int]:
        self.interface = usb_link.hid_interface(self.device) or 0
        usb_link.claim(self.device, (self.interface,))
        self.reports = usb_link.HidReports(self.device, self.interface, REPORT)
        for _ in range(2):  # (InfoPanel asks again where no answer came)
            self.reports.write(hid_hello(), WRITE_TIMEOUT_MS)
            for _ in range(8):  # (usausa reads up to eight reports for it)
                answer = self.reports.read(ANSWER_TIMEOUT_MS)
                if answer is None:
                    break
                said = hid_answer(answer)
                if said is not None:
                    return said
        raise DeviceError(t("the {panel} does not say what it is", panel=self.describe()))

    def _ask_bulk(self) -> tuple[int, int]:
        self.interface = 0
        usb_link.claim(self.device, (0,))
        self.out, self.into = usb_link.endpoints(self.device, 0)
        if self.out is None or self.into is None:
            raise DeviceError("bulk endpoints not found")
        self.device.write(self.out, bulk_hello(), WRITE_TIMEOUT_MS)
        said = bulk_answer(self._read() or b"")
        if said is None:
            raise DeviceError(t("the {panel} does not say what it is", panel=self.describe()))
        return said

    def _read(self) -> bytes | None:
        import usb.core

        try:
            return bytes(self.device.read(self.into, PACKET, ANSWER_TIMEOUT_MS))
        except usb.core.USBTimeoutError:
            return None

    # -- pictures ---------------------------------------------------------------------------

    def _encode(self, native: Image.Image) -> list[bytes]:
        data = jpeg(turned(dimmed(native, self.brightness), self.turn))
        if self.product == HID_PRODUCT:
            return hid_reports(data, *self.size)
        return bulk_writes(data)

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))
        if self.device is not None and self._picture is not None:
            with self._keeper.lock:
                self._send(self._encode(self._picture))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        native = to_native(frame, self.size)
        writes = self._encode(native)
        with self._keeper.lock:
            self._keeper.check()
            self._picture = native
            self._send(writes)

    def _send(self, writes: list[bytes]) -> None:
        import usb.core

        try:
            if self.product == HID_PRODUCT:
                for report in writes:
                    self.reports.write(report, WRITE_TIMEOUT_MS)
            else:
                for chunk in writes:
                    self.device.write(self.out, chunk, WRITE_TIMEOUT_MS)
                self.missed = 0 if answered(self._read()) else self.missed + 1
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1  # the main loop connects again
            raise DeviceError(
                t("the {panel} stopped answering: {error}", panel=self.describe(), error=exc)
            ) from exc
        self.counters.bytes += sum(len(write) for write in writes)
        self._writes = writes
        self._keeper.sent()
        if self.missed >= MISSED:
            self.counters.usb_errors += 1
            raise DeviceError(
                t(
                    "the {panel} stopped answering: {error}",
                    panel=self.describe(),
                    error=f"no answer to {self.missed} pictures",
                )
            )

    def _again(self) -> None:
        if self.device is not None and self._writes:
            self._send(self._writes)

    def close(self) -> None:
        self._keeper.stop()
        if self.device is not None:
            usb_link.release(self.device, (self.interface,), self.place)
        self.device, self.place, self.reports = None, "", None
        self._picture, self._writes, self.missed = None, [], 0

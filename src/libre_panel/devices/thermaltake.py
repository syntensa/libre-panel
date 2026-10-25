"""Thermaltake's 6" LCD of 1480x720 (USB 264a:2347; the TOUGHLIQUID Ultra's, as InfoPanel names
it), a HID device that takes JPEG pictures. The vendor's application is TT RGB Plus.

The protocol as InfoPanel (GPL-3.0, habibrehmansg/infopanel, ThermaltakePanel) documents it
from packets of the vendor's application, which ASRock's 360 LCD speaks too:

- Reports of 1024 bytes, without a report id.
- A command is text like an HTTP request: "POST conn 1", then the lines "SeqNumber=<n>" (a
  number that counts up) and "Date=<milliseconds since 1970>", for one with a body
  "ContentType=json" and "ContentLength=<n>", an empty line and the body; lines end with CR LF.
  It goes framed: 5A 00, the length (the text's + 5), the text, a checksum (the length and
  the text's bytes summed, the low byte), 5A 00. The panel answers in text, with "200" where
  it took the command.
- Opening: "POST conn 1", then "POST realtimeDisplay 1" with {"enable":true}. Closing: the same
  with {"enable":false}, which gives the screen back to the panel.
- A picture is a JPEG in pieces of 1000 bytes, each in a report after a header of 24 bytes: 5C
  03 FD 00, the number of pieces and this piece's number (16 bit, big endian), 01, zeros.
- InfoPanel dims the picture itself and streams without a pause; Libre Panel dims it too ("POST
  brightness 1", which InfoPanel builds but does not send, is not sent) and sends the last
  picture again after a second without one.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from PIL import Image

from libre_panel.devices import usb_link
from libre_panel.devices.base import DeviceError, Display, FrameError, KeepShowing, dimmed
from libre_panel.devices.models import PanelModel, get_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROTOCOL = "usb-thermaltake"
VENDOR_ID, PRODUCT_ID = 0x264A, 0x2347
MODEL_ID = "thermaltake-lcd-6"
REPORT = 1024
PIECE_HEADER = 24
PIECE = REPORT - PIECE_HEADER  # 1000 bytes of the JPEG a report
MAX_JPEG = 512 * 1024
QUALITIES = (85, 75, 65, 50)  # (InfoPanel's default is 85)
WRITE_TIMEOUT_MS = 5000
ANSWER_TIMEOUT_MS = 2000
ANSWER_PAUSE_S = 0.05  # InfoPanel's, between a command and the read of its answer
AGAIN_S = 1.0


def present_model() -> PanelModel | None:
    """The LCD on the bus (not opened); None for none, or no pyusb."""
    if usb_link.present(VENDOR_ID, (PRODUCT_ID,)) is None:
        return None
    return get_model(MODEL_ID)


def framed(text: str) -> bytes:
    """A command's text, framed: 5A 00, the length, the text, the checksum, 5A 00."""
    content = text.encode("ascii")
    length = len(content) + 5
    if length > 0xFF:
        raise ValueError("a command is at most 250 bytes")
    checksum = (length + sum(content)) & 0xFF
    return b"\x5a\x00" + bytes((length,)) + content + bytes((checksum,)) + b"\x5a\x00"


def command(name: str, number: int, body: str | None = None, now: float | None = None) -> bytes:
    """``name`` ("POST conn 1") as the panel takes it, with its sequence number and the date."""
    lines = [
        name,
        f"SeqNumber={number}",
        f"Date={int((time.time() if now is None else now) * 1000)}",
    ]
    if body is not None:
        lines += ["ContentType=json", f"ContentLength={len(body)}"]
    return framed("\r\n".join(lines) + "\r\n\r\n" + (body or ""))


def pieces(jpeg: bytes) -> list[bytes]:
    """A JPEG as the panel's reports: a header of 24 bytes and 1000 bytes of it each."""
    count = max(1, -(-len(jpeg) // PIECE))
    reports = []
    for number in range(count):
        head = bytearray(PIECE_HEADER)
        head[0:4] = b"\x5c\x03\xfd\x00"
        head[4:6] = count.to_bytes(2, "big")
        head[6:8] = number.to_bytes(2, "big")
        head[8] = 0x01
        reports.append(
            bytes(head) + jpeg[number * PIECE : (number + 1) * PIECE].ljust(PIECE, b"\0")
        )
    return reports


def jpeg(picture: Image.Image) -> bytes:
    for quality in QUALITIES:
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_JPEG:
            return buffer.getvalue()
    raise FrameError(f"frame too detailed for the panel ({buffer.tell() // 1024} KB)")


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the panel's picture, which lies (1480x720)."""
    if (frame.width > frame.height) != (size[0] > size[1]):
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


class ThermaltakeDisplay(Display):
    name = "thermaltake"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model or get_model(MODEL_ID)
        self.device: Any = None
        self.place = ""
        self.interface = 0
        self.reports: usb_link.HidReports | None = None
        self.number = 100  # the commands' sequence number (InfoPanel starts at 100)
        self.brightness = 100
        self._picture: Image.Image | None = None
        self._pieces: list[bytes] = []
        self._keeper = KeepShowing(AGAIN_S, self._again, name="thermaltake-keep")

    def describe(self) -> str:
        return self.model.label

    @property
    def size(self) -> tuple[int, int]:
        return (max(self.model.native_width, self.model.native_height),
                min(self.model.native_width, self.model.native_height))  # fmt: skip

    def open(self) -> None:
        import usb.core

        self.device, _pid, self.place = usb_link.take(VENDOR_ID, (PRODUCT_ID,), self.describe())
        try:
            self.interface = usb_link.hid_interface(self.device) or 0
            usb_link.claim(self.device, (self.interface,))
            self.reports = usb_link.HidReports(self.device, self.interface, REPORT)
            self._drain()
            for name, body in (
                ("POST conn 1", None),
                ("POST realtimeDisplay 1", '{"enable":true}'),
            ):
                if not self._ask(name, body):
                    raise DeviceError(
                        t(
                            "the {panel} did not start: {error}",
                            panel=self.describe(),
                            error=f"{name}: no 200",
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
        log.info("%s, %dx%d", self.model.label, *self.size)
        self._keeper.start()

    def _drain(self) -> None:
        for _ in range(16):
            if self.reports.read(10) is None:
                return

    def _ask(self, name: str, body: str | None = None) -> bool:
        """Whether the panel took the command (its answer says 200)."""
        self.reports.write(command(name, self.number, body), WRITE_TIMEOUT_MS)
        self.number += 1
        time.sleep(ANSWER_PAUSE_S)
        answer = self.reports.read(ANSWER_TIMEOUT_MS)
        return answer is not None and b"200" in answer

    # -- pictures ---------------------------------------------------------------------------

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))
        if self.device is not None and self._picture is not None:
            with self._keeper.lock:
                self._send(pieces(jpeg(dimmed(self._picture, self.brightness))))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        native = to_native(frame, self.size)
        reports = pieces(jpeg(dimmed(native, self.brightness)))
        with self._keeper.lock:
            self._keeper.check()
            self._picture = native
            self._send(reports)

    def _send(self, reports: list[bytes]) -> None:
        import usb.core

        try:
            for report in reports:
                self.reports.write(report, WRITE_TIMEOUT_MS)
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1  # the main loop connects again
            raise DeviceError(
                t("the {panel} stopped answering: {error}", panel=self.describe(), error=exc)
            ) from exc
        self.counters.bytes += REPORT * len(reports)
        self._pieces = reports
        self._keeper.sent()

    def _again(self) -> None:
        if self.device is not None and self._pieces:
            self._send(self._pieces)

    def close(self) -> None:
        self._keeper.stop()
        if self.reports is not None:
            try:  # the screen back to the panel
                self._ask("POST realtimeDisplay 1", '{"enable":false}')
            except Exception:  # noqa: BLE001 - gone already
                pass
        if self.device is not None:
            usb_link.release(self.device, (self.interface,), self.place)
        self.device, self.place, self.reports = None, "", None
        self._picture, self._pieces = None, []

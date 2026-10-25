"""BeadaPanel (NXElec): USB screens from the 2.1" round one to the bars of 8.8" and more, all on
USB 4e58:1001; the panel says which it is.

The protocol, as the vendor's own sources show it (its Linux driver JT365/beada, GPL-2.0+; the
Raspberry Pi daemon and the Panel-Link and Status-Link demos of NXElec, GPL-3.0; its answers
in InfoPanel's issue 67) and as InfoPanel (GPL-3.0, habibrehmansg/infopanel) drives it:

- Interface 0 has two channels: Status-Link (bulk OUT 0x02, IN 0x82) for the panel's state,
  Panel-Link (bulk OUT 0x01) for the pictures.
- A Status-Link message is "STATUS-LINK", version 1, its type, 0, a sequence number (0) and the
  message's length (16 bit), a checksum of the 18 bytes before it (the one's complement of
  their sum in 16-bit words, the carries folded in), then its payload. Type 1 asks for the
  panel's information: after the answer's header of 20 bytes come the firmware's version (16
  bit), the Panel-Link and Status-Link versions, the platform, the model, the serial number
  (64), the screen's width and height (16 bit), its storage (32 bit) and its highest and
  present backlight. Type 2 resets the Panel-Link channel (the panel wants a second after
  it), type 3 sets the backlight (one byte, up to the highest).
- A Panel-Link tag is 270 bytes: "PANEL-LINK", version 1, its type, a format of 256 bytes and
  the checksum of the 268 before it. The tag that starts a stream (type 5; 1 where the panel's
  Panel-Link version is 1) carries "video/x-raw, format=RGB16, width=W, height=H,
  framerate=0/1" with the size the panel says; each picture follows as it is: W x H pixels
  of RGB565, little endian. Type 6 (2) ends the stream.
- The vendor's faster "write-through" stream ("image/x-raw", a framebuffer's size from a table
  of models, on firmware 710 and later) is not used: the plain one works on every model, as
  the vendor says, if the pictures have the size the tag says.
- The sources stream all the time; Libre Panel sends the last picture again after a second
  without one, so the panel is never left waiting long.

Libre Panel sends these messages only (no storage, no clock, nothing the panel keeps).
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Any

import numpy as np
from PIL import Image

from libre_panel.devices import usb_link
from libre_panel.devices.base import DeviceError, Display, KeepShowing
from libre_panel.devices.models import MODELS, PanelModel, get_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROTOCOL = "usb-beada"
VENDOR_ID, PRODUCT_ID = 0x4E58, 0x1001
STATUS_OUT, STATUS_IN, MEDIA_OUT = 0x02, 0x82, 0x01

GET_INFO, RESET_LINK, SET_BACKLIGHT = 1, 2, 3  # Status-Link
START, END = 5, 6  # Panel-Link, on a panel whose Panel-Link version is 2 or more
START_V1, END_V1 = 1, 2  # on one of version 1
TAG = 270
INFO = 80  # the information's bytes after the answer's header
RESET_SETTLE_S = 1.0  # after the reset, as InfoPanel waits
WRITE_TIMEOUT_MS = 3000
READ_TIMEOUT_MS = 2000
AGAIN_S = 1.0  # the last picture again after this long without one

# the vendor's model codes (the information's byte 5): its name, and the model of the catalog
CODES = {
    0: ("5", "beadapanel-5"),
    1: ("7", "beadapanel-7"),
    2: ("6", "beadapanel-6"),
    3: ("3", "beadapanel-3"),
    4: ("4", "beadapanel-4"),
    10: ("5C", "beadapanel-5"),
    11: ("5T", "beadapanel-5"),
    12: ("7C", "beadapanel-7"),
    13: ("3C", "beadapanel-3"),
    14: ("4C", "beadapanel-4"),
    15: ("6C", "beadapanel-6"),
    16: ("6S", "beadapanel-6"),
    17: ("2", "beadapanel-2"),
    18: ("2W", "beadapanel-2w"),
    19: ("7S", "beadapanel-7s"),
    20: ("5S", "beadapanel-5s"),
    21: ("8", "beadapanel-8"),
    22: ("11", "beadapanel-11"),
    23: ("9", "beadapanel-9"),
    24: ("Y", "beadapanel-8"),
    25: ("X", "beadapanel-11"),
    26: ("Z", "beadapanel-9"),
}


def models() -> list[PanelModel]:
    return [model for model in MODELS if model.protocol == PROTOCOL]


def present_model() -> PanelModel | None:
    """A BeadaPanel on the bus (none opened); None for none, or no pyusb. Which one it is the
    panel says when it is opened: until then, the first of the catalog."""
    if usb_link.present(VENDOR_ID, (PRODUCT_ID,)) is None:
        return None
    return models()[0]


def checksum(data: bytes) -> int:
    """The one's complement of the sum of ``data`` in 16-bit words (little endian), the carries
    folded in."""
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"<{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def status_message(kind: int, payload: bytes = b"") -> bytes:
    header = b"STATUS-LINK" + bytes((1, kind, 0)) + struct.pack("<HH", 0, 20 + len(payload))
    return header + struct.pack("<H", checksum(header)) + payload


def panel_tag(kind: int, form: str = "") -> bytes:
    body = b"PANEL-LINK" + bytes((1, kind)) + form.encode("ascii").ljust(256, b"\0")
    return body + struct.pack("<H", checksum(body))


def stream_format(width: int, height: int) -> str:
    return f"video/x-raw, format=RGB16, width={width}, height={height}, framerate=0/1"


def panel_info(answer: bytes) -> dict[str, Any] | None:
    """What the panel says of itself (its serial number left out); None for no answer of that
    shape."""
    if len(answer) < 20 + INFO or answer[:11] != b"STATUS-LINK" or answer[12] != GET_INFO:
        return None
    info = answer[20 : 20 + INFO]
    width, height = struct.unpack_from("<HH", info, 70)
    return {
        "firmware": struct.unpack_from("<H", info, 0)[0],
        "panel_link": info[2],
        "platform": info[4],
        "code": info[5],
        "size": (width, height),
        "top": info[78],  # the highest backlight
    }


def rgb565(picture: Image.Image) -> bytes:
    """The picture's pixels as RGB565, little endian."""
    pixels = np.asarray(picture.convert("RGB"), dtype=np.uint16)
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    return (((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)).astype("<u2").tobytes()


def model_of(code: int, size: tuple[int, int]) -> PanelModel | None:
    """The catalog's model for the panel's code, else the first of its size; None for none."""
    if code in CODES:
        return get_model(CODES[code][1])
    wanted = tuple(sorted(size))
    return next(
        (m for m in models() if tuple(sorted((m.native_width, m.native_height))) == wanted), None
    )


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the picture the panel's stream has."""
    if (frame.width > frame.height) != (size[0] > size[1]):
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


class BeadaDisplay(Display):
    name = "beadapanel"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model
        self.device: Any = None
        self.place = ""
        self.info: dict[str, Any] = {}
        self.size = (800, 480)
        self._pixels = b""
        self._keeper = KeepShowing(AGAIN_S, self._again, name="beada-keep")

    def describe(self) -> str:
        return self.model.label if self.model else "BeadaPanel"

    def open(self) -> None:
        import usb.core

        self.device, _pid, self.place = usb_link.take(VENDOR_ID, (PRODUCT_ID,), "BeadaPanel")
        try:
            usb_link.claim(self.device, (0,))
            self._status(GET_INFO)
            try:
                answer = bytes(self.device.read(STATUS_IN, 512, READ_TIMEOUT_MS))
            except usb.core.USBTimeoutError:
                answer = b""
            info = panel_info(answer)
            if info is None or not all(info["size"]):
                raise DeviceError(t("the {panel} does not say what it is", panel="BeadaPanel"))
            model = model_of(info["code"], info["size"])
            if model is None:
                raise DeviceError(
                    t(
                        "the {panel} is a kind Libre Panel does not know yet ({kind})",
                        panel="BeadaPanel",
                        kind=str(info["code"]),
                    )
                )
            self.info, self.model, self.size = info, model, info["size"]
            self._status(RESET_LINK)
            time.sleep(RESET_SETTLE_S)
            self._status(SET_BACKLIGHT, bytes((self._level(self.config.brightness),)))
            start = START_V1 if info["panel_link"] == 1 else START
            self.device.write(MEDIA_OUT, panel_tag(start, stream_format(*self.size)),
                              WRITE_TIMEOUT_MS)  # fmt: skip
        except usb.core.USBError as exc:
            self.close()
            self.counters.usb_errors += 1
            raise DeviceError(
                t("the {panel} did not start: {error}", panel=self.describe(), error=exc)
            ) from exc
        except BaseException:
            self.close()
            raise
        name = CODES.get(info["code"], ("?",))[0]
        log.info("BeadaPanel %s (firmware %d), %dx%d", name, info["firmware"], *self.size)
        self._keeper.start()

    def _status(self, kind: int, payload: bytes = b"") -> None:
        self.device.write(STATUS_OUT, status_message(kind, payload), WRITE_TIMEOUT_MS)

    def _level(self, percent: int) -> int:
        """0-100 % -> the panel's backlight, up to the highest it said."""
        top = self.info.get("top") or 100
        return round(max(0, min(100, int(percent))) * top / 100)

    def set_brightness(self, percent: int) -> None:
        if self.device is not None:
            with self._keeper.lock:
                self._sent(self._status, SET_BACKLIGHT, bytes((self._level(percent),)))

    def screen_off(self) -> bool:
        if self.device is None:
            return False
        self._keeper.stop()
        try:
            with self._keeper.lock:
                self._status(SET_BACKLIGHT, b"\0")  # (its next open() sets it again)
        except Exception:  # noqa: BLE001 - gone: dark anyway
            return False
        return True

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        pixels = rgb565(to_native(frame, self.size))
        with self._keeper.lock:
            self._keeper.check()
            self._sent(self._picture, pixels)

    def _picture(self, pixels: bytes) -> None:
        self.device.write(MEDIA_OUT, pixels, WRITE_TIMEOUT_MS)
        self.counters.bytes += len(pixels)
        self._pixels = pixels
        self._keeper.sent()

    def _again(self) -> None:
        if self.device is not None and self._pixels:
            self._sent(self._picture, self._pixels)

    def _sent(self, send, *args: Any) -> None:
        import usb.core

        try:
            send(*args)
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1  # the main loop connects again
            raise DeviceError(
                t("the {panel} stopped answering: {error}", panel=self.describe(), error=exc)
            ) from exc

    def close(self) -> None:
        self._keeper.stop()
        if self.device is not None:
            if self.info:  # the stream's end
                end = END_V1 if self.info["panel_link"] == 1 else END
                try:
                    self.device.write(MEDIA_OUT, panel_tag(end), WRITE_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - gone already
                    pass
            usb_link.release(self.device, (0,), self.place)
        self.device, self.place, self.info, self._pixels = None, "", {}, b""

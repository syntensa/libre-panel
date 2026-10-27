"""USB displays that speak "udisp": Waveshare's USB monitors with the card image that makes
them a USB display (the 2.8" round one: an Allwinner T113 that shows what the PC sends), and
the ESP32 displays the protocol was made for. They take whole pictures, as JPEG or as raw
pixels, as fast as USB 2.0 carries them: Libre Panel drives them as it does a panel in video
mode, every frame at a steady pace.

The protocol, as chuanjinpang's xfz1986 USB display drivers (GPL-2.0, Windows and Linux) and
the firmware beside them speak it, and as the display tells it in its USB product string:

- The device is vendor-specific (``303a:2986``, ``2987`` without audio and touch), one
  interface with a bulk OUT endpoint; every picture is one transfer to it.
- Its product string says what it takes, fields between underscores:
  ``t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32`` is 480x480 (``R``), taking JPEG (``Ejpg`` and
  a quality of 1 to 10) and pixels of 16 bits (RGB565) and 32 (RGB888); ``Fps30`` would cap
  the frames a second, ``Bl20`` a JPEG at 20 KB.
- A picture is a header of 16 bytes, little-endian, and its payload: a CRC-16 of the payload
  (sent as 0: the drivers do), the type (0 RGB565, 1 RGB888, 2 YUV420, 3 JPEG), a command
  byte (0), x, y, width and height (16 bits each), then 32 bits: a frame number in the low 10
  and the payload's length in the high 22.

The display has no command for its backlight that is known: the brightness goes into the
picture. Nothing is written on its card.
"""

from __future__ import annotations

import io
import logging
import re
import struct
from typing import Any

from PIL import Image

from libre_panel.devices import usb_link
from libre_panel.devices.base import DeviceError, Display, FrameError, KeepShowing, dimmed
from libre_panel.devices.models import MODELS, PanelModel
from libre_panel.i18n import t

log = logging.getLogger(__name__)

PROTOCOL = "usb-udisp"
VENDOR_ID = 0x303A  # (Espressif's: the protocol's first displays were ESP32s)
PRODUCT_IDS = (0x2986, 0x2987)
HEADER = struct.Struct("<HBBHHHHI")  # crc16, type, cmd, x, y, width, height, id | length
RGB565, RGB888, YUV420, JPEG = 0, 1, 2, 3
MAX_PAYLOAD = (1 << 22) - 1
FRAME_IDS = 1 << 10
QUALITIES = (90, 80, 70, 60, 50)
WRITE_TIMEOUT_MS = 3000
STREAM_FPS = 30  # every frame, as a video panel gets them (less where the display says less)
AGAIN_S = 1.0  # the last picture again after this long without one


def header(kind: int, size: tuple[int, int], payload: int, frame: int = 0,
           at: tuple[int, int] = (0, 0)) -> bytes:  # fmt: skip
    """The 16 bytes before a picture of ``size`` at ``at``, ``payload`` bytes of ``kind``."""
    if not 0 <= payload <= MAX_PAYLOAD:
        raise FrameError(f"a picture of {payload} bytes is more than the display takes")
    packed = (frame % FRAME_IDS) | (payload << 10)
    return HEADER.pack(0, kind, 0, at[0], at[1], size[0], size[1], packed)


def described(product: str) -> dict[str, Any] | None:
    """What a product string says: ``{"size": (w, h), "jpeg": quality or None, "rgb16",
    "rgb32", "fps", "limit"}``; None where it is no udisp display's (no ``R<w>x<h>``)."""
    fields = (product or "").split("_")
    said: dict[str, Any] = {"size": None, "jpeg": None, "rgb16": False, "rgb32": False,
                            "fps": None, "limit": None}  # fmt: skip
    for field in fields:
        if found := re.fullmatch(r"R(\d+)x(\d+)", field):
            said["size"] = (int(found[1]), int(found[2]))
        elif found := re.fullmatch(r"Ejpg(\d+)", field):
            said["jpeg"] = int(found[1])
        elif field == "Ergb16":
            said["rgb16"] = True
        elif field in ("Ergb32", "Ergb24"):
            said["rgb32"] = True
        elif found := re.fullmatch(r"Fps(\d+)", field):
            said["fps"] = int(found[1])
        elif found := re.fullmatch(r"Bl(\d+)", field):
            said["limit"] = int(found[1]) * 1024
    return said if said["size"] else None


def model_for(size: tuple[int, int]) -> PanelModel | None:
    """The model of a udisp display of ``size`` (its own way round); None for a size not known
    yet."""
    for model in MODELS:
        if model.protocol == PROTOCOL and (model.native_width, model.native_height) == size:
            return model
    return None


def _product(device: Any) -> str:
    """The device's product string (pyusb asks the device for it)."""
    try:
        return device.product or ""
    except Exception:  # noqa: BLE001 - no right to ask, or gone
        return ""


def present_model() -> PanelModel | None:
    """The display on the bus (not opened), by what its product string says; None for none, a
    size not known yet, or no pyusb."""
    try:
        found = usb_link.find(VENDOR_ID, PRODUCT_IDS)
    except DeviceError:
        return None
    for device, _product_id in found:
        said = described(_product(device))
        if said is not None and (model := model_for(said["size"])) is not None:
            return model
    return None


def jpeg(picture: Image.Image, limit: int | None) -> bytes:
    """``picture`` as a baseline JPEG within ``limit`` bytes (the display's buffer, where it
    says one)."""
    most = min(limit or MAX_PAYLOAD, MAX_PAYLOAD)
    for quality in QUALITIES:
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= most:
            return buffer.getvalue()
    raise FrameError(f"frame too detailed for the display ({buffer.tell() // 1024} KB)")


def rgb565_value(colour: tuple[int, int, int]) -> int:
    """One colour as the display's 16 bits."""
    r, g, b = colour
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def rgb565(picture: Image.Image) -> bytes:
    """``picture``'s pixels as 16 bits each, little-endian: red in the top 5, green in the
    middle 6, blue in the low 5."""
    import numpy as np

    pixels = np.asarray(picture.convert("RGB"), dtype=np.uint16)
    packed = ((pixels[..., 0] >> 3) << 11) | ((pixels[..., 1] >> 2) << 5) | (pixels[..., 2] >> 3)
    return packed.astype("<u2").tobytes()


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the display's picture."""
    if (frame.width > frame.height) != (size[0] > size[1]):
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


class UdispDisplay(Display):
    name = "udisp"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model or next(m for m in MODELS if m.protocol == PROTOCOL)
        self.device: Any = None
        self.place = ""
        self.endpoint: int | None = None
        self.said: dict[str, Any] = {}
        self.brightness = 100
        self.frame = 0
        self._last: bytes = b""
        self._keeper = KeepShowing(AGAIN_S, self._again, name="udisp-keep")

    def describe(self) -> str:
        return self.model.label

    @property
    def size(self) -> tuple[int, int]:
        return (self.model.native_width, self.model.native_height)

    @property
    def streaming(self) -> bool:  # type: ignore[override]
        """Every frame at a steady pace, values gliding and graphs scrolling, as a panel in video
        mode is driven: the display takes whole pictures that fast."""
        return self.device is not None

    @property
    def stream_fps(self) -> int:  # type: ignore[override]
        return min(STREAM_FPS, self.said.get("fps") or STREAM_FPS)

    def open(self) -> None:
        import usb.core

        self.device, _pid, self.place = usb_link.take(VENDOR_ID, PRODUCT_IDS, self.describe())
        try:
            said = described(_product(self.device))
            if said is None or said["size"] != self.size:
                got = "×".join(map(str, said["size"])) if said else "?"
                raise DeviceError(
                    t(
                        "the USB display shows {size}, not the {panel}'s size",
                        size=got,
                        panel=self.describe(),
                    )  # fmt: skip
                )
            self.said = said
            usb_link.claim(self.device, (0,))
            self.endpoint, _into = usb_link.endpoints(self.device, 0)
            if self.endpoint is None:
                raise DeviceError(t("the {panel} has no endpoint for pictures",
                                    panel=self.describe()))  # fmt: skip
        except usb.core.USBError as exc:
            self.close()
            self.counters.usb_errors += 1
            raise DeviceError(
                t("the {panel} did not start: {error}", panel=self.describe(), error=exc)
            ) from exc
        except BaseException:
            self.close()
            raise
        log.info("%s, %dx%d, takes %s", self.model.label, *self.size, self._takes())
        self._keeper.start()

    def _takes(self) -> str:
        kinds = [f"JPEG {self.said['jpeg']}" if self.said.get("jpeg") else ""]
        kinds += [
            "RGB565" if self.said.get("rgb16") else "",
            "RGB888" if self.said.get("rgb32") else "",
        ]
        return ", ".join(kind for kind in kinds if kind) or "?"

    def _encoded(self, picture: Image.Image) -> bytes:
        """The picture as the display takes it: JPEG where it does (a tenth of the bytes of
        the pixels), else raw pixels."""
        if self.said.get("jpeg") or not (self.said.get("rgb32") or self.said.get("rgb16")):
            data = jpeg(picture, self.said.get("limit"))
            kind = JPEG
        elif self.said.get("rgb32"):
            data, kind = picture.tobytes(), RGB888  # (R, G, B, a byte each)
        else:
            data, kind = rgb565(picture), RGB565
        self.frame += 1
        return header(kind, picture.size, len(data), self.frame) + data

    # -- pictures ---------------------------------------------------------------------------

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        message = self._encoded(dimmed(to_native(frame, self.size), self.brightness))
        with self._keeper.lock:
            self._keeper.check()
            self._send(message)

    def _send(self, message: bytes) -> None:
        import usb.core

        try:
            self.device.write(self.endpoint, message, WRITE_TIMEOUT_MS)
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1  # the main loop connects again
            raise DeviceError(
                t("the {panel} stopped answering: {error}", panel=self.describe(), error=exc)
            ) from exc
        self.counters.bytes += len(message)
        self._last = message
        self._keeper.sent()

    def _again(self) -> None:
        if self.device is not None and self._last:
            self._send(self._last)

    def close(self) -> None:
        self._keeper.stop()
        if self.device is not None:
            usb_link.release(self.device, (0,), self.place)
        self.device, self.place, self.endpoint = None, "", None
        self._last = b""

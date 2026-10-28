"""USB displays that speak "udisp": Waveshare's USB monitors with the card image that makes
them a USB display (the 2.8" round one: an Allwinner T113 that shows what the PC sends), and
the ESP32 displays the protocol was made for. They take whole pictures, as JPEG or as raw
pixels, as fast as USB 2.0 carries them: Libre Panel streams them as it does a TURZX panel in
video mode, every frame at a steady pace.

The protocol, as chuanjinpang's xfz1986 USB display drivers (GPL-2.0, Windows and Linux) and
the firmware beside them speak it, and as the display tells it in its USB product string:

- The device is vendor-specific (``303a:2986``, ``2987`` without audio and touch), one
  interface with a bulk OUT endpoint; every picture is one transfer to it.
- Its product string says what it takes, fields between underscores:
  ``t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32`` is 480x480 (``R``), taking JPEG (``Ejpg`` and
  a quality of 1 to 10) and pixels of 16 bits (RGB565) and 32 (RGB888); ``Fps30`` would cap
  the frames a second, ``Bl20`` a JPEG at 20 KB. The kind of picture named first is the one
  it is sent: the drivers send that one and no other ("first is high prio"). The others may
  never be drawn: Waveshare's 2.8" names raw pixels after JPEG, but the program on its card
  that draws the pictures decodes JPEG only, and raw pixels arrive and leave it black (#30).
- A picture is a header of 16 bytes, little-endian, and its payload: a CRC-16 of the payload
  (sent as 0: the drivers do), the type (0 RGB565, 1 RGB888, 2 YUV420, 3 JPEG), a command
  byte (0), x, y, width and height (16 bits each), then 32 bits: a frame number in the low 10
  and the payload's length in the high 22.
- A transfer ends with a packet shorter than the endpoint's (512 bytes). A picture of whole
  packets has none, and a display that waits for one would take the next picture for the rest
  of it: after such a picture the Windows driver sends a header of type 0xFF, a picture of 1x1
  with nothing in it, which a display that needs no end passes over as a command it does not
  know (the ESP32 firmware does). Libre Panel ends such a picture so too.

Waveshare's 2.8" on its USB display image (``t113udisp``) shows a picture turned by 180°: the
picture's top is at the edge away from the cable, upright as the display is mounted (#33).
Libre Panel turns what it sends it, so that it is upright with *Upside down* off.

The display has no command for its backlight that is known: the brightness goes into the
picture, and a change of it sends the last picture again at once. Nothing is written on its
card.
"""

from __future__ import annotations

import io
import logging
import re
import struct
import time
from collections.abc import Callable
from typing import Any

from PIL import Image

from libre_panel.config import JPEG_LAYOUTS
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
END = HEADER.pack(0, 0xFF, 0, 0, 0, 1, 1, 0)  # after a picture of whole packets
MAX_PAYLOAD = (1 << 22) - 1
FRAME_IDS = 1 << 10
QUALITIES = (90, 80, 70, 60, 50)  # a JPEG's: [device] jpeg_quality, then lower where too large
WRITE_TIMEOUT_MS = 3000
REACHED = 0.9  # of the pictures a second the display came to: the most it is driven at
TRIAL = 24  # the first frames, timed for how many the display takes a second
NAMES = {RGB565: "RGB565", RGB888: "RGB888", YUV420: "YUV420", JPEG: "JPEG"}
AGAIN_S = 1.0  # the last picture again after this long without one
TURNED = ("t113udisp",)  # displays that show a picture turned by 180° (the 2.8": #33)


def header(kind: int, size: tuple[int, int], payload: int, frame: int = 0,
           at: tuple[int, int] = (0, 0)) -> bytes:  # fmt: skip
    """The 16 bytes before a picture of ``size`` at ``at``, ``payload`` bytes of ``kind``."""
    if not 0 <= payload <= MAX_PAYLOAD:
        raise FrameError(f"a picture of {payload} bytes is more than the display takes")
    packed = (frame % FRAME_IDS) | (payload << 10)
    return HEADER.pack(0, kind, 0, at[0], at[1], size[0], size[1], packed)


def described(product: str) -> dict[str, Any] | None:
    """What a product string says: ``{"name": the display's own (its first field), "size":
    (w, h), "kind": the kind of picture named first (None for none), "jpeg": quality or None,
    "rgb16", "rgb32", "fps", "limit"}``; None where it is no udisp display's (no
    ``R<w>x<h>``)."""
    fields = (product or "").split("_")
    said: dict[str, Any] = {"name": fields[0], "size": None, "kind": None, "jpeg": None,
                            "rgb16": False, "rgb32": False, "fps": None,
                            "limit": None}  # fmt: skip
    named: list[int] = []
    for field in fields:
        if found := re.fullmatch(r"R(\d+)x(\d+)", field):
            said["size"] = (int(found[1]), int(found[2]))
        elif found := re.fullmatch(r"Ejpg(\d+)", field):
            said["jpeg"] = int(found[1])
            named.append(JPEG)
        elif field == "Ergb16":
            said["rgb16"] = True
            named.append(RGB565)
        elif field in ("Ergb32", "Ergb24"):
            said["rgb32"] = True
            named.append(RGB888)
        elif found := re.fullmatch(r"Fps(\d+)", field):
            said["fps"] = int(found[1])
        elif found := re.fullmatch(r"Bl(\d+)", field):
            said["limit"] = int(found[1]) * 1024
    said["kind"] = named[0] if named else None
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


def jpeg(picture: Image.Image, limit: int | None, layout: str = "4:4:4",
         quality: int = QUALITIES[0]) -> bytes:  # fmt: skip
    """``picture`` as a baseline JPEG within ``limit`` bytes (the display's buffer, where it
    says one): its colours at every pixel ("4:4:4", as the protocol's drivers send theirs:
    coloured lines and small text stay sharp), or at every second pixel each way ("4:2:0", as
    most JPEGs are made: for a decoder that shows only those). At ``quality`` ([device]
    jpeg_quality), lower only where it would be more than ``limit``."""
    most = min(limit or MAX_PAYLOAD, MAX_PAYLOAD)
    subsampling = 2 if layout == "4:2:0" else 0
    for step in (quality, *(lower for lower in QUALITIES if lower < quality)):
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=step, subsampling=subsampling)
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


def takes(said: dict[str, Any]) -> int:
    """The kind of picture a display is sent: the one it names first, as the protocol's drivers
    do; JPEG where it names none (theirs too). The others it names are not tried: they may
    arrive and never be drawn (Waveshare's 2.8", #30)."""
    return JPEG if said.get("kind") is None else said["kind"]


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the display's picture."""
    if (frame.width > frame.height) != (size[0] > size[1]):
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


class UdispDisplay(Display):
    name = "udisp"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True,
                 clock: Callable[[], float] = time.perf_counter) -> None:  # fmt: skip
        super().__init__(config)
        self.model = model or next(m for m in MODELS if m.protocol == PROTOCOL)
        self.clock = clock
        self.kind = JPEG  # the kind of picture the display names first (takes())
        layout = getattr(config, "jpeg", "")
        self.layout = layout if layout in JPEG_LAYOUTS else JPEG_LAYOUTS[0]
        self.quality = getattr(config, "jpeg_quality", QUALITIES[0])  # (a JPEG's)
        self.rate: float | None = None  # the pictures a second it came to (_time())
        self._timed: list[float] = []
        self.device: Any = None
        self.place = ""
        self.endpoint: int | None = None
        self.packet = 512  # the endpoint's largest packet
        self.said: dict[str, Any] = {}
        self.brightness = 100
        self.turned = 0  # degrees the display shows a picture turned by (TURNED): undone
        self.frame = 0
        self._last: bytes = b""
        self._shown: Image.Image | None = None  # the last picture, before it was dimmed
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
        """The frames a second of a TURZX panel in video mode (``[video] fps``, 30 by default),
        fewer where the display says so or takes fewer."""
        wanted = self._wanted()
        return max(1, min(wanted, round(self.rate * REACHED))) if self.rate else wanted

    @property
    def picture(self) -> str:  # type: ignore[override]
        return f"JPEG {self.layout}" if self.kind == JPEG else NAMES[self.kind]

    @property
    def jpeg_layout(self) -> str | None:  # type: ignore[override]
        return self.layout if self.kind == JPEG else None

    def set_jpeg(self, layout: str) -> None:
        if layout not in JPEG_LAYOUTS:
            raise ValueError(f"a JPEG's layout is one of {', '.join(JPEG_LAYOUTS)}")
        self.layout = layout
        if hasattr(self.config, "jpeg"):
            self.config.jpeg = layout  # (and so once it is opened again)

    def _wanted(self) -> int:
        fps = self.config.video.fps
        return min(fps, self.said.get("fps") or fps)

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
            self.kind, self.rate, self._timed = takes(said), None, []
            self.turned = 180 if str(said["name"]).startswith(TURNED) else 0
            usb_link.claim(self.device, (0,))
            self.endpoint, _into = usb_link.endpoints(self.device, 0)
            if self.endpoint is None:
                raise DeviceError(t("the {panel} has no endpoint for pictures",
                                    panel=self.describe()))  # fmt: skip
            self.packet = usb_link.packet_size(self.device, self.endpoint)
        except usb.core.USBError as exc:
            self.close()
            self.counters.usb_errors += 1
            raise DeviceError(
                t("the {panel} did not start: {error}", panel=self.describe(), error=exc)
            ) from exc
        except BaseException:
            self.close()
            raise
        log.info("%s, %dx%d, sent as %s", self.model.label, *self.size, self.picture)
        self._keeper.start()

    def _encoded(self, picture: Image.Image) -> bytes:
        """The picture as the kind the display names first (:func:`takes`)."""
        if self.kind == JPEG:
            data = jpeg(picture, self.said.get("limit"), self.layout, self.quality)
        elif self.kind == RGB888:
            data = picture.tobytes()  # (R, G, B, a byte each)
        else:
            data = rgb565(picture)
        self.frame += 1
        return header(self.kind, picture.size, len(data), self.frame) + data

    def _time(self, seconds: float) -> None:
        """Time the first pictures: the display takes the next one only once it has drawn the
        last (the transfer waits for it), so encoding and sending tell how many it shows a
        second, and the stream is driven no faster (:attr:`stream_fps`)."""
        if self.rate is not None:
            return
        self._timed.append(seconds)
        if len(self._timed) < TRIAL:
            return
        self.rate = len(self._timed) / max(sum(self._timed), 1e-6)
        self._timed = []
        log.info("%s: %.0f pictures a second (%s)", self.describe(), self.rate, self.picture)

    # -- pictures ---------------------------------------------------------------------------

    def set_brightness(self, percent: int) -> None:
        """The brightness goes into the picture: the last one goes again at once, so that a
        change shows also while nothing new is drawn (the panel test watches it)."""
        brightness = max(0, min(100, int(percent)))
        changed, self.brightness = brightness != self.brightness, brightness
        if changed and self.device is not None and self._shown is not None:
            message = self._encoded(dimmed(self._shown, brightness))
            try:
                with self._keeper.lock:  # (a fault of the keeper's stays for show() to raise)
                    self._send(message)
            except DeviceError as exc:  # (the next frame finds it out too and connects again)
                log.debug("%s: the picture at the new brightness did not go: %s",
                          self.describe(), exc)  # fmt: skip

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the {panel} is not open", panel=self.describe()))
        began = self.clock()
        picture = to_native(frame, self.size)
        if self.turned:
            picture = picture.transpose(Image.Transpose.ROTATE_180)
        self._shown = picture
        message = self._encoded(dimmed(picture, self.brightness))
        with self._keeper.lock:
            self._keeper.check()
            self._send(message)
        self._time(self.clock() - began)

    def _send(self, message: bytes) -> None:
        import usb.core

        ended = len(message) % self.packet == 0  # (nothing short ends it: END does)
        try:
            self.device.write(self.endpoint, message, WRITE_TIMEOUT_MS)
            if ended:
                self.device.write(self.endpoint, END, WRITE_TIMEOUT_MS)
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1  # the main loop connects again
            raise DeviceError(
                t("the {panel} stopped answering: {error}", panel=self.describe(), error=exc)
            ) from exc
        self.counters.bytes += len(message) + (len(END) if ended else 0)
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
        self._last, self._shown = b"", None

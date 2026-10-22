"""JONSBO DS339: a 3.39" screen of 376x960 (960x376 lying down) that sits on a graphics card
or in a case, on a USB 2.0 header. It is a USB display chip, the MacroSilicon MS9132
(345f:9132), as USB-to-HDMI adapters have it too: Libre Panel opens it only where
``device.model = "jonsbo-ds339"`` says so, never by ``auto``.

The protocol as InfoPanel (GPL-3.0, habibrehmansg/infopanel; from the ms912x Linux drivers and
a USB capture of the vendor's application on a DS339) documents it:

- It is controlled over its HID interface (0) with 8-byte feature reports, each written
  (SET_REPORT) and read back (GET_REPORT): A6 writes a setting, B5 reads a register, B6
  writes one, C5/C6 address and write internal memory, F5 reads its flash. The reply's bytes
  3-7 are the data.
- Opening it replays the vendor's start: registers, power on (A6 07), its display control
  with the output off, then its EDID, 4 bytes at a time (B5 C0 xx). The EDID says the
  panel's size; a "VIC" (a mode number of the vendor's) goes with each size.
- The mode: the input size and RGB888 (A6 01), the output by its VIC (A6 02), and A6 04 01
  to take frames; 0.4 s after it the first frame may come.
- A frame goes to bulk endpoint 0x04 of interface 3, in pieces of 64 KB: FF 00, x/16, y (16
  bit), width/16 (rounded down), height | 0x8000 (16 bit, big endian), the pixels as BGR,
  and FF C0 00 00 00 00 00 00. After each the panel's state is read (B5 00 32).
- The output is turned on (A6 05 01) only once two frames are in: turned on before, the chip
  stops taking frames.
- No command for the backlight is known: the brightness goes into the pixels. A6 07 00 turns
  the panel off; the next open() turns it on again.

On Windows the frames need the vendor's driver on interface 3 (installed with its
application); the HID interface needs none.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PIL import Image

from libre_panel.devices.base import (
    DeviceError,
    Display,
    KeepShowing,
    dimmed,
    leave_place,
    take_place,
)
from libre_panel.devices.models import PanelModel, get_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

VENDOR_ID = 0x345F
PRODUCT_ID = 0x9132
MODEL_ID = "jonsbo-ds339"
CONTROL, VIDEO = 0, 3  # the interfaces: HID feature reports, and the frames
ENDPOINT_OUT = 0x04
CHUNK = 64 * 1024
WRITE_TIMEOUT_MS = 5000
REPORT_TIMEOUT_MS = 1000
FIRST_FRAME_S = 0.4  # after A6 04 01, as the vendor's application waits
AGAIN_S = 1.0  # the last frame again after this long without one (the vendor streams)
TRAILER = bytes((0xFF, 0xC0, 0, 0, 0, 0, 0, 0))


def _report(*data: int) -> bytes:
    return bytes(data) + bytes(8 - len(data))


STATUS = _report(0xB5, 0x00, 0x32)
# power, registers and the EDID, as the vendor's application starts the chip
START = (
    _report(0xB5, 0xFF, 0x00),
    _report(0xB5, 0xF0, 0x00),
    _report(0xB5, 0x00, 0x31),
    _report(0xB5, 0x00, 0x30),
    _report(0xB5, 0xF4, 0x39),
    _report(0xF5, 0x00, 0x1F, 0xE0),
    _report(0xF5, 0x00, 0x1F, 0xE8),
    _report(0xF5, 0x00, 0x1F, 0xF0),
    _report(0xF5, 0x00, 0x1F, 0xF8),
    _report(0xA6, 0x07, 0x01, 0x02),  # power on
    _report(0xB5, 0xC4, 0x54),
    _report(0xA6, 0x05, 0x00),  # output off
    _report(0xB5, 0xC5, 0x55),
    _report(0xB5, 0xF0, 0x05),
    _report(0xB6, 0xF0, 0x05, 0x20),  # display control, the output still off
    _report(0xC5, 0xB0, 0x00),
    _report(0xC6, 0xB0, 0xDF),
    _report(0xC5, 0xA0, 0x00),
    _report(0xC6, 0xA0, 0xE4),
    _report(0xC5, 0xA0, 0x00),
    _report(0xC6, 0xA0, 0xF0),
    _report(0xB5, 0xF0, 0x16),
    _report(0xB6, 0xF0, 0x16, 0x00),
    STATUS,
    *(_report(0xB5, 0xC0, offset) for offset in range(0, 128, 4)),  # the EDID
)
VIDEO_ON = (
    _report(0xB5, 0xF0, 0x05),
    _report(0xB6, 0xF0, 0x05, 0x30),  # display control, the output on
    _report(0xA6, 0x05, 0x01),
    _report(0xB5, 0xC5, 0x55),
)
POWER_OFF = _report(0xA6, 0x07)
# the vendor's mode number for each size of panel (its framebuffer, standing on end)
VIC = {
    (480, 480): 143,
    (240, 320): 147,
    (320, 240): 148,
    (240, 240): 156,
    (480, 272): 159,
    (360, 960): 160,
    (376, 960): 171,
    (320, 960): 178,
    (600, 1600): 177,
}


def pause_after(report: bytes) -> float:
    """The pause the vendor's application makes after a report, in seconds."""
    if report[0] == 0xA6:
        return {0x07: 0.105, 0x05: 0.015, 0x03: 0.015, 0x01: 0.015, 0x02: 0.025}.get(
            report[1], 0.003
        )
    return 0.108 if report[:3] == STATUS[:3] else 0.003


def edid_size(edid: bytes) -> tuple[int, int] | None:
    """The size of the preferred timing of an EDID's block 0; None for no EDID."""
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    width = ((edid[0x3A] & 0xF0) << 4) | edid[0x38]
    height = ((edid[0x3D] & 0xF0) << 4) | edid[0x3B]
    return (width, height) if width and height else None


def frame_packet(native: Image.Image) -> bytes:
    """A frame as the chip takes it: its header, the pixels as BGR and the trailer."""
    width, height = native.size
    header = bytes((0xFF, 0x00, 0x00, 0x00, 0x00, width // 16)) + (height | 0x8000).to_bytes(
        2, "big"
    )
    return header + native.convert("RGB").tobytes("raw", "BGR") + TRAILER


def to_native(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """A frame turned and sized into the framebuffer, which stands on end: a landscape frame
    a quarter turn clockwise, as on the TURZX panels."""
    if frame.width > frame.height:
        frame = frame.transpose(Image.Transpose.ROTATE_270)
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    return frame.convert("RGB")


def _backend() -> Any:
    from libre_panel.devices.turzx_usb import _backend as backend

    return backend()


def _device() -> Any:
    try:
        import usb.core
    except ImportError as exc:
        raise DeviceError("pyusb is not installed: pip install 'libre-panel[usb]'") from exc
    try:
        return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=_backend())
    except usb.core.NoBackendError as exc:
        raise DeviceError("no libusb backend found (see docs/HARDWARE.md)") from exc


class Ds339Display(Display):
    name = "jonsbo-ds339"

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model or get_model(MODEL_ID)
        self.device: Any = None
        self.place = ""
        self.size = (376, 960)  # the framebuffer, standing on end; its EDID may say otherwise
        self.brightness = 100
        self.frames = 0
        self._picture: Image.Image | None = None
        self._packet = b""
        self._keeper = KeepShowing(AGAIN_S, self._again, name="ds339-keep")

    def describe(self) -> str:
        return self.model.label

    # -- the chip -------------------------------------------------------------------------

    def report(self, report: bytes) -> bytes:
        """One feature report written and read back; the reply's data (5 bytes)."""
        self.device.ctrl_transfer(0x21, 0x09, 0x0300, CONTROL, report, REPORT_TIMEOUT_MS)
        reply = bytes(self.device.ctrl_transfer(0xA1, 0x01, 0x0300, CONTROL, 8,
                                                REPORT_TIMEOUT_MS))  # fmt: skip
        time.sleep(pause_after(report))
        return reply[3:8]

    def open(self) -> None:
        import usb.core

        device = _device()
        if device is None:
            raise DeviceError(t("no JONSBO DS339 on the USB bus"))
        place = f"usb:{getattr(device, 'bus', None)}.{getattr(device, 'address', None)}"
        if not take_place(place):
            raise DeviceError(t("another panel of Libre Panel has the DS339 open"))
        self.device, self.place, self.frames = device, place, 0
        try:
            self._claim()
            edid = bytearray(128)
            for report in START:
                reply = self.report(report)
                if report[:2] == bytes((0xB5, 0xC0)):
                    edid[report[2] : report[2] + 4] = reply[:4]
            size = edid_size(bytes(edid))
            if size is not None and size in VIC:
                self.size = size
            elif size is not None:
                log.warning("DS339: its EDID says %dx%d, a size without a known mode", *size)
            self._mode(*self.size, VIC.get(self.size, VIC[(376, 960)]))
        except usb.core.USBError as exc:
            self.close()
            self.counters.usb_errors += 1
            raise DeviceError(t("the DS339 did not start: {error}", error=exc)) from exc
        except BaseException:
            self.close()
            raise
        log.info("JONSBO DS339, %dx%d", *self.size)
        self._keeper.start()

    def _claim(self) -> None:
        import usb.core
        import usb.util

        for interface in (CONTROL, VIDEO):
            try:
                if self.device.is_kernel_driver_active(interface):
                    self.device.detach_kernel_driver(interface)
            except (NotImplementedError, usb.core.USBError):
                pass  # (Windows has none to detach)
        try:
            configured = self.device.get_active_configuration().bConfigurationValue
        except usb.core.USBError:
            configured = None
        if configured != 1:
            self.device.set_configuration(1)
        for interface in (CONTROL, VIDEO):
            usb.util.claim_interface(self.device, interface)

    def _mode(self, width: int, height: int, vic: int) -> None:
        w, h = width.to_bytes(2, "big"), height.to_bytes(2, "big")
        for report in (
            STATUS,
            _report(0xB5, 0xF4, 0x39),
            _report(0xA6, 0x03, 0x03),
            _report(0xB5, 0xC5, 0x58),
            _report(0xA6, 0x01, *w, *h, 0x11, 0x00),  # in: RGB888
            _report(0xB5, 0xC5, 0x56),
            _report(0xA6, 0x02, vic, 0x00, *w, *h),  # out: the mode
            _report(0xB5, 0xC5, 0x57),
            _report(0xA6, 0x04, 0x01),  # frames are taken from now on
        ):
            self.report(report)
        time.sleep(FIRST_FRAME_S)

    # -- frames -----------------------------------------------------------------------------

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))
        if self.device is not None and self._picture is not None:
            with self._keeper.lock:
                self._send(frame_packet(dimmed(self._picture, self.brightness)))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.device is None:
            raise DeviceError(t("the DS339 is not open"))
        native = to_native(frame, self.size)
        packet = frame_packet(dimmed(native, self.brightness))
        with self._keeper.lock:
            self._keeper.check()
            self._picture = native
            self._send(packet)
            if self.frames == 1:  # the output goes on once two frames are in
                self._send(packet)

    def _send(self, packet: bytes) -> None:
        import usb.core

        try:
            for start in range(0, len(packet), CHUNK):
                self.device.write(ENDPOINT_OUT, packet[start : start + CHUNK], WRITE_TIMEOUT_MS)
                self.counters.bytes += min(CHUNK, len(packet) - start)
            self.frames += 1
            try:
                self.report(STATUS)
            except usb.core.USBError:
                pass  # (the state is only asked, as the vendor's application does)
            if self.frames == 2:
                for report in VIDEO_ON:
                    self.report(report)
        except usb.core.USBError as exc:
            self.counters.usb_errors += 1
            raise DeviceError(t("the DS339 stopped answering: {error}", error=exc)) from exc
        self._packet = packet
        self._keeper.sent()

    def _again(self) -> None:
        if self.device is not None and self._packet:
            self._send(self._packet)

    def screen_off(self) -> bool:
        if self.device is None:
            return False
        import usb.core

        try:
            self.report(POWER_OFF)
        except usb.core.USBError:
            return False
        return True

    def close(self) -> None:
        self._keeper.stop()
        if self.device is not None:
            import usb.util

            for interface in (VIDEO, CONTROL):
                try:
                    usb.util.release_interface(self.device, interface)
                except Exception:  # noqa: BLE001 - gone already
                    pass
            try:
                usb.util.dispose_resources(self.device)
            except Exception:  # noqa: BLE001
                pass
        if self.place:
            leave_place(self.place)
        self.device, self.place = None, ""
        self._picture, self._packet = None, b""

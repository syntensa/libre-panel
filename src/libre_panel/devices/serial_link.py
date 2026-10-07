"""What the serial panel drivers share: finding the port, the link, RGB565.

Serial panels (Turing rev. A-D, XuanFang, UsbPCMonitor, Kipye, WeAct) show up
as a serial port (USB CDC). Several share a USB bridge chip and its ids, so a
port is matched by USB id first and by its serial number where the vendor set
one. The protocols follow turing-smart-screen-python (GPL-3.0), which
documented them on the hardware; see docs/HARDWARE.md for their status.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops

from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import MODELS, PanelModel, models_for_usb
from libre_panel.i18n import t

log = logging.getLogger(__name__)

SERIAL_PROTOCOLS = ("serial-a", "serial-b", "serial-c", "serial-d", "serial-weact")
# Generic Linux USB gadget serial ids, which awake rev. C panels use, and so do
# other devices (a Raspberry Pi as a USB gadget, development boards). Without a
# model in the config such a port counts only with the panels' serial number.
GADGET_IDS = {(0x0525, 0xA4A7), (0x1D6B, 0x0121), (0x1D6B, 0x0106)}
GADGET_SERIAL = "20080411"


def _generic(info: Any) -> bool:
    return (info.vid, info.pid) in GADGET_IDS and info.serial_number != GADGET_SERIAL


@dataclass
class FoundPort:
    device: str  # "COM3", "/dev/ttyACM0"
    model: PanelModel
    serial_number: str | None


def _ports() -> list[Any]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise DeviceError(
            t("serial panels need pyserial: pip install 'libre-panel[serial]'")
        ) from exc
    return list(list_ports.comports())


def find_port(model: PanelModel | None = None, port: str = "") -> FoundPort | None:
    """The port of a connected serial panel: the first whose USB ids (and serial
    number, where the vendor set one) fit ``model``, or any known serial panel
    when ``model`` is None. ``port`` (device.port) limits the search to that
    port, and is trusted for ``model`` even when its ids say nothing."""
    wanted = [model] if model is not None else [m for m in MODELS if m.protocol in SERIAL_PROTOCOLS]
    for info in _ports():
        if port and info.device != port:
            continue
        if model is None and not port and _generic(info):
            continue  # some other gadget: never talked to unasked
        # a serial number that names a model settles it; else every model of the chip
        candidates = [
            m for m in models_for_usb(info.vid, info.pid, info.serial_number) if m in wanted
        ]
        if candidates:
            return FoundPort(info.device, candidates[0], info.serial_number)
    if port:
        if model is None:
            raise DeviceError(t("device.port {port}: set device.model as well", port=port))
        return FoundPort(port, model, None)
    return None


class SerialLink:
    """One open serial port. Errors become :class:`DeviceError`, so the main
    loop reconnects as with USB panels."""

    def __init__(self, device: str, baud: int = 115200, rtscts: bool = True) -> None:
        self.device = device
        try:
            import serial
        except ImportError as exc:
            raise DeviceError(
                t("serial panels need pyserial: pip install 'libre-panel[serial]'")
            ) from exc
        self._errors = (serial.SerialException, OSError)
        try:
            self.port = open_serial(device, baud, rtscts)
        except self._errors as exc:
            raise DeviceError(t("cannot open {port}: {error}", port=device, error=exc)) from exc

    def write(self, data: bytes) -> None:
        try:
            self.port.write(data)
            if sys.platform == "darwin":
                self.port.flush()  # macOS garbles bitmaps otherwise (reference library)
        except self._errors as exc:
            raise DeviceError(t("serial write failed: {error}", error=exc)) from exc

    def read(self, size: int) -> bytes:
        try:
            return bytes(self.port.read(size))
        except self._errors as exc:
            raise DeviceError(t("serial read failed: {error}", error=exc)) from exc

    def drain_input(self) -> None:
        try:
            self.port.reset_input_buffer()
        except self._errors:
            pass

    def close(self) -> None:
        try:
            self.port.close()
        except self._errors:
            pass


def open_serial(device: str, baud: int, rtscts: bool) -> Any:
    """pyserial's port (tests replace this with a simulated panel)."""
    import serial

    return serial.Serial(device, baud, timeout=1, write_timeout=10, rtscts=rtscts)


def rgb565(image: Image.Image, byteorder: str = "little") -> bytes:
    """Pixels as 16-bit RGB565, two bytes each, in ``byteorder``."""
    r, g, b = image.convert("RGB").split()
    high = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    low = ImageChops.add(g.point(lambda v: (v & 0x1C) << 3), b.point(lambda v: v >> 3))
    pair = (low, high) if byteorder == "little" else (high, low)
    return Image.merge("LA", pair).tobytes()

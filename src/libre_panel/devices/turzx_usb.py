"""Turing / TURZX V1.x USB panels (VID 0x1CBE): 2.8" round, 4.6", 5.2", 8", 8.8", 9.2", 12.3".

Status: experimental. The protocol comes from the GPL-3.0 reference
implementation turing-smart-screen-python (Matthieu Houdebine) and was
verified on real hardware (9.2", PID 0x0092) by the SPUR II project, which
also found the fixes marked below. See docs/protocol/turzx-usb.md.

Wire format: every command is one 512-byte packet. 500 bytes of plain text
([0] command id, [2:4] magic 1A 6D, [4:8] milliseconds since local midnight,
little endian, [8:] arguments) are DES-CBC encrypted with key = IV =
``slv3tuzx`` (zero padded to 504 bytes); bytes 510/511 carry the trailer
A1 1A. A payload (PNG, H.264) follows the packet in the same bulk transfer.
The panel answers on the bulk IN endpoint: byte 0 echoes the command id,
0xC8 means "accepted" (not "displayed").

Only harmless commands are used here. Restart (11), 125 and all storage/file
commands are deliberately absent; firmware is never touched.
"""

from __future__ import annotations

import io
import logging
import struct
import sys
import time
from typing import Any

from PIL import Image

from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import MODELS, PanelModel, orientation_of

log = logging.getLogger(__name__)

VENDOR_ID = 0x1CBE
DES_KEY = b"slv3tuzx"
MAGIC = b"\x1a\x6d"
TRAILER = b"\xa1\x1a"
ACK = 0xC8
# Replies are exactly 512 bytes = wMaxPacketSize, so USB ends them with a
# zero-length packet. Reading 512 leaves that ZLP in the pipe and every later
# reply is off by one until the device stalls (found by SPUR II). Asking for
# more than one packet consumes it.
READ_LEN = 1024

CMD_SYNC = 10
CMD_BRIGHTNESS = 14
CMD_FRAME_RATE = 15
CMD_UPLOAD_PNG = 102
CMD_STOP_STREAM = 123

USB_PIDS = {pid: m for m in MODELS if m.protocol == "usb-turing" for _vid, pid in m.usb_ids}


def ms_since_midnight(now: float | None = None) -> int:
    now = time.time() if now is None else now
    local = time.localtime(now)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    return int((now - midnight) * 1000) & 0xFFFFFFFF


def _cipher() -> Any:
    try:
        from Crypto.Cipher import DES
    except ImportError as exc:
        raise DeviceError("pycryptodome is missing: pip install 'libre-panel[usb]'") from exc
    return DES.new(DES_KEY, DES.MODE_CBC, iv=DES_KEY)


def build_packet(cmd: int, args: bytes = b"", now: float | None = None) -> bytes:
    if not 0 <= cmd <= 255:
        raise ValueError("command id must fit in one byte")
    if len(args) > 492:
        raise ValueError("arguments too long")
    plain = bytearray(500)
    plain[0] = cmd
    plain[2:4] = MAGIC
    plain[4:8] = struct.pack("<I", ms_since_midnight(now))
    plain[8 : 8 + len(args)] = args
    encrypted = _cipher().encrypt(bytes(plain).ljust(504, b"\x00"))
    packet = bytearray(512)
    packet[: len(encrypted)] = encrypted
    packet[510:512] = TRAILER
    return bytes(packet)


def decode_packet(packet: bytes) -> bytes:
    """Inverse of build_packet (tests, capture analysis)."""
    if len(packet) < 512 or packet[510:512] != TRAILER:
        raise ValueError("not a command packet")
    try:
        from Crypto.Cipher import DES
    except ImportError as exc:
        raise DeviceError("pycryptodome is missing") from exc
    plain = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_KEY).decrypt(bytes(packet[:504]))
    if plain[2:4] != MAGIC:
        raise ValueError("bad magic")
    return plain[:500]


def brightness_arg(percent: int) -> int:
    """0-100 % -> the panel's 0-102 scale."""
    return int(max(0, min(100, percent)) / 100 * 102)


def encode_png_rgba(image: Image.Image, compress_level: int = 2) -> bytes:
    # The panel only decodes PNG colour type 6 (RGBA). RGB frames come out
    # shifted by a byte per pixel and tiled four times (SPUR II).
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG", compress_level=compress_level)
    return buffer.getvalue()


def to_native(frame: Image.Image) -> Image.Image:
    """Rotate a theme frame into the panel's portrait framebuffer."""
    if orientation_of(*frame.size) == "landscape":
        return frame.transpose(Image.Transpose.ROTATE_270)  # verified on 9.2"
    return frame.transpose(Image.Transpose.ROTATE_180)  # per reference library, unverified


def _backend() -> Any:
    try:
        import libusb_package  # bundles libusb-1.0 for Windows/macOS

        return libusb_package.get_libusb1_backend()
    except ImportError:
        return None


class UsbTransport:
    """Bulk transport on interface 0 (EP 0x01 OUT, EP 0x81 IN, 512-byte packets)."""

    def __init__(self, device: Any, ep_out: Any, ep_in: Any, pid: int) -> None:
        self.device, self.ep_out, self.ep_in, self.pid = device, ep_out, ep_in, pid

    @classmethod
    def open(cls, pid: int | None = None) -> UsbTransport:
        try:
            import usb.core
            import usb.util
        except ImportError as exc:
            raise DeviceError("pyusb is missing: pip install 'libre-panel[usb]'") from exc
        backend = _backend()
        pids = [pid] if pid is not None else list(USB_PIDS)
        device = None
        try:
            for candidate in pids:
                device = usb.core.find(idVendor=VENDOR_ID, idProduct=candidate, backend=backend)
                if device is not None:
                    pid = candidate
                    break
        except usb.core.NoBackendError as exc:
            raise DeviceError("libusb not found: pip install libusb-package") from exc
        if device is None or pid is None:
            raise DeviceError(
                "no Turing/TURZX USB panel found. Is it plugged in, and is the TURZX app closed? "
                "(it holds the device exclusively)"
            )
        try:
            if sys.platform.startswith("linux") and device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)
            device.set_configuration()
        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 13:
                raise DeviceError(
                    "permission denied: install the udev rule from packaging/linux/"
                ) from exc
            log.debug("set_configuration: %s", exc)
        interface = usb.util.find_descriptor(device.get_active_configuration(), bInterfaceNumber=0)
        if interface is None:
            raise DeviceError("USB interface 0 not found")

        def endpoint(direction: int) -> Any:
            return usb.util.find_descriptor(
                interface,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == direction,
            )

        ep_out, ep_in = endpoint(usb.util.ENDPOINT_OUT), endpoint(usb.util.ENDPOINT_IN)
        if ep_out is None or ep_in is None:
            raise DeviceError("bulk endpoints not found")
        return cls(device, ep_out, ep_in, pid)

    def drain(self, timeout_ms: int = 50, limit: int = 64) -> int:
        """Discard replies left over from an earlier (crashed) session."""
        import usb.core

        dropped = 0
        for _ in range(limit):
            try:
                self.ep_in.read(READ_LEN, timeout_ms)
                dropped += 1
            except usb.core.USBError:
                break
        return dropped

    def command(
        self, cmd: int, args: bytes = b"", payload: bytes = b"", timeout_ms: int = 2000
    ) -> bytes:
        import usb.core

        try:
            self.ep_out.write(build_packet(cmd, args) + payload, timeout_ms)
            reply = bytes(self.ep_in.read(READ_LEN, timeout_ms))
        except usb.core.USBError as exc:
            raise DeviceError(f"USB error on command {cmd}: {exc}") from exc
        if not reply or reply[0] != cmd:
            raise DeviceError(f"command {cmd}: unexpected reply {reply[:16].hex(' ')}")
        if ACK not in (reply[1:2] + reply[8:9]):
            raise DeviceError(f"command {cmd} not accepted: {reply[:16].hex(' ')}")
        return reply

    def sync(self, attempts: int = 3) -> bytes:
        last: Exception | None = None
        for _ in range(attempts):
            self.drain()
            try:
                return self.command(CMD_SYNC)
            except DeviceError as exc:
                last = exc
        raise DeviceError(f"panel does not answer the sync command: {last}")

    def close(self) -> None:
        try:
            import usb.util

            usb.util.dispose_resources(self.device)
        except Exception:  # closing must never raise
            pass


class TurzxUsbDisplay(Display):
    """Bitmap path: every frame is sent as a full RGBA PNG (about 9 fps on 9.2").

    The smooth 25-50 fps path (H.264 background layer, command 110 + 121)
    is documented in docs/protocol/turzx-usb.md and not wired in yet.
    """

    name = "turzx-usb"

    def __init__(self, config, model: PanelModel | None = None) -> None:
        super().__init__(config)
        self.model = model
        self.transport: UsbTransport | None = None

    def open(self) -> None:
        pid = self.model.usb_ids[0][1] if self.model and self.model.usb_ids else None
        self.transport = UsbTransport.open(pid)
        self.model = self.model or USB_PIDS.get(self.transport.pid)
        reply = self.transport.sync()
        log.info(
            "connected to %s (%s)",
            self.model.label if self.model else f"PID {self.transport.pid:04x}",
            reply[2:10].split(b"\x00")[0].decode("ascii", "replace"),
        )

    def set_brightness(self, percent: int) -> None:
        if self.transport:
            self.transport.command(CMD_BRIGHTNESS, bytes([brightness_arg(percent)]))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.transport is None:
            raise DeviceError("panel not open")
        png = encode_png_rgba(to_native(frame))
        self.transport.command(CMD_UPLOAD_PNG, struct.pack(">I", len(png)), png, timeout_ms=5000)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

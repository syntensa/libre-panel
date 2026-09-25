"""A simulated Turing V1.x USB panel for tests.

It checks every packet the way the real device needs it (DES, magic, trailer,
size field, RGBA PNG) and answers with echo + ACK, including the 512-byte
replies whose zero-length packet trips up readers that ask for exactly 512.
"""

from __future__ import annotations

import io
import struct

from PIL import Image

from libre_panel.devices.turzx_usb import ACK, READ_LEN, UsbTransport, decode_packet


class Unplugged(Exception):
    """Stands in for usb.core.USBError."""


class FakePanel:
    def __init__(self, pid: int = 0x0092, native=(480, 1920)) -> None:
        self.pid = pid
        self.native = native
        self.commands: list[int] = []
        self.frames: list[Image.Image] = []
        self.brightness: list[int] = []
        self.pending: list[bytes] = []
        self.unplugged = False
        self.fail_next_writes = 0

    # endpoint OUT
    def write(self, data: bytes, timeout: int) -> None:
        import usb.core

        if self.unplugged or self.fail_next_writes:
            self.fail_next_writes = max(0, self.fail_next_writes - 1)
            raise usb.core.USBError("No such device (it may have been disconnected)", errno=19)
        data = bytes(data)
        plain = decode_packet(data[:512])  # checks trailer and magic
        cmd = plain[0]
        self.commands.append(cmd)
        payload = data[512:]
        if cmd in (101, 102, 121):
            size = struct.unpack(">I", plain[8:12])[0]
            assert size == len(payload), f"size field {size} != payload {len(payload)}"
        if cmd == 102:
            assert payload[25] == 6, "panel needs RGBA PNGs (colour type 6)"
            with Image.open(io.BytesIO(payload)) as png:
                assert png.size == self.native, f"frame {png.size} != framebuffer {self.native}"
                self.frames.append(png.copy())
        if cmd == 14:
            assert 0 <= plain[8] <= 102
            self.brightness.append(plain[8])
        reply = bytes([cmd, ACK]) + (b"turzx_00" if cmd == 10 else b"")
        self.pending.append(reply.ljust(512, b"\x00"))

    # endpoint IN
    def read(self, length: int, timeout: int) -> bytes:
        import usb.core

        assert length >= READ_LEN, "read at least 1024 bytes or the ZLP stays in the pipe"
        if self.unplugged or not self.pending:
            raise usb.core.USBTimeoutError("Operation timed out")
        return self.pending.pop(0)


def transport_for(panel: FakePanel) -> UsbTransport:
    return UsbTransport(device=None, ep_out=panel, ep_in=panel, pid=panel.pid)

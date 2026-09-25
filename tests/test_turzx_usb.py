import struct

import pytest
from PIL import Image

pytest.importorskip("Crypto")

from libre_panel.config import DeviceConfig  # noqa: E402
from libre_panel.devices.base import DeviceError  # noqa: E402
from libre_panel.devices.turzx_usb import (  # noqa: E402
    CMD_UPLOAD_PNG,
    TRAILER,
    TurzxUsbDisplay,
    UsbTransport,
    brightness_arg,
    build_packet,
    decode_packet,
    encode_png_rgba,
    to_native,
)


def test_packet_layout():
    packet = build_packet(CMD_UPLOAD_PNG, struct.pack(">I", 119_000), now=0)
    assert len(packet) == 512
    assert packet[510:512] == TRAILER
    assert packet[504:510] == b"\x00" * 6
    plain = decode_packet(packet)
    assert plain[0] == 102
    assert plain[2:4] == b"\x1a\x6d"
    assert struct.unpack(">I", plain[8:12])[0] == 119_000


def test_brightness_scale():
    assert [brightness_arg(p) for p in (0, 25, 100, 150, -5)] == [0, 25, 102, 102, 0]


def test_png_is_rgba():
    png = encode_png_rgba(Image.new("RGB", (8, 8), "red"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[25] == 6  # IHDR colour type: RGBA; RGB frames are garbled by the panel


def test_rotation_into_portrait_framebuffer():
    assert to_native(Image.new("RGB", (1920, 480))).size == (480, 1920)
    assert to_native(Image.new("RGB", (480, 1920))).size == (480, 1920)


class FakeEndpoint:
    def __init__(self, replies=None):
        self.written = []
        self.replies = list(replies or [])

    def write(self, data, timeout):
        self.written.append(bytes(data))

    def read(self, length, timeout):
        import usb.core

        if not self.replies:
            raise usb.core.USBTimeoutError("timeout")
        return self.replies.pop(0)


usb = pytest.importorskip("usb")


def make_transport(replies):
    out, inp = FakeEndpoint(), FakeEndpoint(replies)
    return UsbTransport(device=None, ep_out=out, ep_in=inp, pid=0x0092), out


def test_command_checks_the_echo():
    transport, out = make_transport([bytes([10, 0xC8]) + b"turzx_00"])
    reply = transport.command(10)
    assert reply[2:10] == b"turzx_00"
    assert decode_packet(out.written[0])[0] == 10

    transport, _ = make_transport([bytes([111, 0xC8])])  # stale reply of another command
    with pytest.raises(DeviceError, match="unexpected reply"):
        transport.command(112)


def test_show_sends_png_after_header():
    transport, out = make_transport([bytes([102, 0xC8])])
    display = TurzxUsbDisplay(DeviceConfig(driver="turzx"))
    display.transport = transport
    display.show(Image.new("RGB", (1920, 480), "blue"))
    sent = out.written[0]
    size = struct.unpack(">I", decode_packet(sent[:512])[8:12])[0]
    assert len(sent) == 512 + size
    with Image.open(__import__("io").BytesIO(sent[512:])) as png:
        assert png.size == (480, 1920) and png.mode == "RGBA"

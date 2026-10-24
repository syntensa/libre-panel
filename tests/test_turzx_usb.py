import io
import struct
import time

import pytest
from PIL import Image

pytest.importorskip("Crypto")

from libre_panel.config import ConfigError, DeviceConfig, parse_config  # noqa: E402
from libre_panel.devices import turzx_usb  # noqa: E402
from libre_panel.devices.base import DeviceError, FrameError  # noqa: E402
from libre_panel.devices.models import get_model  # noqa: E402
from libre_panel.devices.turzx_usb import (  # noqa: E402
    CMD_UPLOAD_PNG,
    TRAILER,
    TurzxUsbDisplay,
    UsbTransport,
    brightness_arg,
    build_packet,
    decode_packet,
    encode_jpeg,
    encode_png_rgba,
    firmware_of,
    jpeg_width,
    still_format,
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


def test_the_firmware_s_name_is_read_from_the_sync_reply():
    assert firmware_of(bytes([10, 0xC8]) + b"turzx_00" + bytes(502)) == "turzx_00"
    assert firmware_of(bytes([10, 0xC8, 0, 0, 0, 0, 0, 0]) + b"turzx_0001_0015" + bytes(489)) == (
        "turzx_0001_0015"
    )
    assert firmware_of(bytes([10, 0xC8]) + b"turzx_00\x9f\x11" + bytes(500)) == "turzx_00"
    assert firmware_of(bytes([10, 0xC8]) + bytes(510)) == ""


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


def test_a_jpeg_still_is_as_wide_as_the_decoder_s_layer():
    """The 9.2"'s JPEG layer is 464 wide: its 462 columns that show, in whole blocks of 16.
    Where the bezel hides nothing the framebuffer's width stays."""
    assert jpeg_width(get_model("turing-9.2-usb"), 480) == 464
    assert jpeg_width(get_model("turing-12.3-usb"), 720) == 720
    assert jpeg_width(get_model("turing-5.2-usb"), 720) == 720
    assert jpeg_width(get_model("turing-8.8-usb"), 480) == 480  # (nothing measured hidden)
    assert jpeg_width(None, 470) == 480  # a width of whole blocks, as the decoder takes them


def test_a_jpeg_still_is_cut_to_the_layer_not_stretched():
    from PIL import JpegImagePlugin

    native = Image.new("RGB", (480, 1920), (0, 0, 160))
    native.paste((255, 255, 255), (396, 0, 404, 1920))  # a white stripe at x 400
    native.paste((255, 0, 0), (464, 0, 480, 1920))  # the columns behind the bezel
    with Image.open(io.BytesIO(encode_jpeg(native, model=get_model("turing-9.2-usb")))) as jpeg:
        assert jpeg.size == (464, 1920)
        assert JpegImagePlugin.get_sampling(jpeg) == 0  # 4:4:4
        assert not jpeg.info.get("progressive")
        assert min(jpeg.getpixel((400, 900))) > 230  # where it was: cut, not squeezed
        red, _, blue = jpeg.getpixel((463, 900))
        assert blue > 120 and red < 40  # nothing of the hidden columns came in
    tall = Image.new("RGB", (720, 1280), "orange")
    with Image.open(io.BytesIO(encode_jpeg(tall, model=get_model("turing-5.2-usb")))) as jpeg:
        assert jpeg.size == (720, 1280)


def test_a_busy_jpeg_still_takes_a_lower_quality_within_the_payload_limit(monkeypatch):
    noise = Image.effect_noise((464, 400), 90).convert("RGB")
    best = len(encode_jpeg(noise))
    monkeypatch.setattr(turzx_usb, "MAX_PAYLOAD", best - 1)
    assert len(encode_jpeg(noise)) < best
    monkeypatch.setattr(turzx_usb, "MAX_PAYLOAD", 100)
    with pytest.raises(FrameError, match="too detailed"):
        encode_jpeg(noise)


@pytest.mark.parametrize(
    ("still", "model", "expected"),
    [
        ("auto", "turing-5.2-usb", "jpeg"),  # it shows nothing of a PNG
        ("auto", "turing-9.2-usb", "png"),
        ("auto", "turing-12.3-usb", "png"),
        ("auto", None, "png"),
        ("jpeg", "turing-9.2-usb", "jpeg"),
        ("png", "turing-5.2-usb", "png"),
    ],
)
def test_auto_takes_jpeg_stills_on_the_5_2_and_png_elsewhere(still, model, expected):
    assert still_format(still, get_model(model) if model else None) == expected
    display = TurzxUsbDisplay(DeviceConfig(still=still), get_model(model) if model else None)
    assert display.stills() == expected


def test_the_stills_setting_is_read_and_checked():
    assert parse_config({}).device.still == "auto"
    assert parse_config({"device": {"still": "jpeg"}}).device.still == "jpeg"
    panels = parse_config({"panels": [{"model": "turing-5.2-usb", "still": "png"}]}).panels
    assert panels[0].device.still == "png"
    with pytest.raises(ConfigError, match="device.still"):
        parse_config({"device": {"still": "webp"}})


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset to switch time zones")
def test_ms_since_midnight_east_of_utc(monkeypatch):
    """Near the epoch, midnight east of UTC lies before 1970 (mktime fails on Windows)."""
    from libre_panel.devices.turzx_usb import ms_since_midnight

    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    try:
        assert ms_since_midnight(0) == 3_600_000  # 01:00 local time
        assert ms_since_midnight(90_061.5) == ((2 * 60 + 1) * 60 + 1) * 1000 + 500
    finally:
        monkeypatch.undo()
        time.tzset()

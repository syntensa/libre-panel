"""udisp USB displays (devices/udisp.py): Waveshare's USB monitors as USB displays (#21). Each
picture is one bulk transfer: a header of 16 bytes and the JPEG (or the pixels)."""

import io
import struct

import pytest
from fake_usb import FakeUsbDevice, Interface, plug, taken
from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices import (
    beadapanel,
    lianli_usb,
    nzxt_kraken,
    serial_link,
    thermalright,
    thermaltake,
    turzx_usb,
    udisp,
)
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import get_model, models_for_usb
from libre_panel.devices.turzx import TurzxDisplay

# what Derik-for-real24's 2.8" says of itself with Waveshare's USB display image (#21)
PRODUCT = "t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32"
RED, BLUE = (230, 20, 20), (20, 20, 230)


def corner_red(size):
    frame = Image.new("RGB", size, BLUE)
    frame.paste(RED, (0, 0, size[0] // 4, size[1] // 4))
    return frame


def near(a, b, by=40):
    return all(abs(x - y) <= by for x, y in zip(a, b, strict=True))


class FakeDisplay(FakeUsbDevice):
    """The display: one interface with a bulk OUT and IN endpoint; each transfer a picture."""

    def __init__(self, product=PRODUCT) -> None:
        super().__init__([Interface(0, (0x01, 0x81))])
        self.product = product
        self.frames: list[dict] = []

    def received(self, endpoint, data):
        assert endpoint == 0x01
        crc, kind, cmd, x, y, w, h, packed = struct.unpack_from("<HBBHHHHI", data)
        payload = bytes(data[16:])
        assert (crc, cmd, x, y) == (0, 0, 0, 0) and packed >> 10 == len(payload)
        frame = {"kind": kind, "size": (w, h), "id": packed & 0x3FF, "payload": payload}
        if kind == udisp.JPEG:
            frame["picture"] = Image.open(io.BytesIO(payload)).convert("RGB")
        self.frames.append(frame)


@pytest.fixture
def bus(monkeypatch):
    def plugged(display):
        plug(monkeypatch, udisp.VENDOR_ID, (display, 0x2986))
        return display

    monkeypatch.setattr(udisp, "AGAIN_S", 30)
    return plugged


def opened(model="waveshare-2.8-udisp"):
    return TurzxDisplay(DeviceConfig(driver="turzx", model=model))


def test_what_the_product_string_says():
    said = udisp.described(PRODUCT)
    assert said == {"size": (480, 480), "jpeg": 5, "rgb16": True, "rgb32": True, "fps": None,
                    "limit": None}  # fmt: skip
    said = udisp.described("esp32udisp0_R320x240_Ejpg4_Fps30_Bl20_Ergb16")
    assert said["size"] == (320, 240) and said["fps"] == 30 and said["limit"] == 20 * 1024
    assert udisp.described("Virtual Serial Port") is None  # (no size: no udisp display)
    assert udisp.model_for((480, 480)) == get_model("waveshare-2.8-udisp")
    assert udisp.model_for((320, 240)) is None


def test_the_header_of_a_picture():
    head = udisp.header(udisp.JPEG, (480, 480), 70000, frame=1027)
    assert len(head) == 16
    assert head[:4] == b"\x00\x00\x03\x00"  # no CRC (the drivers send none), JPEG, command 0
    assert struct.unpack_from("<HHHH", head, 4) == (0, 0, 480, 480)
    packed = struct.unpack_from("<I", head, 12)[0]
    assert packed & 0x3FF == 1027 % 1024 and packed >> 10 == 70000
    with pytest.raises(Exception, match="more than the display takes"):
        udisp.header(udisp.JPEG, (480, 480), 1 << 22)


def test_the_display_is_connected_and_shown_jpeg_pictures(bus):
    display = bus(FakeDisplay())
    with opened() as screen:
        assert screen.model.id == "waveshare-2.8-udisp" and display.claimed == {0}
        screen.show(corner_red((480, 480)))
        shown = display.frames[-1]
        assert shown["kind"] == udisp.JPEG and shown["size"] == (480, 480)
        assert near(shown["picture"].getpixel((5, 5)), RED)
        assert near(shown["picture"].getpixel((475, 475)), BLUE)
        screen.set_brightness(0)  # (the brightness goes into the picture)
        screen.show(corner_red((480, 480)))
        assert near(display.frames[-1]["picture"].getpixel((5, 5)), (0, 0, 0), by=8)
        assert [f["id"] for f in display.frames] == [1, 2]
    assert not taken(display) and display.claimed == set()


def test_the_display_is_driven_as_a_panel_in_video_mode(bus):
    """Every frame at a steady 30 a second (values glide, graphs scroll), fewer where the
    display says so."""
    bus(FakeDisplay())
    screen = opened()
    assert not screen.streaming  # (not open yet)
    with screen:
        assert screen.streaming and screen.stream_fps == 30
    bus(FakeDisplay("t113udisp1_R480x480_Ejpg5_Fps20"))
    with opened() as screen:
        assert screen.stream_fps == 20


def test_raw_pixels_where_the_display_takes_no_jpeg(bus):
    display = bus(FakeDisplay("udisp0_R480x480_Ergb16"))
    with opened() as screen:
        screen.show(corner_red((480, 480)))
    shown = display.frames[-1]
    assert shown["kind"] == udisp.RGB565 and len(shown["payload"]) == 480 * 480 * 2
    assert struct.unpack_from("<H", shown["payload"])[0] == udisp.rgb565_value(RED)
    display = bus(FakeDisplay("udisp0_R480x480_Ergb32"))
    with opened() as screen:
        screen.show(corner_red((480, 480)))
    shown = display.frames[-1]
    assert shown["kind"] == udisp.RGB888 and shown["payload"][:3] == bytes(RED)


def test_a_display_of_another_size_is_not_driven_as_this_one(bus):
    display = bus(FakeDisplay("t113udisp1_R800x800_Ejpg5"))
    with pytest.raises(DeviceError, match="800×800"):
        opened().open()
    assert display.frames == [] and not taken(display)


def test_a_display_gone_is_a_fault_the_loop_comes_back_from(bus):
    display = bus(FakeDisplay())
    with opened() as screen:
        display.broken = True
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(corner_red((480, 480)))
        assert screen.counters.usb_errors == 1


def test_auto_finds_the_display_by_its_product_string(bus, monkeypatch):
    def no_usb_panel(pid=None, beside=(), skip=0):
        raise DeviceError("no Turing/TURZX USB panel found")

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    for family in (lianli_usb, thermalright, beadapanel, nzxt_kraken, thermaltake):
        monkeypatch.setattr(family, "present_model", lambda: None)
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    bus(FakeDisplay())
    assert udisp.present_model() == get_model("waveshare-2.8-udisp")
    with opened("auto") as screen:
        assert screen.model.id == "waveshare-2.8-udisp"
    bus(FakeDisplay("t113udisp1_R800x800_Ejpg5"))  # a size not known yet: not taken for it
    assert udisp.present_model() is None


def test_the_model():
    model = get_model("waveshare-2.8-udisp")
    assert models_for_usb(0x303A, 0x2986) == [model]
    assert model.size("landscape") == (480, 480) and model.shape == "round"
    assert model.driver == "unverified" and "udisp" in model.limits

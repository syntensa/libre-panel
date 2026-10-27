"""udisp USB displays (devices/udisp.py): Waveshare's USB monitors as USB displays (#21). Each
picture is one bulk transfer: a header of 16 bytes and the picture, as the kind the display
names first (JPEG on Waveshare's 2.8", whose card draws nothing else: #30)."""

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
            frame["jpeg"] = Image.open(io.BytesIO(payload))
            frame["picture"] = frame["jpeg"].convert("RGB")
        self.frames.append(frame)


@pytest.fixture
def bus(monkeypatch):
    def plugged(display):
        plug(monkeypatch, udisp.VENDOR_ID, (display, 0x2986))
        return display

    monkeypatch.setattr(udisp, "AGAIN_S", 30)
    return plugged


def opened(model="waveshare-2.8-udisp", fps=None):
    config = DeviceConfig(driver="turzx", model=model)
    if fps:
        config.video.fps = fps
    return TurzxDisplay(config)


def pixel(shown, x, y):
    """The 16 bits of a raw picture's pixel."""
    return struct.unpack_from("<H", shown["payload"], 2 * (y * shown["size"][0] + x))[0]


def test_what_the_product_string_says():
    said = udisp.described(PRODUCT)
    assert said == {"size": (480, 480), "kind": udisp.JPEG, "jpeg": 5, "rgb16": True,
                    "rgb32": True, "fps": None, "limit": None}  # fmt: skip
    said = udisp.described("esp32udisp0_R320x240_Ejpg4_Fps30_Bl20_Ergb16")
    assert said["size"] == (320, 240) and said["fps"] == 30 and said["limit"] == 20 * 1024
    assert said["kind"] == udisp.JPEG  # (the kind named first)
    assert udisp.described("udisp0_R480x480_Ergb16_Ejpg5")["kind"] == udisp.RGB565
    assert udisp.described("udisp0_R480x480_Fps30")["kind"] is None
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


def test_the_display_is_connected_and_sent_jpeg_as_it_names_first(bus):
    """Derik's 2.8" names JPEG first and raw pixels after it, but the program on its card that
    draws the pictures decodes JPEG only: raw pixels left it black (#30). It is sent what the
    protocol's drivers send it: baseline JPEG, its colours at every pixel (4:4:4)."""
    display = bus(FakeDisplay())
    with opened() as screen:
        assert screen.model.id == "waveshare-2.8-udisp" and display.claimed == {0}
        assert screen.picture == "JPEG"
        screen.show(corner_red((480, 480)))
        shown = display.frames[-1]
        assert shown["kind"] == udisp.JPEG and shown["size"] == (480, 480)
        assert shown["jpeg"].size == (480, 480) and not shown["jpeg"].info.get("progressive")
        assert [(h, v) for _id, h, v, _table in shown["jpeg"].layer] == [(1, 1)] * 3  # 4:4:4
        assert near(shown["picture"].getpixel((5, 5)), RED)
        assert near(shown["picture"].getpixel((475, 475)), BLUE)
        assert screen.counters.bytes == 16 + len(shown["payload"])
        screen.set_brightness(0)  # (the brightness goes into the picture)
        screen.show(corner_red((480, 480)))
        assert all(most <= 2 for _least, most in display.frames[-1]["picture"].getextrema())
        assert [f["id"] for f in display.frames] == [1, 2]
        assert {f["kind"] for f in display.frames} == {udisp.JPEG}
    assert not taken(display) and display.claimed == set()


def test_raw_pixels_for_a_display_that_names_them_first(bus):
    """Every pixel as it was drawn, 16 bits each, where the display asks for that first."""
    display = bus(FakeDisplay("udisp0_R480x480_Ergb16_Ejpg5"))
    with opened() as screen:
        assert screen.picture == "RGB565"
        screen.show(corner_red((480, 480)))
    shown = display.frames[-1]
    assert shown["kind"] == udisp.RGB565 and len(shown["payload"]) == 480 * 480 * 2
    assert pixel(shown, 5, 5) == udisp.rgb565_value(RED)
    assert pixel(shown, 475, 475) == udisp.rgb565_value(BLUE)


def test_the_display_is_driven_as_a_panel_in_video_mode(bus):
    """Every frame at a steady pace (values glide, graphs scroll): the frames a second of a
    TURZX panel in video mode, 30 unless set otherwise, fewer where the display says so."""
    bus(FakeDisplay())
    screen = opened()
    assert not screen.streaming  # (not open yet)
    with screen:
        assert screen.streaming and screen.stream_fps == 30
    bus(FakeDisplay())
    with opened(fps=50) as screen:
        assert screen.stream_fps == 50
    bus(FakeDisplay("t113udisp1_R480x480_Ejpg5_Ergb16_Fps20"))
    with opened(fps=50) as screen:
        assert screen.stream_fps == 20


class Timed(FakeDisplay):
    """A display that takes so long for each kind of picture (seconds), on a clock of its own."""

    def __init__(self, costs, product=PRODUCT) -> None:
        super().__init__(product)
        self.costs, self.now = costs, 0.0

    def clock(self):
        return self.now

    def received(self, endpoint, data):
        super().received(endpoint, data)
        self.now += self.costs[self.frames[-1]["kind"]]


def driven(display, frames, fps=None):
    with opened(fps=fps) as screen:
        screen._impl.clock = display.clock
        for _ in range(frames):
            screen.show(corner_red((480, 480)))
        return screen._impl.rate, screen.stream_fps


def test_the_stream_goes_no_faster_than_the_display_takes_pictures(bus):
    """The first pictures are timed: a display that takes them as fast as wanted gets the
    frames a second wanted, one that takes fewer a tenth fewer than it took."""
    fast = bus(Timed({udisp.JPEG: 1 / 90}))
    rate, fps = driven(fast, udisp.TRIAL + 5)
    assert round(rate) == 90 and fps == 30
    slow = bus(Timed({udisp.JPEG: 1 / 20}))
    rate, fps = driven(slow, udisp.TRIAL + 5)
    assert round(rate) == 20 and fps == 18
    more = bus(Timed({udisp.JPEG: 1 / 40}))
    _rate, fps = driven(more, udisp.TRIAL + 5, fps=50)
    assert fps == 36  # (50 wanted, 40 reached: a tenth of room)


def test_nothing_but_the_kind_named_first_is_tried(bus):
    """However slowly the JPEG goes, the raw pixels Derik's 2.8" names after it are not tried:
    its card would never draw them (#30)."""
    slow_jpeg = bus(Timed({udisp.JPEG: 1 / 10, udisp.RGB565: 1 / 60, udisp.RGB888: 1 / 40}))
    _rate, fps = driven(slow_jpeg, 3 * udisp.TRIAL)
    assert {f["kind"] for f in slow_jpeg.frames} == {udisp.JPEG} and fps == 9


def test_the_kinds_of_picture_a_display_takes(bus):
    display = bus(FakeDisplay("udisp0_R480x480_Ergb32"))
    with opened() as screen:
        screen.show(corner_red((480, 480)))
    shown = display.frames[-1]
    assert shown["kind"] == udisp.RGB888 and shown["payload"][:3] == bytes(RED)
    for product in ("t113udisp1_R480x480_Ejpg5", "udisp0_R480x480_Fps30"):  # (none named: JPEG)
        display = bus(FakeDisplay(product))
        with opened() as screen:
            screen.show(corner_red((480, 480)))
        shown = display.frames[-1]
        assert shown["kind"] == udisp.JPEG and shown["size"] == (480, 480)
        assert near(shown["picture"].getpixel((5, 5)), RED)
        assert near(shown["picture"].getpixel((475, 475)), BLUE)


def test_the_doctor_names_the_kind_of_picture_sent_and_its_size(bus):
    """0.7.2's report said RGB565 and its size for every panel with a driver of its own, while
    the 2.8" was sent JPEG (#30): the speed check says what went, and how much."""
    from libre_panel.doctor import Doctor

    display = bus(FakeDisplay())

    def no_usb_panel(pid=None, beside=()):
        raise DeviceError("no Turing/TURZX USB panel found")

    def panel():
        screen = opened()
        screen.open()
        return screen

    answers = iter(["y", "", "y"])  # the test card (a round one), Enter, the brightness
    report = Doctor(ask=lambda question: next(answers), say=lambda text: None,
                    pause=lambda seconds: None, frames=3, open_transport=no_usb_panel,
                    open_serial=panel).run()  # fmt: skip
    text = report.text()
    assert report.passed, text
    speed = next(line for line in text.splitlines() if "per frame" in line)
    sent = [16 + len(f["payload"]) for f in display.frames[-4:-1]]  # (before the last card)
    assert speed.endswith(f"JPEG {sum(sent) // len(sent) // 1024} KB"), speed
    assert {f["kind"] for f in display.frames} == {udisp.JPEG}


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

"""Thermalright's Trofeo Vision (devices/thermalright.py): the 6.86" HID screen and the 9.16" and
11.3" bars, each asked what it is, then shown JPEG pictures in the packets it takes."""

import io
import time

import pytest
from fake_usb import FakeUsbDevice, Interface, plug, taken
from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices import lianli_usb, serial_link, thermalright, turzx_usb
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import get_model, models_for_usb
from libre_panel.devices.turzx import TurzxDisplay

RED, BLUE = (230, 20, 20), (20, 20, 230)


def corner_red(size):
    frame = Image.new("RGB", size, BLUE)
    frame.paste(RED, (0, 0, size[0] // 4, size[1] // 4))
    return frame


def near(a, b, by=40):
    return all(abs(x - y) <= by for x, y in zip(a, b, strict=True))


class FakeBar(FakeUsbDevice):
    """A bar (0416:5408): answers its question with its kind (byte 20) and variant (byte 22),
    puts the JPEG of a picture's packets together, answers 03 FF .. 01 once all are in."""

    def __init__(self, kind=2, variant=1, address=7) -> None:
        super().__init__([Interface(0, (0x01, 0x81))], address=address)
        self.kind, self.variant = kind, variant
        self.pictures: list[Image.Image] = []
        self.packets: list[bytes] = []
        self.quiet = False  # takes pictures without a word

    def received(self, endpoint, data):
        assert endpoint == 0x01
        if data[0:2] == b"\x02\xff":  # the question
            assert len(data) == thermalright.HELLO and data[8] == 1 and not any(data[9:])
            answer = bytearray(512)
            answer[0:2], answer[8], answer[20], answer[22] = b"\x03\xff", 1, self.kind, self.variant
            self.answer(0x81, bytes(answer))
            return
        assert len(data) <= 4096 and len(data) % 512 == 0
        self.packets += [data[at : at + 512] for at in range(0, len(data), 512)]
        count = int.from_bytes(self.packets[0][9:11], "little")
        if len(self.packets) < count + (-count % 4):
            return
        jpeg = bytearray()
        size = int.from_bytes(self.packets[0][2:6], "little")
        for number, packet in enumerate(self.packets[:count]):
            assert packet[0:2] == b"\x01\xff" and packet[8] == 1
            assert int.from_bytes(packet[2:6], "little") == size
            assert int.from_bytes(packet[9:11], "little") == count
            assert int.from_bytes(packet[11:13], "little") == number
            jpeg += packet[16 : 16 + int.from_bytes(packet[6:8], "little")]
        assert all(packet == bytes(512) for packet in self.packets[count:])  # the fillers
        assert len(jpeg) == size
        self.pictures.append(Image.open(io.BytesIO(bytes(jpeg))).convert("RGB"))
        self.packets = []
        if not self.quiet:
            answer = bytearray(512)
            answer[0:2], answer[8] = b"\x03\xff", 1
            self.answer(0x81, bytes(answer))


class FakeHidScreen(FakeUsbDevice):
    """The 6.86" (0416:5302): its HID interface (1) beside a vendor one; answers its question
    with its kind (byte 5) and variant (byte 4), puts a picture's reports together."""

    def __init__(self, kind=0x80, variant=0) -> None:
        super().__init__([Interface(0, (0x02, 0x82)), Interface(1, (0x03, 0x84), kind=3)])
        self.kind, self.variant = kind, variant
        self.pictures: list[Image.Image] = []
        self.headers: list[bytes] = []
        self._jpeg = bytearray()
        self._left = 0

    def received(self, endpoint, data):
        assert endpoint == 0x03 and len(data) == 512
        if self._left:
            self._jpeg += data[: self._left]
            self._left -= len(data[: self._left])
        elif data[:4] == thermalright.MAGIC and data[12] == 1:  # the question
            answer = bytearray(512)
            answer[0:4], answer[4], answer[5], answer[12] = data[:4], self.variant, self.kind, 1
            self.answer(0x84, bytes(answer))
            return
        else:
            assert data[:4] == thermalright.MAGIC and data[4] == 2 and data[12] == 2
            self.headers.append(data[:20])
            size = int.from_bytes(data[16:20], "little")
            self._jpeg = bytearray(data[20 : 20 + size])
            self._left = size - len(self._jpeg)
        if not self._left:
            self.pictures.append(Image.open(io.BytesIO(bytes(self._jpeg))).convert("RGB"))


@pytest.fixture
def bus(monkeypatch):
    def plugged(*devices):
        plug(monkeypatch, thermalright.VENDOR_ID, *devices)
        return devices[0][0]

    monkeypatch.setattr(thermalright, "AGAIN_S", {0x5302: 30, 0x5408: 30})
    return plugged


def opened(model="auto"):
    return TurzxDisplay(DeviceConfig(driver="turzx", model=model))


def test_a_bar_says_what_it_is_and_is_shown_jpeg_packets(bus):
    bar = bus((FakeBar(kind=2, variant=1), 0x5408))  # variant 1: as it is (usausa's 2)
    with opened("thermalright-trofeo-9.16") as screen:
        assert screen.model.id == "thermalright-trofeo-9.16" and bar.claimed == {0}
        screen.show(corner_red((1920, 462)))
        shown = bar.pictures[-1]
        assert shown.size == (1920, 462)  # what TRCC sends, not the 1920x480 the bar says
        assert near(shown.getpixel((5, 5)), RED) and near(shown.getpixel((1900, 450)), BLUE)
        writes = bar.to(0x01)[1:]
        assert all(len(w) == 4096 for w in writes[:-1]) and sum(map(len, writes)) % 2048 == 0
        screen.set_brightness(0)  # no backlight command: the picture goes again, dark
        assert bar.pictures[-1].getpixel((5, 5)) == (0, 0, 0)
    assert not taken(bar) and bar.claimed == set()


def test_a_bar_of_another_variant_is_shown_its_pictures_turned_half_round(bus):
    bar = bus((FakeBar(kind=0, variant=0), 0x5408))
    with opened() as screen:  # (found by auto: no TURZX panel on the bus)
        assert screen.model.id == "thermalright-trofeo-9.16" and screen._impl.turn == 180
        screen.show(corner_red((1920, 462)))
        shown = bar.pictures[-1]
        assert near(shown.getpixel((1915, 457)), RED) and near(shown.getpixel((5, 5)), BLUE)


def test_the_bar_that_says_five_is_the_eleven_inch_one(bus):
    bar = bus((FakeBar(kind=5, variant=3), 0x5408))
    with opened("thermalright-trofeo-9.16") as screen:
        assert screen.model.id == "thermalright-trofeo-11.3"
        screen.show(corner_red((1920, 440)))
        assert bar.pictures[-1].size == (1920, 440)


def test_the_six_inch_screen_takes_its_jpeg_in_hid_reports(bus):
    hid = FakeHidScreen(kind=0x80, variant=0)
    bus((hid, 0x5302))
    with opened("thermalright-trofeo-6.86") as screen:
        assert screen.model.id == "thermalright-trofeo-6.86" and hid.claimed == {1}
        screen.show(corner_red((1280, 480)))
        assert hid.pictures[-1].size == (1280, 480) and near(hid.pictures[-1].getpixel((5, 5)), RED)
        header = hid.headers[-1]
        assert int.from_bytes(header[8:10], "little") == 1280
        assert int.from_bytes(header[10:12], "little") == 480


def test_a_six_inch_screen_of_variant_two_is_shown_its_pictures_on_end(bus):
    hid = FakeHidScreen(kind=0x80, variant=2)
    bus((hid, 0x5302))
    with opened("thermalright-trofeo-6.86") as screen:
        screen.show(corner_red((1280, 480)))
        shown = hid.pictures[-1]  # a quarter clockwise: the top left corner is top right
        assert shown.size == (480, 1280) and near(shown.getpixel((475, 5)), RED)


def test_a_thermalright_screen_of_another_kind_is_not_driven(bus):
    hid = FakeHidScreen(kind=0x3A)  # (a Frozen Warframe: other pictures)
    bus((hid, 0x5302))
    with pytest.raises(DeviceError, match="does not know yet"):
        opened("thermalright-trofeo-6.86").open()
    assert len(hid.sent) == 1 and not taken(hid) and hid.claimed == set()  # the question alone


def test_a_screen_that_does_not_answer_is_not_driven(bus):
    bar = bus((FakeBar(), 0x5408))
    bar.answer = lambda endpoint, data: None  # (it takes, and says nothing)
    with pytest.raises(DeviceError, match="does not say what it is"):
        opened("thermalright-trofeo-9.16").open()
    assert not taken(bar)


def test_the_last_picture_goes_again_while_nothing_changes(bus, monkeypatch):
    monkeypatch.setattr(thermalright, "AGAIN_S", {0x5302: 30, 0x5408: 0.05})
    bar = bus((FakeBar(), 0x5408))
    with opened("thermalright-trofeo-9.16") as screen:
        screen.show(corner_red((1920, 462)))
        deadline = time.monotonic() + 2
        while len(bar.pictures) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(bar.pictures) >= 3  # (it shows its logo again after a few seconds)


def test_a_bar_that_stops_answering_is_a_fault_the_loop_comes_back_from(bus):
    bar = bus((FakeBar(), 0x5408))
    with opened("thermalright-trofeo-9.16") as screen:
        bar.quiet = True
        for _ in range(thermalright.MISSED - 1):
            screen.show(corner_red((1920, 462)))  # (an answer that does not come is no fault)
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(corner_red((1920, 462)))
        assert screen.counters.usb_errors == 1
        bar.quiet, bar.broken = False, True
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(corner_red((1920, 462)))


def test_two_bars_are_two_panels(bus):
    first, second = FakeBar(address=7), FakeBar(kind=5, address=8)
    bus((first, 0x5408), (second, 0x5408))
    with opened("thermalright-trofeo-9.16") as one, opened("thermalright-trofeo-11.3") as two:
        assert one.model.id == "thermalright-trofeo-9.16" and taken(first)
        assert two.model.id == "thermalright-trofeo-11.3" and taken(second)


def test_auto_finds_a_trofeo_after_the_turzx_panels_and_lian_li(bus, monkeypatch):
    def no_usb_panel(pid=None, beside=(), skip=0):
        raise DeviceError("no Turing/TURZX USB panel found")

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    monkeypatch.setattr(lianli_usb, "present_model", lambda: None)
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    bus((FakeHidScreen(), 0x5302))
    assert thermalright.present_model().id == "thermalright-trofeo-6.86"
    with opened() as screen:
        assert screen.model.id == "thermalright-trofeo-6.86"


def test_auto_looks_on_past_a_thermalright_screen_it_does_not_drive(bus, monkeypatch):
    """0416:5302 is the id of many of Thermalright's screens: one of another kind does not keep
    auto from the panels after it."""
    from libre_panel.devices import jonsbo_zc360

    def no_usb_panel(pid=None, beside=(), skip=0):
        raise DeviceError("no Turing/TURZX USB panel found")

    looked = []
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    monkeypatch.setattr(lianli_usb, "present_model", lambda: None)
    monkeypatch.setattr(jonsbo_zc360, "present", lambda: looked.append("zc360") or False)
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    hid = FakeHidScreen(kind=0x3A)
    bus((hid, 0x5302))
    with pytest.raises(DeviceError, match="does not know yet"):  # (when there is no other)
        opened().open()
    assert looked == ["zc360"] and not taken(hid)


def test_the_packets_of_a_picture():
    exact = bytes(range(256)) * 31  # 7936 bytes: 16 packets of 496, no filler
    writes = thermalright.bulk_writes(exact)
    assert [len(w) for w in writes] == [4096, 4096]
    one = thermalright.bulk_writes(b"\xff\xd8\xff\xd9")
    assert len(one) == 1 and len(one[0]) == 2048  # one packet and three empty ones
    assert one[0][6:8] == (4).to_bytes(2, "little") and one[0][512:] == bytes(1536)
    reports = thermalright.hid_reports(bytes(1100), 1280, 480)
    assert len(reports) == 3 and all(len(r) == 512 for r in reports)  # 20 + 1100 in 512s
    assert thermalright.bulk_answer(bytes(512)) is None
    assert thermalright.turn("thermalright-trofeo-9.16", 4) == 0


def test_the_models():
    assert {m.id for m in models_for_usb(0x0416, 0x5408)} == {
        "thermalright-trofeo-9.16",
        "thermalright-trofeo-11.3",
    }
    assert [m.id for m in models_for_usb(0x0416, 0x5302)] == ["thermalright-trofeo-6.86"]
    assert get_model("thermalright-trofeo-9.16").size("landscape") == (1920, 462)
    assert get_model("thermalright-trofeo-6.86").driver == "unverified"

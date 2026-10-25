"""Thermaltake's 6" LCD (devices/thermaltake.py): text commands framed in HID reports of 1024
bytes, then JPEG pictures in pieces of 1000 bytes."""

import io
import time

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
)
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


class FakeLcd(FakeUsbDevice):
    """The LCD: answers each framed command with a "200" (or not), puts a picture's pieces
    together."""

    def __init__(self, accepts=True) -> None:
        super().__init__([Interface(0, (0x01, 0x81), kind=3)])
        self.accepts = accepts
        self.commands: list[tuple[str, dict[str, str], str]] = []
        self.pictures: list[Image.Image] = []
        self._pieces: list[bytes] = []

    def received(self, endpoint, data):
        assert endpoint == 0x01 and len(data) == 1024
        if data[:2] == b"\x5a\x00":
            length = data[2]
            text = data[3 : length - 2]
            assert data[length - 2] == (length + sum(text)) & 0xFF  # the checksum
            assert data[length - 1 : length + 1] == b"\x5a\x00" and not any(data[length + 1 :])
            head, _, body = text.decode("ascii").partition("\r\n\r\n")
            name, *lines = head.split("\r\n")
            fields = dict(line.split("=", 1) for line in lines)
            if body:
                assert fields["ContentType"] == "json" and int(fields["ContentLength"]) == len(body)
            self.commands.append((name, fields, body))
            answer = b"POST 200 OK\r\n" if self.accepts else b"POST 400\r\n"
            self.answer(0x81, answer.ljust(1024, b"\0"))
            return
        assert data[:4] == b"\x5c\x03\xfd\x00" and data[8] == 1 and not any(data[9:24])
        count, number = int.from_bytes(data[4:6], "big"), int.from_bytes(data[6:8], "big")
        assert number == len(self._pieces)
        self._pieces.append(data[24:])
        if number == count - 1:
            jpeg = b"".join(self._pieces)  # (the last piece filled up with zeros)
            self.pictures.append(Image.open(io.BytesIO(jpeg)).convert("RGB"))
            self._pieces = []


@pytest.fixture
def bus(monkeypatch):
    def plugged(lcd):
        plug(monkeypatch, thermaltake.VENDOR_ID, (lcd, 0x2347))
        return lcd

    monkeypatch.setattr(thermaltake.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(thermaltake, "AGAIN_S", 30)
    return plugged


def opened(model="thermaltake-lcd-6"):
    return TurzxDisplay(DeviceConfig(driver="turzx", model=model))


def test_the_lcd_is_connected_and_shown_jpeg_pictures_in_pieces(bus):
    lcd = bus(FakeLcd())
    with opened() as screen:
        assert screen.model.id == "thermaltake-lcd-6" and lcd.claimed == {0}
        assert [(name, body) for name, _, body in lcd.commands] == [
            ("POST conn 1", ""),
            ("POST realtimeDisplay 1", '{"enable":true}'),
        ]
        numbers = [int(fields["SeqNumber"]) for _, fields, _ in lcd.commands]
        assert numbers == [100, 101] and all("Date" in fields for _, fields, _ in lcd.commands)
        screen.show(corner_red((1480, 720)))
        shown = lcd.pictures[-1]
        assert shown.size == (1480, 720)
        assert near(shown.getpixel((5, 5)), RED) and near(shown.getpixel((1470, 710)), BLUE)
        screen.set_brightness(0)  # (the brightness goes into the picture)
        assert lcd.pictures[-1].getpixel((5, 5)) == (0, 0, 0)
    assert lcd.commands[-1][0] == "POST realtimeDisplay 1"
    assert lcd.commands[-1][2] == '{"enable":false}'  # the screen back to the panel
    assert not taken(lcd) and lcd.claimed == set()


def test_a_portrait_frame_is_turned_into_the_lying_picture(bus):
    lcd = bus(FakeLcd())
    with opened() as screen:
        screen.show(corner_red((720, 1480)))
        shown = lcd.pictures[-1]  # a quarter clockwise
        assert shown.size == (1480, 720) and near(shown.getpixel((1475, 5)), RED)


def test_an_lcd_that_does_not_take_the_commands_is_not_driven(bus):
    lcd = bus(FakeLcd(accepts=False))
    with pytest.raises(DeviceError, match="did not start"):
        opened().open()
    assert lcd.pictures == [] and not taken(lcd)


def test_the_last_picture_goes_again_while_nothing_changes(bus, monkeypatch):
    monkeypatch.setattr(thermaltake, "AGAIN_S", 0.05)
    lcd = bus(FakeLcd())
    with opened() as screen:
        screen.show(corner_red((1480, 720)))
        deadline = time.monotonic() + 2
        while len(lcd.pictures) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(lcd.pictures) >= 3


def test_an_lcd_gone_is_a_fault_the_loop_comes_back_from(bus):
    lcd = bus(FakeLcd())
    with opened() as screen:
        lcd.broken = True
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(corner_red((1480, 720)))
        assert screen.counters.usb_errors == 1


def test_auto_finds_the_lcd_after_the_others(bus, monkeypatch):
    def no_usb_panel(pid=None, beside=(), skip=0):
        raise DeviceError("no Turing/TURZX USB panel found")

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    for family in (lianli_usb, thermalright, beadapanel, nzxt_kraken):
        monkeypatch.setattr(family, "present_model", lambda: None)
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    bus(FakeLcd())
    with opened("auto") as screen:
        assert screen.model.id == "thermaltake-lcd-6"


def test_the_frame_of_a_command():
    text = "POST conn 1\r\nSeqNumber=112\r\nContentType=json\r\nContentLength=240\r\n\r\n"
    packet = thermaltake.framed(text)
    assert packet[:3] == bytes((0x5A, 0, len(text) + 5)) and packet[-2:] == b"\x5a\x00"
    assert packet[-3] == 0x07  # the checksum TT RGB Plus sent with this text (InfoPanel's notes)
    one = thermaltake.command("POST brightness 1", 7, '{"value":50}', now=1.5)
    assert b"SeqNumber=7\r\nDate=1500\r\nContentType=json\r\nContentLength=12\r\n\r\n{" in one
    pieces = thermaltake.pieces(bytes(2500))
    assert [p[4:8] for p in pieces] == [
        b"\x00\x03\x00\x00",
        b"\x00\x03\x00\x01",
        b"\x00\x03\x00\x02",
    ]
    assert all(len(p) == 1024 for p in pieces)


def test_the_model():
    assert models_for_usb(0x264A, 0x2347) == [get_model("thermaltake-lcd-6")]
    assert get_model("thermaltake-lcd-6").size("landscape") == (1480, 720)

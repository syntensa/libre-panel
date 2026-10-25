"""The JONSBO DS916 (a USB serial port that takes JPEG pictures) and DS339 (a MacroSilicon
MS9132 USB display chip), on simulated devices."""

import io
import threading
import time

import pytest
import usb.core
import usb.util
from fake_serial import FakeSerial, port_info
from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices import base, ms9132, serial_link, turzx_usb
from libre_panel.devices import jonsbo_ds916 as ds916
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import get_model, models_for_usb
from libre_panel.devices.turzx import TurzxDisplay
from libre_panel.theme.defaults import default_theme

RED, BLUE = (230, 20, 20), (20, 20, 230)


def landscape(size):
    """A frame with its top left quarter red and the rest blue."""
    frame = Image.new("RGB", size, BLUE)
    frame.paste(RED, (0, 0, size[0] // 2, size[1] // 2))
    return frame


def near(a, b, by=40):
    return all(abs(x - y) <= by for x, y in zip(a, b, strict=True))


# -- DS916 ------------------------------------------------------------------------------------


class FakeDs916(FakeSerial):
    """A DS916: it says what it is to F0 A5 5A 0F; anything else is a JPEG picture."""

    def __init__(self, identity=b"VMAXB160462*1920S00000000") -> None:
        super().__init__()
        self.identity = identity
        self.pictures: list[bytes] = []
        self.broken = False
        self.lock = threading.Lock()

    def write(self, data):
        self._check()
        if self.broken:
            import serial

            raise serial.SerialException("gone")
        data = bytes(data)
        with self.lock:
            if data == ds916.HELLO:
                self.answers += self.identity
            else:
                assert data[:2] == b"\xff\xd8"  # a JPEG and nothing else
                self.pictures.append(data)
        return len(data)

    def last(self) -> Image.Image:
        with self.lock:
            return Image.open(io.BytesIO(self.pictures[-1])).convert("RGB")


@pytest.fixture
def ds916_port(monkeypatch):
    panel = FakeDs916()
    port = port_info("COM9", vid=0x33C3, pid=0xF101)

    def open_serial(device, baud, rtscts):
        assert device == "COM9" and not rtscts
        return panel

    monkeypatch.setattr(serial_link, "_ports", lambda: [port])
    monkeypatch.setattr(serial_link, "open_serial", open_serial)
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    monkeypatch.setattr(ds916, "AGAIN_S", 0.05)
    return panel


def no_usb_panel(pid=None, beside=()):
    raise DeviceError("no Turing/TURZX USB panel found")


def test_a_ds916_is_found_by_auto_and_shown_a_jpeg_standing_on_end(ds916_port):
    with TurzxDisplay(DeviceConfig(driver="turzx", model="auto")) as screen:
        assert screen.model.id == "jonsbo-ds916" and not screen.slow
        assert screen._impl.native == (462, 1920)  # as the panel said
        screen.show(landscape((1920, 462)))
        shown = ds916_port.last()
        assert shown.size == (462, 1920)
        # a quarter turn clockwise: the frame's top left corner is at the top right
        assert near(shown.getpixel((450, 10)), RED) and near(shown.getpixel((10, 10)), BLUE)
        screen.set_brightness(50)  # no command for it: the last picture again, darker
        assert ds916_port.last().getpixel((450, 10))[0] < 140
    assert ds916_port.closed


def test_the_last_picture_goes_again_while_nothing_changes(ds916_port):
    with TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-ds916")) as screen:
        screen.show(landscape((1920, 462)))
        deadline = time.monotonic() + 30
        while len(ds916_port.pictures) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(set(ds916_port.pictures)) == 1 and len(ds916_port.pictures) >= 3
        ds916_port.broken = True  # unplugged: the next frame says so, and the loop reconnects
        deadline = time.monotonic() + 30
        with pytest.raises(DeviceError):
            while time.monotonic() < deadline:
                screen.show(landscape((1920, 462)))
                time.sleep(0.02)
        assert screen.counters.usb_errors >= 1
    assert not base.place_taken("COM9")


def test_a_ds916_that_does_not_say_what_it_is_is_not_used(ds916_port):
    ds916_port.identity = b""
    with pytest.raises(DeviceError, match="does not say what it is"):
        TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-ds916")).open()
    assert not base.place_taken("COM9")


def test_a_big_picture_is_made_small_enough(monkeypatch):
    noise = Image.effect_noise((462, 1920), 100).convert("RGB")
    monkeypatch.setattr(ds916, "MAX_JPEG", 120 * 1024)
    assert len(ds916.jpeg(noise)) <= 120 * 1024 or ds916.QUALITIES[-1] == 30


# -- DS339 ------------------------------------------------------------------------------------


def edid(width=376, height=960) -> bytes:
    block = bytearray(128)
    block[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    block[0x38], block[0x3A] = width & 0xFF, (width >> 8) << 4
    block[0x3B], block[0x3D] = height & 0xFF, (height >> 8) << 4
    return bytes(block)


class FakeMs9132:
    """The chip: feature reports on interface 0 (each read back), frames on endpoint 0x04."""

    def __init__(self, edid_block=None) -> None:
        self.bus, self.address = 1, 9
        self.edid = edid_block if edid_block is not None else edid()
        self.reports: list[bytes] = []
        self.data = bytearray()
        self.frames: list[bytes] = []
        self.claimed: set[int] = set()
        self.broken = False
        self._reply = bytes(8)

    def is_kernel_driver_active(self, interface):
        return False

    def get_active_configuration(self):
        return type("Configuration", (), {"bConfigurationValue": 1})()

    def set_configuration(self, value):
        raise AssertionError("it is configured already")

    def ctrl_transfer(self, request_type, request, value, index, data_or_length, timeout):
        assert value == 0x0300 and index == ms9132.CONTROL
        if request_type == 0x21:  # SET_REPORT
            report = bytes(data_or_length)
            assert len(report) == 8
            self.reports.append(report)
            reply = bytearray(report)
            if report[:2] == b"\xb5\xc0":
                reply[3:7] = self.edid[report[2] : report[2] + 4]
            self._reply = bytes(reply)
            return 8
        assert request_type == 0xA1 and data_or_length == 8
        return self._reply

    def write(self, endpoint, data, timeout):
        assert endpoint == ms9132.ENDPOINT_OUT and len(data) <= ms9132.CHUNK
        if self.broken:
            raise usb.core.USBError("gone")
        self.data += bytes(data)
        width, height = self.size()
        if len(self.data) == 8 + width * height * 3 + 8:  # a whole frame
            assert self.data.endswith(ms9132.TRAILER)
            assert int.from_bytes(self.data[6:8], "big") == height | 0x8000
            self.frames.append(bytes(self.data))
            self.data.clear()
        return len(data)

    def size(self) -> tuple[int, int]:
        return ms9132.edid_size(self.edid) or (376, 960)

    def picture(self) -> Image.Image:
        blue, green, red = Image.frombytes("RGB", self.size(), self.frames[-1][8:-8]).split()
        return Image.merge("RGB", (red, green, blue))

    def sent(self, *prefix: int) -> list[bytes]:
        return [r for r in self.reports if r[: len(prefix)] == bytes(prefix)]


@pytest.fixture
def chip(monkeypatch):
    fake = FakeMs9132()
    monkeypatch.setattr(ms9132, "_device", lambda: fake)
    monkeypatch.setattr(ms9132.time, "sleep", lambda seconds: None)
    for name in ("claim_interface", "release_interface", "dispose_resources"):
        monkeypatch.setattr(usb.util, name, lambda device, *args: device.claimed.add(*args[:1]))
    monkeypatch.setattr(ms9132, "AGAIN_S", 30)
    return fake


def test_the_ds339_is_started_as_the_vendor_does_and_shown_bgr_frames(chip):
    with TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-ds339")) as screen:
        impl = screen._impl
        assert isinstance(impl, ms9132.Ms9132Display) and impl.size == (376, 960)
        assert chip.reports[: len(ms9132.START)] == list(ms9132.START)  # the vendor's start
        assert chip.sent(0xA6, 0x02)[0][2] == 171  # the mode of 376x960
        assert chip.sent(0xA6, 0x04) and not chip.sent(0xA6, 0x05, 0x01)  # output still off
        screen.show(landscape((960, 376)))
        assert len(chip.frames) == 2 and chip.sent(0xA6, 0x05, 0x01)  # two frames, then on
        header = chip.frames[0][:8]
        assert header == bytes((0xFF, 0, 0, 0, 0, 376 // 16, 0x83, 0xC0))  # 960 | 0x8000
        assert len(chip.frames[0]) == 8 + 376 * 960 * 3 + 8
        shown = chip.picture()
        assert near(shown.getpixel((370, 5)), RED) and near(shown.getpixel((5, 5)), BLUE)
        screen.show(landscape((960, 376)))
        assert len(chip.frames) == 3 and len(chip.sent(0xA6, 0x05, 0x01)) == 1
        screen.set_brightness(0)
        assert chip.picture().getpixel((370, 5)) == (0, 0, 0)
        assert screen.screen_off() and chip.reports[-1] == ms9132.POWER_OFF
    assert not base.place_taken("usb:1.9")


def test_the_ds339_takes_its_size_from_its_edid(chip):
    chip.edid = edid(360, 960)
    with TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-ds339")) as screen:
        assert screen._impl.size == (360, 960) and chip.sent(0xA6, 0x02)[0][2] == 160
        screen.show(landscape((960, 376)))
        assert chip.frames[-1][5] == 360 // 16
    chip.edid = bytes(128)  # none: as the model says
    with TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-ds339")) as screen:
        assert screen._impl.size == (376, 960)


def test_a_ds339_gone_is_a_fault_the_loop_comes_back_from(chip):
    with TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-ds339")) as screen:
        chip.broken = True
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(landscape((960, 376)))
        assert screen.counters.usb_errors == 1


def test_the_ds339_is_never_found_by_auto(chip, monkeypatch):
    """USB-to-HDMI adapters have the chip too: only a config that names the DS339 opens it."""
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    with pytest.raises(DeviceError):
        TurzxDisplay(DeviceConfig(driver="turzx", model="auto")).open()
    assert chip.reports == []


def test_the_models_of_both():
    nine, three = get_model("jonsbo-ds916"), get_model("jonsbo-ds339")
    assert nine.size("landscape") == (1920, 462) and default_theme(nine) == "lumen"
    assert three.size("landscape") == (960, 376) and default_theme(three) == "libre-default"
    assert models_for_usb(0x33C3, 0xF101) == [nine] and three in models_for_usb(0x345F, 0x9132)
    assert nine.driver == three.driver == "unverified"
    assert ms9132.edid_size(edid(376, 960)) == (376, 960) and ms9132.edid_size(bytes(128)) is None

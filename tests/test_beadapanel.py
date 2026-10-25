"""BeadaPanel (devices/beadapanel.py): asked over Status-Link what it is, reset, its backlight
set, then a Panel-Link stream of RGB565 pictures of the size it says."""

import time

import numpy as np
import pytest
from fake_usb import FakeUsbDevice, Interface, plug, taken
from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices import beadapanel, lianli_usb, serial_link, turzx_usb
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import get_model, models_for_usb
from libre_panel.devices.turzx import TurzxDisplay

RED, BLUE = (230, 20, 20), (20, 20, 230)


def corner_red(size):
    frame = Image.new("RGB", size, BLUE)
    frame.paste(RED, (0, 0, size[0] // 4, size[1] // 4))
    return frame


def near(a, b, by=12):
    return all(abs(x - y) <= by for x, y in zip(a, b, strict=True))


class FakeBeada(FakeUsbDevice):
    """A BeadaPanel: Status-Link on 0x02 and 0x82 (its information, the reset, the backlight),
    Panel-Link on 0x01 (a tag, then pictures of the size it says, as RGB565)."""

    def __init__(self, code=15, size=(1280, 480), panel_link=2, top=100, address=7) -> None:
        super().__init__([Interface(0, (0x01, 0x02, 0x82))], address=address)
        self.code, self.size, self.panel_link, self.top = code, size, panel_link, top
        self.status: list[int] = []
        self.backlight: list[int] = []
        self.tags: list[tuple[int, str]] = []
        self.pictures: list[Image.Image] = []
        self._data = bytearray()

    def info(self) -> bytes:
        payload = bytearray(80)
        payload[0:2] = (712).to_bytes(2, "little")
        payload[2], payload[3], payload[4], payload[5] = self.panel_link, 1, 2, self.code
        payload[6:16] = b"0123456789"  # (a serial number)
        payload[70:74] = self.size[0].to_bytes(2, "little") + self.size[1].to_bytes(2, "little")
        payload[78], payload[79] = self.top, self.top
        head = b"STATUS-LINK" + bytes((1, 1, 0)) + (0).to_bytes(2, "little")
        head += (100).to_bytes(2, "little")
        return head + beadapanel.checksum(head).to_bytes(2, "little") + bytes(payload)

    def received(self, endpoint, data):
        if endpoint == 0x02:
            assert data[:11] == b"STATUS-LINK" and data[11] == 1 and data[13:16] == bytes(3)
            assert int.from_bytes(data[16:18], "little") == len(data)
            assert int.from_bytes(data[18:20], "little") == beadapanel.checksum(data[:18])
            self.status.append(data[12])
            if data[12] == beadapanel.GET_INFO:
                self.answer(0x82, self.info())
            elif data[12] == beadapanel.SET_BACKLIGHT:
                assert len(data) == 21
                self.backlight.append(data[20])
            else:
                assert data[12] == beadapanel.RESET_LINK and len(data) == 20
            return
        assert endpoint == 0x01
        if data[:10] == b"PANEL-LINK":
            assert len(data) == 270 and data[10] == 1
            assert int.from_bytes(data[268:270], "little") == beadapanel.checksum(data[:268])
            self.tags.append((data[11], data[12:268].rstrip(b"\0").decode("ascii")))
            return
        assert self.tags and beadapanel.RESET_LINK in self.status  # (the pipe is armed)
        self._data += data
        width, height = self.size
        if len(self._data) == width * height * 2:
            value = np.frombuffer(bytes(self._data), "<u2").reshape(height, width)
            rgb = np.dstack(((value >> 11) << 3, ((value >> 5) & 0x3F) << 2, (value & 0x1F) << 3))
            self.pictures.append(Image.fromarray(rgb.astype(np.uint8), "RGB"))
            self._data.clear()


@pytest.fixture
def bus(monkeypatch):
    def plugged(*devices):
        plug(monkeypatch, beadapanel.VENDOR_ID, *((device, 0x1001) for device in devices))
        return devices[0]

    monkeypatch.setattr(beadapanel.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(beadapanel, "AGAIN_S", 30)
    return plugged


def opened(model="auto", brightness=60):
    return TurzxDisplay(DeviceConfig(driver="turzx", model=model, brightness=brightness))


def test_a_beadapanel_says_what_it_is_and_takes_a_stream_of_rgb565_pictures(bus):
    panel = bus(FakeBeada(code=15, size=(1280, 480)))  # a 6C
    with opened("beadapanel-6") as screen:
        assert screen.model.id == "beadapanel-6" and panel.claimed == {0}
        assert panel.status == [1, 2, 3] and panel.backlight == [60]  # asked, reset, backlight
        assert panel.tags == [
            (5, "video/x-raw, format=RGB16, width=1280, height=480, framerate=0/1")
        ]
        screen.show(corner_red((1280, 480)))
        shown = panel.pictures[-1]
        assert shown.size == (1280, 480)
        assert near(shown.getpixel((5, 5)), RED) and near(shown.getpixel((1270, 470)), BLUE)
        screen.set_brightness(35)
        assert panel.backlight[-1] == 35
    assert panel.tags[-1][0] == 6 and not taken(panel) and panel.claimed == set()  # its end


def test_a_panel_of_panel_link_one_gets_its_own_tags_and_the_backlight_its_scale(bus):
    panel = bus(FakeBeada(code=0, size=(800, 480), panel_link=1, top=200))  # a 5
    with opened("beadapanel-5", brightness=50) as screen:
        assert screen.model.id == "beadapanel-5"
        assert panel.tags[0][0] == 1 and panel.backlight == [100]  # half of its highest
    assert panel.tags[-1][0] == 2


def test_the_panel_says_which_it_is_and_a_portrait_frame_is_turned_into_its_stream(bus):
    panel = bus(FakeBeada(code=21, size=(1920, 480)))  # an 8 (8.8")
    with opened() as screen:  # (auto: no TURZX panel on the bus)
        assert screen.model.id == "beadapanel-8"
        screen.show(corner_red((480, 1920)))
        shown = panel.pictures[-1]  # a quarter clockwise, as the TURZX bars
        assert shown.size == (1920, 480) and near(shown.getpixel((1915, 5)), RED)


def test_an_unknown_code_is_known_by_its_size_else_not_driven(bus):
    panel = bus(FakeBeada(code=99, size=(800, 480)))
    with opened() as screen:
        assert screen.model.id == "beadapanel-4"  # the first of 800x480
    panel.code, panel.size = 99, (1024, 600)
    with pytest.raises(DeviceError, match="does not know yet"):
        opened().open()
    assert not taken(panel) and panel.tags[-1][0] == 6  # (the first one's end only)


def test_a_panel_that_does_not_answer_is_not_driven(bus):
    panel = bus(FakeBeada())
    panel.answer = lambda endpoint, data: None
    with pytest.raises(DeviceError, match="does not say what it is"):
        opened("beadapanel-6").open()
    assert panel.tags == [] and not taken(panel)


def test_dark_on_quit_turns_the_backlight_off(bus):
    panel = bus(FakeBeada())
    with opened("beadapanel-6") as screen:
        assert screen.screen_off() and panel.backlight[-1] == 0


def test_the_last_picture_goes_again_while_nothing_changes(bus, monkeypatch):
    monkeypatch.setattr(beadapanel, "AGAIN_S", 0.05)
    panel = bus(FakeBeada(code=17, size=(480, 480)))  # a 2, round
    with opened("beadapanel-2") as screen:
        screen.show(corner_red((480, 480)))
        deadline = time.monotonic() + 2
        while len(panel.pictures) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(panel.pictures) >= 3


def test_a_panel_gone_is_a_fault_the_loop_comes_back_from(bus):
    panel = bus(FakeBeada())
    with opened("beadapanel-6") as screen:
        panel.broken = True
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(corner_red((1280, 480)))
        assert screen.counters.usb_errors == 1


def test_auto_finds_a_beadapanel_after_the_others(bus, monkeypatch):
    def no_usb_panel(pid=None, beside=(), skip=0):
        raise DeviceError("no Turing/TURZX USB panel found")

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    monkeypatch.setattr(lianli_usb, "present_model", lambda: None)
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    bus(FakeBeada(code=19, size=(1280, 400)))  # a 7S
    assert beadapanel.present_model() is not None  # (which one, it says when it is opened)
    with opened() as screen:
        assert screen.model.id == "beadapanel-7s"


def test_the_messages():
    assert beadapanel.checksum(b"\xff\xff\x01\x00") == 0xFFFE  # (0xffff + 1, the carry folded)
    tag = beadapanel.panel_tag(beadapanel.START, "x")
    assert len(tag) == 270 and tag[:12] == b"PANEL-LINK\x01\x05" and tag[12:13] == b"x"
    assert beadapanel.status_message(3, b"\x2a")[16:18] == (21).to_bytes(2, "little")
    pixel = Image.new("RGB", (1, 1), (255, 0, 0))
    assert beadapanel.rgb565(pixel) == (0xF800).to_bytes(2, "little")  # red in the high bits


def test_the_models():
    models = models_for_usb(0x4E58, 0x1001)
    assert len(models) == len(beadapanel.models()) and models[0].id == "beadapanel-2"
    assert {code: model for code, (_name, model) in beadapanel.CODES.items()}[26] == "beadapanel-9"
    assert get_model("beadapanel-2w").shape == "round"
    assert get_model("beadapanel-6").size("landscape") == (1280, 480)

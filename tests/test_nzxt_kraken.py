"""NZXT Krakens with an LCD (devices/nzxt_kraken.py): the screen's reports over the HID interface,
the pictures over bulk into two buckets of the cooler's memory in turn (or, on a Kraken 2023
with firmware 2, straight)."""

import os

import numpy as np
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


def near(a, b, by=12):
    return all(abs(x - y) <= by for x, y in zip(a, b, strict=True))


class FakeKraken(FakeUsbDevice):
    """A Kraken: reports of 64 bytes (on endpoint 0x01, or through a fake hidraw node), each
    answered with its id + 1 after a status report of its own; pictures on bulk 0x02, a header
    of 20 bytes by itself first, into an armed bucket (or straight, on firmware 2)."""

    def __init__(self, size=320, firmware=(1, 5, 11), turn=0) -> None:
        super().__init__([Interface(0, (0x02,)), Interface(1, (0x01, 0x81), kind=3)])
        self.size, self.firmware, self.turn = size, firmware, turn
        self.reports: list[bytes] = []
        self.brightness: list[tuple[int, int]] = []
        self.buckets: dict[int, Image.Image] = {}
        self.setups: list[tuple[int, int, int]] = []  # (bucket, start, size), KiB
        self.shown: int | str | None = "liquid"
        self.direct: list[Image.Image] = []
        self.armed: int | str | None = None
        self.writes: list[bytes] = []
        self.inbox: list[bytes] = []  # what the HID interface answers

    def received(self, endpoint, data):
        if endpoint == 0x01:
            self.report(data)
        else:
            assert endpoint == 0x02 and self.armed is not None, "no bucket armed"
            self.writes.append(data)

    def reply(self, report, taken=1):
        self.inbox.append(bytes((0x75, 0x01)) + bytes(62))  # its own status, in between
        answer = bytearray(64)
        answer[0], answer[1], answer[14] = report[0] + 1, report[1], taken
        return answer

    def report(self, report):
        assert len(report) == 64
        report = bytes(report)
        self.reports.append(report)
        command = report[:2]
        assert report[0] in (0x10, 0x30, 0x32, 0x36, 0x38)  # (the screen's: never the pump's)
        if command == b"\x10\x01":
            answer = self.reply(report)
            answer[17:20] = bytes(self.firmware)
        elif command == b"\x30\x01":
            answer = self.reply(report)
            answer[20:24] = self.size.to_bytes(2, "little") * 2
            answer[24], answer[26] = 50, self.turn
        elif command == b"\x30\x02":
            assert report[2] == 1 and report[4:7] == b"\x00\x00\x01" and report[7] == self.turn
            self.brightness.append((report[3], report[7]))
            return
        elif command == b"\x38\x01":
            assert report[2] in (2, 4)
            if report[2] == 4:
                assert report[3] in self.buckets
            self.shown = report[3] if report[2] == 4 else "liquid"
            answer = self.reply(report)
        elif command == b"\x32\x02":
            refused = report[2] == self.shown
            if not refused:
                self.buckets.pop(report[2], None)
            answer = self.reply(report, 0x09 if refused else 1)
        elif command == b"\x32\x01":
            bucket = report[2]
            assert report[3] == bucket + 1 and report[8] == 1
            assert bucket not in self.buckets and bucket != self.shown  # (deleted first)
            start, size = report[4] | report[5] << 8, report[6] | report[7] << 8
            self.setups.append((bucket, start, size))
            answer = self.reply(report)
        elif command == b"\x36\x03":
            answer = self.reply(report)
        elif command == b"\x36\x01":
            self.armed = "direct" if report[2:5] == b"\x00\x01\x06" else report[2]
            self.writes = []
            answer = self.reply(report)
        else:
            assert command == b"\x36\x02" and self.armed is not None
            self.ended()
            answer = self.reply(report)
        self.inbox.append(bytes(answer))

    def ended(self):
        header, *chunks = self.writes
        assert len(header) == 20 and header[:12] == nzxt_kraken.MAGIC  # a transfer by itself
        kind, length = header[12], int.from_bytes(header[16:20], "little")
        data = b"".join(chunks)
        assert len(data) == length and header[13:16] == bytes(3)
        if self.armed == "direct":
            assert kind == 0x06
            value = np.frombuffer(data, ">u2").reshape(self.size, self.size)
            rgb = np.dstack(((value >> 11) << 3, ((value >> 5) & 0x3F) << 2, (value & 0x1F) << 3))
            self.direct.append(Image.fromarray(rgb.astype(np.uint8), "RGB"))
        else:
            assert kind == 0x02
            pixels = np.frombuffer(data, np.uint8).reshape(self.size, self.size, 4)
            assert not pixels[..., 3].any()  # R G B 0
            self.buckets[self.armed] = Image.fromarray(pixels[..., :3].copy(), "RGB")
        self.armed = None

    def read(self, endpoint, size, timeout):  # the HID interface's answers
        assert endpoint == 0x81
        if self.inbox:
            return self.inbox.pop(0)
        return super().read(endpoint, size, timeout)

    def picture(self) -> Image.Image:
        return self.buckets[self.shown]


class FakeHidraw:
    """The Kraken's hidraw node, as the driver uses it on Linux."""

    def __init__(self, kraken: FakeKraken) -> None:
        self.kraken, self.closed = kraken, False

    def write(self, report, timeout_ms=0):
        self.kraken.report(bytes(report).ljust(64, b"\0"))

    def read(self, timeout_ms=1000):
        return self.kraken.inbox.pop(0) if self.kraken.inbox else None

    def close(self):
        self.closed = True


@pytest.fixture
def bus(monkeypatch):
    nodes = []

    def plugged(kraken, product=0x3008):
        def node(path):
            assert path == "/dev/hidraw9"
            nodes.append(FakeHidraw(kraken))
            return nodes[-1]

        plug(monkeypatch, nzxt_kraken.VENDOR_ID, (kraken, product))
        monkeypatch.setattr(nzxt_kraken, "hidraw_node", lambda device: "/dev/hidraw9")
        monkeypatch.setattr(nzxt_kraken, "Hidraw", node)
        kraken.nodes = nodes
        return kraken

    monkeypatch.setattr(nzxt_kraken, "_linux", lambda: True)
    monkeypatch.setattr(nzxt_kraken.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(nzxt_kraken, "ANSWER_S", 0.2)
    return plugged


def opened(model="auto", brightness=60):
    return TurzxDisplay(DeviceConfig(driver="turzx", model=model, brightness=brightness))


def test_a_kraken_z_is_shown_its_pictures_through_two_buckets_in_turn(bus):
    kraken = bus(FakeKraken())
    with opened("nzxt-kraken-z") as screen:
        assert screen.model.id == "nzxt-kraken-z"
        assert kraken.claimed == {0}  # the bulk interface; the HID one stays with the kernel
        assert kraken.brightness == [(60, 0)] and kraken.shown == "liquid"
        screen.show(corner_red((320, 320)))
        assert kraken.shown == 0 and kraken.setups == [(0, 0, 401)]  # 20 + 320 x 320 x 4, KiB
        assert all(len(chunk) <= 512 for chunk in kraken.writes)  # (the Z's packets)
        shown = kraken.picture()
        assert near(shown.getpixel((5, 5)), RED) and near(shown.getpixel((315, 315)), BLUE)
        screen.show(corner_red((320, 320)))
        assert kraken.shown == 1 and kraken.setups[-1] == (1, 24320 // 2, 401)
        screen.show(corner_red((320, 320)))
        assert kraken.shown == 0  # (bucket 0 deleted again: it was not on screen)
        screen.set_brightness(40)
        assert kraken.brightness[-1] == (40, 0)  # the turn as it was
    assert kraken.shown == "liquid" and kraken.nodes[-1].closed and not taken(kraken)


def test_the_turn_the_cooler_is_set_to_goes_into_the_pixels(bus):
    kraken = bus(FakeKraken(turn=1))
    with opened("nzxt-kraken-z") as screen:
        screen.show(corner_red((320, 320)))
        assert near(kraken.picture().getpixel((315, 5)), RED)  # a quarter clockwise


def test_a_kraken_2023_with_firmware_2_takes_its_pictures_straight(bus):
    kraken = bus(FakeKraken(size=240, firmware=(2, 1, 0)), product=0x300E)
    with opened() as screen:  # (auto: no TURZX panel on the bus)
        assert screen.model.id == "nzxt-kraken-2023"
        screen.show(corner_red((240, 240)))
        assert len(kraken.direct) == 2 and kraken.setups == []  # the first one twice
        assert near(kraken.direct[-1].getpixel((5, 5)), RED)
        screen.show(corner_red((240, 240)))
        assert len(kraken.direct) == 3 and len(kraken.writes[1]) == 240 * 240 * 2  # one write


def test_a_kraken_2023_with_firmware_1_and_the_2024_ones_use_the_buckets(bus):
    kraken = bus(FakeKraken(size=640, firmware=(1, 2, 0)), product=0x3012)
    with opened("nzxt-kraken-elite-2024") as screen:
        screen.show(corner_red((640, 640)))
        assert kraken.shown == 0 and kraken.picture().size == (640, 640)
        assert len(kraken.writes) == 2  # the header, and 2 MB at a time


def test_on_windows_the_reports_go_over_pyusb(bus, monkeypatch):
    kraken = bus(FakeKraken())
    monkeypatch.setattr(nzxt_kraken, "_linux", lambda: False)
    with opened("nzxt-kraken-z") as screen:
        assert kraken.claimed == {0, 1} and kraken.nodes == []
        screen.show(corner_red((320, 320)))
        assert kraken.shown == 0 and kraken.to(0x01)  # the reports on its interrupt endpoint


def test_a_kraken_that_does_not_answer_is_not_driven(bus):
    kraken = bus(FakeKraken())
    kraken.reply = lambda report, taken=1: bytearray(64)  # (answers nothing it was asked)
    with pytest.raises(DeviceError, match="stopped answering"):
        opened("nzxt-kraken-z").open()
    assert not taken(kraken) and kraken.claimed == set()


def test_a_kraken_gone_is_a_fault_the_loop_comes_back_from(bus):
    kraken = bus(FakeKraken())
    with opened("nzxt-kraken-z") as screen:
        kraken.broken = True
        with pytest.raises(DeviceError, match="stopped answering"):
            screen.show(corner_red((320, 320)))
        assert screen.counters.usb_errors == 1


def test_dark_on_quit_turns_the_brightness_down(bus):
    kraken = bus(FakeKraken())
    with opened("nzxt-kraken-z") as screen:
        assert screen.screen_off() and kraken.brightness[-1] == (0, 0)


def test_auto_finds_a_kraken_after_the_others(bus, monkeypatch):
    def no_usb_panel(pid=None, beside=(), skip=0):
        raise DeviceError("no Turing/TURZX USB panel found")

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    for family in (lianli_usb, thermalright, beadapanel):
        monkeypatch.setattr(family, "present_model", lambda: None)
    monkeypatch.setattr(serial_link, "_ports", lambda: [])
    bus(FakeKraken(size=240), product=0x3014)
    with opened() as screen:
        assert screen.model.id == "nzxt-kraken-plus-2024"


@pytest.mark.skipif(
    os.name == "nt", reason="Windows cannot make a folder named 3-4:1.0, as the kernel does"
)
def test_the_hidraw_node_is_found_in_sysfs(tmp_path):
    device = type("Device", (), {"bus": 3, "address": 12})()
    usb = tmp_path / "sys/bus/usb/devices/3-4"
    for name, text in (("busnum", "3"), ("devnum", "12")):
        (usb / name).parent.mkdir(parents=True, exist_ok=True)
        (usb / name).write_text(text + "\n")
    for interface, kind in (("3-4:1.0", "ff"), ("3-4:1.1", "03")):
        (usb / interface).mkdir()
        (usb / interface / "bInterfaceClass").write_text(kind + "\n")
    (usb / "3-4:1.1/0003:1E71:3008.0007/hidraw/hidraw5").mkdir(parents=True)
    assert nzxt_kraken.hidraw_node(device, str(tmp_path)) == str(tmp_path / "dev/hidraw5")
    other = type("Device", (), {"bus": 3, "address": 13})()
    assert nzxt_kraken.hidraw_node(other, str(tmp_path)) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO stands in for the node")
def test_the_hidraw_node_takes_and_gives_reports_of_64_bytes(tmp_path):
    fifo = tmp_path / "hidraw0"
    os.mkfifo(fifo)
    node = nzxt_kraken.Hidraw(str(fifo))
    assert node.read(10) is None
    node.write(b"\x30\x01")  # (a FIFO gives back what it was given)
    assert node.read(100) == b"\x30\x01" + bytes(62)
    node.close()
    node.close()
    with pytest.raises(DeviceError):
        nzxt_kraken.Hidraw(str(tmp_path / "gone"))


def test_the_models():
    assert [m.id for m in models_for_usb(0x1E71, 0x3008)] == ["nzxt-kraken-z"]
    assert models_for_usb(0x1E71, 0x300C) == []  # the 2023 Elite: liquidctl calls its LCD broken
    assert {m.shape for m in nzxt_kraken.models()} == {"round"}
    assert get_model("nzxt-kraken-elite-2024").size("landscape") == (640, 640)
    assert nzxt_kraken.header(2, 409600)[12:] == bytes((2, 0, 0, 0)) + (409600).to_bytes(
        4, "little"
    )

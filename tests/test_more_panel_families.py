"""Panel families whose protocols InfoPanel documents: the Hongtai / Jungle Leopard screens
(a USB serial port, JPEG pictures) and Lian Li's TURZX-like USB screens; and the models that
our drivers serve as they are (Turing 1.6" and 2.1" USB, the VMAX 4.6" on the MS9132)."""

import io
import json
import struct
import threading
import time

import pytest
import usb.core
from fake_serial import FakeSerial, port_info
from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices import base, hongtai, lianli_usb, serial_link, turzx_usb
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import get_model, models_for_usb
from libre_panel.devices.turzx import TurzxDisplay
from libre_panel.devices.turzx_usb import UsbTransport, build_packet, decode_packet

RED, BLUE = (230, 20, 20), (20, 20, 230)


def corner_red(size):
    frame = Image.new("RGB", size, BLUE)
    frame.paste(RED, (0, 0, size[0] // 2, size[1] // 2))
    return frame


def near(a, b, by=40):
    return all(abs(x - y) <= by for x, y in zip(a, b, strict=True))


def no_usb_panel(pid=None, beside=()):
    raise DeviceError("no Turing/TURZX USB panel found")


# -- Hongtai ----------------------------------------------------------------------------------


class FakeHongtai(FakeSerial):
    """Answers 06 with its JSON; takes 11 + a JPEG envelope + FF D9 FF D9 as a picture."""

    def __init__(self, width=480, height=960) -> None:
        super().__init__()
        self.info = {"status": 200, "model": "TXW818", "width": width, "height": height}
        self.commands: list[int] = []
        self.pictures: list[Image.Image] = []
        self.brightness: list[int] = []
        self.lock = threading.Lock()

    def write(self, data):
        self._check()
        data = bytes(data)
        with self.lock:
            while data:
                data = self._take(data)
        return 0

    def _take(self, data: bytes) -> bytes:
        if data.startswith(hongtai.RESET):
            return data[len(hongtai.RESET) :]
        if data[:2] == b"\x55\xaa":
            length = struct.unpack("<H", data[2:4])[0]
            body, chk = data[: length - 2], data[length - 2 : length]
            assert struct.unpack("<H", chk)[0] == sum(body) & 0xFFFF
            command = data[4]
            assert command not in hongtai.NEVER
            self.commands.append(command)
            if command == hongtai.CMD_INFO:
                answer = json.dumps(self.info).encode()
                head = b"\x55\xaa" + struct.pack("<H", len(answer) + 7) + bytes((6,)) + answer
                self.answers += head + struct.pack("<H", sum(head) & 0xFFFF)
            if command == hongtai.CMD_LIGHT:
                self.brightness.append(data[5])
            return data[length:]
        size = struct.unpack("<I", data[:4])[0]
        jpeg = data[4 : 4 + size]
        assert struct.unpack("<H", data[4 + size : 6 + size])[0] == sum(data[: 4 + size]) & 0xFFFF
        assert data[6 + size : 10 + size] == hongtai.END
        self.pictures.append(Image.open(io.BytesIO(jpeg)).convert("RGB"))
        return data[10 + size :]


@pytest.fixture
def hongtai_port(monkeypatch):
    panels = {}

    def plug(fake, device="COM5"):
        panels[device] = fake
        return fake

    def open_serial(device, baud, rtscts):
        assert baud == 2_000_000 and not rtscts
        return panels[device]

    monkeypatch.setattr(
        serial_link, "_ports", lambda: [port_info(d, vid=0x33C3, pid=0x7788) for d in panels]
    )
    monkeypatch.setattr(serial_link, "open_serial", open_serial)
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    monkeypatch.setattr(lianli_usb, "present_model", lambda: None)
    monkeypatch.setattr(hongtai.time, "sleep", lambda seconds: None)
    return plug


def test_a_hongtai_screen_says_its_size_and_takes_jpeg_pictures(hongtai_port):
    panel = hongtai_port(FakeHongtai())
    with TurzxDisplay(DeviceConfig(driver="turzx", model="auto")) as screen:
        assert screen.model.id == "hongtai-480x960" and not screen.slow
        screen.set_brightness(40)
        screen.show(corner_red((960, 480)))
        shown = panel.pictures[-1]
        assert shown.size == (480, 960)  # turned into the panel's way round
        assert near(shown.getpixel((470, 10)), RED) and near(shown.getpixel((10, 10)), BLUE)
        assert panel.commands[-1] == hongtai.CMD_LIVE and panel.brightness == [40]
    assert panel.commands[-1] == hongtai.CMD_CLOSE and panel.closed
    assert not base.place_taken("COM5")


def test_the_strip_is_told_apart_by_its_size(hongtai_port):
    panel = hongtai_port(FakeHongtai(1920, 462))
    with TurzxDisplay(DeviceConfig(driver="turzx", model="auto")) as screen:
        assert screen.model.id == "hongtai-1920x462"
        screen.show(corner_red((1920, 462)))
        assert panel.pictures[-1].size == (1920, 462)  # (it lies as the frame does)


def test_the_live_picture_is_kept_while_nothing_changes(hongtai_port, monkeypatch):
    monkeypatch.setattr(hongtai, "LIVE_EVERY_S", 0.05)
    panel = hongtai_port(FakeHongtai())
    with TurzxDisplay(DeviceConfig(driver="turzx", model="hongtai-480x960")) as screen:
        screen.show(corner_red((960, 480)))
        before = panel.commands.count(hongtai.CMD_LIVE)
        deadline = time.monotonic() + 2
        while panel.commands.count(hongtai.CMD_LIVE) < before + 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert panel.commands.count(hongtai.CMD_LIVE) >= before + 2
        assert len(panel.pictures) == 1  # 11 alone, no picture again


def test_a_hongtai_command_that_is_never_sent_cannot_be_built():
    for command in hongtai.NEVER:
        with pytest.raises(ValueError):
            hongtai.frame(command)
    # the sum of every byte before it (InfoPanel's code; its notes' example says 0x111)
    assert hongtai.frame(0x11) == bytes((0x55, 0xAA, 7, 0, 0x11, 0x17, 0x01))


# -- Lian Li ----------------------------------------------------------------------------------


class FakeLianLiTransport:
    """The TURZX transport as Lian Li's screen answers it: pictures say how many wait."""

    def __init__(self, waiting=(0,)) -> None:
        self.asked: list[tuple[int, bytes]] = []
        self.pushed: list[tuple[int, bytes]] = []
        self.levels = list(waiting)
        self.closed = False

    def drain(self):
        return 0

    def ask(self, cmd, args=b"", timeout_ms=2000, echo=True):
        self.asked.append((cmd, bytes(args)))
        return bytes(512)

    def push(self, cmd, args, payload, timeout_ms=5000, reply_ms=200):
        assert struct.unpack(">I", args)[0] == len(payload)
        self.pushed.append((cmd, payload))
        reply = bytearray(512)
        reply[8] = self.levels.pop(0) if self.levels else 0
        return bytes(reply)

    def query(self, cmd, timeout_ms=400):
        assert cmd == lianli_usb.CMD_LEVEL
        self.asked.append((cmd, b""))
        reply = bytearray(512)
        reply[8] = self.levels.pop(0) if self.levels else 0
        return bytes(reply)

    def close(self):
        self.closed = True


@pytest.fixture
def lianli(monkeypatch):
    transport = FakeLianLiTransport()
    opened = []

    def open_(pid=None, beside=()):
        opened.append(pid)
        return transport

    monkeypatch.setattr(UsbTransport, "open", staticmethod(open_))
    transport.opened = opened
    return transport


def test_a_lian_li_screen_is_set_up_and_shown_jpeg_on_its_background_layer(lianli):
    with TurzxDisplay(DeviceConfig(driver="turzx", model="lianli-universal-8.8")) as screen:
        assert lianli.opened == [0xA088]
        commands = [cmd for cmd, _ in lianli.asked]
        assert commands[:3] == [10, 123, 52] and commands[-1] == 15
        assert lianli.pushed[0][0] == lianli_usb.CMD_PNG  # the layer over it, cleared
        clear = Image.open(io.BytesIO(lianli.pushed[0][1]))
        assert clear.size == (480, 1920) and clear.getextrema()[3] == (0, 0)
        screen.set_brightness(55)
        assert lianli.asked[-1] == (14, bytes((55,)))
        screen.show(corner_red((1920, 480)))
        command, data = lianli.pushed[-1]
        shown = Image.open(io.BytesIO(data)).convert("RGB")
        assert command == lianli_usb.CMD_JPEG and shown.size == (480, 1920)
        assert near(shown.getpixel((470, 10)), RED) and near(shown.getpixel((10, 10)), BLUE)
    assert lianli.closed


def test_a_full_lian_li_screen_is_waited_for(lianli):
    lianli.levels = [0, 5, 4, 3, 1]  # (the cleared layer, then a picture with 5 waiting)
    with TurzxDisplay(DeviceConfig(driver="turzx", model="lianli-hydroshift2-lcd")) as screen:
        screen.show(corner_red((480, 480)))
        assert [cmd for cmd, _ in lianli.asked].count(lianli_usb.CMD_LEVEL) == 3


def test_the_oled_curve_lies_as_its_framebuffer_does(lianli):
    with TurzxDisplay(DeviceConfig(driver="turzx", model="lianli-hydroshift2-oled")) as screen:
        screen.show(corner_red((2288, 1080)))
        shown = Image.open(io.BytesIO(lianli.pushed[-1][1])).convert("RGB")
        assert shown.size == (2288, 1080) and near(shown.getpixel((10, 10)), RED)


def test_a_lian_li_screen_is_found_by_auto(lianli, monkeypatch):
    monkeypatch.setattr(
        UsbTransport, "present", staticmethod(lambda pid: pid == 0xA092)
    )  # (no TURZX panel: UsbTransport.open, faked, would take any)
    monkeypatch.setattr(turzx_usb, "_pick", lambda *args, **kwargs: None)
    real_open = UsbTransport.open

    def only_lianli(pid=None, beside=()):
        if pid is None:
            raise DeviceError("no Turing/TURZX USB panel found")
        return real_open(pid, beside)

    monkeypatch.setattr(UsbTransport, "open", staticmethod(only_lianli))
    with TurzxDisplay(DeviceConfig(driver="turzx", model="auto")) as screen:
        assert screen.model.id == "lianli-universal-9.2"


def test_the_transport_pushes_a_command_and_its_picture_in_one_write():
    class Endpoint:
        def __init__(self, answer=None):
            self.written, self.answer = [], answer

        def write(self, data, timeout):
            self.written.append(bytes(data))

        def read(self, size, timeout):
            if self.answer is None:
                raise usb.core.USBTimeoutError("no answer")
            return self.answer

    out, quiet = Endpoint(), Endpoint()
    transport = UsbTransport(None, out, quiet, 0xA088)
    assert transport.push(101, struct.pack(">I", 3), b"abc") is None  # no answer is no fault
    assert len(out.written) == 1 and out.written[0][512:] == b"abc"
    assert decode_packet(out.written[0][:512])[0] == 101
    answering = UsbTransport(None, out, Endpoint(build_packet(101)), 0xA088)
    assert answering.push(101, b"", b"") is not None


# -- models for the drivers as they are ---------------------------------------------------------


def test_the_models_our_drivers_serve_as_they_are():
    assert models_for_usb(0x1CBE, 0x0016) == [get_model("turing-1.6-usb")]
    assert models_for_usb(0x1CBE, 0x0021) == [get_model("turing-2.1-usb")]
    assert get_model("turing-2.1-usb").shape == "round"
    assert turzx_usb.USB_PIDS[0x0016].id == "turing-1.6-usb"
    assert 0xA088 not in turzx_usb.USB_PIDS  # Lian Li's are not opened as TURZX panels
    vmax = get_model("vmax-4.6")
    assert vmax.protocol == "usb-ms9132" and vmax.size("landscape") == (960, 320)
    assert {m.id for m in models_for_usb(0x33C3, 0x7788)} == {"hongtai-480x960", "hongtai-1920x462"}


def test_each_panel_written_from_notes_says_what_it_cannot_do():
    """Settings → Panel says it under the panel's name: nobody has run them yet, and what each
    does otherwise than a TURZX USB panel."""
    from libre_panel.devices.models import MODELS
    from libre_panel.editor.settings import _model_info

    new = (
        "usb-jonsbo",
        "serial-jonsbo",
        "usb-ms9132",
        "serial-hongtai",
        "usb-lianli",
        "usb-thermalright",
        "usb-beada",
        "usb-nzxt",
        "usb-thermaltake",
    )
    for model in MODELS:
        if model.protocol in new or model.id in ("turing-1.6-usb", "turing-2.1-usb"):
            assert model.limits.startswith("Not yet seen on the hardware."), model.id
    for model_id in ("jonsbo-ds916", "hongtai-480x960", "lianli-universal-8.8", "vmax-4.6"):
        assert "no smooth motion" in get_model(model_id).limits.lower()
    for model_id in ("jonsbo-ds339", "vmax-4.6"):
        limits = get_model(model_id).limits
        assert "choose it by hand" in limits and "Windows" in limits
    assert "L-Connect" in get_model("lianli-hydroshift2-oled").limits
    assert get_model("turing-9.2-usb").limits == ""  # (a panel that runs says nothing)
    assert _model_info(get_model("jonsbo-ds916"))["limits"] == get_model("jonsbo-ds916").limits
    assert _model_info(get_model("turing-9.2-usb"))["limits"] == ""

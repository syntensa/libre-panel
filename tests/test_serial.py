"""Serial panels against simulated devices (tests/fake_serial.py)."""

import threading
import time

import pytest
from fake_serial import FakeRevA, port_info, quantized
from PIL import Image, ImageChops, ImageDraw

from libre_panel import app
from libre_panel.config import Config, DeviceConfig, SensorsConfig, parse_config
from libre_panel.devices import serial_link, turzx_usb
from libre_panel.devices.base import DeviceError
from libre_panel.devices.turing_rev_a import NEVER, brightness_level, header
from libre_panel.devices.turzx import TurzxDisplay


@pytest.fixture
def ports(monkeypatch):
    """Plug simulated panels into simulated serial ports; no USB panel is there."""
    attached = {}

    def attach(fake, **info):
        port = port_info(**info)
        attached[port.device] = (port, fake)
        return fake

    def open_serial(device, baud, rtscts):
        assert baud == 115200 and rtscts, "rev. A runs at 115200 baud with RTS/CTS"
        if device not in attached:
            import serial

            raise serial.SerialException(f"could not open port {device}")
        return attached[device][1]

    def no_usb_panel(pid=None):
        raise DeviceError("no Turing/TURZX USB panel found")

    monkeypatch.setattr(serial_link, "_ports", lambda: [p for p, _ in attached.values()])
    monkeypatch.setattr(serial_link, "open_serial", open_serial)
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_usb_panel))
    return attach


def picture(size, seed=0):
    image = Image.new("RGB", size, (8, 12, 20))
    draw = ImageDraw.Draw(image)
    w, h = size
    for i in range(0, w, 7):
        draw.line([(i, 0), (w - 1 - i, h - 1)], fill=((i * 3 + seed) % 256, i % 256, 200))
    draw.rectangle([w // 4, h // 4, w // 2, h // 2], fill=(250, 120 + seed % 100, 30))
    return image


def same(a, b):
    return ImageChops.difference(quantized(a), b).getbbox() is None


def display(model="turing-3.5", **device):
    return TurzxDisplay(DeviceConfig(driver="turzx", model=model, **device))


def test_header_packs_ten_bits_per_number():
    from fake_serial import unpack

    assert unpack(header(197, 479, 319, 1, 1023)) == (479, 319, 1, 1023)
    assert header(110, 153)[5] == 110


def test_rev_a_shows_a_landscape_frame_pixel_for_pixel(ports):
    panel = ports(FakeRevA(), serial_number="USB35INCHIPSV2")
    with display() as screen:
        screen.set_brightness(40)
        frame = picture((480, 320))
        screen.show(frame)
        assert screen.describe() == screen.model.label
    assert panel.orientation == 2 and panel.size == (480, 320)  # landscape, set by the panel
    assert same(frame, panel.screen)
    assert panel.brightness == [brightness_level(40)] == [153]
    assert panel.commands[0] == 69  # asked for its model first
    assert not NEVER & set(panel.commands)  # no reset, clear, black or screen off
    assert panel.closed


def test_rev_a_sends_only_the_changed_box(ports):
    panel = ports(FakeRevA())
    with display() as screen:
        first = picture((480, 320))
        screen.show(first)
        second = first.copy()
        ImageDraw.Draw(second).rectangle([100, 50, 179, 89], fill=(0, 255, 0))
        screen.show(second, (100, 50, 180, 90))
    assert panel.rects[-1] == (100, 50, 179, 89)  # the device takes the last pixel, inclusive
    assert same(second, panel.screen)


def test_rev_a_turns_with_the_theme(ports):
    panel = ports(FakeRevA())
    with display() as screen:
        screen.show(picture((480, 320)))
        tall = picture((320, 480), seed=40)
        screen.show(tall, (0, 0, 10, 10))  # a new orientation sends the whole frame
        assert panel.orientation == 0 and panel.size == (320, 480)
        assert panel.rects[-1] == (0, 0, 319, 479)
        assert same(tall, panel.screen)


def test_auto_finds_a_turing_3_5_on_a_serial_port(ports):
    ports(FakeRevA())  # silent on HELLO: a Turing 3.5"
    with display(model="auto") as screen:
        assert screen.model.id == "turing-3.5"


@pytest.mark.parametrize(
    "answer, native, model",
    [
        (1, (320, 480), "usbpcmonitor-3.5"),
        (2, (480, 800), "usbpcmonitor-5"),
        (3, (600, 1024), "usbpcmonitor-7"),
    ],  # fmt: skip
)
def test_usbpcmonitor_panels_say_their_size(ports, answer, native, model):
    panel = ports(FakeRevA(native=native, hello=bytes([answer] * 6)))
    with display(model="auto") as screen:
        assert screen.model.id == model
        w, h = native
        frame = picture((h, w))
        screen.show(frame)
    assert same(frame, panel.screen)


def test_a_chosen_model_stays_when_the_panel_does_not_say(ports):
    ports(FakeRevA(native=(480, 800)))
    with display(model="usbpcmonitor-5") as screen:
        assert screen.model.id == "usbpcmonitor-5"


def test_a_xuanfang_is_not_taken_for_rev_a(ports):
    """Rev. A and B share the bridge chip; the XuanFang's serial number tells them apart."""
    panel = ports(FakeRevA(), serial_number="2017-2-25")
    with pytest.raises(DeviceError, match="no Turing rev. A panel"):
        display().open()
    assert panel.commands == []


def test_device_port_names_the_port(ports):
    panel = ports(FakeRevA(), device="/dev/ttyUSB7", vid=0x0403, pid=0x6001)  # ids say nothing
    with display(port="/dev/ttyUSB7") as screen:
        screen.show(picture((480, 320)))
    assert panel.rects
    with pytest.raises(DeviceError, match="set device.model"):
        display(model="auto", port="/dev/ttyUSB7").open()


def test_an_unplugged_panel_raises_so_the_loop_reconnects(ports):
    panel = ports(FakeRevA())
    screen = display()
    screen.open()
    panel.unplugged = True
    with pytest.raises(DeviceError):
        screen.show(picture((480, 320)))
    screen.close()


def test_device_port_in_the_config():
    assert parse_config({}).device.port == ""
    assert parse_config({"device": {"port": " COM5 "}}).device.port == "COM5"


def test_main_loop_drives_a_rev_a_panel(ports):
    panel = ports(FakeRevA())
    config = Config(theme="libre-default", fps=10, sensors=SensorsConfig(providers=["demo"]))
    config.device = DeviceConfig(driver="turzx", model="turing-3.5", brightness=70)
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    thread.start()
    try:
        end = time.monotonic() + 20
        while len(panel.rects) < 3 and time.monotonic() < end:
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(20)
    assert not thread.is_alive()
    assert panel.rects[0] == (0, 0, 479, 319) and len(panel.rects) >= 3
    assert panel.brightness and panel.brightness[0] == brightness_level(70)
    assert not NEVER & set(panel.commands)


def test_doctor_checks_a_rev_a_panel(ports):
    from libre_panel.doctor import Doctor

    panel = ports(FakeRevA())

    def no_usb_panel(pid=None):
        raise DeviceError("no Turing/TURZX USB panel found")

    # two test cards, the ruler (top, bottom, left, right), Enter, brightness
    answers = iter(["y", "y", "4", "2", "2", "2", "", "y"])
    doctor = Doctor(
        ask=lambda question: next(answers),
        say=lambda text: None,
        pause=lambda seconds: None,
        frames=3,
        open_transport=no_usb_panel,
    )
    report = doctor.run()
    assert report.passed, report.text()
    assert report.model.id == "turing-3.5"
    text = report.text()
    assert "find and open a serial panel" in text and "RGB565 300 KB" in text
    assert "hidden: top 2 px, bottom 0 px, left 0 px, right 0 px" in text
    assert panel.brightness == [brightness_level(p) for p in (10, 100, 60)]
    assert not NEVER & set(panel.commands)
    assert panel.closed


def test_a_detected_panel_of_another_size_gets_the_frame_fitted(ports):
    """With model = "auto" the main loop renders the theme's size; the driver fits
    it to the panel it found, as the main loop does for a chosen model."""
    panel = ports(FakeRevA(native=(480, 800), hello=bytes([2] * 6)))
    with display(model="auto") as screen:
        screen.show(Image.new("RGB", (480, 320), (200, 40, 40)), (0, 0, 10, 10))
    assert panel.size == (800, 480) and panel.rects[-1] == (0, 0, 799, 479)
    assert panel.screen.getpixel((400, 240)) == (200, 40, 40)  # red, quantized exactly

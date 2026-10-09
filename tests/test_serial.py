"""Serial panels against simulated devices (tests/fake_serial.py)."""

import threading
import time

import pytest
from fake_serial import FakeRevA, FakeRevB, FakeRevC, FakeRevD, FakeWeAct, port_info, quantized
from PIL import Image, ImageChops, ImageDraw

from libre_panel import app
from libre_panel.config import Config, DeviceConfig, SensorsConfig, parse_config
from libre_panel.devices import (
    kipye_rev_d,
    serial_link,
    turing_rev_b,
    turing_rev_c,
    turzx_usb,
    weact,
)
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import find_model
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
    assert "find and open a USB serial panel" in text and "RGB565 300 KB" in text
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


# -- rev. B (XuanFang), rev. D (Kipye), WeAct ------------------------------------

FAMILIES = {
    "xuanfang-3.5": (lambda: FakeRevB(), {"serial_number": "2017-2-25"}),
    "kipye-3.5": (lambda: FakeRevD(), {"vid": 0x454D, "pid": 0x4E41}),
    "weact-3.5": (lambda: FakeWeAct(), {"vid": 0x1A86, "pid": 0xFE0C, "serial_number": "AB0001"}),
    "weact-0.96": (
        lambda: FakeWeAct(native=(80, 160)),
        {"vid": 0x1A86, "pid": 0xFE0C, "serial_number": "AD0001"},
    ),
}
NEVER_SENT = {
    "xuanfang-3.5": turing_rev_b.NEVER,
    "kipye-3.5": kipye_rev_d.NEVER,
    "weact-3.5": weact.NEVER,
    "weact-0.96": weact.NEVER,
}


@pytest.fixture
def cooldowns(monkeypatch):
    waited = []
    monkeypatch.setattr(turing_rev_b.RevBDisplay, "sleep", staticmethod(waited.append))
    return waited


def as_panel_shows(model, image):
    """Rev. D knows only portrait: a landscape frame arrives turned."""
    if model == "kipye-3.5" and image.width > image.height:
        return image.transpose(Image.Transpose.ROTATE_270)
    return image


@pytest.mark.parametrize("model", list(FAMILIES))
def test_serial_families_show_frames_pixel_for_pixel(ports, cooldowns, model):
    make, info = FAMILIES[model]
    panel = ports(make(), **info)
    with display(model="auto") as screen:
        assert screen.model.id == model  # found by its USB ids and serial number
        w, h = screen.model.size("landscape")
        wide = picture((w, h))
        screen.show(wide)
        assert same(as_panel_shows(model, wide), panel.screen)
        changed = wide.copy()
        box = (w // 4, h // 4, w // 2, h // 2)
        ImageDraw.Draw(changed).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill="lime")
        screen.show(changed, box)
        assert len(panel.rects) == 2 and panel.rects[-1] != panel.rects[0]  # only the box
        assert same(as_panel_shows(model, changed), panel.screen)
        tall = picture((h, w), seed=60)
        screen.show(tall)
        assert same(tall, panel.screen)
    assert panel.closed
    assert not set(panel.commands) & NEVER_SENT[model]


def test_rev_b_brightness_by_sub_revision(ports, cooldowns):
    full = ports(FakeRevB(sub_revision=0x12), serial_number="2017-2-25")
    with display(model="xuanfang-3.5") as screen:
        screen.set_brightness(50)
        screen.set_brightness(100)
    assert full.brightness == [127, 255]


def test_rev_b_older_panels_only_switch_the_light(ports, cooldowns):
    old = ports(FakeRevB(sub_revision=0x01), serial_number="2017-2-25")
    with display(model="xuanfang-3.5") as screen:
        for percent in (0, 40, 100):
            screen.set_brightness(percent)
        screen.show(picture((480, 320)))
        screen.show(picture((480, 320), seed=9), (0, 0, 20, 20))
    assert old.brightness == [1, 0, 0]  # 1 = off
    assert cooldowns == [turing_rev_b.COOLDOWN_S] * 2  # a pause after every bitmap


def test_rev_b_that_does_not_answer_is_not_driven(ports, cooldowns):
    class Silent(FakeRevB):
        def parse(self):
            self.buffer.clear()

    ports(Silent(), serial_number="2017-2-25")
    with pytest.raises(DeviceError, match="did not answer"):
        display(model="xuanfang-3.5").open()


def test_rev_d_drops_the_acknowledgements_and_sends_brightness_twice(ports):
    panel = ports(FakeRevD(), vid=0x454D, pid=0x4E41)
    with display(model="kipye-3.5") as screen:
        screen.set_brightness(50)
        screen.show(picture((480, 320)))
        assert panel.answers == b""  # nothing piles up on the line
    assert panel.brightness == [250, 250]  # 0-500, twice: the panel sometimes misses one


def test_weact_brightness_and_a_chosen_size(ports):
    panel = ports(FakeWeAct(native=(80, 160)), vid=0x1A86, pid=0xFE0C, serial_number="XY")
    with display(model="weact-0.96") as screen:
        assert screen.model.id == "weact-0.96"  # chosen in the config; the serial says nothing
        screen.set_brightness(50)
        screen.show(picture((160, 80)))
    assert panel.brightness == [127] and panel.size == (160, 80)


# -- rev. C (Turing 2.1", 5", 8.8") -----------------------------------------------

AWAKE = {"vid": 0x0525, "pid": 0xA4A7, "serial_number": "20080411"}


@pytest.fixture
def no_waiting(monkeypatch):
    waited = []
    monkeypatch.setattr(turing_rev_c.RevCDisplay, "sleep", staticmethod(waited.append))
    return waited


def rev_c_frames(model, orientation):
    w, h = find_model(model).size(orientation)
    first = picture((w, h))
    second = first.copy()
    box = (w // 5, h // 3, w // 2, h // 2)
    ImageDraw.Draw(second).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=(9, 200, 77))
    return first, second, box


@pytest.mark.parametrize("rom", [87, 90])
@pytest.mark.parametrize(
    "model, orientation",
    [
        ("turing-5", "landscape"),
        ("turing-5", "portrait"),
        ("turing-2.1", "portrait"),
        ("turing-8.8", "landscape"),
        ("turing-8.8", "portrait"),
    ],  # fmt: skip
)
def test_rev_c_boxes_land_where_whole_frames_put_them(ports, no_waiting, model, orientation, rom):
    """A box goes row by row to offsets in the panel's own framebuffer; it must
    end up where the whole frame (simply turned) puts the same pixels."""
    first, second, box = rev_c_frames(model, orientation)
    panel = ports(FakeRevC(model, rom), **AWAKE)
    with display(model=model) as screen:
        screen.show(first)
        screen.show(second, box)
    assert panel.full_frames == 1 and panel.boxes == 1
    whole = ports(FakeRevC(model, rom), **AWAKE)  # the same port, a fresh panel
    with display(model=model) as screen:
        screen.show(second)
    assert ImageChops.difference(panel.screen, whole.screen).getbbox() is None


def test_rev_c_whole_frames_as_the_reference_turns_them(ports, no_waiting):
    panel = ports(FakeRevC("turing-5"), **AWAKE)
    frame = picture((800, 480))
    with display(model="turing-5") as screen:
        screen.set_brightness(50)
        screen.show(frame)  # landscape: the 5" framebuffer is landscape
        assert ImageChops.difference(panel.screen, frame).getbbox() is None
        tall = picture((480, 800), seed=30)
        screen.show(tall)  # portrait: a quarter turn counter-clockwise
        turned = tall.rotate(90, expand=True)
        assert ImageChops.difference(panel.screen, turned).getbbox() is None
    assert panel.commands[:3] == [0x01, 0x79, 0x96]  # HELLO, stop video, stop media
    assert panel.brightness == [127]
    assert not set(panel.commands) & turing_rev_c.NEVER  # no restart, no stored options
    assert panel.counts == []  # whole frames only


def test_rev_c_counts_its_boxes(ports, no_waiting):
    panel = ports(FakeRevC("turing-5"), **AWAKE)
    first, second, box = rev_c_frames("turing-5", "landscape")
    with display(model="turing-5") as screen:
        screen.show(first)
        for _ in range(3):
            screen.show(second, box)
    assert panel.counts == [0, 1, 2]


def test_rev_c_wakes_a_sleeping_panel(ports, no_waiting):
    awake = FakeRevC("turing-5")

    class Sleeping(FakeRevC):
        def close(self):  # opened and closed: it comes back with other ids
            ports(awake, device="/dev/ttyACM1", **AWAKE)

    ports(Sleeping(), device="/dev/ttyACM0", vid=0x1A86, pid=0xCA21, serial_number="USB7INCH")
    with display(model="auto") as screen:
        assert screen.model.id == "turing-5"  # the sleeping panel's serial number said 5"
        screen.show(picture((800, 480)))
    assert awake.commands[:3] == [0x01, 0x79, 0x96] and awake.full_frames == 1
    assert no_waiting  # it gave the panel a moment


def test_rev_c_takes_its_size_from_the_frame(ports, no_waiting):
    panel = ports(FakeRevC("turing-8.8"), **AWAKE)  # awake: no serial number names the size
    with display(model="auto") as screen:
        screen.show(picture((1920, 480)))
        assert screen.model.id == "turing-8.8"
    assert panel.full_frames == 1


def test_rev_c_that_is_no_rev_c_is_not_driven(ports, no_waiting):
    ports(FakeRevC("turing-5", hello=b"something else"), **AWAKE)
    with pytest.raises(DeviceError, match="did not answer"):
        display(model="turing-5").open()


def test_other_usb_gadgets_are_left_alone(ports, no_waiting):
    """Awake rev. C panels use generic Linux gadget ids. Without a model in the
    config, a port with those ids but not the panels' serial number is no panel."""
    pi = ports(FakeRevC("turing-5"), vid=0x0525, pid=0xA4A7, serial_number="0123456789")
    with pytest.raises(DeviceError):
        display(model="auto").open()
    assert pi.commands == []  # not a byte sent
    with display(model="turing-5") as screen:  # chosen in the config: trusted
        screen.show(picture((800, 480)))
    assert pi.full_frames == 1


def sleeping_rev_c(ports, awake, serial_number="CT21INCH", device="/dev/ttyACM1"):
    """A rev. C panel asleep on ``device``; opened, it comes back awake on ttyACM3."""

    class Sleeping(FakeRevC):
        def close(self):
            ports(awake, device="/dev/ttyACM3", vid=0x1D6B, pid=0x0121, serial_number="20080411")

    ports(Sleeping(), device=device, vid=0x1A86, pid=0xCA21, serial_number=serial_number)


@pytest.mark.parametrize(
    ("frame", "model"),
    [((800, 480), "turing-5"), ((480, 480), "turing-2.1"), ((480, 320), "turing-5")],
)
def test_ct21inch_does_not_say_the_size(ports, no_waiting, frame, model):
    """A 5" sold as UsbPCMonitor sleeps as CT21INCH, like the round 2.1": the
    frame decides, and failing that the 5" (a reported panel)."""
    awake = FakeRevC(model)
    sleeping_rev_c(ports, awake)
    with display(model="auto") as screen:
        screen.show(picture(frame))
        assert screen.model.id == model
    assert awake.full_frames == 1


def test_awake_rev_c_ids_fit_every_size(ports, no_waiting):
    from libre_panel.devices.serial_link import find_port

    ports(FakeRevC("turing-5"), device="/dev/ttyACM3", **AWAKE)
    found = find_port()
    assert not found.sure
    assert [m.id for m in found.candidates] == ["turing-2.1", "turing-5", "turing-8.8"]


def test_doctor_asks_which_panel_it_is(ports, no_waiting):
    from libre_panel.doctor import Doctor

    awake = FakeRevC("turing-5")
    sleeping_rev_c(ports, awake)
    asked, said = [], []

    def ask(question):
        asked.append(question)
        return "2" if "Which one" in question else "n"

    doctor = Doctor(ask=ask, say=said.append, pause=lambda s: None, frames=2)
    display_ = doctor.open_serial()
    try:
        assert display_.model.id == "turing-5"
    finally:
        display_.close()
    assert any("Which one is it? [1-2]" in q for q in asked)
    assert '1) Turing 2.1" round' in said[-3] and '2) Turing 5"' in said[-2]
    quiet = Doctor(ask=None, say=said.append, pause=lambda s: None)
    assert quiet._choose_size([find_model("turing-2.1"), find_model("turing-5")]).id == "turing-5"
    assert "turing-5" not in said[-1] and "assumed" in said[-1]


def test_doctor_uses_the_panel_in_the_config(ports, no_waiting):
    from libre_panel.doctor import Doctor

    awake = FakeRevC("turing-5")
    sleeping_rev_c(ports, awake)
    doctor = Doctor(ask=None, say=lambda text: None, pause=lambda s: None, model_id="turing-5")
    screen = doctor.open_serial()
    try:
        assert screen.model.id == "turing-5"
    finally:
        screen.close()


def test_a_model_of_another_kind_names_the_right_one(ports, no_waiting):
    """A rev. C panel set up as UsbPCMonitor 5" (rev. A) stayed black: now it says so."""
    sleeping_rev_c(ports, FakeRevC("turing-5"))
    with pytest.raises(DeviceError, match='rev. C.*"turing-2.1" or "turing-5"'):
        display(model="usbpcmonitor-5", port="/dev/ttyACM1").open()

"""Driver, main loop and doctor against the simulated panel."""

import threading
import time

import pytest
from PIL import Image

pytest.importorskip("Crypto")
pytest.importorskip("usb")

from fake_panel import FakePanel, transport_for  # noqa: E402

from libre_panel import app  # noqa: E402
from libre_panel.config import Config, DeviceConfig, SensorsConfig  # noqa: E402
from libre_panel.devices import turzx_usb  # noqa: E402
from libre_panel.devices.base import DeviceError, Display, FrameError  # noqa: E402
from libre_panel.devices.turzx_usb import TurzxUsbDisplay, access_hint, encode_frame  # noqa: E402
from libre_panel.doctor import Doctor  # noqa: E402


@pytest.fixture
def panel(monkeypatch):
    """The 9.2" panel is plugged in; UsbTransport.open finds it."""
    fake = FakePanel()

    def open_transport(pid=None):
        if fake.unplugged:
            raise DeviceError("no Turing/TURZX USB panel found")
        return transport_for(fake)

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(open_transport))
    return fake


def test_driver_shows_theme_frames(panel):
    display = TurzxUsbDisplay(DeviceConfig(driver="turzx"))
    display.open()
    display.set_brightness(60)
    display.show(Image.new("RGB", (1920, 480), "teal"))
    display.close()
    assert panel.commands == [10, 14, 102]
    assert panel.brightness == [61]
    assert display.model.id == "turing-9.2-usb"
    # landscape theme frame, rotated into the portrait framebuffer
    assert panel.frames[0].getpixel((0, 0))[:3] == (0, 128, 128)


def test_driver_reconnects_after_a_hiccup(panel):
    display = TurzxUsbDisplay(DeviceConfig(driver="turzx"))
    display.open()
    display.set_brightness(40)
    panel.fail_next_writes = 1  # e.g. standby / replug
    display.show(Image.new("RGB", (1920, 480)))
    assert panel.commands == [10, 14, 10, 14, 102]  # resync, brightness again, frame
    assert len(panel.frames) == 1


def test_driver_gives_up_when_unplugged(panel):
    display = TurzxUsbDisplay(DeviceConfig(driver="turzx"))
    display.open()
    panel.unplugged = True
    with pytest.raises(DeviceError):
        display.show(Image.new("RGB", (1920, 480)))


def test_oversized_frames_are_refused_without_reconnecting(monkeypatch):
    monkeypatch.setattr(turzx_usb, "MAX_PAYLOAD", 100)
    with pytest.raises(FrameError):
        encode_frame(Image.effect_noise((200, 200), 100))


@pytest.mark.parametrize(
    "platform, errno, expected",
    [
        ("win32", 13, "TURZX app"),
        ("win32", 16, "TURZX app"),
        ("linux", 13, "udev"),
        ("linux", 16, "in use"),
        ("darwin", 16, "in use"),
        ("linux", 5, None),
    ],
)
def test_access_hints(monkeypatch, platform, errno, expected):
    class USBError(Exception):
        pass

    exc = USBError("error")
    exc.errno = errno
    monkeypatch.setattr(turzx_usb.sys, "platform", platform)
    hint = access_hint(exc)
    assert (hint is None) if expected is None else (expected in hint)


class FlakyDisplay(Display):
    """Missing at start, then works, then gets unplugged once."""

    def __init__(self, config):
        super().__init__(config)
        self.opens = 0
        self.shown = 0
        self.plan = ["missing", "missing", "ok"]

    def open(self):
        self.opens += 1
        state = self.plan.pop(0) if self.plan else "ok"
        if state == "missing":
            raise DeviceError("no panel")

    def show(self, frame, region=None):
        self.shown += 1
        if self.shown == 2:
            raise DeviceError("unplugged")


def test_main_loop_waits_for_the_panel_and_survives_unplugging(monkeypatch):
    flaky = {}

    def create(cfg):
        flaky["display"] = FlakyDisplay(cfg)
        return flaky["display"]

    monkeypatch.setattr(app, "create_display", create)
    monkeypatch.setattr(app._Link, "BACKOFF_S", (0.01,))
    config = Config(device=DeviceConfig(driver="turzx"), sensors=SensorsConfig(providers=["demo"]))
    config.refresh_ms = 100
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and flaky.get("display", FlakyDisplay(None)).shown < 4:
        time.sleep(0.05)
    stop.set()
    thread.join(5)
    display = flaky["display"]
    assert display.opens >= 4  # two misses, first connection, reconnect after unplug
    assert display.shown >= 4


def answers(*replies):
    queue = list(replies)
    return lambda prompt: queue.pop(0) if queue else "y"


def test_doctor_passes_on_a_working_panel(panel):
    doctor = Doctor(ask=answers("y", "y", "y"), say=lambda s: None, pause=lambda s: None, frames=5)
    report = doctor.run()
    assert report.passed, report.text()
    text = report.text()
    assert "turing-9.2-usb" in text and "fps" in text and "turzx_00" in text
    assert panel.commands.count(102) == 2 + 5 + 1  # two cards, speed test, final card
    assert panel.brightness == [10, 102, 61]
    sizes = {frame.size for frame in panel.frames}
    assert sizes == {(480, 1920)}  # both orientations end up in the portrait framebuffer


def test_doctor_records_what_the_user_saw(panel):
    doctor = Doctor(
        ask=answers("n", "image is upside down", "y", "y"),
        say=lambda s: None,
        pause=lambda s: None,
        frames=1,
    )
    report = doctor.run()
    assert not report.passed
    assert "upside down" in report.text()


def test_doctor_without_questions_only_skips_the_visual_checks(panel):
    report = Doctor(ask=None, say=lambda s: None, pause=lambda s: None, frames=1).run()
    assert not report.passed and not report.failed
    assert "visual checks were skipped" in report.text()


def test_doctor_without_panel(panel):
    panel.unplugged = True
    report = Doctor(ask=None, say=lambda s: None, pause=lambda s: None).run()
    assert report.failed
    assert "no Turing/TURZX USB panel found" in report.text()


def test_doctor_report_has_no_personal_data(panel):
    report = Doctor(ask=None, say=lambda s: None, pause=lambda s: None, frames=1).run()
    text = report.text().lower()
    for private in ("serial number", "serial=", "serial_number", "users\\", "/home/"):
        assert private not in text

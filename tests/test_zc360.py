"""The JONSBO ZC-360: three screens of 640x180, each a USB device of its own, driven as one
panel of 1920x180, one screen after the other (devices/jonsbo_zc360.py)."""

import pytest
import usb.core
import usb.util
from PIL import Image

from libre_panel import identify
from libre_panel.config import DeviceConfig
from libre_panel.devices import base
from libre_panel.devices import jonsbo_zc360 as zc
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import find_model, models_for_usb
from libre_panel.devices.turzx import TurzxDisplay
from libre_panel.theme.defaults import default_theme
from libre_panel.theme.model import theme_for


def answer(state: int) -> bytes:
    """A controller's answer that says ``state``."""
    packet = bytearray(32)
    packet[0:2] = b"\xaa\x55"
    packet[2:10] = zc._cipher().encrypt(bytes((state, 0, 0, 0, 0, 0, 0, 0)))
    return bytes(packet)


class Screen:
    """A ZC-360 screen on the bus: what it was sent, and its answers (a fresh controller says
    0x60 to its first frame, 0x62 to every frame after command 88)."""

    def __init__(self, serial: str, address: int) -> None:
        self.serial, self.bus, self.address = serial, 1, address
        self.writes: list[bytes] = []
        self.committed = False
        self.frames = 0
        self.broken = False
        self._pending = b""

    def is_kernel_driver_active(self, interface):
        return False

    def set_configuration(self, value):
        pass

    def write(self, endpoint, data, timeout):
        assert endpoint == zc.ENDPOINT_OUT
        if self.broken:
            raise usb.core.USBError("gone")
        data = bytes(data)
        self.writes.append(data)
        if len(data) == 32:  # a command
            command = zc._cipher().decrypt(data[2:10])[0]
            self.committed = self.committed or command == zc.CMD_COMMIT
            self._pending = answer(0x59 if command == zc.CMD_COMMIT else 0x55)
        elif (data[11] << 8 | data[12]) == zc.RECORDS - 7:  # the frame's last write
            self.frames += 1
            self._pending = answer(zc.SHOWING if self.committed else zc.FIRST_FRAME)
        return len(data)

    def read(self, endpoint, size, timeout):
        assert endpoint == zc.ENDPOINT_IN
        pending, self._pending = self._pending, b""
        if not pending:
            raise usb.core.USBTimeoutError("no answer")
        return pending

    def commands(self) -> list[int]:
        return [zc._cipher().decrypt(w[2:10])[0] for w in self.writes if len(w) == 32]

    def frame_writes(self) -> int:
        return sum(1 for w in self.writes if len(w) == 4096)


@pytest.fixture
def bus(monkeypatch):
    screens = [Screen("C3", 7), Screen("A1", 5), Screen("B2", 6)]  # not in their order
    monkeypatch.setattr(zc, "_devices", lambda: list(screens))
    monkeypatch.setattr(zc, "_serial", lambda device: device.serial)
    monkeypatch.setattr(zc.time, "sleep", lambda seconds: None)
    for name in ("claim_interface", "release_interface", "dispose_resources"):
        monkeypatch.setattr(usb.util, name, lambda *args: None)
    yield sorted(screens, key=lambda s: s.serial)  # as the driver orders them
    for screen in screens:
        base.leave_place(zc._place(screen))


def opened(port: str = "", brightness: int = 70) -> zc.Zc360Display:
    display = zc.Zc360Display(DeviceConfig(driver="turzx", model=zc.MODEL_ID, port=port,
                                           brightness=brightness))  # fmt: skip
    display.open()
    return display


def thirds(*colours: str) -> Image.Image:
    picture = Image.new("RGB", (1920, 180))
    for n, colour in enumerate(colours):
        picture.paste(colour, (n * 640, 0, (n + 1) * 640, 180))
    return picture


def native_colour(screen: Screen) -> tuple[int, int, int]:
    """The colour of the first pixel of the last frame the screen was sent (BGR on the wire)."""
    last = [w for w in screen.writes if len(w) == 4096][-90]
    blue, green, red = last[32:35]
    return red, green, blue


def test_a_command_and_a_frame_are_as_the_controller_takes_them():
    packet = zc.command_packet(zc.CMD_BRIGHTNESS, 70)
    assert len(packet) == 32 and packet[:2] == b"\xaa\x55" and packet[10] == 0x01
    assert packet[31] == 0xBB and zc._cipher().decrypt(packet[2:10]) == bytes((84, 70)) + bytes(6)
    assert zc.answer_state(answer(0x62)) == 0x62 and zc.answer_state(b"") is None
    picture = Image.new("RGB", (640, 180), "black")
    picture.putpixel((0, 0), (255, 0, 0))  # the top left corner, red
    writes = zc.frame_writes(picture)
    assert len(writes) == 90 and all(len(w) == 4096 for w in writes)
    records = [w[i : i + 512] for w in writes for i in range(0, 4096, 512)]
    assert [r[11] << 8 | r[12] for r in records] == list(range(1, 721))
    assert all(r[:2] == b"\xaa\x55" and r[10] == 0x02 and r[31] == 0xBB for r in records)
    pixels = b"".join(r[32:] for r in records)
    # turned a quarter clockwise into the 180x640 framebuffer: the top left corner is at the
    # end of its first row, and the bytes are blue, green, red
    assert pixels[179 * 3 : 180 * 3] == bytes((0, 0, 255)) and pixels[:3] == bytes(3)


def test_the_three_screens_are_opened_set_up_and_written_one_after_the_other(bus):
    display = opened(brightness=70)
    try:
        for screen in bus:  # all three claimed and told 56, 84 (its brightness), 86
            assert screen.commands() == [56, 84, 86]
            assert zc._cipher().decrypt(screen.writes[1][2:10])[1] == 70
        display.show(thirds("#ff0000", "#00ff00", "#0000ff"))
        assert [native_colour(s) for s in bus] == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        assert all(s.commands()[-1] == zc.CMD_COMMIT for s in bus)  # each first frame: 88
        assert display.counters.bytes == 3 * 90 * 4096
        display.show(thirds("#ff0000", "#00ff00", "#0000ff"))  # after 88: once more, 0x62
        assert all(s.frame_writes() == 180 for s in bus)
        before = [s.frame_writes() for s in bus]
        display.show(thirds("#ff0000", "#00ff00", "#0000ff"))  # the same: nothing goes out
        assert [s.frame_writes() for s in bus] == before
        display.show(thirds("#ff0000", "#ffffff", "#0000ff"))  # the middle one only
        assert [s.frame_writes() - b for s, b in zip(bus, before, strict=True)] == [0, 90, 0]
        assert bus[1].commands().count(zc.CMD_COMMIT) == 1  # committed once, then 0x62
        bus[2].committed = False  # (as if powered again): 0x60, 88, and the picture once more
        display.show(thirds("#ff0000", "#ffffff", "#ffffff"))
        display.show(thirds("#ff0000", "#ffffff", "#ffffff"))
        display.show(thirds("#ff0000", "#ffffff", "#ffffff"))
        assert [s.frame_writes() - b for s, b in zip(bus, before, strict=True)] == [0, 90, 180]
        assert bus[2].commands().count(zc.CMD_COMMIT) == 2
    finally:
        display.close()
    assert not any(base.place_taken(zc._place(s)) for s in bus)


def test_the_order_of_the_screens_and_which_is_which(bus, monkeypatch):
    assert zc.screen_order("2,1,3") == [1, 0, 2] and zc.screen_order("312") == [2, 0, 1]
    assert zc.screen_order("1,1,2") == [0, 1, 2] and zc.screen_order("") == [0, 1, 2]
    display = opened(port="2,1,3")
    try:
        display.show(thirds("#ff0000", "#00ff00", "#0000ff"))
        # the left third on the second screen by serial number, the middle one on the first
        assert [native_colour(s) for s in bus] == [(0, 255, 0), (255, 0, 0), (0, 0, 255)]
        monkeypatch.setattr(identify, "showing", lambda: True)
        display.show(thirds("#ff0000", "#00ff00", "#0000ff"))
        for number, screen in enumerate(bus, 1):  # each its number, whatever the order
            sent = [w for w in screen.writes if len(w) == 4096][-90:]
            pixels = b"".join(w[i + 32 : i + 512] for w in sent for i in range(0, 4096, 512))
            native = Image.frombytes("RGB", (180, 640), pixels)
            assert native.getbbox() is not None  # (a number on black)
            expected = identify.picture(number, (640, 180)).transpose(Image.Transpose.ROTATE_270)
            red, green, blue = expected.split()
            assert native.tobytes() == Image.merge("RGB", (blue, green, red)).tobytes()
    finally:
        display.close()


def test_a_screen_missing_or_gone_is_a_fault_the_loop_comes_back_from(bus, monkeypatch):
    monkeypatch.setattr(zc, "_devices", lambda: bus[:2])
    with pytest.raises(DeviceError, match="2 of its 3 screens"):
        opened()
    assert not any(base.place_taken(zc._place(s)) for s in bus)
    monkeypatch.setattr(zc, "_devices", lambda: list(bus))
    display = opened()
    try:
        bus[2].broken = True
        with pytest.raises(DeviceError, match="stopped answering"):
            display.show(thirds("#ff0000", "#00ff00", "#0000ff"))
        assert display.counters.usb_errors == 1
    finally:
        display.close()


def test_brightness_and_dark_on_quit(bus):
    display = opened()
    try:
        display.set_brightness(30)
        assert all(zc._cipher().decrypt(s.writes[-1][2:10])[:2] == bytes((84, 30)) for s in bus)
        assert display.screen_off()
        assert all(zc._cipher().decrypt(s.writes[-1][2:10])[:2] == bytes((84, 0)) for s in bus)
    finally:
        display.close()


def test_the_zc360_is_a_model_with_its_own_lumen(bus):
    model = find_model("jonsbo-zc360")
    assert model.size("landscape") == (1920, 180) and model.driver == "unverified"
    assert models_for_usb(0x43A8, 0x0E61) == [model]
    assert theme_for(default_theme(model), "landscape", (1920, 180)) == "lumen-1920x180"
    display = TurzxDisplay(DeviceConfig(driver="turzx", model="jonsbo-zc360"))
    display.open()  # the TURZX family's driver hands it on
    try:
        assert isinstance(display._impl, zc.Zc360Display) and display.model is model
        display.show(thirds("#ff0000", "#00ff00", "#0000ff"))
        assert all(s.frames == 1 for s in bus)
    finally:
        display.close()


# -- each screen a panel of its own ------------------------------------------------------------


def alone(port: str = "", brightness: int = 70) -> zc.Zc360Display:
    config = DeviceConfig(driver="turzx", model=zc.SCREEN_MODEL_ID, port=port,
                          brightness=brightness)  # fmt: skip
    display = zc.Zc360Display(config, find_model(zc.SCREEN_MODEL_ID))
    display.open()
    return display


def test_a_screen_is_a_panel_of_its_own_by_its_place_on_the_bus(bus):
    by_place = sorted(bus, key=lambda s: s.address)  # (the fake screens have no ports)
    assert zc.screen_number("2") == 1 and zc.screen_number("") is None
    assert zc.screen_number("4") is None and zc.screen_number("1,2,3") is None
    second = alone(port="2", brightness=40)
    try:
        screen = by_place[1]
        assert screen.commands() == [56, 84, 86] and not by_place[0].writes
        assert zc._cipher().decrypt(screen.writes[1][2:10])[:2] == bytes((84, 40))
        second.show(Image.new("RGB", (640, 180), "#ff0000"))
        assert native_colour(screen) == (255, 0, 0) and screen.commands()[-1] == zc.CMD_COMMIT
        assert not any(s.frame_writes() for s in bus if s is not screen)
        second.show(Image.new("RGB", (180, 640), "#00ff00"))  # a portrait card: turned to fit
        assert native_colour(screen) == (0, 255, 0)
        with pytest.raises(DeviceError, match="screen 2 open"):  # another panel has it
            alone(port="2")
        first = alone()  # without a port: the first free one
        try:
            assert by_place[0].commands() == [56, 84, 86]
            assert not zc.all_free() and zc.present()  # "auto" leaves the ZC-360 to them
            second.set_brightness(20)  # each panel its own screen's
            assert zc._cipher().decrypt(screen.writes[-1][2:10])[:2] == bytes((84, 20))
            assert zc._cipher().decrypt(by_place[0].writes[-1][2:10])[:2] != bytes((84, 20))
        finally:
            first.close()
    finally:
        second.close()
    assert zc.all_free()


def test_a_screen_that_is_not_there_is_a_fault(bus, monkeypatch):
    monkeypatch.setattr(zc, "_devices", lambda: bus[:2])
    with pytest.raises(DeviceError, match="screen 3 is not on the USB bus"):
        alone(port="3")
    assert not any(base.place_taken(zc._place(s)) for s in bus)


def test_the_screens_take_turns_across_the_panels_processes(bus, monkeypatch):
    said = []
    monkeypatch.setattr(zc, "_wait", lambda handle: said.append("turn"))
    monkeypatch.setattr(zc, "_done", lambda handle: said.append("done"))
    for screen in bus:
        write = screen.write
        monkeypatch.setattr(
            screen, "write", lambda e, d, t, w=write: said.append(len(d)) or w(e, d, t)
        )
    display = alone(port="1")
    try:
        said.clear()
        display.show(Image.new("RGB", (640, 180), "#ff0000"))
        # the frame and its answer in one turn (then the first frame's 88, a command)
        assert said == ["turn", *[4096] * 90, "done", 32]
    finally:
        display.close()


def test_a_screen_of_its_own_has_lumen_in_its_size(bus):
    model = find_model(zc.SCREEN_MODEL_ID)
    assert model.size("landscape") == (640, 180) and model.driver == "supported"  # (#13)
    assert model.hidden_edges("landscape") == {"top": 0, "right": 2, "bottom": 0, "left": 2}
    assert models_for_usb(0x43A8, 0x0E61) == [find_model(zc.MODEL_ID)]  # "auto": all three
    assert theme_for(default_theme(model), "landscape", (640, 180)) == "lumen-640x180"
    for theme in ("lumen-media", "lumen-clock", "lumen-rings", "lumen-memory"):
        assert theme_for(f"{theme}-640x180", "landscape", (640, 180)) == f"{theme}-640x180"

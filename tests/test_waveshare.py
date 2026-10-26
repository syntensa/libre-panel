"""Waveshare's USB monitors (#21): the frames and maps of their protocol to the byte, a display
that answers as theirs do, and the service that sends it the readings, its brightness and its
picture turned, and writes nothing on its card."""

import re
import struct
import subprocess
import sys
import threading
import zlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_service import wait_for

from libre_panel.devices import waveshare
from libre_panel.devices.base import DeviceError, leave_place, take_place
from libre_panel.services import waveshare as service

MAGIC = b"AA551234 FIXEDCMDHEAD "
RULES = Path(__file__).parents[1] / "src/libre_panel/assets/60-libre-panel.rules"


def qstring(text):
    data = text.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def answer(command, payload=b""):
    return MAGIC + struct.pack("<4I", 2, command, len(payload), zlib.crc32(payload)) + payload


class Display:
    """A Waveshare USB monitor on a port: it reads the frames written to it and answers each,
    as PCMonitorLinux does (``says``: what FIND_DEVICE answers; ``silent``: it answers
    nothing, as a port of another device with the same ids)."""

    def __init__(self, says=None, silent=False, themes=()):
        self.says, self.silent, self.themes = says, silent, list(themes)
        self.frames = waveshare.Frames()
        self.got = []  # (command, payload)
        self.out = bytearray()
        self.closed = False
        self.lock = threading.Lock()

    def write(self, data):
        for frame in self.frames.feed(data):
            assert frame.kind == 0
            with self.lock:
                self.got.append((frame.command, frame.payload))
            if self.silent:
                continue
            if frame.command == waveshare.FIND_DEVICE:
                reply = waveshare.qmap(self.says) if self.says else b""
            elif frame.command == waveshare.GET_VERSION:
                reply = waveshare.qmap({"versionV2": "V2.04"})
            elif frame.command == waveshare.GET_THEME:
                reply = waveshare.qmap({**{path: "123" for path in self.themes},
                                        "/data/PCMonitorLinux": "overlay"})  # fmt: skip
            elif frame.command in (waveshare.SET_BRIGHTNESS, waveshare.SET_CANVAS_FLIP,
                                   waveshare.RELOAD_THEME):  # fmt: skip
                reply = frame.payload  # (they echo what they were sent)
            else:
                reply = b""
            with self.lock:
                self.out += answer(frame.command, reply)
        return len(data)

    def read(self, size):
        with self.lock:
            data, self.out[:] = bytes(self.out[:size]), self.out[size:]
        return data

    def close(self):
        self.closed = True

    def commands(self):
        with self.lock:
            return [command for command, _ in self.got]

    def last(self, command):
        with self.lock:
            return next(payload for c, payload in reversed(self.got) if c == command)


class Clock:
    """A clock that moves a tenth of a second each time it is read (a silent port's reads)."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.1
        return self.now


# -- the protocol ---------------------------------------------------------------------------


def test_a_frame_is_the_head_the_numbers_and_the_payload():
    assert waveshare.frame(waveshare.FIND_DEVICE) == MAGIC + bytes(16)
    assert len(MAGIC) == 22 and MAGIC.endswith(b" ")
    payload = b"80"
    sent = waveshare.frame(waveshare.SET_BRIGHTNESS, payload)
    assert sent == MAGIC + struct.pack("<4I", 0, 4, 2, zlib.crc32(payload)) + payload
    assert sent[22:26] == bytes(4) and sent[26:30] == b"\x04\x00\x00\x00"  # little endian


def test_a_map_is_written_as_qt_writes_it_and_read_back():
    assert waveshare.qmap({"a": "1"}) == b"\x00\x00\x00\x01" + qstring("a") + qstring("1")
    # from the last of the keys' UTF-16 code units to the first, as Qt 5 writes a QMap (a theme
    # Waveshare's program saved has its maps so: canvas_w, canvas_rotate, canvas_h, canvas_flip)
    written = waveshare.qmap({"b": "2", "Z": "3", "Cpu温度": "54", "a": "1"})
    assert written.startswith(b"\x00\x00\x00\x04")
    order = list(waveshare.read_qmap(written))
    assert order == ["b", "a", "Z", "Cpu温度"]
    canvas = {"canvas_flip": "0", "canvas_h": "480", "canvas_rotate": "0", "canvas_w": "480"}
    assert list(waveshare.read_qmap(waveshare.qmap(canvas))) == [
        "canvas_w", "canvas_rotate", "canvas_h", "canvas_flip",
    ]  # fmt: skip
    assert waveshare.read_qmap(written) == {"b": "2", "Z": "3", "Cpu温度": "54", "a": "1"}
    null = b"\x00\x00\x00\x01" + qstring("res") + b"\xff\xff\xff\xff"
    assert waveshare.read_qmap(null) == {"res": None}
    for broken in (b"\x00\x00", b"\x00\x00\x00\x01" + qstring("a"), waveshare.qmap({}) + b"x"):
        with pytest.raises(ValueError):
            waveshare.read_qmap(broken)
    assert waveshare.answer_of(b"") is None
    assert waveshare.answer_of(b"80") == "80"  # brightness echoes its number


def test_frames_are_found_among_other_bytes_in_any_pieces():
    one = answer(waveshare.GET_VERSION, waveshare.qmap({"versionV2": "V2.04"}))
    two = answer(waveshare.SYSTEM_DATA)
    bad = bytearray(answer(waveshare.FIND_DEVICE, b"xy"))
    bad[-1] ^= 0xFF  # its CRC does not fit: passed over
    stream = b"noise" + one + bytes(bad) + two
    frames = waveshare.Frames()
    found = []
    for at in range(0, len(stream), 7):
        found += frames.feed(stream[at : at + 7])
    assert [(f.kind, f.command) for f in found] == [(2, 8), (2, 1)]
    assert waveshare.answer_of(found[0].payload) == {"versionV2": "V2.04"}
    assert frames.skipped >= len(b"noise")
    # a head whose length cannot be the display's is passed over too
    huge = MAGIC + struct.pack("<4I", 2, 1, waveshare.MAX_PAYLOAD + 1, 0)
    assert waveshare.Frames().feed(huge + two) == [waveshare.Frame(2, 1, b"")]


def test_the_link_asks_and_the_display_answers():
    display = Display(says={"screen_inch": "2.8", "screen_w": "480", "screen_h": "640"})
    link = waveshare.Link(display)
    assert link.hello() == {"screen_inch": "2.8", "screen_w": "480", "screen_h": "640"}
    assert link.version() == "V2.04"
    link.set_brightness(130)
    assert display.last(waveshare.SET_BRIGHTNESS) == b"255"  # the display counts 0 to 255
    link.set_brightness(50)
    assert display.last(waveshare.SET_BRIGHTNESS) == b"128"
    link.set_flipped(True)
    assert waveshare.read_qmap(display.last(waveshare.SET_CANVAS_FLIP)) == {"canvas_flip": "1"}
    link.send_values({"Cpu_Temp": "54"})
    assert waveshare.read_qmap(display.last(waveshare.SYSTEM_DATA)) == {"Cpu_Temp": "54"}
    # a display that says nothing of itself still answers the handshake
    assert waveshare.Link(Display(says=None)).hello() == {}
    link.close()
    assert display.closed


def test_the_link_has_no_command_that_writes_on_the_card():
    """FILE_START (6), FILE_END (7) and DELETE_THEMES (11) write and delete on the display's
    card, SET_SCAN_STATE (5) does nothing known: Libre Panel sends none of them. (RELOAD_THEME
    shows a theme that is on the card already.)"""
    assert waveshare.COMMANDS.isdisjoint({5, 6, 7, 11})
    display = Display()
    link = waveshare.Link(display)
    for command in (5, 6, 7, 11):
        with pytest.raises(ValueError):
            link.ask(command)
    assert display.got == []


def test_a_port_that_does_not_answer_is_not_the_display():
    display = Display(silent=True)
    with pytest.raises(DeviceError, match="did not answer"):
        waveshare.Link(display, clock=Clock()).hello()
    assert display.commands() == [waveshare.FIND_DEVICE]  # asked once, nothing more


def test_the_display_is_the_first_port_that_answers_the_handshake(monkeypatch):
    ports = {"/dev/ttyACM0": Display(silent=True), "/dev/ttyACM1": Display(says={"a": "b"})}
    monkeypatch.setattr(waveshare, "candidates", lambda port="": list(ports))
    monkeypatch.setattr(waveshare, "ANSWER_S", 0.3)

    def opener(device):
        return ports[device]

    link, port, said = waveshare.connect(opener=opener)
    assert port == "/dev/ttyACM1" and said == {"a": "b"} and link.port is ports[port]
    assert ports["/dev/ttyACM0"].closed  # the silent one is let go
    ports.pop("/dev/ttyACM1")
    with pytest.raises(DeviceError, match="ttyACM0: the display did not answer"):
        waveshare.connect(opener=opener)


def test_ports_are_looked_at_by_their_ids_and_a_taken_one_is_left(monkeypatch):
    from serial.tools import list_ports

    def port(device, vid, pid, product=None, description=""):
        return SimpleNamespace(device=device, vid=vid, pid=pid, product=product,
                               description=description)  # fmt: skip

    found = [
        port("/dev/ttyACM0", 0x1234, 0x5678),  # the placeholder ids
        port("/dev/ttyACM1", 0x1D6B, 0x0104, "Virtual Serial Port"),
        port("COM7", 0x1D6B, 0x0104, None, "USB Serial Device (COM7)"),  # Windows: no name
        port("/dev/ttyACM2", 0x1D6B, 0x0104, "Some Other Gadget"),  # another gadget
        port("/dev/ttyACM3", 0x1A86, 0x5722),  # a TURZX panel
        port("/dev/ttyS0", None, None),
    ]
    monkeypatch.setattr(list_ports, "comports", lambda: found)
    assert waveshare.candidates() == ["/dev/ttyACM0", "/dev/ttyACM1", "COM7"]
    take_place("/dev/ttyACM0")
    try:
        assert waveshare.candidates() == ["/dev/ttyACM1", "COM7"]
        assert waveshare.candidates("/dev/ttyACM0") == []
    finally:
        leave_place("/dev/ttyACM0")
    assert waveshare.candidates("/dev/ttyUSB9") == ["/dev/ttyUSB9"]  # a port named is asked


@pytest.mark.skipif(sys.platform == "win32", reason="Linux's /proc, with links")
def test_another_program_that_has_the_port_open_is_named(tmp_path):
    """wsmonitor's service opens the port without pyserial's lock: Libre Panel would write over
    its frames, so on Linux the port counts as busy while another process has it open."""
    device = tmp_path / "ttyACM0"
    device.write_text("")
    for pid, name, target in ((101, "python3", device), (102, "bash", tmp_path / "other"),
                              (103, "python3", device)):  # fmt: skip
        (tmp_path / "proc" / str(pid) / "fd").mkdir(parents=True)
        (tmp_path / "proc" / str(pid) / "comm").write_text(f"{name}\n")
        (tmp_path / "proc" / str(pid) / "fd" / "3").symlink_to(target)
    (tmp_path / "proc" / "self").mkdir()  # (not a process number)
    assert waveshare.holders(str(device), tmp_path / "proc") == ["python3", "python3"]
    assert waveshare.holders(str(tmp_path / "nothing"), tmp_path / "proc") == []
    assert waveshare.holders(str(device), tmp_path / "no-proc") == []
    reason = f"{device} is busy: python3 has it open"
    assert service.kind_of(reason) == "busy"


# -- the service ------------------------------------------------------------------------------


def snapshot(readings):
    return SimpleNamespace(value=readings.get, readings=readings)


READINGS = {
    "cpu.load": 12.6, "cpu.temp": 54.4, "cpu.freq": 4712.3, "cpu.name": "chip0000",
    "gpu.load": 97.0, "gpu.temp": 68.2, "gpu.mem.used": 8123.6, "mem.load": 41.0,
    "mem.used": 13.04, "mem.free": 18.66, "net.down": 2_560_000.0, "net.up": 51_200.0,
    "audio.volume": 35.0, "disk.1.load": 40.2, "disk.2.load": 71.0,
    "fan.board.case_1": 800.0, "fan.board.cpu_fan": 1180.4, "fan.board.pump": 2934.0,
    "fan.gpu.fan_1.percent": 40.0,
}  # fmt: skip


def test_the_readings_go_under_the_display_s_english_and_chinese_keys():
    got = service.values(snapshot(READINGS), datetime(2026, 1, 5, 9, 8, 7))
    assert got["Cpu_Freq"] == got["Cpu使用率"] == "13"  # Waveshare's name for the load
    assert got["Cpu_Temp"] == got["Cpu温度"] == "54"
    assert got["Cpu_Clock"] == "4712" and got["Cpu_Model"] == got["Cpu型号"] == "chip0000"
    assert got["Gpu_Load"] == "97" and got["GPU_Temp"] == "68" and got["Gpu_Usedata"] == "8124"
    assert got["Memory_Used"] == "13.0" and got["Memory_Available"] == "18.7"
    assert got["Download_Speed"] == "2500" and got["Upload_Speed"] == "50.0"  # KB/s
    assert service.kilo(4300.0) == "4.2" and service.kilo(None) is None
    # the fans by what their names say, whatever chip and header they are on
    assert got["Cpu_Fan"] == got["Cpu风扇"] == "1180" and got["Pump_Fan"] == "2934"
    assert service.fan(snapshot({"fan.cpu": 900.0, "fan.board.cpu_fan": 1.0}), "cpu") == 900.0
    assert got["vol"] == got["音量"] == "35"
    assert got["Partition_Usage_0"] == "40" and got["分区利用率_1"] == "71"
    assert got["time"] == got["时间"] == "09:08:07" and got["date"] == "2026-01-05"
    assert got["hour"] == "09" and got["minute"] == "08" and got["second"] == "07"
    assert got["week"] == got["星期"] == "Mon"  # short, in the app's language
    assert (got["year"], got["month"], got["day"]) == ("2026", "01", "05")
    assert "Cpu_Power" not in got and "GPU_Temp" in got  # only what Libre Panel has
    assert all(isinstance(value, str) for value in got.values())
    # the drive's use without numbers: the first partition
    assert service.values(snapshot({"disk.load": 63.0}), datetime(2026, 1, 5))[
        "Partition_Usage_0"
    ] == "63"  # fmt: skip
    assert set(service.values(None, datetime(2026, 1, 5))) == {
        "time", "时间", "date", "日期", "week", "星期", "year", "month", "day",
        "hour", "时", "minute", "分", "second", "秒",
    }  # fmt: skip


def test_the_values_can_carry_their_units_as_waveshare_s_program_sends_them():
    """A capture of Waveshare's program (#21): "75%", "24RPM", "31℃", "0.0KB/s"."""
    got = service.values(snapshot(READINGS), datetime(2026, 1, 5, 9, 8, 7), units=True)
    assert got["Cpu_Freq"] == "13%" and got["Cpu_Temp"] == got["Cpu温度"] == "54℃"
    assert got["Cpu_Clock"] == "4712MHZ" and got["Cpu_Fan"] == "1180RPM"
    assert got["Memory_Used"] == "13.0GB" and got["Upload_Speed"] == "50.0KB/s"
    assert got["Partition_Usage_0"] == "40%" and got["Gpu_Usedata"] == "8124MB"
    assert got["vol"] == "35" and got["Cpu_Model"] == "chip0000" and got["time"] == "09:08:07"


def test_why_the_display_is_not_there_is_told_apart():
    windows = "COM5: could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)"
    locked = "/dev/ttyACM0: Could not exclusively lock port /dev/ttyACM0: [Errno 11] Resource temporarily unavailable"  # noqa: E501
    assert service.kind_of(windows) == service.kind_of(locked) == "busy"
    assert service.kind_of("/dev/ttyACM0: [Errno 13] Permission denied: '/dev/ttyACM0'") == "denied"
    assert service.kind_of("/dev/ttyACM0: the display did not answer") == "silent"
    assert service.kind_of("no Waveshare USB monitor is connected") == "absent"


class Host:
    def __init__(self, readings):
        self.readings = readings
        self.wanted = ()

    def want(self, *keys):
        self.wanted = keys

    def snapshot(self):
        return snapshot(self.readings)


def started(options, connect):
    given = {"port": "", "brightness": 0, "flip": "", "units": False, "every_s": 0.25,
             "theme": "", "rotate": [], "rotate_min": 0.0, **options}  # fmt: skip
    feeder = service.Waveshare(Host(READINGS), given, connect=connect,
                               now=lambda: datetime(2026, 1, 5, 9, 8, 7))  # fmt: skip
    feeder.start()
    return feeder


def test_the_service_sends_the_readings_and_sets_what_is_asked():
    display = Display(says={"screen_inch": "2.8", "screen_w": "480", "screen_h": "640"})
    asked = []

    def connect(port):
        asked.append(port)
        link = waveshare.Link(display)
        return link, "/dev/ttyACM0", link.hello()

    feeder = started({"brightness": 60, "flip": "upside-down", "port": "/dev/ttyACM0"}, connect)
    try:
        assert wait_for(lambda: display.commands().count(waveshare.SYSTEM_DATA) >= 2, 5)
        assert {"cpu.temp", "disk.1.load", "fan.*"} <= set(feeder.host.wanted)
        commands = display.commands()
        assert commands[:4] == [waveshare.FIND_DEVICE, waveshare.GET_VERSION,
                                waveshare.SET_BRIGHTNESS, waveshare.SET_CANVAS_FLIP]  # fmt: skip
        assert display.last(waveshare.SET_BRIGHTNESS) == b"153"  # 60 % of 255
        assert waveshare.read_qmap(display.last(waveshare.SET_CANVAS_FLIP)) == {"canvas_flip": "1"}
        sent = waveshare.read_qmap(display.last(waveshare.SYSTEM_DATA))
        assert sent["Cpu_Temp"] == "54" and sent["时间"] == "09:08:07"
        assert feeder.summary() == {"state": "connected", "port": "/dev/ttyACM0", "inch": "2.8",
                                    "size": "480×640", "version": "V2.04", "themes": [],
                                    "theme": ""}  # fmt: skip
        assert asked == ["/dev/ttyACM0"]
        assert set(display.commands()) <= waveshare.COMMANDS
    finally:
        feeder.stop()
    assert display.closed


CARD = [
    "/data/theme/480x480/021-astronaut.Theme",
    "/data/theme/480x480/Space Suit.Theme",
    "/data/theme/480x480/大波浪-MIC.Theme",
    "/data/theme/480x480/Vinyl.Theme",
]


def test_the_link_lists_the_card_s_themes_and_shows_one():
    display = Display(themes=CARD)
    link = waveshare.Link(display)
    listed = link.themes()  # by name, the display's own entry left out
    assert listed == sorted(CARD, key=lambda path: waveshare.theme_name(path).casefold())
    assert [waveshare.theme_name(path) for path in listed][:2] == ["021-astronaut", "Space Suit"]
    link.show_theme(CARD[2])
    assert display.last(waveshare.RELOAD_THEME).decode("utf-8") == CARD[2]
    assert waveshare.Link(Display()).themes() == []  # (an empty card)


def test_the_service_shows_a_theme_on_the_card_and_takes_turns_with_others():
    """The theme chosen goes on the display when it connects, the picture turned again after it
    (loading a theme sets it upright); the themes in turn follow at random, never the same
    twice running; a theme that is not on the card is passed over. The card's themes are on
    the service's card in the app."""
    display = Display(themes=CARD)

    def connect(port):
        link = waveshare.Link(display)
        return link, "/dev/ttyACM0", link.hello()

    picked = []

    def pick(choices):
        picked.append(list(choices))
        return choices[0]

    options = {"flip": "upside-down", "theme": CARD[1], "rotate_min": 0.25 / 60,
               "rotate": [CARD[0], CARD[3], "/data/theme/480x480/Gone.Theme"]}  # fmt: skip
    given = {"port": "", "brightness": 0, "units": False, "every_s": 0.25, **options}
    feeder = service.Waveshare(Host(READINGS), given, connect=connect, pick=pick,
                               now=lambda: datetime(2026, 1, 5, 9, 8, 7))  # fmt: skip
    feeder.start()
    try:
        assert wait_for(lambda: display.commands().count(waveshare.RELOAD_THEME) >= 3, 10)
    finally:
        feeder.stop()
    shown = [payload.decode() for command, payload in display.got
             if command == waveshare.RELOAD_THEME]  # fmt: skip
    assert shown[0] == CARD[1] and set(shown[1:]) <= {CARD[0], CARD[3]}
    assert all(a != b for a, b in zip(shown, shown[1:], strict=False))
    assert all("/data/theme/480x480/Gone.Theme" not in choices for choices in picked)
    commands = display.commands()
    after = commands.index(waveshare.RELOAD_THEME)
    assert commands[after + 1] == waveshare.SET_CANVAS_FLIP  # turned again after the theme
    summary = feeder.summary()
    assert summary["themes"][0] == {"id": CARD[0], "name": "021-astronaut"}
    assert summary["theme"] in (CARD[0], CARD[3])
    assert set(commands) <= waveshare.COMMANDS


def test_the_service_leaves_brightness_and_picture_alone_unless_they_are_set():
    display = Display()

    def connect(port):
        link = waveshare.Link(display)
        return link, "COM5", link.hello()

    feeder = started({}, connect)
    try:
        assert wait_for(lambda: waveshare.SYSTEM_DATA in display.commands(), 5)
    finally:
        feeder.stop()
    assert waveshare.SET_BRIGHTNESS not in display.commands()
    assert waveshare.SET_CANVAS_FLIP not in display.commands()
    assert feeder.summary()["state"] == "connected" and feeder.summary()["inch"] == ""


def test_the_service_says_why_it_has_no_display_and_keeps_looking():
    tries = []

    def connect(port):
        tries.append(port)
        raise DeviceError(
            "COM5: could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)"
        )  # noqa: E501

    feeder = started({}, connect)
    try:
        assert wait_for(lambda: feeder.summary()["state"] == "busy", 5)
        assert "Access is denied" in feeder.summary()["reason"]
    finally:
        feeder.stop()
    assert tries  # (and it stops at once, between two tries)


def test_a_display_that_goes_away_is_looked_for_again():
    displays = [Display(), Display()]

    def connect(port):
        if not displays:
            raise DeviceError("no Waveshare USB monitor is connected")
        display = displays[0]
        link = waveshare.Link(display, clock=Clock())
        return link, "/dev/ttyACM0", link.hello()

    feeder = started({}, connect)
    try:
        first = displays[0]
        assert wait_for(lambda: waveshare.SYSTEM_DATA in first.commands(), 5)
        first.silent = True  # unplugged: no answer
        displays.clear()
        assert wait_for(lambda: feeder.summary()["state"] == "absent", 10)
        assert first.closed
    finally:
        feeder.stop()


def test_the_service_is_built_in_and_its_options_are_worded_in_the_app():
    from libre_panel.plugins.loader import BUILT_IN_PARTS

    assert BUILT_IN_PARTS["services"]["waveshare"] == "libre_panel.services.waveshare:Waveshare"
    app = Path(__file__).parents[1] / "src/libre_panel/editor/app/views/settings/services.js"
    words = app.read_text(encoding="utf-8")
    for option in service.Waveshare.options:
        assert f'"waveshare.{option}"' in words
    assert "waveshare: {" in words and "WaveshareStatus" in words


# -- Linux: the rule that gives the display to cdc_acm ---------------------------------------


def test_the_udev_rule_gives_the_display_s_interfaces_to_cdc_acm(tmp_path):
    """Units with the placeholder ids are taken by usbtouchscreen; once it has the display's
    data interface (class 0a, which no touchscreen has), both go to cdc_acm. The rule's command
    is run here as udev runs it (its %k, %% and $$ replaced), on a folder for /sys."""
    text = RULES.read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "RUN+=" in line)
    for part in ('DRIVER=="usbtouchscreen"', 'ATTRS{idVendor}=="1234"', 'ATTRS{idProduct}=="5678"',
                 'ATTR{bInterfaceClass}=="0a"', 'ENV{DEVTYPE}=="usb_interface"'):  # fmt: skip
        assert part in line
    assert 'SUBSYSTEM=="tty", ATTRS{idVendor}=="1234", ATTRS{idProduct}=="5678"' in text
    if sys.platform == "win32":
        pytest.skip("runs a shell")
    command = re.search(r'RUN\+="(.*)"\s*$', line).group(1)
    replaced = re.sub(r"%k|%%|\$\$", lambda m: {"%k": "8-6.3:1.1", "%%": "%", "$$": "$"}[m[0]],
                      command)  # fmt: skip
    drivers = tmp_path / "drivers"
    for name in ("usbtouchscreen", "cdc_acm"):
        (drivers / name).mkdir(parents=True)
    (drivers / "usbtouchscreen" / "8-6.3:1.0").write_text("")  # it has the control interface too
    script = re.match(r"/bin/sh -c '(.*)'$", replaced).group(1)
    # (each write to a file of /sys is one of its own: here they are kept one after the other)
    script = script.replace("/sys/bus/usb/drivers", str(drivers)).replace("> ", ">> ")
    subprocess.run(["/bin/sh", "-c", script], check=True)
    assert (drivers / "usbtouchscreen" / "unbind").read_text() == "8-6.3:1.0" + "8-6.3:1.1"
    assert (drivers / "cdc_acm" / "bind").read_text() == "8-6.3:1.0"

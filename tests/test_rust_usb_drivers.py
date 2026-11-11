"""The drivers of the USB screens of other makers in Rust (rust/devices: udisp ...) against
those of devices/, to the byte: each opened on a screen the test scripts (its product string,
its interfaces, what it answers each read), frames shown, the brightness set, the JPEGs' layout
changed, closed; what went to the screen, in order, and what the driver says of itself."""

from __future__ import annotations

import base64
import io
import random
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest
import usb.core
from PIL import Image
from rust_check import image, noise, run

from libre_panel.config import DeviceConfig
from libre_panel.devices import (
    beadapanel,
    jonsbo_zc360,
    lianli_usb,
    nzxt_kraken,
    thermalright,
    thermaltake,
    turzx_usb,
    udisp,
    usb_link,
)
from libre_panel.devices.models import get_model


def _b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


class FakeScreen:
    """A USB screen as pyusb shows it to usb_link and the drivers, scripted as the Rust side's
    (check/src/usb_drivers.rs): each read takes the next answer, a write fails as told."""

    bus, address = 1, 2

    def __init__(self, case: dict[str, Any], events: list[Any]) -> None:
        self.product = case["product"]
        self.events = events
        self.answers = deque(case.get("answers", []))
        self.fail_writes = {n: (errno, text) for n, errno, text in case.get("fail_writes", [])}
        self.writes = 0
        self.config = [
            SimpleNamespace(
                bInterfaceNumber=i["number"],
                bInterfaceClass=i["class"],
                endpoints=[
                    SimpleNamespace(bEndpointAddress=a, wMaxPacketSize=m)
                    for a, m, _interrupt in i["endpoints"]
                ],  # fmt: skip
            )
            for i in case["interfaces"]
        ]

    def get_active_configuration(self) -> list[Any]:
        return [_Interface(i) for i in self.config]

    def _answer(self, size: int) -> bytes:
        answer = self.answers.popleft() if self.answers else None
        if answer is None:
            raise usb.core.USBTimeoutError("Operation timed out", errno=110)
        if isinstance(answer, dict):
            raise usb.core.USBError(answer["error"])
        return base64.b64decode(answer)[:size]

    def write(self, endpoint: int, data: bytes, timeout: int) -> int:
        self.writes += 1
        self.events.append(["write", endpoint, _b64(data)])
        if self.writes in self.fail_writes:
            errno, text = self.fail_writes[self.writes]
            raise usb.core.USBError(text, errno=errno)
        return len(data)

    def read(self, endpoint: int, size: int, timeout: int) -> bytes:
        self.events.append(["read", endpoint, size])
        return self._answer(size)

    def ctrl_transfer(self, request_type, request, value, index, data_or_length, timeout=None):
        if isinstance(data_or_length, int):
            self.events.append(["ctrl", request_type, request, value, index, data_or_length])
            return self._answer(data_or_length)
        self.events.append(["ctrl", request_type, request, value, index, _b64(data_or_length)])
        return len(data_or_length)


class _Interface:
    def __init__(self, info: Any) -> None:
        self.bInterfaceNumber = info.bInterfaceNumber
        self.bInterfaceClass = info.bInterfaceClass
        self._endpoints = info.endpoints

    def __iter__(self):
        return iter(self._endpoints)


class _Tagged(list):
    """The events of one of several screens, each beginning with its number."""

    def __init__(self, events: list[Any], tag: int) -> None:
        super().__init__()
        self.events, self.tag = events, tag

    def append(self, event: Any) -> None:
        self.events.append([self.tag, *event])


def _zc360_screens(case: dict[str, Any], events: list[Any], monkeypatch: pytest.MonkeyPatch):
    """The ZC-360's screens of the case as pyusb would list them, scripted each."""
    screens = []
    for n, part in enumerate(case["screens"]):
        screen = FakeScreen(part, _Tagged(events, n))
        screen.address, screen.port_numbers, screen.serial = (
            part["address"],
            part["ports"],
            part["serial"],
        )
        screens.append(screen)
    monkeypatch.setattr(jonsbo_zc360, "_devices", lambda: list(screens))
    monkeypatch.setattr(jonsbo_zc360, "_serial", lambda device: device.serial)
    monkeypatch.setattr(
        jonsbo_zc360.Screen, "claim", lambda self: self.device.events.append(["claim", [0]])
    )
    monkeypatch.setattr(
        jonsbo_zc360.Screen, "release", lambda self: self.device.events.append(["release"])
    )
    monkeypatch.setattr(jonsbo_zc360.time, "sleep", lambda seconds: None)


def _python_session(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    events: list[Any] = []
    if case["driver"] == "zc360":
        _zc360_screens(case, events, monkeypatch)
        case = {**case, "product": "", "interfaces": []}
    screen = FakeScreen(case, events)
    monkeypatch.setattr(
        usb_link,
        "take",
        lambda vendor, products, label: (screen, case.get("product_id", 0), "usb:1.2"),
    )
    monkeypatch.setattr(
        usb_link, "claim", lambda device, interfaces: events.append(["claim", list(interfaces)])
    )
    monkeypatch.setattr(
        usb_link, "release", lambda device, interfaces, where: events.append(["release"])
    )
    if case["driver"] == "lianli":
        _turzx_transport(screen, events, monkeypatch)
    options = case.get("options", {})
    config = DeviceConfig(driver="turzx")
    config.jpeg = options.get("jpeg", "4:4:4")
    config.jpeg_quality = options.get("jpeg_quality", 90)
    config.video.fps = options.get("fps", 30)
    config.brightness = options.get("brightness", 100)
    config.port = options.get("port", "")
    model = get_model(case["model"]) if case.get("model") else None
    display = _driver(case["driver"], config, model)
    try:
        display.open()
    except Exception as error:  # noqa: BLE001 (what Python raises, as it is)
        return {"error": str(error), "events": events}
    failed = None
    for step in case["steps"]:
        try:
            if "brightness" in step:
                display.set_brightness(step["brightness"])
            elif "jpeg" in step:
                display.set_jpeg(step["jpeg"])
            else:
                display.show(step["show"], step.get("region"))
        except Exception as error:  # noqa: BLE001
            failed = str(error)
            break
    model = display.model.id if display.model else None
    told = {"picture": display.picture, "jpeg_layout": display.jpeg_layout,
            "describe": display.describe(), "streaming": bool(display.streaming),
            "slow": bool(display.slow), "model": model}  # fmt: skip
    counters = display.counters
    display.close()
    return {"events": events, "error": failed, "bytes": counters.bytes,
            "usb_errors": counters.usb_errors, "told": told}  # fmt: skip


def _driver(name: str, config: DeviceConfig, model: Any) -> Any:
    if name == "udisp":
        return udisp.UdispDisplay(config, model)
    if name == "beada":
        return beadapanel.BeadaDisplay(config, model)
    if name == "thermaltake":
        return thermaltake.ThermaltakeDisplay(config, model)
    if name == "lianli":
        return lianli_usb.LianLiDisplay(config, model)
    if name == "thermalright":
        return thermalright.TrofeoDisplay(config, model)
    if name == "nzxt":
        return nzxt_kraken.KrakenDisplay(config, model)
    if name == "zc360":
        return jonsbo_zc360.Zc360Display(config, model)
    raise AssertionError(name)


class _Endpoint:
    """An endpoint of the fake screen, as turzx_usb.UsbTransport writes to and reads it."""

    def __init__(self, screen: FakeScreen, address: int) -> None:
        self.screen, self.bEndpointAddress = screen, address

    def write(self, data: bytes, timeout: int) -> int:
        return self.screen.write(self.bEndpointAddress, data, timeout)

    def read(self, size: int, timeout: int) -> bytes:
        return self.screen.read(self.bEndpointAddress, size, timeout)


def _turzx_transport(screen: FakeScreen, events: list[Any], monkeypatch: pytest.MonkeyPatch):
    """turzx_usb.UsbTransport on the fake screen (Lian Li's), as the Rust side claims it."""

    def opened(pid, beside=(), skip=0):
        events.append(["claim", [0]])
        out = next(e.bEndpointAddress for i in screen.config for e in i.endpoints
                   if not e.bEndpointAddress & 0x80)  # fmt: skip
        into = next(e.bEndpointAddress for i in screen.config for e in i.endpoints
                    if e.bEndpointAddress & 0x80)  # fmt: skip
        return turzx_usb.UsbTransport(screen, _Endpoint(screen, out), _Endpoint(screen, into),
                                      pid, "usb:1.2")  # fmt: skip

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(opened))
    monkeypatch.setattr(turzx_usb.UsbTransport, "close", lambda self: events.append(["release"]))
    monkeypatch.setattr(turzx_usb, "ms_since_midnight", lambda now=None: 12_345_678)


def _rust_case(case: dict[str, Any]) -> dict[str, Any]:
    steps = [{**s, "show": image(s["show"])} if "show" in s else s for s in case["steps"]]
    return {"op": "usb_session", **case, "steps": steps}


def _png_written(event: list[Any]) -> list[Any]:
    """A write of a TURZX command packet and a PNG (Lian Li's layer) as what it means: the
    command, the length its arguments name (the PNG's own) and the PNG's pixels. (Rust packs
    its PNGs with zlib-rs, not zlib: the same pixels, other bytes, as tests/test_rust_devices.py
    takes the TURZX panels' PNGs.)"""
    if event[0] != "write":
        return event
    data = base64.b64decode(event[2])
    if data[512:520] != b"\x89PNG\r\n\x1a\n":
        return event
    plain = turzx_usb.decode_packet(data[:512])
    payload = data[512:]
    assert int.from_bytes(plain[8:12], "big") == len(payload)
    with Image.open(io.BytesIO(payload)) as png:
        pixels = (png.mode, png.size, _b64(png.tobytes()))
    return ["write png", event[1], plain[0], list(pixels)]


def _compare(cases: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> int:
    """Each case on both sides, the same; how many opened."""
    opened = 0
    for n, (case, got) in enumerate(zip(cases, run([_rust_case(c) for c in cases]),
                                        strict=True)):  # fmt: skip
        want = _python_session(case, monkeypatch)
        assert got["error"] == want["error"], (n, case["product"])
        ours, theirs = ([_png_written(e) for e in side["events"]] for side in (got, want))
        assert ours == theirs, (n, case["product"])
        if "told" in want:
            opened += 1
            assert got["told"] == want["told"], n
            assert got["usb_errors"] == want["usb_errors"], n
            if case["driver"] != "lianli":  # (its PNG's bytes are counted)
                assert got["bytes"] == want["bytes"], n
    return opened


def _frame(rng: random.Random, sizes: list[tuple[int, int]]) -> Image.Image:
    size = rng.choice(sizes)
    mode = rng.choice(["RGB", "RGB", "RGBA", "L"])
    if rng.random() < 0.3:
        return noise(rng, mode, size)
    out = Image.linear_gradient("L").resize(size).convert(mode)
    tint = Image.new(mode, size, tuple(rng.randrange(256) for _ in mode) if mode != "L" else 99)
    return Image.blend(out, tint, rng.random())


def _udisp_case(rng: random.Random) -> dict[str, Any]:
    product = rng.choice([
        "t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32",
        "esp32s3_R480x480_Ergb16_Ejpg8_Fps20",
        "esp32s3_R480x480_Ergb32_Fps60",
        "esp32s3_R480x480_Ergb24",
        "esp32s3_R480x480_Ejpg5_Bl20",
        "esp32s3_R480x480_Ejpg5_Bl1",
        "esp32s3_R480x480",
        "esp32s3_R320x240_Ejpg5",
        "",
    ])  # fmt: skip
    endpoints = rng.choice([[[0x01, 512, False]], [[0x02, 64, False], [0x81, 64, False]],
                            [[0x81, 512, False]]])  # fmt: skip
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 5)):
        roll = rng.random()
        if roll < 0.25:
            steps.append({"brightness": rng.choice([-5, 0, 37, 100, 130, rng.randrange(100)])})
        elif roll < 0.35:
            steps.append({"jpeg": rng.choice(["4:2:0", "4:4:4", "4:1:1"])})
        else:
            frame = _frame(rng, [(480, 480), (320, 240), (240, 320), (1, 1), (481, 479)])
            steps.append({"show": frame, "region": None})
    options = {"jpeg": rng.choice(["4:4:4", "4:2:0", "nope"]),
               "jpeg_quality": rng.choice([50, 75, 90, 100]),
               "fps": rng.choice([10, 30, 60])}  # fmt: skip
    fail = [[rng.randrange(1, 4), 32, "Pipe error"]] if rng.random() < 0.15 else []
    return {"driver": "udisp", "model": rng.choice([None, "waveshare-2.8-udisp"]),
            "product": product, "interfaces": [{"number": 0, "class": 255,
                                                 "endpoints": endpoints}],
            "options": options, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_udisp_sessions_as_udisp_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(udisp, "AGAIN_S", 3600.0)  # (the keeper sends nothing in a case)
    rng = random.Random(0x0D15)
    assert _compare([_udisp_case(rng) for _ in range(40)], monkeypatch) > 15


def _beada_info(rng: random.Random, code: int, size: tuple[int, int], top: int, link: int) -> bytes:
    info = bytearray(80)
    info[0:2] = rng.randrange(600, 800).to_bytes(2, "little")
    info[2], info[4], info[5], info[78] = link, rng.randrange(4), code, top
    info[70:74] = size[0].to_bytes(2, "little") + size[1].to_bytes(2, "little")
    header = (
        b"STATUS-LINK" + bytes((1, 1, 0)) + (0).to_bytes(2, "little") + (100).to_bytes(2, "little")
    )
    return header + b"\0\0" + bytes(info)


def _beada_case(rng: random.Random) -> dict[str, Any]:
    code, size = rng.choice([(17, (480, 480)), (3, (320, 480)), (99, (480, 800)), (98, (123, 45)),
                             (13, (0, 480))])  # fmt: skip
    roll = rng.random()
    if roll < 0.1:
        answers: list[Any] = [None]
    elif roll < 0.15:
        answers = [_b64(b"STATUS-LINK" + bytes(10))]
    else:
        info = _beada_info(rng, code, size, rng.choice([0, 100, 255, 37]), rng.choice([1, 2]))
        answers = [_b64(info)]
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([-5, 0, 37, 50, 100, 130])})
        else:
            steps.append({"show": _frame(rng, [size if all(size) else (8, 8), (480, 320), (9, 7)]),
                          "region": None})  # fmt: skip
    fail = [[rng.randrange(1, 7), 5, "Input/Output Error"]] if rng.random() < 0.15 else []
    return {"driver": "beada", "model": None, "product": "BeadaPanel",
            "interfaces": [{"number": 0, "class": 255,
                            "endpoints": [[0x01, 512, False], [0x02, 512, False],
                                          [0x82, 512, False]]}],
            "answers": answers, "options": {"brightness": rng.choice([0, 40, 100])},
            "steps": steps, "fail_writes": fail}  # fmt: skip


def test_beadapanel_sessions_as_beadapanel_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beadapanel, "AGAIN_S", 3600.0)
    monkeypatch.setattr(beadapanel.time, "sleep", lambda seconds: None)
    rng = random.Random(0xBEAD)
    assert _compare([_beada_case(rng) for _ in range(30)], monkeypatch) > 10


def _thermaltake_case(rng: random.Random) -> dict[str, Any]:
    stale = [_b64(rng.randbytes(20)) for _ in range(rng.choice([0, 0, 2, 20]))]
    ok = [_b64(b"HTTP/1.1 200 OK"), _b64(b"200")]
    answers = [*stale, None, *(rng.choice([ok, ok, ok, [_b64(b"500")], [None]])), _b64(b"200")]
    endpoints = rng.choice([[[0x01, 1024, True], [0x81, 1024, True]], [[0x81, 1024, True]]])
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([0, 37, 100, 130])})
        else:
            steps.append({"show": _frame(rng, [(1480, 720), (720, 1480), (64, 48)]),
                          "region": None})  # fmt: skip
    fail = [[rng.randrange(3, 8), 19, "No such device"]] if rng.random() < 0.15 else []
    return {"driver": "thermaltake", "model": None, "product": "LCD",
            "interfaces": [{"number": 1, "class": 3, "endpoints": endpoints}],
            "answers": answers, "options": {}, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_thermaltake_sessions_as_thermaltake_py_drives_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(thermaltake, "AGAIN_S", 3600.0)
    monkeypatch.setattr(thermaltake.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(thermaltake.time, "time", lambda: 1_700_000_000.123)
    rng = random.Random(0x7A7E)
    assert _compare([_thermaltake_case(rng) for _ in range(24)], monkeypatch) > 8


def _lianli_case(rng: random.Random) -> dict[str, Any]:
    model = rng.choice(["lianli-hydroshift2-lcd", "lianli-hydroshift2-lcd", "lianli-universal-9.2"])
    reply = lambda level: _b64(bytes((101, 0xC8, 0, 0, 0, 0, 0, 0, level)) + rng.randbytes(4))  # noqa: E731
    answers: list[Any] = [_b64(b"stale"), None]  # (drained)
    answers += [_b64(rng.randbytes(16)) for _ in range(3)]  # 10, 123, 52
    answers += [rng.choice([None, reply(0)])]  # the PNG layer
    answers += [_b64(rng.randbytes(16))]  # 15
    size = (480, 480) if model.endswith("lcd") else (1920, 464)
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([-1, 0, 37, 100, 150])})
            answers.append(_b64(rng.randbytes(16)))
        else:
            steps.append({"show": _frame(rng, [size, (size[1], size[0]), (33, 21)]),
                          "region": None})  # fmt: skip
            level = rng.choice([None, 0, 2, 3, 4, 6])
            answers.append(None if level is None else reply(level))
            if level is not None and level > 3:  # asked with 122 until 2 or fewer wait
                answers += [_b64(bytes((122, 0, 0, 0, 0, 0, 0, 0, 5))), None,
                            _b64(bytes((122, 0, 0, 0, 0, 0, 0, 0, 1)))]  # fmt: skip
    if rng.random() < 0.1:
        answers = answers[:3]  # (it stops answering while it opens)
    fail = [[rng.randrange(2, 9), 32, "Pipe error"]] if rng.random() < 0.15 else []
    return {"driver": "lianli", "model": model, "product": "Lian Li",
            "interfaces": [{"number": 0, "class": 255,
                            "endpoints": [[0x01, 512, False], [0x81, 512, False]]}],
            "answers": answers, "options": {}, "steps": steps, "fail_writes": fail}  # fmt: skip


def test_lianli_sessions_as_lianli_usb_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(0x11A1)
    assert _compare([_lianli_case(rng) for _ in range(24)], monkeypatch) > 8


def _trofeo_case(rng: random.Random) -> dict[str, Any]:
    hid = rng.random() < 0.4
    steps: list[dict[str, Any]] = []
    answers: list[Any] = []
    if hid:
        said = bytearray(32)
        said[0:4], said[12] = thermalright.MAGIC, 1
        said[5], said[4] = rng.choice([0x80, 0x80, 0x81]), rng.choice([1, 2])
        answers = rng.choice([[_b64(said)], [_b64(bytes(30)), _b64(said)], [None, _b64(said)],
                              [None, None]])  # fmt: skip
        interfaces = [{"number": 0, "class": 3, "endpoints": rng.choice(
            [[[0x01, 512, True], [0x81, 512, True]], [[0x81, 512, True]]])}]  # fmt: skip
        sizes = [(1280, 480), (480, 1280), (40, 30)]
    else:
        said = bytearray(512)
        said[0:2], said[8] = b"\x03\xff", 1
        said[20], said[22] = rng.choice([1, 3, 4, 5, 9]), rng.choice([0, 1, 3])
        answers = [rng.choice([_b64(said), _b64(said), None, _b64(bytes(30))])]
        interfaces = [{"number": 0, "class": 255,
                       "endpoints": [[0x01, 512, False], [0x81, 512, False]]}]  # fmt: skip
        sizes = [(1920, 462), (1920, 440), (462, 1920), (50, 20)]
    for _ in range(rng.randrange(1, 8)):
        if rng.random() < 0.25:
            steps.append({"brightness": rng.choice([0, 33, 100, 140])})
        else:
            steps.append({"show": _frame(rng, sizes), "region": None})
        if not hid:  # (the bar's answer to each picture)
            took = bytes((3, 0xFF, 0, 0, 0, 0, 0, 0, 1))
            answers.append(rng.choice([_b64(took), _b64(took), None]))
    fail = [[rng.randrange(2, 6), 19, "No such device"]] if rng.random() < 0.15 else []
    return {"driver": "thermalright", "model": None, "product": "Trofeo",
            "product_id": thermalright.HID_PRODUCT if hid else thermalright.BULK_PRODUCT,
            "interfaces": interfaces, "answers": answers, "options": {}, "steps": steps,
            "fail_writes": fail}  # fmt: skip


def test_trofeo_sessions_as_thermalright_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(thermalright, "AGAIN_S", {thermalright.HID_PRODUCT: 3600.0,
                                                  thermalright.BULK_PRODUCT: 3600.0})  # fmt: skip
    rng = random.Random(0x7709)
    assert _compare([_trofeo_case(rng) for _ in range(40)], monkeypatch) > 12


def _kraken_answer(asked: tuple[int, int], **at: int) -> str:
    answer = bytearray(64)
    answer[0], answer[1] = asked[0] + 1, asked[1]
    for place, value in at.items():
        answer[int(place[1:])] = value
    return _b64(answer)


def _kraken_case(rng: random.Random) -> dict[str, Any]:
    product, firmware = rng.choice([(nzxt_kraken.Z3, 1), (nzxt_kraken.KRAKEN_2023, 2),
                                    (nzxt_kraken.KRAKEN_2023, 1), (0x3014, 1)])  # fmt: skip
    size = {nzxt_kraken.Z3: 320}.get(product, 240)
    direct = product == nzxt_kraken.KRAKEN_2023 and firmware == 2
    status = _b64(bytes((0x75, 0x02)) + rng.randbytes(30))  # (the cooler's own report)

    def asked(report: tuple[int, int], **at: int) -> list[str]:
        return ([status] if rng.random() < 0.2 else []) + [_kraken_answer(report, **at)]

    answers: list[Any] = [status] * rng.randrange(3) + [None]  # (drained)
    answers += asked((0x10, 0x01), b17=firmware, b18=rng.randrange(9), b19=rng.randrange(9))
    answers += asked((0x30, 0x01), b26=rng.randrange(8))
    if not direct:
        answers += asked((0x38, 0x01)) + asked((0x32, 0x02)) + asked((0x32, 0x02))
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.3:
            steps.append({"brightness": rng.choice([-3, 0, 45, 100, 120])})
            continue
        steps.append({"show": _frame(rng, [(size, size), (64, 48), (size + 3, size)]),
                      "region": None})  # fmt: skip
        if direct:
            times = 2 if not any("show" in s for s in steps[:-1]) else 1
            answers += [rng.choice([status, None]) for _ in range(2 * times)]
        else:
            taken = rng.choice([1, 1, 0])
            answers += [rng.choice([status, None])]  # 36 03
            answers += asked((0x32, 0x02), b14=taken)
            if taken:
                answers += asked((0x32, 0x02), b14=0)
            answers += asked((0x32, 0x01), b14=rng.choice([1, 0]))
            answers += [rng.choice([status, None])]  # 36 01
            answers += asked((0x38, 0x01), b14=1)
    fail = [[rng.randrange(4, 12), 19, "No such device"]] if rng.random() < 0.15 else []
    endpoints = rng.choice([[[0x01, 64, True], [0x81, 64, True]], [[0x81, 64, True]]])
    return {"driver": "nzxt", "model": None, "product": "Kraken", "product_id": product,
            "interfaces": [{"number": 0, "class": 255, "endpoints": [[0x02, 512, False]]},
                           {"number": 1, "class": 3, "endpoints": endpoints}],
            "answers": answers, "options": {"brightness": rng.choice([0, 60, 100])},
            "steps": steps, "fail_writes": fail}  # fmt: skip


def test_kraken_sessions_as_nzxt_kraken_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nzxt_kraken, "_linux", lambda: False)  # (the HID interface itself)
    monkeypatch.setattr(nzxt_kraken.time, "sleep", lambda seconds: None)
    rng = random.Random(0x2A3)
    assert _compare([_kraken_case(rng) for _ in range(24)], monkeypatch) > 10


def _zc360_answer(state: int) -> str:
    """A controller's answer saying ``state``."""
    from Crypto.Cipher import DES

    answer = bytearray(32)
    answer[2:10] = DES.new(jonsbo_zc360.DES_KEY, DES.MODE_ECB).encrypt(
        bytes((state, 0, 0, 0, 0, 0, 0, 0))
    )
    return _b64(answer)


def _zc360_case(rng: random.Random) -> dict[str, Any]:
    single = rng.random() < 0.35
    count = rng.choice([3, 3, 3, 4, 2]) if not single else rng.choice([1, 2, 3])
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 4)):
        if rng.random() < 0.25:
            steps.append({"brightness": rng.choice([-1, 0, 50, 100, 120])})
        else:
            sizes = (
                [(640, 180), (180, 640), (300, 90)]
                if single
                else [(1920, 180), (180, 1920), (960, 90)]
            )
            frame = _frame(rng, sizes)
            steps.append({"show": frame, "region": None})
            if rng.random() < 0.3:
                steps.append({"show": frame, "region": None})  # (the same again: not written)
    states = [0x62, 0x62, 0x60, 0x59, None]
    screens = []
    for n in range(count):
        answers = [rng.choice([_zc360_answer(0x10), None]) for _ in range(3)]
        answers += [None if (state := rng.choice(states)) is None else _zc360_answer(state)
                    for _ in range(40)]  # fmt: skip
        fail = [[rng.randrange(1, 60), 19, "No such device"]] if rng.random() < 0.08 else []
        screens.append({"product": "TURZX-338inch-r", "serial": rng.choice(["A1", "B2", "C3", ""]),
                        "address": 10 + n, "ports": [1, rng.randrange(1, 5)],
                        "interfaces": [{"number": 0, "class": 255,
                                        "endpoints": [[0x02, 512, False], [0x82, 512, False]]}],
                        "answers": answers, "fail_writes": fail})  # fmt: skip
    port = rng.choice(["", "2,1,3", "312", "1", "3", "4", "nope"])
    return {"driver": "zc360", "product": "ZC-360",
            "model": "jonsbo-zc360-screen" if single else rng.choice([None, "jonsbo-zc360"]),
            "screens": screens, "options": {"brightness": rng.choice([0, 70, 100]), "port": port},
            "steps": steps}  # fmt: skip


def test_zc360_sessions_as_jonsbo_zc360_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(0x2C36)
    assert _compare([_zc360_case(rng) for _ in range(30)], monkeypatch) > 12

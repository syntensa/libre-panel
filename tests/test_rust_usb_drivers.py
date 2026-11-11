"""The drivers of the USB screens of other makers in Rust (rust/devices: udisp ...) against
those of devices/, to the byte: each opened on a screen the test scripts (its product string,
its interfaces, what it answers each read), frames shown, the brightness set, the JPEGs' layout
changed, closed; what went to the screen, in order, and what the driver says of itself."""

from __future__ import annotations

import base64
import random
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest
import usb.core
from PIL import Image
from rust_check import image, noise, run

from libre_panel.config import DeviceConfig
from libre_panel.devices import udisp, usb_link
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


def _python_session(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    events: list[Any] = []
    screen = FakeScreen(case, events)
    monkeypatch.setattr(usb_link, "take", lambda vendor, products, label: (screen, 0, "usb:1.2"))
    monkeypatch.setattr(
        usb_link, "claim", lambda device, interfaces: events.append(["claim", list(interfaces)])
    )
    monkeypatch.setattr(
        usb_link, "release", lambda device, interfaces, where: events.append(["release"])
    )
    options = case.get("options", {})
    config = DeviceConfig(driver="turzx")
    config.jpeg = options.get("jpeg", "4:4:4")
    config.jpeg_quality = options.get("jpeg_quality", 90)
    config.video.fps = options.get("fps", 30)
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
    raise AssertionError(name)


def _rust_case(case: dict[str, Any]) -> dict[str, Any]:
    steps = [{**s, "show": image(s["show"])} if "show" in s else s for s in case["steps"]]
    return {"op": "usb_session", **case, "steps": steps}


def _compare(cases: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> int:
    """Each case on both sides, the same; how many opened."""
    opened = 0
    for n, (case, got) in enumerate(zip(cases, run([_rust_case(c) for c in cases]),
                                        strict=True)):  # fmt: skip
        want = _python_session(case, monkeypatch)
        assert got["error"] == want["error"], (n, case["product"])
        assert got["events"] == want["events"], (n, case["product"])
        if "told" in want:
            opened += 1
            assert got["told"] == want["told"], n
            assert (got["bytes"], got["usb_errors"]) == (want["bytes"], want["usb_errors"]), n
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

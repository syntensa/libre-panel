"""The serial panels' drivers in Rust (rust/devices' serial_panel: Turing rev. A, XuanFang rev. B,
Kipye rev. D, WeAct) against those of devices/, to the byte: each opened on a port the test
scripts (what the panel answers, read as the driver asks for it), frames shown whole and in
parts, of the panel's size and of another, turned, the brightness set, closed; what went to the
port, in order, and what the driver says of itself."""

from __future__ import annotations

import base64
import random
from typing import Any

import pytest
import serial
from rust_check import image, run
from test_rust_usb_drivers import _b64, _frame

from libre_panel.config import DeviceConfig
from libre_panel.devices import (
    kipye_rev_d,
    serial_link,
    serial_panel,
    turing_rev_a,
    turing_rev_b,
    weact,
)
from libre_panel.devices.models import get_model
from libre_panel.devices.serial_link import FoundPort


class FakePort:
    """pyserial's port, scripted as the Rust side's (check/src/usb_drivers.rs ScriptedLink)."""

    def __init__(self, case: dict[str, Any], events: list[Any]) -> None:
        self.events = events
        self.incoming = bytearray(base64.b64decode(case.get("incoming", "")))
        self.fail_writes = {n: text for n, text in case.get("fail_writes", [])}
        self.writes = 0

    def write(self, data: bytes) -> int:
        self.writes += 1
        self.events.append(["write", _b64(data)])
        if self.writes in self.fail_writes:
            raise serial.SerialException(self.fail_writes[self.writes])
        return len(data)

    def read(self, size: int) -> bytes:
        self.events.append(["read", size])
        out = bytes(self.incoming[:size])
        del self.incoming[:size]
        return out

    def reset_input_buffer(self) -> None:
        self.events.append(["drain"])

    def close(self) -> None:
        self.events.append(["close"])


DRIVERS = {
    "rev_a": turing_rev_a.RevADisplay,
    "rev_b": turing_rev_b.RevBDisplay,
    "rev_d": kipye_rev_d.RevDDisplay,
    "weact": weact.WeActDisplay,
}


def _python_session(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    events: list[Any] = []
    found = FoundPort(case["found"]["device"], get_model(case["found"]["model"]),
                      case["found"].get("serial"))  # fmt: skip
    monkeypatch.setattr(serial_panel, "find_port", lambda model, port: found)
    monkeypatch.setattr(serial_link, "open_serial",
                        lambda device, baud, rtscts: FakePort(case, events))  # fmt: skip
    monkeypatch.setattr(turing_rev_b.RevBDisplay, "sleep", staticmethod(lambda seconds: None))
    model = get_model(case["model"]) if case.get("model") else None
    display = DRIVERS[case["driver"]](DeviceConfig(driver="turzx"), model,
                                      configured=case.get("configured", True))  # fmt: skip
    try:
        display.open()
    except Exception as error:  # noqa: BLE001 (what Python raises, as it is)
        return {"error": str(error), "events": events}
    failed = None
    for step in case["steps"]:
        try:
            if "brightness" in step:
                display.set_brightness(step["brightness"])
            else:
                display.show(step["show"], step.get("region"))
        except Exception as error:  # noqa: BLE001
            failed = str(error)
            break
    told = {"picture": display.picture, "jpeg_layout": display.jpeg_layout,
            "describe": display.describe(), "streaming": bool(display.streaming),
            "slow": bool(display.slow),
            "model": display.model.id if display.model else None}  # fmt: skip
    counters = display.counters
    display.close()
    return {"events": events, "error": failed, "bytes": counters.bytes,
            "usb_errors": counters.usb_errors, "told": told}  # fmt: skip


def _rust_case(case: dict[str, Any]) -> dict[str, Any]:
    steps = [{**s, "show": image(s["show"])} if "show" in s else s for s in case["steps"]]
    return {"op": "usb_session", **case, "steps": steps}


def _compare(cases: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> int:
    opened = 0
    for n, (case, got) in enumerate(zip(cases, run([_rust_case(c) for c in cases]),
                                        strict=True)):  # fmt: skip
        want = _python_session(case, monkeypatch)
        assert got["error"] == want["error"], (n, case["driver"])
        assert got["events"] == want["events"], (n, case["driver"])
        if "told" in want:
            opened += 1
            assert got["told"] == want["told"], n
            assert (got["bytes"], got["usb_errors"]) == (want["bytes"], want["usb_errors"]), n
    return opened


SIZES = {"turing-3.5": (320, 480), "usbpcmonitor-5": (480, 800), "usbpcmonitor-7": (600, 1024),
         "xuanfang-3.5": (320, 480), "kipye-3.5": (320, 480), "weact-3.5": (320, 480),
         "weact-0.96": (80, 160)}  # fmt: skip


def _case(rng: random.Random, driver: str) -> dict[str, Any]:
    port_model, answers = {
        "rev_a": (rng.choice(["turing-3.5", "usbpcmonitor-5"]),
                  [b"", bytes([1] * 6), bytes([2] * 6), bytes([3] * 6), b"\x07\x01"]),
        "rev_b": ("xuanfang-3.5", [b"", b"\xcaHELLO\x0a\x01\x00\xca", b"\xcaHELLO\x0a\x11\x00\xca",
                                   b"\xcaHELLO\x0a\x12\x00\xca", b"\x01\x02\x03"]),
        "rev_d": ("kipye-3.5", [b"", rng.randbytes(40)]),
        "weact": (rng.choice(["weact-3.5", "weact-0.96"]), [b"", rng.randbytes(19)]),
    }[driver]  # fmt: skip
    configured = rng.random() < 0.5
    model = port_model if configured else None
    serial_number = rng.choice([None, "AB1234", "AD99", "XX"]) if driver == "weact" else None
    w, h = SIZES[port_model]
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(1, 5)):
        roll = rng.random()
        if roll < 0.25:
            steps.append({"brightness": rng.choice([-10, 0, 1, 37, 99, 100, 150])})
            continue
        size = rng.choice([(w, h), (h, w), (w, h), (w // 2 + 1, h // 3 + 1)])
        region = None
        if roll < 0.7:
            x0, y0 = rng.randrange(size[0]), rng.randrange(size[1])
            region = [x0, y0, rng.randrange(x0, size[0] + 1), rng.randrange(y0, size[1] + 1)]
        steps.append({"show": _frame(rng, [size]), "region": region})
    fail = [[rng.randrange(1, 12), "write timeout"]] if rng.random() < 0.15 else []
    return {"driver": driver, "model": model, "configured": configured,
            "found": {"device": "COM9", "model": port_model, "serial": serial_number},
            "incoming": _b64(rng.choice(answers)), "steps": steps,
            "fail_writes": fail}  # fmt: skip


@pytest.mark.parametrize("driver", list(DRIVERS))
def test_serial_sessions_as_the_python_drivers_drive_them(driver, monkeypatch) -> None:
    rng = random.Random(f"serial-{driver}")
    assert _compare([_case(rng, driver) for _ in range(20)], monkeypatch) > 5

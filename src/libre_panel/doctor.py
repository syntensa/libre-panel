"""``libre-panel doctor``: check a connected panel end to end.

Runs only display commands (sync, brightness, frames): the same ones normal
operation uses. Shows test cards for both orientations, asks what the panel
shows, measures speed and writes a report without personal data (no serial
numbers, user names or paths) that can be attached to an issue.
"""

from __future__ import annotations

import platform
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from libre_panel import __version__
from libre_panel.devices.base import DeviceError, list_serial_ports
from libre_panel.devices.models import PanelModel, models_for_usb

ISSUE_URL = "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml"


@dataclass
class Step:
    name: str
    result: str  # "ok", "fail", "skip", "info"
    detail: str = ""


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)
    model: PanelModel | None = None

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(s.result in ("ok", "info") for s in self.steps)

    @property
    def failed(self) -> bool:
        return not self.steps or any(s.result == "fail" for s in self.steps)

    @property
    def summary(self) -> str:
        if self.passed:
            return "all checks passed"
        if self.failed:
            return "problems found"
        return "no problems found, but the visual checks were skipped"

    def add(self, name: str, result: str, detail: str = "") -> Step:
        step = Step(name, result, detail)
        self.steps.append(step)
        return step

    def text(self) -> str:
        lines = [
            f"Libre Panel doctor report — {datetime.now():%Y-%m-%d %H:%M}",
            f"libre-panel {__version__}, Python {platform.python_version()}, "
            f"{platform.system()} {platform.release()} ({platform.machine()})",
            f"panel: {self.model.id + ' — ' + self.model.label if self.model else 'none found'}",
            "",
        ]
        for step in self.steps:
            lines.append(f"[{step.result.upper():>4}] {step.name}")
            if step.detail:
                lines.extend(f"       {line}" for line in step.detail.splitlines())
        lines += ["", f"RESULT: {self.summary}"]
        return "\n".join(lines) + "\n"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(max(8, size))


def test_card(
    width: int, height: int, title: str, subtitle: str, round_panel: bool = False
) -> Image.Image:
    """A card that makes cut-off edges, rotation, mirroring and colour mix-ups obvious."""
    img = Image.new("RGB", (width, height), "black")
    d = ImageDraw.Draw(img)
    unit = max(8, min(width, height) // 12)
    if round_panel:
        d.ellipse([0, 0, width - 1, height - 1], outline="white", width=4)
    else:
        d.rectangle([0, 0, width - 1, height - 1], outline="white", width=4)
        d.rectangle([10, 10, width - 11, height - 11], outline="#ffd400", width=2)
    small = _font(max(8, unit // 2))
    inset = unit if not round_panel else int(min(width, height) * 0.2)
    if round_panel:
        # Corners do not exist on a round panel: label the edges instead.
        labels = {
            "LEFT": ((inset // 2, height // 2 - unit), "lm"),
            "RIGHT": ((width - inset // 2, height // 2 - unit), "rm"),
            "BOTTOM": ((width // 2, height - inset // 2), "md"),
        }
    else:
        labels = {
            "TOP LEFT": ((inset, inset), "la"),
            "TOP RIGHT": ((width - inset, inset), "ra"),
            "BOTTOM LEFT": ((inset, height - inset), "ld"),
            "BOTTOM RIGHT": ((width - inset, height - inset), "rd"),
        }
    for label, (xy, anchor) in labels.items():
        d.text(xy, label, font=small, fill="white", anchor=anchor)
    # Arrow pointing to the top edge.
    cx = width // 2
    top = inset
    d.polygon(
        [(cx, top), (cx - unit // 2, top + unit // 2), (cx + unit // 2, top + unit // 2)],
        fill="white",
    )
    d.text((cx, top + unit // 2 + 4), "UP", font=small, fill="white", anchor="ma")
    # Title
    d.text((cx, height // 2 - unit), title, font=_font(unit), fill="white", anchor="mm")
    d.text((cx, height // 2), subtitle, font=small, fill="#cbd5e1", anchor="mm")
    # Colour boxes with their names
    names = [("RED", "#ff0000"), ("GREEN", "#00ff00"), ("BLUE", "#0000ff"), ("WHITE", "#ffffff")]
    box = max(6, unit // 2)
    gap = box * 3
    x0 = cx - (len(names) * gap) // 2 + gap // 2
    y0 = height // 2 + unit // 2
    for i, (name, color) in enumerate(names):
        x = x0 + i * gap
        d.rectangle([x - box // 2, y0, x + box // 2, y0 + box], fill=color)
        d.text((x, y0 + box + 2), name, font=_font(max(8, unit // 3)), fill="white", anchor="ma")
    # Grey ramp: the darkest steps show how deep the panel's black goes.
    steps = 16
    ramp_w = min(width - 2 * inset, steps * unit)
    ramp_y = y0 + box + unit
    if ramp_y + unit // 2 < height - inset:
        for i in range(steps):
            v = round(i * 255 / (steps - 1))
            x = cx - ramp_w // 2 + i * ramp_w // steps
            d.rectangle([x, ramp_y, x + ramp_w // steps - 1, ramp_y + unit // 2], fill=(v, v, v))
    return img


def _versions() -> str:
    parts = []
    for dist in ("pillow", "pyusb", "pycryptodome", "libusb-package", "pyserial"):
        try:
            parts.append(f"{dist} {version(dist)}")
        except PackageNotFoundError:
            parts.append(f"{dist} missing")
    return ", ".join(parts)


class Doctor:
    def __init__(
        self,
        ask: Callable[[str], str] | None = None,
        say: Callable[[str], None] = print,
        open_transport: Callable[..., object] | None = None,
        pause: Callable[[float], None] = time.sleep,
        seconds_per_card: float = 6.0,
        frames: int = 20,
    ) -> None:
        self.ask = ask
        self.say = say
        self.pause = pause
        self.seconds_per_card = seconds_per_card
        self.frames = frames
        if open_transport is None:
            from libre_panel.devices.turzx_usb import UsbTransport

            open_transport = UsbTransport.open
        self.open_transport = open_transport
        self.transport = None
        self.report = Report()

    # -- helpers -----------------------------------------------------------

    def _step(self, name: str, result: str, detail: str = "") -> None:
        self.report.add(name, result, detail)
        mark = {"ok": "ok", "fail": "FAIL", "skip": "skip", "info": "info"}[result]
        self.say(f"[{mark:>4}] {name}" + (f" — {detail}" if detail else ""))

    def _question(self, name: str, question: str) -> None:
        if self.ask is None:
            self.pause(self.seconds_per_card)
            self._step(name, "skip", "not checked (no questions asked)")
            return
        answer = self.ask(f"{question} [y/n] ").strip().lower()
        if answer.startswith(("y", "j")):
            self._step(name, "ok", "confirmed by looking at the panel")
        else:
            note = self.ask("What do you see instead? (Enter to skip) ").strip()
            self._step(name, "fail", note or "not as expected")

    def _send(self, image: Image.Image) -> tuple[float, int]:
        from libre_panel.devices.turzx_usb import CMD_UPLOAD_PNG, encode_frame, to_native

        png = encode_frame(to_native(image))
        started = time.perf_counter()
        self.transport.command(CMD_UPLOAD_PNG, struct.pack(">I", len(png)), png, timeout_ms=5000)
        return time.perf_counter() - started, len(png)

    # -- the checks --------------------------------------------------------

    def run(self) -> Report:
        self._step("environment", "info", _versions())
        self.transport = self._find_and_open()
        if self.transport is None:
            return self.report
        try:
            self._checks()
        except DeviceError as exc:
            self._step("panel stopped answering", "fail", str(exc))
        finally:
            if self.transport is not None:
                self.transport.close()
        return self.report

    def _find_and_open(self):
        try:
            transport = self.open_transport()
        except DeviceError as exc:
            self._step("find and open a USB panel (VID 1CBE)", "fail", str(exc))
            self._report_serial_ports()
            return None
        matches = models_for_usb(0x1CBE, transport.pid)
        self.report.model = matches[0] if matches else None
        label = self.report.model.label if self.report.model else "unknown size"
        self._step("find and open a USB panel", "ok", f"1cbe:{transport.pid:04x} — {label}")
        return transport

    def _report_serial_ports(self) -> None:
        try:
            ports = list_serial_ports()
        except DeviceError:
            return
        for port in ports:
            if not port["vid"]:
                continue
            models = models_for_usb(
                int(port["vid"], 16), int(port["pid"], 16), port["serial_number"]
            )
            if models:
                names = ", ".join(m.id for m in models)
                self._step(
                    "serial panel found",
                    "info",
                    f"{port['device']} {port['vid']}:{port['pid']} — {names}; "
                    "the driver for serial panels is not available yet",
                )

    def _checks(self) -> None:
        from libre_panel.devices.turzx_usb import CMD_BRIGHTNESS, brightness_arg

        transport = self.transport
        dropped = transport.drain()
        reply = transport.sync()
        ident = reply[2:10].split(b"\x00")[0].decode("ascii", "replace")
        detail = f"reply {reply[:2].hex(' ')} '{ident}'"
        if dropped:
            detail += f", cleared {dropped} stale replies"
        self._step("handshake (command 10)", "ok", detail)

        model = self.report.model
        if model is None:
            self._step("panel size", "fail", f"unknown product id {transport.pid:04x}")
            return
        round_panel = model.shape == "round"
        orientations = ["landscape"] if round_panel else ["landscape", "portrait"]
        for n, orientation in enumerate(orientations, 1):
            w, h = model.size(orientation)
            subtitle = f"{model.label} · {w}x{h} {orientation} · card {n}"
            self._send(test_card(w, h, "Libre Panel", subtitle, round_panel))
            self.say(f"\nThe panel should now show test card {n} ({orientation}).")
            self._question(
                f"test card, {orientation}",
                "Is the white frame visible on all four edges, is 'UP' at the top edge, "
                "is the text readable (not mirrored) and are RED/GREEN/BLUE the right colours?",
            )

        for percent in (10, 100):
            transport.command(CMD_BRIGHTNESS, bytes([brightness_arg(percent)]))
            self.pause(1.5)
        transport.command(CMD_BRIGHTNESS, bytes([brightness_arg(60)]))
        self._question("brightness (command 14)", "Did the panel go dark and then bright?")

        w, h = model.size("landscape")
        cards = [
            test_card(w, h, "Libre Panel", f"speed test · frame {i + 1}", round_panel)
            for i in range(2)
        ]
        times, sizes = [], []
        for i in range(self.frames):
            seconds, size = self._send(cards[i % 2])
            times.append(seconds)
            sizes.append(size)
        if times:
            avg = sum(times) / len(times)
            self._step(
                f"speed ({len(times)} full frames)",
                "ok",
                f"{avg * 1000:.0f} ms per frame = {1 / avg:.1f} fps, "
                f"worst {max(times) * 1000:.0f} ms, PNG {sum(sizes) // len(sizes) // 1024} KB",
            )

        pid = transport.pid
        transport.close()
        self.transport = None
        self.transport = self.open_transport(pid)
        self.transport.sync()
        self._step("close and reconnect", "ok")
        self._send(test_card(w, h, "Test finished", "you can close this window", round_panel))


def run_doctor(
    report_path: Path | None = None, ask_questions: bool = True, frames: int = 20
) -> Report:
    ask = input if ask_questions and sys.stdin.isatty() else None
    print("Libre Panel doctor — close the TURZX app before starting.\n")
    doctor = Doctor(ask=ask, frames=frames)
    report = doctor.run()
    path = report_path or Path(
        f"libre-panel-doctor-{report.model.id if report.model else 'no-panel'}.txt"
    )
    path.write_text(report.text(), encoding="utf-8")
    print()
    print(f"Result: {report.summary}.")
    if not report.passed and not report.failed:
        print("Run `libre-panel doctor` in a terminal to answer the visual checks.")
    print(f"Report: {path.resolve()}")
    print(f"Please attach it to a hardware report: {ISSUE_URL}")
    return report

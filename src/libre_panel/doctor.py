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
from libre_panel.config import ConfigError, load_config
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
    # Pixels the glass hides per edge (landscape), from the ruler card.
    hidden: dict[str, int | None] = field(default_factory=dict)

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


RULER_EDGES = ("top", "bottom", "left", "right")


RULER_COLOR = (255, 212, 0)


def ruler_card(width: int, height: int, step: int = 2, depth: int = 40) -> Image.Image:
    """Numbered bars that start at each edge, each as deep as its number.

    Bar ``k`` covers the ``k`` pixels next to the edge, so glass that hides
    ``k`` pixels or more hides it completely: the smallest number whose bar
    shows is one step more than the hidden strip. The numbers sit well inside
    the card, big enough to read at arm's length.
    """
    img = Image.new("RGB", (width, height), "black")
    d = ImageDraw.Draw(img)
    size = max(12, min(20, min(width, height) // 20))
    font = _font(size)
    inset = max(45, depth + 12)  # where the numbers start
    thick = max(4, size // 3)  # of a bar, along the edge
    shifts = {True: round(size * 1.2), False: round(size * 1.8)}  # to a second row of numbers
    # keep clear of the numbers of the rulers across (two rows at most)
    clear = {
        True: inset + shifts[False] + round(size * 1.6) + size // 2,
        False: inset + shifts[True] + size + size // 2,
    }
    for edge in RULER_EDGES:
        horizontal = edge in ("top", "bottom")
        along = width if horizontal else height
        margin = min(clear[horizontal], along // 4)
        need = size * 1.6 if horizontal else size * 1.1  # a number's width or height
        marks = list(range(step, depth + 1, step))
        while True:  # one row of numbers, two staggered rows, or fewer bars
            room = (along - 2 * margin) / len(marks)
            rows = 1 if room >= need else 2
            if room * rows >= need or len(marks) <= 2:
                break
            marks = marks[1::2]
        shift = shifts[horizontal]
        for i, k in enumerate(marks):
            pos = round(margin + (i + 0.5) * room)
            a, b = pos - thick // 2, pos - thick // 2 + thick - 1
            label = inset + (i % rows) * shift
            if edge == "top":
                d.rectangle([a, 0, b, k - 1], fill=RULER_COLOR)
                d.text((pos, label), str(k), font=font, fill="white", anchor="ma")
            elif edge == "bottom":
                d.rectangle([a, height - k, b, height - 1], fill=RULER_COLOR)
                d.text((pos, height - label), str(k), font=font, fill="white", anchor="md")
            elif edge == "left":
                d.rectangle([0, a, k - 1, b], fill=RULER_COLOR)
                d.text((label, pos), str(k), font=font, fill="white", anchor="lm")
            else:
                d.rectangle([width - k, a, width - 1, b], fill=RULER_COLOR)
                d.text((width - label, pos), str(k), font=font, fill="white", anchor="rm")
    # The title and hint go between the side rulers' numbers, where they fit
    # (the terminal asks the same question).
    free = width - 2 * (inset + round(size * 1.8) + 2 * size)
    cx, cy = width // 2, height // 2
    hint = "at each edge: the smallest number whose yellow bar you can see"
    for text, big, y, fill in (
        ("RULER", size * 3 // 2, cy - size, "white"),
        (hint, size, cy + size, "#cbd5e1"),
    ):
        for points in range(big, 9, -1):
            if d.textlength(text, font=_font(points)) <= free:
                d.text((cx, y), text, font=_font(points), fill=fill, anchor="mm")
                break
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
        brightness: int = 60,
    ) -> None:
        self.ask = ask
        self.brightness = brightness  # set again after the brightness check
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

    def _ruler(self, model, step: int = 2) -> None:
        """Measure the strip the glass hides at each edge (landscape)."""
        w, h = model.size("landscape")
        self._send(ruler_card(w, h, step=step))
        self.say("\nThe panel should now show the RULER card (landscape).")
        if self.ask is None:
            self.pause(self.seconds_per_card)
            self._step("hidden edges (ruler)", "skip", "not measured (no questions asked)")
            return
        hidden = {}
        for edge in RULER_EDGES:
            answer = self.ask(
                f"{edge.capitalize()} edge: smallest number whose yellow bar you can see "
                "(Enter = cannot tell)? "
            ).strip()
            # bar k shows, bar k - step does not: the glass hides k - step pixels (up to k - 1)
            hidden[edge] = max(0, int(answer) - step) if answer.isdigit() else None
        parts = [f"{edge} {'?' if v is None else f'{v} px'}" for edge, v in hidden.items()]
        self.report.hidden = hidden
        self._step("hidden edges (ruler)", "info", "hidden: " + ", ".join(parts))

    def _checks(self) -> None:
        from libre_panel.devices.turzx_usb import CMD_BRIGHTNESS, brightness_arg

        transport = self.transport
        dropped = transport.drain()
        reply = transport.sync()
        # (the bytes after the first two change with every connection)
        detail = f"reply {reply[:2].hex(' ')}"
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

        if not round_panel:
            self._ruler(model)

        if self.ask is not None:
            self.ask("\nBrightness: the panel goes dark, then bright. Watch it and press Enter. ")
        for percent in (10, 100):
            transport.command(CMD_BRIGHTNESS, bytes([brightness_arg(percent)]))
            self.pause(1.5)
        transport.command(CMD_BRIGHTNESS, bytes([brightness_arg(self.brightness)]))
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
    report_path: Path | None = None,
    ask_questions: bool = True,
    frames: int = 20,
    config_path: Path | None = None,
) -> Report:
    ask = input if ask_questions and sys.stdin.isatty() else None
    print("Libre Panel doctor — close the TURZX app before starting.\n")
    try:
        brightness = load_config(config_path).device.brightness
    except ConfigError:
        brightness = 60
    doctor = Doctor(ask=ask, frames=frames, brightness=brightness)
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

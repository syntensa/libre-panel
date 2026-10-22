"""``libre-panel doctor``: check a connected panel end to end.

Runs only display commands (sync, brightness, frames): the same ones normal
operation uses. Shows test cards for both orientations, asks what the panel
shows, measures speed and writes a report without personal data (no serial
numbers, user names or paths) that can be attached to an issue. On Windows it
also asks LibreHardwareMonitor (inside Libre Panel, else its web server) once, to say
whether the temperatures and power that only it gives arrive.
"""

from __future__ import annotations

import importlib
import os
import platform
import re
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from libre_panel import __version__
from libre_panel.config import Config, ConfigError, VideoConfig, load_config
from libre_panel.devices.base import DeviceError, list_serial_ports
from libre_panel.devices.models import PanelModel, models_for_usb

ISSUE_URL = "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml"
# The folders of a Windows path (C:\..., file:///C:/..., \\server\share\...) up to its last part
_FOLDERS = re.compile(
    r"(?:file:/*)?(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\)(?:[^\\/:*?\"<>|;\r\n]+[\\/])*"
)


def without_folders(text: str, home: str | None = None) -> str:
    """``text`` for the report: what LibreHardwareMonitor and .NET say names a file with its
    folders, and a user's own folders hold the user's name. The home folder becomes ``~``, the
    folders of any other path ``…``; the file a path names stays."""
    home = os.path.expanduser("~") if home is None else home  # "~" itself where there is none
    if len(home) > 3:  # not a bare "/" or "C:\"
        for spelt in {home, home.replace("\\", "/")}:
            text = re.sub(re.escape(spelt), "~", text, flags=re.IGNORECASE)
    return _FOLDERS.sub("…\\\\", text)


def sensors_check(
    config_path: Path | None = None,
    platform: str = sys.platform,
    fetch: Callable | None = None,
    provider: Callable | None = None,
) -> tuple[str, str] | None:
    """Windows: do the temperatures and power that need LibreHardwareMonitor arrive? Linux: is
    something missing that the one-click setup brings (the panel's permission, the mainboard's
    fans, the processor's power)?

    (result, what to say) for the report; None elsewhere, and on Linux when nothing is missing.
    Where Libre Panel can read LibreHardwareMonitor itself it does that; otherwise it
    asks the web server. ``fetch(url)`` asks the web server (tests give their own, and
    then only it is asked); ``provider(options)`` makes the source that reads inside Libre
    Panel (also for tests)."""
    if platform.startswith("linux"):
        from libre_panel import linux_setup

        said = linux_setup.hint(linux_setup.gather(), "libre-panel system-setup --linux")
        return ("info", said) if said else None
    if platform != "win32":
        return None
    from libre_panel.sensors.librehardwaremonitor import (
        DEFAULT_URL,
        fetch_tree,
        hardware_hint,
        readings_from_tree,
    )

    try:
        config = load_config(config_path)
    except ConfigError:
        config = Config()
    options = config.sensors.options.get("librehardwaremonitor", {})
    url = options.get("url", DEFAULT_URL)
    # a report goes to a public issue: no address of the user's network in it
    shown = url if url == DEFAULT_URL else "the address in [sensors.librehardwaremonitor]"
    if fetch is None and options.get("mode", "auto") != "webserver":
        found = _inside_check(options, config.sensors.providers, shown, platform, provider)
        if found is not None:
            return found
    try:
        readings = readings_from_tree((fetch or fetch_tree)(url), options.get("gpu"))
        answers = True
    except Exception:  # noqa: BLE001 - not running, or not what it should be
        readings, answers = {}, False
    hint = hardware_hint(readings, config.sensors.providers, shown, platform)
    if hint is None:
        return "info", f"LibreHardwareMonitor answers at {shown}: temperature and power arrive"
    state = "answers, but sends no temperatures or power" if answers else "does not answer"
    return "info", f"LibreHardwareMonitor {state} at {shown}. {hint}"


def _inside_check(
    options: dict, providers: list[str], shown: str, platform: str, make: Callable | None
) -> tuple[str, str] | None:
    """The same question when the sensor service or LibreHardwareMonitor inside Libre Panel
    reads it; None where neither can (then the web server is asked, unless it was asked for
    by ``mode``)."""
    from libre_panel.sensors import lhm_inprocess, lhm_service
    from libre_panel.sensors.librehardwaremonitor import (
        LibreHardwareMonitorProvider,
        dll_dir_of,
        hardware_hint,
        ring0_steps,
    )

    mode = options.get("mode", "auto")
    if not (mode == "service" or (mode == "auto" and lhm_service.installed())):
        folder, why = lhm_inprocess.availability(dll_dir_of(options))
        if folder is None:
            if mode == "inprocess":
                return "info", f"LibreHardwareMonitor cannot run inside Libre Panel: {why}"
            return None
    source = (make or LibreHardwareMonitorProvider)(options)
    try:
        source.wait_ready(lhm_inprocess.READY_SECONDS)
        readings, status = source.read(), source.status()
    finally:
        source.close()
    hint = hardware_hint(readings, providers, shown, platform, status)
    if hint is not None:
        return "info", hint
    where = "in the sensor service" if status.mode == "service" else "inside Libre Panel"
    text = f"LibreHardwareMonitor runs {where}: temperature and power arrive"
    if status.ring0 is False:  # the graphics card's do, the processor's do not
        text += f", but not the CPU's: {' and '.join(ring0_steps(status))}"
    return "info", text


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
    width: int,
    height: int,
    title: str,
    subtitle: str,
    round_panel: bool = False,
    hidden: dict[str, int] | None = None,
) -> Image.Image:
    """A card that makes cut-off edges, rotation, mirroring and colour mix-ups obvious.

    ``hidden``: the strips the panel's bezel is known to hide (PanelModel.hidden_edges); the
    frame goes inside them, so that a frame not seen means more is hidden than known."""
    img = Image.new("RGB", (width, height), "black")
    d = ImageDraw.Draw(img)
    unit = max(8, min(width, height) // 12)
    edge = {"top": 0, "right": 0, "bottom": 0, "left": 0, **(hidden or {})}
    x0, y0 = edge["left"], edge["top"]
    x1, y1 = width - 1 - edge["right"], height - 1 - edge["bottom"]
    if round_panel:
        d.ellipse([0, 0, width - 1, height - 1], outline="white", width=4)
    else:
        d.rectangle([x0, y0, x1, y1], outline="white", width=4)
        d.rectangle([x0 + 10, y0 + 10, x1 - 10, y1 - 10], outline="#ffd400", width=2)
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


# each library by its distribution's name and the module it brings
LIBRARIES = {"pillow": "PIL", "pyusb": "usb", "pycryptodome": "Crypto",
             "libusb-package": "libusb_package", "pyserial": "serial"}  # fmt: skip


def _versions() -> str:
    """The libraries' versions: from their metadata, else (the Windows and Linux downloads
    bundle the modules without it) from the module."""
    parts = []
    for dist, module in LIBRARIES.items():
        try:
            parts.append(f"{dist} {version(dist)}")
            continue
        except PackageNotFoundError:
            pass
        try:
            found = importlib.import_module(module)
        except ImportError:
            parts.append(f"{dist} missing")
        else:
            parts.append(f"{dist} {getattr(found, '__version__', 'bundled')}")
    return ", ".join(parts)


def open_serial_panel(
    port: str = "",
    choose: Callable[[list], object] | None = None,
    model_id: str = "",
    ask: Callable[[Any], Any] | None = None,
):
    """A connected serial panel with a driver, opened; None when there is none.

    ``model_id`` (device.model) names the panel; else it is found by its USB ids,
    and where those fit several sizes ``choose`` picks one of them. A port with the generic
    ids that other USB devices have too is opened only where ``ask`` says which panel it is."""
    from libre_panel.config import DeviceConfig
    from libre_panel.devices import jonsbo_zc360
    from libre_panel.devices.models import find_model
    from libre_panel.devices.serial_link import FoundPort, find_port, gadget_ports
    from libre_panel.devices.turzx import OTHER_DRIVERS, SERIAL_DRIVERS, TurzxDisplay

    model = find_model(model_id)
    if model is None and jonsbo_zc360.present():  # (its own USB ids: no TURZX panel)
        model = find_model(jonsbo_zc360.MODEL_ID)
    if model is not None and model.protocol in OTHER_DRIVERS:
        pass
    elif model is None or model.protocol not in SERIAL_DRIVERS:
        found = find_port(None, port)
        if found is None and not port and ask is not None:
            for maybe in gadget_ports():
                chosen = ask(maybe)
                if chosen is not None:
                    found = FoundPort(maybe.device, chosen, maybe.serial_number, (chosen,))
                    break
        if found is None or found.model.protocol not in SERIAL_DRIVERS:
            return None
        model, port = found.model, found.device
        unsure = {m.protocol for m in found.candidates} == {"serial-c"} and not found.sure
        if unsure and choose is not None:  # rev. C panels cannot say their size
            model = choose(list(found.candidates))
    display = TurzxDisplay(DeviceConfig(driver="turzx", model=model.id, port=port))
    display.open()
    return display


class Terminal:
    """How the doctor asks in a terminal: ``ask`` is ``input``. The app asks with buttons
    instead (doctor_session.py): each question has a key it is known by there."""

    def __init__(self, ask: Callable[[str], str], say: Callable[[str], None]) -> None:
        self.ask = ask
        self.say = say

    def confirm(self, key: str, question: str) -> tuple[bool, str]:
        """Yes or no, and with a no what the person sees instead."""
        answer = self.ask(f"{question} [y/n] ").strip().lower()
        if answer.startswith(("y", "j")):
            return True, ""
        return False, self.ask("What do you see instead? (Enter to skip) ").strip()

    def choose(self, key: str, question: str, options: list[str]) -> int | None:
        """The index of one of ``options``; None for none of them."""
        for number, label in enumerate(options, start=1):
            self.say(f"  {number}) {label}")
        answer = self.ask(f"{question} [1-{len(options)}] ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        return None

    def number(self, key: str, question: str) -> int | None:
        """A whole number; None where the person cannot tell."""
        answer = self.ask(question).strip()
        return int(answer) if answer.isdigit() else None

    def wait(self, key: str, text: str) -> None:
        """Until the person is ready (watching the panel)."""
        self.ask(text)


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
        open_serial: Callable[[], object] | None = None,
        model_id: str = "",
        port: str = "",
        sensors: Callable[[], tuple[str, str] | None] | None = None,
        video: VideoConfig | None = None,
        questions: Any = None,
        speed_seconds: float = 20.0,
    ) -> None:
        self.ask = ask
        # who answers the questions: the app (``questions``), a terminal (``ask``), or nobody
        if questions is None and ask is not None:
            questions = Terminal(ask, say)
        self.questions = questions
        self.video = video  # the [video] settings: whether this panel would get the video mode
        self.brightness = brightness  # set again after the brightness check
        self.say = say
        self.pause = pause
        self.seconds_per_card = seconds_per_card
        self.frames = frames
        # the speed test ends after this long, at two frames at least: an older serial panel
        # (rev. A at 115200 baud) takes up to half a minute for one whole frame
        self.speed_seconds = speed_seconds
        if open_transport is None:
            from libre_panel.devices.turzx_usb import UsbTransport

            open_transport = UsbTransport.open
        self.open_transport = open_transport
        if open_serial is None:  # the panel in the config, else what is found (asking its size)

            def open_serial() -> object:
                return open_serial_panel(port, self._choose_size, model_id, self._ask_gadget)

        self.open_serial = open_serial
        self.sensors = sensors  # asked for a step of the report, even where no panel is found
        self.transport = None  # a USB panel
        self.display = None  # a serial panel, through its driver
        self.report = Report()

    # -- helpers -----------------------------------------------------------

    def _step(self, name: str, result: str, detail: str = "") -> None:
        self.report.add(name, result, detail)
        mark = {"ok": "ok", "fail": "FAIL", "skip": "skip", "info": "info"}[result]
        self.say(f"[{mark:>4}] {name}" + (f" — {detail}" if detail else ""))

    def _question(self, key: str, name: str, question: str) -> None:
        if self.questions is None:
            self.pause(self.seconds_per_card)
            self._step(name, "skip", "not checked (no questions asked)")
            return
        seen, note = self.questions.confirm(key, question)
        if seen:
            self._step(name, "ok", "confirmed by looking at the panel")
        else:
            self._step(name, "fail", note or "not as expected")

    def _choose_size(self, candidates: list) -> object:
        """Which of several panels with the same USB ids this one is."""
        labels = ", ".join(m.label for m in candidates)
        default = next((m for m in candidates if m.shape == "rect"), candidates[0])
        if self.questions is None:
            self._step("panel size", "info", f"{default.label} assumed (its USB ids fit {labels}); "
                       "set device.model if it is another")  # fmt: skip
            return default
        self.say("Its USB ids fit several panels:")
        index = self.questions.choose("size", "Which one is it?", [m.label for m in candidates])
        chosen = default if index is None else candidates[index]
        self._step("panel size", "info", f"{chosen.label}, as chosen (USB ids fit {labels})")
        return chosen

    def _ask_gadget(self, found: Any) -> object:
        """Which panel is on a port with the generic ids of an awake rev. C panel, which other
        USB devices have too: None (nothing is sent to it) unless the person says."""
        if self.questions is None:
            return None
        self.say(f"\n{found.device} has the USB ids of a Turing panel (rev. C), which other USB "
                 "devices have too.")  # fmt: skip
        index = self.questions.choose(
            "gadget",
            f"Is the device on {found.device} one of these panels? (Enter: none of them)",
            [m.label for m in found.candidates],
        )
        if index is None:
            self._step("USB serial panel found", "info", f"{found.device}: not a panel, as told")
            return None
        chosen = found.candidates[index]
        self._step("panel size", "info", f"{chosen.label} on {found.device}, as chosen")
        return chosen

    def _send(self, image: Image.Image) -> tuple[float, int]:
        """Show a whole frame; (seconds it took, bytes sent)."""
        started = time.perf_counter()
        if self.display is not None:
            self.display.show(image)
            return time.perf_counter() - started, image.width * image.height * 2  # RGB565
        from libre_panel.devices.turzx_usb import CMD_UPLOAD_PNG, encode_frame, to_native

        png = encode_frame(to_native(image))
        started = time.perf_counter()
        self.transport.command(CMD_UPLOAD_PNG, struct.pack(">I", len(png)), png, timeout_ms=5000)
        return time.perf_counter() - started, len(png)

    def _set_brightness(self, percent: int) -> None:
        if self.display is not None:
            self.display.set_brightness(percent)
            return
        from libre_panel.devices.turzx_usb import CMD_BRIGHTNESS, brightness_arg

        self.transport.command(CMD_BRIGHTNESS, bytes([brightness_arg(percent)]))

    # -- the checks --------------------------------------------------------

    def run(self) -> Report:
        self._step("environment", "info", _versions())
        self._sensors_step()
        self.transport = self._find_and_open()
        if self.transport is None:
            self.display = self._find_serial()
            if self.display is None:
                return self.report
        try:
            self._checks()
        except DeviceError as exc:
            self._step("panel stopped answering", "fail", str(exc))
        finally:
            for device in (self.transport, self.display):
                if device is not None:
                    device.close()
        return self.report

    def _sensors_step(self) -> None:
        if self.sensors is None:
            return
        try:
            step = self.sensors()
        except Exception as exc:  # noqa: BLE001 - a side check must not stop the panel check
            step = ("info", f"not checked ({type(exc).__name__}: {exc})")
        if step is not None:
            result, detail = step
            self._step("temperature and power readings", result, without_folders(detail))

    def _find_and_open(self):
        try:
            transport = self.open_transport()
        except DeviceError as exc:
            self._step("find and open a USB panel (VID 1CBE)", "info", str(exc))
            return None
        matches = models_for_usb(0x1CBE, transport.pid)
        self.report.model = matches[0] if matches else None
        label = self.report.model.label if self.report.model else "unknown size"
        self._step("find and open a USB panel", "ok", f"1cbe:{transport.pid:04x} — {label}")
        return transport

    def _find_serial(self):
        try:
            display = self.open_serial()
        except DeviceError as exc:
            self._step("open a USB serial panel (virtual COM port)", "fail", str(exc))
            return None
        if display is None:
            self._step(
                "find a panel",
                "fail",
                "no USB panel and no USB serial panel (virtual COM port) with a driver",
            )
            self._report_serial_ports()
            return None
        self.report.model = display.model
        if display.model.protocol.startswith("serial"):
            self._step("find and open a USB serial panel (virtual COM port)", "ok",
                       display.model.label)  # fmt: skip
        else:
            self._step("find and open a USB panel", "ok", display.model.label)
        return display

    def _report_serial_ports(self) -> None:
        """The ports with a panel's USB ids that were not opened, and why."""
        from libre_panel.devices.serial_link import GADGET_IDS

        try:
            ports = list_serial_ports()
        except DeviceError:
            return
        for port in ports:
            if not port["vid"]:
                continue
            ids = (int(port["vid"], 16), int(port["pid"], 16))
            models = models_for_usb(*ids, port["serial_number"])
            if not models:
                continue
            names = ", ".join(m.id for m in models)
            if ids in GADGET_IDS:  # the serial number is said only where it is the panels'
                number = "none" if not port["serial_number"] else "another (not shown)"
                why = (f"serial number {number}; these USB ids are other USB devices' too, so "
                       "Libre Panel talks to it only once the panel is chosen: Settings → "
                       "Panel → Another panel (device.model = "
                       + " or ".join(f'"{m.id}"' for m in models) + ")")  # fmt: skip
            else:
                why = "not opened"
            where = f"{port['device']} {port['vid']}:{port['pid']}"
            self._step("USB serial panel found", "info", f"{where} — {names}; {why}")

    def _ruler(self, model, step: int = 2) -> None:
        """Measure the strip the glass hides at each edge (landscape)."""
        w, h = model.size("landscape")
        self._send(ruler_card(w, h, step=step))
        self.say("\nThe panel should now show the RULER card (landscape).")
        if self.questions is None:
            self.pause(self.seconds_per_card)
            self._step("hidden edges (ruler)", "skip", "not measured (no questions asked)")
            return
        hidden = {}
        for edge in RULER_EDGES:
            seen = self.questions.number(
                f"ruler-{edge}",
                f"{edge.capitalize()} edge: smallest number whose yellow bar you can see "
                "(Enter = cannot tell)? ",
            )
            # bar k shows, bar k - step does not: the glass hides k - step pixels (up to k - 1)
            hidden[edge] = None if seen is None else max(0, seen - step)
        parts = [f"{edge} {'?' if v is None else f'{v} px'}" for edge, v in hidden.items()]
        self.report.hidden = hidden
        self._step("hidden edges (ruler)", "info", "hidden: " + ", ".join(parts))

    def _handshake(self) -> bool:
        transport = self.transport
        dropped = transport.drain()
        reply = transport.sync()
        # (the bytes after the first two change with every connection)
        detail = f"reply {reply[:2].hex(' ')}"
        if dropped:
            detail += f", cleared {dropped} stale replies"
        self._step("handshake (command 10)", "ok", detail)
        if self.report.model is None:
            self._step("panel size", "fail", f"unknown product id {transport.pid:04x}")
            return False
        return True

    def _checks(self) -> None:
        usb = self.display is None
        if usb and not self._handshake():
            return
        model = self.report.model
        round_panel = model.shape == "round"
        orientations = ["landscape"] if round_panel else ["landscape", "portrait"]
        for n, orientation in enumerate(orientations, 1):
            w, h = model.size(orientation)
            subtitle = f"{model.label} · {w}x{h} {orientation} · card {n}"
            hidden = {} if round_panel else model.hidden_edges(orientation)
            self._send(test_card(w, h, "Libre Panel", subtitle, round_panel, hidden))
            self.say(f"\nThe panel should now show test card {n} ({orientation}).")
            if any(hidden.values()):  # its frame sits inside what the bezel is known to hide
                strips = ", ".join(f"{edge} {px} px" for edge, px in hidden.items() if px)
                self.say(f"(The frame keeps inside what the bezel hides here: {strips}.)")
            self._question(
                f"card-{orientation}",
                f"test card, {orientation}",
                "Is the white frame visible on all four edges, is 'UP' at the top edge, "
                "is the text readable (not mirrored) and are RED/GREEN/BLUE the right colours?",
            )

        if not round_panel:
            self._ruler(model)

        if self.questions is not None:
            self.questions.wait(
                "brightness-watch",
                "\nBrightness: the panel goes dark, then bright. Watch it and press Enter. ",
            )
        for percent in (10, 100):
            self._set_brightness(percent)
            self.pause(1.5)
        self._set_brightness(self.brightness)
        self._question(
            "brightness",
            "brightness (command 14)" if usb else "brightness",
            "Did the panel go dark and then bright?",
        )

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
            if len(times) >= 2 and sum(times) >= self.speed_seconds:
                break
        if times:
            avg = sum(times) / len(times)
            kind = "PNG" if usb else "RGB565"
            self._step(
                f"speed ({len(times)} full frames)",
                "ok",
                f"{avg * 1000:.0f} ms per frame = {1 / avg:.1f} fps, "
                f"worst {max(times) * 1000:.0f} ms, {kind} {sum(sizes) // len(sizes) // 1024} KB",
            )

        if usb:
            pid = self.transport.pid
            self.transport.close()
            self.transport = None
            self.transport = self.open_transport(pid)
            self.transport.sync()
        else:
            self.display.close()
            self.display.open()
        self._step("close and reconnect", "ok")
        self._send(test_card(w, h, "Test finished", "you can close this window", round_panel))
        if usb and self.video is not None:
            self._video_step(model)

    def _video_step(self, model) -> None:
        from libre_panel.devices.turzx import video_mode

        on, missing = video_mode(self.video, model)
        if on:
            detail = f"on, {self.video.fps} fps"
        elif missing:
            detail = "off: " + "; ".join(missing)
        else:
            detail = f'off (mode = "{self.video.mode}")'
        self._step("video mode", "info", detail)


def run_doctor(
    report_path: Path | None = None,
    ask_questions: bool = True,
    frames: int = 20,
    config_path: Path | None = None,
) -> Report:
    ask = input if ask_questions and sys.stdin.isatty() else None
    print("Libre Panel doctor — close the TURZX app before starting.\n")
    try:
        device = load_config(config_path).device
        brightness, model_id, port = device.brightness, device.model, device.port
        video = device.video
    except ConfigError:
        brightness, model_id, port = 60, "", ""
        video = None
    if model_id == "auto":
        model_id = ""
    doctor = Doctor(
        ask=ask,
        frames=frames,
        brightness=brightness,
        model_id=model_id,
        port=port,
        sensors=lambda: sensors_check(config_path),
        video=video,
    )
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

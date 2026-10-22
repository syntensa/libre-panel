"""Display driver interface.

A driver only moves pixels to hardware. It knows nothing about themes or
sensors, which keeps adding a new panel family (TURZX revisions, other
vendors) down to one small class registered under ``libre_panel.devices``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices.models import PanelModel


class DeviceError(RuntimeError):
    pass


# Where the panels this process has open are: a serial port, or "usb:<bus>.<address>". With
# more panels at once ([[panels]]) each loop's search for a panel passes the others' by.
_TAKEN: set[str] = set()
_TAKEN_LOCK = threading.Lock()


def take_place(place: str) -> bool:
    """Note that a panel at ``place`` is open; False where one of this process has it already."""
    with _TAKEN_LOCK:
        if place in _TAKEN:
            return False
        _TAKEN.add(place)
        return True


def leave_place(place: str) -> None:
    with _TAKEN_LOCK:
        _TAKEN.discard(place)


def place_taken(place: str) -> bool:
    return place in _TAKEN


def places_taken() -> set[str]:
    """The places noted as taken (a panel's own process tells the first panel's of its own)."""
    with _TAKEN_LOCK:
        return set(_TAKEN)


class FrameError(DeviceError):
    """This frame cannot be shown (e.g. too large); the connection is fine."""


@dataclass
class Counters:
    """What the drivers count while the panel runs, for the editor's diagnostics.

    The main loop gives every display the same one (``display.counters``), so the
    numbers go on when a display is made again after a replug. A driver adds to the
    counters that apply to it; each is written by one thread, so nothing is locked
    and counting costs a few additions per frame.
    """

    bytes: int = 0  # sent to the panel
    blocks: int = 0  # video: pictures handed to the panel's decoder
    waits: int = 0  # video: times the sender waited for room in the panel's queue
    drops: int = 0  # video: pictures thrown away unsent (the stream was replaced)
    max_depth: int = 0  # video: the deepest queue the panel reported
    usb_errors: int = 0  # errors on the panel's link that made the driver reconnect
    panel_restarts: int = 0  # video: restarts of the panel to clear a hung decoder
    ffmpeg_restarts: int = 0  # video: times ffmpeg stopped by itself and was started again


def dimmed(picture: Image.Image, percent: int) -> Image.Image:
    """``picture`` at ``percent`` of its brightness: the brightness of a panel that has no
    command for its backlight goes into the pixels."""
    if percent >= 100:
        return picture
    level = max(0, percent) / 100
    return picture.point([int(value * level) for value in range(256)] * len(picture.getbands()))


class KeepShowing:
    """For a panel that shows a picture of its own again when it gets none for a while (the
    JONSBO DS916, DS339): ``send`` sends the last picture again from a thread of its own
    whenever nothing went out for ``every`` seconds. The driver sends under ``lock`` and calls
    :meth:`sent` after; a fault of the thread is raised by the next :meth:`check`."""

    def __init__(self, every: float, send: Callable[[], None], name: str = "keep-showing") -> None:
        self.every = every
        self.lock = threading.Lock()
        self._send = send
        self._name = name
        self._last = time.monotonic()
        self._fault: DeviceError | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._last = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def sent(self) -> None:
        self._last = time.monotonic()

    def check(self) -> None:
        fault, self._fault = self._fault, None
        if fault is not None:
            raise fault

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(min(0.25, self.every / 2)):
            with self.lock:
                if self._stop.is_set() or time.monotonic() - self._last < self.every:
                    continue
                try:
                    self._send()
                except DeviceError as exc:
                    self._fault = exc
                    return
                self._last = time.monotonic()


class Display:
    name = "base"
    # A streaming display (a video encoder) wants every frame at ``stream_fps``,
    # changed or not; others get only frames that changed, at the configured fps.
    streaming = False
    stream_fps = 0
    # a slow link (the USB serial panels, pixels over a serial port): the main loop keeps the
    # motion calm and sends the parts that changed one by one (app.calm, changed_regions)
    slow = False
    # True once open() brought the panel back after the driver restarted it
    # (a hung video decoder): the main loop tells the services, unlike a replug.
    restarted = False
    # The panel this is, once it is open and the driver knows (a panel found by its USB ids
    # learns its model when it connects); None before, and for drivers that do not say.
    model: PanelModel | None = None

    def __init__(self, config: DeviceConfig) -> None:
        self.config = config
        # Replaced by the main loop's, shared by every display it makes (and by the
        # displays a wrapping one makes inside: ``auto``, ``turzx``).
        self.counters = Counters()

    def open(self) -> None:
        """Connect and initialise the panel."""

    def close(self) -> None:
        """Release the device; must be safe to call twice."""

    def set_brightness(self, percent: int) -> None:
        """0-100. Drivers without brightness control ignore it."""

    def screen_off(self) -> bool:
        """Libre Panel quits ([device] dark_on_quit): the panel's own command that turns its
        screen off, where it has one (its next open() turns it on again); whether it did."""
        return False

    def describe(self) -> str:
        """What frames go to, for status displays (tray, editor)."""
        return self.name

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        """Push ``frame`` (RGB, theme size). ``region`` is the changed box
        (x0, y0, x1, y1); drivers that support partial updates send only that."""
        raise NotImplementedError

    def __enter__(self) -> Display:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


_BUILTIN_DRIVERS = {
    "auto": "libre_panel.devices.auto:AutoDisplay",
    "virtual": "libre_panel.devices.virtual:VirtualDisplay",
    "turzx": "libre_panel.devices.turzx:TurzxDisplay",
}


def available_drivers() -> dict[str, str]:
    found = dict(_BUILTIN_DRIVERS)
    for ep in entry_points(group="libre_panel.devices"):
        found[ep.name] = ep.value
    return found


def create_display(config: DeviceConfig) -> Display:
    drivers = available_drivers()
    if config.driver not in drivers:
        raise DeviceError(
            f"unknown display driver {config.driver!r} (available: {', '.join(sorted(drivers))})"
        )
    module_name, _, attr = drivers[config.driver].partition(":")
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)(config)


def list_usb_devices(
    vendor_ids: tuple[int, ...] = (0x1CBE, 0x43A8, 0x345F),
) -> list[dict[str, Any]]:
    """Plain USB panels (Turing V1.x hardware, the JONSBO ZC-360's screens, the MS9132 chip of
    the JONSBO DS339); needs pyusb and a libusb backend."""
    try:
        import usb.core
    except ImportError as exc:
        raise DeviceError("pyusb is not installed: pip install 'libre-panel[usb]'") from exc
    found = []
    try:
        for vid in vendor_ids:
            for dev in usb.core.find(find_all=True, idVendor=vid) or []:
                found.append({"vid": f"{dev.idVendor:04x}", "pid": f"{dev.idProduct:04x}"})
    except usb.core.NoBackendError as exc:
        raise DeviceError("no libusb backend found (see docs/HARDWARE.md)") from exc
    return found


def list_serial_ports() -> list[dict[str, Any]]:
    """Serial/USB-CDC ports with USB ids, for finding a panel (needs pyserial)."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise DeviceError("pyserial is not installed: pip install 'libre-panel[serial]'") from exc
    ports = []
    for port in list_ports.comports():
        ports.append(
            {
                "device": port.device,
                "description": port.description,
                "vid": f"{port.vid:04x}" if port.vid is not None else None,
                "pid": f"{port.pid:04x}" if port.pid is not None else None,
                "serial_number": port.serial_number,
                "manufacturer": port.manufacturer,
            }
        )
    return ports

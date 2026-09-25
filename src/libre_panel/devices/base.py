"""Display driver interface.

A driver only moves pixels to hardware. It knows nothing about themes or
sensors, which keeps adding a new panel family (TURZX revisions, other
vendors) down to one small class registered under ``libre_panel.devices``.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from PIL import Image

from libre_panel.config import DeviceConfig


class DeviceError(RuntimeError):
    pass


class FrameError(DeviceError):
    """This frame cannot be shown (e.g. too large); the connection is fine."""


class Display:
    name = "base"

    def __init__(self, config: DeviceConfig) -> None:
        self.config = config

    def open(self) -> None:
        """Connect and initialise the panel."""

    def close(self) -> None:
        """Release the device; must be safe to call twice."""

    def set_brightness(self, percent: int) -> None:
        """0-100. Drivers without brightness control ignore it."""

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


def list_usb_devices(vendor_ids: tuple[int, ...] = (0x1CBE,)) -> list[dict[str, Any]]:
    """Plain USB panels (Turing V1.x hardware); needs pyusb and a libusb backend."""
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

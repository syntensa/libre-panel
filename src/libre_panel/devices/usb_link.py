"""What the drivers of the USB screens of other makers share (Thermalright, and those after it):
finding a screen by its USB ids, taking it for one panel of this process, claiming its
interfaces, finding their endpoints, and HID reports sent and read over pyusb.

pyusb and a libusb backend are needed, as for the TURZX USB panels; without them a screen is
not found, which is no fault.
"""

from __future__ import annotations

import logging
from typing import Any

from libre_panel.devices.base import DeviceError, leave_place, take_place
from libre_panel.i18n import t

log = logging.getLogger(__name__)

HID_CLASS = 3


def _backend() -> Any:
    from libre_panel.devices.turzx_usb import _backend as backend

    return backend()


def find(vendor: int, products: tuple[int, ...]) -> list[tuple[Any, int]]:
    """The devices on the bus with one of these ids, each with its product id."""
    try:
        import usb.core
    except ImportError as exc:
        raise DeviceError("pyusb is not installed: pip install 'libre-panel[usb]'") from exc
    found = []
    try:
        for product in products:
            devices = usb.core.find(find_all=True, idVendor=vendor, idProduct=product,
                                    backend=_backend())  # fmt: skip
            found += [(device, product) for device in devices or []]
    except usb.core.NoBackendError as exc:
        raise DeviceError("no libusb backend found (see docs/HARDWARE.md)") from exc
    return found


def present(vendor: int, products: tuple[int, ...]) -> int | None:
    """The product id of the first such device on the bus (none is opened); None for none, or
    no pyusb."""
    try:
        found = find(vendor, products)
    except DeviceError:
        return None
    return found[0][1] if found else None


def place(device: Any) -> str:
    """Where a device is on the bus, for :func:`take_place`."""
    return f"usb:{getattr(device, 'bus', None)}.{getattr(device, 'address', None)}"


def take(vendor: int, products: tuple[int, ...], label: str) -> tuple[Any, int, str]:
    """The first such device that no panel of this process has open, taken for this one: the
    device, its product id and its place (to :func:`leave_place` when it is let go)."""
    found = find(vendor, products)
    if not found:
        raise DeviceError(t("no {panel} on the USB bus", panel=label))
    for device, product in found:
        where = place(device)
        if take_place(where):
            return device, product, where
    raise DeviceError(t("another panel of Libre Panel has the {panel} open", panel=label))


def claim(device: Any, interfaces: tuple[int, ...]) -> None:
    """Claim ``interfaces``, a kernel driver on them let go first. The configuration is set only
    where the device has none yet: setting it again would reset the interfaces that other
    drivers have."""
    import usb.core
    import usb.util

    from libre_panel.devices.turzx_usb import access_hint

    try:
        for interface in interfaces:
            try:
                if device.is_kernel_driver_active(interface):
                    device.detach_kernel_driver(interface)
            except (NotImplementedError, usb.core.USBError):
                pass  # (Windows has none to detach)
        try:
            configured = device.get_active_configuration().bConfigurationValue
        except usb.core.USBError:
            configured = None
        if configured is None:
            device.set_configuration()
        for interface in interfaces:
            usb.util.claim_interface(device, interface)
    except usb.core.USBError as exc:
        raise DeviceError(access_hint(exc) or f"cannot open the panel: {exc}") from exc


def release(device: Any, interfaces: tuple[int, ...], where: str) -> None:
    """Let the device go (safe to call when it is gone already) and leave its place."""
    import usb.util

    for interface in interfaces:
        try:
            usb.util.release_interface(device, interface)
        except Exception:  # noqa: BLE001 - gone already
            pass
    try:
        usb.util.dispose_resources(device)
    except Exception:  # noqa: BLE001
        pass
    if where:
        leave_place(where)


def interfaces(device: Any) -> list[Any]:
    """The interfaces of the device's configuration."""
    return list(device.get_active_configuration())


def hid_interface(device: Any) -> int | None:
    """The number of the device's HID interface; None for none."""
    for interface in interfaces(device):
        if interface.bInterfaceClass == HID_CLASS:
            return interface.bInterfaceNumber
    return None


def packet_size(device: Any, address: int, default: int = 512) -> int:
    """The largest packet of the endpoint at ``address`` (a bulk one's is 512 at USB 2.0's high
    speed); ``default`` where the device does not say."""
    for interface in interfaces(device):
        for endpoint in interface:
            if endpoint.bEndpointAddress == address:
                return int(getattr(endpoint, "wMaxPacketSize", 0) or 0) & 0x7FF or default
    return default


def endpoints(device: Any, number: int) -> tuple[int | None, int | None]:
    """The first OUT and the first IN endpoint of interface ``number`` (their addresses)."""
    for interface in interfaces(device):
        if interface.bInterfaceNumber == number:
            addresses = [endpoint.bEndpointAddress for endpoint in interface]
            out = next((a for a in addresses if not a & 0x80), None)
            into = next((a for a in addresses if a & 0x80), None)
            return out, into
    return None, None


class HidReports:
    """Reports of one size to and from a device's HID interface: written to its interrupt OUT
    endpoint (with SET_REPORT where it has none), read from its interrupt IN endpoint. A
    device without report ids (``ids`` False) takes them as they are; one with ids has its id
    in byte 0."""

    def __init__(self, device: Any, interface: int, size: int, ids: bool = False) -> None:
        self.device, self.interface, self.size, self.ids = device, interface, size, ids
        self.out, self.into = endpoints(device, interface)

    def write(self, report: bytes, timeout_ms: int = 3000) -> None:
        data = bytes(report).ljust(self.size, b"\0")
        if self.out is not None:
            self.device.write(self.out, data, timeout_ms)
        else:  # SET_REPORT, an output report
            report_id = data[0] if self.ids else 0
            self.device.ctrl_transfer(0x21, 0x09, 0x0200 | report_id, self.interface, data,
                                      timeout_ms)  # fmt: skip

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        """The next input report; None where none came in time."""
        import usb.core

        if self.into is None:
            return None
        try:
            return bytes(self.device.read(self.into, self.size, timeout_ms))
        except usb.core.USBTimeoutError:
            return None

"""A USB device as pyusb shows it to the drivers of other makers' screens (devices/usb_link.py):
its interfaces and their endpoints, what each endpoint was sent, and what the device answers.
A fake of a screen says in ``received`` what it does with what it gets."""

import usb.core
import usb.util

from libre_panel.devices import base, usb_link


class Endpoint:
    def __init__(self, address: int) -> None:
        self.bEndpointAddress = address


class Interface(list):
    def __init__(self, number: int, endpoints: tuple[int, ...], kind: int = 0xFF) -> None:
        super().__init__(Endpoint(address) for address in endpoints)
        self.bInterfaceNumber, self.bInterfaceClass = number, kind


class Configuration(list):
    bConfigurationValue = 1


class FakeUsbDevice:
    def __init__(self, interfaces: list[Interface], bus: int = 1, address: int = 7) -> None:
        self.bus, self.address = bus, address
        self.configuration = Configuration(interfaces)
        self.sent: list[tuple[int, bytes]] = []
        self.answers: dict[int, list[bytes]] = {}
        self.claimed: set[int] = set()
        self.broken = False

    def is_kernel_driver_active(self, interface):
        return False

    def get_active_configuration(self):
        return self.configuration

    def set_configuration(self, value=None):
        raise AssertionError("it is configured already: setting it again resets its interfaces")

    def write(self, endpoint, data, timeout):
        if self.broken:
            raise usb.core.USBError("gone")
        data = bytes(data)
        self.sent.append((endpoint, data))
        self.received(endpoint, data)
        return len(data)

    def received(self, endpoint: int, data: bytes) -> None:
        """What the device does with what it got (a fake of a screen says it)."""

    def answer(self, endpoint: int, data: bytes) -> None:
        self.answers.setdefault(endpoint, []).append(bytes(data))

    def read(self, endpoint, size, timeout):
        if self.broken:
            raise usb.core.USBError("gone")
        waiting = self.answers.get(endpoint)
        if not waiting:
            raise usb.core.USBTimeoutError("no answer")
        return waiting.pop(0)[:size]

    def to(self, endpoint: int) -> list[bytes]:
        """What ``endpoint`` was sent, a write each."""
        return [data for to, data in self.sent if to == endpoint]


def plug(monkeypatch, vendor: int, *devices: tuple[FakeUsbDevice, int]) -> None:
    """Put these devices (each with its product id) on the bus that usb_link looks at."""

    def find(wanted, products):
        assert wanted == vendor
        return [(device, pid) for pid in products for device, has in devices if has == pid]

    monkeypatch.setattr(usb_link, "find", find)
    monkeypatch.setattr(usb.util, "claim_interface", lambda device, n: device.claimed.add(n))
    monkeypatch.setattr(usb.util, "release_interface", lambda device, n: device.claimed.discard(n))
    monkeypatch.setattr(usb.util, "dispose_resources", lambda device: None)


def taken(device: FakeUsbDevice) -> bool:
    return base.place_taken(usb_link.place(device))

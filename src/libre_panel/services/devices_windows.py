"""What Windows can tell about drives, USB devices, game controllers and storage, through
ctypes: the Windows half of the ``devices`` service.

Everything here only asks. No device is opened for data, nothing is written, and the USB
devices are listed from the system's own table, not opened (the panel's own device is among
them, and Libre Panel never opens it for this). The calls load on first use, and each reader
takes the DLL it needs as an argument, so that tests bring a fake one.
"""

from __future__ import annotations

import ctypes
import functools
import re
import string
import struct
from collections.abc import Callable, Collection
from typing import Any

# -- drives and storage ----------------------------------------------------------

SEM_FAILCRITICALERRORS = 0x0001  # no "there is no disk in the drive" box for a card reader
DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
FILE_SHARE_READ_WRITE, OPEN_EXISTING = 3, 3
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
STORAGE_DEVICE_PROPERTY = 0
STORAGE_ADAPTER_TEMPERATURE_PROPERTY = 51  # the temperature, and the limits the drive sets
# a fixed drive on one of these buses is an external one (a USB SSD says it is "fixed");
# the internal ones (SATA, NVMe: 17) are not told about
EXTERNAL_BUSES = {7: "USB", 12: "SD", 13: "MMC"}
INVALID_HANDLE = ctypes.c_void_p(-1).value


@functools.cache
def _kernel32() -> Any:
    c = ctypes
    kernel = c.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.GetLogicalDrives.restype = c.c_uint32
    kernel.GetDriveTypeW.argtypes = [c.c_wchar_p]
    kernel.GetDriveTypeW.restype = c.c_uint
    kernel.CreateFileW.argtypes = [
        c.c_wchar_p, c.c_uint32, c.c_uint32, c.c_void_p, c.c_uint32, c.c_uint32, c.c_void_p,
    ]  # fmt: skip
    kernel.CreateFileW.restype = c.c_void_p
    kernel.DeviceIoControl.argtypes = [
        c.c_void_p, c.c_uint32, c.c_void_p, c.c_uint32, c.c_void_p, c.c_uint32,
        c.POINTER(c.c_uint32), c.c_void_p,
    ]  # fmt: skip
    kernel.DeviceIoControl.restype = c.c_int
    kernel.CloseHandle.argtypes = [c.c_void_p]
    kernel.CloseHandle.restype = c.c_int
    kernel.GetDiskFreeSpaceExW.argtypes = [
        c.c_wchar_p, c.POINTER(c.c_ulonglong), c.POINTER(c.c_ulonglong), c.c_void_p,
    ]  # fmt: skip
    kernel.GetDiskFreeSpaceExW.restype = c.c_int
    kernel.GetVolumeInformationW.argtypes = [
        c.c_wchar_p, c.c_wchar_p, c.c_uint32, c.c_void_p, c.c_void_p, c.c_void_p,
        c.c_wchar_p, c.c_uint32,
    ]  # fmt: skip
    kernel.GetVolumeInformationW.restype = c.c_int
    kernel.SetErrorMode.argtypes = [c.c_uint]
    kernel.SetErrorMode.restype = c.c_uint
    return kernel


def _query(kernel: Any, letter: str, property_id: int) -> bytes | None:
    """What IOCTL_STORAGE_QUERY_PROPERTY says about the drive: the volume is opened with no
    access rights at all (it reads nothing off the disk), or None."""
    handle = kernel.CreateFileW(
        f"\\\\.\\{letter}:", 0, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if handle is None or handle == INVALID_HANDLE:
        return None
    try:
        query = struct.pack("<II", property_id, 0) + b"\0" * 4  # STORAGE_PROPERTY_QUERY
        out = ctypes.create_string_buffer(1024)
        got = ctypes.c_uint32()
        if not kernel.DeviceIoControl(
            handle, IOCTL_STORAGE_QUERY_PROPERTY, query, len(query), out, 1024,
            ctypes.byref(got), None,
        ):  # fmt: skip
            return None
        return out.raw[: got.value]
    finally:
        kernel.CloseHandle(handle)


def parse_device(raw: bytes) -> tuple[int | None, str]:
    """(bus type, "Maker Product") from a STORAGE_DEVICE_DESCRIPTOR."""
    if len(raw) < 36:
        return None, ""

    def text(offset: int) -> str:
        if not offset or offset >= len(raw):
            return ""
        end = raw.find(b"\0", offset)
        return raw[offset : end if end >= 0 else len(raw)].decode("ascii", "replace").strip()

    maker, product = struct.unpack_from("<II", raw, 12)
    bus = struct.unpack_from("<I", raw, 28)[0]
    return bus, " ".join(part for part in (text(maker), text(product)) if part)


def parse_temperature(raw: bytes) -> tuple[float | None, float | None, float | None]:
    """(temperature, warning limit, critical limit) in °C from a
    STORAGE_TEMPERATURE_DATA_DESCRIPTOR; None for what it does not say."""
    if len(raw) < 40:
        return None, None, None
    critical, warning, count = struct.unpack_from("<hhH", raw, 8)
    found = []
    for i in range(count):  # STORAGE_TEMPERATURE_INFO, 16 bytes each
        offset = 24 + 16 * i
        if offset + 4 > len(raw):
            break
        value = struct.unpack_from("<h", raw, offset + 2)[0]
        if -40 < value < 150:
            found.append(value)

    def limit(value: int) -> float | None:
        return float(value) if 0 < value < 150 else None

    return (float(max(found)) if found else None), limit(warning), limit(critical)


def _drives(kernel: Any, kinds: Collection[int]) -> list[tuple[str, int]]:
    mask = kernel.GetLogicalDrives()
    found = []
    for i, letter in enumerate(string.ascii_uppercase):
        if mask >> i & 1:
            kind = kernel.GetDriveTypeW(f"{letter}:\\")
            if kind in kinds:
                found.append((letter, kind))
    return found


def _space(kernel: Any, letter: str) -> tuple[int, int] | None:
    """(free, total) bytes of the drive, None for a card reader without a card."""
    free, total = ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if not kernel.GetDiskFreeSpaceExW(
        f"{letter}:\\", ctypes.byref(free), ctypes.byref(total), None
    ):
        return None
    return free.value, total.value


def read_drives(kernel: Any = None) -> dict[str, dict[str, Any]]:
    """The drives that come and go, by letter: ``{name, label, total, free, bus}``. Drives of
    the removable kind, and fixed ones on a USB, SD or MMC bus; an empty card reader has no
    entry until it has a card."""
    kernel = kernel or _kernel32()
    before = kernel.SetErrorMode(SEM_FAILCRITICALERRORS)
    try:
        found = {}
        for letter, kind in _drives(kernel, (DRIVE_REMOVABLE, DRIVE_FIXED)):
            raw = _query(kernel, letter, STORAGE_DEVICE_PROPERTY)
            bus, name = parse_device(raw) if raw else (None, "")
            if kind == DRIVE_FIXED and bus not in EXTERNAL_BUSES:
                continue
            space = _space(kernel, letter)
            if space is None:
                continue
            label = ctypes.create_unicode_buffer(64)
            kernel.GetVolumeInformationW(f"{letter}:\\", label, 64, None, None, None, None, 0)
            found[letter] = {
                "name": name, "label": label.value, "free": space[0], "total": space[1],
                "bus": EXTERNAL_BUSES.get(bus, "USB"),
            }  # fmt: skip
        return found
    finally:
        kernel.SetErrorMode(before)


def read_storage(kernel: Any = None) -> dict[str, dict[str, Any]]:
    """The fixed drives, by letter: ``{name, total, free, temp, warn, crit}``. The
    temperature and its limits are None where the drive does not tell."""
    kernel = kernel or _kernel32()
    before = kernel.SetErrorMode(SEM_FAILCRITICALERRORS)
    try:
        found = {}
        for letter, _kind in _drives(kernel, (DRIVE_FIXED,)):
            space = _space(kernel, letter)
            if space is None:
                continue
            raw = _query(kernel, letter, STORAGE_DEVICE_PROPERTY)
            name = parse_device(raw)[1] if raw else ""
            raw = _query(kernel, letter, STORAGE_ADAPTER_TEMPERATURE_PROPERTY)
            temp, warn, crit = parse_temperature(raw) if raw else (None, None, None)
            found[letter] = {
                "name": name, "free": space[0], "total": space[1],
                "temp": temp, "warn": warn, "crit": crit,
            }  # fmt: skip
        return found
    finally:
        kernel.SetErrorMode(before)


# -- game controllers (XInput) ---------------------------------------------------------

XINPUT_FLAG_GAMEPAD = 1
XINPUT_CAPS_WIRELESS = 0x0002
BATTERY_DEVTYPE_GAMEPAD = 0
BATTERY_WIRED = 1  # the other battery types: 2 alkaline, 3 rechargeable; 0 none, 255 unknown


class _Capabilities(ctypes.Structure):  # XINPUT_CAPABILITIES
    _fields_ = [
        ("Type", ctypes.c_ubyte),
        ("SubType", ctypes.c_ubyte),
        ("Flags", ctypes.c_ushort),
        ("Gamepad", ctypes.c_ubyte * 12),
        ("Vibration", ctypes.c_ushort * 2),
    ]


class _Battery(ctypes.Structure):  # XINPUT_BATTERY_INFORMATION
    _fields_ = [("Type", ctypes.c_ubyte), ("Level", ctypes.c_ubyte)]


@functools.cache
def _xinput() -> Any:
    c = ctypes
    for name in ("xinput1_4", "xinput1_3"):
        try:
            dll = c.WinDLL(name)  # type: ignore[attr-defined]
            dll.XInputGetCapabilities.argtypes = [c.c_uint32, c.c_uint32, c.POINTER(_Capabilities)]
            dll.XInputGetCapabilities.restype = c.c_uint32
            dll.XInputGetBatteryInformation.argtypes = [
                c.c_uint32, c.c_ubyte, c.POINTER(_Battery),
            ]  # fmt: skip
            dll.XInputGetBatteryInformation.restype = c.c_uint32
            return dll
        except (OSError, AttributeError):
            continue
    return None


def read_controllers(xinput: Any = None) -> dict[int, dict[str, Any]]:
    """The connected controllers, by slot (0 to 3): ``{wireless, battery, level}``. The
    battery is 1 for a cable, 2 or 3 for a battery (``level`` 0 empty, 1 low, 2 medium, 3 full),
    else None."""
    xinput = xinput or _xinput()
    if xinput is None:  # no XInput on this Windows
        return {}
    found = {}
    for slot in range(4):
        caps = _Capabilities()
        if xinput.XInputGetCapabilities(slot, XINPUT_FLAG_GAMEPAD, ctypes.byref(caps)) != 0:
            continue
        battery, kind, level = _Battery(), None, None
        if (
            xinput.XInputGetBatteryInformation(slot, BATTERY_DEVTYPE_GAMEPAD, ctypes.byref(battery))
            == 0
        ):
            kind, level = battery.Type, battery.Level
        found[slot] = {
            "wireless": bool(caps.Flags & XINPUT_CAPS_WIRELESS),
            "battery": kind,
            "level": level,
        }
    return found


# -- USB devices (SetupAPI) ------------------------------------------------------------

DIGCF_PRESENT, DIGCF_ALLCLASSES = 0x2, 0x4
SPDRP_DEVICEDESC, SPDRP_SERVICE, SPDRP_CLASS, SPDRP_FRIENDLYNAME = 0x00, 0x04, 0x07, 0x0C
# what a top-level USB device is called: USB\VID_xxxx&PID_xxxx\<serial or port>; the parts of
# a composite device (...&MI_00) have more in the middle and do not match
_TOP_LEVEL = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})\\[^\\]+$", re.IGNORECASE)
# hubs, mass storage (the drives are told by the drive readers) and the drivers of Xbox
# controllers (XInput tells those) are not USB devices to tell about
SKIPPED_SERVICES = {"usbhub", "usbhub3", "usbstor", "uaspstor", "xusb22", "xboxgip"}


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _DeviceInfo(ctypes.Structure):  # SP_DEVINFO_DATA
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("ClassGuid", _Guid),
        ("DevInst", ctypes.c_uint32),
        ("Reserved", ctypes.c_size_t),
    ]


class _PropertyKey(ctypes.Structure):  # DEVPROPKEY
    _fields_ = [("fmtid", _Guid), ("pid", ctypes.c_uint32)]


# DEVPKEY_Device_BusReportedDeviceDesc: the name the device itself reports
_NAME_BYTES = (ctypes.c_ubyte * 8)(0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2)
_BUS_REPORTED_NAME = _PropertyKey(_Guid(0x540B947E, 0x8B40, 0x45BC, _NAME_BYTES), 4)


@functools.cache
def _setupapi() -> Any:
    c = ctypes
    api = c.WinDLL("setupapi", use_last_error=True)  # type: ignore[attr-defined]
    api.SetupDiGetClassDevsW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_void_p, c.c_uint32]
    api.SetupDiGetClassDevsW.restype = c.c_void_p
    api.SetupDiEnumDeviceInfo.argtypes = [c.c_void_p, c.c_uint32, c.POINTER(_DeviceInfo)]
    api.SetupDiEnumDeviceInfo.restype = c.c_int
    api.SetupDiGetDeviceInstanceIdW.argtypes = [
        c.c_void_p, c.POINTER(_DeviceInfo), c.c_wchar_p, c.c_uint32, c.POINTER(c.c_uint32),
    ]  # fmt: skip
    api.SetupDiGetDeviceInstanceIdW.restype = c.c_int
    api.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        c.c_void_p, c.POINTER(_DeviceInfo), c.c_uint32, c.POINTER(c.c_uint32), c.c_void_p,
        c.c_uint32, c.POINTER(c.c_uint32),
    ]  # fmt: skip
    api.SetupDiGetDeviceRegistryPropertyW.restype = c.c_int
    api.SetupDiGetDevicePropertyW.argtypes = [
        c.c_void_p, c.POINTER(_DeviceInfo), c.POINTER(_PropertyKey), c.POINTER(c.c_uint32),
        c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32), c.c_uint32,
    ]  # fmt: skip
    api.SetupDiGetDevicePropertyW.restype = c.c_int
    api.SetupDiDestroyDeviceInfoList.argtypes = [c.c_void_p]
    api.SetupDiDestroyDeviceInfoList.restype = c.c_int
    return api


def keep_usb(entry: dict[str, Any], ignore: Collection[str]) -> bool:
    """Whether a USB device is one to tell about: not a hub, not mass storage or an Xbox
    controller (others tell those), and not on the list of what to leave out (``VID:PID``)."""
    if f"{entry['vid']}:{entry['pid']}" in ignore:
        return False
    if (entry.get("service") or "").lower() in SKIPPED_SERVICES:
        return False
    if "hub" in (entry.get("name") or "").lower() and (entry.get("klass") or "").upper() == "USB":
        return False
    return True


def read_usb(ignore: Collection[str] = (), api: Any = None) -> dict[str, dict[str, Any]]:
    """The USB devices that plug straight into the machine, by instance id:
    ``{vid, pid, name, klass, service}``. ``ignore`` holds ``VID:PID`` in upper case. The
    system's list is read; no device is opened."""
    api = api or _setupapi()
    handle = api.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if handle is None or handle == INVALID_HANDLE:
        return {}

    def registry_text(info: _DeviceInfo, field: int) -> str:
        buffer, kind = ctypes.create_unicode_buffer(512), ctypes.c_uint32()
        if api.SetupDiGetDeviceRegistryPropertyW(
            handle, ctypes.byref(info), field, ctypes.byref(kind), buffer,
            ctypes.sizeof(buffer), None,
        ):  # fmt: skip
            return buffer.value
        return ""

    found = {}
    try:
        index = 0
        while True:
            info = _DeviceInfo()
            info.cbSize = ctypes.sizeof(_DeviceInfo)
            if not api.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                break
            index += 1
            ident = ctypes.create_unicode_buffer(512)
            if not api.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(info), ident, 512, None):
                continue
            match = _TOP_LEVEL.match(ident.value)
            if not match:
                continue
            name, kind = ctypes.create_unicode_buffer(512), ctypes.c_uint32()
            if not api.SetupDiGetDevicePropertyW(
                handle, ctypes.byref(info), ctypes.byref(_BUS_REPORTED_NAME), ctypes.byref(kind),
                name, ctypes.sizeof(name), None, 0,
            ):  # fmt: skip
                name.value = ""
            entry = {
                "vid": match.group(1).upper(), "pid": match.group(2).upper(),
                "name": (
                    name.value
                    or registry_text(info, SPDRP_FRIENDLYNAME)
                    or registry_text(info, SPDRP_DEVICEDESC)
                ).strip(),
                "klass": registry_text(info, SPDRP_CLASS),
                "service": registry_text(info, SPDRP_SERVICE),
            }  # fmt: skip
            if keep_usb(entry, ignore):
                found[ident.value] = entry
    finally:
        api.SetupDiDestroyDeviceInfoList(handle)
    return found


# -- Windows tells when a device comes or goes -----------------------------------------

CM_NOTIFY_FILTER_FLAG_ALL_INTERFACE_CLASSES = 0x1
CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE = 0
CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL, CM_NOTIFY_ACTION_DEVICEINTERFACEREMOVAL = 0, 1


class _Filter(ctypes.Structure):
    """CM_NOTIFY_FILTER: 16 bytes of header, then a union whose biggest member is 200
    characters. Windows checks ``cbSize`` against exactly these 416 bytes."""

    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("FilterType", ctypes.c_int),
        ("Reserved", ctypes.c_uint32),
        ("Union", ctypes.c_ubyte * 400),
    ]


# WINFUNCTYPE exists only on Windows, where it is stdcall; elsewhere (the tests) cdecl does.
_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_uint32,
)


@functools.cache
def _cfgmgr() -> Any:
    c = ctypes
    cm = c.WinDLL("cfgmgr32")  # type: ignore[attr-defined]
    cm.CM_Register_Notification.argtypes = [
        c.POINTER(_Filter), c.c_void_p, _CALLBACK, c.POINTER(c.c_void_p),
    ]  # fmt: skip
    cm.CM_Register_Notification.restype = c.c_uint32
    cm.CM_Unregister_Notification.argtypes = [c.c_void_p]
    cm.CM_Unregister_Notification.restype = c.c_uint32
    return cm


class ChangeNotifier:
    """Windows' "tell me when a device comes or goes" (``CM_Register_Notification``): the
    function given to ``start`` is called for every device interface that appears or goes,
    from a thread of Windows. A plugged device brings a dozen of them in a row."""

    def __init__(self, cfgmgr: Any = None) -> None:
        self._cfgmgr = cfgmgr
        self._handle: ctypes.c_void_p | None = None
        self._callback: Any = None  # stays here: Windows calls it for as long as it is registered
        self.notices = 0  # how many came

    def start(self, on_change: Callable[[], None]) -> bool:
        """Register. False where Windows will not (before Windows 8): the poll then carries on
        alone."""
        try:
            cm = self._cfgmgr or _cfgmgr()

            def notice(handle: Any, context: Any, action: int, data: Any, size: int) -> int:
                # In a thread of Windows: nothing may be raised, nothing may be slow.
                try:
                    if action in (
                        CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL,
                        CM_NOTIFY_ACTION_DEVICEINTERFACEREMOVAL,
                    ):
                        self.notices += 1
                        on_change()
                except Exception:  # noqa: BLE001, S110
                    pass
                return 0

            self._callback = _CALLBACK(notice)
            filter_ = _Filter()
            filter_.cbSize = ctypes.sizeof(_Filter)
            filter_.Flags = CM_NOTIFY_FILTER_FLAG_ALL_INTERFACE_CLASSES
            filter_.FilterType = CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE
            handle = ctypes.c_void_p()
            if (
                cm.CM_Register_Notification(
                    ctypes.byref(filter_), None, self._callback, ctypes.byref(handle)
                )
                != 0
                or not handle.value
            ):
                return False
            self._cfgmgr, self._handle = cm, handle
            return True
        except Exception:  # noqa: BLE001 - no notices: the poll does it
            return False

    def stop(self) -> None:
        """Unregister (this waits for a notice that is under way, so not from a notice)."""
        handle, self._handle = self._handle, None
        if handle is not None and self._cfgmgr is not None:
            try:
                self._cfgmgr.CM_Unregister_Notification(handle)
            except Exception:  # noqa: BLE001, S110
                pass


class Readers:
    """What the watcher asks: the four readers above, as the system gives them."""

    def drives(self) -> dict[str, dict[str, Any]]:
        return read_drives()

    def storage(self) -> dict[str, dict[str, Any]]:
        return read_storage()

    def controllers(self) -> dict[int, dict[str, Any]]:
        return read_controllers()

    def usb(self, ignore: Collection[str]) -> dict[str, dict[str, Any]]:
        return read_usb(ignore)

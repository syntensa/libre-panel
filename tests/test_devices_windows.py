"""The Windows half of the devices service: drives and storage through DeviceIoControl,
controllers through XInput, USB devices through SetupAPI and the notices of the configuration
manager. Windows' calls are faked below the code (they fill the buffers the code gives them);
no Windows is needed."""

import ctypes
import struct

from libre_panel.services import devices_windows as windows

# -- drives ------------------------------------------------------------------------------------


def descriptor(vendor="", product="", bus=7):
    """A STORAGE_DEVICE_DESCRIPTOR: 36 bytes of header (the offsets of the texts at 12 and 16,
    the bus at 28), then the texts."""
    raw, tail = bytearray(36), b""
    offsets = []
    for text in (vendor, product):
        offsets.append(36 + len(tail) if text else 0)
        tail += text.encode() + b"\0" if text else b""
    struct.pack_into("<II", raw, 12, *offsets)
    struct.pack_into("<I", raw, 28, bus)
    return bytes(raw) + tail


def temperature(temps, warn, crit):
    """A STORAGE_TEMPERATURE_DATA_DESCRIPTOR with one 16-byte entry per sensor."""
    raw = bytearray(24 + 16 * len(temps))
    struct.pack_into("<hhH", raw, 8, crit, warn, len(temps))
    for i, value in enumerate(temps):
        struct.pack_into("<h", raw, 24 + 16 * i + 2, value)
    return bytes(raw)


class FakeKernel:
    """kernel32 over a made-up set of drives: ``{letter: {kind, bus, vendor, product, label,
    free, total, ready, temps, warn, crit, opens}}``. It writes into the buffers it is given."""

    def __init__(self, drives):
        self.drives = drives
        self.mode, self.modes = 0, []
        self.opened, self.closed, self.queries = [], [], []

    def SetErrorMode(self, mode):  # noqa: N802 - Windows' names
        before, self.mode = self.mode, mode
        self.modes.append(mode)
        return before

    def GetLogicalDrives(self):
        return sum(1 << (ord(letter) - 65) for letter in self.drives)

    def GetDriveTypeW(self, root):
        drive = self.drives.get(root[0])
        return drive["kind"] if drive else 1  # "no root directory"

    def CreateFileW(self, path, access, share, security, disposition, flags, template):
        letter = path[-2]
        self.opened.append((path, access, share, disposition))
        return (
            windows.INVALID_HANDLE
            if self.drives[letter].get("opens") is False
            else 1000 + ord(letter)
        )

    def DeviceIoControl(self, handle, code, query, size, out, out_size, returned, overlapped):
        assert code == windows.IOCTL_STORAGE_QUERY_PROPERTY and out_size == 1024
        drive = self.drives[chr(handle - 1000)]
        prop, kind = struct.unpack_from("<II", query)
        assert kind == 0 and len(query) == size  # a plain query
        self.queries.append((chr(handle - 1000), prop))
        if prop == windows.STORAGE_DEVICE_PROPERTY:
            data = descriptor(drive.get("vendor", ""), drive.get("product", ""), drive["bus"])
        elif prop == windows.STORAGE_ADAPTER_TEMPERATURE_PROPERTY and "temps" in drive:
            data = temperature(drive["temps"], drive.get("warn", 0), drive.get("crit", 0))
        else:
            return 0  # the drive does not know that property
        ctypes.memmove(out, data, len(data))
        returned._obj.value = len(data)
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1

    def GetDiskFreeSpaceExW(self, root, free, total, other):  # noqa: N802
        drive = self.drives[root[0]]
        if drive.get("ready") is False:
            return 0  # a card reader without a card
        free._obj.value, total._obj.value = drive["free"], drive["total"]
        return 1

    def GetVolumeInformationW(self, root, label, size, *rest):  # noqa: N802
        label.value = self.drives[root[0]].get("label", "")
        return 1


GB = 1000**3
DRIVES = {
    "C": {
        "kind": 3,
        "bus": 17,
        "vendor": "Maker",
        "product": "System SSD",
        "free": 100 * GB,
        "total": 500 * GB,
        "temps": [41, 38],
        "warn": 70,
        "crit": 85,
    },  # fmt: skip
    "D": {"kind": 3, "bus": 7, "product": "External SSD", "free": 200 * GB, "total": 1000 * GB},
    "E": {
        "kind": 2,
        "bus": 7,
        "vendor": "Kingston",
        "product": "DataTraveler",
        "label": "STICK",
        "free": 20 * GB,
        "total": 30 * GB,
    },  # fmt: skip
    "F": {"kind": 2, "bus": 12, "label": "", "free": 5 * GB, "total": 64 * GB},
    "G": {"kind": 2, "bus": 12, "ready": False, "free": 0, "total": 0},
    "H": {"kind": 5, "bus": 3, "free": 0, "total": 0},  # an optical drive
    "N": {"kind": 4, "bus": 0, "free": 1, "total": 2},  # a network drive
}


def test_the_descriptors_are_read_by_their_layout():
    assert windows.parse_device(descriptor("Kingston ", " DataTraveler", 7)) == (
        7,
        "Kingston DataTraveler",
    )
    assert windows.parse_device(descriptor("", "Only product", 12)) == (12, "Only product")
    assert windows.parse_device(descriptor("", "", 17)) == (17, "")
    assert windows.parse_device(b"short") == (None, "")
    assert windows.parse_temperature(temperature([41, 55, 38], 70, 85)) == (55.0, 70.0, 85.0)
    assert windows.parse_temperature(temperature([30], 0, -5)) == (
        30.0,
        None,
        None,
    )  # no limits told
    assert windows.parse_temperature(temperature([-100, 200], 70, 85))[0] is None  # nonsense values
    assert windows.parse_temperature(b"") == (None, None, None)
    assert windows.parse_temperature(temperature([], 70, 85)) == (None, None, None)


def test_drives_that_come_and_go_are_removable_ones_and_external_fixed_ones():
    kernel = FakeKernel(DRIVES)
    found = windows.read_drives(kernel)
    assert sorted(found) == [
        "D",
        "E",
        "F",
    ]  # not the internal NVMe, an empty reader, a disc, the network
    assert found["E"] == {
        "name": "Kingston DataTraveler", "label": "STICK", "free": 20 * GB,
        "total": 30 * GB, "bus": "USB",
    }  # fmt: skip
    assert found["D"]["bus"] == "USB" and found["F"]["bus"] == "SD" and found["F"]["name"] == ""
    assert kernel.modes == [windows.SEM_FAILCRITICALERRORS, 0]  # no "insert a disk" box, and undone


def test_a_drive_is_opened_without_any_access_and_always_closed():
    kernel = FakeKernel(DRIVES)
    windows.read_drives(kernel)
    assert {path for path, *_ in kernel.opened} >= {"\\\\.\\E:", "\\\\.\\F:"}
    assert all(
        access == 0 and share == 3 and disposition == 3
        for _p, access, share, disposition in kernel.opened
    )
    assert sorted(kernel.closed) == sorted(1000 + ord(path[-2]) for path, *_ in kernel.opened)


def test_a_removable_drive_that_cannot_be_asked_still_comes_without_a_name():
    drives = {"E": {**DRIVES["E"], "opens": False}}
    found = windows.read_drives(FakeKernel(drives))
    assert found["E"]["name"] == "" and found["E"]["bus"] == "USB"  # the volume label can name it
    fixed = {"D": {**DRIVES["D"], "opens": False}}
    assert windows.read_drives(FakeKernel(fixed)) == {}  # a fixed one is external only by its bus


def test_storage_is_the_fixed_drives_with_the_temperature_they_tell():
    kernel = FakeKernel(DRIVES)
    found = windows.read_storage(kernel)
    assert sorted(found) == ["C", "D"]
    assert found["C"] == {
        "name": "Maker System SSD", "free": 100 * GB, "total": 500 * GB,
        "temp": 41.0, "warn": 70.0, "crit": 85.0,
    }  # fmt: skip
    assert (
        found["D"]["temp"] is None and found["D"]["warn"] is None
    )  # it does not know that property
    assert kernel.modes == [windows.SEM_FAILCRITICALERRORS, 0]
    assert (("C", windows.STORAGE_ADAPTER_TEMPERATURE_PROPERTY)) in kernel.queries
    assert not any(
        letter in "EFGHN" for letter, _prop in kernel.queries
    )  # only fixed drives are asked


# -- controllers ---------------------------------------------------------------------------------


class FakeXInput:
    """xinput1_4: pads by slot, ``{flags, type, level}``; no ``type`` means no battery answer."""

    def __init__(self, pads):
        self.pads, self.calls = pads, []

    def XInputGetCapabilities(self, slot, flags, caps):  # noqa: N802
        self.calls.append(("caps", slot, flags))
        if slot not in self.pads:
            return 1167  # ERROR_DEVICE_NOT_CONNECTED
        caps._obj.Flags = self.pads[slot]["flags"]
        return 0

    def XInputGetBatteryInformation(self, slot, devtype, battery):  # noqa: N802
        self.calls.append(("battery", slot, devtype))
        pad = self.pads[slot]
        if "type" not in pad:
            return 1
        battery._obj.Type, battery._obj.Level = pad["type"], pad["level"]
        return 0


def test_the_controllers_come_with_their_slot_and_battery():
    xinput = FakeXInput({
        0: {"flags": 0x0002, "type": 3, "level": 1},  # wireless, rechargeable, low
        2: {"flags": 0, "type": 1, "level": 3},  # a cable
        3: {"flags": 0x0002},  # wireless: no battery answer yet
    })  # fmt: skip
    found = windows.read_controllers(xinput)
    assert found == {
        0: {"wireless": True, "battery": 3, "level": 1},
        2: {"wireless": False, "battery": 1, "level": 3},
        3: {"wireless": True, "battery": None, "level": None},
    }  # fmt: skip
    assert [c for c in xinput.calls if c[0] == "caps"] == [("caps", slot, 1) for slot in range(4)]
    assert ("battery", 0, 0) in xinput.calls  # the gamepad's, not a headset's


def test_without_xinput_there_are_no_controllers(monkeypatch):
    monkeypatch.setattr(windows, "_xinput", lambda: None)
    assert windows.read_controllers() == {}


def test_the_structures_have_the_layout_windows_expects():
    assert ctypes.sizeof(windows._Capabilities) == 20 and ctypes.sizeof(windows._Battery) == 2
    assert ctypes.sizeof(windows._Filter) == 416  # Windows checks the size of this one
    assert ctypes.sizeof(windows._DeviceInfo) == (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert bytes(windows._BUS_REPORTED_NAME.fmtid)[:4] == bytes.fromhex(
        "7e940b54"
    )  # {540B947E-...}
    assert windows._BUS_REPORTED_NAME.pid == 4


# -- USB devices -----------------------------------------------------------------------------------


class FakeSetupApi:
    """SetupAPI over a made-up list of devices ``{id, name, friendly, desc, klass, service}``."""

    def __init__(self, devices, handle=77):
        self.devices, self.handle = devices, handle
        self.destroyed, self.asked = [], []

    def SetupDiGetClassDevsW(self, guid, enumerator, window, flags):  # noqa: N802
        self.asked.append((enumerator, flags))
        return self.handle

    def SetupDiEnumDeviceInfo(self, handle, index, info):  # noqa: N802
        assert handle == self.handle and info._obj.cbSize == ctypes.sizeof(windows._DeviceInfo)
        if index >= len(self.devices):
            return 0
        info._obj.DevInst = index  # what Windows would fill: the code only passes it back
        return 1

    def SetupDiGetDeviceInstanceIdW(self, handle, info, buffer, size, needed):  # noqa: N802
        buffer.value = self.devices[info._obj.DevInst]["id"]
        return 1

    def SetupDiGetDevicePropertyW(self, handle, info, key, kind, buffer, size, needed, flags):  # noqa: N802
        assert key._obj.pid == 4 and key._obj.fmtid.Data1 == 0x540B947E
        name = self.devices[info._obj.DevInst].get("name")
        if not name:
            return 0
        buffer.value = name
        return 1

    def SetupDiGetDeviceRegistryPropertyW(self, handle, info, field, kind, buffer, size, needed):  # noqa: N802
        device = self.devices[info._obj.DevInst]
        key = {windows.SPDRP_FRIENDLYNAME: "friendly", windows.SPDRP_DEVICEDESC: "desc",
               windows.SPDRP_CLASS: "klass", windows.SPDRP_SERVICE: "service"}[field]  # fmt: skip
        if not device.get(key):
            return 0
        buffer.value = device[key]
        return 1

    def SetupDiDestroyDeviceInfoList(self, handle):  # noqa: N802
        self.destroyed.append(handle)
        return 1


def device(ident, name="", klass="", service="", friendly="", desc=""):
    return {"id": ident, "name": name, "klass": klass, "service": service,
            "friendly": friendly, "desc": desc}  # fmt: skip


PHONE = "USB\\VID_18D1&PID_4EE1\\0123456789ABCDEF"
USB_DEVICES = [
    device(PHONE, "Android Phone", "WPD", "WUDFWpdMtp"),
    device("USB\\VID_8087&PID_0024\\5&2A3B4C5D&0&1", "Generic USB Hub", "USB", "USBHUB3"),
    device("USB\\VID_0781&PID_5567\\4C530001", "USB Mass Storage Device", "USB", "USBSTOR"),
    device("USB\\VID_045E&PID_028E\\1&2&3", "Controller", "XnaComposite", "xusb22"),
    device("USB\\VID_18D1&PID_4EE1&MI_00\\6&1A2B3C4D&0&0000", "Phone (interface)", "WPD"),
    device("USB\\VID_1CBE&PID_0092\\ABCDEF", "Display Panel", "USB"),
    device("USB\\ROOT_HUB30\\4&1A2B&0&0", "USB Root Hub (USB 3.0)", "USB", "USBHUB3"),
    device(
        "USB\\VID_046D&PID_C31C\\7&88", "", "HIDClass", "kbdhid", "USB Keyboard", "USB Input Device"
    ),
    device("USB\\VID_1A2B&PID_3C4D\\SER", "", "USB", "winusb", "", "Some Gadget"),
    device("USB\\VID_0A5C&PID_21E8\\6&2&1", "Wireless Hub Dongle", "Bluetooth", "BTHUSB"),
]


def test_the_usb_devices_that_plug_into_the_machine_are_listed_without_the_rest():
    api = FakeSetupApi(USB_DEVICES)
    found = windows.read_usb({"1CBE:0092"}, api)
    assert sorted(entry["vid"] for entry in found.values()) == ["046D", "0A5C", "18D1", "1A2B"]
    phone = found[PHONE]
    assert phone == {
        "vid": "18D1",
        "pid": "4EE1",
        "name": "Android Phone",
        "klass": "WPD",
        "service": "WUDFWpdMtp",
    }
    # no hub, no mass storage, no Xbox controller, no part of a composite device, no panel
    assert not any(key.startswith(("USB\\ROOT", "USB\\VID_1CBE", "USB\\VID_8087")) for key in found)
    assert "&MI_" not in "".join(found)
    assert api.asked == [("USB", windows.DIGCF_PRESENT | windows.DIGCF_ALLCLASSES)]
    assert api.destroyed == [77]  # the list is always given back


def test_a_usb_device_is_named_by_what_it_says_then_by_the_system():
    found = windows.read_usb((), FakeSetupApi(USB_DEVICES))
    assert found["USB\\VID_046D&PID_C31C\\7&88"]["name"] == "USB Keyboard"  # the friendly name
    assert found["USB\\VID_1A2B&PID_3C4D\\SER"]["name"] == "Some Gadget"  # the description
    assert (
        found["USB\\VID_0A5C&PID_21E8\\6&2&1"]["name"] == "Wireless Hub Dongle"
    )  # a "hub" that is not USB class


def test_the_panel_is_enumerated_only_to_be_left_out():
    found = windows.read_usb({"1CBE:0092"}, FakeSetupApi(USB_DEVICES))
    assert "USB\\VID_1CBE&PID_0092\\ABCDEF" not in found
    shown = windows.read_usb((), FakeSetupApi(USB_DEVICES))  # without the list it is there
    assert "USB\\VID_1CBE&PID_0092\\ABCDEF" in shown


def test_no_device_list_means_no_devices_and_nothing_to_give_back():
    api = FakeSetupApi(USB_DEVICES, handle=windows.INVALID_HANDLE)
    assert windows.read_usb((), api) == {} and api.destroyed == []
    assert windows.read_usb((), FakeSetupApi(USB_DEVICES, handle=None)) == {}


def test_what_is_kept_of_the_usb_devices():
    keep = windows.keep_usb
    assert keep(
        {"vid": "18D1", "pid": "4EE1", "name": "Phone", "klass": "WPD", "service": ""}, set()
    )
    assert not keep({"vid": "18D1", "pid": "4EE1", "name": "Phone"}, {"18D1:4EE1"})
    assert not keep({"vid": "0781", "pid": "5567", "service": "UASPStor"}, set())  # any case
    assert not keep({"vid": "8087", "pid": "0024", "name": "Root HUB", "klass": "usb"}, set())
    assert keep({"vid": "8087", "pid": "0024", "name": "Hub of Things", "klass": "Other"}, set())


# -- Windows' notices of a device that comes or goes -----------------------------------------------


class FakeConfigManager:
    """cfgmgr32: keeps the filter and the callback it is given, and gives a handle."""

    def __init__(self, result=0, handle=99):
        self.result, self.handle = result, handle
        self.filter = self.callback = self.registered_handle = None
        self.unregistered = []

    def CM_Register_Notification(self, filter_ref, context, callback, handle_ref):  # noqa: N802
        flt = filter_ref._obj
        self.filter = (flt.cbSize, flt.Flags, flt.FilterType)
        self.callback = callback
        if self.result == 0:
            handle_ref._obj.value = self.handle
        return self.result

    def CM_Unregister_Notification(self, handle):  # noqa: N802
        self.unregistered.append(handle.value)
        return 0

    def windows_notices(self, action):
        """What Windows does: call the function it was given, with five arguments."""
        return self.callback(self.handle, None, action, None, 0)


def test_the_notifier_registers_for_every_device_interface():
    cm = FakeConfigManager()
    heard = []
    notifier = windows.ChangeNotifier(cm)
    assert notifier.start(lambda: heard.append(1)) is True
    assert cm.filter == (416, windows.CM_NOTIFY_FILTER_FLAG_ALL_INTERFACE_CLASSES, 0)
    assert cm.windows_notices(windows.CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL) == 0
    assert cm.windows_notices(windows.CM_NOTIFY_ACTION_DEVICEINTERFACEREMOVAL) == 0
    assert len(heard) == 2 and notifier.notices == 2
    assert cm.windows_notices(2) == 0 and len(heard) == 2  # other actions are no news
    notifier.stop()
    assert cm.unregistered == [99]
    notifier.stop()  # twice is fine
    assert cm.unregistered == [99]


def test_an_error_in_the_function_never_reaches_windows():
    cm = FakeConfigManager()

    def broken():
        raise RuntimeError("boom")

    windows.ChangeNotifier(cm).start(broken)
    assert cm.windows_notices(windows.CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL) == 0


def test_where_windows_refuses_there_are_no_notices_and_the_poll_goes_on_alone(monkeypatch):
    assert windows.ChangeNotifier(FakeConfigManager(result=5)).start(lambda: None) is False
    assert windows.ChangeNotifier(FakeConfigManager(handle=0)).start(lambda: None) is False

    def missing():
        raise OSError("no cfgmgr32")

    monkeypatch.setattr(windows, "_cfgmgr", missing)
    notifier = windows.ChangeNotifier()
    assert notifier.start(lambda: None) is False
    notifier.stop()  # never registered: nothing to undo


def test_the_readers_are_the_four_functions(monkeypatch):
    monkeypatch.setattr(windows, "read_drives", lambda: {"E": 1})
    monkeypatch.setattr(windows, "read_storage", lambda: {"C": 2})
    monkeypatch.setattr(windows, "read_controllers", lambda: {0: 3})
    monkeypatch.setattr(windows, "read_usb", lambda ignore: {"x": sorted(ignore)})
    readers = windows.Readers()
    assert readers.drives() == {"E": 1} and readers.storage() == {"C": 2}
    assert readers.controllers() == {0: 3} and readers.usb({"B", "A"}) == {"x": ["A", "B"]}

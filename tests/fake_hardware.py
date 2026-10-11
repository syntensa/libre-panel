"""Computers by hand, for the tests of the hardware names: a Windows registry and its
firmware table, a Linux with its /proc and /sys, and the SMBIOS structures in them.

The names in here are public products; none belongs to whoever runs the tests."""

import ctypes
import struct
from types import SimpleNamespace

from libre_panel.sensors.hardware import Adapter, Found
from libre_panel.sensors.smbios import Module


def gigabytes(n):
    return n * 1024**3


# A computer of the kind most people have, named the way a system names its parts, and the
# names a panel shows for it.
PC = Found(
    cpu="AMD Ryzen 5 7600 6-Core Processor",
    adapters=(Adapter("NVIDIA GeForce RTX 4070", gigabytes(12)),),
    modules=(
        Module(16384, "DDR5", 5600, "Kingston Technology Company, Inc."),
        Module(16384, "DDR5", 5600, "Kingston Technology Company, Inc."),
    ),
    memory=int(31.8 * 2**30),
)
TIDY = {"cpu": "Ryzen 5 7600", "gpu": "GeForce RTX 4070", "memory": "Kingston DDR5-5600 32 GB"}

# -- SMBIOS ---------------------------------------------------------------------------------

DDR4, DDR5, LPDDR5 = 0x1A, 0x22, 0x23


def structure(kind, body=b"", strings=(), length=None):
    """One SMBIOS structure: the fixed part (``body`` after the four bytes of header), the
    strings, each ended by a zero byte, and one more zero."""
    length = length or 4 + len(body)
    fixed = bytes([kind, length]) + struct.pack("<H", 0x0100 + kind) + body.ljust(length - 4, b"\0")
    text = b"".join(s.encode() + b"\0" for s in strings) or b"\0"
    return fixed + text + b"\0"


def memory_device(
    megabytes=16384,
    kind=DDR5,
    speed=5600,
    configured=None,
    maker="Kingston",
    size_field=None,
    detail=0x0080,
    length=0x5C,
):
    """A type 17 structure as a firmware writes it. The serial number, the asset tag and
    the part number are in it, as in a real one: the reader must leave them alone."""
    strings = ["DIMM A1", "BANK 0", maker, "SERIAL-1234", "ASSET-TAG", "PART-NUMBER-X"]
    body = bytearray(length - 4)

    def put(offset, fmt, value):
        if offset + struct.calcsize(fmt) <= length:  # an older firmware stops sooner
            struct.pack_into(fmt, body, offset - 4, value)

    if size_field is None:
        size_field = megabytes if megabytes < 0x7FFF else 0x7FFF
    put(0x0C, "<H", size_field)
    put(0x10, "<B", 1)  # device locator: string 1
    put(0x11, "<B", 2)
    put(0x12, "<B", kind)
    put(0x13, "<H", detail)
    put(0x15, "<H", speed)
    put(0x17, "<B", 3 if maker else 0)  # manufacturer: string 3
    put(0x18, "<B", 4)  # serial number
    put(0x19, "<B", 5)  # asset tag
    put(0x1A, "<B", 6)  # part number
    put(0x1C, "<I", megabytes)  # the extended size
    if configured is not None:
        put(0x20, "<H", configured)
    return structure(17, bytes(body), strings, length)


def smbios_table(*structures):
    """Structures one after the other, a "physical memory array" in front and the end after."""
    return structure(16, bytes(11)) + b"".join(structures) + structure(127, b"")


def windows_blob(table):
    """What GetSystemFirmwareTable('RSMB') gives: four bytes of version, the length, the table."""
    return bytes([0, 3, 4, 0]) + struct.pack("<I", len(table)) + table


def fake_kernel32(blob):
    """``kernel32`` with a GetSystemFirmwareTable that has ``blob`` (None: no table)."""
    calls = []

    def get(signature, table_id, buffer, size):
        calls.append((signature, table_id, size))
        if blob is None:
            return 0
        if buffer is not None:
            ctypes.memmove(buffer, blob, min(size, len(blob)))
        return len(blob)

    return SimpleNamespace(GetSystemFirmwareTable=get, calls=calls)


# -- Windows --------------------------------------------------------------------------------

CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
DISPLAY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRegistry:
    """The parts of ``winreg`` Libre Panel uses, over ``{path: {value name: value}}``."""

    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, keys, denied=()):
        self.keys = dict(keys)
        self.denied = set(denied)  # keys that are there but may not be opened

    def _exists(self, path):
        return path in self.keys or any(key.startswith(path + "\\") for key in self.keys)

    def OpenKey(self, parent, sub):  # noqa: N802 - winreg's names
        path = sub if parent == self.HKEY_LOCAL_MACHINE else f"{parent.path}\\{sub}"
        if not self._exists(path):
            raise FileNotFoundError(path)
        if path in self.denied:
            raise PermissionError(path)
        return FakeKey(path)

    def EnumKey(self, key, index):  # noqa: N802
        prefix = key.path + "\\"
        names = sorted({k[len(prefix) :].split("\\")[0] for k in self.keys if k.startswith(prefix)})
        if index >= len(names):
            raise OSError("no more data")
        return names[index]

    def QueryValueEx(self, key, name):  # noqa: N802
        values = self.keys.get(key.path, {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 0


def adapter_key(number, name, **values):
    return f"{DISPLAY}\\{number:04d}", {"DriverDesc": name, **values}


def windows_registry(cpu="", *adapters):
    """A registry with a processor and display adapters, each ``(name, values)``."""
    keys = {CPU_KEY: {"ProcessorNameString": cpu}} if cpu else {}
    keys[DISPLAY + "\\Configuration"] = {}  # not an adapter
    keys[DISPLAY + "\\Properties"] = {"x": 1}
    for number, (name, values) in enumerate(adapters):
        path, found = adapter_key(number, name, **values)
        keys[path] = found
    return FakeRegistry(keys)


# -- Linux ----------------------------------------------------------------------------------

PCI_IDS = """\
# A list of PCI ids (a few of them)
1002  Advanced Micro Devices, Inc. [AMD/ATI]
\t7340  Navi 14 [Radeon RX 5500/5500M / Pro 5500M]
\t744c  Navi 31 [Radeon RX 7900 XT/7900 XTX/7900M]
\t\t1002 0e3a  Navi 31 [Radeon RX 7900 XTX]
10de  NVIDIA Corporation
\t1c82  GP107 [GeForce GTX 1050 Ti]
\t2786  AD104 [GeForce RTX 4070]
\t\t10de 1234  A subsystem of the 2786
8086  Intel Corporation
\t1912  HD Graphics 530
\t4680  AlderLake-S GT1 [UHD Graphics 770]
\t4905  DG1 [Iris Xe MAX Graphics]
C 00  Unclassified device
\t00  Non-VGA unclassified device
"""

AMDGPU_IDS = """\
# List of AMDGPU IDs
1.0.0
744C,\tC8,\tAMD Radeon RX 7900 XT
744C,\tCC,\tAMD Radeon RX 7900 XTX
7340,\tC5,\tAMD Radeon RX 5500 XT
"""

NVIDIA_INFORMATION = """\
Model: \t\t NVIDIA GeForce RTX 4070
IRQ:   \t\t 142
GPU UUID: \t GPU-00000000-0000-0000-0000-000000000000
Video BIOS: \t 95.04.1e.00.74
Bus Type: \t PCIe
Bus Location: \t 0000:01:00.0
"""


class LinuxPC:
    """A Linux machine's files in a folder. Fill it with ``card``, ``cpuinfo`` and so on;
    ``paths`` are the arguments the readers take."""

    def __init__(self, root):
        self.root = root
        self.cpuinfo = root / "proc" / "cpuinfo"
        self.drm = root / "sys" / "class" / "drm"
        self.nvidia = root / "proc" / "driver" / "nvidia" / "gpus"
        self.pci_ids = [root / "usr" / "share" / "hwdata" / "pci.ids"]
        self.amdgpu_ids = root / "usr" / "share" / "libdrm" / "amdgpu.ids"
        self.dmi = root / "sys" / "firmware" / "dmi" / "entries"
        self.drm.mkdir(parents=True)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def card(self, number, vendor, device, revision=None, slot=None, vram=None, connector=True):
        folder = self.drm / f"card{number}" / "device"
        self.write(folder / "vendor", f"0x{vendor:04x}\n")
        self.write(folder / "device", f"0x{device:04x}\n")
        if revision is not None:
            self.write(folder / "revision", f"0x{revision:02x}\n")
        if slot:
            self.write(folder / "uevent", f"DRIVER=x\nPCI_SLOT_NAME={slot}\nPCI_ID=0:0\n")
        if vram is not None:
            self.write(folder / "mem_info_vram_total", f"{vram}\n")
        if connector:  # the connectors are folders of their own: card0-DP-1
            (self.drm / f"card{number}-DP-1").mkdir(exist_ok=True)

    def modules(self, *structures):
        for number, data in enumerate(structures):
            folder = self.dmi / f"17-{number}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "raw").write_bytes(data)
            (folder / "type").write_text("17\n")

    def ids(self):
        self.write(self.pci_ids[0], PCI_IDS)
        self.write(self.amdgpu_ids, AMDGPU_IDS)

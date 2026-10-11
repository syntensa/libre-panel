"""The memory modules in the firmware's SMBIOS table, for the memory's name.

The table has one "type 17" structure per memory slot: who made the module, what kind of
memory it is, how fast it runs and how big it is. Windows hands the table over whole
(``GetSystemFirmwareTable``: no WMI, no program, no administrator rights); Linux has a file
per structure under ``/sys/firmware/dmi/entries``, which most systems let only root read.

Of a structure only what names the kind of memory is taken: serial numbers, asset tags and
part numbers (they name one module) stay where they are.
"""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MEMORY_DEVICE = 17  # the structure type of a memory slot
END_OF_TABLE = 127
ENTRIES = Path("/sys/firmware/dmi/entries")

# The memory types (byte 0x12 of a slot) that say something about the memory.
MEMORY_TYPES = {
    0x0F: "SDRAM", 0x12: "DDR", 0x13: "DDR2", 0x14: "DDR2", 0x18: "DDR3", 0x1A: "DDR4",
    0x1B: "LPDDR", 0x1C: "LPDDR2", 0x1D: "LPDDR3", 0x1E: "LPDDR4", 0x20: "HBM", 0x21: "HBM2",
    0x22: "DDR5", 0x23: "LPDDR5", 0x24: "HBM3",
}  # fmt: skip
_NON_VOLATILE_TYPE = 0x1F  # a "logical non-volatile device"
_NON_VOLATILE_DETAIL = 0x1000  # bit 12 of the type detail


@dataclass(frozen=True)
class Module:
    """One memory module that is in its slot."""

    megabytes: int
    kind: str = ""  # "DDR5"
    speed: int = 0  # megatransfers a second it runs at; 0: not told
    maker: str = ""  # as the firmware spells it


def structures(data: bytes) -> Iterator[tuple[int, bytes, list[bytes]]]:
    """The structures of a table, each as (type, its fixed part, its strings). A structure
    is its fixed part, then its strings (each ends with a zero byte), then one more zero."""
    pos = 0
    while pos + 4 <= len(data):
        kind, length = data[pos], data[pos + 1]
        if length < 4 or pos + length > len(data):
            return  # not a table, or cut off
        start = pos + length
        stop = data.find(b"\0\0", start)
        end = len(data) if stop < 0 else stop
        text = data[start:end]
        yield kind, data[pos:start], text.split(b"\0") if text else []
        if kind == END_OF_TABLE or stop < 0:
            return
        pos = stop + 2


def _text(strings: list[bytes], index: int) -> str:
    """String number ``index`` (counted from 1; 0: none)."""
    if not 1 <= index <= len(strings):
        return ""
    return strings[index - 1].decode("utf-8", errors="replace").strip()


def _module(body: bytes, strings: list[bytes]) -> Module | None:
    if len(body) < 0x15:  # SMBIOS 2.1 is the first to say how big a module is
        return None
    (size,) = struct.unpack_from("<H", body, 0x0C)
    if size in (0, 0xFFFF):  # nothing in the slot, or not told
        return None
    if size == 0x7FFF:  # 32 GB or more: the size is in the extended field
        if len(body) < 0x20:
            return None
        megabytes = struct.unpack_from("<I", body, 0x1C)[0] & 0x7FFFFFFF
    elif size & 0x8000:  # counted in kilobytes
        megabytes = (size & 0x7FFF) // 1024
    else:
        megabytes = size
    kind_id = body[0x12]
    (detail,) = struct.unpack_from("<H", body, 0x13)
    if not megabytes or kind_id == _NON_VOLATILE_TYPE or detail & _NON_VOLATILE_DETAIL:
        return None  # memory that keeps its data off is no working memory
    speeds = []
    if len(body) >= 0x22:  # SMBIOS 2.7: the speed it runs at comes first
        speeds.append(struct.unpack_from("<H", body, 0x20)[0])
    if len(body) >= 0x17:  # SMBIOS 2.3: the speed it is made for, and who made it
        speeds.append(struct.unpack_from("<H", body, 0x15)[0])
    speed = next((s for s in speeds if s not in (0, 0xFFFF)), 0)
    maker = _text(strings, body[0x17]) if len(body) > 0x17 else ""
    return Module(megabytes, MEMORY_TYPES.get(kind_id, ""), speed, maker)


def memory_modules(data: bytes) -> list[Module]:
    """The modules in a table (or in the structures read from Linux's files)."""
    found = []
    for kind, body, strings in structures(data):
        module = _module(body, strings) if kind == MEMORY_DEVICE else None
        if module is not None:
            found.append(module)
    return found


def windows_table(kernel32: Any = None) -> bytes:
    """The SMBIOS table Windows has (the firmware table ``RSMB``); raises OSError without one.
    ``kernel32`` is for tests."""
    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32")  # only Windows has it: imported when asked
    get = kernel32.GetSystemFirmwareTable
    get.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    get.restype = ctypes.c_uint32
    signature = int.from_bytes(b"RSMB", "big")
    size = get(signature, 0, None, 0)
    if not size:
        raise OSError("this computer has no SMBIOS table")
    buffer = ctypes.create_string_buffer(size)
    got = get(signature, 0, buffer, size)
    if not 8 <= got <= size:
        raise OSError("the SMBIOS table could not be read")
    raw = buffer.raw[:got]
    # RawSMBIOSData: four bytes about the version, the length of the table, the table
    (length,) = struct.unpack_from("<I", raw, 4)
    return raw[8 : 8 + length]


def linux_table(entries: Path = ENTRIES) -> bytes:
    """The memory structures of ``/sys/firmware/dmi/entries``, one after the other; what the
    user may not read (usually all of it) is left out."""
    data = b""
    for path in sorted(entries.glob(f"{MEMORY_DEVICE}-*/raw")):
        try:
            data += path.read_bytes()
        except OSError:
            continue
    return data

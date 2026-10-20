"""Which threads of the processor belong to one core: the physical cores, as Windows and
Linux tell them, so that a list of the cores shows eight cores of two threads each rather
than sixteen thin bars.

A core's threads are given as their places in psutil's list of the processor's threads
(``psutil.cpu_times(percpu=True)``), the order the load source measures them in. Where
that cannot be told for sure (more than 64 threads on Windows, threads that are offline on
Linux, macOS) there are no cores, and the lists show the threads as before.
"""

from __future__ import annotations

import logging
import struct
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_RELATION_PROCESSOR_CORE = 0  # LOGICAL_PROCESSOR_RELATIONSHIP
_SYSFS = Path("/sys/devices/system/cpu")


def parse_processor_cores(raw: bytes) -> list[list[int]]:
    """The cores in what GetLogicalProcessorInformationEx(RelationProcessorCore) wrote: one
    SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX after another, each a PROCESSOR_RELATIONSHIP with
    its GROUP_AFFINITY masks (a thread is group * 64 + its bit)."""
    cores: list[list[int]] = []
    at = 0
    while at + 8 <= len(raw):
        relation, size = struct.unpack_from("<II", raw, at)
        if size < 8 or at + size > len(raw):
            break
        if relation == _RELATION_PROCESSOR_CORE:
            # PROCESSOR_RELATIONSHIP: Flags, EfficiencyClass, Reserved[20], GroupCount, then
            # GroupMask[GroupCount] (KAFFINITY Mask, WORD Group, WORD Reserved[3]) from 24 on
            (groups,) = struct.unpack_from("<H", raw, at + 8 + 22)
            threads = []
            for g in range(groups):
                mask, group = struct.unpack_from("<QH", raw, at + 8 + 24 + 16 * g)
                threads += [group * 64 + bit for bit in range(64) if mask >> bit & 1]
            cores.append(sorted(threads))
        at += size
    return cores


def _windows_cores() -> list[list[int]] | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ask = kernel32.GetLogicalProcessorInformationEx
    ask.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    ask.restype = wintypes.BOOL
    size = wintypes.DWORD(0)
    ask(_RELATION_PROCESSOR_CORE, None, ctypes.byref(size))  # (how large the answer is)
    if not size.value:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if not ask(_RELATION_PROCESSOR_CORE, buffer, ctypes.byref(size)):
        return None
    return parse_processor_cores(buffer.raw[: size.value])


def _linux_cores(root: Path = _SYSFS) -> list[list[int]] | None:
    """The threads of each core from sysfs (package and core id of every thread that is
    online), as their places among the online threads."""
    found: dict[tuple[str, str], list[int]] = {}
    numbers = []
    for cpu in root.glob("cpu[0-9]*"):
        try:
            number = int(cpu.name[3:])
            package = (cpu / "topology" / "physical_package_id").read_text().strip()
            core = (cpu / "topology" / "core_id").read_text().strip()
        except (OSError, ValueError):
            continue  # (an offline thread has no topology)
        numbers.append(number)
        found.setdefault((package, core), []).append(number)
    if not numbers:
        return None
    place = {number: i for i, number in enumerate(sorted(numbers))}
    return [sorted(place[n] for n in threads) for threads in found.values()]


def physical_cores(threads: int, platform: str = sys.platform) -> list[list[int]] | None:
    """The cores of this processor as lists of their threads' places (0 to ``threads`` - 1),
    in the order of their first thread; None where a core has only one thread (nothing to
    group) or the cores cannot be told for sure."""
    try:
        if platform == "win32":
            cores = _windows_cores()
        elif platform.startswith("linux"):
            cores = _linux_cores()
        else:
            return None
    except Exception as exc:  # noqa: BLE001 - the threads are shown instead
        log.debug("no processor topology: %s", exc)
        return None
    return checked(cores, threads)


def checked(cores: list[list[int]] | None, threads: int) -> list[list[int]] | None:
    """``cores`` if they hold each of the ``threads`` once and one has more than one."""
    if not cores:
        return None
    every = sorted(t for core in cores for t in core)
    if every != list(range(threads)) or all(len(core) == 1 for core in cores):
        return None
    return sorted(cores, key=lambda core: core[0])

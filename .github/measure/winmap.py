"""MEASUREMENT (feature branch only): what a Windows process's memory is made of, from within.

Every committed region of its address space (VirtualQuery), how much of each is in its working
set and private (QueryWorkingSetEx: the pages Task Manager's Memory column counts), and what
owns it: a heap (its regions, its large blocks; HeapWalk), a program file (an image), a mapped
file, a thread's stack, Python's arenas (1 MiB each), or other memory allocated directly
(VirtualAlloc)."""

import collections
import ctypes
import os
import time
from ctypes import wintypes

import numpy as np

MB = 1 << 20
PAGE = 4096
MEM_COMMIT = 0x1000
MEM_PRIVATE, MEM_MAPPED, MEM_IMAGE = 0x20000, 0x40000, 0x1000000
PAGE_GUARD = 0x100
REGION, UNCOMMITTED, BUSY = 0x0001, 0x0002, 0x0004
TOP = 0x7FFF_FFFF_0000  # the end of a 64-bit process's own addresses


class MBI(ctypes.Structure):  # MEMORY_BASIC_INFORMATION (64-bit)
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD),
    ]  # fmt: skip


class Region(ctypes.Structure):
    _fields_ = [("dwCommittedSize", wintypes.DWORD), ("dwUnCommittedSize", wintypes.DWORD),
                ("lpFirstBlock", ctypes.c_void_p), ("lpLastBlock", ctypes.c_void_p)]  # fmt: skip


class Block(ctypes.Structure):
    _fields_ = [("hMem", wintypes.HANDLE), ("dwReserved", wintypes.DWORD * 3)]


class Union(ctypes.Union):
    _fields_ = [("Block", Block), ("Region", Region)]


class Entry(ctypes.Structure):  # PROCESS_HEAP_ENTRY
    _fields_ = [("lpData", ctypes.c_void_p), ("cbData", wintypes.DWORD),
                ("cbOverhead", ctypes.c_ubyte), ("iRegionIndex", ctypes.c_ubyte),
                ("wFlags", wintypes.WORD), ("u", Union)]  # fmt: skip


class Summary(ctypes.Structure):  # HEAP_SUMMARY
    _fields_ = [("cb", wintypes.DWORD), ("allocated", ctypes.c_size_t),
                ("committed", ctypes.c_size_t), ("reserved", ctypes.c_size_t),
                ("max_reserve", ctypes.c_size_t)]  # fmt: skip


class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX2
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage", "PrivateUsage", "PrivateWorkingSetSize")
    ] + [("SharedCommitUsage", ctypes.c_uint64)]  # fmt: skip


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQuery.restype = ctypes.c_size_t
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.GetProcessHeap.restype = wintypes.HANDLE
k32.GetProcessHeaps.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
k32.GetProcessHeaps.restype = wintypes.DWORD
k32.HeapWalk.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
k32.HeapLock.argtypes = k32.HeapUnlock.argtypes = [wintypes.HANDLE]
k32.HeapSummary.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(Summary)]
k32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
psapi.QueryWorkingSetEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
psapi.GetMappedFileNameW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR,
                                     wintypes.DWORD]  # fmt: skip
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]


def counters() -> dict:
    c = Counters(cb=ctypes.sizeof(Counters))
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return {"private_ws": c.PrivateWorkingSetSize / MB, "ws": c.WorkingSetSize / MB,
            "commit": c.PrivateUsage / MB}  # fmt: skip


def committed_regions() -> list[tuple[int, int, int, int, int]]:
    """(base, size, allocation base, type, protection) of each committed region."""
    mbi = MBI()
    address, found = 0, []
    while address < TOP:
        if not k32.VirtualQuery(ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if mbi.State == MEM_COMMIT:
            found.append((base, size, mbi.AllocationBase or 0, mbi.Type, mbi.Protect))
        address = base + size
    return found


def page_states(regions) -> list[tuple[int, int]]:
    """(private pages in the working set, shared ones) of each region."""
    total = sum(size // PAGE for _, size, *_ in regions)
    table = np.zeros((total, 2), dtype=np.uint64)
    at = 0
    for base, size, *_ in regions:
        n = size // PAGE
        table[at : at + n, 0] = np.arange(n, dtype=np.uint64) * PAGE + base
        at += n
    step = 1 << 16
    for start in range(0, total, step):
        part = table[start : start + step]
        psapi.QueryWorkingSetEx(k32.GetCurrentProcess(), part.ctypes.data, part.nbytes)
    attributes = table[:, 1]
    valid = (attributes & 1).astype(bool)
    shared = ((attributes >> 15) & 1).astype(bool)
    private = valid & ~shared
    out, at = [], 0
    for _, size, *_ in regions:
        n = size // PAGE
        out.append((int(private[at : at + n].sum()), int((valid & shared)[at : at + n].sum())))
        at += n
    return out


class Range(ctypes.Structure):
    _fields_ = [("start", ctypes.c_uint64), ("end", ctypes.c_uint64)]


class Totals(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "busy", "free", "committed", "uncommitted", "blocks", "regions", "large")]  # fmt: skip


ROOM, ROOM_LARGE = 4096, 16384


def walker():
    """heapwalk.dll's walk_heap (LP_RIG_HEAPWALK), which walks a heap in one call with its lock
    held only there; None without it. (Walked from here step by step, the lock was held while
    ctypes gave up the GIL between the steps: a thread with the GIL waited for the lock in
    malloc, the walk for the GIL, and the app stood still.)"""
    path = os.environ.get("LP_RIG_HEAPWALK")
    if not path or not os.path.isfile(path):
        return None
    walk = ctypes.CDLL(path).walk_heap
    walk.argtypes = [wintypes.HANDLE, ctypes.POINTER(Range), ctypes.c_int,
                     ctypes.POINTER(Range), ctypes.c_int, ctypes.POINTER(Totals)]  # fmt: skip
    walk.restype = ctypes.c_int
    return walk


def heaps() -> list[dict]:
    """Each heap of the process: its summary, and where heapwalk.dll is to be had its regions
    (address ranges), its large blocks and what is busy and free in it."""
    n = k32.GetProcessHeaps(0, None)
    handles = (wintypes.HANDLE * (n + 16))()
    n = k32.GetProcessHeaps(len(handles), handles)
    own = k32.GetProcessHeap()
    walk = walker()
    found = []
    for handle in handles[:n]:
        row = {"handle": handle, "process_heap": handle == own, "ranges": [], "large": []}
        if walk is not None:
            regions, large, totals = (Range * ROOM)(), (Range * ROOM_LARGE)(), Totals()
            if walk(handle, regions, ROOM, large, ROOM_LARGE, ctypes.byref(totals)):
                row["ranges"] = [(r.start, r.end) for r in regions[: min(totals.regions, ROOM)]]
                row["large"] = [(r.start, r.end - r.start)
                                for r in large[: min(totals.large, ROOM_LARGE)]]  # fmt: skip
                row.update(busy=totals.busy, free=totals.free, blocks=totals.blocks,
                           region_committed=totals.committed,
                           region_uncommitted=totals.uncommitted)  # fmt: skip
        summary = Summary(cb=ctypes.sizeof(Summary))
        k32.HeapSummary(handle, 0, ctypes.byref(summary))
        row.update(summary_allocated=summary.allocated, summary_committed=summary.committed)
        found.append(row)
    return found


def mapped_name(address: int) -> str:
    buffer = ctypes.create_unicode_buffer(520)
    if psapi.GetMappedFileNameW(k32.GetCurrentProcess(), ctypes.c_void_p(address), buffer, 520):
        return buffer.value.rsplit("\\", 1)[-1]
    return "?"


def allocation_base(address: int) -> int:
    mbi = MBI()
    if not k32.VirtualQuery(ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        return -1
    return mbi.AllocationBase or 0


def heap_kind() -> str:
    """Which heap the process heap is: Windows' NT heap ("nt", its signature 0xFFEEFFEE) or the
    segment heap ("segment", 0xDDEEDDEE), by the signature both keep at their handle's 16th
    byte."""
    signature = ctypes.c_uint32.from_address(k32.GetProcessHeap() + 0x10).value
    return {0xFFEEFFEE: "nt", 0xDDEEDDEE: "segment"}.get(signature, hex(signature))


def census() -> dict:
    """What the process holds now, by owner (MB: committed, private in the working set)."""
    started = time.perf_counter()
    before = counters()
    found_heaps = heaps()
    regions = committed_regions()
    states = page_states(regions)
    heap_of: dict[int, str] = {}  # allocation base -> owner
    for number, heap in enumerate(found_heaps):
        name = "process heap" if heap["process_heap"] else f"heap {number}"
        for start, _end in heap["ranges"]:
            heap_of.setdefault(allocation_base(start), name)
        for address, _size in heap["large"]:  # (those outside its regions)
            heap_of.setdefault(allocation_base(address), name + ": large blocks")
    allocations: dict[int, dict] = {}
    for (_base, size, allocation, kind, protect), (private, shared) in zip(
        regions, states, strict=True
    ):
        row = allocations.setdefault(allocation, {"kind": kind, "size": 0, "private": 0,
                                                  "shared": 0, "guard": False})  # fmt: skip
        row["size"] += size
        row["private"] += private * PAGE
        row["shared"] += shared * PAGE
        row["guard"] = row["guard"] or bool(protect & PAGE_GUARD)
    owners = collections.defaultdict(lambda: [0, 0, 0])  # committed, private, count
    images = collections.Counter()
    others = []
    for allocation, row in allocations.items():
        if row["kind"] == MEM_IMAGE:
            owner = "program files (images)"
            images[mapped_name(allocation)] += row["private"]
        elif row["kind"] == MEM_MAPPED:
            owner = "mapped files and sections"
        elif allocation in heap_of:
            owner = heap_of[allocation]
            if owner.startswith("heap ") and not owner.endswith("large blocks"):
                owner = "other heaps"
            elif owner.startswith("heap "):
                owner = "other heaps: large blocks"
        elif row["guard"]:
            owner = "thread stacks"
        elif row["size"] == MB:
            owner = "Python's arenas (1 MiB)"
        else:
            owner = "other direct allocations"
            others.append((row["private"], row["size"], allocation))
        owners[owner][0] += row["size"]
        owners[owner][1] += row["private"]
        owners[owner][2] += 1
    after = counters()

    def mb(value: int) -> float:
        return round(value / MB, 1)

    by_owner = sorted(owners.items(), key=lambda kv: -kv[1][1])
    heap_rows = []
    for heap in found_heaps:
        row = {k: mb(v) for k, v in heap.items() if k in (
            "busy", "free", "region_committed", "region_uncommitted", "summary_allocated",
            "summary_committed")}  # fmt: skip
        row.update(process_heap=heap["process_heap"], blocks=heap.get("blocks"),
                   regions=len(heap["ranges"]), large_blocks=len(heap["large"]),
                   large_mb=mb(sum(size for _, size in heap["large"])))  # fmt: skip
        heap_rows.append(row)
    return {
        "heap_kind": heap_kind(),
        "counters": before,
        "counters_after": after,
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
        "owners": {
            k: {"committed": mb(v[0]), "private_ws": mb(v[1]), "allocations": v[2]}
            for k, v in by_owner
        },  # fmt: skip
        "private_ws_counted": mb(sum(v[1] for v in owners.values())),
        "images_private_top": [(k, round(v / MB, 2)) for k, v in images.most_common(15)],
        "others_top": [
            (round(p / MB, 2), round(size / MB, 2), hex(a))
            for p, size, a in sorted(others, reverse=True)[:15]
        ],  # fmt: skip
        "heaps": heap_rows,
    }


def trim() -> None:
    """The working set emptied (as light mode does)."""
    k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1).value,
                                 ctypes.c_size_t(-1).value)  # fmt: skip

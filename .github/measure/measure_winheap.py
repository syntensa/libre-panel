"""Windows: what the process holds after the app's pictures (the renderer's frames with changing
readings, as the app's window draws them, and its pictures of several themes) went, and what
light mode's give-back returns. Step by step: the private working set (Task Manager's Memory
column), the working set, the commit (private bytes), and the process heap by HeapWalk:
its committed regions, what is allocated in them, what is free there, what is uncommitted."""

import ctypes
import gc
import sys
import time
from ctypes import wintypes

from libre_panel import light
from libre_panel.load_report import c_heap
from libre_panel.render import kept
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme

MB = 1 << 20
kernel32 = ctypes.WinDLL("kernel32")
psapi = ctypes.WinDLL("psapi")


class Counters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t), ("PrivateWorkingSetSize", ctypes.c_size_t),
        ("SharedCommitUsage", ctypes.c_ulonglong),
    ]  # fmt: skip


class Region(ctypes.Structure):
    _fields_ = [("dwCommittedSize", wintypes.DWORD), ("dwUnCommittedSize", wintypes.DWORD),
                ("lpFirstBlock", ctypes.c_void_p), ("lpLastBlock", ctypes.c_void_p)]  # fmt: skip


class Block(ctypes.Structure):
    _fields_ = [("hMem", wintypes.HANDLE), ("dwReserved", wintypes.DWORD * 3)]


class Union(ctypes.Union):
    _fields_ = [("Block", Block), ("Region", Region)]


class Entry(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("cbData", wintypes.DWORD),
                ("cbOverhead", ctypes.c_ubyte), ("iRegionIndex", ctypes.c_ubyte),
                ("wFlags", wintypes.WORD), ("u", Union)]  # fmt: skip


kernel32.GetProcessHeap.restype = wintypes.HANDLE
kernel32.HeapWalk.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
kernel32.HeapLock.argtypes = kernel32.HeapUnlock.argtypes = [wintypes.HANDLE]
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
REGION, UNCOMMITTED, BUSY = 0x0001, 0x0002, 0x0004


def walk():
    heap = kernel32.GetProcessHeap()
    committed = uncommitted = busy = free = 0
    entry = Entry()
    kernel32.HeapLock(heap)
    try:
        while kernel32.HeapWalk(heap, ctypes.byref(entry)):
            if entry.wFlags & REGION:
                committed += entry.u.Region.dwCommittedSize
                uncommitted += entry.u.Region.dwUnCommittedSize
            elif entry.wFlags & UNCOMMITTED:
                pass
            elif entry.wFlags & BUSY:
                busy += entry.cbData + entry.cbOverhead
            else:
                free += entry.cbData
    finally:
        kernel32.HeapUnlock(heap)
    return committed, uncommitted, busy, free


def line(step):
    c = Counters()
    c.cb = ctypes.sizeof(c)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    summary = c_heap() or {"in_use": 0, "free": 0}
    committed, uncommitted, busy, free = walk()
    print(
        f"{step:40} privateWS {c.PrivateWorkingSetSize / MB:6.1f}  "
        f"WS {c.WorkingSetSize / MB:6.1f}  "
        f"commit {c.PrivateUsage / MB:6.1f} | heap walk: committed {committed / MB:6.1f}  busy "
        f"{busy / MB:6.1f}  free {free / MB:6.1f}  uncommitted {uncommitted / MB:6.1f} | summary "
        f"in use {summary['in_use'] / MB:6.1f} free {summary['free'] / MB:6.1f}",
        flush=True,
    )


line("start")
panels = [Renderer(load_theme(find_theme(name)), preview=True)
          for name in ("lumen-1920x720", "lumen-companion")]  # fmt: skip
for k in range(120):  # the panels' frames with readings that change, the window open
    snapshot = demo_snapshot(fixed_time=1_700_000_000 + k)
    for panel in panels:
        panel.render(snapshot)
line("120 frames of two panels, readings changing")
for _ in range(2):  # the app's pictures of the themes (Start, Design)
    for name in ("arena", "almanac", "encore", "terra", "contrast", "lumen", "arena-companion"):
        try:
            picture = Renderer(load_theme(find_theme(name)), preview=True)
        except Exception as exc:  # noqa: BLE001
            print("  (no", name, exc, ")")
            continue
        picture.render(demo_snapshot(fixed_time=1_700_000_000))
        picture.close()
line("the app's pictures of the themes made")
light.mark()
for k in range(5):
    for panel in panels:
        panel.render(demo_snapshot(fixed_time=1_700_000_200 + k))
light.shed()
light.empty_caches()
gc.collect()
line("the caches let go (before giving back)")
started = time.perf_counter()
light.windows_heaps()
line(f"heaps given back ({(time.perf_counter() - started) * 1000:.1f} ms)")
kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), ctypes.c_size_t(-1),
                                  ctypes.c_size_t(-1))  # fmt: skip
line("working set trimmed")
for k in range(60):
    for panel in panels:
        panel.render(demo_snapshot(fixed_time=1_700_000_300 + k))
line("60 more frames")
light.tidy()
line("tidy")
print(
    "python",
    sys.version.split()[0],
    "kept",
    round(sum(r["bytes"] for r in kept.stats().values()) / MB, 1),
    "MB",
)

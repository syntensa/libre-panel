"""Windows: what the process heap keeps after the app's pictures (the renderer's previews of
several themes, as the app's window makes them) went, and what light mode's give-back returns.
Prints, step by step: the private working set (USS, the Task Manager's Memory column), the
commit (psutil's private), and the process heap's bytes in use and committed but free."""

import gc
import sys
import time

import psutil

from libre_panel import light
from libre_panel.load_report import c_heap
from libre_panel.render import kept
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme

MB = 1 << 20
proc = psutil.Process()


def line(step):
    full = proc.memory_full_info()
    heap = c_heap() or {"in_use": 0, "free": 0}
    print(
        f"{step:44} private {full.uss / MB:7.1f}  commit {getattr(full, 'private', 0) / MB:7.1f}  "
        f"heap in use {heap['in_use'] / MB:7.1f}  free {heap['free'] / MB:7.1f} MB",
        flush=True,
    )


line("start")
snapshot = demo_snapshot(fixed_time=1_700_000_000)
panels = [
    Renderer(load_theme(find_theme(name)), preview=True)
    for name in ("lumen-1920x720", "lumen-companion")
]
for _ in range(5):
    for panel in panels:
        panel.render(snapshot)
line("two panels drawn")
themes = [
    t
    for t in (
        "arena",
        "almanac",
        "encore",
        "terra",
        "contrast",
        "lumen",
        "arena-1920x720",
        "almanac-1920x720",
    )
]
for _round in range(3):  # the app's window: its pictures of every theme, again and again
    for name in themes:
        try:
            picture = Renderer(load_theme(find_theme(name)), preview=True)
        except Exception as exc:  # noqa: BLE001
            print("  (no", name, exc, ")")
            continue
        picture.render(snapshot)
        picture.close()
    for panel in panels:
        panel.render(snapshot)
line("the app's pictures of the themes made")
light.mark()
for panel in panels:
    panel.render(snapshot)
light.shed()
light.empty_caches()
gc.collect()
line("the caches let go (light mode, before giving back)")
started = time.perf_counter()
light.windows_heaps()
took = (time.perf_counter() - started) * 1000
line(f"heaps given back ({took:.1f} ms)")
light.give_back()  # (the heaps again, and the working set)
line("working set trimmed too")
for _ in range(50):
    for panel in panels:
        panel.render(snapshot)
line("50 frames of the two panels later")
started = time.perf_counter()
light.tidy()
took = (time.perf_counter() - started) * 1000
line(f"tidy ({took:.1f} ms)")
print(
    "python",
    sys.version.split()[0],
    "pictures kept",
    round(sum(r["bytes"] for r in kept.stats().values()) / MB, 1),
    "MB",
)

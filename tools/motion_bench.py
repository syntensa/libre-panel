"""How fluid the themes are in video mode, measured headless.

    python tools/motion_bench.py [theme ...] [--seconds 6] [--switch THEME]

Each theme runs through the main loop (``MainLoop.step``) at 50 fps in real
time, with demo readings, the helper thread and a streaming display in
memory, as on a video panel. Prints how long a frame takes (median, 95th
percentile, slowest), how many frames changed the first graph and how fast
it moved, and how many frames changed a number between readings.
``--switch`` shows another theme halfway and prints the longest gap between
two frames around the switch. Times depend on the machine; tests check the
motion only (tests/test_motion.py).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from libre_panel import clock  # noqa: E402
from libre_panel.app import MainLoop  # noqa: E402
from libre_panel.config import Config, DeviceConfig, SensorsConfig  # noqa: E402
from libre_panel.devices.base import Display  # noqa: E402
from libre_panel.plugins import PluginHost  # noqa: E402
from libre_panel.sensors.base import SensorHub  # noqa: E402
from libre_panel.sensors.demo import DemoProvider  # noqa: E402

FPS = 50


class Stream(Display):
    """A video panel in memory: when each frame came, and what it showed in
    the boxes looked at (whole frames would take gigabytes)."""

    streaming = True
    stream_fps = FPS

    def __init__(self) -> None:
        super().__init__(DeviceConfig())
        self.boxes: dict[str, tuple[int, int, int, int]] = {}
        self.frames: list[tuple[float, dict[str, object]]] = []

    def show(self, frame, region=None) -> None:
        parts = {name: frame.crop(box) for name, box in self.boxes.items()}
        self.frames.append((clock(), parts))


class Demo(DemoProvider):
    """Demo readings that follow the clock (they move as real ones do)."""

    def read(self):
        self.fixed_time = 1_700_000_000 + clock()
        return super().read()


def boxes(loop: MainLoop) -> dict[str, tuple[int, int, int, int]]:
    """Where the first graph and the first gliding number (else number) are."""
    found = {}
    widgets = loop.renderer.widgets
    graph = next((w for w in widgets if w["type"] == "graph"), None)
    if graph is not None:
        found["graph"] = graph["x"], graph["y"], graph["x"] + graph["w"], graph["y"] + graph["h"]
    metrics = [w for w in widgets if w["type"] == "metric" and "{value:." in w["format"]]
    metric = next((w for w in metrics if w.get("smooth")), metrics[0] if metrics else None)
    if metric is not None:
        size = metric.get("font_size", 24)  # the room its number takes
        width = size * 3
        x = metric["x"] - {"right": width, "center": width // 2}.get(metric.get("align"), 0)
        found["number"] = x, metric["y"], x + width, metric["y"] + size
    return found


def profile(region) -> list[int]:
    """Per column, the row of its brightest pixel: the curve's line."""
    region = region.convert("L")
    data, (w, h) = region.load(), region.size
    rows = []
    for x in range(w):
        column = [data[x, y] for y in range(h)]
        rows.append(column.index(max(column)))
    return rows


def moved(before: list[int], after: list[int], most: int = 80) -> int:
    span = len(after) - most
    return min(range(most), key=lambda s: sum(abs(before[x + s] - after[x]) for x in range(span)))


def bench(theme: str, seconds: float, switch: str | None) -> None:
    display, host = Stream(), PluginHost()
    config = Config(theme=theme, sensors=SensorsConfig(providers=["demo"]))
    loop = MainLoop(config, host=host, hub=SensorHub([Demo({})]), display=display)
    times: list[float] = []
    display.boxes = boxes(loop)
    every = round(loop.theme.refresh_ms * FPS / 1000)
    start, asked = clock(), None
    try:
        for n in range(round(seconds * FPS)):
            due = start + n / FPS
            wait = due - clock()
            if wait > 0:
                time.sleep(wait)
            if switch and asked is None and n >= seconds * FPS / 2:
                asked = clock()
                host.request_theme(switch, by="bench", transition="cut")
            began = clock()
            loop.step(due)
            times.append((clock() - began) * 1000)
    finally:
        loop.close()
        host.close()
    steady = sorted(times[FPS:])  # from the second second on: past the first frame
    print(f"{theme}: frame median {statistics.median(steady):.1f} ms, "
          f"95% {steady[int(len(steady) * 0.95)]:.1f} ms, slowest {steady[-1]:.1f} ms "
          f"(first {times[0]:.0f} ms)")  # fmt: skip
    shown = [parts for t, parts in display.frames if asked is None or t < asked]
    if "graph" in display.boxes:
        crops = [parts["graph"].tobytes() for parts in shown[FPS:]]
        changed = sum(a != b for a, b in zip(crops, crops[1:], strict=False))
        late = shown[-1]["graph"], shown[-1 - FPS]["graph"]
        speed = moved(profile(late[1]), profile(late[0]))
        print(f"  graph: changed in {changed} of {len(crops) - 1} frames, {speed} px a second")
    if "number" in display.boxes:
        crops = [parts["number"].tobytes() for parts in shown]
        between = sum(1 for n in range(1, len(crops)) if crops[n] != crops[n - 1] and n % every)
        print(f"  number: changed in {between} frames between readings ({every} frames apart)")
    if asked is not None:
        stamps = [t for t, _ in display.frames if t >= asked - 0.1]
        gaps = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
        print(f"  switch to {switch}: longest gap between frames {max(gaps[: 2 * FPS]):.0f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("themes", nargs="*", default=["spur-ii", "studio"])
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--switch", help="show this theme halfway and time the switch")
    args = parser.parse_args()
    for theme in args.themes:
        bench(theme, args.seconds, args.switch)


if __name__ == "__main__":
    main()

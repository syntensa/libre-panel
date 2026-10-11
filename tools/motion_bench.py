"""How fluid the themes are in video mode, measured headless.

    python tools/motion_bench.py [theme ...] [--seconds 6] [--warmup 1] [--switch THEME]
                                 [--dust] [--finish facet] [--fans N]

Each theme runs through the main loop (``MainLoop.step``) at 50 fps in real
time, with demo readings, the helper thread and a streaming display in
memory, as on a video panel. Prints how long a frame takes after the first
``--warmup`` seconds (median, 95th percentile, slowest), how many frames
changed the first graph and how fast it moved, and how many frames changed
a number between readings. With a panel light (theme ``look.light``) it
also prints the frames a sweep of light ran over: the panel starts with
one. ``--switch`` shows another theme halfway and prints the longest gap
between two frames around the switch. Times depend on the machine; tests
check the motion only (tests/test_motion.py).

``--dust`` (the theme's ``look.dust`` with its defaults), ``--finish facet``
(every text, bar, gauge and graph of the theme in facets) and ``--fans N`` (N
fan widgets on the theme) run each theme twice, as it is and with that look,
and print what the look adds to a frame. The two run side by side, every
frame drawn by both, so the machine's noise is the same for both and the
difference of the frame times is the look's cost.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
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
from libre_panel.theme.model import find_theme, save_theme  # noqa: E402

FPS = 50
FINISHED = ("text", "metric", "clock", "weather", "countdown", "bar", "gauge", "graph")
FANS = ("gpu.fan", "fan.cpu", "fan.board.case_1", "fan.board.case_2", "fan.board.pump")


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


def sweeping(loop: MainLoop, now: float) -> bool:
    """Whether a sweep of the panel light runs over the frame at ``now``."""
    light = getattr(loop.renderer.look, "light", None)
    return light is not None and bool(light.running(now))


class Run:
    """A theme going through the main loop, with the frame times it takes."""

    def __init__(self, theme: str, warmup: float = 1.0) -> None:
        self.display, self.host = Stream(), PluginHost()
        config = Config(theme=theme, sensors=SensorsConfig(providers=["demo"]))
        hub = SensorHub([Demo({})])
        self.loop = MainLoop(config, host=self.host, hub=hub, display=self.display)
        self.display.boxes = boxes(self.loop)
        self.times: list[float] = []
        self.swept: list[float] = []  # frames a sweep of light ran over, after the warm-up
        self.warm = round(warmup * FPS)  # frames left out at the start

    def step(self, due: float) -> None:
        began = clock()
        self.loop.step(due)
        self.times.append((clock() - began) * 1000)
        if len(self.times) > self.warm and sweeping(self.loop, due):
            self.swept.append(self.times[-1])

    def close(self) -> None:
        self.loop.close()
        self.host.close()

    def steady(self) -> list[float]:
        """The frame times past the warm-up, sorted."""
        return sorted(self.times[self.warm :])


def wait_for(due: float) -> None:
    wait = due - clock()
    if wait > 0:
        time.sleep(wait)


def bench(theme: str, seconds: float, switch: str | None, warmup: float = 1.0) -> None:
    run = Run(theme, warmup)
    every = round(run.loop.theme.refresh_ms * FPS / 1000)
    start, asked = clock(), None
    try:
        for n in range(round(seconds * FPS)):
            due = start + n / FPS
            wait_for(due)
            if switch and asked is None and n >= seconds * FPS / 2:
                asked = clock()
                run.host.request_theme(switch, by="bench", transition="cut")
            run.step(due)
    finally:
        run.close()
    display, times, steady = run.display, run.times, run.steady()
    print(f"{theme}: frame median {statistics.median(steady):.1f} ms, "
          f"95% {steady[int(len(steady) * 0.95)]:.1f} ms, slowest {steady[-1]:.1f} ms "
          f"(first {times[0]:.0f} ms)")  # fmt: skip
    swept = run.swept
    if swept:
        print(f"  light: a sweep over {len(swept)} frames, "
              f"median {statistics.median(swept):.1f} ms, slowest {max(swept):.1f} ms")  # fmt: skip
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


def compare(plain: str, look: str, seconds: float, what: str, warmup: float = 1.0) -> None:
    """``plain`` and ``look`` (the same theme with the look) side by side: every frame is
    drawn by both, one after the other (the one that goes first alternates), so the
    machine's mood is the same for both and what is left is the look's cost."""
    runs = [Run(plain, warmup), Run(look, warmup)]
    start = clock()
    try:
        for n in range(round(seconds * FPS)):
            due = start + n / FPS
            wait_for(due)
            for run in runs if n % 2 else runs[::-1]:
                run.step(due)
    finally:
        for run in runs:
            run.close()
    a, b = runs[0].steady(), runs[1].steady()
    for name, times in ((plain, a), (f"{plain} + {what}", b)):
        print(
            f"{name}: frame median {statistics.median(times):.1f} ms, "
            f"95% {times[int(len(times) * 0.95)]:.1f} ms, slowest {times[-1]:.1f} ms"
        )
    # frame by frame: the same moment, with and without
    warm = runs[0].warm
    diff = sorted(y - x for x, y in zip(runs[0].times[warm:], runs[1].times[warm:], strict=True))
    print(
        f"  {what} adds {statistics.median(diff):.1f} ms to a frame at the median, "
        f"{statistics.mean(diff):.1f} ms on average, "
        f"{diff[int(len(diff) * 0.95)]:.1f} ms at the 95%"
    )


def variant(name: str, dust: bool, finish: str, fans: int) -> str:
    """``name`` with the look asked for, saved as a user theme (of the temporary home the
    bench runs in); the new theme's name."""
    data = json.loads((find_theme(name) / "theme.json").read_text(encoding="utf-8"))
    if dust:
        data["look"] = {**data.get("look", {}), "dust": True}
    if finish:
        for widget in data["widgets"]:
            if widget["type"] in FINISHED:
                widget["finish"] = finish
    for n in range(fans):  # along the top, over what is there: the cost is what counts
        data["widgets"].append(
            {
                "type": "fan",
                "id": f"bench-fan-{n}",
                "x": 8 + 60 * n,
                "y": 4,
                "w": 48,
                "h": 48,
                "sensor": FANS[n % len(FANS)],
            }  # fmt: skip
        )
    save_theme(f"{name}-bench", data, source=name)
    return f"{name}-bench"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("themes", nargs="*", default=["spur-ii", "studio", "spur-classic"])
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--warmup", type=float, default=1.0, help="seconds left out at the start")
    parser.add_argument("--switch", help="show this theme halfway and time the switch")
    parser.add_argument("--dust", action="store_true", help="also with the theme's look.dust")
    parser.add_argument("--finish", choices=["facet"], help="also with every shape finished so")
    parser.add_argument("--fans", type=int, default=0, help="also with this many fan widgets")
    args = parser.parse_args()
    looks = [
        (what, ok)
        for what, ok in (("dust", args.dust), (f"{args.finish} finish", bool(args.finish)),
                         (f"{args.fans} fans", args.fans > 0))
        if ok
    ]  # fmt: skip
    if looks:  # the variants are user themes: in a home of their own, not the user's
        home = tempfile.TemporaryDirectory()
        os.environ["LIBRE_PANEL_HOME"] = home.name
    for theme in args.themes:
        bench(theme, args.seconds, args.switch, args.warmup)
        if looks:
            what = " + ".join(w for w, _ in looks)
            name = variant(theme, args.dust, args.finish or "", args.fans)
            compare(theme, name, args.seconds, what, args.warmup)


if __name__ == "__main__":
    main()

"""How two panels in video mode slow each other down, measured headless.

    python tools/panels_bench.py [THEME@MODEL ...] [--frames 300] [--slow-scan]

Each panel's main loop (``MainLoop.step``) draws its frames on demo readings, first alone, then
all of them at once, each in a thread of its own as the app runs them; the frames go to a
streaming display in memory as the bytes ffmpeg gets. Prints how long a frame takes alone and
beside the others (median and slowest). They share one interpreter: what one panel's frame
holds of it the others wait for, so a frame beside the others takes longer than alone.
``--slow-scan`` lets the processes' scan take as long as on a PC with hundreds of them (a
millisecond each), for themes that show the processes (Lumen Companion). The default is the
pair most people have: Lumen on a 12.3" and Lumen Companion on end beside it.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("LIBRE_PANEL_HOME", tempfile.mkdtemp())

from libre_panel import app  # noqa: E402
from libre_panel.config import parse_config  # noqa: E402
from libre_panel.devices.base import Display  # noqa: E402
from libre_panel.devices.models import find_model  # noqa: E402
from libre_panel.devices.turzx_video import rgb_bytes  # noqa: E402
from libre_panel.sensors.base import SensorHub  # noqa: E402
from libre_panel.sensors.demo import DemoProvider  # noqa: E402

DEFAULT = ["lumen@turing-12.3-usb", "lumen-companion@turing-9.2-usb@portrait"]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Stream(Display):
    """A video panel in memory: each frame becomes the bytes ffmpeg would get."""

    streaming, stream_fps = True, 50

    def __init__(self, config, model) -> None:
        super().__init__(config)
        self.model = model

    def show(self, frame, region=None) -> None:
        rgb_bytes(frame)  # as the video panels make them


def loop_for(spec: str):
    theme, model, *way = spec.split("@")
    device = {"model": model, "orientation": way[0] if way else ""}
    config = parse_config({"theme": theme, "fps": 50, "device": device})
    clock = Clock()
    loop = app.MainLoop(config, display=Stream(config.device, find_model(model)), clock=clock,
                        hub=SensorHub([DemoProvider()]))  # fmt: skip
    for _ in range(60):  # (warm: every kind of frame drawn once)
        clock.now += 0.02
        loop.step(clock.now)
    return spec, loop, clock


def run(loop, clock, frames: int, out: list[float]) -> None:
    for _ in range(frames):
        clock.now += 0.02
        start = time.perf_counter()
        loop.step(clock.now)
        out.append(time.perf_counter() - start)


def slow_scan() -> None:
    """Hundreds of processes, a millisecond of the interpreter each (as on Windows)."""
    from libre_panel.sensors import psutil_provider

    class Busy:
        pid, info = 7, {"name": "busy.exe", "memory_percent": 1.0}

        def cpu_percent(self, interval=None):
            sum(range(30000))
            return 1.0

    psutil_provider.psutil.process_iter = lambda attrs: [Busy() for _ in range(350)]
    provider = psutil_provider.PsutilProvider()

    def scan() -> None:
        while True:
            provider._processes()
            time.sleep(psutil_provider.PROCESSES_EVERY)

    threading.Thread(target=scan, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("panels", nargs="*", default=DEFAULT, help="THEME@MODEL[@portrait]")
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--slow-scan", action="store_true")
    args = parser.parse_args()
    loops = [loop_for(spec) for spec in args.panels]
    for spec, loop, clock in loops:
        alone: list[float] = []
        run(loop, clock, args.frames, alone)
        print(f"alone   {spec:44} {statistics.median(alone) * 1000:6.1f} ms a frame")
    if args.slow_scan:
        slow_scan()
    beside: list[list[float]] = [[] for _ in loops]
    threads = [threading.Thread(target=run, args=(loop, clock, args.frames, beside[n]))
               for n, (_, loop, clock) in enumerate(loops)]  # fmt: skip
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for (spec, loop, _), times in zip(loops, beside, strict=True):
        print(f"beside  {spec:44} {statistics.median(times) * 1000:6.1f} ms a frame, "
              f"slowest {max(times) * 1000:.0f} ms")  # fmt: skip
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

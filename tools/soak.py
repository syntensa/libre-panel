"""A long run of the background app with two panels: does it keep its memory, its threads and
its pace?

    python tools/soak.py [--hours 24] [--every 60] [--out soak] [--video] [-- command ...]

The app starts as autostart starts it (``libre-panel tray --background``, or the command after
``--``) in a settings folder of its own: two virtual 9.2" bars (Lumen and Lumen Companion) on the
demo readings, with notices, the timer (focus and break of a minute each, so a notice comes every
minute) and the power service running. Every ``--every`` seconds it notes the app's memory
(RSS), its threads, its open files (handles on Windows), its processor time and the panels'
frame rate and frame times, into ``<out>/soak.jsonl`` as it goes; at the end (or on Ctrl+C) it
writes ``<out>/soak.md`` and ``<out>/soak.png`` and quits the app.

It passes when, after the first ten minutes, the memory grows by less than ``--max-growth`` MB
an hour, the threads and the open files do not climb, every panel kept showing and no frame took
longer than the frame's whole time on average.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import psutil

WARM_UP_S = 600.0  # what the first ten minutes do (caches, fonts, the first pictures) is no leak
MODEL = "turing-9.2-usb"


def config(home: Path, video: bool) -> str:
    """The soak's config.toml."""
    return (
        'theme = "lumen"\nfps = 10\n\n'
        f'[device]\ndriver = "virtual"\nmodel = "{MODEL}"\n\n'
        f'[video]\nmode = "{"on" if video else "off"}"\nlocal_clip = "soak.h264"\n\n'
        '[sensors]\nproviders = ["demo"]\n\n'
        '[services]\nenabled = ["notices", "timer", "power"]\n\n'
        "[services.timer]\nminutes = 1\nbreak_minutes = 1\n\n"
        "[services.power]\nrest_w = 50\nprice = 0.3\n\n"
        f'[[panels]]\ntheme = "lumen-companion"\ndriver = "virtual"\nmodel = "{MODEL}"\n'
    )


class App:
    """The app under test, talked to as the smoke test does (packaging/smoke_test.py)."""

    def __init__(self, home: Path, command: list[str]) -> None:
        self.home = home
        env = dict(os.environ, LIBRE_PANEL_HOME=str(home))
        self.proc = subprocess.Popen(command, env=env)
        self.url = self._wait(lambda: json.loads((home / "instance.json").read_text())["editor"])
        self.token = self._wait(lambda: json.loads(
            (home / "private" / "editor.json").read_text(encoding="utf-8"))["token"])  # fmt: skip
        self._wait(lambda: self.get("api/app")["panel"]["state"] == "showing", 120)

    def _wait(self, check, seconds: float = 90):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                found = check()
            except (OSError, ValueError, KeyError):
                found = None
            if found:
                return found
            time.sleep(0.5)
        raise SystemExit("the app did not start")

    def call(self, path: str, body: dict | None = None):
        request = urllib.request.Request(self.url + path, headers={"X-Libre-Panel": self.token})
        if body is not None:
            request.data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local
        with opener.open(request, timeout=20) as answer:
            return json.loads(answer.read())

    def get(self, path: str):
        return self.call(path)

    def quit(self) -> None:
        try:
            self.call("api/app", {"action": "quit"})
            self.proc.wait(30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait(10)


def sample(app: App, process: psutil.Process, began: float) -> dict:
    """One look at the app: the process and what it says about its panels."""
    with process.oneshot():
        memory = process.memory_info().rss / 2**20
        threads = process.num_threads()
        files = process.num_handles() if sys.platform == "win32" else process.num_fds()
        cpu = sum(process.cpu_times()[:2])
    children = len(process.children(recursive=True))
    row = {"t": round(time.monotonic() - began, 1), "rss_mb": round(memory, 1), "threads": threads,
           "files": files, "cpu_s": round(cpu, 1), "children": children}  # fmt: skip
    try:
        panel = app.get("api/app")["panel"]
    except (OSError, ValueError, KeyError) as exc:
        row["error"] = str(exc)
        return row
    stats = panel.get("stats") or {}
    row.update(state=panel.get("state"), fps=stats.get("fps"), target=stats.get("target_fps"),
               render_ms=stats.get("render_ms"), peak_ms=stats.get("render_peak_ms"),
               more=[(m.get("state"), m.get("fps")) for m in panel.get("more", [])])  # fmt: skip
    return row


def slope(points: list[tuple[float, float]]) -> float:
    """Growth per hour of the least-squares line through (seconds, value)."""
    if len(points) < 3:
        return 0.0
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in points) / den * 3600


def summary(rows: list[dict], max_growth: float) -> tuple[dict, bool]:
    late = [r for r in rows if r["t"] >= WARM_UP_S] or rows
    times = [r["render_ms"] for r in rows if r.get("render_ms") is not None]
    peaks = [r["peak_ms"] for r in rows if r.get("peak_ms") is not None]
    fps = [r["fps"] for r in rows if r.get("fps") is not None]
    target = next((r["target"] for r in rows if r.get("target")), 10)
    not_showing = sum(1 for r in late if r.get("state") != "showing"
                      or any(state != "showing" for state, _ in r.get("more", [])))  # fmt: skip
    found = {
        "hours": round(rows[-1]["t"] / 3600, 2) if rows else 0,
        "samples": len(rows),
        "rss_start_mb": late[0]["rss_mb"] if late else None,
        "rss_end_mb": rows[-1]["rss_mb"] if rows else None,
        "rss_max_mb": max((r["rss_mb"] for r in rows), default=None),
        "rss_growth_mb_h": round(slope([(r["t"], r["rss_mb"]) for r in late]), 2),
        "threads": (
            min((r["threads"] for r in late), default=0),
            max((r["threads"] for r in late), default=0),
        ),  # fmt: skip
        "files_growth_h": round(slope([(r["t"], r["files"]) for r in late]), 2),
        "children_max": max((r["children"] for r in rows), default=0),
        "fps_mean": round(statistics.fmean(fps), 2) if fps else None,
        "fps_target": target,
        "render_ms_median": round(statistics.median(times), 2) if times else None,
        "render_ms_p99": round(sorted(times)[int(len(times) * 0.99) - 1], 2) if times else None,
        "peak_ms_max": max(peaks, default=None),
        "not_showing": not_showing,
        "errors": sum(1 for r in rows if "error" in r),
    }
    budget = 1000 / max(1, target)
    passed = (
        found["rss_growth_mb_h"] < max_growth
        and found["threads"][1] - found["threads"][0] <= 4
        and found["files_growth_h"] < 5
        and not_showing == 0
        and found["errors"] == 0
        and (found["render_ms_median"] or 0) < budget
    )
    return found, passed


def chart(rows: list[dict], path: Path) -> None:
    """Two charts one above the other, on one time axis: the memory, and the frame times
    (the mean of each sample's seconds and their longest frame)."""
    from PIL import Image, ImageDraw, ImageFont

    from libre_panel.fonts import builtin_font_path

    w, h, pad = 1200, 360, 56
    surface, ink, muted, grid = (255, 255, 255), (28, 33, 40), (100, 108, 120), (226, 230, 235)
    blue, orange = (44, 110, 210), (214, 110, 32)
    font = ImageFont.truetype(str(builtin_font_path("builtin:Barlow-Regular")), 15)
    title = ImageFont.truetype(str(builtin_font_path("builtin:Barlow-SemiBold")), 19)
    picture = Image.new("RGB", (w, 2 * h), surface)
    draw = ImageDraw.Draw(picture)
    hours = max(rows[-1]["t"] / 3600, 1e-6) if rows else 1.0

    def plot(top: int, name: str, unit: str, series: list[tuple[str, tuple, list]]) -> None:
        values = [v for _, _, points in series for _, v in points]
        if not values:
            return
        low, high = 0.0, max(values) * 1.15 or 1.0
        x0, y0, x1, y1 = pad + 20, top + 50, w - pad, top + h - 40
        draw.text((x0, top + 14), name, font=title, fill=ink)
        for n in range(5):  # recessive grid, the values on the left
            y = y1 - (y1 - y0) * n / 4
            draw.line([(x0, y), (x1, y)], fill=grid, width=1)
            draw.text((x0 - 8, y), f"{low + (high - low) * n / 4:.0f}", font=font, fill=muted,
                      anchor="rm")  # fmt: skip
        for n in range(int(hours) + 1):
            x = x0 + (x1 - x0) * n / hours
            draw.text((x, y1 + 8), f"{n} h", font=font, fill=muted, anchor="mt")
        draw.text((x0 - 8, y0 - 22), unit, font=font, fill=muted, anchor="rm")
        legend_x = x1
        for label, color, points in reversed(series):
            line = [(x0 + (x1 - x0) * t / 3600 / hours, y1 - (y1 - y0) * (v - low) / (high - low))
                    for t, v in points]  # fmt: skip
            if len(line) > 1:
                draw.line(line, fill=color, width=2, joint="curve")
            if len(series) > 1:  # a legend for more than one series, its text in ink
                text_w = draw.textlength(label, font=font)
                draw.text((legend_x, top + 20), label, font=font, fill=ink, anchor="rm")
                draw.line([(legend_x - text_w - 30, top + 20), (legend_x - text_w - 10, top + 20)],
                          fill=color, width=3)  # fmt: skip
                legend_x -= text_w + 50

    plot(0, "Memory of the app", "MB", [("RSS", blue, [(r["t"], r["rss_mb"]) for r in rows])])
    plot(h, "Frame times", "ms", [
        ("mean", blue, [(r["t"], r["render_ms"]) for r in rows if r.get("render_ms") is not None]),
        ("longest", orange, [(r["t"], r["peak_ms"]) for r in rows if r.get("peak_ms") is not None]),
    ])  # fmt: skip
    picture.save(path, optimize=True)


def report(rows: list[dict], found: dict, passed: bool, path: Path, args) -> None:
    lines = [
        f"# Soak test: {found['hours']} h, two panels",
        "",
        f"**{'Passed' if passed else 'Failed'}** · {sys.platform} · Python {sys.version.split()[0]}"
        f" · video mode {'on' if args.video else 'off'} · a sample every {args.every:.0f} s",
        "",
        "| | |",
        "|---|---|",
        f"| Memory after the warm-up, at the end, most | {found['rss_start_mb']} MB, "
        f"{found['rss_end_mb']} MB, {found['rss_max_mb']} MB |",
        f"| Memory growth after the warm-up | {found['rss_growth_mb_h']} MB an hour "
        f"(at most {args.max_growth}) |",
        f"| Threads after the warm-up | {found['threads'][0]} to {found['threads'][1]} |",
        f"| Open files (handles) | {found['files_growth_h']:+} an hour |",
        f"| Programs started by the app, at most at once | {found['children_max']} |",
        f"| Frames per second | {found['fps_mean']} of {found['fps_target']} |",
        f"| Frame time: median, 99th percentile, longest | {found['render_ms_median']} ms, "
        f"{found['render_ms_p99']} ms, {found['peak_ms_max']} ms |",
        f"| Samples where a panel did not show | {found['not_showing']} of {found['samples']} |",
        f"| Samples the app did not answer | {found['errors']} |",
        "",
        "![Memory and frame times](soak.png)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--every", type=float, default=60.0, help="seconds between two samples")
    parser.add_argument("--out", type=Path, default=Path("soak"))
    parser.add_argument("--video", action="store_true", help="the video mode on (needs ffmpeg)")
    parser.add_argument("--max-growth", type=float, default=5.0, help="MB an hour at most")
    parser.add_argument("command", nargs="*", help="how to start the app (after --)")
    args = parser.parse_args()
    command = args.command or [sys.executable, "-m", "libre_panel", "tray", "--background",
                               "--port", "0"]  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "soak.jsonl"
    rows: list[dict] = []
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder)
        (home / "config.toml").write_text(config(home, args.video), encoding="utf-8")
        app = App(home, command)
        process = psutil.Process(app.proc.pid)
        try:
            app.call("api/plugins/control/shortcut", {"action": "timer"})  # the timer runs
        except OSError as exc:
            print("the timer did not start:", exc)
        began, end = time.monotonic(), time.monotonic() + args.hours * 3600
        with log.open("w", encoding="utf-8") as out:
            while not stop["now"] and time.monotonic() < end and app.proc.poll() is None:
                row = sample(app, process, began)
                rows.append(row)
                out.write(json.dumps(row) + "\n")
                out.flush()
                wake = time.monotonic() + args.every
                while not stop["now"] and time.monotonic() < min(wake, end):
                    time.sleep(0.5)
        ended = app.proc.poll()
        app.quit()
        if ended is not None:
            rows.append({"t": round(time.monotonic() - began, 1), "rss_mb": 0, "threads": 0,
                         "files": 0, "cpu_s": 0, "children": 0,
                         "error": f"the app ended by itself ({ended})"})  # fmt: skip
        found, passed = summary(rows, args.max_growth)
        chart(rows, args.out / "soak.png")
        report(rows, found, passed, args.out / "soak.md", args)
        logs = home / "logs" / "libre-panel.log"
        if logs.exists():
            (args.out / "libre-panel.log").write_text(logs.read_text("utf-8"), encoding="utf-8")
    print((args.out / "soak.md").read_text(encoding="utf-8"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

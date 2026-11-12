"""What the Rust app's frames cost on this computer: Lumen on a simulated 12.3" at 25 fps, made
into video by the engine and sent to the fake panel of fake_panel.py, the app's process measured
for a while (CPU, page faults, memory) and its own profile said (LP_PROFILE: where a frame's
time goes, per part).

CI's Windows job runs it with the downloads' programs::

    python tests/frame_load.py --app libre-panel-rust/libre-panel.exe \
        --engine libre-panel-rust/tools/libre-panel-engine.exe

``--env NAME=VALUE`` (repeatable) runs it again with that set, after a run without, so the two
can be compared on the same machine.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time

import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_panel import FakePanel  # noqa: E402
from panel_socket import PanelSocket  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def measure(app: str, engine: str, seconds: float, theme: str, extra: dict[str, str]) -> None:
    fake = FakePanel(pid=0x0123, native=(720, 1920))
    sock = PanelSocket(fake)
    home = tempfile.mkdtemp(prefix="lp-load-")
    present = os.path.join(home, "panel-present")
    alive = True

    def watch() -> None:  # (the app sees the simulated panel plugged in through this file)
        while alive:
            if fake.present:
                open(present, "a").close()
            elif os.path.exists(present):
                os.unlink(present)
            time.sleep(0.02)

    threading.Thread(target=watch, daemon=True).start()
    with open(os.path.join(home, "config.toml"), "w", encoding="utf-8") as f:
        f.write(
            f'theme = "{theme}"\n[device]\ndriver = "turzx"\nmodel = "turing-12.3-usb"\n'
            '[video]\nmode = "on"\nfps = 25\ndevice_fps = 30\nlocal_clip = "usr/data/clip.h264"\n'
            '[sensors]\nproviders = ["psutil"]\n'
        )
    env = {
        **os.environ,
        "LIBRE_PANEL_HOME": home,
        "LIBRE_PANEL_DATA": os.path.join(ROOT, "src", "libre_panel"),
        "LP_ENGINE_LINK": sock.link,
        "LIBRE_PANEL_ENGINE": engine,
        "LP_ENGINE_PRESENT": present,
        "LP_PROFILE": "1",
        **extra,
    }
    env.pop("DISPLAY", None)
    p = subprocess.Popen(
        [app, "run"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    lines: list[str] = []
    threading.Thread(target=lambda: lines.extend(p.stderr), daemon=True).start()
    time.sleep(12)  # (the panel found, the stream begun, the caches warm)
    me = psutil.Process(p.pid)
    procs = [me, *me.children(recursive=True)]

    def sample(q: psutil.Process) -> tuple[float, int]:
        t = q.cpu_times()
        faults = getattr(q.memory_info(), "num_page_faults", 0)  # (Windows)
        return t.user + t.system, faults

    before = {q.pid: sample(q) for q in procs}
    t0 = time.monotonic()
    time.sleep(seconds)
    span = time.monotonic() - t0
    label = " ".join(f"{k}={v}" for k, v in extra.items()) or "as shipped"
    print(f"== {theme}, {label}: over {span:.0f} s")
    for q in procs:
        try:
            cpu, faults = sample(q)
            mem = q.memory_info()
        except psutil.Error:
            continue
        c0, f0 = before[q.pid]
        private = getattr(mem, "private", getattr(mem, "rss", 0))
        print(
            f"  {q.name()}: {100 * (cpu - c0) / span:.1f} % of a core, "
            f"{(faults - f0) / span:.0f} page faults/s, private {private / 2**20:.0f} MB"
        )
    p.terminate()
    try:
        p.wait(20)
    except subprocess.TimeoutExpired:
        p.kill()
    for q in procs[1:]:  # (an engine the app had no time to end)
        try:
            q.kill()
        except psutil.Error:
            pass
    alive = False
    sock.close()
    for line in lines:
        if line.startswith("profile ") or "video:" in line:
            print("  " + line.rstrip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--theme", default="lumen")
    ap.add_argument("--env", action="append", default=[], help="NAME=VALUE for a second run")
    args = ap.parse_args()
    measure(args.app, args.engine, args.seconds, args.theme, {})
    if args.env:
        extra = dict(item.split("=", 1) for item in args.env)
        measure(args.app, args.engine, args.seconds, args.theme, extra)


if __name__ == "__main__":
    main()

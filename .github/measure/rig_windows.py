"""MEASUREMENT (feature branch only): the background app as it runs at login on Windows, with two
simulated TURZX panels (the 12.3" with Lumen, the 9.2" with its companion) through Libre Panel's
engine, the services on, the window never opened; every 15 s the private working set (Task
Manager's Memory column) and the commit of its processes, and a breakdown of the main process
(rig_site/sitecustomize.py, winmap.py).

    python .github/measure/rig_windows.py MINUTES"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

here = Path(__file__).resolve().parent
root = here.parents[1]
minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
sys.path[:0] = [str(root / "tests"), str(root / "src"), str(root / "tools")]
from fake_panel import FakePanel  # noqa: E402
from panel_socket import PanelSocket  # noqa: E402
from soak import App  # noqa: E402

panels = {0x0123: FakePanel(pid=0x0123, native=(720, 1920)),
          0x0092: FakePanel(pid=0x0092, native=(480, 1920))}  # fmt: skip
sockets = {pid: PanelSocket(fake) for pid, fake in panels.items()}


def keep_short():
    while True:
        time.sleep(1)
        for fake in panels.values():
            if len(fake.blocks) > 100:
                del fake.blocks[:-20]
            if len(fake.frames) > 4:
                del fake.frames[:-2]
            if len(fake.jpegs) > 4:
                del fake.jpegs[:-2]


threading.Thread(target=keep_short, daemon=True).start()
services = ["agents", "autopilot", "cooling", "devices", "downloads", "game", "memory",
            "notices", "power", "timer"]  # fmt: skip
if "LP_RIG_SERVICES" in os.environ:
    services = [s for s in os.environ["LP_RIG_SERVICES"].split(",") if s]
providers = os.environ.get("LP_RIG_PROVIDERS", "demo").split(",")
config = (
    'theme = "lumen"\n\n'
    '[device]\ndriver = "turzx"\nmodel = "turing-12.3-usb"\n\n'
    '[video]\nmode = "on"\nencoder = "native"\nlocal_clip = "usr/data/clip.h264"\n\n'
    f"[sensors]\nproviders = {json.dumps(providers)}\n\n"
    f"[services]\nenabled = {json.dumps(services)}\n\n"
    '[[panels]]\ndriver = "turzx"\nmodel = "turing-9.2-usb"\n'
    '[panels.video]\nmode = "on"\nlocal_clip = "usr/data/clip.h264"\n'
)
census_at = minutes * 60 - 200  # (the census takes 150 s and more)
with tempfile.TemporaryDirectory() as folder:
    home = Path(folder)
    (home / "config.toml").write_text(config, encoding="utf-8")
    census = home / "census.json"
    os.environ.update(
        LP_RIG_LINKS=",".join(f"{pid:04x}={s.link}" for pid, s in sockets.items()),
        LP_RIG_CENSUS=str(census), LP_RIG_CENSUS_AT=str(census_at), LP_RIG_MEASURE=str(here),
        PYTHONPATH=os.pathsep.join([str(here / "rig_site"), str(root / "src")]),
    )  # fmt: skip
    command = [sys.executable, "-m", "libre_panel", "tray", "--background", "--no-icon",
               "--port", "0"]  # fmt: skip
    app = App(home, command)
    main = psutil.Process(app.proc.pid)
    print("panel 1:", app.get("api/app")["panel"].get("state"), flush=True)
    began = time.monotonic()
    end = began + minutes * 60
    rows = []
    while time.monotonic() < end and app.proc.poll() is None:
        time.sleep(15)
        row = {}
        for p in [main] + main.children(recursive=True):
            try:
                name = p.name()
                kind = "main" if p.pid == main.pid else "engine" if "engine" in name else name
                info = p.memory_full_info()
                row.setdefault(kind, []).append(
                    (round(info.uss / 2**20, 1), round(info.private / 2**20, 1))
                )
            except psutil.Error:
                pass
        rows.append(row)
        print(
            f"{time.monotonic() - began:5.0f} s  "
            + "  ".join(
                f"{k} " + " + ".join(f"{ws:.0f} ({commit:.0f})" for ws, commit in v)
                for k, v in row.items()
            )
            + "   MB private working set (commit)",
            flush=True,
        )
        if census.exists() and time.monotonic() < end - 30:
            end = time.monotonic() + 30
    if census.exists():
        print(census.read_text(encoding="utf-8"))
    else:
        print("no census")
    log = home / "logs" / "libre-panel.log"
    if log.exists():
        lines = log.read_text("utf-8", "replace").splitlines()
        print(f"the log ({len(lines)} lines), its warnings and errors:")
        for line in lines:
            if " WARNING " in line or " ERROR " in line or "light mode" in line:
                print("  ", line[:220])
    print("panel 1 at the end:", app.get("api/app")["panel"].get("state"), flush=True)
    app.quit()

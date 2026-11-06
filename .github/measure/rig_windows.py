"""MEASUREMENT (feature branch only): the background app as it runs at login on Windows, with two
simulated TURZX panels (the 12.3" with Lumen, the 9.2" with its companion) through Libre Panel's
engine, the services on, the window never opened; every 15 s the private working set (Task
Manager's Memory column) and the commit of its processes, and a breakdown of the main process
(rig_site/sitecustomize.py, winmap.py). As at the user's: first some minutes with the window
never opened, then the app's pages open in a browser for a while (Start, Design, Look, Live,
the settings, the history, the diagnosis), then closed; a breakdown some minutes after.

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
census_at = minutes * 60 - 220  # (the census takes 150 s and more)
OPEN_AT = 150  # s: the app's pages open
VIEWS = ("home", "design", "look", "live", "settings/panel", "history", "diagnose")
# (LP_RIG_STAY: seconds on the last of LP_RIG_VIEWS, "live": the panel's picture, one after the
# other, as a person who leaves the window open on it)
VIEWS = tuple(os.environ.get("LP_RIG_VIEWS", ",".join(VIEWS)).split(","))
STAY_S = float(os.environ.get("LP_RIG_STAY", "20"))
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
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
    try:
        app = App(home, command)
    except SystemExit:
        log = home / "logs" / "libre-panel.log"
        if log.exists():
            print("\n".join(log.read_text("utf-8", "replace").splitlines()[-60:]))
        raise
    main = psutil.Process(app.proc.pid)
    print("panel 1:", app.get("api/app")["panel"].get("state"), flush=True)
    began = time.monotonic()
    end = began + minutes * 60

    def sample(label=""):
        row = {}
        threads = None
        for p in [main] + main.children(recursive=True):
            try:
                name = p.name()
                kind = "main" if p.pid == main.pid else "engine" if "engine" in name else name
                info = p.memory_full_info()
                if kind == "main":
                    threads = p.num_threads()
                row.setdefault(kind, []).append(
                    (round(info.uss / 2**20, 1), round(info.private / 2**20, 1))
                )
            except psutil.Error:
                pass
        print(
            f"{time.monotonic() - began:5.0f} s  "
            + "  ".join(
                f"{k} " + " + ".join(f"{ws:.0f} ({commit:.0f})" for ws, commit in v)
                for k, v in row.items()
            )
            + f"   MB private working set (commit), threads {threads} {label}",
            flush=True,
        )

    def open_pages():
        """The app's pages in a browser, as its window shows them, each a while; then closed."""
        from playwright.sync_api import sync_playwright

        from libre_panel.editor import access

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            said = []  # (what the page said: why a view did not show)
            page.on("console", lambda m: said.append(f"console {m.type}: {m.text}"[:240]))
            page.on("pageerror", lambda e: said.append(f"pageerror: {e}"[:240]))
            page.on("requestfailed", lambda r: said.append(f"failed: {r.url} {r.failure}"[:240]))
            page.on("response", lambda r: said.append(f"{r.status} {r.url}"[:200])
                    if r.status >= 400 else None)  # fmt: skip
            for view in VIEWS:
                page.goto(access.with_token(f"{app.url}#/{view}", app.token))
                try:
                    page.wait_for_selector(".view > *", timeout=30_000)
                except Exception as exc:  # noqa: BLE001
                    print("  (the page", view, "did not show:", str(exc)[:80], ")")
                    print("\n".join(f"    {line}" for line in said[-30:]), flush=True)
                    said.clear()
                if view == VIEWS[-1] and STAY_S > 20:  # (a while there, looked at as it goes)
                    for _ in range(int(STAY_S // 30)):
                        page.wait_for_timeout(30_000)
                        sample(f"(the app open: {view})")
                else:
                    page.wait_for_timeout(20_000)
                    sample(f"(the app open: {view})")
            browser.close()
        sample("(the app closed)")

    opened = False
    while time.monotonic() < end and app.proc.poll() is None:
        time.sleep(15)
        if not opened and time.monotonic() - began >= OPEN_AT:
            opened = True
            open_pages()
            continue
        sample()
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
    try:
        print("panel 1 at the end:", app.get("api/app")["panel"].get("state"), flush=True)
    except OSError as exc:
        print("the app does not answer at the end:", exc)
    app.quit()

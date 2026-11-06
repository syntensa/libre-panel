"""MEASUREMENT (feature branch only): the Windows rig's pages did not show after 27dc8fa. The
app as the rig runs it (two simulated panels through the engine), its pages opened in
Chromium: does each view show, and what did the page and the server say; a picture of each
that did not show (into LP_PAGES_SHOTS).

    python .github/measure/pages_check.py ROOT [VIEWS]   (ROOT: the checkout whose code runs)"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
views = (sys.argv[2] if len(sys.argv) > 2 else "home,design,live").split(",")
here = root / ".github" / "measure"
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
            for name in ("blocks", "frames", "jpegs"):
                items = getattr(fake, name)
                if len(items) > 20:
                    del items[:-4]


threading.Thread(target=keep_short, daemon=True).start()
config = (
    'theme = "lumen"\n\n'
    '[device]\ndriver = "turzx"\nmodel = "turing-12.3-usb"\n\n'
    '[video]\nmode = "on"\nencoder = "native"\nlocal_clip = "usr/data/clip.h264"\n\n'
    '[sensors]\nproviders = ["demo"]\n\n'
    '[services]\nenabled = ["agents", "notices", "timer"]\n\n'
    '[[panels]]\ndriver = "turzx"\nmodel = "turing-9.2-usb"\n'
    '[panels.video]\nmode = "on"\nlocal_clip = "usr/data/clip.h264"\n'
)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    home = Path(folder)
    (home / "config.toml").write_text(config, encoding="utf-8")
    os.environ.update(
        LP_RIG_LINKS=",".join(f"{pid:04x}={s.link}" for pid, s in sockets.items()),
        PYTHONPATH=os.pathsep.join([str(here / "rig_site"), str(root / "src")]),
    )
    command = [sys.executable, "-m", "libre_panel", "tray", "--background", "--no-icon",
               "--port", "0"]  # fmt: skip
    app = App(home, command)
    print("panel 1:", app.get("api/app")["panel"].get("state"), flush=True)
    # (LP_PAGES_AFTER: as the rig, after light mode let go, its heaps tidied every 15 s)
    time.sleep(float(os.environ.get("LP_PAGES_AFTER", "20")))
    from playwright.sync_api import sync_playwright

    from libre_panel.editor import access

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        said = []
        page.on("console", lambda m: said.append(f"console {m.type}: {m.text}"[:300]))
        page.on("pageerror", lambda e: said.append(f"pageerror: {e}"[:300]))
        page.on("requestfailed", lambda r: said.append(f"failed: {r.url} {r.failure}"[:300]))
        page.on("response", lambda r: said.append(f"{r.status} {r.url}"[:200])
                if r.status >= 400 else None)  # fmt: skip
        for view in views:
            started = time.monotonic()
            page.goto(access.with_token(f"{app.url}#/{view}", app.token))
            try:
                page.wait_for_selector(".view > *", timeout=30_000)
                print(f"{view}: shown in {time.monotonic() - started:.1f} s", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"{view}: did not show ({str(exc)[:80]})", flush=True)
                shots = Path(os.environ.get("LP_PAGES_SHOTS", "."))
                shots.mkdir(parents=True, exist_ok=True)
                name = f"{root.name}_{view.replace('/', '_')}"
                page.screenshot(path=str(shots / f"{name}.png"))
                (shots / f"{name}.html").write_text(page.content(), encoding="utf-8")
            page.wait_for_timeout(5_000)
        browser.close()
        print("\n".join(said), flush=True)
        refused = sum("ERR_CONNECTION_REFUSED" in line and "failed:" in line for line in said)
        print(f"connections turned away: {refused}", flush=True)
    log = home / "logs" / "libre-panel.log"
    if log.exists():
        for line in log.read_text("utf-8", "replace").splitlines():
            if " WARNING " in line or " ERROR " in line or "Traceback" in line:
                print("  log:", line[:260])
    app.quit()

"""MEASUREMENT (feature branch only): what the app's window holds on Windows, WebView2's
processes beside the window's own: open on the app's pages (Start, Design, Live), then
minimized, then shown again. The window as it is (WebView2 told to hold less while it is
minimized: window.memory_target), as before (not told), and with browser flags to try beside
Libre Panel's own (LP_WV2_TRY: sets of them, split by ";"). The Task Manager's Memory column is
the private working set; the commit beside it.

    python .github/measure/webview2_memory.py"""

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

import psutil

here = Path(__file__).resolve().parent
root = here.parents[1]
sys.path[:0] = [str(root / "tools"), str(root / "src")]
from soak import App  # noqa: E402

TRY = [flags.strip() for flags in os.environ.get("LP_WV2_TRY", "").split(";") if flags.strip()]
RUN = (  # the window's process: as the app starts it, or as before (WebView2 not told)
    "import sys\n"
    "import libre_panel.window as w\n"
    "if sys.argv[1] == 'before':\n"
    "    w.memory_target = lambda *a, **k: False\n"
    "w.main(sys.argv[2:])\n"
)
CONFIG = (
    'theme = "lumen"\n\n[device]\ndriver = "virtual"\nmodel = "turing-12.3-usb"\n\n'
    '[sensors]\nproviders = ["demo"]\n'
)

user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
SW_MINIMIZE, SW_RESTORE = 6, 9


def windows_of(pid: int) -> list:
    found = []

    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(each), 0)
    return found


def kind_of(p: psutil.Process, window: int) -> str:
    if p.pid == window:
        return "window (Python)"
    if "msedgewebview2" not in p.name().lower():
        return p.name()
    words = p.cmdline()
    kind = next((w.split("=", 1)[1] for w in words if w.startswith("--type=")), "browser")
    if kind == "utility":
        sub = next((w.split("=", 1)[1] for w in words if w.startswith("--utility-sub-type=")), "")
        kind = "utility " + sub.rsplit(".", 1)[-1]
    return kind


def held(window: psutil.Process) -> dict[str, list[tuple[float, float]]]:
    """The window's process and those under it (WebView2's), by kind: (private working set,
    commit) in MB each."""
    rows: dict[str, list[tuple[float, float]]] = {}
    try:
        family = [window, *window.children(recursive=True)]
    except psutil.Error:
        return rows
    for p in family:
        try:
            info = p.memory_full_info()
            rows.setdefault(kind_of(p, window.pid), []).append(
                (info.uss / 2**20, info.private / 2**20)
            )
        except psutil.Error:
            pass
    return rows


def say(label: str, rows: dict) -> tuple[float, float]:
    ws = sum(a for v in rows.values() for a, _ in v)
    commit = sum(b for v in rows.values() for _, b in v)
    parts = "  ".join(f"{k} " + "+".join(f"{a:.0f}" for a, _ in v) for k, v in sorted(rows.items()))
    print(f"  {label:<26} all {ws:6.1f} MB ({commit:6.1f} committed)   {parts}", flush=True)
    return ws, commit


def session(app: App, variant: str, flags: str, window_module) -> dict[str, float]:
    arguments = " ".join(window_module.BROWSER_ARGUMENTS) + (f" {flags}" if flags else "")
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    env[window_module.WEBVIEW2_ARGUMENTS] = arguments  # (ours, and the flags to try)
    env["LIBRE_PANEL_TOKEN"] = app.token
    print(f"== {variant}{' + ' + flags if flags else ''}", flush=True)
    proc = subprocess.Popen([sys.executable, "-c", RUN, variant, app.url, "--view", "home"],
                            env=env)  # fmt: skip
    window = psutil.Process(proc.pid)
    hwnd = None
    for _ in range(120):
        found = windows_of(proc.pid)
        if found:
            hwnd = found[0]
            break
        time.sleep(0.5)
    if hwnd is None:
        print("  no window", flush=True)
        proc.kill()
        return {}
    time.sleep(20)
    say("open: Start", held(window))
    window_module.focus_window(app.url, "design", app.token)
    time.sleep(25)
    say("open: Design", held(window))
    window_module.focus_window(app.url, "live", app.token)
    time.sleep(40)
    seen = {}
    for n in range(3):
        seen["open"] = say(f"open: Live ({n + 1})", held(window))[0]
        time.sleep(5)
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    time.sleep(30)
    for n in range(3):
        seen["minimized"] = say(f"minimized ({n + 1})", held(window))[0]
        time.sleep(5)
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(20)
    for n in range(2):
        seen["shown again"] = say(f"shown again ({n + 1})", held(window))[0]
        time.sleep(5)
    window_module.close_window(app.url)
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        for p in window.children(recursive=True):
            p.kill()
        proc.kill()
    time.sleep(5)  # (WebView2's processes end after the window's)
    return seen


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    home = Path(folder)
    (home / "config.toml").write_text(CONFIG, encoding="utf-8")
    os.environ.update(LIBRE_PANEL_HOME=str(home), PYTHONPATH=str(root / "src"))
    from libre_panel import window as window_module

    app = App(home, [sys.executable, "-m", "libre_panel", "tray", "--background", "--no-icon",
                     "--port", "0"])  # fmt: skip
    try:
        print("(a first window, not counted: WebView2's profile made)", flush=True)
        session(app, "now", "", window_module)
        results = []
        for variant, flags in [("before", ""), ("now", ""), *[("now", f) for f in TRY],
                               ("before", ""), ("now", "")]:  # fmt: skip
            results.append((variant, flags, session(app, variant, flags, window_module)))
        print("\nprivate working set of the window and WebView2's processes, MB:", flush=True)
        for variant, flags, seen in results:
            cells = "  ".join(f"{k} {v:6.1f}" for k, v in seen.items())
            print(f"  {variant:<7}{(' + ' + flags) if flags else '':<44} {cells}", flush=True)
    finally:
        app.quit()

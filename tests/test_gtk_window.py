"""The app's window drawn by GTK and WebKitGTK (gtk_window.py), for real: in a Python that has
PyGObject with WebKitGTK, on a display of Xvfb's. Where there is none (most test machines),
these are skipped; tests/test_window.py checks the rest with a fake."""

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from libre_panel import window

SRC = str(Path(__file__).resolve().parent.parent / "src")
PROBE = (
    "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1'); "
    "from gi.repository import Gtk, WebKit2"
)


def _gtk_python():
    """A Python here with PyGObject and WebKitGTK 4.1, and xvfb-run for a display."""
    if not sys.platform.startswith("linux") or shutil.which("xvfb-run") is None:
        return None
    for name in ("python3", "python3.14", "python3.13", "python3.12", "python3.11"):
        found = shutil.which(name, path="/usr/bin:/usr/local/bin")
        if found and subprocess.run([found, "-c", PROBE], capture_output=True,
                                    timeout=30).returncode == 0:  # fmt: skip
            return found
    return None


PYTHON = _gtk_python()
pytestmark = pytest.mark.skipif(PYTHON is None, reason="no Python with GTK and WebKitGTK here")


class Page(http.server.BaseHTTPRequestHandler):
    """The app's page, as far as the window sees it: it calls window.pywebview.api."""

    body = b""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(Page.body)

    def log_message(self, *args):
        pass


@pytest.fixture
def page():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


def environment(home):
    return {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
            "PYTHONPATH": SRC, window.GTK_ENV: "1"}  # fmt: skip


BRIDGE = """
import json, sys, threading, time
from libre_panel import gtk_window
seen = []
class Api:
    def set_title(self, title): seen.append(["set_title", title]); win.set_title(title)
    def set_dirty(self, dirty): seen.append(["set_dirty", dirty]); win.confirm_close = dirty
    def note(self, *what): seen.append(["note", *what])
win = gtk_window.create_window("Libre Panel", sys.argv[1], js_api=Api(), width=800, height=600)
loaded = []
win.events.loaded += lambda: loaded.append(1)
def later():
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any(s[0] == "note" for s in seen):
        time.sleep(0.1)
    win.evaluate_js("window.pywebview.api.note('js', 6 * 7)")
    time.sleep(1)
    seen.append(["title", win._gtk.get_title() if win._gtk else None])
    win.destroy()
threading.Thread(target=later, daemon=True).start()
gtk_window.start(private_mode=True)
print(json.dumps({"seen": seen, "loaded": len(loaded)}), flush=True)
import os; os._exit(0)
"""


def test_the_page_talks_to_its_window_as_with_pywebview(page, tmp_path):
    """window.pywebview.api and its "pywebviewready": the title, unsaved changes, a call from
    outside the page; and the loaded event."""
    Page.body = b"""<!doctype html><title>t</title><script>
      window.addEventListener("pywebviewready", () => {
        window.pywebview.api.set_title("Lumen - Libre Panel");
        window.pywebview.api.set_dirty(true);
        window.pywebview.api.note("ready");
      });</script><p>the app</p>"""
    done = subprocess.run(["xvfb-run", "-a", PYTHON, "-c", BRIDGE, page], capture_output=True,
                          env=environment(tmp_path), timeout=90, text=True)  # fmt: skip
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert said["loaded"] >= 1
    assert said["seen"][:3] == [["set_title", "Lumen - Libre Panel"], ["set_dirty", True],
                                ["note", "ready"]]  # fmt: skip
    assert ["note", "js", 42] in said["seen"]
    assert ["title", "Lumen - Libre Panel"] in said["seen"]


def test_a_release_builds_window_opens_comes_to_the_front_and_closes(page, tmp_path, monkeypatch):
    """As the AppImage opens it: the system's Python, ``-m libre_panel.window``; another start
    finds it, brings it to the front, closes it; it ends cleanly and keeps its size."""
    Page.body = b"<!doctype html><title>Libre Panel</title><p>the app</p>"
    env = environment(tmp_path)
    for name in ("HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, env[name])
    process = subprocess.Popen(["xvfb-run", "-a", PYTHON, "-m", "libre_panel.window", page,
                                "--view", "home"], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)  # fmt: skip
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and window.running() is None:
            time.sleep(0.2)
        assert window.running() is not None
        time.sleep(1)
        assert window.focus_window(page, "design")
        assert window.close_window(page)
        assert process.wait(timeout=30) == 0  # (no fault as Python ends: it ends before)
    finally:
        if process.poll() is None:
            process.kill()
    assert window.running() is None
    from libre_panel.config import config_dir

    state = json.loads((config_dir() / window.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["width"] >= window.MIN_SIZE[0] and state["maximized"] is False

"""The app's own window (pywebview), with a fake pywebview: no real window opens."""

import json
import os
import socket
import sys
import threading
from types import SimpleNamespace

import pytest

from libre_panel import window

URL = "http://127.0.0.1:8765/"


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def set(self, *args):
        for handler in self.handlers:
            handler(*args)


class FakeWindow:
    def __init__(self, title, url, **options):
        self.title, self.url, self.options = title, url, options
        self.confirm_close = False
        self.events = SimpleNamespace(
            **{
                name: FakeEvent()
                for name in ("resized", "maximized", "minimized", "restored", "closed")
            }
        )
        self.calls = []
        self._on_top = False

    def evaluate_js(self, script):
        self.calls.append(("js", script))

    def set_title(self, title):
        self.title = title

    def load_url(self, url):
        self.calls.append(("load", url))

    def restore(self):
        self.calls.append(("restore",))

    def show(self):
        self.calls.append(("show",))

    def destroy(self):
        self.calls.append(("destroy",))

    @property
    def on_top(self):
        return self._on_top

    @on_top.setter
    def on_top(self, value):
        self._on_top = value
        self.calls.append(("on_top", value))


class FakeWebview:
    """What run_window uses of pywebview; start() plays a short session."""

    def __init__(self, session=None, screen=(2560, 1440)):
        self.settings = {}
        self.screens = [SimpleNamespace(width=screen[0], height=screen[1])]
        self.windows = []
        self.started = None
        self.session = session

    def create_window(self, title, url, **options):
        self.windows.append(FakeWindow(title, url, **options))
        return self.windows[-1]

    def start(self, **options):
        self.started = options
        if self.session:
            self.session(self.windows[0])


def test_addresses_of_views():
    assert window.view_url(URL, "design") == URL + "#/design"
    assert window.view_url("http://127.0.0.1:8765/#/look", "live") == URL + "#/live"
    assert window.view_url("http://127.0.0.1:8765", None) == URL
    assert window.local_url(URL + "#/home") and window.local_url("http://localhost:9/")
    assert not window.local_url("https://example.org/") and not window.local_url("file:///x")


def test_the_command_that_opens_the_window(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = window.window_command(URL)
    assert command[1:] == ["-m", "libre_panel", "window", URL]
    exe = tmp_path / "libre-panel.exe"
    (tmp_path / window.WINDOWED_EXE).write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert window.window_command(URL) == [str(tmp_path / window.WINDOWED_EXE), "window", URL]
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", "/apps/Libre_Panel.AppImage")
    config = tmp_path / "config.toml"
    assert window.window_command(URL, config) == [
        "/apps/Libre_Panel.AppImage", "--config", str(config.resolve()), "window", URL,
    ]  # fmt: skip


def test_without_pywebview_the_browser_opens(monkeypatch):
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    monkeypatch.setattr(window, "installed", lambda: False)
    assert window.open_window(URL, "live") == "browser"
    monkeypatch.setattr(window, "installed", lambda: True)
    assert window.open_window(URL, "home", browser=True) == "browser"
    assert opened == [URL + "#/live", URL + "#/home"]


def test_a_window_process_starts_when_none_is_open(monkeypatch):
    started = []
    monkeypatch.setattr(window, "installed", lambda: True)
    monkeypatch.setattr(
        window.subprocess, "Popen", lambda command, **kw: started.append((command, kw))
    )
    assert window.open_window(URL, "design") == "window"
    command, options = started[0]
    assert command[-2:] == ["window", URL + "#/design"]
    assert options["stdin"] == window.subprocess.DEVNULL  # it lives on its own


def answering_window(fake):
    """A window that listens as an open one does, and the file that names it."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    shown = {"url": URL + "#/home", "minimized": True}
    window._Requests(listener, "secret", fake, shown).start()
    path = window._running_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "port": listener.getsockname()[1], "token": "secret", "url": URL}
    path.write_text(json.dumps(info))
    return listener, shown


def test_an_open_window_comes_to_the_front_on_the_view(monkeypatch):
    fake = FakeWindow("Libre Panel", URL)
    listener, shown = answering_window(fake)
    monkeypatch.setattr(window.time, "sleep", lambda s: None)
    try:
        assert window.open_window(URL, "design") == "focused"
        assert ("js", 'location.hash = "#/design"') in fake.calls  # no reload: nothing is lost
        assert ("restore",) in fake.calls and ("show",) in fake.calls  # it was minimized
        assert fake.calls[-2:] == [("on_top", True), ("on_top", False)]
        # another server (the editor started anew): the page loads from there
        other = "http://127.0.0.1:9999/"
        assert window.focus_window(other, "look")
        assert ("load", other + "#/look") in fake.calls and shown["url"] == other + "#/look"
        # a wrong token, or a page that is not Libre Panel's, is refused
        info = window.running()
        assert not window._ask({**info, "token": "guess"}, ["focus", URL])
        assert not window._ask(info, ["focus", "https://example.org/"])
    finally:
        listener.close()


def test_closing_only_a_window_of_this_server(monkeypatch):
    fake = FakeWindow("Libre Panel", URL)
    listener, _shown = answering_window(fake)
    try:
        assert not window.close_window("http://127.0.0.1:9999/")
        assert ("destroy",) not in fake.calls
        assert window.close_window(URL)
        assert ("destroy",) in fake.calls
    finally:
        listener.close()


def test_a_gone_window_is_not_asked(monkeypatch):
    import psutil

    path = window._running_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 4242, "port": 9, "token": "x"}))
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    assert window.running() is None and not window.focus_window(URL)


def test_the_window(monkeypatch):
    """Title, size (kept from last time, at most the screen), the page's controls, one at a time."""
    window.save_state({"width": 3000, "height": 1000, "maximized": False})
    seen = {}

    def session(win):
        seen["running"] = window.running()
        bridge = win.options["js_api"]
        bridge.set_dirty(True)  # unsaved changes: closing asks first
        seen["confirm"] = win.confirm_close
        bridge.set_title("● Studio – Libre Panel")  # the page's title in the title bar
        seen["title"] = win.title
        bridge.set_title("  ")
        seen["no title"] = win.title
        assert window.open_window(URL, "home") == "focused"  # one window at a time
        win.events.resized.set(1500, 950)
        win.events.closed.set()

    monkeypatch.setattr(window.time, "sleep", lambda s: None)
    monkeypatch.setattr(window, "installed", lambda: True)
    monkeypatch.setattr(window.subprocess, "Popen", lambda *a, **k: pytest.fail("a second window"))
    fake = FakeWebview(session, screen=(2560, 1440))
    assert window.run_window(URL, "design", webview=fake) == 0
    win = fake.windows[0]
    assert win.title == "Libre Panel" and win.url == URL + "#/design"
    assert (win.options["width"], win.options["height"]) == (2560, 1000)
    assert (
        win.options["min_size"] == window.MIN_SIZE and win.options["background_color"] == "#0A0E13"
    )
    assert seen["running"]["pid"] == os.getpid() and seen["confirm"] is True
    assert seen["title"] == "● Studio – Libre Panel" and seen["no title"] == "Libre Panel"
    assert fake.settings["ALLOW_DOWNLOADS"] is True
    assert fake.started["private_mode"] is False  # the app's own settings stay
    assert "global.quitConfirmation" in fake.started["localization"]
    assert window.running() is None  # the window is gone
    assert window.load_state() == {"width": 1500, "height": 950, "maximized": False}


def test_a_maximized_window_keeps_its_normal_size(monkeypatch):
    window.save_state({"width": 1300, "height": 800, "maximized": False})

    def session(win):
        win.events.maximized.set()
        win.events.resized.set(2560, 1400)  # the maximized size is not the one to keep

    assert window.run_window(URL, None, webview=FakeWebview(session)) == 0
    assert window.load_state() == {"width": 1300, "height": 800, "maximized": True}


def test_without_a_backend_the_browser_opens(monkeypatch):
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    monkeypatch.setattr(window, "load_backend", lambda: None)
    assert window.run_window(URL, "settings") == 0
    assert opened == [URL + "#/settings"]


def test_only_libre_panels_own_pages(monkeypatch):
    monkeypatch.setattr(window, "load_backend", lambda: pytest.fail("no window for it"))
    assert window.run_window("https://example.org/") == 2


def test_check_without_pywebview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # importing it fails
    worked, lines = window.check()
    assert not worked and lines[0].startswith("pywebview: not available")
    assert window.load_backend() is None


def test_cli_window_command(monkeypatch, capsys):
    from libre_panel import cli

    monkeypatch.setattr(
        window, "check", lambda: (True, ["backend: winforms, renderer edgechromium"])
    )
    assert cli.main(["window", "--check"]) == 0
    assert "edgechromium" in capsys.readouterr().out
    shown = []
    monkeypatch.setattr(window, "run_window", lambda url, view: shown.append((url, view)) or 0)
    assert cli.main(["window", URL, "--view", "live"]) == 0
    assert cli.main(["window", URL, "--view", "nonsense"]) == 0
    assert shown == [(URL, "live"), (URL, None)]


def test_the_background_app_opens_its_window(monkeypatch, isolated_home):
    from libre_panel.service import EditorService

    opened = []
    monkeypatch.setattr(window, "open_window", lambda url, view, **kw: opened.append((url, view)))
    service = EditorService(port=0)
    try:
        url = service.open("settings")
        assert opened == [(url, "settings")]
    finally:
        service.stop()


def test_the_editor_command(monkeypatch):
    """``libre-panel editor`` serves the app in a window of its own (``--browser``: not)."""
    from libre_panel import cli
    from libre_panel.editor import server

    calls = []
    monkeypatch.setattr(server, "serve", lambda **kw: calls.append(kw))
    assert cli.main(["editor", "--browser", "--port", "0"]) == 0
    assert cli.main(["editor", "--port", "0"]) == 0
    assert [c["browser"] for c in calls] == [True, False]


def test_serve_opens_the_window_and_stops_with_it(monkeypatch):
    from libre_panel.editor import server

    fake = FakeWebview()
    shown = []
    monkeypatch.setattr(window, "load_backend", lambda: fake)
    monkeypatch.setattr(
        window, "run_window", lambda url, view, webview: shown.append((url, view, webview))
    )
    done = threading.Event()
    thread = threading.Thread(target=lambda: (server.serve(port=0), done.set()), daemon=True)
    thread.start()
    assert done.wait(10)  # the window closed: the server stopped too
    url, view, backend = shown[0]
    assert url.startswith("http://127.0.0.1:") and view == "design" and backend is fake

"""The app's own window (pywebview), with a fake pywebview: no real window opens."""

import json
import os
import socket
import sys
import threading
from types import SimpleNamespace

import pytest

from libre_panel import window
from libre_panel.editor import access

URL = "http://127.0.0.1:8765/"
TOKEN, OTHER = "T" * 43, "O" * 43  # access tokens of the app's server (editor/access.py)


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


class OpenProcess:
    """A window's process that stays open: the watch on it times out."""

    def wait(self, timeout=None):
        raise window.subprocess.TimeoutExpired("window", timeout)


def started_into(started):
    """A Popen stand-in that notes (command, options) and gives a process that stays open."""

    def popen(command, **options):
        started.append((command, options))
        return OpenProcess()

    return popen


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
    monkeypatch.setattr(window.subprocess, "Popen", started_into(started))
    assert window.open_window(URL, "design") == "window"
    command, options = started[0]
    assert command[-2:] == ["window", URL + "#/design"]
    assert options["stdin"] == window.subprocess.DEVNULL  # it lives on its own


def test_the_window_says_what_goes_wrong_in_a_log_of_its_own(monkeypatch):
    """Its error output goes to logs/window.log: a crash of .NET in it is not lost."""
    started = []
    monkeypatch.setattr(window, "installed", lambda: True)
    monkeypatch.setattr(window.subprocess, "Popen", started_into(started))
    assert window.open_window(URL, "design") == "window"
    errors = started[0][1]["stderr"]
    assert errors.name == str(window.window_log()) and errors.closed  # the process has its own
    assert "the window starts" in window.window_log().read_text(encoding="utf-8")


class EndedProcess:
    def __init__(self, code):
        self.code = code

    def wait(self, timeout=None):
        return self.code


def join_watches():
    for thread in [t for t in threading.enumerate() if t.name == "window-watch"]:
        thread.join(5)


@pytest.mark.parametrize("code, browser", [(0xE0434352, True), (1, True), (0, False)])
def test_a_window_that_ends_at_once_with_an_error_leaves_the_browser(monkeypatch, code, browser):
    """0.5.0's window died at once on Windows and nothing said so: now the app opens in the
    browser instead (with the token), and the log says where to look. One that ends well (it
    brought another to the front, or was closed) does not."""
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    window._watch(EndedProcess(code), URL + "#/design", TOKEN)
    join_watches()
    assert opened == ([access.with_token(URL + "#/design", TOKEN)] if browser else [])


def test_a_window_that_stays_open_is_left_alone(monkeypatch):
    monkeypatch.setattr(window.webbrowser, "open", lambda url: pytest.fail("the browser"))
    window._watch(OpenProcess(), URL, TOKEN, settle=0.01)
    join_watches()


def test_on_windows_the_window_gets_no_png_for_its_icon():
    """pywebview's WinForms makes a System.Drawing.Icon of the file, which a PNG is not: the
    window died at once. Without one it takes the program's own icon."""
    assert window._icon("win32") is None
    assert window._icon("linux").endswith("icon.png")


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


def test_a_garbled_request_leaves_the_window_answering(monkeypatch):
    """Anything on this machine can connect to the window's port. A line it cannot use (a
    token that is not ASCII, a window call that fails) is refused, and the next request is
    answered: else a second start opened a second window, and quitting could not close it."""
    fake = FakeWindow("Libre Panel", URL)
    listener, _shown = answering_window(fake)
    monkeypatch.setattr(window.time, "sleep", lambda s: None)
    info = window.running()
    try:
        assert not window._ask({**info, "token": "géheim"}, ["close"])
        monkeypatch.setattr(fake, "show", lambda: 1 / 0)  # the window says no
        assert not window._ask(info, ["focus", URL + "#/look"])
        assert window.close_window(URL)
        assert ("destroy",) in fake.calls
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

    asked = []
    monkeypatch.setattr(
        window,
        "check",
        lambda open_one: (
            asked.append(open_one) or (True, ["backend: winforms, renderer edgechromium"])
        ),
    )
    assert cli.main(["window", "--check"]) == 0
    assert "edgechromium" in capsys.readouterr().out
    assert cli.main(["window", "--check", "--open"]) == 0  # a real window, for a moment
    assert asked == [False, True]
    shown = []
    monkeypatch.setattr(window, "run_window", lambda url, view: shown.append((url, view)) or 0)
    assert cli.main(["window", URL, "--view", "live"]) == 0
    assert cli.main(["window", URL, "--view", "nonsense"]) == 0
    assert shown == [(URL, "live"), (URL, None)]


def test_the_background_app_opens_its_window(monkeypatch, isolated_home):
    from libre_panel.service import EditorService

    opened, tokens = [], []
    monkeypatch.setattr(
        window,
        "open_window",
        lambda url, view, **kw: opened.append((url, view)) or tokens.append(kw["token"]),
    )
    service = EditorService(port=0)
    try:
        url = service.open("settings")
        assert opened == [(url, "settings")]
        assert tokens == [service.token] and service.token  # (the address has none)
    finally:
        service.stop()


# -- the access token of the app's server (editor/access.py) -----------------------------------


def test_the_window_process_is_told_the_token_apart_from_its_command_line(monkeypatch):
    """Other users read a command line (Linux); the environment of a process is its user's."""
    started = []
    monkeypatch.setattr(window, "installed", lambda: True)
    monkeypatch.setattr(window.subprocess, "Popen", started_into(started))
    assert window.open_window(URL, "design", token=TOKEN) == "window"
    command, options = started[0]
    assert command[-2:] == ["window", URL + "#/design"]
    assert TOKEN not in " ".join(command) and options["env"][access.ENV] == TOKEN
    # a start that knows none finds the token the running app wrote down for its user
    access.publish(OTHER, 8765)
    window.open_window(URL, "design")
    assert started[1][1]["env"][access.ENV] == OTHER
    window.open_window(URL + "#token=" + TOKEN, "look")  # or has it in the address it is given
    assert started[2][1]["env"][access.ENV] == TOKEN and TOKEN not in " ".join(started[2][0])
    access.token_file().unlink()
    window.open_window(URL, "design")  # none to tell
    assert started[3][1]["env"] is None


def test_the_browser_gets_the_address_with_the_token(monkeypatch):
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    monkeypatch.setattr(window, "installed", lambda: False)
    window.open_window(URL, "live", token=TOKEN)
    window.open_window(URL + "#/look&token=" + OTHER, "live")  # (the view is the one asked for)
    access.publish(TOKEN, 8765)
    window.open_window(URL, None)
    access.publish(TOKEN, 9999)  # the file is about another server
    window.open_window(URL, "home")
    assert opened == [
        URL + "#/live&token=" + TOKEN,
        URL + "#/live&token=" + OTHER,
        URL + "#token=" + TOKEN,
        URL + "#/home",
    ]


def test_an_open_window_is_given_the_token_for_its_page(monkeypatch):
    fake = FakeWindow("Libre Panel", URL)
    listener, shown = answering_window(fake)
    monkeypatch.setattr(window.time, "sleep", lambda s: None)
    try:
        assert window.open_window(URL, "design", token=TOKEN) == "focused"
        # the page moves to the route and takes the token, which it takes out of its address
        assert ("js", f'location.hash = "#/design&token={TOKEN}"') in fake.calls
        assert shown["url"] == URL + "#/design"  # (the page is known by its name alone)
        assert window.focus_window(URL, None, OTHER)  # a token alone leaves the route
        assert ("js", f'location.hash = "#token={OTHER}"') in fake.calls
        # another server (the app started anew): the page loads from there, with its token
        other = "http://127.0.0.1:9999/"
        assert window.focus_window(other, "look", TOKEN)
        assert ("load", other + "#/look&token=" + TOKEN) in fake.calls
        assert shown["url"] == other + "#/look"
        # asked for a page with no token to give, it loads the page and the page asks for one
        calls = len(fake.calls)
        assert window.focus_window(URL)
        assert fake.calls[calls:][0] == ("load", URL)
    finally:
        listener.close()


def test_the_window_loads_the_app_with_the_token_in_its_address(monkeypatch):
    seen = {}

    def session(win):
        seen["file"] = window._running_file().read_text()
        seen["running"] = window.running()

    fake = FakeWebview(session)
    assert window.run_window(URL, "design", webview=fake, token=TOKEN) == 0
    assert fake.windows[0].url == URL + "#/design&token=" + TOKEN  # (it takes it out of there)
    assert TOKEN not in seen["file"] and seen["running"]["url"] == URL + "#/design"


def test_the_window_finds_the_token_where_the_one_that_started_it_left_it(monkeypatch):
    """What it is told wins; then what the address has, the environment, the file."""

    def opened(url, **kwargs):
        fake = FakeWebview()
        assert window.run_window(url, "look", webview=fake, **kwargs) == 0
        return fake.windows[0].url

    assert opened(URL, token=TOKEN) == URL + "#/look&token=" + TOKEN
    assert opened(URL + "#token=" + OTHER) == URL + "#/look&token=" + OTHER
    assert opened(URL + "#token=" + OTHER, token=TOKEN) == URL + "#/look&token=" + TOKEN
    monkeypatch.setenv(access.ENV, TOKEN)
    assert opened(URL) == URL + "#/look&token=" + TOKEN
    assert access.ENV not in os.environ  # what the window starts does not get it
    access.publish(OTHER, 8765)
    assert opened(URL) == URL + "#/look&token=" + OTHER
    assert opened("http://127.0.0.1:9999/") == "http://127.0.0.1:9999/#/look"  # not its token
    monkeypatch.setenv(access.ENV, TOKEN)
    assert opened(URL, token=OTHER) == URL + "#/look&token=" + OTHER
    assert access.ENV not in os.environ  # (taken out in any case)


def test_a_window_without_a_backend_opens_the_browser_with_the_token(monkeypatch):
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    monkeypatch.setattr(window, "load_backend", lambda: None)
    assert window.run_window(URL, "settings", token=TOKEN) == 0
    assert opened == [URL + "#/settings&token=" + TOKEN]


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
        window,
        "run_window",
        lambda url, view, webview, token: shown.append((url, view, webview, token)),
    )
    done = threading.Event()
    thread = threading.Thread(target=lambda: (server.serve(port=0), done.set()), daemon=True)
    thread.start()
    assert done.wait(10)  # the window closed: the server stopped too
    url, view, backend, token = shown[0]
    assert url.startswith("http://127.0.0.1:") and view == "design" and backend is fake
    assert url.endswith("/") and len(token) >= 40  # the token comes apart from the address

"""Libre Panel's own window: the app (start, design, look, live, settings) in a native
window through pywebview: WebView2 on Windows, WebKitGTK on Linux.

There is one window at a time. Whoever wants the app shown (the tray, ``libre-panel
start``, ``libre-panel editor``, a second start) calls :func:`open_window`: an open window
is brought to the front and shows the asked view; otherwise a new window process starts
(``libre-panel window <url>``: pywebview needs a main thread of its own, which the tray
already uses). Without pywebview, or without a backend that can show the app (no GTK with
WebKit, no WebView2 runtime, no desktop), the browser opens instead.

A window process holds a lock (``window.lock``) from its first moment to its last: one that
starts while another is open, or still opening (.NET and WebView2 take seconds), never shows
a window of its own; it waits for the other to answer and brings that one to the front.

The open window answers on a local port, with a token from a file only this user can
read, so another start finds it: ``focus <token> <url>`` and ``close <token>``. It answers
at once and then acts on its window, in a thread of its own: a page that is still loading
does not keep the other start waiting (which then opened a second window).

The app's server wants a token of its own (editor/access.py): the window brings it to the
page in the address, after a ``#``. It is not given to a program on its command line, which
other users of a Linux PC can read: a window process gets it in its environment, or reads it
from the file the running app wrote.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from libre_panel.config import config_dir
from libre_panel.editor import access
from libre_panel.i18n import t
from libre_panel.instance import AlreadyRunning, InstanceLock

log = logging.getLogger(__name__)

VIEWS = ("home", "design", "look", "live", "settings")
TITLE = "Libre Panel"
MIN_SIZE = (1100, 700)
DEFAULT_SIZE = (1440, 900)
BACKGROUND = "#0A0E13"  # the app's background: no white flash while it loads
RUNNING_FILENAME = "window.json"  # the open window: pid, port, token
LOCK_FILENAME = "window.lock"  # held by the window's process, opening or open
OPENING_S = 30.0  # how long a second start waits for a window that is still opening
STATE_FILENAME = "window-state.json"  # its size, kept for the next time
WINDOWED_EXE = "LibrePanel.exe"  # the Windows build without a console window
ANSWER_S = 2.0  # how long a start waits for the open window to answer
WINDOW_LOG = "window.log"  # next to the tray's log: what the window process says, and why it ended
SETTLE_S = 15.0  # a window that ends with an error this soon did not open: the browser instead

# WebView2's processes, lighter (Windows): what its browser engine is started with beside
# pywebview's own arguments. Each is in Microsoft's list of WebView2's browser flags or among
# Chromium's switches, and none changes what the window shows or how it is used: the app is
# one page from Libre Panel's own server on this PC, which navigates nowhere else.
BROWSER_ARGUMENTS = (
    "--renderer-process-limit=1",  # one page: one renderer process (Chromium's)
    # no warm spare renderer kept for a page of another site, which the app never opens (its
    # links open in the browser): one process fewer (in WebView2's list)
    "--disable-features=SpareRendererForSitePerProcess",
    "--disk-cache-size=1",  # no HTTP cache on the disk for pages from this PC (in the list)
    "--disable-gpu-shader-disk-cache",  # nor one of the GPU's shaders (Chromium's)
)
# pywebview has no setting for them: WebView2 reads them from its environment variable when the
# window's engine starts, and appends them to the app's (the lists of features are joined). An
# elevated program does not read it (Microsoft's rule): a window of an app started with the
# highest rights opens as before.
WEBVIEW2_ARGUMENTS = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"


def view_url(url: str, view: str | None) -> str:
    """The app's address with a view: ``http://127.0.0.1:8765/#/design``."""
    base = url.split("#", 1)[0]
    if not base.endswith("/"):
        base += "/"
    return f"{base}#/{view}" if view else base


def local_url(url: str) -> bool:
    """Only Libre Panel's own server is opened in its window (it gets the window's controls)."""
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")


def installed() -> bool:
    """pywebview is there (whether it has a backend is found out in the window process)."""
    return importlib.util.find_spec("webview") is not None


def window_command(url: str, config_path: Path | None = None) -> list[str]:
    """The command that opens the window, for this program as it was started."""
    options = ["--config", str(Path(config_path).resolve())] if config_path else []
    if getattr(sys, "frozen", False):  # release build
        appimage = os.environ.get("APPIMAGE")
        if appimage and sys.platform.startswith("linux"):
            return [appimage, *options, "window", url]
        exe = Path(sys.executable)
        if sys.platform == "win32" and exe.with_name(WINDOWED_EXE).exists():
            exe = exe.with_name(WINDOWED_EXE)  # no console window behind it
        return [str(exe), *options, "window", url]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    return [str(python), "-m", "libre_panel", *options, "window", url]


def _detached() -> dict[str, Any]:
    """Popen options for a process that lives on its own (and leaves no console behind)."""
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        options["creationflags"] = flags
    else:
        options["start_new_session"] = True
    return options


def window_log() -> Path:
    """Where the window process writes what it says (it has no terminal): logs/window.log."""
    from libre_panel.config import log_file

    return log_file().with_name(WINDOW_LOG)


def _error_output() -> Any:
    """The file the window's error output goes to, so that a failure of .NET or of pywebview
    is not lost; DEVNULL when it cannot be written. It is started anew past a megabyte."""
    path = window_log()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 1_000_000:
            path.replace(path.with_name(WINDOW_LOG + ".1"))
        output = open(path, "ab")  # noqa: SIM115 - handed to the process, closed after Popen
        output.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} the window starts\n".encode())
        output.flush()
        return output
    except OSError:
        return subprocess.DEVNULL


def _watch(process: Any, target: str, token: str | None, settle: float = SETTLE_S) -> None:
    """Open the app in the browser if the window's process ends with an error before it has
    been open for ``settle`` seconds: nobody is left without the app (a window that cannot
    start on this PC, as one with a broken WebView2)."""

    def watch() -> None:
        try:
            code = process.wait(settle)
        except subprocess.TimeoutExpired:
            return  # it is open
        if code == 0:
            return  # it ended as it should: another window came to the front, or it was closed
        log.warning(
            "Libre Panel's window ended at once (exit code %s; %s says why): "
            "the app opens in the browser instead",
            code,
            window_log(),
        )
        webbrowser.open(access.with_token(target, token))

    threading.Thread(target=watch, name="window-watch", daemon=True).start()


# -- the open window, from outside ------------------------------------------------------


def _running_file() -> Path:
    return config_dir() / RUNNING_FILENAME


def running() -> dict[str, Any] | None:
    """What the open window wrote about itself, if a window is open."""
    import psutil

    try:
        info = json.loads(_running_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not isinstance(info.get("port"), int):
        return None
    if not psutil.pid_exists(info.get("pid", -1)):
        return None
    return info


def _ask(info: dict[str, Any], words: list[str]) -> bool:
    """Send one request to the open window; True when it answered "ok"."""
    line = " ".join([words[0], str(info.get("token", "")), *words[1:]]) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=ANSWER_S) as conn:
            conn.sendall(line.encode("utf-8"))
            return conn.makefile("rb").readline().strip() == b"ok"
    except OSError:
        return False


def _address(url: str, view: str | None, token: str | None = None) -> str:
    """The address a window or a browser opens: ``url`` on ``view``, with the access token of
    the app (the one given, the one in ``url``, or the one the running app wrote down)."""
    base, inside = access.split_token(url)
    return access.with_token(view_url(base, view), token or inside or access.read_token(base))


def _allow_foreground(pid: Any) -> None:
    """Windows lets only the program the user just started bring a window to the front: this
    start passes that on to the window's process (else its window only flashes in the
    taskbar)."""
    if sys.platform != "win32" or not isinstance(pid, int):
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(pid)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def focus_window(url: str, view: str | None = None, token: str | None = None) -> bool:
    """Bring the open window to the front, showing ``view`` of ``url``; False when none is open.
    ``token`` is the access token of ``url``'s server, if the caller knows it."""
    info = running()
    if not info:
        return False
    _allow_foreground(info.get("pid"))
    return _ask(info, ["focus", _address(url, view, token)])


def _window_lock() -> InstanceLock:
    return InstanceLock(config_dir() / LOCK_FILENAME)


def _wait_for_window(url: str, view: str | None, token: str | None) -> bool:
    """Another window process holds the lock: its window is open or opening. Bring it to the
    front as soon as it answers; False when it has not after :data:`OPENING_S`."""
    deadline = time.monotonic() + OPENING_S
    while True:
        if focus_window(url, view, token):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def close_window(url: str | None = None) -> bool:
    """Close the open window (only one that shows ``url``'s server, when given)."""
    info = running()
    if not info or (url and view_url(info.get("url", ""), None) != view_url(url, None)):
        return False
    return _ask(info, ["close"])


def open_window(
    url: str,
    view: str | None = "home",
    browser: bool = False,
    config_path: Path | None = None,
    token: str | None = None,
) -> str:
    """Show the app: the open window comes to the front (on ``view``), or a new one opens,
    or, without pywebview (or with ``browser``), the browser. Returns "focused", "window"
    or "browser". ``token`` is the access token of ``url``'s server (the running app wrote it
    down for the programs of this user, so a caller in another process needs not know it)."""
    base, inside = access.split_token(url)
    target = view_url(base, view)
    token = token or inside or access.read_token(base)
    if not browser:
        if focus_window(base, view, token):
            return "focused"
        if installed():
            # the token goes in the environment: a command line is for everybody to read
            env = {**os.environ, access.ENV: token} if token else None
            errors = _error_output()
            try:
                process = subprocess.Popen(  # noqa: S603
                    window_command(target, config_path),
                    env=env,
                    **{**_detached(), "stderr": errors},
                )
            except OSError as exc:
                log.warning("could not open Libre Panel's window: %s", exc)
            else:
                _watch(process, target, token)
                return "window"
            finally:
                if errors is not subprocess.DEVNULL:
                    errors.close()  # the process has a handle of its own
    webbrowser.open(access.with_token(target, token))
    return "browser"


# -- the window itself (on the main thread of its own process) ------------------------------


def _state_file() -> Path:
    return config_dir() / STATE_FILENAME


def load_state() -> dict[str, Any]:
    """The window's size and whether it was maximised, as it was last time."""
    try:
        state = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    width, height = state.get("width"), state.get("height")
    if not (isinstance(width, int) and isinstance(height, int)):
        width, height = DEFAULT_SIZE
    return {
        "width": max(MIN_SIZE[0], width),
        "height": max(MIN_SIZE[1], height),
        "maximized": state.get("maximized") is True,
    }


def save_state(state: dict[str, Any]) -> None:
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("window size not kept: %s", exc)


def load_backend() -> Any:
    """pywebview with a backend that can show the app, or None (the reason is logged).

    On Windows pythonnet is started on the .NET Framework first, as LibreHardwareMonitor
    starts it: both must run on the same runtime in a process."""
    try:
        import webview
        from webview.guilib import initialize
    except Exception as exc:  # not installed, or broken
        log.info("no window: pywebview is not available (%s)", exc)
        return None
    if sys.platform == "win32":
        try:
            import pythonnet

            pythonnet.load("netfx")  # returns at once when a runtime runs already
        except Exception as exc:
            log.info("no window: pythonnet could not start the .NET Framework (%s)", exc)
            return None
    try:
        backend = initialize()
    except Exception as exc:  # no GTK with WebKit, no display, ...
        log.info("no window: no backend for pywebview (%s)", exc)
        return None
    if getattr(backend, "renderer", None) == "mshtml":  # the old Internet Explorer engine
        log.info("no window: the WebView2 runtime is missing")
        return None
    return webview


def check(open_one: bool = False, seconds: float = 60.0) -> tuple[bool, list[str]]:
    """Whether the window can open here: for the release build's test. Without ``open_one``
    nothing is opened, and a missing WebView2 runtime on the machine is said, but not a
    failure of the build. With it a real window opens, minimized, as the app's own does (the
    same options to webview.start), until its page has loaded or ``seconds`` have passed:
    only that proves a window shows (0.5.0's died at once, at an icon .NET could not use)."""
    lines = []
    try:
        import webview
        from webview.guilib import initialize
    except Exception as exc:
        return False, [f"pywebview: not available ({exc})"]
    lines.append(
        f"pywebview: {getattr(webview, '__version__', '?')} in {Path(webview.__file__).parent}"
    )
    if sys.platform == "win32":
        folder = Path(webview.__file__).parent / "lib"
        for name in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"):
            if not (folder / name).is_file():
                return False, [*lines, f"missing: {folder / name}"]
        try:
            import pythonnet

            pythonnet.load("netfx")
        except Exception as exc:
            return False, [*lines, f"pythonnet: the .NET Framework did not start ({exc})"]
    try:
        backend = initialize()
    except Exception as exc:
        return False, [*lines, f"backend: none ({exc})"]
    renderer = getattr(backend, "renderer", "?")
    lines.append(f"backend: {backend.__name__.rsplit('.', 1)[-1]}, renderer {renderer}")
    if renderer == "mshtml":
        lines.append(
            "the WebView2 runtime is not installed here: the app would open in the browser"
        )
        return not open_one, lines
    if open_one:
        loaded = _open_for_a_moment(webview, seconds)
        lines.append("window: opened and loaded" if loaded else "window: did not load")
        return loaded, lines
    return True, lines


def _open_for_a_moment(webview: Any, seconds: float) -> bool:
    """Open a minimized window with a page of its own, as _show does, and close it once the
    page has loaded; False when it has not after ``seconds``."""
    import tempfile

    done = {"loaded": False}
    window = webview.create_window(
        TITLE,
        html="<!doctype html><title>Libre Panel</title><p>check</p>",
        minimized=True,
        background_color=BACKGROUND,
    )

    def loaded() -> None:
        done["loaded"] = True
        window.destroy()

    def give_up() -> None:
        if not done["loaded"]:
            window.destroy()

    window.events.loaded += loaded
    timer = threading.Timer(seconds, give_up)
    timer.daemon = True
    timer.start()
    # not the app's own window data; WebView2's processes may hold its files a moment longer
    _lighter_engine()  # (as the window's: the release build's test opens it so)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as storage:
        webview.start(**{**_start_options(), "storage_path": storage})
    timer.cancel()
    return done["loaded"]


class Bridge:
    """What the page may call (``window.pywebview.api``): it says when there are unsaved
    changes (then closing asks first) and what its title is, and it may close its window
    (after Quit)."""

    def __init__(self) -> None:
        self._window: Any = None  # private: pywebview exposes public attributes to the page

    def set_dirty(self, dirty: bool) -> None:
        if self._window is not None:
            self._window.confirm_close = bool(dirty)

    def set_title(self, title: str) -> None:
        """The page's title in the title bar and the taskbar: the theme, unsaved marked."""
        if self._window is not None and isinstance(title, str):
            self._window.set_title(" ".join(title.split())[:200] or TITLE)

    def close(self) -> None:
        if self._window is not None:
            threading.Thread(target=self._window.destroy, daemon=True).start()


class _Requests(threading.Thread):
    """Answers the other starts: ``focus <token> <url>`` and ``close <token>``. A request is
    answered as soon as it is understood; what it asks of the window is done after, one at a
    time, in a thread of its own (:meth:`act`)."""

    def __init__(self, listener: socket.socket, token: str, window: Any, shown: dict) -> None:
        super().__init__(name="window-requests", daemon=True)
        self.listener = listener
        self.token = token
        self.window = window
        self.shown = shown  # {"url": ..., "minimized": bool}
        self.todo: queue.Queue[list[str]] = queue.Queue()
        threading.Thread(target=self._work, name="window-acts", daemon=True).start()

    def _work(self) -> None:
        while True:
            words = self.todo.get()
            try:
                self.act(words)
            except Exception:  # a window call that failed: this request, not the next
                log.debug("a request to the window failed", exc_info=True)
            finally:
                self.todo.task_done()

    def settle(self) -> None:
        """Wait until every request so far is done (for the tests)."""
        self.todo.join()

    def run(self) -> None:
        while True:
            try:
                conn, _addr = self.listener.accept()
            except OSError:
                return  # closed: the window is gone
            with conn:
                conn.settimeout(ANSWER_S)
                try:
                    words = conn.makefile("rb").readline(4096).decode("utf-8").split()
                    answer = self.handle(words)
                    conn.sendall(b"ok\n" if answer else b"no\n")
                except (OSError, UnicodeDecodeError):
                    continue
                except Exception:  # a window call that failed: this request, not the next
                    log.debug("a request to the window failed", exc_info=True)

    def handle(self, words: list[str]) -> bool:
        # as bytes: compare_digest refuses text that is not ASCII (anyone may send some)
        given = words[1].encode("utf-8") if len(words) >= 2 else b""
        if not given or not secrets.compare_digest(given, self.token.encode("utf-8")):
            return False
        if words[0] == "close" or (words[0] == "focus" and len(words) == 3 and local_url(words[2])):
            self.todo.put(words)
            return True
        return False

    def act(self, words: list[str]) -> None:
        """What a request asks of the window. Only calls that pywebview hands to the window's
        own thread: a property set from here (``on_top``) is set on this thread on Windows, and
        holds the Python lock while the window, activated by it, waits for that lock in its
        handler: the window froze ("not responding") when Libre Panel was started again."""
        if words[0] == "close":
            self.window.destroy()
            return
        url = words[2]
        page, token = access.split_token(url)  # the page is shown without the token in its name
        current = self.shown["url"]
        # a token of its own: another run of the app (the one shown may have hung and ended),
        # whose page is loaded anew; a page left over from that run stayed black
        known = self.shown.get("token")
        again = bool(token and known) and token != known
        if view_url(page, None) == view_url(current, None) and "#" in url and not again:
            # the same server: the page goes to the route
            route = url.split("#", 1)[1]
            self.window.evaluate_js(f"location.hash = {json.dumps('#' + route)}")
        elif page != current or again:
            self.window.load_url(url)
        self.shown["url"] = page
        self.shown["token"] = token or self.shown.get("token")
        if self.shown.get("minimized"):
            self.window.restore()
        self.window.show()  # shown and activated: to the front (the start allowed it)


def _icon(platform: str | None = None) -> str | None:
    """The window's icon file for GTK. On Windows none: pywebview's WinForms makes a
    System.Drawing.Icon of the file it is given, which a PNG is not (the window died at
    once), and without one it takes the icon of the program itself."""
    if (platform or sys.platform) == "win32":
        return None
    path = Path(__file__).resolve().parent / "editor" / "static" / "icon.png"
    return str(path) if path.is_file() else None


def _start_options() -> dict[str, Any]:
    """What webview.start gets, the same for the window and for its check (check(open_one))."""
    return {
        "private_mode": False,  # the app's own settings (the open panel, recent commands) stay
        "storage_path": str(config_dir() / "window-data"),
        "icon": _icon(),
        "localization": {
            "global.quitConfirmation": t("Close the window? Changes that are not saved are lost."),
            "global.ok": t("Close"),
            "global.cancel": t("Cancel"),
        },
    }


def browser_arguments(environ: Any = None, platform: str | None = None) -> str | None:
    """What WebView2's engine is started with beside pywebview's (:data:`BROWSER_ARGUMENTS`),
    for its environment variable; None off Windows, and where the variable is set already
    (the person's own, for a test of theirs: kept as it is)."""
    environ = os.environ if environ is None else environ
    if (platform or sys.platform) != "win32" or environ.get(WEBVIEW2_ARGUMENTS):
        return None
    return " ".join(BROWSER_ARGUMENTS)


def _lighter_engine(environ: Any = None) -> None:
    """Have the window's WebView2 start with :func:`browser_arguments` (before webview.start)."""
    environ = os.environ if environ is None else environ
    arguments = browser_arguments(environ)
    if arguments:
        environ[WEBVIEW2_ARGUMENTS] = arguments


def _register(listener: socket.socket, token: str, url: str) -> None:
    """Write the file that lets other starts find this window (only this user may read it).
    The window lock is this process's, so one there is left behind by a window that is gone."""
    path = _running_file()
    info = {"pid": os.getpid(), "port": listener.getsockname()[1], "token": token, "url": url}
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(info, file)
        os.replace(tmp, path)
    except OSError as exc:  # this window works; it is just not found by others
        log.warning("the window could not write %s: %s", path, exc)


def _unregister() -> None:
    try:
        info = json.loads(_running_file().read_text(encoding="utf-8"))
        if info.get("pid") == os.getpid():
            _running_file().unlink()
    except (OSError, ValueError, AttributeError):
        pass


def run_window(
    url: str, view: str | None = None, webview: Any = None, token: str | None = None
) -> int:
    """Show the app in a window on this (main) thread until it is closed. An open window
    is brought to the front instead; without a backend the browser opens. ``token`` is the
    access token of the app's server: else the one in ``url``, the one the process that started
    this one passed on in its environment, or the one the running app wrote down."""
    base, inside = access.split_token(url)
    target = view_url(base, view) if view else base
    if not local_url(target):
        log.error("refusing to open %s in Libre Panel's window", target)
        return 2
    inherited = access.inherited_token()  # (taken out of the environment in any case)
    token = token or inside or inherited or access.read_token(base)
    lock = _window_lock()
    try:
        lock.acquire()
    except AlreadyRunning:  # another window is open, or opening: that one, never a second
        if not _wait_for_window(base, view, token):
            log.warning("the open window does not answer: the app opens in the browser")
            webbrowser.open(access.with_token(target, token))
        return 0
    try:
        if focus_window(base, view, token):  # (a window of a Libre Panel before the lock)
            return 0
        webview = webview or load_backend()
        if webview is None:
            webbrowser.open(access.with_token(target, token))
            return 0
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        secret = secrets.token_hex(16)  # what the other starts of the window must say
        try:
            _register(listener, secret, target)  # names the page, not the access token
            return _show(webview, access.with_token(target, token), listener, secret)
        finally:
            listener.close()
            _unregister()
    finally:
        lock.release()


def _show(webview: Any, url: str, listener: socket.socket, secret: str) -> int:
    state = load_state()
    try:
        screen = webview.screens[0]
        state["width"] = min(state["width"], screen.width)
        state["height"] = min(state["height"], screen.height)
    except Exception:  # no screens known: the saved size it is
        pass
    bridge = Bridge()
    window = webview.create_window(
        TITLE,
        url,
        js_api=bridge,
        width=state["width"],
        height=state["height"],
        min_size=MIN_SIZE,
        maximized=state["maximized"],
        background_color=BACKGROUND,
        text_select=True,
    )
    bridge._window = window
    page, token = access.split_token(url)
    shown = {"url": page, "token": token, "minimized": False}

    def resized(width: int, height: int) -> None:
        if not state["maximized"] and not shown["minimized"]:
            state["width"], state["height"] = max(MIN_SIZE[0], width), max(MIN_SIZE[1], height)

    def maximized() -> None:
        state["maximized"] = True
        shown["minimized"] = False

    def minimized() -> None:
        shown["minimized"] = True

    def restored() -> None:
        state["maximized"] = False
        shown["minimized"] = False

    window.events.resized += resized
    window.events.maximized += maximized
    window.events.minimized += minimized
    window.events.restored += restored
    window.events.closed += lambda: save_state(state)
    _Requests(listener, secret, window, shown).start()
    webview.settings["ALLOW_DOWNLOADS"] = True  # "Export" saves a theme file
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    _lighter_engine()
    webview.start(**_start_options())
    save_state(state)
    return 0

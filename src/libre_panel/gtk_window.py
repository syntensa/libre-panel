"""Libre Panel's window without pywebview: the few calls of pywebview's that window.py makes,
made with GTK 3 and WebKitGTK through PyGObject.

The AppImage and the Linux archive bring a Python of their own, which has no GTK: their
window runs in the system's Python instead (``python3 -m libre_panel.window``, see
window.window_command), which has PyGObject and WebKitGTK from the distribution
(``python-gobject`` and ``webkit2gtk-4.1`` on Arch, ``python3-gi`` and
``gir1.2-webkit2-4.1`` on Debian and Ubuntu); pywebview it seldom has, so this module stands
in for it. It needs nothing but the standard library and PyGObject.

What it does as pywebview does there: one window with the app's page, its size, a title the
page sets, the question before closing with unsaved changes, the page's ``window.pywebview.api``
(window.Bridge) and its ``pywebviewready`` event, files the page saves (*Export*), links to
other sites in the browser, and calls from other threads (window._Requests) done on GTK's.
"""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

__version__ = "gtk"  # (what window.check names)
settings: dict[str, Any] = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}
_windows: list[Window] = []
_gi: tuple[Any, ...] | None = None  # Gtk, Gdk, GLib, Gio, WebKit2

# The page's side of the window: window.pywebview.api.<name>(...) posts the call, the page
# hears "pywebviewready" once it may call (window.Bridge's methods, named in __NAMES__).
_BRIDGE_JS = """
(() => {
  const post = (name, args) => {
    window.webkit.messageHandlers.libre.postMessage(JSON.stringify({ name, args }));
    return Promise.resolve(null);
  };
  const api = {};
  for (const name of __NAMES__) api[name] = (...args) => post(name, args);
  window.pywebview = { api };
  const ready = () => window.dispatchEvent(new CustomEvent("pywebviewready"));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else setTimeout(ready, 0);
})();
"""


def load() -> tuple[Any, ...]:
    """GTK 3 and WebKitGTK (4.1, else 4.0): ``(Gtk, Gdk, GLib, Gio, WebKit2)``; ImportError
    where they are not there, ValueError where only other versions are."""
    global _gi
    if _gi is None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        for version in ("4.1", "4.0"):
            try:
                gi.require_version("WebKit2", version)
                break
            except ValueError:
                continue
        else:
            raise ValueError("WebKitGTK 4.1 or 4.0 is not installed")
        from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2

        _gi = (Gtk, Gdk, GLib, Gio, WebKit2)
    return _gi


def ready() -> str:
    """ "" where a window can open here; else why not (no PyGObject, no WebKitGTK, no
    display)."""
    try:
        Gtk = load()[0]
    except (ImportError, ValueError) as exc:
        return str(exc) or type(exc).__name__
    ok, _rest = Gtk.init_check(None)
    return "" if ok else "no display for GTK"


def _local(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")


class _Screen:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height


def __getattr__(name: str) -> Any:  # webview.screens, as pywebview has them
    if name != "screens":
        raise AttributeError(name)
    Gtk, Gdk = load()[:2]
    Gtk.init_check(None)
    display = Gdk.Display.get_default()
    found = []
    for n in range(display.get_n_monitors() if display else 0):
        area = display.get_monitor(n).get_geometry()
        found.append(_Screen(area.width, area.height))
    return found


class Event:
    """``window.events.<name> += handler``, as pywebview's; handlers that fail are logged."""

    def __init__(self) -> None:
        self._handlers: list[Callable[..., Any]] = []

    def __iadd__(self, handler: Callable[..., Any]) -> Event:
        self._handlers.append(handler)
        return self

    def __isub__(self, handler: Callable[..., Any]) -> Event:
        self._handlers.remove(handler)
        return self

    def fire(self, *args: Any) -> None:
        for handler in list(self._handlers):
            try:
                handler(*args)
            except Exception:  # (one handler's error is not the window's)
                log.exception("a window event's handler failed")


class _Events:
    def __init__(self) -> None:
        for name in ("loaded", "shown", "closing", "closed", "resized", "maximized",
                     "minimized", "restored"):  # fmt: skip
            setattr(self, name, Event())


class Window:
    """One window: made by :func:`create_window`, shown by :func:`start`. Its methods may be
    called from any thread: what they do is done on GTK's."""

    def __init__(self, title: str, url: str | None = None, html: str | None = None,
                 js_api: Any = None, width: int = 800, height: int = 600,
                 min_size: tuple[int, int] = (200, 100), maximized: bool = False,
                 minimized: bool = False, background_color: str = "#FFFFFF",
                 **_ignored: Any) -> None:  # fmt: skip
        self.title, self.url, self.html, self.js_api = title, url, html, js_api
        self.size, self.min_size = (width, height), min_size
        self.maximized, self.minimized = maximized, minimized
        self.background_color = background_color
        self.events = _Events()
        self.confirm_close = False  # (window.Bridge sets it while there are unsaved changes)
        self._gtk: Any = None
        self._view: Any = None
        self._text: dict[str, str] = {}
        self._state = 0

    # -- from any thread ---------------------------------------------------------------------

    def _later(self, do: Callable[[], Any]) -> None:
        GLib = load()[2]

        def once() -> bool:
            if self._gtk is not None:
                try:
                    do()
                except Exception:
                    log.exception("a call to the window failed")
            return False

        GLib.idle_add(once)

    def set_title(self, title: str) -> None:
        self._later(lambda: self._gtk.set_title(title))

    def load_url(self, url: str) -> None:
        self._later(lambda: self._view.load_uri(url))

    def evaluate_js(self, script: str) -> None:
        def run() -> None:
            if hasattr(self._view, "evaluate_javascript"):  # (WebKitGTK 2.40 and later)
                self._view.evaluate_javascript(script, -1, None, None, None, None, None)
            else:
                self._view.run_javascript(script, None, None, None)

        self._later(run)

    def show(self) -> None:
        self._later(lambda: self._gtk.present())

    def restore(self) -> None:
        self._later(lambda: self._gtk.deiconify())

    def minimize(self) -> None:
        self._later(lambda: self._gtk.iconify())

    def destroy(self) -> None:
        """Closed at once, without the question (as pywebview's)."""
        self._later(lambda: self._gtk.destroy())

    # -- on GTK's thread ---------------------------------------------------------------------

    def _build(self, storage: str | None, icon: str | None, text: dict[str, str],
               private: bool) -> None:  # fmt: skip
        Gtk, Gdk, _GLib, _Gio, WebKit2 = load()
        self._text = text
        window = Gtk.Window(title=self.title)
        window.set_default_size(*self.size)
        window.set_size_request(*self.min_size)
        if icon and Path(icon).is_file():
            try:
                window.set_icon_from_file(icon)
            except Exception:
                log.debug("no window icon from %s", icon, exc_info=True)
        if storage and not private and hasattr(WebKit2, "WebsiteDataManager"):
            data = WebKit2.WebsiteDataManager(
                base_data_directory=str(Path(storage) / "data"),
                base_cache_directory=str(Path(storage) / "cache"),
            )
            context = WebKit2.WebContext.new_with_website_data_manager(data)
        else:
            context = WebKit2.WebContext.get_default()
        context.connect("download-started", self._download)
        content = WebKit2.UserContentManager()
        content.register_script_message_handler("libre")
        content.connect("script-message-received::libre", self._message)
        names = sorted(
            name for name in dir(self.js_api or object()) if not name.startswith("_")
            and callable(getattr(self.js_api, name, None))
        )  # fmt: skip
        content.add_script(WebKit2.UserScript(
            _BRIDGE_JS.replace("__NAMES__", json.dumps(names)),
            WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START, None, None,
        ))  # fmt: skip
        view = WebKit2.WebView(web_context=context, user_content_manager=content)
        colour = Gdk.RGBA()
        if colour.parse(self.background_color):
            view.set_background_color(colour)
        view.get_settings().set_property("enable-developer-extras", False)
        view.connect("load-changed", self._load_changed)
        view.connect("decide-policy", self._decide)
        view.connect("create", self._new_window)
        window.add(view)
        window.connect("delete-event", self._closing)
        window.connect("destroy", self._destroyed)
        window.connect("configure-event", self._configured)
        window.connect("window-state-event", self._state_changed)
        self._gtk, self._view = window, view
        if self.html is not None:
            view.load_html(self.html, "about:blank")
        elif self.url:
            view.load_uri(self.url)
        if self.maximized:
            window.maximize()
        window.show_all()
        if self.minimized:
            window.iconify()
        self.events.shown.fire()

    def _message(self, _content: Any, result: Any) -> None:
        """A call of the page's ``window.pywebview.api``: done here, on GTK's thread."""
        try:
            value = result.get_js_value() if hasattr(result, "get_js_value") else result
            call = json.loads(value.to_string())
            name, args = str(call.get("name", "")), call.get("args") or []
        except (ValueError, AttributeError, TypeError):
            return
        method = getattr(self.js_api, name, None) if not name.startswith("_") else None
        if callable(method):
            try:
                method(*args)
            except Exception:
                log.exception("the page's call %s failed", name)

    def _load_changed(self, _view: Any, event: Any) -> None:
        if event == load()[4].LoadEvent.FINISHED:
            self.events.loaded.fire()

    def _decide(self, _view: Any, decision: Any, kind: Any) -> bool:
        """Links to another site go to the browser; a file the page saves is downloaded."""
        WebKit2 = load()[4]
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            response = decision.get_response()
            disposition = ""
            headers = response.get_http_headers() if response is not None else None
            if headers is not None:
                disposition = headers.get_one("Content-Disposition") or ""
            if settings.get("ALLOW_DOWNLOADS") and (
                "attachment" in disposition.lower() or not decision.is_mime_type_supported()
            ):
                decision.download()
                return True
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if uri.startswith(("about:", "data:", "blob:")) or _local(uri):
            return False
        if settings.get("OPEN_EXTERNAL_LINKS_IN_BROWSER", True):
            threading.Thread(target=webbrowser.open, args=(uri,), daemon=True).start()
        decision.ignore()
        return True

    def _new_window(self, _view: Any, action: Any) -> None:
        """A link that would open a window of its own: another site in the browser, the app's
        own page in this window."""
        uri = action.get_request().get_uri()
        if _local(uri):
            self._view.load_uri(uri)
        elif uri and settings.get("OPEN_EXTERNAL_LINKS_IN_BROWSER", True):
            threading.Thread(target=webbrowser.open, args=(uri,), daemon=True).start()
        return None

    def _download(self, _context: Any, download: Any) -> None:
        download.connect("decide-destination", self._destination)

    def _destination(self, download: Any, suggested: str) -> bool:
        """Where a file the page saves goes: asked, as a browser asks."""
        Gtk = load()[0]
        save = self._text.get("global.saveFile", "Save")
        dialog = Gtk.FileChooserDialog(title=save, parent=self._gtk,
                                       action=Gtk.FileChooserAction.SAVE)  # fmt: skip
        dialog.add_buttons(self._text.get("global.cancel", "Cancel"), Gtk.ResponseType.CANCEL,
                           save, Gtk.ResponseType.ACCEPT)  # fmt: skip
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(suggested or "download")
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            dialog.set_current_folder(str(downloads))
        answer = dialog.run()
        target = dialog.get_filename()
        dialog.destroy()
        if answer != Gtk.ResponseType.ACCEPT or not target:
            download.cancel()
            return True
        download.set_allow_overwrite(True)
        download.set_destination(Path(target).as_uri())
        return True

    def _closing(self, _window: Any, _event: Any) -> bool:
        """Closed by the person: with unsaved changes, asked first (True keeps it open)."""
        self.events.closing.fire()
        if not self.confirm_close:
            return False
        Gtk = load()[0]
        dialog = Gtk.MessageDialog(transient_for=self._gtk, modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE,
                                   text=self._text.get("global.quitConfirmation",
                                                       "Close the window?"))  # fmt: skip
        dialog.add_buttons(self._text.get("global.cancel", "Cancel"), Gtk.ResponseType.CANCEL,
                           self._text.get("global.ok", "Close"), Gtk.ResponseType.OK)  # fmt: skip
        answer = dialog.run()
        dialog.destroy()
        return answer != Gtk.ResponseType.OK

    def _destroyed(self, _window: Any) -> None:
        self._gtk = self._view = None
        self.events.closed.fire()
        if self in _windows:
            _windows.remove(self)
        if not _windows:
            load()[0].main_quit()

    def _configured(self, window: Any, _event: Any) -> bool:
        width, height = window.get_size()
        self.events.resized.fire(width, height)
        return False

    def _state_changed(self, _window: Any, event: Any) -> bool:
        Gdk = load()[1]
        changed, now = event.changed_mask, event.new_window_state
        for state, yes in ((Gdk.WindowState.ICONIFIED, self.events.minimized),
                           (Gdk.WindowState.MAXIMIZED, self.events.maximized)):  # fmt: skip
            if changed & state:
                (yes if now & state else self.events.restored).fire()
        return False


def create_window(title: str, url: str | None = None, **options: Any) -> Window:
    """A window, shown by :func:`start` (as pywebview's)."""
    window = Window(title, url, **options)
    _windows.append(window)
    return window


def start(private_mode: bool = True, storage_path: str | None = None, icon: str | None = None,
          localization: dict[str, str] | None = None, **_ignored: Any) -> None:  # fmt: skip
    """Show the windows made so far and run GTK until the last is closed."""
    Gtk = load()[0]
    ok, _rest = Gtk.init_check(None)
    if not ok:
        raise RuntimeError("no display for GTK")
    if storage_path:
        Path(storage_path).mkdir(parents=True, exist_ok=True)
    for window in list(_windows):
        window._build(storage_path, icon, dict(localization or {}), private_mode)
    if _windows:
        Gtk.main()
    while Gtk.events_pending():  # (what the closed window left: done before Python ends)
        Gtk.main_iteration_do(False)

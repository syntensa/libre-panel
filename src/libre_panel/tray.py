"""Tray icon for the background app.

Windows and macOS get the full menu. On Linux, pystray uses AppIndicator when
PyGObject is installed (full menu) and plain X11 tray icons otherwise, which
only know a click: it opens the editor, which has the same controls (pause,
brightness, start with system, quit). Without any tray the app keeps running
without an icon.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel.branding import logo
from libre_panel.config import config_dir, set_active_theme
from libre_panel.service import BackgroundApp
from libre_panel.theme.model import list_themes

log = logging.getLogger(__name__)

_STATE_TEXT = {
    "starting": "starting",
    "showing": "showing on {target}",
    "waiting": "waiting for the panel",
    "paused": "paused",
    "error": "needs attention",
    "stopped": "stopped",
}


def headline(state: dict[str, Any]) -> str:
    text = _STATE_TEXT.get(state["state"], state["state"])
    return "Libre Panel: " + text.format(target=state.get("target") or "the panel")


def tooltip(state: dict[str, Any]) -> str:
    text = headline(state)
    if state.get("detail"):
        text += "\n" + state["detail"]
    return text if len(text) <= 120 else text[:119] + "…"  # Windows: at most 127 characters


def load_pystray() -> Any:
    """The pystray module, or None (reason logged) when no tray can be shown."""
    try:
        import pystray
    except Exception as exc:  # not installed, or no display (the X11 backend raises)
        log.warning("no tray icon (%s); Libre Panel keeps running without one", exc)
        return None
    return pystray


def open_path(path: Path) -> None:
    """Show a file or folder with the system's default program."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a local path chosen by the program
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Tray:
    POLL_S = 1.0

    def __init__(self, app: BackgroundApp, pystray: Any, log_path: Path | None = None) -> None:
        self.app = app
        self.pystray = pystray
        self.log_path = log_path
        self.icon: Any = None
        self._last: tuple | None = None

    # -- menu ------------------------------------------------------------------

    def build_menu(self) -> Any:
        menu, item = self.pystray.Menu, self.pystray.MenuItem
        app = self.app
        return menu(
            item(lambda _: headline(app.panel.state()), None, enabled=False),
            item("Open theme editor", self.open_editor, default=True),
            item("Theme", menu(self._theme_items)),
            item("Brightness", menu(self._brightness_items)),
            item("Pause panel", self.toggle_pause, checked=lambda _: app.panel.paused),
            menu.SEPARATOR,
            item(
                "Start with system",
                self.toggle_autostart,
                checked=lambda _: bool(app.snapshot()["autostart"]),
            ),
            item("Open settings folder", self.open_settings),
            item("Open log", self.open_log, visible=self.log_path is not None),
            menu.SEPARATOR,
            item("Quit Libre Panel", self.quit),
        )

    def _theme_items(self):
        item = self.pystray.MenuItem
        for theme in list_themes():
            name = theme["id"]
            yield item(
                name,
                self._later(set_active_theme, name, self.app.config_path),
                checked=lambda _, name=name: self.app.active_theme() == name,
                radio=True,
            )

    def _brightness_items(self):
        item = self.pystray.MenuItem
        for step in self.app.BRIGHTNESS_STEPS:
            yield item(
                "Off" if step == 0 else f"{step} %",
                self._later(self.app.set_brightness, step),
                checked=lambda _, step=step: self.app.brightness() == step,
                radio=True,
            )

    # -- actions (in a worker thread, so the menu never freezes) ---------------

    def _later(self, func: Callable[..., Any], *args: Any) -> Callable[[], None]:
        def action() -> None:
            def work() -> None:
                try:
                    func(*args)
                except Exception as exc:
                    log.error("%s", exc)
                    self._notify(str(exc))
                self.refresh()

            threading.Thread(target=work, daemon=True).start()

        return action

    def _notify(self, message: str) -> None:
        if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", False):
            try:
                self.icon.notify(message, "Libre Panel")
            except Exception:
                pass

    def open_editor(self) -> None:
        self._later(self.app.editor.open)()

    def toggle_pause(self) -> None:
        panel = self.app.panel
        self._later(panel.resume if panel.paused else panel.pause)()

    def toggle_autostart(self) -> None:
        self._later(lambda: self.app.set_autostart(not self.app.snapshot()["autostart"]))()

    def open_settings(self) -> None:
        folder = config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self._later(open_path, folder)()

    def open_log(self) -> None:
        if self.log_path is not None:
            self._later(open_path, self.log_path)()

    def quit(self) -> None:
        self.app.quit()

    # -- icon ------------------------------------------------------------------

    def refresh(self) -> None:
        """Update icon, tooltip and menu when something changed."""
        if self.icon is None:
            return
        state = self.app.panel.state()
        snapshot = self.app.snapshot()
        signature = (
            state["state"],
            state["target"],
            state["detail"],
            self.app.active_theme(),
            snapshot["brightness"],
            snapshot["autostart"],
        )
        if signature == self._last:
            return
        changed_state = self._last is None or self._last[0] != signature[0]
        self._last = signature
        try:
            if changed_state:
                self.icon.icon = logo(64, state["state"])
            self.icon.title = tooltip(state)
            self.icon.update_menu()
        except Exception as exc:  # e.g. an X11 tray that went away
            log.debug("tray update failed: %s", exc)

    def _setup(self, icon: Any) -> None:
        icon.visible = True
        log.info("tray icon ready")
        while not self.app.quit_requested.wait(self.POLL_S):
            self.refresh()

    def run(self) -> None:
        """Show the icon; blocks until Quit (must run on the main thread for macOS)."""
        state = self.app.panel.state()
        self.icon = self.pystray.Icon(
            "libre-panel", logo(64, state["state"]), tooltip(state), self.build_menu()
        )
        self.app.on_quit(self.icon.stop)
        self.icon.run(setup=self._setup)


def _quit_on_signals(app: BackgroundApp) -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: app.quit())


def run_app(
    app: BackgroundApp,
    use_icon: bool = True,
    open_editor: bool = False,
    log_path: Path | None = None,
) -> int:
    """Run until Quit (tray, editor), Ctrl+C or SIGTERM."""
    url = app.start()
    if open_editor:
        webbrowser.open(url)
    pystray = load_pystray() if use_icon else None
    _quit_on_signals(app)
    try:
        if pystray is not None:
            try:
                Tray(app, pystray, log_path).run()
            except Exception:  # the icon is a convenience; the panel must keep running
                log.exception("tray icon failed; Libre Panel keeps running without one")
        if not app.quit_requested.is_set():
            print(f"Libre Panel is running. Theme editor: {url}  (Ctrl+C to stop)")
            while not app.quit_requested.wait(0.5):  # a timeout keeps Ctrl+C working on Windows
                pass
    except KeyboardInterrupt:
        pass
    finally:
        app.quit()
        app.shutdown()
    return 0

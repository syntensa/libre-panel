"""Tray icon for the background app.

Windows gets the full menu. On Linux, pystray uses AppIndicator when
PyGObject is installed (full menu) and plain X11 tray icons otherwise, which
only know a click: it opens the app's window, which has the same controls
(pause, brightness, start with system, quit). Without any tray the app keeps
running without an icon.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import i18n
from libre_panel.branding import logo
from libre_panel.config import ConfigError, config_dir, load_config, set_active_theme
from libre_panel.devices.models import MODELS, PanelModel, find_model
from libre_panel.i18n import t
from libre_panel.service import BackgroundApp
from libre_panel.theme.model import THEME_FILENAME, list_themes, version_base

log = logging.getLogger(__name__)

TOOLTIP_CHARS = 120  # Windows: at most 127 characters


def headline(state: dict[str, Any]) -> str:
    target = state.get("target") or t("the panel")
    text = {
        "starting": t("starting"),
        "showing": t("showing on {target}", target=target),
        "waiting": t("waiting for the panel"),
        "paused": t("paused"),
        "error": t("needs attention"),
        "stopped": t("stopped"),
    }.get(state["state"], state["state"])
    if state.get("mode") and state["state"] == "showing":
        text += f" ({mode_name(state['mode'])})"  # set by a service, e.g. "game"
    return "Libre Panel: " + text


def mode_name(mode: str) -> str:
    """A mode's name as the app's pages call it: "game" is "In game"."""
    names = {"game": t("In game"), "media": t("Music"), "idle": t("Idle")}
    return names.get(mode, mode)


def tooltip(state: dict[str, Any]) -> str:
    text = headline(state)
    line = state.get("notice") or state.get("hint")  # a missing theme, else missing readings
    if state.get("detail"):
        text += "\n" + state["detail"]
    elif line:  # what to do about it: it gives the headline room
        text += "\n" + line
        if len(text) > TOOLTIP_CHARS:
            text = "Libre Panel\n" + line
    return text if len(text) <= TOOLTIP_CHARS else text[: TOOLTIP_CHARS - 1] + "…"


def load_pystray() -> Any:
    """The pystray module, or None (reason logged) when no tray can be shown."""
    try:
        import pystray
    except Exception as exc:  # not installed, or no display (the X11 backend raises)
        log.warning("no tray icon (%s); Libre Panel keeps running without one", exc)
        return None
    return pystray


def _theme_facts(path: str) -> dict[str, Any]:
    """A theme's name, panel, size and collection, from its theme.json (without loading it)."""
    try:
        data = json.loads((Path(path) / THEME_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    display = data.get("display") if isinstance(data.get("display"), dict) else {}
    size = [display.get("width"), display.get("height")]
    return {
        "name": data["name"] if isinstance(data.get("name"), str) and data["name"].strip() else "",
        "model": display.get("model"),
        "size": sorted(size) if all(isinstance(n, int) for n in size) else None,
        "collection": data["collection"] if isinstance(data.get("collection"), str) else "",
    }


def theme_choices(
    model: PanelModel | None, report: str | None = None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, list[tuple[str, str]]]]:
    """The themes the tray offers for the panel, as (id, name), by name, in three parts, as Start
    shows them: those made for ``model`` (all where it is not known); the others ("More
    themes": other panels, and ``report``, the round report's); and each collection's (the
    screens Libre Panel grew out of), apart. A theme's version for a panel on end
    ("lumen-vertical") is not listed: it is chosen as its theme."""
    themes = list_themes()
    ids = {theme["id"] for theme in themes}
    first: list[tuple[str, str]] = []
    more: list[tuple[str, str]] = []
    collections: dict[str, list[tuple[str, str]]] = {}
    for theme in themes:
        if version_base(theme["id"]) in ids:  # ("lumen-vertical", "lumen-1920x720")
            continue
        facts = _theme_facts(theme["path"])
        entry = (theme["id"], facts["name"] or theme["id"])
        width, height = model.size("landscape") if model is not None else (0, 0)
        fits = (
            model is None
            or facts["model"] == model.id
            or facts["size"] == sorted((width, height))
            or f"{theme['id']}-{width}x{height}" in ids  # its version for the panel's size
        )
        if facts["collection"]:
            collections.setdefault(facts["collection"], []).append(entry)
        elif fits and theme["id"] != report:
            first.append(entry)
        else:
            more.append(entry)

    def by_name(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return sorted(entries, key=lambda entry: entry[1].casefold())

    sets = {name: by_name(entries) for name, entries in sorted(collections.items())}
    return by_name(first), by_name(more), sets


def open_path(path: Path) -> None:
    """Show a file or folder with the system's default program (Windows, or ``xdg-open``)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a local path chosen by the program
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path)])
    else:
        raise OSError(t("Opening a file or folder is not supported on this system: no xdg-open."))


class Tray:
    POLL_S = 1.0
    STOP_S = 2.0  # how long Quit waits for an X11 tray's loop to end

    def __init__(self, app: BackgroundApp, pystray: Any, log_path: Path | None = None) -> None:
        self.app = app
        self.pystray = pystray
        self.log_path = log_path
        self.icon: Any = None
        self._last: tuple | None = None
        # Icon updates and stopping never overlap: on X11 an update sent after
        # the stop waits forever for the icon's (finished) event loop.
        self._lock = threading.Lock()
        self._stopped = False

    # -- menu ------------------------------------------------------------------

    def build_menu(self) -> Any:
        """What most people come for first (the app, the theme, the brightness, a pause), then
        the app's views and the switches set once, the folders, and Quit."""
        menu, item = self.pystray.Menu, self.pystray.MenuItem
        app = self.app
        return menu(
            item(lambda _: headline(app.panel.state()), None, enabled=False),
            item(  # the theme config.toml names is missing: the panel shows its own instead
                lambda _: app.panel.state().get("notice", ""),
                None,
                enabled=False,
                visible=lambda _: bool(app.panel.state().get("notice")),
            ),
            item(  # temperatures missing: what to do about it, as long as it is the case
                lambda _: app.panel.state().get("hint", ""),
                None,
                enabled=False,
                visible=lambda _: bool(app.panel.state().get("hint")),
            ),
            item(t("Open Libre Panel"), self.open_app, default=True),
            item(  # only until the question on Start is answered
                t("Smooth motion: confirm the clip name…"),
                self.open_clip_question,
                visible=lambda _: self.asks_for_clip(),
            ),
            menu.SEPARATOR,
            item(t("Theme"), menu(self._theme_items)),
            item(t("Brightness"), menu(self._brightness_items)),
            item(t("Pause panel"), self.toggle_pause, checked=lambda _: app.panel.paused),
            menu.SEPARATOR,
            item(t("Design"), self.open_editor),  # the app's tab of that name
            item(t("Settings"), self.open_settings_view),
            item(
                t("Upside down"), self.toggle_upside_down, checked=lambda _: bool(app.upside_down())
            ),
            item(
                t("Start with system"),
                self.toggle_autostart,
                checked=lambda _: bool(app.snapshot()["autostart"]),
            ),
            menu.SEPARATOR,
            item(t("Open settings folder"), self.open_settings),
            item(t("Open log"), self.open_log, visible=self.log_path is not None),
            menu.SEPARATOR,
            item(t("Quit Libre Panel"), self.quit),
        )

    def _panel_model(self) -> tuple[PanelModel | None, str | None]:
        """The panel the themes are offered for (config.toml's model, else the one the running
        panel says it is; nothing on the USB bus is looked at) and the round report's theme."""
        try:
            config = load_config(self.app.config_path)
        except ConfigError:
            return None, None
        target = self.app.panel.state().get("target") or ""
        model = find_model(config.device.model) or next(
            (m for m in MODELS if target.startswith(m.label)), None
        )
        report = config.services.options.get("memory", {}).get("report_theme", "round-report")
        return model, report if isinstance(report, str) else None

    def _theme_items(self):
        """The themes by name: those made for the panel, then the rest and each collection in
        a submenu of its own. The one on the panel's normal page is checked."""
        item, menu = self.pystray.MenuItem, self.pystray.Menu
        first, more, collections = theme_choices(*self._panel_model())

        def choice(theme_id: str, name: str) -> Any:
            return item(
                name,
                self._later(set_active_theme, theme_id, self.app.config_path),
                checked=lambda _, theme_id=theme_id: self._chosen() == theme_id,
                radio=True,
            )

        yield from (choice(*entry) for entry in first)
        if more or collections:
            yield menu.SEPARATOR
        if more:
            yield item(t("More themes"), menu(*(choice(*entry) for entry in more)))
        for name, entries in collections.items():
            yield item(name, menu(*(choice(*entry) for entry in entries)))

    def _chosen(self) -> str | None:
        """The theme of the panel's normal page, by the id it is chosen by (Lumen, not its
        version for a panel on end)."""
        active = self.app.active_theme()
        base = version_base(active) if active else None
        if base and any(theme["id"] == base for theme in list_themes()):
            return base
        return active

    def _brightness_items(self):
        item = self.pystray.MenuItem
        for step in self.app.BRIGHTNESS_STEPS:
            yield item(
                t("Off") if step == 0 else f"{step} %",
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

    def open_app(self) -> None:
        """The app's window on its start view (also a click on the icon)."""
        self._later(self.app.editor.open, "home")()

    def open_clip_question(self) -> None:
        """The app's window on Start, at the card that asks for the clip's name."""
        self._later(self.app.editor.open, "home/clip")()

    def open_editor(self) -> None:
        self._later(self.app.editor.open, "design")()

    def open_settings_view(self) -> None:
        self._later(self.app.editor.open, "settings")()

    def toggle_pause(self) -> None:
        panel = self.app.panel
        self._later(panel.resume if panel.paused else panel.pause)()

    def toggle_upside_down(self) -> None:
        self._later(lambda: self.app.set_upside_down(not self.app.upside_down()))()

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

    def asks_for_clip(self) -> bool:
        """Whether the menu points to the question about the panel's standby clip."""
        try:
            return self.app.clip_question()
        except Exception:  # a line of the menu must not take the menu down
            log.debug("could not tell whether the clip name is asked for", exc_info=True)
            return False

    # -- icon ------------------------------------------------------------------

    def stop_icon(self) -> None:
        """Remove the icon once no update is under way."""
        with self._lock:
            self._stopped = True
        if self.icon is not None:
            self.icon.stop()

    def refresh(self) -> None:
        """Update icon, tooltip and menu when something changed."""
        with self._lock:
            if self.icon is not None and not self._stopped:
                self._refresh()

    def _refresh(self) -> None:
        state = self.app.panel.state()
        snapshot = self.app.snapshot()
        signature = (
            i18n.language(),
            state["state"],
            state["target"],
            state["detail"],
            state.get("hint"),
            state.get("notice"),
            state.get("mode"),
            self.app.active_theme(),
            snapshot["brightness"],
            snapshot["upside_down"],
            snapshot["autostart"],
            self.asks_for_clip(),
        )
        if signature == self._last:
            return
        changed_state = self._last is None or self._last[1] != signature[1]
        changed_language = self._last is not None and self._last[0] != signature[0]
        self._last = signature
        try:
            if changed_language:
                self.icon.menu = self.build_menu()
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
        """Show the icon; blocks until Quit."""
        state = self.app.panel.state()
        self.icon = self.pystray.Icon(
            "libre-panel", logo(64, state["state"]), tooltip(state), self.build_menu()
        )
        self.app.on_quit(self.stop_icon)
        if not type(self.icon).__module__.endswith("._xorg"):
            self.icon.run(setup=self._setup)
            return
        # pystray's loop for an X11 tray now and then misses the wake-up its stop() sends and
        # waits for an X event that never comes (one quit in a few): there the loop runs
        # beside, and Quit goes on without it once it has had a moment to end.
        loop = threading.Thread(target=self._run_icon, name="tray", daemon=True)
        loop.start()
        while not self.app.quit_requested.wait(0.5):  # (a timeout keeps Ctrl+C working)
            if not loop.is_alive():  # the icon went (its tray did): the panel goes on
                return
        loop.join(self.STOP_S)

    def _run_icon(self) -> None:
        try:
            self.icon.run(setup=self._setup)
        except Exception:  # the icon is a convenience; the panel must keep running
            log.exception("tray icon failed; Libre Panel keeps running without one")


def _quit_on_signals(app: BackgroundApp) -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: app.quit())


# Windows asks the user about programs that take longer than about 5 s.
SESSION_END_S = 4.5


def _stop_with_windows(app: BackgroundApp, stopped: threading.Event):
    """Stop cleanly when Windows shuts down or the user signs out."""
    if sys.platform != "win32":
        return None
    from libre_panel.winsession import SessionEndWatcher, shut_down_early

    def session_ends() -> None:
        log.info("Windows is ending the session; stopping the panel")
        app.quit()
        stopped.wait(SESSION_END_S)  # answer Windows once the panel is left in order

    def sleeps() -> None:
        if app.panel.sleep():
            log.info("the computer went to sleep; the panel stopped until it wakes")

    def wakes() -> None:
        if not app.quit_requested.is_set() and app.panel.wake():
            log.info("the computer woke; the panel starts again")

    try:
        shut_down_early()
        return SessionEndWatcher(session_ends, sleeps, wakes).start()
    except OSError as exc:
        log.warning("no clean stop at shutdown: %s", exc)
        return None


def run_app(
    app: BackgroundApp,
    use_icon: bool = True,
    open_editor: bool = False,
    log_path: Path | None = None,
) -> int:
    """Run until Quit (tray, editor), Ctrl+C, SIGTERM or the end of the Windows session."""
    url = app.start()
    if open_editor:
        app.editor.open("home")
    pystray = load_pystray() if use_icon else None
    _quit_on_signals(app)
    stopped = threading.Event()
    app.session_watcher = _stop_with_windows(app, stopped)
    try:
        if pystray is not None:
            try:
                Tray(app, pystray, log_path).run()
            except Exception:  # the icon is a convenience; the panel must keep running
                log.exception("tray icon failed; Libre Panel keeps running without one")
        if not app.quit_requested.is_set():
            print(
                t(
                    "Libre Panel is running. Theme editor: {url}  (Ctrl+C to stop)",
                    url=app.editor.address or url,  # with the token: it opens the app
                )
            )
            while not app.quit_requested.wait(0.5):  # a timeout keeps Ctrl+C working on Windows
                pass
    except KeyboardInterrupt:
        pass
    finally:
        app.quit()
        app.shutdown()
        stopped.set()
        if app.session_watcher is not None:
            app.session_watcher.stop()
    return 0

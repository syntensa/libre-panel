"""Libre Panel as a background app: the panel loop, the editor and their controls.

``libre-panel tray`` (with icon) and ``libre-panel start`` (in a terminal)
both run a :class:`BackgroundApp`. The editor talks to it through
:meth:`BackgroundApp.snapshot` and :meth:`BackgroundApp.act`, so pausing,
brightness, autostart and quitting work the same from the tray menu and from
the editor (which matters on Linux desktops whose tray has no menus).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel.app import RunStatus, _Watcher, run, sensor_hint
from libre_panel.autostart import Autostart, launch_command
from libre_panel.config import (
    CONFIG_FILENAME,
    ConfigError,
    config_dir,
    load_config,
    set_brightness,
    set_config_value,
    user_themes_dir,
)
from libre_panel.devices import turzx
from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import MODELS, PanelModel
from libre_panel.editor import access
from libre_panel.i18n import t
from libre_panel.theme.defaults import normal_theme
from libre_panel.theme.model import THEME_FILENAME, ThemeError, find_theme

log = logging.getLogger(__name__)

INSTANCE_FILENAME = "instance.json"


class PanelService:
    """Runs the main loop in a thread that can be paused and resumed.

    A broken config or theme does not end the app: it waits until the file is
    fixed (for example by saving in the editor) and starts again. An
    unexpected crash restarts the loop after a pause.
    """

    RETRY_S = 30.0

    def __init__(
        self,
        config_path: Path | None = None,
        restart_delay: float = 5.0,
        host: Any = None,
        on_config: Callable[[Any], None] | None = None,
    ) -> None:
        self.config_path = config_path
        self.restart_delay = restart_delay
        self.host = host  # services' PluginHost
        self.on_config = on_config
        self.status = RunStatus()
        self.error = ""
        self.platform = sys.platform  # what the hint about the sensors goes by (tests set it)
        self._paused = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._paused = False
            self._stop = threading.Event()
            self.status = RunStatus()
            self._thread = threading.Thread(
                target=self._main, args=(self._stop, self.status), name="panel", daemon=True
            )
            self._thread.start()

    def _halt(self) -> None:
        with self._lock:
            self._stop.set()
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=10)
            if thread.is_alive():
                log.warning("the panel loop did not stop in time")

    def pause(self) -> None:
        """Stop sending frames and release the panel (another program may use it)."""
        self._paused = True
        self._halt()

    def resume(self) -> None:
        self.start()

    def stop(self) -> None:
        self._halt()

    def _config_path(self) -> Path:
        return self.config_path or config_dir() / CONFIG_FILENAME

    def _watch_paths(self) -> list[Path]:
        paths = [self._config_path(), user_themes_dir()]
        try:
            normal = normal_theme(load_config(self.config_path), self.status.default_theme)
            paths.append(find_theme(normal) / THEME_FILENAME)
        except (ConfigError, ThemeError):
            pass
        return paths

    def _main(self, stop: threading.Event, status: RunStatus) -> None:
        while not stop.is_set():
            try:
                config = load_config(self.config_path)
                self.error = ""
                if self.on_config is not None:
                    # a change made while the loop did not run (the panel was paused, the
                    # file was broken) reaches the services now; one in step is left alone
                    self.on_config(config)
                run(config, stop=stop, status=status, host=self.host, on_config=self.on_config)
            except (ConfigError, ThemeError, DeviceError) as exc:
                self.error = str(exc)
                log.error("panel stopped: %s (waiting for the config or theme to change)", exc)
                watcher = _Watcher(*self._watch_paths())
                for _ in range(int(self.RETRY_S)):
                    if stop.wait(1.0) or watcher.changed():
                        break
            except Exception as exc:  # keep a background app alive through bugs
                self.error = f"unexpected error: {exc}"
                log.exception("panel loop crashed; starting again in %.0f s", self.restart_delay)
                stop.wait(self.restart_delay)

    def _notice(self) -> str:
        """What the tray and Start say while the theme config.toml names cannot be shown."""
        missing = self.status.missing_theme
        if not missing:
            return ""
        return t(
            "The theme “{name}” is no longer installed, so the panel shows “{shown}”. "
            "Choose a theme in the tray menu or on Start.",
            name=missing,
            shown=self.status.theme or self.status.default_theme,
        )

    def _hint(self) -> str:
        snapshot = self.host.latest if self.host is not None else None
        readings = snapshot.readings if snapshot is not None else {}
        return sensor_hint(self.status.hub, readings, self.platform)

    def state(self) -> dict[str, Any]:
        status = self.status
        if self._paused:
            state = "paused"
        elif self.error:
            state = "error"
        elif not self.running:
            state = "stopped"
        else:
            state = status.state
        return {
            "state": state,
            "detail": self.error or (status.detail if state == "waiting" else ""),
            # what to do when temperatures and power show "--" (Windows), in the user's language
            "hint": self._hint() if state in ("showing", "waiting") else "",
            # a theme config.toml names that cannot be shown: the panel shows its own instead
            "missing_theme": status.missing_theme,
            "notice": self._notice() if state in ("showing", "waiting") else "",
            "target": status.target,
            "theme": status.theme,
            # what a config.toml without a theme shows on this panel (see theme.defaults)
            "default_theme": status.default_theme,
            "frames": status.frames,
            "mode": status.mode,
            # numbers of the last seconds (see libre_panel.meter); none while it does not run
            "stats": status.stats if state in ("showing", "waiting") else {},
        }


class EditorService:
    """The theme editor web server, started on first use."""

    def __init__(
        self, config_path: Path | None = None, port: int = 8765, controls: Any = None
    ) -> None:
        self.config_path = config_path
        self.port = port
        self.controls = controls
        self._server = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str | None:
        if self._server is None:
            return None
        return f"http://127.0.0.1:{self._server.server_address[1]}/"

    @property
    def token(self) -> str | None:
        """What a request to the server must carry (editor/access.py)."""
        return None if self._server is None else self._server.token

    @property
    def address(self) -> str | None:
        """``url`` with the token: the way into the app for the person who started Libre Panel
        (a terminal may show it; a log never does)."""
        return access.with_token(self.url, self.token) if self._server is not None else None

    def start(self) -> str:
        from libre_panel.editor.server import make_server

        with self._lock:
            if self._server is None:
                try:
                    server = make_server(self.port, self.config_path, controls=self.controls)
                except OSError:  # port taken: any free port will do
                    server = make_server(0, self.config_path, controls=self.controls)
                self._server = server
                # for the programs of this user that start later: the window, `libre-panel quit`
                access.publish(server.token, server.server_address[1])
                self._thread = threading.Thread(
                    target=server.serve_forever, name="editor", daemon=True
                )
                self._thread.start()
                log.info("theme editor at %s", self.url)
            return self.url

    def open(self, view: str = "home") -> str:
        """Show the app on ``view``: in its window (opened or brought to the front), or in
        the browser where there can be no window."""
        from libre_panel.window import open_window

        url = self.start()
        open_window(url, view, config_path=self.config_path, token=self.token)
        return url

    def stop(self) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            from libre_panel.window import close_window

            close_window(f"http://127.0.0.1:{server.server_address[1]}/")  # it would show nothing
            access.forget(server.token)
            server.shutdown()
            server.server_close()
            server.editor_state.close()


def instance_file() -> Path:
    return config_dir() / INSTANCE_FILENAME


def running_instance() -> dict[str, Any] | None:
    """What the running app wrote about itself (its editor address), if anything."""
    try:
        return json.loads(instance_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class BackgroundApp:
    """The panel loop plus the editor, with controls for the tray and the editor."""

    BRIGHTNESS_STEPS = (0, 10, 25, 50, 75, 100)

    def __init__(
        self,
        config_path: Path | None = None,
        port: int = 8765,
        autostart: Autostart | None = None,
        autostart_command: list[str] | None = None,
    ) -> None:
        from libre_panel.plugins import PluginHost, ServiceManager

        self.config_path = config_path
        self.plugin_host = PluginHost()
        self.services = ServiceManager(self.plugin_host)
        self.panel = PanelService(config_path, host=self.plugin_host, on_config=self._config_loaded)
        self.editor = EditorService(config_path, port, controls=self)
        self.autostart = autostart or Autostart()
        self.autostart_command = autostart_command or launch_command(config_path)
        self.quit_requested = threading.Event()
        self._on_quit: list[Callable[[], None]] = []
        self.session_watcher: Any = None  # Windows: stops cleanly at shutdown (tray.run_app)

    # -- lifecycle -----------------------------------------------------------

    def _check_folders(self) -> None:
        """With the highest rights Libre Panel runs what config.toml and the plugins name: the
        log says so when other users can change them (editor/access.py)."""
        if not access.elevated():
            return
        folders = [config_dir()]
        if self.config_path is not None:
            folders.append(Path(self.config_path).resolve().parent)
        for folder in dict.fromkeys(folders):
            why = access.folder_risk(folder)
            if why:
                log.warning(
                    "Libre Panel runs with administrator rights and runs what the settings and "
                    "plugins in %s name, but %s: any user who can change them can run a program "
                    "with those rights",
                    folder,
                    why,
                )

    def start(self) -> str:
        self._check_folders()
        try:
            self.services.apply(load_config(self.config_path))
        except ConfigError as exc:
            log.error("services not started: %s", exc)  # they start once the config is fixed
        self.panel.start()
        url = self.editor.start()
        self._write_instance(url)
        return url

    def on_quit(self, callback: Callable[[], None]) -> None:
        self._on_quit.append(callback)

    def quit(self) -> None:
        if self.quit_requested.is_set():
            return
        self.quit_requested.set()
        for callback in self._on_quit:
            try:
                callback()
            except Exception:
                log.exception("while quitting")

    def _config_loaded(self, config: Any) -> None:
        """config.toml changed: bring the services in line (in the background, so
        starting or stopping one never stalls the panel)."""
        threading.Thread(
            target=self.services.apply, args=(config,), name="services", daemon=True
        ).start()

    def follow_config(self) -> None:
        """Bring the services in line with config.toml now. The panel loop does it when it
        sees a change; this is for a page that has just written one, so the services follow
        also while the panel is paused. The file is left as it is if it cannot be read."""
        try:
            config = load_config(self.config_path)
        except ConfigError:
            return  # the services go on as they are until the file is fixed
        self._config_loaded(config)

    def shutdown(self) -> None:
        self.services.stop()
        self.panel.stop()
        self.editor.stop()
        self.plugin_host.close()
        try:
            data = running_instance()
            if data and data.get("pid") == os.getpid():
                instance_file().unlink()
        except OSError:
            pass

    def _write_instance(self, url: str) -> None:
        path = instance_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid(), "editor": url}), encoding="utf-8")

    # -- controls (tray menu and editor) ------------------------------------

    def brightness(self) -> int | None:
        try:
            return load_config(self.config_path).device.brightness
        except ConfigError:
            return None

    def upside_down(self) -> bool | None:
        try:
            return load_config(self.config_path).device.rotate == 180
        except ConfigError:
            return None

    def set_upside_down(self, on: bool) -> None:
        set_config_value("device.rotate", 180 if on else 0, self.config_path)

    def active_theme(self) -> str | None:
        """The theme of the normal page: config.toml's, else the panel's own default."""
        try:
            config = load_config(self.config_path)
        except ConfigError:
            return None
        return normal_theme(config, self.panel.status.default_theme)

    def _panel_in_use(self) -> PanelModel | None:
        """The panel the loop sends frames to, if the video mode is proven on it: what the
        panel says it shows on starts with the model's label (as the app's Start finds it)."""
        target = self.panel.status.target
        return next((m for m in MODELS if m.video and target.startswith(m.label)), None)

    def clip_question(self) -> bool:
        """Whether the person is still to be asked for the panel's standby clip (Start asks;
        the tray points to it). Nothing here looks at the USB bus: it is asked once a second."""
        try:
            device = load_config(self.config_path).device
        except ConfigError:
            return False
        return turzx.clip_question(device, self._panel_in_use)

    def snapshot(self) -> dict[str, Any]:
        try:
            autostart = self.autostart.is_enabled()
        except OSError:
            autostart = None
        return {
            "panel": self.panel.state(),
            "brightness": self.brightness(),
            "upside_down": self.upside_down(),
            "autostart": autostart,
            "autostart_location": self.autostart.location(),
            "can_quit": True,
        }

    def set_brightness(self, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("brightness must be between 0 and 100")
        set_brightness(percent, self.config_path)  # the loop picks it up from the file

    def set_autostart(self, enabled: bool) -> None:
        if enabled:
            where = self.autostart.enable(self.autostart_command)
            log.info("autostart enabled: %s", where)
        elif self.autostart.disable():
            log.info("autostart disabled")

    def act(self, action: str, value: Any = None) -> dict[str, Any]:
        """One control action from the editor; returns the new snapshot."""
        if action == "pause":
            self.panel.pause()
        elif action == "resume":
            self.panel.resume()
        elif action == "brightness":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("brightness must be a whole number")
            self.set_brightness(value)
        elif action == "upside_down":
            if not isinstance(value, bool):
                raise ValueError("upside_down must be true or false")
            self.set_upside_down(value)
        elif action == "autostart":
            if not isinstance(value, bool):
                raise ValueError("autostart must be true or false")
            self.set_autostart(value)
        elif action == "quit":
            # Answer the request first; the server stops as part of quitting.
            timer = threading.Timer(0.3, self.quit)
            timer.daemon = True
            timer.start()
        else:
            raise ValueError(f"unknown action {action!r}")
        return self.snapshot()

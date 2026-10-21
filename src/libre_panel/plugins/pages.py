"""Editor pages (B6): a plugin's own page in the theme editor.

The page's files (``index.html``, scripts, styles) are served from
``/plugins/<id>/``; its API from ``/api/plugins/<id>/<path>``. Pages link
``/static/editor.css`` and import ``/static/kit.js`` to look and work like
the editor. The editor listens on 127.0.0.1 only, and a request to a page's API (GET as much as
POST) needs the secret token of the run in the ``X-Libre-Panel`` header, as everywhere in the
editor (``editor/access.py``): ``kit.js`` sends it.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from libre_panel.config import CONFIG_FILENAME, config_dir, log_file
from libre_panel.plugins.host import NO_PANEL, Delivery, ServiceHost

log = logging.getLogger(__name__)

MAX_LOG_LINES = 1000


def tail(path: Path, lines: int) -> list[str]:
    """The last ``lines`` lines of a text file (all of them if it is shorter), read from
    its end: a log of a megabyte costs a few kilobytes. Empty if it cannot be read."""
    lines = max(1, min(int(lines), MAX_LOG_LINES))
    try:
        with path.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            window = lines * 200
            while True:
                start = max(0, size - window)
                file.seek(start)
                rows = file.read(size - start).decode("utf-8", errors="replace").splitlines()
                if start == 0 or len(rows) > lines:  # enough, or the whole file
                    break
                window *= 2
    except OSError:
        return []
    if start > 0:
        rows = rows[1:]  # the first line is cut off
    return rows[-lines:]


def _status_of(provider: Any) -> dict[str, Any] | None:
    """What a sensor source says about itself: its ``status()`` as a dict, or None."""
    status = getattr(provider, "status", None)
    if not callable(status):
        return None
    try:
        found = status()
        if dataclasses.is_dataclass(found) and not isinstance(found, type):
            return dataclasses.asdict(found)
        return dict(found) if isinstance(found, dict) else None
    except Exception:  # a source that cannot say must not break the page
        log.debug(
            "status of sensor source %s failed", getattr(provider, "name", "?"), exc_info=True
        )
        return None


class PageContext:
    """What a page gets: the panel's readings, picture and state, the services (in the
    background app), config.toml and data folders. In a plain ``libre-panel editor`` no
    panel runs: ``background`` is False, ``snapshot``, ``shown_frame``, ``status`` and
    ``service`` give None, ``services`` and ``sources`` are empty."""

    def __init__(self, controls: Any = None, config_path: Path | None = None) -> None:
        self._controls = controls
        self._config_path = config_path

    @property
    def _manager(self) -> Any:
        return getattr(self._controls, "services", None)

    @property
    def _host(self) -> Any:
        return getattr(self._controls, "plugin_host", None)

    @property
    def background(self) -> bool:
        """True when the background app (the tray, ``libre-panel start``) serves this
        editor, so the panel and the services run here; False in ``libre-panel editor``."""
        return self._controls is not None

    @property
    def elevated(self) -> bool:
        """True when Libre Panel runs with administrator rights (root on Linux). A request
        cannot be told from the person's, and what it asks for is done with those rights: a page
        then leaves what names a program, a library or a file to config.toml, as the app's own
        settings do (``editor/settings.py``), and does not start or write what a request names."""
        from libre_panel.editor import access

        return access.elevated()

    @property
    def config_path(self) -> Path:
        """config.toml (change single settings with ``libre_panel.config.set_config_value``)."""
        path = getattr(self._controls, "config_path", None) or self._config_path
        return path or config_dir() / CONFIG_FILENAME

    def status(self) -> dict[str, Any] | None:
        """What the panel is doing and how it runs, as ``/api/app`` shows it: ``state``
        (``showing``, ``waiting``, ``paused``, ``error``, ``stopped``, ``starting``),
        ``detail``, ``target``, ``theme``, ``frames``, ``mode`` and ``stats`` (frame rate,
        drawing time, data rate, the video link and faults; empty while the panel does
        not run). None without the background app."""
        panel = getattr(self._controls, "panel", None)
        return panel.state() if panel is not None else None

    def sources(self) -> list[dict[str, Any]]:
        """The sensor sources the panel reads, the first that has a reading wins: each
        ``{"name", "readings", "status"}`` with the number of readings the last snapshot
        took from it and what the source says about itself, as a dict, if it has a
        ``status()`` (LibreHardwareMonitor does), else None. Empty without the
        background app."""
        panel = getattr(self._controls, "panel", None)
        hub = getattr(getattr(panel, "status", None), "hub", None)
        if hub is None:
            return []
        return [
            {
                "name": provider.name,
                "readings": hub.readings_from(provider),
                "status": _status_of(provider),
            }
            for provider in list(hub.providers)
        ]

    def snapshot(self) -> Any:
        """The readings and history the panel was last drawn from, or None."""
        host = self._host
        return host.latest if host is not None else None

    def shown_frame(self, panel: int = 1) -> Image.Image | None:
        """The frame on the panel now, after transitions and messages, in the theme's
        orientation (not turned for a panel mounted upside down); None before the first
        frame, while the panel is paused and without the background app. Read it only:
        the main loop may hand the same image out again. ``panel``: which one, as *Which
        is which?* numbers them (1: ``[device]``'s, 2 and on: those of ``[[panels]]``)."""
        if panel == 1:
            host = self._host
            return host.shown if host is not None else None
        status = getattr(getattr(self._controls, "panel", None), "status", None)
        more = getattr(status, "more", None) or {}
        beside = more.get(panel - 2)
        return getattr(beside, "shown", None)

    def notify(self, text: str, **options: Any) -> Delivery:
        """A message on the panel, for a test or to answer an action of the page. The
        arguments are those of ``ServiceHost.notify`` (``icon``, ``level``, ``seconds``,
        ``kind``, ``rank``, ``payload``, ``key``); the answer says whether it will show
        or why not (``Delivery``). Without the background app nothing shows it."""
        host = self._host
        if host is None:
            return Delivery(False, NO_PANEL)
        return ServiceHost(host, "editor").notify(text, **options)

    def log_tail(self, lines: int = 40) -> list[str]:
        """The last ``lines`` lines (at most 1000) of the log the tray app writes
        (``logs/libre-panel.log`` in the settings folder). Empty when there is no log,
        e.g. when ``libre-panel start`` logs to its terminal instead."""
        return tail(log_file(), lines)

    def service(self, name: str) -> Any:
        """The running service of that name, or None (e.g. in ``libre-panel editor``)."""
        manager = self._manager
        if manager is None:
            return None
        running = manager.running.get(name)
        return running[0] if running else None

    def services(self) -> dict[str, str]:
        """Each enabled service and its state: ``running``, ``failed: ...``,
        ``not installed`` or ``stopped``."""
        manager = self._manager
        return dict(manager.state) if manager is not None else {}

    def shortcut(self, action: str) -> bool:
        """Do what a keyboard shortcut does (``timer``, ``timer_reset``, ``mic_mute``), from a
        button of the app; False without the background app."""
        if self._controls is None or not hasattr(self._controls, "shortcut"):
            return False
        self._controls.shortcut(action)
        return True

    def restart_service(self, name: str) -> str:
        """Stop and start an enabled service again (with its options); its new state."""
        manager = self._manager
        if manager is None:
            raise LookupError("no services run in this editor")
        return manager.restart(name)

    def follow_config(self) -> None:
        """After a page wrote config.toml: have the running app bring its services in line
        with it now (a service switched on starts, one switched off stops, one whose
        options changed restarts), instead of when the panel loop next sees the file, which
        it does not while the panel is paused. Nothing happens in a plain editor."""
        follow = getattr(self._controls, "follow_config", None)
        if callable(follow):
            follow()

    def data_dir(self, service: str) -> Path:
        """The data folder a service of that name uses (``host.data_dir``)."""
        return config_dir() / "plugins-data" / service


class EditorPage:
    """A page in the editor. Set ``api = 1``, ``title`` ({"en": ..., "de": ...}),
    optionally ``icon`` (a built-in icon name) and ``static``, the folder with
    ``index.html`` next to the plugin's module (default ``"page"``).
    """

    title: dict[str, str] = {}
    icon = ""
    static = "page"

    def __init__(self, context: PageContext) -> None:
        self.context = context

    def handle(self, method: str, path: str, query: dict[str, list[str]], body: Any):
        """Answer ``/api/plugins/<id>/<path>``: return (status, JSON-able data), or
        (status, bytes, content type) to send a file (a picture)."""
        return 404, {"error": "not found"}

    @classmethod
    def folder(cls) -> Path | None:
        module = sys.modules.get(cls.__module__)
        if module is None or not getattr(module, "__file__", None):
            return None
        return Path(module.__file__).resolve().parent / cls.static

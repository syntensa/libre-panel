"""Editor pages (B6): a plugin's own page in the theme editor.

The page's files (``index.html``, scripts, styles) are served from
``/plugins/<id>/``; its API from ``/api/plugins/<id>/<path>``. Pages link
``/static/editor.css`` and import ``/static/kit.js`` to look and work like
the editor. The editor listens on 127.0.0.1 only and writes need its
``X-Libre-Panel`` header, as everywhere in the editor.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from libre_panel.config import CONFIG_FILENAME, config_dir

log = logging.getLogger(__name__)


class PageContext:
    """What a page gets: the panel's readings, the services (in the background
    app), config.toml and data folders. In a plain ``libre-panel editor`` no
    panel runs: ``snapshot`` and ``service`` are None, ``services`` is empty."""

    def __init__(self, controls: Any = None, config_path: Path | None = None) -> None:
        self._controls = controls
        self._config_path = config_path

    @property
    def _manager(self) -> Any:
        return getattr(self._controls, "services", None)

    @property
    def config_path(self) -> Path:
        """config.toml (change single settings with ``libre_panel.config.set_config_value``)."""
        path = getattr(self._controls, "config_path", None) or self._config_path
        return path or config_dir() / CONFIG_FILENAME

    def snapshot(self) -> Any:
        """The readings and history the panel was last drawn from, or None."""
        host = getattr(self._controls, "plugin_host", None)
        return host.latest if host is not None else None

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

    def restart_service(self, name: str) -> str:
        """Stop and start an enabled service again (with its options); its new state."""
        manager = self._manager
        if manager is None:
            raise LookupError("no services run in this editor")
        return manager.restart(name)

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
        """Answer ``/api/plugins/<id>/<path>``: return (status, JSON-able data)."""
        return 404, {"error": "not found"}

    @classmethod
    def folder(cls) -> Path | None:
        module = sys.modules.get(cls.__module__)
        if module is None or not getattr(module, "__file__", None):
            return None
        return Path(module.__file__).resolve().parent / cls.static

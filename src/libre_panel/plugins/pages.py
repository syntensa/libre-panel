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

from libre_panel.config import config_dir

log = logging.getLogger(__name__)


class PageContext:
    """What a page gets: the running services (in the background app) and data folders."""

    def __init__(self, controls: Any = None) -> None:
        self._controls = controls

    def service(self, name: str) -> Any:
        """The running service of that name, or None (e.g. in ``libre-panel editor``)."""
        manager = getattr(self._controls, "services", None)
        if manager is None:
            return None
        running = manager.running.get(name)
        return running[0] if running else None

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

"""What the built-in pages share: routes, and the answers to requests that cannot be met."""

from __future__ import annotations

from typing import Any, ClassVar

from libre_panel.i18n import t
from libre_panel.plugins.pages import EditorPage


class Refused(Exception):
    """A request that cannot be met; the page shows ``str(error)`` (already translated)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Request:
    """The query and the body of a request, with readers that refuse what is wrong."""

    def __init__(self, query: dict[str, list[str]] | None, body: Any) -> None:
        self.query = query or {}
        self.body = body if isinstance(body, dict) else {}

    def param(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default

    def integer(self, name: str, default: int, low: int, high: int) -> int:
        """A whole number from the query, kept between ``low`` and ``high``."""
        try:
            return max(low, min(high, int(self.param(name, str(default)))))
        except ValueError:
            return default

    def text(self, name: str) -> str:
        value = self.body.get(name)
        if not isinstance(value, str) or not value:
            raise Refused(t("{name} is missing", name=name))
        return value


class ConsolePage(EditorPage):
    """An editor page that answers by a table of routes: (method, path) -> the name of
    a method. A method takes the :class:`Request` and gives the data (sent as JSON), or
    ``(status, data)``, or ``(status, bytes, content type)``."""

    api = 1
    static = "web"  # index.html and the scripts of every page are in one folder
    routes: ClassVar[dict[tuple[str, str], str]] = {}

    def handle(self, method: str, path: str, query: dict[str, list[str]], body: Any):
        name = self.routes.get((method, path.strip("/")))
        if name is None:
            return 404, {"error": t("not found")}
        try:
            result = getattr(self, name)(Request(query, body))
        except Refused as exc:
            return exc.status, {"error": str(exc)}
        return result if isinstance(result, tuple) else (200, result)

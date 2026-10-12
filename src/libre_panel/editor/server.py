"""Local web server for the visual theme editor.

Binds to 127.0.0.1 only. That keeps other computers out, not the other users of this one, and not
a website the user visits (it can try a local port). So a request must carry the expected Host
header, and the secret token of this run in the ``X-Libre-Panel`` header (access.py): everything
but the app's own files needs it. Every route is in ``ROUTES``; one that is not there is not
answered, and the token is asked for before the answer "not found".
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import socket
import sys
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from libre_panel import __version__, i18n, sequence
from libre_panel.config import (
    FAN_LOOKS,
    FAN_PAINTS,
    ConfigError,
    edit_config,
    hardware_name,
    load_config,
    set_config_value,
    theme_chosen,
    user_themes_dir,
)
from libre_panel.devices.models import MODELS, PROTOCOLS
from libre_panel.editor import access
from libre_panel.editor import settings as app_settings
from libre_panel.editor.presets import presets_for_editor
from libre_panel.fonts import BUILTIN_PREFIX, DEFAULT_FONT, builtin_font_path, builtin_fonts
from libre_panel.i18n import t
from libre_panel.icons import ICON_NAMES
from libre_panel.render import fan, fan_looks
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import TIME_SECOND, SensorHub, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.sensors.hardware import detected_names
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.defaults import default_theme, panel_model
from libre_panel.theme.model import (
    COMMON_FIELDS,
    DUST_MAX_AREAS,
    DUST_MAX_COLORS,
    EFFECT_FIELDS,
    FINISHES,
    LOOK_RANGES,
    LOOK_SPECS,
    PRIMARY_FIELDS,
    THEME_FILENAME,
    TOAST_ANCHORS,
    WIDGET_SPECS,
    ThemeError,
    find_theme,
    list_themes,
    load_theme,
    parse_theme,
    resolve_asset,
    save_theme,
    valid_theme_name,
)
from libre_panel.theme.modules import (
    MODULE_KINDS,
    detach,
    look_theme_parts,
    make_grid,
    module_info,
    templates,
)

log = logging.getLogger(__name__)

# What the console's pages and plugins' pages share (kit.js, editor.css, stats.js), and the icon.
STATIC_DIR = Path(__file__).resolve().parent / "static"
APP_DIR = Path(__file__).resolve().parent / "app"  # the app: shell, views, vendored libraries
MAX_JSON = 2 * 1024 * 1024
MAX_ASSET = 10 * 1024 * 1024
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ttf", ".otf"}
# Parts of the language catalog the editor needs.
_EDITOR_TABLES = ("messages", "fields", "widgets", "enums", "icons")
# Readings of sources that work on demand: a running panel has none of them until a theme (or a
# service) shows one, so a sensor field offers them from here, whatever the live readings say.
# (key, unit, label), as the sources give them; the demo values of the previews cover them too.
ON_DEMAND = (
    ("audio.volume", "%", "Volume"),
    ("audio.muted", "", "Muted"),
    ("cpu.load", "%", "CPU load"),  # the fast load
    ("gpu.load", "%", "GPU load"),
    ("media.title", "", "Title"),
    ("media.artist", "", "Artist"),
    ("media.album", "", "Album"),
    ("media.source", "", "Player"),
    ("media.state", "", "Media"),
    ("media.playing", "", "Playing"),
    ("media.duration", "s", "Length"),
    ("media.position", "s", "Position"),
    ("media.progress", "", "Progress"),
    ("media.remaining", "s", "Remaining"),
    ("game.fps", "fps", "Frames per second"),
    ("game.frametime", "ms", "Frame time"),
    ("game.low", "fps", "1% low"),
    ("game.frametime_peak", "ms", "Frame time peak"),
    ("game.limiter", "", "Limited by"),
    ("game.app", "", "Game"),
    ("game.name", "", "Game"),
    ("weather.rain.minutes", "min", "Rain starts or stops"),
    ("weather.rain.text", "", "Rain"),
    ("net.ping", "ms", "Ping"),
)
# Pictures a source or service has, for an image widget (``"src": "@media.cover"``). They are
# no readings: they come marked ``picture``, and a sensor field leaves them out.
PICTURES = (
    ("media.cover", "Album cover"),
    ("media.app_icon", "Player icon"),
    ("game.logo", "Game logo"),
)
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
# The names /api/hardware talks about, and the setting in config.toml ([hardware]) of each.
_HARDWARE_SETTINGS = {"cpu": "cpu_name", "gpu": "gpu_name", "memory": "memory_name"}


def _natural(key: str) -> list[Any]:
    """A key in the order people count (fan_2 before fan_10)."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", key)]


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
}
# The app's files (ES modules in folders) and the theme files the editor shows.
_APP_TYPES = {**_CONTENT_TYPES, ".txt": "text/plain; charset=utf-8"}
_APP_PATH = re.compile(r"^(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/){0,4}[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_ASSET_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}
# A page of the panel: "normal", "report" or a mode ([modes.<name>] in config.toml).
_MODE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# The routes of the app's Settings, and the method of editor/settings.py::Settings behind each.
_SETTINGS_GET = {
    "/api/settings": "read",
    "/api/panel": "panel",
    "/api/clip": "clip_question",
    "/api/system": "system",
    "/api/unlock": "unlock",
    "/api/about": "about",
}
_SETTINGS_POST = {
    "/api/settings": "write",
    "/api/system": "set_system",
    "/api/unlock": "set_unlock",
    "/api/location": "search_place",
    "/api/open": "open_folder",
}
# What a plugin's editor page may serve from its folder.
_PAGE_TYPES = {
    **_CONTENT_TYPES,
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


def _theme_list() -> list[dict[str, Any]]:
    """The installed themes (theme.model.list_themes) with their names and panels, for
    lists and galleries: read from each theme.json, without loading the theme."""
    themes = list_themes()
    for item in themes:
        try:
            data = json.loads((Path(item["path"]) / THEME_FILENAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        display = data.get("display") if isinstance(data, dict) else None
        display = display if isinstance(display, dict) else {}
        name = data.get("name") if isinstance(data, dict) else None
        item["name"] = name if isinstance(name, str) and name.strip() else item["id"]
        item["model"] = display.get("model") if isinstance(display.get("model"), str) else None
        collection = data.get("collection") if isinstance(data, dict) else None
        item["collection"] = collection if isinstance(collection, str) else ""
        item["size"] = (
            [display["width"], display["height"]]
            if all(isinstance(display.get(k), int) for k in ("width", "height"))
            else None
        )
    return themes


def _finish_types(types: list[str]) -> dict[str, list[str]]:
    """The widget types each finish draws (any other is drawn flat): the editor offers a
    finish only where it shows. A module counts when its finish reaches one of its parts."""
    from libre_panel.render import finish

    found: dict[str, list[str]] = {}
    for name in FINISHES[1:]:
        kinds = [kind for kind in types if kind != "module" and finish.get(name, kind)]
        if any(finish.get(name, part) for part in finish.PART_TYPES):
            kinds.append("module")
        found[name] = kinds
    return found


NOTICE_AGE_S = 1.2  # how far into its life a preview shows a notice: drawn in full


def _with_notice(renderer: Renderer, frame: Any, kind: str) -> Any:
    """``frame`` with the test message of ``kind`` (the Control page's: volume, music, ...)
    drawn on it in the theme's toast style, a moment after it began; None for an unknown
    kind. The previews of the editor have no messages of their own."""
    from libre_panel.pages.control import probes
    from libre_panel.plugins.host import Toast
    from libre_panel.render.overlays import ToastLayer

    made = probes().get(kind)
    if made is None:
        return None
    _name, options = made
    toast = Toast(
        options["text"],
        options.get("icon"),
        options.get("level", "info"),
        options.get("seconds"),
        options.get("kind", ""),
        options.get("rank", 0),
        dict(options.get("payload", {})),
        "editor",
    )
    layer = ToastLayer(renderer)
    try:
        layer.add([toast])
        layer.advance(0.0)
        return layer.draw(frame, NOTICE_AGE_S)
    finally:
        layer.close()


def _look_ranges() -> dict[str, dict[str, list[float]]]:
    """``LOOK_RANGES`` by block, for sliders that stop where the theme would refuse."""
    found: dict[str, dict[str, list[float]]] = {}
    for (block, key), (low, high) in LOOK_RANGES.items():
        found.setdefault(block, {})[key] = [low, high]
    return found


def _plugin_specs() -> dict[str, Any]:
    """Installed plugin widget types, screens and toast styles (and the built-in
    styles besides the card), for the inspector and the building blocks."""
    from libre_panel.plugins.loader import registry
    from libre_panel.plugins.render import label_for
    from libre_panel.render.overlays import BUILT_IN_STYLES

    installed = registry()
    widgets: dict[str, Any] = {}
    labels: dict[str, str] = {}
    presets: list[dict[str, Any]] = []
    for name in installed.names("widgets"):
        cls = installed.get("widgets", name)
        if cls is None:
            continue
        widgets[name] = {k: list(v) for k, v in cls.spec.items()}
        labels[name] = label_for(cls)
        defaults = {k: v[1] for k, v in cls.spec.items()}
        for i, preset in enumerate(cls.presets):
            widget = {"type": name, "id": "part", "x": 0, "y": 0, **defaults, **preset["widget"]}
            text = preset.get("label", {})
            presets.append(
                {
                    "id": f"{name}-{i + 1}",
                    "name": text.get(i18n.language()) or text.get("en") or labels[name],
                    "size": [widget.get("w", 100), widget.get("h", 100)],
                    "widgets": [widget],
                }
            )
    screens = {}
    for name in installed.names("screens"):
        cls = installed.get("screens", name)
        if cls is not None:
            options = {k: list(v) for k, v in cls.options.items()}
            screens[name] = {"label": label_for(cls), "options": options}
    toasts = {}
    for name, cls in BUILT_IN_STYLES.items():  # the card is the editor's own first choice
        if name != "card":
            label = i18n.t("{name} (built in)", name=label_for(cls))
            toasts[name] = {"label": label, "options": {k: list(v) for k, v in cls.options.items()}}
    for name in installed.names("toasts"):
        cls = installed.get("toasts", name)
        if cls is not None:
            options = {k: list(v) for k, v in cls.options.items()}
            toasts[name] = {"label": label_for(cls), "options": options}
    return {
        "widgets": widgets,
        "widget_labels": labels,
        "presets": presets,
        "screens": screens,
        "toasts": toasts,
    }


class EditorState:
    def __init__(self, config_path: Path | None = None, controls: Any = None) -> None:
        self.config_path = config_path
        self.controls = controls
        self._hub: SensorHub | None = None
        self._lock = threading.Lock()
        self.pages: dict[str, Any] = {}  # plugin editor pages, created on first use
        self.previews: dict[tuple, bytes] = {}  # pictures of saved themes
        self.previews_lock = threading.Lock()
        self.restart = app_settings.Restart()  # settings the panel loop takes in when it restarts

    def live_snapshot(self):
        """The readings for previews. In the background app they are the panel's
        own: sources that drive hardware must not run twice in one process."""
        from libre_panel.app import build_hub

        host = getattr(self.controls, "plugin_host", None)
        if host is not None:
            return host.latest or Snapshot()
        with self._lock:
            if self._hub is None:
                self._hub = build_hub(load_config(self.config_path))
                self._hub.snapshot()  # rates need a previous sample
            return self._hub.snapshot()

    def fan_choices(self) -> fan.Choices:
        """What the user chose for the fans in config.toml (their look and colours): the
        canvas shows them as the panel does. Read again when the file changed."""
        try:
            stamp = self.config_path.stat().st_mtime_ns if self.config_path else None
        except OSError:
            stamp = None
        kept = getattr(self, "_fan_choices", None)
        if kept is None or kept[0] != stamp:
            try:
                choices = fan.Choices.of(load_config(self.config_path))
            except (ConfigError, OSError):
                choices = kept[1] if kept is not None else fan.Choices()
            kept = self._fan_choices = (stamp, choices)
        return kept[1]

    def follow_config(self) -> None:
        """config.toml was written: the sources of this editor's own hub (a plain editor;
        in the background app the panel's follow the file by themselves) take it in."""
        with self._lock:
            hub = self._hub
            if hub is None:
                return
            try:
                hub.follow_config(load_config(self.config_path))
            except ConfigError:
                pass  # until the file is fixed they go on as they are

    def close(self) -> None:
        with self._lock:
            if self._hub is not None:
                self._hub.close()


_PART = re.compile(r"\{(\w+)([+*]?)\}")
_PARTS = {"": "[^/]+", "+": ".+", "*": ".*"}


def _compile(template: str) -> re.Pattern[str]:
    """The pattern of a route's template: ``{name}`` is one part of the path, ``{name+}`` the
    rest of it (one character or more), ``{name*}`` the rest, or nothing."""
    pieces, last = [], 0
    for part in _PART.finditer(template):
        pieces.append(re.escape(template[last : part.start()]))
        pieces.append(f"(?P<{part[1]}>{_PARTS[part[2]]})")
        last = part.end()
    pieces.append(re.escape(template[last:]))
    return re.compile("".join(pieces))


@dataclass(frozen=True)
class Route:
    """One way into the server (``ROUTES``): the method, the path as a template, what to do."""

    method: str
    template: str
    handle: Callable[[EditorHandler, re.Match[str], ParseResult], None]
    # the app's own files and those of plugins' pages (code, no data): no token needed
    public: bool = False
    # an <img> asks for it and cannot send the header: the token may be in the query (?token=)
    picture: bool = False
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern", _compile(self.template))


class EditorHandler(BaseHTTPRequestHandler):
    server_version = f"LibrePanelEditor/{__version__}"
    state: EditorState
    allowed_hosts: set[str]
    token: str  # what a request must carry (access.py); made with the server
    # The running background app (tray or `start`); None for a plain `libre-panel editor`.
    controls: Any = None

    def log_message(self, fmt: str, *args: Any) -> None:
        # the line of a request has its address, and a picture's address may have the token
        shown = [access.hide_token(arg) if isinstance(arg, str) else arg for arg in args]
        log.debug("editor: " + fmt, *shown)

    # -- helpers -----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def parse_request(self) -> bool:
        self._body_read = False
        return super().parse_request()

    def _discard_body(self) -> None:
        """Read what the client sent before answering without it. Closing a socket
        with unread data resets the connection (at once on Windows), and the client
        then sees a connection error instead of the answer."""
        if getattr(self, "_body_read", True):
            return
        self._body_read = True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < length <= MAX_ASSET:
            self.rfile.read(length)

    def _error(self, message: str, status: int = 400) -> None:
        self._discard_body()
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        """Only the names this server listens under are answered (a web page that reaches it
        through a name of its own gets nothing), and a browser's request only from a page of
        the app itself."""
        if self.headers.get("Host", "") not in self.allowed_hosts:
            self._error("forbidden host", HTTPStatus.FORBIDDEN)
            return False
        origin = self.headers.get("Origin")  # sent by a browser on a request from another page
        if origin is not None and origin not in {f"http://{host}" for host in self.allowed_hosts}:
            self._error("forbidden origin", HTTPStatus.FORBIDDEN)
            return False
        return True

    def _token_ok(self, url: ParseResult, route: Route | None) -> bool:
        """Whether the request carries this run's token: in the header, or, for a picture, which
        an <img> asks for and cannot send a header with, in the query."""
        given = self.headers.get(access.HEADER)
        if given is None and route is not None and route.picture:
            given = (parse_qs(url.query).get(access.NAME) or [""])[0]
        return access.matches(given, self.token)

    def _read_body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit:
            self._error("request too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        self._body_read = True
        return self.rfile.read(length)

    def _read_json(self) -> Any:
        body = self._read_body(MAX_JSON)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error("invalid JSON")
            return None

    # -- the routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def _serve(self, method: str) -> None:
        """Answer a request: only to a name of this server and only with the token, but for
        the app's own files. The token is asked for first, also for a path that is no route:
        only those who may use the server learn what is there."""
        if not self._host_ok():
            return
        url = urlparse(self.path)
        route, match = None, None
        for candidate in ROUTES:
            found = candidate.method == method and candidate.pattern.fullmatch(url.path)
            if found:
                route, match = candidate, found
                break
        if (route is None or not route.public) and not self._token_ok(url, route):
            return self._error(
                t(
                    "Access token missing or wrong. Open Libre Panel from its window or its "
                    "tray icon, or with the address that libre-panel editor prints."
                ),
                HTTPStatus.FORBIDDEN,
            )
        if route is None or match is None:
            return self._error("not found", HTTPStatus.NOT_FOUND)
        route.handle(self, match, url)

    # -- plugin editor pages ---------------------------------------------------

    def _page_class(self, page_id: str) -> Any:
        from libre_panel.plugins.loader import registry

        return registry().get("editor_pages", page_id)

    def _pages(self) -> list[dict[str, str]]:
        from libre_panel.plugins.loader import registry

        pages = []
        for page_id in registry().names("editor_pages"):
            cls = self._page_class(page_id)
            folder = cls.folder() if cls is not None else None
            if folder is None or not (folder / "index.html").is_file():
                continue
            title = cls.title.get(i18n.language()) or cls.title.get("en") or page_id
            pages.append({"id": page_id, "title": title, "icon": cls.icon})
        return pages

    def _page_file(self, page_id: str, name: str) -> None:
        cls = self._page_class(page_id)
        folder = cls.folder() if cls is not None else None
        content_type = _PAGE_TYPES.get(Path(name).suffix.lower())
        if folder is None or content_type is None:
            return self._error("not found", HTTPStatus.NOT_FOUND)
        base = folder.resolve()
        file = (base / name).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        self._send(200, file.read_bytes(), content_type)

    def _page_api(self, method: str, page_id: str, path: str, query: dict, body: Any) -> None:
        from libre_panel.plugins.pages import PageContext

        cls = self._page_class(page_id)
        if cls is None:
            return self._error("not found", HTTPStatus.NOT_FOUND)
        pages = self.state.pages
        if page_id not in pages:
            pages[page_id] = cls(PageContext(self.controls, self.state.config_path))
        try:
            status, data, *kind = pages[page_id].handle(method, path, query, body)
            if isinstance(data, bytes):  # a file, e.g. a picture: (status, bytes, content type)
                payload, content_type = data, kind[0] if kind else "application/octet-stream"
            else:
                payload, content_type = json.dumps(data).encode("utf-8"), "application/json"
        except Exception as exc:  # a broken page must not take the editor down
            log.exception("editor page %s failed", page_id)
            return self._error(f"{page_id}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
        self._send(int(status), payload, content_type)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.MOVED_PERMANENTLY)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _static(self, name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
            return self._error("not found", HTTPStatus.NOT_FOUND)
        file = STATIC_DIR / name
        if not file.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        content_type = _CONTENT_TYPES.get(file.suffix, "application/octet-stream")
        self._send(200, file.read_bytes(), content_type)

    # -- the app ---------------------------------------------------------------

    def _app_file(self, name: str) -> None:
        """A file of the app: its page, modules, styles and vendored libraries."""
        content_type = _APP_TYPES.get(Path(name).suffix.lower())
        if not _APP_PATH.fullmatch(name) or ".." in name or content_type is None:
            return self._error("not found", HTTPStatus.NOT_FOUND)
        base = APP_DIR.resolve()
        file = (base / name).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        self._send(200, file.read_bytes(), content_type)

    def _font(self, name: str) -> None:
        """The bundled fonts, for the app's own text (never from the network)."""
        path = builtin_font_path(BUILTIN_PREFIX + name.removesuffix(".ttf"))
        if not name.endswith(".ttf") or path is None:
            return self._error("not found", HTTPStatus.NOT_FOUND)
        self._send(200, path.read_bytes(), "font/ttf")

    def _asset_file(self, theme_id: str, rel: str) -> None:
        """An image or font of a theme, for the library of pictures and their previews."""
        try:
            file = resolve_asset(find_theme(theme_id), rel)
        except ThemeError:
            return self._error("not found", HTTPStatus.NOT_FOUND)
        content_type = _ASSET_TYPES.get(file.suffix.lower())
        if content_type is None or not file.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        self._send(200, file.read_bytes(), content_type)

    def _theme_preview(self, theme_id: str, query: dict[str, list[str]]) -> None:
        """A saved theme as a picture (``?w=`` its width), for lists of themes. The
        pictures are kept until the theme changes."""
        try:
            width = max(64, min(1920, int((query.get("w") or ["480"])[0])))
        except ValueError:
            width = 480
        try:
            folder = find_theme(theme_id)
            stamp = (folder / THEME_FILENAME).stat().st_mtime_ns
        except (ThemeError, OSError):
            return self._error("not found", HTTPStatus.NOT_FOUND)
        key = (theme_id, stamp, width, i18n.language())
        with self.state.previews_lock:
            png = self.state.previews.get(key)
        if png is None:
            try:
                theme = load_theme(folder)
            except ThemeError as exc:
                return self._error(str(exc), HTTPStatus.NOT_FOUND)
            renderer = Renderer(theme, preview=True)
            try:
                frame, _boxes = renderer.render(demo_snapshot(fixed_time=1_700_000_000))
            finally:
                renderer.close()
            frame = frame.convert("RGB")
            if frame.width > width:
                frame = frame.resize((width, max(1, round(frame.height * width / frame.width))))
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG")
            png = buffer.getvalue()
            with self.state.previews_lock:
                cache = self.state.previews
                while len(cache) >= 48:  # the oldest go first
                    cache.pop(next(iter(cache)))
                cache[key] = png
        self._send(200, png, "image/png")

    def _fan_look(self, query: dict[str, list[str]]) -> None:
        """``GET /api/fan-look?look=libre&paint=own``: a fan of that look as a picture
        (PNG), in its square frame and the app's accent: the tiles of Settings show them."""
        look = (query.get("look") or [fan_looks.DEFAULT])[0]
        paint = (query.get("paint") or ["theme"])[0]
        if look not in fan_looks.LOOKS or paint not in FAN_PAINTS:
            return self._error("unknown look or colours", HTTPStatus.NOT_FOUND)
        rotor = fan.Rotor("preview", fan_looks.look(look).blades)
        rotor.settle(1800.0, 3000.0)  # a fan that runs: lit ...
        rotor.speed = 0.0  # ... drawn standing, its blades sharp
        image = fan.fan_image((34, 211, 238), 112, 112, rotor, "square", look, paint)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self._send(200, buffer.getvalue(), "image/png")

    def _readings(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Sensor values for the editor's pickers (``?keys=a,b``; all without it) and
        the recent history of some (``?history=a``): live where there are live ones,
        sample values otherwise, each marked ``sample``."""
        snapshot = demo_snapshot()
        live = False
        measured: set[str] = set()  # the keys this computer gives; the rest are samples
        try:
            current = self.state.live_snapshot()
            live = bool(current.readings)
            measured = set(current.readings)
            snapshot.readings.update(current.readings)
            snapshot.history.update({k: v for k, v in current.history.items() if len(v) > 1})
        except Exception as exc:  # live sensors are optional for the editor
            log.debug("live sensors unavailable: %s", exc)

        def plain(value: Any) -> Any:  # JSON has no NaN
            return None if isinstance(value, float) and not math.isfinite(value) else value

        wanted = [k for k in ",".join(query.get("keys", [])).split(",") if k]
        readings = {}
        for key in wanted or sorted(snapshot.readings):
            reading = snapshot.readings.get(key)
            if reading is not None and not key.startswith("_"):
                readings[key] = {
                    "value": plain(reading.value),
                    "unit": reading.unit,
                    "label": reading.label,
                    # a sample value: this computer gives none for the key (a fan it does not
                    # have), so the panel shows nothing there
                    "sample": key not in measured,
                }
        if not wanted or TIME_SECOND in wanted:  # the clock's own (a seconds run): always live
            second = snapshot.value(TIME_SECOND)
            readings[TIME_SECOND] = {
                "value": second, "unit": "s", "label": "Seconds of the minute", "sample": False
            }  # fmt: skip
        history = {}
        for key in ",".join(query.get("history", [])).split(","):
            values = snapshot.history.get(key) if key else None
            if values:
                history[key] = [plain(round(v, 3)) for v in values[-60:]]
        return {"live": live, "readings": readings, "history": history}

    def _panel_state(self) -> dict[str, Any]:
        """What the running app says about its panel (``panel`` of ``/api/app``); empty
        without one, or when it cannot say."""
        if self.controls is None:
            return {}
        try:
            return self.controls.snapshot()["panel"] or {}
        except Exception as exc:  # the panel's state is a nice-to-have here
            log.debug("no panel state: %s", exc)
            return {}

    def _panel_pages(self) -> None:
        """The pages of the panel: the theme it shows normally (config.toml's, else the
        panel's own default), the theme of each mode ([modes.<name>], game mode's among
        them, with its frame rate; None: the normal one) and the round report's (the memory
        service's ``report_theme``), and which page is on show now, whether config.toml names
        a theme at all (``chosen``) and the panel's own ``default``. The console's
        ``control/themes`` has the first two."""
        from libre_panel.services.game import MODE as GAME_MODE
        from libre_panel.services.memory import Memory

        try:
            config = load_config(self.state.config_path)
        except ConfigError as exc:
            return self._error(str(exc), HTTPStatus.CONFLICT)
        panel = self._panel_state()
        # the panel's own default: the running app's word, else worked out from the config
        default = panel.get("default_theme") or default_theme(panel_model(config.device))
        pages = [{"id": "normal", "theme": config.theme if config.theme_given else default,
                  "sequence": config.sequence.active}]  # fmt: skip
        names = [GAME_MODE, *sorted(name for name in config.modes if name != GAME_MODE)]
        for name in names:
            mode = config.modes.get(name)
            theme, fps = (mode.theme or None, mode.fps) if mode else (None, None)
            pages.append({"id": name, "mode": name, "theme": theme, "fps": fps})
        memory = config.services.options.get(Memory.name, {})
        report = memory.get("report_theme", Memory.options["report_theme"][1])
        pages.append(
            {
                "id": "report",
                "service": Memory.name,
                "theme": report if isinstance(report, str) and report else None,
                "on": Memory.name in config.services.enabled and memory.get("report", True) is True,
            }
        )
        chosen = theme_chosen(config.path)  # false on a first start: Start offers the themes
        self._json(
            {
                "pages": pages,
                "shown": panel.get("theme"),
                "mode": panel.get("mode"),
                "chosen": chosen,
                "default": default,
            }
        )

    def _i18n(self) -> dict[str, Any]:
        try:
            setting = load_config(self.state.config_path).language
        except ConfigError:
            setting = "auto"
        return {
            "language": i18n.language(),
            "setting": setting,
            "languages": i18n.LANGUAGES,
            **{k: v for k, v in i18n.catalog(i18n.language()).items() if k in _EDITOR_TABLES},
        }

    def _specs(self) -> dict[str, Any]:
        presets = presets_for_editor()
        for preset in presets["presets"]:
            preset["name"] = i18n.t(preset["name"])
        models = [{**m.to_dict(), "default_theme": default_theme(m)} for m in MODELS]
        for model in models:
            if model.get("notes"):
                model["notes"] = i18n.t(model["notes"])
        widgets = {t: {k: list(v) for k, v in spec.items()} for t, spec in WIDGET_SPECS.items()}
        plugins = _plugin_specs()
        widgets.update(plugins["widgets"])
        presets["presets"] += plugins["presets"]
        return {
            "version": __version__,
            "widgets": widgets,
            "widget_labels": plugins["widget_labels"],
            "screens": plugins["screens"],
            "toasts": plugins["toasts"],
            "toast_anchors": list(TOAST_ANCHORS),
            "common": {k: list(v) for k, v in COMMON_FIELDS.items()},
            "effect_fields": list(EFFECT_FIELDS),
            # names for values a name of its own would not say (the language's catalog first)
            "enum_labels": {**fan_looks.labels(), "theme": "the theme's", "own": "the look's own"},
            # what the inspector shows first for each widget type
            "primary": {kind: list(keys) for kind, keys in PRIMARY_FIELDS.items()},
            # which widget types each finish draws (flat draws all)
            "finishes": _finish_types(list(widgets)),
            # the panel's look, block by block: Theme -> Look
            "look": {
                name: {k: list(v) for k, v in spec.items()} for name, spec in LOOK_SPECS.items()
            },
            # the range each numeric field of a look block accepts: {block: {field: [low, high]}}
            "look_ranges": _look_ranges(),
            "models": models,
            "protocols": PROTOCOLS,
            "fonts": [BUILTIN_PREFIX + name for name in builtin_fonts()],
            "default_font": DEFAULT_FONT,
            "icons": list(ICON_NAMES),
            "modules": module_info(),
            # what the look blocks that are lists may hold, at most
            "look_limits": {"dust": {"colors": DUST_MAX_COLORS, "areas": DUST_MAX_AREAS}},
            **presets,
        }

    def _assets(self, theme_id: str) -> None:
        try:
            folder = find_theme(theme_id)
        except ThemeError as exc:
            return self._error(str(exc), HTTPStatus.NOT_FOUND)
        files = sorted(
            path.relative_to(folder).as_posix()
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in ASSET_EXTENSIONS
        )
        self._json(files)

    def _get_theme(self, theme_id: str) -> None:
        try:
            folder = find_theme(theme_id)
            theme = load_theme(folder)
        except ThemeError as exc:
            return self._error(str(exc), HTTPStatus.NOT_FOUND)
        builtin = not folder.resolve().is_relative_to(user_themes_dir().resolve())
        self._json(
            {
                "id": theme_id,
                "builtin": builtin,
                "theme": theme.to_dict(),
                "warnings": theme.warnings,
            }
        )

    def _sensors(self) -> list[dict[str, Any]]:
        """Every key a theme can show: those that work on demand, the demo's and the panel's
        own (a source's unit and label come last, so they win), and the pictures."""
        found: dict[str, dict[str, Any]] = {
            key: {"key": key, "unit": unit, "label": label} for key, unit, label in ON_DEMAND
        }
        readings = dict(demo_snapshot().readings)
        try:
            readings.update(self.state.live_snapshot().readings)
        except Exception as exc:  # live sensors are optional for the editor
            log.debug("live sensors unavailable: %s", exc)
        for key, reading in readings.items():
            found[key] = {"key": key, "unit": reading.unit, "label": reading.label}
        for key, label in PICTURES:
            found[key] = {"key": key, "unit": "", "label": label, "picture": True}
        return [found[key] for key in sorted(found)]

    # -- what POST routes do ---------------------------------------------------------

    def _post_page(self, page_id: str, path: str, query: dict[str, list[str]]) -> None:
        """A plugin's page gets what it was sent (a JSON object) and the answer is its own."""
        body = self._read_json()
        if body is None:
            return
        self._page_api("POST", page_id, path, query, body)

    def _set_language(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        setting = payload.get("language") if isinstance(payload, dict) else None
        if setting not in ("auto", *i18n.LANGUAGES):
            return self._error("language must be auto, en or de")
        try:
            set_config_value("language", setting, self.state.config_path)
        except ConfigError as exc:
            return self._error(str(exc))
        i18n.set_language(setting)  # the tray and the panel follow at once
        self._json(self._i18n())

    def _set_panel_page(self) -> None:
        """``{"page": ..., "theme": ...}`` and, for a mode, ``{"fps": ...}``: what a page of
        the panel shows, written to config.toml. ``page`` is "normal", "report" (the round
        report) or a mode's name; a mode's empty theme is the normal one's."""
        from libre_panel.services.memory import Memory

        payload = self._read_json()
        if payload is None:
            return
        page = payload.get("page") if isinstance(payload, dict) else None
        if not isinstance(page, str) or not _MODE_NAME.fullmatch(page):
            return self._error('expected {"page": "normal", "report" or a mode, ...}')
        theme = payload.get("theme")
        fps = payload.get("fps")
        if theme is not None and not isinstance(theme, str):
            return self._error("theme must be a theme name")
        if fps is not None and (isinstance(fps, bool) or not isinstance(fps, int)):
            return self._error("fps must be a whole number")
        if page in ("normal", "report") and (fps is not None or not theme):
            return self._error(f"the page {page} needs a theme and has no frame rate of its own")
        keys = {"normal": "theme", "report": f"services.{Memory.name}.report_theme"}
        key = keys.get(page, f"modes.{page}.theme")
        changes: list[tuple[str, str, Any]] = []
        if theme is not None:
            changes.append(("value", key, theme))
        if fps is not None:
            changes.append(("value", f"modes.{page}.fps", fps))
        try:
            if theme:
                find_theme(theme)
            if page == "normal" and load_config(self.state.config_path).sequence.active:
                # one theme chosen: the screens in turn stop (they are kept, to go on later)
                changes.append(("value", "sequence.on", False))
            if changes:
                edit_config(*changes, path=self.state.config_path)
        except ThemeError as exc:
            return self._error(str(exc))
        except (ConfigError, OSError) as exc:
            return self._error(str(exc), HTTPStatus.CONFLICT)
        self._panel_pages()

    def _sequence(self) -> None:
        """The screens in turn: their steps (a theme and its seconds each), whether they
        show, and where the running panel stands in them (``now``: the step's index and
        theme and the seconds left of it; None while it shows no sequence)."""
        try:
            config = load_config(self.state.config_path)
        except ConfigError as exc:
            return self._error(str(exc), HTTPStatus.CONFLICT)
        steps = [{"theme": step.theme, "seconds": step.seconds} for step in config.sequence.steps]
        now = self._panel_state().get("sequence") if config.sequence.active else None
        self._json(
            {
                "on": config.sequence.on,
                "steps": steps,
                "now": now,
                "shortest_s": sequence.SHORTEST_S,
                "longest_s": sequence.LONGEST_S,
            }
        )

    def _set_sequence(self) -> None:
        """``{"on": true, "steps": [{"theme": "almanac", "seconds": 300}, ...]}``: the screens
        in turn, written to config.toml ([sequence]); without steps they are off."""
        payload = self._read_json()
        if payload is None:
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("on"), bool):
            return self._error('expected {"on": true or false, "steps": [...]}')
        raw = payload.get("steps", [])
        if not isinstance(raw, list) or not all(isinstance(step, dict) for step in raw):
            return self._error('steps: expected a list of {"theme": ..., "seconds": ...}')
        try:
            steps = sequence.parse_steps(
                [{"theme": step.get("theme"), "seconds": step.get("seconds")} for step in raw]
            )
            for step in steps:
                find_theme(step.theme)
            edit_config(
                ("value", "sequence.on", payload["on"] and bool(steps)),
                ("tables", "sequence.steps", [step.to_table() for step in steps]),
                path=self.state.config_path,
            )
        except (sequence.StepError, ThemeError) as exc:
            return self._error(str(exc))
        except (ConfigError, OSError) as exc:
            return self._error(str(exc), HTTPStatus.CONFLICT)
        self._sequence()

    def _settings(self, method: str, body: bool = False) -> None:
        """A route of the Settings (editor/settings.py): its answer, or why it is refused."""
        args = []
        if body:
            payload = self._read_json()
            if payload is None:
                return
            args.append(payload)
        settings = app_settings.Settings(self.state.config_path, self.controls, self.state.restart)
        try:
            self._json(getattr(settings, method)(*args))
        except app_settings.SettingsError as exc:
            self._error(str(exc), exc.status)
        except Exception as exc:  # the app shows the reason; a dropped connection says nothing
            log.exception("settings: %s failed", method)
            self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _preset_previews(self) -> None:
        """Each building block (presets.py and plugins') drawn in the given palette,
        for the library."""
        payload = self._read_json()
        if payload is None:
            return
        palette = payload.get("palette") if isinstance(payload, dict) else None
        palette = palette if isinstance(palette, dict) else {}
        specs = presets_for_editor()
        tokens = specs["tokens"]

        def resolve(value: Any) -> Any:  # "$accent": the theme's colour, else the default
            if isinstance(value, str) and value.startswith("$"):
                name = value[1:]
                return f"@{name}" if name in palette else tokens.get(name, "#ffffff")
            if isinstance(value, list):
                return [resolve(v) for v in value]
            if isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            return value

        snapshot = demo_snapshot(fixed_time=1_700_000_000)
        previews = {}
        pad = 8
        for preset in specs["presets"] + _plugin_specs()["presets"]:
            width, height = (max(16, int(v) + 2 * pad) for v in preset["size"])
            widgets = [
                {**raw, "x": raw.get("x", 0) + pad, "y": raw.get("y", 0) + pad}
                for raw in resolve(preset["widgets"])
            ]
            try:
                theme = parse_theme(
                    {
                        "format": "libre-panel-theme/1",
                        "display": {"width": width, "height": height},
                        "palette": palette,
                        "background": {"color": "@bg" if "bg" in palette else "#0b1016"},
                        "widgets": widgets,
                    }
                )
            except ThemeError as exc:
                log.debug("no preview for %s: %s", preset["id"], exc)
                continue
            renderer = Renderer(theme, preview=True)
            try:
                frame, _boxes = renderer.render(snapshot)
            finally:
                renderer.close()
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG")
            previews[preset["id"]] = base64.b64encode(buffer.getvalue()).decode("ascii")
        self._json({"previews": previews})

    def _hardware(self, refresh: bool = False) -> None:
        """The names of the processor, graphics card and memory: what Libre Panel finds
        (``detected``, as the panel would show it) and the user's own text for each
        (``custom``, empty: the detected one). ``?refresh=1`` asks the computer again."""
        try:
            config = load_config(self.state.config_path)
        except ConfigError as exc:
            return self._error(str(exc))
        card = config.sensors.options.get("librehardwaremonitor", {}).get("gpu")
        found = detected_names(card if isinstance(card, str) else "", refresh)
        own = config.hardware
        custom = {"cpu": own.cpu_name, "gpu": own.gpu_name, "memory": own.memory_name}
        answer: dict[str, Any] = {
            part: {"detected": found[part], "custom": custom[part]} for part in custom
        }
        answer["fans"] = self._fans(config)
        # how the fans turn, for all of them ("": Noctua's G2 in the theme's colours)
        answer["fan_look"] = {
            "look": own.fan_look,
            "paint": own.fan_paint,
            "looks": [{"id": key, "label": label} for key, label in fan_looks.labels().items()],
        }
        self._json(answer)

    def _fans(self, config: Any) -> list[dict[str, Any]]:
        """The fans this computer reads, each ``{key, detected, custom, value}``: the name it
        goes by without one of the user's own (FanControl's, else the sensor's), the user's,
        and its speed now (which tells one from the other). A fan named but not read now
        stays in the list, so that its name can be taken back."""
        from libre_panel.sensors.fan_names import FanNames, is_fan

        try:
            readings = self.state.live_snapshot().readings
        except Exception as exc:  # live sensors are optional here
            log.debug("live sensors unavailable: %s", exc)
            readings = {}
        names = FanNames()
        names.follow_config(config)
        own = config.hardware.fans
        fans = {}
        for key, reading in readings.items():
            if is_fan(key, reading):
                value = reading.value if isinstance(reading.value, int | float) else None
                fans[key] = {
                    "key": key,
                    "detected": names.detected(reading, readings),
                    "custom": own.get(key, ""),
                    "value": round(value) if value is not None and math.isfinite(value) else None,
                }
        hw = config.hardware
        for key in [*own, *hw.fan_looks, *hw.fan_paints]:
            fans.setdefault(
                key, {"key": key, "detected": "", "custom": own.get(key, ""), "value": None}
            )
        for key, fan_ in fans.items():  # its own look and colours ("": as all fans)
            fan_["look"], fan_["paint"] = hw.fan_looks.get(key, ""), hw.fan_paints.get(key, "")
        return [fans[key] for key in sorted(fans, key=_natural)]

    def _set_hardware(self) -> None:
        """Set the user's own text for any of ``cpu``, ``gpu`` and ``memory``, and for fans
        (``{"fans": {"fan.lpc.fan_1": "Intake"}}``); "" gives the detected name back. The
        fans' look and colours: ``fan_look`` and ``fan_paint`` for all of them, and
        ``fan_looks`` / ``fan_paints`` by sensor ("" for "as all of them"). Answers like the
        GET."""
        payload = self._read_json()
        if payload is None:
            return
        tables = ("fans", "fan_looks", "fan_paints")
        choices = {"fan_look": FAN_LOOKS, "fan_paint": FAN_PAINTS}
        by_fan = {"fan_looks": FAN_LOOKS, "fan_paints": FAN_PAINTS}

        def fan_table(value: Any) -> bool:
            return isinstance(value, dict) and all(
                isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in value.items()
            )

        parts = (
            {k: v for k, v in payload.items() if k not in (*tables, *choices)}
            if isinstance(payload, dict)
            else None
        )
        if (
            not isinstance(payload, dict)
            or not payload
            or not set(parts) <= set(_HARDWARE_SETTINGS)
            or not all(isinstance(text, str) for text in parts.values())
            or not all(fan_table(payload[k]) for k in tables if k in payload)
            or not all(
                payload[k] in ("", *allowed) for k, allowed in choices.items() if k in payload
            )
            or not all(
                v in ("", *allowed)
                for k, allowed in by_fan.items()
                for v in payload.get(k, {}).values()
            )
        ):
            return self._error(
                'expected text for "cpu", "gpu" or "memory", "fans" by sensor, or the fans\' '
                '"fan_look" and "fan_paint" (also "fan_looks" and "fan_paints" by sensor): '
                '{"cpu": "My CPU", "fans": {"fan.lpc.fan_1": "Intake"}, "fan_look": "libre"}'
            )
        try:
            changes: list[tuple[str, str, Any]] = []
            for part, text in parts.items():
                name = f"hardware.{_HARDWARE_SETTINGS[part]}"
                changes.append(("value", name, hardware_name(text, name)))
            for key in choices:
                if key in payload:
                    changes.append(("value", f"hardware.{key}", payload[key]))
            if any(key in payload for key in tables):
                hw = load_config(self.state.config_path).hardware
                kept = {
                    "name": dict(hw.fans),
                    "look": dict(hw.fan_looks),
                    "paint": dict(hw.fan_paints),
                }
                given = (("fans", "name"), ("fan_looks", "look"), ("fan_paints", "paint"))
                for table, field in given:
                    for key, text in payload.get(table, {}).items():
                        if field == "name":
                            text = hardware_name(text, f"hardware.fans ({key})")
                        if text:
                            kept[field][key.strip()] = text
                        else:
                            kept[field].pop(key.strip(), None)
                sensors = sorted(set().union(*kept.values()), key=_natural)
                rows = [
                    {
                        "sensor": key,
                        **{f: kept[f][key] for f in ("name", "look", "paint") if key in kept[f]},
                    }
                    for key in sensors
                ]
                changes.append(("tables", "hardware.fans", rows))
            edit_config(*changes, path=self.state.config_path)  # all of them in one write
        except (ConfigError, OSError) as exc:
            return self._error(str(exc))
        self.state.follow_config()
        self._hardware()

    def _app_action(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        if self.controls is None:
            return self._error("this editor does not run the panel", HTTPStatus.NOT_FOUND)
        if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
            return self._error('expected {"action": ...}')
        try:
            snapshot = self.controls.act(payload["action"], payload.get("value"))
        except (ValueError, ConfigError, OSError) as exc:
            return self._error(str(exc))
        self._json({"available": True, **snapshot})

    def _app_state(self) -> None:
        """``GET /api/app``: the background app's state, if this editor belongs to one."""
        if self.controls is None:
            return self._json({"available": False})
        self._json({"available": True, **self.controls.snapshot()})

    def _base_folder(self, base: Any) -> Path | None:
        if isinstance(base, str) and valid_theme_name(base):
            try:
                return find_theme(base)
            except ThemeError:
                return None
        return None

    def _render(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        try:
            theme = parse_theme(payload.get("theme"), root=self._base_folder(payload.get("base")))
        except ThemeError as exc:
            return self._error(str(exc))
        snapshot = demo_snapshot()
        if payload.get("live"):
            try:
                live = self.state.live_snapshot()
                snapshot.readings.update(live.readings)
                snapshot.history.update({k: v for k, v in live.history.items() if len(v) > 1})
            except Exception as exc:
                log.warning("live sensors unavailable: %s", exc)
        renderer = Renderer(theme, preview=True)
        renderer.fan_choices = self.state.fan_choices()
        try:
            frame, boxes = renderer.render(snapshot)
            notice = payload.get("notice")
            if notice:  # a test message on it, as the theme's toast style shows it
                shown = _with_notice(renderer, frame, notice) if isinstance(notice, str) else None
                if shown is None:
                    return self._error("notice must be the kind of a test message")
                frame = shown
        finally:
            renderer.close()  # plugin screens end with the preview
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG")
        has_modules = any(w["type"] == "module" for w in theme.widgets)
        grid = make_grid(theme.width, theme.height, theme.grid, theme.model)
        self._json(
            {
                "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "boxes": boxes,
                "width": theme.width,
                "height": theme.height,
                "warnings": theme.warnings + renderer.warnings,
                # the grid modules sit on (active once the theme has modules or a
                # grid), and every module's cells, also of hidden ones
                "grid": grid.to_dict(),
                "grid_active": has_modules or theme.grid is not None,
                "module_boxes": renderer.module_boxes,
            }
        )

    def _module_previews(self) -> None:
        """Each module kind in a 2x1 box with the given look, for the library."""
        payload = self._read_json()
        if payload is None:
            return
        palette = payload.get("palette") or look_theme_parts("arctic")[0]
        style = payload.get("style") or look_theme_parts("arctic")[1]
        sources = {"ring": "cpu", "stat": "gpu", "graph": "cpu"}
        snapshot = demo_snapshot(fixed_time=1_700_000_000)
        previews = {}
        for kind in MODULE_KINDS:
            try:
                theme = parse_theme(
                    {
                        "format": "libre-panel-theme/1",
                        "display": {"width": 460, "height": 220},
                        "palette": palette,
                        "style": style,
                        "background": {"color": "#000000"},
                        "grid": {"columns": 2, "rows": 1, "gap": 10, "margin": 10},
                        "widgets": [
                            {"type": "module", "id": "m", "module": kind, "cols": 2,
                             "source": sources.get(kind, "cpu")}
                        ],
                    }
                )  # fmt: skip
            except ThemeError as exc:
                return self._error(str(exc))
            renderer = Renderer(theme, preview=True)
            try:
                frame, _boxes = renderer.render(snapshot)
                x, y, w, h = renderer.module_boxes["m"]
            finally:
                renderer.close()
            buffer = io.BytesIO()
            frame.crop((x, y, x + w, y + h)).save(buffer, format="PNG")
            previews[kind] = base64.b64encode(buffer.getvalue()).decode("ascii")
        self._json({"previews": previews})

    def _templates(self) -> None:
        """Starting layouts of modules for a panel."""
        payload = self._read_json()
        if payload is None:
            return
        try:
            width, height = int(payload["width"]), int(payload["height"])
            grid = make_grid(width, height, None, str(payload.get("model", "custom")))
        except (KeyError, TypeError, ValueError) as exc:
            return self._error(f"bad size: {exc}")
        self._json({"grid": grid.to_dict(), "templates": templates(grid.columns, grid.rows)})

    def _detach(self) -> None:
        """A module's parts as plain widgets."""
        payload = self._read_json()
        if payload is None:
            return
        try:
            theme = parse_theme(payload.get("theme"), root=self._base_folder(payload.get("base")))
        except ThemeError as exc:
            return self._error(str(exc))
        self._json({"widgets": detach(theme, str(payload.get("id", "")))})

    def _adapt(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        try:
            result = adapt_theme(
                payload.get("theme"),
                model=str(payload.get("model", "custom")),
                orientation=str(payload.get("orientation", "landscape")),
                mode=str(payload.get("mode", "scale")),
                width=int(payload.get("width") or 0),
                height=int(payload.get("height") or 0),
            )
        except (ThemeError, KeyError, ValueError, TypeError) as exc:
            return self._error(str(exc))
        self._json({"theme": result})

    def _save(self, theme_id: str) -> None:
        payload = self._read_json()
        if payload is None:
            return
        source = payload.get("source")
        try:
            folder = save_theme(
                theme_id, payload.get("theme"), source if isinstance(source, str) else None
            )
        except (ThemeError, OSError) as exc:
            return self._error(str(exc))
        self._json({"ok": True, "id": theme_id, "path": str(folder)})

    def _upload_asset(self, theme_id: str, query: dict[str, list[str]]) -> None:
        name = (query.get("name") or [""])[0]
        if not valid_theme_name(theme_id):
            return self._error("invalid theme name")
        if not _ASSET_NAME.fullmatch(name) or Path(name).suffix.lower() not in ASSET_EXTENSIONS:
            return self._error(f"asset must be one of: {', '.join(sorted(ASSET_EXTENSIONS))}")
        folder = user_themes_dir() / theme_id
        if not (folder / "theme.json").is_file():
            return self._error("save the theme before adding images or fonts")
        body = self._read_body(MAX_ASSET)
        if body is None:
            return
        target = folder / "assets" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self._json({"ok": True, "path": f"assets/{name}"})


def _refresh_asked(query: str) -> bool:
    """``?refresh=1`` (and ``?refresh``): ask the computer for its hardware's names again."""
    asked = parse_qs(query, keep_blank_values=True).get("refresh")
    return asked is not None and asked[0] not in ("0", "false")


# Every route of the server, the first that fits answers. A request needs the token, unless the
# route is ``public``; tests/test_editor_access.py goes through this list.
ROUTES: tuple[Route, ...] = (
    # -- the app: its page, modules, fonts, styles; the files of plugins' pages -----------------
    Route("GET", "/", lambda h, m, u: h._app_file("index.html"), public=True),
    Route("GET", "/index.html", lambda h, m, u: h._app_file("index.html"), public=True),
    # a bookmark of the old editor leads to the app
    Route("GET", "/classic", lambda h, m, u: h._redirect("/"), public=True),
    Route("GET", "/classic/", lambda h, m, u: h._redirect("/"), public=True),
    Route("GET", "/classic/index.html", lambda h, m, u: h._redirect("/"), public=True),
    Route("GET", "/app/fonts/{name*}", lambda h, m, u: h._font(m["name"]), public=True),
    Route("GET", "/app/{name*}", lambda h, m, u: h._app_file(m["name"]), public=True),
    Route("GET", "/static/{name*}", lambda h, m, u: h._static(m["name"]), public=True),
    Route(
        "GET",
        "/plugins/{page}/{name*}",
        lambda h, m, u: h._page_file(m["page"], m["name"] or "index.html"),
        public=True,
    ),
    # -- reading ------------------------------------------------------------------------------
    Route("GET", "/api/specs", lambda h, m, u: h._json(h._specs())),
    Route("GET", "/api/themes", lambda h, m, u: h._json(_theme_list())),
    Route("GET", "/api/themes/{theme}/assets", lambda h, m, u: h._assets(m["theme"])),
    Route(
        "GET",
        "/api/themes/{theme}/assets/{rel+}",
        lambda h, m, u: h._asset_file(m["theme"], unquote(m["rel"])),
        picture=True,
    ),
    Route(
        "GET",
        "/api/themes/{theme}/preview",
        lambda h, m, u: h._theme_preview(m["theme"], parse_qs(u.query)),
        picture=True,
    ),
    Route("GET", "/api/readings", lambda h, m, u: h._json(h._readings(parse_qs(u.query)))),
    Route("GET", "/api/pages", lambda h, m, u: h._panel_pages()),
    Route("GET", "/api/sequence", lambda h, m, u: h._sequence()),
    *(
        Route("GET", path, lambda h, m, u, name=name: h._settings(name))
        for path, name in _SETTINGS_GET.items()
    ),
    Route("GET", "/api/themes/{theme+}", lambda h, m, u: h._get_theme(m["theme"])),
    Route("GET", "/api/sensors", lambda h, m, u: h._json(h._sensors())),
    Route("GET", "/api/i18n", lambda h, m, u: h._json(h._i18n())),
    Route("GET", "/api/app", lambda h, m, u: h._app_state()),
    Route("GET", "/api/hardware", lambda h, m, u: h._hardware(_refresh_asked(u.query))),
    Route("GET", "/api/fan-look", lambda h, m, u: h._fan_look(parse_qs(u.query)), picture=True),
    Route("GET", "/api/plugins/pages", lambda h, m, u: h._json(h._pages())),
    Route(
        "GET",
        "/api/plugins/{page}/{rest*}",
        lambda h, m, u: h._page_api("GET", m["page"], m["rest"], parse_qs(u.query), None),
        picture=True,  # a page may answer with a picture
    ),
    # -- writing ------------------------------------------------------------------------------
    Route("POST", "/api/render", lambda h, m, u: h._render()),
    Route("POST", "/api/adapt", lambda h, m, u: h._adapt()),
    Route("POST", "/api/module-previews", lambda h, m, u: h._module_previews()),
    Route("POST", "/api/templates", lambda h, m, u: h._templates()),
    Route("POST", "/api/detach", lambda h, m, u: h._detach()),
    Route("POST", "/api/app", lambda h, m, u: h._app_action()),
    Route("POST", "/api/language", lambda h, m, u: h._set_language()),
    Route("POST", "/api/pages", lambda h, m, u: h._set_panel_page()),
    Route("POST", "/api/sequence", lambda h, m, u: h._set_sequence()),
    *(
        Route("POST", path, lambda h, m, u, name=name: h._settings(name, body=True))
        for path, name in _SETTINGS_POST.items()
    ),
    Route("POST", "/api/preset-previews", lambda h, m, u: h._preset_previews()),
    Route("POST", "/api/hardware", lambda h, m, u: h._set_hardware()),
    Route(
        "POST",
        "/api/plugins/{page}/{rest*}",
        lambda h, m, u: h._post_page(m["page"], m["rest"], parse_qs(u.query)),
    ),
    Route(
        "POST",
        "/api/themes/{theme}/assets",
        lambda h, m, u: h._upload_asset(m["theme"], parse_qs(u.query)),
    ),
    Route("POST", "/api/themes/{theme*}", lambda h, m, u: h._save(m["theme"])),
)


def hold_port(sock: Any, platform: str = sys.platform) -> bool:
    """On Windows, keep the port to this server alone: there SO_REUSEADDR (Python's
    default for HTTP servers) lets another program bind it too and take requests, and
    the token in them. Returns whether the address may be reused (elsewhere it may)."""
    if platform != "win32":
        return True
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    return False


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[EditorHandler]) -> None:
        super().__init__(address, handler)
        self.token = handler.token  # what a request must carry (access.py)

    def server_bind(self) -> None:
        self.allow_reuse_address = hold_port(self.socket)
        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A browser that closes a tab mid-request is normal, not an error.
        import sys

        if isinstance(sys.exc_info()[1], ConnectionError):
            log.debug("editor: client went away")
            return
        log.exception("editor: request failed")


def make_server(
    port: int = 8765, config_path: Path | None = None, controls: Any = None, token: str = ""
) -> EditorServer:
    """The editor's server, not yet serving. Its ``token`` is new, unless given (access.py)."""
    state = EditorState(config_path, controls)
    handler = type(
        "Handler",
        (EditorHandler,),
        {
            "state": state,
            "allowed_hosts": set(),
            "controls": controls,
            "token": token or access.new_token(),
        },
    )
    server = EditorServer(("127.0.0.1", port), handler)
    real_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    server.editor_state = state  # type: ignore[attr-defined]
    return server


def serve(
    port: int = 8765,
    open_browser: bool = True,
    config_path: Path | None = None,
    browser: bool = False,
    view: str = "design",
) -> None:
    """Serve the app until Ctrl+C, or until its window is closed: it opens in a window of
    its own when pywebview can show one (``browser``: in the browser anyway)."""
    from libre_panel import window

    try:
        server = make_server(port, config_path)
    except OSError:  # port taken: any free port will do
        server = make_server(0, config_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    # the address with the token: the way into the app for whoever started it (access.py)
    print(
        f"Libre Panel theme editor running at {access.with_token(url, server.token)}"
        "  (Ctrl+C to stop)"
    )
    backend = None if browser or not open_browser else window.load_backend()
    if backend is not None:
        thread = threading.Thread(target=server.serve_forever, name="editor", daemon=True)
        thread.start()
        try:
            window.run_window(url, view, webview=backend, token=server.token)
        finally:
            server.shutdown()
            server.server_close()
            server.editor_state.close()  # type: ignore[attr-defined]
        return
    if open_browser:
        webbrowser.open(access.with_token(window.view_url(url, view), server.token))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.editor_state.close()  # type: ignore[attr-defined]

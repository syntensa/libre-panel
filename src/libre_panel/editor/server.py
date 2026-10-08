"""Local web server for the visual theme editor.

Binds to 127.0.0.1 only. Because any website the user visits could try to
talk to a local port, requests must carry the expected Host header and
writes need a custom header that browsers never send cross-origin without a
CORS preflight (which this server does not grant).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from libre_panel import __version__, i18n
from libre_panel.config import (
    ConfigError,
    load_config,
    set_active_theme,
    set_config_value,
    user_themes_dir,
)
from libre_panel.devices.models import MODELS, PROTOCOLS
from libre_panel.editor.presets import presets_for_editor
from libre_panel.fonts import BUILTIN_PREFIX, DEFAULT_FONT, builtin_fonts
from libre_panel.icons import ICON_NAMES
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import SensorHub, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import (
    COMMON_FIELDS,
    EFFECT_FIELDS,
    TOAST_ANCHORS,
    WIDGET_SPECS,
    ThemeError,
    find_theme,
    list_themes,
    load_theme,
    parse_theme,
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

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_JSON = 2 * 1024 * 1024
MAX_ASSET = 10 * 1024 * 1024
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ttf", ".otf"}
# Parts of the language catalog the editor needs.
_EDITOR_TABLES = ("messages", "fields", "widgets", "enums", "icons")
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
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


def _plugin_specs() -> dict[str, Any]:
    """Installed plugin widget types and screens, for the inspector and the building blocks."""
    from libre_panel.plugins.loader import registry
    from libre_panel.plugins.render import label_for

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

    def close(self) -> None:
        with self._lock:
            if self._hub is not None:
                self._hub.close()


class EditorHandler(BaseHTTPRequestHandler):
    server_version = f"LibrePanelEditor/{__version__}"
    state: EditorState
    allowed_hosts: set[str]
    # The running background app (tray or `start`); None for a plain `libre-panel editor`.
    controls: Any = None

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("editor: " + fmt, *args)

    # -- helpers -----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        if self.headers.get("Host", "") not in self.allowed_hosts:
            self._error("forbidden host", HTTPStatus.FORBIDDEN)
            return False
        return True

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

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path == "/api/specs":
            return self._json(self._specs())
        if path == "/api/themes":
            return self._json(list_themes())
        match = re.fullmatch(r"/api/themes/([^/]+)/assets", path)
        if match:
            return self._assets(match.group(1))
        if path.startswith("/api/themes/"):
            return self._get_theme(path[len("/api/themes/") :])
        if path == "/api/sensors":
            return self._json(self._sensors())
        if path == "/api/active":
            try:
                return self._json({"theme": load_config(self.state.config_path).theme})
            except ConfigError as exc:
                return self._error(str(exc))
        if path == "/api/i18n":
            return self._json(self._i18n())
        if path == "/api/app":
            if self.controls is None:
                return self._json({"available": False})
            return self._json({"available": True, **self.controls.snapshot()})
        if path == "/api/plugins/pages":
            return self._json(self._pages())
        match = re.fullmatch(r"/plugins/([^/]+)/(.*)", path)
        if match:
            return self._page_file(match.group(1), match.group(2) or "index.html")
        match = re.fullmatch(r"/api/plugins/([^/]+)/(.*)", path)
        if match:
            query = parse_qs(urlparse(self.path).query)
            return self._page_api("GET", match.group(1), match.group(2), query, None)
        self._error("not found", HTTPStatus.NOT_FOUND)

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
            status, data = pages[page_id].handle(method, path, query, body)
            payload = json.dumps(data).encode("utf-8")
        except Exception as exc:  # a broken page must not take the editor down
            log.exception("editor page %s failed", page_id)
            return self._error(f"{page_id}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
        self._send(int(status), payload, "application/json")

    def _static(self, name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
            return self._error("not found", HTTPStatus.NOT_FOUND)
        file = STATIC_DIR / name
        if not file.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        content_type = _CONTENT_TYPES.get(file.suffix, "application/octet-stream")
        self._send(200, file.read_bytes(), content_type)

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
        models = [m.to_dict() for m in MODELS]
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
            "models": models,
            "protocols": PROTOCOLS,
            "fonts": [BUILTIN_PREFIX + name for name in builtin_fonts()],
            "default_font": DEFAULT_FONT,
            "icons": list(ICON_NAMES),
            "modules": module_info(),
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

    def _sensors(self) -> list[dict[str, str]]:
        readings = dict(demo_snapshot().readings)
        try:
            readings.update(self.state.live_snapshot().readings)
        except Exception as exc:  # live sensors are optional for the editor
            log.debug("live sensors unavailable: %s", exc)
        return [{"key": k, "unit": r.unit, "label": r.label} for k, r in sorted(readings.items())]

    # -- POST --------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return
        if self.headers.get("X-Libre-Panel") != "1":
            return self._error("missing X-Libre-Panel header", HTTPStatus.FORBIDDEN)
        url = urlparse(self.path)
        path = url.path
        if path == "/api/render":
            return self._render()
        if path == "/api/adapt":
            return self._adapt()
        if path == "/api/module-previews":
            return self._module_previews()
        if path == "/api/templates":
            return self._templates()
        if path == "/api/detach":
            return self._detach()
        if path == "/api/activate":
            return self._activate()
        if path == "/api/app":
            return self._app_action()
        if path == "/api/language":
            return self._set_language()
        match = re.fullmatch(r"/api/plugins/([^/]+)/(.*)", path)
        if match:
            body = self._read_json()
            if body is None:
                return None
            query = parse_qs(url.query)
            return self._page_api("POST", match.group(1), match.group(2), query, body)
        match = re.fullmatch(r"/api/themes/([^/]+)/assets", path)
        if match:
            return self._upload_asset(match.group(1), parse_qs(url.query))
        if path.startswith("/api/themes/"):
            return self._save(path[len("/api/themes/") :])
        self._error("not found", HTTPStatus.NOT_FOUND)

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
        try:
            frame, boxes = renderer.render(snapshot)
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
            frame, _boxes = renderer.render(snapshot)
            x, y, w, h = renderer.module_boxes["m"]
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

    def _activate(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        theme_id = payload.get("id")
        try:
            find_theme(theme_id if isinstance(theme_id, str) else "")
            path = set_active_theme(theme_id, self.state.config_path)
        except (ThemeError, ConfigError, OSError) as exc:
            return self._error(str(exc))
        self._json({"ok": True, "theme": theme_id, "config": str(path)})

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


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A browser that closes a tab mid-request is normal, not an error.
        import sys

        if isinstance(sys.exc_info()[1], ConnectionError):
            log.debug("editor: client went away")
            return
        log.exception("editor: request failed")


def make_server(
    port: int = 8765, config_path: Path | None = None, controls: Any = None
) -> ThreadingHTTPServer:
    state = EditorState(config_path, controls)
    handler = type(
        "Handler",
        (EditorHandler,),
        {"state": state, "allowed_hosts": set(), "controls": controls},
    )
    server = EditorServer(("127.0.0.1", port), handler)
    real_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    server.editor_state = state  # type: ignore[attr-defined]
    return server


def serve(port: int = 8765, open_browser: bool = True, config_path: Path | None = None) -> None:
    try:
        server = make_server(port, config_path)
    except OSError:  # port taken: any free port will do
        server = make_server(0, config_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Libre Panel theme editor running at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.editor_state.close()  # type: ignore[attr-defined]

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

from libre_panel import __version__
from libre_panel.config import ConfigError, load_config, set_active_theme, user_themes_dir
from libre_panel.devices.models import MODELS, PROTOCOLS
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import SensorHub
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import (
    WIDGET_SPECS,
    ThemeError,
    find_theme,
    list_themes,
    load_theme,
    parse_theme,
    save_theme,
    valid_theme_name,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_JSON = 2 * 1024 * 1024
MAX_ASSET = 10 * 1024 * 1024
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ttf", ".otf"}
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
}


class EditorState:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path
        self._hub: SensorHub | None = None
        self._lock = threading.Lock()

    def live_snapshot(self):
        from libre_panel.app import build_hub

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

    def _error(self, message: str, status: int = 400) -> None:
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
        if path.startswith("/api/themes/"):
            return self._get_theme(path[len("/api/themes/") :])
        if path == "/api/sensors":
            return self._json(self._sensors())
        if path == "/api/active":
            try:
                return self._json({"theme": load_config(self.state.config_path).theme})
            except ConfigError as exc:
                return self._error(str(exc))
        self._error("not found", HTTPStatus.NOT_FOUND)

    def _static(self, name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
            return self._error("not found", HTTPStatus.NOT_FOUND)
        file = STATIC_DIR / name
        if not file.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        content_type = _CONTENT_TYPES.get(file.suffix, "application/octet-stream")
        self._send(200, file.read_bytes(), content_type + "; charset=utf-8")

    def _specs(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "widgets": {
                t: {k: list(v) for k, v in spec.items()} for t, spec in WIDGET_SPECS.items()
            },
            "models": [m.to_dict() for m in MODELS],
            "protocols": PROTOCOLS,
        }

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
        if path == "/api/activate":
            return self._activate()
        match = re.fullmatch(r"/api/themes/([^/]+)/assets", path)
        if match:
            return self._upload_asset(match.group(1), parse_qs(url.query))
        if path.startswith("/api/themes/"):
            return self._save(path[len("/api/themes/") :])
        self._error("not found", HTTPStatus.NOT_FOUND)

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
        renderer = Renderer(theme)
        frame, boxes = renderer.render(snapshot)
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG")
        self._json(
            {
                "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "boxes": boxes,
                "width": theme.width,
                "height": theme.height,
                "warnings": theme.warnings + renderer.warnings,
            }
        )

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


def make_server(port: int = 8765, config_path: Path | None = None) -> ThreadingHTTPServer:
    state = EditorState(config_path)
    handler = type("Handler", (EditorHandler,), {"state": state, "allowed_hosts": set()})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    real_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    server.editor_state = state  # type: ignore[attr-defined]
    return server


def serve(port: int = 8765, open_browser: bool = True, config_path: Path | None = None) -> None:
    server = make_server(port, config_path)
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

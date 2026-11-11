"""The plugin host: the Python plugins of Libre Panel for the Rust app (docs/RUST.md, R10).

Plugins are Python packages (docs/PLUGINS.md). The Rust app runs them here, in a process of
their own that it starts only when plugins are installed, and talks to it over this program's
standard input and output: each message is a length (4 bytes, little-endian) and that many
bytes of JSON, and where the JSON has ``"blob": n``, n bytes of data after it (a picture's
pixels). A request has an ``id`` and gets an answer with the same ``id``; what plugins do on
their own (a service publishing a reading, a toast) goes out as a message with ``event``.

``python -m libre_panel.plugin_host --folder <settings>/plugins``; the downloads carry it as
the program libre-panel-plugins.
"""

from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, BinaryIO

log = logging.getLogger("libre_panel.plugin_host")


class Channel:
    """The messages to and from the Rust app."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self.reader, self.writer = reader, writer
        self._lock = threading.Lock()

    def receive(self) -> tuple[dict[str, Any], bytes] | None:
        head = self._exactly(4)
        if head is None:
            return None
        (size,) = struct.unpack("<I", head)
        body = self._exactly(size)
        if body is None:
            return None
        message = json.loads(body)
        blob = b""
        if message.get("blob"):
            blob = self._exactly(int(message["blob"])) or b""
        return message, blob

    def send(self, message: dict[str, Any], blob: bytes = b"") -> None:
        if blob:
            message = {**message, "blob": len(blob)}
        body = json.dumps(message, ensure_ascii=False, allow_nan=False, default=str).encode()
        with self._lock:
            self.writer.write(struct.pack("<I", len(body)) + body + blob)
            self.writer.flush()

    def _exactly(self, size: int) -> bytes | None:
        data = b""
        while len(data) < size:
            part = self.reader.read(size - len(data))
            if not part:
                return None
            data += part
        return data


def _source(found: Any) -> str:
    return found.source


class Host:
    """What the Rust app asks of the plugins."""

    def __init__(self, channel: Channel, folder: Path | None) -> None:
        from libre_panel.plugins import loader
        from libre_panel.plugins.loader import BUILT_IN, discover

        self.channel = channel
        self.registry = discover(folder)
        # (Libre Panel's own parts are the Rust app's)
        for kind in list(self.registry.parts):
            parts = self.registry.parts[kind]
            for name in [n for n, found in parts.items() if found.source == BUILT_IN]:
                del parts[name]
        loader._registry = self.registry  # (what the renderer and the sources look up)
        self.ops = {"hello": self.hello, "catalog": self.catalog, "check": self.check,
                    "config": self.follow_config,
                    "sensors.open": self.sensor_open, "sensors.read": self.sensor_read,
                    "sensors.images": self.sensor_images, "sensors.close": self.sensor_close,
                    "services.apply": self.services_apply, "services.summary": self.summary,
                    "services.restart": self.services_restart, "emit": self.emit,
                    "services.stop": self.services_stop, "schemas": self.schemas,
                    "draw.open": self.draw_open, "draw.widget": self.draw_widget,
                    "draw.screen": self.draw_screen, "draw.close": self.draw_close,
                    "transition.open": self.transition_open,
                    "transition.frame": self.transition_frame,
                    "transition.switch": self.transition_switch,
                    "transition.close": self.transition_close,
                    "toast.open": self.toast_open, "toast.draw": self.toast_draw,
                    "toast.lifetime": self.toast_lifetime, "toast.hold": self.toast_hold,
                    "toast.rebind": self.toast_rebind, "toast.close": self.toast_close,
                    "pages.list": self.pages_list, "page.handle": self.page_handle}  # fmt: skip
        self.pages: dict[str, Any] = {}
        self.styles: dict[int, Any] = {}
        self.transitions: dict[int, list[Any]] = {}
        self.drawings: dict[int, _Drawing] = {}
        self._drawing_ids = iter(range(1, 1 << 62))
        self.config: Any = None
        self.sources: dict[str, Any] = {}
        self._calls: dict[int, tuple[threading.Event, list[Any]]] = {}
        self._call_ids = iter(range(1, 1 << 62))
        self._calls_lock = threading.Lock()
        self.bridge: Bridge | None = None
        self.services: Any = None

    # -- the messages ----------------------------------------------------------------------

    def event(self, what: str, blob: bytes = b"", /, **fields: Any) -> None:
        """Something a plugin did, told to the Rust app."""
        self.channel.send({**fields, "event": what}, blob)

    def call(self, what: str, timeout: float, default: Any, /, **fields: Any) -> Any:
        """Something a plugin asks of the Rust app (a toast's delivery), its answer; ``default``
        where none comes in ``timeout`` seconds."""
        with self._calls_lock:
            ident = next(self._call_ids)
            waiting = (threading.Event(), [default])
            self._calls[ident] = waiting
        self.channel.send({**fields, "event": what, "call": ident})
        waiting[0].wait(timeout)
        with self._calls_lock:
            self._calls.pop(ident, None)
        return waiting[1][0]

    def serve(self) -> None:
        """Requests are done one after another on a thread of their own, so that a plugin that
        asks the Rust app something (a toast's delivery) while one is done hears its answer."""
        import queue

        requests: queue.Queue[tuple[dict[str, Any], bytes] | None] = queue.Queue()
        worker = threading.Thread(target=self._work, args=(requests,), name="plugin-requests",
                                  daemon=True)  # fmt: skip
        worker.start()
        while (received := self.channel.receive()) is not None:
            message, blob = received
            if "answer" in message:
                with self._calls_lock:
                    waiting = self._calls.get(message["answer"])
                if waiting is not None:
                    waiting[1][0] = message.get("value")
                    waiting[0].set()
                continue
            requests.put((message, blob))
        requests.put(None)
        worker.join(10)
        self.services_stop({}, b"")
        for source in list(self.sources.values()):
            _close(source)

    def _work(self, requests: Any) -> None:
        while (request := requests.get()) is not None:
            message, blob = request
            op = self.ops.get(message.get("op", ""))
            answer: dict[str, Any] = {"id": message.get("id")}
            out = b""
            if op is None:
                answer["error"] = f"no such request: {message.get('op')!r}"
            else:
                try:
                    told = op(message, blob)
                    if isinstance(told, tuple):
                        told, out = told
                    answer.update(told or {})
                except Exception as exc:  # a broken plugin must not end the host
                    log.error("plugin request %s failed:\n%s", message.get("op"),
                              traceback.format_exc())  # fmt: skip
                    answer["error"] = f"{type(exc).__name__}: {exc}"
            self.channel.send(answer, out)

    # -- what is installed ---------------------------------------------------------------

    def hello(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The plugin API, the parts found (not loaded), what was held back and the errors;
        a theme package's folder (loading one runs no plugin code)."""
        from libre_panel.plugins.loader import API_VERSION

        parts = []
        for found in self.registry.all():
            part = {"kind": found.kind, "name": found.name, "source": _source(found)}
            if found.kind == "themes":
                folder = self.registry.get("themes", found.name)
                part["folder"] = str(folder) if folder is not None else None
                part["error"] = found.error
            parts.append(part)
        return {"api": API_VERSION, "parts": parts, "errors": list(self.registry.errors),
                "held_back": list(self.registry.held_back)}  # fmt: skip

    def check(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """Each part loaded, as `libre-panel plugins` shows it: "" or why it failed."""
        parts = []
        for found in self.registry.all():
            self.registry.get(found.kind, found.name)
            parts.append({"kind": found.kind, "name": found.name, "source": _source(found),
                          "error": found.error})  # fmt: skip
        return {"parts": parts, "errors": list(self.registry.errors)}

    def catalog(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """What Settings lists of the plugins (editor/settings.py's _catalog): each service with
        where it came from, why it does not load and the options it declares; each sensor
        source with where it came from."""
        from libre_panel.editor.settings import _declared

        services = []
        for name, found in self.registry.parts.get("services", {}).items():
            cls = self.registry.get("services", name)
            services.append({"name": name, "source": found.source,
                             "error": found.error if cls is None else "",
                             "options": _declared(cls)})  # fmt: skip
        found_sensors = sorted(self.registry.parts.get("sensors", {}).items())
        sensors = [{"name": name, "source": found.source} for name, found in found_sensors]
        return {"services": services, "sensors": sensors}

    # -- config.toml ---------------------------------------------------------------------

    def follow_config(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """config.toml was read (``path``): the sources take what they follow of it (and the
        language is the app's)."""
        from libre_panel import i18n
        from libre_panel.config import load_config

        self.config = load_config(Path(message["path"]))
        if getattr(self.config, "language", ""):
            i18n.set_language(self.config.language)
        for source in list(self.sources.values()):
            source.follow_config(self.config)
        return {}

    # -- sensor sources ------------------------------------------------------------------

    def sensor_open(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The plugin's sensor source ``name`` made with its ``options``; how often it is read."""
        from libre_panel.sensors.base import create_provider

        name = message["name"]
        source = create_provider(name, message.get("options") or {})
        if self.config is not None:
            source.follow_config(self.config)
        _close(self.sources.pop(name, None))
        self.sources[name] = source
        every = source.every_frame
        return {"every_frame": every if isinstance(every, str) else bool(every),
                "overrides": list(source.overrides)}  # fmt: skip

    def sensor_read(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The readings of now, with the keys the themes on show read (``wanted``), and which
        pictures the source holds (a number that changes with them)."""
        from libre_panel.sensors.base import want

        want(_WANTS, frozenset(message.get("wanted") or ()))
        source = self.sources[message["name"]]
        readings = [_reading(reading) for reading in source.read().values()]
        pictures = source.images()
        held = hash(tuple((key, id(image)) for key, image in pictures.items())) if pictures else 0
        return {"readings": readings, "images": held & ((1 << 53) - 1)}

    def sensor_images(self, message: dict[str, Any], blob: bytes) -> tuple[dict, bytes]:
        """The pictures the source holds, as RGBA pixels one after another."""
        pictures = self.sources[message["name"]].images()
        return _pictures(pictures)

    def sensor_close(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        _close(self.sources.pop(message["name"], None))
        return {}

    # -- services ------------------------------------------------------------------------

    def services_apply(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The plugin services config.toml enables (``enabled``, ``options``) started, stopped
        or restarted as ServiceManager does it; each one's state."""
        from types import SimpleNamespace

        from libre_panel.plugins.host import ServiceManager

        if self.bridge is None:
            self.bridge = Bridge(self)
            self.services = ServiceManager(self.bridge, self.registry)
        enabled = [name for name in message.get("enabled", [])
                   if name in self.registry.parts.get("services", {})]  # fmt: skip
        options = message.get("options") or {}
        config = SimpleNamespace(services=SimpleNamespace(enabled=enabled, options=options))
        self.services.apply(config)
        return {"state": dict(self.services.state)}

    def services_restart(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        state = self.services.restart(message["name"]) if self.services else "not installed"
        return {"state": state}

    def services_stop(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        if self.services is not None:
            self.services.stop()
            self.services = None
            if self.bridge is not None:
                self.bridge.close()
            self.bridge = None
        return {}

    def summary(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """What a running service says it is doing (its card), or None."""
        running = self.services.running.get(message["name"]) if self.services else None
        return {"summary": running[0].summary() if running else None}

    def emit(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """An event of the app (``event``, ``data``) told to the services that hear it; a
        sample's and a change's ``snapshot`` as the app has it, and the mode on now."""
        if self.bridge is None:
            return {}
        data = dict(message.get("data") or {})
        if "mode" in message:
            self.bridge.mode_now = message["mode"]
        if "snapshot" in data:
            data["snapshot"] = _snapshot(data["snapshot"])
            self.bridge.latest = data["snapshot"]
        if "keys" in data:
            data["keys"] = frozenset(data["keys"])
        self.bridge.emit(message["event"], wait=bool(message.get("wait")), **data)
        return {}

    # -- what plugins draw ----------------------------------------------------------------

    def schemas(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The fields of the widget types (``spec``), screens and toast styles (``options``)
        as [kind, name, [[field, kind, default], ...]]: what themes are checked against."""
        parts = []
        for kind, attribute in (("widgets", "spec"), ("screens", "options"),
                                ("toasts", "options")):  # fmt: skip
            for name in self.registry.names(kind):
                cls = self.registry.get(kind, name)
                fields = getattr(cls, attribute, None) if cls is not None else None
                if isinstance(fields, dict):
                    told = [[key, of, default] for key, (of, default) in fields.items()]
                    parts.append([kind, name, told])
        return {"parts": parts}

    def draw_open(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """A renderer for a theme of the Rust app's (its size, palette, font, model, folder and
        screen as the app has it, dressed): the plugins' widget types and the screen are drawn
        with it, as the Python app's renderer draws them."""
        from libre_panel.render.renderer import Renderer
        from libre_panel.theme.model import DEFAULT_FONT, Theme

        told = message["theme"]
        theme = Theme(name=told.get("name", ""), width=int(told["width"]),
                      height=int(told["height"]), widgets=[],
                      model=told.get("model") or "custom", palette=dict(told.get("palette") or {}),
                      font=told.get("font") or DEFAULT_FONT, screen=told.get("screen"),
                      root=Path(told["root"]) if told.get("root") else None)  # fmt: skip
        renderer = Renderer(theme, animate=bool(message.get("animate")),
                            preview=bool(message.get("preview")))  # fmt: skip
        ident = next(self._drawing_ids)
        self.drawings[ident] = _Drawing(renderer)
        return {"drawing": ident, "screen": renderer.screen is not None,
                "warnings": _warnings(renderer)}  # fmt: skip

    def draw_widget(self, message: dict[str, Any], blob: bytes) -> tuple[dict, bytes]:
        """A plugin widget type's piece of ``widget`` (renderer._draw_plugin, its effects
        applied); ``same`` where it is the piece it gave last for that widget."""
        drawing = self.drawings[message["drawing"]]
        renderer, snapshot, now = drawing.begin(message, blob)
        widget = message["widget"]
        piece = renderer._draw_plugin(widget, snapshot, now)
        told: dict[str, Any] = {"moving": renderer.moving, "warnings": _warnings(renderer)}
        if piece is None:
            drawing.last.pop(widget["id"], None)
            return {**told, "piece": None}, b""
        if drawing.last.get(widget["id"]) is piece:
            return {**told, "same": True}, b""
        drawing.last[widget["id"]] = piece
        return _piece(piece, told)

    def draw_screen(self, message: dict[str, Any], blob: bytes) -> tuple[dict, bytes]:
        """The screen's picture of this frame (renderer._screen_image), RGB or RGBA."""
        drawing = self.drawings[message["drawing"]]
        renderer, snapshot, now = drawing.begin(message, blob)
        image = renderer._screen_image(snapshot, now) if renderer.screen is not None else None
        told: dict[str, Any] = {"moving": renderer.moving, "warnings": _warnings(renderer)}
        if image is None:
            return {**told, "image": None}, b""
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        if drawing.screen is image:
            return {**told, "same": True}, b""
        drawing.screen = image
        return {**told, "image": {"mode": image.mode, "size": list(image.size)}}, image.tobytes()

    def transition_open(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """A plugin's transition ``name`` with ``params`` (overlays.transition), its context the
        drawing's renderer (the new theme's); what it says of itself."""
        from libre_panel.plugins.render import RenderContext

        renderer = self.drawings[message["drawing"]].renderer
        cls = self.registry.get("transitions", message["name"])
        if cls is None:
            raise LookupError(f"no plugin has the transition {message['name']!r}")
        kind = cls(RenderContext(renderer, cls), dict(message.get("params") or {}))
        ident = next(self._drawing_ids)
        self.transitions[ident] = [kind, None]
        return {"transition": ident, "duration": float(kind.duration),
                "switch": str(getattr(kind, "switch", "wait")),
                "toasts": str(getattr(kind, "toasts", "wait")),
                "from_shown": bool(getattr(kind, "from_shown", False))}  # fmt: skip

    def transition_frame(self, message: dict[str, Any], blob: bytes) -> tuple[dict, bytes]:
        """frame(old, new, t): the old frame where it is sent (``old``), then the new one, RGB
        in ``size``; the frame it shows, RGB."""
        from PIL import Image

        held = self.transitions[message["transition"]]
        size = tuple(message["size"])
        length = size[0] * size[1] * 3
        if message.get("old"):
            held[1] = Image.frombytes("RGB", size, blob[:length])
            blob = blob[length:]
        new = Image.frombytes("RGB", size, blob[:length])
        out = held[0].frame(held[1], new, float(message["t"]))
        if out.mode != "RGB":
            out = out.convert("RGB")
        return {"size": list(out.size)}, out.tobytes()

    def transition_switch(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """on_switch(theme, mode, mode_changed): what a switch does while it plays."""
        kind = self.transitions[message["transition"]][0]
        said = kind.on_switch(message.get("theme") or "", message.get("mode"),
                              bool(message.get("mode_changed")))  # fmt: skip
        return {"policy": str(said)}

    def transition_close(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        self.transitions.pop(message["transition"], None)
        return {}

    def toast_open(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """A plugin's toast style ``name`` with the theme's ``options`` (ToastLayer's), its
        context the drawing's renderer; how long a toast takes to leave."""
        from libre_panel.plugins.render import RenderContext

        renderer = self.drawings[message["drawing"]].renderer
        cls = self.registry.get("toasts", message["name"])
        if cls is None:
            raise LookupError(f"no plugin has the toast style {message['name']!r}")
        style = cls(RenderContext(renderer, cls), dict(message.get("options") or {}))
        style.anchor = message.get("anchor") or "top-right"
        ident = next(self._drawing_ids)
        self.styles[ident] = style
        return {"style": ident, "seconds": float(getattr(style, "leave_s", 0.25))}

    def toast_draw(self, message: dict[str, Any], blob: bytes) -> tuple[dict, bytes]:
        """draw(frame, toast, age, previous): the frame (RGB, ``size``) with the toast on it."""
        from PIL import Image

        style = self.styles[message["style"]]
        frame = Image.frombytes("RGB", tuple(message["size"]), blob)
        toast, previous = _toasts(message)
        age = float(message["age"])
        if _takes_previous(style):
            out = style.draw(frame, toast, age, previous=previous)
        else:  # (styles written before ``previous`` existed)
            out = style.draw(frame, toast, age)
        if out.mode != "RGB":
            out = out.convert("RGB")
        return {"size": list(out.size)}, out.tobytes()

    def toast_lifetime(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        toast, previous = _toasts(message)
        return {"seconds": float(self.styles[message["style"]].lifetime(toast, previous))}

    def toast_hold(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        toast, _ = _toasts(message)
        style = self.styles[message["style"]]
        return {"seconds": float(style.hold_s(toast, float(message["theme_seconds"])))}

    def toast_rebind(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The style goes on with a new theme: its context becomes that theme's."""
        from libre_panel.plugins.render import RenderContext

        style = self.styles[message["style"]]
        drawing = self.drawings.get(message["drawing"])
        if drawing is not None:
            style.context = RenderContext(drawing.renderer, type(style))
        style.anchor = message.get("anchor") or style.anchor
        return {}

    def toast_close(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        style = self.styles.pop(message["style"], None)
        if style is not None:
            style.close()
        return {}

    # -- the plugins' pages in the app ------------------------------------------------------

    def pages_list(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        """The plugins' editor pages that have their files: each ``{id, titles, icon,
        folder}`` (titles by language, the folder their files are served from)."""
        pages = []
        for page_id in self.registry.names("editor_pages"):
            cls = self.registry.get("editor_pages", page_id)
            folder = cls.folder() if cls is not None else None
            if folder is None or not (folder / "index.html").is_file():
                continue
            pages.append({"id": page_id, "titles": dict(getattr(cls, "title", {}) or {}),
                          "icon": str(getattr(cls, "icon", "") or ""),
                          "folder": str(folder)})  # fmt: skip
        return {"pages": pages}

    def page_handle(self, message: dict[str, Any], blob: bytes) -> tuple[dict, bytes]:
        """A request to a plugin's page (``/api/plugins/<page>/<path>``), with what the app
        told of itself (``context``); its answer: ``status`` and ``json``, or ``status``,
        ``kind`` and the bytes; ``after``: what the page asked the app to do (follow
        config.toml, a shortcut, restart a service of the app's)."""
        from libre_panel.plugins.pages import PageContext

        page_id = message["page"]
        cls = self.registry.get("editor_pages", page_id)
        if cls is None:
            raise LookupError(f"no plugin has the page {page_id!r}")
        told = message.get("context") or {}
        controls = _PageControls(self, told) if told.get("background") else None
        context = PageContext(controls, Path(told["config_path"]) if told.get("config_path")
                              else None)  # fmt: skip
        page = self.pages.get(page_id)
        if page is None:
            page = self.pages[page_id] = cls(context)
        else:
            page.context = context
        query = {key: list(values) for key, values in (message.get("query") or {}).items()}
        status, data, *kind = page.handle(message["method"], message.get("path", ""), query,
                                          message.get("body"))  # fmt: skip
        after = controls.after if controls is not None else []
        if isinstance(data, bytes):
            return {"status": int(status), "kind": kind[0] if kind else
                    "application/octet-stream", "after": after}, data  # fmt: skip
        return {"status": int(status), "json": data, "after": after}, b""

    def draw_close(self, message: dict[str, Any], blob: bytes) -> dict[str, Any]:
        drawing = self.drawings.pop(message["drawing"], None)
        if drawing is not None:
            drawing.renderer.close()
        return {}


class _Drawing:
    """A renderer the Rust app draws plugins with, what it gave last, and the pictures of the
    readings it was sent (by key: their fingerprint and picture)."""

    def __init__(self, renderer: Any) -> None:
        self.renderer = renderer
        self.last: dict[str, Any] = {}
        self.screen: Any = None
        self.pictures: dict[str, tuple[Any, Any]] = {}
        self.snapshot: Any = None

    def begin(self, message: dict[str, Any], blob: bytes) -> tuple[Any, Any, float]:
        """The renderer as the frame has it (its rate, video mode, readings' timing), and the
        frame's snapshot with its pictures (those not sent before come in ``blob``)."""
        from PIL import Image

        renderer = self.renderer
        frame = message.get("frame") or {}
        now = float(frame.get("now", 0.0))
        renderer.fps = int(frame.get("fps") or 0)
        renderer.continuous = bool(frame.get("continuous"))
        renderer.sample = tuple(frame.get("sample") or (0.0, 1.0))
        renderer.moving = False
        renderer.warnings = []
        told = message.get("snapshot")
        if told != "same" or self.snapshot is None:
            self.snapshot = _snapshot(told if isinstance(told, dict) else {})
        snapshot = self.snapshot
        at, pictures = 0, {}
        for told in message.get("pictures") or []:
            key, mark = told["key"], told["mark"]
            if "size" in told:
                width, height = told["size"]
                size = width * height * 4
                image = Image.frombytes("RGBA", (width, height), blob[at : at + size])
                at += size
                self.pictures[key] = (mark, image)
            kept = self.pictures.get(key)
            if kept is not None and kept[0] == mark:
                pictures[key] = kept[1]
        for key in [k for k in self.pictures if k not in pictures]:
            del self.pictures[key]
        snapshot.images = pictures
        renderer.snapshot = snapshot
        return renderer, snapshot, now


class _PageServices:
    """The services as a page sees them: each enabled one's state as the app told it, the
    plugins' running here."""

    def __init__(self, host: Host, controls: _PageControls) -> None:
        self._host, self._controls = host, controls
        self.state = dict(controls.told.get("services") or {})

    @property
    def running(self) -> dict[str, Any]:
        return dict(self._host.services.running) if self._host.services is not None else {}

    def restart(self, name: str) -> str:
        if name in self.running:
            return self._host.services.restart(name)
        self._controls.after.append(["restart", name])  # (one of the app's: it restarts it)
        return self.state.get(name, "not installed")


class _PageControls:
    """The running app as a page's context reaches it in the plugin host: what the app told
    with the request, and what the page asks of it, done by the app once the page answered."""

    def __init__(self, host: Host, told: dict[str, Any]) -> None:
        self.told = told
        self.after: list[Any] = []
        self.config_path = Path(told["config_path"]) if told.get("config_path") else None
        self.services = _PageServices(host, self)
        self.plugin_host = host.bridge
        status = told.get("status")
        self.panel = type("_Panel", (), {"state": staticmethod(lambda: status)})()

    def follow_config(self) -> None:
        self.after.append(["follow_config"])

    def shortcut(self, action: str) -> None:
        self.after.append(["shortcut", action])


def _takes_previous(style: Any) -> bool:
    """Whether a toast style's draw takes ``previous`` (ToastLayer._make_style)."""
    import inspect

    try:
        parameters = inspect.signature(style.draw).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "previous" or p.kind is p.VAR_KEYWORD for p in parameters)


def _toast(told: dict[str, Any]) -> Any:
    from libre_panel.plugins.host import Toast

    return Toast(text=told.get("text", ""), icon=told.get("icon"), level=told.get("level", "info"),
                 seconds=told.get("seconds"), kind=told.get("kind", ""),
                 rank=int(told.get("rank") or 0), payload=dict(told.get("payload") or {}),
                 service=told.get("service", ""), key=told.get("key", ""))  # fmt: skip


def _toasts(message: dict[str, Any]) -> tuple[Any, Any]:
    """The toast of a request, and the one it replaced with that one's age, or None."""
    previous = message.get("previous")
    return _toast(message["toast"]), (_toast(previous[0]), float(previous[1])) if previous else None


def _warnings(renderer: Any) -> list[str]:
    said, renderer.warnings = list(renderer.warnings), []
    return said


def _piece(piece: Any, told: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """A piece as its place and layers (RGBA, then the backdrop's L mask and the light's RGBA
    where it has them), their pixels one after another."""
    layer = piece.layer if piece.layer.mode == "RGBA" else piece.layer.convert("RGBA")
    data = bytearray(layer.tobytes())
    made: dict[str, Any] = {"x": piece.x, "y": piece.y, "size": list(layer.size),
                            "box": list(piece.box) if piece.box is not None else None,
                            "add": piece.add}  # fmt: skip
    if piece.backdrop is not None:
        mask = piece.backdrop.convert("L")
        made["backdrop"] = {"size": list(mask.size), "radius": piece.backdrop_radius}
        data += mask.tobytes()
    if piece.light is not None:
        light = piece.light.layer.convert("RGBA")
        made["light"] = {"x": piece.light.x, "y": piece.light.y, "size": list(light.size),
                         "add": piece.light.add}  # fmt: skip
        data += light.tobytes()
    return {**told, "piece": made}, bytes(data)


_WANTS = type("_RustApp", (), {})()  # (who reads what the themes on show read, for want())


def _close(source: Any) -> None:
    if source is None:
        return
    try:
        source.close()
    except Exception:  # a source's own problem: it is gone either way
        log.exception("a plugin's sensor source failed while closing")


def _reading(reading: Any) -> dict[str, Any]:
    value = reading.value
    if isinstance(value, float) and value != value:  # (JSON has no NaN)
        value = None
    return {"key": reading.key, "value": value, "unit": reading.unit, "label": reading.label,
            "origin": reading.origin, "device": reading.device}  # fmt: skip


def _pictures(pictures: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Pictures as their keys and sizes, and their RGBA pixels one after another."""
    told, data = [], bytearray()
    for key, image in pictures.items():
        if image is None:
            continue
        rgba = image.convert("RGBA")
        told.append({"key": key, "size": list(rgba.size)})
        data += rgba.tobytes()
    return {"images": told}, bytes(data)


def _snapshot(told: dict[str, Any]) -> Any:
    """A snapshot as the app sent it: its readings, history and moment."""
    from datetime import datetime

    from libre_panel.sensors.base import Reading, Snapshot

    readings = {r["key"]: Reading(r["key"], r.get("value"), r.get("unit", ""),
                                  r.get("label", ""), r.get("origin", ""), r.get("stamp"),
                                  r.get("device", ""))
                for r in told.get("readings", [])}  # fmt: skip
    now = datetime.fromisoformat(told["now"]) if told.get("now") else datetime.now()
    return Snapshot(readings=readings, history=dict(told.get("history") or {}), now=now)


def _transition(spec: Any) -> Any:
    """A transition as JSON: a name, [name, parameters] or None."""
    if isinstance(spec, tuple):
        return [spec[0], dict(spec[1])]
    return spec


class _Bridge:
    """Defined below on first use (the plugin API is imported only then)."""


def _make_bridge() -> type:
    from libre_panel.plugins.host import ANY_MODE, Delivery, PluginHost

    class Bridge(PluginHost):
        """The services' PluginHost: what they do goes to the Rust app, whose main loop decides
        (the theme, the mode, whether a toast shows); what it tells them comes back here."""

        def __init__(self, host: Host) -> None:
            super().__init__()
            self.host = host
            self.mode_now: str | None = None
            self._pictures = 0

        @property
        def mode(self) -> str | None:  # (the main loop's, as it last told)
            return self.mode_now

        def publish(self, reading: Any, owner: str | None = None) -> None:
            super().publish(reading, owner)
            if reading.stamp is None:  # (a state goes as one: publish_state)
                self.host.event("publish", owner=owner, reading=_reading(reading))

        def publish_state(self, key: str, value: Any, owner: str | None = None,
                          label: str = "") -> None:  # fmt: skip
            super().publish_state(key, value, owner, label)
            self.host.event("state", owner=owner, key=key, value=value, label=label)

        def unpublish(self, key: str) -> None:
            super().unpublish(key)
            self.host.event("unpublish", key=key)

        def want(self, owner: str, keys: Any) -> None:
            self.host.event("want", owner=owner, keys=sorted(keys))

        def publish_image(self, key: str, image: Any, owner: str | None = None) -> None:
            super().publish_image(key, image, owner)
            if image is None:
                self.host.event("image", owner=owner, key=key)
                return
            told, data = _pictures({key: image})
            self.host.event("image", data, owner=owner, key=key, size=told["images"][0]["size"])

        def request_theme(self, name: str | None, by: str, transition: Any = None,
                          priority: int = 0, mode: str | None = ANY_MODE) -> None:  # fmt: skip
            self.host.event("theme", name=name, by=by, transition=_transition(transition),
                            priority=int(priority), mode=mode)  # fmt: skip

        def set_mode(self, name: str | None, by: str | None = None,
                     transition: Any = None) -> None:  # fmt: skip
            self.host.event("mode", name=name, by=by, transition=_transition(transition))

        def notify(self, toast: Any) -> Any:
            payload = {}
            for key, value in toast.payload.items():
                if hasattr(value, "convert"):  # a picture: published, and named in its place
                    self._pictures += 1
                    named = f"plugin-toast.{toast.service or 'plugin'}.{self._pictures % 8}"
                    self.publish_image(named, value, owner=toast.service or None)
                    value = named
                payload[key] = value
            told = self.host.call("notify", 5.0, None, toast={
                "text": toast.text, "icon": toast.icon, "level": toast.level,
                "seconds": toast.seconds, "kind": toast.kind, "rank": toast.rank,
                "payload": payload, "service": toast.service, "key": toast.key})  # fmt: skip
            if not isinstance(told, dict):
                return Delivery(True)
            return Delivery(bool(told.get("shown")), str(told.get("reason") or ""))

        def forget(self, owner: str) -> None:
            super().forget(owner)
            self.host.event("forget", owner=owner)

    return Bridge


def Bridge(host: Host) -> Any:  # noqa: N802 (made as a class is)
    global _Bridge
    if not isinstance(_Bridge, type) or _Bridge.__name__ != "Bridge":
        _Bridge = _make_bridge()
    return _Bridge(host)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="libre-panel-plugins")
    parser.add_argument("--folder", type=Path, default=None, help="the plugin folders' folder")
    args = parser.parse_args(argv)
    # (the messages own standard output: what plugins print goes to the log)
    channel = Channel(sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout = sys.stderr
    # (the app puts each line into its own log: "<level>\t<logger>\t<message>")
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(levelname)s\t%(name)s\t%(message)s")  # fmt: skip
    Host(channel, args.folder).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())

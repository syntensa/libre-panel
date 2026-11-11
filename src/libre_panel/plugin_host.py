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
        from libre_panel.plugins.loader import BUILT_IN, discover

        self.channel = channel
        self.registry = discover(folder)
        # (Libre Panel's own parts are the Rust app's)
        for kind in list(self.registry.parts):
            parts = self.registry.parts[kind]
            for name in [n for n, found in parts.items() if found.source == BUILT_IN]:
                del parts[name]
        self.ops = {"hello": self.hello, "check": self.check}

    def serve(self) -> None:
        while (received := self.channel.receive()) is not None:
            message, blob = received
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="libre-panel-plugins")
    parser.add_argument("--folder", type=Path, default=None, help="the plugin folders' folder")
    args = parser.parse_args(argv)
    # (the messages own standard output: what plugins print goes to the log)
    channel = Channel(sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(name)s: %(message)s")  # fmt: skip
    Host(channel, args.folder).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())

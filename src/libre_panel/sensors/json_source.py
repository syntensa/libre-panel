"""Values from JSON: the address of a NAS, a router, a Proxmox host, a second PC, or a file
another program writes. No account: what the address answers is read as it is.

``[sensors.json]``::

    update_s = 30

    [[sensors.json.sources]]
    name = "nas"
    url = "http://192.168.1.10:61208/api/4/quicklook"     # or file = "C:/Data/values.json"
    fields = { cpu = "cpu", temp = "sensors[0].value" }    # empty: every value it has
    units = { cpu = "%", temp = "°C" }
    headers = { Authorization = "Bearer ..." }             # where the address wants one

Readings: ``json.<name>.<field>``, a number or a text (true and false as 1 and 0). A field is
a path into the answer: names and list places, ``a.b[0].c`` (or ``a.b.0.c``). Without
``fields`` every value up to three levels deep is read (at most 100), its key the path with
dots. An answer that does not come keeps the last values for three times ``update_s``, then
they go. Each source is asked in a thread of its own, never on the frame's time.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from libre_panel import __version__
from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

MAX_BYTES = 1024 * 1024
MAX_SOURCES = 16
MAX_VALUES = 100
DEPTH = 3
TIMEOUT_S = 5.0
_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_STEP = re.compile(r"\[(\d+)\]")


def slug(text: str) -> str:
    return _NAME.sub("_", str(text)).strip("_")[:40] or "value"


def pick(data: Any, path: str) -> Any:
    """The value at ``path`` (``a.b[0].c``), or None where there is none."""
    for step in _STEP.sub(r".\1", path).split("."):
        if step == "":
            continue
        if isinstance(data, dict):
            data = data.get(step)
        elif isinstance(data, list) and step.isdigit() and int(step) < len(data):
            data = data[int(step)]
        else:
            return None
    return data


def scalar(value: Any) -> float | str | None:
    """A value a reading can hold: a number, a text, true and false as 1 and 0."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return value[:200]
    return None


def flatten(data: Any, prefix: str = "", depth: int = DEPTH) -> dict[str, float | str]:
    """Every value of ``data`` down to ``depth`` levels, by its path with dots."""
    found: dict[str, float | str] = {}
    items: list[tuple[str, Any]]
    if isinstance(data, dict):
        items = [(slug(key), value) for key, value in data.items()]
    elif isinstance(data, list):
        items = [(str(n), value) for n, value in enumerate(data)]
    else:
        value = scalar(data)
        return {prefix or "value": value} if value is not None else {}
    for key, value in items:
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, (dict, list)):
            if depth > 1:
                for inner, got in flatten(value, path, depth - 1).items():
                    if len(found) >= MAX_VALUES:
                        return found
                    found[inner] = got
        else:
            got = scalar(value)
            if got is not None:
                found[path] = got
        if len(found) >= MAX_VALUES:
            break
    return found


class Source:
    """One address or file of ``sources``."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.name = slug(entry.get("name") or "json")
        self.url = str(entry.get("url") or "").strip()
        self.file = str(entry.get("file") or "").strip()
        fields = entry.get("fields") or {}
        self.fields = (
            {slug(k): str(v) for k, v in fields.items()} if isinstance(fields, dict) else {}
        )
        units = entry.get("units") or {}
        self.units = {slug(k): str(v) for k, v in units.items()} if isinstance(units, dict) else {}
        headers = entry.get("headers") or {}
        self.headers = (
            {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
        )
        if self.url and not self.url.startswith(("http://", "https://")):
            raise ValueError(
                f"json source {self.name!r}: the url must start with http:// or https://"
            )
        if not self.url and not self.file:
            raise ValueError(f"json source {self.name!r}: needs a url or a file")

    def load(self) -> Any:
        if self.url:
            request = urllib.request.Request(
                self.url, headers={"User-Agent": f"LibrePanel/{__version__}", **self.headers}
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:  # noqa: S310
                text = answer.read(MAX_BYTES + 1)
        else:
            with Path(self.file).expanduser().open("rb") as file:
                text = file.read(MAX_BYTES + 1)
        if len(text) > MAX_BYTES:
            raise ValueError("the answer is larger than 1 MB")
        return json.loads(text.decode("utf-8-sig"))

    def readings(self, data: Any) -> dict[str, Reading]:
        if self.fields:
            values = {field: scalar(pick(data, path)) for field, path in self.fields.items()}
        else:
            values = dict(flatten(data))
        out = {}
        for field, value in values.items():
            if value is None:
                continue
            key = f"json.{self.name}.{field}"
            unit = self.units.get(field, "") if isinstance(value, float) else ""
            out[key] = Reading(key, value, unit, f"{self.name} {field}")
        return out


class JsonProvider(SensorProvider):
    """``json.<name>.<field>`` from addresses and files."""

    name = "json"

    def __init__(self, options: dict[str, Any] | None = None, *, start: bool = True) -> None:
        super().__init__(options)
        self.every = max(2.0, float(self.options.get("update_s", 30)))
        self.sources: list[Source] = []
        for n, entry in enumerate(list(self.options.get("sources") or [])[:MAX_SOURCES], 1):
            if not isinstance(entry, dict):
                log.warning("json: sources[%d] is no table, left out", n)
                continue
            try:
                self.sources.append(Source(entry))
            except ValueError as exc:
                log.warning("json: %s", exc)
        self._got: dict[str, tuple[float, dict[str, Reading]]] = {}  # name -> (when, readings)
        self._failed: set[str] = set()  # said once, until it answers again
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        if start:
            for source in self.sources:
                thread = threading.Thread(target=self._run, args=(source,), daemon=True,
                                          name=f"sensors-json-{source.name}")  # fmt: skip
                thread.start()
                self._threads.append(thread)

    def fetch(self, source: Source) -> None:
        """Ask ``source`` once and keep what it says."""
        try:
            readings = source.readings(source.load())
        except Exception as exc:  # noqa: BLE001 - an address that fails must not stop the panel
            if source.name not in self._failed:
                self._failed.add(source.name)
                log.warning("json: %s does not answer: %s", source.name, exc)
            return
        if source.name in self._failed:
            self._failed.discard(source.name)
            log.info("json: %s answers again", source.name)
        with self._lock:
            self._got[source.name] = (time.monotonic(), readings)

    def _run(self, source: Source) -> None:
        while not self._stop.is_set():
            self.fetch(source)
            self._stop.wait(self.every)

    def read(self) -> dict[str, Reading]:
        out: dict[str, Reading] = {}
        now = time.monotonic()
        with self._lock:
            for when, readings in self._got.values():
                if now - when <= 3 * self.every:
                    out.update(readings)
        return out

    def close(self) -> None:
        self._stop.set()

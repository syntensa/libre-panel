"""The log for the app (Live → Diagnose): its last entries, the whole of it to save, and the
detail it is written with, set without a restart.

The background app writes ``libre-panel.log`` (cli._log_file: a line per entry, "date time LEVEL
logger: message", a traceback on the lines after it, at most three files of a megabyte). What
leaves through here has the folders of the person's own taken out, as the doctor's report does:
a home folder holds a name.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from libre_panel.config import log_file
from libre_panel.doctor import without_folders

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ENTRY = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:,\d+)? "  # the time
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) ([\w.\-]+): ?(.*)$"  # level, logger: message
)
MAX_ENTRIES = 500
MAX_BYTES = 3 << 20  # all three files at most
QUIET = ("PIL", "urllib3", "asyncio", "comtypes")  # their DEBUG says nothing about the panel


def _files(path: Path | None = None) -> list[Path]:
    """The log and its older parts, the oldest first."""
    path = path or log_file()
    older = [path.with_name(f"{path.name}.{n}") for n in (2, 1)]
    return [p for p in (*older, path) if p.is_file()]


def _read(path: Path | None = None, budget: int = MAX_BYTES) -> str:
    parts: list[str] = []
    for file in reversed(_files(path)):  # the newest first, until the budget is spent
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if len(data) > budget:
            data = data[-budget:]
            data = data[data.find(b"\n") + 1 :]  # from a whole line
        parts.insert(0, data.decode("utf-8", "replace"))
        budget -= len(data)
        if budget <= 0:
            break
    return "".join(parts)


def entries(level: str = "DEBUG", limit: int = MAX_ENTRIES, path: Path | None = None,
            home: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """The last ``limit`` entries of ``level`` and worse, the oldest first: ``{time, level,
    logger, message}``, a traceback's lines in its message."""
    least = LEVELS.index(level) if level in LEVELS else 0
    found: list[dict[str, Any]] = []
    for line in without_folders(_read(path, 512 << 10), home).splitlines():
        match = _ENTRY.match(line)
        if match:
            found.append({"time": match[1], "level": match[2], "logger": match[3],
                          "message": match[4]})  # fmt: skip
        elif found and line.strip():  # a traceback, or a message of several lines
            found[-1]["message"] += "\n" + line
    return [e for e in found if LEVELS.index(e["level"]) >= least][-limit:]


def text(path: Path | None = None, home: str | None = None) -> str:
    """The whole log to save and send along, without the person's folders."""
    return without_folders(_read(path), home)


def verbose() -> bool:
    return logging.getLogger().level <= logging.DEBUG


def set_verbose(on: bool) -> None:
    """Every detail into the log from now on (as ``-v``), or only what matters; until the app
    starts again. The libraries that say much in detail and nothing of the panel stay quiet."""
    logging.getLogger().setLevel(logging.DEBUG if on else logging.INFO)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.INFO if on else logging.NOTSET)
    logging.getLogger(__name__).info("detailed log %s", "on" if on else "off")

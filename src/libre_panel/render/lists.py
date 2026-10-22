"""What a list widget shows: readings picked by name or pattern, named and sorted.

``items`` holds one entry per line::

    cpu.temp = CPU      a reading, with the name to show
    temp.*              every reading that matches, in natural order
    !temp.acpi*         none of these

A reading that repeats another under a friendlier key (``cpu.temp`` is one of
the ``temp.*`` readings) is shown once. Names come from the line, else from a
reading beside the key (``disk.1.name`` for ``disk.1.load``), else from the
sensor's label.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

from libre_panel.sensors.base import Reading

_GLOB = re.compile(r"[*?\[]")
MAX_ITEMS = 64

# The fans seen running since Libre Panel started (render.lists.used): a header of the
# mainboard that has read 0 all along has no fan on it
_RAN: set[str] = set()


@dataclass(frozen=True)
class Entry:
    pattern: str
    name: str = ""
    exclude: bool = False


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    reading: Reading
    detail: Reading | None = None

    @property
    def number(self) -> float | None:
        value = self.reading.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)


def parse_items(text: str) -> list[Entry]:
    entries = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        exclude = line.startswith("!")
        pattern, _, name = line.lstrip("!").partition("=")
        pattern = pattern.strip()
        if pattern:
            entries.append(Entry(pattern, name.strip(), exclude))
    return entries


def read_keys(text: str) -> set[str]:
    """The keys and patterns a list reads (for the providers that work on demand)."""
    return {e.pattern for e in parse_items(text) if not e.exclude}


def _natural(key: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", key)]


def _sibling(key: str, name: str) -> str:
    head, dot, _last = key.rpartition(".")
    return f"{head}.{name}" if dot else ""


def used(item: Item) -> bool:
    """Whether a fan of a list is one (``hide_unused``): it has run since Libre Panel started,
    or it is the graphics card's (which stands while the card is cool). A mainboard's header
    that has read 0 all along has nothing on it."""
    number = item.number
    if number is None or number > 0:
        _RAN.add(item.key)
        return True
    return item.key in _RAN or item.key == "gpu.fan" or item.key.startswith("fan.gpu")


def pick(widget: dict[str, Any], readings: dict[str, Reading]) -> list[Item]:
    entries = parse_items(widget.get("items", ""))
    excluded = [e.pattern for e in entries if e.exclude]
    # what a reading left out repeats is left out too ("!cpu.temp", which is temp.k10temp.tctl)
    repeated = {readings[p].origin for p in excluded if p in readings and readings[p].origin}
    detail = widget.get("detail", "")
    items: list[Item] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.exclude:
            continue
        if _GLOB.search(entry.pattern):
            keys = sorted(fnmatch.filter(readings, entry.pattern), key=_natural)
        else:
            keys = [entry.pattern] if entry.pattern in readings else []
        for key in keys:
            reading = readings[key]
            origin = reading.origin or key
            if reading.value is None or origin in seen or key in seen:
                continue
            if key in repeated or any(  # left out by its key, or by the one it repeats
                fnmatch.fnmatchcase(key, pattern) or fnmatch.fnmatchcase(origin, pattern)
                for pattern in excluded
            ):
                continue
            seen.update((origin, key))
            name = entry.name
            if not name:
                beside = readings.get(_sibling(key, "name"))
                if beside is not None and isinstance(beside.value, str) and beside.key != key:
                    name = beside.value
                else:
                    name = reading.label or key
            extra = readings.get(_sibling(key, detail)) if detail else None
            items.append(Item(key, name, reading, extra))
            if len(items) >= MAX_ITEMS:
                break
    order = widget.get("sort", "none")
    if order in ("high", "low"):
        known = [i for i in items if i.number is not None]
        known.sort(key=lambda i: i.number or 0.0, reverse=order == "high")
        items = known + [i for i in items if i.number is None]
    elif order == "name":
        items.sort(key=lambda i: _natural(i.name.casefold()))
    return items


def short_names(names: list[str]) -> list[str]:
    """Names without the words they all start with: "Core 1", "Core 2" -> "1", "2"."""
    if len(names) < 2:
        return names
    words = [name.split(" ") for name in names]
    common = 0
    while all(len(w) > common + 1 for w in words) and len({w[common] for w in words}) == 1:
        common += 1
    return [" ".join(w[common:]) for w in words]

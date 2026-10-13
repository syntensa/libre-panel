"""Whom Start tells what is new in a release, and until when (views/home/news.js).

Someone who used an older Libre Panel is told once, until they close the card; someone whose
first Libre Panel is this one gets Start's first steps instead. ``news.json`` beside
config.toml keeps the version that first ran with it (``first``; "older" where config.toml
already named a theme when this version first ran: it was chosen in a version from before the
file) and the release whose news were closed last (``seen``). The file is written when Start
first asks, and only where config.toml's folder is there.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from libre_panel import __version__
from libre_panel.config import CONFIG_FILENAME, config_dir, theme_chosen

log = logging.getLogger(__name__)

NEWS_FILENAME = "news.json"
OLDER = "older"  # ``first`` of a config.toml that a version from before news.json chose a theme in
RELEASE = re.compile(r"\d{1,4}\.\d{1,4}")  # what ``seen`` holds: "0.6"


class News:
    def __init__(self, config_path: Path | None = None) -> None:
        config = config_path or config_dir() / CONFIG_FILENAME
        self.path = config.parent / NEWS_FILENAME
        # worked out as this version starts, before a first start of it chooses a theme
        self.first = OLDER if theme_chosen(config) else __version__

    def _read(self) -> dict[str, str] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("first"), str):
            return None
        seen = data.get("seen")
        return {"first": data["first"], "seen": seen if isinstance(seen, str) else ""}

    def _write(self, state: dict[str, str]) -> None:
        if not self.path.parent.is_dir():
            return  # nothing is there yet (a first start): asked again, it is worked out again
        try:
            self.path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        except OSError as exc:
            log.debug("news.json not written: %s", exc)

    def state(self) -> dict[str, str]:
        """``{"first": version or "older", "seen": release or ""}``, kept from the first ask."""
        state = self._read()
        if state is None:
            state = {"first": self.first, "seen": ""}
            self._write(state)
        return state

    def see(self, release: str) -> dict[str, str]:
        """The news of ``release`` ("0.6") were closed: Start does not show them again."""
        if not RELEASE.fullmatch(release):
            raise ValueError("seen is a release such as 0.6")
        state = {**self.state(), "seen": release}
        self._write(state)
        return state

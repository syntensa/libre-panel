"""Whether a newer Libre Panel is out, for Start to say so (views/home/update.js).

Only where config.toml's ``check_updates`` is on (Start asks once; Settings → System switches
it): at most once a day Libre Panel asks GitHub for its latest release. That request carries
what any does (the computer's address, "libre-panel/<version>"), nothing of the person or the
panel. ``updates.json`` beside config.toml keeps the answer and when it came, so that the
window and a browser ask once a day between them, and the version whose card was closed
("later"): its card does not come again, the next version's does.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import __version__
from libre_panel.config import CONFIG_FILENAME, config_dir

log = logging.getLogger(__name__)

UPDATES_FILENAME = "updates.json"
LATEST_URL = "https://api.github.com/repos/syntensa/libre-panel/releases/latest"
DAY_S = 24 * 3600
RETRY_S = 3600  # after no answer: the next try an hour later, not at each look at Start
NOTES_CHARS = 600  # of the release notes, the start (their first paragraph says what is new)
VERSION = re.compile(r"v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?(?:\.(\d{1,4}))?")


def version_key(text: str) -> tuple[int, int, int, int] | None:
    """``"v0.6.1"``, ``"0.6"`` or a hotfix's ``"0.6.8.1"`` as numbers to compare; None for
    what is no version."""
    found = VERSION.fullmatch(text.strip())
    if not found:
        return None
    major, minor, patch, fix = found.groups()
    return int(major), int(minor), int(patch or 0), int(fix or 0)


def newer(latest: str, current: str = __version__) -> bool:
    """Whether ``latest`` is a later release than ``current`` (an unknown one is not)."""
    a, b = version_key(latest), version_key(current)
    return a is not None and b is not None and a > b


def install_kind() -> str:
    """How this Libre Panel came: ``"setup"`` (the Windows setup), ``"portable"`` (the Windows
    .zip), ``"appimage"``, ``"linux"`` (the Linux .tar.gz) or ``"python"`` (pip and the like),
    which says the download that replaces it."""
    if not getattr(sys, "frozen", False):
        return "python"
    if sys.platform == "win32":
        # Inno Setup leaves its uninstaller beside the program it installed
        return "setup" if Path(sys.executable).with_name("unins000.exe").is_file() else "portable"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    return "linux"


# the end of each download's name on a release, by how Libre Panel came
ASSET_ENDINGS = {
    "setup": "-windows-x64-setup.exe",
    "portable": "-windows-x64-portable.zip",
    "appimage": "-x86_64.AppImage",
    "linux": "-linux-x64.tar.gz",
}


def _release(data: Any, kind: str) -> dict[str, str] | None:
    """What Start shows of GitHub's answer: the version, its page, the start of its notes and
    the download for this kind of install ("" for pip: its command is shown instead)."""
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        return None
    version = data["tag_name"].strip().removeprefix("v")
    if version_key(version) is None:
        return None
    page = data.get("html_url")
    if not isinstance(page, str) or not page.startswith("https://github.com/"):
        return None
    ending = ASSET_ENDINGS.get(kind)
    download = ""
    for asset in data.get("assets") or []:
        name = asset.get("name") if isinstance(asset, dict) else None
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if ending and isinstance(name, str) and name.endswith(ending) and isinstance(url, str):
            if url.startswith("https://github.com/"):
                download = url
    body = data.get("body") if isinstance(data.get("body"), str) else ""
    notes = body.strip().split("\n\n", 1)[0].replace("\n", " ").strip()[:NOTES_CHARS]
    published = data.get("published_at") if isinstance(data.get("published_at"), str) else ""
    return {
        "version": version,
        "page": page,
        "download": download,
        "notes": notes,
        "published": published,
    }


def fetch_latest(timeout: float = 10) -> Any:
    """GitHub's answer on the latest release (drafts and pre-releases are never it)."""
    request = urllib.request.Request(
        LATEST_URL,
        headers={
            "User-Agent": f"libre-panel/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class Updates:
    def __init__(
        self,
        config_path: Path | None = None,
        fetch: Callable[[], Any] = fetch_latest,
        clock: Callable[[], float] = time.time,
        kind: Callable[[], str] = install_kind,
    ) -> None:
        config = config_path or config_dir() / CONFIG_FILENAME
        self.path = config.parent / UPDATES_FILENAME
        self.fetch = fetch
        self.clock = clock
        self.kind = kind
        self.lock = threading.Lock()  # one ask at a time: the window and a browser look alike

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        if not self.path.parent.is_dir():
            return  # a first start: nothing is kept yet
        try:
            self.path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        except OSError as exc:
            log.debug("updates.json not written: %s", exc)

    def _due(self, data: dict[str, Any]) -> bool:
        asked = data.get("asked")
        if not isinstance(asked, int | float):
            return True
        wait = RETRY_S if data.get("error") else DAY_S
        return not 0 <= self.clock() - asked < wait

    def _ask(self, data: dict[str, Any]) -> dict[str, Any]:
        kind = self.kind()
        try:
            latest = _release(self.fetch(), kind)
            error = "" if latest else "GitHub's answer named no release"
        except (OSError, ValueError, http.client.HTTPException) as exc:
            latest, error = None, str(getattr(exc, "reason", exc)) or type(exc).__name__
            log.info("no word on a newer Libre Panel: %s", error)
        data = {**data, "asked": self.clock(), "error": error}
        if latest:
            data["latest"] = latest
        self._write(data)
        return data

    def state(self, check: bool | None, now: bool = False) -> dict[str, Any]:
        """``{"check", "current", "kind", "latest", "newer", "asked", "error", "later"}``.
        GitHub is asked only where ``check`` is on, and only once a day (``now``: at once, as
        "Look now" in Settings asks)."""
        with self.lock:
            data = self._read()
            if check and (now or self._due(data)):
                data = self._ask(data)
        latest = data.get("latest") if isinstance(data.get("latest"), dict) else None
        later = data.get("later") if isinstance(data.get("later"), str) else ""
        return {
            "check": check,
            "current": __version__,
            "kind": self.kind(),
            "latest": latest if check else None,
            "newer": bool(check and latest and newer(str(latest.get("version", "")))),
            "asked": data.get("asked") if isinstance(data.get("asked"), int | float) else None,
            "error": data.get("error") if check and isinstance(data.get("error"), str) else "",
            "later": later,
        }

    def later(self, version: str, check: bool | None) -> dict[str, Any]:
        """The card of ``version`` was closed: Start shows it no more (a later one it does)."""
        if version_key(version) is None:
            raise ValueError("later is a version such as 0.7.0")
        with self.lock:
            self._write({**self._read(), "later": version})
        return self.state(check)

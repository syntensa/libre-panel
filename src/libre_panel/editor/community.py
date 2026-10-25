"""The themes the community shares, for Start → Themes (views/home/sharing.js).

The project's GitHub keeps a list of them, ``community/themes/index.json`` (the format is in
``community/themes/README.md``): each entry names a theme's .zip and its preview beside the
list, and the zip's SHA-256. Libre Panel asks for the list only when the person opens
*Community themes*, keeps it an hour, and each preview as long. Those requests carry what any
does (the computer's address, "libre-panel/<version>"), nothing of the person or the panel.
*Install* downloads the zip from beside the list (nowhere else: a redirect to another host is
not followed), checks its SHA-256 and that the theme in it is made for the sizes the list says,
and puts it in place as an import of the file does (theme/share.py).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from libre_panel import __version__
from libre_panel.i18n import t
from libre_panel.theme.model import valid_theme_name
from libre_panel.theme.share import MAX_ZIP, import_theme, inspect_shared

log = logging.getLogger(__name__)

INDEX_URL = (
    "https://raw.githubusercontent.com/syntensa/libre-panel/main/community/themes/index.json"
)
INDEX_FORMAT = "libre-panel-community/1"
TIMEOUT_S = 10
KEEP_S = 3600  # the list and the previews, kept an hour
RETRY_S = 60  # after no answer: asked again a minute later, not at each look
MAX_INDEX = 1024 * 1024
MAX_PREVIEW = 2 * 1024 * 1024
MAX_ENTRIES = 500
KEPT_PREVIEWS = 100  # at most so many previews kept at once (the oldest goes)
# a file beside the list: a path in its folder, nothing that climbs out or names another host
_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_SIZE = re.compile(r"^(\d{2,5})x(\d{2,5})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TEXTS = {"name": 80, "author": 80, "description": 400}


class CommunityError(ValueError):
    """A community theme that cannot be installed, said in words."""


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


class _SameHost(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only on the same host: the list and its files come from the
    project's GitHub and nowhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != _origin(req.full_url):
            return None  # (urllib then answers with the redirect as an error)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int, timeout: float = TIMEOUT_S) -> bytes:
    """The bytes at ``url``, at most ``limit`` of them (more is an error)."""
    request = urllib.request.Request(url, headers={"User-Agent": f"libre-panel/{__version__}"})
    with urllib.request.build_opener(_SameHost).open(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url}: larger than {limit} bytes")
    return data


def beside(index_url: str, path: str) -> str:
    """The address of a file the list names (``aurora/aurora.zip``): in the list's folder."""
    return index_url.rsplit("/", 1)[0] + "/" + path


def _entry(raw: Any) -> dict[str, Any] | None:
    """An entry of the list as the app shows it, or None for one that is not sound (left out:
    the others still show)."""
    if not isinstance(raw, dict):
        return None
    entry: dict[str, Any] = {}
    for key, most in _TEXTS.items():
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > most:
            return None
        entry[key] = value.strip()
    sizes = raw.get("sizes")
    if not isinstance(sizes, list) or not sizes or len(sizes) > 16:
        return None
    entry["sizes"] = []
    for size in sizes:
        found = _SIZE.fullmatch(size) if isinstance(size, str) else None
        if found is None:
            return None
        entry["sizes"].append([int(found[1]), int(found[2])])
    zip_path, preview, sha256 = raw.get("zip"), raw.get("preview"), raw.get("sha256")
    if (
        not entry["name"]
        or not isinstance(zip_path, str)
        or not _PATH.fullmatch(zip_path)
        or not zip_path.endswith(".zip")
        or not isinstance(preview, str)
        or not _PATH.fullmatch(preview)
        or not preview.endswith(".png")
        or not isinstance(sha256, str)
        or not _SHA256.fullmatch(sha256.lower())
    ):
        return None
    theme_id = zip_path.rsplit("/", 1)[-1][: -len(".zip")]  # "aurora/aurora.zip": "aurora"
    if not valid_theme_name(theme_id):
        return None
    return {**entry, "id": theme_id, "zip": zip_path, "preview": preview, "sha256": sha256.lower()}


def parse_index(data: bytes) -> list[dict[str, Any]]:
    """The entries of the community's list (its bytes), each once by its id; ValueError for
    what is not such a list."""
    index = json.loads(data.decode("utf-8"))
    if not isinstance(index, dict) or index.get("format") != INDEX_FORMAT:
        raise ValueError(f"not a list of community themes ({INDEX_FORMAT})")
    themes = index.get("themes")
    if not isinstance(themes, list):
        raise ValueError("its themes are no list")
    entries: dict[str, dict[str, Any]] = {}
    for raw in themes[:MAX_ENTRIES]:
        entry = _entry(raw)
        if entry is None:
            log.info("community themes: an entry that is not sound is left out")
        elif entry["id"] not in entries:
            entries[entry["id"]] = entry
    return list(entries.values())


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    """What the app shows of an entry (the paths and the checksum stay here)."""
    keys = ("id", "name", "author", "description", "sizes")
    return {key: entry[key] for key in keys}


def _reason(exc: BaseException) -> str:
    return str(getattr(exc, "reason", "") or exc) or type(exc).__name__


# no answer, another answer than a list, or one nested too deep to read
_FAILURES = (OSError, ValueError, RecursionError, http.client.HTTPException)


class Community:
    """The list, kept an hour, and its previews; one ask for the list at a time."""

    def __init__(
        self,
        get: Callable[[str, int], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.get = get or fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.entries: list[dict[str, Any]] | None = None  # the last list that came
        self.url = ""  # where it came from
        self.asked: float | None = None
        self.error = ""
        self.previews: dict[tuple[str, str], tuple[float, bytes]] = {}

    def _index(self) -> list[dict[str, Any]] | None:
        """The list: the one kept, or (after an hour, or a minute after no answer) GitHub's."""
        with self.lock:
            now, url = self.clock(), INDEX_URL
            wait = RETRY_S if self.error else KEEP_S
            if url != self.url or self.asked is None or not 0 <= now - self.asked < wait:
                try:
                    self.entries, self.error = parse_index(self.get(url, MAX_INDEX)), ""
                except _FAILURES as exc:
                    self.error = _reason(exc)
                    log.info("no list of community themes: %s", self.error)
                    if url != self.url:
                        self.entries = None
                self.url, self.asked = url, now
            return self.entries

    def themes(self) -> dict[str, Any]:
        """``{"themes": [{id, name, author, description, sizes}], "offline"}``: ``id`` is the
        name of the entry's zip (``aurora`` for ``aurora/aurora.zip``), ``offline`` true when
        the list could not be had (the last one kept still shows)."""
        entries = self._index()
        return {"themes": [_public(entry) for entry in entries or []], "offline": bool(self.error)}

    def _find(self, theme_id: str) -> dict[str, Any] | None:
        return next((entry for entry in self._index() or [] if entry["id"] == theme_id), None)

    def preview(self, theme_id: str) -> bytes | None:
        """The PNG an entry names as its preview (kept an hour); None where there is none."""
        entry = self._find(theme_id)
        if entry is None:
            return None
        key, now = (theme_id, entry["preview"]), self.clock()
        kept = self.previews.get(key)
        if kept is not None and 0 <= now - kept[0] < KEEP_S:
            return kept[1]
        try:
            data = self.get(beside(self.url, entry["preview"]), MAX_PREVIEW)
        except _FAILURES as exc:
            log.info("no preview of the community theme %s: %s", theme_id, _reason(exc))
            return None
        if not data.startswith(b"\x89PNG"):
            return None
        if len(self.previews) >= KEPT_PREVIEWS:
            self.previews.pop(min(self.previews, key=lambda k: self.previews[k][0]), None)
        self.previews[key] = (now, data)
        return data

    def install(self, theme_id: str) -> dict[str, Any]:
        """Download an entry's zip, check it against the list's SHA-256 and put the theme in
        the person's themes folder (theme/share.py): ``{"id", "name", "files"}``."""
        entry = self._find(theme_id) if isinstance(theme_id, str) else None
        if entry is None:
            if self.error:
                raise CommunityError(t("The community themes cannot be reached right now."))
            raise CommunityError(t("This theme is not in the community's list (any more)."))
        try:
            data = self.get(beside(self.url, entry["zip"]), MAX_ZIP)
        except _FAILURES as exc:
            raise CommunityError(
                t("The theme could not be downloaded: {reason}", reason=_reason(exc))
            ) from exc
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise CommunityError(
                t("The download is not the file the community's list names (its checksum "
                  "differs): nothing was installed.")
            )  # fmt: skip
        width, height = inspect_shared(data)["size"]
        if not any(size in ([width, height], [height, width]) for size in entry["sizes"]):
            raise CommunityError(
                t("The theme in the download is made for {size}, not for the panels the "
                  "community's list names: nothing was installed.", size=f"{width} × {height}")
            )  # fmt: skip
        return import_theme(data, folder=entry["id"])

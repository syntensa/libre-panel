"""Notices from other programs: a short message on the panel that the command line (``libre-panel
notify``), a script or a Stream Deck button sends to the running app (``/api/notify``).

Only where config.toml's ``allow_notify`` is on (Settings → Services), and only with the
notify key: a secret of its own in ``notify.json`` beside config.toml, made when it is first
needed and readable by this user only. It lets a program show a notice and nothing else, so
it may go into a Stream Deck button or a script without giving the app away; *New key* in
Settings makes another and the old one stops working. The app answers on this computer only.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from libre_panel.config import config_dir
from libre_panel.icons import ICON_NAMES

log = logging.getLogger(__name__)

KEY_FILE = "notify.json"
HEADER = "X-Libre-Panel-Notify"  # or ``?key=`` in the address, for a program that sends no header
KIND = "outside"  # the kind of these notices, which a theme may switch off (toast.off)
MAX_TEXT = 160
MAX_TITLE = 40
SECONDS = (1.0, 60.0)
DEFAULT_SECONDS = 5.0
LEVELS = ("info", "warning", "error")


class NoticeError(ValueError):
    """What is wrong with a notice that was sent."""


def key_file() -> Path:
    return config_dir() / KEY_FILE


def read_key() -> str:
    """The notify key, or "" where none was made yet."""
    try:
        data = json.loads(key_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    key = data.get("key") if isinstance(data, dict) else None
    return key if isinstance(key, str) else ""


def new_key() -> str:
    """Make a new notify key (the old one stops working) and keep it for this user only."""
    key = secrets.token_urlsafe(24)
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{KEY_FILE}.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(temporary, flags, 0o600), "w", encoding="utf-8") as file:
        json.dump({"key": key}, file)
    os.replace(temporary, path)
    return key


def key(create: bool = False) -> str:
    """The notify key; with ``create`` one is made where there is none yet."""
    found = read_key()
    return found or (new_key() if create else "")


def _text(params: dict[str, Any], name: str, limit: int) -> str:
    value = params.get(name, "")
    if not isinstance(value, str):
        raise NoticeError(f"{name}: a text")
    value = " ".join(value.split())  # one line: no line breaks or runs of spaces on the panel
    if len(value) > limit:
        raise NoticeError(f"{name}: at most {limit} characters")
    return value


def notice(params: dict[str, Any]) -> dict[str, Any]:
    """The arguments of ``notify`` for what a program sent: ``text`` (needed), ``title``,
    ``seconds`` (1 to 60, 5 when not given), ``icon`` (one of the icons themes use) and
    ``level`` (info, warning, error). Numbers may come as text, as in an address."""
    text = _text(params, "text", MAX_TEXT)
    if not text:
        raise NoticeError("text: what the notice says")
    title = _text(params, "title", MAX_TITLE)
    seconds = params.get("seconds", DEFAULT_SECONDS)
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        raise NoticeError("seconds: a number") from None
    if not SECONDS[0] <= seconds <= SECONDS[1]:
        raise NoticeError(f"seconds: from {SECONDS[0]:g} to {SECONDS[1]:g}")
    icon = params.get("icon") or None
    if icon is not None and icon not in ICON_NAMES:
        raise NoticeError("icon: one of " + ", ".join(ICON_NAMES))
    level = params.get("level") or "info"
    if level not in LEVELS:
        raise NoticeError("level: info, warning or error")
    payload = {"label": title} if title else {}
    return {
        "text": text,
        "icon": icon,
        "level": level,
        "seconds": seconds,
        "kind": KIND,
        "rank": 1,
        "payload": payload,
    }


def address(port: int, key: str, text: str = "") -> str:
    """The address a program opens to show ``text`` (a Stream Deck's "Website" action, say)."""
    from urllib.parse import urlencode

    query = urlencode({"key": key, **({"text": text} if text else {})})
    return f"http://127.0.0.1:{port}/api/notify?{query}"

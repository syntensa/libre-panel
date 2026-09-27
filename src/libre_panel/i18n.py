"""Languages: English (the source text) and German.

Texts are looked up by their English wording in ``locale/<lang>.json``; the
same catalog serves the tray, the editor (through ``/api/i18n``) and the
panel itself (weekday and month names, weather descriptions). A text without
a translation stays English, so a missing entry never breaks anything; the
tests make sure there are none.

The language is ``language`` in config.toml: ``"auto"`` (the system
language), ``"en"`` or ``"de"``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

LANGUAGES = {"en": "English", "de": "Deutsch"}
LOCALE_DIR = Path(__file__).resolve().parent / "locale"

_current = "en"


@cache
def catalog(language: str) -> dict[str, Any]:
    """The translation tables of a language (empty for English)."""
    if language == "en" or language not in LANGUAGES:
        return {}
    return json.loads((LOCALE_DIR / f"{language}.json").read_text(encoding="utf-8"))


def _macos_language() -> str:
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"[A-Za-z]{2}", result.stdout)
    return match.group(0) if match else ""


def _windows_language() -> str:
    try:
        import ctypes
        import locale

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        return locale.windows_locale.get(lcid, "")
    except (AttributeError, OSError):
        return ""


def system_language() -> str:
    """The user's language if Libre Panel speaks it, otherwise English."""
    candidates = [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    candidates += os.environ.get("LANGUAGE", "").split(":")
    if sys.platform == "win32":
        candidates.insert(0, _windows_language())
    elif sys.platform == "darwin":
        candidates.insert(0, _macos_language())
    for value in candidates:
        code = value[:2].lower()
        if code in LANGUAGES:
            return code
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return "en"  # the first real setting counts, even if we do not speak it
    return "en"


def resolve(setting: str | None) -> str:
    if setting in LANGUAGES:
        return setting
    return system_language()


def set_language(setting: str | None) -> str:
    """Switch the language of this process (``"auto"``, ``"en"``, ``"de"``)."""
    global _current
    _current = resolve(setting)
    return _current


def language() -> str:
    return _current


def t(text: str, **values: Any) -> str:
    """Translate an English text; ``{name}`` placeholders are filled from ``values``."""
    message = catalog(_current).get("messages", {}).get(text, text)
    return message.format(**values) if values else message


def field_label(key: str) -> str:
    return catalog(_current).get("fields", {}).get(key, key.replace("_", " "))


# -- dates on the panel --------------------------------------------------------

_DATE_TOKENS = re.compile(r"%%|%[aAbB]|%d(?= %[bB])")


def format_date(moment: datetime, fmt: str) -> str:
    """``strftime`` with weekday and month names in the current language.

    In German a day number followed by a month name gets its period
    ("27. September").
    """
    names = catalog(_current)
    if not names:
        return moment.strftime(fmt)

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "%%":
            return token
        if token == "%d":
            return f"{moment.day:02d}."
        table = {"%a": "days_short", "%A": "days", "%b": "months_short", "%B": "months"}[token]
        index = moment.weekday() if token in ("%a", "%A") else moment.month - 1
        return names[table][index].replace("%", "%%")

    return moment.strftime(_DATE_TOKENS.sub(replace, fmt))

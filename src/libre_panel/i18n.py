"""Languages: English (the source text), German, French, Spanish, Polish and Portuguese (Brazil).

Texts are looked up by their English wording in ``locale/<lang>.json``; the
same catalog serves the tray, the editor (through ``/api/i18n``) and the
panel itself (weekday and month names, weather descriptions). A text without
a translation stays English, so a missing entry never breaks anything; the
tests make sure there are none.

The language is ``language`` in config.toml: ``"auto"`` (the system
language) or one of ``LANGUAGES``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

LANGUAGES = {
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "pl": "Polski",
    "pt": "Português (Brasil)",
}
# what follows a day's number before a month's name: "27. September", "27 de septiembre"
DAY_BEFORE_MONTH = {"de": ".", "es": " de", "pt": " de"}
DECIMAL_COMMA = {"de", "fr", "es", "pl", "pt"}  # 1,5 rather than 1.5
LOCALE_DIR = Path(__file__).resolve().parent / "locale"

_current = "en"


@cache
def catalog(language: str) -> dict[str, Any]:
    """The translation tables of a language (empty for English)."""
    if language == "en" or language not in LANGUAGES:
        return {}
    return json.loads((LOCALE_DIR / f"{language}.json").read_text(encoding="utf-8"))


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
    """Switch the language of this process (``"auto"`` or one of ``LANGUAGES``)."""
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

_DATE_CODE = re.compile(r"%[-#_0^]?[A-Za-z%]")
_NAMES = {"%a": "days_short", "%A": "days", "%b": "months_short", "%B": "months"}


def format_date(moment: datetime, fmt: str) -> str:
    """``strftime`` with weekday and month names in the current language.

    A day number followed by a month name is joined as the language does it: German gives it a
    period ("27. September"), Spanish and Portuguese a "de" ("27 de septiembre"), and Polish
    names the month in the genitive there ("27 września", ``months_after_day``).

    Only the codes go through ``strftime``, one at a time, never the names or the text around
    them: on Windows, Python before 3.12 passes its format through the ANSI code page, where a
    "ś" is lost (and an "ä" only happens to survive).
    """
    names = catalog(_current)

    def code(match: re.Match[str]) -> str:
        token = match.group(0)
        if names and token == "%d" and re.match(r" %[bB]", fmt[match.end() :]):
            return f"{moment.day:02d}{DAY_BEFORE_MONTH.get(_current, '')}"
        if names and token in _NAMES:
            table = _NAMES[token]
            if token == "%B" and "months_after_day" in names:
                if fmt[: match.start()].rstrip().endswith("%d"):  # "27 września"
                    table = "months_after_day"
            index = moment.weekday() if token in ("%a", "%A") else moment.month - 1
            return names[table][index]
        return moment.strftime(token)

    return _DATE_CODE.sub(code, fmt)

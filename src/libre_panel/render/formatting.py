"""Format strings for shared themes.

Themes come from strangers, so ``str.format`` is locked down: only the named
fields ``value``, ``unit`` and ``label`` exist, attribute/index access is
refused and absurd widths are rejected. Two extra format specs exist:
``{value:bytes}`` (1.2 MB) and ``{value:duration}`` (3d 5h).
"""

from __future__ import annotations

import re
import string
from typing import Any

_ALLOWED_FIELDS = {"value", "unit", "label"}
_HUGE_NUMBER = re.compile(r"\d{4,}")
MAX_OUTPUT = 200


class FormatError(ValueError):
    pass


def human_bytes(value: float) -> str:
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"  # pragma: no cover


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class _SafeFormatter(string.Formatter):
    def get_field(self, field_name: str, args: Any, kwargs: dict[str, Any]) -> Any:
        if field_name not in _ALLOWED_FIELDS:
            raise FormatError(f"unknown field {{{field_name}}}; use value, unit or label")
        return kwargs[field_name], field_name

    def format_field(self, value: Any, format_spec: str) -> str:
        if _HUGE_NUMBER.search(format_spec):
            raise FormatError("format width too large")
        if format_spec == "bytes":
            return human_bytes(value)
        if format_spec == "duration":
            return human_duration(value)
        return super().format_field(value, format_spec)


_FORMATTER = _SafeFormatter()


def safe_format(fmt: str, value: Any, unit: str = "", label: str = "") -> str:
    try:
        text = _FORMATTER.format(fmt, value=value, unit=unit, label=label)
    except FormatError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise FormatError(str(exc)) from exc
    return text[:MAX_OUTPUT]

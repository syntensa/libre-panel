"""Collect every text Libre Panel shows through its translation layer.

Used by test_i18n.py: every collected text must have a German translation,
and the catalog must not carry texts nobody uses any more.
"""

from __future__ import annotations

import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "libre_panel"
STATIC = SRC / "editor" / "static"

_JS_CALL = re.compile(r"""\bt\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
_JS_TEMPLATE_CALL = re.compile(r"\bt\(\s*`")


def python_messages() -> set[str]:
    found = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            first = node.args[0]
            if name == "t" and isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def js_messages() -> set[str]:
    source = (STATIC / "editor.js").read_text(encoding="utf-8")
    if _JS_TEMPLATE_CALL.search(source):
        raise AssertionError('t(`...`) in editor.js: use t("... {name}", { name })')
    found = set()
    for literal in _JS_CALL.findall(source):
        if literal.startswith("'"):
            literal = '"' + literal[1:-1].replace('\\"', '"').replace('"', '\\"') + '"'
            literal = literal.replace("\\'", "'")
        found.add(json.loads(literal))
    return found


class _HtmlTexts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: set[str] = set()
        self._stack: list[list[str] | None] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in (attrs.get("data-i18n-attr") or "").split():
            self.found.add(" ".join(attrs[name].split()))
        if tag in ("img", "input", "meta", "link", "br"):
            return
        self._stack.append([] if "data-i18n" in attrs else None)

    def handle_data(self, data):
        for texts in self._stack:
            if texts is not None:
                texts.append(data)

    def handle_endtag(self, tag):
        if self._stack:
            texts = self._stack.pop()
            if texts is not None:
                self.found.add(" ".join("".join(texts).split()))


def html_messages() -> set[str]:
    parser = _HtmlTexts()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    return parser.found


def dynamic_messages() -> set[str]:
    """Texts translated from data tables rather than written as t("...")."""
    from libre_panel.devices.models import MODELS
    from libre_panel.editor.presets import PRESETS
    from libre_panel.sensors.psutil_provider import BATTERY_STATES
    from libre_panel.sensors.sky import MOON_PHASES
    from libre_panel.weather.open_meteo import _WMO

    return (
        set(_WMO.values())
        | set(BATTERY_STATES)
        | set(MOON_PHASES)
        | {"Unknown"}
        | {p["name"] for p in PRESETS}
        | {m.notes for m in MODELS if m.notes}
    )


def all_messages() -> set[str]:
    return python_messages() | js_messages() | html_messages() | dynamic_messages()


def tables() -> dict[str, set[str]]:
    from libre_panel.icons import ICON_NAMES
    from libre_panel.theme.model import COMMON_FIELDS, TOAST_ANCHORS, WIDGET_SPECS

    fields, enums = set(COMMON_FIELDS), set(TOAST_ANCHORS)  # the editor's toast position
    for spec in [COMMON_FIELDS, *WIDGET_SPECS.values()]:
        fields |= set(spec)
        for kind, _ in spec.values():
            kind = kind.rstrip("?")
            if kind.startswith("enum:"):
                enums |= set(kind[5:].split("|"))
    return {
        "fields": fields,
        "widgets": set(WIDGET_SPECS),
        "enums": enums,
        "icons": set(ICON_NAMES),
    }


if __name__ == "__main__":  # list what the German catalog lacks
    import sys

    sys.path.insert(0, str(SRC.parent))
    catalog = json.loads((SRC / "locale" / "de.json").read_text(encoding="utf-8"))
    for text in sorted(all_messages() - set(catalog.get("messages", {}))):
        print("messages:", json.dumps(text, ensure_ascii=False))
    for table, keys in tables().items():
        for key in sorted(keys - set(catalog.get(table, {}))):
            print(f"{table}:", key)

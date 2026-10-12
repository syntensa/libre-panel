"""Collect every text Libre Panel shows through its translation layer.

Used by test_i18n.py: every collected text must have a German translation,
and the catalog must not carry texts nobody uses any more.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "libre_panel"
STATIC = SRC / "editor" / "static"  # what the console's and plugins' pages share (kit.js)
PAGES = SRC / "pages" / "web"  # the scripts of the built-in pages
APP = SRC / "editor" / "app"  # the app's modules; not its vendored libraries

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


def script_files() -> list[Path]:
    """The scripts that show texts: those the built-in pages share, the pages' own and the
    app's (not its vendored libraries)."""
    app = (path for path in APP.rglob("*.js") if "vendor" not in path.relative_to(APP).parts)
    return sorted(STATIC.glob("*.js")) + sorted(PAGES.glob("*.js")) + sorted(app)


def js_messages() -> set[str]:
    found = set()
    for path in script_files():
        source = path.read_text(encoding="utf-8")
        if _JS_TEMPLATE_CALL.search(source):
            raise AssertionError(f't(`...`) in {path.name}: use t("... {{name}}", {{ name }})')
        for literal in _JS_CALL.findall(source):
            if literal.startswith("'"):
                literal = '"' + literal[1:-1].replace('\\"', '"').replace('"', '\\"') + '"'
                literal = literal.replace("\\'", "'")
            found.add(json.loads(literal))
    return found


def dynamic_messages() -> set[str]:
    """Texts translated from data tables rather than written as t("...")."""
    from libre_panel.devices.models import MODELS
    from libre_panel.editor.presets import PRESETS
    from libre_panel.sensors.media import MEDIA_STATES
    from libre_panel.sensors.psutil_provider import BATTERY_STATES
    from libre_panel.sensors.sky import MOON_PHASES
    from libre_panel.weather.open_meteo import _WMO

    return (
        set(_WMO.values())
        | set(BATTERY_STATES)
        | set(MOON_PHASES)
        | set(MEDIA_STATES)
        | {"Unknown"}
        | {p["name"] for p in PRESETS}
        | {m.notes for m in MODELS if m.notes}
    )


def theme_messages() -> set[str]:
    """The words of the built-in themes that are shown in the panel's language ("translate")."""
    found = set()
    for path in (SRC / "themes").glob("*/theme.json"):
        for widget in json.loads(path.read_text(encoding="utf-8")).get("widgets", []):
            if widget.get("type") == "text" and widget.get("translate"):
                found.add(widget["text"])
    return found


def all_messages() -> set[str]:
    return python_messages() | js_messages() | dynamic_messages() | theme_messages()


def tables() -> dict[str, set[str]]:
    from libre_panel.icons import ICON_NAMES
    from libre_panel.render.overlays import BUILT_IN_STYLES
    from libre_panel.theme.model import COMMON_FIELDS, LOOK_SPECS, TOAST_ANCHORS, WIDGET_SPECS

    fields, enums = set(COMMON_FIELDS), set(TOAST_ANCHORS)  # the editor's toast position
    styles = [style.options for style in BUILT_IN_STYLES.values()]  # Theme -> Messages
    looks = list(LOOK_SPECS.values())  # Theme -> Look
    for spec in [COMMON_FIELDS, *WIDGET_SPECS.values(), *styles, *looks]:
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

"""Theme format ``libre-panel-theme/1``.

A theme is a folder with a ``theme.json`` and optional assets (images, fonts).
Themes are meant to be shared, so everything in them is treated as untrusted:
asset paths may not leave the theme folder and format strings cannot reach
into Python objects (see ``libre_panel.render.formatting``).
"""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from PIL import ImageColor

from libre_panel.config import user_themes_dir
from libre_panel.devices.models import find_model, orientation_of

THEME_FORMAT = "libre-panel-theme/1"
THEME_FILENAME = "theme.json"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ThemeError(ValueError):
    """Raised for invalid or unsafe themes."""


# Field kinds: int, number, number?, string, text, color, color?, bool, sensor,
# format, font, asset, rules, and "enum:a|b|c". The editor builds its property
# forms from this table, so it is the single source of truth for widgets.
_COMMON: dict[str, tuple[str, Any]] = {
    "id": ("string", ""),
    "x": ("int", 0),
    "y": ("int", 0),
    "visible": ("bool", True),
}
_TEXT_STYLE: dict[str, tuple[str, Any]] = {
    "font": ("font", ""),
    "font_size": ("int", 24),
    "color": ("color", "#ffffff"),
    "align": ("enum:left|center|right", "left"),
}

WIDGET_SPECS: dict[str, dict[str, tuple[str, Any]]] = {
    "rect": {
        "w": ("int", 100),
        "h": ("int", 50),
        "color": ("color?", "#1f2937"),
        "radius": ("int", 0),
        "outline": ("color?", None),
        "outline_width": ("int", 1),
    },
    "text": {"text": ("text", "Label"), **_TEXT_STYLE},
    "metric": {
        "sensor": ("sensor", "cpu.load"),
        "format": ("format", "{value:.0f}{unit}"),
        "fallback": ("string", "--"),
        **_TEXT_STYLE,
        "color_rules": ("rules", []),
    },
    "bar": {
        "sensor": ("sensor", "cpu.load"),
        "w": ("int", 200),
        "h": ("int", 16),
        "min": ("number", 0),
        "max": ("number", 100),
        "color": ("color", "#22d3ee"),
        "background": ("color?", "#1f2937"),
        "radius": ("int", 4),
        "direction": ("enum:right|left|up|down", "right"),
        "color_rules": ("rules", []),
    },
    "gauge": {
        "sensor": ("sensor", "cpu.load"),
        "w": ("int", 120),
        "h": ("int", 120),
        "min": ("number", 0),
        "max": ("number", 100),
        "start_angle": ("number", 135),
        "end_angle": ("number", 405),
        "thickness": ("int", 12),
        "color": ("color", "#22d3ee"),
        "background": ("color?", "#1f2937"),
        "color_rules": ("rules", []),
    },
    "graph": {
        "sensor": ("sensor", "cpu.load"),
        "w": ("int", 200),
        "h": ("int", 60),
        "min": ("number?", 0),
        "max": ("number?", 100),
        "history": ("int", 60),
        "color": ("color", "#22d3ee"),
        "fill": ("bool", True),
        "line_width": ("int", 2),
        "background": ("color?", None),
    },
    "clock": {"format": ("string", "%H:%M"), **_TEXT_STYLE},
    "image": {"src": ("asset", ""), "w": ("int", 0), "h": ("int", 0)},
    "weather": {
        "field": (
            "enum:temperature|apparent_temperature|humidity|wind_speed|description|code",
            "temperature",
        ),
        "format": ("format", "{value:.0f}{unit}"),
        "fallback": ("string", "--"),
        **_TEXT_STYLE,
    },
}


@dataclass
class Theme:
    name: str
    width: int
    height: int
    widgets: list[dict[str, Any]]
    author: str = ""
    license: str = ""
    description: str = ""
    background_color: str = "#000000"
    background_image: str | None = None
    refresh_ms: int = 1000
    model: str = "custom"
    root: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def orientation(self) -> str:
        return orientation_of(self.width, self.height)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": THEME_FORMAT,
            "name": self.name,
            "author": self.author,
            "license": self.license,
            "description": self.description,
            "display": {
                "model": self.model,
                "orientation": self.orientation,
                "width": self.width,
                "height": self.height,
            },
            "background": {"color": self.background_color, "image": self.background_image},
            "refresh_ms": self.refresh_ms,
            "widgets": deepcopy(self.widgets),
        }


def builtin_themes_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "themes"


def valid_theme_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and ".." not in name


def resolve_asset(root: Path, rel: str) -> Path:
    """Resolve an asset path inside a theme folder, refusing anything outside it."""
    if not rel or not isinstance(rel, str):
        raise ThemeError("empty asset path")
    if Path(rel).is_absolute() or PureWindowsPath(rel).drive or rel.startswith(("/", "\\")):
        raise ThemeError(f"asset path must be relative to the theme folder: {rel!r}")
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ThemeError(f"asset path leaves the theme folder: {rel!r}")
    return target


def _check_color(value: Any, where: str, optional: bool) -> Any:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ThemeError(f"{where}: expected a color string like '#22d3ee'")
    try:
        ImageColor.getrgb(value)
    except ValueError as exc:
        raise ThemeError(f"{where}: invalid color {value!r}") from exc
    return value


def _coerce(kind: str, value: Any, where: str) -> Any:
    optional = kind.endswith("?")
    base = kind.rstrip("?")
    if value is None and optional:
        return None
    if base == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ThemeError(f"{where}: expected an integer")
        return int(value)
    if base == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ThemeError(f"{where}: expected a number")
        return value
    if base == "bool":
        if not isinstance(value, bool):
            raise ThemeError(f"{where}: expected true/false")
        return value
    if base == "color":
        return _check_color(value, where, optional)
    if base.startswith("enum:"):
        options = base[5:].split("|")
        if value not in options:
            raise ThemeError(f"{where}: must be one of {', '.join(options)}")
        return value
    if base == "rules":
        if not isinstance(value, list):
            raise ThemeError(f"{where}: expected a list of rules")
        rules = []
        for i, rule in enumerate(value):
            if not isinstance(rule, dict) or "above" not in rule or "color" not in rule:
                raise ThemeError(f"{where}[{i}]: rule needs 'above' and 'color'")
            rules.append(
                {
                    "above": _coerce("number", rule["above"], f"{where}[{i}].above"),
                    "color": _check_color(rule["color"], f"{where}[{i}].color", False),
                }
            )
        return rules
    # string, text, sensor, format, font, asset
    if not isinstance(value, str):
        raise ThemeError(f"{where}: expected a string")
    return value


def normalize_widget(raw: Any, index: int) -> tuple[dict[str, Any], list[str]]:
    where = f"widgets[{index}]"
    if not isinstance(raw, dict):
        raise ThemeError(f"{where}: expected an object")
    wtype = raw.get("type")
    if wtype not in WIDGET_SPECS:
        raise ThemeError(f"{where}: unknown widget type {wtype!r}")
    spec = {**_COMMON, **WIDGET_SPECS[wtype]}
    widget: dict[str, Any] = {"type": wtype}
    for key, (kind, default) in spec.items():
        value = raw.get(key, deepcopy(default))
        widget[key] = _coerce(kind, value, f"{where}.{key}")
    if not widget["id"]:
        widget["id"] = f"{wtype}-{index + 1}"
    unknown = sorted(set(raw) - set(spec) - {"type"})
    warnings = [f"{where}: ignoring unknown field {k!r}" for k in unknown]
    return widget, warnings


def parse_theme(data: Any, root: Path | None = None) -> Theme:
    if not isinstance(data, dict):
        raise ThemeError("theme.json must contain an object")
    if data.get("format") != THEME_FORMAT:
        raise ThemeError(
            f"unsupported theme format {data.get('format')!r}, expected {THEME_FORMAT}"
        )
    display = data.get("display", {})
    if not isinstance(display, dict):
        raise ThemeError("display must be an object")
    model_id = _coerce("string", display.get("model", "custom"), "display.model")
    panel = find_model(model_id)
    if model_id != "custom" and panel is None:
        raise ThemeError(f"display.model: unknown panel model {model_id!r}")
    if panel is not None:
        orientation = _coerce(
            "enum:landscape|portrait",
            display.get("orientation", "landscape"),
            "display.orientation",
        )
        width, height = panel.size(orientation)
        for key, expected in (("width", width), ("height", height)):
            if key in display and display[key] != expected:
                raise ThemeError(
                    f"display.{key} is {display[key]} but {panel.label} in {orientation} "
                    f"is {width}x{height}"
                )
    else:
        width = _coerce("int", display.get("width"), "display.width")
        height = _coerce("int", display.get("height"), "display.height")
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise ThemeError("display size must be between 16 and 4096 pixels")
    background = data.get("background", {}) or {}
    bg_color = _check_color(background.get("color", "#000000"), "background.color", False)
    bg_image = background.get("image")
    if bg_image is not None:
        bg_image = _coerce("string", bg_image, "background.image")
        if root is not None:
            resolve_asset(root, bg_image)
    refresh_ms = _coerce("int", data.get("refresh_ms", 1000), "refresh_ms")
    if refresh_ms < 100:
        raise ThemeError("refresh_ms must be at least 100")

    raw_widgets = data.get("widgets", [])
    if not isinstance(raw_widgets, list):
        raise ThemeError("widgets must be a list")
    widgets, warnings, seen = [], [], set()
    for i, raw in enumerate(raw_widgets):
        widget, w = normalize_widget(raw, i)
        if widget["id"] in seen:
            raise ThemeError(f"widgets[{i}]: duplicate id {widget['id']!r}")
        seen.add(widget["id"])
        if root is not None:
            for key in ("src", "font"):
                if widget.get(key):
                    resolve_asset(root, widget[key])
        widgets.append(widget)
        warnings.extend(w)

    return Theme(
        name=_coerce("string", data.get("name", "Untitled"), "name"),
        author=_coerce("string", data.get("author", ""), "author"),
        license=_coerce("string", data.get("license", ""), "license"),
        description=_coerce("string", data.get("description", ""), "description"),
        width=width,
        height=height,
        background_color=bg_color,
        background_image=bg_image,
        refresh_ms=refresh_ms,
        model=model_id,
        widgets=widgets,
        root=root,
        warnings=warnings,
    )


def load_theme(folder: Path) -> Theme:
    path = folder / THEME_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ThemeError(f"no {THEME_FILENAME} in {folder}") from exc
    except json.JSONDecodeError as exc:
        raise ThemeError(f"{path}: {exc}") from exc
    return parse_theme(data, root=folder)


def _search_dirs() -> list[Path]:
    # User themes first so a user can shadow a built-in theme of the same name.
    return [user_themes_dir(), builtin_themes_dir()]


def find_theme(name: str) -> Path:
    if not valid_theme_name(name):
        raise ThemeError(f"invalid theme name {name!r}")
    for base in _search_dirs():
        folder = base / name
        if (folder / THEME_FILENAME).is_file():
            return folder
    raise ThemeError(f"theme {name!r} not found")


def list_themes() -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for base in reversed(_search_dirs()):
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if (folder / THEME_FILENAME).is_file() and valid_theme_name(folder.name):
                found[folder.name] = {
                    "id": folder.name,
                    "builtin": base == builtin_themes_dir(),
                    "path": str(folder),
                }
    return sorted(found.values(), key=lambda t: t["id"])


def save_theme(name: str, data: dict[str, Any], source: str | None = None) -> Path:
    """Validate and save a theme into the user themes directory.

    When saving a copy of another theme, that theme's assets are copied along
    so images and fonts keep working.
    """
    if not valid_theme_name(name):
        raise ThemeError(f"invalid theme name {name!r}")
    target = user_themes_dir() / name
    if source and source != name and not target.exists():
        src_folder = find_theme(source)
        shutil.copytree(src_folder, target)
    target.mkdir(parents=True, exist_ok=True)
    theme = parse_theme(data, root=target)
    (target / THEME_FILENAME).write_text(
        json.dumps(theme.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target

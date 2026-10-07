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
from libre_panel.fonts import BUILTIN_PREFIX, DEFAULT_FONT, builtin_font_path, builtin_fonts
from libre_panel.icons import ICON_NAMES

THEME_FORMAT = "libre-panel-theme/1"
THEME_FILENAME = "theme.json"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ThemeError(ValueError):
    """Raised for invalid or unsafe themes."""


# Field kinds: int, number, number?, string, text, color, color?, bool, sensor,
# format, font, asset, rules, icon and "enum:a|b|c". Colors may name a palette
# entry ("@accent"). The editor builds its property forms from this table, so
# it is the single source of truth for widgets.
_COMMON: dict[str, tuple[str, Any]] = {
    "id": ("string", ""),
    "x": ("int", 0),
    "y": ("int", 0),
    "visible": ("bool", True),
    "locked": ("bool", False),  # editor only: not draggable
    "hide_if_missing": ("bool", False),  # hide when its sensor has no value
    # Show only while this sensor has a value ("!key": only while it has none),
    # so a card, its labels and icons go with the readings they frame.
    "needs": ("sensor", ""),
    "opacity": ("number", 1.0),
    "glow": ("number", 0.0),  # 0 = off, 1 = strong
    "glow_radius": ("int", 10),
    "shadow": ("color?", None),
    "shadow_offset": ("int", 3),
    "shadow_blur": ("int", 6),
}
COMMON_FIELDS = _COMMON
# Fields the editor groups under "Effects".
EFFECT_FIELDS = ("opacity", "glow", "glow_radius", "shadow", "shadow_offset", "shadow_blur")


def _text_style(tabular: bool) -> dict[str, tuple[str, Any]]:
    return {
        "font": ("font", ""),  # empty = the theme's font
        "font_size": ("int", 24),
        "color": ("color", "#ffffff"),
        "align": ("enum:left|center|right", "left"),
        "letter_spacing": ("int", 0),
        "tabular": ("bool", tabular),  # equal-width digits: numbers do not jitter
    }


WIDGET_SPECS: dict[str, dict[str, tuple[str, Any]]] = {
    "rect": {
        "w": ("int", 100),
        "h": ("int", 50),
        "color": ("color?", "#1f2937"),
        "color2": ("color?", None),
        "gradient": ("enum:vertical|horizontal", "vertical"),
        "radius": ("int", 0),
        "outline": ("color?", None),
        "outline_width": ("int", 1),
        "backdrop_blur": ("int", 0),
    },
    "text": {"text": ("text", "Label"), **_text_style(False)},
    "metric": {
        "sensor": ("sensor", "cpu.load"),
        "format": ("format", "{value:.0f}{unit}"),
        "fallback": ("string", "--"),
        **_text_style(True),
        "color_rules": ("rules", []),
    },
    "bar": {
        "sensor": ("sensor", "cpu.load"),
        "w": ("int", 200),
        "h": ("int", 16),
        "min": ("number", 0),
        "max": ("number", 100),
        "color": ("color", "#22d3ee"),
        "color2": ("color?", None),
        "background": ("color?", "#1f2937"),
        "radius": ("int", 4),
        "direction": ("enum:right|left|up|down", "right"),
        "scale": ("enum:linear|sqrt|log", "linear"),
        "segments": ("int", 0),
        "segment_gap": ("int", 2),
        "smooth": ("bool", True),
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
        "color2": ("color?", None),
        "background": ("color?", "#1f2937"),
        "scale": ("enum:linear|sqrt|log", "linear"),
        "cap": ("enum:round|flat", "round"),
        "ticks": ("int", 0),
        "tick_color": ("color?", None),
        "smooth": ("bool", True),
        "color_rules": ("rules", []),
    },
    "graph": {
        "sensor": ("sensor", "cpu.load"),
        "w": ("int", 200),
        "h": ("int", 60),
        "min": ("number?", 0),
        "max": ("number?", 100),
        "scale": ("enum:linear|sqrt|log", "linear"),
        "history": ("int", 60),
        "color": ("color", "#22d3ee"),
        "fill": ("bool", True),
        "fill_fade": ("bool", True),
        "smooth": ("bool", True),
        "per_frame": ("bool", False),
        "line_width": ("int", 2),
        "grid": ("int", 0),
        "grid_color": ("color?", "#ffffff1f"),
        "background": ("color?", None),
    },
    "clock": {"format": ("string", "%H:%M"), **_text_style(True)},
    "image": {"src": ("asset", ""), "w": ("int", 0), "h": ("int", 0)},
    "weather": {
        "field": (
            "enum:temperature|apparent_temperature|humidity|wind_speed|description|code",
            "temperature",
        ),
        "format": ("format", "{value:.0f}{unit}"),
        "fallback": ("string", "--"),
        **_text_style(True),
    },
    "icon": {
        "icon": ("icon", "cpu"),  # "weather" follows the current weather code
        "size": ("int", 32),
        "color": ("color", "#ffffff"),
        "stroke": ("number", 2.0),
    },
}

_PALETTE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


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
    palette: dict[str, str] = field(default_factory=dict)
    font: str = DEFAULT_FONT
    smoothing_ms: int = 400
    screen: dict[str, Any] | None = None  # {"name": ..., "options": {...}}: a plugin draws
    # messages from services: where, how long, which kinds not, and a plugin style
    toast: dict[str, Any] = field(default_factory=lambda: dict(TOAST_DEFAULTS))
    root: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def orientation(self) -> str:
        return orientation_of(self.width, self.height)

    @property
    def toast_anchor(self) -> str:
        return self.toast["anchor"]

    def to_dict(self) -> dict[str, Any]:
        data = {
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
            "palette": dict(self.palette),
            "font": self.font,
            "background": {"color": self.background_color, "image": self.background_image},
            "refresh_ms": self.refresh_ms,
            "animation": {"smoothing_ms": self.smoothing_ms},
            "widgets": deepcopy(self.widgets),
        }
        if self.screen is not None:
            data["screen"] = deepcopy(self.screen)
        toast = {k: deepcopy(v) for k, v in self.toast.items() if v != TOAST_DEFAULTS[k]}
        if toast:
            data["toast"] = toast
        return data


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


def _check_color(
    value: Any, where: str, optional: bool, palette: dict[str, str] | None = None
) -> Any:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ThemeError(f"{where}: expected a color string like '#22d3ee'")
    if value.startswith("@"):
        if palette is None:
            raise ThemeError(f"{where}: palette references are not allowed here, use a colour")
        if value[1:] not in palette:
            raise ThemeError(f"{where}: {value!r} is not in the theme palette")
        return value
    try:
        ImageColor.getrgb(value)
    except ValueError as exc:
        raise ThemeError(f"{where}: invalid color {value!r}") from exc
    return value


def _check_font(value: str, where: str, root: Path | None) -> str:
    if value.startswith(BUILTIN_PREFIX):
        if builtin_font_path(value) is None:
            names = ", ".join(BUILTIN_PREFIX + n for n in builtin_fonts())
            raise ThemeError(f"{where}: unknown built-in font {value!r} (available: {names})")
    elif value and root is not None:
        resolve_asset(root, value)
    return value


def _coerce(kind: str, value: Any, where: str, palette: dict[str, str] | None = None) -> Any:
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
        return _check_color(value, where, optional, palette)
    if base == "icon":
        if value not in ICON_NAMES:
            raise ThemeError(f"{where}: unknown icon {value!r}")
        return value
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
                    "color": _check_color(rule["color"], f"{where}[{i}].color", False, palette),
                }
            )
        return rules
    # string, text, sensor, format, font, asset
    if not isinstance(value, str):
        raise ThemeError(f"{where}: expected a string")
    return value


_BASE_KINDS = {
    "int", "number", "bool", "color", "icon", "rules",
    "string", "text", "sensor", "format", "font", "asset",
}  # fmt: skip


def valid_kind(kind: Any) -> bool:
    """A field kind the loader and the editor know (plugins declare their fields with these)."""
    if not isinstance(kind, str):
        return False
    base = kind.rstrip("?")
    return base in _BASE_KINDS or (base.startswith("enum:") and len(base) > 5)


def _plugin(kind: str, name: str) -> Any:
    from libre_panel.plugins.loader import registry

    return registry().get(kind, name)


def _check_paths(
    spec: dict[str, tuple[str, Any]], values: dict[str, Any], where: str, root: Path | None
) -> None:
    """Fonts and assets named in plugin fields stay inside the theme folder too."""
    for key, (kind, _default) in spec.items():
        value = values.get(key)
        if not value or not isinstance(value, str):
            continue
        if kind.rstrip("?") == "font":
            _check_font(value, f"{where}.{key}", root)
        elif kind.rstrip("?") == "asset" and root is not None:
            resolve_asset(root, value)


def normalize_widget(
    raw: Any, index: int, palette: dict[str, str] | None = None, root: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    where = f"widgets[{index}]"
    if not isinstance(raw, dict):
        raise ThemeError(f"{where}: expected an object")
    wtype = raw.get("type")
    plugin = None
    if wtype in WIDGET_SPECS:
        spec = {**_COMMON, **WIDGET_SPECS[wtype]}
    elif isinstance(wtype, str) and "." in wtype:  # a plugin's widget type
        plugin = _plugin("widgets", wtype)
        if plugin is None:
            return _missing_plugin_widget(raw, wtype, index, where, palette)
        spec = {**_COMMON, **plugin.spec}
    else:
        raise ThemeError(f"{where}: unknown widget type {wtype!r}")
    widget: dict[str, Any] = {"type": wtype}
    for key, (kind, default) in spec.items():
        value = raw.get(key, deepcopy(default))
        widget[key] = _coerce(kind, value, f"{where}.{key}", palette)
    if not 0 <= widget["opacity"] <= 1:
        raise ThemeError(f"{where}.opacity: must be between 0 and 1")
    if not 0 <= widget["glow"] <= 1:
        raise ThemeError(f"{where}.glow: must be between 0 and 1")
    for key in ("glow_radius", "shadow_blur", "backdrop_blur"):
        if key in widget and not 0 <= widget[key] <= 64:
            raise ThemeError(f"{where}.{key}: must be between 0 and 64")
    if not widget["id"]:
        widget["id"] = f"{wtype}-{index + 1}"
    if plugin is not None:
        _check_paths(plugin.spec, widget, where, root)
    unknown = sorted(set(raw) - set(spec) - {"type"})
    warnings = [f"{where}: ignoring unknown field {k!r}" for k in unknown]
    return widget, warnings


def _missing_plugin_widget(
    raw: dict[str, Any], wtype: str, index: int, where: str, palette: dict[str, str] | None
) -> tuple[dict[str, Any], list[str]]:
    """Kept as it is, so saving the theme loses nothing; it is not drawn."""
    widget = deepcopy(raw)
    for key, (kind, default) in _COMMON.items():
        widget[key] = _coerce(kind, raw.get(key, deepcopy(default)), f"{where}.{key}", palette)
    if not widget["id"]:
        widget["id"] = f"{wtype}-{index + 1}"
    return widget, [f"{where}: widget type {wtype!r} needs a plugin that is not installed"]


TOAST_ANCHORS = ("top-right", "top-left", "bottom-right", "bottom-left", "top", "bottom")


TOAST_DEFAULTS: dict[str, Any] = {
    "anchor": "top-right",
    "seconds": 4.0,
    "queue": True,  # false: the same or a higher rank takes over, a lower one is dropped
    "off": [],
    "style": "",
    "options": {},
}


def _parse_toast(
    raw: Any, palette: dict[str, str], root: Path | None, warnings: list[str]
) -> dict[str, Any]:
    """``"toast": {"anchor": "bottom-right", "seconds": 5, "off": ["music"],
    "style": "myplugin.band", "options": {...}}``: messages from services."""
    toast = deepcopy(TOAST_DEFAULTS)
    if raw is None:
        return toast
    if not isinstance(raw, dict):
        raise ThemeError("toast must be an object")
    anchors = "enum:" + "|".join(TOAST_ANCHORS)
    toast["anchor"] = _coerce(anchors, raw.get("anchor", "top-right"), "toast.anchor")
    seconds = _coerce("number", raw.get("seconds", 4.0), "toast.seconds")
    if not 0.5 <= seconds <= 60:
        raise ThemeError("toast.seconds: must be between 0.5 and 60")
    toast["seconds"] = seconds
    toast["queue"] = _coerce("bool", raw.get("queue", True), "toast.queue")
    off = raw.get("off", [])
    if not isinstance(off, list) or not all(isinstance(kind, str) for kind in off):
        raise ThemeError('toast.off: expected a list of kinds, e.g. ["music"]')
    toast["off"] = list(off)
    toast["style"] = _coerce("string", raw.get("style", ""), "toast.style")
    if toast["style"]:
        given = raw.get("options", {}) or {}
        toast["options"] = _plugin_options(
            "toasts", toast["style"], given, "toast", palette, root, warnings
        )
    return toast


def _plugin_options(
    kind: str,
    name: str,
    given: Any,
    where: str,
    palette: dict[str, str],
    root: Path | None,
    warnings: list[str],
) -> dict[str, Any]:
    """A plugin's options with its defaults, checked against its schema."""
    if not isinstance(given, dict):
        raise ThemeError(f"{where}.options must be an object")
    plugin = _plugin(kind, name)
    if plugin is None:
        warnings.append(f"{where} {name!r} needs a plugin that is not installed")
        return deepcopy(given)
    options = {
        key: _coerce(kind_of, given.get(key, deepcopy(default)), f"{where}.options.{key}", palette)
        for key, (kind_of, default) in plugin.options.items()
    }
    _check_paths(plugin.options, options, f"{where}.options", root)
    warnings.extend(
        f"{where}.options: ignoring unknown option {k!r}" for k in sorted(set(given) - set(options))
    )
    return options


def _parse_screen(
    raw: Any, palette: dict[str, str], root: Path | None, warnings: list[str]
) -> dict[str, Any] | None:
    """``"screen": {"name": ..., "options": {...}}``: a plugin draws the whole frame."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ThemeError("screen must be an object with a name")
    name = _coerce("string", raw.get("name", ""), "screen.name")
    if not name:
        raise ThemeError("screen.name: missing")
    given = raw.get("options", {}) or {}
    options = _plugin_options("screens", name, given, "screen", palette, root, warnings)
    return {"name": name, "options": options}


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
    raw_palette = data.get("palette", {}) or {}
    if not isinstance(raw_palette, dict):
        raise ThemeError("palette must be an object of name: color")
    palette: dict[str, str] = {}
    for name, color in raw_palette.items():
        if not isinstance(name, str) or not _PALETTE_NAME.match(name):
            raise ThemeError(f"palette: invalid name {name!r} (use a-z, 0-9, - and _)")
        palette[name] = _check_color(color, f"palette.{name}", False)  # no references here
    font = _check_font(
        _coerce("string", data.get("font", DEFAULT_FONT), "font") or DEFAULT_FONT, "font", root
    )
    animation = data.get("animation", {}) or {}
    smoothing_ms = _coerce("int", animation.get("smoothing_ms", 400), "animation.smoothing_ms")
    if not 0 <= smoothing_ms <= 5000:
        raise ThemeError("animation.smoothing_ms: must be between 0 and 5000")
    background = data.get("background", {}) or {}
    bg_color = _check_color(background.get("color", "#000000"), "background.color", False, palette)
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
        widget, w = normalize_widget(raw, i, palette, root)
        if widget["id"] in seen:
            raise ThemeError(f"widgets[{i}]: duplicate id {widget['id']!r}")
        seen.add(widget["id"])
        if widget.get("font"):
            _check_font(widget["font"], f"widgets[{i}].font", root)
        if root is not None and widget.get("src"):
            resolve_asset(root, widget["src"])
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
        palette=palette,
        font=font,
        smoothing_ms=smoothing_ms,
        widgets=widgets,
        screen=_parse_screen(data.get("screen"), palette, root, warnings),
        toast=_parse_toast(data.get("toast"), palette, root, warnings),
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


def plugin_theme_dirs() -> list[Path]:
    """Folders of themes that plugins bring (``libre_panel.themes``)."""
    from libre_panel.plugins.loader import registry

    installed = registry()
    folders = (installed.get("themes", name) for name in installed.names("themes"))
    return [folder for folder in folders if folder is not None and folder.is_dir()]


def _search_dirs() -> list[Path]:
    # User themes first so a user can shadow a plugin's or a built-in theme of
    # the same name; a plugin's shadows a built-in one.
    return [user_themes_dir(), *plugin_theme_dirs(), builtin_themes_dir()]


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
                source = "user" if base == user_themes_dir() else "plugin"
                found[folder.name] = {
                    "id": folder.name,
                    "builtin": source != "user",  # read-only: saved as a copy
                    "source": "built-in" if base == builtin_themes_dir() else source,
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

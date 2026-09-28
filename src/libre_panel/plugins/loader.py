"""Finding and loading plugins.

Two ways to install, same package:

- ``pip install`` into Libre Panel's Python: the package's entry points
  (``[project.entry-points."libre_panel.services"]`` and so on);
- a folder ``<settings>/plugins/<name>/`` with the package and a
  ``plugin.toml`` holding the same entry point table. That is how plugins get
  into the downloads (Windows setup, macOS app, AppImage), which cannot pip
  install. The folder goes onto ``sys.path`` when one of its parts is used.

Libre Panel never downloads or installs code itself.
"""

from __future__ import annotations

import logging
import sys
import tomllib
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from libre_panel.config import config_dir

log = logging.getLogger(__name__)

API_VERSION = 1
PLUGINS_DIRNAME = "plugins"
PLUGIN_FILENAME = "plugin.toml"

# kind -> entry point group
GROUPS = {
    "services": "libre_panel.services",
    "screens": "libre_panel.screens",
    "widgets": "libre_panel.widgets",
    "transitions": "libre_panel.transitions",
    "toasts": "libre_panel.toasts",
    "editor_pages": "libre_panel.editor_pages",
}


class PluginError(ValueError):
    pass


@dataclass
class Found:
    """One registered part of a plugin, loaded on first use."""

    kind: str
    name: str
    target: str  # "module:attribute"
    source: str  # where it came from, for `libre-panel plugins`
    folder: Path | None = None
    loaded: Any = None
    error: str = ""


@dataclass
class Registry:
    parts: dict[str, dict[str, Found]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add(self, found: Found) -> None:
        kind = self.parts.setdefault(found.kind, {})
        if found.name in kind:
            self.errors.append(
                f"{found.kind} {found.name!r} from {found.source} ignored: "
                f"already registered by {kind[found.name].source}"
            )
            return
        kind[found.name] = found

    def names(self, kind: str) -> list[str]:
        return sorted(self.parts.get(kind, {}))

    def all(self) -> list[Found]:
        return [found for kind in sorted(self.parts) for found in self.parts[kind].values()]

    def get(self, kind: str, name: str) -> Any:
        """The plugin class, or None when it is missing or broken (the reason is logged)."""
        found = self.parts.get(kind, {}).get(name)
        if found is None:
            return None
        if found.loaded is None and not found.error:
            try:
                found.loaded = _load(found)
            except Exception as exc:  # a broken plugin must not stop Libre Panel
                found.error = f"{type(exc).__name__}: {exc}"
                log.error("plugin %s %r (%s) failed to load: %s", kind, name, found.source, exc)
        return found.loaded


def _base_class(kind: str) -> type:
    from libre_panel.plugins.host import Service
    from libre_panel.plugins.pages import EditorPage
    from libre_panel.plugins.render import Screen, ToastStyle, Transition, WidgetType

    bases = {"services": Service, "screens": Screen, "widgets": WidgetType}
    drawn = {"transitions": Transition, "toasts": ToastStyle}
    return {**bases, **drawn, "editor_pages": EditorPage}[kind]


def _load(found: Found) -> Any:
    if found.folder is not None and str(found.folder) not in sys.path:
        sys.path.insert(0, str(found.folder))
    module_name, _, attribute = found.target.partition(":")
    if not module_name or not attribute:
        raise PluginError(f"{found.target!r} is not 'module:attribute'")
    module = __import__(module_name, fromlist=[attribute])
    obj = getattr(module, attribute)
    base = _base_class(found.kind)
    if not (isinstance(obj, type) and issubclass(obj, base)):
        raise PluginError(f"{found.target} is not a {base.__name__}")
    api = getattr(obj, "api", None)
    if api != API_VERSION:
        raise PluginError(
            f"{found.target} is written for plugin API {api}; "
            f"this Libre Panel has API {API_VERSION}"
        )
    fields = {"widgets": "spec", "screens": "options", "toasts": "options"}.get(found.kind)
    if fields:
        from libre_panel.theme.model import valid_kind

        for key, declared in getattr(obj, fields).items():
            if not (isinstance(declared, tuple) and len(declared) == 2 and valid_kind(declared[0])):
                raise PluginError(f"{found.target}.{fields}[{key!r}]: unknown field kind")
    if found.kind == "widgets" and "." not in found.name:
        raise PluginError(
            f"widget type {found.name!r} needs a dot (e.g. 'myplugin.ring'), "
            "so it never clashes with a built-in widget"
        )
    return obj


def plugins_dir() -> Path:
    return config_dir() / PLUGINS_DIRNAME


def discover(folder: Path | None = None) -> Registry:
    """Everything installed, without importing any plugin code yet."""
    registry = Registry()
    for kind, group in GROUPS.items():
        for entry in entry_points(group=group):
            dist = entry.dist
            source = f"package {dist.name} {dist.version}" if dist else "package"
            registry.add(Found(kind, entry.name, entry.value, source))
    root = folder or plugins_dir()
    if root.is_dir():
        for plugin in sorted(p for p in root.iterdir() if (p / PLUGIN_FILENAME).is_file()):
            _read_folder(plugin, registry)
    for error in registry.errors:
        log.error("plugins: %s", error)
    return registry


def _read_folder(folder: Path, registry: Registry) -> None:
    source = f"folder {folder}"
    try:
        meta = tomllib.loads((folder / PLUGIN_FILENAME).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        registry.errors.append(f"{source}: {PLUGIN_FILENAME} unreadable ({exc})")
        return
    if meta.get("api") != API_VERSION:
        registry.errors.append(
            f"{source}: written for plugin API {meta.get('api')}; "
            f"this Libre Panel has API {API_VERSION}"
        )
        return
    table = meta.get("entry-points", {})
    kinds = {group: kind for kind, group in GROUPS.items()}
    for group, parts in table.items() if isinstance(table, dict) else []:
        if group not in kinds:
            registry.errors.append(f"{source}: unknown entry point group {group!r}")
            continue
        for name, target in parts.items() if isinstance(parts, dict) else []:
            if isinstance(target, str):
                registry.add(Found(kinds[group], name, target, source, folder=folder))


_registry: Registry | None = None


def registry() -> Registry:
    """The installed plugins (found once per process)."""
    global _registry
    if _registry is None:
        _registry = discover()
    return _registry


def reset_registry() -> None:
    """Look again (tests, and after installing a plugin folder)."""
    global _registry
    _registry = None

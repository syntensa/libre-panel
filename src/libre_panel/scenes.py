"""Scenes: a theme for every panel at once ("Gaming", "Work", "Film"), put on with one click on
Start, a keyboard shortcut (the next one) or the tray.

A scene is kept in config.toml (``[[scenes]]``: its name and the themes, the first panel's
first) and the one that is on in ``scene``; config.parse_config gives the panels its themes.
A theme chosen for a panel by itself afterwards takes the scene off (config.set_active_theme,
Settings → Panels).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from libre_panel.config import (
    MAX_SCENES,
    SCENE_NAME_LENGTH,
    ConfigError,
    edit_config,
    load_config,
)


def _tables(config: Any) -> list[dict[str, Any]]:
    return [{"name": scene.name, "themes": list(scene.themes)} for scene in config.scenes]


def state(path: Path | None = None) -> dict[str, Any]:
    """``{"scenes": [{name, themes}], "on": the name of the one that is on, or ""}``."""
    config = load_config(path)
    on = config.scene if any(scene.name == config.scene for scene in config.scenes) else ""
    return {"scenes": _tables(config), "on": on}


def put_on(name: str, path: Path | None = None) -> dict[str, Any]:
    """Put the scene ``name`` on ("": none; the panels show their own themes again)."""
    config = load_config(path)
    if name and not any(scene.name == name for scene in config.scenes):
        raise ConfigError(f"no scene {name!r}")
    if name:
        changes: list[tuple[str, str, Any]] = [("value", "scene", name)]
        if config.sequence.active:  # a scene, like a theme chosen, stops the screens in turn
            changes.append(("value", "sequence.on", False))
        edit_config(*changes, path=path)
    elif config.scene:
        edit_config(("unset", "scene", None), path=path)
    return state(path)


def next_one(path: Path | None = None) -> str:
    """Put the scene after the one that is on, the first after the last; its name ("": there
    are none)."""
    config = load_config(path)
    names = [scene.name for scene in config.scenes]
    if not names:
        return ""
    at = names.index(config.scene) if config.scene in names else -1
    name = names[(at + 1) % len(names)]
    put_on(name, path)
    return name


def save(name: str, path: Path | None = None) -> dict[str, Any]:
    """Keep what the panels show now as the scene ``name`` (one of that name is replaced),
    and put it on."""
    name = " ".join(str(name).split())
    if not name or len(name) > SCENE_NAME_LENGTH:
        raise ConfigError(f"a scene's name: 1 to {SCENE_NAME_LENGTH} characters")
    config = load_config(path)
    themes = [config.theme if config.theme_given else ""] + [p.theme for p in config.panels]
    tables = [table for table in _tables(config) if table["name"] != name]
    if len(tables) >= MAX_SCENES:
        raise ConfigError(f"at most {MAX_SCENES} scenes")
    tables.append({"name": name, "themes": themes})
    edit_config(("tables", "scenes", tables), ("value", "scene", name), path=path)
    return state(path)


def delete(name: str, path: Path | None = None) -> dict[str, Any]:
    """Take the scene ``name`` out (off, if it was on)."""
    config = load_config(path)
    tables = [table for table in _tables(config) if table["name"] != name]
    changes: list[tuple[str, str, Any]] = [("tables", "scenes", tables)]
    if config.scene == name:
        changes.append(("unset", "scene", None))
    edit_config(*changes, path=path)
    return state(path)

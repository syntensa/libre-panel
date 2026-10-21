"""Modules of one panel's theme sent to another panel (Design → a module's menu → Copy to panel
2, Move to panel 2): the theme that panel shows gets them, each on free cells of its grid.

A theme of the person's own is written as it is; a built-in one (Lumen Companion) gets a copy of
its own first (``lumen-companion-own``), which the panel then shows: what comes with Libre Panel
is never written over. The modules keep their settings and get a new id where the theme has
theirs already.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from libre_panel.config import ConfigError, load_config, user_themes_dir
from libre_panel.devices.models import find_model
from libre_panel.i18n import t
from libre_panel.theme.defaults import default_theme
from libre_panel.theme.model import (
    ThemeError,
    find_theme,
    load_theme,
    save_theme,
    theme_for,
    valid_theme_name,
)
from libre_panel.theme.modules import make_grid

OWN = "-own"  # a built-in theme's copy of the person's own: "lumen-companion-own"


class SendError(ValueError):
    pass


def shown_theme(config: Any, n: int, status: Any = None) -> str:
    """The theme the panel ``[[panels]]`` at ``n`` shows: as it says (``status``, its
    RunStatus), else the one config.toml gives it in the version for its size and way up."""
    seen = getattr(status, "theme", "") or ""
    if seen:
        return seen
    panel = config.panels[n]
    model = find_model(panel.device.model)
    name = panel.theme or default_theme(model)
    way = "portrait" if panel.device.orientation == "portrait" else "landscape"
    return theme_for(name, panel.device.orientation, model.size(way) if model else None)


def _own_copy_name(name: str) -> str:
    """A name for the person's copy of the built-in theme ``name`` that no theme has yet."""
    base = name[: 64 - len(OWN) - 3]
    candidate, number = f"{base}{OWN}", 2
    while (user_themes_dir() / candidate).exists():
        candidate, number = f"{base}{OWN}-{number}", number + 1
    return candidate


def _free_cells(columns: int, rows: int, taken: set[tuple[int, int]], cols: int, height: int):
    """The first place (column, row) where ``cols`` x ``height`` cells are all free."""
    for row in range(rows - height + 1):
        for col in range(columns - cols + 1):
            if all((c, r) not in taken for c in range(col, col + cols)
                   for r in range(row, row + height)):  # fmt: skip
                return col, row
    return None


def place_modules(data: dict[str, Any], modules: list[dict[str, Any]], theme: Any) -> list[str]:
    """Put ``modules`` into the theme ``data`` (its JSON, as ``theme`` was loaded from it), each
    on free cells of its grid, in its own size where that fits, else in a smaller one. The ids
    of those put in; :class:`SendError` when one finds no room."""
    grid = make_grid(theme.width, theme.height, theme.grid, theme.model)
    widgets = data.setdefault("widgets", [])
    taken: set[tuple[int, int]] = set()
    for widget in widgets:
        if widget.get("type") == "module" and all(k in widget for k in ("col", "row")):
            for c in range(widget["col"], widget["col"] + widget.get("cols", 1)):
                for r in range(widget["row"], widget["row"] + widget.get("rows", 1)):
                    taken.add((c, r))
    ids = {widget.get("id") for widget in widgets}
    placed = []
    for module in modules:
        if module.get("type") != "module":
            raise SendError(t("only modules go to another panel"))
        wanted = (min(module.get("cols", 1), grid.columns), min(module.get("rows", 1), grid.rows))
        sizes = [wanted, (max(1, wanted[0] // 2), wanted[1]), (max(1, wanted[0] // 2), 1), (1, 1)]
        spot = None
        for cols, height in sizes:
            spot = _free_cells(grid.columns, grid.rows, taken, cols, height)
            if spot is not None:
                break
        if spot is None:
            name = data.get("name") or theme.name
            raise SendError(t("there are no free cells for it on “{theme}”: make room there in "
                              "Design first", theme=name))  # fmt: skip
        sent = deepcopy(module)
        sent.update(col=spot[0], row=spot[1], cols=cols, rows=height)
        if not sent.get("id") or sent["id"] in ids:
            sent["id"] = f"m-{uuid.uuid4().hex[:8]}"
        ids.add(sent["id"])
        for c in range(spot[0], spot[0] + cols):
            for r in range(spot[1], spot[1] + height):
                taken.add((c, r))
        widgets.append(sent)
        placed.append(sent["id"])
    return placed


def send(
    modules: list[dict[str, Any]], number: int, config_path: Path | None = None, status: Any = None
) -> dict[str, Any]:
    """Send ``modules`` to the panel ``number`` (2 and on, as *Which is which?* numbers them):
    ``{"theme": the theme they went to, "copied": whether it is a copy made now, "ids": the
    modules' ids there}``. A built-in theme gets a copy of its own first, and the panel shows it
    from then on (config.toml's ``[[panels]]``)."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise SendError(str(exc)) from exc
    n = number - 2
    if not 0 <= n < len(config.panels):
        raise SendError(t("there is no panel {number}", number=number))
    name = shown_theme(config, n, status)
    try:
        folder = find_theme(name)
        theme = load_theme(folder)
    except ThemeError as exc:
        raise SendError(str(exc)) from exc
    data = json.loads((folder / "theme.json").read_text(encoding="utf-8"))
    built_in = not folder.resolve().is_relative_to(user_themes_dir().resolve())
    target = _own_copy_name(name) if built_in else name
    if built_in:
        data["name"] = t("{name} (mine)", name=data.get("name") or name)
    ids = place_modules(data, modules, theme)
    if not valid_theme_name(target):
        raise SendError(f"invalid theme name {target!r}")
    try:
        save_theme(target, data, source=name if built_in else None)
    except (ThemeError, OSError) as exc:
        raise SendError(str(exc)) from exc
    if built_in:  # the panel shows the copy from now on
        from libre_panel.editor.settings import Restart, Settings

        settings = Settings(config.path, None, Restart())
        panels = settings.read()["config"]["panels"]
        panels[n] = {**panels[n], "theme": target}
        settings.write({"set": {"panels": panels}})
    return {"theme": target, "copied": built_in, "ids": ids}

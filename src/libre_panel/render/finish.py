"""Finishes: how a widget's shape is drawn, in the same place and size.

A widget's ``finish`` (theme field) is ``"flat"``, the renderer's own drawing,
or the name of a finish registered here: ``"lit"`` (render.lit, SPUR II's
light) or ``"facet"``. Rects with a ``ridge`` name are drawn by render.look's
ridges. A finish draws some widget types;
for any other type, or a widget it cannot draw (``accepts``), the widget is
drawn flat and the theme loader warns once (theme.model).

A finish is code, not data: subclass :class:`Finish`, set ``name`` and
``types``, and :func:`register` an instance. Built-in finishes are imported
the first time a theme names them, so a theme without one loads nothing.
"""

from __future__ import annotations

import importlib
from typing import Any

from libre_panel.theme.model import AUTO, FINISHES

FLAT = FINISHES[0]
# The types a module hands its finish to (theme.modules): its rings, bars,
# graphs, icons and texts are drawn with it where the finish has them.
PART_TYPES = ("bar", "gauge", "graph", "icon", "symbol", "downloads", "text", "metric")
# Built-in finishes: the modules that register them when they are imported.
_BUILT_IN = {
    "lit": ("libre_panel.render.lit", "libre_panel.render.symbols", "libre_panel.render.download"),
    "facet": ("libre_panel.render.facet",),
    "acrylic": ("libre_panel.render.facet",),
}


class Finish:
    """One finish for some widget types.

    ``draw`` draws the whole widget, as the renderer's ``_draw_<type>`` does
    (it may hand a widget back to the flat drawing, e.g. one too thin to
    look lit). A graph's finish may leave the graph to the renderer and only
    draw its line instead (``curve``). ``prepare`` builds, away from the
    render thread, what the widget's first frame needs (Renderer.warm): a
    first frame that loads layers stalls the panel.
    """

    name = ""
    types: tuple[str, ...] = ()

    def accepts(self, widget: dict[str, Any]) -> bool:
        """Whether this finish can draw ``widget`` (else it is drawn flat)."""
        return True

    def widget(self, widget: dict[str, Any]) -> dict[str, Any]:
        """The widget as the renderer keeps it (once, when a renderer starts):
        a copy with fields moved aside, or the widget itself."""
        return widget

    def draw(self, renderer: Any, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        raise NotImplementedError  # not overridden: the renderer draws the widget itself

    def curve(self, renderer: Any, widget: dict[str, Any], layer: Any, runs: list, color: Any,
              scale: int) -> bool:  # fmt: skip
        """Draw a graph's line through ``runs`` (lists of points at ``scale``)
        onto ``layer``; False leaves it to the renderer."""
        return False

    def lettering(self, renderer: Any, widget: dict[str, Any], font: Any, text: str) -> bool:
        """Whether ``text`` of a text widget, in ``font``, is laid out by the renderer and
        drawn letter by letter by :meth:`glyph` (else it is drawn flat)."""
        return False

    def glyph(self, renderer: Any, widget: dict[str, Any], font: Any, ch: str, fill: Any,
              at: float) -> tuple[Any, int, int] | None:  # fmt: skip
        """The letter ``ch`` with the pen ``at`` (its fraction of a pixel) from the
        pixel it stands on: ``(image, left, top)`` with ``left`` and ``top`` from that
        pixel and the line's top, or None for a blank. Only after :meth:`lettering`."""
        return None

    def prepare(self, renderer: Any, widget: dict[str, Any]) -> None:
        """Load and build what drawing ``widget`` needs (any thread)."""


_REGISTERED: dict[tuple[str, str], Finish] = {}
_imported: set[str] = set()


def register(finish: Finish) -> None:
    """Make ``finish`` available for its ``types`` (the last one registered wins)."""
    for widget_type in finish.types:
        _REGISTERED[(finish.name, widget_type)] = finish


def get(name: str, widget_type: str) -> Finish | None:
    """The finish ``name`` for widgets of ``widget_type``, or None (drawn flat)."""
    if name not in _imported and name in _BUILT_IN:
        _imported.add(name)
        for module in _BUILT_IN[name]:
            importlib.import_module(module)
    return _REGISTERED.get((name, widget_type))


def supports(widget: dict[str, Any]) -> bool:
    """Whether ``widget`` is drawn with the finish it names (flat always is)."""
    name = widget.get("finish", FLAT)
    if name in (FLAT, AUTO):
        return True
    if widget["type"] == "module":
        return any(get(name, part) is not None for part in PART_TYPES)
    finish = get(name, widget["type"])
    return finish is not None and finish.accepts(widget)


def attach(renderer: Any) -> dict[str, Finish]:
    """The finishes of a renderer's widgets that are not flat, by widget id;
    the renderer's widget list gets the copies they draw from."""
    found: dict[str, Finish] = {}
    for i, widget in enumerate(renderer.widgets):
        name = widget.get("finish", FLAT)
        if name == FLAT:
            continue
        finish = get(name, widget["type"])
        if finish is None or not finish.accepts(widget):
            continue  # drawn flat: the theme loader has warned (module parts quietly)
        renderer.widgets[i] = finish.widget(widget)
        found[widget["id"]] = finish
    if any(w["type"] == "rect" and w.get("ridge") for w in renderer.widgets):
        from libre_panel.render.look import ridges  # rects that are ridges, by name

        found.update(ridges(renderer))
    return found


def draws(finish: Finish) -> bool:
    """Whether ``finish`` draws whole widgets (else only parts, e.g. a graph's line)."""
    return type(finish).draw is not Finish.draw

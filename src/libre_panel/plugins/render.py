"""Screens, widget types, transitions and toast styles: plugin code that draws.

Themes stay data: a theme names a screen or a widget type, the code comes
from an installed plugin. Options and widget fields are declared with the
same field kinds built-in widgets use (``int``, ``number``, ``bool``,
``string``, ``color``, ``color?``, ``font``, ``sensor``, ``enum:a|b``, ...),
so the theme loader checks them and the editor's inspector can edit them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PIL import Image

from libre_panel import i18n
from libre_panel.devices.models import find_model


class RenderContext:
    """What screens and widget types get from the renderer."""

    def __init__(self, renderer: Any, plugin: type | None = None) -> None:
        self._renderer = renderer
        theme = renderer.theme
        self.width, self.height = theme.width, theme.height
        self.size = (theme.width, theme.height)
        self.orientation = theme.orientation
        self.palette = dict(theme.palette)
        self.model = find_model(theme.model)
        self.assets: Path | None = None  # the plugin's own folder, for pre-rendered files
        module = sys.modules.get(plugin.__module__) if plugin is not None else None
        if module is not None and getattr(module, "__file__", None):
            self.assets = Path(module.__file__).resolve().parent

    @property
    def fps(self) -> int:
        """The frame rate the panel runs at (it changes with modes)."""
        return self._renderer.fps

    @property
    def continuous(self) -> bool:
        """True in video mode: every frame reaches the panel, so motion shows
        between readings (the built-in graphs scroll on every frame)."""
        return self._renderer.continuous

    def progress(self, now: float) -> float:
        """How far ``now`` is from the last reading to the next, 0 to 1: move a
        curve by this part of a sample width to scroll it between readings."""
        return self._renderer.progress(now)

    @property
    def language(self) -> str:
        return i18n.language()

    def t(self, text: str, **values: Any) -> str:
        return i18n.t(text, **values)

    def hidden_edges(self) -> dict[str, int]:
        """Pixels the panel's bezel hides per edge (data only; nothing is cropped)."""
        if self.model is None:
            return dict.fromkeys(("top", "right", "bottom", "left"), 0)
        return self.model.hidden_edges(self.orientation)

    def font(self, ref: str = "", size: int = 24) -> Any:
        """A font by theme reference (``builtin:Barlow-Bold``, a file in the theme, or
        empty for the theme's font)."""
        return self._renderer.font(ref or self._renderer.theme.font, size)

    def color(self, value: str | None, alpha_scale: float = 1.0) -> tuple[int, int, int, int]:
        """A colour value (``#rrggbb``, ``#rrggbbaa`` or ``@name`` from the palette) as RGBA."""
        return self._renderer.color(value, alpha_scale) or (0, 0, 0, 0)

    @property
    def supersample(self) -> int:
        """Draw this much larger, then :meth:`downsample`: smooth edges."""
        from libre_panel.render.renderer import SUPERSAMPLE

        return SUPERSAMPLE

    def downsample(self, layer: Image.Image, width: int, height: int) -> Image.Image:
        return self._renderer._down(layer, width, height)

    def keep_moving(self) -> None:
        """Ask for the next frame at full rate (something is animating)."""
        self._renderer.moving = True


class Screen:
    """Draws the whole frame in code; the theme's widgets are drawn on top.

    Set ``name``, ``api = 1``, optionally ``label`` ({"en": ..., "de": ...}) and
    ``options`` ({key: (field kind, default)}). ``render`` runs on the render
    thread at up to 50 fps and returns an RGB or RGBA image of ``context.size``.
    ``__init__`` also runs for every preview in the editor: keep expensive
    preparation in a module-level cache.
    """

    name = ""
    label: dict[str, str] = {}
    options: dict[str, tuple[str, Any]] = {}
    moving = False  # True while it animates (the PNG path then draws at full fps)
    suppresses: frozenset[str] = frozenset()  # toast kinds it shows anyway, e.g. {"music"}

    def __init__(self, context: RenderContext, options: dict[str, Any]) -> None:
        self.context = context
        self.options = options

    def render(self, snapshot: Any, now: float) -> Image.Image:
        raise NotImplementedError

    def close(self) -> None:
        pass


class WidgetType:
    """A widget the editor can place, drawn by plugin code.

    Set ``type`` (with a dot, e.g. ``"myplugin.ring"``, so it never clashes with
    a built-in), ``api = 1``, ``label``, ``spec`` ({field: (kind, default)}; the
    common fields x, y, id, opacity, glow, shadow, ... come automatically) and
    optionally ``presets`` for the editor's building blocks.
    """

    type = ""
    label: dict[str, str] = {}
    spec: dict[str, tuple[str, Any]] = {}
    presets: list[dict[str, Any]] = []

    def key(self, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        """What the picture depends on: an unchanged key reuses the last picture.
        The default is the value of the widget's ``sensor`` field, if it has one."""
        sensor = widget.get("sensor")
        return snapshot.value(sensor) if sensor else None

    def draw(
        self, widget: dict[str, Any], ctx: RenderContext, snapshot: Any, now: float
    ) -> Image.Image | tuple[Image.Image, tuple[int, int]] | None:
        """An RGBA image placed at the widget's x, y (or at the returned position)."""
        raise NotImplementedError


class Transition:
    """A way from one theme (or mode) to the next (built in: cut, fade, slide).

    Set ``name``, ``api = 1`` and ``duration`` in seconds. A new instance is
    made for every switch, with the new theme's ``context`` (palette, fonts,
    size) and the ``params`` the service passed (``show_theme(name,
    transition=("myplugin.entrance", {...}))``, ``{}`` otherwise); set
    ``self.duration`` in ``__init__`` if it depends on them. ``frame`` gets
    the last frame of the old theme, the current frame of the new one (same
    size, RGB) and ``t`` from 0 to 1, and returns the frame to show. While it
    plays, further switches and new toasts wait.
    """

    name = ""
    duration = 0.4

    def __init__(
        self, context: RenderContext | None = None, params: dict[str, Any] | None = None
    ) -> None:
        self.context = context
        self.params = dict(params or {})

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        raise NotImplementedError


class ToastStyle:
    """How toasts look; a theme picks one with ``"toast": {"style": name}``.

    Set ``name``, ``api = 1``, optionally ``label``, ``options`` ({key: (field
    kind, default)}, set in the theme's ``toast.options``) and ``leave_s``,
    the time a toast takes to leave after its hold time. ``draw`` puts one
    toast (:class:`~libre_panel.plugins.Toast`: text, icon, level, kind,
    payload, ...) on the finished frame and returns it; ``age`` runs from 0,
    when it arrives, to ``toast.seconds + leave_s``. It runs at up to 50 fps:
    keep what does not change in ``self``.
    """

    name = ""
    label: dict[str, str] = {}
    options: dict[str, tuple[str, Any]] = {}
    leave_s = 0.25

    def __init__(self, context: RenderContext, options: dict[str, Any]) -> None:
        self.context = context
        self.options = options

    def draw(self, frame: Image.Image, toast: Any, age: float) -> Image.Image:
        raise NotImplementedError

    def close(self) -> None:
        pass


def label_for(cls: type) -> str:
    """The plugin's label in the current language (English, else its name)."""
    labels = getattr(cls, "label", {}) or {}
    name = getattr(cls, "name", "") or getattr(cls, "type", "")
    return labels.get(i18n.language()) or labels.get("en") or name

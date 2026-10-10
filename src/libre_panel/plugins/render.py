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
    def preview(self) -> bool:
        """True in an editor preview (or ``libre-panel render``): not the panel. Leave
        what the panel's drawing keeps between frames (histories, smoothing) alone."""
        return self._renderer.preview

    @property
    def shown(self) -> Image.Image | None:
        """The frame the panel showed last, after transitions and toasts (in the
        panel's size), or None; e.g. for particles that start from what is on it."""
        return self._renderer.shown

    @property
    def toast(self) -> tuple[Any, float] | None:
        """The toast on the panel and its age in seconds, or None (screens can
        react the moment one begins)."""
        return self._renderer.toast

    @property
    def continuous(self) -> bool:
        """True in video mode: every frame reaches the panel, so motion shows
        between readings (the built-in graphs scroll on every frame)."""
        return self._renderer.continuous

    def progress(self, now: float) -> float:
        """How far ``now`` is from the last reading to the next, 0 to 1: move a
        curve by this part of a sample width to scroll it between readings."""
        return self._renderer.progress(now)

    def image(self, key: str) -> Image.Image | None:
        """An image a service or sensor source published (``"media.cover"``), as
        of the readings the panel was last drawn from, or None."""
        snapshot = getattr(self._renderer, "snapshot", None)
        return (getattr(snapshot, "images", None) or {}).get(key)

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
    thread at up to 50 fps and returns an RGB or RGBA image of ``context.size``;
    do not change an image after returning it (return the same object again
    when nothing changed). An RGB image with no widgets on top goes to the
    panel as it is. ``__init__`` also runs for every preview in the editor
    (``context.preview``): keep expensive preparation in a module-level cache.
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
    """A way from one theme (or mode) to the next (built in: cut, fade, slide,
    tube, entrance).

    Set ``name``, ``api = 1`` and ``duration`` in seconds. A new instance is
    made for every switch, with the new theme's ``context`` (palette, fonts,
    size) and the ``params`` the service passed (``show_theme(name,
    transition=("myplugin.entrance", {...}))``, ``{}`` otherwise); set
    ``self.duration`` in ``__init__`` if it depends on them. ``frame`` gets
    the last frame of the old theme, the current frame of the new one (same
    size, RGB) and ``t`` from 0 to 1, and returns the frame to show.

    ``switch`` says what a switch that comes while it plays does: ``"wait"``
    (the default) holds it until the end; ``"follow"`` shows the new theme at
    once and this transition keeps playing, uncovering it (an entrance that
    reveals the screen asked for meanwhile); ``"restart"`` starts the new
    switch's transition from the frame that shows now. A mode change that
    brings a transition of its own never waits behind a following one: it
    starts from the frame that shows now. :meth:`on_switch` can decide case
    by case.

    ``toasts`` says what toasts do meanwhile: ``"wait"`` (the default) lets
    the one on show stay and new ones wait; ``"over"`` lets them go on over
    the transition; ``"restart"`` makes the one on show leave and come again
    afterwards; ``"hide"`` decides them as usual (screens see them in
    ``context.toast``) but draws none, and the one on show rolls in afresh
    on the frame the transition ends. ``from_shown = True`` hands the old
    frame over as it showed, with its toast.
    """

    name = ""
    duration = 0.4
    switch = "wait"
    toasts = "wait"  # "wait", "over", "restart" (leave, come again) or "hide"
    from_shown = False  # True: the old frame with the toast on it, as it showed

    def __init__(
        self, context: RenderContext | None = None, params: dict[str, Any] | None = None
    ) -> None:
        self.context = context
        self.params = dict(params or {})

    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image:
        raise NotImplementedError

    def on_switch(self, theme: str, mode: str | None, mode_changed: bool) -> str:
        """What a switch does while this plays: ``"wait"``, ``"follow"`` or
        ``"restart"`` (see ``switch``, the default). Asked on every frame it
        plays, with the theme the panel should show and the mode on now;
        ``mode_changed`` is True when that is not the mode it began in (a game
        that ended during its own entrance)."""
        return self.switch


class ToastStyle:
    """How toasts look; a theme picks one with ``"toast": {"style": name}``
    (built in: card, band).

    Set ``name``, ``api = 1``, optionally ``label``, ``options`` ({key: (field
    kind, default)}, set in the theme's ``toast.options``) and ``leave_s``,
    the time a toast takes to leave after its hold time. ``draw`` puts one
    toast (:class:`~libre_panel.plugins.Toast`: text, icon, level, kind,
    payload, ...) on the finished frame and returns it; ``age`` runs from 0,
    when it arrives, to :meth:`lifetime`. ``previous`` is the toast this one
    replaced at once (the same key again, or one it took over from) and that
    one's age then, or None: change the content in place instead of rolling
    in again. It runs at up to 50 fps: keep what does not change in ``self``.
    ``self.anchor`` is the theme's toast position.

    The instance stays across theme switches while the theme names the same
    style with the same options at the same size, so what it keeps per toast
    goes on; ``self.context`` then becomes the new theme's.
    """

    name = ""
    label: dict[str, str] = {}
    options: dict[str, tuple[str, Any]] = {}
    leave_s = 0.25

    def __init__(self, context: RenderContext, options: dict[str, Any]) -> None:
        self.context = context
        self.options = options

    def draw(self, frame: Image.Image, toast: Any, age: float, previous: Any = None) -> Image.Image:
        raise NotImplementedError

    def lifetime(self, toast: Any, previous: Any) -> float:
        """Seconds from its arrival until ``toast`` is gone, asked once when it
        starts (``previous`` as for :meth:`draw`). A toast that continues
        another (the volume while it turns) may end sooner than one that
        rolls in afresh."""
        return toast.seconds + self.leave_s

    def hold_s(self, toast: Any, theme_seconds: float) -> float:
        """How long a toast without its own ``seconds`` holds, asked before
        anything else sees it; the default is the theme's hold time."""
        return theme_seconds

    def close(self) -> None:
        pass


def label_for(cls: type) -> str:
    """The plugin's label in the current language (English, else its name)."""
    labels = getattr(cls, "label", {}) or {}
    name = getattr(cls, "name", "") or getattr(cls, "type", "")
    return labels.get(i18n.language()) or labels.get("en") or name

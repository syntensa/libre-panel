"""The app's pictures, drawn from what can go to another process: every theme's picture (Start's
list, Design's sizes), Design's and Look's picture of the theme, the building blocks' and the
modules' pictures, the looks of the weather symbols and the fans, and the start screen's.

Each is a function of what it is given (the person's choices as config.toml has them, a
theme's folder or its JSON, the readings for live values): editor/pictures.py runs them in a
process of their own while the app is open, or here (a plain ``libre-panel editor``, the
tests). The pictures are the same either way, pixel for pixel. A picture for a list (a
building block, a module) takes ``turn`` for each of its parts: those of a list are drawn a
few at a time (editor/server.py, preview_slot_count)."""

from __future__ import annotations

import base64
import contextlib
import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from libre_panel import i18n
from libre_panel.config import DEFAULT_CARDS, FAN_PAINTS, SYMBOL_PACKS
from libre_panel.editor.presets import presets_for_editor
from libre_panel.render import fan, fan_looks
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import laid_out
from libre_panel.theme.model import (
    ThemeError,
    load_theme,
    parse_theme,
    switches_for,
    with_accent,
    with_card_opacity,
    with_light_colours,
)
from libre_panel.theme.modules import MODULE_KINDS, look_theme_parts, make_grid, with_cards

log = logging.getLogger(__name__)

NOTICE_AGE_S = 1.2  # how far into its life a preview shows a notice: drawn in full


class BadRequest(ValueError):
    """What was asked for cannot be drawn (the app hears why, as a 400)."""


def plugin_specs() -> dict[str, Any]:
    """Installed plugin widget types, screens and toast styles (and the built-in
    styles besides the card), for the inspector and the building blocks."""
    from libre_panel.plugins.loader import registry
    from libre_panel.plugins.render import editor_specs, label_for
    from libre_panel.render.overlays import BUILT_IN_STYLES

    told = editor_specs(registry())
    toasts = {}
    for name, cls in BUILT_IN_STYLES.items():  # the card is the editor's own first choice
        if name != "card":
            label = i18n.t("{name} (built in)", name=label_for(cls))
            toasts[name] = {"label": label, "options": {k: list(v) for k, v in cls.options.items()}}
    told["toasts"] = {**toasts, **told["toasts"]}
    return told


def _png(frame: Any) -> bytes:
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()


def draw_theme(
    theme: Any, width: int, accent: str | None, symbols: str, chosen: tuple[tuple[str, bool], ...],
    cards: tuple[str, ...] = DEFAULT_CARDS, card_opacity: int = 0,
    size: tuple[int, int] | None = None, light: bool = False,
) -> bytes:  # fmt: skip
    """A theme as a PNG ``width`` wide at most, as the panel shows it with those choices (on a
    panel of ``size``: scaled into it where it is not laid out on it; ``light``: in light
    colours)."""
    dressed = with_light_colours(with_accent(theme, accent), light)
    dressed = with_card_opacity(dressed, card_opacity)
    renderer = Renderer(with_cards(dressed, cards), preview=True)
    renderer.symbols = symbols
    renderer.switches = dict(chosen)
    try:
        frame, _boxes = renderer.render(demo_snapshot(fixed_time=1_700_000_000))
    finally:
        renderer.close()
    frame = frame.convert("RGB")
    if size is not None and frame.size != tuple(size):  # (as the panel's loop fits it)
        frame = ImageOps.pad(frame, size, method=Image.Resampling.LANCZOS, color="black")
    if frame.width > width:
        frame = frame.resize((width, max(1, round(frame.height * width / frame.width))))
    return _png(frame)


def theme_preview(
    folder: str, size: tuple[int, int] | None, width: int, accent: str | None, symbols: str,
    chosen: tuple[tuple[str, bool], ...], cards: tuple[str, ...], card_opacity: int, light: bool,
) -> bytes:  # fmt: skip
    """The picture of the saved theme in ``folder`` (laid out on a panel of ``size`` where one
    is named): :func:`draw_theme`. ThemeError where it does not load."""
    theme = load_theme(Path(folder))
    if size is not None:
        theme = laid_out(theme, size)
    return draw_theme(theme, width, accent, symbols, chosen, cards, card_opacity, size, light)


def with_notice(renderer: Renderer, frame: Any, kind: str, look: str = "") -> Any:
    """``frame`` with the test message of ``kind`` (the Control page's: volume, music, ...)
    drawn on it in the theme's toast style (the volume in ``look``, where one is named), a
    moment after it began; None for an unknown kind. The previews of the editor have no
    messages of their own."""
    from libre_panel.pages.control import probes
    from libre_panel.plugins.host import Toast
    from libre_panel.render.overlays import ToastLayer

    made = probes(look).get(kind)
    if made is None:
        return None
    _name, options = made
    toast = Toast(
        options["text"],
        options.get("icon"),
        options.get("level", "info"),
        options.get("seconds"),
        options.get("kind", ""),
        options.get("rank", 0),
        dict(options.get("payload", {})),
        "editor",
    )
    layer = ToastLayer(renderer)
    try:
        layer.add([toast])
        layer.advance(0.0)
        return layer.draw(frame, NOTICE_AGE_S)
    finally:
        layer.close()


Live = tuple[dict[str, Reading], dict[str, list[float]]]  # the readings and their history


def render(
    raw: Any, root: str | None, cards: tuple[str, ...], fan_choices: Any, symbols: str,
    every_switch: dict[str, dict[str, bool]], live: Live | None, notice: Any = None,
    look: Any = None,
) -> dict[str, Any]:  # fmt: skip
    """Design's and Look's picture of the theme ``raw`` (its JSON; ``root``: the folder its
    assets come from), with the demo values and, ``live``, the readings and their history over
    them; with the test message ``notice`` on it. What /api/render answers: the PNG (base64),
    the boxes, the warnings, the grid and the modules' boxes. ThemeError for a theme that does
    not parse, BadRequest for a notice that is no test message's kind."""
    theme = parse_theme(raw, root=Path(root) if root else None)
    snapshot = demo_snapshot()
    if live is not None:
        readings, history = live
        snapshot.readings.update(readings)
        snapshot.history.update(history)
    renderer = Renderer(with_cards(theme, cards), preview=True)
    renderer.fan_choices = fan_choices
    renderer.symbols = symbols
    renderer.switches = switches_for(theme, every_switch)
    try:
        frame, boxes = renderer.render(snapshot)
        if notice:  # a test message on it, as the theme's toast style shows it
            style = look if isinstance(look, str) else ""
            shown = with_notice(renderer, frame, notice, style) if isinstance(notice, str) else None
            if shown is None:
                raise BadRequest("notice must be the kind of a test message")
            frame = shown
    finally:
        renderer.close()  # plugin screens end with the preview
    has_modules = any(w["type"] == "module" for w in theme.widgets)
    grid = make_grid(theme.width, theme.height, theme.grid, theme.model)
    return {
        "png": base64.b64encode(_png(frame)).decode("ascii"),
        "boxes": boxes,
        "width": theme.width,
        "height": theme.height,
        "warnings": theme.warnings + renderer.warnings,
        # the grid modules sit on (active once the theme has modules or a grid), and every
        # module's cells, also of hidden ones
        "grid": grid.to_dict(),
        "grid_active": has_modules or theme.grid is not None,
        "module_boxes": renderer.module_boxes,
    }


def module_previews(palette: Any, style: Any, symbols: str, turn: Any = None) -> dict[str, str]:
    """Each module kind in a 2x1 box with the given look, for the library (PNGs, base64).
    ThemeError where a module's theme does not parse."""
    palette = palette or look_theme_parts("arctic")[0]
    style = style or look_theme_parts("arctic")[1]
    sources = {"ring": "cpu", "stat": "gpu", "graph": "cpu"}
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    previews = {}
    for kind in MODULE_KINDS:
        theme = parse_theme(
            {
                "format": "libre-panel-theme/1",
                "display": {"width": 460, "height": 220},
                "palette": palette,
                "style": style,
                "background": {"color": "#000000"},
                "grid": {"columns": 2, "rows": 1, "gap": 10, "margin": 10},
                "widgets": [
                    {"type": "module", "id": "m", "module": kind, "cols": 2,
                     "source": sources.get(kind, "cpu")}
                ],
            }
        )  # fmt: skip
        with turn or contextlib.nullcontext():  # (a turn at a time, as the themes' pictures)
            renderer = Renderer(theme, preview=True)
            renderer.symbols = symbols
            try:
                frame, _boxes = renderer.render(snapshot)
                x, y, w, h = renderer.module_boxes["m"]
            finally:
                renderer.close()
        previews[kind] = base64.b64encode(_png(frame.crop((x, y, x + w, y + h)))).decode("ascii")
    return previews


def preset_previews(palette: Any, symbols: str, turn: Any = None) -> dict[str, str]:
    """Each building block (presets.py and plugins') drawn in the given palette, for the
    library (PNGs, base64)."""
    palette = palette if isinstance(palette, dict) else {}
    specs = presets_for_editor()
    tokens = specs["tokens"]

    def resolve(value: Any) -> Any:  # "$accent": the theme's colour, else the default
        if isinstance(value, str) and value.startswith("$"):
            name = value[1:]
            return f"@{name}" if name in palette else tokens.get(name, "#ffffff")
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        return value

    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    previews = {}
    pad = 8
    for preset in specs["presets"] + plugin_specs()["presets"]:
        width, height = (max(16, int(v) + 2 * pad) for v in preset["size"])
        widgets = [
            {**raw, "x": raw.get("x", 0) + pad, "y": raw.get("y", 0) + pad}
            for raw in resolve(preset["widgets"])
        ]
        try:
            theme = parse_theme(
                {
                    "format": "libre-panel-theme/1",
                    "display": {"width": width, "height": height},
                    "palette": palette,
                    "background": {"color": "@bg" if "bg" in palette else "#0b1016"},
                    "widgets": widgets,
                }
            )
        except ThemeError as exc:
            log.debug("no preview for %s: %s", preset["id"], exc)
            continue
        with turn or contextlib.nullcontext():  # (a turn at a time, as the themes' pictures)
            renderer = Renderer(theme, preview=True)
            renderer.symbols = symbols
            try:
                frame, _boxes = renderer.render(snapshot)
            finally:
                renderer.close()
        previews[preset["id"]] = base64.b64encode(_png(frame)).decode("ascii")
    return previews


def fan_look(look: str, paint: str) -> bytes:
    """A fan of that look as a picture (PNG), in its square frame and the app's accent: the
    tiles of Settings show them. BadRequest for a look or colours there are not."""
    if look not in fan_looks.LOOKS or paint not in FAN_PAINTS:
        raise BadRequest("unknown look or colours")
    rotor = fan.Rotor("preview", fan_looks.look(look).blades)
    rotor.settle(1800.0, 3000.0)  # a fan that runs: lit ...
    rotor.speed = 0.0  # ... drawn standing, its blades sharp
    image = fan.fan_image((34, 211, 238), 112, 112, rotor, "square", look, paint)
    return _png(image)


def symbols_look(pack: str) -> bytes:
    """The weather symbols and the moon in that look as a picture (PNG), on a panel's dark
    ground: the tiles of Settings show them. The sun behind a cloud and snow, lit, by day, and
    the moon waxing. BadRequest for a look there is not."""
    if pack not in SYMBOL_PACKS:
        raise BadRequest("unknown look")
    k = 120
    theme = parse_theme({
        "format": "libre-panel-theme/1",
        "display": {"width": 3 * k, "height": k},
        "palette": look_theme_parts("arctic")[0],
        "background": {"color": "#0b1016"},
        "widgets": [
            {"type": "symbol", "id": "sun", "x": 0, "y": 0, "size": k, "finish": "lit",
             "sensor": "weather.code"},
            {"type": "symbol", "id": "snow", "x": k, "y": 0, "size": k, "finish": "lit",
             "sensor": "weather.hour.1.code"},
            {"type": "moon", "id": "moon", "x": 2 * k + 10, "y": 10, "size": k - 20},
        ],
    })  # fmt: skip
    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    for key, value in (("weather.code", 2), ("weather.is_day", 1), ("moon.phase", 0.36),
                       ("weather.hour.1.code", 73), ("weather.hour.1.day", 1)):  # fmt: skip
        snapshot.readings[key] = Reading(key, value)
    renderer = Renderer(theme, preview=True)
    renderer.symbols = pack
    try:
        frame, _boxes = renderer.render(snapshot)
    finally:
        renderer.close()
    return _png(frame)


def start_screen_preview(model_id: str, what: str, width: int, way: str, design: str) -> bytes:
    """The app's picture of the boot logo (PNG) or of the standby clip (animated):
    devices/panel_files.py's, kept there for the next ask."""
    from libre_panel.devices import panel_files

    return panel_files.cached_preview(model_id, what, width, way, design)


def start_screen_file(model_id: str, what: str, way: str, design: str, full: bool) -> bytes:
    """The boot logo's JPEG or the standby clip as the panel takes them: devices/panel_files.py's.
    DeviceError where it cannot be made (no ffmpeg for the clip)."""
    from libre_panel.devices import panel_files

    return panel_files.cached_file(model_id, what, way, design, full)


def forget() -> None:
    """Let go of what the pictures here keep for the next ask (the start screen's)."""
    from libre_panel.devices import panel_files
    from libre_panel.render import bootscreen

    for cached in (panel_files.cached_preview, panel_files.cached_file, bootscreen._look,
                   bootscreen._ends, bootscreen._dither):  # fmt: skip
        cached.cache_clear()

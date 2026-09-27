"""Ready-made widget groups ("building blocks") for the editor.

Coordinates are relative to the block's top-left corner. Colours written as
``$name`` are tokens: the editor uses the theme's palette entry of the same
name when it exists (``@name``) and the default below otherwise, so a block
dropped into a theme picks up that theme's colours.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

TOKEN_DEFAULTS = {
    "accent": "#22D3EE",
    "accent_deep": "#0B6C85",
    "text": "#F1F5F9",
    "muted": "#8793A6",
    "dim": "#4A5568",
    "card": "#101722",
    "card2": "#0B1018",
    "edge": "#1B2534",
    "good": "#34D399",
    "up": "#F472B6",
    "warn": "#FBBF24",
    "crit": "#F87171",
}
TEMP_RULES = [{"above": 75, "color": "$warn"}, {"above": 88, "color": "$crit"}]


def _text(id, x, baseline, text, size, color, **kw):
    """Text placed by its baseline (Barlow: ascent == font size)."""
    widget = {"type": "text", "id": id, "x": x, "y": baseline - size, "text": text}
    return {**widget, "font_size": size, "color": color, **kw}


def _metric(id, x, baseline, sensor, fmt, size, color, **kw):
    widget = {"type": "metric", "id": id, "x": x, "y": baseline - size, "sensor": sensor}
    return {**widget, "format": fmt, "font_size": size, "color": color, **kw}


PRESETS: list[dict[str, Any]] = [
    {
        "id": "ring",
        "name": "Ring with value",
        "size": [140, 172],
        "widgets": [
            {
                "type": "gauge",
                "id": "ring",
                "x": 0,
                "y": 0,
                "w": 140,
                "h": 140,
                "sensor": "cpu.load",
                "thickness": 12,
                "color": "$accent_deep",
                "color2": "$accent",
                "background": "#ffffff10",
                "glow": 0.5,
                "glow_radius": 7,
            },
            _metric(
                "value",
                70,
                84,
                "cpu.load",
                "{value:.0f}%",
                32,
                "$text",
                align="center",
                font="builtin:Barlow-SemiBold",
            ),
            _text("label", 70, 168, "CPU", 13, "$muted", align="center", letter_spacing=3),
        ],
    },
    {
        "id": "bar-row",
        "name": "Bar row",
        "size": [320, 30],
        "widgets": [
            _text("label", 0, 18, "TEMP", 14, "$muted", letter_spacing=2),
            _metric(
                "value",
                150,
                20,
                "cpu.temp",
                "{value:.0f}{unit}",
                20,
                "$text",
                align="right",
                font="builtin:Barlow-SemiBold",
                color_rules=TEMP_RULES,
            ),
            {
                "type": "bar",
                "id": "bar",
                "x": 166,
                "y": 9,
                "w": 154,
                "h": 8,
                "sensor": "cpu.temp",
                "min": 30,
                "max": 95,
                "color": "$accent_deep",
                "color2": "$accent",
                "background": "#ffffff10",
                "radius": 4,
                "glow": 0.5,
                "glow_radius": 6,
                "color_rules": TEMP_RULES,
            },
        ],
    },
    {
        "id": "big-number",
        "name": "Big number",
        "size": [160, 86],
        "widgets": [
            _text("label", 0, 16, "CPU LOAD", 13, "$muted", letter_spacing=2),
            _metric(
                "value",
                116,
                80,
                "cpu.load",
                "{value:.0f}",
                60,
                "$text",
                align="right",
                font="builtin:Barlow-SemiBold",
                glow=0.3,
            ),
            _text("unit", 120, 80, "%", 24, "$muted"),
        ],
    },
    {
        "id": "clock",
        "name": "Clock and date",
        "size": [240, 96],
        "widgets": [
            {
                "type": "clock",
                "id": "time",
                "x": 0,
                "y": 6,
                "format": "%H:%M",
                "font_size": 64,
                "font": "builtin:Barlow-SemiBold",
                "color": "$text",
                "glow": 0.25,
            },
            {
                "type": "clock",
                "id": "date",
                "x": 2,
                "y": 76,
                "format": "%A, %d %B",
                "font_size": 17,
                "color": "$muted",
                "tabular": False,
            },
        ],
    },
    {
        "id": "weather",
        "name": "Weather",
        "size": [220, 56],
        "widgets": [
            {
                "type": "icon",
                "id": "icon",
                "x": 0,
                "y": 6,
                "icon": "weather",
                "size": 42,
                "color": "$muted",
                "stroke": 1.8,
                "hide_if_missing": True,
            },
            {
                "type": "weather",
                "id": "temp",
                "x": 54,
                "y": 2,
                "field": "temperature",
                "format": "{value:.0f}°",
                "font_size": 32,
                "font": "builtin:Barlow-SemiBold",
                "color": "$text",
                "hide_if_missing": True,
            },
            {
                "type": "weather",
                "id": "desc",
                "x": 56,
                "y": 38,
                "field": "description",
                "format": "{value}",
                "font_size": 15,
                "color": "$muted",
                "tabular": False,
                "hide_if_missing": True,
            },
        ],
    },
    {
        "id": "network",
        "name": "Network down/up",
        "size": [300, 28],
        "widgets": [
            {
                "type": "icon",
                "id": "down-icon",
                "x": 0,
                "y": 3,
                "icon": "download",
                "size": 20,
                "color": "$good",
                "stroke": 2.2,
            },
            _metric("down", 26, 22, "net.down", "{value:bytes}/s", 20, "$text", tabular=False),
            {
                "type": "icon",
                "id": "up-icon",
                "x": 160,
                "y": 3,
                "icon": "upload",
                "size": 20,
                "color": "$up",
                "stroke": 2.2,
            },
            _metric("up", 186, 22, "net.up", "{value:bytes}/s", 20, "$text", tabular=False),
        ],
    },
    {
        "id": "history-card",
        "name": "History card",
        "size": [420, 130],
        "widgets": [
            {
                "type": "rect",
                "id": "card",
                "x": 0,
                "y": 0,
                "w": 420,
                "h": 130,
                "color": "$card",
                "color2": "$card2",
                "radius": 12,
                "outline": "$edge",
            },
            _text("title", 16, 30, "CPU HISTORY", 13, "$muted", letter_spacing=2),
            _metric(
                "value",
                404,
                34,
                "cpu.load",
                "{value:.0f}%",
                24,
                "$text",
                align="right",
                font="builtin:Barlow-SemiBold",
            ),
            {
                "type": "graph",
                "id": "graph",
                "x": 10,
                "y": 44,
                "w": 400,
                "h": 78,
                "sensor": "cpu.load",
                "history": 120,
                "min": 0,
                "max": 100,
                "color": "$accent",
                "fill": True,
                "fill_fade": True,
                "smooth": True,
                "line_width": 2,
                "grid": 2,
                "grid_color": "#ffffff0c",
                "glow": 0.4,
                "glow_radius": 6,
            },
        ],
    },
    {
        "id": "cpu-card",
        "name": "CPU card",
        "size": [376, 186],
        "widgets": [
            {
                "type": "rect",
                "id": "card",
                "x": 0,
                "y": 0,
                "w": 376,
                "h": 186,
                "color": "$card",
                "color2": "$card2",
                "radius": 14,
                "outline": "$edge",
            },
            {
                "type": "icon",
                "id": "icon",
                "x": 16,
                "y": 14,
                "icon": "cpu",
                "size": 22,
                "color": "$accent",
                "stroke": 1.9,
                "glow": 0.5,
                "glow_radius": 6,
            },
            _text(
                "title",
                46,
                33,
                "CPU",
                16,
                "$accent",
                font="builtin:Barlow-SemiBold",
                letter_spacing=3,
            ),
            _metric(
                "load",
                124,
                104,
                "cpu.load",
                "{value:.0f}",
                58,
                "$text",
                align="right",
                font="builtin:Barlow-SemiBold",
                glow=0.3,
            ),
            _text("pct", 128, 104, "%", 24, "$muted"),
            _metric("power", 18, 128, "cpu.power", "{value:.0f} W", 16, "$muted", fallback=""),
            {
                "type": "gauge",
                "id": "temp-ring",
                "x": 250,
                "y": 16,
                "w": 108,
                "h": 108,
                "sensor": "cpu.temp",
                "min": 20,
                "max": 100,
                "thickness": 9,
                "color": "$accent_deep",
                "color2": "$accent",
                "background": "#ffffff0f",
                "ticks": 8,
                "tick_color": "#ffffff22",
                "glow": 0.5,
                "glow_radius": 6,
                "color_rules": TEMP_RULES,
            },
            _metric(
                "temp",
                304,
                82,
                "cpu.temp",
                "{value:.0f}°",
                28,
                "$text",
                align="center",
                font="builtin:Barlow-SemiBold",
                fallback="--",
                color_rules=TEMP_RULES,
            ),
            _text("temp-label", 304, 100, "TEMP", 11, "$muted", align="center", letter_spacing=2),
            {
                "type": "graph",
                "id": "graph",
                "x": 12,
                "y": 136,
                "w": 352,
                "h": 42,
                "sensor": "cpu.load",
                "history": 90,
                "min": 0,
                "max": 100,
                "color": "$accent",
                "fill": True,
                "fill_fade": True,
                "smooth": True,
                "line_width": 2,
                "glow": 0.35,
                "glow_radius": 6,
            },
        ],
    },
    {
        "id": "section",
        "name": "Section with three rows",
        "size": [450, 172],
        "widgets": [
            {
                "type": "rect",
                "id": "card",
                "x": 0,
                "y": 0,
                "w": 450,
                "h": 172,
                "color": "$card",
                "color2": "$card2",
                "radius": 12,
                "outline": "$edge",
            },
            {
                "type": "icon",
                "id": "icon",
                "x": 16,
                "y": 12,
                "icon": "gpu",
                "size": 22,
                "color": "$accent",
                "stroke": 1.9,
                "glow": 0.5,
                "glow_radius": 6,
            },
            _text(
                "title",
                48,
                32,
                "GPU",
                19,
                "$accent",
                font="builtin:Barlow-SemiBold",
                letter_spacing=3,
            ),
            {
                "type": "rect",
                "id": "rule",
                "x": 16,
                "y": 44,
                "w": 418,
                "h": 2,
                "color": "$accent",
                "color2": "#00000000",
                "gradient": "horizontal",
                "radius": 1,
            },
            *[
                widget
                for i, (label, sensor, fmt, lo, hi) in enumerate(
                    [
                        ("TEMP", "gpu.temp", "{value:.0f}{unit}", 30, 95),
                        ("POWER", "gpu.power", "{value:.0f} W", 0, 350),
                        ("FAN", "gpu.fan", "{value:.0f} RPM", 0, 3000),
                    ]
                )
                for widget in (
                    _text(f"label-{i}", 16, 82 + 36 * i, label, 16, "$muted", letter_spacing=2),
                    _metric(
                        f"value-{i}",
                        229,
                        83 + 36 * i,
                        sensor,
                        fmt,
                        23,
                        "$text",
                        align="right",
                        font="builtin:Barlow-SemiBold",
                    ),
                    {
                        "type": "bar",
                        "id": f"bar-{i}",
                        "x": 247,
                        "y": 71 + 36 * i,
                        "w": 183,
                        "h": 8,
                        "sensor": sensor,
                        "min": lo,
                        "max": hi,
                        "color": "$accent_deep",
                        "color2": "$accent",
                        "background": "#ffffff0d",
                        "radius": 4,
                        "glow": 0.55,
                        "glow_radius": 6,
                    },
                )
            ],
        ],
    },
    {
        "id": "icon-label",
        "name": "Icon with label",
        "size": [160, 26],
        "widgets": [
            {
                "type": "icon",
                "id": "icon",
                "x": 0,
                "y": 1,
                "icon": "temperature",
                "size": 22,
                "color": "$accent",
                "stroke": 2,
            },
            _text("label", 30, 20, "CPU TEMP", 15, "$muted", letter_spacing=2),
        ],
    },
]


def resolve_tokens(value: Any, palette: dict[str, str] | None = None) -> Any:
    """Replace ``$name`` colour tokens (palette entry if present, else default)."""
    palette = palette or {}
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:]
        return f"@{name}" if name in palette else TOKEN_DEFAULTS[name]
    if isinstance(value, list):
        return [resolve_tokens(v, palette) for v in value]
    if isinstance(value, dict):
        return {k: resolve_tokens(v, palette) for k, v in value.items()}
    return value


def presets_for_editor() -> dict[str, Any]:
    return {"presets": deepcopy(PRESETS), "tokens": dict(TOKEN_DEFAULTS)}

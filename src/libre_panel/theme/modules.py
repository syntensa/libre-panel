"""Modules: building blocks that fill cells of a theme's grid.

A module widget names a kind ("ring", "weather" ...), the cells it covers
on the theme's grid (``col``, ``row``, ``cols``, ``rows``) and a few
options. Before a theme is drawn, every module becomes plain widgets laid
out for its size, the way home-screen widgets show more the larger they
are: a 1x1 CPU ring shows the load, a 2x1 one adds temperature, power and
the processor's name, a 4x1 one a history graph as well.

Colours, fonts and the card behind each module come from the theme's look:
the palette roles in ``ROLES`` and the ``style`` section. A theme without
them gets the "arctic" look of SPUR II. Labels are in the user's language.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import ImageColor, ImageFont

from libre_panel import timezones
from libre_panel.devices.models import find_model
from libre_panel.fonts import builtin_font_path
from libre_panel.i18n import t

# -- looks -------------------------------------------------------------------

# Palette roles the modules paint with.
ROLES = (
    "bg", "bg2", "surface", "line", "track", "text", "text2", "text3",
    "accent", "cpu", "gpu", "mem", "disk", "net", "warn", "crit",
)  # fmt: skip

STYLE_DEFAULTS: dict[str, Any] = {
    "card": "glass",  # glass | flat | outline | none
    "radius": 18,  # corner radius of a card 200 px tall
    "glow": 0.6,  # 0 = none, 1 = strong
    "backdrop": "gradient",  # gradient | flat: the screen behind the modules
    "display_font": "builtin:Barlow-SemiBold",  # numbers and the clock
    "text_font": "builtin:Barlow-Medium",  # labels
}
CARDS = ("glass", "flat", "outline", "none")
BACKDROPS = ("gradient", "flat")

LOOKS: dict[str, dict[str, Any]] = {
    "arctic": {
        "name": "Arctic",
        "palette": {
            "bg": "#0A1119", "bg2": "#060A0F", "surface": "#14202E", "line": "#1F3147",
            "track": "#162332", "text": "#EEF6FF", "text2": "#B8CAE0", "text3": "#8CA0B8",
            "accent": "#13E5D7", "cpu": "#13E5D7", "gpu": "#A3AEFF", "mem": "#E0A8FF",
            "disk": "#8FD3FF", "net": "#7EE8A6", "warn": "#FFCB6B", "crit": "#FF7A85",
        },
        "style": {"card": "glass", "radius": 18, "glow": 0.6},
    },
    "neon": {
        "name": "Neon",
        "palette": {
            "bg": "#07060F", "bg2": "#020107", "surface": "#110F26", "line": "#2D2760",
            "track": "#1B1838", "text": "#F7F5FF", "text2": "#CBC4F5", "text3": "#9089C6",
            "accent": "#FF4FD8", "cpu": "#00F0FF", "gpu": "#FF4FD8", "mem": "#B26BFF",
            "disk": "#FFE45C", "net": "#4CFF9A", "warn": "#FFB020", "crit": "#FF3B5C",
        },
        "style": {"card": "outline", "radius": 14, "glow": 1.0},
    },
    "graphite": {
        "name": "Graphite",
        "palette": {
            "bg": "#141516", "bg2": "#0C0D0E", "surface": "#1E2023", "line": "#2C2F33",
            "track": "#2A2D31", "text": "#F2F2F2", "text2": "#C6C9CE", "text3": "#8D9298",
            "accent": "#FF8A3D", "cpu": "#FF8A3D", "gpu": "#5EB1FF", "mem": "#C792EA",
            "disk": "#9CCC65", "net": "#4DD0E1", "warn": "#FFC857", "crit": "#FF5C5C",
        },
        "style": {"card": "flat", "radius": 10, "glow": 0.15},
    },
    "paper": {
        "name": "Paper",
        "palette": {
            "bg": "#E9EEF3", "bg2": "#DCE3EA", "surface": "#FFFFFF", "line": "#D3DBE3",
            "track": "#E2E8EF", "text": "#15212C", "text2": "#3E4C5A", "text3": "#687887",
            "accent": "#0B8C84", "cpu": "#0B8C84", "gpu": "#4B57C9", "mem": "#9B4FC4",
            "disk": "#2F7FB8", "net": "#2E9E5B", "warn": "#C98200", "crit": "#D6434E",
        },
        "style": {"card": "flat", "radius": 16, "glow": 0.0},
    },
    "sunset": {
        "name": "Sunset",
        "palette": {
            "bg": "#170D16", "bg2": "#0B0609", "surface": "#24151F", "line": "#3D2434",
            "track": "#2F1B28", "text": "#FFF2EB", "text2": "#F2CBBA", "text3": "#B98F82",
            "accent": "#FF7E5F", "cpu": "#FF7E5F", "gpu": "#FEB47B", "mem": "#EC6FAA",
            "disk": "#FFD27A", "net": "#7FD8BE", "warn": "#FFC857", "crit": "#FF4E6A",
        },
        "style": {"card": "glass", "radius": 20, "glow": 0.5},
    },
    "mono": {
        "name": "Mono",
        "palette": {
            "bg": "#000000", "bg2": "#000000", "surface": "#0B0B0B", "line": "#2A2A2A",
            "track": "#1E1E1E", "text": "#FFFFFF", "text2": "#C2C2C2", "text3": "#7D7D7D",
            "accent": "#FFFFFF", "cpu": "#FFFFFF", "gpu": "#FFFFFF", "mem": "#FFFFFF",
            "disk": "#FFFFFF", "net": "#FFFFFF", "warn": "#FFC400", "crit": "#FF3D3D",
        },
        "style": {
            "card": "outline", "radius": 4, "glow": 0.0, "backdrop": "flat",
            "display_font": "builtin:JetBrainsMono-Bold",
            "text_font": "builtin:JetBrainsMono-Medium",
        },
    },
}  # fmt: skip
DEFAULT_LOOK = "arctic"


def look_theme_parts(look: str) -> tuple[dict[str, str], dict[str, Any]]:
    """The palette and style a look sets (for the editor's look picker)."""
    chosen = LOOKS[look]
    return dict(chosen["palette"]), {**STYLE_DEFAULTS, **chosen["style"]}


# -- sources -----------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    label: str
    load: str  # the main reading
    fmt: str  # its format
    icon: str
    role: str  # palette role of its colour
    details: tuple[tuple[str, str, str], ...] = ()  # (sensor, format, label)
    name: str = ""  # the hardware's name, if the sensors report one
    low: float | None = 0
    high: float | None = 100
    scale: str = "linear"


def _sources() -> dict[str, Source]:
    # Built on use: the labels follow the language.
    rate = "{value:bytes}/s"
    return {
        "cpu": Source(
            "CPU", "cpu.load", "{value:.0f}%", "cpu", "cpu",
            (("cpu.temp", "{value:.0f}°C", t("Temp")), ("cpu.power", "{value:.0f} W", t("Power")),
             ("cpu.freq", "{value:.0f} MHz", t("Clock"))),
            name="cpu.name",
        ),
        "gpu": Source(
            "GPU", "gpu.load", "{value:.0f}%", "gpu", "gpu",
            (("gpu.temp", "{value:.0f}°C", t("Temp")), ("gpu.power", "{value:.0f} W", t("Power")),
             ("gpu.fan", "{value:.0f} RPM", t("Fan"))),
            name="gpu.name",
        ),
        "mem": Source(
            t("RAM"), "mem.load", "{value:.0f}%", "ram", "mem",
            (("mem.used", "{value:.1f} GB", t("Used")), ("mem.total", "{value:.0f} GB", t("Total")),
             ("swap.load", "{value:.0f}%", t("Swap"))),
        ),
        "disk": Source(
            t("Disk"), "disk.load", "{value:.0f}%", "disk", "disk",
            (("disk.used", "{value:.0f} GB", t("Used")),
             ("disk.total", "{value:.0f} GB", t("Total")), ("disk.read", rate, t("Read"))),
        ),
        "net": Source(
            t("Network"), "net.down", rate, "network", "net",
            (("net.down", rate, t("Down")), ("net.up", rate, t("Up"))),
            low=0, high=None, scale="sqrt",
        ),
    }  # fmt: skip


# -- the grid ----------------------------------------------------------------

GRID_DEFAULTS: dict[str, int] = {"columns": 0, "rows": 0, "gap": 0, "margin": 0}  # 0 = automatic


@dataclass(frozen=True)
class Grid:
    x: int
    y: int
    cell_w: float
    cell_h: float
    gap: int
    columns: int
    rows: int

    def box(self, col: int, row: int, cols: int, rows: int) -> list[int]:
        x0 = self.x + col * (self.cell_w + self.gap)
        y0 = self.y + row * (self.cell_h + self.gap)
        x1 = x0 + cols * self.cell_w + (cols - 1) * self.gap
        y1 = y0 + rows * self.cell_h + (rows - 1) * self.gap
        return [round(x0), round(y0), round(x1) - round(x0), round(y1) - round(y0)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "cell_w": self.cell_w,
            "cell_h": self.cell_h,
            "gap": self.gap,
            "columns": self.columns,
            "rows": self.rows,
        }


def make_grid(
    width: int, height: int, settings: dict[str, int] | None = None, model: str = "custom"
) -> Grid:
    """The cells of a ``width`` x ``height`` frame.

    Automatic values give cells of about half the short side (two rows on a
    bar, three columns on a 3.5" panel) and keep clear of strips the panel's
    bezel hides.
    """
    given = {**GRID_DEFAULTS, **(settings or {})}
    short = min(width, height)
    margin = given["margin"] or max(4, min(28, round(short * 0.05)))
    gap = given["gap"] or max(3, min(20, round(short * 0.035)))
    left = top = right = bottom = margin
    panel = find_model(model)
    if panel is not None:
        orientation = "landscape" if width >= height else "portrait"
        hidden = panel.hidden_edges(orientation)
        top, right = top + hidden["top"], right + hidden["right"]
        bottom, left = bottom + hidden["bottom"], left + hidden["left"]
    usable_w = max(1, width - left - right)
    usable_h = max(1, height - top - bottom)
    target = max(64, min(240, short / 2))
    columns = given["columns"] or max(1, round(usable_w / target))
    rows = given["rows"] or max(1, round(usable_h / target))
    cell_w = max(1.0, (usable_w - (columns - 1) * gap) / columns)
    cell_h = max(1.0, (usable_h - (rows - 1) * gap) / rows)
    return Grid(left, top, cell_w, cell_h, gap, columns, rows)


# -- building ----------------------------------------------------------------

CAP_TOP = 0.30  # Barlow: the top of capitals and digits below the text's y, per font size
CAP = 0.70  # their height, per font size
CLOCK_WIDTH = 2.65  # "00:00" in font sizes


@lru_cache(maxsize=64)
def _width_per_size(font: str, text: str) -> float:
    """How wide ``text`` is in ``font``, per pixel of font size."""
    path = builtin_font_path(font)
    if path is None:  # a theme's own font: about as wide as Barlow
        return CLOCK_WIDTH if text == "00:00" else len(text) * 0.55
    return ImageFont.truetype(str(path), 100).getlength(text) / 100


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rgb(color: str) -> tuple[int, int, int]:
    """Any colour a theme may hold ("#0b8", "#0B8C84CC", "teal") as red, green, blue."""
    red, green, blue = ImageColor.getrgb(color)[:3]
    return red, green, blue


def _hex(color: str) -> str:
    """``color`` as "#RRGGBB", the form the modules mix and add alpha to."""
    return "#{:02X}{:02X}{:02X}".format(*_rgb(color))


def mix(a: str, b: str, amount: float) -> str:
    """``a`` moved ``amount`` (0-1) of the way to ``b``."""
    ca, cb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(ca, cb, strict=True))


_CARDLESS = ("clock", "text", "analog", "image")  # kinds without a card unless asked


class _Build:
    """Collects the widgets of one module, laid out in its box."""

    def __init__(
        self,
        module: dict[str, Any],
        box: list[int],
        colors: dict[str, str],
        style: dict[str, Any],
    ) -> None:
        self.module = module
        self.x, self.y, self.w, self.h = box
        self.cols, self.rows = module["cols"], module["rows"]
        self.colors = colors
        self.style = style
        self.u = min(self.w, self.h)
        self.card_on = module["card"] == "on" or (
            module["card"] == "auto" and module["module"] not in _CARDLESS
        )
        self.card_on = self.card_on and style["card"] != "none"
        self.pad = round(_clamp(self.u * 0.1, 6, 26)) if self.card_on else round(self.u * 0.04)
        self.ix, self.iy = self.x + self.pad, self.y + self.pad
        self.iw, self.ih = self.w - 2 * self.pad, self.h - 2 * self.pad
        self.ts = round(_clamp(self.u * 0.075, 9, 22))  # small labels
        self.glow = float(style["glow"])
        self.out: list[dict[str, Any]] = []
        self.needs = ""  # added to every widget (a source's fallback)

    # colours
    def c(self, role: str) -> str:
        return self.colors[role]

    def tint(self, role: str) -> str:
        """The module's own colour if it has one, else the role's."""
        return self.module.get("color") or self.c(role)

    def deep(self, color: str) -> str:
        return mix(color, self.c("surface"), 0.55)

    # widgets
    def add(self, kind: str, name: str, **fields: Any) -> dict[str, Any]:
        widget = {"type": kind, "id": name, **fields}
        if self.needs:
            own = widget.get("needs", "")
            widget["needs"] = f"{own},{self.needs}" if own else self.needs
        self.out.append(widget)
        return widget

    def card(self) -> None:
        if not self.card_on:
            return
        radius = round(self.style["radius"] * self.u / 200)
        kind = self.style["card"]
        fill = {"glass": self.c("surface") + "E6", "flat": self.c("surface")}.get(kind)
        outline = self.c("line") if kind in ("glass", "outline") else None
        self.add(
            "rect", "card", x=self.x, y=self.y, w=self.w, h=self.h, color=fill,
            radius=radius, outline=outline, outline_width=max(1, round(self.u / 160)),
        )  # fmt: skip

    def _text_fields(self, size: float, cap_top: float, font: str, **kw: Any) -> dict[str, Any]:
        size = max(6, round(size))
        fields = {"font": self.style[font], "font_size": size, "y": round(cap_top - CAP_TOP * size)}
        fields.update(kw)
        return fields

    def label(self, name: str, text: str, x: float, cap_top: float, size: float,
              color: str | None = None, align: str = "left", width: float = 0) -> None:  # fmt: skip
        fields = self._text_fields(size, cap_top, "text_font")
        self.add(
            "text", name, x=round(x), text=text.upper(), color=color or self.c("text3"),
            letter_spacing=max(1, round(fields["font_size"] * 0.14)), align=align,
            max_width=round(width), tabular=False, **fields,
        )  # fmt: skip

    def value(self, name: str, sensor: str, fmt: str, x: float, cap_top: float, size: float,
              color: str | None = None, align: str = "left", width: float = 0,
              font: str = "display_font", tabular: bool = True, **kw: Any) -> None:  # fmt: skip
        fields = self._text_fields(size, cap_top, font)
        self.add(
            "metric", name, x=round(x), sensor=sensor, format=fmt,
            color=color or self.c("text"), align=align, max_width=round(width),
            tabular=tabular, **fields, **kw,
        )  # fmt: skip

    def clock(self, name: str, fmt: str, x: float, cap_top: float, size: float,
              color: str | None = None, align: str = "left", width: float = 0,
              font: str = "display_font", **kw: Any) -> None:  # fmt: skip
        fields = self._text_fields(size, cap_top, font)
        self.add(
            "clock", name, x=round(x), format=fmt, color=color or self.c("text"), align=align,
            max_width=round(width), **fields, **kw,
        )  # fmt: skip

    def ring(self, name: str, sensor: str, x: float, y: float, d: float, color: str,
             low: float | None = 0, high: float | None = 100) -> None:  # fmt: skip
        d = round(d)
        self.add(
            "gauge", name, x=round(x), y=round(y), w=d, h=d, sensor=sensor,
            min=0 if low is None else low, max=100 if high is None else high,
            thickness=max(3, round(d * 0.08)), color=self.deep(color), color2=color,
            background=self.c("track"), cap="round", glow=round(0.45 * self.glow, 2),
            glow_radius=max(2, round(d * 0.05)), smooth=True,
        )  # fmt: skip

    def bar(self, name: str, sensor: str, x: float, y: float, w: float, h: float,
            color: str, low: float | None = 0, high: float | None = 100,
            scale: str = "linear") -> None:  # fmt: skip
        h = max(2, round(h))
        self.add(
            "bar", name, x=round(x), y=round(y), w=max(4, round(w)), h=h, sensor=sensor,
            min=0 if low is None else low, max=100 if high is None else high, scale=scale,
            color=self.deep(color), color2=color, background=self.c("track"), radius=h // 2,
            glow=round(0.3 * self.glow, 2), glow_radius=max(2, h),
        )  # fmt: skip

    def graph(self, name: str, sensor: str, x: float, y: float, w: float, h: float,
              color: str, low: float | None = 0, high: float | None = 100,
              scale: str = "linear", fill: bool = True) -> None:  # fmt: skip
        w, h = max(8, round(w)), max(8, round(h))
        self.add(
            "graph", name, x=round(x), y=round(y), w=w, h=h, sensor=sensor, min=low, max=high,
            scale=scale, history=max(30, min(300, w // 3)), color=color, fill=fill,
            fill_fade=True, smooth=True, line_width=max(1, round(self.u / 110)),
            glow=round(0.35 * self.glow, 2), glow_radius=max(2, round(self.u / 40)),
        )  # fmt: skip

    def icon(self, name: str, icon: str, x: float, y: float, size: float, color: str) -> None:
        size = max(8, round(size))
        self.add(
            "icon", name, x=round(x), y=round(y), size=size, icon=icon, color=color,
            stroke=round(_clamp(size / 14, 1.5, 3.5), 1),
        )  # fmt: skip

    def listing(self, name: str, items: str, x: float, y: float, w: float, h: float,
                color: str, style: str = "bars", size: float = 0, **kw: Any) -> None:  # fmt: skip
        """A list of readings (temperatures, drives, cores ...), as many as fit."""
        self.add(
            "list", name, x=round(x), y=round(y), w=max(4, round(w)), h=max(4, round(h)),
            items=items, style=style, font=self.style["display_font"],
            label_font=self.style["text_font"], font_size=max(7, round(size or self.ts * 1.3)),
            color=self.c("text"), muted=self.c("text3"), color2=color,
            **{"background": self.c("track"), **kw},
        )  # fmt: skip

    def head(self, icon: str, title: str, color: str, room: float = 0) -> float:
        """Icon and title at the top (``room`` kept free on the right); returns
        the height they take."""
        self.icon("icon", icon, self.ix, self.iy - self.ts * 0.1, self.ts * 1.5, color)
        self.label("title", title, self.ix + self.ts * 2.1, self.iy + self.ts * 0.15,
                   self.ts * 1.05, color=color, width=self.iw - self.ts * 2.1 - room)  # fmt: skip
        return self.ts * 2.4

    # shapes of the box
    @property
    def aspect(self) -> float:
        return self.iw / max(1, self.ih)

    @property
    def large(self) -> bool:
        return self.cols >= 2 and self.rows >= 2


def _detail_rows(b: _Build, src: Source, x: float, top: float, width: float, height: float,
                 color: str, title: bool = True) -> None:  # fmt: skip
    """Source title, the hardware name and detail rows in a column."""
    y = top
    if title:
        size = b.ts * 1.15
        b.label("title", b.module["title"] or src.label, x, y, size, color=color, width=width)
        y += CAP * size + b.ts * 0.9
        if src.name and height - (y - top) > b.ts * 6:
            b.value("name", src.name, "{value}", x, y, b.ts * 0.95, color=b.c("text3"),
                    width=width, font="text_font", tabular=False, fallback="",
                    hide_if_missing=True, fit="ellipsis")  # fmt: skip
            y += CAP * b.ts * 0.95 + b.ts * 1.1
    room = top + height - y
    count = min(len(src.details), 3, int(room // (b.ts * 2.6)))
    if count < 1:
        return
    row = min(room / count, b.u * 0.26)
    value_size = min(row * 0.56, b.u * 0.17, width * 0.2)
    for i, (sensor, fmt, text) in enumerate(src.details[:count]):
        base = y + i * row + (row - CAP * value_size) / 2
        b.label(
            f"d{i}-label", text, x, base + CAP * (value_size - b.ts) / 2, b.ts, width=width * 0.4
        )
        b.value(f"d{i}", sensor, fmt, x + width, base, value_size, align="right",
                width=width * 0.58, hide_if_missing=True,
                color_rules=_heat(b) if sensor.endswith(".temp") else [])  # fmt: skip


def _heat(b: _Build) -> list[dict[str, Any]]:
    return [{"above": 75, "color": b.c("warn")}, {"above": 88, "color": b.c("crit")}]


def _ring_inside(b: _Build, src: Source, cx: float, cy: float, d: float, color: str) -> None:
    value_size, label_size = d * 0.25, max(7, d * 0.085)
    gap = d * 0.05
    total = CAP * value_size + gap + CAP * label_size
    top = cy - total / 2
    b.value("value", src.load, src.fmt, cx, top, value_size, align="center", width=d * 0.66)
    b.label("label", b.module["title"] or src.label, cx, top + CAP * value_size + gap,
            label_size, color=color, align="center", width=d * 0.6)  # fmt: skip


def build_ring(b: _Build, src: Source) -> None:
    color = b.tint(src.role)
    b.card()
    a = b.aspect
    if b.large and 0.7 < a < 1.6:  # big square: ring and details, history below
        top_h = min(b.ih * 0.58, b.iw * 0.5)
        _ring_wide(b, src, color, b.ix, b.iy, b.iw, top_h)
        gy = b.iy + top_h + b.pad
        b.graph("history", src.load, b.ix, gy, b.iw, b.iy + b.ih - gy, color,
                src.low, src.high, src.scale)  # fmt: skip
    elif a >= 1.45:
        _ring_wide(b, src, color, b.ix, b.iy, b.iw, b.ih, graph_below=b.large)
    elif a <= 0.72:  # tall: ring on top, details below
        d = b.iw
        b.ring("ring", src.load, b.ix, b.iy, d, color, src.low, src.high)
        _ring_inside(b, src, b.ix + d / 2, b.iy + d / 2, d, color)
        room = b.ih - d - b.pad
        if room > b.ts * 3:
            graph_h = room * 0.45 if room > d * 0.9 else 0
            _detail_rows(b, src, b.ix, b.iy + d + b.pad, b.iw, room - graph_h, color, False)
            if graph_h:
                b.graph("history", src.load, b.ix, b.iy + b.ih - graph_h, b.iw, graph_h, color,
                        src.low, src.high, src.scale)  # fmt: skip
    else:  # compact
        d = min(b.iw, b.ih)
        x, y = b.ix + (b.iw - d) / 2, b.iy + (b.ih - d) / 2
        b.ring("ring", src.load, x, y, d, color, src.low, src.high)
        _ring_inside(b, src, x + d / 2, y + d / 2, d, color)


def _ring_wide(b: _Build, src: Source, color: str, x: float, y: float, w: float, h: float,
               graph_below: bool = False) -> None:  # fmt: skip
    d = h
    b.ring("ring", src.load, x, y, d, color, src.low, src.high)
    _ring_inside(b, src, x + d / 2, y + d / 2, d, color)
    rest = w - d - b.pad
    if rest < b.ts * 5:
        return
    column = min(rest, d * 1.3)
    graph_w = rest - column - b.pad
    if graph_w > d * 0.8:  # a long box: the history on the right
        _detail_rows(b, src, x + d + b.pad, y + h * 0.04, column, h * 0.92, color)
        b.graph("history", src.load, x + d + column + 2 * b.pad, y + h * 0.12, graph_w, h * 0.76,
                color, src.low, src.high, src.scale)  # fmt: skip
    elif graph_below:  # a big box: the history under the details
        _detail_rows(b, src, x + d + b.pad, y, rest, h * 0.6, color)
        b.graph("history", src.load, x + d + b.pad, y + h * 0.66, rest, h * 0.34, color,
                src.low, src.high, src.scale)  # fmt: skip
    else:
        _detail_rows(b, src, x + d + b.pad, y + h * 0.04, column, h * 0.92, color)


def build_stat(b: _Build, src: Source) -> None:
    """A large number with its name; a bar and a history graph when there is room."""
    color = b.tint(src.role)
    b.card()
    sensor, fmt = src.load, src.fmt
    if b.module["source"] == "sensor":
        sensor = b.module["sensor"] or "cpu.load"
        fmt = b.module["format"] or "{value:.0f}{unit}"
    title = b.module["title"] or (src.label if b.module["source"] != "sensor" else sensor)
    a = b.aspect
    b.icon("icon", src.icon, b.ix, b.iy - b.ts * 0.1, b.ts * 1.5, color)
    b.label("title", title, b.ix + b.ts * 2.1, b.iy + b.ts * 0.15, b.ts * 1.05, color=color,
            width=b.iw - b.ts * 2.1)  # fmt: skip
    head = b.ts * 1.9
    tall = a <= 0.72
    graph_room = b.large or a >= 2.4 or tall
    stacked = b.large or tall  # the history under the number
    body_h = b.ih - head
    if graph_room and stacked:
        body_h = b.ih * 0.5 - head
    size = min(body_h * 0.62, b.iw * (0.3 if a < 2.4 else 0.18))
    cap_top = b.iy + head + (body_h - CAP * size) * (0.35 if graph_room else 0.45)
    width = b.iw if not (graph_room and not stacked) else b.iw * 0.42
    b.value("value", sensor, fmt, b.ix, cap_top, size, width=width)
    known = b.module["source"] != "sensor" and src.high is not None
    if known and not (graph_room and stacked) and b.ih > b.ts * 6:
        bar_h = max(3, b.u * 0.035)
        b.bar("bar", sensor, b.ix, b.iy + b.ih - bar_h, width, bar_h, color,
              src.low, src.high, src.scale)  # fmt: skip
    src_known = b.module["source"] in ("cpu", "gpu", "mem", "disk")
    if stacked and b.large and a >= 1.6 and src_known:  # room beside the number: details
        _detail_rows(b, src, b.ix + b.iw * 0.5, b.iy, b.iw * 0.5, b.ih * 0.5, color, False)
    if graph_room:
        if stacked:
            gy = b.iy + b.ih * 0.5 + b.pad / 2
            b.graph(
                "history",
                sensor,
                b.ix,
                gy,
                b.iw,
                b.iy + b.ih - gy,
                color,
                src.low if known else None,
                src.high if known else None,
                src.scale,
            )
        else:
            gx = b.ix + width + b.pad
            b.graph(
                "history",
                sensor,
                gx,
                b.iy + head,
                b.ix + b.iw - gx,
                b.ih - head,
                color,
                src.low if known else None,
                src.high if known else None,
                src.scale,
            )


def build_graph(b: _Build, src: Source) -> None:
    """A history graph under its name and current value."""
    color = b.tint(src.role)
    b.card()
    sensor, fmt = src.load, src.fmt
    if b.module["source"] == "sensor":
        sensor = b.module["sensor"] or "cpu.load"
        fmt = b.module["format"] or "{value:.0f}{unit}"
    known = b.module["source"] != "sensor"
    title = b.module["title"] or (src.label if known else sensor)
    value_size = _clamp(b.ih * 0.17, b.ts * 1.2, b.ts * 3.2)
    b.label("title", title, b.ix, b.iy + (CAP * value_size - CAP * b.ts) / 2, b.ts, color=color,
            width=b.iw * 0.5)  # fmt: skip
    b.value("value", sensor, fmt, b.ix + b.iw, b.iy, value_size, align="right", width=b.iw * 0.55)
    gy = b.iy + CAP * value_size + b.pad * 0.8
    b.graph("history", sensor, b.ix, gy, b.iw, b.iy + b.ih - gy, color,
            src.low if known else None, src.high if known else None, src.scale)  # fmt: skip


def build_bars(b: _Build, _src: Source) -> None:
    """Rows with a bar each: CPU, RAM, disk and GPU, as many as fit."""
    b.card()
    sources = _sources()
    order = [sources[k] for k in ("cpu", "mem", "disk", "gpu")]
    wide = b.aspect >= 2.2
    row_min = b.ts * (2.2 if wide else 3.3)
    count = int(_clamp(b.ih // row_min, 1, 4))
    row = b.ih / count
    for i, src in enumerate(order[:count]):
        color = b.c(src.role)
        top = b.iy + i * row
        needs = src.load if src.role == "gpu" else ""
        size = min(row * (0.42 if wide else 0.3), b.ts * 1.6)
        bar_h = max(3, round(min(row * 0.14, b.u * 0.03)))
        if wide:
            mid = top + row / 2
            label_w = b.iw * 0.2
            value_w = b.iw * 0.2
            b.label(f"r{i}-label", src.label, b.ix, mid - CAP * b.ts / 2, b.ts, color=color,
                    width=label_w)  # fmt: skip
            b.bar(f"r{i}-bar", src.load, b.ix + label_w, mid - bar_h / 2,
                  b.iw - label_w - value_w - b.pad, bar_h, color)  # fmt: skip
            b.value(f"r{i}-value", src.load, src.fmt, b.ix + b.iw, mid - CAP * size / 2, size,
                    align="right", width=value_w)  # fmt: skip
        else:
            b.label(f"r{i}-label", src.label, b.ix, top + row * 0.12 + CAP * (size - b.ts) / 2,
                    b.ts, color=color, width=b.iw * 0.5)  # fmt: skip
            b.value(f"r{i}-value", src.load, src.fmt, b.ix + b.iw, top + row * 0.12, size,
                    align="right", width=b.iw * 0.5)  # fmt: skip
            b.bar(f"r{i}-bar", src.load, b.ix, top + row * 0.12 + CAP * size + row * 0.16,
                  b.iw, bar_h, color)  # fmt: skip
        if needs:
            for w in b.out[-3:]:
                w["needs"] = needs


def build_network(b: _Build, src: Source) -> None:
    """Download and upload; a graph of the download when there is room."""
    color = b.tint("net")
    up_color = mix(color, b.c("text"), 0.45)
    b.card()
    a = b.aspect
    stacked = b.large or a <= 0.72  # the graph under the numbers
    graph = a >= 1.45 or stacked
    column = b.iw if not graph or stacked else min(b.iw * 0.45, b.ih * 1.6)
    lines_h = b.ih if not (graph and stacked) else b.ih * 0.45
    size = min(lines_h * 0.26, column * 0.16)
    icon = size * 0.95
    rate = "{value:bytes}/s"
    for i, (key, name, tone) in enumerate((("net.down", "download", color),
                                           ("net.up", "upload", up_color))):  # fmt: skip
        cap_top = b.iy + lines_h * (0.18 + 0.5 * i)
        b.icon(f"{name}-icon", name, b.ix, cap_top + CAP * size / 2 - icon / 2, icon, tone)
        b.value(name, key, rate, b.ix + icon * 1.35, cap_top, size, width=column - icon * 1.35)
    if not graph:
        return
    if stacked:
        gx, gy, gw, gh = b.ix, b.iy + lines_h + b.pad, b.iw, b.ih - lines_h - b.pad
    else:
        gx, gy = b.ix + column + b.pad, b.iy
        gw, gh = b.iw - column - b.pad, b.ih
    b.graph("down-history", "net.down", gx, gy, gw, gh, color, 0, None, "sqrt")
    b.graph("up-history", "net.up", gx, gy, gw, gh, up_color, 0, None, "sqrt", fill=False)


def build_clock(b: _Build, _src: Source) -> None:
    """The time, the seconds when wide enough, and the date."""
    b.card()
    a = b.aspect
    if a <= 0.72:  # tall: hours over minutes
        size = min(b.iw / 1.3, b.ih * 0.34)
        date_size = max(8, size * 0.2)
        gap = size * 0.22
        block = 2 * CAP * size + gap + date_size * 1.2 + CAP * date_size
        top = b.iy + (b.ih - block) / 2
        cx = b.ix + b.iw / 2
        glow = round(0.3 * b.glow, 2)
        b.clock("hours", "%H", cx, top, size, align="center", glow=glow,
                glow_radius=max(2, round(size / 12)))  # fmt: skip
        b.clock("minutes", "%M", cx, top + CAP * size + gap, size, align="center",
                color=b.c("accent"), glow=round(0.6 * b.glow, 2),
                glow_radius=max(2, round(size / 12)))  # fmt: skip
        b.clock("date", "%a %d %b", cx, top + 2 * CAP * size + gap + date_size * 1.2, date_size,
                color=b.c("text2"), align="center", width=b.iw, font="text_font",
                tabular=False)  # fmt: skip
        return
    seconds = a >= 2.2
    date_ratio = 0.2
    clock_width = _width_per_size(b.style["display_font"], "00:00")
    size = min(b.ih / (1 + date_ratio * 2.2), b.iw / (clock_width + (0.62 if seconds else 0)))
    date_size = max(8, size * date_ratio)
    block = CAP * size + date_size * 1.1 + CAP * date_size
    top = b.iy + (b.ih - block) / 2
    left = a >= 1.45
    x = b.ix if left else b.ix + b.iw / 2
    align = "left" if left else "center"
    glow = round(0.3 * b.glow, 2)
    b.clock("time", "%H:%M", x, top, size, align=align, width=b.iw, glow=glow,
            glow_radius=max(2, round(size / 12)))  # fmt: skip
    if seconds:
        sx = x + clock_width * size + size * 0.08
        b.clock("seconds", "%S", sx, top, size * 0.36, color=b.c("accent"),
                glow=round(0.7 * b.glow, 2), glow_radius=max(2, round(size / 16)))  # fmt: skip
    fmt = "%A · %d %B" if b.iw > size * 4.2 else "%a %d %b"
    b.clock("date", fmt, x, top + CAP * size + date_size * 1.1, date_size, color=b.c("text2"),
            align=align, width=b.iw, font="text_font", tabular=False)  # fmt: skip


def build_date(b: _Build, _src: Source) -> None:
    """A calendar sheet: the day, the weekday, the month; the month's days when large."""
    b.card()
    _date_layout(b)


def _date_layout(b: _Build) -> None:
    a = b.aspect
    if (b.large and a >= 1.6) or a >= 3.0:  # the sheet beside the month
        width = min(b.iw * 0.4, b.ih * 1.3)
        _sheet(b, b.ix, b.iy, width, b.ih)
        _month(b, b.ix + width + b.pad, b.iy, b.iw - width - b.pad, b.ih)
    elif b.large or (a <= 0.72 and b.ih >= b.iw * 1.6):  # the sheet above the month
        top = b.ih * (0.32 if b.large else 0.4)
        _sheet(b, b.ix, b.iy, b.iw, top)
        _month(b, b.ix, b.iy + top + b.pad * 0.5, b.iw, b.ih - top - b.pad * 0.5)
    else:
        _sheet(b, b.ix, b.iy, b.iw, b.ih)


def _month(b: _Build, x: float, y: float, w: float, h: float) -> None:
    b.add(
        "calendar", "calendar", x=round(x), y=round(y), w=round(w), h=round(h),
        font=b.style["text_font"], font_size=round(min(h / 7 * 0.6, w / 7 * 0.45)),
        color=b.c("text2"), color2=b.tint("accent"), muted=b.c("text3"),
    )  # fmt: skip


def _sheet(b: _Build, x: float, y: float, w: float, h: float) -> None:
    accent = b.tint("accent")
    if w / max(1, h) >= 1.6:  # day on the left, names on the right
        size = min(h * 0.78, w * 0.3)
        b.clock("day", "%d", x, y + (h - CAP * size) / 2, size, color=accent,
                glow=round(0.5 * b.glow, 2), glow_radius=max(2, round(size / 14)))  # fmt: skip
        nx = x + size * 1.3
        name = min(h * 0.2, (w - size * 1.3) * 0.16)
        b.clock("weekday", "%A", nx, y + h / 2 - CAP * name - name * 0.25, name,
                width=x + w - nx, font="text_font", tabular=False)  # fmt: skip
        b.clock("month", "%B %Y", nx, y + h / 2 + name * 0.25, name * 0.8, color=b.c("text3"),
                width=x + w - nx, font="text_font", tabular=False)  # fmt: skip
        return
    size = min(h * 0.46, w * 0.5)
    name = max(8, min(size * 0.26, w * 0.14))
    block = CAP * size + name * 0.9 + CAP * name + name * 0.6 + CAP * name * 0.8
    top = y + (h - block) / 2
    b.clock("day", "%d", x, top, size, color=accent, glow=round(0.5 * b.glow, 2),
            glow_radius=max(2, round(size / 14)))  # fmt: skip
    top += CAP * size + name * 0.9
    b.clock("weekday", "%A", x, top, name, width=w, font="text_font", tabular=False)
    top += CAP * name + name * 0.6
    b.clock("month", "%B %Y", x, top, name * 0.8, color=b.c("text3"), width=w,
            font="text_font", tabular=False)  # fmt: skip


def build_weather(b: _Build, _src: Source) -> None:
    """The weather of the configured place, with a forecast when there is room.

    A calendar sheet stands in while there is no weather. The forecast shows
    days or hours (``forecast``): as columns beside or under the weather now,
    as rows in a tall module.
    """
    b.card()
    start = len(b.out)
    _date_layout(b)
    for widget in b.out[start:]:
        widget["needs"] = "!weather.temperature"
    start = len(b.out)
    chosen = b.module["forecast"]
    mode = {"auto": "days", "both": "days"}.get(chosen, chosen)
    a = b.aspect
    if mode != "off" and a >= 3.0:  # long: now | forecast
        width = min(b.iw * 0.4, b.ih * 2.0)
        _weather_now(b, b.ix, b.iy, width, b.ih, details=False)
        _forecast(b, b.ix + width + b.pad, b.iy, b.iw - width - b.pad, b.ih, mode)
    elif mode != "off" and b.large and a >= 1.6:  # big and wide: now with details | forecast
        width = b.iw * 0.4
        _weather_now(b, b.ix, b.iy, width, b.ih, details=True)
        x, w = b.ix + width + b.pad, b.iw - width - b.pad
        both = chosen == "both" or (chosen == "auto" and b.ih >= b.ts * 14)
        if both:  # days above, hours below
            half = (b.ih - b.pad) / 2
            _forecast(b, x, b.iy, w, half, "days")
            _forecast(b, x, b.iy + half + b.pad, w, half, "hours", prefix="h")
        else:
            _forecast(b, x, b.iy, w, b.ih, mode)
    elif mode != "off" and b.large:  # big: now above the forecast
        top = b.ih * 0.5
        _weather_now(b, b.ix, b.iy, b.iw, top, details=False)
        _forecast(b, b.ix, b.iy + top + b.pad * 0.5, b.iw, b.ih - top - b.pad * 0.5, mode)
    elif mode != "off" and a <= 0.72 and b.ih >= b.iw * 1.8:  # tall: the forecast as rows
        top = b.ih * 0.42
        _weather_now(b, b.ix, b.iy, b.iw, top, details=False)
        _forecast(
            b, b.ix, b.iy + top + b.pad * 0.5, b.iw, b.ih - top - b.pad * 0.5, mode, rows=True
        )
    else:
        _weather_now(b, b.ix, b.iy, b.iw, b.ih, details=b.large or a >= 2.3)
    for widget in b.out[start:]:
        own = widget.get("needs", "")
        widget["needs"] = f"weather.temperature,{own}" if own else "weather.temperature"


def _weather_now(b: _Build, x: float, y: float, w: float, h: float, details: bool) -> None:
    """Symbol, temperature and sky in a box; feels-like, humidity and wind if asked."""
    icon_color = b.tint("text")
    desc = b.ts * 1.25
    a = w / max(1, h)
    if a <= 0.72:  # tall: icon, temperature, sky, then the details as rows
        size = min(w * 0.42, h * 0.18)
        b.icon("icon", "weather", x, y, size, icon_color)
        temp_top = y + size * 1.25
        b.add("weather", "temp", field="temperature", format="{value:.0f}°",
              **_temp_fields(b, x, temp_top, size * 1.1, w))  # fmt: skip
        desc_top = temp_top + CAP * size * 1.1 + b.ts * 1.1
        _weather_desc(b, x, desc_top, w, desc)
        room = y + h - (desc_top + CAP * desc + b.ts * 1.4)
        rows = min(3, int(room // (b.ts * 2.4))) if details or room > b.ts * 3 else 0
        for i, (field, icon, fmt) in enumerate(_WEATHER_DETAILS[:rows]):
            row_y = y + h - (rows - i) * b.ts * 2.4 + b.ts * 0.6
            b.icon(f"{field}-icon", icon, x, row_y - b.ts * 0.25, b.ts * 1.3, b.c("text3"))
            b.add("weather", field, field=field, format=fmt,
                  **b._text_fields(b.ts * 1.15, row_y, "text_font", x=round(x + b.ts * 1.8),
                                   color=b.c("text2"), max_width=round(w - b.ts * 1.8),
                                   tabular=True))  # fmt: skip
        return
    details = details and h > b.ts * 7
    main_h = h * (0.62 if details else 1.0)
    if a >= 1.45:
        size = min(main_h * 0.62, w * 0.22)
        b.icon("icon", "weather", x, y + (main_h * 0.7 - size) / 2, size, icon_color)
        b.add("weather", "temp", field="temperature", format="{value:.0f}°",
              **_temp_fields(b, x + size * 1.2, y + (main_h * 0.7 - CAP * size) / 2, size,
                             w - size * 1.2))  # fmt: skip
        desc_top = y + main_h * 0.7 + b.ts * 0.2
    else:
        size = min(h * 0.34, w * 0.36)
        b.icon("icon", "weather", x, y, size, icon_color)
        b.add("weather", "temp", field="temperature", format="{value:.0f}°",
              **_temp_fields(b, x + w, y + size * 0.12, size, w * 0.6, "right"))  # fmt: skip
        desc_top = y + size + b.ts * 1.2
    _weather_desc(b, x, desc_top, w, desc)
    if details:
        row_y = y + h - CAP * b.ts * 1.1
        b.add("rect", "rule", x=round(x), y=round(row_y - b.ts * 1.6), w=round(w), h=1,
              color=b.c("line"))  # fmt: skip
        count = 3 if w / 3 >= b.ts * 6.5 else 2
        step = w / count
        for i, (field, icon, fmt) in enumerate(_WEATHER_DETAILS[:count]):
            col_x = x + i * step
            b.icon(f"{field}-icon", icon, col_x, row_y - b.ts * 0.25, b.ts * 1.3, b.c("text3"))
            b.add("weather", field, field=field, format=fmt,
                  **b._text_fields(b.ts * 1.1, row_y, "text_font", x=round(col_x + b.ts * 1.7),
                                   color=b.c("text2"), max_width=round(step - b.ts * 2),
                                   tabular=True))  # fmt: skip


def _forecast(b: _Build, x: float, y: float, w: float, h: float, mode: str,
              rows: bool = False, prefix: str = "f") -> None:  # fmt: skip
    """Days (today first) or hours (every ``step`` hours) as columns, or as rows."""
    step = max(1, min(6, b.module["step"]))
    limit = 7 if mode == "days" else 24 // step
    if rows:
        count = int(_clamp(h // (b.ts * 2.6), 1, limit))
    else:
        count = int(_clamp(w // max(b.ts * 5.0, h * 0.7), 1, limit))  # readable columns
    slots = [i for i in range(count)] if mode == "days" else [step * (i + 1) for i in range(count)]
    base = "weather.day" if mode == "days" else "weather.hour"
    label = "name" if mode == "days" else "time"
    for i, n in enumerate(slots):
        key = f"{base}.{n}"
        tag = f"{prefix}{i}"
        first = len(b.out)
        if rows:
            row = h / count
            mid = y + i * row + row / 2
            size = min(row * 0.42, b.ts * 1.4)
            b.value(f"{tag}-label", f"{key}.{label}", "{value}", x, mid - CAP * b.ts / 2, b.ts,
                    color=b.c("text3"), width=w * 0.34, font="text_font", tabular=False,
                    fallback="")  # fmt: skip
            icon = min(row * 0.7, b.ts * 2.2)
            b.icon(f"{tag}-icon", "weather", x + w * 0.36, mid - icon / 2, icon, b.tint("text"))
            b.out[-1]["sensor"] = f"{key}.code"
            first_temp = "high" if mode == "days" else "temperature"
            b.value(f"{tag}-temp", f"{key}.{first_temp}", "{value:.0f}°", x + w,
                    mid - CAP * size / 2, size, align="right", width=w * 0.3)  # fmt: skip
        else:
            col = w / count
            cx = x + col * i + col / 2
            label_size = min(b.ts, col * 0.2)
            b.value(f"{tag}-label", f"{key}.{label}", "{value}", cx, y + h * 0.04, label_size,
                    color=b.c("text3"), align="center", width=col * 0.92, font="text_font",
                    tabular=False, fallback="")  # fmt: skip
            icon = min(col * 0.52, h * 0.3)
            icon_y = y + h * 0.04 + CAP * label_size + h * 0.08
            b.icon(f"{tag}-icon", "weather", cx - icon / 2, icon_y, icon, b.tint("text"))
            b.out[-1]["sensor"] = f"{key}.code"
            size = min(col * 0.26, h * 0.16)
            temp_top = icon_y + icon + h * 0.07
            if mode == "days":
                b.value(f"{tag}-high", f"{key}.high", "{value:.0f}°", cx - col * 0.04, temp_top,
                        size, align="right", width=col * 0.46)  # fmt: skip
                b.value(f"{tag}-low", f"{key}.low", "{value:.0f}°", cx + col * 0.04, temp_top, size,
                        color=b.c("text3"), width=col * 0.46)  # fmt: skip
            else:
                b.value(f"{tag}-temp", f"{key}.temperature", "{value:.0f}°", cx, temp_top, size,
                        align="center", width=col * 0.9)  # fmt: skip
            rain_top = temp_top + CAP * size + h * 0.08
            if rain_top + CAP * label_size <= y + h:  # room for the chance of rain
                b.value(f"{tag}-rain", f"{key}.rain", "{value:.0f}%", cx, rain_top, label_size,
                        color=b.tint("net"), align="center", width=col * 0.9, font="text_font",
                        hide_if_missing=True)  # fmt: skip
        for widget in b.out[first:]:
            widget["needs"] = f"{key}.code"


_WEATHER_DETAILS = (
    ("apparent_temperature", "temperature", "{value:.0f}°"),
    ("humidity", "humidity", "{value:.0f}%"),
    ("wind_speed", "wind", "{value:.0f} km/h"),
)


def _weather_desc(b: _Build, x: float, top: float, w: float, size: float) -> None:
    b.add("weather", "desc", field="description", format="{value}", fallback="",
          **b._text_fields(size, top, "text_font", x=round(x), color=b.c("text2"),
                           max_width=round(w), fit="ellipsis", tabular=False))  # fmt: skip


def _temp_fields(b: _Build, x: float, cap_top: float, size: float, width: float,
                 align: str = "left") -> dict[str, Any]:  # fmt: skip
    return b._text_fields(size, cap_top, "display_font", x=round(x), color=b.c("text"),
                          align=align, max_width=round(width), tabular=True)  # fmt: skip


def build_system(b: _Build, _src: Source) -> None:
    """The names of the processor and graphics card, and the uptime."""
    b.card()
    rows = (
        ("cpu.name", "{value}", "CPU", "cpu"),
        ("gpu.name", "{value}", "GPU", "gpu"),
        ("sys.uptime", "{value:duration}", t("Uptime"), "accent"),
    )
    row_min = b.ts * 3.4
    count = int(_clamp(b.ih // row_min, 1, 3))
    row = b.ih / count
    for i, (sensor, fmt, text, role) in enumerate(rows[:count]):
        top = b.iy + i * row + (row - (CAP * b.ts + b.ts * 0.7 + CAP * b.ts * 1.3)) / 2
        b.label(f"r{i}-label", text, b.ix, top, b.ts, color=b.c(role), width=b.iw)
        b.value(f"r{i}", sensor, fmt, b.ix, top + CAP * b.ts + b.ts * 0.7, b.ts * 1.3,
                color=b.c("text"), width=b.iw, font="text_font", hide_if_missing=True,
                fallback="", tabular=False, fit="ellipsis")  # fmt: skip


# -- time and the sky ----------------------------------------------------------


def build_sun(b: _Build, _src: Source) -> None:
    """Sunrise and sunset under the day's arc; the moon's phase beside or below.

    Without a place (``[weather]`` latitude and longitude) only the moon shows.
    """
    b.card()
    a = b.aspect
    start = len(b.out)
    b.needs = "sun.rise"
    if b.large and a < 1.6:
        top = b.ih * 0.58
        _sun(b, b.ix, b.iy, b.iw, top, details=True)
        _moon(b, b.ix, b.iy + top + b.pad, b.iw, b.ih - top - b.pad, details=True)
    elif a >= 1.45:
        width = (b.iw - b.pad * 2) * 0.5
        _sun(b, b.ix, b.iy, width, b.ih, details=b.large)
        _moon(b, b.ix + width + b.pad * 2, b.iy, b.iw - width - b.pad * 2, b.ih,
              details=b.large)  # fmt: skip
    elif a <= 0.72 and b.ih > b.iw * 1.5:
        top = b.ih * 0.55
        _sun(b, b.ix, b.iy, b.iw, top)
        _moon(b, b.ix, b.iy + top + b.pad, b.iw, b.ih - top - b.pad)
    else:
        _sun(b, b.ix, b.iy, b.iw, b.ih)
    first = len(b.out)
    b.needs = "!sun.rise"
    _moon(b, b.ix, b.iy, b.iw, b.ih, details=b.large or a >= 1.45)
    for widget in b.out[first:]:
        widget["id"] = "only-" + widget["id"]
    b.needs = ""
    del start


def _sun(b: _Build, x: float, y: float, w: float, h: float, details: bool = False) -> None:
    color = b.tint("warn")
    text_h = b.ts * (4.6 if details else 2.4)
    d = min(w, (h - text_h) * 2)
    cx = x + w / 2
    base = y + d / 2  # the horizon: the arc's ends
    b.add(
        "gauge", "arc", x=round(cx - d / 2), y=round(y), w=round(d), h=round(d),
        sensor="sun.progress", min=0, max=1, start_angle=180, end_angle=360,
        thickness=max(3, round(d * 0.055)), color=b.deep(color), color2=color,
        background=b.c("track"), cap="round", glow=round(0.45 * b.glow, 2),
        glow_radius=max(2, round(d * 0.04)), smooth=True,
    )  # fmt: skip
    icon = d * 0.26
    b.icon("sun-icon", "sun", cx - icon / 2, base - icon - d * 0.04, icon, color)
    b.out[-1]["needs"] = "sun.progress"
    b.icon("night-icon", "moon", cx - icon / 2, base - icon - d * 0.04, icon, b.c("text2"))
    b.out[-1]["needs"] = "!sun.progress"
    size = b.ts * 1.3
    cap = base + b.ts * 0.7
    mark = b.ts * 1.5
    # the times under the arc's ends (not further out than a little)
    left = max(x, cx - d / 2 - mark * 0.6)
    right = min(x + w, cx + d / 2 + mark * 0.6)
    needed = 2 * (mark * 1.2 + size * 2.7) + b.ts
    if right - left < needed:  # a small arc: the times use the whole width
        left, right = x, x + w
        size = min(size, max(7.0, (w - b.ts - 2 * mark * 1.2) / 5.4))
    half = (right - left) / 2
    b.icon("rise-icon", "sunrise", left, cap + CAP * size / 2 - mark / 2, mark, b.c("text3"))
    b.value("rise", "sun.rise", "{value}", left + mark * 1.2, cap, size, width=half - mark * 1.2)
    b.icon("set-icon", "sunset", right - mark, cap + CAP * size / 2 - mark / 2, mark,
           b.c("text3"))  # fmt: skip
    b.value("set", "sun.set", "{value}", right - mark * 1.2, cap, size, align="right",
            width=half - mark * 1.2)  # fmt: skip
    if details:
        line = cap + CAP * size + b.ts * 1.3
        b.label("daylight-label", t("Daylight"), left, line, b.ts * 0.9, width=half)
        b.value("daylight", "sun.daylight", "{value:duration}", right, line - b.ts * 0.05,
                b.ts * 1.05, align="right", width=half, font="text_font",
                color=b.c("text2"))  # fmt: skip


def _moon(b: _Build, x: float, y: float, w: float, h: float, details: bool = False) -> None:
    beside = w / max(1, h) >= 1.2 and w >= b.ts * 14
    lines = [("name", "moon.name", "{value}", 1.25, "text"),
             ("lit", "moon.illumination", t("{value:.0f}% lit"), 1.0, "text2")]  # fmt: skip
    if details:
        lines.append(("full", "moon.full_in", t("Full moon in {value:.0f} days"), 0.95, "text3"))
    block = sum(CAP * b.ts * scale for *_rest, scale, _c in lines) + b.ts * 0.9 * (len(lines) - 1)
    if beside:
        disc = min(h * 0.82, w * 0.38)
        dx, dy = x, y + (h - disc) / 2
        tx, align, width = x + disc + b.pad * 1.2, "left", w - disc - b.pad * 1.2
        top = y + (h - block) / 2
    else:
        disc = min(w * 0.5, h * 0.55, h - block - b.ts * 1.2)
        dx, dy = x + (w - disc) / 2, y + (h - disc - block - b.ts * 1.2) / 2
        tx, align, width = x + w / 2, "center", w
        top = dy + disc + b.ts * 1.2
    if disc < 8:
        return
    b.add("moon", "moon", x=round(dx), y=round(dy), size=round(disc), sensor="moon.phase",
          color=b.c("text"), color2=mix(b.c("track"), b.c("text3"), 0.22),
          glow=round(0.25 * b.glow, 2),
          glow_radius=max(2, round(disc * 0.08)))  # fmt: skip
    for name, key, fmt, scale, role in lines:
        size = b.ts * scale
        b.value(name, key, fmt, tx, top, size, color=b.c(role), align=align, width=width,
                font="text_font", tabular=False, fallback="", fit="ellipsis",
                hide_if_missing=True)  # fmt: skip
        top += CAP * size + b.ts * 0.9


def _face(b: _Build, name: str, x: float, y: float, d: float, zone: str = "") -> None:
    b.add(
        "analog", name, x=round(x), y=round(y), w=round(d), h=round(d), timezone=zone,
        color=b.c("text"), color2=b.c("accent") if not b.module.get("color") else b.tint("accent"),
        face=mix(b.c("surface"), b.c("track"), 0.7) if b.card_on else b.c("surface"),
        marks=b.c("text3"), seconds=d >= 90,
        glow=round(0.15 * b.glow, 2), glow_radius=max(2, round(d * 0.03)),
    )  # fmt: skip


def build_analog(b: _Build, _src: Source) -> None:
    """A clock face; the time and the date beside or under it when there is room."""
    b.card()
    zone = b.module["timezone"]
    a = b.aspect
    if a >= 1.6:  # face | time and date
        d = b.ih
        _face(b, "face", b.ix, b.iy, d, zone)
        x = b.ix + d + b.pad * 1.6
        w = b.ix + b.iw - x
        size = min(b.ih * 0.3, w / 3.0)
        date_size = max(8, size * 0.32)
        block = CAP * size + date_size * 1.2 + CAP * date_size
        top = b.iy + (b.ih - block) / 2
        b.clock("time", "%H:%M", x, top, size, width=w, timezone=zone)
        fmt = "%A · %d %B" if w > size * 4.2 else "%a %d %b"
        b.clock("date", fmt, x, top + CAP * size + date_size * 1.2, date_size,
                color=b.c("text2"), width=w, font="text_font", tabular=False,
                timezone=zone)  # fmt: skip
        if b.module["title"]:
            b.label("title", b.module["title"], x, top - b.ts * 1.6, b.ts, width=w)
        return
    tall = a <= 0.72 and b.ih > b.iw * 1.3
    label = b.module["title"]
    room = b.ts * 2.4 if (tall or label) else 0
    d = min(b.iw, b.ih - room)
    x, y = b.ix + (b.iw - d) / 2, b.iy + (b.ih - d - room) / 2
    _face(b, "face", x, y, d, zone)
    if tall and not label:
        b.clock("date", "%a %d %b", b.ix + b.iw / 2, y + d + b.ts * 1.0, b.ts * 1.2,
                color=b.c("text2"), align="center", width=b.iw, font="text_font",
                tabular=False, timezone=zone)  # fmt: skip
    elif label:
        b.label("title", label, b.ix + b.iw / 2, y + d + b.ts * 1.0, b.ts, align="center",
                width=b.iw)  # fmt: skip


def _cities(b: _Build) -> list[tuple[str, str]]:
    found = []
    for line in (b.module["items"] or default_items("world")).splitlines():
        zone, _, name = line.partition("=")
        zone = zone.strip()
        if not zone or zone.startswith("#") or timezones.zone(zone) is None:
            continue
        found.append((zone, name.strip() or zone.rsplit("/", 1)[-1].replace("_", " ")))
    return found


def build_world(b: _Build, _src: Source) -> None:
    """The time in other places: rows of cities, clock faces when big."""
    b.card()
    cities = _cities(b)
    if not cities:
        b.label("none", t("Add places in the settings"), b.ix + b.iw / 2,
                b.iy + b.ih / 2 - CAP * b.ts / 2, b.ts, align="center", width=b.iw)  # fmt: skip
        return
    top = 0.0
    if b.module["title"]:
        top = b.head("globe", b.module["title"], b.tint("accent"))
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    if b.large and h > b.ts * 9:  # faces in a grid
        n = len(cities)
        best = (0.0, 1)
        for columns in range(1, n + 1):
            lines = -(-n // columns)
            cell_w = (w - (columns - 1) * b.pad) / columns
            cell_h = (h - (lines - 1) * b.pad) / lines
            size = min(cell_w, cell_h - b.ts * 3.2)
            if size > best[0]:
                best = (size, columns)
        d, columns = best
        d *= 0.92
        lines = -(-n // columns)
        cell_w = (w - (columns - 1) * b.pad) / columns
        cell_h = (h - (lines - 1) * b.pad) / lines
        for i, (zone, name) in enumerate(cities):
            row, col = divmod(i, columns)
            cx = x + col * (cell_w + b.pad) + cell_w / 2
            cy = y + row * (cell_h + b.pad) + (cell_h - d - b.ts * 3.2) / 2
            _face(b, f"c{i}-face", cx - d / 2, cy, d, zone)
            b.label(f"c{i}-name", name, cx, cy + d + b.ts * 0.8, b.ts * 0.95, align="center",
                    width=cell_w)  # fmt: skip
            b.clock(f"c{i}-time", "%a %H:%M", cx, cy + d + b.ts * 2.2, b.ts, color=b.c("text3"),
                    align="center", width=cell_w, font="text_font", timezone=zone)  # fmt: skip
        return
    n = len(cities)
    best: tuple[float, int] = (1e9, 1)
    for columns in range(1, n + 1):  # cells close to 3.5:1, the shape of a row
        lines = -(-n // columns)
        cell_w, cell_h = w / columns, h / lines
        if cell_h < b.ts * 2.2 and columns < n:
            continue
        score = abs(math.log(max(1e-3, cell_w / max(1.0, cell_h)) / 3.5))
        if score < best[0]:
            best = (score, columns)
    columns = best[1]
    lines = -(-n // columns)
    gap = b.pad * 1.5
    cell_w = (w - (columns - 1) * gap) / columns
    cell_h = h / lines
    stacked = cell_h >= b.ts * 4.6 and cell_w < b.ts * 16
    for i, (zone, name) in enumerate(cities):
        line, col = divmod(i, columns)
        cx = x + col * (cell_w + gap)
        cy = y + line * cell_h
        if stacked:  # name, time, weekday, centred
            size = min(cell_h * 0.32, cell_w * 0.3)
            block = CAP * b.ts + b.ts * 0.9 + CAP * size + b.ts * 0.8 + CAP * b.ts * 0.9
            top = cy + (cell_h - block) / 2
            mid = cx + cell_w / 2
            b.label(f"c{i}-name", name, mid, top, b.ts, color=b.c("text2"), align="center",
                    width=cell_w)  # fmt: skip
            top += CAP * b.ts + b.ts * 0.9
            b.clock(f"c{i}-time", "%H:%M", mid, top, size, align="center", width=cell_w,
                    timezone=zone)  # fmt: skip
            b.clock(f"c{i}-day", "%a", mid, top + CAP * size + b.ts * 0.8, b.ts * 0.9,
                    color=b.c("text3"), align="center", width=cell_w, font="text_font",
                    tabular=False, timezone=zone)  # fmt: skip
            continue
        mid = cy + cell_h / 2
        size = min(cell_h * 0.5, b.ts * 2.2, cell_w * 0.16)
        day = cell_h >= b.ts * 3.4
        name_top = mid - (CAP * b.ts + (b.ts * 0.8 + CAP * b.ts * 0.9 if day else 0)) / 2
        b.label(f"c{i}-name", name, cx, name_top, b.ts, color=b.c("text2"),
                width=cell_w * 0.55)  # fmt: skip
        if day:
            b.clock(f"c{i}-day", "%a", cx, name_top + CAP * b.ts + b.ts * 0.8, b.ts * 0.9,
                    color=b.c("text3"), width=cell_w * 0.5, font="text_font",
                    tabular=False, timezone=zone)  # fmt: skip
        b.clock(f"c{i}-time", "%H:%M", cx + cell_w, mid - CAP * size / 2, size, align="right",
                width=cell_w * 0.45, timezone=zone)  # fmt: skip


def build_countdown(b: _Build, _src: Source) -> None:
    """The days (or hours) left until a day you choose; every year with "12-24"."""
    from libre_panel.render.countdown import parse_target

    b.card()
    target = b.module["target"] or "01-01"
    parsed = parse_target(target)
    timed = parsed is not None and parsed[3] is not None
    color = b.tint("accent")
    title = b.module["title"] or (t("New Year") if not b.module["target"] else t("Countdown"))
    top = b.head("hourglass", title, color)
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    fields = {"target": target}
    wide = w / max(1, h) >= 2.2
    number_w = w * (0.45 if wide else 1.0)
    size = min(h * (0.5 if not b.large else 0.42), number_w * 0.42)
    unit = b.ts * 1.1
    block = CAP * size + b.ts * 0.9 + CAP * unit
    cap = y + (h - block) / 2 if wide or not b.large else y + h * 0.08
    b.add("countdown", "number", part="number", **fields,
          **b._text_fields(size, cap, "display_font", x=round(x), color=b.c("text"),
                           max_width=round(number_w), tabular=True, glow=round(0.3 * b.glow, 2),
                           glow_radius=max(2, round(size / 14))))  # fmt: skip
    b.add("countdown", "unit", part="unit", **fields,
          **b._text_fields(unit, cap + CAP * size + b.ts * 0.9, "text_font", x=round(x),
                           color=color, max_width=round(number_w), tabular=False))  # fmt: skip
    rest_x, rest_y, rest_w = x, cap + block + b.ts * 1.6, w
    if wide:
        rest_x, rest_w = x + w * 0.5, w * 0.5
        rest_y = y + (h - (CAP * b.ts * 1.3 + (b.ts * 1.8 if timed else 0))) / 2
    lines = []
    if timed:
        lines.append(("clock", b.ts * 1.3, b.c("text2"), "display_font"))
    if wide or b.large:
        lines.append(("date", b.ts, b.c("text3"), "text_font"))
    for part, line_size, line_color, font in lines:
        if rest_y + CAP * line_size > b.iy + b.ih:
            break
        b.add("countdown", part, part=part, **fields,
              **b._text_fields(line_size, rest_y, font, x=round(rest_x), color=line_color,
                               max_width=round(rest_w), tabular=part == "clock",
                               fit="ellipsis"))  # fmt: skip
        rest_y += CAP * line_size + b.ts * 1.0


def build_image(b: _Build, _src: Source) -> None:
    """A picture, or several in turn (``items``: one per line, "photos/*.jpg" a folder)."""
    b.card()
    pictures = [line.strip() for line in b.module["items"].splitlines() if line.strip()]
    radius = round(b.style["radius"] * b.u / 200) if b.style["card"] != "none" else 0
    if not pictures:
        b.add("rect", "frame", x=b.x, y=b.y, w=b.w, h=b.h, color=b.c("surface"),
              outline=b.c("line"), radius=radius)  # fmt: skip
        icon = min(b.w, b.h) * 0.28
        b.icon("icon", "image", b.x + (b.w - icon) / 2, b.y + b.h / 2 - icon * 0.75, icon,
               b.c("text3"))  # fmt: skip
        b.label("hint", t("Add pictures in the settings"), b.x + b.w / 2,
                b.y + b.h / 2 + icon * 0.45, b.ts * 0.9, align="center",
                width=b.w * 0.9)  # fmt: skip
        return
    b.add("image", "picture", x=b.x, y=b.y, w=b.w, h=b.h, src="", slides="\n".join(pictures),
          seconds=max(1, b.module["seconds"]), fit="cover", radius=radius)  # fmt: skip
    if b.module["title"]:
        shade = round(b.h * 0.4)
        b.add("rect", "shade", x=b.x, y=b.y + b.h - shade, w=b.w, h=shade, color="#00000000",
              color2=b.c("bg2") + "CC", radius=radius)  # fmt: skip
        b.label("title", b.module["title"], b.x + b.pad * 2, b.y + b.h - b.pad * 2 - CAP * b.ts,
                b.ts * 1.05, color=b.c("text"), width=b.w - b.pad * 4)  # fmt: skip


# -- music and the calendar ----------------------------------------------------


def build_music(b: _Build, _src: Source) -> None:
    """What is playing: the cover, title, artist and how far it is."""
    b.card()
    color = b.tint("accent")
    a = b.aspect
    b.needs = "media.title"
    if a >= 1.45:  # cover | words
        d = b.ih
        _cover(b, b.ix, b.iy, d)
        _song(b, b.ix + d + b.pad * 1.4, b.iy, b.iw - d - b.pad * 1.4, b.ih, color)
    elif (a <= 0.72 or b.large) and b.ih > b.ts * 12:  # cover over words
        d = min(b.iw, b.ih * 0.55)
        _cover(b, b.ix + (b.iw - d) / 2, b.iy, d)
        _song(b, b.ix, b.iy + d + b.pad, b.iw, b.ih - d - b.pad, color)
    else:
        b.icon("icon", "music", b.ix, b.iy - b.ts * 0.1, b.ts * 1.5, color)
        _song(b, b.ix, b.iy + b.ts * 2.2, b.iw, b.ih - b.ts * 2.2, color, state=False)
    b.needs = "!media.title"
    icon = min(b.iw, b.ih) * 0.3
    b.icon("idle-icon", "music", b.ix + (b.iw - icon) / 2, b.iy + b.ih / 2 - icon * 0.8, icon,
           b.c("text3"))  # fmt: skip
    b.label("idle", t("Nothing playing"), b.ix + b.iw / 2, b.iy + b.ih / 2 + icon * 0.45, b.ts,
            align="center", width=b.iw)  # fmt: skip
    b.needs = ""


def _cover(b: _Build, x: float, y: float, d: float) -> None:
    radius = round(b.style["radius"] * d / 200)
    b.add("rect", "cover-frame", x=round(x), y=round(y), w=round(d), h=round(d),
          color=mix(b.c("track"), b.c("surface"), 0.3), radius=radius)  # fmt: skip
    icon = d * 0.36
    b.icon("cover-icon", "music", x + (d - icon) / 2, y + (d - icon) / 2, icon, b.c("text3"))
    b.add("image", "cover", x=round(x), y=round(y), w=round(d), h=round(d), src="@media.cover",
          fit="cover", radius=radius)  # fmt: skip


def _song(b: _Build, x: float, y: float, w: float, h: float, color: str,
          state: bool = True) -> None:  # fmt: skip
    """Title, artist (album) and the progress, filling the height."""
    title = min(b.ts * 1.9, h * 0.2, w * 0.12)
    small = b.ts * 1.05
    lines = [("title", "media.title", title, "text", "display_font"),
             ("artist", "media.artist", small * 1.15, "text2", "text_font")]  # fmt: skip
    if h > b.ts * 11:
        lines.append(("album", "media.album", small, "text3", "text_font"))
    bar_block = b.ts * 3.2 if h > b.ts * 6 else 0

    def needed() -> float:
        words = sum(CAP * size + b.ts * 0.9 for *_n, size, _c, _f in lines)
        return words + bar_block + (CAP * b.ts + b.ts * 1.0 if state else 0)

    if needed() > h and len(lines) > 2:
        lines.pop()  # the album goes first
    if needed() > h:
        state = False
    block = needed()
    top = y + max(0.0, (h - block) / 2)
    if state:
        b.value("state", "media.state", "{value}", x, top, b.ts, color=color, width=w,
                font="text_font", tabular=False, fallback="", hide_if_missing=True)  # fmt: skip
        top += CAP * b.ts + b.ts * 1.0
    for name, key, size, role, font in lines:
        b.value(name, key, "{value}", x, top, size, color=b.c(role), width=w, font=font,
                tabular=False, fallback="", fit="ellipsis", hide_if_missing=True)  # fmt: skip
        top += CAP * size + b.ts * 0.9
    if bar_block:
        top += b.ts * 0.4
        b.bar("progress", "media.progress", x, top, w, max(3, b.ts * 0.32), color, 0, 1)
        b.out[-1]["hide_if_missing"] = True
        times = top + b.ts * 0.9
        b.value("position", "media.position", "{value:clock}", x, times, b.ts * 0.95,
                color=b.c("text3"), width=w / 2, hide_if_missing=True)  # fmt: skip
        b.value("length", "media.duration", "{value:clock}", x + w, times, b.ts * 0.95,
                color=b.c("text3"), align="right", width=w / 2, hide_if_missing=True)  # fmt: skip


def build_agenda(b: _Build, _src: Source) -> None:
    """The next events of your calendars (``[sensors.calendar]``)."""
    b.card()
    color = b.tint("accent")
    top = b.head("calendar", b.module["title"] or t("Agenda"), color)
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    stacked = w < b.ts * 24  # the time above the title, else beside it
    columns = 2 if not stacked and w > b.ts * 50 else 1
    gap = b.pad * 2
    column_w = (w - (columns - 1) * gap) / columns
    row = b.ts * (3.4 if stacked else 2.3)
    per_column = int(_clamp(h // row, 1, 12))
    when_w = 0 if stacked else min(column_w * 0.36, b.ts * 11)
    for i in range(per_column * columns):
        key = f"calendar.{i + 1}"
        col, line = divmod(i, per_column)
        cx, cy = x + col * (column_w + gap), y + line * row
        first = len(b.out)
        mark = {"y": round(cy + b.ts * 0.15), "w": max(2, round(b.ts * 0.2)),
                "h": round(row - b.ts * 0.9), "radius": max(1, round(b.ts * 0.1))}  # fmt: skip
        b.add("rect", f"e{i}-mark", x=round(cx), color=b.c("line"), **mark)
        b.add("rect", f"e{i}-now", x=round(cx), color=color, **mark)
        b.out[-1]["needs"] = f"{key}.now"  # the one going on is marked
        inset = b.ts * 0.8
        if stacked:
            b.value(f"e{i}-when", f"{key}.when", "{value}", cx + inset, cy + b.ts * 0.2,
                    b.ts * 0.95, color=b.c("text3"), width=column_w - inset, font="text_font",
                    tabular=False, fallback="", fit="ellipsis")  # fmt: skip
            b.value(f"e{i}-title", f"{key}.title", "{value}", cx + inset, cy + b.ts * 1.55,
                    b.ts * 1.25, color=b.c("text"), width=column_w - inset, font="text_font",
                    tabular=False, fallback="", fit="ellipsis")  # fmt: skip
        else:
            mid = cy + row / 2 - b.ts * 0.45
            b.value(f"e{i}-when", f"{key}.when", "{value}", cx + inset,
                    mid - CAP * b.ts * 0.5 / 2, b.ts * 0.95, color=b.c("text3"),
                    width=when_w - inset, font="text_font", tabular=False, fallback="",
                    fit="ellipsis")  # fmt: skip
            b.value(f"e{i}-title", f"{key}.title", "{value}", cx + when_w,
                    mid - CAP * b.ts * 0.1, b.ts * 1.2, color=b.c("text"),
                    width=column_w - when_w, font="text_font", tabular=False, fallback="",
                    fit="ellipsis")  # fmt: skip
        for widget in b.out[first:]:
            own = widget.get("needs", "")
            widget["needs"] = f"{key}.title,{own}" if own else f"{key}.title"
    mid_y = y + h / 2 - CAP * b.ts / 2
    b.label("none", t("No upcoming events"), x + w / 2, mid_y, b.ts, align="center", width=w)
    b.out[-1]["needs"] = "calendar.events,!calendar.1.title"
    b.label("setup", t("No calendar set up"), x + w / 2, mid_y, b.ts, align="center", width=w)
    b.out[-1]["needs"] = "!calendar.events"


def build_game(b: _Build, _src: Source) -> None:
    """Frames per second of the game being played (``[sensors.presentmon]``)."""
    b.card()
    color = b.tint("gpu")
    a = b.aspect
    b.needs = "game.fps"
    title = b.module["title"]
    top = b.ts * 2.4
    b.icon("icon", "gamepad", b.ix, b.iy - b.ts * 0.1, b.ts * 1.5, color)
    if title:
        b.label("title", title, b.ix + b.ts * 2.1, b.iy + b.ts * 0.15, b.ts * 1.05, color=color,
                width=b.iw - b.ts * 2.1)  # fmt: skip
    else:
        b.value("title", "game.app", "{value}", b.ix + b.ts * 2.1, b.iy + b.ts * 0.15,
                b.ts * 1.05, color=color, width=b.iw - b.ts * 2.1, font="text_font",
                tabular=False, fallback="", fit="ellipsis")  # fmt: skip
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    graph = b.large or a >= 3.0 or (a <= 0.72 and h > b.ts * 12)
    stacked = b.large or a <= 0.72  # the history under the number
    number_h = h * 0.55 if graph and stacked else h
    number_w = w if not (graph and not stacked) else w * 0.45
    size = min(number_h * 0.62, number_w * 0.34)
    cap = y + (number_h - CAP * size) / 2 - (b.ts * 0.6 if number_h > b.ts * 6 else 0)
    b.value("fps", "game.fps", "{value:.0f}", x, cap, size, width=number_w * 0.62,
            glow=round(0.3 * b.glow, 2), glow_radius=max(2, round(size / 14)))  # fmt: skip
    unit_x = x + number_w * 0.64
    b.label("unit", "FPS", unit_x, cap + CAP * size - CAP * b.ts, b.ts, color=color,
            width=number_w * 0.36)  # fmt: skip
    if number_h > b.ts * 6.5:
        rows = (("low", "game.low", "{value:.0f}", t("1% low")),
                ("frametime", "game.frametime", "{value:.1f} ms", t("Frame time")))  # fmt: skip
        for i, (name, key, fmt, text) in enumerate(rows):
            line = cap + i * b.ts * 2.4
            if line + CAP * b.ts * 1.2 > y + number_h:
                break
            b.label(f"{name}-label", text, unit_x, line, b.ts * 0.85, width=number_w * 0.36)
            b.value(name, key, fmt, unit_x, line + b.ts * 1.1, b.ts * 1.05, color=b.c("text2"),
                    width=number_w * 0.36, hide_if_missing=True)  # fmt: skip
        b.out = [w_ for w_ in b.out if w_["id"] != "unit"]  # the rows say what it is
        b.label("unit", "FPS", x, cap + CAP * size + b.ts * 0.8, b.ts, color=color,
                width=number_w * 0.6)  # fmt: skip
    if graph:
        if stacked:
            gx, gy, gw, gh = x, y + number_h + b.pad, w, h - number_h - b.pad
        else:
            gx = x + number_w + b.pad
            gy, gw, gh = y, x + w - gx, h
        b.graph("history", "game.fps", gx, gy, gw, gh, color, None, None)  # its own range
    b.needs = "!game.fps"
    icon = min(b.iw, b.ih) * 0.3
    b.icon("idle-icon", "gamepad", b.ix + (b.iw - icon) / 2, b.iy + b.ih / 2 - icon * 0.8, icon,
           b.c("text3"))  # fmt: skip
    b.label("idle", t("No game running"), b.ix + b.iw / 2, b.iy + b.ih / 2 + icon * 0.45, b.ts,
            align="center", width=b.iw)  # fmt: skip
    b.needs = ""


def build_text(b: _Build, _src: Source) -> None:
    """A title with the accent line under it."""
    b.card()
    text = b.module["text"] or "Libre Panel"
    size = min(b.ih * 0.5, b.iw / max(2, len(text) * 0.55))
    line = max(2, round(size * 0.07))
    block = CAP * size + size * 0.25 + line
    top = b.iy + (b.ih - block) / 2
    b.add("text", "text", x=round(b.ix), text=text, color=b.c("text"),
          max_width=round(b.iw), **b._text_fields(size, top, "display_font"))  # fmt: skip
    b.add("rect", "line", x=round(b.ix + size * 0.04), y=round(top + CAP * size + size * 0.25),
          w=round(min(b.iw, size * 1.6)), h=line, color=b.tint("accent"), radius=line // 2,
          glow=round(0.7 * b.glow, 2), glow_radius=max(2, line * 3))  # fmt: skip


# -- lists of readings ---------------------------------------------------------


def default_items(kind: str) -> str:
    """What a list module shows while its ``items`` are empty."""
    return {
        "temps": "cpu.temp = CPU\ngpu.temp = GPU\ntemp.*",
        "drives": "disk.*.load",
        "world": "America/New_York = New York\nEurope/London = London\nAsia/Tokyo = Tokyo\n"
        "Australia/Sydney = Sydney",
        "values": f"cpu.load = CPU\ngpu.load = GPU\nmem.load = {t('RAM')}\n"
        f"cpu.temp = {t('CPU temp')}\ngpu.temp = {t('GPU temp')}\nnet.down = {t('Download')}",
    }.get(kind, "")


def _heat_rules(b: _Build, warn: float = 75, crit: float = 88) -> list[dict[str, Any]]:
    return [{"above": warn, "color": b.c("warn")}, {"above": crit, "color": b.c("crit")}]


def build_temps(b: _Build, _src: Source) -> None:
    """Every temperature as a bar, CPU and GPU first; the fans too when there is room."""
    b.card()
    color = b.tint("accent")
    top = b.head("temperature", b.module["title"] or t("Temperatures"), color)
    items = b.module["items"] or default_items("temps")
    fans = "fan.*\ngpu.fan"
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    temps = {"min": 20, "max": 100, "color_rules": _heat_rules(b), "format": "{value:.0f}°C",
             "empty": t("No temperature readings")}  # fmt: skip
    fan_list = {"format": "{value:.0f} RPM", "empty": t("No fan readings"), "uppercase": True}
    if b.aspect >= 3.0 and b.iw > b.ts * 40:  # long: temperatures | fans
        width = (w - b.pad) * 0.6
        b.listing("temps", items, x, y, width, h, color, **temps)
        b.listing("fans", fans, x + width + b.pad, y, w - width - b.pad, h, color, "rows",
                  **fan_list)  # fmt: skip
    elif (b.large or b.aspect <= 0.72) and h > b.ts * 11:  # big: the fans as tiles below
        fan_h = _clamp(h * 0.3, b.ts * 4.4, b.ts * 7)
        b.listing("temps", items, x, y, w, h - fan_h - b.pad, color, **temps)
        b.listing("fans", fans, x, y + h - fan_h, w, fan_h, color, "cells", b.ts * 1.2,
                  levels=False, background=mix(b.c("track"), b.c("surface"), 0.35),
                  max_items=max(1, int(w // (b.ts * 5.5))), columns=0, **fan_list)  # fmt: skip
    else:
        b.listing("temps", items, x, y, w, h, color, "bars" if h > b.ts * 4.5 else "rows", **temps)


def build_cores(b: _Build, _src: Source) -> None:
    """The load of every core: columns side by side, or tiles; the history when big."""
    b.card()
    color = b.tint("cpu")
    top = b.head("cpu", b.module["title"] or t("CPU cores"), color, b.ts * 3.6)
    b.value("value", "cpu.load", "{value:.0f}%", b.ix + b.iw, b.iy, b.ts * 1.6, align="right",
            width=b.iw * 0.3)  # fmt: skip
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    items = "cpu.core.*.load"
    if b.large and h > b.ts * 12:
        cores_h = h * 0.6
        b.listing("cores", items, x, y, w, cores_h, color, "columns", b.ts * 1.1)
        gy = y + cores_h + b.pad
        b.graph("history", "cpu.load", x, gy, w, b.iy + b.ih - gy, color)
    elif b.aspect >= 1.45:
        b.listing("cores", items, x, y, w, h, color, "columns", b.ts * 1.1)
    else:
        b.listing("cores", items, x, y, w, h, color, "cells", b.ts * 1.4)


def build_drives(b: _Build, _src: Source) -> None:
    """Every drive: how full, how much is free; reading and writing when big."""
    b.card()
    color = b.tint("disk")
    top = b.head("disk", b.module["title"] or t("Drives"), color)
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    rates = b.large and h > b.ts * 12
    if rates:
        h -= b.ts * 3.4
    b.listing("drives", b.module["items"] or default_items("drives"), x, y, w, h, color,
              detail="free", detail_format=t("{value:.0f} GB free"), format="{value:.0f}%",
              color_rules=_heat_rules(b, 85, 95), uppercase=False)  # fmt: skip
    if rates:
        cap = b.iy + b.ih - CAP * b.ts * 1.3
        half = w / 2
        for i, (key, icon, name) in enumerate((("disk.read", "download", "read"),
                                               ("disk.write", "upload", "write"))):  # fmt: skip
            cx = x + i * half
            b.icon(f"{name}-icon", icon, cx, cap + CAP * b.ts * 0.65 - b.ts * 0.7, b.ts * 1.4,
                   b.c("text3"))  # fmt: skip
            b.value(name, key, "{value:bytes}/s", cx + b.ts * 1.9, cap, b.ts * 1.3,
                    width=half - b.ts * 2.2, hide_if_missing=True)  # fmt: skip


def build_processes(b: _Build, _src: Source) -> None:
    """The programs that use the most processor time (or memory)."""
    b.card()
    memory = b.module["sort"] == "memory"
    color = b.tint("mem" if memory else "cpu")
    top = b.head("list", b.module["title"] or t("Processes"), color, b.ts * 3.4)
    b.label("sort", "RAM" if memory else "CPU", b.ix + b.iw, b.iy + b.ts * 0.15, b.ts,
            color=b.c("text3"), align="right")  # fmt: skip
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    bars = (b.large or b.aspect <= 0.72) and h > b.ts * 10
    b.listing("processes", "proc.mem.*.value" if memory else "proc.cpu.*.value", x, y, w, h,
              color, "bars" if bars else "rows", format="{value:.1f}%", min=0, max=0,
              uppercase=False, empty=t("Waiting for the first count …"))  # fmt: skip


def build_values(b: _Build, _src: Source) -> None:
    """Readings of your choice as tiles: a small dashboard."""
    b.card()
    color = b.tint("accent")
    y, h = b.iy, b.ih
    if b.module["title"]:
        top = b.head("grid", b.module["title"], color)
        y, h = y + top, h - top
    items = b.module["items"] or default_items("values")
    tiles = "cells" if b.aspect < 3.0 or b.ih > b.ts * 6 else "rows"
    b.listing("values", items, b.ix, y, b.iw, h, color, tiles, b.ts * 2.4, levels=False,
              background=mix(b.c("track"), b.c("surface"), 0.35), format="auto",
              empty=t("Add readings in the settings"))  # fmt: skip


def build_netinfo(b: _Build, _src: Source) -> None:
    """Address, ping and today's traffic; the ping's history when big."""
    b.card()
    color = b.tint("net")
    top = b.head("network", b.module["title"] or t("Network"), color)
    items = "\n".join(
        f"{key} = {name}"
        for key, name in (
            ("net.ip", t("IP address")), ("net.ping", t("Ping")),
            ("net.down", t("Download")), ("net.up", t("Upload")),
            ("net.today.down", t("Received today")), ("net.today.up", t("Sent today")),
        )
    )  # fmt: skip
    x, y, w, h = b.ix, b.iy + top, b.iw, b.ih - top
    graph = (b.large or b.aspect <= 0.72) and h > b.ts * 14
    rows_h = h * 0.62 if graph else h
    b.listing("details", items, x, y, w, rows_h, color, "rows", b.ts * 1.25, format="auto")
    if graph:
        gy = y + rows_h + b.pad
        b.label("ping-title", t("Ping"), x, gy, b.ts * 0.9, width=w * 0.5)
        gy += b.ts * 1.6
        b.graph("ping-history", "net.ping", x, gy, w, b.iy + b.ih - gy, color, 0, None)
        b.out[-1]["needs"] = "net.ping"


def build_battery(b: _Build, _src: Source) -> None:
    """The battery's charge, whether it charges and the time left; its history when big."""
    b.card()
    start = len(b.out)
    color = b.tint("net")
    a = b.aspect
    below = (b.large or a <= 0.72) and b.ih > b.ts * 14  # the history under the battery
    beside = not below and a >= 3.4  # the history on the right
    main_h = b.ih * 0.55 if below else b.ih
    main_w = b.iw * 0.45 if beside else b.iw
    if (main_w / main_h >= 1.45 and not (below and a <= 0.72)) or beside:  # numbers beside
        bh = min(main_h * 0.5, main_w * 0.2)
        bw = bh * 1.9
        _battery_shape(b, b.ix, b.iy + (main_h - bh) / 2, bw, bh, color)
        tx = b.ix + bw + b.pad * 1.6
        size = min(main_h * 0.36, (main_w - bw - b.pad * 1.6) * 0.3)
        cap = b.iy + (main_h - (CAP * size + b.ts * 1.0 + CAP * b.ts * 1.1)) / 2
        b.value("value", "battery.load", "{value:.0f}%", tx, cap, size, width=b.ix + main_w - tx)
        _battery_state(b, tx, cap + CAP * size + b.ts * 1.0, b.ix + main_w - tx)
    else:  # the battery above the numbers
        bw = min(main_w * 0.62, main_h * 0.7)
        bh = bw / 1.9
        size = min(main_h * 0.26, main_w * 0.3)
        block = bh + b.ts * 0.9 + CAP * size + b.ts * 0.9 + CAP * b.ts * 1.1
        y = b.iy + (main_h - block) / 2
        cx = b.ix + main_w / 2
        _battery_shape(b, cx - bw / 2, y, bw, bh, color)
        y += bh + b.ts * 0.9
        b.value("value", "battery.load", "{value:.0f}%", cx, y, size, align="center", width=main_w)
        _battery_state(b, cx, y + CAP * size + b.ts * 0.9, main_w, "center")
    if below:
        gy = b.iy + main_h + b.pad
        b.graph("history", "battery.load", b.ix, gy, b.iw, b.iy + b.ih - gy, color)
    elif beside:
        gx = b.ix + main_w + b.pad
        b.graph("history", "battery.load", gx, b.iy + b.ih * 0.12, b.ix + b.iw - gx, b.ih * 0.76,
                color)  # fmt: skip
    for widget in b.out[start:]:
        widget["needs"] = "battery.load"
    b.label("none", t("No battery"), b.ix + b.iw / 2, b.iy + b.ih / 2 - CAP * b.ts / 2, b.ts,
            align="center", width=b.iw)  # fmt: skip
    b.out[-1]["needs"] = "!battery.load"


def _battery_shape(b: _Build, x: float, y: float, w: float, h: float, color: str) -> None:
    line = max(2, round(h * 0.07))
    nub = max(3, round(w * 0.06))
    body = w - nub - line
    b.add("rect", "battery", x=round(x), y=round(y), w=round(body), h=round(h), color=None,
          outline=b.c("text2"), outline_width=line, radius=round(h * 0.18))  # fmt: skip
    b.add("rect", "battery-nub", x=round(x + body + line * 0.6), y=round(y + h * 0.32),
          w=nub, h=round(h * 0.36), color=b.c("text2"), radius=max(1, nub // 2))  # fmt: skip
    inset = line * 2.2
    rules = [{"above": 20, "color": b.c("warn")}, {"above": 40, "color": color}]
    b.add(
        "bar", "charge", x=round(x + inset), y=round(y + inset), w=round(body - 2 * inset),
        h=round(h - 2 * inset), sensor="battery.load", min=0, max=100, color=b.c("crit"),
        color2=None, background=None, radius=round(h * 0.08), color_rules=rules,
        glow=round(0.3 * b.glow, 2), glow_radius=max(2, round(h * 0.1)),
    )  # fmt: skip


def _battery_state(b: _Build, x: float, cap: float, width: float, align: str = "left") -> None:
    b.value("state", "battery.state", "{value}", x, cap, b.ts * 1.1, color=b.c("text2"),
            align=align, width=width, font="text_font", tabular=False, fallback="",
            hide_if_missing=True)  # fmt: skip
    b.value("left", "battery.left", "{value:duration}", x, cap + b.ts * 2.0, b.ts * 1.1,
            color=b.c("text3"), align=align, width=width, font="text_font",
            hide_if_missing=True)  # fmt: skip


@dataclass(frozen=True)
class Kind:
    name: str
    build: Callable[[_Build, Source], None]
    sources: tuple[str, ...] = ()  # which "source" values it takes (empty: none)
    span: tuple[int, int] = (1, 1)  # the size it starts with


def kinds() -> dict[str, Kind]:
    readings = ("cpu", "gpu", "mem", "disk", "net", "sensor")
    return {
        "clock": Kind(t("Time"), build_clock, span=(2, 1)),
        "date": Kind(t("Calendar"), build_date),
        "weather": Kind(t("Weather"), build_weather, span=(2, 1)),
        "ring": Kind(t("Ring"), build_ring, ("cpu", "gpu", "mem", "disk")),
        "stat": Kind(t("Big number"), build_stat, readings),
        "graph": Kind(t("History"), build_graph, readings, span=(2, 1)),
        "bars": Kind(t("Bars"), build_bars, span=(1, 2)),
        "network": Kind(t("Network"), build_network, span=(2, 1)),
        "system": Kind(t("System"), build_system),
        "text": Kind(t("Title"), build_text, span=(2, 1)),
        "temps": Kind(t("Temperatures"), build_temps, span=(1, 2)),
        "cores": Kind(t("CPU cores"), build_cores, span=(2, 1)),
        "drives": Kind(t("Drives"), build_drives, span=(2, 1)),
        "processes": Kind(t("Processes"), build_processes, span=(1, 2)),
        "netinfo": Kind(t("Network details"), build_netinfo, span=(2, 1)),
        "battery": Kind(t("Battery"), build_battery),
        "values": Kind(t("Dashboard"), build_values, span=(2, 1)),
        "sun": Kind(t("Sun & moon"), build_sun, span=(2, 1)),
        "analog": Kind(t("Analog clock"), build_analog),
        "world": Kind(t("World clock"), build_world, span=(2, 1)),
        "countdown": Kind(t("Countdown"), build_countdown, span=(2, 1)),
        "image": Kind(t("Picture"), build_image, span=(2, 2)),
        "music": Kind(t("Music"), build_music, span=(2, 1)),
        "agenda": Kind(t("Agenda"), build_agenda, span=(2, 2)),
        "game": Kind(t("Game FPS"), build_game, span=(2, 1)),
    }


MODULE_KINDS = (
    "clock", "date", "weather", "ring", "stat", "graph", "bars", "network", "system", "text",
    "temps", "cores", "drives", "processes", "netinfo", "battery", "values",
    "sun", "analog", "world", "countdown", "image", "music", "agenda", "game",
)  # fmt: skip
MODULE_SOURCES = ("cpu", "gpu", "mem", "disk", "net", "sensor")
MODULE_FALLBACKS = ("auto", "none", "cpu", "gpu", "mem", "disk")


# -- expanding a theme -------------------------------------------------------


def colors_of(palette: dict[str, str]) -> dict[str, str]:
    """The palette roles, from the theme where it has them, else from the default look."""
    default = LOOKS[DEFAULT_LOOK]["palette"]
    return {role: _hex(palette.get(role, default[role])) for role in ROLES}


def _fallback(module: dict[str, Any]) -> str | None:
    chosen = module["fallback"]
    if chosen == "auto":
        return "disk" if module["source"] == "gpu" else None
    return None if chosen in ("none", module["source"]) else chosen


def build_module(
    module: dict[str, Any], box: list[int], colors: dict[str, str], style: dict[str, Any]
) -> list[dict[str, Any]]:
    """The plain widgets of one module in ``box`` (raw, not yet checked)."""
    kind = kinds()[module["module"]]
    sources = _sources()
    source = module["source"] if kind.sources else "cpu"
    if source not in kind.sources and kind.sources:
        source = kind.sources[0]
    src = sources.get(source, sources["cpu"])
    b = _Build(module, box, colors, style)
    fallback = _fallback(module) if kind.sources and source in sources else None
    if fallback is None:
        if kind.sources:  # without its readings the module stays empty
            b.needs = b.module["sensor"] if source == "sensor" else src.load
        kind.build(b, src)
    else:  # e.g. no GPU readings: the disk instead
        b.needs = src.load
        kind.build(b, src)
        first = len(b.out)
        b.needs = "!" + src.load
        kind.build(b, sources[fallback])
        for widget in b.out[first:]:
            widget["id"] = "alt-" + widget["id"]
    return b.out


def expand(theme: Any) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    """The widgets to draw, with every module replaced by its parts.

    Returns them and the box of every module (its cells), by module id. Parts
    have the id ``"<module id>/<part>"`` and carry ``"_module"``: the
    module's id, so the renderer can follow the module's visibility.
    """
    from libre_panel.theme.model import ThemeError, normalize_widget

    modules = [w for w in theme.widgets if w["type"] == "module"]
    if not modules and not theme.grid:
        return list(theme.widgets), {}
    grid = make_grid(theme.width, theme.height, theme.grid, theme.model)
    colors = colors_of(theme.palette)
    style = {**STYLE_DEFAULTS, **(theme.style or {})}
    widgets: list[dict[str, Any]] = []
    boxes: dict[str, list[int]] = {}
    if style["backdrop"] == "gradient":
        widgets.append(
            normalize_widget(
                {"type": "rect", "id": "\0backdrop", "x": 0, "y": 0, "w": theme.width,
                 "h": theme.height, "color": colors["bg"], "color2": colors["bg2"]},
                0,
            )[0]
        )  # fmt: skip
    for widget in theme.widgets:
        if widget["type"] != "module":
            widgets.append(widget)
            continue
        col = min(max(0, widget["col"]), grid.columns - 1)
        row = min(max(0, widget["row"]), grid.rows - 1)
        cols = min(max(1, widget["cols"]), grid.columns - col)
        rows = min(max(1, widget["rows"]), grid.rows - row)
        placed = {**widget, "col": col, "row": row, "cols": cols, "rows": rows}
        color = placed["color"] or ""
        if color.startswith("@"):  # a palette entry
            color = theme.palette.get(color[1:], "")
        placed["color"] = _hex(color) if color else None
        box = grid.box(col, row, cols, rows)
        boxes[widget["id"]] = box
        for i, raw in enumerate(build_module(placed, box, colors, style)):
            raw["id"] = f"{widget['id']}/{raw['id']}"
            try:
                part, _warnings = normalize_widget(raw, i)
            except ThemeError as exc:  # a bug in a module must not break the theme
                theme.warnings.append(f"module {widget['id']!r}: {exc}")
                continue
            part["_module"] = widget["id"]
            widgets.append(part)
    return widgets, boxes


def module_info() -> dict[str, Any]:
    """What the editor needs: the kinds, their sources and sizes, the looks."""
    return {
        "kinds": {
            key: {
                "name": kind.name,
                "sources": list(kind.sources),
                "span": list(kind.span),
                "items": default_items(key),
            }
            for key, kind in kinds().items()
        },
        "looks": {
            key: {
                "name": look["name"],
                "palette": look_theme_parts(key)[0],
                "style": look_theme_parts(key)[1],
            }
            for key, look in LOOKS.items()
        },  # fmt: skip
        "grid": dict(GRID_DEFAULTS),
        "style": dict(STYLE_DEFAULTS),
    }


# -- starting layouts and detaching ------------------------------------------


def _m(kind: str, col: int, row: int, cols: int = 1, rows: int = 1, **options: Any) -> dict:
    return {"module": kind, "col": col, "row": row, "cols": cols, "rows": rows, **options}


_TEMPLATES: dict[tuple[int, int], list[tuple[str, list[dict[str, Any]]]]] = {
    (8, 2): [
        ("overview", [_m("clock", 0, 0, 3), _m("network", 0, 1, 3), _m("weather", 3, 0, 2, 2),
                      _m("ring", 5, 0, 3, source="cpu"), _m("ring", 5, 1, 3, source="gpu")]),
        ("performance", [_m("stat", 0, 0, 2, 2, source="cpu"), _m("ring", 2, 0, 2, source="gpu"),
                         _m("ring", 2, 1, 2, source="mem"), _m("bars", 4, 0, 1, 2),
                         _m("clock", 5, 0, 3), _m("graph", 5, 1, 3, source="net")]),
        ("calm", [_m("clock", 0, 0, 4, 2), _m("weather", 4, 0, 4), _m("date", 4, 1, 2),
                  _m("system", 6, 1, 2)]),
    ],
    (3, 2): [
        ("overview", [_m("clock", 0, 0, 2), _m("weather", 2, 0), _m("ring", 0, 1, source="cpu"),
                      _m("ring", 1, 1, source="mem"), _m("network", 2, 1)]),
        ("performance", [_m("ring", 0, 0, 1, 2, source="cpu"), _m("stat", 1, 0, 2, source="gpu"),
                         _m("bars", 1, 1), _m("date", 2, 1)]),
        ("calm", [_m("clock", 0, 0, 3), _m("weather", 0, 1, 2), _m("date", 2, 1)]),
    ],
    (2, 2): [
        ("overview", [_m("clock", 0, 0, 2), _m("ring", 0, 1, source="cpu"),
                      _m("ring", 1, 1, source="mem")]),
        ("performance", [_m("ring", 0, 0, source="cpu"), _m("ring", 1, 0, source="gpu"),
                         _m("ring", 0, 1, source="mem"), _m("ring", 1, 1, source="disk")]),
        ("calm", [_m("clock", 0, 0, 2), _m("weather", 0, 1, 2)]),
    ],
    (2, 1): [
        ("overview", [_m("stat", 0, 0, source="cpu"), _m("stat", 1, 0, source="mem")]),
        ("performance", [_m("ring", 0, 0, source="cpu"), _m("ring", 1, 0, source="gpu")]),
        ("calm", [_m("clock", 0, 0, 2)]),
    ],
    (2, 3): [
        ("overview", [_m("clock", 0, 0, 2), _m("stat", 0, 1, source="cpu"),
                      _m("stat", 1, 1, source="mem"), _m("graph", 0, 2, 2, source="cpu")]),
        ("performance", [_m("ring", 0, 0, source="cpu"), _m("ring", 1, 0, source="gpu"),
                         _m("ring", 0, 1, source="mem"), _m("ring", 1, 1, source="disk"),
                         _m("network", 0, 2, 2)]),
        ("calm", [_m("clock", 0, 0, 2), _m("weather", 0, 1, 2), _m("date", 0, 2, 2)]),
    ],
}  # fmt: skip

# Wishes for other grids: (kind, options, cols, rows), placed where they fit.
_WISHES: dict[str, list[tuple[str, dict[str, Any], int, int]]] = {
    "overview": [("clock", {}, 3, 1), ("weather", {}, 2, 2), ("ring", {"source": "cpu"}, 3, 1),
                 ("ring", {"source": "gpu"}, 3, 1), ("network", {}, 3, 1),
                 ("ring", {"source": "mem"}, 1, 1), ("bars", {}, 1, 1)],
    "performance": [("stat", {"source": "cpu"}, 2, 2), ("ring", {"source": "gpu"}, 2, 1),
                    ("ring", {"source": "mem"}, 2, 1), ("bars", {}, 1, 2),
                    ("clock", {}, 3, 1), ("graph", {"source": "net"}, 3, 1),
                    ("ring", {"source": "disk"}, 1, 1)],
    "calm": [("clock", {}, 4, 2), ("weather", {}, 4, 1), ("date", {}, 2, 1),
             ("system", {}, 2, 1)],
}  # fmt: skip


def _pack(columns: int, rows: int, wishes: list[tuple[str, dict[str, Any], int, int]]) -> list:
    """Place each wish at the first free spot, smaller if it must be."""
    free = [[True] * columns for _ in range(rows)]
    placed = []

    def fits(col: int, row: int, cols: int, rows_: int) -> bool:
        return all(
            free[r][c] for r in range(row, row + rows_) for c in range(col, col + cols)
        )  # fmt: skip

    for kind, options, want_cols, want_rows in wishes:
        spot = None
        sizes = sorted(
            {(min(c, columns), min(r, rows)) for c in range(want_cols, 0, -1)
             for r in range(want_rows, 0, -1)},
            key=lambda s: -s[0] * s[1],
        )  # fmt: skip
        for cols, rows_ in sizes:
            for row in range(rows - rows_ + 1):
                for col in range(columns - cols + 1):
                    if fits(col, row, cols, rows_):
                        spot = (col, row, cols, rows_)
                        break
                if spot:
                    break
            if spot:
                break
        if spot is None:
            continue
        col, row, cols, rows_ = spot
        for r in range(row, row + rows_):
            for c in range(col, col + cols):
                free[r][c] = False
        placed.append(_m(kind, col, row, cols, rows_, **options))
    return placed


def templates(columns: int, rows: int) -> list[dict[str, Any]]:
    """Starting layouts for a grid of ``columns`` x ``rows`` cells."""
    names = {"overview": t("Overview"), "performance": t("Performance"), "calm": t("Calm")}
    chosen = _TEMPLATES.get((columns, rows))
    if chosen is None:
        chosen = [(key, _pack(columns, rows, wishes)) for key, wishes in _WISHES.items()]
    return [
        {"id": key, "name": names[key], "modules": [dict(m) for m in modules]}
        for key, modules in chosen
    ]


def detach(theme: Any, module_id: str) -> list[dict[str, Any]]:
    """The parts of one module as plain widgets, to edit them one by one."""
    widgets, _boxes = expand(theme)
    parts = []
    for widget in widgets:
        if widget.get("_module") != module_id:
            continue
        part = {k: v for k, v in widget.items() if k != "_module"}
        part["id"] = part["id"].replace("/", "-")
        parts.append(part)
    return parts

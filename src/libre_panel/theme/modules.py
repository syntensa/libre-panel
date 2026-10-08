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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from libre_panel.devices.models import find_model
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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def mix(a: str, b: str, amount: float) -> str:
    """``a`` moved ``amount`` (0-1) of the way to ``b``."""
    ca, cb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(ca, cb, strict=True))


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
            module["card"] == "auto" and module["module"] not in ("clock", "text")
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
    value_size = min(row * 0.56, b.u * 0.17)
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
    size = min(b.ih / (1 + date_ratio * 2.2), b.iw / (CLOCK_WIDTH + (0.62 if seconds else 0)))
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
        sx = x + CLOCK_WIDTH * size + size * 0.08
        b.clock("seconds", "%S", sx, top, size * 0.36, color=b.c("accent"),
                glow=round(0.7 * b.glow, 2), glow_radius=max(2, round(size / 16)))  # fmt: skip
    fmt = "%A · %d %B" if b.iw > size * 4.2 else "%a %d %b"
    b.clock("date", fmt, x, top + CAP * size + date_size * 1.1, date_size, color=b.c("text2"),
            align=align, width=b.iw, font="text_font", tabular=False)  # fmt: skip


def build_date(b: _Build, _src: Source) -> None:
    """A calendar sheet: the day, the weekday, the month."""
    b.card()
    _sheet(b, b.ix, b.iy, b.iw, b.ih)


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
    """The weather of the configured place; a calendar sheet while there is none."""
    b.card()
    start = len(b.out)
    _sheet(b, b.ix, b.iy, b.iw, b.ih)
    for widget in b.out[start:]:
        widget["needs"] = "!weather.temperature"
    start = len(b.out)
    a = b.aspect
    icon_color = b.tint("text")
    desc = b.ts * 1.25
    if a <= 0.72:  # tall: icon, temperature, sky, then the details as rows
        size = min(b.iw * 0.42, b.ih * 0.18)
        b.icon("icon", "weather", b.ix, b.iy, size, icon_color)
        temp_top = b.iy + size * 1.25
        b.add("weather", "temp", field="temperature", format="{value:.0f}°",
              **_temp_fields(b, b.ix, temp_top, size * 1.1, b.iw))  # fmt: skip
        desc_top = temp_top + CAP * size * 1.1 + b.ts * 1.1
        _weather_desc(b, desc_top, desc)
        room = b.iy + b.ih - (desc_top + CAP * desc + b.ts * 1.4)
        rows = min(3, int(room // (b.ts * 2.4)))
        for i, (field, icon, fmt) in enumerate(_WEATHER_DETAILS[:rows]):
            y = b.iy + b.ih - (rows - i) * b.ts * 2.4 + b.ts * 0.6
            b.icon(f"{field}-icon", icon, b.ix, y - b.ts * 0.25, b.ts * 1.3, b.c("text3"))
            b.add("weather", field, field=field, format=fmt,
                  **b._text_fields(b.ts * 1.15, y, "text_font", x=round(b.ix + b.ts * 1.8),
                                   color=b.c("text2"), max_width=round(b.iw - b.ts * 1.8),
                                   tabular=True))  # fmt: skip
    else:
        details = (b.large or a >= 2.3) and b.ih > b.ts * 7
        main_h = b.ih * (0.62 if details else 1.0)
        if a >= 1.45 or b.large:
            size = min(main_h * 0.62, b.iw * 0.22)
            b.icon("icon", "weather", b.ix, b.iy + (main_h * 0.7 - size) / 2, size, icon_color)
            b.add("weather", "temp", field="temperature", format="{value:.0f}°",
                  **_temp_fields(b, b.ix + size * 1.2, b.iy + (main_h * 0.7 - CAP * size) / 2,
                                 size, b.iw - size * 1.2))  # fmt: skip
            desc_top = b.iy + main_h * 0.7 + b.ts * 0.2
        else:
            size = min(b.ih * 0.34, b.iw * 0.36)
            b.icon("icon", "weather", b.ix, b.iy, size, icon_color)
            b.add("weather", "temp", field="temperature", format="{value:.0f}°",
                  **_temp_fields(b, b.ix + b.iw, b.iy + size * 0.12, size, b.iw * 0.6,
                                 "right"))  # fmt: skip
            desc_top = b.iy + size + b.ts * 1.2
        _weather_desc(b, desc_top, desc)
        if details:
            y = b.iy + b.ih - CAP * b.ts * 1.1
            b.add("rect", "rule", x=round(b.ix), y=round(y - b.ts * 1.6), w=round(b.iw), h=1,
                  color=b.c("line"))  # fmt: skip
            count = 3 if b.iw / 3 >= b.ts * 6.5 else 2
            step = b.iw / count
            for i, (field, icon, fmt) in enumerate(_WEATHER_DETAILS[:count]):
                x = b.ix + i * step
                b.icon(f"{field}-icon", icon, x, y - b.ts * 0.25, b.ts * 1.3, b.c("text3"))
                b.add("weather", field, field=field, format=fmt,
                      **b._text_fields(b.ts * 1.1, y, "text_font", x=round(x + b.ts * 1.7),
                                       color=b.c("text2"), max_width=round(step - b.ts * 2),
                                       tabular=True))  # fmt: skip
    for widget in b.out[start:]:
        widget["needs"] = "weather.temperature"


_WEATHER_DETAILS = (
    ("apparent_temperature", "temperature", "{value:.0f}°"),
    ("humidity", "humidity", "{value:.0f}%"),
    ("wind_speed", "wind", "{value:.0f} km/h"),
)


def _weather_desc(b: _Build, top: float, size: float) -> None:
    b.add("weather", "desc", field="description", format="{value}", fallback="",
          **b._text_fields(size, top, "text_font", x=round(b.ix), color=b.c("text2"),
                           max_width=round(b.iw), fit="ellipsis", tabular=False))  # fmt: skip


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
    }


MODULE_KINDS = (
    "clock", "date", "weather", "ring", "stat", "graph", "bars", "network", "system", "text",
)  # fmt: skip
MODULE_SOURCES = ("cpu", "gpu", "mem", "disk", "net", "sensor")
MODULE_FALLBACKS = ("auto", "none", "cpu", "gpu", "mem", "disk")


# -- expanding a theme -------------------------------------------------------


def colors_of(palette: dict[str, str]) -> dict[str, str]:
    """The palette roles, from the theme where it has them, else from the default look."""
    default = LOOKS[DEFAULT_LOOK]["palette"]
    return {role: palette.get(role, default[role])[:7] for role in ROLES}


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
        if (placed["color"] or "").startswith("@"):  # a palette entry
            placed["color"] = theme.palette.get(placed["color"][1:], "")[:7] or None
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
            key: {"name": kind.name, "sources": list(kind.sources), "span": list(kind.span)}
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

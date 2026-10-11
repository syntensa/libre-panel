"""A download as the Studio's inset shows it: what loads (the phase, the source and the name),
a progress under it, the speed and the time left, the loaded amount against the whole, and the
speed of the last minutes as a graph.

The state is the one the ``downloads`` service publishes (``snapshot.state("downloads")``, see
docs/PLUGINS.md); nothing here looks at a file or at the network. The widget is laid out in the
numbers of the inset it comes from (596 x 130 px on SPUR II's 1920 x 480 bar) and scaled with
its box: a box that is not as wide leaves the graph out, and a name that does not fit is cut.

The progress is light in its groove with the ``lit`` finish, a flat bar without it. A download of
unknown size (a browser's) has no share to show: a stretch of light runs to and fro in the
groove instead. Everything but the progress is drawn once per state (the service publishes one
a second) and kept; the running stretch is the only thing that costs a frame.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from libre_panel.i18n import t
from libre_panel.render import light
from libre_panel.render.finish import Finish, register
from libre_panel.render.formatting import rate_parts
from libre_panel.render.renderer import Piece

REF_W, REF_H = 596, 130  # the inset's size in its own pixels
MARGIN, MARGIN_RIGHT = 13, 16
VALUE_X = 240  # where the numbers of the readings end (their units follow)
GRAPH_LEAST = 40  # a graph narrower than this (in reference px) is left out
GRAPH_FILL = 105  # the share of the line's colour under it, at its top (of 255)
HISTORY = 180  # the speeds a state carries: the graph's width, if there is room
SPREAD = 1.45  # how much further apart than their size lines may stand in a taller box
STACK_ASPECT = 2.4  # a box less wide than this many times its height stacks the card
BAR_H = 6  # the progress's thickness
# where things are, in reference px: the heading's baseline, the name's (stacked: a line of its
# own), the progress's top, the readings' baselines, the amount's (stacked: a line of its own),
# the graph's top and bottom edges (beside the readings, from x 392; stacked: over the width)
WIDE = {"size": (596, 130), "head": 30, "name": None, "bar": 44, "rows": (81, 125),
        "amount": None, "graph": (60, 104)}  # fmt: skip
STACKED = {"size": (330, 254), "head": 30, "name": 58, "bar": 68, "rows": (106, 150),
           "amount": 182, "graph": (196, 246)}  # fmt: skip
GRAPH_X = 392
MB = 1024 * 1024
RUN = 2.4  # seconds a stretch of light takes to and fro, for a download of unknown size
STRETCH = 0.25  # its share of the groove
PHASES = ("downloading", "preparing", "paused", "installing", "done")


def heading(phase: str) -> str:
    """The word at the head of a card for a phase (the service's, see docs/PLUGINS.md)."""
    return {
        "paused": t("Paused"), "installing": t("Installing"), "done": t("Done"),
    }.get(phase, t("Download"))  # fmt: skip


# -- the numbers, in words ---------------------------------------------------------------------


def amount(size: float) -> tuple[str, str]:
    """A size as a number and its unit: GB from one GB on, else MB."""
    size = max(0.0, float(size))
    if size >= 1024 * MB:
        return f"{size / (1024 * MB):.1f}", "GB"
    return f"{size / MB:.0f}", "MB"


def time_left(seconds: float) -> tuple[str, str]:
    """A span as a number and its unit: seconds below a minute, then minutes, then hours."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}", "s"
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}", "min"
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}", "h"


def of_total(loaded: float, total: float) -> str:
    """``"9.3 / 24.1 GB"``: both in the unit of the whole (``"994 / 1.9 GB"`` would read as
    nearly done)."""
    number, unit = amount(total)
    divisor, digits = (1024 * MB, 1) if unit == "GB" else (MB, 0)
    return f"{loaded / divisor:.{digits}f} / {number} {unit}"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


class Facts(NamedTuple):
    """What the card shows of a state, as text: it is also what a picture is kept by."""

    phase: str
    source: str
    name: str
    share: float | None  # how much is loaded, 0 to 1; None: the size is not known
    rows: tuple[tuple[str, str, str], ...]  # (label, number, unit), top to bottom
    amount: str  # "9.3 / 24.1 GB", or ""
    history: tuple[float, ...]  # bytes a second, the oldest first


def read(state: dict[str, Any]) -> Facts:
    """The card's texts from a state; a field that is missing or no number is left out."""
    phase = state.get("phase")
    phase = phase if phase in PHASES else "downloading"
    loaded = _number(state.get("bytes")) or 0.0
    total = _number(state.get("total"))
    total = total if total and total > 0 else None
    rate = _number(state.get("rate"))
    left = _number(state.get("remaining_s"))
    elapsed = _number(state.get("elapsed_s"))
    done = phase == "done"
    share = 1.0 if done else (None if total is None else max(0.0, min(1.0, loaded / total)))
    if done:
        rows = ((t("Total"), *amount(total or loaded)), (t("Duration"), *time_left(elapsed or 0)))
    elif phase == "installing":
        rows = ((t("Loaded"), *amount(total or loaded)), (t("Duration"), *time_left(elapsed or 0)))
    else:
        speed = ("--", "") if rate is None else rate_parts(max(0.0, rate))
        second = (t("Remaining"), *time_left(left)) if left is not None else (
            t("Loaded"), *amount(loaded))  # fmt: skip
        rows = ((t("Rate"), *speed), second)
    text = ""
    if total and phase not in ("done", "installing"):
        text = ("≈ " if state.get("estimated") is True else "") + of_total(loaded, total)
    history = state.get("history")
    series = tuple(
        max(0.0, v) for v in (_number(x) for x in history) if v is not None
    ) if isinstance(history, list) else ()  # fmt: skip
    name = str(state.get("name") or "")
    more = _number(state.get("more"))
    if more and more >= 1:
        name = f"{name}  +{int(more)}"
    return Facts(phase, str(state.get("source") or "").upper(), name, share, rows, text, series)


# -- the geometry ------------------------------------------------------------------------------


class Layout(NamedTuple):
    g: float  # the size of things: the reference's, times this
    gy: float  # the distance of lines
    top: float  # where the reference's top edge lies
    left: float
    right: float
    graph: tuple[float, float, float, float] | None  # x0, y0, x1, y1
    stacked: bool  # a box that is nearly square or tall: the graph under the readings
    ref: dict[str, Any]  # where things are in the reference's pixels (WIDE or STACKED)

    def y(self, ref_y: float) -> float:
        """A line of the reference, in the box."""
        return self.top + ref_y * self.gy


def layout(w: float, h: float) -> Layout:
    """Where things go in a box of ``w`` x ``h``: the reference's numbers, as large as the
    box allows. A taller box spreads its lines a little and keeps the card in the middle; one
    that is nearly square or tall puts the graph under the readings, over the whole width."""
    stacked = w < STACK_ASPECT * h
    ref = STACKED if stacked else WIDE
    ref_w, ref_h = ref["size"]
    g = max(0.15, min(w / ref_w, h / ref_h))
    gy = min(h / ref_h, g * SPREAD)
    top = (h - ref_h * gy) / 2
    left, right = MARGIN * g, w - MARGIN_RIGHT * g
    graph = None
    y0, y1 = (top + v * gy for v in ref["graph"])
    if stacked:
        if y1 - y0 >= 10:
            graph = (left, y0, right, y1)
    elif right - GRAPH_X * g >= GRAPH_LEAST * g:
        graph = (GRAPH_X * g, y0, right, y1)
    return Layout(g, gy, top, left, right, graph, stacked, ref)


def rail(lay: Layout) -> tuple[float, float, float, float]:
    """The progress's groove: x0, y0, x1, y1."""
    y = lay.y(lay.ref["bar"])
    return lay.left, y, lay.right, y + max(3.0, BAR_H * lay.g)


# -- drawing -----------------------------------------------------------------------------------


def _tracked(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font: Any, fill: Any,
             tracking: float, anchor: str = "l") -> float:  # fmt: skip
    """Letter-spaced text with its baseline at ``y``, from ``x`` (``anchor`` ``"r"``: ending
    there); returns its width."""
    width = light.tracked_width(font, text, tracking)
    if anchor == "r":
        x -= width
    if tracking:
        light.draw_tracked(draw, (x, y), text, font, fill, tracking)
    else:
        draw.text((x, y), text, font=font, fill=fill, anchor="ls")
    return width


def _cut(text: str, font: Any, room: float) -> str:
    """``text`` cut with an ellipsis to fit ``room`` px."""
    if font.getlength(text) <= room:
        return text
    while len(text) > 1 and font.getlength(text.rstrip() + "…") > room:
        text = text[:-1]
    return text.rstrip() + "…"


def _graph(values: tuple[float, ...], size: tuple[int, int], g: float, color: tuple[int, ...],
           ) -> Image.Image:  # fmt: skip
    """The speed as a curve that grows from the bottom edge, on a scale of its own highest
    value, with a halo and a fill that fades out below the line (RGBA, ``size``)."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if len(values) < 2 or w < 4 or h < 4:
        return layer
    step = max(1.0, w / (HISTORY - 1))  # as many as a state carries fill the width
    shown = values[-max(2, int(w / step) + 1) :]
    peak = max(MB, max(shown) * 1.1)
    smooth = np.convolve(np.pad(np.asarray(shown, np.float64), 1, mode="edge"),
                         [0.25, 0.5, 0.25], mode="valid")  # fmt: skip
    k = 3  # drawn at k times the size, then reduced
    margin = 2 * k
    xs = w - 1 - (len(shown) - 1 - np.arange(len(shown))) * step
    ys = (h - 1) - np.clip(smooth / peak, 0, 1) * (h - 1)
    points = [(x * k + margin, y * k + margin) for x, y in zip(xs, ys, strict=True)]
    big = (w * k + 2 * margin, h * k + 2 * margin)
    line = Image.new("L", big, 0)
    ImageDraw.Draw(line).line(points, fill=255, width=max(2, round(2 * g * k)), joint="curve")
    area = Image.new("L", big, 0)
    ImageDraw.Draw(area).polygon([*points, (points[-1][0], big[1]), (points[0][0], big[1])],
                                 fill=255)  # fmt: skip
    line = line.resize(size, Image.Resampling.LANCZOS, box=(margin, margin, margin + w * k,
                                                             margin + h * k))  # fmt: skip
    area = area.resize(size, Image.Resampling.LANCZOS, box=(margin, margin, margin + w * k,
                                                             margin + h * k))  # fmt: skip
    fade = np.linspace(GRAPH_FILL, 0, h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    fill = ImageChops.multiply(area, Image.fromarray(fade.astype(np.uint8), "L"))
    halo = line.filter(ImageFilter.GaussianBlur(max(1.0, 2.2 * g))).point(lambda v: int(v * 0.55))
    body = color[:3]
    for mask in (fill, halo, line):
        layer.alpha_composite(Image.merge("RGBA", (*(Image.new("L", size, c) for c in body), mask)))
    return layer


def _bar_flat(width: int, thick: int, share: float | None, color: tuple[int, ...],
              track: tuple[int, ...] | None, run: float) -> Image.Image:  # fmt: skip
    """A flat progress: a pill in its groove, filled to ``share``, or (no share) a stretch of it
    at ``run`` (0 to 1) along the groove."""
    u = 3
    layer = Image.new("RGBA", (width * u, thick * u), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    radius = thick * u / 2
    if track:
        draw.rounded_rectangle([0, 0, width * u - 1, thick * u - 1], radius=radius, fill=track)
    if share is None:
        span = max(thick, round(width * STRETCH))
        x0 = round((width - span) * run)
        a, b = x0 * u, (x0 + span) * u - 1
    else:
        a, b = 0, max(round(share * width * u) - 1, -1)
    if b - a >= 1:
        draw.rounded_rectangle([a, 0, b, thick * u - 1], radius=radius, fill=color)
    return layer.resize((width, thick), Image.Resampling.LANCZOS)


def _run(now: float) -> float:
    """Where the stretch of light is, 0 to 1, going to and fro (slow at the ends)."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * now / RUN)


def _still(phase: str, share: float | None) -> bool:
    """Whether the progress stands: it shows a share, or it is paused."""
    return share is not None or phase == "paused"


def accent(renderer: Any, widget: dict[str, Any], phase: str) -> tuple[int, ...]:
    """The progress's colour: the widget's, the ``done`` colour for a finished download, and a
    muted one while it waits."""
    if phase == "done":
        return renderer.color(widget["done"])
    if phase == "paused":
        return renderer.color(widget["muted"])
    return renderer.color(widget["color2"])


class _Base(NamedTuple):
    """Everything that stays while a state does: texts and graph, on a canvas with room around
    the widget for the progress's glow."""

    image: Image.Image
    pad: int


def _base(renderer: Any, widget: dict[str, Any], r: Facts, pad: int) -> _Base:
    w, h = widget["w"], widget["h"]
    lay = layout(w, h)
    g = lay.g
    image = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ox = oy = pad
    color, muted = renderer.color(widget["color"]), renderer.color(widget["muted"])
    mid = light.mix(color[:3], muted[:3], 0.45)
    tone = accent(renderer, widget, r.phase)

    def bold(size: float) -> Any:
        return renderer.font(widget["font"], max(6, round(size)))

    def plain(size: float) -> Any:
        return renderer.font(widget["label_font"], max(6, round(size)))

    # the heading: the phase, the source and, to the right (stacked: under it), the name
    head, source = bold(26 * g), bold(20 * g)
    base = oy + lay.y(lay.ref["head"])
    word = heading(r.phase).upper()
    x = ox + lay.left + _tracked(draw, ox + lay.left, base, word, head, tone, 3 * g)
    x += 14 * g
    if r.source and ox + lay.right - x > 30 * g:
        x += _tracked(draw, x, base, r.source, source, muted, 2 * g) + 20 * g
    name_font = plain(22 * g)
    if lay.stacked:
        if r.name:
            text = _cut(r.name, name_font, lay.right - lay.left)
            draw.text((ox + lay.left, oy + lay.y(lay.ref["name"])), text, font=name_font,
                      fill=mid, anchor="ls")  # fmt: skip
    elif r.name and ox + lay.right - x > 40 * g:
        text = _cut(r.name, name_font, ox + lay.right - x)
        draw.text((ox + lay.right, base), text, font=name_font, fill=mid, anchor="rs")

    # the two readings: their names, numbers (ending in one column) and units
    label, number, unit = plain(22 * g), bold(34 * g), plain(19 * g)
    for baseline, (name, value, suffix) in zip(lay.ref["rows"], r.rows, strict=True):
        y = oy + lay.y(baseline)
        _tracked(draw, ox + lay.left, y, name.upper(), label, muted, 2 * g)
        draw.text((ox + VALUE_X * g, y), value, font=number, fill=color, anchor="rs")
        if suffix:
            draw.text((ox + VALUE_X * g + 6 * g, y), suffix, font=unit, fill=muted, anchor="ls")
    if r.amount:
        small = plain(20 * g)
        if lay.stacked:
            draw.text((ox + lay.left, oy + lay.y(lay.ref["amount"])), r.amount, font=small,
                      fill=mid, anchor="ls")  # fmt: skip
        else:
            y = oy + lay.y(lay.ref["rows"][1])
            edge = ox + VALUE_X * g + 6 * g + unit.getlength("MB/s") + 14 * g
            if ox + lay.right - small.getlength(r.amount) > edge:  # not into the unit
                draw.text((ox + lay.right, y), r.amount, font=small, fill=mid, anchor="rs")

    # the speed of the last minutes, grown from the bottom edge
    if lay.graph is not None:
        x0, y0, x1, y1 = lay.graph
        gw, gh = max(1, round(x1 - x0)), max(1, round(y1 - y0))
        rule = (*muted[:3], 90)
        y = oy + round(y1)
        draw.line([(ox + round(x0), y), (ox + round(x0) + gw - 1, y)], fill=rule, width=1)
        if r.history:
            image.alpha_composite(
                _graph(r.history, (gw, gh), g, tone), (ox + round(x0), oy + round(y0))
            )
    return _Base(image, pad)


def _pad(renderer: Any) -> int:
    """Room around the widget for the progress's glow."""
    return math.ceil(9 * min(renderer.theme.width, renderer.theme.height) / 480) + 2


def draw(renderer: Any, widget: dict[str, Any], snapshot: Any, now: float,
         finish_name: str = "flat") -> Piece | None:  # fmt: skip
    """The download card of the state ``widget["state"]`` holds. Nothing downloads: the widget's
    ``empty`` text, or nothing."""
    state = snapshot.state(widget["state"])
    if not isinstance(state, dict):
        return _empty(renderer, widget)
    r = read(state)
    pad = _pad(renderer)
    lay = layout(widget["w"], widget["h"])
    x0, y0, x1, y1 = rail(lay)
    length, thick = max(2, round(x1 - x0)), max(2, round(y1 - y0))
    running = not _still(r.phase, r.share)
    if running and renderer.animate:
        renderer.moving = True
    run = _run(now) if running else 0.0
    frac = None if r.share is None else round(r.share * length) / length
    step = round(run * 64)  # the stretch's places: a frame that stays has the same picture
    content = (r, frac, step if frac is None and running else 0, finish_name)

    def build() -> Piece:
        canvas = _bases(renderer, widget, r, pad).image.copy()
        tone = accent(renderer, widget, r.phase)
        track = renderer.color(widget["background"])
        if finish_name != "lit":
            canvas.alpha_composite(_bar_flat(length, thick, frac, tone, track, run),
                                   (pad + round(x0), pad + round(y0)))  # fmt: skip
            return Piece(canvas, widget["x"] - pad, widget["y"] - pad, _box(widget))
        from libre_panel.render import lit

        bar = {
            "x": widget["x"] + round(x0), "y": widget["y"] + round(y0), "w": length, "h": thick,
            "direction": "right", "segments": 0, "segment_gap": 2,
            "background": widget["background"], "glow": widget["glow"],
            "glow_radius": max(2, thick),
        }  # fmt: skip
        pieces = [lit.bar_piece(renderer, bar, 0.0 if frac is None else frac, (tone, None))]
        if frac is None and running:  # the stretch of light, in the groove
            span = max(thick, round(length * STRETCH))
            start = round((length - span) * step / 64)
            pieces.append(lit.bar_piece(
                renderer, {**bar, "x": bar["x"] + start, "w": span, "background": None},
                1.0, (tone, None)))  # fmt: skip
        glow = None
        for piece in pieces:
            canvas.alpha_composite(piece.layer, (piece.x - widget["x"] + pad,
                                                 piece.y - widget["y"] + pad))  # fmt: skip
            glow = piece.light or glow
        return Piece(canvas, widget["x"] - pad, widget["y"] - pad, _box(widget), light=glow)

    # (its glow is the progress's, drawn with it: the generic halo is not wanted)
    return renderer._cached({**widget, "glow": 0.0}, content, build)


def _box(widget: dict[str, Any]) -> list[int]:
    return [widget["x"], widget["y"], widget["w"], widget["h"]]


def _bases(renderer: Any, widget: dict[str, Any], r: Facts, pad: int) -> _Base:
    """The part of the card that stays while the state does, kept for the widget."""
    kept = renderer._downloads.get(widget["id"])
    if kept is not None and kept[0] == r:
        return kept[1]
    base = _base(renderer, widget, r, pad)
    renderer._downloads[widget["id"]] = (r, base)
    return base


def _empty(renderer: Any, widget: dict[str, Any]) -> Piece | None:
    """What stands where no download runs: the widget's ``empty`` text, in the middle."""
    text = widget["empty"]
    if not text:
        return None

    def build() -> Piece:
        w, h = widget["w"], widget["h"]
        lay = layout(w, h)
        font = renderer.font(widget["label_font"], max(6, round(24 * lay.g)))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((w / 2, h / 2), text, font=font,
                                   fill=renderer.color(widget["muted"]), anchor="mm")  # fmt: skip
        return Piece(layer, widget["x"], widget["y"])

    return renderer._cached({**widget, "glow": 0.0}, ("empty", text), build)


class LitDownloads(Finish):
    """The ``lit`` finish of a download card: its progress is light in its groove."""

    name = "lit"
    types = ("downloads",)

    def draw(self, renderer: Any, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        return draw(renderer, widget, snapshot, now, "lit")


register(LitDownloads())

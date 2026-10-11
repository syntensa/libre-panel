"""Cards: the large modules after SPUR II's Studio screen: what plays, the weather and the
next hours, a download, rings with their values, and a slot that shows one card of several.

A card is laid out in the numbers of the card it comes from (706 x 376 px on SPUR II's
1920 x 480 bar), scaled with the module's height, so it looks as that card does at its
size. Where the module is too small or too narrow for that, the older module of its kind
takes over (the weather card becomes the weather module, the playback card the music
module): the cards adapt to any panel, as every module does.

Cards are lit by themselves (their ``finish`` is ``auto``): values are light in their
grooves and the weather symbols are lit bodies, unless the theme says otherwise. Frames are
flat thin lines in their colour, never ridges.
"""

from __future__ import annotations

from typing import Any

from libre_panel.i18n import t
from libre_panel.theme import modules as M
from libre_panel.theme.modules import CAP, _Build


class _Ref:
    """A card in the numbers of the reference card (``ref_w`` x ``ref_h`` px): sizes and
    distances grow with the module's height, or shrink with its width where the card would
    not fit (``least_w``: how wide it needs to be at the reference height). The right edge
    stays at the module's right edge, so a wider module has room for more hours or a longer
    title, not for bigger letters. ``ok`` says whether it is big enough to be this card."""

    def __init__(self, b: _Build, ref_w: float, ref_h: float, least_w: float, floor: float = 0.55,
                 aspect: float = 1.35):  # fmt: skip
        self.b = b
        self.g = min(b.h / ref_h, b.w / least_w)
        # too small, or too narrow for its height (the card would stand in the middle of a
        # box with room left above and below it): the older module of its kind does better
        self.ok = self.g >= floor and b.w >= aspect * b.h
        self.left = b.x
        # a card that is not as tall stands in the middle
        self.top = b.y + (b.h - ref_h * self.g) / 2

    def x(self, v: float) -> float:
        return self.left + v * self.g

    def y(self, v: float) -> float:
        return self.top + v * self.g

    def s(self, v: float, low: int = 7) -> int:
        return max(low, round(v * self.g))

    def right(self, margin: float) -> float:
        return self.b.x + self.b.w - margin * self.g

    def cap(self, baseline: float, size: float) -> float:
        """Where the capitals of a text of ``size`` start, for its baseline at ``baseline``."""
        return self.y(baseline) - CAP * self.s(size)


def _need(widget: dict[str, Any], need: str) -> None:
    """Add a condition to a part (it has others already, from the module)."""
    own = widget.get("needs", "")
    widget["needs"] = f"{need},{own}" if own else need


def _bar(b: _Build, name: str, sensor: str, x: float, y: float, w: float, h: float, color: str,
         high: float = 100, **fields: Any) -> dict[str, Any]:  # fmt: skip
    """A bar of one colour (no gradient: a progress or a rain chance is one colour) in a groove."""
    h = max(2, round(h))
    return b.add(
        "bar", name, x=round(x), y=round(y), w=max(4, round(w)), h=h, sensor=sensor, min=0,
        max=high, color=color, color2=None, background=b.c("track"), radius=h // 2,
        smooth=True, **fields,
    )  # fmt: skip


# -- the weather card -------------------------------------------------------------------------


def build_forecast(b: _Build, src: M.Source) -> None:
    """The weather: a symbol that moves, the temperature with the day's range, the sky, the
    details, the rain of the next two hours and the sun, and under them the next hours, each
    with its symbol, its temperature and its chance of rain as a bar.

    Where there is no weather a calendar sheet stands in; where the module is small it is the
    weather module.
    """
    ref = _Ref(b, 706, 376, 590)
    if not ref.ok:
        M.build_weather(b, src)
        return
    b.card()
    start = len(b.out)
    M._date_layout(b)
    for widget in b.out[start:]:
        widget["needs"] = "!weather.temperature"
    b.needs = "weather.temperature"
    clouds = b.module.get("color") or b.c("text2")
    left, right = ref.x(280), ref.right(26)
    width = min(right - left, ref.s(400))  # the column of text: a wider card has more hours
    b.add("symbol", "symbol", x=round(ref.x(30)), y=round(ref.y(12)), size=ref.s(196),
          sensor="weather.code", color=clouds, moving=b.module["moving"])  # fmt: skip
    b.label("kicker", b.module["title"] or t("Weather"), left, ref.cap(36, 21), ref.s(21),
            width=width)  # fmt: skip

    # the temperature, and beside it today's range: as wide as two digits and the degree sign
    # (a wider number gets smaller to fit, it never runs into them)
    size = ref.s(104)
    at = left - ref.s(4)
    digits = M._width_per_size(b.style["display_font"], "00°") * size
    range_x = at + digits + ref.s(24)
    b.add("weather", "temp", field="temperature", format="{value:.0f}°",
          **M._temp_fields(b, at, ref.cap(126, 104), size, range_x - ref.s(12) - at))  # fmt: skip
    arrow = ref.s(22)
    for name, key, baseline, role, icon in (
        ("high", "weather.day.0.high", 84, "text2", "up"),
        ("low", "weather.day.0.low", 122, "text3", "down"),
    ):
        top = ref.cap(baseline, 26)
        b.icon(f"{name}-icon", icon, range_x, top + CAP * ref.s(26) / 2 - arrow / 2, arrow,
               b.c(role))  # fmt: skip
        _need(b.out[-1], key)
        b.value(name, key, "{value:.0f}°", range_x + arrow + ref.s(6), top, ref.s(26),
                color=b.c(role), width=max(8, left + width - range_x - arrow - ref.s(6)),
                hide_if_missing=True)  # fmt: skip

    M._weather_desc(b, left, ref.cap(164, 30), width, ref.s(30))
    small = ref.s(20)
    details = (("weather.apparent_temperature", t("Feels like {value:.0f}°"), 0, 148),
               ("weather.wind_speed", t("Wind {value:.0f} {unit}"), 148, 144),
               ("weather.humidity", "{value:.0f}%", 292, 70))  # fmt: skip
    squeeze = min(1.0, width / ref.s(362))  # a narrow column draws them closer
    for i, (key, fmt, at, room) in enumerate(details):
        if i == 2 and squeeze < 0.9:  # (the last goes first)
            break
        b.value(f"detail{i}", key, fmt, left + ref.s(at) * squeeze, ref.cap(194, 20), small,
                color=b.c("text3"), width=ref.s(room) * squeeze, font="text_font",
                tabular=False, fallback="", hide_if_missing=True)  # fmt: skip

    # the rain of the next two hours (blue when it starts or stops soon), and the sun
    line = ref.cap(222, 20)
    for name, need, role in (("rain", "weather.rain.minutes", "gpu"),
                             ("dry", "!weather.rain.minutes", "text3")):  # fmt: skip
        b.value(name, "weather.rain.text", "{value}", left, line, small, color=b.c(role),
                width=width * 0.55, font="text_font", tabular=False, fallback="",
                fit="ellipsis", needs=need)  # fmt: skip
    for name, key, text, up in (
        ("sun-set", "sun.set", t("Sun until {value}"), 1),
        ("sun-rise", "sun.next_rise", t("Sun from {value}"), 0),
    ):
        b.value(name, key, text, left + width, line, small, color=b.c("text3"), align="right",
                width=width * 0.42, font="text_font", tabular=False, fallback="",
                fit="ellipsis", needs=f"sun.up={up}")  # fmt: skip

    # the next hours across the card
    step = max(1, min(6, b.module["step"]))
    strip = ref.x(24)
    count = int(M._clamp((right - strip) // ref.s(109), 1, 24 // step))
    cell = (right - strip) / count
    edge = ref.s(64)
    for i in range(count):
        key = f"weather.hour.{step * (i + 1)}"
        cx = strip + cell * (i + 0.5)
        first = len(b.out)
        b.value(f"h{i}-time", f"{key}.time", "{value}", cx, ref.cap(252, 24), ref.s(24),
                color=b.c("text2"), align="center", width=cell * 0.95, font="text_font",
                tabular=False, fallback="")  # fmt: skip
        b.add("symbol", f"h{i}-symbol", x=round(cx - edge / 2), y=round(ref.y(258)), size=edge,
              sensor=f"{key}.code", color=clouds, moving=b.module["moving"],
              offset=round(i * 1.7, 2))  # fmt: skip
        b.value(f"h{i}-temp", f"{key}.temperature", "{value:.0f}°", cx, ref.cap(352, 34),
                ref.s(34), align="center", width=cell * 0.92)  # fmt: skip
        _bar(b, f"h{i}-rain", f"{key}.rain", cx - cell * 0.36, ref.y(358), cell * 0.72, ref.s(9, 3),
             b.c("gpu"))  # fmt: skip
        for widget in b.out[first:]:  # a column only while its hour is known
            _need(widget, f"{key}.code")
    b.needs = ""


# -- the playback card ------------------------------------------------------------------------


def build_playback(b: _Build, src: M.Source) -> None:
    """What plays: the cover, the state, the title and artist, the app with its icon, the
    progress with the time played and the length. Nothing playing is a note and a word."""
    ref = _Ref(b, 706, 376, 560)
    if not ref.ok:
        M.build_music(b, src)
        return
    b.card()
    color = b.tint("accent")
    left, right = ref.x(280), ref.right(26)
    width = right - left
    b.needs = "media.title"
    M._cover(b, ref.x(24), ref.y(24), ref.s(228))

    # the state, as a sign and a word
    label = ref.s(21)
    top = ref.cap(54, 21)
    sign = ref.s(22)
    for name, state, icon, text in (("playing", 1, "play", t("Now playing")),
                                    ("paused", 0, "pause", t("Paused"))):  # fmt: skip
        b.icon(f"{name}-sign", icon, left, top + CAP * label / 2 - sign / 2, sign, color)
        _need(b.out[-1], f"media.playing={state}")
        b.label(name, text, left + ref.s(30), top, label, width=width - ref.s(30))
        _need(b.out[-1], f"media.playing={state}")

    b.value("title", "media.title", "{value}", left, ref.cap(118, 40), ref.s(40), width=width,
            tabular=False, fallback="", fit="ellipsis", hide_if_missing=True)  # fmt: skip
    b.value("artist", "media.artist", "{value}", left, ref.cap(160, 28), ref.s(28),
            color=b.c("text2"), width=width, font="text_font", tabular=False, fallback="",
            fit="ellipsis", hide_if_missing=True)  # fmt: skip

    # the app that plays it: its icon, and its name
    icon = ref.s(30)
    b.add("image", "app-icon", x=round(left), y=round(ref.y(182)), w=icon, h=icon,
          src="@media.app_icon", fit="contain")  # fmt: skip
    b.value("app", "media.source", "{value}", left + ref.s(40), ref.cap(206, 20), ref.s(20),
            color=b.c("text3"), width=width - ref.s(40), font="text_font", tabular=False,
            fallback="", fit="ellipsis", hide_if_missing=True)  # fmt: skip

    # the progress, with the time played and the length
    strip = ref.x(24)
    _bar(b, "progress", "media.progress", strip, ref.y(304), right - strip, ref.s(8, 3), color,
         high=1, hide_if_missing=True)  # fmt: skip
    time = ref.s(22)
    b.value("position", "media.position", "{value:clock}", strip, ref.cap(340, 22), time,
            color=b.c("text3"), width=(right - strip) / 2, font="text_font",
            hide_if_missing=True)  # fmt: skip
    b.value("length", "media.duration", "{value:clock}", right, ref.cap(340, 22), time,
            color=b.c("text3"), align="right", width=(right - strip) / 2, font="text_font",
            hide_if_missing=True)  # fmt: skip

    b.needs = "!media.title"
    side = ref.s(228)
    note = side * 0.3
    b.icon("idle-icon", "music", ref.x(24) + (side - note) / 2, ref.y(24) + side / 2 - note * 0.8,
           note, b.c("text3"))  # fmt: skip
    b.label("idle", t("Nothing playing"), ref.x(24) + side / 2, ref.y(24) + side / 2 + note * 0.45,
            ref.s(21), align="center", width=side)  # fmt: skip
    b.needs = ""


# -- the ring instrument ---------------------------------------------------------------------


def _unit_of(fmt: str) -> tuple[str, str]:
    """A value format split into the number's and the unit's after it: ``"{value:.0f}°C"``
    is (``"{value:.0f}"``, ``"°C"``). A format that makes its own unit (bytes) stays whole."""
    head, brace, tail = fmt.rpartition("}")
    if not brace or "bytes" in fmt:
        return fmt, ""
    return head + brace, tail.strip()


def build_instrument(b: _Build, src: M.Source) -> None:
    """A ring with its value inside, as SPUR II's Studio shows CPU, GPU and RAM: the load as
    light in a milled groove, the number in the middle with a small unit, the name under it
    and the readings of the part (temperature, power, clock ...) in rows below, as many as
    fit. A wide module puts the name and the rows beside the ring."""
    color = b.tint(src.role)
    b.card()
    rows = list(src.details)
    wide = b.aspect >= 1.3
    # sizes follow the ring's diameter (164 px with its groove 10 px thick in the Studio)
    if wide:
        d = min(b.ih * 0.92, b.iw * 0.46)
        count = min(len(rows), int((b.ih - d * 0.2) // (d * 0.268)))
    else:
        d = b.iw / 1.12
        count = min(len(rows), 3)
        while True:  # as many rows as fit with a ring that is not much smaller
            need = 1.38 + (0.287 + 0.268 * (count - 1) if count else 0)
            if count == 0 or b.ih / need >= 0.7 * d:
                break
            count -= 1
        d = min(d, b.ih / (1.38 + (0.287 + 0.268 * (count - 1) if count else 0)))
    d = max(24, round(d))
    u = d / 164
    pad = 0.05 * d
    if wide:
        cx, top = b.ix + pad + d / 2, b.iy + (b.ih - d) / 2
    else:
        total = d * (1.33 + (0.287 + 0.268 * (count - 1) if count else 0)) + pad
        cx, top = b.ix + b.iw / 2, b.iy + (b.ih - total) / 2 + pad

    b.add("gauge", "ring", x=round(cx - d / 2), y=round(top), w=d, h=d, sensor=src.load,
          min=0 if src.low is None else src.low, max=100 if src.high is None else src.high,
          start_angle=-90, end_angle=270, thickness=max(3, round(10 * u)), color=color,
          color2=None, background=b.c("track"), cap="flat", smooth=True)  # fmt: skip

    # the number in the middle with its unit: centred as a pair, for two digits
    size, small = max(8, round(50 * u)), max(7, round(22 * u))
    digits = M._width_per_size(b.style["display_font"], "00") * size
    unit = _unit_of(src.fmt)[1]
    sign = M._width_per_size(b.style["text_font"], unit or "%") * small
    gap = 4 * u
    pair = digits + gap + sign
    baseline = top + d / 2 + 0.11 * d
    b.value("value", src.load, _unit_of(src.fmt)[0], cx + pair / 2 - sign - gap,
            baseline - CAP * size, size, align="right", width=digits * 1.7)  # fmt: skip
    if unit:
        b.add("text", "unit", text=unit, x=round(cx + pair / 2 - sign),
              **b._text_fields(small, baseline - CAP * small, "text_font", color=b.c("text3"),
                               tabular=False))  # fmt: skip

    # its name, and the readings
    title = b.module["title"] or src.label
    label, value, note = max(7, round(20 * u)), max(8, round(30 * u)), max(7, round(17 * u))
    shown = rows[:count]
    if wide:
        x0 = b.ix + pad * 2 + d + 0.12 * d
        block = d * 0.16 * CAP + d * 0.14 + max(0, count - 1) * d * 0.268 + CAP * value
        first = b.iy + (b.ih - block) / 2
        b.label("title", title, x0, first, max(8, round(26 * u)), color=color,
                width=b.ix + b.iw - x0)  # fmt: skip
        row_top = first + d * 0.16 * CAP + d * 0.14
        room = b.ix + b.iw - x0 - pad
    else:
        base = top + d + 0.28 * d
        b.label("title", title, cx, base - CAP * max(8, round(26 * u)), max(8, round(26 * u)),
                color=color, align="center", width=b.iw)  # fmt: skip
        row_top = base + 0.287 * d - CAP * value
        room = min(b.iw - pad, 1.1 * d)
    # a reading in a row: its name, its number (right-aligned in a column of the widest) and
    # its unit; the row is as wide as the widest of each, smaller where that is not room
    names = [name.upper() for _, _, name in shown]
    parts = [_unit_of(fmt) for _, fmt, _ in shown]
    fonts = b.style["text_font"], b.style["display_font"]
    gap = 8 * u

    def measure(k: float) -> tuple[float, float, float]:
        spacing = max(1, round(label * k * 0.14))
        names_w = max(
            (M._width_per_size(fonts[0], n) * label * k + spacing * len(n) for n in names),
            default=0,
        )
        number_w = max(
            (M._width_per_size(fonts[1], _sample(n, s)) * value * k for n, s in parts), default=0
        )
        unit_w = max((M._width_per_size(fonts[0], s) * note * k for _, s in parts), default=0)
        return names_w, number_w, unit_w

    k = 1.0
    for _ in range(4):  # smaller until the row fits (at least a little over half)
        names_w, number_w, unit_w = measure(k)
        total = names_w + gap * k + number_w + (5 * u * k + unit_w if unit_w else 0)
        if total <= room or k <= 0.6:
            break
        k = max(0.6, k * room / total)
    label_k, value_k, note_k = label * k, value * k, note * k
    names_w, number_w, unit_w = measure(k)
    total = names_w + gap * k + number_w + (5 * u * k + unit_w if unit_w else 0)
    left = x0 if wide else cx - total / 2
    edge = left + names_w + gap * k + number_w
    for i, (sensor, _fmt, name) in enumerate(shown):
        number, suffix = parts[i]
        cap = row_top + i * 0.268 * d
        base = cap + CAP * value
        first = len(b.out)
        b.label(f"r{i}-label", name, left, base - CAP * label_k, label_k, width=names_w + 2 * u)
        b.value(f"r{i}", sensor, number, edge, base - CAP * value_k, value_k, align="right",
                width=number_w * 1.15, hide_if_missing=True,
                color_rules=M._heat(b) if sensor.endswith(".temp") else [])  # fmt: skip
        if suffix:
            b.add("text", f"r{i}-unit", text=suffix, x=round(edge + 5 * u * k),
                  **b._text_fields(note_k, base - CAP * note_k, "text_font", color=b.c("text3"),
                                   max_width=round(unit_w * 1.2), tabular=False))  # fmt: skip
        for widget in b.out[first:]:
            if widget["id"] != f"r{i}":
                _need(widget, sensor)


def _sample(number: str, unit: str) -> str:
    """The widest a reading may be, as text to measure: a clock or a fan in four digits."""
    if ".1f" in number:
        return "00.0"
    return "0000" if unit in ("MHz", "RPM") else "000"


# -- the other cards (made next) ---------------------------------------------------------------


def build_download(b: _Build, src: M.Source) -> None:
    """A download as the inset of SPUR II's Studio screen: what loads, the progress, the speed
    and the time left, and the speed of the last minutes (render.download). Where nothing
    downloads, the card says so."""
    b.card()
    b.add("downloads", "download", x=round(b.ix), y=round(b.iy), w=round(b.iw), h=round(b.ih),
          font=b.style["display_font"], label_font=b.style["text_font"], color=b.c("text"),
          muted=b.c("text3"), color2=b.tint("net"), done=b.c("accent"), background=b.c("track"),
          empty=t("No download running"), glow=round(0.3 * b.glow, 2))  # fmt: skip


def build_cards(b: _Build, src: M.Source) -> None:
    b.card()


def build_netcard(b: _Build, src: M.Source) -> None:
    M.build_network(b, src)

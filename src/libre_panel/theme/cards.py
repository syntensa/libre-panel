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
from libre_panel.theme.model import COVER_COLOR
from libre_panel.theme.modules import CAP, _Build


class _Ref:
    """A card in the numbers of the reference card (``ref_w`` x ``ref_h`` px): sizes and
    distances grow with the module's height, or shrink with its width where the card would
    not fit (``least_w``: how wide it needs to be at the reference height). The right edge
    stays at the module's right edge, so a wider module has room for more hours or a longer
    title, not for bigger letters. ``ok`` says whether it is big enough to be this card."""

    def __init__(self, b: _Build, ref_w: float, ref_h: float, least_w: float, floor: float = 0.55,
                 aspect: float = 1.35, height: float | None = None):  # fmt: skip
        self.b = b
        h = b.h if height is None else height  # (``height``: only the top of the box)
        self.g = min(h / ref_h, b.w / least_w)
        # too small, or too narrow for its height (the card would stand in the middle of a
        # box with room left above and below it): the older module of its kind does better
        self.ok = self.g >= floor and b.w >= aspect * h
        self.left = b.x
        # a card that is not as tall stands in the middle
        self.top = b.y + (h - ref_h * self.g) / 2

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
    weather module. A tall one (a column of a large panel) has the card at its top, nearly at
    the card's own size, and under it the next days as rows.
    """
    ref = _Ref(b, 706, 376, 590)
    head = b.h
    if not ref.ok:
        head = round(b.w / 1.5)
        ref = _Ref(b, 706, 376, 590, height=head)
        if ref.g < 0.8 or b.h - head < 0.6 * head:
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
    gap = ref.s(14)  # ... but never into each other: each has its room less this
    for i, (key, fmt, at, room) in enumerate(details):
        if i == 2 and squeeze < 0.9:  # (the last goes first)
            break
        # (places and rooms in the reference's px, not ``s``: its smallest size would move the
        # first one right of the text column it belongs to)
        b.value(f"detail{i}", key, fmt, left + at * ref.g * squeeze, ref.cap(194, 20), small,
                color=b.c("text3"), width=max(8, room * ref.g * squeeze - gap), font="text_font",
                tabular=False, fallback="", hide_if_missing=True)  # fmt: skip

    # the rain of the next two hours (blue when it starts or stops soon), and the sun; in a
    # tall card across it, under the symbol (its column of text is narrow for both)
    line = ref.cap(222, 20)
    at, room = (ref.x(30), right - ref.x(30)) if head < b.h else (left, width)
    for name, need, role in (("rain", "weather.rain.minutes", "gpu"),
                             ("dry", "!weather.rain.minutes", "text3")):  # fmt: skip
        b.value(name, "weather.rain.text", "{value}", at, line, small, color=b.c(role),
                width=room * 0.55, font="text_font", tabular=False, fallback="",
                fit="ellipsis", needs=need)  # fmt: skip
    for name, key, text, up in (
        ("sun-set", "sun.set", t("Sun until {value}"), 1),
        ("sun-rise", "sun.next_rise", t("Sun from {value}"), 0),
    ):
        b.value(name, key, text, at + room, line, small, color=b.c("text3"), align="right",
                width=room * 0.42, font="text_font", tabular=False, fallback="",
                fit="ellipsis", needs=f"sun.up={up}")  # fmt: skip

    # the next hours across the card (a tall one has room to set them off the line above)
    drop = ref.s(18) if head < b.h else 0
    lower = ref.s(4) if head < b.h else 0  # (its bars as far under the degrees as the days')
    step = max(1, min(6, b.module["step"] or 1))  # (the Studio shows the next hours)
    strip = ref.x(24)
    count = int(M._clamp((right - strip) // ref.s(109), 1, 24 // step))
    cell = (right - strip) / count
    edge = ref.s(64)
    for i in range(count):
        key = f"weather.hour.{step * (i + 1)}"
        cx = strip + cell * (i + 0.5)
        first = len(b.out)
        b.value(f"h{i}-time", f"{key}.time", "{value}", cx, ref.cap(252, 24) + drop, ref.s(24),
                color=b.c("text2"), align="center", width=cell * 0.95, font="text_font",
                tabular=False, fallback="")  # fmt: skip
        b.add("symbol", f"h{i}-symbol", x=round(cx - edge / 2), y=round(ref.y(258) + drop),
              size=edge, sensor=f"{key}.code", color=clouds, moving=b.module["moving"],
              offset=round(i * 1.7, 2))  # fmt: skip
        b.value(f"h{i}-temp", f"{key}.temperature", "{value:.0f}°", cx, ref.cap(352, 34) + drop,
                ref.s(34), align="center", width=cell * 0.92)  # fmt: skip
        _bar(b, f"h{i}-rain", f"{key}.rain", cx - cell * 0.36, ref.y(358) + drop + lower,
             cell * 0.72, ref.s(9, 3), b.c("gpu"))  # fmt: skip
        for widget in b.out[first:]:  # a column only while its hour is known
            _need(widget, f"{key}.code")
    if head < b.h:  # the next days under the card, each under an hour: as many, as wide
        top = b.y + head + drop
        days = _Build({**b.module, "module": "outlook", "card": "off"},
                      [round(strip), round(top), round(right - strip),
                       round(b.y + b.h - top - ref.s(8))], b.colors, b.style)  # fmt: skip
        days.ix, days.iy, days.iw, days.ih = strip, top, right - strip, days.h
        for i in range(min(count, 7)):
            first = len(days.out)
            M._outlook_column(days, i, f"weather.day.{i}", count, clouds, b.c("gpu"))
            for widget in days.out[first:]:  # a day only while it is known
                _need(widget, f"weather.day.{i}.code")
        low = max(w["y"] + w.get("h", w.get("size", 0)) for w in days.out)
        down = max(0, round((top + days.ih - low) / 2))  # the days in the middle of their room
        for widget in days.out:
            widget["id"] = "days-" + widget["id"]
            widget["y"] += down
            _need(widget, "weather.temperature")  # (without weather: the calendar sheet)
            b.out.append(widget)
    b.needs = ""


# -- the playback card ------------------------------------------------------------------------


def build_playback(b: _Build, src: M.Source) -> None:
    """What plays: the cover, the state, the title and artist, the app with its icon, the
    progress with the time played and the length (``time`` ``remaining``: the time left).
    Nothing playing is a note and a word."""
    ref = _Ref(b, 706, 376, 560)
    if not ref.ok:
        M.build_music(b, src)
        return
    b.card()
    color = b.module.get("color") or COVER_COLOR  # the colour the cover carries, as the Studio's
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
    for name, shift, need in (("app", 40, "@media.app_icon"), ("app-bare", 0, "!@media.app_icon")):
        b.value(name, "media.source", "{value}", left + ref.s(shift), ref.cap(206, 20), ref.s(20),
                color=b.c("text3"), width=width - ref.s(shift), font="text_font",
                tabular=False, fallback="", fit="ellipsis", hide_if_missing=True,
                needs=need)  # fmt: skip

    # the progress, with the time played and the length
    strip = ref.x(24)
    _bar(b, "progress", "media.progress", strip, ref.y(304), right - strip, ref.s(8, 3), color,
         high=1, hide_if_missing=True)  # fmt: skip
    time = ref.s(22)
    b.value("position", "media.position", "{value:clock}", strip, ref.cap(340, 22), time,
            color=b.c("text3"), width=(right - strip) / 2, font="text_font",
            hide_if_missing=True)  # fmt: skip
    sensor, fmt = M.end_time(b)  # the length, or what is left of it
    b.value("length", sensor, fmt, right, ref.cap(340, 22), time, color=b.c("text3"),
            align="right", width=(right - strip) / 2, font="text_font",
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


def _fan_name(sensor: str) -> str:
    """A fan's name from its sensor: ``gpu.fan`` is "gpu", ``fan.board.case_1`` "case 1"."""
    parts = sensor.split(".")
    name = parts[-2] if parts[-1] == "fan" and len(parts) > 1 else parts[-1]
    return name.replace("_", " ")


def _fan_title(
    b: _Build, title: str, sensor: str, x: float, top: float, size: float, color: str,
    width: float, align: str = "left",
) -> None:  # fmt: skip
    """The fan's name over its speed: the module's ``title``, else the name the fan goes by
    (the user's own, FanControl's, the sensor's: sensors/fan_names.py), in capitals."""
    if title:
        b.label("title", title, x, top, size, color=color, align=align, width=width)
        return
    b.value(
        "title", sensor, "{label:upper}", x, top, size, color=color, align=align, width=width,
        font="text_font", tabular=False, letter_spacing=max(1, round(size * 0.14)),
        fit="shrink", fallback=_fan_name(sensor).upper(),
    )  # fmt: skip


def _build_fan(b: _Build, src: M.Source) -> None:
    """A fan with its speed, as the top row of the Studio screen shows them: the rotor in
    its ring (the ``fan`` widget: it turns with the speed) and, beside it or under it, the
    name and the speed in RPM. ``sensor`` says which fan (default ``gpu.fan``)."""
    sensor = b.module["sensor"] or src.load
    color = b.module["color"] or b.c("gpu" if sensor.startswith("gpu") else "cpu")
    b.card()
    title = b.module["title"]
    fonts = b.style["text_font"], b.style["display_font"]
    wide = b.aspect >= 1.5
    value = min(b.ih * (0.36 if wide else 0.2), b.iw * 0.2)
    digits = M._width_per_size(fonts[1], "0000")
    unit_size = max(7, value * 0.5)
    unit_w = M._width_per_size(fonts[0], "RPM") * unit_size
    name = value * 0.55
    gap = value * 0.16
    if wide:
        d = min(b.ih * 0.84, b.iw * 0.4)
        x0 = b.ix + b.pad * 0.5
        left = x0 + d + 0.16 * d
        room = b.ix + b.iw - left
        if digits * value + gap + unit_w > room:  # smaller until the number and its unit fit
            k = room / (digits * value + gap + unit_w)
            value, unit_size, name, gap = value * k, unit_size * k, name * k, gap * k
        unit_w = M._width_per_size(fonts[0], "RPM") * unit_size
        block = CAP * name + gap * 1.6 + CAP * value
        top = b.iy + (b.ih - block) / 2
        fan_xy = (x0, b.iy + (b.ih - d) / 2)
        _fan_title(b, title, sensor, left, top, max(7, name), color, room)
        number_at, cap = left + digits * value, top + CAP * name + gap * 1.6
        align_right = True
    else:
        d = min(b.iw * 0.74, b.ih * 0.52)
        if digits * value + gap + unit_w > b.iw * 0.92:
            k = b.iw * 0.92 / (digits * value + gap + unit_w)
            value, unit_size, name, gap = value * k, unit_size * k, name * k, gap * k
            unit_w = M._width_per_size(fonts[0], "RPM") * unit_size
        block = d + 0.14 * d + CAP * name + gap * 1.6 + CAP * value
        top = b.iy + (b.ih - block) / 2
        cx = b.ix + b.iw / 2
        fan_xy = (cx - d / 2, top)
        title_top = top + d + 0.14 * d
        _fan_title(b, title, sensor, cx, title_top, max(7, name), color, b.iw, align="center")
        cap = title_top + CAP * name + gap * 1.6
        pair = digits * value + gap + unit_w
        number_at = cx - pair / 2 + digits * value
        align_right = True
    b.add("fan", "fan", x=round(fan_xy[0]), y=round(fan_xy[1]), w=round(d), h=round(d),
          sensor=sensor, color=color, max_rpm=3000, frame="ring")  # fmt: skip
    b.value("value", sensor, "{value:.0f}", number_at, cap, max(8, value),
            align="right" if align_right else "left", width=digits * value * 1.15)  # fmt: skip
    b.add("text", "unit", text="RPM", x=round(number_at + gap),
          **b._text_fields(unit_size, cap + CAP * value - CAP * unit_size, "text_font",
                           color=b.c("text3"), tabular=False))  # fmt: skip


# The readings under a ring, as the Studio screen shows them: the processor's temperature, power
# and clock; the graphics card's temperature, power and memory (its fans have instruments of
# their own); what is free of the memory, its modules' temperature and the speed they run at.
# A source that is not here shows the rows its module has.
def _rows() -> dict[str, tuple[tuple[str, str, str], ...]]:
    heat = ("{value:.0f}°C", t("Temp"))
    return {
        "cpu": (("cpu.temp", heat[0], heat[1]), ("cpu.power", "{value:.0f} W", t("Watt")),
                ("cpu.freq", "{value:.0f} MHz", t("Freq"))),
        "gpu": (("gpu.temp", heat[0], heat[1]), ("gpu.power", "{value:.0f} W", t("Watt")),
                ("gpu.mem.used", "{value:gb} GB", t("VRAM"))),
        "mem": (("mem.free", "{value:.1f} GB", t("Free")), ("mem.temp", heat[0], heat[1]),
                ("mem.speed", "{value:.0f} MT/s", t("Freq"))),
    }  # fmt: skip


# What a PC often cannot tell stays on show as "--" with its name, so the column keeps its
# shape: the modules' temperature needs administrator rights, their speed a readable SMBIOS
# table. The other sources leave a row out that has no reading.
_KEPT = ("mem",)


def _heat_of(b: _Build, sensor: str) -> list[dict[str, Any]]:
    """The colours a temperature row turns to: a memory module runs cooler than a processor
    (about 40 to 55 °C, 85 °C is its limit)."""
    if sensor == "mem.temp":
        return M._heat_rules(b, warn=58, crit=66)
    return M._heat(b) if sensor.endswith(".temp") else []


def build_instrument(b: _Build, src: M.Source) -> None:
    """A ring with its value inside, as SPUR II's Studio shows CPU, GPU and RAM: the load as
    light in a milled groove, the number in the middle with a small unit, the name under it
    and the readings of the part (temperature, power, clock ...) in rows below, as many as
    fit. A wide module puts the name and the rows beside the ring. ``source`` ``fan`` is a
    fan instead: the rotor in its ring with its name and speed."""
    if b.module["source"] == "fan":
        _build_fan(b, src)
        return
    color = b.tint(src.role)
    b.card()
    rows = list(_rows().get(src.role, src.details))
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
    kept = src.role in _KEPT
    for i, (sensor, _fmt, name) in enumerate(shown):
        number, suffix = parts[i]
        cap = row_top + i * 0.268 * d
        base = cap + CAP * value
        first = len(b.out)
        b.label(f"r{i}-label", name, left, base - CAP * label_k, label_k, width=names_w + 2 * u)
        b.value(f"r{i}", sensor, number, edge, base - CAP * value_k, value_k, align="right",
                width=number_w * 1.15, hide_if_missing=not kept, fallback="--",
                color_rules=_heat_of(b, sensor))  # fmt: skip
        if suffix:
            b.add("text", f"r{i}-unit", text=suffix, x=round(edge + 5 * u * k),
                  **b._text_fields(note_k, base - CAP * note_k, "text_font", color=b.c("text3"),
                                   max_width=round(unit_w * 1.2), tabular=False))  # fmt: skip
        for widget in b.out[first:]:
            if widget["id"] != f"r{i}" and not (kept and widget["id"] == f"r{i}-label"):
                _need(widget, sensor)  # (no unit where there is no number)


def _sample(number: str, unit: str) -> str:
    """The widest a reading may be, as text to measure: a clock, a fan or the memory's speed in
    four digits."""
    if ".1f" in number:
        return "00.0"
    return "0000" if unit in ("MHz", "RPM", "MT/s") else "000"


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


# -- the network, as the Studio's NET section has it -----------------------------------------------


def build_net(b: _Build, src: M.Source) -> None:
    """UP and DOWN with the speed beside them, and the history of both: the upload above a
    line, the download hanging below it. Where the module is small, it is the network module."""
    ref = _Ref(b, 596, 130, 540, floor=0.45, aspect=1.7)
    if not ref.ok:
        M.build_network(b, src)
        return
    b.card()
    color = b.tint("net")
    left, right = ref.x(13), ref.right(16)
    title = b.module["title"] or t("Net")
    size = ref.s(26)
    b.label("title", title, left, ref.cap(30, 26), size, color=color, width=ref.s(300))
    width = M._width_per_size(b.style["text_font"], title.upper()) * size + max(
        1, round(size * 0.14)
    ) * len(title)
    thin = max(1, round(1.5 * ref.g))
    b.add("rect", "rule", x=round(left), y=round(ref.y(37)), w=round(width + ref.s(6)), h=thin,
          color=M.mix(color, b.c("surface"), 0.55))  # fmt: skip
    for i, (key, name) in enumerate((("net.up", t("Up")), ("net.down", t("Down")))):
        baseline = (81, 125)[i]
        first = len(b.out)
        b.label(name.lower(), name, left, ref.cap(baseline, 26), ref.s(26), width=ref.s(150))
        b.value(
            f"{name.lower()}-value",
            key,
            "{value:rate}",
            ref.x(240),
            ref.cap(baseline, 34),
            ref.s(34),
            align="right",
            width=ref.s(150),
        )
        b.value(f"{name.lower()}-unit", key, "{value:rate_unit}", ref.x(246), ref.cap(baseline, 19),
                ref.s(19), color=b.c("text3"), width=ref.s(80), font="text_font", tabular=False,
                fallback="")  # fmt: skip
        for widget in b.out[first:]:
            widget["hide_if_missing"] = True
    gx, gw = ref.x(392), right - ref.x(392)
    if gw >= ref.s(40):
        mid, half = ref.y(88), ref.s(38)
        for name, key, down in (
            ("up-history", "net.up", False),
            ("down-history", "net.down", True),
        ):
            b.add("graph", name, x=round(gx), y=round(mid if down else mid - half), w=round(gw),
                  h=round(half), sensor=key, min=0, max=None, scale="sqrt",
                  history=max(30, min(300, round(gw) // 2)), color=color, fill=True,
                  fill_fade=True, smooth=True, line_width=max(1, round(2 * ref.g)),
                  direction="down" if down else "up", inset=False,
                  glow=round(0.35 * b.glow, 2), glow_radius=max(2, round(ref.g * 6)))  # fmt: skip
        b.add("rect", "axis", x=round(gx), y=round(mid), w=round(gw), h=max(1, round(ref.g)),
              color=b.c("line"))  # fmt: skip


# -- a slot: one card of several ---------------------------------------------------------------


def _child(b: _Build, name: str, box: list[int]) -> list[dict[str, Any]]:
    """The parts of the card ``name`` in ``box`` (a module without a card of its own)."""
    child = {**b.module, "module": name, "card": "off", "title": "", "items": "", "text": ""}
    if name != "net":
        return M.build_module(child, box, b.colors, b.style)
    inner = _Build(child, box, b.colors, b.style)
    build_net(inner, M._sources()["net"])
    return inner.out


def build_cards(b: _Build, src: M.Source) -> None:
    """A slot: the module's ``items`` are the cards it holds, one a line (``playback``,
    ``forecast``, ``download``, ``instrument``, ``net`` ...); the first that has something to
    show is on show, and a change rolls the old card up into a line of light and the new one
    down out of it (render.slot). Its frame is a thin line in the colour of the card on show.
    Every card is built for the whole slot: it is the slot's size that makes a card compact."""
    b.card(outline=False)
    inset = max(1, round(min(b.w, b.h) / 140)) + 1
    box = [b.x + inset, b.y + inset, b.w - 2 * inset, b.h - 2 * inset]
    for i, (name, _rule) in enumerate(M.slot_items(b.module)):
        for widget in _child(b, name, box):
            widget["id"] = f"c{i}-{widget['id']}"
            widget["_card"] = i
            b.out.append(widget)


def build_netcard(b: _Build, src: M.Source) -> None:
    """The network card: a download while one runs (the Studio's inset), else the network."""
    build_cards(b, src)

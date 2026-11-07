"""rust/render against render/ (docs/RUST.md, R4): the texts readings become (formatting.py):
str.format locked down to value, unit and label, every spec of Python's format() on every
kind of value, the app's own specs, and the usual text of a reading; the conditions widgets
are drawn on (``needs``); the frames of themes, to the bit: their ground (a colour, a gradient,
a picture) and their cards."""

from __future__ import annotations

import hashlib
import json
import math
import random
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from rust_check import image, noise, picture, run

from libre_panel import fonts
from libre_panel import icons as icon_set
from libre_panel.fonts.fallback import cursive
from libre_panel.render import formatting
from libre_panel.render.renderer import Renderer, need_holds
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import ThemeError, load_theme

FLOATS = [0.0, -0.0, 1.0, -1.5, 0.5, 2.5, 0.125, 0.375, 2.675, 1.005, 1e16, 1e-5, 123.456,
          1234567.891, math.nan, -math.nan, math.inf, -math.inf, 1e308, 5e-324, 9.995, 0.05,
          999.95, 1e22, 1e-7, 12345678901234567890.0, 0.1 + 0.2, 100.0, 99.95, 1023.5, 1048576.0,
          -1e-300, 86399.9, 3600.0]  # fmt: skip
INTS = [0, 1, -1, 7, 255, -42, 1234567, 10**20, -(2**63), 2**70, 10**400, 0x10FFFF, 0x110000,
        0xD800, 65, 86400, 90061, 59, 3599]  # fmt: skip
STRS = ["", "abc", "Grüße", "日本語", "123", " 1_000 ", "1e3", "inf", "١٢٣",
        "-0", "12.5", "ſtraße", "á", "nan", "5 "]  # fmt: skip
UNITS = ["", "%", "°C", "B", "B/s", "s", "GB", "GiB", "MB/s", "KiB", "W", "rpm", "°", "TB"]
LABELS = ["", "CPU Temp", "ſtraße", "İstanbul", "温度"]
# specs each value is formatted in: Python's edges (c, z, grouping with zeros, n, %, =) and the
# app's own
PROBES = ["", "c", "d", "n", "x", "#o", "_b", "e", "E", "%", ".0f", "z.0f", "z.1f", "010,", "+",
          " ", "=+10", "^9.2", ">5", "s", "<3", ".3g", "#.0e", "012_.3f", "bytes", "duration",
          "clock", "rate", "rate_unit", "upper", "gb", "999", "1000"]  # fmt: skip


def _typed(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"b": v}
    if isinstance(v, float):
        return {"f": struct.pack(">d", v).hex()}
    if isinstance(v, int):
        return {"i": str(v)}
    if isinstance(v, str):
        return {"s": v}
    if isinstance(v, list):
        return {"l": [_typed(x) for x in v]}
    return {"n": None}


def _values(rng: random.Random) -> list[Any]:
    values: list[Any] = [*FLOATS, *INTS, *STRS, True, False, None, [1, "a"]]
    for _ in range(120):
        values.append(struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0])
        values.append(round(rng.uniform(-2000, 2000), rng.randrange(0, 6)))
        values.append(rng.randrange(-(10**6), 10**6))
    return values


def _spec(rng: random.Random) -> str:
    pick = rng.choice
    spec = ""
    if rng.random() < 0.3:
        spec += pick(["", " ", "*", "0", "x", "{", "é", "<"]) + pick("<>=^")
    elif rng.random() < 0.3:
        spec += pick("<>=^")
    if rng.random() < 0.3:
        spec += pick("+- ")
    if rng.random() < 0.15:
        spec += "z"
    if rng.random() < 0.2:
        spec += "#"
    if rng.random() < 0.25:
        spec += "0"
    if rng.random() < 0.5:
        spec += str(pick([0, 1, 2, 5, 8, 10, 12, 20, 30, 999, 1000]))
    if rng.random() < 0.25:
        spec += pick([",", "_", ",_", "_,"])
    if rng.random() < 0.5:
        digits = pick([0, 1, 2, 3, 6, 10, 17, 20, 50, 999])
        spec += "." + str(digits) if rng.random() < 0.95 else "."
    if rng.random() < 0.8:
        spec += pick(list("bcdeEfFgGnosxX%") + ["", "q", "r", "dd"])
    return spec


def _format(rng: random.Random) -> str:
    pick = rng.choice
    field = pick(["value", "value", "value", "unit", "label", "", "0", "value.real", "value[0]",
                  " value", "x"])  # fmt: skip
    conv = pick(["", "", "", "!r", "!s", "!a", "!x", "!", "!\0"])
    spec = pick([_spec(rng), _spec(rng), "bytes", "duration", "clock", "rate", "rate_unit",
                 "upper", "gb", "{unit}", ">{label}", "{value}", "{unit:{label}}"])  # fmt: skip
    one = "{" + field + conv + (":" + spec if spec or rng.random() < 0.5 else "") + "}"
    around = pick(["", "", " °C", "{{", "}}", "x{{y}}z", "{", "}", "a{b", "{:", "{value:{"])
    if rng.random() < 0.7:  # a field of the value, plain: the spec's own refusals only
        clean = "{value:" + _spec(rng) + "}"
        return pick([clean, clean + " °C", "≈ " + clean, clean + "{{%}}"])
    return pick([one, one + around, around + one, one + one, f"{one} / {{unit}}", one[:-1]])


def _outcome(fn: Any, *args: Any) -> dict[str, Any]:
    try:
        text = fn(*args)
    except formatting.FormatError:
        return {"error": "FormatError"}
    except Exception as error:  # noqa: BLE001 (every kind Python raises is compared)
        return {"error": type(error).__name__}
    if isinstance(text, str) and any(0xD800 <= ord(c) < 0xE000 for c in text):
        # a half of a surrogate pair ({value:c} of 0xD800), which a Rust str cannot hold: a
        # refusal there, so the widget shows its fallback (docs/RUST.md)
        return {"error": "FormatError"}
    return {"text": text}


def test_texts_of_readings_as_python_formats_them() -> None:
    rng = random.Random(0xF0A7)
    values = _values(rng)
    cases = []
    for _ in range(8000):
        cases.append((_format(rng), rng.choice(values), rng.choice(UNITS), rng.choice(LABELS)))
    for i, v in enumerate(values):
        for j, spec in enumerate(PROBES):
            unit = UNITS[(i + j) % len(UNITS)]
            cases.append(("{value:" + spec + "}", v, unit, LABELS[(i + j) % len(LABELS)]))
    cases += [("{value:gb}", v, u, "") for v in values[:80] for u in UNITS]
    cases += [("{value:.0f}°C", 61.5, "°C", ""), ("{value:bytes}/s", 1234567, "B/s", ""),
              ("{label:upper}", 0, "", "ſtraße"), ("{value:,.2f}", 1e6 + 0.5, "", ""),
              ("{value:>300}", 1, "", ""), ("{value!r:{unit}}", "a", ">9", ""),
              ("{value:{unit:{label}}}", 5, "d", ""),
              ("{value:{unit:{label:{}}}}", 5, "", ""),
              # (a field's error before what is wrong further on: Python reads on as it goes)
              ("{value:c}{", 0x110000, "", ""), ("{value:.1f}}", 10**400, "", ""),
              ("{value:{value:c}}{", 0x110000, "", ""), ("{value:bytes}{value!", 10**400, "", ""),
              ("{value:c}{{}}{", -1, "", ""), ("{value:c}{x}", 0x110000, "", ""),
              ("{x}{value:c}", 0x110000, "", "")]  # fmt: skip
    auto = [(v, u) for v in values for u in UNITS]
    case = {"op": "render_format", "cases": [
        {"fmt": f, "value": _typed(v), "unit": u, "label": label} for f, v, u, label in cases],
        "auto": [{"value": _typed(v), "unit": u} for v, u in auto],
        "parts": [_typed(v) for v in values]}  # fmt: skip
    [ours] = run([case])
    assert ours["cases"] == [_outcome(formatting.safe_format, *c) for c in cases]
    assert ours["auto"] == [_outcome(formatting.auto_format, v, u) for v, u in auto]
    assert ours["parts"] == [
        {
            "bytes": _outcome(formatting.human_bytes, v),
            "duration": _outcome(formatting.human_duration, v),
            "clock": _outcome(formatting.clock_duration, v),
            "rate": _outcome(lambda x: list(formatting.rate_parts(x)), v),
        }
        for v in values
    ]
    # (texts, not only refusals alike)
    assert sum("text" in o for o in ours["cases"]) > 10000


# what conditions name and compare with: readings with a number, a word, none; a picture; the
# clock's own second; white space and the digits of other scripts; words in other cases
NEED_KEYS = ["cpu.load", "gpu.temp", "media.playing", "game.limiter", "media.state", "x",
             "time.second", "media.cover", "a b", "ſ"]  # fmt: skip
NEED_VALUES = ["1", "0", "-1", "80", "2.5", "-0.5", "١٢", "١٢.٥", "gpu", "GPU", "Straße",
               "STRASSE", "playing", "null", "a b", "a-b.c", "_x", "1a", "", "5.", ".5", "-",
               "x\ny", "ǅ", "Ⅻ", "x²"]  # fmt: skip
READING_VALUES = [None, True, False, 0, 1, 80, 2.5, -0.5, 12, 12.5, math.nan, math.inf, 10**400,
                  "gpu", "GPU ", "null", "", "strasse", "Straße", "playing", " 1", "a b", "0",
                  "x²"]  # fmt: skip


def _need(rng: random.Random) -> str:
    pick = rng.choice
    if rng.random() < 0.1:
        return pick(["", " ", "!", "!!", "@media.cover", "!@media.cover", "@", "@x", "=5", "a=",
                     "a!b", "a==", "a=<5", "a=>5", " =1", "a = = 1"])  # fmt: skip
    head = pick(["", "", "!", "!!", " ! ", "\t"]) + pick(NEED_KEYS)
    if rng.random() < 0.3:
        return head + pick(["", " ", "\n"])

    def space() -> str:
        return pick(["", "", " ", "\t", "\u3000"])

    op = pick(["=", "==", "!=", ">", ">=", "<", "<=", "=<", "=>", "!", "<>"])
    return head + space() + op + space() + pick(NEED_VALUES) + space()


def _snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    keys = rng.sample(NEED_KEYS, rng.randrange(len(NEED_KEYS)))
    readings = {key: rng.choice(READING_VALUES) for key in keys}
    images = [key for key in ("media.cover", "x", "") if rng.random() < 0.4]
    now = datetime(2026, 11, 7, 12, 30, rng.randrange(60), rng.randrange(10**6))
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()}, now=now,
                        images={key: object() for key in images})  # fmt: skip
    given = {"readings": {k: _typed(v) for k, v in readings.items()}, "images": images,
             "now": [now.year, now.month, now.day, now.hour, now.minute, now.second,
                     now.microsecond]}  # fmt: skip
    return given, snapshot


def test_conditions_of_widgets_hold_as_python_s() -> None:
    rng = random.Random(0x4EED)
    needs = [_need(rng) for _ in range(1500)]
    pairs = [_snapshot(rng) for _ in range(40)]
    [ours] = run([{"op": "render_needs", "needs": needs, "snapshots": [g for g, _ in pairs]}])
    expected = [[need_holds(need, snapshot) for need in needs] for _, snapshot in pairs]
    assert ours["holds"] == expected
    held = sum(map(sum, expected))
    assert 0.2 < held / (len(needs) * len(pairs)) < 0.8  # (both answers, often)


# -- frames -------------------------------------------------------------------------------------

THEME_FORMAT = "libre-panel-theme/1"
PALETTE = ["accent", "surface", "line", "text2"]


def _hex(rng: random.Random, alpha: bool = False) -> str:
    bands = 4 if alpha else 3
    return "#" + "".join(f"{rng.randrange(256):02x}" for _ in range(bands))


def _colour(rng: random.Random) -> str:
    pick = rng.random()
    if pick < 0.45:
        return _hex(rng)
    if pick < 0.65:
        return _hex(rng, alpha=True)
    if pick < 0.8:
        return "@" + rng.choice(PALETTE)
    if pick < 0.9:
        return "@surface"
    return "@cover" if rng.random() < 0.5 else rng.choice(["red", "rgb(10, 200, 30)", "#abc"])


def _rect(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    w, h = size

    def length(most: int) -> int:
        return rng.choice([rng.randrange(1, 14), rng.randrange(1, most + 40), most])

    rect: dict[str, Any] = {"type": "rect", "id": f"r{i}", "x": rng.randrange(-30, w),
                            "y": rng.randrange(-30, h), "w": length(w), "h": length(h),
                            "radius": rng.choice([0, 0, 1, 2, 5, 12, 40, 999, 10**30, -1, -2, -3]),
                            "gradient": rng.choice(["vertical", "horizontal"])}  # fmt: skip
    rect["color"] = _colour(rng) if rng.random() < 0.85 else None
    if rng.random() < 0.3:
        rect["color2"] = _colour(rng)
    if rng.random() < 0.35:
        rect["outline"] = _colour(rng)
        rect["outline_width"] = rng.choice([1, 1, 2, 3, 6, 30, 0, -1])
    if rng.random() < 0.15:
        rect["backdrop_blur"] = rng.choice([1, 4, 10, 64])
    if rng.random() < 0.3:
        rect["opacity"] = rng.choice([0.0, 0.3, 0.5, 0.999, 0.9989, 1, 0, 0.75])
    if rng.random() < 0.01:  # (out of range: the theme is refused, in both)
        rect["opacity" if rng.random() < 0.5 else "backdrop_blur"] = rng.choice([2, -1, 65])
    if rng.random() < 0.08:
        rect["visible"] = False
    if rng.random() < 0.15:
        rect["needs"] = rng.choice(["cpu.load", "!cpu.load", "cpu.load>50", "switch.frames",
                                    "!switch.frames", "@media.cover", "a, b"])  # fmt: skip
    return rect


# (and marks that reach beyond their letter: a combining acute's ink lies left of where it
# goes (Barlow), a double breve's right of it (Liberation), past a small text's margin)
TEXTS = ["CPU", "GPU Temp", "Hello World", "1234567890", "12:45", "Grüße aus Köln", "ﬁne ﬂow",
         "Ünïcödé", "  spaced  ", "", "two\nlines", "日本語", "😀 ok", "مرحبا", "x" * 40, "ß",
         "Ⅻ", "-12.5 °C", "W", "iiii", "e\u0301te\u0301 42", "a\u0361b 12"]  # fmt: skip
FONTS = ["", "", "builtin:Barlow-Medium", "builtin:Barlow-Bold", "builtin:Barlow-Regular",
         "builtin:BarlowCondensed-SemiBold", "builtin:JetBrainsMono-Medium",
         "builtin:LiberationSans-Regular", "builtin:LiberationSans-Bold"]  # fmt: skip


def _text(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    w, h = size
    text: dict[str, Any] = {"type": "text", "id": f"t{i}", "text": rng.choice(TEXTS),
                            "x": rng.randrange(-20, w + 10), "y": rng.randrange(-20, h),
                            "font": rng.choice(FONTS),
                            "font_size": rng.choice([6, 10, 14, 24, 37, 60, 120]),
                            "color": _colour(rng),
                            "align": rng.choice(["left", "center", "right"])}  # fmt: skip
    if rng.random() < 0.4:
        text["letter_spacing"] = rng.choice([1, 2.5, -1, 4, 0.3])
    if rng.random() < 0.4:
        text["tabular"] = True
    if rng.random() < 0.4:
        text["max_width"] = rng.choice([20, 40, 100, 300])
        text["fit"] = rng.choice(["shrink", "ellipsis"])
    if rng.random() < 0.2:
        text["uppercase"] = True
    if rng.random() < 0.1:
        text["translate"] = True
    if rng.random() < 0.2:
        text["opacity"] = rng.choice([0.5, 0.0, 0.999])
    return text


def _card_or_text(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _rect(rng, i, size) if rng.random() < 0.3 else _text(rng, i, size)


SENSORS = ["cpu.load", "cpu.temp", "gpu.temp", "audio.volume", "weather.description",
           "net.down", "mem.used", "battery.state"]  # fmt: skip
METRIC_FORMATS = ["{value:.0f}{unit}", "{value}", "{value:.1f} {unit}", "{label}: {value:.0f}",
                  "{value:bytes}", "{value:duration}", "{value:rate}", "{value:upper}", "{nope}",
                  "{value:x}", "{value:>8}", "", "{unit}", "{value:.0f}%", "{value:,}",
                  "{value:gb}", "{value!r}", "{0}", "{value:c}"]  # fmt: skip


def _metric(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A reading shown as a text, in a format, a fallback, colours above thresholds."""
    metric = _text(rng, i, size)
    for key in ("text", "uppercase", "translate"):
        metric.pop(key, None)
    metric.update(type="metric", sensor=rng.choice(SENSORS), format=rng.choice(METRIC_FORMATS))
    if rng.random() < 0.3:
        metric["fallback"] = rng.choice(["--", "", "n/a", "…", "Grüße"])
    if rng.random() < 0.5:
        metric["color_rules"] = [
            {"above": rng.choice([0, 20, 50.5, 80, -5, 10**9, 100, 50, 49.99]),
             "color": rng.choice([_colour(rng), "@accent", "@text2"])}
            for _ in range(rng.randrange(1, 4))
        ]  # fmt: skip
    if rng.random() < 0.2:
        metric["smooth"] = True  # (a still frame: no glide)
    if rng.random() < 0.15:
        metric["needs"] = rng.choice(["audio.muted", "cpu.load>10", "audio.muted, cpu.load"])
    return metric


def _metric_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    pick = rng.random()
    return _metric(rng, i, size) if pick < 0.7 else _card_or_text(rng, i, size)


READING_VALUES: list[Any] = [0, 1, 42, -3, 99.5, 50.5, 50, 1e12, 2**70, 0.004, -0.0, True,
                             False, None, "Overcast", "Clear", "42", "", "charging", math.nan,
                             math.inf, 123456789, 7.25]  # fmt: skip


def _metric_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """Readings of every kind for the metrics: their values, units and labels, the volume's
    muting."""
    readings: dict[str, Reading] = {}
    given: dict[str, Any] = {}
    for key in SENSORS:
        if rng.random() < 0.8:
            value = rng.choice(READING_VALUES)
            unit = rng.choice(["", "%", "°C", "B/s", "GB", "W"])
            label = rng.choice(["", "CPU", "Grüße"])
            readings[key] = Reading(key, value, unit=unit, label=label)
            given[key] = {"v": _typed(value), "unit": unit, "label": label}
    if rng.random() < 0.5:
        muted = rng.choice([1, 0, 1.0, True, "1", None, 2])
        readings["audio.muted"] = Reading("audio.muted", muted)
        given["audio.muted"] = _typed(muted)
    return {"readings": given, "images": {}}, Snapshot(readings=readings)


def _frame_theme(rng: random.Random, folder: Path, make: Any = None) -> dict[str, Any]:
    """A theme of cards (or what `make` makes) on a ground of a colour, a gradient, a picture
    or all of them."""
    make = make or _rect
    folder.mkdir()
    size = rng.choice([(64, 48), (160, 120), (320, 240), (480, 128), (17, 400)])
    palette = {name: _hex(rng, alpha=rng.random() < 0.2) for name in PALETTE}
    if make is _metric_or_more and rng.random() < 0.6:  # (the colour of a muted volume)
        palette["crit"] = _hex(rng)
    background: dict[str, Any] = {"color": rng.choice([_hex(rng), _hex(rng), _hex(rng, True),
                                                       "@accent", "#00000000"])}  # fmt: skip
    if rng.random() < 0.3:
        background["gradient"] = [_hex(rng), rng.choice([_hex(rng), _hex(rng, True), "@text2"])]
        if rng.random() < 0.5:
            background["gradient_direction"] = "horizontal"
    if rng.random() < 0.3:
        mode = rng.choice(["RGB", "RGBA", "RGBA"])
        picture_size = (rng.randrange(8, 400), rng.randrange(8, 300))
        noise(rng, mode, picture_size).save(folder / "ground.png")
        background["image"] = "ground.png"
    widgets = [make(rng, i, size) for i in range(rng.randrange(13))]
    data: dict[str, Any] = {"format": THEME_FORMAT, "name": "cards",
                            "display": {"width": size[0], "height": size[1]},
                            "palette": palette, "background": background,
                            "widgets": widgets}  # fmt: skip
    if rng.random() < 0.3:
        data["style"] = {"card_opacity": rng.choice([1.0, 0.5, 0.4, 1, 0.75, 0.3])}
    if rng.random() < 0.2:
        data["switches"] = {"frames": {"name": "Frames", "on": rng.random() < 0.5}}
    if make is not _rect and rng.random() < 0.3:
        data["font"] = rng.choice(FONTS[2:])
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def _frame_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    readings = {"cpu.load": rng.choice([10, 80, 50.5])} if rng.random() < 0.6 else {}
    images = {}
    if rng.random() < 0.4:  # a cover, its colour for "@cover"
        side = (rng.randrange(20, 90), rng.randrange(20, 90))
        images["media.cover"] = noise(rng, rng.choice(["RGB", "RGBA"]), side)
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()}, images=images)
    given = {"readings": {k: _typed(v) for k, v in readings.items()},
             "images": {k: image(v) for k, v in images.items()}}  # fmt: skip
    return given, snapshot


def _python_frame(folder: Path, snapshot: Snapshot, dither: bool) -> dict[str, Any]:
    try:
        theme = load_theme(folder)
    except ThemeError as error:
        return {"error": str(error)}
    renderer = Renderer(theme, preview=True, dither=dither)
    frame, boxes = renderer.render(snapshot, now=1000.0)
    return {"frame": frame, "boxes": boxes}


def _compare_frames(
    tmp_path: Path, seed: int, count: int, make: Any = None, snapshot_of: Any = None
) -> int:
    """Frames of `count` random themes (of what `make` makes) drawn by both, compared to the
    bit, and their boxes; how many were drawn."""
    rng = random.Random(seed)
    cases, expected = [], []
    for n in range(count):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, make)
        given, snapshot = (snapshot_of or _frame_snapshot)(rng)
        dither = rng.random() < 0.3
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither})
        expected.append(_python_frame(folder, snapshot, dither))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if "error" in want:
            assert "error" in got, n
            continue
        assert "sha256" in got, (n, got)
        frame = want["frame"]
        if got["sha256"] != hashlib.sha256(frame.tobytes()).hexdigest():
            [again] = run([{"op": "render_frame", "frames": [{**cases[n], "bytes": True}],
                            "fonts_dir": str(fonts.FONT_DIR)}])  # fmt: skip
            mine = picture(again["frames"][0]["frame"])
            differ = sum(a != b for a, b in zip(mine.getdata(), frame.getdata(), strict=True))
            raise AssertionError(f"theme {n}: {differ} pixels differ")
        assert got["boxes"] == want["boxes"], n
        drawn += 1
    return drawn


def test_frames_of_cards_as_python_draws_them(tmp_path: Path) -> None:
    assert _compare_frames(tmp_path, 0xCA2D, 160) > 120


def test_frames_of_texts_as_python_draws_them(tmp_path: Path) -> None:
    assert _compare_frames(tmp_path, 0x7E47, 160, _card_or_text) > 120


def test_frames_of_readings_shown_as_texts_as_python_draws_them(tmp_path: Path) -> None:
    assert _compare_frames(tmp_path, 0x3E7, 200, _metric_or_more, _metric_snapshot) > 150


def _measured(renderer: Renderer, widget: dict[str, Any]) -> float:
    """The width _fit holds a widget's text against its max_width."""
    font = renderer.font(widget.get("font", ""), widget.get("font_size", 24))
    text, tabular = widget["text"], widget.get("tabular", False)
    if cursive(text):
        return float(font.getlength(text))
    cell = renderer._digit_cell(font) if tabular else 0
    advances = [cell if tabular and ch in "0123456789" else renderer._advance(font, ch)
                for ch in text]  # fmt: skip
    return sum(advances) + widget.get("letter_spacing", 0) * max(0, len(text) - 1)


def test_frames_of_texts_that_just_fit_as_python_fits_them(tmp_path: Path) -> None:
    # (a text as wide as its max_width keeps its size; a pixel narrower, it is made to fit)
    folder = tmp_path / "measure"
    folder.mkdir()
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "measure", "display": {"width": 64, "height": 48},
        "widgets": []}), encoding="utf-8")  # fmt: skip
    measurer = Renderer(load_theme(folder), preview=True)
    exact = 0

    def just_fitting(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
        nonlocal exact
        text = _text(rng, i, size)
        for key in ("uppercase", "translate"):
            text.pop(key, None)
        if not text["text"].strip() or "\n" in text["text"]:
            text["text"] = rng.choice(["CPU 42", "12:45", "Grüße"])
        text["font"] = rng.choice(FONTS[2:])
        text["letter_spacing"] = rng.choice([0, 0, 1, -1, 4])
        measured = _measured(measurer, text)
        if measured == int(measured):
            exact += 1
            text["max_width"] = max(1, int(measured) - rng.choice([0, 0, 1]))
        else:
            text["max_width"] = max(1, rng.choice([math.floor(measured), math.ceil(measured)]))
        text["fit"] = rng.choice(["shrink", "ellipsis"])
        return text

    assert _compare_frames(tmp_path, 0xF175, 90, just_fitting) > 70
    assert exact > 40  # (widths that are whole: there it is the max_width to the pixel)


def test_frames_beyond_their_widgets_are_not_drawn_here_yet(tmp_path: Path) -> None:
    # (a screen, the panel's look and a finish other than flat: Rust says it does not draw them
    # yet, rather than a frame without them)
    rng = random.Random(0xB3D)
    cases, wanted = [], []
    for n in range(30):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _text)
        beyond = rng.choice(["screens", "looks", "finishes"])
        if beyond == "screens":
            data["screen"] = {"name": rng.choice(["earth", "no-such-screen"])}
        elif beyond == "looks":
            data["look"] = {"grid": rng.choice([True, {}, {"spacing": 12, "major": 0}])}
        else:
            finished = _text(rng, 99, (64, 48))
            finished["finish"] = rng.choice(["lit", "facet", "acrylic"])
            data["widgets"].append(finished)
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        try:
            load_theme(folder)
        except ThemeError:
            continue  # (one Python refuses: the frame tests have those)
        given, _ = _frame_snapshot(rng)
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        wanted.append(beyond)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    assert [got.get("unported", got) for got in ours["frames"]] == wanted
    assert len(wanted) > 20


def test_frames_of_cards_with_glow_and_shadow_within_a_level(tmp_path: Path) -> None:
    # (a glow's soft light is blurred in floating point: numpy multiplies by the blur's
    # matrices in float32 where Rust takes the passes, which Python's own result on another
    # processor differs by as much: now and then a pixel a level apart)
    import numpy as np

    rng = random.Random(0x61F)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder)
        for widget in data["widgets"]:
            if rng.random() < 0.6:
                widget["glow"] = rng.choice([0.3, 0.7, 1.0, 1])
                widget["glow_radius"] = rng.choice([2, 4, 6, 10, 20])
            if rng.random() < 0.4:
                widget["shadow"] = _hex(rng, alpha=rng.random() < 0.5)
                widget["shadow_blur"] = rng.choice([0, 2, 4, 6, 12])
                widget["shadow_offset"] = rng.choice([0, 3, -4, 8])
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _frame_snapshot(rng)
        dither = rng.random() < 0.3
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither, "bytes": True})
        expected.append(_python_frame(folder, snapshot, dither))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    differ = total = drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if "error" in want:
            assert "error" in got, n
            continue
        assert "frame" in got, (n, got)
        apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
        assert apart.max() <= 1, n
        differ += int((apart.max(axis=2) > 0).sum())
        total += apart.shape[0] * apart.shape[1]
        assert got["boxes"] == want["boxes"], n
        drawn += 1
    assert drawn > 45
    assert differ <= total // 10000


def test_gradients_in_float32_as_numpy_works_them_out() -> None:
    import numpy as np

    from libre_panel.render.renderer import _ramp_floats

    rng = random.Random(0x6AD)

    def colour() -> list[int]:
        return [rng.randrange(256) for _ in range(4)]

    grounds = [[colour(), colour(), n] for n in (1, 2, 3, 16, 48, 128, 480, 720, 1920, 4096)]
    grounds += [[colour(), colour(), rng.randrange(1, 4097)] for _ in range(60)]
    ramps = [[colour(), colour(), rng.randrange(1, 900), rng.choice([1, 3])] for _ in range(120)]
    [ours] = run([{"op": "render_ramps", "grounds": grounds, "ramps": ramps}])

    def bits(values: Any) -> list[str]:
        return [f"{v:08x}" for v in np.asarray(values, np.float32).ravel().view(np.uint32)]

    for (a, b, n), got in zip(grounds, ours["grounds"], strict=True):
        lo, hi = np.asarray(a, np.float32), np.asarray(b, np.float32)
        t = np.linspace(0.0, 1.0, n, dtype=np.float32)
        assert got == bits(lo[None, :3] + (hi - lo)[None, :3] * t[:, None]), n
    for (a, b, steps, sub), got in zip(ramps, ours["ramps"], strict=True):
        assert got == bits(_ramp_floats(tuple(a), tuple(b), steps, sub)), (steps, sub)


def test_every_icon_as_icons_py_draws_it() -> None:
    rng = random.Random(0x1C0)
    cases, expected = [], []
    for name in [*icon_set.ICON_NAMES, "no-such-icon"]:
        sizes = [(8, 2.0), (24, 2.0), (37, 2.5), (64, 1.0)]
        sizes.append((rng.choice([5, 13, 48, 100, 160]), rng.choice([0.5, 1.75, 4.0, 2])))
        for size, stroke in sizes:
            colour = (*(rng.randrange(256) for _ in range(3)), rng.choice([255, 255, 128, 30]))
            cases.append({"op": "icon", "name": name, "size": size, "color": list(colour),
                          "stroke": stroke})  # fmt: skip
            expected.append(icon_set.draw_icon(name, size, colour, stroke))
    for name in ("partly", "gamepad", "wifi-1"):  # (the largest: drawn 2048 px wide)
        cases.append({"op": "icon", "name": name, "size": 600, "color": [9, 200, 90, 255],
                      "stroke": 2.0})  # fmt: skip
        expected.append(icon_set.draw_icon(name, 600, (9, 200, 90, 255), 2.0))
    for n, (got, want) in enumerate(zip(run(cases), expected, strict=True)):
        assert "image" in got, (n, got)
        shown = (cases[n]["name"], cases[n]["size"], cases[n]["stroke"])
        assert picture(got["image"]).tobytes() == want.tobytes(), shown


WEATHER_CODES: list[Any] = [0, 1, 2, 3, 45, 48, 51, 61, 82, 83, 71, 85, 86, 77, 94, 95, 99,
                            1e300, -1e300, -5, 2.9, -0.5, "3", " 45 ", "abc", "", None,
                            math.nan, math.inf, True, False, 10**30, -(10**30), "1_000", [1],
                            "٣"]  # fmt: skip
SIGNALS: list[Any] = [None, 10, 33.9, 34, 66, 66.99, 67, 100, True, False, math.nan, "50",
                      2**70, -(2**70), -math.inf]  # fmt: skip


def test_the_icon_the_weather_and_the_connection_show_as_icons_py_chooses_it() -> None:
    weather = [{"code": _typed(code), "night": night} for code in WEATHER_CODES
               for night in (False, True)]  # fmt: skip
    kinds = ["lan", "wlan", "", None, 5, "LAN"]
    link = [{"kind": _typed(kind), "signal": _typed(signal)}
            for kind in kinds for signal in SIGNALS]  # fmt: skip
    [got] = run([{"op": "icon_names", "weather": weather, "link": link}])
    assert got["weather"] == [icon_set.weather_icon_name(code, night) for code in WEATHER_CODES
                              for night in (False, True)]  # fmt: skip
    assert got["link"] == [icon_set.link_icon(kind, signal)
                           for kind in kinds for signal in SIGNALS]  # fmt: skip


def _icon(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    w, h = size
    icon: dict[str, Any] = {"type": "icon", "id": f"i{i}", "x": rng.randrange(-20, w),
                            "y": rng.randrange(-20, h),
                            "icon": rng.choice([*icon_set.ICON_NAMES, "weather", "link"]),
                            "size": rng.choice([8, 16, 24, 32, 48, 70]), "color": _colour(rng),
                            "stroke": rng.choice([1, 1.5, 2, 2.5, 3])}  # fmt: skip
    if icon["icon"] in ("weather", "link") and rng.random() < 0.4:
        icon["sensor"] = rng.choice(["weather.hour.3.code", "weather.code", "net.link.kind",
                                     "other.code", "x"])  # fmt: skip
    if rng.random() < 0.2:
        icon["opacity"] = rng.choice([0.5, 0.0])
    if rng.random() < 0.3:
        icon["hide_if_missing"] = True
    return icon


def _icon_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _icon(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def _icon_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The weather and the connection the icons follow, at an hour of the day or night."""
    values = {"weather.code": WEATHER_CODES, "weather.hour.3.code": WEATHER_CODES,
              "weather.hour.3.day": [True, False, 0, 1, None], "weather.is_day": [1, 0, None],
              "net.link.kind": ["lan", "wlan", "", None], "net.wifi.signal": SIGNALS,
              "other.code": [61]}  # fmt: skip
    readings = {k: rng.choice(v) for k, v in values.items() if rng.random() < 0.7}
    now = datetime(2026, 11, 7, rng.randrange(24), rng.randrange(60), 5)
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()}, now=now)
    moment = [now.year, now.month, now.day, now.hour, now.minute, now.second, 0]
    given = {"readings": {k: _typed(v) for k, v in readings.items()}, "images": {},
             "now": moment}  # fmt: skip
    return given, snapshot


def test_frames_of_icons_as_python_draws_them(tmp_path: Path) -> None:
    assert _compare_frames(tmp_path, 0x1C05, 120, _icon_or_more, _icon_snapshot) > 90


def test_the_weather_icon_by_day_and_night_as_python_tells_it(tmp_path: Path) -> None:
    # (the sun or the moon: the reading of its own hour says, else now's for now, else the
    # clock: night before 6 and from 20)
    cases, expected = [], []
    absent = object()
    for sensor in ["", "weather.code", "weather.hour.3.code", "x"]:
        for hour in (0, 5, 6, 12, 19, 20, 23):
            for day in (absent, True, False, 0, None, "", 0.0, "yes"):
                for is_day in (absent, 1, 0):
                    folder = tmp_path / f"t{len(cases)}"
                    folder.mkdir()
                    icon = {"type": "icon", "id": "w", "icon": "weather", "x": 4, "y": 4,
                            "size": 32, "color": "#ffcc00"}  # fmt: skip
                    if sensor:
                        icon["sensor"] = sensor
                    data = {"format": THEME_FORMAT, "name": "night", "widgets": [icon],
                            "display": {"width": 40, "height": 40},
                            "background": {"color": "#000000"}}  # fmt: skip
                    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
                    readings: dict[str, Any] = {"weather.code": 0, "weather.hour.3.code": 1,
                                                "x": 0}  # fmt: skip
                    if day is not absent:
                        readings["weather.day"] = readings["weather.hour.3.day"] = day
                    if is_day is not absent:
                        readings["weather.is_day"] = is_day
                    now = datetime(2026, 11, 7, hour, 30)
                    snapshot = Snapshot(
                        readings={k: Reading(k, v) for k, v in readings.items()}, now=now
                    )
                    given = {"readings": {k: _typed(v) for k, v in readings.items()},
                             "images": {}, "now": [2026, 11, 7, hour, 30, 0, 0]}  # fmt: skip
                    cases.append({"folder": str(folder), "snapshot": given, "dither": False})
                    expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        assert got.get("sha256") == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n


def test_widgets_left_out_where_their_reading_is_missing_as_python_leaves_them_out(
    tmp_path: Path,
) -> None:
    # (hide_if_missing: a weather widget's field, the code a weather icon follows, else the
    # widget's sensor; a graph Python leaves out by its course and history: Rust says it does
    # not draw graphs yet, rather than a frame with it or without it)
    rng = random.Random(0x41D)
    cases, expected, left_out = [], [], 0
    for n in range(80):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        kind = rng.choice(["weather", "icon", "icon", "graph", "metric"])
        widget: dict[str, Any] = {"type": kind, "id": "left", "x": rng.randrange(40),
                                  "y": rng.randrange(40)}  # fmt: skip
        widget["hide_if_missing"] = rng.random() < 0.8
        if kind == "weather":
            widget["field"] = rng.choice(["temperature", "humidity", "code", "description"])
        elif kind == "icon":
            widget.update(icon=rng.choice(["weather", "weather", "link", "cpu"]), size=24)
        if kind != "weather" and rng.random() < 0.6:
            widget["sensor"] = rng.choice(["weather.hour.3.code", "weather.code", "cpu.load",
                                           "net.link.kind"])  # fmt: skip
        data["widgets"].insert(rng.randrange(len(data["widgets"]) + 1), widget)
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        try:
            theme = load_theme(folder)
        except ThemeError:
            continue  # (one Python refuses: the frame tests have those)
        values = {"weather.temperature": 21.5, "weather.humidity": 40, "weather.code": 61,
                  "weather.hour.3.code": 3, "cpu.load": 50, "net.link.kind": "lan"}  # fmt: skip
        readings = {k: v for k, v in values.items() if rng.random() < 0.5}
        now = datetime(2026, 11, 7, 12, 30, 5)
        snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()}, now=now)
        given = {"readings": {k: _typed(v) for k, v in readings.items()}, "images": {},
                 "now": [2026, 11, 7, 12, 30, 5, 0]}  # fmt: skip
        renderer = Renderer(theme, preview=True)
        [shown] = [w for w in renderer.widgets if w["id"] == "left"]
        gone = renderer._missing(shown, snapshot)
        left_out += gone
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        if kind == "graph" or (kind == "weather" and not gone):
            expected.append({"unported": f"{kind} widgets"})
        else:
            expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if "unported" in want:
            assert got.get("unported") == want["unported"], (n, got)
            continue
        assert got.get("sha256") == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n
        assert got["boxes"] == want["boxes"], n
    assert left_out > 20 and len(cases) > 60

"""rust/render against render/ (docs/RUST.md, R4): the texts readings become (formatting.py):
str.format locked down to value, unit and label, every spec of Python's format() on every
kind of value, the app's own specs, and the usual text of a reading; the conditions widgets
are drawn on (``needs``); the frames of themes, to the bit: their ground (a colour, a gradient,
a picture) and their cards."""

from __future__ import annotations

import base64
import calendar
import functools
import hashlib
import io
import json
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest
from rust_check import image, noise, picture, run

from libre_panel import fonts, i18n
from libre_panel import icons as icon_set
from libre_panel.fonts.fallback import cursive
from libre_panel.render import download, formatting, lists, moon
from libre_panel.render.countdown import countdown_text
from libre_panel.render.lit import WEATHER_ICONS
from libre_panel.render.renderer import Renderer, need_holds
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import ThemeError, load_theme


@functools.cache
def _moon_face() -> tuple[Any, Any, Any]:
    """moon.face() as numpy makes it on a processor without AVX-512 (its own exp for float32,
    the C library's for the rest; with AVX-512 on Linux it takes Intel's SVML for some, a unit
    in the last place apart here and there): made in a process told to leave those of its
    features unused that this one has. Rust's face is that one."""
    import numpy as np
    from numpy._core._multiarray_umath import __cpu_features__ as features

    env = dict(os.environ)
    unused = [name for name in ("X86_V4", "AVX512_ICL", "AVX512_SPR") if features.get(name)]
    if unused:
        env["NPY_DISABLE_CPU_FEATURES"] = " ".join(unused)
    code = ("import sys; import numpy as np; from libre_panel.render import moon; "
            "a, gx, gy = moon.face(); np.savez(sys.argv[1], albedo=a, gx=gx, gy=gy)")  # fmt: skip
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "face.npz"
        subprocess.run([sys.executable, "-c", code, str(path)], env=env, check=True, timeout=600)
        with np.load(path) as made:
            return made["albedo"], made["gx"], made["gy"]


@pytest.fixture(autouse=True)
def _moon_as_rust_s(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every moon Python draws here of that face (made when one is first drawn)."""
    monkeypatch.setattr(moon, "face", _moon_face)
    moon._disc_kept.cache_clear()
    yield
    moon._disc_kept.cache_clear()


_DRAWN_LOOKS = """
import json, struct, sys
import numpy as np
from libre_panel.render import fan, fan_looks
for n, (name, size, what) in enumerate(json.loads(open(sys.argv[1]).read())):
    look = fan_looks.look(name)
    if what == "frame":
        made = fan_looks.frame(look, size)
    elif what == "all":
        made = np.stack([fan_looks.rotor(look, size, a)
                         for a in fan_looks.angles(look, fan.POSITIONS)])
    else:
        made = fan_looks.rotor(look, size, struct.unpack("<d", struct.pack("<Q", what))[0])
    np.save(f"{sys.argv[2]}/{n}.npy", made)
"""
# what the looks' rotors and frames were drawn as, by (look, size, "all" / "frame" / an angle's
# bits): numpy's on a processor without AVX-512 (as _moon_face's)
_LOOKS_DRAWN: dict[tuple[str, int, Any], Any] = {}
# (the rotors' and frames' sizes the fans of FAN_SIDES have)
_LOOK_SIZES = (6, 7, 10, 13, 18, 27, 37, 38, 39, 45, 52, 60, 79, 120)
_FRAME_SIZES = (8, 9, 12, 13, 17, 23, 34, 47, 48, 49, 57, 66, 76, 100, 151)


def _draw_looks(asked: list[tuple[str, int, Any]]) -> None:
    """fan_looks.rotor (at all its positions: "all"; at an angle: its bits) and fan_looks.frame
    ("frame") of each asked for that is not drawn yet, as numpy draws them on a processor
    without AVX-512 (its own exp for float32, the C library's for the rest, as _moon_face has
    it), in a process of its own told to leave those features unused: Rust's are those."""
    import numpy as np

    asked = [key for key in dict.fromkeys(asked) if key not in _LOOKS_DRAWN]
    if not asked:
        return
    env = dict(os.environ)
    if _avx512():
        env["NPY_DISABLE_CPU_FEATURES"] = " ".join(_avx512())
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "asked.json").write_text(json.dumps(asked), encoding="utf-8")
        subprocess.run([sys.executable, "-c", _DRAWN_LOOKS, str(Path(folder) / "asked.json"),
                        folder], env=env, check=True, timeout=900)  # fmt: skip
        for n, key in enumerate(asked):
            _LOOKS_DRAWN[key] = np.load(Path(folder) / f"{n}.npy")


def _rotor_drawn(look_: Any, size: int, angle: float) -> Any:
    """fan_looks.rotor as Rust's (_draw_looks): the look at all its positions drawn at once (and
    at the sizes the fans here have, when one is first asked for)."""
    from libre_panel.render import fan, fan_looks

    angles = fan_looks.angles(look_, fan.POSITIONS)
    if angle not in angles:
        key = (look_.name, size, _bits(angle))
        _draw_looks([key])
        return _LOOKS_DRAWN[key].copy()
    if (look_.name, size, "all") not in _LOOKS_DRAWN:
        _draw_looks([(look_.name, k, "all") for k in (size, *_LOOK_SIZES)])
    return _LOOKS_DRAWN[(look_.name, size, "all")][angles.index(angle)].copy()


def _frame_drawn(look_: Any, size: int) -> Any:
    """fan_looks.frame as Rust's (_draw_looks; at the sizes the fans here have, when one is
    first asked for)."""
    if (look_.name, size, "frame") not in _LOOKS_DRAWN:
        _draw_looks([(look_.name, s, "frame") for s in (size, *_FRAME_SIZES)])
    return _LOOKS_DRAWN[(look_.name, size, "frame")].copy()


@pytest.fixture(autouse=True)
def _fan_looks_as_rust_s(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every rotor and square frame of a look Python draws here drawn as Rust's (none kept from
    before)."""
    from libre_panel.render import fan, fan_looks

    monkeypatch.setattr(fan_looks, "rotor", _rotor_drawn)
    monkeypatch.setattr(fan_looks, "frame", _frame_drawn)
    for kept in (fan._layers, fan._frames, fan._images):
        kept.clear()
    yield
    for kept in (fan._layers, fan._frames, fan._images):
        kept.clear()


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
                     now.microsecond, now.fold]}  # fmt: skip
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
    if make is _symbol_or_more and rng.random() < 0.6:  # (the sun's, rain's, snow's, the ground's)
        palette.update({name: _hex(rng) for name in rng.sample(SYMBOL_ROLES, 3)})
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


def _python_frame(
    folder: Path,
    snapshot: Snapshot,
    dither: bool,
    look: str | None = None,
    now: float = 1000.0,
    place: tuple[float, float] | None = None,
    fans: Any = None,
) -> dict[str, Any]:
    try:
        theme = load_theme(folder)
    except ThemeError as error:
        return {"error": str(error)}
    renderer = Renderer(theme, preview=True, dither=dither)
    if look is not None:  # (the look of the symbols and the moon the person chose)
        renderer.symbols = look
    renderer.place = place  # (your place, for the earth, as the loop sets it)
    if fans is not None:  # (what the person chose for the fans, as the app sets it)
        renderer.fan_choices = fans
    lists._RAN.clear()  # (each frame as a process's first: no fan seen running before it)
    try:
        frame, boxes = renderer.render(snapshot, now=now)
    except Exception as error:  # (a frame Python cannot make: what lies over a widget failed)
        return {"failed": f"{type(error).__name__}: {error}"}
    return {"frame": frame, "boxes": boxes, "warnings": list(renderer.warnings)}


# what Rust does not draw where it does not know the computer's zone (a TZ the C library reads
# by rules of its own: test_without_the_computer_s_zone_rust_leaves_out_what_needs_it)
ZONE_NEEDED = ("another place's time without the computer's own time zone",
               "the earth without the computer's own time zone",
               "slideshows without the computer's own time zone")  # fmt: skip
# (what the frames here may leave out: the slideshow patterns Rust does not match without case
# yet, on Windows; none elsewhere)
NOT_HERE = ("slideshow patterns of other than ASCII on Windows",) if sys.platform == "win32" else ()


def _compare_frames(
    tmp_path: Path,
    seed: int,
    count: int,
    make: Any = None,
    snapshot_of: Any = None,
    language: str = "en",
    undefined: tuple[str, ...] = (),
    looks: tuple[str, ...] = (),
    when: Any = None,
    place_of: Any = None,
    globes: bool = False,
    left_out: list[tuple[int, str]] | None = None,
    files: Any = None,
    fans_of: Any = None,
) -> int:
    """Frames of `count` random themes (of what `make` makes) drawn by both, compared to the
    bit, and their boxes; how many were drawn. Rust may leave out those it says are `undefined`:
    where what Python draws is left to the processor. Each in one of `looks` (the person's look
    of the symbols and the moon), where there are any; each at a time `when` tells, where it
    is given; your place where `place_of` gives one; Python's globes as it makes them with the
    C library's float32 functions where they are drawn (`globes`: _exact_globes). What Rust
    does not draw here (NOT_HERE) it may leave out too; each it left out and why, in
    `left_out` where it is given. The theme's own files where `files` makes them; what the
    person chose for the fans where `fans_of` gives it."""
    rng = random.Random(seed)
    cases, asked = [], []
    try:
        i18n.set_language(language)
        for n in range(count):
            folder = tmp_path / f"t{n}"
            _frame_theme(rng, folder, make)
            if files is not None:
                files(rng, folder)
            given, snapshot = (snapshot_of or _frame_snapshot)(rng)
            dither = rng.random() < 0.3
            cases.append({"folder": str(folder), "snapshot": given, "dither": dither})
            look = rng.choice(looks) if looks else None
            if look is not None:
                cases[-1]["symbols"] = look
            now = 1000.0 if when is None else when(rng)
            cases[-1]["now"] = now
            place = None if place_of is None else place_of(rng)
            if place is not None:
                cases[-1]["place"] = list(place)
            fans = None if fans_of is None else fans_of(rng)
            if fans is not None:
                cases[-1]["fan_choices"] = _choices_given(fans)
            asked.append((folder, snapshot, dither, look, now, place, fans))

        def draw() -> list[dict[str, Any]]:
            return [_python_frame(*frame) for frame in asked]

        expected = _exact_globes(tmp_path, draw) if globes else draw()
    finally:
        i18n.set_language("en")
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": language, "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if "error" in want:
            assert "error" in got, n
            continue
        if "failed" in want:  # (Python's frame fails: Rust makes none)
            assert "unported" in got, (n, got, want)
            continue
        if got.get("unported") in undefined + NOT_HERE:
            if left_out is not None:
                left_out.append((n, got["unported"]))
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
    # (a screen, the panel's look and a finish Rust does not draw yet: the facets' rings and
    # graphs; Rust says it does not draw them yet, rather than a frame without them)
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
            if rng.random() < 0.5:
                finished = {**_gauge(rng, 99, (64, 48)), "finish": "facet"}
            else:
                finished = {"type": "graph", "id": "g99", "x": 4, "y": 4, "w": 40, "h": 20,
                            "finish": "facet"}  # fmt: skip
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
    # widget's sensor; a graph without a course published whole and with fewer than two
    # values in its history)
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
        history: dict[str, list[float]] = {}
        if kind == "graph":  # (its history of no value, one or more; now and then a course)
            key = widget.get("sensor", "cpu.load")
            if rng.random() < 0.8:
                history[key] = [rng.uniform(0, 100) for _ in range(rng.choice([0, 1, 2, 5]))]
            if rng.random() < 0.25:
                readings[key] = rng.choice(["[40, 61.5, 52]", "[7]", "[1, 2", "61.5"])
        now = datetime(2026, 11, 7, 12, 30, 5)
        snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()},
                            history=history, now=now)  # fmt: skip
        given = {"readings": {k: _typed(v) for k, v in readings.items()}, "images": {},
                 "history": {k: [_typed(v) for v in vs] for k, vs in history.items()},
                 "now": [2026, 11, 7, 12, 30, 5, 0]}  # fmt: skip
        renderer = Renderer(theme, preview=True)
        [shown] = [w for w in renderer.widgets if w["id"] == "left"]
        gone = renderer._missing(shown, snapshot)
        left_out += gone
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        assert got.get("sha256") == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n
        assert got["boxes"] == want["boxes"], n
    assert left_out > 20 and len(cases) > 60


COUNTDOWN_TARGETS = [
    "12-24", "2026-12-24", "2026-12-24 18:00", "2026-12-24T18:00", " 12-24 ", "12-24\n", "1-1",
    "01-01", "2-29", "2-30", "4-31", "2027-2-29", "2028-2-29 12:00", "13-1", "0-5", "12-0",
    "12-32", "11-07", "11-7", "2026-11-07", "11-07 19:30", "11-07 12:00", "11-07 12:30",
    "2025-12-24", "2026-11-06 23:59", "0000-12-24", "0000-1-1", "9999-12-31 23:59",
    "\u0661\u0662-\u0662\u0664",
    "\uff12\uff10\uff12\uff16-\uff11\uff12-\uff12\uff14 \uff10\uff18:\uff10\uff15",
    "12-24 24:00", "12-24 23:60", "12-24 7:05", "12-24 07:5", "12-24  18:00", "12-24 18:00:00",
    "12-24t18:00", "\u00a012-24\u3000", "123-4-5", "12345-1-1", "", "x", "12/24", "12-24 18",
    "2026-1-1 0:00", "12-24 \n", "\t2026-12-24\t", "12-24\u0085", "12-24\u200b", "2-28 23:00",
    "12-31 23:59", "1-1 0:00", "11-08 12:30", "11-09 12:31", "11-10 12:31", "11-10 12:30",
    "11-07 13:35", "11-07 12:35", "2026-11-10 12:30", "11-08", "12-24\x1f", "\x1e12-24 \x1d",
]  # fmt: skip
COUNTDOWN_NOWS = [
    datetime(2026, 11, 7, 12, 30, 5, 250000), datetime(2026, 12, 24, 0, 0, 0, 0),
    datetime(2026, 12, 31, 23, 59, 59, 999999), datetime(2028, 2, 29, 10, 0, 0, 1),
    datetime(2026, 2, 28, 23, 0, 0, 0), datetime(2026, 12, 24, 18, 0, 0, 0),
    datetime(9999, 12, 31, 12, 0, 0, 7),
]  # fmt: skip
COUNTDOWN_PARTS = ["number", "unit", "clock", "text", "days", "date", "nope"]


def _target(rng: random.Random) -> str:
    """A countdown's target, as one may write it, or nearly."""
    year = rng.choice(["", "", "2026-", "2027-", "2025-", "0000-", "9999-", "2028-"])
    month, day = rng.randrange(14), rng.randrange(33)
    text = f"{year}{month:02d}-{day:02d}" if rng.random() < 0.5 else f"{year}{month}-{day}"
    if rng.random() < 0.5:
        sep = rng.choice([" ", "T", " ", "  ", "t"])
        text += f"{sep}{rng.randrange(25)}:{rng.randrange(61):02d}"
    return rng.choice(["", " ", "\t"]) + text + rng.choice(["", " ", "\n"])


def _moment(now: datetime) -> list[int]:
    return [now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond,
            now.fold]  # fmt: skip


def _countdown_expected(text: str, part: str, now: datetime, done: str) -> Any:
    try:
        return countdown_text(text, part, now, done)
    except ValueError as error:
        return {"error": str(error)}


def test_countdown_texts_as_countdown_py_tells_them() -> None:
    # (the regular expression's digits and white space as Python's re reads them, the 28th for
    # a day the month lacks, the year 0 as this year's, every year's next one, to the
    # microsecond, in the panel's language; the date it counts to as format_date writes it)
    rng = random.Random(0xC0D)
    targets = COUNTDOWN_TARGETS + [_target(rng) for _ in range(400)]
    cases, expected = [], []
    try:
        for n, text in enumerate(targets):
            for now in COUNTDOWN_NOWS:
                parts = COUNTDOWN_PARTS
                if n >= len(COUNTDOWN_TARGETS):
                    parts = rng.sample(COUNTDOWN_PARTS, 3)
                for part in parts:
                    done = rng.choice(["", "", "Party!"])
                    language = rng.choice(["en", "de", "ja", "ru"])
                    i18n.set_language(language)
                    expected.append(_countdown_expected(text, part, now, done))
                    cases.append({"text": text, "part": part, "now": _moment(now), "done": done,
                                  "language": language})  # fmt: skip
    finally:
        i18n.set_language("en")
    [ours] = run([{"op": "countdown_text", "locale": str(i18n.LOCALE_DIR), "cases": cases}])
    for n, (got, want) in enumerate(zip(ours, expected, strict=True)):
        assert got == want, (n, cases[n])
    said = {w for w in expected if isinstance(w, str)}
    assert {"--", "0", "", "Party!", "1", "00:00:00"} <= said
    assert any(isinstance(w, dict) and "error" in w for w in expected)
    dates = [w for c, w in zip(cases, expected, strict=True) if c["part"] == "date"]
    assert sum(isinstance(w, str) and w != "--" for w in dates) > 300


WEATHER_FIELDS = ["temperature", "apparent_temperature", "humidity", "wind_speed", "description",
                  "code"]  # fmt: skip


def _weather_or_countdown(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """The weather's readings and countdowns, as texts; cards and texts between them."""
    pick = rng.random()
    if pick < 0.3:
        return _card_or_text(rng, i, size)
    widget = _text(rng, i, size)
    for key in ("text", "uppercase", "translate"):
        widget.pop(key, None)
    if pick < 0.65:
        widget.update(type="weather", field=rng.choice(WEATHER_FIELDS))
        if rng.random() < 0.6:
            widget["format"] = rng.choice(["{value:.0f}{unit}", "{value}", "{value:.1f} {unit}",
                                           "{nope}", "{label}", "{value:>6}"])  # fmt: skip
        if rng.random() < 0.3:
            widget["fallback"] = rng.choice(["--", "", "n/a"])
        return widget
    widget.update(type="countdown", target=rng.choice(COUNTDOWN_TARGETS[:28]),
                  part=rng.choice(["number", "unit", "clock", "text", "days"]))  # fmt: skip
    if rng.random() < 0.3:
        widget["done"] = rng.choice(["", "Party!", "Grüße"])
    return widget


def _weather_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The weather's readings now, at a moment the countdowns count from."""
    values = {"weather.temperature": [21.5, -3.25, 0.0, 35, None, "warm"],
              "weather.apparent_temperature": [19.75, -10.0], "weather.humidity": [40, 100, 0],
              "weather.wind_speed": [12.3, 0, 99.95],
              "weather.description": ["Overcast", "Partly cloudy", "Grüße", ""],
              "weather.code": [61, 3, 0, 95]}  # fmt: skip
    units = {"weather.temperature": "°C", "weather.apparent_temperature": "°C",
             "weather.humidity": "%", "weather.wind_speed": "km/h"}  # fmt: skip
    readings: dict[str, Reading] = {}
    given: dict[str, Any] = {}
    for key, choices in values.items():
        if rng.random() < 0.75:
            value, label = rng.choice(choices), rng.choice(["", "Weather"])
            readings[key] = Reading(key, value, unit=units.get(key, ""), label=label)
            given[key] = {"v": _typed(value), "unit": units.get(key, ""), "label": label}
    now = rng.choice(COUNTDOWN_NOWS[:6])
    snapshot = Snapshot(readings=readings, now=now)
    return {"readings": given, "images": {}, "now": _moment(now)}, snapshot


def test_frames_of_the_weather_and_countdowns_as_python_draws_them(tmp_path: Path) -> None:
    assert _compare_frames(tmp_path, 0x3EA7, 160, _weather_or_countdown, _weather_snapshot) > 120


def test_frames_of_the_weather_and_countdowns_in_german_as_python_draws_them(
    tmp_path: Path,
) -> None:
    # (the weather's description, the countdown's units and "Heute" in the panel's language)
    assert _compare_frames(tmp_path, 0xDE, 120, _weather_or_countdown, _weather_snapshot, "de") > 90


CLOCK_FORMATS = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%-I:%M", "%A", "%a %d %b", "%d %B %Y",
                 "%A, %d. %B", "%B %d", "%d.%m.%Y", "%x", "%X", "%c", "%Y-%m-%d", "%j",
                 "%U/%W/%V", "%%", "%-d.%-m.", "%e %b", "%k:%M", "%l:%M %P", "%#d", "",
                 "Week %V", "%A %d %B %Y %H:%M:%S %p", "%G-W%V-%u", "%y", "%C", "%h", "%D",
                 "%F", "%R", "%T", "%r", "%n%H", "%t%M", "%Z%z", "%^A", "%#A", "%-d %B",
                 "%b %-d", "%:z", "%", "%Q", "%Ey", "Grüße %H", "%c %c %c %c %c"]  # fmt: skip


def _clock(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """The time in a format of strftime's, in capitals now and then."""
    clock = _text(rng, i, size)
    for key in ("text", "uppercase", "translate"):
        clock.pop(key, None)
    clock.update(type="clock", format=rng.choice(CLOCK_FORMATS))
    if rng.random() < 0.3:
        clock["uppercase"] = True
    if rng.random() < 0.3:
        clock["digits"] = rng.choice(["jump", "roll", "fade"])
        clock["digits_seconds"] = rng.choice([0.42, 1, 0.1, 2, 0])
    return clock


def _clock_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _clock(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def _clock_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """A moment of the years round now, to the microsecond."""
    year, month = rng.randrange(1990, 2070), rng.randrange(1, 13)
    day = rng.randrange(1, calendar.monthrange(year, month)[1] + 1)
    now = datetime(year, month, day, rng.randrange(24), rng.randrange(60), rng.randrange(60),
                   rng.randrange(10**6))  # fmt: skip
    return {"readings": {}, "images": {}, "now": _moment(now)}, Snapshot(readings={}, now=now)


def test_frames_of_clocks_as_python_draws_them(tmp_path: Path) -> None:
    # (strftime's codes as the C library writes them: glibc's on Linux, the Universal CRT's on
    # Windows, where a code it does not know makes the clock "--:--")
    assert _compare_frames(tmp_path, 0xC10C, 160, _clock_or_more, _clock_snapshot) > 120


def test_frames_of_clocks_in_german_as_python_draws_them(tmp_path: Path) -> None:
    # (the days' and the months' names in German, "7. November")
    assert _compare_frames(tmp_path, 0xDE0C, 120, _clock_or_more, _clock_snapshot, "de") > 90


# (the zones a clock shows: of every kind of offset and summer time, none of the name, a key
# Python refuses, none)
CLOCK_ZONES = ["Asia/Tokyo", "UTC", "Europe/Berlin", "America/St_Johns", "Australia/Lord_Howe",
               "Pacific/Chatham", "Asia/Kolkata", "America/New_York", "Etc/GMT+12",
               "Pacific/Kiritimati", "America/Santiago", "Nowhere/Else", "../etc", ""]  # fmt: skip


def _zoned(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A clock or a clock face in a time zone (CLOCK_ZONES), or a card."""
    if rng.random() < 0.15:
        return _card_or_text(rng, i, size)
    widget = _clock(rng, i, size) if rng.random() < 0.6 else _analog(rng, i, size)
    widget["timezone"] = rng.choice(CLOCK_ZONES)
    return widget


def _local_changes(years: range) -> list[tuple[int, int, int]]:
    """The moments the computer's clocks change in `years` (UTC seconds), as the C library has
    them, and the offsets before and after."""

    def offset(t: int) -> int:
        return time.localtime(t).tm_gmtoff

    found = []
    t = int(datetime(years.start, 1, 1).timestamp())
    end = int(datetime(years.stop, 1, 1).timestamp())
    while t < end:
        if offset(t) != offset(t + 86400):
            lo, hi = t, t + 86400
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if offset(mid) == offset(t) else (lo, mid)
            found.append((hi, offset(lo), offset(hi)))
        t += 86400
    return found


def _near_changes(changes: list[tuple[int, int, int]]) -> Any:
    """Snapshots at moments about the computer's clocks' changes (in a gap, twice in a fold,
    either fold) and at random."""

    def snapshot_of(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
        if not changes or rng.random() < 0.3:
            given, snapshot = _clock_snapshot(rng)
            now = snapshot.now.replace(fold=rng.randrange(2))
        else:
            at, before, after = rng.choice(changes)
            wall = at + rng.choice([before, after]) + rng.choice([-3600, -1, 0, 1, 1800, 3599])
            now = (datetime(1970, 1, 1) + timedelta(seconds=wall)).replace(
                microsecond=rng.choice([0, 1, 999999]), fold=rng.randrange(2)
            )
        return _clock_moment(now)

    return snapshot_of


@pytest.fixture
def local_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """The computer's zone made another (TZ, the C library's tzset), for this process and
    lp-check's; as it was afterwards."""

    def set_zone(tz: str) -> None:
        monkeypatch.setenv("TZ", tz)
        time.tzset()

    yield set_zone
    monkeypatch.undo()
    time.tzset()


# (the computer's zones: of summer time, north and south, half an hour's, 14 hours east, a TZ
# string)
LOCAL_ZONES = ["Europe/Berlin", "Australia/Lord_Howe", "America/St_Johns", "Pacific/Kiritimati",
               "CET-1CEST,M3.5.0,M10.5.0/3"]  # fmt: skip


@pytest.mark.skipif(sys.platform == "win32", reason="the computer's zone set by TZ and tzset")
def test_clocks_in_another_time_zone_as_python_shows_them(tmp_path: Path,
                                                         local_zone: Any) -> None:  # fmt: skip
    # (the frame's time in another zone: through the computer's zone (its fold where the clocks
    # go back, the gap where they go on) into the other, as datetime's astimezone and zoneinfo
    # take it; the computer's own time where no zone has the name, warned as Python warns)
    drawn = 0
    for n, tz in enumerate(LOCAL_ZONES):
        local_zone(tz)
        changes = _local_changes(range(2024, 2028))
        snapshot_of = _near_changes(changes)
        folder = tmp_path / f"z{n}"
        folder.mkdir()
        drawn += _compare_frames(folder, 0x2E0 + n, 40, _zoned, snapshot_of)
    assert drawn > 150, drawn


def _zone_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if w.startswith("unknown time zone")]


def test_clocks_of_no_zone_warn_as_python_warns(tmp_path: Path) -> None:
    # (a zone of no such name, a key Python refuses: the computer's own time, and the warning;
    # none for a zone that is, nor for none)
    rng = random.Random(0x7E)
    cases, expected = [], []
    for n, zone in enumerate(["Nowhere/Else", "../etc", "Asia/Tokyo", "Europe/Berlin/", "UTC", ""]):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        for kind in (_clock, _analog):
            clock = kind(rng, 90 + len(data["widgets"]), (64, 48))
            clock["timezone"] = zone
            data["widgets"].append(clock)
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _clock_snapshot(rng)
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n
        assert _zone_warnings(got["warnings"]) == _zone_warnings(want["warnings"]), n
    assert sum(len(_zone_warnings(w["warnings"])) for w in expected) == 6


def _zoned_or_earth(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    pick = rng.random()
    if pick < 0.1:
        return _earth(rng, i, size)
    if pick < 0.2:
        return _slideshow(rng, i, size)
    return _zoned(rng, i, size) if pick < 0.45 else _card_or_text(rng, i, size)


@pytest.mark.skipif(sys.platform == "win32", reason="the C runtime's zone: Rust's is the same")
def test_without_the_computer_s_zone_rust_leaves_out_what_needs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (where Rust does not know the computer's zone (a TZ the C library reads by a rule of its
    # own, summer time without its rules): a globe, another place's time and a slideshow by the
    # clock Rust says it does not draw, never a frame otherwise than Python's; the computer's
    # own time, a zone of no such name and a slideshow of one picture it draws)
    monkeypatch.setenv("TZ", "CET-1CEST")
    time.tzset()
    left_out: list[tuple[int, str]] = []
    try:
        drawn = _compare_frames(tmp_path, 0x2E9, 80, _zoned_or_earth, _clock_snapshot,
                                undefined=(*ZONE_NEEDED, UNREAD), left_out=left_out,
                                files=_theme_files)  # fmt: skip
    finally:
        monkeypatch.undo()
        time.tzset()
    reasons = {why for _, why in left_out}
    assert set(ZONE_NEEDED) <= reasons <= {*ZONE_NEEDED, *NOT_HERE, UNREAD}, left_out
    assert all(why != UNREAD or _unread_in(tmp_path / f"t{n}") for n, why in left_out)
    assert drawn > 10 and len(left_out) > 30, (drawn, len(left_out))


# (the sides of a clock face's box: none, less than the least face, odd and even, about where
# its marks between the hours show (110 px), the almanacs')
ANALOG_SIDES = [-4, 0, 7, 8, 9, 31, 64, 109, 110, 111, 160, 201, 396, 402]


def _analog(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A clock face: of every size, square or not, with its face and marks or without them,
    its hands in the theme's colours or their own, its second hand shown or not, ticking,
    gliding or going round (a still frame shows them alike); another place's time now and
    then."""
    w, h = size
    widget: dict[str, Any] = {
        "type": "analog", "id": f"a{i}", "x": rng.randrange(-60, w), "y": rng.randrange(-60, h),
        "w": rng.choice(ANALOG_SIDES), "h": rng.choice(ANALOG_SIDES),
    }  # fmt: skip
    if rng.random() < 0.5:
        widget["h"] = widget["w"]
    for key in ("color", "color2"):
        if rng.random() < 0.6:
            widget[key] = rng.choice([_colour(rng), "@accent", "@text2", _hex(rng, True)])
    for key in ("face", "marks"):
        if rng.random() < 0.6:
            widget[key] = rng.choice([None, _colour(rng), "@surface", "@line", _hex(rng, True)])
    if rng.random() < 0.4:
        widget["seconds"] = rng.random() < 0.5
    if rng.random() < 0.4:
        widget["hand"] = rng.choice(["tick", "glide", "sweep"])
    if rng.random() < 0.05:
        widget["timezone"] = rng.choice(["Asia/Tokyo", "UTC", "Nowhere/Else"])
    if rng.random() < 0.1:
        widget["opacity"] = rng.choice([0.5, 0.0, 0.25])
    return widget


def _analog_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _analog(rng, i, size) if rng.random() < 0.75 else _card_or_text(rng, i, size)


# (the hands' edges: midnight and noon, a second and a minute before them, a minute's last)
ANALOG_MOMENTS = [{"hour": 0, "minute": 0, "second": 0},
                  {"hour": 11, "minute": 59, "second": 59},
                  {"hour": 12, "minute": 0, "second": 0},
                  {"hour": 23, "minute": 59, "second": 59},
                  {"minute": 59}, {"second": 59}, {"second": 0},
                  {"hour": 6, "minute": 30}]  # fmt: skip


def _analog_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """A moment of the years round now, to the microsecond, at the hands' edges now and then."""
    given, snapshot = _clock_snapshot(rng)
    if rng.random() < 0.3:
        now = snapshot.now.replace(**rng.choice(ANALOG_MOMENTS))
        given, snapshot = _clock_moment(now)
    return given, snapshot


def _clock_moment(now: datetime) -> tuple[dict[str, Any], Snapshot]:
    return {"readings": {}, "images": {}, "now": _moment(now)}, Snapshot(readings={}, now=now)


def test_frames_of_analog_clocks_as_python_draws_them(tmp_path: Path) -> None:
    # (the face, its rim and its marks (those between the hours from 110 px), the hour and the
    # minute hands, the second hand and the dot in the middle, drawn three times as large and
    # reduced as the renderer's _canvas and _down make them)
    drawn = _compare_frames(tmp_path, 0xA7A1, 300, _analog_or_more, _analog_snapshot)
    assert drawn > 200, drawn


def test_analog_clocks_frame_after_frame_as_python_keeps_them(tmp_path: Path) -> None:
    # (one renderer drawing its clocks frame after frame: the same moment again, a part of a
    # second, a second, a minute, an hour or half a day later: each face drawn anew where a
    # hand moved, kept where none did)
    rng = random.Random(0xA7A4)
    cases, expected = [], []
    for n in range(40):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _analog)
        now = _analog_snapshot(rng)[1].now
        steps: list[tuple[Snapshot, float]] = []
        given = []
        for _ in range(rng.randrange(2, 8)):
            readings, snapshot = _clock_moment(now)
            steps.append((snapshot, 1000.0))
            given.append({"snapshot": readings, "now": 1000.0})
            now += timedelta(seconds=rng.choice([0, 0.4, 1, 1, 5, 59, 60, 3600, 43200]))
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        try:
            expected.append(_python_steps(folder, steps))
        except ThemeError:
            expected.append(None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                continue
            if g.get("unported") in NOT_HERE:
                continue
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            drawn += 1
    assert drawn > 130, drawn


def test_analog_clocks_at_every_size(tmp_path: Path) -> None:
    # (every side from 1 to 140 px, square and not, at times all round the dial: the marks
    # between the hours from 110 px, the widths of the hands and the marks rounded where they
    # cross a half)
    rng = random.Random(0xA7A2)
    cases, expected = [], []
    for side in range(1, 141):
        folder = tmp_path / f"t{side}"
        folder.mkdir()
        widgets = [{"type": "analog", "id": "a0", "x": 4, "y": 4, "w": side, "h": side,
                    "face": "#111821", "marks": "#8a97a8", "color2": "#22d3ee"},
                   {"type": "analog", "id": "a1", "x": 150, "y": 4, "w": side,
                    "h": side + side % 7, "face": None, "marks": "#ffffff80",
                    "seconds": side % 2 == 0}]  # fmt: skip
        data = {"format": THEME_FORMAT, "name": "clocks", "display": {"width": 300, "height": 160},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _analog_snapshot(rng)
        cases.append({"folder": str(folder), "snapshot": given, "dither": side % 5 == 0})
        expected.append(_python_frame(folder, snapshot, side % 5 == 0))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert got.get("sha256") == hashlib.sha256(want["frame"].tobytes()).hexdigest(), case
        assert got["boxes"] == want["boxes"], case["folder"]


def test_analog_clocks_with_glow_and_shadow_within_a_level(tmp_path: Path) -> None:
    # (a clock casts its light as a card does: blurred in floating point, now and then a pixel
    # a level apart; the almanacs' faces among them)
    import numpy as np

    looks = [{"glow": 0.15, "face": "#111821", "marks": "#5b6474"},
             {"shadow": "#000000a0", "shadow_blur": 4, "opacity": 0.8},
             {"glow": 1, "glow_radius": 6, "face": None, "seconds": False}]  # fmt: skip
    rng = random.Random(0xA7A3)
    cases, expected = [], []
    for n, side in enumerate([24, 64, 110, 160, 240, 396]):
        folder = tmp_path / f"t{side}"
        folder.mkdir()
        widgets = [{"type": "analog", "id": f"a{i}", "x": 30 + i * (side + 40), "y": 30,
                    "w": side, "h": side, **look} for i, look in enumerate(looks)]  # fmt: skip
        data = {"format": THEME_FORMAT, "name": "clocks",
                "display": {"width": 3 * side + 140, "height": side + 60},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _analog_snapshot(rng)
        dither = n % 2 == 1
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither, "bytes": True})
        expected.append(_python_frame(folder, snapshot, dither))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    differ = total = 0
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert "frame" in want and "frame" in got, (case["folder"], got, want)
        apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
        assert apart.max() <= 1, case["folder"]
        differ += int((apart.max(axis=2) > 0).sum())
        total += apart.shape[0] * apart.shape[1]
        assert got["boxes"] == want["boxes"], case["folder"]
    assert differ <= total // 10000, differ


BAR_SENSORS = ["cpu.load", "gpu.temp", "net.down", "audio.volume", "mem.used"]
BAR_VALUES = [0, 1, 50, 50.5, 99.99, 100, 150, -5, 33.3333, 1e9, 0.0001, None, "x", True, 7,
              12.5, 66.66666, 0.5]  # fmt: skip


def _bar(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A reading as a bar: its range and scale, its colours, segments rising as levels on a
    mixer, thresholds, a mark where a second reading stands, a dot at its head."""
    w, h = size
    bar: dict[str, Any] = {"type": "bar", "id": f"b{i}", "x": rng.randrange(-20, w),
                           "y": rng.randrange(-20, h), "w": rng.choice([1, 2, 7, 40, 120, 300]),
                           "h": rng.choice([1, 3, 8, 16, 40]), "sensor": rng.choice(BAR_SENSORS),
                           "color": _colour(rng), "radius": rng.choice([0, 2, 4, 8, 50]),
                           "direction": rng.choice(["right", "left", "up", "down"])}  # fmt: skip
    if rng.random() < 0.4:
        bar["min"], bar["max"] = rng.choice([(0, 100), (-50, 50), (10, 10), (0, 1e6), (100, 0),
                                             (0, 1), (0.5, 99.5)])  # fmt: skip
    if rng.random() < 0.3:
        bar["scale"] = rng.choice(["sqrt", "log"])
    if rng.random() < 0.4:
        bar["color2"] = _colour(rng)
    if rng.random() < 0.3:
        bar["background"] = rng.choice([None, _colour(rng), "@accent"])
    if rng.random() < 0.4:
        bar["segments"] = rng.choice([1, 3, 10, 24])
        bar["segment_gap"] = rng.choice([0, 1, 2, 5])
        if rng.random() < 0.5:
            bar["rising"] = rng.choice([0.2, 0.5, 1, 0])
    if rng.random() < 0.4:
        thresholds = [0, 20, 50, 80, 100, -5, 50.5]
        bar["color_rules"] = [
            {"above": rng.choice(thresholds), "color": _colour(rng)}
            for _ in range(rng.randrange(1, 4))
        ]
    if rng.random() < 0.3:
        bar["mark"] = rng.choice(BAR_SENSORS)
        bar["mark_color"] = _colour(rng)
        bar["mark_width"] = rng.choice([1, 2, 5, 400])
    if rng.random() < 0.3:
        bar["end_dot"] = rng.choice([1, 2.5, 4, 0.4])
    if rng.random() < 0.2:
        bar["opacity"] = rng.choice([0.5, 0.0])
    return bar


def _bar_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _bar(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def _bar_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The readings the bars stand at, and their marks."""
    readings: dict[str, Reading] = {}
    given: dict[str, Any] = {}
    for key in BAR_SENSORS:
        if rng.random() < 0.85:
            value = rng.choice(BAR_VALUES)
            readings[key] = Reading(key, value)
            given[key] = _typed(value)
    return {"readings": given, "images": {}}, Snapshot(readings=readings)


def test_frames_of_bars_as_python_draws_them(tmp_path: Path) -> None:
    # (where the reading stands on a linear, square-root or logarithmic scale, in steps of the
    # supersampled grid; the track, the filled part in one colour, a gradient across the whole
    # track or a threshold's; segments, the last lit one partly, rising as levels on a mixer;
    # a mark where a second reading stands, a dot at the head)
    assert _compare_frames(tmp_path, 0xBA2, 200, _bar_or_more, _bar_snapshot) > 150


def test_frames_of_bars_with_glow_within_a_level(tmp_path: Path) -> None:
    # (the glow round a bar and round its dot: blurred in floating point, as a card's is, so
    # now and then a pixel a level apart)
    import numpy as np

    rng = random.Random(0xB61)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _bar)
        for widget in data["widgets"]:
            widget["glow"] = rng.choice([0.3, 0.7, 1.0, 1])
            widget["glow_radius"] = rng.choice([2, 4, 6, 10, 20])
            if rng.random() < 0.6:
                widget["end_dot"] = rng.choice([1, 2.5, 4])
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _bar_snapshot(rng)
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


GAUGE_ANGLES = [
    (135, 405), (90, 450), (180, 360), (0, 360), (-90, 270), (405, 135), (0, 0), (10.5, 300.25),
    (0, 720), (270, -90), (-45, 45), (150, 390.5), (-200, 160), (0, 3600), (359.5, 0.25),
]  # fmt: skip
GAUGE_THICKNESS = [1, 2, 4, 8, 12, 20, 60, 0, -3, 1000]


def _gauge(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A reading as a ring: its range and scale, its sweep, thickness and caps, its colours (a
    gradient along the scale), its track and the ticks across it, thresholds; now and then one
    of no size or less (Python leaves it out where it cannot draw it)."""
    w, h = size
    d = rng.choice([1, 2, 5, 12, 30, 48, 80, 120, 200, 0, -4])
    gauge: dict[str, Any] = {"type": "gauge", "id": f"g{i}", "x": rng.randrange(-20, w),
                             "y": rng.randrange(-20, h), "w": d,
                             "h": d if rng.random() < 0.7 else rng.choice([1, 9, 40, 130]),
                             "sensor": rng.choice(BAR_SENSORS), "color": _colour(rng),
                             "thickness": rng.choice(GAUGE_THICKNESS)}  # fmt: skip
    if rng.random() < 0.7:
        gauge["start_angle"], gauge["end_angle"] = rng.choice(GAUGE_ANGLES)
    if rng.random() < 0.4:
        gauge["min"], gauge["max"] = rng.choice([(0, 100), (-50, 50), (10, 10), (0, 1e6),
                                                 (100, 0), (0, 1), (0.5, 99.5)])  # fmt: skip
    if rng.random() < 0.3:
        gauge["scale"] = rng.choice(["sqrt", "log"])
    if rng.random() < 0.4:
        gauge["color2"] = _colour(rng)
    if rng.random() < 0.4:
        gauge["background"] = rng.choice([None, _colour(rng), "@accent"])
    if rng.random() < 0.4:
        gauge["cap"] = "flat"
    if rng.random() < 0.4:
        gauge["ticks"] = rng.choice([1, 4, 12, 0, -2])
        if rng.random() < 0.5:
            gauge["tick_color"] = rng.choice([None, _colour(rng)])
    if rng.random() < 0.3:
        thresholds = [0, 20, 50, 80, 100, -5, 50.5]
        gauge["color_rules"] = [
            {"above": rng.choice(thresholds), "color": _colour(rng)}
            for _ in range(rng.randrange(1, 4))
        ]
    if rng.random() < 0.2:
        gauge["opacity"] = rng.choice([0.5, 0.0])
    return gauge


def _gauge_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _gauge(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def test_frames_of_gauges_as_python_draws_them(tmp_path: Path) -> None:
    # (where the reading stands on the ring, in steps of the supersampled grid along its arc;
    # the track and the ticks across it; the value's arc, its caps round or flat, in one
    # colour, a gradient along the scale or a threshold's colour)
    assert _compare_frames(tmp_path, 0x6A6E, 200, _gauge_or_more, _bar_snapshot) > 150


def test_frames_of_gauges_with_glow_within_a_level(tmp_path: Path) -> None:
    # (the glow round a ring: blurred in floating point, as a card's is, so now and then a
    # pixel a level apart)
    import numpy as np

    rng = random.Random(0x6A61)
    cases, expected = [], []
    for n in range(50):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _gauge)
        for widget in data["widgets"]:
            widget["glow"] = rng.choice([0.3, 0.7, 1.0, 1])
            widget["glow_radius"] = rng.choice([2, 4, 6, 10, 20])
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _bar_snapshot(rng)
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
    assert drawn > 40
    assert differ <= total // 10000


def test_numbers_a_float_does_not_hold_are_not_drawn_here_yet(tmp_path: Path) -> None:
    # (Python counts with an int of more than 53 bits exactly, where a float rounds it: Rust
    # says it does not draw such a ring or bar yet, rather than one a step off)
    rng = random.Random(0xF10A7)
    cases = []
    for n, (make, key) in enumerate([(_gauge, "max"), (_gauge, "start_angle"), (_bar, "min"),
                                     (_gauge, "end_angle"), (_bar, "max")]):  # fmt: skip
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        widget = make(rng, 99, (64, 48))
        widget[key] = 2**53 + 1
        data["widgets"].append(widget)
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, _ = _bar_snapshot(rng)
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    unported = "numbers a float does not hold as they are"
    assert [got.get("unported") for got in ours["frames"]] == [unported] * 5


def _calendar(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """The month as a grid of days: its size, the font and its size, the colours of the days,
    of today's dot and of the weekdays' names, the week starting on Monday or Sunday."""
    w, h = size
    month: dict[str, Any] = {"type": "calendar", "id": f"k{i}", "x": rng.randrange(-20, w),
                             "y": rng.randrange(-20, h),
                             "w": rng.choice([0, 14, 60, 140, 280, 400]),
                             "h": rng.choice([0, 14, 50, 120, 200, 300]),
                             "font_size": rng.choice([16, 6, 30, 1, 100]),
                             "color": _colour(rng), "color2": _colour(rng),
                             "muted": _colour(rng)}  # fmt: skip
    if rng.random() < 0.5:
        month["first_day"] = "sunday"
    if rng.random() < 0.5:
        month["font"] = rng.choice(FONTS)
    if rng.random() < 0.2:
        month["opacity"] = rng.choice([0.5, 0.0])
    return month


def _calendar_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _calendar(rng, i, size) if rng.random() < 0.6 else _card_or_text(rng, i, size)


def _calendar_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """A day of the years round now, now and then one of the first week of the year 1 or of the
    last of 9999 (whose Sunday Python cannot name)."""
    pick = rng.random()
    if pick < 0.05:
        now = datetime(1, 1, rng.randrange(1, 8), 12)
    elif pick < 0.1:
        now = datetime(9999, 12, rng.randrange(24, 32), 12)
    else:
        return _clock_snapshot(rng)
    return {"readings": {}, "images": {}, "now": _moment(now)}, Snapshot(readings={}, now=now)


def test_frames_of_calendars_as_python_draws_them(tmp_path: Path) -> None:
    # (the weekdays' names cut to two letters, the days of the month below them in their
    # cells, today on a dot drawn four times larger and reduced, its number dark or light)
    assert _compare_frames(tmp_path, 0xCA1, 160, _calendar_or_more, _calendar_snapshot) > 120


def test_frames_of_calendars_in_other_languages_as_python_draws_them(tmp_path: Path) -> None:
    # (the weekdays' names of the language, cut to two letters: "Mo", "周一", "月", "pn", "Пн")
    for n, language in enumerate(["de", "zh", "ja", "pl", "ru"]):
        folder = tmp_path / language
        folder.mkdir()
        drawn = _compare_frames(folder, 0xCA10 + n, 40, _calendar, _calendar_snapshot, language)
        assert drawn > 30, language


AGENT_STATES = ["working", "waiting", "idle", "off", "busy", "", 3, None, "Working"]


def _agent(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """The sign of an AI agent at work: its size, its colours (at work, waiting, off), the
    reading of its state."""
    w, h = size
    sign: dict[str, Any] = {"type": "agent", "id": f"a{i}", "x": rng.randrange(-20, w),
                            "y": rng.randrange(-20, h),
                            "size": rng.choice([1, 8, 9, 20, 33, 64, 130]),
                            "color": _colour(rng), "color2": _colour(rng),
                            "muted": _colour(rng)}  # fmt: skip
    if rng.random() < 0.3:
        sign["sensor"] = rng.choice(["ai.codex.state", "ai.claude.state"])
    if rng.random() < 0.2:
        sign["opacity"] = rng.choice([0.5, 0.0])
    return sign


def _agent_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _agent(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def _agent_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The states the agents' readings say (some of them none Python knows, or none)."""
    readings: dict[str, Reading] = {}
    given: dict[str, Any] = {}
    for key in ("ai.claude.state", "ai.codex.state"):
        if rng.random() < 0.9:
            value = rng.choice(AGENT_STATES)
            readings[key] = Reading(key, value)
            given[key] = _typed(value)
    return {"readings": given, "images": {}}, Snapshot(readings=readings)


def test_frames_of_agents_signs_as_python_draws_them(tmp_path: Path) -> None:
    # (at work three lights on a faint ring, waiting the ring in its colour with a light in its
    # middle, idle a light resting at its top, off the ring faint; any other state is off)
    assert _compare_frames(tmp_path, 0xA6E, 160, _agent_or_more, _agent_snapshot) > 120


def _sunpath(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """The sun's way over the sky: its size and thickness, the sun's colour and the day's
    start's, the horizon's and the moon's."""
    w, h = size
    way: dict[str, Any] = {"type": "sunpath", "id": f"s{i}", "x": rng.randrange(-20, w),
                           "y": rng.randrange(-20, h),
                           "w": rng.choice([0, 8, 30, 77, 160, 300]),
                           "h": rng.choice([0, 6, 20, 44, 120]),
                           "thickness": rng.choice([2, 0.5, 1, 3.7, 8, -1, 20]),
                           "color": _colour(rng), "background": _colour(rng),
                           "night": _colour(rng)}  # fmt: skip
    if rng.random() < 0.4:
        way["color2"] = _colour(rng)
    if rng.random() < 0.2:
        way["sensor"] = "sun.other"
    if rng.random() < 0.2:
        way["opacity"] = rng.choice([0.5, 0.0])
    return way


def _sunpath_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _sunpath(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


SUN_PROGRESS = [0, 0.5, 1, 0.25, 0.731, 0.001, 0.999, 1.2, -0.1, "0.4", " 0.7 ", "x", "nan",
                True, 2**60]  # fmt: skip


def _sunpath_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """Where the sun is on its way (or what is no such place), whether it is up."""
    readings: dict[str, Reading] = {}
    given: dict[str, Any] = {}
    progress = rng.choice([*SUN_PROGRESS, rng.random(), rng.random(), None])
    up = rng.choice([None, None, 0, 1, 1.0, True, "1", 0.5])
    for key, value in (("sun.progress", progress), ("sun.up", up), ("sun.other", rng.random())):
        if value is not None:
            readings[key] = Reading(key, value)
            given[key] = _typed(value)
    return {"readings": given, "images": {}}, Snapshot(readings=readings)


def test_frames_of_the_sun_s_way_as_python_draws_them(tmp_path: Path) -> None:
    # (the horizon fading at its ends, the day's way dotted; by day the part gone from the
    # day's start's colour to the sun's in its own soft light, the sun glowing where it stands,
    # in numpy's float32; by night the moon's crescent; nothing where the sun's times are not
    # known)
    assert _compare_frames(tmp_path, 0x50A, 200, _sunpath_or_more, _sunpath_snapshot) > 150


def test_a_calendar_s_dot_as_python_rounds_its_side(tmp_path: Path) -> None:
    # (October 2026 in six rows of 37.5 px: today's dot 34.5 px across, which round() makes
    # 34, half to even, where rounding half away from zero would make it 35)
    def month(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
        return {**_calendar(rng, i, size), "w": 280, "h": 225, "first_day": "monday"}

    def october(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
        now = datetime(2026, 10, rng.randrange(1, 32), 12)
        return {"readings": {}, "images": {}, "now": _moment(now)}, Snapshot(readings={}, now=now)

    assert _compare_frames(tmp_path, 0xD07, 12, month, october) > 8


THEMES = Path(__file__).parent.parent / "src" / "libre_panel" / "themes"


def _soft_light(folder: Path) -> bool:
    """Whether a theme's frame holds light blurred in floating point (a glow, a shadow, the lit
    finish's): numpy multiplies by the blur's matrices in float32, and a processor's library
    adds them up in its own order."""
    renderer = Renderer(load_theme(folder), preview=True)
    return any(w.get("glow") or w.get("shadow") or w.get("finish") == "lit"
               for w in renderer.widgets)  # fmt: skip


def test_the_app_s_own_themes_as_python_draws_them(tmp_path: Path) -> None:
    # (every theme the app brings, with the demo's readings at a fixed moment: the same to the
    # bit where Rust draws all a frame holds, a pixel now and then a level apart where its light
    # is blurred in floating point, else Rust says what it does not draw yet; how many it draws
    # is the measure of what is done)
    import dataclasses

    import numpy as np

    from libre_panel.sensors.demo import demo_snapshot

    snapshot = dataclasses.replace(demo_snapshot(fixed_time=1_760_000_000.0),
                                   now=datetime(2026, 11, 7, 18, 5, 9, 250))  # fmt: skip
    # (each reading with the one it repeats under a friendlier key: a list shows it once)
    readings = {key: {"v": _typed(r.value), "unit": r.unit, "label": r.label, "origin": r.origin}
                for key, r in snapshot.readings.items()}  # fmt: skip
    folders = sorted(p.parent for p in THEMES.glob("*/theme.json"))
    # (the histories the themes' graphs draw, their last numbers: none shows more of them)
    graphs = [w for f in folders for w in Renderer(load_theme(f), preview=True).widgets
              if w["type"] == "graph"]  # fmt: skip
    assert max(w["history"] for w in graphs) <= 400
    drawn_from = sorted({w["sensor"] for w in graphs} & set(snapshot.history))
    history = {key: [_typed(float(v)) for v in snapshot.history[key][-400:]] for key in drawn_from}
    given = {"readings": readings, "images": {k: image(v) for k, v in snapshot.images.items()},
             "history": history, "now": _moment(snapshot.now)}  # fmt: skip
    soft = {f for f in folders if _soft_light(f)}
    cases = [{"folder": str(f), "snapshot": given, "dither": False, "bytes": f in soft}
             for f in folders]  # fmt: skip
    expected = _exact_globes(tmp_path, lambda: [_python_frame(f, snapshot, False) for f in folders])
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = []
    for folder, got, want in zip(folders, ours["frames"], expected, strict=True):
        if "unported" in got:
            continue
        assert "frame" in want, (folder.name, got, want.get("error"))
        if folder in soft:  # (its light blurred in floating point: within a level)
            apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
            assert apart.max() <= 1, folder.name
            assert (apart.max(axis=2) > 0).sum() <= apart.shape[0] * apart.shape[1] // 10000
        else:
            sha = hashlib.sha256(want["frame"].tobytes()).hexdigest()
            assert got.get("sha256") == sha, folder.name
        assert got["boxes"] == want["boxes"], folder.name
        drawn.append(folder.name)
    assert len(folders) > 90
    # (Windows' least from before Rust knew the computer's zone there, when Terra's waited)
    assert len(drawn) >= (93 if sys.platform == "win32" else 99), drawn


def _lit(
    rng: random.Random, i: int, size: tuple[int, int], shadowed: bool = False
) -> dict[str, Any]:
    """Texts and readings lit (their glow light added over them and around them, their letters
    faded), in an area or not; clocks and line icons the lit finish leaves flat (`shadowed`:
    with the renderer's own glow round those, and shadows)."""
    w, h = size
    pick = rng.random()
    if pick < 0.4:
        widget = _text(rng, i, size)
    elif pick < 0.7:
        widget = _metric(rng, i, size)
    elif pick < 0.8:
        widget = _clock(rng, i, size)
    elif pick < 0.9:
        widget = _icon(rng, i, size)
        if widget["icon"] in WEATHER_ICONS:  # (the finish's own: not drawn by Rust yet)
            widget["icon"] = rng.choice([n for n in icon_set.ICON_NAMES if n not in WEATHER_ICONS])
    else:
        return _card_or_text(rng, i, size)
    widget["finish"] = "lit"
    if widget["type"] in ("text", "metric") or shadowed:
        widget["glow"] = rng.choice([0, 0.25, 0.6, 1, 1.0])
        widget["glow_radius"] = rng.choice([0, 1, 3, 10, 24])
    widget["opacity"] = rng.choice([1, 1.0, 0.999, 0.5, 0.0, 0.998])
    if rng.random() < 0.3:
        widget["glow_area"] = [rng.randrange(-20, w), rng.randrange(-20, h),
                               rng.randrange(1, w + 40), rng.randrange(1, h + 40)]  # fmt: skip
    elif rng.random() < 0.1:  # (from far off to inside the panel: ints past a machine's)
        far = rng.choice([10**30, 2**63, 2**64 + 7])
        widget["glow_area"] = [-far, rng.randrange(-20, h), far + rng.randrange(1, w + 40), far]
    if shadowed and rng.random() < 0.6:
        widget["shadow"] = _hex(rng, alpha=rng.random() < 0.5)
        widget["shadow_blur"] = rng.choice([0, 2, 4, 6, 12])
        widget["shadow_offset"] = rng.choice([0, 3, -4, 8])
    return widget


def _lit_shadowed(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _lit(rng, i, size, shadowed=True)


def _lit_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """Readings of every kind, at a moment of the years round now."""
    given, snapshot = _metric_snapshot(rng)
    moment, at = _clock_snapshot(rng)
    return {**given, "now": moment["now"]}, Snapshot(readings=snapshot.readings, now=at.now)


def test_frames_of_lit_texts_and_readings_as_python_draws_them(tmp_path: Path) -> None:
    # (a lit text's glow is light: the text in its colours on black, blurred, in whole levels,
    # added over it and around it; its letters faded by its opacity, not its light; the lit
    # finish leaves clocks and line icons flat)
    assert _compare_frames(tmp_path, 0x117, 200, _lit, _lit_snapshot) > 150


def test_the_lit_finish_s_icons_as_python_draws_them(tmp_path: Path) -> None:
    # (the lit finish draws the weather's icons as their symbols, from 16 px on; every other
    # icon, and a smaller one, it leaves a line icon)
    names = sorted({*icon_set.ICON_NAMES, *WEATHER_ICONS, "link"})
    cases, expected = [], []
    now = Snapshot(readings={}, now=datetime(2026, 11, 7, 18, 5, 9))
    for n, (name, size) in enumerate((name, size) for name in names for size in (15, 32)):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        icon = {"type": "icon", "id": "i", "x": 4, "y": 4, "icon": name, "size": size,
                "color": "#4ad", "finish": "lit"}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "lit", "display": {"width": 40, "height": 40},
            "widgets": [icon]}), encoding="utf-8")  # fmt: skip
        cases.append({"folder": str(folder), "snapshot": {"readings": {}, "images": {},
                      "now": [2026, 11, 7, 18, 5, 9, 0]}, "dither": False})  # fmt: skip
        expected.append(_python_frame(folder, now, False)["frame"])
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert got.get("sha256") == hashlib.sha256(want.tobytes()).hexdigest(), case["folder"]


def test_a_lit_text_s_light_in_float32_as_numpy_works_it_out(tmp_path: Path) -> None:
    # (its strength made float32 before it scales the blurred text: one a hair below 1 is 1
    # there, and the light's levels whole levels of that)
    rng = random.Random(0x1F32)

    def lit_text(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
        text = _text(rng, i, size)
        text.update(finish="lit", glow=rng.choice([0.99999998, 0.3, 0.7, 0.1, 0.999999]),
                    glow_radius=rng.choice([1, 2, 6]), color="#ffffff")  # fmt: skip
        return text

    assert _compare_frames(tmp_path, rng.randrange(1 << 30), 60, lit_text, _lit_snapshot) > 45


FLAT_FINISHES = [("lit", "clock"), ("lit", "weather"), ("lit", "countdown"), ("lit", "rect"),
                 ("lit", "calendar"), ("facet", "icon"), ("acrylic", "bar"), ("flat", "text"),
                 ("flat", "metric"), ("lit", "agent"), ("facet", "sunpath")]  # fmt: skip
LETTERED_FINISHES = [(finish, kind) for finish in ("facet", "acrylic")
                     for kind in ("text", "metric", "clock", "weather", "countdown")]  # fmt: skip
OWN_FINISHES = [("lit", "bar"), ("facet", "bar"), ("lit", "gauge"), ("lit", "downloads")]
SYMBOL_FINISHES = [("lit", "symbol"), ("lit", "icon")]
GRAPH_FINISHES = [("lit", "graph")]
UNDRAWN_FINISHES = [("facet", "gauge"), ("facet", "graph")]
# a download the lit card shows (the state the downloads service publishes)
DOWNLOADING = json.dumps({"phase": "downloading", "bytes": 3e8, "total": 1e9, "rate": 2.5e6,
                          "remaining_s": 280, "name": "a.iso",
                          "history": [1e6, 2e6, 3e6, 2.5e6]})  # fmt: skip


def test_each_finish_rust_does_not_draw_yet_is_refused(tmp_path: Path) -> None:
    # (a finish that takes the widget and that Rust does not draw: the whole frame refused,
    # rather than a frame without it; one that does not take it: the widget drawn flat; the
    # facets' letters and bars, the lit bar and ring: their own, the lit ones' halos within a
    # level; a lit symbol and a lit weather icon: frosted glass, to the bit; a lit graph with
    # no history: its ground alone, to the bit; a lit download card: its light in its groove)
    import numpy as np

    makers = {"clock": _clock, "weather": _weather_or_countdown, "rect": _rect, "bar": _bar,
              "gauge": _gauge, "calendar": _calendar, "icon": _icon, "agent": _agent,
              "sunpath": _sunpath, "symbol": _symbol}  # fmt: skip
    rng = random.Random(0xF1)
    cases, wanted = [], []
    pairs = [*FLAT_FINISHES, *LETTERED_FINISHES, *OWN_FINISHES, *SYMBOL_FINISHES,
             *GRAPH_FINISHES, *UNDRAWN_FINISHES]  # fmt: skip
    for n, (finish, kind) in enumerate(pairs):
        widget = makers.get(kind, _text)(rng, 0, (64, 48))
        if kind in ("weather", "countdown"):
            widget = {**_text(rng, 0, (64, 48)), "type": kind, "field": "temperature",
                      "target": "2030-01-01", "part": "number"}  # fmt: skip
            widget.pop("text", None)
        widget.update(type=kind, finish=finish)
        if (finish, kind) in FLAT_FINISHES:  # (faded as the renderer fades it, not as lit does)
            widget.update(opacity=0.5, glow=0)
        if (finish, kind) in LETTERED_FINISHES:
            widget.update(font_size=40, max_width=300)
        if (finish, kind) in OWN_FINISHES:  # (raised, or its light, in a groove; the lit glow)
            widget.update(x=8, y=12, w=40, h=10, glow=0.8 if finish == "lit" else 0, glow_radius=6,
                          background="@surface")  # fmt: skip
            if kind == "gauge":
                widget.update(y=4, h=40, thickness=8)
            if kind == "downloads":
                widget.update(x=0, y=0, w=64, h=48)
        if (finish, kind) in SYMBOL_FINISHES:  # (a weather's symbol, large enough to be one)
            widget.update(x=8, y=4, size=40, opacity=1.0, icon="weather", sensor="weather.code")
            widget.pop("hide_if_missing", None)
            if kind == "symbol":
                widget.pop("icon")
        if kind in ("graph", "symbol", "downloads"):
            widget.pop("text", None)
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "finishes", "display": {"width": 64, "height": 48},
            "palette": {"accent": "#2ee6d6", "surface": "#223344", "line": "#3155573b",
                        "text2": "#a93214"}, "widgets": [widget]}), encoding="utf-8")  # fmt: skip
        readings = {"downloads": DOWNLOADING} if kind == "downloads" else {}
        given = {k: _typed(v) for k, v in readings.items()}
        cases.append({"folder": str(folder), "snapshot": {"readings": given, "images": {},
                      "now": [2026, 11, 7, 18, 5, 9, 0]}, "dither": False})  # fmt: skip
        if finish == "lit" and (finish, kind) in OWN_FINISHES:
            cases[-1]["bytes"] = True
        wanted.append("finishes" if (finish, kind) in UNDRAWN_FINISHES else None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    got = [frame.get("unported") for frame in ours["frames"]]
    for case, frame, pair in zip(cases, ours["frames"], pairs, strict=True):
        if pair in UNDRAWN_FINISHES:
            continue
        state = {"downloads": Reading("downloads", DOWNLOADING)} if pair[1] == "downloads" else {}
        now = Snapshot(readings=state, now=datetime(2026, 11, 7, 18, 5, 9))
        want = _python_frame(Path(case["folder"]), now, False)["frame"]  # (drawn as Python does)
        if pair in OWN_FINISHES and pair[0] == "lit":
            apart = np.abs(np.asarray(picture(frame["frame"]), int) - np.asarray(want, int))
            assert apart.max() <= 1, pair
        else:
            assert frame.get("sha256") == hashlib.sha256(want.tobytes()).hexdigest(), pair
    wrong = [(pair, g) for pair, g, w in zip(pairs, got, wanted, strict=True) if g != w]
    refused = [frame["error"] for frame in ours["frames"] if "error" in frame]
    assert not refused, refused
    assert not wrong, wrong


def test_frames_of_lit_texts_with_shadows_within_a_level(tmp_path: Path) -> None:
    # (a lit text's shadow is the renderer's, under the letters, and their light is worked out
    # of both; the shadow and the glow round a clock or an icon the finish leaves flat are
    # blurred in floating point, as a card's are: now and then a pixel a level apart)
    import numpy as np

    rng = random.Random(0x5AD)
    cases, expected = [], []
    for n in range(80):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _lit_shadowed)
        given, snapshot = _lit_snapshot(rng)
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
    assert drawn > 60
    assert differ <= total // 10000


FACET_CHARS = "0123456789:AaBgjQW%°.,-–@iIl€ß+ ?"


def test_facet_letters_as_python_makes_them() -> None:
    # (a letter's bevel worked out of its coverage drawn four times larger: the distance to its
    # edge, the slope of that, the normals averaged down, lit and, of metal, mirroring the sky
    # and the ground; in numpy's float32, in numpy's order; then coloured)
    import numpy as np

    from libre_panel.fonts.fallback import truetype
    from libre_panel.render import facets

    rng = random.Random(0xFAC)
    cases, wanted = [], []
    for n in range(260):
        path = str(fonts.builtin_font_path(rng.choice(FONTS[2:])))
        size = rng.choice([20, 21, 27, 28, 29, 33, 40, 51, 52, 53, 64, 96] + [130, 220] * (n < 12))
        ch, quarter = rng.choice(FACET_CHARS), rng.randrange(4)
        style = rng.choice(["metal", "acrylic"])
        fill = (rng.randrange(256), rng.randrange(256), rng.randrange(256),
                rng.choice([255, 255, 128, 77, 0]))  # fmt: skip
        sky = tuple(rng.randrange(256) for _ in range(3))
        ground = tuple(rng.randrange(256) for _ in range(3))
        cases.append({"op": "facet_glyph", "path": path, "size": size, "ch": ch,
                      "quarter": quarter, "style": style, "fill": list(fill),
                      "environment": [*sky, *ground]})  # fmt: skip
        g = facets._glyph_geometry(truetype(path, size), ch, quarter, style)
        if g is None:
            wanted.append(None)
            continue
        patch = facets._coloured(g, fill, (sky, ground))
        wanted.append((g.left, g.top, g.cover, g.terms, np.asarray(patch)))
    blanks = 0
    for n, (got, want) in enumerate(zip(run(cases), wanted, strict=True)):
        if want is None:
            assert got["glyph"] is None, n
            blanks += 1
            continue
        glyph = got["glyph"]
        assert (glyph["left"], glyph["top"]) == want[:2], n
        assert np.array_equal(np.asarray(picture(glyph["cover"])), want[2]), n
        assert np.array_equal(np.asarray(picture(glyph["terms"])), want[3]), (n, cases[n])
        assert np.array_equal(np.asarray(picture(glyph["patch"])), want[4]), n
    assert 0 < blanks < 30


def _lettered(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """Texts, readings, clocks, the weather and countdowns lettered by the facet and acrylic
    finishes (from 20 px on; smaller ones, and texts of more than a line, stay flat)."""
    pick = rng.random()
    if pick < 0.3:
        widget = _text(rng, i, size)
    elif pick < 0.5:
        widget = _metric(rng, i, size)
    elif pick < 0.75:
        widget = _clock(rng, i, size)
    elif pick < 0.9:
        widget = _weather_or_countdown(rng, i, size)
        if widget["type"] not in ("weather", "countdown"):
            return widget
    else:
        return _card_or_text(rng, i, size)
    widget["finish"] = rng.choice(["facet", "acrylic"])
    widget["font_size"] = rng.choice([12, 19, 20, 24, 28, 29, 40, 52, 64, 90])
    return widget


def _lettered_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """Readings of every kind and the weather's, at a moment the clocks and countdowns show."""
    given, snapshot = _metric_snapshot(rng)
    weather, at = _weather_snapshot(rng)
    readings = {**snapshot.readings, **at.readings}
    return ({**given, "readings": {**given["readings"], **weather["readings"]},
             "now": weather["now"]}, Snapshot(readings=readings, now=at.now))  # fmt: skip


def test_frames_of_lettered_texts_as_python_draws_them(tmp_path: Path) -> None:
    # (each letter the finish's own, laid where the renderer lays its letters: at the pen's
    # quarter pixel, over what is laid before it)
    assert _compare_frames(tmp_path, 0xACC, 160, _lettered, _lettered_snapshot) > 120


def test_frames_of_metal_lettering_mirroring_the_theme_s_sky(tmp_path: Path) -> None:
    # (the sky metal mirrors is the palette's accent, else its primary colour, else its cpu
    # colour, a third of the way to white; the Libre look's where it has none of them; the
    # ground below is the background)
    palettes = [{}, {"accent": "#ff2050"}, {"primary": "rgb(10, 200, 30)"}, {"cpu": "#5020ff80"},
                {"primary": "#123456", "cpu": "#654321"}, {"cpu": "red", "accent": "#0ff"},
                {"text": "#ffffff"}]  # fmt: skip
    cases, expected = [], []
    for n, palette in enumerate(palettes):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        text = {"type": "text", "id": "t", "x": 4, "y": 2, "text": "MWo", "font_size": 60,
                "font": "builtin:Barlow-Bold", "color": "#c8ccd4", "finish": "facet"}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "sky", "display": {"width": 140, "height": 80},
            "palette": palette, "background": {"color": ["#000000", "#203040"][n % 2]},
            "widgets": [text]}), encoding="utf-8")  # fmt: skip
        snapshot = Snapshot(readings={})
        cases.append({"folder": str(folder), "snapshot": {"readings": {}, "images": {}},
                      "dither": False})  # fmt: skip
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    frames = [hashlib.sha256(want["frame"].tobytes()).hexdigest() for want in expected]
    assert len(set(frames)) == len(palettes) - 1  # (each sky shows ...)
    assert frames[0] == frames[-1]  # (... and a palette of none of its names has the Libre one)
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        assert got.get("sha256") == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n


def _lit_bar(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """Bars lit: their groove milled into the panel (from 6 px thick to 400), the light of each
    lit span a pill over it, a glow light added over it, in an area or not (one whose corner
    no panel meets, or whose side no machine makes, too); some with a shadow."""
    w, h = size
    bar = _bar(rng, i, size)
    bar["finish"] = "lit"
    bar[rng.choice(["w", "h"])] = rng.choice([2, 5, 6, 9, 14, 24, 400, 401])
    if rng.random() < 0.7:
        bar["glow"] = rng.choice([0, 0.3, 0.6, 1, 1.0])
        bar["glow_radius"] = rng.choice([0, 1, 3, 6, 10, 24])
    if rng.random() < 0.25:
        bar["glow_area"] = [rng.randrange(-20, w), rng.randrange(-20, h),
                            rng.randrange(1, w + 40), rng.randrange(1, h + 40)]  # fmt: skip
    elif rng.random() < 0.08:
        far = rng.choice([10**30, 2**63, 2**64 + 7])
        areas = [
            [far, 0, 50, 50],
            [-far, 0, 50, 50],
            [0, -far, far + 9, 30],
            [-far, 0, far + 9, far],
        ]
        bar["glow_area"] = rng.choice(areas)
    if rng.random() < 0.6:
        bar["background"] = rng.choice([_colour(rng), "@accent", "#16233280"])
    if rng.random() < 0.15:
        bar["shadow"] = _hex(rng, alpha=rng.random() < 0.5)
        bar["shadow_blur"] = rng.choice([0, 2, 4, 6, 12])
        bar["shadow_offset"] = rng.choice([0, 3, -4, 8])
    return bar


def _lit_bar_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _lit_bar(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def test_frames_of_lit_bars_within_a_level(tmp_path: Path) -> None:
    # (a lit bar's light: its core towards white, its halo the coverage blurred in floating
    # point, as a card's glow is, and its core's curve a power numpy takes from SVML on a
    # processor with AVX-512: now and then a pixel a level apart)
    import numpy as np

    rng = random.Random(0x11B)
    cases, expected = [], []
    for n in range(120):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _lit_bar_or_more)
        given, snapshot = _bar_snapshot(rng)
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
    assert drawn > 90
    assert differ <= total // 10000


def test_facet_bars_as_python_makes_them() -> None:
    # (a raised bar in a milled track: each box's shading worked out four times larger at its
    # ends and in one column between, in numpy's float32 and order; one colour or a gradient
    # along the whole track, segments in their colours, filled from either end; none where a
    # bevel does not read or the segments do not fit)
    import numpy as np

    from libre_panel.render import facets

    rng = random.Random(0xFAB)

    def rgba() -> tuple[int, int, int, int]:
        return (rng.randrange(256), rng.randrange(256), rng.randrange(256),
                rng.choice([255, 255, 128, 0]))  # fmt: skip

    cases, wanted = [], []
    for n in range(300):
        thick = rng.choice([5, 6, 7, 9, 12, 16, 24, 40] if n % 10 else [6, 400, 401])
        span = rng.choice([-3, 1, 2, 3, 7, 12, 30, 80, 300])
        direction = rng.choice(["right", "left", "up", "down"])
        w, h = (span, thick) if direction in ("right", "left") else (thick, span)
        frac = rng.choice([0, 1e-9, 0.001, 0.1, 0.25, 0.5, 0.731, 0.999, 1, 1.5, -0.2,
                           rng.random()])  # fmt: skip
        paint = (rgba(), rgba() if rng.random() < 0.4 else None)
        track = rgba() if rng.random() < 0.7 else None
        segments, gap = rng.choice([0, 0, 0, 1, 3, 10, 24]), rng.choice([0, 1, 2, 5])
        if n % 6 == 0:  # (a gradient's middle segments: their colours a half apart, to even)
            paint, segments, gap, frac = (rgba(), rgba()), rng.choice([3, 5]), 1, 1
        cases.append({"op": "facet_bar", "w": w, "h": h, "frac": frac, "direction": direction,
                      "paint": list(paint[0]), "paint2": paint[1] and list(paint[1]),
                      "track": track and list(track), "segments": segments,
                      "gap": gap})  # fmt: skip
        facets._images.clear()  # (a segment's box is kept by its fill in 64ths: none from before)
        layer = facets.bar_layer(w, h, frac, direction, paint, track, segments, gap)
        wanted.append(None if layer is None else np.asarray(layer))
    raised = 0
    for n, (got, want) in enumerate(zip(run(cases), wanted, strict=True)):
        if want is None:
            assert got["layer"] is None, n
            continue
        assert np.array_equal(np.asarray(picture(got["layer"])), want), (n, cases[n])
        raised += 1
    assert raised > 120


def _facet_bar(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """Bars of the facet finish: raised in a milled track (from 6 px thick to 400), flat where
    a bevel does not read."""
    bar = _bar(rng, i, size)
    bar["finish"] = "facet"
    bar[rng.choice(["w", "h"])] = rng.choice([2, 5, 6, 9, 14, 24, 400, 401])
    if rng.random() < 0.6:
        bar["background"] = rng.choice([_colour(rng), "@accent", "#16233280"])
    return bar


def _facet_bar_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _facet_bar(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _unkept_bar_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """_bar_snapshot, each frame from no kept boxes: Python keeps a raised segment's box by its
    fill in 64ths, so after another frame of the same bar it may take one filled a little
    apart."""
    from libre_panel.render import facets

    facets._images.clear()
    return _bar_snapshot(rng)


def test_frames_of_facet_bars_as_python_draws_them(tmp_path: Path) -> None:
    # (a raised bar with straight, bevelled ends in a milled track, in its colour, a gradient
    # or a threshold's; segments; flat where a bevel does not read)
    assert _compare_frames(tmp_path, 0xFA8, 160, _facet_bar_or_more, _unkept_bar_snapshot) > 120


def _lit_gauge(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """Rings lit: the arc of light on the middle of a groove milled into the panel, its ticks
    flat on it; a round one's arc worked out, any other's drawn; flat under 8 px across or
    with a groove under 2."""
    gauge = _gauge(rng, i, size)
    gauge["finish"] = "lit"
    if rng.random() < 0.6:
        gauge["background"] = rng.choice([_colour(rng), "@accent", "#16233280"])
    if rng.random() < 0.1:  # (where the finish begins to draw it)
        gauge["w"] = gauge["h"] = rng.choice([7, 8])
    if rng.random() < 0.1:  # (ticks with no groove under them)
        gauge["background"], gauge["ticks"] = None, rng.choice([4, 12])
    return gauge


def _lit_gauge_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _lit_gauge(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _unkept_ring_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """_bar_snapshot, each frame from no kept lights: Python keeps a closed ring's start by all
    it is made of but its halo, so after a frame of another panel size it may take one of
    another softness."""
    from libre_panel.render import lit

    lit._PICTURES.clear()
    return _bar_snapshot(rng)


def test_frames_of_lit_gauges_within_a_level(tmp_path: Path) -> None:
    # (a round ring's arc worked out from each pixel's distance and angle, the angle numpy's
    # float32 arctan2, which it takes from SVML on a processor with AVX-512, as the groove's;
    # its light's halo blurred in floating point: now and then a pixel a level apart)
    import numpy as np

    rng = random.Random(0x1A6)
    cases, expected = [], []
    for n in range(120):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _lit_gauge_or_more)
        given, snapshot = _unkept_ring_snapshot(rng)
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
    assert drawn > 90
    assert differ <= total // 10000


LIVE_PICTURES = ["media.cover", "game.logo", "media.app_icon"]


def _image(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A picture a source or a service has (an album cover, a game's logo, the playing app's
    icon), fitted into its box (stretched, contained where it says, covering it), blurred into a
    backdrop of its colours, its corners rounded; one the snapshot lacks, a widget with none,
    now and then one of no size or with a slideshow of blank lines."""
    w, h = size
    image: dict[str, Any] = {"type": "image", "id": f"m{i}", "x": rng.randrange(-20, w),
                             "y": rng.randrange(-20, h), "w": rng.choice([0, 1, 7, 40, 64, 120]),
                             "h": rng.choice([0, 1, 9, 40, 64, 90]),
                             "src": rng.choice(["@media.cover", "@game.logo", "@media.app_icon",
                                                "@nothing", ""]),
                             "fit": rng.choice(["stretch", "contain", "cover"])}  # fmt: skip
    if rng.random() < 0.4:
        image["align"] = rng.choice(["left", "center", "right"])
    if rng.random() < 0.35:
        image["blur"] = rng.choice([1, 4, 9, 20, 40])
    if rng.random() < 0.35:
        image["radius"] = rng.choice([1, 4, 12, 50, 400])
    if rng.random() < 0.2:
        image["opacity"] = rng.choice([0.5, 0.0])
    if rng.random() < 0.1:  # (a slideshow of no lines: none)
        image["slides"] = rng.choice(["", "\n", "  \n\t \n"])
    return image


def _image_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _image(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _image_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The pictures the sources give: of any size and shape, opaque, see-through or grey; at a
    moment of the years round now (the picture on show takes its timestamp)."""
    images, given = {}, {}
    for name in LIVE_PICTURES:
        if rng.random() < 0.85:
            size = (rng.choice([1, 3, 16, 64, 300]), rng.choice([1, 5, 16, 64, 240]))
            picture = noise(rng, rng.choice(["RGB", "RGBA", "L"]), size)
            images[name], given[name] = picture, image(picture)
    now = _clock_snapshot(rng)[1].now
    return ({"readings": {}, "images": given, "now": _moment(now)},
            Snapshot(readings={}, images=images, now=now))  # fmt: skip


def test_frames_of_live_pictures_as_python_draws_them(tmp_path: Path) -> None:
    # (a picture a source or a service has, fitted into the widget's box with LANCZOS, blurred
    # into a backdrop at a fraction of its size and drawn up, its corners rounded; nothing for
    # one the snapshot lacks, a clear piece for a widget with none)
    assert _compare_frames(tmp_path, 0x1AA6E, 160, _image_or_more, _image_snapshot) > 120


# -- the theme's own pictures ---------------------------------------------------------------------

# (the theme's still pictures: PNGs of every mode, one turned as its EXIF says; JPEGs turned
# so, in colour and grey (its suffix in capitals); a still GIF; one in a folder below, a hidden
# one, one of a name that is not ASCII)
THEME_STILLS = ["pics/a.png", "pics/b.png", "pics/c.png", "pics/d.png", "pics/e.jpg",
                "pics/F.JPG", "pics/g.gif", "pics/sub/h.png", "pics/.i.png",
                "intl/ä.png"]  # fmt: skip
# (animated ones: a GIF, a WebP, a PNG, a GIF cut short; frames of their own lengths)
THEME_MOVING = ["anim/run.gif", "anim/spin.webp", "anim/wave.png", "anim/cut.gif"]
# (what is no picture to show: not there, a folder; outside the theme, which the theme's picture
# may not name, a slideshow's may)
THEME_NONE = ["pics/none.png", "pics"]
THEME_OUTSIDE = ["../outside.png", "/etc/hostname", "pics/../../outside.png"]
# (files Rust does not read: not a picture, cut short, a picture cut short where Pillow raises
# what Python does not catch)
THEME_UNREAD = ["bad/broken.png", "bad/notes.txt", "bad/half.png", "anim/cut.gif"]
UNREAD = "pictures Rust does not read"
# (how long a frame says it shows, in ms: none, less than the least, a few hundredths, more)
FRAME_MS = [0, 5, 19, 20, 30, 40, 100, 250, 1000]


def _picture_size(rng: random.Random) -> tuple[int, int]:
    return rng.choice([1, 2, 7, 16, 40, 64, 97]), rng.choice([1, 3, 9, 16, 40, 64, 75])


def _turned(rng: random.Random) -> bytes:
    """EXIF of an orientation (1 to 8: upright, mirrored, turned), now and then none."""
    from PIL import Image

    if rng.random() < 0.2:
        return b""
    exif = Image.Exif()
    exif[0x0112] = rng.randrange(1, 9)
    return exif.tobytes()


def _theme_files(rng: random.Random, folder: Path) -> None:
    """The theme's own pictures (THEME_STILLS, THEME_MOVING, THEME_UNREAD), of any size; the
    animated ones of 2 to 5 frames, each to show for its own time; a picture outside the theme
    beside it."""
    (folder / "pics" / "sub").mkdir(parents=True)
    for name in ("anim", "intl", "bad"):
        (folder / name).mkdir()
    pics, anim = folder / "pics", folder / "anim"
    noise(rng, "RGB", _picture_size(rng)).save(pics / "a.png", exif=_turned(rng))
    noise(rng, "RGBA", _picture_size(rng)).save(pics / "b.png")
    noise(rng, "L", _picture_size(rng)).save(pics / "c.png")
    paletted = noise(rng, "RGB", _picture_size(rng)).quantize(rng.choice([2, 16, 256]))
    paletted.save(pics / "d.png", **({"transparency": 0} if rng.random() < 0.5 else {}))
    noise(rng, "RGB", _picture_size(rng)).save(pics / "e.jpg", quality=rng.choice([40, 90]),
                                               exif=_turned(rng))  # fmt: skip
    noise(rng, "L", _picture_size(rng)).save(pics / "F.JPG", "JPEG", exif=_turned(rng))
    noise(rng, "RGB", _picture_size(rng)).quantize(64).save(pics / "g.gif")
    noise(rng, "RGB", _picture_size(rng)).save(pics / "sub" / "h.png")
    noise(rng, "RGBA", _picture_size(rng)).save(pics / ".i.png")
    noise(rng, "RGBA", _picture_size(rng)).save(folder / "intl" / "ä.png")

    def frames(modes: list[str]) -> tuple[list[Any], list[int]]:
        size = _picture_size(rng)
        count = rng.randrange(2, 6)
        return ([noise(rng, rng.choice(modes), size) for _ in range(count)],
                [rng.choice(FRAME_MS) for _ in range(count)])  # fmt: skip

    run, ms = frames(["RGB", "RGBA", "L"])
    run[0].save(anim / "run.gif", save_all=True, append_images=run[1:], duration=ms, loop=0,
                disposal=rng.choice([0, 1, 2]))  # fmt: skip
    spin, ms = frames(["RGB", "RGBA"])
    spin[0].save(anim / "spin.webp", save_all=True, append_images=spin[1:], duration=ms,
                 lossless=rng.random() < 0.5)  # fmt: skip
    wave, ms = frames(["RGB", "RGBA"])
    wave[0].save(anim / "wave.png", save_all=True, append_images=wave[1:], duration=ms)
    cut, ms = frames(["RGB"])
    cut[0].save(anim / "cut.gif", save_all=True, append_images=cut[1:], duration=ms)
    data = (anim / "cut.gif").read_bytes()
    (anim / "cut.gif").write_bytes(data[: len(data) * rng.choice([3, 6, 9]) // 10])
    bad = folder / "bad"
    (bad / "broken.png").write_bytes(rng.randbytes(rng.randrange(1, 200)))
    (bad / "notes.txt").write_text("not a picture", encoding="utf-8")
    half = io.BytesIO()
    noise(rng, "RGB", (40, 30)).save(half, "PNG")
    (bad / "half.png").write_bytes(half.getvalue()[: rng.randrange(60, len(half.getvalue()))])
    if not (folder.parent / "outside.png").exists():
        noise(rng, "RGB", (8, 8)).save(folder.parent / "outside.png")


def _theme_picture(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A picture of the theme's files (or a source's) in an image widget: as _image makes one,
    of the theme's pictures (still, animated, none of them)."""
    widget = _image(rng, i, size)
    pick = rng.random()
    if pick < 0.04:
        widget["src"] = rng.choice(THEME_UNREAD)
    elif pick < 0.06:
        widget["src"] = rng.choice(THEME_OUTSIDE)  # (the theme refused as it is read)
    else:
        widget["src"] = rng.choice(THEME_STILLS * 3 + THEME_MOVING * 2 + THEME_NONE
                                   + ["@media.cover", ""])  # fmt: skip
    widget.pop("slides", None)
    if rng.random() < 0.2:
        widget["w"], widget["h"] = 0, 0  # (as it is)
    if rng.random() < 0.2:
        widget["speed"] = rng.choice([0.1, 0.25, 0.5, 2, 4, 10])
    return widget


def _theme_picture_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _theme_picture(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _unread_in(folder: Path) -> bool:
    """Whether the theme shows a file Rust does not read (THEME_UNREAD), or names one in a
    slideshow."""
    widgets = json.loads((folder / "theme.json").read_text(encoding="utf-8"))["widgets"]
    shown = " ".join(f"{w.get('src', '')} {w.get('slides', '')}" for w in widgets)
    return any(rel in shown for rel in THEME_UNREAD) or "bad/" in shown


def test_frames_of_the_theme_s_pictures_as_python_draws_them(tmp_path: Path) -> None:
    # (a picture of the theme's folder, upright as its EXIF says, in RGBA, fitted into the
    # widget's box with LANCZOS (or as it is), blurred, its corners rounded; an animated one's
    # first frame, finished so; a clear piece of the widget's size for one that is not there,
    # a folder or outside the theme; one Rust does not read it says it does not draw)
    left: list[tuple[int, str]] = []
    drawn = _compare_frames(tmp_path, 0x7E1A, 160, _theme_picture_or_more, _image_snapshot,
                            undefined=(UNREAD,), left_out=left, files=_theme_files)  # fmt: skip
    assert all(why == UNREAD and _unread_in(tmp_path / f"t{n}") for n, why in left), left
    assert drawn > 90 and len(left) > 10, (drawn, len(left))


# (slideshow lines: names, a source's, patterns of a folder's still pictures (by suffix, any
# case, hidden ones, below, above and back, of none), a line of blanks)
SLIDE_STILLS = ["pics/a.png", "pics/none.png", *THEME_OUTSIDE, "@media.cover", "@game.logo",
                "pics/*.png", "pics/*.jpg", "pics/*.JPG", "pics/*.[jJ][pP][gG]", "pics/**/*.png",
                "pics/*", "pics/.*", "pics/?.png", "pics/[a-c].png", "pics/[!a].png",
                "*/sub/*.png", "pics/sub/../*.gif", "pics/*.PNG", "pics/*.txt", "  pics/*.gif  ",
                "nothing/*", "", "bad/*.png", "intl/*"]  # fmt: skip
# (how long each picture shows: less than the least, a second, more, a day, an hour of days)
SLIDE_SECONDS = [-5, 0, 1, 2, 3, 7, 10, 60, 3600, 86400, 10**6]


def _slideshow(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """An image widget showing a slideshow of still pictures, `seconds` each."""
    widget = _image(rng, i, size)
    widget["src"] = rng.choice(["", "pics/b.png", "@media.cover", "pics/e.jpg"])
    lines = rng.sample(SLIDE_STILLS, rng.randrange(1, 5))
    widget["slides"] = rng.choice(["\n", "\r\n", "\n\n"]).join(lines)
    widget["seconds"] = rng.choice(SLIDE_SECONDS)
    return widget


def _slideshow_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _slideshow(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def _slide_moment(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """A moment of the computer's clock round now: at random, or at a picture's turn (a
    second's millionth before it, at it, after it), the pictures the sources give."""
    import dataclasses

    given, snapshot = _image_snapshot(rng)
    t = rng.uniform(631152000, 3155760000)
    if rng.random() < 0.5:
        every = max(1, rng.choice(SLIDE_SECONDS))
        t = (t // every) * every + rng.choice([-1e-6, 0, 1e-6, 0.5])
    now = datetime.fromtimestamp(t)
    return {**given, "now": _moment(now)}, dataclasses.replace(snapshot, now=now)


def test_slideshows_of_still_pictures_as_python_shows_them(tmp_path: Path) -> None:
    # (still pictures take turns by the clock, `seconds` each: the frame's time as a timestamp
    # of the computer's zone, floor-divided as floats are; a folder's pictures as pathlib's glob
    # finds them, sorted; one picture alone shows; of a slideshow with a file Rust does not
    # read, Rust says it does not draw it)
    left: list[tuple[int, str]] = []
    drawn = _compare_frames(tmp_path, 0x511D, 160, _slideshow_or_more, _slide_moment,
                            undefined=(UNREAD,), left_out=left, files=_theme_files)  # fmt: skip
    assert all(why in NOT_HERE or _unread_in(tmp_path / f"t{n}") for n, why in left), left
    # (on Windows fewer: the patterns Rust does not match without case yet, and its least from
    # before Rust knew the computer's zone there, when slideshows of one picture alone were drawn)
    assert drawn > (8 if sys.platform == "win32" else 85), drawn


def test_animated_pictures_frame_after_frame_as_python_shows_them(tmp_path: Path) -> None:
    # (one renderer showing its animated pictures frame after frame: each from its first frame
    # when it first shows, then the frame of the moment at its speed, round and round: the same
    # moment again, a part of a frame, a frame, a round, a minute later; back in time)
    rng = random.Random(0xA41E)
    cases, expected, unread = [], [], []
    for n in range(40):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _theme_picture)
        _theme_files(rng, folder)
        steps: list[tuple[Snapshot, float]] = []
        given = []
        now = rng.uniform(0, 10**6)
        for _ in range(rng.randrange(2, 9)):
            readings, snapshot = _image_snapshot(random.Random(n))
            steps.append((snapshot, now))
            given.append({"snapshot": readings, "now": now})
            now += rng.choice([0, 0.004, 0.02, 0.05, 0.1, 0.3, 1, 2.5, 60, -0.5])
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        unread.append(_unread_in(folder))
        try:
            expected.append(_python_steps(folder, steps))
        except ThemeError:
            expected.append(None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                continue
            if g.get("unported") == UNREAD and unread[n]:
                continue
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            drawn += 1
    assert drawn > 80, drawn


def test_an_animation_of_more_than_its_frames_hold_keeps_every_other(tmp_path: Path) -> None:
    # (an animated picture whose frames, finished, take more than moving.FRAMES_BYTES: every
    # second (third ...) kept, each showing as long as those it stands for; the last kept)
    from PIL import Image

    folder = tmp_path / "big"
    folder.mkdir()
    frames = [Image.new("RGB", (1200, 1100), (n * 9, 255 - n * 9, n * 3)) for n in range(25)]
    frames[0].save(folder / "big.gif", save_all=True, append_images=frames[1:],
                   duration=[30 + 10 * (n % 3) for n in range(25)], loop=0)  # fmt: skip
    widget = {"type": "image", "id": "m0", "x": -500, "y": -400, "w": 0, "h": 0,
              "src": "big.gif"}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "big", "display": {"width": 64, "height": 48},
        "widgets": [widget]}), encoding="utf-8")  # fmt: skip
    moment = datetime(2026, 11, 8, 12, 0)
    steps = [(Snapshot(readings={}, now=moment), 50.0 + 0.07 * k) for k in range(24)]
    want = _python_steps(folder, steps)
    given = [{"snapshot": {"readings": {}, "images": {}, "now": _moment(moment)}, "now": t}
             for _, t in steps]  # fmt: skip
    case = {"folder": str(folder), "dither": False, "steps": given}
    [ours] = run([{"op": "render_frame", "fonts_dir": str(fonts.FONT_DIR), "frames": [case]}])
    got = ours["frames"][0]["steps"]
    shas = [hashlib.sha256(w["frame"].tobytes()).hexdigest() for w in want]
    assert [g["sha256"] for g in got] == shas
    assert 4 < len(set(shas)) < 25  # (frames of their own, fewer than the picture's)


def test_an_animation_at_its_frames_ends_at_any_speed_and_back_in_time(tmp_path: Path) -> None:
    # (an animation of frames of 100, 200 and 300 ms (a round of 0.6 s) at the speeds a widget
    # may ask (less than the least, the least, as made, the most, more), at moments exactly at
    # each frame's end, just before and after, a round later, a minute later, and before it
    # first showed: the frame of the moment as moving.Animation.index finds it)
    from PIL import Image

    moment = datetime(2026, 11, 8, 12, 0)
    times = [0.0, 0.05, 0.1, 0.3, 0.30000000000000004, 0.6, 0.6000000000000001, 0.65, 1.2,
             60.0, -0.05, -0.1, -0.35, -0.6, -1.0]  # fmt: skip
    cases, wanted = [], []
    for n, speed in enumerate([0.1, 0.25, 0.4, 1, 3, 4, 10, -2, 0]):
        folder = tmp_path / f"s{n}"
        folder.mkdir()
        frames = [Image.new("RGB", (8, 8), colour) for colour in ((250, 0, 0), (0, 250, 0),
                                                                     (0, 0, 250))]  # fmt: skip
        frames[0].save(folder / "run.gif", save_all=True, append_images=frames[1:],
                       duration=[100, 200, 300], loop=0)  # fmt: skip
        widget = {"type": "image", "id": "m0", "x": 0, "y": 0, "w": 8, "h": 8,
                  "src": "run.gif", "speed": speed}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "run", "display": {"width": 16, "height": 16},
            "widgets": [widget]}), encoding="utf-8")  # fmt: skip
        steps = [(Snapshot(readings={}, now=moment), t) for t in times]
        given = [{"snapshot": {"readings": {}, "images": {}, "now": _moment(moment)}, "now": t}
                 for t in times]  # fmt: skip
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        wanted.append([hashlib.sha256(w["frame"].tobytes()).hexdigest()
                       for w in _python_steps(folder, steps)])  # fmt: skip
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], wanted, strict=True)):
        assert [g["sha256"] for g in got["steps"]] == want, n
    assert all(len(set(want)) == 3 for want in wanted)  # (each frame shows at some moment)


def _slide_list_theme(rng: random.Random, folder: Path) -> None:
    """A theme of image widgets, one a line of SLIDE_LINES (with its picture now and then); its
    files, links among them where the system makes them (to a picture, to a folder, out of the
    theme, round to a folder above)."""
    _theme_files(rng, folder)
    lines = list(SLIDE_LINES)
    widgets = []
    for i, line in enumerate(lines):
        src = rng.choice(["", "", "pics/a.png", "@media.cover"])
        widgets.append({"type": "image", "id": f"m{i}", "x": 0, "y": 0, "w": 8, "h": 8,
                        "src": src, "slides": line})  # fmt: skip
    widgets.append({"type": "image", "id": "all", "x": 0, "y": 0, "w": 8, "h": 8,
                    "slides": rng.choice(["\n", "\r\n", "\x0b", " "]).join(lines)})  # fmt: skip
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "slides", "display": {"width": 32, "height": 32},
        "widgets": widgets}), encoding="utf-8")  # fmt: skip
    if sys.platform == "win32":  # (links there need a right of their own)
        return
    for link, to in [("pics/link.png", "a.png"), ("pics/linked", "sub"),
                     ("pics/out.png", "../../outside.png"), ("pics/sub/loop", ".."),
                     ("anim/gone.gif", "nowhere.gif")]:  # fmt: skip
        (folder / link).symlink_to(to)


# (slideshow lines as _slides reads them: names, a source's, patterns of every kind of part
# (stars, marks, classes and their negations, hidden names, any case of a suffix), "**" alone,
# twice, below and at the end, ".." up and out of the theme, links, a closing slash, empty and
# dotted parts, patterns pathlib refuses (a root, "**" in a part, also where no folder leads))
SLIDE_LINES = ["pics/a.png", "pics/none.png", "@media.cover", "@", "pics/*.png", "pics/*.jpg",
               "pics/*.JPG", "pics/*.PNG", "pics/*.[jJ][pP][gG]", "pics/*", "pics/*/", "pics/.*",
               "pics/?.png", "pics/[a-c].png", "pics/[!a].png", "pics/[", "pics/[]",
               "pics/**/*.png", "**/*.png", "**", "**/", "**/**/*.gif", "**/sub/**/*.png",
               "**/*/**/*.png",
               "*/h.png", "*/*/h.png", "pics/sub/../*.png", "../*.png", "pics/../../*",
               "../*/pics/*.png", "anim/*", "pics/link*", "pics/linked/*", "pics/*/*.png",
               "pics/sub/loop/*", "pics/sub/loop/sub/*", "./pics/./*.png", "pics//*.png",
               "pics\\*.png", "*.txt", "  pics/*.gif  ", "/etc/*", "pics/a**.png",
               "nowhere/a**b", "**/x**", "pics/*.mp4", "intl/*", "intl/ä.png", "ä*", "*/ä.png",
               "*/Ä.png"]  # fmt: skip


def _python_slides(folder: Path) -> dict[str, Any]:
    """Each image widget's slideshow as _slides finds it (its error where it raises one)."""
    renderer = Renderer(load_theme(folder), preview=True)
    found: dict[str, Any] = {}
    for widget in renderer.widgets:
        if widget["type"] == "image":
            try:
                found[widget["id"]] = renderer._slides(widget)
            except Exception as error:  # noqa: BLE001 (where pathlib refuses the pattern)
                found[widget["id"]] = {"error": str(error)}
    return found


def test_slideshow_pictures_as_python_finds_them(tmp_path: Path) -> None:
    # (the pictures of a slideshow's lines: a name as it stands, a source's, a pattern's a folder's
    # pictures and videos as pathlib's glob (Python 3.12) finds them, sorted as paths are, none
    # outside the theme; where pathlib refuses a pattern, its error)
    rng = random.Random(0x511E)
    cases, expected = [], []
    for n in range(6):
        # (the theme with a name that is no Unicode apart: patterns of the others go there)
        folder = tmp_path / ("odd" if n == 5 else "even") / f"t{n}"
        folder.mkdir(parents=True)
        _slide_list_theme(rng, folder)
        if n == 5 and sys.platform != "win32":  # (a name that is no Unicode)
            with open(os.path.join(os.fsencode(folder / "pics"), b"\xff.png"), "wb") as out:
                noise(rng, "RGB", (4, 4)).save(out, "PNG")
        cases.append({"op": "image_slides", "folder": str(folder),
                      "fonts_dir": str(fonts.FONT_DIR)})  # fmt: skip
        expected.append(_python_slides(folder))
    ours = run(cases)
    lists_found = refused = 0
    for n, (got, want) in enumerate(zip(ours, expected, strict=True)):
        assert set(got["slides"]) == set(want), n
        for key, slides in want.items():
            mine = got["slides"][key]
            if isinstance(mine, dict) and "unported" in mine:
                # (Rust does not read such names yet, nor match without case on Windows all
                # that pathlib matches)
                odd = n == 5 and mine["unported"] == "folders of names that are no Unicode"
                assert odd or mine["unported"] in NOT_HERE, (n, key, mine)
                refused += 1
                continue
            assert mine == slides, (n, key)
            lists_found += isinstance(slides, list) and len(slides) > 1
    assert lists_found > 60, lists_found
    assert refused >= (5 if sys.platform != "win32" else 0), refused


@pytest.mark.skipif(sys.platform == "win32", reason="the computer's zone set by TZ and tzset")
def test_a_picture_where_the_frame_s_time_has_no_timestamp_as_python_shows_it(
    tmp_path: Path, local_zone: Any
) -> None:
    # (a picture alone still takes the frame's timestamp: at the edges of the years 1 and 9999,
    # where the computer's zone moves it past them, Python fails at it and leaves the widget
    # out, as Rust does; where Rust does not know the zone (a TZ the C library reads by a rule
    # of its own), it says it does not draw a picture there, and draws it in the years between)
    rng = random.Random(0x7E1F)
    edge = "pictures at the edges of the years without the computer's own time zone"
    moments = [datetime(1, 1, 1), datetime(1, 1, 1, 12), datetime(1, 1, 2, 23, 59, 59),
               datetime(9999, 12, 31, 23, 59, 59), datetime(9999, 12, 31, 3),
               datetime(9998, 12, 30), datetime(2026, 11, 8, 12)]  # fmt: skip
    looks = [{"src": "pics/a.png"}, {"src": "", "slides": "pics/b.png"},
             {"src": "@media.cover"}, {"src": "anim/run.gif"}]  # fmt: skip
    drawn = refused = 0
    for z, tz in enumerate(["Asia/Tokyo", "America/New_York", "UTC", "CET-1CEST"]):
        local_zone(tz)
        cases, expected = [], []
        for n, (now, look) in enumerate((m, lk) for m in moments for lk in looks):
            folder = tmp_path / f"z{z}t{n}"
            folder.mkdir()
            _theme_files(rng, folder)
            widget = {"type": "image", "id": "m0", "x": 2, "y": 1, "w": 20, "h": 14, **look}
            (folder / "theme.json").write_text(json.dumps({
                "format": THEME_FORMAT, "name": "edges", "display": {"width": 24, "height": 16},
                "widgets": [widget]}), encoding="utf-8")  # fmt: skip
            given, snapshot = _image_snapshot(rng)
            given["now"], snapshot.now = _moment(now), now
            cases.append({"folder": str(folder), "snapshot": given, "dither": False})
            expected.append(_python_frame(folder, snapshot, False))
        [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
        for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
            if got.get("unported") == edge:
                assert tz == "CET-1CEST" and moments[n // len(looks)].year in (1, 9999), n
                refused += 1
                continue
            assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), (tz, n)
            drawn += 1
    assert drawn > 90 and refused == 5 * len(looks), (drawn, refused)


def test_videos_and_pictures_rust_does_not_draw_yet(tmp_path: Path) -> None:
    # (a video, and a slideshow whose turn is a video's: Rust says it does not draw them yet,
    # nor a file it does not read (a picture, a slideshow's, the background's; one named with a
    # NUL, an animation cut short whose first frame is whole), nor a slideshow with one where
    # the turn is another's; an animated picture alone, a slideshow of one, of none there, a
    # slideshow with a picture that moves (its first turn a still's, an animated one's), it
    # draws)
    from PIL import Image

    rng = random.Random(0x7E1D)
    looks = [({"src": "clips/a.mp4"}, "videos"),
             ({"src": "pics/a.png", "slides": "anim/run.gif"}, None),
             ({"src": "", "slides": "pics/b.png\nclips/*"}, None),
             ({"src": "", "slides": "clips/*\npics/b.png"}, "videos"),
             ({"src": "", "slides": "anim/wave.png\npics/c.png"}, None),
             ({"src": "anim/run.gif"}, None), ({"src": "", "slides": "anim/spin.webp"}, None),
             ({"src": "", "slides": "pics/*.gif"}, None), ({"src": "bad/half.png"}, UNREAD),
             ({"src": "anim/cut.gif"}, UNREAD), ({"src": "bad/notes.txt"}, UNREAD),
             ({"src": "pics/a.png", "slides": "bad/broken.png", "seconds": 1, "_at": 2e9}, UNREAD),
             ({"src": "", "slides": "pics/a\u0000.jpg"}, UNREAD),
             ({"src": "pics/a.png", "_ground": "bad/half.png"}, UNREAD),
             ({"src": "anim/cut.gif", "_cut": True}, UNREAD),
             ({"src": "bad/half.png", "slides": "pics/a.png"}, UNREAD),
             ({"src": "", "slides": "bad/broken.png"}, UNREAD),
             ({"src": "pics", "slides": "pics/none.png\n../outside.png"}, None)]  # fmt: skip
    cases, wanted = [], []
    for n, (change, why) in enumerate(looks):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        _theme_files(rng, folder)
        (folder / "clips").mkdir()
        (folder / "clips" / "a.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        if change.get("_cut"):  # (four whole frames, cut short in the last)
            frames = [noise(rng, "RGB", (16, 16)) for _ in range(4)]
            frames[0].save(folder / "anim" / "cut.gif", save_all=True, append_images=frames[1:],
                           duration=50)  # fmt: skip
            data = (folder / "anim" / "cut.gif").read_bytes()
            (folder / "anim" / "cut.gif").write_bytes(data[: len(data) * 9 // 10])
            assert Image.open(folder / "anim" / "cut.gif").size == (16, 16)
        widget = {"type": "image", "id": "m0", "x": 0, "y": 0, "w": 20, "h": 20,
                  **{k: v for k, v in change.items() if not k.startswith("_")}}  # fmt: skip
        data = {"format": THEME_FORMAT, "name": "pictures",
                "display": {"width": 32, "height": 32}, "widgets": [widget]}  # fmt: skip
        if "_ground" in change:
            data["background"] = {"image": change["_ground"]}
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _clock_snapshot(rng)
        if "_at" in change:  # (the turn of the first picture: seconds from the epoch, even)
            given, snapshot = _clock_moment(datetime.fromtimestamp(change["_at"]))
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        # (Python's frames of those Rust leaves out are not asked for: their videos want ffmpeg)
        wanted.append((why, None if why else _python_frame(folder, snapshot, False)))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, (why, want)) in enumerate(zip(ours["frames"], wanted, strict=True)):
        if why is not None:
            assert got.get("unported") == why, (n, got)
            continue
        assert want is not None and "frame" in want, (n, want)
        assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n


SYMBOL_CODES: list[Any] = [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82,
                           71, 73, 75, 77, 85, 86, 95, 96, 99, 4, 100, -1, 2.7, "61", " 3 ", "x",
                           None, True, False, math.nan, math.inf, 10**30, "٣"]  # fmt: skip
SYMBOL_ROLES = ["bg", "base", "text", "tx_1", "warn", "warning", "primary", "cpu"]


def _symbol(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A weather symbol standing: of any kind of weather (a code of each, and what is none), by
    day or night, flat or lit, in either look, small or large."""
    w, h = size
    symbol: dict[str, Any] = {"type": "symbol", "id": f"s{i}", "x": rng.randrange(-20, w),
                              "y": rng.randrange(-20, h),
                              "size": rng.choice([4, 8, 12, 16, 24, 40, 64, 100, 120, 128, 160]),
                              "color": rng.choice([_colour(rng), "@accent", "#ffffff"]),
                              "moving": rng.random() < 0.5}  # fmt: skip
    if rng.random() < 0.6:
        symbol["sensor"] = rng.choice(["weather.code", "weather.hour.3.code", "other.code", "x"])
    if rng.random() < 0.5:
        symbol["pack"] = rng.choice(["auto", "classic", "vivid"])
    if rng.random() < 0.6:
        symbol["finish"] = rng.choice(["lit", "lit", "facet", "flat"])
    if rng.random() < 0.15:
        symbol["opacity"] = rng.choice([0.5, 0.0])
    return symbol


def _lit_weather_icon(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A weather icon in the lit finish: its weather's symbol, lit, from 16 px on; smaller, its
    line icon."""
    icon = _icon(rng, i, size)
    icon.update(icon=rng.choice(["weather", "weather", *WEATHER_ICONS]),
                size=rng.choice([8, 15, 16, 17, 24, 40, 64, 121]), finish="lit")  # fmt: skip
    return icon


def _symbol_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    r = rng.random()
    if r < 0.55:
        return _symbol(rng, i, size)
    return _lit_weather_icon(rng, i, size) if r < 0.85 else _card_or_text(rng, i, size)


def _symbol_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The weather the symbols show, of the hour or of now, at an hour of the day or night."""
    values = {"weather.code": SYMBOL_CODES, "weather.hour.3.code": SYMBOL_CODES,
              "weather.hour.3.day": [True, False, 0, 1, None, "", "0"],
              "weather.is_day": [1, 0, None, "0"], "other.code": SYMBOL_CODES}  # fmt: skip
    readings = {k: rng.choice(v) for k, v in values.items() if rng.random() < 0.8}
    now = datetime(2026, 11, 7, rng.randrange(24), rng.randrange(60), 5)
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()}, now=now)
    moment = [now.year, now.month, now.day, now.hour, now.minute, now.second, 0]
    given = {"readings": {k: _typed(v) for k, v in readings.items()}, "images": {},
             "now": moment}  # fmt: skip
    return given, snapshot


def test_frames_of_weather_symbols_as_python_draws_them(tmp_path: Path) -> None:
    # (a symbol standing, of each kind of weather by day and night, flat or lit as frosted glass,
    # in either look, its colours the widget's and the palette's; a lit weather icon its
    # weather's symbol from 16 px on)
    assert _compare_frames(tmp_path, 0x5D3B, 160, _symbol_or_more, _symbol_snapshot) > 120


def test_sine_and_cosine_as_numpy_works_them_out() -> None:
    # (numpy's float32 sin and cos are its own where it has AVX2 and FMA3: Cody-Waite's
    # reduction to a quarter turn and polynomials, fused; the C library's past 117,435.992 and
    # 71,476.0625; the whole number of quarter turns one fused step, as GCC and MSVC build it)
    import numpy as np

    rng = np.random.default_rng(0x51C0)
    x = np.concatenate([rng.uniform(-4, 4, 60_000), rng.uniform(-200, 200, 60_000),
                        rng.uniform(-1.2e5, 1.2e5, 60_000)]).astype(np.float32)  # fmt: skip
    edges = [0.0, -0.0, 1e-30, -1e-30, math.pi, -math.pi, math.pi / 2, math.pi / 4, 71476.0625,
             71476.07, 117435.992, 117436.0, math.inf, -math.inf, math.nan]  # fmt: skip
    # (where the whole number of quarter turns, its product fused, makes another sine or cosine
    # than unfused)
    fused = [0x42782F92, 0x42CDC63A, 0x435669E9, 0x435CB268, 0x4362FAE7, 0x43694366, 0x43890D4F,
             0x43A2F85A, 0x43CFBCE2, 0x43D60561, 0x43DC4DE0, 0x43E8DEDE]  # fmt: skip
    x = np.concatenate(
        [x, np.array(edges, np.float32), np.array(fused, np.uint32).view(np.float32)]
    )
    for func in ("sin", "cos"):
        with np.errstate(invalid="ignore"):
            want = getattr(np, func)(x)
        [got] = run([{"op": f"np_{func}", "bits": x.view(np.uint32).tolist()}])
        ours = np.array(got["bits"], np.uint32).view(np.float32)
        same = (ours.view(np.uint32) == want.view(np.uint32)) | (np.isnan(ours) & np.isnan(want))
        assert same.all(), (func, x[~same][:5])


def test_exp_as_numpy_works_it_out() -> None:
    # (numpy's float32 exp is its own, never the C library's: a ratio of two polynomials,
    # fused, a few units in the last place off the true value at most)
    import numpy as np

    rng = np.random.default_rng(0xE4F)
    x = np.concatenate([rng.uniform(-110, 90, 150_000), rng.uniform(-1, 1, 30_000),
                        rng.uniform(-104, -87, 20_000)]).astype(np.float32)  # fmt: skip
    edges = [0.0, -0.0, 88.72283935546875, 88.7228, -103.97208404541015625, -103.972, 1e-30,
             -1e-30, 87.3, -87.3, math.inf, -math.inf, math.nan]  # fmt: skip
    # (where the first step of its numerator, fused, gives another number than unfused: a dozen
    # of the 1.1 billion float32s between -104 and 89)
    fused = [0x3EA09998, 0x3EA4B63D, 0x3EB5C2D4, 0x3ED42DD3, 0x3EDD401B, 0x3F80DF72, 0x3F8629C1,
             0x3FD9987E, 0x3FDEE2CD, 0x401928C5, 0x4045854B, 0x4071E1D1]  # fmt: skip
    # (where the whole number of the exponent comes out another, its product and sum one fused
    # step (numpy's build for Windows) or two (for Linux): 84 of the float32s, a dozen here)
    builds = [0x3F851592, 0x410AA123, 0x416E7150, 0x42359AC4, 0x4256E029, 0x428B5295,
              0x42A2E3BC, 0xC073FCE1, 0xC1CA6624, 0xC2145560, 0xC25F3183, 0xC29506D2]  # fmt: skip
    x = np.concatenate([x, np.array(edges, np.float32),
                        np.array(fused + builds, np.uint32).view(np.float32)])  # fmt: skip
    with np.errstate(over="ignore", under="ignore"):
        want = np.exp(x)
    [got] = run([{"op": "np_exp", "bits": x.view(np.uint32).tolist()}])
    ours = np.array(got["bits"], np.uint32).view(np.float32)
    same = (ours.view(np.uint32) == want.view(np.uint32)) | (np.isnan(ours) & np.isnan(want))
    assert same.all(), x[~same][:5]


def test_still_weather_symbols_as_python_makes_them() -> None:
    # (each kind of weather, by day and by night where it has a sky, at sizes from 8 px to past
    # the second look's largest, in both looks, flat and as frosted glass, the same to the bit;
    # at 136 and 262 px drops and crystals at rest lie a hair past a whole pixel: not moved)
    from libre_panel.render import symbols

    rng = random.Random(0x5B01)
    cases, expected = [], []
    for kind in symbols.KINDS:
        for night in (False, True) if kind in ("clear", "mostly", "partly") else (False,):
            for k in (8, 16, 33, 64, 120, 136, 161, 262):
                for pack in symbols.PACKS:
                    for lit in (False, True):
                        own = (*(rng.randrange(256) for _ in range(3)), rng.choice([255, 180]))
                        dark = (*(rng.randrange(256) for _ in range(3)), own[3])
                        sun, rain, snow = [(*(rng.randrange(256) for _ in range(3)), 255)
                                           for _ in range(3)]  # fmt: skip
                        colours = {"cloud": own, "moon": own, "dark": dark, "sun": sun,
                                   "bolt": sun, "rain": rain, "snow": snow}  # fmt: skip
                        layers, _ = symbols.layers_for(kind, night, k, "lit" if lit else "flat",
                                                       colours, pack)  # fmt: skip
                        expected.append(symbols.compose(layers, kind, night, None))
                        cases.append({"op": "symbol", "kind": kind, "night": night, "k": k,
                                      "pack": pack, "lit": lit,
                                      "colours": [own, dark, sun, rain, snow]})  # fmt: skip
    got = run(cases)
    apart = [(c["kind"], c["night"], c["k"], c["pack"], c["lit"]) for c, g, want
             in zip(cases, got, expected, strict=True)
             if picture(g["image"]).tobytes() != want.tobytes()]  # fmt: skip
    assert not apart, apart


# (the moments of a moving symbol: a step's start, within a breath and a turn, a bolt's flashes
# and their edges (0.07, 0.14, 0.22 of its 3.7 s), the shooting star's window (0.8 s of 9.3), a
# splash's (0.22 of a fall), far on, before nought)
SYMBOL_TIMES = [0.0, 0.04, 0.5, 1.0, 1.36, 2.2, 3.1, 3.7, 3.76, 3.8, 3.84, 3.88, 3.92, 3.96,
                4.3, 7.4, 9.3, 9.5, 9.7, 10.04, 12.9, 18.6, 18.9, 33.3, 100.04, 1234.56,
                -0.04, -2.6]  # fmt: skip


def test_moving_weather_symbols_as_python_makes_them() -> None:
    # (each kind of weather, by day and by night where it has a sky, at sizes from 8 px to past
    # the second look's largest, in both looks, flat and as frosted glass, at moments of its
    # motion: the corona breathing, the rays turned (and on a big sun pulsing), the stars
    # twinkling, a big clear night's shooting star, fog's bands passing, rain and drizzle
    # falling near and far and splashing, crystals falling and turning, clouds drifting and
    # breathing, a bolt's flashes and its cloud lit from within: the same to the bit)
    from libre_panel.render import symbols

    rng = random.Random(0x5B02)
    cases, expected = [], []
    for kind in symbols.KINDS:
        for night in (False, True) if kind in ("clear", "mostly", "partly") else (False,):
            for k in (8, 16, 33, 64, 120, 136, 161, 262):
                for pack in symbols.PACKS:
                    for lit in (False, True):
                        own = (*(rng.randrange(256) for _ in range(3)), rng.choice([255, 180]))
                        dark = (*(rng.randrange(256) for _ in range(3)), own[3])
                        sun, rain, snow = [(*(rng.randrange(256) for _ in range(3)), 255)
                                           for _ in range(3)]  # fmt: skip
                        colours = {"cloud": own, "moon": own, "dark": dark, "sun": sun,
                                   "bolt": sun, "rain": rain, "snow": snow}  # fmt: skip
                        layers, _ = symbols.layers_for(kind, night, k, "lit" if lit else "flat",
                                                       colours, pack)  # fmt: skip
                        for t in rng.sample(SYMBOL_TIMES, 4):
                            expected.append(symbols.compose(layers, kind, night, t))
                            cases.append({"op": "symbol", "kind": kind, "night": night, "k": k,
                                          "pack": pack, "lit": lit, "t": t,
                                          "colours": [own, dark, sun, rain, snow]})  # fmt: skip
    got = run(cases)
    apart = [(c["kind"], c["night"], c["k"], c["pack"], c["lit"], c["t"]) for c, g, want
             in zip(cases, got, expected, strict=True)
             if picture(g["image"]).tobytes() != want.tobytes()]  # fmt: skip
    assert not apart, (len(apart), apart[:8])


GRAPH_SENSORS = ["cpu.load", "gpu.temp", "net.down", "report.course", "game.fps", "none"]


def _graph(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A graph of a sensor's history or of a course a service published: of any size, its
    range its own or the values', on any scale, filled or not, fading or flat, smoothed or not,
    hanging from the top, inset or to the edges, with a grid, a background, an end dot, a glow
    kept between its sides, left out where its reading is missing; flat or lit."""
    w, h = size
    graph: dict[str, Any] = {"type": "graph", "id": f"g{i}", "x": rng.randrange(-20, w),
                             "y": rng.randrange(-20, h),
                             "w": rng.choice([0, 1, 2, 7, 40, 120, 300]),
                             "h": rng.choice([1, 2, 9, 30, 60, 100]),
                             "sensor": rng.choice([*GRAPH_SENSORS, *["report.course"] * 2]),
                             "color": _colour(rng),
                             "history": rng.choice([0, 1, 2, 3, 10, 60, 120, 200]),
                             "fill": rng.random() < 0.8, "fill_fade": rng.random() < 0.7,
                             "smooth": rng.random() < 0.8, "inset": rng.random() < 0.7,
                             "direction": rng.choice(["up", "up", "down"]),
                             "scale": rng.choice(["linear", "linear", "sqrt", "log"]),
                             "line_width": rng.choice([1, 2, 2, 3, 4])}  # fmt: skip
    # (min and max none: the range the values' own, with room; one of them fixed, the values
    # perhaps beyond it)
    if rng.random() < 0.7:
        graph["min"] = rng.choice([None, None, None, 0, 0, -50, 10.5, 30])
    if rng.random() < 0.7:
        graph["max"] = rng.choice([None, None, None, 100, 100, 50, 175, 1e6, 0, 2**53 + 1])
    if rng.random() < 0.1 and graph.get("min") is not None:  # (a range of one value)
        graph["max"] = graph["min"]
    if rng.random() < 0.05:  # (ints a float does not hold: Python counts with them exactly)
        graph["min"], graph["max"] = rng.choice([(2**53, 2**53 + 1), (10**30, 10**30 + 7),
                                                 (-(2**60), 2**60)])  # fmt: skip
    if rng.random() < 0.2:
        graph["range_step"] = rng.choice([5, 10, 0.5, 25])
    if rng.random() < 0.4:
        graph["fill_alpha"] = rng.choice([0.0, 0.25, 0.28, 0.3, 0.5, 1.0, 1.7, -0.5, math.nan])
    if rng.random() < 0.25:
        graph["grid"] = rng.choice([1, 2, 3, 6])  # (3 at an odd height: a line half a row off)
        graph["grid_color"] = rng.choice([None, "#ffffff1f", _hex(rng, True), "@line"])
    if rng.random() < 0.2:
        graph["background"] = rng.choice([_hex(rng), _hex(rng, True), "@surface"])
    if rng.random() < 0.25:
        graph["end_dot"] = rng.choice([1.7, 2, 3.4, 0.5])
    if rng.random() < 0.3:
        graph["glow"] = rng.choice([0.21, 0.35, 1.0])
        graph["glow_radius"] = rng.choice([2, 6, 10, 24])
    if rng.random() < 0.2:
        graph["clip"] = True
    if rng.random() < 0.15:
        graph["opacity"] = rng.choice([0.5, 0.0])
    if rng.random() < 0.2:
        graph["hide_if_missing"] = True
    if rng.random() < 0.4:
        graph["finish"] = "lit"
    return graph


def _graph_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _graph(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _history(rng: random.Random) -> list[float]:
    """A sensor's numbers as the hub keeps them: a load, a temperature, a rate over many
    powers of ten, a constant, now and then one that is no number to draw (NaN, infinity)."""
    n = rng.choice([0, 1, 2, 3, 5, 40, 119, 120, 121, 300])
    kind = rng.choice(["load", "temp", "rate", "flat", "idle", "wild"])
    out: list[float] = []
    v = rng.uniform(0, 100)
    for _ in range(n):
        if kind == "load":
            v = min(100.0, max(0.0, v + rng.uniform(-15, 15)))
        elif kind == "temp":
            v = 30 + 50 * rng.random()
        elif kind == "rate":
            v = 10 ** rng.uniform(0, 9)
        elif kind == "flat":
            v = 42.0
        elif kind == "idle":  # (resting at 0 now and then: the least value 0)
            v = max(0.0, rng.uniform(-40.0, 25.0))
        else:
            v = rng.choice([rng.uniform(-1e6, 1e6), -3.5, 0.0, 1e300, -1e300, 1.7e308, -1.7e308])
        out.append(v)
    if out and rng.random() < 0.2:
        for _ in range(rng.randrange(1, 4)):
            out[rng.randrange(len(out))] = rng.choice([math.nan, math.inf, -math.inf])
    return out


def _course_text(rng: random.Random) -> Any:
    """A course a service published whole: numbers as text, a gap null (or anything not a
    number to draw: an integer of over 1023 bits too), and what is none: too short, too long,
    not a list, not text, not starting with its bracket."""
    n = rng.choice([0, 1, 2, 3, 30, 300, 4000, 4001])
    points: list[str] = []
    for _ in range(n):
        points.append(rng.choice([f"{rng.uniform(20, 90):.2f}", str(rng.randrange(0, 100)),
                                  "null", "NaN", "Infinity", "true", "1e400", "-0.0",
                                  str(10**400), str(2**1023), '"61"', "[1]"]))  # fmt: skip
    same = rng.choice(["42", "0", "-7.5"])  # (a course of one value: a range from it to one above)
    return rng.choice([f"[{', '.join(points)}]", f"[{', '.join(points)}]", "61.5", "[1, 2",
                       "", 61.5, None, f"[{same}, {same}, {same}]",
                       f" [{', '.join(points)}]"])  # fmt: skip


def _graph_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The histories the graphs draw, a course as a reading, at a moment."""
    history = {k: _history(rng) for k in GRAPH_SENSORS if rng.random() < 0.85}
    readings: dict[str, Any] = {}
    if rng.random() < 0.8:
        readings["report.course"] = _course_text(rng)
    if rng.random() < 0.3:  # (a number where a course could be: the history draws)
        readings["cpu.load"] = rng.uniform(0, 100)
    now = datetime(2026, 11, 7, rng.randrange(24), rng.randrange(60), 5)
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()},
                        history=history, now=now)  # fmt: skip
    moment = [now.year, now.month, now.day, now.hour, now.minute, now.second, 0]
    given = {"readings": {k: _typed(v) for k, v in readings.items()}, "images": {},
             "history": {k: [_typed(float(v)) for v in values] for k, values in history.items()},
             "now": moment}  # fmt: skip
    return given, snapshot


class _StillRange:
    """The renderer's _glided_bounds as a still frame takes it: no range kept, none gliding."""

    RANGE_ROOM = Renderer.RANGE_ROOM
    animate = False
    _graph_bounds = Renderer._graph_bounds
    _glided_bounds = Renderer._glided_bounds

    def __init__(self) -> None:
        self._ranges: dict[str, Any] = {}


def _range_case(rng: random.Random) -> tuple[dict[str, Any], list[float]]:
    """A graph's min, max and range step and the numbers it draws: a range of its own, the
    values' or one of each; one value (a range of none), values beyond a fixed end, a least value
    of 0, steps fine and coarse, spans over a float's largest."""
    pool = [0.0, -0.0, 0.1, 1.0, 5.0, 10.5, 25.0, 30.0, 42.0, 99.9, 100.0, 175.0, 1e6, -3.5,
            -50.0, 1e300, -1e300, 1.7e308, -1.7e308, 5e-324]  # fmt: skip

    def number() -> float:
        r = rng.random()
        if r < 0.5:
            return rng.choice(pool)
        if r < 0.75:
            return rng.uniform(-100, 200)
        return 10 ** rng.uniform(-3, 9) * rng.choice([1, -1])

    n = rng.choice([1, 1, 2, 3, 5, 8, 40])
    numbers = [number()] * n if rng.random() < 0.25 else [number() for _ in range(n)]
    # (ints a float does not hold: Python counts with them exactly; one past a float's largest)
    ends: list[Any] = [0, 0.0, -50, 10.5, 30, 42, 100, 175, 1e6, -1e300, 1.7e308, math.nan,
                       math.inf, -math.inf, 2**53 + 1, -(2**60) - 3, 10**30 + 7, 2**1030,
                       *numbers]  # fmt: skip
    widget: dict[str, Any] = {"id": "g", "min": None, "max": None}
    if rng.random() < 0.5:
        widget["min"] = rng.choice(ends)
    if rng.random() < 0.5:
        widget["max"] = rng.choice(ends)
    if rng.random() < 0.15 and widget["min"] is not None:
        widget["max"] = widget["min"]
    if rng.random() < 0.5:
        widget["range_step"] = rng.choice([5, 10, 0.5, 25, 0.1, 3, 1e-300, 1e300, -5, 0, math.nan])
    return widget, numbers


def test_ranges_of_graphs_as_python_finds_them() -> None:
    # (the range a still frame draws a graph in: its ends to the bit, an int where Python's is
    # one, exact however large (a range in whole steps of an int); and Python's errors)
    rng = random.Random(0x7A9E)
    cases, expected = [], []
    for _ in range(20000):
        widget, numbers = _range_case(rng)
        try:
            (lo, hi), _glides = _StillRange()._glided_bounds(widget, numbers, 0.0)
            want: Any = [_typed(lo), _typed(hi)]  # (an int or a float, as Python has it)
        except (ValueError, OverflowError, ZeroDivisionError) as error:
            want = {"failed": str(error)}
        cases.append({"widget": {k: _typed(v) for k, v in widget.items()},
                      "numbers": [_typed(float(v)) for v in numbers]})  # fmt: skip
        expected.append(want)
    [ours] = run([{"op": "graph_range", "cases": cases}])
    apart = [(c, g, w) for c, g, w in zip(cases, ours, expected, strict=True) if g != w]
    assert not apart, apart[:3]
    assert sum("failed" in w for w in expected if isinstance(w, dict)) > 100


class _GlidingRange(_StillRange):
    """The renderer's _glided_bounds and _range_now as moving frames take them."""

    animate = True
    continuous = False
    RANGE_GLIDE_S, RANGE_STEP_GLIDE_S = Renderer.RANGE_GLIDE_S, Renderer.RANGE_STEP_GLIDE_S
    RANGE_STEP_NEAR, RANGE_NARROW_S = Renderer.RANGE_STEP_NEAR, Renderer.RANGE_NARROW_S
    RANGE_USED = Renderer.RANGE_USED
    _range_glide = Renderer._range_glide
    _range_now = Renderer._range_now


def _range_next(rng: random.Random, numbers: list[float]) -> list[float]:
    """A graph's numbers a frame later: the same, all of them larger or smaller, a peak come
    in, the older half gone, or others."""
    r = rng.random()
    if r < 0.3:
        return numbers
    if r < 0.5:
        factor = rng.choice([0.5, 0.9, 0.99, 1.01, 1.1, 2.0])
        moved = [v * factor for v in numbers]
    elif r < 0.7:
        moved = [*numbers, rng.choice([numbers[-1] * 3, 1e6, -50.0, 0.0, numbers[-1] + 0.1])]
    elif r < 0.85:
        moved = numbers[len(numbers) // 2 :]
    else:
        moved = _range_case(rng)[1]
    return [v for v in moved if math.isfinite(v)] or numbers


def test_ranges_of_graphs_as_they_glide_as_python_finds_them() -> None:
    # (a graph's own range frame after frame: on from the one drawn last toward the one it
    # glides to at its pace (in whole steps faster, and there once near), wider at once where
    # the values leave it, narrower once they used little of it for three seconds, anew when
    # the time goes back; the values always inside; where it is a moment later; its ends to the
    # bit, an int where Python's is one; and Python's errors; frame after frame in still frames
    # too, where it is the values' each time)
    rng = random.Random(0x7A9F)
    cases, expected = [], []
    for _ in range(3000):
        widget, numbers = _range_case(rng)
        glide, frames, want = _GlidingRange(), [], []
        glide.animate = rng.random() < 0.85
        now = rng.uniform(0, 1000)
        for _ in range(rng.randrange(1, 14)):
            later = rng.choice([None, now, now + rng.choice([0.01, 0.05, 0.3, 2.0, -1.0])])
            try:
                (lo, hi), gliding = glide._glided_bounds(widget, numbers, now)
                after = None if later is None else glide._range_now(widget, later)
                later_range = None if after is None else [_typed(v) for v in after]
                want.append({"range": [_typed(lo), _typed(hi)], "gliding": gliding,
                             "later": later_range})  # fmt: skip
            except (ValueError, OverflowError, ZeroDivisionError) as error:
                want.append({"failed": str(error)})
            frames.append({"numbers": [_typed(float(v)) for v in numbers], "now": now,
                           "later": later})  # fmt: skip
            numbers = _range_next(rng, numbers)
            now += rng.choice([0, 0.02, 0.04, 0.1, 0.12, 0.5, 1.0, 2.99, 3.0, 3.01, 5.0, -0.5])
        cases.append({"widget": {k: _typed(v) for k, v in widget.items()}, "frames": frames,
                      "animate": glide.animate})  # fmt: skip
        expected.append(want)
    [ours] = run([{"op": "graph_glide", "cases": cases}])
    apart = [(c, g, w) for c, g, w in zip(cases, ours, expected, strict=True) if g != w]
    assert not apart, apart[:2]
    steps = [s for w in expected for s in w]
    assert sum(s.get("gliding", False) for s in steps) > 3000
    assert sum("failed" in s for s in steps) > 1000


def test_graphs_through_points_that_are_no_numbers(tmp_path: Path) -> None:
    # (values near a float's largest: a range of no finite span. On a log scale every point is
    # NaN: smoothed, the points stay between the rows; a fill's fade fails on them, as does a
    # range in whole steps, as Python's widget does; drawn as they are, Pillow's C turns them
    # into ints as the processor does (the least int on x86, 0 on ARM): Rust leaves those out.
    # A NaN end dot fails Python's frame. Linear and square-root scales lay them at the bottom)
    rng = random.Random(0x4A4)
    undefined = "graphs through points that are no numbers (a log scale of no finite span)"
    dot = "a graph's end dot: cannot convert float NaN to integer"
    looks: list[tuple[dict[str, Any], str | None]] = [
        ({"scale": "log"}, undefined),
        ({"scale": "log", "smooth": True}, None),
        ({"scale": "log", "fill": True, "fill_fade": True}, None),
        ({"scale": "log", "fill": True, "fill_fade": True, "direction": "down"}, None),
        ({"scale": "log", "fill": True, "fill_fade": True, "range_step": 5}, None),
        ({"scale": "log", "fill": True, "fill_fade": False}, undefined),
        ({"scale": "log", "finish": "lit"}, undefined),
        ({"scale": "log", "smooth": True, "end_dot": 2}, dot),
        ({"scale": "linear", "end_dot": 2}, None),
        ({"scale": "sqrt", "fill": True}, None),
    ]
    values = [1.7e308 if i % 7 == 0 else -1.7e308 if i % 11 == 0 else 40.0 + i for i in range(60)]
    now = datetime(2026, 11, 8, 3, 0, 5)
    snapshot = Snapshot(readings={}, history={"cpu.load": values}, now=now)
    given = {"readings": {}, "images": {}, "history": {"cpu.load": [_typed(v) for v in values]},
             "now": [2026, 11, 8, 3, 0, 5, 0]}  # fmt: skip
    cases, expected = [], []
    graph = {"type": "graph", "id": "nan", "x": 2, "y": 2, "w": 40, "h": 30, "sensor": "cpu.load",
             "color": "#40c0ff", "history": 60, "fill": False, "smooth": False, "line_width": 2,
             "min": None, "max": None}  # fmt: skip
    for n, (look, _wanted) in enumerate(looks):
        for attempt in range(20):  # (on a ground the theme takes)
            folder = tmp_path / f"t{n}_{attempt}"
            data = _frame_theme(rng, folder, _card_or_text)
            data["widgets"].append({**graph, **look})
            (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
            want = _python_frame(folder, snapshot, False)
            if "error" not in want:
                break
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        expected.append(want)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want, (_look, wanted)) in enumerate(zip(ours["frames"], expected, looks,
                                                         strict=True)):  # fmt: skip
        assert "error" not in want, (n, want)
        assert got.get("unported") == wanted, (n, got.get("unported"))
        if wanted is None:
            assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n
            assert got["boxes"] == want["boxes"], n
        assert ("failed" in want) == (wanted == dot), (n, want.get("failed"))


def test_frames_of_graphs_as_python_draws_them(tmp_path: Path) -> None:
    # (a graph of a sensor's history or a course published whole, its range its own or the
    # values' with room, filled or not, smoothed or not, with its grid, background and end
    # dot; flat or lit: its line's halo under it, worked out at a quarter of the size)
    # (a curve through NaN points, as a log scale of no finite span makes them: Pillow's C turns
    # them into ints as the processor does, so Rust leaves it out)
    undefined = ("graphs through points that are no numbers (a log scale of no finite span)",)
    drawn = _compare_frames(tmp_path, 0x6A9, 200, _graph_or_more, _graph_snapshot,
                            undefined=undefined)  # fmt: skip
    assert drawn > 150


# (a line left out twice over, a name with an = in it, a set with no star, every reading, a
# pattern with no line's name and none at all, keys without a dot, digits of other scripts)
LIST_ITEMS = ["cpu.temp = CPU", "gpu.temp = GPU", "fan.*", "temp.*", "!temp.acpi*", "disk.*.load",
              "core.?", "core.*", "proc.[0-9]*", "proc.[!1]*", "# a comment", "", "  ", "nothing",
              "!cpu.temp", "net.* = Net", "fan.[a-c]*", "*.load", "!*.name", "gpu.*",
              "drive.ü*", "fan.gpu = Card", "!!core.1", "gpu.* = GPU = 2", "core.[12]", "disk.*",
              "= Nameless", "!", "up*", "area.*", "*", "core.٣*", "# fan.*", "load.*",
              "temp.*\ncpu.temp = CPU", "!cpu.temp\ntemp.*",
              "!temp.k10temp*\ncpu.temp = CPU"]  # fmt: skip
LIST_FORMATS = ["auto", "", "{value:.1f}", "{value:.0f} {unit}", "{label}: {value}", "{nope}",
                "{value:q}", "{value:bytes}", "{value:>6.2f}"]  # fmt: skip


def _list(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A list of readings picked by key and pattern (left out, named, sorted), as rows, bars,
    upright columns or tiles, in columns of its own or as many as fit, with a detail beside
    a name, colour rules, the largest value filling the bar where its range is none."""
    w, h = size
    items = "\n".join(rng.choice(LIST_ITEMS) for _ in range(rng.randrange(0, 6)))
    data: dict[str, Any] = {"type": "list", "id": f"l{i}", "x": rng.randrange(-20, w),
                            "y": rng.randrange(-20, h),
                            "w": rng.choice([1, 20, 80, 160, 240, 300, 400, 640]),
                            "h": rng.choice([1, 12, 40, 100, 200, 400]), "items": items,
                            "style": rng.choice(["rows", "bars", "bars", "columns", "cells"]),
                            "columns": rng.choice([0, 0, 0, 1, 2, 3, 5]),
                            "max_items": rng.choice([0, 0, 1, 3, 10, -1]),
                            "sort": rng.choice(["none", "high", "low", "name"]),
                            "format": rng.choice(LIST_FORMATS),
                            "font_size": rng.choice([3, 5, 6, 9, 12, 18, 26, 40]),
                            "uppercase": rng.random() < 0.6, "levels": rng.random() < 0.8,
                            "color": _colour(rng), "muted": _colour(rng), "color2": _colour(rng),
                            "scale": rng.choice(["linear", "sqrt", "log"])}  # fmt: skip
    if rng.random() < 0.4:
        data["detail"] = rng.choice(["name", "free", "nope", "crit"])
        data["detail_format"] = rng.choice(["{value:.0f} {unit}", "{value}", "{bad}"])
    if rng.random() < 0.5:
        data["min"], data["max"] = rng.choice([(0, 100), (20, 90), (0, 0), (50, 10), (-5, 5.5)])
    if rng.random() < 0.3:
        data["background"] = rng.choice([None, _hex(rng), _hex(rng, True), "@surface"])
    if rng.random() < 0.3:
        data["color_rules"] = [{"above": rng.choice([10, 50, 70.5]), "color": _hex(rng)}
                               for _ in range(rng.randrange(1, 4))]  # fmt: skip
    if rng.random() < 0.2:
        data["empty"] = rng.choice(["No readings", "—", "Keine Werte"])
    if rng.random() < 0.5:
        data["font"], data["label_font"] = rng.choice(FONTS), rng.choice(FONTS)
    if rng.random() < 0.15:
        data["opacity"] = 0.6
    return data


def _list_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _list(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _list_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """Readings a list picks among: temperatures (one repeating another under a friendlier
    key), fans, drives with their names and free room beside them, cores, processes, values
    of every kind (none, text, a bool, an int, a float) and units."""
    readings: list[Reading] = []

    def add(key: str, value: Any, unit: str = "", label: str = "", origin: str = "") -> None:
        if rng.random() < 0.85:
            readings.append(Reading(key, value, unit, label, origin))

    add("cpu.temp", rng.uniform(30, 95), "°C", "CPU Package", "temp.k10temp.tctl")
    add("temp.k10temp.tctl", rng.uniform(30, 95), "°C", "Tctl")
    add("temp.acpi.1", rng.uniform(20, 60), "°C", "ACPI")
    add("temp.k10temp.crit", 95.0, "°C", "Tctl crit")  # (details beside the temperatures)
    add("temp.acpi.crit", 80, "°C")
    add("load.avg", rng.uniform(0, 0.9), "", "Load")  # (a largest value below 1)
    add("gpu.temp", rng.uniform(30, 90), "°C", "GPU Core")
    for n in rng.sample([1, 2, 3, 10, 11, 20], rng.randrange(0, 6)):
        add(f"fan.{n}", rng.choice([0, 0.0, rng.uniform(300, 2400), 1200]), "rpm", f"Fan {n}")
    add("fan.gpu", rng.uniform(0, 3000), "rpm", "")
    add("fan.amd", rng.uniform(0, 3000), "rpm", "AMD")
    for n in range(1, rng.randrange(1, 4)):
        add(f"disk.{n}.load", rng.uniform(0, 100), "%", "")
        add(f"disk.{n}.name", rng.choice(["System", "Games", "Ⅻ", ""]))
        add(f"disk.{n}.free", rng.choice([rng.uniform(1e9, 2e12), None]), "B")
    for n in range(1, rng.randrange(1, 14)):
        add(f"core.{n}", rng.uniform(0, 100), "%", f"Core {n}")
    for n in rng.sample(range(1, 100), rng.choice([0, 3, 7, 70, 70])):  # (more than a list takes)
        add(f"proc.{n}", rng.choice([rng.uniform(0, 50), "idle", None, True, 7, 50, 10]), "%",
            rng.choice(["Firefox", "Steam", "ſystem", "Code", "Code 2"]))  # fmt: skip
    add("net.down", rng.uniform(0, 1e8), "B/s", "Down")
    add("net.up", rng.choice([0, 12.5, 1e7]), "B/s", "Up")
    add("drive.ünter", 42, "GB", "")
    add("gpu.load", rng.choice([True, None, "high", 61.5]), "%", "GPU")
    add("uptime", rng.uniform(0, 1e6), "s", "Uptime")  # (keys without a dot: none beside them)
    add("name", "Box")
    add("core.٣", rng.uniform(0, 100), "%", "Core ٣")  # (a digit re's \d takes, int() reads)
    add("area.1²", 12.0, "m²", "Area")  # (a digit int() refuses: the list fails, as Python's)
    rng.shuffle(readings)
    now = datetime(2026, 11, 7, 18, 5, 9)
    snapshot = Snapshot(readings={r.key: r for r in readings}, now=now)
    given = {"readings": {r.key: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                                  "origin": r.origin} for r in readings},
             "images": {}, "now": [2026, 11, 7, 18, 5, 9, 0]}  # fmt: skip
    return given, snapshot


def test_lists_rust_does_not_draw_yet(tmp_path: Path) -> None:
    # (ints a float does not hold, rules above NaN)
    rng = random.Random(0x1157)
    looks: list[tuple[dict[str, Any], str]] = [
        ({"max": 2**53 + 1}, "numbers a float does not hold as they are"),
        ({"color_rules": [{"above": math.nan, "color": "#ff0000"}]}, "colour rules above NaN"),
    ]
    cases = []
    for n, (look, _why) in enumerate(looks):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        data["widgets"].append({**_list(rng, 99, (64, 48)), "items": "fan.*", **look})
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, _ = _list_snapshot(rng)
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    assert [got.get("unported", got.get("error")) for got in ours["frames"]] == [
        why for _look, why in looks
    ]


def _list_sweep_snapshot() -> tuple[dict[str, Any], Snapshot]:
    """The readings the lists across their widths pick among, at fixed values: one repeating
    another, details beside some (one of none), cores to fill columns and tiles, tasks to fill
    more of them than a list takes, one under no key."""
    readings = [
        Reading("cpu.temp", 61.5, "°C", "CPU Package", "temp.k10temp.tctl"),
        Reading("temp.k10temp.tctl", 61.5, "°C", "Tctl"),
        Reading("temp.k10temp.crit", 95.0, "°C", "Tctl crit"),
        Reading("temp.acpi.1", 41.0, "°C", "ACPI"),
        Reading("temp.acpi.crit", 80, "°C"),
        *(Reading(f"core.{n}", float(n * 37 % 101), "%", f"Core {n}") for n in range(1, 10)),
        Reading("disk.1.load", 37.5, "%"),
        Reading("disk.1.name", "System"),
        Reading("disk.1.free", None, "B"),
        Reading("disk.2.load", 81.0, "%"),
        Reading("disk.2.name", "Games"),
        Reading("disk.2.free", 4.2e11, "B"),
        Reading("load.avg", 0.37, "", "Load"),
        *(Reading(f"proc.{n}", float(n * 13 % 50), "%", f"Task {n}") for n in range(1, 71)),
        Reading("# note", 12.0, "%", "Note"),  # (a line of its name is a comment all the same)
        Reading("", 7.0, "%", "Unnamed"),  # (a service may publish under no key at all)
    ]
    snapshot = Snapshot(readings={r.key: r for r in readings}, now=datetime(2026, 11, 8, 9, 5, 9))
    given = {"readings": {r.key: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                                  "origin": r.origin} for r in readings},
             "images": {}, "now": [2026, 11, 8, 9, 5, 9, 0]}  # fmt: skip
    return given, snapshot


# rows with a detail beside the names (and a comment), upright columns of cores and of long
# names, bars beside the names and below them (names in capitals as Python's Unicode makes them,
# not as a later one would: ƛ and ɤ have none in 15.0; a low list showing three of its cores:
# they share the width), tiles with and without their levels, and more of them than a list
# takes (64, and one a later line adds); a line of no pattern (none), and a reading under no key
# with one left out that repeats nothing
LIST_SWEEP: list[dict[str, Any]] = [
    {"items": "temp.*\ncpu.temp = CPU\ncore.*\n# note", "style": "rows", "detail": "crit",
     "detail_format": "{value:.0f} {unit}", "font_size": 12},
    {"items": "core.*", "style": "columns", "font_size": 12, "uppercase": True},
    {"items": "disk.*.load\ntemp.*", "style": "columns", "font_size": 12},
    {"items": "core.*\nload.*", "style": "bars", "font_size": 9, "min": 0, "max": 0},
    {"items": "disk.*.load\n!cpu.temp\ntemp.*\nload.avg = Load ƛɤ", "style": "bars",
     "detail": "free", "detail_format": "{value}", "font_size": 12, "uppercase": True,
     "label_font": "builtin:LiberationSans-Regular"},
    {"items": "core.*", "style": "bars", "font_size": 6, "h": 24, "max_items": 3},
    {"items": "core.[1-4]\ndisk.*.load", "style": "cells", "font_size": 18},
    {"items": "core.*", "style": "cells", "levels": False, "font_size": 18},
    {"items": "proc.*\ncore.1", "style": "cells", "font_size": 9, "max_items": 100},
    {"items": "= Nameless\n!core.2\n*", "style": "rows", "font_size": 9, "max_items": 4},
]  # fmt: skip


def test_lists_across_their_widths(tmp_path: Path) -> None:
    # (each list at every third width from 8 to 400 px (each list one more than the one before:
    # every width comes up), low and tall: where a name, a detail or a value fits, how many
    # columns, whether bars stand beside the names, how wide the tiles: Python's thresholds,
    # each crossed; and lists of no width or height, or less, as Python draws them: a pixel)
    given, snapshot = _list_sweep_snapshot()
    cases, expected = [], []
    sizes = [(w, h) for w in range(8, 401, 3) for h in (40, 160)]
    for w, h in [*sizes, (-8, 40), (-1, 160), (0, 0), (2, 40), (100, -4), (100, 0)]:
        folder = tmp_path / f"t{w}x{h}"
        folder.mkdir()
        widgets, y = [], 4
        for i, look in enumerate(LIST_SWEEP):
            widget = {"type": "list", "id": f"l{i}", "x": 4, "y": y, "w": w + i, "h": h,
                      "color": "#e0e0e0", "muted": "#909090", "color2": "#40c0ff",
                      "background": "#202830", "levels": True, **look}  # fmt: skip
            widgets.append(widget)
            y += widget["h"] + 12
        data = {"format": THEME_FORMAT, "name": "lists", "display": {"width": 424, "height": y},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert "frame" in want, (case["folder"], want)
        sha = hashlib.sha256(want["frame"].tobytes()).hexdigest()
        assert got.get("sha256") == sha, case["folder"]
        assert got["boxes"] == want["boxes"], case["folder"]


# lists casting a glow or a shadow, small type among them (the least room round a list for its
# glyphs: the light's picture two pixels smaller), at widths that take the light's picture across
# the size it is blurred at half from
LIST_LIGHT: list[dict[str, Any]] = [
    {"items": "temp.*\ncpu.temp = CPU\ncore.*", "style": "rows", "detail": "crit",
     "detail_format": "{value:.0f} {unit}", "font_size": 4, "glow": 0.7, "glow_radius": 6},
    {"items": "disk.*.load\ncore.*", "style": "bars", "font_size": 5, "shadow": "#000000a0",
     "shadow_blur": 4, "shadow_offset": 3},
    {"items": "core.*", "style": "cells", "font_size": 12, "glow": 0.35, "glow_radius": 10,
     "shadow": "#00000060", "shadow_blur": 6},
]  # fmt: skip


def test_lists_with_glow_and_shadow_within_a_level(tmp_path: Path) -> None:
    # (their light blurred in floating point, as a card's: now and then a pixel a level apart)
    import numpy as np

    given, snapshot = _list_sweep_snapshot()
    cases, expected = [], []
    for w in range(8, 401, 3):
        folder = tmp_path / f"t{w}"
        folder.mkdir()
        widgets, y = [], 24
        for i, look in enumerate(LIST_LIGHT):
            widget = {"type": "list", "id": f"l{i}", "x": 24, "y": y, "w": w + i, "h": 40,
                      "color": "#e0e0e0", "muted": "#909090", "color2": "#40c0ff",
                      "background": "#202830", "levels": True, **look}  # fmt: skip
            widgets.append(widget)
            y += widget["h"] + 48
        data = {"format": THEME_FORMAT, "name": "lists", "display": {"width": 456, "height": y},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        dither = w % 2 == 0
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither, "bytes": True})
        expected.append(_python_frame(folder, snapshot, dither))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    differ = total = 0
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert "frame" in want and "frame" in got, (case["folder"], got, want)
        apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
        assert apart.max() <= 1, case["folder"]
        differ += int((apart.max(axis=2) > 0).sum())
        total += apart.shape[0] * apart.shape[1]
        assert got["boxes"] == want["boxes"], case["folder"]
    assert differ <= total // 10000


def _bits(x: float) -> int:
    """A float's 64 bits, as lp-check takes numbers that JSON cannot hold (NaN, infinities)."""
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def test_a_fan_s_layers_as_python_reads_them() -> None:
    # (the rotor's grey layers, read from its pictures as fan._plane does (a mean of three
    # channels in float32, from the first) and averaged down to each size as _reduced does
    # (Image.resize of mode F, the box filter): every float the same to the bit)
    import numpy as np

    from libre_panel.render import fan

    sizes = [*range(6, 41), 48, 57, 60, 76, 100, 151, 152, 153]
    cases = [{"op": "fan_layers", "fonts_dir": str(fonts.FONT_DIR), "k": k} for k in sizes]
    for k, got in zip(sizes, run(cases), strict=True):
        want = fan.rotor_layers(k).astype("<f4")
        ours = np.frombuffer(base64.b64decode(got["data"]), "<f4").reshape(want.shape)
        assert np.array_equal(ours.view(np.uint32), want.view(np.uint32)), k


def test_fans_stand_as_python_settles_them() -> None:
    # (Rotor.settle in a still frame: where its key puts its blades, its speed (rising as the
    # 0.7th power of its share, none below 30 rpm or with no range), its ring, its light; of
    # NaN, infinities, nothing at all; and what they make: its position, blur, dim and arc)
    from libre_panel.render import fan

    rng = random.Random(0xFA4)
    cases, wanted = [], []
    for _ in range(20000):
        key = "".join(rng.choice("abcfan.1234\0é") for _ in range(rng.randrange(0, 12)))
        rpm = rng.choice([None, 0.0, 29.9, 30.0, rng.uniform(0, 5000), rng.uniform(0, 5000),
                          math.nan, math.inf, -5.0, 1e300])  # fmt: skip
        most = rng.choice([0.0, -1.0, 1e-9, rng.uniform(0, 5000), 2000.0, math.nan, math.inf,
                           1e300])  # fmt: skip
        rotor = fan.Rotor(key)
        rotor.settle(rpm, most)
        cases.append({"key": key, "rpm": None if rpm is None else _bits(rpm),
                      "max_rpm": _bits(most)})  # fmt: skip
        wanted.append({"angle": _bits(rotor.angle), "speed": _bits(rotor.speed),
                       "ring": _bits(rotor.ring), "light": _bits(rotor.light),
                       "phase": rotor.phase, "blur": rotor.blur, "dim": rotor.dim,
                       "steps": round(rotor.ring * fan.RING_STEPS)})  # fmt: skip
    [got] = run([{"op": "fan_rotor", "cases": cases}])
    assert got["rotors"] == wanted


def test_a_fan_s_picture_as_python_lays_it() -> None:
    # (its ring (the groove all round, the arc's glow at each of its lengths) or none, its
    # rotor in the theme's colour and a lighter one, blurred into its disc by its speed and
    # dimmed by its light, laid as the renderer lays fan.fan_parts: to the bit, at the sizes
    # lists draw them)
    from PIL import Image

    from libre_panel.render import fan

    rng = random.Random(0xFA5)
    cases, wanted = [], []
    # (every length of the arc, from none to all round, at three sizes; then 400 at random)
    arcs = [(size, steps / fan.RING_STEPS) for size in (12, 34, 76) for steps in range(121)]
    for n in range(len(arcs) + 400):
        size = rng.choice([8, 12, 13, 15, 17, 20, 23, 27, 34, 48, 49, 57, 66, 76, 100])
        colour = tuple(rng.randrange(256) for _ in range(3))
        if rng.random() < 0.2:
            colour = rng.choice([(255, 255, 255), (0, 0, 0), (19, 229, 215)])
        rotor = fan.Rotor("x")
        rotor.angle = rng.random()
        rotor.speed = rng.choice([0.0, 0.22, rng.uniform(0, 1.1), 1.1])
        rotor.ring = rng.choice([0.0, 1.0, rng.random(), 0.996, 0.004])
        rotor.light = rng.choice([1.0, 0.5, 0.25])
        frame = rng.choice(["ring", "none"])
        if n < len(arcs):
            (size, rotor.ring), frame = arcs[n], "ring"
        parts = fan.fan_parts(colour, size, size, rotor, frame)
        canvas = fan.geometry(size, size)[2]
        first, at = parts[0]
        if at == 0 and first.size == (canvas, canvas):  # (as Renderer._laid_fan lays them)
            laid, rest = first.copy(), parts[1:]
        else:
            laid, rest = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0)), parts
        for part, at in rest:
            laid.alpha_composite(part, (at, at))
        cases.append({"colour": list(colour), "size": size, "angle": _bits(rotor.angle),
                      "speed": _bits(rotor.speed), "ring": _bits(rotor.ring),
                      "light": _bits(rotor.light), "ring_shown": frame == "ring"})  # fmt: skip
        wanted.append(laid.tobytes())
    [got] = run([{"op": "fan_picture", "fonts_dir": str(fonts.FONT_DIR), "cases": cases}])
    for case, ours, want in zip(cases, got["pictures"], wanted, strict=True):
        assert picture(ours).tobytes() == want, case


FAN_LOOKS = ("noctua-g2", "noctua-a12x25", "noctua-a14", "libre")


def test_the_looks_drawn_as_numpy_draws_them() -> None:
    # (fan_looks.rotor and fan_looks.frame of every look, the default's among them (drawn as
    # the others where it is in its own colours), at the smallest sizes and larger, at each
    # blade's positions and at angles of every kind: every float32 of every layer the same to
    # the bit as numpy's, its float32 and float64 steps as numpy takes them)
    import numpy as np

    rng = random.Random(0xFA60)
    asked = []
    for name in FAN_LOOKS:
        for size in [1, 2, 3, 4, 5, 8, 11, 16, 25, 38, 47, 64, 95, rng.randrange(6, 130)]:
            asked.append((name, size, "frame"))
            blades = {"libre": 6, "noctua-a14": 7}.get(name, 9)
            for angle in [360 / blades * rng.randrange(48) / 48, rng.uniform(-720, 720),
                          rng.choice([0.0, -0.0, 1e-9, 359.999, 1e6, -1e-300])]:  # fmt: skip
                asked.append((name, size, _bits(angle)))
    # (and the least sizes where a pixel's centre lies on an edge: of Libre Panel's hub ring, a
    # rotor of 339 px; of a screw hole of Noctua's pads, a frame of 510 px)
    asked += [("libre", 339, _bits(12.5)), ("noctua-a14", 510, "frame")]
    _draw_looks(asked)
    cases = [{"look": name, "size": size, **({} if what == "frame" else {"angle": what})}
             for name, size, what in asked]  # fmt: skip
    [got] = run([{"op": "fan_look_layers", "cases": cases}])
    for key, ours in zip(asked, got["layers"], strict=True):
        want = _LOOKS_DRAWN[key].astype("<f4")
        found = np.frombuffer(base64.b64decode(ours), "<f4").reshape(want.shape)
        assert np.array_equal(found.view(np.uint32), want.view(np.uint32)), key


def test_a_fan_s_layers_of_each_look_as_python_draws_them() -> None:
    # (rotor_layers of the drawn looks: each look's rotor at every position over its blade's
    # pitch, at the sizes the fans here have and more: every float the same to the bit)
    import numpy as np

    from libre_panel.render import fan

    sizes = [6, 7, 10, 13, 18, 27, 38, 45, 60, 79]
    looks = FAN_LOOKS[1:]
    _draw_looks([(name, k, "all") for name in looks for k in sizes])
    cases = [{"op": "fan_layers", "fonts_dir": str(fonts.FONT_DIR), "k": k, "look": name}
             for name in looks for k in sizes]  # fmt: skip
    for case, got in zip(cases, run(cases), strict=True):
        want = fan.rotor_layers(case["k"], case["look"]).astype("<f4")
        ours = np.frombuffer(base64.b64decode(got["data"]), "<f4").reshape(want.shape)
        assert np.array_equal(ours.view(np.uint32), want.view(np.uint32)), case


def test_a_fan_of_each_look_as_python_lays_it() -> None:
    # (a fan of every look in the theme's colours or its own, in its ring, its lit square frame
    # (the Blender frame, or the look's own drawn one, at each of its brightnesses) or none, its
    # rotor at every speed and light: laid as the renderer lays fan.fan_parts, to the bit)
    from PIL import Image

    from libre_panel.render import fan

    rng = random.Random(0xFA61)
    sizes = [8, 12, 13, 17, 23, 34, 48, 49, 57, 76, 100]
    _draw_looks([(name, fan.rotor_size(size), "all") for name in FAN_LOOKS[1:] for size in sizes]
                + [(name, size, "frame") for name in FAN_LOOKS for size in sizes])  # fmt: skip
    cases, wanted = [], []
    # (every brightness of the square frame of every look, its own colours and the theme's;
    # then 600 at random)
    shades = [(name, paint, k / fan.SHADES) for name in FAN_LOOKS for paint in ("theme", "own")
              for k in range(fan.SHADES + 1)]  # fmt: skip
    for n in range(len(shades) + 600):
        size = rng.choice(sizes)
        colour = tuple(rng.randrange(256) for _ in range(3))
        if rng.random() < 0.2:
            colour = rng.choice([(255, 255, 255), (0, 0, 0), (19, 229, 215), (104, 70, 54)])
        rotor = fan.Rotor("x")
        rotor.angle = rng.random()
        rotor.speed = rng.choice([0.0, 0.22, rng.uniform(0, 1.1), 1.1])
        rotor.ring = rng.choice([0.0, 1.0, rng.random(), 0.0625, 0.9375, 0.004])
        rotor.light = rng.choice([1.0, 0.5, 0.25])
        frame = rng.choice(["ring", "square", "square", "none"])
        look, paint = rng.choice(FAN_LOOKS), rng.choice(["theme", "own"])
        if n < len(shades):
            (look, paint, rotor.ring), frame = shades[n], "square"
        parts = fan.fan_parts(colour, size, size, rotor, frame, look, paint)
        canvas = fan.geometry(size, size)[2]
        first, at = parts[0]
        if at == 0 and first.size == (canvas, canvas):  # (as Renderer._laid_fan lays them)
            laid, rest = first.copy(), parts[1:]
        else:
            laid, rest = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0)), parts
        for part, at in rest:
            laid.alpha_composite(part, (at, at))
        cases.append({"colour": list(colour), "size": size, "angle": _bits(rotor.angle),
                      "speed": _bits(rotor.speed), "ring": _bits(rotor.ring),
                      "light": _bits(rotor.light), "frame": frame, "look": look,
                      "paint": paint})  # fmt: skip
        wanted.append(laid.tobytes())
    [got] = run([{"op": "fan_picture", "fonts_dir": str(fonts.FONT_DIR), "cases": cases}])
    for case, ours, want in zip(cases, got["pictures"], wanted, strict=True):
        assert picture(ours).tobytes() == want, case


# (fans by pattern and by key, named, the graphics card's among them; lines of other readings)
FAN_ITEMS = ["fan.*", "fan.*", "fan.[0-9]*", "fan.gpu = Card", "gpu.fan", "fan.?", "temp.*",
             "fan.1 = Front\nfan.2 = Back", "!fan.amd\nfan.*", "fan.cpu*", "*.fan",
             "fan.gpu*"]  # fmt: skip


def _fan_list(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A list of fans: each standing beside its name and number in its ring (or none), as
    many as fit in columns and lines, those never run left out where it says."""
    w, h = size
    items = "\n".join(rng.choice(FAN_ITEMS) for _ in range(rng.randrange(1, 4)))
    data: dict[str, Any] = {"type": "list", "id": f"f{i}", "x": rng.randrange(-20, w),
                            "y": rng.randrange(-20, h), "style": "fans", "items": items,
                            "w": rng.choice([1, 30, 80, 160, 240, 320, 480]),
                            "h": rng.choice([1, 12, 40, 60, 100, 200, 400]),
                            "columns": rng.choice([0, 0, 0, 1, 2, 3, 7]),
                            "max_items": rng.choice([0, 0, 1, 2, 5, -1]),
                            "sort": rng.choice(["none", "high", "low", "name"]),
                            "format": rng.choice(["auto", "", "{value:.0f}", "{value:.0f} {unit}",
                                                  "{nope}"]),
                            "font_size": rng.choice([3, 6, 9, 12, 14, 18, 26, 40]),
                            "uppercase": rng.random() < 0.5, "levels": rng.random() < 0.75,
                            "hide_unused": rng.random() < 0.4,
                            "color": _colour(rng), "muted": _colour(rng),
                            "color2": rng.choice([_colour(rng), _colour(rng), "#ffffff",
                                                  "#000000", _hex(rng, True)])}  # fmt: skip
    if rng.random() < 0.5:
        data["min"], data["max"] = rng.choice([(0, 2000), (0, 3000), (0, 0), (500, 100),
                                               (0, 1e-9), (0, 40)])  # fmt: skip
    if rng.random() < 0.3:
        data["color_rules"] = [{"above": rng.choice([800, 1500, 2200]), "color": _hex(rng)}
                               for _ in range(rng.randrange(1, 3))]  # fmt: skip
    if rng.random() < 0.4:
        data["font"], data["label_font"] = rng.choice(FONTS), rng.choice(FONTS)
    if rng.random() < 0.15:
        data["opacity"] = 0.6
    if rng.random() < 0.15:
        data["empty"] = "No fans"
    return data


def _fan_list_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    pick = rng.random()
    if pick < 0.7:
        return _fan_list(rng, i, size)
    return _list(rng, i, size) if pick < 0.85 else _card_or_text(rng, i, size)


def _fan_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """Fans at every speed a reading gives: none, at rest, below what turns (30 rpm), turning,
    past the most, not a number, not a number at all; the graphics card's (kept where the ones
    never run are left out), temperatures among them."""
    readings: list[Reading] = []

    def add(key: str, value: Any, unit: str = "rpm", label: str = "") -> None:
        if rng.random() < 0.85:
            readings.append(Reading(key, value, unit, label))

    def speed() -> Any:
        return rng.choice([None, 0, 0.0, 12.0, 29.9, 30.0, rng.uniform(30, 3000),
                           rng.uniform(300, 2400), 1200, 2000.0, 4500.0, math.nan, math.inf,
                           -3.0, "n/a", True])  # fmt: skip

    for n in rng.sample([1, 2, 3, 4, 7, 10, 11, 20], rng.randrange(0, 8)):
        add(f"fan.{n}", speed(), "rpm", rng.choice([f"Fan {n}", "", "Front", "CPU Fan", "Pump"]))
    add("fan.gpu", speed(), "rpm", "")
    add("fan.gpu1", speed(), "rpm", "GPU 2")
    add("gpu.fan", speed(), "rpm", "GPU Fan")
    add("fan.cpu", speed(), "rpm", "CPU")
    add("fan.amd", speed(), "rpm", "AMD")
    add("pump.fan", speed(), "rpm", "Pump")
    add("temp.cpu", rng.uniform(30, 90), "°C", "CPU")
    add("temp.gpu", rng.uniform(30, 90), "°C", "GPU")
    rng.shuffle(readings)
    now = datetime(2026, 11, 8, 6, 5, 9)
    snapshot = Snapshot(readings={r.key: r for r in readings}, now=now)
    given = {"readings": {r.key: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                                  "origin": r.origin} for r in readings},
             "images": {}, "now": [2026, 11, 8, 6, 5, 9, 0]}  # fmt: skip
    return given, snapshot


def test_frames_of_fan_lists_as_python_draws_them(tmp_path: Path) -> None:
    # (each fan as a still frame shows it, Rotor.settle: where its key puts its blades, its
    # blur and light by its speed, its ring as full; laid on its name and number)
    assert _compare_frames(tmp_path, 0xFA45, 200, _fan_list_or_more, _fan_snapshot) > 150


def _fan_sweep_snapshot() -> tuple[dict[str, Any], Snapshot]:
    """The fans the fan lists across their widths show, at fixed speeds: one standing (left out
    where the ones never run are), one repeating another, one of no number, one past the most,
    one below what turns; a temperature among them."""
    readings = [
        Reading("gpu.fan", 1797.36, "rpm", "GPU Fan"),
        Reading("fan.board.case_1", 811.98, "rpm", "Case fan 1"),
        Reading("fan.board.case_2", 0.0, "rpm", "Case fan 2"),
        Reading("fan.board.pump", 2123.86, "rpm", "Pump"),
        Reading("fan.cpu", 1329.55, "rpm", "CPU fan"),
        Reading("cpu.fan", 1329.55, "rpm", "CPU fan", "fan.cpu"),
        Reading("fan.lpc.fan_1", "n/a", "rpm", "Fan #1"),
        Reading("fan.lpc.fan_2", 4800.0, "rpm", "Fan #2"),
        Reading("fan.lpc.fan_3", 25.0, "rpm", "Fan #3"),
        Reading("temp.cpu", 61.5, "°C", "CPU"),
    ]
    snapshot = Snapshot(readings={r.key: r for r in readings}, now=datetime(2026, 11, 8, 6, 5, 9))
    given = {"readings": {r.key: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                                  "origin": r.origin} for r in readings},
             "images": {}, "now": [2026, 11, 8, 6, 5, 9, 0]}  # fmt: skip
    return given, snapshot


# Arena's fans (in rings, at most six, a fixed range), fans without rings, in columns of their
# own, of the largest type beside a temperature, and many columns of small ones
FAN_SWEEP: list[dict[str, Any]] = [
    {"items": "gpu.fan = GPU fan\nfan.*", "font_size": 25, "uppercase": True, "max_items": 6,
     "min": 0, "max": 3000, "format": "{value:.0f}", "hide_unused": True},
    {"items": "fan.*", "font_size": 12, "levels": False},
    {"items": "fan.*\ncpu.fan", "font_size": 6, "columns": 2, "hide_unused": True},
    {"items": "fan.cpu\nfan.gpu = Card\ntemp.cpu", "font_size": 40, "min": 0, "max": 0},
    {"items": "fan.*", "font_size": 9, "columns": 7},
]  # fmt: skip


def test_fan_lists_across_their_widths(tmp_path: Path) -> None:
    # (each list at every ninth width from 8 to 640 px (each one more than the one before),
    # low and tall: how many fans fit side by side and in lines, how wide their places are and
    # how much of their names and numbers shows; Python's thresholds, each crossed; and lists
    # of no width or height, or less)
    given, snapshot = _fan_sweep_snapshot()
    cases, expected = [], []
    sizes = [(w, h) for w in range(8, 641, 9) for h in (40, 132)]
    for w, h in [*sizes, (-8, 40), (0, 0), (100, 0), (100, -4), (300, 1), (2, 132)]:
        folder = tmp_path / f"t{w}x{h}"
        folder.mkdir()
        widgets, y = [], 4
        for i, look in enumerate(FAN_SWEEP):
            widget = {"type": "list", "id": f"f{i}", "x": 4, "y": y, "w": w + i, "h": h,
                      "style": "fans", "color": "#eef6ff", "muted": "#8ca0b8",
                      "color2": "#13e5d7", "levels": True, **look}  # fmt: skip
            widgets.append(widget)
            y += max(h, 0) + 12
        data = {"format": THEME_FORMAT, "name": "fans", "display": {"width": 660, "height": y},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        cases.append({"folder": str(folder), "snapshot": given, "dither": False})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert "frame" in want, (case["folder"], want)
        sha = hashlib.sha256(want["frame"].tobytes()).hexdigest()
        assert got.get("sha256") == sha, case["folder"]
        assert got["boxes"] == want["boxes"], case["folder"]


def test_fan_lists_with_glow_and_shadow_within_a_level(tmp_path: Path) -> None:
    # (their names and numbers cast the light, the fans lie on it: blurred in floating point,
    # as a card's, now and then a pixel a level apart)
    import numpy as np

    given, snapshot = _fan_sweep_snapshot()
    looks = [{**FAN_SWEEP[0], "glow": 0.7, "glow_radius": 6},
             {**FAN_SWEEP[1], "shadow": "#000000a0", "shadow_blur": 4, "opacity": 0.8}]  # fmt: skip
    cases, expected = [], []
    for w in range(40, 641, 30):
        folder = tmp_path / f"t{w}"
        folder.mkdir()
        widgets = [{"type": "list", "id": f"f{i}", "x": 24, "y": 24 + i * 180, "w": w, "h": 132,
                    "style": "fans", "color": "#eef6ff", "muted": "#8ca0b8", "color2": "#13e5d7",
                    "levels": True, **look} for i, look in enumerate(looks)]  # fmt: skip
        data = {"format": THEME_FORMAT, "name": "fans", "display": {"width": 690, "height": 380},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        dither = w % 60 == 0
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither, "bytes": True})
        expected.append(_python_frame(folder, snapshot, dither))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    differ = total = 0
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert "frame" in want and "frame" in got, (case["folder"], got, want)
        apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
        assert apart.max() <= 1, case["folder"]
        differ += int((apart.max(axis=2) > 0).sum())
        total += apart.shape[0] * apart.shape[1]
        assert got["boxes"] == want["boxes"], case["folder"]
    assert differ <= total // 10000


def test_fans_seen_running_stay_in_a_list(tmp_path: Path) -> None:
    # (a list that leaves out the fans never run keeps one that ran before, standing now, as
    # Python keeps those it saw running from frame to frame (lists._RAN); a frame of its own
    # leaves it out)
    folder = tmp_path / "fans"
    folder.mkdir()
    widget = {"type": "list", "id": "f0", "x": 4, "y": 4, "w": 300, "h": 60, "style": "fans",
              "items": "fan.*", "hide_unused": True, "font_size": 12, "color": "#eef6ff",
              "muted": "#8ca0b8", "color2": "#13e5d7"}  # fmt: skip
    data = {"format": THEME_FORMAT, "name": "fans", "display": {"width": 320, "height": 80},
            "palette": {name: "#808080" for name in PALETTE},
            "background": {"color": "#101418"}, "widgets": [widget]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")

    def readings(rpm: float) -> tuple[dict[str, Any], Snapshot]:
        found = [Reading("fan.1", 1200.0, "rpm", "Front"), Reading("fan.3", rpm, "rpm", "Rear")]
        snapshot = Snapshot(readings={r.key: r for r in found}, now=datetime(2026, 11, 8, 6, 5))
        given = {"readings": {r.key: {"v": _typed(r.value), "unit": r.unit, "label": r.label}
                              for r in found},
                 "images": {}, "now": [2026, 11, 8, 6, 5, 0, 0]}  # fmt: skip
        return given, snapshot

    frames = [(900.0, False), (0.0, True), (0.0, False)]  # (it ran; it stands after; alone)
    cases, expected = [], []
    for rpm, after in frames:
        given, snapshot = readings(rpm)
        if not after:
            lists._RAN.clear()
        theme = load_theme(folder)
        frame, boxes = Renderer(theme, preview=True).render(snapshot, now=1000.0)
        expected.append(frame)
        cases.append({"folder": str(folder), "snapshot": given, "dither": False, "after": after})
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for got, want in zip(ours["frames"], expected, strict=True):
        assert got["sha256"] == hashlib.sha256(want.tobytes()).hexdigest()
    assert expected[1].tobytes() != expected[2].tobytes()  # (the one that ran shows)


# (the sides of a fan's box: none, less than the least fan, odd and even, the lists' sizes)
FAN_SIDES = [-6, 0, 1, 5, 7, 8, 9, 12, 13, 17, 23, 34, 47, 48, 49, 57, 66, 76, 100, 151]


def _fan(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A fan: of every size, square or not (the fan and its glow in the middle of its box, a
    pixel more to one side where the room is odd), in its ring, its lit square frame or none,
    of every look, in the theme's colour, one of its own or the look's own, at a most of its own
    (past a float's range among them)."""
    w, h = size
    widget: dict[str, Any] = {
        "type": "fan", "id": rng.choice([f"f{i}", f"fan-{i}", f"Lüfter {i}", f"gpu.fan.{i}"]),
        "x": rng.randrange(-40, w), "y": rng.randrange(-40, h),
        "w": rng.choice(FAN_SIDES), "h": rng.choice(FAN_SIDES),
    }  # fmt: skip
    if rng.random() < 0.4:  # (square, as most are)
        widget["h"] = widget["w"]
    if rng.random() < 0.85:
        widget["sensor"] = rng.choice(["gpu.fan", "gpu.fan", "fan.1", "fan.2", "fan.cpu",
                                       "fan.gpu", "pump.fan", "temp.cpu", "fan.none"])  # fmt: skip
    if rng.random() < 0.7:
        widget["max_rpm"] = rng.choice([1, 0.5, 30, 1000, 2000, 2000.0, 2400.5, 4500, 1e-9,
                                        1e300, math.inf, math.nan, 2**60 + 1, 10**400])  # fmt: skip
    if rng.random() < 0.6:
        widget["color"] = rng.choice([_colour(rng), _colour(rng), "@accent", "@text2",
                                      _hex(rng, True)])  # fmt: skip
    if rng.random() < 0.6:
        widget["frame"] = rng.choice(["ring", "square", "square", "none"])
    if rng.random() < 0.6:
        widget["look"] = rng.choice(["auto", "noctua-g2", "noctua-a12x25", "noctua-a14", "libre"])
    if rng.random() < 0.5:
        widget["paint"] = rng.choice(["auto", "theme", "own"])
    if rng.random() < 0.1:
        widget["opacity"] = rng.choice([0.5, 0.0, 0.25])
    return widget


# (the sensors of the fans here: the widgets' and the lists')
FAN_SENSORS = ["gpu.fan", "fan.1", "fan.2", "fan.3", "fan.cpu", "fan.gpu", "fan.gpu1", "fan.amd",
               "pump.fan", "temp.cpu"]  # fmt: skip


def _fan_choices(rng: random.Random) -> Any:
    """What a person may choose for the fans (fan.Choices, config.toml's [hardware]): a look and
    colours for all or none, and for some fans by their sensor (none chosen among them)."""
    from libre_panel.render import fan

    looks, paints = ["", *FAN_LOOKS], ["", "theme", "own"]
    return fan.Choices(
        rng.choice(looks), rng.choice(paints),
        {key: rng.choice(looks) for key in rng.sample(FAN_SENSORS, rng.randrange(0, 6))},
        {key: rng.choice(paints) for key in rng.sample(FAN_SENSORS, rng.randrange(0, 6))},
    )  # fmt: skip


def _choices_given(choices: Any) -> dict[str, Any]:
    """fan.Choices as lp-check takes it."""
    return {"look": choices.look, "paint": choices.paint, "looks": dict(choices.looks),
            "paints": dict(choices.paints)}  # fmt: skip


def _fan_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _fan(rng, i, size) if rng.random() < 0.75 else _card_or_text(rng, i, size)


def _fan_widget_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The fans' readings as the lists' (none, standing, turning, past the most, no number),
    now and then one an int: past a float's range, or one a float rounds."""
    given, snapshot = _fan_snapshot(rng)
    if rng.random() < 0.25:
        key = rng.choice(["gpu.fan", "fan.1", "fan.cpu"])
        r = Reading(key, rng.choice([10**400, -(10**400), 2**60 + 1, 1800]), "rpm", "")
        snapshot.readings[key] = r
        given["readings"][key] = {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                                  "origin": r.origin}  # fmt: skip
    return given, snapshot


def test_frames_of_fans_as_python_draws_them(tmp_path: Path) -> None:
    # (each fan as a still frame shows it, Rotor.settle: where its id puts its blades, its blur
    # and light by its speed, its ring as full; laid in the middle of its box, with its effects)
    drawn = _compare_frames(tmp_path, 0xFA46, 300, _fan_or_more, _fan_widget_snapshot)
    assert drawn > 270, drawn  # (289)


def test_fans_frame_after_frame_as_python_keeps_them(tmp_path: Path) -> None:
    # (one renderer drawing its fans frame after frame as their readings change: each drawn anew
    # where its blur, its light or its ring shows otherwise, kept where they show the same)
    rng = random.Random(0xFA48)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _fan)
        steps: list[tuple[Snapshot, float]] = []
        given = []
        for _ in range(rng.randrange(2, 8)):
            readings, snapshot = _fan_widget_snapshot(rng)
            steps.append((snapshot, 1000.0))
            given.append({"snapshot": readings, "now": 1000.0})
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        try:
            expected.append(_python_steps(folder, steps))
        except ThemeError:
            expected.append(None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                continue
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            drawn += 1
    assert drawn > 180, drawn


def test_fans_with_glow_and_shadow_within_a_level(tmp_path: Path) -> None:
    # (a fan casts its light as a card does: blurred in floating point, now and then a pixel a
    # level apart; at every size the lists draw them, in its ring and in none)
    import numpy as np

    found = [Reading("gpu.fan", 1200.0, "rpm", "GPU"), Reading("fan.1", 0.0, "rpm", "Front"),
             Reading("fan.2", 2900.0, "rpm", "Back")]  # fmt: skip
    snapshot = Snapshot(readings={r.key: r for r in found}, now=datetime(2026, 11, 8, 6, 5))
    given = {"readings": {r.key: {"v": _typed(r.value), "unit": r.unit, "label": r.label}
                          for r in found},
             "images": {}, "now": [2026, 11, 8, 6, 5, 0, 0]}  # fmt: skip
    looks = [{"glow": 0.7, "glow_radius": 6, "sensor": "gpu.fan"},
             {"shadow": "#000000a0", "shadow_blur": 4, "opacity": 0.8, "sensor": "fan.1"},
             {"glow": 1, "shadow": "#102030", "frame": "none", "sensor": "fan.2"},
             {"glow": 0.3, "glow_radius": 14, "frame": "none", "sensor": "fan.none"}]  # fmt: skip
    cases, expected = [], []
    for n, side in enumerate([8, 13, 23, 34, 48, 49, 57, 76, 100, 151]):
        folder = tmp_path / f"t{side}"
        folder.mkdir()
        widgets = [{"type": "fan", "id": f"f{i}", "x": 20 + i * 170, "y": 24, "w": side,
                    "h": side + i % 2, "color": "#13e5d7", **look}
                   for i, look in enumerate(looks)]  # fmt: skip
        data = {"format": THEME_FORMAT, "name": "fans", "display": {"width": 690, "height": 220},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        dither = n % 2 == 1
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither, "bytes": True})
        expected.append(_python_frame(folder, snapshot, dither))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    differ = total = 0
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        assert "frame" in want and "frame" in got, (case["folder"], got, want)
        apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
        assert apart.max() <= 1, case["folder"]
        differ += int((apart.max(axis=2) > 0).sum())
        total += apart.shape[0] * apart.shape[1]
        assert got["boxes"] == want["boxes"], case["folder"]
    assert differ <= total // 10000, differ


def test_frames_of_lists_as_python_draws_them(tmp_path: Path) -> None:
    # (readings picked by key and pattern as fnmatch matches them, in natural order, left
    # out, named and sorted as lists.pick does; as rows, bars, columns and tiles, with their
    # details, rules and the largest value filling the bar where the range is none)
    assert _compare_frames(tmp_path, 0x7157, 200, _list_or_more, _list_snapshot) > 150


# -- the moon ----------------------------------------------------------------------------


def test_tanh_as_numpy_works_it_out() -> None:
    # (numpy's float32 tanh is its own, SVML's converted: a polynomial of degree six on each of
    # 32 intervals of |x|, fused; 1 past the last)
    import numpy as np

    rng = np.random.default_rng(0x7A4)
    bits = rng.integers(0, 2**32, 200_000, dtype=np.uint64).astype(np.uint32)
    x = np.concatenate([bits.view(np.float32), rng.uniform(-12, 12, 150_000).astype(np.float32),
                        rng.uniform(-0.3, 0.3, 50_000).astype(np.float32)])  # fmt: skip
    # (each interval's start and the floats either side of it, where |x| changes polynomial)
    starts = np.array([0x3D400000 + (k << 21) for k in range(32)], np.uint32)
    edges = np.concatenate([starts - 1, starts, starts + 1, starts | 0x80000000])
    special = [0.0, -0.0, 1e-30, -1e-30, 9.0, 9.010913, -9.010913, 1e30, math.inf, -math.inf,
               math.nan]  # fmt: skip
    x = np.concatenate([x, edges.view(np.float32), np.array(special, np.float32)])
    want = np.tanh(x)
    [got] = run([{"op": "np_tanh", "bits": x.view(np.uint32).tolist()}])
    ours = np.array(got["bits"], np.uint32).view(np.float32)
    same = (ours.view(np.uint32) == want.view(np.uint32)) | (np.isnan(ours) & np.isnan(want))
    assert same.all(), x[~same][:5]


def test_the_moon_s_face_as_numpy_makes_it() -> None:
    # (numpy's default_rng(1969) and its draws; noise of bicubic grids; the seas, craters and
    # rays in float32 or float64 where moon.py's numbers are numpy's doubles, with the C
    # library's exp, pow, hypot and atan2 or numpy's own exp; np.convolve's sums, the ends as
    # OpenBLAS adds them; np.gradient: the same to the bit)
    import numpy as np

    [got] = run([{"op": "moon_face"}])
    for name, want in zip(("albedo", "gx", "gy"), _moon_face(), strict=True):
        ours = np.frombuffer(base64.b64decode(got[name]["data"]), "<f4").reshape(want.shape)
        assert ours.tobytes() == want.astype("<f4").tobytes(), name


MOON_PHASES = [0.0, 0.0005, 0.001, 0.004, 0.0041, 0.1, 0.25, 0.36, 0.4995, 0.5, 0.5004, 0.6,
               0.75, 0.9, 0.996, 0.9961, 0.9995, 0.99999, 1.0, 1.25, -0.2, 3.75, -1e-9, 1e300,
               0.123456, 0.6666]  # fmt: skip


def test_moon_pictures_as_moon_py_makes_them() -> None:
    # (the disc lit at each phase, north and south, its night side in a colour or a faint
    # earthshine, at sizes from a pixel up; the halo at each strength; the two together, the
    # halo breathing at a few moments)
    import numpy as np

    rng = random.Random(0x300)
    cases, expected = [], []
    for size in [1, 2, 3, 5, 8, 9, 13, 24, 31, 64, 100, 127, 200]:
        for _ in range(6):
            phase = rng.choice(MOON_PHASES)
            colour = (rng.randrange(256), rng.randrange(256), rng.randrange(256), 255)
            night = rng.choice([None, (40, 50, 70, 255), (rng.randrange(256), 0, 255, 255)])
            mirror = rng.random() < 0.5
            t = rng.choice([None, None, 0.0, 1.75, 3.5, 1e6 + 0.1])
            strength = rng.choice([0.0, 0.25, 1 / 3, 0.4, 1.0, 1.5])
            base = {"size": size, "phase": phase, "colour": list(colour[:3]),
                    "night": list(night[:3]) if night else None, "mirror": mirror}  # fmt: skip
            cases += [{**base, "what": "disc"}, {**base, "what": "halo", "strength": strength},
                      {**base, "what": "picture", "t": t}]  # fmt: skip
            expected += [moon.disc(size, phase, colour, night, mirror),
                         moon.halo(size, colour[:3], strength),
                         moon.picture(size, phase, colour, night, mirror, t)]  # fmt: skip
    [got] = run([{"op": "moon", "cases": cases}])
    for case, ours, want in zip(cases, got["pictures"], expected, strict=True):
        assert ours["size"] == list(want.size), case
        assert np.array_equal(np.asarray(picture(ours)), np.asarray(want)), case


MOON_SENSORS = ["moon.phase", "moon.phase", "other.phase", "nope"]


def _moon(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A moon: of any size, in either look (or the person's), lit in a colour of its own, the
    palette's or the cover's, its night side in another or none, as seen north or south."""
    w, h = size
    widget: dict[str, Any] = {"type": "moon", "id": f"m{i}", "x": rng.randrange(-20, w),
                              "y": rng.randrange(-20, h),
                              "size": rng.choice([0, 4, 8, 9, 12, 16, 23, 24, 40, 64, 97, 128]),
                              "color": rng.choice([_colour(rng), "@accent", "#f2efe6", "@cover"]),
                              "mirror": rng.random() < 0.4}  # fmt: skip
    if rng.random() < 0.7:
        widget["color2"] = rng.choice([_colour(rng), None, "@surface", "#2b3242"])
    if rng.random() < 0.6:
        widget["sensor"] = rng.choice(MOON_SENSORS)
    if rng.random() < 0.7:
        widget["pack"] = rng.choice(["auto", "classic", "vivid", "classic"])
    if rng.random() < 0.15:
        widget["opacity"] = rng.choice([0.5, 0.0])
    return widget


def _moon_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _moon(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def _moon_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The moon's phase (and another's): any number, what is none, a cover for "@cover"."""
    readings: dict[str, Any] = {}
    if rng.random() < 0.9:
        readings["moon.phase"] = rng.choice([*MOON_PHASES, *MOON_PHASES, 0, 1, 7, True, None,
                                             "0.5", math.nan, math.inf])  # fmt: skip
    if rng.random() < 0.5:
        readings["other.phase"] = rng.random()
    images = {}
    if rng.random() < 0.4:
        side = (rng.randrange(20, 90), rng.randrange(20, 90))
        images["media.cover"] = noise(rng, rng.choice(["RGB", "RGBA"]), side)
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()}, images=images)
    given = {"readings": {k: _typed(v) for k, v in readings.items()},
             "images": {k: image(v) for k, v in images.items()}}  # fmt: skip
    return given, snapshot


def test_frames_of_moons_as_python_draws_them(tmp_path: Path) -> None:
    # (a moon standing, in either look: the classic one's disc and its lit side bounded by the
    # terminator, the vivid one lit as it looks, with its halo as bright as it is full; of any
    # phase a reading gives, north and south, in its colours or the palette's or the cover's;
    # the look its own or the one the person chose)
    looks = ("classic", "vivid")
    assert _compare_frames(tmp_path, 0x300D, 160, _moon_or_more, _moon_snapshot, looks=looks) > 140


def _moon_module_theme(rng: random.Random, folder: Path) -> None:
    """A theme of moon and sun modules (and others) on a grid, in a look's palette and style,
    its glow off."""
    from libre_panel import design
    from libre_panel.theme import modules

    folder.mkdir()
    w, h = rng.choice([(480, 320), (320, 240), (800, 480), (480, 128), (240, 320), (160, 128)])
    look = design.LOOKS[rng.choice(list(design.LOOKS))]
    style = dict(look["style"])
    if rng.random() < 0.6:
        style.update(card=rng.choice(modules.CARDS), radius=rng.randrange(0, 101))
    style["glow"] = 0.0
    grid = {"columns": rng.choice([1, 2, 3, 4]), "rows": rng.choice([1, 2, 3]),
            "gap": rng.choice([0, 8]), "margin": rng.choice([0, 8, 24])}  # fmt: skip
    widgets = []
    for i in range(rng.randrange(1, 4)):
        kind = rng.choice(["moon", "moon", "sun", "text", "clock"])
        at = {"col": rng.randrange(grid["columns"]), "row": rng.randrange(grid["rows"]),
              "cols": rng.randrange(1, grid["columns"] + 1),
              "rows": rng.randrange(1, grid["rows"] + 1)}  # fmt: skip
        module: dict[str, Any] = {"type": "module", "id": f"m{i}", "module": kind, **at,
                                  "finish": "flat"}  # fmt: skip
        if rng.random() < 0.4:
            module["title"] = rng.choice(["", "Mond", "Tonight"])
        widgets.append(module)
    data = {"format": THEME_FORMAT, "name": "moons",
            "display": {"model": "custom", "width": w, "height": h,
                        "orientation": "portrait" if h > w else "landscape"},
            "palette": dict(look["palette"]), "grid": grid, "style": style,
            "widgets": widgets}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


def test_frames_of_moon_modules_as_python_draws_them(tmp_path: Path) -> None:
    # (the moon module and the sun's, the moon beside or below its name, how much of it is lit
    # and the days to the next full and new moon; with a place and without, by the demo's
    # readings at phases of every kind)
    import dataclasses

    rng = random.Random(0x3010)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        _moon_module_theme(rng, folder)
        snapshot = dataclasses.replace(_demo(), history={})
        readings = dict(snapshot.readings)
        readings["moon.phase"] = Reading("moon.phase", rng.choice(MOON_PHASES), "", "Moon phase")
        if rng.random() < 0.3:  # (no place: no sun's times, the moon alone)
            readings = {k: v for k, v in readings.items() if not k.startswith("sun.")}
        snapshot = dataclasses.replace(snapshot, readings=readings)
        cases.append({"folder": str(folder), "snapshot": _snapshot_given(snapshot)})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if "unported" in got:
            continue
        assert "frame" in want, (n, got, want)
        assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), n
        assert got["boxes"] == want["boxes"], n
        drawn += 1
    assert drawn > 40


def test_moons_too_large_are_not_drawn_here(tmp_path: Path) -> None:
    # (a moon of more than 4096 px a side, lit at twice that: Rust says it does not draw one)
    folder = tmp_path / "large"
    folder.mkdir()
    data = {"format": THEME_FORMAT, "name": "large", "display": {"width": 64, "height": 48},
            "widgets": [{"type": "moon", "id": "m", "x": 0, "y": 0, "size": 4097}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    snapshot = {"readings": {"moon.phase": _typed(0.3)}, "images": {}}
    [ours] = run([{"op": "render_frame", "frames": [{"folder": str(folder), "snapshot": snapshot}],
                   "fonts_dir": str(fonts.FONT_DIR)}])  # fmt: skip
    assert ours["frames"][0].get("unported") == "moons larger than 4096 px"


# -- the earth ---------------------------------------------------------------------------


# (a Python that draws the earth with numpy's float32 arcsin, arctan2 and power from the C
# library: numpy on Linux with AVX-512 takes SVML's, a unit or two in the last place apart)
_EARTH_PICTURES = """
import json, sys
from pathlib import Path
from libre_panel.render import earth
cases, out = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])
for n, c in enumerate(cases):
    place = None if c["place"] is None else tuple(c["place"])
    image = earth.picture(c["size"], c["t"], tuple(c["centre"]), place, tuple(c["air"]),
                          tuple(c["mark"]), c["clouds"], c["lights"])
    (out / f"{n}.raw").write_bytes(image.tobytes())
"""


def _avx512() -> list[str]:
    """numpy's targets of AVX-512 this processor has (SVML's way where it takes them)."""
    try:
        from numpy._core._multiarray_umath import __cpu_dispatch__, __cpu_features__
    except ImportError:  # (numpy before 2)
        from numpy.core._multiarray_umath import __cpu_dispatch__, __cpu_features__
    return [f for f in __cpu_dispatch__ if __cpu_features__.get(f) and f not in ("X86_V3",)
            and (f.startswith("AVX512") or f == "X86_V4")]  # fmt: skip


def _earth_pictures(tmp_path: Path, cases: list[dict[str, Any]], libm: bool) -> list[bytes]:
    """earth.picture of each case by Python, in a process of its own: numpy's float32 functions
    the C library's (`libm`), else as numpy takes them here."""
    folder = tmp_path / ("libm" if libm else "here")
    folder.mkdir()
    (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    env = dict(os.environ)
    if libm and _avx512():
        env["NPY_DISABLE_CPU_FEATURES"] = " ".join(_avx512())
    subprocess.run([sys.executable, "-c", _EARTH_PICTURES, str(folder / "cases.json"),
                    str(folder)], env=env, check=True)  # fmt: skip
    return [(folder / f"{n}.raw").read_bytes() for n in range(len(cases))]


def _earth_case(rng: random.Random) -> dict[str, Any]:
    """A globe: of every size, at a moment of the years round now (an equinox, a solstice), seen
    from above a place anywhere (the poles among them), a place marked or none, in the air's
    colour and your place's or others, with the clouds and the lights or without them."""
    # (and the sizes where libjpeg reads the maps at another scale: 1/8, 1/4, 1/2, whole)
    size = rng.choice([8, 9, 16, 17, 40, 47, 48, 64, 94, 95, 100, 160, 161, 189, 190, 240,
                       396, 480])  # fmt: skip
    t = rng.choice([1_760_000_000.0, 1_774_094_400.0, 1_782_000_000.5, 1_766_275_200.0,
                    rng.uniform(1.6e9, 2.0e9), rng.uniform(1.6e9, 2.0e9)])  # fmt: skip
    centre = rng.choice([(20.0, 0.0), (20.0, 15.0), (48.1, 11.6), (-33.9, 151.2), (90.0, 0.0),
                         (-90.0, 45.0), (0.0, 180.0), (0.0, -180.0),
                         (rng.uniform(-90, 90), rng.uniform(-180, 180))])  # fmt: skip
    place = rng.choice([None, centre, (rng.uniform(-90, 90), rng.uniform(-180, 180)),
                        (centre[0] + 60, centre[1]), (89.9, 0.0)])  # fmt: skip
    return {"size": size, "t": t, "centre": list(centre),
            "place": None if place is None else list(place),
            "air": rng.choice([[107, 173, 255], [rng.randrange(256) for _ in range(3)]]),
            "mark": rng.choice([[255, 210, 122], [rng.randrange(256) for _ in range(3)]]),
            "clouds": rng.random() < 0.8, "lights": rng.random() < 0.8}  # fmt: skip


def _rust_earths(cases: list[dict[str, Any]]) -> list[Any]:
    """The same pictures by Rust (lp-check's earth_picture: the doubles as their bits)."""
    asked = [{**c, "t": _bits(c["t"]), "centre": [_bits(v) for v in c["centre"]],
              "place": None if c["place"] is None else [_bits(v) for v in c["place"]]}
             for c in cases]  # fmt: skip
    [got] = run([{"op": "earth_picture", "fonts_dir": str(fonts.FONT_DIR), "cases": asked}])
    return got["pictures"]


def test_the_earth_as_python_draws_it(tmp_path: Path) -> None:
    # (the globe as earth.picture makes it: the maps drafted by libjpeg to the width the round
    # needs, the sun where the sunrise equation puts it, the day, the night and its lights, the
    # clouds, the glint, the dusk, the air at the rim, your place: to the bit where numpy takes
    # its float32 functions from the C library; where it takes SVML's (AVX-512), as near as
    # Python's own two ways come to each other)
    import numpy as np

    rng = random.Random(0xEA27)
    cases = [_earth_case(rng) for _ in range(80)]
    # (places near the rim, where the mark fades: 85, 87 and 88 degrees from the middle, the
    # last hidden; of a small globe and a large one)
    for size, lon in ((64, 30.0), (396, -120.0)):
        for away in (85.0, 87.0, 88.0, -86.5):
            cases.append({**_earth_case(rng), "size": size, "centre": [0.0, lon],
                          "place": [0.0, lon + away]})  # fmt: skip
    ours = _rust_earths(cases)
    wanted = _earth_pictures(tmp_path, cases, libm=True)
    here = _earth_pictures(tmp_path, cases, libm=False) if _avx512() else wanted
    apart = most = 0
    for case, got, want, there in zip(cases, ours, wanted, here, strict=True):
        assert "error" not in got, (case, got)
        assert picture(got).tobytes() == want, case
        size = case["size"]
        a = np.frombuffer(want, np.uint8).reshape(size, size, 4).astype(int)
        b = np.frombuffer(there, np.uint8).reshape(size, size, 4).astype(int)
        far = np.abs(a - b).max(axis=2)
        assert (far > 0).sum() <= size * size // 2000 + 2, case
        apart, most = apart + int((far > 0).sum()), max(most, int(far.max()))
    assert most <= 48, (apart, most)


_TURNING_GLOBES = """
import hashlib, json, sys
from pathlib import Path
from libre_panel.render import earth
globes, out = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])
answers = []
for n, g in enumerate(globes):
    globe = earth.Turning(g["size"], g["lat"], tuple(g["air"]), tuple(g["mark"]), g["clouds"],
                          g["lights"])
    said = []
    for k, c in enumerate(g["calls"]):
        try:
            if c[0] == "light":
                globe.light(c[1])
                said.append(None)
            elif c[0] == "frame":
                image = globe.frame(c[1], None if c[2] is None else tuple(c[2]))
                (out / f"{n}_{k}.raw").write_bytes(image.tobytes())
                said.append("frame")
            elif c[0] == "step_of":
                said.append(list(globe.step_of(c[1], c[2], c[3])))
            elif c[0] == "map":
                if globe.map is None:
                    raise RuntimeError("light the map first")
                said.append(hashlib.sha256(globe.map.tobytes()).hexdigest())
            elif c[0] == "tables":
                said.append(hashlib.sha256(b"".join(globe.tables())).hexdigest())
            else:
                said.append(globe.step_at(c[1]))
        except Exception as error:
            said.append({"error": str(error)})
    answers.append({"width": globe.width, "said": said})
(out / "answers.json").write_text(json.dumps(answers))
"""


def _turning_case(rng: random.Random) -> dict[str, Any]:
    """A turning globe and what is asked of it: of every size, seen from any latitude (beyond
    60 degrees too), in any colours, with the clouds and the lights or without; lit for a
    moment, its frames at steps a pixel, a few and half a turn on, past its round and before
    nought, your place marked or not (near the rim among them), lit anew now and then; its
    steps at times and turns of every length (a turn less than a second, of none, at an
    infinite time), the step with a longitude in the middle."""
    size = rng.choice([8, 9, 16, 40, 47, 64, 76, 95, 160, 189, 240, 360])
    lat = rng.choice([20.0, -30.0, 30.0, 0.0, rng.uniform(-30, 30), 75.0, -61.5,
                      rng.uniform(-90, 90)])  # fmt: skip
    width = max(64, 2 * math.ceil(math.pi * max(8, size) * 0.86 / 2))
    t = rng.choice([1_760_000_000.0, 1_774_094_400.0, 1_766_275_200.0, rng.uniform(1.6e9, 2e9)])
    calls: list[list[Any]] = [["tables"], ["light", t], ["map"]]
    step = rng.randrange(width)
    for _ in range(rng.randrange(3, 9)):
        place = rng.choice([None, (rng.uniform(-90, 90), rng.uniform(-180, 180)), (lat, 0.0),
                            (0.0, 85.0), (48.1, 11.6)])  # fmt: skip
        calls.append(["frame", step, None if place is None else list(place)])
        step += rng.choice([1, 1, 2, max(1, width // 180), width // 90, width // 2, -1,
                            width, -width - 3, rng.randrange(width)])  # fmt: skip
        if rng.random() < 0.15:
            t += rng.choice([60.0, 600.0, 43_200.0])
            calls += [["light", t], ["map"]]
    for _ in range(6):
        calls.append(["step_of", rng.choice([rng.uniform(0, 1e6), 0.0, 1234.5, -1234.56,
                                             rng.uniform(-1e5, 0)]),
                      rng.choice([180, 60, 0.5, 1.0, 1e-3, 86400.0, 3.7, 180.0]),
                      rng.randrange(-width, width)])  # fmt: skip
    # (and a longitude halfway between two steps: Python's round goes to the even one)
    for lon in (rng.uniform(-180, 180), rng.uniform(-180, 180), 0.0, 180.0, -180.0, 11.6,
                0.5 * 360.0 / width, -3.5 * 360.0 / width, 6.5 * 360.0 / width):  # fmt: skip
        calls.append(["step_at", lon])
    return {"size": size, "lat": lat,
            "air": rng.choice([[107, 173, 255], [rng.randrange(256) for _ in range(3)]]),
            "mark": rng.choice([[255, 210, 122], [rng.randrange(256) for _ in range(3)]]),
            "clouds": rng.random() < 0.8, "lights": rng.random() < 0.8,
            "calls": calls}  # fmt: skip


def test_turning_globes_as_python_turns_them(tmp_path: Path) -> None:
    # (earth.Turning: the disc's pixels and where each lies on the map, the map lit whole for a
    # moment (drafted by libjpeg, boxed to the round, each pixel's sun from numpy's own float32
    # sines and cosines), the whole map and a frame at a step with the air laid anew every two
    # degrees and kept between, your place marked; the steps of the turn at a time and with a
    # longitude in the middle, and Python's errors: to the bit where numpy takes its float32
    # functions from the C library)
    rng = random.Random(0x7A1E)
    globes = [_turning_case(rng) for _ in range(30)]
    # (where each pixel of the disc lies on the map, of globes of many sizes seen from many
    # latitudes: a pixel at a row's or a column's edge comes out as numpy rounds it)
    for size in [*range(8, 64, 3), *range(64, 700, 37), 1000]:
        globes.append({**_turning_case(rng), "size": size, "lat": rng.uniform(-60, 60),
                       "calls": [["tables"]]})  # fmt: skip
    # (a frame before the map is lit fails as Python's does; steps of no turn, of an endless
    # time, of a time not a number)
    globes.append({**_turning_case(rng), "size": 40,
                   "calls": [["frame", 0, None], ["light", 1_760_000_000.0], ["frame", 3, None],
                             ["step_of", 1000.0, math.inf, 0], ["step_of", math.inf, 60, 0],
                             ["step_of", math.nan, 60, 0], ["step_at", math.inf],
                             ["step_at", math.nan]]})  # fmt: skip
    folder = tmp_path / "turning"
    folder.mkdir()
    (folder / "globes.json").write_text(json.dumps(globes), encoding="utf-8")
    env = dict(os.environ)
    if _avx512():
        env["NPY_DISABLE_CPU_FEATURES"] = " ".join(_avx512())
    subprocess.run([sys.executable, "-c", _TURNING_GLOBES, str(folder / "globes.json"),
                    str(folder)], env=env, check=True)  # fmt: skip
    wanted = json.loads((folder / "answers.json").read_text(encoding="utf-8"))

    def bits(call: list[Any]) -> list[Any]:
        if call[0] in ("map", "tables"):
            return call
        if call[0] in ("light", "step_at"):
            return [call[0], _bits(call[1])]
        if call[0] == "frame":
            return [*call[:2], None if call[2] is None else [_bits(v) for v in call[2]]]
        return [call[0], _bits(call[1]), _bits(float(call[2])), call[3]]

    asked = [{**g, "lat": _bits(g["lat"]), "calls": [bits(c) for c in g["calls"]]}
             for g in globes]  # fmt: skip
    [got] = run([{"op": "turning_globe", "fonts_dir": str(fonts.FONT_DIR), "globes": asked}])
    frames = 0
    for n, (g, ours, want) in enumerate(zip(globes, got["globes"], wanted, strict=True)):
        assert ours["width"] == want["width"], n
        for k, (call, a, b) in enumerate(zip(g["calls"], ours["answers"], want["said"],
                                             strict=True)):  # fmt: skip
            if b == "frame":
                raw = (folder / f"{n}_{k}.raw").read_bytes()
                assert picture(a).tobytes() == raw, (n, k, call)
                frames += 1
            else:
                assert a == b, (n, k, call, a, b)
    assert frames > 100, frames


T = TypeVar("T")


_TURNING_REPLAY = """
import json, sys
from pathlib import Path
from libre_panel.render import earth
logs, out = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])
for n, log in enumerate(logs):
    size, lat, air, mark, clouds, lights = log["args"]
    globe = earth.Turning(size, lat, tuple(air), tuple(mark), clouds, lights)
    k = 0
    for call in log["calls"]:
        if call[0] == "lit":
            try:
                globe.use(call[1], globe.lit(call[1]))
            except Exception:
                pass
        else:
            place = None if call[2] is None else tuple(call[2])
            (out / f"{n}_{k}.raw").write_bytes(globe.frame(call[1], place).tobytes())
            k += 1
"""


def _exact_globes(tmp_path: Path, draw: Callable[[], T]) -> T:
    """What `draw` draws, its globes as Python makes them where numpy's float32 functions are
    the C library's: where numpy takes SVML's here (AVX-512), the globes it asks earth.picture
    for are made so in a process of their own, and drawn again with them (the globe itself is
    test_the_earth_as_python_draws_it's); and the frames of turning globes: each globe's lights
    and frames, in their order, made again so in a process of their own (each frame laid on
    the air the globe kept), and drawn again with them."""
    from PIL import Image

    from libre_panel.render import earth

    if not _avx512():
        return draw()
    real, asked = earth.picture, []
    turning = earth.Turning
    init, lit, frame = turning.__init__, turning.lit, turning.frame
    logs: list[dict[str, Any]] = []
    made_turns: dict[tuple[int, int], Any] = {}
    serial = [0]

    def begin(self: Any, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)
        self._serial, self._frames = serial[0], 0
        serial[0] += 1
        if not made_turns:  # (the first time: what each globe does is written down)
            logs.append({"args": [self.size, self.lat, list(self.air), list(self.mark),
                                  self.clouds, self.lights], "calls": []})  # fmt: skip

    def lighting(self: Any, t: float) -> Any:
        if not made_turns:
            logs[self._serial]["calls"].append(["lit", t])
        return lit(self, t)

    def framing(self: Any, step: int, place: Any) -> Image.Image:
        k, self._frames = self._frames, self._frames + 1
        if made_turns:
            return made_turns[(self._serial, k)].copy()
        logs[self._serial]["calls"].append(["frame", step, None if place is None
                                            else list(place)])  # fmt: skip
        return Image.new("RGBA", (self.size, self.size))

    turning.__init__, turning.lit, turning.frame = begin, lighting, framing
    try:
        return _exact_still_globes(tmp_path, draw, earth, real, asked, logs, made_turns, serial)
    finally:
        turning.__init__, turning.lit, turning.frame = init, lit, frame


def _exact_still_globes(tmp_path: Path, draw: Callable[[], T], earth: Any, real: Any,
                        asked: list[Any], logs: list[dict[str, Any]],
                        made_turns: dict[tuple[int, int], Any],
                        serial: list[int]) -> T:  # fmt: skip
    """_exact_globes' two drawings: the first writes down the globes asked for, the second
    draws with them made where numpy's float32 functions are the C library's."""
    from PIL import Image

    def key(*args: Any) -> tuple[Any, ...]:
        size, t, centre, place, air, mark, clouds, lights = args
        return (max(8, int(size)), t, tuple(centre), None if place is None else tuple(place),
                tuple(air), tuple(mark), bool(clouds), bool(lights))  # fmt: skip

    def record(*args: Any) -> Image.Image:
        asked.append(key(*args))
        return Image.new("RGBA", (asked[-1][0],) * 2)

    earth.picture = record
    try:
        draw()
    finally:
        earth.picture = real
    if logs:  # (the turning globes' frames, made again in a process of their own)
        folder = tmp_path / "turns"
        folder.mkdir()
        (folder / "logs.json").write_text(json.dumps(logs), encoding="utf-8")
        env = dict(os.environ, NPY_DISABLE_CPU_FEATURES=" ".join(_avx512()))
        subprocess.run([sys.executable, "-c", _TURNING_REPLAY, str(folder / "logs.json"),
                        str(folder)], env=env, check=True)  # fmt: skip
        for n, log in enumerate(logs):
            side = max(8, int(log["args"][0]))
            frames = [c for c in log["calls"] if c[0] == "frame"]
            for k in range(len(frames)):
                raw = (folder / f"{n}_{k}.raw").read_bytes()
                made_turns[(n, k)] = Image.frombytes("RGBA", (side, side), raw)
        made_turns.setdefault((-1, -1), None)  # (made: the second drawing takes them)
        serial[0] = 0
    wanted = list(dict.fromkeys(asked))
    cases = [{"size": k[0], "t": k[1], "centre": list(k[2]),
              "place": None if k[3] is None else list(k[3]), "air": list(k[4]),
              "mark": list(k[5]), "clouds": k[6], "lights": k[7]} for k in wanted]  # fmt: skip
    folder = tmp_path / "globes"
    folder.mkdir()
    raws = _earth_pictures(folder, cases, libm=True)
    made = {k: Image.frombytes("RGBA", (k[0], k[0]), raw)
            for k, raw in zip(wanted, raws, strict=True)}  # fmt: skip
    earth.picture = lambda *args: made[key(*args)].copy()
    try:
        return draw()
    finally:
        earth.picture = real


# (a globe's sides: none, the least, odd and even, where libjpeg reads the maps at another
# scale (47 and 48, 94 and 95, 189 and 190), the almanacs')
EARTH_SIDES = [-5, 0, 8, 9, 16, 31, 47, 48, 64, 94, 95, 120, 160, 189, 190]


def _earth(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A globe: of every size, anywhere on the panel and over its edges, your place marked or
    not, its air and your place in any colours, with its clouds and lights or without, turning
    or not (a still frame shows a turning globe standing)."""
    w, h = size
    widget: dict[str, Any] = {"type": "earth", "id": f"e{i}", "x": rng.randrange(-40, w),
                              "y": rng.randrange(-40, h),
                              "size": rng.choice(EARTH_SIDES)}  # fmt: skip
    for key in ("color", "color2"):
        if rng.random() < 0.6:
            widget[key] = rng.choice([_colour(rng), "@accent", "#6badff", _hex(rng, True)])
    for key in ("clouds", "lights", "place"):
        if rng.random() < 0.4:
            widget[key] = rng.random() < 0.5
    if rng.random() < 0.3:
        widget["spin"] = rng.choice([0, 0.5, 2, -1])
    if rng.random() < 0.1:
        widget["opacity"] = rng.choice([0.5, 0.0, 0.25])
    return widget


def _turning_earth(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A globe that turns in a moving frame: in a turn of any time (less than a second and
    much more), or one that stands."""
    widget = _earth(rng, i, size)
    widget["spin"] = rng.choice([180, 60, 20, 5, 0.5, 1, 1e-3, 86400, 0, -1])
    return widget


def _turning_earth_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _turning_earth(rng, i, size) if rng.random() < 0.6 else _card_or_text(rng, i, size)


def _earth_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _earth(rng, i, size) if rng.random() < 0.5 else _card_or_text(rng, i, size)


def _earth_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """A moment of the years round now, its fold now and then."""
    given, snapshot = _clock_snapshot(rng)
    return _clock_moment(snapshot.now.replace(fold=int(rng.random() < 0.2)))


def _place(rng: random.Random) -> tuple[float, float] | None:
    """Your place: none (the globe faces your time zone's longitude), or anywhere."""
    return rng.choice([None, None, (48.1, 11.6), (-33.9, 151.2), (89.9, 0.0), (0.0, 180.0),
                       (rng.uniform(-90, 90), rng.uniform(-180, 180))])  # fmt: skip


def test_frames_of_the_earth_as_python_draws_them(tmp_path: Path) -> None:
    # (the globe lit for the frame's moment on the wall clock, in steps of redraw_s, seen from
    # above your place or else your time zone's longitude a little north, your place marked
    # where the widget asks, laid where the widget stands)
    drawn = _compare_frames(tmp_path, 0xEA28, 60, _earth_or_more, _earth_snapshot,
                            place_of=_place, globes=True)  # fmt: skip
    assert drawn > 45, drawn


def test_the_earth_frame_after_frame_as_python_keeps_it(tmp_path: Path) -> None:
    # (one renderer drawing its globes frame after frame: the same moment again, a part of a
    # step of redraw_s, a step, a minute, an hour, a day later: each globe lit anew where its
    # moment moved, kept where it did not)
    rng = random.Random(0xEA29)
    cases, asked = [], []
    for n in range(24):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _earth)
        now, place = _earth_snapshot(rng)[1].now, _place(rng)
        steps, given = [], []
        for _ in range(rng.randrange(2, 6)):
            readings, snapshot = _clock_moment(now)
            steps.append((snapshot, 1000.0))
            given.append({"snapshot": readings, "now": 1000.0})
            now += timedelta(seconds=rng.choice([0, 0.4, 19.9, 20, 60, 600, 3600, 86400]))
        cases.append({"folder": str(folder), "dither": False, "steps": given,
                      **({} if place is None else {"place": list(place)})})  # fmt: skip
        asked.append((folder, steps, place))

    def draw() -> list[Any]:
        out: list[Any] = []
        for folder, steps, place in asked:
            try:
                out.append(_python_steps(folder, steps, place))
            except ThemeError:
                out.append(None)
        return out

    expected = _exact_globes(tmp_path, draw)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                continue
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            drawn += 1
    assert drawn > 60, drawn


def test_globes_that_turn_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its globes frame after frame as the panels' loop does: those that
    # turn a step at a time, beginning with your place (or your time zone's longitude) in the
    # middle, the frame due at the next step (it does not move on), lit anew where the moment
    # on the wall clock moved; at a frame rate or none, the frames at any distance, back in
    # time too)
    rng = random.Random(0xEA30)
    cases, asked = [], []
    for n in range(30):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _turning_earth_or_more)
        place = _place(rng)
        now, fps = rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        moment = _earth_snapshot(rng)[1].now
        steps, frames = [], []
        for _ in range(rng.randrange(2, 9)):
            readings, snapshot = _clock_moment(moment)
            steps.append((snapshot, now))
            frames.append({"snapshot": readings, "now": now})
            gone = rng.choice([*MOVING_STEPS, 0.07, 0.3, 3.7, 60.0])
            now += gone
            moment += timedelta(seconds=rng.choice([max(0.0, gone), max(0.0, gone), 20, 600]))
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": frames,
                      **({} if place is None else {"place": list(place)})})  # fmt: skip
        asked.append((folder, steps, fps, place))
    expected = _exact_globes(tmp_path, lambda: [_python_moving(*a) for a in asked])
    drawn, _ = _compare_moving(cases, expected)
    due = sum(w.get("due") is not None for want in expected if want for w in want)
    assert drawn > 120 and due > 60, (drawn, due)


def test_the_earth_with_glow_and_shadow_within_a_level(tmp_path: Path) -> None:
    # (a globe casts its light as a card does: blurred in floating point, now and then a pixel
    # a level apart)
    import numpy as np

    looks = [{"glow": 0.15}, {"shadow": "#000000a0", "shadow_blur": 4, "opacity": 0.8},
             {"glow": 1, "glow_radius": 6, "place": False}]  # fmt: skip
    rng = random.Random(0xEA2A)
    cases, asked = [], []
    for n, (look, side) in enumerate((lk, sd) for lk in looks for sd in (16, 48, 95, 160)):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        widgets = [{"type": "earth", "id": "e0", "x": 10, "y": 8, "size": side, **look}]
        data = {"format": THEME_FORMAT, "name": "globes",
                "display": {"width": side + 40, "height": side + 30},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given, snapshot = _earth_snapshot(rng)
        place = _place(rng)
        cases.append({"folder": str(folder), "snapshot": given, "dither": False, "bytes": True,
                      **({} if place is None else {"place": list(place)})})  # fmt: skip
        asked.append((folder, snapshot, False, None, 1000.0, place))
    expected = _exact_globes(tmp_path, lambda: [_python_frame(*a) for a in asked])
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for case, got, want in zip(cases, ours["frames"], expected, strict=True):
        apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
        assert apart.max() <= 1, case["folder"]
        assert got["boxes"] == want["boxes"], case["folder"]


@pytest.mark.skipif(sys.platform == "win32", reason="the computer's zone set by TZ and tzset")
def test_the_earth_in_the_computer_s_zone_as_python_draws_it(tmp_path: Path,
                                                             local_zone: Any) -> None:  # fmt: skip
    # (the moment a globe is lit for, the frame's time as a timestamp of the computer's zone,
    # its fold where the clocks go back; without your place, the globe faces your time zone's
    # longitude: its offset now, 15 degrees an hour)
    drawn = 0
    for n, tz in enumerate(LOCAL_ZONES):
        local_zone(tz)
        changes = _local_changes(range(2024, 2028))
        snapshot_of = _near_changes(changes)
        folder = tmp_path / f"z{n}"
        folder.mkdir()
        drawn += _compare_frames(folder, 0xEA30 + n, 12, _earth_or_more, snapshot_of,
                                 place_of=lambda rng: None, globes=True)  # fmt: skip
    assert drawn > 45, drawn


# -- downloads: what loads, its progress, its speed ---------------------------------------------

# numbers a download's state may carry, and what is none: an int past a float's range (Python
# fails at it), the infinities and NaN json.loads reads, a bool, a text, a list
DOWNLOAD_NUMBERS = [0, 1, -1, 7, 59, 60, 61, 3599, 3600, 86399, 10**6, 2**53 + 1, 10**20,
                    10**308, 10**309, -(10**309), 0.0, -0.0, 0.5, 0.9999, 1.0, 1.5, 59.999,
                    3599.5, -3.5, 1e300, 1.7e308, 5e-324, math.inf, -math.inf, math.nan,
                    1048575.6, 1048576.0, 1073741823.9, 1073741824.0, 9.95 * 1073741824,
                    123456789.0, -5e9, -(2**40), -1.5e12, True, False, None, "5", [],
                    {}]  # fmt: skip
DOWNLOAD_TEXTS = ["", " ", "  x  ", "file.iso", "Ünïcødé ßtraße", "名前", "a\nb", " lead",
                  "trail ", "x" * 200, "ﬁ", 0, 1, 0.0, 2.5, True, False, None, [],
                  [1, "a"], {"k": None}, {}]  # fmt: skip


def _download_number(rng: random.Random, wild: float = 0.4) -> Any:
    """A number of a download's state: one of every kind now and then (`wild`), else a size,
    a speed or a time of the sizes a download has."""
    if rng.random() < wild:
        return rng.choice(DOWNLOAD_NUMBERS)
    kind = rng.random()
    if kind < 0.3:
        return rng.randrange(-(10**4), 10**12)
    if kind < 0.6:
        return rng.uniform(-1e3, 3e10)
    return round(rng.uniform(0, 5e9), rng.randrange(0, 4))


def _download_state(rng: random.Random, wild: float = 0.4) -> dict[str, Any]:
    """A state as the downloads service publishes one, or another sender: each field there or
    not, of the kind it should be or of another."""
    state: dict[str, Any] = {}
    if rng.random() < 0.9:
        state["phase"] = rng.choice([*download.PHASES, "downloading", "", "Done", 3, None])
    for key in ("bytes", "total", "rate", "remaining_s", "elapsed_s", "more"):
        if rng.random() < 0.75:
            state[key] = _download_number(rng, wild)
    if rng.random() < 0.85:
        state["name"] = rng.choice([*DOWNLOAD_TEXTS, "ubuntu-24.04.iso", "Game (Steam)"])
    if rng.random() < 0.7:
        state["source"] = rng.choice([*DOWNLOAD_TEXTS, "steam", "browser", "aria2"])
    if rng.random() < 0.5:
        state["estimated"] = rng.choice([True, False, 1, "yes", None])
    if rng.random() < 0.8:
        n = rng.choice([0, 1, 2, 3, 10, 60, 179, 180, 181, 400])
        base = rng.choice([rng.uniform(0, 4e7), rng.uniform(0, 1e6), rng.uniform(0, 3e4)])
        speeds = [max(0.0, base + rng.gauss(0, base / 3 + 1)) if rng.random() > wild / 4
                  else _download_number(rng, wild) for _ in range(n)]  # fmt: skip
        state["history"] = speeds if rng.random() < 0.95 else "speeds"
    return state


def test_downloads_read_as_download_py_reads_them() -> None:
    # (a state's texts, in five languages: the phase, the source and the name, a bool that is no
    # number, minus nought, a size past a float's range as Python fails at it, the infinities
    # and NaN; seconds, minutes and hours of any number; MB and GB; the last 180 speeds)
    rng = random.Random(0xD0A1)
    cases, expected = [], []
    try:
        for _ in range(2500):
            text = json.dumps(_download_state(rng))
            language = rng.choice(["en", "de", "ja", "ru", "fr"])
            i18n.set_language(language)
            try:
                facts = download.read(json.loads(text))
                want = {"facts": repr([facts.phase, facts.source, facts.name, facts.share,
                                       [list(row) for row in facts.rows], facts.amount,
                                       list(facts.history)])}  # fmt: skip
            except OverflowError as error:
                want = {"error": str(error)}
            cases.append({"state": text, "language": language})
            expected.append(want)
    finally:
        i18n.set_language("en")
    [ours] = run([{"op": "download_read", "locale": str(i18n.LOCALE_DIR), "cases": cases}])
    for n, (got, want) in enumerate(zip(ours, expected, strict=True)):
        assert got == want, (n, cases[n])
    facts = [w["facts"] for w in expected if "facts" in w]
    assert len(facts) > 1200 and len(expected) - len(facts) > 300
    for said in ["'0', 's'", "'min'", "'h'", "'GB'", "'KB/s'", "'MB/s'", "'--'", "≈ ", "  +"]:
        assert any(said in f for f in facts), said


# boxes at the edges of the card's rules: a width 2.4 to 2.5 times its height (stacked or not),
# a graph of 40 reference px or just less, one of four px, the reference's sizes and more
DOWNLOAD_BOXES = [(245, 100), (299, 122), (596, 240), (240, 100), (68, 20), (67, 20), (69, 28),
                  (8, 60), (9, 60), (7, 70), (330, 254), (400, 254), (609, 254), (596, 130),
                  (700, 130)]  # fmt: skip


def _download(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A download card: wide, stacked or too small for its parts, flat or lit, in colours of
    its own or the palette's, with a groove or none, a glow, a text for nothing downloading."""
    w, h = size
    widget: dict[str, Any] = {
        "type": "downloads", "id": f"d{i}", "x": rng.randrange(-30, w), "y": rng.randrange(-30, h),
        "w": rng.choice([0, 1, 3, 20, 60, 120, 200, 330, 400, 596, 700]),
        "h": rng.choice([0, 1, 4, 10, 30, 60, 130, 180, 254, 300]),
    }  # fmt: skip
    if rng.random() < 0.3:  # (at an edge of the layout's rules)
        widget["w"], widget["h"] = rng.choice(DOWNLOAD_BOXES)
    if rng.random() < 0.5:
        widget["finish"] = "lit"
    for key in ("color", "muted", "color2", "done"):
        if rng.random() < 0.5:
            widget[key] = rng.choice([_colour(rng), "@accent", "@text2", _hex(rng, True)])
    if rng.random() < 0.5:
        widget["background"] = rng.choice([None, _colour(rng), "@surface", _hex(rng, True)])
    if rng.random() < 0.5:
        widget["empty"] = rng.choice(["", "No download running", "Nichts lädt", "x\ny"])
    if rng.random() < 0.5:
        widget["glow"] = rng.choice([0, 0.3, 0.6, 1])
    if rng.random() < 0.25:
        widget["state"] = rng.choice(["downloads", "other.state", "missing"])
    for key in ("font", "label_font"):
        if rng.random() < 0.3:
            widget[key] = rng.choice(FONTS)
    if rng.random() < 0.1:
        widget["opacity"] = 0.5
    return widget


def _download_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _download(rng, i, size) if rng.random() < 0.75 else _card_or_text(rng, i, size)


def _download_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The downloads' state (mostly a download's, of the sizes downloads have), another's, or
    what is no dict."""
    readings: dict[str, Any] = {}
    if rng.random() < 0.9:
        digits = '{"phase": "done", "bytes": 1' + "0" * 4400 + "}"  # (past json's 4300)
        readings["downloads"] = rng.choice(["", "null", "[1, 2]", "{", "5", digits])
        if rng.random() < 0.93:
            readings["downloads"] = json.dumps(_download_state(rng, wild=0.1))
    if rng.random() < 0.3:
        readings["other.state"] = json.dumps(_download_state(rng, wild=0.1))
    snapshot = Snapshot(readings={k: Reading(k, v) for k, v in readings.items()})
    return {"readings": {k: _typed(v) for k, v in readings.items()}}, snapshot


def _download_time(rng: random.Random) -> float:
    """When a frame is drawn: where a stretch of light runs, at any point of its way."""
    return rng.choice([1000.0, 0.0, 0.6, 1.2, rng.uniform(0, 1e5), 1.7e9 + rng.random()])


def test_frames_of_downloads_as_python_draws_them(tmp_path: Path) -> None:
    # (a download card standing: its heading, source and name, its two readings, the amount, the
    # speed's curve with its halo and fading fill; its progress a flat pill or light in its
    # groove, the share it shows to its pixels, or a stretch of light where the size is not
    # known, where it is at the frame's time; wide, stacked, or too small for some of it; its
    # text where nothing downloads)
    drawn = _compare_frames(tmp_path, 0xD0A2, 300, _download_or_more, _download_snapshot,
                            when=_download_time)  # fmt: skip
    assert drawn > 240


# the downloads a card's edges are tried with: one of a known size, from a source, with a long
# name and its amount beside the second reading; one paused, of fewer MB, that waits
DOWNLOAD_EDGE_STATES = [
    {"phase": "downloading", "source": "steam", "rate": 2.5e6, "remaining_s": 75,
     "name": "Counter-Strike 2 — the workshop's maps and sounds", "bytes": 3.2e9, "total": 9.9e9,
     "estimated": True, "history": [1.5e6 + 9e5 * ((i * 7) % 11) for i in range(180)]},
    {"phase": "paused", "source": "browser", "name": "ubuntu-24.04.1-desktop-amd64.iso",
     "bytes": 3.27e8, "total": 1.028e9, "elapsed_s": 41,
     "history": [4e5 + 3e4 * ((i * 5) % 13) for i in range(120)]},
]  # fmt: skip


def test_download_cards_at_the_edges_of_what_fits(tmp_path: Path) -> None:
    # (a card of every width from 312 px to 1000, 130 high, of both downloads, and stacked, 254
    # high, from 120 to 609: where the source and the name come and go, the name is cut, and the
    # amount keeps off the second reading's unit, each a pixel apart; and of every height, wide
    # and stacked: the graph's fill fading over each height of its own)
    boxes = [*((w, 130, s) for s in (0, 1) for w in range(312, 1001)),
             *((w, 254, 0) for w in range(120, 610)), *((1000, h, 0) for h in range(30, 300)),
             *((609, h, 1) for h in range(254, 420))]  # fmt: skip
    cases, expected = [], []
    for n, (w, h, s) in enumerate(boxes):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        card = {"type": "downloads", "id": "d", "x": 0, "y": 0, "w": w, "h": h}
        data = {"format": THEME_FORMAT, "name": "edges", "display": {"width": w, "height": h},
                "background": {"color": "#101418"}, "widgets": [card]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        text = json.dumps(DOWNLOAD_EDGE_STATES[s])
        snapshot = Snapshot(readings={"downloads": Reading("downloads", text)})
        cases.append({"folder": str(folder), "snapshot": {"readings": {"downloads": _typed(text)}}})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        sha = hashlib.sha256(want["frame"].tobytes()).hexdigest()
        assert got.get("sha256") == sha, (n, boxes[n])
        assert got["boxes"] == want["boxes"], (n, boxes[n])


def _download_edge_cases(folder: Path) -> list[tuple[int, int, dict[str, Any]]]:
    """Cards where the card's texts just fit, found by download.py's own measures: of the
    smallest fonts, a source that just comes or goes beside the heading; long amounts beside the
    second reading's unit, a pixel or less off it or on it; names exactly as long as their
    room, wide after the heading and the source, and stacked."""
    from libre_panel.render import light

    folder.mkdir()
    data = {"format": THEME_FORMAT, "name": "measures", "display": {"width": 64, "height": 48},
            "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    renderer = Renderer(load_theme(folder), preview=True)

    def font(size: float) -> Any:
        return renderer.font("", max(6, round(size)))

    word = download.heading("downloading").upper()
    cases: list[tuple[int, int, dict[str, Any]]] = []
    base = {"phase": "downloading", "source": "steam", "rate": 2.5e6, "remaining_s": 75}
    for h in (10, 60):  # (the source: in 6 px fonts, where g is 0.15)
        cases += [(w, h, {**base, "name": "a.iso"}) for w in range(8, 121)]
    for total in (1e18, 2.5e18, 7e18, 4e20, 1e22, 3e24, 1e27, 5e29, 1e32, 7.7e33):
        amount = "≈ " + download.of_total(0.45 * total, total)
        for w in range(312, 1001):
            lay = download.layout(w, 130)
            g = lay.g
            unit, small = font(19 * g), font(20 * g)
            edge = download.VALUE_X * g + 6 * g + 14 * g
            off = lay.right - small.getlength(amount) - edge - unit.getlength("MB/s")
            if abs(off) <= 2 + abs(unit.getlength("MB/s") - unit.getlength("KB/s")) + 4 * g:
                cases.append((w, 130, {**base, "bytes": 0.45 * total, "total": total,
                                       "estimated": True, "name": "a.iso"}))  # fmt: skip
    small, unit = font(20), font(19)
    exact = []  # (long amounts that meet the unit to the bit, the card 130 high: g is 1)
    for k in range(18, 60):
        for m in range(10, 100):
            total = m * 10.0 ** (k - 1)
            for share, near in ((0.45, True), (0.13, False)):
                amount = ("≈ " if near else "") + download.of_total(share * total, total)
                length = small.getlength(amount) + unit.getlength("MB/s")
                if length == int(length) and 320 <= length <= 724:
                    exact.append((int(276 + length), 130, {**base, "bytes": share * total,
                                  "total": total, "estimated": near, "name": "a.iso"}))  # fmt: skip
    assert len(exact) >= 3
    cases += exact[:8]
    names = {}
    for a in range(61):
        for b in range(21):
            for c in range(4):
                name = "x" * a + "i" * b + "m" * c
                names.setdefault(font(22).getlength(name), name)
    wide = [w for w in range(596, 1000) if (download.layout(w, 130).right - download.MARGIN - (
        light.tracked_width(font(26), word, 3) + 14 + light.tracked_width(font(20), "STEAM", 2)
        + 20)) in names]  # fmt: skip
    for w in wide[:8]:
        room = (download.layout(w, 130).right - download.MARGIN - light.tracked_width(
            font(26), word, 3) - 14 - light.tracked_width(font(20), "STEAM", 2) - 20)  # fmt: skip
        cases.append((w, 130, {**base, "name": names[room]}))
    stacked = [w for w in range(330, 610) if w - 29 in names][:8]
    cases += [(w, 254, {**base, "name": names[w - 29]}) for w in stacked]
    assert len(wide) >= 3 and len(stacked) >= 3
    return cases


def test_download_cards_where_their_texts_just_fit(tmp_path: Path) -> None:
    # (the cards the card's rules decide by a pixel or less, found by download.py's measures: a
    # source beside the heading in the smallest fonts, a long amount beside the second
    # reading's unit or meeting it to the bit, a name as long as its room to the 64th of a
    # pixel: whole)
    edges = _download_edge_cases(tmp_path / "measures")
    cases, expected = [], []
    for n, (w, h, state) in enumerate(edges):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        card = {"type": "downloads", "id": "d", "x": 0, "y": 0, "w": w, "h": h}
        display = {"width": max(16, w), "height": max(16, h)}  # (16 px at least)
        data = {"format": THEME_FORMAT, "name": "fit", "display": display,
                "background": {"color": "#101418"}, "widgets": [card]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        text = json.dumps(state)
        snapshot = Snapshot(readings={"downloads": Reading("downloads", text)})
        cases.append({"folder": str(folder), "snapshot": {"readings": {"downloads": _typed(text)}}})
        expected.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        sha = hashlib.sha256(want["frame"].tobytes()).hexdigest()
        assert got.get("sha256") == sha, (n, edges[n][:2])
    assert len(edges) > 560


def test_downloads_frame_after_frame_as_python_keeps_them(tmp_path: Path) -> None:
    # (one renderer drawing a card frame after frame, a hair or seconds apart: the stretch of
    # light of a download of unknown size kept by its place of 64, drawn where it stood when it
    # came there; a new state drawn anew)
    rng = random.Random(0xD0A3)
    cases, expected = [], []
    for n in range(80):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _download)
        state = _download_state(rng, wild=0.1)
        if rng.random() < 0.7:  # (of unknown size: its stretch of light runs)
            state.pop("total", None)
            state["phase"] = rng.choice(["downloading", "preparing", "paused"])
        now = rng.uniform(0, 1e4)
        steps: list[tuple[Snapshot, float]] = []
        given = []
        for _ in range(rng.randrange(2, 9)):
            if rng.random() < 0.2:
                state = _download_state(rng, wild=0.1)
            text = json.dumps(state)
            steps.append((Snapshot(readings={"downloads": Reading("downloads", text)}), now))
            given.append({"snapshot": {"readings": {"downloads": _typed(text)}}, "now": now})
            now += rng.choice([0.0, 0.004, 0.01, 0.02, 0.04, 0.1, 0.3, 1.2, 2.4])
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        try:
            expected.append(_python_steps(folder, steps))
        except ThemeError:
            expected.append(None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                continue
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            drawn += 1
    assert drawn > 350


def _download_module_theme(rng: random.Random, folder: Path) -> None:
    """A theme of download modules (and others) on a grid, in a look's palette and style: lit,
    flat, or the finish the module brings."""
    from libre_panel import design
    from libre_panel.theme import modules

    folder.mkdir()
    w, h = rng.choice([(480, 320), (320, 240), (800, 480), (480, 128), (240, 320), (1920, 480)])
    look = design.LOOKS[rng.choice(list(design.LOOKS))]
    style = dict(look["style"])
    if rng.random() < 0.6:
        style.update(card=rng.choice(modules.CARDS), radius=rng.randrange(0, 101))
    grid = {"columns": rng.choice([1, 2, 3, 4]), "rows": rng.choice([1, 2, 3]),
            "gap": rng.choice([0, 8]), "margin": rng.choice([0, 8, 24])}  # fmt: skip
    widgets = []
    for i in range(rng.randrange(1, 4)):
        kind = rng.choice(["download", "download", "download", "text", "clock"])
        at = {"col": rng.randrange(grid["columns"]), "row": rng.randrange(grid["rows"]),
              "cols": rng.randrange(1, grid["columns"] + 1),
              "rows": rng.randrange(1, grid["rows"] + 1)}  # fmt: skip
        module: dict[str, Any] = {"type": "module", "id": f"m{i}", "module": kind, **at}
        if rng.random() < 0.6:
            module["finish"] = rng.choice(["lit", "flat"])
        widgets.append(module)
    data = {"format": THEME_FORMAT, "name": "downloads",
            "display": {"model": "custom", "width": w, "height": h,
                        "orientation": "portrait" if h > w else "landscape"},
            "palette": dict(look["palette"]), "grid": grid, "style": style,
            "widgets": widgets}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


def test_frames_of_download_modules_as_python_draws_them(tmp_path: Path) -> None:
    # (the download module in every look, lit or flat, its card in the cell it is given, a
    # download of every phase or none, by the demo's readings; to the bit, but where the look's
    # glow is blurred in floating point: a level apart now and then, as in the app's themes)
    import dataclasses

    import numpy as np

    rng = random.Random(0xD0A4)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        _download_module_theme(rng, folder)
        snapshot = dataclasses.replace(_demo(), history={})
        readings = dict(snapshot.readings)
        if rng.random() < 0.85:
            text = json.dumps(_download_state(rng, wild=0.05))
            readings["downloads"] = Reading("downloads", text)
        snapshot = dataclasses.replace(snapshot, readings=readings)
        now = _download_time(rng)
        soft = _soft_light(folder)
        cases.append({"folder": str(folder), "snapshot": _snapshot_given(snapshot), "now": now,
                      "bytes": soft})  # fmt: skip
        expected.append(_python_frame(folder, snapshot, False, now=now))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = exact = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if "unported" in got:
            continue
        assert "frame" in want, (n, got, want)
        sha = hashlib.sha256(want["frame"].tobytes()).hexdigest()
        if cases[n]["bytes"] and got["sha256"] != sha:
            apart = np.abs(np.asarray(picture(got["frame"]), int) - np.asarray(want["frame"], int))
            assert apart.max() <= 1, n
            assert (apart.max(axis=2) > 0).sum() <= apart.shape[0] * apart.shape[1] // 10000, n
        else:
            assert got["sha256"] == sha, n
            exact += 1
        assert got["boxes"] == want["boxes"], n
        drawn += 1
    assert drawn > 40 and exact > drawn - 8


# -- card slots: a module showing one card of several ----------------------------------------

# the cards whose rule is their own (a track, the weather, an agent, a download), the network's,
# and others that always have something to show
SLOT_CARDS = ["playback", "music", "forecast", "weather", "ai", "download", "net", "network"]
SLOT_OTHERS = ["clock", "date", "ring", "stat", "text", "countdown", "huge", "system", "values",
               "volume", "game", "agenda", "netinfo", "ping", "timer", "battery", "instrument",
               "graph", "bars", "world", "sun", "temps", "cores"]  # fmt: skip
SLOT_RULES = ["media.playing=1", "weather.temperature", "!ai.claude.busy", "ai.codex.busy>=1",
              "media.title, weather.temperature", "net.up>100", "@media.cover", "nope", ",",
              "!nope", "media.playing = 0", "game.limiter=GPU",
              " ai.claude.busy > 1 , !media.title"]  # fmt: skip
# the downloads' state as a service publishes it, and what is no dict: a list, JSON it is not,
# a dict json.loads reads with NaN and an infinity in it
SLOT_DOWNLOADS = [None, '{"files": [{"name": "a.iso", "done": 0.5}]}', "[1, 2]", "{", "null",
                  '{"a": NaN}', " {} ", '{"a": 1}x', "{}", '"text"', '{"a": 1e999}',
                  '\ufeff{}']  # fmt: skip


def _demo() -> Snapshot:
    from libre_panel.sensors.demo import demo_snapshot

    global _DEMO
    if _DEMO is None:
        _DEMO = demo_snapshot(fixed_time=1_760_000_000.0)
    return _DEMO


_DEMO: Snapshot | None = None


def _slot_items(rng: random.Random, cards: list[str] | None = None) -> str:
    """A slot's cards, one a line: a card, its own time, its own rule; a comment, a line that
    names no card."""
    lines = []
    for _ in range(rng.randrange(1, 5)):
        if cards is not None:
            line = rng.choice(cards)
        else:
            line = rng.choice(SLOT_CARDS) if rng.random() < 0.7 else rng.choice(SLOT_OTHERS)
        if rng.random() < 0.25:
            line += rng.choice([" for 5m", " for 90s", " for 1h", " for 3", " for 5s"])
        if rng.random() < 0.25:
            line += " if " + rng.choice(SLOT_RULES)
        lines.append(line)
    if rng.random() < 0.1:
        lines.insert(rng.randrange(len(lines) + 1), "# a comment")
    if rng.random() < 0.05:
        lines.append("bogus")
    return "\n".join(lines)


def _slot_theme(rng: random.Random, folder: Path, make: Any = None, glow: float = 0.0,
                cards: list[str] | None = None, moving: bool = False) -> None:  # fmt: skip
    """A theme of card slots on a grid (and other modules beside them, over them, under them),
    in a look's palette and style, the style's corners, cards and glow as given; `moving`: its
    changes of every way, of a moment to seconds, its tracks sliding in."""
    from libre_panel import design
    from libre_panel.theme import modules

    folder.mkdir()
    w, h = rng.choice([(480, 320), (320, 240), (800, 480), (480, 128), (640, 180), (240, 320),
                       (160, 128), (64, 48)])  # fmt: skip
    look = design.LOOKS[rng.choice(list(design.LOOKS))]
    style = dict(look["style"])
    if rng.random() < 0.7:
        style.update(card=rng.choice(modules.CARDS), radius=rng.randrange(0, 101),
                     backdrop=rng.choice(modules.BACKDROPS),
                     card_opacity=rng.choice([0.4, 0.7, 1, 0.85]))  # fmt: skip
    style["glow"] = glow
    grid = {"columns": rng.choice([1, 2, 3, 4, 6]), "rows": rng.choice([1, 2, 3, 4]),
            "gap": rng.choice([0, 4, 8, 16]), "margin": rng.choice([0, 8, 24])}  # fmt: skip
    columns, rows = grid["columns"], grid["rows"]
    widgets: list[dict[str, Any]] = []
    for i in range(rng.randrange(1, 4)):
        at = {"col": rng.randrange(columns), "row": rng.randrange(rows),
              "cols": rng.randrange(1, columns + 1),
              "rows": rng.randrange(1, rows + 1)}  # fmt: skip
        if i and rng.random() < 0.35:  # (another module: the frame lies over the slot's alone)
            kind = rng.choice(["ring", "clock", "text", "stat", "date"])
            widgets.append({"type": "module", "id": f"m{i}", "module": kind, "finish": "flat",
                            **at})  # fmt: skip
            continue
        # (the module's kind, then its cards: in this order from the generator)
        kind = rng.choice(["cards", "cards", "cards", "netcard"])
        items = _slot_items(rng, cards) if rng.random() < 0.9 else ""
        module: dict[str, Any] = {"type": "module", "id": f"s{i}", **at, "module": kind,
                                  "items": items}  # fmt: skip
        options: dict[str, list[Any]] = {
            "order": ["first", "turns"], "transition": ["light", "fade", "cut", "random"],
            "hold": [2.0, 0, 0.5, -1, 10], "change": [0.6, 0, 2], "slide": [0, 0.4],
            "turn": [5.0, 0.01, 1, 1440], "card": ["auto", "on", "off"],
            "color": [None, "#ff8800", "@accent", "@cover", "teal", "#11223380"],
            "forecast": ["auto", "off", "days"],
        }  # fmt: skip
        if moving:
            options.update(transition=["light", "fade", "slide", "rise", "wipe", "cut", "random"],
                           change=[0.6, 0, 2, 0.3], slide=[0, 0.4, 1.0])  # fmt: skip
        for key, choices in options.items():
            if rng.random() < 0.5:
                module[key] = rng.choice(choices)
        if rng.random() < 0.85:  # (most flat: the finishes Rust does not draw yet aside)
            module["finish"] = "flat"
        widgets.append(module)
    rng.shuffle(widgets)
    data = {"format": THEME_FORMAT, "name": "slots",
            "display": {"model": "custom", "width": w, "height": h,
                        "orientation": "portrait" if h > w else "landscape"},
            "palette": dict(look["palette"]) if rng.random() < 0.8 else
            {"accent": _hex(rng), "bg": _hex(rng), "net": _hex(rng)},
            "grid": grid, "style": style, "widgets": widgets}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


def _snapshot_given(snapshot: Snapshot) -> dict[str, Any]:
    """`snapshot` as lp-check reads it: its readings with their units, labels and origins, its
    pictures, histories and moment."""
    return {"readings": {k: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                             "origin": r.origin} for k, r in snapshot.readings.items()},
            "images": {k: image(v) for k, v in snapshot.images.items()},
            "history": {k: [_typed(v) for v in values] for k, values in snapshot.history.items()},
            "now": _moment(snapshot.now)}  # fmt: skip


def _slot_snapshot(rng: random.Random) -> tuple[dict[str, Any], Snapshot]:
    """The demo's readings, with a track playing, paused or none, the weather of any kind by day
    or night or none, agents at work or not, a download running or what is no download; its
    cover or another or none, at an hour of the day or the night."""
    import dataclasses

    demo = _demo()
    readings = dict(demo.readings)
    images = dict(demo.images)

    def put(key: str, choices: list[Any]) -> None:
        value = rng.choice(choices)
        if value == "<gone>":
            readings.pop(key, None)
        else:
            readings[key] = Reading(key, value)

    for key, p, choices in [
        ("media.title", 0.8, ["Midnight Drive", "", None, 0, "<gone>", "Song"]),
        ("media.playing", 0.8, [1, 0, True, False, 0.0, 2.5, "1", None, "<gone>"]),
        ("media.artist", 0.5, ["The Night Owls", "", None, "<gone>"]),
        ("weather.temperature", 0.6, [21.5, None, "<gone>", 0, -3.0]),
        ("weather.code", 0.6, [0, 1, 2, 3, 45, 61, 71, 95, 51, None, "x", 2.7]),
        ("weather.is_day", 0.5, [0, 1, None, "<gone>"]),
        ("ai.claude.busy", 0.7, [0, 1, 2, True, 0.5, 1.5, None, "1", "<gone>"]),
        ("ai.codex.busy", 0.7, [0, 1, 2, False, 0.99, None, "<gone>"]),
        ("downloads", 0.7, [*SLOT_DOWNLOADS[1:], "<gone>"]),
    ]:  # fmt: skip
        if rng.random() < p:
            put(key, choices)
    if rng.random() < 0.3:
        images.pop("media.cover", None)
    elif rng.random() < 0.3:  # (another cover: its colour, or a grey one's none)
        side = (rng.randrange(20, 90), rng.randrange(20, 90))
        cover = noise(rng, rng.choice(["RGB", "RGBA"]), side)
        images["media.cover"] = cover.convert("L").convert("RGB") if rng.random() < 0.3 else cover
    # (the histories the cards' graphs draw, their last numbers: the same for both)
    history = {k: [float(v) for v in values[-100:]] for k, values in demo.history.items()}
    now = datetime(2026, 11, 7, rng.randrange(24), rng.randrange(60), 9, 250)
    snapshot = dataclasses.replace(demo, readings=readings, images=images, history=history,
                                   now=now)  # fmt: skip
    return _snapshot_given(snapshot), snapshot


# what a slot's card may hold that Rust does not draw here (it still chooses the card and its
# frame's colour as Python does: the checks compare those of every frame)
SLOT_NOT_YET = NOT_HERE


def _slots_of(renderer: Renderer) -> list[list[Any]]:
    """What each slot shows, as lp-check tells it: its id, its card, its frame's colour and
    thickness."""
    return [[s.id, s.kinds[s.shown], list(s._colour) if s._colour else None, s.width]
            for s in renderer._slots.values()]  # fmt: skip


def _compare_slot_frames(got: dict[str, Any], want: dict[str, Any], where: Any) -> bool:
    """A frame of card slots: whether both drew it (to the bit, its boxes, what each slot shows),
    or Rust says it does not draw one of its card's widgets yet (what each slot shows still as
    Python has it)."""
    if "failed" in want:  # (Python's frame fails: Rust makes none)
        assert "unported" in got, (where, got, want)
        return False
    if "slots" in got:
        assert got["slots"] == want["slots"], where
    if "unported" in got:
        assert got["unported"] in SLOT_NOT_YET or got["unported"] == "finishes", (where, got)
        return False
    assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), where
    assert got["boxes"] == want["boxes"], where
    return True


def test_frames_of_card_slots_as_python_draws_them(tmp_path: Path) -> None:
    # (which card each slot shows: the first with something to show by its rule or its own,
    # else the first; only its parts drawn, none of the bars' marks or the graphs' dots over
    # them; the slot's frame round it in the card's colour (the module's own, the cover's, the
    # weather's by day and night, the network's, the accent) over the ground, in strips along
    # its edges where it is a frame, laid right after the slot's parts; in every style, corner
    # and size)
    rng = random.Random(0x5107)
    cases, expected = [], []
    for n in range(240):
        folder = tmp_path / f"t{n}"
        _slot_theme(rng, folder)
        given, snapshot = _slot_snapshot(rng)
        dither = rng.random() < 0.2
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither})
        renderer = Renderer(load_theme(folder), preview=True, dither=dither)
        lists._RAN.clear()
        try:
            frame, boxes = renderer.render(snapshot, now=1000.0)
            expected.append({"frame": frame, "boxes": boxes, "slots": _slots_of(renderer)})
        except Exception as error:  # noqa: BLE001 (a frame Python cannot make)
            expected.append({"failed": f"{type(error).__name__}: {error}"})
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = chosen = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        drawn += _compare_slot_frames(got, want, n)
        chosen += "slots" in got
    assert drawn > 120
    assert chosen > 180


def _python_steps(folder: Path, steps: list[tuple[Snapshot, float]],
                  place: tuple[float, float] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Frames one after another by one renderer, each at its time (your place `place`)."""
    renderer = Renderer(load_theme(folder), preview=True)
    renderer.place = place
    lists._RAN.clear()
    out: list[dict[str, Any]] = []
    for snapshot, now in steps:
        try:
            frame, boxes = renderer.render(snapshot, now=now)
            out.append({"frame": frame, "boxes": boxes, "slots": _slots_of(renderer)})
        except Exception as error:  # noqa: BLE001 (a frame Python cannot make)
            out.append({"failed": f"{type(error).__name__}: {error}"})
    return out


def test_card_slots_frame_after_frame_as_python_keeps_them(tmp_path: Path) -> None:
    # (what a slot's rules keep from frame to frame: a track paused since when, for `hold`
    # minutes; an agent at work until when, for 45 s more; a card's turn since when, for its own
    # time or the slot's; frames a second apart and exactly at each of those times)
    import dataclasses

    rng = random.Random(0x5109)
    deltas = [0, 1, 4.99, 5, 30, 44.9, 45, 45.1, 60, 89, 90, 119.9, 120, 120.1, 300, 299.99, 3600]
    kinds = ["playback", "music", "forecast", "weather", "ai", "net", "date", "clock", "text"]
    cases, expected = [], []
    for n in range(100):
        folder = tmp_path / f"t{n}"
        _slot_theme(rng, folder, cards=kinds)
        steps: list[tuple[Snapshot, float]] = []
        snapshot, now = dataclasses.replace(_demo(), history={}), 1000.0
        for _ in range(rng.randrange(2, 8)):
            readings = dict(snapshot.readings)
            for key, p, choices in [
                ("media.title", 0.3, ["Midnight Drive", "Other", "", "<gone>"]),
                ("media.playing", 0.5, [1, 0, 0, False]),
                ("media.artist", 0.15, ["The Night Owls", "Someone"]),
                ("weather.temperature", 0.2, [21.5, "<gone>"]),
                ("ai.claude.busy", 0.4, [0, 1, 2, "<gone>"]),
                ("ai.codex.busy", 0.3, [0, 1, "<gone>"]),
            ]:  # fmt: skip
                if rng.random() < p:
                    value = rng.choice(choices)
                    if value == "<gone>":
                        readings.pop(key, None)
                    else:
                        readings[key] = Reading(key, value)
            snapshot = dataclasses.replace(snapshot, readings=readings)
            steps.append((snapshot, now))
            now += rng.choice(deltas)
        given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        expected.append(_python_steps(folder, steps))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = changed = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        before = None
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            drawn += _compare_slot_frames(g, w, (n, i))
            if "slots" in w:
                shown = [slot[1] for slot in w["slots"]]
                changed += before is not None and shown != before
                before = shown
    assert drawn > 280
    assert changed > 45  # (cards that came and went, from one frame to the next)


def _slot_readings_after(rng: random.Random, readings: dict[str, Reading]) -> dict[str, Reading]:
    """The slots' readings of the next frame: as before, or now and then a track that comes, goes,
    pauses or is another, the weather and the agents coming and going."""
    out = dict(readings)
    for key, p, choices in [
        ("media.title", 0.25, ["Midnight Drive", "Other", "Midnight Drive", "", "<gone>"]),
        ("media.playing", 0.3, [1, 1, 0, False]),
        ("media.artist", 0.15, ["The Night Owls", "Someone"]),
        ("weather.temperature", 0.2, [21.5, "<gone>"]),
        ("weather.code", 0.1, [0, 61, 71, 95, 3]),
    ]:  # fmt: skip
        if rng.random() < p:
            value = rng.choice(choices)
            if value == "<gone>":
                out.pop(key, None)
            else:
                out[key] = Reading(key, value)
    return out


def test_card_slots_moving_from_card_to_card_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its slots frame after frame as the panels' loop does: a change of
    # cards on its way, as the light rolls them, a fade, a slide, a rise or a wipe has them, or at
    # once, the old card as it showed itself last; a new track sliding in on the playback card;
    # the frame's colour reaching the card's over half a second; at a frame rate or none, the
    # frames a moment to a second apart, and back in time)
    import dataclasses

    rng = random.Random(0x5110)
    kinds = ["playback", "music", "forecast", "weather", "net", "date", "clock", "text", "ring",
             "stat"]  # fmt: skip
    cases, expected = [], []
    for n in range(80):
        folder = tmp_path / f"t{n}"
        _slot_theme(rng, folder, cards=kinds, moving=True)
        snapshot = dataclasses.replace(_demo(), history={})
        now, fps = rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps: list[tuple[Snapshot, float]] = []
        for _ in range(rng.randrange(3, 20)):
            steps.append((snapshot, now))
            now += rng.choice([0.02, 0.04, 0.04, 0.1, 0.1, 0.2, 0.3, 0.5, 1.0, 0, -0.1])
            readings = _slot_readings_after(rng, dict(snapshot.readings))
            snapshot = dataclasses.replace(snapshot, readings=readings)
        given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 700 and moving > 250, (drawn, moving)


def test_card_slots_warmed_up_for_a_panel_as_python_draws_them(tmp_path: Path) -> None:
    # (a panel's renderer, not a preview's: its first moving frame draws every card of every
    # slot once first, whichever is on show, each slot then forgetting what it showed (those
    # warmed before it are begun again by the slots after it), and draws on from there: the
    # cards each slot shows, its changes, a new track sliding in, as the panels' loop draws
    # them; at a frame rate or none, the frames a moment to a second apart, and back in time)
    import dataclasses

    rng = random.Random(0x5111)
    kinds = ["playback", "music", "forecast", "weather", "net", "date", "clock", "text", "ring",
             "stat"]  # fmt: skip
    cases, expected = [], []
    for n in range(50):
        folder = tmp_path / f"t{n}"
        _slot_theme(rng, folder, cards=kinds, moving=True)
        snapshot = dataclasses.replace(_demo(), history={})
        now, fps = rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps: list[tuple[Snapshot, float]] = []
        for _ in range(rng.randrange(2, 12)):
            steps.append((snapshot, now))
            now += rng.choice([0.02, 0.04, 0.1, 0.3, 1.0, 0, -0.1])
            readings = _slot_readings_after(rng, dict(snapshot.readings))
            snapshot = dataclasses.replace(snapshot, readings=readings)
        given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "preview": False, "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps, preview=False))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 250 and moving > 60, (drawn, moving)


def test_card_slots_warmed_up_at_their_edges_as_python_draws_them(tmp_path: Path) -> None:
    # (a panel's slot showing a track, its ring card drawn only in the warm-up, then shown the
    # frame after it as the track stops and the load leaps: its ring and number glide from where
    # the warm-up left them; one slot alone, or two side by side (the one warmed first begun
    # again by the other's warm-up); its change of each way, at a frame rate and none)
    import dataclasses

    snapshot = dataclasses.replace(_demo(), history={})
    first = dict(snapshot.readings)
    first.update({"media.title": Reading("media.title", "Midnight Drive"),
                  "media.playing": Reading("media.playing", 1),
                  "cpu.load": Reading("cpu.load", 10.0)})  # fmt: skip
    then = dict(first)
    then.update({"media.title": Reading("media.title", ""),
                 "media.playing": Reading("media.playing", 0),
                 "cpu.load": Reading("cpu.load", 90.0)})  # fmt: skip
    steps = [(dataclasses.replace(snapshot, readings=first), 1000.0)]
    steps += [(dataclasses.replace(snapshot, readings=then), t)
              for t in (1000.04, 1000.08, 1000.3, 1000.7, 1001.5)]  # fmt: skip
    cases, expected = [], []
    for n, (transition, fps, two) in enumerate(
        (t, fps, two) for t in ("fade", "light", "cut") for fps in (0, 50) for two in (False, True)
    ):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        slot = {"type": "module", "id": "s0", "col": 0, "row": 0, "cols": 1, "rows": 1,
                "module": "cards", "items": "playback\nring", "transition": transition,
                "change": 0.6, "hold": 0}  # fmt: skip
        widgets = [slot]
        if two:
            widgets.append({**slot, "id": "s1", "col": 1, "items": "playback\nstat\nring"})
        data = {"format": THEME_FORMAT, "name": "warm", "display": {"width": 480, "height": 320},
                "palette": {name: "#808080" for name in PALETTE},
                "background": {"color": "#101418"}, "widgets": widgets,
                "grid": {"columns": 2 if two else 1, "rows": 1, "gap": 0, "margin": 0}}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "preview": False, "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps, preview=False))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 72 and moving > 30, (drawn, moving)


def test_card_slots_change_every_way_as_python_draws_them(tmp_path: Path) -> None:
    # (a slot of a track's card and the weather's, its change of each way: the track stops and
    # the weather shows, frames a 25th of a second apart through the change, one back in time
    # within it; the track again, then another track sliding in; the frame's colour on its way;
    # at a frame rate and none)
    import dataclasses

    from libre_panel import design

    look = design.LOOKS["libre"]
    cases, expected = [], []
    for n, (transition, fps) in enumerate([(t, fps) for t in ("light", "fade", "slide", "rise",
                                           "wipe", "cut") for fps in (0, 50)]):  # fmt: skip
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        module = {"type": "module", "id": "s", "col": 0, "row": 0, "cols": 2, "rows": 1,
                  "module": "cards", "items": "playback\nweather", "transition": transition,
                  "change": 0.6, "slide": 0.4, "hold": 0, "finish": "flat"}  # fmt: skip
        theme = {"format": THEME_FORMAT, "name": "change",
                 "display": {"model": "custom", "width": 320, "height": 120,
                             "orientation": "landscape"},
                 "palette": dict(look["palette"]), "style": dict(look["style"]),
                 "grid": {"columns": 2, "rows": 1, "gap": 4, "margin": 8},
                 "widgets": [module]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
        demo = dataclasses.replace(_demo(), history={})
        steps: list[tuple[Snapshot, float]] = []
        for k in range(64):
            readings = dict(demo.readings)
            title = "Midnight Drive" if k < 48 else "Another Song"
            readings["media.title"] = Reading("media.title", title)
            readings["media.playing"] = Reading("media.playing", 0 if 10 <= k < 30 else 1)
            readings["weather.temperature"] = Reading("weather.temperature", 21.5)
            now = 1000.0 + k / 25 - (0.1 if k == 16 else 0.0)
            steps.append((dataclasses.replace(demo, readings=readings), now))
        given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 12 * 64 and moving > 500, (drawn, moving)


def _slot_case(folder: Path, module: dict[str, Any], size: tuple[int, int] = (320, 120),
               grid: tuple[int, int] = (2, 1), at: tuple[int, int, int, int] = (0, 0, 2, 1),
               more: list[dict[str, Any]] | None = None, ground: str | None = None,
               style: dict[str, Any] | None = None) -> None:  # fmt: skip
    """A theme of one card slot (in the Libre look), its module's own fields given, at `at` on a
    grid of `grid` on a panel of `size`; other modules after it, another ground or style."""
    from libre_panel import design

    look = design.LOOKS["libre"]
    folder.mkdir()
    slot = {"type": "module", "id": "s", "col": at[0], "row": at[1], "cols": at[2],
            "rows": at[3], "module": "cards", "finish": "flat", **module}  # fmt: skip
    theme: dict[str, Any] = {
        "format": THEME_FORMAT, "name": "slot",
        "display": {"model": "custom", "width": size[0], "height": size[1],
                    "orientation": "portrait" if size[1] > size[0] else "landscape"},
        "palette": dict(look["palette"]), "style": {**look["style"], **(style or {})},
        "grid": {"columns": grid[0], "rows": grid[1], "gap": 4, "margin": 8},
        "widgets": [slot, *(more or [])]}  # fmt: skip
    if ground is not None:
        theme["background"] = {"color": ground}
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")


def _slot_steps(times: list[float], readings_at: Any) -> list[tuple[Snapshot, float]]:
    """The demo's snapshot at each of `times`, its readings as `readings_at(k, readings)` makes
    them for the k-th."""
    import dataclasses

    demo = dataclasses.replace(_demo(), history={})
    steps = []
    for k, now in enumerate(times):
        readings = dict(demo.readings)
        readings_at(k, readings)
        steps.append((dataclasses.replace(demo, readings=readings), now))
    return steps


def test_card_slots_at_the_edges_of_their_changes_as_python_draws_them(tmp_path: Path) -> None:
    # (frames at times a float holds as they are: a change that ends exactly at a frame, a fade
    # a moment after it began and a moment before it ends; a track sliding in with less to show,
    # then rolling up; a change into the track's card that gets another track on the way; a cut
    # from one track's card to another's; a small slot on a large panel whose line of light is
    # short; a slot on a clear ground under a see-through bar laid after it; frames close
    # together where the cards' rolling edges meet in one row)
    def put(readings: dict[str, Reading], **values: Any) -> None:
        for key, value in values.items():
            key = key.replace("_", ".")
            if value is None:
                readings.pop(key, None)
            else:
                readings[key] = Reading(key, value)

    def track(title: str, artist: str | None, playing: int) -> dict[str, Any]:
        return {"media_title": title, "media_artist": artist, "media_playing": playing,
                "weather_temperature": 21.5}  # fmt: skip

    quick = [1000.0 + k / 32 for k in range(72)]
    plays = {"items": "playback\nweather", "hold": 0, "change": 0.5, "slide": 0.25}
    cases: list[tuple[dict[str, Any], dict[str, Any], list[float], Any]] = [
        # (a change of 0.5 s from 1000.25 on, ending at the frame of 1000.75; the frame's colour
        # its own: nothing else moves on then)
        ({**plays, "transition": "light", "color": "#ff8800"}, {}, quick[:40],
         lambda k, r: put(r, **track("Midnight Drive", "A", int(not 8 <= k < 30)))),
        # (a fade, and frames a 128th after it began and before it ends)
        ({**plays, "transition": "fade"}, {},
         sorted([*quick[:40], 1000.25 + 1 / 128, 1000.25 + 63 / 128]),
         lambda k, r: put(r, **track("Midnight Drive", "A", int(not 8 <= k < 30)))),
        # (another track with no artist slides in, then the card rolls up; it comes back, and
        # on its way another track plays)
        ({**plays, "transition": "light"}, {}, quick,
         lambda k, r: put(r, **track("Midnight Drive" if k < 8 else "Another" if k < 50
                                     else "Third", "A" if k < 8 else None,
                                     int(not 20 <= k < 44)))),
        # (a cut from the track's card to the music card: another artist, nothing kept to slide)
        ({"items": "playback if media.artist=A\nmusic if media.artist=B", "hold": 0,
          "slide": 0.25, "transition": "cut"}, {}, quick[:40],
         lambda k, r: put(r, **track("Song", "B" if 8 <= k < 24 else "A", 1))),
        # (a small slot on a panel 480 px high: a short line of light, and a short beam)
        ({**plays, "transition": "light"}, {"size": (480, 480), "grid": (6, 8),
                                             "at": (1, 1, 1, 1)}, quick[:40],
         lambda k, r: put(r, **track("Midnight Drive", "A", int(not 8 <= k < 30)))),
        ({**plays, "transition": "wipe"}, {"size": (480, 480), "grid": (6, 8),
                                            "at": (1, 1, 1, 1)}, quick[:40],
         lambda k, r: put(r, **track("Midnight Drive", "A", int(not 8 <= k < 30)))),
        # (on a clear ground, a see-through bar laid after the slot over its top edge, where
        # the line of light glows over the ground)
        ({**plays, "transition": "light"},
         {"ground": "#00000000", "more": [{"type": "rect", "id": "r", "x": 0, "y": 0, "w": 320,
                                            "h": 18, "color": "#ffffff80"}]}, quick[:40],
         lambda k, r: put(r, **track("Midnight Drive", "A", int(not 8 <= k < 30)))),
        # (frames 8192ths of a second apart where the rolling up and down meet in one row)
        ({**plays, "transition": "light"}, {},
         [1000.0, 1000.25, *[1000.25 + 0.183 + k / 8192 for k in range(200)]],
         lambda k, r: put(r, **track("Midnight Drive", "A", int(k < 1)))),
    ]  # fmt: skip
    given, expected = [], []
    for n, (module, shape, times, readings_at) in enumerate(cases):
        folder = tmp_path / f"t{n}"
        _slot_case(folder, module, **shape)
        steps = _slot_steps(times, readings_at)
        frames = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        given.append({"folder": str(folder), "dither": False, "animate": True, "fps": 0,
                      "steps": frames})  # fmt: skip
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(given, expected)
    assert drawn == sum(len(case[2]) for case in cases) and moving > 250, (drawn, moving)


def test_card_slots_change_with_their_glow_as_python_draws_them(tmp_path: Path) -> None:
    # (a lit slot whose parts glow (the track's progress, its light added over it), changing each
    # way: the glow cut, faded and moved with its part; within a level, the light blurred in
    # floating point)
    given, expected = [], []
    for n, transition in enumerate(["light", "fade", "slide", "rise", "wipe"]):
        folder = tmp_path / f"t{n}"
        _slot_case(folder, {"items": "playback\nweather", "hold": 0, "change": 0.5,
                            "transition": transition, "finish": "lit"},
                   style={"glow": 0.8})  # fmt: skip

        def readings_at(k: int, r: dict[str, Reading]) -> None:
            r["media.title"] = Reading("media.title", "Midnight Drive")
            r["media.playing"] = Reading("media.playing", int(not 8 <= k < 30))
            r["media.progress"] = Reading("media.progress", 0.6)
            r["weather.temperature"] = Reading("weather.temperature", 21.5)

        steps = _slot_steps([1000.0 + k / 32 for k in range(48)], readings_at)
        frames = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        given.append({"folder": str(folder), "dither": False, "animate": True, "fps": 0,
                      "bytes": True, "steps": frames})  # fmt: skip
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(given, expected, level=True)
    assert drawn == 5 * 48 and moving > 180, (drawn, moving)


def test_card_slots_keep_their_cards_to_the_second(tmp_path: Path) -> None:
    # (frames a hair before, at and a hair after each time a slot's rules keep: an agent's 45 s,
    # a paused track's `hold` (begun anew for another track, another artist, a title gone, a
    # track played in between), each card's turn (its line's, the slot's, the slot's held to its
    # shortest and longest), as Python's renderer keeps them, frame after frame)
    import dataclasses

    demo = dataclasses.replace(_demo(), history={})
    quiet = {"ai.claude.busy": 0, "ai.codex.busy": 0, "media.playing": 0}

    def at(**given: Any) -> Snapshot:
        """The demo's readings with these (``ai__claude__busy`` for ai.claude.busy; None: gone),
        no agent at work and nothing playing unless they say so."""
        found = dict(demo.readings)
        for key, value in {**quiet, **{k.replace("__", "."): v for k, v in given.items()}}.items():
            if value is None:
                found.pop(key, None)
            else:
                found[key] = Reading(key, value)
        return dataclasses.replace(demo, readings=found)

    title = {"media__title": "Midnight Drive", "media__artist": "The Night Owls"}
    scenes: list[tuple[dict[str, Any], list[tuple[Snapshot, float]]]] = [
        ({"items": "ai\ndate"}, [  # an agent's card, 45 s after it last worked
            (at(ai__claude__busy=1), 1000.0), (at(), 1044.9), (at(), 1044.999), (at(), 1045.0),
            (at(), 1045.1)]),
        ({"items": "ai\ndate"}, [
            (at(ai__codex__busy=2), 1000.0), (at(), 1030.0), (at(ai__codex__busy=1), 1040.0),
            (at(), 1084.99), (at(), 1085.0)]),
        ({"items": "playback\ndate", "hold": 0.5}, [  # a paused track, 30 s
            (at(**title, media__playing=1), 1000.0), (at(**title), 1010.0), (at(**title), 1039.9),
            (at(**title), 1040.0), (at(**title), 1040.1)]),
        ({"items": "playback\ndate", "hold": 0.5}, [  # another track: begun anew
            (at(**title), 1000.0), (at(**{**title, "media__title": "Other"}), 1020.0),
            (at(**{**title, "media__title": "Other"}), 1045.0),
            (at(**{**title, "media__title": "Other"}), 1050.0)]),
        ({"items": "playback\ndate", "hold": 0.5}, [  # another artist
            (at(**title), 1000.0), (at(**{**title, "media__artist": "Someone"}), 1020.0),
            (at(**{**title, "media__artist": "Someone"}), 1045.0)]),
        ({"items": "playback\ndate", "hold": 0.5}, [  # the title gone between
            (at(**title), 1000.0), (at(media__title=None), 1020.0), (at(**title), 1025.0),
            (at(**title), 1045.0), (at(**title), 1055.0)]),
        ({"items": "playback\ndate", "hold": 0.5}, [  # played between
            (at(**title), 1000.0), (at(**title, media__playing=1), 1020.0), (at(**title), 1035.0),
            (at(**title), 1060.0), (at(**title), 1065.0)]),
        *[({"items": "playback\ndate", "hold": 0}, [(at(**title, media__playing=playing), 1000.0)])
          for playing in (True, False, 0.5, -1.0, 2, 0.0, "1", None)],  # (what plays: a number)
        ({"items": "playback\ndate", "hold": 2}, [  # the default's two minutes
            (at(**title), 1000.0), (at(**title), 1119.99), (at(**title), 1120.0)]),
        ({"items": "date for 5s\nclock for 90s\ntext", "order": "turns", "turn": 1}, [
            (at(), 1000.0), (at(), 1004.99), (at(), 1005.0), (at(), 1094.99), (at(), 1095.0),
            (at(), 1154.99), (at(), 1155.0), (at(), 1159.99), (at(), 1160.0)]),
        ({"items": "date\nclock", "order": "turns", "turn": 0.01}, [  # held to 5 s at least
            (at(), 1000.0), (at(), 1001.0), (at(), 1004.99), (at(), 1005.0), (at(), 1009.0)]),
        ({"items": "date\nclock", "order": "turns", "turn": 2000}, [  # and to a day at most
            (at(), 1000.0), (at(), 1000.0 + 86399.9), (at(), 1000.0 + 86400.0)]),
        ({"items": "date for 5s\nplayback for 5s", "order": "turns", "hold": 0}, [  # alone again
            (at(**title), 1000.0), (at(**title), 1005.0), (at(**title, media__playing=1), 1006.0),
            (at(**title, media__playing=1), 1010.0)]),
        ({"items": "date\nclock", "order": "turns", "turn": math.inf}, [  # none finite: 5 min
            (at(), 1000.0), (at(), 1299.9), (at(), 1300.0)]),
        ({"items": "date\nclock", "order": "turns", "turn": math.nan}, [
            (at(), 1000.0), (at(), 1001.0), (at(), 1299.9), (at(), 1300.0)]),
        ({"items": "playback\ndate", "hold": math.inf}, [  # a pause held for ever
            (at(**title), 1000.0), (at(**title), 1000.0 + 1e9)]),
        ({"items": "playback\ndate", "hold": math.nan}, [(at(**title), 1000.0)]),  # for none
        ({"items": "playback\ndate", "hold": -math.inf}, [(at(**title), 1000.0)]),
        ({"items": "playback\ndate\nclock", "order": "turns", "turn": 1, "hold": 0.5}, [
            (at(**title, media__playing=1), 1000.0), (at(**title, media__playing=1), 1059.0),
            (at(**title), 1060.0), (at(), 1120.0), (at(), 1180.0), (at(**title, media__playing=1),
                                                                   1200.0), (at(), 1240.0)]),
    ]  # fmt: skip
    cases, expected = [], []
    for n, (module, steps) in enumerate(scenes):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        data = {"format": THEME_FORMAT, "name": "slots",
                "display": {"model": "custom", "width": 320, "height": 240,
                            "orientation": "landscape"},
                "grid": {"columns": 2, "rows": 2, "gap": 8, "margin": 8},
                "style": {"card": "glass", "radius": 18, "glow": 0},
                "widgets": [{"type": "module", "id": "s0", "module": "cards", "finish": "flat",
                             "col": 0, "row": 0, "cols": 2, "rows": 1, **module}]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        cases.append({"folder": str(folder), "dither": False, "steps": given})
        expected.append(_python_steps(folder, steps))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    changes = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        cards = [w["slots"][0][1] for w in want]
        changes += sum(a != b for a, b in zip(cards, cards[1:], strict=False))
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            assert _compare_slot_frames(g, w, (n, i)), (n, i, g.get("unported"))
    assert changes >= 16  # (each scene's card changes where its time is up, and only there)


def test_card_slots_python_cannot_make_are_not_made(tmp_path: Path) -> None:
    # (a slot's turn, hold or change past a float's range: Python's renderer fails as it is
    # made, at float() of it, and Rust makes none either)
    cases, expected = [], []
    for field in ("turn", "hold", "change", "slide"):
        folder = tmp_path / field
        folder.mkdir()
        data = {"format": THEME_FORMAT, "name": "slots",
                "display": {"model": "custom", "width": 320, "height": 240,
                            "orientation": "landscape"},
                "grid": {"columns": 2, "rows": 2},
                "widgets": [{"type": "module", "id": "s0", "module": "cards",
                             "items": "date\nclock", field: 10**400}]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        try:
            Renderer(load_theme(folder), preview=True)
            expected.append(None)
        except OverflowError as error:
            expected.append(str(error))
        cases.append({"folder": str(folder), "snapshot": {"readings": {}, "images": {}},
                      "dither": False})  # fmt: skip
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    assert expected == ["int too large to convert to float"] * 4
    assert [got.get("error") for got in ours["frames"]] == expected


def test_card_slots_frames_in_their_cards_colours(tmp_path: Path) -> None:
    # (the colour of a slot's frame for every card: the weather's of each kind of weather, by
    # day and by night (the forecast's word for it, Open-Meteo's, the clock's), the network's,
    # a download's, the cover's (a grey one carries none), the accent; the module's own over
    # all of them)
    import dataclasses

    from libre_panel import design

    demo = dataclasses.replace(_demo(), history={})
    palette = dict(design.LOOKS["neon"]["palette"])
    rng = random.Random(0x5C01)
    grey = noise(rng, "RGB", (40, 40)).convert("L").convert("RGB")
    cases, expected = [], []

    def case(items: str, changes: dict[str, Any], hour: int = 12, color: Any = None,
             cover: Any = "demo") -> None:  # fmt: skip
        n = len(cases)
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        module = {"type": "module", "id": "s0", "module": "cards", "finish": "flat",
                  "col": 0, "row": 0, "cols": 2, "rows": 1, "items": items}  # fmt: skip
        if color is not None:
            module["color"] = color
        data = {"format": THEME_FORMAT, "name": "slots",
                "display": {"model": "custom", "width": 320, "height": 240,
                            "orientation": "landscape"},
                "grid": {"columns": 2, "rows": 2, "gap": 8, "margin": 8},
                "style": {"card": "outline", "radius": 30, "glow": 0},
                "palette": palette, "widgets": [module]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        readings = dict(demo.readings)
        for key, value in changes.items():
            if value is None:
                readings.pop(key, None)
            else:
                readings[key] = Reading(key, value)
        images = dict(demo.images)
        if cover is None:
            images.pop("media.cover", None)
        elif cover != "demo":
            images["media.cover"] = cover
        snapshot = dataclasses.replace(demo, readings=readings, images=images,
                                       now=datetime(2026, 11, 7, hour, 5, 9))  # fmt: skip
        cases.append({"folder": str(folder), "snapshot": _snapshot_given(snapshot),
                      "dither": False})  # fmt: skip
        renderer = Renderer(load_theme(folder), preview=True)
        frame, boxes = renderer.render(snapshot, now=1000.0)
        expected.append({"frame": frame, "boxes": boxes, "slots": _slots_of(renderer)})

    for code in [0, 1, 2, 3, 45, 48, 51, 55, 61, 67, 80, 71, 77, 86, 95, 99, None, "x", 2.7]:
        for day in [{"weather.is_day": 1}, {"weather.is_day": 0}, {"weather.is_day": None}]:
            for hour in [12, 23] if day["weather.is_day"] is None else [12]:
                for items in ["forecast", "weather"]:
                    case(items, {"weather.code": code, "weather.temperature": 21.5, **day},
                         hour)  # fmt: skip
    for items in ["net", "network", "date", "ai", "clock"]:
        case(items, {})
    case("download\nnet", {"downloads": '{"files": []}'})
    case("playback", {"media.playing": 1})  # (the demo's cover)
    case("playback", {"media.playing": 1}, cover=None)  # (none: the third text, half over)
    case("playback", {"media.playing": 1}, cover=grey)  # (a grey one carries no colour)
    for color in ["#ff8800", "@accent", "@cover", "teal", "#11223380"]:
        case("forecast\nplayback", {"weather.code": 71, "media.playing": 1}, color=color)
        case("playback", {"media.playing": 1}, color=color, cover=None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    colours = set()
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        assert _compare_slot_frames(got, want, n), (n, got.get("unported"))
        assert "slots" in got, n
        colours.add(tuple(want["slots"][0][2]))
    assert len(colours) >= 12  # (the cards' colours, each its own)


# -- moving frames: what glides and fades -------------------------------------------------------


def _python_moving(folder: Path, steps: list[tuple[Snapshot, float]], fps: float = 0.0,
                   place: tuple[float, float] | None = None, preview: bool = True,
                   fans: Any = None) -> list[dict[str, Any]] | None:  # fmt: skip
    """Moving frames one after another by one renderer (animate: as the panels' loop draws
    them; your place `place`; a panel's frames, not a preview's, where not `preview`; what the
    person chose for the fans, `fans`, where given), each at its time: the frame, its boxes,
    whether it moves on and when it is due again; None for a theme Python does not read."""
    try:
        renderer = Renderer(load_theme(folder), animate=True, preview=preview)
    except ThemeError:
        return None
    renderer.fps = fps
    renderer.place = place
    if fans is not None:
        renderer.fan_choices = fans
    lists._RAN.clear()
    out: list[dict[str, Any]] = []
    for snapshot, now in steps:
        try:
            frame, boxes = renderer.render(snapshot, now=now)
            out.append({"frame": frame, "boxes": boxes, "moving": renderer.moving,
                        "due": renderer.due})  # fmt: skip
        except Exception as error:  # noqa: BLE001 (a frame Python cannot make)
            out.append({"failed": f"{type(error).__name__}: {error}"})
    return out


def _compare_moving(cases: list[dict[str, Any]], expected: list[list[dict[str, Any]] | None],
                    level: bool = False) -> tuple[int, int]:  # fmt: skip
    """Rust's moving frames against Python's, step after step: each to the bit (`level`: within
    a level, now and then, where light is blurred in floating point), its boxes, whether it
    moves on and when it is due again. After a frame Rust does not draw, the rest of its steps
    are left (what moves went on in Python's alone). How many were drawn, and of them how many
    move on."""
    import numpy as np

    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = moving = differ = total = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                break
            if "unported" in g:
                break
            if level:
                apart = np.abs(np.asarray(picture(g["frame"]), int) - np.asarray(w["frame"], int))
                assert apart.max() <= 1, (n, i)
                differ += int((apart.max(axis=2) > 0).sum())
                total += apart.shape[0] * apart.shape[1]
            else:
                assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            assert g["moving"] == w["moving"], (n, i)
            assert g["due"] == w["due"], (n, i)
            drawn += 1
            moving += w["moving"]
    assert differ <= total // 10000, (differ, total)
    return drawn, moving


MOVING_SENSORS = ["cpu.load", "gpu.temp", "audio.volume", "mem.used"]
MOVING_FORMATS = ["{value:.0f}", "{value:.1f}{unit}", "{value:.2f}", "{value}", "{value:.0f}%",
                  "{value:,.0f}", "{value:.3g}"]  # fmt: skip


def _moving_metric(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A metric whose number glides to a new reading (smooth: the theme's glide or its own
    glide_ms; the volume's at once unless it has one) and whose colour fades over its
    thresholds."""
    metric = _metric(rng, i, size)
    metric.update(sensor=rng.choice(MOVING_SENSORS), format=rng.choice(MOVING_FORMATS),
                  smooth=rng.random() < 0.7)  # fmt: skip
    metric.pop("needs", None)
    if rng.random() < 0.4:
        metric["glide_ms"] = rng.choice([0, 1, 100, 250, 1000, 3000])
    if rng.random() < 0.6:
        metric["color_rules"] = [
            {"above": rng.choice([10, 30, 50, 70, 90, 50.5, 50.0]),
             "color": rng.choice([_colour(rng), "@accent", _hex(rng, True)])}
            for _ in range(rng.randrange(1, 4))
        ]  # fmt: skip
    return metric


def _moving_metric_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _moving_metric(rng, i, size) if rng.random() < 0.75 else _card_or_text(rng, i, size)


# (how far apart frames come: at once, a 60th, 50th, 25th of a second, a tenth, at the edges of
# a pause (a quarter of a second) and of an idle second, two seconds, back in time)
MOVING_STEPS = [0, 1 / 60, 0.02, 0.04, 0.1, 0.2, 0.25, 0.26, 0.5, 1.0, 1.01, 2.0, -0.1]


def _moving_readings(rng: random.Random, before: dict[str, Reading]) -> dict[str, Reading]:
    """The readings of the next frame: each sensor's as before, or a new one now and then (any
    number from none to past its thresholds, none at all, the same value anew)."""
    readings = dict(before)
    for key in MOVING_SENSORS:
        if key not in readings or rng.random() < 0.35:
            value = rng.choice([rng.uniform(0, 100), rng.uniform(0, 100), rng.randrange(101),
                                0, 100, 50.5, 49.9, None, 1e9])  # fmt: skip
            if value is None and rng.random() < 0.5:
                readings.pop(key, None)
            else:
                readings[key] = Reading(key, value, unit=rng.choice(["", "%", "°C"]))
    if rng.random() < 0.1:
        readings["audio.muted"] = Reading("audio.muted", rng.choice([0, 1]))
    return readings


def _moving_given(readings: dict[str, Reading]) -> dict[str, Any]:
    return {"readings": {k: {"v": _typed(r.value), "unit": r.unit, "label": r.label}
                         for k, r in readings.items()}, "images": {}}  # fmt: skip


def _moving_themes(tmp_path: Path, rng: random.Random, count: int, make: Any,
                   level: bool = False) -> tuple[int, int]:  # fmt: skip
    """`count` themes of what `make` makes, each drawn frame after frame by one renderer as the
    panels' loop draws it (at a frame rate or none, the frames at any distance, the readings new
    between them now and then), by Rust and by Python: _compare_moving's counts."""
    from libre_panel.render import lit

    cases, expected = [], []
    for n in range(count):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, make)
        data["animation"] = {"smoothing_ms": rng.choice([0, 100, 400, 400, 1000, 5000])}
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        fps = rng.choice([0, 25, 30, 50, 60])
        now, readings = rng.uniform(1000, 2000), _moving_readings(rng, {})
        steps, given = [], []
        for _ in range(rng.randrange(2, 14)):
            steps.append((Snapshot(readings=readings), now))
            given.append({"snapshot": _moving_given(readings), "now": now})
            now += rng.choice(MOVING_STEPS)
            readings = _moving_readings(rng, readings)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given, "bytes": level})  # fmt: skip
        lit._PICTURES.clear()  # (a ring's light kept from a theme of another size: see above)
        expected.append(_python_moving(folder, steps, fps))
    return _compare_moving(cases, expected, level)


def test_moving_frames_of_metrics_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its metrics frame after frame as the panels' loop does: a smooth
    # number gliding to each new reading along its sensor's spring, its text at the history's
    # points, standing where it reads the same; a colour fading over its thresholds a step per
    # 0.18 s, for the bar and the number of a value together; after a pause each as it is)
    drawn, moving = _moving_themes(tmp_path, random.Random(0x3071), 60, _moving_metric_or_more)
    assert drawn > 300 and moving > 60, (drawn, moving)


def _moving_bar(rng: random.Random, i: int, size: tuple[int, int],
                lit: bool = False) -> dict[str, Any]:  # fmt: skip
    """A bar or a ring gliding to each new reading (smooth unless told not to: the theme's
    glide, its own glide_ms, the volume's a third of a second), its colour fading over its
    thresholds, a bar's dot at its head on the way with it; flat or facet, or `lit`."""
    if lit:
        widget = (_lit_bar if rng.random() < 0.5 else _lit_gauge)(rng, i, size)
    else:
        widget = rng.choice([_bar, _bar, _gauge, _gauge, _facet_bar])(rng, i, size)
    widget["sensor"] = rng.choice(MOVING_SENSORS)
    if rng.random() < 0.3:
        widget["smooth"] = rng.random() < 0.5
    if rng.random() < 0.4:
        widget["glide_ms"] = rng.choice([0, 1, 100, 250, 1000, 3000])
    if rng.random() < 0.6:
        widget["color_rules"] = [
            {"above": rng.choice([10, 30, 50, 70, 90, 50.5, 50.0, -5]),
             "color": rng.choice([_colour(rng), "@accent", _hex(rng, True)])}
            for _ in range(rng.randrange(1, 4))
        ]  # fmt: skip
    if "mark" in widget:
        widget["mark"] = rng.choice(MOVING_SENSORS)
    if widget["type"] == "bar" and rng.random() < 0.3:
        widget["end_dot"] = rng.choice([1, 2.5, 4])
    return widget


def _moving_bar_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    if rng.random() < 0.75:
        return _moving_bar(rng, i, size)
    return _moving_metric_or_more(rng, i, size)


def _moving_lit_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    if rng.random() < 0.8:
        return _moving_bar(rng, i, size, lit=True)
    return _moving_metric(rng, i, size)


def test_moving_frames_of_bars_and_rings_as_python_draws_them(tmp_path: Path) -> None:
    # (bars, facet bars and rings frame after frame: each where its gliding value stands, in
    # steps of the supersampled grid, with the numbers of its sensor; its colour, a gradient's
    # too, mixed on its way over a threshold; a bar's dot at its gliding head; still once its
    # value is as good as there)
    drawn, moving = _moving_themes(tmp_path, random.Random(0xBA60), 60, _moving_bar_or_more)
    assert drawn > 300 and moving > 80, (drawn, moving)


def test_moving_frames_of_lit_bars_and_rings_within_a_level(tmp_path: Path) -> None:
    # (lit bars and rings frame after frame: their light at the gliding value, in its colour on
    # the way; blurred in floating point, so now and then a pixel a level apart)
    drawn, moving = _moving_themes(tmp_path, random.Random(0x1A60), 50, _moving_lit_or_more, True)
    assert drawn > 200 and moving > 60, (drawn, moving)


def test_a_number_glides_and_its_colour_fades_as_python_shows_them(tmp_path: Path) -> None:
    # (a reading that jumps from 10 to 90 and back, frames a 50th of a second apart for two
    # seconds: the number on its way at each frame, its colour fading over two thresholds, the
    # same for numbers of the same rules (50 as 50.0) that are away a while and come back on
    # the way; at the theme's glide, its own, and at once; a bar with its dot and a
    # ring of the sensor on the way with the numbers, in their colours on the way, a ring that
    # is away a while too; moving while on the way)
    folder = tmp_path / "glide"
    folder.mkdir()
    rules = [{"above": 30, "color": "#ff8800"}, {"above": 50, "color": "#ff0000c0"}]
    twin = [{"above": 30.0, "color": "#ff8800"}, {"above": 50.0, "color": "#ff0000c0"}]
    twin2 = [{"above": 30.0, "color": "#ff8800"}, {"above": 50, "color": "#ff0000c0"}]
    twin3 = [{"above": 30, "color": "#ff8800"}, {"above": 50.0, "color": "#ff0000c0"}]

    def metric(i: int, **more: Any) -> dict[str, Any]:
        return {"type": "metric", "id": f"m{i}", "x": 2, "y": 2 + 14 * i, "w": 60, "h": 12,
                "sensor": "cpu.load", "format": "{value:.1f}", "color": "#ffffff",
                "smooth": True, "color_rules": rules, **more}  # fmt: skip

    ring = {"type": "gauge", "sensor": "cpu.load", "w": 30, "h": 30, "thickness": 6,
            "color": "#ffffff", "color2": "#2080ff"}  # fmt: skip
    widgets = [metric(0), metric(1, glide_ms=700), metric(2, smooth=False),
               metric(3, color_rules=twin, needs="cpu.temp"),
               metric(4, color_rules=twin2, needs="gpu.temp"),
               metric(5, color_rules=twin3, needs="mem.used"),
               {"type": "bar", "id": "b4", "x": 66, "y": 4, "w": 56, "h": 8, "sensor": "cpu.load",
                "color": "#ffffff", "color2": "#2080ff", "color_rules": rules, "end_dot": 2.5},
               {**ring, "id": "g5", "x": 66, "y": 18, "color_rules": rules},
               {**ring, "id": "g6", "x": 98, "y": 18, "color_rules": twin, "needs": "cpu.temp"},
               {**ring, "id": "g7", "x": 66, "y": 50, "glide_ms": 700}]  # fmt: skip
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "glide", "display": {"width": 128, "height": 84},
        "palette": {name: "#808080" for name in PALETTE}, "animation": {"smoothing_ms": 400},
        "background": {"color": "#101418"}, "widgets": widgets}), encoding="utf-8")  # fmt: skip
    steps, given = [], []
    for k in range(100):
        value = 10.0 if k < 5 or k >= 60 else 90.0
        readings = {"cpu.load": Reading("cpu.load", value)}
        if not 20 <= k < 40:  # (the twins away a while: back after it, on the fade's way)
            readings["cpu.temp"] = Reading("cpu.temp", 40)
        if not 20 <= k < 74:
            readings["gpu.temp"] = Reading("gpu.temp", 50)
        if not 20 <= k < 80:
            readings["mem.used"] = Reading("mem.used", 30)
        steps.append((Snapshot(readings=readings), 500.0 + k * 0.02))
        given.append({"snapshot": _moving_given(readings), "now": 500.0 + k * 0.02})
    for fps in (0, 50):
        case = {"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                "steps": given}  # fmt: skip
        drawn, moving = _compare_moving([case], [_python_moving(folder, steps, fps)])
        assert drawn == 100 and 40 < moving < 100, (fps, drawn, moving)


def test_a_value_off_the_panel_a_moment_glides_on_or_starts_anew_as_python_shows_it(
    tmp_path: Path,
) -> None:
    # (frames a 32nd of a second apart, a reading that jumps from 10 to 90 and two numbers and
    # rings of their own glide that are off the panel a while on the way: back after exactly a
    # pause (a quarter of a second) each glides on, back after a little more each starts where
    # it is; their colours over their thresholds the same; the numbers that stay all along)
    folder = tmp_path / "away"
    folder.mkdir()

    def rules(colour: str) -> list[dict[str, Any]]:
        return [{"above": 30, "color": colour}, {"above": 60, "color": "#ff0000"}]

    def metric(i: int, **more: Any) -> dict[str, Any]:
        return {"type": "metric", "id": f"m{i}", "x": 2, "y": 2 + 14 * i, "w": 60, "h": 12,
                "sensor": "cpu.load", "format": "{value:.2f}", "color": "#ffffff",
                "smooth": True, **more}  # fmt: skip

    widgets = [metric(0, color_rules=rules("#ff8800")),
               metric(1, glide_ms=700, needs="cpu.temp", color_rules=rules("#00ff88")),
               metric(2, glide_ms=900, needs="gpu.temp", color_rules=rules("#8800ff")),
               {"type": "gauge", "id": "g3", "x": 66, "y": 2, "w": 30, "h": 30, "thickness": 6,
                "sensor": "cpu.load", "color": "#ffffff", "glide_ms": 700, "needs": "cpu.temp",
                "color_rules": rules("#00ff88")},
               {"type": "bar", "id": "b4", "x": 66, "y": 40, "w": 56, "h": 8, "sensor": "cpu.load",
                "color": "#ffffff", "glide_ms": 900, "needs": "gpu.temp",
                "color_rules": rules("#8800ff")}]  # fmt: skip
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "away", "display": {"width": 128, "height": 56},
        "palette": {name: "#808080" for name in PALETTE}, "animation": {"smoothing_ms": 400},
        "background": {"color": "#101418"}, "widgets": widgets}), encoding="utf-8")  # fmt: skip
    steps, given = [], []
    for k in range(40):
        readings = {"cpu.load": Reading("cpu.load", 10.0 if k < 4 else 90.0)}
        if not 10 <= k < 18:  # (away 9 frames: back after 0.28125 s)
            readings["cpu.temp"] = Reading("cpu.temp", 40)
        if not 10 <= k < 17:  # (away 8 frames: back after 0.25 s, a pause)
            readings["gpu.temp"] = Reading("gpu.temp", 50)
        now = 500.0 + k / 32
        steps.append((Snapshot(readings=readings), now))
        given.append({"snapshot": _moving_given(readings), "now": now})
    # (and a frame at the time of the one before, which the number off the panel then missed:
    # it was last drawn in the frame before that one, half a second ago, and glides on)
    again, again_given = [], []
    for cpu, temp, now in [(10.0, True, 600.0), (90.0, True, 600.1), (90.0, False, 600.6),
                           (90.0, True, 600.6), (90.0, True, 600.64)]:  # fmt: skip
        readings = {"cpu.load": Reading("cpu.load", cpu)}
        readings.update({"cpu.temp": Reading("cpu.temp", 40)} if temp else {})
        readings["gpu.temp"] = Reading("gpu.temp", 50)
        again.append((Snapshot(readings=readings), now))
        again_given.append({"snapshot": _moving_given(readings), "now": now})
    cases = [
        {"folder": str(folder), "dither": False, "animate": True, "steps": given},
        {"folder": str(folder), "dither": False, "animate": True, "steps": again_given},
    ]
    drawn, moving = _compare_moving(cases, [_python_moving(folder, steps),
                                            _python_moving(folder, again)])  # fmt: skip
    assert drawn == 45 and moving > 12, (drawn, moving)


def test_a_muted_volume_whose_number_glides_past_its_value_as_python_shows_it(
    tmp_path: Path,
) -> None:
    # (the volume's number with a glide of its own, muted as it glides past a new value: the
    # word stands for it, its glide goes on, and while it passes its value its colour is not
    # told it was drawn, so after the muting it fades on or starts anew as in Python)
    folder = tmp_path / "muted"
    folder.mkdir()
    widget = {
        "type": "metric",
        "id": "v",
        "x": 2,
        "y": 2,
        "w": 60,
        "h": 12,
        "sensor": "audio.volume",
        "format": "{value:.0f}",
        "color": "#ffffff",
        "smooth": True,
        "glide_ms": 1500,
        "color_rules": [{"above": 30, "color": "#ff8800"}, {"above": 60, "color": "#ff0000"}],
    }
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "muted", "display": {"width": 64, "height": 16},
        "palette": {name: "#808080" for name in PALETTE}, "background": {"color": "#101418"},
        "widgets": [widget]}), encoding="utf-8")  # fmt: skip
    cases, expected = [], []
    for jump, back, muted in [(200, 6, range(6, 18)), (200, 6, range(5, 15)),
                              (90, 14, range(14, 26))]:  # fmt: skip
        steps, given = [], []
        for k in range(back + 40):
            volume = 10 if k < 2 else jump if k < back else 65
            readings = {"audio.volume": Reading("audio.volume", volume),
                        "audio.muted": Reading("audio.muted", int(k in muted))}  # fmt: skip
            steps.append((Snapshot(readings=readings), 700.0 + k / 32))
            given.append({"snapshot": _moving_given(readings), "now": 700.0 + k / 32})
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 46 + 46 + 54 and moving > 40, (drawn, moving)


def test_a_glide_of_more_milliseconds_than_a_float_holds_rust_leaves_out(tmp_path: Path) -> None:
    # (a glide_ms a float does not hold as it is: Python divides the whole number by 1000, Rust
    # would round it first; one a float holds glides as in Python)
    frames, cases = [], []
    for n, (kind, ms) in enumerate([(k, ms) for k in ("metric", "bar", "gauge")
                                    for ms in (2**53 + 1, 2**53, 2**52 + 1)]):  # fmt: skip
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        widget = {"type": kind, "id": "w", "x": 2, "y": 2, "w": 40, "h": 12 if kind != "gauge"
                  else 40, "sensor": "cpu.load", "smooth": True, "glide_ms": ms}  # fmt: skip
        if kind == "metric":
            widget["format"] = "{value:.3f}"
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "far", "display": {"width": 48, "height": 48},
            "palette": {name: "#808080" for name in PALETTE}, "background": {"color": "#000000"},
            "widgets": [widget]}), encoding="utf-8")  # fmt: skip
        steps, given = [], []
        for k, value in enumerate([10.0, 90.0, 90.0]):
            readings = {"cpu.load": Reading("cpu.load", value)}
            steps.append((Snapshot(readings=readings), 700.0 + k))
            given.append({"snapshot": _moving_given(readings), "now": 700.0 + k})
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        frames.append(_python_moving(folder, steps))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    for n, (got, want) in enumerate(zip(ours["frames"], frames, strict=True)):
        if n % 3 < 2:  # (2**53 + 1 and 2**53: more than 53 bits)
            assert got["steps"][0]["unported"] == "numbers a float does not hold as they are", n
            continue
        for g, w in zip(got["steps"], want, strict=True):
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), n
            assert g["moving"] == w["moving"], n


ROLLING_FORMATS = ["%H:%M:%S", "%S", "%M:%S", "%-S", "%I:%M:%S %p", "%H:%M", "%T", "%r", "%c",
                   "%S.%S", "%X", "%H:%M:%S %A", "%-I:%M:%S", "%S %S %S"]  # fmt: skip
# (how far apart frames come: at once, a 50th of a second, a 25th, a tenth, at the edges of the
# digits' time, a second and a moment more or less, a minute; in steps of whole microseconds)
ROLLING_STEPS = [0, 20000, 40000, 100000, 310000, 419999, 420000, 420001, 500000, 999999,
                 10**6, 1020000, 60 * 10**6]  # fmt: skip


def _rolling_clock(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A clock whose digits roll or fade for digits_seconds after the second turns (or jump, or
    for no time or more than a second: as they stand), in its letters' spacing, digits of one
    width, a width it fits, any alignment, a finish's own letters."""
    clock = _clock(rng, i, size)
    clock.update(
        format=rng.choice(ROLLING_FORMATS),
        digits=rng.choice(["roll", "roll", "fade", "jump"]),
        digits_seconds=rng.choice([0.42, 0.42, 1, 0.1, 0.9999, 1.5, 0, -1]),
    )
    if rng.random() < 0.2:
        clock["finish"] = rng.choice(["facet", "acrylic"])
    return clock


def _rolling_clock_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _rolling_clock(rng, i, size) if rng.random() < 0.7 else _card_or_text(rng, i, size)


def test_clocks_whose_digits_roll_or_fade_as_python_shows_them(tmp_path: Path) -> None:
    # (one renderer drawing its clocks frame after frame: from a moment of any part of a second,
    # each a frame of the digits' time and beyond it; what changes on the second rolls (the old
    # letter up and out, the new one in from below, soft at the edges of the digits' band) or
    # fades, letter by letter, in their colour, a finish's letters with theirs; what stays where
    # it stands; another size or width, as it stands)
    rng = random.Random(0xC10C)
    cases, expected = [], []
    for n in range(80):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _rolling_clock_or_more)
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        _, first = _clock_snapshot(rng)
        when, now = first.now, rng.uniform(1000, 2000)
        steps, given = [], []
        for _ in range(rng.randrange(2, 12)):
            steps.append((Snapshot(readings={}, now=when), now))
            given.append({"snapshot": {"readings": {}, "images": {}, "now": _moment(when)},
                          "now": now})  # fmt: skip
            gap = rng.choice(ROLLING_STEPS)
            when, now = when + timedelta(microseconds=gap), now + gap / 1e6
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 350 and moving > 60, (drawn, moving)


def test_a_rolling_clock_in_the_first_second_of_the_years_as_python_shows_it(
    tmp_path: Path,
) -> None:
    # (a clock whose digits roll, the frame's time in the first second of the year 1: Python
    # cannot take a second from it and leaves the clock out, warned; a second later it rolls,
    # and in the first second again, once its digits' time is over, it stands)
    folder = tmp_path / "first"
    folder.mkdir()
    widgets = [{"type": "clock", "id": "c", "x": 2, "y": 2, "font_size": 14, "format": "%S",
                "digits": kind, "color": "#ffffff"} for kind in ("roll",)]  # fmt: skip
    widgets.append({**widgets[0], "id": "f", "y": 20, "digits": "fade"})
    (folder / "theme.json").write_text(json.dumps({
        "format": THEME_FORMAT, "name": "first", "display": {"width": 48, "height": 40},
        "palette": {name: "#808080" for name in PALETTE}, "background": {"color": "#000000"},
        "widgets": widgets}), encoding="utf-8")  # fmt: skip
    moments = [
        datetime(1, 1, 1, 0, 0, 0, 100000),
        datetime(1, 1, 1, 0, 0, 1, 100000),
        datetime(1, 1, 1, 0, 0, 0, 900000),
        datetime(1, 1, 2, 0, 0, 0, 100000),
    ]
    renderer = Renderer(load_theme(folder), animate=True, preview=True)
    cases, expected = [], []
    for k, when in enumerate(moments):
        frame, boxes = renderer.render(Snapshot(readings={}, now=when), now=800.0 + k)
        expected.append((frame, boxes, renderer.moving, list(renderer.warnings)))
        cases.append({"snapshot": {"readings": {}, "images": {}, "now": _moment(when)},
                      "now": 800.0 + k})  # fmt: skip
    [ours] = run([{"op": "render_frame", "fonts_dir": str(fonts.FONT_DIR), "frames": [
        {"folder": str(folder), "dither": False, "animate": True, "steps": cases}]}])  # fmt: skip
    for k, (got, (frame, boxes, moving, warnings)) in enumerate(
        zip(ours["frames"][0]["steps"], expected, strict=True)
    ):
        assert got["sha256"] == hashlib.sha256(frame.tobytes()).hexdigest(), k
        assert (got["boxes"], got["moving"]) == (boxes, moving), k
        assert got["warnings"] == warnings, k
    assert expected[0][3] == ["widget 'c' failed: date value out of range",
                              "widget 'f' failed: date value out of range"]  # fmt: skip
    assert [e[2] for e in expected] == [False, True, False, True]


def test_a_clock_s_digits_at_the_edges_of_their_time_as_python_shows_them(
    tmp_path: Path,
) -> None:
    # (the digits' time of 0.42 s, half a second, a second and a little less, at the moments of
    # its edges: rolling or fading until just before its end, standing from it on)
    cases, expected = [], []
    for n, (seconds, kind) in enumerate([(0.42, "roll"), (0.5, "fade"), (1, "roll"),
                                         (0.9999, "fade"), (1.0, "fade")]):  # fmt: skip
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "edges", "display": {"width": 64, "height": 24},
            "palette": {name: "#808080" for name in PALETTE},
            "background": {"color": "#000000"}, "widgets": [{
                "type": "clock", "id": "c", "x": 2, "y": 2, "font_size": 16, "format": "%M:%S",
                "digits": kind, "digits_seconds": seconds, "color": "#40c0ff"}]}),
            encoding="utf-8")  # fmt: skip
        steps, given = [], []
        for k, micro in enumerate([0, 1, 419999, 420000, 420001, 499999, 500000, 999899,
                                   999900, 999999]):  # fmt: skip
            when = datetime(2026, 11, 8, 10, 0, 0, micro)
            steps.append((Snapshot(readings={}, now=when), 900.0 + k))
            given.append({"snapshot": {"readings": {}, "images": {}, "now": _moment(when)},
                          "now": 900.0 + k})  # fmt: skip
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    assert (drawn, moving) == (50, 3 + 6 + 10 + 8 + 10), (drawn, moving)


def test_a_rolling_clock_s_texts_fitted_otherwise_or_over_midnight_as_python_shows_them(
    tmp_path: Path,
) -> None:
    # (the second's texts that fit in other sizes, yet lay out alike in the first's: it stands;
    # of other lengths that the ellipsis makes alike: it stands; the microseconds of the second
    # before over midnight, as they are: it rolls)
    clocks = [({"format": "%S", "font_size": 10, "max_width": 7, "fit": "shrink", "tabular": False},
               datetime(2026, 11, 8, 10, 0, 13, 100000)),
              ({"format": "%-S", "font_size": 16, "max_width": 4, "fit": "ellipsis"},
               datetime(2026, 11, 8, 10, 0, 10, 100000)),
              ({"format": "%H:%M:%S.%f", "font_size": 9},
               datetime(2026, 11, 9, 0, 0, 0, 100000))]  # fmt: skip
    cases, expected = [], []
    for n, (more, when) in enumerate(clocks):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        widget = {"type": "clock", "id": "c", "x": 2, "y": 2, "digits": "roll",
                  "color": "#ffffff", **more}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps({
            "format": THEME_FORMAT, "name": "fit", "display": {"width": 96, "height": 24},
            "palette": {name: "#808080" for name in PALETTE},
            "background": {"color": "#000000"}, "widgets": [widget]}),
            encoding="utf-8")  # fmt: skip
        steps = [(Snapshot(readings={}, now=when), 700.0)]
        given = [{"snapshot": {"readings": {}, "images": {}, "now": _moment(when)}, "now": 700.0}]
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    # (on Windows, where the C library and the glyphs are another's, two of them roll)
    assert (drawn, moving) == (3, 2 if sys.platform == "win32" else 1), (drawn, moving)


def _gliding_list(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A list whose bars glide to each new reading (smooth, as lists are unless told not to;
    the theme's glide, or for the volume its own), or not; rows, bars, columns or tiles."""
    data = _list(rng, i, size)
    data["items"] += rng.choice(["", "\naudio.volume = Vol", "\ncore.*"])
    if rng.random() < 0.25:
        data["smooth"] = rng.random() < 0.5
    if rng.random() < 0.2:
        data["levels"] = False
    return data


def _gliding_list_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _gliding_list(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _list_readings_after(rng: random.Random, readings: dict[str, Reading]) -> dict[str, Reading]:
    """The readings of the next frame: as before, or now and then new numbers for some."""
    out = dict(readings)
    if rng.random() < 0.5:
        for key, reading in readings.items():
            number = reading.value
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                if rng.random() < 0.6:
                    value = rng.choice([number * rng.uniform(0.2, 1.8), rng.uniform(0, 100), 0])
                    out[key] = Reading(key, value, reading.unit, reading.label, reading.origin)
    if rng.random() < 0.3:
        out["audio.volume"] = Reading("audio.volume", rng.choice([10, 55, 90, 33.3]), "%")
    return out


def test_lists_whose_bars_glide_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its lists frame after frame: each bar on its way to its reading with
    # the bars and rings of its sensor, the volume's a third of a second behind, in steps of what
    # can be seen; drawn apart from the names and numbers, which stay as they are, and laid
    # under them; the tiles of cells too)
    rng = random.Random(0x1157)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _gliding_list_or_more)
        data["animation"] = {"smoothing_ms": rng.choice([0, 100, 400, 400, 1000])}
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        _, first = _list_snapshot(rng)
        readings, now, fps = dict(first.readings), rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps, given = [], []
        for _ in range(rng.randrange(2, 10)):
            steps.append((Snapshot(readings=readings, now=first.now), now))
            given.append({"snapshot": {
                "readings": {k: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                                 "origin": r.origin} for k, r in readings.items()},
                "images": {}, "now": _moment(first.now)}, "now": now})  # fmt: skip
            now += rng.choice(MOVING_STEPS)
            readings = _list_readings_after(rng, readings)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 250 and moving > 60, (drawn, moving)


def test_lists_whose_largest_value_fills_their_bars_glide_as_python_draws_them(
    tmp_path: Path,
) -> None:
    # (lists without a range of their own: the largest value shown fills the bar, and a bar
    # stands once it is within a five-hundredth of that span; one whose range is upside down,
    # its bars empty below its least, the same; frames a 50th of a second apart as the readings
    # jump there and back)
    cases, expected = [], []
    for n, (lo, hi) in enumerate([(0, 0), (50, 10)]):
        folder = tmp_path / f"t{n}"
        folder.mkdir()
        widget = {"type": "list", "id": "l", "x": 2, "y": 2, "w": 100, "h": 28,
                  "items": "core.*", "style": "bars", "font_size": 9, "min": lo, "max": hi,
                  "color": "#40c0ff", "muted": "#203040"}  # fmt: skip
        theme = {"format": THEME_FORMAT, "name": "largest", "display": {"width": 104, "height": 32},
                 "palette": {name: "#808080" for name in PALETTE},
                 "background": {"color": "#101418"}, "animation": {"smoothing_ms": 400},
                 "widgets": [widget]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
        steps, given = [], []
        for k in range(120):
            values = [12.0, 30.0, 47.0] if k < 3 or k >= 60 else [40.0, 8.0, 33.0]
            readings = {f"core.{i}": Reading(f"core.{i}", v, "%", f"Core {i}")
                        for i, v in enumerate(values, 1)}  # fmt: skip
            steps.append((Snapshot(readings=readings), 400.0 + k / 50))
            given.append({"snapshot": _moving_given(readings), "now": 400.0 + k / 50})
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 240 and moving > 180, (drawn, moving)


def _turning_fan(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """Fans that turn in a moving frame: lists of them and fan widgets (of the same sensors now
    and then, on one beat), some of them off the panel a while (their needs not met)."""
    pick = rng.random()
    if pick < 0.1:
        return _card_or_text(rng, i, size)
    data = _fan_list(rng, i, size) if pick < 0.5 else _fan(rng, i, size)
    if rng.random() < 0.2:
        data["needs"] = rng.choice(["fan.cpu", "!fan.2", "fan.1>1000", "gpu.fan"])
    return data


def _fan_readings_after(rng: random.Random, readings: dict[str, Reading]) -> dict[str, Reading]:
    """The fans' readings of the next frame: as before, or now and then new speeds for some
    (none, at rest, below what turns, turning, past the most), a reading gone or back."""
    out = dict(readings)
    if rng.random() < 0.5:
        for key in ["fan.1", "fan.2", "fan.cpu", "gpu.fan", "fan.gpu", "pump.fan"]:
            if rng.random() < 0.4:
                value = rng.choice([None, 0, 0.0, 12.0, 30.0, rng.uniform(30, 3000), 1200, 4500.0])
                if value is None and rng.random() < 0.5:
                    out.pop(key, None)
                else:
                    out[key] = Reading(key, value, "rpm", key.title())
    return out


def _fans_given(readings: dict[str, Reading], now: datetime) -> dict[str, Any]:
    return {"readings": {k: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                             "origin": r.origin} for k, r in readings.items()},
            "images": {}, "now": _moment(now)}  # fmt: skip


def test_fans_that_turn_as_python_turns_them(tmp_path: Path) -> None:
    # (one renderer drawing its fans frame after frame as the panels' loop does: each turning
    # with its speed, which follows the reading with inertia, runs out and stands at 0 RPM, its
    # ring and light following too; its blades where the beat of its sensor has them, the same
    # for every fan of the sensor; at a frame rate or none, the frames at any distance)
    rng = random.Random(0xFA4E)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _turning_fan)
        _, first = _fan_snapshot(rng)
        readings, now, fps = dict(first.readings), rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps, given = [], []
        for _ in range(rng.randrange(2, 14)):
            steps.append((Snapshot(readings=readings, now=first.now), now))
            given.append({"snapshot": _fans_given(readings, first.now), "now": now})
            now += rng.choice(MOVING_STEPS + [0.02, 0.04, 0.04])
            readings = _fan_readings_after(rng, readings)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 420 and moving > 300, (drawn, moving)  # (448 and 330)


def test_fans_in_what_the_person_chose_as_python_draws_them(tmp_path: Path) -> None:
    # (fan widgets of every look, paint and frame ("auto" ones among them) and lists of fans,
    # in what a person chose for the fans: a look and colours for all, and for some fans by
    # their sensor, or none; each as a still frame shows it, to the bit)
    drawn = _compare_frames(tmp_path, 0xFA62, 200, _turning_fan, _fan_widget_snapshot,
                            fans_of=_fan_choices)  # fmt: skip
    assert drawn > 170, drawn  # (186)


def test_fans_in_what_the_person_chose_turning_as_python_turns_them(tmp_path: Path) -> None:
    # (the same frame after frame in a moving frame: each fan turning on the beat of its sensor
    # for the blades of its look, the widget's or the one the person chose)
    rng = random.Random(0xFA63)
    cases, expected = [], []
    for n in range(40):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _turning_fan)
        choices = _fan_choices(rng)
        _, first = _fan_snapshot(rng)
        readings, now, fps = dict(first.readings), rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps, given = [], []
        for _ in range(rng.randrange(2, 10)):
            steps.append((Snapshot(readings=readings, now=first.now), now))
            given.append({"snapshot": _fans_given(readings, first.now), "now": now})
            now += rng.choice(MOVING_STEPS + [0.02, 0.04, 0.04])
            readings = _fan_readings_after(rng, readings)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given, "fan_choices": _choices_given(choices)})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps, fans=choices))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 200 and moving > 150, (drawn, moving)  # (223 and 169)


def test_fans_on_one_beat_through_pauses_and_back_in_time_as_python_turns_them(
    tmp_path: Path,
) -> None:
    # (two fan widgets of one sensor at other speeds and a list of it and another, all on the
    # sensor's beat; one fan off the panel a while; frames a 50th apart, a pause, frames a
    # moment back in time and further back (another clock), a fan that stops and runs out, one
    # whose reading goes and comes back)
    folder = tmp_path / "beat"
    folder.mkdir()
    fans = [{"type": "fan", "id": "a", "x": 0, "y": 0, "w": 34, "h": 34, "sensor": "fan.1",
             "max_rpm": 2000},
            {"type": "fan", "id": "b", "x": 36, "y": 0, "w": 34, "h": 34, "sensor": "fan.1",
             "max_rpm": 500, "frame": "none"},
            {"type": "fan", "id": "c", "x": 72, "y": 0, "w": 34, "h": 34, "sensor": "fan.2",
             "max_rpm": 3000, "needs": "fan.cpu"},
            {"type": "list", "id": "l", "x": 0, "y": 36, "w": 108, "h": 40, "style": "fans",
             "items": "fan.1 = One\nfan.2 = Two", "min": 0, "max": 3000,
             "font_size": 9}]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "beat", "display": {"width": 108, "height": 78},
             "palette": {name: "#808080" for name in PALETTE},
             "background": {"color": "#101418"}, "widgets": fans}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    at = datetime(2026, 11, 8, 6, 5, 9)
    times = [500.0 + k / 50 for k in range(12)] + [501.5, 501.52, 501.47, 501.49, 501.1,
                                                   501.12, 501.14, 503.0, 502.9]  # fmt: skip
    times += [503.0 + k / 25 for k in range(1, 40)]
    steps, given = [], []
    for k, now in enumerate(times):
        one = 1200.0 if k < 20 else 0.0 if k < 34 else None if k < 40 else 2400
        readings = {"fan.2": Reading("fan.2", 800, "rpm", "Two")}
        if one is not None:
            readings["fan.1"] = Reading("fan.1", one, "rpm", "One")
        if not 6 <= k < 16:
            readings["fan.cpu"] = Reading("fan.cpu", 900, "rpm", "CPU")
        steps.append((Snapshot(readings=readings, now=at), now))
        given.append({"snapshot": _fans_given(readings, at), "now": now})
    cases = [{"folder": str(folder), "dither": False, "animate": True, "fps": fps,
              "steps": given} for fps in (0, 25, 50)]  # fmt: skip
    expected = [_python_moving(folder, steps, fps) for fps in (0, 25, 50)]
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 3 * len(times) and moving > 150, (drawn, moving)


# (how far apart frames of a breathing moon come: as other moving frames, and over a breath)
BREATH_STEPS = [*MOVING_STEPS, 0.05, 0.149, 0.15, 0.3, 0.7, 1.3, 3.5, 7.0]


def _breathing_moon(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _moon(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def test_moons_whose_halo_breathes_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its moons frame after frame as the panels' loop does: the halo of
    # the moon as it looks breathing slowly, its strength at the frame's time in steps of a
    # tenth of a second (a step's edges too), the picture made anew where that changed, the
    # classic moon standing; at a frame rate or none, the frames at any distance, back in time
    # too, over a breath and more, the phase new now and then; moon modules the same)
    rng = random.Random(0x300E)
    cases, expected = [], []
    for n in range(70):
        folder = tmp_path / f"t{n}"
        if n < 55:
            _frame_theme(rng, folder, _breathing_moon)
            given, snapshot = _moon_snapshot(rng)
        else:
            import dataclasses

            _moon_module_theme(rng, folder)
            snapshot = dataclasses.replace(_demo(), history={})
            given = _snapshot_given(snapshot)
        now, fps = rng.choice([rng.uniform(1000, 2000), 1000.05, 1000.25]), rng.choice([0, 25, 50])
        steps, frames = [], []
        for _ in range(rng.randrange(2, 16)):
            steps.append((snapshot, now))
            frames.append({"snapshot": given, "now": now})
            now += rng.choice(BREATH_STEPS)
            if n < 55 and rng.random() < 0.15:
                given, snapshot = _moon_snapshot(rng)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": frames})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 550 and moving > 400, (drawn, moving)


def test_moons_at_the_edges_of_their_halo_s_steps_as_python_draws_them(tmp_path: Path) -> None:
    # (moons full, at 0.6 and at 0.3, and a classic one, in frames at the very half of a step
    # of the halo's time, where Python's round takes the even step: the halo's strength on
    # either side of it apart; back in time to a strength drawn before, and long after)
    folder = tmp_path / "halves"
    folder.mkdir()
    moons = [{"type": "moon", "id": "a", "x": 0, "y": 0, "size": 48, "sensor": "moon.phase",
              "color": "#f2efe6"},
             {"type": "moon", "id": "b", "x": 50, "y": 0, "size": 48, "sensor": "other.phase",
              "color": "@accent", "mirror": True},
             {"type": "moon", "id": "c", "x": 100, "y": 0, "size": 48, "sensor": "third.phase",
              "color": "#e8d8a0", "color2": "#2b3242"},
             {"type": "moon", "id": "d", "x": 150, "y": 0, "size": 40, "sensor": "moon.phase",
              "color": "#f2efe6", "pack": "classic"}]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "halves", "display": {"width": 200, "height": 50},
             "palette": {name: "#808080" for name in PALETTE},
             "background": {"color": "#101418"}, "widgets": moons}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    readings = {key: Reading(key, phase) for key, phase in
                (("moon.phase", 0.5), ("other.phase", 0.6), ("third.phase", 0.3))}  # fmt: skip
    times = [1000.25, 1000.55, 1000.95, 1001.15, 1003.15, 1005.25, 1005.85, 1006.65, 1000.25,
             2000.05]  # fmt: skip
    steps = [(Snapshot(readings=readings), now) for now in times]
    given = [{"snapshot": _moving_given(readings), "now": now} for now in times]
    cases = [{"folder": str(folder), "dither": False, "animate": True, "fps": fps,
              "steps": given} for fps in (0, 50)]  # fmt: skip
    expected = [_python_moving(folder, steps, fps) for fps in (0, 50)]
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == moving == 2 * len(times), (drawn, moving)


def _moving_analog(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A clock face whose second hand glides to each second (in the clock's own time, the
    digits' 0.42 s, or a time that is none) or goes round; now and then one that ticks or shows
    no second hand."""
    widget = _analog(rng, i, size)
    widget["seconds"] = rng.random() < 0.9
    widget["hand"] = rng.choice(["glide", "glide", "sweep", "sweep", "tick"])
    if rng.random() < 0.5:
        widget["digits_seconds"] = rng.choice([1, 1.0, 0.999, 0.25, 0.05, 0.0, -0.5, 2, 0.42])
    return widget


def _moving_analog_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _moving_analog(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _glowing_analog(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A clock face whose second hand moves, with a glow or a shadow or both, its face solid,
    see-through or none, the whole now and then see-through too."""
    widget = _moving_analog(rng, i, size)
    widget["face"] = rng.choice(["#1f2937", "#1f2937", "#1f293780", None, "@surface"])
    if rng.random() < 0.7:
        widget.update(glow=rng.choice([0.3, 0.8, 1.0]), glow_radius=rng.choice([2, 6, 10]))
    if rng.random() < 0.6:
        widget["shadow"] = rng.choice(["#000000", "#00000080", "@accent"])
        widget.update(shadow_blur=rng.choice([2, 6]), shadow_offset=rng.choice([0, 3, -2]))
    if rng.random() < 0.2:
        widget["opacity"] = rng.choice([0.5, 0.25])
    return widget


# (how far apart the frames of a moving clock come: at once, a 60th, 50th, 25th of a second, a
# tenth and so, the digits' time, half a second, a second, five, back in time)
CLOCK_STEPS = [0, 1 / 60, 0.02, 0.04, 0.1, 0.13, 0.25, 0.42, 0.5, 1.0, 2.5, 5.0, -0.1, -1.0]


def _clock_frames(
    rng: random.Random, start: datetime, count: int
) -> tuple[list[tuple[Snapshot, float]], list[dict[str, Any]]]:
    """`count` frames from the moment `start` on, a step of CLOCK_STEPS apart, the panel's
    clock going with it."""
    steps, given = [], []
    moment, now = start, rng.uniform(1000, 2000)
    for _ in range(count):
        readings, snapshot = _clock_moment(moment)
        steps.append((snapshot, now))
        given.append({"snapshot": readings, "now": now})
        step = rng.choice(CLOCK_STEPS)
        moment += timedelta(seconds=step)
        now += step
    return steps, given


def _moving_clocks(tmp_path: Path, rng: random.Random, count: int, make: Any,
                   level: bool = False) -> tuple[int, int]:  # fmt: skip
    """`count` themes of the clocks `make` makes, each drawn frame after frame by one renderer
    as the panels' loop draws it, by Rust and by Python: _compare_moving's counts."""
    cases, expected = [], []
    for n in range(count):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, make)
        steps, given = _clock_frames(rng, _analog_snapshot(rng)[1].now, rng.randrange(2, 16))
        fps = rng.choice([0, 25, 50])
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given, "bytes": level})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    return _compare_moving(cases, expected, level)


def test_clocks_whose_second_hand_glides_or_goes_round_as_python_draws_them(
    tmp_path: Path,
) -> None:
    # (one renderer drawing its clocks frame after frame as the panels' loop does: a second hand
    # that glides to each second as rolling digits do, in the clock's own time or the digits',
    # or goes round; the face with the hour and minute hands kept, the minute hand moving on
    # every five seconds; at rest for the rest of the second; frames at any distance, back in
    # time too. Windows' least from before Rust knew the computer's zone there, when another
    # place's clocks were left out)
    drawn, moving = _moving_clocks(tmp_path, random.Random(0xA7A5), 60, _moving_analog_or_more)
    least = (380, 280) if sys.platform == "win32" else (440, 340)
    assert drawn > least[0] and moving > least[1], (drawn, moving)


def test_clocks_whose_second_hand_moves_with_their_glow_and_shadow_within_a_level(
    tmp_path: Path,
) -> None:
    # (moving second hands on faces with a glow or a shadow: a solid face's kept while it is,
    # the hand lying on it; a see-through face's, or none's, made whole each frame; blurred in
    # floating point, so now and then a pixel a level apart)
    drawn, moving = _moving_clocks(tmp_path, random.Random(0xA7A6), 40, _glowing_analog, True)
    assert drawn > 240 and moving > 210, (drawn, moving)


def test_second_hands_at_the_edges_of_their_seconds_as_python_draws_them(tmp_path: Path) -> None:
    # (clocks gliding in the digits' time, in a second, in a quarter, one going round, one that
    # ticks, one without its second hand, at the edges: a second's very start, a moment before
    # and at the end of a glide, a second's last microsecond; over five seconds (the minute hand
    # moves on), a minute and noon; back in time to a face kept; faces of the cover's colour as
    # the cover changes: kept as they were while their hands stay, made anew once eight others
    # came after them, and a hand at rest in the cover's colour kept as it was for the rest of
    # its second; a clock going round alone, on the second: the frame moves on all the same)
    folder = tmp_path / "edges"
    folder.mkdir()
    clocks = [{"type": "analog", "id": "g", "x": 0, "y": 0, "w": 64, "h": 64, "hand": "glide",
               "color2": "@cover"},
              {"type": "analog", "id": "o", "x": 66, "y": 0, "w": 64, "h": 64, "hand": "glide",
               "digits_seconds": 1, "face": "@cover"},
              {"type": "analog", "id": "q", "x": 132, "y": 0, "w": 48, "h": 64, "hand": "glide",
               "digits_seconds": 0.25, "color": "@cover"},
              {"type": "analog", "id": "s", "x": 0, "y": 66, "w": 120, "h": 120, "hand": "sweep",
               "color2": "@cover", "marks": "@cover"},
              {"type": "analog", "id": "t", "x": 122, "y": 66, "w": 40, "h": 40, "hand": "tick"},
              {"type": "analog", "id": "n", "x": 122, "y": 110, "w": 40, "h": 40, "hand": "glide",
               "seconds": False}]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "edges", "display": {"width": 182, "height": 188},
             "palette": {name: "#808080" for name in PALETTE},
             "background": {"color": "#101418"}, "widgets": clocks}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    rng = random.Random(0xA7A7)
    covers = [noise(rng, "RGB", (40, 40)), noise(rng, "RGB", (40, 40))]
    base = datetime(2026, 11, 8, 11, 59, 50)
    frames = [(0, 0), (0, 1), (0, 249_999), (0, 250_000), (0, 419_999), (0, 420_000),
              (0, 999_999), (4, 999_999), (5, 0), (5, 200_000), (9, 900_000), (10, 100_000),
              (9, 950_000), (10, 300_000), *[(15 + 5 * k, 300_000) for k in range(9)],
              (10, 300_000), (10, 600_000), (0, 500_000)]  # fmt: skip
    steps, given = [], []
    for k, (second, micro) in enumerate(frames):
        moment = base + timedelta(seconds=second, microseconds=micro)
        cover = covers[k >= 6]  # (another cover from the seventh frame on)
        now = 2000.0 + second + micro / 1e6
        steps.append((Snapshot(readings={}, images={"media.cover": cover}, now=moment), now))
        given.append({"snapshot": {"readings": {}, "images": {"media.cover": image(cover)},
                                   "now": _moment(moment)}, "now": now})  # fmt: skip
    alone = tmp_path / "alone"
    alone.mkdir()
    sweep = {"type": "analog", "id": "s", "x": 0, "y": 0, "w": 60, "h": 60, "hand": "sweep"}
    data = {**theme, "name": "alone", "display": {"width": 60, "height": 60}, "widgets": [sweep]}
    (alone / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    seconds = [0, 1, 1, 6, 5]
    on_the_second = [_clock_moment(base + timedelta(seconds=k)) for k in seconds]
    lone = [(snapshot, 3000.0 + k) for k, (_, snapshot) in zip(seconds, on_the_second, strict=True)]
    lone_given = [{"snapshot": g, "now": 3000.0 + k}
                  for k, (g, _) in zip(seconds, on_the_second, strict=True)]  # fmt: skip
    cases = [{"folder": str(f), "dither": False, "animate": True, "fps": fps, "steps": g}
             for f, g in ((folder, given), (alone, lone_given)) for fps in (0, 50)]  # fmt: skip
    expected = [_python_moving(f, st, fps) for f, st in ((folder, steps), (alone, lone))
                for fps in (0, 50)]  # fmt: skip
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == moving == 2 * (len(frames) + len(seconds)), (drawn, moving)


GLIDE_SENSORS = ["cpu.load", "gpu.temp", "net.down", "game.fps"]


def _gliding_graph(rng: random.Random, i: int, size: tuple[int, int],
                   lit: bool = False) -> dict[str, Any]:  # fmt: skip
    """A graph of a sensor's history whose range is its own or has one end of its own, in
    whole steps now and then, flat (`lit`: with its glow, flat or lit, as _graph makes it); or
    something else."""
    if rng.random() < 0.2:
        return _card_or_text(rng, i, size)
    graph = _graph(rng, i, size)
    graph["sensor"] = rng.choice(GLIDE_SENSORS)
    graph["min"] = rng.choice([None, None, None, 0, -50, 10.5])
    graph["max"] = rng.choice([None, None, None, 100, 175])
    if rng.random() < 0.4:
        graph["range_step"] = rng.choice([5, 10, 0.5, 25])
    if not lit:
        for key in ("glow", "glow_radius", "finish"):
            graph.pop(key, None)
    return graph


def _glowing_graph(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _gliding_graph(rng, i, size, lit=True)


def _histories_after(rng: random.Random, history: dict[str, list[float]]) -> dict[str, list[float]]:
    """The histories a frame later: now and then a new value at the end (the same, near, a peak,
    a dip, nothing), the oldest gone past the most kept."""
    out = {}
    for key, values in history.items():
        values = list(values)
        if rng.random() < 0.6:
            last = values[-1] if values and math.isfinite(values[-1]) else 50.0
            values.append(rng.choice([last, last * 1.02, last * 0.97, last + 5, last * 3 + 10,
                                      0.0, last * 0.2]))  # fmt: skip
            values = values[-300:]
        out[key] = values
    return out


def _history_given(history: dict[str, list[float]], moment: datetime) -> dict[str, Any]:
    numbers = {k: [_typed(float(v)) for v in values] for k, values in history.items()}
    return {"readings": {}, "images": {}, "now": _moment(moment), "history": numbers}


def _gliding_graphs(tmp_path: Path, rng: random.Random, count: int, make: Any,
                    level: bool = False) -> tuple[int, int]:  # fmt: skip
    """`count` themes of the graphs `make` makes, each drawn frame after frame by one renderer
    as the panels' loop draws it, their histories growing, by Rust and by Python:
    _compare_moving's counts."""
    cases, expected = [], []
    for n in range(count):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, make)
        history = {k: _history(rng) for k in GLIDE_SENSORS if rng.random() < 0.9}
        moment = datetime(2026, 11, 7, rng.randrange(24), rng.randrange(60), 5)
        now, fps = rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps, given = [], []
        for _ in range(rng.randrange(2, 16)):
            steps.append((Snapshot(readings={}, history=history, now=moment), now))
            given.append({"snapshot": _history_given(history, moment), "now": now})
            now += rng.choice([*MOVING_STEPS, 0.3, 0.6, 1.2, 3.0, 3.5])
            history = _histories_after(rng, history)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given, "bytes": level})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    return _compare_moving(cases, expected, level)


def test_graphs_whose_own_range_glides_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its graphs frame after frame as the panels' loop does, their
    # histories growing: a range of its own gliding from the one drawn last toward the one it
    # goes to, wider at once where the values leave it, narrower once they used little of it
    # for a while, in whole steps faster and there once near; an end dot in the range of the
    # frame; at a frame rate or none, the frames at any distance, back in time too)
    drawn, moving = _gliding_graphs(tmp_path, random.Random(0x6AA), 60, _gliding_graph)
    assert drawn > 360 and moving > 140, (drawn, moving)


def test_graphs_whose_own_range_glides_with_their_glow_within_a_level(tmp_path: Path) -> None:
    # (gliding graphs with a glow, flat or lit: their light blurred in floating point, so now
    # and then a pixel a level apart)
    drawn, moving = _gliding_graphs(tmp_path, random.Random(0x6AB), 50, _glowing_graph, True)
    assert drawn > 300 and moving > 90, (drawn, moving)


def test_graph_ranges_widening_narrowing_and_in_steps_as_python_draws_them(
    tmp_path: Path,
) -> None:
    # (graphs of a range of their own (min and max none), one in whole steps from 0 up, one up to
    # 100, two with an end dot: a peak comes in (wider at once), stays a while, leaves the
    # window (narrower after three seconds of little use); frames a 50th of a second apart
    # through the glides, a pause, back in time (anew), at two frame rates)
    folder = tmp_path / "glide"
    folder.mkdir()
    graphs = [{"type": "graph", "id": "a", "x": 0, "y": 0, "w": 90, "h": 40, "sensor": "game.fps",
               "history": 30, "color": "#22d3ee", "end_dot": 2, "min": None, "max": None},
              {"type": "graph", "id": "b", "x": 0, "y": 44, "w": 90, "h": 40, "sensor": "game.fps",
               "history": 30, "color": "#f59e0b", "range_step": 25, "end_dot": 1.7, "min": 0,
               "max": None},
              {"type": "graph", "id": "c", "x": 0, "y": 88, "w": 90, "h": 40, "sensor": "cpu.load",
               "history": 30, "color": "#a3e635", "min": None, "max": 100,
               "fill": True}]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "glide", "display": {"width": 90, "height": 128},
             "palette": {name: "#808080" for name in PALETTE},
             "background": {"color": "#101418"}, "widgets": graphs}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    moment = datetime(2026, 11, 7, 20, 5, 5)
    fps_values = [60.0 + (k % 3) for k in range(30)]
    load = [20.0 + (k % 5) for k in range(30)]
    times = [700.0 + k / 50 for k in range(40)] + [701.2, 703.0, 704.5, 706.0, 706.02, 702.0,
                                                    702.02, 708.0]  # fmt: skip
    steps, given = [], []
    for k, now in enumerate(times):
        if k == 5:  # (a peak comes in)
            fps_values = [*fps_values[1:], 140.0]
            load = [*load[1:], 95.0]
        if k == 41:  # (it leaves the window)
            fps_values = [60.0 + (j % 3) for j in range(30)]
            load = [20.0 + (j % 5) for j in range(30)]
        history = {"game.fps": fps_values, "cpu.load": load}
        steps.append((Snapshot(readings={}, history=history, now=moment), now))
        given.append({"snapshot": _history_given(history, moment), "now": now})
    cases = [{"folder": str(folder), "dither": False, "animate": True, "fps": fps,
              "steps": given} for fps in (0, 50)]  # fmt: skip
    expected = [_python_moving(folder, steps, fps) for fps in (0, 50)]
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 2 * len(times) and moving > 60, (drawn, moving)


def _moving_agent(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _agent(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


def _agent_readings(rng: random.Random) -> dict[str, Reading]:
    """The agents' states: at work and waiting mostly, idle, off, one Python does not know, or
    none."""
    readings = {}
    for key in ("ai.claude.state", "ai.codex.state"):
        if rng.random() < 0.9:
            state = rng.choice(["working", "working", "waiting", "waiting", "idle", "off", "busy"])
            readings[key] = Reading(key, state)
    return readings


def test_agents_signs_that_move_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its agents' signs frame after frame as the panels' loop does: at
    # work three lights going round once in 1.6 s, waiting the ring breathing once in 2.4 s,
    # each in 48 steps of the frame's time; idle and off standing; the states new now and then;
    # at a frame rate or none, the frames at any distance, back in time too)
    rng = random.Random(0xA6F)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _moving_agent)
        readings = _agent_readings(rng)
        now, fps = rng.uniform(-100, 5000), rng.choice([0, 25, 50])
        steps, given = [], []
        for _ in range(rng.randrange(2, 14)):
            steps.append((Snapshot(readings=readings), now))
            given.append({"snapshot": _moving_given(readings), "now": now})
            now += rng.choice([*MOVING_STEPS, 1.6 / 48, 2.4 / 48, 0.8, 1.2, 1.6, 2.4])
            if rng.random() < 0.15:
                readings = _agent_readings(rng)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 350 and moving > 200, (drawn, moving)


def test_agents_signs_at_the_edges_of_their_steps_as_python_draws_them(tmp_path: Path) -> None:
    # (a sign at work and one waiting, in frames a float either side of the times their steps
    # change at (int() of the steps gone), at a round's and a breath's end, before nought (the
    # steps counted back from the last), far on, and back in time)
    folder = tmp_path / "steps"
    folder.mkdir()
    signs = [{"type": "agent", "id": "w", "x": 0, "y": 0, "size": 40, "sensor": "ai.claude.state",
              "color": "#22d3ee", "color2": "#f59e0b", "muted": "#5b6472"},
             {"type": "agent", "id": "b", "x": 44, "y": 0, "size": 40, "sensor": "ai.codex.state",
              "color": "#a3e635", "color2": "#f472b6", "muted": "#5b6472"}]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "steps", "display": {"width": 84, "height": 40},
             "palette": {name: "#808080" for name in PALETTE},
             "background": {"color": "#101418"}, "widgets": signs}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    readings = {"ai.claude.state": Reading("ai.claude.state", "working"),
                "ai.codex.state": Reading("ai.codex.state", "waiting")}  # fmt: skip
    times = []
    for k in (1, 5, 24, 47, 48, 49, 96, -1, -47, -48):
        for length in (1.6, 2.4):
            at = k * length / 48
            times += [math.nextafter(at, -math.inf), at, math.nextafter(at, math.inf)]
    times += [1e9, 1e9 + 0.0333, 123456.789, 0.0, -0.0, 3.2, 2.0]
    steps = [(Snapshot(readings=readings), now) for now in times]
    given = [{"snapshot": _moving_given(readings), "now": now} for now in times]
    cases = [{"folder": str(folder), "dither": False, "animate": True, "fps": fps,
              "steps": given} for fps in (0, 50)]  # fmt: skip
    expected = [_python_moving(folder, steps, fps) for fps in (0, 50)]
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == moving == 2 * len(times), (drawn, moving)


def _moving_symbol(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A weather symbol that moves (or now and then one that stands), its motion shifted by an
    offset of its own now and then."""
    if rng.random() < 0.15:
        return _card_or_text(rng, i, size)
    symbol = _symbol(rng, i, size)
    symbol["moving"] = rng.random() < 0.85
    if rng.random() < 0.4:
        symbol["offset"] = rng.choice([0.37, -1.2, 2, 0.02, 4.65])
    return symbol


def test_weather_symbols_that_move_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing its symbols frame after frame as the panels' loop does: each moving
    # with the planned time of the frame (its offset added), a picture a 25th of a second, the
    # weather new now and then; flat or lit, in either look; at a frame rate or none, the frames
    # at any distance, back in time too)
    rng = random.Random(0x5D3C)
    cases, expected = [], []
    for n in range(60):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _moving_symbol)
        given, snapshot = _symbol_snapshot(rng)
        now, fps = rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps, frames = [], []
        for _ in range(rng.randrange(2, 12)):
            steps.append((snapshot, now))
            frames.append({"snapshot": given, "now": now})
            now += rng.choice([*MOVING_STEPS, 0.07, 0.14, 0.8, 3.7])
            if rng.random() < 0.15:
                given, snapshot = _symbol_snapshot(rng)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": frames})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 340 and moving > 290, (drawn, moving)


def test_the_app_s_own_themes_in_moving_frames_as_python_draws_them(tmp_path: Path) -> None:
    # (every theme the app brings, drawn as the panels' loop draws it: frames a 25th of a second
    # apart and more, its readings new between them, at 50 fps; the same to the bit where Rust
    # draws all it holds, a level apart where its light is blurred in floating point, whether it
    # moves on as Python's; else Rust says what it does not draw yet)
    import dataclasses

    import numpy as np

    from libre_panel.sensors.demo import demo_snapshot

    first = dataclasses.replace(demo_snapshot(fixed_time=1_760_000_000.0),
                                now=datetime(2026, 11, 7, 18, 5, 9, 250))  # fmt: skip
    later = dict(first.readings)
    for key, reading in first.readings.items():
        number = reading.value
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            later[key] = dataclasses.replace(reading, value=number * 0.7 + 3)
    snapshots = [first, first, dataclasses.replace(first, readings=later),
                 dataclasses.replace(first, readings=later)]  # fmt: skip
    times = [900.0, 900.04, 900.08, 900.6]
    folders = sorted(p.parent for p in THEMES.glob("*/theme.json"))
    # (the histories the themes' graphs draw, their last numbers, as for a still frame)
    graphs = [w for f in folders for w in Renderer(load_theme(f), preview=True).widgets
              if w["type"] == "graph"]  # fmt: skip
    drawn_from = sorted({w["sensor"] for w in graphs} & set(first.history))
    history = {key: [_typed(float(v)) for v in first.history[key][-400:]] for key in drawn_from}

    def given(snapshot: Snapshot) -> dict[str, Any]:
        readings = {key: {"v": _typed(r.value), "unit": r.unit, "label": r.label,
                          "origin": r.origin} for key, r in snapshot.readings.items()}  # fmt: skip
        return {"readings": readings, "images": {k: image(v) for k, v in snapshot.images.items()},
                "history": history, "now": _moment(snapshot.now)}  # fmt: skip

    soft = {f for f in folders if _soft_light(f)}
    cases = [{"folder": str(f), "dither": False, "animate": True, "fps": 50, "bytes": f in soft,
              "steps": [{"snapshot": given(s), "now": t} for s, t in zip(snapshots, times,
                                                                       strict=True)]}
             for f in folders]  # fmt: skip
    steps = list(zip(snapshots, times, strict=True))
    expected = _exact_globes(tmp_path, lambda: [_python_moving(f, steps, 50) for f in folders])
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR),
                   "language": "en", "locale": str(i18n.LOCALE_DIR)}])  # fmt: skip
    drawn = []
    for folder, got, want in zip(folders, ours["frames"], expected, strict=True):
        assert want is not None, folder.name
        if any("unported" in g for g in got["steps"]):
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            assert "frame" in w, (folder.name, i, w.get("failed"))
            if folder in soft:  # (its light blurred in floating point: within a level)
                apart = np.abs(np.asarray(picture(g["frame"]), int) - np.asarray(w["frame"], int))
                assert apart.max() <= 1, (folder.name, i)
            else:
                assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (
                    folder.name, i)  # fmt: skip
            assert g["boxes"] == w["boxes"], (folder.name, i)
            assert g["moving"] == w["moving"], (folder.name, i)
        drawn.append(folder.name)
    # (Windows' least from before Rust knew the computer's zone there, when Terra's waited)
    assert len(drawn) >= (93 if sys.platform == "win32" else 99), drawn


# -- turned widgets ---------------------------------------------------------------------------

# (a quarter turn and its multiples either way, a whole turn either way, angles of every other
# kind, a hair off none and off a whole turn: -1e-20 % 360 is a whole turn, 360.0)
TURNS: list[float] = [90, 180, 270, -90, -180, -270, 360, -360, 45, 30.5, -12.25, 359.99, 0.3,
                      1e-7, -1e-20, 135, 225.5, 315, 89.9, 1, -1, 7.5, 0]  # fmt: skip


def _bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def test_points_and_boxes_turned_as_python_turns_them() -> None:
    # (the renderer's turns to the bit: a point turned about another (math.radians, the C
    # library's cosine and sine, as Python takes them), the box round a box turned (its corners
    # rounded to six places, floored and ceiled), a rotate's angle as float(rotate) % 360 makes
    # it, every angle of TURNS and any other, about whole, half and any other points)
    from libre_panel.render.renderer import _turned_box, _turned_point

    rng = random.Random(0x7E2F)

    def angle() -> float:
        pick = rng.random()
        if pick < 0.4:
            return float(rng.choice(TURNS)) % 360
        if pick < 0.5:
            return float(rng.randrange(0, 360))
        return rng.uniform(0, 360)

    def spot() -> float:
        pick = rng.random()
        if pick < 0.3:
            return float(rng.randrange(-600, 2400))
        if pick < 0.6:
            return rng.randrange(-1200, 4800) / 2
        return rng.uniform(-600, 2400)

    points = [[spot(), spot(), angle(), spot(), spot()] for _ in range(20000)]
    boxes = [[rng.randrange(-500, 2000), rng.randrange(-500, 2000), rng.randrange(0, 1000),
              rng.randrange(0, 1000), angle(), spot(), spot()] for _ in range(20000)]  # fmt: skip
    angles = [*map(float, TURNS), -0.0, 5e-324, -5e-324, 1e-300, -1e-300, 359.99999999999994,
              -359.99999999999994, 720.0, -720.5, 1e17, -1e17, 0.1, -0.1]  # fmt: skip
    angles += [rng.uniform(-360, 360) for _ in range(2000)]
    case = {"op": "turned", "points": [[_bits(v) for v in p] for p in points],
            "boxes": [[*b[:4], *map(_bits, b[4:])] for b in boxes],
            "angles": [_bits(a) for a in angles]}  # fmt: skip
    [ours] = run([case])
    for p, got in zip(points, ours["points"], strict=True):
        want = _turned_point((p[0], p[1]), (p[2], (p[3], p[4])))
        assert got == [_bits(want[0]), _bits(want[1])], p
    for b, got in zip(boxes, ours["boxes"], strict=True):
        assert got == _turned_box(b[:4], (b[4], (b[5], b[6]))), b
    for a, got in zip(angles, ours["angles"], strict=True):
        assert got == _bits(a % 360), a


def _turning(make: Any) -> Callable[[random.Random, int, tuple[int, int]], dict[str, Any]]:
    """`make`'s widgets, most of them turned (rotate: TURNS)."""

    def made(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
        widget = make(rng, i, size)
        if rng.random() < 0.75:
            widget["rotate"] = rng.choice(TURNS)
        return widget

    return made


GRAPH_UNDEFINED = ("graphs through points that are no numbers (a log scale of no finite span)",)
TURNED_FAMILIES: dict[str, tuple[Any, Any, dict[str, Any]]] = {
    "texts": (_card_or_text, None, {}),
    "metrics": (_metric_or_more, _metric_snapshot, {}),
    "icons": (_icon_or_more, _icon_snapshot, {}),
    "weather": (_weather_or_countdown, _weather_snapshot, {}),
    "clocks": (_clock_or_more, _clock_snapshot, {}),
    "analog clocks": (_analog_or_more, _analog_snapshot, {}),
    "bars": (_bar_or_more, _bar_snapshot, {}),
    "gauges": (_gauge_or_more, _bar_snapshot, {}),
    "calendars": (_calendar_or_more, _calendar_snapshot, {}),
    "agents": (_agent_or_more, _agent_snapshot, {}),
    "sun's ways": (_sunpath_or_more, _sunpath_snapshot, {}),
    "lit": (_lit, _lit_snapshot, {}),
    "lettered": (_lettered, _lettered_snapshot, {}),
    "pictures": (_image_or_more, _image_snapshot, {}),
    "theme pictures": (_theme_picture_or_more, _image_snapshot,  # (many Rust does not read)
                       {"undefined": (UNREAD,), "files": _theme_files, "least": 15}),
    "symbols": (_symbol_or_more, _symbol_snapshot, {}),
    "graphs": (_graph_or_more, _graph_snapshot, {"undefined": GRAPH_UNDEFINED}),
    "lists": (_list_or_more, _list_snapshot, {}),
    "fan lists": (_fan_list_or_more, _fan_snapshot, {}),
    "fans": (_fan_or_more, _fan_widget_snapshot, {}),
    "moons": (_moon_or_more, _moon_snapshot, {"looks": ("classic", "vivid")}),
    "downloads": (_download_or_more, _download_snapshot, {"when": _download_time}),
}  # fmt: skip


@pytest.mark.parametrize("family", list(TURNED_FAMILIES))
def test_turned_widgets_as_python_draws_them(tmp_path: Path, family: str) -> None:
    # (every kind of widget turned, clockwise: about the middle of its box, a text about the
    # point it is aligned at halfway down its line; a quarter turn and its multiples pixel for
    # pixel, any other angle smoothly, all of it kept; its box in the editor the box round it
    # turned, a frosted one's backdrop turned with it and its box where that now lies, its light
    # turned too; what lies over it turned with it: a bar's mark and dot, a graph's end dot, a
    # list's fans laid apart over it)
    make, snapshot_of, more = TURNED_FAMILIES[family]
    more = dict(more)
    least = more.pop("least", 25)
    seed = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16)
    drawn = _compare_frames(tmp_path, seed, 40, _turning(make), snapshot_of, **more)
    assert drawn > least, drawn


def _turned_modules(rng: random.Random, folder: Path) -> None:
    """The theme in `folder` with most of its modules turned (rotate: TURNS)."""
    path = folder / "theme.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for widget in data["widgets"]:
        if widget["type"] == "module" and rng.random() < 0.75:
            widget["rotate"] = rng.choice(TURNS)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_turned_modules_and_card_slots_as_python_draws_them(tmp_path: Path) -> None:
    # (a module turned as a whole about its middle: each of its parts, a slot's card on show and
    # its frame over it; the module's box in the editor the box round it turned)
    rng = random.Random(0x7E2D)
    cases, expected = [], []
    for n in range(120):
        folder = tmp_path / f"t{n}"
        _slot_theme(rng, folder)
        _turned_modules(rng, folder)
        given, snapshot = _slot_snapshot(rng)
        dither = rng.random() < 0.2
        cases.append({"folder": str(folder), "snapshot": given, "dither": dither})
        renderer = Renderer(load_theme(folder), preview=True, dither=dither)
        lists._RAN.clear()
        try:
            frame, boxes = renderer.render(snapshot, now=1000.0)
            expected.append({"frame": frame, "boxes": boxes, "slots": _slots_of(renderer)})
        except Exception as error:  # noqa: BLE001 (a frame Python cannot make)
            expected.append({"failed": f"{type(error).__name__}: {error}"})
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = sum(
        _compare_slot_frames(got, want, n)
        for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True))
    )
    assert drawn > 60, drawn


def test_turned_widgets_frame_after_frame_as_python_draws_them(tmp_path: Path) -> None:
    # (one renderer drawing turned widgets frame after frame as the panels' loop does: lists
    # whose bars glide, their names and numbers laid apart over them where they meet no bar;
    # fans that turn, a list's laid apart over it; card slots changing cards, the old one, the
    # line of light and the frame turned with the module; a piece turned as it was the frame
    # before is that frame's again)
    import dataclasses

    rng = random.Random(0x7E2E)
    kinds = ["playback", "music", "forecast", "weather", "net", "date", "clock", "text", "ring",
             "stat"]  # fmt: skip
    cases, expected = [], []
    for n in range(90):
        folder = tmp_path / f"t{n}"
        family = n % 3
        now, fps = rng.uniform(1000, 2000), rng.choice([0, 25, 50])
        steps: list[tuple[Snapshot, float]] = []
        given: list[dict[str, Any]] = []
        count = rng.randrange(2, 10)
        if family == 2:  # card slots
            _slot_theme(rng, folder, cards=kinds, moving=True)
            _turned_modules(rng, folder)
            snapshot = dataclasses.replace(_demo(), history={})
            for _ in range(count):
                steps.append((snapshot, now))
                now += rng.choice([0.02, 0.04, 0.1, 0.3, 1.0, 0, -0.1])
                readings = _slot_readings_after(rng, dict(snapshot.readings))
                snapshot = dataclasses.replace(snapshot, readings=readings)
            given = [{"snapshot": _snapshot_given(s), "now": t} for s, t in steps]
        else:
            make = _gliding_list_or_more if family == 0 else _turning_fan
            data = _frame_theme(rng, folder, _turning(make))
            if family == 0:
                data["animation"] = {"smoothing_ms": rng.choice([0, 100, 400, 400, 1000])}
                (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
            _, first = (_list_snapshot if family == 0 else _fan_snapshot)(rng)
            readings = dict(first.readings)
            after = _list_readings_after if family == 0 else _fan_readings_after
            for _ in range(count):
                steps.append((Snapshot(readings=readings, now=first.now), now))
                given.append({"snapshot": _fans_given(readings, first.now), "now": now})
                now += rng.choice(MOVING_STEPS + [0.02, 0.04])
                readings = after(rng, readings)
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 250 and moving > 100, (drawn, moving)


def test_turned_fan_lists_whose_fans_touch_as_python_draws_them(tmp_path: Path) -> None:
    # (fans whose pictures touch, edge to edge, and lie on none of the names and numbers: laid
    # apart over their list as Python lays them, and so turned apart, each about the list's
    # middle; those that overlap a pixel laid on the list and turned with it)
    from libre_panel.render import fan as fan_shapes

    cases, asked = [], []
    n = 0
    for fs in (6, 7, 9, 12):
        size = max(12, round(fs * 1.9))
        canvas = fan_shapes.geometry(size, size)[2]
        for columns, w, h in ((1, canvas, 2 * canvas), (1, canvas, 3 * canvas),
                              (1, canvas + 1, 2 * canvas), (2, 2 * canvas, canvas),
                              (3, 3 * canvas - 1, canvas)):  # fmt: skip
            for count, angle in ((2, 30.5), (3, 90), (4, 200.25), (6, 333.3), (3, 45)):
                folder = tmp_path / f"t{n}"
                folder.mkdir()
                n += 1
                widget = {"type": "list", "id": "l", "x": 6, "y": 5, "w": w, "h": h,
                          "style": "fans", "font_size": fs, "columns": columns, "levels": True,
                          "items": "\n".join(f"fan.{k}" for k in range(1, count + 1)),
                          "color": "#40c0ff", "muted": "#203040", "color2": "#ff8800",
                          "rotate": angle}  # fmt: skip
                side = max(w, h) * 2 + 12
                theme = {"format": THEME_FORMAT, "name": "touching",
                         "display": {"width": side, "height": side},
                         "palette": {name: "#808080" for name in PALETTE},
                         "background": {"color": "#101418"}, "widgets": [widget]}  # fmt: skip
                (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
                readings = {f"fan.{k}": Reading(f"fan.{k}", 450.0 * k, "rpm", f"Fan {k}")
                            for k in range(1, count + 1)}  # fmt: skip
                snapshot = Snapshot(readings=readings)
                cases.append({"folder": str(folder), "snapshot": _snapshot_given(snapshot),
                              "dither": False, "now": 1000.0})  # fmt: skip
                asked.append(_python_frame(folder, snapshot, False))
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = 0
    for i, (got, want) in enumerate(zip(ours["frames"], asked, strict=True)):
        assert "frame" in want, (i, want)
        assert got["sha256"] == hashlib.sha256(want["frame"].tobytes()).hexdigest(), i
        assert got["boxes"] == want["boxes"], i
        drawn += 1
    assert drawn == 100, drawn


def _crowded_list(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """A list whose bars glide, its names and numbers crowded on them: tall letters, small
    rows, narrow columns (now and then a letter comes within a pixel or two of a bar); now and
    then faded (laid joined)."""
    names = ["Core 1", "Tgpyq", "ÜÖÄ", "Ⅻ", "gggg", "Fan", "Ångström"]
    count = rng.randrange(1, 9)
    w, h = size
    faded = {"opacity": rng.choice([0.6, 0.85, 0.998])} if rng.random() < 0.3 else {}
    return {**faded, "type": "list", "id": f"l{i}", "x": rng.randrange(0, max(1, w // 3)),
            "y": rng.randrange(0, max(1, h // 3)), "w": rng.choice([20, 40, 60, 100, 200]),
            "h": rng.choice([8, 12, 20, 30, 50, 80]),
            "style": rng.choice(["rows", "bars", "columns"]),
            "items": "\n".join(f"core.{k} = {rng.choice(names)}" for k in range(1, count + 1)),
            "font_size": rng.choice([6, 8, 10, 12, 16, 20, 26, 40]),
            "columns": rng.choice([0, 0, 1, 2, 3]), "levels": True,
            "uppercase": rng.random() < 0.5,
            "format": rng.choice(["{value:.0f}%", "{value:,.1f} gy", "{value}"]),
            "color": _colour(rng), "muted": _colour(rng), "color2": _colour(rng),
            "rotate": rng.choice([30.5, 45, 89.9, 135, 200.25, 333.3, 90, 180])}  # fmt: skip


def test_turned_lists_whose_text_comes_near_their_bars_as_python_draws_them(
    tmp_path: Path,
) -> None:
    # (lists whose bars glide, turned, their names and numbers crowded on their bars: laid
    # apart from the bars where no letter lies in the box of a bar, as Python lays them, a
    # letter now and then within a pixel or two of one, where turned apart and turned joined
    # are not the same pixels)
    rng = random.Random(0x7E30)
    cases, expected = [], []
    for n in range(120):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _crowded_list)
        data["animation"] = {"smoothing_ms": 400}
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        count = 9
        readings = {f"core.{k}": Reading(f"core.{k}", rng.uniform(0, 100), "%", f"Core {k}")
                    for k in range(1, count + 1)}  # fmt: skip
        now, steps, given = rng.uniform(1000, 2000), [], []
        for _ in range(3):
            steps.append((Snapshot(readings=readings), now))
            given.append({"snapshot": _moving_given(readings), "now": now})
            now += rng.choice([0.04, 0.1, 0.2])
            readings = {k: Reading(k, rng.uniform(0, 100), "%", r.label)
                        for k, r in readings.items()}  # fmt: skip
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 250 and moving > 100, (drawn, moving)


# -- pictures that move in a moving frame -----------------------------------------------------

# (a slideshow's lines with pictures that move: animated ones alone, in a folder's pattern,
# beside stills, the GIF cut short, none there)
SLIDE_MOVING = [*THEME_MOVING[:3], "anim/*", "anim/*.gif", "pics/a.png", "pics/*.png",
                "pics/none.png", "anim/cut.gif", "@media.cover", "pics/g.gif"]  # fmt: skip


def _moving_slideshow(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    """An image widget showing a slideshow with pictures that move (or an animated picture),
    `seconds` each (the animated ones whole rounds, at their speed)."""
    widget = _theme_picture(rng, i, size)
    if rng.random() < 0.75:
        widget["src"] = rng.choice(["", "", "pics/b.png", "anim/run.gif"])
        lines = rng.sample(SLIDE_MOVING, rng.randrange(1, 5))
        widget["slides"] = "\n".join(lines)
        widget["seconds"] = rng.choice([0, 1, 2, 3, 0.5, 2.5, 10, 60])
        if rng.random() < 0.4:
            widget["speed"] = rng.choice([0.25, 0.5, 2, 4, 10, 0.1])
    return widget


def _moving_slideshow_or_more(rng: random.Random, i: int, size: tuple[int, int]) -> dict[str, Any]:
    return _moving_slideshow(rng, i, size) if rng.random() < 0.8 else _card_or_text(rng, i, size)


@pytest.mark.parametrize("animate", [True, False])
def test_pictures_that_move_frame_after_frame_as_python_shows_them(
    tmp_path: Path, animate: bool
) -> None:
    # (one renderer showing animated pictures and slideshows with pictures that move frame
    # after frame, in moving frames (the frame moves on with them) and in still ones: each
    # picture from its first frame when it first shows, then the frame of the moment at its
    # speed, round and round; a slideshow's picture that moves playing whole rounds, as many as
    # take its seconds at least, a still one its seconds, then the next from its start; the
    # same moment again, a part of a frame, a frame, a round, a minute later; back in time)
    rng = random.Random(0xA42E if animate else 0xA42F)
    cases, expected, unread = [], [], []
    for n in range(50):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _moving_slideshow_or_more)
        _theme_files(rng, folder)
        if rng.random() < 0.8:  # (most whole, of frames of their own lengths, for the patterns)
            size = _picture_size(rng)
            whole = [noise(rng, "RGB", size) for _ in range(rng.randrange(2, 6))]
            whole[0].save(folder / "anim" / "cut.gif", save_all=True, append_images=whole[1:],
                          duration=[rng.choice(FRAME_MS) for _ in whole], loop=0)  # fmt: skip
        steps: list[tuple[Snapshot, float]] = []
        given = []
        now = rng.uniform(1000, 10**6)
        fps = rng.choice([0, 25, 50])
        for _ in range(rng.randrange(2, 12)):
            readings, snapshot = _image_snapshot(random.Random(n))
            steps.append((snapshot, now))
            given.append({"snapshot": readings, "now": now})
            now += rng.choice([0, 0.004, 0.02, 0.05, 0.1, 0.3, 1, 2.5, 7, 60, -0.5, -20])
        cases.append({"folder": str(folder), "dither": False, "steps": given,
                      **({"animate": True, "fps": fps} if animate else {})})  # fmt: skip
        # (the GIF where it is cut short, by its name or a pattern of its folder)
        unread.append(_unread_in(folder) or "anim/*" in (folder / "theme.json").read_text())
        if animate:
            expected.append(_python_moving(folder, steps, fps))
        else:
            try:
                expected.append(_python_steps(folder, steps))
            except ThemeError:
                expected.append(None)
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    drawn = moving = 0
    for n, (got, want) in enumerate(zip(ours["frames"], expected, strict=True)):
        if want is None:  # (a theme Python does not read: nor does Rust)
            assert "error" in got, n
            continue
        for i, (g, w) in enumerate(zip(got["steps"], want, strict=True)):
            if "failed" in w:  # (Python's frame fails: Rust makes none)
                assert "unported" in g, (n, i, g, w)
                break
            # (a file Rust does not read, or what it does not draw here (on Windows, a slideshow
            # by the clock): what moves goes on in Python's)
            if "unported" in g:
                assert g["unported"] in NOT_HERE or g["unported"] == UNREAD and unread[n], (n, i, g)
                break
            assert g["sha256"] == hashlib.sha256(w["frame"].tobytes()).hexdigest(), (n, i)
            assert g["boxes"] == w["boxes"], (n, i)
            if animate:
                assert g["moving"] == w["moving"], (n, i)
                assert g["due"] == w["due"], (n, i)
                moving += w["moving"]
            drawn += 1
    assert drawn > (170 if NOT_HERE else 200), drawn  # (on Windows, 190 and 202 of them)
    assert not animate or moving > 80, moving


def test_slideshows_whose_rounds_count_near_a_whole_as_python_counts_them(tmp_path: Path) -> None:
    # (an animation whose rounds make a slideshow's seconds a hair over a whole number of them,
    # as floats divide: 7 s of 0.026 + 0.030 s rounds are 125.00000000000001 of them, which
    # Python takes for 125, not 126: the next picture shows from 7.000000000000001 s on; at its
    # speed too; a moment back in time, the animation again from its start)
    cases, expected = [], []
    rng = random.Random(0x5110E)
    for n, (ms, speed, seconds) in enumerate([((26, 30), 1, 7), ((22, 20), 1.5, 7),
                                              ((36, 20), 2, 7), ((26, 30), 4, 7)]):  # fmt: skip
        folder = tmp_path / f"t{n}"
        (folder / "anim").mkdir(parents=True)
        (folder / "pics").mkdir()
        frames = [noise(rng, "RGB", (12, 10)) for _ in ms]
        frames[0].save(folder / "anim" / "tick.png", save_all=True, append_images=frames[1:],
                       duration=list(ms))  # fmt: skip
        noise(rng, "RGB", (12, 10)).save(folder / "pics" / "still.png")
        widget = {"type": "image", "id": "m0", "x": 1, "y": 1, "w": 12, "h": 10, "src": "",
                  "slides": "anim/tick.png\npics/still.png", "seconds": seconds,
                  "speed": speed}  # fmt: skip
        theme = {"format": THEME_FORMAT, "name": "rounds", "display": {"width": 20, "height": 16},
                 "background": {"color": "#101418"}, "widgets": [widget]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
        length = sum(m / 1000 for m in ms) / speed
        shows = math.ceil(seconds / length - 1e-6) * length  # (the tick's turn ends)
        times = [1000.0, 1000.0 + shows / 2, 1000.0 + shows + (length / 2), 1000.0 + shows + 0.001]
        steps = [(Snapshot(), t) for t in times]
        given = [{"snapshot": _snapshot_given(Snapshot()), "now": t} for t in times]
        cases.append({"folder": str(folder), "dither": False, "animate": True, "steps": given})
        expected.append(_python_moving(folder, steps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn == 16 and moving == 12, (drawn, moving)


def test_downloads_whose_light_runs_frame_after_frame_as_python_draws_them(tmp_path: Path) -> None:
    # (a download of a size not known, its stretch of light running to and fro in its groove
    # frame after frame as the panels' loop draws it, flat or lit: where it is at each frame's
    # time; the frame moves on while it runs, not while it stands (a share shown, or paused);
    # the download going on in another state now and then; frames a part of a second to a minute
    # apart, and back in time)
    rng = random.Random(0xD0A3)
    cases, expected = [], []
    for n in range(80):
        folder = tmp_path / f"t{n}"
        _frame_theme(rng, folder, _download_or_more)

        def state() -> dict[str, Reading]:
            made = _download_state(rng, wild=0.1)
            if rng.random() < 0.6:  # (a size not known: the light runs)
                made.pop("total", None)
            return {"downloads": Reading("downloads", json.dumps(made))}

        readings = state()
        now = rng.choice([1000.0, rng.uniform(0, 1e5), 1.7e9 + rng.random()])
        fps = rng.choice([0, 25, 50])
        steps, given = [], []
        for _ in range(rng.randrange(2, 10)):
            steps.append((Snapshot(readings=readings), now))
            given.append({"snapshot": {"readings": {k: _typed(r.value)
                                                    for k, r in readings.items()}},
                          "now": now})  # fmt: skip
            now += rng.choice([0.02, 0.04, 0.1, 0.3, 0.6, 1.2, 5, 60, -0.3])
            if rng.random() < 0.2:
                readings = state()
        cases.append({"folder": str(folder), "dither": False, "animate": True, "fps": fps,
                      "steps": given})  # fmt: skip
        expected.append(_python_moving(folder, steps, fps))
    drawn, moving = _compare_moving(cases, expected)
    assert drawn > 300 and moving > 100, (drawn, moving)

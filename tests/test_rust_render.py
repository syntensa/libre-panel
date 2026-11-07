"""rust/render against render/ (docs/RUST.md, R4): the texts readings become (formatting.py):
str.format locked down to value, unit and label, every spec of Python's format() on every
kind of value, the app's own specs, and the usual text of a reading."""

from __future__ import annotations

import math
import random
import struct
from typing import Any

from rust_check import run

from libre_panel.render import formatting

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

"""rust/py against Python (docs/RUST.md): what Python does that the app's files and messages
depend on, as the Rust app does it: str's repr, its white space and digits (Python's regular
expressions and int() go by them), lower() and upper(), the division of ints, over every code
point where the Unicode database is the release's (Python 3.12: Unicode 15.0)."""

from __future__ import annotations

import calendar
import fnmatch
import hashlib
import json
import math
import random
import re
import string
import struct
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from rust_check import run

from libre_panel import i18n

RELEASE_UNICODE = "15.0.0"


def test_repr_quotes_and_escapes_as_python_does() -> None:
    rng = random.Random(0x9E9)
    # characters whose kind has not changed since long before Unicode 15: ASCII and its
    # controls, Latin-1, the general punctuation, CJK, the private use area, emoji
    pools = [
        range(0, 0x80),
        range(0x80, 0x100),
        range(0x2000, 0x2070),
        range(0x4E00, 0x4F00),
        range(0xE000, 0xE010),
        range(0x1F600, 0x1F650),
        [0x27, 0x22, 0x5C, 0x10FFFF],
    ]
    texts = ["", "abc", "it's", "'\"", "a\\b\n\t\r"]
    for _ in range(400):
        texts.append("".join(chr(rng.choice(rng.choice(pools))) for _ in range(rng.randrange(8))))
    [ours] = run([{"op": "py", "texts": texts}])
    assert ours["reprs"] == [repr(text) for text in texts]


def test_lower_upper_and_casefold_as_python_s() -> None:
    rng = random.Random(0x10E)
    # the capital sigma among cased letters, case-ignorable ones (marks, modifiers, the
    # apostrophe, the full stop, format characters) and caseless ones; letters that lower()
    # or upper() make two of (İ, ß, ŉ, ﬃ, ΐ); Greek, Cyrillic, Armenian, Georgian, Cherokee,
    # Deseret, Adlam, the fullwidth Latin, circled letters, Roman numerals
    pools = [
        "ΣΣΣΣσς",
        "AbΑβДжՁ\u10a0Ᏼꭰǅᾈ\U00010400\U0001e900Ａｚⓐⓩⅰⅷªº",
        "\u0301\u0345\u20dd'.:\u00b7\u2019ʰ\u00ad\u200d\u02c2",
        " 1-_中\u05d0\u0627😀\n",
        "İßŉﬃΐǰẞ",
    ]
    texts = ["", "Σ", "ΑΣ", "ΑΣΑ", "Α'Σ", "ΑΣ'", "Α\u0301Σ\u0301", "ΑΣ\u0301Α", "ΣΑΣ ΣΑΣ."]
    for _ in range(2000):
        texts.append("".join(rng.choice(rng.choice(pools)) for _ in range(rng.randrange(7))))
    [ours] = run([{"op": "py", "texts": texts}])
    assert ours["lowers"] == [text.lower() for text in texts]
    assert ours["uppers"] == [text.upper() for text in texts]
    assert ours["casefolds"] == [text.casefold() for text in texts]


def test_ints_divide_to_the_nearest_float() -> None:
    rng = random.Random(0xD1F)
    pairs = [(0, 5), (0, -5), (1, 3), (-7, 2), (2**53 + 1, 1), (2**127 - 1, 255), (-(2**127), 1)]
    for _ in range(3000):
        a = rng.randrange(-(2 ** rng.randrange(1, 128)), 2 ** rng.randrange(1, 128))
        b = rng.choice([255, 100, 3, -7]) if rng.random() < 0.5 else rng.randrange(1, 2**63)
        pairs.append((a, b if rng.random() < 0.8 else -b))
    divisions = [[str(a), str(b)] for a, b in pairs]
    [ours] = run([{"op": "py", "texts": [], "divisions": divisions}])
    assert [q.hex() for q in ours["quotients"]] == [(a / b).hex() for a, b in pairs]


@pytest.mark.skipif(
    unicodedata.unidata_version != RELEASE_UNICODE,
    reason="the tables are Unicode 15.0's, as the release's Python (3.12) has them",
)
def test_digits_printable_white_space_and_case_over_every_code_point() -> None:
    # lower(), upper() and casefold() of each character, and what the final sigma makes of it:
    # one that is skipped (case-ignorable), a letter of a case, or neither
    sigma = {("ς", "σ"): "I", ("σ", "σ"): "C", ("ς", "ς"): "N"}
    lines, cases, folds = [], [], []
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        ch = chr(code)
        digit = unicodedata.decimal(ch, -1) if unicodedata.category(ch) == "Nd" else -1
        printable, space = ch.isprintable(), ch.isspace()
        if digit != -1 or not printable or space:
            lines.append(f"{code:04X} {digit} {int(printable)} {int(space)}\n")
        low, up = ch.lower(), ch.upper()
        kind = sigma[("AΣ" + ch).lower()[1], ("AΣ" + ch + "B").lower()[1]]
        if low != ch or up != ch or kind != "N":
            low_codes = ".".join(f"{ord(c):X}" for c in low)
            up_codes = ".".join(f"{ord(c):X}" for c in up)
            cases.append(f"{code:04X} {low_codes} {up_codes} {kind}\n")
        if (folded := ch.casefold()) != ch:
            folds.append(f"{code:04X} {'.'.join(f'{ord(c):X}' for c in folded)}\n")
    [ours] = run([{"op": "py", "texts": []}])
    assert ours["sha256"] == hashlib.sha256("".join(lines).encode()).hexdigest()
    assert ours["case_sha256"] == hashlib.sha256("".join(cases).encode()).hexdigest()
    assert ours["fold_sha256"] == hashlib.sha256("".join(folds).encode()).hexdigest()


# what JSON's strings hold: the escapes, quotes and controls, Latin-1, the line separators
# Python's splitlines() knows, CJK, an astral emoji, a private use character
_CHARS = ['a', 'Z', ' ', '"', '\\', '/', '\n', '\t', '\x00', '\x1f', '\x7f', 'é', ' ',
          '\u0085', '中', '😀', '', '﻿', '퟿', '￿']  # fmt: skip


def _json_text(rng: random.Random, depth: int = 0) -> str:
    """A JSON text as anyone may write one: white space anywhere, escapes of any kind (pairs and
    halves of surrogates among them), numbers of every form, ints of many digits, NaN and
    Infinity, a key given twice."""

    def space() -> str:
        return "".join(rng.choice(" \t\n\r") for _ in range(rng.choice([0, 0, 0, 1, 2])))

    def string() -> str:
        out = []
        for _ in range(rng.randrange(6)):
            ch = rng.choice(_CHARS)
            kind = rng.randrange(6)
            if kind == 0:
                out.append(f"\\u{ord(ch):04x}" if ord(ch) < 0x10000 else "\\ud83d\\ude00")
            elif kind == 1:
                out.append(rng.choice(['\\"', "\\\\", "\\/", "\\b", "\\f", "\\n", "\\r", "\\t"]))
            elif kind == 2 and rng.random() < 0.1:
                out.append(rng.choice(["\\ud800", "\\udfff", "\\ud83d\\u0041", "\\uDBFF\\uDFFF"]))
            elif ch in '"\\' or ord(ch) < 0x20:
                out.append(json.dumps(ch)[1:-1])
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'

    def number() -> str:
        kind = rng.randrange(6)
        if kind == 0:
            return str(rng.randrange(-(10**6), 10**6))
        if kind == 1:
            return str(rng.randrange(-(10**40), 10**40))
        if kind == 2:
            return rng.choice(["0", "-0", "0.0", "-0.0", "1e16", "1E-5", "2.5e+300", "1e400"])
        if kind == 3:
            return rng.choice(["NaN", "Infinity", "-Infinity"])
        whole = str(rng.randrange(10 ** rng.randrange(1, 20)))
        fraction = (
            "." + str(rng.randrange(10 ** rng.randrange(1, 20))) if rng.random() < 0.7 else ""
        )
        exponent = rng.choice(["", "e", "E"]) + rng.choice(["", "+", "-"]) + str(rng.randrange(330))
        return rng.choice(["", "-"]) + whole + fraction + (exponent if exponent[0] in "eE" else "")

    kind = rng.randrange(9 if depth < 4 else 6)
    if kind == 0:
        return rng.choice(["null", "true", "false"])
    if kind in (1, 2):
        return number()
    if kind in (3, 4, 5):
        return string()
    items = [_json_text(rng, depth + 1) for _ in range(rng.randrange(4))]
    if kind in (6, 7):
        keys = [string() for _ in items]
        if len(keys) > 1 and rng.random() < 0.3:
            keys[-1] = keys[0]  # given twice: the last value in the first one's place
        pairs = [
            space() + k + space() + ":" + space() + v + space()
            for k, v in zip(keys, items, strict=True)
        ]
        return "{" + ",".join(pairs) + space() + "}"
    return "[" + ",".join(space() + v + space() for v in items) + space() + "]"


def _broken(rng: random.Random, text: str) -> str:
    """The text with a slip or two: a character gone, one too many, cut short."""
    for _ in range(rng.choice([1, 1, 2])):
        at = rng.randrange(len(text) + 1)
        kind = rng.randrange(4)
        if kind == 0 and text:
            text = text[:at] + text[at + 1 :]
        elif kind == 1:
            text = text[:at] + rng.choice(',:[]{}"\\ 0eE.+-ntfNIu\x01x') + text[at:]
        elif kind == 2:
            text = text[:at]
        else:
            text = text[:at] + text[at : at + 1] * 2 + text[at + 1 :]
    return text


def _python_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        return {"error": str(error), "kind": "decode"}
    except RecursionError as error:
        return {"error": str(error), "kind": "recursion"}
    except ValueError as error:
        return {"error": str(error), "kind": "value"}
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:  # half a surrogate pair: Rust's str cannot hold it
        return {"half": True}
    return {
        "repr": repr(value),
        "dumps": [
            json.dumps(value),
            json.dumps(value, indent=2, ensure_ascii=False),
            json.dumps(value, sort_keys=True, ensure_ascii=False),
            json.dumps(value, separators=(",", ":")),
        ],
    }


@pytest.mark.skipif(
    sys.version_info[:2] != (3, 12),
    reason="the release's json is Python 3.12's (3.13 words some refusals differently)",
)
def test_json_is_read_and_written_as_python_s() -> None:
    rng = random.Random(0x15)
    texts = [_json_text(rng) for _ in range(3000)]
    texts += [_broken(rng, text) for text in texts[:3000]]
    texts += ["", " ", "﻿[]", "1" * 4300, "-" + "1" * 4301, '{"a":1,}', "[1,]", "01"]
    # every theme that comes with the app, as it is on disk
    themes = sorted(
        (Path(__file__).resolve().parent.parent / "src/libre_panel/themes").glob("*/theme.json")
    )
    texts += [path.read_text(encoding="utf-8") for path in themes]
    [ours] = run([{"op": "json", "texts": texts}])
    halves = 0
    for text, mine in zip(texts, ours["results"], strict=True):
        want = _python_json(text)
        if "half" in want:
            halves += 1
            assert mine["kind"] == "value" and mine["error"].startswith("half of a surrogate pair")
            continue
        assert mine == want, repr(text)
    assert len(themes) > 20 and 0 < halves < len(texts) // 10


def test_floats_are_written_as_python_s_repr() -> None:
    rng = random.Random(0xF1)
    specials = [0.0, -0.0, 1e16, 1e15, 9999999999999998.0, 1e-4, 1e-5, 0.1, 5e-324, 2e16 + 8]
    specials += [1.7976931348623157e308, 123.456, float("inf"), float("-inf"), float("nan")]
    values = specials + [struct.unpack("<d", rng.randbytes(8))[0] for _ in range(20000)]
    values += [rng.uniform(-1e6, 1e6) for _ in range(5000)] + [
        float(rng.randrange(10**18)) for _ in range(2000)
    ]
    bits = [struct.pack("<d", v)[::-1].hex() for v in values]
    [ours] = run([{"op": "json", "texts": [], "floats": bits}])
    assert ours["floats"] == [repr(v) for v in values]


def test_nesting_as_deep_as_python_reads_it() -> None:
    [ours] = run([{"op": "json", "texts": []}])
    deepest = ours["max_depth"]

    def reads(depth: int) -> bool:
        try:
            json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return False
        return True

    # Python's own limit, less what the test's stack already holds: as deep as here, or a little
    # less; and never deeper
    assert not reads(deepest + 1) and reads(deepest - 200)
    texts = ["[" * n + "]" * n for n in (deepest - 200, deepest, deepest + 1)]
    texts.append('{"a":' * (deepest + 1) + "1" + "}" * (deepest + 1))
    [ours] = run([{"op": "json", "texts": texts}])
    results = ours["results"]
    assert "repr" in results[0] and "repr" in results[1]
    too_deep = "maximum recursion depth exceeded while decoding a JSON array from a unicode string"
    assert results[2] == {"error": too_deep, "kind": "recursion"}
    assert results[3]["error"].endswith("decoding a JSON object from a unicode string")


def test_floats_are_formatted_as_python_s_g() -> None:
    rng = random.Random(0x6E)
    values = [0.0, -0.0, 5.0, 12.3, 2.1, 0.125, 2.5, 0.5, 999999.5, 1e-5, 1e-4, 123456789.0]
    values += [float("inf"), float("-inf"), float("nan")]
    values += [struct.unpack("<d", rng.randbytes(8))[0] for _ in range(5000)]
    values += [rng.randrange(-2000, 2000) / rng.choice([2, 4, 8, 1024, 10]) for _ in range(5000)]
    values += [round(rng.uniform(0, 30), 1) for _ in range(2000)]
    general = [(v, rng.choice([1, 2, 3, 6, 6, 10, 17])) for v in values]
    pairs = [[struct.pack("<d", v)[::-1].hex(), p] for v, p in general]
    [ours] = run([{"op": "json", "texts": [], "general": pairs}])
    assert ours["general"] == [format(v, f".{p}g") for v, p in general]


def test_hypot_is_python_s_own() -> None:
    rng = random.Random(0x4799)
    specials = [
        0.0,
        -0.0,
        1.0,
        3.0,
        4.0,
        5e-324,
        2.2250738585072014e-308,
        1e308,
        1.7976931348623157e308,
    ]
    specials += [float("inf"), float("-inf"), float("nan"), 0.1, 1e-300]
    pairs = [(a, b) for a in specials for b in specials]
    for _ in range(20000):
        kind = rng.randrange(3)
        if kind == 0:
            pairs.append((rng.uniform(-1, 1), rng.uniform(-1, 1)))
        elif kind == 1:
            draw = lambda: struct.unpack("<d", rng.randbytes(8))[0]  # noqa: E731
            pairs.append((draw(), draw()))
        else:
            scale = 10.0 ** rng.randrange(-320, 300)
            pairs.append((rng.uniform(-1, 1) * scale, rng.uniform(-1, 1) * scale))
    bits = [[struct.pack("<d", v)[::-1].hex() for v in pair] for pair in pairs]
    [ours] = run([{"op": "math", "hypot": bits}])
    want = [struct.pack("<d", math.hypot(a, b))[::-1].hex() for a, b in pairs]
    assert ours["hypot"] == want


def _number(rng: random.Random) -> Any:
    """A number as a JSON file may hold one: bools, small and huge ints, floats of every kind."""
    sign = rng.choice([1, -1])
    pick = rng.randrange(11)
    if pick == 0:
        return rng.choice([True, False])
    if pick == 1:
        return rng.randrange(-5, 6)
    if pick == 2:
        return rng.randrange(-(10**6), 10**6)
    if pick == 3:
        return sign * rng.randrange(2**52, 2**70)
    if pick == 4:
        return sign * rng.randrange(1, 10 ** rng.randrange(20, 400))
    if pick == 5:
        specials = [0.0, -0.0, 0.5, 2.5, 1e308, 5e-324, math.inf, -math.inf, math.nan, 4.0, 2.0**53]
        return rng.choice(specials)
    if pick == 6:
        return struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
    if pick == 7:
        return rng.uniform(-1000, 1000)
    if pick == 8:
        # about the largest float: past it ints no longer turn into one
        return rng.choice(
            [sign * 2 ** rng.randrange(1000, 1100), 2**1024 - 2**970, 2**1024 - 2**971]
        )
    if pick == 9:
        return float(rng.randrange(-100, 100)) * 2.0 ** rng.randrange(-1100, 1000)
    return rng.choice([2**53 + 1, 2**63, -(2**63), 3 * 2**60])


def _python_number_op(op: str, a: Any, b: Any) -> dict[str, Any]:
    calls = {
        "+": lambda: a + b,
        "-": lambda: a - b,
        "*": lambda: a * b,
        "/": lambda: a / b,
        "//": lambda: a // b,
        "%": lambda: a % b,
        "<": lambda: a < b,
        ">": lambda: a > b,
        "max": lambda: max(a, b),
        "min": lambda: min(a, b),
        "round": lambda: round(a),
        "log": lambda: math.log(a),
    }
    try:
        return {"value": json.dumps(calls[op]())}
    except Exception as error:  # noqa: BLE001 (every kind Python raises is compared)
        return {"error": str(error), "kind": type(error).__name__}


@pytest.mark.skipif(
    sys.version_info[:2] != (3, 12),
    reason="Python 3.12's messages (3.13 words a float modulo by zero otherwise)",
)
def test_arithmetic_on_numbers_as_python_s() -> None:
    rng = random.Random(0x4E0)
    others = [None, "a", "", [1], [], {"a": 1}, {}, "8"]

    def value() -> Any:
        return _number(rng) if rng.random() < 0.8 else rng.choice(others)

    cases: list[tuple[str, Any, Any]] = []
    for _ in range(30000):
        op = rng.choice(["+", "-", "*", "/", "//", "%", "<", ">", "max", "min", "round", "log"])
        if op == "log":  # (of a float)
            a = abs(_number(rng)) if rng.random() < 0.7 else _number(rng)
            cases.append((op, float(a) if abs(a) < 2**1000 else 1.5, 0))
        else:
            cases.append((op, value(), value()))
    [ours] = run([{"op": "py_num", "cases": [json.dumps(c) for c in cases]}])
    digits = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)  # (products of ints of 400 digits, written whole)
    try:
        for (op, a, b), got in zip(cases, ours["answers"], strict=True):
            if got.get("kind") == "Unsupported":
                # what Python counts with sequences (joins, repeats, orders, formats), never a
                # number with a number
                assert isinstance(a, (str, list)) or isinstance(b, (str, list)), (op, a, b)
                continue
            assert got == _python_number_op(op, a, b), (op, a, b)
    finally:
        sys.set_int_max_str_digits(digits)


@pytest.mark.skipif(
    unicodedata.unidata_version != RELEASE_UNICODE,
    reason=f"needs Python with Unicode {RELEASE_UNICODE} (the release's)",
)
def test_names_meet_without_regard_to_case_as_python_s_re() -> None:
    import _sre
    import fnmatch
    import glob
    import re

    rng = random.Random(0x16C)
    # characters with case, with more than one lowercase of one uppercase, without case
    pool = "aAbBkKsSiIeEzZ" + "ſKıİßẞΣσςΙιͅθϑϴβϐвᲀǅǄǆ" + "0123456789-x[]*?._ äÄöÖ"
    pairs = []
    for _ in range(6000):
        stem = "".join(rng.choice(pool) for _ in range(rng.randrange(1, 6)))
        if rng.random() < 0.5:  # the stem's characters, some in another case
            name = "".join(
                rng.choice([c, c.swapcase()[:1], c.lower()[:1], c.upper()[:1], rng.choice(pool)])
                for c in stem
            )
        else:
            name = "".join(rng.choice(pool) for _ in range(rng.randrange(0, 6)))
        pairs.append([stem, name + rng.choice(["", "-vertical", "-1920x720"])])
    [ours] = run([{"op": "theme_adapt", "fonts_dir": "", "builtin": "", "ignorecase": pairs}])
    for (stem, name), got in zip(pairs, ours["ignorecase"], strict=True):
        # pathlib's glob on Windows: fnmatch's pattern of the escaped stem, without case
        pattern = re.compile(fnmatch.translate(glob.escape(stem) + "*"), re.IGNORECASE)
        assert got == (len(stem) if pattern.match(name) else None), (stem, name)
    # and the tables it goes by, over every code point
    [table] = run([{"op": "py_num", "cases": []}])
    lines = "".join(
        f"{cp:x} {_sre.unicode_tolower(cp):x} {int(_sre.unicode_iscased(cp))}\n"
        for cp in range(0x110000)
    )
    assert table["re_digest"] == hashlib.sha256(lines.encode()).hexdigest()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12's sum() (Neumaier's)")
def test_floats_summed_as_python_s_sum() -> None:
    rng = random.Random(0x5E)
    lists: list[list[float]] = [[], [-0.0], [0.55] * 7, [0.1] * 10, [1e308, 1e308, -1e308]]
    lists += [[1e16, 1.0, -1e16], [math.inf, -math.inf], [math.nan, 1.0], [5e-324] * 3]
    for _ in range(3000):
        pick = rng.randrange(3)
        n = rng.randrange(1, 12)
        if pick == 0:
            lists.append([rng.uniform(-100, 100) for _ in range(n)])
        elif pick == 1:
            lists.append([float(rng.choice([0.55, 1.0, 0.1, 0.3, 2.65])) for _ in range(n)])
        else:
            lists.append([struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
                          for _ in range(n)])  # fmt: skip
    bits = [[struct.pack(">d", x).hex() for x in items] for items in lists]
    [ours] = run([{"op": "py_num", "cases": [], "sums": bits}])
    want = [struct.pack(">d", float(sum(items))).hex() for items in lists]
    # (NaN's bits as the sums make them: compared as NaN)
    for got, expected, items in zip(ours["sums"], want, lists, strict=True):
        if math.isnan(sum(items)):
            assert math.isnan(struct.unpack(">d", bytes.fromhex(got))[0]), items
        else:
            assert got == expected, items


def test_lines_split_as_python_s_splitlines() -> None:
    rng = random.Random(0x11E)
    ends = ["\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]
    texts = ["", "\n", "a", "a\n", "\r\n\r\n", "a\r\rb", "\n\na"]
    for _ in range(2000):
        texts.append(
            "".join(rng.choice(["a", "bc", " ", "é", *ends]) for _ in range(rng.randrange(8)))
        )
    [ours] = run([{"op": "py_num", "cases": [], "splitlines": texts}])
    assert ours["splitlines"] == [text.splitlines() for text in texts]


@pytest.mark.skipif(
    unicodedata.unidata_version != RELEASE_UNICODE,
    reason=f"needs Python with Unicode {RELEASE_UNICODE} (the release's)",
)
def test_wide_characters_as_python_s_east_asian_width() -> None:
    [ours] = run([{"op": "py_num", "cases": []}])
    wide = "".join(
        "1" if unicodedata.east_asian_width(chr(cp)) in "WF" else "0" for cp in range(0x110000)
    )
    assert ours["wide_digest"] == hashlib.sha256(wide.encode()).hexdigest()


def test_time_zones_found_as_zoneinfo_finds_them() -> None:
    # (in the search path, else in the tzdata package, on Windows all of them; a key checked
    # by the system's own paths' rules)
    from libre_panel import timezones

    keys = ["", "UTC", "Asia/Tokyo", "Europe/Berlin", "America/Argentina/Buenos_Aires", "Nope/Zone"]
    keys += ["/etc/localtime", "../etc/passwd", "Europe/", "./UTC", "..", ".", "a//b", "zone.tab"]
    keys += ["tzdata.zi", "posixrules", "Factory", "Etc/GMT+5", "US/Pacific", "europe/berlin"]
    keys += ["Asia", "Europe/Berlin\0", "Antarctica/Troll", "iso3166.tab", "right/UTC"]
    keys += ["Europe\\Berlin", "C:UTC", "..\\zoneinfo\\UTC", "America/Argentina", "Etc/UTC"]
    keys += ["Europe/Berlin/", "America.Argentina/Cordoba", "__init__.py", "America/Indiana/Knox"]
    [ours] = run([{"op": "py_num", "cases": [], "zones": keys}])
    assert ours["zones"] == [timezones.zone.__wrapped__(k) is not None for k in keys]


def _float_bits(text: str) -> str | None:
    try:
        return struct.pack(">d", float(text)).hex()
    except ValueError:
        return None


def test_floats_read_as_python_reads_them() -> None:
    texts = ["", " ", "5", "-5", "+5", "5.", ".5", ".", "-.", "1e5", "1E5", "1e", "1e+", "1e-5",
             "1.e5", ".e5", "e5", "1_0", "1__0", "_10", "10_", "1_.0", "1._0", "1.0_1", "1e1_0",
             "1_e10", "0x10", "inf", "-inf", "+Inf", "INFINITY", "infinity", "infin", "nan",
             "-nan", "NaN", "+nAn", "nana", "  7  ", "\t7\n", "7\x0b", "7\x0c", "\r7", "7\x1c",
             "\x1f7", "7\x00", "\x00", "1" * 400, "0." + "0" * 400 + "1", "1e400", "-1e400",
             "1e-400", "2.2250738585072011e-308", "4.9406564584124654e-324", "9007199254740993",
             "0.1", "00012", "-0", "-0.0", "+.5e-3", "1e999999999999", "1e-999999999999",
             "12abc", "abc", "1,5", "١٢", "۱۲.۵", "1 2", " 5　", " " "6",
             "5\u0085", "7 ", "8\x85", "٥_٥", "5é", "١e٢", "𝟓", "Ⅻ", "½",
             "\U0001d7ce\U0001d7cf", "\x7f", "5\x7f", "1e٢"]  # fmt: skip
    rng = random.Random(0xF10A7)
    parts = ["", "-", "+", "1", "23", "0", ".", "e", "E", "_", " ", "5", "9", "inf", "nan", "\t"]
    for _ in range(3000):
        texts.append("".join(rng.choice(parts) for _ in range(rng.randrange(1, 7))))
    for _ in range(500):
        x = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        texts.append(repr(x) if rng.random() < 0.7 else f"{x:.{rng.randrange(1, 25)}e}")
    if unicodedata.unidata_version != RELEASE_UNICODE:  # (digits and spaces of every script)
        texts = [t for t in texts if t.isascii()]
    [ours] = run([{"op": "py_float", "texts": texts}])
    assert ours["floats"] == [_float_bits(t) for t in texts]


class _Echo(string.Formatter):
    """A Formatter whose fields are their own names: what str.format reads of a format string
    (its fields, conversions, nested specs and numbering), not what a field holds."""

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> tuple[str, str]:
        return field_name, field_name


_FORMAT_PIECES = ["{", "}", "{{", "}}", "!", ":", "[", "]", "0", "1", "²", "١", "①", "a", "x",
                  "s", "r", "\0", "<", ">5", "^7", ".2", "=", " ", "é", "{}", "{0}", "{1}",
                  "{a}", "{²}", "{١}", "!r", "!s", "!a", "!\0", "!x", ":{}", "{a:{b}}"]  # fmt: skip


def _echo_outcome(text: str) -> dict[str, Any]:
    kinds = {ValueError: "Value", TypeError: "Type", OverflowError: "Overflow"}
    try:
        return {"text": _Echo().format(text)}
    except (ValueError, TypeError, OverflowError) as error:
        return {"error": kinds[type(error)]}


def test_format_strings_read_as_python_s_str_format() -> None:
    rng = random.Random(0x5F0)
    texts = ["", "a", "{a:{b:{c}}}", "{a:{b}}", "{:{:{}}}", "{}{}", "{0}{}", "{}{0}", "{²}{}",
             "{}{²}", "{①}{}", "{a!\0}", "{a!\0:>5}", "{a[}]}", "{a[b}", "{a!r:^9}", "{!r}",
             "{a!}", "{a!}}", "{a!r", "{a:", "{a:{", "}", "{", "{{}}", "x{a!a}y", "{é!a}",
             "{a:{b!r}}", "{a:{}}{}", "{0:{1}}{}", "{0:{}}", "{x:{<:{}}}", "{x:{<:{<}}}",
             "{x:{<}}"]  # fmt: skip
    for _ in range(6000):
        texts.append("".join(rng.choice(_FORMAT_PIECES) for _ in range(rng.randrange(9))))
    [ours] = run([{"op": "py_vformat", "texts": texts}])
    assert ours["texts"] == [_echo_outcome(t) for t in texts]
    # (texts, not only refusals alike)
    assert sum("text" in o for o in ours["texts"]) > 1500


STRFTIME_TOKENS = [f"%{flag}{code}" for flag in ("", "-", "#", "_", "0", "^")
                   for code in string.ascii_letters + "%"]  # fmt: skip


def _moments(rng: random.Random, small_years: bool = True) -> list[datetime]:
    """Edge days (the years from 1 to 9999, the turns of the ISO weeks, midnight and noon) and
    random ones."""
    years = (1, 9, 99, 999, 1000, 1899, 1900, 1999, 2000, 2026, 9999)
    moments = [datetime(year, month, day, hour, 5, 9, 250)
               for year in (years if small_years else years[4:])
               for month, day in ((1, 1), (1, 3), (1, 4), (2, 28), (12, 28), (12, 29), (12, 31))
               for hour in (0, 12, 13, 23)]  # fmt: skip
    for _ in range(200):
        year = rng.choice([rng.randrange(1 if small_years else 1000, 10000),
                           rng.randrange(1990, 2060)])  # fmt: skip
        month = rng.randrange(1, 13)
        day = rng.randrange(1, calendar.monthrange(year, month)[1] + 1)
        moments.append(datetime(year, month, day, rng.randrange(24), rng.randrange(60),
                                rng.randrange(60), rng.randrange(10**6)))  # fmt: skip
    return moments


def _at(moment: datetime) -> list[int]:
    return [moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second,
            moment.microsecond]  # fmt: skip


def _strftime_unported(token: str, at: datetime) -> bool:
    """What lp_py::strftime does not write yet: the years before 1000 (Python's own answer for
    them differs between its releases), %s on Linux (the computer's zone), %#z and %#Z on
    Windows (the same)."""
    flag, code = (token[1], token[2]) if len(token) == 3 else ("", token[1])
    small, iso_small = at.year < 1000, at.isocalendar()[0] < 1000
    if sys.platform == "win32":
        if flag not in ("", "#"):
            return False  # (Python's ValueError)
        if code in "zZ":
            return flag == "#"
        if code == "G":
            return iso_small
        return small and (code in "CFYc" or (code == "x" and flag == "#"))
    if code == "s":
        return True
    if code == "G":
        return iso_small
    return small and code in "CFYc"


def test_dates_written_as_python_s_strftime_writes_them() -> None:
    # (each code as datetime.strftime writes it: %f, %z and %Z its own, the rest the C
    # library's, glibc's on Linux, the Universal CRT's on Windows, where Python refuses what
    # the CRT does not know; what Rust does not write yet, it says, and nothing else)
    rng = random.Random(0x57F)
    cases, expected = [], []
    for at in _moments(rng):
        for token in STRFTIME_TOKENS:
            try:
                want: Any = at.strftime(token)
            except ValueError as error:
                want = {"error": str(error)}
            if _strftime_unported(token, at):
                want = "unported"
            cases.append({"token": token, "at": _at(at)})
            expected.append(want)
    [ours] = run([{"op": "strftime", "cases": cases}])
    wrong = [(case, got, want) for case, got, want in zip(cases, ours, expected, strict=True)
             if (not (isinstance(got, dict) and "unported" in got) if want == "unported"
                 else got != want)]  # fmt: skip
    assert not wrong, f"{len(wrong)} differ: {wrong[:40]}"
    # (Windows refuses every flag but #: a third of the codes written there)
    written = sum(isinstance(w, str) and w != "unported" for w in expected)
    assert written > (30_000 if sys.platform == "win32" else 50_000)


_DATE_PIECES = ["%d", "%-d", "%B", "%b", "%a", "%A", "%H:%M", "%Y", "%y", "%e", "%j", "%p", "%I",
                " ", ". ", ", ", "Uhr", "年", "日", "%", "%%", "%:", "%d %B", "%d. %B", "%-d. %b",
                "%B %d", "%b %-d", "%d %b", "%-d %B", "%A, %d %B %Y", "%a %d %b", "%x", "%X",
                "%c", "%#d", "%-m/%-d", "%d  %B", " %d %B", "%B%d", "%d %B %d %b"]  # fmt: skip


def test_dates_on_the_panel_as_format_date_writes_them() -> None:
    # (strftime's codes one at a time, with the days' and the months' names of the language, a
    # day's number before a month's name joined as the language joins them, the month and the
    # day as numbers in Chinese and Japanese)
    rng = random.Random(0xDA7E)
    tokens = [t for t in STRFTIME_TOKENS if t[-1] != "s" and t not in ("%#z", "%#Z")]
    moments = _moments(rng, small_years=False)
    cases, expected = [], []
    try:
        for n in range(6000):
            pieces = [rng.choice(_DATE_PIECES if rng.random() < 0.7 else tokens)
                      for _ in range(rng.randrange(1, 6))]  # fmt: skip
            fmt, at, language = "".join(pieces), rng.choice(moments), list(i18n.LANGUAGES)[n % 10]
            i18n.set_language(language)
            try:
                want: Any = i18n.format_date(at, fmt)
            except ValueError as error:
                want = {"error": str(error)}
            cases.append({"fmt": fmt, "at": _at(at), "language": language})
            expected.append(want)
    finally:
        i18n.set_language("en")
    [ours] = run([{"op": "format_date", "locale": str(i18n.LOCALE_DIR), "cases": cases}])
    wrong = [(case, got, want) for case, got, want in zip(cases, ours, expected, strict=True)
             if got != want]  # fmt: skip
    assert not wrong, f"{len(wrong)} differ: {wrong[:40]}"
    assert sum(isinstance(w, str) for w in expected) > 2000


def test_fnmatch_as_python_matches() -> None:
    # (fnmatchcase as translate() makes a pattern a regular expression: sets and their ranges, a
    # range running backwards left out, a set's first ] or !, hyphens at its ends, escapes,
    # stars; each pattern against every name. And filter, which on Windows takes the names and
    # the pattern as normcase has them: slashes turned, letters small as the system makes them)
    rng = random.Random(0xF4A7)
    patterns = ["".join(rng.choice("ab-]![\\^*?&~|z.0") for _ in range(rng.randrange(0, 9)))
                for _ in range(400)]  # fmt: skip
    patterns += [rng.choice(["", "a", "*", "?"]) + "["
                 + "".join(rng.choice("!]-abz0^\\[") for _ in range(rng.randrange(0, 6))) + "]"
                 + rng.choice(["", "*", "a", "]", "-]"]) for _ in range(200)]  # fmt: skip
    names = sorted({"".join(rng.choice("ab-]![\\^&~|z.0A") for _ in range(rng.randrange(0, 5)))
                    for _ in range(300)})  # fmt: skip
    pairs = [(pattern, name) for pattern in patterns for name in names]
    # (sets made of ranges both ways, hyphens alone and doubled, a first ! or ], escapes: each
    # against every printable letter)
    parts = ["a-z", "z-a", "0-9", "-", "a", "b", "z", "0", "\\", "^", "[", "a-", "-z", "!", "]",
             "--", "b-a", "a-b-c", "-a-", "c-b", "c-b-a"]  # fmt: skip
    for _ in range(2000):
        body = ("!" if rng.random() < 0.4 else "") + ("]" if rng.random() < 0.15 else "")
        body += "".join(rng.choice(parts) for _ in range(rng.randrange(1, 5)))
        pairs += [(f"[{body}]", chr(c)) for c in range(32, 127)]
    cases, wanted = [], []
    for pattern, name in pairs:
        cases.append([name, pattern])
        try:
            wanted.append(fnmatch.fnmatchcase(name, pattern))
        except re.error:
            wanted.append("error")
    files = ["temp.cpu", "temp.gpu", "fan.1", "fan.10", "fan.2", "Fan.3", "disk.a-b", "x[1]",
             "C:/x", "ÄÖ.ü", "İ", "ß", "ΣΑΣ"]  # fmt: skip
    globs = ["temp.*", "fan.?", "fan.*", "*[0-9]", "F*", "x[[]*", "*", "[!t]*", "disk.a[-]b",
             "c:\\*", "äö.*", "i̇", "SS", "σας"]  # fmt: skip
    [got] = run([{"op": "fnmatch", "cases": cases, "names": files, "patterns": globs}])
    assert ["error" if isinstance(g, dict) else g for g in got["matched"]] == wanted
    assert got["filtered"] == [fnmatch.filter(files, p) for p in globs]
    assert sum(w is True for w in wanted) > 20000

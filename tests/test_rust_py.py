"""rust/py against Python (docs/RUST.md): what Python does that the app's files and messages
depend on, as the Rust app does it: str's repr, its white space and digits (Python's regular
expressions and int() go by them), lower() and upper(), the division of ints, over every code
point where the Unicode database is the release's (Python 3.12: Unicode 15.0)."""

from __future__ import annotations

import hashlib
import random
import unicodedata

import pytest
from rust_check import run

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


def test_lower_and_upper_as_python_s() -> None:
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
    # lower() and upper() of each character, and what the final sigma makes of it: one that
    # is skipped (case-ignorable), a letter of a case, or neither
    sigma = {("ς", "σ"): "I", ("σ", "σ"): "C", ("ς", "ς"): "N"}
    lines, cases = [], []
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
    [ours] = run([{"op": "py", "texts": []}])
    assert ours["sha256"] == hashlib.sha256("".join(lines).encode()).hexdigest()
    assert ours["case_sha256"] == hashlib.sha256("".join(cases).encode()).hexdigest()

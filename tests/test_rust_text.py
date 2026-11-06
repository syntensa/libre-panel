"""rust/text against Pillow (docs/RUST.md, stage R2): fonts opened, measured and drawn as
Pillow 13.0's FreeTypeFont does it, with its basic layout and with libraqm's (shaped by
HarfBuzz, ordered by FriBiDi), and ImageDraw's text, textbbox and textlength on top, byte for
byte, with the FreeType and the HarfBuzz Pillow's wheel brings."""

from __future__ import annotations

import base64
import hashlib
import random
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont, features
from rust_check import pillow_libfreetype, pillow_libharfbuzz, run

ROOT = Path(__file__).resolve().parent.parent
BASIC = ImageFont.Layout.BASIC
LAYOUT = {"basic": BASIC, "raqm": ImageFont.Layout.RAQM}
# libraqm's layout where Pillow has it here (FriBiDi found) and lp-check its HarfBuzz
LAYOUTS = ["basic"] + (["raqm"] if features.check("raqm") and pillow_libharfbuzz() else [])
BUNDLED = sorted((ROOT / "src" / "libre_panel" / "fonts").glob("*.ttf"))
# fonts of other kinds where the system has them: OpenType (CFF), a collection's second face,
# Type 1, CJK, a font of bitmaps in colour (one size only)
SYSTEM = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/opentype/inter/Inter-Bold.otf", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 1),
    ("/usr/share/fonts/X11/Type1/c0648bt_.pfb", 0),
    ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", 0),
    ("/usr/share/fonts/truetype/freefont/FreeMono.ttf", 0),
]
EMOJI = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")
WORDS = [
    "Hello",
    "AVATAR Ta Wo",
    "12:34:56",
    "°C 72%",
    "Grüße äöü ß",
    "",
    " ",
    "x",
    "yjgpq",
    "TYPOGRAPHY To",
    "1.5 GHz",
    "—–‘’“”…",
    "Ťž Łódź",
    "中文字体 测试",
    "日本語のテキスト",
    "  lead",
    "trail  ",
]
LINES = ["line one\nline two", "a\nbb\nccc ddd eee", "Wo rd s\nj u s t i f y\nend", "x\n\ny"]
# what libraqm shapes and orders: joined and right-to-left scripts, both directions in one
# text, marks, paired brackets, the scripts of India and Thailand, ligatures, controls
SHAPED = [
    "مرحبا بالعالم",
    "שלום עולם",
    "abc אבג 123 def",
    "(مرحبا) [test] {x}",
    "«שלום» (abc)",
    "नमस्ते दुनिया",
    "สวัสดีครับ",
    "office ffi fl",
    "e\u0301 a\u0308 n\u0303",
    "1 + 2 = ٣",
    "ABC\u202edef\u202c",
    "x\u200dy",
    "١٢٣ 456",
    "ﻻ لا",
    "(((((",
    ")))a((",
    "a\u00a0b",
]
SHAPED_LINES = ["مرحبا\nبالعالم", "abc אבג\n123", "नमस्ते\nदुनिया"]
SHAPING_FONTS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/truetype/freefont/FreeSerif.ttf", 0),
    ("/usr/share/fonts/opentype/tlwg/Loma.otf", 0),
    ("/usr/share/fonts/opentype/unifont/unifont.otf", 0),
]
ANCHORS = [
    None,
    "la",
    "lt",
    "lm",
    "ls",
    "lb",
    "ld",
    "ma",
    "mm",
    "ms",
    "md",
    "ra",
    "rm",
    "rs",
    "rd",
    "xx",
    "l",
]


@pytest.fixture(autouse=True)
def _needs_freetype() -> None:
    if pillow_libfreetype() is None:
        pytest.skip("Pillow's FreeType is built into it here: none for lp-check to load")


def _fonts() -> list[tuple[str, int]]:
    found = [(str(path), 0) for path in BUNDLED]
    return found + [(path, index) for path, index in SYSTEM if Path(path).is_file()]


def _digest(im: Image.Image) -> dict[str, Any]:
    return {
        "mode": im.mode,
        "size": list(im.size),
        "sha256": hashlib.sha256(im.tobytes()).hexdigest(),
    }


def _image(im: Image.Image) -> dict[str, Any]:
    return {
        "mode": im.mode,
        "size": list(im.size),
        "data": base64.b64encode(im.tobytes()).decode("ascii"),
    }


def _ink(colour: list[int]) -> Any:
    return colour[0] if len(colour) == 1 else tuple(colour)


def _pillow(font: ImageFont.FreeTypeFont, c: dict[str, Any]) -> dict[str, Any]:
    """What Pillow answers to one call (its error, where it raises one)."""
    try:
        fn, text, mode = c["fn"], c["text"], c.get("mode", "")
        stroke = c.get("stroke_width", 0)
        how = {key: c.get(key) for key in ("direction", "features", "language")}
        if fn == "getlength":
            return {"length": font.getlength(text, mode, *how.values())}
        if fn == "getbbox":
            return {"box": list(font.getbbox(text, mode, *how.values(), stroke, c.get("anchor")))}
        if fn == "getmask2":
            im, offset = font.getmask2(
                text,
                mode,
                *how.values(),
                stroke,
                c.get("anchor"),
                c.get("ink", 0),
                tuple(c.get("start", (0, 0))),
                stroke_filled=c.get("stroke_filled", False),
            )
            mask = Image.new(im.mode, (1, 1))._new(im)  # (the core in an Image)
            return {"image": _digest(mask), "offset": list(offset)}
        options = {
            "anchor": c.get("anchor"),
            "spacing": c.get("spacing", 4),
            "align": c.get("align", "left"),
            "embedded_color": c.get("embedded_color", False),
            **how,
        }
        if fn == "draw":
            v = c["image"]
            im = Image.frombytes(v["mode"], tuple(v["size"]), base64.b64decode(v["data"]))
            stroke_fill = c.get("stroke_fill")
            ImageDraw.Draw(im).text(
                tuple(c["xy"]),
                text,
                fill=_ink(c["fill"]),
                font=font,
                stroke_width=stroke,
                stroke_fill=None if stroke_fill is None else _ink(stroke_fill),
                **options,
            )
            return {"image": _digest(im)}
        draw = ImageDraw.Draw(Image.new(c.get("image_mode", "RGBA"), (1, 1)))
        if fn == "textbbox":
            return {
                "box": list(
                    draw.textbbox(tuple(c["xy"]), text, font=font, stroke_width=stroke, **options)
                )
            }
        options.pop("anchor"), options.pop("spacing"), options.pop("align")
        return {"length": draw.textlength(text, font=font, **options)}
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        return {"error": str(error)}


def _compare(
    path: str, size: float, calls: list[dict[str, Any]], index: int = 0, layout: str = "basic"
) -> None:
    font = ImageFont.truetype(path, size, index, layout_engine=LAYOUT[layout])
    [ours] = run(
        [
            {
                "op": "font",
                "digest": True,
                "calls": calls,
                "font": {"path": path, "size": size, "index": index, "layout": layout},
            }
        ]
    )
    where = f"{Path(path).name}[{index}] at {size}, {layout}"
    assert ours.get("layout", "").lower() == layout, where
    assert ours.get("name") == list(font.getname()), where
    assert ours["metrics"] == list(font.getmetrics()), where
    assert (ours["height"], ours["ppem"], ours["glyphs"]) == (
        font.font.height,
        [font.font.x_ppem, font.font.y_ppem],
        font.font.glyphs,
    ), where
    for c, mine in zip(calls, ours["results"], strict=True):
        theirs = _pillow(font, c)
        if "error" in theirs:  # refused by both, for the same reason
            assert mine.get("error") == theirs["error"], (where, c)
        else:
            assert mine == theirs, (where, {k: v for k, v in c.items() if k != "image"})


@pytest.mark.parametrize("layout", LAYOUTS)
def test_fonts_are_measured_and_drawn_as_pillow_does_it(layout: str) -> None:
    rng = random.Random(0x7E47)
    for path, index in _fonts():
        for size in rng.sample([8, 9.5, 11, 12, 13.3, 16, 18, 22.7, 24, 32, 40, 48, 64, 97], 3):
            calls = []
            for _ in range(30):
                c: dict[str, Any] = {
                    "fn": rng.choice(["getlength", "getbbox", "getmask2"]),
                    "text": rng.choice(WORDS),
                    "mode": rng.choice(["", "L", "1", "RGBA"]),
                }
                if c["fn"] != "getlength":
                    c["anchor"] = rng.choice(ANCHORS)
                    c["stroke_width"] = rng.choice([0, 0, 0, 1, 2, 0.5, 1.5])
                if c["fn"] == "getmask2":
                    c["stroke_filled"] = rng.random() < 0.5
                    c["start"] = [rng.choice([0, 0.25, 0.5, 0.75, -0.5]), rng.choice([0, 0.3])]
                calls.append(c)
            _compare(path, size, calls, index, layout)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_text_is_drawn_and_its_box_and_length_are_as_imagedraw_s(layout: str) -> None:
    rng = random.Random(0xD7A3)
    for path, index in _fonts():
        for size in rng.sample([9, 12, 13.3, 16, 22.7, 24, 32, 48], 2):
            calls = []
            for _ in range(24):
                c: dict[str, Any] = {
                    "fn": rng.choice(["draw", "draw", "textbbox", "textlength"]),
                    "text": rng.choice(WORDS + LINES),
                    "anchor": rng.choice(ANCHORS),
                    "align": rng.choice(["left", "center", "right", "justify", "middle"]),
                    "spacing": rng.choice([4, 0, 2.5, 10]),
                    "xy": [rng.choice([0, 10, 33.5, 60.25, -3.7]), rng.choice([0, 12, 30.5])],
                }
                if c["fn"] == "draw":
                    mode = rng.choice(["L", "RGB", "RGBA"])
                    w, h = rng.choice([(120, 60), (200, 90), (64, 64)])
                    noisy = rng.random() < 0.5
                    base = (
                        Image.frombytes(mode, (w, h), rng.randbytes(w * h * len(mode)))
                        if noisy
                        else Image.new(mode, (w, h))
                    )
                    c["image"] = _image(base)
                    c["fill"] = [rng.randrange(256) for _ in mode]
                    if rng.random() < 0.3:
                        c["stroke_width"] = rng.choice([1, 2, 0.5, 1.5])
                        if rng.random() < 0.5:
                            c["stroke_fill"] = [rng.randrange(256) for _ in mode]
                else:
                    c["image_mode"] = rng.choice(["L", "RGBA"])
                    if c["fn"] == "textbbox" and rng.random() < 0.3:
                        c["stroke_width"] = rng.choice([1, 2.5])
                calls.append(c)
            _compare(path, size, calls, index, layout)


@pytest.mark.skipif("raqm" not in LAYOUTS, reason="needs Pillow's libraqm (FriBiDi)")
def test_scripts_are_shaped_and_ordered_as_libraqm_does_it() -> None:
    rng = random.Random(0x5A9E)
    fonts = [(path, index) for path, index in SHAPING_FONTS if Path(path).is_file()]
    fonts += [(str(path), 0) for path in BUNDLED[:3]]
    for path, index in fonts:
        for size in rng.sample([9, 12, 13.3, 16, 22.7, 32, 48], 2):
            calls = []
            for _ in range(40):
                c: dict[str, Any] = {
                    "fn": rng.choice(["getlength", "getbbox", "getmask2", "draw", "textbbox"]),
                    "text": rng.choice(SHAPED + WORDS[:6]),
                    "mode": rng.choice(["", "L", "1"]),
                }
                pick = rng.random()
                if pick < 0.15:
                    c["direction"] = rng.choice(["rtl", "ltr", "ttb"])
                elif pick < 0.25:
                    c["features"] = rng.choice(
                        [["-kern"], ["+smcp"], ["liga=0"], ["-liga", "+dlig"]]
                    )
                elif pick < 0.32:
                    c["language"] = rng.choice(["tr", "ar", "sr", "ja", "zh-Hans"])
                if c["fn"] in ("getbbox", "getmask2", "draw", "textbbox"):
                    c["anchor"] = rng.choice([None, "la", "ls", "mm", "rd", "lt", "mt", "rb"])
                    c["stroke_width"] = rng.choice([0, 0, 1, 1.5])
                if c["fn"] == "getmask2":
                    c["stroke_filled"] = rng.random() < 0.5
                    c["start"] = [rng.choice([0, 0.5, -0.25]), rng.choice([0, 0.3])]
                if c["fn"] in ("draw", "textbbox"):
                    c["text"] = rng.choice(SHAPED + SHAPED_LINES)
                    c["xy"] = [rng.choice([0, 20, 40.5]), rng.choice([0, 15, 30.5])]
                    c["align"] = rng.choice(["left", "center", "right", "justify"])
                    c.pop("mode")
                if c["fn"] == "draw":
                    mode = rng.choice(["L", "RGB", "RGBA"])
                    base = Image.frombytes(mode, (160, 80), rng.randbytes(160 * 80 * len(mode)))
                    c["image"] = _image(base)
                    c["fill"] = [rng.randrange(256) for _ in mode]
                calls.append(c)
            _compare(path, size, calls, index, "raqm")


@pytest.mark.skipif(not EMOJI.is_file(), reason="needs Noto Color Emoji")
@pytest.mark.parametrize("layout", LAYOUTS)
def test_colour_glyphs_are_drawn_in_their_own_colours_as_pillow_draws_them(layout: str) -> None:
    rng = random.Random(0xE30)
    calls = []
    for text in ["😀", "🎮🔥", "A😀B", "👍🏽", "❤️", "x"]:
        for mode in ("RGBA", "RGB"):
            for embedded in (True, False):
                for stroke in (0, 2):  # (a stroke needs outlines: refused)
                    base = Image.frombytes(mode, (300, 160), rng.randbytes(300 * 160 * len(mode)))
                    calls.append(
                        {
                            "fn": "draw",
                            "text": text,
                            "xy": [3.5, 2],
                            "image": _image(base),
                            "fill": [rng.randrange(256) for _ in mode],
                            "embedded_color": embedded,
                            "stroke_width": stroke,
                        }
                    )
        calls.append({"fn": "textbbox", "text": text, "xy": [0, 0], "embedded_color": True})
        calls.append({"fn": "textlength", "text": text, "embedded_color": True})
        # (refused: embedded colours only in RGB and RGBA)
        calls.append(
            {
                "fn": "draw",
                "text": text,
                "fill": [200],
                "xy": [0, 0],
                "embedded_color": True,
                "image": _image(Image.new("L", (200, 140))),
            }
        )
    _compare(str(EMOJI), 109, calls, layout=layout)


def test_fonts_are_refused_where_pillow_refuses_them(tmp_path: Path) -> None:
    broken = tmp_path / "broken.ttf"
    broken.write_bytes(b"\x00\x01\x00\x00" + bytes(200))
    cases = [
        (str(ROOT / "nothing.ttf"), 12),
        (str(broken), 12),
        (str(BUNDLED[0]), 0),
        (str(BUNDLED[0]), -3),
    ]
    if EMOJI.is_file():
        cases.append((str(EMOJI), 24))  # a font of one size only
    answers = run(
        [
            {"op": "font", "font": {"path": path, "size": size, "layout": "basic"}, "calls": []}
            for path, size in cases
        ]
    )
    for (path, size), ours in zip(cases, answers, strict=True):
        with pytest.raises((OSError, ValueError)) as refused:
            ImageFont.truetype(path, size, layout_engine=BASIC)
        assert ours.get("error") == str(refused.value), (path, size)
    # what the basic layout cannot do, it refuses (KeyError)
    [ours] = run(
        [
            {
                "op": "font",
                "font": {"path": str(BUNDLED[0]), "size": 12, "layout": "basic"},
                "calls": [
                    {"fn": "getlength", "text": "x", "direction": "rtl"},
                    {"fn": "getbbox", "text": "x", "features": ["-kern"]},
                    {"fn": "getbbox", "text": "x", "language": "de"},
                ],
            }
        ]
    )
    assert {r["kind"] for r in ours["results"]} == {"Key"}


@pytest.mark.skipif("raqm" not in LAYOUTS, reason="needs Pillow's libraqm (FriBiDi)")
def test_libraqm_s_refusals_and_the_default_layout_are_pillow_s() -> None:
    calls = [
        {"fn": "getlength", "text": "two\rparagraphs"},  # (RuntimeError: one paragraph only)
        {"fn": "getlength", "text": "x\u2029y"},
        {"fn": "getlength", "text": "x", "direction": "up"},
        {"fn": "getbbox", "text": "x", "features": ["not a feature"]},
    ]
    _compare(str(BUNDLED[0]), 12, calls, layout="raqm")
    # a font opened without a layout has Pillow's: libraqm's, where it has it
    [ours] = run([{"op": "font", "font": {"path": str(BUNDLED[0]), "size": 12}, "calls": []}])
    default = ImageFont.truetype(str(BUNDLED[0]), 12).layout_engine
    assert ours["layout"].lower() == {BASIC: "basic"}.get(default, "raqm")


# -- fonts/fallback.py ---------------------------------------------------------------------------

CHAIN = [
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        0,
    ),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None, 0),
    (
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
        0,
    ),
    (str(EMOJI), None, 0),  # (bitmaps only: never fallen back to)
    ("/usr/share/fonts/opentype/unifont/unifont.otf", None, 0),
]
CHAIN_SCRIPTS = {"ja": [("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", None, 0)]}
MIXED = [
    "Hello",
    "Привет мир",
    "Γειά σου",
    "中文 测试",
    "日本語のテキスト",
    "Grüße äöü",
    "مرحبا بالعالم",
    "שלום עולם",
    "abc 中文 def",
    " 中 ",
    "áb",
    "é ẍ",
    "x 中 y",
    "Ω≈∑ ★ ♫",
    "A‍B",
    "tab\there",
    "́start",
    "中́",
    "x 😀 y",
    "Łódź ŠŽ",
    "x  中文  y",
    "　中　",
    "abc def",
    "١٢٣ ٤",
]


def _chain_files() -> bool:
    return all(Path(path).is_file() for path, _, _ in CHAIN + CHAIN_SCRIPTS["ja"])


@pytest.mark.skipif(not _chain_files(), reason="needs the fonts of the chain")
@pytest.mark.parametrize("layout", LAYOUTS)
def test_faces_that_fall_back_draw_and_measure_as_fallback_py_s(layout: str) -> None:
    from libre_panel.fonts.fallback import FallbackFont, Fallbacks

    rng = random.Random(0xFA11)
    for path in BUNDLED:
        size = rng.choice([11, 13.3, 16, 22.7, 32])
        language = rng.choice([None, "ja", "zh", "de"])
        asked = None if language is None else (lambda named=language: named)
        chain = Fallbacks(CHAIN, None, asked, CHAIN_SCRIPTS)
        font = FallbackFont(str(path), size, layout_engine=LAYOUT[layout], chain=chain)
        calls = []
        for _ in range(30):
            c: dict[str, Any] = {
                "fn": rng.choice(
                    ["getlength", "getbbox", "getmask2", "draw", "textbbox", "textlength"]
                ),
                "text": rng.choice(MIXED),
            }
            if c["fn"] in ("getlength", "getbbox", "getmask2"):
                c["mode"] = rng.choice(["", "L", "1", "RGBA"])
            if c["fn"] in ("getbbox", "getmask2", "draw", "textbbox"):
                c["anchor"] = rng.choice(
                    [None, "la", "ls", "lm", "mm", "ms", "rd", "ra", "lt", "xx"]
                )
                c["stroke_width"] = rng.choice([0, 0, 1, 1.5])
            if c["fn"] == "getmask2":
                c["stroke_filled"] = rng.random() < 0.5
                c["start"] = [rng.choice([0, 0.5, -0.25]), rng.choice([0, 0.3])]
            if c["fn"] in ("draw", "textbbox"):
                c["xy"] = [rng.choice([0, 20, 40.5]), rng.choice([0, 15, 30.5])]
            if c["fn"] == "draw":
                mode = rng.choice(["L", "RGBA"])
                c["image"] = _image(
                    Image.frombytes(mode, (180, 70), rng.randbytes(180 * 70 * len(mode)))
                )
                c["fill"] = [rng.randrange(256) for _ in mode]
            calls.append(c)
        fallbacks = {
            "fonts": [list(e) for e in CHAIN],
            "language": language,
            "scripts": {k: [list(e) for e in v] for k, v in CHAIN_SCRIPTS.items()},
        }
        [ours] = run(
            [
                {
                    "op": "font",
                    "digest": True,
                    "calls": calls,
                    "fallbacks": fallbacks,
                    "font": {"path": str(path), "size": size, "layout": layout},
                }
            ]
        )
        for c, mine in zip(calls, ours["results"], strict=True):
            theirs = _pillow(font, c)
            where = (path.name, size, language, {k: v for k, v in c.items() if k != "image"})
            if "error" in theirs:
                assert mine.get("error") == theirs["error"], where
            else:
                assert mine == theirs, where


def test_what_a_font_covers_is_read_as_fallback_py_reads_it(tmp_path: Path) -> None:
    from libre_panel.fonts.fallback import coverage

    fonts = [(str(p), 0) for p in BUNDLED] + [(p, i) for p, i in SYSTEM if Path(p).is_file()]
    fonts += [(p, i) for p, _, i in CHAIN if Path(p).is_file()]
    fonts += [
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 1),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 7),
    ]
    # files that are no fonts, or broken ones: cut short anywhere, a collection's header alone
    whole = BUNDLED[0].read_bytes()
    for n, cut in enumerate([0, 3, 10, 100, 300, 1000, 5000, 20000, len(whole) - 7]):
        broken = tmp_path / f"cut{n}.ttf"
        broken.write_bytes(whole[:cut])
        fonts.append((str(broken), 0))
    for name, data in [
        ("ttcf.ttc", b"ttcf\x00\x01\x00\x00\x00\x00\x00\x02"),
        ("short.ttc", b"ttcf\x00\x01"),
        ("noise.ttf", random.Random(5).randbytes(999)),
    ]:
        (tmp_path / name).write_bytes(data)
        fonts.append((str(tmp_path / name), 0))
    rng = random.Random(0xC0DE)
    codes = sorted(
        {
            *range(0, 0x250),
            *range(0x2000, 0x2070),
            *range(0x4E00, 0x4E40),
            *(rng.randrange(0x110000) for _ in range(400)),
            0xFFFF,
            0x1F600,
            0x10FFFF,
        }
    )
    answers = run([{"op": "coverage", "path": p, "index": i, "codes": codes} for p, i in fonts])
    for (path, index), ours in zip(fonts, answers, strict=True):
        theirs = coverage(path, index)
        if theirs is None:
            assert ours["coverage"] is None, (path, index)
            continue
        mine = ours["coverage"]
        assert mine is not None, (path, index)
        assert (mine["outlines"], mine["len"]) == (theirs.outlines, len(theirs)), (path, index)
        assert mine["has"] == [code in theirs for code in codes], (path, index)


@pytest.mark.skipif(
    __import__("unicodedata").unidata_version != "15.0.0",
    reason="the tables are Unicode 15.0's, as the release's Python (3.12) has them",
)
def test_the_unicode_tables_are_those_of_the_release_s_python() -> None:
    import unicodedata

    follows = {"Mn", "Mc", "Me", "Cf", "Zs", "Zl", "Zp", "Cc"}
    lines = []
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        kind = unicodedata.category(chr(code))
        parts = unicodedata.normalize("NFD", chr(code))
        if kind in follows or parts != chr(code):
            kind = kind if kind in follows else "-"
            lines.append(f"{code:04X} {kind} {','.join(f'{ord(p):04X}' for p in parts)}\n")
    [ours] = run([{"op": "unicode"}])
    assert ours["sha256"] == hashlib.sha256("".join(lines).encode()).hexdigest()


def test_system_fonts_are_found_as_discover_finds_them(tmp_path: Path) -> None:
    from libre_panel import i18n
    from libre_panel.fonts.fallback import _fontconfig, discover

    # Windows: its font folder, here a made one with some of the table's files
    fonts = tmp_path / "Fonts"
    fonts.mkdir()
    for name in ("segoeui.ttf", "segoeuib.ttf", "msyh.ttc", "YuGothR.ttc", "arial.ttf"):
        (fonts / name).write_bytes(b"")
    cases = [(True, str(tmp_path)), (False, None)]
    answers = run(
        [
            {"op": "discover", "windows": w, "windir": d, "language": i18n.language()}
            for w, d in cases
        ]
    )
    for (windows, windir), ours in zip(cases, answers, strict=True):
        environ = {"WINDIR": windir} if windir else dict(__import__("os").environ)
        theirs = discover("win32" if windows else "linux", environ)
        assert [tuple(e) for e in ours["fonts"]] == theirs.fonts, windows
        assert {k: [tuple(e) for e in v] for k, v in ours["scripts"]} == theirs.scripts, windows
    # fontconfig's answers, where it is there (an ideograph with the app's language too)
    for bold in (False, True):
        [ours] = run(
            [{"op": "fontconfig", "text": "A中ω😀", "bold": bold, "language": i18n.language()}]
        )
        theirs = [_fontconfig(ch, bold) for ch in "A中ω😀"]
        assert [[tuple(f) for f in a] for a in ours["answers"]] == theirs

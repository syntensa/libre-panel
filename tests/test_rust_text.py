"""rust/text against Pillow (docs/RUST.md, stage R2): fonts opened, measured and drawn as
Pillow 13.0's FreeTypeFont does it with its basic layout, and ImageDraw's text, textbbox and
textlength on top, byte for byte, with the FreeType Pillow's wheel brings."""

from __future__ import annotations

import base64
import hashlib
import random
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont
from rust_check import pillow_libfreetype, run

ROOT = Path(__file__).resolve().parent.parent
BASIC = ImageFont.Layout.BASIC
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
        if fn == "getlength":
            return {"length": font.getlength(text, mode)}
        if fn == "getbbox":
            return {
                "box": list(font.getbbox(text, mode, None, None, None, stroke, c.get("anchor")))
            }
        if fn == "getmask2":
            im, offset = font.getmask2(
                text,
                mode,
                None,
                None,
                None,
                stroke,
                c.get("anchor"),
                c.get("ink", 0),
                tuple(c.get("start", (0, 0))),
                stroke_filled=c.get("stroke_filled", False),
            )
            mask = Image.frombytes(im.mode, im.size, bytes(im)) if 0 not in im.size else None
            return {"image": _digest(mask or Image.new(im.mode, im.size)), "offset": list(offset)}
        options = {
            "anchor": c.get("anchor"),
            "spacing": c.get("spacing", 4),
            "align": c.get("align", "left"),
            "embedded_color": c.get("embedded_color", False),
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
        return {
            "length": draw.textlength(text, font=font, embedded_color=options["embedded_color"])
        }
    except (OSError, ValueError, KeyError) as error:
        return {"error": str(error)}


def _compare(path: str, size: float, calls: list[dict[str, Any]], index: int = 0) -> None:
    font = ImageFont.truetype(path, size, index, layout_engine=BASIC)
    [ours] = run(
        [
            {
                "op": "font",
                "digest": True,
                "calls": calls,
                "font": {"path": path, "size": size, "index": index, "layout": "basic"},
            }
        ]
    )
    where = f"{Path(path).name}[{index}] at {size}"
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


def test_fonts_are_measured_and_drawn_as_pillow_does_it() -> None:
    rng = random.Random(0x7E47)
    for path, index in _fonts():
        for size in rng.sample([8, 9.5, 11, 12, 13.3, 16, 18, 22.7, 24, 32, 40, 48, 64, 97], 3):
            calls = []
            for _ in range(30):
                c: dict[str, Any] = {
                    "fn": rng.choice(["getlength", "getbbox", "getmask2"]),
                    "text": rng.choice(WORDS),
                    "mode": rng.choice(["", "L", "1"]),
                }
                if c["fn"] != "getlength":
                    c["anchor"] = rng.choice(ANCHORS)
                    c["stroke_width"] = rng.choice([0, 0, 0, 1, 2, 0.5, 1.5])
                if c["fn"] == "getmask2":
                    c["stroke_filled"] = rng.random() < 0.5
                    c["start"] = [rng.choice([0, 0.25, 0.5, 0.75, -0.5]), rng.choice([0, 0.3])]
                calls.append(c)
            _compare(path, size, calls, index)


def test_text_is_drawn_and_its_box_and_length_are_as_imagedraw_s() -> None:
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
            _compare(path, size, calls, index)


@pytest.mark.skipif(not EMOJI.is_file(), reason="needs Noto Color Emoji")
def test_colour_glyphs_are_drawn_in_their_own_colours_as_pillow_draws_them() -> None:
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
    _compare(str(EMOJI), 109, calls)


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

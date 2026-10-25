"""Characters the bundled fonts have no glyph for are drawn with a system font
(libre_panel.fonts.fallback). The fonts of a machine differ from the next one's, so the tests make
their own: Liberation Sans (bundled; it has the Cyrillic, Greek and Hebrew that Barlow lacks) and
fonts made on the spot (tests/fake_fonts.py) for the scripts no bundled font has."""

import struct
import subprocess
import types
from pathlib import Path

import pytest
from fake_fonts import make_collection, make_font
from PIL import Image, ImageDraw, ImageFont, features

from libre_panel import i18n
from libre_panel.fonts import builtin_font_path, fallback
from libre_panel.fonts.fallback import (
    FallbackFont,
    Fallbacks,
    coverage,
    cursive,
    discover,
    truetype,
)
from libre_panel.render import facets
from libre_panel.sensors.demo import demo_snapshot

BASIC = ImageFont.Layout.BASIC
LAYOUTS = [BASIC] + ([ImageFont.Layout.RAQM] if features.check("raqm") else [])


def bundled(name: str) -> str:
    return str(builtin_font_path("builtin:" + name))


LIBERATION = (bundled("LiberationSans-Regular"), bundled("LiberationSans-Bold"), 0)


@pytest.fixture
def liberation() -> Fallbacks:
    """Fonts to fall back to that have Cyrillic, Greek and Hebrew."""
    return Fallbacks([LIBERATION])


@pytest.fixture
def use():
    """Make the process fall back to a set of fonts (as the renderer's faces do), and back."""
    before = fallback.set_fallbacks(None)

    def choose(fonts: Fallbacks) -> Fallbacks:
        fallback.set_fallbacks(fonts)
        return fonts

    yield choose
    fallback.set_fallbacks(before)


def face(name: str, size: int = 32, chain: Fallbacks | None = None, **options) -> FallbackFont:
    return FallbackFont(bundled(name), size, chain=chain, **options)


def plain(name: str, size: int = 32, **options) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(bundled(name), size, **options)


def draw(font, text, anchor="la", xy=(40, 70), size=(480, 140), **options) -> Image.Image:
    image = Image.new("L", size, 0)
    ImageDraw.Draw(image).text(xy, text, font=font, fill=255, anchor=anchor, **options)
    return image


def runs(*parts, anchor="ls", xy=(40, 70), size=(480, 140)) -> Image.Image:
    """``(font, text)`` pairs drawn one after the other on one baseline, each where the one
    before ends: what a text of several fonts has to come out as."""
    image = Image.new("L", size, 0)
    pen = float(xy[0])
    for font, text in parts:
        ImageDraw.Draw(image).text((pen, xy[1]), text, font=font, fill=255, anchor=anchor)
        pen += font.getlength(text)
    return image


def ink(image: Image.Image) -> Image.Image:
    """What was drawn, cut out: where it lies does not matter."""
    box = image.getbbox()
    assert box is not None, "nothing was drawn"
    return image.crop(box)


def colours(image: Image.Image) -> int:
    return sum(1 for count in image.histogram() if count)


# -- what a font file covers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Barlow-Medium", "LiberationSans-Regular", "JetBrainsMono-Medium"]
)
def test_the_cmap_says_what_freetype_draws(name):
    """A character is covered when FreeType draws a glyph of the font and not its missing one
    (the basic layout: a character is its glyph and nothing else, no libraqm that builds an
    accented letter from its parts)."""
    path = bundled(name)
    covered = coverage(path)
    font = ImageFont.truetype(path, 32, layout_engine=BASIC)

    def look(code: int):
        mask = font.getmask(chr(code))
        return mask.size, bytes(mask)

    missing = {look(0xFFFF), look(0x10FFFE)}  # no font has a noncharacter
    for code in range(0x21, 0x3000):
        if 0xD800 <= code < 0xE000:
            continue
        seen = look(code)
        if any(seen[1]):  # (a blank glyph cannot be told from a blank missing one)
            assert (code in covered) == (seen not in missing), hex(code)


def test_the_bundled_fonts_have_what_the_review_found_them_lacking():
    barlow, liberation, jetbrains = (coverage(bundled(n)) for n in (
        "Barlow-Medium", "LiberationSans-Regular", "JetBrainsMono-Medium"))  # fmt: skip
    assert all(ch in barlow for ch in "Aä€ế…") and not any(ch in barlow for ch in "Жα中한א♪😀")
    assert all(ch in liberation for ch in "ЖαאΩ") and not any(ch in liberation for ch in "中한😀")
    assert "Ж" in jetbrains and "中" not in jetbrains


def one_table(form: int, body: bytes, platform: int, encoding: int) -> bytes:
    """A cmap table of one subtable."""
    return struct.pack(">HHHHL", 0, 1, platform, encoding, 12) + struct.pack(">H", form) + body


def format4(segments) -> bytes:
    """The body of a format 4 subtable: ``(first, last, delta, glyphs)``, the glyphs an array that
    the segment points into, or None. The last segment, 0xFFFF, is added."""
    segments = [*segments, (0xFFFF, 0xFFFF, 1, None)]
    count = len(segments)
    array, offsets = [], []
    for n, (_first, _last, _delta, glyphs) in enumerate(segments):
        if glyphs is None:
            offsets.append(0)
        else:
            offsets.append(2 * (count - n + len(array)))  # from its own entry to its glyphs
            array += glyphs
    body = struct.pack(">5H", 16 + 8 * count + 2 * len(array), 0, 2 * count, 0, 0)  # (rangeShift:)
    body += struct.pack(">H", 0)
    body += struct.pack(f">{count}H", *(s[1] for s in segments)) + b"\0\0"
    body += struct.pack(f">{count}H", *(s[0] for s in segments))
    body += struct.pack(f">{count}H", *(s[2] for s in segments))
    return body + struct.pack(f">{count}H", *offsets) + struct.pack(f">{len(array)}H", *array)


def ranges(table: bytes):
    found = fallback._unicode_ranges(table)
    return None if found is None else sorted(found)


def test_every_form_of_cmap_subtable_is_read():
    codes = bytes(65) + bytes([5, 0, 7]) + bytes(256 - 68)  # glyphs for A and C
    assert ranges(one_table(0, struct.pack(">HH", 262, 0) + codes, 1, 0)) is None  # Mac: no Unicode
    assert ranges(one_table(0, struct.pack(">HH", 262, 0) + codes, 0, 3)) == [(65, 65), (67, 67)]
    # format 4: segments, one of them with an array of glyphs that has a missing one in it
    body = format4([(0x30, 0x39, 100, None), (0x41, 0x45, 0, [1, 2, 0, 4, 5])])
    assert ranges(one_table(4, body, 3, 1)) == [(0x30, 0x39), (0x41, 0x42), (0x44, 0x45)]
    body = struct.pack(">HHHH3H", 16, 0, 0x30, 3, 5, 0, 7)  # format 6: a trimmed table
    assert ranges(one_table(6, body, 0, 4)) == [(0x30, 0x30), (0x32, 0x32)]
    body = struct.pack(">HLLLL3H", 0, 0, 0, 0x1F600, 3, 5, 0, 7)  # format 10
    assert ranges(one_table(10, body, 3, 10)) == [(0x1F600, 0x1F600), (0x1F602, 0x1F602)]
    groups = struct.pack(">LLL", 0x4E00, 0x4E05, 1) + struct.pack(">LLL", 0x1F600, 0x1F64F, 9)
    for form in (12, 13):  # groups of code points
        body = struct.pack(">HLLL", 0, 0, 0, 2) + groups
        assert ranges(one_table(form, body, 3, 10)) == [(0x4E00, 0x4E05), (0x1F600, 0x1F64F)]
    assert ranges(one_table(14, bytes(16), 0, 5)) is None  # variation sequences only


def test_the_widest_unicode_table_is_the_one_read():
    narrow = struct.pack(">H", 6) + struct.pack(">HHHH2H", 0, 0, 0x41, 2, 1, 1)
    wide = struct.pack(">HHLLL", 12, 0, 0, 0, 1) + struct.pack(">LLL", 0x4E00, 0x4E00, 1)
    head = struct.pack(">HH", 0, 2) + struct.pack(">HHL", 3, 1, 20)
    head += struct.pack(">HHL", 3, 10, 20 + len(narrow))
    assert ranges(head + narrow + wide) == [(0x4E00, 0x4E00)]


def test_a_font_file_is_read_for_what_it_covers(tmp_path):
    one = make_font(tmp_path / "one.ttf", "中日😀ا")
    found = coverage(str(one))
    assert [ch in found for ch in "中日😀اA"] == [True] * 4 + [False] and found.outlines
    assert len(found) == 4
    pair = make_collection(tmp_path / "two.ttc", "AB", "中")  # a collection: its fonts by index
    assert [len(coverage(str(pair), n)) for n in (0, 1)] == [2, 1]
    assert coverage(str(pair), 2) is None
    assert not coverage(str(make_font(tmp_path / "emoji.ttf", "😀", outlines=False))).outlines
    (tmp_path / "note.txt").write_text("not a font")
    assert coverage(str(tmp_path / "note.txt")) is None
    assert coverage(str(tmp_path / "missing.ttf")) is None
    cut = tmp_path / "cut.ttf"
    cut.write_bytes(one.read_bytes()[:40])
    assert coverage(str(cut)) is None  # a file that ends too soon is no font


# -- the fonts to fall back to -------------------------------------------------------------------


def test_the_first_font_that_has_the_character_is_the_one(tmp_path):
    a = make_font(tmp_path / "a.ttf", "Ж")
    a_bold = make_font(tmp_path / "a-bold.ttf", "Ж")
    b = make_font(tmp_path / "b.ttf", "Ж中")
    fonts = Fallbacks([(a, a_bold, 0), (b, None, 0)])
    assert fonts.find("Ж") == (str(a), 0)
    assert fonts.find("Ж", bold=True) == (str(a_bold), 0)
    assert fonts.find("中") == (str(b), 0) and fonts.find("中", True) == (str(b), 0)  # no bold file
    assert fonts.find("😀") is None and fonts.find("😀") is None


def test_a_font_of_bitmaps_is_no_font_to_fall_back_to(tmp_path):
    emoji = make_font(tmp_path / "emoji.ttf", "😀", outlines=False)  # colour emoji: no outlines
    mono = make_font(tmp_path / "mono.ttf", "😀")
    assert Fallbacks([(emoji, None, 0)]).find("😀") is None
    assert Fallbacks([(emoji, None, 0), (mono, None, 0)]).find("😀") == (str(mono), 0)


def test_a_font_freetype_makes_no_face_of_is_no_font_to_fall_back_to(tmp_path, monkeypatch):
    """Noto Color Emoji has outlines for no size (a bitmap of one): Pillow cannot open it at the
    size of a text, and the next font that has the emoji is the one."""
    bitmaps = make_font(tmp_path / "colour.ttf", "😀")
    mono = make_font(tmp_path / "mono.ttf", "😀")
    real = ImageFont.truetype

    def truetype_but(path, *args, **kwargs):
        if str(path) == str(bitmaps):
            raise OSError("invalid pixel size")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", truetype_but)
    fonts = Fallbacks([(bitmaps, None, 0), (mono, None, 0)])
    assert fonts.find("😀") == (str(mono), 0)


def test_fontconfig_is_asked_for_what_no_listed_font_has(tmp_path, monkeypatch):
    cjk = make_font(tmp_path / "cjk.ttf", "中日")
    asked = []

    def query(ch, bold):
        asked.append((ch, bold))
        return [(str(tmp_path / "gone.ttf"), 0), (str(cjk), 0)] if ch in "中日" else []

    fonts = Fallbacks(query=query)
    assert fonts.find("中") == (str(cjk), 0) and asked == [("中", False)]
    assert fonts.find("日") == (str(cjk), 0) and fonts.find("中") == (str(cjk), 0)
    assert asked == [("中", False)]  # that font is known now: the next character costs no question
    assert fonts.find("😀") is None and fonts.find("😀") is None
    assert asked == [("中", False), ("😀", False)]  # and what nobody has is not asked twice
    assert fonts.find("日", bold=True) == (str(cjk), 0)  # (the bold ones are another question)
    assert asked[-1] == ("日", True)
    monkeypatch.setattr(fallback, "QUERIES", 3)
    assert fonts.find("ก") is None and len(asked) == 3  # the questions are used up


def test_a_query_that_fails_is_no_answer():
    def broken(ch, bold):
        raise subprocess.TimeoutExpired("fc-match", 5)

    assert Fallbacks(query=broken).find("中") is None


def test_the_fonts_of_windows_are_those_of_its_folder(tmp_path):
    folder = tmp_path / "Fonts"
    folder.mkdir()
    for name in ("segoeui.ttf", "segoeuib.ttf", "msyh.ttc", "seguisym.ttf"):
        make_font(folder / name, "x")
    found = discover("win32", {"WINDIR": str(tmp_path)})
    assert [(Path(a).name, b and Path(b).name, i) for a, b, i in found.fonts] == [
        ("segoeui.ttf", "segoeuib.ttf", 0),
        ("seguisym.ttf", None, 0),
        ("msyh.ttc", None, 0),
    ]
    assert found.query is None
    assert not discover("win32", {"WINDIR": str(tmp_path / "nowhere")}).fonts


def test_elsewhere_the_usual_folders_are_looked_in_and_fontconfig_asked(tmp_path, monkeypatch):
    noto = tmp_path / "noto"
    noto.mkdir()
    make_font(noto / "NotoSans-Regular.ttf", "x")
    make_font(noto / "NotoSans-Bold.ttf", "x")
    monkeypatch.setattr(fallback, "_FOLDERS", (str(tmp_path / "gone"), str(noto)))
    found = discover("linux", {})
    assert found.fonts == [(str(noto / "NotoSans-Regular.ttf"), str(noto / "NotoSans-Bold.ttf"), 0)]
    assert found.query is fallback._fontconfig


def test_fontconfig_answers_with_files_and_indexes(monkeypatch):
    seen = []

    def run(command, **options):
        seen.append(command)
        return types.SimpleNamespace(
            stdout="/f/one.ttc\t2\n/f/two.otf\t0\nnoise\n/f/three.ttf\tx\n"
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/fc-match")
    assert fallback._fontconfig("中", False) == [("/f/one.ttc", 2), ("/f/two.otf", 0)]
    assert seen[-1][:2] == ["/usr/bin/fc-match", "-s"] and seen[-1][-1] == ":charset=4e2d"
    fallback._fontconfig("😀", True)
    assert seen[-1][-1] == ":charset=1f600:weight=bold"
    monkeypatch.setattr("shutil.which", lambda name: None)  # no fontconfig: no answer
    assert fallback._fontconfig("中", False) == []


def test_the_east_asian_fonts_of_the_language_come_first_for_the_ideographs(tmp_path):
    """Japanese and Chinese draw the same ideographs in forms of their own, and a font has one
    language's: a Chinese font listed before a Japanese one drew Japanese text in Chinese forms.
    The fonts of the app's language come first, for those characters only."""
    chinese = make_font(tmp_path / "chinese.ttf", "直骨Ж")
    chinese_bold = make_font(tmp_path / "chinese-bold.ttf", "直骨Ж")
    japanese = make_font(tmp_path / "japanese.ttf", "直骨あЖ")
    japanese_bold = make_font(tmp_path / "japanese-bold.ttf", "直骨あЖ")
    zh, ja = (chinese, chinese_bold, 0), (japanese, japanese_bold, 0)
    language = ["en"]
    fonts = Fallbacks([zh, ja], language=lambda: language[0], scripts={"ja": [ja], "zh": [zh]})
    assert fonts.find("直") == (str(chinese), 0)  # no language of those: the order of the list
    language[0] = "ja"
    assert fonts.find("直") == (str(japanese), 0)
    assert fonts.find("直", bold=True) == (str(japanese_bold), 0)
    assert fonts.find("骨") == (str(japanese), 0) and fonts.find("あ") == (str(japanese), 0)
    assert fonts.find("Ж") == (str(chinese), 0)  # what is no ideograph keeps the order of the list
    language[0] = "zh"  # the language was changed in the app: the next text takes it in
    assert fonts.find("直") == (str(chinese), 0) and fonts.find("直", True) == (
        str(chinese_bold),
        0,
    )
    language[0] = "de"
    assert fonts.find("直") == (str(chinese), 0)
    language[0] = "ja"
    assert fonts.find("直") == (str(japanese), 0)  # (and what was found for a language is kept)
    assert Fallbacks([zh, ja]).find("直") == (str(chinese), 0)  # without a language: as it was


def test_the_face_of_the_language_in_a_collection_is_the_one(tmp_path):
    """Noto Sans CJK is one file of faces: the first is Japanese, the third Simplified Chinese."""
    noto = make_collection(tmp_path / "noto.ttc", "直", "直", "直", "直", "直")
    zenhei = make_font(tmp_path / "zenhei.ttf", "直")
    language = ["ja"]
    fonts = Fallbacks(
        [(noto, None, 0), (zenhei, None, 0)],
        language=lambda: language[0],
        scripts={"ja": [(noto, None, 0)], "zh": [(noto, None, 2), (zenhei, None, 0)]},
    )
    assert fonts.find("直") == (str(noto), 0)
    language[0] = "zh"
    assert fonts.find("直") == (str(noto), 2)


def japanese_machine(tmp_path, monkeypatch, with_noto: bool):
    """The folders of a Linux PC with a Chinese font (WenQuanYi) and a Japanese one (IPAGothic),
    and Noto Sans CJK if asked: every font has the ideographs."""
    wqy, ipa = tmp_path / "wqy", tmp_path / "ipafont-gothic"
    wqy.mkdir()
    ipa.mkdir()
    make_collection(wqy / "wqy-zenhei.ttc", "直")
    make_font(ipa / "ipag.ttf", "直")
    folders = [wqy, ipa]
    if with_noto:
        noto = tmp_path / "noto-cjk"
        noto.mkdir()
        make_collection(noto / "NotoSansCJK-Regular.ttc", "直", "直", "直", "直", "直")
        folders.append(noto)
    monkeypatch.setattr(fallback, "_FOLDERS", tuple(str(folder) for folder in folders))


@pytest.mark.parametrize("language, noto, wanted", [
    ("ja", False, ("ipafont-gothic/ipag.ttf", 0)),
    ("zh", False, ("wqy/wqy-zenhei.ttc", 0)),
    ("de", False, ("wqy/wqy-zenhei.ttc", 0)),  # (as it was: the order of the table)
    ("ja", True, ("noto-cjk/NotoSansCJK-Regular.ttc", 0)),
    ("zh", True, ("noto-cjk/NotoSansCJK-Regular.ttc", 2)),
])  # fmt: skip
def test_on_linux_the_fonts_of_the_apps_language_draw_the_ideographs(
    tmp_path, monkeypatch, language, noto, wanted
):
    japanese_machine(tmp_path, monkeypatch, noto)
    monkeypatch.setattr(fallback, "_fontconfig", lambda ch, bold: [])
    found = discover("linux", {})
    i18n.set_language(language)
    path, index = found.find("直")
    assert (Path(path).relative_to(tmp_path).as_posix(), index) == wanted
    for other in ("ja", "zh"):  # the language can be changed while the app runs
        i18n.set_language(other)
        assert found.variant() == other


@pytest.mark.parametrize("language, wanted", [
    ("ja", "YuGothR.ttc"), ("zh", "msyh.ttc"), ("de", "YuGothR.ttc"),  # (de: the table's order)
])  # fmt: skip
def test_on_windows_the_fonts_of_the_apps_language_draw_the_ideographs(tmp_path, language, wanted):
    folder = tmp_path / "Fonts"
    folder.mkdir()
    make_font(folder / "segoeui.ttf", "x")
    for name in ("YuGothR.ttc", "msyh.ttc"):
        make_font(folder / name, "直")
    found = discover("win32", {"WINDIR": str(tmp_path)})
    i18n.set_language(language)
    assert Path(found.find("直")[0]).name == wanted


def test_the_text_of_a_face_follows_the_language_of_the_app(tmp_path, use):
    """A face that drew a text keeps how it laid it out, until the language is another."""
    wide = make_font(tmp_path / "japanese.ttf", "直", advance=1000)
    narrow = make_font(tmp_path / "chinese.ttf", "直", advance=500)
    language = ["ja"]
    chain = use(
        Fallbacks(
            [(narrow, None, 0), (wide, None, 0)],
            language=lambda: language[0],
            scripts={"ja": [(wide, None, 0)], "zh": [(narrow, None, 0)]},
        )
    )
    font = face("Barlow-Medium", 40, chain)
    assert font.getlength("直") == 40 and font.getlength("直") == 40  # (the second one is kept)
    language[0] = "zh"
    assert font.getlength("直") == 20


def test_fontconfig_is_asked_for_the_language_of_the_app_for_the_ideographs(monkeypatch):
    seen = []

    def run(command, **options):
        seen.append(command[-1])
        return types.SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/fc-match")
    for language, ch, bold, wanted in [
        ("en", "中", False, ":charset=4e2d"),
        ("ja", "中", False, ":charset=4e2d:lang=ja"),
        ("zh", "中", True, ":charset=4e2d:lang=zh-cn:weight=bold"),
        ("ja", "😀", False, ":charset=1f600"),  # (no ideograph: the language has no say)
        ("de", "ｱ", False, ":charset=ff71"),
    ]:
        i18n.set_language(language)
        fallback._fontconfig(ch, bold)
        assert seen[-1] == wanted, language


def test_the_fonts_of_the_system_are_found_once_and_can_be_replaced(monkeypatch, use):
    found = []
    monkeypatch.setattr(fallback, "discover", lambda platform, env: found.append(1) or Fallbacks())
    assert fallback.system_fallbacks() is fallback.system_fallbacks() and found == [1]
    mine = use(Fallbacks())
    assert fallback.system_fallbacks() is mine and found == [1]


# -- the face ------------------------------------------------------------------------------------

ANCHORS = [None] + [h + v for h in "lmr" for v in "amsd"]
LATIN = ["Hello", "Hamburgefonstiv 0123", "Äpfel & Öl, Zürich °C – 5 € ’", "x", "  lead", "W.A.V.E"]


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("name", ["Barlow-Medium", "LiberationSans-Bold", "JetBrainsMono-Bold"])
def test_text_the_font_has_is_drawn_exactly_as_before(name, layout, liberation):
    for size in (11, 24, 57):
        before = plain(name, size, layout_engine=layout)
        font = face(name, size, liberation, layout_engine=layout)
        for text in LATIN:
            assert font.getlength(text) == before.getlength(text)
            for anchor in ANCHORS:
                assert font.getbbox(text, anchor=anchor) == before.getbbox(text, anchor=anchor)
                for xy in ((40, 70), (40.5, 70), (40.25, 70.75)):
                    now = draw(font, text, anchor, xy).tobytes()
                    assert now == draw(before, text, anchor, xy).tobytes()


def test_what_the_font_has_not_is_drawn_by_the_fallback(liberation):
    font = face("Barlow-Medium", 36, liberation)
    own, theirs = plain("Barlow-Medium", 36), plain("LiberationSans-Regular", 36)
    word = "Привет"
    assert draw(font, word, "ls").tobytes() != draw(own, word, "ls").tobytes()  # not the boxes
    assert draw(font, word, "ls").tobytes() == draw(theirs, word, "ls").tobytes()  # one font
    # Cyrillic by Liberation, the comma and the space (Barlow has them) by Barlow
    phrase = "Привет, мир"
    parts = [(theirs, "Привет"), (own, ", "), (theirs, "мир")]
    assert draw(font, phrase, "ls").tobytes() == runs(*parts).tobytes()
    assert font.getlength(phrase) == pytest.approx(sum(f.getlength(t) for f, t in parts), abs=0.05)
    box = font.getbbox(phrase, anchor="ls")
    assert box[0] <= 0 and box[2] >= round(font.getlength(phrase)) - 1 and box[1] < 0


@pytest.mark.parametrize("anchor", ["ls", "la", "lm", "ld"])
def test_runs_share_the_baseline_and_the_size(liberation, anchor):
    font = face("Barlow-Medium", 40, liberation)
    own, theirs = plain("Barlow-Medium", 40), plain("LiberationSans-Regular", 40)
    mixed, alone = draw(font, "AЖ", anchor), draw(own, "A", anchor)
    cut = int(40 + own.getlength("A"))  # the A is where it is on its own
    assert mixed.crop((0, 0, cut, 140)).tobytes() == alone.crop((0, 0, cut, 140)).tobytes()
    assert mixed.getbbox()[3] == alone.getbbox()[3]  # and the Ж ends on the same baseline
    if anchor == "ls":  # the Ж is what the other font draws, at the same size
        assert mixed.tobytes() == runs((own, "A"), (theirs, "Ж")).tobytes()


@pytest.mark.parametrize("vertical", "amsd")
@pytest.mark.parametrize("horizontal", "mr")
def test_the_whole_text_is_what_the_anchor_says(liberation, horizontal, vertical):
    """Centred and right: the runs together, as one text, are placed around the anchor, a whole
    pixel as Pillow does it."""
    font = face("Barlow-Medium", 40, liberation)
    own, theirs = plain("Barlow-Medium", 40), plain("LiberationSans-Regular", 40)
    total = own.getlength("A") + theirs.getlength("Ж")
    shift = int(total / 2 + 0.5) if horizontal == "m" else int(total + 0.5)
    anchored = draw(font, "AЖ", horizontal + vertical, (240, 70))
    from_the_left = draw(font, "AЖ", "l" + vertical, (240 - shift, 70))
    assert anchored.tobytes() == from_the_left.tobytes()


def test_a_bold_face_falls_back_to_the_bold_font(liberation):
    bold, medium, semi = (
        face(n, 36, liberation) for n in ("Barlow-Bold", "Barlow-Medium", "Barlow-SemiBold")
    )
    regular_font, bold_font = plain("LiberationSans-Regular", 36), plain("LiberationSans-Bold", 36)
    assert draw(bold, "Ж", "ls").tobytes() == draw(bold_font, "Ж", "ls").tobytes()
    assert draw(semi, "Ж", "ls").tobytes() == draw(bold_font, "Ж", "ls").tobytes()
    assert draw(medium, "Ж", "ls").tobytes() == draw(regular_font, "Ж", "ls").tobytes()


def test_spaces_marks_and_invisible_characters_go_with_their_neighbours(liberation):
    font = face("Barlow-Medium", 36, liberation)
    own, theirs = plain("Barlow-Medium", 36), plain("LiberationSans-Regular", 36)
    # the spaces at the edge of a run are the font's own (with libraqm those of a right-to-left
    # run would lie at its wrong end); those inside it are the other font's
    assert font.getlength(" Привет ") == pytest.approx(
        2 * own.getlength(" ") + theirs.getlength("Привет"), abs=0.05
    )
    assert font.getlength("Привет мир") == pytest.approx(theirs.getlength("Привет мир"), abs=0.05)
    # a space the font has not (a thin one) is a plain space, not a box
    assert " " not in coverage(bundled("Barlow-Medium"))
    assert draw(font, "A B").tobytes() == draw(own, "A B").tobytes()
    # variation selectors and joiners that the font has not are left out
    assert draw(font, "A️B‍C").tobytes() == draw(own, "ABC").tobytes()
    # a mark goes with the letter before it, in the font of that letter
    assert draw(font, "Ж́", "ls").tobytes() == draw(theirs, "Ж́", "ls").tobytes()
    assert draw(font, "é").tobytes() == draw(own, "é").tobytes()


def test_a_text_of_invisible_characters_is_nothing(liberation):
    font = face("Barlow-Medium", 36, liberation)
    barlow = coverage(bundled("Barlow-Medium"))
    assert "\u200d" not in barlow and "\ufe0f" not in barlow  # (not boxes, then: no glyph at all)
    for text in ("\u200d", "\ufe0f\u200d"):
        assert font.getlength(text) == 0
        assert font.getbbox(text, anchor="ls")[0] == font.getbbox(text, anchor="ls")[2]
        assert not draw(font, text, "ls").getbbox()


def test_a_character_no_font_has_is_drawn_as_before(liberation):
    font = face("Barlow-Medium", 36, liberation)
    own = plain("Barlow-Medium", 36)
    for text in ("ก", "AกB", "\U0001f600"):  # Thai and an emoji: not in these fonts
        assert draw(font, text).tobytes() == draw(own, text).tobytes()
        assert font.getlength(text) == own.getlength(text)


def test_a_fallback_that_is_no_font_is_left_out(tmp_path):
    broken = tmp_path / "broken.ttf"
    broken.write_bytes(b"not a font at all")
    font = face("Barlow-Medium", 36, Fallbacks([(broken, None, 0)]))
    assert draw(font, "Привет").tobytes() == draw(plain("Barlow-Medium", 36), "Привет").tobytes()


def test_a_face_whose_coverage_is_unknown_is_left_alone(liberation, monkeypatch):
    monkeypatch.setattr(fallback, "coverage", lambda *args: None)
    font = face("Barlow-Medium", 36, liberation)
    assert draw(font, "Привет").tobytes() == draw(plain("Barlow-Medium", 36), "Привет").tobytes()


def test_scripts_no_bundled_font_has_come_from_fonts_of_the_system(tmp_path):
    """Ideographs, an emoji beyond the Basic Multilingual Plane and Arabic: each from a font of its
    own, at the size of the text and on its baseline."""
    cjk = make_font(tmp_path / "cjk.ttf", "中日", box=(50, -100, 950, 800))
    emoji = make_font(tmp_path / "emoji.ttf", "😀", box=(0, 0, 1000, 1000))
    arabic = make_font(tmp_path / "arabic.ttf", "اب", box=(100, 0, 400, 600))
    fonts = Fallbacks([(cjk, None, 0), (emoji, None, 0), (arabic, None, 0)])
    font, own = face("Barlow-Medium", 40, fonts), plain("Barlow-Medium", 40)
    image = draw(font, "A中😀ا", "ls")
    start = int(40 + own.getlength("A"))

    def sizes(first: int, last: int):
        box = image.crop((first, 0, last, 140)).getbbox()
        return box[2] - box[0], box[3] - box[1], box[3]

    width, height, bottom = sizes(start, start + 40)  # 900 x 900 units, 100 under the baseline
    assert (height, bottom) == (36, 70 + 4) and width in (36, 37)
    assert sizes(start + 41, start + 81)[1:] == (40, 70)  # the emoji: 1000 x 1000, on the baseline
    width, height, bottom = sizes(start + 81, 480)  # the letter: 300 x 600 units
    assert (height, bottom) == (24, 70) and width in (12, 13)
    assert font.getlength("中") == 40 and font.getlength("A中") == own.getlength("A") + 40


def test_the_masks_take_the_mode_asked_for(liberation):
    font = face("Barlow-Medium", 36, liberation)
    image = Image.new("L", (300, 100), 0)
    drawn = ImageDraw.Draw(image)
    drawn.fontmode = "1"  # no smooth edges
    drawn.text((10, 60), "ЖA", font=font, fill=255, anchor="ls")
    assert colours(image) == 2
    assert colours(draw(font, "ЖA", "ls")) > 2


def test_lines_and_boxes_of_a_text_count_the_runs(liberation):
    font = face("Barlow-Medium", 30, liberation)
    measure = ImageDraw.Draw(Image.new("L", (10, 10)))
    assert measure.textlength("AЖЖЖ", font=font) == font.getlength("AЖЖЖ")
    assert (
        measure.textbbox((0, 0), "AЖЖЖ", font=font)[2]
        > measure.textbbox((0, 0), "A", font=font)[2] + 40
    )
    image = Image.new("L", (300, 150), 0)  # two lines, the second with the other font's letters
    ImageDraw.Draw(image).text((10, 10), "A\nЖЖ", font=font, fill=255)
    top, bottom = image.crop((0, 0, 300, 50)).getbbox(), image.crop((0, 50, 300, 150)).getbbox()
    assert top and bottom and bottom[2] - bottom[0] > top[2] - top[0]
    one, two = (measure.textbbox((0, 0), t, font=font) for t in ("A", "A\nЖЖ"))
    assert two[3] > one[3] * 1.5


def test_a_text_is_looked_through_once_and_not_many_are_kept(liberation, monkeypatch):
    asked = []
    original = liberation.find
    monkeypatch.setattr(
        liberation, "find", lambda ch, bold=False: asked.append(ch) or original(ch, bold)
    )
    font = face("Barlow-Medium", 30, liberation)
    for _ in range(40):
        draw(font, "Привет")
        font.getlength("Привет")
        font.getbbox("Привет")
    assert sorted(asked) == sorted("Привет")  # each character, once
    for n in range(fallback.PLANS + 20):
        font.getlength(f"Ж{n}")
    assert len(font._plans) <= fallback.PLANS


@pytest.mark.parametrize("layout", LAYOUTS)
def test_right_to_left_text_is_drawn_in_logical_order_and_with_libraqm_shaped(liberation, layout):
    """Hebrew by the fallback comes out as Pillow draws it in that layout: the letters in the
    order they are written, from the left, and with libraqm shaped and reordered, the first
    letter on the right."""
    word = "שלום"  # shin lamed vav mem, in the order they are written
    font = face("Barlow-Medium", 40, liberation, layout_engine=layout)
    theirs = plain("LiberationSans-Regular", 40, layout_engine=layout)
    assert draw(font, word, "ls").tobytes() == draw(theirs, word, "ls").tobytes()
    written = runs(*[(theirs, letter) for letter in word])
    if layout == BASIC:
        assert draw(font, word, "ls").tobytes() == written.tobytes()
    else:
        assert draw(font, word, "ls").tobytes() != written.tobytes()  # reordered


def test_the_face_is_made_by_pillows_function(monkeypatch):
    """A tool that lays text out with the basic layout (tools/compare_reference.py) patches
    ``ImageFont.truetype``: the faces go on to be made so."""
    real = ImageFont.truetype
    monkeypatch.setattr(
        ImageFont, "truetype", lambda *a, **k: real(*a, **{**k, "layout_engine": BASIC})
    )
    font = truetype(bundled("Barlow-Medium"), 20)
    assert type(font) is FallbackFont and font.layout_engine == BASIC
    monkeypatch.setattr(ImageFont, "truetype", lambda *a, **k: "not a font")
    assert truetype(bundled("Barlow-Medium"), 20) == "not a font"  # what is no FreeType face stays


# -- in the renderer -----------------------------------------------------------------------------


def theme(widgets, size=(300, 80), **parts):
    display = {"width": size[0], "height": size[1]}
    return {"format": "libre-panel-theme/1", "name": "t", "background": {"color": "#000000"},
            "display": display, "widgets": widgets, **parts}  # fmt: skip


def frame_of(widget: dict, size=(300, 80)) -> Image.Image:
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    base = {"type": "text", "id": "t", "x": 10, "y": 10, "font_size": 30, "color": "#ffffff"}
    renderer = Renderer(parse_theme(theme([{**base, **widget}], size)), preview=True)
    try:
        return renderer.render(demo_snapshot(1_700_000_000))[0].convert("L")
    finally:
        renderer.close()


def test_the_fonts_of_the_renderer_fall_back(use, liberation, tmp_path):
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    use(liberation)
    folder = tmp_path / "theme"
    folder.mkdir()
    (folder / "own.ttf").write_bytes(Path(bundled("Barlow-Bold")).read_bytes())
    renderer = Renderer(parse_theme(theme([]), folder), preview=True)
    expected = {"builtin:Barlow-Medium": "Regular", "": "Regular", "own.ttf": "Bold"}
    for ref, weight in expected.items():
        font = renderer.font(ref, 20)
        assert isinstance(font, FallbackFont)
        assert font.getlength("Ж") == plain(f"LiberationSans-{weight}", 20).getlength("Ж")
    renderer.close()


def test_a_text_widget_shows_the_letters_its_font_lacks(use, liberation):
    use(Fallbacks())  # nothing to fall back to: boxes, as before
    boxes = frame_of({"text": "Привет"})
    use(liberation)
    letters = frame_of({"text": "Привет"})
    assert letters.tobytes() != boxes.tobytes()
    reference = Image.new("L", (300, 80), 0)  # the shapes Liberation draws, at the same size
    ImageDraw.Draw(reference).text(
        (10, 10), "Привет", font=plain("LiberationSans-Regular", 30), fill=255
    )
    assert ink(letters).size == ink(reference).size
    spaced = frame_of({"text": "Привет", "letter_spacing": 3})  # letter by letter
    assert spaced.getbbox() and spaced.getbbox()[2] > letters.getbbox()[2] + 10
    assert frame_of({"text": "Привет", "tabular": True, "align": "right", "x": 290}).getbbox()


def plain_truetype(font, size=10, index=0, **options):
    return ImageFont.truetype(str(font), size, index, **options)


@pytest.mark.parametrize("name", ["lumen", "studio", "libre-default", "spur-classic"])
def test_a_theme_with_latin_text_is_drawn_exactly_as_with_pillows_own_faces(name, monkeypatch):
    """The whole renderer, with its caches and its glyph by glyph layouts: the frame of a
    built-in theme, with its time, numbers, names and cards, is the same picture whether the
    faces are Pillow's own or fall back."""
    from libre_panel.render import light, overlays
    from libre_panel.render import renderer as renderer_module
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import find_theme, load_theme

    def frame(face_maker) -> bytes:
        for module in (renderer_module, light, overlays):
            monkeypatch.setattr(module, "truetype", face_maker)
        light.face.cache_clear()
        renderer = Renderer(load_theme(find_theme(name)), animate=True)
        try:
            return renderer.render(demo_snapshot(1_700_000_000), 5.0)[0].tobytes()
        finally:
            renderer.close()
            light.face.cache_clear()

    assert frame(truetype) == frame(plain_truetype)


@pytest.mark.parametrize("style", ["card", "band"])
def test_a_toast_with_latin_text_is_drawn_exactly_as_with_pillows_own_faces(style, monkeypatch):
    from libre_panel.plugins import Toast
    from libre_panel.render import light, overlays
    from libre_panel.render.overlays import ToastLayer
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    def shown(face_maker) -> bytes:
        for module in (light, overlays):
            monkeypatch.setattr(module, "truetype", face_maker)
        light.face.cache_clear()
        toast = {"style": style, "anchor": "bottom", "seconds": 3, "queue": False}
        renderer = Renderer(parse_theme(theme([], (800, 320), toast=toast)), preview=True)
        layer = ToastLayer(renderer)
        try:
            text = "Midnight Drive — The Night Owls, Zürich 21 °C"
            layer.add(
                [Toast(kind="info", level="info", text=text, payload={"label": "Now playing"})]
            )
            layer.advance(0.0)
            return layer.apply(Image.new("RGB", (800, 320), (16, 24, 32)), 1.5).tobytes()
        finally:
            layer.close()
            renderer.close()
            light.face.cache_clear()

    assert shown(truetype) == shown(plain_truetype)


def test_the_band_and_the_card_toast_draw_the_letters_too(use, liberation):
    from libre_panel.plugins import Toast
    from libre_panel.render.overlays import ToastLayer
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    def shown(style: str) -> bytes:
        toast = {"style": style, "anchor": "bottom", "seconds": 3, "queue": False}
        renderer = Renderer(parse_theme(theme([], (800, 320), toast=toast)), preview=True)
        layer = ToastLayer(renderer)
        try:
            layer.add(
                [Toast(kind="info", level="info", text="Привет, мир", payload={"label": "Играет"})]
            )
            layer.advance(0.0)
            return layer.apply(Image.new("RGB", (800, 320), (16, 24, 32)), 1.5).tobytes()
        finally:
            layer.close()
            renderer.close()

    for style in ("card", "band"):
        use(Fallbacks())
        boxes = shown(style)
        use(liberation)
        assert shown(style) != boxes, style


def test_facet_lettering_has_the_letters_of_the_fallback(use, liberation):
    font = truetype(bundled("Barlow-Bold"), 48)
    use(Fallbacks())
    boxes = facets.glyph(font, "Ж", (255, 255, 255, 255))
    use(liberation)
    letter = facets.glyph(font, "Ж", (255, 255, 255, 255))
    assert letter is not None and boxes is not None
    assert letter[0].tobytes() != boxes[0].tobytes()
    assert facets.glyph(font, "Ж", (255, 255, 255, 255))[0] is letter[0]  # (kept)


# -- right to left and letters that join ---------------------------------------------------------


def test_the_scripts_whose_letters_join_are_known():
    assert all(cursive(text) for text in ("مرحبا", "ܐܒܓ", "ߊߋ", "Hello مرحبا", "ﻣﺮﺣﺒﺎ", "٠"))
    assert not any(cursive(text) for text in ("", "Hello", "Привет", "שלום", "中文", "Äpfel – 5 €"))


def test_letters_that_join_are_not_set_one_by_one(use, tmp_path):
    """Letter spacing, equal-width digits and lettering with facets draw one letter at a time:
    letters that join are drawn whole, as they would without them (with libraqm Pillow shapes
    them; a Latin text is the control that the layout is otherwise different)."""
    arabic = make_font(tmp_path / "arabic.ttf", "اب")
    use(Fallbacks([(arabic, None, 0)]))
    text = "ابا 2"
    plain_frame = frame_of({"text": text})
    for spaced in (
        {"letter_spacing": 6},
        {"tabular": True},
        {"letter_spacing": 3, "tabular": True},
    ):
        assert frame_of({"text": text, **spaced}).tobytes() == plain_frame.tobytes()
        assert (
            frame_of({"text": "ABA 2", **spaced}).tobytes() != frame_of({"text": "ABA 2"}).tobytes()
        )
    assert plain_frame.getbbox()


def test_letters_that_join_are_cut_to_the_width_of_the_whole(use, tmp_path):
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    arabic = make_font(tmp_path / "arabic.ttf", "اب")  # 30 px wide each at 30
    use(Fallbacks([(arabic, None, 0)]))
    renderer = Renderer(parse_theme(theme([])), preview=True)
    widget = {"font_size": 30, "max_width": 130, "fit": "ellipsis", "letter_spacing": 10}
    cut, _size = renderer._fit(widget, "اب" * 3)
    assert cut == "ابا…"  # three letters and the ellipsis: the spacing is not counted
    assert renderer._fit(widget, "ABCDEFGH")[0] != "ABCDEFGH"  # (Latin, with it, is cut shorter)
    renderer.close()

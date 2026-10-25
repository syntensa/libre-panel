"""Characters the bundled fonts have no glyph for, drawn with a system font.

Barlow, Liberation Sans and JetBrains Mono carry Latin and little else, so a track title in
Cyrillic, Greek, Chinese, Japanese, Korean, Arabic or Hebrew, or with an emoji, drew as boxes.
:func:`truetype` is ``PIL.ImageFont.truetype`` for a face that draws what its file has no glyph
for with the first system font that has it, at the same size and weight and on the same baseline:
Windows' Segoe UI, Segoe UI Symbol, Yu Gothic, Microsoft YaHei, Malgun Gothic, Nirmala UI, Ebrima
and the like, and on Linux what fontconfig names (``fc-match``) or Noto Sans and DejaVu Sans.

The ideographs are the same characters in Japanese and in Chinese text, but each language draws
them in forms of its own, and a font has one language's. For them the East Asian fonts of the app's
language come first (``scripts`` of :class:`Fallbacks`): Yu Gothic or Noto Sans CJK JP for Japanese,
Microsoft YaHei or Noto Sans CJK SC for Chinese.

What a font covers is read from its ``cmap`` table (no new dependency, and a font file is read once
per process), so text that the face covers, all the Latin the bundled fonts carry, goes straight to
Pillow: it draws exactly as before and costs a check of ``str.isascii``. Right-to-left text is
drawn in logical order; with Pillow's libraqm (Layout.RAQM) it is also shaped and reordered,
run by run.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import struct
import sys
import threading
import unicodedata
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from libre_panel import i18n

log = logging.getLogger(__name__)

# -- what a font file covers ------------------------------------------------------------------

_SFNT = (b"\x00\x01\x00\x00", b"OTTO", b"true")
# (platform, encoding) of the cmap subtables that map Unicode, the widest first
_UNICODE_SUBTABLES = ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0))
_OUTLINES = (b"glyf", b"CFF ", b"CFF2")  # a font of bitmaps only (colour emoji) has none


class Coverage:
    """The code points a font file has glyphs for, and whether it draws outlines at any size."""

    __slots__ = ("_ends", "_starts", "outlines")

    def __init__(self, ranges: Sequence[tuple[int, int]], outlines: bool = True) -> None:
        ranges = sorted(ranges)
        self._starts = [first for first, _ in ranges]
        self._ends = [last for _, last in ranges]
        self.outlines = outlines

    def __contains__(self, ch: object) -> bool:
        code = ord(ch) if isinstance(ch, str) else int(ch)  # type: ignore[call-overload]
        at = bisect_right(self._starts, code) - 1
        return at >= 0 and code <= self._ends[at]

    def __len__(self) -> int:
        return sum(last - first + 1 for first, last in zip(self._starts, self._ends, strict=True))


def _read_cmap(path: str, index: int) -> tuple[bytes, bool] | None:
    """The ``cmap`` table of font ``index`` in a TrueType or OpenType file (or collection) and
    whether the font has outlines; None for any other file."""
    with open(path, "rb") as file:
        head = file.read(12)
        base = 0
        if head[:4] == b"ttcf":  # a collection: the fonts' offsets follow
            count = struct.unpack(">L", head[8:12])[0]
            if not 0 <= index < count:
                return None
            file.seek(12 + 4 * index)
            base = struct.unpack(">L", file.read(4))[0]
            file.seek(base)
            head = file.read(12)
        if head[:4] not in _SFNT:
            return None
        tables = struct.unpack(">H", head[4:6])[0]
        file.seek(base + 12)
        found = {
            tag: (offset, length)
            for tag, _sum, offset, length in struct.iter_unpack(">4sLLL", file.read(16 * tables))
        }
        if b"cmap" not in found:
            return None
        offset, length = found[b"cmap"]
        file.seek(offset)
        return file.read(length), any(tag in found for tag in _OUTLINES)


def _subtable_ranges(table: bytes, at: int) -> list[tuple[int, int]] | None:
    """The code points one cmap subtable maps to a glyph (not to glyph 0), as ranges."""
    form = struct.unpack_from(">H", table, at)[0]
    ranges: list[tuple[int, int]] = []
    if form == 4:  # segments of the Basic Multilingual Plane
        count = struct.unpack_from(">H", table, at + 6)[0] // 2
        ends = struct.unpack_from(f">{count}H", table, at + 14)
        starts = struct.unpack_from(f">{count}H", table, at + 16 + 2 * count)
        offsets_at = at + 16 + 6 * count
        offsets = struct.unpack_from(f">{count}H", table, offsets_at)
        for n, (first, last) in enumerate(zip(starts, ends, strict=True)):
            if first > last or first == 0xFFFF:
                continue
            if not offsets[n]:  # the glyph follows from the code: all of them
                ranges.append((first, last))
                continue
            glyphs_at = offsets_at + 2 * n + offsets[n]  # an array of glyphs, some of them 0
            room = max(0, min(last - first + 1, (len(table) - glyphs_at) // 2))
            glyphs = struct.unpack_from(f">{room}H", table, glyphs_at)
            begin = None
            for k, glyph in enumerate(glyphs):
                if glyph and begin is None:
                    begin = first + k
                elif not glyph and begin is not None:
                    ranges.append((begin, first + k - 1))
                    begin = None
            if begin is not None:
                ranges.append((begin, first + len(glyphs) - 1))
    elif form in (12, 13):  # groups of code points
        count = struct.unpack_from(">L", table, at + 12)[0]
        data = table[at + 16 : at + 16 + 12 * count]
        ranges = [(first, last) for first, last, _glyph in struct.iter_unpack(">3L", data)]
    elif form == 6:  # a trimmed table
        first, count = struct.unpack_from(">HH", table, at + 6)
        glyphs = struct.unpack_from(f">{count}H", table, at + 10)
        ranges = [(first + k, first + k) for k, glyph in enumerate(glyphs) if glyph]
    elif form == 10:
        first, count = struct.unpack_from(">LL", table, at + 12)
        glyphs = struct.unpack_from(f">{count}H", table, at + 20)
        ranges = [(first + k, first + k) for k, glyph in enumerate(glyphs) if glyph]
    elif form == 0:  # one byte per code
        glyphs = table[at + 6 : at + 262]
        ranges = [(code, code) for code, glyph in enumerate(glyphs) if glyph]
    else:
        return None
    return ranges


def _unicode_ranges(table: bytes) -> list[tuple[int, int]] | None:
    count = struct.unpack_from(">H", table, 2)[0]
    subtables = {}
    for n in range(count):
        platform, encoding, at = struct.unpack_from(">HHL", table, 4 + 8 * n)
        subtables[(platform, encoding)] = at
    for key in _UNICODE_SUBTABLES:
        if key in subtables:
            ranges = _subtable_ranges(table, subtables[key])
            if ranges is not None:
                return ranges
    return None


@lru_cache(maxsize=128)
def coverage(path: str, index: int = 0) -> Coverage | None:
    """What font ``index`` of the file at ``path`` covers; None where that cannot be told (not a
    TrueType or OpenType font, no Unicode table, unreadable)."""
    try:
        read = _read_cmap(path, index)
        ranges = _unicode_ranges(read[0]) if read else None
    except (OSError, struct.error, ValueError, IndexError):
        return None
    return Coverage(ranges, read[1]) if read and ranges is not None else None


# -- system fonts ------------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _opens(path: str, index: int) -> bool:
    """Whether FreeType makes a face of the font at a size we ask for: a colour emoji font of
    bitmaps (Noto Color Emoji) has one size only, and the face of any other would be boxes."""
    try:
        ImageFont.truetype(path, 16, index)
    except (OSError, ValueError):
        return False
    return True


# Regular file, bold file and index in the file of the system fonts that fill the gaps of the
# bundled ones, in the order they are tried. Windows: its font folder; Segoe UI and Segoe UI
# Symbol cover most scripts and symbols, the East Asian fonts the rest of the ideographs.
_WINDOWS = (
    ("segoeui.ttf", "segoeuib.ttf", 0),  # Latin, Greek, Cyrillic, Hebrew, Arabic, Armenian ...
    ("seguisym.ttf", None, 0),  # symbols, arrows, mathematics, dingbats
    ("YuGothR.ttc", "YuGothB.ttc", 0),  # Japanese
    ("meiryo.ttc", "meiryob.ttc", 0),
    ("msyh.ttc", "msyhbd.ttc", 0),  # Simplified Chinese
    ("msjh.ttc", "msjhbd.ttc", 0),  # Traditional Chinese
    ("malgun.ttf", "malgunbd.ttf", 0),  # Korean
    ("Nirmala.ttc", "NirmalaB.ttc", 0),  # Indic scripts
    ("Nirmala.ttf", "NirmalaB.ttf", 0),
    ("ebrima.ttf", "ebrimabd.ttf", 0),  # African scripts
    ("LeelawUI.ttf", "LeelaUIb.ttf", 0),  # Thai, Lao, Khmer
    ("seguiemj.ttf", None, 0),  # emoji: the outlines under its colours, monochrome
    ("seguihis.ttf", None, 0),  # historic scripts
    ("mmrtext.ttf", "mmrtextb.ttf", 0),  # Myanmar
    ("gadugi.ttf", "gadugib.ttf", 0),  # Cherokee, Canadian syllabics
    ("simsun.ttc", None, 0),
    ("msgothic.ttc", None, 0),
    ("arial.ttf", "arialbd.ttf", 0),
)

# The East Asian fonts of a language, tried first for the characters whose forms depend on it (the
# language of the app: ja, zh): the same entries as the tables, by language.
_WINDOWS_EAST_ASIAN = {
    "ja": (
        ("YuGothR.ttc", "YuGothB.ttc", 0),
        ("meiryo.ttc", "meiryob.ttc", 0),
        ("msgothic.ttc", None, 0),
    ),
    "zh": (
        ("msyh.ttc", "msyhbd.ttc", 0),
        ("msjh.ttc", "msjhbd.ttc", 0),
        ("simsun.ttc", None, 0),
    ),
}

# Linux (and anything else with fonts in files): the usual places of Noto Sans and DejaVu Sans, for
# where fontconfig is not installed; with it, ``fc-match`` finds the rest.
_FOLDERS = (
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/noto",
    "/usr/share/fonts/google-noto",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/noto-cjk",
    "/usr/share/fonts/google-noto-cjk",
    "/usr/share/fonts/truetype/wqy",
    "/usr/share/fonts/opentype/ipafont-gothic",  # Japanese: IPAGothic, Takao and VL Gothic
    "/usr/share/fonts/ipa-gothic",
    "/usr/share/fonts/OTF",
    "/usr/share/fonts/truetype/takao-gothic",
    "/usr/share/fonts/truetype/vlgothic",
    "/usr/share/fonts/vlgothic",
)
_POSIX = (
    ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf", 0),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", 0),
    ("NotoSansArabic-Regular.ttf", "NotoSansArabic-Bold.ttf", 0),
    ("NotoSansHebrew-Regular.ttf", "NotoSansHebrew-Bold.ttf", 0),
    ("NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc", 0),
    ("wqy-zenhei.ttc", None, 0),
    ("wqy-microhei.ttc", None, 0),
    ("NotoSansSymbols-Regular.ttf", None, 0),
    ("NotoSansSymbols2-Regular.ttf", None, 0),
    ("NotoEmoji-Regular.ttf", None, 0),
    ("ipag.ttf", None, 0),  # Japanese, for where no font above has the ideographs
    ("TakaoGothic.ttf", None, 0),
    ("VL-Gothic-Regular.ttf", None, 0),
)
# Noto Sans CJK is one collection of five faces: Japanese, Korean, Simplified and Traditional
# Chinese, Hong Kong (in this order), or a file for each of them.
_POSIX_EAST_ASIAN = {
    "ja": (
        ("NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc", 0),
        ("NotoSansCJKjp-Regular.otf", "NotoSansCJKjp-Bold.otf", 0),
        ("ipag.ttf", None, 0),
        ("ipagp.ttf", None, 0),
        ("TakaoGothic.ttf", None, 0),
        ("VL-Gothic-Regular.ttf", None, 0),
    ),
    "zh": (
        ("NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc", 2),
        ("NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Bold.otf", 0),
        ("wqy-zenhei.ttc", None, 0),
        ("wqy-microhei.ttc", None, 0),
    ),
}
# fontconfig's name of the languages that have forms of their own
_FONTCONFIG_LANGUAGES = {"ja": "ja", "zh": "zh-cn"}


def east_asian(ch: str) -> bool:
    """Whether the forms of ``ch`` depend on the language: the ideographs, the kana, the symbols
    and punctuation that go with them and the full-width forms."""
    code = ord(ch)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x3FFFF
    )


_SERIALS = itertools.count(1)
QUERIES = 48  # questions to fontconfig in a process; most texts need none


class Fallbacks:
    """Where to find a glyph a font lacks: system font files tried in order, then ``query`` (a
    function ``(ch, bold) -> [(path, index), ...]``, fontconfig's), which is asked for a character
    no listed font has, a few times at most. Answers are kept: a character is looked for once.

    ``scripts``: by language, the fonts that come first for the characters whose forms depend on
    it (:func:`east_asian`) while ``language()`` (the app's) is that language; the others as
    always. A language the app is switched to later is taken in at the next text."""

    def __init__(
        self,
        fonts: Iterable[tuple[str | os.PathLike, str | os.PathLike | None, int]] = (),
        query: Callable[[str, bool], Iterable[tuple[str, int]]] | None = None,
        language: Callable[[], str] | None = None,
        scripts: Mapping[str, Iterable[tuple[str | os.PathLike, str | os.PathLike | None, int]]]
        | None = None,
    ) -> None:
        self.fonts = self._listed(fonts)
        self.scripts = {name: self._listed(first) for name, first in (scripts or {}).items()}
        self.query = query
        self.language = language
        self.serial = next(_SERIALS)  # tells one set from another (what is cached by font)
        self._learned: dict[tuple[bool, str], list[tuple[str, int]]] = {}
        self._found: dict[tuple[str, bool, str], tuple[str, int] | None] = {}
        self._asked = 0
        self._lock = threading.Lock()

    @staticmethod
    def _listed(
        fonts: Iterable[tuple[str | os.PathLike, str | os.PathLike | None, int]],
    ) -> list[tuple[str, str | None, int]]:
        return [(os.fspath(a), os.fspath(b) if b else None, i) for a, b, i in fonts]

    def variant(self) -> str:
        """The language whose East Asian fonts come first now ("": the usual order)."""
        if self.language is None:
            return ""
        try:
            language = self.language()
        except Exception:  # (the order of the fonts is not worth a broken panel)
            return ""
        return language if language in self.scripts else ""

    def find(self, ch: str, bold: bool = False) -> tuple[str, int] | None:
        """The file and the index in it of the first font that has a glyph for ``ch``."""
        key = (ch, bold, self.variant() if east_asian(ch) else "")
        try:
            return self._found[key]
        except KeyError:
            pass
        with self._lock:
            if key not in self._found:
                self._found[key] = self._search(ord(ch), bold, key[2])
        return self._found[key]

    def _search(self, code: int, bold: bool, variant: str) -> tuple[str, int] | None:
        first = self.scripts.get(variant, [])
        fonts = [*first, *(font for font in self.fonts if font not in first)]
        learned = self._learned.setdefault((bold, variant), [])
        listed = ((b if bold and b else a, i) for a, b, i in fonts)
        for path, index in (*listed, *learned):
            if self._has(code, path, index):
                return path, index
        if self.query is None or self._asked >= QUERIES:
            return None
        self._asked += 1
        try:
            answers = list(self.query(chr(code), bold))
        except Exception:  # a broken tool must not blank the panel's text
            log.debug("the font query for U+%04X failed", code, exc_info=True)
            return None
        for path, index in answers:
            if self._has(code, path, index):
                learned.append((path, index))  # the next character may be in it too
                return path, index
        return None

    @staticmethod
    def _has(code: int, path: str, index: int) -> bool:
        found = coverage(path, index)
        return found is not None and found.outlines and code in found and _opens(path, index)


def _fontconfig(ch: str, bold: bool) -> list[tuple[str, int]]:
    """The fonts fontconfig would take for ``ch``, best first (``fc-match -s``: the ones that have
    it come first, those made for the app's language among them for an ideograph)."""
    import shutil
    import subprocess

    program = shutil.which("fc-match")
    if program is None:
        return []
    language = _FONTCONFIG_LANGUAGES.get(i18n.language()) if east_asian(ch) else None
    pattern = (
        f":charset={ord(ch):x}"
        + (f":lang={language}" if language else "")
        + (":weight=bold" if bold else "")
    )
    run = subprocess.run(
        [program, "-s", "-f", "%{file}\t%{index}\n", pattern],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    found = []
    for line in run.stdout.splitlines()[:12]:
        path, _, index = line.partition("\t")
        if path and index.isdigit():
            found.append((path, int(index)))
    return found


def discover(platform: str, environ: Mapping[str, str]) -> Fallbacks:
    """The system fonts to fall back to on ``platform`` (``sys.platform``): those of the table
    that exist, and on a system with font files in folders, fontconfig. The East Asian fonts of
    the app's language come first for the characters whose forms depend on it."""
    if platform == "win32":
        root = environ.get("WINDIR") or environ.get("SystemRoot") or "C:\\Windows"
        folders = [Path(root) / "Fonts"]
        table, query, east_asian_tables = _WINDOWS, None, _WINDOWS_EAST_ASIAN
    else:
        folders = [Path(folder) for folder in _FOLDERS]
        table, query, east_asian_tables = _POSIX, _fontconfig, _POSIX_EAST_ASIAN

    def installed(
        entries: Iterable[tuple[str, str | None, int]],
    ) -> list[tuple[Path, Path | None, int]]:
        found = []
        for regular, bold, index in entries:
            for folder in folders:
                if (folder / regular).is_file():
                    heavy = folder / bold if bold and (folder / bold).is_file() else None
                    found.append((folder / regular, heavy, index))
                    break
        return found

    scripts = {name: installed(entries) for name, entries in east_asian_tables.items()}
    return Fallbacks(installed(table), query, i18n.language, scripts)


_system: Fallbacks | None = None
_swaps = 0  # how often a test set the fonts to fall back to
_system_lock = threading.Lock()


def system_fallbacks() -> Fallbacks:
    """The system's fonts to fall back to, found once."""
    global _system
    if _system is None:
        with _system_lock:
            if _system is None:
                _system = discover(sys.platform, os.environ)
    return _system


def set_fallbacks(fallbacks: Fallbacks | None) -> Fallbacks | None:
    """Fall back to ``fallbacks`` from now on (None: look for the system's again); returns the
    ones used before. For tests: a face follows it unless it was given a set of its own."""
    global _system, _swaps
    with _system_lock:
        before, _system = _system, fallbacks
        _swaps += 1
    return before


# -- the face ------------------------------------------------------------------------------------

PLANS = 256  # texts whose runs a face keeps
# marks, format characters, spaces and controls go with the character before them
_FOLLOWS = frozenset({"Mn", "Mc", "Me", "Cf", "Zs", "Zl", "Zp", "Cc"})
_ANCHORS_H, _ANCHORS_V = "lmr", "amsd"  # what a line of horizontal text can be anchored at
_UNSET: Any = object()


# Blocks of scripts whose letters join (Arabic, Syriac, N'Ko, Mandaic, Mongolian, Adlam): the
# letters take their shape from their neighbours, so a text of them is drawn whole or not at all
_CURSIVE = (
    (0x0600, 0x077F),  # Arabic, Syriac, Arabic Supplement
    (0x07C0, 0x07FF),
    (0x0840, 0x085F),
    (0x08A0, 0x08FF),
    (0x1800, 0x18AF),
    (0xFB50, 0xFDFF),  # Arabic presentation forms
    (0xFE70, 0xFEFF),
    (0x1E900, 0x1E95F),
)


def cursive(text: str) -> bool:
    """Whether ``text`` has letters of a script that joins them. Spread out letter by letter
    (letter spacing, lettering with facets) they could not join: such text is laid out whole,
    and with libraqm Pillow shapes it, else it shows the letters apart, in the order written."""
    return not text.isascii() and any(
        first <= ord(ch) <= last for ch in text for first, last in _CURSIVE
    )


def _has(found: Coverage | None, ch: str, shaped: bool = False) -> bool:
    """Whether a face covers ``ch``; one whose coverage is unknown covers everything (as before).
    ``shaped``: libraqm lays out the text and builds a letter from its parts (an accent) when the
    font has the parts."""
    if found is None or ch in found:
        return True
    if shaped:
        parts = unicodedata.normalize("NFD", ch)
        return len(parts) > 1 and all(part in found for part in parts)
    return False


def _edged(runs: list[list[Any]]) -> tuple[tuple[Any, str], ...]:
    """``(spec, run)`` of the runs, the spaces at the edges of a run of another font drawn with
    the font itself: with libraqm a space at the end of a right-to-left run would lie at its left,
    against the run before, and the spaces of a font are the width of its own."""
    out: list[tuple[Any, str]] = []

    def put(spec: Any, text: str) -> None:
        if text:
            out.append((spec, out.pop()[1] + text) if out and out[-1][0] == spec else (spec, text))

    for spec, _found, chars in runs:
        run = "".join(chars)
        body = run.strip()
        if spec is None or not body:
            put(None, run)
            continue
        lead = len(run) - len(run.lstrip())
        put(None, run[:lead])
        put(spec, body)
        put(None, run[lead + len(body) :])
    return tuple(out)


class FallbackFont(ImageFont.FreeTypeFont):
    """A FreeType face that draws a character it has no glyph for with a system font.

    A text made only of characters the face has goes to Pillow as it is. Any other is cut into
    runs by the face that has their glyphs; the runs share the baseline of this face, the size and
    (bold or not) the weight. Marks, spaces and the invisible go with the character before them.
    ``getlength``, ``getbbox`` and ``getmask2`` know the runs, so every way Pillow draws and
    measures text (``ImageDraw.text``, ``textbbox``, ``textlength``, anchors) comes out right.
    The fonts are loaded for this face alone: FreeType faces are not shared between threads.
    """

    def __init__(self, *args: Any, chain: Fallbacks | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._start(chain)

    def _start(self, chain: Fallbacks | None = None) -> None:
        self._chain = chain
        self._seen: Fallbacks | None = None
        self._variant = ""
        self._plans: dict[str, tuple[tuple[Any, str], ...] | None] = {}
        self._faces: dict[Any, Any] = {}
        self._drops: dict[str, int] = {}
        self._own: Any = _UNSET
        self._heavy: bool | None = None

    @property
    def chain_id(self) -> int:
        """Which set of fonts it falls back to, for what is cached by face: 0 for the system's,
        which never changes in a run (``set_fallbacks`` is for tests)."""
        return self._chain.serial if self._chain is not None else -_swaps

    # -- which face draws what

    def _coverage(self) -> Coverage | None:
        if self._own is _UNSET:
            path = self.path
            usable = isinstance(path, (str, os.PathLike))
            self._own = coverage(os.fspath(path), self.index) if usable else None
        return self._own

    def _bold(self) -> bool:
        if self._heavy is None:
            try:
                style = self.getname()[1] or ""
            except Exception:
                style = ""
            name = f"{style} {Path(str(self.path)).stem}".lower()
            self._heavy = any(word in name for word in ("bold", "black", "heavy"))
        return self._heavy

    def _face(self, spec: tuple[str, int] | None) -> Any:
        """The plain face for a font of the chain (None: this font's own file), made once."""
        faces = self._faces
        if spec not in faces:
            path, index = (self.path, self.index) if spec is None else spec
            try:
                faces[spec] = ImageFont.truetype(
                    path, self.size, index, self.encoding, layout_engine=self.layout_engine
                )
            except (OSError, ValueError):  # a font that cannot be opened is not one to fall back to
                faces[spec] = None
        return faces[spec]

    def _plan(self, text: str) -> tuple[tuple[Any, str], ...] | None:
        """``text`` in runs, ``(spec, run)`` (spec None: the font itself); None where the font
        has all of it."""
        chain = self._chain or system_fallbacks()
        variant = chain.variant()
        if chain is not self._seen:  # (the set of fonts changed: tests)
            self._plans.clear()
            self._faces.clear()
            self._seen = chain
        elif variant != self._variant:  # (the app's language was changed: other forms first)
            self._plans.clear()
        self._variant = variant
        plans = self._plans
        if text in plans:
            return plans[text]
        plan = self._split(text, chain)
        if len(plans) >= PLANS:
            plans.clear()
        plans[text] = plan
        return plan

    def _split(self, text: str, chain: Fallbacks) -> tuple[tuple[Any, str], ...] | None:
        own = self._coverage()
        if own is None or self._face(None) is None:
            return None
        shaped = self.layout_engine == ImageFont.Layout.RAQM
        bold = self._bold()
        runs: list[list[Any]] = []  # [spec, its coverage, characters]
        for ch in text:
            kind = unicodedata.category(ch)
            if kind in _FOLLOWS:
                if not runs:
                    runs.append([None, own, []])
                spec, found, chars = runs[-1]
                if _has(found, ch, shaped and spec is None):
                    chars.append(ch)
                elif kind == "Zs":  # a space the face has not: its plain space
                    chars.append(" ")
                elif kind in ("Cc", "Zl", "Zp"):
                    chars.append(ch)
                continue  # (a mark or an invisible one that the face has not cannot be drawn)
            spec, found = None, own
            if not _has(own, ch, shaped):
                where = chain.find(ch, bold)
                if where is not None and self._face(where) is not None:
                    spec, found = where, coverage(*where)
            if runs and runs[-1][0] == spec:
                runs[-1][2].append(ch)
            else:
                runs.append([spec, found, [ch]])
        plan = _edged(runs) or ((None, ""),)  # (all of it left out: invisible characters)
        return None if plan == ((None, text),) else plan

    def _runs(self, text: Any, direction: str | None) -> tuple[tuple[Any, str], ...] | None:
        if direction == "ttb" or not isinstance(text, str):
            return None
        return self._plan(text)

    # -- laying the runs out

    def _drop(self, vertical: str) -> int:
        """How far below an anchor of that vertical kind the baseline lies, as Pillow places this
        face: every run goes on that baseline, whatever its own font's ascent is."""
        if vertical == "s":
            return 0
        if vertical not in self._drops:

            def top(anchor: str) -> float:
                return ImageFont.FreeTypeFont.getbbox(self, "H", "", None, None, None, 0, anchor)[1]

            self._drops[vertical] = round(top("l" + vertical) - top("ls"))
        return self._drops[vertical]

    def _place(
        self,
        plan: tuple[tuple[Any, str], ...],
        mode: str,
        direction: str | None,
        features: list[str] | None,
        language: str | None,
        anchor: str | None,
    ) -> tuple[list[tuple[Any, str, float]], int, str] | None:
        """Where each run starts, from the anchor (``[(face, run, x)]``), how far below it the
        baseline is and the horizontal anchor; None for an anchor a horizontal text has not."""
        anchor = anchor or "la"
        if len(anchor) != 2 or anchor[0] not in _ANCHORS_H or anchor[1] not in _ANCHORS_V:
            return None
        faces = [self._face(spec) for spec, _ in plan]
        widths = [
            face.getlength(run, mode, direction, features, language)
            for face, (_, run) in zip(faces, plan, strict=True)
        ]
        units = round(sum(widths) * 64)  # (1/64 px, as FreeType counts; Pillow rounds the shift)
        x = {"l": 0, "m": -((units // 2 + 32) >> 6), "r": -((units + 32) >> 6)}[anchor[0]]
        placed = []
        for face, (_, run), width in zip(faces, plan, widths, strict=True):
            placed.append((face, run, x))
            x += width
        return placed, self._drop(anchor[1]), anchor[0]

    # -- what Pillow asks of a font

    def getlength(
        self,
        text: Any,
        mode: str = "",
        direction: str | None = None,
        features: list[str] | None = None,
        language: str | None = None,
    ) -> float:
        plan = None if text.isascii() else self._runs(text, direction)
        if plan is None:
            return super().getlength(text, mode, direction, features, language)
        return sum(
            self._face(spec).getlength(run, mode, direction, features, language)
            for spec, run in plan
        )

    def getbbox(
        self,
        text: Any,
        mode: str = "",
        direction: str | None = None,
        features: list[str] | None = None,
        language: str | None = None,
        stroke_width: float = 0,
        anchor: str | None = None,
    ) -> tuple[float, float, float, float]:
        plan = None if text.isascii() else self._runs(text, direction)
        placed = plan and self._place(plan, mode, direction, features, language, anchor)
        if not placed:
            return super().getbbox(text, mode, direction, features, language, stroke_width, anchor)
        runs, drop, _horizontal = placed
        found = self._extent(runs, drop, mode, direction, features, language, stroke_width)
        if found is None:
            return (0, drop, 0, drop)
        left, top, right, bottom = found
        return (math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom))

    @staticmethod
    def _extent(
        runs: list[tuple[Any, str, float]],
        drop: int,
        mode: str,
        direction: str | None,
        features: list[str] | None,
        language: str | None,
        stroke_width: float,
    ) -> tuple[float, float, float, float] | None:
        """The box all the runs' ink lies in, from the anchor; None for no ink (spaces)."""
        found = None
        for face, run, x in runs:
            left, top, right, bottom = face.getbbox(
                run, mode, direction, features, language, stroke_width, "ls"
            )
            if right <= left or bottom <= top:
                continue
            box = (x + left, drop + top, x + right, drop + bottom)
            found = box if found is None else (
                min(found[0], box[0]), min(found[1], box[1]),
                max(found[2], box[2]), max(found[3], box[3]),
            )  # fmt: skip
        return found

    def getmask2(
        self,
        text: Any,
        mode: str = "",
        direction: str | None = None,
        features: list[str] | None = None,
        language: str | None = None,
        stroke_width: float = 0,
        anchor: str | None = None,
        ink: int = 0,
        start: tuple[float, float] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, tuple[int, int]]:
        plan = None if text.isascii() or mode not in ("", "L", "1") else self._runs(text, direction)
        placed = plan and self._place(plan, mode, direction, features, language, anchor)
        if not placed:
            return super().getmask2(
                text, mode, direction, features, language, stroke_width, anchor, ink, start,
                *args, **kwargs,
            )  # fmt: skip
        runs, drop, horizontal = placed
        if len(runs) == 1:  # one face: Pillow places it on the baseline, we move it to ours
            face, run, _x = runs[0]
            mask, (x, y) = face.getmask2(
                run, mode, direction, features, language, stroke_width, horizontal + "s", ink,
                start, *args, **kwargs,
            )  # fmt: skip
            return mask, (x, y + drop)
        return self._compose(runs, drop, mode, direction, features, language, stroke_width, start)

    def _compose(
        self,
        runs: list[tuple[Any, str, float]],
        drop: int,
        mode: str,
        direction: str | None,
        features: list[str] | None,
        language: str | None,
        stroke_width: float,
        start: tuple[float, float] | None,
    ) -> tuple[Any, tuple[int, int]]:
        """Several faces' runs in one mask, each drawn by Pillow at its place on the baseline."""
        fx, fy = start or (0.0, 0.0)
        found = self._extent(runs, drop, mode, direction, features, language, stroke_width)
        if found is None:  # nothing to see
            return Image.new("L", (1, 1)).im, (0, 0)
        x0, y0 = math.floor(found[0] + min(fx, 0)), math.floor(found[1] + min(fy, 0))
        width = math.ceil(found[2] + max(fx, 0)) - x0 + 2  # (a pixel more for the fraction)
        height = math.ceil(found[3] + max(fy, 0)) - y0 + 2
        canvas = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(canvas)
        if mode == "1":
            draw.fontmode = "1"
        for face, run, x in runs:
            draw.text(
                (x + fx - x0, drop + fy - y0), run, fill=255, font=face, anchor="ls",
                direction=direction, features=features, language=language,
                stroke_width=stroke_width,
            )  # fmt: skip
        return canvas.im, (x0, y0)


def truetype(
    font: str | os.PathLike, size: float = 10, index: int = 0, **options: Any
) -> FallbackFont:
    """``PIL.ImageFont.truetype`` for a face that draws what its file has no glyph for with a system
    font. Pillow's own function makes the face, so whatever replaces it (a tool that forces the
    basic layout) goes on to hold."""
    face = ImageFont.truetype(os.fspath(font), size, index, **options)
    if type(face) is ImageFont.FreeTypeFont:
        face.__class__ = FallbackFont
        face._start()  # type: ignore[attr-defined]
    return face  # type: ignore[return-value]

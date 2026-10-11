"""TrueType fonts made on the spot, for tests that need a font with exactly the characters they say
(CJK, emoji, Arabic: none of the bundled fonts has them, and what a machine has differs)."""

from __future__ import annotations

import struct
from pathlib import Path

UPEM = 1000


def _glyph(box: tuple[int, int, int, int]) -> bytes:
    """A filled rectangle: one contour of four points."""
    x0, y0, x1, y1 = box
    data = struct.pack(">hhhhh", 1, x0, y0, x1, y1) + struct.pack(">HH", 3, 0)
    data += bytes([1, 1, 1, 1])  # on the curve, every coordinate a 16 bit step
    data += struct.pack(">4h", x0, 0, x1 - x0, 0)
    data += struct.pack(">4h", y0, y1 - y0, 0, y0 - y1)
    return data + b"\0" * (-len(data) % 4)


def _cmap(codes: list[int]) -> bytes:
    """A cmap table with the glyph n + 1 for ``codes[n]``: format 4 for those of the Basic
    Multilingual Plane, and format 12 for all."""
    glyphs = {code: n + 1 for n, code in enumerate(codes)}
    narrow = sorted(c for c in codes if c < 0xFFFF)
    count = len(narrow) + 1  # (and the last segment)
    ends = [*narrow, 0xFFFF]
    deltas = [(glyphs[c] - c) % 65536 for c in narrow] + [1]
    format4 = struct.pack(">7H", 4, 16 + 8 * count, 0, 2 * count, 0, 0, 0)
    format4 += struct.pack(f">{count}H", *ends) + b"\0\0" + struct.pack(f">{count}H", *ends)
    format4 += struct.pack(f">{count}H", *deltas) + b"\0\0" * count
    groups = b"".join(struct.pack(">LLL", c, c, glyphs[c]) for c in sorted(codes))
    format12 = struct.pack(">HHLLL", 12, 0, 16 + len(groups), 0, len(codes)) + groups
    table = struct.pack(">HH", 0, 2)
    table += struct.pack(">HHL", 3, 1, 4 + 16) + struct.pack(">HHL", 3, 10, 4 + 16 + len(format4))
    return table + format4 + format12


def _tables(codes: list[int], box: tuple[int, int, int, int], advance: int, outlines: bool):
    count = len(codes) + 1
    shapes = [b""] + [_glyph(box)] * len(codes)  # (glyph 0, the missing one, is empty)
    glyf = b"".join(shapes)
    loca, at = [], 0
    for shape in shapes:
        loca.append(at // 2)
        at += len(shape)
    loca.append(at // 2)
    head = struct.pack(">LLLLHHqqhhhhHHhhh", 0x10000, 0x10000, 0, 0x5F0F3CF5, 3, UPEM, 0, 0,
                       box[0], box[1], box[2], box[3], 0, 8, 2, 0, 0)  # fmt: skip
    hhea = struct.pack(">LhhhHhhhhhh", 0x10000, 800, -200, 0, advance, box[0], 0, box[2], 1, 0, 0)
    hhea += b"\0" * 8 + struct.pack(">hH", 0, count)
    maxp = struct.pack(">LHHHHHHHHHHHHHH", 0x10000, count, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    tables = {
        b"head": head,
        b"hhea": hhea,
        b"maxp": maxp,
        b"hmtx": b"".join(struct.pack(">Hh", advance, box[0]) for _ in range(count)),
        b"cmap": _cmap(codes),
    }
    if outlines:
        tables[b"glyf"] = glyf
        tables[b"loca"] = struct.pack(f">{len(loca)}H", *loca)
    else:  # only bitmaps, as a colour emoji font has
        tables[b"CBDT"] = b"\0\3\0\0"
    return tables


def _sfnt(tables: dict[bytes, bytes], at: int) -> bytes:
    """The font file's part for ``tables``, placed ``at`` bytes into the file."""
    count = len(tables)
    top = 1 << (count.bit_length() - 1)
    head = struct.pack(
        ">LHHHH", 0x10000, count, top * 16, count.bit_length() - 1, count * 16 - top * 16
    )
    offset = at + 12 + 16 * count
    directory, body = b"", b""
    for tag in sorted(tables):
        data = tables[tag]
        directory += struct.pack(">4sLLL", tag, 0, offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    return head + directory + body


def make_font(
    path: Path,
    chars: str,
    box: tuple[int, int, int, int] = (100, 0, 900, 700),
    advance: int = 1000,
    outlines: bool = True,
) -> Path:
    """A font file at ``path`` whose glyph for each of ``chars`` is a filled box (``box`` in
    1000 units to the em, ``advance`` wide); the other characters have no glyph."""
    path.write_bytes(_sfnt(_tables([ord(c) for c in chars], box, advance, outlines), 0))
    return path


def make_collection(path: Path, *fonts: str) -> Path:
    """A font collection at ``path``: font n has the characters ``fonts[n]``."""
    made = [_tables([ord(c) for c in chars], (100, 0, 900, 700), 1000, True) for chars in fonts]
    head = struct.pack(">4sLL", b"ttcf", 0x10000, len(fonts))
    at = len(head) + 4 * len(fonts)
    offsets, body = [], b""
    for tables in made:
        offsets.append(at + len(body))
        body += _sfnt(tables, at + len(body))
    path.write_bytes(head + struct.pack(f">{len(fonts)}L", *offsets) + body)
    return path

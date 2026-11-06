"""rust/decode against Pillow (docs/RUST.md, stage R1): pictures decoded from their files as the
app opens them, ImageOps.exif_transpose(Image.open(file)).convert(mode), byte for byte."""

from __future__ import annotations

import base64
import io
import random
import struct
import zlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from rust_check import picture, run

ROOT = Path(__file__).resolve().parent.parent
ERROR = object()

# (bit depth, colour type) as PNG allows them, and the samples of a pixel
PNG_KINDS = [(1, 0), (2, 0), (4, 0), (8, 0), (16, 0), (8, 2), (16, 2), (1, 3), (2, 3), (4, 3),
             (8, 3), (8, 4), (16, 4), (8, 6), (16, 6)]  # fmt: skip
SAMPLES = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ADAM7 = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
         (0, 1, 1, 2)]  # fmt: skip


def chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _pack(pixels: list[tuple[int, ...]], depth: int) -> bytes:
    """A row's samples as PNG packs them (below 8 bits the first in the high bits)."""
    samples = [s for px in pixels for s in px]
    if depth == 8:
        return bytes(samples)
    if depth == 16:
        return b"".join(struct.pack(">H", s) for s in samples)
    out, per = bytearray(), 8 // depth
    for i in range(0, len(samples), per):
        byte = 0
        for j, s in enumerate(samples[i : i + per]):
            byte |= s << (8 - depth * (j + 1))
        out.append(byte)
    return bytes(out)


def _filtered(rows: list[bytes], bpp: int, rng: random.Random) -> bytes:
    """The rows each with a filter of its own (0 to 4), as an encoder may choose them."""
    out, prior = bytearray(), bytes(len(rows[0]) if rows else 0)
    for row in rows:
        kind = rng.randrange(5)
        line = bytearray()
        for i, x in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            b = prior[i]
            c = prior[i - bpp] if i >= bpp else 0
            if kind == 0:
                v = x
            elif kind == 1:
                v = x - a
            elif kind == 2:
                v = x - b
            elif kind == 3:
                v = x - (a + b) // 2
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                v = x - (a if pa <= pb and pa <= pc else b if pb <= pc else c)
            line.append(v & 255)
        out += bytes([kind]) + line
        prior = row
    return bytes(out)


def make_png(rng: random.Random, depth: int, colour: int, w: int, h: int, *, interlace: bool,
             before: list[bytes], after: list[bytes]) -> bytes:  # fmt: skip
    """A PNG of random samples, its palette, transparency and the chunks given."""
    n = SAMPLES[colour]
    top = (1 << depth) - 1
    pixels = [[tuple(rng.choice([0, top, rng.randint(0, top)]) for _ in range(n))
               for _ in range(w)] for _ in range(h)]  # fmt: skip
    bpp = max(1, depth * n // 8)
    if interlace:
        data = b""
        for x0, y0, dx, dy in ADAM7:
            rows = [_pack(pixels[y][x0::dx], depth) for y in range(y0, h, dy) if x0 < w]
            if rows and rows[0]:
                data += _filtered(rows, bpp, rng)
    else:
        data = _filtered([_pack(row, depth) for row in pixels], bpp, rng)
    head = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, int(interlace)))
    extra = []
    if colour == 3:
        entries = rng.randint(1, min(256, 1 << depth))
        extra.append(chunk(b"PLTE", bytes(rng.randrange(256) for _ in range(entries * 3))))
    if colour in (0, 2, 3) and rng.random() < 0.6:
        if colour == 3:
            # (now and then more entries than a palette has, which Pillow refuses for RGBA)
            k = rng.randint(250, 300) if rng.random() < 0.1 else rng.randint(1, 20)
            if rng.random() < 0.5:  # one clear index among opaque ones
                trns = bytearray([255] * k)
                trns[rng.randrange(k)] = 0
            else:
                trns = bytearray(rng.randrange(256) for _ in range(k))
        else:
            px = pixels[rng.randrange(h)][rng.randrange(w)]  # (often a pixel's own colour)
            values = px if rng.random() < 0.7 else tuple(rng.randint(0, top) for _ in px)
            if rng.random() < 0.1:  # (beyond the bit depth: only its low byte counts)
                values = tuple(v | 0x0100 for v in values)
            trns = bytearray(b"".join(struct.pack(">H", v) for v in values))
        extra.append(chunk(b"tRNS", bytes(trns)))
    body = chunk(b"IDAT", zlib.compress(data, rng.randint(0, 9)))
    end = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + head + b"".join(before) + b"".join(extra) + body + \
        b"".join(after) + end  # fmt: skip


def _exif(orientation: int, big: bool) -> bytes:
    """EXIF data of one Orientation tag, as cameras write it."""
    e = ">" if big else "<"
    head = (b"MM\x00\x2a" if big else b"II\x2a\x00") + struct.pack(e + "I", 8)
    entry = struct.pack(e + "HHI", 274, 3, 1) + struct.pack(e + "H", orientation) + b"\0\0"
    return head + struct.pack(e + "H", 1) + entry + struct.pack(e + "I", 0)


def _text_chunks(rng: random.Random) -> tuple[list[bytes], list[bytes]]:
    """EXIF, XMP and ImageMagick's raw EXIF profile, at random before or after the image."""
    chunks = []
    o = rng.randint(0, 9)
    pick = rng.random()
    if pick < 0.4:
        chunks.append(chunk(b"eXIf", _exif(o, rng.random() < 0.5)))
    elif pick < 0.6:
        xmp = f'<x:xmpmeta><rdf:Description tiff:Orientation="{o}"/></x:xmpmeta>'
        chunks.append(chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + xmp.encode()))
    elif pick < 0.75:
        data = _exif(o, False)
        text = f"\nexif\n{len(data):8}\n" + data.hex() + "\n"
        chunks.append(chunk(b"zTXt", b"Raw profile type exif\0\0" + zlib.compress(text.encode())))
    before, after = [], []
    for c in chunks:
        (before if rng.random() < 0.5 else after).append(c)
    return before, after


def opened(data: bytes, mode: str) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        return ImageOps.exif_transpose(im).convert(mode)


def check(pairs: list[tuple[dict[str, Any], Any]]) -> None:
    answers = run([case for case, _ in pairs])
    for (case, want), got in zip(pairs, answers, strict=True):
        label = case.get("label", case["mode"])
        if want is ERROR:
            assert "error" in got, (label, "Pillow refuses it")
            continue
        assert "error" not in got, (label, got.get("error"))
        have = picture(got["image"])
        assert (have.mode, have.size) == (want.mode, want.size), label
        assert have.tobytes() == want.tobytes(), label


def case_of(data: bytes, mode: str, label: str) -> tuple[dict[str, Any], Any]:
    try:
        want: Any = opened(data, mode)
    except (OSError, ValueError, SyntaxError):
        want = ERROR
    return {"op": "open", "data": base64.b64encode(data).decode(), "mode": mode,
            "label": label}, want  # fmt: skip


def test_pngs_of_every_kind_open_as_pillow_opens_them():
    rng = random.Random(30)
    pairs = []
    for depth, colour in PNG_KINDS:
        for n in range(40):
            w, h = rng.randint(1, 23), rng.randint(1, 19)
            before, after = _text_chunks(rng)
            data = make_png(rng, depth, colour, w, h, interlace=n % 3 == 0, before=before,
                            after=after)  # fmt: skip
            for mode in ("RGBA", "RGB", "L"):
                pairs.append(case_of(data, mode, f"{depth}-bit colour {colour} {w}x{h} {mode}"))
    check(pairs)


def test_the_app_s_own_pngs_open_as_pillow_opens_them():
    pairs = []
    for path in sorted((ROOT / "src").rglob("*.png")):
        pairs.append(case_of(path.read_bytes(), "RGBA", str(path.relative_to(ROOT))))
    assert len(pairs) > 100
    check(pairs)


def test_pillow_s_own_pngs_open_as_pillow_opens_them():
    rng = random.Random(31)
    pairs = []
    for mode in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16"):
        for _ in range(12):
            w, h = rng.randint(1, 30), rng.randint(1, 30)
            im = Image.new(mode, (w, h))
            raw = bytes(rng.randrange(256) for _ in range(len(im.tobytes())))
            im = Image.frombytes(mode, (w, h), raw)
            out = io.BytesIO()
            params: dict[str, Any] = {}
            if mode == "P":
                im.putpalette(bytes(rng.randrange(256) for _ in range(3 * rng.randint(1, 256))))
                if rng.random() < 0.5:
                    params["transparency"] = rng.randrange(256)
                if rng.random() < 0.3:
                    params["bits"] = rng.choice([1, 2, 4])
            elif mode in ("L", "RGB") and rng.random() < 0.5:
                params["transparency"] = im.getpixel((0, 0))
            im.save(out, "PNG", **params)
            for target in ("RGBA", "RGB", "L"):
                pairs.append(case_of(out.getvalue(), target, f"Pillow's {mode} {params}"))
    check(pairs)

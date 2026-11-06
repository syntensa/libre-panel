"""rust/decode against Pillow (docs/RUST.md, stage R1): pictures decoded from their files as the
app opens them, ImageOps.exif_transpose(Image.open(file)).convert(mode), and animations' frames
as it reads them (render/moving.py's read_animation), byte for byte."""

from __future__ import annotations

import base64
import hashlib
import io
import random
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageOps, ImageSequence
from rust_check import picture, pillow_libjpeg, run

ROOT = Path(__file__).resolve().parent.parent
ERROR = object()
# what Pillow raises on files it cannot read (Image.open lets EOFError and IndexError through)
REFUSED = (OSError, ValueError, SyntaxError, EOFError, IndexError, TypeError, struct.error,
           Image.DecompressionBombError)  # fmt: skip

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


def _samples(rng: random.Random, depth: int, colour: int, w: int, h: int, *,
             interlace: bool) -> tuple[list[list[tuple[int, ...]]], bytes]:  # fmt: skip
    """Random samples of w x h and their rows filtered as an encoder may filter them."""
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
    return pixels, data


def _palette_and_transparency(rng: random.Random, depth: int, colour: int,
                              pixels: list[list[tuple[int, ...]]]) -> list[bytes]:  # fmt: skip
    """A palette (P) and now and then a tRNS chunk, as make_png gives them."""
    top = (1 << depth) - 1
    h, w = len(pixels), len(pixels[0])
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
    return extra


def make_png(rng: random.Random, depth: int, colour: int, w: int, h: int, *, interlace: bool,
             before: list[bytes], after: list[bytes]) -> bytes:  # fmt: skip
    """A PNG of random samples, its palette, transparency and the chunks given."""
    pixels, data = _samples(rng, depth, colour, w, h, interlace=interlace)
    head = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, int(interlace)))
    extra = _palette_and_transparency(rng, depth, colour, pixels)
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


# the size of each of TIFF's types (Pillow's _load_dispatch)
TIFF_UNITS = {1: 1, 2: 1, 6: 1, 7: 1, 3: 2, 8: 2, 4: 4, 9: 4, 11: 4, 13: 4, 5: 8, 10: 8, 12: 8,
              16: 8}  # fmt: skip


def _odd_exif(rng: random.Random) -> bytes:
    """EXIF of an Orientation tag of any type and count (bytes, text, fractions, negative
    numbers, none at all: Pillow keeps it and turns nothing, nor reads XMP's), now and then
    after a tag whose value lies beyond the data (where Pillow reads no further)."""
    e = ">" if rng.random() < 0.5 else "<"
    entries = []
    if rng.random() < 0.3:
        entries.append((270, 2, 100, struct.pack(e + "I", 5000)))  # (beyond the data)
    typ = rng.choice([*TIFF_UNITS, 99])
    count = rng.choice([0, 1, 1, 1, 2])
    size = TIFF_UNITS.get(typ, 1) * count
    value = bytes(rng.randrange(256) for _ in range(size))
    if typ == 3 and count and rng.random() < 0.7:
        value = struct.pack(e + "H", rng.randint(1, 8)) + value[2:]
    entries.append((274, typ, count, value))
    head = (b"MM\x00\x2a" if e == ">" else b"II\x2a\x00") + struct.pack(e + "I", 8)
    after = 8 + 2 + 12 * len(entries) + 4
    ifd, data = struct.pack(e + "H", len(entries)), b""
    for tag, typ_, count_, v in entries:
        if len(v) > 4:
            ifd += struct.pack(e + "HHII", tag, typ_, count_, after + len(data))
            data += v
        else:
            ifd += struct.pack(e + "HHI", tag, typ_, count_) + v.ljust(4, b"\0")
    return head + ifd + struct.pack(e + "I", 0) + data


def _text_chunks(rng: random.Random) -> tuple[list[bytes], list[bytes]]:
    """EXIF, XMP and ImageMagick's raw EXIF profile, at random before or after the image."""
    chunks = []
    o = rng.randint(0, 9)
    pick = rng.random()
    if pick < 0.1:
        chunks.append(chunk(b"eXIf", _odd_exif(rng)))
        xmp = f'<x:xmpmeta><rdf:Description tiff:Orientation="{rng.randint(2, 8)}"/></x:xmpmeta>'
        chunks.append(chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + xmp.encode()))
    elif pick < 0.4:
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
    except REFUSED:
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


# -- GIF ------------------------------------------------------------------------------------------


def _sub_blocks(data: bytes, rng: random.Random) -> bytes:
    """Data as GIF's sub-blocks of random lengths, and the empty one that ends them."""
    out, i = bytearray(), 0
    while i < len(data):
        part = data[i : i + rng.randint(1, 255)]
        out += bytes([len(part)]) + part
        i += len(part)
    return bytes(out) + b"\0"


def gif_lzw(pixels: list[int], bits: int, rng: random.Random, *, literal: bool = False,
            clears: bool = True) -> bytes:  # fmt: skip
    """The pixels as GIF's LZW codes, in sub-blocks: strings of the table where ``literal`` is
    not set, a clear code now and then (``clears``), the code sizes as Pillow's decoder grows
    them."""
    clear = 1 << bits
    codes: list[tuple[int, int]] = []
    state = {"width": bits + 1, "next": clear + 2, "first": True}

    def emit(code: int) -> None:
        codes.append((code, state["width"]))
        if code == clear:
            state.update(width=bits + 1, next=clear + 2, first=True)
        elif state["first"]:
            state["first"] = False
        elif state["next"] < 4096:
            if state["next"] == (1 << state["width"]) - 1 and state["width"] < 12:
                state["width"] += 1
            state["next"] += 1

    def fresh() -> dict[tuple[int, ...], int]:
        return {(i,): i for i in range(min(clear, 256))}

    table, following = fresh(), clear + 2
    if rng.random() < 0.8:
        emit(clear)
    run_: tuple[int, ...] = ()
    for k in pixels:
        if clears and run_ and rng.random() < 0.02:
            emit(table[run_])
            emit(clear)
            table, following, run_ = fresh(), clear + 2, ()
        longer = (*run_, k)
        if longer in table and not (literal and run_):
            run_ = longer
            continue
        emit(table[run_])
        if not literal and following < 4096:
            table[longer] = following
            following += 1
        run_ = (k,)
    if run_:
        emit(table[run_])
    emit(clear + 1)
    packed, acc, count = bytearray(), 0, 0
    for code, width in codes:
        acc |= code << count
        count += width
        while count >= 8:
            packed.append(acc & 255)
            acc >>= 8
            count -= 8
    if count:
        packed.append(acc & 255)
    return _sub_blocks(bytes(packed), rng)


def _colour_table(rng: random.Random, size: int, *, grey: bool) -> bytes:
    """A colour table of 2 << size entries: random, or the grey ramp (Pillow's "not needed")."""
    n = 2 << size
    if grey:
        return bytes(v for i in range(n) for v in (i, i, i))
    return bytes(rng.randrange(256) for _ in range(3 * n))


def _gce(rng: random.Random, colours: int) -> bytes:
    """A graphic control extension: any disposal (0 to 7), a transparent index or none."""
    flags = rng.randrange(8) << 2 | rng.randrange(2) << 1
    index = rng.randrange(colours) if rng.random() < 0.9 else rng.randrange(256)
    if rng.random() < 0.5:
        flags |= 1
    delay = rng.choice([0, 1, 2, 5, 10, 100, rng.randrange(65536)])
    return b"!\xf9\x04" + struct.pack("<BHB", flags, delay, index) + b"\0"


def make_gif(rng: random.Random, *, early_end: bool = False) -> bytes:
    """A GIF of random frames: its own or the file's colour table (or none, or the grey ramp),
    any place and size (now and then beyond the screen), interlaced or not, graphic controls of
    every disposal and transparency, comments, application blocks and stray bytes between."""
    w, h = rng.randint(1, 24), rng.randint(1, 24)
    size = rng.randrange(8)
    pick = rng.random()
    table = None if pick < 0.2 else _colour_table(rng, size, grey=pick < 0.3)
    flags = rng.randrange(8) << 4 | rng.randrange(2) << 3 | size
    if table is not None:
        flags |= 128
    out = bytearray(rng.choice([b"GIF89a", b"GIF87a"]) + struct.pack("<HHBBB", w, h, flags,
                    rng.randrange(256), 0))  # fmt: skip
    if table is not None:
        out += table
    if rng.random() < 0.3:
        loops = struct.pack("<H", rng.randrange(65536))
        out += b"!\xff\x0bNETSCAPE2.0\x03\x01" + loops + b"\0"
    for _ in range(rng.randint(1, 5)):
        local = None
        if rng.random() < 0.35:
            local_size = rng.randrange(8)
            local = _colour_table(rng, local_size, grey=rng.random() < 0.2)
        colours = len(local or table or b"\0" * 768) // 3
        for _ in range(rng.choice([0, 1, 1, 1, 2])):
            out += _gce(rng, colours)
        if rng.random() < 0.15:
            out += b"!\xfe" + _sub_blocks(b"a comment", rng)
        if rng.random() < 0.05:
            out += bytes([rng.choice([0, 1, 0x7F, 0xFE])])  # (a stray byte, skipped)
        x0 = rng.randint(0, w - 1) if rng.random() < 0.6 else 0
        y0 = rng.randint(0, h - 1) if rng.random() < 0.6 else 0
        fw = rng.randint(1, w - x0) if rng.random() < 0.85 else rng.randint(1, w + 6)
        fh = rng.randint(1, h - y0) if rng.random() < 0.85 else rng.randint(1, h + 6)
        if rng.random() < 0.01:
            fw = 0  # (a frame of no pixels: Pillow refuses it)
        descriptor = rng.randrange(2) << 6  # (interlaced)
        if local is not None:
            descriptor |= 128 | local_size
        out += b"," + struct.pack("<HHHHB", x0, y0, fw, fh, descriptor) + (local or b"")
        need = max(1, (colours - 1).bit_length())
        bits = rng.choice([max(2, need)] * 8 + [need, min(8, need + rng.randint(1, 3)), 12])
        literal = bits < 2 or bits == 12  # (no strings at Pillow's own code sizes there)
        span = 1 << bits
        pixels = []
        while len(pixels) < fw * fh:
            v = rng.randrange(min(colours, span))
            pixels += [v] * rng.choice([1, 1, 2, 3, 8])
        count = fw * fh if not early_end else rng.randint(0, fw * fh)
        out += bytes([bits]) + gif_lzw(pixels[:count], bits, rng, literal=literal)
    if rng.random() < 0.9:
        out += b";"
    return bytes(out)


def app_frames(data: bytes) -> list[tuple[Any, Image.Image]]:
    """An animation's frames as read_animation reads them: n_frames, the first converted, then
    ImageSequence's frames, each with its duration and converted to RGBA."""
    with Image.open(io.BytesIO(data)) as source:
        count = getattr(source, "n_frames", 1)
        first = source.convert("RGBA")
        frames = []
        for n, frame in enumerate(ImageSequence.Iterator(source)):
            duration = frame.info.get("duration")
            frames.append((duration, first if n == 0 else frame.convert("RGBA")))
    assert len(frames) <= count  # (an APNG may have fewer than it says)
    return frames


def check_frames(cases: list[tuple[bytes, str]], *, digest: bool = False) -> None:
    pairs: list[tuple[dict[str, Any], Any]] = []
    for data, label in cases:
        try:
            want: Any = app_frames(data)
        except REFUSED:
            want = ERROR
        case = {"op": "frames", "data": base64.b64encode(data).decode(), "digest": digest}
        pairs.append(({**case, "label": label}, want))
    answers = run([case for case, _ in pairs])
    for (case, want), got in zip(pairs, answers, strict=True):
        label = case["label"]
        if want is ERROR:
            assert "error" in got, (label, "Pillow refuses it")
            continue
        assert "error" not in got, (label, got.get("error"))
        assert len(got["frames"]) == len(want), label
        for n, (frame, (duration, image)) in enumerate(zip(got["frames"], want, strict=True)):
            assert frame["duration"] == duration, (label, n)
            if digest:
                assert tuple(frame["size"]) == image.size, (label, n)
                assert frame["sha256"] == hashlib.sha256(image.tobytes()).hexdigest(), (label, n)
                continue
            have = picture(frame["image"])
            assert (have.mode, have.size) == (image.mode, image.size), (label, n)
            assert have.tobytes() == image.tobytes(), (label, n)


def test_gif_frames_are_read_as_the_app_reads_them():
    rng = random.Random(40)
    check_frames([(make_gif(rng), f"GIF {n}") for n in range(400)])


def test_gifs_open_as_pillow_opens_them():
    rng = random.Random(41)
    pairs = []
    for n in range(200):
        data = make_gif(rng)
        for mode in ("RGBA", "RGB", "L"):
            pairs.append(case_of(data, mode, f"GIF {n} {mode}"))
    check(pairs)


def test_broken_gifs_are_refused_where_pillow_refuses_them():
    """Cut short, a byte changed, bytes put in, LZW data that ends before its frame does (Pillow
    reads on into the file after it): read or refused as Pillow reads or refuses them."""
    rng = random.Random(42)
    cases = []
    for n in range(300):
        data = bytearray(make_gif(rng, early_end=n % 4 == 3))
        if n % 4 == 0:
            data = data[: rng.randrange(13, len(data) + 1)]
        elif n % 4 == 1:
            data[rng.randrange(13, len(data))] = rng.randrange(256)
        elif n % 4 == 2:
            at = rng.randrange(13, len(data) + 1)
            data[at:at] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 4)))
        cases.append((bytes(data), f"broken GIF {n}"))
    check_frames(cases)
    check([case_of(data, "RGBA", label) for data, label in cases])


def test_pillow_s_animations_are_read_as_the_app_reads_them():
    rng = random.Random(43)
    cases = []
    while len(cases) < 60:
        mode = rng.choice(["P", "L", "RGB", "RGBA"])
        w, h = rng.randint(1, 40), rng.randint(1, 40)
        colours = [tuple(rng.randrange(256) for _ in range(4)) for _ in range(rng.randint(1, 12))]
        frames = []
        for _ in range(rng.randint(2, 6)):
            im = Image.new("RGBA", (w, h), rng.choice(colours))
            for _ in range(rng.randint(0, 4)):
                x, y = rng.randrange(w), rng.randrange(h)
                box = (x, y, rng.randint(x, w), rng.randint(y, h))
                im.paste(rng.choice(colours), box)
            if mode != "RGBA":
                im = im.convert(mode)
            frames.append(im)
        params: dict[str, Any] = {
            "save_all": True, "append_images": frames[1:], "loop": 0,
            "duration": rng.choice([40, [rng.randrange(20, 200) for _ in frames]]),
            "disposal": rng.choice([0, 1, 2, 3, [rng.randrange(4) for _ in frames]]),
            "optimize": rng.random() < 0.5, "interlace": rng.random() < 0.5,
        }  # fmt: skip
        if mode in ("P", "L") and rng.random() < 0.5:
            params["transparency"] = rng.randrange(256)
        out = io.BytesIO()
        try:
            frames[0].save(out, "GIF", **params)
        except TypeError:  # (Pillow's writer: a list of disposals for frames all the same)
            continue
        cases.append((out.getvalue(), f"Pillow's {mode} {w}x{h} {len(frames)} frames"))
    check_frames(cases)


def test_the_project_s_own_animations_are_read_as_the_app_reads_them():
    """The GIFs of docs/media (made by other encoders, of hundreds of frames each): the three
    smallest, each frame by its SHA-256."""
    paths = sorted((ROOT / "docs" / "media").glob("*.gif"), key=lambda p: p.stat().st_size)
    assert paths
    check_frames([(p.read_bytes(), p.name) for p in paths[:3]], digest=True)


def test_lzw_data_is_read_in_pillow_s_64_kb_reads():
    """A frame whose data spans several of ImageFile.load's 64 KB reads; and a frame whose data
    ends (its end code) before its last pixel, with a comment of zeros after it: Pillow's
    decoder is given more of the file after an end code where its reads have not reached the
    end, so it reads on into the comment (and the frame is filled from it) where the file goes
    on for more than one read, and the file is "truncated" where it does not."""
    rng = random.Random(44)
    table = bytes(rng.randrange(256) for _ in range(768))
    head = b"GIF89a" + struct.pack("<HHBBB", 300, 300, 0xF7, 0, 0) + table
    cases = []
    pixels = [rng.randrange(256) for _ in range(300 * 300)]
    frame = b"," + struct.pack("<HHHHB", 0, 0, 300, 300, 0) + b"\x08"
    cases.append((head + frame + gif_lzw(pixels, 8, rng) + b";", "a frame of 90,000 pixels"))
    for trailing in (1000, 65000, 65536, 66000, 140000):
        pixels = [rng.randrange(256) for _ in range(80 * 80)]
        frame = b"," + struct.pack("<HHHHB", 0, 0, 80, 80, 0) + b"\x08"
        data = frame + gif_lzw(pixels[:5000], 8, rng, clears=False)  # (its table full)
        comment = b"!\xfe" + (b"\xff" + bytes(255)) * (trailing // 256) + b"\0"
        small = b"," + struct.pack("<HHHHB", 2, 2, 4, 4, 0) + b"\x08"
        small += gif_lzw([1] * 16, 8, rng)
        cases.append((head + data + comment + small + b";", f"{trailing} bytes after the end"))
    check_frames(cases)
    check([case_of(data, "RGBA", label) for data, label in cases])


# -- APNG -----------------------------------------------------------------------------------------


def _split(kind: bytes, data: bytes, rng: random.Random, seq: list[int]) -> bytes:
    """Image data in one to three chunks (fdAT's each with its sequence number)."""
    cuts = sorted(rng.sample(range(1, len(data)), min(len(data) - 1, rng.randint(0, 2))))
    out = b""
    for a, b in zip([0, *cuts], [*cuts, len(data)], strict=True):
        if kind == b"fdAT":
            out += chunk(b"fdAT", struct.pack(">I", seq[0]) + data[a:b])
            seq[0] += 1
        else:
            out += chunk(b"IDAT", data[a:b])
    return out


def make_apng(rng: random.Random, depth: int, colour: int, w: int, h: int) -> bytes:
    """An APNG of random frames: a default image or not, the first frame's place given before
    its data or not (or smaller than the picture, which Pillow takes), every disposal and blend
    (and values beyond them), its frames in one to three chunks, chunks between them, now and
    then a number of frames that is not the file's, a sequence error, interlaced frames."""
    interlace = rng.random() < 0.1
    pixels, _ = _samples(rng, depth, colour, w, h, interlace=False)
    head = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, int(interlace)))
    out = b"\x89PNG\r\n\x1a\n" + head + b"".join(_palette_and_transparency(rng, depth, colour,
                                                                            pixels))  # fmt: skip
    count = rng.randint(1, 4)
    default = rng.random() < 0.25
    said = count if rng.random() < 0.9 else max(1, count + rng.choice([-1, 1]))
    out += chunk(b"acTL", struct.pack(">II", said, rng.randrange(3)))
    if default:
        _, data = _samples(rng, depth, colour, w, h, interlace=interlace)
        out += _split(b"IDAT", zlib.compress(data), rng, [0])
    seq = [0]
    for n in range(count):
        if n == 0 and rng.random() < 0.8:
            x, y, fw, fh = 0, 0, w, h
        else:
            fw, fh = rng.randint(1, w), rng.randint(1, h)
            x, y = rng.randint(0, w - fw), rng.randint(0, h - fh)
        dispose = rng.choice([0, 1, 2] * 6 + [3])
        blend = rng.choice([0, 1] * 6 + [2])
        delay = struct.pack(">HH", rng.randrange(200), rng.choice([0, 10, 100, 1000]))
        number = seq[0] if rng.random() < 0.97 else seq[0] + 1  # (now and then out of order)
        out += chunk(b"fcTL", struct.pack(">IIIII", number, fw, fh, x, y) + delay +
                     bytes([dispose, blend]))  # fmt: skip
        seq[0] = number + 1
        _, data = _samples(rng, depth, colour, fw, fh, interlace=interlace)
        kind = b"IDAT" if n == 0 and not default else b"fdAT"
        out += _split(kind, zlib.compress(data, rng.randint(0, 9)), rng, seq)
        if rng.random() < 0.2:
            out += chunk(b"tEXt", b"Comment\0between frames")
        if rng.random() < 0.1:
            out += chunk(b"prVt", b"a chunk of its own")
    return out + chunk(b"IEND", b"")


def test_apng_frames_are_read_as_the_app_reads_them():
    rng = random.Random(50)
    cases, pairs = [], []
    for depth, colour in PNG_KINDS:
        for n in range(20):
            w, h = rng.randint(1, 17), rng.randint(1, 15)
            data = make_apng(rng, depth, colour, w, h)
            label = f"APNG {n}, {depth}-bit colour {colour} {w}x{h}"
            cases.append((data, label))
            pairs.append(case_of(data, "RGBA", label))
    check_frames(cases)
    check(pairs)


def test_pillow_s_apngs_are_read_as_the_app_reads_them():
    rng = random.Random(51)
    cases = []
    while len(cases) < 60:
        mode = rng.choice(["RGBA", "RGB", "P", "L", "LA"])
        w, h = rng.randint(1, 40), rng.randint(1, 40)
        colours = [tuple(rng.randrange(256) for _ in range(4)) for _ in range(rng.randint(1, 8))]
        frames = []
        for _ in range(rng.randint(2, 5)):
            im = Image.new("RGBA", (w, h), rng.choice(colours))
            for _ in range(rng.randint(0, 4)):
                x, y = rng.randrange(w), rng.randrange(h)
                im.paste(rng.choice(colours), (x, y, rng.randint(x, w), rng.randint(y, h)))
            frames.append(im if mode == "RGBA" else im.convert(mode))
        params: dict[str, Any] = {
            "save_all": True, "append_images": frames[1:], "loop": rng.randrange(3),
            "duration": rng.choice([40, [rng.randrange(20, 200) for _ in frames]]),
            "disposal": rng.choice([0, 1, 2, [rng.randrange(3) for _ in frames]]),
            "blend": rng.choice([0, 1, [rng.randrange(2) for _ in frames]]),
            "default_image": rng.random() < 0.2,
        }  # fmt: skip
        out = io.BytesIO()
        try:
            frames[0].save(out, "PNG", **params)
        except (TypeError, ValueError):  # (Pillow's writer refuses some of these)
            continue
        cases.append((out.getvalue(), f"Pillow's {mode} APNG {w}x{h} {len(frames)} frames"))
    check_frames(cases)
    check([case_of(data, "RGBA", label) for data, label in cases])


# -- JPEG -----------------------------------------------------------------------------------------

# (libjpeg-turbo is Pillow's own, given to lp-check: Windows' wheels have it built in)
needs_libjpeg = pytest.mark.skipif(pillow_libjpeg() is None, reason="Pillow's libjpeg as a file")


def _jfif(rng: random.Random) -> bytes:
    """A JFIF segment of a density in no unit, in inches or in centimetres."""
    unit = rng.randrange(3)
    data = b"JFIF\0\x01\x02" + bytes([unit]) + struct.pack(">HHBB", 72, 72, 0, 0)
    return b"\xff\xe0" + struct.pack(">H", len(data) + 2) + data


def _with_segments(jpeg: bytes, segments: list[bytes]) -> bytes:
    """A JPEG with segments put in after its SOI (in place of its own JFIF where one is given)."""
    body = jpeg[2:]
    if body.startswith(b"\xff\xe0") and any(s.startswith(b"\xff\xe0") for s in segments):
        body = body[2 + struct.unpack(">H", body[2:4])[0] :]
    return b"\xff\xd8" + b"".join(segments) + body


def make_jpeg(rng: random.Random, mode: str, w: int, h: int) -> bytes:
    """A JPEG written by Pillow (any quality, subsampling, progressive or not), with an EXIF
    orientation (or an odd EXIF, or one Pillow cannot read), XMP's, a JFIF density or none."""
    colours = [tuple(rng.randrange(256) for _ in range(4)) for _ in range(rng.randint(1, 6))]
    im = Image.frombytes("RGBA", (w, h), rng.randbytes(w * h * 4))
    if rng.random() < 0.6:  # (flat regions as well as noise)
        for _ in range(rng.randint(1, 5)):
            x, y = rng.randrange(w), rng.randrange(h)
            im.paste(rng.choice(colours), (x, y, rng.randint(x, w), rng.randint(y, h)))
    im = im.convert(mode)
    params: dict[str, Any] = {"quality": rng.randint(1, 100), "optimize": rng.random() < 0.3,
                              "progressive": rng.random() < 0.3}  # fmt: skip
    if mode != "L":
        params["subsampling"] = rng.randrange(3)
    out = io.BytesIO()
    im.save(out, "JPEG", **params)
    segments = []
    if rng.random() < 0.5:
        segments.append(_jfif(rng))
    pick = rng.random()
    if pick < 0.4:
        exif = b"Exif\0\0" + _exif(rng.randint(0, 9), rng.random() < 0.5)
    elif pick < 0.55:
        exif = b"Exif\0\0" + _odd_exif(rng)
    elif pick < 0.62:
        exif = b"Exif\0\0XX\x2a\0\x08\0\0\0"  # (not TIFF's head)
    else:
        exif = b""
    if exif:
        segments.append(b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif)
    if rng.random() < 0.3:
        xmp = (b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta><rdf:Description tiff:Orientation=\""
               + str(rng.randint(1, 8)).encode() + b'"/></x:xmpmeta>')  # fmt: skip
        segments.append(b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp)
    return _with_segments(out.getvalue(), segments)


@needs_libjpeg
def test_jpegs_open_as_pillow_opens_them():
    rng = random.Random(60)
    pairs = []
    for n in range(240):
        mode = ("L", "RGB", "CMYK")[n % 3]
        w, h = rng.randint(1, 70), rng.randint(1, 50)
        data = make_jpeg(rng, mode, w, h)
        for target in ("RGBA", "RGB", "L"):
            pairs.append(case_of(data, target, f"JPEG {n}, {mode} {w}x{h} {target}"))
    check(pairs)


def drafted(data: bytes, mode: str | None, size: tuple[int, int], target: str, *,
            exif: bool) -> Image.Image:  # fmt: skip
    """Image.open(f), draft(mode, size), then (exif_transpose and) convert, as render/earth.py
    reads its maps."""
    with Image.open(io.BytesIO(data)) as im:
        im.draft(mode, size)
        return (ImageOps.exif_transpose(im) if exif else im).convert(target)


@needs_libjpeg
def test_jpeg_drafts_are_read_as_pillow_reads_them():
    rng = random.Random(61)
    pairs = []
    for n in range(240):
        mode = ("L", "RGB", "CMYK")[n % 3]
        w, h = rng.randint(1, 160), rng.randint(1, 120)
        data = make_jpeg(rng, mode, w, h)
        # (sizes the scales 1/2, 1/4 and 1/8 cover, and larger)
        size = tuple(max(1, v // rng.choice([1, 2, 3, 4, 5, 8, 9, 16, 20]) + rng.randint(-1, 3))
                     for v in (w, h))  # fmt: skip
        ask = rng.choice([None, "L", "RGB"])
        target = rng.choice(["RGB", "L", "RGBA"])
        exif = rng.random() < 0.3
        label = f"JPEG {n}, {mode} {w}x{h} drafted {ask} {size} as {target}"
        try:
            want: Any = drafted(data, ask, size, target, exif=exif)
        except REFUSED:
            want = ERROR
        case = {"op": "open", "data": base64.b64encode(data).decode(), "mode": target,
                "draft": {"mode": ask, "size": list(size)}, "exif": exif,
                "label": label}  # fmt: skip
        pairs.append((case, want))
    check(pairs)


@needs_libjpeg
def test_broken_jpegs_are_refused_where_pillow_refuses_them():
    """Cut short, bytes changed or put in: refused as Pillow refuses them, and read (libjpeg's
    warnings and all) where it reads them."""
    rng = random.Random(62)
    pairs = []
    for n in range(300):
        data = bytearray(make_jpeg(rng, ("L", "RGB", "CMYK")[n % 3], rng.randint(1, 60),
                                   rng.randint(1, 40)))  # fmt: skip
        if n % 3 == 0:
            data = data[: rng.randrange(2, len(data))]
        elif n % 3 == 1:
            for _ in range(rng.randint(1, 4)):
                data[rng.randrange(2, len(data))] = rng.randrange(256)
        else:
            at = rng.randrange(2, len(data))
            data[at:at] = rng.randbytes(rng.randint(1, 8))
        pairs.append(case_of(bytes(data), "RGB", f"broken JPEG {n}"))
    check(pairs)


@needs_libjpeg
def test_mpo_frames_are_read_as_the_app_reads_them():
    """MPOs (a JPEG of several pictures, as cameras keep a large preview): Pillow gives each as
    a frame, and the app reads them as an animation."""
    rng = random.Random(63)
    cases = []
    for n in range(30):
        frames = []
        for _ in range(rng.randint(2, 4)):
            w, h = rng.randint(1, 40), rng.randint(1, 30)
            frames.append(Image.frombytes("RGB", (w, h), rng.randbytes(w * h * 3)))
        out = io.BytesIO()
        frames[0].save(out, "MPO", save_all=True, append_images=frames[1:],
                       quality=rng.randint(30, 95))  # fmt: skip
        cases.append((out.getvalue(), f"MPO {n} of {len(frames)}"))
    check_frames(cases)
    check([case_of(data, "RGBA", label) for data, label in cases])


@needs_libjpeg
def test_the_app_s_earth_maps_are_read_as_earth_py_reads_them():
    """render/earth.py's _read: each map drafted to the globe's width (JPEG's fast scales),
    then converted (day and night to RGB, the clouds to L)."""
    maps = ROOT / "src" / "libre_panel" / "assets" / "earth"
    pairs = []
    for name, mode in (("day.jpg", "RGB"), ("night.jpg", "RGB"), ("clouds.jpg", "L")):
        data = (maps / name).read_bytes()
        for width in (90, 300, 700, 2048):
            want = drafted(data, mode, (width, width // 2), mode, exif=False)
            case = {"op": "open", "data": base64.b64encode(data).decode(), "mode": mode,
                    "draft": {"mode": mode, "size": [width, width // 2]}, "exif": False,
                    "label": f"{name} for a globe of {width}"}  # fmt: skip
            pairs.append((case, want))
    check(pairs)

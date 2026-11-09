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
from rust_check import picture, pillow_libjpeg, pillow_libwebp, run

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


# -- WebP -----------------------------------------------------------------------------------------

# (libwebp is Pillow's own, given to lp-check: Windows' wheels have it built in)
needs_libwebp = pytest.mark.skipif(not pillow_libwebp(), reason="Pillow's libwebp as files")


def _webp_frame(rng: random.Random, mode: str, w: int, h: int) -> Image.Image:
    colours = [tuple(rng.randrange(256) for _ in range(4)) for _ in range(rng.randint(1, 6))]
    im = Image.frombytes("RGBA", (w, h), rng.randbytes(w * h * 4))
    for _ in range(rng.randint(0, 5)):
        x, y = rng.randrange(w), rng.randrange(h)
        im.paste(rng.choice(colours), (x, y, rng.randint(x, w), rng.randint(y, h)))
    return im if mode == "RGBA" else im.convert(mode)


def make_webp(rng: random.Random, *, frames: int = 1) -> bytes:
    """A WebP written by Pillow: lossy or lossless, with alpha or none, an EXIF orientation and
    XMP's now and then; animated where `frames` > 1 (each frame its own duration)."""
    mode = rng.choice(["RGB", "RGBA"])
    w, h = rng.randint(1, 60), rng.randint(1, 45)
    ims = [_webp_frame(rng, mode, w, h) for _ in range(frames)]
    params: dict[str, Any] = {"lossless": rng.random() < 0.4, "quality": rng.randint(0, 100),
                              "method": rng.randint(0, 6)}  # fmt: skip
    if rng.random() < 0.4:
        params["exif"] = b"Exif\0\0" + _exif(rng.randint(0, 9), rng.random() < 0.5)
    if rng.random() < 0.2:
        params["xmp"] = (b'<x:xmpmeta><rdf:Description tiff:Orientation="'
                         + str(rng.randint(1, 8)).encode() + b'"/></x:xmpmeta>')  # fmt: skip
    if frames > 1:
        params.update(save_all=True, append_images=ims[1:], loop=rng.randrange(3),
                      duration=[rng.randrange(1, 300) for _ in ims],
                      minimize_size=rng.random() < 0.5, allow_mixed=rng.random() < 0.3)  # fmt: skip
    out = io.BytesIO()
    ims[0].save(out, "WEBP", **params)
    return out.getvalue()


@needs_libwebp
def test_webps_open_as_pillow_opens_them():
    rng = random.Random(70)
    pairs = []
    for n in range(150):
        data = make_webp(rng)
        for target in ("RGBA", "RGB", "L"):
            pairs.append(case_of(data, target, f"WebP {n} {target}"))
    check(pairs)


@needs_libwebp
def test_webp_animations_are_read_as_the_app_reads_them():
    """Frames laid on each other by libwebp itself; their durations as the app reads them
    (before each frame is loaded: the first frame's alone)."""
    rng = random.Random(71)
    cases = [(make_webp(rng, frames=rng.randint(2, 5)), f"WebP animation {n}") for n in range(60)]
    check_frames(cases)


@needs_libwebp
def test_broken_webps_are_refused_where_pillow_refuses_them():
    rng = random.Random(72)
    pairs, cases = [], []
    for n in range(200):
        data = bytearray(make_webp(rng, frames=rng.choice([1, 1, 3])))
        if n % 2 == 0:
            data = data[: rng.randrange(16, len(data))]
        else:
            for _ in range(rng.randint(1, 4)):
                data[rng.randrange(16, len(data))] = rng.randrange(256)
        pairs.append(case_of(bytes(data), "RGBA", f"broken WebP {n}"))
        cases.append((bytes(data), f"broken WebP {n}"))
    check(pairs)
    check_frames(cases)


# -- thumbnails as media.py makes a cover small: Image.open, thumbnail, convert ------------------


def thumbnailed(data: bytes, size: tuple[float, float], resample: int, gap: float | None,
                target: str) -> Image.Image:  # fmt: skip
    """Image.open(f), thumbnail(size, resample, reducing_gap), convert(target), no
    exif_transpose between: as media.py makes a track's cover small."""
    with Image.open(io.BytesIO(data)) as im:
        im.thumbnail(size, resample, gap)
        out = im.convert(target)
    if out.mode != target:
        # (convert("L") of a GIF whose L is P underneath gives that P picture itself, which
        # no part of the app can use: lp-check refuses it)
        raise ValueError(f"{target} asked, {out.mode} given")
    return out


def thumbnail_case(data: bytes, size: tuple[float, float], resample: int, gap: float | None,
                   target: str, label: str) -> tuple[dict[str, Any], Any]:  # fmt: skip
    try:
        want: Any = thumbnailed(data, size, resample, gap, target)
    except (*REFUSED, ZeroDivisionError):
        want = ERROR
    return {"op": "open", "data": base64.b64encode(data).decode(), "mode": target,
            "exif": False, "label": label,
            "thumbnail": {"size": list(size), "resample": resample,
                          "reducing_gap": gap}}, want  # fmt: skip


def _small_size(rng: random.Random, w: int, h: int) -> tuple[float, float]:
    """A thumbnail's size for a picture of w x h: from as large to a 33rd, whole or not, now
    and then 0 or less."""
    div = rng.choice([1, 1.5, 2, 3, 4, 5, 7, 8, 9, 13, 16, 17, 33])
    size = (max(1, w / div + rng.uniform(-2, 2)), max(1, h / div + rng.uniform(-2, 2)))
    if rng.random() < 0.3:
        size = (round(size[0]), round(size[1]))
    if rng.random() < 0.05:
        size = (rng.choice([0, -3, 1]), rng.randint(-2, 40))
    return size


def test_thumbnails_are_made_as_pillow_makes_them():
    """Every kind of picture made small in its own mode (P and 1 by NEAREST, LA as RGBA is,
    CMYK's bands as they are, I;16 in 16 bits), by each filter and reducing_gap, a JPEG
    drafted first (decoded at 1/2, 1/4 or 1/8, resized from the box its draft gives)."""
    rng = random.Random(80)
    sources = ["png", "gif"]
    if pillow_libjpeg():
        sources += ["jpeg", "jpeg"]
    if pillow_libwebp():
        sources.append("webp")
    pairs = []
    for n in range(150):
        kind = sources[n % len(sources)]
        if kind == "png":
            depth, colour = rng.choice(PNG_KINDS)
            w, h = rng.randint(1, 90), rng.randint(1, 70)
            data = make_png(rng, depth, colour, w, h, interlace=rng.random() < 0.3, before=[],
                            after=[])  # fmt: skip
            label = f"PNG {depth}/{colour} {w}x{h}"
        elif kind == "gif":
            data, label = make_gif(rng), "GIF"
        elif kind == "jpeg":
            mode = rng.choice(["L", "RGB", "CMYK"])
            w, h = rng.randint(1, 160), rng.randint(1, 120)
            data, label = make_jpeg(rng, mode, w, h), f"JPEG {mode} {w}x{h}"
        else:
            data, label = make_webp(rng), "WebP"
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except REFUSED:
            w, h = 20, 20
        for _ in range(3):
            size = _small_size(rng, w, h)
            resample, gap = rng.randrange(6), rng.choice([None, 1.0, 1.5, 2.0, 2.0, 3.0])
            target = rng.choice(["RGBA", "RGBA", "RGB", "L"])
            pairs.append(thumbnail_case(data, size, resample, gap, target,
                                        f"{n} {label} to {size}, filter {resample}, "
                                        f"gap {gap}, {target}"))  # fmt: skip
    check(pairs)


def _photo(rng: random.Random, mode: str, w: int, h: int) -> Image.Image:
    """A picture of w x h as a cover is (smooth light and colour, a few hard edges), small
    to write at any size."""
    im = Image.frombytes("RGBA", (6, 5), rng.randbytes(6 * 5 * 4))
    im = im.resize((w, h), Image.Resampling.BICUBIC).convert(mode if mode != "CMYK" else "RGB")
    for _ in range(4):
        x, y = rng.randrange(w), rng.randrange(h)
        box = (x, y, rng.randint(x, w), rng.randint(y, h))
        im.paste(tuple(rng.randrange(256) for _ in im.getbands()), box)
    return im.convert("CMYK") if mode == "CMYK" else im


def test_covers_are_made_small_as_media_py_makes_them():
    """thumbnail((512, 512)) and convert("RGBA") on covers of the sizes covers come in: JPEGs
    of every draft scale (1, 2, 4 and 8), PNGs, GIFs and WebPs larger than 512."""
    rng = random.Random(81)
    covers = []
    for mode, fmt, (w, h) in [("RGBA", "PNG", (700, 700)), ("P", "PNG", (1000, 800)),
                              ("LA", "PNG", (600, 900)), ("P", "GIF", (640, 640))]:  # fmt: skip
        im = _photo(rng, "RGBA" if mode in ("RGBA", "LA") else "RGB", w, h)
        im = im.convert(mode) if mode != "P" else im.quantize(rng.choice([16, 200, 256]))
        out = io.BytesIO()
        im.save(out, fmt)
        covers.append((out.getvalue(), f"{fmt} {mode} {w}x{h}"))
    if pillow_libwebp():
        out = io.BytesIO()
        _photo(rng, "RGBA", 900, 900).save(out, "WEBP", quality=80)
        covers.append((out.getvalue(), "WebP RGBA 900x900"))
    if pillow_libjpeg():
        for mode, (w, h) in [("RGB", (600, 600)), ("RGB", (1023, 700)), ("RGB", (1500, 1500)),
                             ("RGB", (2048, 2048)), ("L", (3001, 2201)),
                             ("CMYK", (2100, 2500)), ("RGB", (4100, 4097)),
                             ("L", (8200, 8199))]:  # fmt: skip
            out = io.BytesIO()
            _photo(rng, mode, w, h).save(
                out,
                "JPEG",
                quality=rng.choice([75, 85, 95]),
                subsampling=rng.randrange(3) if mode != "L" else -1,
            )
            covers.append((out.getvalue(), f"JPEG {mode} {w}x{h}"))
    check([thumbnail_case(data, (512, 512), Image.Resampling.BICUBIC, 2.0, "RGBA", label)
           for data, label in covers])  # fmt: skip


def test_sixteen_bit_greys_are_made_small_as_pillow_makes_them():
    """I;16 resized in 16 bits as Pillow 13.0 does it (each pass's sum CLIP16(ROUND_UP)), its
    reduce refused: greys below 256 to see each value, and 0 next to 65535 to see what the
    filters' lobes do at the ends, in the first pass and the second."""
    rng = random.Random(82)
    pairs = []
    for n in range(120):
        w, h = rng.randint(1, 60), rng.randint(1, 50)
        if n % 2:
            values = [rng.choice([0, 65535, rng.randrange(300)]) for _ in range(w * h)]
        else:
            values = [rng.randrange(300) for _ in range(w * h)]
        out = io.BytesIO()
        Image.frombytes("I;16", (w, h), struct.pack(f"<{w * h}H", *values)).save(out, "PNG")
        size = _small_size(rng, w, h)
        resample, gap = rng.randrange(6), rng.choice([None, 1.0, 2.0])
        pairs.append(thumbnail_case(out.getvalue(), size, resample, gap,
                                    rng.choice(["RGB", "L", "RGBA"]),
                                    f"I;16 {n} {w}x{h} to {size}, filter {resample}, "
                                    f"gap {gap}"))  # fmt: skip
    check(pairs)


def test_a_gif_whose_l_is_p_underneath_is_made_small_as_pillow_makes_it():
    """A GIF's first frame whose own colour table is the grey ramp, under the file's palette:
    L to Python, P underneath. Resized only by NEAREST (into P), converted as an L would ask
    and a P would do (to RGBA with a transparency refused), as the cover would be."""
    rng = random.Random(83)
    found = []
    for _ in range(4000):
        data = make_gif(rng)
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                if im.mode == "L" and im.im.mode == "P":
                    found.append((data, im.size))
        except REFUSED:
            continue
        if len(found) == 24:
            break
    assert len(found) == 24
    pairs = []
    for n, (data, (w, h)) in enumerate(found):
        for resample in (0, 3):
            for target in ("RGBA", "RGB", "L"):
                size = (w + 5, h) if n % 4 == 0 else _small_size(rng, w, h)
                pairs.append(thumbnail_case(data, size, resample, 2.0, target,
                                            f"L over P {n} to {size}, filter {resample}, "
                                            f"{target}"))  # fmt: skip
    check(pairs)


# -- pictures Pillow fails at, as the renderer reads them -------------------------------------


def _ways(path: Path) -> dict[str, list[Any]]:
    """What becomes of the file at `path` along the renderer's three ways of reading a picture:
    _read_picture (open, exif_transpose, RGBA: its size), moving.read_animation (the frames it
    keeps, None for a still) and _moving (n_frames). Each its result, or the class of what
    Python raises, whether it is an OSError (what the renderer goes on from), and its text
    (with the file's name as <PATH>)."""
    from libre_panel.render import moving

    def outcome(read: Any) -> list[Any]:
        try:
            return ["ok", read()]
        except Exception as exc:  # noqa: BLE001 (every class Python raises is compared)
            text = str(exc).replace(repr(str(path)), "<PATH>")
            return [type(exc).__name__, isinstance(exc, OSError), text]

    def still() -> list[int]:
        with Image.open(path) as src:
            return list(ImageOps.exif_transpose(src).convert("RGBA").size)

    def animation() -> int | None:
        found = moving.read_animation(path, lambda im: im)
        return None if found is None else len(found.frames)

    def frames() -> int:
        with Image.open(path) as found:
            return getattr(found, "n_frames", 1)

    return {"read": outcome(still), "animation": outcome(animation), "moves": outcome(frames)}


def _broken(rng: random.Random, data: bytes, low: int) -> bytes:
    """Cut short, a few bytes changed or a few put in, from `low` on."""
    out = bytearray(data)
    how = rng.randrange(3)
    if how == 0:
        return bytes(out[: rng.randrange(low, len(out) + 1)])
    if how == 1:
        for _ in range(rng.randint(1, 4)):
            out[rng.randrange(low, len(out))] = rng.randrange(256)
        return bytes(out)
    at = rng.randrange(low, len(out) + 1)
    out[at:at] = rng.randbytes(rng.randint(1, 8))
    return bytes(out)


def _odd_header(rng: random.Random) -> bytes:
    """EXIF data Pillow may fail at: no TIFF header, a short one, BigTIFF's, odd tags."""
    pick = rng.random()
    if pick < 0.3:
        return rng.randbytes(rng.randint(0, 12))
    if pick < 0.5:
        head = rng.choice([b"MM\x00\x2a", b"II\x2a\x00", b"MM\x00\x2b", b"II\x2b\x00"])
        return head + rng.randbytes(rng.randint(0, 6))
    return _odd_exif(rng) if pick < 0.8 else _exif(rng.randint(0, 9), rng.random() < 0.5)


def _png_of_exif(rng: random.Random) -> bytes:
    """A PNG whose EXIF Pillow may fail at: in eXIf, in text chunks named "exif" (bytes in
    tEXt, text in zTXt and iTXt, empty or not), as a raw profile of hexadecimal that
    bytes.fromhex may refuse."""
    blob, pick = _odd_header(rng), rng.random()
    if pick < 0.3:
        extra = chunk(b"eXIf", blob)
    elif pick < 0.45:
        extra = chunk(b"tEXt", b"exif\0" + blob)
    elif pick < 0.6:
        text = rng.choice([b"", b"abc", blob.hex().encode()])
        extra = chunk(b"zTXt", b"exif\0\0" + zlib.compress(text))
    elif pick < 0.75:
        extra = chunk(b"iTXt", b"exif\0\0\0\0\0" + rng.choice([b"", b"x"]))
    else:
        hexed = blob.hex()
        if rng.random() < 0.6:
            at = rng.randrange(len(hexed) + 1)
            hexed = hexed[:at] + rng.choice(["g", " ", "z0", "é", "1"]) + hexed[at:]
        text = f"\nexif\n{len(blob):8}\n{hexed}\n".encode()
        extra = chunk(b"zTXt", b"Raw profile type exif\0\0" + zlib.compress(text))
    return make_png(rng, 8, rng.choice([0, 2, 3, 6]), rng.randint(1, 12), rng.randint(1, 12),
                    interlace=False, before=[extra], after=[])  # fmt: skip


def _sized(rng: random.Random) -> tuple[str, bytes]:
    """A picture whose header says a size beyond Pillow's limit, near it, or of nothing."""
    big = rng.choice([0, 1, 20000, 65535, 70000, 1 << 20])
    other = rng.choice([0, 1, 9000, 65535])
    pick = rng.randrange(4)
    if pick == 0:
        data = make_png(rng, 8, 2, rng.randint(1, 9), rng.randint(1, 9), interlace=False,
                        before=[], after=[])  # fmt: skip
        head = struct.pack(">II", big, other) + data[24:29]
        return ".png", data[:8] + chunk(b"IHDR", head) + data[33:]
    if pick == 1:
        gif = bytearray(make_gif(rng))
        gif[6:10] = struct.pack("<HH", min(big, 65535), min(other, 65535))
        at = gif.find(b",", 13)
        if at > 0 and rng.random() < 0.5:  # (a frame beyond the screen)
            gif[at + 5 : at + 9] = struct.pack("<HH", min(big, 65535), min(other, 65535))
        return ".gif", bytes(gif)
    if pick == 2:
        jpeg = bytearray(make_jpeg(rng, "RGB", rng.randint(1, 9), rng.randint(1, 9)))
        at = next(i for i in range(len(jpeg) - 1) if jpeg[i] == 0xFF and jpeg[i + 1] in
                  (0xC0, 0xC1, 0xC2))  # fmt: skip
        jpeg[at + 5 : at + 9] = struct.pack(">HH", min(other, 65535), min(big, 65535))
        return ".jpg", bytes(jpeg)
    webp = bytearray(make_webp(rng))
    if webp[12:16] == b"VP8X":
        webp[24:27] = struct.pack("<I", max(big - 1, 0))[:3]
        webp[27:30] = struct.pack("<I", max(other - 1, 0))[:3]
    return ".webp", bytes(webp)


def _mpo(rng: random.Random) -> bytes:
    """An MPO of two pictures as Pillow writes one."""
    ims = [Image.open(io.BytesIO(make_jpeg(rng, "RGB", rng.randint(1, 16), rng.randint(1, 16))))
           for _ in range(2)]  # fmt: skip
    out = io.BytesIO()
    ims[0].save(out, "MPO", save_all=True, append_images=ims[1:])
    return out.getvalue()


def _pictures_to_break(rng: random.Random, n: int) -> list[tuple[str, str, bytes]]:
    """Pictures of each kind broken in each way: (their kind, a name's suffix, the bytes)."""
    out: list[tuple[str, str, bytes]] = []
    for i in range(n):
        kind = i % 11
        if kind == 0:
            depth, colour = rng.choice(PNG_KINDS)
            before, after = _text_chunks(rng)
            data = make_png(rng, depth, colour, rng.randint(1, 20), rng.randint(1, 20),
                            interlace=rng.random() < 0.3, before=before, after=after)  # fmt: skip
            out.append(("png", ".png", _broken(rng, data, 8)))
        elif kind == 1:
            depth, colour = rng.choice([(8, 2), (8, 6), (8, 3), (8, 0), (16, 2)])
            data = make_apng(rng, depth, colour, rng.randint(1, 12), rng.randint(1, 12))
            out.append(("apng", ".png", _broken(rng, data, 8)))
        elif kind == 2:
            data = make_gif(rng, early_end=rng.random() < 0.25)
            out.append(("gif", ".gif", _broken(rng, data, 6)))
        elif kind == 3:
            data = make_jpeg(rng, rng.choice(["L", "RGB", "CMYK"]), rng.randint(1, 40),
                             rng.randint(1, 30))  # fmt: skip
            out.append(("jpeg", ".jpg", _broken(rng, data, 2)))
        elif kind == 4:
            data = make_webp(rng, frames=rng.choice([1, 1, 3]))
            out.append(("webp", ".webp", _broken(rng, data, 12)))
        elif kind == 5:
            seg = b"Exif\0\0" + _odd_header(rng)
            app1 = b"\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg
            jpeg = make_jpeg(rng, "RGB", rng.randint(1, 20), rng.randint(1, 20))
            data = _with_segments(jpeg, [app1])
            out.append(("jpeg exif", ".jpg", data))
        elif kind == 6:
            out.append(("png exif", ".png", _png_of_exif(rng)))
        elif kind == 7:
            im = _webp_frame(rng, "RGB", rng.randint(1, 20), rng.randint(1, 20))
            webp = io.BytesIO()
            im.save(webp, "WEBP", exif=b"Exif\0\0" + _odd_header(rng))
            out.append(("webp exif", ".webp", webp.getvalue()))
        elif kind == 8:
            out.append(("sized", *_sized(rng)))
        elif kind == 9:
            data = _mpo(rng)
            out.append(("mpo", ".jpg", data if rng.random() < 0.3 else _broken(rng, data, 2)))
        else:
            data = rng.choice([b"", b"hello\n", b"<html>not found</html>", b"\0" * 30])
            out.append(("no picture", rng.choice([".png", ".gif", ".jpg", ".webp"]), data))
    return out


@needs_libjpeg
@needs_libwebp
def test_pictures_pillow_fails_at_fail_as_they_fail_in_python(tmp_path: Path) -> None:
    """Pictures cut short, with bytes changed or put in, EXIF Pillow cannot read, text where
    it wants bytes, sizes beyond its limit or of nothing, MP indexes of odd kinds: along each
    of the renderer's ways of reading a picture, what Pillow reads, or what it raises: its
    class (the renderer goes on without the picture from an OSError, without the animation
    from a ValueError too; anything else fails the widget, with its text) and its text. Of a
    file of none of its kinds (Pillow knows forty more) Rust cannot say: it leaves those to
    Python."""
    rng = random.Random(0xB20C)
    cases, want, kinds = [], [], []
    for n, (kind, suffix, data) in enumerate(_pictures_to_break(rng, 990)):
        path = tmp_path / f"picture{n}{suffix}"
        path.write_bytes(data)
        want.append(_ways(path))
        cases.append({"op": "ways", "data": base64.b64encode(data).decode(), "name": "<PATH>"})
        kinds.append(kind)
    raised: dict[str, int] = {}
    for n, (kind, got, theirs) in enumerate(zip(kinds, run(cases), want, strict=True)):
        for way, expected in theirs.items():
            have = got[way]
            if have.get("raised") == "unported":
                assert kind == "no picture" and expected[0] == "UnidentifiedImageError", (n, way)
                continue
            found = ["ok", have["ok"]] if "ok" in have else [have["raised"], have["os"],
                                                             have["msg"]]  # fmt: skip
            assert found == expected, (n, kind, way)
            raised[expected[0]] = raised.get(expected[0], 0) + 1
    for name, least in [("ok", 1000), ("OSError", 300), ("UnidentifiedImageError", 200),
                        ("SyntaxError", 40), ("ValueError", 5), ("TypeError", 14),
                        ("IndexError", 5), ("error", 12),
                        ("DecompressionBombError", 40)]:  # fmt: skip
        assert raised.get(name, 0) >= least, (name, raised)


# -- failures the random breaks seldom reach, made on purpose ------------------------------------


def _chunks_of(png: bytes) -> list[tuple[bytes, bytes]]:
    """A PNG's chunks after its signature: (type, data) each."""
    out, at = [], 8
    while at + 8 <= len(png):
        (n,) = struct.unpack(">I", png[at : at + 4])
        out.append((png[at + 4 : at + 8], png[at + 8 : at + 8 + n]))
        at += 12 + n
    return out


def _png_of(chunks: list[tuple[bytes, bytes]]) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunk(kind, data) for kind, data in chunks)


def _short_chunk(rng: random.Random, kind: bytes, length: int, after: bool) -> bytes:
    """A PNG with a `kind` chunk of `length` bytes before its data, or after it."""
    chunks = _chunks_of(make_png(rng, 8, 2, 5, 4, interlace=False, before=[], after=[]))
    at = [k for k, _ in chunks].index(b"IDAT") + after
    chunks.insert(at, (kind, rng.randbytes(length)))
    return _png_of(chunks)


def _broken_data_then(rng: random.Random, kind: bytes, length: int) -> bytes:
    """A PNG whose data zlib refuses, a `kind` chunk of `length` bytes after it."""
    chunks = _chunks_of(make_png(rng, 8, 2, 9, 7, interlace=False, before=[], after=[]))
    at = [k for k, _ in chunks].index(b"IDAT")
    data = bytearray(chunks[at][1])
    for i in range(2, len(data) - 4, 3):
        data[i] ^= 0x5A
    chunks[at] = (b"IDAT", bytes(data))
    chunks.insert(at + 1, (kind, b"\x01" * length))
    return _png_of(chunks)


def apng_cut(rng: random.Random, kind: bytes, nth: int, keep: int) -> bytes:
    """An APNG of three frames as Pillow writes one, its `nth` `kind` chunk cut to `keep`
    bytes."""
    frames = [Image.frombytes("RGB", (6, 5), rng.randbytes(90)) for _ in range(3)]
    out = io.BytesIO()
    frames[0].save(out, "PNG", save_all=True, append_images=frames[1:], duration=50)
    chunks = _chunks_of(out.getvalue())
    at = [i for i, (k, _) in enumerate(chunks) if k == kind][nth]
    chunks[at] = (kind, chunks[at][1][:keep])
    return _png_of(chunks)


def _gif_image(rng: random.Random, w: int, h: int) -> tuple[bytes, bytes]:
    """A frame's palette and its image data (the LZW code size, the blocks, their end) as
    Pillow writes a picture of `w` x `h` of four colours."""
    im = Image.frombytes("P", (w, h), bytes(rng.randrange(4) for _ in range(w * h)))
    im.putpalette(list(rng.randbytes(12)))
    out = io.BytesIO()
    im.save(out, "GIF")
    data = out.getvalue()
    at, palette = 13, b""
    if data[10] & 0x80:
        palette, at = data[at : at + (3 << ((data[10] & 7) + 1))], at + (3 << ((data[10] & 7) + 1))
    while data[at] == 0x21:  # (the extensions)
        at += 2
        while data[at]:
            at += data[at] + 1
        at += 1
    if data[at + 9] & 0x80:
        size = 3 << ((data[at + 9] & 7) + 1)
        palette, at = data[at + 10 : at + 10 + size], at + size
    start = at = at + 10
    at += 1
    while data[at]:
        at += data[at] + 1
    return palette, data[start : at + 1]


def gif_grown_without_a_palette(rng: random.Random, *, bits: int | None, cut: bool,
                                transparent: bool) -> bytes:  # fmt: skip
    """A GIF of no palette of its screen's: a first frame of a palette of its own, a second of
    none that grows the screen (load_end then wants a palette: "image has no palette"),
    transparent or not, of the LZW code size `bits` (its own where None), its data cut short."""
    palette, first = _gif_image(rng, 4, 4)
    _, second = _gif_image(rng, 4, 4)
    if bits is not None:
        second = bytes([bits]) + second[1:]
    size = (len(palette) // 3).bit_length() - 2
    out = b"GIF89a" + struct.pack("<HHBBB", 4, 4, 0, 0, 0)
    out += b"," + struct.pack("<HHHHB", 0, 0, 4, 4, 0x80 | size) + palette + first
    if transparent:
        out += b"\x21\xf9\x04\x01\x01\x00\x00\x00"
    out += b"," + struct.pack("<HHHHB", 2, 2, 4, 4, 0)
    return out + (second[:3] if cut else second + b";")


def _gif_screen_grown(w: int, h: int, keep: int) -> bytes:
    """A GIF of a 1 x 1 screen whose first frame says it is `w` x `h`, cut `keep` bytes after
    its ",": the size Image.open grows to is checked against the bomb's limit before the frame's
    flags are read."""
    screen = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0)
    frame = b"," + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x02\x02\x44\x01\x00;"
    return screen + frame[:keep]


def mpo_of(first: bytes, second: bytes, *, head: bytes = b"II*\x00", quant: int = 2,
           quant_type: int = 4, entry_type: int = 7, entries: int = 2,
           formats: tuple[int, int] = (0, 0)) -> bytes:  # fmt: skip
    """Two JPEGs as an MPO of an MP index made here: its TIFF header `head` (the directory read
    in the byte order of its first two letters, the entries big-endian only where it is
    "MM\\0*"), NumberOfImages `quant` of the TIFF type `quant_type`, `entries` MP entries (each
    picture's format as given) in a value of the type `entry_type`."""
    order = ">" if head[:2] == b"MM" else "<"
    entry_order = ">" if head == b"MM\x00\x2a" else "<"

    def tag(number: int, kind: int, count: int, value: bytes) -> bytes:
        return struct.pack(f"{order}HHI", number, kind, count) + value

    if quant_type in (3, 8):
        count = struct.pack(f"{order}H", quant) + b"\0\0"
    elif quant_type == 7:
        count = b"0002"
    else:
        count = struct.pack(f"{order}I", quant)

    def index(second_at: int) -> bytes:
        sizes = (len(first), len(second))
        data = b"".join(struct.pack(f"{entry_order}LLLHH",
                                    (formats[i] << 24) | (0x030000 if i == 0 else 0x010001),
                                    sizes[i], second_at if i else 0, 0, 0)
                        for i in range(entries))  # fmt: skip
        return (head + struct.pack(f"{order}IH", 8, 3) + tag(0xB000, 7, 4, b"0100")
                + tag(0xB001, quant_type, 1, count)
                + tag(0xB002, entry_type, 16 * entries, struct.pack(f"{order}I", 50))
                + struct.pack(f"{order}I", 0) + data)  # fmt: skip

    length = len(index(0))
    second_at = 2 + 8 + length + len(first) - 2 - 10  # (from the MP header, after "MPF\0")
    segment = b"\xff\xe2" + struct.pack(">H", 6 + length) + b"MPF\0" + index(second_at)
    return b"\xff\xd8" + segment + first[2:] + second


# (the MP indexes made: of BYTEs and of ASCII, the headers Pillow takes in both byte orders,
# BigTIFF's, NumberOfImages of none, one, more than its entries and of each type, entries
# cut short, of other formats)
MP_INDEXES: list[dict[str, Any]] = [
    {}, {"entry_type": 1}, {"entry_type": 2}, {"head": b"MM\x00\x2a"}, {"head": b"MM\x2a\x00"},
    {"head": b"II\x00\x2a"}, {"head": b"MM\x00\x2b"}, {"head": b"II\x2b\x00"}, {"quant": 1},
    {"quant": 0}, {"quant": 3}, {"quant_type": 13}, {"quant_type": 3}, {"quant_type": 9},
    {"quant_type": 7}, {"entries": 1}, {"entries": 1, "quant": 1}, {"formats": (1, 0)},
    {"formats": (0, 4)}, {"formats": (0, 8)}, {"quant": 1, "formats": (0, 1)},
]  # fmt: skip


def _rare_failures(rng: random.Random) -> list[tuple[str, str, bytes]]:
    """(what each is, its name's suffix, its bytes)"""
    out: list[tuple[str, str, bytes]] = []
    for kind, least in [(b"gAMA", 4), (b"cHRM", 32), (b"sRGB", 1), (b"pHYs", 9)]:
        for length in sorted({0, least - 1}):
            for after in (False, True):
                out.append((f"{kind!r} of {length} bytes, after the data {after}", ".png",
                            _short_chunk(rng, kind, length, after)))  # fmt: skip
        for length in (least - 1, least):
            out.append((f"data zlib refuses, then {kind!r} of {length} bytes", ".png",
                        _broken_data_then(rng, kind, length)))  # fmt: skip
    for kind, nth, keep in [(b"acTL", 0, 4), (b"fcTL", 0, 20), (b"fcTL", 1, 20), (b"fdAT", 0, 3),
                            (b"fdAT", 1, 3)]:  # fmt: skip
        out.append((f"APNG of its {kind!r} {nth} cut to {keep}", ".png",
                    apng_cut(rng, kind, nth, keep)))  # fmt: skip
    for bits in (None, 13):
        for cut in (False, True):
            for transparent in (False, True):
                out.append((f"GIF grown without a palette, code size {bits}, cut {cut}, "
                            f"transparent {transparent}", ".gif",
                            gif_grown_without_a_palette(rng, bits=bits, cut=cut,
                                                        transparent=transparent)))  # fmt: skip
    for w, h in [(65535, 65535), (65535, 1000), (20000, 9000)]:
        for keep in (8, 9, 10, 99):
            out.append((f"GIF screen grown to {w}x{h}, {keep} bytes of its frame", ".gif",
                        _gif_screen_grown(w, h, keep)))  # fmt: skip
    first = make_jpeg(rng, "RGB", rng.randint(2, 20), rng.randint(2, 20))
    second = make_jpeg(rng, "RGB", rng.randint(2, 20), rng.randint(2, 20))
    for kw in MP_INDEXES:
        out.append((f"MPO of {kw}", ".jpg", mpo_of(first, second, **kw)))
    return out


@needs_libjpeg
def test_failures_the_random_breaks_seldom_reach_fail_as_in_python(tmp_path: Path) -> None:
    # (a gAMA, cHRM, sRGB or pHYs too short, before the data (Image.open's ValueError) and
    # after it (load_end's, raised before what the decoder found); an APNG cut in its acTL,
    # in a later fcTL or fdAT (no animation: a ValueError); a GIF whose second frame grows the
    # screen without a palette (load_end's ValueError before the decoder's own error), one
    # whose screen grows beyond the bomb's limit before its frame's flags are there; MP
    # indexes of every kind Pillow reads: of BYTEs, in both byte orders and the headers it
    # takes, BigTIFF's, a NumberOfImages of each type, entries cut short, of other formats)
    rng = random.Random(0xB20E)
    cases, want, labels = [], [], []
    for n, (label, suffix, data) in enumerate(_rare_failures(rng)):
        path = tmp_path / f"rare{n}{suffix}"
        path.write_bytes(data)
        want.append(_ways(path))
        cases.append({"op": "ways", "data": base64.b64encode(data).decode(), "name": "<PATH>"})
        labels.append(label)
    seen: set[str] = set()
    for label, got, theirs in zip(labels, run(cases), want, strict=True):
        for way, expected in theirs.items():
            have = got[way]
            found = ["ok", have["ok"]] if "ok" in have else [have["raised"], have["os"],
                                                             have["msg"]]  # fmt: skip
            assert found == expected, (label, way)
            seen.add(f"{expected[0]} {expected[-1]}" if expected[0] != "ok" else "ok")
    for wanted in ["ValueError Truncated gAMA chunk", "ValueError image has no palette",
                   "ValueError APNG contains truncated fcTL chunk",
                   "ValueError APNG contains truncated acTL chunk",
                   "OSError codec configuration error when reading image file"]:  # fmt: skip
        assert wanted in seen, wanted
    assert any(s.startswith("DecompressionBombError") for s in seen), seen


def test_a_palette_s_transparency_of_one_zero_among_255s_takes_a_newline_after_them() -> None:
    # (PngStream's _simple_palette is b"^\xff*\x00\xff*$", whose $ allows a newline before
    # the end: one 0 among 255s and a newline after them is one transparent index; two
    # newlines, one before, are an alpha for each index)
    rows = zlib.compress(b"\x00" + bytes(range(6)))
    palette = bytes(v for i in range(6) for v in (i * 40, 255 - i * 40, 90))
    pairs = []
    for trns in [b"\xff\x00\n", b"\x00\n", b"\xff\xff\x00\xff\n", b"\x00\n\n", b"\n\x00",
                 b"\xff\x00\xff", b"\x00\xff\n\xff", b"\n"]:  # fmt: skip
        data = _png_of([(b"IHDR", struct.pack(">IIBBBBB", 6, 1, 8, 3, 0, 0, 0)),
                        (b"PLTE", palette), (b"tRNS", trns), (b"IDAT", rows),
                        (b"IEND", b"")])  # fmt: skip
        for mode in ("RGBA", "RGB"):
            pairs.append(case_of(data, mode, f"tRNS {trns!r} as {mode}"))
    check(pairs)

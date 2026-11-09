"""rust/pixels against Pillow (docs/RUST.md, stage R1): each operation on random pictures, its
result byte for byte as Pillow's, and refused where Pillow refuses it."""

from __future__ import annotations

import base64
import math
import random
import struct
from collections.abc import Callable
from typing import Any

from PIL import Image
from rust_check import image, noise, picture, run

MODES = ["L", "RGB", "RGBA", "RGBa"]
ERROR = object()  # what Pillow refuses


def expect(make: Callable[[], Any]) -> Any:
    try:
        return make()
    except (ValueError, TypeError):
        return ERROR


def check(pairs: list[tuple[dict[str, Any], Any]]) -> None:
    """Each case's answer from lp-check is what Pillow gave for it."""
    answers = run([case for case, _ in pairs])
    for (case, want), got in zip(pairs, answers, strict=True):
        label = {
            k: v
            for k, v in case.items()
            if not isinstance(v, (dict, list)) or (isinstance(v, list) and len(v) <= 4)
        } | {k: (v["mode"], tuple(v["size"])) for k, v in case.items() if isinstance(v, dict)}
        if want is ERROR:
            assert "error" in got, (label, "Pillow refuses it")
            continue
        assert "error" not in got, (label, got.get("error"))
        if isinstance(want, Image.Image):
            same(picture(got["image"]), want, label)
        elif isinstance(want, list):
            assert len(got["images"]) == len(want), label
            for have, one in zip(got["images"], want, strict=True):
                same(picture(have), one, label)
        else:
            assert got["box"] == (list(want) if want is not None else None), label


def same(have: Image.Image, want: Image.Image, label: Any) -> None:
    assert (have.mode, have.size) == (want.mode, want.size), label
    if have.tobytes() != want.tobytes():
        a, b = have.tobytes(), want.tobytes()
        at = next(i for i in range(len(a)) if a[i] != b[i])
        bands = len(want.getbands())
        x, y = (at // bands) % want.width, (at // bands) // want.width
        raise AssertionError((label, (x, y), have.getpixel((x, y)), want.getpixel((x, y))))


def size(rng: random.Random) -> tuple[int, int]:
    return rng.randint(1, 13), rng.randint(1, 13)


def test_conversions_between_the_modes_are_pillow_s_to_the_bit():
    rng = random.Random(1)
    pairs = []
    for source in MODES:
        for target in MODES:
            for n in range(40):
                im = noise(rng, source, size(rng), "valid" if n % 2 else "mixed")
                case = {"op": "convert", "image": image(im), "mode": target}
                pairs.append((case, expect(lambda im=im, target=target: im.convert(target))))
    check(pairs)


def test_one_picture_over_another_is_pillow_s_to_the_bit():
    rng = random.Random(2)
    pairs = []
    for _ in range(300):
        s = size(rng)
        a, b = noise(rng, "RGBA", s), noise(rng, "RGBA", s)
        case = {"op": "alpha_composite", "a": image(a), "b": image(b)}
        pairs.append((case, Image.alpha_composite(a, b)))
    for _ in range(300):
        base, over = noise(rng, "RGBA", size(rng)), noise(rng, "RGBA", size(rng))
        dest = (rng.randint(-6, base.width + 2), rng.randint(-6, base.height + 2))
        if rng.random() < 0.5:
            source: tuple[int, ...] = (rng.randint(0, over.width), rng.randint(0, over.height))
            box = (*source, over.width, over.height)  # Image.py's way with two of them
        else:
            x0, y0 = rng.randint(0, over.width), rng.randint(0, over.height)
            source = box = (x0, y0, x0 + rng.randint(0, 9), y0 + rng.randint(0, 9))
        want = base.copy()
        want.alpha_composite(over, dest, source)
        case = {"op": "alpha_composite_at", "image": image(base), "over": image(over),
                "dest": list(dest), "source": list(box)}  # fmt: skip
        pairs.append((case, want))
    check(pairs)


def test_a_crop_reaching_beyond_the_picture_is_pillow_s():
    rng = random.Random(3)
    pairs = []
    for mode in MODES:
        for _ in range(60):
            im = noise(rng, mode, size(rng))
            x0, y0 = rng.randint(-8, im.width + 4), rng.randint(-8, im.height + 4)
            box = (x0, y0, x0 + rng.randint(0, 20), y0 + rng.randint(0, 20))
            pairs.append(({"op": "crop", "image": image(im), "box": list(box)}, im.crop(box)))
    check(pairs)


def test_a_picture_pasted_with_and_without_a_mask_is_pillow_s():
    rng = random.Random(4)
    pairs = []
    for target in MODES:
        for source in MODES:
            for masked in (None, "L", "RGBA", "RGBa", "RGB"):
                for _ in range(12):
                    base, part = noise(rng, target, size(rng)), noise(rng, source, size(rng))
                    mask = noise(rng, masked, part.size, "valid") if masked else None
                    at = (rng.randint(-8, base.width), rng.randint(-8, base.height))

                    def pasted(base=base, part=part, at=at, mask=mask):
                        out = base.copy()
                        out.paste(part, at, mask)
                        return out

                    case = {"op": "paste", "image": image(base), "paste": image(part),
                            "at": list(at), "mask": image(mask) if mask else None}  # fmt: skip
                    pairs.append((case, expect(pasted)))
    check(pairs)


def test_a_colour_pasted_into_a_box_through_a_mask_is_pillow_s():
    rng = random.Random(5)
    pairs = []
    for target in MODES:
        for masked in (None, "L", "RGBA"):
            for _ in range(40):
                base = noise(rng, target, size(rng))
                x0, y0 = rng.randint(-6, base.width), rng.randint(-6, base.height)
                box = (x0, y0, x0 + rng.randint(1, 12), y0 + rng.randint(1, 12))
                bands = len(base.getbands())
                colour = [rng.choice([0, 255, rng.randrange(256)]) for _ in range(bands)]
                mask = noise(rng, masked, (box[2] - x0, box[3] - y0)) if masked else None
                ink: Any = colour[0] if bands == 1 else tuple(colour)
                out = base.copy()
                out.paste(ink, box, mask)
                case = {"op": "paste_colour", "image": image(base), "colour": colour,
                        "box": list(box), "mask": image(mask) if mask else None}  # fmt: skip
                pairs.append((case, out))
    check(pairs)


def test_each_band_through_its_table_is_pillow_s():
    rng = random.Random(6)
    pairs = []
    for mode in MODES:
        for _ in range(40):
            im = noise(rng, mode, size(rng))
            bands = len(im.getbands())
            lut = [rng.randint(-40, 300) for _ in range(256 * bands)]  # beyond 0..255 too
            pairs.append(({"op": "point", "image": image(im), "lut": lut}, im.point(lut)))
        im = noise(rng, mode, size(rng))
        short = [1] * 255
        pairs.append(({"op": "point", "image": image(im), "lut": short}, ERROR))
    check(pairs)


def test_bands_split_merged_and_an_alpha_put_are_pillow_s():
    rng = random.Random(7)
    pairs = []
    for mode in MODES:
        for _ in range(20):
            im = noise(rng, mode, size(rng))
            pairs.append(({"op": "split", "image": image(im)}, list(im.split())))
            band = rng.randrange(len(im.getbands()))
            case = {"op": "getchannel", "image": image(im), "band": band}
            pairs.append((case, im.getchannel(band)))
            bands = [noise(rng, "L", im.size) for _ in im.getbands()]
            case = {"op": "merge", "mode": mode, "bands": [image(b) for b in bands]}
            pairs.append((case, Image.merge(mode, bands)))
            alpha = noise(rng, "L", im.size)
            value = rng.randrange(256)
            for given in ({"alpha": image(alpha)}, {"value": value}):

                def put(im=im, given=given, alpha=alpha, value=value):
                    out = im.copy()
                    out.putalpha(alpha if "alpha" in given else value)
                    return out

                want = ERROR if mode == "L" else put()  # (L would become LA: not here)
                pairs.append(({"op": "putalpha", "image": image(im), **given}, want))
    check(pairs)


def test_the_box_round_what_is_not_empty_is_pillow_s():
    rng = random.Random(8)
    pairs = []
    for mode in MODES:
        for _ in range(60):
            w, h = size(rng)
            im = Image.new(mode, (w, h), 0)
            for _ in range(rng.randint(0, 3)):
                x, y = rng.randrange(w), rng.randrange(h)
                bands = len(im.getbands())
                im.putpixel((x, y), tuple(rng.choice([0, rng.randrange(1, 256)])
                                          for _ in range(bands)) if bands > 1 else 7)  # fmt: skip
            for alpha_only in (True, False):
                case = {"op": "getbbox", "image": image(im), "alpha_only": alpha_only}
                pairs.append((case, im.getbbox(alpha_only=alpha_only)))
    check(pairs)


def test_blocks_reduced_to_one_pixel_are_pillow_s():
    rng = random.Random(9)
    pairs = []
    for mode in MODES:
        for _ in range(80):
            im = noise(rng, mode, (rng.randint(1, 30), rng.randint(1, 30)))
            factor = (rng.randint(1, 7), rng.randint(1, 7))
            box = None
            if rng.random() < 0.5:
                x0, y0 = rng.randrange(im.width), rng.randrange(im.height)
                box = (x0, y0, rng.randint(x0 + 1, im.width), rng.randint(y0 + 1, im.height))
            case = {"op": "reduce", "image": image(im), "factor": list(factor),
                    "box": list(box) if box else None}  # fmt: skip
            pairs.append((case, expect(lambda im=im, f=factor, b=box: im.reduce(f, b))))
        im = noise(rng, mode, (8, 8))
        for bad in ((0, 0, 9, 8), (3, 3, 3, 8)):  # beyond the picture, and empty
            case = {"op": "reduce", "image": image(im), "factor": [2, 2], "box": list(bad)}
            pairs.append((case, ERROR))
        for box in (None, (0, 0, 9, 8), (2, 3, 6, 3), (5, 2, 1, 4), (1, 6, 7, 2)):
            # (a factor of 1: Image.py's crop of the box, beyond the picture or the wrong way)
            case = {"op": "reduce", "image": image(im), "factor": [1, 1],
                    "box": list(box) if box else None}  # fmt: skip
            pairs.append((case, expect(lambda im=im, b=box: im.reduce(1, b))))
    check(pairs)


FILTERS = [
    Image.Resampling.NEAREST,
    Image.Resampling.BOX,
    Image.Resampling.BILINEAR,
    Image.Resampling.HAMMING,
    Image.Resampling.BICUBIC,
    Image.Resampling.LANCZOS,
]


def test_a_picture_resized_with_every_filter_is_pillow_s():
    rng = random.Random(10)
    pairs = []
    for mode in MODES:
        for f in FILTERS:
            for n in range(30):
                im = noise(rng, mode, (rng.randint(1, 40), rng.randint(1, 40)))
                size = (rng.randint(1, 60), rng.randint(1, 60))
                box = None
                if n % 3 == 1:  # a box of floats inside the picture
                    x0, y0 = rng.uniform(0, im.width - 0.01), rng.uniform(0, im.height - 0.01)
                    box = (x0, y0, rng.uniform(x0, im.width), rng.uniform(y0, im.height))
                elif n % 3 == 2:  # whole pixels, the size asked for: a crop
                    w, h = rng.randint(1, im.width), rng.randint(1, im.height)
                    x0, y0 = rng.randint(0, im.width - w), rng.randint(0, im.height - h)
                    box, size = (x0, y0, x0 + w, y0 + h), (w, h)
                gap = rng.choice([None, None, 1.5, 2.0, 3.0])

                def resized(im=im, size=size, f=f, box=box, gap=gap):
                    return im.resize(size, f, box, gap)

                case = {"op": "resize", "image": image(im), "size": list(size), "filter": int(f),
                        "box": list(box) if box else None, "reducing_gap": gap}  # fmt: skip
                pairs.append((case, expect(resized)))
            # the height shrunk by more than twice the width (Pillow 13: its columns first), a
            # picture over a hundred times as tall as wide, RGBA with a reducing gap
            for w, h, size in ((12, 33, (32, 1)), (5, 90, (9, 7)), (20, 64, (18, 3)),
                               (2, 250, (3, 17)), (30, 31, (61, 2))):  # fmt: skip
                im = noise(rng, mode, (w, h))
                gap = rng.choice([None, 1.5, 2.0])
                case = {"op": "resize", "image": image(im), "size": list(size), "filter": int(f),
                        "box": None, "reducing_gap": gap}  # fmt: skip
                pairs.append((case, im.resize(size, f, None, gap)))
    check(pairs)


def test_floats_resized_are_pillow_s_to_the_bit():
    rng = random.Random(11)
    pairs = []
    for f in FILTERS:
        for n in range(30):
            w, h = rng.randint(1, 30), rng.randint(1, 30)
            values = [rng.uniform(-2.0, 300.0) for _ in range(w * h)]
            im = Image.frombytes("F", (w, h), struct.pack(f"<{w * h}f", *values))
            size = (rng.randint(1, 45), rng.randint(1, 45))
            box = None
            if n % 2:
                x0, y0 = rng.uniform(0, w - 0.01), rng.uniform(0, h - 0.01)
                box = (x0, y0, rng.uniform(x0, w), rng.uniform(y0, h))
            floats = {"mode": "F", "size": [w, h], "data": base64.b64encode(im.tobytes()).decode()}
            case = {"op": "resize", "image": floats, "size": list(size), "filter": int(f),
                    "box": list(box) if box else None}  # fmt: skip
            pairs.append((case, im.resize(size, f, box)))
    answers = run([case for case, _ in pairs])
    for (case, want), got in zip(pairs, answers, strict=True):
        assert "floats" in got, (case["filter"], case["size"], got.get("error"))
        assert tuple(got["floats"]["size"]) == want.size
        have = base64.b64decode(got["floats"]["data"])
        assert have == want.tobytes(), (case["filter"], case["size"], case["box"])


def test_gaussian_and_box_blurs_are_pillow_s_to_the_bit():
    from PIL import ImageFilter

    rng = random.Random(12)
    pairs = []
    for mode in MODES:
        for n in range(60):
            im = noise(rng, mode, (rng.randint(1, 40), rng.randint(1, 40)))
            pick = rng.random()
            if pick < 0.4:
                radius: Any = rng.choice([0.5, 1, 2, 3.5, 6, 10, 25]) * rng.uniform(0.5, 1.5)
            elif pick < 0.8:
                radius = (rng.uniform(0, 12), rng.uniform(0, 12))  # x and y apart
            else:
                radius = rng.choice([0, (0, 4.2), (3.3, 0)])
            xy = radius if isinstance(radius, tuple) else (radius, radius)
            box = n % 2 == 1
            blur = ImageFilter.BoxBlur(radius) if box else ImageFilter.GaussianBlur(radius)
            want = im.filter(blur)
            case = {"op": "box_blur" if box else "gaussian_blur", "image": image(im),
                    "radius": list(xy), "passes": 1}  # fmt: skip
            pairs.append((case, want))
        im = noise(rng, mode, (6, 5))
        for radius in ((-1.0, 2.0), (3.0, float("nan")), (2.0**31, 1.0)):  # (mostly refused)
            for op, kind in (("box_blur", ImageFilter.BoxBlur), ("gaussian_blur",
                             ImageFilter.GaussianBlur)):  # fmt: skip
                given = [str(r) if r != r else r for r in radius]  # (NaN as text: JSON has none)
                case = {"op": op, "image": image(im), "radius": given, "passes": 1}
                pairs.append((case, expect(lambda im=im, k=kind, r=radius: im.filter(k(r)))))
    check(pairs)


def test_channel_operations_blend_and_composite_are_pillow_s():
    from PIL import ImageChops

    rng = random.Random(13)
    pairs = []
    names = ["multiply", "screen", "add", "subtract", "lighter", "darker", "difference",
             "add_modulo", "subtract_modulo"]  # fmt: skip
    for mode in MODES:
        for name in names:
            for _ in range(15):
                a = noise(rng, mode, size(rng))
                other = rng.choice([mode, "RGBa" if mode == "RGBA" else mode])  # as many bands
                b = noise(rng, other, size(rng))  # (another size: the smaller of both)
                extra: dict[str, Any] = {}
                if name in ("add", "subtract") and rng.random() < 0.6:
                    extra = {"scale": rng.choice([0.5, 1.0, 1.7, 2.0, 3.0]),
                             "offset": rng.randint(-60, 60)}  # fmt: skip
                want = getattr(ImageChops, name)(a, b, **extra)
                case = {"op": "chop", "name": name, "a": image(a), "b": image(b), **extra}
                pairs.append((case, want))
        for _ in range(30):
            s = size(rng)
            a, b = noise(rng, mode, s), noise(rng, mode, s)
            alpha = rng.choice([0.0, 1.0, rng.random(), rng.uniform(-1.5, 2.5)])
            case = {"op": "blend", "a": image(a), "b": image(b), "alpha": alpha}
            pairs.append((case, Image.blend(a, b, alpha)))
            mask = noise(rng, rng.choice(["L", "RGBA"]), s)
            case = {"op": "composite", "a": image(a), "b": image(b), "mask": image(mask)}
            pairs.append((case, Image.composite(a, b, mask)))
            im = noise(rng, mode, s)
            x, y = rng.randint(-40, 40), rng.randint(-40, 40)
            pairs.append(({"op": "offset", "image": image(im), "x": x, "y": y},
                          ImageChops.offset(im, x, y)))  # fmt: skip
    check(pairs)


def _colour(rng: random.Random, bands: int) -> list[int]:
    return [rng.randrange(256) for _ in range(bands)]


def _points(rng: random.Random, w: int, h: int, n: int) -> list[tuple[float, float]]:
    """Points on and beyond the picture, whole or not (C truncates them, -0.5 to 0)."""
    out = []
    for _ in range(n):
        x, y = rng.uniform(-12, w + 12), rng.uniform(-12, h + 12)
        if rng.random() < 0.4:
            x, y = float(round(x)), float(round(y))
        out.append((x, y))
    if rng.random() < 0.3 and len(out) > 2:  # a horizontal run, a point twice
        out[1] = (out[1][0], out[0][1])
        out.insert(2, out[2])
    return out


def test_points_lines_polygons_rectangles_and_bitmaps_are_pillow_s():
    from PIL import ImageDraw

    rng = random.Random(14)
    pairs = []
    for mode in MODES:
        bands = len(Image.new(mode, (1, 1)).getbands())
        for _ in range(120):
            im = noise(rng, mode, (rng.randint(1, 40), rng.randint(1, 40)))
            out: Any = im.copy()
            draw = ImageDraw.Draw(out).draw
            commands = []
            refused = False
            for _ in range(rng.randint(1, 4)):
                colour = _colour(rng, bands)
                ink = draw.draw_ink(colour[0] if bands == 1 else tuple(colour))
                kind = rng.choice(["points", "lines", "lines", "polygon", "polygon", "rectangle",
                                   "bitmap"])  # fmt: skip
                width = rng.choice([1, 1, 2, 3, 4, 7, 12])
                fill = rng.random() < 0.6
                c: dict[str, Any] = {"kind": kind, "ink": colour, "width": width, "fill": fill}
                if kind == "points":
                    xy = _points(rng, im.width, im.height, rng.randint(1, 8))
                    draw.draw_points(xy, ink)
                elif kind == "lines":
                    xy = _points(rng, im.width, im.height, rng.randint(1, 6))
                    draw.draw_lines(xy, ink, width)
                elif kind == "polygon":
                    xy = _points(rng, im.width, im.height, rng.randint(1, 8))
                    try:
                        draw.draw_polygon(xy, ink, int(fill), width)
                    except TypeError:  # (one point: Pillow refuses it, so must lp-check)
                        refused = True
                elif kind == "rectangle":
                    x0, y0 = rng.uniform(-10, im.width), rng.uniform(-10, im.height)
                    xy = (x0, y0, x0 + rng.uniform(0, 25), y0 + rng.uniform(0, 25))
                    draw.draw_rectangle(xy, ink, int(fill), width)
                    c["xy"] = list(xy)
                else:
                    mask = noise(rng, "L", (rng.randint(1, 12), rng.randint(1, 12)))
                    at = (rng.uniform(-8, im.width), rng.uniform(-8, im.height))
                    draw.draw_bitmap(at, mask.im, ink)
                    xy, c["bitmap"] = [at], image(mask)
                if "xy" not in c:
                    c["xy"] = [list(p) for p in xy]
                commands.append(c)
                if refused:
                    out = ERROR
                    break
            pairs.append(({"op": "draw", "image": image(im), "commands": commands}, out))
    check(pairs)


def _angle(rng: random.Random) -> float:
    """Angles as themes give them and beyond: quarters, whole and odd degrees, turns over."""
    pick = rng.random()
    if pick < 0.3:
        return float(rng.choice([0, 45, 90, 135, 180, 270, 360, -90, -180, 450, 540, 720]))
    if pick < 0.6:
        return float(rng.randint(-400, 800))
    return rng.uniform(-800, 800)


def test_ellipses_arcs_chords_and_pieslices_are_pillow_s():
    from PIL import ImageDraw

    rng = random.Random(15)
    pairs = []
    for mode in MODES:
        bands = len(Image.new(mode, (1, 1)).getbands())
        for _ in range(200):
            im = noise(rng, mode, (rng.randint(1, 48), rng.randint(1, 48)))
            out: Any = im.copy()
            draw = ImageDraw.Draw(out).draw
            commands = []
            for _ in range(rng.randint(1, 3)):
                colour = _colour(rng, bands)
                ink = draw.draw_ink(colour[0] if bands == 1 else tuple(colour))
                kind = rng.choice(["ellipse", "arc", "chord", "pieslice"])
                width = rng.choice([0, 1, 1, 2, 3, 4, 6, 9, 15, 40])
                fill = rng.random() < 0.4
                x0, y0 = rng.uniform(-12, im.width), rng.uniform(-12, im.height)
                if rng.random() < 0.4:
                    x0, y0 = float(round(x0)), float(round(y0))
                w, h = rng.choice([0.0, 1.0, 2.0, rng.uniform(0, 50)]), rng.uniform(0, 50)
                if rng.random() < 0.15:
                    w, h = h, h  # (a circle)
                xy = (x0, y0, x0 + w, y0 + h)
                if rng.random() < 0.03:
                    xy = (x0 + 1, y0, x0, y0 + h)  # (the wrong way round: refused)
                start = _angle(rng)
                end = rng.choice([start, start + 360, start + 360.5, start + 90, _angle(rng)])
                c: dict[str, Any] = {"kind": kind, "ink": colour, "width": width, "fill": fill,
                                     "xy": list(xy), "start": start, "end": end}  # fmt: skip
                commands.append(c)
                try:
                    if kind == "ellipse":
                        draw.draw_ellipse(xy, ink, int(fill), width)
                    elif kind == "arc":
                        draw.draw_arc(xy, start, end, ink, width)
                    elif kind == "chord":
                        draw.draw_chord(xy, start, end, ink, int(fill), width)
                    else:
                        draw.draw_pieslice(xy, start, end, ink, int(fill), width)
                except ValueError:
                    out = ERROR
                    break
            pairs.append(({"op": "draw", "image": image(im), "commands": commands}, out))
    check(pairs)


def _floats(rng: random.Random, size: tuple[int, int]) -> Image.Image:
    w, h = size
    values = [rng.choice([0.0, 1.0, -1.0, rng.uniform(-5, 5)]) for _ in range(w * h)]
    return Image.frombytes("F", size, struct.pack(f"<{w * h}f", *values))


def _ink(rng: random.Random, mode: str) -> Any:
    """A colour as a theme gives ImageDraw one (and as lp-check takes it)."""
    if mode == "F":
        return rng.choice([0.0, 1.0, 0.5, rng.uniform(-3, 3)])
    if mode == "L":
        return rng.randrange(256)
    return tuple(rng.randrange(256) for _ in range(rng.choice([3, 4])))


def _json_ink(ink: Any) -> Any:
    """An ink as lp-check takes it: a list of values (bytes), a number (F) or null."""
    if isinstance(ink, int):
        return [ink]
    return list(ink) if isinstance(ink, tuple) else ink


def test_imagedraw_s_own_methods_are_pillow_s_on_bytes_and_floats():
    from PIL import ImageDraw

    rng = random.Random(16)
    pairs = []
    kinds = ["point", "line", "line", "rectangle", "rounded_rectangle", "rounded_rectangle",
             "ellipse", "circle", "arc", "chord", "pieslice", "polygon", "bitmap"]  # fmt: skip
    for mode in [*MODES, "F"]:
        for _ in range(150):
            size = (rng.randint(1, 56), rng.randint(1, 56))
            im = _floats(rng, size) if mode == "F" else noise(rng, mode, size)
            out: Any = im.copy()
            draw = ImageDraw.Draw(out)
            commands = []
            for _ in range(rng.randint(1, 3)):
                kind = rng.choice(kinds[:-1] if mode == "F" else kinds)
                fill = None if rng.random() < 0.3 else _ink(rng, mode)
                outline = None if rng.random() < 0.4 else _ink(rng, mode)
                if rng.random() < 0.15:
                    outline = fill  # (its own fill's ink: ImageDraw leaves the outline out)
                width = rng.choice([0, 1, 1, 2, 3, 5, 6, 9, 12, 20])
                x0, y0 = rng.uniform(-10, size[0]), rng.uniform(-10, size[1])
                if rng.random() < 0.5:
                    x0, y0 = float(round(x0)), float(round(y0))
                box = (x0, y0, x0 + rng.uniform(0, 45), y0 + rng.uniform(0, 45))
                if rng.random() < 0.3:
                    box = tuple(float(round(v)) for v in box)
                xy = _points(rng, size[0], size[1], rng.randint(1, 7))
                start, end = _angle(rng), _angle(rng)
                c: dict[str, Any] = {"kind": kind, "fill": _json_ink(fill), "width": width,
                                     "outline": _json_ink(outline)}  # fmt: skip
                try:
                    if kind == "point":
                        draw.point(xy, fill)
                        c["xy"] = [list(p) for p in xy]
                    elif kind == "line":
                        joint = rng.choice([None, "curve", "curve"])
                        draw.line(xy, fill, width, joint)
                        c |= {"xy": [list(p) for p in xy], "joint": joint}
                    elif kind == "rectangle":
                        draw.rectangle(box, fill, outline, width)
                        c["xy"] = list(box)
                    elif kind == "rounded_rectangle":
                        radius = rng.choice([0, 1, 2.5, 4, 8, 15, 40, rng.uniform(0, 30)])
                        corners = None
                        if rng.random() < 0.3:
                            corners = tuple(rng.random() < 0.6 for _ in range(4))
                        draw.rounded_rectangle(box, radius, fill, outline, width, corners=corners)
                        c |= {"xy": list(box), "radius": radius, "corners": corners}
                    elif kind == "ellipse":
                        draw.ellipse(box, fill, outline, width)
                        c["xy"] = list(box)
                    elif kind == "circle":
                        middle, radius = xy[0], rng.uniform(0, 25)
                        draw.circle(middle, radius, fill, outline, width)
                        c |= {"xy": [list(middle)], "radius": radius}
                    elif kind == "arc":
                        draw.arc(box, start, end, fill, width)
                        c |= {"xy": list(box), "start": start, "end": end}
                    elif kind in ("chord", "pieslice"):
                        getattr(draw, kind)(box, start, end, fill, outline, width)
                        c |= {"xy": list(box), "start": start, "end": end}
                    elif kind == "polygon":
                        draw.polygon(xy, fill, outline, width)
                        c["xy"] = [list(p) for p in xy]
                    else:
                        mask = noise(rng, "L", (rng.randint(1, 12), rng.randint(1, 12)))
                        at = (rng.uniform(-8, size[0]), rng.uniform(-8, size[1]))
                        draw.bitmap(at, mask, fill)
                        c |= {"xy": [list(at)], "bitmap": image(mask)}
                except (ValueError, TypeError):
                    commands.append(c)
                    out = ERROR
                    break
                commands.append(c)
            pairs.append(({"op": "imagedraw", "image": image(im), "commands": commands}, out))
    check(pairs)


def test_transposes_transforms_and_rotations_are_pillow_s():
    rng = random.Random(17)
    pairs = []
    resamples = [0, 0, 2, 2, 3, 3]
    for mode in MODES:
        bands = len(Image.new(mode, (1, 1)).getbands())
        for method in range(7):
            im = noise(rng, mode, size(rng))
            case = {"op": "transpose", "image": image(im), "method": method}
            pairs.append((case, im.transpose(Image.Transpose(method))))
        for _ in range(160):
            im = noise(rng, mode, (rng.randint(1, 30), rng.randint(1, 30)))
            resample = rng.choice(resamples + [1] * (rng.random() < 0.05))
            fill = _colour(rng, bands) if rng.random() < 0.3 else None
            out_size = (rng.randint(1, 36), rng.randint(1, 36))
            kind = rng.choice(["affine", "affine", "affine", "extent", "perspective", "quad"])
            turn, scale = rng.uniform(-3.2, 3.2), rng.choice([1.0, 0.5, 2.0, rng.uniform(0.2, 3)])
            if kind == "affine":
                c, s = math.cos(turn) / scale, math.sin(turn) / scale
                data = [c, s, rng.uniform(-20, 30), -s, c, rng.uniform(-20, 30)]
                if rng.random() < 0.25:  # (a scale only: Geometry.c's own path for it)
                    data[1] = data[3] = 0.0
                if rng.random() < 0.1:  # (beyond fixed point's reach)
                    data[0] *= 3000
            elif kind == "extent":
                x0, y0 = rng.uniform(-10, 20), rng.uniform(-10, 20)
                data = [x0, y0, x0 + rng.uniform(-5, 40), y0 + rng.uniform(-5, 40)]
            elif kind == "perspective":
                tilt = rng.uniform(-0.3, 0.3)
                data = [1 / scale, tilt, rng.uniform(-5, 5), -tilt, 1 / scale, rng.uniform(-5, 5),
                        rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01)]  # fmt: skip
            else:
                data = [rng.uniform(-5, 35) for _ in range(8)]
            how = getattr(Image.Transform, kind.upper())
            case = {"op": "transform", "image": image(im), "size": list(out_size), "method": kind,
                    "data": data, "resample": resample, "fillcolor": fill}  # fmt: skip
            colour = None if fill is None else fill[0] if bands == 1 else tuple(fill)
            pairs.append((case, expect(
                lambda im=im, s=out_size, how=how, d=data, r=resample, c=colour: im.transform(
                    s, how, d, r, fillcolor=c))))  # fmt: skip
        for _ in range(160):
            im = noise(rng, mode, (rng.randint(1, 30), rng.randint(1, 30)))
            angle = rng.choice([0.0, 90.0, 180.0, 270.0, -90.0, 450.0, 45.0, 30.0,
                                rng.uniform(-720, 720), rng.uniform(-3, 3)])  # fmt: skip
            resample = rng.choice(resamples)
            expand = rng.random() < 0.5
            center = (rng.uniform(-5, 35), rng.uniform(-5, 35)) if rng.random() < 0.25 else None
            shift = (rng.randint(-6, 6), rng.randint(-6, 6)) if rng.random() < 0.2 else None
            fill = _colour(rng, bands) if rng.random() < 0.3 else None
            case = {"op": "rotate", "image": image(im), "angle": angle, "resample": resample,
                    "expand": expand, "center": center, "translate": shift,
                    "fillcolor": fill}  # fmt: skip
            colour = None if fill is None else fill[0] if bands == 1 else tuple(fill)
            pairs.append((case, im.rotate(angle, resample, expand, center, shift, colour)))
    check(pairs)


def test_thumbnails_image_ops_extrema_and_gradients_are_pillow_s():
    from PIL import ImageOps

    rng = random.Random(18)
    pairs: list[tuple[dict[str, Any], Any]] = []
    methods = [1, 1, 3, 3, 2, 0, 4, 5]
    for mode in MODES:
        bands = len(Image.new(mode, (1, 1)).getbands())
        for _ in range(60):
            im = noise(rng, mode, (rng.randint(1, 70), rng.randint(1, 70)))
            want_size = (rng.choice([rng.uniform(1, 90), float(rng.randint(1, 90))]),
                         rng.choice([rng.uniform(1, 90), float(rng.randint(1, 90))]))  # fmt: skip
            method, gap = rng.choice(methods), rng.choice([None, 2.0, 2.0, 3.0])
            small = im.copy()
            small.thumbnail(want_size, method, gap)
            case = {"op": "thumbnail", "image": image(im), "size": list(want_size),
                    "method": method, "reducing_gap": gap}  # fmt: skip
            pairs.append((case, small))
            room = (rng.randint(0, 60), rng.randint(0, 60))
            method = rng.choice(methods)
            centering = rng.choice([(0.5, 0.5), (0.0, 1.0), (rng.uniform(-0.5, 1.5),
                                    rng.uniform(-0.5, 1.5))])  # fmt: skip
            colour = _colour(rng, bands) if rng.random() < 0.6 else None
            fill = None if colour is None else colour[0] if bands == 1 else tuple(colour)
            bleed = rng.choice([0.0, 0.0, 0.1, 0.25, 0.6])
            made = {
                "contain": expect(lambda im=im, r=room, m=method: ImageOps.contain(im, r, m)),
                "cover": expect(lambda im=im, r=room, m=method: ImageOps.cover(im, r, m)),
                "pad": expect(lambda im=im, r=room, m=method, f=fill, c=centering:
                              ImageOps.pad(im, r, m, f, c)),
                "fit": expect(lambda im=im, r=room, m=method, b=bleed, c=centering:
                              ImageOps.fit(im, r, m, b, c)),
            }  # fmt: skip
            for op, want in made.items():
                case = {"op": op, "image": image(im), "size": list(room), "method": method,
                        "centering": list(centering), "color": colour, "bleed": bleed}  # fmt: skip
                pairs.append((case, want))
        for orientation in range(10):
            im = noise(rng, mode, size(rng))
            turned = im.copy()
            turned.getexif()[0x0112] = orientation
            case = {"op": "exif_transpose", "image": image(im), "orientation": orientation}
            pairs.append((case, ImageOps.exif_transpose(turned)))
        for _ in range(10):
            im = noise(rng, mode, size(rng))
            want: Any = im.getextrema()
            want = [want] if bands == 1 else list(want)
            got = run([{"op": "getextrema", "image": image(im)}])[0]
            assert got["extrema"] == [list(e) for e in want], (mode, im.size)
    for name in ("linear_gradient", "radial_gradient"):
        pairs.append(({"op": name}, getattr(Image, name)("L")))
    check(pairs)


def _few_colours(rng: random.Random, w: int, h: int) -> Image.Image:
    """An RGB picture of one of the kinds a quantizer meets: noise, a few colours, smooth
    light, shapes on a ground, or values that repeat along a channel (ties along an axis)."""
    kind = rng.randrange(5)
    if kind == 0:
        return Image.frombytes("RGB", (w, h), rng.randbytes(w * h * 3))
    if kind == 1:
        cols = [bytes(rng.randrange(256) for _ in range(3)) for _ in range(rng.randint(1, 12))]
        return Image.frombytes("RGB", (w, h), b"".join(rng.choice(cols) for _ in range(w * h)))
    if kind == 2:
        small = Image.frombytes("RGB", (3, 3), rng.randbytes(27))
        return small.resize((w, h), Image.Resampling.BICUBIC)
    if kind == 3:
        im = Image.new("RGB", (w, h), tuple(rng.randrange(256) for _ in range(3)))
        for _ in range(rng.randint(1, 8)):
            x, y = rng.randrange(w), rng.randrange(h)
            box = (x, y, rng.randint(x, w), rng.randint(y, h))
            im.paste(tuple(rng.randrange(256) for _ in range(3)), box)
        return im
    vals = [rng.randrange(256) for _ in range(rng.randint(1, 4))]
    data = bytes(rng.choice(vals) if rng.random() < 0.7 else rng.randrange(256)
                 for _ in range(w * h * 3))  # fmt: skip
    return Image.frombytes("RGB", (w, h), data)


def test_quantized_pictures_and_their_colours_are_pillow_s():
    """quantize(colors, MEDIANCUT) then convert("RGB"), as render/cover.py cuts a cover down
    (its 48 x 48 picture to 8 colours among them), and getcolors in Pillow's own order (None
    past maxcolors); pictures of more than 65536 colours too (counted at half the precision);
    RGBA and a number of colours outside 1 to 256 refused."""
    rng = random.Random(90)
    cases: list[dict[str, Any]] = []
    wants: list[Any] = []

    def quantized(im: Image.Image, colors: int) -> None:
        cases.append({"op": "quantize", "image": image(im), "colors": colors})
        wants.append(expect(lambda: im.quantize(colors, Image.Quantize.MEDIANCUT).convert("RGB")))

    def coloured(im: Image.Image, maxcolors: int) -> None:
        cases.append({"op": "getcolors", "image": image(im), "maxcolors": maxcolors})
        wants.append(("colours", im.getcolors(maxcolors)))

    for _ in range(150):
        im = _few_colours(rng, rng.randint(1, 90), rng.randint(1, 70))
        if rng.random() < 0.15:
            im = im.convert("L")
        colors = rng.choice([1, 2, 3, 5, 8, 8, 8, 16, 64, 255, 256, rng.randint(1, 256)])
        quantized(im, colors)
        few = im.quantize(colors, Image.Quantize.MEDIANCUT).convert("RGB")
        maxcolors = rng.choice([1, 2, 7, 8, 256, im.width * im.height, rng.randint(1, 3000)])
        coloured(few, maxcolors)
        coloured(im, maxcolors)
    for _ in range(20):
        cover = noise(rng, "RGBA", (rng.randint(48, 400), rng.randint(48, 400)))
        cover = cover.resize((5, 4)).resize(cover.size, Image.Resampling.BICUBIC)
        small = cover.convert("RGB").resize((48, 48), Image.Resampling.BILINEAR)
        quantized(small, 8)
        coloured(small.quantize(8, Image.Quantize.MEDIANCUT).convert("RGB"), 48 * 48)
    for size in ((300, 260), (270, 280)):
        quantized(Image.frombytes("RGB", size, rng.randbytes(size[0] * size[1] * 3)), 64)
    quantized(noise(rng, "RGBA", (9, 7)), 8)
    quantized(noise(rng, "RGBa", (9, 7)), 8)
    quantized(noise(rng, "RGB", (9, 7)), 0)
    quantized(noise(rng, "RGB", (9, 7)), 257)
    answers = run(cases)
    for case, want, got in zip(cases, wants, answers, strict=True):
        label = (case["op"], case["image"]["mode"], tuple(case["image"]["size"]))
        if want is ERROR:
            assert "error" in got, (label, "Pillow refuses it")
            continue
        assert "error" not in got, (label, got.get("error"))
        if isinstance(want, Image.Image):
            assert picture(got["image"]).tobytes() == want.tobytes(), label
            continue
        listed = want[1]
        if listed is None:
            assert got["colors"] is None, label
        else:
            mine = [(c, tuple(px) if len(px) > 1 else px[0]) for c, px in got["colors"]]
            assert mine == listed, label


def _float32s(a: Any) -> str:
    import numpy as np

    return base64.b64encode(np.ascontiguousarray(a, np.float32).tobytes()).decode()


def test_the_renderer_s_dither_its_levels_ramps_and_blur():
    """render/dither.py's numpy: its noise (default_rng(0x11B7E)'s float32s: SeedSequence and
    PCG64), to_u8 and ramp_u8 to the byte, and its blur to the bit where it takes its passes
    (single precision's running sums); where it multiplies by their matrices through numpy's
    BLAS (whose sums go in the processor's own order) within half a thousandth of a level."""
    import numpy as np

    from libre_panel.render import dither as d

    rng = random.Random(91)
    cases: list[dict[str, Any]] = []
    wants: list[tuple[str, Any]] = []
    cases.append({"op": "dither_noise", "w": 128, "h": 128, "x": 0, "y": 0})
    wants.append(("floats", d._tile()))
    for _ in range(12):
        w, h = rng.randint(1, 300), rng.randint(1, 300)
        x, y = rng.randint(-500, 2000), rng.randint(-500, 2000)
        cases.append({"op": "dither_noise", "w": w, "h": h, "x": x, "y": y})
        wants.append(("floats", d.noise(w, h, x, y)))
    for _ in range(60):
        w, h, c = rng.randint(1, 60), rng.randint(1, 40), rng.choice([1, 1, 3, 4])
        values = np.array([rng.choice([0.0, 255.0, rng.uniform(-5, 260), rng.uniform(0, 3),
                                       rng.randint(0, 255) + 0.5])
                           for _ in range(w * h * c)], np.float32)  # fmt: skip
        shaped = values.reshape(h, w, c) if c > 1 else values.reshape(h, w)
        x, y, dither = rng.randint(-50, 900), rng.randint(-50, 900), rng.random() < 0.8
        cases.append({"op": "to_u8", "values": _float32s(values), "w": w, "h": h, "c": c,
                      "x": x, "y": y, "dither": dither})  # fmt: skip
        wants.append(("bytes", d.to_u8(shaped, x, y, dither)))
    for _ in range(40):
        n, c = rng.randint(1, 200), rng.choice([3, 4])
        line = np.array([[rng.uniform(0, 255) for _ in range(c)] for _ in range(n)], np.float32)
        length = rng.choice([1, 127, 128, 129, rng.randint(1, 500)])
        across, dither = rng.random() < 0.5, rng.random() < 0.8
        cases.append({"op": "ramp_u8", "line": _float32s(line), "n": n, "c": c, "length": length,
                      "across": across, "dither": dither})  # fmt: skip
        wants.append(("bytes", d.ramp_u8(line, length, across, dither)))
    products = 0
    for k in range(40):
        h, w = rng.randint(1, 700), rng.randint(1, 100)
        if k % 2:
            h, w = w, h
        radius = rng.choice([0.0, 0.5, 1.0, 3.3, 6.0, 12.5, 30.0, rng.uniform(0.1, 40)])
        a = np.zeros((h, w), np.float32)
        if k % 3 == 0:  # bright and long: single precision's running sums at their worst
            a[:] = 255.0 * (np.random.default_rng(k).random((h, w)) < 0.9)
            far = min(d.reach(radius), h, w)
            a[h - far :] = 0
            a[:, w - far :] = 0
        for _ in range(rng.randint(1, 5)):
            y0, x0 = rng.randrange(h), rng.randrange(w)
            a[y0 : y0 + rng.randint(1, 40), x0 : x0 + rng.randint(1, 40)] = rng.uniform(0, 255)
        cases.append({"op": "blur_floats", "values": _float32s(a), "w": w, "h": h,
                      "radius": radius})  # fmt: skip
        far = d.reach(radius)
        rows = d._stepped(h) if h > far and not a[h - far :].any() else h
        cols = d._stepped(w) if w > far and not a[:, w - far :].any() else w
        took = max(h, w) <= d.PRODUCT_MOST and d._served(rows) and d._served(cols)
        products += took and radius > 0
        wants.append(("products" if took and radius > 0 else "passes", d.blur(a, radius)))
    assert products >= 8
    answers = run(cases)
    for case, (kind, want), got in zip(cases, wants, answers, strict=True):
        assert "error" not in got, (case["op"], got.get("error"))
        if kind == "bytes":
            assert base64.b64decode(got["bytes"]) == np.asarray(want).tobytes(), case["op"]
            continue
        mine = np.frombuffer(base64.b64decode(got["floats"]), np.float32)
        ours = np.asarray(want, np.float32).ravel()
        if kind == "products":
            assert np.abs(mine - ours).max() <= 5e-4, (case["w"], case["h"], case["radius"])
        else:
            assert np.array_equal(mine, ours), (case["op"], case.get("radius"))


def _colour_texts(rng: random.Random, count: int) -> list[str]:
    """Colours in every form ImageColor reads, and as many it refuses: names in any case,
    hex of every length, rgb() in numbers and percent, hsl(), hsv(), hsb(), rgba(), white space
    and digits of other scripts, a last line end, numbers of more than 53 bits (where floats
    are no longer whole), garbage."""
    from PIL import ImageColor

    names = list(ImageColor.colormap)
    spaces = ["", " ", "  ", "\t", "　", "\xa0", "\n", "​"]
    other_digits = "٠٣٩۵१３"

    def number(top: int) -> str:
        text = str(rng.randrange(top))
        return "".join(rng.choice(other_digits) if rng.random() < 0.15 else c for c in text)

    def space() -> str:
        return rng.choice(spaces) if rng.random() < 0.4 else ""

    def one() -> str:
        kind = rng.randrange(10)
        end = rng.choice(["", "", "\n", " "])
        if kind == 0:
            return "".join(c.upper() if rng.random() < 0.3 else c for c in rng.choice(names))
        if kind == 1:
            n = rng.choice([3, 4, 5, 6, 7, 8, 9])
            return "#" + "".join(rng.choice("0123456789abcdefABCDEFg") for _ in range(n)) + end
        if kind in (2, 3):
            percent = rng.choice(["", "%"])
            n = rng.choice([3, 3, 3, 2, 4])
            # (at most 18 digits: within 64 bits, also in percent and with other digits)
            top = rng.choice([400, 400, 400, 10**17])
            parts = (space() + number(top) + percent + space() for _ in range(n))
            return "rgb(" + ",".join(parts) + ")" + end
        if kind in (4, 5):
            parts = []
            for i in range(3):
                fraction = (
                    rng.choice(["", ".", ".5", "." + number(99)]) if rng.random() < 0.5 else ""
                )
                parts.append(space() + number(400) + fraction + ("%" if i else "") + space())
            return rng.choice(["hsl", "hsv", "hsb", "HSL"]) + "(" + ",".join(parts) + ")" + end
        if kind == 6:
            top = rng.choice([300, 300, 300, 2**31])
            parts = (space() + number(top) + space() for _ in range(rng.choice([4, 4, 3])))
            return "rgba(" + ",".join(parts) + ")"
        if kind == 7:
            return "".join(rng.choice("abcdefrgbhsl(),.%# 0123456789") for _ in range(9))
        if kind == 8:
            return "#" + "a" * rng.randrange(95, 105)
        # (and lower()'s: İ to two characters, the final sigma, ʕ a letter of a case and
        # U+A7CB none yet in Unicode 15.0)
        garbage = ["", " ", "#", "rgb()", "RGB(1,2,3)", "rgb(1, 2, 3, )"]
        return rng.choice([*garbage, "İ", "ΣΑΣ", "ΑΣʕ", "ΑΣ'ʰ", "\ua7cb"])

    return [one() for _ in range(count)]


def test_numpy_s_random_draws_as_numpy_draws_them() -> None:
    """numpy's default_rng(seed) and the draws the renderer asks of it, in turn: doubles,
    normals and exponentials (the ziggurat, its wedges and tails), powers, uniforms and Pareto's,
    each the same double to the bit; float32s and integers, each of them half of a 64-bit draw
    as numpy keeps the other half for the next (whatever is drawn between), the integers by
    Lemire's method, its draws thrown away again where a range is wide (and none where it is
    a power of two) and the range of all 32 bits among them."""
    import numpy as np

    rng = random.Random(0x5EED)
    hexed = lambda x: struct.pack(">d", x).hex()  # noqa: E731
    kinds = ["normal", "exponential", "power", "uniform", "random", "pareto", "float32"]
    # (the widest: draws thrown away about every other time, all of 32 bits, 2**32 - 1 of them;
    # powers of two, where Lemire's method throws none away)
    wide = [(0, 2**31 + 1), (-5, 2**32 - 5), (-(2**31), 2**31 - 1), (0, 2**31), (-(2**29), 3 << 29)]
    ranges = [(1, 4), (1, 3), (0, 1000), (7, 8), *wide]
    programs, expected = [], []
    for seed in [0, 1, 1969, 0x11B7E, 2**32 - 1, *(rng.randrange(2**32) for _ in range(9))]:
        draws: list[list[Any]] = []
        want: list[str] = []
        generator = np.random.default_rng(seed)
        for _ in range(10):
            kind = rng.choice([*kinds, "integers", "integers"])
            n = rng.choice([1, 2, 3, 26, 320, 5000])
            if kind == "normal":
                draws.append([kind, n])
                want += map(hexed, generator.standard_normal(n).tolist())
            elif kind == "exponential":
                draws.append([kind, n])
                want += map(hexed, generator.standard_exponential(n).tolist())
            elif kind == "power":
                a = rng.choice([0.22, 1.0, 3.5, 0.05])
                draws.append([kind, hexed(a), n])
                want += map(hexed, generator.power(a, n).tolist())
            elif kind == "uniform":
                lo, hi = rng.choice([(-0.98, 0.98), (0.0, 1.4), (0.0, 2 * math.pi), (5.0, 5.0)])
                draws.append([kind, hexed(lo), hexed(hi), n])
                want += map(hexed, generator.uniform(lo, hi, n).tolist())
            elif kind == "pareto":
                a = rng.choice([2.6, 1.0, 0.5, 7.0])
                draws.append([kind, hexed(a), n])
                want += map(hexed, generator.pareto(a, n).tolist())
            elif kind == "float32":
                draws.append([kind, n])
                singles = generator.random(n, np.float32).tolist()
                want += [struct.pack(">f", x).hex() for x in singles]
            elif kind == "integers":
                lo, hi = rng.choice(ranges)
                draws.append([kind, lo, hi, n])
                want += map(str, generator.integers(lo, hi, n).tolist())
            else:
                draws.append([kind, n])
                want += map(hexed, generator.random(n).tolist())
        programs.append({"seed": seed, "draws": draws})
        expected.append(want)
    # (and every range in turn, a double drawn between)
    generator = np.random.default_rng(0x1E31)
    draws, want = [], []
    for lo, hi in ranges:
        draws += [["integers", lo, hi, 40], ["random", 1]]
        want += map(str, generator.integers(lo, hi, 40).tolist())
        want += map(hexed, generator.random(1).tolist())
    programs.append({"seed": 0x1E31, "draws": draws})
    expected.append(want)
    [got] = run([{"op": "np_random", "programs": programs}])
    assert got["programs"] == expected


def test_colours_are_read_and_given_as_imagecolor_does_it() -> None:
    from PIL import ImageColor

    colors = _colour_texts(random.Random(0xC010), 3000)
    modes = ["RGB", "RGBA", "RGBa", "L", "LA", "La", "1", "I", "F", "P", "HSV", "PA"]
    cases = [{"op": "getrgb", "colors": colors}]
    cases += [{"op": "getcolor", "colors": colors, "mode": mode} for mode in modes]
    answers = [answer["answers"] for answer in run(cases)]

    def pillow(read: Any, *args: str) -> Any:
        try:
            value = read(*args)
        except ValueError as error:
            return {"error": str(error)}
        return list(value) if isinstance(value, tuple) else [value]

    for i, color in enumerate(colors):
        assert answers[0][i] == pillow(ImageColor.getrgb, color), repr(color)
        for mode, ours in zip(modes, answers[1:], strict=True):
            assert ours[i] == pillow(ImageColor.getcolor, color, mode), (repr(color), mode)

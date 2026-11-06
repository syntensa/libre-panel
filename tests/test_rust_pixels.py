"""rust/pixels against Pillow (docs/RUST.md, stage R1): each operation on random pictures, its
result byte for byte as Pillow's, and refused where Pillow refuses it."""

from __future__ import annotations

import base64
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

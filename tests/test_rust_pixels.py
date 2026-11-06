"""rust/pixels against Pillow (docs/RUST.md, stage R1): each operation on random pictures, its
result byte for byte as Pillow's, and refused where Pillow refuses it."""

from __future__ import annotations

import random
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

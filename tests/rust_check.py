"""lp-check (rust/check): Libre Panel's Rust crates run from the tests, to compare them with the
Python app they replace (docs/RUST.md)."""

from __future__ import annotations

import base64
import json
import os
import random
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def find() -> Path | None:
    """The lp-check that `cargo build --release` in rust/ made (LIBRE_PANEL_CHECK: another)."""
    named = os.environ.get("LIBRE_PANEL_CHECK")
    if named:
        return Path(named)
    name = "lp-check.exe" if os.name == "nt" else "lp-check"
    for profile in ("release", "debug"):
        exe = ROOT / "rust" / "target" / profile / name
        if exe.is_file():
            return exe
    return None


def pillow_libjpeg() -> str | None:
    """The libjpeg Pillow's wheel brings, the one its JPEGs are decoded with (LP_LIBJPEG:
    another); None where it is built into Pillow (Windows' wheels)."""
    import PIL

    if os.environ.get("LP_LIBJPEG"):
        return os.environ["LP_LIBJPEG"]
    here = Path(PIL.__file__).resolve().parent
    found = [*sorted((here.parent / "pillow.libs").glob("libjpeg*.so*")),
             *sorted((here / ".dylibs").glob("libjpeg*.dylib"))]  # fmt: skip
    return str(found[0]) if found else None


def pillow_libfreetype() -> str | None:
    """The FreeType Pillow's wheel brings, the one its letters are drawn with (LP_LIBFREETYPE:
    another); None where it is built into Pillow (Windows' wheels)."""
    import PIL

    if os.environ.get("LP_LIBFREETYPE"):
        return os.environ["LP_LIBFREETYPE"]
    here = Path(PIL.__file__).resolve().parent
    found = [*sorted((here.parent / "pillow.libs").glob("libfreetype*.so*")),
             *sorted((here / ".dylibs").glob("libfreetype*.dylib"))]  # fmt: skip
    return str(found[0]) if found else None


def pillow_libharfbuzz() -> str | None:
    """The HarfBuzz Pillow's wheel brings for libraqm (LP_LIBHARFBUZZ: another), made for its
    FreeType; None where it is built into Pillow."""
    import PIL

    if os.environ.get("LP_LIBHARFBUZZ"):
        return os.environ["LP_LIBHARFBUZZ"]
    here = Path(PIL.__file__).resolve().parent
    found = [*sorted((here.parent / "pillow.libs").glob("libharfbuzz*.so*")),
             *sorted((here / ".dylibs").glob("libharfbuzz*.dylib"))]  # fmt: skip
    return str(found[0]) if found else None


def pillow_libwebp() -> dict[str, str]:
    """libwebp and libwebpdemux as Pillow's wheel brings them, named for lp-check (none where
    they are built into Pillow)."""
    import PIL

    here = Path(PIL.__file__).resolve().parent
    named = {}
    for key, stem in (("LP_LIBWEBP", "libwebp-"), ("LP_LIBWEBPDEMUX", "libwebpdemux")):
        if os.environ.get(key):
            named[key] = os.environ[key]
            continue
        found = [*sorted((here.parent / "pillow.libs").glob(stem + "*.so*")),
                 *sorted((here / ".dylibs").glob(stem + "*.dylib"))]  # fmt: skip
        if found:
            named[key] = str(found[0])
    return named if len(named) == 2 else {}


def image(im: Image.Image) -> dict[str, Any]:
    """An image as lp-check takes it."""
    data = base64.b64encode(im.tobytes()).decode("ascii")
    return {"mode": im.mode, "size": list(im.size), "data": data}


def picture(value: dict[str, Any]) -> Image.Image:
    """An image lp-check gave."""
    return Image.frombytes(value["mode"], tuple(value["size"]), base64.b64decode(value["data"]))


def run(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each case's answer from lp-check, in order."""
    exe = find()
    if exe is None:
        pytest.skip("needs lp-check (cargo build --release in rust/)")
    text = "".join(json.dumps(case) + "\n" for case in cases)
    env = dict(os.environ)
    if (libjpeg := pillow_libjpeg()) is not None:
        env["LP_LIBJPEG"] = libjpeg
    env.update(pillow_libwebp())
    if (libfreetype := pillow_libfreetype()) is not None:
        env["LP_LIBFREETYPE"] = libfreetype
    if (libharfbuzz := pillow_libharfbuzz()) is not None:
        env["LP_LIBHARFBUZZ"] = libharfbuzz
    done = subprocess.run(
        [str(exe)], input=text, capture_output=True, text=True, timeout=900, check=True, env=env
    )
    # (one answer a line: split at "\n" only, as JSON leaves U+2028 and the like as they are)
    answers = [json.loads(line) for line in done.stdout.split("\n") if line]
    assert len(answers) == len(cases), done.stderr
    return answers


def noise(
    rng: random.Random, mode: str, size: tuple[int, int], alpha: str = "mixed"
) -> Image.Image:
    """A picture of random bands; its alpha (RGBA, RGBa) often 0 or 255 and else anything, or
    "valid" for an RGBa whose colours are no more than its alpha (as Pillow makes them)."""
    w, h = size
    if mode == "RGBa" and alpha == "valid":
        return noise(rng, "RGBA", size).convert("RGBa")
    bands = {"L": 1, "RGB": 3, "RGBA": 4, "RGBa": 4}[mode]
    data = bytearray(rng.randbytes(w * h * bands))
    if bands == 4:
        for i in range(3, len(data), 4):
            pick = rng.random()
            data[i] = 0 if pick < 0.25 else 255 if pick < 0.5 else data[i]
    return Image.frombytes(mode, size, bytes(data))

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
    done = subprocess.run(
        [str(exe)], input=text, capture_output=True, text=True, timeout=900, check=True
    )
    answers = [json.loads(line) for line in done.stdout.splitlines()]
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

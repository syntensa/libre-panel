"""Make the earth's maps in src/libre_panel/assets/earth from NASA's pictures.

    python tools/earth_maps.py [--sources FOLDER]

The globe (render/earth.py) is drawn from four maps of the whole earth, 2048 x 1024 px,
longitude -180 to 180 from left to right and latitude 90 to -90 from top to bottom:

- ``day.jpg``: NASA's Blue Marble Next Generation, August 2004, with the sea floor's relief
  (NASA Earth Observatory, Reto Stöckli).
- ``night.jpg``: NASA's Black Marble 2016, the earth at night in colour (NASA Earth
  Observatory, Joshua Stevens, from Suomi NPP VIIRS data by Miguel Román, NASA GSFC).
- ``clouds.jpg``: the clouds of NASA's Blue Marble (2002), as grey levels.
- ``water.png``: where the sea and the lakes are (white), made from the Blue Marble's map of
  land, sea and ice: the glint of the sun is on water only.

NASA's pictures are not under copyright; NASA asks to be named (assets/NOTICE.md). The pictures
are fetched from NASA and checked against the SHA-256 written here; ``--sources`` takes them
from a folder instead.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

SIZE = (2048, 1024)
OUT = Path(__file__).resolve().parent.parent / "src" / "libre_panel" / "assets" / "earth"
BASE = "https://eoimages.gsfc.nasa.gov/images/imagerecords/"
SOURCES = {
    "day": (
        BASE + "73000/73776/world.topo.bathy.200408.3x5400x2700.jpg",
        "f76d7b94445e8975a755c849ddaa93ed07b7e01118fb0b7e6bc5546a68c18d5c",
    ),
    "night": (
        BASE + "144000/144898/BlackMarble_2016_01deg.jpg",
        "d87de751a264e4f8ff69c68de5dab9606daee87a6f15ae743c93200743bd7ec1",
    ),
    "clouds": (
        BASE + "57000/57747/cloud_combined_2048.jpg",
        "daddaad84d7a33bbbc86cdda3f591099f57cee8607b7bcf3b67eb7e4f7a1c793",
    ),
    "land": (
        BASE + "57000/57730/land_ocean_ice_2048.jpg",
        "d4dc80a6ef571939d0abe04a9bed3d3d1e6cd63e59514be1c5e43a6b069e6f1e",
    ),
}


def fetch(name: str, folder: Path | None) -> Image.Image:
    url, sha = SOURCES[name]
    if folder is not None:
        data = (folder / url.rsplit("/", 1)[1]).read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=120) as answer:  # noqa: S310 (NASA's https)
            data = answer.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit(f"{url}: not the picture this tool was made for (SHA-256 differs)")
    return Image.open(io.BytesIO(data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", type=Path, help="a folder with NASA's pictures")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    lanczos = Image.Resampling.LANCZOS
    day = fetch("day", args.sources).convert("RGB").resize(SIZE, lanczos)
    day.save(OUT / "day.jpg", quality=88, optimize=True)
    night = fetch("night", args.sources).convert("RGB").resize(SIZE, lanczos)
    night.save(OUT / "night.jpg", quality=88, optimize=True)
    clouds = fetch("clouds", args.sources).convert("L").resize(SIZE, lanczos)
    clouds.save(OUT / "clouds.jpg", quality=85, optimize=True)
    land = np.asarray(fetch("land", args.sources).convert("RGB").resize(SIZE, lanczos)).astype(int)
    # the sea is blue, its shallows cyan; land is green, brown or white (ice)
    water = (land[..., 2] > land[..., 0] + 25) & (land[..., 2] > land[..., 1] + 8)
    Image.fromarray(water).save(OUT / "water.png", optimize=True)
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}: {path.stat().st_size // 1024} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

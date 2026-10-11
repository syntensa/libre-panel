"""Compare a theme with a reference picture of the same screen, region by region.

    python tools/compare_reference.py REFERENCE.png [--theme spur-classic] [--values values.json]
        [--time "2026-09-28 14:29:59"] [--place LAT,LON] [--layout basic|raqm] [--out DIR]
        [--tolerance 12] [--region NAME=x0,y0,x1,y1 ...] [--zoom 3]

The theme is drawn once, as the panel would show it, at ``--time`` with the
readings in ``--values`` and the sun over ``--place`` (else at noon). Into
``--out`` (a new temporary folder if not given) go ``NAME-side.png``, the
reference above the render, ``NAME-diff.png``, their difference made four
times brighter, and for every region a crop of both enlarged ``--zoom``
times. Printed: the share of pixels whose channels all lie within
``--tolerance`` of the reference, for the whole picture and per region, and
the mean difference. The reference is only read, never copied.

``--layout basic`` (the default) lays text out as Pillow does without
libraqm, as on Windows, where SPUR II drew its references: whole-pixel
advances and no kerning. ``raqm`` keeps what this Pillow has.

``values.json``::

    {"readings": {"cpu.temp": 70, "cpu.load": [29.1, "%"], "cpu.name": "8-Core Processor"},
     "history": {"cpu.load": [12.4, 18.8, 25.7]}}

Readings by key, as a value or ``[value, unit]``; histories oldest first.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from libre_panel.render.renderer import Renderer  # noqa: E402
from libre_panel.sensors.base import Reading, Snapshot  # noqa: E402
from libre_panel.theme.model import find_theme, load_theme  # noqa: E402

# The bands of a 1920 x 480 screen in SPUR II's layout.
BAR_REGIONS = {
    "header": (0, 0, 1920, 86),
    "charts": (0, 86, 1920, 306),
    "sections": (0, 306, 1920, 480),
}


def snapshot(values: dict, when: datetime) -> Snapshot:
    readings = {}
    for key, given in values.get("readings", {}).items():
        value, unit = given if isinstance(given, list) else (given, "")
        readings[key] = Reading(key, value, unit, key)
    history = {key: [float(v) for v in series] for key, series in values.get("history", {}).items()}
    return Snapshot(readings=readings, history=history, now=when)


def render(theme_id: str, values: dict, when: datetime, place, layout: str = "") -> Image.Image:
    """The theme's frame; ``layout`` "basic" lays its text out without libraqm."""
    folder = Path(theme_id)
    theme = load_theme(folder if (folder / "theme.json").is_file() else find_theme(theme_id))
    truetype = ImageFont.truetype
    if layout == "basic":  # for this frame only
        basic = ImageFont.Layout.BASIC
        ImageFont.truetype = lambda *a, **k: truetype(*a, **{"layout_engine": basic, **k})
    renderer = Renderer(theme)
    renderer.place = place
    for warning in renderer.warnings + theme.warnings:
        print(f"warning: {warning}")
    try:
        frame, _ = renderer.render(snapshot(values, when), 0.0)
    finally:
        renderer.close()
        ImageFont.truetype = truetype
    return frame.convert("RGB")


def within(a: np.ndarray, b: np.ndarray, tolerance: int) -> tuple[float, float]:
    """The share of pixels whose channels all lie within ``tolerance``, and the mean difference."""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float((diff.max(-1) <= tolerance).mean()), float(diff.mean())


def stacked(*images: Image.Image, gap: int = 6) -> Image.Image:
    w = max(image.width for image in images)
    out = Image.new("RGB", (w, sum(image.height for image in images) + gap * (len(images) - 1)),
                    (255, 0, 255))  # fmt: skip
    y = 0
    for image in images:
        out.paste(image, (0, y))
        y += image.height + gap
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("reference", type=Path)
    parser.add_argument("--theme", default="spur-classic", help="a theme name or folder")
    parser.add_argument("--values", type=Path, help="readings and histories (JSON)")
    parser.add_argument("--time", default="2026-09-28 14:29:59", help="the panel's time of day")
    parser.add_argument("--place", help="LAT,LON the sun is seen from (default: noon)")
    parser.add_argument("--layout", choices=("basic", "raqm"), default="basic",
                        help="text layout: basic as on Windows (default), or raqm")  # fmt: skip
    parser.add_argument("--out", type=Path, help="where the pictures go")
    parser.add_argument("--tolerance", type=int, default=12, help="levels a channel may differ")
    parser.add_argument("--region", action="append", default=[], help="NAME=x0,y0,x1,y1")
    parser.add_argument("--zoom", type=int, default=3)
    args = parser.parse_args()

    values = json.loads(args.values.read_text(encoding="utf-8")) if args.values else {}
    when = datetime.fromisoformat(args.time)
    place = tuple(float(v) for v in args.place.split(",")) if args.place else None
    frame = render(args.theme, values, when, place, args.layout)
    with Image.open(args.reference) as image:
        reference = image.convert("RGB")
    if reference.size != frame.size:
        sys.exit(f"the reference is {reference.size}, the theme {frame.size}")
    regions = {"all": (0, 0, *frame.size)}
    if frame.size == (1920, 480):
        regions.update(BAR_REGIONS)
    for given in args.region:
        name, box = given.split("=", 1)
        regions[name] = tuple(int(v) for v in box.split(","))

    out = args.out or Path(tempfile.mkdtemp(prefix="compare-"))
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.theme).name
    a, b = np.asarray(reference), np.asarray(frame)
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(-1)
    amplified = Image.fromarray(np.minimum(diff * 4, 255).astype(np.uint8), "L").convert("RGB")
    stacked(reference, frame).save(out / f"{stem}-side.png")
    amplified.save(out / f"{stem}-diff.png")
    print(f"{stem} against {args.reference.name}, tolerance {args.tolerance}:")
    for name, (x0, y0, x1, y1) in regions.items():
        share, mean = within(a[y0:y1, x0:x1], b[y0:y1, x0:x1], args.tolerance)
        print(f"  {name:10s} {share * 100:6.2f} % within, mean difference {mean:5.2f}")
        if name == "all" or args.zoom <= 1:
            continue
        size = ((x1 - x0) * args.zoom, (y1 - y0) * args.zoom)
        crops = [im.crop((x0, y0, x1, y1)).resize(size, Image.Resampling.NEAREST)
                 for im in (reference, frame)]  # fmt: skip
        sheet = stacked(*crops)
        ImageDraw.Draw(sheet).text((4, 4), f"{name}: reference / render", fill=(255, 0, 255))
        sheet.save(out / f"{stem}-{name}-x{args.zoom}.png")
    print(f"pictures in {out}")


if __name__ == "__main__":
    main()

"""The colour a cover carries: what a card's frame takes on while a track plays.

Not the average (every cover averages to a brownish grey): the picture is reduced to a few
colours and the one that joins how much of it there is with how colourful it is wins, so a
small strong area carries a cover more than a large grey ground. Too dark and too pale
candidates are left out; a cover of nothing but greys has none. A colour that is too dark to
read as a thin line on a dark panel is brightened until it is not.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from PIL import Image

SIDE = 48  # the cover is looked at this small
COLOURS = 8
DARKEST = 60  # a candidate whose brightest channel is below this carries nothing
GREYEST = 0.18  # ... and one less colourful than this is grey
BRIGHT = 180  # the winner is brightened to this, as a line on dark ground
KEPT = 8

_kept: OrderedDict[int, tuple[Any, tuple[int, int, int] | None]] = OrderedDict()


def dominant(image: Image.Image | None) -> tuple[int, int, int] | None:
    """The colour that carries ``image``, or None (no picture, or only greys). Worked out
    once per picture: a track's cover is the same object for as long as it plays."""
    if image is None:
        return None
    hit = _kept.get(id(image))
    if hit is not None and hit[0] is image:  # (the picture is held, so its id stays its own)
        _kept.move_to_end(id(image))
        return hit[1]
    found = _find(image)
    _kept[id(image)] = (image, found)
    while len(_kept) > KEPT:
        _kept.popitem(last=False)
    return found


def _find(image: Image.Image) -> tuple[int, int, int] | None:
    try:
        small = image.convert("RGB").resize((SIDE, SIDE), Image.Resampling.BILINEAR)
        few = small.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT).convert("RGB")
        best, score = None, 0.0
        for count, (r, g, b) in few.getcolors(SIDE * SIDE) or []:
            top = max(r, g, b)
            if top < DARKEST:
                continue
            colourful = (top - min(r, g, b)) / max(1, top)
            if colourful < GREYEST:
                continue
            value = (count / (SIDE * SIDE)) ** 0.5 * colourful
            if value > score:
                best, score = (r, g, b), value
    except (OSError, ValueError):  # a picture Pillow cannot read
        return None
    if best is None:
        return None
    top = max(best)
    if top < BRIGHT:
        k = BRIGHT / top
        best = (min(255, int(best[0] * k)), min(255, int(best[1] * k)), min(255, int(best[2] * k)))
    return best

"""Where the panels stand (Settings → Panel → More panels), and one picture across all of them.

Each panel's picture has a size in millimetres, from its model's diagonal and pixels, the way it
is mounted, and a place where the person put it (config.toml ``at = [x, y]``: the top left
corner, in millimetres). A picture across the panels (``across``) covers the panels that are
placed as one: each shows its own part of it, by where it stands, under its theme, instead of
the theme's background picture.

The picture is kept in the settings folder (``across/picture.jpg``), made again from what the
person gave: at most :data:`MAX_SIDE` pixels on its long side, without what the file said
about itself (where it was taken, by which camera).
"""

from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from libre_panel.i18n import t

MM_PER_INCH = 25.4
ACROSS_FILE = "across/picture.jpg"  # in the settings folder
MAX_SIDE = 4096  # a picture across the panels is kept at most this large
MAX_BYTES = 40 * 1024 * 1024  # what may be given
MAX_PIXELS = 80_000_000  # (a picture that says it is larger is refused before it is read)

Rect = tuple[float, float, float, float]  # x, y, width, height in millimetres


class PictureError(ValueError):
    pass


def millimetres(model: Any, size: tuple[int, int] | None = None) -> tuple[float, float] | None:
    """The size of ``model``'s picture in millimetres, as mounted: ``size`` (its frames in
    pixels, turned as the panel is) or its landscape size. None for a model not known."""
    if model is None or not model.size_inch:
        return None
    per = model.size_inch * MM_PER_INCH / math.hypot(model.native_width, model.native_height)
    width, height = size or model.size("landscape")
    return width * per, height * per


def rect(at: tuple[int, int] | None, model: Any, size: tuple[int, int] | None) -> Rect | None:
    """Where a panel's picture is, in millimetres: at its place, in its size; None while it is
    not placed or its size is not known."""
    mm = millimetres(model, size)
    if at is None or mm is None:
        return None
    return float(at[0]), float(at[1]), mm[0], mm[1]


def part(picture: Image.Image, rects: list[Rect | None], index: int) -> Image.Image | None:
    """The part of ``picture`` the panel ``rects[index]`` shows: the picture covers all the
    panels that are placed (as large as it must be, its middle at theirs), and each shows what
    lies where it stands. None for a panel that is not placed."""
    own = rects[index] if 0 <= index < len(rects) else None
    placed = [r for r in rects if r is not None]
    if own is None:
        return None
    left = min(r[0] for r in placed)
    top = min(r[1] for r in placed)
    width = max(r[0] + r[2] for r in placed) - left
    height = max(r[1] + r[3] for r in placed) - top
    per = min(picture.width / width, picture.height / height)  # pixels a millimetre: it covers
    shift_x = (picture.width / per - width) / 2  # (millimetres of it beyond the panels)
    shift_y = (picture.height / per - height) / 2
    x, y, w, h = own
    box = (
        (x - left + shift_x) * per,
        (y - top + shift_y) * per,
        (x - left + shift_x + w) * per,
        (y - top + shift_y + h) * per,
    )
    return picture.crop(tuple(round(v) for v in box))  # type: ignore[arg-type]


def across_path(across: str, config_dir: Path) -> Path | None:
    """The file ``across`` names: a path of its own, or one in the settings folder."""
    if not across:
        return None
    path = Path(across).expanduser()
    return path if path.is_absolute() else config_dir / path


@lru_cache(maxsize=2)
def _load(path: str, stamp: int) -> Image.Image | None:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def load(path: Path | None) -> tuple[Image.Image, int] | None:
    """The picture across the panels and when its file was last written; None without one, or
    with one that cannot be read."""
    if path is None:
        return None
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    picture = _load(str(path), stamp)
    return (picture, stamp) if picture is not None else None


def save_picture(data: bytes, config_dir: Path) -> str:
    """Keep ``data`` (a picture's file: JPEG, PNG, WebP, ...) as the picture across the
    panels: made again, at most :data:`MAX_SIDE` on its long side, without its metadata.
    What config.toml names it by (``across``); :class:`PictureError` when it is no picture."""
    if len(data) > MAX_BYTES:
        raise PictureError(t("the picture is larger than {size} MB", size=MAX_BYTES // 1048576))
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise PictureError(t("the picture has too many pixels"))
            image = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, PictureError):
            raise
        raise PictureError(t("this is not a picture that can be read")) from exc
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    target = config_dir / ACROSS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    kept = target.with_suffix(".tmp")
    image.save(kept, "JPEG", quality=92, optimize=True)  # (nothing of the file's own metadata)
    kept.replace(target)
    return ACROSS_FILE

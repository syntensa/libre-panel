"""Where the panels stand (Settings → Panels), and one picture across all of them.

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
GAP_MM = 30  # between two panels not placed yet, as the app lays them out
GUESS_MM_PER_PX = 0.118  # a panel of a model not known: as dense as the 9.2" bar


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


def panel_rect(device: Any, status: Any = None, guess: bool = False) -> Rect | None:
    """Where the panel of ``device`` (``[device]`` or a ``[[panels]]`` table's) stands, in the
    size of its model as set, else as the panel said it is (``status``, its RunStatus: the
    model and the size of its frames); None while it is not placed, or of a size not known.
    ``guess``: a panel of a model not known that says the size of its frames is taken as
    dense as the 9.2" bar, and one not placed stands at 0, 0 (:func:`laid_out` moves it)."""
    from libre_panel.devices.models import find_model

    told = getattr(status, "model", "") or ""
    model = find_model(device.model) or (find_model(told) if told else None)
    way = "portrait" if device.orientation == "portrait" else "landscape"
    size = getattr(status, "size", None) or (model.size(way) if model else None)
    at = device.at if device.at is not None or not guess else (0, 0)
    found = rect(at, model, size)
    if found is None and guess and at is not None and size is not None:
        found = (float(at[0]), float(at[1]), size[0] * GUESS_MM_PER_PX, size[1] * GUESS_MM_PER_PX)
    return found


def places(config: Any, statuses: list[Any]) -> list[Rect | None]:
    """Where every panel stands (:func:`panel_rect`): the first panel's first, then those of
    ``[[panels]]``; ``statuses`` the RunStatus of each in that order (None: it does not run)."""
    devices = [config.device, *(panel.device for panel in config.panels)]
    statuses = list(statuses) + [None] * (len(devices) - len(statuses))
    return [panel_rect(device, status) for device, status in zip(devices, statuses, strict=False)]


def laid_out(config: Any, statuses: list[Any]) -> list[Rect | None]:
    """Every panel where it is shown to stand: its place, or, not placed, in a row with the
    others not placed, beside those that are (as Settings → Panels lays them
    out); None for one whose size is not known at all."""
    devices = [config.device, *(panel.device for panel in config.panels)]
    statuses = list(statuses) + [None] * (len(devices) - len(statuses))
    sized = [panel_rect(d, s, guess=True) for d, s in zip(devices, statuses, strict=False)]
    placed = [r for r, d in zip(sized, devices, strict=False) if r is not None and d.at]
    loose = [r for r, d in zip(sized, devices, strict=False) if r is not None and not d.at]
    row = sum(r[2] for r in loose) + GAP_MM * max(0, len(loose) - 1)
    x = max(r[0] + r[2] for r in placed) + GAP_MM if placed else -round(row / 2)
    y = min(r[1] for r in placed) if placed else 0.0
    shown: list[Rect | None] = []
    for found, device in zip(sized, devices, strict=False):
        if found is None or device.at:
            shown.append(found)
            continue
        shown.append((x, y, found[2], found[3]))
        x += found[2] + GAP_MM
    return shown


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


PHOTO_MAX_SIDE = 4000  # a picture of all panels is at most this large
PHOTO_MARK = "Libre Panel"


def photo(panels: list[tuple[Rect, Image.Image | None, bool, int]]) -> Image.Image | None:
    """One picture of the panels where they stand, to share: each ``(rect, frame, round,
    number)`` (its place, the frame it shows or None, whether it is round, its number as
    *Which is which?* gives it), in a dark frame on a dark ground, as large as the densest of
    them shows its frame (at most :data:`PHOTO_MAX_SIDE`). None without a panel."""
    from PIL import ImageDraw, ImageFilter, ImageFont

    from libre_panel.fonts import builtin_font_path

    if not panels:
        return None
    per = max((f.width / r[2] for r, f, _, _ in panels if f is not None), default=4.0)
    left = min(r[0] for r, *_ in panels)
    top = min(r[1] for r, *_ in panels)
    width = max(r[0] + r[2] for r, *_ in panels) - left
    height = max(r[1] + r[3] for r, *_ in panels) - top
    margin = max(width, height) * 0.08 + 12
    longest = (max(width, height) + 2 * margin) * per
    if longest > PHOTO_MAX_SIDE:
        per *= PHOTO_MAX_SIDE / longest
    size = (round((width + 2 * margin) * per), round((height + 2 * margin) * per))
    canvas = Image.new("RGB", size, (11, 14, 19))
    ground = ImageDraw.Draw(canvas)
    for y in range(size[1]):  # a little lighter towards the bottom
        share = y / max(1, size[1] - 1)
        ground.line([(0, y), (size[0], y)], fill=(11 + round(9 * share), 14 + round(11 * share),
                                                  19 + round(15 * share)))  # fmt: skip
    path = builtin_font_path("builtin:Barlow-Medium")
    for (x_mm, y_mm, w_mm, h_mm), frame, round_, number in panels:
        x, y = round((x_mm - left + margin) * per), round((y_mm - top + margin) * per)
        w, h = max(1, round(w_mm * per)), max(1, round(h_mm * per))
        bezel = max(6, round(min(w, h) * 0.035))
        outer = (x - bezel, y - bezel, x + w + bezel, y + h + bezel)
        shadow = Image.new("L", size, 0)
        drawn = ImageDraw.Draw(shadow)
        shifted = (outer[0], outer[1] + bezel, outer[2], outer[3] + bezel)
        if round_:
            drawn.ellipse(shifted, fill=150)
        else:
            drawn.rounded_rectangle(shifted, radius=bezel * 2, fill=150)
        shadow = shadow.filter(ImageFilter.GaussianBlur(bezel * 1.5))
        canvas.paste((0, 0, 0), (0, 0), shadow)
        body = ImageDraw.Draw(canvas)
        if round_:
            body.ellipse(outer, fill=(6, 8, 11), outline=(42, 49, 60), width=max(1, bezel // 4))
        else:
            body.rounded_rectangle(outer, radius=bezel * 2, fill=(6, 8, 11), outline=(42, 49, 60),
                                   width=max(1, bezel // 4))  # fmt: skip
        if frame is not None:
            screen = frame.convert("RGB").resize((w, h), Image.LANCZOS)
        else:  # it shows nothing now: its number
            screen = Image.new("RGB", (w, h), (14, 17, 22))
            if path:
                font = ImageFont.truetype(str(path), max(10, round(min(w, h) * 0.3)))
                ImageDraw.Draw(screen).text((w / 2, h / 2), str(number), font=font,
                                            fill=(70, 80, 95), anchor="mm")  # fmt: skip
        mask = None
        if round_:
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, w - 1, h - 1), fill=255)
        canvas.paste(screen, (x, y), mask)
    if path:
        font = ImageFont.truetype(str(path), max(12, round(size[1] * 0.028)))
        gap = round(margin * per * 0.35)
        ImageDraw.Draw(canvas).text((size[0] - gap, size[1] - gap), PHOTO_MARK, font=font,
                                    fill=(120, 130, 146), anchor="rs")  # fmt: skip
    return canvas

"""Light baked in Blender, colour from the theme: SPUR II's weather symbols.

Blender rendered the bodies of the weather symbols once, without colour
(``assets/light3d/weather``): a sphere, the sun's rays, a cloud, a drop and a
bolt. Each comes as three grey layers lit from the top left: ``shape`` (the
diffuse light), ``core`` (bright in the middle of a body, dark at its rims)
and ``gloss`` (the reflection). Here they are coloured with a widget's colour
and fitted to its size, so nothing 3D runs while drawing and a new colour
needs no new render.

Preparing the layers from the PNGs takes a few hundred milliseconds, far too
long for a frame (SPUR II froze for 118 ms on its first weather change):
:class:`Part` keeps them as ``.npz`` in the cache folder and prepares them
again when a source PNG is newer, and :func:`preload` loads them in a thread
of its own before the first frame.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from libre_panel.config import config_dir

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "light3d"
LAYERS = ("shape", "core", "gloss")
CORE_MIN = 0.596  # the core layer at the rim of a body (measured on SPUR II's ring)

# Weather bodies by kind; ``glow`` lights the middle towards white (sun, bolt).
WEATHER = {
    "sun": {"ambient": 0.75, "diffuse": 0.25, "core": 0.25, "glow": 0.35, "gloss": 0.30},
    "cloud": {"ambient": 0.45, "diffuse": 0.65, "core": 0.20, "glow": 0.0, "gloss": 0.12},
    "moon": {"ambient": 0.50, "diffuse": 0.50, "core": 0.30, "glow": 0.0, "gloss": 0.45},
    "glass": {"ambient": 0.45, "diffuse": 0.45, "core": 0.35, "glow": 0.0, "gloss": 0.90},
    "bolt": {"ambient": 0.70, "diffuse": 0.30, "core": 0.20, "glow": 0.30, "gloss": 0.50},
}
BODIES = ("sphere", "rays", "cloud", "drop", "bolt")


# -- preparing the layers ------------------------------------------------------------


def _read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as image:
        data = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255
    return data[..., :3].mean(-1), data[..., 3]


def _layers(folder: Path, stem: str) -> np.ndarray:
    """``stem``'s shape, core and gloss (normalised, premultiplied by its
    coverage) and the coverage: (4, h, w)."""
    shape, alpha = _read(folder / f"{stem}_shape.png")
    core, _ = _read(folder / f"{stem}_core.png")
    gloss, _ = _read(folder / f"{stem}_gloss.png")
    core = np.clip((core - CORE_MIN) / (1 - CORE_MIN), 0, 1)
    return np.stack([shape * alpha, core * alpha, gloss * alpha, alpha]).astype(np.float32)


def _plain(samples: np.ndarray) -> np.ndarray:
    """Shape, core and gloss without the coverage they were premultiplied with."""
    return np.clip(samples[:3] / np.maximum(samples[3], 1e-4), 0, 1).astype(np.float32)


def _weather_build(folder: Path) -> dict[str, np.ndarray]:
    # at the size Blender made them, so that a big symbol keeps every detail of its light;
    # in half floats, which is half the memory and more than the eye can tell apart
    return {name: _layers(folder, name).astype(np.float16) for name in BODIES}


class Part:
    """The prepared layers of one part (``ring``, ``bar``, ``weather``), loaded
    once per process; thread-safe.

    The tables are kept as ``<name>.npz`` in ``cache/light3d`` of the config
    folder and prepared again when a source PNG is newer (or the format
    changed). ``get`` returns None when the files are missing or broken: the
    caller draws flat.
    """

    VERSION = 2  # 2: the weather bodies at full size, in half floats

    def __init__(
        self,
        name: str,
        stems: tuple[str, ...],
        build: Callable[[Path], dict[str, np.ndarray]],
        folder: Path = ASSETS,
    ) -> None:
        self.name, self.build = name, build
        self.folder = Path(folder) / name
        self.sources = [self.folder / f"{stem}_{layer}.png" for stem in stems for layer in LAYERS]
        self._data: dict[str, np.ndarray] | None = None
        self._missing = False
        self._lock = threading.Lock()

    @property
    def cache(self) -> Path:
        return config_dir() / "cache" / "light3d" / f"{self.name}.npz"

    @property
    def loaded(self) -> bool:
        return self._data is not None or self._missing

    def get(self) -> dict[str, np.ndarray] | None:
        if not self.loaded:
            with self._lock:
                if not self.loaded:
                    self._data = self._load()
                    self._missing = self._data is None
        return self._data

    def reset(self) -> None:
        """Forget what was loaded (tests; the next ``get`` loads again)."""
        with self._lock:
            self._data, self._missing = None, False

    def _load(self) -> dict[str, np.ndarray] | None:
        try:
            newest = max(path.stat().st_mtime for path in self.sources)
        except OSError:
            log.info("light layers %r are missing: drawn flat", self.name)
            return None
        data = self._read_cache(newest)
        if data is not None:
            return data
        try:
            data = self.build(self.folder)
        except Exception as exc:  # drawn flat
            log.warning("light layers %r could not be prepared: %s", self.name, exc)
            return None
        self._write_cache(data)
        return data

    def _read_cache(self, newest: float) -> dict[str, np.ndarray] | None:
        try:
            if self.cache.stat().st_mtime < newest:
                return None  # a source is newer
            with np.load(self.cache) as stored:
                if int(stored["version"]) != self.VERSION:
                    return None
                return {key: stored[key] for key in stored.files if key != "version"}
        except (OSError, ValueError, KeyError):
            return None

    def _write_cache(self, data: dict[str, np.ndarray]) -> None:
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            part = self.cache.with_name(f"{self.name}.{os.getpid()}.tmp")
            with open(part, "wb") as file:
                np.savez(file, version=np.array(self.VERSION), **data)
            os.replace(part, self.cache)  # whole or not at all, also for another process
        except OSError as exc:
            log.info("light layers %r not cached: %s", self.name, exc)


WEATHER_BODIES = Part("weather", BODIES, _weather_build)
PARTS = {"weather": WEATHER_BODIES}


def preload(names: tuple[str, ...] | None = None) -> None:
    """Load the parts (all, or ``names``) now: in a thread of its own before
    the first frame, so no frame waits for them."""
    for name in names or tuple(PARTS):
        PARTS[name].get()


# -- looking up and colouring ----------------------------------------------------------


def tint(shade: np.ndarray, colour: Any, mix: dict[str, float]) -> np.ndarray:
    """RGB (n, 3, 0 to 1) of the shading ``shade`` (3, n) in ``colour``: RGB 0 to
    1, one (3) or one per point (n, 3), lit as ``mix`` says. Worked out channel
    by channel: a maximum over three values per pixel is slow as an axis of
    its own."""
    shape, core, gloss = shade
    c = np.asarray(colour, np.float32)
    c = c[:, None] if c.ndim == 1 else c.T  # (3, 1) or (3, n)
    light = (mix["ambient"] + mix["diffuse"] * shape) * ((1 - mix["core"]) + mix["core"] * core)
    rgb = c * light
    peak = np.maximum(np.maximum(rgb[0], rgb[1]), rgb[2])
    rgb = rgb / np.maximum(1.0, peak)  # pale colours would run to white
    if mix.get("glow"):
        rgb = rgb + mix["glow"] * core**3 * (1 - rgb)
    rgb = rgb + mix["gloss"] * gloss
    return np.clip(rgb, 0, 1).T


def rgba(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """RGB and coverage (0 to 1) as uint8 RGBA."""
    out = np.concatenate([rgb * 255 + 0.5, alpha[..., None] * 255 + 0.5], -1)
    return np.clip(out, 0, 255).astype(np.uint8)


def unit_colour(color: tuple[int, ...]) -> np.ndarray:
    return np.asarray(color[:3], np.float32) / 255


class Kept:
    """Finished pictures by key, the most recently used kept up to ``budget``
    bytes; thread-safe (the render thread and the helper thread draw)."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self._items: OrderedDict[Hashable, Any] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable, make: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return hit
        made = make()
        with self._lock:
            if key not in self._items:
                self._items[key] = made
                self._size += _bytes(made)
            while self._size > self.budget and len(self._items) > 1:
                _key, old = self._items.popitem(last=False)
                self._size -= _bytes(old)
        return made

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0


def _bytes(value: Any) -> int:
    if isinstance(value, Image.Image):
        return value.width * value.height * len(value.getbands())
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, tuple):
        return sum(_bytes(v) for v in value) + 64
    return 1024  # tables of arrays and the like: small next to pictures


# -- weather bodies ------------------------------------------------------------------------


def body(name: str, kind: str, colour: np.ndarray, size: tuple[int, int],
         centre: tuple[float, float], width: float) -> Image.Image | None:  # fmt: skip
    """Weather body ``name`` lit as ``kind`` (:data:`WEATHER`) in ``colour``
    (RGB 0 to 1) on a clear picture of ``size``: its square picture centred
    at ``centre`` (fractions allowed), ``width`` pixels wide. Averaged as an
    area, so small symbols have no steps. None without the layers."""
    data = WEATHER_BODIES.get()
    if data is None:
        return None
    w, h = int(size[0]), int(size[1])
    source = data[name]
    q = source.shape[1] / width  # source pixels per pixel
    fx0, fy0 = centre[0] - width / 2, centre[1] - width / 2
    x0, y0 = max(0, math.floor(fx0)), max(0, math.floor(fy0))
    x1, y1 = min(w, math.ceil(fx0 + width)), min(h, math.ceil(fy0 + width))
    out = np.zeros((h, w, 4), np.uint8)
    if x1 <= x0 or y1 <= y0:
        return Image.fromarray(out, "RGBA")
    margin = math.ceil(q) + 2
    box = (margin + (x0 - fx0) * q, margin + (y0 - fy0) * q,
           margin + (x1 - fx0) * q, margin + (y1 - fy0) * q)  # fmt: skip
    layers = np.stack([
        np.asarray(Image.fromarray(np.pad(source[i].astype(np.float32), margin), "F").resize(
            (x1 - x0, y1 - y0), Image.Resampling.BOX, box=box))
        for i in range(4)
    ])  # fmt: skip
    rgb = tint(_plain(layers).reshape(3, -1), colour, WEATHER[kind])
    out[y0:y1, x0:x1] = rgba(rgb.reshape(y1 - y0, x1 - x0, 3), np.clip(layers[3], 0, 1))
    return Image.fromarray(out, "RGBA")


def crescent(image: Image.Image, cx: float, cy: float, r: float) -> Image.Image:
    """``image`` (a lit disc of radius ``r`` at cx, cy) cut to the moon's
    crescent, as the flat moon icon: an offset disc takes the rest."""
    s = 4
    w, h = image.size
    mask = Image.new("L", (w * s, h * s), 0)
    draw = ImageDraw.Draw(mask)
    R, X, Y = r * s, cx * s, cy * s
    draw.ellipse([X - R, Y - R, X + R, Y + R], fill=255)
    draw.ellipse([X - 0.45 * R, Y - 1.15 * R, X + 1.55 * R, Y + 0.85 * R], fill=0)
    mask = mask.reduce(s)
    out = image.copy()
    out.putalpha(Image.fromarray(
        (np.asarray(image.getchannel("A"), np.float32) * np.asarray(mask, np.float32) / 255)
        .astype(np.uint8)))  # fmt: skip
    return out

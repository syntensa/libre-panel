"""A frame as its layers, for an engine that composes them (stage 2 of docs/ROADMAP.md).

The renderer composes every frame from its pieces (Renderer._compose). Where Libre Panel's
engine has the panel, it can do that itself: it is sent each piece's layers once and then, for
every frame, only the order they lie in (devices/engine.py's :class:`SceneSender`), and it
composes the frame anew where a layer came or went, pixel for pixel as the renderer would
(engine/src/scene.rs). Such a frame is a :class:`SceneFrame`: a picture whose pixels are only
made when something asks for them (the app's picture of the panel, a transition that starts
from it), composed then as the renderer composes them (:func:`compose`).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from libre_panel.render import moving

if TYPE_CHECKING:
    from libre_panel.render.renderer import Piece


@dataclass(frozen=True, eq=False)
class Scene:
    """A frame of ``size``: ``ground``, the theme's colour (RGB) or its picture (RGB, the frame's
    size), with ``pieces`` (widget id, piece) laid on it in turn; ``half``: turned half a turn,
    as for a panel mounted upside down (app.turned). Its pieces are never changed: the
    renderer hands out new ones."""

    size: tuple[int, int]
    ground: tuple[int, int, int] | Image.Image
    pieces: tuple[tuple[str, Piece], ...]
    half: bool = False


def layerable(piece: Piece) -> bool:
    """Whether the engine composes ``piece`` as the renderer does: no frosted glass beneath it
    (the engine has no blur of what lies under a piece), and layers it takes as they are."""
    if piece.backdrop is not None:
        return False
    layer = piece.layer
    if piece.add:
        return layer.mode in ("RGBA", "RGB", "L")
    if moving.solid(layer):
        ok = layer.mode in ("RGBA", "RGB")
    else:
        ok = layer.mode == "RGBA"
    light = piece.light
    return ok and (light is None or light.layer.mode in ("RGBA", "RGB", "L"))


def compose(scene: Scene) -> Image.Image:
    """The frame of ``scene`` (RGB), as the renderer composes it: its ground with each piece
    laid on it in turn (Renderer._composite onto the opaque frame)."""
    from libre_panel.render.renderer import Renderer

    ground = scene.ground
    if isinstance(ground, Image.Image):
        canvas = ground.copy()
    else:
        canvas = Image.new("RGB", scene.size, ground)
    for _wid, piece in scene.pieces:
        Renderer._composite(canvas, piece, (0, 0), True)
    if scene.half:
        canvas = canvas.transpose(Image.Transpose.ROTATE_180)
    return canvas


class Lazy(Image.Image):
    """A picture of ``size`` in ``mode`` whose pixels are made the first time anything asks for
    them (``make``, a picture of that size and mode), from any thread, once. Copies of it are
    pictures like any other."""

    def __init__(self, size: tuple[int, int], mode: str, make: Any) -> None:
        super().__init__()
        self._mode, self._size = mode, size
        self._make = make
        self._making = threading.Lock()

    @property  # type: ignore[override]
    def im(self) -> Any:
        if self._im is None:
            self.load()
        return super().im

    @im.setter
    def im(self, im: Any) -> None:
        self._im = im

    @property
    def composed(self) -> bool:
        """Whether its pixels were made (something asked for them)."""
        return self._im is not None

    def load(self) -> Any:
        if self._im is None:
            with self._making:
                if self._im is None:
                    made = self._make()
                    self._make = None  # (what it was made of may go)
                    self._im = made.im
        return super().load()


class Again(Lazy):
    """A :class:`Lazy` whose pixels may go again (:meth:`forget`), made anew (the same) when
    something asks for them once more; ``used``: when that was last (monotonic seconds). A card
    an engine lays without its pixels keeps them only while frames composed here, or the app's
    picture of the panel, ask for them (render/scene.py's Filled)."""

    def __init__(self, size: tuple[int, int], mode: str, make: Any) -> None:
        super().__init__(size, mode, make)
        self._again = make
        self.used = 0.0

    @property  # type: ignore[override]
    def im(self) -> Any:
        im = self._im
        while im is None:  # (made, or made again where it went meanwhile)
            self.load()
            im = self._im
        self.used = time.monotonic()
        return im

    @im.setter
    def im(self, im: Any) -> None:
        self._im = im

    def load(self) -> Any:
        with self._making:  # (its pixels stay while Pillow looks at them here)
            if self._im is None:
                self._im = self._again().im
            return Image.Image.load(self)

    def forget(self) -> int:
        """Its pixels go, where they were made: the bytes they took (0: none were made)."""
        with self._making:
            if self._im is None:
                return 0
            self._im = None
        return self.width * self.height * len(self.mode)


Cut = tuple[Image.Image, int, int, int, int, int, int]  # (picture, x, y in it, x, y, w, h)


@dataclass(frozen=True, eq=False)
class View:
    """How an engine cuts a piece's picture ``layer`` from pictures it keeps (engine/src/scene.rs's
    view): ``parts`` (picture, x and y in it, x and y in the piece's, w, h), each pasted in turn
    on a transparent picture of the piece's size, nothing where it lies off its picture (as
    Pillow crops and pastes). A scrolling graph's window is cut so from its strip, every frame
    anew; the strip goes to the engine once."""

    layer: Image.Image
    parts: tuple[Cut, ...]


def viewed(size: tuple[int, int], parts: list[Cut] | tuple[Cut, ...]) -> View:
    """The :class:`View` of a picture of ``size`` cut from ``parts``, its ``layer`` made here
    only when something asks for its pixels (a frame composed here)."""
    parts = tuple(parts)

    def make() -> Image.Image:
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        for image, sx, sy, dx, dy, w, h in parts:
            out.paste(image.crop((sx, sy, sx + w, sy + h)), (dx, dy))
        return out

    return View(Lazy(size, "RGBA", make), parts)


def cut_columns(view: View, left: int, right: int) -> View:
    """``view`` cut to its columns from ``left`` up to ``right`` (a graph without its glow
    beside its box: Renderer's _clip_sides), its pixels not made either."""
    parts = []
    for image, sx, sy, dx, dy, w, h in view.parts:
        a, b = max(dx, left), min(dx + w, right)
        if b > a:
            parts.append((image, sx + a - dx, sy, a - left, dy, b - a, h))
    return viewed((right - left, view.layer.height), parts)


@dataclass(frozen=True, eq=False)
class Globe:
    """How an engine makes a turning globe's picture ``layer`` itself (engine/src/globe.rs), as
    render/earth.py's Turning.picture_of makes it: from ``turning``'s tables (where each pixel
    of its disc lies on its map, sent once), its ``map`` (the lit map as a picture, sent once a
    light), at ``step`` of its turn, with ``air`` over it (sent every two degrees) and ``mark``
    (your place's: a small picture and where it lies) over that."""

    layer: Image.Image
    turning: Any
    map: Image.Image
    step: int
    air: Image.Image | None
    mark: tuple[Image.Image, tuple[int, int]] | None


@dataclass(frozen=True, eq=False)
class Filled:
    """How an engine lays a piece whose inside is all one colour (engine/src/scene.rs's
    Layer::filled): ``fill`` (x, y, w, h in the piece, and its RGBA) as that colour alone, and
    ``parts`` (picture, x and y in the piece) round it as their pixels: together all of the
    piece, none over another, so laid in turn they are the piece. Its ``layer`` is made here
    only when something asks for its pixels (a frame composed here), the inside filled and the
    parts pasted: the piece's pixels, which go again when nothing asked for them a while
    (:class:`Again`, Renderer._forget_fills)."""

    layer: Image.Image
    fill: tuple[int, int, int, int, tuple[int, int, int, int]]
    parts: tuple[tuple[Image.Image, int, int], ...]


# a piece smaller than this (px) goes as its pixels: the parts round its inside would be most of it
FILLED_LEAST = 160 * 160


def filled(image: Image.Image, inset: int) -> Filled | None:
    """``image`` (RGBA) as a :class:`Filled`, where all of it ``inset`` px in from its edges is
    one colour (a card's inside, its corners and outline within the inset); else None. A
    Lumen card on the 12.3" keeps a sixth of its bytes so (the strips along its edges)."""
    w, h = image.size
    if image.mode != "RGBA" or inset < 1 or min(w, h) < 2 * inset + 32 or w * h < FILLED_LEAST:
        return None
    a = np.asarray(image)
    inside = a[inset : h - inset, inset : w - inset]
    colour = inside[0, 0]
    if not (inside == colour).all():
        return None
    rgba = (int(colour[0]), int(colour[1]), int(colour[2]), int(colour[3]))
    parts = (
        (image.crop((0, 0, w, inset)), 0, 0),
        (image.crop((0, h - inset, w, h)), 0, h - inset),
        (image.crop((0, inset, inset, h - inset)), 0, inset),
        (image.crop((w - inset, inset, w, h - inset)), w - inset, inset),
    )

    def make() -> Image.Image:
        out = Image.new("RGBA", (w, h), rgba)
        for part, x, y in parts:
            out.paste(part, (x, y))
        return out

    return Filled(Again((w, h), "RGBA", make), (inset, inset, w - 2 * inset, h - 2 * inset, rgba),
                  parts)  # fmt: skip


Placed = tuple[Image.Image, int, int]  # (picture, x, y where its top left lies)


@dataclass(frozen=True, eq=False)
class Laid:
    """How an engine lays a piece's picture ``layer`` itself (engine/src/scene.rs's laid):
    ``parts`` (picture, x, y), each laid over those before it on a clear picture of the layer's
    size, as Pillow's alpha_composite lays it (a fan's ring, its rotor over it); ``kept``,
    pictures the engine keeps with it besides, unused (the rotor's other positions: each goes to
    the engine once, not with every frame that shows it). Its ``layer`` is made here only when
    something asks for its pixels (a frame composed here)."""

    layer: Image.Image
    parts: tuple[Placed, ...]
    kept: tuple[Image.Image, ...] = ()


def laid(
    size: tuple[int, int],
    parts: list[Placed] | tuple[Placed, ...],
    kept: tuple[Image.Image, ...] = (),
) -> Laid:
    """The :class:`Laid` of a picture of ``size`` laid from ``parts``, its pixels not made."""
    parts = tuple(parts)

    def make() -> Image.Image:
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        for image, x, y in parts:
            out.alpha_composite(image, (x, y))
        return out

    return Laid(Lazy(size, "RGBA", make), parts, tuple(kept))


def turned_globe(turning: Any, step: int, place: tuple[float, float] | None) -> Globe:
    """The :class:`Globe` of ``turning`` at ``step`` (its air and mark worked out here, as
    Turning.frame works them out), its pixels made only when asked for, from what it holds."""
    map_, air, mark = turning.map, turning.air_at(step), turning.mark_at(step, place)
    size = (turning.size, turning.size)
    layer = Lazy(size, "RGBA", lambda: turning.picture_of(map_, step, air, mark))
    return Globe(layer, turning, turning.map_image, step, air, mark)


class SceneFrame(Lazy):
    """The frame of a :class:`Scene` as a picture (RGB): its pixels are composed the first time
    anything asks for them (:func:`compose`). ``scene`` is its own: a copy, one drawn on, is not
    the scene's frame."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(scene.size, "RGB", lambda: compose(scene))
        self.scene = scene

    def turned(self) -> SceneFrame:
        """The frame turned half a turn (app.turned), its pixels not made either."""
        out = SceneFrame(replace(self.scene, half=not self.scene.half))
        out.info = dict(self.info)
        return out

    def over(self, wid: str, piece: Piece) -> SceneFrame:
        """The frame with ``piece`` laid over all of it (a message: overlays.ToastLayer), its
        pixels not made either. Not for a frame turned already: what lies over it is laid
        before it turns, as it is drawn here (app.turned)."""
        if self.scene.half:
            raise ValueError("a piece goes over a frame before it turns")
        out = SceneFrame(replace(self.scene, pieces=(*self.scene.pieces, (wid, piece))))
        out.info = dict(self.info)
        return out

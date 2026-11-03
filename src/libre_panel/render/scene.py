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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

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

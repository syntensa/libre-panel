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


class SceneFrame(Image.Image):
    """The frame of a :class:`Scene` as a picture (RGB): its pixels are composed the first time
    anything asks for them (:func:`compose`), from any thread, once. Copies of it are pictures
    like any other (``scene`` is its own: one drawn on is not the scene's frame)."""

    def __init__(self, scene: Scene) -> None:
        super().__init__()
        self._mode, self._size = "RGB", scene.size
        self.scene = scene
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
                    self._im = compose(self.scene).im
        return super().load()

    def turned(self) -> SceneFrame:
        """The frame turned half a turn (app.turned), its pixels not made either."""
        out = SceneFrame(replace(self.scene, half=not self.scene.half))
        out.info = dict(self.info)
        return out

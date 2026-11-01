"""The panel's video frames as ffmpeg's encoder takes them (YUV 4:2:0, turned into the panel's
framebuffer), kept from frame to frame and made anew only where the frame changed.

A frame of the 12.3" is five and a half megabytes as Pillow keeps it; ffmpeg turned every one
of them a quarter (``transpose``) and converted every one to YUV (swscale), though from one
frame to the next a hundredth of its pixels changes (Lumen: 1.2 %, four in a hundred blocks of
16 pixels). Here the parts the renderer composed anew (Renderer.damage) are compared with the
frame before, and the blocks that changed in them are converted, turned and laid into the YUV
kept for the panel, all of them at once; ffmpeg reads two megabytes a frame and encodes them as
they are.

The colours are BT.601, as swscale made them, in the panel's range (limited: light 16-235,
colours 16-240; or full); a pair of rows and columns shares the colours of its four pixels'
average. Against swscale's: the light within one level, the colours 52 dB and more (an encoder
at crf 20 is ten times further from either).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

Box = tuple[int, int, int, int]

# limited range: light 16-235, colours 16-240 about 128 (Pillow's YCbCr is JPEG's, full range)
LIMITED_Y = [16 + round(v * 219 / 255) for v in range(256)]
LIMITED_C = [128 + round((v - 128) * 224 / 255) for v in range(256)]
TURNS = {270: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180}


def _mapped(image: Image.Image, table: list[int]) -> Image.Image:
    """``image`` through ``table`` (Image.point, without its rounding of every entry anew on
    each call)."""
    return image._new(image.im.point(table, None))


def packed(image: Image.Image) -> np.ndarray:
    """``image`` as one unsigned 32-bit number a pixel (R, G and B in its low three bytes, as
    Pillow keeps them), rows by columns: a pixel compared in one go."""
    image = image if image.mode == "RGB" else image.convert("RGB")
    raw = image.tobytes("raw", "RGBX")
    return np.frombuffer(raw, np.uint32).reshape(image.height, image.width)


def block_of(size: tuple[int, int]) -> int | None:
    """The blocks in which a frame of ``size`` is compared and converted: 16 pixels (a part
    the renderer composed anew, a graph's box, changes along its curve only: Lumen's 12.3"
    composes a fifth of the frame anew in each, and of that a twentieth changes), or 8 where
    the frame is not made of whole blocks of 16; None where neither fits."""
    for block in (16, 8):
        if size[0] % block == 0 and size[1] % block == 0:
            return block
    return None


def turned_size(size: tuple[int, int], rotate: int | None) -> tuple[int, int]:
    """The size of a frame of ``size`` once turned as ``rotate`` says (270: a quarter turn
    clockwise, ffmpeg's ``transpose=1``; 180; None: as it is)."""
    return (size[1], size[0]) if rotate == 270 else size


def turned_box(box: Box, size: tuple[int, int], rotate: int | None) -> Box:
    """Where ``box`` of a frame of ``size`` lies once the frame is turned."""
    x0, y0, x1, y1 = box
    width, height = size
    if rotate == 270:  # (x, y) -> (height - 1 - y, x)
        return height - y1, x0, height - y0, x1
    if rotate == 180:
        return width - x1, height - y1, width - x0, height - y0
    return box


def _turned(planes: np.ndarray, rotate: int | None) -> np.ndarray:
    """Pictures (..., rows, columns) turned as the frame is (the last two axes)."""
    if rotate == 270:
        return np.rot90(planes, -1, axes=(-2, -1))
    if rotate == 180:
        return planes[..., ::-1, ::-1]
    return planes


class YuvFrame:
    """The YUV 4:2:0 the panel's encoder is fed, for frames of ``size`` turned as ``rotate``
    says, in full range or limited (``full``). :meth:`update` makes it anew where a frame
    changed, :meth:`write` hands it to ffmpeg's stdin."""

    def __init__(self, size: tuple[int, int], rotate: int | None, full: bool) -> None:
        block = block_of(size)
        if block is None:
            raise ValueError(f"no blocks of 8 or 16 pixels make {size[0]}x{size[1]}")
        self.size, self.rotate, self.full, self.block = size, rotate, full, block
        width, height = turned_size(size, rotate)
        self.y = np.full((height, width), 0 if full else 16, np.uint8)
        self.u = np.full((height // 2, width // 2), 128, np.uint8)
        self.v = np.full((height // 2, width // 2), 128, np.uint8)
        self.whole = True  # the next frame is made whole, whatever changed
        self.previous: Image.Image | None = None  # the frame made last (frames are not drawn on)

    @property
    def bytes_per_frame(self) -> int:
        return self.y.size + self.u.size + self.v.size

    def update(self, frame: Image.Image, damage: list[Box] | None) -> float:
        """Make the YUV of ``frame`` where it differs from the frame before: ``damage``, the
        boxes that may have changed (None: all of it; [] nothing). The share of the frame
        made anew."""
        if frame.size != self.size:
            raise ValueError(f"a frame of {frame.size}, not {self.size}")
        previous, self.previous = self.previous, frame
        if self.whole or damage is None or previous is None:
            self.whole = False
            self._whole(frame)
            return 1.0
        if previous is frame:
            return 0.0  # (the same frame again)
        found = [self._moved(previous, frame, grown)
                 for grown in map(self._blocks, damage) if grown]  # fmt: skip
        found = [part for part in found if part is not None]
        if not found:
            return 0.0
        tiles = np.concatenate([tiles for tiles, _rows, _cols in found])
        rows = np.concatenate([rows for _tiles, rows, _cols in found])
        cols = np.concatenate([cols for _tiles, _rows, cols in found])
        self._lay(tiles, rows, cols)
        return len(tiles) * self.block * self.block / (self.size[0] * self.size[1])

    def _whole(self, frame: Image.Image) -> None:
        turn = TURNS.get(self.rotate)  # type: ignore[arg-type]
        image = frame if frame.mode == "RGB" else frame.convert("RGB")
        light, blue, red = self._convert(image.transpose(turn) if turn is not None else image)
        self.y[...] = np.asarray(light)
        self.u[...] = np.asarray(blue)
        self.v[...] = np.asarray(red)

    def _convert(self, image: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
        """The light and the colours (half as wide and high) of an RGB ``image``: Pillow's
        YCbCr (BT.601), the colours of each two by two their average, in the panel's range.
        Pixel by pixel and pair by pair: the same of a part as of the whole, turned or not."""
        light, blue, red = image.convert("YCbCr").split()
        blue, red = blue.reduce(2), red.reduce(2)
        if not self.full:
            light = _mapped(light, LIMITED_Y)
            blue, red = _mapped(blue, LIMITED_C), _mapped(red, LIMITED_C)
        return light, blue, red

    def _blocks(self, box: Box) -> Box | None:
        """``box`` grown to whole blocks, within the frame."""
        block = self.block
        x0, y0, x1, y1 = box
        x0, y0 = max(0, x0 - x0 % block), max(0, y0 - y0 % block)
        x1 = min(self.size[0], x1 + (-x1) % block)
        y1 = min(self.size[1], y1 + (-y1) % block)
        return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None

    def _moved(
        self, previous: Image.Image, frame: Image.Image, box: Box
    ) -> tuple[np.ndarray, ...] | None:
        """The blocks of ``box`` (whole blocks) in which ``frame`` differs from the frame
        before: their pixels (packed, block by block) and where they are (rows and columns of
        blocks). (Frames are not drawn on once shown: the one before is as it was made.)"""
        block = self.block
        x0, y0, x1, y1 = box
        after = packed(frame.crop(box))
        before = packed(previous.crop(box))
        rows, cols = (y1 - y0) // block, (x1 - x0) // block
        # (eight pixels' answers at a time: a block's row of them is one or two numbers)
        moved = (after != before).view(np.uint64).reshape(rows, block, cols, block // 8)
        moved = moved.any(axis=3).any(axis=1)
        row, col = np.nonzero(moved)
        if not row.size:
            return None
        tiles = after.reshape(rows, block, cols, block).transpose(0, 2, 1, 3)[row, col]
        return tiles, row + y0 // block, col + x0 // block

    def _lay(self, tiles: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> None:
        """Convert ``tiles`` (blocks of packed pixels) in one go, side by side in one picture,
        and lay them, turned, where they are in the YUV."""
        block, count = self.block, len(tiles)
        half = block // 2
        strip = np.ascontiguousarray(tiles.transpose(1, 0, 2)).reshape(block, count * block)
        image = Image.frombuffer("RGB", (count * block, block), strip, "raw", "RGBX", 0, 1)
        light, blue, red = self._convert(image)
        light = _turned(np.asarray(light).reshape(block, count, block).transpose(1, 0, 2),
                        self.rotate)  # fmt: skip
        blue = _turned(np.asarray(blue).reshape(half, count, half).transpose(1, 0, 2), self.rotate)
        red = _turned(np.asarray(red).reshape(half, count, half).transpose(1, 0, 2), self.rotate)
        high, wide = self.size[1] // block, self.size[0] // block  # (the frame's blocks)
        if self.rotate == 270:  # (x, y) -> (height - 1 - y, x): rows of columns, from the right
            rows, cols = cols, high - 1 - rows
        elif self.rotate == 180:
            rows, cols = high - 1 - rows, wide - 1 - cols
        for plane, made, size in ((self.y, light, block), (self.u, blue, half),
                                  (self.v, red, half)):  # fmt: skip
            height, width = plane.shape
            grid = plane.reshape(height // size, size, width // size, size).transpose(0, 2, 1, 3)
            grid[rows, cols] = made

    def write(self, out: Any) -> None:
        """The three planes into ffmpeg's stdin ``out``, as they are kept (no copy of them as
        bytes); an OSError when ffmpeg is gone."""
        for plane in (self.y, self.u, self.v):
            view = memoryview(plane).cast("B")
            while view:  # an unbuffered pipe may take less than asked
                view = view[out.write(view) :]

    def tobytes(self) -> bytes:
        """The YUV as ffmpeg reads it (for the tests)."""
        return self.y.tobytes() + self.u.tobytes() + self.v.tobytes()

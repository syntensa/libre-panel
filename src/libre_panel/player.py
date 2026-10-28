"""The player (the app's *Player*): videos, animated pictures and pictures of a folder of its
own, played on panels instead of their themes, one after the other.

- **The library** is a folder beside config.toml (:func:`folder`, ``player``): what lies there
  (:data:`SUFFIXES`) is what can be played. The app copies a file into it (written as it comes,
  a video of gigabytes too), takes one out and opens the folder; what is put there by hand plays
  as well. What each one is, how long a round of it plays and its size, ffmpeg and Pillow tell
  once for each file as it is (:func:`info`, :func:`library`).
- **A playback** (:class:`Playback`): the files in their order, the one playing, since when or
  where it stands paused, on which panels, how it fills them (``cover``: to the edges,
  ``contain``: whole, on black), how long a picture shows and what comes after the last
  (``all``: the first again, ``one``: the same again, ``off``: the themes again). It lives in
  the app's process: :func:`play` and the rest change it, every panel's loop asks it on every
  frame (:func:`now`), and a panel in a process of its own is told it by the first panel's
  process (:func:`export`, :func:`adopt`), as the colour check is. Its times are on the wall
  clock, which every process of the app reads alike. Nothing of it is written: it ends with
  the app.
- **On a panel** (:class:`Screen`, one for each panel's loop): a video through ffmpeg, each
  frame at its moment of the playback (moving.VideoFeed, from where it was sought), an
  animated picture frame by frame, a picture fitted; on black. Notices, the alarm and the rest
  still go over it; paused, the frame stays.
"""

from __future__ import annotations

import io
import itertools
import logging
import math
import random
import re
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from libre_panel.render import moving

log = logging.getLogger(__name__)

FOLDER = "player"
FITS = ("cover", "contain")
LOOPS = ("all", "one", "off")
PICTURE_S = 10.0  # a picture's time, and an animated picture's at least (in whole rounds)
PICTURE_RANGE = (1.0, 3600.0)
BACK_S = 3.0  # further into an item than this, "previous" starts it again
STILLS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SUFFIXES = (*moving.VIDEO_SUFFIXES, *STILLS)
_NAME = re.compile(r'[^\\/:*?"<>|\x00-\x1f]{1,120}')
KINDS = ("video", "animation", "picture")

clock = time.time  # (the wall clock: a panel in a process of its own reads the same)


class PlayerError(Exception):
    """Something the player cannot do, said for the person (a name, nothing to play ...)."""


def folder() -> Path:
    """The library: the folder beside config.toml whose files can be played."""
    from libre_panel.config import config_dir

    return config_dir() / FOLDER


def valid_name(name: Any) -> bool:
    """Whether ``name`` is one of a file the library may hold: a name alone (no folder), not
    hidden, of a video or a picture."""
    return (
        isinstance(name, str)
        and _NAME.fullmatch(name) is not None
        and name == name.strip()
        and not name.startswith(".")
        and not name.endswith(".")
        and Path(name).suffix.lower() in SUFFIXES
    )


# -- the library ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Info:
    """A file of the library as ffmpeg or Pillow read it."""

    name: str
    kind: str  # KINDS: a video, an animated picture, a picture
    bytes: int
    length: float  # a round of it in seconds: a video's, an animated picture's (0: a picture)
    size: tuple[int, int] | None  # its pictures' (None: not told)
    fps: float | None = None  # a video's frames a second (None: not told)


_infos: dict[Path, tuple[tuple[int, int], Info | None]] = {}
_infos_lock = threading.Lock()


def _ffmpeg() -> str:
    """ffmpeg, as the videos of themes find it; "" where there is none."""
    from libre_panel.devices.base import DeviceError
    from libre_panel.devices.h264 import find_ffmpeg

    try:
        return find_ffmpeg()
    except DeviceError:
        return ""


def info(path: Path) -> Info | None:
    """What the file at ``path`` is; None for one that is no video or picture that can be read.
    Read once for each file as it is (its time and size)."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    with _infos_lock:
        hit = _infos.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    found = _read_info(path, stat.st_size)
    with _infos_lock:
        _infos[path] = (key, found)
    return found


def _read_info(path: Path, size: int) -> Info | None:
    if moving.is_video(path.name):
        ffmpeg = _ffmpeg()
        if not ffmpeg:
            return Info(path.name, "video", size, 0.0, None)  # (it plays once ffmpeg is there)
        told = moving.video_info(ffmpeg, path)
        if told.size is None:
            return None  # (ffmpeg cannot read it)
        return Info(path.name, "video", size, told.length or 0.0, told.size, told.fps)
    try:
        with Image.open(path) as image:
            count = getattr(image, "n_frames", 1)
            shape = image.size
            length = 0.0
            for n in range(count if count > 1 else 0):
                image.seek(n)
                seconds = (image.info.get("duration") or 0) / 1000
                length += seconds if seconds >= moving.LEAST_FRAME_S else moving.USUAL_FRAME_S
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        log.info("player: %s is no picture that can be read (%s)", path.name, exc)
        return None
    return Info(path.name, "animation" if count > 1 else "picture", size, length, shape)


def library(where: Path | None = None) -> list[Info]:
    """What can be played: the files of the folder that are videos or pictures, by name."""
    where = folder() if where is None else where
    try:
        paths = [path for path in where.iterdir() if path.is_file() and valid_name(path.name)]
    except OSError:
        return []
    paths.sort(key=lambda path: path.name.casefold())
    return [found for path in paths if (found := info(path)) is not None]


def has_ffmpeg() -> bool:
    """Whether videos can play here (ffmpeg is found)."""
    return bool(_ffmpeg())


_thumbs: dict[tuple[Path, int, int, int], bytes] = {}  # (path, mtime, bytes, width) -> JPEG
THUMBS = 300  # pictures of the library kept at most


def thumbnail(name: str, width: int = 320, where: Path | None = None) -> bytes | None:
    """A JPEG of the file ``name`` of the library, ``width`` wide: a video's picture a little
    way in (its first is often black), an animated picture's first; None for none."""
    if not valid_name(name):
        return None
    path = (folder() if where is None else where) / name
    found = info(path)
    if found is None:
        return None
    width = max(16, min(int(width), 1280))
    stat = path.stat()
    key = (path, stat.st_mtime_ns, stat.st_size, width)
    with _infos_lock:
        hit = _thumbs.get(key)
    if hit is not None:
        return hit
    picture = None
    if found.kind == "video":
        ffmpeg = _ffmpeg()
        if ffmpeg and found.size:
            height = max(2, round(width * found.size[1] / max(found.size[0], 1)))
            at = min(1.0, found.length * 0.1) if found.length else 0.0
            picture = moving.first_frame(ffmpeg, path, width, height, "contain", start=at)
    else:
        try:
            with Image.open(path) as image:
                picture = ImageOps.exif_transpose(image).convert("RGBA")
                picture.thumbnail((width, width * 4), Image.Resampling.LANCZOS)
        except (OSError, ValueError, Image.DecompressionBombError):
            picture = None
    if picture is None:
        return None
    flat = Image.new("RGB", picture.size, "black")
    flat.paste(picture, (0, 0), picture.getchannel("A") if picture.mode == "RGBA" else None)
    out = io.BytesIO()
    flat.save(out, format="JPEG", quality=82)
    data = out.getvalue()
    with _infos_lock:
        if len(_thumbs) >= THUMBS:
            _thumbs.pop(next(iter(_thumbs)))
        _thumbs[key] = data
    return data


def remove(name: str, where: Path | None = None) -> None:
    """Take the file ``name`` out of the library (it plays no more)."""
    if not valid_name(name):
        raise PlayerError("not a file of the library")
    path = (folder() if where is None else where) / name
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise PlayerError(f"{name} could not be removed: {exc}") from exc


# -- the playback ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """A file in a playback and how long it plays: a video to its end (0: not told, until the
    next is asked), an animated picture in whole rounds for the picture time at least, a
    picture for the picture time."""

    name: str
    kind: str
    seconds: float
    fps: float | None = None


def item_of(found: Info, picture_s: float) -> Item:
    if found.kind == "video":
        return Item(found.name, "video", found.length, found.fps)
    if found.kind == "animation" and found.length > 0:
        rounds = max(1, math.ceil(picture_s / found.length - 1e-9))
        return Item(found.name, "animation", rounds * found.length)
    return Item(found.name, found.kind, picture_s)


@dataclass(frozen=True)
class Playback:
    items: tuple[Item, ...]
    panels: tuple[int, ...]  # 1 the first panel, 2 the first of [[panels]] ...
    index: int = 0
    began: float = 0.0  # when the item on show was at its start, on ``clock`` (it plays)
    paused: float | None = None  # where it stands, paused: seconds into the item
    fit: str = "cover"
    loop: str = "all"
    shuffle: bool = False
    picture_s: float = PICTURE_S
    jump: int = 0  # one more at each jump (another item, a seek): its frames from elsewhere
    where: str = ""  # the library's folder

    @property
    def item(self) -> Item:
        return self.items[self.index]

    def position(self, at: float) -> float:
        """Seconds into the item on show at ``at``."""
        return self.paused if self.paused is not None else max(0.0, at - self.began)


@dataclass(frozen=True)
class Showing:
    """What a panel plays at a moment (:func:`now`)."""

    path: Path
    kind: str
    fit: str
    position: float  # seconds into it
    jump: int
    paused: bool
    fps: float | None = None  # a video's own


_lock = threading.Lock()
_own: Playback | None = None  # this process's, the app's
_told: Playback | None = None  # in a panel's own process: the first panel's process's
_jumps = itertools.count(1)


def _advanced(playback: Playback | None, at: float) -> Playback | None:
    """``playback`` at ``at``: the items that ended since moved on from, as its ``loop`` says;
    None where the last ended and nothing comes after it."""
    for _ in range(4 * len(playback.items) if playback is not None else 0):
        length = playback.item.seconds
        if playback.paused is not None or length <= 0 or at - playback.began < length - 1e-6:
            return playback
        began = playback.began + length  # (the next begins where it ended: no time lost)
        if playback.loop == "one":
            playback = replace(playback, began=began, jump=next(_jumps))
        elif playback.index + 1 < len(playback.items):
            playback = replace(playback, index=playback.index + 1, began=began,
                               jump=next(_jumps))  # fmt: skip
        elif playback.loop == "all":
            items = _shuffled(playback.items) if playback.shuffle else playback.items
            playback = replace(playback, items=items, index=0, began=began, jump=next(_jumps))
        else:
            return None
    if playback is not None and playback.paused is None:  # (away for long: on from now)
        playback = replace(playback, began=at - min(at - playback.began, playback.item.seconds))
    return playback


def _shuffled(items: tuple[Item, ...]) -> tuple[Item, ...]:
    mixed = list(items)
    random.shuffle(mixed)
    return tuple(mixed)


def _moved(playback: Playback, step: int, at: float) -> Playback:
    """The item ``step`` away, from its start (round from the last to the first); paused, it
    stays paused there."""
    index = (playback.index + step) % len(playback.items)
    paused = 0.0 if playback.paused is not None else None
    return replace(playback, index=index, began=at, paused=paused, jump=next(_jumps))


def _sought(playback: Playback, seconds: float, at: float) -> Playback:
    length = playback.item.seconds
    seconds = max(0.0, min(seconds, length - 0.05) if length > 0 else seconds)
    paused = seconds if playback.paused is not None else None
    return replace(playback, began=at - seconds, paused=paused, jump=next(_jumps))


def _change(how: Any) -> dict[str, Any] | None:
    """The playback changed by ``how(playback, at)`` (once it is where the time says)."""
    global _own
    at = clock()
    with _lock:
        _own = _advanced(_own, at)
        if _own is not None:
            _own = how(_own, at)
    return status()


def play(names: list[str], panels: tuple[int, ...] | list[int] = (1,), start: str | None = None,
         fit: str = "cover", loop: str = "all", shuffle: bool = False,
         picture_s: float = PICTURE_S,
         where: Path | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Play the files ``names`` of the library in their order on ``panels``, from ``start``
    (the first where it is none of them), instead of their themes. With ``shuffle`` in another
    order, ``start`` first."""
    where = folder() if where is None else where
    if fit not in FITS:
        raise PlayerError(f"fit: one of {', '.join(FITS)}")
    if loop not in LOOPS:
        raise PlayerError(f"loop: one of {', '.join(LOOPS)}")
    panels = tuple(sorted({int(n) for n in panels}))
    if not panels or any(n < 1 for n in panels):
        raise PlayerError("panels: the numbers of the panels to play on, 1 the first")
    try:
        picture_s = min(max(float(picture_s), PICTURE_RANGE[0]), PICTURE_RANGE[1])
    except (TypeError, ValueError) as exc:
        raise PlayerError("picture_s: seconds") from exc
    found = [info(where / name) for name in names if valid_name(name)]  # (outside the lock)
    items = [item_of(f, picture_s) for f in found if f is not None]
    if not items:
        raise PlayerError("nothing to play: no video or picture of the library")
    first = next((item for item in items if item.name == start), items[0])
    if shuffle:
        items = [first, *_shuffled(tuple(item for item in items if item is not first))]
    global _own
    with _lock:
        _own = Playback(tuple(items), panels, items.index(first), clock(), None, fit, loop,
                        shuffle, picture_s, next(_jumps), str(where))  # fmt: skip
    log.info("player: %d to play on panel %s, from %s", len(items),
             ", ".join(map(str, panels)), first.name)  # fmt: skip
    return status()


def pause() -> dict[str, Any] | None:
    return _change(lambda p, at: p if p.paused is not None else replace(p, paused=p.position(at)))


def resume() -> dict[str, Any] | None:
    return _change(
        lambda p, at: p if p.paused is None else replace(p, began=at - p.paused, paused=None)
    )


def toggle() -> dict[str, Any] | None:
    """Pause, or play on where it stands paused."""
    with _lock:
        paused = _own is not None and _own.paused is not None
    return resume() if paused else pause()


def next_item() -> dict[str, Any] | None:
    return _change(lambda p, at: _moved(p, +1, at))


def previous_item() -> dict[str, Any] | None:
    """The item before; one that has played a few seconds (:data:`BACK_S`) from its start."""
    return _change(
        lambda p, at: _sought(p, 0.0, at) if p.position(at) > BACK_S else _moved(p, -1, at)
    )


def seek(seconds: float) -> dict[str, Any] | None:
    """To ``seconds`` into the item on show."""
    return _change(lambda p, at: _sought(p, float(seconds), at))


def configure(fit: str | None = None, loop: str | None = None, shuffle: bool | None = None,
              picture_s: float | None = None) -> dict[str, Any] | None:  # fmt: skip
    """How the playback goes on: how it fills the panels, what comes after the last, in
    another order, how long a picture shows (from the next one on)."""
    if fit is not None and fit not in FITS:
        raise PlayerError(f"fit: one of {', '.join(FITS)}")
    if loop is not None and loop not in LOOPS:
        raise PlayerError(f"loop: one of {', '.join(LOOPS)}")
    changes: dict[str, Any] = {}
    if fit is not None:
        changes["fit"] = fit
    if loop is not None:
        changes["loop"] = loop
    if shuffle is not None:
        changes["shuffle"] = bool(shuffle)
    remade: dict[str, Item] = {}  # the pictures' new times (read outside the lock)
    if picture_s is not None:
        try:
            changes["picture_s"] = min(max(float(picture_s), PICTURE_RANGE[0]), PICTURE_RANGE[1])
        except (TypeError, ValueError) as exc:
            raise PlayerError("picture_s: seconds") from exc
        with _lock:
            current = _own
        for item in current.items if current is not None else ():
            found = info(Path(current.where) / item.name) if item.kind != "video" else None
            if found is not None:
                remade[item.name] = item_of(found, changes["picture_s"])

    def changed(playback: Playback, at: float) -> Playback:
        items = tuple(remade.get(item.name, item) for item in playback.items)
        return replace(playback, **changes, items=items)

    return _change(changed)


def stop() -> None:
    """The themes again, on every panel the player played on."""
    global _own
    with _lock:
        stopped, _own = _own, None
    if stopped is not None:
        log.info("player: stopped")


def now(number: int) -> Showing | None:
    """What panel ``number`` plays now; None: its theme."""
    global _own
    at = clock()
    with _lock:
        if _own is not None:
            _own = _advanced(_own, at)
        playback = _own if _own is not None else _told
    if playback is None or number not in playback.panels:
        return None
    item = playback.item
    return Showing(Path(playback.where) / item.name, item.kind, playback.fit,
                   playback.position(at), playback.jump, playback.paused is not None,
                   item.fps)  # fmt: skip


def status() -> dict[str, Any] | None:
    """The playback for the app: its items, the one on show, where it stands; None: none."""
    global _own
    at = clock()
    with _lock:
        _own = _advanced(_own, at)
        playback = _own
    if playback is None:
        return None
    item = playback.item
    return {
        "items": [asdict(i) for i in playback.items],
        "index": playback.index,
        "name": item.name,
        "kind": item.kind,
        "position": round(playback.position(at), 3),
        "seconds": item.seconds,
        "paused": playback.paused is not None,
        "panels": list(playback.panels),
        "fit": playback.fit,
        "loop": playback.loop,
        "shuffle": playback.shuffle,
        "picture_s": playback.picture_s,
    }


def export() -> dict[str, Any] | None:
    """What a panel's own process needs to play along: the playback as it is (it changes only
    at a change, and as an item ends)."""
    global _own
    with _lock:
        _own = _advanced(_own, clock())
        playback = _own
    return asdict(playback) if playback is not None else None


def adopt(told: Any) -> None:
    """In a panel's own process: what the first panel's process said (:func:`export`)."""
    global _told
    try:
        playback = None
        if isinstance(told, dict):
            items = tuple(Item(**item) for item in told["items"])
            playback = Playback(**{**told, "items": items, "panels": tuple(told["panels"])})
    except (KeyError, TypeError, ValueError):
        playback = None
    with _lock:
        _told = playback


# -- on a panel -----------------------------------------------------------------------------


def _fitted(image: Image.Image, size: tuple[int, int], fit: str) -> Image.Image:
    """``image`` across ``size`` (``cover``) or whole within it (``contain``), on black, RGB."""
    image = ImageOps.exif_transpose(image).convert("RGBA")
    if fit == "cover":
        image = ImageOps.fit(image, size, Image.Resampling.LANCZOS)
    else:
        image = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    out = Image.new("RGB", size, "black")
    out.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2), image)
    return out


class Screen:
    """What one panel's loop draws while the player plays on it (app.MainLoop): the item of the
    moment at the panel's size, on black, a new picture each time (the loop draws on it)."""

    def __init__(self) -> None:
        self._key: tuple[Any, ...] | None = None
        self._feed: moving.VideoFeed | None = None
        self._animation: moving.Animation | None = None
        self._still: Image.Image | None = None
        self._last: Image.Image | None = None  # (shown until the next one's first frame)
        self._ffmpeg: str | None = None

    def frame(self, showing: Showing, size: tuple[int, int], fps: int) -> Image.Image:
        """The panel's frame of ``showing``, ``size`` large; ``fps``: the panel's frames a
        second (a video is decoded at as many, no more than its own)."""
        rate = max(1, int(fps))
        if showing.kind == "video" and showing.fps:
            rate = min(rate, max(1, math.ceil(showing.fps - 0.05)))
        jump = showing.jump if showing.kind == "video" else 0  # (a picture's time says it)
        key = (showing.path, showing.kind, showing.fit, size, rate, jump)
        if key != self._key:
            self._open(showing, size, rate)
            self._key = key
        if self._feed is not None:
            _count, picture = self._feed.frame(showing.position)
            if picture is not None:
                frame = Image.new("RGB", size, "black")
                frame.paste(picture)  # (opaque, or black where contain left room: alpha 0)
                self._last = frame
                return frame.copy()
        elif self._animation is not None:
            frame = self._animation.frames[self._animation.index(showing.position)]
            self._last = frame
            return frame.copy()
        elif self._still is not None:
            self._last = self._still
            return self._still.copy()
        last = self._last
        return last.copy() if last is not None and last.size == size else Image.new("RGB", size)

    def _open(self, showing: Showing, size: tuple[int, int], rate: int) -> None:
        self._close_feed()
        self._animation = self._still = None
        if showing.kind == "video":
            if self._ffmpeg is None:
                self._ffmpeg = _ffmpeg()
            if not self._ffmpeg:
                log.warning("player: %s needs ffmpeg", showing.path.name)
                return
            start = showing.position if showing.position > 0.05 else 0.0
            self._feed = moving.VideoFeed(self._ffmpeg, showing.path, *size, showing.fit,
                                          fps=rate, start=start, rounds=False,
                                          began=start)  # fmt: skip
            return
        try:
            if showing.kind == "animation":
                self._animation = moving.read_animation(
                    showing.path, lambda image: _fitted(image, size, showing.fit)
                )
            if self._animation is None:
                with Image.open(showing.path) as image:
                    self._still = _fitted(image, size, showing.fit)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            log.warning("player: %s cannot be shown (%s)", showing.path.name, exc)

    def _close_feed(self) -> None:
        if self._feed is not None:
            self._feed.close()
            self._feed = None

    def close(self) -> None:
        """Nothing plays here any more: ffmpeg stops, the pictures go."""
        self._close_feed()
        self._key = None
        self._animation = self._still = self._last = None

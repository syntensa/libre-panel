"""A panel as a monitor (the app's *Screen*): a monitor of this computer, a part of
one or a window on a panel instead of its theme, as it changes, until it is stopped.

- **What is shown** (:class:`Source`): a monitor (``monitor``, by its id: capture.py), a part
  of one (``region``), a window (``window``), or the monitor of its own that the computer
  extends its desktop to for the panel (``virtual``: extend.py made it, or the desktop does on
  Wayland); fitted into the panel's frame whole (``contain``), to its edges (``cover``) or
  stretched; the mouse pointer with it or not.
- **Where** (:func:`show`, :func:`now`): on which panels. It lives in the app's process, as the
  Player's playback does: every panel's loop asks it each frame, and a panel in a process of
  its own is told it by the first panel's process (:func:`export`, :func:`adopt`). Nothing of
  it is written: it ends with the app.
- **On a panel**: a TURZX panel in video mode encodes the screen straight to its stream
  (devices/h264.py, :func:`capture_source`): one ffmpeg captures, fits and encodes, nothing goes
  through Libre Panel. Any other panel gets frames (:class:`Screen`): ffmpeg captures and fits
  them, the newest is drawn; notices and the alarm still go over them.

A picture is as old as capturing, encoding and the panel's queue make it: about a tenth of a
second on a TURZX panel. A panel shows no pointer of its own and takes no touch: the mouse goes
there as to any monitor (``virtual``), or the screen shown is one with a mouse already.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from PIL import Image

from libre_panel import capture

log = logging.getLogger(__name__)

KINDS = ("monitor", "window", "virtual")
FITS = ("contain", "cover", "stretch")
IDLE_S = 5.0  # a capture nobody asked a frame of for this long: ffmpeg stops
RETRY_S = 3.0  # after ffmpeg stopped by itself (a window closed, a monitor gone)

clock = time.time


class MirrorError(Exception):
    """Something that cannot be shown, said for the person."""


@dataclass(frozen=True)
class Source:
    """What a panel shows as a monitor: ``kind`` (:data:`KINDS`), ``id`` (the monitor's or the
    window's, capture.py; a virtual one's is found as it starts), ``name`` (for the app),
    ``region`` (x, y, width, height within a monitor), ``fit`` and ``cursor``."""

    kind: str
    id: str = ""
    name: str = ""
    region: tuple[int, int, int, int] | None = None
    fit: str = "contain"
    cursor: bool = True


@dataclass(frozen=True)
class Mirroring:
    source: Source
    panels: tuple[int, ...]
    since: float


_lock = threading.Lock()
_own: Mirroring | None = None  # the app's process: what the app said
_told: Mirroring | None = None  # a panel's own process: what the first panel's said


def source_of(raw: Any) -> Source:
    """A :class:`Source` from the app's JSON (MirrorError: what is wrong with it)."""
    if not isinstance(raw, dict):
        raise MirrorError("source: expected {kind, id, ...}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise MirrorError(f"kind: one of {', '.join(KINDS)}")
    ident = raw.get("id", "")
    if not isinstance(ident, str) or (kind != "virtual" and not ident):
        raise MirrorError("id: which monitor or window")
    region = raw.get("region")
    if region is not None:
        if (not isinstance(region, (list, tuple)) or len(region) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in region)
                or region[2] < 16 or region[3] < 16):  # fmt: skip
            raise MirrorError("region: [x, y, width, height] in pixels")
        region = tuple(region)
    fit = raw.get("fit", "contain")
    if fit not in FITS:
        raise MirrorError(f"fit: one of {', '.join(FITS)}")
    name = raw.get("name", "")
    return Source(kind, ident, name if isinstance(name, str) else "", region, fit,
                  raw.get("cursor", True) is not False)  # fmt: skip


def show(source: Source, panels: tuple[int, ...] | list[int] = (1,),
         size: tuple[int, int] | None = None) -> None:  # fmt: skip
    """``source`` on ``panels`` from now on, instead of their themes (and of the Player). The
    panel's own monitor (``virtual``) goes on the desktop first, ``size`` large (the first
    panel's frame), and off again with :func:`stop` (Wayland: the capture makes it)."""
    global _own
    chosen = tuple(sorted({int(n) for n in panels if int(n) >= 1}))
    if not chosen:
        raise MirrorError("panels: at least one")
    from libre_panel import extend, player

    with _lock:
        before = _own
    own = source.kind == "virtual" and capture.platform() != "wayland"
    if own:
        if size is None:
            raise MirrorError("the panel's size is not known: choose its model in Settings")
        try:
            extend.attach(size)
        except (extend.ExtendError, OSError) as exc:
            raise MirrorError(str(exc)) from exc
        _detach_on_exit()
    elif before is not None and before.source.kind == "virtual":
        extend.detach()
    player.stop()  # (one or the other on a panel)
    with _lock:
        _own = Mirroring(source, chosen, clock())
    log.info("monitor: %s %s on panel %s", source.kind, source.name or source.id,
             ", ".join(map(str, chosen)))  # fmt: skip


def stop() -> None:
    """The themes again (the panel's own monitor off the desktop)."""
    global _own
    with _lock:
        stopped, _own = _own, None
    if stopped is not None:
        if stopped.source.kind == "virtual":
            from libre_panel import extend

            extend.detach()
        log.info("monitor: stopped")


_registered = False


def _detach_on_exit() -> None:
    """The panel's own monitor goes off the desktop as the app ends, too (Windows keeps where
    its monitors are)."""
    global _registered
    if not _registered:
        import atexit

        atexit.register(stop)
        _registered = True


def now(number: int) -> Source | None:
    """What panel ``number`` shows as a monitor now; None: its theme (or the Player)."""
    with _lock:
        mirroring = _own if _own is not None else _told
    if mirroring is None or number not in mirroring.panels:
        return None
    return mirroring.source


def status() -> dict[str, Any] | None:
    with _lock:
        mirroring = _own
    if mirroring is None:
        return None
    return {"source": asdict(mirroring.source), "panels": list(mirroring.panels),
            "since": mirroring.since}  # fmt: skip


def export() -> dict[str, Any] | None:
    """What a panel's own process needs to show along (:func:`adopt`)."""
    with _lock:
        mirroring = _own
    return asdict(mirroring) if mirroring is not None else None


def adopt(told: Any) -> None:
    """In a panel's own process: what the first panel's process said (:func:`export`)."""
    global _told
    try:
        mirroring = None
        if isinstance(told, dict):
            raw = told["source"]
            region = tuple(raw["region"]) if raw.get("region") else None
            source = Source(**{**raw, "region": region})
            mirroring = Mirroring(source, tuple(told["panels"]), float(told["since"]))
    except (KeyError, TypeError, ValueError):
        mirroring = None
    with _lock:
        _told = mirroring


# -- capturing ------------------------------------------------------------------------------


def capture_of(ffmpeg: str, source: Source, fps: int,
               size: tuple[int, int] | None = None) -> capture.Capture:  # fmt: skip
    """ffmpeg's input for ``source`` here, now (a monitor's place and a window's handle are
    looked up as it starts: they change); ``size``: the panel's frame (Wayland's capture
    program fits the pictures to it). MirrorError where it cannot be captured."""
    where = capture.platform()
    try:
        if where == "wayland":
            from libre_panel import wayland

            return wayland.capture_of(source, fps, size)
        if source.kind == "window":
            window = capture.find_window(source.id, where)
            if window is None:
                raise MirrorError(f"the window {source.name or source.id} is gone")
            return capture.window_capture(ffmpeg, window, fps, source.cursor, where)
        if source.kind == "virtual":
            from libre_panel import extend

            monitor = extend.virtual_monitor(where)
            if monitor is None:
                raise MirrorError("no monitor of the panel's own: set it up first")
        else:
            monitor = capture.find_monitor(source.id, where)
            if monitor is None:
                raise MirrorError(f"the monitor {source.name or source.id} is gone")
        return capture.monitor_capture(ffmpeg, monitor, fps, source.cursor, source.region, where)
    except capture.CaptureError as exc:
        raise MirrorError(str(exc)) from exc


def fitted(fit: str, width: int, height: int, background: str = "black") -> str:
    """ffmpeg's filter fitting the captured pictures into a frame of width x height."""
    if fit == "cover":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    if fit == "contain":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background}")  # fmt: skip
    return f"scale={width}:{height}"


def capture_source(ffmpeg: str, source: Source, fps: int, size: tuple[int, int]) -> Any:
    """The source for a TURZX panel's stream (devices/h264.CaptureSource): its picture goes
    from the screen straight into the panel's video."""
    from libre_panel.devices.h264 import CaptureSource

    found = capture_of(ffmpeg, source, fps, size)
    return CaptureSource(found.inputs, found.prefilter, source.fit, feeder=found.feeder,
                         fds=found.fds, name=found.label or source.name)  # fmt: skip


def frames_command(ffmpeg: str, found: capture.Capture, size: tuple[int, int], fit: str,
                   fps: int) -> list[str]:  # fmt: skip
    """ffmpeg capturing ``found`` into raw RGB frames of ``size`` on its output, ``fps`` a
    second (the newest is read: :class:`CaptureFeed`)."""
    filters = [found.prefilter, fitted(fit, *size), f"fps={fps}"]
    return [ffmpeg, "-v", "error", "-nostdin", *found.inputs, "-an",
            "-vf", ",".join(part for part in filters if part),
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]  # fmt: skip


class CaptureFeed:
    """The screen as ffmpeg captures it, into frames of ``size``: a thread reads them as they
    come and keeps the newest (a monitor shows what is now, never what was). ffmpeg starts with
    the first ask, stops :data:`IDLE_S` after the last, and starts again :data:`RETRY_S` after
    it stopped by itself."""

    def __init__(self, command: list[str], size: tuple[int, int], feeder: tuple[str, ...] = (),
                 fds: tuple[int, ...] = (), clock: Any = time.monotonic) -> None:  # fmt: skip
        self.command, self.size, self.feeder, self.fds = command, size, feeder, fds
        self.clock = clock
        self._lock = threading.Lock()
        self._newest: Image.Image | None = None
        self._count = 0
        self._asked = 0.0
        self._failed = -RETRY_S
        self._processes: list[subprocess.Popen] = []
        self._thread: threading.Thread | None = None

    def frame(self) -> tuple[int, Image.Image | None]:
        """The number of the newest frame (a new one is a new picture) and the frame."""
        now = self.clock()
        with self._lock:
            self._asked = now
            running = self._thread is not None and self._thread.is_alive()
            if not running and now - self._failed >= RETRY_S:
                self._start()
            return self._count, self._newest

    def _start(self) -> None:
        processes: list[subprocess.Popen] = []
        try:
            stdin: Any = subprocess.DEVNULL
            if self.feeder:
                feeder = subprocess.Popen(self.feeder, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, pass_fds=self.fds)  # fmt: skip
                processes.append(feeder)
                stdin = feeder.stdout
            ffmpeg = subprocess.Popen(self.command, stdin=stdin, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, **capture._quiet())  # fmt: skip
            processes.append(ffmpeg)
        except OSError as exc:
            log.warning("monitor: the capture did not start (%s)", exc)
            for process in processes:
                process.kill()
            self._failed = self.clock()
            return
        self._processes = processes
        self._thread = threading.Thread(target=self._read, args=(processes,),
                                        name="monitor-feed", daemon=True)  # fmt: skip
        self._thread.start()

    def _read(self, processes: list[subprocess.Popen]) -> None:
        size = self.size[0] * self.size[1] * 3
        stream = processes[-1].stdout
        try:
            while stream is not None:
                data = stream.read(size)
                if len(data) < size:
                    break
                picture = Image.frombytes("RGB", self.size, data)
                with self._lock:
                    if self._processes is not processes:
                        break  # closed
                    self._newest, self._count = picture, self._count + 1
                    idle = self.clock() - self._asked > IDLE_S
                if idle:
                    break  # nobody looks: the theme again
        finally:
            for process in processes:
                process.kill()
                process.wait()
            with self._lock:
                if self._processes is processes:
                    self._processes = []
                    if self.clock() - self._asked <= IDLE_S:
                        self._failed = self.clock()
                        log.warning("monitor: the capture stopped")

    def close(self) -> None:
        with self._lock:
            processes, self._processes = self._processes, []
            self._newest, self._asked = None, -IDLE_S * 2
        for process in processes:
            process.kill()


class Screen:
    """What one panel's loop draws while it is a monitor (app.MainLoop): the newest picture of
    the source at the panel's size, on black (the loop draws on it)."""

    def __init__(self) -> None:
        self._key: tuple[Any, ...] | None = None
        self._feed: CaptureFeed | None = None
        self._last: Image.Image | None = None
        self._ffmpeg: str | None = None
        self._failed = -RETRY_S
        self.error = ""  # why it shows nothing (for the app)

    def frame(self, source: Source, size: tuple[int, int], fps: int,
              still: bool = False) -> Image.Image:  # fmt: skip
        """The panel's frame of ``source``, ``size`` large, ``fps`` a second; ``still``: the
        panel's stream shows it straight (the app's picture alone is drawn here, of a capture
        of its own; on Wayland the last frame, as a second capture would ask the person
        again)."""
        if still and capture.platform() == "wayland":
            self.close()
            last = self._last
            return last.copy() if last is not None and last.size == size else Image.new("RGB", size)
        rate = max(1, int(fps))
        key = (source, size, rate)
        if key != self._key and time.monotonic() - self._failed >= RETRY_S:
            self._open(source, size, rate)
            self._key = key if self._feed is not None else None
        if self._feed is not None:
            _count, picture = self._feed.frame()
            if picture is not None:
                self._last = picture
                return picture.copy()
        last = self._last
        return last.copy() if last is not None and last.size == size else Image.new("RGB", size)

    def _open(self, source: Source, size: tuple[int, int], rate: int) -> None:
        self.close()
        if self._ffmpeg is None:
            from libre_panel.devices.base import DeviceError
            from libre_panel.devices.h264 import find_ffmpeg

            try:
                self._ffmpeg = find_ffmpeg()
            except DeviceError as exc:
                self.error, self._failed = str(exc), time.monotonic()
                return
        try:
            found = capture_of(self._ffmpeg, source, rate, size)
        except MirrorError as exc:
            if str(exc) != self.error:
                log.warning("monitor: %s", exc)
            self.error, self._failed = str(exc), time.monotonic()
            return
        self.error = ""
        command = frames_command(self._ffmpeg, found, size, source.fit, rate)
        self._feed = CaptureFeed(command, size, found.feeder, found.fds)

    def close(self) -> None:
        if self._feed is not None:
            self._feed.close()
            self._feed = None
        self._key = None

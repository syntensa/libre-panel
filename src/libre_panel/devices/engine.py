"""Libre Panel's engine with a TURZX panel of its own (``libre-panel-engine panel``; stage 1b of
docs/ROADMAP.md, "Next: a native engine").

The engine opens the panel Libre Panel picked and is the only one to talk to it from then on:
it sends the commands it is given (the video's start and stop, the brightness, the PNG layer)
byte for byte as :class:`~libre_panel.devices.turzx_usb.UsbTransport` sends them, and the
pictures into the panel's decoder as :class:`~libre_panel.devices.turzx_video.VideoSession`
does, each when it is due: the time its frame was drawn for, a fixed lead later, from a thread
of its own above the others in priority. Python stays the brain (what is shown, and when it is
drawn); the engine is the hands, and nothing of Python's (its GIL, its collector, a busy main
loop) stands between a frame and the panel. The records it takes and gives: engine/src/wire.rs.

:class:`EngineTransport` is a ``UsbTransport`` for the drivers, :class:`EngineStream` an
encoder of drawn frames as :class:`~libre_panel.devices.h264.Encoder` is ffmpeg's.
"""

from __future__ import annotations

import collections
import itertools
import json
import logging
import os
import queue
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from libre_panel import winsession
from libre_panel.devices.base import DeviceError, leave_place, take_place
from libre_panel.devices.h264 import (
    ABOVE_NORMAL,
    NATIVE,
    NO_WINDOW,
    EncoderStopped,
    _close_pipe,
    _wide_pipe,
    _widen,
)
from libre_panel.i18n import t

log = logging.getLogger(__name__)

MAGIC = b"LPE1"
# what the engine says (wire.rs)
READY, REPLY, COUNTS, FAILED, WAITING = 64, 65, 66, 67, 68
# what it is asked: a command (kind 4) and how its reply is read
OPS = {"command": 1, "ask": 2, "query": 3, "drain": 4, "push": 5}
STREAM, END, PICTURE = 5, 6, 8
# A reply may wait for the picture being sent (5 s a block at most, 1.5 s for room in the
# panel's queue, the hang check's readings): this long beyond a command's own time.
REPLY_SLACK_S = 10.0


class EngineHung(DeviceError):
    """The engine found the panel's video decoder hung (turzx_video.DecoderHung's case); its
    text is the depth the queue stood at."""


class EngineFailed(DeviceError):
    """The engine could not open the panel that pyusb found (or did not start): pyusb may."""


def can_send(engine: str) -> bool:
    """Whether ``engine`` can have the panel (``libre-panel-engine panel``): an engine of
    stage 1a encodes only. Asked once per program."""
    if engine not in _CAN_SEND:
        try:
            done = subprocess.run(
                [engine, "version"], capture_output=True, timeout=10, creationflags=NO_WINDOW
            )
            said = done.stdout.decode("utf-8", "replace")
            _CAN_SEND[engine] = done.returncode == 0 and "panel" in said.split("(")[-1]
        except (OSError, subprocess.SubprocessError):
            _CAN_SEND[engine] = False
    return _CAN_SEND[engine]


_CAN_SEND: dict[str, bool] = {}


def _head(kind: int, a: int = 0, b: int = 0) -> bytes:
    return MAGIC + bytes((kind, 0, 0, 0)) + struct.pack("<II", a & 0xFFFFFFFF, b & 0xFFFFFFFF)


class EngineLink:
    """The engine's process with the panel: its records in and out.

    A thread reads what it says (replies, counts, a failure); writes go out whole, one at a
    time (a frame, a command, a picture of ffmpeg's)."""

    READY_S = 10.0

    def __init__(self, command: list[str], popen: Callable[..., Any] = subprocess.Popen) -> None:
        self.command = command
        self.place = ""
        self.counts: dict[str, float] = {}
        self.waiting = False  # frames wait for the panel to take pictures (it sets the pace)
        self.failed: DeviceError | None = None  # the video path stopped
        self.on_failed: Callable[[DeviceError], None] | None = None
        self.on_counts: Callable[[dict[str, float]], None] | None = None
        self.errors: collections.deque[str] = collections.deque(maxlen=10)
        self._ids = itertools.count(1)
        self._replies: dict[int, tuple[threading.Event, list[tuple[int, bytes]]]] = {}
        self._write_lock = threading.Lock()
        self._ask_lock = threading.Lock()  # a command and its reply as one (UsbTransport's lock)
        self._ready = threading.Event()
        self._ended = threading.Event()
        self._closing = False  # (an end asked for, not one to report)
        if winsession.ending():  # Windows refuses a new program now, with a box that names it
            raise DeviceError("Windows is ending the session: no engine now")
        # its stdin as wide as ffmpeg's (h264.Encoder: Windows gives a pipe 4 KB of room)
        wide = _wide_pipe() if popen is subprocess.Popen else None
        try:
            self.proc = popen(
                command,
                stdin=subprocess.PIPE if wide is None else wide[0],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=NO_WINDOW | ABOVE_NORMAL,
            )
        except OSError as exc:
            if wide is not None:
                _close_pipe(wide)
            raise DeviceError(t("cannot start Libre Panel's engine: {error}", error=exc)) from exc
        if wide is not None:  # (the engine has its end: this process keeps the one it writes to)
            os.close(wide[0])
            self.proc.stdin = wide[1]
        else:
            _widen(self.proc.stdin)
        self._threads = [
            threading.Thread(target=self._read_loop, name="engine-reader", daemon=True),
            threading.Thread(target=self._error_loop, name="engine-errors", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        deadline = time.monotonic() + self.READY_S
        while not self._ready.wait(0.05):
            if self._ended.is_set() or self.proc.poll() is not None:
                self._ended.wait(1.0)  # (its last words)
                self.close()
                raise DeviceError(self.reason())
            if time.monotonic() > deadline:
                self.close()
                raise DeviceError("Libre Panel's engine did not open the panel")

    # -- what it says -----------------------------------------------------------------

    @staticmethod
    def _exact(stream: Any, n: int) -> bytes | None:
        data = bytearray()
        while len(data) < n:
            part = stream.read(n - len(data))
            if not part:
                return None
            data += part
        return bytes(data)

    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        try:
            while True:
                head = self._exact(stdout, 16)
                if head is None or head[:4] != MAGIC:
                    break
                kind = head[4]
                a, b = struct.unpack("<II", head[8:16])
                data = self._exact(stdout, b) if b else b""
                if data is None:
                    break
                self._heard(kind, a, data)
        except (OSError, ValueError):
            pass
        finally:
            self._ended.set()
            for event, _slot in list(self._replies.values()):
                event.set()  # (no reply will come)
            self._ended_by_itself()

    def _ended_by_itself(self) -> None:
        """An engine that ended unasked (it crashed, or was ended from outside) is a link that
        failed: said as one, once, with its last words."""
        if self._closing or self.on_failed is None or self.failed is not None:
            return
        self._threads[1].join(1.0)  # (what it said on its way out)
        self.failed = DeviceError(f"Libre Panel's engine stopped ({self.reason()})")
        self.on_failed(self.failed)

    def _heard(self, kind: int, a: int, data: bytes) -> None:
        if kind == READY:
            self.place = data.decode("utf-8", "replace")
            self._ready.set()
        elif kind == REPLY:
            waiting = self._replies.get(a)
            if waiting is not None and data:
                event, slot = waiting
                slot.append((data[0], data[1:]))
                event.set()
        elif kind == COUNTS:
            try:
                self.counts = json.loads(data)
            except ValueError:
                return
            if self.on_counts is not None:
                self.on_counts(self.counts)
        elif kind == FAILED:
            text = data.decode("utf-8", "replace")
            self.failed = EngineHung(text) if a == 2 else DeviceError(text)
            if self.on_failed is not None:
                self.on_failed(self.failed)
        elif kind == WAITING:
            self.waiting = bool(a)

    def _error_loop(self) -> None:
        try:
            for line in self.proc.stderr:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self.errors.append(text)
                    log.warning("engine: %s", text.removeprefix("libre-panel-engine: "))
        except (OSError, ValueError):
            pass

    @property
    def alive(self) -> bool:
        return not self._ended.is_set() and self.proc.poll() is None

    def reason(self) -> str:
        """Why the engine stopped, as far as it said."""
        if self.errors:
            return self.errors[-1].removeprefix("libre-panel-engine: ")
        code = self.proc.poll()
        return f"exit code {code}" if code is not None else "no answer"

    # -- what it is told ---------------------------------------------------------------

    def write(self, data: bytes | Callable[[Any], None]) -> None:
        """A record, whole; or a callable that writes one (a frame, in its writer thread)."""
        stdin = self.proc.stdin
        with self._write_lock:
            if callable(data):
                data(stdin)
                return
            view = memoryview(data)
            while view:  # an unbuffered pipe may take less than asked
                view = view[stdin.write(view) :]

    def request(
        self, op: str, cmd: int, args: bytes = b"", payload: bytes = b"",
        timeout_ms: int = 2000, reply_ms: int = 0, echo: bool = True,
    ) -> tuple[int, bytes]:  # fmt: skip
        """A command for the panel and how the engine reads its reply (:data:`OPS`): its
        status (0 the reply, 1 none in time, 2 it failed: the text why) and its bytes."""
        with self._ask_lock:
            if not self.alive:
                raise DeviceError(
                    f"USB error on command {cmd}: the engine stopped ({self.reason()})"
                )
            number = next(self._ids)
            event, slot = threading.Event(), []
            self._replies[number] = (event, slot)
            what = OPS[op] | (cmd & 0xFF) << 8 | (0x1_0000 if echo else 0)
            record = (_head(4, number, what)
                      + struct.pack("<IIII", timeout_ms, reply_ms, len(args), len(payload))
                      + args + payload)  # fmt: skip
            try:
                self.write(record)
                answered = event.wait(timeout_ms / 1000 + REPLY_SLACK_S)
            except (OSError, ValueError) as exc:
                raise DeviceError(
                    f"USB error on command {cmd}: the engine is gone ({exc})"
                ) from exc
            finally:
                self._replies.pop(number, None)
            if not answered or not slot:
                why = self.reason() if not self.alive else "in time"
                raise DeviceError(f"USB error on command {cmd}: the engine gave no reply ({why})")
            return slot[0]

    def stream(self, options: list[str], block: int) -> None:
        """A new stream of drawn frames: the engine's encoder with ``options`` (those of
        ``libre-panel-engine encode``), its pictures cut into ``block``-sized blocks."""
        text = "\0".join(options).encode("utf-8")
        self.write(_head(STREAM, block, len(text)) + text)

    def end(self) -> None:
        """The stream ended: its pictures not yet sent are dropped, its encoder closed."""
        self.write(_head(END))

    def picture(self, picture: bytes, block: int) -> None:
        """A picture encoded elsewhere (ffmpeg's: a video from its file, the screen, a colour
        check), to be sent as it is, at once."""
        self.write(_head(PICTURE, block, len(picture)) + picture)

    def close(self, timeout: float = 5.0) -> None:
        """The input ends: the engine sends the commands it still has (the video's stop),
        then lets the panel go and ends."""
        self._closing = True
        try:
            with self._write_lock:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                pass
        for thread in self._threads:
            thread.join(timeout)
        if any(thread.is_alive() for thread in self._threads):
            return  # a thread still reads a pipe; closing it under the thread can block
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


class EngineTransport:
    """:class:`~libre_panel.devices.turzx_usb.UsbTransport` for the drivers, through the
    engine, which has the panel: the same commands, the same replies, the same errors."""

    def __init__(self, link: EngineLink, pid: int, place: str | None = None) -> None:
        self.link, self.pid = link, pid
        self.place = place  # where it is on the bus, while it is open (take_place)

    @staticmethod
    def present(pid: int) -> bool:
        from libre_panel.devices.turzx_usb import UsbTransport

        return UsbTransport.present(pid)

    @classmethod
    def open(
        cls, engine: str, pid: int | None = None, beside: tuple[str, ...] = (), skip: int = 0,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> EngineTransport:  # fmt: skip
        """The panel ``UsbTransport.open`` would take (the same choice: turzx_usb._pick), opened
        by the engine: it is told the product id and where the panel is on the bus."""
        from libre_panel.devices import turzx_usb

        try:
            import usb.core
        except ImportError as exc:
            raise DeviceError("pyusb is missing: pip install 'libre-panel[usb]'") from exc
        try:
            picked = turzx_usb._pick(pid, beside, usb.core, turzx_usb._backend(), skip=skip)
        except usb.core.NoBackendError as exc:
            raise DeviceError("libusb not found: pip install libusb-package") from exc
        if picked is None:
            raise DeviceError(
                t(
                    "no Turing/TURZX USB panel found. Is it plugged in, and is the TURZX app "
                    "closed? (it holds the device exclusively)"
                )
            )
        device, pid = picked
        place = turzx_usb._place(device)
        if not take_place(place):
            raise DeviceError(t("the panel is in use by another panel of Libre Panel"))
        command = [engine, "panel", "--pid", f"{pid:04x}"]
        try:
            ports = list(getattr(device, "port_numbers", None) or ())
        except Exception:  # (some backends cannot tell)
            ports = []
        if ports:
            command += ["--ports", ".".join(str(port) for port in ports)]
        for option, value in (("--bus", "bus"), ("--address", "address")):
            if isinstance(getattr(device, value, None), int):
                command += [option, str(getattr(device, value))]
        try:
            link = EngineLink(command, popen=popen)
        except DeviceError as exc:
            leave_place(place)
            hint = turzx_usb.access_hint(exc)
            raise EngineFailed(hint or str(exc)) from exc
        except BaseException:
            leave_place(place)
            raise
        log.debug("the engine has the panel (%s)", link.place)
        return cls(link, pid, place)

    def _given(self, cmd: int, status: int, data: bytes) -> bytes:
        if status == 2:
            raise DeviceError(data.decode("utf-8", "replace"))
        if status == 1:
            raise DeviceError(f"USB error on command {cmd}: no reply in time")
        return data

    def command(
        self, cmd: int, args: bytes = b"", payload: bytes = b"", timeout_ms: int = 2000
    ) -> bytes:
        status, data = self.link.request("command", cmd, args, payload, timeout_ms)
        return self._given(cmd, status, data)

    def ask(self, cmd: int, args: bytes = b"", timeout_ms: int = 2000, echo: bool = True) -> bytes:
        status, data = self.link.request("ask", cmd, args, b"", timeout_ms, echo=echo)
        return self._given(cmd, status, data)

    def push(
        self, cmd: int, args: bytes, payload: bytes, timeout_ms: int = 5000, reply_ms: int = 200
    ) -> bytes | None:
        status, data = self.link.request("push", cmd, args, payload, timeout_ms, reply_ms)
        return None if status == 1 else self._given(cmd, status, data)

    def query(self, cmd: int, timeout_ms: int = 400) -> bytes | None:
        status, data = self.link.request("query", cmd, b"", b"", timeout_ms)
        return None if status == 1 else self._given(cmd, status, data)

    def drain(self, timeout_ms: int = 50, limit: int = 64) -> int:
        status, data = self.link.request("drain", 0, b"", b"", timeout_ms, limit)
        return struct.unpack("<I", data[:4])[0] if status == 0 and len(data) >= 4 else 0

    def sync(self, attempts: int = 3) -> bytes:
        from libre_panel.devices.turzx_usb import CMD_SYNC

        last: Exception | None = None
        for _ in range(attempts):
            self.drain()
            try:
                return self.command(CMD_SYNC)
            except DeviceError as exc:
                last = exc
        raise DeviceError(f"panel does not answer the sync command: {last}")

    def close(self) -> None:
        self.link.close()
        if self.place is not None:
            leave_place(self.place)
            self.place = None


class EngineStream:
    """One stream of drawn frames into the engine's encoder, the pictures it makes going to
    the panel from the engine: for the display what :class:`~libre_panel.devices.h264.Encoder`
    is with ffmpeg. A writer thread packs each frame into the pipe, so the render loop waits
    for a full pipe at most ``write``'s timeout; a full pipe is the panel setting the pace."""

    def __init__(
        self, link: EngineLink, size: tuple[int, int], options: list[str], block: int,
        lead: float | None = None,
    ) -> None:  # fmt: skip
        self.link, self.size = link, size
        # how long after the time a frame was drawn for the engine sends its picture (seconds;
        # None: the engine's own choice, h264.engine_lead's)
        self.lead = lead
        if lead is not None:
            options = [*options, "--lead-ms", str(max(1, round(lead * 1000)))]
        self.name = NATIVE
        self.maxrate = ""
        self.settings: Any = None
        self.numbered = 0  # frames numbered for the engine (turzx_video._frame_for)
        self.yuv = None
        self.source = None
        self._frames: queue.Queue[Callable[[Any], None] | None] = queue.Queue(maxsize=1)
        self._closed = threading.Event()
        self._base = int(link.counts.get("pictures", 0))
        if not link.alive:
            raise DeviceError(f"Libre Panel's engine stopped ({link.reason()})")
        try:
            link.stream(options, block)
        except (OSError, ValueError) as exc:
            raise DeviceError(f"Libre Panel's engine stopped ({exc})") from exc
        self._writer = threading.Thread(target=self._write_loop, name="engine-writer", daemon=True)
        self._writer.start()

    @property
    def pictures(self) -> int:
        """The pictures of this stream the engine sent to the panel (as it last counted)."""
        return max(0, int(self.link.counts.get("pictures", 0)) - self._base)

    @property
    def alive(self) -> bool:
        return not self._closed.is_set() and self.link.alive and self.link.failed is None

    @property
    def waiting(self) -> bool:
        """Frames wait for the panel to take pictures: it sets the pace, nothing stalled."""
        return self.link.waiting

    def reason(self) -> str:
        return self.link.reason()

    def write(self, frame: Callable[[Any], None], timeout: float = 2.0) -> None:
        if not self.alive:
            raise EncoderStopped(f"the engine stopped ({self.reason()})")
        try:
            self._frames.put(frame, timeout=timeout)
        except queue.Full:
            raise EncoderStopped(f"the engine takes no frames for {timeout:.2f} s") from None

    def _write_loop(self) -> None:
        while True:
            frame = self._frames.get()
            if frame is None or self._closed.is_set():
                return
            try:
                self.link.write(frame)
            except (OSError, ValueError):  # the engine is gone; its reader notices the end
                return

    def close(self, timeout: float = 3.0) -> None:
        """The stream's end: the frames not yet written and the pictures not yet sent are
        dropped (a new stream starts with a keyframe)."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._frames.put_nowait(None)
        except queue.Full:
            try:  # make room for the stop sign; the frame is not needed any more
                self._frames.get_nowait()
                self._frames.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass
        self._writer.join(timeout)
        if self.link.alive:
            try:
                self.link.end()
            except (OSError, ValueError):
                pass

"""The sensor service without Windows: LibreHardwareMonitor read by a thread, and the answers.

``Reader`` opens LibreHardwareMonitor's ``Computer`` and keeps the hardware that clients named
in the last ``REQUEST_SECONDS`` up to date, once a second, as Libre Panel does with the parts
a theme on show reads. While anyone asks, every piece is read now and then as well
(``REFRESH_SECONDS``), so that the rest of the list is not hours old; while nobody asks, nothing
is read at all. A request never touches the hardware: it is answered from what the thread read
last, so however often clients ask, the hardware is read once a second.

``serve(stream, handler)`` is one connection: a request in, an answer out, at most ``RATE`` a
second, until the client goes or says nothing for ``IDLE_SECONDS``. ``winpipe`` gives it the
pipe; the tests give it bytes.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from libre_panel import __version__
from libre_panel.sensors.librehardwaremonitor import LhmSensor
from libre_panel.sensorservice.protocol import (
    HEADER,
    MAX_ANSWER,
    MAX_REQUEST,
    ProtocolError,
    body_length,
    decode,
    encode,
    error_answer,
    hello_answer,
    parse_request,
    read_answer,
)

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0  # how often the hardware that is asked for is read
REQUEST_SECONDS = 10.0  # a piece of hardware named in a request is read for this long
REFRESH_SECONDS = 60.0  # while anyone asks, every piece is read this often
PAUSE_SECONDS = 0.002  # between two pieces of a round
MAX_ASKED = 512  # pieces of hardware kept in mind at once
RATE = 4.0  # requests a second a connection may make, after
BURST = 8  # this many at once
IDLE_SECONDS = 30.0  # a client that asks nothing for this long is let go
MESSAGE_SECONDS = 5.0  # a message that has begun must be through within this long


@dataclass(frozen=True)
class Snapshot:
    """What the reader read last, as the answers give it."""

    state: str = "starting"  # "starting", "running" or "failed"
    error: str = ""
    sensors: tuple[LhmSensor, ...] = ()
    ring0: bool | None = None
    pawnio: str | None = None
    stamp: float | None = None  # when, on the reader's clock; None before the first time


class Reader:
    """LibreHardwareMonitor's ``Computer``, opened and read by a thread of the service.

    ``opener`` returns the open computer (``winservice`` opens every kind of hardware; tests
    give a stand-in), ``pawnio`` the version of the PawnIO driver (None: not installed)."""

    def __init__(
        self,
        opener: Callable[[], Any],
        *,
        poll_seconds: float = POLL_SECONDS,
        request_seconds: float = REQUEST_SECONDS,
        refresh_seconds: float = REFRESH_SECONDS,
        pawnio: Callable[[], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.poll_seconds = max(0.05, poll_seconds)
        self.request_seconds = request_seconds
        self.refresh_seconds = refresh_seconds
        self._opener = opener
        self._pawnio = pawnio
        self._clock = clock
        self._nodes: list[Any] = []  # lhm_inprocess._Node, read by the thread only
        self._asked: dict[str, float] = {}  # hardware id -> when a client last named it
        self._last_request: float | None = None  # when a client last asked at all
        self._everything = False  # every piece in the next round
        self._full: float | None = None  # when every piece was last read
        self._failed: set[str] = set()  # hardware whose update raised (said once)
        self._snapshot = Snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()  # a request after a quiet time: read at once
        self._thread = threading.Thread(target=self._run, name="lhm-service", daemon=True)

    def start(self) -> None:
        self._thread.start()

    # -- the thread -------------------------------------------------------------------------

    def _run(self) -> None:
        computer = None
        try:
            computer = self._opener()
            self._begin(computer)
        except Exception as exc:  # noqa: BLE001 - .NET exceptions come as all kinds
            log.warning("LibreHardwareMonitor did not start: %s", exc, exc_info=True)
            with self._lock:
                self._snapshot = Snapshot("failed", f"{type(exc).__name__}: {exc}")
        else:
            delay = self.poll_seconds
            while not self._stop.is_set():
                self._wake.wait(delay)
                self._wake.clear()
                if self._stop.is_set():
                    break
                started = self._clock()
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001 - one bad round must not end the service
                    log.warning("LibreHardwareMonitor could not be read", exc_info=True)
                delay = max(0.05, self.poll_seconds - (self._clock() - started))
        finally:
            if computer is not None:
                try:
                    computer.Close()
                except Exception:  # noqa: BLE001 - closing at the end changes nothing now
                    log.debug("LibreHardwareMonitor did not close cleanly", exc_info=True)

    def _begin(self, computer: Any) -> None:
        """Everything the computer has, read once in full (so that every sensor is known)."""
        from libre_panel.sensors.lhm_inprocess import _forget_history, build_nodes

        self._nodes = build_nodes(computer)
        _forget_history(self._nodes)
        for node in self._nodes:
            if node.entries:
                self._update(node)
        self._full = self._clock()
        count = sum(len(node.entries) for node in self._nodes)
        log.info("LibreHardwareMonitor is open: %d parts, %d sensors", len(self._nodes), count)
        self._publish()

    def _update(self, node: Any) -> None:
        try:
            node.update()
        except Exception as exc:  # noqa: BLE001 - LibreHardwareMonitor raises .NET exceptions
            if node.hardware_id not in self._failed:
                self._failed.add(node.hardware_id)
                log.warning("could not update a part (%s): %s", node.hardware_type, exc)
            return
        self._failed.discard(node.hardware_id)

    def refresh(self) -> bool:
        """One round: read what was asked for (everything, now and then). False when nobody
        asked, and nothing was read."""
        now = self._clock()
        with self._lock:
            recent = {hw for hw, at in self._asked.items() if now - at <= self.request_seconds}
            self._asked = {hw: at for hw, at in self._asked.items() if hw in recent}
            everything, self._everything = self._everything, False
            asking = (
                self._last_request is not None and now - self._last_request <= self.request_seconds
            )
            if not recent and not everything:
                if asking and self._snapshot.state == "running":
                    # clients ask, for none of the hardware: what was read stays good (as
                    # Libre Panel's own source keeps it), only nobody reads anything now
                    self._snapshot = dataclasses.replace(self._snapshot, stamp=now)
                return False
        full = everything or self._full is None or now - self._full >= self.refresh_seconds
        nodes = [n for n in self._nodes if n.entries and (full or n.hardware_id in recent)]
        for position, node in enumerate(nodes):
            if position:
                self._stop.wait(PAUSE_SECONDS)
            self._update(node)
        if full:
            self._full = now
        self._publish()
        return True

    def _publish(self) -> None:
        from libre_panel.sensors.lhm_inprocess import live_sensors, ring0_state

        ring0 = ring0_state(self._nodes)
        sensors = tuple(live_sensors(self._nodes, ring0))
        pawnio = self._pawnio() if self._pawnio is not None else None
        with self._lock:
            self._snapshot = Snapshot("running", "", sensors, ring0, pawnio, self._clock())

    # -- for the answers --------------------------------------------------------------------

    def ask(self, hardware: Iterable[str], everything: bool = False) -> None:
        """A client wants this hardware read (``everything``: all of it, once). The first
        request after a quiet time reads everything at once: what was read last is old."""
        now = self._clock()
        with self._lock:
            last, self._last_request = self._last_request, now
            quiet = last is None or now - last > self.request_seconds
            for hardware_id in hardware:
                self._asked[hardware_id] = now
            if len(self._asked) > MAX_ASKED:  # a client that names ever new ids: the oldest go
                newest = sorted(self._asked.items(), key=lambda item: item[1])[-MAX_ASKED:]
                self._asked = dict(newest)
            self._everything = self._everything or everything or quiet
        if everything or quiet:
            self._wake.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        """Stop; the thread closes the computer, so nothing is closed while it reads."""
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(10)


class Handler:
    """The answer to each request, from what the reader read last."""

    def __init__(
        self,
        reader: Reader,
        version: str = __version__,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reader = reader
        self.version = version
        self._clock = clock

    def answer(self, message: dict[str, Any]) -> dict[str, Any]:
        try:
            request = parse_request(message)
        except ProtocolError as exc:
            return error_answer(str(exc))
        if request.op == "hello":
            return hello_answer(self.version)
        self.reader.ask(request.hardware, request.everything)
        snap = self.reader.snapshot()
        return read_answer(
            snap.sensors,
            state=snap.state,
            age=None if snap.stamp is None else self._clock() - snap.stamp,
            ring0=snap.ring0,
            pawnio=snap.pawnio,
            error=snap.error,
        )


# -- one connection ------------------------------------------------------------------------------


class ConnectionEnded(ConnectionError):
    """The other side went, said nothing for too long, or the service stops."""


class Stream(Protocol):
    def read(self, size: int, timeout: float) -> bytes:
        """Exactly ``size`` bytes, or ConnectionEnded."""

    def write(self, data: bytes, timeout: float) -> None:
        """All of ``data``, or ConnectionEnded."""

    def wait(self, seconds: float) -> bool:
        """Pause; True when the service stops meanwhile."""


@dataclass
class Limiter:
    """At most ``rate`` requests a second, after a first ``burst`` (a token bucket)."""

    rate: float = RATE
    burst: int = BURST
    _tokens: float = field(default=-1.0, repr=False)
    _at: float | None = field(default=None, repr=False)

    def delay(self, now: float) -> float:
        """How long the request that comes now has to wait."""
        if self._at is None:
            self._tokens = float(self.burst)
        else:
            self._tokens = min(float(self.burst), self._tokens + (now - self._at) * self.rate)
        self._at = now
        self._tokens -= 1.0
        return 0.0 if self._tokens >= 0 else -self._tokens / self.rate


def serve(
    stream: Stream,
    handler: Handler,
    *,
    limiter: Limiter | None = None,
    clock: Callable[[], float] = time.monotonic,
    idle_seconds: float = IDLE_SECONDS,
) -> int:
    """Answer one client until it goes; returns how many requests it made. A request that is
    no JSON object, or not one of the protocol, gets an error answer and the connection goes
    on; a length out of bounds ends it (nobody knows where the next message would start)."""
    limiter = limiter or Limiter()
    count = 0
    while True:
        try:
            length = body_length(stream.read(HEADER.size, idle_seconds), MAX_REQUEST)
            body = stream.read(length, MESSAGE_SECONDS)
        except (ConnectionEnded, ProtocolError) as exc:
            log.debug("a client is let go: %s", exc)
            return count
        count += 1
        delay = limiter.delay(clock())
        if delay > 0 and stream.wait(delay):
            return count
        try:
            answer = handler.answer(decode(body))
        except ProtocolError as exc:
            answer = error_answer(str(exc))
        try:
            data = encode(answer, MAX_ANSWER)
        except ProtocolError as exc:  # thousands of sensors more than any computer has
            data = encode(error_answer(f"the answer is too large: {exc}"), MAX_ANSWER)
        try:
            stream.write(data, MESSAGE_SECONDS)
        except ConnectionEnded:
            return count

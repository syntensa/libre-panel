"""LibreHardwareMonitor through Libre Panel's sensor service (Windows).

Where the sensor service is installed (``libre-panel system-setup --service``, which the
Windows setup runs), it reads LibreHardwareMonitor with the rights that the CPU's, the
mainboard's, the memory's and the drives' sensors need, and Libre Panel asks it over a local
pipe instead of loading LibreHardwareMonitor itself, without those rights.
``ServiceSource`` is that, for ``LibreHardwareMonitorProvider``, with the face of
``InProcessSource`` (start, read, status, wait_ready, close).

- Only what a theme on show reads is kept up to date: each request names the hardware with a
  sensor that the themes read (by raw key, or by the friendly key it makes), as the source
  inside Libre Panel updates only those. The first request of a connection asks for
  everything once, so that every sensor is known.
- The pipe's server must be the service: the process the Service Control Manager names for
  LibrePanelSensors. A program that took the pipe's name is not talked to.
- A service that is installed but not running (its start comes a while after Windows starts)
  is asked to start; it lets everyone signed in at this PC do that.
- While it does not answer it is asked again with growing pauses; after ``WAIT_SECONDS``
  ``on_failure`` tells the provider, which reads LibreHardwareMonitor another way meanwhile,
  and ``on_ready`` tells it when the service answers again.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from libre_panel.sensors.base import Reading, wanted, wanted_keys
from libre_panel.sensors.librehardwaremonitor import LhmSensor, LhmStatus, readings_from_sensors
from libre_panel.sensorservice import SERVICE_NAME
from libre_panel.sensorservice.protocol import (
    PROTOCOL,
    ProtocolError,
    Readings,
    ServiceError,
    hello_request,
    parse_hello,
    parse_readings,
    read_request,
)

log = logging.getLogger(__name__)

WAIT_SECONDS = 20.0  # without an answer for this long, the provider reads another way meanwhile
REQUEST_SECONDS = 2.0  # one request and its answer
RETRY_SECONDS = (1.0, 10.0)  # pauses between tries while it does not answer: doubling up to
SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
DISABLED = 4  # the service's start type "Disabled"


class Unavailable(Exception):
    """The service cannot be asked now; the text says why."""


class Link(Protocol):
    """One connection to the service."""

    def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]: ...

    def close(self) -> None: ...


def registration(name: str = SERVICE_NAME) -> dict[str, Any] | None:
    """What Windows has on record for the service (its program, its start type), from the
    registry any user may read; None where it is not installed, and off Windows."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{SERVICES_KEY}\{name}") as key:
            found = {}
            for value in ("ImagePath", "Start"):
                try:
                    found[value] = winreg.QueryValueEx(key, value)[0]
                except OSError:
                    found[value] = None
            return found
    except OSError:
        return None


def installed() -> bool:
    """Whether the sensor service is installed here and not switched off."""
    found = registration()
    return found is not None and found.get("Start") != DISABLED


def open_link() -> Link:
    """A connection to the running service, after making sure that the pipe's server is it.
    A service that is installed but stopped is asked to start (and this try fails)."""
    from libre_panel.sensorservice import winapi, winpipe

    status = winapi.service_status(SERVICE_NAME)
    if status is None:
        raise Unavailable("the sensor service is not installed")
    state, pid = status
    if state != winapi.SERVICE_RUNNING:
        if state == winapi.SERVICE_STOPPED and winapi.start_service(SERVICE_NAME):
            raise Unavailable("the sensor service is starting")
        raise Unavailable(f"the sensor service is {winapi.STATE_NAMES.get(state, state)}")
    link = winpipe.connect()
    if not pid or link.server_pid() != pid:
        link.close()
        raise Unavailable("the pipe is held by another program than the sensor service")
    return link


def status_of(readings: Readings | None, why: str = "") -> LhmStatus:
    """The status of the source after an answer (or none, and why)."""
    if readings is None:
        return LhmStatus("service", error=why)
    error = readings.error if readings.state == "failed" else ""
    # the service runs with the system's rights: what keeps the CPU's sensors away is PawnIO
    return LhmStatus("service", True, readings.ring0, readings.pawnio, error)


class ServiceSource:
    """The sensor service, asked by a thread of its own once a second."""

    def __init__(
        self,
        *,
        poll_seconds: float = 1.0,
        stale_seconds: float = 5.0,
        gpu: str | None = None,
        on_ready: Callable[[LhmStatus], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        link: Callable[[], Link] = open_link,
        clock: Callable[[], float] = time.monotonic,
        wait_seconds: float = WAIT_SECONDS,
    ) -> None:
        self.poll_seconds = max(0.1, poll_seconds)
        self.stale_seconds = max(stale_seconds, 3 * self.poll_seconds) if stale_seconds > 0 else 0
        self.gpu = gpu
        self.wait_seconds = wait_seconds
        self.version = ""  # the service's, once it said hello
        self._open = link
        self._on_ready = on_ready
        self._on_failure = on_failure
        self._clock = clock
        self._link: Link | None = None
        self._everything = True  # the next request asks for every piece once
        self._keys: dict[str, set[str]] = {}  # hardware id -> the friendly keys its sensors make
        self._learned = 0  # grows with each key or piece of hardware learned
        self._needed: tuple[Any, list[str]] | None = None
        self._latest: dict[str, Reading] = {}
        self._beat: float | None = None  # when the readings shown were read
        self._status = LhmStatus("service")
        self._failed = False  # on_failure was called and nothing answered since
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()  # the first readings are in, or it gave up for now
        self._thread = threading.Thread(target=self._run, name="lhm-service", daemon=True)

    def start(self) -> None:
        self._thread.start()

    # -- the thread -------------------------------------------------------------------------

    def _run(self) -> None:
        since = self._clock()  # when the service last answered (or the start)
        pause = RETRY_SECONDS[0]
        try:
            while not self._stop.is_set():
                started = self._clock()
                try:
                    self.ask()
                except (Unavailable, ServiceError, ProtocolError, OSError, EOFError) as exc:
                    self._lost(str(exc) or type(exc).__name__, since)
                    self._stop.wait(pause)
                    pause = min(pause * 2, RETRY_SECONDS[1])
                    continue
                except Exception as exc:  # noqa: BLE001 - whatever it was, the thread goes on
                    log.warning("the sensor service could not be read", exc_info=True)
                    self._lost(f"{type(exc).__name__}: {exc}", since)
                    self._stop.wait(pause)
                    continue
                since, pause = self._clock(), RETRY_SECONDS[0]
                if self._failed:
                    self._failed = False
                    log.info("the sensor service answers again")
                    self._call(self._on_ready, self.status())
                self._stop.wait(max(0.05, self.poll_seconds - (self._clock() - started)))
        finally:
            self._disconnect()

    def _lost(self, why: str, since: float) -> None:
        """No answer: the connection goes, the status says why, and once the service has
        been silent for ``wait_seconds`` the provider is told."""
        self._disconnect()
        with self._lock:
            self._status = LhmStatus("service", error=why)
        if not self._failed and self._clock() - since >= self.wait_seconds:
            self._failed = True
            log.info("the sensor service does not answer: %s", why)
            self._call(self._on_failure, why)
            self._done.set()

    def _disconnect(self) -> None:
        link, self._link = self._link, None
        if link is not None:
            try:
                link.close()
            except OSError:
                pass

    @staticmethod
    def _call(callback: Callable[[Any], None] | None, argument: Any) -> None:
        if callback is None:
            return
        try:
            callback(argument)
        except Exception:  # noqa: BLE001 - the provider's business; the thread goes on
            log.exception("the sensor service: a callback failed")

    def ask(self) -> None:
        """One round: connect (and say hello) if need be, ask for the readings, keep them."""
        if self._link is None:
            link = self._open()
            try:
                hello = parse_hello(link.request(hello_request(), REQUEST_SECONDS))
            except BaseException:
                link.close()
                raise
            if hello.protocol != PROTOCOL:
                link.close()
                raise Unavailable(
                    f"the sensor service speaks protocol {hello.protocol}, this Libre Panel "
                    f"{PROTOCOL}: set it up again from this version"
                )
            self._link, self.version, self._everything = link, hello.service, True
        request = read_request(self.needed(), self._everything)
        answer = parse_readings(self._link.request(request, REQUEST_SECONDS))
        self._everything = False
        if answer.state == "failed":
            raise Unavailable(f"LibreHardwareMonitor did not start in the service: {answer.error}")
        self._take(answer)

    def _take(self, answer: Readings) -> None:
        sources: dict[str, tuple[str, ...]] = {}
        readings = readings_from_sensors(answer.sensors, self.gpu, sources)
        self._learn(answer.sensors, sources)
        # what the service read last may be old (nobody asked for a while): it reads anew now
        fresh = answer.age is not None and (
            self.stale_seconds <= 0 or answer.age <= self.stale_seconds
        )
        status = status_of(answer)
        with self._lock:
            self._status = status
            if fresh:
                self._latest = readings
                self._beat = self._clock() - (answer.age or 0.0)
        if fresh and not self._done.is_set():
            count = len(answer.sensors)
            log.info("LibreHardwareMonitor is read through the sensor service (%d sensors)", count)
            if status.ring0 is False:
                log.info("the sensor service cannot read the CPU's sensors: PawnIO is missing")
            self._call(self._on_ready, status)
            self._done.set()

    def _learn(self, sensors: Iterable[LhmSensor], sources: dict[str, tuple[str, ...]]) -> None:
        """Which piece of hardware makes which friendly key; never forgotten (a sensor without
        a value now still belongs to its hardware)."""
        hardware_of = {}
        for sensor in sensors:
            hardware_of[sensor.sensor_id] = sensor.hardware_id
            if sensor.hardware_id not in self._keys:
                self._keys[sensor.hardware_id] = set()
                self._learned += 1
        for key, ids in sources.items():
            if key.startswith("lhm:"):
                continue
            for sensor_id in ids:
                keys = self._keys.get(hardware_of.get(sensor_id, ""))
                if keys is not None and key not in keys:
                    keys.add(key)
                    self._learned += 1

    def needed(self) -> list[str]:
        """The hardware with a sensor that a theme on show reads (worked out again only when
        what the themes read, or what is known, has changed)."""
        state = (wanted_keys(), self._learned)
        if self._needed is None or self._needed[0] != state:
            found = [
                hardware
                for hardware, keys in self._keys.items()
                if wanted(f"lhm:{hardware}/") or any(wanted(key) for key in keys)
            ]
            self._needed = (state, found)
        return self._needed[1]

    # -- for the provider ---------------------------------------------------------------------

    def read(self) -> dict[str, Reading]:
        with self._lock:
            if self._beat is None or (
                self.stale_seconds > 0 and self._clock() - self._beat > self.stale_seconds
            ):
                return {}  # not read yet, or not lately: no numbers that stand still
            return dict(self._latest)

    def live(self) -> bool:
        """Whether the service gives readings now."""
        return bool(self.read())

    def status(self) -> LhmStatus:
        with self._lock:
            return self._status

    def wait_ready(self, timeout: float) -> bool:
        """Whether the first readings are in (or it gave up for now) within ``timeout``."""
        return self._done.wait(timeout)

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(10)
        self._done.set()


# -- `libre-panel sensors --check-service` ---------------------------------------------------------


def check(
    link: Callable[[], Link] = open_link,
    platform: str | None = None,
    seconds: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, list[str]]:
    """Whether the sensor service answers this account with readings, and what it says:
    (worked, lines to print)."""
    if (platform or sys.platform) != "win32":
        return False, ["The sensor service is for Windows only."]
    try:
        connection = link()
    except (Unavailable, OSError) as exc:
        return False, [f"The sensor service does not answer: {exc}"]
    try:
        hello = parse_hello(connection.request(hello_request(), REQUEST_SECONDS))
        lines = [f"sensor service {hello.service}, protocol {hello.protocol}"]
        answer = parse_readings(connection.request(read_request((), True), REQUEST_SECONDS))
        waited = 0.0
        while answer.state == "starting" or answer.age is None or answer.age > 2.0:
            if waited >= seconds:
                break
            sleep(0.5)
            waited += 0.5
            answer = parse_readings(connection.request(read_request((), True), REQUEST_SECONDS))
    except (ServiceError, ProtocolError, OSError, EOFError) as exc:
        return False, [f"The sensor service does not answer properly: {exc}"]
    finally:
        connection.close()
    if answer.state == "failed":
        return False, [*lines, f"LibreHardwareMonitor did not start in it: {answer.error}"]
    readings = readings_from_sensors(answer.sensors)
    parts = {sensor.hardware_id for sensor in answer.sensors}
    state = {True: "available", False: "not available", None: "no such sensors"}[answer.ring0]
    pawnio = "not installed" if answer.pawnio is None else answer.pawnio or "installed"
    lines += [
        f"read through the sensor service: {len(answer.sensors)} sensors of {len(parts)} parts",
        f"CPU temperature, power and clock: {state}",
        f"PawnIO driver: {pawnio}",
    ]
    for key in ("cpu.temp", "cpu.power", "gpu.temp", "mem.temp"):
        reading = readings.get(key)
        if reading is not None and isinstance(reading.value, float):
            lines.append(f"{key}: {reading.value:.1f} {reading.unit}".rstrip())
    return bool(answer.sensors), lines

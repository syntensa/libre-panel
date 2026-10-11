"""What the sensor service and Libre Panel say to each other over the pipe.

A message is one JSON object of at most a set number of bytes in UTF-8, sent after its length
as four bytes (little endian). The client asks, the service answers, one at a time, and these
are the only questions:

- ``{"v": 1, "op": "hello"}``: which service and protocol this is.
  ``{"v": 1, "ok": true, "service": "0.5.0", "protocol": 1}``
- ``{"v": 1, "op": "read", "hardware": ["/amdcpu/0", ...], "all": false}``: the readings. The
  service updates the hardware named here for the next few seconds (``all``: every piece
  once), and answers at once with what it read last::

    {"v": 1, "ok": true, "state": "running", "error": "", "age": 0.4, "ring0": true,
     "pawnio": "2.0.1", "sensors": [[sensor id, hardware id, hardware name, type, label,
     value, unit], ...]}

  ``age`` is how many seconds ago that was (null before the first time), ``state`` is
  ``starting``, ``running`` or ``failed`` (then ``error`` says why).

Everything that comes in is checked here before anything else looks at it, on both sides: a
length out of bounds ends the connection, anything else that is not as above is answered with
``{"v": 1, "ok": false, "error": "..."}`` (the service) or taken for no answer (the client).
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from libre_panel.sensors.librehardwaremonitor import LhmSensor

PROTOCOL = 1
HEADER = struct.Struct("<I")  # the length of the message that follows
MAX_REQUEST = 4096  # bytes: a request names at most MAX_HARDWARE pieces of hardware
MAX_ANSWER = 4 * 1024 * 1024  # bytes: some thousand sensors fit many times over
MAX_HARDWARE = 64  # pieces of hardware one request may name
MAX_SENSORS = 10_000  # sensors one answer may carry
MAX_TEXT = 300  # characters of a name, a label, a version or an error
STATES = ("starting", "running", "failed")
# A hardware id as LibreHardwareMonitor makes them: "/amdcpu/0", "/nic/%7B…%7D"; printable
# ASCII without spaces, so nothing else gets through to the service's set of wishes
_HARDWARE_ID = re.compile(r"/[!-~]{1,199}")


class ProtocolError(ValueError):
    """A message that is not one of this protocol; the text says what is wrong with it."""


# -- framing -------------------------------------------------------------------------------------


def encode(message: dict[str, Any], limit: int) -> bytes:
    """``message`` with its length in front, ready to be written. Raises ProtocolError when it
    is longer than ``limit`` bytes (or holds what JSON cannot say, such as NaN)."""
    try:
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"cannot be sent: {exc}") from exc
    data = body.encode("utf-8")
    if len(data) > limit:
        raise ProtocolError(f"{len(data)} bytes, more than {limit}")
    return HEADER.pack(len(data)) + data


def body_length(header: bytes, limit: int) -> int:
    """The length that four bytes announce; ProtocolError when it is none or above ``limit``
    (then the connection cannot go on: nobody knows where the next message starts)."""
    if len(header) != HEADER.size:
        raise ProtocolError("the length is cut short")
    (length,) = HEADER.unpack(header)
    if not 0 < length <= limit:
        raise ProtocolError(f"a message of {length} bytes (at most {limit})")
    return length


def _no_constant(name: str) -> None:
    raise ValueError(f"{name} is not a number JSON has")


def decode(body: bytes) -> dict[str, Any]:
    """The JSON object in ``body``; ProtocolError for anything else."""
    try:
        message = json.loads(body.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError(f"not JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("not a JSON object")
    return message


def read_message(read_exact: Callable[[int], bytes], limit: int) -> dict[str, Any]:
    """One message from a stream: ``read_exact(n)`` returns exactly ``n`` bytes or raises."""
    return decode(read_exact(body_length(read_exact(HEADER.size), limit)))


# -- requests (checked by the service) -----------------------------------------------------------


@dataclass(frozen=True)
class Request:
    op: str  # "hello" or "read"
    hardware: tuple[str, ...] = ()  # read: the hardware to keep up to date
    everything: bool = False  # read: update every piece once


def hello_request() -> dict[str, Any]:
    return {"v": PROTOCOL, "op": "hello"}


def read_request(hardware: Iterable[str] = (), everything: bool = False) -> dict[str, Any]:
    wanted = list(dict.fromkeys(hardware))[:MAX_HARDWARE]
    return {"v": PROTOCOL, "op": "read", "hardware": wanted, "all": everything}


def parse_request(message: dict[str, Any]) -> Request:
    """What a client asks, checked; ProtocolError for anything this protocol does not have."""
    if message.get("v") != PROTOCOL:
        raise ProtocolError(f"protocol {message.get('v')!r} is not {PROTOCOL}")
    op = message.get("op")
    if op == "hello":
        return Request("hello")
    if op != "read":
        raise ProtocolError("a request is hello or read")
    hardware = message.get("hardware", [])
    if not isinstance(hardware, list) or len(hardware) > MAX_HARDWARE:
        raise ProtocolError(f"hardware is a list of at most {MAX_HARDWARE} ids")
    for item in hardware:
        if not isinstance(item, str) or not _HARDWARE_ID.fullmatch(item):
            raise ProtocolError("a hardware id is a path such as /amdcpu/0")
    everything = message.get("all", False)
    if not isinstance(everything, bool):
        raise ProtocolError("all is true or false")
    return Request("read", tuple(dict.fromkeys(hardware)), everything)


# -- answers (made by the service, checked by the client) ----------------------------------------


def error_answer(why: str) -> dict[str, Any]:
    return {"v": PROTOCOL, "ok": False, "error": why[:MAX_TEXT]}


def hello_answer(version: str) -> dict[str, Any]:
    return {"v": PROTOCOL, "ok": True, "service": version[:MAX_TEXT], "protocol": PROTOCOL}


def read_answer(
    sensors: Iterable[LhmSensor],
    *,
    state: str,
    age: float | None,
    ring0: bool | None,
    pawnio: str | None,
    error: str = "",
) -> dict[str, Any]:
    rows = [
        [s.sensor_id, s.hardware_id, s.hardware_name, s.sensor_type, s.label, s.value, s.unit]
        for s in sensors
        if math.isfinite(s.value)
    ]
    return {
        "v": PROTOCOL,
        "ok": True,
        "state": state,
        "error": error[:MAX_TEXT],
        "age": None if age is None else round(max(0.0, age), 3),
        "ring0": ring0,
        "pawnio": pawnio,
        "sensors": rows[:MAX_SENSORS],
    }


class ServiceError(Exception):
    """The service understood the request and said no (``{"ok": false}``)."""


@dataclass(frozen=True)
class Hello:
    service: str
    protocol: int


@dataclass(frozen=True)
class Readings:
    state: str
    error: str
    age: float | None
    ring0: bool | None
    pawnio: str | None
    sensors: tuple[LhmSensor, ...]


def _text(value: Any, what: str, empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT or "\x00" in value:
        raise ProtocolError(f"{what} is not a text of at most {MAX_TEXT} characters")
    if not empty and not value:
        raise ProtocolError(f"{what} is empty")
    return value


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProtocolError(f"{what} is not a number")
    try:
        number = float(value)
    except OverflowError as exc:  # an integer of hundreds of digits
        raise ProtocolError(f"{what} is too large") from exc
    if not math.isfinite(number):
        raise ProtocolError(f"{what} is not a finite number")
    return number


def _checked(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("v") != PROTOCOL:
        raise ProtocolError(f"protocol {message.get('v')!r} is not {PROTOCOL}")
    if message.get("ok") is False:
        raise ServiceError(_text(message.get("error", ""), "error"))
    if message.get("ok") is not True:
        raise ProtocolError("ok is true or false")
    return message


def parse_hello(message: dict[str, Any]) -> Hello:
    message = _checked(message)
    protocol = message.get("protocol")
    if isinstance(protocol, bool) or not isinstance(protocol, int):
        raise ProtocolError("protocol is not a whole number")
    return Hello(_text(message.get("service"), "service", empty=False), protocol)


def _sensor(row: Any) -> LhmSensor:
    if not isinstance(row, list) or len(row) != 7:
        raise ProtocolError("a sensor is a list of seven values")
    sensor_id, hardware_id, name, kind, label, value, unit = row
    return LhmSensor(
        _text(sensor_id, "a sensor id", empty=False),
        _text(hardware_id, "a hardware id", empty=False),
        _text(name, "a hardware name"),
        _text(kind, "a sensor type", empty=False),
        _text(label, "a label"),
        _number(value, "a value"),
        _text(unit, "a unit"),
    )


def parse_readings(message: dict[str, Any]) -> Readings:
    """The answer to ``read``, checked whole: one thing wrong in it and none of it is taken."""
    message = _checked(message)
    state = message.get("state")
    if state not in STATES:
        raise ProtocolError(f"state is one of {', '.join(STATES)}")
    age = message.get("age")
    age = None if age is None else _number(age, "age")
    ring0 = message.get("ring0")
    if ring0 is not None and not isinstance(ring0, bool):
        raise ProtocolError("ring0 is true, false or null")
    pawnio = message.get("pawnio")
    pawnio = None if pawnio is None else _text(pawnio, "pawnio")
    rows = message.get("sensors")
    if not isinstance(rows, list) or len(rows) > MAX_SENSORS:
        raise ProtocolError(f"sensors is a list of at most {MAX_SENSORS}")
    return Readings(
        state,
        _text(message.get("error", ""), "error"),
        age,
        ring0,
        pawnio,
        tuple(_sensor(row) for row in rows),
    )

"""The UPS, through Network UPS Tools (NUT): its battery, how long it lasts, its load and
whether the PC runs on it. NUT's server (``upsd``, on this PC, a NAS or a Raspberry Pi, and
WinNUT's on Windows) says it to anyone on its port; nothing to install here.

``[sensors.nut]``::

    host = "127.0.0.1"   # where upsd runs
    port = 3493
    ups = ""             # its name in ups.conf ("": the first it has)
    update_s = 5
    # username = "..."   # only where upsd wants one to read (upsd.users)
    # password = "..."

Readings: ``ups.charge`` (%), ``ups.runtime`` (min), ``ups.load`` (%), ``ups.power`` (W, as
the UPS says, or its load of the nominal power), ``ups.input`` (V), ``ups.on_battery`` (1 while
the mains is off) and ``ups.status`` ("On mains", "On battery", "Battery low"). A UPS that does
not answer keeps its last values for three times ``update_s``, then they go. It is asked in a
thread of its own, never on the frame's time.
"""

from __future__ import annotations

import logging
import shlex
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from libre_panel.i18n import t
from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

PORT = 3493
TIMEOUT_S = 5.0
MAX_LINES = 500  # a UPS has some 50 to 150 values


class NutError(Exception):
    """What upsd answered with ERR, or a broken answer."""


class Client:
    """One talk with upsd: a line out, lines back (the protocol is text, a line each)."""

    def __init__(self, host: str, port: int, timeout: float = TIMEOUT_S) -> None:
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._file = self._socket.makefile("rb")

    def ask(self, line: str) -> str:
        self._socket.sendall(line.encode("utf-8") + b"\n")
        return self._line()

    def _line(self) -> str:
        raw = self._file.readline(4096)
        if not raw:
            raise NutError("upsd closed the connection")
        text = raw.decode("utf-8", "replace").rstrip("\r\n")
        if text.startswith("ERR "):
            raise NutError(text[4:])
        return text

    def listing(self, what: str) -> list[list[str]]:
        """``LIST <what>``: the lines between BEGIN and END, each split as the shell splits."""
        first = self.ask(f"LIST {what}")
        if first != f"BEGIN LIST {what}":
            raise NutError(f"unexpected answer {first!r}")
        rows = []
        for _ in range(MAX_LINES):
            line = self._line()
            if line == f"END LIST {what}":
                return rows
            rows.append(shlex.split(line))
        raise NutError("too long an answer")

    def close(self) -> None:
        try:
            self._socket.sendall(b"LOGOUT\n")
        except OSError:
            pass
        self._socket.close()


def readings(values: dict[str, str]) -> dict[str, Reading]:
    """The readings of a UPS from NUT's variables (``battery.charge`` and so on)."""

    def number(name: str) -> float | None:
        try:
            return float(values[name])
        except (KeyError, ValueError):
            return None

    out: dict[str, Reading] = {}

    def put(key: str, value: float | str | None, unit: str, label: str) -> None:
        if value is not None:
            out[key] = Reading(key, value, unit, label)

    put("ups.charge", number("battery.charge"), "%", "UPS battery")
    runtime = number("battery.runtime")
    put("ups.runtime", None if runtime is None else round(runtime / 60, 1), "min", "UPS runtime")
    load = number("ups.load")
    put("ups.load", load, "%", "UPS load")
    power = number("ups.realpower")
    nominal = number("ups.realpower.nominal")
    if power is None and nominal is not None and load is not None:
        power = round(nominal * load / 100)
    put("ups.power", power, "W", "UPS power")
    put("ups.input", number("input.voltage"), "V", "UPS input")
    flags = values.get("ups.status", "").split()
    if flags:
        on_battery = "OB" in flags
        put("ups.on_battery", 1 if on_battery else 0, "", "UPS on battery")
        text = t("On battery") if on_battery else t("On mains")
        if "LB" in flags:
            text = t("Battery low")
        put("ups.status", text, "", "UPS")
    return out


class NutProvider(SensorProvider):
    """``ups.*`` from NUT's upsd."""

    name = "nut"

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        *,
        start: bool = True,
        connect: Callable[[str, int], Client] = Client,
    ) -> None:
        super().__init__(options)
        self.host = str(self.options.get("host") or "127.0.0.1")
        self.port = int(self.options.get("port") or PORT)
        self.ups = str(self.options.get("ups") or "")
        self.every = max(1.0, float(self.options.get("update_s", 5)))
        self._connect = connect
        self._got: tuple[float, dict[str, Reading]] | None = None
        self._failed = False  # said once, until it answers again
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if start:
            threading.Thread(target=self._run, daemon=True, name="sensors-nut").start()

    def fetch(self) -> None:
        """Ask upsd once and keep what it says."""
        try:
            client = self._connect(self.host, self.port)
            try:
                user, password = self.options.get("username"), self.options.get("password")
                if user:
                    client.ask(f"USERNAME {user}")
                    client.ask(f"PASSWORD {password or ''}")
                name = self.ups
                if not name:
                    found = client.listing("UPS")
                    if not found:
                        raise NutError("upsd knows no UPS")
                    name = found[0][1]
                rows = client.listing(f"VAR {name}")
            finally:
                client.close()
        except (OSError, NutError, IndexError, ValueError) as exc:
            if not self._failed:
                self._failed = True
                log.warning("nut: %s:%d does not answer: %s", self.host, self.port, exc)
            return
        if self._failed:
            self._failed = False
            log.info("nut: %s:%d answers again", self.host, self.port)
        values = {row[2]: row[3] for row in rows if len(row) >= 4 and row[0] == "VAR"}
        with self._lock:
            self._got = (time.monotonic(), readings(values))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.fetch()
            self._stop.wait(self.every)

    def read(self) -> dict[str, Reading]:
        with self._lock:
            got = self._got
        if got is None or time.monotonic() - got[0] > 3 * self.every:
            return {}
        return dict(got[1])

    def close(self) -> None:
        self._stop.set()

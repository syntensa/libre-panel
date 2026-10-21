"""Power use: the watts the PC draws now, and the energy of today and of this month, with what
it costs at the price you give.

The watts are those the sensors report (``cpu.power`` and ``gpu.power``, or the ``sensors``
named) and ``rest_w`` for the rest of the PC (the mainboard, drives, fans, what the power
supply loses: some 40 to 80 W). It is an estimate: only a meter at the socket knows.

``[services.power]``::

    sensors = ["cpu.power", "gpu.power"]
    rest_w = 50
    price = 0.30        # per kWh; 0: no cost
    currency = "€"

Readings: ``power.watts``, ``power.today`` and ``power.month`` (kWh), with a price
``power.today.cost`` and ``power.month.cost`` (in the currency). The totals are kept in the
service's folder (``power.json``) every minute and when it stops, so a restart loses nothing;
a new day and a new month begin at zero. Time the PC sleeps is not counted.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from libre_panel.plugins import Service
from libre_panel.sensors.base import Snapshot
from libre_panel.services.readings import number

KEEP_S = 60.0  # how often the totals are written
GAP_S = 10.0  # a longer pause between two samples (sleep, a stall) is not counted
FILE = "power.json"


class Power(Service):
    """Watts now, kWh today and this month, and their cost."""

    name = "power"
    api = 1
    options = {
        "sensors": ("list", ["cpu.power", "gpu.power"]),
        "rest_w": ("number", 0.0),
        "price": ("number", 0.0),
        "currency": ("string", "€"),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(host, options)
        self.clock, self.wall = clock, wall
        self.keys = [str(key) for key in options["sensors"] if str(key).strip()]
        self.rest = max(0.0, float(options["rest_w"]))
        self.price = max(0.0, float(options["price"]))
        self.currency = str(options["currency"])
        self.day, self.month = "", ""
        self.today_wh = self.month_wh = 0.0
        self._last: float | None = None  # when the last sample came
        self._kept = clock()
        self._lock = threading.Lock()

    # -- the service ------------------------------------------------------------------------

    def start(self) -> None:
        self._load()
        self.host.want(*self.keys)
        self.host.on("sample", self.on_sample)

    def stop(self) -> None:
        self._save()

    def _path(self) -> Any:
        return self.host.data_dir / FILE

    def _load(self) -> None:
        try:
            kept = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
        if not isinstance(kept, dict):
            kept = {}
        now = self.wall()
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        with self._lock:
            self.day, self.month = day, month
            if kept.get("day") == day:
                self.today_wh = number(kept.get("today_wh")) or 0.0
            if kept.get("month") == month:
                self.month_wh = number(kept.get("month_wh")) or 0.0

    def _save(self) -> None:
        with self._lock:
            kept = {"day": self.day, "today_wh": round(self.today_wh, 3),
                    "month": self.month, "month_wh": round(self.month_wh, 3)}  # fmt: skip
        try:
            self._path().write_text(json.dumps(kept), encoding="utf-8")
        except OSError as exc:
            self.host.log.warning("power: the totals cannot be kept: %s", exc)

    # -- every sample -------------------------------------------------------------------------

    def watts(self, snapshot: Snapshot) -> float | None:
        """What the PC draws now: the sensors' sum and the rest; None without any sensor."""
        found = [number(snapshot.value(key)) for key in self.keys]
        values = [value for value in found if value is not None and value >= 0]
        if not values:
            return None
        return sum(values) + self.rest

    def on_sample(self, snapshot: Snapshot) -> None:
        now, wall = self.clock(), self.wall()
        watts = self.watts(snapshot)
        with self._lock:
            day, month = wall.strftime("%Y-%m-%d"), wall.strftime("%Y-%m")
            if day != self.day:
                self.day, self.today_wh = day, 0.0
            if month != self.month:
                self.month, self.month_wh = month, 0.0
            last, self._last = self._last, now
            if watts is not None and last is not None and 0 < now - last <= GAP_S:
                energy = watts * (now - last) / 3600
                self.today_wh += energy
                self.month_wh += energy
            today, this_month = self.today_wh / 1000, self.month_wh / 1000
        self.host.publish("power.watts", None if watts is None else round(watts, 1), "W", "Power")
        self.host.publish("power.today", round(today, 3), "kWh", "Energy today")
        self.host.publish("power.month", round(this_month, 3), "kWh", "Energy this month")
        if self.price > 0:
            unit = self.currency
            self.host.publish("power.today.cost", round(today * self.price, 2), unit, "Cost today")
            self.host.publish(
                "power.month.cost", round(this_month * self.price, 2), unit, "Cost this month"
            )
        if now - self._kept >= KEEP_S:
            self._kept = now
            self._save()

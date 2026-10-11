"""Power from energy counters: the processor's RAPL zones, a graphics card's energy file.

The kernel counts the energy a processor package has used, in microjoules, in
``/sys/class/powercap/intel-rapl:0/energy_uj`` (Intel since Sandy Bridge, AMD from Zen, through
the same driver). The power in watts is the difference between two readings over the time
between them. Since 2020 (the PLATYPUS side channel) the kernel lets only root read those
files; ``libre-panel system-setup --linux`` can open them, if the person says so
(``linux_setup.py``). Without it the processor's power is simply not there.

A counter counts up to a range, then starts again from zero: the range is in
``max_energy_range_uj`` next to it.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from libre_panel.sensors.hwmon import read_number, read_text

POWERCAP = "sys/class/powercap"
_ZONE = re.compile(r"^intel-rapl(?:-mmio)?:\d+$")  # a package, not its parts (intel-rapl:0:0)
MIN_SECONDS = 0.25  # readings closer than this give the last power again: a counter ticks


@dataclass(frozen=True)
class Counter:
    """An energy counter: where it is, and where it starts again (0: not told)."""

    path: Path
    wrap: int = 0


def package_counters(root: Path = Path("/")) -> list[Counter]:
    """The counters of the processor packages (a PC with two sockets has two). The zones of the
    MSR interface come first; the memory-mapped ones (``intel-rapl-mmio``) report the same
    packages and count only where there are no others."""
    folder = Path(root) / POWERCAP
    try:
        names = sorted(name for name in os.listdir(folder) if _ZONE.match(name))
    except OSError:
        return []
    found: dict[bool, list[Counter]] = {False: [], True: []}
    for name in names:
        zone = folder / name
        if not (read_text(zone / "name") or "").startswith("package"):
            continue
        if not (zone / "energy_uj").exists():
            continue
        wrap = read_number(zone / "max_energy_range_uj") or 0
        found["mmio" in name].append(Counter(zone / "energy_uj", wrap))
    return found[False] or found[True]


def unreadable(counters: list[Counter], can_read: Callable[[Path], bool] | None = None) -> bool:
    """Whether the counters are there but this user may not read them (the kernel's way of
    keeping the side channel shut). ``can_read`` says whether a file may be read; the default
    asks the system (``os.access``)."""
    can = can_read or (lambda path: os.access(path, os.R_OK))
    return bool(counters) and not any(can(counter.path) for counter in counters)


class Joules:
    """Watts from counters of microjoules, asked now and then. The first call has nothing to
    compare with and gives None; a counter that cannot be read leaves the sum out (None)."""

    def __init__(
        self, counters: list[Counter], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.counters = counters
        self._clock = clock
        self._last: tuple[float, list[int]] | None = None
        self._watts: float | None = None

    def watts(self) -> float | None:
        now = self._clock()
        energies = [read_number(counter.path) for counter in self.counters]
        if not self.counters or any(energy is None for energy in energies):
            self._last, self._watts = None, None  # a counter that is gone gives no half sum
            return None
        known = [int(energy) for energy in energies if energy is not None]
        if self._last is None:
            self._last = (now, known)
            return None
        then, before = self._last
        if now - then < MIN_SECONDS:
            return self._watts
        joules = 0.0
        for counter, old, new in zip(self.counters, before, known, strict=True):
            step = new - old
            if step < 0:  # the counter started again
                step += counter.wrap if counter.wrap else 0
            if step < 0:
                self._last, self._watts = (now, known), None
                return None
            joules += step / 1e6
        self._last = (now, known)
        self._watts = joules / (now - then)
        return self._watts

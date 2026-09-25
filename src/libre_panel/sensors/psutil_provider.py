"""Cross-platform sensors via psutil (Windows, Linux, macOS).

Temperatures and fans are only available where the OS exposes them
(mostly Linux). On Windows use the LibreHardwareMonitor provider for those.
"""

from __future__ import annotations

import os
import time
from typing import Any

import psutil

from libre_panel.sensors.base import Reading, SensorProvider

GIB = 1024**3

# Preferred temperature chips, in order, for cpu.temp / gpu.temp on Linux.
_CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
_CPU_LABELS = ("Package id 0", "Tctl", "Tdie", "")
_GPU_CHIPS = ("amdgpu", "nouveau", "radeon")


def _system_disk() -> str:
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


class PsutilProvider(SensorProvider):
    name = "psutil"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.disk_path = self.options.get("disk", _system_disk())
        self._last_time: float | None = None
        self._last_net = None
        self._last_disk = None
        psutil.cpu_percent(interval=None)  # prime the counter

    def _rates(self, out: dict[str, Reading]) -> None:
        now = time.monotonic()
        net = psutil.net_io_counters()
        try:
            disk = psutil.disk_io_counters()
        except (RuntimeError, OSError):
            disk = None
        if self._last_time is not None:
            dt = max(now - self._last_time, 1e-3)
            if net and self._last_net:
                down = (net.bytes_recv - self._last_net.bytes_recv) / dt
                up = (net.bytes_sent - self._last_net.bytes_sent) / dt
                out["net.down"] = Reading("net.down", max(down, 0.0), "B/s", "Network download")
                out["net.up"] = Reading("net.up", max(up, 0.0), "B/s", "Network upload")
            if disk and self._last_disk:
                read = (disk.read_bytes - self._last_disk.read_bytes) / dt
                write = (disk.write_bytes - self._last_disk.write_bytes) / dt
                out["disk.read"] = Reading("disk.read", max(read, 0.0), "B/s", "Disk read")
                out["disk.write"] = Reading("disk.write", max(write, 0.0), "B/s", "Disk write")
        self._last_time, self._last_net, self._last_disk = now, net, disk

    def _temperatures(self, out: dict[str, Reading]) -> None:
        reader = getattr(psutil, "sensors_temperatures", None)
        if reader is None:
            return
        try:
            temps = reader() or {}
        except (OSError, RuntimeError):
            return
        for chip in _CPU_CHIPS:
            entries = temps.get(chip)
            if not entries:
                continue
            by_label = {e.label: e.current for e in entries}
            for label in _CPU_LABELS:
                if label in by_label:
                    out["cpu.temp"] = Reading("cpu.temp", by_label[label], "°C", "CPU temperature")
                    break
            else:
                out["cpu.temp"] = Reading("cpu.temp", entries[0].current, "°C", "CPU temperature")
            break
        for chip in _GPU_CHIPS:
            if temps.get(chip):
                out["gpu.temp"] = Reading(
                    "gpu.temp", temps[chip][0].current, "°C", "GPU temperature"
                )
                break
        for chip, entries in temps.items():
            for i, entry in enumerate(entries):
                label = entry.label or str(i)
                key = f"temp.{chip}.{label}".replace(" ", "_").lower()
                out[key] = Reading(key, entry.current, "°C", f"{chip} {label}")

    def _fans(self, out: dict[str, Reading]) -> None:
        reader = getattr(psutil, "sensors_fans", None)
        if reader is None:
            return
        try:
            fans = reader() or {}
        except (OSError, RuntimeError):
            return
        for chip, entries in fans.items():
            for i, entry in enumerate(entries):
                label = entry.label or str(i)
                key = f"fan.{chip}.{label}".replace(" ", "_").lower()
                out[key] = Reading(key, float(entry.current), "RPM", f"Fan {chip} {label}")

    def read(self) -> dict[str, Reading]:
        out: dict[str, Reading] = {}
        out["cpu.load"] = Reading("cpu.load", psutil.cpu_percent(interval=None), "%", "CPU load")
        # cpu_freq does not exist on every platform (e.g. macOS on Apple Silicon).
        freq_reader = getattr(psutil, "cpu_freq", None)
        try:
            freq = freq_reader() if freq_reader else None
        except (OSError, RuntimeError, NotImplementedError):
            freq = None
        if freq:
            out["cpu.freq"] = Reading("cpu.freq", freq.current, "MHz", "CPU clock")
        out["cpu.cores"] = Reading("cpu.cores", float(psutil.cpu_count() or 0), "", "CPU threads")

        mem = psutil.virtual_memory()
        out["mem.load"] = Reading("mem.load", mem.percent, "%", "Memory usage")
        out["mem.used"] = Reading(
            "mem.used", (mem.total - mem.available) / GIB, "GiB", "Memory used"
        )
        out["mem.total"] = Reading("mem.total", mem.total / GIB, "GiB", "Memory total")
        swap = psutil.swap_memory()
        out["swap.load"] = Reading("swap.load", swap.percent, "%", "Swap usage")

        try:
            disk = psutil.disk_usage(self.disk_path)
            out["disk.load"] = Reading("disk.load", disk.percent, "%", "Disk usage")
            out["disk.used"] = Reading("disk.used", disk.used / GIB, "GiB", "Disk used")
            out["disk.total"] = Reading("disk.total", disk.total / GIB, "GiB", "Disk total")
        except OSError:
            pass

        self._rates(out)
        self._temperatures(out)
        self._fans(out)

        battery = getattr(psutil, "sensors_battery", lambda: None)()
        if battery is not None:
            out["battery.load"] = Reading("battery.load", battery.percent, "%", "Battery")
        uptime = time.time() - psutil.boot_time()
        out["sys.uptime"] = Reading("sys.uptime", uptime, "s", "Uptime")
        return out

"""Cross-platform sensors via psutil (Windows, Linux, macOS).

Temperatures and fans are only available where the OS exposes them
(mostly Linux). On Windows use the LibreHardwareMonitor provider for those.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import date
from typing import Any

import psutil

from libre_panel.sensors.base import Reading, SensorProvider, wanted

GIB = 1024**3

# Preferred temperature chips, in order, for cpu.temp / gpu.temp on Linux.
_CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
_CPU_LABELS = ("Package id 0", "Tctl", "Tdie", "")
_GPU_CHIPS = ("amdgpu", "nouveau", "radeon")
# Names people know for the chips Linux reports.
_CHIP_NAMES = {
    "coretemp": "CPU", "k10temp": "CPU", "zenpower": "CPU", "cpu_thermal": "CPU",
    "amdgpu": "GPU", "nouveau": "GPU", "radeon": "GPU", "nvme": "NVMe", "acpitz": "ACPI",
    "iwlwifi_1": "Wi-Fi", "pch_cannonlake": "Chipset", "thinkpad": "ThinkPad",
}  # fmt: skip


# File systems that are not drives a user thinks of (Linux, macOS).
_PSEUDO_FS = {
    "squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "autofs", "ramfs",
    "nsfs", "efivarfs", "fuse.portal", "devfs",
}  # fmt: skip
_TOP = 8  # top processes kept, per sort
PING_EVERY = 5.0  # seconds
# battery.state: shown in the user's language (the renderer translates it)
BATTERY_STATES = ("On battery", "Charging", "Charged")
PROCESSES_EVERY = 2.0


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
        psutil.cpu_percent(interval=None, percpu=True)
        self._partitions: list[tuple[str, str]] = []  # (mount point, name)
        self._partitions_at = 0.0
        self._ip, self._ip_at = "", -1e9
        self._day: date | None = None
        self._day_start = None  # counters at midnight (or start)
        self.ping_target = str(self.options.get("ping", "1.1.1.1:443"))
        self._background: dict[str, dict[str, Reading]] = {"proc": {}, "ping": {}}
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()

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
        keys: dict[tuple[str, int], str] = {}
        for chip, entries in temps.items():
            for i, entry in enumerate(entries):
                label = entry.label or str(i)
                key = _slug(f"temp.{chip}.{label}")
                keys[chip, i] = key
                name = f"{_CHIP_NAMES.get(chip, chip)} {entry.label or i + 1}"
                out[key] = Reading(key, entry.current, "°C", name)
        for chip in _CPU_CHIPS:
            entries = temps.get(chip)
            if not entries:
                continue
            by_label = {e.label: i for i, e in enumerate(entries)}
            index = next((by_label[label] for label in _CPU_LABELS if label in by_label), 0)
            out["cpu.temp"] = Reading(
                "cpu.temp", entries[index].current, "°C", "CPU temperature", keys[chip, index]
            )
            break
        for chip in _GPU_CHIPS:
            if temps.get(chip):
                out["gpu.temp"] = Reading(
                    "gpu.temp", temps[chip][0].current, "°C", "GPU temperature", keys[chip, 0]
                )
                break

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
                key = _slug(f"fan.{chip}.{label}")
                name = f"{_CHIP_NAMES.get(chip, chip)} {entry.label or i + 1}"
                out[key] = Reading(key, float(entry.current), "RPM", name)

    # -- drives, cores, network details ----------------------------------------

    def _drives(self, out: dict[str, Reading]) -> None:
        """disk.<n>.name|load|used|free|total for every drive, the system's first."""
        now = time.monotonic()
        if now - self._partitions_at > 30 or not self._partitions:
            self._partitions = self._find_partitions()
            self._partitions_at = now
        for n, (mount, name) in enumerate(self._partitions, start=1):
            try:
                usage = psutil.disk_usage(mount)
            except OSError:
                continue
            out[f"disk.{n}.name"] = Reading(f"disk.{n}.name", name, "", name)
            out[f"disk.{n}.load"] = Reading(f"disk.{n}.load", usage.percent, "%", name)
            out[f"disk.{n}.used"] = Reading(f"disk.{n}.used", usage.used / GIB, "GiB", name)
            out[f"disk.{n}.free"] = Reading(f"disk.{n}.free", usage.free / GIB, "GiB", name)
            out[f"disk.{n}.total"] = Reading(f"disk.{n}.total", usage.total / GIB, "GiB", name)

    def _find_partitions(self) -> list[tuple[str, str]]:
        try:
            parts = psutil.disk_partitions(all=False)
        except (OSError, RuntimeError):
            return []
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        system = os.path.normcase(self.disk_path)
        for part in parts:
            options = part.opts.split(",")
            if part.fstype in _PSEUDO_FS or "cdrom" in options or not part.fstype:
                continue
            if os.name != "nt" and "ro" in options and part.mountpoint != "/":
                continue  # read-only images, recovery
            if part.mountpoint.startswith(("/snap", "/boot", "/System/Volumes")):
                continue
            if part.device in seen:
                continue
            seen.add(part.device)
            name = part.mountpoint.rstrip("\\") if os.name == "nt" else part.mountpoint
            found.append((part.mountpoint, name or part.mountpoint))
        found.sort(key=lambda p: os.path.normcase(p[0]) != system)  # the system drive first
        return found

    def _cores(self, out: dict[str, Reading]) -> None:
        try:
            loads = psutil.cpu_percent(interval=None, percpu=True)
        except (OSError, RuntimeError):
            return
        for n, load in enumerate(loads, start=1):
            out[f"cpu.core.{n}.load"] = Reading(f"cpu.core.{n}.load", load, "%", f"Core {n}")

    def _network(self, out: dict[str, Reading]) -> None:
        now = time.monotonic()
        if now - self._ip_at > 30:  # addresses change rarely
            self._ip, self._ip_at = _local_ip(), now
        ip = self._ip
        if ip:
            out["net.ip"] = Reading("net.ip", ip, "", "IP address")
        net = self._last_net
        if net is None:
            return
        today = date.today()
        if self._day != today:  # the first reading, or midnight
            self._day, self._day_start = today, net
        start = self._day_start
        out["net.today.down"] = Reading(
            "net.today.down",
            float(max(0, net.bytes_recv - start.bytes_recv)),
            "B",
            "Received today",
        )
        out["net.today.up"] = Reading(
            "net.today.up", float(max(0, net.bytes_sent - start.bytes_sent)), "B", "Sent today"
        )

    # -- costly ones in the background, only while a theme shows them ----------

    def _keep(self, name: str, work: Any, every: float) -> None:
        if name in self._threads and self._threads[name].is_alive():
            return

        def loop() -> None:
            idle = 0.0
            while not self._stop.is_set():
                if wanted(_PREFIXES[name]):
                    idle = 0.0
                    try:
                        self._background[name] = work()
                    except Exception:  # noqa: BLE001 - a sensor must not stop the panel
                        self._background[name] = {}
                else:
                    idle += every
                    if idle > 60:  # nobody looked for a minute: end, start again on demand
                        self._background[name] = {}
                        return
                self._stop.wait(every)

        thread = threading.Thread(target=loop, name=f"sensors-{name}", daemon=True)
        self._threads[name] = thread
        thread.start()

    def _processes(self) -> dict[str, Reading]:
        cores = psutil.cpu_count() or 1
        rows = []
        for proc in psutil.process_iter(["name", "memory_percent"]):
            try:
                cpu = proc.cpu_percent(interval=None) / cores
            except (psutil.Error, OSError):
                continue
            name = proc.info.get("name") or ""
            if not name or proc.pid == 0 or name.lower() in ("system idle process", "idle"):
                continue
            rows.append((name, cpu, proc.info.get("memory_percent") or 0.0))
        merged: dict[str, list[float]] = {}  # one row per program (browsers have many)
        for name, cpu, mem in rows:
            total = merged.setdefault(name, [0.0, 0.0])
            total[0] += cpu
            total[1] += mem
        out: dict[str, Reading] = {}
        for sort, index, unit in (("cpu", 0, "%"), ("mem", 1, "%")):
            top = sorted(merged.items(), key=lambda kv: kv[1][index], reverse=True)[:_TOP]
            for n, (name, values) in enumerate(top, start=1):
                key = f"proc.{sort}.{n}"
                label = _program_name(name)
                out[f"{key}.name"] = Reading(f"{key}.name", label, "", label)
                out[f"{key}.value"] = Reading(f"{key}.value", round(values[index], 1), unit, label)
        return out

    def _ping(self) -> dict[str, Reading]:
        host, _, port = self.ping_target.rpartition(":")
        if not host:
            host, port = self.ping_target, "443"
        start = time.perf_counter()
        try:
            with socket.create_connection((host, int(port)), timeout=2):
                pass
        except (OSError, ValueError):
            return {"net.ping": Reading("net.ping", None, "ms", "Ping")}
        ms = (time.perf_counter() - start) * 1000
        return {"net.ping": Reading("net.ping", round(ms, 1), "ms", f"Ping {host}")}

    def close(self) -> None:
        self._stop.set()

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
        self._cores(out)
        self._drives(out)
        self._network(out)
        if wanted("proc."):
            self._keep("proc", self._processes, PROCESSES_EVERY)
            out.update(self._background["proc"])
        if wanted("net.ping"):
            self._keep("ping", self._ping, PING_EVERY)
            out.update(self._background["ping"])

        try:
            battery = getattr(psutil, "sensors_battery", lambda: None)()
        except (OSError, RuntimeError):
            battery = None
        if battery is not None:
            out["battery.load"] = Reading("battery.load", battery.percent, "%", "Battery")
            plugged = battery.power_plugged
            if plugged is not None:
                out["battery.plugged"] = Reading(
                    "battery.plugged", 1 if plugged else 0, "", "Plugged in"
                )
                state = "On battery"
                if plugged:
                    state = "Charged" if battery.percent >= 99.5 else "Charging"
                out["battery.state"] = Reading("battery.state", state, "", "Battery")
            left = battery.secsleft
            if isinstance(left, (int, float)) and left >= 0 and not plugged:
                out["battery.left"] = Reading("battery.left", float(left), "s", "Battery time left")
        uptime = time.time() - psutil.boot_time()
        out["sys.uptime"] = Reading("sys.uptime", uptime, "s", "Uptime")
        return out


_PREFIXES = {"proc": "proc.", "ping": "net.ping"}


def _local_ip() -> str:
    """The address this computer uses towards the internet (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET: routing only
            return probe.getsockname()[0]
    except OSError:
        return ""


def _program_name(name: str) -> str:
    """'chrome.exe' -> 'chrome'."""
    return name[:-4] if name.lower().endswith(".exe") else name


def _slug(key: str) -> str:
    return key.replace(" ", "_").lower()

"""Cross-platform sensors via psutil (Windows, Linux).

Temperatures and fans are only available where the OS exposes them
(mostly Linux). On Windows use the LibreHardwareMonitor provider for those.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections import deque
from datetime import date
from typing import Any

import psutil

from libre_panel.sensors.base import Reading, SensorProvider, wanted
from libre_panel.sensors.hwmon import CHIP_NAMES as _CHIP_NAMES
from libre_panel.sensors.hwmon import slug as _slug
from libre_panel.sensors.hwmon import unique as _unique

GIB = 1024**3

# Preferred temperature chips, in order, for cpu.temp / gpu.temp on Linux.
_CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
_CPU_LABELS = ("Package id 0", "Tctl", "Tdie", "")
_GPU_CHIPS = ("amdgpu", "nouveau", "radeon")

# File systems that are not drives a user thinks of (Linux).
_PSEUDO_FS = {
    "squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "autofs", "ramfs",
    "nsfs", "efivarfs", "fuse.portal",
}  # fmt: skip
_TOP = 8  # top processes kept, per sort
PING_EVERY = 5.0  # seconds
PING_TRIES = 20  # net.ping.lost: the share of so many last tries that got no answer
PING_HOSTS = 4  # the most the ping connects to
# battery.state: shown in the user's language (the renderer translates it)
BATTERY_STATES = ("On battery", "Charging", "Charged")
PROCESSES_EVERY = 2.0
# The scan of all processes takes the interpreter in bursts of at most so long, then leaves it
# to the panels' loops a moment: on Windows hundreds of processes take a good part of a second,
# which held every panel's frame (all panels stood still for a moment every few seconds).
SCAN_BURST_S = 0.002
SCAN_PAUSE_S = 0.002


def _system_disk() -> str:
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    # Fedora Atomic (Bazzite, Silverblue, Kinoite; composefs) mounts a read-only
    # image of a few MB at "/", always full; the disk itself is at /sysroot.
    if os.path.ismount("/sysroot") and _tiny("/"):
        return "/sysroot"
    return "/"


def _tiny(path: str) -> bool:
    try:
        return psutil.disk_usage(path).total < GIB
    except OSError:
        return False


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
        # what the ping connects to: a host:port, or a list of up to four (net.ping.2 ...)
        given = self.options.get("ping", "1.1.1.1:443")
        targets = [given] if isinstance(given, str) else list(given or [])
        self.ping_targets = [str(t).strip() for t in targets if str(t).strip()][:PING_HOSTS]
        self.ping_targets = self.ping_targets or ["1.1.1.1:443"]
        self.ping_target = self.ping_targets[0]
        self._background: dict[str, dict[str, Reading]] = {"proc": {}, "ping": {}}
        self._cpu_seen: dict[int, tuple[float, float]] = {}  # pid: its CPU seconds, and when
        self._ping_tries: list[deque[bool]] = [deque(maxlen=PING_TRIES) for _ in self.ping_targets]
        # False where the Linux source reads the kernel's chips (build_hub): the same files
        self.hwmon = True
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
        used: dict[str, int] = {}
        for chip, entries in temps.items():
            for i, entry in enumerate(entries):
                label = entry.label or str(i)
                name = f"{_CHIP_NAMES.get(chip, chip)} {entry.label or i + 1}"
                key, name = _unique(_slug(f"temp.{chip}.{label}"), name, used)
                keys[chip, i] = key
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
        used: dict[str, int] = {}
        for chip, entries in fans.items():
            for i, entry in enumerate(entries):
                label = entry.label or str(i)
                name = f"{_CHIP_NAMES.get(chip, chip)} {entry.label or i + 1}"
                key, name = _unique(_slug(f"fan.{chip}.{label}"), name, used)
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
            if os.name != "nt" and "ro" in options and part.mountpoint not in ("/", self.disk_path):
                continue  # read-only images, recovery
            if part.mountpoint.startswith(("/snap", "/boot", "/System/Volumes")):
                continue
            if part.device in seen:
                continue
            seen.add(part.device)
            name = part.mountpoint.rstrip("\\") if os.name == "nt" else part.mountpoint
            if name == "/sysroot":
                name = "/"  # the system's disk (see _system_disk)
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
        """The programs that use the most processor time since the last count, and the most
        memory. The processor's share comes from each process's CPU seconds between two counts,
        kept here by its id: not from psutil's own count, which lives on its Process objects
        and starts again at 0 for one that another part of Libre Panel's list made anew."""
        cores = psutil.cpu_count() or 1
        rows = []
        seen: dict[int, tuple[float, float]] = {}
        burst = time.perf_counter()
        for proc in psutil.process_iter(["name", "memory_percent", "cpu_times"]):
            if time.perf_counter() - burst > SCAN_BURST_S:
                time.sleep(SCAN_PAUSE_S)
                burst = time.perf_counter()
            times = proc.info.get("cpu_times")
            if times is None:  # (not ours to read)
                continue
            now, spent = time.monotonic(), times.user + times.system
            seen[proc.pid] = (spent, now)
            before = self._cpu_seen.get(proc.pid)
            cpu = 0.0
            if before is not None and now > before[1]:
                cpu = max(0.0, spent - before[0]) / (now - before[1]) / cores * 100
            name = proc.info.get("name") or ""
            if not name or proc.pid == 0 or name.lower() in ("system idle process", "idle"):
                continue
            rows.append((name, min(100.0, cpu), proc.info.get("memory_percent") or 0.0))
        self._cpu_seen = seen
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
        """Each target's time to connect: ``net.ping`` for the first, ``net.ping.2`` and on
        for the others, each with the share of its last tries that got no answer and its host."""
        out: dict[str, Reading] = {}
        for n, target in enumerate(self.ping_targets):
            host, _, port = target.rpartition(":")
            if not host or "]" in port:  # no port given ("example.org", "[::1]")
                host, port = target, "443"
            host = host.strip("[]")
            start = time.perf_counter()
            try:
                with socket.create_connection((host, int(port)), timeout=2):
                    pass
                ms: float | None = (time.perf_counter() - start) * 1000
            except (OSError, ValueError):
                ms = None
            tries = self._ping_tries[n]
            tries.append(ms is not None)
            lost = round(100 * tries.count(False) / len(tries))
            key = "net.ping" if n == 0 else f"net.ping.{n + 1}"
            out[key] = Reading(key, None if ms is None else round(ms, 1), "ms",
                               f"Ping {host}" if ms is not None else "Ping")  # fmt: skip
            out[f"{key}.lost"] = Reading(f"{key}.lost", lost, "%", "Ping lost")
            out[f"{key}.host"] = Reading(f"{key}.host", host, "", "Ping host")
        return out

    def close(self) -> None:
        self._stop.set()

    def read(self) -> dict[str, Reading]:
        out: dict[str, Reading] = {}
        out["cpu.load"] = Reading("cpu.load", psutil.cpu_percent(interval=None), "%", "CPU load")
        # cpu_freq is not available on every system (some ARM boards, virtual machines).
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
        # what programs can still take without the system swapping: used and free add up
        out["mem.free"] = Reading("mem.free", mem.available / GIB, "GiB", "Memory free")
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
        if self.hwmon:
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

"""What Libre Panel costs this PC, and where: *Live → Diagnose → Load* (``GET /api/load``), and
the same lines in the panel report.

Libre Panel is several processes: this one (the first panel's loop, the sensors, the app's
server, the tray, the shortcuts, the memory store, the weather), one for each further panel
(panel_process.py), an ffmpeg for each panel in the video mode (devices/h264.py), PresentMon
while a game's frame rate is read (sensors/presentmon.py), the window while it is open
(window.py, with WebView2's processes on Windows) and, on Windows, the sensor service
(sensorservice). :class:`LoadSampler` measures them with psutil: each one's processor time over
the last ``WINDOW_S`` and its memory (the working set, psutil's RSS), and the threads of this
process by their names (``threading``'s, matched to the thread ids the system gives psutil).
The other processes' threads are not looked at: from outside their names are not known, and
asking costs.

- **Per PC and per core.** psutil counts one whole core as 100 %, the Task Manager the whole
  PC: both are given, per PC being per core divided by the logical processors.
- **The sensor service** is the process the Service Control Manager names for
  ``LibrePanelSensors``, the one the service's client checks the pipe's server against
  (sensors/lhm_service.py): its hello says no process id. Where the Service Control Manager
  cannot be asked, it is the process of the service's program (``libre-panel-sensors.exe``).
  The report says which.
- **What LibreHardwareMonitor's rounds take**, piece by piece: the status of the source that
  reads it (the service's answers, or the source inside Libre Panel).
- **Only while asked.** The thread samples only while somebody asked within ``IDLE_S`` (the
  card asks every two seconds while it is on screen), then ends. A request never waits for a
  sample: it gets the last one. A sample costs a few milliseconds of psutil; the report says
  how many.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import psutil

log = logging.getLogger(__name__)

WINDOW_S = 2.0  # the processor time is measured over this long: the card asks this often
IDLE_S = 15.0  # nobody asked for this long: the thread ends (and the report is old)
LOOK_S = 30.0  # the sensor service, not found, is looked for again after this long
WINDOW_LOOK_S = 10.0  # the window's process (window.json) is looked for this often
# the tree of processes is walked this often (it reads every process's parent: the costly part
# of a sample, on Linux some milliseconds); in between the processes found are measured
TREE_S = 6.0
TEXT_THREADS = 10  # threads named in the text; the rest go with "other"
# Python's own names for a thread nobody named: "Thread-12 (process_request_thread)", "Dummy-3"
_UNNAMED = re.compile(r"(?:Thread|Dummy)-\d+(?: \(.*\))?")
OTHER = "other"


@dataclass(frozen=True)
class ServiceFound:
    """Where the sensor service is: its process (None: none), how that was found ("service
    manager" or "program name") and its state ("running", "not running", "not installed")."""

    pid: int | None
    how: str = ""
    state: str = "running"


def find_service(
    platform: str | None = None,
    status: Callable[[str], tuple[int, int] | None] | None = None,
    processes: Callable[..., Iterable[Any]] | None = None,
) -> ServiceFound | None:
    """The sensor service's process: the one the Service Control Manager names, else the one of
    its program. None off Windows, where there is none."""
    if (platform or sys.platform) != "win32":
        return None
    from libre_panel.sensorservice import SERVICE_EXE, SERVICE_NAME

    try:
        if status is None:
            from libre_panel.sensorservice import winapi

            status = winapi.service_status
        found = status(SERVICE_NAME)
    except (OSError, AttributeError) as exc:  # it cannot be asked: the program's name it is
        log.debug("the Service Control Manager does not say where the sensor service is: %s", exc)
    else:
        if found is None:
            return ServiceFound(None, state="not installed")
        _, pid = found
        return (
            ServiceFound(pid, "service manager") if pid else ServiceFound(None, state="not running")
        )
    for proc in (processes or psutil.process_iter)(["name"]):
        if str(proc.info.get("name") or "").lower() == SERVICE_EXE:
            return ServiceFound(proc.pid, "program name")
    return ServiceFound(None, state="not running")


def _panel_pids() -> dict[int, int]:
    from libre_panel.panel_process import panel_pids

    return panel_pids()


def _window_pid() -> int | None:
    from libre_panel.window import running

    pid = (running() or {}).get("pid")
    return pid if isinstance(pid, int) else None


def thread_names() -> dict[int, str]:
    """The names of this process's threads, by the id the system gives each."""
    return {t.native_id: t.name for t in threading.enumerate() if t.native_id is not None}


def role_of(
    name: str,
    cmdline: list[str],
    pid: int,
    parent: int | None,
    panels: dict[int, int],
    window: int | None,
) -> tuple[str, int | None]:
    """What a process of Libre Panel's is (and the panel it is for, where it is a panel's), by
    its id, its program's name and its command line: "panel", "ffmpeg", "presentmon",
    "window", "webview" (the window's browser engine) or "other"."""
    if pid in panels:
        return "panel", panels[pid]
    if pid == window:
        return "window", None
    low = name.lower()
    if low.startswith("ffmpeg"):  # the video of the panel whose process started it
        return "ffmpeg", panels.get(parent) if parent is not None else None
    if low.startswith("presentmon"):
        return "presentmon", None
    if low.startswith(("msedgewebview2", "webkit")):  # WebView2 (Windows), WebKitGTK (Linux)
        return "webview", None
    if "--multiprocessing-fork" in cmdline:  # a panel's process this one did not start
        return "panel", None
    if "window" in cmdline[1:] and any("libre" in part.lower() for part in cmdline[:3]):
        return "window", None
    return OTHER, None


def label_of(role: str, panel: int | None = None, name: str = "", platform: str = "") -> str:
    """A row's name in the text (English, for an issue)."""
    if role == "main":
        return "main process"
    if role == "panel":
        return f"panel {panel}" if panel else "panel"
    if role == "ffmpeg":
        return f"ffmpeg (panel {panel})" if panel else "ffmpeg"
    if role == "presentmon":
        return "PresentMon"
    if role == "window":
        return "window"
    if role == "webview":
        return "WebView2" if (platform or sys.platform) == "win32" else "WebKit"
    if role == "service":
        return "sensor service"
    return f"other: {name}" if name else OTHER


_ORDER = {"main": 0, "panel": 1, "ffmpeg": 2, "presentmon": 3, "window": 4, "webview": 5,
          "service": 6, OTHER: 7}  # fmt: skip


@dataclass
class _Measured:
    key: tuple[int, float]  # (process id, when it started): a process, also after its id is reused
    role: str
    panel: int | None
    name: str
    cpu: float  # seconds of processor time so far (user and system)
    rss: int
    born: float  # when it started (seconds since 1970)


@dataclass
class _Sample:
    at: float  # on the clock
    wall: float  # seconds since 1970
    cpu: dict[tuple[int, float], float]
    threads: dict[int, float]  # thread id -> seconds of processor time so far


def _times(proc: Any) -> tuple[float, int]:
    """(processor seconds, memory) of a process, in one go where psutil can."""
    with getattr(proc, "oneshot", contextlib.nullcontext)():
        times = proc.cpu_times()
        return float(times.user) + float(times.system), int(proc.memory_info().rss)


def _round_report(done: Any) -> dict[str, Any] | None:
    if done is None:
        return None
    parts = sorted(done.parts, key=lambda part: part.seconds, reverse=True)
    return {
        "ms": round(done.seconds * 1000, 2),
        "full": done.full,
        "parts": [
            {"kind": p.kind, "name": p.name, "ms": round(p.seconds * 1000, 2)} for p in parts
        ],
    }


def sensors_report(status: Any) -> dict[str, Any] | None:
    """What LibreHardwareMonitor's rounds take, from the status of the source that reads it
    (None: it is not read). ``reported`` False: the source does not say (a sensor service older
    than the times, or the web server, whose rounds are its own program's)."""
    if status is None:
        return None
    timing = status.timing
    return {
        "mode": status.mode,
        "version": status.version,
        "error": status.error,
        "reported": timing is not None,
        "last": _round_report(timing.last) if timing is not None else None,
        "full": _round_report(timing.full) if timing is not None else None,
    }


class LoadSampler:
    """Samples Libre Panel's processes and this process's threads in a thread of its own, while
    somebody asks (:meth:`report`). Every way to the system is a parameter, for the tests:
    ``me`` this process, ``process(pid)`` another, ``panels()`` the panels' processes,
    ``window()`` the window's, ``service()`` where the sensor service is, ``lhm()`` the status
    of LibreHardwareMonitor's source and ``names()`` the names of this process's threads."""

    def __init__(
        self,
        *,
        me: Any = None,
        process: Callable[[int], Any] | None = None,
        cpu_count: int | None = None,
        panels: Callable[[], dict[int, int]] = _panel_pids,
        window: Callable[[], int | None] = _window_pid,
        service: Callable[[], ServiceFound | None] = find_service,
        lhm: Callable[[], Any] = lambda: None,
        names: Callable[[], dict[int, str]] = thread_names,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        timer: Callable[[], float] = time.perf_counter,
        window_s: float = WINDOW_S,
        idle_s: float = IDLE_S,
        platform: str | None = None,
    ) -> None:
        self._me = me if me is not None else psutil.Process()
        self._process = process or psutil.Process
        self.cpus = max(1, cpu_count or psutil.cpu_count() or 1)
        self._panels, self._window, self._service = panels, window, service
        self._lhm, self._names = lhm, names
        self._clock, self._wall, self._timer = clock, wall, timer
        self.window_s, self.idle_s = window_s, idle_s
        self.platform = platform or sys.platform
        self._roles: dict[tuple[int, float], tuple[str, int | None, str]] = {}
        self._before: _Sample | None = None
        self._service_proc: Any = None
        self._service_found: ServiceFound | None = None
        self._service_looked: float | None = None
        self._window_pid: int | None = None
        self._window_looked: float | None = None
        self._tree: list[Any] = []  # the processes under this one (and the window's)
        self._tree_at: float | None = None
        self._costs: deque[float] = deque(maxlen=6)  # what the last samples took
        self._latest: dict[str, Any] | None = None
        self._latest_at: float | None = None
        self._asked: float | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._lock = threading.Lock()
        self._fresh = threading.Condition(self._lock)
        self._stop = threading.Event()

    # -- what the app asks ----------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """The last sample's report (``ready`` False before there is one), at once; the thread
        samples on for ``idle_s`` (and starts, if it had ended)."""
        now = self._clock()
        with self._lock:
            self._asked = now
            if self._thread is None and not self._closed:
                self._thread = threading.Thread(target=self._run, name="load-report", daemon=True)
                self._thread.start()
            latest, at = self._latest, self._latest_at
        if latest is None or at is None or now - at > self.idle_s:
            return {"ready": False, "cpus": self.cpus, "window_s": self.window_s}
        return {**latest, "age_s": round(max(0.0, now - at), 2)}

    def text(self, wait: float = 0.0) -> str:
        """The report as text (for the panel report): the last one, or the first that comes
        within ``wait`` seconds; "" when none does."""
        report = self.report()
        if not report["ready"] and wait > 0:
            asked = self._clock()
            with self._fresh:
                self._fresh.wait_for(
                    lambda: self._latest_at is not None and self._latest_at >= asked, wait
                )
            report = self.report()
        return report.get("text", "") if report["ready"] else ""

    def close(self) -> None:
        with self._lock:
            self._closed = True
            thread = self._thread
        self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self.sample()
                except Exception:  # noqa: BLE001 - a report is a help: the thread goes on
                    log.debug("the load could not be measured", exc_info=True)
                if self._stop.wait(self.window_s):
                    return
                with self._lock:
                    if self._asked is None or self._clock() - self._asked > self.idle_s:
                        self._thread = None  # nobody looks: it ends, the next ask starts it
                        return
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    # -- a sample -------------------------------------------------------------------------------

    def sample(self) -> dict[str, Any] | None:
        """Measure once; the report of the time since the sample before (None for the first,
        or the first after a pause: what it measured is only where the next starts)."""
        started = self._timer()
        now, wall = self._clock(), self._wall()
        measured = self._measure_all(now)
        threads = self._thread_times()
        self._costs.append(self._timer() - started)
        sample = _Sample(now, wall, {m.key: m.cpu for m in measured}, threads)
        before, self._before = self._before, sample
        if before is None or not 0 < now - before.at <= self.idle_s:
            return None
        cost = sum(self._costs) / len(self._costs)
        report = self._report(before, sample, measured, cost)
        with self._fresh:
            self._latest, self._latest_at = report, now
            self._fresh.notify_all()
        return report

    def _measure_all(self, now: float) -> list[_Measured]:
        panels = self._panels()
        window = self._window_now(now)
        me = self._me
        out: list[_Measured] = []
        try:
            out.append(self._measured(me, "main", None, ""))
        except psutil.Error:  # (this process: it is there, but the system may refuse)
            log.debug("this process could not be measured", exc_info=True)
        gone = set()
        for child in self._children(now, window):
            try:
                role, panel, name = self._role(child, panels, window)
                out.append(self._measured(child, role, panel, name))
            except psutil.Error:  # it ended meanwhile, or may not be looked at
                gone.add(child.pid)
        if gone:
            self._tree = [child for child in self._tree if child.pid not in gone]
        service = self._service_now(now)
        if service is not None:
            try:
                out.append(self._measured(service, "service", None, ""))
            except psutil.NoSuchProcess:  # it ended (Windows starts it again): looked for anew
                self._service_proc, self._service_looked = None, None
            except psutil.Error:  # it may not be looked at: looked for again in a while
                self._service_proc = None
                log.debug("the sensor service's process could not be measured", exc_info=True)
        return out

    def _children(self, now: float, window: int | None) -> list[Any]:
        """The processes under this one, and the window's with its own where another start
        opened it (then it is not under this one): walked anew every ``TREE_S``."""
        if self._tree_at is not None and 0 <= now - self._tree_at < TREE_S:
            return self._tree
        self._tree_at = now
        me = self._me
        try:
            children = list(me.children(recursive=True))
        except psutil.Error:
            children = []
        if window is not None and window != me.pid and window not in {c.pid for c in children}:
            try:
                orphan = self._process(window)
                children += [orphan, *orphan.children(recursive=True)]
            except psutil.Error:
                pass
        self._tree = children
        return children

    def _measured(self, proc: Any, role: str, panel: int | None, name: str) -> _Measured:
        cpu, rss = _times(proc)
        born = float(proc.create_time())
        return _Measured((proc.pid, born), role, panel, name, cpu, rss, born)

    def _role(self, proc: Any, panels: dict[int, int], window: int | None) -> tuple[str, Any, str]:
        """A child's role, worked out once (its name and command line are asked for then)."""
        key = (proc.pid, float(proc.create_time()))
        known = self._roles.get(key)
        if known is not None and not (known[0] == "panel" and known[1] is None):
            return known
        name = proc.name()
        try:
            cmdline = list(proc.cmdline())
        except psutil.Error:
            cmdline = []
        try:
            parent = proc.ppid()
        except psutil.Error:
            parent = None
        role, panel = role_of(name, cmdline, proc.pid, parent, panels, window)
        known = (role, panel, name if role == OTHER else "")
        if len(self._roles) > 512:  # ffmpeg's that came and went
            self._roles.clear()
        self._roles[key] = known
        return known

    def _window_now(self, now: float) -> int | None:
        if self._window_looked is None or now - self._window_looked >= WINDOW_LOOK_S:
            self._window_looked = now
            try:
                self._window_pid = self._window()
            except Exception:  # noqa: BLE001 - no window that can be found is no window
                self._window_pid = None
        return self._window_pid

    def _service_now(self, now: float) -> Any:
        """The sensor service's process: kept once found, looked for again while it is not
        (at most every ``LOOK_S``)."""
        if self._service_proc is not None:
            return self._service_proc
        if self._service_looked is not None and now - self._service_looked < LOOK_S:
            return None
        self._service_looked = now
        try:
            found = self._service()
        except Exception:  # noqa: BLE001 - the report goes on without it
            log.debug("the sensor service could not be looked for", exc_info=True)
            found = None
        self._service_found = found
        if found is not None and found.pid:
            try:
                self._service_proc = self._process(found.pid)
            except psutil.Error:
                self._service_proc = None
        return self._service_proc

    def _thread_times(self) -> dict[int, float]:
        try:
            return {int(t[0]): float(t[1]) + float(t[2]) for t in self._me.threads()}
        except psutil.Error:
            return {}

    # -- the report -----------------------------------------------------------------------------

    def _report(
        self, before: _Sample, now: _Sample, measured: list[_Measured], cost: float
    ) -> dict[str, Any]:
        elapsed = now.at - before.at

        def core(seconds: float) -> float:
            return max(0.0, seconds) / elapsed * 100.0

        rows: dict[tuple[str, int | None, str], dict[str, Any]] = {}
        main_cpu = None
        for m in measured:
            then = before.cpu.get(m.key)
            if then is None and m.born >= before.wall - 0.5:
                then = 0.0  # it started since: all its time is in the window
            share = None if then is None else core(m.cpu - then)
            if m.role == "main":
                main_cpu = share
            row = rows.setdefault(
                (m.role, m.panel, m.name),
                {"role": m.role, "panel": m.panel, "name": m.name, "count": 0,
                 "cpu_core": None, "rss": 0},
            )  # fmt: skip
            row["count"] += 1
            row["rss"] += m.rss
            if share is not None:
                row["cpu_core"] = (row["cpu_core"] or 0.0) + share
        processes = sorted(rows.values(), key=lambda r: (_ORDER.get(r["role"], 9), r["panel"] or 0))
        for row in processes:
            row["label"] = label_of(row["role"], row["panel"], row["name"], self.platform)
            if row["role"] == "service" and self._service_found is not None:
                row["found_by"] = self._service_found.how
            self._shares(row)
        total = {"cpu_core": sum(r["cpu_core"] or 0.0 for r in processes),
                 "rss": sum(r["rss"] for r in processes)}  # fmt: skip
        self._shares(total)
        service = self._service_found
        report: dict[str, Any] = {
            "ready": True,
            "cpus": self.cpus,
            "window_s": round(elapsed, 2),
            "sample_ms": round(cost * 1000, 2),
            "total": total,
            "processes": processes,
            "threads": self._threads_report(before, now, core, main_cpu),
            "service": None
            if service is None
            else {"state": service.state, "found_by": service.how},
            "sensors": self._sensors(),
        }
        report["text"] = report_text(report)
        return report

    def _shares(self, row: dict[str, Any]) -> None:
        cpu = row["cpu_core"]
        row["cpu_core"] = None if cpu is None else round(cpu, 2)
        row["cpu_pc"] = None if cpu is None else round(cpu / self.cpus, 2)

    def _threads_report(
        self, before: _Sample, now: _Sample, core: Callable[[float], float], main: float | None
    ) -> list[dict[str, Any]]:
        names = self._names()
        spent: dict[str, float] = {}
        count: dict[str, int] = {}
        for tid, seconds in now.threads.items():
            name = names.get(tid, OTHER)  # (a thread of .NET or of a library: no name)
            if _UNNAMED.fullmatch(name):
                name = OTHER
            spent[name] = spent.get(name, 0.0) + core(seconds - before.threads.get(tid, 0.0))
            count[name] = count.get(name, 0) + 1
        if main is not None:  # what the threads that ended meanwhile spent
            ended = max(0.0, main - sum(spent.values()))
            spent[OTHER] = spent.get(OTHER, 0.0) + ended
            count.setdefault(OTHER, 0)
        out = [
            {"name": name, "count": count[name], "cpu_core": round(share, 2),
             "cpu_pc": round(share / self.cpus, 2)}
            for name, share in spent.items()
        ]  # fmt: skip
        out.sort(key=lambda t: (t["name"] == OTHER, -t["cpu_core"], t["name"]))
        return out

    def _sensors(self) -> dict[str, Any] | None:
        try:
            return sensors_report(self._lhm())
        except Exception:  # noqa: BLE001 - the report goes on without it
            log.debug("no round times of LibreHardwareMonitor", exc_info=True)
            return None


# -- the text (for the panel report, and "Copy as text") ------------------------------------------


_FOUND = {
    "service manager": "the process the Service Control Manager names",
    "program name": "found by its program's name",
}


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f} %"


def _mb(value: int) -> str:
    return f"{value / 1024**2:.0f} MB"


def _parts(done: dict[str, Any], most: int = 5) -> str:
    parts = done["parts"]
    said = ", ".join(f"{p['kind']} {p['ms']:.1f}" for p in parts[:most])
    more = f", {len(parts) - most} more" if len(parts) > most else ""
    return f"{done['ms']:.1f} ms" + (f" ({said}{more})" if said else " (nothing read)")


def sensors_text(sensors: dict[str, Any] | None) -> list[str]:
    if sensors is None:
        return []
    mode = sensors["mode"]
    if mode == "webserver":
        return ["LibreHardwareMonitor: read from its web server (its own program, not counted)"]
    where = {"service": "the sensor service", "inprocess": "inside Libre Panel"}.get(mode, mode)
    if mode == "service" and sensors["version"]:
        where += f" {sensors['version']}"
    head = f"LibreHardwareMonitor rounds, {where}:"
    if sensors["error"]:
        return [f"{head} {sensors['error']}"]
    if not sensors["reported"]:
        return [f"{head} not reported by this service version"]
    lines = [head]
    if sensors["last"] is not None:
        lines.append(f"  last round: {_parts(sensors['last'])}")
    full = sensors["full"]
    if full is not None and not (sensors["last"] or {}).get("full"):
        lines.append(f"  all hardware: {_parts(full)}")
    return lines if len(lines) > 1 else [f"{head} none yet"]


def report_text(report: dict[str, Any]) -> str:
    """The report in a few lines of English, for an issue: no names, no folders, no ids."""
    lines = [
        f"Libre Panel load over {report['window_s']:.1f} s, {report['cpus']} logical processors "
        "(PC: of the whole PC, as the Task Manager counts; core: of one core)",
        f"{'':<22}{'PC':>8}{'core':>9}{'memory':>10}",
    ]

    def row(label: str, data: dict[str, Any]) -> str:
        return (
            f"{label:<22}{_pct(data['cpu_pc']):>8}{_pct(data['cpu_core']):>9}{_mb(data['rss']):>10}"
        )

    lines.append(row("Libre Panel, total", report["total"]))
    for process in report["processes"]:
        label = process["label"] + (f" ×{process['count']}" if process["count"] > 1 else "")
        lines.append(row(label[:21], process))
    service = report.get("service")
    if service is not None:
        if any(p["role"] == "service" for p in report["processes"]):
            how = _FOUND.get(service["found_by"], service["found_by"])
            lines.append(f"(the sensor service: {how})")
        else:
            lines.append(f"sensor service: {service['state']}")
    threads = report["threads"]
    if threads:
        named = [t for t in threads if t["name"] != OTHER][:TEXT_THREADS]
        rest = sum(t["cpu_core"] for t in threads) - sum(t["cpu_core"] for t in named)
        said = [f"{t['name']} {t['cpu_core']:.1f}" for t in named]
        said.append(f"{OTHER} {max(0.0, rest):.1f}")
        lines.append("threads of the main process, % of one core: " + ", ".join(said))
    lines += sensors_text(report.get("sensors"))
    lines.append(f"this measurement took {report['sample_ms']:.1f} ms")
    return "\n".join(lines) + "\n"

"""Frames per second of the game you play, through Intel's PresentMon (Windows).

PresentMon (MIT license) is a separate program: download the console
version from https://github.com/GameTechDev/PresentMon/releases and name it
in ``[sensors.presentmon]`` (or put it on the PATH)::

    path = "C:/Tools/PresentMon-2.3.0-x64.exe"

It reads the frames Windows shows, which needs administrator rights or
membership in the "Performance Log Users" group. It never touches the game:
no hook, no memory read, no overlay.

The recording session must exist *before* the game starts: the anti-cheat of
some games refuses to let a new session begin while the game runs, but leaves
one that is already running alone. So PresentMon runs for as long as the
``game`` service is on or a theme shows ``game.*`` readings (it stops a minute
after neither is), and is started again only slowly while a game runs. It sits
in a Windows job that ends it with Libre Panel, whatever way Libre Panel ends.

Readings: ``game.fps`` (the last second), ``game.frametime`` (ms), ``game.low``
(the 1% lows of the last 30 s, in fps), ``game.frametime_peak`` (the longest
frame time since the last reading, in ms: a stutter between two readings still
shows), ``game.limiter`` (``cpu``, ``gpu`` or ``cap``: who holds the frame rate
down, judged from how long each waits for the other) and ``game.app`` (the
program). The peak is there all the time a game runs, 0 when no frame came, so
a graph of it never stops.

The game is the program that the ``game`` service found (``set_target``);
without it, the program with the most frames. The desktop compositor and known
overlays do not count.
"""

from __future__ import annotations

import collections
import csv
import functools
import logging
import shutil
import statistics
import subprocess
import sys
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

SESSION_NAME = "LibrePanel"  # the one Windows recording session of Libre Panel
WINDOW = 1.0  # seconds the frame rate is taken over
LOWS_WINDOW = 30.0  # seconds the 1% lows are taken over
HOLD_SECONDS = 3.0  # with no game found for us: the peak goes on this long after the last frame
IGNORED = {
    "dwm.exe", "explorer.exe", "<unknown>", "steamwebhelper.exe", "discord.exe",
    "nvidia overlay.exe", "gamebar.exe", "librepanel.exe", "libre-panel.exe",
}  # fmt: skip
# the time from one frame to the next, as PresentMon 2 (and 1) names it
_FRAME_COLUMNS = ("MsBetweenPresents", "msBetweenPresents", "FrameTime", "MsBetweenAppStart")
# how long each side works and waits for the other, per frame (PresentMon 2)
_WAIT_COLUMNS = ("CPUBusy", "CPUWait", "GPUBusy", "GPUWait")

SUPERVISE_SECONDS = 1.0  # how often the session is looked after
IDLE_SECONDS = 60.0  # nobody reads game.* for this long: PresentMon ends, and starts on demand
STEADY_SECONDS = 3.0  # a PresentMon that lives this long has its session: old complaints are over
RESTART_SECONDS = 2.0  # it ended after it had worked: start it again after this long
RETRY_SECONDS = (2.0, 60.0)  # it ended at once: wait this long, doubling up to the second value
BLOCKED_SECONDS = 15.0  # the anti-cheat blocks a new session, or a game runs: try this seldom
NOT_FOUND_SECONDS = 30.0  # PresentMon is not there: look again after this long
STOP_SECONDS = 5.0  # a PresentMon that does not end when asked is killed after this long

# The game the ``game`` service found, as the file name PresentMon reports it. Kept
# here so that a source made after the game was found knows it too.
_target: str | None = None
_providers: weakref.WeakSet[PresentMonProvider] = weakref.WeakSet()


def set_target(exe: str | None) -> None:
    """Say which program is the game, as the ``game`` service finds it: only its frames
    count from now on. None takes it back (the program with the most frames counts)."""
    global _target
    _target = exe.lower() if exe else None
    for provider in list(_providers):
        provider.set_target(_target)


def find_presentmon(path: str = "") -> str | None:
    if path:
        return path if Path(path).is_file() else shutil.which(path)
    for name in ("PresentMon", "PresentMon-x64", "presentmon"):
        found = shutil.which(name)
        if found:
            return found
    for folder in (Path("C:/Program Files/Intel/PresentMon"), Path.home() / "Downloads"):
        if folder.is_dir():
            matches = sorted(folder.glob("PresentMon*.exe"), reverse=True)
            if matches:
                return str(matches[0])
    return None


# -- what the numbers mean (no Windows needed) ------------------------------------------

Waits = tuple[float, float, float, float]  # CPU busy, CPU wait, GPU busy, GPU wait (ms)
Frame = tuple[float, float, Waits | None]  # when it came, how long it took, its waits


class FrameCounter:
    """Frames per program as they come; the rate of the one that counts.

    The one that counts is ``target`` (the game the service found), else the program
    with the most frames in the last second.
    """

    def __init__(self) -> None:
        self._frames: dict[str, deque[Frame]] = {}  # app -> frames of the last LOWS_WINDOW
        self._peaks: dict[str, float] = {}  # app -> longest frame since the last readings()
        self.target: str | None = None
        self._last: tuple[str, float] | None = None  # the program that counted, and when

    def set_target(self, exe: str | None) -> None:
        exe = exe.lower() if exe else None
        if exe != self.target:
            self.target = exe
            self._last = None

    def add(self, app: str, ms: float, at: float, waits: Waits | None = None) -> None:
        if not app or app.lower() in IGNORED or not 0 < ms < 5000:
            return
        frames = self._frames.setdefault(app, deque())
        frames.append((at, ms, waits))
        self._peaks[app] = max(self._peaks.get(app, 0.0), ms)
        while frames and at - frames[0][0] > LOWS_WINDOW:
            frames.popleft()

    def readings(self, now: float) -> dict[str, Reading]:
        """What the frames say at ``now``. Call it once per reading of the sensors: the
        peak is the longest frame since the call before."""
        best = self._counted(now)
        peaks, self._peaks = self._peaks, {}
        out: dict[str, Reading] = {}
        if best is not None:
            self._last = (best, now)
            out = self._metrics(best, now)
        elif self.target is None:
            if self._last is None or now - self._last[1] > HOLD_SECONDS:
                return out  # no game: no peak, nothing
            best = self._last[0]  # a game that stalls for a moment is still a game
        # With a game found (or just seen) the peak is always there, 0 without a frame. The
        # game found counts however few frames it has: a hitch of a second is one frame.
        if self.target is not None:
            peak = max((p for app, p in peaks.items() if app.lower() == self.target), default=0.0)
        else:
            peak = peaks.get(best or "", 0.0)
        out["game.frametime_peak"] = Reading(
            "game.frametime_peak", round(peak, 2), "ms", "Frame time peak"
        )
        return out

    def _counted(self, now: float) -> str | None:
        """The program whose frames count now, or None (fewer than two in the last second)."""
        counts: dict[str, int] = {}
        for app, frames in list(self._frames.items()):
            while frames and now - frames[0][0] > LOWS_WINDOW:
                frames.popleft()
            if not frames:
                del self._frames[app]
                self._peaks.pop(app, None)
                continue
            counts[app] = sum(1 for at, _ms, _waits in reversed(frames) if now - at <= WINDOW)
        if self.target is not None:
            counts = {app: n for app, n in counts.items() if app.lower() == self.target}
        best, best_count = None, 0
        for app, count in counts.items():
            if count > best_count:
                best, best_count = app, count
        return best if best_count >= 2 else None

    def _metrics(self, app: str, now: float) -> dict[str, Reading]:
        frames = self._frames[app]
        recent = [f for f in frames if now - f[0] <= WINDOW]
        mean = sum(ms for _at, ms, _waits in recent) / len(recent)
        slowest = sorted((ms for _at, ms, _waits in frames), reverse=True)
        low = slowest[: max(1, len(slowest) // 100)]  # the slowest 1 %
        name = app[:-4] if app.lower().endswith(".exe") else app
        out = {
            "game.fps": Reading("game.fps", round(1000 / mean, 1), "fps", "Frames per second"),
            "game.frametime": Reading("game.frametime", round(mean, 2), "ms", "Frame time"),
            "game.low": Reading(
                "game.low", round(1000 / (sum(low) / len(low)), 1), "fps", "1% low"
            ),  # fmt: skip
            "game.app": Reading("game.app", name, "", "Game"),
        }
        limiter = limited_by([w for _at, _ms, w in recent if w is not None], mean)
        if limiter:
            out["game.limiter"] = Reading("game.limiter", limiter, "", "Limited by")
        return out


def limited_by(waits: list[Waits], mean: float) -> str | None:
    """``cpu``, ``gpu`` or ``cap`` from the waits of the last frames, or None.

    Judged from the waits, not from the load: when the GPU waits longer for the CPU than
    the CPU for the GPU, the CPU holds the frame rate down. When neither waits much and
    both work much less than a frame takes, something else does: a frame rate limit, or
    vertical sync.
    """
    if len(waits) < 3 or mean <= 0:
        return None
    cpu_busy, cpu_wait, gpu_busy, gpu_wait = (
        statistics.median(w[i] for w in waits) for i in range(4)
    )
    if max(cpu_busy, gpu_busy) < 0.6 * mean and max(cpu_wait, gpu_wait) < 0.25 * mean:
        return "cap"
    return "cpu" if gpu_wait > cpu_wait else "gpu"


def frame_column(header: list[str]) -> str | None:
    return next((c for c in _FRAME_COLUMNS if c in header), None)


class Columns:
    """Where PresentMon's CSV puts what is needed. The header row comes with the first
    frame, not when PresentMon starts."""

    def __init__(self, app: int, frame: int, waits: tuple[int, ...] | None) -> None:
        self.app, self.frame, self.waits = app, frame, waits

    @classmethod
    def of(cls, header: list[str]) -> Columns | None:
        """The columns of a header row; None if the row is no header or has no frame time."""
        column = frame_column(header)
        if "Application" not in header or column is None:
            return None
        waits = None
        if all(name in header for name in _WAIT_COLUMNS):  # PresentMon 2 and later
            waits = tuple(header.index(name) for name in _WAIT_COLUMNS)
        return cls(header.index("Application"), header.index(column), waits)

    def frame_of(self, row: list[str]) -> tuple[str, float, Waits | None] | None:
        """(program, frame time, waits) of a row; None for a row that is no frame
        (PresentMon writes ``NA`` where it measured nothing)."""
        try:
            app, ms = row[self.app], float(row[self.frame])
        except (IndexError, ValueError):
            return None
        waits: Waits | None = None
        if self.waits is not None:
            try:
                busy_cpu, wait_cpu, busy_gpu, wait_gpu = (float(row[i]) for i in self.waits)
                waits = (busy_cpu, wait_cpu, busy_gpu, wait_gpu)
            except (IndexError, ValueError):
                waits = None
        return app, ms, waits


# -- the job that ends PresentMon with Libre Panel (Windows) -----------------------------

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # the information class SetInformationJobObject takes


def _extended_limits() -> Any:
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION. Made on first use; fixed-size types, so
    that it has the layout Windows expects (144 bytes on 64 bit) on every system."""
    import ctypes as c

    class BasicLimits(c.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [
            ("PerProcessUserTimeLimit", c.c_int64),
            ("PerJobUserTimeLimit", c.c_int64),
            ("LimitFlags", c.c_uint32),
            ("MinimumWorkingSetSize", c.c_size_t),
            ("MaximumWorkingSetSize", c.c_size_t),
            ("ActiveProcessLimit", c.c_uint32),
            ("Affinity", c.c_size_t),
            ("PriorityClass", c.c_uint32),
            ("SchedulingClass", c.c_uint32),
        ]

    class ExtendedLimits(c.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", c.c_uint64 * 6),
            ("ProcessMemoryLimit", c.c_size_t),
            ("JobMemoryLimit", c.c_size_t),
            ("PeakProcessMemoryUsed", c.c_size_t),
            ("PeakJobMemoryUsed", c.c_size_t),
        ]

    return ExtendedLimits


@functools.cache
def _kernel32() -> Any:
    import ctypes as c

    kernel = c.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel.CreateJobObjectW.argtypes = (c.c_void_p, c.c_wchar_p)
    kernel.CreateJobObjectW.restype = c.c_void_p
    kernel.SetInformationJobObject.argtypes = (c.c_void_p, c.c_int, c.c_void_p, c.c_uint32)
    kernel.SetInformationJobObject.restype = c.c_int
    kernel.AssignProcessToJobObject.argtypes = (c.c_void_p, c.c_void_p)
    kernel.AssignProcessToJobObject.restype = c.c_int
    kernel.CloseHandle.argtypes = (c.c_void_p,)
    kernel.CloseHandle.restype = c.c_int
    return kernel


class JobObject:
    """A Windows job that ends everything in it when its last handle closes, which is when
    this process ends, however it ends. Without it a PresentMon would run on after a
    Libre Panel that was killed, until its output pipe is full."""

    def __init__(self, kernel: Any = None) -> None:
        import ctypes as c

        self._kernel = kernel or _kernel32()
        self.handle = self._kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("no job object")
        limits = _extended_limits()()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, c.byref(limits), c.sizeof(limits)
        ):
            self._kernel.CloseHandle(self.handle)
            raise OSError("the job object cannot be set up")

    def add(self, process: Any) -> bool:
        """Put a started program in the job. False if that is not possible (it may be in a
        job whose rules do not allow it): it then runs without this safeguard."""
        handle = getattr(process, "_handle", None)  # Windows' handle of a subprocess.Popen
        if handle is None:
            return False
        return bool(self._kernel.AssignProcessToJobObject(self.handle, int(handle)))


@functools.cache
def shared_job() -> JobObject | None:
    """The one job of this process, made on first use; None if Windows will not make it."""
    try:
        return JobObject()
    except Exception as exc:  # noqa: BLE001 - PresentMon then runs without the safeguard
        log.debug("presentmon: no job object (%s)", exc)
        return None


# -- the provider -------------------------------------------------------------------------


class _Run:
    """One PresentMon process and what is known about its output."""

    def __init__(self, process: Any, started: float) -> None:
        self.process, self.started = process, started
        self.errors: deque[str] = collections.deque(maxlen=4)  # the last lines of stderr
        self.header = False  # a header row came: the session works
        self.steady = False  # it has lived long enough to have its session
        self.ended_by_us = False
        self.readers: list[threading.Thread] = []


class PresentMonProvider(SensorProvider):
    """``game.*`` readings from PresentMon, while a theme or a service reads them."""

    name = "presentmon"

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        job: Any = None,
        tool: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(options)
        self.path = str(self.options.get("path", ""))
        self._counter = FrameCounter()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: Any = None
        self._program: str | None = None  # the PresentMon that was started
        self._stop = threading.Event()
        self._popen, self._job, self._tool = popen, job, tool
        self._said: str | None = None  # the problem the log has told, so that it says it once
        self.problem: str | None = None  # why there is no data, for a status display
        self.blocked = False  # the anti-cheat of a game refuses the session
        self._counter.set_target(_target)
        _providers.add(self)

    def set_target(self, exe: str | None) -> None:
        with self._lock:
            self._counter.set_target(exe)

    # -- reading -----------------------------------------------------------------------

    def read(self) -> dict[str, Reading]:
        if self._stop.is_set() or not wanted("game."):
            return {}
        self._start()
        with self._lock:
            return self._counter.readings(time.monotonic())

    def close(self) -> None:
        self._stop.set()
        self._end(self._process)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)

    # -- looking after the session -------------------------------------------------------

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="sensors-presentmon", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        """Keep a PresentMon running while somebody reads ``game.*``: start it, start it
        again when it ended (slowly while it keeps failing, or a game runs), and end it
        when nobody has read for a minute."""
        run: _Run | None = None
        next_start = 0.0
        failures = 0
        idle_since: float | None = None
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if wanted("game."):
                    idle_since = None
                elif idle_since is None:
                    idle_since = now
                elif now - idle_since > IDLE_SECONDS:
                    return
                if run is not None and run.process.poll() is None:
                    if not run.steady and now - run.started >= STEADY_SECONDS:
                        run.steady = True
                        self._recording()
                elif run is not None:
                    delay, failures = self._ended(run, failures)
                    run, next_start = None, now + delay
                if run is None and now >= next_start:
                    run = self._begin()
                    if run is None:  # not there, or it would not start
                        next_start = now + NOT_FOUND_SECONDS
                self._stop.wait(SUPERVISE_SECONDS)
        finally:
            if run is not None:
                self._end(run.process)
            self._release_session(self._program)

    def _begin(self) -> _Run | None:
        program = find_presentmon(self.path)
        if program is None:
            self._say("PresentMon not found: set path in [sensors.presentmon]")
            return None
        # Frames of every program: which one counts is decided here, so that a game that
        # starts later is no reason to start PresentMon again.
        command = [
            program, "--output_stdout", "--no_console_stats",
            "--session_name", SESSION_NAME, "--stop_existing_session",
        ]  # fmt: skip
        try:
            process = self._popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )  # fmt: skip
        except OSError as exc:
            self._say(f"PresentMon could not start: {exc}")
            return None
        self._process, self._program = process, program
        self._bind_to_job(process)
        run = _Run(process, time.monotonic())
        for target, name in ((self._read_frames, "frames"), (self._read_errors, "errors")):
            reader = threading.Thread(
                target=target, args=(run,), name=f"presentmon-{name}", daemon=True
            )
            reader.start()
            run.readers.append(reader)
        return run

    def _bind_to_job(self, process: Any) -> None:
        job = self._job
        if job is None and sys.platform == "win32":
            job = self._job = shared_job()
        if job is None:
            return
        try:
            if not job.add(process):
                log.debug("presentmon: the process could not join the job")
        except Exception:  # noqa: BLE001 - it runs without the safeguard
            log.debug("presentmon: the job refused the process", exc_info=True)

    def _read_frames(self, run: _Run) -> None:
        columns: Columns | None = None
        try:
            for row in csv.reader(run.process.stdout):
                if self._stop.is_set() or run.ended_by_us:
                    break
                if columns is None:
                    columns = Columns.of(row)
                    if columns is not None:
                        run.header = True
                    elif "Application" in row:  # a header, but not one with a frame time
                        self._say(f"PresentMon: no frame time column in {row[:12]}")
                        run.ended_by_us = True
                        self._end(run.process)
                    continue
                frame = columns.frame_of(row)
                if frame is not None:
                    with self._lock:
                        self._counter.add(frame[0], frame[1], time.monotonic(), frame[2])
        except (OSError, ValueError, csv.Error):  # the pipe closed under us
            pass

    @staticmethod
    def _read_errors(run: _Run) -> None:
        try:
            for line in run.process.stderr:
                line = line.strip()
                if line:
                    run.errors.append(line)
        except (OSError, ValueError):
            pass

    def _ended(self, run: _Run, failures: int) -> tuple[float, int]:
        """A PresentMon ended: say why, and (how long to wait, how often it failed in a row)."""
        for reader in run.readers:
            reader.join(1.5)  # the last lines of its output
        game_runs = self._counter.target is not None
        if run.header:  # it worked: start it again soon
            self.blocked = False
            return (BLOCKED_SECONDS if game_runs else RESTART_SECONDS), 0
        lines = list(run.errors)
        # The cause is in the "error:" line; before it there is always the same note on rights.
        error = " / ".join([line for line in lines if line.lower().startswith("error")] or lines)
        self.blocked = "access denied" in error.lower()
        failures += 1
        if self.blocked:
            self._say(
                "PresentMon cannot start a session while a game with an anti-cheat runs; "
                "it tries again until the game is over",
                cause="blocked",
            )
            return BLOCKED_SECONDS, failures
        if not run.ended_by_us:  # (else it was said, and the cause is the header)
            self._say(
                "PresentMon ended at once: it needs administrator rights or the "
                f'"Performance Log Users" group{": " + error if error else ""}',
                cause="rights",
            )
        delay = min(RETRY_SECONDS[0] * 2 ** (failures - 1), RETRY_SECONDS[1])
        return (max(delay, BLOCKED_SECONDS) if game_runs else delay), failures

    def _say(self, problem: str, cause: str = "") -> None:
        """Tell a problem in the log once, and again only if its cause is another (the
        words may differ from try to try)."""
        self.problem = problem
        if (cause or problem) != self._said:
            self._said = cause or problem
            log.warning("%s", problem)

    def _recording(self) -> None:
        """PresentMon lives: whatever was said before is over (said before the problem is
        cleared, so whoever sees it cleared finds the line in the log)."""
        if self._said is not None:
            self._said = None
            log.info("PresentMon is recording")
        self.problem, self.blocked = None, False

    # -- ending it -----------------------------------------------------------------------

    @staticmethod
    def _end(process: Any) -> None:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()

    def _release_session(self, program: str | None) -> None:
        """A recording session of Windows outlives its program when that is killed: stop it
        by name (the next start would do so too). Windows only."""
        tool = self._tool or (subprocess.run if sys.platform == "win32" else None)
        if tool is None or program is None:
            return
        try:
            tool(
                [program, "--session_name", SESSION_NAME, "--terminate_existing_session"],
                capture_output=True, timeout=STOP_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )  # fmt: skip
        except Exception:  # noqa: BLE001, S110 - it was only for tidiness
            pass

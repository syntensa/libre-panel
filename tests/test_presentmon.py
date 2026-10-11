"""PresentMon: the readings from its frames (the peak, who limits the frame rate, the game the
service found), its session that must be there before the game, and the job that ends it with
Libre Panel. The program, its output and Windows' job calls are faked; no Windows is needed."""

import ctypes
import gc
import queue
import subprocess
import threading
import time

import pytest
from fake_sensors import show, wait_for

from libre_panel.sensors import base, presentmon
from libre_panel.sensors.presentmon import FrameCounter, JobObject, PresentMonProvider


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """Nothing on show, no game found, PresentMon "installed", and quick paces."""
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    monkeypatch.setattr(presentmon, "_target", None)
    monkeypatch.setattr(presentmon, "find_presentmon", lambda path="": path or "PresentMon.exe")
    for name, seconds in {
        "SUPERVISE_SECONDS": 0.005,
        "STEADY_SECONDS": 0.05,
        "RESTART_SECONDS": 0.01,
        "BLOCKED_SECONDS": 0.25,
        "NOT_FOUND_SECONDS": 0.05,
        "STOP_SECONDS": 1.0,
    }.items():
        monkeypatch.setattr(presentmon, name, seconds)
    monkeypatch.setattr(presentmon, "RETRY_SECONDS", (0.01, 0.08))


# -- the numbers --------------------------------------------------------------------------------


def play(counter, app, frames, ms, start=100.0, waits=None):
    """``frames`` frames of ``ms`` each, one after the other from ``start``."""
    for i in range(frames):
        counter.add(app, ms, start + i * ms / 1000, waits)
    return start + frames * ms / 1000


def test_the_peak_is_the_longest_frame_since_the_last_readings():
    counter = FrameCounter()
    end = play(counter, "Game.exe", 100, 8.0)
    counter.add("Game.exe", 55.0, end)  # a stutter between two readings
    first = counter.readings(end + 0.01)
    assert first["game.frametime_peak"].value == 55.0 and first["game.frametime_peak"].unit == "ms"
    end = play(counter, "Game.exe", 100, 8.0, start=end + 0.5)
    second = counter.readings(end)
    assert (
        second["game.frametime_peak"].value == 8.0
    )  # only what came since: the stutter is told once


def test_the_peak_goes_on_with_zero_while_a_game_runs_and_no_frame_comes():
    counter = FrameCounter()
    counter.set_target("game.exe")
    out = counter.readings(100.0)
    assert out == {"game.frametime_peak": out["game.frametime_peak"]}
    assert out["game.frametime_peak"].value == 0.0  # a graph of it never stops
    end = play(counter, "Game.exe", 50, 8.0)
    assert counter.readings(end)["game.frametime_peak"].value == 8.0
    again = counter.readings(end + 0.1)
    assert (
        again["game.frametime_peak"].value == 0.0 and "game.fps" in again
    )  # frames are still there
    quiet_game = counter.readings(end + 40.0)  # paused, minimized: no fps, but the line goes on
    assert (
        set(quiet_game) == {"game.frametime_peak"}
        and quiet_game["game.frametime_peak"].value == 0.0
    )


def test_without_a_game_there_is_nothing_and_a_game_that_stalls_for_a_moment_is_still_one():
    counter = FrameCounter()
    assert counter.readings(100.0) == {}
    end = play(counter, "Game.exe", 100, 8.0)
    assert "game.fps" in counter.readings(end)
    stalled = counter.readings(end + 2.0)  # a loading screen: no frame for two seconds
    assert set(stalled) == {"game.frametime_peak"} and stalled["game.frametime_peak"].value == 0.0
    assert counter.readings(end + 2.0 + presentmon.HOLD_SECONDS + 0.1) == {}  # and then it is over


def test_only_the_frames_of_the_game_that_was_found_count():
    counter = FrameCounter()
    play(counter, "Game.exe", 60, 16.7, start=100.0)  # the game, at 60 fps
    end = play(counter, "Browser.exe", 200, 4.0, start=100.0)  # something busier
    assert counter.readings(end)["game.app"].value == "Browser"  # the old rule: the most frames
    counter.set_target("GAME.EXE")  # the service says which program is the game (any case)
    out = counter.readings(end)
    assert out["game.app"].value == "Game" and 55 < out["game.fps"].value < 65
    counter.set_target(None)
    assert counter.readings(end)["game.app"].value == "Browser"


def test_the_peak_is_the_one_of_the_program_that_counts():
    counter = FrameCounter()
    counter.set_target("game.exe")
    end = play(counter, "Game.exe", 100, 8.0)
    counter.add("Browser.exe", 900.0, end)  # a hitch of something else
    assert counter.readings(end)["game.frametime_peak"].value == 8.0


def test_a_target_with_fewer_than_two_frames_a_second_has_no_rate():
    counter = FrameCounter()
    counter.set_target("game.exe")
    counter.add("Game.exe", 30.0, 100.0)
    out = counter.readings(100.5)
    assert set(out) == {"game.frametime_peak"} and out["game.frametime_peak"].value == 30.0


def test_the_desktop_and_overlays_never_count_not_even_as_the_target():
    counter = FrameCounter()
    counter.set_target("dwm.exe")
    play(counter, "dwm.exe", 100, 8.0)
    out = counter.readings(101.0)
    assert set(out) == {"game.frametime_peak"} and out["game.frametime_peak"].value == 0.0


def test_junk_frames_are_dropped():
    counter = FrameCounter()
    for ms in (0.0, -3.0, 5000.0, 99999.0):
        counter.add("Game.exe", ms, 100.0)
    counter.add("", 8.0, 100.0)
    assert counter.readings(100.0) == {}


def waits(cpu_busy, cpu_wait, gpu_busy, gpu_wait):
    return (cpu_busy, cpu_wait, gpu_busy, gpu_wait)


@pytest.mark.parametrize(
    ("timing", "who"),
    [
        (waits(7.0, 0.2, 2.0, 5.0), "cpu"),  # the GPU waits for the CPU
        (waits(2.0, 5.0, 7.5, 0.1), "gpu"),  # the CPU waits for the GPU
        (waits(2.0, 0.3, 3.0, 0.4), "cap"),  # both mostly idle, and neither waits: a limit
        (waits(7.0, 0.2, 7.0, 0.2), "gpu"),  # busy both, no one waits more: not a cap
    ],
)
def test_who_limits_the_frame_rate_comes_from_the_waits(timing, who):
    counter = FrameCounter()
    end = play(counter, "Game.exe", 100, 8.0, waits=timing)
    out = counter.readings(end)
    assert out["game.limiter"].value == who and out["game.limiter"].unit == ""


def test_the_limiter_needs_the_waits_and_a_few_frames():
    counter = FrameCounter()
    end = play(counter, "Game.exe", 100, 8.0)  # PresentMon 1: no waits in its output
    assert "game.limiter" not in counter.readings(end)
    few = FrameCounter()
    few.add("Game.exe", 8.0, 100.0, waits(7.0, 0.2, 2.0, 5.0))
    few.add("Game.exe", 8.0, 100.01, waits(7.0, 0.2, 2.0, 5.0))
    assert "game.limiter" not in few.readings(100.02)  # two frames say too little
    assert presentmon.limited_by([], 8.0) is None


def test_the_limiter_goes_by_the_middle_of_the_waits_not_by_a_hitch():
    counter = FrameCounter()
    end = play(counter, "Game.exe", 101, 8.0, waits=waits(2.0, 5.0, 7.5, 0.1))  # the GPU holds it
    counter.add("Game.exe", 8.0, end, waits(2.0, 0.1, 7.5, 400.0))  # one frame says otherwise
    assert counter.readings(end)["game.limiter"].value == "gpu"


# -- PresentMon's output ---------------------------------------------------------------------------

HEADER = "Application,ProcessID,FrameTime,CPUBusy,CPUWait,GPUBusy,GPUWait,DisplayLatency"


def row(app, ms, cpu_busy=1.0, cpu_wait=1.0, gpu_busy=5.0, gpu_wait=0.5, latency="NA"):
    return f"{app},42,{ms},{cpu_busy},{cpu_wait},{gpu_busy},{gpu_wait},{latency}"


def test_the_header_row_says_where_things_are():
    columns = presentmon.Columns.of(HEADER.split(","))
    assert (columns.app, columns.frame, columns.waits) == (0, 2, (3, 4, 5, 6))
    assert columns.frame_of(row("Game.exe", 8.5, 1, 2, 3, 4).split(",")) == (
        "Game.exe", 8.5, (1.0, 2.0, 3.0, 4.0),
    )  # fmt: skip
    # PresentMon 1 has no waits and calls the frame time something else
    old = presentmon.Columns.of(["Application", "ProcessID", "MsBetweenPresents"])
    assert (old.frame, old.waits) == (2, None)
    assert old.frame_of(["Game.exe", "42", "7.5"]) == ("Game.exe", 7.5, None)
    assert presentmon.Columns.of(["Application", "ProcessID"]) is None  # no frame time
    assert presentmon.Columns.of(["Game.exe", "42", "7.5"]) is None  # not a header


def test_na_and_short_rows_are_no_frames_or_no_waits():
    columns = presentmon.Columns.of(HEADER.split(","))
    assert columns.frame_of(["Game.exe", "42", "NA", "1", "1", "1", "1", "NA"]) is None
    assert columns.frame_of(["Game.exe"]) is None
    assert columns.frame_of(["Game.exe", "42", "8.0", "NA", "1", "1", "1", "NA"]) == (
        "Game.exe", 8.0, None,
    )  # a frame, whose waits are not known  # fmt: skip


# -- the program ---------------------------------------------------------------------------------


class Pipe:
    """A text stream fed by hand; the thread that reads it waits on it like on a real pipe."""

    def __init__(self):
        self._lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        line = self._lines.get()
        if line is None:
            raise StopIteration
        return line

    def feed(self, *lines):
        for line in lines:
            self._lines.put(line + "\n")

    def close(self):
        self._lines.put(None)


class FakeProcess:
    """PresentMon by hand: it writes what the test feeds it, ends when told to, or when asked to."""

    def __init__(self):
        self.stdout, self.stderr = Pipe(), Pipe()
        self.returncode = None
        self._handle = 4242  # what Windows' Popen has
        self.terminated = self.killed = 0
        self.deaf = False  # does not end when asked to

    def poll(self):
        return self.returncode

    def finish(self, code=0):
        if self.returncode is None:
            self.returncode = code
            self.stdout.close()
            self.stderr.close()

    def terminate(self):
        self.terminated += 1
        if not self.deaf:
            self.finish(1)

    def kill(self):
        self.killed += 1
        self.finish(-9)

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("PresentMon", timeout)
        return self.returncode


class Launcher:
    """``subprocess.Popen`` by hand: what is asked to start, with which options, and the
    processes that come (those given first, then plain ones)."""

    def __init__(self, *queued):
        self.queued, self.started = list(queued), []

    def __call__(self, command, **options):
        item = self.queued.pop(0) if self.queued else FakeProcess()
        if isinstance(item, Exception):
            raise item
        self.started.append((command, options, item))
        return item

    @property
    def processes(self):
        return [process for _command, _options, process in self.started]


class FakeJob:
    def __init__(self, fails=False):
        self.added, self.fails = [], fails

    def add(self, process):
        self.added.append(process)
        if self.fails:
            raise OSError("access denied")
        return True


class Tool:
    """``subprocess.run`` for the call that ends the recording session by name."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))


@pytest.fixture
def make():
    made = []

    def build(*queued, job=None, **options):
        launcher, tool = Launcher(*queued), Tool()
        provider = PresentMonProvider(options, popen=launcher, job=job or FakeJob(), tool=tool)
        made.append(provider)
        return provider, launcher, tool

    yield build
    for provider in made:
        provider.close()


def first_process(launcher):
    assert wait_for(lambda: launcher.started)
    return launcher.processes[0]


def test_it_starts_when_somebody_reads_game_and_not_before(make):
    provider, launcher, _tool = make()
    assert provider.read() == {} and provider._thread is None and not launcher.started
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    command, options, _ = launcher.started[0]
    assert command[0] == "PresentMon.exe"
    assert command[1:] == [
        "--output_stdout", "--no_console_stats", "--session_name", "LibrePanel",
        "--stop_existing_session",
    ]  # fmt: skip
    assert options["stdout"] == options["stderr"] == subprocess.PIPE
    assert options["encoding"] == "utf-8" and options["bufsize"] == 1
    assert process.returncode is None and theme is not None


def test_it_takes_the_path_from_the_options(make, monkeypatch):
    asked = []
    monkeypatch.setattr(
        presentmon, "find_presentmon", lambda path="": asked.append(path) or "x.exe"
    )
    provider, launcher, _tool = make(path="C:/Tools/PresentMon.exe")
    theme = show("game.fps")
    provider.read()
    first_process(launcher)
    assert asked[0] == "C:/Tools/PresentMon.exe" and theme is not None


def feed_game(process, frames=60, app="Racer.exe", ms=8.0, **timing):
    process.stdout.feed(HEADER, *[row(app, ms, **timing) for _ in range(frames)])


def test_frames_become_readings(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    feed_game(process, 60)
    assert wait_for(lambda: "game.fps" in provider.read())
    out = provider.read()
    assert out["game.app"].value == "Racer" and 100 < out["game.fps"].value < 130
    assert out["game.limiter"].value in ("cap", "cpu", "gpu") and "game.frametime_peak" in out
    assert theme is not None


def test_a_stutter_shows_in_one_reading_and_only_one(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    peaks = []

    def reading():
        found = provider.read().get("game.frametime_peak")
        if found is not None:
            peaks.append(found.value)
        return 61.5 in peaks

    process.stdout.feed(HEADER, *[row("Racer.exe", 8.0)] * 30, row("Racer.exe", 61.5))
    assert wait_for(reading)  # in the reading after the frame came
    process.stdout.feed(*[row("Racer.exe", 8.0)] * 30)
    assert wait_for(lambda: peaks.append(provider.read()["game.frametime_peak"].value) or peaks[-1])
    assert peaks.count(61.5) == 1 and theme is not None  # told once, not again and again


def test_the_game_the_service_found_is_the_one_that_counts(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    presentmon.set_target("Racer.exe")
    process.stdout.feed(HEADER, *[row("Other.exe", 2.0)] * 300, *[row("Racer.exe", 16.0)] * 60)
    assert wait_for(lambda: provider.read().get("game.app") is not None)
    out = provider.read()
    assert out["game.app"].value == "Racer" and 55 < out["game.fps"].value < 70
    presentmon.set_target(None)
    assert wait_for(lambda: provider.read()["game.app"].value == "Other")
    assert theme is not None


def test_a_source_made_after_the_game_was_found_knows_it(make):
    presentmon.set_target("Racer.exe")
    provider, _launcher, _tool = make()
    assert provider._counter.target == "racer.exe"
    presentmon.set_target(None)
    assert provider._counter.target is None


def test_the_process_is_put_in_the_job_and_runs_without_it_when_that_fails(make):
    job = FakeJob()
    provider, launcher, _tool = make(job=job)
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    assert wait_for(lambda: job.added == [process])
    broken = FakeJob(fails=True)
    other, launcher2, _tool2 = make(job=broken)
    other.read()
    process2 = first_process(launcher2)
    assert wait_for(lambda: broken.added == [process2])
    feed_game(process2, 30)
    assert wait_for(lambda: "game.fps" in other.read())  # and it goes on without the safeguard
    assert theme is not None


def test_it_goes_on_running_as_long_as_somebody_reads_and_is_not_started_again(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    time.sleep(0.15)  # many looks after the session, and well past the time it needs to settle
    assert len(launcher.started) == 1 and process.returncode is None
    assert provider.problem is None and theme is not None


def test_an_ended_presentmon_is_started_again_soon_when_no_game_runs(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    first = first_process(launcher)
    feed_game(first, 10)
    assert wait_for(lambda: "game.fps" in provider.read())
    first.finish(1)
    assert wait_for(lambda: len(launcher.started) == 2)  # RESTART_SECONDS is 0.01
    assert theme is not None


def test_while_a_game_runs_it_is_started_again_only_slowly(make, monkeypatch):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    presentmon.set_target("racer.exe")
    provider.read()
    first = first_process(launcher)
    feed_game(first, 10)
    assert wait_for(lambda: "game.frametime_peak" in provider.read())
    first.finish(1)
    time.sleep(0.12)  # far longer than the quick restart, shorter than BLOCKED_SECONDS (0.25)
    assert len(launcher.started) == 1
    assert wait_for(lambda: len(launcher.started) == 2)  # but it comes
    assert theme is not None


def test_a_session_the_anti_cheat_refuses_is_tried_again_slowly_and_said_once(make, caplog):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    with caplog.at_level("INFO", logger=presentmon.__name__):
        provider.read()
        first = first_process(launcher)
        first.stderr.feed(
            "warning: PresentMon requires administrator privileges or Performance Log Users",
            "error: failed to start trace session (access denied).",
        )
        first.finish(1)
        assert wait_for(lambda: provider.blocked)
        time.sleep(0.1)
        assert len(launcher.started) == 1  # BLOCKED_SECONDS (0.25) has not passed
        assert wait_for(lambda: len(launcher.started) == 2)
        second = launcher.processes[1]
        second.stderr.feed("error: failed to start trace session (access denied).")
        second.finish(1)
        assert wait_for(lambda: len(launcher.started) == 3)
    said = [r.message for r in caplog.records if "anti-cheat" in r.message]
    assert len(said) == 1  # the same trouble is said once
    assert "rights" not in caplog.text and "administrator rights" not in caplog.text
    assert theme is not None


def test_a_presentmon_that_cannot_run_without_rights_says_so_and_waits_longer_each_time(
    make, caplog
):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    with caplog.at_level("INFO", logger=presentmon.__name__):
        provider.read()
        first = first_process(launcher)
        first.stderr.feed("error: PresentMon requires administrator privileges")
        first.finish(1)
        assert wait_for(lambda: len(launcher.started) == 2)
        launcher.processes[1].finish(1)
        assert wait_for(lambda: len(launcher.started) == 3)
        launcher.processes[2].finish(1)
        assert wait_for(lambda: len(launcher.started) == 4)
    texts = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert (
        len(texts) == 1 and 'administrator rights or the "Performance Log Users" group' in texts[0]
    )
    assert "requires administrator privileges" in texts[0]  # and what PresentMon said
    assert provider.problem.startswith("PresentMon ended at once") and not provider.blocked
    assert theme is not None


def test_a_presentmon_that_works_clears_the_old_complaint(make, caplog):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    with caplog.at_level("INFO", logger=presentmon.__name__):
        provider.read()
        first = first_process(launcher)
        first.finish(1)  # at once, with nothing to say
        assert wait_for(lambda: len(launcher.started) == 2)
        assert provider.problem is not None
        assert wait_for(lambda: provider.problem is None)  # the second one lives
    assert "PresentMon is recording" in caplog.text and theme is not None


def test_presentmon_that_is_not_there_is_looked_for_again(make, monkeypatch, caplog):
    where = []
    monkeypatch.setattr(presentmon, "find_presentmon", lambda path="": where[0] if where else None)
    provider, launcher, _tool = make()
    theme = show("game.fps")
    with caplog.at_level("WARNING", logger=presentmon.__name__):
        provider.read()
        assert wait_for(lambda: provider.problem is not None)
        time.sleep(0.15)  # NOT_FOUND_SECONDS is 0.05: several looks
        assert not launcher.started
        where.append("C:/Tools/PresentMon.exe")  # the user put it there
        assert wait_for(lambda: launcher.started)
    assert caplog.text.count("PresentMon not found") == 1 and theme is not None


def test_a_presentmon_that_cannot_start_is_said_and_tried_again(make, caplog):
    provider, launcher, _tool = make(OSError("not a valid program"))
    theme = show("game.fps")
    with caplog.at_level("WARNING", logger=presentmon.__name__):
        provider.read()
        assert wait_for(lambda: provider.problem and "could not start" in provider.problem)
        assert wait_for(lambda: launcher.started)  # the next try works
    assert "not a valid program" in caplog.text and theme is not None


def test_an_output_without_a_frame_time_ends_it_and_says_so(make, caplog):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    with caplog.at_level("WARNING", logger=presentmon.__name__):
        provider.read()
        process = first_process(launcher)
        process.stdout.feed("Application,ProcessID,SomethingElse", "Game.exe,42,1.0")
        assert wait_for(lambda: process.terminated)
        assert wait_for(lambda: provider.problem and "no frame time column" in provider.problem)
    assert "administrator rights" not in caplog.text  # that is not the trouble
    assert theme is not None


# -- stopping --------------------------------------------------------------------------------------


def test_nobody_reading_for_a_minute_ends_it_and_the_session(make, monkeypatch):
    monkeypatch.setattr(presentmon, "IDLE_SECONDS", 0.05)
    provider, launcher, tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    feed_game(process, 10)
    assert wait_for(lambda: "game.fps" in provider.read())
    del theme
    gc.collect()  # the theme is gone: nobody reads game.*
    assert wait_for(
        lambda: (
            process.terminated and provider._thread is not None and not provider._thread.is_alive()
        )
    )
    assert provider.read() == {}
    assert tool.calls and tool.calls[0][0][1:] == [
        "--session_name",
        "LibrePanel",
        "--terminate_existing_session",
    ]
    theme = show("game.fps")  # and somebody reads again: it starts again
    provider.read()
    assert wait_for(lambda: len(launcher.started) == 2) and theme is not None


def test_the_service_keeps_it_running_though_no_theme_shows_game(make, monkeypatch):
    """host.want("game.*"): the session must be there before the game starts."""
    monkeypatch.setattr(presentmon, "IDLE_SECONDS", 0.05)
    provider, launcher, _tool = make()
    reader = type("Service", (), {})()
    base.want(reader, {"game.*"})
    provider.read()
    process = first_process(launcher)
    time.sleep(0.2)  # four times the idle time
    assert process.returncode is None and len(launcher.started) == 1
    assert reader is not None


def test_close_ends_the_process_and_the_session_and_the_thread(make):
    provider, launcher, tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    thread = provider._thread
    provider.close()
    assert process.terminated >= 1 and process.returncode is not None
    assert not thread.is_alive()
    assert len(tool.calls) == 1
    command, options = tool.calls[0]
    assert command == [
        "PresentMon.exe",
        "--session_name",
        "LibrePanel",
        "--terminate_existing_session",
    ]
    assert options["timeout"] == presentmon.STOP_SECONDS and options["capture_output"]
    assert provider.read() == {} and theme is not None  # and it stays closed
    provider.close()  # twice is fine


def test_a_presentmon_that_does_not_end_when_asked_is_killed(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    process = first_process(launcher)
    process.deaf = True
    provider.close()
    assert process.killed >= 1 and process.returncode == -9 and theme is not None


def test_on_other_systems_than_windows_no_session_is_stopped_by_name():
    provider = PresentMonProvider({}, popen=Launcher(), job=FakeJob())  # no tool of its own
    assert provider._tool is None
    provider._release_session("PresentMon.exe")  # must not run anything: Windows only


# -- the job ---------------------------------------------------------------------------------


class FakeKernel:
    """kernel32's job calls by hand, keeping what they are given."""

    def __init__(self, handle=77, sets=True, assigns=True):
        self.handle, self.sets, self.assigns = handle, sets, assigns
        self.created, self.set, self.assigned, self.closed = [], [], [], []

    def CreateJobObjectW(self, attributes, name):  # noqa: N802 - Windows' name
        self.created.append((attributes, name))
        return self.handle

    def SetInformationJobObject(self, job, info_class, info, size):  # noqa: N802
        self.set.append(
            (job, info_class, bytes(ctypes.string_at(ctypes.addressof(info._obj), size)), size)
        )
        self.limits = info._obj
        return int(self.sets)

    def AssignProcessToJobObject(self, job, process):  # noqa: N802
        self.assigned.append((job, process))
        return int(self.assigns)

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


def test_the_job_ends_what_is_in_it_when_the_last_handle_closes():
    kernel = FakeKernel()
    job = JobObject(kernel)
    assert kernel.created == [(None, None)]
    [(handle, info_class, raw, size)] = kernel.set
    assert (handle, info_class) == (77, 9)  # JobObjectExtendedLimitInformation
    assert (
        kernel.limits.BasicLimitInformation.LimitFlags == 0x2000
    )  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert size == len(raw) == (144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)
    # the flag stands where Windows reads it: after the two 8-byte time limits
    assert int.from_bytes(raw[16:20], "little") == 0x2000
    assert job.handle == 77 and kernel.closed == []


def test_a_program_started_is_added_to_the_job_by_its_handle():
    kernel = FakeKernel()
    job = JobObject(kernel)
    process = FakeProcess()
    assert job.add(process) is True
    assert kernel.assigned == [(77, 4242)]
    assert job.add(object()) is False  # no handle (not Windows): nothing is asked
    assert len(kernel.assigned) == 1
    kernel.assigns = False
    assert job.add(process) is False  # Windows refuses (the program is in a job already)


def test_no_job_means_an_error_and_nothing_is_left_open():
    with pytest.raises(OSError, match="no job"):
        JobObject(FakeKernel(handle=0))
    kernel = FakeKernel(sets=False)
    with pytest.raises(OSError, match="cannot be set up"):
        JobObject(kernel)
    assert kernel.closed == [77]


def test_the_shared_job_is_none_where_windows_will_not_make_it(monkeypatch):
    presentmon.shared_job.cache_clear()
    monkeypatch.setattr(presentmon, "JobObject", lambda: (_ for _ in ()).throw(OSError("no")))
    try:
        assert presentmon.shared_job() is None
    finally:
        presentmon.shared_job.cache_clear()


def test_where_the_program_is_not_windows_no_job_is_made(make, monkeypatch):
    monkeypatch.setattr(presentmon.sys, "platform", "linux")
    asked = []
    monkeypatch.setattr(presentmon, "shared_job", lambda: asked.append(1))
    provider = PresentMonProvider({}, popen=Launcher(), tool=Tool())
    provider._bind_to_job(FakeProcess())
    assert asked == []
    monkeypatch.setattr(presentmon.sys, "platform", "win32")
    provider._bind_to_job(FakeProcess())
    assert asked == [1]


def test_threads_of_a_provider_that_was_closed_are_gone(make):
    provider, launcher, _tool = make()
    theme = show("game.fps")
    provider.read()
    first_process(launcher)
    provider.close()
    assert wait_for(
        lambda: all(
            not t.name.startswith("presentmon") or not t.is_alive() for t in threading.enumerate()
        )
    )
    assert theme is not None

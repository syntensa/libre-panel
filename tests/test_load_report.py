"""What Libre Panel costs the PC (load_report.py): the sampler on stand-ins for psutil's processes
and threads, what each process is, where the sensor service is, the text for an issue, and the
thread that samples only while somebody asks."""

import threading
import time
from collections import namedtuple

import psutil
import pytest
from fake_sensors import wait_for

from libre_panel import load_report
from libre_panel.load_report import LoadSampler, ServiceFound, find_service, role_of
from libre_panel.sensors.librehardwaremonitor import LhmStatus, PartTime, RoundTime, Timing

Times = namedtuple("Times", "user system")
Memory = namedtuple("Memory", "rss")
MB = 1024**2


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeProcess:
    """A process as psutil hands it over: its processor seconds (``cpu``), its memory, its
    children and, for this process, its threads (id -> seconds). It counts what is asked."""

    def __init__(self, pid, name="python3", cmdline=(), cpu=0.0, rss=10 * MB, born=0.0,
                 parent=None, children=(), threads=None):  # fmt: skip
        self.pid, self._name, self._cmdline = pid, name, list(cmdline)
        self.cpu, self.rss, self.born, self.parent = cpu, rss, born, parent
        self.kids = list(children)
        self.thread_times = threads or {}
        self.gone = False
        self.asked = {"name": 0, "cmdline": 0, "children": 0}

    def _alive(self):
        if self.gone:
            raise psutil.NoSuchProcess(self.pid)

    def cpu_times(self):
        self._alive()
        return Times(self.cpu * 0.75, self.cpu * 0.25)

    def memory_info(self):
        self._alive()
        return Memory(self.rss)

    def create_time(self):
        return self.born

    def name(self):
        self.asked["name"] += 1
        return self._name

    def cmdline(self):
        self.asked["cmdline"] += 1
        return self._cmdline

    def ppid(self):
        return self.parent

    def children(self, recursive=False):
        self.asked["children"] += 1
        found = []
        for kid in self.kids:
            if kid.gone:
                continue
            found.append(kid)
            if recursive:
                found += kid.children(recursive=True)
        return found

    def threads(self):
        return [(tid, seconds * 0.8, seconds * 0.2) for tid, seconds in self.thread_times.items()]


class Machine:
    """Libre Panel as it runs on Windows with two panels in the video mode, PresentMon, the
    window and the sensor service; ``run(seconds, **cpu)`` lets the processes spend time."""

    def __init__(self):
        self.webview = [FakeProcess(31, "msedgewebview2.exe", born=10.0, rss=100 * MB),
                        FakeProcess(32, "msedgewebview2.exe", born=10.0, rss=50 * MB)]  # fmt: skip
        window = ["C:/LP/LibrePanel.exe", "window", "http://127.0.0.1:8765/"]
        self.window = FakeProcess(30, "LibrePanel.exe", window, born=10.0, children=self.webview)
        self.ffmpeg2 = FakeProcess(21, "ffmpeg.exe", born=10.0, parent=20, rss=40 * MB)
        self.panel2 = FakeProcess(20, "libre-panel.exe", ["libre-panel.exe",
                                  "--multiprocessing-fork"], born=10.0, children=[self.ffmpeg2],
                                  rss=90 * MB)  # fmt: skip
        self.ffmpeg1 = FakeProcess(11, "ffmpeg.exe", born=10.0, parent=1, rss=40 * MB)
        self.presentmon = FakeProcess(12, "PresentMon-2.3.0-x64.exe", born=10.0, parent=1)
        self.conhost = FakeProcess(13, "conhost.exe", born=10.0, parent=1, rss=5 * MB)
        self.me = FakeProcess(1, "libre-panel.exe", born=5.0, rss=200 * MB,
                              children=[self.panel2, self.ffmpeg1, self.presentmon, self.conhost,
                                        self.window],
                              threads={101: 0.0, 102: 0.0, 103: 0.0, 104: 0.0})  # fmt: skip
        self.service = FakeProcess(900, "libre-panel-sensors.exe", born=1.0, rss=80 * MB)
        self.processes = {p.pid: p for p in [self.me, self.panel2, self.service, self.window]}
        self.names = {101: "MainThread", 102: "panel", 103: "Thread-9 (process_request_thread)"}
        self.lhm = None
        self.looked = 0
        self.found = ServiceFound(900, "service manager")
        self.clock = Clock()

    def find(self):
        self.looked += 1
        return self.found

    def sampler(self, **options):
        defaults = dict(
            me=self.me, process=self.processes.__getitem__, cpu_count=8,
            panels=lambda: {20: 2}, window=lambda: 30, service=self.find,
            lhm=lambda: self.lhm, names=lambda: dict(self.names), clock=self.clock,
            wall=lambda: 1_000_000.0 + self.clock.now, platform="win32",
        )  # fmt: skip
        return LoadSampler(**{**defaults, **options})

    def run(self, seconds, threads=None, **cpu):
        self.clock.now += seconds
        for name, spent in cpu.items():
            getattr(self, name).cpu += spent
        for tid, spent in (threads or {}).items():
            self.me.thread_times[tid] = self.me.thread_times.get(tid, 0.0) + spent


def rows(report):
    return {(r["role"], r["panel"], r["name"]): r for r in report["processes"]}


# -- what a process is --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, cmdline, pid, parent, expected",
    [
        ("libre-panel.exe", ["libre-panel.exe", "--multiprocessing-fork"], 20, 1, ("panel", 2)),
        (
            "python3",
            [
                "python3",
                "-c",
                "from multiprocessing.spawn import spawn_main",
                "--multiprocessing-fork",
            ],
            25,
            1,
            ("panel", None),
        ),  # fmt: skip
        ("ffmpeg.exe", ["ffmpeg", "-i", "-"], 21, 20, ("ffmpeg", 2)),
        ("ffmpeg", ["ffmpeg"], 11, 1, ("ffmpeg", None)),  # the main process's
        ("PresentMon-2.3.0-x64.exe", [], 12, 1, ("presentmon", None)),
        (
            "msedgewebview2.exe",
            ["msedgewebview2.exe", "--type=renderer"],
            31,
            30,
            ("webview", None),
        ),
        ("WebKitWebProcess", [], 33, 30, ("webview", None)),
        (
            "LibrePanel.exe",
            ["C:/LP/LibrePanel.exe", "window", "http://x/"],
            34,
            1,
            ("window", None),
        ),
        (
            "python3",
            ["python3", "-m", "libre_panel", "window", "http://x/"],
            35,
            1,
            ("window", None),
        ),
        ("python3", ["python3", "tool.py", "window"], 36, 1, ("other", None)),
        ("conhost.exe", ["conhost.exe", "0xffffffff"], 13, 1, ("other", None)),
        ("anything", [], 30, 1, ("window", None)),  # the one window.json names
    ],
)
def test_what_a_process_is(name, cmdline, pid, parent, expected):
    assert role_of(name, cmdline, pid, parent, {20: 2}, window=30) == expected


# -- a sample -------------------------------------------------------------------------------------


def test_every_process_is_measured_over_the_window_per_core_and_per_pc():
    machine = Machine()
    sampler = machine.sampler()
    assert sampler.sample() is None  # the first: where the next starts
    machine.run(2.0, me=0.5, panel2=0.2, ffmpeg2=0.4, ffmpeg1=0.1, presentmon=0.02, window=0.06,
                service=0.04, conhost=0.0)  # fmt: skip
    machine.webview[0].cpu += 0.1
    report = sampler.sample()
    assert report["ready"] and report["cpus"] == 8 and report["window_s"] == 2.0
    found = rows(report)
    main = found[("main", None, "")]
    assert (main["cpu_core"], main["cpu_pc"], main["rss"]) == (25.0, 3.12, 200 * MB)
    assert found[("panel", 2, "")]["cpu_core"] == 10.0
    assert found[("ffmpeg", 2, "")]["cpu_core"] == 20.0  # the video of panel 2, its process's
    assert found[("ffmpeg", None, "")]["cpu_core"] == 5.0  # the main process's
    assert found[("presentmon", None, "")]["cpu_core"] == 1.0
    assert found[("window", None, "")]["cpu_core"] == 3.0
    webview = found[("webview", None, "")]
    assert webview["count"] == 2 and webview["cpu_core"] == 5.0 and webview["rss"] == 150 * MB
    service = found[("service", None, "")]
    assert service["cpu_core"] == 2.0 and service["found_by"] == "service manager"
    assert found[("other", None, "conhost.exe")]["label"] == "other: conhost.exe"
    assert [r["role"] for r in report["processes"]] == [
        "main", "panel", "ffmpeg", "ffmpeg", "presentmon", "window", "webview", "service", "other"
    ]  # fmt: skip
    total = report["total"]
    assert total["cpu_core"] == pytest.approx(71.0) and total["cpu_pc"] == pytest.approx(
        71 / 8, abs=0.01
    )
    assert total["rss"] == sum(r["rss"] for r in report["processes"])
    assert report["service"] == {"state": "running", "found_by": "service manager"}
    assert report["sample_ms"] >= 0


def test_this_processs_threads_by_their_names_and_the_rest_as_other():
    machine = Machine()
    sampler = machine.sampler()
    sampler.sample()
    # MainThread 2 %, the panel 15 %, a request 1 %, a thread without a Python name (.NET) 3 %,
    # and one that ended meanwhile: the process spent 25 % in all
    machine.run(2.0, me=0.5, threads={101: 0.04, 102: 0.3, 103: 0.02, 104: 0.06, 105: 0.0})
    threads = sampler.sample()["threads"]
    assert [(t["name"], t["count"], t["cpu_core"]) for t in threads] == [
        ("panel", 1, 15.0),
        ("MainThread", 1, 2.0),
        ("other", 3, 8.0),  # the request, the .NET thread, the new one, and 4 % that ended
    ]
    assert threads[0]["cpu_pc"] == round(15 / 8, 2)


def test_a_process_that_started_in_the_window_counts_from_its_start_an_old_one_found_later_not():
    machine = Machine()
    sampler = machine.sampler()
    sampler.sample()
    late = FakeProcess(40, "ffmpeg.exe", cpu=0.2, born=1_000_000.0 + machine.clock.now + 0.5,
                       parent=1)  # fmt: skip
    old = FakeProcess(41, "helper.exe", cpu=50.0, born=3.0, parent=1)
    machine.me.kids += [late, old]
    machine.run(load_report.TREE_S)  # (the tree is walked again)
    found = rows(sampler.sample())
    assert found[("ffmpeg", None, "")]["count"] == 2
    assert found[("ffmpeg", None, "")]["cpu_core"] == round(0.2 / load_report.TREE_S * 100, 2)
    assert found[("other", None, "helper.exe")]["cpu_core"] is None  # nothing to count from
    machine.run(2.0, me=0.1)
    old.cpu += 0.2
    assert rows(sampler.sample())[("other", None, "helper.exe")]["cpu_core"] == 10.0


def test_the_tree_is_walked_now_and_then_and_a_name_asked_for_once():
    machine = Machine()
    sampler = machine.sampler()
    for _ in range(4):
        sampler.sample()
        machine.run(2.0, me=0.1)
    assert machine.me.asked["children"] == 2  # at 0 s and at 6 s
    assert machine.ffmpeg2.asked == {"name": 1, "cmdline": 1, "children": 2}
    machine.ffmpeg1.gone = True  # it ended: gone from the report at once
    report = sampler.sample()
    assert ("ffmpeg", None, "") not in rows(report) and ("ffmpeg", 2, "") in rows(report)


def test_the_sensor_service_is_looked_for_now_and_then_while_it_is_not_found():
    machine = Machine()
    machine.found = ServiceFound(None, state="not running")
    sampler = machine.sampler()
    sampler.sample()
    machine.run(2.0)
    report = sampler.sample()
    assert report["service"] == {"state": "not running", "found_by": ""}
    assert "sensor service: not running" in report["text"]
    assert machine.looked == 1
    machine.found = ServiceFound(900, "program name")
    for _ in range(int(load_report.LOOK_S / 2) - 2):  # (looked for at 0 s; now at 2 s)
        machine.run(2.0)
        assert ("service", None, "") not in rows(sampler.sample())
    assert machine.looked == 1  # not every sample: every LOOK_S
    machine.run(2.0)
    found = rows(sampler.sample())
    assert machine.looked == 2 and found[("service", None, "")]["found_by"] == "program name"
    machine.service.gone = True  # it stopped: looked for at once the next time
    machine.run(2.0)
    sampler.sample()
    assert machine.looked == 2
    machine.run(2.0)
    sampler.sample()
    assert machine.looked == 3


def test_the_sensor_service_is_where_the_service_control_manager_says_else_by_its_program():
    assert find_service("linux") is None  # there is none
    assert find_service("win32", status=lambda name: (4, 900)) == ServiceFound(
        900, "service manager"
    )
    assert find_service("win32", status=lambda name: None) == ServiceFound(
        None, "", "not installed"
    )
    assert find_service("win32", status=lambda name: (1, 0)).state == "not running"

    def refused(name):
        raise OSError(5, "access denied")

    Found = namedtuple("Found", "pid info")
    listed = [Found(4, {"name": "System"}), Found(901, {"name": "Libre-Panel-Sensors.exe"})]
    assert find_service("win32", status=refused, processes=lambda attrs: listed) == ServiceFound(
        901, "program name"
    )
    assert find_service("win32", status=refused, processes=lambda attrs: []).pid is None


# -- the rounds of LibreHardwareMonitor -----------------------------------------------------------

CPU = PartTime("/amdcpu/0", "8-Core Processor", "Cpu", 0.0061)
GPU = PartTime("/gpu-nvidia/0", "Graphics Card", "GpuNvidia", 0.0037)
CHIP = PartTime("/lpc/nct0000/0", "Super I/O Chip", "SuperIO", 0.020)
RAM = PartTime("/ram", "Total Memory", "Memory", 0.0005)


def test_the_rounds_slowest_first_and_an_older_service_that_does_not_say():
    timing = Timing(RoundTime((GPU, CPU)), RoundTime((CPU, GPU, CHIP, RAM), full=True))
    status = LhmStatus("service", True, True, "2.0.1", timing=timing, version="0.7.1")
    report = load_report.sensors_report(status)
    assert report["reported"] and report["version"] == "0.7.1"
    assert report["last"]["ms"] == 9.8 and not report["last"]["full"]
    assert [p["kind"] for p in report["last"]["parts"]] == ["Cpu", "GpuNvidia"]
    assert [p["kind"] for p in report["full"]["parts"]] == ["SuperIO", "Cpu", "GpuNvidia", "Memory"]
    lines = load_report.sensors_text(report)
    assert lines[0] == "LibreHardwareMonitor rounds, the sensor service 0.7.1:"
    assert lines[1] == "  last round: 9.8 ms (Cpu 6.1, GpuNvidia 3.7)"
    assert lines[2].startswith("  all hardware: 30.3 ms (SuperIO 20.0, Cpu 6.1")
    older = load_report.sensors_report(LhmStatus("service", True, True, version="0.6.0"))
    assert not older["reported"] and older["last"] is None
    assert load_report.sensors_text(older) == [
        "LibreHardwareMonitor rounds, the sensor service 0.6.0: "
        "not reported by this service version"
    ]
    silent = load_report.sensors_report(LhmStatus("service", error="the pipe is busy"))
    assert load_report.sensors_text(silent)[0].endswith(": the pipe is busy")
    web = load_report.sensors_report(LhmStatus("webserver"))
    assert "its own program" in load_report.sensors_text(web)[0]
    inside = Timing(RoundTime(()), None)  # nothing read: no theme on show needs it
    lines = load_report.sensors_text(
        load_report.sensors_report(LhmStatus("inprocess", timing=inside))
    )
    assert lines == ["LibreHardwareMonitor rounds, inside Libre Panel:",
                     "  last round: 0.0 ms (nothing read)"]  # fmt: skip
    assert load_report.sensors_report(None) is None


def test_the_text_is_for_an_issue():
    machine = Machine()
    machine.lhm = LhmStatus("service", timing=Timing(RoundTime((CPU,))), version="0.7.1")
    sampler = machine.sampler()
    sampler.sample()
    machine.run(2.0, me=0.5, panel2=0.2, ffmpeg2=0.4, threads={102: 0.3})
    text = sampler.sample()["text"]
    lines = text.splitlines()
    assert lines[0].startswith("Libre Panel load over 2.0 s, 8 logical processors")
    assert "Task Manager" in lines[0]
    assert lines[2].split() == ["Libre", "Panel,", "total", "6.9", "%", "55.0", "%", "625", "MB"]
    assert lines[3].split() == ["main", "process", "3.1", "%", "25.0", "%", "200", "MB"]
    assert any(line.startswith("ffmpeg (panel 2)") for line in lines)
    assert any(line.startswith("WebView2 ×2") for line in lines)
    assert "(the sensor service: the process the Service Control Manager names)" in lines
    assert (
        "threads of the main process, % of one core: panel 15.0, MainThread 0.0, other 10.0"
        in lines
    )
    assert "  last round: 6.1 ms (Cpu 6.1)" in lines
    assert lines[-1].startswith("this measurement took")
    assert "8-Core Processor" not in text and "/amdcpu" not in text  # types, no names or ids
    assert text.endswith("\n")


# -- only while asked ------------------------------------------------------------------------------


def test_the_thread_samples_while_asked_and_ends_when_nobody_asks():
    machine = Machine()
    sampler = machine.sampler(clock=time.monotonic, window_s=0.02, idle_s=0.3)
    try:
        assert sampler.report() == {"ready": False, "cpus": 8, "window_s": 0.02}
        assert wait_for(lambda: sampler.report()["ready"], 5)
        thread = sampler._thread
        assert thread is not None and thread.name == "load-report"
        assert wait_for(lambda: sampler._thread is None, 5)  # nobody asked for 0.3 s
        assert wait_for(lambda: not thread.is_alive(), 2)
        time.sleep(0.35)
        assert not sampler.report()["ready"]  # the last report is old: it starts anew
        assert sampler._thread is not None
        assert sampler.text(wait=5).startswith("Libre Panel load over")
    finally:
        sampler.close()
    assert sampler._thread is None


def test_a_request_never_waits_for_a_sample():
    machine = Machine()
    slow = threading.Event()

    def threads():
        slow.wait(2)  # a sample that hangs on the system
        return {}

    machine.me.threads = threads
    sampler = machine.sampler(clock=time.monotonic, window_s=0.01)
    try:
        sampler.report()
        started = time.monotonic()
        for _ in range(5):
            assert not sampler.report()["ready"]
        assert time.monotonic() - started < 0.5
        assert sampler.text() == ""  # nothing to wait for: none in
    finally:
        slow.set()
        sampler.close()


def test_a_process_that_cannot_be_measured_leaves_out_only_itself():
    machine = Machine()
    sampler = machine.sampler()

    def refused():
        raise psutil.AccessDenied(900)

    machine.service.cpu_times = refused
    sampler.sample()
    machine.run(2.0, me=0.2)
    report = sampler.sample()
    assert ("service", None, "") not in rows(report) and rows(report)[("main", None, "")]
    assert machine.looked == 1  # not asked again at every sample, only every LOOK_S


def test_the_pages_get_the_status_of_libre_hardware_monitor_without_its_times():
    """The sources' states (Settings → Sensors, the console's pages) stay as they were: the round
    times are for the load only."""
    from types import SimpleNamespace

    from libre_panel.plugins.pages import _status_of

    status = LhmStatus(
        "service", True, True, "2.0.1", timing=Timing(RoundTime((CPU,))), version="0.7.1"
    )
    said = _status_of(SimpleNamespace(status=lambda: status))
    assert said == {"mode": "service", "elevated": True, "ring0": True, "pawnio": "2.0.1",
                    "error": "", "service": ""}  # fmt: skip

"""The sensor service as a .NET program of its own (sensorhost/, C#) against the Python one it is
to replace (sensorservice/): started by hand (--console) and as a Windows service, asked over the
real pipe with Libre Panel's own client, it answers as the Python service does. The checks of a
request word for word, the same sensors of the same hardware (names, types, units), the same
rights on the pipe, the same limits; and what each of the two holds.

Windows only, where CI built the program (LIBRE_PANEL_SENSOR_HOST: ci.yml, job sensor-host), as
an administrator, as the runner is one: LibreHardwareMonitor and a service need it."""

from __future__ import annotations

import ctypes
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from libre_panel import __version__
from libre_panel.sensorservice import PIPE_NAME, SERVICE_NAME, core, protocol

HOST = os.environ.get("LIBRE_PANEL_SENSOR_HOST", "")
MB = 1024 * 1024

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows' named pipes and services"),
    pytest.mark.skipif(not HOST, reason="the sensor service's .NET program is not built here"),
]

# The Python service as winservice.run makes it, with the libraries the .NET program was built
# against (the Python one finds them only in its own build): to compare what each holds.
PYTHON_SERVICE = """
import functools, sys, threading
from pathlib import Path
from libre_panel.sensors import lhm_inprocess
from libre_panel.sensorservice import core, winpipe, winservice
opener = functools.partial(winservice.open_everything, Path(sys.argv[1]))
reader = core.Reader(opener, pawnio=lhm_inprocess.pawnio_version, trim=winservice.give_back)
reader.start()
winpipe.PipeServer(core.Handler(reader, memory=winservice.own_memory)).start()
threading.Event().wait()
"""


@pytest.fixture(autouse=True)
def administrator():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("LibreHardwareMonitor and a service need an administrator")


def connect(seconds: float = 30.0):
    """Libre Panel's connection to the pipe, once something answers under its name."""
    from libre_panel.sensorservice import winpipe

    deadline = time.monotonic() + seconds
    while True:
        try:
            return winpipe.connect(PIPE_NAME, timeout=2.0)
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def ask_until(link, done, seconds: float = 90.0, hardware=(), everything: bool = True):
    """The readings, asked twice a second (below the limit) until ``done(readings)``."""
    deadline = time.monotonic() + seconds
    while True:
        readings = protocol.parse_readings(
            link.request(protocol.read_request(hardware, everything), 5)
        )
        if done(readings) or time.monotonic() > deadline:
            return readings
        time.sleep(0.5)


def fresh(readings) -> bool:
    timing = readings.timing
    return (
        readings.state != "starting"
        and readings.age is not None
        and readings.age <= 2.0
        and timing is not None
        and timing.full is not None
    )


def raw(link, body: bytes, seconds: float = 5.0) -> dict:
    """``body`` as a message as it is, and the answer."""
    link.stream.write(struct.pack("<I", len(body)) + body, seconds)
    return protocol.read_message(lambda size: link.stream.read(size, seconds), protocol.MAX_ANSWER)


def pipe_dacl(name: str) -> str:
    """The DACL of a pipe in SDDL, from an instance opened only to read it."""
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        w.LPVOID,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    kernel.CreateFileW.restype = w.HANDLE
    advapi.GetSecurityInfo.argtypes = (
        [w.HANDLE, ctypes.c_int, w.DWORD] + [w.LPVOID] * 4 + [ctypes.POINTER(w.LPVOID)]
    )
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), w.LPVOID,
    ]  # fmt: skip
    dacl_only = 0x4  # DACL_SECURITY_INFORMATION
    handle = kernel.CreateFileW(name, 0x20000 | 0x80, 0, None, 3, 0, None)  # READ_CONTROL
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = w.LPVOID()
        status = advapi.GetSecurityInfo(
            handle, 1, dacl_only, None, None, None, None, ctypes.byref(descriptor)
        )  # SE_FILE_OBJECT
        if status:
            raise OSError(status, "GetSecurityInfo")
        text = w.LPWSTR()
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, dacl_only, ctypes.byref(text), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return text.value
    finally:
        kernel.CloseHandle(handle)


def started(command: list[str], out: Path) -> subprocess.Popen:
    with open(out, "w", encoding="utf-8") as sink:
        return subprocess.Popen(command, stdout=sink, stderr=subprocess.STDOUT)


def ended(process: subprocess.Popen, out: Path) -> None:
    process.kill()
    process.wait(15)
    print(out.read_text(encoding="utf-8", errors="replace"))


# What the Python service turns away, and how it says it (core.Handler.answer). Python takes
# 1.0 and true for 1, as it compares them.
REQUESTS = [
    {"v": 2, "op": "hello"},
    {"op": "hello"},
    {"v": "1", "op": "hello"},
    {"v": "it's", "op": "hello"},
    {"v": None, "op": "hello"},
    {"v": False, "op": "hello"},
    {"v": True, "op": "hello"},
    {"v": 1.0, "op": "hello"},
    {"v": 2.5, "op": "hello"},
    {"v": 1e16, "op": "hello"},
    {"v": 1e-05, "op": "hello"},
    {"v": 0.0001, "op": "hello"},
    {"v": -0.0, "op": "hello"},
    {"v": 100.0, "op": "hello"},
    {"v": 10**30, "op": "hello"},
    {"v": [1, "a\n", None], "op": "hello"},
    {"v": {"a": {"b": True}}, "op": "hello"},
    {"v": "\u00e4\u2028\x01", "op": "hello"},
    {"v": 1, "op": "bye"},
    {"v": 1, "op": 1},
    {"v": 1},
    {"v": 1, "op": "hello", "more": "is no matter"},
    {"v": 1, "op": "read", "hardware": "/amdcpu/0"},
    {"v": 1, "op": "read", "hardware": None},
    {"v": 1, "op": "read", "hardware": ["/a"] * 65},
    {"v": 1, "op": "read", "hardware": ["amdcpu/0"]},
    {"v": 1, "op": "read", "hardware": ["/"]},
    {"v": 1, "op": "read", "hardware": ["/a b"]},
    {"v": 1, "op": "read", "hardware": ["/" + "a" * 200]},
    {"v": 1, "op": "read", "hardware": ["/\u00e4"]},
    {"v": 1, "op": "read", "hardware": [1]},
    {"v": 1, "op": "read", "all": 1},
    {"v": 1, "op": "read", "all": None},
]
NOT_OBJECTS = [[1, 2], "hello", 7, None]
NOT_JSON = [b"{", b"\xff\xfe", b"NaN", b'{"v": NaN}', b'{"v": 1} x', b'\xef\xbb\xbf{"v": 1}']


@pytest.fixture(scope="class")
def host(tmp_path_factory):
    """The .NET program started by hand, for the tests of a class."""
    out = tmp_path_factory.mktemp("host") / "console.txt"
    process = started([HOST, "--console"], out)
    try:
        connect().close()
        yield process
    finally:
        ended(process, out)


class TestOnThePipe:
    """The .NET program started by hand, asked as Libre Panel asks it."""

    def test_it_says_hello_with_the_apps_version(self, host):
        link = connect()
        try:
            assert link.server_pid() == host.pid
            hello = protocol.parse_hello(link.request(protocol.hello_request(), 5))
        finally:
            link.close()
        assert hello == protocol.Hello(__version__, protocol.PROTOCOL)

    def test_it_reads_librehardwaremonitor_and_says_what_it_holds(self, host):
        link = connect()
        try:
            readings = ask_until(link, fresh)
        finally:
            link.close()
        assert readings.state == "running", readings.error
        assert fresh(readings) and readings.sensors
        memory = readings.memory
        assert memory is not None and memory.private and memory.working_set and memory.managed
        parts = readings.timing.full.parts
        assert parts and all(part.seconds >= 0 for part in parts)
        print(
            f"{len(readings.sensors)} sensors of {len(parts)} parts; it holds "
            f"{memory.private / MB:.1f} MB (working set {memory.working_set / MB:.1f} MB, "
            f".NET's heap {memory.managed / MB:.1f} MB)"
        )

    def test_the_same_sensors_as_the_python_service(self, host):
        from libre_panel.sensors import lhm_inprocess
        from libre_panel.sensorservice import winservice

        folder = Path(HOST).parent / "lhm"
        reader = core.Reader(
            lambda: winservice.open_everything(folder), pawnio=lhm_inprocess.pawnio_version
        )
        reader.start()
        try:
            deadline = time.monotonic() + 90
            while reader.snapshot().state == "starting" and time.monotonic() < deadline:
                time.sleep(0.2)
            reader.ask((), everything=True)  # a round of all hardware, as below
            time.sleep(3)
            python = reader.snapshot()
        finally:
            reader.close()
        assert python.state == "running", python.error
        link = connect()
        try:
            link.request(protocol.read_request((), True), 5)
            time.sleep(3)
            ours = protocol.parse_readings(link.request(protocol.read_request((), True), 5))
        finally:
            link.close()

        def described(sensors):
            return {
                (s.sensor_id, s.hardware_id, s.hardware_name, s.sensor_type, s.label, s.unit)
                for s in sensors
            }

        mine, theirs = described(ours.sensors), described(python.sensors)
        assert mine == theirs, (sorted(mine - theirs), sorted(theirs - mine))
        assert (ours.ring0, ours.pawnio) == (python.ring0, python.pawnio)
        print(f"the same {len(mine)} sensors as the Python service")

    def test_it_checks_a_request_as_the_python_service_does(self, host):
        python = core.Handler(None, version=__version__)
        link = connect()
        try:
            for message in REQUESTS:
                body = json.dumps(message).encode()
                assert raw(link, body) == python.answer(message), message
                time.sleep(0.25)  # (within the limit of requests)
            for value in NOT_OBJECTS:
                answer = raw(link, json.dumps(value).encode())
                assert answer == protocol.error_answer("not a JSON object"), value
                time.sleep(0.25)
            for body in NOT_JSON:
                answer = raw(link, body)
                assert answer["ok"] is False and answer["error"].startswith("not JSON: "), body
                time.sleep(0.25)
            # and the connection goes on after all of them
            assert protocol.parse_hello(link.request(protocol.hello_request(), 5))
        finally:
            link.close()

    @pytest.mark.parametrize("length", [0, protocol.MAX_REQUEST + 1])
    def test_a_length_out_of_bounds_ends_the_connection(self, host, length):
        link = connect()
        try:
            link.stream.write(struct.pack("<I", length), 5)
            with pytest.raises(core.ConnectionEnded):
                link.stream.read(4, 5)
        finally:
            link.close()

    def test_a_message_that_stops_halfway_is_let_go(self, host):
        link = connect()
        try:
            link.stream.write(struct.pack("<I", 10) + b'{"v":', 5)
            begun = time.monotonic()
            with pytest.raises(core.ConnectionEnded):
                link.stream.read(4, 20)
            took = time.monotonic() - begun
        finally:
            link.close()
        assert core.MESSAGE_SECONDS - 1 < took < core.MESSAGE_SECONDS + 5

    def test_requests_beyond_the_first_ones_wait_their_turn(self, host):
        link = connect()
        try:
            begun = time.monotonic()
            for _ in range(core.BURST + 4):
                protocol.parse_hello(link.request(protocol.hello_request(), 5))
            took = time.monotonic() - begun
        finally:
            link.close()
        assert took >= 0.9 * 4 / core.RATE

    def test_the_pipe_has_the_rights_the_python_service_gives_it(self, host):
        from libre_panel.sensorservice import winpipe

        ours = pipe_dacl(PIPE_NAME)
        name = rf"\\.\pipe\LibrePanelSensorsTest-{os.getpid()}"
        server = winpipe.PipeServer(core.Handler(None), name=name, instances=1)
        server.start()
        try:
            theirs = pipe_dacl(name)
        finally:
            server.stop()
        print("the pipe:", ours)
        assert ours == theirs and ours.startswith("D:P")

    def test_a_second_one_does_not_join_the_name(self, host, tmp_path):
        out = tmp_path / "second.txt"
        second = started([HOST, "--console"], out)
        try:
            deadline = time.monotonic() + 60
            while "the pipe cannot be made" not in out.read_text(
                encoding="utf-8", errors="replace"
            ):
                assert second.poll() is None and time.monotonic() < deadline
                time.sleep(0.2)
            link = connect()
            try:
                assert link.server_pid() == host.pid
            finally:
                link.close()
        finally:
            ended(second, out)


def sc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sc.exe", *args], capture_output=True, text=True, timeout=60)


def service_state(seconds: float, wanted: int) -> int | None:
    from libre_panel.sensorservice import winapi

    deadline = time.monotonic() + seconds
    while True:
        status = winapi.service_status(SERVICE_NAME)
        state = None if status is None else status[0]
        if state == wanted or time.monotonic() > deadline:
            return state
        time.sleep(0.25)


def test_as_a_windows_service_libre_panel_reads_through_it():
    from libre_panel.sensors import lhm_service
    from libre_panel.sensorservice import winapi

    if winapi.service_status(SERVICE_NAME) is not None:
        pytest.skip("a sensor service is installed here already")
    made = sc("create", SERVICE_NAME, "binPath=", f'"{HOST}"', "start=", "demand", "obj=",
              "LocalSystem")  # fmt: skip
    assert made.returncode == 0, made.stdout + made.stderr
    try:
        assert sc("start", SERVICE_NAME).returncode == 0
        assert service_state(60, winapi.SERVICE_RUNNING) == winapi.SERVICE_RUNNING
        link = connect()
        try:  # LibreHardwareMonitor open (Libre Panel's check waits 15 s for that)
            assert fresh(ask_until(link, fresh))
        finally:
            link.close()
        worked, lines = lhm_service.check()
        print("\n".join(lines))
        assert worked and any("read through the sensor service" in line for line in lines)
        assert sc("stop", SERVICE_NAME).returncode == 0
        assert service_state(30, winapi.SERVICE_STOPPED) == winapi.SERVICE_STOPPED
        said = sc("queryex", SERVICE_NAME).stdout
        print(said)
        assert " 0  (0x0)" in said.split("WIN32_EXIT_CODE")[1].splitlines()[0]  # stopped well
    finally:
        sc("stop", SERVICE_NAME)
        service_state(30, winapi.SERVICE_STOPPED)
        sc("delete", SERVICE_NAME)


def test_what_each_holds(tmp_path):
    """Each alone, by hand, asked for all its hardware once a second for half a minute (as
    Libre Panel asks for what its themes read): what it holds then, as it says itself."""

    def held(command: list[str], out: Path):
        process = started(command, out)
        try:
            link = connect(60)
            try:
                readings = ask_until(link, fresh)
                assert readings.state == "running", readings.error
                hardware = sorted({s.hardware_id for s in readings.sensors})[
                    : protocol.MAX_HARDWARE
                ]
                for _ in range(30):
                    readings = protocol.parse_readings(
                        link.request(protocol.read_request(hardware), 5)
                    )
                    time.sleep(1)
            finally:
                link.close()
        finally:
            ended(process, out)
        assert readings.memory is not None and readings.memory.private
        return readings

    folder = str(Path(HOST).parent / "lhm")
    ours = held([HOST, "--console"], tmp_path / "host.txt")
    python = held([sys.executable, "-c", PYTHON_SERVICE, folder], tmp_path / "python.txt")
    for name, readings in ((".NET program", ours), ("Python service", python)):
        memory = readings.memory
        print(
            f"{name}: {memory.private / MB:.1f} MB private working set, working set "
            f"{memory.working_set / MB:.1f} MB, .NET's heap {memory.managed / MB:.1f} MB, "
            f"{len(readings.sensors)} sensors"
        )
    assert ours.memory.private < python.memory.private

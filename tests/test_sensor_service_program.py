"""The sensor service (sensorhost/, the .NET program) held to what it answered where the Python
service it replaced answered the same (tests/golden/given/win32/sensor_service.json, written on
CI while the Python service was there): started by hand (--console) and as a Windows service,
asked over its named pipe by a client of this file's own, as the protocol says (a length of four
bytes, little-endian, then a JSON object). Its checks of a request word for word, the rights on its
pipe, its limits (the length of a request, a message that stops halfway, requests that come
too fast), one program at the name, LibreHardwareMonitor read in it, and Dell Command | Monitor
asked in it for a client. What the Rust app's client makes of it: rust/sensors service.rs's
and dell.rs's tests.

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
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
KEPT = ROOT / "tests" / "golden" / "given" / "win32" / "sensor_service.json"
HOST = os.environ.get("LIBRE_PANEL_SENSOR_HOST", "")
# what the service is held to (the Python service's sensorservice/protocol.py and core.py)
PIPE_NAME = r"\\.\pipe\LibrePanelSensors"
SERVICE_NAME = "LibrePanelSensors"
PROTOCOL = 1
MAX_REQUEST = 4096  # the longest request
MAX_ANSWER = 4 * 1024 * 1024
RATE = 4.0  # requests a second a connection may make, after
BURST = 8  # this many at once
MESSAGE_SECONDS = 5.0  # a message that has begun must be through within this long
SERVICE_RUNNING, SERVICE_STOPPED = 4, 1

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows' named pipes and services"),
    pytest.mark.skipif(not HOST, reason="the sensor service's .NET program is not built here"),
]


@pytest.fixture(autouse=True)
def administrator() -> None:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("LibreHardwareMonitor and a service need an administrator")


def version() -> str:
    """The version the program says of itself: the project's (CI builds it with that)."""
    with open(ROOT / "pyproject.toml", "rb") as file:
        return tomllib.load(file)["project"]["version"]


class Link:
    """A connection to the pipe: a request in, an answer out."""

    def __init__(self, file: Any) -> None:
        self.file = file

    def write(self, data: bytes) -> None:
        self.file.write(data)

    def read(self, size: int) -> bytes:
        """``size`` bytes; fewer where the service ended the connection."""
        data = b""
        while len(data) < size:
            try:
                piece = self.file.read(size - len(data))
            except OSError:  # (the pipe was broken by the other side)
                break
            if not piece:
                break
            data += piece
        return data

    def answer(self) -> dict[str, Any] | None:
        head = self.read(4)
        if len(head) < 4:
            return None
        (length,) = struct.unpack("<I", head)
        assert 0 < length <= MAX_ANSWER, length
        body = self.read(length)
        assert len(body) == length
        return json.loads(body.decode("utf-8"))

    def send(self, body: bytes) -> dict[str, Any] | None:
        self.write(struct.pack("<I", len(body)) + body)
        return self.answer()

    def ask(self, message: dict[str, Any]) -> dict[str, Any] | None:
        return self.send(json.dumps(message).encode("utf-8"))

    def server_pid(self) -> int:
        import msvcrt
        from ctypes import wintypes as w

        pid = w.ULONG()
        handle = msvcrt.get_osfhandle(self.file.fileno())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.GetNamedPipeServerProcessId(w.HANDLE(handle), ctypes.byref(pid)):
            raise ctypes.WinError(ctypes.get_last_error())
        return pid.value

    def close(self) -> None:
        self.file.close()


def connect(seconds: float = 30.0) -> Link:
    """A connection to the pipe, once something answers under its name."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return Link(open(PIPE_NAME, "r+b", buffering=0))  # noqa: SIM115
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def read_request(everything: bool = True) -> dict[str, Any]:
    return {"v": PROTOCOL, "op": "read", "hardware": [], "all": everything}


def fresh(answer: dict[str, Any] | None) -> bool:
    timing = (answer or {}).get("timing") or {}
    age = (answer or {}).get("age")
    return (
        bool(answer)
        and answer.get("state") == "running"  # type: ignore[union-attr]
        and age is not None
        and age <= 2.0
        and timing.get("full") is not None
    )


def ask_until(link: Link, done: Any, seconds: float = 90.0) -> dict[str, Any] | None:
    """The readings, asked twice a second (below the limit) until ``done(answer)``."""
    deadline = time.monotonic() + seconds
    while True:
        answer = link.ask(read_request())
        if done(answer) or time.monotonic() > deadline:
            return answer
        time.sleep(0.5)


def pipe_dacl(name: str) -> str:
    """The DACL of a pipe in SDDL, from an instance opened only to read it."""
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD,
                                   w.HANDLE]  # fmt: skip
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


@pytest.fixture(scope="module")
def kept() -> dict[str, Any]:
    return json.loads(KEPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory):
    """The program started by hand, for the tests of the module that ask it on its pipe."""
    out = tmp_path_factory.mktemp("host") / "console.txt"
    process = started([HOST, "--console"], out)
    try:
        connect().close()
        yield process
    finally:
        ended(process, out)


def test_it_says_hello_with_the_apps_version(host: subprocess.Popen) -> None:
    link = connect()
    try:
        assert link.server_pid() == host.pid
        hello = link.ask({"v": PROTOCOL, "op": "hello"})
    finally:
        link.close()
    assert hello == {"v": 1, "ok": True, "service": version(), "protocol": PROTOCOL}


def test_it_checks_a_request_as_the_python_service_did(host: subprocess.Popen, kept) -> None:
    """Each request the Python service turned away (or took), with the answer it gave, word
    for word; the service's version where it said its own."""

    def ours(answer: dict[str, Any]) -> dict[str, Any]:
        return {**answer, "service": version()} if "service" in answer else answer

    link = connect()
    try:
        for message, answer in kept["requests"]:
            assert link.ask(message) == ours(answer), message
            time.sleep(0.25)  # (within the limit of requests)
        for value, answer in kept["not_objects"]:
            assert link.send(json.dumps(value).encode("utf-8")) == answer, value
            time.sleep(0.25)
        for body, answer in kept["not_json"]:
            assert link.send(body.encode("latin-1")) == answer, body
            time.sleep(0.25)
        # and the connection goes on after all of them
        assert link.ask({"v": PROTOCOL, "op": "hello"})["ok"] is True
    finally:
        link.close()


@pytest.mark.parametrize("length", [0, MAX_REQUEST + 1])
def test_a_length_out_of_bounds_ends_the_connection(host: subprocess.Popen, length: int) -> None:
    link = connect()
    try:
        link.write(struct.pack("<I", length))
        assert link.read(4) == b""  # (ended, nothing said)
    finally:
        link.close()


def test_a_message_that_stops_halfway_is_let_go(host: subprocess.Popen) -> None:
    link = connect()
    try:
        link.write(struct.pack("<I", 10) + b'{"v":')
        begun = time.monotonic()
        assert link.read(4) == b""
        took = time.monotonic() - begun
    finally:
        link.close()
    assert MESSAGE_SECONDS - 1 < took < MESSAGE_SECONDS + 5


def test_requests_beyond_the_first_ones_wait_their_turn(host: subprocess.Popen) -> None:
    link = connect()
    try:
        begun = time.monotonic()
        for _ in range(BURST + 4):
            assert link.ask({"v": PROTOCOL, "op": "hello"})["ok"] is True
        took = time.monotonic() - begun
    finally:
        link.close()
    assert took >= 0.9 * 4 / RATE


def test_the_pipe_has_the_rights_the_python_service_gave_it(host: subprocess.Popen, kept) -> None:
    """People signed in may read; the system and administrators may do all; the network's
    users nothing (the Python service's winpipe)."""
    ours = pipe_dacl(PIPE_NAME)
    print("the pipe:", ours)
    assert ours == kept["pipe"]["dacl"] and ours.startswith("D:P")


def test_a_second_one_does_not_join_the_name(host: subprocess.Popen, tmp_path: Path) -> None:
    out = tmp_path / "second.txt"
    second = started([HOST, "--console"], out)
    try:
        deadline = time.monotonic() + 60
        while "the pipe cannot be made" not in out.read_text(encoding="utf-8", errors="replace"):
            assert second.poll() is None and time.monotonic() < deadline
            time.sleep(0.2)
        link = connect()
        try:
            assert link.server_pid() == host.pid
        finally:
            link.close()
    finally:
        ended(second, out)


def test_it_reads_librehardwaremonitor_and_says_what_it_holds(host: subprocess.Popen) -> None:
    link = connect()
    try:
        answer = ask_until(link, fresh)
    finally:
        link.close()
    assert answer is not None and answer["ok"] is True, answer
    assert answer["state"] == "running", answer.get("error")
    assert fresh(answer) and answer["sensors"]
    memory = answer["memory"]
    assert memory["private"] and memory["working_set"] and memory["managed"]
    parts = answer["timing"]["full"]["parts"]
    assert parts and all(part[3] >= 0 for part in parts)
    print(f"{len(answer['sensors'])} sensors of {len(parts)} parts; it holds "
          f"{memory['private'] / 2**20:.1f} MB")  # fmt: skip


DELL_FIELDS = 6  # ElementName, DeviceID, SensorType, BaseUnits, UnitModifier, CurrentReading


def test_it_asks_dell_command_monitor_for_a_client(host: subprocess.Popen) -> None:
    """Dell's fans as Dell Command | Monitor reports them, which it may tell administrators
    only, asked by the service for a client (rust/sensors dell.rs asks so where it is turned
    away): their rows, or why there are none, and the connection goes on. CI's computers have
    no Dell Command | Monitor: its namespace is not there."""
    link = connect()
    try:
        answer = link.ask({"v": PROTOCOL, "op": "dell"})
        time.sleep(0.25)
        assert link.ask({"v": PROTOCOL, "op": "hello"})["ok"] is True
    finally:
        link.close()
    assert answer is not None and answer["v"] == PROTOCOL, answer
    if answer["ok"]:
        assert set(answer) == {"v", "ok", "rows"}, answer
        assert all(len(row) == DELL_FIELDS for row in answer["rows"]), answer
    else:
        assert set(answer) == {"v", "ok", "error"}, answer
        assert answer["error"].startswith("Dell Command | Monitor: "), answer
    print(answer)


def sc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sc.exe", *args], capture_output=True, text=True, timeout=60)


def service_state(seconds: float, wanted: int) -> int | None:
    """The service's state as sc.exe tells it, waited for until it is ``wanted``."""
    deadline = time.monotonic() + seconds
    while True:
        said = sc("query", SERVICE_NAME).stdout
        state = None
        for line in said.splitlines():
            if "STATE" in line and ":" in line:
                state = int(line.split(":", 1)[1].split()[0])
        if state == wanted or time.monotonic() > deadline:
            return state
        time.sleep(0.25)


def test_as_a_windows_service_it_is_read_on_its_pipe(host: subprocess.Popen) -> None:
    """Installed as Libre Panel's setup installs it (LocalSystem), started and stopped by
    Windows, read on the pipe; its exit code 0. (The program started by hand is ended for it:
    one program at the name.)"""
    if sc("query", SERVICE_NAME).returncode == 0:
        pytest.skip("a sensor service is installed here already")
    host.kill()
    host.wait(15)
    made = sc("create", SERVICE_NAME, "binPath=", f'"{HOST}"', "start=", "demand", "obj=",
              "LocalSystem")  # fmt: skip
    assert made.returncode == 0, made.stdout + made.stderr
    try:
        assert sc("start", SERVICE_NAME).returncode == 0
        assert service_state(60, SERVICE_RUNNING) == SERVICE_RUNNING
        link = connect()
        try:
            assert fresh(ask_until(link, fresh))
        finally:
            link.close()
        program = os.environ.get("LIBRE_PANEL_PROGRAM")  # (the Rust app, where CI has it)
        if program:
            done = subprocess.run([program, "sensors", "--check-service"], capture_output=True,
                                  text=True, timeout=120)  # fmt: skip
            print(done.stdout, done.stderr)
            assert done.returncode == 0 and "read through the sensor service" in done.stdout
        assert sc("stop", SERVICE_NAME).returncode == 0
        assert service_state(30, SERVICE_STOPPED) == SERVICE_STOPPED
        said = sc("queryex", SERVICE_NAME).stdout
        print(said)
        assert " 0  (0x0)" in said.split("WIN32_EXIT_CODE")[1].splitlines()[0]  # stopped well
    finally:
        sc("stop", SERVICE_NAME)
        service_state(30, SERVICE_STOPPED)
        sc("delete", SERVICE_NAME)

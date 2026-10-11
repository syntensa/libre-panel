"""The sensor service without Windows: its protocol, its reader of LibreHardwareMonitor (on the
fake .NET objects of fake_lhm), its answers, one connection, who may open its pipe, and the
service's body. The pipe and the service host themselves are Windows' (test_installer.py on CI).
"""

import json
import logging
import random
import struct
import threading

import pytest
from fake_lhm import FakeHardware, FakeSensor, ring0_off, small_computer
from fake_sensors import wait_for

from libre_panel import __version__
from libre_panel.sensors.librehardwaremonitor import LhmSensor
from libre_panel.sensorservice import core, protocol, winapi, winpipe, winservice
from libre_panel.sensorservice.protocol import ProtocolError, ServiceError

CPU, GPU, CHIP = "/amdcpu/0", "/gpu-nvidia/0", "/lpc/nct0000/0"


def frame(message):
    body = json.dumps(message).encode()
    return struct.pack("<I", len(body)) + body


def frames(data):
    """The messages in what was written, one after the other."""
    out, rest = [], bytes(data)
    while rest:
        (length,) = struct.unpack("<I", rest[:4])
        out.append(json.loads(rest[4 : 4 + length]))
        rest = rest[4 + length :]
    return out


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


# -- the protocol ----------------------------------------------------------------------------------


def test_a_message_goes_through_whole():
    message = {"v": 1, "op": "read", "hardware": [CPU], "all": False, "ü": "°C"}
    data = protocol.encode(message, protocol.MAX_REQUEST)
    assert struct.unpack("<I", data[:4])[0] == len(data) - 4
    rest = iter([data[:4], data[4:]])
    assert protocol.read_message(lambda size: next(rest), protocol.MAX_REQUEST) == message


@pytest.mark.parametrize("length", [0, protocol.MAX_REQUEST + 1, 2**32 - 1])
def test_a_length_out_of_bounds_is_refused(length):
    with pytest.raises(ProtocolError):
        protocol.body_length(struct.pack("<I", length), protocol.MAX_REQUEST)


def test_a_cut_length_is_refused():
    with pytest.raises(ProtocolError, match="cut short"):
        protocol.body_length(b"\x01\x00", protocol.MAX_REQUEST)


@pytest.mark.parametrize(
    "body",
    [b"", b"\xff\xfe", b"[1, 2]", b'"text"', b"{", b"null", b'{"a": NaN}', b"[" * 5000],
)
def test_only_a_json_object_is_a_message(body):
    with pytest.raises(ProtocolError):
        protocol.decode(body)


def test_what_cannot_be_said_or_is_too_long_is_not_sent():
    with pytest.raises(ProtocolError, match="cannot be sent"):
        protocol.encode({"value": float("nan")}, protocol.MAX_ANSWER)
    with pytest.raises(ProtocolError, match="more than"):
        protocol.encode({"x": "y" * protocol.MAX_REQUEST}, protocol.MAX_REQUEST)


def test_the_requests_there_are():
    assert protocol.parse_request(protocol.hello_request()) == protocol.Request("hello")
    request = protocol.parse_request(protocol.read_request([CPU, GPU, CPU], everything=True))
    assert request == protocol.Request("read", (CPU, GPU), True)
    assert protocol.parse_request({"v": 1, "op": "read"}) == protocol.Request("read")
    # never more than the protocol allows, however many the client knows
    ids = [f"/hdd/{n}" for n in range(200)]
    assert len(protocol.read_request(ids)["hardware"]) == protocol.MAX_HARDWARE


@pytest.mark.parametrize(
    "message",
    [
        {"op": "hello"},
        {"v": 2, "op": "hello"},
        {"v": "1", "op": "hello"},
        {"v": 1, "op": "write"},
        {"v": 1, "op": "set_fan", "hardware": [CPU]},
        {"v": 1},
        {"v": 1, "op": "read", "hardware": "/amdcpu/0"},
        {"v": 1, "op": "read", "hardware": [5]},
        {"v": 1, "op": "read", "hardware": ["amdcpu/0"]},
        {"v": 1, "op": "read", "hardware": ["/with space"]},
        {"v": 1, "op": "read", "hardware": ["/" + "x" * 300]},
        {"v": 1, "op": "read", "hardware": ["/ü"]},
        {"v": 1, "op": "read", "hardware": ["/a"] * (protocol.MAX_HARDWARE + 1)},
        {"v": 1, "op": "read", "all": "yes"},
    ],
)
def test_anything_else_is_refused(message):
    with pytest.raises(ProtocolError):
        protocol.parse_request(message)


SENSOR = LhmSensor(
    "/amdcpu/0/temperature/2",
    CPU,
    "8-Core Processor",
    "Temperature",
    "Core (Tctl/Tdie)",
    55.5,
    "°C",
)


def test_the_readings_come_back_as_they_were_sent():
    answer = protocol.read_answer(
        [SENSOR], state="running", age=0.25, ring0=True, pawnio="2.0.1", error=""
    )
    sent = json.loads(protocol.encode(answer, protocol.MAX_ANSWER)[4:])
    readings = protocol.parse_readings(sent)
    assert readings.sensors == (SENSOR,) and readings.ring0 is True
    assert (readings.state, readings.age, readings.pawnio) == ("running", 0.25, "2.0.1")
    hello = protocol.parse_hello(protocol.hello_answer("0.5.0"))
    assert (hello.service, hello.protocol) == ("0.5.0", protocol.PROTOCOL)


def test_a_service_that_says_no_is_heard():
    with pytest.raises(ServiceError, match="unknown"):
        protocol.parse_readings(protocol.error_answer("unknown request"))


def good_answer(**changes):
    answer = protocol.read_answer([SENSOR], state="running", age=0.1, ring0=None, pawnio=None)
    answer.update(changes)
    return answer


@pytest.mark.parametrize(
    "answer",
    [
        good_answer(v=2),
        good_answer(ok="yes"),
        good_answer(state="happy"),
        good_answer(age="soon"),
        good_answer(age=float("inf")),
        good_answer(ring0="yes"),
        good_answer(pawnio=2),
        good_answer(error=None),
        good_answer(sensors={}),
        good_answer(sensors=[[1] * 7]),
        good_answer(sensors=[["/a/1", "/a", "A", "Load", "x", 1.0]]),
        good_answer(sensors=[["/a/1", "/a", "A", "Load", "x", True, "%"]]),
        good_answer(sensors=[["/a/1", "/a", "A", "Load", "x", 10**400, "%"]]),
        good_answer(sensors=[["", "/a", "A", "Load", "x", 1.0, "%"]]),
        good_answer(sensors=[["/a/1", "/a", "A" * 1000, "Load", "x", 1.0, "%"]]),
        good_answer(sensors=[["/a/1", "/a", "A\x00", "Load", "x", 1.0, "%"]]),
        good_answer(sensors=[["/a/1", "/a", "A", "Load", "x", 1.0, "%"]] * 10_001),
    ],
)
def test_an_answer_with_anything_wrong_in_it_is_not_taken(answer):
    with pytest.raises(ProtocolError):
        protocol.parse_readings(answer)


def test_garbage_is_only_ever_a_protocol_error():
    """A little fuzzing: random bytes, and good messages with random damage, are refused
    with ProtocolError (or taken, when the damage happens to leave a message), never with
    anything else."""
    rng = random.Random(4711)
    good = [
        protocol.encode(protocol.hello_request(), protocol.MAX_REQUEST),
        protocol.encode(protocol.read_request([CPU, GPU]), protocol.MAX_REQUEST),
        protocol.encode(good_answer(), protocol.MAX_ANSWER),
    ]
    for _ in range(3000):
        if rng.random() < 0.3:
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64)))
        else:
            data = bytearray(rng.choice(good))
            for _ in range(rng.randrange(1, 4)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            data = bytes(data[: rng.randrange(1, len(data) + 1)])
        rest = bytearray(data)

        def read_exact(size, rest=rest):
            if len(rest) < size:
                raise EOFError
            chunk = bytes(rest[:size])
            del rest[:size]
            return chunk

        try:
            message = protocol.read_message(read_exact, protocol.MAX_ANSWER)
        except (ProtocolError, EOFError):
            continue
        for parse in (protocol.parse_request, protocol.parse_readings, protocol.parse_hello):
            try:
                parse(message)
            except (ProtocolError, ServiceError):
                pass


# -- the reader ------------------------------------------------------------------------------------


def updates(computer):
    """How often each part with sensors was updated (the mainboard itself has none: its
    chip is a part of its own)."""
    return {item.Identifier: item.updates for item in computer.everything() if item.Sensors}


def begun(computer=None, **options):
    """A reader that opened the computer (as its thread does first), without the thread:
    the test makes each round itself."""
    computer = computer or small_computer()
    clock = options.pop("clock", Clock())
    reader = core.Reader(lambda: computer, clock=clock, **options)
    reader._begin(computer)
    return reader, computer, clock


def test_it_reads_everything_once_when_it_opens():
    reader, computer, clock = begun(pawnio=lambda: "2.0.1")
    assert set(updates(computer).values()) == {1}
    snap = reader.snapshot()
    assert snap.state == "running" and snap.ring0 is True and snap.pawnio == "2.0.1"
    assert snap.stamp == clock.now
    names = {sensor.label for sensor in snap.sensors}
    assert {"Core (Tctl/Tdie)", "GPU Core", "Fan #1", "DIMM #1"} <= names


def test_nothing_is_read_while_nobody_asks():
    reader, computer, clock = begun()
    before = updates(computer)
    clock.now += 100
    assert reader.refresh() is False
    assert updates(computer) == before


def test_only_what_was_asked_for_is_read_and_for_a_while_only():
    reader, computer, clock = begun()
    reader.ask([CPU])  # the first after a quiet time: everything, now
    reader.refresh()
    assert set(updates(computer).values()) == {2}
    for _ in range(3):
        clock.now += 1
        reader.ask([CPU, CHIP])
        reader.refresh()
    counts = updates(computer)
    assert counts[CPU] == counts[CHIP] == 5 and counts[GPU] == counts["/ram"] == 2
    clock.now += core.REQUEST_SECONDS + 1  # nobody asked since
    assert reader.refresh() is False
    assert updates(computer) == counts


def test_a_client_that_asks_for_no_hardware_keeps_what_was_read_but_reads_nothing():
    """A theme that shows none of these sensors: nothing is read, and what was read stays good
    for the lists of sensors (as Libre Panel's own source keeps it); after a quiet time, the
    first request reads everything once more, so nothing hours old passes for new."""
    reader, computer, clock = begun()
    reader.ask([])  # the first request: everything, once
    reader.refresh()
    counts = updates(computer)
    for _ in range(5):
        clock.now += 1
        reader.ask([])
        assert reader.refresh() is False
        assert reader.snapshot().stamp == clock.now  # (the readings' age stays small)
    assert updates(computer) == counts
    clock.now += core.REQUEST_SECONDS + 5  # nobody asks: the stamp ages
    reader.refresh()
    assert clock.now - reader.snapshot().stamp > core.REQUEST_SECONDS
    reader.ask([])
    reader.refresh()
    assert set(updates(computer).values()) == {count + 1 for count in counts.values()}


def test_while_anyone_asks_everything_is_read_now_and_then():
    reader, computer, clock = begun(refresh_seconds=30)
    reader.ask([CPU])
    reader.refresh()
    start = updates(computer)[GPU]
    for _ in range(31):
        clock.now += 1
        reader.ask([CPU])
        reader.refresh()
    assert updates(computer)[GPU] == start + 1  # once in thirty seconds
    reader.ask([CPU], everything=True)  # a new client asks for every piece once
    reader.refresh()
    assert updates(computer)[GPU] == start + 2


def test_hardware_nobody_has_is_forgotten():
    reader, computer, clock = begun()
    for number in range(core.MAX_ASKED + 50):
        reader.ask([f"/hdd/{number}"])
        clock.now += 0.001
    assert len(reader._asked) == core.MAX_ASKED
    assert "/hdd/0" not in reader._asked and f"/hdd/{core.MAX_ASKED + 49}" in reader._asked
    clock.now += core.REQUEST_SECONDS + 1
    reader.refresh()
    assert reader._asked == {}


def test_without_rights_the_cpu_is_left_out_not_zero():
    reader, _, _ = begun(ring0_off(small_computer()))
    snap = reader.snapshot()
    assert snap.ring0 is False
    assert not [s for s in snap.sensors if s.hardware_id == CPU and s.sensor_type != "Load"]
    assert any(s.hardware_id == GPU for s in snap.sensors)


def test_a_part_that_fails_is_said_once_and_the_rest_is_read(caplog):
    caplog.set_level(logging.WARNING, logger="libre_panel.sensorservice.core")
    reader, computer, clock = begun()
    gpu = next(item for item in computer.everything() if item.Identifier == GPU)
    gpu.raises = RuntimeError("driver gone")
    for _ in range(3):
        clock.now += 1
        reader.ask([CPU, GPU])
        reader.refresh()
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 1 and "GpuNvidia" in said[0] and "driver gone" in said[0]
    assert updates(computer)[CPU] == 4


def test_a_library_that_does_not_open_leaves_the_reason():
    def opener():
        raise RuntimeError("no .NET Framework")

    reader = core.Reader(opener, poll_seconds=0.05)
    reader.start()
    try:
        assert wait_for(lambda: reader.snapshot().state == "failed")
        assert reader.snapshot().error == "RuntimeError: no .NET Framework"
    finally:
        reader.close()


def test_the_thread_reads_at_once_when_asked_and_closes_the_computer():
    computer = small_computer()
    reader = core.Reader(lambda: computer, poll_seconds=3600)
    reader.start()
    try:
        assert wait_for(lambda: reader.snapshot().state == "running")
        assert updates(computer)[CPU] == 1
        reader.ask([CPU])  # after a quiet time it does not wait for its next turn
        assert wait_for(lambda: updates(computer)[CPU] == 2)
    finally:
        reader.close()
    assert computer.closed == 1


# -- the answers -----------------------------------------------------------------------------------


def test_hello_says_the_version_and_the_protocol():
    reader, _, _ = begun()
    answer = core.Handler(reader).answer(protocol.hello_request())
    assert answer == {"v": 1, "ok": True, "service": __version__, "protocol": 1}


def test_a_read_says_what_was_read_last_and_how_long_ago():
    reader, computer, clock = begun(pawnio=lambda: "2.0.1")
    handler = core.Handler(reader, clock=clock)
    clock.now += 0.75
    answer = handler.answer(protocol.read_request([GPU]))
    readings = protocol.parse_readings(answer)
    assert readings.age == 0.75 and readings.pawnio == "2.0.1" and readings.state == "running"
    assert len(readings.sensors) == len(reader.snapshot().sensors)
    assert GPU in reader._asked  # and it is read from now on
    assert updates(computer)[GPU] == 1  # but not by the request itself


def test_a_request_that_is_not_one_gets_an_error_answer():
    reader, _, _ = begun()
    answer = core.Handler(reader).answer({"v": 1, "op": "set_fan", "speed": 100})
    assert answer["ok"] is False and "hello or read" in answer["error"]


def test_before_the_first_reading_it_says_it_starts():
    reader = core.Reader(lambda: None)
    readings = protocol.parse_readings(core.Handler(reader).answer(protocol.read_request()))
    assert readings.state == "starting" and readings.age is None and readings.sensors == ()


# -- one connection --------------------------------------------------------------------------------


class FakeStream:
    """What a client sends, all at once; the end of it is a client that went."""

    def __init__(self, data=b"", stopping=False):
        self.data = bytearray(data)
        self.written = bytearray()
        self.waits = []
        self.stopping = stopping

    def read(self, size, timeout):
        if len(self.data) < size:
            raise core.ConnectionEnded("gone")
        chunk = bytes(self.data[:size])
        del self.data[:size]
        return chunk

    def write(self, data, timeout):
        self.written += data

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.stopping


def test_a_client_is_answered_until_it_goes():
    reader, _, _ = begun()
    stream = FakeStream(frame(protocol.hello_request()) + frame(protocol.read_request([CPU])))
    assert core.serve(stream, core.Handler(reader)) == 2
    hello, readings = frames(stream.written)
    assert hello["service"] == __version__ and readings["ok"] is True and readings["sensors"]


def test_a_broken_request_is_answered_and_the_connection_goes_on():
    reader, _, _ = begun()
    data = struct.pack("<I", 5) + b"{oops" + frame({"v": 1, "op": "nonsense"})
    stream = FakeStream(data + frame(protocol.hello_request()))
    assert core.serve(stream, core.Handler(reader)) == 3
    broken, unknown, hello = frames(stream.written)
    assert broken["ok"] is False and "not JSON" in broken["error"]
    assert unknown["ok"] is False and hello["ok"] is True


@pytest.mark.parametrize("length", [0, protocol.MAX_REQUEST + 1])
def test_a_length_out_of_bounds_ends_the_connection(length):
    reader, _, _ = begun()
    stream = FakeStream(struct.pack("<I", length) + b"x" * 10 + frame(protocol.hello_request()))
    assert core.serve(stream, core.Handler(reader)) == 0
    assert stream.written == b""


def test_a_client_that_asks_too_often_waits():
    reader, _, _ = begun()
    clock = Clock()
    stream = FakeStream(frame(protocol.hello_request()) * 12)
    core.serve(stream, core.Handler(reader), clock=clock, limiter=core.Limiter(rate=4, burst=8))
    assert len(frames(stream.written)) == 12
    assert stream.waits == pytest.approx([0.25, 0.5, 0.75, 1.0])  # at once: the 9th waits


def test_the_limit_lets_a_client_that_asks_once_a_second_through():
    limiter, clock = core.Limiter(rate=4, burst=8), Clock()
    for _ in range(100):
        assert limiter.delay(clock()) == 0
        clock.now += 1


def test_stopping_ends_a_connection_that_waits():
    reader, _, _ = begun()
    stream = FakeStream(frame(protocol.hello_request()) * 20, stopping=True)
    assert core.serve(stream, core.Handler(reader), clock=Clock()) == 9
    assert len(frames(stream.written)) == 8


def test_an_answer_too_large_is_an_error_answer(monkeypatch):
    reader, _, _ = begun()
    monkeypatch.setattr(core, "MAX_ANSWER", 200)
    stream = FakeStream(frame(protocol.read_request()))
    core.serve(stream, core.Handler(reader))
    (answer,) = frames(stream.written)
    assert answer["ok"] is False and "too large" in answer["error"]


# -- who may open the pipe -------------------------------------------------------------------------


def aces(sddl):
    assert sddl.startswith("D:P(")  # protected: nothing inherited from anywhere
    return [ace.split(";") for ace in sddl[3:].strip("()").split(")(")]


def test_the_pipe_lets_people_signed_in_read_and_the_system_and_administrators_do_all():
    assert winpipe.pipe_sddl() == "D:P(D;;GA;;;NU)(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x120083;;;IU)"
    entries = aces(winpipe.pipe_sddl())
    assert entries[0] == ["D", "", "GA", "", "", "NU"]  # network logons first, and refused
    allowed = {ace[5]: ace[2] for ace in entries if ace[0] == "A"}
    assert allowed == {"SY": "GA", "BA": "GA", "IU": "0x120083"}


def test_people_signed_in_may_not_make_an_instance_or_change_the_rights():
    rights = winpipe.CLIENT_RIGHTS
    file_create_pipe_instance, delete, write_dac, write_owner = 0x4, 0x10000, 0x40000, 0x80000
    for right in (file_create_pipe_instance, delete, write_dac, write_owner, 0x100, 0x10):
        assert not rights & right
    assert rights & winapi.FILE_READ_DATA and rights & winapi.FILE_WRITE_DATA
    # what a client asks for when it opens the pipe is within what it may
    assert winapi.CLIENT_ACCESS & ~rights == 0


def test_more_readers_can_be_named():
    sddl = winpipe.pipe_sddl(("IU", "S-1-5-21-1-2-3-1001"))
    assert sddl.endswith("(A;;0x120083;;;IU)(A;;0x120083;;;S-1-5-21-1-2-3-1001)")


# -- the service's body ----------------------------------------------------------------------------


class FakeReader:
    def __init__(self):
        self.started = self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True


class FakeServer:
    made = []

    def __init__(self, handler, fails=0):
        self.handler, self.fails = handler, fails
        self.starts = 0
        self.stopped = False
        FakeServer.made.append(self)

    def start(self):
        self.starts += 1
        if self.starts <= self.fails:
            raise PermissionError("the name is taken")

    def stop(self):
        self.stopped = True


def run_service(monkeypatch, fails=0):
    monkeypatch.setattr(winservice, "RETRY_SECONDS", 0.01)
    FakeServer.made = []
    reader, stop, running = FakeReader(), threading.Event(), []
    thread = threading.Thread(
        target=winservice.run,
        args=(stop, lambda: running.append(True)),
        kwargs={
            "reader_factory": lambda: reader,
            "server_factory": lambda handler: FakeServer(handler, fails),
        },
    )
    thread.start()
    return reader, stop, running, thread


def test_the_service_reads_answers_and_stops_when_told(monkeypatch):
    reader, stop, running, thread = run_service(monkeypatch)
    assert wait_for(lambda: running)
    (server,) = FakeServer.made
    assert reader.started and server.starts == 1 and isinstance(server.handler, core.Handler)
    stop.set()
    thread.join(10)
    assert not thread.is_alive() and server.stopped and reader.closed and running == [True]


def test_a_taken_pipe_name_is_tried_again_and_windows_does_not_wait_for_it(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="libre_panel.sensorservice.winservice")
    reader, stop, running, thread = run_service(monkeypatch, fails=3)
    assert wait_for(lambda: FakeServer.made and FakeServer.made[0].starts == 4)
    assert running == [True]  # said once, at the first try
    stop.set()
    thread.join(10)
    assert FakeServer.made[0].stopped and reader.closed
    assert "another program holds its name" in caplog.records[0].getMessage()


def test_a_name_that_stays_taken_is_given_up_when_stopping(monkeypatch):
    reader, stop, running, thread = run_service(monkeypatch, fails=10**9)
    assert wait_for(lambda: running)
    stop.set()
    thread.join(10)
    assert not FakeServer.made[0].stopped and reader.closed  # never started, never stopped


def test_every_kind_of_hardware_is_opened_and_a_kind_that_fails_costs_only_itself(
    monkeypatch, caplog
):
    from libre_panel.sensors import lhm_inprocess

    class Computer:
        def __setattr__(self, name, value):
            if name == "IsStorageEnabled":
                raise RuntimeError("DiskInfoToolkit is missing")
            object.__setattr__(self, name, value)

    computer = Computer()
    monkeypatch.setattr(lhm_inprocess, "open_computer", lambda folder: computer)
    assert winservice.open_everything("libs") is computer
    assert computer.IsControllerEnabled and computer.IsPsuEnabled and computer.IsBatteryEnabled
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert said == ["LibreHardwareMonitor cannot read Storage: DiskInfoToolkit is missing"]


def test_a_library_that_is_not_there_is_the_readers_failure(monkeypatch):
    from libre_panel.sensors import lhm_inprocess

    monkeypatch.setattr(lhm_inprocess, "availability", lambda *a, **k: (None, "it is gone"))
    reader = winservice.make_reader()
    reader.start()
    try:
        assert wait_for(lambda: reader.snapshot().state == "failed")
        assert reader.snapshot().error.endswith("it is gone")
    finally:
        reader.close()


def test_drives_and_the_rest_are_read_through_the_same_code():
    """A drive is hardware like any other: the reader keeps it up to date when asked."""
    drive = FakeHardware(
        "/nvme/0",
        "Solid-State Drive",
        "Storage",
        [FakeSensor("/nvme/0/temperature/0", "Composite Temperature", "Temperature", 41.0)],
    )
    computer = small_computer()
    computer.Hardware.append(drive)
    reader, computer, clock = begun(computer)
    clock.now += 1
    reader.ask(["/nvme/0"])
    reader.refresh()
    assert drive.updates == 2
    assert any(s.hardware_id == "/nvme/0" for s in reader.snapshot().sensors)

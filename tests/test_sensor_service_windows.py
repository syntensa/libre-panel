"""The sensor service's Windows calls on Windows itself: a real pipe, served and asked in this
process, and the questions any user may ask Windows. (The service host and system-setup need
an administrator: packaging/windows/test_installer.py proves them on CI.)"""

import os
import sys
import threading
import time
import uuid

import pytest
from fake_lhm import small_computer

from libre_panel.sensorservice import core, protocol, winpipe

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' named pipes")


def reader():
    computer = small_computer()
    made = core.Reader(lambda: computer)
    made._begin(computer)
    return made


def pipe_name():
    return rf"\\.\pipe\LibrePanelSensorsTest-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def server_for(name):
    from libre_panel.sensorservice import winapi

    # the user running the tests is named too: a CI runner may not log on interactively
    sddl = winpipe.pipe_sddl(("IU", winapi.token_user_sid()))
    return winpipe.PipeServer(core.Handler(reader()), name=name, sddl=sddl, instances=2)


def test_a_real_pipe_answers_and_its_server_is_the_process_that_made_it():
    name = pipe_name()
    server = server_for(name)
    server.start()
    try:
        link = winpipe.connect(name)
        try:
            assert link.server_pid() == os.getpid()
            hello = protocol.parse_hello(link.request(protocol.hello_request(), 5))
            assert hello.protocol == protocol.PROTOCOL
            readings = protocol.parse_readings(link.request(protocol.read_request(["/a"]), 5))
            assert readings.state == "running" and readings.sensors
            # the same connection, again: the instance stays the client's
            assert protocol.parse_hello(link.request(protocol.hello_request(), 5))
        finally:
            link.close()
        # the next client gets the instance the first one left
        for _ in range(3):
            link = winpipe.connect(name)
            try:
                assert protocol.parse_hello(link.request(protocol.hello_request(), 5))
            finally:
                link.close()
    finally:
        server.stop()


def test_a_name_that_is_taken_is_not_joined():
    name = pipe_name()
    server = server_for(name)
    server.start()
    second = server_for(name)
    try:
        with pytest.raises(OSError):
            second.start()
    finally:
        second.stop()
        server.stop()


def test_stopping_ends_the_wait_for_a_client_that_says_nothing():
    name = pipe_name()
    server = server_for(name)
    server.start()
    link = winpipe.connect(name)
    try:
        started = time.monotonic()
        stopper = threading.Thread(target=server.stop)
        stopper.start()
        stopper.join(15)
        assert not stopper.is_alive() and time.monotonic() - started < 10
    finally:
        link.close()


def test_no_pipe_under_a_name_is_file_not_found():
    with pytest.raises(FileNotFoundError):
        winpipe.connect(pipe_name(), timeout=0.2)


def test_what_any_user_may_ask_windows():
    from libre_panel.sensorservice import winapi

    assert winapi.service_status("LibrePanelSensorsNoSuchService") is None
    state, pid = winapi.service_status("EventLog")  # always runs
    assert state == winapi.SERVICE_RUNNING and pid > 0
    me = winapi.token_user_sid()
    assert me.startswith("S-1-5-")
    assert winapi.account_name("S-1-5-32-545")  # "Users", in this Windows' language
    assert isinstance(winapi.group_members("S-1-5-32-545"), set)
    assert winapi.token_has("S-1-1-0")  # everyone
    assert winapi.system_folder().lower().endswith("system32")
    signed_in = winapi.session_user_sid()  # none in a session without a user (a service's)
    assert signed_in is None or signed_in.startswith("S-1-5-")

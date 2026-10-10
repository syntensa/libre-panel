"""LibreHardwareMonitor: its readings go when its web server stops answering."""

import json
import logging
import threading
from pathlib import Path

import pytest

from libre_panel.sensors import librehardwaremonitor as lhm

LOGGER = "libre_panel.sensors.librehardwaremonitor"
TREE = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))


def wait_for(predicate, timeout=10.0):
    pause = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        pause.wait(0.01)
    return False


@pytest.fixture
def web_server(monkeypatch):
    """What the provider's requests get: the real data, or an exception to raise."""
    state = {"answer": TREE}

    def fetch(self):
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(lhm.LibreHardwareMonitorProvider, "_fetch", fetch)
    return state


def provider_with(**options):
    """After its first request the thread waits an hour: the test makes the next
    requests itself, at the times it chooses."""
    return lhm.LibreHardwareMonitorProvider({"poll_seconds": 3600, **options})


def records(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_a_silent_web_server_hides_the_readings_after_a_while(web_server, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    provider = provider_with(stale_seconds=5)
    try:
        assert wait_for(lambda: provider.read())
        answered = provider._answered_at
        web_server["answer"] = OSError("connection refused")

        provider._poll_once(answered + 3)  # a short silence: the last numbers stay
        assert provider.read()["cpu.load"].value == 0.4
        assert not records(caplog, logging.WARNING)

        provider._poll_once(answered + 6)  # long enough: "--" on the panel
        assert provider.read() == {}
        provider._poll_once(answered + 9)
        warning = records(caplog, logging.WARNING)
        assert len(warning) == 1
        assert "has not answered" in warning[0] and "hidden" in warning[0]

        web_server["answer"] = TREE
        provider._poll_once(answered + 10)
        assert provider.read()["cpu.load"].value == 0.4
        provider._poll_once(answered + 11)
        back = records(caplog, logging.INFO)
        assert len(back) == 1 and "answers again" in back[0]
        assert len(records(caplog, logging.WARNING)) == 1  # nothing more to complain about
    finally:
        provider.close()


def test_a_slow_answer_in_time_is_not_a_silence(web_server, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    provider = provider_with(stale_seconds=5)
    try:
        assert wait_for(lambda: provider.read())
        answered = provider._answered_at
        web_server["answer"] = TimeoutError("slow")
        provider._poll_once(answered + 4.9)
        web_server["answer"] = TREE
        provider._poll_once(answered + 5.5)  # it answered after all
        assert provider.read()["cpu.load"].value == 0.4
        assert not records(caplog, logging.WARNING) and not records(caplog, logging.INFO)
    finally:
        provider.close()


def test_how_long_is_set_in_the_options(web_server):
    assert lhm.STALE_SECONDS == 5.0
    assert provider_with().stale_seconds == 5.0
    provider = provider_with(stale_seconds=1.5)
    try:
        assert wait_for(lambda: provider.read())
        answered = provider._answered_at
        web_server["answer"] = OSError("gone")
        provider._poll_once(answered + 1.4)
        assert provider.read()
        provider._poll_once(answered + 1.5)
        assert provider.read() == {}
    finally:
        provider.close()


def test_zero_keeps_the_last_readings_as_before(web_server, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    provider = provider_with(stale_seconds=0)
    try:
        assert wait_for(lambda: provider.read())
        answered = provider._answered_at
        web_server["answer"] = OSError("connection refused")
        provider._poll_once(answered + 3600)
        provider._poll_once(answered + 7200)
        assert provider.read()["cpu.load"].value == 0.4
        warning = records(caplog, logging.WARNING)
        assert len(warning) == 1 and "not reachable" in warning[0]  # said once, at the first
    finally:
        provider.close()


def test_a_web_server_that_never_answered_is_named_once(web_server, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    web_server["answer"] = OSError("connection refused")
    provider = provider_with()
    try:
        assert wait_for(lambda: provider._warned)  # the thread's first request failed
        assert provider.read() == {}
        provider._poll_once()
        warning = records(caplog, logging.WARNING)
        assert len(warning) == 1 and "not reachable" in warning[0]
        assert "connection refused" in warning[0]

        web_server["answer"] = TREE  # LibreHardwareMonitor was started in the meantime
        provider._poll_once()
        assert provider.read()["cpu.load"].value == 0.4
        assert len(records(caplog, logging.INFO)) == 1
    finally:
        provider.close()
    assert wait_for(lambda: not provider._thread.is_alive())

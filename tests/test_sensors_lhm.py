"""LibreHardwareMonitor: its readings go when its web server stops answering."""

import json
import logging
import threading
from pathlib import Path

import pytest
from fake_sensors import wait_for

from libre_panel.sensors import librehardwaremonitor as lhm
from libre_panel.sensors.base import Reading

LOGGER = "libre_panel.sensors.librehardwaremonitor"
TREE = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))


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


def records(caplog, level, provider):
    """What the test's provider logged, from the test's thread or its own: a provider of
    another test may still be asking the real address, which on Windows takes until its
    timeout."""
    mine = {threading.get_ident(), getattr(provider._thread, "ident", None)}
    return [r.getMessage() for r in caplog.records if r.levelno == level and r.thread in mine]


def test_a_silent_web_server_hides_the_readings_after_a_while(web_server, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    provider = provider_with(stale_seconds=5)
    try:
        assert wait_for(lambda: provider.read())
        answered = provider._answered_at
        web_server["answer"] = OSError("connection refused")

        provider._poll_once(answered + 3)  # a short silence: the last numbers stay
        assert provider.read()["cpu.load"].value == 0.4
        assert not records(caplog, logging.WARNING, provider)

        provider._poll_once(answered + 6)  # long enough: "--" on the panel
        assert provider.read() == {}
        provider._poll_once(answered + 9)
        warning = records(caplog, logging.WARNING, provider)
        assert len(warning) == 1
        assert "has not answered" in warning[0] and "hidden" in warning[0]

        web_server["answer"] = TREE
        provider._poll_once(answered + 10)
        assert provider.read()["cpu.load"].value == 0.4
        provider._poll_once(answered + 11)
        back = records(caplog, logging.INFO, provider)
        assert len(back) == 1 and "answers again" in back[0]
        assert len(records(caplog, logging.WARNING, provider)) == 1  # nothing more to say
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
        assert not records(caplog, logging.WARNING, provider)
        assert not records(caplog, logging.INFO, provider)
    finally:
        provider.close()


def test_how_long_is_set_in_the_options(web_server):
    assert lhm.STALE_SECONDS == 5.0
    assert provider_with().stale_seconds == 5.0
    provider = provider_with(stale_seconds=1.5)
    try:
        assert wait_for(lambda: provider.read())
        # a time to which 1.5 s add up exactly: Windows' clock in milliseconds gives some
        # (126.503) to which 1.5 s later is a hair less than 1.5 s later
        answered = provider._answered_at = 1000.0
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
        warning = records(caplog, logging.WARNING, provider)
        assert len(warning) == 1 and "not reachable" in warning[0]  # said once, at the first
    finally:
        provider.close()


def test_a_web_server_that_never_answered_costs_one_quiet_line(web_server, caplog):
    """Windows lists LibreHardwareMonitor for everyone: most have it not running."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    web_server["answer"] = OSError("connection refused")
    provider = provider_with()
    try:
        assert wait_for(lambda: provider._warned)  # the thread's first request failed
        assert provider.read() == {}
        for _ in range(10):
            provider._poll_once()
        assert not records(caplog, logging.WARNING, provider)
        said = records(caplog, logging.INFO, provider)
        assert len(said) == 1 and "does not answer" in said[0] and "connection refused" in said[0]
        assert "administrator" in said[0] and "Options -> Remote Web Server -> Run" in said[0]

        web_server["answer"] = TREE  # LibreHardwareMonitor was started in the meantime
        provider._poll_once()
        assert provider.read()["cpu.load"].value == 0.4
        back = records(caplog, logging.INFO, provider)
        assert len(back) == 2 and "answers again" in back[1]
    finally:
        provider.close()
    assert wait_for(lambda: not provider._thread.is_alive())


def test_requests_come_less_often_while_nothing_answers(web_server):
    web_server["answer"] = OSError("connection refused")
    provider = provider_with()
    try:
        assert wait_for(lambda: provider._warned)
        provider.poll_seconds, provider._misses = 1.0, 0  # the thread is parked: start over
        waits = [provider._poll_once() for _ in range(8)]
        assert waits == [1, 2, 4, 8, 16, 30, 30, 30] and lhm.BACKOFF_SECONDS == 30
        web_server["answer"] = TREE
        assert provider._poll_once() == 1.0  # answered: the normal rate again
        web_server["answer"] = OSError("gone")
        assert provider._poll_once(provider._answered_at + 1) == 1.0  # their time is not up
    finally:
        provider.close()


def test_readings_still_shown_are_asked_for_at_the_normal_rate_until_they_go(web_server):
    provider = provider_with(stale_seconds=5)
    try:
        assert wait_for(lambda: provider.read())
        provider._stop.set()  # from here on only the test asks, at the times it gives
        provider._thread.join(5)
        provider.poll_seconds = 1.0
        answered = provider._answered_at
        web_server["answer"] = OSError("gone")
        # they are hidden on time, however seldom it would be asked later
        assert [provider._poll_once(answered + s) for s in (1, 2, 3, 4)] == [1.0] * 4
        assert provider.read()
        # gone now, first step of the backoff (a little past 5 s: a large monotonic
        # clock makes (t + 5) - t a hair less than 5)
        assert provider._poll_once(answered + 5.001) == 1.0
        assert provider.read() == {}, (answered, provider._answered_at, provider._misses)
        assert [provider._poll_once(answered + 6) for _ in range(3)] == [2.0, 4.0, 8.0]
    finally:
        provider.close()


def test_a_request_is_given_up_after_a_second(monkeypatch):
    seen = []

    def urlopen(request, timeout=None):
        seen.append((request.full_url, timeout))
        raise OSError("refused")

    monkeypatch.setattr(lhm.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(lhm._DIRECT, "open", urlopen)  # this PC's own server: no proxy
    with pytest.raises(OSError):
        lhm.fetch_tree("http://192.0.2.7:8085/data.json")
    provider = lhm.LibreHardwareMonitorProvider({"poll_seconds": 3600})
    try:
        assert wait_for(lambda: len(seen) >= 2)  # the provider's own first request
    finally:
        provider.close()
    assert seen[0] == ("http://192.0.2.7:8085/data.json", 1.0)
    assert seen[1] == (lhm.DEFAULT_URL, 1.0) and lhm.REQUEST_SECONDS == 1.0


def test_the_hint_names_what_to_do_on_windows():
    url = "http://127.0.0.1:8085/data.json"
    load_only = {"cpu.load": Reading("cpu.load", 5.0, "%"), "gpu.load": Reading("gpu.load", 9.0)}
    hint = lhm.hardware_hint(load_only, ["load", "psutil"], url, "win32")
    assert "No CPU or GPU temperature or power readings" in hint
    assert "run it as administrator" in hint
    assert "Options -> Remote Web Server -> Run" in hint and url in hint
    assert 'Add "librehardwaremonitor" to providers' in hint  # it is not in the list
    listed = ["load", "librehardwaremonitor", "psutil"]
    assert "Add " not in lhm.hardware_hint(load_only, listed, url, "win32")
    for key in lhm.HARDWARE_KEYS:  # any one of them: the sensors arrive
        assert lhm.hardware_hint({key: Reading(key, 50.0)}, listed, url, "win32") is None
    assert lhm.hardware_hint({}, ["psutil"], url, "linux") is None  # psutil has them there


def test_this_pcs_own_web_server_is_asked_past_a_system_proxy(monkeypatch):
    """A proxy set for the system (common at work) cannot reach 127.0.0.1 on this PC."""
    import http.server
    import threading

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"Text": "Sensor", "Children": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://192.0.2.1:9")  # nobody there
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        port = server.server_address[1]
        assert lhm.fetch_tree(f"http://127.0.0.1:{port}/data.json")["Text"] == "Sensor"
    finally:
        server.shutdown()
        server.server_close()


def test_amounts_of_data_read_as_before_from_0_9_7_on():
    """LibreHardwareMonitor 0.9.7 tells every amount of data in bytes, under one type (its web
    server in a unit that reads well); 0.9.6 told GB, and a graphics card's memory in MB
    (SmallData). The readings stay as they were, whichever tells them."""
    sensor, gib, mib = lhm.LhmSensor, 2**30, 2**20
    gpu = ("/gpu-nvidia/0", "NVIDIA GeForce")
    newer = [
        sensor("/gpu-nvidia/0/data/1", *gpu, "Data", "GPU Memory Used", 577 * mib, "B"),
        sensor("/gpu-nvidia/0/data/2", *gpu, "Data", "GPU Memory Total", 16.0, "GB"),
        sensor("/ram/data/0", "/ram", "Generic Memory", "Data", "Memory Used", 12 * gib, "B"),
        sensor("/ram/data/1", "/ram", "Generic Memory", "Data", "Memory Available", 20.5, "GB"),
    ]
    got = lhm.readings_from_sensors(newer)
    assert got["gpu.mem.used"].value == pytest.approx(577) and got["gpu.mem.used"].unit == "MB"
    assert got["lhm:/gpu-nvidia/0/data/2"].value == pytest.approx(16384)
    assert (got["lhm:/ram/data/0"].value, got["lhm:/ram/data/0"].unit) == (pytest.approx(12), "GB")
    assert got["lhm:/ram/data/1"].value == pytest.approx(20.5)
    older = [sensor("/gpu-nvidia/0/smalldata/1", *gpu, "SmallData", "GPU Memory Used", 577.0, "MB")]
    assert lhm.readings_from_sensors(older)["gpu.mem.used"].value == 577.0  # 0.9.6: as it was

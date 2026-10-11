"""LibreHardwareMonitor through the sensor service: the source that asks it, the provider that
prefers it (service, then inside Libre Panel, then the web server), what is said when it does
not answer, and ``libre-panel sensors --check-service``.

The service here is the real one without its pipe: each request goes through the protocol's
encoding both ways to a ``core.Handler`` whose reader holds fake .NET objects (fake_lhm)."""

import logging
import sys
import time
import types

import pytest
from fake_lhm import ring0_off, small_computer
from fake_sensors import show, wait_for

from libre_panel import cli, doctor
from libre_panel.pages import control
from libre_panel.sensors import lhm_inprocess as inside
from libre_panel.sensors import lhm_service
from libre_panel.sensors import librehardwaremonitor as lhm
from libre_panel.sensors.base import Reading, SensorHub
from libre_panel.sensorservice import core, protocol

REAL_REGISTRATION = lhm_service.registration  # (the tests' own machine is kept out: conftest)
CPU, GPU = "/amdcpu/0", "/gpu-nvidia/0"


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class Service:
    """A sensor service in this process, reached through ``link()``; ``down`` (an exception)
    makes it unreachable, ``requests`` keeps what was asked."""

    def __init__(self, computer=None, clock=None, version="0.5.0"):
        self.clock = clock or Clock()
        self.computer = computer or small_computer()
        self.reader = core.Reader(lambda: self.computer, clock=self.clock, pawnio=lambda: "2.0.1")
        self.reader._begin(self.computer)
        self.handler = core.Handler(self.reader, version=version, clock=self.clock)
        self.down = None
        self.requests = []
        self.opened = self.closed = 0

    def link(self):
        if self.down is not None:
            raise self.down
        self.opened += 1
        return Link(self)


class Link:
    def __init__(self, service):
        self.service = service

    def request(self, message, timeout):
        sent = protocol.encode(message, protocol.MAX_REQUEST)
        request = protocol.decode(sent[4:])
        self.service.requests.append(request)
        answer = self.service.handler.answer(request)
        return protocol.decode(protocol.encode(answer, protocol.MAX_ANSWER)[4:])

    def close(self):
        self.service.closed += 1


def source_for(service, **options):
    """A source of the service; tests that do not start its thread make each round
    themselves (``ask``)."""
    options.setdefault("clock", service.clock)
    return lhm_service.ServiceSource(link=service.link, **options)


# -- the source ------------------------------------------------------------------------------------


def test_the_readings_come_through_the_service():
    service = Service()
    source = source_for(service)
    source.start()
    try:
        assert source.wait_ready(10)
        readings = source.read()
        assert readings["cpu.temp"].value == 55.0 and readings["gpu.temp"].value == 48.0
        assert readings["mem.temp"].value == 38.0  # the memory module, which needs the rights
        assert source.status() == lhm.LhmStatus("service", True, True, "2.0.1")
        assert source.version == "0.5.0" and source.live()
        hello, first = service.requests
        assert hello == protocol.hello_request()
        assert first["op"] == "read" and first["all"] is True  # a new connection: everything once
    finally:
        source.close()
    assert service.closed == 1


def test_only_the_hardware_a_theme_reads_is_asked_for():
    service = Service()
    source = source_for(service)
    source.ask()  # learns what there is
    theme = show("cpu.temp", "lhm:/gpu-nvidia/0/load/0")
    source.ask()
    assert service.requests[-1]["hardware"] == [CPU, GPU] and service.requests[-1]["all"] is False
    del theme
    source.ask()
    assert service.requests[-1]["hardware"] == []
    keep = show("temp.lpc.system")  # a friendly key of the mainboard's chip
    source.ask()
    assert service.requests[-1]["hardware"] == ["/lpc/nct0000/0"]
    del keep


def test_what_the_service_read_long_ago_is_not_shown():
    """Nobody asked for a while: its last readings are old, and it reads anew now."""
    service = Service()
    source = source_for(service)
    service.clock.now += 120
    source.ask()
    assert source.read() == {} and not source.wait_ready(0)
    service.reader.refresh()  # its thread's next round, woken by the request
    source.ask()
    assert source.read()["cpu.temp"].value == 55.0 and source.wait_ready(0)


def test_readings_that_stop_coming_go_after_stale_seconds():
    service = Service()
    source = source_for(service, stale_seconds=5)
    source.ask()
    assert source.read()
    service.clock.now += 5.5
    assert source.read() == {}  # not numbers that stand still


def test_without_pawnio_the_cpu_is_out_of_reach_and_the_status_says_why():
    service = Service(ring0_off(small_computer()))
    service.reader._pawnio = lambda: None
    service.reader._publish()
    source = source_for(service)
    source.ask()
    assert "cpu.temp" not in source.read() and "gpu.temp" in source.read()
    assert source.status() == lhm.LhmStatus("service", True, False, None)


def test_a_service_that_does_not_answer_is_given_up_after_a_while_and_taken_back(caplog):
    caplog.set_level(logging.INFO, logger="libre_panel.sensors.lhm_service")
    service = Service()
    service.down = lhm_service.Unavailable("the sensor service is starting")
    failed, ready = [], []
    source = source_for(
        service,
        poll_seconds=0.01,
        wait_seconds=0,
        on_failure=failed.append,
        on_ready=ready.append,
        clock=time.monotonic,
    )
    source.start()
    try:
        assert wait_for(lambda: failed) and source.wait_ready(0)
        assert failed == ["the sensor service is starting"]
        assert source.status() == lhm.LhmStatus("service", error="the sensor service is starting")
        assert source.read() == {} and not ready
        service.down = None  # it runs now
        assert wait_for(lambda: len(ready) >= 1)
        assert ready[-1].mode == "service" and ready[-1].ring0 is True
    finally:
        source.close()
    assert failed == ["the sensor service is starting"]  # said once for the one silence


def test_a_service_of_another_protocol_is_not_used():
    service = Service()
    source = source_for(service)
    service.handler.answer = lambda message: {
        "v": 1, "ok": True, "service": "9.0", "protocol": 7,
    }  # fmt: skip
    with pytest.raises(lhm_service.Unavailable, match="protocol 7.*set it up again"):
        source.ask()
    assert service.closed == 1 and source._link is None


def test_a_service_whose_library_did_not_start_counts_as_not_answering():
    def broken():
        raise RuntimeError("no .NET Framework")

    service = Service()
    service.reader = core.Reader(broken)
    service.reader._run()
    service.handler = core.Handler(service.reader)
    source = source_for(service)
    with pytest.raises(lhm_service.Unavailable, match="no .NET Framework"):
        source.ask()


def test_an_answer_that_is_not_the_protocols_is_not_taken():
    service = Service()
    source = source_for(service)
    source.ask()
    service.handler.answer = lambda message: {"v": 1, "ok": True, "state": "running"}
    with pytest.raises(protocol.ProtocolError):
        source.ask()


# -- the provider: service, then inside Libre Panel, then the web server ---------------------------


class FakeServiceSource:
    """What the provider makes where the sensor service is installed."""

    made = []

    def __init__(self, **options):
        self.options = options
        self.started = self.closed = False
        self.readings = {}
        self.state = lhm.LhmStatus("service", True, True, "2.0.1")
        self.ready = True
        self.made.append(self)

    def start(self):
        self.started = True

    def read(self):
        return dict(self.readings)

    def live(self):
        return bool(self.readings)

    def status(self):
        return self.state

    def wait_ready(self, timeout):
        return self.ready

    def close(self):
        self.closed = True


class FakeInside:
    made = []

    def __init__(self, opener, **options):
        self.options = options
        self.closed = False
        self.readings = {"gpu.temp": Reading("gpu.temp", 50.0, "°C")}
        self.state = lhm.LhmStatus("inprocess", False, False, "2.0.1")
        self.made.append(self)

    def start(self):
        pass

    def read(self):
        return dict(self.readings)

    def status(self):
        return self.state

    def wait_ready(self, timeout):
        return True

    def close(self):
        self.closed = True


@pytest.fixture
def installed(monkeypatch):
    """The sensor service is installed; its source and the one inside Libre Panel are
    stand-ins, and the web server answers nothing."""
    FakeServiceSource.made, FakeInside.made = [], []
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: {"Start": 2})
    monkeypatch.setattr(lhm_service, "ServiceSource", FakeServiceSource)
    monkeypatch.setattr(inside, "availability", lambda *a, **k: ("libraries", ""))
    monkeypatch.setattr(inside, "InProcessSource", FakeInside)
    asked = []

    def fetch(self):
        asked.append(True)
        raise OSError("connection refused")

    monkeypatch.setattr(lhm.LibreHardwareMonitorProvider, "_fetch", fetch)
    return types.SimpleNamespace(services=FakeServiceSource.made, inside=FakeInside.made, web=asked)


def provider_with(**options):
    return lhm.LibreHardwareMonitorProvider({"poll_seconds": 3600, **options})


def test_where_the_service_is_installed_it_is_all_that_is_read(installed):
    provider = provider_with(gpu="radeon", stale_seconds=9)
    try:
        (service,) = installed.services
        assert service.started and service.options["gpu"] == "radeon"
        assert service.options["stale_seconds"] == 9
        assert installed.inside == [] and provider._thread is None and not installed.web
        service.readings = {"cpu.temp": Reading("cpu.temp", 60.0, "°C")}
        assert provider.read() == service.readings
        assert provider.status() == service.state and provider.wait_ready(0)
    finally:
        provider.close()
    assert service.closed


def test_a_service_that_is_switched_off_counts_as_not_installed(installed, monkeypatch):
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: {"Start": 4})
    provider = provider_with()
    try:
        assert installed.services == [] and len(installed.inside) == 1
    finally:
        provider.close()


def test_without_the_service_it_reads_inside_libre_panel_as_before(installed, monkeypatch):
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: None)
    provider = provider_with()
    try:
        assert installed.services == [] and len(installed.inside) == 1
        assert provider.status().mode == "inprocess"
    finally:
        provider.close()


def test_the_mode_service_asks_the_service_only(installed, monkeypatch):
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: None)  # not even there
    provider = provider_with(mode="service")
    try:
        (service,) = installed.services
        service.options["on_failure"]("the sensor service is not installed")
        assert installed.inside == [] and provider._thread is None
        service.state = lhm.LhmStatus("service", error="the sensor service is not installed")
        assert provider.status() == service.state
    finally:
        provider.close()


def test_auto_reads_inside_libre_panel_while_the_service_does_not_answer(installed, caplog):
    caplog.set_level(logging.INFO, logger="libre_panel.sensors.librehardwaremonitor")
    provider = provider_with()
    (service,) = installed.services
    try:
        service.state = lhm.LhmStatus("service", error="the sensor service is stopped")
        assert provider.read() == {}  # its first moments: nothing yet, nothing else either
        service.options["on_failure"]("the sensor service is stopped")
        (fallback,) = installed.inside
        assert provider.read() == fallback.readings
        assert provider.status() == lhm.LhmStatus(
            "inprocess", False, False, "2.0.1", service="the sensor service is stopped"
        )
        service.options["on_failure"]("again")  # one fallback, however often it is said
        assert len(installed.inside) == 1
        # it answers again: what read meanwhile stops
        service.readings = {"cpu.temp": Reading("cpu.temp", 61.0, "°C")}
        service.state = lhm.LhmStatus("service", True, True, "2.0.1")
        service.options["on_ready"](service.state)
        assert fallback.closed and provider._source is None
        assert provider.read() == service.readings and provider.status() == service.state
    finally:
        provider.close()
    said = [r.getMessage() for r in caplog.records]
    assert any("does not answer" in line and "stopped" in line for line in said)
    assert any("answers again" in line for line in said)


def test_a_service_installed_while_libre_panel_runs_takes_over_when_it_answers(
    installed, monkeypatch
):
    """The setup, or "Unlock all sensors", while the panel reads inside Libre Panel: a look at
    the registry now and then finds the service, which takes over once it answers."""
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: None)
    provider = provider_with()
    try:
        (fallback,) = installed.inside
        assert provider.read() == fallback.readings and installed.services == []
        monkeypatch.setattr(lhm_service, "registration", lambda name=None: {"Start": 2})
        assert provider.read() == fallback.readings and installed.services == []  # not yet
        provider._looked -= lhm.SERVICE_LOOK_SECONDS
        assert provider.read() == fallback.readings  # it reads on until the service answers
        (service,) = installed.services
        assert service.started and provider.status() == fallback.state
        service.readings = {"cpu.temp": Reading("cpu.temp", 61.0, "°C")}
        service.options["on_ready"](service.state)
        assert fallback.closed and provider.read() == service.readings
        provider._looked -= lhm.SERVICE_LOOK_SECONDS
        provider.read()
        assert len(installed.services) == 1  # found once
    finally:
        provider.close()
    assert service.closed


def test_waiting_for_the_service_and_then_for_what_replaces_it(installed):
    provider = provider_with()
    (service,) = installed.services
    try:
        service.ready = False
        assert not provider.wait_ready(0)
        service.ready = True
        assert provider.wait_ready(0)
        service.options["on_failure"]("gone")
        assert provider.wait_ready(0)  # the source inside Libre Panel is ready too
    finally:
        provider.close()


# -- what is said ----------------------------------------------------------------------------------

DRIVER = "CPU temperature and power need the PawnIO driver: install it from pawnio.eu."
SILENT = (
    "CPU temperature and power are missing: the sensor service does not answer. "
    "Set it up again in Settings → Sensors, or restart Windows."
)
UNLOCK = (
    "CPU temperature, power and fans need the sensor service: choose “Unlock all sensors” on Start."
)
GPU_ONLY = {"gpu.temp": Reading("gpu.temp", 50.0)}


def user(status, readings=None, unlock=False):
    return lhm.user_hint(GPU_ONLY if readings is None else readings, "win32", status, True, unlock)


def test_the_service_without_pawnio_says_to_install_it():
    assert user(lhm.LhmStatus("service", True, False, None)) == DRIVER
    assert user(lhm.LhmStatus("service", True, True, "2.0.1")) == ""
    assert user(lhm.LhmStatus("service", True, None, None)) == ""  # no such sensors here


def test_a_service_that_does_not_answer_is_said_so():
    assert user(lhm.LhmStatus("service", error="it is stopped")) == SILENT
    fallback = lhm.LhmStatus("inprocess", False, False, "2.0.1", service="it is stopped")
    assert user(fallback) == SILENT
    assert user(fallback, {"cpu.temp": Reading("cpu.temp", 40.0)}) == ""


def test_a_download_that_can_unlock_says_so_instead_of_administrator_rights():
    assert user(lhm.LhmStatus("inprocess", False, False, "2.0.1"), unlock=True) == UNLOCK
    assert user(lhm.LhmStatus("inprocess", False, False, None), unlock=True) == UNLOCK
    # its reader did not start, and nothing arrives: the service is the way out
    assert user(lhm.LhmStatus("webserver", error="no pythonnet"), {}, unlock=True) == UNLOCK
    # with the rights, only the driver is missing: that is not unlocked by rights
    assert user(lhm.LhmStatus("inprocess", True, False, None), unlock=True) == DRIVER


def test_the_unlock_hint_speaks_german():
    from libre_panel import i18n

    i18n.set_language("de")
    assert user(lhm.LhmStatus("inprocess", False, False, "2.0.1"), unlock=True) == (
        "CPU-Temperatur, -Leistung und Lüfter brauchen den Sensordienst: "
        "auf Start „Alle Sensoren freischalten“ wählen."
    )


def hint(status, unlock=False):
    return lhm.hardware_hint({}, ["librehardwaremonitor"], lhm.DEFAULT_URL, "win32", status, unlock)


def test_the_command_line_hint_names_the_service():
    assert hint(lhm.LhmStatus("service", error="it is stopped")).endswith(
        "The sensor service does not answer: it is stopped."
    )
    assert hint(lhm.LhmStatus("service", True, False, None)).endswith(
        "The sensor service runs, but the CPU's sensors are out of reach: "
        "install the PawnIO driver (https://pawnio.eu)."
    )
    assert hint(lhm.LhmStatus("service", True, True, "2.0.1")).endswith(
        "finds no sensors it supports."
    )
    fallback = lhm.LhmStatus("inprocess", False, False, "2.0.1", service="it is stopped")
    assert hint(fallback).endswith("The sensor service does not answer: it is stopped.")
    out_of_reach = hint(lhm.LhmStatus("inprocess", False, False, "2.0.1"), unlock=True)
    assert out_of_reach.endswith(f"out of reach: {lhm.SERVICE_STEP}.")
    assert "autostart enable --elevated" not in out_of_reach
    unstarted = hint(lhm.LhmStatus("inprocess", error="no pythonnet"), unlock=True)
    assert unstarted.endswith(f"no pythonnet. Instead: {lhm.SERVICE_STEP}.")


def test_the_control_page_names_the_service():
    status = {"mode": "service", "elevated": True, "ring0": False, "pawnio": None, "error": ""}
    assert control.lhm_rows(status) == [
        ["Mode", "sensor service"],
        ["CPU sensors", "out of reach"],
        ["PawnIO driver", "not installed"],
    ]
    fallback = {"mode": "inprocess", "elevated": False, "ring0": True, "pawnio": "2.0.1"}
    fallback["service"] = "it is stopped"
    assert ["Sensor service", "it is stopped"] in control.lhm_rows(fallback)


def test_the_run_status_hint_follows_the_service(installed):
    from libre_panel import app

    provider = provider_with()
    (service,) = installed.services
    service.state = lhm.LhmStatus("service", True, False, None)
    hub = SensorHub([provider])
    try:
        assert app.sensor_hint(hub, GPU_ONLY, "win32") == DRIVER
        service.readings = {"cpu.temp": Reading("cpu.temp", 44.0, "°C")}
        assert app.sensor_hint(hub, GPU_ONLY, "win32") == ""
    finally:
        provider.close()


# -- what Windows has on record --------------------------------------------------------------------


class FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_winreg(services):
    module = types.ModuleType("winreg")
    module.HKEY_LOCAL_MACHINE = "HKLM"

    def open_key(root, path):
        name = path.rsplit("\\", 1)[-1]
        if root != "HKLM" or not path.startswith(lhm_service.SERVICES_KEY) or name not in services:
            raise FileNotFoundError(path)
        return FakeKey(services[name])

    def query(key, value):
        if value not in key.values:
            raise FileNotFoundError(value)
        return key.values[value], 1

    module.OpenKey, module.QueryValueEx = open_key, query
    return module


def test_the_registration_is_read_from_the_registry(monkeypatch):
    program = r'"C:\Program Files\Libre Panel\sensors\libre-panel-sensors.exe"'
    winreg = fake_winreg({"LibrePanelSensors": {"ImagePath": program, "Start": 2}})
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    assert REAL_REGISTRATION() == {"ImagePath": program, "Start": 2}
    assert REAL_REGISTRATION("Other") is None
    monkeypatch.setattr(lhm_service, "registration", REAL_REGISTRATION)
    assert lhm_service.installed()
    monkeypatch.setitem(
        sys.modules, "winreg", fake_winreg({"LibrePanelSensors": {"Start": lhm_service.DISABLED}})
    )
    assert not lhm_service.installed()


def test_off_windows_nothing_is_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)  # import winreg fails
    assert REAL_REGISTRATION() is None


# -- libre-panel sensors --check-service -----------------------------------------------------------


def test_the_check_reads_through_the_service_and_says_what_it_found():
    service = Service()
    worked, lines = lhm_service.check(service.link, platform="win32", sleep=lambda s: None)
    assert worked
    assert lines[0] == "sensor service 0.5.0, protocol 1"
    assert "read through the sensor service:" in lines[1] and "sensors of" in lines[1]
    assert "CPU temperature, power and clock: available" in lines
    assert "PawnIO driver: 2.0.1" in lines and "cpu.temp: 55.0 °C" in lines
    assert service.closed == 1


def test_the_check_waits_for_fresh_readings():
    service = Service()
    service.clock.now += 300  # its readings are old: it reads anew when asked
    pauses = []

    def sleep(seconds):
        pauses.append(seconds)
        service.reader.refresh()

    worked, lines = lhm_service.check(service.link, platform="win32", sleep=sleep)
    assert worked and pauses == [0.5]


def test_the_check_fails_where_the_service_does_not_answer():
    service = Service()
    service.down = FileNotFoundError("no pipe")
    worked, lines = lhm_service.check(service.link, platform="win32")
    assert not worked and lines == ["The sensor service does not answer: no pipe"]
    assert lhm_service.check(service.link, platform="linux")[0] is False


def test_the_check_on_the_command_line(monkeypatch, capsys):
    monkeypatch.setattr(
        lhm_service, "check", lambda platform=None: (True, ["sensor service 0.5.0, protocol 1"])
    )
    monkeypatch.setattr(cli, "_platform", lambda: "win32")
    assert cli.main(["sensors", "--check-service"]) == 0
    assert capsys.readouterr().out == "sensor service 0.5.0, protocol 1\n"
    monkeypatch.setattr(lhm_service, "check", lambda platform=None: (False, ["no"]))
    assert cli.main(["sensors", "--check-service"]) == 1


def test_doctor_names_the_service(installed):
    def make(options):
        provider = provider_with(**options)
        service = installed.services[-1]
        service.readings = {"cpu.temp": Reading("cpu.temp", 40.0, "°C")}
        return provider

    result = doctor._inside_check({}, ["librehardwaremonitor"], "here", "win32", make)
    assert result == (
        "info",
        "LibreHardwareMonitor runs in the sensor service: temperature and power arrive",
    )

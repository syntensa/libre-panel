"""LibreHardwareMonitor: inside Libre Panel or through its web server, and what is said of it.

The source that reads inside Libre Panel is a stand-in here (see test_sensors_lhm_inprocess
for the real one on fake .NET objects), so these tests do not depend on the machine.
"""

import json
import logging
import types
from pathlib import Path

import pytest
from fake_lhm import ring0_off, small_computer
from fake_sensors import wait_for

from libre_panel import cli, doctor
from libre_panel.sensors import lhm_inprocess as inside
from libre_panel.sensors import librehardwaremonitor as lhm
from libre_panel.sensors.base import Reading, SensorHub

LOGGER = "libre_panel.sensors.librehardwaremonitor"
TREE = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))
URL = lhm.DEFAULT_URL
ADMIN_STEP = "run Libre Panel as administrator (libre-panel autostart enable --elevated)"
PAWNIO_STEP = "install the PawnIO driver (https://pawnio.eu)"


class FakeSource:
    """What the provider makes where LibreHardwareMonitor can run inside Libre Panel."""

    made: list = []

    def __init__(self, opener, **options):
        self.opener, self.options = opener, options
        self.started = self.closed = False
        self.readings: dict = {}
        self.state = lhm.LhmStatus("inprocess", True, True, "2.0.1")
        self.ready = True
        self.fails = False
        self.made.append(self)

    def start(self):
        self.started = True

    def read(self):
        if self.fails:
            raise RuntimeError("broken")
        return dict(self.readings)

    def status(self):
        return self.state

    def wait_ready(self, timeout):
        return self.ready

    def close(self):
        self.closed = True


@pytest.fixture
def web_server(monkeypatch):
    """What the web server answers: the real data, or an exception to raise."""
    state = {"answer": TREE, "asked": 0}

    def fetch(self):
        state["asked"] += 1
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(lhm.LibreHardwareMonitorProvider, "_fetch", fetch)
    return state


@pytest.fixture
def possible(monkeypatch):
    """LibreHardwareMonitor can run inside Libre Panel; the source is a stand-in."""
    FakeSource.made = []
    asked = []

    def availability(dll_dir=None, platform=None):
        asked.append(dll_dir)
        return Path("libraries"), ""

    monkeypatch.setattr(inside, "availability", availability)
    monkeypatch.setattr(inside, "InProcessSource", FakeSource)
    return types.SimpleNamespace(sources=FakeSource.made, dll_dirs=asked)


@pytest.fixture
def impossible(monkeypatch):
    why = ['pythonnet is not installed (pip install "libre-panel[lhm]")']
    monkeypatch.setattr(inside, "availability", lambda dll_dir=None, platform=None: (None, why[0]))
    return why


def provider_with(**options):
    return lhm.LibreHardwareMonitorProvider({"poll_seconds": 3600, **options})


def records(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


# -- the way is chosen by `mode` ------------------------------------------------------------


def test_it_reads_inside_libre_panel_where_it_can(possible, web_server):
    provider = provider_with(poll_seconds=2, stale_seconds=9, gpu="radeon", dll_dir="my libs")
    (source,) = possible.sources
    assert possible.dll_dirs == ["my libs"] and source.started
    assert source.options["poll_seconds"] == 2 and source.options["stale_seconds"] == 9
    assert source.options["gpu"] == "radeon"
    assert provider._thread is None and web_server["asked"] == 0  # the web server is not asked
    source.readings = {"cpu.load": Reading("cpu.load", 5.0, "%")}
    assert provider.read() == source.readings
    assert provider.status() == source.state
    provider.close()
    assert source.closed


def test_the_web_server_is_read_where_it_cannot_be_inside(impossible, web_server, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    provider = provider_with()
    try:
        assert wait_for(lambda: provider.read())
        assert provider.read()["cpu.load"].value == 0.4
        assert provider.status() == lhm.LhmStatus("webserver")
    finally:
        provider.close()
    assert not records(caplog, logging.WARNING) and not records(caplog, logging.INFO)
    said = records(caplog, logging.DEBUG)
    assert any("web server" in line and "pythonnet is not installed" in line for line in said)


def test_the_web_server_mode_does_not_even_look(monkeypatch, web_server):
    monkeypatch.setattr(inside, "availability", lambda *a, **k: pytest.fail("looked for them"))
    provider = provider_with(mode="webserver")
    try:
        assert wait_for(lambda: provider.read())
        assert provider._source is None
        assert provider.status() == lhm.LhmStatus("webserver")
    finally:
        provider.close()


def test_the_mode_inside_never_asks_the_web_server(impossible, web_server, caplog):
    provider = provider_with(mode="inprocess")
    try:
        assert provider._thread is None and provider.read() == {}
        assert provider.wait_ready(0)  # nothing to wait for
        assert web_server["asked"] == 0
        status = provider.status()
        assert (status.mode, status.error) == ("inprocess", impossible[0])
    finally:
        provider.close()
    said = records(caplog, logging.WARNING)
    assert len(said) == 1 and "cannot run inside Libre Panel" in said[0] and "pythonnet" in said[0]


def test_inside_that_fails_later_goes_to_the_web_server_in_auto_only(possible, web_server):
    auto = provider_with()
    (source,) = possible.sources
    assert auto._thread is None
    source.options["on_failure"]("pythonnet already runs on CoreCLR")
    try:
        assert wait_for(lambda: auto.read())  # the web server takes over
        assert auto.read()["cpu.load"].value == 0.4
    finally:
        auto.close()

    asked = web_server["asked"]
    strict = provider_with(mode="inprocess")
    possible.sources[-1].options["on_failure"]("pythonnet already runs on CoreCLR")
    assert strict._thread is None and web_server["asked"] == asked
    strict.close()


def test_a_mode_that_is_not_one_becomes_auto(possible, web_server, caplog):
    provider = provider_with(mode="somehow")
    assert provider.mode == "auto" and provider._source is not None
    said = records(caplog, logging.WARNING)
    assert len(said) == 1 and "somehow" in said[0] and '"inprocess"' in said[0]
    provider.close()
    assert lhm.LibreHardwareMonitorProvider({"mode": "WebServer", "poll_seconds": 3600}).mode == (
        "webserver"
    )


def test_the_default_mode_is_auto(possible):
    assert provider_with().mode == "auto"


def test_a_source_that_breaks_does_not_stop_the_reading(possible):
    provider = provider_with()
    possible.sources[0].fails = True
    assert provider.read() == {}
    provider.close()


# -- without administrator rights a running LibreHardwareMonitor fills in ---------------------


def test_a_web_server_that_answers_fills_in_what_the_cpu_lacks(possible, web_server, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    provider = provider_with()
    (source,) = possible.sources
    source.readings = {
        "gpu.temp": Reading("gpu.temp", 61.0, "°C"),  # its own: stays
        "cpu.load": Reading("cpu.load", 9.0, "%"),
    }
    source.options["on_ready"](lhm.LhmStatus("inprocess", False, False, None))
    try:
        assert wait_for(lambda: provider._thread is not None and provider._latest)
        read = provider.read()
        assert read["cpu.temp"].value == 40.0  # from the web server: the CPU's
        assert read["gpu.temp"].value == 61.0 and read["cpu.load"].value == 9.0  # not replaced
        assert any("fill in" in line for line in records(caplog, logging.INFO))
    finally:
        provider.close()


def test_no_web_server_is_started_for_nothing(possible, web_server):
    web_server["answer"] = OSError("connection refused")
    provider = provider_with()
    (source,) = possible.sources
    source.options["on_ready"](lhm.LhmStatus("inprocess", False, False, None))
    assert provider._thread is None and web_server["asked"] == 1  # asked once, no poller
    for status in (
        lhm.LhmStatus("inprocess", True, True, "2.0.1"),  # the CPU's sensors work
        lhm.LhmStatus("inprocess", True, None, "2.0.1"),  # there are none
    ):
        source.options["on_ready"](status)
    assert web_server["asked"] == 1
    provider.close()
    inside_only = provider_with(mode="inprocess")  # this mode does not ask at all
    possible.sources[-1].options["on_ready"](lhm.LhmStatus("inprocess", False, False, None))
    assert inside_only._thread is None and web_server["asked"] == 1
    inside_only.close()


def test_waiting_for_the_first_readings(possible, web_server):
    provider = provider_with()
    (source,) = possible.sources
    source.ready = False
    assert not provider.wait_ready(0)
    source.ready = True
    assert provider.wait_ready(0)  # no web poller: nothing else to wait for
    source.options["on_ready"](lhm.LhmStatus("inprocess", False, False, None))
    try:
        assert provider.wait_ready(10)  # and with one: until it has asked
        assert provider._polled.is_set()
    finally:
        provider.close()


# -- what is said when it does not work -----------------------------------------------------


def hint(status, readings=None, providers=("load", "librehardwaremonitor", "psutil")):
    return lhm.hardware_hint(readings or {}, list(providers), URL, "win32", status)


def test_the_hint_for_inside_says_to_run_as_administrator():
    said = hint(lhm.LhmStatus("inprocess", False, False, "2.0.1"))
    assert said.startswith("No CPU or GPU temperature or power readings.")
    assert "runs inside Libre Panel" in said
    assert said.endswith(f"out of reach: {ADMIN_STEP}.") and "PawnIO" not in said
    assert "start LibreHardwareMonitor" not in said and "Remote Web Server" not in said


def test_the_hint_for_inside_names_what_is_still_missing():
    both = hint(lhm.LhmStatus("inprocess", False, False, None))
    assert f"{ADMIN_STEP} and {PAWNIO_STEP}." in both
    driver = hint(lhm.LhmStatus("inprocess", True, False, None))
    assert driver.endswith(f"out of reach: {PAWNIO_STEP}.") and ADMIN_STEP not in driver
    old = hint(lhm.LhmStatus("inprocess", True, False, "1.4.0"))
    assert "update the PawnIO driver to version 2 or newer" in old
    nothing = hint(lhm.LhmStatus("inprocess", True, True, "2.0.1"))
    assert nothing.endswith("but finds no sensors it supports.")


def test_the_hint_for_inside_says_why_it_did_not_start():
    said = hint(lhm.LhmStatus("inprocess", error="pythonnet already runs on CoreCLR"))
    assert said.endswith("could not start inside Libre Panel: pythonnet already runs on CoreCLR")


def test_the_hint_is_the_old_one_for_the_web_server_and_nothing_when_readings_arrive():
    old = hint(None)
    assert "run it as administrator" in old and "Options -> Remote Web Server -> Run" in old
    assert hint(lhm.LhmStatus("webserver")) == old
    for status in (None, lhm.LhmStatus("inprocess", False, False, None)):
        assert hint(status, {"gpu.temp": Reading("gpu.temp", 50.0)}) is None
    assert lhm.hardware_hint({}, [], URL, "linux", lhm.LhmStatus("inprocess")) is None


# -- libre-panel sensors ---------------------------------------------------------------


def run_sensors(isolated_home, monkeypatch, capsys, provider, *args):
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[sensors]\nproviders = ["librehardwaremonitor"]\n')
    monkeypatch.setattr(
        "libre_panel.app.build_hub", lambda config, demo=False: SensorHub([provider])
    )
    monkeypatch.setattr(cli, "_platform", lambda: "win32")
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    code = cli.main(["--config", str(path), "sensors", *args])
    return code, capsys.readouterr()


def test_sensors_waits_for_a_source_that_starts_in_the_background(
    isolated_home, possible, monkeypatch, capsys
):
    provider = provider_with()
    (source,) = possible.sources
    source.readings = {"gpu.temp": Reading("gpu.temp", 50.0, "°C", "GPU temp")}
    waited = []
    source.wait_ready = lambda timeout: waited.append(timeout) or True
    code, out = run_sensors(isolated_home, monkeypatch, capsys, provider)
    assert code == 0 and waited == [inside.READY_SECONDS]
    assert "gpu.temp" in out.out and out.err == ""  # a reading of the graphics card arrives


def test_sensors_says_to_run_as_administrator_when_inside(
    isolated_home, possible, monkeypatch, capsys
):
    provider = provider_with()
    (source,) = possible.sources
    source.readings = {"cpu.load": Reading("cpu.load", 5.0, "%")}
    source.state = lhm.LhmStatus("inprocess", False, False, "2.0.1")
    code, out = run_sensors(isolated_home, monkeypatch, capsys, provider)
    assert code == 0 and "cpu.load" in out.out
    assert "note: No CPU or GPU temperature or power readings." in out.err
    assert ADMIN_STEP in out.err and "Remote Web Server" not in out.err


def test_sensors_asks_for_every_sensor_of_the_library(isolated_home, possible, monkeypatch, capsys):
    from libre_panel.sensors import base

    provider = provider_with()
    seen = []
    possible.sources[0].wait_ready = lambda timeout: seen.append(base.wanted("lhm:/amdcpu/0/"))
    run_sensors(isolated_home, monkeypatch, capsys, provider)
    assert seen == [True]  # while it waits, every node counts as read
    assert not base.wanted("lhm:/amdcpu/0/")  # and afterwards nothing stays asked for


# -- libre-panel sensors --check-lhm ---------------------------------------------------------


@pytest.fixture
def library(monkeypatch, tmp_path):
    """The libraries are there (an empty folder with the main one), and what is opened is a
    fake computer the test can change."""
    folder = tmp_path / "libraries"
    folder.mkdir()
    for name in inside.LIBRARIES:
        (folder / f"{name}.dll").write_bytes(b"")
    opened = {"computer": small_computer(), "error": None}

    def open_computer(where):
        assert where == folder
        if opened["error"]:
            raise opened["error"]
        return opened["computer"]

    monkeypatch.setattr(inside, "availability", lambda dll_dir=None, platform=None: (folder, ""))
    monkeypatch.setattr(inside, "open_computer", open_computer)
    monkeypatch.setattr(inside, "pawnio_version", lambda: "2.0.1")
    monkeypatch.setattr("libre_panel.autostart.is_admin", lambda: True)
    return opened


def test_the_check_opens_the_library_and_reports(library):
    worked, lines = inside.check()
    assert worked
    assert lines[1] == f"libraries found: {len(inside.LIBRARIES)} of {len(inside.LIBRARIES)}"
    assert "opened: 5 devices with 11 sensors" in lines
    assert "administrator rights: yes" in lines and "PawnIO driver: 2.0.1" in lines
    assert "CPU temperature, power and clock: available" in lines
    assert library["computer"].closed == 1


def test_the_check_needs_no_rights_and_does_not_mind_a_cpu_it_cannot_read(library, monkeypatch):
    monkeypatch.setattr("libre_panel.autostart.is_admin", lambda: False)
    monkeypatch.setattr(inside, "pawnio_version", lambda: None)
    library["computer"] = ring0_off(small_computer())
    worked, lines = inside.check()
    assert worked  # a build must not fail for this
    assert "administrator rights: no" in lines and "PawnIO driver: not installed" in lines
    assert "CPU temperature, power and clock: not available" in lines


def test_the_check_fails_for_a_library_that_cannot_be_opened(library):
    library["error"] = OSError("Could not load file or assembly 'BlackSharp.Core'")
    worked, lines = inside.check()
    assert not worked
    assert lines[-1].startswith("could not be opened: OSError: Could not load file or assembly")


def test_the_check_fails_where_the_libraries_are_not_there(monkeypatch):
    monkeypatch.setattr(inside, "availability", lambda dll_dir=None, platform=None: (None, "no"))
    assert inside.check() == (False, ["LibreHardwareMonitor cannot run inside Libre Panel: no"])


def test_the_check_goes_on_when_one_chip_cannot_be_updated(library):
    library["computer"] = small_computer()
    library["computer"].Hardware[0].SubHardware[0].raises = RuntimeError("no port")
    worked, lines = inside.check()
    assert worked and "opened: 5 devices with 11 sensors" in lines


def test_the_check_on_the_command_line(isolated_home, library, capsys):
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[sensors.librehardwaremonitor]\ndll_dir = "my libs"\n')
    assert cli.main(["--config", str(path), "sensors", "--check-lhm"]) == 0
    out = capsys.readouterr().out
    assert "opened: 5 devices" in out and "administrator rights: yes" in out
    library["error"] = RuntimeError("boom")
    assert cli.main(["--config", str(path), "sensors", "--check-lhm"]) == 1
    assert "boom" in capsys.readouterr().out


def test_the_check_hands_the_dll_dir_of_the_config_on(isolated_home, monkeypatch, capsys):
    asked = []

    def check(dll_dir=None, platform=None, opener=None):
        asked.append(dll_dir)
        return False, ["nothing"]

    monkeypatch.setattr(inside, "check", check)
    named = isolated_home / "named.toml"
    named.parent.mkdir(parents=True, exist_ok=True)
    named.write_text('[sensors.librehardwaremonitor]\ndll_dir = "my libs"\n')
    plain = isolated_home / "plain.toml"
    plain.write_text("")
    assert cli.main(["--config", str(named), "sensors", "--check-lhm"]) == 1
    assert cli.main(["--config", str(plain), "sensors", "--check-lhm"]) == 1
    assert asked == ["my libs", None]


# -- libre-panel doctor -----------------------------------------------------------------


class FakeProvider:
    def __init__(self, readings, status):
        self.readings, self.state = readings, status
        self.waited = []
        self.closed = False

    def wait_ready(self, timeout):
        self.waited.append(timeout)
        return True

    def read(self):
        return self.readings

    def status(self):
        return self.state

    def close(self):
        self.closed = True


def check_with(isolated_home, readings, status, text=""):
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[sensors]\nproviders = ["load", "librehardwaremonitor"]\n' + text)
    made = []

    def make(options):
        made.append(options)
        return FakeProvider(readings, status)

    result = doctor.sensors_check(path, "win32", provider=make)
    return result, made


def test_doctor_asks_inside_libre_panel_first(isolated_home, possible):
    gpu = {"gpu.temp": Reading("gpu.temp", 50.0), "cpu.temp": Reading("cpu.temp", 60.0)}
    result, made = check_with(isolated_home, gpu, lhm.LhmStatus("inprocess", True, True, "2.0.1"))
    assert result == (
        "info",
        "LibreHardwareMonitor runs inside Libre Panel: temperature and power arrive",
    )
    assert made == [{}]


def test_doctor_tells_when_the_graphics_card_arrives_but_not_the_cpu(isolated_home, possible):
    gpu = {"gpu.temp": Reading("gpu.temp", 50.0)}
    status = lhm.LhmStatus("inprocess", False, False, None)
    (result, said), _ = check_with(isolated_home, gpu, status)
    assert result == "info"
    assert said == (
        "LibreHardwareMonitor runs inside Libre Panel: temperature and power arrive, "
        f"but not the CPU's: {ADMIN_STEP} and {PAWNIO_STEP}"
    )


def test_doctor_names_what_to_do_when_nothing_arrives(isolated_home, possible):
    status = lhm.LhmStatus("inprocess", False, False, "2.0.1")
    (result, said), _ = check_with(isolated_home, {}, status)
    assert result == "info" and said.startswith("No CPU or GPU temperature or power readings.")
    assert ADMIN_STEP in said


def test_doctor_waits_for_it_and_lets_it_go(isolated_home, possible):
    provider = FakeProvider({}, lhm.LhmStatus("inprocess", True, True, "2.0.1"))
    doctor.sensors_check(isolated_home / "config.toml", "win32", provider=lambda options: provider)
    assert provider.waited == [inside.READY_SECONDS] and provider.closed


def test_doctor_goes_to_the_web_server_where_it_cannot_be_inside(
    isolated_home, impossible, monkeypatch
):
    asked = []

    def refuse(url, timeout=1.0):
        asked.append(url)
        raise OSError("connection refused")

    assert doctor.sensors_check(None, "win32", lambda url: TREE)[1].startswith(
        "LibreHardwareMonitor answers"
    )
    monkeypatch.setattr(lhm, "fetch_tree", refuse)
    result, said = doctor.sensors_check(
        None, "win32", provider=lambda options: pytest.fail("not inside")
    )
    assert result == "info" and "does not answer" in said and "Remote Web Server" in said
    assert asked == [URL]


def test_doctor_says_why_when_the_mode_is_inside_only(isolated_home, impossible):
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[sensors.librehardwaremonitor]\nmode = "inprocess"\n')
    result, said = doctor.sensors_check(path, "win32")
    assert result == "info"
    assert said == f"LibreHardwareMonitor cannot run inside Libre Panel: {impossible[0]}"


def test_doctor_in_the_web_server_mode_asks_only_it(isolated_home, monkeypatch):
    monkeypatch.setattr(inside, "availability", lambda *a, **k: pytest.fail("looked"))
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[sensors.librehardwaremonitor]\nmode = "webserver"\n')
    asked = []

    def refuse(url, timeout=1.0):
        asked.append(url)
        raise OSError("connection refused")

    monkeypatch.setattr(lhm, "fetch_tree", refuse)
    assert doctor.sensors_check(path, "win32")[1].startswith("LibreHardwareMonitor does not")
    assert asked == [URL]


def test_a_fake_computer_is_enough_for_the_real_provider(monkeypatch):
    """The provider and the source together, on a fake computer, as the panel runs them."""
    computer = small_computer()
    monkeypatch.setattr(inside, "availability", lambda *a, **k: (Path("libraries"), ""))
    monkeypatch.setattr(inside, "open_computer", lambda folder: computer)
    provider = provider_with(poll_seconds=0.1)
    try:
        assert provider.wait_ready(10)
        read = provider.read()
        assert read["gpu.temp"].value == 48.0 and read["cpu.temp"].value == 55.0
        assert provider.status().mode == "inprocess" and provider._thread is None
    finally:
        provider.close()
    assert computer.closed == 1

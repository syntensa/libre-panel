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


def test_a_download_that_cannot_use_its_library_is_a_fault_but_pip_without_one_is_not(
    impossible, web_server, monkeypatch
):
    """The Windows download carries LibreHardwareMonitor: where it cannot be used the install
    is broken and the status says why. With pip the web server is the way, no fault."""
    pip = provider_with()
    try:
        assert pip.status() == lhm.LhmStatus("webserver")
    finally:
        pip.close()
    monkeypatch.setattr(lhm.sys, "frozen", True, raising=False)
    download = provider_with()
    try:
        assert download.status() == lhm.LhmStatus("webserver", error=impossible[0])
    finally:
        download.close()
    web = provider_with(mode="webserver")  # asked for: nothing is wrong
    try:
        assert web.status() == lhm.LhmStatus("webserver")
    finally:
        web.close()


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


def test_the_status_after_a_start_that_failed_depends_on_the_mode(possible, web_server):
    auto = provider_with()
    possible.sources[0].state = lhm.LhmStatus(
        "inprocess", error="pythonnet already runs on CoreCLR"
    )
    assert auto.status() == lhm.LhmStatus("webserver", error="pythonnet already runs on CoreCLR")
    said = hint(auto.status())  # the steps for the web server, and why not inside
    assert "Options -> Remote Web Server -> Run" in said and said.endswith(
        "(Inside Libre Panel it could not start: pythonnet already runs on CoreCLR.)"
    )
    auto.close()
    strict = provider_with(mode="inprocess")
    possible.sources[-1].state = lhm.LhmStatus(
        "inprocess", error="pythonnet already runs on CoreCLR"
    )
    assert strict.status().mode == "inprocess"  # the problem as it is, no web server to turn to
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


def test_a_dll_dir_that_is_no_folder_name_is_left_out(possible, caplog):
    for value, passed in (("", None), (None, None), (5, None), (["x"], None), ("libs", "libs")):
        possible.dll_dirs.clear()
        provider_with(dll_dir=value).close()
        assert possible.dll_dirs == [passed], value
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(said) == 2 and "dll_dir must be a folder name, not 5" in said[0]


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


# -- the same, for the tray and the editor ---------------------------------------------------

BOTH = "CPU temperature and power need administrator rights and the PawnIO driver (pawnio.eu)."
ADMIN = "CPU temperature and power need administrator rights: run Libre Panel as administrator."
DRIVER = "CPU temperature and power need the PawnIO driver: install it from pawnio.eu."
OLD = "CPU temperature and power need PawnIO 2 or newer: update it from pawnio.eu."
UNSTARTED = (
    "Temperatures and power are missing: their reader did not start. "
    "Reinstall Libre Panel; the log says why."
)
SERVER = (
    "Temperatures and power need LibreHardwareMonitor: start it as administrator "
    "and turn on Options > Remote Web Server > Run."
)
GPU_ONLY = {"gpu.temp": Reading("gpu.temp", 50.0)}  # the card's arrive without rights


def user(status, readings=None, platform="win32", ready=True):
    return lhm.user_hint(GPU_ONLY if readings is None else readings, platform, status, ready)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (lhm.LhmStatus("inprocess", False, False, None), BOTH),
        (lhm.LhmStatus("inprocess", False, False, "2.0.1"), ADMIN),
        (lhm.LhmStatus("inprocess", False, False, ""), ADMIN),  # a driver that says no version
        (lhm.LhmStatus("inprocess", True, False, None), DRIVER),
        (lhm.LhmStatus("inprocess", True, False, "1.4.0"), OLD),
        (lhm.LhmStatus("inprocess", False, False, "1.4.0"), BOTH),
    ],
)
def test_the_user_is_told_what_keeps_the_cpu_out_of_reach(status, expected):
    """The graphics card's readings arrive, the processor's do not: say what to do about it,
    in a sentence a person can follow (no command line, no setting)."""
    assert user(status) == expected
    assert "libre-panel" not in expected and "config" not in expected


@pytest.mark.parametrize(
    "status",
    [
        lhm.LhmStatus("inprocess"),  # still starting: nothing is known yet
        lhm.LhmStatus("inprocess", False, None, None),  # the CPU's sensors are not there at all
        lhm.LhmStatus("inprocess", False, True, None),  # in reach
        lhm.LhmStatus("inprocess", True, False, "2.0.1"),  # nothing a person could do
    ],
)
def test_nothing_is_said_where_nothing_can_be_done_or_is_not_known_yet(status):
    assert user(status) == ""


def test_nothing_is_said_off_windows_while_starting_or_when_the_readings_are_there():
    out_of_reach = lhm.LhmStatus("inprocess", False, False, None)
    assert user(out_of_reach, platform="linux") == "" and user(out_of_reach, ready=False) == ""
    cpu = {"cpu.temp": Reading("cpu.temp", 45.0)}  # a web server as administrator filled it in
    assert user(out_of_reach, cpu) == ""
    assert user(lhm.LhmStatus("webserver"), GPU_ONLY) == ""
    assert user(lhm.LhmStatus("webserver", error="boom"), GPU_ONLY) == ""


def test_when_nothing_arrives_the_user_is_told_to_reinstall_or_to_start_the_web_server():
    none = {}
    assert (
        user(lhm.LhmStatus("webserver", error="LibreHardwareMonitorLib.dll was not found"), none)
        == UNSTARTED
    )
    assert user(lhm.LhmStatus("inprocess", error="pythonnet could not start"), none) == UNSTARTED
    assert user(lhm.LhmStatus("webserver"), none) == SERVER
    assert user(None, none) == SERVER  # a source that does not say how it is read
    # the web server's text is not the CLI's: no address, no setting to edit
    assert "127.0.0.1" not in SERVER and "config.toml" not in SERVER


def test_the_hint_speaks_the_language_of_the_user():
    from libre_panel import i18n

    i18n.set_language("de")
    assert user(lhm.LhmStatus("inprocess", False, False, None)) == (
        "CPU-Temperatur und -Leistung brauchen Administratorrechte und den PawnIO-Treiber "
        "(pawnio.eu)."
    )
    assert user(lhm.LhmStatus("inprocess", False, False, "2.0")) == (
        "CPU-Temperatur und -Leistung brauchen Administratorrechte: "
        "Libre Panel als Administrator ausführen."
    )
    assert user(lhm.LhmStatus("inprocess", True, False, None)).endswith(
        "von pawnio.eu installieren."
    )
    assert user(lhm.LhmStatus("inprocess", True, False, "1.4")).endswith(
        "den Treiber von pawnio.eu aktualisieren."
    )
    assert user(lhm.LhmStatus("webserver"), {}).startswith("Temperaturen und Leistung brauchen")
    assert user(lhm.LhmStatus("webserver", error="x"), {}) == (
        "Temperaturen und Leistung fehlen: Das Auslesen ist nicht gestartet. "
        "Libre Panel neu installieren; das Protokoll nennt den Grund."
    )


def test_the_run_status_hint_follows_the_source_that_runs_inside(possible, web_server):
    """What ``sensor_hint`` makes of the hub: asked again with each look, never before the source
    has started, and gone as soon as the source gives the readings, whatever the last sample."""
    from libre_panel import app

    provider = provider_with()
    (source,) = possible.sources
    source.state = lhm.LhmStatus("inprocess", False, False, "2.0.1")
    hub = SensorHub([provider])
    try:
        source.ready = False  # .NET is still loading
        assert app.sensor_hint(hub, {}, "win32") == ""
        source.ready = True
        assert app.sensor_hint(hub, {}, "win32") == ADMIN
        assert app.sensor_hint(hub, {}, "linux") == app.sensor_hint(None, {}, "win32") == ""
        assert app.sensor_hint(SensorHub([]), {}, "win32") == ""  # not in the list of sources
        source.state = lhm.LhmStatus("inprocess", True, False, None)
        assert app.sensor_hint(hub, GPU_ONLY, "win32") == DRIVER
        source.readings = {"cpu.temp": Reading("cpu.temp", 44.0, "°C")}  # in reach at last
        assert app.sensor_hint(hub, GPU_ONLY, "win32") == ""
    finally:
        provider.close()


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


def test_a_live_preview_waits_for_the_source_too(isolated_home, possible, monkeypatch, tmp_path):
    provider = provider_with()
    (source,) = possible.sources
    source.readings = {"gpu.temp": Reading("gpu.temp", 50.0, "°C", "GPU temp")}
    waited = []
    source.wait_ready = lambda timeout: waited.append(timeout) or True
    monkeypatch.setattr(
        "libre_panel.app.build_hub", lambda config, demo=False: SensorHub([provider])
    )
    picture = tmp_path / "live.png"
    assert cli.main(["preview", "libre-default", "-o", str(picture), "--live"]) == 0
    assert picture.is_file() and waited == [inside.READY_SECONDS] and source.closed


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

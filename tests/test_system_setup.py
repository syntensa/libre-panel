"""``libre-panel system-setup`` with a Windows of its own (``FakeSystem``): the sensor service
copied, registered and started, PawnIO, the group for game frame rates, and taking it all back;
the command, the UAC run behind "Unlock all sensors", and the app's route for it."""

import hashlib
import json
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest
from fake_sensors import wait_for
from test_editor_server import request

from libre_panel import bundled, cli
from libre_panel.editor.server import make_server
from libre_panel.sensors import lhm_service
from libre_panel.sensorservice import SERVICE_EXE, SERVICE_NAME, setup

ME = "S-1-5-21-1111-2222-3333-1001"
SC = r"C:\Windows\System32\sc.exe"
ICACLS = r"C:\Windows\System32\icacls.exe"


class FakeSystem:
    """A Windows with a Service Control Manager, groups and a registry of its own; ``codes``
    makes an sc.exe verb end with that code."""

    def __init__(self, root):
        self.root = root / "Program Files"
        self.root.mkdir(parents=True)
        self.installed = None  # the service: setup.ServiceInfo
        self.calls = []
        self.codes = {}
        self.pawnio = None
        self.installer_code = 0
        self.members = set()
        self.recorded = []
        self.user = ME
        self.slept = 0.0
        self.lingers = 0  # how often the service's process is still there after it stopped

    def program_files(self):
        return self.root

    def tool(self, name):
        return rf"C:\Windows\System32\{name}"

    def service(self):
        if self.installed is None:
            return None
        return setup.ServiceInfo(self.installed.state, self.installed.pid, self.installed.program)

    def run(self, args, timeout=120.0):
        self.calls.append(list(args))
        program = args[0].rsplit("\\", 1)[-1].lower()
        if program == "icacls.exe":
            return 0
        if program != "sc.exe":  # PawnIO's installer
            if self.installer_code in (0, setup.RESTART_NEEDED):
                self.pawnio = "2.2.0"
            return self.installer_code
        verb = args[1]
        if verb in self.codes:
            return self.codes[verb]
        service = self.installed
        if verb == "create":
            if service is not None:
                return setup.SC_EXISTS
            self.installed = setup.ServiceInfo("stopped", 0, args[4])
        elif service is None:
            return setup.SC_MISSING
        elif verb == "config":
            service.program = args[4]
        elif verb == "start":
            if service.state == "running":
                return setup.SC_RUNNING
            service.state, service.pid = "running", 4242
        elif verb == "stop":
            if service.state == "stopped":
                return setup.SC_NOT_STARTED
            service.state, service.pid = "stopped", 0
        elif verb == "delete":
            self.installed = None
        return 0

    def pawnio_version(self):
        return self.pawnio

    def group_add(self, group, member):
        assert group == setup.PERF_LOG_USERS
        if member in self.members:
            return False
        self.members.add(member)
        return True

    def group_remove(self, group, member):
        if member not in self.members:
            return False
        self.members.discard(member)
        return True

    def group_members(self, group):
        return set(self.members)

    def session_user(self):
        return self.user

    def added(self):
        return list(self.recorded)

    def set_added(self, sids):
        self.recorded = list(sids)

    def alive(self, pid):
        assert pid == 4242
        if self.lingers:
            self.lingers -= 1
            return True
        return False

    def sleep(self, seconds):
        self.slept += seconds

    def verbs(self):
        return [call[1] for call in self.calls if call[0] == SC]


@pytest.fixture
def system(tmp_path):
    return FakeSystem(tmp_path)


@pytest.fixture
def download(tmp_path):
    """The service's folder as the portable download carries it."""
    folder = tmp_path / "zip" / "Libre Panel" / "sensors"
    (folder / "_internal" / "lhm").mkdir(parents=True)
    (folder / SERVICE_EXE).write_bytes(b"MZ service")
    (folder / "_internal" / "lhm" / "LibreHardwareMonitorLib.dll").write_bytes(b"MZ lhm")
    return folder


def target(system):
    return system.root / "Libre Panel" / "sensors"


# -- the service -----------------------------------------------------------------------------------


def test_the_service_is_copied_where_only_administrators_write_registered_and_started(
    system, download
):
    out = setup.apply(system, service=True, source=download)
    assert not out.failed, out.lines
    assert (target(system) / SERVICE_EXE).read_bytes() == b"MZ service"
    assert (target(system) / "_internal" / "lhm" / "LibreHardwareMonitorLib.dll").is_file()
    program = target(system) / SERVICE_EXE
    assert system.calls[0] == [
        ICACLS, str(target(system)), "/inheritance:r", "/grant:r",
        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX", "/Q",
    ]  # fmt: skip
    assert system.calls[1] == [
        SC, "create", SERVICE_NAME, "binPath=", f'"{program}"', "start=", "delayed-auto",
        "obj=", "LocalSystem", "DisplayName=", "Libre Panel sensors",
    ]  # fmt: skip
    assert system.verbs() == ["create", "description", "failure", "failureflag", "sdset", "start"]
    assert system.service().state == "running" and system.service().program == f'"{program}"'
    assert out.lines[0] == f"copied the sensor service to {target(system)}"
    assert out.lines[-1] == "the sensor service runs"


def test_the_path_reaches_sc_exe_quoted_inside_the_value():
    """subprocess quotes each argument; the path's own quotes go through as \\" (sc.exe reads
    its arguments as C programs do), so Windows starts exactly this program."""
    args = setup.sc_create(SC, Path(r"C:\Program Files\Libre Panel\sensors") / SERVICE_EXE)
    line = subprocess.list2cmdline(args)
    expected = r'binPath= "\"C:\Program Files\Libre Panel\sensors' + "/" + SERVICE_EXE + r'\""'
    if sys.platform == "win32":
        expected = expected.replace("/", "\\")
    assert expected in line
    assert args[args.index("start=") + 1] == "delayed-auto"
    assert args[args.index("obj=") + 1] == "LocalSystem"


def test_what_the_service_may_and_whom_it_restarts_for():
    settings = {args[1]: args[3:] for args in setup.sc_settings(SC)}
    assert settings["failure"] == [
        "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/60000",
    ]  # fmt: skip
    assert settings["failureflag"] == ["1"]
    sddl = settings["sdset"][0]
    aces = dict(ace.split(";;;")[::-1] for ace in sddl[2:].strip("()").split(")("))
    assert aces["IU"] == "A;;CCLCSWRPLOCRRC"  # people signed in may start it (RP), not stop it
    assert "WP" not in aces["IU"] and "DC" not in aces["IU"] and "WD" not in aces["IU"]
    assert aces["BA"].endswith("RCWDWO") and "S:" not in sddl  # the DACL only


def test_set_up_again_it_is_brought_up_to_date(system, download):
    setup.apply(system, service=True, source=download)
    (download / SERVICE_EXE).write_bytes(b"MZ newer")
    system.calls.clear()
    out = setup.apply(system, service=True, source=download)
    assert not out.failed
    assert system.verbs() == [
        "stop",
        "config",
        "description",
        "failure",
        "failureflag",
        "sdset",
        "start",
    ]
    assert (target(system) / SERVICE_EXE).read_bytes() == b"MZ newer"
    assert not (target(system).parent / "sensors.old").exists()


def test_where_the_setup_put_it_under_program_files_nothing_is_copied(system):
    installed = target(system)
    installed.mkdir(parents=True)
    (installed / SERVICE_EXE).write_bytes(b"MZ from the setup")
    out = setup.apply(system, service=True, source=installed)
    assert not out.failed and "copied" not in " ".join(out.lines)
    assert system.verbs()[0] == "create"
    assert (installed / SERVICE_EXE).read_bytes() == b"MZ from the setup"


def test_a_pip_install_has_no_service_to_set_up(system):
    out = setup.apply(system, service=True, source=None)
    assert out.failed and "comes with the Windows downloads" in out.lines[0]
    assert system.calls == []


def test_a_service_that_does_not_start_is_said(system, download):
    system.codes["start"] = 1053
    out = setup.apply(system, service=True, source=download)
    assert (
        out.failed
        and out.lines[-1]
        == "error: the sensor service: the sensor service did not start (sc.exe: 1053)"
    )


def test_a_service_that_stays_starting_is_said_with_where_its_log_is(system, download):
    system.codes["start"] = 0  # sc.exe says yes, the service never runs
    out = setup.apply(system, service=True, source=download)
    assert out.failed and "did not start in time" in out.lines[-1] and "logs" in out.lines[-1]
    assert system.slept >= setup.STATE_SECONDS


def test_removing_it_stops_and_deletes_it_and_its_files(system, download):
    setup.apply(system, service=True, source=download)
    system.calls.clear()
    out = setup.apply(system, service=True, remove=True)
    assert not out.failed and system.verbs() == ["stop", "delete"]
    assert system.service() is None
    assert not target(system).exists() and not target(system).parent.exists()
    again = setup.apply(system, service=True, remove=True)  # nothing there: nothing to do
    assert not again.failed and again.lines == []


def test_its_files_are_replaced_only_once_its_process_let_go_of_them(system, download):
    setup.apply(system, service=True, source=download)
    system.lingers = 3  # the process ends a moment after the service says it stopped
    out = setup.apply(system, service=True, source=download)
    assert not out.failed and system.lingers == 0 and system.slept == 1.5


def test_the_files_of_the_setup_are_left_to_its_uninstaller(system):
    installed = target(system)
    installed.mkdir(parents=True)
    (installed / SERVICE_EXE).write_bytes(b"MZ")
    (installed.parent / "unins000.exe").write_bytes(b"MZ")
    setup.apply(system, service=True, source=installed)
    out = setup.apply(system, service=True, remove=True)
    assert not out.failed and system.service() is None and (installed / SERVICE_EXE).exists()


# -- PawnIO ----------------------------------------------------------------------------------------


def test_pawnio_is_installed_silently_from_the_download(system, tmp_path):
    installer = tmp_path / "PawnIO_setup.exe"
    out = setup.apply(system, pawnio=True, installer=installer)
    assert not out.failed and out.pawnio_installed
    assert system.calls == [[str(installer), "-install", "-silent"]]
    assert out.lines == ["installed PawnIO"]


def test_pawnio_2_is_left_as_it_is_and_an_old_one_is_replaced(system, tmp_path):
    system.pawnio = "2.0.1"
    out = setup.apply(system, pawnio=True, installer=tmp_path / "PawnIO_setup.exe")
    assert out.lines == ["PawnIO 2.0.1 is installed"] and system.calls == []
    system.pawnio = "1.4.0"
    setup.apply(system, pawnio=True, installer=tmp_path / "PawnIO_setup.exe")
    assert len(system.calls) == 1


def test_pawnio_that_wants_a_restart_has_worked(system, tmp_path):
    system.installer_code = setup.RESTART_NEEDED
    out = setup.apply(system, pawnio=True, installer=tmp_path / "PawnIO_setup.exe")
    assert not out.failed and out.lines == ["installed PawnIO: Windows needs a restart to finish"]


def test_pawnio_that_fails_or_is_not_there_is_said(system, tmp_path):
    system.installer_code = 1603
    out = setup.apply(system, pawnio=True, installer=tmp_path / "PawnIO_setup.exe")
    assert out.failed and out.lines == ["error: PawnIO: PawnIO's installer ended with 1603"]
    out = setup.apply(system, pawnio=True, installer=None)
    assert out.failed and "https://pawnio.eu" in out.lines[0]


def test_pawnio_comes_first_and_a_running_service_starts_again_to_see_it(
    system, download, tmp_path
):
    setup.apply(system, service=True, source=download)
    system.calls.clear()
    installed = target(system)
    out = setup.apply(
        system, service=True, pawnio=True, source=installed, installer=tmp_path / "PawnIO_setup.exe"
    )
    assert not out.failed
    assert system.calls[0][1:] == ["-install", "-silent"]
    assert system.verbs() == [
        "config",
        "description",
        "failure",
        "failureflag",
        "sdset",
        "stop",
        "start",
    ]


def test_pawnio_is_not_removed(system):
    out = setup.apply(system, pawnio=True, remove=True)
    assert not out.failed and "PawnIO stays" in out.lines[0] and system.calls == []


# -- Performance Log Users -------------------------------------------------------------------------


def test_the_person_signed_in_joins_performance_log_users_once(system):
    out = setup.apply(system, perf_log_users=True)
    assert not out.failed and system.members == {ME} and system.recorded == [ME]
    assert "next sign-in" in out.lines[0]
    again = setup.apply(system, perf_log_users=True)
    assert again.lines == ["already in Performance Log Users"] and system.recorded == [ME]


def test_the_person_can_be_named(system):
    other = "S-1-5-21-1111-2222-3333-1002"
    setup.apply(system, perf_log_users=True, user=other)
    assert system.members == {other}


def test_only_the_memberships_it_gave_are_taken_back(system):
    earlier = "S-1-5-21-1111-2222-3333-1003"
    system.members.add(earlier)  # was in the group before
    setup.apply(system, perf_log_users=True)
    out = setup.apply(system, perf_log_users=True, remove=True)
    assert not out.failed and system.members == {earlier} and system.recorded == []


def test_nobody_signed_in_is_an_error(system):
    system.user = None
    out = setup.apply(system, perf_log_users=True)
    assert out.failed and "--user-sid" in out.lines[0]


def test_a_step_that_fails_does_not_keep_the_others_from_running(system, download):
    out = setup.apply(system, service=True, pawnio=True, perf_log_users=True, source=download)
    assert out.failed and out.lines[0].startswith("error: PawnIO:")  # no installer here
    assert system.service().state == "running" and system.members == {ME}


# -- what came with the program --------------------------------------------------------------------


def frozen(monkeypatch, folder):
    monkeypatch.setattr(setup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(setup.sys, "executable", str(folder / "LibrePanel.exe"))


def test_the_download_carries_the_service_and_pawnios_installer(monkeypatch, tmp_path):
    assert setup.bundled_service() is None and setup.program_folder() is None  # (a pip install)
    program = tmp_path / "Libre Panel"
    (program / "sensors").mkdir(parents=True)
    (program / "tools").mkdir()
    frozen(monkeypatch, program)
    assert setup.bundled_service() is None  # the folder without its program
    (program / "sensors" / SERVICE_EXE).write_bytes(b"MZ")
    assert setup.bundled_service() == program / "sensors"
    assert setup.pawnio_installer() is None
    (program / "tools" / "PawnIO_setup.exe").write_bytes(b"MZ")
    assert setup.pawnio_installer() is None  # not the file that was fetched
    monkeypatch.setattr(bundled, "PAWNIO_SETUP_SHA256", hashlib.sha256(b"MZ").hexdigest())
    assert setup.pawnio_installer() == program / "tools" / "PawnIO_setup.exe"
    assert setup.cli_program() is None
    (program / "libre-panel.exe").write_bytes(b"MZ")
    assert setup.cli_program() == program / "libre-panel.exe"


def test_unlocking_is_offered_where_the_download_has_the_service_and_windows_does_not(
    monkeypatch, tmp_path
):
    program = tmp_path / "Libre Panel"
    (program / "sensors").mkdir(parents=True)
    (program / "sensors" / SERVICE_EXE).write_bytes(b"MZ")
    frozen(monkeypatch, program)
    assert setup.unlock_offered("win32") and not setup.unlock_offered("linux")
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: {"Start": 2})
    assert not setup.unlock_offered("win32")
    monkeypatch.setattr(lhm_service, "registration", lambda name=None: {"Start": 4})
    assert setup.unlock_offered("win32")  # switched off: setting it up again switches it on


# -- the command -----------------------------------------------------------------------------------


def arguments(*flags, user=None):
    names = ("service", "pawnio", "perf_log_users", "remove", "status")
    given = {name: f"--{name.replace('_', '-')}" in flags for name in names}
    return types.SimpleNamespace(user_sid=user, **given)


def test_the_command_needs_windows_and_an_administrator(system, capsys):
    assert setup.run_command(arguments("--service"), system, lambda: True, "linux") == 1
    code = setup.run_command(arguments("--service"), system, lambda: False, "win32")
    assert code == setup.EXIT_NOT_ADMIN and "Run as administrator" in capsys.readouterr().err
    assert system.calls == []


def test_the_command_says_what_it_did(system, download, monkeypatch, capsys):
    monkeypatch.setattr(setup, "bundled_service", lambda: download)
    code = setup.run_command(
        arguments("--service", "--perf-log-users"), system, lambda: True, "win32"
    )
    out = capsys.readouterr()
    assert code == 0 and "the sensor service runs" in out.out and out.err == ""
    code = setup.run_command(arguments("--pawnio"), system, lambda: True, "win32")
    assert code == 1 and "error: PawnIO" in capsys.readouterr().err


def test_the_command_wants_to_be_told_what_and_takes_only_a_sid(system, capsys):
    assert setup.run_command(arguments(), system, lambda: True, "win32") == 1
    assert "--service" in capsys.readouterr().err
    assert (
        setup.run_command(arguments("--perf-log-users", user="me"), system, lambda: True, "win32")
        == 1
    )
    assert "is not a SID" in capsys.readouterr().err


def test_remove_alone_removes_the_service(system, download):
    setup.apply(system, service=True, source=download)
    assert setup.run_command(arguments("--remove"), system, lambda: True, "win32") == 0
    assert system.service() is None


def test_the_status_needs_no_rights(system, capsys):
    system.pawnio = "2.0.1"
    assert setup.run_command(arguments("--status"), system, lambda: False, "win32") == 0
    said = json.loads(capsys.readouterr().out)
    assert said["service"] is None and said["pawnio"] == "2.0.1" and said["perf_log_users"] is False


def test_system_setup_on_the_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr(setup, "run_command", lambda args, platform=None: seen.append(args) or 0)
    assert cli.main(["system-setup", "--service", "--pawnio", "--user-sid", ME]) == 0
    (args,) = seen
    assert args.service and args.pawnio and not args.perf_log_users and args.user_sid == ME


# -- the UAC run behind "Unlock all sensors" -------------------------------------------------------


def test_the_run_asks_windows_and_keeps_how_it_ended(tmp_path):
    started = threading.Event()
    launched = []

    def launch(program, parameters):
        launched.append((program, parameters))
        started.wait(10)
        return 0

    elevator = setup.Elevator(launch)
    job = elevator.start("unlock", tmp_path / "libre-panel.exe", ME)
    assert job.running and elevator.start("remove", tmp_path / "x.exe", ME) is job  # one at a time
    started.set()
    assert wait_for(lambda: not job.running)
    assert job.as_dict() == {
        "action": "unlock", "running": False, "code": 0, "cancelled": False, "error": "",
    }  # fmt: skip
    assert launched == [
        (
            str(tmp_path / "libre-panel.exe"),
            f"system-setup --service --pawnio --perf-log-users --user-sid {ME}",
        )
    ]


def test_a_no_to_windows_question_is_kept_apart_from_a_failure(tmp_path):
    from libre_panel.sensorservice.winapi import Cancelled

    def refuse(program, parameters):
        raise Cancelled

    job = setup.Elevator(refuse).start("remove", tmp_path / "libre-panel.exe", None)
    assert wait_for(lambda: not job.running) and job.cancelled and job.code is None

    def broken(program, parameters):
        raise OSError("no shell")

    job = setup.Elevator(broken).start("unlock", tmp_path / "libre-panel.exe", None)
    assert wait_for(lambda: not job.running) and job.error == "no shell" and not job.cancelled
    with pytest.raises(ValueError):
        setup.Elevator(broken).start("format", tmp_path / "libre-panel.exe", None)


# -- the app's route -------------------------------------------------------------------------------


@pytest.fixture
def server(isolated_home, monkeypatch):
    # The route answers for the system it runs on: these tests are about the sensor service, so
    # the system is not Linux, which has a setup of its own (test_setup_api.py).
    monkeypatch.setattr("libre_panel.linux_setup.supported", lambda: False)
    made = make_server(port=0)
    threading.Thread(target=made.serve_forever, daemon=True).start()
    yield made.server_address[1]
    made.shutdown()
    made.server_close()
    made.editor_state.close()


def test_the_route_says_that_only_a_windows_download_can_unlock(server):
    status, data = request(server, "GET", "/api/unlock")
    assert status == 200 and data["available"] is False and data["job"] is None
    assert data["command"].endswith("system-setup --service --pawnio --perf-log-users")
    status, data = request(server, "POST", "/api/unlock", {"action": "unlock"})
    assert status == 409 and "Windows downloads" in data["error"]
    status, data = request(server, "POST", "/api/unlock", {"action": "format"})
    assert status == 400


def test_the_route_starts_the_run_and_says_how_it_went(server, monkeypatch, tmp_path):
    monkeypatch.setattr(
        setup,
        "unlock_state",
        lambda platform=None: {
            "platform": "win32",
            "available": True,
            "service": None,
            "pawnio": None,
            "frame_rates": "no",
            "job": setup.ELEVATOR.job.as_dict() if setup.ELEVATOR.job else None,
        },
    )
    monkeypatch.setattr(setup, "cli_program", lambda: tmp_path / "libre-panel.exe")
    launched = []
    monkeypatch.setattr(setup, "ELEVATOR", setup.Elevator(lambda p, a: launched.append(a) or 0))
    status, data = request(server, "POST", "/api/unlock", {"action": "unlock"})
    assert status == 200 and data["job"]["action"] == "unlock"
    assert wait_for(lambda: not setup.ELEVATOR.job.running)
    status, data = request(server, "GET", "/api/unlock")
    assert data["job"] == {
        "action": "unlock", "running": False, "code": 0, "cancelled": False, "error": "",
    }  # fmt: skip
    assert launched[0].startswith("system-setup --service --pawnio --perf-log-users")

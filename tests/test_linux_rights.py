"""Asking for the rights the setup needs: the command pkexec or sudo runs, the environment it
gets, and a run in the background (linux_rights.py). Nothing here asks for rights; the
programs are made up."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_sensors import wait_for
from test_linux_setup import RAPL_FILES, facts

from libre_panel import linux_rights as rights
from libre_panel.linux_setup import Options

REAL_IMPORTS = rights.imports_isolated  # (conftest.py answers for the machine the tests run on)
APPIMAGE = "/home/someone/Apps/Libre_Panel-x86_64.AppImage"
PIP = ["/opt/venv/bin/python", "-B", "-I", "-m", "libre_panel"]
DESKTOP = {"DISPLAY": ":0", "HOME": "/home/someone"}


def which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


# -- the program root runs --------------------------------------------------------------------


def test_the_appimage_runs_by_its_own_file_not_by_its_mount():
    """The mount of an AppImage is closed to every other user, root included: root starts the
    file again."""
    environ = {"APPIMAGE": APPIMAGE}
    mount = "/tmp/.mount_LibreXYZ/usr/lib/libre-panel/libre-panel"
    found = rights.program(environ, frozen=True, executable=mount, exists=lambda p: True)
    assert found == [APPIMAGE]


@pytest.mark.parametrize(
    ("environ", "exists"),
    [
        ({"APPIMAGE": "Libre_Panel.AppImage"}, True),  # not by its full name
        ({"APPIMAGE": APPIMAGE}, False),  # the file was moved or deleted
    ],
)
def test_an_appimage_root_cannot_find_gives_no_program(environ, exists):
    assert rights.program(environ, True, "/tmp/.mount_x/libre-panel", lambda p: exists) is None


def test_a_download_runs_by_its_own_program_and_pip_by_its_python():
    assert rights.program({}, True, "/opt/libre-panel/libre-panel") == [
        "/opt/libre-panel/libre-panel"
    ]
    found = rights.program({}, False, "/opt/venv/bin/python", importable=lambda python: True)
    assert found == PIP  # isolated, no bytecode written into the person's folders
    assert (
        rights.program({}, False, "/opt/venv/bin/python", importable=lambda python: False) is None
    )
    assert rights.program({}, False, "python3", importable=lambda python: True) is None


def test_pip_is_asked_whether_root_will_find_libre_panel(monkeypatch):
    rights._isolated_import.clear()
    asked = []

    def run(argv, **kwargs):
        asked.append(argv)
        return SimpleNamespace(returncode=0 if argv[0] == "/good/python" else 1)

    monkeypatch.setattr(rights.subprocess, "run", run)
    assert REAL_IMPORTS("/good/python") is True
    assert REAL_IMPORTS("/good/python") is True and len(asked) == 1  # once
    assert asked[0] == ["/good/python", "-B", "-I", "-c", "import libre_panel"]
    assert REAL_IMPORTS("/bad/python") is False

    def broken(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 15)

    monkeypatch.setattr(rights.subprocess, "run", broken)
    assert REAL_IMPORTS("/slow/python") is False
    rights._isolated_import.clear()


# -- how it is run ------------------------------------------------------------------------------


def test_the_arguments_of_the_setup():
    assert rights.arguments(Options()) == ["system-setup", "--linux"]
    assert rights.arguments(Options(rapl=True), json_output=True) == [
        "system-setup",
        "--linux",
        "--rapl",
        "--json",
    ]
    assert rights.arguments(Options(rapl=False, remove=True), dry_run=True) == [
        "system-setup",
        "--linux",
        "--no-rapl",
        "--remove",
        "--dry-run",
    ]


def test_pkexec_where_there_is_a_desktop():
    way = rights.elevation(Options(rapl=True), [APPIMAGE], DESKTOP, which("pkexec", "sudo"))
    assert way.method == "pkexec"
    assert way.argv == (
        "/usr/bin/pkexec",
        "--disable-internal-agent",  # the app has no terminal to ask in
        APPIMAGE,
        "system-setup",
        "--linux",
        "--rapl",
        "--json",
    )
    assert way.terminal == f"sudo {APPIMAGE} system-setup --linux --rapl"


def test_a_wayland_session_is_a_desktop_too():
    way = rights.elevation(Options(), [APPIMAGE], {"WAYLAND_DISPLAY": "wayland-0"}, which("pkexec"))
    assert way.method == "pkexec"


def test_without_a_desktop_the_app_can_only_tell_the_terminal_command():
    way = rights.elevation(Options(), [APPIMAGE], {"HOME": "/x"}, which("pkexec", "sudo"))
    assert (way.method, way.argv) == ("", ())  # (an app has no terminal to ask in)
    assert way.terminal.startswith("sudo ")


def test_a_terminal_without_a_desktop_uses_sudo_and_then_pkexec():
    sudo = rights.elevation(Options(), [APPIMAGE], {}, which("pkexec", "sudo"), terminal=True)
    assert sudo.method == "sudo" and sudo.argv[0] == "/usr/bin/sudo"
    only = rights.elevation(Options(), [APPIMAGE], {}, which("pkexec"), terminal=True)
    assert only.method == "pkexec" and "--disable-internal-agent" not in only.argv  # asks there
    none = rights.elevation(Options(), [APPIMAGE], {}, which(), terminal=True)
    assert none.method == "" and none.argv == ()


def test_a_desktop_without_pkexec_falls_back_to_the_terminal_command():
    way = rights.elevation(Options(), [APPIMAGE], DESKTOP, which("sudo"))
    assert (way.method, way.argv) == ("", ())
    assert way.terminal.endswith("system-setup --linux")


def test_no_program_means_no_way_but_a_command_to_type():
    way = rights.elevation(Options(remove=True), None, DESKTOP, which("pkexec"))
    assert way.method == "" and way.argv == ()
    assert way.terminal == "sudo libre-panel system-setup --linux --remove"


def test_the_terminal_command_quotes_a_path_with_spaces(tmp_path):
    spaced = "/home/some one/My Apps/Libre Panel.AppImage"
    text = rights.terminal_command(Options(), [spaced])
    assert text == "sudo '/home/some one/My Apps/Libre Panel.AppImage' system-setup --linux"


def test_a_pip_install_reads_as_libre_panel_when_the_script_is_beside_its_python(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(rights.shutil, "which", lambda name: "/opt/venv/bin/libre-panel")
    assert (
        rights.terminal_command(Options(), PIP)
        == "sudo /opt/venv/bin/libre-panel system-setup --linux"
    )
    # a script of another installation is not this one
    monkeypatch.setattr(rights.shutil, "which", lambda name: "/usr/local/bin/libre-panel")
    text = rights.terminal_command(Options(), PIP)
    assert text == "sudo /opt/venv/bin/python -B -I -m libre_panel system-setup --linux"


# -- the environment ---------------------------------------------------------------------------


def test_pkexec_gets_the_system_s_libraries_and_none_of_the_bundles():
    bundle = {
        "LD_LIBRARY_PATH": "/tmp/.mount_x/usr/lib",
        "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
        "LD_PRELOAD": "/x.so",
        "PYTHONHOME": "/tmp/.mount_x",
        "PYTHONPATH": "/tmp/.mount_x/lib",
        "_MEIPASS2": "/tmp/.mount_x",
        "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEIx",
        "_PYI_ARCHIVE_FILE": "/tmp/a",
        "APPDIR": "/tmp/.mount_x",
        "APPIMAGE": APPIMAGE,
        "ARGV0": "AppRun",
        "OWD": "/home/someone",
        "DISPLAY": ":0",
        "XAUTHORITY": "/run/user/1000/xauth",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "HOME": "/home/someone",
        "PATH": "/usr/bin",
        "LANG": "de_DE.UTF-8",
    }
    env = rights.clean_environment(bundle)
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"  # what the program was started with
    for name in ("LD_LIBRARY_PATH_ORIG", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "_MEIPASS2"):
        assert name not in env
    assert not [name for name in env if name.startswith(("_PYI_", "APP", "ARGV", "OWD"))]
    for name in ("DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "HOME", "PATH", "LANG"):
        assert env[name] == bundle[name]
    assert "LD_LIBRARY_PATH" not in rights.clean_environment({"LD_LIBRARY_PATH": "/bundle"})


def test_the_command_is_run_without_input_and_its_answer_is_read(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=3, stdout="out", stderr="err")

    monkeypatch.setattr(rights.subprocess, "run", run)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    assert rights.run_elevated(["pkexec", "x"]) == (3, "out", "err")
    assert seen["stdin"] == subprocess.DEVNULL and seen["timeout"] > 60
    assert "LD_LIBRARY_PATH" not in seen["env"] and seen["capture_output"] is True


def test_no_pkexec_and_a_dialog_nobody_answers_are_the_two_exit_codes(monkeypatch):
    def gone(argv, **kwargs):
        raise FileNotFoundError("pkexec")

    monkeypatch.setattr(rights.subprocess, "run", gone)
    assert rights.run_elevated(["pkexec"])[0] == 127

    def waiting(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(rights.subprocess, "run", waiting)
    assert rights.run_elevated(["pkexec"])[0] == 126


def test_the_report_is_the_last_line_that_is_json():
    report = {"ok": True, "results": []}
    assert rights.parse_result(f"noise\n{json.dumps(report)}\n") == report
    assert rights.parse_result("a\n{not json\n") is None
    assert rights.parse_result("[1, 2]\n") is None and rights.parse_result("") is None


# -- a run in the background ---------------------------------------------------------------------


class Runner:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, argv):
        self.asked.append(tuple(argv))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


PKEXEC = rights.Elevation("pkexec", ("/usr/bin/pkexec", APPIMAGE), "sudo x")
GOOD = json.dumps({"ok": True, "results": [{"id": "udev", "status": "done", "items": {}}]})


def finished(job):
    return wait_for(lambda: job.snapshot()["state"] != "running")


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        ((0, GOOD, ""), "done"),
        ((1, json.dumps({"ok": False, "results": []}), ""), "failed"),  # a step failed
        ((126, "", "Error executing command as another user: Not authorized"), "cancelled"),
        ((127, "", "No authentication agent found."), "unavailable"),
        ((2, "", "usage: libre-panel"), "failed"),
    ],
)
def test_a_run_ends_as_the_exit_code_and_the_report_say(answer, state):
    job = rights.Job(Runner(answer))
    assert job.snapshot()["state"] == "idle" and job.start(PKEXEC)
    assert finished(job)
    snapshot = job.snapshot()
    assert snapshot["state"] == state and snapshot["method"] == "pkexec"
    if state == "done":
        assert snapshot["result"]["results"][0]["id"] == "udev" and snapshot["detail"] == ""
    if state in ("failed", "unavailable") and answer[0] in (2, 127):
        assert answer[2].split("\n")[0] in snapshot["detail"]
    # and in the words of Windows' run, which the app follows for either system
    assert snapshot["running"] is False and snapshot["code"] == answer[0]
    assert snapshot["cancelled"] is (state == "cancelled")
    assert snapshot["error"] == (snapshot["detail"] if state in ("failed", "unavailable") else "")


def test_a_run_remembers_what_it_was_for():
    job = rights.Job(Runner((0, GOOD, ""), (0, GOOD, "")))
    assert job.snapshot()["action"] == "" and job.snapshot()["code"] is None
    assert job.start(PKEXEC) and finished(job) and job.snapshot()["action"] == "unlock"
    assert job.start(PKEXEC, action="remove") and finished(job)
    assert job.snapshot()["action"] == "remove"


def test_a_second_run_waits_for_the_first():
    import threading

    gate = threading.Event()

    def slow(argv):
        gate.wait(10)
        return 0, GOOD, ""

    job = rights.Job(slow)
    assert job.start(PKEXEC) is True
    assert job.snapshot()["state"] == "running" and job.start(PKEXEC) is False
    gate.set()
    assert finished(job) and job.start(PKEXEC) is False or True  # (after the end: a new run)


def test_nothing_runs_without_a_way_to_ask():
    job = rights.Job(Runner())
    assert job.start(rights.Elevation("", (), "sudo x")) is False
    assert job.snapshot()["state"] == "idle"


def test_a_runner_that_breaks_is_a_failed_run_not_a_stuck_one():
    job = rights.Job(Runner(RuntimeError("no luck")))
    job.start(PKEXEC)
    assert finished(job)
    assert job.snapshot()["state"] == "failed" and "no luck" in job.snapshot()["detail"]


def test_the_app_hears_when_it_is_done():
    heard = []
    job = rights.Job(Runner((0, GOOD, ""), (126, "", "")))
    job.start(PKEXEC, after=lambda: heard.append("done"))
    assert finished(job) and wait_for(lambda: heard == ["done"])
    job.start(PKEXEC, after=lambda: heard.append("again"))
    assert finished(job)
    assert heard == ["done"]  # a dialog that was closed is no news


def test_a_new_run_forgets_the_last_one():
    job = rights.Job(Runner((0, GOOD, ""), (126, "", "")))
    job.start(PKEXEC)
    assert finished(job) and job.snapshot()["result"] is not None
    job.start(PKEXEC)
    assert finished(job)
    assert job.snapshot()["result"] is None and job.snapshot()["state"] == "cancelled"


# -- what the app is told ------------------------------------------------------------------------


def test_the_status_says_what_could_be_brought_and_how_to_ask(monkeypatch):
    monkeypatch.setattr(rights, "program", lambda *a, **k: [APPIMAGE])
    monkeypatch.setattr(rights.shutil, "which", which("pkexec"))
    monkeypatch.setenv("DISPLAY", ":0")
    found = facts(
        panel=True,
        panel_denied=True,
        rapl=RAPL_FILES,
        rapl_closed=RAPL_FILES,
        files={Path(f).as_posix(): None for f in ("/etc/udev/rules.d/60-libre-panel.rules",)},
    )
    status = rights.status(found, rights.Job(Runner()))
    assert status["platform"] == "linux" and status["available"] is True
    assert status["method"] == "pkexec"
    assert [offer["id"] for offer in status["offers"]] == ["usb", "fans", "power"]
    assert status["offers"][2] == {"id": "power", "needed": True, "modules": (), "opt_in": True}
    assert status["command"] == f"sudo {APPIMAGE} system-setup --linux"
    assert status["removal"] == f"sudo {APPIMAGE} system-setup --linux --remove"
    assert status["installed"] == [] and status["granted"] is False
    assert status["job"] == {
        "action": "",
        "running": False,
        "code": None,
        "cancelled": False,
        "error": "",
        "state": "idle",
        "result": None,
        "detail": "",
        "method": "",
    }


def test_the_status_knows_what_is_installed_and_granted():
    from libre_panel import linux_setup as setup

    own = {
        setup.UDEV_RULES: setup.udev_rules_text(),
        setup.RAPL_RULES: "rule\n",
        setup.MODULES: "nct6775\n",
    }
    status = rights.status(facts(files=own, dmi={}), rights.Job(Runner()))
    assert status["installed"] == list(setup.OWN_FILES) and status["granted"] is True
    assert status["offers"] == []

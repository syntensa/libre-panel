"""The app's route for the setup of Linux (``/api/setup``): what it tells, and the run that
the person starts from Start or Settings. No rights are asked for: the run is a stand-in."""

import json
import threading

import pytest
from fake_sensors import wait_for
from test_editor_server import request
from test_linux_rights import APPIMAGE, GOOD, Runner
from test_linux_setup import RAPL_FILES, facts

from libre_panel import linux_rights as rights
from libre_panel.editor import access
from libre_panel.editor.server import make_server


@pytest.fixture
def port():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    server.editor_state.close()


@pytest.fixture
def linux(monkeypatch):
    """A Linux desktop whose polkit can be asked, with a PC that has a fan chip to load and
    energy counters nobody may read."""
    monkeypatch.setattr("libre_panel.editor.settings.sys.platform", "linux")
    monkeypatch.setattr(rights, "program", lambda *a, **k: [APPIMAGE])
    monkeypatch.setattr(rights.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("DISPLAY", ":0")
    computer = facts(rapl=RAPL_FILES, rapl_closed=RAPL_FILES, panel=True, panel_denied=True)
    monkeypatch.setattr("libre_panel.linux_setup.gather", lambda *a, **k: computer)
    return computer


def run_with(monkeypatch, *answers):
    runner = Runner(*answers)
    monkeypatch.setattr(rights, "JOB", rights.Job(runner))
    return runner


def wait_done(port):
    assert wait_for(lambda: request(port, "GET", "/api/setup")[1]["job"]["state"] != "running")
    return request(port, "GET", "/api/setup")[1]


def test_off_linux_there_is_nothing_to_set_up(port, monkeypatch):
    monkeypatch.setattr("libre_panel.editor.settings.sys.platform", "win32")
    assert request(port, "GET", "/api/setup") == (200, {"supported": False})
    status, data = request(port, "POST", "/api/setup", {})
    assert status == 404 and "Linux" in data["error"]


def test_the_app_is_told_what_could_be_brought_and_how_to_ask(port, linux):
    status, data = request(port, "GET", "/api/setup")
    assert status == 200 and data["supported"] is True and data["method"] == "pkexec"
    assert [offer["id"] for offer in data["offers"]] == ["usb", "fans", "power"]
    assert data["offers"][0]["needed"] is True  # the panel is there, and may not be used
    assert data["offers"][1]["modules"] == ["nct6775"]
    assert data["command"] == f"sudo {APPIMAGE} system-setup --linux"
    assert data["job"]["state"] == "idle"


def test_a_run_asks_pkexec_for_the_setup_and_the_app_hears_how_it_went(port, linux, monkeypatch):
    runner = run_with(monkeypatch, (0, GOOD, ""))
    status, data = request(port, "POST", "/api/setup", {"rapl": True})
    assert status == 200 and data["job"]["state"] in ("running", "done")
    done = wait_done(port)
    assert done["job"]["state"] == "done" and done["job"]["result"]["ok"] is True
    assert runner.asked == [
        (
            "/usr/bin/pkexec",
            "--disable-internal-agent",
            APPIMAGE,
            "system-setup",
            "--linux",
            "--rapl",
            "--json",
        )
    ]


@pytest.mark.parametrize(
    ("payload", "flags"),
    [
        ({}, []),
        ({"rapl": None}, []),
        ({"rapl": False}, ["--no-rapl"]),
        ({"remove": True}, ["--remove"]),
    ],
)
def test_the_flags_of_the_request_are_the_flags_of_the_command(
    port, linux, monkeypatch, payload, flags
):
    runner = run_with(monkeypatch, (0, GOOD, ""))
    assert request(port, "POST", "/api/setup", payload)[0] == 200
    wait_done(port)
    assert runner.asked[0][3:] == ("system-setup", "--linux", *flags, "--json")


@pytest.mark.parametrize(
    "payload",
    [
        {"rapl": "yes"},
        {"rapl": 1},
        {"remove": "no"},
        {"command": "/bin/sh"},  # nothing but the two flags goes to root
        {"rapl": True, "argv": ["x"]},
        [],
        "rapl",
    ],
)
def test_nothing_but_the_two_flags_is_taken_from_a_request(port, linux, monkeypatch, payload):
    runner = run_with(monkeypatch)
    status, data = request(port, "POST", "/api/setup", payload)
    assert status == 400 and "error" in data
    assert runner.asked == []


def test_a_closed_dialog_is_said_to_be_closed(port, linux, monkeypatch):
    run_with(monkeypatch, (126, "", "Error executing command as another user: Not authorized"))
    request(port, "POST", "/api/setup", {})
    assert wait_done(port)["job"]["state"] == "cancelled"


def test_no_polkit_agent_is_said_too(port, linux, monkeypatch):
    run_with(monkeypatch, (127, "", "No authentication agent found."))
    request(port, "POST", "/api/setup", {})
    done = wait_done(port)["job"]
    assert done["state"] == "unavailable" and "No authentication agent" in done["detail"]


def test_a_run_is_not_started_twice(port, linux, monkeypatch):
    gate = threading.Event()

    def slow(argv):
        gate.wait(10)
        return 0, GOOD, ""

    monkeypatch.setattr(rights, "JOB", rights.Job(slow))
    assert request(port, "POST", "/api/setup", {})[0] == 200
    status, data = request(port, "POST", "/api/setup", {})
    assert status == 409 and "running already" in data["error"]
    gate.set()
    assert wait_done(port)["job"]["state"] == "done"


def test_where_polkit_cannot_be_asked_the_answer_is_the_terminal_command(port, linux, monkeypatch):
    run_with(monkeypatch)
    monkeypatch.setattr(rights.shutil, "which", lambda name: None)  # no pkexec here
    data = request(port, "GET", "/api/setup")[1]
    assert data["method"] == "" and data["command"].startswith("sudo ")
    status, error = request(port, "POST", "/api/setup", {})
    assert status == 409
    assert "Run this in a terminal: " + data["command"] in error["error"]


def test_while_libre_panel_runs_as_root_it_asks_for_nothing(port, linux, monkeypatch):
    runner = run_with(monkeypatch)
    monkeypatch.setattr(access, "elevated", lambda: True)
    status, error = request(port, "POST", "/api/setup", {})
    assert status == 403 and "administrator rights" in error["error"] and runner.asked == []


def test_the_german_texts_of_the_refusals(port, linux, monkeypatch):
    from libre_panel import i18n

    run_with(monkeypatch)
    monkeypatch.setattr(access, "elevated", lambda: True)
    i18n.set_language("de")
    error = request(port, "POST", "/api/setup", {})[1]["error"]
    assert "Administratorrechten" in error and "Terminal" in error


def test_a_request_without_the_token_starts_nothing(port, linux, monkeypatch):
    runner = run_with(monkeypatch, (0, GOOD, ""))
    status, _ = request(port, "POST", "/api/setup", {}, headers={access.HEADER: None})
    assert status == 403 and runner.asked == []
    assert json.dumps(request(port, "GET", "/api/setup")[1]["job"]["state"]) == '"idle"'

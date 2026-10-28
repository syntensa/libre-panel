"""AI activity (agents.py, services/agents.py, /api/ai/*, the agent widget and the ai module):
what Claude Code and Codex do, from their hooks, Claude Code's status line and, in the
background, the programs themselves; set up in their settings, and taken out again."""

import http.client
import json
import os
import stat
import sys
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from test_editor_server import request

from libre_panel import agents
from libre_panel.config import set_config_value
from libre_panel.editor.server import make_server
from libre_panel.render import agent as sign
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.services import agents as service
from libre_panel.theme.model import find_theme, load_theme, parse_theme

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def hook(board, event, session="one", agent="claude", **more):
    board.hook(agent, {"hook_event_name": event, "session_id": session, **more})


def test_the_state_follows_the_hooks():
    clock = Clock()
    board = agents.Board(clock)
    assert board.state("claude") == ("off", "", NOW)
    hook(board, "SessionStart")
    assert board.state("claude")[0] == "idle"
    clock.now += 5
    hook(board, "UserPromptSubmit", prompt="the words of the prompt")
    hook(board, "PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
    assert board.state("claude") == ("working", "Bash", NOW + 5)
    clock.now += 3
    hook(board, "PermissionRequest")
    assert board.state("claude") == ("waiting", "Bash", NOW + 8)
    hook(board, "PostToolUse")
    assert board.state("claude")[0] == "working"
    hook(board, "Notification", notification_type="permission_prompt")
    assert board.state("claude")[0] == "waiting"
    hook(board, "Notification", notification_type="idle_prompt")
    assert board.state("claude")[0] == "idle"
    hook(board, "Notification", notification_type="auth_success")  # says nothing of the state
    assert board.state("claude")[0] == "idle"
    hook(board, "Stop")
    assert board.state("claude") == ("idle", "", NOW + 8)
    # more sessions: one that waits for you before one that works before one that rests
    hook(board, "UserPromptSubmit", session="two")
    assert board.state("claude")[0] == "working"
    hook(board, "PermissionRequest", session="three")
    assert board.state("claude")[0] == "waiting"
    hook(board, "SessionEnd", session="three")
    assert board.state("claude")[0] == "working"
    assert board.state("codex")[0] == "off"  # each agent apart
    hook(board, "PreToolUse", agent="codex", tool_name="shell")
    assert board.state("codex") == ("working", "shell", NOW + 8)
    for session in ("one", "two"):
        hook(board, "SessionEnd", session=session)
    assert board.state("claude")[0] == "off"
    # nothing of what was asked or done is kept, and no session id
    kept = repr(board.agents)
    assert "the words" not in kept and "ls" not in kept and "'one'" not in kept
    board.hook("gemini", {"hook_event_name": "Stop"})  # an agent it does not know: nothing
    board.hook("claude", ["not", "a", "table"])
    assert board.state("claude")[0] == "off"


def test_the_task_is_the_name_of_the_session():
    clock = Clock()
    board = agents.Board(clock)
    hook(board, "SessionStart")
    assert board.readings()["ai.claude.task"] is None
    hook(board, "UserPromptSubmit", session_title="The  hooks\nof the panel",
         prompt="the words of the prompt")  # fmt: skip
    assert board.readings()["ai.claude.task"] == "The hooks of the panel"  # on one line
    assert "the words" not in repr(board.agents)  # what was asked stays out
    hook(board, "UserPromptSubmit", session_title="x" * 200)
    assert len(board.readings()["ai.claude.task"]) == agents.MAX_TASK
    hook(board, "SessionStart")  # a session taken up again begins without one
    assert board.readings()["ai.claude.task"] is None
    hook(board, "UserPromptSubmit")  # a session that carries no name: none
    assert board.readings()["ai.claude.task"] is None
    # of several sessions the one that counts carries it
    hook(board, "UserPromptSubmit", session="two", session_title="The other one")
    hook(board, "PermissionRequest", session="two")
    assert board.readings()["ai.claude.task"] == "The other one"


def test_with_task_off_the_service_keeps_what_the_session_is_called_off_the_panels():
    board = agents.Board(Clock())
    hook(board, "UserPromptSubmit", session_title="The hooks of the panel")
    sent: dict = {}
    host = SimpleNamespace(publish=sent.__setitem__, log=SimpleNamespace(info=print))
    service.Agents(host, {}, board=board).publish()  # (on by default)
    assert sent["ai.claude.task"] == "The hooks of the panel"
    service.Agents(host, {"task": False}, board=board).publish()
    assert sent["ai.claude.task"] is None and sent["ai.claude.state"] == "working"


def test_the_board_knows_when_each_agent_last_said_something(caplog):
    clock = Clock()
    board = agents.Board(clock)
    assert board.last_heard("claude") == (None, "")
    with caplog.at_level("INFO"):
        hook(board, "SessionStart")
        clock.now += 30
        hook(board, "PreToolUse", tool_name="Read")
    assert [r.getMessage() for r in caplog.records if "first word" in r.getMessage()] == [
        "AI activity: the first word from claude (SessionStart)"
    ]  # (once, the first time)
    clock.now += 12
    assert board.last_heard("claude") == (12.0, "PreToolUse")
    board.status("claude", {"model": {"display_name": "Opus"}})
    assert board.last_heard("claude") == (0.0, "statusLine")
    assert board.last_heard("codex") == (None, "")


def test_a_session_that_went_quiet():
    clock = Clock()
    board = agents.Board(clock)
    hook(board, "PreToolUse", tool_name="Edit")
    clock.now += agents.WORKING_STALE_S + 1  # (a window closed while it worked)
    assert board.state("claude")[0] == "idle"
    hook(board, "PermissionRequest")
    clock.now += agents.WAITING_STALE_S - 1
    assert board.state("claude")[0] == "waiting"
    clock.now += 2
    assert board.state("claude")[0] == "idle"
    clock.now += agents.SESSION_GONE_S
    assert board.state("claude")[0] == "off" and not board.agents["claude"].sessions


def test_the_status_line_brings_the_model_the_context_and_the_plan():
    clock = Clock()
    board = agents.Board(clock)
    data = {
        "session_id": "s",
        "model": {"id": "a-model", "display_name": "Opus"},
        "context_window": {"used_percentage": 34.4},
        "cost": {"total_cost_usd": 1.25},
        "rate_limits": {
            "five_hour": {"used_percentage": 23, "resets_at": NOW + 3600},
            "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 3 * 86400},
        },
    }
    assert board.status("claude", data) == "Opus · ctx 34% · 5h 23% · 7d 41%"
    seen = board.readings()
    assert (seen["ai.claude.model"], seen["ai.claude.context"], seen["ai.claude.cost"]) == (
        "Opus", 34.4, 1.25)  # fmt: skip
    assert seen["ai.claude.five_hour"] == 23 and seen["ai.claude.five_hour_reset"] == 3600
    assert seen["ai.claude.week"] == 41.2 and seen["ai.claude.week_reset"] == 3 * 86400
    assert seen["ai.claude.five_hour_age"] == 0 and seen["ai.claude.sessions"] == 1
    assert (seen["ai.claude.state"], seen["ai.claude.busy"]) == ("idle", 0)
    assert (seen["ai.codex.state"], seen["ai.codex.busy"], seen["ai.codex.five_hour"]) == (
        "off", -1, None)  # fmt: skip
    clock.now += 3601  # the five hours begin anew: what they said is over
    seen = board.readings()
    assert seen["ai.claude.five_hour"] is None and seen["ai.claude.five_hour_reset"] is None
    assert seen["ai.claude.week"] == 41.2 and seen["ai.claude.week_age"] == 3601
    # what makes no sense is left out
    nonsense = {
        "rate_limits": {"five_hour": {"used_percentage": 140}},
        "context_window": {"used_percentage": "full"},
        "cost": {"total_cost_usd": -3},
    }
    assert board.status("claude", nonsense) == "Opus · 7d 41%"
    assert board.readings()["ai.claude.cost"] is None
    assert board.status("gemini", data) == "" and board.status("claude", "text") == ""


def test_the_busy_reading_and_a_hook_wake_the_service():
    clock = Clock()
    board = agents.Board(clock)
    hook(board, "PermissionRequest", agent="codex")
    assert board.changed.is_set()
    assert board.readings()["ai.codex.busy"] == 2
    published = {}
    host = SimpleNamespace(publish=published.__setitem__, log=SimpleNamespace(info=print))
    agents_service = service.Agents(host, {}, board=board)
    agents_service.publish()
    assert published["ai.codex.state"] == "waiting" and published["ai.claude.state"] == "off"


def test_claude_tells_its_usage_without_a_message_to_the_model():
    answer = {
        "type": "control_response",
        "response": {
            "subtype": "success", "request_id": "libre-panel-usage",
            "response": {"rate_limits_available": True, "rate_limits": {
                "five_hour": {"utilization": 12.5, "resets_at": "2026-10-22T18:00:00Z"},
                "seven_day": {"utilization": 40, "resets_at": 1_800_100_000_000},  # (ms)
            }},
        },
    }  # fmt: skip
    lines = [json.dumps({"type": "system", "subtype": "init"}), json.dumps(answer)]
    found = agents.claude_quotas(agents.claude_usage_reply(lines))
    assert found == {
        "five_hour": (12.5, datetime(2026, 10, 22, 18, tzinfo=UTC).timestamp()),
        "week": (40.0, 1_800_100_000.0),
    }
    failed = {"type": "control_response",
              "response": {"subtype": "error", "request_id": "libre-panel-usage"}}  # fmt: skip
    for wrong, why in (([], "no answer"), (["{"], "no JSON"), ([json.dumps(failed)], "sign in"),
                       ([json.dumps({"type": "result"})], "unexpected")):  # fmt: skip
        with pytest.raises(ValueError, match=why):
            agents.claude_usage_reply(wrong)
    with pytest.raises(ValueError, match="no limits"):  # (an API key, no plan)
        agents.claude_quotas({"rate_limits_available": False})
    env = agents.claude_environment()
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env
    assert env["DISABLE_TELEMETRY"] == "1" and env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"


def test_codex_limits_by_the_length_of_their_window_and_its_tokens():
    data = {"rateLimits": {
        "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": NOW + 600},
        "secondary": {"usedPercent": 55, "windowDurationMins": 10080, "resetsAt": NOW + 86400},
    }}  # fmt: skip
    assert agents.codex_quotas(data) == {"five_hour": (30.0, NOW + 600),
                                         "week": (55.0, NOW + 86400)}  # fmt: skip
    by_id = {"rateLimits": None, "rateLimitsByLimitId": {"codex": data["rateLimits"]}}
    assert agents.codex_quotas(by_id) == agents.codex_quotas(data)
    odd = {"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 720}}}
    assert agents.codex_quotas(odd) == {}  # (neither the short window nor the week)
    usage = {"dailyUsageBuckets": [
        {"startDate": "2026-10-18", "tokens": 900},  # last week
        {"startDate": "2026-10-19", "tokens": 1000},
        {"startDate": "2026-10-21", "tokens": 2500},
        {"startDate": "2026-10-22", "tokens": 400}, {"startDate": "2026-10-22", "tokens": 100},
        "nonsense",
    ]}  # fmt: skip
    assert agents.codex_tokens(usage, "2026-10-22", "2026-10-19") == (500.0, 4000.0)


def program(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport json, os, sys\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.mark.skipif(sys.platform == "win32", reason="a script as the program")
def test_the_programs_are_asked_and_ended(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    claude = program(
        tmp_path,
        "claude",
        """
if sys.argv[1:] == ["--version"]:
    print("2.1.300 (Claude Code)")
    sys.exit()
assert "--no-session-persistence" in sys.argv
assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in os.environ
asked = json.loads(sys.stdin.readline())
assert asked["request"]["subtype"] == "get_usage"
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
print(json.dumps({"type": "control_response", "response": {"subtype": "success",
    "request_id": asked["request_id"], "response": {"rate_limits_available": True,
    "rate_limits": {"five_hour": {"utilization": 7, "resets_at": 1800000000}}}}}), flush=True)
sys.stdin.read()  # (it waits for more, as Claude Code does)
""",
    )
    assert agents.ask_claude(claude) == {"five_hour": (7.0, 1_800_000_000.0)}
    old = program(tmp_path, "old-claude", 'print("2.1.100 (Claude Code)")')
    with pytest.raises(ValueError, match="2.1.293"):
        agents.ask_claude(old)
    codex = program(
        tmp_path,
        "codex",
        """
assert sys.argv[1:] == ["app-server"]
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "account/rateLimits/read":
        result = {"rateLimits": {"primary": {"usedPercent": 12, "windowDurationMins": 300}}}
    elif message.get("method") == "account/usage/read":
        result = {"dailyUsageBuckets": [{"startDate": "2026-10-22", "tokens": 42}]}
    elif "id" in message:
        result = {}
    else:
        continue
    print(json.dumps({"id": message["id"], "result": result}), flush=True)
""",
    )
    quotas, usage = agents.ask_codex(codex)
    assert quotas == {"five_hour": (12.0, None)}
    assert usage == {"dailyUsageBuckets": [{"startDate": "2026-10-22", "tokens": 42}]}
    signed_out = program(
        tmp_path,
        "codex-out",
        """
for line in sys.stdin:
    message = json.loads(line)
    if "id" in message:
        print(json.dumps({"id": message["id"], "error": {"message": "not signed in"}}), flush=True)
""",
    )
    with pytest.raises(ValueError, match="sign in"):
        agents.ask_codex(signed_out)


def test_the_service_asks_one_program_at_a_time_and_waits_after_a_failure(monkeypatch):
    clock = Clock(1000.0)
    board = agents.Board()
    host = SimpleNamespace(publish=lambda key, value: None, log=SimpleNamespace(info=print))
    agents_service = service.Agents(host, {"codex_usage": True}, board=board, clock=clock)
    monkeypatch.setattr(agents, "program", lambda name: f"/bin/{name}")
    monkeypatch.setattr(agents, "ask_claude", lambda path: {"week": (61.0, None)})

    def codex_fails(path):
        raise ValueError("Codex reports no limits: sign in to Codex with ChatGPT")

    monkeypatch.setattr(agents, "ask_codex", codex_fails)
    agents_service._ask("claude")
    agents_service._ask("codex")
    assert board.readings()["ai.claude.week"] == 61.0
    assert board.agents["claude"].quotas["week"].source == "background"
    assert agents_service._next["claude"] == 1000 + agents.CLAUDE_EVERY_S
    assert agents_service._next["codex"] == 1000 + service.RETRY_S
    assert "sign in" in board.agents["codex"].usage_error
    monkeypatch.setattr(agents, "program", lambda name: None)
    agents_service._ask("claude")
    assert board.agents["claude"].usage_error == "not installed"
    off = service.Agents(host, {"claude_usage": False, "codex_usage": False}, board=board,
                         clock=clock)  # fmt: skip
    off._ask_when_due()
    assert off._asking is None


def test_set_up_and_taken_out_again_keeping_what_is_the_persons_own():
    own = {
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
        "statusLine": {"type": "command", "command": "my-line"},
    }
    plan = agents.claude_plan(own, 8765, "k3y", status_line=True)
    assert plan["model"] == "opus" and plan["statusLine"] == own["statusLine"]  # theirs stays
    assert set(plan["hooks"]) == set(agents.HOOK_EVENTS)
    assert plan["hooks"]["Stop"][0] == own["hooks"]["Stop"][0] and len(plan["hooks"]["Stop"]) == 2
    ours = plan["hooks"]["PreToolUse"][0]["hooks"][0]
    assert ours == {"type": "command", "command": agents.hook_command(8765, "claude", "k3y"),
                    "async": True, "timeout": 5}  # fmt: skip
    assert ours["command"].endswith(
        '-s -f -m 2 --data-binary "@-" "http://127.0.0.1:8765/api/ai/hook?agent=claude&key=k3y"'
    )
    assert agents.installed(plan) == {"hooks": True, "status_line": False,
                                      "other_status_line": True, "port": 8765}  # fmt: skip
    again = agents.claude_plan(plan, 9000, "k3y", status_line=True)  # replaced, not doubled
    assert len(again["hooks"]["Stop"]) == 2 and ":9000/" in str(again["hooks"]["Stop"][1])
    assert agents.installed(again)["port"] == 9000  # (the address it calls now)
    assert agents.without_ours(again) == own
    bare = agents.claude_plan({}, 8765, "k", status_line=True)
    assert bare["statusLine"] == {"type": "command",
                                  "command": agents.status_command(8765, "k")}  # fmt: skip
    assert agents.installed(bare) == {"hooks": True, "status_line": True,
                                      "other_status_line": False, "port": 8765}  # fmt: skip
    assert agents.installed(own)["port"] is None
    assert agents.without_ours(bare) == {}
    assert "statusLine" not in agents.claude_plan({}, 8765, "k", status_line=False)
    codex = agents.codex_plan({"hooks": {}}, 8765, "k")
    assert set(codex["hooks"]) == set(agents.CODEX_HOOK_EVENTS)
    assert "agent=codex" in str(codex) and agents.without_ours(codex) == {}
    with pytest.raises(ValueError, match="no table"):
        agents.claude_plan({"hooks": []}, 8765, "k", status_line=False)


def test_the_file_as_it_was_is_kept_once(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    assert agents.read_settings(path) == {}
    assert agents.write_settings(path, {"a": 1}) is None  # a new file: nothing to keep
    kept = agents.write_settings(path, {"a": 2})
    assert kept == path.with_name("settings.json.libre-panel.bak")
    assert json.loads(kept.read_text(encoding="utf-8")) == {"a": 1}
    agents.write_settings(path, {"a": 3})
    assert json.loads(kept.read_text(encoding="utf-8")) == {"a": 1}  # the first copy: theirs
    assert agents.read_settings(path) == {"a": 3}
    path.write_text("  \n", encoding="utf-8")
    assert agents.read_settings(path) == {}
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="no settings table"):
        agents.read_settings(path)


def test_the_key_is_made_once_and_kept_for_this_user(isolated_home):
    assert agents.key() == ""
    first = agents.key(create=True)
    assert len(first) >= 32 and agents.key(create=True) == first == agents.read_key()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(agents.key_file()).st_mode) == 0o600


@pytest.fixture
def server(isolated_home, tmp_path, monkeypatch):
    """The app's server, with a home of its own for the agents' settings."""
    home = tmp_path / "user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(agents, "BOARD", agents.Board(Clock()))
    monkeypatch.setattr(agents, "program", lambda name: "/bin/claude" if name == "claude" else None)
    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], home
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


def sent(port, path, body, headers=None):
    """As the hook's curl sends it: no token of the app, no JSON content type."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    hdrs = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {})}  # fmt: skip
    conn.request("POST", path, body=data, headers=hdrs)
    response = conn.getresponse()
    answer = response.read()
    conn.close()
    return response.status, answer


def test_the_hooks_come_in_only_switched_on_and_with_the_key(server):
    port, _home = server
    event = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Read"}
    key = agents.key(create=True)
    status, answer = sent(port, f"/api/ai/hook?agent=claude&key={key}", event)
    assert status == 403 and b"switched off" in answer
    set_config_value("services.enabled", ["agents"])
    assert sent(port, "/api/ai/hook?agent=claude", event)[0] == 403  # no key
    assert sent(port, "/api/ai/hook?agent=claude&key=wrong", event)[0] == 403
    assert agents.BOARD.state("claude")[0] == "off"
    assert sent(port, f"/api/ai/hook?agent=gemini&key={key}", event)[0] == 400
    assert sent(port, f"/api/ai/hook?agent=claude&key={key}", b"{")[0] == 400
    assert sent(port, f"/api/ai/hook?agent=claude&key={key}", event) == (204, b"")
    assert agents.BOARD.state("claude")[:2] == ("working", "Read")
    status, answer = sent(port, "/api/ai/hook?agent=codex", {"hook_event_name": "Stop"},
                          headers={agents.HEADER: key})  # fmt: skip
    assert status == 204 and agents.BOARD.state("codex")[0] == "idle"
    line = {"model": {"display_name": "Opus"}, "rate_limits": {"five_hour": {"used_percentage": 9}}}
    assert sent(port, f"/api/ai/status?agent=claude&key={key}", line) == (
        200,
        "Opus · 5h 9%".encode(),
    )
    from_a_page = {"Origin": "https://example.com"}
    assert sent(port, f"/api/ai/hook?agent=claude&key={key}", event, from_a_page)[0] == 403


def test_settings_show_what_is_added_then_add_and_remove_it(server):
    port, home = server
    status, found = request(port, "GET", "/api/ai/setup")
    assert status == 200 and found["on"] is False
    assert found["claude"]["program"] and not found["codex"]["program"]
    assert found["claude"]["path"] == str(home / ".claude" / "settings.json")
    assert found["codex"]["path"] == str(home / ".codex" / "hooks.json")
    status, answer = request(
        port, "POST", "/api/ai/setup", {"agent": "claude", "action": "install"}
    )
    assert status == 409 and "switched off" in answer["error"]
    set_config_value("services.enabled", ["agents"])
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    status, preview = request(
        port, "POST", "/api/ai/setup", {"agent": "claude", "action": "preview", "status_line": True}
    )
    assert status == 200 and json.loads(preview["before"]) == {"theme": "dark"}
    after = json.loads(preview["after"])
    assert after["theme"] == "dark" and f":{port}/api/ai/status" in after["statusLine"]["command"]
    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}  # not yet
    status, done = request(
        port, "POST", "/api/ai/setup", {"agent": "claude", "action": "install", "status_line": True}
    )
    assert status == 200 and done["hooks"] and done["status_line"]
    assert json.loads(settings.read_text(encoding="utf-8")) == after
    assert done["backup"] == str(settings.with_name("settings.json.libre-panel.bak"))
    assert agents.read_key() in settings.read_text(encoding="utf-8")
    status, done = request(port, "POST", "/api/ai/setup", {"agent": "codex", "action": "install"})
    assert status == 200 and done["backup"] is None and (home / ".codex" / "hooks.json").exists()
    status, found = request(port, "GET", "/api/ai/setup")
    assert found["on"] and found["claude"]["hooks"] and found["codex"]["hooks"]
    assert found["port"] == port and found["claude"]["port"] == port  # (where the hooks call)
    assert found["claude"]["heard"] is None  # nothing heard yet
    key = agents.read_key()
    sent(port, f"/api/ai/hook?agent=claude&key={key}", {"hook_event_name": "Stop"})
    status, found = request(port, "GET", "/api/ai/setup")
    assert found["claude"]["heard"] == 0.0 and found["claude"]["heard_what"] == "Stop"
    set_config_value("services.enabled", [])  # switched off, it can still be taken out
    for agent in ("claude", "codex"):
        status, done = request(port, "POST", "/api/ai/setup", {"agent": agent, "action": "remove"})
        assert status == 200 and not done["hooks"]
    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}
    settings.write_text("{broken", encoding="utf-8")
    status, found = request(port, "GET", "/api/ai/setup")
    assert found["claude"]["problem"]
    status, answer = request(port, "POST", "/api/ai/setup", {"agent": "claude", "action": "remove"})
    assert status == 400 and "cannot be read" in answer["error"]
    assert request(port, "POST", "/api/ai/setup", {"agent": "x", "action": "remove"})[0] == 400
    # the app's own pages need the app's token, as every other route
    assert request(port, "GET", "/api/ai/setup", headers={"X-Libre-Panel": None})[0] == 403


def test_the_sign_moves_while_it_works_or_waits():
    colors = ((46, 230, 214, 255), (245, 185, 66, 255), (91, 100, 116, 255))
    assert sign.step("working", 0) == 0 and sign.step("working", sign.ROUND_S / 2) == 24
    assert sign.step("working", sign.ROUND_S) == 0 and sign.step("idle", 5) is None
    assert sign.step("waiting", sign.BREATH_S / 4) == 12 and sign.step("off", 5) is None
    pictures = {state: sign.picture(64, state, 0 if state in ("working", "waiting") else None,
                                    *colors) for state in sign_states()}  # fmt: skip
    assert all(p.size == (64, 64) and p.mode == "RGBA" for p in pictures.values())
    assert len({p.tobytes() for p in pictures.values()}) == 4
    assert sign.picture(64, "working", 0, *colors) is pictures["working"]  # kept
    later = sign.picture(64, "working", 12, *colors)
    assert later.tobytes() != pictures["working"].tobytes()  # the lights went round
    waiting = np.asarray(pictures["waiting"])
    lit = waiting[waiting[..., 3] > 128][:, :3].mean(axis=0)
    assert lit[0] > lit[2]  # in the waiting colour
    assert sign.picture(4, "idle", None, *colors).size == (8, 8)


def sign_states():
    return ("working", "waiting", "idle", "off")


def test_the_widget_reads_the_state_and_asks_for_frames_while_it_moves():
    theme = parse_theme({
        "format": "libre-panel-theme/1", "name": "ai",
        "display": {"width": 160, "height": 80},
        "widgets": [{"type": "agent", "id": "a", "x": 0, "y": 0, "size": 80,
                     "sensor": "ai.claude.state"},
                    {"type": "agent", "id": "b", "x": 80, "y": 0, "size": 80,
                     "sensor": "ai.codex.state"}],
    })  # fmt: skip
    renderer = Renderer(theme, animate=True)
    snapshot = demo_snapshot(fixed_time=NOW)
    snapshot.readings["ai.claude.state"] = Reading("ai.claude.state", "working")
    snapshot.readings["ai.codex.state"] = Reading("ai.codex.state", "nonsense")  # as off
    first, _ = renderer.render(snapshot)
    assert renderer.moving
    grey = np.asarray(first.convert("L"), np.float32)
    assert grey[:, :80].mean() > grey[:, 80:].mean() > 0  # working is lit, off is faint
    snapshot.readings["ai.claude.state"] = Reading("ai.claude.state", "idle")
    renderer.render(snapshot)
    assert not renderer.moving


@pytest.mark.parametrize(
    "name", ["agents", "agents-1920x720", "agents-claude", "agents-claude-1920x720"]
)
def test_the_agents_theme_shows_both(name):
    theme = load_theme(find_theme(name))
    snapshot = demo_snapshot(fixed_time=NOW)
    board = agents.Board(Clock())
    hook(board, "PermissionRequest")
    board.status("claude", {"model": {"display_name": "Opus"}, "rate_limits": {
        "five_hour": {"used_percentage": 97, "resets_at": NOW + 5400}}})  # fmt: skip
    hook(board, "PreToolUse", agent="codex", tool_name="shell")
    for key, value in board.readings().items():
        snapshot.readings[key] = Reading(key, value)
    image, _ = Renderer(theme).render(snapshot)
    assert image.size == (theme.width, theme.height)
    assert np.asarray(image.convert("L")).std() > 10  # (something is drawn)


def test_claude_code_alone_also_shows_how_full_its_context_is():
    from libre_panel.theme.modules import expand

    def read(name, tag=""):
        widgets, _boxes = expand(load_theme(find_theme(name)))
        starts = (f"{tag}claude-", f"{tag}codex-")
        mine = [w for w in widgets if w["id"].partition("/")[2].startswith(starts)]
        return {w.get("sensor") for w in mine if str(w.get("sensor", "")).startswith("ai.")}

    alone, both = read("agents-claude"), read("agents")
    shown = {"ai.claude.state", "ai.claude.five_hour", "ai.claude.week", "ai.claude.context"}
    assert shown <= alone
    assert not any(key.startswith("ai.codex.") for key in alone)
    assert "ai.codex.state" in both and "ai.claude.context" not in both  # (side by side: alike)
    assert shown <= read("agents", "only-")  # (as alone while Codex is not running)


def test_the_module_says_when_a_window_begins_anew_only_where_it_fits():
    from libre_panel.theme.modules import expand

    def shown(cols, rows):
        data = {"format": "libre-panel-theme/1", "name": "ai",
                "display": {"model": "turing-9.2-usb"},
                "widgets": [{"type": "module", "id": "m0", "module": "ai", "col": 0, "row": 0,
                             "cols": cols, "rows": rows}]}  # fmt: skip
        widgets, _boxes = expand(parse_theme(data))
        both = [w["id"].partition("/")[2] for w in widgets]  # (the two side by side)
        return {i for i in both if i.startswith(("claude-", "codex-")) and
                i.endswith(("-reset", "-label"))}  # fmt: skip

    windows = {f"{a}-{window}" for a in ("claude", "codex") for window in ("five_hour", "week")}
    # two cells: the name of each window and its share, with no room for the time between them
    assert shown(2, 1) == {f"{w}-label" for w in windows}
    assert shown(4, 2) == {f"{w}-label" for w in windows} | {f"{w}-reset" for w in windows}


def test_the_module_says_what_the_session_is_called_on_the_screen_of_one_agent():
    from libre_panel.theme.modules import expand

    def parts(cols, rows, agent, tag=""):
        data = {"format": "libre-panel-theme/1", "name": "ai",
                "display": {"model": "turing-9.2-usb"},
                "widgets": [{"type": "module", "id": "m0", "module": "ai", "col": 0, "row": 0,
                             "cols": cols, "rows": rows, "agent": agent}]}  # fmt: skip
        widgets, _boxes = expand(parse_theme(data))
        mine = [w for w in widgets if w["id"].partition("/")[2].startswith(f"{tag}claude-")]
        return (any(w.get("sensor") == "ai.claude.task" for w in mine),
                len([w for w in mine if w["id"].endswith("-bar")]))  # fmt: skip

    assert parts(6, 2, "claude")[0] and parts(2, 1, "claude")[0]  # however small the module is
    # side by side the plan's limits are the better use of a short part, and keep every bar;
    # where the part is tall enough for both, it says it too
    assert parts(2, 1, "both") == (False, 2) and parts(4, 2, "both") == (False, 2)
    assert parts(6, 2, "both") == (True, 2)
    # Claude Code alone while Codex is not running: as on its own screen
    assert parts(6, 2, "both", "only-") == parts(6, 2, "claude") == (True, 3)


def test_of_both_the_one_that_runs_has_the_module_to_itself_while_the_other_is_off():
    """A card of AI activity for someone who uses only Claude Code shows Claude Code, not
    Codex not running beside it; both while both run, or neither, or nothing is known yet."""
    from libre_panel.render.renderer import need_holds
    from libre_panel.sensors.base import Snapshot
    from libre_panel.theme.modules import expand

    data = {"format": "libre-panel-theme/1", "name": "ai", "display": {"model": "turing-9.2-usb"},
            "widgets": [{"type": "module", "id": "m0", "module": "ai", "col": 0, "row": 0,
                         "cols": 6, "rows": 2}]}  # fmt: skip
    widgets, _boxes = expand(parse_theme(data))

    def signs(**states):
        snapshot = Snapshot(readings={f"ai.{agent}.state": Reading(f"ai.{agent}.state", state)
                                      for agent, state in states.items()})  # fmt: skip
        return sorted(
            w["id"].partition("/")[2] for w in widgets if w["id"].endswith("-sign")
            and all(need_holds(n, snapshot) for n in w.get("needs", "").split(",") if n)
        )  # fmt: skip

    assert signs(claude="working", codex="waiting") == ["claude-sign", "codex-sign"]
    assert signs() == ["claude-sign", "codex-sign"]  # (no AI activity set up)
    assert signs(claude="working", codex="off") == ["only-claude-sign"]
    assert signs(claude="off", codex="idle") == ["only-codex-sign"]
    assert signs(claude="off", codex="off") == ["none-claude-sign", "none-codex-sign"]


def test_the_sessions_one_by_one_the_one_that_counts_first():
    """``.s1`` to ``.s3``: waiting for you before working before idle, and of the same state the
    one that came to it last first; ``.s1`` is the one the agent's state is of."""
    clock = Clock()
    board = agents.Board(clock)
    hook(board, "UserPromptSubmit", session="a", session_title="The hooks")
    clock.now += 10
    hook(board, "Stop", session="b")
    clock.now += 10
    hook(board, "PermissionRequest", session="c")
    board.hook("claude", {"hook_event_name": "UserPromptSubmit", "session_id": "d",
                          "session_title": "In the cloud"}, cloud=True)  # fmt: skip
    clock.now += 5
    seen = board.readings()
    assert seen["ai.claude.state"] == "waiting" and seen["ai.claude.sessions"] == 4
    assert [seen[f"ai.claude.s{n}.state"] for n in (1, 2, 3)] == ["waiting", "working", "working"]
    assert (seen["ai.claude.s2.task"], seen["ai.claude.s2.cloud"]) == ("In the cloud", 1)
    assert (seen["ai.claude.s3.task"], seen["ai.claude.s3.since"]) == ("The hooks", 25)
    assert seen["ai.codex.s1.state"] is None and seen["ai.codex.s1.since"] is None
    published: dict = {}
    host = SimpleNamespace(publish=published.__setitem__, log=SimpleNamespace(info=print))
    service.Agents(host, {"task": False}, board=board).publish()
    assert published["ai.claude.s2.task"] is None  # (what a session is called stays off)


def test_the_cost_of_the_day_adds_up_what_each_session_cost_since_it_last_said():
    clock = Clock(datetime(2026, 10, 28, 22, 0).timestamp())
    board = agents.Board(clock)
    assert board.readings()["ai.claude.cost_today"] is None  # (nothing said yet)

    def said(session, dollars):
        board.status("claude", {"session_id": session, "cost": {"total_cost_usd": dollars}})

    said("a", 1.0)
    said("a", 1.5)
    said("b", 0.25)
    assert board.readings()["ai.claude.cost_today"] == pytest.approx(1.75)
    said("a", 0.5)  # (less than before: a session begun anew under its name)
    assert board.readings()["ai.claude.cost_today"] == pytest.approx(2.25)
    clock.now += 3 * 3600  # after midnight: what came before is yesterday's
    assert board.readings()["ai.claude.cost_today"] == 0
    said("a", 0.75)  # (a's 0.25 since midnight)
    assert board.readings()["ai.claude.cost_today"] == pytest.approx(0.25)
    assert board.readings()["ai.codex.cost_today"] is None


def test_a_notice_when_a_session_begins_to_wait_where_it_is_switched_on():
    board = agents.Board(Clock())
    told: list = []
    host = SimpleNamespace(publish=lambda key, value: None, log=SimpleNamespace(info=print),
                           notify=lambda text, **options: told.append((text, options)))  # fmt: skip
    quiet = service.Agents(host, {}, board=board)  # (off by default)
    on = service.Agents(host, {"notice": True}, board=board)
    hook(board, "UserPromptSubmit", session_title="The hooks of the panel")
    quiet.publish()
    on.publish()
    hook(board, "PermissionRequest")
    quiet.publish()
    on.publish()
    on.publish()  # (still waiting: once)
    assert len(told) == 1
    text, options = told[0]
    assert text == "The hooks of the panel" and options["kind"] == "agent"
    assert options["icon"] == "agent" and "Claude Code" in options["payload"]["label"]
    hook(board, "PostToolUse")
    on.publish()
    hook(board, "PermissionRequest")
    on.publish()
    assert len(told) == 2  # (it waits again)
    from libre_panel.icons import draw_icon
    from libre_panel.render.overlays import SHOWN_BY

    assert draw_icon("agent", 24, (255, 255, 255, 255)).getbbox() is not None
    assert "ai.claude.state" in SHOWN_BY["agent"]  # (a theme that shows it leaves it out)


def test_claude_code_alone_lists_its_other_sessions_and_the_cost_of_the_day():
    from libre_panel.theme.modules import expand

    def parts(name):
        widgets, _boxes = expand(load_theme(find_theme(name)))
        return {w["id"].partition("/")[2] for w in widgets}

    tall = parts("agents-claude-1920x720")  # (room under the meters for two)
    assert {"claude-s2-sign", "claude-s2-task", "claude-s3-waiting", "claude-cost"} <= tall
    assert "claude-s4-sign" not in tall
    low = parts("agents-claude")  # (the 9.2": no room)
    assert "claude-cost" in low and not any(p.startswith("claude-s2") for p in low)
    assert "codex-cost" not in parts("agents")

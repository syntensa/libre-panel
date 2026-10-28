"""Claude Code's cloud sessions on the panel (agents_cloud): a hook in the cloud environment
says by a relay (an ntfy topic) when a session works and waits for you; Libre Panel listens."""

import io
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from test_agents import Clock, request, server  # noqa: F401 (the fixture)

from libre_panel import agents, agents_cloud
from libre_panel.config import set_config_value
from libre_panel.render.renderer import need_holds
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.services import agents as service
from libre_panel.theme.model import parse_theme
from libre_panel.theme.modules import expand

UNIX = pytest.mark.skipif(sys.platform == "win32", reason="the hook runs in a Linux cloud VM")


def test_a_new_relay_is_a_long_random_topic_and_a_wrong_address_says_why():
    one, two = agents_cloud.new_relay(), agents_cloud.new_relay()
    assert one != two and one.startswith("https://ntfy.sh/libre-panel-")
    assert agents_cloud.relay_problem(one) == "" and agents_cloud.relay_domain(one) == "ntfy.sh"
    assert agents_cloud.relay_problem("https://ntfy.example.org/a-topic-of-my-own") == ""
    for wrong in ("ntfy.sh/libre-panel-abcdefghijklmnop", "https://ntfy.sh/short",
                  "https://ntfy.sh/", "ftp://ntfy.sh/libre-panel-abcdefghijklmnop",
                  "https://ntfy.sh/libre-panel-abcdefghijklmnop?x=1"):  # fmt: skip
        assert agents_cloud.relay_problem(wrong), wrong


def said(**fields):
    return json.dumps({"v": 1, "agent": "claude", "event": "UserPromptSubmit",
                       "session": "0123456789abcdef", **fields})  # fmt: skip


def test_only_a_message_of_the_hooks_counts():
    assert agents_cloud.message(said(session_title="Cards")) == {
        "hook_event_name": "UserPromptSubmit", "session_id": "cloud:0123456789abcdef",
        "session_title": "Cards",
    }  # fmt: skip
    waiting = agents_cloud.message(
        said(event="Notification", notification_type="permission_prompt")
    )
    assert waiting["notification_type"] == "permission_prompt"
    wrongs = (
        said(v=2),
        said(agent="codex"),
        said(event="Whatever"),
        said(session=""),
        said(session="x" * 65),
        "not json",
        json.dumps([1]),
        said(x="y" * 3000),
        42,
    )
    for wrong in wrongs:
        assert agents_cloud.message(wrong) is None, wrong


def test_a_cloud_session_counts_as_claude_codes_and_says_where_it_runs():
    clock = Clock()
    board = agents.Board(clock)
    assert board.cloud_last_heard() is None
    board.hook("claude", agents_cloud.message(said(session_title="Cards")), cloud=True)
    found = board.readings()
    assert found["ai.claude.state"] == "working" and found["ai.claude.cloud"] == 1
    assert found["ai.claude.task"] == "Cards" and board.cloud_last_heard() == 0.0
    assert board.last_heard("claude")[0] is None  # (the hooks on this PC said nothing)
    board.hook("claude", {"hook_event_name": "PermissionRequest", "session_id": "here"})
    assert board.readings()["ai.claude.cloud"] == 0  # a session here waits for you: it counts
    board.hook("claude", {"hook_event_name": "SessionEnd", "session_id": "here"})
    end = agents_cloud.message(said(event="SessionEnd"))
    board.hook("claude", end, cloud=True)
    assert board.readings()["ai.claude.state"] == "off"


def test_the_listener_takes_the_relays_messages_and_asks_for_what_it_missed():
    board = agents.Board(Clock())
    asked = []

    def opener(req, timeout):
        asked.append((req.full_url, req.get_header("Authorization"), timeout))
        lines = [json.dumps({"event": "open"}), json.dumps({"event": "keepalive"}),
                 json.dumps({"id": "m1", "event": "message", "message": said(session_title="A")}),
                 json.dumps({"id": "m2", "event": "message", "message": "not one of ours"}),
                 "garbage"]  # fmt: skip
        return io.BytesIO("\n".join(lines).encode() + b"\n")

    listener = agents_cloud.Listener("https://ntfy.sh/libre-panel-abcdefghijklmnop/", board,
                                     token="t0k", opener=opener)  # fmt: skip
    listener.listen()
    assert asked == [("https://ntfy.sh/libre-panel-abcdefghijklmnop/json", "Bearer t0k", 120.0)]
    assert board.readings()["ai.claude.state"] == "working" and listener.last_id == "m2"
    listener.listen()  # (after a break: what came since)
    assert asked[-1][0].endswith("/json?since=m2")
    quiet = agents_cloud.Listener("https://ntfy.sh/libre-panel-abcdefghijklmnop", board,
                                  named=False)  # fmt: skip
    line = json.dumps({"id": "m3", "event": "message",
                       "message": said(session="other", session_title="Secret")})  # fmt: skip
    assert quiet.take(line) and "Secret" not in repr(board.agents)  # (task = false)


def test_the_service_listens_where_a_relay_is_set(monkeypatch):
    started = []
    monkeypatch.setattr(agents_cloud.Listener, "start", lambda self: started.append(self.url))
    monkeypatch.setattr(agents_cloud.Listener, "stop", lambda self: started.append("stopped"))
    warned = []
    log = SimpleNamespace(info=print, warning=lambda *a: warned.append(a))
    host = SimpleNamespace(publish=lambda key, value: None, log=log)
    relay = agents_cloud.new_relay()
    running = service.Agents(host, {"cloud": relay, "cloud_token": "t"}, board=agents.Board())
    running.start()
    assert started == [relay] and running.cloud.token == "t" and running.cloud.named
    running.stop()
    assert started[-1] == "stopped" and running.cloud is None
    wrong = service.Agents(host, {"cloud": "https://ntfy.sh/x"}, board=agents.Board())
    wrong.start()
    assert wrong.cloud is None and warned
    wrong.stop()


class Relay(BaseHTTPRequestHandler):
    """An ntfy topic: what is posted to it."""

    posted: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Relay.posted.append((self.path, json.loads(body), dict(self.headers)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def relay():
    Relay.posted = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/libre-panel-abcdefghijklmnop"
    srv.shutdown()
    srv.server_close()


@UNIX
def test_the_hook_says_the_state_when_it_changes_and_never_the_prompt(relay, tmp_path):
    hook = tmp_path / "hook.py"
    hook.write_text(agents_cloud.hook_script(relay, named=True), encoding="utf-8")
    env = {**os.environ, "TMPDIR": str(tmp_path), "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}

    def event(name, **more):
        data = {"hook_event_name": name, "session_id": "abc", "prompt": "the secret prompt",
                "tool_input": {"file_path": "/secret"}, **more}  # fmt: skip
        subprocess.run([sys.executable, str(hook)], input=json.dumps(data), text=True,
                       env=env, check=True, timeout=30)  # fmt: skip

    event("UserPromptSubmit", session_title="Cards on the panel")
    event("PreToolUse", tool_name="Edit")  # (still working: nothing)
    event("PostToolUse", tool_name="Edit")
    event("PermissionRequest")
    event("PreToolUse", tool_name="Bash")
    event("Notification", notification_type="idle_prompt")
    events = [body["event"] for _path, body, _headers in Relay.posted]
    assert events == ["UserPromptSubmit", "PermissionRequest", "PreToolUse", "Notification"]
    first = Relay.posted[0][1]
    assert first["session_title"] == "Cards on the panel" and first["agent"] == "claude"
    assert "secret" not in json.dumps([body for _p, body, _h in Relay.posted])
    assert Relay.posted[0][2]["X-Firebase"] == "no"
    assert agents_cloud.message(json.dumps(first))["session_title"] == "Cards on the panel"
    # while it works, again after five minutes
    (state,) = (tmp_path / "libre-panel-cloud").iterdir()
    event("UserPromptSubmit")
    kept = json.loads(state.read_text(encoding="utf-8"))
    state.write_text(json.dumps({**kept, "at": kept["at"] - agents_cloud.AGAIN_S}), "utf-8")
    event("PostToolUse")
    assert [body["event"] for _p, body, _h in Relay.posted][-2:] == ["UserPromptSubmit",
                                                                      "PostToolUse"]  # fmt: skip
    hook.write_text(agents_cloud.hook_script(relay, named=False), encoding="utf-8")
    event("SessionStart", session_id="new")
    event("UserPromptSubmit", session_id="new", session_title="Not to be told")
    assert "Not to be told" not in json.dumps([body for _p, body, _h in Relay.posted])


@UNIX
def test_the_setup_script_puts_the_hook_into_the_sessions_settings_once(relay, tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    own = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
    (home / ".claude" / "settings.json").write_text(json.dumps(own), encoding="utf-8")
    script = agents_cloud.setup_script(relay, named=True)
    assert "127.0.0.1" in script.splitlines()[3]  # (the host to allow, at the top)
    env = {**os.environ, "HOME": str(home)}
    for _ in range(2):  # (an environment may run it again)
        subprocess.run(["bash", "-c", script], env=env, check=True, timeout=60)
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    command = f"python3 {home}/.claude/libre-panel-cloud.py"
    for event in agents.HOOK_EVENTS:
        ours = [g for g in settings["hooks"][event] if agents_cloud.MARK in json.dumps(g)]
        assert ours == [{"hooks": [{"type": "command", "command": command, "async": True,
                                    "timeout": 10}]}], event  # fmt: skip
    assert settings["hooks"]["Stop"][0] == own["hooks"]["Stop"][0]  # theirs stays
    hook = (home / ".claude" / "libre-panel-cloud.py").read_text(encoding="utf-8")
    assert json.dumps(relay) in hook
    compile(hook, "hook", "exec")


def test_settings_give_the_cloud_environments_setup_script(server):  # noqa: F811
    port, _home = server
    status, found = request(port, "GET", "/api/ai/setup")
    assert found["cloud"] == {"relay": "", "heard": None, "problem": ""}
    status, plan = request(port, "POST", "/api/ai/setup", {"agent": "cloud", "action": "preview"})
    assert status == 200 and agents_cloud.relay_problem(plan["relay"]) == ""
    assert plan["domain"] == "ntfy.sh" and json.dumps(plan["relay"]) in plan["script"]
    set_config_value("services.agents.cloud", plan["relay"])
    status, again = request(port, "POST", "/api/ai/setup", {"agent": "cloud", "action": "preview"})
    assert again["relay"] == plan["relay"]  # (the one set: Show the setup script)
    set_config_value("services.agents.task", False)
    status, again = request(port, "POST", "/api/ai/setup", {"agent": "cloud", "action": "preview"})
    assert "NAMED = False" in again["script"]
    status, found = request(port, "GET", "/api/ai/setup")
    assert found["cloud"]["relay"] == plan["relay"] and found["cloud"]["heard"] is None
    asked = {"agent": "cloud", "action": "preview", "relay": "https://x/short"}
    status, wrong = request(port, "POST", "/api/ai/setup", asked)
    assert status == 400 and "topic" in wrong["error"]


def test_the_module_says_a_cloud_session_works_in_the_cloud():
    data = {"format": "libre-panel-theme/1", "name": "ai", "display": {"model": "turing-9.2-usb"},
            "widgets": [{"type": "module", "id": "m0", "module": "ai", "col": 0, "row": 0,
                         "cols": 6, "rows": 2, "agent": "claude"}]}  # fmt: skip
    widgets, _boxes = expand(parse_theme(data))

    def shown(**values):
        snapshot = Snapshot(readings={f"ai.claude.{k}": Reading(f"ai.claude.{k}", v)
                                      for k, v in values.items()})  # fmt: skip
        lines = [w for w in widgets if w["id"].endswith(("-tool", "-cloud", "-model"))]
        needs = {w["id"]: [n for n in w.get("needs", "").split(",") if n] for w in lines}
        return {w["id"].partition("/")[2] for w in lines
                if all(need_holds(n, snapshot) for n in needs[w["id"]])}  # fmt: skip

    assert shown(state="working", cloud=0, tool="Edit") == {"claude-tool"}
    assert shown(state="working", cloud=1) == {"claude-cloud"}
    assert shown(state="idle", cloud=1) == {"claude-cloud"}
    assert shown(state="idle", model="Opus") == {"claude-model"}  # (before any cloud: here)
    assert next(w for w in widgets if w["id"].endswith("claude-cloud"))["text"] == "In the cloud"

"""What the AI coding agents on this PC do: Claude Code and Codex.

Three ways in, all on this computer; no account and none of their keys is read:

- **Hooks**: each event of theirs (a prompt sent, a tool used, a question to you, the answer
  done) runs a command that posts it to ``/api/ai/hook`` (curl, in the background, so a panel
  that does not run costs them nothing).
- **Claude Code's status line**: what it would show below the prompt (the model, how full the
  context is, the plan's 5-hour and 7-day usage) is posted to ``/api/ai/status``, which
  answers with the line it then shows.
- **In the background** (the service's ``claude_usage`` and ``codex_usage``): the plan's usage
  asked of the programs themselves while no terminal is open. Claude Code (2.1.293 and later)
  answers a headless usage request without a message to the model, at most every three
  minutes; Codex's app-server reads its account's limits every minute.

Hooks and the status line are set up from the app (Settings → Game mode and more → AI activity): it
shows what it adds to ``~/.claude/settings.json`` and ``~/.codex/hooks.json``, keeps a copy
of each file, and takes out again only what it added. The address carries the AI key, a
secret of its own in ``ai.json`` beside config.toml (as the notify key).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from libre_panel import rights
from libre_panel.config import config_dir

log = logging.getLogger(__name__)

AGENTS = ("claude", "codex")
STATES = ("working", "waiting", "idle", "off")
KEY_FILE = "ai.json"
HEADER = "X-Libre-Panel-AI"  # or ``?key=`` in the address
MAX_BODY = 256 * 1024
MAX_TASK = 120  # of what a session is called, on the one line a theme has for it

# Hook events and what they say of a session (Claude Code's names; Codex uses the same).
WORKING = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "TaskCreated",
})  # fmt: skip
IDLE = frozenset({"SessionStart", "Stop", "StopFailure"})
WAITING_NOTICES = frozenset({
    "permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input",
})  # fmt: skip
HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
    "Notification", "PreCompact", "Stop", "StopFailure", "SessionEnd",
)  # fmt: skip
CODEX_HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
    "PreCompact", "Stop",
)  # fmt: skip

WORKING_STALE_S = 15 * 60  # working without a word for so long: taken as idle (a closed window)
WAITING_STALE_S = 2 * 3600
SESSION_GONE_S = 6 * 3600  # a session that said nothing for so long is over
FIVE_HOUR, WEEK = "five_hour", "week"


def _session(value: Any) -> str:
    """A short name for a session id (the id itself is not kept)."""
    text = str(value or "")
    return hashlib.sha256(text.encode()).hexdigest()[:12] if text else "-"


def _task(data: dict[str, Any]) -> str:
    """What a session is called, where it carries a name; never what was asked of it."""
    value = data.get("session_title")
    if not isinstance(value, str) or not value.strip():
        return ""
    return " ".join(value.split())[:MAX_TASK]  # one line: a theme has no more room


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _percent(value: Any) -> float | None:
    found = _number(value)
    return found if found is not None and 0 <= found <= 100 else None


def _epoch(value: Any) -> float | None:
    """A moment as seconds since 1970: a number, or an ISO 8601 text."""
    found = _number(value)
    if found is not None:
        return found / 1000 if found > 1e11 else found  # (milliseconds)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


@dataclass
class Session:
    state: str = "idle"
    changed: float = 0.0
    seen: float = 0.0
    tool: str = ""
    task: str = ""


@dataclass
class Quota:
    used: float  # percent
    resets: float | None  # when, seconds since 1970
    seen: float  # when it was read
    source: str = ""


@dataclass
class Agent:
    sessions: dict[str, Session] = field(default_factory=dict)
    model: str = ""
    context: float | None = None
    cost: float | None = None
    quotas: dict[str, Quota] = field(default_factory=dict)
    tokens_today: float | None = None
    tokens_week: float | None = None
    usage_error: str = ""
    heard: float = 0.0  # when it last said something (a hook, its status line); 0: never
    heard_what: str = ""  # what that was (the hook's event, "statusLine")


class Board:
    """What is known of each agent, from every way in; thread-safe."""

    def __init__(self, clock: Any = time.time) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self.agents = {name: Agent() for name in AGENTS}
        self.changed = threading.Event()

    # -- in --------------------------------------------------------------------------------

    def hook(self, agent: str, data: dict[str, Any]) -> None:
        """One hook event (its JSON as the agent sends it)."""
        if agent not in self.agents or not isinstance(data, dict):
            return
        event = str(data.get("hook_event_name") or "")
        key, now = _session(data.get("session_id")), self.clock()
        with self._lock:
            self._heard(agent, now, event[:40] or "?")
            sessions = self.agents[agent].sessions
            if event == "SessionEnd":
                sessions.pop(key, None)
                self.changed.set()
                return
            session = sessions.setdefault(key, Session(changed=now))
            session.seen = now
            state = None
            if event in WORKING:
                state = "working"
                if event == "PreToolUse":
                    session.tool = str(data.get("tool_name") or "")[:40]
                elif event == "UserPromptSubmit":
                    session.tool = ""
                    session.task = _task(data)
            elif event == "PermissionRequest":
                state = "waiting"
            elif event == "Notification":
                kind = str(data.get("notification_type") or "")
                if kind in WAITING_NOTICES:
                    state = "waiting"
                elif kind == "idle_prompt":
                    state = "idle"
            elif event in IDLE:
                state = "idle"
                session.tool = ""
                if event == "SessionStart":
                    session.task = ""  # a session taken up again begins without one
            if state is not None and state != session.state:
                session.state, session.changed = state, now
            self.changed.set()

    def status(self, agent: str, data: dict[str, Any]) -> str:
        """Claude Code's status line data; the line it shows (the model, the context, the
        plan's usage), "" where nothing is known."""
        if agent not in self.agents or not isinstance(data, dict):
            return ""
        now = self.clock()
        model = data.get("model") if isinstance(data.get("model"), dict) else {}
        context = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
        cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
        limits = data.get("rate_limits") if isinstance(data.get("rate_limits"), dict) else {}
        with self._lock:
            self._heard(agent, now, "statusLine")
            known = self.agents[agent]
            if model.get("display_name"):
                known.model = str(model["display_name"])[:40]
            known.context = _percent(context.get("used_percentage"))
            spent = _number(cost.get("total_cost_usd"))
            known.cost = spent if spent is not None and spent >= 0 else None
            for window, name in ((FIVE_HOUR, "five_hour"), (WEEK, "seven_day")):
                value = limits.get(name) if isinstance(limits.get(name), dict) else {}
                used = _percent(value.get("used_percentage"))
                if used is not None:
                    known.quotas[window] = Quota(used, _epoch(value.get("resets_at")), now,
                                                 "status")  # fmt: skip
            key = _session(data.get("session_id"))
            if key != "-":
                known.sessions.setdefault(key, Session(changed=now)).seen = now
            self.changed.set()
            return self._line(known, now)

    def _heard(self, agent: str, now: float, what: str) -> None:
        known = self.agents[agent]
        if not known.heard:
            log.info("AI activity: the first word from %s (%s)", agent, what)
        known.heard, known.heard_what = now, what

    def last_heard(self, agent: str) -> tuple[float | None, str]:
        """How many seconds ago the agent last said something, and what (None: never)."""
        with self._lock:
            known = self.agents[agent]
            return (max(0.0, self.clock() - known.heard) if known.heard else None), known.heard_what

    @staticmethod
    def _line(known: Agent, now: float) -> str:
        parts = [known.model] if known.model else []
        if known.context is not None:
            parts.append(f"ctx {known.context:.0f}%")
        for window, label in ((FIVE_HOUR, "5h"), (WEEK, "7d")):
            quota = known.quotas.get(window)
            if quota is not None and not (quota.resets is not None and quota.resets < now):
                parts.append(f"{label} {quota.used:.0f}%")
        return " · ".join(parts)

    def quota(self, agent: str, window: str, used: float, resets: float | None,
              source: str = "") -> None:  # fmt: skip
        with self._lock:
            self.agents[agent].quotas[window] = Quota(used, resets, self.clock(), source)
            self.agents[agent].usage_error = ""
            self.changed.set()

    def tokens(self, agent: str, today: float | None, week: float | None) -> None:
        with self._lock:
            self.agents[agent].tokens_today, self.agents[agent].tokens_week = today, week
            self.changed.set()

    def usage_failed(self, agent: str, why: str) -> None:
        with self._lock:
            self.agents[agent].usage_error = why

    # -- out -------------------------------------------------------------------------------

    def _best(self, agent: str, now: float) -> Session | None:
        """The session that counts for the agent, its state as it stands (one that went stale
        taken as idle): waiting for you before working before idle; None without any. Under
        the lock, and sessions long gone are dropped on the way."""
        sessions = self.agents[agent].sessions
        for key in [k for k, s in sessions.items() if now - s.seen > SESSION_GONE_S]:
            del sessions[key]
        best: tuple[int, Session] | None = None
        for session in sessions.values():
            state = session.state
            if state == "working" and now - session.seen > WORKING_STALE_S:
                state = "idle"
            elif state == "waiting" and now - session.seen > WAITING_STALE_S:
                state = "idle"
            rank = {"waiting": 3, "working": 2, "idle": 1}[state]
            if best is None or rank > best[0]:
                best = (rank, replace(session, state=state))
        return best[1] if best is not None else None

    def state(self, agent: str) -> tuple[str, str, float]:
        """The agent's state (``STATES``), the tool it uses and since when it is so: waiting
        for you before working before idle, over its sessions; "off" without any."""
        now = self.clock()
        with self._lock:
            best = self._best(agent, now)
        if best is None:
            return "off", "", now
        return best.state, best.tool, best.changed

    def readings(self) -> dict[str, Any]:
        """``ai.<agent>.*`` as numbers and texts (None: not known)."""
        now = self.clock()
        out: dict[str, Any] = {}
        for agent in AGENTS:
            with self._lock:
                known = self.agents[agent]
                best = self._best(agent, now)
                state = best.state if best is not None else "off"
                since = best.changed if best is not None else now
                quotas = dict(known.quotas)
                active = sum(1 for s in known.sessions.values() if now - s.seen <= SESSION_GONE_S)
                values = {
                    "state": state,
                    "busy": {"waiting": 2, "working": 1, "idle": 0, "off": -1}[state],
                    "tool": best.tool if best is not None else "",
                    "task": (best.task if best is not None else "") or None,
                    "since": max(0.0, now - since),  # seconds
                    "sessions": active,
                    "model": known.model or None,
                    "context": known.context,
                    "cost": known.cost,
                    "tokens_today": known.tokens_today,
                    "tokens_week": known.tokens_week,
                }
            for window, name in ((FIVE_HOUR, "five_hour"), (WEEK, "week")):
                quota = quotas.get(window)
                if quota is not None and quota.resets is not None and quota.resets < now:
                    quota = None  # its window began anew: what it said is over
                values[name] = quota.used if quota else None
                left = (quota.resets - now) if quota and quota.resets else None
                values[f"{name}_reset"] = left  # seconds
                values[f"{name}_age"] = now - quota.seen if quota else None
            out.update({f"ai.{agent}.{name}": value for name, value in values.items()})
        return out


BOARD = Board()


# -- the AI key ----------------------------------------------------------------------------------


def key_file() -> Path:
    return config_dir() / KEY_FILE


def read_key() -> str:
    try:
        data = json.loads(key_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    found = data.get("key") if isinstance(data, dict) else None
    return found if isinstance(found, str) else ""


def new_key() -> str:
    found = secrets.token_urlsafe(24)
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{KEY_FILE}.{os.getpid()}")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                   encoding="utf-8") as file:  # fmt: skip
        json.dump({"key": found}, file)
    os.replace(temporary, path)
    return found


def key(create: bool = False) -> str:
    return read_key() or (new_key() if create else "")


# -- in the background: the plan's usage, asked of the programs ---------------------------------

CLAUDE_MIN_VERSION = (2, 1, 293)
CLAUDE_EVERY_S = 180.0
CODEX_EVERY_S = 60.0
RUN_TIMEOUT_S = 30.0


def program(name: str) -> str | None:
    """Where the agent's own program is (on PATH), or None; with the highest rights only one
    from a safe place (rights.py)."""
    return rights.which(name)


def _hidden() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def claude_version(path: str) -> tuple[int, ...] | None:
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=RUN_TIMEOUT_S, **_hidden())  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", done.stdout or "")
    return tuple(int(part) for part in found.groups()) if found else None


def claude_environment() -> dict[str, str]:
    """Claude Code's environment for the usage request: without the switches that would keep
    it from asking (it counts the request as optional traffic), with its own reports off."""
    blocked = {
        "DISABLE_TELEMETRY",
        "DISABLE_ERROR_REPORTING",
        "DISABLE_AUTOUPDATER",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "CLAUDE_CODE_ENABLE_TELEMETRY",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() not in blocked}
    env.update(DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1", DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_ENABLE_TELEMETRY="0")  # fmt: skip
    return env


CLAUDE_REQUEST = {"type": "control_request", "request_id": "libre-panel-usage",
                  "request": {"subtype": "get_usage", "skip_behaviors": True}}  # fmt: skip


def claude_usage_reply(lines: Any) -> dict[str, Any]:
    """The usage in Claude Code's answer (its lines of JSON); ValueError where there is none."""
    for line in lines:
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise ValueError("an answer that is no JSON") from exc
        if not isinstance(message, dict) or message.get("type") == "system":
            continue
        if message.get("type") != "control_response":
            raise ValueError(f"an unexpected answer ({message.get('type')})")
        response = message.get("response") if isinstance(message.get("response"), dict) else {}
        if response.get("request_id") != CLAUDE_REQUEST["request_id"]:
            continue
        data = response.get("response")
        if response.get("subtype") != "success" or not isinstance(data, dict):
            raise ValueError("no usage: sign in to Claude Code again, or update it")
        return data
    raise ValueError("no answer")


def claude_quotas(data: dict[str, Any]) -> dict[str, tuple[float, float | None]]:
    """``{five_hour|week: (percent used, resets at)}`` of Claude Code's usage answer."""
    limits = data.get("rate_limits") if isinstance(data.get("rate_limits"), dict) else {}
    if not data.get("rate_limits_available") or not limits:
        raise ValueError("the plan reports no limits")
    out = {}
    for window, name in ((FIVE_HOUR, "five_hour"), (WEEK, "seven_day")):
        value = limits.get(name) if isinstance(limits.get(name), dict) else {}
        used = _percent(value.get("utilization"))
        if used is not None:
            out[window] = (used, _epoch(value.get("resets_at")))
    if not out:
        raise ValueError("the plan reports no limits")
    return out


def ask_claude(path: str) -> dict[str, tuple[float, float | None]]:
    """The plan's usage from Claude Code itself: a headless usage request, no message to the
    model, nothing kept of the session."""
    version = claude_version(path)
    if version is None or version < CLAUDE_MIN_VERSION:
        raise ValueError("Claude Code 2.1.293 or later answers in the background")
    command = [path, "--safe-mode", "--no-session-persistence", "-p", "--input-format",
               "stream-json", "--output-format", "stream-json", "--verbose"]  # fmt: skip
    lines = _exchange(command, [json.dumps(CLAUDE_REQUEST)], claude_environment(), 1)
    return claude_quotas(claude_usage_reply(lines))


def codex_quotas(data: dict[str, Any]) -> dict[str, tuple[float, float | None]]:
    """``{five_hour|week: (percent, resets at)}`` of Codex's ``account/rateLimits/read``: its
    windows by their length (up to 6 hours: the short one; a day and more: the week)."""
    limits = data.get("rateLimits") if isinstance(data.get("rateLimits"), dict) else {}
    by_id = data.get("rateLimitsByLimitId")
    if not limits and isinstance(by_id, dict):
        limits = by_id.get("codex") if isinstance(by_id.get("codex"), dict) else {}
    out = {}
    for name in ("primary", "secondary"):
        value = limits.get(name) if isinstance(limits.get(name), dict) else {}
        used = _percent(value.get("usedPercent"))
        minutes = _number(value.get("windowDurationMins"))
        if used is None or minutes is None:
            continue
        window = FIVE_HOUR if minutes <= 360 else WEEK if minutes >= 1440 else None
        if window is not None:
            out[window] = (used, _epoch(value.get("resetsAt")))
    return out


def codex_tokens(data: dict[str, Any], today: str, week_start: str) -> tuple[float, float]:
    """Today's and this week's tokens of Codex's ``account/usage/read`` (days as
    "YYYY-MM-DD")."""
    day = week = 0.0
    for bucket in data.get("dailyUsageBuckets") or []:
        if not isinstance(bucket, dict):
            continue
        date, tokens = str(bucket.get("startDate") or ""), _number(bucket.get("tokens")) or 0.0
        if date == today:
            day += tokens
        if week_start <= date <= today:
            week += tokens
    return day, week


def ask_codex(path: str) -> tuple[dict[str, tuple[float, float | None]], dict[str, Any] | None]:
    """The account's limits (and its daily tokens where Codex has them) from its app-server."""
    requests = [
        json.dumps({"id": 1, "method": "initialize",
                    "params": {"clientInfo": {"name": "libre_panel", "version": "1.0"}}}),
        json.dumps({"method": "initialized", "params": {}}),
        json.dumps({"id": 2, "method": "account/rateLimits/read", "params": {}}),
        json.dumps({"id": 3, "method": "account/usage/read", "params": {}}),
    ]  # fmt: skip
    replies: dict[int, Any] = {}
    for line in _exchange([path, "app-server"], requests, None, 3, ids=True):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and isinstance(message.get("id"), int):
            replies[message["id"]] = message
    limits = replies.get(2, {})
    if not isinstance(limits.get("result"), dict):
        raise ValueError("Codex reports no limits: sign in to Codex with ChatGPT")
    usage = replies.get(3, {}).get("result")
    return codex_quotas(limits["result"]), usage if isinstance(usage, dict) else None


def _exchange(command: list[str], requests: list[str], env: dict[str, str] | None,
              wanted: int, ids: bool = False) -> list[str]:  # fmt: skip
    """Start ``command``, write ``requests`` (a line each) and gather the lines it answers
    until ``wanted`` answers came (with ``ids``: replies with an ``id``; else any line that is
    not a system message), or the time is up; then end it."""
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                   env=env, **_hidden())  # fmt: skip
    except OSError as exc:
        raise ValueError(f"cannot start {Path(command[0]).name}: {exc}") from exc
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for line in process.stdout:  # type: ignore[union-attr]
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, name="agents-read", daemon=True).start()
    got: list[str] = []
    try:
        for request in requests:
            process.stdin.write(request + "\n")  # type: ignore[union-attr]
        process.stdin.flush()  # type: ignore[union-attr]
        deadline, answers = time.monotonic() + RUN_TIMEOUT_S, 0
        while answers < wanted:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                break
            if line is None:
                break
            got.append(line)
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if not isinstance(message, dict):
                continue
            if ids and isinstance(message.get("id"), int):
                answers += 1
            elif not ids and message.get("type") not in (None, "system"):
                answers += 1
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            process.stdin.close()  # type: ignore[union-attr]
        process.kill()
        with contextlib.suppress(subprocess.SubprocessError):
            process.wait(timeout=5)
    return got


# -- setting up the hooks and the status line ----------------------------------------------------

MARK = "/api/ai/"  # what tells Libre Panel's entries from the person's own


def curl() -> str:
    return "curl.exe" if sys.platform == "win32" else "curl"


def hook_command(port: int, agent: str, secret: str) -> str:
    url = f"http://127.0.0.1:{port}{MARK}hook?agent={agent}&key={secret}"
    return f'{curl()} -s -f -m 2 --data-binary "@-" "{url}"'


def status_command(port: int, secret: str) -> str:
    url = f"http://127.0.0.1:{port}{MARK}status?agent=claude&key={secret}"
    return f'{curl()} -s -f -m 1 --data-binary "@-" "{url}"'


def claude_settings_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "settings.json"


def codex_hooks_path(home: Path | None = None) -> Path:
    base = os.environ.get("CODEX_HOME")
    return (Path(base) if base else (home or Path.home()) / ".codex") / "hooks.json"


def _ours(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    return isinstance(hooks, list) and any(
        isinstance(h, dict) and MARK in str(h.get("command", "")) for h in hooks
    )


def with_hooks(settings: dict[str, Any], events: tuple[str, ...], command: str) -> dict[str, Any]:
    """``settings`` with Libre Panel's hook (``command``, in the background) on each of
    ``events``; the person's own hooks stay, an earlier one of Libre Panel's is replaced."""
    out = json.loads(json.dumps(settings))
    hooks = out.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" is no table')
    for event in events:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f'"hooks.{event}" is no list')
        groups = [group for group in groups if not _ours(group)]
        groups.append({"hooks": [{"type": "command", "command": command, "async": True,
                                  "timeout": 5}]})  # fmt: skip
        hooks[event] = groups
    return out


def without_ours(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` without what Libre Panel added (its hooks, its status line)."""
    out = json.loads(json.dumps(settings))
    hooks = out.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            groups = hooks[event]
            if isinstance(groups, list):
                kept = [group for group in groups if not _ours(group)]
                if kept:
                    hooks[event] = kept
                else:
                    del hooks[event]
        if not hooks:
            del out["hooks"]
    line = out.get("statusLine")
    if isinstance(line, dict) and MARK in str(line.get("command", "")):
        del out["statusLine"]
    return out


_PORT = re.compile(r"127\.0\.0\.1:(\d+)" + re.escape(MARK))


def installed(settings: dict[str, Any]) -> dict[str, Any]:
    """Whether Libre Panel's hooks are in ``settings``, whose status line there is, and the
    port Libre Panel's hooks call (None: none of them)."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    ours = any(isinstance(g, list) and any(_ours(x) for x in g) for g in hooks.values())
    line = settings.get("statusLine")
    line_ours = isinstance(line, dict) and MARK in str(line.get("command", ""))
    other = bool(line) and not line_ours
    found = _PORT.search(json.dumps(hooks)) if ours else None
    return {"hooks": ours, "status_line": line_ours, "other_status_line": other,
            "port": int(found.group(1)) if found else None}  # fmt: skip


def read_settings(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} holds no settings table")
    return data


def write_settings(path: Path, data: dict[str, Any]) -> Path | None:
    """Write ``data`` to ``path``, keeping the file as it was beside it (``.libre-panel.bak``,
    once: the first copy is the person's own); the copy, or None for a new file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".libre-panel.bak")
    kept = None
    if path.exists():
        if not backup.exists():
            shutil.copy2(path, backup)
        kept = backup
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return kept


def claude_plan(settings: dict[str, Any], port: int, secret: str,
                status_line: bool) -> dict[str, Any]:  # fmt: skip
    """Claude Code's settings with Libre Panel's hooks, and its status line where wanted and
    the person has none of their own."""
    out = with_hooks(without_ours(settings), HOOK_EVENTS, hook_command(port, "claude", secret))
    if status_line and not out.get("statusLine"):
        out["statusLine"] = {"type": "command", "command": status_command(port, secret)}
    return out


def codex_plan(settings: dict[str, Any], port: int, secret: str) -> dict[str, Any]:
    command = hook_command(port, "codex", secret)
    return with_hooks(without_ours(settings), CODEX_HOOK_EVENTS, command)

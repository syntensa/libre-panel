"""AI activity of Claude Code's cloud sessions (claude.ai/code), for the panel at home.

A cloud session runs on a machine of Anthropic's that cannot reach this PC; what it does comes
by a relay, an ntfy topic (ntfy.sh, or an ntfy server of your own) whose address is a secret
of its own:

- **In the cloud**: the environment's setup script (:func:`setup_script`, shown in Settings →
  Game mode and more → AI activity) writes a hook into the session's own Claude Code settings.
  On every event it works out the session's state as the board does (working, waiting for
  you, idle) and posts it to the relay only when it changes, and every five minutes while it
  works (a working session that says nothing for 15 minutes is taken as idle): a few messages
  a prompt, as ntfy.sh takes 250 a day. It sends the event's name, a short name for the
  session (a hash of its id) and, where wanted, what the session is called; never a prompt,
  a tool's input or a file. The environment needs the relay's host among its allowed domains.
- **On this PC**: the agents service (``cloud`` in ``[services.agents]``) listens to the
  topic (:class:`Listener`) and hands each message, checked, to the board as a hook of a
  cloud session.

Whoever knows the address can read and write the topic: it is long and random (:func:`new_relay`),
and what comes from it is taken for no more than a session's state.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import urllib.parse
import urllib.request
from typing import Any

from libre_panel import agents

log = logging.getLogger(__name__)

RELAY_HOST = "https://ntfy.sh"
TOPIC = re.compile(r"[A-Za-z0-9_-]{16,64}")
MAX_MESSAGE = 2048  # a message of the hook's is some 200 bytes
AGAIN_S = 300  # while a session works, its state goes again after this long
RETRY_S = (5.0, 15.0, 60.0)  # waits before listening again after the relay went away
READ_TIMEOUT_S = 120.0  # ntfy says something at least every 45 s ("keepalive")
EVENTS = frozenset(agents.HOOK_EVENTS) | agents.WORKING | {"SessionEnd"}


def new_relay(host: str = RELAY_HOST) -> str:
    """A new relay address: the host and a long random topic."""
    return f"{host.rstrip('/')}/libre-panel-{secrets.token_urlsafe(24)}"


def relay_problem(url: str) -> str:
    """What is wrong with a relay address ("" where nothing is): https (or http on this
    network), a host, and one long topic."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return "an https address, as https://ntfy.sh/<topic>"
    topic = parts.path.strip("/").split("/")[-1]
    if not TOPIC.fullmatch(topic) or parts.query or parts.fragment:
        return "one topic of 16 to 64 letters, digits, - and _ after the host"
    return ""


def relay_domain(url: str) -> str:
    """The host the cloud environment has to allow."""
    return urllib.parse.urlsplit(url).hostname or ""


def message(text: Any) -> dict[str, Any] | None:
    """A message from the relay as a hook of a cloud session (``hook_event_name``,
    ``session_id``, ``notification_type``, ``session_title``), or None where it is not one
    of the hook's: anyone with the address could have sent it."""
    if not isinstance(text, str) or len(text) > MAX_MESSAGE:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("agent") != "claude":
        return None
    event, session = data.get("event"), data.get("session")
    if event not in EVENTS or not isinstance(session, str) or not 1 <= len(session) <= 64:
        return None
    out: dict[str, Any] = {"hook_event_name": event, "session_id": f"cloud:{session}"}
    kind = data.get("notification_type")
    if isinstance(kind, str) and len(kind) <= 40:
        out["notification_type"] = kind
    title = data.get("session_title")
    if isinstance(title, str) and title.strip():
        out["session_title"] = title[: agents.MAX_TASK * 2]
    return out


class Listener:
    """Listens to the relay's topic and hands each message to ``board`` (ntfy's JSON stream:
    one object a line; after a break it asks for what came since the last one it had)."""

    def __init__(self, url: str, board: agents.Board, token: str = "", named: bool = True,
                 opener: Any = urllib.request.urlopen) -> None:  # fmt: skip
        self.url, self.board, self.token, self.named = url.rstrip("/"), board, token, named
        self.opener = opener
        self.last_id = ""
        self._stop = threading.Event()
        self._response: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="agents-cloud", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        response = self._response
        if response is not None:
            try:
                response.close()  # (a read that waits ends with it)
            except Exception:  # noqa: BLE001 - it is going anyway
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self.listen()
                failures = 0
            except Exception as exc:  # noqa: BLE001 - the relay went away: listen again later
                if failures == 0:
                    log.info("AI activity: the relay for cloud sessions is not reachable (%s)",
                             exc)  # fmt: skip
                failures += 1
            self._stop.wait(RETRY_S[min(failures, len(RETRY_S) - 1)] if failures else 1.0)

    def listen(self) -> None:
        """One connection: its messages, until it ends."""
        query = f"?since={urllib.parse.quote(self.last_id)}" if self.last_id else ""
        request = urllib.request.Request(f"{self.url}/json{query}")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        self._response = self.opener(request, timeout=READ_TIMEOUT_S)
        try:
            for line in self._response:
                if self._stop.is_set():
                    return
                self.take(line)
        finally:
            response, self._response = self._response, None
            response.close()

    def take(self, line: bytes | str) -> bool:
        """One line of the stream; whether it was a hook's message the board took."""
        try:
            item = json.loads(line)
        except ValueError:
            return False
        if not isinstance(item, dict) or item.get("event") != "message":
            return False
        if isinstance(item.get("id"), str) and len(item["id"]) <= 64:
            self.last_id = item["id"]
        data = message(item.get("message"))
        if data is None:
            return False
        if not self.named:
            data.pop("session_title", None)
        self.board.hook("claude", data, cloud=True)
        return True


# -- in the cloud -----------------------------------------------------------------------------

HOOK_FILE = "~/.claude/libre-panel-cloud.py"
MARK = "libre-panel-cloud.py"  # what tells Libre Panel's hook in the cloud from the person's own

HOOK = r'''"""Libre Panel: what this cloud session of Claude Code does, for the panel at home.

On each event: the session's state (working, waiting for you, idle), to the relay when it
changes and every five minutes while it works; never a prompt, a tool's input or a file. A
relay that is busy (429) is asked again a few times. What went is noted in
~/.claude/libre-panel-cloud.log. Written by the environment's setup script (Libre Panel:
Settings -> AI activity)."""
import fcntl, hashlib, json, os, sys, time, urllib.error, urllib.request

RELAY = __RELAY__
NAMED = __NAMED__  # what the session is called goes along
AGAIN_S = __AGAIN__
WORKING = __WORKING__
IDLE = __IDLE__
WAITING = __WAITING__
TRIES = (0, 2, 5, 10, 20)  # seconds before each try: a relay shared by many is often busy
WITHIN_S = 45  # (the hook's own time is a minute)
LOG = os.path.expanduser("~/.claude/libre-panel-cloud.log")


def note(*parts):
    """A line in the log (its last hundred lines kept), for a look at what the hook does."""
    try:
        if os.path.getsize(LOG) > 32768:
            with open(LOG, encoding="utf-8") as f:
                tail = f.readlines()[-100:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except OSError:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(p for p in parts if p) + "\n")
    except OSError:
        pass


def state_of(event, kind):
    if event in WORKING:
        return "working"
    if event == "PermissionRequest" or (event == "Notification" and kind in WAITING):
        return "waiting"
    if event in IDLE or (event == "Notification" and kind == "idle_prompt"):
        return "idle"
    return "off" if event == "SessionEnd" else None


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    event, kind = str(data.get("hook_event_name") or ""), str(data.get("notification_type") or "")
    state = state_of(event, kind)
    if state is None:
        return
    session = hashlib.sha256(str(data.get("session_id") or "").encode()).hexdigest()[:16]
    title = data.get("session_title") if NAMED and event == "UserPromptSubmit" else None
    title = " ".join(title.split())[:120] if isinstance(title, str) else ""
    folder = os.path.join(os.environ.get("TMPDIR") or "/tmp", "libre-panel-cloud")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, session), "a+", encoding="utf-8") as kept:
        fcntl.flock(kept, fcntl.LOCK_EX)
        kept.seek(0)
        try:
            last = json.loads(kept.read() or "{}")
        except ValueError:
            last = {}
        now = time.time()
        again = state == "working" and now - float(last.get("at", 0)) >= AGAIN_S
        if last.get("state") == state and (not title or title == last.get("title")) and not again:
            return
        body = {"v": 1, "agent": "claude", "event": event, "session": session}
        if kind:
            body["notification_type"] = kind[:40]
        if title:
            body["session_title"] = title
        request = urllib.request.Request(RELAY, data=json.dumps(body).encode(), method="POST",
                                         headers={"X-Firebase": "no", "X-Priority": "1"})
        token = os.environ.get("LIBRE_PANEL_RELAY_TOKEN")
        if token:
            request.add_header("Authorization", "Bearer " + token)
        failed, sent = [], False
        for wait in TRIES:
            if time.time() + wait - now > WITHIN_S:
                break
            time.sleep(wait)
            try:
                urllib.request.urlopen(request, timeout=10).close()
                sent = True
                break
            except urllib.error.HTTPError as exc:
                failed.append("HTTP %d" % exc.code)
                if exc.code != 429 and exc.code < 500:
                    break  # (refused for good: a token that is wrong, an address that is not)
            except Exception as exc:
                failed.append("%s: %s" % (type(exc).__name__, str(exc)[:120]))
        if not sent:
            note(event, state, "not sent:", ", ".join(failed))
            return  # (the next event tries again)
        note(event, state, "sent", "(after %s)" % ", ".join(failed) if failed else "")
        kept.seek(0)
        kept.truncate()
        kept.write(json.dumps({"state": state, "at": now, "title": title or last.get("title", "")}))


if __name__ == "__main__":
    main()
'''

MERGE = r"""import json, os
path = os.path.expanduser("~/.claude/settings.json")
try:
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
except (OSError, ValueError):
    settings = {}
if not isinstance(settings, dict):
    settings = {}
hooks = settings.setdefault("hooks", {})
command = "python3 " + os.path.expanduser(__FILE__)
for event in __EVENTS__:
    groups = [g for g in hooks.get(event, []) if __MARK__ not in json.dumps(g)]
    groups.append({"hooks": [{"type": "command", "command": command, "async": True,
                              "timeout": 60}]})
    hooks[event] = groups
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)
"""


def hook_script(relay: str, named: bool) -> str:
    """The hook that runs in the cloud session, for the relay at ``relay``."""
    values = {
        "__RELAY__": json.dumps(relay),
        "__NAMED__": "True" if named else "False",
        "__AGAIN__": str(AGAIN_S),
        "__WORKING__": repr(sorted(agents.WORKING)),
        "__IDLE__": repr(sorted(agents.IDLE)),
        "__WAITING__": repr(sorted(agents.WAITING_NOTICES)),
    }
    text = HOOK
    for name, value in values.items():
        text = text.replace(name, value)
    return text


def setup_script(relay: str, named: bool = True) -> str:
    """The cloud environment's setup script: the hook, and the hook in the session's own Claude
    Code settings on each event (an earlier one of Libre Panel's replaced, the rest kept)."""
    merge = (MERGE.replace("__FILE__", json.dumps(HOOK_FILE))
             .replace("__EVENTS__", repr(list(agents.HOOK_EVENTS)))
             .replace("__MARK__", json.dumps(MARK)))  # fmt: skip
    return (
        "#!/bin/bash\n"
        "# Libre Panel: AI activity of this environment's Claude Code sessions, for the panel\n"
        "# at home (Settings -> Game mode and more -> AI activity). It needs the relay's host\n"
        f"# among the environment's allowed domains: {relay_domain(relay)}\n"
        "# What it sends, in a session: ~/.claude/libre-panel-cloud.log\n"
        "mkdir -p ~/.claude\n"
        f"cat > {HOOK_FILE} <<'LIBRE_PANEL_HOOK'\n{hook_script(relay, named)}LIBRE_PANEL_HOOK\n"
        f"python3 - <<'LIBRE_PANEL_MERGE'\n{merge}LIBRE_PANEL_MERGE\n"
    )

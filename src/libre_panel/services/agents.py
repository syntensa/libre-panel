"""AI activity: what Claude Code and Codex do on this PC, for a screen that shows it.

The readings come from libre_panel.agents: their hooks and Claude Code's status line (set up in
Settings → Services → AI activity, which shows what it adds), and in the background the plan's
usage, asked of the programs themselves while a theme shows ``ai.*``:

``[services.agents]``::

    claude_usage = true   # ask Claude Code every three minutes (2.1.293 and later)
    codex_usage = true    # ask Codex's app-server every minute

Readings, for ``claude`` and ``codex``: ``ai.<agent>.state`` (working, waiting, idle, off),
``.busy`` (2 waiting for you, 1 working, 0 idle, -1 off), ``.tool``, ``.since`` (seconds in
the state), ``.sessions``, ``.model``, ``.context`` (% of the context window), ``.cost`` ($ of
the session, an estimate), ``.five_hour`` and ``.week`` (% of the plan's limits used), with
``_reset`` (seconds until the window begins anew, ``{value:duration}``) and ``_age`` (seconds
since it was read), and Codex's ``.tokens_today`` and ``.tokens_week``.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Any

from libre_panel import agents
from libre_panel.plugins import Service
from libre_panel.sensors.base import wanted

TICK_S = 1.0
RETRY_S = 15 * 60.0  # a program that did not answer is asked again after this long


class Agents(Service):
    """Publishes ``ai.*`` from the board; asks the agents' programs for the plan's usage."""

    name = "agents"
    api = 1
    options = {
        "claude_usage": ("bool", True),
        "codex_usage": ("bool", True),
    }

    def __init__(self, host: Any, options: dict[str, Any], board: agents.Board | None = None,
                 clock: Any = time.monotonic) -> None:  # fmt: skip
        super().__init__(host, options)
        self.board = board or agents.BOARD
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._asking: threading.Thread | None = None
        self._next = {"claude": 0.0, "codex": 0.0}

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="service-agents", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.publish()
            if wanted("ai."):
                self._ask_when_due()
            self.board.changed.clear()
            self.board.changed.wait(TICK_S)  # (a hook comes at once)
            if self._stop.is_set():
                break

    def publish(self) -> None:
        for key, value in self.board.readings().items():
            self.host.publish(key, value)

    # -- the plan's usage ---------------------------------------------------------------------

    def _ask_when_due(self) -> None:
        if self._asking is not None and self._asking.is_alive():
            return  # (one program at a time, beside the panel)
        now = self.clock()
        for agent, enabled in (("claude", self.options.get("claude_usage", True)),
                               ("codex", self.options.get("codex_usage", True))):  # fmt: skip
            if enabled and now >= self._next[agent]:
                self._asking = threading.Thread(target=self._ask, args=(agent,),
                                                name=f"agents-{agent}", daemon=True)  # fmt: skip
                self._asking.start()
                return

    def _ask(self, agent: str) -> None:
        every = agents.CLAUDE_EVERY_S if agent == "claude" else agents.CODEX_EVERY_S
        path = agents.program(agent)
        if path is None:
            self._next[agent] = self.clock() + RETRY_S
            self.board.usage_failed(agent, "not installed")
            return
        try:
            if agent == "claude":
                quotas = agents.ask_claude(path)
            else:
                quotas, usage = agents.ask_codex(path)
                if usage is not None:
                    today = date.today()
                    monday = today - timedelta(days=today.weekday())
                    self.board.tokens(agent, *agents.codex_tokens(usage, today.isoformat(),
                                                                  monday.isoformat()))  # fmt: skip
        except ValueError as exc:
            self.host.log.info("%s: no usage (%s)", agent, exc)
            self.board.usage_failed(agent, str(exc))
            self._next[agent] = self.clock() + max(every, RETRY_S)
            return
        for window, (used, resets) in quotas.items():
            self.board.quota(agent, window, used, resets, source="background")
        self._next[agent] = self.clock() + every

"""A timer on the panel: so many minutes counted down, and a notice when they are up. With
``break_minutes``, focus and break take turns (the Pomodoro way): when the focus time is up
the break begins by itself, and after the break the next focus waits to be started.

A keyboard shortcut starts and pauses it (``[hotkeys] timer``), another sets it back
(``timer_reset``); so do the buttons in Settings → Game mode and more. Readings for a theme
(the Timer module shows them): ``timer.left`` (seconds), ``timer.text`` ("24:13"),
``timer.running`` (1 or 0), ``timer.share`` (how much of the time has passed, 0 to 1),
``timer.phase`` ("Timer", or "Focus" and "Break") and ``timer.rounds`` (the focus times
finished since the service started).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from libre_panel.i18n import t
from libre_panel.plugins import Service

TICK_S = 0.2
TOGGLE, RESET = "timer", "timer_reset"  # the shortcuts' actions (hotkeys.py)


def clock_text(seconds: float) -> str:
    """``24:13``; ``1:02:05`` from an hour on. A second begun counts (``0:01`` until the end)."""
    whole = max(0, int(-(-seconds // 1)))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class Timer(Service):
    """Minutes counted down, started and paused by a shortcut or in the app."""

    name = "timer"
    api = 1
    options = {
        "minutes": ("number", 25.0),
        "break_minutes": ("number", 0.0),  # 0: no break, a plain timer
        "notice": ("bool", True),
    }

    def __init__(
        self, host: Any, options: dict[str, Any], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        super().__init__(host, options)
        self.clock = clock
        self.focus_s = max(1.0, float(options["minutes"]) * 60)
        self.break_s = max(0.0, float(options["break_minutes"]) * 60)
        self.phase = "focus"
        self.left = self.focus_s
        self.ends: float | None = None  # while it runs: when, on the clock
        self.rounds = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the service ------------------------------------------------------------------------

    def start(self) -> None:
        self.host.on("shortcut", self.on_shortcut)
        self.publish()
        self._thread = threading.Thread(target=self._run, name="service-timer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            self.tick()

    def on_shortcut(self, action: str = "", **_: Any) -> None:
        if action == TOGGLE:
            self.toggle()
        elif action == RESET:
            self.reset()

    # -- what it does -------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.ends is not None

    def _length(self) -> float:
        return self.break_s if self.phase == "break" else self.focus_s

    def toggle(self) -> None:
        """Start, or pause where it is."""
        with self._lock:
            now = self.clock()
            if self.ends is None:
                if self.left <= 0:
                    self.left = self._length()
                self.ends = now + self.left
            else:
                self.left = max(0.0, self.ends - now)
                self.ends = None
        self.publish()

    def reset(self) -> None:
        """Back to the start of a focus time, stopped."""
        with self._lock:
            self.phase, self.left, self.ends = "focus", self.focus_s, None
        self.publish()

    def tick(self) -> None:
        told = None
        with self._lock:
            now = self.clock()
            if self.ends is not None:
                self.left = max(0.0, self.ends - now)
                if self.left <= 0:
                    told = self._ended(now)
        self.publish()
        if told and self.options["notice"]:
            self.host.notify(
                told, icon="hourglass", level="info", seconds=8.0, kind="timer", rank=2,
                payload={"label": t("Timer")}, key="timer",
            )  # fmt: skip

    def _ended(self, now: float) -> str:
        """The time is up (under the lock): what to say, and what comes next."""
        if self.phase == "focus":
            self.rounds += 1
            if self.break_s > 0:  # the break begins by itself
                self.phase, self.left = "break", self.break_s
                self.ends = now + self.break_s
                return t("Time for a break")
            self.ends = None
            return t("Time is up")
        self.phase, self.left, self.ends = "focus", self.focus_s, None  # the next focus waits
        return t("The break is over")

    def publish(self) -> None:
        with self._lock:
            left, length, running = self.left, self._length(), self.ends is not None
            phase, rounds = self.phase, self.rounds
        name = t("Timer") if self.break_s <= 0 else t("Break") if phase == "break" else t("Focus")
        share = 1 - left / length if length > 0 else 1.0
        self.host.publish("timer.left", round(left, 1), "s", "Time left")
        self.host.publish("timer.text", clock_text(left), "", "Time left")
        self.host.publish("timer.running", 1 if running else 0, "", "Timer runs")
        self.host.publish("timer.share", round(max(0.0, min(1.0, share)), 4), "", "Time passed")
        self.host.publish("timer.phase", name, "", "Timer")
        self.host.publish("timer.rounds", rounds, "", "Rounds")

    def state(self) -> dict[str, Any]:
        """For the app: how it stands now."""
        with self._lock:
            return {"left": round(self.left, 1), "running": self.ends is not None,
                    "phase": self.phase, "rounds": self.rounds}  # fmt: skip

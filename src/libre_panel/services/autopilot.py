"""Autopilot: the theme that fits what you are doing.

Rules in ``config.toml`` are checked from the top; the first whose conditions
all hold names the theme to show (music playing, the microphone in use, the
load over a limit, a program, the time of day). When none holds, the theme of
``config.toml`` shows. The service never writes the configuration: it asks the
host for a theme the way any service can (``show_theme`` with the lowest
priority, for normal mode only), so a game mode or a service with a higher
priority goes first, and the choice is back by itself when they are done.

Nothing flickers: a new choice must hold for ``hold_s``, and a theme then stays
for ``min_s`` (a rule can say ``immediate``). Right after the system started the
load is no signal, since everything starts at once.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from libre_panel.plugins import Service
from libre_panel.sensors.base import Snapshot
from libre_panel.services import machine as computer
from libre_panel.services.readings import number

CONDITIONS = ("music", "microphone", "cpu_over", "gpu_over", "apps", "time")
FILL = 0.8  # a mean over a window counts once this much of it has been seen
LOAD_EVERY_S = 0.5  # load readings are kept this far apart
SLOW_EVERY_S = 1.0  # the microphone and the window in front are asked this often
PROGRAMS_EVERY_S = 2.0  # and the list of programs (where no window in front is known)
THEME_EVERY_S = 10.0  # whether a theme is installed
LONGEST_S = 3600.0
TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


@dataclass
class Rule:
    """A theme and the conditions at which it shows."""

    name: str
    theme: str
    when: dict[str, Any]  # condition -> what it must be (checked values)
    for_s: float = 20.0  # the window the load is averaged over
    hold_s: float | None = None
    immediate: bool = False


def parse_time(text: Any) -> tuple[int, int] | None:
    """``"22:00-06:30"`` as minutes after midnight, (22 * 60, 6 * 60 + 30)."""
    found = TIME.match(text) if isinstance(text, str) else None
    if not found:
        return None
    start_h, start_m, end_h, end_m = map(int, found.groups())
    if max(start_h, end_h) > 23 or max(start_m, end_m) > 59:
        return None
    return start_h * 60 + start_m, end_h * 60 + end_m


def in_time(window: tuple[int, int], minutes: int) -> bool:
    start, end = window
    return start <= minutes < end if start <= end else minutes >= start or minutes < end


def parse_conditions(when: Any) -> tuple[dict[str, Any], str]:
    """The conditions of a rule with their values checked, and what is wrong (or "")."""
    if not isinstance(when, dict):
        return {}, "'when' must be a table, e.g. { music = true }"
    found: dict[str, Any] = {}
    for key, value in when.items():
        if key == "for_s":
            continue
        if key in ("music", "microphone"):
            if not isinstance(value, bool):
                return {}, f"{key} must be true or false"
            found[key] = value
        elif key in ("cpu_over", "gpu_over"):
            if number(value) is None:
                return {}, f"{key} must be a number (a load in percent)"
            found[key] = number(value)
        elif key == "apps":
            items = value if isinstance(value, list) else []
            names = {x.strip().lower() for x in items if isinstance(x, str)} - {""}
            if not names:
                return {}, "apps must be a list of program names"
            found[key] = names
        elif key == "time":
            if parse_time(value) is None:
                return {}, 'time must look like "22:00-06:30"'
            found[key] = parse_time(value)
        else:
            return {}, f"unknown condition {key!r} (known: {', '.join(CONDITIONS)})"
    if not found:
        return {}, "it has no conditions"
    return found, ""


def parse_rules(raw: list[Any], report: Callable[[str], None]) -> list[Rule]:
    """The ``rules`` option; a rule that can never match is reported and left out."""
    rules = []
    for n, entry in enumerate(raw, 1):
        if isinstance(entry, dict) and entry.get("enabled", True) is False:
            continue
        name = str(entry.get("name") or f"rule {n}") if isinstance(entry, dict) else f"rule {n}"
        theme = entry.get("theme") if isinstance(entry, dict) else None
        if not isinstance(theme, str) or not theme:
            report(f"rule {name!r} skipped: it needs a theme")
            continue
        when, problem = parse_conditions(entry.get("when"))
        if problem:
            report(f"rule {name!r} skipped: {problem}")
            continue
        for_s, hold = number(entry["when"].get("for_s", 20.0)), number(entry.get("hold_s", 0.0))
        if for_s is None or for_s <= 0 or hold is None or hold < 0:
            report(f"rule {name!r} skipped: for_s and hold_s must be numbers of seconds")
            continue
        hold = hold if "hold_s" in entry else None
        immediate = entry.get("immediate") is True
        rules.append(Rule(name, theme, when, min(for_s, LONGEST_S), hold, immediate))
    return rules


def theme_installed(name: str) -> bool:
    from libre_panel.theme.model import ThemeError, find_theme

    try:
        find_theme(name)
    except ThemeError:
        return False
    return True


@dataclass
class Signals:
    """What the rules look at, as of the last sample."""

    music: bool = False
    microphone: bool = False
    app: str | None = None  # the program in front (where that is known)
    running: set[str] = field(default_factory=set)  # else the programs that run
    minutes: int = 0  # the time of day
    loads: deque[tuple[float, float | None, float | None]] = field(default_factory=deque)

    def mean(self, which: str, window_s: float, now: float) -> float | None:
        """The load ('cpu' or 'gpu') averaged over ``window_s``; None while less than
        four fifths of the window has been seen (a peak right after the start must
        not count as a load)."""
        column = 1 if which == "cpu" else 2
        seen = [s for s in self.loads if now - s[0] <= window_s and s[column] is not None]
        if not seen or now - seen[0][0] < FILL * window_s:
            return None
        return sum(s[column] for s in seen) / len(seen)


class Autopilot(Service):
    """Shows the theme of the first rule that holds."""

    name = "autopilot"
    api = 1
    options = {
        "rules": ("list", []),
        "hold_s": ("number", 8.0),
        "min_s": ("number", 20.0),
        "quiet_after_boot_s": ("number", 240.0),
        "transition": ("string", "tube"),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        machine: Any = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = datetime.now,
        installed: Callable[[str], bool] = theme_installed,
    ) -> None:
        super().__init__(host, options)
        self.machine = machine or computer.current()
        self.clock, self.wall, self.installed = clock, wall, installed
        self.rules = parse_rules(options["rules"], lambda text: host.log.warning("%s", text))
        used = {key for rule in self.rules for key in rule.when}
        self.reads = {key for key in CONDITIONS if key in used}
        loaded = [r.for_s for r in self.rules if {"cpu_over", "gpu_over"} & r.when.keys()]
        self.longest = max(loaded, default=0.0)  # how far back the load is kept
        self.signals = Signals()
        self.rule: Rule | None = None  # the rule whose theme is shown
        self.theme: str | None = None  # that theme (None: the user's own)
        self._candidate: tuple[str | None, float] = (None, 0.0)  # what the rules say, since when
        self._changed = float("-inf")
        self._asked: dict[str, float] = {}  # slow signals: when each was last asked
        self._installed: dict[str, tuple[bool, float]] = {}
        self._past_boot = False
        self._stopped = False

    def start(self) -> None:
        if not self.rules:
            self.host.log.info("no usable rules in [services.autopilot]: nothing to do")
            return
        keys = []
        if "music" in self.reads:
            keys.append("media.playing")
        if "cpu_over" in self.reads:
            keys.append("cpu.load")
        if "gpu_over" in self.reads:
            keys.append("gpu.load")
        self.host.want(*keys)
        self.host.on("sample", self.on_sample)

    def stop(self) -> None:
        self._stopped = True  # a sample that is under way then asks for nothing

    # -- every sample ------------------------------------------------------------

    def on_sample(self, snapshot: Snapshot) -> None:
        if self._stopped:
            return
        now = self.clock()
        self._read(snapshot, now)
        self.decide(now)

    def _read(self, snapshot: Snapshot, now: float) -> None:
        signals = self.signals
        if "music" in self.reads:
            signals.music = bool(snapshot.value("media.playing"))
        if self.longest:
            if not signals.loads or now - signals.loads[-1][0] >= LOAD_EVERY_S:
                signals.loads.append(
                    (now, number(snapshot.value("cpu.load")), number(snapshot.value("gpu.load")))
                )
            while signals.loads and now - signals.loads[0][0] > self.longest:
                signals.loads.popleft()
        if "microphone" in self.reads and self._due("microphone", now, SLOW_EVERY_S):
            signals.microphone = self.machine.microphone_in_use()
        if "apps" in self.reads:
            if self.machine.has_foreground:
                if self._due("app", now, SLOW_EVERY_S):
                    signals.app = self.machine.foreground_name()
            elif self._due("running", now, PROGRAMS_EVERY_S):
                signals.running = {program.name for program in self.machine.programs()}
        if "time" in self.reads:
            at = self.wall()
            signals.minutes = at.hour * 60 + at.minute

    def _due(self, what: str, now: float, every: float) -> bool:
        """Whether a signal that costs something is asked for again."""
        if now - self._asked.get(what, float("-inf")) < every:
            return False
        self._asked[what] = now
        return True

    # -- the rules -------------------------------------------------------------------

    def holds(self, rule: Rule, now: float, quiet: bool) -> bool:
        """Whether all the conditions of a rule hold. Anything unknown does not."""
        signals = self.signals
        for key, wanted in rule.when.items():
            if key == "music" and signals.music != wanted:
                return False
            if key == "microphone" and signals.microphone != wanted:
                return False
            if key in ("cpu_over", "gpu_over"):
                load = None if quiet else signals.mean(key[:3], rule.for_s, now)
                if load is None or load < wanted:
                    return False
            if key == "apps":
                if self.machine.has_foreground:
                    if signals.app not in wanted:
                        return False
                elif not wanted & signals.running:
                    return False
            if key == "time" and not in_time(wanted, signals.minutes):
                return False
        return True

    def _usable(self, rule: Rule, now: float) -> bool:
        """Whether the rule's theme is installed (asked now and then; said once if not)."""
        known = self._installed.get(rule.theme)
        if known is None or now - known[1] >= THEME_EVERY_S:
            there = self.installed(rule.theme)
            if not there and (known is None or known[0]):
                self.host.log.warning(
                    "rule %r skipped: theme %r is not installed", rule.name, rule.theme
                )
            known = self._installed[rule.theme] = (there, now)
        return known[0]

    def _after_boot_quiet(self) -> bool:
        """Right after the system started the load says nothing about what you do."""
        if self._past_boot:
            return False
        uptime = self.machine.uptime_s()
        if uptime is None or uptime >= self.options["quiet_after_boot_s"]:
            self._past_boot = True
            return False
        return True

    def decide(self, now: float) -> None:
        """Pick the rule that holds, and follow it if the choice has been stable long
        enough and the theme before has stayed long enough."""
        if self.host.mode is not None:
            # A game: the request stays where it is, resting there (mode=None), and
            # applies again at once after it. A new choice must hold afresh then.
            self._candidate = (self.theme, now)
            return
        quiet = self._after_boot_quiet() if self.longest else False
        usable = [rule for rule in self.rules if self._usable(rule, now)]
        rule = next((r for r in usable if self.holds(r, now, quiet)), None)
        theme = rule.theme if rule else None
        if theme == self.theme:
            self._candidate = (theme, now)
            return
        if self._candidate[0] != theme:
            self._candidate = (theme, now)
        hold = self.options["hold_s"]
        if rule is not None and rule.hold_s is not None:
            hold = rule.hold_s
        stable = now - self._candidate[1] >= hold
        free = now - self._changed >= self.options["min_s"] or (rule is not None and rule.immediate)
        if stable and free:
            self._follow(rule, now)

    def _follow(self, rule: Rule | None, now: float) -> None:
        self.rule, self.theme, self._changed = rule, rule.theme if rule else None, now
        transition = self.options["transition"] or None
        if rule is None:
            self.host.log.info("no rule holds: the theme of config.toml")
            self.host.restore_theme(transition=transition)
        else:
            self.host.log.info("rule %r: theme %r", rule.name, rule.theme)
            self.host.show_theme(rule.theme, transition=transition, priority=0, mode=None)

    def status(self) -> dict[str, Any]:
        """What the autopilot is doing, for a console page."""
        now = self.clock()
        return {
            "rule": self.rule.name if self.rule else None,
            "theme": self.theme,
            "since_s": round(now - self._changed, 1) if self._changed > float("-inf") else None,
            "candidate": self._candidate[0] if self._candidate[0] != self.theme else None,
            "resting": self.host.mode is not None,
            "signals": {
                "music": self.signals.music,
                "microphone": self.signals.microphone,
                "app": self.signals.app,
                "cpu": self.signals.mean("cpu", 20.0, now),
                "gpu": self.signals.mean("gpu", 20.0, now),
            },
        }

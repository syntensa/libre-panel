"""Game mode: the panel changes by itself when you play.

The service looks every few seconds for a game: a program from the list in the
settings, or any program of a game installed through Steam whose window has
been in front once (where Libre Panel cannot tell which window is in front,
any program of such a game that runs). Once it finds one, it publishes the
game's name (``game.name``) and its logo from Steam's cache (``game.logo``),
and switches to the mode ``game`` (``[modes.game]``: its own frame rate and
theme) with an entrance that shows the logo. A few seconds after the game ends
it switches back, with the entrance for the end.

Without the entrance a message ("Game mode") says that the panel changed.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image

from libre_panel.i18n import t
from libre_panel.plugins import Service
from libre_panel.sensors.base import Snapshot
from libre_panel.services import machine as computer
from libre_panel.services.steam import Library

NAME = "game.name"
LOGO = "game.logo"
MODE = "game"
SAMPLE_WAIT_S = 1.0  # at most so long the switch waits for a sample that has the logo
TICK_S = 0.2  # how often the worker looks at that deadline while it waits
POLL_RANGE_S = (0.25, 60.0)  # how often it looks for a game


@dataclass(frozen=True)
class Found:
    """A game that runs."""

    key: str  # the same key, the same game: its program, "steam:<number>", "" for ``force``
    name: str
    pid: int = 0
    path: str = ""
    appid: str = ""  # Steam's number for it, if it is a Steam game


def parse_programs(raw: list[Any], report: Callable[[str], None]) -> dict[str, str]:
    """The ``programs`` option: ``"game.exe"`` or ``"game.exe = A Better Name"``,
    as {program in lower case: name to show}."""
    programs = {}
    for entry in raw:
        if not isinstance(entry, str) or not entry.partition("=")[0].strip():
            report(f"programs: {entry!r} is no program name")
            continue
        program, _, label = entry.partition("=")
        program = program.strip().lower()
        programs[program] = label.strip() or program.removesuffix(".exe")
    return programs


class Detector:
    """Tells whether a game runs. Stays on a game for ``linger_s`` after its
    program ended (games start again while they are set up; launchers swap
    their process)."""

    def __init__(
        self,
        machine: Any,
        library: Library,
        programs: dict[str, str],
        steam: bool = True,
        linger_s: float = 6.0,
    ) -> None:
        self.machine, self.library, self.programs = machine, library, programs
        self.steam, self.linger_s = steam, linger_s
        self.game: Found | None = None
        self.seen = 0.0  # when its program was last seen
        self.front: set[int] = set()  # programs whose window has been in front once

    def look(self, now: float) -> Found | None:
        """The game that runs at ``now``, or None."""
        running = self.machine.programs() if self.programs or self.steam else []
        hit = self._listed(running) or (self._steam(running) if self.steam else None)
        if hit is not None:
            self.game, self.seen = hit, now
        elif self.game is not None and now - self.seen > self.linger_s:
            self.game = None
            self.front &= {program.pid for program in running}
        return self.game

    def _listed(self, running: list[Any]) -> Found | None:
        for program, label in self.programs.items():
            for found in running:
                if found.name == program:
                    steam = self.library.match(found.path)
                    return Found(
                        program, label, found.pid, found.path, steam.appid if steam else ""
                    )
        return None

    def _steam(self, running: list[Any]) -> Found | None:
        """A program of a game installed through Steam. Where we know which window
        is in front, it must have been in front once: a library also holds programs
        that run all day (a wallpaper)."""
        if not self.library.games():
            return None
        front = self.machine.foreground_pid() if self.machine.has_foreground else None
        current = self.game.pid if self.game else 0
        for program in sorted(running, key=lambda p: p.pid != current):  # keep the game it is
            game = self.library.match(program.path)
            if game is None:
                continue
            if front is not None:
                if program.pid == front:
                    self.front.add(program.pid)
                if program.pid not in self.front:
                    continue
            key = f"steam:{game.appid}"  # one game, whatever the program of it that is in front
            return Found(key, game.name, program.pid, program.path, game.appid)
        return None


class Game(Service):
    """Switches to game mode while a game runs."""

    name = "game"
    api = 1
    options = {
        "programs": ("list", []),
        "steam": ("bool", True),
        "steam_path": ("string", ""),
        "ignore": ("list", []),
        "automatic": ("bool", True),
        "force": ("bool", False),
        "entrance": ("bool", True),
        "logo": ("bool", True),
        "linger_s": ("number", 6.0),
        "poll_s": ("number", 2.0),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        machine: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(host, options)
        self.clock = clock
        self.machine = machine or computer.current()
        self.library = Library(options["steam_path"], options["ignore"], clock)
        programs = parse_programs(options["programs"], lambda text: host.log.warning("%s", text))
        self.detector = Detector(
            self.machine, self.library, programs, options["steam"], options["linger_s"]
        )
        self.poll_s = min(max(float(options["poll_s"]), POLL_RANGE_S[0]), POLL_RANGE_S[1])
        self.found: Found | None = None  # the game the panel is set up for
        self.logo: Image.Image | None = None
        self.in_mode = False
        self.waiting: float | None = None  # since when the switch waits for a sample with the logo
        self._lock = threading.RLock()
        self._ended = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: str | None = None

    def start(self) -> None:
        self.host.on("sample", self.on_sample)
        self._thread = threading.Thread(target=self._run, name="game-mode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._ended.set()
        if self._thread is not None:
            self._thread.join(3.0)
            self._thread = None

    # -- looking ---------------------------------------------------------------------

    def _run(self) -> None:
        due = self.clock() + self.poll_s  # not at once: the panel is still starting
        while True:
            wait = TICK_S if self.waiting is not None else max(0.01, due - self.clock())
            if self._ended.wait(wait):
                return
            try:
                self.advance()
                if self.clock() >= due:
                    due = self.clock() + self.poll_s
                    self.detect()
                self._report(None)
            except Exception as exc:  # one failed round must not end the service
                self._report(f"{type(exc).__name__}: {exc}")

    def _report(self, failure: str | None) -> None:
        """Say a new failure once, and when it is over."""
        if failure != self._failure:
            if failure:
                self.host.log.warning("looking for games failed: %s", failure)
            else:
                self.host.log.info("looking for games works again")
            self._failure = failure

    def detect(self) -> None:
        """Look for a game once and set the panel up for what is found."""
        now = self.clock()
        found = self.detector.look(now) if self.options["automatic"] else None
        if found is None and self.options["force"]:
            found = Found("", t("Game mode"))
        with self._lock:
            if not self._ended.is_set():  # a look that was under way when the service stopped
                self._apply(found, now)

    def _apply(self, found: Found | None, now: float) -> None:
        if found is None:
            if self.found is not None:
                self._end()
            return
        if self.found is not None and self.found.key == found.key:
            return  # still the same game
        self.found, self.logo = found, self._logo_of(found)
        self.host.log.info("game: %s", found.name)
        self.host.publish(NAME, found.name, "", "Game")
        self.host.publish_image(LOGO, self.logo)
        if self.in_mode:
            return  # one game after the other: the screens learn the new name and logo
        if self.logo is None:
            self._enter()
        else:  # the first game screen should have the logo: wait for a sample that has it
            self.waiting = now

    def _logo_of(self, found: Found) -> Image.Image | None:
        if not (self.options["logo"] and found.appid):
            return None
        return self.library.logo(found.appid)

    # -- switching -----------------------------------------------------------------------

    def on_sample(self, snapshot: Snapshot) -> None:
        with self._lock:
            there = self.waiting is not None and snapshot.images.get(LOGO) is not None
            if there and not self._ended.is_set():
                self._enter()

    def advance(self) -> None:
        """The switch no longer waits for a sample once a second has passed."""
        with self._lock:
            late = self.waiting is not None and self.clock() - self.waiting >= SAMPLE_WAIT_S
            if late and not self._ended.is_set():
                self._enter()

    def _enter(self) -> None:
        self.waiting = None
        self.host.set_mode(MODE, transition=self._transition("start"))
        self.in_mode = True
        if not self.options["entrance"]:
            self._announce(t("Game mode"))

    def _end(self) -> None:
        was_in_mode = self.in_mode
        if was_in_mode:
            self.host.set_mode(None, transition=self._transition("end"))
            if not self.options["entrance"]:
                self._announce(t("Game ended"))
        self.host.log.info("game over" if was_in_mode else "game gone before the mode began")
        self.waiting, self.in_mode, self.found, self.logo = None, False, None, None
        self.host.publish(NAME, None, "", "Game")
        self.host.publish_image(LOGO, None)

    def _transition(self, kind: str) -> tuple[str, dict[str, Any]] | None:
        """The entrance with the game's name and logo (a picture, not its key: it
        does not depend on a sample), or the theme's own transition."""
        if not self.options["entrance"]:
            return None
        params: dict[str, Any] = {"kind": kind}
        if self.found is not None and self.found.key:  # a forced mode has no game to name
            params["title"] = self.found.name
        if self.logo is not None:
            params["logo"] = self.logo
        return ("entrance", params)

    def status(self) -> dict[str, Any]:
        """What the service is doing, for a console page."""
        return {
            "game": self.found.name if self.found else None,
            "steam_id": (self.found.appid or None) if self.found else None,
            "mode": MODE if self.in_mode else None,
            "waiting": self.waiting is not None,
        }

    def _announce(self, label: str) -> None:
        if self.found is not None:
            self.host.notify(
                self.found.name, icon="gamepad", kind="game", key="game", rank=1, seconds=3.0,
                payload={"label": label},
            )  # fmt: skip

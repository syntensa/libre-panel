"""A panel beside the first in a process of its own (``[[panels]]``, in the background app).

Two loops in one process share its interpreter: a frame gives it up a hundred times (each
picture pasted, each part composed) and waits each time for the other loop to give it back, so
beside another panel a frame took half as long again as alone and both panels lost frames. In a
process of its own each panel draws as fast as alone.

The first panel's process (:class:`PanelProcess`) starts it and tells it what it needs of the
first: the readings the first panel's loop took (asked for at the pace of this panel's theme),
the ones that follow on every frame, the first panel's mode, theme and fans' beats, where the
panels stand, whether the app quits, *Which is which?*, the colour check's A and B on a panel
(colour_show.py), and the device places its own panels hold. The panel's process
(:func:`child`) runs its loop on those (:func:`libre_panel.app.run` with its ``[[panels]]``
table, which it reads and follows itself) and says back how it is doing
(:class:`RemoteStatus`), the picture it shows when the app asks (Live, the picture of all
panels), the device places it holds, and the readings its theme reads: the sources in the
first panel's process do costly work (the processes, the ping, a sensor of LibreHardwareMonitor)
only while a theme there wants it, so they are told of this one's too. Its log lines go to the
first's log, with its number.
"""

from __future__ import annotations

import io
import logging
import logging.handlers
import multiprocessing
import os
import threading
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

PUSH_S = 0.02  # the readings that follow on every frame go across this often
STATUS_S = 0.25  # how the panel is doing comes back this often
SHOWN_S = 0.2  # its picture, asked for (Live, the picture of all panels), at most this often
FIRST_PICTURE_S = 1.0  # how long the first read of it waits for it to come
START_S = 30.0  # a process that says nothing for so long after its start is ended
# what comes back of the panel's RunStatus (not its picture, the drivers' counters or a hub)
SENT = ("state", "target", "theme", "default_theme", "missing_theme", "sequence", "size",
        "model", "detail", "frames", "mode", "stats")  # fmt: skip
_PIDS: dict[int, int] = {}  # the panels' processes started here: process id -> panel number


def panel_pids() -> dict[int, int]:
    """Each panel's process started here and not ended: its process id -> the panel's number
    (2: the first beside [device]'s). What Live -> Diagnose -> Load calls them by."""
    return dict(_PIDS)


# -- the first panel's side ------------------------------------------------------------------


class RemoteStatus:
    """How a panel in a process of its own is doing, as a :class:`RunStatus` says it: its
    numbers as the process said them last, and ``shown``, its picture, asked for anew when read
    (the one before until it comes)."""

    def __init__(self) -> None:
        from libre_panel.app import RunStatus

        default = RunStatus()
        for f in fields(RunStatus):
            if f.name != "shown":
                setattr(self, f.name, getattr(default, f.name))
        self._picture: bytes | None = None
        self._image: Image.Image | None = None
        self._asked = 0.0
        self._ask: Any = None  # the process's way to be asked for its picture
        self._came = threading.Event()  # (its first picture: the panel photo waits for it)

    @property
    def shown(self) -> Image.Image | None:
        now = time.monotonic()
        if self._ask is not None and now - self._asked >= SHOWN_S:
            self._asked = now
            self._ask()
            if self._picture is None:
                self._came.wait(FIRST_PICTURE_S)
        if self._image is None and self._picture is not None:
            try:
                self._image = Image.open(io.BytesIO(self._picture)).convert("RGB")
            except OSError:
                self._picture = None
        return self._image

    def took(self, picture: bytes) -> None:
        self._picture, self._image = picture, None
        self._came.set()


class PanelProcess:
    """The process of the panel beside the first at ``[[panels]]`` ``n``, and the threads of
    the first panel's process that keep it informed (:data:`PUSH_S`) and hear it."""

    def __init__(self, n: int, path: Path, more: Any, status: RemoteStatus) -> None:
        self.n, self.more, self.status = n, more, status
        ctx = multiprocessing.get_context("spawn")
        self._conn, theirs = ctx.Pipe()
        self._logs: Any = ctx.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sent_state: tuple[Any, ...] | None = None
        self._sent_at = 0.0
        self._fresh: Any = None
        self._fresh_images: dict[str, Any] = {}
        self.heard = False  # whether the process said a word (it started)
        self._held: set[str] = set()  # the device places the process holds (as it said)
        self._marked: set[str] = set()  # those of them noted as taken here
        self._wanting = _Wanting()  # the readings its theme reads, wanted here in its name
        status._ask = lambda: self._send(("shown?",))
        self.process = ctx.Process(target=child, args=(n, str(path), theirs, self._logs),
                                   name=f"panel-{n + 2}", daemon=True)  # fmt: skip
        self.process.start()
        if self.process.pid is not None:
            _PIDS[self.process.pid] = n + 2
        theirs.close()
        self._threads = [
            threading.Thread(target=target, name=f"panel-{n + 2}-{name}", daemon=True)
            for name, target in (
                ("push", self._push),
                ("hear", self._hear),
                ("log", self._log),
            )  # fmt: skip
        ]
        for thread in self._threads:
            thread.start()

    def alive(self) -> bool:
        return self.process.is_alive()

    def stop(self) -> None:
        """End the panel's loop (its panel goes dark or shows the clock, as on quitting)."""
        self._send(("state", self._state()))  # (quitting as it is now: the loop ends so)
        self._send(("stop",))

    def join(self, timeout: float) -> bool:
        """Wait for the process to end (it lets its panel go first), at most ``timeout``; one
        that does not is ended. True when it ended by itself."""
        self.process.join(timeout)
        ended = not self.process.is_alive()
        if not ended:
            self.process.terminate()
            self.process.join(2.0)
        if not self.process.is_alive():
            _PIDS.pop(self.process.pid, None)
        self._stop.set()
        self._unmark()
        _forget(self._wanting)
        try:
            self._conn.close()
        except OSError:
            pass
        self._logs.put(None)
        return ended

    # -- what goes across --

    def _send(self, message: tuple[Any, ...]) -> None:
        with self._lock:
            try:
                self._conn.send(message)
            except (OSError, ValueError, EOFError):
                pass  # the process ended: the receiver says so

    def _state(self) -> dict[str, Any]:
        from libre_panel import colour_show, identify, transition_show
        from libre_panel.devices import base

        more = self.more
        quitting = more.quitting() if more.quitting is not None else None
        beats = more.beats.export() if more.beats is not None else {}
        return {"mode": more._mode(), "first": more._look() or "", "places": more.places(),
                "quitting": quitting, "identify": identify.left(), "beats": beats,
                "transition": transition_show.export(),
                "taken": sorted(base.places_taken() - self._held),
                "colours": colour_show.export()}  # fmt: skip

    def _push(self) -> None:
        hub = self.more.hub
        while not self._stop.wait(PUSH_S):
            now = time.monotonic()
            state = self._state()
            key = tuple(sorted((k, repr(v)) for k, v in state.items() if k != "beats"))
            if key != self._sent_state or now - self._sent_at >= 1.0:  # the beats each second
                self._send(("state", state))
                self._sent_state, self._sent_at = key, now
            if hub.every_frame:
                fresh, images = hub.fresh(True), hub.frame_images(True)
                changed = {k: v for k, v in images.items() if self._fresh_images.get(k) is not v}
                gone = [k for k in self._fresh_images if k not in images]
                if fresh != self._fresh or changed or gone:
                    self._send(("fresh", fresh, changed, gone))
                    self._fresh, self._fresh_images = fresh, dict(images)

    def _hear(self) -> None:
        from libre_panel.devices import base
        from libre_panel.sensors.base import HubView

        while not self._stop.is_set():
            try:
                message = self._conn.recv()
            except (EOFError, OSError):
                break
            kind = message[0]
            if kind == "snapshot?":  # its loop takes a sample: the first's readings, shared
                hub = self.more.hub
                readings, images = hub.shared(HubView.SHARE_S)
                readings.update(hub.fresh())
                self._send(("snapshot", readings, images, hub.age(), hub.every_frame))
            elif kind == "status":
                self.heard = True
                for name, value in message[1].items():
                    setattr(self.status, name, value)
            elif kind == "shown":
                self.status.took(message[1])
            elif kind == "wants":  # what its theme reads: the sources here read it too
                from libre_panel.sensors.base import want

                want(self._wanting, frozenset(message[1]))
            elif kind == "places":  # the device it holds: none of this process's takes it
                self._held = set(message[1])
                for place in self._held - self._marked:
                    if base.take_place(place):
                        self._marked.add(place)
                for place in self._marked - self._held:
                    base.leave_place(place)
                self._marked &= self._held
        if not self._stop.is_set():
            self.status.state = "error"
            self.status.detail = "the panel's process ended"

    def _unmark(self) -> None:
        from libre_panel.devices import base

        for place in self._marked:
            base.leave_place(place)
        self._marked.clear()

    def _log(self) -> None:
        while True:
            try:
                record = self._logs.get()
            except (EOFError, OSError):
                return
            if record is None:
                return
            logging.getLogger(record.name).handle(record)


class _Wanting:
    """Who wants the readings a panel's process said its theme reads (sensors.base.want)."""


def _forget(wanting: _Wanting) -> None:
    from libre_panel.sensors.base import want

    want(wanting, frozenset())


# -- the panel's side ------------------------------------------------------------------------


class _Link:
    """The panel's process's end: what the first panel's process told it last, and its way to
    ask for the readings and to say how the panel is doing."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._replies: list[tuple[Any, ...]] = []
        self._replied = threading.Condition()
        self.state: dict[str, Any] = {"mode": None, "first": "", "places": [], "quitting": None}
        self.fresh: dict[str, Any] = {}
        self.fresh_images: dict[str, Any] = {}
        self.every_frame = False
        self.status: Any = None
        self.beats: Any = None
        self._foreign: set[str] = set()  # device places the other process's panels hold

    def send(self, message: tuple[Any, ...]) -> None:
        with self._lock:
            try:
                self.conn.send(message)
            except (OSError, ValueError, EOFError):
                self.stop.set()

    def ask(self, message: tuple[Any, ...], timeout: float = 5.0) -> tuple[Any, ...] | None:
        with self._replied:
            self._replies.clear()
            self.send(message)
            if not self._replied.wait_for(lambda: self._replies or self.stop.is_set(), timeout):
                return None
            return self._replies.pop() if self._replies else None

    def hear(self) -> None:
        from libre_panel import colour_show, identify, transition_show
        from libre_panel.devices import base

        while True:
            try:
                message = self.conn.recv()
            except (EOFError, OSError):
                self.stop.set()
                return
            kind = message[0]
            if kind == "stop":
                self.stop.set()
                return
            if kind == "state":
                state = message[1]
                self.state = state
                if state["identify"]:  # Which is which? shows on every panel at once
                    identify.show(state["identify"])
                transition_show.adopt(state.get("transition"))  # a transition shown, everywhere
                colour_show.adopt(state.get("colours"))  # the colour check's A and B
                if self.beats is not None and state.get("beats"):
                    self.beats.adopt(state["beats"])
                foreign = set(state.get("taken", ()))
                for place in foreign - self._foreign:
                    base.take_place(place)
                for place in self._foreign - foreign:
                    base.leave_place(place)
                self._foreign = foreign
            elif kind == "fresh":
                _, fresh, changed, gone = message
                self.fresh = fresh
                images = {**self.fresh_images, **changed}
                for key in gone:
                    images.pop(key, None)
                self.fresh_images = images
            elif kind == "snapshot":
                with self._replied:
                    self._replies.append(message)
                    self._replied.notify_all()
            elif kind == "shown?":
                self._picture()

    def _picture(self) -> None:
        frame = getattr(self.status, "shown", None)
        if frame is None:
            return
        buffer = io.BytesIO()
        frame.convert("RGB").save(buffer, "JPEG", quality=88)
        self.send(("shown", buffer.getvalue()))

    def tell(self) -> None:
        """How the panel is doing and the device places it holds, every :data:`STATUS_S`, and
        the readings its theme reads whenever they change."""
        from libre_panel.devices import base
        from libre_panel.sensors.base import wanted_keys

        told: frozenset[str] | None = None
        while not self.stop.wait(STATUS_S):
            status = self.status
            self.send(("status", {name: getattr(status, name) for name in SENT}))
            self.send(("places", sorted(base.places_taken() - self._foreign)))
            wants = wanted_keys()
            if wants != told:
                self.send(("wants", sorted(wants)))
                told = wants


def _remote_hub(link: _Link) -> Any:
    from libre_panel.sensors.base import HubView, Snapshot, remember

    class RemoteHub(HubView):
        """The first panel's readings, from its process: asked for at this panel's pace, and
        those that follow on every frame as it sent them last."""

        def __init__(self) -> None:
            self._history: dict[str, Any] = {}
            self._fresh_images: set[str] = set()
            self._age: tuple[float | None, float] = (None, time.monotonic())

        @property
        def providers(self) -> list[Any]:
            return []

        @property
        def every_frame(self) -> bool:
            return link.every_frame

        def readings_from(self, provider: Any) -> int:
            return 0

        def snapshot(self) -> Snapshot:
            reply = link.ask(("snapshot?",))
            if reply is None:  # (the first panel's process is busy or gone: the last ones)
                readings, images = dict(link.fresh), {}
            else:
                _, readings, images, age, link.every_frame = reply
                self._age = (age, time.monotonic())
            return Snapshot(readings=readings, history=remember(self._history, readings),
                            images=images)  # fmt: skip

        def age(self) -> float | None:
            age, at = self._age
            return None if age is None else age + time.monotonic() - at

        def fresh(self, video: bool = True) -> dict[str, Any]:
            return dict(link.fresh)

        def fresh_images(self, video: bool = True) -> dict[str, Any]:
            images = dict(link.fresh_images)
            gone = self._fresh_images - set(images)
            self._fresh_images = set(images)
            return {**images, **dict.fromkeys(gone)}

    return RemoteHub()


class _Whose(logging.Filter):
    """Every log line of the panel's process is the panel's (cli.py's prefix: "panel 2: ")."""

    def __init__(self, whose: str) -> None:
        super().__init__()
        self.whose = whose

    def filter(self, record: logging.LogRecord) -> bool:
        record.threadName = self.whose
        return True


def child(n: int, path: str, conn: Any, logs: Any) -> None:
    """The panel's process: its loop for ``[[panels]]`` ``n`` of the config.toml at ``path``,
    on what the first panel's process tells it, until it is told to stop."""
    whose = threading.current_thread().name = f"panel-{n + 2}"
    handler = logging.handlers.QueueHandler(logs)
    handler.addFilter(_Whose(whose))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    log.info("in a process of its own (%d)", os.getpid())
    from libre_panel import app
    from libre_panel.render import fan

    link = _Link(conn)
    link.status = app.RunStatus()
    link.beats = fan.Beats()
    for target, name in ((link.hear, "hear"), (link.tell, "tell")):
        threading.Thread(target=target, name=f"panel-{n + 2}-{name}", daemon=True).start()
    hub = _remote_hub(link)
    try:
        app.run_beside(
            n, Path(path), link.stop, link.status, hub,
            quitting=lambda: link.state["quitting"], mode_of=lambda: link.state["mode"],
            look_of=lambda: link.state["first"], places_of=lambda: link.state["places"],
            beats=link.beats,
        )  # fmt: skip
    finally:
        link.send(("status", {"state": "stopped"}))
        conn.close()

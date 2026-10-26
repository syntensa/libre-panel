"""Services (B3) and what they may do: the host between plugins and the main loop.

A service runs in the background app (tray, ``start``) and in ``libre-panel
run``. It talks to the panel only through its :class:`ServiceHost`: publish
readings, states and images, show another theme for a while, switch the mode
(with its own frame rate), show a toast, and hear about events (among them
every new sample of the readings). Every call into a service is guarded: a
failing service is logged and the panel keeps going.
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

import libre_panel
from libre_panel.config import config_dir
from libre_panel.sensors.base import Reading, SensorProvider, Snapshot
from libre_panel.sensors.base import want as want_readings

log = logging.getLogger(__name__)

EVENTS = (
    "panel-connected",
    "panel-lost",
    "panel-restarted",  # back after the driver restarted it to clear a hung decoder
    "theme-changed",
    "mode-changed",
    "sample",  # a new snapshot of the readings (snapshot=), about every refresh_ms
    "fresh",  # between two samples, readings that follow on every frame changed (snapshot=, keys=)
    "shortcut",  # a keyboard shortcut of [hotkeys] was pressed, or its button in the app (action=)
    "quit",
)
NEWEST = ("sample", "fresh")  # delivered in a thread of their own, only the newest of each
OPTION_TYPES = {
    "int": int,
    "number": (int, float),
    "bool": bool,
    "string": str,
    "list": list,
}


def apply_options(schema: dict[str, tuple[str, Any]], given: dict[str, Any], where: str) -> dict:
    """The plugin's defaults, overridden by what config.toml sets (types checked)."""
    from libre_panel.plugins.loader import PluginError

    result = {key: default for key, (_kind, default) in schema.items()}
    for key, value in given.items():
        if key not in schema:
            log.warning("%s: unknown option %r ignored", where, key)
            continue
        kind = schema[key][0]
        expected = OPTION_TYPES.get(kind)
        wrong_bool = isinstance(value, bool) and kind in ("int", "number")
        if expected is None or wrong_bool or not isinstance(value, expected):
            raise PluginError(f"{where}.{key}: expected {kind}, got {value!r}")
        result[key] = value
    return result


@dataclass(frozen=True, eq=False)
class Toast:
    """A short message on the panel (``ServiceHost.notify``).

    ``kind`` names what it is about (``"music"``, ``"volume"``, ...): a theme
    can switch kinds off and a screen can leave out those it shows anyway.
    A toast with the ``key`` of the one on show (default: its kind) replaces
    it in place (the volume while it turns). A higher ``rank`` replaces a
    lower one on the panel; the others wait (see the theme's ``toast.queue``).
    ``payload`` carries what a toast style draws besides the text (e.g.
    ``{"image": cover, "progress": 0.4, "color": "#ff0000"}``). ``seconds``
    None means the theme's hold time.
    """

    text: str
    icon: str | None = None
    level: str = "info"  # info | warning | error
    seconds: float | None = None
    kind: str = ""
    rank: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    service: str = ""  # who sent it
    key: str = ""  # the same key replaces the one on show; empty: the kind


NO_PANEL = "no-panel"  # the panel is not connected or paused: the message waits for it
OFF_IN_THEME = "theme"  # the theme switches its kind off (``toast.off``)
LEFT_OUT = "screen"  # the screen shows its kind itself (``Screen.suppresses``)


@dataclass(frozen=True)
class Delivery:
    """What ``notify`` says about a message: whether it will show, and if not, why.

    ``shown`` is True when nothing stands in its way (a message of a higher rank can
    still take its place). Otherwise ``reason`` is ``"theme"`` (the theme switches its
    kind off), ``"screen"`` (the screen shows its kind itself) or ``"no-panel"``. The
    answer is that of the moment: a theme or screen that comes before the message is
    due changes it. A Delivery is true when the message will show."""

    shown: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.shown


TransitionSpec = str | tuple[str, dict[str, Any]] | None
"""A transition: a name, (name, parameters for it) or None for config.toml's."""

ANY_MODE = "*"  # a theme request that counts in every mode


class Service:
    """Base class for services: long-running helpers such as a game mode.

    Set ``name``, ``api = 1`` and optionally an ``options`` schema
    ({key: (type, default)}; types int, number, bool, string, list). ``start``
    must return quickly (start your own threads); ``stop`` must end them
    within a few seconds.
    """

    name = ""
    options: dict[str, tuple[str, Any]] = {}

    def __init__(self, host: ServiceHost, options: dict[str, Any]) -> None:
        self.host = host
        self.options = options

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class ServiceHost:
    """What one service may do. Thread-safe; call it from any thread."""

    def __init__(self, hub: PluginHost, name: str) -> None:
        self._hub = hub
        self.name = name
        self.log = logging.getLogger(f"libre_panel.service.{name}")

    @property
    def data_dir(self) -> Path:
        """``<settings>/plugins-data/<service>/``: the service's own files."""
        path = config_dir() / "plugins-data" / self.name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def snapshot(self) -> Snapshot | None:
        """The latest readings and history the panel was drawn from."""
        return self._hub.latest

    def publish(self, key: str, value: float | str | None, unit: str = "", label: str = "") -> None:
        """A reading themes can show like any sensor (e.g. ``game.fps``). It goes
        away when the service stops. It reaches the next frame, not only the next
        sample, so ``set_mode`` and ``show_theme`` right after it see the value."""
        self._hub.publish(Reading(key, value, unit, label), owner=self.name)

    def publish_state(self, key: str, value: dict | list | None, label: str = "") -> None:
        """A structured value (a dict or a list, ``None`` for none) that screens, widget
        types and other services read with ``snapshot.state(key, default)`` (or
        ``context.state``). It is kept as JSON text with the time it was published
        (``snapshot.state_stamp(key)``, on the main loop's clock), so it is never
        smoothed and never in a graph's history. It reaches the next frame and goes
        away when the service stops."""
        self._hub.publish_state(key, value, owner=self.name, label=label)

    def unpublish(self, key: str) -> None:
        self._hub.unpublish(key)

    def want(self, *keys: str) -> None:
        """Say which readings the service reads (``"media.*"``, ``"audio.volume"``): a
        source that works on demand (what is playing, the volume, the CPU and GPU
        load, game frame rates) then runs although no theme on the panel shows them.
        Replaces the keys of an earlier call; none takes them back."""
        self._hub.want(self.name, keys)

    def publish_image(self, key: str, image: Image.Image | None) -> None:
        """An image for screens and widgets (e.g. ``media.cover``); None removes it."""
        self._hub.publish_image(key, image, owner=self.name)

    def show_theme(
        self,
        name: str,
        transition: TransitionSpec = None,
        priority: int = 0,
        mode: str | None = ANY_MODE,
    ) -> None:
        """Show another theme until :meth:`restore_theme` (config.toml is not changed).

        ``transition``: ``"cut"``, ``"fade"``, ``"slide"`` or one from a plugin,
        or ``(name, {parameters})`` for a plugin transition that takes some;
        the default is ``transition`` in config.toml. When several services ask
        for a theme, the highest ``priority`` wins, and among equals the last
        to ask. A service's theme wins over a mode's. ``mode`` limits the
        request to one mode: ``None`` = only while no mode is on (an autopilot),
        ``"game"`` = only in game mode; by default it counts in every mode.
        The request stays and counts again when its mode comes back.
        """
        self._hub.request_theme(
            name, by=self.name, transition=transition, priority=priority, mode=mode
        )

    def restore_theme(self, transition: TransitionSpec = None) -> None:
        """Take back this service's :meth:`show_theme` (another service's may show then)."""
        self._hub.request_theme(None, by=self.name, transition=transition)

    @property
    def mode(self) -> str | None:
        return self._hub.mode

    def set_mode(self, name: str | None, transition: TransitionSpec = None) -> None:
        """Switch to a mode from ``[modes.<name>]`` (frame rate, theme); None ends it.
        ``transition`` plays on the switch, also when the theme stays the same.
        A mode (and a theme) a service set ends when the service stops."""
        self._hub.set_mode(name, by=self.name, transition=transition)

    def notify(
        self,
        text: str,
        icon: str | None = None,
        level: str = "info",
        seconds: float | None = None,
        kind: str = "",
        rank: int = 0,
        payload: dict[str, Any] | None = None,
        key: str = "",
    ) -> Delivery:
        """A short message on the panel (see :class:`Toast`). The answer says whether it
        will show or why not (see :class:`Delivery`); it is true when it will."""
        hold = None if seconds is None else float(seconds)
        toast = Toast(text, icon, level, hold, kind, int(rank), dict(payload or {}), self.name, key)
        return self._hub.notify(toast)

    def on(self, event: str, callback: Callable[..., None]) -> None:
        """Hear about ``panel-connected``, ``panel-lost``, ``panel-restarted``
        (after ``panel-connected`` when the panel came back from a restart that
        cleared a hung decoder, not from a replug), ``theme-changed``,
        ``mode-changed``, ``sample`` (``snapshot=``: a new snapshot of the readings,
        about every ``refresh_ms``), ``fresh`` (``snapshot=``, ``keys=``: a reading
        of a source that follows on every frame, the volume, changed between two
        samples; ``keys`` are the readings that did), ``shortcut`` (``action=``: a keyboard
        shortcut of ``[hotkeys]`` was pressed, such as ``timer``) and ``quit``. Callbacks run in
        an event thread; a slow ``sample`` or ``fresh`` callback is handed the newest
        one when it is free again, not every one."""
        self._hub.listen(event, callback, owner=self.name)


class _HostProvider(SensorProvider):
    name = "services"
    every_frame = True  # cheap (a copy of a dict): a published reading reaches the next frame

    def __init__(self, hub: PluginHost) -> None:
        super().__init__()
        self.hub = hub

    def read(self) -> dict[str, Reading]:
        return self.hub.published()


class _Reader:
    """Stands for a service where readings are asked for (``sensors.base.want``)."""


class PluginHost:
    """Shared by all services and the main loop.

    ``clock`` is the main loop's clock (``libre_panel.clock``), which the time stamps
    of states come from.
    """

    MAX_TOASTS = 20

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or libre_panel.clock
        self._lock = threading.Lock()
        # value and the service that owns it: a stopped service leaves nothing behind
        self._published: dict[str, tuple[Reading, str | None]] = {}
        self._images: dict[str, tuple[Image.Image, str | None]] = {}
        self.latest: Snapshot | None = None
        # The frame on the panel now, after transitions and toasts; set by the main loop,
        # read by editor pages (PageContext.shown_frame). It is never drawn on afterwards.
        self.shown: Image.Image | None = None
        # Says whether a toast would show now (the main loop knows the theme, the screen and
        # the panel); None while no loop runs. ``notify`` passes its answer on.
        self.judge: Callable[[Toast], Delivery] | None = None
        # theme requests by service: (theme, priority, order of asking, mode it counts in)
        self._themes: dict[str, tuple[str, int, int, str | None]] = {}
        self._asked = itertools.count()
        # the transition for the next switch, and a count of switches the main
        # loop has not seen yet (a mode change with a transition is one even
        # when the theme stays)
        self._transition: TransitionSpec = None
        self.switches = 0
        self._mode: tuple[str, str | None] | None = None  # (mode, service)
        self._toasts: deque[Toast] = deque(maxlen=self.MAX_TOASTS)
        self._listeners: dict[str, list[tuple[str | None, Callable[..., None]]]] = {}
        self._events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self._dispatcher: threading.Thread | None = None
        # Samples (and the changes between them) have a thread of their own and only the
        # newest of each is kept: a slow listener neither holds up the other events nor
        # falls behind. One thread for both, so a service never hears them at once.
        self._newest: dict[str, dict[str, Any]] = {}
        self._sample_ready = threading.Event()
        self._sampler: threading.Thread | None = None
        self._sampler_stop = False
        self._failed: dict[int, str] = {}  # sample listener (id) -> the last error logged
        self._readers: dict[str, _Reader] = {}

    # -- for services ------------------------------------------------------

    def publish(self, reading: Reading, owner: str | None = None) -> None:
        with self._lock:
            self._published[reading.key] = (reading, owner)

    def publish_state(
        self, key: str, value: dict | list | None, owner: str | None = None, label: str = ""
    ) -> None:
        if value is not None and not isinstance(value, dict | list):
            raise TypeError(
                f"publish_state takes a dict, a list or None, not {type(value).__name__} "
                "(publish takes a number or a text)"
            )
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        self.publish(Reading(key, text, "", label, "", self.clock()), owner=owner)

    def want(self, owner: str, keys: Iterable[str]) -> None:
        with self._lock:
            reader = self._readers.setdefault(owner, _Reader())
        want_readings(reader, frozenset(keys))

    def unpublish(self, key: str) -> None:
        with self._lock:
            self._published.pop(key, None)

    def publish_image(self, key: str, image: Image.Image | None, owner: str | None = None):
        with self._lock:
            if image is None:
                self._images.pop(key, None)
            else:
                self._images[key] = (image.copy(), owner)

    def request_theme(
        self,
        name: str | None,
        by: str,
        transition: TransitionSpec = None,
        priority: int = 0,
        mode: str | None = ANY_MODE,
    ) -> None:
        with self._lock:
            before = self._top_theme()
            if name:
                self._themes[by] = (name, int(priority), next(self._asked), mode)
            else:
                self._themes.pop(by, None)
            if self._top_theme() != before:  # a request that shows nothing new plays nothing
                self._transition = transition
        log.info("service %s: %s", by, f"shows theme {name!r}" if name else "restores the theme")

    def set_mode(
        self, name: str | None, by: str | None = None, transition: TransitionSpec = None
    ) -> None:
        with self._lock:
            if name == self.mode:
                return
            self._mode = (name, by) if name else None
            if transition is not None:  # else a theme request's own transition stays
                self._transition = transition
                self.switches += 1
        log.info("mode: %s", name or "normal")
        self.emit("mode-changed", mode=name)

    def take_transition(self) -> TransitionSpec:
        """The transition a service chose for the switch under way (once)."""
        with self._lock:
            chosen, self._transition = self._transition, None
        return chosen

    def forget(self, owner: str) -> None:
        """A service stopped: its readings, images, listeners, theme and mode go too."""
        with self._lock:
            self._published = {k: v for k, v in self._published.items() if v[1] != owner}
            self._images = {k: v for k, v in self._images.items() if v[1] != owner}
            for event, listeners in self._listeners.items():
                self._listeners[event] = [(o, cb) for o, cb in listeners if o != owner]
            self._themes.pop(owner, None)
            mode_ended = self._mode is not None and self._mode[1] == owner
            if mode_ended:
                self._mode = None
            reader = self._readers.pop(owner, None)
            kept = {id(cb) for cbs in self._listeners.values() for _o, cb in cbs}
            self._failed = {i: text for i, text in self._failed.items() if i in kept}
        if reader is not None:
            want_readings(reader, frozenset())
        if mode_ended:
            self.emit("mode-changed", mode=None)

    def notify(self, toast: Toast) -> Delivery:
        with self._lock:
            self._toasts.append(toast)
        judge = self.judge
        return judge(toast) if judge is not None else Delivery(False, NO_PANEL)

    def listen(self, event: str, callback: Callable[..., None], owner: str | None = None) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r} (known: {', '.join(EVENTS)})")
        with self._lock:
            self._listeners.setdefault(event, []).append((owner, callback))
            if event in NEWEST:
                if self._sampler is None:
                    self._sampler_stop = False
                    self._sampler = threading.Thread(
                        target=self._sample_loop, name="plugin-samples", daemon=True
                    )
                    self._sampler.start()
            elif self._dispatcher is None:
                self._dispatcher = threading.Thread(
                    target=self._dispatch, name="plugin-events", daemon=True
                )
                self._dispatcher.start()

    # -- for the main loop -------------------------------------------------

    def provider(self) -> SensorProvider:
        """Published readings, merged into the snapshot like any sensor source."""
        return _HostProvider(self)

    def published(self) -> dict[str, Reading]:
        with self._lock:
            return {key: reading for key, (reading, _owner) in self._published.items()}

    def images(self) -> dict[str, Image.Image]:
        with self._lock:
            return {key: image for key, (image, _owner) in self._images.items()}

    @property
    def theme(self) -> str | None:
        """The theme services asked for, if any: the highest priority, then the latest."""
        with self._lock:
            return self._top_theme()

    def _top_theme(self) -> str | None:
        top = self._top_request()
        return top[1][0] if top else None

    def _top_request(self) -> tuple[str, tuple[Any, ...]] | None:
        """The request on top, with who asked: the highest priority, then the latest."""
        mode = self._mode[0] if self._mode else None
        requests = [(by, r) for by, r in self._themes.items() if r[3] == ANY_MODE or r[3] == mode]
        return max(requests, key=lambda item: (item[1][1], item[1][2])) if requests else None

    @property
    def theme_owner(self) -> str | None:
        """Who asked for :attr:`theme`: a service's name (for the transition of its switch)."""
        with self._lock:
            top = self._top_request()
        return top[0] if top else None

    @property
    def mode(self) -> str | None:
        mode = self._mode
        return mode[0] if mode else None

    @property
    def mode_owner(self) -> str | None:
        """Who set the mode on now: a service's name, or "pages" (libre_panel.modes)."""
        mode = self._mode
        return mode[1] if mode else None

    def take_toasts(self) -> list[Toast]:
        with self._lock:
            toasts = list(self._toasts)
            self._toasts.clear()
        return toasts

    def emit(self, event: str, wait: bool = False, **data: Any) -> None:
        """Tell the listeners, in the event thread (or here, with ``wait``). The
        main loop emits ``sample`` (``snapshot=``) after every new snapshot."""
        if not self._listeners.get(event):
            return
        if wait:
            self._deliver(event, data)
        elif event in NEWEST:
            with self._lock:
                held = self._newest.get(event)
                if event == "fresh" and held is not None:  # what moved before is not lost
                    data = {**data, "keys": held.get("keys", frozenset()) | data["keys"]}
                self._newest[event] = data  # one not yet delivered is replaced: the newest counts
            self._sample_ready.set()
        else:
            self._events.put((event, data))

    def fresh(
        self, snapshot: Snapshot, before: dict[str, Reading], latest: dict[str, Reading]
    ) -> None:
        """For the main loop, between two samples: ``latest`` (the readings of the sources
        that follow on every frame) went into ``snapshot``, whose readings were ``before``.
        Tell the ``fresh`` listeners which readings changed, if any did."""
        if not self._listeners.get("fresh"):
            return
        moved = frozenset(
            key
            for key, reading in latest.items()
            if (old := before.get(key)) is not reading and old != reading  # same object: same
        )
        if moved:
            self.emit("fresh", snapshot=snapshot, keys=moved)

    def _deliver(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            callbacks = [callback for _owner, callback in self._listeners.get(event, [])]
        for callback in callbacks:
            try:
                callback(**data)
            except Exception as exc:
                if event in NEWEST:  # ten times a second: say each failure once
                    text = f"{type(exc).__name__}: {exc}"
                    if self._failed.get(id(callback)) == text:
                        continue
                    self._failed[id(callback)] = text
                log.exception("a %s listener failed", event)

    def _sample_loop(self) -> None:
        while True:
            self._sample_ready.wait()
            self._sample_ready.clear()
            with self._lock:
                newest, self._newest = self._newest, {}
                stopping = self._sampler_stop
            if stopping:
                return
            for event, data in newest.items():
                self._deliver(event, data)

    def _dispatch(self) -> None:
        while True:
            item = self._events.get()
            if item is None:
                return
            self._deliver(*item)

    def close(self) -> None:
        if self._dispatcher is not None:
            self._events.put(None)
            self._dispatcher.join(5)
            self._dispatcher = None
        with self._lock:
            sampler, self._sampler = self._sampler, None
            self._sampler_stop = True
        if sampler is not None:
            self._sample_ready.set()
            sampler.join(5)


class ServiceManager:
    """Starts the services enabled in config.toml, and keeps them in step with it."""

    STOP_S = 5.0

    def __init__(self, host: PluginHost, registry: Any = None) -> None:
        from libre_panel.plugins.loader import registry as installed

        self.host = host
        self.registry = registry or installed()
        self.running: dict[str, tuple[Service, dict[str, Any]]] = {}
        self.state: dict[str, str] = {}
        self._wanted: dict[str, dict[str, Any]] = {}  # enabled services and their options
        self._lock = threading.Lock()

    def apply(self, config: Any) -> None:
        """Start, stop or restart services so they match ``config.services``."""
        wanted = {name: config.services.options.get(name, {}) for name in config.services.enabled}
        with self._lock:
            self._wanted = dict(wanted)
            for name in [n for n in self.running if n not in wanted]:
                self._stop(name)
            for name, given in wanted.items():
                if name in self.running:
                    if self.running[name][1] == given:
                        continue
                    self._stop(name)  # its options changed
                self._start(name, given)
            for name in [n for n in self.state if n not in wanted]:
                del self.state[name]

    def restart(self, name: str) -> str:
        """Stop an enabled service and start it again; returns its new state."""
        with self._lock:
            if name not in self._wanted:
                raise LookupError(f"service {name!r} is not enabled in config.toml")
            if name in self.running:
                self._stop(name)
            self._start(name, self._wanted[name])
            return self.state[name]

    def _start(self, name: str, given: dict[str, Any]) -> None:
        from libre_panel.plugins.loader import PluginError

        cls = self.registry.get("services", name)
        if cls is None:
            found = self.registry.parts.get("services", {}).get(name)
            self.state[name] = f"failed: {found.error}" if found else "not installed"
            log.error("service %r: %s", name, self.state[name])
            return
        try:
            options = apply_options(cls.options, given, f"services.{name}")
            service = cls(ServiceHost(self.host, name), options)
            service.start()
        except PluginError as exc:
            self.state[name] = f"failed: {exc}"
            log.error("service %r not started: %s", name, exc)
            return
        except Exception as exc:
            self.state[name] = f"failed: {type(exc).__name__}: {exc}"
            log.exception("service %r failed to start", name)
            return
        self.running[name] = (service, dict(given))
        self.state[name] = "running"
        log.info("service %r started", name)

    def _stop(self, name: str) -> None:
        service, _ = self.running[name]  # "running" until its stop() has returned
        worker = threading.Thread(target=self._stop_one, args=(name, service), daemon=True)
        worker.start()
        worker.join(self.STOP_S)
        if worker.is_alive():
            log.warning("service %r did not stop within %.0f s", name, self.STOP_S)
        del self.running[name]
        self.host.forget(name)
        self.state[name] = "stopped"

    @staticmethod
    def _stop_one(name: str, service: Service) -> None:
        try:
            service.stop()
        except Exception:
            log.exception("service %r failed while stopping", name)

    def stop(self) -> None:
        self.host.emit("quit", wait=True)  # before the services stop and lose their listeners
        with self._lock:
            for name in list(reversed(self.running)):
                self._stop(name)

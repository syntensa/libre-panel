"""Wires config, sensors, renderer and display together."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from libre_panel import i18n
from libre_panel.config import Config, ConfigError, load_config
from libre_panel.devices.base import DeviceError, Display, FrameError, create_display
from libre_panel.devices.models import find_model
from libre_panel.plugins.render import RenderContext
from libre_panel.render.overlays import ToastLayer, transition
from libre_panel.render.renderer import Renderer, changed_region
from libre_panel.sensors.base import SensorHub, SensorProvider, create_provider
from libre_panel.theme.model import THEME_FILENAME, Theme, ThemeError, find_theme, load_theme

log = logging.getLogger(__name__)


def build_hub(config: Config, demo: bool = False) -> SensorHub:
    providers: list[SensorProvider] = []
    names = ["demo"] if demo else config.sensors.providers
    for name in names:
        providers.append(create_provider(name, config.sensors.options.get(name, {})))
    if config.weather.enabled and not demo:
        from libre_panel.weather.open_meteo import OpenMeteoProvider

        providers.append(OpenMeteoProvider(config.weather))
    return SensorHub(providers)


def load_configured_theme(config: Config) -> Theme:
    return load_theme(find_theme(config.theme))


def target_size(config: Config, theme: Theme) -> tuple[int, int]:
    """Frame size the configured panel expects, in the theme's orientation."""
    panel = find_model(config.device.model)
    if panel is None:
        return theme.width, theme.height
    size = panel.size(theme.orientation)
    if size != (theme.width, theme.height):
        log.warning(
            "theme %r is %dx%d but %s is %dx%d; the frame will be scaled to fit. "
            "Adapt the theme to this panel in the editor for a sharp result.",
            config.theme,
            theme.width,
            theme.height,
            panel.label,
            *size,
        )
    return size


def fit_frame(frame: Image.Image, size: tuple[int, int], background: str) -> Image.Image:
    if frame.size == size:
        return frame
    return ImageOps.pad(frame, size, method=Image.Resampling.LANCZOS, color=background)


class _Watcher:
    """Notices when config.toml or the active theme.json change on disk.

    Saving in the editor (or any text editor) updates the panel without a restart.
    """

    def __init__(self, *paths: Path | None, known: dict[Path, int | None] | None = None) -> None:
        self.paths = [p for p in paths if p is not None]
        known = known or {}
        self.stamps = [known[p] if p in known else self._stamp(p) for p in self.paths]

    @staticmethod
    def _stamp(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def changed(self) -> bool:
        stamps = [self._stamp(p) for p in self.paths]
        changed, self.stamps = stamps != self.stamps, stamps
        return changed


def _theme_file(theme: Theme) -> Path | None:
    return theme.root / THEME_FILENAME if theme.root else None


@dataclass
class RunStatus:
    """What the main loop is doing, for the tray icon and the editor.

    The loop thread writes it; other threads only read single attributes.
    """

    state: str = "starting"  # starting | showing | waiting
    target: str = ""  # where frames go: a panel model, or a PNG file
    theme: str = ""
    detail: str = ""  # why the panel is not available
    frames: int = 0
    mode: str | None = None  # set by a service, e.g. "game"


class _Link:
    """Keeps the panel connected: a missing or unplugged panel is retried with
    growing pauses instead of ending the program (autostart before USB is up,
    replugging, standby)."""

    BACKOFF_S = (1, 2, 5, 10, 30)

    def __init__(self, display: Display, brightness: int, status: RunStatus) -> None:
        self.display = display
        self.brightness = brightness
        self.status = status
        self.connected = False
        self.failures = 0
        self.retry_at = 0.0

    def ensure(self, now: float) -> bool:
        if self.connected:
            return True
        if now < self.retry_at:
            return False
        try:
            self.display.open()
            self.display.set_brightness(self.brightness)
        except DeviceError as exc:
            self.display.close()
            self._failed(exc, now)
            return False
        if self.failures:
            log.info("panel connected")
        self.connected, self.failures = True, 0
        self.status.target = self.display.describe()
        self.status.state, self.status.detail = "showing", ""
        return True

    def show(self, frame: Image.Image, region: tuple[int, int, int, int], now: float) -> bool:
        try:
            self.display.show(frame, region)
        except FrameError as exc:
            log.error("frame skipped: %s", exc)
            return False
        except DeviceError as exc:
            self.display.close()
            self.connected = False
            self._failed(exc, now)
            return False
        self.status.frames += 1
        self.status.target = self.display.describe()  # auto may switch from PNG to a panel
        return True

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent
        if self.connected:
            try:
                self.display.set_brightness(percent)
            except DeviceError as exc:
                log.warning("brightness not set: %s", exc)

    def _failed(self, exc: Exception, now: float) -> None:
        self.status.state, self.status.detail = "waiting", str(exc)
        if self.failures == 0:
            log.warning("panel not available: %s (retrying in the background)", exc)
        delay = self.BACKOFF_S[min(self.failures, len(self.BACKOFF_S) - 1)]
        self.failures += 1
        self.retry_at = now + delay


def _theme_name(config: Config, host: Any) -> str:
    """A theme a service shows, else the active mode's theme, else config.toml's."""
    if host is not None:
        if host.theme:
            return host.theme
        mode = config.modes.get(host.mode or "")
        if mode is not None and mode.theme:
            return mode.theme
    return config.theme


def _fps(config: Config, host: Any, display: Any = None) -> int:
    """Frames per second: the active mode's, else a streaming display's, else config.fps."""
    mode = config.modes.get(host.mode or "") if host is not None else None
    if mode is not None and mode.fps:
        return mode.fps
    if display is not None and display.streaming:
        return display.stream_fps
    return config.fps


class _Pacer:
    """Frame clock for streaming displays, without drift (SPUR II).

    Frame n is due at anchor + n / fps. A loop that falls more than a frame
    behind takes a new anchor instead of sprinting to catch up: the panel's
    player would show the burst late anyway.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock, self.sleep = clock, sleep
        self.fps = 0
        self.frames = 0
        self.anchor = (0.0, 0)
        self.reanchored = 0

    def reset(self) -> None:
        self.fps = 0

    def wait(self, fps: int, stop: threading.Event) -> float:
        """Wait until the next frame is due; returns the time it is planned for.

        Drawn at its planned time, a frame that starts a little late does not
        bend an animation (SPUR II: 20 ms steps, not 41 and then 13).
        """
        now = self.clock()
        if fps != self.fps:
            self.fps, self.anchor = fps, (now, self.frames)
        self.frames += 1
        start, first = self.anchor
        due = start + (self.frames - first) / fps
        if now < due:
            if due - now > 0.05:
                stop.wait(due - now)
            else:
                self.sleep(due - now)  # precise also on Windows (high-resolution timer)
        elif now - due > 1 / fps:
            self.anchor = (now, self.frames)
            self.reanchored += 1
            return now
        return due


def _blend(
    blend: tuple[Any, Image.Image, float], frame: Image.Image, now: float
) -> tuple[Image.Image, tuple[Any, Image.Image, float] | None]:
    """The frame a transition shows now, and the transition if it goes on."""
    kind, old, began = blend
    progress = (now - began) / kind.duration if kind.duration else 1.0
    if progress >= 1 or old.size != frame.size:
        return frame, None
    try:
        out = kind.frame(old, frame, progress)
        if out.size != frame.size:
            raise ValueError(f"drew {out.size[0]}x{out.size[1]}, not {frame.width}x{frame.height}")
    except Exception:  # a broken transition must not blank the panel
        log.exception("transition %r failed", getattr(kind, "name", kind))
        return frame, None
    return out if out.mode == frame.mode else out.convert(frame.mode), blend


def _begin(
    kind: Any,
    last_frame: Image.Image | None,
    shown_frame: Image.Image | None,
    now: float,
    toasts: ToastLayer,
) -> tuple[Any, Image.Image, float] | None:
    """A transition starts, from the last frame (with its toast, if the
    transition asks for that); a toast it must not cover steps aside."""
    if kind is None or last_frame is None:
        return None
    old = last_frame
    if getattr(kind, "from_shown", False) and shown_frame is not None:
        old = shown_frame
    if getattr(kind, "toasts", "wait") == "restart":
        toasts.set_aside()
    return (kind, old, now)


def _same_device(a, b) -> bool:
    """Brightness changes on the open panel; anything else needs it opened again."""
    return replace(a, brightness=0) == replace(b, brightness=0)


def run(
    config: Config,
    once: bool = False,
    stop: threading.Event | None = None,
    status: RunStatus | None = None,
    host: Any = None,
    on_config: Callable[[Config], None] | None = None,
) -> None:
    """Main loop: read sensors at the theme's rate, render at ``config.fps`` so
    values glide between readings, and send only frames that changed. A
    streaming display (video mode) gets every frame at its own steady rate.

    With ``once`` a single frame is sent and any device error is raised.
    ``status`` is kept up to date for status displays. ``host`` connects the
    services (a :class:`~libre_panel.plugins.PluginHost`); ``on_config`` hears
    about every config.toml that was loaded.
    """
    stop = stop or threading.Event()
    status = status or RunStatus()
    i18n.set_language(config.language)
    shown = _theme_name(config, host) if not once else config.theme
    try:
        theme = load_theme(find_theme(shown))
    except ThemeError:
        if shown == config.theme:
            raise
        log.warning("theme %r not found; showing %r", shown, config.theme)
        shown = config.theme
        theme = load_configured_theme(config)
    refused: str | None = None  # a requested theme that failed to load
    status.theme = shown
    for warning in theme.warnings:
        log.warning("theme %s: %s", shown, warning)
    renderer = Renderer(theme, animate=config.fps > 1)
    hub = build_hub(config)
    if host is not None and not once:
        hub.providers.append(host.provider())
    display = create_display(config.device)
    size = target_size(config, theme)
    known = {config.path: config.stamp} if config.path and config.stamp else None
    watcher = _Watcher(config.path, _theme_file(theme), known=known)
    link = _Link(display, config.device.brightness, status)
    toasts = ToastLayer(renderer)
    blend: tuple[Any, Image.Image, float] | None = None  # a transition under way
    shown_frame: Image.Image | None = None  # the last frame as it went out, toasts and all
    switches = host.switches if host is not None else 0  # mode changes with a transition
    last_frame: Image.Image | None = None
    pacer = _Pacer()
    planned: float | None = None  # when the pacer planned the next streaming frame
    previous = None
    snapshot = None
    next_sample = 0.0
    try:
        if once:
            display.open()
            display.set_brightness(config.device.brightness)
            frame, _ = renderer.render(hub.snapshot())
            display.show(fit_frame(frame, size, theme.background_color), None)
            return
        while not stop.is_set():
            started = time.perf_counter()  # fine-grained on every system (monotonic is not
            # on Windows before Python 3.13); a streaming frame is drawn at its planned time
            now, planned = (planned if planned is not None else started), None
            # While a transition plays, a switch waits for its end, unless the
            # transition says otherwise (Transition.switch: follow or restart).
            policy = getattr(blend[0], "switch", "wait") if blend is not None else None
            may_switch = policy in (None, "follow", "restart")
            changed = watcher.changed() if may_switch else False
            wanted = _theme_name(config, host)
            if may_switch and (changed or (wanted != shown and wanted != refused)):
                try:
                    fresh = load_config(config.path) if changed and config.path else config
                    wanted = _theme_name(fresh, host)
                    new_theme = load_theme(find_theme(wanted))
                except (ConfigError, ThemeError) as exc:
                    log.warning("keeping theme %r: %s", shown, exc)  # e.g. half-written file
                    refused = wanted
                else:
                    device = config.device
                    if not _same_device(device, fresh.device):
                        log.info("device settings changed; opening the panel again")
                        display.close()
                        display = create_display(fresh.device)
                        link = _Link(display, fresh.device.brightness, status)
                        device = fresh.device
                    elif fresh.device.brightness != device.brightness:
                        link.set_brightness(fresh.device.brightness)
                        device.brightness = fresh.device.brightness
                    fresh.device, config = device, fresh
                    i18n.set_language(config.language)
                    renderer.close()
                    theme, renderer = new_theme, Renderer(new_theme, animate=config.fps > 1)
                    size, previous, next_sample = target_size(config, theme), None, 0.0
                    known = {config.path: config.stamp} if config.path and config.stamp else None
                    watcher = _Watcher(config.path, _theme_file(theme), known=known)
                    toasts.set_renderer(renderer)
                    if wanted != shown:
                        chosen = host.take_transition() if host is not None else None
                        if policy != "follow":  # a following one uncovers the new theme
                            ctx = RenderContext(renderer)
                            kind = transition(chosen or config.transition, ctx)
                            blend = _begin(kind, last_frame, shown_frame, now, toasts) or blend
                        if host is not None:
                            switches = host.switches  # this was the switch
                            host.emit("theme-changed", theme=wanted)
                    shown, refused, status.theme = wanted, None, wanted
                    log.info("showing theme %r", shown)
                    if changed and on_config is not None:
                        on_config(config)
            restart = blend is not None and getattr(blend[0], "switch", "wait") == "restart"
            if host is not None and host.switches != switches and (blend is None or restart):
                switches = host.switches  # a mode change that keeps the theme, with a transition
                kind = transition(host.take_transition(), RenderContext(renderer))
                blend = _begin(kind, last_frame, shown_frame, now, toasts) or blend
            status.mode = host.mode if host is not None else None
            if host is not None:
                toasts.add(host.take_toasts())
            if started >= next_sample or snapshot is None:
                snapshot = hub.snapshot()
                if host is not None:
                    snapshot.images = host.images()
                    host.latest = snapshot
                every = (config.refresh_ms or theme.refresh_ms) / 1000
                next_sample = started + every
                renderer.new_sample(now, every)
            elif hub.every_frame:  # cheap sources follow on every frame (a new dict:
                snapshot.readings = {**snapshot.readings, **hub.fresh()}  # helpers may read)
            was_connected = link.connected
            if link.ensure(started):
                streaming = display.streaming
                # a slow piece (a new background) must not stall a video
                renderer.background_builds = streaming
                renderer.continuous = streaming  # graphs scroll, values glide on
                renderer.fps = _fps(config, host, display)  # screens may pace animations by it
                over = blend is not None and getattr(blend[0], "toasts", "wait") == "over"
                toasts.advance(now, hold=blend is not None and not over)
                renderer.toast = toasts.showing(now)  # screens see a toast the moment it begins
                snapshot.now = datetime.now()  # the clock ticks between readings too
                frame, _ = renderer.render(snapshot, now)
                frame = fit_frame(frame, size, theme.background_color)
                if blend is not None:
                    frame, blend = _blend(blend, frame, now)
                last_frame = frame
                frame = shown_frame = renderer.shown = toasts.draw(frame, now)
                if streaming:
                    link.show(frame, None, started)
                    previous = None
                else:
                    region = changed_region(previous, frame)
                    if region is not None:
                        previous = frame if link.show(frame, region, started) else None
            else:
                previous = None  # send a full frame after reconnecting
            if host is not None and link.connected != was_connected:
                host.emit("panel-connected" if link.connected else "panel-lost")
            if link.connected and display.streaming:
                planned = pacer.wait(_fps(config, host, display), stop)  # a steady clock
                continue
            pacer.reset()
            # While something glides, draw at full fps; otherwise wake for the next
            # reading, but at least twice a second so a seconds clock never skips.
            interval = 1 / _fps(config, host)
            if not (renderer.moving or blend is not None or toasts.active):
                interval = max(interval, min(0.5, next_sample - started))
            stop.wait(max(0.0, interval - (time.perf_counter() - started)))
    finally:
        display.close()
        toasts.close()
        renderer.close()
        hub.close()

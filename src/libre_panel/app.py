"""Wires config, sensors, renderer and display together."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from libre_panel import i18n
from libre_panel.config import Config, ConfigError, load_config
from libre_panel.devices.base import DeviceError, Display, FrameError, create_display
from libre_panel.devices.models import find_model
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

    def __init__(self, *paths: Path | None) -> None:
        self.paths = [p for p in paths if p is not None]
        self.stamps = [self._stamp(p) for p in self.paths]

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


def _fps(config: Config, host: Any) -> int:
    mode = config.modes.get(host.mode or "") if host is not None else None
    return mode.fps if mode is not None and mode.fps else config.fps


def run(
    config: Config,
    once: bool = False,
    stop: threading.Event | None = None,
    status: RunStatus | None = None,
    host: Any = None,
    on_config: Callable[[Config], None] | None = None,
) -> None:
    """Main loop: read sensors at the theme's rate, render at ``config.fps`` so
    values glide between readings, and send only frames that changed.

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
    watcher = _Watcher(config.path, _theme_file(theme))
    link = _Link(display, config.device.brightness, status)
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
            started = time.monotonic()
            changed = watcher.changed()
            wanted = _theme_name(config, host)
            if changed or (wanted != shown and wanted != refused):
                try:
                    fresh = load_config(config.path) if changed and config.path else config
                    wanted = _theme_name(fresh, host)
                    new_theme = load_theme(find_theme(wanted))
                except (ConfigError, ThemeError) as exc:
                    log.warning("keeping theme %r: %s", shown, exc)  # e.g. half-written file
                    refused = wanted
                else:
                    device = config.device  # the open device stays as it is
                    if fresh.device.brightness != device.brightness:
                        link.set_brightness(fresh.device.brightness)
                        device.brightness = fresh.device.brightness
                    fresh.device, config = device, fresh
                    i18n.set_language(config.language)
                    theme, renderer = new_theme, Renderer(new_theme, animate=config.fps > 1)
                    size, previous, next_sample = target_size(config, theme), None, 0.0
                    watcher = _Watcher(config.path, _theme_file(theme))
                    if wanted != shown and host is not None:
                        host.emit("theme-changed", theme=wanted)
                    shown, refused, status.theme = wanted, None, wanted
                    log.info("showing theme %r", shown)
                    if changed and on_config is not None:
                        on_config(config)
            status.mode = host.mode if host is not None else None
            if started >= next_sample or snapshot is None:
                snapshot = hub.snapshot()
                if host is not None:
                    snapshot.images = host.images()
                    host.latest = snapshot
                next_sample = started + (config.refresh_ms or theme.refresh_ms) / 1000
            was_connected = link.connected
            if link.ensure(started):
                snapshot.now = datetime.now()  # the clock ticks between readings too
                frame, _ = renderer.render(snapshot, started)
                frame = fit_frame(frame, size, theme.background_color)
                region = changed_region(previous, frame)
                if region is not None:
                    previous = frame if link.show(frame, region, started) else None
            else:
                previous = None  # send a full frame after reconnecting
            if host is not None and link.connected != was_connected:
                host.emit("panel-connected" if link.connected else "panel-lost")
            # While something glides, draw at full fps; otherwise wake for the next
            # reading, but at least twice a second so a seconds clock never skips.
            interval = 1 / _fps(config, host)
            if not renderer.moving:
                interval = max(interval, min(0.5, next_sample - started))
            stop.wait(max(0.0, interval - (time.monotonic() - started)))
    finally:
        display.close()
        hub.close()

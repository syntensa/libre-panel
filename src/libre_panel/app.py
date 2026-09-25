"""Wires config, sensors, renderer and display together."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

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


class _Link:
    """Keeps the panel connected: a missing or unplugged panel is retried with
    growing pauses instead of ending the program (autostart before USB is up,
    replugging, standby)."""

    BACKOFF_S = (1, 2, 5, 10, 30)

    def __init__(self, display: Display, brightness: int) -> None:
        self.display = display
        self.brightness = brightness
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
        return True

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent
        if self.connected:
            try:
                self.display.set_brightness(percent)
            except DeviceError as exc:
                log.warning("brightness not set: %s", exc)

    def _failed(self, exc: Exception, now: float) -> None:
        if self.failures == 0:
            log.warning("panel not available: %s (retrying in the background)", exc)
        delay = self.BACKOFF_S[min(self.failures, len(self.BACKOFF_S) - 1)]
        self.failures += 1
        self.retry_at = now + delay


def run(config: Config, once: bool = False, stop: threading.Event | None = None) -> None:
    """Main loop: sample sensors, render, push only what changed.

    With ``once`` a single frame is sent and any device error is raised.
    """
    stop = stop or threading.Event()
    theme = load_configured_theme(config)
    for warning in theme.warnings:
        log.warning("theme %s: %s", config.theme, warning)
    renderer = Renderer(theme)
    hub = build_hub(config)
    display = create_display(config.device)
    size = target_size(config, theme)
    watcher = _Watcher(config.path, _theme_file(theme))
    link = _Link(display, config.device.brightness)
    previous = None
    try:
        if once:
            display.open()
            display.set_brightness(config.device.brightness)
            frame, _ = renderer.render(hub.snapshot())
            display.show(fit_frame(frame, size, theme.background_color), None)
            return
        while not stop.is_set():
            started = time.monotonic()
            if watcher.changed():
                try:
                    fresh = load_config(config.path) if config.path else config
                    new_theme = load_configured_theme(fresh)
                except (ConfigError, ThemeError) as exc:
                    log.warning("keeping the current theme: %s", exc)  # e.g. half-written file
                else:
                    device = config.device  # the open device stays as it is
                    if fresh.device.brightness != device.brightness:
                        link.set_brightness(fresh.device.brightness)
                        device.brightness = fresh.device.brightness
                    fresh.device, config = device, fresh
                    theme, renderer = new_theme, Renderer(new_theme)
                    size, previous = target_size(config, theme), None
                    watcher = _Watcher(config.path, _theme_file(theme))
                    log.info("reloaded theme %r", config.theme)
            if link.ensure(started):
                frame, _ = renderer.render(hub.snapshot())
                frame = fit_frame(frame, size, theme.background_color)
                region = changed_region(previous, frame)
                if region is not None:
                    previous = frame if link.show(frame, region, started) else None
            else:
                previous = None  # send a full frame after reconnecting
            interval = (config.refresh_ms or theme.refresh_ms) / 1000
            stop.wait(max(0.0, interval - (time.monotonic() - started)))
    finally:
        display.close()
        hub.close()

"""Wires config, sensors, renderer and display together."""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from libre_panel import clock as loop_clock
from libre_panel import i18n, identify, modes, presence, timing, winsession
from libre_panel.accent import accent_of
from libre_panel.config import Config, ConfigError, brightness_now, load_config
from libre_panel.devices.base import Counters, DeviceError, Display, FrameError, create_display
from libre_panel.devices.models import PanelModel, find_model
from libre_panel.devices.turzx import video_mode
from libre_panel.devices.virtual import VirtualDisplay
from libre_panel.meter import Meter
from libre_panel.plugins.host import LEFT_OUT, NO_PANEL, OFF_IN_THEME, Delivery, Toast
from libre_panel.plugins.render import RenderContext
from libre_panel.render import fan
from libre_panel.render.motion import Motion
from libre_panel.render.overlays import ToastLayer, switch_policy, transition
from libre_panel.render.renderer import Renderer, changed_region, changed_regions
from libre_panel.sensors.base import (
    HubView,
    SensorHub,
    SensorProvider,
    Snapshot,
    create_provider,
)
from libre_panel.sensors.fan_names import FanNames
from libre_panel.sequence import Rotation
from libre_panel.theme.defaults import default_theme, panel_model
from libre_panel.theme.model import (
    THEME_FILENAME,
    Theme,
    ThemeError,
    find_theme,
    load_theme,
    switches_for,
    theme_for,
    with_accent,
)

log = logging.getLogger(__name__)


def build_hub(config: Config, demo: bool = False) -> SensorHub:
    providers: list[SensorProvider] = []
    names = ["demo"] if demo else config.sensors.providers
    for name in names:
        try:
            providers.append(create_provider(name, config.sensors.options.get(name, {})))
        except Exception as exc:  # a misspelt or misconfigured source must not stop the panel
            log.error("sensor source %r not started: %s", name, exc)
    if any(provider.name == "linux" for provider in providers):
        for provider in providers:  # the Linux source reads the kernel's chips: psutil leaves them
            if provider.name == "psutil":
                provider.hwmon = False
    if config.weather.enabled and not demo:
        from libre_panel.weather.open_meteo import OpenMeteoProvider

        providers.append(OpenMeteoProvider(config.weather))
    if not demo:
        from libre_panel.sensors.hardware import HardwareProvider
        from libre_panel.sensors.media import MediaProvider
        from libre_panel.sensors.sky import SkyProvider

        given = {p.name for p in providers}
        if "sky" not in given:  # the sun for the weather's place (if set), the moon everywhere
            place = {"latitude": config.weather.latitude, "longitude": config.weather.longitude}
            providers.append(SkyProvider(place))
        if "media" not in given:  # idle until a theme shows what is playing
            providers.append(MediaProvider(config.sensors.options.get("media", {})))
        if not given & {"hardware", "demo"}:  # a demo has names of its own, made up
            providers.append(HardwareProvider())  # the names of this PC, and [hardware]
    hub = SensorHub(providers, names=FanNames())  # the fans by the names they go by
    hub.follow_config(config)  # [hardware] and the like, which no source's own table holds
    return hub


def load_configured_theme(config: Config, default: str | None = None) -> Theme:
    """The theme config.toml names; where it names none, ``default`` (the panel's own)."""
    return load_theme(find_theme(_theme_name(config, None, default)))


def target_size(config: Config, theme: Theme, name: str | None = None) -> tuple[int, int]:
    """Frame size the configured panel expects, in the theme's orientation. ``name`` is the
    theme's id for the log (the one config.toml names, if not given)."""
    panel = find_model(config.device.model)
    if panel is None:
        return theme.width, theme.height
    size = panel.size(theme.orientation)
    if size != (theme.width, theme.height):
        log.warning(
            "theme %r is %dx%d but %s is %dx%d; the frame will be scaled to fit. "
            "Adapt the theme to this panel in the editor for a sharp result.",
            name or config.theme,
            theme.width,
            theme.height,
            panel.label,
            *size,
        )
    return size


def fit_frame(
    frame: Image.Image, size: tuple[int, int], background: str | tuple[int, ...]
) -> Image.Image:
    """``frame`` scaled into ``size``; ``background`` fills the rest (a colour, not
    a palette name: :func:`background_of` gives the theme's)."""
    if frame.size == size:
        return frame
    return ImageOps.pad(frame, size, method=Image.Resampling.LANCZOS, color=background)


def turned(frame: Image.Image, rotate: int) -> Image.Image:
    """The frame as it goes to the panel: upside down with ``rotate = 180``."""
    return frame.transpose(Image.Transpose.ROTATE_180) if rotate == 180 else frame


def _on_panel(display: Display) -> bool:
    """Whether frames go to a panel: only a panel is turned, never the PNG file
    that stands in for one (it is a picture to look at, like the editor's)."""
    if isinstance(display, VirtualDisplay):
        return False
    using = getattr(display, "using_panel", None)  # auto: a panel, or the PNG file
    return True if using is None else bool(using)


def _place(config: Config) -> tuple[float, float] | None:
    """Where the panel light's sun is seen from: the weather's place in
    config.toml when it is set (the light stands at noon without)."""
    latitude, longitude = config.weather.latitude, config.weather.longitude
    return None if latitude is None or longitude is None else (latitude, longitude)


def _brightness(config: Config) -> int:
    """The brightness the panel shows now (config.brightness_now), the night's sunset and
    sunrise at the weather's place."""
    return brightness_now(config.device, place=_place(config))


def background_of(renderer: Renderer) -> tuple[int, int, int]:
    """The theme's background colour, its palette name (``"@bg"``) resolved."""
    return renderer.color(renderer.theme.background_color)[:3]


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

    def pending(self) -> bool:
        """Whether :meth:`changed` would say so, without taking the change."""
        return [self._stamp(p) for p in self.paths] != self.stamps


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
    # What a config.toml without a theme shows on this panel (Lumen on a bar, Libre Default
    # elsewhere), as the loop worked it out; empty until it has.
    default_theme: str = ""
    # A theme config.toml names that cannot be shown (removed, or from a plugin that is not
    # installed): the panel shows default_theme instead until another is chosen.
    missing_theme: str = ""
    # Where the screens in turn stand (None while there are none, or they are off): the step's
    # index and theme, and the seconds left of it
    sequence: dict[str, Any] | None = None
    # the size of the panel's frames where it is known (the app shows each theme in its
    # version for it: theme.model.theme_for)
    size: tuple[int, int] | None = None
    detail: str = ""  # why the panel is not available
    frames: int = 0
    mode: str | None = None  # set by a service, e.g. "game"
    # The numbers the meter makes about every second (a new dict each time): frame rate,
    # drawing time, data rate, the video link, faults. Empty until the first second is over.
    stats: dict[str, Any] = field(default_factory=dict)
    counters: Counters = field(default_factory=Counters)  # what the drivers count (shared)
    hub: SensorHub | None = None  # the sensor sources the loop reads, for the editor's pages
    # the panels beside this one ([[panels]]), by their place in config.toml: what each loop
    # does (a new dict at each change)
    more: dict[int, RunStatus] = field(default_factory=dict)


def sensor_hint(
    hub: SensorHub | None, readings: dict[str, Any], platform: str = sys.platform
) -> str:
    """What to tell a person who sees "--" where Windows' temperatures and power should be,
    in their language ("" when nothing is the matter, and off Windows). ``readings`` are what
    the panel last drew; LibreHardwareMonitor is asked again, so a hint never outlives the
    fault because the last sample came before it was fixed."""
    if hub is None or platform != "win32":
        return ""
    from libre_panel.sensors.librehardwaremonitor import user_hint
    from libre_panel.sensorservice.setup import unlock_offered

    for provider in list(hub.providers):
        if provider.name != "librehardwaremonitor":
            continue
        try:
            ready = getattr(provider, "wait_ready", lambda timeout: True)(0)
            status = provider.status()
            return user_hint(
                {**readings, **provider.read()}, platform, status, ready, unlock_offered(platform)
            )
        except Exception:  # a hint must not break the state it is part of
            log.debug("no hint about LibreHardwareMonitor", exc_info=True)
    return ""


Box = tuple[int, int, int, int]


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
        if now < self.retry_at or winsession.ending():  # (no new start on the way out)
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

    def show(self, frame: Image.Image, region: Box | list[Box], now: float) -> bool:
        """Send ``frame``'s ``region`` (or each of a list of them, one after the other: a slow
        panel takes the parts that changed rather than one box around all of them)."""
        try:
            for part in region if isinstance(region, list) else [region]:
                self.display.show(frame, part)
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
        delay = getattr(exc, "retry_s", None) or delay  # e.g. a panel restarting: soon
        self.failures += 1
        self.retry_at = now + delay


def _theme_name(
    config: Config,
    host: Any,
    default: str | None = None,
    rotation: Rotation | None = None,
    reported: PanelModel | None = None,
) -> str:
    """A theme a service shows, else the active mode's theme, else the step of the screens in
    turn that is due (``rotation`` follows them), else config.toml's; where that names none,
    the panel's own ``default``. Each in its version for the panel: for its size (Lumen for
    the 12.3" on a 12.3"), else for the way it is mounted (``device.orientation``: Lumen
    Vertical for Lumen on a panel on end)."""
    named = _named(config, host, default, rotation)
    return theme_for(named, config.device.orientation, panel_size(config, reported))


def panel_size(config: Config, reported: PanelModel | None = None) -> tuple[int, int] | None:
    """The size of the panel's frames, as far as it is known without looking on the bus: the
    configured model's, else the one the display reported; turned as it is mounted."""
    panel = find_model(config.device.model) or reported
    if panel is None:
        return None
    return panel.size("portrait" if config.device.orientation == "portrait" else "landscape")


def _named(config: Config, host: Any, default: str | None, rotation: Rotation | None) -> str:
    if host is not None:
        if host.theme:
            return host.theme
        page = modes.page_theme(config, host.mode)  # (music's page has Encore of its own)
        if page:
            return page
    if rotation is not None and config.sequence.active:
        place = rotation.where(config.sequence.steps)
        if place is not None:
            return place.theme
    return config.theme if config.theme_given or default is None else default


def _sequence_state(config: Config, rotation: Rotation) -> dict[str, Any] | None:
    """Where the screens in turn stand, for the app (RunStatus.sequence)."""
    if not config.sequence.active:
        return None
    place = rotation.where(config.sequence.steps)
    if place is None:
        return None
    return {"index": place.index, "theme": place.theme, "left_s": round(place.left_s, 1)}


def calm(config: Config, display: Any) -> bool:
    """Whether the panel's motion is kept calm (``[device] motion``): a picture a second, no
    gliding, turning or fading, for a slow panel (a USB serial one) by itself."""
    motion = config.device.motion
    return motion == "calm" or (motion == "auto" and bool(getattr(display, "slow", False)))


def _fps(config: Config, host: Any, display: Any = None) -> int:
    """Frames per second: one where the motion is calm, else the active mode's, else a
    streaming display's, else config.fps."""
    if display is not None and not display.streaming and calm(config, display):
        return 1
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
        clock: Callable[[], float] = loop_clock,
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


def _expects_video(config: Config) -> bool:
    """Whether the panel will probably be driven in video mode, which decides what a frame
    draws ahead (the graphs' strips): a guess before the panel is open. A wrong one costs a
    little time at the first frame, nothing more."""
    device = config.device
    if device.driver not in ("auto", "turzx"):
        return False
    model = find_model(device.model)
    if model is None and device.video.mode == "auto":  # the panel on the bus decides: the
        return bool(device.video.local_clip)  # setup says whether it is meant for video
    return video_mode(device.video, model)[0]


class _Warmup:
    """The next theme's renderer, built in a thread of its own while the panel
    goes on showing the current theme (SPUR II preloads the same way): its
    screen's ``prepare()`` and one frame of its widgets (cards, texts, graph
    strips) are done when it comes on, so the switch does not stall a video."""

    LIMIT_S = 2.0  # a switch waits at most this long, then builds it itself

    def __init__(self) -> None:
        self.key: Any = None
        self.started = 0.0
        self.thread: threading.Thread | None = None
        self.result: tuple[Renderer, Motion] | None = None

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def ready(self, key: Any, now: float) -> bool:
        """Whether a switch to ``key`` may go ahead: warmed up, or waited enough."""
        return self.key == key and (not self.busy or now - self.started > self.LIMIT_S)

    def start(self, key: Any, build: Callable[[], tuple[Renderer, Motion]], now: float) -> None:
        self.discard()
        self.key, self.started = key, now

        def work() -> None:
            try:
                self.result = build()
            except Exception:  # the switch builds it itself and reports what fails
                log.exception("warming up the next theme failed")

        self.thread = threading.Thread(target=work, name="theme-warmup", daemon=True)
        self.thread.start()

    def take(self, key: Any) -> tuple[Renderer, Motion] | None:
        """The renderer warmed up for ``key``, once, if it is done."""
        if self.key != key or self.busy or self.result is None:
            return None
        result, self.result, self.key = self.result, None, None
        return result

    def discard(self) -> None:
        """Close a warmed-up renderer nobody took."""
        if self.result is not None and not self.busy:
            self.result[0].close()
            self.result = None
        self.key = None

    def close(self) -> None:
        if self.thread is not None:
            self.thread.join(5)
        self.discard()


# what changes on the open panel: brightness (the night's too) and the picture's turn
_FOLLOWED = (
    "brightness", "rotate", "night", "night_from", "night_to", "night_brightness",
    "dark_when_away", "away_brightness",
)  # fmt: skip
NIGHT_CHECK_S = 30.0  # how often the loop looks whether night began or ended


def _same_device(a, b) -> bool:
    """Brightness and the picture's turn change on the open panel; anything else
    needs it opened again."""
    return replace(a, **{name: getattr(b, name) for name in _FOLLOWED}) == b


class MainLoop:
    """One frame at a time of what :func:`run` does: follow switches, take
    readings, draw, send. ``run`` drives it with the pacer; a test drives
    :meth:`step` with its own clock, sensor hub, display and host.

    ``hub`` (built from the config without one) also gets the host's readings;
    ``clock`` is the clock of the frames' times (:func:`libre_panel.clock`).
    """

    def __init__(
        self,
        config: Config,
        *,
        once: bool = False,
        status: RunStatus | None = None,
        host: Any = None,
        on_config: Callable[[Config], None] | None = None,
        hub: SensorHub | None = None,
        display: Display | None = None,
        clock: Callable[[], float] = loop_clock,
        panel: int | None = None,
    ) -> None:
        self.status = status = status or RunStatus()
        self.host, self.on_config, self.clock = host, on_config, clock
        self.panel = panel  # a panel beside [device]'s: config.toml's [[panels]][panel] (run)
        self.warmup = _Warmup()  # the next theme, built while this one shows
        self.idle = threading.Event()  # set between frames: the warm-up works then
        i18n.set_language(config.language)
        # A config that names no theme shows its panel's own: worked out from the config (or
        # the USB bus) now, and again when the display says which panel it is (step).
        told = getattr(display, "model", None)  # (a display that is open says so already)
        self.reported: PanelModel | None = told if isinstance(told, PanelModel) else None
        self.worked_out: tuple[Any, str] | None = None  # the default for a changed model or driver
        panel = panel_model(config.device, self.reported)
        self.default = status.default_theme = default_theme(panel)
        named = theme_for(
            config.theme if config.theme_given else self.default,
            config.device.orientation,
            panel_size(config, self.reported),
        )
        # the screens in turn, on the loop's clock
        self.rotation = Rotation(clock)
        self.pages = modes.PageSwitch(clock)  # music's page, the idle clock's
        shown = (
            _theme_name(config, host, self.default, self.rotation, self.reported)
            if not once
            else named
        )
        self.refused: str | None = None  # a requested theme that failed to load
        status.missing_theme = ""
        try:
            theme = load_theme(find_theme(shown))
        except ThemeError:
            if shown == named:
                theme, shown = self._instead_of(named, status)
            else:
                log.warning("theme %r not found; showing %r", shown, named)
                shown = named
                try:
                    theme = load_configured_theme(config, self.default)
                except ThemeError:
                    theme, shown = self._instead_of(named, status)
        if not config.theme_given:
            log.info(
                "no theme in config.toml: %r, the default for %s",
                self.default,
                panel.label if panel else "a panel that is not known yet",
            )
        status.theme = shown
        for warning in theme.warnings:
            log.warning("theme %s: %s", shown, warning)
        # What moves on the panel goes on from theme to theme: every renderer
        # gets this one (gliding values, graph histories).
        self.motion = Motion()
        # The display first: an unknown driver fails here, before any sensor source has
        # started (the background app tries again and again; each try would leave them on).
        self.display = display if display is not None else create_display(config.device)
        self.hub = hub if hub is not None else build_hub(config)
        if host is not None and not once:
            self.hub.providers.append(host.provider())
        self.first: Snapshot | None = None  # the readings the first theme was drawn with
        if not once:
            self._draw_ahead(theme, config)
        self.renderer = renderer = self._renderer(theme, config)
        status.counters, status.stats = Counters(), {}  # a new loop counts from zero
        self.display.counters = status.counters  # the drivers count into these
        status.hub = self.hub
        self.meter = Meter(status, self.clock())
        self.config, self.shown, self.theme = config, shown, theme
        self.size = target_size(config, theme, shown)
        known = {config.path: config.stamp} if config.path and config.stamp else None
        self.watcher = _Watcher(config.path, _theme_file(theme), known=known)
        self.link = _Link(self.display, _brightness(config), status)
        self.night_at = 0.0  # when the loop last looked whether it is night (_follow_night)
        self.presence = presence.changes()  # the person left or came back since: at once
        self.toasts = ToastLayer(renderer)
        if host is not None:
            host.judge = self.judge  # ``notify`` says whether a message will show
        self.blend: tuple[Any, Image.Image, float] | None = None  # a transition under way
        self.began_in: str | None = None  # the mode it began in
        self.shown_frame: Image.Image | None = None  # the last frame as it went out, toasts and all
        self.switches = host.switches if host is not None else 0  # mode changes with a transition
        self.last_frame: Image.Image | None = None
        self.previous: Image.Image | None = None
        self.turn = 0  # how the last frame went out: turned (180) or not
        self.snapshot: Snapshot | None = None
        self.next_sample = 0.0
        self.started = 0.0  # when the last step began, on the clock
        drawn = renderer.snapshot is not None  # (drawn ahead: prepared, and its widgets made)
        if not once and renderer.prepares and not drawn:  # prepare() and light layers at least
            prepare = threading.Thread(target=renderer.warm, args=(None, 0.0), daemon=True)
            prepare.start()
            prepare.join(_Warmup.LIMIT_S)

    def _draw_ahead(self, theme: Theme, config: Config) -> None:
        """Draw the first theme once before the loop starts, as a switch draws the next one
        (:class:`_Warmup`): its texts, cards, graph strips and fans are made here, in a thread
        of its own, and not in the first frame, which took 200 to 350 ms. It is drawn with the
        first readings, which the first frame takes as its sample (the hub is asked once). A
        theme that is not ready after ``_Warmup.LIMIT_S`` is drawn by the first frame."""
        try:
            first = self.hub.snapshot()
        except Exception:  # the first frame asks again, and says what is wrong
            log.exception("the first readings failed")
            return
        if self.host is not None:
            first.images = {**first.images, **self.host.images()}
        self.first, now = first, self.clock()
        streaming = self.display.streaming  # a panel that is open already says so ...
        video = streaming or _expects_video(config)  # ... else the settings are asked
        fps = _fps(config, self.host, self.display)
        if video and not streaming:
            fps = config.device.video.fps  # what a video panel is driven at
        every = (config.refresh_ms or theme.refresh_ms) / 1000
        how = video, fps, (now, every)
        self.idle.set()  # no frame is under way: the drawing need not wait for one
        key = self._warm_key(theme, config)
        self.warmup.start(key, self._warm_job(theme, config, now, first, how), now)
        give_up = time.monotonic() + self.warmup.LIMIT_S  # (wall time, whatever the clock is)
        while not self.warmup.ready(key, now) and time.monotonic() < give_up:
            time.sleep(0.005)

    def _renderer(self, theme: Theme, config: Config) -> Renderer:
        """The renderer for ``theme``: warmed up if it was, else a new one."""
        warmed = self.warmup.take(self._warm_key(theme, config))
        if warmed is None:
            renderer = Renderer(with_accent(theme, accent_of(config.accent)),
                                animate=config.fps > 1, motion=self.motion)  # fmt: skip
        else:
            renderer, motion = warmed
            renderer.motion = self.motion  # from now on the panel's
            self.motion.adopt(motion)  # graph histories it began
        renderer.place = _place(config)
        renderer.fan_choices = fan.Choices.of(config)
        renderer.symbols = config.symbols
        renderer.switches = switches_for(theme, config.switches)
        return renderer

    @staticmethod
    def _warm_key(theme: Theme, config: Config) -> Any:
        theme_file = _theme_file(theme)
        stamp = _Watcher._stamp(theme_file) if theme_file else None
        switches = tuple(sorted(switches_for(theme, config.switches).items()))
        accent = accent_of(config.accent)
        return (str(theme.root), stamp, theme.name, config.fps > 1, config.symbols, switches,
                accent)  # fmt: skip

    def _warmed(
        self, config: Config, host: Any, shown: str, refused: str | None, watcher: _Watcher,
        now: float,
    ) -> bool:  # fmt: skip
        """Whether a switch coming up may happen now. The theme it brings is
        warmed up in a thread of its own first (:class:`_Warmup`) while this
        one goes on; nothing to switch to, or a theme that fails, goes ahead."""
        pending = watcher.pending()
        wanted = _theme_name(config, host, self.default, self.rotation, self.reported)
        if not pending and wanted in (shown, refused):
            return True
        try:
            fresh = load_config(config.path) if pending and config.path else config
            default = self._default_for(fresh)
            fresh_name = _theme_name(fresh, host, default, self.rotation, self.reported)
            theme = load_theme(find_theme(fresh_name))
        except (ConfigError, ThemeError):
            return True  # the switch says why
        if fresh.language != config.language:
            return True  # a new language: modules are laid out in it, at once
        key = self._warm_key(theme, fresh)
        if self.warmup.ready(key, now):
            return True
        if not self.warmup.busy:
            self.warmup.start(key, self._warm_job(theme, fresh, now), now)
        return False

    def _warm_job(
        self,
        theme: Theme,
        config: Config,
        now: float,
        snapshot: Snapshot | None = None,
        how: tuple[bool, int, tuple[float, float]] | None = None,
    ) -> Callable[[], tuple[Renderer, Motion]]:
        """Building and drawing ``theme`` once, with copies of what the panel's
        drawing keeps (it goes on meanwhile). It is drawn with the readings the panel
        has now and as it is driven now (video mode or not, its frame rate, when the
        readings were taken), unless ``snapshot`` and ``how`` say so: the first theme
        has neither yet."""
        motion = self.motion.copy()
        snapshot = self.snapshot if snapshot is None else snapshot
        if snapshot is not None:  # dicts of its own: this thread goes on replacing them
            snapshot = Snapshot(
                dict(snapshot.readings), snapshot.history, snapshot.now, dict(snapshot.images)
            )
        animate = config.fps > 1
        if how is None:
            current = self.renderer
            how = current.continuous, current.fps, current.sample
        continuous, fps, sample = how

        def build() -> tuple[Renderer, Motion]:
            renderer = Renderer(with_accent(theme, accent_of(config.accent)), animate=animate,
                                motion=motion)  # fmt: skip
            renderer.continuous, renderer.fps, renderer.sample = continuous, fps, sample
            renderer.fan_choices = fan.Choices.of(config)  # their rotors, warmed as shown
            renderer.symbols = config.symbols  # and the symbols in their look
            renderer.switches = switches_for(theme, config.switches)  # and as switched
            # widget by widget between the panel's frames, not while one is drawn
            renderer.warm(snapshot, now, pause=lambda: self.idle.wait(0.1))
            return renderer, motion

        return build

    def _instead_of(self, named: str, status: RunStatus) -> tuple[Theme, str]:
        """The panel's own theme, shown while the one config.toml names cannot be (a theme
        that was removed, or came from a plugin that is not installed): the panel stays lit,
        and the app and the tray say which theme is missing. config.toml is left as it is, so
        the theme shows again once it is back; choosing another one ends it."""
        try:
            find_theme(named)
        except ThemeError as exc:
            why = str(exc)
        else:
            why = f"theme {named!r} cannot be loaded"
        if named == self.default:
            raise ThemeError(why)
        theme = load_theme(find_theme(self.default))
        log.warning(
            "%s: showing %r, the panel's own, until it is back or another is chosen",
            why,
            self.default,
        )
        status.missing_theme = named
        self.refused = named  # tried again when config.toml changes, not on every frame
        return theme, self.default

    def _default_for(self, fresh: Config) -> str:
        """The default theme for a config just read: the one now, unless it names another
        panel or driver, which are worked out again (once for a change: a switch asks at
        every frame while its theme is warmed up)."""
        now = self.config.device
        if (fresh.device.model, fresh.device.driver) == (now.model, now.driver):
            return self.default
        reported = self.reported if _same_device(now, fresh.device) else None  # same display
        asked = fresh.device.model, fresh.device.driver, reported
        if self.worked_out is None or self.worked_out[0] != asked:
            self.worked_out = asked, default_theme(panel_model(fresh.device, reported))
        return self.worked_out[1]

    def _follow_panel(self) -> None:
        """A display that has connected says which panel it is. Where config.toml does not
        name the model, that settles the default theme (a bar gets Lumen); a changed default
        is switched to by the usual way, once. A display that says nothing, or nothing new
        (the panel was unplugged), changes nothing."""
        reported = getattr(self.display, "model", None)  # (a plugin's display may not say)
        if not isinstance(reported, PanelModel) or reported == self.reported:
            return
        self.reported = reported
        default = default_theme(panel_model(self.config.device, reported))
        if default != self.default:
            if not self.config.theme_given:
                log.info("the panel is a %s: its default theme is %r", reported.label, default)
            self.default = self.status.default_theme = default

    @property
    def streaming(self) -> bool:
        """Whether a connected streaming display wants every frame (video mode)."""
        return self.link.connected and self.display.streaming

    @property
    def fps(self) -> int:
        """The rate frames are planned at now (a mode's, the display's, config.fps)."""
        return _fps(self.config, self.host, self.display)

    def judge(self, toast: Toast) -> Delivery:
        """Whether a message would show now: the panel has to be connected, and neither
        the theme (``toast.off``) nor the screen (``suppresses``) may leave its kind out.
        ``PluginHost.notify`` asks, from any thread: only attributes are read."""
        if not self.link.connected:
            return Delivery(False, NO_PANEL)
        if toast.kind in self.toasts.off:
            return Delivery(False, OFF_IN_THEME)
        if toast.kind in self.toasts.left_out:
            return Delivery(False, LEFT_OUT)
        return Delivery(True)

    def _follow_night(self) -> None:
        """Night began or ended ([device] night), or the person left or came back
        (presence.py, [device] dark_when_away): the panel takes its brightness. The night is
        looked at twice a minute, on the computer's clock; the person at once."""
        now = time.monotonic()
        changes = presence.changes()
        if now - self.night_at < NIGHT_CHECK_S and changes == self.presence:
            return
        self.night_at, self.presence = now, changes
        device = self.config.device
        wanted = _brightness(self.config)
        if wanted != self.link.brightness:
            if presence.away() and device.dark_when_away:
                why = "the person is away"
            else:
                why = "night" if wanted != device.brightness else "day"
            log.info("the panel's brightness is %d %%: %s", wanted, why)
            self.link.set_brightness(wanted)

    def once(self) -> None:
        """A single frame to the panel; device errors are raised."""
        config, display, renderer = self.config, self.display, self.renderer
        display.open()
        display.set_brightness(_brightness(config))
        frame, _ = renderer.render(self.hub.snapshot())
        frame = fit_frame(frame, self.size, background_of(renderer))
        self.turn = config.device.rotate if _on_panel(display) else 0
        display.show(turned(frame, self.turn), None)

    def _follow_pages(self, config: Config, host: Any, snapshot: Snapshot | None) -> None:
        """Switch to the page that is due (music while it plays, the clock when the PC is
        idle), or back; never over a mode a service set (a game)."""
        owner = host.mode_owner
        if owner is not None and owner != modes.OWNER:
            return
        due = self.pages.due(config, snapshot.readings if snapshot is not None else {})
        if due != (host.mode if owner == modes.OWNER else None):
            host.set_mode(due, by=modes.OWNER)

    def step(self, now: float | None = None) -> Image.Image | None:
        """One frame: follow a switch, take readings when due, draw and send.

        ``now`` is the time on :attr:`clock` the frame is planned for (None:
        now). Returns the frame as the display gets it (turned as the panel
        is mounted), or None while the panel is not connected.
        """
        self._follow_panel()  # a panel that has connected may change the default theme
        self._follow_night()
        config, host, status, hub, on_config = (
            self.config, self.host, self.status, self.hub, self.on_config
        )  # fmt: skip
        shown, refused, theme, renderer = self.shown, self.refused, self.theme, self.renderer
        default, reported = self.default, self.reported
        display, size, watcher, link, toasts = (
            self.display, self.size, self.watcher, self.link, self.toasts
        )  # fmt: skip
        blend, began_in, shown_frame = self.blend, self.began_in, self.shown_frame
        switches, last_frame = self.switches, self.last_frame
        previous, turn, snapshot, next_sample = (
            self.previous, self.turn, self.snapshot, self.next_sample
        )  # fmt: skip
        out = None
        self.started = started = self.clock()  # fine-grained on every system (monotonic is
        # not on Windows before Python 3.13); a streaming frame is drawn at its planned time
        now = started if now is None else now
        self.idle.clear()
        try:  # the frame's state is kept whatever happens
            # While a transition plays, a switch waits for its end, unless the
            # transition says otherwise (Transition.on_switch: follow or restart).
            if host is not None:
                self._follow_pages(config, host, snapshot)
            wanted = _theme_name(config, host, default, self.rotation, reported)
            status.sequence = _sequence_state(config, self.rotation)
            status.size = panel_size(config, reported)
            mode = host.mode if host is not None else None
            playing = blend
            policy = switch_policy(blend[0], wanted, mode, began_in) if blend else None
            may_switch = policy in (None, "follow", "restart")
            may_switch = may_switch and self._warmed(config, host, shown, refused, watcher, now)
            changed = watcher.changed() if may_switch else False
            if may_switch and (changed or (wanted != shown and wanted != refused)):
                fresh = config
                try:
                    if changed and config.path:
                        fresh = panel_config(load_config(config.path), self.panel)
                    fresh_default = self._default_for(fresh)
                    wanted = _theme_name(fresh, host, fresh_default, self.rotation, reported)
                    new_theme = load_theme(find_theme(wanted))
                except (ConfigError, ThemeError) as exc:
                    log.warning("keeping theme %r: %s", shown, exc)  # e.g. half-written file
                    refused = wanted
                    if fresh is not config and on_config is not None:
                        on_config(fresh)  # the file loaded: services follow it, theme or not
                else:
                    device = config.device
                    if not _same_device(device, fresh.device):
                        log.info("device settings changed; opening the panel again")
                        display.close()
                        display = create_display(fresh.device)
                        display.counters = status.counters
                        link = _Link(display, _brightness(fresh), status)
                        device = fresh.device
                        reported = None  # the new display has not said which panel it is
                    for name in _FOLLOWED:  # (on the open panel)
                        setattr(device, name, getattr(fresh.device, name))
                    if _brightness(fresh) != link.brightness:
                        link.set_brightness(_brightness(fresh))
                    fresh.device, config = device, fresh
                    default = status.default_theme = fresh_default
                    i18n.set_language(config.language)
                    hub.follow_config(config)  # taken at the sample that follows (next_sample)
                    renderer.close()
                    theme, renderer = new_theme, self._renderer(new_theme, config)
                    size, previous, next_sample = target_size(config, theme, wanted), None, 0.0
                    known = {config.path: config.stamp} if config.path and config.stamp else None
                    watcher = _Watcher(config.path, _theme_file(theme), known=known)
                    toasts.set_renderer(renderer)
                    if wanted != shown:
                        chosen = host.take_transition() if host is not None else None
                        # a following one uncovers the new theme, unless a mode
                        # change brought a transition of its own
                        if policy != "follow" or (host is not None and host.switches != switches):
                            ctx = RenderContext(renderer)
                            # a calm panel changes at once: a fade is a whole picture a frame
                            spec = "cut" if calm(config, display) else chosen or config.transition
                            kind = transition(spec, ctx)
                            blend = _begin(kind, last_frame, shown_frame, now, toasts) or blend
                        if host is not None:
                            switches = host.switches  # this was the switch
                            host.emit("theme-changed", theme=wanted)
                    shown, refused, status.theme = wanted, None, wanted
                    status.missing_theme = ""
                    log.info("showing theme %r", shown)
                    if changed and on_config is not None:
                        on_config(config)
            if host is not None and host.switches != switches and policy != "wait":
                switches = host.switches  # a mode change that keeps the theme, with a transition
                spec = host.take_transition()
                kind = transition("cut" if calm(config, display) else spec, RenderContext(renderer))
                blend = _begin(kind, last_frame, shown_frame, now, toasts) or blend
            if blend is not playing:
                began_in = mode
            status.mode = host.mode if host is not None else None
            if started >= next_sample or snapshot is None:
                snapshot, self.first = self.first or hub.snapshot(), None
                if host is not None:
                    snapshot.images = {**snapshot.images, **host.images()}
                    host.latest = snapshot
                    host.emit("sample", snapshot=snapshot)
                every = (config.refresh_ms or theme.refresh_ms) / 1000
                next_sample = started + every
                renderer.new_sample(now, every)
            elif hub.every_frame:  # cheap sources follow on every frame (a new dict:
                latest = hub.fresh(video=display.streaming)  # helpers may read the old one)
                before, snapshot.readings = snapshot.readings, {**snapshot.readings, **latest}
                # their pictures too (a new cover with its title), and the services' own
                pictures = hub.fresh_images(video=display.streaming)
                if host is not None:
                    pictures.update(host.images())
                if any(snapshot.images.get(k) is not v for k, v in pictures.items()):
                    merged = {**snapshot.images, **pictures}
                    snapshot.images = {k: v for k, v in merged.items() if v is not None}
                if host is not None:  # a service hears of a change at once, not at the sample
                    host.fresh(snapshot, before, latest)
            was_connected = link.connected
            if link.ensure(started):
                streaming = display.streaming
                # a slow piece (a new background) must not stall a video
                renderer.background_builds = streaming
                renderer.continuous = streaming  # graphs scroll, values glide on
                renderer.fps = _fps(config, host, display)  # screens may pace animations by it
                # nothing glides, turns or breathes on a calm panel (it would be sent every frame)
                renderer.animate = config.fps > 1 and not calm(config, display)
                if host is not None:  # (while the panel is away they wait there, the newest)
                    toasts.add(host.take_toasts())
                go_on = getattr(blend[0], "toasts", "wait") in ("over", "hide") if blend else True
                toasts.advance(now, hold=not go_on)
                renderer.toast = toasts.showing(now)  # screens see a toast the moment it begins
                # the clock ticks between readings too, at the planned frame's time
                snapshot.now = datetime.fromtimestamp(time.time() - (self.clock() - now))
                drawing = self.clock()
                frame, _ = renderer.render(snapshot, now)
                frame = fit_frame(frame, size, background_of(renderer))
                if blend is not None:
                    playing_kind = blend[0]
                    frame, blend = _blend(blend, frame, now)
                    if blend is None:  # it ended: the panel light may sweep (look.light)
                        renderer.transition_ended(playing_kind, now)
                last_frame = frame
                # decided after _blend: the frame a hiding transition ends shows the toast
                hidden = blend is not None and getattr(blend[0], "toasts", "wait") == "hide"
                frame = shown_frame = renderer.shown = toasts.draw(frame, now, hidden=hidden)
                if identify.showing():  # which panel is which: its number instead, a moment
                    number = 1 if self.panel is None else self.panel + 2
                    accent = renderer.theme.palette.get("accent") or "#2EE6D6"
                    frame = identify.picture(number, frame.size, accent)
                self.meter.drew(self.clock() - drawing)
                if host is not None:
                    host.shown = frame  # for the editor's pages: never drawn on from here on
                upside = config.device.rotate if _on_panel(display) else 0
                if upside != turn:  # turned now, or no longer: the whole frame again
                    previous, turn = None, upside
                out = turned(frame, turn)  # as the panel is mounted
                if streaming:
                    link.show(out, None, started)
                    previous = None
                elif display.slow:  # the parts that changed, each on its own
                    parts = changed_regions(previous, out)
                    if parts:
                        previous = out if link.show(out, parts, started) else None
                else:
                    region = changed_region(previous, out)
                    if region is not None:
                        previous = out if link.show(out, region, started) else None
            else:
                previous = None  # send a full frame after reconnecting
            if host is not None and link.connected != was_connected:
                host.emit("panel-connected" if link.connected else "panel-lost")
                if link.connected and display.restarted:
                    host.emit("panel-restarted")  # back after a restart, not a replug
        finally:
            self.idle.set()
            self.config, self.shown, self.refused, self.theme = config, shown, refused, theme
            self.default, self.reported = default, reported
            self.renderer, self.display, self.size, self.watcher = renderer, display, size, watcher
            self.link, self.blend, self.began_in = link, blend, began_in
            self.shown_frame, self.switches, self.last_frame = shown_frame, switches, last_frame
            self.previous = previous
            self.turn, self.snapshot, self.next_sample = turn, snapshot, next_sample
        self.meter.tick(started, self.fps, self.streaming)
        return out

    def pause(self) -> float:
        """Seconds to wait before the next frame when the display is not streaming."""
        config, host, renderer, blend, toasts = (
            self.config, self.host, self.renderer, self.blend, self.toasts
        )  # fmt: skip
        started, next_sample = self.started, self.next_sample
        # While something glides, draw at full fps; otherwise wake for the next
        # reading, but at least twice a second so a seconds clock never skips.
        interval = 1 / _fps(config, host, self.display)
        if not (renderer.moving or blend is not None or toasts.active):
            interval = max(interval, min(0.5, next_sample - started))
        return max(0.0, interval - (self.clock() - started))

    def go_dark(self) -> None:
        """Libre Panel quits ([device] dark_on_quit): a black picture, and the panel's light
        off, as turing-smart-screen-python turns these panels off; a panel with a command of
        its own for it (Turing rev. A) has its screen turned off as well. The next start lights
        it again. A slow panel (USB serial) gets no black picture: a whole one takes it up to
        half a minute."""
        if not self.link.connected:
            return
        if not self.display.slow:
            try:
                self.display.show(Image.new("RGB", self.size, "black"), None)
            except Exception as exc:  # noqa: BLE001 - the light goes off all the same
                log.debug("no black picture before going dark: %s", exc)
        try:
            self.display.set_brightness(0)
            off = self.display.screen_off()
        except Exception as exc:  # noqa: BLE001 - quitting goes on
            log.warning("the panel did not go dark: %s", exc)
            return
        log.info("the panel went dark%s: Libre Panel quits", ", its screen off" if off else "")

    def close(self) -> None:
        if self.host is not None:
            self.host.shown = None  # a paused panel shows no live picture...
            self.host.judge = None  # ...and no message
        self.status.hub = None
        self.display.close()
        self.toasts.close()
        self.warmup.close()
        self.renderer.close()
        self.hub.close()


def panel_config(config: Config, panel: int | None) -> Config:
    """The config a loop runs with: config.toml's, or for a panel beside [device]'s, the one
    of ``[[panels]]`` at ``panel`` (:meth:`Config.for_panel`)."""
    if panel is None:
        return config
    if panel >= len(config.panels) or not config.panels[panel].on:
        raise ConfigError(f"panels[{panel}]: no longer in config.toml")
    return config.for_panel(panel)


class _MorePanels:
    """The panels beside [device]'s (``[[panels]]``): each in a loop of its own (:func:`run`
    with ``panel``), in a thread of its own, on the first panel's readings (:class:`HubView`).
    Each follows config.toml as the first panel's loop does; one starts as it is added or
    switched on, ends as it is taken out or off, and all end with the first panel's loop."""

    RETRY_S = 30.0  # after a config or theme that cannot be shown
    HALT_S = 10.0  # how long an end waits for a loop to let its panel go

    def __init__(
        self, hub: Any, status: RunStatus, quitting: Callable[[], bool | str | None] | None
    ) -> None:
        self.hub, self.status, self.quitting = hub, status, quitting
        self._runs: dict[int, tuple[threading.Thread, threading.Event]] = {}
        self._seen: Config | None = None

    def follow(self, config: Config) -> None:
        """Start and end loops as ``config`` (the first panel's, as loaded) says. Cheap
        while it is the same: the first panel's loop calls it on every frame."""
        if config is self._seen:
            return
        self._seen = config
        wanted = {n for n, panel in enumerate(config.panels) if panel.on}
        for n in sorted(set(self._runs) - wanted):
            self._end([n])
        for n in sorted(wanted - set(self._runs)):
            status, stop = RunStatus(), threading.Event()
            thread = threading.Thread(
                target=self._main, args=(n, config, stop, status), name=f"panel-{n + 2}",
                daemon=True,
            )  # fmt: skip
            self._runs[n] = (thread, stop)
            self.status.more = {**self.status.more, n: status}
            thread.start()

    def _main(self, n: int, config: Config, stop: threading.Event, status: RunStatus) -> None:
        while not stop.is_set():
            try:
                run(panel_config(config, n), stop=stop, status=status, hub=HubView(self.hub),
                    panel=n, quitting=self.quitting)  # fmt: skip
            except (ConfigError, ThemeError, DeviceError) as exc:
                status.state, status.detail = "error", str(exc)
                log.error("panel %d stopped: %s", n + 2, exc)
                stop.wait(self.RETRY_S)
            except Exception as exc:  # the first panel goes on through a bug in this one's loop
                status.state, status.detail = "error", f"unexpected error: {exc}"
                log.exception("the loop of panel %d crashed; starting again", n + 2)
                stop.wait(5.0)
            if config.path is not None and not stop.is_set():
                try:
                    config = load_config(config.path)
                except ConfigError:
                    pass  # the first panel's loop says what is wrong with it

    def _end(self, numbers: list[int]) -> None:
        runs = [(n, *self._runs.pop(n)) for n in numbers if n in self._runs]
        for _, _, stop in runs:
            stop.set()  # all at once: each lets its panel go while the others do
        for n, thread, _ in runs:
            thread.join(timeout=self.HALT_S)
            if thread.is_alive():
                log.warning("the loop of panel %d did not stop in time", n + 2)
        gone = {n for n, _, _ in runs}
        self.status.more = {n: s for n, s in self.status.more.items() if n not in gone}

    def close(self) -> None:
        self._end(list(self._runs))


HANG_FILENAME = "libre-panel-hang.log"
HANG_S = 120.0  # a step of the loop this long is a hang: a whole serial frame takes half a minute


class _HangWatch:
    """Where every thread is when a step of the loop does not end within ``seconds`` (the panel
    stands still, and the tray may stand with it), written once per hang to ``path``.
    faulthandler's timer needs no interpreter, so it writes even while a call holds it; a
    step that ends in time takes it back."""

    def __init__(self, path: Path | None, seconds: float = HANG_S) -> None:
        self.path, self.seconds = path, seconds
        self._file: Any = None
        self._started = 0.0

    def arm(self) -> None:
        if self.path is None:
            return
        import faulthandler

        from libre_panel import __version__

        if self._file is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115 - until close
                started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._file.write(f"-- libre-panel {__version__}, panel loop started {started}\n")
                self._file.flush()
            except OSError as exc:
                log.debug("no file for a hang: %s", exc)
                self.path = None
                return
        self._started = time.monotonic()
        faulthandler.dump_traceback_later(self.seconds, file=self._file)

    def disarm(self) -> None:
        if self._file is None:
            return
        import faulthandler

        faulthandler.cancel_dump_traceback_later()
        took = time.monotonic() - self._started
        if took >= self.seconds:  # it went on after all: the file says where it stood
            log.warning("the panel loop stood still for %.0f s; where: %s", took, self.path)

    def close(self) -> None:
        if self._file is not None:
            import faulthandler

            faulthandler.cancel_dump_traceback_later()
            self._file.close()
            self._file = None


def run(
    config: Config,
    once: bool = False,
    stop: threading.Event | None = None,
    status: RunStatus | None = None,
    host: Any = None,
    on_config: Callable[[Config], None] | None = None,
    hang_log: Path | None = None,
    quitting: Callable[[], bool | str | None] | None = None,
    hub: Any = None,
    panel: int | None = None,
) -> None:
    """Main loop: read sensors at the theme's rate, render at ``config.fps`` so
    values glide between readings, and send only frames that changed. A
    streaming display (video mode) gets every frame at its own steady rate.

    With ``once`` a single frame is sent and any device error is raised.
    ``status`` is kept up to date for status displays. ``host`` connects the
    services (a :class:`~libre_panel.plugins.PluginHost`); ``on_config`` hears
    about every config.toml that was loaded. ``hang_log``: where a step that hangs
    writes where every thread stands (:class:`_HangWatch`). ``quitting`` says, when the loop
    ends, why: Libre Panel quits (True) or the computer goes to sleep ("sleep"); the panel
    then goes dark where ``[device] dark_on_quit`` or ``dark_on_sleep`` says so.

    The panels of ``[[panels]]`` run beside it, each in a loop of its own (:class:`_MorePanels`):
    one of them is this with ``panel``, its place there, and ``hub``, the first panel's readings
    (a :class:`HubView`); the guards and the hang watch are the first panel's loop's alone.
    """
    stop = stop or threading.Event()
    loop = MainLoop(config, once=once, status=status, host=host, on_config=on_config, hub=hub,
                    panel=panel)  # fmt: skip
    pacer = _Pacer()
    planned: float | None = None  # when the pacer planned the next streaming frame
    first = panel is None  # the first panel's loop, [device]'s
    guards = timing.Guards()  # SPUR II's, while this drives the panel
    watch = _HangWatch(hang_log if first else None)  # (faulthandler has one timer)
    more = _MorePanels(loop.hub, loop.status, quitting) if first and not once else None
    try:
        if once:
            loop.once()
            return
        if first:
            guards.start()
        while not stop.is_set():
            watch.arm()
            loop.step(planned)
            watch.disarm()
            if first:
                guards.settled()  # after the first frame
            if more is not None:
                more.follow(loop.config)
            if loop.streaming:
                planned = pacer.wait(loop.fps, stop)  # a steady clock
                continue
            planned = None
            pacer.reset()
            stop.wait(loop.pause())
    finally:
        if more is not None:
            more.close()  # (on the first panel's readings, which close with it)
        watch.close()
        guards.stop()
        why, device = quitting() if quitting is not None else None, loop.config.device
        if (why == "sleep" and device.dark_on_sleep) or (why is True and device.dark_on_quit):
            loop.go_dark()
        loop.close()

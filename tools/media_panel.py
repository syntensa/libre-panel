"""Panel clips for tools/readme_media.py: the real main loop, on a clock of its own.

A clip is the panel as it looks in video mode: ``MainLoop.step`` driven frame by frame at
50 frames a second, with the demo source moving its values, a streaming display in memory and
scripted events (a notice, a theme switch, a game that starts, a card that changes). Nothing
here waits for the wall clock, so a clip is the same on every run, whatever the machine:

- the loop's clock is a number the recipe sets, and the demo's values follow it;
- the time of day on the panel stands still (as in the pictures of tools/readme_images.py),
  or, in a clip of a clock, runs with the loop's clock from that same moment;
- the next theme is not warmed up in a thread, the pieces of a frame are not built in a helper
  thread: when those finish depends on the machine. The pictures are the same either way.

Each recipe is a :class:`PanelClip`: how long it runs, and a function that sets the stage
(themes, events, changes to the demo's values). Add one by adding it to ``CLIPS``. The
panel's own start screen (``START_CLIPS``) is not the main loop: its pictures are shown as the
panel shows them.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time as real_time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

import readme_images as images
from media_encode import FrameSink, Recorded, Settings, fade_loop
from PIL import Image, ImageChops

from libre_panel import app, i18n
from libre_panel.app import MainLoop
from libre_panel.config import (
    Config,
    DeviceConfig,
    ModeConfig,
    SensorsConfig,
    SequenceConfig,
    user_themes_dir,
)
from libre_panel.devices.base import Display
from libre_panel.plugins import PluginHost, loader
from libre_panel.plugins.host import ServiceHost, apply_options
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider, Snapshot
from libre_panel.sensors.demo import DemoProvider, demo_history, demo_report
from libre_panel.sequence import Step
from libre_panel.services.notices import Notices
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import (
    THEME_FORMAT,
    find_theme,
    load_theme,
    pair_version,
    parse_theme,
    save_theme,
)
from libre_panel.theme.modules import look_theme_parts, make_grid

START = 1000.0  # the loop's clock at the first frame of a clip (the number is arbitrary)
WARMUP_S = (
    2.0  # frames drawn before the clip begins: the panel's own start (a sweep of light) is over
)
PREFILL_S = 120.0  # how much history the graphs have when the clip begins ...
PREFILL_MAX = 300  # ... in readings at most (a graph shows fewer, and a hub keeps 600)
FADE_S = 0.5  # a clip's end is blended into its start over this long

Tweak = Callable[[dict[str, Reading], float], None]  # (readings, seconds into the clip)


class Clock:
    """The main loop's clock: the planned time of the frame being drawn."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now

    @property
    def seconds(self) -> float:
        """Seconds into the clip (negative before it begins)."""
        return self.now - START


class Eye(Display):
    """A video panel that looks at nothing: the loop's frames are taken from ``step``."""

    streaming = True

    def __init__(self, fps: int) -> None:
        super().__init__(DeviceConfig())
        self.stream_fps = fps

    def describe(self) -> str:
        return "clip recorder"

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        pass


class Speakers(SensorProvider):
    """The volume of the default output as the ``audio`` source reads it: on every frame, so
    a bar on the panel follows the volume keys at once."""

    name = "audio"
    every_frame = True

    def __init__(self) -> None:
        super().__init__()
        self.percent = 30.0
        self.muted = False

    def read(self) -> dict[str, Reading]:
        return {
            "audio.volume": Reading(
                "audio.volume", 0.0 if self.muted else self.percent, "%", "Volume"
            ),
            "audio.muted": Reading("audio.muted", 1 if self.muted else 0, "", "Muted"),
        }


class ClipDemo(DemoProvider):
    """The demo source on the clip's clock: its values move as real ones do and a recipe's
    tweaks change them. The demo's waves are shaped like a load (the shape its static pictures
    have), not like a sine.

    With a ``period`` (the length of a clip that loops) the waves have it too: the wave a period
    on is blended in over the period, smoothly, so a value is the same when the clip ends as when
    it begins, and so is every graph that has the clip's values in it.
    """

    def __init__(self, clock: Clock, tweaks: list[Tweak], period: float | None = None) -> None:
        super().__init__({})
        self.clock, self.tweaks, self.period = clock, tweaks, period

    def read(self) -> dict[str, Reading]:
        seconds = self.clock.seconds
        self.fixed_time = images.WHEN + seconds
        readings = super().read()
        for key in _waves():
            reading = readings.get(key)
            if reading is None:
                continue
            value = self._wave(key, seconds)
            readings[key] = replace(reading, value=value)
            if reading.origin in readings:  # the copy under a friendlier name
                readings[reading.origin] = replace(readings[reading.origin], value=value)
        for tweak in self.tweaks:
            tweak(readings, seconds)
        return readings

    def _wave(self, key: str, seconds: float) -> float:
        if self.period is None:
            return self._shape(key, seconds)
        x = seconds % self.period
        share = _smooth(x / self.period)
        return (1 - share) * self._shape(key, x + self.period) + share * self._shape(key, x)

    def _shape(self, key: str, seconds: float) -> float:
        self.fixed_time = images.WHEN + seconds
        return demo_history(self, key, 1)[0]


def _waves() -> list[str]:
    """The demo's readings that move (the others are constants)."""
    from libre_panel.sensors.demo import _WAVES

    return [key for key, (_label, _unit, _base, amplitude, _period) in _WAVES.items() if amplitude]


# -- changes to the demo's values ---------------------------------------------------------------


def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def bump(x: float, rise: float, hold: float, fall: float) -> float:
    """A bump ``x`` seconds after it began: up to 1 over ``rise`` seconds, 1 for ``hold``, down
    to 0 over ``fall``, and 0 before and after."""
    if x < 0:
        return 0.0
    if x < rise:
        return _smooth(x / rise)
    if x < rise + hold:
        return 1.0
    return 1.0 - _smooth((x - rise - hold) / fall)


def surge(
    changes: dict[str, float], at: float, rise: float, hold: float, fall: float,
    every: float | None = None,
) -> Tweak:  # fmt: skip
    """A burst of work: each reading in ``changes`` goes up by its amount and settles again.
    With ``every`` the burst comes again that often, before the clip as well: a clip that is as
    long as that starts where it ends, graphs included (a burst that came once would be in the
    graph at the end and not at the start)."""

    def tweak(readings: dict[str, Reading], seconds: float) -> None:
        x = seconds - at
        share = bump(x % every if every else x, rise, hold, fall)
        for key, amount in changes.items():
            reading = readings.get(key)
            if reading is None or not isinstance(reading.value, int | float):
                continue
            value = reading.value + amount * share
            value = min(100.0, value) if reading.unit == "%" else value
            readings[key] = replace(reading, value=max(0.0, value))

    return tweak


def silence(prefix: str, start: float, end: float) -> Tweak:
    """The readings that begin with ``prefix`` (what plays: ``"media."``) are gone from
    ``start`` to ``end`` seconds."""

    def tweak(readings: dict[str, Reading], seconds: float) -> None:
        if start <= seconds < end:
            for key in [k for k in readings if k.startswith(prefix)]:
                del readings[key]

    return tweak


def agents_story(steps: list[tuple[float, dict[str, dict[str, Any]]]]) -> Tweak:
    """What Claude Code and Codex do (``ai.*``): from each step's second on, what it says of
    each agent it names (``{"claude": {"state": "working", "tool": "Edit"}}``), on top of the
    steps before; ``busy`` and ``since`` go with the state, the tool only while it works. An
    agent that is ``off`` has no more than its state; one named nowhere keeps the demo's."""

    def tweak(readings: dict[str, Reading], seconds: float) -> None:
        said: dict[str, dict[str, Any]] = {}
        began: dict[str, float] = {}
        for at, agents in steps:
            if at > seconds:
                break
            for agent, values in agents.items():
                if "state" in values and values["state"] != said.get(agent, {}).get("state"):
                    began[agent] = at
                said[agent] = {**said.get(agent, {}), **values}
        for agent, values in said.items():
            state = values.get("state", "idle")
            parts = {
                "state": state,
                "busy": {"waiting": 2, "working": 1, "idle": 0, "off": -1}[state],
                "tool": values.get("tool", "") if state == "working" else "",
                "task": values.get("task"),
                "since": 60.0 + seconds - began.get(agent, seconds),
            }
            if state == "off":
                for key in [k for k in readings if k.startswith(f"ai.{agent}.")]:
                    del readings[key]
                parts = {"state": state, "busy": -1}
            for part, value in parts.items():
                key = f"ai.{agent}.{part}"
                if value is None:
                    readings.pop(key, None)
                    continue
                old = readings.get(key)
                readings[key] = replace(old, value=value) if old else Reading(key, value)

    return tweak


# -- what a clip is recorded in -----------------------------------------------------------------


class _FrozenTime:
    """``time`` as the main loop sees it: the time of day stands still."""

    def __getattr__(self, name: str) -> Any:
        return getattr(real_time, name)

    def time(self) -> float:
        return images.WHEN


class _RunningTime(_FrozenTime):
    """``time`` for a clip whose clock runs: the time of day goes on with the clip's own clock
    (from the same moment, so a clip is still the same on every run)."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def time(self) -> float:
        return images.WHEN + self.clock.seconds


class _NoWarmup:
    """A switch of theme happens in the frame it is asked for: the next theme is not built
    in a thread beforehand (when it is done depends on the machine, and so would the frame
    the switch comes in)."""

    LIMIT_S = 120.0  # the first theme's preparation is waited for
    busy = False

    def ready(self, key: Any, now: float) -> bool:
        return True

    def start(self, key: Any, build: Callable[[], Any], now: float) -> None:
        pass

    def take(self, key: Any) -> None:
        return None

    def discard(self) -> None:
        pass

    def close(self) -> None:
        pass


class _SyncRenderer(Renderer):
    """A renderer that builds every piece in the frame that needs it. On a video panel a
    changed piece is built in a helper thread while frames go on with the old one, and shows a
    frame or two later; which frame depends on the machine."""

    @property
    def background_builds(self) -> bool:
        return False

    @background_builds.setter
    def background_builds(self, value: bool) -> None:
        pass


@contextlib.contextmanager
def workshop() -> Iterator[Path]:
    """What a clip is recorded in: a settings folder of its own (none of the user's themes or
    config, no plugin installed on this machine: a mod would shadow what the clips show), time
    that stands still, and pieces built where they are drawn. Yields the folder."""
    language = i18n.language()
    with (
        tempfile.TemporaryDirectory(prefix="readme-media-home-") as home,
        contextlib.ExitStack() as stack,
    ):
        stack.enter_context(mock.patch.dict(os.environ, {"LIBRE_PANEL_HOME": home}))
        stack.enter_context(mock.patch.object(loader, "_installed", lambda group: iter(())))
        stack.enter_context(mock.patch.object(app, "time", _FrozenTime()))
        stack.enter_context(mock.patch.object(app, "_Warmup", _NoWarmup))
        stack.enter_context(mock.patch.object(app, "Renderer", _SyncRenderer))
        loader.reset_registry()
        try:
            yield Path(home)
        finally:
            i18n.set_language(language)
            loader.reset_registry()


def theme_folder(source: str) -> Path | None:
    """``source`` as a folder with a theme in it (``theme.json``), if it is one."""
    folder = Path(source)
    return folder if (folder / "theme.json").is_file() else None


def theme_data(source: str | dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    """The data of a theme, and the folder its files are in (None: it has none). ``source`` is
    theme data, one of the :data:`LAYOUTS`, a folder with a theme in it, or the id of a built-in
    or user theme."""
    if isinstance(source, dict):
        return dict(source), None
    if source in LAYOUTS:
        return LAYOUTS[source](), None
    folder = theme_folder(source) or find_theme(source)
    return json.loads((folder / "theme.json").read_text(encoding="utf-8")), folder


# -- the stage ----------------------------------------------------------------------------------


class Stage:
    """The panel of one clip: the main loop and everything a recipe may do to it.

    A recipe sets it up before the first frame (:meth:`theme`, :meth:`show`, :meth:`at`,
    :meth:`tweak`); :meth:`frames` then runs the loop and gives the pictures, one at a time.
    """

    def __init__(self, settings: Settings, zoom: int = 1, period: float | None = None) -> None:
        self.settings = settings
        self.period = period  # the clip's length, if it loops: its values are the same by then
        self.factor = settings.scale * zoom  # the panel's pixels, against its own size
        self.clock = Clock()
        self.host = PluginHost(clock=self.clock)
        self.speakers = Speakers()
        self.loop: MainLoop | None = None
        self.notices: Notices | None = None
        self.start = ""  # the theme the clip begins with
        self.modes: dict[str, str] = {}
        self.transition = "fade"
        self._events: list[tuple[float, int, Callable[[Stage], None]]] = []
        self._tweaks: list[Tweak] = []
        self._made = 0

    # -- setting up

    def theme(self, source: str | dict[str, Any], name: str | None = None, **changes: Any) -> str:
        """The id to show a theme by in this clip. ``source`` is the id of a built-in or a user
        theme, a folder with a theme in it, one of the :data:`LAYOUTS`, or theme data;
        ``changes`` replace keys of its data (``toast={...}``). It is scaled to the clip's pixels.
        A theme that comes as it is, at its own size, is shown by its own id."""
        named = isinstance(source, str) and source not in LAYOUTS and theme_folder(source) is None
        if named and not changes and self.factor == 1:
            find_theme(source)  # (it is there: or this says it is not)
            return source  # type: ignore[return-value]
        data, folder = theme_data(source)
        if isinstance(source, str) and source in LAYOUTS and not changes and self.factor == 1:
            name = name or source
        data.update(changes)
        if self.factor != 1:
            theme = parse_theme(data)
            size = (
                max(8, round(theme.width * self.factor)),
                max(8, round(theme.height * self.factor)),
            )
            data = adapt_theme(data, "custom", theme.orientation, "scale", *size)
        self._made += 1
        by_path = isinstance(source, str) and theme_folder(source) is not None
        name = name or (Path(source).name if by_path else f"clip-{self._made}")  # type: ignore[arg-type]
        if folder is not None:  # a theme with files of its own: they come along
            shutil.copytree(folder, user_themes_dir() / name, dirs_exist_ok=True)
        save_theme(name, data)
        return name

    def show(
        self,
        theme: str,
        modes: dict[str, str] | None = None,
        transition: str = "fade",
        turns: list[tuple[str, float]] | None = None,
        pages: dict[str, str] | None = None,
        leave_s: float | None = None,
    ) -> None:
        """The theme the clip begins with, the themes of the modes (``{"game": theme}``), the
        transition config.toml names for a switch, screens in turn (theme, seconds), and the
        pages the panel switches to by itself, switched on (``{"media": theme}``; music's
        page stays ``leave_s`` after a track stops)."""
        self.start, self.modes, self.transition = theme, dict(modes or {}), transition
        self.turns = list(turns or [])
        self.pages, self.leave_s = dict(pages or {}), leave_s

    def at(self, seconds: float, action: Callable[[Stage], None]) -> None:
        """Do ``action(stage)`` just before the frame at ``seconds`` is drawn."""
        self._events.append((seconds, len(self._events), action))

    def tweak(self, change: Tweak) -> None:
        """Change the demo's readings (see :func:`surge`, :func:`silence`)."""
        self._tweaks.append(change)

    # -- what a recipe does while the clip runs

    def turn_volume(self, percent: float) -> None:
        """The volume key: the volume changes now, and the notices service tells it as the
        app would (the volume's own band, in the theme's toast style)."""
        self.speakers.percent = percent
        self._tell_volume()

    def _tell_volume(self) -> None:
        assert self.notices is not None
        snapshot = Snapshot(readings=self.speakers.read())
        self.notices.on_fresh(snapshot, frozenset({"audio.volume"}))

    def value(self, key: str, default: Any = None) -> Any:
        """A reading as the last sample took it (the name the demo gives its game, say)."""
        snapshot = self.loop.snapshot if self.loop is not None else None
        found = snapshot.value(key) if snapshot is not None else None
        return default if found is None else found

    # -- running

    def _begin(self, at: float = START) -> MainLoop:
        """The panel's main loop, its first frame due at ``at``, with the history its graphs
        would have by then (read before the loop draws its first theme ahead, as the app does
        with the readings it has)."""
        config = Config(
            theme=self.start,
            language="en",
            fps=self.settings.fps,
            transition=self.transition,
            sensors=SensorsConfig(providers=["demo"]),
            modes={
                **{name: ModeConfig(theme=theme) for name, theme in self.modes.items()},
                **{name: ModeConfig(theme=theme, on=True) for name, theme in self.pages.items()},
            },
            sequence=SequenceConfig(True, [Step(theme, s) for theme, s in self.turns]),
        )
        hub = SensorHub([self.speakers, ClipDemo(self.clock, self._tweaks, self.period)])
        every = (config.refresh_ms or load_theme(find_theme(self.start)).refresh_ms) / 1000
        for k in range(
            min(PREFILL_MAX, round(PREFILL_S / every)), 0, -1
        ):  # what graphs drew so far
            self.clock.now = at - k * every
            hub.snapshot()
        self.clock.now = at
        loop = MainLoop(
            config, host=self.host, hub=hub, display=Eye(self.settings.fps), clock=self.clock
        )
        if self.leave_s is not None:
            loop.pages.leave_s = self.leave_s
        options = apply_options(Notices.options, {"music": False, "restarted": False}, "notices")
        self.notices = Notices(
            ServiceHost(self.host, "notices"), options, clock=self.clock,
            wall=lambda: datetime.fromtimestamp(images.WHEN),
        )  # fmt: skip
        self._tell_volume()  # the first value is no news: later ones are
        return loop

    def frames(self, seconds: float, warmup: float = WARMUP_S) -> Iterator[Image.Image]:
        """The panel's frames for ``seconds``, after ``warmup`` seconds that are not given.

        A frame is only good until the next is asked for (the renderer draws on it again):
        take what you need before going on."""
        fps = self.settings.fps
        self.loop = self._begin(START - round(warmup * fps) / fps)
        events = sorted(self._events)
        done = 0
        try:
            for n in range(-round(warmup * fps), round(seconds * fps)):
                self.clock.now = START + n / fps
                while n >= 0 and done < len(events) and events[done][0] <= n / fps + 1e-9:
                    events[done][2](self)
                    done += 1
                frame = self.loop.step(self.clock.now)
                assert frame is not None  # the display is always there
                if n >= 0:
                    yield frame
        finally:
            self.loop.close()
            self.host.close()


# -- the picture of a clip ----------------------------------------------------------------------


class Bezel:
    """The device frame of tools/readme_images.py (bezel, soft shadow, glass, on its dark
    background) around a screen of one size, made once: a frame is pasted in. The pictures
    use ``device`` and ``background`` of that tool; what the glass shows of them is read off
    two probes (a black and a white screen) rather than copied."""

    def __init__(self, size: tuple[int, int]) -> None:
        sw, sh = size
        border = max(4, min(24, round(0.0375 * sh)))
        across, down = round(1.2 * border), round(1.8 * border)  # room round the bezel
        margin = images.shadow_margin(border)  # what device() leaves round it for the shadow
        dark = images.device(Image.new("RGB", size, (0, 0, 0)), border)
        white = images.device(Image.new("RGB", size, (255, 255, 255)), border)
        glass = (margin + border, margin + border, margin + border + sw, margin + border + sh)
        black_glass, white_glass = (d.crop(glass).getchannel("R") for d in (dark, white))
        # inside the glass a white screen shows white and a black one only the reflection
        self.mask = ImageChops.subtract(white_glass, black_glass).point(lambda v: 255 if v else 0)
        self.sheen = Image.composite(black_glass, Image.new("L", size, 0), self.mask)
        self.white = Image.new("RGB", size, (255, 255, 255))
        total = (sw + 2 * border + 2 * across, sh + 2 * border + 2 * down)
        canvas = images.background(*total, glow=(0.5, 1.0)).convert("RGBA")
        canvas.alpha_composite(dark, (across - margin, down - margin))
        self.base = canvas.convert("RGB")
        self.origin = (across + border, down + border)
        self.size = total

    def frame(self, screen: Image.Image) -> Image.Image:
        lit = Image.composite(self.white, screen.convert("RGB"), self.sheen)
        out = self.base.copy()
        out.paste(lit, self.origin, self.mask)
        return out


class Presenter:
    """From a frame of the panel to a picture of the clip: cropped (a close-up), then in the
    device frame, unless the clip is bare."""

    def __init__(self, size: tuple[int, int], crop: tuple[int, int, int, int] | None, bare: bool):
        self.box = (
            None if crop is None else (crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3])
        )
        shown = size if crop is None else (crop[2], crop[3])
        self.bezel = None if bare else Bezel(shown)

    def __call__(self, frame: Image.Image) -> Image.Image:
        if self.box is not None:
            frame = frame.crop(self.box)
        return frame if self.bezel is None else self.bezel.frame(frame)


# -- layouts: Libre Panel's own look, made of modules ---------------------------------------------


def module(kind: str, col: int, row: int, cols: int = 1, rows: int = 1, **options: Any) -> dict:
    return {"module": kind, "col": col, "row": row, "cols": cols, "rows": rows, **options}


def modules_theme(
    look: str,
    modules: list[dict],
    dust: bool = False,
    refresh_ms: int = 500,
    model: str = "turing-9.2-usb",
) -> dict[str, Any]:
    """A theme made of modules for a panel (the 9.2" bar unless ``model`` says another), in one
    of the looks of the editor (the pictures of tools/readme_images.py are made the same way)."""
    palette, style = look_theme_parts(look)
    data: dict[str, Any] = {
        "format": THEME_FORMAT,
        "name": f"Clip layout ({look})",
        "display": {"model": model, "orientation": "landscape"},
        "palette": palette,
        "style": style,
        "background": {"color": palette["bg"]},
        "refresh_ms": refresh_ms,
        "animation": {"smoothing_ms": 500},
        "widgets": [{"type": "module", "id": f"m{n}", **m} for n, m in enumerate(modules)],
    }
    if dust:
        data["look"] = {"dust": {"strength": 1.6}}
    return data


# Themes a recipe may name besides the built-in ones: screens that Libre Panel's themes do not
# have, made of modules in Libre Panel's own look (tools/media_app.py adds its own).
# ``--theme clip=THEME`` puts any theme in a clip's place.
LAYOUTS: dict[str, Callable[[], dict[str, Any]]] = {}


# -- recipes ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Focus:
    """A close-up of what a theme has of some kinds of widget: the box around all the widgets
    of these ``kinds`` (a widget's type, or the module of a module), with this much room on each
    side. So a close-up finds its place in whatever theme it is made of."""

    kinds: tuple[str, ...]
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def box(self, data: dict[str, Any]) -> tuple[int, int, int, int] | None:
        """(x, y, width, height) in the panel's own pixels; None if the theme has none of them."""
        theme = parse_theme(data)
        grid = make_grid(theme.width, theme.height, theme.grid, theme.model)
        found = []
        for widget in data.get("widgets", []):
            kind = widget.get("module") if widget.get("type") == "module" else widget.get("type")
            if kind not in self.kinds:
                continue
            if widget.get("type") == "module":
                found.append(
                    grid.box(
                        widget["col"], widget["row"], widget.get("cols", 1), widget.get("rows", 1)
                    )
                )
            elif all(key in widget for key in ("x", "y", "w", "h")):
                found.append([widget["x"], widget["y"], widget["w"], widget["h"]])
        if not found:
            return None
        x0 = max(0, min(x for x, _y, _w, _h in found) - self.left)
        y0 = max(0, min(y for _x, y, _w, _h in found) - self.top)
        x1 = min(theme.width, max(x + w for x, _y, w, _h in found) + self.right)
        y1 = min(theme.height, max(y + h for _x, y, _w, h in found) + self.bottom)
        return x0, y0, x1 - x0, y1 - y0


@dataclass(frozen=True)
class PanelClip:
    """One clip: what it shows and for how long, and the function that sets the stage.

    ``themes`` are the themes it shows, by default: ids of built-in themes, or of the
    :data:`LAYOUTS`; ``record`` takes others in their place, and the recipe asks the stage for
    what it needs of them (a card slot, a graph) without naming one."""

    about: str
    themes: tuple[str, ...]
    seconds: float
    setup: Callable[[Stage, tuple[str, ...]], None]
    poster: float = 0.0  # the second the poster is taken at
    zoom: int = 1  # drawn at this many times the panel's size: a small panel or a close-up is sharp
    # of the panel's own pixels: (x, y, width, height), or a :class:`Focus` that finds the box in
    # the theme; a theme that has none of them is shown whole
    crop: tuple[int, int, int, int] | Focus | None = None
    framed: bool = True  # in the device frame (a close-up is a detail: it is not)
    loop: str = "fade"  # "fade": the end is blended into the start; "cut": it is seamless as it is
    # Whether the demo's values come round with the clip, so that its end leads on into its start
    # as it is. Only for a screen without graphs: one that shows much more time than the clip
    # would show the same stretch again and again. Else the end gives way to the start.
    periodic: bool = False
    warmup: float = WARMUP_S
    # Whether the time of day runs with the clip (the seconds roll, a second hand glides); else
    # it stands still at the moment of the README's pictures.
    clock_runs: bool = False

    @property
    def kind(self) -> str:
        return "panel"


def record(
    name: str,
    clip: PanelClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """Run ``clip`` (with ``themes`` instead of its own) and keep its frames in ``folder``."""
    themes = themes or clip.themes
    if len(themes) != len(clip.themes):
        raise ValueError(f"{name} shows {len(clip.themes)} theme(s), not {len(themes)}")
    with workshop(), contextlib.ExitStack() as stack:
        box = clip.crop
        if isinstance(box, Focus):
            box = box.box(theme_data(themes[0])[0])
        whole = box is None and clip.crop is not None  # a close-up of what the theme lacks
        stage = Stage(settings, 1 if whole else clip.zoom, clip.seconds if clip.periodic else None)
        if clip.clock_runs:
            stack.enter_context(mock.patch.object(app, "time", _RunningTime(stage.clock)))
        clip.setup(stage, themes)
        fps = settings.fps
        overlap = round(FADE_S * fps) if clip.loop == "fade" else 0
        total = clip.seconds + overlap / fps
        sink = FrameSink(folder, fps)
        present: Presenter | None = None
        crop = tuple(round(v * stage.factor) for v in box) if box else None
        bare = settings.bare or not clip.framed
        for frame in stage.frames(total, clip.warmup):
            if present is None:
                present = Presenter(frame.size, crop, bare)  # type: ignore[arg-type]
            sink.add(present(frame))
        frames = sink.frames()
    if overlap:
        frames = fade_loop(frames, overlap)
    return Recorded(frames, min(frames.count - 1, round(clip.poster * fps)))


def work_burst(stage: Stage, at: float = 2.0) -> None:
    """A burst of work: the loads, temperatures, power and clocks go up and settle, and the fans
    run faster (what a theme shows of them is its own business). In a clip that comes round, it
    comes round with it."""
    stage.tweak(surge({
        "cpu.load": 50, "cpu.temp": 15, "cpu.power": 42, "cpu.freq": 380,
        "gpu.load": 30, "gpu.temp": 9, "gpu.power": 90, "gpu.freq": 150,
        "fan.lpc.fan_1": 160, "fan.lpc.fan_2": 420, "fan.lpc.fan_3": 140,
        "fan.lpc.fan_4": 200, "gpu.fan": 520,
    }, at=at, rise=1.1, hold=0.8, fall=2.4, every=stage.period))  # fmt: skip


# Video mode: what is drawn on every frame. The history graphs scroll, the rings and numbers glide
# to each new reading, the fans turn faster as they speed up, and the dust drifts (if the theme
# has any).
def video_mode(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    work_burst(stage)


# The volume key, twice as many presses up as down: the band rolls down from its glowing edge,
# the number counts and the bar glides.
def volume_band(stage: Stage, themes: tuple[str, ...]) -> None:
    toast = {"style": "band", "anchor": "bottom", "seconds": 2.0}
    stage.show(stage.theme(themes[0], toast=toast))
    start = stage.speakers.percent
    up = [(0.8 + 0.1 * k, start + 2 * (k + 1)) for k in range(12)]
    down = [(2.4 + 0.1 * k, start + 24 - 2 * (k + 1)) for k in range(12)]
    for at, percent in up + down:
        stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# The volume over a screen in each of its looks (Look → Notices → Volume): Bar, Levels, Ring and
# Large one after the other, each turned up and down a little and gone before the next comes.
VOLUME_LOOKS = ("bar", "levels", "ring", "big")
LOOK_EVERY_S = 2.5  # one look's turn: up, down, its hold of 1.2 s and its fade


def volume_looks(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0], toast={"seconds": 1.2}))
    start = stage.speakers.percent
    for n, look in enumerate(VOLUME_LOOKS):
        begin = 0.5 + LOOK_EVERY_S * n
        stage.at(begin - 0.1, lambda s, look=look: setattr(s.notices, "look", look))
        up = [(begin + 0.07 * k, start + 3 * (k + 1)) for k in range(6)]
        down = [(begin + 0.6 + 0.07 * k, start + 18 - 3 * (k + 1)) for k in range(4)]
        for at, percent in up + down:
            stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# Encore: what plays, the cover's colours behind it, the time rolling on, and the volume turned
# up and then down: its levels rise and fall (the screen shows the volume itself, so no notice).
def encore_screen(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    start = stage.speakers.percent
    up = [(1.0 + 0.12 * k, start + 4 * (k + 1)) for k in range(10)]
    down = [(3.6 + 0.12 * k, start + 40 - 4 * (k + 1)) for k in range(10)]
    for at, percent in up + down:
        stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# A screen as it runs: its symbols move, its clock rolls, its second hand glides.
def screen(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))


# Screens in turn: each shows for its time, then the next comes with the tube, by itself.
def screens_in_turn(stage: Stage, themes: tuple[str, ...]) -> None:
    names = [stage.theme(theme) for theme in themes]
    stage.show(names[0], transition="tube", turns=[(name, 2.6) for name in names])


# Libre Panel's own screens on the bar, one after the other as screens in turn go (a beam of
# light across): the README's tour of its themes. Each a moment, and round again. It begins in
# the first one's time (which runs through the warm-up too), so that the last beam, back to the
# first, is over before the clip ends, and its end is blended into its start.
TOUR_S = 2.6


def themes_tour(stage: Stage, themes: tuple[str, ...]) -> None:
    names = [stage.theme(theme) for theme in themes]
    turns = [(name, TOUR_S) for name in names]
    turns[0] = (names[0], WARMUP_S + TOUR_S * 0.5 - 0.2)
    stage.show(names[0], transition="tube", turns=turns)


@dataclass(frozen=True)
class StartClip:
    """The panel's own screens (render/bootscreen.py): the boot logo of a panel (by its model's
    id), then its standby clip, round and round, in the device frame; then the clips of the
    other ``looks``, ``look_s`` each, one fading into the next."""

    about: str
    themes: tuple[str, ...]  # the panel's model
    seconds: float
    logo_s: float = 1.6
    poster: float = 3.0
    looks: tuple[str, ...] = ()
    look_s: float = 2.2
    fade_s: float = 0.3

    @property
    def kind(self) -> str:
        return "panel"


def record_start(
    name: str,
    clip: StartClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """The boot logo for ``logo_s``, then the standby clip at its own rate, as the panel shows
    them; then the other looks' clips."""
    from libre_panel.devices.models import get_model
    from libre_panel.render import bootscreen

    model = get_model((themes or clip.themes)[0])
    scale = min(1.0, settings.scale)  # (drawn at the clip's size: quicker than made smaller)
    logo = bootscreen.boot_logo(model, scale=scale)
    size = logo.size
    present = Presenter(size, None, settings.bare)
    fps = settings.fps
    sink = FrameSink(folder, fps)
    shown = present(logo)
    sink.add(shown, round(clip.logo_s * fps))
    own = clip.seconds - clip.logo_s - len(clip.looks) * clip.look_s
    parts = [(bootscreen.DEFAULT, own), *((look, clip.look_s) for look in clip.looks)]
    fade = round(clip.fade_s * fps)
    before: list[Image.Image] = []  # the last pictures of the look before, to fade from
    for look, seconds in parts:
        loop = bootscreen.standby_frames(model, design=look, scale=scale)
        count = round(seconds * fps)
        for n in range(count):
            picture = loop[int(n / fps * bootscreen.CLIP_FPS) % len(loop)]
            if n < fade and before:
                picture = Image.blend(before[n], picture, (n + 1) / (fade + 1))
            sink.add(present(picture))
        ends = [loop[int((count + k) / fps * bootscreen.CLIP_FPS) % len(loop)] for k in range(fade)]
        before = ends
    frames = sink.frames()
    return Recorded(frames, min(frames.count - 1, round(clip.poster * fps)))


# Music's page: nothing plays, then a track starts and the panel opens Encore by itself (a line
# of light), the volume is turned up and down there, and when the track stops it goes back.
def music_page(stage: Stage, themes: tuple[str, ...]) -> None:
    home, music = (stage.theme(theme) for theme in themes)
    stage.show(home, transition="tube", pages={"media": music}, leave_s=0.6)
    stage.tweak(silence("media.", -1000.0, 1.0))  # nothing plays: the theme of every day
    stage.tweak(silence("media.", 7.0, 1000.0))  # the track stops
    stage.tweak(silence("ai.", -1000.0, 1000.0))  # (the demo's AI tools would take the card)
    start = stage.speakers.percent
    steps = [(3.2 + 0.1 * k, start + 4 * (k + 1)) for k in range(8)]
    steps += [(4.8 + 0.1 * k, start + 32 - 4 * (k + 1)) for k in range(5)]
    for at, percent in steps:
        stage.at(at, lambda s, percent=percent: s.turn_volume(percent))


# After a game: the memory service puts its round report on the panel (a line of light).
def round_report(stage: Stage, themes: tuple[str, ...]) -> None:
    home, report = (stage.theme(theme) for theme in themes)
    stage.show(home, transition="tube")
    finished = demo_report(images.WHEN)  # the round that just ended, as the service tells it
    stage.tweak(lambda readings, seconds: readings.update(finished))
    stage.at(1.0, lambda s: s.host.request_theme(report, by="memory", transition="tube",
                                                 priority=10))  # fmt: skip


# One screen to the next, as an autopilot or a plugin switches them: a line ignites at
# mid-height and opens, the new screen already live between its edges; back again after.
def theme_tube(stage: Stage, themes: tuple[str, ...]) -> None:
    first, second = (stage.theme(theme) for theme in themes)
    stage.show(first, transition="tube")
    stage.at(1.0, lambda s: s.host.request_theme(second, by="clip", transition="tube"))
    stage.at(3.9, lambda s: s.host.request_theme(None, by="clip", transition="tube"))


# A game starts and ends: the entrance closes the screen to a line, opens a band with the game's
# name, and opens onto the screen of the game; at the end the other way round.
def game_entrance(stage: Stage, themes: tuple[str, ...]) -> None:
    home, game = (stage.theme(theme) for theme in themes)
    stage.show(home, modes={"game": game})

    def play(stage: Stage, kind: str) -> None:
        # the name the demo gives its game; a logo too, if a source publishes one
        params = {"title": stage.value("game.app", "Starfall"), "kind": kind, "logo": "game.logo"}
        stage.host.set_mode("game" if kind == "start" else None, by="game",
                            transition=("entrance", params))  # fmt: skip

    stage.at(0.8, lambda s: play(s, "start"))
    stage.at(4.6, lambda s: play(s, "end"))


# A card slot: what plays, until nothing does; then the weather, with a line of light that
# ignites at the top edge between the two (and back again). What plays is taken at a reading,
# once a second: the card changes at the second after it stops.
def card_ignition(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    stage.tweak(silence("media.", 2.0, 5.0))
    stage.tweak(silence("ai.", -1000.0, 1000.0))  # (the demo's AI tools would come first)


# Lumen's cards in turn, each a moment (2.3 s: in a clip the time of a card is cut short, a
# card's own is five seconds at least), and from one to the next another way each time: a fade,
# the new card pushing the old one out to the left, upwards, and a beam of light that wipes
# across. After four changes the first card is on show again (a card changes at the reading
# after its time is up), and the clip ends a moment later: it loops.
CARD_TURN_S = 2.3
CARD_WAYS = ("fade", "slide", "rise", "wipe")


def card_turns(stage: Stage, themes: tuple[str, ...]) -> None:
    data, _ = theme_data(themes[0])
    widgets = [
        {**w, "items": "playback\nforecast\nanalog\ndate", "order": "turns"}
        if w.get("type") == "module" and w.get("module") in ("cards",) else w
        for w in data["widgets"]
    ]  # fmt: skip
    stage.show(stage.theme(themes[0], widgets=widgets))

    def slot(s: Stage) -> Any:
        assert s.loop is not None
        found = list(s.loop.renderer._slots.values())
        assert found, "the theme has no card slot"
        return found[0]

    def begin(s: Stage) -> None:  # (the cards' time, from now on)
        box = slot(s)
        box.turns = [CARD_TURN_S] * len(box.turns)
        box.turn_at = s.clock.now
        box.transition = CARD_WAYS[0]

    def way(name: str) -> Callable[[Stage], None]:
        return lambda s: setattr(slot(s), "transition", name)

    stage.at(0.0, begin)
    for n, name in enumerate(CARD_WAYS[1:], start=1):
        stage.at(n * CARD_TURN_S + 0.3, way(name))


# AI activity on Lumen, without the AI screen: the weather, until Claude Code sets to work; then
# the AI card rolls down out of the line of light by itself, Claude Code alone on it (Codex does
# not run): its session, the tools at work, and at last waiting for you. Nothing plays.
def ai_card(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    stage.tweak(silence("media.", -60.0, 60.0))
    stage.tweak(agents_story([
        (-60.0, {"claude": {"state": "idle", "task": "Cards on the panel"},
                 "codex": {"state": "off"}}),
        (1.0, {"claude": {"state": "working", "tool": "Read"}}),
        (3.2, {"claude": {"tool": "Edit"}}),
        (4.8, {"claude": {"tool": "Bash"}}),
        (6.4, {"claude": {"state": "waiting"}}),
    ]))  # fmt: skip


# The AI screen on its own: Claude Code and Codex side by side, each sign moving as it works or
# waits for you, the tools changing, Claude Code's session under them.
def agents_screen(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    stage.tweak(agents_story([
        (-60.0, {"claude": {"state": "working", "tool": "Read", "task": "Cards on the panel"},
                 "codex": {"state": "working", "tool": "shell"}}),
        (1.4, {"claude": {"tool": "Edit"}}),
        (2.8, {"codex": {"state": "waiting"}}),
        (4.2, {"claude": {"tool": "Bash"}}),
        (5.8, {"claude": {"state": "waiting"},
               "codex": {"state": "working", "tool": "apply_patch"}}),
    ]))  # fmt: skip


# The small panel: a burst of work fills the rings, the history scrolls, and what plays in the
# corner gives way to the weather and comes back (a card on a small panel is small).
def small_panel(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    work_burst(stage, at=0.6)
    stage.tweak(silence("media.", 4.0, 7.0))


# A ring that fills and a fan that runs up, close: drawn at twice the panel's size, cropped.
def close_up(stage: Stage, themes: tuple[str, ...]) -> None:
    stage.show(stage.theme(themes[0]))
    work_burst(stage, at=1.0)


# The clips show Libre Panel's own screens: Lumen on the bars, Libre Default on the small panel,
# Studio for the notice. Their graphs show a long stretch of time, so none of the clips loops on
# data that comes round (see ``periodic``): the end gives way to the start.
CLIPS: dict[str, PanelClip] = {
    "video-mode": PanelClip(
        "video mode: lit curves scrolling, rings and numbers gliding, fans running up",
        ("lumen",), 8.0, video_mode, poster=4.0,
    ),
    "card-ignition": PanelClip(
        "the card slot changes from playback to the weather and back, with its line of light",
        ("lumen",), 6.6, card_ignition, poster=3.4,
    ),
    "card-turns": PanelClip(
        "the card slot's cards in turn, each a moment, and four ways from one to the next",
        ("lumen",), 4 * CARD_TURN_S + 1.2, card_turns, poster=CARD_TURN_S * 1.5,
    ),
    "ai-card": PanelClip(
        "AI activity on Lumen by itself: Claude Code sets to work and its card rolls in",
        ("lumen",), 8.4, ai_card, poster=4.0,
    ),
    "agents": PanelClip(
        "the AI screen: Claude Code and Codex at work and waiting for you, side by side",
        ("agents",), 7.2, agents_screen, poster=3.6,
    ),
    "small-panel": PanelClip(
        "the small panel: lit rings filling, a history that scrolls, a card that changes",
        ("libre-default",), 8.0, small_panel, poster=1.6, zoom=2,
    ),
    "volume-band": PanelClip(
        "the volume key: a notice in the band style, its number counting and its bar gliding",
        ("studio",), 6.8, volume_band, poster=2.2,
    ),
    "volume-looks": PanelClip(
        "the volume over the screen in its four looks: Bar, Levels, Ring and Large",
        ("lumen",), 10.6, volume_looks, poster=5.6,
    ),
    "theme-tube": PanelClip(
        "one screen to the next with the tube, and back again",
        ("lumen", "studio"), 6.6, theme_tube, poster=1.5,
    ),
    "game-entrance": PanelClip(
        "a game starts and ends with the entrance: the screen closes to a line, a band opens",
        ("lumen", "arena"), 7.8, game_entrance, poster=4.2,
    ),
    "close-up-rings": PanelClip(
        "a close-up of the rings: lit arcs that fill, numbers that glide, at twice the size",
        ("lumen",), 6.0, close_up, poster=3.0, zoom=2, framed=False,
        crop=Focus(("instrument", "ring", "gauge"), 14, 14, 14, 32),
    ),
    "close-up-curves": PanelClip(
        "a close-up of the curves and the fans: a lit history that scrolls, fans that run up",
        ("lumen",), 6.0, close_up, poster=3.0, zoom=2, framed=False,
        crop=Focus(("graph", "fan"), 20, 44, 20, 22),
    ),
    "encore": PanelClip(
        "Encore: what plays, the cover's colours behind it, the time rolling, the volume rising",
        ("encore",), 7.0, encore_screen, poster=2.4, periodic=True, clock_runs=True,
    ),
    "nimbus": PanelClip(
        "Nimbus: the weather's symbols moving, the next days, the moon, a small clock rolling",
        ("nimbus",), 6.0, screen, poster=2.0, periodic=True, clock_runs=True,
    ),
    "almanac": PanelClip(
        "Almanac to Almanac Analog and back: rolling digits, then a second hand that glides",
        ("almanac", "almanac-analog"), 6.6, theme_tube, poster=2.6, clock_runs=True,
    ),
    "music-page": PanelClip(
        "music's page: a track starts and Encore opens by itself; the volume; back when it stops",
        ("lumen", "encore"), 9.0, music_page, poster=4.2, clock_runs=True,
    ),
    "round-report": PanelClip(
        "after a game: the round report opens, in Libre Panel's look",
        ("lumen", "round-report"), 6.0, round_report, poster=4.5, loop="cut",
    ),
    "screens-in-turn": PanelClip(
        "screens in turn: Almanac, Nimbus and Encore one after another, each for its time",
        ("almanac", "nimbus", "encore"), 7.8, screens_in_turn, poster=1.2, loop="cut",
        clock_runs=True,
    ),
    "themes-tour": PanelClip(
        "Libre Panel's own screens on the bar, one after another through the tube",
        ("lumen", "studio", "encore", "nimbus", "almanac", "arena"), 6 * TOUR_S, themes_tour,
        poster=TOUR_S * 0.25, clock_runs=True,
    ),
    # SPUR II's classic screen, where Libre Panel started: the only clip of its own look
    "where-it-started": PanelClip(
        "SPUR II's classic screen in video mode, where it started: curves, fans, dust",
        ("spur-classic",), 8.0, video_mode, poster=4.0,
    ),
}  # fmt: skip

# -- several panels at once ---------------------------------------------------------------------


class _ClipMonotonic(_FrozenTime):
    """``time`` for the readings: how old they are is told on the clip's clock, so a panel beside
    the first shares the first panel's readings at the same frames on every run."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def monotonic(self) -> float:
        return self.clock.now


GAP_MM = 30  # between two panels of a clip, as more-panels.png has them


@dataclass(frozen=True)
class PanelsClip:
    """Several panels at once, where they stand: the first panel's loop and a loop for each panel
    beside it, on the first panel's readings and in its beat, as the app runs them, in the
    picture *Picture of all panels* saves.

    ``themes`` are the first panel's theme, then each other panel's; ``alarm`` the theme each
    panel beside the first shows while a sensor is at its critical limit ("": its own)."""

    about: str
    themes: tuple[str, ...]
    seconds: float
    setup: Callable[[Stage, tuple[str, ...]], None]
    # where each panel stands, by column and row: one panel's size and a gap of 30 mm apart
    places: tuple[tuple[int, int], ...]
    model: str = "turing-9.2-usb"
    alarm: tuple[str, ...] = ()
    poster: float = 0.0
    loop: str = "fade"
    warmup: float = WARMUP_S

    @property
    def kind(self) -> str:
        return "panels"


def alarm_at(stage: Stage, start: float, end: float) -> None:
    """A sensor at its critical limit from ``start`` to ``end`` seconds: the processor's
    temperature over 90 °C, and the notices service's ``alarm`` (as the app's would say it)."""
    stage.tweak(surge({"cpu.temp": 34, "cpu.load": 40}, at=start - 0.6, rise=0.6,
                      hold=end - start, fall=1.0))  # fmt: skip

    def say(on: int) -> Callable[[Stage], None]:
        return lambda s: s.host.publish(Reading("alarm", on, "", "Alarm"), owner="notices")

    stage.at(start, say(1))
    stage.at(end, say(0))


# Two bars, one above the other: Lumen (its pair version), and Lumen Companion beside it. A burst
# of work goes up on both at once, the same fans turning in step; then the processor gets too hot:
# both edges pulse red, and the second shows High Contrast until it has cooled.
def more_panels(stage: Stage, themes: tuple[str, ...]) -> None:
    # as the app does while a companion runs beside it: the first panel's pair version
    stage.show(stage.theme(pair_version(themes[0]) or themes[0]))
    work_burst(stage, at=0.8)
    alarm_at(stage, 3.6, 6.8)


def record_panels(
    name: str,
    clip: PanelsClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """Run ``clip``'s panels in step, frame by frame, and keep their pictures in ``folder``."""
    from libre_panel import arrangement
    from libre_panel.devices.models import find_model
    from libre_panel.sensors import base

    themes = themes or clip.themes
    if len(themes) != len(clip.themes):
        raise ValueError(f"{name} shows {len(clip.themes)} theme(s), not {len(themes)}")
    w, h = arrangement.millimetres(find_model(clip.model))
    with workshop(), contextlib.ExitStack() as stack:
        stage = Stage(settings)
        stack.enter_context(mock.patch.object(base, "time", _ClipMonotonic(stage.clock)))
        clip.setup(stage, themes)
        first = stage._begin(START - round(clip.warmup * settings.fps) / settings.fps)
        stage.loop = first
        beside = []
        for n, theme in enumerate(themes[1:]):
            alarm = clip.alarm[n] if n < len(clip.alarm) else ""
            modes = {"alarm": ModeConfig(theme=stage.theme(alarm), on=True)} if alarm else {}
            config = Config(theme=stage.theme(theme), language="en", fps=settings.fps,
                            transition="fade", sensors=SensorsConfig(providers=["demo"]),
                            modes=modes)  # fmt: skip
            beside.append(MainLoop(
                config, hub=base.HubView(first.hub), display=Eye(settings.fps),
                clock=stage.clock, panel=n, mode_of=lambda: stage.host.mode, beats=first.beats,
            ))  # fmt: skip
        fps = settings.fps
        overlap = round(FADE_S * fps) if clip.loop == "fade" else 0
        sink = FrameSink(folder, fps)
        events = sorted(stage._events)
        done = 0
        try:
            for n in range(-round(clip.warmup * fps), round((clip.seconds + overlap / fps) * fps)):
                stage.clock.now = START + n / fps
                while n >= 0 and done < len(events) and events[done][0] <= n / fps + 1e-9:
                    events[done][2](stage)
                    done += 1
                frames = [loop.step(stage.clock.now) for loop in (first, *beside)]
                if n < 0:
                    continue
                shown = [((c * (w + GAP_MM), r * (h + GAP_MM), w, h), frame.copy(), False, k + 1)
                         for k, ((c, r), frame) in enumerate(zip(clip.places, frames,
                                                                 strict=True))]  # fmt: skip
                picture = arrangement.photo(shown)
                assert picture is not None
                sink.add(picture)
        finally:
            for loop in (first, *beside):
                loop.close()
            stage.host.close()
        recorded = sink.frames()
    if overlap:
        recorded = fade_loop(recorded, overlap)
    return Recorded(recorded, min(recorded.count - 1, round(clip.poster * fps)))


PANELS_CLIPS: dict[str, PanelsClip] = {
    "more-panels": PanelsClip(
        "two panels at once: the same beat on both, then an alarm that turns both edges red",
        ("lumen", "lumen-companion"), 9.0, more_panels, places=((0, 0), (0, 1)),
        alarm=("contrast",), poster=3.0,
    ),
}  # fmt: skip


# The panel's own screens: its boot logo, then its standby clip, as the panel shows them.
START_CLIPS: dict[str, StartClip] = {
    "start-screen": StartClip(
        "the panel's own screens: the boot logo, then the standby clip's light running out, then"
        " the clips of the other looks",
        ("turing-9.2-usb",), 5.0 + 6 * 2.2, poster=2.0,
        looks=("aurora", "pulse", "circuit", "night", "levels", "dark"),
    ),
}  # fmt: skip

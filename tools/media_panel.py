"""Panel clips for tools/readme_media.py: the real main loop, on a clock of its own.

A clip is the panel as it looks in video mode: ``MainLoop.step`` driven frame by frame at
50 frames a second, with the demo source moving its values, a streaming display in memory and
scripted events (a notice, a theme switch, a game that starts, a card that changes). Nothing
here waits for the wall clock, so a clip is the same on every run, whatever the machine:

- the loop's clock is a number the recipe sets, and the demo's values follow it;
- the time of day on the panel stands still (as in the pictures of tools/readme_images.py);
- the next theme is not warmed up in a thread, the pieces of a frame are not built in a helper
  thread: when those finish depends on the machine. The pictures are the same either way.

Each recipe is a :class:`PanelClip`: how long it runs, and a function that sets the stage
(themes, events, changes to the demo's values). Add one by adding it to ``CLIPS``.
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
from libre_panel.config import Config, DeviceConfig, ModeConfig, SensorsConfig, user_themes_dir
from libre_panel.devices.base import Display
from libre_panel.plugins import PluginHost, loader
from libre_panel.plugins.host import ServiceHost, apply_options
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider, Snapshot
from libre_panel.sensors.demo import DemoProvider, demo_history
from libre_panel.services.notices import Notices
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import THEME_FORMAT, find_theme, parse_theme, save_theme
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


# -- what a clip is recorded in -----------------------------------------------------------------


class _FrozenTime:
    """``time`` as the main loop sees it: the time of day stands still."""

    def __getattr__(self, name: str) -> Any:
        return getattr(real_time, name)

    def time(self) -> float:
        return images.WHEN


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
        self, theme: str, modes: dict[str, str] | None = None, transition: str = "fade"
    ) -> None:
        """The theme the clip begins with, the themes of the modes (``{"game": theme}``) and
        the transition config.toml names for a switch."""
        self.start, self.modes, self.transition = theme, dict(modes or {}), transition

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

    def _begin(self) -> MainLoop:
        config = Config(
            theme=self.start,
            language="en",
            fps=self.settings.fps,
            transition=self.transition,
            sensors=SensorsConfig(providers=["demo"]),
            modes={name: ModeConfig(theme=theme) for name, theme in self.modes.items()},
        )
        hub = SensorHub([self.speakers, ClipDemo(self.clock, self._tweaks, self.period)])
        loop = MainLoop(
            config, host=self.host, hub=hub, display=Eye(self.settings.fps), clock=self.clock
        )
        every = (config.refresh_ms or loop.theme.refresh_ms) / 1000
        for k in range(
            min(PREFILL_MAX, round(PREFILL_S / every)), 0, -1
        ):  # what graphs drew so far
            self.clock.now = START - k * every
            hub.snapshot()
        self.clock.now = START
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
        self.loop = self._begin()
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


def layout_game() -> dict[str, Any]:
    """A screen for a game: the frame rate large with its course, the CPU and the GPU, the
    cores and the temperatures."""
    return modules_theme(
        "arctic",
        [
            module("game", 0, 0, 4, 2),
            module("instrument", 4, 0, 1, 2, source="cpu"),
            module("instrument", 5, 0, 1, 2, source="gpu"),
            module("cores", 6, 0, 2), module("temps", 6, 1, 2),
        ],
    )  # fmt: skip


# Themes a recipe may name besides the built-in ones: screens that Libre Panel's themes do not
# have (a game's), made of modules in Libre Panel's own look. ``--theme clip=THEME`` puts any
# theme in a clip's place.
LAYOUTS: dict[str, Callable[[], dict[str, Any]]] = {
    "layout-game": layout_game,
}


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
    with workshop():
        box = clip.crop
        if isinstance(box, Focus):
            box = box.box(theme_data(themes[0])[0])
        whole = box is None and clip.crop is not None  # a close-up of what the theme lacks
        stage = Stage(settings, 1 if whole else clip.zoom, clip.seconds if clip.periodic else None)
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
    "small-panel": PanelClip(
        "the small panel: lit rings filling, a history that scrolls, a card that changes",
        ("libre-default",), 8.0, small_panel, poster=1.6, zoom=2,
    ),
    "volume-band": PanelClip(
        "the volume key: a notice in the band style, its number counting and its bar gliding",
        ("studio",), 6.8, volume_band, poster=2.2,
    ),
    "theme-tube": PanelClip(
        "one screen to the next with the tube, and back again",
        ("lumen", "studio"), 6.6, theme_tube, poster=1.5,
    ),
    "game-entrance": PanelClip(
        "a game starts and ends with the entrance: the screen closes to a line, a band opens",
        ("lumen", "layout-game"), 7.8, game_entrance, poster=4.2,
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
    # SPUR II's classic screen, where Libre Panel started: the only clip of its own look
    "where-it-started": PanelClip(
        "SPUR II's classic screen in video mode, where it started: curves, fans, dust",
        ("spur-classic",), 8.0, video_mode, poster=4.0,
    ),
}  # fmt: skip

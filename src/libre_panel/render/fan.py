"""A fan that turns with its speed: a lit rotor in a ring that shows the load.

The rotor has a look (render/fan_looks.py): SPUR II's, made once in Blender without colour
(nine blades, as Noctua's second generation), or one drawn from its shape (the first
NF-A12x25 and NF-A14, Libre Panel's own). Each is lit from the top left, in 48 positions
over one blade's pitch (after that it looks the same again, so the turn loops without a
jump). Grey layers per position (shape, core, gloss, and where the second colour goes) are
coloured here, in the theme's colour or the look's own, and the coloured result is kept by
colour, size and position. The ring around it shows how fast the fan
runs: a faint groove all round and a glowing arc, from twelve o'clock
clockwise, as long as its share of ``max_rpm``.

What turns on the panel is not the real speed (1200 RPM are 20 turns a second,
and at 50 frames a second that is flicker) but a calm one that rises with it,
and follows it with inertia like a real fan: it runs up, runs out, and stands
still at 0 RPM. A fan without a reading stands still and shows dimmed.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from libre_panel.render import fan_looks

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "light3d" / "fan"

BLADES = 9  # the Blender rotor's; each look has its own (fan_looks.Look.blades)
POSITIONS = 48  # rotor images over one blade's pitch
FAN_SIZE = 48  # the sizes below are for a fan this big, and scale with it
LAYER_SIZE = 38  # the rotor's diameter
RING_CENTRE = 22  # the ring's middle line, from the fan's middle
RING_STROKE = 5
RING_HALO = 5.0  # how soft the glow around the arc is
MARGIN = 9  # room the glow takes around the fan
GROOVE = 0.22  # the empty ring's brightness
GLOW_WHITE = 0.75  # the arc's core goes this far towards white
GLOW_SHARE = 1.45  # how strongly the glow stands beside the core
RING_STEPS = 120  # lengths of the arc
SHADES = 8  # steps of dimming and of blur that are kept apart

# how the grey layers make a colour: the colour times (ambient + diffuse * shape),
# darker at the blade's edges by the core layer, the gloss added white
AMBIENT, DIFFUSE, CORE, GLOSS = 0.35, 0.65, 0.30, 0.55
CORE_FLOOR = 0.596  # the core layer's darkest grey

# what the fan shows
MIN_TURNS = 0.22  # turns a second once it turns at all
MAX_TURNS = 1.1  # at ``max_rpm`` and above
STANDING_RPM = 30  # below this it stands (a semi-passive graphics card's fan)
INERTIA_S = 0.45  # the time constant it follows the speed with
MAX_STEP = 0.3  # blade pitches per frame at most: it never seems to turn backwards
FRICTION = 0.35  # turns a second lost every second while it runs out
BLUR = 0.8  # how far the blades blur into a disc at full speed
LIGHT = {"running": 1.0, "stopped": 0.5, "missing": 0.25}  # how bright it shows

_lock = threading.Lock()  # the coloured images and the kept layers, held for a moment
_load = threading.Lock()  # reading the layers, held for as long as that takes
_images: OrderedDict[tuple[Any, ...], Image.Image] = OrderedDict()
# (look, size) -> (positions, 5, k, k): shape, core, gloss, alpha, part; the oldest first
_layers: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
_frames: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
IMAGES = 768  # coloured images kept
# The layers of a size are a few megabytes (17 for a fan of 150 px), and a fan that is resized in
# the editor asks for one size after the other: the sizes kept are as many as this holds.
LAYER_BYTES = 48 << 20


@dataclass
class Choices:
    """What the user chose for the fans (``[hardware]`` in config.toml): a look and colours
    for all, and for some fans by their sensor. A widget's own look and colours come first;
    "auto" takes these."""

    look: str = ""
    paint: str = ""
    looks: dict[str, str] = field(default_factory=dict)
    paints: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, config: Any) -> Choices:
        hw = config.hardware
        return cls(hw.fan_look, hw.fan_paint, dict(hw.fan_looks), dict(hw.fan_paints))

    def for_widget(self, widget: dict[str, Any]) -> tuple[str, str]:
        """``(look, paint)`` of a fan widget."""
        sensor = widget.get("sensor", "")
        look = widget.get("look", "auto")
        if look == "auto" or look not in fan_looks.LOOKS:
            look = self.looks.get(sensor) or self.look or fan_looks.DEFAULT
        paint = widget.get("paint", "auto")
        if paint not in ("theme", "own"):
            paint = self.paints.get(sensor) or self.paint or "theme"
        return look, paint


def turns(rpm: float | None, max_rpm: float) -> float:
    """The turns a second the fan shows for ``rpm`` (0: it stands)."""
    if rpm is None or rpm < STANDING_RPM or max_rpm <= 0:
        return 0.0
    share = min(1.0, rpm / (max_rpm * 0.75)) ** 0.7  # full speed a little before the ring is full
    return MIN_TURNS + (MAX_TURNS - MIN_TURNS) * share


def _start(key: str) -> float:
    """Where the blades of a fan stand at first, in blade pitches (0 to 1): every fan in its
    own position, or they all turn in step."""
    return (sum(map(ord, key)) % 97) / 97.0


class Beat:
    """Where a fan's blades stand, for every panel that shows it at the same pace: two panels
    side by side show the same fan in the same position. Each panel's frame adds up the
    angle to its planned time; a frame a moment behind another panel's takes it back by the
    speed. One of :class:`Beats`."""

    def __init__(self, angle: float) -> None:
        self.angle = angle  # in blade pitches, at ``at``
        self.at: float | None = None
        self._lock = threading.Lock()

    def begin(self, now: float) -> float:
        """Where the fan stands for a rotor's first frame (the beat begins then, if it has not
        begun on another panel)."""
        with self._lock:
            if self.at is None:
                self.at = now
            return self.angle

    def turn(self, now: float, rate: float) -> float:
        """The angle at ``now`` (the main loop's clock) of a fan that turns ``rate`` blade
        pitches a second."""
        with self._lock:
            if self.at is None or self.at - now > 0.2:  # the first, or on another clock (a test)
                self.at = now
            elif now > self.at:  # after a pause, as far as a frame of 0.2 s at most (as a rotor)
                self.angle = (self.angle + min(MAX_STEP, rate * min(0.2, now - self.at))) % 1.0
                self.at = now
            else:  # another panel drew a frame for a later time
                return (self.angle - min(MAX_STEP, rate * (self.at - now))) % 1.0
            return self.angle


class Beats:
    """The beats of the fans, for the renderers of all panels (the main loops share one)."""

    def __init__(self) -> None:
        self._made: dict[tuple[str, int, int], Beat] = {}
        self._lock = threading.Lock()

    def export(self) -> list[tuple[tuple[str, int, int], float, float]]:
        """Where each beat stands, and when (the main loops' clock: ``time.monotonic``), for a
        panel in a process of its own (panel_process.py)."""
        with self._lock:
            made = list(self._made.items())
        return [(key, beat.angle, beat.at) for key, beat in made if beat.at is not None]

    def adopt(self, beats: list[tuple[tuple[str, int, int], float, float]]) -> None:
        """Stand where another process's beats stand (:meth:`export`): the same fan in step
        on every panel, whichever process draws it."""
        for key, angle, at in beats:
            beat = self.of(*key)
            with beat._lock:
                beat.angle, beat.at = angle, at

    def of(self, sensor: str, fps: int, blades: int) -> Beat:
        """The turn of the fan ``sensor`` on every panel that draws ``fps`` frames a second in
        a look of ``blades`` blades (on panels of other rates each turns as suits its rate)."""
        key = (sensor, fps, blades)
        with self._lock:
            found = self._made.get(key)
            if found is None:
                found = self._made[key] = Beat(_start(sensor))
            return found


class Rotor:
    """One fan's turning: where its blades are, how fast it shows, how full its ring
    and how bright it is. Worked out from the planned frame times, never from the
    clock, so the turn is the same at 25, 30 and 50 frames a second.

    The angle is added up from the speed times the time between frames, not
    taken from the time: a speed that moves a little would otherwise throw the
    rotor to a far position. With a :class:`Beat` it is the beat's, the same on
    every panel that shows the fan.
    """

    def __init__(self, key: str, blades: int = BLADES) -> None:
        self.angle = _start(key)  # in blade pitches, 0 to 1
        self.blades = blades  # the look's: a pitch is a turn over this many
        self.beat: Beat | None = None
        self.speed = 0.0  # turns a second, shown
        self.ring = 0.0  # share of the ring that glows
        self.light = 1.0
        self.at: float | None = None

    @staticmethod
    def _targets(rpm: float | None, max_rpm: float) -> tuple[float, float, float]:
        speed = turns(rpm, max_rpm)
        ring = 0.0 if rpm is None or max_rpm <= 0 else min(1.0, max(0.0, rpm / max_rpm))
        state = "missing" if rpm is None else "running" if speed > 0 else "stopped"
        return speed, ring, LIGHT[state]

    def advance(self, rpm: float | None, max_rpm: float, now: float) -> None:
        """The fan at the frame planned for ``now``, with ``rpm`` (None: no reading)."""
        speed, ring, light = self._targets(rpm, max_rpm)
        if self.at is None:  # the first frame: as it is
            self.speed, self.ring, self.light, self.at = speed, ring, light, now
            if self.beat is not None:  # where the fan stands on the other panels
                self.angle = self.beat.begin(now)
            return
        dt = max(0.0, min(0.2, now - self.at))
        self.at = now
        follow = 1.0 - math.exp(-dt / INERTIA_S)
        self.speed += (speed - self.speed) * follow
        self.ring += (ring - self.ring) * follow
        self.light += (light - self.light) * follow
        if speed == 0.0:
            # the exponential alone would creep on for seconds, hardly seen; a fan stops
            self.speed = max(0.0, self.speed - FRICTION * dt)
            if self.speed < 0.02:
                self.speed = 0.0
        if self.beat is not None:
            self.angle = self.beat.turn(now, self.speed * self.blades)
        else:
            self.angle = (self.angle + min(MAX_STEP, self.speed * self.blades * dt)) % 1.0

    def settle(self, rpm: float | None, max_rpm: float) -> None:
        """No animation (a preview, a picture): show the speed's look, standing."""
        self.speed, self.ring, self.light = self._targets(rpm, max_rpm)

    @property
    def phase(self) -> int:
        return int(self.angle * POSITIONS) % POSITIONS

    @property
    def blur(self) -> int:
        """How far the blades blur, in steps of 1/``SHADES`` (0 while it turns slowly)."""
        return round(SHADES * (self.speed / MAX_TURNS) ** 2)

    @property
    def dim(self) -> int:
        """How much it is dimmed, 0 (not at all) to ``SHADES``."""
        return round((1.0 - self.light) * SHADES)

    @property
    def turning(self) -> bool:
        return self.speed > 0.0


# -- the layers ----------------------------------------------------------------------


def _plane(name: str) -> np.ndarray:
    """One layer file as two planes: its grey (the mean of the colour channels) and its alpha."""
    with Image.open(ASSETS / name) as image:
        a = np.asarray(image.convert("RGBA"), np.float32) / 255
    return np.stack([a[..., :3].mean(-1), a[..., 3]])


def _reduced(planes: list[np.ndarray], size: int) -> np.ndarray:
    """``(5, size, size)``: shape, core and gloss times alpha, then alpha, each averaged
    down from the layers' own size (a box filter: edges do not darken), and the part where a
    second colour goes (none: the Blender layers are of one colour)."""
    (shape, alpha), (core, _), (gloss, _) = planes
    core = np.clip((core - CORE_FLOOR) / (1 - CORE_FLOOR), 0, 1)
    part = np.zeros_like(alpha)
    stack = np.stack([shape * alpha, core * alpha, gloss * alpha, alpha, part]).astype(np.float32)
    if stack.shape[-1] == size:
        return stack
    return np.stack(
        [
            np.asarray(Image.fromarray(layer, "F").resize((size, size), Image.Resampling.BOX))
            for layer in stack
        ]
    )


def _kept_layers(cache: OrderedDict[Any, np.ndarray], size: Any) -> np.ndarray | None:
    """The layers of ``size`` (a look and a size) if they are kept (they are then the newest)."""
    with _lock:
        layers = cache.get(size)
        if layers is not None:
            cache.move_to_end(size)
        return layers


def _keep_layers(cache: OrderedDict[Any, np.ndarray], size: Any, layers: np.ndarray) -> None:
    """Keep ``layers`` as the newest; the oldest go while more than ``LAYER_BYTES`` are kept."""
    with _lock:
        cache[size] = layers
        cache.move_to_end(size)
        while len(cache) > 1 and sum(kept.nbytes for kept in cache.values()) > LAYER_BYTES:
            cache.popitem(last=False)


def rotor_layers(size: int, look: str = fan_looks.DEFAULT) -> np.ndarray:
    """All positions of the rotor at ``size`` px: ``(POSITIONS, 5, size, size)``. The
    first call per size reads 144 small pictures (about 150 ms) or draws the look (as
    long): do it off the render thread (``Renderer.warm``) or at the very start."""
    found = fan_looks.look(look)
    key = (found.name, size)
    layers = _kept_layers(_layers, key)
    if layers is not None:
        return layers
    with _load:
        layers = _kept_layers(_layers, key)
        if layers is None:
            if found.drawn:
                angles = fan_looks.angles(found, POSITIONS)
                layers = np.stack([fan_looks.rotor(found, size, angle) for angle in angles])
            else:
                kinds = ("shape", "core", "gloss")
                layers = np.stack(
                    [
                        _reduced([_plane(f"rotor_{kind}_{p:02d}.png") for kind in kinds], size)
                        for p in range(POSITIONS)
                    ]
                )
            _keep_layers(_layers, key, layers)
    return layers


Colours = tuple[tuple[int, int, int], tuple[int, int, int]]  # the main one, the second


def colours(look: str, paint: str, color: tuple[int, int, int], frame: bool = False) -> Colours:
    """The colours a fan's rotor (or its square ``frame``) is painted in: the look's own
    (``paint`` "own": Noctua's brown and beige), else the theme's ``color`` with a lighter
    one of it for the hub, the sticker or the frame's pads."""
    found = fan_looks.look(look)
    if paint == "own":
        if frame:
            return found.frame_color, found.pad_color
        return found.blade_color, found.hub_color
    lighter = tuple(round(v + (255 - v) * 0.45) for v in color)
    return color, lighter  # type: ignore[return-value]


def _coloured(
    layers: np.ndarray, color: tuple[int, int, int], second: tuple[int, int, int] | None = None
) -> np.ndarray:
    """RGBA (uint8, ``(..., size, size, 4)``) of the grey layers ``(..., 5, size, size)``
    in ``color``, and ``second`` where their part says so."""
    c = np.asarray(color, np.float32) / 255
    alpha = np.clip(layers[..., 3, :, :], 0, 1)
    safe = np.maximum(layers[..., 3, :, :], 1e-6)
    shade, core, gloss = (np.clip(layers[..., i, :, :] / safe, 0, 1)[..., None] for i in range(3))
    if second is not None and second != color and layers.shape[-3] > 4:
        part = np.clip(layers[..., 4, :, :] / safe, 0, 1)[..., None]
        c = c * (1 - part) + (np.asarray(second, np.float32) / 255) * part
    rgb = c * (AMBIENT + DIFFUSE * shade) * ((1 - CORE) + CORE * core)
    rgb = rgb / np.maximum(1.0, rgb.max(-1, keepdims=True))  # keep the hue: pastels do not wash out
    rgb = np.clip(rgb + GLOSS * gloss, 0, 1)
    out = np.concatenate([rgb * 255 + 0.5, alpha[..., None] * 255 + 0.5], -1)
    return out.clip(0, 255).astype(np.uint8)


def _kept(key: tuple[Any, ...], make: Callable[[], Image.Image]) -> Image.Image:
    with _lock:
        image = _images.get(key)
        if image is not None:
            _images.move_to_end(key)
            return image
    image = make()
    with _lock:
        _images[key] = image
        while len(_images) > IMAGES:
            _images.popitem(last=False)
    return image


def rotor_image(
    color: tuple[int, int, int], size: int, phase: int, look: str = fan_looks.DEFAULT,
    second: tuple[int, int, int] | None = None,
) -> Image.Image:  # fmt: skip
    """The rotor at ``phase`` (0 to ``POSITIONS`` - 1), ``size`` px square, RGBA."""
    key = ("rotor", look, color, second, size, phase % POSITIONS)
    return _kept(
        key,
        lambda: Image.fromarray(
            _coloured(rotor_layers(size, look)[phase % POSITIONS], color, second), "RGBA"
        ),
    )


def disc_image(
    color: tuple[int, int, int], size: int, look: str = fan_looks.DEFAULT,
    second: tuple[int, int, int] | None = None,
) -> Image.Image:  # fmt: skip
    """The rotor blurred into a disc: the mean over all positions (each coloured first:
    averaging the layers gives a flatter shading than the turning rotor shows)."""

    def make() -> Image.Image:
        rgba = _coloured(rotor_layers(size, look), color, second).astype(np.float32)
        alpha = rgba[..., 3:] / 255
        mean_alpha = alpha.mean(0)
        rgb = (rgba[..., :3] * alpha).mean(0) / np.maximum(mean_alpha, 1e-6)
        out = np.concatenate([rgb, mean_alpha * 255], -1)
        return Image.fromarray(out.round().clip(0, 255).astype(np.uint8), "RGBA")

    return _kept(("disc", look, color, second, size), make)


# -- the ring ------------------------------------------------------------------------


def _glow(mask: Image.Image, color: tuple[int, int, int], halo: float) -> Image.Image:
    """A glowing RGBA image of a coverage mask: a core that burns towards white, in a halo."""
    m = np.asarray(mask, np.float32) / 255
    soft = np.asarray(mask.filter(ImageFilter.GaussianBlur(halo)), np.float32) / 255
    c = np.asarray(color, np.float32)
    core = m**0.7
    rgb = c + (255.0 - c) * (GLOW_WHITE * core)[..., None]
    alpha = np.clip(core + GLOW_SHARE * np.clip(soft - m, 0, 1), 0, 1)
    out = np.zeros(m.shape + (4,), np.uint8)
    out[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _ring_mask(canvas: int, centre: float, stroke: float, share: float) -> Image.Image:
    """Coverage (L) of the arc from twelve o'clock, ``share`` of the way round, drawn
    three times larger and reduced."""
    u = 3
    g = canvas * u
    mask = Image.new("L", (g, g), 0)
    if share > 0:
        draw = ImageDraw.Draw(mask)
        r = (centre + stroke / 2) * u  # Pillow draws a wide arc inwards from its box
        box = [g / 2 - r, g / 2 - r, g / 2 + r, g / 2 + r]
        width = max(1, round(stroke * u))
        if share >= 0.995:
            draw.ellipse(box, outline=255, width=width)
        else:
            draw.arc(box, -90, -90 + 360 * share, fill=255, width=width)
    return mask.reduce(u)


def ring_image(color: tuple[int, int, int], size: int, steps: int, canvas: int) -> Image.Image:
    """The faint groove all round with the glowing arc ``steps`` of ``RING_STEPS`` long."""

    def make() -> Image.Image:
        scale = size / FAN_SIZE
        centre, stroke = RING_CENTRE * scale, max(1.5, RING_STROKE * scale)
        groove = np.asarray(_ring_mask(canvas, centre, stroke, 1.0), np.float32) / 255
        out = np.zeros((canvas, canvas, 4), np.uint8)
        out[..., :3] = color
        out[..., 3] = np.clip(groove * GROOVE * 255 + 0.5, 0, 255).astype(np.uint8)
        image = Image.fromarray(out, "RGBA")
        if steps > 0:
            arc = _glow(
                _ring_mask(canvas, centre, stroke, steps / RING_STEPS), color, RING_HALO * scale
            )
            image.alpha_composite(arc)
        return image

    return _kept(("ring", color, size, steps, canvas), make)


def frame_layers(size: int, look: str = fan_looks.DEFAULT, paint: str = "theme") -> np.ndarray:
    """The square frame's layers at ``size`` px: ``(5, size, size)``, like a rotor's: the
    Blender frame, or the look's own (Noctua's with its pads; always in its own colours)."""
    found = fan_looks.look(look)
    drawn = found.drawn or paint == "own"
    key = (found.name if drawn else "", size)
    layers = _kept_layers(_frames, key)
    if layers is None:
        with _load:
            if drawn:
                layers = fan_looks.frame(found, size)
            else:
                planes = [_plane(f"frame_{kind}.png") for kind in ("shape", "core", "gloss")]
                layers = _reduced(planes, size)
            _keep_layers(_frames, key, layers)
    return layers


def frame_image(
    color: tuple[int, int, int], size: int, steps: int, look: str = fan_looks.DEFAULT,
    paint: str = "theme",
) -> Image.Image:  # fmt: skip
    """The lit square frame of a fan, brighter the faster it runs (``steps`` of ``SHADES``)."""

    def make() -> Image.Image:
        bright = (
            0.58 + 0.42 * steps / SHADES
        )  # the idle frame is dark, a fast one as bright as the blades
        layers = frame_layers(size, look, paint).copy()
        layers[2] *= bright  # gloss follows the brightness
        main, second = colours(look, paint, color, frame=True)
        scaled = tuple(round(v * bright) for v in main)
        lit = tuple(round(v * bright) for v in second)
        return Image.fromarray(_coloured(layers, scaled, lit), "RGBA")  # type: ignore[arg-type]

    return _kept(("frame", look, paint, color, size, steps), make)


# -- the fan -------------------------------------------------------------------------


def rotor_size(size: int) -> int:
    """The rotor's diameter in a fan of ``size`` px."""
    return max(6, round(size * LAYER_SIZE / FAN_SIZE))


def preload(
    color: tuple[int, int, int], w: int, h: int, look: str = fan_looks.DEFAULT,
    paint: str = "theme",
) -> None:  # fmt: skip
    """Everything a fan of this colour, size and look will ask for but its ring: the
    layers, all rotor positions and the blur disc. For a thread that is not drawing a frame."""
    for job in preload_jobs(color, w, h, look, paint):
        job()


def preload_jobs(
    color: tuple[int, int, int], w: int, h: int, look: str = fan_looks.DEFAULT,
    paint: str = "theme",
) -> list[Callable[[], Any]]:  # fmt: skip
    """:func:`preload` in steps: a rotor position each, then the blur disc."""
    k = rotor_size(geometry(w, h)[0])
    main, second = colours(look, paint, color)
    jobs: list[Callable[[], Any]] = [
        lambda phase=phase: rotor_image(main, k, phase, look, second) for phase in range(POSITIONS)
    ]
    return [*jobs, lambda: disc_image(main, k, look, second)]


def geometry(w: int, h: int) -> tuple[int, int, int]:
    """``(size, margin, canvas)``: the fan's diameter, the room the glow takes, and the
    square image that holds both."""
    size = max(8, min(w, h))
    margin = max(2, round(size * MARGIN / FAN_SIZE))
    return size, margin, size + 2 * margin


def fan_image(
    color: tuple[int, int, int], w: int, h: int, rotor: Rotor, frame: str = "ring",
    look: str = fan_looks.DEFAULT, paint: str = "theme",
) -> Image.Image:  # fmt: skip
    """The fan as RGBA, ``geometry(w, h)[2]`` px square: its frame (``ring``,
    ``square`` or ``none``) and the rotor of its ``look`` at its position, in the theme's
    ``color`` or the look's own (``paint``), blurred by its speed, dimmed by its light. The
    ring shows the load in the theme's colour. Kept by what is on it, so a fan that has been
    round once costs a lookup."""
    size, _, canvas = geometry(w, h)
    key = (
        "fan",
        color,
        size,
        rotor.phase,
        rotor.blur,
        rotor.dim,
        round(rotor.ring * RING_STEPS),
        frame,
        look,
        paint,
    )

    def make() -> Image.Image:
        k = rotor_size(size)
        image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        if frame == "ring":
            image.alpha_composite(ring_image(color, size, round(rotor.ring * RING_STEPS), canvas))
        elif frame == "square":
            at = (canvas - size) // 2
            steps = round(rotor.ring * SHADES)
            image.alpha_composite(frame_image(color, size, steps, look, paint), (at, at))
        main, second = colours(look, paint, color)
        blades = rotor_image(main, k, rotor.phase, look, second)
        if rotor.blur:
            disc = disc_image(main, k, look, second)
            blades = Image.blend(blades, disc, BLUR * rotor.blur / SHADES)
        if rotor.dim:
            blades = blades.copy()
            alpha = blades.getchannel("A")
            blades.putalpha(alpha.point([round(v * (1 - rotor.dim / SHADES)) for v in range(256)]))
        at = (canvas - k) // 2
        image.alpha_composite(blades, (at, at))
        return image

    return _kept(key, make)

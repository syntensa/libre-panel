"""The looks a fan widget's rotor and square frame can have: drawn here from their shape.

``noctua-g2`` is SPUR II's rotor from Blender (render/fan.py reads its layers): the nine
blades of Noctua's second generation (NF-A12x25 G2, NF-A14x25 G2), swept back at the hub
and bent forward further out. The others are drawn from a few numbers, at the size they
are shown, with the light of the Blender rotor (top left in front):

- ``noctua-a12x25``: the first NF-A12x25, nine broad blades swept forward all the way
  out, with narrow gaps and grooves near their tips (Noctua's flow acceleration channels);
- ``noctua-a14``: the first 140 mm of that line, the NF-A14, seven narrower blades with
  wider gaps;
- ``libre``: Libre Panel's own fan, six round-tipped blades with a lit ridge each, as the
  panel's facets, around a hub with a ring of light.

Each look also has colours of its own (Noctua's brown impeller in a beige frame with brown
pads; Libre Panel's white blades with a cyan hub) for a fan that does not take the theme's.
The drawings are Libre Panel's, made from the blades' shape only; Noctua and the model names
are their owner's trademarks and name the fans the looks are like.

The layers have the format of the Blender rotor's (``fan._reduced``): shape, core and gloss
times alpha, alpha, and a fifth, ``part`` times alpha: 1 where the look's second colour goes
(the hub, a frame's pads), 0 elsewhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

RGB = tuple[int, int, int]

SUPERSAMPLE = 3  # each pixel is worked out from this many squared, for smooth edges
# the light, as the Blender rotor's: from the top left, in front (screen y points down)
LIGHT = np.array([-0.45, -0.55, 1.0]) / np.linalg.norm([-0.45, -0.55, 1.0])
HALF = (LIGHT + np.array([0.0, 0.0, 1.0])) / np.linalg.norm(LIGHT + np.array([0.0, 0.0, 1.0]))


@dataclass(frozen=True)
class Look:
    """A fan's look: what its blades are like, and its own colours."""

    name: str
    label: str
    blades: int
    drawn: bool = True  # False: the Blender rotor's layers (render/fan.py)
    hub: float = 0.34  # the hub's radius, of the rotor's
    sweep: float = 0.0  # degrees the blade's middle turns clockwise from hub to tip
    bend: float = 1.4  # how the sweep grows outwards (1: evenly; more: mostly out at the tip)
    fill_hub: float = 0.75  # how much of a blade's pitch the blade takes at the hub ...
    fill_tip: float = 0.85  # ... and at its tip
    round_tip: bool = False  # a blade that ends in a round tip, short of the ring
    ridge: bool = False  # a lit ridge along each blade (facets), instead of a twist
    grooves: bool = False  # grooves near the tips (flow acceleration channels)
    sticker: float = 0.0  # a flat disc on the hub, of the hub's radius (0: a dome)
    hub_ring: bool = False  # the hub's rim in the second colour
    blade_color: RGB = (230, 236, 244)  # its own colours: the blades, ...
    hub_color: RGB = (34, 211, 238)  # ... the hub (or the sticker on it) ...
    frame_color: RGB = (64, 72, 86)  # ... the square frame ...
    pad_color: RGB = (34, 211, 238)  # ... and its corners
    frame_style: str = "plain"  # "noctua": pads in the corners; "plain": a frame with a lit ring


NOCTUA_BROWN = (104, 70, 54)
NOCTUA_BEIGE = (212, 192, 164)

LOOKS: dict[str, Look] = {
    look.name: look
    for look in (
        Look(
            "noctua-g2", "Noctua NF-A12x25 G2 / NF-A14x25 G2", blades=9, drawn=False,
            blade_color=NOCTUA_BROWN, hub_color=NOCTUA_BROWN, frame_color=NOCTUA_BEIGE,
            pad_color=NOCTUA_BROWN, frame_style="noctua",
        ),
        Look(
            "noctua-a12x25", "Noctua NF-A12x25 (G1)", blades=9, hub=0.35, sweep=48.0,
            bend=1.25, fill_hub=0.80, fill_tip=0.88, grooves=True, sticker=0.78,
            blade_color=NOCTUA_BROWN, hub_color=NOCTUA_BEIGE, frame_color=NOCTUA_BEIGE,
            pad_color=NOCTUA_BROWN, frame_style="noctua",
        ),
        Look(
            "noctua-a14", "Noctua NF-A14 (G1, 140 mm)", blades=7, hub=0.33, sweep=34.0,
            bend=1.35, fill_hub=0.60, fill_tip=0.74, grooves=True, sticker=0.78,
            blade_color=NOCTUA_BROWN, hub_color=NOCTUA_BEIGE, frame_color=NOCTUA_BEIGE,
            pad_color=NOCTUA_BROWN, frame_style="noctua",
        ),
        Look(
            "libre", "Libre Panel", blades=6, hub=0.30, sweep=26.0, bend=1.1, fill_hub=0.42,
            fill_tip=0.62, round_tip=True, ridge=True, hub_ring=True,
        ),
    )
}  # fmt: skip
DEFAULT = "noctua-g2"


def look(name: str) -> Look:
    """The look of that name, else the default."""
    return LOOKS.get(name) or LOOKS[DEFAULT]


def _smooth(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _grid(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel centres from -1 to 1 over ``n`` pixels: x to the right, y down."""
    axis = (np.arange(n, dtype=np.float32) + 0.5) / n * 2 - 1
    return np.meshgrid(axis, axis)


def _lit(height: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Diffuse shade (a flat face 0.9) and gloss of a height field (``scale``: how steep a
    unit of height per pixel stands)."""
    gy, gx = np.gradient(height * scale)
    norm = np.sqrt(gx * gx + gy * gy + 1.0)
    nx, ny, nz = -gx / norm, -gy / norm, 1.0 / norm
    diffuse = np.clip((nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / LIGHT[2] * 0.9, 0, 1)
    spec = np.clip(nx * HALF[0] + ny * HALF[1] + nz * HALF[2], 0, 1) ** 60
    return diffuse, spec


def _reduce(planes: list[np.ndarray], alpha: np.ndarray, size: int) -> np.ndarray:
    """``(len(planes) + 1, size, size)``: each plane times alpha, then alpha, averaged over
    the supersampled pixels (as ``fan._reduced``: edges do not darken)."""
    s = alpha.shape[0] // size
    stack = np.stack([*(p * alpha for p in planes[:3]), alpha, *(p * alpha for p in planes[3:])])
    return stack.reshape(len(stack), size, s, size, s).mean(axis=(2, 4)).astype(np.float32)


def rotor(look_: Look, size: int, angle: float) -> np.ndarray:
    """The rotor turned ``angle`` degrees clockwise, ``size`` px square: ``(5, size, size)``
    (shape, core, gloss, alpha, part; all but alpha times alpha)."""
    n = size * SUPERSAMPLE
    x, y = _grid(n)
    r = np.sqrt(x * x + y * y)
    theta = np.degrees(np.arctan2(y, x)) - angle  # clockwise on the screen
    px = 2.0 / n  # a pixel, in radii
    hub = look_.hub
    pitch = 360.0 / look_.blades

    # the blades: where each pixel stands across the blade nearest to it
    out = np.clip((r - hub) / (1 - hub), 0, 1)  # 0 at the hub, 1 at the tip
    middle = look_.sweep * out**look_.bend
    across = (theta - middle + pitch / 2) % pitch - pitch / 2  # degrees from the blade's middle
    half = pitch / 2 * (look_.fill_hub + (look_.fill_tip - look_.fill_hub) * out)
    edge = (half - np.abs(across)) * np.radians(1) * r  # in radii, to the blade's side
    reach = 1.0 - r  # to the ring
    if look_.round_tip:  # a round end: as far from the tip's centre as the blade is wide
        tip_r = np.radians(half) * 0.98
        reach = np.minimum(
            reach + 0.0,
            (0.97 - tip_r) - r + np.sqrt(np.maximum(0.0, tip_r**2 - (np.radians(across) * r) ** 2)),
        )
    inner = np.minimum(edge, reach)
    blade = inner > 0
    s = np.clip(across / np.maximum(half, 1e-6), -1, 1)  # -1 trailing edge, 1 leading edge
    bevel = 3.0 * px
    height = 0.25 + 0.75 * _smooth(0.0, bevel, inner)
    if look_.ridge:  # two faces meeting along the middle: one lit, one in shade
        height = height * (1.0 - 0.45 * np.abs(s))
    else:  # a twisted blade: it rises towards its leading edge
        height = height * (0.75 + 0.25 * s)
    if look_.grooves:  # short grooves near the tip, behind the trailing edge
        groove = (
            np.exp(-(((s + 0.55) / 0.07) ** 2))
            * _smooth(0.66, 0.78, r)
            * (1 - _smooth(0.9, 0.97, r))
        )
        height = height - 0.35 * groove
    core = 0.35 + 0.65 * _smooth(0.0, 6 * px, inner)

    # the hub: a dome, a flat sticker on it or a ring round it
    on_hub = r <= hub
    q = np.clip(r / hub, 0, 1)
    dome = 0.95 + 0.35 * np.sqrt(np.clip(1 - q * q, 0, 1))
    part = np.zeros_like(r)
    if look_.sticker:
        flat = q < look_.sticker
        dome = np.where(flat, 1.34, dome)
        part = np.where(on_hub & flat, 1.0, part)
    if look_.hub_ring:
        ring = (q > 0.72) & (q < 0.92)
        dome = np.where(q < 0.72, 1.1 - 0.15 * q, dome)
        part = np.where(on_hub & ring, 1.0, part)
    height = np.where(on_hub, dome * _smooth(0.0, 2 * px, hub - r) + 0.2, height)
    core = np.where(on_hub, 0.55 + 0.45 * _smooth(0.0, 4 * px, hub - r), core)
    alpha = np.where(on_hub | blade, 1.0, 0.0).astype(np.float32)

    diffuse, spec = _lit(height, 1.0 / (6 * px))
    gloss = 0.55 * spec
    return _reduce([diffuse, core, gloss, part], alpha, size)


def frame(look_: Look, size: int) -> np.ndarray:
    """The square frame round the rotor, ``size`` px: ``(5, size, size)`` as :func:`rotor`'s
    (part: Noctua's pads in the corners, Libre Panel's lit ring)."""
    n = size * SUPERSAMPLE
    x, y = _grid(n)
    px = 2.0 / n
    corner = 0.16
    qx, qy = np.maximum(np.abs(x) - (1 - corner), 0), np.maximum(np.abs(y) - (1 - corner), 0)
    outside = np.sqrt(qx * qx + qy * qy) - corner  # < 0 inside the rounded square
    r = np.sqrt(x * x + y * y)
    opening = 0.84  # the rotor's 38 of 48 px, and a little room
    body = (outside < 0) & (r > opening)
    inner = np.minimum(-outside, r - opening)
    height = 0.3 + 0.7 * _smooth(0.0, 4 * px, inner)
    part = np.zeros_like(r)
    if look_.frame_style == "noctua":  # pads over the corners, each round a screw hole
        cx, cy = np.abs(x) - 0.83, np.abs(y) - 0.83
        d = np.sqrt(cx * cx + cy * cy)
        pad = (d < 0.2) & (outside < 0)
        hole = d < 0.07
        part = np.where(pad, 1.0, part)
        height = np.where(pad, height + 0.25 * _smooth(0.0, 3 * px, 0.2 - d), height)
        body = (body | pad) & ~hole
    else:  # a lit ring round the opening
        ring = (r > opening) & (r < opening + 0.05)
        part = np.where(ring, 1.0, part)
        height = np.where(ring, height + 0.2, height)
    core = 0.45 + 0.55 * _smooth(0.0, 5 * px, inner)
    diffuse, spec = _lit(height, 1.0 / (6 * px))
    alpha = body.astype(np.float32)
    return _reduce([diffuse, core, 0.45 * spec, part], alpha, size)


def labels() -> dict[str, str]:
    """Each look's name for people."""
    return {name: found.label for name, found in LOOKS.items()}


def pitch(look_: Look) -> float:
    return 360.0 / look_.blades


def angles(look_: Look, positions: int) -> list[float]:
    """The rotor's angles for ``positions`` steps over one blade's pitch."""
    return [pitch(look_) * p / positions for p in range(positions)]


def _check() -> None:  # (the looks' numbers are sane: a blade stays within its pitch)
    from libre_panel.config import FAN_LOOKS  # (config.toml and themes name them)

    assert tuple(LOOKS) == FAN_LOOKS, "config.FAN_LOOKS names the looks, in this order"
    for found in LOOKS.values():
        assert 0 < found.fill_hub <= 1 and 0 < found.fill_tip <= 1, found.name
        assert found.blades >= 2 and not math.isnan(found.sweep), found.name


_check()

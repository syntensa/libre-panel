"""The earth as it is now: a globe lit by the sun where it stands at this moment, the cities'
lights on its night side, the clouds, the air glowing at its rim, your place marked.

The globe is drawn from four maps of the whole earth (assets/earth, made by
tools/earth_maps.py from NASA's Blue Marble and Black Marble): the land and the sea by day,
the earth at night, the clouds and where the water is. They are read when a picture is made
and let go after it (some 17 MB while it is made, none meanwhile).

A globe that stands faces your place (``[weather]`` latitude and longitude; without one the
longitude of the computer's time zone) as the earth turns: its picture is made again whenever
the line between day and night has moved a pixel at its rim (every minute or so on a large
globe), at twice its size and reduced, with the glint of the sun on the sea.

A globe that turns (``spin``: the seconds it takes for a turn) is seen from a point that goes
round it; the day and the night stay where they are on the earth at this moment, so the
night side with its lights comes round. Its maps are lit once for the moment (:class:`Turning`,
again when the day has moved a pixel) into one map at the size the globe needs: a frame
only looks that map up, a pixel of the globe a number, shifted by the turn, and lays the air
over it. It turns in steps of a pixel at its middle.

The sun's place follows the same sunrise equation as the sun's readings (sensors/sky.py).
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

MAPS = Path(__file__).resolve().parent.parent / "assets" / "earth"
SHARE = 0.86  # the disc's share of the picture: the rest is the air's glow
SS = 2  # a standing globe is made at twice its size and reduced
AIR = (107, 173, 255)  # the air's colour, unless the widget gives its own
LIGHTS = (1.0, 0.80, 0.50)  # the colour of the cities' lights
DUSK = (1.0, 0.42, 0.16)  # the band along the line between day and night
BAND = 128  # rows worked on at a time (the arrays of a band stay small)
DAY_S = 86164.1  # a turn of the earth against the stars: how fast the day moves on

_lock = threading.Lock()


# -- the sun and the view ---------------------------------------------------------------------


def subsolar(t: float) -> tuple[float, float]:
    """Where the sun stands overhead at ``t`` (Unix time): latitude and longitude, degrees."""
    from libre_panel.sensors import sky

    day = datetime.fromtimestamp(t, UTC).date()
    noon, declination, _ = sky._sun_path(day, 0.0)
    past = 360 * (sky._julian(t) - noon)  # degrees past noon at Greenwich
    return math.degrees(declination), (180 - past) % 360 - 180


def zone_longitude() -> float:
    """The longitude of the computer's time zone (its offset from UTC, 15 degrees an hour)."""
    offset = datetime.now().astimezone().utcoffset()
    hours = offset.total_seconds() / 3600 if offset is not None else 0.0
    return max(-180.0, min(180.0, hours * 15))


def redraw_s(size: int) -> float:
    """How long a globe ``size`` px wide shows the same day: until the line between day and
    night has moved a pixel at its rim (the earth turns a degree in four minutes)."""
    r = max(1.0, size * SHARE / 2)
    return max(20.0, min(600.0, DAY_S / (2 * math.pi * r)))


def _point(lat: float, lon: float) -> np.ndarray:
    a, b = math.radians(lat), math.radians(lon)
    return np.array([math.cos(a) * math.cos(b), math.cos(a) * math.sin(b), math.sin(a)], np.float32)


def _basis(lat: float, lon: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The view of the point (lat, lon) in the middle: right, up and towards the viewer,
    in the earth's axes (x to 0°/0°, y to 0°/90°E, z to the north pole)."""
    a, b = math.radians(lat), math.radians(lon)
    right = np.array([-math.sin(b), math.cos(b), 0.0], np.float32)
    up = np.array([-math.sin(a) * math.cos(b), -math.sin(a) * math.sin(b), math.cos(a)], np.float32)
    return right, up, _point(lat, lon)


def _smooth(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) * (1.0 / (hi - lo)), 0, 1)
    return t * t * (3 - 2 * t)


# -- the maps ---------------------------------------------------------------------------------


def _read(name: str, mode: str, width: int) -> np.ndarray:
    """A map as an array, ``width`` px wide or the next size a JPEG reads fast (half, a
    quarter) above it."""
    with Image.open(MAPS / name) as image:
        if image.format == "JPEG":
            image.draft(mode, (width, width // 2))
        return np.asarray(image.convert(mode))


def maps(width: int) -> dict[str, np.ndarray]:
    """The four maps for a globe whose round is ``width`` px: day and night (RGB), clouds and
    water (one value a pixel), each as wide as needed and no wider (at most 2048 px)."""
    with _lock:  # (two globes at once would hold them twice)
        return {
            "day": _read("day.jpg", "RGB", width),
            "night": _read("night.jpg", "RGB", width),
            "clouds": _read("clouds.jpg", "L", width),
            "water": _read("water.png", "L", width),
        }


def _lookup(table: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """``table``'s pixels at the points (radians), nearest."""
    h, w = table.shape[:2]
    row = ((0.5 - lat * (1 / math.pi)) * h).astype(np.int32)
    np.clip(row, 0, h - 1, out=row)
    col = ((lon * (0.5 / math.pi) + 0.5) * w).astype(np.int32)
    col %= w
    return table[row, col]


# -- the light ----------------------------------------------------------------------------


def _lit(day: np.ndarray, night: np.ndarray, clouds: np.ndarray, mu: np.ndarray,
         show_clouds: bool, show_lights: bool) -> np.ndarray:  # fmt: skip
    """The colour (0 to 1, (n, 3)) of ground with these maps' values, the sun ``mu`` (the
    cosine of its angle from overhead, below the horizon negative) above it."""
    land = day.astype(np.float32) * (1.12 / 255)
    cover = _smooth(0.22, 0.80, clouds.astype(np.float32) * (1 / 255)) if show_clouds else None
    if cover is not None:
        land *= (1 - 0.92 * cover)[:, None]
        land += (0.97 * cover)[:, None]
    lit = _smooth(-0.10, 0.10, mu)
    bright = 0.45 + 0.55 * np.sqrt(np.clip(mu, 0, 1))
    rgb = land * (bright * lit)[:, None]
    dark = 1 - _smooth(-0.16, 0.04, mu)
    seen = dark > 0
    if seen.any():  # the night side: the ground faintly, and the lights
        n = night[seen].astype(np.float32) * (1 / 255)
        veil = None if cover is None else cover[seen]
        base = n * 0.30
        if veil is not None:
            base *= (1 - 0.5 * veil)[:, None]
            base += veil[:, None] * np.array([0.03, 0.04, 0.07], np.float32)
        if show_lights:
            glow = np.clip((n[:, 0] - 0.6 * n[:, 2]) * (1 / 0.62), 0, 1) ** 0.85 * 1.25
            if veil is not None:
                glow *= 1 - 0.7 * veil
            base += glow[:, None] * np.array(LIGHTS, np.float32)
        rgb[seen] += base * dark[seen][:, None]
    band = np.exp(-(((mu - 0.015) * (1 / 0.045)) ** 2)) * 0.22  # dusk and dawn
    rgb *= (1 - 0.5 * band)[:, None]
    rgb += band[:, None] * (np.array(DUSK, np.float32) * 0.45)
    return rgb


def _air(
    size: int,
    view: tuple[np.ndarray, np.ndarray, np.ndarray],
    sun: np.ndarray,
    color: tuple[int, int, int],
) -> Image.Image:
    """The air over a globe ``size`` px (RGBA): bright at the rim of the day side, inside the
    disc and as a glow around it, none on the night side. Only the light on each of its pixels
    is worked out for the view and the sun; where they lie, and what they weigh, once a size
    (:func:`_air_geometry`), the same numbers in the same order."""
    right, up, toward = view
    g = _air_geometry(size)
    sx, sy, sz = (float(np.dot(axis, sun)) for axis in (right, up, toward))  # the sun, seen
    # inside: the light on the ground there; outside: on the rim beside it
    mu = np.empty(len(g.idx), np.float32)
    mu[g.inner] = g.x_in * sx + g.y_in * sy + g.z_in * sz
    mu[g.outer] = (g.x_out * sx + g.y_out * sy) / g.rho_out
    alpha = np.zeros(len(g.idx), np.float32)
    alpha[g.inner] = g.fres_in * _smooth(-0.35, 0.55, mu[g.inner]) * g.edge_in
    glow = np.clip(g.halo_glow * _smooth(-0.30, 0.45, mu[g.glow]) * 0.85, 0, 1) * g.rest_glow
    alpha[g.glow] += glow
    out = g.colored(color)
    out[g.idx, 3] = np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out.reshape(size, size, 4), "RGBA")


class _AirGeometry:
    """What of a globe's air stays whatever the view and the sun (:func:`_air`): its pixels
    (:func:`_air_pixels`), which lie inside the disc (``inner``: the light on the ground there
    counts, ``fres``) and which in the glow at its rim (``glow``: where the disc's own edge
    leaves room for the glow, ``halo``), and where each lies."""

    def __init__(self, size: int) -> None:
        r = size * SHARE / 2
        self.idx = idx = _air_pixels(size)
        ys, xs = np.divmod(idx, size)
        x = (xs.astype(np.float32) + (0.5 - size / 2)) * (1 / r)
        y = ((size / 2 - 0.5) - ys.astype(np.float32)) * (1 / r)
        rho = np.sqrt(x * x + y * y)
        z = np.sqrt(np.clip(1 - rho * rho, 0, 1))
        inner = rho < 1
        fres = np.where(inner, (1 - z) ** 2.5, 0.0)
        width = 0.05
        halo = np.exp(-np.clip(rho - 1, 0, None) * (1 / (width * 0.30)))
        halo *= np.clip((1 + width * 2.2 - rho) * (1 / (width * 0.6)), 0, 1)
        edge = np.clip((1.0 - rho) * r + 0.5, 0, 1)  # the disc's own edge, smooth
        # (outside the disc the light on the ground weighs nothing, and where the edge is
        # whole the glow nothing: those terms are naught there, their sum the other one)
        self.inner = np.flatnonzero(inner)
        self.outer = np.flatnonzero(~inner)
        self.x_in, self.y_in, self.z_in = x[inner], y[inner], z[inner]
        self.x_out, self.y_out = x[~inner], y[~inner]
        self.rho_out = np.maximum(rho[~inner], 1e-6)
        self.fres_in = 0.75 * fres[inner]
        self.edge_in = edge[inner]
        rest = 1 - edge
        glow = (rest > 0) & (halo > 0)
        self.glow = np.flatnonzero(glow)
        self.halo_glow, self.rest_glow = halo[glow], rest[glow]
        self.size = size
        self._colored: dict[tuple[int, int, int], np.ndarray] = {}

    def colored(self, color: tuple[int, int, int]) -> np.ndarray:
        """A new picture of the air (size x size x 4, flat) in ``color``, all of it clear."""
        made = self._colored.get(color)
        if made is None:
            made = np.zeros((self.size * self.size, 4), np.uint8)
            made[self.idx, :3] = np.minimum(255, np.array(color, np.float32) * 1.13).astype(
                np.uint8
            )
            if len(self._colored) > 4:
                self._colored.clear()
            self._colored[color] = made
        return made.copy()


_AIR_GEOMETRY: dict[int, _AirGeometry] = {}


def _air_geometry(size: int) -> _AirGeometry:
    got = _AIR_GEOMETRY.get(size)
    if got is None:
        if len(_AIR_GEOMETRY) > 8:
            _AIR_GEOMETRY.clear()
        got = _AIR_GEOMETRY[size] = _AirGeometry(size)
    return got


_AIR_PIXELS: dict[int, np.ndarray] = {}


def _air_pixels(size: int) -> np.ndarray:
    """The pixels of a globe ``size`` px where its air shows: its rim inside and the glow
    outside (the middle of the disc has none to speak of)."""
    got = _AIR_PIXELS.get(size)
    if got is None:
        r = size * SHARE / 2
        ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5 - size / 2
        rho = np.hypot(xs, ys) / r
        got = np.flatnonzero(((rho > 0.55) & (rho < 1.12)).ravel()).astype(np.int32)
        if len(_AIR_PIXELS) > 8:
            _AIR_PIXELS.clear()
        _AIR_PIXELS[size] = got
    return got


def _spot(size: int, view: tuple[np.ndarray, np.ndarray, np.ndarray],
          place: tuple[float, float] | None,
          color: tuple[int, int, int]) -> tuple[Image.Image, tuple[int, int]] | None:  # fmt: skip
    """Your place on the globe: a dot with a soft ring (RGBA) and where it goes, while it is on
    the side towards us (fading as it goes round)."""
    if place is None:
        return None
    right, up, toward = view
    p = _point(*place)
    depth = float(np.dot(p, toward))
    if depth <= 0.05:
        return None
    r = size * SHARE / 2
    cx = size / 2 + float(np.dot(p, right)) * r
    cy = size / 2 - float(np.dot(p, up)) * r
    dot = max(1.4, size * 0.008)
    reach = int(dot * 4) + 2
    x0, x1 = max(0, int(cx) - reach), min(size, int(cx) + reach + 1)
    y0, y1 = max(0, int(cy) - reach), min(size, int(cy) + reach + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32) + 0.5
    d = np.hypot(xs - cx, ys - cy)
    core = np.clip(dot - d + 0.5, 0, 1)
    ring = np.exp(-(((d - dot * 2.4) / (dot * 0.45)) ** 2)) * 0.45
    halo = np.exp(-((d / (dot * 2.2)) ** 2)) * 0.25
    a = np.clip(core + ring + halo * (1 - core), 0, 1) * min(1.0, (depth - 0.05) / 0.25)
    patch = np.empty((y1 - y0, x1 - x0, 4), np.uint8)
    patch[..., :3] = color
    patch[..., 3] = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(patch, "RGBA"), (x0, y0)


# -- a globe that stands ----------------------------------------------------------------------


def turn_latitude(place: tuple[float, float] | None) -> float:
    """Where a turning globe is seen from: above your latitude, but at most 30 degrees from the
    equator (it turns, and shows no pole); 20 degrees north without a place."""
    return 20.0 if place is None else max(-30.0, min(30.0, place[0]))


def view_of(place: tuple[float, float] | None) -> tuple[float, float]:
    """The middle of a standing globe: your place, else your time zone's longitude a little
    north of the equator (most of the land shows)."""
    if place is not None:
        return place
    return 20.0, zone_longitude()


def picture(size: int, t: float, centre: tuple[float, float], place: tuple[float, float] | None,
            air: tuple[int, int, int] = AIR, mark: tuple[int, int, int] = (255, 210, 122),
            clouds: bool = True, lights: bool = True) -> Image.Image:  # fmt: skip
    """The earth at ``t`` seen from above ``centre`` (lat, lon), ``size`` px square, the
    disc :data:`SHARE` of it and the air around it; ``place`` marked."""
    size = max(8, int(size))
    m = size * SS
    r = m * SHARE / 2
    view = _basis(*centre)
    right, up, toward = view
    sun = _point(*subsolar(t))
    # the glint of the sun on water: where the ground's normal halves sun and viewer
    half = sun + toward
    half /= max(1e-6, float(np.linalg.norm(half)))
    found = maps(round(math.pi * 2 * r))
    rgba = np.zeros((m, m, 4), np.uint8)  # (premultiplied)
    top = max(0, int(m / 2 - r) - 1)
    bottom = min(m, int(m / 2 + r) + 2)
    xs = (np.arange(m, dtype=np.float32) + (0.5 - m / 2)) * (1 / r)
    for y0 in range(top, bottom, BAND):
        y1 = min(bottom, y0 + BAND)
        ys = ((m / 2 - 0.5) - np.arange(y0, y1, dtype=np.float32)) * (1 / r)
        rho2 = xs[None, :] ** 2 + ys[:, None] ** 2
        inside = rho2 < 1.0
        if not inside.any():
            continue
        rows, at = np.nonzero(inside)
        x, y, rho2 = xs[at], ys[rows], rho2[inside]
        z = np.sqrt(1.0 - rho2)
        px = x * right[0] + y * up[0] + z * toward[0]
        py = x * right[1] + y * up[1] + z * toward[1]
        pz = x * right[2] + y * up[2] + z * toward[2]
        lat = np.arcsin(np.clip(pz, -1, 1))
        lon = np.arctan2(py, px)
        mu = px * sun[0] + py * sun[1] + pz * sun[2]
        cover = _lookup(found["clouds"], lat, lon)
        rgb = _lit(_lookup(found["day"], lat, lon), _lookup(found["night"], lat, lon), cover,
                   mu, clouds, lights)  # fmt: skip
        if mu.max() > 0:  # the glint of the sun on the water
            water = _lookup(found["water"], lat, lon).astype(np.float32) * (1 / 255)
            nh = np.clip(px * half[0] + py * half[1] + pz * half[2], 0, 1)
            glint = water * (nh**160 * 0.35 + nh**18 * 0.06) * _smooth(-0.10, 0.10, mu)
            if clouds:
                glint *= 1 - _smooth(0.22, 0.80, cover * (1 / 255))
            rgb += glint[:, None] * np.array([1.0, 0.93, 0.80], np.float32)
        edge = np.clip((1.0 - np.sqrt(rho2)) * r + 0.5, 0, 1)  # the disc's edge, smooth
        band = rgba[y0:y1]
        band[rows, at, :3] = np.clip(rgb * (edge * 255)[:, None] + 0.5, 0, 255)
        band[rows, at, 3] = (edge * 255 + 0.5).astype(np.uint8)
    del found
    globe = Image.fromarray(rgba, "RGBa").reduce(SS).convert("RGBA")
    del rgba
    out = Image.alpha_composite(globe, _air(size, view, sun, air))
    spot = _spot(size, view, place, mark)
    if spot is not None:
        out.alpha_composite(spot[0], dest=spot[1])
    return out


# -- a globe that turns -----------------------------------------------------------------------


class Turning:
    """A globe ``size`` px that turns, seen from latitude ``lat``: :meth:`light` lights its
    map for a moment (the slow part, for the helper thread), :meth:`frame` shows it from a
    longitude (a lookup a pixel)."""

    AIR_STEP = 2.0  # degrees the view goes round before its air is laid anew

    def __init__(self, size: int, lat: float, air: tuple[int, int, int] = AIR,
                 mark: tuple[int, int, int] = (255, 210, 122), clouds: bool = True,
                 lights: bool = True) -> None:  # fmt: skip
        self.size = size = max(8, int(size))
        self.lat = max(-60.0, min(60.0, lat))
        self.air, self.mark, self.clouds, self.lights = air, mark, clouds, lights
        r = size * SHARE / 2
        # the map's round is the disc's at its middle: a pixel there, a step of the turn
        self.width = max(64, 2 * math.ceil(math.pi * r))
        ys, xs = (np.mgrid[0:size, 0:size].astype(np.float32) + 0.5 - size / 2) / r
        rho2 = (xs * xs + ys * ys).ravel()
        self.inside = np.flatnonzero(rho2 < 1.0)
        x, y = xs.ravel()[self.inside], -ys.ravel()[self.inside]
        z = np.sqrt(1 - rho2[self.inside])
        a = math.radians(self.lat)
        # seen from longitude 0: a turn adds to every pixel's longitude
        px = -math.sin(a) * y + math.cos(a) * z
        py = x
        pz = math.cos(a) * y + math.sin(a) * z
        h = self.width // 2
        lat_px = np.arcsin(np.clip(pz, -1, 1))
        lon_px = np.arctan2(py, px)
        self.rowbase = np.clip(((0.5 - lat_px / math.pi) * h).astype(np.int32), 0, h - 1)
        self.rowbase *= self.width
        self.col = ((lon_px / (2 * math.pi) + 0.5) * self.width).astype(np.int32) % self.width
        rho = np.sqrt(rho2[self.inside])
        rim = rho > 1 - 2 / r
        self.rim = self.inside[rim]
        self.rim_alpha = np.clip((1.0 - rho[rim]) * r + 0.5, 0, 1)
        self.map: np.ndarray | None = None  # the earth lit (uint32 RGBA a pixel)
        self.lit_at: float | None = None
        self._air_key: tuple[int, float | None] | None = None
        self._air_layer: Image.Image | None = None
        self._sun: np.ndarray | None = None
        self._map_image: Image.Image | None = None
        self._tables: tuple[bytes, ...] | None = None

    def lit(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        """The map lit as the earth is at ``t`` (uint32, RGBA a pixel) and the sun's
        direction: the slow part, which changes nothing here (the helper thread does it)."""
        w, h = self.width, self.width // 2
        found = maps(w)
        lat = (0.5 - (np.arange(h, dtype=np.float32) + 0.5) / h) * math.pi
        lon = ((np.arange(w, dtype=np.float32) + 0.5) / w - 0.5) * (2 * math.pi)
        sun = _point(*subsolar(t))
        cos_lon, sin_lon = np.cos(lon), np.sin(lon)
        out = np.empty((h, w), np.uint32)
        mapped = out.view(np.uint8).reshape(h, w, 4)
        size = (w, h)
        box = Image.Resampling.BOX
        day = np.asarray(Image.fromarray(found["day"]).resize(size, box))
        night = np.asarray(Image.fromarray(found["night"]).resize(size, box))
        clouds = np.asarray(Image.fromarray(found["clouds"]).resize(size, box))
        del found
        for y0 in range(0, h, BAND // 2):
            y1 = min(h, y0 + BAND // 2)
            mu = np.cos(lat[y0:y1])[:, None] * (
                cos_lon[None, :] * sun[0] + sin_lon[None, :] * sun[1]
            )
            mu += np.sin(lat[y0:y1])[:, None] * sun[2]
            n = (y1 - y0) * w
            rgb = _lit(day[y0:y1].reshape(n, 3), night[y0:y1].reshape(n, 3),
                       clouds[y0:y1].reshape(n), mu.reshape(n), self.clouds,
                       self.lights)  # fmt: skip
            mapped[y0:y1, :, :3] = np.clip(rgb * 255 + 0.5, 0, 255).reshape(y1 - y0, w, 3)
        mapped[..., 3] = 255
        return out, sun

    def use(self, t: float, lit: tuple[np.ndarray, np.ndarray]) -> None:
        """Show the map :meth:`lit` made for ``t`` from the next frame on."""
        self.map, self._sun = lit
        self.lit_at = t
        self._air_key = None
        self._map_image = None

    @property
    def map_image(self) -> Image.Image:
        """The lit map as a picture (RGBA, its width x half of it), the same while the light is:
        what an engine that makes the globe's pictures gets (render/scene.py's Globe)."""
        if self.map is None:
            raise RuntimeError("light the map first")
        if self._map_image is None:
            h, w = self.map.shape
            self._map_image = Image.frombuffer("RGBA", (w, h), self.map, "raw", "RGBA", 0, 1)
        return self._map_image

    def tables(self) -> tuple[bytes, ...]:
        """Where each pixel of the disc finds itself on the map, for an engine (globe.rs): the
        disc's pixels, their map's columns at step 0 and rows times the width, the rim's pixels
        (u32 each) and their alpha (u8), as :meth:`picture_of` uses them."""
        if self._tables is None:
            alpha = (self.rim_alpha * 255 + 0.5).astype(np.uint8)
            self._tables = tuple(
                np.ascontiguousarray(a, "<u4").tobytes()
                for a in (self.inside, self.col, self.rowbase, self.rim)
            ) + (alpha.tobytes(),)
        return self._tables

    def air_at(self, step: int) -> Image.Image:
        """The air over the globe seen from the longitude of ``step``: laid anew every
        :attr:`AIR_STEP` degrees and with each light."""
        if self._sun is None:
            raise RuntimeError("light the map first")
        key = (round(self.longitude(step) / self.AIR_STEP), self.lit_at)
        if key != self._air_key or self._air_layer is None:
            view = _basis(self.lat, self.longitude(step))
            self._air_layer = _air(self.size, view, self._sun, self.air)
            self._air_key = key
        return self._air_layer

    def mark_at(self, step: int, place: tuple[float, float] | None
                ) -> tuple[Image.Image, tuple[int, int]] | None:  # fmt: skip
        """Your place's mark seen from the longitude of ``step``, and where it lies."""
        return _spot(self.size, _basis(self.lat, self.longitude(step)), place, self.mark)

    def picture_of(self, map_: np.ndarray, step: int, air: Image.Image | None,
                   mark: tuple[Image.Image, tuple[int, int]] | None) -> Image.Image:  # fmt: skip
        """The globe at ``step`` from ``map_``: each pixel of the disc the map's at its place,
        the rim's alpha its own, ``air`` over it and ``mark`` over that (changing nothing here:
        any thread may make it)."""
        idx = self.col + (self.width - step % self.width)  # (westwards: the ground goes east)
        idx[idx >= self.width] -= self.width
        idx += self.rowbase
        out = np.zeros(self.size * self.size, np.uint32)
        out[self.inside] = map_.take(idx)
        rgba = out.view(np.uint8).reshape(self.size * self.size, 4)
        rgba[self.rim, 3] = (self.rim_alpha * 255 + 0.5).astype(np.uint8)
        image = Image.frombuffer("RGBA", (self.size, self.size), out, "raw", "RGBA", 0, 1)
        if air is not None:
            image = Image.alpha_composite(image, air)
        if mark is not None:
            image.alpha_composite(mark[0], dest=mark[1])
        return image

    def light(self, t: float) -> None:
        """Light the map as the earth is at ``t``, here and now."""
        self.use(t, self.lit(t))

    def longitude(self, step: int) -> float:
        """The longitude in the middle at ``step``: the view goes west, as the earth turns
        east under it (the ground moves from left to right)."""
        return (180.0 - 360.0 * (step % self.width) / self.width) % 360.0 - 180.0

    def step_at(self, longitude: float) -> int:
        """The step that has ``longitude`` in the middle."""
        return round(-longitude * self.width / 360.0) % self.width

    def step_of(self, now: float, turn_s: float, offset: int = 0) -> tuple[int, float]:
        """The step of the turn at ``now`` (a pixel at the globe's middle a step), moved on by
        ``offset``, and when the next one is due."""
        rate = self.width / max(1.0, turn_s)
        count = math.floor(now * rate)
        return (count + offset) % self.width, (count + 1) / rate

    def frame(self, step: int, place: tuple[float, float] | None) -> Image.Image:
        """The globe seen from the longitude of ``step`` (0 to its width)."""
        if self.map is None or self._sun is None:
            raise RuntimeError("light the map first")
        return self.picture_of(self.map, step, self.air_at(step), self.mark_at(step, place))

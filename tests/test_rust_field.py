"""The light field under a theme in Rust (rust/render/src/field.rs) as the Python app's
render/field.py made it, but finer: its levels at an eighth of the panel (in sixteenths of a
level, where Python made them whole) and the picture drawn up (through a dither, where Python
rounded each pixel), for panels of every size where they stand in canvases of one to three
panels, at moments over hours and under every load; and frames over it, step after step. What
lp-check answers is checked against what it answered when the light was made so, which with
whole levels and no dither was what the Python app answered (golden.py); that the field runs on
from one panel to the next, and the dither's own sums, are field.rs's own tests."""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import math
import random
import struct
from pathlib import Path
from typing import Any

import golden
import pytest
from PIL import Image, ImageDraw
from rust_check import run
from test_rust_render import (
    THEMES,
    _card_or_text,
    _frame_snapshot,
    _frame_theme,
    _moving,
    _snapshot_given,
)
from test_rust_render import _demo as _demo_snapshot

from libre_panel import fonts
from libre_panel.sensors.base import Snapshot

pytestmark = pytest.mark.kept

LOOKS = ("arena", "lumen")  # the looks of the light (field.rs's look)
LOAD_SENSORS = ("cpu.load", "gpu.load")  # whose loads the light follows
RATE = 6.25  # fields a second
Canvas = tuple[tuple[float, float, float, float], tuple[float, float, float, float]]


def _stepped(t: float) -> float:
    """The moment of the field shown at `t`: the last of its steps (field.rs's stepped)."""
    return math.floor(t * RATE) / RATE


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _case(rng: random.Random) -> dict[str, Any]:
    w, h = rng.choice([(1920, 720), (1920, 480), (480, 480), (320, 170), (800, 480), (101, 37)])
    per = rng.uniform(0.06, 0.2)  # (millimetres a pixel)
    own = [rng.uniform(-300, 300), rng.uniform(-100, 100), w * per, h * per]
    left, top = own[0] - rng.uniform(0, 400) * rng.random(), own[1] - rng.uniform(0, 60)
    whole = [left, top, own[0] + own[2] - left + rng.uniform(0, 400) * rng.random(),
             own[1] + own[3] - top + rng.uniform(0, 60)]  # fmt: skip
    t = rng.choice([0.0, 1e9 + rng.uniform(0, 86400), rng.uniform(0, 2000)])
    load = rng.choice([0.0, 1.0, rng.random(), -0.5, 1.7])
    base = rng.choice([[0, 0, 0], [10, 14, 19], [rng.randrange(256) for _ in range(3)]])
    look = rng.choice(LOOKS)
    return {"size": [w, h], "own": own, "whole": whole, "t": t, "load": load, "base": base,
            "look": look}  # fmt: skip


def _wired(case: dict[str, Any], digest: bool) -> dict[str, Any]:
    return {"op": "field", "look": case["look"], "size": case["size"], "digest": digest,
            "own": [_bits(v) for v in case["own"]], "whole": [_bits(v) for v in case["whole"]],
            "t": _bits(case["t"]), "load": _bits(case["load"]), "base": case["base"]}  # fmt: skip


def test_the_light_field_as_python_makes_it() -> None:
    rng = random.Random(0xF1E1D)
    cases = [_case(rng) for _ in range(120)]
    small = [c for c in cases if c["size"][0] * c["size"][1] < 500_000]
    got = run([_wired(c, digest=c not in small) for c in cases])
    assert {c["look"] for c in cases} == set(LOOKS)
    for case, answer in zip(cases, got, strict=True):
        fw, fh, data = answer["small"]
        assert len(base64.b64decode(data)) == fw * fh * 3 * 2, case  # (two bytes a level)
        assert ("sha256" in answer["field"]) == (case not in small), case
    assert len(got) == len(cases) and len(small) > 20


# -- frames over the light, step after step ---------------------------------------------------

LIGHT_SWITCHES = {"light_flows": "The light drifts", "light_load": "The light follows the load"}


def _lit(folder: Path, rng: random.Random, look: str | None = None) -> None:
    """The theme in `folder` with a light field under it: drifting or still, following the load
    or at rest, by a switch of the theme (on or off) or always; of any look, in the colours of
    the cover while there is one or not."""
    path = folder / "theme.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    spec: dict[str, Any] = {"look": look or rng.choice(LOOKS)}
    if rng.random() < 0.45:
        spec["colours"] = "@media.cover"
    for key, switch in (("moves", "light_flows"), ("load", "light_load")):
        spec[key] = rng.choice(["", f"switch.{switch}", f"!switch.{switch}"])
    data.setdefault("background", {})["field"] = spec
    switches = data.setdefault("switches", {})
    for name, label in LIGHT_SWITCHES.items():
        switches[name] = {"label": label, "on": rng.random() < 0.7}
    path.write_text(json.dumps(data), encoding="utf-8")


def _loads(rng: random.Random, n: int) -> list[float]:
    """A load's last `n` numbers: busy, idle, rising, none."""
    kind = rng.choice(["busy", "idle", "rising", "mixed"])
    if kind == "busy":
        return [rng.uniform(85, 100) for _ in range(n)]
    if kind == "idle":
        return [rng.uniform(0, 4) for _ in range(n)]
    if kind == "rising":
        return [100 * k / max(1, n - 1) for k in range(n)]
    return [rng.choice([0, 3.5, 47.0, 100, 120, -5]) for _ in range(n)]


def _covers() -> list[Any]:
    """Covers a track may have: the demo's, a warm and a cold one, one of greys (no colour of
    its own), a small dark one, and none."""

    def made(a: tuple[int, int, int], b: tuple[int, int, int], size: int = 48) -> Image.Image:
        im = Image.new("RGB", (size, size), a)
        ImageDraw.Draw(im).ellipse((size // 4, size // 3, size, size), fill=b)
        return im

    mode, png = golden.given("demo_cover")
    with Image.open(io.BytesIO(base64.b64decode(png))) as kept:
        demo = kept.convert(mode)
    return [
        demo,
        made((200, 40, 30), (250, 180, 40)),
        made((20, 40, 120), (60, 200, 230)),
        made((90, 90, 90), (200, 200, 200)),
        made((40, 10, 30), (90, 20, 60), 20),
        None,
    ]


COVERS = _covers()


def _lit_steps(rng: random.Random, snapshot: Snapshot) -> list[Any]:
    """Frames of a panel one after another, as its loop draws them at 25 a second: a new
    reading now and then (its history of loads grown by one, the light easing to it over the
    second after; a new cover or none now and then, the light gliding to its colours), the
    frames a 25th to a second apart, now and then at the very step of the light or back in
    time. What lp-check is given."""
    now = rng.choice([1000.0, 1_790_000_000.0 + rng.uniform(0, 86400), rng.uniform(0, 5000)])
    history = {key: _loads(rng, rng.randrange(0, 14)) for key in LOAD_SENSORS
               if rng.random() < 0.8}  # fmt: skip
    images = dict(snapshot.images)
    images.pop("media.cover", None)
    cover = rng.choice(COVERS)
    given = []
    for _ in range(rng.randrange(6, 16)):
        sample = None
        if rng.random() < 0.35:  # (a reading came: its loads one longer)
            for values in history.values():
                values.append(rng.choice([0.0, 12.5, 64.0, 99.0, 100.0]))
            sample = (now, rng.choice([1.0, 0.5, 2.0]))
            if rng.random() < 0.5:
                cover = rng.choice(COVERS)
        shot = dataclasses.replace(
            snapshot, history={k: list(v) for k, v in history.items()},
            images={**images, **({} if cover is None else {"media.cover": cover})},
        )  # fmt: skip
        given.append({"snapshot": _snapshot_given(shot), "now": now,
                      "sample": None if sample is None else list(sample)})  # fmt: skip
        now += rng.choice([0.04, 0.04, 0.04, 0.12, 0.16, 0.2, 0.5, 1.0, 0.0, -0.08])
        if rng.random() < 0.15:  # (at a step of the light itself)
            now = _stepped(now) + rng.choice([0.0, 1 / RATE])
    return given


def _canvas(rng: random.Random, size: tuple[int, int]) -> Canvas | None:
    """Where a panel of `size` stands among others (millimetres), or None: by itself."""
    if rng.random() < 0.3:
        return None
    per = rng.uniform(0.06, 0.2)
    own = (rng.uniform(-200, 200), rng.uniform(-50, 50), size[0] * per, size[1] * per)
    whole = (own[0] - rng.uniform(0, 300), own[1] - rng.uniform(0, 40),
             own[2] + rng.uniform(0, 600), own[3] + rng.uniform(0, 80))  # fmt: skip
    return own, whole


def _frames_case(
    folder: Path, given: list[Any], canvas: Canvas | None, continuous: bool = True
) -> dict[str, Any]:
    return {
        "folder": str(folder),
        "dither": False,
        "animate": True,
        "preview": False,
        "fps": 25,
        "continuous": continuous,
        "steps": given,
        "light_canvas": None if canvas is None else [list(canvas[0]), list(canvas[1])],
    }


def _steps(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps lp-check drew of the themes it read."""
    return [step for got in frames if "error" not in got for step in got["steps"]
            if "unported" not in step]  # fmt: skip


def test_frames_over_the_light_as_python_lays_them(tmp_path: Path) -> None:
    # (themes of cards and texts over a light field, drifting or not, following the load or
    # not: each frame to the bit, its boxes, whether it moves on and when it is due again (the
    # light's next step); where the panel stands among others or by itself; the light stepping
    # while the cards stand, the cards changing while the light stands, both; on a panel the
    # frames stream to, and on one sent whole pictures, where the light stands; the lights in
    # their look's colours or a cover's, some on their way from one cover's to the next)
    rng = random.Random(0x11647)
    cases = []
    for n in range(40):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        _lit(folder, rng)
        size = (data["display"]["width"], data["display"]["height"])
        given = _lit_steps(rng, _frame_snapshot(rng)[1])
        canvas, streams = _canvas(rng, size), rng.random() < 0.75
        cases.append(_frames_case(folder, given, canvas, streams))
    made = _steps(_moving(cases))
    due = sum(step["due"] is not None for step in made)  # (the light steps on: a frame is due)
    lit = len({step["sha256"] for step in made})
    assert len(made) > 300 and due > 100 and lit > 150, (len(made), due, lit)


def test_lumen_over_the_light_on_both_panels_as_python_lays_it(tmp_path: Path) -> None:
    # (Lumen on the 12.3" and its companion on end beside it, as the person's two panels stand,
    # each its part of one light; the demo's readings, the loads busy, idle or rising: within a
    # level or two of Python's where a lit ring, its light made in floating point to a level,
    # lies over it)
    import shutil

    rng = random.Random(0x1A3E2)
    whole = (0.0, 0.0, 357.21, 226.72)  # (the 12.3" across, the 9.2" on end 8 mm to its right)
    places: dict[str, Canvas] = {
        "lumen-1920x720": ((0.0, 58.5, 292.53, 109.70), whole),
        "lumen-companion-vertical": ((300.53, 0.0, 56.68, 226.72), whole),
    }
    cases = []
    for n, (name, canvas) in enumerate(places.items()):
        folder = shutil.copytree(THEMES / name, tmp_path / f"{name}-{n}")
        _lit(folder, rng)
        given = _lit_steps(rng, _demo_snapshot())
        cases.append({**_frames_case(folder, given, canvas), "bytes": True})
    assert len(_steps(_moving(cases))) >= 12


def test_frames_of_layers_over_the_light_as_python_hands_them_out(tmp_path: Path) -> None:
    # (the same frames as a panel's engine gets them: as their layers over the light where the
    # engine draws the light up and takes every piece as it is, else composed here; each, made
    # into its picture as the engine composes it, to the bit, and gone the same way as Python's)
    rng = random.Random(0x5CE7E)
    cases = []
    for n in range(30):
        folder = tmp_path / f"t{n}"
        data = _frame_theme(rng, folder, _card_or_text)
        _lit(folder, rng)
        size = (data["display"]["width"], data["display"]["height"])
        given = _lit_steps(rng, _frame_snapshot(rng)[1])
        canvas, native = _canvas(rng, size), rng.random() < 0.7
        streams = rng.random() < 0.75
        cases.append({**_frames_case(folder, given, canvas, streams), "scene": True,
                      "native_lights": native})  # fmt: skip
    [ours] = run([{"op": "render_frame", "frames": cases, "fonts_dir": str(fonts.FONT_DIR)}])
    ways = {True: 0, False: 0}
    for n, got in enumerate(ours["frames"]):
        if "error" in got:  # (a theme Python did not read: nor does Rust)
            continue
        for i, g in enumerate(got["steps"]):
            assert "unported" not in g, (n, i, g)
            assert not g["layered"] or cases[n]["native_lights"], (n, i)
            ways[g["layered"]] += 1
    assert ways[True] > 100 and ways[False] > 50, ways

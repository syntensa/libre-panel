"""rust/render's screens of the panel's own against Python's (docs/RUST.md): render/bootscreen.py's
boot logo, standby clip and pictures of Libre Panel's own look for every panel of the catalog,
in both orientations, at the panel's size and smaller for the app, and render/bootlooks.py's
looks for panels of every shape, as numpy draws them on a processor without AVX-512 (with it,
numpy on Linux takes SVML's power, a unit in the last place apart now and then): to the bit,
and each look's canvases to the bit of their floats before they are made pictures; where
Python's raises (a look there is not), Rust's fails."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rust_check import run

from libre_panel import fonts
from libre_panel.devices.models import MODELS

_PYTHON = r"""
import dataclasses, hashlib, json, sys
import numpy as np
from libre_panel.devices.models import MODELS
from libre_panel.render import bootscreen
out = []
for case in json.loads(open(sys.argv[1]).read()):
    model = next(m for m in MODELS if m.id == case["model"])
    if "hidden" in case:  # (other hidden strips)
        clear = case.get("clear")
        model = dataclasses.replace(model, hidden=tuple(case["hidden"]),
                                    portrait_clear=tuple(clear) if clear else None)
    o, design, scale = case["orientation"], case["design"], case.get("scale", 1.0)
    canvases = []  # (a look's canvases: the digests of their floats, a zero's sign aside)
    try:
        if case["kind"] == "look":
            where = bootscreen.room(model, o, scale)
            look = bootscreen.design_of(design).make(where)
            pictures = []
            for p in [None] + case["phases"]:
                canvas = look.still() if p is None else look.frame(p)
                floats = (canvas + np.float32(0)).astype("<f4").tobytes()
                canvases.append(hashlib.sha256(floats).hexdigest())
                pictures.append(bootscreen.finish(canvas, where, dither=scale == 1.0))
        elif case["kind"] == "logo":
            pictures = [bootscreen.boot_logo(model, o, design, scale=scale)]
        elif case["kind"] == "clip":
            pictures = bootscreen.standby_frames(model, case.get("count"), o, design, scale)
        else:
            pictures = [bootscreen.picture(model, case.get("phase"), o, design)]
    except Exception as e:
        out.append({"error": type(e).__name__})
        continue
    made = {"pictures": [{"size": list(p.size), "sha256": hashlib.sha256(p.tobytes()).hexdigest()}
                         for p in pictures]}
    if canvases:
        made["canvases"] = canvases
    out.append(made)
open(sys.argv[2], "w").write(json.dumps(out))
"""
# points of the loop: its ends, just past its start and short of its end, the wave's turns, and
# past the loop both ways
PHASES = [0.0, 1e-9, 0.1, 0.125, 0.25, 0.3, 0.5, 2 / 3, 0.75, 0.999999, 1.0, -0.25, 1.5]


def _avx512() -> list[str]:
    """The AVX-512 features numpy found here (to be left unused)."""
    from numpy._core._multiarray_umath import __cpu_features__ as features

    return [name for name in ("X86_V4", "AVX512_ICL", "AVX512_SPR") if features.get(name)]


def _python(cases: list[dict[str, Any]]) -> list[Any]:
    """Each case's pictures (their sizes and digests) as Python draws them, in a process told to
    leave numpy's AVX-512 paths unused."""
    if not cases:
        return []
    env = dict(os.environ)
    if _avx512():
        env["NPY_DISABLE_CPU_FEATURES"] = " ".join(_avx512())
    with tempfile.TemporaryDirectory() as folder:
        asked, made = Path(folder) / "cases.json", Path(folder) / "made.json"
        asked.write_text(json.dumps(cases), encoding="utf-8")
        subprocess.run([sys.executable, "-c", _PYTHON, str(asked), str(made)], env=env,
                       check=True, timeout=1800)  # fmt: skip
        return json.loads(made.read_text(encoding="utf-8"))


def _both(cases: list[dict[str, Any]]) -> tuple[list[Any], list[Any]]:
    """Each case's pictures as Python draws them and as Rust does (lp-check), the cases dealt
    out to as many processes of each as the processor has cores (four at most), all at once."""
    ways = max(1, min(4, os.cpu_count() or 1))
    parts = [cases[k::ways] for k in range(ways)]
    asked = [[dict(case, op="boot", fonts_dir=str(fonts.FONT_DIR)) for case in part]
             for part in parts]  # fmt: skip
    with ThreadPoolExecutor(max_workers=2 * ways) as pool:
        theirs = [pool.submit(_python, part) for part in parts]
        mine = [pool.submit(run, part) if part else None for part in asked]
        want = [future.result() for future in theirs]
        got = [future.result() if future else [] for future in mine]
    order = lambda done: [done[n % ways][n // ways] for n in range(len(cases))]  # noqa: E731
    return order(want), order(got)


def _cases(rng: random.Random) -> list[dict[str, Any]]:
    """Every panel of the catalog in both orientations: its boot logo at its size and as the
    app draws it 960 px wide, a picture of its clip at a point of the loop, and four pictures
    of the clip of the first panel of each size and shape; two whole clips (the look's own
    number of pictures, asked for with none and with 0); logos and clips far smaller (rooms of
    8 px a side among them); and a look there is not."""
    cases: list[dict[str, Any]] = []
    shapes: set[Any] = set()
    for model in MODELS:
        for o in ("landscape", "portrait"):
            base = {"model": model.id, "orientation": o, "design": rng.choice(["", "libre-panel"])}
            cases.append(dict(base, kind="logo", scale=1.0))
            width = model.size(o)[0]
            if width > 960:  # (the app's picture of it)
                cases.append(dict(base, kind="logo", scale=min(1.0, 960 / width)))
            phase = rng.choice(PHASES + [rng.random()])
            cases.append(dict(base, kind="picture", phase=phase))
            if (model.size(o), model.shape) not in shapes:
                shapes.add((model.size(o), model.shape))
                cases.append(dict(base, kind="clip", count=4,
                                  scale=rng.choice([1.0, 1.0, 0.5, 0.75])))  # fmt: skip
    bar = next(m for m in MODELS if m.size("landscape") == (1920, 480))
    small = next(m for m in MODELS if m.size("landscape") == (480, 320))
    cases.append({"model": bar.id, "orientation": "landscape", "design": "", "kind": "clip",
                  "scale": 0.5})  # fmt: skip
    cases.append({"model": small.id, "orientation": "portrait", "design": "", "kind": "clip",
                  "count": 0, "scale": 1.0})  # fmt: skip
    for _ in range(24):
        model = rng.choice(MODELS)
        scale = rng.choice([0.01, 0.02, 0.04, 0.06, 0.1, rng.uniform(0.05, 0.9)])
        cases.append({"model": model.id, "orientation": rng.choice(["landscape", "portrait"]),
                      "design": "", "kind": rng.choice(["logo", "clip"]), "count": 2,
                      "scale": scale})  # fmt: skip
    cases.append({"model": bar.id, "orientation": "landscape", "design": "nonsense",
                  "kind": "logo", "scale": 1.0})  # fmt: skip
    cases += _strips(rng, [""])
    return cases


def _strips(rng: random.Random, designs: list[str]) -> list[dict[str, Any]]:
    """Panels of the catalog as though their bezels hid other strips, of each side another
    (and the portrait's own clear strips now and then): logos and pictures of each look."""
    cases: list[dict[str, Any]] = []
    for design in designs:
        for _ in range(4):
            model = rng.choice(MODELS)
            hidden = [rng.choice([0, 0, rng.randrange(1, 60)]) for _ in range(4)]
            clear = [rng.randrange(0, 30) for _ in range(4)] if rng.random() < 0.3 else None
            cases.append({"model": model.id, "orientation": rng.choice(["landscape", "portrait"]),
                          "design": design, "kind": rng.choice(["logo", "picture"]),
                          "phase": rng.random(), "scale": 1.0, "hidden": hidden,
                          "clear": clear})  # fmt: skip
    return cases


def test_the_panel_s_own_screens_as_python_draws_them() -> None:
    cases = _cases(random.Random(0xB007))
    want, got = _both(cases)
    drawn, refused, sizes = 0, 0, set()
    for case, mine, theirs in zip(cases, got, want, strict=True):
        if "error" in theirs:
            assert "error" in mine, (case, mine)
            refused += 1
            continue
        assert mine == theirs, case
        drawn += len(theirs["pictures"])
        sizes.update(tuple(p["size"]) for p in theirs["pictures"])
    assert drawn > 350 and len(sizes) > 60 and refused >= 1, (drawn, len(sizes), refused)


# the other looks (render/bootlooks.py) and Libre Panel's own, and how many rooms each is drawn
# for by chance besides those it always is
DESIGNS = {
    "aurora": 9, "pulse": 15, "circuit": 15, "night": 7, "levels": 13, "dark": 17, "libre-panel": 9
}  # fmt: skip


def _look_cases(rng: random.Random) -> list[dict[str, Any]]:
    """Each look made for a bar (1920 x 480), a round panel, a bar on end and rooms of the
    catalog chosen by chance, at the panel's size, smaller for the app and far smaller: its
    still and two pictures of its clip, and the floats of the canvases they are finished from;
    its boot logo and a clip of two pictures as the app asks for them; and panels with other
    hidden strips."""
    rooms = [(m.id, o) for m in MODELS for o in ("landscape", "portrait")]
    bar = next(m for m in MODELS if m.size("landscape") == (1920, 480))
    disc = next(m for m in MODELS if m.shape == "round")
    always = [(bar.id, "landscape", 1.0), (disc.id, "landscape", 1.0), (bar.id, "portrait", 0.5)]
    cases: list[dict[str, Any]] = []
    for design, more in DESIGNS.items():
        chosen = always + [(*room, rng.choice([1.0, 1.0, 0.5, 0.75, rng.uniform(0.02, 0.3)]))
                           for room in rng.sample(rooms, more)]  # fmt: skip
        for model_id, o, scale in chosen:
            phases = [rng.choice(PHASES), rng.random()]
            cases.append({"model": model_id, "orientation": o, "design": design, "kind": "look",
                          "scale": scale, "phases": phases})  # fmt: skip
        small = rng.choice([m for m in MODELS if max(m.size("landscape")) <= 480])
        cases.append({"model": small.id, "orientation": "landscape", "design": design,
                      "kind": "logo", "scale": 1.0})  # fmt: skip
        cases.append({"model": small.id, "orientation": "portrait", "design": design,
                      "kind": "clip", "count": 2, "scale": 0.5})  # fmt: skip
    return cases + _strips(rng, list(DESIGNS))


def _every_room(rng: random.Random) -> list[dict[str, Any]]:
    """Each look made for each room of the catalog (a room of each size, shape, strips and
    orientation), at the panel's size and as the app draws it 960 px wide: its still and a
    picture of its clip. A look's choices change from one room to the next: Pulse's beats on
    the bars of 11" and 11.3", the blur of Aurora's stars on the 5.2", 8" and 12.3", two of its
    stars on one pixel on the 6.86" on end."""
    each: dict[Any, tuple[Any, str]] = {}
    for m in MODELS:
        for o in ("landscape", "portrait"):
            each.setdefault((m.size(o), m.shape, m.hidden, m.portrait_clear, o), (m, o))
    cases: list[dict[str, Any]] = []
    for design in DESIGNS:
        for model, o in each.values():
            width = model.size(o)[0]
            for scale in [1.0] + ([960 / width] if width > 960 else []):
                cases.append({"model": model.id, "orientation": o, "design": design,
                              "kind": "look", "scale": scale,
                              "phases": [rng.random()]})  # fmt: skip
    return cases


def test_every_look_of_the_panel_s_screens_as_python_draws_it() -> None:
    cases = _look_cases(random.Random(0xB00715)) + _every_room(random.Random(0xB00716))
    want, got = _both(cases)
    drawn: dict[str, int] = {}
    floats = 0
    for case, mine, theirs in zip(cases, got, want, strict=True):
        assert mine == theirs, case
        drawn[case["design"]] = drawn.get(case["design"], 0) + len(theirs["pictures"])
        floats += len(theirs.get("canvases", []))
    assert len(drawn) == len(DESIGNS) and min(drawn.values()) > 180 and floats > 1300, (
        drawn,
        floats,
    )

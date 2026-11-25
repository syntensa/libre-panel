"""rust/render's screens of the panel's own against Python's (docs/RUST.md): render/bootscreen.py's
boot logo, standby clip and pictures of Libre Panel's own look for every panel of the catalog,
in both orientations, at the panel's size and smaller for the app, and render/bootlooks.py's
looks for panels of every shape, as numpy drew them on a processor without AVX-512: to the
bit, and each look's canvases to the bit of their floats before they are made pictures; where
Python's raised (a look there is not), Rust's fails. What Python drew is kept (golden.py), on
each system as its CI drew it: lp-check's answers are checked against it."""

from __future__ import annotations

import os
import random
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import golden
import pytest
from rust_check import run

from libre_panel import fonts
from libre_panel.devices.models import MODELS

pytestmark = pytest.mark.kept  # (what bootscreen.py and bootlooks.py drew: tests/golden)

# points of the loop: its ends, just past its start and short of its end, the wave's turns, and
# past the loop both ways
PHASES = [0.0, 1e-9, 0.1, 0.125, 0.25, 0.3, 0.5, 2 / 3, 0.75, 0.999999, 1.0, -0.25, 1.5]


def _drawn(cases: list[dict[str, Any]]) -> list[Any]:
    """Each case's pictures (their sizes and digests) as lp-check draws them: the cases dealt
    out to as many processes as the processor has cores (four at most), all at once, and the
    answers checked against those kept in the cases' order."""
    asked = [dict(case, op="boot", fonts_dir=str(fonts.FONT_DIR)) for case in cases]
    ways = max(1, min(4, os.cpu_count() or 1))
    parts = [asked[k::ways] for k in range(ways)]
    with ThreadPoolExecutor(max_workers=ways) as pool:
        done = list(pool.map(lambda part: run(part, checked=False) if part else [], parts))
    answers = [done[n % ways][n // ways] for n in range(len(cases))]
    golden.answered(answers)
    return answers


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
    drawn, refused, sizes = 0, 0, set()
    for answer in _drawn(_cases(random.Random(0xB007))):
        if "error" in answer:
            refused += 1
            continue
        drawn += len(answer["pictures"])
        sizes.update(tuple(p["size"]) for p in answer["pictures"])
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
    drawn: dict[str, int] = {}
    floats = 0
    for case, answer in zip(cases, _drawn(cases), strict=True):
        drawn[case["design"]] = drawn.get(case["design"], 0) + len(answer["pictures"])
        floats += len(answer.get("canvases", []))
    assert len(drawn) == len(DESIGNS) and min(drawn.values()) > 180 and floats > 1300, (
        drawn,
        floats,
    )

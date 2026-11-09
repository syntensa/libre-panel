"""rust/render's transitions against Python's (docs/RUST.md): overlays.transition for each
built-in name, with the parameters a service passes, the new theme's colours and language, and
the frames each draws from an old screen to the new one, the same to the bit; where Python's
raises, Rust's fails too."""

from __future__ import annotations

import hashlib
import json
import random
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from rust_check import image, run

from libre_panel import fonts, i18n
from libre_panel.plugins.render import RenderContext
from libre_panel.render import overlays
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import load_theme

THEME_FORMAT = "libre-panel-theme/1"
NAMES = ["fade", "slide", "tube", "entrance", "wipe", "iris", "blinds", "dissolve", "slide-right",
         "slide-up", "slide-down", "zoom", "light", "glitch", "tiles", "shatter"]  # fmt: skip
OTHERS = ["cut", "", "nope", "Fade"]  # (none for these)
SIZES = [(1920, 480), (480, 1920), (800, 480), (480, 480), (1280, 720), (320, 240), (240, 320),
         (1920, 462), (180, 640), (64, 36), (16, 9), (3, 2), (1, 1), (1000, 31)]  # fmt: skip
# what a palette may say of the colours transitions draw with (void, accent, text, muted; a
# theme with a colour Pillow cannot read does not load)
COLOURS = ["#000000", "#2ee6d6", "#ff0000", "#12345678", "red", "rgb(1,2,3)", "#abc",
           "hsl(120, 50%, 50%)", "#FFFFFF", "lightgoldenrodyellow"]  # fmt: skip
ROLES = ["void", "accent", "primary", "text", "tx_1", "muted", "text3", "dim"]
LANGUAGES = ["en", "de", "fr", "ru", "ja", "zh", "pl"]
# the pictures a service may have published, and what an entrance names as its logo
PICTURES = {"media.cover": {"size": [120, 90], "mode": "RGB", "seed": 7},
            "app.icon": {"size": [48, 48], "mode": "RGBA", "seed": 11},
            "game.logo": {"size": [900, 120], "mode": "RGBA", "seed": 13},
            "tall.logo": {"size": [20, 400], "mode": "L", "seed": 5}}  # fmt: skip
LOGOS = [None, "media.cover", "app.icon", "game.logo", "tall.logo", "not.there", "", 7, ["x"]]
TITLES = [None, "", "Cyberpunk 2077", "Straße", "Dvořák · Symphony No. 9", "ゲーム", "Ünïcödé ß",
          "A" * 80, 0, 1, 2.5, True, ["a", 1]]  # fmt: skip
KINDS = [None, "start", "end", "media", "media-end", "idle", "idle-end", "other", "", 1, ["start"]]


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _typed(v: Any) -> Any:
    """A value as lp-check's typed() takes it."""
    if isinstance(v, bool):
        return {"b": v}
    if isinstance(v, int):
        return {"i": str(v)}
    if isinstance(v, float):
        return {"f": _bits(v)}
    if isinstance(v, str):
        return {"s": v}
    if isinstance(v, list):
        return {"l": [_typed(x) for x in v]}
    return None


def _float(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


def _noise(spec: dict[str, Any]) -> Image.Image:
    """A frame of noise as lp-check makes it: each byte the top 8 of the low 32 bits of its
    index times 2654435761 plus the seed."""
    (w, h), mode, seed = spec["size"], spec["mode"], spec["seed"]
    index = np.arange(w * h * len(mode), dtype=np.uint64)
    made = (index * np.uint64(2654435761) + np.uint64(seed)) & np.uint64(0xFFFFFFFF)
    return Image.frombytes(mode, (w, h), (made >> np.uint64(24)).astype(np.uint8).tobytes())


def _sha(im: Image.Image) -> str:
    return hashlib.sha256(im.tobytes()).hexdigest()


def _frame(size: tuple[int, int], mode: str, seed: int) -> dict[str, Any]:
    """A frame of noise, `seed` spread over 32 bits: frames of seeds a few apart would be the
    same picture (their sums differ in low bits only, and the noise is the top byte)."""
    return {"size": list(size), "mode": mode, "seed": (seed * 0x9E3779B9) & 0xFFFFFFFF}


def _python(folder: Path, language: str, switches: list[dict[str, Any]]) -> list[Any]:
    """Each switch's answer from Python, as lp-check gives Rust's."""
    i18n.set_language(language)
    try:
        renderer = Renderer(load_theme(folder), preview=True)
        renderer.snapshot = Snapshot(images={k: _noise(v) for k, v in PICTURES.items()})
        context = RenderContext(renderer)
        olds: dict[str, Image.Image] = {}  # (the same spec, the same frame: its id stays)
        answers: list[Any] = []
        for switch in switches:
            made = overlays.transition((switch["name"], switch["raw"]), context)
            if made is None:
                answers.append({"made": False})
                continue
            steps = []
            for old_spec, new_spec, t in switch["steps"]:
                old = olds.setdefault(json.dumps(old_spec), _noise(old_spec))
                try:
                    out = made.frame(old, _noise(new_spec), _float(t))
                    steps.append({"size": list(out.size), "mode": out.mode, "sha256": _sha(out)})
                except Exception:
                    steps.append({"error": True})
            answers.append({
                "made": True, "name": made.name, "duration": _bits(made.duration),
                "switch": made.switch,
                "on_switch": [made.on_switch("x", None, False), made.on_switch("x", None, True)],
                "toasts": made.toasts, "from_shown": made.from_shown, "steps": steps,
            })  # fmt: skip
        return answers
    finally:
        i18n.set_language("en")


def _theme(folder: Path, size: tuple[int, int], palette: dict[str, str]) -> None:
    folder.mkdir()
    display = {"width": max(16, size[0]), "height": max(16, size[1])}  # (the frames' own sizes)
    theme = {"format": THEME_FORMAT, "name": "transitions", "palette": palette, "widgets": [],
             "display": display}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")


def _params(rng: random.Random, name: str) -> dict[str, Any] | None:
    if name != "entrance":
        return rng.choice([None, {}, {"title": "ignored", "kind": "start"}])
    params: dict[str, Any] = {}
    for key, values in (("title", TITLES), ("text", TITLES), ("logo", LOGOS), ("kind", KINDS)):
        if rng.random() < 0.6:
            params[key] = rng.choice(values)
    return params


def _times(rng: random.Random) -> list[float]:
    """How far a transition has gone at each step: mostly on, now and then back or at its end."""
    kind = rng.random()
    if kind < 0.6:
        times = sorted(rng.random() for _ in range(rng.randrange(1, 9)))
    elif kind < 0.8:
        times = [rng.choice([0.0, 0.25, 0.5, 0.999, 1.0, 1.5]) for _ in range(rng.randrange(1, 5))]
    else:
        times = [rng.uniform(-0.1, 1.1) for _ in range(rng.randrange(1, 6))]
    return times


def _compare(tmp_path: Path, seed: int, count: int, scenarios: Any = ()) -> int:
    """`count` themes with switches drawn at random, then `scenarios`' own, in Rust and in
    Python: every answer alike. How many frames were compared."""
    rng = random.Random(seed)
    cases = []
    for _ in range(count):
        size = rng.choice(SIZES)
        palette = {role: rng.choice(COLOURS) for role in ROLES if rng.random() < 0.4}
        language = rng.choice(LANGUAGES)
        modes = rng.choice([("RGB", "RGB")] * 6 + [("RGBA", "RGBA"), ("L", "L"), ("RGB", "RGBA")])
        switches = []
        for _ in range(rng.randrange(1, 4)):
            name = rng.choice(NAMES * 3 + OTHERS)
            old = rng.randrange(1000)
            steps = []
            for t in _times(rng):
                if rng.random() < 0.08:  # (another old frame: a shatter breaks it anew)
                    old = rng.randrange(1000)
                new = _frame(size, modes[1], rng.randrange(1000))
                steps.append([_frame(size, modes[0], old), new, _bits(t)])
            switches.append({"name": name, "raw": _params(rng, name), "steps": steps})
        cases.append((size, palette, language, switches))
    for scenario in scenarios:
        cases.append(scenario())
    wired = []
    for n, (size, palette, language, switches) in enumerate(cases):
        folder = tmp_path / f"t{n}"
        _theme(folder, size, palette)
        wire = [{"name": s["name"], "steps": s["steps"],
                 "params": {k: _typed(v) for k, v in (s["raw"] or {}).items()}}
                for s in switches]  # fmt: skip
        wired.append({"op": "transition", "theme": str(folder), "fonts_dir": str(fonts.FONT_DIR),
                      "locale": str(i18n.LOCALE_DIR), "language": language,
                      "images": {k: image(_noise(v)) for k, v in PICTURES.items()},
                      "switches": wire})  # fmt: skip
    answers = run(wired)
    frames = 0
    for n, (got, (_, _, language, switches)) in enumerate(zip(answers, cases, strict=True)):
        want = _python(tmp_path / f"t{n}", language, switches)
        assert len(got["switches"]) == len(want), n
        for k, (mine, theirs) in enumerate(zip(got["switches"], want, strict=True)):
            if theirs.get("made"):
                for j, (a, b) in enumerate(zip(mine["steps"], theirs["steps"], strict=True)):
                    assert a == b, (n, k, switches[k]["name"], switches[k]["raw"], j, a, b)
                    frames += "sha256" in b
            assert mine == theirs, (n, k, switches[k]["name"], mine, theirs)
    return frames


# -- scenarios: what the random switches seldom meet, every time ----------------------------


def _entrance_at_its_turns() -> tuple[Any, ...]:
    """Each kind of entrance drawn at the turns of its timeline (seconds of 3.2) and just
    either side: the old frame closing (its edges in the frame while their light grows), the
    band opening with the logo or the title, the text typing in, the band closing and the new
    screen opening."""
    size = (1920, 480)
    turns = [0.0, 0.1, 0.2, 0.6, 0.8, 0.9, 1.25, 1.3, 1.5, 1.7, 2.3, 2.5, 2.65, 2.85, 3.1, 3.2]
    times = sorted({max(0.0, s + d) / 3.2 for s in turns for d in (-0.001, 0.0, 0.001)})
    switches = []
    kinds = [{"kind": "start", "logo": "game.logo"},
             {"kind": "media", "title": "Song", "logo": "media.cover"}, {"kind": "idle"},
             {"kind": "end", "title": "Elden Ring"}, {"title": "Ünïcödé ß", "text": "straße"},
             {"kind": "media-end", "logo": "app.icon", "title": "x"},
             {"kind": "media", "logo": "app.icon",
              "title": "A track whose title goes on, and on, and on, past the band"},
             {"kind": "media", "logo": "media.cover"}]  # fmt: skip
    for k, params in enumerate(kinds):
        steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 50 + k), _bits(t)] for t in times]
        switches.append({"name": "entrance", "raw": params, "steps": steps})
    return size, {"accent": "#ff8800", "void": "#101010"}, "de", switches


def _entrance_on_another_size() -> tuple[Any, ...]:
    """The music's entrance drawn at one size, then at another: its picture is made anew (and
    the track joined to its text again, as Python does)."""
    one, two = (800, 480), (480, 800)
    params = {"kind": "media", "title": "Track", "logo": "media.cover"}
    steps = [[_frame(one, "RGB", 1), _frame(one, "RGB", 2), _bits(0.4)],
             [_frame(two, "RGB", 3), _frame(two, "RGB", 4), _bits(0.45)],
             [_frame(one, "RGB", 1), _frame(one, "RGB", 2), _bits(0.5)]]  # fmt: skip
    return one, {}, "en", [{"name": "entrance", "raw": params, "steps": steps}]


def _every_one_at_its_ends() -> tuple[Any, ...]:
    """Each transition at its start, its middle, just before its end and at it, on a tall
    frame (and where the line of light is almost at its brightest, on its way up and down); a
    shatter whose old frame changes on the way."""
    size = (480, 1920)
    switches = []
    for k, name in enumerate(NAMES):
        steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 99), _bits(t)]
                 for t in (0.0, 1e-9, 0.125, 0.15, 0.5, 0.7, 0.83, 0.999999, 1.0)]  # fmt: skip
        switches.append({"name": name, "raw": {}, "steps": steps})
    steps = [[_frame(size, "RGB", seed), _frame(size, "RGB", 98), _bits(t)]
             for seed, t in ((1, 0.2), (1, 0.4), (2, 0.5), (1, 0.6))]  # fmt: skip
    switches.append({"name": "shatter", "raw": {}, "steps": steps})
    return size, {}, "en", switches


def _kept_for_another_size() -> tuple[Any, ...]:
    """The handovers that keep a map or a strip of light for the frame's size, drawn at one
    size, then at another, then at the first again: made anew each time it changes."""
    one, two = (800, 480), (480, 800)
    switches = []
    for k, name in enumerate(["wipe", "iris", "blinds", "dissolve", "light"]):
        steps = [[_frame(size, "RGB", k), _frame(size, "RGB", 40 + k), _bits(t)]
                 for size, t in ((one, 0.3), (two, 0.5), (one, 0.7))]  # fmt: skip
        switches.append({"name": name, "raw": {}, "steps": steps})
    return one, {"accent": "#ff00ff"}, "en", switches


def _entrance_on_a_narrow_panel() -> tuple[Any, ...]:
    """An entrance on a panel narrower than its band would be: a wide logo and a long title
    made to fit the room it leaves at the sides."""
    size = (480, 800)
    switches = []
    kinds = [{"kind": "start", "logo": "game.logo"},
             {"title": "A title too long for the band of a narrow panel"},
             {"kind": "media", "logo": "tall.logo", "title": "Tall"}]  # fmt: skip
    for k, params in enumerate(kinds):
        steps = [[_frame(size, "RGB", 7 + k), _frame(size, "RGB", 70 + k), _bits(s / 3.2)]
                 for s in (1.0, 1.4, 1.8, 2.4)]  # fmt: skip
        switches.append({"name": "entrance", "raw": params, "steps": steps})
    return size, {}, "en", switches


def _the_line_almost_at_its_brightest() -> tuple[Any, ...]:
    """The line of light on a wide frame, its core in it, while its light is almost all there
    (from 0.99 to 0.999 of it: dimmed all the same), on its way up and on its way down."""
    size = (1920, 480)
    steps = [[_frame(size, "RGB", 3), _frame(size, "RGB", 4), _bits(t)]
             for t in (0.12, 0.13, 0.825, 0.835)]  # fmt: skip
    return size, {}, "en", [{"name": "light", "raw": {}, "steps": steps}]


SCENARIOS = [_entrance_at_its_turns, _entrance_on_another_size, _every_one_at_its_ends,
             _kept_for_another_size, _entrance_on_a_narrow_panel,
             _the_line_almost_at_its_brightest]  # fmt: skip


def test_transitions_as_python_draws_them(tmp_path: Path) -> None:
    frames = _compare(tmp_path, 0x7AA5, 120, scenarios=SCENARIOS)
    assert frames > 900, frames

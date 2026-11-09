"""rust/render's toasts against render/overlays.py (docs/RUST.md, R4): the ToastLayer led
through the same scripts in both (toasts arriving with their ranks, keys, kinds and hold times,
the clock going on, transitions holding or hiding them, toasts set aside, themes switching),
each step's state and every frame drawn with the toast on show, to the bit: the built-in card
with its stripe, icon and text, fading and sliding in by its anchor, clear of the bezel."""

from __future__ import annotations

import hashlib
import json
import random
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from rust_check import run

from libre_panel import fonts
from libre_panel.plugins.host import Toast
from libre_panel.render.overlays import ToastLayer
from libre_panel.render.renderer import Renderer
from libre_panel.render.scene import Scene, SceneFrame
from libre_panel.theme.model import load_theme

THEME_FORMAT = "libre-panel-theme/1"
ANCHORS = ["top-right", "top-left", "bottom-right", "bottom-left", "top", "bottom"]
# (the panels whose bezel hides a strip, one that hides none, and no panel at all)
MODELS = [None, None, None, "turing-9.2-usb", "jonsbo-zc360-screen", "turing-3.5"]
SIZES = [(480, 320), (320, 480), (1920, 480), (800, 480), (64, 48), (17, 400), (240, 240),
         (1280, 720), (160, 1000)]  # fmt: skip
FONTS = ["", "builtin:Barlow-Bold", "builtin:BarlowCondensed-SemiBold",
         "builtin:JetBrainsMono-Medium", "builtin:LiberationSans-Regular"]  # fmt: skip
# the palette's names of the card's roles: its card, its text, and the levels' accents
ROLE_NAMES = ["card", "panel", "surface", "surf_1", "bg2", "text", "tx_1", "accent", "primary",
              "cpu", "warn", "warning", "crit", "error", "danger", "krit"]  # fmt: skip
KINDS = ["", "", "volume", "music", "agent", "update", "game", "x"]
LEVELS = ["info", "info", "warning", "error", "debug", ""]
ICONS = [None, None, "cpu", "music", "volume", "mute", "weather", "agent", "mic_off", "nope", ""]
TEXTS = ["Volume 40 %", "Now playing: Song – Artist", "日本語のテキスト", "😀 done",
         "مرحبا بالعالم", "two\nlines", "", "  spaced  ", "x" * 130, "W" * 60, "a" * 120,
         "a" * 121, "Grüße aus Köln", "été 42", "Update ready — restart to apply the "
         "new version of everything you have installed today", "ok", "jump", "ʃʃ ok",
         "just now: 3 updates", "i" * 120, "i" * 121, "i" * 130, "i" * 119]  # fmt: skip
# what a payload's colour may be: a colour of the theme or Pillow's, numbers, or nothing to read
COLOURS: list[Any] = [None, None, "#ff0000", "#12345678", "@accent", "@nothere", "@cover",
                      "red", "rgb(1,2,3)", "nonsense", "", [255, 0, 0], [300, -5, 12.7],
                      ["12", " 34 ", "x"], ["12", " 34 ", "56"], [True, False, 1],
                      [1.5e300, 0, 0], [float("nan"), 0, 0], [1, 2], 5, ["1_0", "٣", "+7"],
                      [None, 1, 2], [10**30, 3, -(10**30)], [-0.5, 255.9, 128]]  # fmt: skip


def _bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def _float(v: Any) -> float:
    return struct.unpack(">d", bytes.fromhex(v["f"]))[0] if isinstance(v, dict) else float(v)


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


def _colour_of(rng: random.Random) -> str:
    pick = rng.random()
    if pick < 0.5:
        return "#" + "".join(f"{rng.randrange(256):02x}" for _ in range(3))
    if pick < 0.7:
        return "#" + "".join(f"{rng.randrange(256):02x}" for _ in range(4))
    return rng.choice(["#abc", "#abcd", "red", "rgb(10, 200, 30)", "lightgoldenrodyellow",
                       "hsl(120, 50%, 50%)", "#FFFFFF", "black"])  # fmt: skip


def _theme(rng: random.Random, folder: Path, n: int) -> dict[str, Any]:
    """A theme with toast settings (or none), a palette of the card's roles, a font, a panel
    model; some show the volume, what plays or an agent themselves (overlays.SHOWN_BY)."""
    folder.mkdir()
    display: dict[str, Any] = {}
    model = rng.choice(MODELS)
    if model is None:
        display["width"], display["height"] = rng.choice(SIZES)
    else:
        display["model"] = model
        display["orientation"] = rng.choice(["landscape", "portrait"])
    palette = {name: _colour_of(rng) for name in rng.sample(ROLE_NAMES, rng.randrange(0, 7))}
    toast: dict[str, Any] = {}
    if rng.random() < 0.6:
        toast["anchor"] = rng.choice(ANCHORS)
    if rng.random() < 0.5:
        toast["seconds"] = rng.choice([0.5, 1, 2.5, 4, 60, 3.25, 0.75])
    if rng.random() < 0.4:
        toast["queue"] = rng.random() < 0.5
    if rng.random() < 0.3:
        toast["off"] = rng.sample(KINDS[2:], rng.randrange(1, 3))
    if rng.random() < 0.12:
        toast["style"] = rng.choice(["card", "nope", "band", ""])
    widgets = []
    for i in range(rng.choice([0, 0, 1, 2])):
        widget = {"type": "text", "id": f"w{i}", "x": 4, "y": 4, "text": "{value}",
                  "sensor": rng.choice(["audio.volume", "media.title", "ai.claude.state",
                                        "ai.codex.state", "cpu.load", ""])}  # fmt: skip
        if rng.random() < 0.3:
            widget["needs"] = rng.choice(["!audio.volume", "media.title=x, cpu.load",
                                          "@media.title", "switch.frames",
                                          "ai.codex.state>1"])  # fmt: skip
        widgets.append(widget)
    data: dict[str, Any] = {"format": THEME_FORMAT, "name": f"toasts {n}", "display": display,
                            "palette": palette, "widgets": widgets}  # fmt: skip
    if toast or rng.random() < 0.2:
        data["toast"] = toast
    font = rng.choice(FONTS)
    if font:
        data["font"] = font
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def _toast_of(rng: random.Random, n: int) -> dict[str, Any]:
    """A toast as a service sends it, with its number in its payload ("n": what the steps
    name it by, as replace() keeps the payload)."""
    payload: dict[str, Any] = {"n": n}
    colour = rng.choice(COLOURS)
    if colour is not None or rng.random() < 0.1:
        payload["color"] = colour
    kind = rng.choice(KINDS)
    if kind == "volume" and rng.random() < 0.08:  # (the volume's looks are not here yet)
        payload["look"] = rng.choice(["bar", "levels", "ring", "big", "nope", 3])
    seconds = None
    if rng.random() < 0.35:
        seconds = rng.choice([0.0, 0.3, 2.0, 7.5, -1.0, 1e-9, 0.25, float("inf"), float("nan")])
    return {"text": rng.choice(TEXTS), "icon": rng.choice(ICONS), "level": rng.choice(LEVELS),
            "seconds": seconds, "kind": kind, "rank": rng.choice([0, 0, 0, 1, 2, 5, -1, 10**6]),
            "payload": payload, "key": rng.choice(["", "", "", "k1", "k2"])}  # fmt: skip


def _wire(toast: dict[str, Any]) -> dict[str, Any]:
    """A toast as lp-check takes it."""
    seconds = toast["seconds"]
    payload = {key: _typed(value) for key, value in toast["payload"].items()}
    return {**toast, "seconds": None if seconds is None else {"f": _bits(seconds)},
            "payload": payload}  # fmt: skip


def _script(rng: random.Random, themes: list[dict[str, Any]]) -> list[list[Any]]:
    """Steps of the layer's life: toasts arriving, the clock going on in steps fine enough to
    see the fades, frames drawn (of the theme's size, now and then of another), the toast laid
    over a frame the engine composes, set aside, another theme, and what it holds."""
    now = rng.choice([0.0, 1000.0, 12345.678])
    script: list[list[Any]] = [["renderer", 0]]
    size = _size(themes[0])
    n = 0
    for _ in range(rng.randrange(6, 34)):
        pick = rng.random()
        if pick < 0.03:  # (a burst, each with a key of its own: the queue fills to its 20)
            toasts = [{**_toast_of(rng, n + k), "key": f"b{n + k}"} for k in range(25)]
            n += len(toasts)
            script.append(["add", toasts])
        elif pick < 0.25:
            toasts = [_toast_of(rng, n + k) for k in range(rng.choice([1, 1, 1, 2, 3, 22]))]
            n += len(toasts)
            script.append(["add", toasts])
        elif pick < 0.5:
            now += rng.choice([0.0, 0.01, 0.05, 0.09, 0.1, 0.18, 0.2, 0.5, 1.0, 2.0, 4.0, 4.25,
                               4.3, 5.0, 61.0])  # fmt: skip
            script.append(["advance", {"f": _bits(now)}, rng.random() < 0.15])
            script.append(["state", {"f": _bits(now + rng.choice([0.0, 0.03]))}])
        elif pick < 0.75:
            frame_size = size if rng.random() < 0.85 else rng.choice(SIZES)
            spec = {"size": list(frame_size), "mode": rng.choice(["RGB", "RGB", "RGBA"]),
                    "seed": rng.randrange(2**32)}  # fmt: skip
            at = now + rng.choice([0.0, 0.0, 0.04, 0.12, 0.2])
            script.append(["draw", {"f": _bits(at)}, rng.random() < 0.05, spec])
        elif pick < 0.85:
            at = now + rng.choice([0.0, 0.07, 0.3])
            script.append(["laid", {"f": _bits(at)}, rng.random() < 0.05, list(size)])
        elif pick < 0.88:
            script.append(["set_aside"])
        elif pick < 0.96 and len(themes) > 1:
            which = rng.randrange(len(themes))
            size = _size(themes[which])
            script.append(["renderer", which])
        else:
            script.append(["state", {"f": _bits(now)}])
    return script


def _size(theme: dict[str, Any]) -> tuple[int, int]:
    display = theme["display"]
    if "model" not in display:
        return display["width"], display["height"]
    from libre_panel.devices.models import find_model

    model = find_model(display["model"])
    assert model is not None
    return model.size(display["orientation"])


def _noise(spec: dict[str, Any]) -> Image.Image:
    """A frame of noise as lp-check makes it: each byte the top 8 of the low 32 bits of its
    index times 2654435761 plus the seed."""
    (w, h), mode, seed = spec["size"], spec["mode"], spec["seed"]
    index = np.arange(w * h * len(mode), dtype=np.uint64)
    made = (index * np.uint64(2654435761) + np.uint64(seed)) & np.uint64(0xFFFFFFFF)
    return Image.frombytes(mode, (w, h), (made >> np.uint64(24)).astype(np.uint8).tobytes())


def _sha(im: Image.Image) -> str:
    return hashlib.sha256(im.tobytes()).hexdigest()


def _python(folders: list[Path], script: list[list[Any]]) -> list[Any]:
    """Each step's answer from Python's ToastLayer, as lp-check gives Rust's."""
    renderers = [Renderer(load_theme(folder), preview=True) for folder in folders]
    layer: ToastLayer | None = None
    on, steps = 0, []

    def named(toast: Any) -> Any:
        return toast.payload.get("n")

    try:
        for step in script:
            what = step[0]
            out: Any = None
            if what == "renderer":
                on = step[1]
                if layer is None:
                    layer = ToastLayer(renderers[on])
                else:
                    layer.set_renderer(renderers[on])
            elif what == "add":
                assert layer is not None
                layer.add([Toast(t["text"], t["icon"], t["level"], t["seconds"], t["kind"],
                                 t["rank"], dict(t["payload"]), "", t["key"])
                           for t in step[1]])  # fmt: skip
            elif what == "advance":
                assert layer is not None
                layer.advance(_float(step[1]), hold=step[2])
            elif what == "draw":
                assert layer is not None
                frame = _noise(step[3])
                drawn = layer.draw(frame, _float(step[1]), hidden=step[2])
                out = {"sha256": None if drawn is frame else _sha(drawn),
                       "box": list(layer.box) if layer.box else None}  # fmt: skip
            elif what == "laid":
                assert layer is not None
                frame = SceneFrame(Scene(tuple(step[3]), (0, 0, 0), ()))
                laid = layer.draw(frame, _float(step[1]), hidden=step[2])
                picture = None
                if not isinstance(laid, SceneFrame):  # (a style that draws it: the band)
                    picture = {"drawn": True}
                elif laid.scene.pieces:
                    [(_, piece)] = laid.scene.pieces
                    p = piece.layer
                    picture = {"sha256": _sha(p), "size": list(p.size), "mode": p.mode,
                               "x": piece.x, "y": piece.y}  # fmt: skip
                out = {"picture": picture, "box": list(layer.box) if layer.box else None}
            elif what == "set_aside":
                assert layer is not None
                layer.set_aside()
            elif what == "state":
                assert layer is not None
                now = _float(step[1])
                current = None
                if layer.current is not None:
                    toast, began, previous = layer.current
                    current = {"id": named(toast), "began": _bits(began),
                               "seconds": None if toast.seconds is None else _bits(toast.seconds),
                               "previous": None if previous is None
                               else [named(previous[0]), _bits(previous[1])]}  # fmt: skip
                showing = layer.showing(now)
                out = {"current": current, "ends": _bits(layer.ends),
                       "queue": [named(q) for q in layer.queue], "inbox": len(layer.inbox),
                       "active": layer.active, "layered": layer.layered,
                       "showing": None if showing is None
                       else [named(showing[0]), _bits(showing[1])]}  # fmt: skip
            steps.append(out)
    finally:
        if layer is not None:
            layer.close()
        for renderer in renderers:
            renderer.close()
    return steps


def _asks_for_more(themes: list[dict[str, Any]], script: list[list[Any]], at: int) -> bool:
    """Whether step `at` asks for what Rust's toasts do not do yet: the band, or the volume
    in one of its looks."""
    step = script[at]
    if step[0] == "renderer":
        return themes[step[1]].get("toast", {}).get("style") == "band"
    arrived = [t for s in script[:at] if s[0] == "add" for t in s[1]]
    return any(t["kind"] == "volume" and t["payload"].get("look") in ("bar", "levels", "ring",
                                                                          "big")
               for t in arrived)  # fmt: skip


def _compare(tmp_path: Path, seed: int, count: int) -> tuple[int, int]:
    """`count` layers led through scripts, in Rust and in Python: the steps alike up to where
    Rust says what it does not do yet (and only there where it is so). How many frames were
    drawn with a toast on them, and how many layers went to their end."""
    rng = random.Random(seed)
    cases, asked = [], []
    for n in range(count):
        folders, themes = [], []
        for k in range(rng.choice([1, 2, 2, 3])):
            folder = tmp_path / f"t{n}_{k}"
            themes.append(_theme(rng, folder, n))
            folders.append(folder)
        script = _script(rng, themes)
        wired = [[s[0], [_wire(t) for t in s[1]]] if s[0] == "add" else s for s in script]
        cases.append({"themes": [str(f) for f in folders], "script": wired})
        asked.append((folders, themes, script))
    answers = run([{"op": "toasts", "fonts_dir": str(fonts.FONT_DIR), **case} for case in cases])
    drawn = whole = 0
    for n, (got, (folders, themes, script)) in enumerate(zip(answers, asked, strict=True)):
        want = _python(folders, script)
        steps = got["steps"]
        if got["unported"] is not None:
            assert _asks_for_more(themes, script, len(steps)), (n, got["unported"])
        else:
            whole += 1
            assert len(steps) == len(want), n
        for k, (mine, theirs) in enumerate(zip(steps, want, strict=False)):
            assert mine == theirs, (n, k, script[k][0], mine, theirs)
            if script[k][0] in ("draw", "laid") and (theirs.get("sha256") or theirs.get("picture")):
                drawn += 1
    return drawn, whole


def test_toasts_as_python_shows_them(tmp_path: Path) -> None:
    drawn, whole = _compare(tmp_path, 0x7057, 160)
    assert drawn > 400 and whole > 125, (drawn, whole)  # (434 and 138 when written)

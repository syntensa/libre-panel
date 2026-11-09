"""rust/render's toasts against render/overlays.py (docs/RUST.md, R4): the ToastLayer led
through the same scripts in both (toasts arriving with their ranks, keys, kinds and hold times,
the clock going on, transitions holding or hiding them, toasts set aside, themes switching),
each step's state and every frame drawn with the toast on show, to the bit: the built-in card
with its stripe, icon and text, fading and sliding in by its anchor, clear of the bezel; and
the band (render/band.py), rolling in from its edge of light, with its title, tile, pictures
and bar, changing in place and rolling down over the one before."""

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

from libre_panel import fonts
from libre_panel.plugins.host import Toast
from libre_panel.render.overlays import ToastLayer
from libre_panel.render.renderer import Renderer
from libre_panel.render.scene import Scene, SceneFrame
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import load_theme

THEME_FORMAT = "libre-panel-theme/1"
ANCHORS = ["top-right", "top-left", "bottom-right", "bottom-left", "top", "bottom"]
# (the panels whose bezel hides a strip, one that hides none, and no panel at all)
MODELS = [None, None, None, "turing-9.2-usb", "jonsbo-zc360-screen", "turing-3.5"]
SIZES = [(480, 320), (320, 480), (1920, 480), (800, 480), (64, 48), (17, 400), (240, 240),
         (1280, 720), (160, 1000)]  # fmt: skip
# a band's bar beside its text: on the 9.2" bar, and where it just goes beside the text or
# under it (with a tile and without), and on a panel so flat that its width cuts the steps
WIDE = [(1920, 480), (1920, 515), (1920, 480), (864, 480), (1000, 480), (190, 80)]
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


def _theme(rng: random.Random, folder: Path, n: int, band: bool = False) -> dict[str, Any]:
    """A theme with toast settings (or none), a palette of the card's roles, a font, a panel
    model; some show the volume, what plays or an agent themselves (overlays.SHOWN_BY). With
    `band`, every one in the band."""
    folder.mkdir()
    display: dict[str, Any] = {}
    model = rng.choice(MODELS)
    if band and rng.random() < 0.4:  # (wide enough for a bar beside the text: its segments)
        model = rng.choice([None, "turing-9.2-usb"])
        display["orientation"] = "landscape"
        if not model:
            display["width"], display["height"] = rng.choice(WIDE)
    if model is None:
        if "width" not in display:
            display["width"], display["height"] = rng.choice(SIZES)
    else:
        display["model"] = model
        display.setdefault("orientation", rng.choice(["landscape", "portrait"]))
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
    pick = 0.0 if band else rng.random()
    if pick < 0.4:
        toast["style"] = "band"
        if rng.random() < 0.4:  # (its own times: 0 to 10 s, a negative one its default)
            keys = rng.sample(["roll_in", "roll_out"], rng.randrange(1, 3))
            toast["options"] = {key: rng.choice([0, 0.2, 0.45, 1, 2.5, 12, -1, 0.05])
                                for key in keys}  # fmt: skip
            if rng.random() < 0.05:  # (one float() cannot take: the card, as the band fails)
                toast["options"]["roll_in"] = 10**400
    elif pick < 0.5:
        toast["style"] = rng.choice(["card", "nope", ""])
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


def _toast_of(rng: random.Random, n: int, band: bool = False) -> dict[str, Any]:
    """A toast as a service sends it, with its number in its payload ("n": what the steps
    name it by, as replace() keeps the payload). With `band`, more often what the band draws
    besides the text, and often the volume turning (one key again and again: in place)."""
    payload: dict[str, Any] = {"n": n}
    colour = rng.choice(COLOURS)
    if colour is not None or rng.random() < 0.1:
        payload["color"] = colour
    kind = rng.choice(KINDS)
    if kind == "volume" and rng.random() < 0.08:  # (the volume's looks are not here yet)
        payload["look"] = rng.choice(["bar", "levels", "ring", "big", "nope", 3])
    # what the band draws besides the text: a title, a bar, a picture and the badge on it
    for key, choices in (("label", ["Now playing", "Volume", "ß straße", 5, "x" * 80, "", 0,
                                     [1, 2], True, "Ärger über ÖL"]),
                         ("progress", [0.0, 0.37, 1.0, 1.5, -0.2, "0.6", " 0.25 ", "nan", "abc",
                                       True, float("nan"), float("inf"), 0.999, 10**400]),
                         ("image", ["media.cover", "media.cover", "missing", "", 5]),
                         ("badge", ["app.icon", "media.cover", "missing"])):  # fmt: skip
        if rng.random() < (BAND_SHARES[key] if band else 0.35):
            payload[key] = rng.choice(choices)
    if band and rng.random() < 0.4:  # (the volume while it turns: in place, its bar gliding)
        payload["progress"] = round(rng.random(), 2)
        return {**_turned(n, payload["progress"]), "payload": payload}
    if band and rng.random() < 0.15:  # (what plays: its cover, the player's icon on its corner)
        payload.update({"label": "Now playing", "image": "media.cover", "badge": "app.icon"})
        return {"text": "Song – Artist", "icon": "music", "level": "info", "seconds": None,
                "kind": "music", "rank": 0, "payload": payload, "key": ""}  # fmt: skip
    seconds = None
    if rng.random() < 0.35:
        seconds = rng.choice([0.0, 0.3, 2.0, 7.5, -1.0, 1e-9, 0.25, float("inf"), float("nan")])
    return {"text": rng.choice(TEXTS), "icon": rng.choice(ICONS), "level": rng.choice(LEVELS),
            "seconds": seconds, "kind": kind, "rank": rng.choice([0, 0, 0, 1, 2, 5, -1, 10**6]),
            "payload": payload, "key": rng.choice(["", "", "", "k1", "k2"])}  # fmt: skip


def _turned(n: int, share: float) -> dict[str, Any]:
    """The volume as it turns, `share` of the way up: one key again and again (in place)."""
    return {"text": f"Volume {round(100 * share)} %", "icon": "volume", "level": "info",
            "seconds": None, "kind": "volume", "rank": 1, "payload": {"n": n, "progress": share},
            "key": ""}  # fmt: skip


# how often a band's toast carries each of what the band draws besides the text
BAND_SHARES = {"label": 0.5, "progress": 0.7, "image": 0.4, "badge": 0.35}
# how far the clock goes on in a step: fine about a card's fades, through a band's rolling
STEPS = [0.0, 0.01, 0.05, 0.09, 0.1, 0.18, 0.2, 0.5, 1.0, 2.0, 4.0, 4.25, 4.3, 5.0, 61.0]
BAND_STEPS = [0.02, 0.05, 0.08, 0.12, 0.2, 0.26, 0.3, 0.5, 2.0, 4.5]


def _wire(toast: dict[str, Any]) -> dict[str, Any]:
    """A toast as lp-check takes it."""
    seconds = toast["seconds"]
    payload = {key: _typed(value) for key, value in toast["payload"].items()}
    return {**toast, "seconds": None if seconds is None else {"f": _bits(seconds)},
            "payload": payload}  # fmt: skip


def _script(
    rng: random.Random, themes: list[dict[str, Any]], band: bool = False
) -> list[list[Any]]:
    """Steps of the layer's life: toasts arriving, the clock going on in steps fine enough to
    see the fades, frames drawn (of the theme's size, now and then of another), the toast laid
    over a frame the engine composes, set aside, another theme, and what it holds. With `band`,
    in steps through its rolling in and out, and drawn more often."""
    now = rng.choice([0.0, 1000.0, 12345.678])
    script: list[list[Any]] = [["renderer", 0]]
    size = _size(themes[0])
    n = 0
    for _ in range(rng.randrange(6, 34)):
        if band and rng.random() < 0.05:  # (the volume turning, each in place: its bar glides
            # on from where it stood, drawn out of turn too, a frame and then one before it)
            for _ in range(rng.randrange(2, 4)):
                script.append(["add", [_turned(n, round(rng.random(), 2))]])
                n += 1
                now += rng.choice([0.0, 0.02, 0.05])
                script.append(["advance", {"f": _bits(now)}, False])
                for at in rng.choice([[0.05, 0.02], [0.0, 0.09, 0.03], [0.12], [0.03, 0.0]]):
                    spec = {"size": list(size), "mode": "RGB", "seed": rng.randrange(2**32)}
                    script.append(["draw", {"f": _bits(now + at)}, False, spec])
            continue
        pick = rng.random()
        if pick < 0.03:  # (a burst, each with a key of its own: the queue fills to its 20)
            toasts = [{**_toast_of(rng, n + k), "key": f"b{n + k}"} for k in range(25)]
            n += len(toasts)
            script.append(["add", toasts])
        elif pick < 0.25:
            counts = [1, 1, 1, 2] if band else [1, 1, 1, 2, 3, 22]
            toasts = [_toast_of(rng, n + k, band) for k in range(rng.choice(counts))]
            n += len(toasts)
            script.append(["add", toasts])
        elif pick < 0.5:
            now += rng.choice(BAND_STEPS if band else STEPS)
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


# the pictures a band's message may name: a cover not square, an app's icon with its alpha
PICTURES = {"media.cover": {"size": [120, 90], "mode": "RGB", "seed": 7},
            "app.icon": {"size": [48, 48], "mode": "RGBA", "seed": 11}}  # fmt: skip


def _python(folders: list[Path], script: list[list[Any]]) -> list[Any]:
    """Each step's answer from Python's ToastLayer, as lp-check gives Rust's."""
    renderers = [Renderer(load_theme(folder), preview=True) for folder in folders]
    for renderer in renderers:  # (the readings it last drew of: their pictures)
        renderer.snapshot = Snapshot(images={k: _noise(v) for k, v in PICTURES.items()})
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
                    picture = {"sha256": _sha(laid)}
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
    """Whether step `at` asks for what Rust's toasts do not do yet: the volume in one of its
    looks."""
    arrived = [t for s in script[:at] if s[0] == "add" for t in s[1]]
    return any(t["kind"] == "volume" and t["payload"].get("look") in ("bar", "levels", "ring",
                                                                          "big")
               for t in arrived)  # fmt: skip


def _compare(
    tmp_path: Path, seed: int, count: int, band: bool = False, scenarios: Any = ()
) -> tuple[int, int]:
    """`count` layers led through scripts, in Rust and in Python, and then `scenarios`' own:
    the steps alike up to where Rust says what it does not do yet (and only there where it is
    so). How many frames were drawn with a toast on them, and how many layers went to their
    end. With `band`, all in the band."""
    rng = random.Random(seed)
    cases, asked = [], []
    for n in range(count):
        folders, themes = [], []
        for k in range(rng.choice([1, 2, 2, 3])):
            folder = tmp_path / f"t{n}_{k}"
            themes.append(_theme(rng, folder, n, band))
            folders.append(folder)
        script = _script(rng, themes, band)
        wired = [[s[0], [_wire(t) for t in s[1]]] if s[0] == "add" else s for s in script]
        cases.append({"themes": [str(f) for f in folders], "script": wired})
        asked.append((folders, themes, script))
    for n, scenario in enumerate(scenarios):
        themes, script = scenario()
        folders = []
        for k, data in enumerate(themes):
            folder = tmp_path / f"s{n}_{k}"
            folder.mkdir()
            (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
            folders.append(folder)
        wired = [[s[0], [_wire(t) for t in s[1]]] if s[0] == "add" else s for s in script]
        cases.append({"themes": [str(f) for f in folders], "script": wired})
        asked.append((folders, themes, script))
    pictures = {key: image(_noise(spec)) for key, spec in PICTURES.items()}
    answers = run([{"op": "toasts", "fonts_dir": str(fonts.FONT_DIR), "images": pictures, **case}
                   for case in cases])  # fmt: skip
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


# -- scenarios: what the scripts by chance seldom meet, every time ---------------------------


def _plain(size: tuple[int, int], toast: dict[str, Any], **more: Any) -> dict[str, Any]:
    """A theme of `size` and no widgets, with the toast settings `toast`."""
    return {"format": THEME_FORMAT, "name": "scenario", "palette": {}, "widgets": [],
            "display": {"width": size[0], "height": size[1]}, "toast": toast, **more}  # fmt: skip


def _toast(n: int, text: str, **fields: Any) -> dict[str, Any]:
    """A toast of a scenario, `n` its number."""
    toast = {"text": text, "icon": None, "level": "info", "seconds": 5.0, "kind": "", "rank": 0,
             "key": "", **fields}  # fmt: skip
    toast["payload"] = {"n": n, **fields.get("payload", {})}
    return toast


def _at(seconds: float) -> dict[str, str]:
    return {"f": _bits(seconds)}


def _drawn_at(seconds: float, size: tuple[int, int], seed: int = 1) -> list[Any]:
    return ["draw", _at(seconds), False, {"size": list(size), "mode": "RGB", "seed": seed}]


def _cut_on_a_wide_card() -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """A text of more than 120 narrow letters, cut to 120 with "…": room enough for them all
    on the 9.2" bar."""
    size = (1920, 480)
    return [_plain(size, {})], [
        ["renderer", 0], ["add", [_toast(0, "i" * 121)]], ["advance", _at(0.0), False],
        _drawn_at(0.5, size), ["laid", _at(0.5), False, list(size)],
        ["add", [_toast(1, "i" * 130, key="b", rank=3)]], ["advance", _at(0.6), False],
        _drawn_at(1.2, size, 2),
    ]  # fmt: skip


def _a_card_in_another_theme() -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """The same card style at the same size in a theme of other colours: the toast on show is
    drawn anew in them."""
    size = (800, 480)
    one = _plain(size, {"anchor": "top"}, palette={"card": "#203040", "text": "#ffffff"})
    two = _plain(size, {"anchor": "top"}, palette={"card": "#402010", "text": "#00ff00"})
    return [one, two], [
        ["renderer", 0], ["add", [_toast(0, "Update ready")]], ["advance", _at(0.0), False],
        _drawn_at(0.5, size), ["renderer", 1], _drawn_at(0.6, size),
        ["laid", _at(0.7), False, list(size)],
    ]  # fmt: skip


def _a_band_in_another_place() -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """The same band at the same size in a theme that puts it at the bottom: the band goes
    on there."""
    size = (1920, 480)
    one, two = _plain(size, {"style": "band"}), _plain(size, {"style": "band", "anchor": "bottom"})
    return [one, two], [
        ["renderer", 0], ["add", [_toast(0, "Now playing")]], ["advance", _at(0.0), False],
        _drawn_at(1.0, size), ["renderer", 1], _drawn_at(1.1, size),
        ["laid", _at(1.2), False, list(size)],
    ]  # fmt: skip


def _a_change_at_its_end() -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """Another toast rolling down inside the open band, drawn as it rolls and at the very end
    of its change (BandStyle.CHANGE_S after it began, to the bit: 0.125 + 0.26 - 0.125 is 0.26),
    when the band before is gone."""
    size, began = (1920, 480), 0.125
    return [_plain(size, {"style": "band"})], [
        ["renderer", 0], ["add", [_toast(0, "first", key="a")]], ["advance", _at(0.0), False],
        _drawn_at(0.06, size),
        ["add", [_toast(1, "second", key="b", level="error", rank=5)]],
        ["advance", _at(began), False], _drawn_at(began + 0.13, size, 2),
        _drawn_at(began + 0.26, size, 3), _drawn_at(began + 0.3, size, 4),
    ]  # fmt: skip


def _a_band_gone_at_its_end() -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """The volume turning just as the band of the one before is gone (its roll in, hold time
    and roll out to the bit), the one before shown in the card and then in a band that rolls
    in and out quicker than the card leaves: the new one rolls in afresh, its bar where it is
    to be, not going on from the one before."""
    size, end = (1920, 480), (0.05 + 2.0) + 0.05
    quick = {"style": "band", "options": {"roll_in": 0.05, "roll_out": 0.05}}
    return [_plain(size, {}), _plain(size, quick)], [
        ["renderer", 0],
        ["add", [_toast(0, "Volume 20 %", kind="volume", seconds=2.0, payload={"progress": 0.2})]],
        ["advance", _at(0.0), False], ["renderer", 1], _drawn_at(0.5, size),
        _drawn_at(2.08, size, 2),
        ["add", [_toast(1, "Volume 90 %", kind="volume", seconds=2.0, payload={"progress": 0.9})]],
        ["advance", _at(end), False], _drawn_at(end + 0.03, size, 3),
        _drawn_at(end + 0.1, size, 4),
    ]  # fmt: skip


def _bars_where_they_just_fit() -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """A bar on panels just too narrow for it beside the text (864 px without a tile, 1000 with
    one): under it; and on one so flat that its width cuts the bar's steps (31 segments and
    levels, not 50 and 36)."""
    narrow, tiled, flat = (864, 480), (1000, 480), (190, 80)
    themes = [_plain(narrow, {"style": "band"}), _plain(tiled, {"style": "band"}),
              _plain(flat, {"style": "band"})]  # fmt: skip
    return themes, [
        ["renderer", 0], ["add", [_toast(0, "Volume 40 %", kind="volume",
                                         payload={"progress": 0.4})]],
        ["advance", _at(0.0), False], _drawn_at(1.0, narrow),
        ["renderer", 1], ["add", [_toast(1, "Copying", kind="x", key="t", rank=5,
                                         payload={"progress": 0.6, "image": "media.cover"})]],
        ["advance", _at(1.1), False], _drawn_at(2.0, tiled, 2),
        ["renderer", 2], ["add", [_toast(2, "Copying", kind="x", key="u", rank=6,
                                         payload={"progress": 0.7})]],
        ["advance", _at(2.1), False], _drawn_at(3.0, flat, 3),
        ["add", [_toast(3, "Volume 70 %", kind="volume", rank=7, payload={"progress": 0.7})]],
        ["advance", _at(3.1), False], _drawn_at(4.0, flat, 4),
    ]  # fmt: skip


CARD_SCENARIOS = [_cut_on_a_wide_card, _a_card_in_another_theme]
BAND_SCENARIOS = [_a_band_in_another_place, _a_change_at_its_end, _a_band_gone_at_its_end,
                  _bars_where_they_just_fit]  # fmt: skip


def test_toasts_as_python_shows_them(tmp_path: Path) -> None:
    drawn, whole = _compare(tmp_path, 0x7057, 160, scenarios=CARD_SCENARIOS)
    assert drawn > 290 and whole > 135, (drawn, whole)  # (332 and 151 when written)


def test_bands_as_python_shows_them(tmp_path: Path) -> None:
    drawn, whole = _compare(tmp_path, 0xBA4D, 120, band=True, scenarios=BAND_SCENARIOS)
    assert drawn > 700 and whole > 105, (drawn, whole)  # (797 and 120 when written)

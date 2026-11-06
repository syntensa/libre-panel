"""rust/theme against theme/model.py (docs/RUST.md, R3): the format's tables, every theme that
comes with the app, themes with slips in them (each refused in Python's words, or completed as
Python completes it), every widget type with every kind of value in every field, and theme
folders read as Python reads them (line ends, a BOM, what is not UTF-8, assets that leave the
folder)."""

from __future__ import annotations

import copy
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

from rust_check import run

from libre_panel import config, design, fonts, icons
from libre_panel.devices import models as devices
from libre_panel.render import finish, lit
from libre_panel.render.overlays import BUILT_IN_STYLES
from libre_panel.theme import model, modules

FONTS = str(fonts.FONT_DIR)
THEMES = sorted(p.parent for p in model.builtin_themes_dir().glob("*/theme.json"))


def _answer(call: Any) -> dict[str, Any]:
    """What Python's theme/model.py makes of it, as lp-check answers."""
    try:
        made, warnings = call()
    except Exception as error:  # noqa: BLE001 (every kind Python raises is compared)
        return {"error": str(error), "kind": type(error).__name__}
    return {"dict": json.dumps(made), "warnings": warnings}


def _theme(folder: Path) -> dict[str, Any]:
    def call() -> tuple[Any, list[str]]:
        theme = model.load_theme(folder)
        return theme.to_dict(), theme.warnings

    return _answer(call)


def _parsed(data: Any, root: Path | None) -> dict[str, Any]:
    def call() -> tuple[Any, list[str]]:
        theme = model.parse_theme(data, root=root)
        return theme.to_dict(), theme.warnings

    return _answer(call)


def test_the_format_s_tables_and_the_panel_models_are_python_s() -> None:
    for name in ("lit", "facet", "acrylic"):
        finish.get(name, "text")  # (the finishes register themselves as they load)
    finishes: dict[str, list[str]] = {}
    for name, widget_type in finish._REGISTERED:
        finishes.setdefault(name, []).append(widget_type)

    def fields(spec: dict[str, tuple[str, Any]]) -> dict[str, Any]:
        return {k: [kind, default] for k, (kind, default) in spec.items()}

    python = {
        "COMMON": fields(model._COMMON),
        "WIDGET_SPECS": {t: fields(s) for t, s in model.WIDGET_SPECS.items()},
        "LOOK_SPECS": {t: fields(s) for t, s in model.LOOK_SPECS.items()},
        "TOAST_STYLES": {k: fields(c.options) for k, c in BUILT_IN_STYLES.items()},
        "PRIMARY_FIELDS": {k: list(v) for k, v in model.PRIMARY_FIELDS.items()},
        "FINISH_TYPES": finishes,
        "LOOK_RANGES": [[a, b, low, high] for (a, b), (low, high) in model.LOOK_RANGES.items()],
        "STYLE_DEFAULTS": design.STYLE_DEFAULTS,
        "TOAST_DEFAULTS": model.TOAST_DEFAULTS,
        "GRID_DEFAULTS": modules.GRID_DEFAULTS,
        "FINISHES": list(model.FINISHES),
        "EFFECT_FIELDS": list(model.EFFECT_FIELDS),
        "TOAST_ANCHORS": list(model.TOAST_ANCHORS),
        "GONE_LOOK": sorted(model.GONE_LOOK),
        "CARDS": list(modules.CARDS),
        "BACKDROPS": list(modules.BACKDROPS),
        "MODULE_KINDS": list(modules.MODULE_KINDS),
        "MODULE_SOURCES": list(modules.MODULE_SOURCES),
        "MODULE_FALLBACKS": list(modules.MODULE_FALLBACKS),
        "SYMBOL_PACKS": list(config.SYMBOL_PACKS),
        "FAN_LOOKS": list(config.FAN_LOOKS),
        "FAN_PAINTS": list(config.FAN_PAINTS),
        "ICON_NAMES": list(icons.ICON_NAMES),
        "WEATHER_ICONS": list(lit.WEATHER_ICONS),
        "PART_TYPES": list(finish.PART_TYPES),
        "GONE_FIELDS": {k: sorted(v) for k, v in model.GONE_FIELDS.items()},
        "THEME_FORMAT": model.THEME_FORMAT,
        "THEME_FILENAME": model.THEME_FILENAME,
        "BLUR_MAX": model.BLUR_MAX,
        "MAX_SWITCHES": model.MAX_SWITCHES,
        "MAX_DETACHED": model.MAX_DETACHED,
        "CARD_OPACITY_MIN": model.CARD_OPACITY_MIN,
        "COVER_COLOR": model.COVER_COLOR,
        "AUTO": model.AUTO,
        "DEFAULT_THEME": config.DEFAULT_THEME,
        "BUILTIN_PREFIX": fonts.BUILTIN_PREFIX,
        "DEFAULT_FONT": fonts.DEFAULT_FONT,
        "GRID_CELLS": modules.GRID_CELLS,
        "MODELS": [m.to_dict() for m in devices.MODELS],
    }
    [ours] = run([{"op": "theme_tables"}])
    rust = json.loads(ours["tables"])
    assert set(rust) == set(python)
    for key, value in python.items():
        assert json.dumps(rust[key]) == json.dumps(value), key


def test_every_theme_that_comes_with_the_app_loads_as_python_loads_it() -> None:
    answers = run([{"op": "theme", "folder": str(f), "fonts_dir": FONTS} for f in THEMES])
    assert len(THEMES) > 50
    for folder, ours in zip(THEMES, answers, strict=True):
        assert ours == _theme(folder), folder.name


# values that are wrong in some field or other, and right in others: types, the edges of the
# ranges, NaN and the infinities, ints beyond a float, colours, palette names, fonts, assets
_ODD: list[Any] = [
    None, True, False, 0, 1, -1, 2, 15, 16, 100, 4096, 4097, 5000, 5001, 0.5, 0.39, 0.4, 1.0, 1.5,
    60, 61, 64, 65, 360, 361, -361, 1e300, -1e300, 10**400, -(10**400), 2**63, float("nan"),
    float("inf"), float("-inf"), -0.0, "", "x", "#fff", "#ffffff80", "#12", "red", "rgb(1,2,3)",
    "hsl(0, 100%, 50%)", "@accent", "@nope", "@cover", "@", "builtin:Nope", "builtin:Barlow-Bold",
    "builtin:", [], [1, 2], [1, 2, 3, 4], [0, 0, 10, 10], [0, 0, 0, 5], [0, 0, float("nan"), 1],
    [0, 0, float("inf"), 1], [True, 1, 1, 1], ["a"], {}, {"a": 1}, {"above": 50, "color": "#f00"},
    [{"above": 50, "color": "#f00"}, {"above": "x", "color": "#f00"}], [{"above": 1}],
    "../x.png", "/abs.png", "C:x.png", "\\x.png", "img.png", "fonts/none.ttf", "landscape",
    "portrait", "vertical", "horizontal", "auto", "flat", "lit", "facet", "acrylic", "glass",
    "none", "top", "bottom", "music", "card", "band", "x.y", "cpu", "weather", "sun", "Label",
    "ok\n", "a" * 40, "Ab", "a b", "-a",
]  # fmt: skip
_KEYS = [
    "label", "on", "anchor", "seconds", "queue", "off", "style", "options", "name", "spacing",
    "major", "color", "major_color", "alpha", "x", "type", "module", "roll_in", "roll_out", "zz",
    "columns", "rows", "gap", "margin",
]  # fmt: skip
_TOP = [
    "format", "display", "palette", "font", "animation", "background", "refresh_ms", "widgets",
    "grid", "style", "screen", "toast", "look", "switches", "detached", "name", "author",
    "license", "description", "collection", "zz",
]  # fmt: skip


def _odd(rng: random.Random) -> Any:
    return copy.deepcopy(rng.choice(_ODD))


def _some_dict(rng: random.Random, depth: int = 0) -> dict[str, Any]:
    keys = rng.sample(_KEYS, rng.randrange(4))
    return {
        k: (_odd(rng) if depth > 1 or rng.random() < 0.7 else _some_dict(rng, depth + 1))
        for k in keys
    }


def _block(data: dict[str, Any], key: str) -> dict[str, Any]:
    block = data.get(key) if isinstance(data.get(key), dict) else {}
    data[key] = block
    return block


def _mutate(rng: random.Random, data: dict[str, Any]) -> None:
    """One slip somewhere in a theme's data."""
    kind = rng.randrange(14)
    widgets = data.get("widgets") if isinstance(data.get("widgets"), list) else []
    if kind == 0:
        key = rng.choice(_TOP)
        if rng.random() < 0.2:
            data.pop(key, None)
        else:
            data[key] = _odd(rng) if rng.random() < 0.6 else _some_dict(rng)
    elif kind == 1:
        models = ["custom", "turing-5", "turzx-8.8", "nope", 480, 800, 1920, 320, 15]
        value = _odd(rng) if rng.random() < 0.5 else rng.choice(models)
        _block(data, "display")[rng.choice(["model", "orientation", "width", "height"])] = value
    elif kind == 2:
        names = ["accent", "new", "Ab", "a" * 33, "ok\n", "x y", "-a", "a_b-9"]
        _block(data, "palette")[rng.choice(names)] = _odd(rng)
    elif kind in (3, 4, 5, 6) and widgets:
        widget = rng.choice(widgets)
        if not isinstance(widget, dict):
            return
        wtype = widget.get("type")
        own = model.WIDGET_SPECS.get(wtype, {}) if isinstance(wtype, str) else {}
        key = rng.choice([*model._COMMON, *own, "zz", "ridge", "type"])
        if rng.random() < 0.15:
            widget.pop(key, None)
        else:
            widget[key] = _odd(rng)
    elif kind == 7 and widgets:
        widget = rng.choice(widgets)
        if isinstance(widget, dict):
            widget["type"] = rng.choice(
                [*model.WIDGET_SPECS, "nope", "plug.widget", None, 3, ["t"]]
            )
    elif kind == 8 and widgets:
        first = widgets[0].get("id", "") if isinstance(widgets[0], dict) else ""
        plugin = {
            "type": "a.b",
            "font": rng.choice([3, [1], "builtin:Nope", "", "x.ttf"]),
            "src": rng.choice([3, "@x", "", "y.png"]),
            "finish": _odd(rng),
        }
        some = {"type": rng.choice(list(model.WIDGET_SPECS)), "id": rng.choice(["", "x", first])}
        widgets[rng.randrange(len(widgets))] = rng.choice([_odd(rng), some, plugin])
    elif kind == 9:
        styles = ["band", "card", "x.y", "", 3]
        options = [{}, {"roll_in": _odd(rng), "zz": 1}, _odd(rng)]
        data["toast"] = rng.choice(
            [
                _odd(rng),
                {"anchor": _odd(rng), "seconds": _odd(rng)},
                {"style": rng.choice(styles), "options": rng.choice(options)},
                {"off": rng.choice([["music"], [1], "music", []])},
            ]
        )
    elif kind == 10:
        grid = [True, False, None, {}, {"spacing": _odd(rng), "alpha": _odd(rng), "zz": 1}, "x"]
        data["look"] = rng.choice(
            [_odd(rng), {"grid": rng.choice(grid)}, {"dust": 1, "nope": {"a": 1}}, _some_dict(rng)]
        )
    elif kind == 11:
        names = ["frames", "Ab", "ok\n", "a" * 33]
        switches = {
            (rng.choice(names) if rng.random() < 0.5 else f"s{i}"): rng.choice(
                [{"label": _odd(rng), "on": _odd(rng)}, _odd(rng), {}]
            )
            for i in range(rng.choice([0, 1, 2, 9]))
        }
        data["switches"] = rng.choice([_odd(rng), switches])
    elif kind == 12:
        kinds = [{"type": "module", "module": rng.choice(["ring", "nope", 3]), "col": _odd(rng)}]
        modules_ = {f"m{i}": rng.choice([*kinds, {"type": "rect"}, _odd(rng)]) for i in range(3)}
        data["detached"] = rng.choice([_odd(rng), modules_])
    else:
        part = rng.choice(["style", "grid", "background"])
        keys = {
            "style": ["card", "radius", "glow", "backdrop", "display_font", "text_font", "zz"],
            "grid": ["columns", "rows", "gap", "margin", "zz"],
            "background": ["color", "image", "gradient", "gradient_direction", "zz"],
        }[part]
        values = [_odd(rng), ["#000", "#fff"], ["#000", "@accent"], ["#000"], 97, 96, 200, 201]
        _block(data, part)[rng.choice(keys + ["card_opacity"])] = rng.choice(values)


def test_themes_with_slips_in_them_are_refused_or_completed_as_python_does() -> None:
    rng = random.Random(0x7E3E)
    originals = [(f, json.loads((f / "theme.json").read_text(encoding="utf-8"))) for f in THEMES]
    cases, wants = [], []
    for _ in range(3000):
        folder, data = rng.choice(originals)
        data = copy.deepcopy(data)
        for _ in range(rng.choice([1, 1, 2, 3])):
            _mutate(rng, data)
        text = json.dumps(data)
        root = folder if rng.random() < 0.8 else None
        wants.append(_parsed(json.loads(text), root))
        case = {"op": "parse_theme", "data": text, "root": str(root) if root else None}
        cases.append({**case, "fonts_dir": FONTS})
    kinds: dict[str, int] = {}
    for case, want, ours in zip(cases, wants, run(cases), strict=True):
        kinds[want.get("kind", "ok")] = kinds.get(want.get("kind", "ok"), 0) + 1
        assert ours == want, case["data"][:300]
    # what is refused, what loads, and what Python itself raises on the way: every way out
    assert kinds["ThemeError"] > 1000 and kinds["ok"] > 300
    assert {"AttributeError", "OverflowError"} <= set(kinds)


def _values_for(kind: str, rng: random.Random) -> list[Any]:
    """Values for a field of `kind`: the ones it takes, at their edges, and others."""
    base = kind.rstrip("?")
    if base.startswith("enum:"):
        return [*base[5:].split("|"), "nope", 1, None]
    takes = {
        "int": [0, 1, -1, 64, 65, 1.5, -2.7, 10**400, float("nan"), float("inf"), True],
        "number": [0, 1, 0.5, -0.5, 1.0000001, -360, 360, 360.5, float("nan"), True, "1"],
        "bool": [True, False, 0, 1, "true"],
        "color": ["#fff", "#22d3eeaa", "red", "@accent", "@nope", "@cover", "nope", 1, None],
        "icon": [*rng.sample(icons.ICON_NAMES, 3), "weather", "nope", 1],
        "box": [
            [0, 0, 10, 10],
            [0, 0, 0, 1],
            [0.5, 1.5, 2.5, 3.5],
            [0, 0, float("nan"), 1],
            [0, 0, float("inf"), 1],
            [1, 2, 3],
            [True, 1, 1, 1],
            None,
            "x",
        ],  # fmt: skip
        "rules": [
            [],
            [{"above": 50, "color": "#f00"}],
            [{"above": True, "color": "#f00"}],
            [{"color": "#f00"}],
            [1],
            "x",
            [{"above": 1, "color": "@nope"}],
        ],  # fmt: skip
    }.get(base, ["", "x", "builtin:Barlow-Bold", "builtin:Nope", "a.png", "../a.png", 1, None])
    return [*takes, *rng.sample(_ODD, 3)]


def test_every_field_of_every_widget_type_takes_what_python_takes() -> None:
    rng = random.Random(0xF1E1D)
    palette = {"accent": "#22d3ee", "muted": "#8a97a8"}
    root = THEMES[0]
    cases, wants = [], []
    for wtype, own in model.WIDGET_SPECS.items():
        spec = {**model._COMMON, **own}
        for key, (kind, _default) in spec.items():
            for value in _values_for(kind, rng):
                raw: dict[str, Any] = {"type": wtype, key: value}
                if rng.random() < 0.3:
                    raw["zz"] = 1  # an unknown field: a warning
                if rng.random() < 0.1:
                    raw["ridge"] = 1  # gone from rect: dropped without one
                index = rng.randrange(5)
                text = json.dumps(raw)
                wants.append(
                    _answer(
                        lambda t=text, i=index: model.normalize_widget(
                            json.loads(t), i, palette, root
                        )
                    )
                )
                case = {"op": "widget", "data": text, "index": index, "root": str(root)}
                cases.append({**case, "palette": json.dumps(palette), "fonts_dir": FONTS})
    for case, want, ours in zip(cases, wants, run(cases), strict=True):
        assert ours == want, case["data"]
    assert len(cases) > 2000


def _write(folder: Path, content: bytes | str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "theme.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return folder


def test_theme_folders_are_read_as_python_reads_them(tmp_path: Path) -> None:
    base = {
        "format": model.THEME_FORMAT,
        "display": {"width": 320, "height": 240},
        "widgets": [{"type": "image", "src": "pic.png"}],
    }

    def with_asset(src: str, image: str | None = None) -> str:
        data = copy.deepcopy(base)
        data["widgets"][0]["src"] = src
        if image is not None:
            data["background"] = {"image": image}
        return json.dumps(data, indent=2)

    folders: list[Path] = []
    add = folders.append
    # the JSON's slips, found where Python finds them in its text: line ends made "\n"
    add(_write(tmp_path / "crlf", '{\r\n  "format": "x",\r\n  "a": [1 2]\r\n}'))
    add(_write(tmp_path / "cr", '{\r  "format":\r  }'))
    add(_write(tmp_path / "bom", b"\xef\xbb\xbf{}"))
    add(_write(tmp_path / "start", b'{"name": "\xff"}'))
    add(_write(tmp_path / "continuation", b'{"name": "\xe2\x28\xa1"}'))
    add(_write(tmp_path / "pair", b'{"name": "\xed\xa0\x80"}'))
    add(_write(tmp_path / "end", b'{"name": "\xe2\x82'))
    add(_write(tmp_path / "deep", "[" * 20000 + "]" * 20000))
    add(_write(tmp_path / "digits", '{"refresh_ms": ' + "1" * 5000 + "}"))
    add(tmp_path / "none")  # no folder at all
    (tmp_path / "empty").mkdir()
    add(tmp_path / "empty")
    (tmp_path / "dir" / "theme.json").mkdir(parents=True)
    add(tmp_path / "dir")
    # assets: inside, through "..", outside, absolute, a drive, missing but inside
    inside = tmp_path / "assets"
    (inside / "sub").mkdir(parents=True)
    (inside / "pic.png").write_bytes(b"")
    (tmp_path / "outside.png").write_bytes(b"")
    for name, src in [
        ("ok", "pic.png"),
        ("up", "sub/../pic.png"),
        ("out", "../outside.png"),
        ("deep-out", "sub/../../outside.png"),
        ("abs", str(tmp_path / "outside.png")),
        ("drive", "C:pic.png"),
        ("missing", "sub/missing.png"),
        ("dot", "./sub/./x.png"),
    ]:
        folder = tmp_path / f"asset-{name}"
        for item in inside.iterdir():
            target = folder / item.name
            folder.mkdir(exist_ok=True)
            if item.is_dir():
                target.mkdir(exist_ok=True)
            else:
                target.write_bytes(b"")
        add(_write(folder, with_asset(src, image="sub/bg.png")))
    if os.name != "nt":  # (links need rights of their own on Windows)
        linked = _write(tmp_path / "linked", with_asset("link.png"))
        (linked / "link.png").symlink_to(tmp_path / "outside.png")
        add(linked)
        if sys.version_info[:2] == (3, 12):  # (3.13's resolve() lets a loop of links through)
            looped = _write(tmp_path / "looped", with_asset("loop.png"))
            (looped / "loop.png").symlink_to("loop.png")
            add(looped)
        inner = _write(tmp_path / "inner-link", with_asset("in.png"))
        (inner / "pic.png").write_bytes(b"")
        (inner / "in.png").symlink_to("pic.png")
        add(inner)
    answers = run([{"op": "theme", "folder": str(f), "fonts_dir": FONTS} for f in folders])
    for folder, ours in zip(folders, answers, strict=True):
        assert ours == _theme(folder), folder.name


def test_light_colours_are_worked_out_as_python_works_them_out() -> None:
    from libre_panel.theme import light_colours

    rng = random.Random(0x11647)
    roles = [*light_colours.GROUND_ROLES, *light_colours.INK_ROLES, "accent", "cpu", "x", "", None]
    colours = ["", "@cpu", "nope", "red", "teal", "#000", "#fff", "#0b8", "rgb(1,2,3)", "#0B8C84CC"]
    for _ in range(20000):
        digits = rng.choice([3, 4, 6, 6, 6, 8])
        colours.append("#" + "".join(rng.choice("0123456789abcdefABCDEF") for _ in range(digits)))
    cases = [[c, rng.choice(roles)] for c in colours]
    [ours] = run([{"op": "light_colours", "colours": cases}])
    want = [light_colours.light_colour.__wrapped__(c, role) for c, role in cases]
    assert ours["made"] == want


def _python_versions(names: list[str], sizes: list[Any]) -> list[dict[str, Any]]:
    out = []
    for name in names:
        try:
            found: dict[str, Any] = {"path": str(model.find_theme(name))}
        except model.ThemeError as error:
            found = {"error": str(error)}
        fors = [
            model.theme_for(name, orientation, None if size is None else tuple(size))
            for orientation in ("landscape", "portrait", "")
            for size in sizes
        ]
        out.append(
            {
                "stem": model.version_stem(name),
                "base": model.version_base(name),
                "root": model._root(name),
                "companion": model.is_companion(name),
                "agents": model.is_agents_screen(name),
                "pair": model.pair_version(name),
                "unpaired": model.unpaired_version(name),
                "companion_for": model.companion_for(name),
                "find": found,
                "theme_for": fors,
            }
        )
    return out


def test_versions_places_and_variants_of_themes_as_python_s(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import shutil

    home = tmp_path / "home"
    user = home / "themes"
    user.mkdir(parents=True)
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    builtin = model.builtin_themes_dir()
    # the user's own: versions of a theme of theirs, and one of the app's shadowed
    for name, source in [
        ("mine", "libre-default"),
        ("mine-1920x720", "libre-default"),
        ("mine-vertical", "libre-default"),
        ("mine-pair", "libre-default"),
        ("mine-companion", "libre-default"),
        ("lumen", "lumen"),
        ("not-a-theme", None),
    ]:
        if source is None:
            (user / name).mkdir()
        else:
            shutil.copytree(builtin / source, user / name)
    names = sorted({f.name for f in THEMES} | {p.name for p in user.iterdir()})
    names += ["lumen-1920x720\n", "x-١٢x34", "", "a..b", "agents", "agentsx", "-pair"]
    names += ["lumen-pair-vertical", "arena-pair-1920x720", "mine-640x480", "lumen-companions"]
    sizes = [None, [1920, 720], [1920, 480], [480, 480], [320, 480], [800, 480], [1280, 720]]
    variants = []
    rng = random.Random(0x7A21)
    for folder in THEMES:
        variant: dict[str, Any] = {"folder": str(folder), "light": rng.random() < 0.8}
        if rng.random() < 0.4:
            variant["accent"] = rng.choice(["#ff8800", "#FF8800", "", "teal"])
        if rng.random() < 0.4:
            variant["card_opacity"] = rng.choice([0, 30, 50, 100, -5])
        if rng.random() < 0.3:
            variant["look"] = str(rng.choice(THEMES))
        variants.append(variant)
    py_home, rs_home = tmp_path / "py", tmp_path / "rs"
    for root in (py_home, rs_home):
        shutil.copytree(home, root)
    lumen = json.loads((builtin / "lumen" / "theme.json").read_text(encoding="utf-8"))
    broken = {**lumen, "refresh_ms": 5}
    saves = [
        {"name": "my-lumen", "data": json.dumps(lumen), "source": "lumen"},
        {"name": "my-lumen", "data": json.dumps({**lumen, "name": "Again"}), "source": "lumen"},
        {"name": "from-mine", "data": json.dumps(lumen), "source": "mine"},
        {"name": "bad name", "data": json.dumps(lumen), "source": None},
        {"name": "broken", "data": json.dumps(broken), "source": "lumen"},
        {"name": "plain", "data": json.dumps({**lumen, "widgets": []}), "source": None},
    ]
    case = {"op": "theme_versions", "user": str(user), "builtin": str(builtin), "fonts_dir": FONTS}
    [ours] = run([{**case, "names": names, "sizes": sizes, "variants": variants}])
    assert ours["names"] == _python_versions(names, sizes)
    assert ours["listed"] == [json.dumps(entry) for entry in model.list_themes()]

    def variant_of(w: dict[str, Any]) -> dict[str, Any]:
        def call() -> tuple[Any, list[str]]:
            theme = model.load_theme(Path(w["folder"]))
            if "accent" in w:
                theme = model.with_accent(theme, w["accent"])
            if "card_opacity" in w:
                theme = model.with_card_opacity(theme, w["card_opacity"])
            if "look" in w:
                theme = model.with_look(theme, model.load_theme(Path(w["look"])))
            theme = model.with_light_colours(theme, w["light"])
            return theme.to_dict(), []

        made = _answer(call)
        return {"dict": made["dict"]} if "dict" in made else made

    assert ours["variants"] == [variant_of(w) for w in variants]

    # themes saved into each one's own folder of the user's themes, one after another
    def saved_python() -> list[dict[str, Any]]:
        monkeypatch.setenv("LIBRE_PANEL_HOME", str(py_home))
        answers = []
        for s in saves:
            try:
                path = model.save_theme(s["name"], json.loads(s["data"]), s["source"])
                answers.append({"path": str(path)})
            except Exception as error:  # noqa: BLE001
                answers.append({"error": str(error), "kind": type(error).__name__})
        return answers

    py_saved = saved_python()
    rs_case = {**case, "user": str(rs_home / "themes"), "names": [], "saves": saves}
    [rs] = run([rs_case])

    def same(answer: dict[str, Any], root: Path) -> dict[str, Any]:
        return {k: v.replace(str(root), "HOME") for k, v in answer.items()}

    assert [same(a, rs_home) for a in rs["saved"]] == [same(a, py_home) for a in py_saved]

    def files(root: Path) -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
        }

    assert files(rs_home) == files(py_home)

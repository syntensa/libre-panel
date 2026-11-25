"""rust/theme as the Python app's theme/model.py and theme/adapt.py were (docs/RUST.md, R3): the
format's tables, every theme that comes with the app, themes with slips in them (each refused
in Python's words, or completed as Python completed it), every widget type with every kind of
value in every field, theme folders read as Python read them (line ends, a BOM, what is not
UTF-8, assets that leave the folder), and themes moved to other panels: adapted, laid out on a
size they have no version for, with the grids of what their files hold. What lp-check answers
is checked against what the Python app answered (golden.py); its tables, which the cases are
drawn from, are kept as they were (golden.given)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import re
import struct
from pathlib import Path
from typing import Any

import golden
import pytest
from rust_check import run

from libre_panel import cards, config, fonts
from libre_panel.datadir import DATA
from libre_panel.devices import models as devices

FONTS = str(fonts.FONT_DIR)
BUILTIN = DATA / "themes"  # the themes that come with the app
THEMES = [BUILTIN / name for name in golden.given("theme_names")]
TABLES: dict[str, Any] = golden.given("theme_tables")  # theme/model.py's, as they were
WIDGET_SPECS: dict[str, dict[str, Any]] = TABLES["WIDGET_SPECS"]  # (a field: its kind, default)
COMMON: dict[str, Any] = TABLES["COMMON"]
THEME_FORMAT = "libre-panel-theme/1"
VERTICAL = "-vertical"  # "lumen-vertical" is Lumen laid out for a panel that stands on end
SIZED = re.compile(r"^(.+)-(\d{2,5})x(\d{2,5})$")  # "lumen-1920x720": for a panel of that size
# the palette's roles a colour for a light ground is worked out by (theme/light_colours.py)
GROUND_ROLES = ["base", "base_low", "bg", "bg2", "bg3", "bg_low", "card", "card2", "panel",
                "panel2", "surf_1", "surf_2", "surface", "surface2", "void"]  # fmt: skip
INK_ROLES = ["dim", "edge", "grid", "grid_major", "line", "muted", "rule", "rule_soft",
             "rule_strong", "text", "text2", "text3", "track", "tx_1", "tx_2", "tx_3"]  # fmt: skip


@pytest.mark.kept
def test_the_format_s_tables_and_the_panel_models_are_python_s() -> None:
    # (the tables as theme/model.py and its neighbours held them, kept; those of the modules
    # that stay, as they are)
    python = {
        **TABLES,
        "MODULE_KINDS": list(cards.MODULE_KINDS),
        "SYMBOL_PACKS": list(config.SYMBOL_PACKS),
        "FAN_LOOKS": list(config.FAN_LOOKS),
        "FAN_PAINTS": list(config.FAN_PAINTS),
        "DEFAULT_THEME": config.DEFAULT_THEME,
        "BUILTIN_PREFIX": fonts.BUILTIN_PREFIX,
        "DEFAULT_FONT": fonts.DEFAULT_FONT,
        "MODELS": [m.to_dict() for m in devices.MODELS],
    }
    [ours] = run([{"op": "theme_tables"}])
    rust = json.loads(ours["tables"])
    # (which types, not the order their modules happened to load in: both apps only ask
    # whether a type is among a finish's)
    rust["FINISH_TYPES"] = {n: sorted(t) for n, t in sorted(rust["FINISH_TYPES"].items())}
    assert set(rust) == set(python)
    for key, value in python.items():
        assert json.dumps(rust[key]) == json.dumps(value), key


@pytest.mark.kept
def test_every_theme_that_comes_with_the_app_loads_as_python_loads_it() -> None:
    answers = run([{"op": "theme", "folder": str(f), "fonts_dir": FONTS} for f in THEMES])
    assert len(THEMES) > 50
    for folder, ours in zip(THEMES, answers, strict=True):
        assert "dict" in ours, (folder.name, ours)


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
    kind = rng.randrange(15)
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
        own = WIDGET_SPECS.get(wtype, {}) if isinstance(wtype, str) else {}
        key = rng.choice([*COMMON, *own, "zz", "ridge", "type"])
        if rng.random() < 0.15:
            widget.pop(key, None)
        else:
            widget[key] = _odd(rng)
    elif kind == 7 and widgets:
        widget = rng.choice(widgets)
        if isinstance(widget, dict):
            widget["type"] = rng.choice([*WIDGET_SPECS, "nope", "plug.widget", None, 3, ["t"]])
    elif kind == 8 and widgets:
        first = widgets[0].get("id", "") if isinstance(widgets[0], dict) else ""
        plugin = {
            "type": "a.b",
            "font": rng.choice([3, [1], "builtin:Nope", "", "x.ttf"]),
            "src": rng.choice([3, "@x", "", "y.png"]),
            "finish": _odd(rng),
        }
        some = {"type": rng.choice(list(WIDGET_SPECS)), "id": rng.choice(["", "x", first])}
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
    elif kind == 13:
        shine = [True, False, None, [], ["rings"], ["sun", "rings", "sun"], ["glitter"], "rings",
                 [1], ["music", None], {}]  # fmt: skip
        key = rng.choice(["shine", "shine", "shine", "smoothing_ms", "zz"])
        _block(data, "animation")[key] = rng.choice(shine) if rng.random() < 0.8 else _odd(rng)
    else:
        part = rng.choice(["style", "grid", "background"])
        keys = {
            "style": ["card", "radius", "glow", "backdrop", "display_font", "text_font", "zz"],
            "grid": ["columns", "rows", "gap", "margin", "zz"],
            "background": ["color", "image", "gradient", "gradient_direction", "zz"],
        }[part]
        values = [_odd(rng), ["#000", "#fff"], ["#000", "@accent"], ["#000"], 97, 96, 200, 201]
        key = rng.choice(keys + ["card_opacity", "field"])
        if key == "field" and part == "background":
            values = [{"look": "lumen"}, {}, {"look": "nova"}, {"look": None}, "lumen",
                      {"look": "lumen", "moves": "switch.light_flows", "load": "!switch.x"},
                      {"moves": 5}, {"load": _odd(rng)}, {"look": ["lumen"]},
                      _odd(rng)]  # fmt: skip
        _block(data, part)[key] = rng.choice(values)


@pytest.mark.kept
def test_themes_with_slips_in_them_are_refused_or_completed_as_python_does() -> None:
    rng = random.Random(0x7E3E)
    originals = [(f, json.loads((f / "theme.json").read_text(encoding="utf-8"))) for f in THEMES]
    cases = []
    for _ in range(3000):
        folder, data = rng.choice(originals)
        data = copy.deepcopy(data)
        for _ in range(rng.choice([1, 1, 2, 3])):
            _mutate(rng, data)
        text = json.dumps(data)
        root = folder if rng.random() < 0.8 else None
        case = {"op": "parse_theme", "data": text, "root": str(root) if root else None}
        cases.append({**case, "fonts_dir": FONTS})
    kinds: dict[str, int] = {}
    for ours in run(cases):
        kinds[ours.get("kind", "ok")] = kinds.get(ours.get("kind", "ok"), 0) + 1
    # what is refused, what loads, and what Python itself raised on the way: every way out
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
        "icon": [*rng.sample(TABLES["ICON_NAMES"], 3), "weather", "nope", 1],
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


@pytest.mark.kept
def test_every_field_of_every_widget_type_takes_what_python_takes() -> None:
    rng = random.Random(0xF1E1D)
    palette = {"accent": "#22d3ee", "muted": "#8a97a8"}
    root = THEMES[0]
    cases = []
    for wtype, own in WIDGET_SPECS.items():
        spec = {**COMMON, **own}
        for key, (kind, _default) in spec.items():
            for value in _values_for(kind, rng):
                raw: dict[str, Any] = {"type": wtype, key: value}
                if rng.random() < 0.3:
                    raw["zz"] = 1  # an unknown field: a warning
                if rng.random() < 0.1:
                    raw["ridge"] = 1  # gone from rect: dropped without one
                index = rng.randrange(5)
                text = json.dumps(raw)
                case = {"op": "widget", "data": text, "index": index, "root": str(root)}
                cases.append({**case, "palette": json.dumps(palette), "fonts_dir": FONTS})
    answers = run(cases)
    assert len(cases) > 2000
    taken = sum("dict" in ours for ours in answers)
    assert taken > 500 and len(answers) - taken > 500, taken  # (taken, and refused)


def _write(folder: Path, content: bytes | str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "theme.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return folder


@pytest.mark.kept
def test_theme_folders_are_read_as_python_reads_them(tmp_path: Path) -> None:
    base = {
        "format": THEME_FORMAT,
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
        # (a loop of links: refused as Python 3.12 refused it; 3.13's resolve() let it through)
        looped = _write(tmp_path / "looped", with_asset("loop.png"))
        (looped / "loop.png").symlink_to("loop.png")
        add(looped)
        inner = _write(tmp_path / "inner-link", with_asset("in.png"))
        (inner / "pic.png").write_bytes(b"")
        (inner / "in.png").symlink_to("pic.png")
        add(inner)
    answers = run([{"op": "theme", "folder": str(f), "fonts_dir": FONTS} for f in folders])
    read = {f.name for f, ours in zip(folders, answers, strict=True) if "dict" in ours}
    assert {"asset-ok", "asset-up", "asset-missing", "asset-dot"} <= read
    assert not read & {"crlf", "cr", "start", "deep", "none", "empty", "dir", "asset-out"}


@pytest.mark.kept
def test_light_colours_are_worked_out_as_python_works_them_out() -> None:
    rng = random.Random(0x11647)
    # (a colour's job by its role: the ground's, a line's or a text's, any other, none)
    roles = [*GROUND_ROLES, *INK_ROLES, "accent", "cpu", "x", "", None]
    colours = ["", "@cpu", "nope", "red", "teal", "#000", "#fff", "#0b8", "rgb(1,2,3)", "#0B8C84CC"]
    for _ in range(20000):
        digits = rng.choice([3, 4, 6, 6, 6, 8])
        colours.append("#" + "".join(rng.choice("0123456789abcdefABCDEF") for _ in range(digits)))
    cases = [[c, rng.choice(roles)] for c in colours]
    [ours] = run([{"op": "light_colours", "colours": cases}])
    assert len(ours["made"]) == len(cases)
    assert ours["made"][:3] == ["", "@cpu", "nope"]  # (none, a name, no colour: as they are)


@pytest.mark.kept
def test_versions_places_and_variants_of_themes_as_python_s(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import shutil

    home = tmp_path / "home"
    user = home / "themes"
    user.mkdir(parents=True)
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    builtin = BUILTIN
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
    assert len(ours["names"]) == len(names) and len(ours["variants"]) == len(variants)
    # each panel's default theme (theme/defaults.py), with the user's Lumen in place
    assert len(ours["defaults"]) == 1 + len(devices.MODELS)
    assert len(ours["listed"]) > len(THEMES)  # (the app's and the person's own)

    # themes saved into each one's own folder of the user's themes, one after another: what
    # each answered and every file there after them, as the Python app's were in its own copy
    python = golden.told("saved")
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(py_home))
    rs_case = {**case, "user": str(rs_home / "themes"), "names": [], "saves": saves}
    [rs] = run([rs_case])
    saved = [{k: v.replace(str(rs_home), "HOME") for k, v in a.items()} for a in rs["saved"]]
    assert saved == python["saved"]
    files = {
        p.relative_to(rs_home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(rs_home.rglob("*"))
        if p.is_file()
    }
    assert files == python["files"]


MODELS = ["custom"] + [m.id for m in devices.MODELS]
SIZES = sorted({m.size(way) for m in devices.MODELS for way in ("landscape", "portrait")})


def _setting(rng: random.Random) -> Any:
    """A value of a grid's as a file may hold it."""
    pick = rng.randrange(13)
    if pick < 4:
        return rng.randrange(0, 97)
    choices = [
        [0, -1, -3, -4, -5, -100, 200, 1000],
        [10.5, -4.0, 4.0, 0.0, -0.0, 0.25, 1e308, -1e308, 2.5, 47.9, 96.0],
        [float("nan"), float("inf"), float("-inf")],
        [True, False, "8", "", "a", [], [1], {}, {"a": 1}, None],
        [10**19, 10**300, -(10**300), 2**63, 2**64 + 1],
    ]
    if pick < 9:
        return rng.choice(choices[pick - 4])
    if pick < 11:
        return struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
    return rng.uniform(-50, 300)


@pytest.mark.kept
def test_grids_of_what_files_hold_are_python_s() -> None:
    # make_grid of a version's grid as its file holds it (a margin of 10.5, an int of 300
    # digits, a string): Python's arithmetic on each, its numbers or its error
    rng = random.Random(0x6A1D)
    cases = []
    for _ in range(8000):
        grid: Any = {
            k: _setting(rng)
            for k in ("columns", "rows", "gap", "margin", "other")
            if rng.random() < 0.7
        }
        if rng.random() < 0.05:
            grid = rng.choice([None, [], [1], "x", "", 0, 5, True, 1.5, {}])
        w, h = (
            rng.choice([rng.randrange(16, 4097)] * 6 + [1, 0, -5, 10**20, 2**1030])
            for _ in range(2)
        )
        cases.append(
            {"width": w, "height": h, "grid": grid, "model": rng.choice([*MODELS, "nope"])}
        )
    ask = [
        {**c, "width": str(c["width"]), "height": str(c["height"]), "grid": json.dumps(c["grid"])}
        for c in cases
    ]
    [ours] = run([{"op": "theme_adapt", "fonts_dir": FONTS, "builtin": "", "raw_grids": ask}])
    made = sum(isinstance(grid, list) for grid in ours["raw_grids"])
    assert len(ours["raw_grids"]) == len(cases)
    assert made > 1000 and len(cases) - made > 1000, made  # (its numbers, or Python's error)


@pytest.mark.kept
def test_themes_adapted_to_other_panels_as_python_adapts_them() -> None:
    rng = random.Random(0xADA9)
    datas = [json.loads((f / "theme.json").read_text(encoding="utf-8")) for f in THEMES]
    modular = ['"module"' in json.dumps(d) for d in datas]
    cases = []
    for data, has_modules in zip(datas, modular, strict=True):
        for _ in range(3):
            target = rng.choice([*MODELS, "nope"])
            way = rng.choice(["landscape", "portrait"])
            size = rng.choice([(0, 0), (800, 480), (1920, 720), (320, 480), (480, 480), (-1, 5)])
            if not has_modules and rng.random() < 0.3:
                # the editor's /api/adapt takes any int it is sent (a module free theme: on a
                # grid that large, modules would be placed over a set no computer holds)
                size = (rng.choice([10**30, 2**63, 10**400, 4097, 1]), rng.choice([480, 10**30]))
            mode = rng.choice(["scale", "scale", "keep", "other"])
            cases.append((data, target, way, mode, size))
    ask = [
        {
            "data": json.dumps(d),
            "model": t,
            "orientation": w,
            "mode": m,
            "width": str(s[0]),
            "height": str(s[1]),
        }
        for d, t, w, m, s in cases
    ]
    # the grids modules sit on, for any panel and settings
    grids = []
    for _ in range(3000):
        g = {
            "width": rng.randrange(16, 4097),
            "height": rng.randrange(16, 4097),
            "model": rng.choice(MODELS),
        }
        for key in ("columns", "rows", "gap", "margin"):
            g[key] = rng.choice([0, 0, rng.randrange(1, 97), rng.randrange(1, 201)])
        grids.append(g)
    # Python's set of pairs, as _move_modules goes through it; int() of text
    sets = [
        [(c, r) for c in range(cols, 0, -1) for r in range(rows, 0, -1)]
        for cols, rows in ((rng.randrange(1, 13), rng.randrange(1, 13)) for _ in range(600))
    ]
    sets += [
        [(rng.randrange(100), rng.randrange(100)) for _ in range(rng.randrange(60))]
        for _ in range(200)
    ]
    ints = [
        " 12 ",
        "1_000",
        "_1",
        "1__0",
        "+5",
        "-0",
        "\u0661\u0662",
        "",
        " ",
        "x",
        "1.5",
        "0x10",
        "\u3000 7\n",
        "- 1",
        "007",
        "1_",
        "9" * 50,
        "9" * 4301,
    ]
    case = {"op": "theme_adapt", "fonts_dir": FONTS, "builtin": str(BUILTIN)}
    [ours] = run([{**case, "adapt": ask, "grids": grids, "sets": sets, "ints": ints}])
    adapted = sum("dict" in made for made in ours["adapt"])
    assert len(ours["adapt"]) == len(cases)
    assert adapted > 50 and len(cases) - adapted > 20, adapted  # (adapted, and refused)
    assert len(ours["grids"]) == len(grids)
    # (Python's own: a set of pairs in the order it goes through it, and int() of text)
    assert ours["sets"] == [[list(p) for p in set(s)] for s in sets]

    def python_int(text: str) -> str | None:
        try:
            return str(int(text))
        except ValueError:
            return None

    assert ours["ints"] == [python_int(text) for text in ints]  # (4300 digits at most)


@pytest.mark.kept
def test_themes_laid_out_on_panels_they_have_no_version_for_as_python_lays_them_out() -> None:
    rng = random.Random(0x1A1D)
    cases = []
    for folder in THEMES:
        for size in rng.sample(SIZES, 4):
            fitting = [
                m.id for m in devices.MODELS if size in (m.size("landscape"), m.size("portrait"))
            ]
            cases.append(
                {
                    "folder": str(folder),
                    "size": list(size),
                    "model": rng.choice(["custom", *fitting]),
                }
            )
    case = {"op": "theme_adapt", "fonts_dir": FONTS, "builtin": str(BUILTIN)}
    [ours] = run([{**case, "laid_out": cases}])
    assert len(ours["laid_out"]) == len(cases)
    assert all("dict" in made for made in ours["laid_out"])


def _side(rng: random.Random, true_side: int) -> Any:
    if rng.random() < 0.4:
        return true_side
    return rng.choice(
        [
            str(true_side),
            f" {true_side} ",
            "8_00",
            true_side + 0.7,
            True,
            float("nan"),
            float("inf"),
            -true_side,
            0,
            10**30,
            10**400,
            "abc",
            [],
            {},
            None,
            "\u0661\u0662\u0663",
        ]
    )


def _own_versions(rng: random.Random, root: Path) -> tuple[list[Path], list[Path]]:
    """Folders of one's own themes beside each other: versions as the app makes them, and
    versions with odd sizes and grids, files and folders that are none, JSON nested past what
    Python reads; (every folder with a theme.json, the clean ones among them)."""
    import shutil

    with_modules = [
        f for f in THEMES if '"module"' in (f / "theme.json").read_text(encoding="utf-8")
    ]
    windows = os.name == "nt"
    folders: list[Path] = []
    clean: list[Path] = []
    stems = [
        "foo",
        "\u00c4rger",
        "\u017ftern",
        "th\u00e9me",
        "a[b]",
        "dot.ted",
        "K-test",
        "lumen-pair",
    ]
    stems += [] if windows else ["x*y", "q?z"]  # (no such names on Windows)
    for n, stem in enumerate(stems):
        parent = root / f"g{n}"
        parent.mkdir()
        names = [
            stem,
            stem + "-vertical",
            stem + "-pair",
            stem + "-companion",
            stem + "-1x2",
            stem.swapcase() + "-800x480",
        ] + ([] if windows else [stem + "-12x34\n"])
        for _ in range(rng.randrange(2, 6)):
            w, h = (
                str(d) if rng.random() < 0.85 else "".join(chr(0x660 + int(c)) for c in str(d))
                for d in rng.choice(SIZES)
            )  # (some in Arabic-Indic digits)
            names.append(f"{stem}-{w}x{h}")
        good = {stem} | {name for name in names[6:] if rng.random() < 0.6}
        for name in dict.fromkeys(names):
            folder = parent / name
            if folder.exists():  # (the same name in another case, on Windows)
                continue
            kind = 99 if name in good else rng.randrange(20)
            if kind == 0:
                folder.write_text("{}")  # a file, no folder
                continue
            folder.mkdir()
            if kind == 1:
                continue  # no theme.json
            source = rng.choice(with_modules if rng.random() < 0.6 else THEMES)
            for extra in source.iterdir():
                if extra.name != "theme.json" and extra.is_file():
                    shutil.copy(extra, folder / extra.name)
            data = json.loads((source / "theme.json").read_text(encoding="utf-8"))
            display = data.setdefault("display", {})
            if kind == 2:
                (folder / "theme.json").write_bytes(b"{not json")
                continue
            if kind == 3:
                (folder / "theme.json").write_bytes(
                    b'{"display": {"width": 800, "height": 480}, "n": "\xff"}'
                )
                continue
            if kind == 4:
                (folder / "theme.json").write_text('{"display": ' + "[" * 20000 + "]" * 20000 + "}")
                continue
            if kind == 99:  # as the app makes a version: its size in its name
                sized = SIZED.match(name)
                w, h = display.get("width", 800), display.get("height", 480)
                if sized:
                    w, h = int(sized[2]), int(sized[3])
                elif name.endswith(VERTICAL):
                    w, h = sorted((w, h))
                display.update(
                    width=w,
                    height=h,
                    model="custom",
                    orientation="portrait" if h > w else "landscape",
                )
            elif kind == 5:
                data["display"] = rng.choice([[], "x", None, 5])
            else:
                display["width"] = _side(rng, display.get("width", 800))
                display["height"] = _side(rng, display.get("height", 480))
                if rng.random() < 0.2:
                    display["model"] = rng.choice(["custom", "nope", 5, None, "", *MODELS[1:8]])
            if rng.random() < 0.45:
                grid = data.get("grid") if isinstance(data.get("grid"), dict) else {}
                for key in ("columns", "rows", "gap", "margin"):
                    if rng.random() < 0.5:
                        good_values = [0, 2, 4, 8, 12, 24, 48, 96, 10.5, 7.9, 2.5, 0.0, 16]
                        grid[key] = rng.choice(good_values) if kind == 99 else _setting(rng)
                data["grid"] = (
                    grid if kind == 99 or rng.random() < 0.9 else rng.choice([[], [1], "x", 0])
                )
            (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
            folders.append(folder)
            if kind == 99:
                clean.append(folder)
    return folders, clean


def _listed_in_order(answers: list[Any]) -> list[Any]:
    """The answers with each folder's versions in the order of their names: a folder lists
    them as its file system hands them out, another way on each."""
    [ours] = answers
    versions = [
        {**v, "listed": sorted(v["listed"]) if isinstance(v["listed"], list) else v["listed"]}
        for v in ours["versions"]
    ]
    return [{**ours, "versions": versions}]


@pytest.mark.kept
def test_versions_beside_one_s_own_themes_are_found_and_laid_out_as_python_does(
    tmp_path: Path,
) -> None:
    rng = random.Random(0x0E5)
    folders, clean = _own_versions(rng, tmp_path)
    sizes = [*SIZES, (250, 250), (320, 170), (1000, 1000), (3000, 400)]
    versions = [
        {"folder": str(f), "size": list(rng.choice(sizes))} for f in folders for _ in range(2)
    ]
    laid = []
    for folder in clean + rng.sample(folders, min(20, len(folders))):
        for _ in range(5):
            size = rng.choice(sizes)
            fitting = [
                m.id for m in devices.MODELS if size in (m.size("landscape"), m.size("portrait"))
            ]
            laid.append(
                {
                    "folder": str(folder),
                    "size": list(size),
                    "model": rng.choice(["custom", "nope", rng.choice(MODELS[1:]), *fitting]),
                }
            )
    case = {"op": "theme_adapt", "fonts_dir": FONTS, "builtin": str(BUILTIN)}
    [ours] = run([{**case, "versions": versions, "laid_out": laid}], kept_as=_listed_in_order)

    # (versions found and laid out on sizes, not only refused)
    assert sum(isinstance(v["listed"], list) and len(v["listed"]) > 1
               for v in ours["versions"]) >= 10  # fmt: skip
    assert sum("dict" in made for made in ours["laid_out"]) >= 10

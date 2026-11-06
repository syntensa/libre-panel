"""rust/i18n and the modules of rust/theme against i18n.py and theme/modules.py (docs/RUST.md,
R4): every text of every language as t() finds it, the system's language, and themes of
modules laid out on their grids (expand) in every language, with every look, size and option of
the module kinds Rust builds, widget for widget, field for field."""

from __future__ import annotations

import json
import locale
import random
import sys
from typing import Any

import pytest
from rust_check import pillow_libfreetype, run

from libre_panel import design, fonts, i18n
from libre_panel.devices import models as devices
from libre_panel.theme import model, modules

# The module kinds rust/theme builds so far (the others follow, kind by kind).
BUILT = [
    "ring", "stat", "graph", "bars", "network", "clock", "date", "weather", "outlook", "system",
    "sun", "moon", "earth", "analog", "world", "countdown", "huge", "text", "temps", "cores",
    "drives", "processes", "netinfo", "values", "ping", "timer", "power", "ups",
]  # fmt: skip


def test_texts_in_every_language_as_python_finds_them(monkeypatch: Any) -> None:
    monkeypatch.undo()  # the real detection of the system's language, not the fixture's English
    texts = ["", "Temp", "No weather set up", "not a text of the app", "cpu_temp", "font_size"]
    for code in i18n.LANGUAGES:
        catalog = i18n.catalog(code)
        texts += sorted(catalog.get("messages", {}))[:400] + sorted(catalog.get("fields", {}))
    texts = list(dict.fromkeys(texts))
    languages = [*i18n.LANGUAGES, "xx"]
    rng = random.Random(0x118)
    envs: list[dict[str, Any]] = [{}, {"LANG": "C"}, {"LANG": "de_DE.UTF-8"}]
    values = ["", "C", "POSIX", "C.UTF-8", "de_DE", "fr", "sv_SE", "ja_JP.UTF-8", "pt_BR", "Zh"]
    for _ in range(300):
        env = {
            k: rng.choice(values) for k in ("LC_ALL", "LC_MESSAGES", "LANG") if rng.random() < 0.5
        }
        if rng.random() < 0.4:
            env["LANGUAGE"] = ":".join(rng.choice(values) for _ in range(rng.randrange(1, 4)))
        if rng.random() < 0.3:
            env["windows"] = rng.choice([0x0407, 0x0409, 0x0411, 0x0804, 0x041D, 0xFFFF])
        envs.append(env)
    case = {"op": "i18n", "locale": str(i18n.LOCALE_DIR), "texts": texts, "languages": languages}
    [ours] = run([{**case, "envs": envs}])
    for code, got in zip(languages, ours["languages"], strict=True):
        monkeypatch.setattr(i18n, "_current", code)
        assert got == {
            "t": [i18n.t(x) for x in texts],
            "fields": [i18n.field_label(x) for x in texts],
        }

    def system(env: dict[str, Any]) -> str:
        for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            monkeypatch.delenv(key, raising=False)
            if key in env:
                monkeypatch.setenv(key, env[key])
        windows = "windows" in env
        monkeypatch.setattr(sys, "platform", "win32" if windows else "linux")
        name = locale.windows_locale.get(env.get("windows", -1), "")
        monkeypatch.setattr(i18n, "_windows_language", lambda: name)
        return i18n.system_language()

    assert ours["systems"] == [system(env) for env in envs]


FONTS = [f"builtin:{name}" for name in fonts.builtin_fonts()] + ["MyFont.ttf", "ふぉんと"]
SIZES = sorted({m.size(w) for m in devices.MODELS for w in ("landscape", "portrait")})
SIZES += [(800, 480), (320, 240), (1000, 1000)]
TITLES = ["", "", "CPU", "Prozessor-Temperatur", "温度", "Ärger ſtrasse", "İstanbul", "a" * 40]
TARGETS = [
    "", "12-24", "2026-12-24 18:00", "2026-2-30", "٢٠٢٦-١٢-٢٤", " 12-24 ", "12-24\n", "1-1T9:05",
    "13-01", "2026-12-24 24:00", "2026-12-24 18:60", "junk", "123-4",
    "2", " 3 ", "4", "5", "\u30004\n", "2 ",  # a ping's host
]  # fmt: skip
PLACES = [
    "", "Asia/Tokyo = Tokio\nEurope/London", "# none\nUTC", "America/Argentina/Buenos_Aires",
    "Nope/Zone = X\n../etc/passwd\n/etc/localtime\nzone.tab\nEurope/Paris=Paris\x1cAsia/"
    "Kolkata Australia/Perth = ", "Europe/Berlin\r\nAsia/Seoul = 서울\vUS/Pacific",
    "Europe/Rome\nAfrica/Cairo\nAsia/Dubai\nPacific/Auckland\nAmerica/Chicago\nAsia/Shanghai",
]  # fmt: skip


def _module(rng: random.Random, i: int, columns: int, rows: int) -> dict[str, Any]:
    m: dict[str, Any] = {
        "type": "module", "id": f"m{i}", "module": rng.choice(BUILT),
        "col": rng.randrange(0, columns), "row": rng.randrange(0, rows),
        "cols": rng.randrange(1, columns + 1), "rows": rng.randrange(1, rows + 1),
    }  # fmt: skip
    if rng.random() < 0.15:  # past the grid: kept on it
        m.update(col=rng.choice([-2, columns + 3]), cols=rng.choice([0, 99]))
    options = {
        "source": ["cpu", "gpu", "mem", "disk", "net", "sensor", "fan"],
        "sensor": ["", "cpu.temp", "gpu.fan2", "net.up"],
        "format": ["", "{value:.1f} °C", "{value:bytes}", "{value}", "{value:.0f}%", "{value}°%"],
        "title": TITLES,
        "color": [None, "#ff8800", "#0b8", "#11223380", "teal", "@accent", "@nope", "@warn"],
        "card": ["auto", "on", "off"],
        "finish": ["auto", "flat", "lit", "facet", "acrylic"],
        "fallback": ["auto", "none", "cpu", "gpu", "mem", "disk"],
        "forecast": ["auto", "days", "hours", "both", "off"],
        "step": [0, 1, 2, 3, 5, 6, 9],
        "moving": [True, False],
        "timezone": ["", "Asia/Tokyo", "Europe/Berlin", "Nowhere/Land"],
        "target": TARGETS,
        "items": PLACES,
        "text": TITLES,
        "fans": [0, 1, 2, 3, 6, 9, -1, 10**30],
        "fan_items": ["", "fan.lpc.fan_2 = Front\nfan.*"],
        "threads": [True, False],
        "total": [True, False],
        "sort": ["cpu", "memory"],
    }
    for key, choices in options.items():
        if rng.random() < 0.4:
            m[key] = rng.choice(choices)
    return m


def _theme(rng: random.Random) -> dict[str, Any]:
    w, h = rng.choice(SIZES)
    look = design.LOOKS[rng.choice(list(design.LOOKS))]
    style = dict(look["style"])
    if rng.random() < 0.5:
        style.update(card=rng.choice(modules.CARDS), radius=rng.randrange(0, 101),
                     glow=rng.choice([0, 0.3, 0.6, 1, 0.45]),
                     backdrop=rng.choice(modules.BACKDROPS),
                     card_opacity=rng.choice([0.4, 0.7, 1, 0.85]))  # fmt: skip
    if rng.random() < 0.4:
        style.update(display_font=rng.choice(FONTS), text_font=rng.choice(FONTS))
    grid: dict[str, int] = {}
    if rng.random() < 0.8:
        grid = {"columns": rng.choice([0, 1, 2, 3, 4, 6, 8, 12, 24]),
                "rows": rng.choice([0, 1, 2, 3, 4, 6]), "gap": rng.choice([0, 4, 8, 16]),
                "margin": rng.choice([0, 8, 24])}  # fmt: skip
    columns, rows = grid.get("columns") or 4, grid.get("rows") or 2
    data: dict[str, Any] = {
        "format": model.THEME_FORMAT, "name": "Modules",
        "display": {"model": "custom", "orientation": "portrait" if h > w else "landscape",
                    "width": w, "height": h},
        "palette": dict(look["palette"]) if rng.random() < 0.8 else {}, "grid": grid,
        "widgets": [_module(rng, i, columns, rows) for i in range(rng.randrange(1, 7))],
    }  # fmt: skip
    if rng.random() < 0.9:
        data["style"] = style
    return data


def _expanded(case: dict[str, Any]) -> dict[str, Any]:
    i18n.set_language(case["language"])
    try:
        theme = model.with_light_colours(model.parse_theme(json.loads(case["data"])), case["light"])
        widgets, boxes = modules.expand(theme)
    except Exception as error:  # noqa: BLE001 (every kind Python raises is compared)
        return {"error": str(error), "kind": type(error).__name__}
    return {"widgets": json.dumps(widgets), "boxes": [[k, b] for k, b in boxes.items()],
            "warnings": theme.warnings}  # fmt: skip


def test_modules_laid_out_as_python_lays_them_out() -> None:
    if pillow_libfreetype() is None:
        pytest.skip("Pillow's FreeType is built into it here: none for lp-check to load")
    rng = random.Random(0x40D)
    cases = [{"data": json.dumps(_theme(rng)), "light": rng.random() < 0.3,
              "language": rng.choice(list(i18n.LANGUAGES))} for _ in range(500)]  # fmt: skip
    case = {"op": "theme_expand", "fonts_dir": str(fonts.FONT_DIR), "locale": str(i18n.LOCALE_DIR)}
    [ours] = run([{**case, "themes": cases}])
    try:
        python = [_expanded(c) for c in cases]
    finally:
        i18n.set_language("en")
    assert ours["answers"] == python
    # (laid out, not only refused alike: most themes hold modules that are drawn)
    assert sum("widgets" in answer for answer in python) > 300

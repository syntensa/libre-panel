"""rust/i18n and the modules of rust/theme against i18n.py and theme/modules.py (docs/RUST.md,
R4): every text of every language as t() finds it, the system's language, and themes of
modules laid out on their grids (expand) in every language, with every look, size and option of
every module kind and card, widget for widget, field for field."""

from __future__ import annotations

import json
import locale
import math
import random
import struct
import sys
from typing import Any

import pytest
from rust_check import pillow_libfreetype, run

from libre_panel import design, fonts, i18n
from libre_panel.devices import models as devices
from libre_panel.theme import model, modules

# The module kinds of theme/modules.py and theme/cards.py, every one.
BUILT = [
    "ring", "stat", "graph", "bars", "network", "clock", "date", "weather", "outlook", "system",
    "sun", "moon", "earth", "analog", "world", "countdown", "huge", "text", "temps", "cores",
    "drives", "processes", "netinfo", "values", "ping", "timer", "power", "ups", "mic", "battery",
    "ai", "image", "music", "volume", "agenda", "game", "playback", "forecast", "download",
    "instrument", "cards", "netcard",
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
    "photos/*.jpg", "a.png\n\n  b.jpg  \n\u2029", "  ",  # a picture's
    "playback\nforecast for 5m if weather.temperature\n# x\nnet\nring\nbogus", "download",
    "  instrument  for 90s # cpu\ncards\nnetcard", "net if net.up\x1cgame", "# nothing",  # a slot's
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
        "agent": ["both", "claude", "codex"],
        "seconds": [0, 1, 5, -3, 10**30],
        "speed": [1.0, 0.5, 2, 0],
        "time": ["total", "remaining"],
        "top": [175, 0, -5, 60.5, 10**20],
        "history": [True, False],
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
    # a battery's card no higher than its padding: Python's ZeroDivisionError, of a float
    # (wide) and of an int (narrow)
    for width in (480, 30):
        tiny = {
            "format": model.THEME_FORMAT,
            "name": "Tiny",
            "display": {
                "model": "custom",
                "orientation": "landscape" if width > 80 else "portrait",
                "width": width,
                "height": 80,
            },
            "grid": {"columns": 1, "rows": 2, "gap": 8, "margin": 24},
            "widgets": [{"type": "module", "id": "b", "module": "battery", "card": "on"}],
        }
        cases.append({"data": json.dumps(tiny), "light": False, "language": "en"})
    case = {"op": "theme_expand", "fonts_dir": str(fonts.FONT_DIR), "locale": str(i18n.LOCALE_DIR)}
    [ours] = run([{**case, "themes": cases}])
    try:
        python = [_expanded(c) for c in cases]
    finally:
        i18n.set_language("en")
    assert ours["answers"] == python
    # (laid out, not only refused alike: most themes hold modules that are drawn)
    assert sum("widgets" in answer for answer in python) > 300
    assert [a["error"] for a in python[-2:]] == ["float division by zero", "division by zero"]


CARD_LINES = [
    "playback", "forecast for 5m", "bogus", " net if net.up ", "# c", "ring for 2h if x",
    "download\u2028", "clock for 90s # a comment", "cards", "netcard", "instrument for 4s",
    "agenda for 1e1m if calendar.events, weather.temperature", "moon  for  2.5 min", "",
]  # fmt: skip
TIMES = [
    "5m", "90s", "1h", "2.5 min", " 10 ", "3", "4", "86401s", "24h", "1440", "1441", "1e1m",
    "inf", "nan", "-5m", "\u0661\u0660m", "5 M", "5mins", "5 sec", "5 secs", "1_0m", "1__0m",
    "_10m", "10_m", "0x10", "", "m", "m5", "5.m", ".5h", "5.e1s", "5 h ", "\u200a5m", "5\u00b5s",
    "İ5m", "6 MIN", "4.999s", "5s", "86400s", "1.4e3", "2 h", "1 hour", "5\x00m",
]  # fmt: skip


def _bits(x: float | None) -> str | None:
    return None if x is None else struct.pack(">d", x).hex()


def _card(card: Any) -> list[Any] | None:
    return None if card is None else [card.name, card.rule, _bits(card.seconds)]


def test_what_the_editor_asks_of_the_modules_as_python_answers() -> None:
    grids = [(c, r) for c in range(1, 13) for r in range(1, 7)] + [(24, 6), (16, 12), (1, 24)]
    slots = [{"module": "cards", "items": x} for x in [*CARD_LINES, "\n".join(CARD_LINES)]]
    slots += [{"module": "netcard", "items": "ring"}, {"module": "cards", "items": "# only"}]
    rng = random.Random(0x5107)
    seconds = [5.0, 90.0, 300.0, 3600.0, 7200.0, 5.5, 86400.0, 1e-5, 1e22, -0.0, 0.0, 61.0,
               math.inf, -math.inf, math.nan, 123456789.0, 2.5e-300]  # fmt: skip
    seconds += [rng.choice([60, 3600, 1]) * rng.uniform(0, 3000) for _ in range(300)]
    seconds += [float(rng.randrange(1, 100000)) for _ in range(300)]
    languages = list(i18n.LANGUAGES)
    case = {"op": "module_app", "fonts_dir": str(fonts.FONT_DIR), "locale": str(i18n.LOCALE_DIR),
            "languages": languages, "grids": grids, "lines": CARD_LINES + TIMES,
            "modules": [json.dumps(m) for m in slots], "times": TIMES + CARD_LINES,
            "seconds": [_bits(x) for x in seconds], "themes": []}  # fmt: skip
    [ours] = run([case])
    try:
        for code, got in zip(languages, ours["languages"], strict=True):
            i18n.set_language(code)
            assert got == {
                "info": json.dumps(modules.module_info()),
                "templates": [json.dumps(modules.templates(c, r)) for c, r in grids],
            }
    finally:
        i18n.set_language("en")
    assert ours["lines"] == [_card(modules.card_line(x)) for x in CARD_LINES + TIMES]
    assert ours["slot_cards"] == [[_card(c) for c in modules.slot_cards(m)] for m in slots]
    assert ours["times"] == [_bits(modules.card_time(x)) for x in TIMES + CARD_LINES]
    assert ours["time_texts"] == [modules.time_text(x) for x in seconds]


def _app(case: dict[str, Any]) -> dict[str, Any]:
    i18n.set_language(case["language"])
    try:
        theme = model.with_light_colours(model.parse_theme(json.loads(case["data"])), case["light"])
    except Exception as error:  # noqa: BLE001 (every kind Python raises is compared)
        return {"error": str(error), "kind": type(error).__name__}
    try:
        detached: Any = json.dumps(modules.detach(theme, case["id"]))
    except Exception as error:  # noqa: BLE001
        detached = {"error": str(error), "kind": type(error).__name__}
    chosen = modules.with_cards(theme, case["cards"], case["order"], case["transition"])
    return {"detach": detached, "follows": modules.follows_cards(theme),
            "changed": chosen is not theme, "with_cards": json.dumps(chosen.widgets)}  # fmt: skip


def test_modules_detached_and_the_person_s_cards_as_python_has_them() -> None:
    if pillow_libfreetype() is None:
        pytest.skip("Pillow's FreeType is built into it here: none for lp-check to load")
    rng = random.Random(0xDE7)
    cases = []
    for _ in range(200):
        data = _theme(rng)
        if rng.random() < 0.6:  # a card slot: open to the person's cards (empty), or its own
            items = rng.choice(["", "  ", "\n", "ring", "playback\nforecast for 5m"])
            data["widgets"].append({"type": "module", "id": "slot", "module": "cards",
                                    "col": 0, "row": 0, "cols": 2, "items": items})  # fmt: skip
        ids = [w["id"] for w in data["widgets"]] + ["nope", ""]
        cases.append({"data": json.dumps(data), "light": rng.random() < 0.3,
                      "language": rng.choice(list(i18n.LANGUAGES)), "id": rng.choice(ids),
                      "cards": rng.sample(CARD_LINES, rng.randrange(0, 5)),
                      "order": rng.choice(["first", "turns", "x"]),
                      "transition": rng.choice(["light", "fade", "random", "zz"])})  # fmt: skip
    case = {"op": "module_app", "fonts_dir": str(fonts.FONT_DIR), "locale": str(i18n.LOCALE_DIR)}
    [ours] = run([{**case, "themes": cases}])
    try:
        python = [_app(c) for c in cases]
    finally:
        i18n.set_language("en")
    assert ours["themes"] == python
    assert sum("detach" in a and a["detach"] != "[]" for a in python) > 50  # (parts, not none)
    assert sum(a.get("changed", False) for a in python) > 20  # (cards chosen in open slots)

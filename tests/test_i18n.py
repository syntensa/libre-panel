"""German and English: complete catalog, dates, weather texts, language switch."""

import json
import re
import threading
from datetime import datetime

import pytest
from i18n_keys import all_messages, tables

from libre_panel import i18n
from libre_panel.config import load_config
from libre_panel.i18n import format_date, t

CATALOG = json.loads((i18n.LOCALE_DIR / "de.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_text_has_a_german_translation():
    missing = sorted(all_messages() - set(CATALOG["messages"]))
    assert missing == [], "add these to locale/de.json:\n" + "\n".join(missing)


def test_the_catalog_has_no_stale_texts():
    unused = sorted(set(CATALOG["messages"]) - all_messages())
    assert unused == [], "no longer used, remove from locale/de.json:\n" + "\n".join(unused)


def test_the_catalog_has_each_text_once():
    """Two branches that translate the same text merge into a table that has it twice (each
    in its own place, so git sees no clash) and the later one wins unseen."""
    twice = []

    def one_of_each(pairs):
        names = [name for name, _ in pairs]
        twice.extend(sorted({name for name in names if names.count(name) > 1}))
        return dict(pairs)

    text = (i18n.LOCALE_DIR / "de.json").read_text(encoding="utf-8")
    json.loads(text, object_pairs_hook=one_of_each)
    assert twice == [], "in locale/de.json more than once, keep one:\n" + "\n".join(twice)


@pytest.mark.parametrize("table", ["fields", "widgets", "enums", "icons"])
def test_editor_tables_are_complete(table):
    assert set(CATALOG[table]) == tables()[table]


def test_a_long_german_widget_name_may_break_at_a_soft_hyphen():
    """The app's library shows the single widgets in three columns of 75 px: a word of more than
    11 letters does not fit and broke in the middle of a syllable ("Wettersymb-ol"). The catalog
    says where a compound may break (a soft hyphen, which is not seen where it does not)."""
    shy = "\u00ad"
    for key, name in CATALOG["widgets"].items():
        pieces = re.split(rf"[\s\-{shy}]+", name)
        assert all(len(piece) >= 3 for piece in pieces), (key, "a soft hyphen at an end")
        longest = max(len(piece) for piece in pieces)
        assert longest <= 11, f"{key}: {name!r} has a piece too long for a tile"
    assert CATALOG["widgets"]["symbol"].replace(shy, "") == "Wettersymbol"
    assert CATALOG["widgets"]["calendar"].replace(shy, "") == "Monatskalender"
    assert shy not in "".join(CATALOG["messages"].values())  # (the panel draws those: no box)


def test_placeholders_match():
    for english, german in CATALOG["messages"].items():
        assert set(PLACEHOLDER.findall(english)) == set(PLACEHOLDER.findall(german)), english
    assert all(german.strip() for german in CATALOG["messages"].values())


def test_names_of_days_and_months():
    assert len(CATALOG["days"]) == len(CATALOG["days_short"]) == 7
    assert len(CATALOG["months"]) == len(CATALOG["months_short"]) == 12


def test_translate_and_fall_back():
    i18n.set_language("de")
    assert t("Pause panel") == "Panel pausieren"
    assert t("Saved {id}", id="orbit") == "orbit gespeichert"
    assert t("not in the catalog") == "not in the catalog"
    assert i18n.field_label("font_size") == "Schriftgröße"
    i18n.set_language("en")
    assert t("Saved {id}", id="orbit") == "Saved orbit"
    assert i18n.field_label("font_size") == "font size"


def test_german_dates():
    moment = datetime(2026, 9, 27, 9, 5)  # a Sunday
    i18n.set_language("de")
    assert format_date(moment, "%A, %d %B") == "Sonntag, 27. September"
    assert format_date(moment, "%a %d %b") == "So 27. Sep"
    assert format_date(moment, "%a %d.%m.") == "So 27.09."
    assert format_date(moment, "%H:%M") == "09:05"
    assert format_date(datetime(2026, 3, 2), "%B") == "März"
    assert format_date(moment, "100%% %A") == "100% Sonntag"
    i18n.set_language("en")
    assert format_date(moment, "%A, %d %B") == "Sunday, 27 September"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "de_DE.UTF-8"}, "de"),
        ({"LANG": "de_AT.UTF-8"}, "de"),
        ({"LANG": "fr_FR.UTF-8"}, "en"),
        ({"LC_ALL": "C", "LANG": "de_DE.UTF-8"}, "de"),
        ({}, "en"),
    ],
)
def test_system_language_from_the_environment(monkeypatch, env, expected):
    monkeypatch.undo()  # the real detection, not the fixture's English
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    assert i18n.system_language() == expected


def test_language_setting(isolated_home):
    from libre_panel.config import ConfigError, parse_config

    assert load_config().language == "auto"
    assert parse_config({"language": "de"}).language == "de"
    with pytest.raises(ConfigError):
        parse_config({"language": "fr"})


def test_panel_texts_in_german():
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import parse_theme

    theme = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "name": "t",
            "display": {"width": 200, "height": 40},
            "widgets": [
                {"type": "weather", "id": "w", "x": 0, "y": 0, "field": "description"},
                {"type": "clock", "id": "c", "x": 0, "y": 20, "format": "%A"},
            ],
        }
    )
    snapshot = demo_snapshot()
    snapshot.now = datetime(2026, 9, 27, 12, 0)
    renderer = Renderer(theme)
    texts = {}
    original = renderer._text_widget

    def spy(widget, text, color):
        texts[widget["id"]] = text
        return original(widget, text, color)

    renderer._text_widget = spy
    i18n.set_language("de")
    renderer.render(snapshot)
    assert texts == {"w": "Bedeckt", "c": "Sonntag"}
    i18n.set_language("en")
    renderer.render(snapshot)
    assert texts == {"w": "Overcast", "c": "Sunday"}


def test_a_clock_sets_its_date_in_capitals_in_the_language_of_the_panel():
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import parse_theme

    def clock(wid, **extra):
        return {"type": "clock", "id": wid, "x": 0, "y": 0, "format": "%A · %B", **extra}

    theme = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "name": "t",
            "display": {"width": 200, "height": 40},
            "widgets": [clock("plain"), clock("caps", uppercase=True)],
        }
    )
    snapshot = demo_snapshot()
    snapshot.now = datetime(2026, 9, 28, 12, 0)
    renderer = Renderer(theme)
    texts = {}
    original = renderer._text_widget

    def spy(widget, text, color):
        texts[widget["id"]] = text
        return original(widget, text, color)

    renderer._text_widget = spy
    try:
        i18n.set_language("de")
        renderer.render(snapshot)
        assert texts == {"plain": "Montag · September", "caps": "MONTAG · SEPTEMBER"}
        i18n.set_language("en")
        renderer.render(snapshot)
        assert texts == {"plain": "Monday · September", "caps": "MONDAY · SEPTEMBER"}
    finally:
        i18n.set_language("en")


def test_editor_switches_language(isolated_home):
    from test_editor_server import request

    from libre_panel.editor.server import make_server

    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        status, data = request(port, "GET", "/api/i18n")
        assert status == 200 and data["language"] == "en" and data["setting"] == "auto"
        assert "messages" not in data  # English needs no catalog

        status, data = request(port, "POST", "/api/language", {"language": "de"})
        assert status == 200 and data["language"] == "de"
        assert data["messages"]["Save"] == "Speichern" and data["widgets"]["bar"] == "Balken"
        assert load_config().language == "de"
        status, specs = request(port, "GET", "/api/specs")
        assert "CPU-Karte" in [p["name"] for p in specs["presets"]]

        status, _ = request(port, "POST", "/api/language", {"language": "fr"})
        assert status == 400
        request(port, "POST", "/api/language", {"language": "en"})
        assert i18n.language() == "en"
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()


def test_tray_menu_in_german(isolated_home):
    import os

    os.environ.setdefault("PYSTRAY_BACKEND", "dummy")
    pystray = pytest.importorskip("pystray")
    from test_service import write_config

    from libre_panel.service import BackgroundApp
    from libre_panel.tray import Tray

    write_config(isolated_home)
    i18n.set_language("de")
    tray = Tray(BackgroundApp(port=0), pystray)
    texts = [item.text for item in tray.build_menu() if item is not pystray.Menu.SEPARATOR]
    assert "Libre Panel öffnen" in texts and "Theme-Editor" in texts and "Einstellungen" in texts
    assert "Libre Panel beenden" in texts
    assert texts[0] == "Libre Panel: angehalten"


def test_editor_scripts_have_no_untranslated_texts():
    """Visible texts in the editor's scripts and its built-in pages go through t();
    plain English literals are a slip."""
    from i18n_keys import script_files

    pattern = re.compile(
        r'(?:\b(?:text|title|placeholder|label|alt)|"aria-label"): *"[A-Za-z]'
        r'|\b(?:setStatus|confirm|prompt|alert)\("[A-Za-z]'
    )
    slips = [
        f"{path.name}: {line.strip()}"
        for path in script_files()
        for line in path.read_text(encoding="utf-8").splitlines()
        if pattern.search(line)
    ]
    assert slips == []

import json
from pathlib import Path

import pytest

from libre_panel.theme.model import (
    THEME_FORMAT,
    ThemeError,
    find_theme,
    list_themes,
    load_theme,
    parse_theme,
    resolve_asset,
    save_theme,
)


def minimal(**display):
    return {
        "format": THEME_FORMAT,
        "name": "t",
        "display": display or {"width": 100, "height": 50},
        "widgets": [{"type": "text", "id": "a", "text": "hi"}],
    }


@pytest.mark.parametrize("name", ["libre-default", "spur-ii"])
def test_builtin_themes_are_valid(name):
    theme = load_theme(find_theme(name))
    assert theme.warnings == []
    assert theme.widgets


def test_builtin_theme_list():
    ids = {t["id"] for t in list_themes()}
    assert {"libre-default", "spur-ii"} <= ids


def test_defaults_are_filled_in():
    theme = parse_theme(minimal())
    widget = theme.widgets[0]
    assert widget["font_size"] == 24 and widget["align"] == "left" and widget["visible"] is True


def test_model_defines_size():
    theme = parse_theme(minimal(model="turing-9.2-usb", orientation="portrait"))
    assert (theme.width, theme.height) == (480, 1920)
    assert theme.to_dict()["display"]["orientation"] == "portrait"


def test_model_size_mismatch_is_rejected():
    with pytest.raises(ThemeError, match="is 1920x480"):
        parse_theme(
            minimal(model="turing-9.2-usb", orientation="landscape", width=1920, height=462)
        )


@pytest.mark.parametrize(
    "widget, message",
    [
        ({"type": "nope"}, "unknown widget type"),
        ({"type": "bar", "color": "notacolor"}, "invalid color"),
        ({"type": "bar", "w": "wide"}, "expected an integer"),
        ({"type": "text", "align": "justify"}, "must be one of"),
        ({"type": "metric", "color_rules": [{"above": 1}]}, "needs 'above' and 'color'"),
    ],
)
def test_invalid_widgets(widget, message):
    data = minimal()
    data["widgets"] = [widget]
    with pytest.raises(ThemeError, match=message):
        parse_theme(data)


def test_duplicate_ids():
    data = minimal()
    data["widgets"] = [{"type": "text", "id": "a"}, {"type": "text", "id": "a"}]
    with pytest.raises(ThemeError, match="duplicate id"):
        parse_theme(data)


def test_unknown_fields_warn():
    data = minimal()
    data["widgets"][0]["sparkle"] = True
    assert "sparkle" in parse_theme(data).warnings[0]


@pytest.mark.parametrize(
    "rel", ["../secret.png", "/etc/passwd", "C:\\Windows\\x.png", "a/../../b.png"]
)
def test_assets_cannot_leave_the_theme(tmp_path, rel):
    with pytest.raises(ThemeError):
        resolve_asset(tmp_path, rel)


def test_theme_referencing_outside_asset_is_rejected(tmp_path):
    data = minimal()
    data["widgets"] = [{"type": "image", "id": "i", "src": "../../x.png"}]
    with pytest.raises(ThemeError):
        parse_theme(data, root=tmp_path)


def test_save_copy_brings_assets_along(isolated_home, tmp_path):
    data = load_theme(find_theme("libre-default")).to_dict()
    folder = save_theme("mine", data, source="libre-default")
    assert folder == isolated_home / "themes" / "mine"
    saved = json.loads((folder / "theme.json").read_text())
    assert saved["name"] == "Libre Default"
    # User themes shadow built-in ones of the same name.
    assert find_theme("mine") == folder


def test_invalid_theme_names():
    for name in ["../x", "", "a/b", ".hidden"]:
        with pytest.raises(ThemeError):
            find_theme(name)
    with pytest.raises(ThemeError):
        save_theme("../escape", minimal())


def test_builtin_theme_files_are_plain_json():
    root = Path(__file__).resolve().parents[1] / "src" / "libre_panel" / "themes"
    for file in root.glob("*/theme.json"):
        json.loads(file.read_text(encoding="utf-8"))

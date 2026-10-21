"""Modules sent from one panel's theme to another's (distribute.py)."""

import json

import pytest

from libre_panel import distribute
from libre_panel.config import load_config, user_themes_dir
from libre_panel.theme.model import find_theme, load_theme, save_theme

CPU = {"type": "module", "id": "cpu", "module": "stat", "source": "cpu", "col": 0, "row": 0,
       "cols": 2, "rows": 1}  # fmt: skip


@pytest.fixture
def config_path(isolated_home):
    isolated_home.mkdir(parents=True, exist_ok=True)
    path = isolated_home / "config.toml"
    path.write_text('theme = "lumen"\n\n[[panels]]\nmodel = "turing-9.2-usb"\ntheme = "slate"\n',
                    encoding="utf-8")  # fmt: skip
    return path


def test_a_built_in_theme_gets_a_copy_of_one_s_own_with_the_module_and_the_panel_shows_it(
    config_path,
):
    done = distribute.send([CPU], 2, config_path)
    assert done["copied"] and done["theme"] == "slate-own"
    assert load_config(config_path).panels[0].theme == "slate-own"
    copy = json.loads((user_themes_dir() / "slate-own" / "theme.json").read_text("utf-8"))
    assert copy["name"].endswith("(mine)")
    sent = [w for w in copy["widgets"] if w.get("type") == "module"]
    assert (
        len(sent) == 1 and sent[0]["source"] == "cpu" and (sent[0]["col"], sent[0]["row"]) == (0, 0)
    )
    again = distribute.send([CPU], 2, config_path)  # into the copy now, beside the first
    assert not again["copied"] and again["theme"] == "slate-own"
    modules = [w for w in load_theme(find_theme("slate-own")).widgets if w["type"] == "module"]
    assert len(modules) == 2 and modules[0]["id"] != modules[1]["id"]  # a new id
    assert (modules[1]["col"], modules[1]["row"]) != (0, 0)


def test_a_module_goes_where_cells_are_free_and_says_so_where_none_are(config_path):
    data = json.loads((find_theme("lumen-companion") / "theme.json").read_text("utf-8"))
    save_theme("full", data, source="lumen-companion")
    text = config_path.read_text(encoding="utf-8").replace('"slate"', '"full"')
    config_path.write_text(text, encoding="utf-8")
    with pytest.raises(distribute.SendError, match="no free cells"):
        distribute.send([CPU], 2, config_path)
    with pytest.raises(distribute.SendError, match="no panel 3"):
        distribute.send([CPU], 3, config_path)
    with pytest.raises(distribute.SendError, match="only modules"):
        distribute.send([{"type": "text", "id": "t"}], 2, config_path)


def test_the_theme_a_panel_shows_is_the_one_it_says_or_its_version():
    from types import SimpleNamespace

    from libre_panel.config import parse_config

    upright = {"model": "turing-9.2-usb", "orientation": "portrait", "theme": "lumen-companion"}
    config = parse_config({"panels": [upright, {"model": "turing-9.2-usb"}]})
    assert distribute.shown_theme(config, 0) == "lumen-companion-vertical"
    assert distribute.shown_theme(config, 1) == "lumen"  # its own, the bar's
    told = SimpleNamespace(theme="arena-companion")
    assert distribute.shown_theme(config, 0, told) == "arena-companion"

"""Scenes: a theme for every panel at once, put on with a click, a shortcut or the tray."""

import pytest

from libre_panel import scenes
from libre_panel.config import ConfigError, load_config, parse_config, set_active_theme


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'theme = "lumen"\n\n[sequence]\non = true\n'
        '[[sequence.steps]]\ntheme = "slate"\nseconds = 30\n'
        '\n[[panels]]\ntheme = "pico"\n\n[[panels]]\nmodel = "turing-3.5"\n'
        '\n[[scenes]]\nname = "Gaming"\nthemes = ["arena", "lumen", ""]\n'
        '\n[[scenes]]\nname = "Work"\nthemes = ["almanac"]\n',
        encoding="utf-8",
    )
    return path


def test_a_scene_that_is_on_gives_every_panel_its_theme(config_path):
    config = load_config(config_path)
    assert config.theme == "lumen" and [p.theme for p in config.panels] == ["pico", ""]
    scenes.put_on("Gaming", config_path)
    config = load_config(config_path)
    assert config.scene == "Gaming" and config.theme == "arena"
    assert [p.theme for p in config.panels] == ["lumen", ""]  # "": the panel's own stays
    assert not config.sequence.active  # a scene stops the screens in turn, as a theme does
    scenes.put_on("", config_path)
    assert load_config(config_path).theme == "lumen"  # off: the themes of config.toml again
    with pytest.raises(ConfigError, match="no scene"):
        scenes.put_on("Film", config_path)


def test_the_next_scene_follows_the_one_that_is_on_and_the_first_the_last(config_path):
    assert scenes.next_one(config_path) == "Gaming"
    assert scenes.next_one(config_path) == "Work"
    assert scenes.next_one(config_path) == "Gaming"


def test_what_the_panels_show_is_kept_as_a_scene_and_taken_out_again(config_path):
    done = scenes.save(" Film  night ", config_path)
    assert done["on"] == "Film night"
    kept = next(scene for scene in done["scenes"] if scene["name"] == "Film night")
    assert kept["themes"] == ["lumen", "pico", ""]
    done = scenes.save("Film night", config_path)  # again: replaced, not twice
    assert [scene["name"] for scene in done["scenes"]] == ["Gaming", "Work", "Film night"]
    done = scenes.delete("Film night", config_path)
    assert done["on"] == "" and len(done["scenes"]) == 2
    assert "scene =" not in config_path.read_text(encoding="utf-8")


def test_a_theme_chosen_for_the_panel_takes_the_scene_off(config_path):
    scenes.put_on("Work", config_path)
    set_active_theme("slate", config_path)
    config = load_config(config_path)
    assert config.scene == "" and config.theme == "slate"


def test_a_panel_s_own_theme_set_in_the_app_takes_the_scene_off(config_path):
    from libre_panel.editor.settings import Restart, Settings

    scenes.put_on("Gaming", config_path)
    settings = Settings(config_path, None, Restart())
    panels = settings.read()["config"]["panels"]
    settings.write({"set": {"panels": [{**panels[0], "theme": "nimbus"}, panels[1]]}})
    config = load_config(config_path)
    assert config.scene == "" and config.panels[0].theme == "nimbus"
    done = settings.set_scene({"action": "on", "name": "Work"})
    assert done["on"] == "Work" and load_config(config_path).theme == "almanac"


@pytest.mark.parametrize(
    "scenes_table, said",
    [
        ([{"name": "", "themes": ["lumen"]}], "scenes[0].name"),
        ([{"name": "A", "themes": []}], "scenes[0].themes"),
        ([{"name": "A", "themes": ["lumen"]}, {"name": "A", "themes": ["slate"]}], "twice"),
        ([{"name": "A", "themes": ["a", "b", "c", "d", "e"]}], "scenes[0].themes"),
    ],
)
def test_a_wrong_scene_is_said(scenes_table, said):
    with pytest.raises(ConfigError, match=said.replace("[", r"\[").replace("]", r"\]")):
        parse_config({"scenes": scenes_table})


def test_the_shortcut_puts_the_next_scene_on(config_path):
    from libre_panel import service

    app = object.__new__(service.BackgroundApp)
    app.config_path, followed = config_path, []
    app.follow_config = lambda: followed.append(True)
    app.shortcut("next_scene")
    assert load_config(config_path).scene == "Gaming" and followed == [True]

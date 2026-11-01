"""A theme of Libre Panel's own on a panel it has no version for: the version made for the
nearest shape, laid out on the panel's size (theme.adapt.laid_out), not the theme of another
shape scaled into the frame with bars round it."""

import json

from libre_panel.theme.adapt import laid_out, nearest_version, versions_of
from libre_panel.theme.model import THEME_FORMAT, find_theme, load_theme, save_theme


def test_each_panel_gets_the_version_made_for_the_shape_nearest_its_own():
    lumen = find_theme("lumen")
    assert {"lumen", "lumen-vertical", "lumen-1920x720", "lumen-480x480"} <= set(versions_of(lumen))
    assert "lumen-pair" not in versions_of(lumen) and "lumen-companion" not in versions_of(lumen)
    near = {
        (960, 320): "lumen-1920x720",  # the 4.6": the 12.3"'s shape is nearest
        (1280, 480): "lumen-1920x720",  # Thermalright's 6.86": the 12.3"'s shape
        (800, 480): "lumen-1280x720",  # the 5" and the 7": the 5.2"'s
        (1280, 800): "lumen-1280x720",  # the 8"
        (1920, 440): "lumen",  # the 11.3" bars: the 9.2"'s
        (320, 320): "lumen-480x480",  # NZXT's round screens: the 2.1"'s
        (320, 960): "lumen-vertical",  # the 4.6" on end
        (640, 180): "lumen-640x180",  # the ZC-360's screens: made for such alone
    }
    for size, name in near.items():
        assert nearest_version(lumen, size) == name, size


def test_a_panel_without_its_own_version_shows_one_laid_out_on_its_size():
    theme = load_theme(find_theme("lumen"))
    fitted = laid_out(theme, (960, 320))
    assert (fitted.width, fitted.height) == (960, 320)
    assert fitted.root.name == "lumen-1920x720"  # (its pictures from there)
    assert {w["id"] for w in fitted.widgets} == {w["id"] for w in load_theme(fitted.root).widgets}
    made = load_theme(find_theme("lumen-1920x720"))
    assert laid_out(made, (1920, 720)) is made  # made for it: as it is
    assert laid_out(theme, None) is theme  # the size not known: as it is


def test_anyone_elses_theme_stays_as_it_is(tmp_path):
    data = json.loads((find_theme("lumen") / "theme.json").read_text(encoding="utf-8"))
    data["format"] = THEME_FORMAT
    save_theme("my-lumen", data)
    mine = load_theme(find_theme("my-lumen"))
    assert laid_out(mine, (960, 320)) is mine  # (its frames scaled into the panel's, as before)


def test_the_panels_loop_draws_it_on_the_panels_own_size(isolated_home):
    """Lumen on the 4.6" (960x320): laid out on it, drawn in its size, not scaled into it."""
    from test_default_theme_loop import demo_config, loop_for

    from libre_panel.config import DeviceConfig

    loop, clock = loop_for(demo_config(DeviceConfig(model="turing-4.6-usb", driver="virtual"),
                                       theme="lumen"))  # fmt: skip
    try:
        assert loop.status.theme == "lumen"
        assert (loop.renderer.theme.width, loop.renderer.theme.height) == (960, 320)
        assert loop.renderer.theme.root.name == "lumen-1920x720"
        assert loop.size == (960, 320)
        frame = loop.step(clock.now)
        assert frame is not None and frame.size == (960, 320)
    finally:
        loop.close()

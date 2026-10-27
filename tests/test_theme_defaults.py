"""The theme a panel shows when config.toml names none: Lumen on the 1920x480 bars, Libre
Default on every other panel. The rule, which panel a config is for, and config.toml."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from libre_panel import app as app_module
from libre_panel.config import Config, DeviceConfig, load_config
from libre_panel.devices import turzx_usb
from libre_panel.devices.models import MODELS, get_model
from libre_panel.theme.defaults import default_theme, is_bar, normal_theme, panel_model
from libre_panel.theme.model import theme_for

# The panels whose frame is 1920x480: both 8.8" (USB, and the serial V0.x one) and the 9.2"
BARS = [
    "turing-8.8",
    "turing-8.8-usb",
    "turing-9.2-usb",
    "jonsbo-zc360",
    "jonsbo-zc360-screen",  # 640x180: 3.6 times
    "jonsbo-ds916",
    "hongtai-1920x462",
    "lianli-universal-8.8",
    "lianli-universal-9.2",
    "thermalright-trofeo-9.16",
    "thermalright-trofeo-11.3",
    "beadapanel-8",
    "beadapanel-9",
    "beadapanel-11",
]
# the round ones of 480x480, which Lumen has a round version for
ROUND_LUMEN = [m.id for m in MODELS if m.shape == "round" and m.size("landscape") == (480, 480)]
OTHERS = [model.id for model in MODELS if model.id not in BARS + ROUND_LUMEN]
NINE, THREE, FIVE = (get_model(id) for id in ("turing-9.2-usb", "turing-3.5", "turing-5"))


def nothing_on_the_bus(monkeypatch):
    """``present_model`` that fails the test if anything asks it."""

    def asked():
        raise AssertionError("the USB bus was looked at")

    monkeypatch.setattr(turzx_usb, "present_model", asked)


# -- the rule ------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", BARS)
def test_a_bar_gets_lumen(model):
    assert default_theme(get_model(model)) == "lumen" and is_bar(get_model(model))


def test_the_nine_inch_bar_is_a_bar_whatever_its_bezel_hides():
    nine = get_model("turing-9.2-usb")
    assert nine.hidden != (0, 0, 0, 0)  # 18 of its 480 rows are behind the bezel
    assert default_theme(nine) == "lumen"
    assert default_theme(replace(nine, hidden=(0, 90, 0, 0))) == "lumen"  # a frame is 1920x480


@pytest.mark.parametrize("model", OTHERS)
def test_every_other_panel_gets_libre_default(model):
    assert default_theme(get_model(model)) == "libre-default" and not is_bar(get_model(model))


def test_a_panel_that_is_not_known_gets_libre_default():
    assert default_theme(None) == "libre-default" and not is_bar(None)


def test_the_12_inch_panel_is_not_a_bar():
    twelve = get_model("turing-12.3-usb")
    assert twelve.size("landscape") == (1920, 720)  # 2.67 times as long as it is high
    assert default_theme(twelve) == "libre-default"


def test_small_and_portrait_panels_get_libre_default_and_round_ones_round_lumen():
    for model in ("turing-3.5", "usbpcmonitor-3.5", "xuanfang-3.5", "kipye-3.5", "weact-3.5"):
        assert default_theme(get_model(model)) == "libre-default", model
    assert default_theme(get_model("turing-5")) == "libre-default"
    assert default_theme(get_model("usbpcmonitor-7")) == "libre-default"
    for round_one in ("turing-2.1", "turing-2.8-usb", "waveshare-2.8-udisp"):
        # (Lumen has a round version of their 480x480: Libre Default's corners would be cut off)
        assert get_model(round_one).shape == "round"
        assert default_theme(get_model(round_one)) == "lumen"
        assert theme_for("lumen", "landscape", (480, 480)) == "lumen-480x480"
    portrait = get_model("turing-3.5")
    assert portrait.size("portrait") == (320, 480)  # what the Column theme is made for
    assert default_theme(portrait) == "libre-default"
    assert default_theme(get_model("weact-0.96")) == "libre-default"  # 160x80: 2 times


def test_the_rule_is_the_shape_of_the_frame_whichever_way_the_panel_is_mounted():
    """A bar is a bar standing up too: the panel decides, not the theme's orientation."""
    for model in MODELS:
        long, short = max(model.size("landscape")), min(model.size("landscape"))
        standing = model.size("portrait")
        bar_or_round = long >= 3.5 * short or model.id in ROUND_LUMEN
        assert (default_theme(model) == "lumen") == bar_or_round, model.id
        assert (max(standing) >= 3.5 * min(standing)) == is_bar(model), model.id
    assert {model.id for model in MODELS if is_bar(model)} == set(BARS)  # a new model decides here


# -- which panel a config is for ------------------------------------------------------------------


def test_the_configured_model_is_the_panel_and_nothing_is_looked_for(monkeypatch):
    nothing_on_the_bus(monkeypatch)
    assert panel_model(DeviceConfig(model="turing-9.2-usb")) is NINE
    assert panel_model(DeviceConfig(model="turing-5", driver="virtual")) is FIVE
    assert panel_model(DeviceConfig(model="turing-5"), reported=NINE) is FIVE  # configured wins
    assert panel_model(DeviceConfig(model="no-such-panel")) is None  # (a config refuses it)


def test_a_display_that_said_which_panel_it_is_settles_an_automatic_model(monkeypatch):
    nothing_on_the_bus(monkeypatch)
    assert panel_model(DeviceConfig(), reported=THREE) is THREE


@pytest.mark.parametrize("driver", ["auto", "turzx"])
def test_with_an_automatic_model_the_usb_bus_is_asked_for_the_panel(monkeypatch, driver):
    monkeypatch.setattr(turzx_usb, "present_model", lambda *_: NINE)
    assert panel_model(DeviceConfig(driver=driver)) is NINE
    monkeypatch.setattr(turzx_usb, "present_model", lambda *_: None)
    assert panel_model(DeviceConfig(driver=driver)) is None


def test_the_usb_bus_is_found_by_ids_and_never_opened(monkeypatch):
    import usb.core

    seen = []

    def find(find_all, idVendor, idProduct, backend):
        seen.append(idProduct)
        return [SimpleNamespace(bus=1, address=4)] if idProduct == 0x0092 else []

    def opened(*args):
        pytest.fail("a panel was opened")

    monkeypatch.setattr(usb.core, "find", find)
    monkeypatch.setattr(turzx_usb, "_backend", lambda: None)
    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(opened))
    assert panel_model(DeviceConfig()) is NINE and 0x0092 in seen


@pytest.mark.parametrize("driver", ["virtual", "a-plugins-driver"])
def test_a_driver_that_does_not_look_for_a_turzx_panel_has_no_bus_to_ask(monkeypatch, driver):
    nothing_on_the_bus(monkeypatch)
    assert panel_model(DeviceConfig(driver=driver)) is None


def test_a_bus_that_cannot_be_read_is_no_panel(monkeypatch):
    def broken(*args):
        raise OSError("no libusb")

    monkeypatch.setattr(turzx_usb, "present_model", broken)
    assert panel_model(DeviceConfig()) is None and default_theme(panel_model(DeviceConfig())) == (
        "libre-default"
    )


def test_serial_ports_are_not_looked_at(monkeypatch):
    from libre_panel.devices import serial_link

    def touched(*args, **kwargs):
        raise AssertionError("a serial port was looked at")

    monkeypatch.setattr(serial_link, "find_port", touched)
    monkeypatch.setattr(serial_link, "_ports", touched)
    assert panel_model(DeviceConfig()) is None  # nothing on the USB bus, and that is all it tries


def test_the_normal_theme_is_the_one_named_else_the_panels_default(monkeypatch):
    nothing_on_the_bus(monkeypatch)
    named = Config(theme="slate", device=DeviceConfig(model="turing-9.2-usb"))
    assert named.theme_given and normal_theme(named) == "slate"  # asked nothing
    assert normal_theme(named, running="studio") == "slate"
    bar = Config(device=DeviceConfig(model="turing-9.2-usb"))
    assert not bar.theme_given and normal_theme(bar) == "lumen"
    assert normal_theme(Config(device=DeviceConfig(model="turing-3.5"))) == "libre-default"
    assert normal_theme(bar, running="libre-default") == "libre-default"  # the running app knows
    monkeypatch.setattr(turzx_usb, "present_model", lambda *_: NINE)
    assert normal_theme(Config()) == "lumen"  # a bar on the bus
    assert normal_theme(Config(), running="libre-default") == "libre-default"


# -- config.toml ----------------------------------------------------------------------------------


def test_a_theme_in_config_toml_is_given_and_none_is_the_panels_default(isolated_home):
    from libre_panel.config import parse_config

    assert parse_config({}).theme_given is False and parse_config({}).theme == "libre-default"
    named = parse_config({"theme": "libre-default"})  # what 0.4 wrote: kept, not migrated
    assert named.theme_given is True and named.theme == "libre-default"
    assert parse_config({"theme": "slate"}).theme_given is True
    assert load_config().theme_given is False  # no file at all
    assert Config().theme_given is False and Config().theme == "libre-default"
    assert Config(theme="slate").theme_given is True  # a config made in code that names one
    assert Config(theme="slate", theme_given=False).theme_given is False  # unless it says not


def test_the_template_names_no_theme_and_explains_the_default(isolated_home):
    import tomllib

    from libre_panel.config import DEFAULT_CONFIG_TOML, parse_config

    assert "theme" not in tomllib.loads(DEFAULT_CONFIG_TOML)
    lines = [line.strip() for line in DEFAULT_CONFIG_TOML.splitlines()]
    assert not [line for line in lines if line.startswith("theme")]  # no line that sets it
    assert any(line.startswith("# theme = ") for line in lines)  # how to set one
    text = DEFAULT_CONFIG_TOML
    assert "Lumen" in text and "Libre Default" in text and "1920x480" in text
    config = parse_config(tomllib.loads(DEFAULT_CONFIG_TOML))
    assert config.theme_given is False and config.theme == "libre-default"


def test_config_init_and_the_first_write_do_not_pin_a_theme(isolated_home):
    from libre_panel.config import set_brightness, theme_chosen, write_default_config

    path = write_default_config()  # libre-panel config init
    assert not theme_chosen(path) and load_config(path).theme_given is False
    path.unlink()
    set_brightness(30)  # the app's first write makes the file
    assert path.is_file() and load_config(path).device.brightness == 30
    assert not theme_chosen(path) and load_config(path).theme_given is False


def test_theme_chosen_means_that_config_toml_names_a_theme(isolated_home):
    from libre_panel.config import set_active_theme, theme_chosen, write_default_config

    assert theme_chosen() is False  # a first start: no file
    path = write_default_config()
    assert theme_chosen(path) is False  # a file without a theme line
    set_active_theme("lumen", path)  # a choice in the app, or from the tray
    assert theme_chosen(path) is True and load_config(path).theme_given is True
    assert load_config(path).theme == "lumen"
    path.write_text('theme = "libre-default"\n', encoding="utf-8")
    assert theme_chosen(path) is True  # 0.4's file: a choice, whatever it is
    path.write_text("this is not toml", encoding="utf-8")
    assert theme_chosen(path) is False
    path.write_text('# theme = "slate"\n', encoding="utf-8")
    assert theme_chosen(path) is False  # a comment names nothing


def test_a_setting_goes_under_the_commented_line_that_explains_it(isolated_home):
    from libre_panel.config import set_active_theme, set_config_value, write_default_config

    path = write_default_config()
    set_active_theme("lumen", path)  # a theme picked in the app
    set_config_value("video.local_clip", "usr/data/clip.h264", path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Libre Panel configuration"  # not above the title
    assert lines[lines.index('theme = "lumen"') - 1] == '# theme = "slate"'
    assert lines[lines.index('local_clip = "usr/data/clip.h264"') - 1] == '# local_clip = ""'
    assert lines.count('theme = "lumen"') == 1
    config = load_config(path)
    assert (config.theme, config.device.video.local_clip) == ("lumen", "usr/data/clip.h264")
    set_config_value("theme", "orbit", path)  # a second choice replaces the line in place
    assert [line for line in path.read_text().splitlines() if line.startswith("theme")] == [
        'theme = "orbit"'
    ]


def test_taking_the_theme_out_brings_the_panels_default_back(isolated_home):
    from libre_panel.config import edit_config, set_active_theme, write_default_config

    path = write_default_config()
    set_active_theme("slate", path)
    assert load_config(path).theme == "slate"
    edit_config(("unset", "theme", None), path=path)
    assert load_config(path).theme_given is False
    assert "# Libre Panel configuration" in path.read_text(encoding="utf-8")


def test_run_theme_always_wins(monkeypatch, isolated_home):
    from libre_panel import cli

    seen = []
    monkeypatch.setattr(
        app_module, "run", lambda config, **kwargs: seen.append((config.theme, config.theme_given))
    )
    monkeypatch.setattr("libre_panel.instance.InstanceLock", _NoLock)
    isolated_home.mkdir(parents=True)
    (isolated_home / "config.toml").write_text(
        '[device]\nmodel = "turing-9.2-usb"\ndriver = "virtual"\n', encoding="utf-8"
    )
    assert cli.main(["run", "--once", "--theme", "libre-default"]) == 0
    assert cli.main(["run", "--once"]) == 0
    assert seen == [("libre-default", True), ("libre-default", False)]


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

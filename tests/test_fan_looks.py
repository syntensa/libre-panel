"""The looks of a fan's rotor: Noctua's G2 (SPUR II's from Blender), the first NF-A12x25 and
NF-A14 and Libre Panel's own, drawn from their shape; chosen by a theme, or by the user for
all fans and for one."""

import tomllib

import numpy as np
import pytest

from libre_panel.config import ConfigError, parse_config
from libre_panel.render import fan, fan_looks
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import parse_theme

DRAWN = [name for name, look in fan_looks.LOOKS.items() if look.drawn]


def config(toml=""):
    return parse_config(tomllib.loads(toml))


def blades_around(alpha, radius):
    """How many blades a circle of ``radius`` (of the rotor's) crosses."""
    size = alpha.shape[0]
    angles = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    x = (size / 2 + radius * size / 2 * np.cos(angles)).astype(int)
    y = (size / 2 + radius * size / 2 * np.sin(angles)).astype(int)
    solid = alpha[y, x] > 0.5
    return int(np.sum(solid & ~np.roll(solid, 1)))


@pytest.mark.parametrize("name", DRAWN)
def test_a_drawn_rotor_has_its_blades_round_a_hub_and_loops_after_a_pitch(name):
    look = fan_looks.LOOKS[name]
    layers = fan_looks.rotor(look, 96, 0.0)
    assert layers.shape == (5, 96, 96)
    alpha = layers[3]
    assert alpha[48, 48] == pytest.approx(1.0)  # the hub
    assert alpha[0, 0] == 0 and alpha[2, 2] == 0  # round: nothing in the corners
    assert blades_around(alpha, 0.6) == blades_around(alpha, 0.85) == look.blades
    turned = fan_looks.rotor(look, 96, fan_looks.pitch(look))
    assert np.abs(turned[3] - alpha).mean() < 0.01  # a pitch on, it looks the same again
    assert 0.0 <= layers[:4].min() and layers[:4].max() <= 1.0001


def test_the_looks_are_as_their_fans_nine_blades_on_the_a12x25_seven_on_the_a14():
    assert fan_looks.LOOKS["noctua-a12x25"].blades == 9
    assert fan_looks.LOOKS["noctua-a14"].blades == 7
    assert fan_looks.LOOKS["noctua-g2"].blades == fan.BLADES == 9
    for name in DRAWN:
        layers = fan.rotor_layers(40, name)
        assert layers.shape == (fan.POSITIONS, 5, 40, 40)


def test_a_look_paints_in_the_themes_colour_or_its_own():
    assert fan.colours("noctua-a12x25", "own", (34, 211, 238)) == (
        fan_looks.NOCTUA_BROWN, fan_looks.NOCTUA_BEIGE,
    )  # fmt: skip
    assert fan.colours("noctua-a12x25", "own", (0, 0, 0), frame=True) == (
        fan_looks.NOCTUA_BEIGE, fan_looks.NOCTUA_BROWN,
    )  # fmt: skip
    main, second = fan.colours("libre", "theme", (34, 211, 238))
    assert main == (34, 211, 238) and all(b >= a for a, b in zip(main, second, strict=True))


def test_the_user_chooses_for_all_fans_and_for_one_and_a_theme_may_choose_for_its_fan():
    choices = fan.Choices.of(config(
        '[hardware]\nfan_look = "libre"\nfan_paint = "own"\n'
        '[[hardware.fans]]\nsensor = "fan.lpc.fan_2"\nlook = "noctua-a14"\n'
    ))  # fmt: skip
    assert choices.for_widget({"sensor": "fan.lpc.fan_1"}) == ("libre", "own")
    assert choices.for_widget({"sensor": "fan.lpc.fan_2", "look": "auto"}) == ("noctua-a14", "own")
    chosen = {"sensor": "fan.lpc.fan_2", "look": "noctua-a12x25", "paint": "theme"}
    assert choices.for_widget(chosen) == ("noctua-a12x25", "theme")  # the theme's own
    assert fan.Choices().for_widget({"sensor": "gpu.fan"}) == ("noctua-g2", "theme")  # as before


@pytest.mark.parametrize(
    "toml",
    [
        '[hardware]\nfan_look = "noctua-a15"\n',
        '[hardware]\nfan_paint = "brown"\n',
        '[[hardware.fans]]\nsensor = "fan.lpc.fan_1"\nlook = "rgb"\n',
    ],
)
def test_a_look_that_does_not_exist_is_refused_in_config_toml(toml):
    with pytest.raises(ConfigError):
        config(toml)


def test_a_fan_without_a_name_may_have_a_look_of_its_own_in_config_toml():
    hw = config(
        '[[hardware.fans]]\nsensor = "fan.lpc.fan_1"\nlook = "libre"\npaint = "own"\n'
    ).hardware
    assert hw.fans == {} and hw.fan_looks == {"fan.lpc.fan_1": "libre"}
    assert hw.fan_paints == {"fan.lpc.fan_1": "own"}


def theme_with(**fan_widget):
    widget = {"type": "fan", "id": "f", "x": 0, "y": 0, "w": 96, "h": 96, "sensor": "gpu.fan",
              "color": "#22d3ee", "frame": "square", **fan_widget}  # fmt: skip
    return parse_theme({"format": "libre-panel-theme/1", "display": {"width": 120, "height": 120},
                        "background": {"color": "#000000"}, "widgets": [widget]})  # fmt: skip


def test_a_fan_widget_turns_in_its_look_with_as_many_blades():
    snap = Snapshot(readings={"gpu.fan": Reading("gpu.fan", 1200.0, "RPM")})
    frames = {}
    for look in fan_looks.LOOKS:
        renderer = Renderer(theme_with(look=look, paint="own"))
        frames[look] = np.asarray(renderer.render(snap, 0.0)[0], float)
        assert (
            renderer.warnings == [] and renderer._rotors["f"].blades == fan_looks.look(look).blades
        )
    for one in frames:
        for other in frames:
            if one < other:
                assert np.abs(frames[one] - frames[other]).mean() > 1.0, (one, other)
    # Noctua's own colours: a beige frame round a brown rotor
    a12 = frames["noctua-a12x25"]
    frame = a12[3, 48]  # on the frame, between the pads
    assert frame[0] > frame[2] + 20  # beige: warm, not the theme's cyan
    middle = np.asarray(
        Renderer(theme_with(look="noctua-a12x25")).render(
            Snapshot(readings={"gpu.fan": Reading("gpu.fan", 1200.0, "RPM")}), 0.0
        )[0],
        float,
    )[3, 48]
    assert middle[2] > middle[0]  # in the theme's colour: cyan


def test_the_user_choice_reaches_the_panel_and_the_canvas():
    renderer = Renderer(theme_with())
    renderer.fan_choices = fan.Choices(look="libre")
    snap = Snapshot(readings={"gpu.fan": Reading("gpu.fan", 1200.0, "RPM")})
    renderer.render(snap, 0.0)
    assert renderer._rotors["f"].blades == 6

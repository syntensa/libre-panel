"""The demo source: what it makes up so that previews, pictures and the editor show every
screen and card complete."""

from libre_panel.sensors.demo import DemoProvider, demo_logo, demo_snapshot

FIXED = 1_700_000_000


def test_the_demo_has_a_game_logo_that_is_drawn_when_asked_and_not_read_from_a_file():
    logo = demo_snapshot(fixed_time=FIXED).images["game.logo"]
    assert logo.mode == "RGBA" and logo.size == (240, 144)  # a cut-out logo: clear around it
    alpha = logo.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0 and alpha.getextrema() == (0, 255)
    assert logo is DemoProvider().images()["game.logo"]  # made once
    assert demo_logo(120, 72).size == (120, 72)  # in any size
    assert demo_logo().tobytes() == demo_logo().tobytes()  # the same picture every time

"""The demo source: what it makes up so that previews, pictures and the editor show every
screen and card complete."""

from libre_panel.sensors.demo import (
    DemoProvider,
    demo_app_icon,
    demo_downloads,
    demo_logo,
    demo_snapshot,
)

FIXED = 1_700_000_000


def test_the_demo_has_a_game_logo_that_is_drawn_when_asked_and_not_read_from_a_file():
    logo = demo_snapshot(fixed_time=FIXED).images["game.logo"]
    assert logo.mode == "RGBA" and logo.size == (240, 144)  # a cut-out logo: clear around it
    alpha = logo.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0 and alpha.getextrema() == (0, 255)
    assert logo is DemoProvider().images()["game.logo"]  # made once
    assert demo_logo(120, 72).size == (120, 72)  # in any size
    assert demo_logo().tobytes() == demo_logo().tobytes()  # the same picture every time


def test_the_demo_has_a_player_icon_that_is_drawn_when_asked_and_not_read_from_a_file():
    icon = demo_snapshot(fixed_time=FIXED).images["media.app_icon"]
    assert icon.mode == "RGBA" and icon.size == (96, 96)  # as large as the icons a source reads
    alpha = icon.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((48, 90)) == 255  # a rounded square
    assert icon is DemoProvider().images()["media.app_icon"]  # made once
    assert demo_app_icon(48).size == (48, 48)  # in any size
    assert demo_app_icon().tobytes() == demo_app_icon().tobytes()  # the same picture every time
    assert icon.getpixel((48, 4))[:3] != icon.getpixel((48, 92))[:3]  # it shades, blue to violet


def test_the_demo_has_a_game_that_downloads_in_the_state_the_service_publishes():
    snapshot = demo_snapshot(fixed_time=FIXED)
    state = snapshot.state("downloads")
    assert {"id", "source", "name", "phase", "bytes", "total", "rate", "remaining_s", "elapsed_s",
            "estimated", "history", "more"} == set(state)  # fmt: skip
    assert state["phase"] == "downloading" and state["source"] == "steam"
    assert state["name"] == "Space Game" and not state["estimated"] and state["more"] == 0
    assert 0 < state["bytes"] < state["total"] and state["rate"] > 0
    assert len(state["history"]) == 60 and state["history"][-1] == state["rate"]
    left = (state["total"] - state["bytes"]) / state["rate"]
    assert state["remaining_s"] == round(left, 1) and state["elapsed_s"] > 0
    assert "downloads" not in snapshot.history  # a state: never in a graph's history
    assert snapshot.state("downloads") == demo_snapshot(fixed_time=FIXED).state("downloads")
    later = demo_downloads(FIXED + 300)["downloads"]  # it goes on with the time
    assert snapshot.value("downloads") != later.value
    assert later.value.startswith("{") and (later.unit, later.label) == ("", "Downloads")
    assert "downloads" not in DemoProvider({"fixed_time": FIXED}).read()  # (a service's state)

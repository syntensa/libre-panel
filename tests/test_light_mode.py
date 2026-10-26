"""Light mode (libre_panel.light): what only the app needs goes once it has been closed for a
minute, while the setting is on; once per time the app was open."""

import threading

import pytest
from test_editor_server import request

from libre_panel import light
from libre_panel.config import parse_config
from libre_panel.render import facets
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watch(on=True, last=None):
    state = {"on": on, "last": last, "released": 0, "closed": 0}
    clock = Clock()
    w = light.Watch(
        light=lambda: state["on"],
        last_request=lambda: state["last"],
        release=lambda: state.__setitem__("released", state["released"] + 1),
        clock=clock,
        closed=lambda: state.__setitem__("closed", state["closed"] + 1),
    )
    return w, state, clock


@pytest.mark.parametrize("on", [False, True])
def test_the_apps_own_pictures_go_a_minute_after_it_was_closed_light_mode_or_not(on):
    """Its pictures of the themes are the app's alone: they go once it has been closed for a
    minute, with light mode off too, once per time it was open; light mode lets go of more."""
    w, state, clock = watch(on=on, last=1000.0)
    clock.now = 1000 + light.IDLE_S - 1
    w.look()
    assert state["closed"] == 0  # (still open, or just closed)
    clock.now = 1000 + light.IDLE_S
    assert w.look() is on and state["closed"] == 1 and state["released"] == int(on)
    clock.now += 600
    w.look()
    assert state["closed"] == 1 and state["released"] == int(on)
    state["last"] = clock.now  # opened again, and closed
    clock.now += light.IDLE_S
    w.look()
    assert state["closed"] == 2 and state["released"] == 2 * int(on)


def test_the_memory_goes_a_minute_after_the_app_was_closed_and_once():
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S - 1
    assert not w.look() and state["released"] == 0  # (still open, or just closed)
    clock.now = 1000 + light.IDLE_S
    assert w.look() and state["released"] == 1
    clock.now += 600
    assert not w.look() and state["released"] == 1  # once for the time it was open
    state["last"] = clock.now  # the app is opened again ...
    clock.now += light.IDLE_S
    assert w.look() and state["released"] == 2  # ... and closed


@pytest.mark.parametrize(("on", "last"), [(False, 1000.0), (True, None)])
def test_nothing_goes_with_light_mode_off_or_before_the_app_was_ever_open(on, last):
    w, state, clock = watch(on=on, last=last)
    clock.now += 10 * light.IDLE_S
    assert not w.look() and state["released"] == 0


def test_release_empties_the_renderers_caches_and_the_apps_own():
    renderer = Renderer(load_theme(find_theme("lumen")), preview=True)
    renderer.render(demo_snapshot(fixed_time=1_700_000_000))
    renderer.close()
    assert len(facets._glyphs) or len(facets._patches) or len(facets._images)
    told = []
    light.release(lambda: told.append(True))
    assert told == [True] and len(facets._glyphs) == len(facets._patches) == 0
    light.give_back("win32")  # (no Windows here: it says so in the log, no error)


def test_the_setting_is_off_unless_set():
    assert parse_config({}).light_mode is False
    assert parse_config({"light_mode": True}).light_mode is True


def test_only_a_page_of_this_run_counts_as_the_app_being_open(isolated_home):
    from libre_panel.editor.server import make_server

    httpd = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    state = httpd.editor_state
    try:
        assert state.last_request is None
        request(port, "GET", "/api/app", headers={"X-Libre-Panel": "w" * 43})  # an old tab
        assert state.last_request is None
        request(port, "GET", "/api/settings")
        assert state.last_request is not None
    finally:
        httpd.shutdown()
        httpd.server_close()
        state.close()


def test_the_app_lets_go_of_its_pictures_of_the_themes_and_of_the_communitys(isolated_home):
    from libre_panel.service import EditorService

    service = EditorService(port=0)
    service.start()
    try:
        state = service._server.editor_state
        state.previews[("lumen", 1, 480)] = b"\x89PNG" + bytes(1000)
        state.community.previews[("aurora", "preview.png")] = (0.0, b"\x89PNG" + bytes(1000))
        service.forget_pictures()
        assert state.previews == {} and state.community.previews == {}
    finally:
        service.stop()

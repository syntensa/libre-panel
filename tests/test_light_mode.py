"""Light mode (libre_panel.light): what only the app needs goes once it has been closed for a
minute (what the panel draws with stays), and the memory goes back while the setting is on; once
per time the app was open."""

import threading

import pytest
from test_editor_server import request

from libre_panel import light
from libre_panel.config import parse_config
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watch(on=True, last=None):
    state = {"on": on, "last": last, "released": 0, "closed": 0, "marked": 0, "shed": 0,
             "unmarked": 0}  # fmt: skip

    def count(name):
        return lambda: state.__setitem__(name, state[name] + 1)

    clock = Clock()
    w = light.Watch(
        light=lambda: state["on"],
        last_request=lambda: state["last"],
        release=count("released"),
        clock=clock,
        closed=count("closed"),
        mark=count("marked"),
        shed=count("shed"),
        unmark=count("unmarked"),
    )
    return w, state, clock


@pytest.mark.parametrize("on", [False, True])
def test_the_apps_own_pictures_go_a_minute_after_it_was_closed_light_mode_or_not(on):
    """Its pictures of the themes are the app's alone: they go once it has been closed for a
    minute, with light mode off too, once per time it was open; light mode lets go of more."""
    w, state, clock = watch(on=on, last=1000.0)
    clock.now = 1000 + light.IDLE_S - 1
    w.look()
    assert state["closed"] == state["marked"] == 0  # (still open, or just closed)
    clock.now = 1000 + light.IDLE_S
    assert not w.look() and state["closed"] == state["marked"] == 1 and state["shed"] == 0
    clock.now += light.LOOK_S  # the caches noted what the panel draws with meanwhile
    assert w.look() is on and state["shed"] == 1 and state["released"] == int(on)
    clock.now += 600
    w.look()
    assert state["closed"] == state["shed"] == 1 and state["released"] == int(on)
    state["last"] = clock.now  # opened again, and closed
    clock.now += light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    w.look()
    assert state["closed"] == state["shed"] == 2 and state["released"] == 2 * int(on)


def test_the_memory_goes_a_minute_after_the_app_was_closed_and_once():
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S - 1
    assert not w.look() and state["released"] == 0  # (still open, or just closed)
    clock.now = 1000 + light.IDLE_S
    assert not w.look() and state["released"] == 0  # (the caches note what the panel uses)
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 1
    clock.now += 600
    assert not w.look() and state["released"] == 1  # once for the time it was open
    state["last"] = clock.now  # the app is opened again ...
    clock.now += light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 2  # ... and closed


def test_the_caches_keep_all_when_the_app_is_opened_again_before_they_let_go():
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S
    w.look()
    assert state["marked"] == 1
    state["last"] = clock.now + 5  # opened again
    clock.now += light.LOOK_S
    assert not w.look() and state["unmarked"] == 1 and state["shed"] == state["released"] == 0
    w.look()
    assert state["unmarked"] == 1


@pytest.mark.parametrize(("on", "last"), [(False, 1000.0), (False, None)])
def test_nothing_goes_with_light_mode_off(on, last):
    w, state, clock = watch(on=on, last=last)
    for _ in range(5):
        clock.now += 10 * light.IDLE_S
        assert not w.look()
    assert state["released"] == 0


def test_an_app_never_opened_lets_go_a_minute_after_the_start_and_once():
    """Libre Panel started at login, its window never opened: what only the start touched
    goes as it goes for an app closed (the caches keep what the panel draws with), once."""
    w, state, clock = watch(on=True, last=None)
    clock.now = w.began + light.IDLE_S - 1
    assert not w.look() and state["closed"] == 0
    clock.now = w.began + light.IDLE_S
    assert not w.look() and state["closed"] == 1 and state["marked"] == 1
    clock.now += light.LOOK_S
    assert w.look() and state["shed"] == 1 and state["released"] == 1
    for _ in range(5):
        clock.now += 600
        assert not w.look()
    assert state["released"] == 1 and state["closed"] == 1
    state["last"] = clock.now  # (opened now: once closed again, it goes again)
    clock.now += light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 2


def test_the_caches_let_go_of_what_only_the_apps_pictures_drew_with_and_keep_the_panels():
    from libre_panel.render import kept, symbols

    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    panel = Renderer(load_theme(find_theme("lumen")), preview=True)
    panel.render(snapshot)
    for other in ("arena", "almanac", "encore"):  # the app's pictures of other themes
        picture = Renderer(load_theme(find_theme(other)), preview=True)
        picture.render(snapshot)
        picture.close()
    held = kept.stats()
    light.mark()
    panel.render(snapshot)  # the panel draws on: what it draws with is noted
    light.shed()
    left = kept.stats()
    total = sum(row["bytes"] for row in held.values())
    assert sum(row["bytes"] for row in left.values()) < total / 2
    hits = kept.stats()
    panel.render(snapshot)  # ... and finds it all kept
    after = kept.stats()
    misses = sum(after[n]["misses"] - hits[n]["misses"] for n in after if n in hits)
    assert misses == 0
    panel.close()
    told = []
    light.release(lambda: told.append(True))  # light mode: also the caches that cannot tell
    assert told == [True] and len(symbols._sets) == 0
    light.give_back("win32")  # (no Windows here: it says so in the log, no error)


def test_a_cache_notes_what_is_used_and_lets_go_of_the_rest():
    from libre_panel.render.kept import Kept

    kept = Kept("test.shed", 1 << 20)
    for key in "abcd":
        kept.put(key, bytes(100))
    assert kept.shed() == 0 and len(kept) == 4  # (no mark: all stay)
    kept.mark()
    kept.get("a")
    kept.make("e", lambda: bytes(10))
    _ = kept["b"]
    assert kept.shed() > 0 and sorted(kept) == ["a", "b", "e"]
    kept.mark()
    kept.unmark()
    assert kept.shed() == 0 and len(kept) == 3


def test_the_setting_is_on_unless_set_off():
    assert parse_config({}).light_mode is True
    assert parse_config({"light_mode": False}).light_mode is False


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


def test_large_blocks_are_the_system_s_own_on_linux_in_the_app_and_its_engine(monkeypatch):
    """A block of a megabyte and more is mapped (glibc's M_MMAP_THRESHOLD, fixed), in the app as
    it starts and in the engine it starts (GLIBC_TUNABLES); what the environment says holds."""
    calls = []

    class Libc:
        gnu_get_libc_version = object()

        def mallopt(self, param, value):
            calls.append((param, value))
            return 1

    monkeypatch.setattr(light.ctypes, "CDLL", lambda name: Libc())
    for key in ("MALLOC_ARENA_MAX", "MALLOC_MMAP_THRESHOLD_", "GLIBC_TUNABLES"):
        monkeypatch.delenv(key, raising=False)
    assert light.fewer_heaps("linux") is True
    assert calls == [(-3, light.MAPPED_FROM), (-8, 2)]
    want = {"GLIBC_TUNABLES": f"glibc.malloc.mmap_threshold={light.MAPPED_FROM}"}
    assert light.mapped_blocks("linux") == want and light.mapped_blocks("win32") == {}
    calls.clear()
    assert light.fewer_heaps("win32") is False and not calls
    monkeypatch.setenv("GLIBC_TUNABLES", "glibc.malloc.arena_max=1")
    assert light.mapped_blocks("linux") == {}
    light.fewer_heaps("linux")
    assert calls == [(-8, 2)]  # (its own choice of the blocks holds)

    def windows_cdll(name):  # (as Windows' ctypes takes a library named None)
        raise TypeError("argument of type 'NoneType' is not iterable")

    monkeypatch.setattr(light.ctypes, "CDLL", windows_cdll)
    assert light.fewer_heaps("linux") is False  # (asked as on Linux, where there is no glibc)


def test_two_heaps_on_linux_as_the_app_starts(monkeypatch):
    """glibc's heaps per thread held a frame's memory each: two for all threads, set where the
    app starts (light.fewer_heaps), not as a side effect of importing Libre Panel."""
    assert light.fewer_heaps("win32") is False
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    assert light.fewer_heaps("linux") is False  # (one set in the environment holds)

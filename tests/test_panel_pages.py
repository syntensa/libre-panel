"""The pages Libre Panel switches to by itself (libre_panel.modes): music's page while a track
plays (Encore), the clock's when nobody has touched the PC for some minutes (Almanac); a game
goes first, and each is off until it is switched on."""

from types import SimpleNamespace

import pytest
from test_default_theme_loop import demo_config, loop_for, step_until

from libre_panel import modes as pages
from libre_panel.config import ConfigError, ModeConfig, parse_config
from libre_panel.plugins import PluginHost


def test_config_toml_switches_a_page_on_and_gives_the_clock_its_minutes():
    config = parse_config({"modes": {"media": {"on": True}, "idle": {"on": True, "minutes": 2}}})
    assert pages.page_on(config, "media") and pages.page_on(config, "idle")
    assert pages.idle_minutes(config) == 2.0
    assert pages.page_theme(config, "media") == "encore"  # its own screen, unless one is named
    assert pages.page_theme(config, "idle") == "almanac"
    assert pages.page_theme(config, "game") is None  # game mode names none: the normal theme
    off = parse_config({})
    assert not pages.page_on(off, "media") and pages.idle_minutes(off) == pages.IDLE_MINUTES
    named = parse_config({"modes": {"idle": {"on": True, "theme": "almanac-analog"}}})
    assert pages.page_theme(named, "idle") == "almanac-analog"
    for table, says in (({"on": "yes"}, "modes.idle.on"), ({"minutes": 0}, "between"),
                        ({"minutes": "5"}, "a number of minutes")):  # fmt: skip
        with pytest.raises(ConfigError, match=says):
            parse_config({"modes": {"idle": table}})


def switch(**modes):
    clock = SimpleNamespace(now=100.0)
    idle = SimpleNamespace(seconds=0.0)
    found = pages.PageSwitch(lambda: clock.now, lambda: idle.seconds)
    config = demo_config(modes={name: ModeConfig(on=True) for name in modes})
    return found, config, clock, idle


def playing(on):
    return {"media.playing": SimpleNamespace(value=1 if on else 0)}


def test_music_has_its_page_while_it_plays_and_a_moment_after():
    found, config, clock, _ = switch(media=True)
    assert found.due(config, playing(False)) is None
    assert found.due(config, playing(True)) == "media"
    clock.now += pages.MEDIA_LEAVE_S - 1  # the gap between two tracks: it stays
    assert found.due(config, playing(False)) == "media"
    clock.now += 2
    assert found.due(config, playing(False)) is None
    assert {"media.playing", "media.title"} <= found.reads  # the media source runs for it


def test_the_clock_has_its_page_once_the_pc_has_been_idle_for_its_minutes():
    found, config, clock, idle = switch(idle=True)
    idle.seconds = pages.IDLE_MINUTES * 60 - 1
    assert found.due(config, {}) is None
    clock.now += pages.IDLE_EVERY_S
    idle.seconds = pages.IDLE_MINUTES * 60
    assert found.due(config, {}) == "idle"
    clock.now += pages.IDLE_EVERY_S
    idle.seconds = 0.4  # a key: back at once
    assert found.due(config, {}) is None
    idle.seconds = None  # a system that cannot say: never idle
    clock.now += pages.IDLE_EVERY_S
    assert found.due(config, {}) is None and not found.reads


def test_music_goes_before_the_clock_and_a_page_that_is_off_never_shows():
    found, config, clock, idle = switch(media=True, idle=True)
    idle.seconds = 3600
    assert found.due(config, playing(True)) == "media"
    clock.now += pages.MEDIA_LEAVE_S + 1
    assert found.due(config, playing(False)) == "idle"
    config.modes["idle"].on = False
    config.modes["media"].on = False
    assert found.due(config, playing(True)) is None and not found.reads


def test_the_panel_shows_the_music_page_and_a_game_goes_first(isolated_home):
    host = PluginHost()
    config = demo_config(theme="slate", modes={"media": ModeConfig(on=True)})
    loop, clock = loop_for(config, host=host)  # (the demo plays a track)
    try:
        assert step_until(loop, clock, lambda: loop.status.theme == "encore")
        assert host.mode == "media" and host.mode_owner == pages.OWNER
        host.set_mode("game", by="game")  # a game: its mode, and no page over it
        assert step_until(loop, clock, lambda: loop.status.theme == "slate")
        assert host.mode == "game"
        host.set_mode(None, by="game")  # the game is over: the music's page again
        assert step_until(loop, clock, lambda: loop.status.theme == "encore")
        config.modes["media"].on = False  # switched off: the normal theme
        assert step_until(loop, clock, lambda: loop.status.theme == "slate")
        assert host.mode is None
    finally:
        loop.close()
        host.close()


def test_the_panel_shows_the_clock_when_the_pc_is_idle(isolated_home):
    host = PluginHost()
    clock_page = ModeConfig(on=True, minutes=1, theme="almanac-analog")
    config = demo_config(theme="slate", modes={"idle": clock_page})
    loop, clock = loop_for(config, host=host)
    away = SimpleNamespace(seconds=0.0)
    loop.pages.idle = lambda: away.seconds
    try:
        loop.step(clock.now)
        assert loop.status.theme == "slate"
        away.seconds = 61
        assert step_until(loop, clock, lambda: loop.status.theme == "almanac-analog")
        away.seconds = 0.1
        assert step_until(loop, clock, lambda: loop.status.theme == "slate")
    finally:
        loop.close()
        host.close()


def test_logind_says_how_long_the_session_has_been_idle(monkeypatch):
    import time

    monkeypatch.setenv("XDG_SESSION_ID", "3")
    now = time.clock_gettime(time.CLOCK_MONOTONIC)
    since = int((now - 90) * 1e6)

    def run(command, **_):
        assert command[:3] == ["loginctl", "show-session", "3"]
        return SimpleNamespace(stdout=f"IdleHint=yes\nIdleSinceHintMonotonic={since}\n")

    assert 89 <= pages._logind_idle(run) <= 92
    busy = SimpleNamespace(stdout="IdleHint=no\nIdleSinceHintMonotonic=0\n")
    assert pages._logind_idle(lambda command, **_: busy) == 0.0
    monkeypatch.delenv("XDG_SESSION_ID")
    assert pages._logind_idle(run) is None

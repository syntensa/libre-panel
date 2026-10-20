"""The panel sleeps with the computer: Windows says it goes to sleep, the panel loop ends and
the panel goes dark ([device] dark_on_sleep); it starts again when the computer wakes."""

import threading

from test_service import wait_for

from libre_panel import service
from libre_panel.config import parse_config


def panel_service(monkeypatch, tmp_path):
    """A PanelService whose loop only waits to be stopped, and says why it ended."""
    ended = []

    def loop(config, stop, quitting, **_):
        stop.wait(10)
        ended.append(quitting())

    monkeypatch.setattr(service, "run", loop)
    monkeypatch.setattr(service, "load_config", lambda path=None: parse_config({}))
    return service.PanelService(tmp_path / "config.toml"), ended


def test_the_panel_sleeps_with_the_computer_and_wakes_with_it(monkeypatch, tmp_path):
    panel, ended = panel_service(monkeypatch, tmp_path)
    panel.start()
    assert wait_for(lambda: panel.running)
    assert panel.sleep() and ended == ["sleep"] and not panel.running
    assert not panel.sleep()  # (twice: nothing more)
    assert panel.wake() and wait_for(lambda: panel.running)
    assert not panel.wake()  # it was not asleep
    panel.stop()
    assert ended == ["sleep", True]  # Libre Panel quit


def test_a_paused_panel_stays_as_it_is(monkeypatch, tmp_path):
    panel, ended = panel_service(monkeypatch, tmp_path)
    panel.start()
    assert wait_for(lambda: panel.running)
    panel.pause()
    assert not panel.sleep() and not panel.wake() and not panel.running
    assert ended == [None]  # the pause: no reason to go dark


def test_the_setting_is_read_and_on_unless_turned_off():
    assert parse_config({}).device.dark_on_sleep is True
    assert parse_config({"device": {"dark_on_sleep": False}}).device.dark_on_sleep is False


def test_the_session_window_tells_sleep_and_wake():
    from libre_panel import winsession

    calls = []
    watcher = object.__new__(winsession.SessionEndWatcher)  # (no window: not on Windows)
    watcher.on_sleep, watcher.on_wake = (
        (lambda: calls.append("sleep")),
        (lambda: calls.append("wake")),
    )
    for event in (winsession.PBT_APMSUSPEND, winsession.PBT_APMRESUMEAUTOMATIC,
                  winsession.PBT_APMRESUMESUSPEND, 0x000A):  # fmt: skip
        watcher._power(event)
    assert calls == ["sleep", "wake", "wake"]  # (0x0A: the power source changed)
    assert threading.current_thread() is threading.main_thread()


def test_a_loop_that_stops_late_has_the_panel_to_itself_until_it_ends(monkeypatch, tmp_path):
    """A restart (pause, then resume) whose old loop has not let the panel go yet: the new
    loop waits for it, else both would open the panel's port (#7)."""
    running, release = [], threading.Event()

    def loop(config, stop, quitting, **_):
        running.append(threading.current_thread())
        stop.wait(10)
        release.wait(10)  # (still writing a frame)
        running.remove(threading.current_thread())

    monkeypatch.setattr(service, "run", loop)
    monkeypatch.setattr(service, "load_config", lambda path=None: parse_config({}))
    panel = service.PanelService(tmp_path / "config.toml")
    monkeypatch.setattr(panel, "HALT_S", 0.05)
    monkeypatch.setattr(panel, "LINGER_S", 0.05)
    panel.start()
    assert wait_for(lambda: len(running) == 1)
    panel.pause()  # it did not stop in time
    panel.resume()
    assert len(running) == 1 and not panel.running  # no second loop beside it
    release.set()
    assert wait_for(lambda: not running)
    panel.resume()
    assert wait_for(lambda: len(running) == 1)
    panel.stop()

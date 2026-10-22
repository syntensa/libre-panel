"""A panel beside the first in a process of its own (the background app): it shows its theme
on the first panel's readings, says how it is doing and what it shows, its log lines go to the
first's log, and it ends when told to; where a process cannot start, a thread runs it."""

import logging
import threading

from fake_sensors import wait_for

from libre_panel import app, identify
from libre_panel.config import load_config
from libre_panel.devices import base
from libre_panel.panel_process import RemoteStatus
from libre_panel.render import fan
from libre_panel.sensors.base import wanted

CONFIG = """theme = "lumen"
fps = 10
[device]
driver = "virtual"
output = "first.png"
[sensors]
providers = ["demo"]
[[panels]]
driver = "virtual"
output = "second.png"
theme = "lumen-companion"
"""


def test_a_panel_beside_the_first_runs_in_a_process_of_its_own(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    stop, status = threading.Event(), app.RunStatus()
    told = {"stop": stop, "status": status, "apart": True}
    loop = threading.Thread(target=app.run, args=(load_config(path),), kwargs=told, daemon=True)
    caplog.set_level(logging.INFO)
    loop.start()
    try:
        assert wait_for(lambda: 0 in status.more, timeout=10)
        beside = status.more[0]
        assert isinstance(beside, RemoteStatus)
        # (a process of its own takes a few seconds to start: it imports what it draws with)
        assert wait_for(lambda: beside.state == "showing", timeout=90)
        assert beside.theme == "lumen-companion" and beside.target
        assert wait_for(lambda: beside.shown is not None, timeout=10)  # its picture, for Live
        assert beside.shown.size[0] > 0
        # (the picture is noted for Live before the panel's driver writes it)
        assert wait_for(lambda: (tmp_path / "second.png").exists(), timeout=10)
        said = [r for r in caplog.records if "in a process of its own" in r.getMessage()]
        assert said and said[0].threadName == "panel-2"  # its log lines, as its own
        # what its theme reads is wanted here too, where the sources are: Lumen shows no
        # processes, its companion does, so the processes are counted for it
        assert wait_for(lambda: wanted("proc."), timeout=10)
    finally:
        stop.set()
        loop.join(30)
    assert not loop.is_alive()
    assert not wanted("proc.")  # (its process ended: nobody wants them any more)


def test_a_panel_whose_process_cannot_start_runs_in_a_thread(tmp_path, monkeypatch):
    from libre_panel import panel_process

    def refused(*args, **kwargs):
        raise OSError("no processes here")

    monkeypatch.setattr(panel_process, "PanelProcess", refused)
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    more = app._MorePanels(None, app.RunStatus(), None, apart=True)
    monkeypatch.setattr(more, "_main", lambda *args: args[2].wait(5))
    more.follow(load_config(path))
    try:
        assert isinstance(more._runs[0], tuple) and 0 in more._in_threads
    finally:
        more.close()


def test_the_beats_and_which_is_which_go_across():
    first, second = fan.Beats(), fan.Beats()
    beat = first.of("gpu.fan", 50, 7)
    beat.begin(100.0)
    beat.turn(100.5, 2.0)
    second.adopt(first.export())
    there = second.of("gpu.fan", 50, 7)
    assert (there.angle, there.at) == (beat.angle, beat.at)  # the same fan in step
    identify.show(3.0)
    assert 2.5 < identify.left() <= 3.0
    identify.show(0.0)
    assert identify.left() == 0.0
    assert base.take_place("usb:9.9")
    try:
        assert "usb:9.9" in base.places_taken()
    finally:
        base.leave_place("usb:9.9")

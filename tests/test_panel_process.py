"""A panel beside the first in a process of its own (the background app): it shows its theme
on the first panel's readings, says how it is doing and what it shows, its log lines go to the
first's log, and it ends when told to; where a process cannot start, a thread runs it."""

import logging
import threading
import time

from fake_sensors import wait_for

from libre_panel import app, identify
from libre_panel.config import load_config
from libre_panel.devices import base
from libre_panel.load_report import LoadSampler
from libre_panel.panel_process import RemoteStatus, panel_pids
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
        # Live → Diagnose → Load calls its process "panel 2", and measures it
        assert list(panel_pids().values()) == [2]
        sampler = LoadSampler(service=lambda: None, window=lambda: None)
        sampler.sample()
        time.sleep(0.3)
        rows = sampler.sample()["processes"]
        panel = next(row for row in rows if row["role"] == "panel")
        assert panel["panel"] == 2 and panel["label"] == "panel 2" and panel["rss"] > 0
    finally:
        stop.set()
        loop.join(30)
    assert not loop.is_alive()
    assert not wanted("proc.")  # (its process ended: nobody wants them any more)
    assert panel_pids() == {}


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


# -- what a panel's process loads ----------------------------------------------------------------

# A panel's process as the first panel's process starts it (spawn): the program's entry point
# imports the command line first (libre_panel.cli, before multiprocessing hands over), then the
# process runs child(); here a thread plays the first panel's process at the pipe's other end.
CHILD = r"""
import json, sys, threading, time
from multiprocessing import Pipe, Queue

import libre_panel.cli  # (the entry point's import)
from libre_panel import panel_process
from libre_panel.sensors.base import Reading

ours, theirs = Pipe()
said = [{}]

def first():
    while True:
        try:
            message = ours.recv()
        except (EOFError, OSError):
            return
        if message[0] == "snapshot?":
            readings = {"cpu.load": Reading("cpu.load", 40.0, "%", "CPU"),
                        "cpu.temp": Reading("cpu.temp", 55.0, "°C", "CPU")}
            ours.send(("snapshot", readings, {}, 0.1, False))
        elif message[0] == "status":
            said.append(message[1])

threading.Thread(target=first, daemon=True).start()
ours.send(("state", {"mode": None, "first": "", "places": [], "quitting": None, "identify": 0,
                     "beats": {}, "taken": [], "colours": None}))
child = threading.Thread(target=panel_process.child, args=(0, sys.argv[1], theirs, Queue()),
                         daemon=True)
child.start()
end = time.monotonic() + 90
while time.monotonic() < end and not said[-1].get("frames"):
    time.sleep(0.1)
time.sleep(2.0)  # (it draws on a while)
shown = dict(said[-1])
ours.send(("stop",))
child.join(20)
print(json.dumps({"state": shown.get("state"), "frames": shown.get("frames", 0),
                  "modules": sorted(sys.modules)}))
"""

# what only the first panel's process does: the app's server and pages, the sensor sources (the
# first one's readings come across), the tray, the window, the memory's store, the services,
# .NET (LibreHardwareMonitor and the window through pythonnet)
NOT_IN_A_PANELS_PROCESS = (
    "libre_panel.editor", "libre_panel.service", "libre_panel.tray", "libre_panel.window",
    "libre_panel.services", "libre_panel.sensorservice", "libre_panel.weather",
    "libre_panel.load_report", "libre_panel.doctor", "libre_panel.hotkeys",
    "clr", "pythonnet", "clr_loader", "webview", "pystray", "winrt", "paho", "sqlite3",
    "http.server", "socketserver",
)  # fmt: skip
SENSORS_OF_ITS_OWN = {"libre_panel.sensors", "libre_panel.sensors.base",
                      "libre_panel.sensors.fan_names"}  # fmt: skip


def test_a_panels_process_loads_only_what_it_draws_with(tmp_path):
    """No server, no sensor source, no tray, no window, no .NET, no memory store: a panel's
    process draws its theme on the readings it is sent, and loads that alone."""
    import json
    import os
    import subprocess
    import sys

    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, "-c", CHILD, str(path)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=180)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["state"] == "showing" and found["frames"] >= 1  # it drew its theme
    modules = found["modules"]
    loaded = [m for m in modules for name in NOT_IN_A_PANELS_PROCESS
              if m == name or m.startswith(name + ".")]  # fmt: skip
    assert loaded == []
    sensors = {m for m in modules if m.startswith("libre_panel.sensors")}
    assert sensors <= SENSORS_OF_ITS_OWN
    assert "libre_panel.render.renderer" in modules and "libre_panel.devices.virtual" in modules

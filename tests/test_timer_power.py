"""The timer and the power service: what they publish, by a clock in the test's hand."""

import json
from datetime import datetime

import pytest

from libre_panel.config import Config, ServicesConfig
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.services import power, timer


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def host():
    made = PluginHost()
    yield made
    made.close()


def running(host, name, options, **clocks):
    """The service as the manager starts it, with the test's clocks put in."""
    manager = ServiceManager(host)
    manager.apply(Config(services=ServicesConfig([name], {name: options})))
    assert manager.state == {name: "running"}, manager.state
    service = manager.running[name][0]
    for key, value in clocks.items():
        setattr(service, key, value)
    return manager, service


def published(host, key):
    return host.published().get(key)


def value(host, key):
    reading = published(host, key)
    return None if reading is None else reading.value


# -- the timer --------------------------------------------------------------------------------


def test_the_timer_counts_down_pauses_and_says_when_the_time_is_up(host):
    clock = Clock()
    manager, service = running(host, "timer", {"minutes": 1}, clock=clock)
    try:
        assert value(host, "timer.text") == "01:00" and value(host, "timer.running") == 0
        assert value(host, "timer.phase") == "Timer"
        host.emit("shortcut", wait=True, action="timer")  # the keyboard shortcut starts it
        clock.now += 20.4
        service.tick()
        assert value(host, "timer.text") == "00:40" and value(host, "timer.running") == 1
        assert value(host, "timer.share") == pytest.approx(0.34, abs=0.01)
        service.toggle()  # paused: the time stands
        clock.now += 30
        service.tick()
        assert value(host, "timer.text") == "00:40" and value(host, "timer.running") == 0
        service.toggle()
        clock.now += 40
        service.tick()
        toasts = host.take_toasts()
        assert [t.text for t in toasts] == ["Time is up"] and toasts[0].kind == "timer"
        assert value(host, "timer.left") == 0 and value(host, "timer.rounds") == 1
        assert value(host, "timer.running") == 0
        service.toggle()  # once more: from the start
        assert value(host, "timer.text") == "01:00" and value(host, "timer.running") == 1
        host.emit("shortcut", wait=True, action="timer_reset")
        assert value(host, "timer.text") == "01:00" and value(host, "timer.running") == 0
    finally:
        manager.stop()


def test_focus_and_break_take_turns(host):
    clock = Clock()
    manager, service = running(host, "timer", {"minutes": 25, "break_minutes": 5}, clock=clock)
    try:
        assert value(host, "timer.phase") == "Focus"
        service.toggle()
        clock.now += 25 * 60
        service.tick()
        assert [t.text for t in host.take_toasts()] == ["Time for a break"]
        assert value(host, "timer.phase") == "Break" and value(host, "timer.running") == 1
        assert value(host, "timer.text") == "05:00"
        clock.now += 5 * 60
        service.tick()
        assert [t.text for t in host.take_toasts()] == ["The break is over"]
        assert value(host, "timer.phase") == "Focus" and value(host, "timer.running") == 0
        assert value(host, "timer.text") == "25:00"  # the next focus waits to be started
    finally:
        manager.stop()


def test_the_time_left_is_written_as_a_clock():
    assert timer.clock_text(0) == "00:00"
    assert timer.clock_text(0.2) == "00:01"  # a second begun counts
    assert timer.clock_text(25 * 60) == "25:00"
    assert timer.clock_text(3725) == "1:02:05"


# -- power use --------------------------------------------------------------------------------


def watts(cpu, gpu):
    readings = [Reading("cpu.power", cpu, "W"), Reading("gpu.power", gpu, "W")]
    return Snapshot(readings={r.key: r for r in readings if r.value is not None})


def test_power_adds_up_the_watts_and_counts_the_energy_of_the_day_and_the_month(host):
    clock, day = Clock(), datetime(2026, 10, 21, 23, 59, 0)
    options = {"rest_w": 50, "price": 0.30, "currency": "€"}
    manager, service = running(host, "power", options, clock=clock, wall=lambda: day)
    try:
        host.emit("sample", wait=True, snapshot=watts(100, 210))
        assert value(host, "power.watts") == 360 and value(host, "power.today") == 0
        for _ in range(3600):  # an hour of samples a second apart
            clock.now += 1
            host.emit("sample", wait=True, snapshot=watts(100, 210))
        assert value(host, "power.today") == pytest.approx(0.36, abs=0.001)
        assert value(host, "power.today.cost") == pytest.approx(0.11, abs=0.01)
        assert published(host, "power.today.cost").unit == "€"
        clock.now += 60  # the PC slept: no energy for the gap
        host.emit("sample", wait=True, snapshot=watts(100, 210))
        assert value(host, "power.today") == pytest.approx(0.36, abs=0.001)
        day = datetime(2026, 10, 22, 0, 0, 1)  # a new day: today begins at 0, the month goes on
        service.wall = lambda: day
        clock.now += 1
        host.emit("sample", wait=True, snapshot=watts(100, 210))
        assert value(host, "power.today") == 0
        assert value(host, "power.month") == pytest.approx(0.36, abs=0.001)
        host.emit("sample", wait=True, snapshot=watts(None, None))  # no sensor: no watts
        assert value(host, "power.watts") is None
    finally:
        manager.stop()


def test_the_totals_are_kept_for_a_restart_of_the_same_day(host, isolated_home):
    clock, day = Clock(), datetime(2026, 10, 21, 12, 0, 0)
    manager, service = running(host, "power", {}, clock=clock, wall=lambda: day)
    for _ in range(10):
        clock.now += 1
        host.emit("sample", wait=True, snapshot=watts(180, 0))
    manager.stop()  # stopping keeps them
    kept = json.loads((service.host.data_dir / power.FILE).read_text("utf-8"))
    assert kept["day"] == "2026-10-21" and kept["today_wh"] == pytest.approx(0.45, abs=0.01)
    again = power.Power(service.host, {"sensors": ["cpu.power"], "rest_w": 0.0, "price": 0.0,
                                       "currency": "€"}, wall=lambda: day)  # fmt: skip
    again._load()
    assert again.today_wh == pytest.approx(kept["today_wh"])
    later = power.Power(service.host, again.options, wall=lambda: datetime(2026, 11, 2))
    later._load()
    assert later.today_wh == 0 and later.month_wh == 0  # another day, another month

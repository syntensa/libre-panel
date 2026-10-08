"""Lists of readings, the sensors behind them and the modules built on them."""

import gc
import json
from collections import namedtuple
from pathlib import Path

import pytest

from libre_panel.render import lists
from libre_panel.render.formatting import auto_format
from libre_panel.render.renderer import Renderer
from libre_panel.sensors import base, psutil_provider
from libre_panel.sensors.base import Reading, want, wanted
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.sensors.librehardwaremonitor import readings_from_tree
from libre_panel.theme.model import parse_theme

DATA = Path(__file__).parent / "data"


def readings(**values):
    out = {}
    for key, value in values.items():
        key = key.replace("__", ".")
        if isinstance(value, tuple):
            out[key] = Reading(key, *value)
        else:
            out[key] = Reading(key, value, "", "")
    return out


def picked(items, found, **widget):
    return [(i.key, i.name) for i in lists.pick({"items": items, **widget}, found)]


def test_items_by_name_pattern_and_exclusion():
    found = readings(
        cpu__temp=(61.0, "°C", "CPU temperature", "temp.k10temp.tctl"),
        temp__k10temp__tctl=(61.0, "°C", "CPU Tctl"),
        temp__nvme__composite=(40.0, "°C", "NVMe Composite"),
        temp__acpitz__1=(27.0, "°C", "ACPI 1"),
        fan__board__2=(900.0, "RPM", "Fan 2"),
        fan__board__10=(800.0, "RPM", "Fan 10"),
    )
    shown = picked("cpu.temp = CPU\ntemp.*\n!temp.acpitz.*\nfan.*\n# a comment", found)
    assert shown == [
        ("cpu.temp", "CPU"),  # named on its line
        ("temp.nvme.composite", "NVMe Composite"),  # temp.k10temp.tctl is the same sensor
        ("fan.board.2", "Fan 2"),  # natural order: 2 before 10
        ("fan.board.10", "Fan 10"),
    ]
    assert lists.read_keys("cpu.temp = CPU\n!temp.acpi*\nfan.*") == {"cpu.temp", "fan.*"}


def test_names_come_from_a_reading_beside_them():
    found = readings(disk__1__load=(63.0, "%", "C:"), disk__1__name="Data",
                     disk__1__free=(212.0, "GiB", "C:"))  # fmt: skip
    (item,) = lists.pick({"items": "disk.*.load", "detail": "free"}, found)
    assert item.name == "Data" and item.detail.value == 212.0


@pytest.mark.parametrize(
    ("order", "expected"),
    [("high", ["b", "c", "a"]), ("low", ["a", "c", "b"]), ("name", ["a", "b", "c"])],
)
def test_items_are_sorted(order, expected):
    found = readings(x__a=(1.0, "%", "a"), x__b=(9.0, "%", "b"), x__c=(5.0, "%", "c"))
    assert [name for _key, name in picked("x.*", found, sort=order)] == expected


def test_short_names_drop_the_words_they_share():
    assert lists.short_names(["Core 1", "Core 2", "Core 10"]) == ["1", "2", "10"]
    assert lists.short_names(["CPU", "GPU"]) == ["CPU", "GPU"]
    assert lists.short_names(["Fan"]) == ["Fan"]


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [(45.2, "%", "45%"), (61.4, "°C", "61°C"), (2.4e6, "B/s", "2.4 MB/s"), (3.4e9, "B", "3.4 GB"),
     (9000, "s", "2h 30m"), (16.3, "ms", "16 ms"), (1.5, "W", "1.5 W"), (412.2, "GiB", "412 GB"),
     ("192.168.1.20", "", "192.168.1.20"), (None, "", "--")],
)  # fmt: skip
def test_values_are_shown_the_usual_way_for_their_unit(value, unit, text):
    assert auto_format(value, unit) == text


def list_theme(**fields):
    widget = {"type": "list", "id": "list", "x": 10, "y": 10, "w": 300, "h": 180, **fields}
    return parse_theme(
        {"format": "libre-panel-theme/1", "name": "lists",
         "display": {"width": 480, "height": 320}, "widgets": [widget]}
    )  # fmt: skip


@pytest.mark.parametrize("style", ["rows", "bars", "columns", "cells"])
@pytest.mark.parametrize("items", ["cpu.core.*.load", "temp.*\nfan.*", "disk.*.load"])
def test_every_list_style_draws(style, items):
    renderer = Renderer(list_theme(items=items, style=style, detail="free"), preview=True)
    frame, boxes = renderer.render(demo_snapshot(fixed_time=1_700_000_000))
    assert renderer.warnings == []
    assert boxes["list"] == [10, 10, 300, 180]
    assert frame.crop((10, 10, 310, 190)).getbbox() is not None  # something was drawn


def test_a_list_shows_as_many_as_fit():
    renderer = Renderer(list_theme(items="cpu.core.*.load", style="rows", font_size=20, h=90))
    assert renderer._list_room(renderer.widgets[0], 8) == 2  # 90 px hold two rows of 35
    wide = list_theme(items="cpu.core.*.load", style="rows", font_size=12, w=400, h=60)
    renderer = Renderer(wide)
    assert renderer._list_room(renderer.widgets[0], 8) == 6  # side by side
    limited = Renderer(list_theme(max_items=3))
    assert limited._list_room(limited.widgets[0], 8) == 3


def test_an_empty_list_says_so():
    snap = demo_snapshot(fixed_time=1_700_000_000)
    quiet = Renderer(list_theme(items="nothing.*"), preview=True)
    frame, _ = quiet.render(snap)
    assert frame.getbbox() is None or frame.crop((10, 10, 310, 190)).getextrema()[0][1] == 0
    told = Renderer(list_theme(items="nothing.*", empty="No sensors"), preview=True)
    frame, _ = told.render(snap)
    assert frame.crop((10, 10, 310, 190)).getbbox() is not None


def test_renderers_tell_providers_what_they_read():
    gc.collect()
    before = set(base._WANTED)
    renderer = Renderer(list_theme(items="proc.cpu.*.value\n!proc.cpu.1.*"))
    assert base._WANTED[id(renderer)] >= {"proc.cpu.*.value"}
    assert wanted("proc.") and wanted("proc.cpu.2.value")
    del renderer
    gc.collect()
    assert set(base._WANTED) == before
    owner = type("Owner", (), {})()
    want(owner, {"net.*"})
    assert wanted("net.ping")  # a pattern takes the key in
    del owner
    gc.collect()
    assert set(base._WANTED) == before


# -- sensors -----------------------------------------------------------------

Part = namedtuple("Part", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")


def test_drives_leave_out_what_is_not_a_drive(monkeypatch):
    parts = [
        Part("/dev/sda2", "/home", "ext4", "rw"),
        Part("/dev/sda1", "/", "ext4", "rw"),
        Part("/dev/loop3", "/snap/core/1", "squashfs", "ro"),
        Part("tmpfs", "/run", "tmpfs", "rw"),
        Part("/dev/sr0", "/media/cd", "iso9660", "ro"),
        Part("/dev/sda1", "/var/again", "ext4", "rw"),  # the same device twice
    ]
    monkeypatch.setattr(psutil_provider.os, "name", "posix")
    monkeypatch.setattr(psutil_provider.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        psutil_provider.psutil,
        "disk_usage",
        lambda path: Usage(100 * 2**30, 25 * 2**30, 75 * 2**30, 25.0),
    )
    provider = psutil_provider.PsutilProvider({"disk": "/"})
    out = {}
    provider._drives(out)
    assert out["disk.1.name"].value == "/" and out["disk.2.name"].value == "/home"
    assert "disk.3.name" not in out
    assert out["disk.2.free"].value == pytest.approx(75.0)


def test_processes_are_counted_once_per_program(monkeypatch):
    class Proc:
        def __init__(self, pid, name, cpu, mem):
            self.pid, self.info, self._cpu = pid, {"name": name, "memory_percent": mem}, cpu

        def cpu_percent(self, interval=None):
            return self._cpu

    procs = [Proc(0, "System Idle Process", 700, 0), Proc(10, "chrome.exe", 40, 3.0),
             Proc(11, "chrome.exe", 20, 2.0), Proc(12, "game.exe", 50, 9.0),
             Proc(13, "", 99, 9.0)]  # fmt: skip
    monkeypatch.setattr(psutil_provider.psutil, "process_iter", lambda attrs: procs)
    monkeypatch.setattr(psutil_provider.psutil, "cpu_count", lambda: 10)
    out = psutil_provider.PsutilProvider()._processes()
    assert out["proc.cpu.1.name"].value == "chrome" and out["proc.cpu.1.value"].value == 6.0
    assert out["proc.cpu.2.name"].value == "game" and "proc.cpu.3.name" not in out
    assert out["proc.mem.1.name"].value == "game" and out["proc.mem.2.value"].value == 5.0


def test_costly_sensors_wait_until_a_theme_shows_them(monkeypatch):
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    started = []
    provider = psutil_provider.PsutilProvider()
    monkeypatch.setattr(provider, "_keep", lambda name, work, every: started.append(name))
    keys = provider.read()
    assert started == [] and not any(k.startswith("proc.") for k in keys)
    renderer = Renderer(list_theme(items="proc.cpu.*.value\nnet.ping"))
    provider.read()
    assert sorted(started) == ["ping", "proc"]
    del renderer
    provider.close()


Battery = namedtuple("Battery", "percent secsleft power_plugged")


@pytest.mark.parametrize(
    ("battery", "state", "left"),
    [(Battery(55.0, 5400, False), "On battery", 5400.0),
     (Battery(80.0, -2, True), "Charging", None), (Battery(100.0, -2, True), "Charged", None)],
)  # fmt: skip
def test_battery_state_and_time_left(monkeypatch, battery, state, left):
    monkeypatch.setattr(psutil_provider.psutil, "sensors_battery", lambda: battery, raising=False)
    out = psutil_provider.PsutilProvider().read()
    assert out["battery.state"].value == state and out["battery.load"].value == battery.percent
    assert (out["battery.left"].value if "battery.left" in out else None) == left


def test_librehardwaremonitor_names_every_temperature_and_fan():
    tree = json.loads((DATA / "lhm_data.json").read_text(encoding="utf-8"))
    out = readings_from_tree(tree)
    assert out["temp.cpu.package"].label == "CPU Package"
    assert out["temp.gpu.gpu_hot_spot"].label == "GPU Hot Spot"
    assert out["fan.gpu.gpu_fan_2"].unit == "RPM"
    # the friendly aliases point at what they repeat, so a list shows them once
    assert out["cpu.temp"].origin == out["temp.cpu.core_tctl_tdie"].origin != ""
    names = [i.key for i in lists.pick({"items": "cpu.temp = CPU\ntemp.cpu.*"}, out)]
    assert names[0] == "cpu.temp" and "temp.cpu.core_tctl_tdie" not in names


def test_previews_do_not_wake_costly_sensors():
    gc.collect()
    before = set(base._WANTED)
    preview = Renderer(list_theme(items="proc.cpu.*.value"), preview=True)
    assert set(base._WANTED) == before  # the editor's pictures start no process scans
    del preview

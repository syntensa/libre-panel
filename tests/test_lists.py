"""Lists of readings, the sensors behind them and the modules built on them."""

import gc
import json
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

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
    # left out, it is left out under its other key too (beside a panel that shows it)
    assert picked("!cpu.temp\ntemp.*", found) == [
        ("temp.acpitz.1", "ACPI 1"),
        ("temp.nvme.composite", "NVMe Composite"),
    ]
    copied = {**found, "temp.cpu.package": Reading("temp.cpu.package", 61.0, origin="cpu.temp")}
    assert "temp.cpu.package" not in dict(picked("!cpu.temp\ntemp.*", copied))


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


@pytest.mark.parametrize("style", ["rows", "bars", "columns", "cells", "fans"])
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


def cores(**loads):
    from libre_panel.sensors.base import Snapshot

    keys = {n: f"cpu.core.{n}.load" for n in loads}
    return Snapshot(readings={keys[n]: Reading(keys[n], v, "%", f"Core {n}")
                              for n, v in loads.items()})  # fmt: skip


@pytest.mark.parametrize("style", ["bars", "columns", "cells"])
def test_the_bars_of_a_list_glide_to_a_new_reading_on_the_panel(style):
    """As a bar's: the panel shows them on the way (the text says the reading); a preview,
    or a list that is not ``smooth``, shows them where they are."""
    theme = list_theme(items="cpu.core.*.load", style=style)
    panel = Renderer(theme, animate=True)
    panel.render(cores(**{"1": 0.0}), now=100.0)
    panel.render(cores(**{"1": 100.0}), now=100.1)
    ((_name, _detail, text, frac, _rule),) = panel._cache["list\0levels"][0]
    assert 0 < frac < 1 and text == "100%" and panel.moving
    panel.render(cores(**{"1": 100.0}), now=103.0)
    assert panel._cache["list\0levels"][0][0][3] == 1.0 and not panel.moving
    for renderer in (Renderer(theme, preview=True),
                     Renderer(list_theme(items="cpu.core.*.load", style=style, smooth=False),
                              animate=True)):  # fmt: skip
        renderer.render(cores(**{"1": 0.0}), now=100.0)
        renderer.render(cores(**{"1": 100.0}), now=100.1)
        assert renderer._cache["list"][0][0][3] == 1.0


def test_a_list_of_fans_turns_each_one_with_its_speed():
    theme = list_theme(items="fan.*", style="fans", font_size=20, max=3000)
    renderer = Renderer(theme, animate=True)
    assert renderer._list_room(renderer.widgets[0], 20) == 6  # two side by side, three lines
    snap = demo_snapshot(fixed_time=1_700_000_000)
    first, _ = renderer.render(snap, now=100.0)
    second, _ = renderer.render(snap, now=100.2)
    fans = {key.split("\0")[1] for key in renderer._rotors if key.startswith("list\0")}
    assert len(fans) == 6 and all(key.startswith(("fan.", "gpu.fan")) for key in fans)
    assert renderer.moving and first.tobytes() != second.tobytes()  # they turn
    assert renderer.warnings == []


def test_a_lists_fans_turn_over_its_names_and_numbers_as_pieces_of_their_own():
    """The names and numbers stay the piece they are while the fans turn over them, each a
    piece of its own: a frame composes the fans again, not the whole list. Where a fan would
    lie on a name or on another fan, they are laid on the list as before. The frames are the
    ones a renderer that lays every fan on the list draws."""
    from PIL import ImageChops

    theme = list_theme(items="fan.*", style="fans", font_size=20, max=3000)
    split, joined = Renderer(theme, animate=True), Renderer(theme, animate=True)
    joined.keep_static = False
    snap = demo_snapshot(fixed_time=1_700_000_000)
    kept = None
    for k in range(6):
        a, boxes = split.render(snap, now=100.0 + k / 25)
        b, _ = joined.render(snap, now=100.0 + k / 25)
        assert ImageChops.difference(a, b).getbbox() is None, k
        pieces = dict(split._last[0])
        assert boxes["list"] == [10, 10, 300, 180]
        assert sorted(wid for wid in pieces if wid.startswith("list\0")) == [
            f"list\0fan{i}" for i in range(6)
        ]
        assert kept is None or pieces["list"] is kept  # the names and numbers: kept
        kept = pieces["list"]
    assert [wid for wid, _ in joined._last[0]] == ["list"]

    crowded = Renderer(theme, animate=True)
    crowded._list_fan_places = lambda widget, count: [(0.0, 0.0)] * count  # all in one place
    crowded.render(snap, now=100.0)
    assert [wid for wid, _ in crowded._last[0]] == ["list"]


def test_a_pattern_is_matched_again_once_the_readings_are_others():
    """The keys a pattern matches are kept while the readings' keys stay the same: a reading
    that comes, or goes, shows at once."""
    found = readings(fan__board__1=(900.0, "RPM", "Fan 1"), fan__board__2=(800.0, "RPM", "Fan 2"))
    assert [key for key, _ in picked("fan.*", found)] == ["fan.board.1", "fan.board.2"]
    more = {**found, **readings(fan__board__10=(700.0, "RPM", "Fan 10"))}
    assert [key for key, _ in picked("fan.*", more)] == ["fan.board.1", "fan.board.2",
                                                          "fan.board.10"]  # fmt: skip
    del more["fan.board.1"]
    assert [key for key, _ in picked("fan.*", more)] == ["fan.board.2", "fan.board.10"]


def test_fans_that_never_ran_are_left_out_where_asked(monkeypatch):
    """A mainboard's header with nothing on it reads 0 all along; a fan that ran once stays,
    and so does the graphics card's (it stands while the card is cool)."""
    monkeypatch.setattr(lists, "_RAN", set())
    found = readings(fan__lpc__fan_1=(0, "RPM", "Fan #1"), fan__lpc__fan_2=(900, "RPM", "Fan #2"),
                     gpu__fan=(0, "RPM", "GPU fan"))  # fmt: skip
    shown = [i.key for i in lists.pick({"items": "gpu.fan\nfan.*"}, found) if lists.used(i)]
    assert shown == ["gpu.fan", "fan.lpc.fan_2"]
    found["fan.lpc.fan_2"] = Reading("fan.lpc.fan_2", 0, "RPM", "Fan #2")  # it stopped
    shown = [i.key for i in lists.pick({"items": "fan.*"}, found) if lists.used(i)]
    assert shown == ["fan.lpc.fan_2"]
    renderer = Renderer(list_theme(items="fan.*", style="fans", hide_unused=True), preview=True)
    lists._RAN.clear()
    snap = demo_snapshot(fixed_time=1_700_000_000)
    snap.readings["fan.lpc.fan_4"] = Reading("fan.lpc.fan_4", 0, "RPM", "Fan #4")
    renderer.render(snap)
    texts = renderer._cache["list"][0]
    assert texts and "Fan #4" not in [row[0] for row in texts]


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


def test_fedora_atomic_shows_the_disk_not_its_image(monkeypatch):
    # Bazzite, Silverblue: "/" is a composefs image of a few MB, always full
    parts = [
        Part("/dev/nvme0n1p3", "/sysroot", "btrfs", "ro,relatime"),
        Part("/dev/nvme0n1p3", "/var", "btrfs", "rw,relatime"),
        Part("/dev/sdb1", "/run/media/games", "ext4", "rw"),
    ]
    sizes = {"/": (40 * 2**20, 40 * 2**20, 0, 100.0)}
    monkeypatch.setattr(psutil_provider.os, "name", "posix")
    monkeypatch.setattr(psutil_provider.os.path, "ismount", lambda path: path == "/sysroot")
    monkeypatch.setattr(psutil_provider.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        psutil_provider.psutil,
        "disk_usage",
        lambda path: Usage(*sizes.get(path, (500 * 2**30, 100 * 2**30, 400 * 2**30, 20.0))),
    )
    provider = psutil_provider.PsutilProvider()
    assert provider.disk_path == "/sysroot"
    out = provider.read()
    assert out["disk.load"].value == 20.0 and out["disk.total"].value == pytest.approx(500)
    assert out["disk.1.name"].value == "/" and out["disk.2.name"].value == "/run/media/games"
    assert "disk.3.name" not in out  # /var is the same disk


def test_processes_are_counted_once_per_program(monkeypatch):
    """Their share of the processor from their CPU seconds between two counts, whatever
    Process objects psutil hands out: new ones each time here, as when another list of the
    processes (the game mode's) made them anew and psutil's own count began again at 0."""
    times = namedtuple("times", "user system")

    def listing(seconds):  # (pid, name, cores busy, memory %): new objects every time
        rows = [
            (0, "System Idle Process", 7.0, 0),
            (10, "chrome.exe", 0.4, 3.0),
            (11, "chrome.exe", 0.2, 2.0),
            (12, "game.exe", 0.5, 9.0),
            (13, "", 0.9, 9.0),
        ]
        return [SimpleNamespace(pid=pid, info={"name": name, "memory_percent": mem,
                                               "cpu_times": times(busy * seconds, 0.0)})
                for pid, name, busy, mem in rows]  # fmt: skip

    clock = [100.0]
    monkeypatch.setattr(psutil_provider.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(psutil_provider.psutil, "cpu_count", lambda: 10)
    provider = psutil_provider.PsutilProvider()
    monkeypatch.setattr(psutil_provider.psutil, "process_iter", lambda attrs: listing(100.0))
    first = provider._processes()
    assert first["proc.cpu.1.value"].value == 0.0  # (one count: nothing to compare with yet)
    assert first["proc.mem.1.name"].value == "game"  # (the memory is there at once)
    clock[0] += 2.0
    monkeypatch.setattr(psutil_provider.psutil, "process_iter", lambda attrs: listing(102.0))
    out = provider._processes()
    assert out["proc.cpu.1.name"].value == "chrome" and out["proc.cpu.1.value"].value == 6.0
    assert out["proc.cpu.2.name"].value == "game" and out["proc.cpu.2.value"].value == 5.0
    assert "proc.cpu.3.name" not in out  # (not the idle process, nor one without a name)
    assert out["proc.mem.1.name"].value == "game" and out["proc.mem.2.value"].value == 5.0
    clock[0] += 2.0  # a process id taken by a new program: its seconds begin anew, not below 0
    monkeypatch.setattr(psutil_provider.psutil, "process_iter", lambda attrs: listing(1.0))
    assert provider._processes()["proc.cpu.1.value"].value == 0.0


def test_the_scan_of_the_processes_leaves_the_interpreter_to_the_panels_now_and_then(monkeypatch):
    """On Windows a scan of hundreds of processes takes a good part of a second: in one go it
    held every panel's frame (both panels stood still a moment every few seconds)."""
    clock = [0.0]
    times = namedtuple("times", "user system")

    def listing(attrs):
        for _ in range(300):
            clock[0] += 0.001  # a millisecond of the interpreter for each
            yield SimpleNamespace(pid=7, info={"name": "busy.exe", "memory_percent": 1.0,
                                               "cpu_times": times(1.0, 0.0)})  # fmt: skip

    pauses = []
    monkeypatch.setattr(psutil_provider.psutil, "process_iter", listing)
    monkeypatch.setattr(psutil_provider.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(psutil_provider.time, "sleep", pauses.append)
    out = psutil_provider.PsutilProvider()._processes()
    assert out["proc.cpu.1.name"].value == "busy"
    burst = psutil_provider.SCAN_BURST_S / 0.001
    assert len(pauses) >= 300 / (burst + 1) - 1 and set(pauses) == {psutil_provider.SCAN_PAUSE_S}


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


Temp = namedtuple("Temp", "label current high critical")
Fan = namedtuple("Fan", "label current")


def test_two_drives_with_the_same_sensor_names_both_show(monkeypatch):
    temps = {  # two NVMe drives, two AMD GPUs: Linux groups them by name
        "nvme": [Temp("Composite", 40.0, 0, 0), Temp("Composite", 55.0, 0, 0)],
        "amdgpu": [Temp("edge", 50.0, 0, 0), Temp("edge", 60.0, 0, 0)],
    }
    fans = {"amdgpu": [Fan("", 900), Fan("", 1200)]}
    monkeypatch.setattr(psutil_provider.psutil, "sensors_temperatures", lambda: temps,
                        raising=False)  # fmt: skip
    monkeypatch.setattr(psutil_provider.psutil, "sensors_fans", lambda: fans, raising=False)
    out = {}
    provider = psutil_provider.PsutilProvider()
    provider._temperatures(out)
    provider._fans(out)
    assert out["temp.nvme.composite"].value == 40.0 and out["temp.nvme.composite_2"].value == 55.0
    assert out["temp.nvme.composite_2"].label == "NVMe Composite 2"
    assert out["gpu.temp"].value == out[out["gpu.temp"].origin].value == 50.0
    assert [i.number for i in lists.pick({"items": "temp.*"}, out)] == [50.0, 60.0, 40.0, 55.0]
    assert out["fan.amdgpu.0"].value == 900.0 and out["fan.amdgpu.1"].value == 1200.0


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
    assert out["gpu.hotspot"].origin == out["temp.gpu.gpu_hot_spot"].origin != ""
    names = [i.key for i in lists.pick({"items": "cpu.temp = CPU\ntemp.cpu.*"}, out)]
    assert names[0] == "cpu.temp" and "temp.cpu.core_tctl_tdie" not in names


def test_previews_do_not_wake_costly_sensors():
    gc.collect()
    before = set(base._WANTED)
    preview = Renderer(list_theme(items="proc.cpu.*.value"), preview=True)
    assert set(base._WANTED) == before  # the editor's pictures start no process scans
    del preview

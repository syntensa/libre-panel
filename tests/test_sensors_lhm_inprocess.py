"""LibreHardwareMonitor inside Libre Panel: tested with stand-ins for its .NET objects.

Nothing here needs Windows, pythonnet or the libraries: ``fake_lhm`` builds objects
that look like the ones pythonnet hands over, and a fake ``pythonnet`` and ``clr`` stand
in for the loading.
"""

import dataclasses
import json
import logging
import sys
import types
from pathlib import Path

import pytest
from fake_lhm import (
    FakeComputer,
    FakeHardware,
    FakeSensor,
    computer_from_tree,
    lists_only_after_an_update,
    ring0_off,
    small_computer,
    with_raw_throughput,
)
from fake_sensors import show, wait_for

from libre_panel.sensors import base
from libre_panel.sensors import lhm_inprocess as inside
from libre_panel.sensors import librehardwaremonitor as lhm

TREE = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))
LOGGER = "libre_panel.sensors.lhm_inprocess"
REAL_PAWNIO_VERSION = inside.pawnio_version  # before the fixture below replaces it


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    """The computer the tests run on is an administrator's with PawnIO; a test changes that.
    What themes on show read starts empty (and so does not depend on other tests)."""
    monkeypatch.setattr(base, "_WANTED", {})
    state = {"admin": True, "pawnio": "2.0.1"}
    monkeypatch.setattr("libre_panel.autostart.is_admin", lambda: state["admin"])
    monkeypatch.setattr(inside, "pawnio_version", lambda: state["pawnio"])
    return state


def opened(computer, **options):
    """A source for a fake computer, read once; its thread is not started, the test goes
    on from here with ``refresh()``."""
    source = inside.InProcessSource(lambda: computer, **options)
    source._begin(computer)
    return source


def updates(computer):
    return {h.Identifier: h.updates for h in computer.everything()}


# -- one mapping for both ways ------------------------------------------------------


def test_the_two_steps_make_what_readings_from_tree_makes():
    sensors = list(lhm.sensors_from_tree(TREE))
    assert lhm.readings_from_sensors(sensors) == lhm.readings_from_tree(TREE)
    first = sensors[0]
    assert (first.sensor_id, first.hardware_id) == ("/amdcpu/0/voltage/2", "/amdcpu/0")
    assert (first.hardware_name, first.sensor_type) == (
        "AMD Ryzen 5 7600 6-Core Processor",
        "Voltage",
    )
    assert (first.label, first.value, first.unit) == ("VDDCR", 0.643, "V")


def test_every_reading_says_which_sensors_it_is_made_from():
    sources: dict[str, tuple[str, ...]] = {}
    out = lhm.readings_from_sensors(lhm.sensors_from_tree(TREE), sources=sources)
    assert set(sources) == set(out)
    assert sources["lhm:/amdcpu/0/temperature/2"] == ("/amdcpu/0/temperature/2",)
    assert sources["cpu.temp"] == ("/amdcpu/0/temperature/2",)  # the alias: the sensor behind it
    assert sources["temp.cpu.package"] == ("/amdcpu/0/temperature/3",)
    assert sources["gpu.load"] == ("/gpu-nvidia/0/load/0",)
    assert sources["cpu.name"] == () and sources["gpu.name"] == ()  # no sensor to read
    # this version has no average clock: the mean of the cores (the one that says
    # "NaN" has no value, so it is not one of them)
    cores = sources["cpu.freq"]
    assert len(cores) == 7 and all(s.startswith("/amdcpu/0/clock/") for s in cores)
    assert "/amdcpu/0/clock/11" not in cores


def test_inside_libre_panel_gives_the_readings_the_web_server_gives():
    """The same hardware, once as the web server's tree and once as objects of the library:
    the same keys, values, units and labels."""
    tree = with_raw_throughput(TREE)
    web = lhm.readings_from_tree(tree)
    inner = opened(computer_from_tree(tree)).read()
    # a sensor of a type that does not exist: the web server's text tells the unit, the
    # library has nothing to go by
    odd = "lhm:/amdcpu/0/current/1"
    assert inner.pop(odd) == dataclasses.replace(web.pop(odd), unit="")
    assert inner == web
    assert len(inner) > 100 and inner["cpu.temp"].value == 40.0 and inner["gpu.name"].value


def test_the_gpu_option_picks_the_card_in_both_ways():
    tree = with_raw_throughput(TREE)
    other = FakeHardware(
        "/gpu-amd/1",
        "AMD Radeon(TM) Graphics",
        "GpuAmd",
        [FakeSensor("/gpu-amd/1/temperature/0", "GPU Core", "Temperature", 45.0)],
    )
    computer = computer_from_tree(tree)
    computer.Hardware.insert(0, other)
    assert opened(computer).read()["gpu.temp"].value == 27.0
    assert opened(computer, gpu="radeon").read()["gpu.temp"].value == 45.0


@pytest.mark.parametrize(
    ("kind", "unit"),
    [("Voltage", "V"), ("Current", "A"), ("Power", "W"), ("Clock", "MHz"), ("Temperature", "°C"),
     ("Load", "%"), ("Fan", "RPM"), ("Control", "%"), ("Factor", ""), ("Data", "GB"),
     ("SmallData", "MB"), ("Throughput", "B/s"), ("Noise", "dBA"), ("Unheard of", "")],
)  # fmt: skip
def test_units_are_what_the_web_server_prints(kind, unit):
    chip = FakeHardware("/x/0", "Chip", "Motherboard", [FakeSensor("/x/0/a/0", "Thing", kind, 3.0)])
    reading = opened(FakeComputer([chip])).read()["lhm:/x/0/a/0"]
    assert (reading.value, reading.unit, reading.label) == (3.0, unit, "Thing")


def test_a_sensor_without_a_number_is_left_out():
    chip = FakeHardware(
        "/x/0",
        "Chip",
        "Motherboard",
        [
            FakeSensor("/x/0/a/0", "None", "Load", None),
            FakeSensor("/x/0/a/1", "Not a number", "Load", float("nan")),
            FakeSensor("/x/0/a/2", "Infinite", "Load", float("inf")),
            FakeSensor("/x/0/a/3", "Text", "Load", "x"),
            FakeSensor("/x/0/a/4", "Fine", "Load", 5.0),
        ],
    )
    assert list(opened(FakeComputer([chip])).read()) == ["lhm:/x/0/a/4"]


def test_the_memory_temperature_is_the_modules_and_a_theme_that_reads_it_updates_them():
    computer = small_computer()
    source = opened(computer)
    first = updates(computer)
    out = source.read()
    assert out["mem.temp"].value == 38.0
    assert out["mem.temp"].origin == "lhm:/memory/dimm/1/temperature/0"
    assert out["temp.memory.dimm_1"].value == 38.0
    theme = show("mem.temp")  # only the module is updated: the Total Memory has no thermal sensor
    source.refresh()
    assert updates(computer) == {**first, "/memory/dimm/1": 2}
    del theme


# -- ring0: zero is not a reading ------------------------------------------------------


def test_what_needs_ring0():
    assert inside.needs_ring0("Cpu", "Temperature") and inside.needs_ring0("Cpu", "Clock")
    assert inside.needs_ring0("Cpu", "Power") and not inside.needs_ring0("Cpu", "Load")
    assert inside.needs_ring0("SuperIO", "Fan") and inside.needs_ring0("SuperIO", "Voltage")
    assert inside.needs_ring0("EmbeddedController", "Temperature")
    assert inside.needs_ring0("Memory", "Temperature") and not inside.needs_ring0("Memory", "Load")
    for other in ("GpuNvidia", "GpuAmd", "GpuIntel", "Network", "Motherboard", "Storage"):
        assert not inside.needs_ring0(other, "Temperature") and not inside.needs_ring0(other, "Fan")


def test_zero_from_a_ring0_driver_that_does_not_work_is_missing_not_zero():
    source = opened(ring0_off(small_computer()))
    out = source.read()
    gone = [
        "cpu.temp", "cpu.power", "cpu.freq", "temp.cpu.core_tctl_tdie", "temp.memory.dimm_1",
        "mem.temp",
        "fan.lpc.fan_1", "temp.lpc.system", "lhm:/amdcpu/0/temperature/2",
        "lhm:/lpc/nct0000/0/fan/1", "lhm:/memory/dimm/1/temperature/0",
    ]  # fmt: skip
    assert [key for key in gone if key in out] == []
    # what needs no driver is there; a stopped fan of the graphics card is a measurement
    assert out["cpu.load"].value == 7.0 and out["gpu.temp"].value == 48.0
    assert out["gpu.fan"].value == 0.0 and out["fan.gpu.gpu_fan_1"].value == 0.0
    assert out["lhm:/ram/load/0"].value == 40.0
    assert source.status().ring0 is False
    assert not any(k in out for k in lhm.HARDWARE_KEYS if k.startswith("cpu"))


def test_with_a_working_driver_zero_is_a_reading():
    computer = small_computer()
    computer.Hardware[1].sensor("Package").Value = 0.0  # a processor doing nothing
    computer.Hardware[0].SubHardware[0].sensor("Fan #1").Value = 0.0  # a fan that stands still
    source = opened(computer)
    out = source.read()
    assert out["cpu.power"].value == 0.0 and out["fan.lpc.fan_1"].value == 0.0
    assert out["cpu.temp"].value == 55.0
    assert source.status().ring0 is True


def test_a_chip_temperature_of_zero_is_never_a_measurement():
    computer = small_computer()
    computer.Hardware[4].sensor("DIMM #1").Value = 0.0  # a module that could not be read
    out = opened(computer).read()
    assert "temp.memory.dimm_1" not in out and "mem.temp" not in out
    assert out["cpu.temp"].value == 55.0


def test_without_sensors_that_need_ring0_nothing_is_taken_away():
    gpu = small_computer().Hardware[3]
    source = opened(FakeComputer([gpu]))
    assert source.status().ring0 is None  # a machine with a graphics card only
    assert source.read()["gpu.fan"].value == 0.0


def test_the_status_says_who_is_missing(machine):
    machine.update(admin=False, pawnio=None)
    status = opened(ring0_off(small_computer())).status()
    assert (status.mode, status.elevated, status.ring0, status.pawnio) == (
        "inprocess",
        False,
        False,
        None,
    )
    assert lhm.ring0_steps(status) == [
        "run Libre Panel as administrator (libre-panel autostart enable --elevated)",
        "install the PawnIO driver (https://pawnio.eu)",
    ]
    machine.update(admin=True, pawnio="1.9.0")
    assert lhm.ring0_steps(opened(ring0_off(small_computer())).status()) == [
        "update the PawnIO driver to version 2 or newer (https://pawnio.eu)"
    ]
    machine.update(admin=True, pawnio="")  # installed, version not told
    assert lhm.ring0_steps(opened(ring0_off(small_computer())).status()) == []


# -- which nodes are updated -----------------------------------------------------------


def test_only_the_nodes_a_theme_reads_are_updated():
    computer = small_computer()
    source = opened(computer)
    first = updates(computer)
    assert first == {
        "/motherboard": 1,  # updated once to see what it lists: no sensor of its own
        "/lpc/nct0000/0": 1,
        "/amdcpu/0": 1,
        "/ram": 1,
        "/gpu-nvidia/0": 1,
        "/memory/dimm/1": 1,
    }
    source.refresh()  # no theme reads anything
    assert updates(computer) == first

    theme = show("gpu.temp")
    source.refresh()
    assert updates(computer) == {**first, "/gpu-nvidia/0": 2}
    del theme

    theme = show("lhm:/lpc/nct0000/0/fan/1")  # a raw key: the chip, a sub-hardware
    source.refresh()
    assert updates(computer) == {**first, "/gpu-nvidia/0": 2, "/lpc/nct0000/0": 2}
    del theme

    theme = show("temp.*", "cpu.load")  # a pattern, and a key made by the processor
    source.refresh()
    assert updates(computer) == {
        **first,
        "/lpc/nct0000/0": 3,
        "/amdcpu/0": 2,
        "/memory/dimm/1": 2,
        "/gpu-nvidia/0": 3,
    }
    del theme

    before = updates(computer)
    theme = show("lhm:*")  # every sensor, as `libre-panel sensors` asks
    source.refresh()
    assert all(n == before[i] + 1 for i, n in updates(computer).items() if i != "/motherboard")
    del theme


def test_what_all_themes_read_together():
    assert base.wanted_keys() == frozenset()
    one, two = show("a.b", "c.*"), show("c.*", "d")
    assert base.wanted_keys() == {"a.b", "c.*", "d"}
    del one
    assert base.wanted_keys() == {"c.*", "d"}
    del two
    assert base.wanted_keys() == frozenset()


def test_what_themes_read_is_looked_at_again_only_when_it_has_changed(monkeypatch):
    asked = []
    real = inside.wanted
    monkeypatch.setattr(inside, "wanted", lambda key: asked.append(key) or real(key))
    computer = small_computer()
    source = opened(computer)
    theme = show("gpu.temp")
    source.refresh()
    first = len(asked)
    assert first > 0 and computer.Hardware[3].updates == 2
    for _ in range(5):  # the same themes: the same nodes, without asking again
        source.refresh()
    assert len(asked) == first and computer.Hardware[3].updates == 7
    other = show("cpu.load")  # another theme on show: asked again
    source.refresh()
    assert len(asked) > first and computer.Hardware[1].updates == 2
    del theme, other


def test_a_key_made_from_several_sensors_wakes_the_node_of_them_all():
    cores = [
        FakeSensor(f"/intelcpu/0/clock/{i}", f"CPU Core #{i}", "Clock", 3000.0 + i) for i in (1, 2)
    ]
    cpu = FakeHardware("/intelcpu/0", "Processor", "Cpu", cores)
    computer = FakeComputer([cpu])
    source = opened(computer)
    assert source.read()["cpu.freq"].value == 3001.5  # no average clock: the cores' mean
    theme = show("cpu.freq")
    source.refresh()
    assert cpu.updates == 2
    del theme


def test_values_follow_the_rounds():
    computer = small_computer()
    gpu = computer.Hardware[3]
    temperature = gpu.Sensors[0]
    gpu.on_update = lambda hardware: setattr(temperature, "Value", 40.0 + hardware.updates)
    source = opened(computer)
    theme = show("gpu.temp")
    assert source.read()["gpu.temp"].value == 41.0
    source.refresh()
    source.refresh()
    assert source.read()["gpu.temp"].value == 43.0
    del theme


def test_a_node_that_cannot_be_updated_does_not_stop_the_others(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    computer = small_computer()
    cpu, gpu = computer.Hardware[1], computer.Hardware[3]
    source = opened(computer)
    theme = show("gpu.temp", "cpu.temp")
    gpu.raises = RuntimeError("driver gone")
    for _ in range(3):
        source.refresh()
    assert cpu.updates == 4 and gpu.updates == 4
    assert source.read()["gpu.temp"].value == 48.0  # as it was last read
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(said) == 1 and "GpuNvidia" in said[0] and "driver gone" in said[0]
    gpu.raises = None
    source.refresh()
    gpu.raises = RuntimeError("again")
    source.refresh()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2  # said again
    del theme


def test_a_part_that_cannot_be_updated_is_not_named_in_the_log(caplog, monkeypatch, tmp_path):
    """The log goes into issues: it says what kind of part could not be read, never what the
    part is called (a memory module's name holds its maker and part number)."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    computer = small_computer()
    computer.Hardware[4].raises = RuntimeError("SMBus busy")  # the memory module
    computer.Hardware[3].raises = RuntimeError("driver gone")  # the graphics card
    opened(computer)
    monkeypatch.setattr(inside, "availability", lambda dll_dir=None, platform=None: (tmp_path, ""))
    assert inside.check(opener=lambda folder: computer)[0]  # libre-panel sensors --check-lhm
    assert "SMBus busy" in caplog.text and "driver gone" in caplog.text
    names = ["Maker", "PART-1", "Graphics Card"]
    assert not [name for name in names if name in caplog.text]


def test_a_sensor_without_a_value_yet_is_still_known_to_make_its_key():
    computer = small_computer()
    gpu = computer.Hardware[3]
    temperature = gpu.Sensors[0]
    temperature.Value = None  # not there at the first round
    source = opened(computer)
    assert "gpu.temp" not in source.read()
    theme = show("gpu.temp")  # a theme wants it: its node is read, and the value is there
    temperature.Value = 50.0
    source.refresh()
    assert gpu.updates == 2 and source.read()["gpu.temp"].value == 50.0
    del theme


def test_a_round_that_fails_again_and_again_is_said_once(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    source = opened(small_computer())
    ok = source._publish
    source._publish = lambda: (_ for _ in ()).throw(RuntimeError("no mapping"))
    theme = show("gpu.temp")
    for _ in range(4):
        source._refresh_guarded()
    said = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(said) == 1 and "could not be read" in said[0].getMessage()
    source._publish = ok
    source._refresh_guarded()  # it works again
    source._publish = lambda: (_ for _ in ()).throw(RuntimeError("no mapping"))
    source._refresh_guarded()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    del theme


# -- stale readings, the history ----------------------------------------------------------


def test_readings_go_when_the_thread_stops_reading():
    now = [100.0]
    source = opened(small_computer(), stale_seconds=5, clock=lambda: now[0])
    assert source.read()
    now[0] += 4.9
    assert source.read()
    now[0] += 0.2  # five seconds of nothing: not numbers that stand still
    assert source.read() == {}
    source.refresh()  # alive again, though nobody reads anything: what it has is current
    assert source.read()


def test_a_slow_rate_is_not_a_silence_and_zero_keeps_everything():
    now = [0.0]
    slow = opened(small_computer(), poll_seconds=10, stale_seconds=5, clock=lambda: now[0])
    assert slow.stale_seconds == 30  # three rounds
    now[0] = 29
    assert slow.read()
    forever = opened(small_computer(), stale_seconds=0, clock=lambda: now[0])
    now[0] = 1e9
    assert forever.read()


def test_libre_hardware_monitor_keeps_no_history_of_its_own(monkeypatch):
    computer = small_computer()
    opened(computer)
    assert {s.ValuesTimeWindow for h in computer.everything() for s in h.Sensors} == {"a day"}

    system = types.ModuleType("System")
    system.TimeSpan = types.SimpleNamespace(Zero="zero")
    monkeypatch.setitem(sys.modules, "System", system)
    computer = small_computer()
    opened(computer)
    assert {s.ValuesTimeWindow for h in computer.everything() for s in h.Sensors} == {"zero"}


# -- the thread ----------------------------------------------------------------------------


def test_the_thread_opens_reads_and_closes_the_computer():
    computer = small_computer()
    ready = []
    source = inside.InProcessSource(lambda: computer, poll_seconds=0.1, on_ready=ready.append)
    gpu = computer.Hardware[3]
    theme = show("gpu.temp")
    assert not source.wait_ready(0)
    source.start()
    assert source.wait_ready(10)
    assert [s.mode for s in ready] == ["inprocess"] and ready[0].elevated and ready[0].ring0
    assert source.read()["cpu.load"].value == 7.0
    assert wait_for(lambda: gpu.updates >= 3)  # at the rate it was given
    source.close()
    assert computer.closed == 1 and not source._thread.is_alive()
    source.close()
    assert computer.closed == 1
    del theme


def test_a_computer_that_will_not_close_changes_nothing():
    computer = small_computer()
    computer.close_raises = OSError("busy")
    source = inside.InProcessSource(lambda: computer, poll_seconds=0.1)
    source.start()
    assert source.wait_ready(10)
    source.close()
    assert computer.closed == 1 and not source._thread.is_alive()


def test_a_library_that_does_not_load_ends_in_a_message(caplog):
    failed = []

    def opener():
        raise inside.LhmUnavailable("pythonnet already runs on CoreCLR")

    source = inside.InProcessSource(opener, on_failure=failed.append)
    source.start()
    assert source.wait_ready(10)
    assert failed == ["pythonnet already runs on CoreCLR"]
    assert source.status().error == "pythonnet already runs on CoreCLR"
    assert source.read() == {}
    said = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(said) == 1 and "cannot run inside Libre Panel" in said[0].getMessage()
    source.close()


def test_whatever_the_dot_net_side_raises_is_told_not_thrown():
    def opener():
        raise TypeError("Could not load file or assembly 'X'")

    source = inside.InProcessSource(opener)
    source.start()
    assert source.wait_ready(10)
    assert source.status().error == "TypeError: Could not load file or assembly 'X'"
    source.close()


def test_a_computer_that_opened_but_cannot_be_read_is_closed():
    class Broken(FakeComputer):
        @property
        def Hardware(self):  # noqa: N802
            raise RuntimeError("no hardware")

        @Hardware.setter
        def Hardware(self, value):  # noqa: N802
            pass

    computer = Broken([])
    source = inside.InProcessSource(lambda: computer)
    source.start()
    assert source.wait_ready(10)
    assert "no hardware" in source.status().error
    source.close()
    assert computer.closed == 1


def test_a_callback_that_fails_does_not_stop_the_thread(caplog):
    computer = small_computer()

    def bad(status):
        raise ValueError("oops")

    source = inside.InProcessSource(lambda: computer, poll_seconds=0.1, on_ready=bad)
    source.start()
    assert source.wait_ready(10)
    assert source.read() and source._thread.is_alive()
    assert any("callback failed" in r.getMessage() for r in caplog.records)
    source.close()


# -- finding and loading the libraries ----------------------------------------------------------


def library_folder(tmp_path, leave_out=()):
    folder = tmp_path / "libs"
    folder.mkdir(parents=True)
    for name in inside.LIBRARIES:
        if name not in leave_out:
            (folder / f"{name}.dll").write_bytes(b"")
    return folder


@pytest.fixture
def dot_net(monkeypatch):
    """A pythonnet that starts the .NET Framework, a clr that loads, and the Computer of a
    library; ``calls`` is what was asked of them."""
    calls = []
    runtime = types.SimpleNamespace(kind=".NET Framework")
    pythonnet = types.ModuleType("pythonnet")

    def load(name=None):
        calls.append(("load", name))
        if hasattr(pythonnet, "refuse"):
            raise pythonnet.refuse

    pythonnet.load = load
    pythonnet.get_runtime_info = lambda: runtime
    clr = types.ModuleType("clr")

    def add_reference(path):
        calls.append(("add", Path(path).name))
        if getattr(clr, "refuse", None) == Path(path).name:
            raise OSError("An attempt was made to load a program with an incorrect format")

    clr.AddReference = add_reference

    class Computer:
        def __init__(self):
            calls.append(("computer",))

        def Open(self):  # noqa: N802
            calls.append(("open", sorted(vars(self))))

    package = types.ModuleType("LibreHardwareMonitor")
    hardware = types.ModuleType("LibreHardwareMonitor.Hardware")
    hardware.Computer = Computer
    for name, module in {
        "pythonnet": pythonnet,
        "clr": clr,
        "LibreHardwareMonitor": package,
        "LibreHardwareMonitor.Hardware": hardware,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return types.SimpleNamespace(
        calls=calls, runtime=runtime, pythonnet=pythonnet, clr=clr, hardware=hardware
    )


def test_the_libraries_load_in_their_order_on_the_net_framework(dot_net, tmp_path):
    folder = library_folder(tmp_path)
    computer = inside.open_computer(folder)
    added = [c[1] for c in dot_net.calls if c[0] == "add"]
    assert added == [f"{name}.dll" for name in inside.LIBRARIES]
    assert added[-1] == "LibreHardwareMonitorLib.dll" and added[0] == "System.Memory.dll"
    assert dot_net.calls[0] == ("load", "netfx")  # asked for the .NET Framework, by name
    assert str(folder) in sys.path
    # the five groups a panel can use are on; drives are not (they need one more library)
    assert computer.IsCpuEnabled and computer.IsGpuEnabled and computer.IsMemoryEnabled
    assert computer.IsMotherboardEnabled and computer.IsNetworkEnabled
    assert not hasattr(computer, "IsStorageEnabled")
    assert ("open", sorted(vars(computer))) in dot_net.calls


def test_an_amount_of_data_is_in_bytes_from_0_9_7_on(dot_net, tmp_path, monkeypatch):
    """0.9.7 tells every amount of data in bytes, under one type; 0.9.6 told GB, and MB as
    SmallData (librehardwaremonitor._as_before makes them the same)."""
    monkeypatch.setitem(inside._UNITS, "data", "GB")
    folder = library_folder(tmp_path)
    dot_net.hardware.SensorType = type("SensorType", (), {"Data": 12, "Throughput": 13})
    inside.load_library(folder)
    assert inside._UNITS["data"] == "B"
    dot_net.hardware.SensorType = type("SensorType", (), {"Data": 12, "SmallData": 13})
    inside.load_library(folder)
    assert inside._UNITS["data"] == "GB"  # 0.9.6 (a dll_dir of one's own)


def test_a_runtime_that_pythonnet_has_already_started_is_not_used(dot_net, tmp_path):
    dot_net.runtime.kind = "CoreCLR"
    with pytest.raises(inside.LhmUnavailable, match="already runs on CoreCLR.*\\.NET Framework"):
        inside.load_library(library_folder(tmp_path))
    assert not [c for c in dot_net.calls if c[0] == "add"]  # nothing was loaded into it


def test_pythonnet_that_cannot_start_the_net_framework(dot_net, tmp_path):
    dot_net.pythonnet.refuse = RuntimeError("Failed to create a .NET runtime (netfx)")
    with pytest.raises(inside.LhmUnavailable, match="could not start the .NET Framework"):
        inside.load_library(library_folder(tmp_path))


def test_no_pythonnet_at_all(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pythonnet", None)  # an import raises ImportError
    with pytest.raises(inside.LhmUnavailable, match=r'pip install "libre-panel\[lhm\]"'):
        inside.load_library(library_folder(tmp_path))


def test_the_main_library_is_needed_the_others_are_missed(dot_net, tmp_path, caplog):
    with pytest.raises(inside.LhmUnavailable, match="LibreHardwareMonitorLib.dll is not in"):
        inside.load_library(library_folder(tmp_path / "a", ("LibreHardwareMonitorLib",)))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    (tmp_path / "b").mkdir()
    folder = library_folder(tmp_path / "b", ("BlackSharp.Core", "HidSharp"))
    assert inside.load_library(folder)
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 1 and "BlackSharp.Core" in said[0] and "HidSharp" not in said[0]


def test_a_library_of_the_wrong_kind_is_told(dot_net, tmp_path):
    dot_net.clr.refuse = "HidSharp.dll"
    with pytest.raises(inside.LhmUnavailable, match=r"HidSharp.dll did not load: .*format"):
        inside.load_library(library_folder(tmp_path))


def test_finding_the_folder(tmp_path, monkeypatch):
    mine = library_folder(tmp_path)
    assert inside.find_dll_dir(str(mine)) == mine
    assert inside.find_dll_dir(str(tmp_path)) is None  # no such library in it
    assert inside.find_dll_dir(None) is None
    bundle = tmp_path / "bundle"
    (bundle / "lhm").mkdir(parents=True)
    (bundle / "lhm" / "LibreHardwareMonitorLib.dll").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)  # the Windows download
    assert inside.find_dll_dir(None) == bundle / "lhm"
    assert inside.find_dll_dir(str(mine)) == mine  # a folder of one's own comes first
    assert inside.find_dll_dir(str(tmp_path)) == bundle / "lhm"


def test_the_folder_next_to_a_frozen_program(tmp_path, monkeypatch):
    program = tmp_path / "app"
    (program / "_internal" / "lhm").mkdir(parents=True)
    (program / "_internal" / "lhm" / "LibreHardwareMonitorLib.dll").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program / "libre-panel.exe"))
    assert inside.find_dll_dir(None) == program / "_internal" / "lhm"


def test_pythonnet_is_found_also_when_it_is_imported_already(monkeypatch):
    monkeypatch.setitem(sys.modules, "pythonnet", types.ModuleType("pythonnet"))  # no spec
    assert inside._has_pythonnet()
    monkeypatch.setitem(sys.modules, "pythonnet", None)
    assert not inside._has_pythonnet()


def test_availability_says_why_not(tmp_path, monkeypatch, caplog):
    folder, why = inside.availability(None, "linux")
    assert folder is None and "Windows" in why
    monkeypatch.setattr(inside.importlib.util, "find_spec", lambda name: None)
    folder, why = inside.availability(None, "win32")
    assert folder is None and 'pip install "libre-panel[lhm]"' in why
    monkeypatch.setattr(inside.importlib.util, "find_spec", lambda name: object())
    folder, why = inside.availability(None, "win32")
    assert folder is None and "LibreHardwareMonitorLib.dll was not found" in why
    assert "dll_dir" in why
    mine = library_folder(tmp_path)
    assert inside.availability(str(mine), "win32") == (mine, "")
    elsewhere = tmp_path / "elsewhere"
    folder, why = inside.availability(str(elsewhere), "win32")
    assert folder is None and str(elsewhere) in why
    bundle = tmp_path / "bundle"
    (bundle / "lhm").mkdir(parents=True)
    (bundle / "lhm" / "LibreHardwareMonitorLib.dll").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert inside.availability(str(elsewhere), "win32") == (bundle / "lhm", "")
    assert str(elsewhere) in caplog.records[-1].getMessage()  # a dll_dir that does not work


def test_the_pawnio_driver_is_found_where_libre_hardware_monitor_looks(monkeypatch):
    class Key:
        def __init__(self, version):
            self.version = version

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    views = {}  # registry view -> the DisplayVersion there (None: the key has none)
    winreg = types.ModuleType("winreg")
    winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ = "HKLM", 1
    winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY = 0x100, 0x200

    def open_key(root, path, reserved, access):
        assert (root, path) == ("HKLM", inside.PAWNIO_KEY)
        if access & 0x300 not in views:
            raise FileNotFoundError(path)
        return Key(views[access & 0x300])

    def query(key, name):
        assert name == "DisplayVersion"
        if key.version is None:
            raise FileNotFoundError(name)
        return key.version, 1

    winreg.OpenKey, winreg.QueryValueEx = open_key, query
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    assert REAL_PAWNIO_VERSION() is None  # not installed in either view
    views[0x100] = "2.0.1"
    assert REAL_PAWNIO_VERSION() == "2.0.1"
    views.clear()
    views[0x200] = "1.2.3"  # only the 32-bit view has it
    assert REAL_PAWNIO_VERSION() == "1.2.3"
    views.clear()
    views[0x100] = None  # installed, but no version written
    assert REAL_PAWNIO_VERSION() == ""


def test_without_winreg_there_is_no_pawnio(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)  # an import raises ImportError
    assert REAL_PAWNIO_VERSION() is None


def test_a_chip_that_lists_its_fans_only_after_an_update_has_them():
    """LibreHardwareMonitor lists a Super I/O chip's fans, temperatures and voltages only once
    an update has read them: read before, the mainboard's fans were missing for good."""
    computer = small_computer()
    lists_only_after_an_update(computer)
    source = opened(computer)
    readings = source.read()
    assert readings["fan.lpc.fan_1"].value == 900.0
    assert "lhm:/lpc/nct0000/0/control/0" in readings  # and what it listed before


def test_a_fan_that_starts_later_is_found_by_the_next_survey(monkeypatch):
    """A fan that stands at first is not listed (a graphics card in its zero-RPM mode, a pump
    that starts late): a round now and then reads all hardware, also what no theme reads. Its
    history is switched off as the others' are."""
    system = types.ModuleType("System")
    system.TimeSpan = types.SimpleNamespace(Zero="zero")
    monkeypatch.setitem(sys.modules, "System", system)
    now = [100.0]
    computer = small_computer()
    chip = next(item for item in computer.everything() if item.HardwareType == "SuperIO")
    source = opened(computer, clock=lambda: now[0])
    assert "fan.lpc.fan_4" not in source.read()
    chip.Sensors.append(FakeSensor("/lpc/nct0000/0/fan/3", "Fan #4", "Fan", 700.0))  # it turns
    source.refresh()  # no theme reads the chip
    assert "fan.lpc.fan_4" not in source.read()
    now[0] += inside.SURVEY_SECONDS
    source.refresh()
    assert source.read()["fan.lpc.fan_4"].value == 700.0
    assert chip.Sensors[-1].ValuesTimeWindow == "zero"


def test_the_status_says_what_each_round_takes_part_by_part():
    """Live → Diagnose → Load: each node's update is timed, the last round's and the last
    survey's times are in the status (the same as the sensor service answers)."""
    now, ticks = [100.0], [0.0]
    computer = small_computer()
    took = {"/gpu-nvidia/0": 0.004, "/lpc/nct0000/0": 0.02}
    for item in computer.everything():
        seconds = took.get(item.Identifier, 0.001)
        item.on_update = lambda hardware, s=seconds: ticks.__setitem__(0, ticks[0] + s)
    source = opened(computer, clock=lambda: now[0], timer=lambda: ticks[0])
    assert source.status().timing == lhm.Timing()  # opened: no round yet
    source.refresh()  # no theme reads anything: a round that reads nothing
    assert source.status().timing == lhm.Timing(lhm.RoundTime(), None)
    theme = show("gpu.temp")
    source.refresh()
    last = source.status().timing.last
    assert [(p.hardware_id, p.kind) for p in last.parts] == [("/gpu-nvidia/0", "GpuNvidia")]
    assert last.seconds == pytest.approx(0.004) and not last.full
    now[0] += inside.SURVEY_SECONDS
    source.refresh()
    timing = source.status().timing
    assert timing.last is timing.full and timing.full.full and len(timing.full.parts) == 6
    assert timing.full.seconds == pytest.approx(0.004 + 0.02 + 4 * 0.001)
    assert source.status() == lhm.LhmStatus("inprocess", True, True, "2.0.1")  # (as before)
    del theme

"""Which threads are one core (sensors/topology.py), and the load of each core from them."""

import struct

from fake_sensors import Scripted, show, wait_for

from libre_panel.sensors import load, topology
from libre_panel.sensors.base import Reading


def processor_core(*threads, group=0):
    """A SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX of RelationProcessorCore, as Windows writes it."""
    mask = sum(1 << t for t in threads)
    body = struct.pack("<BB20sH", 1 if len(threads) > 1 else 0, 0, b"", 1)
    body += struct.pack("<QH6x", mask, group)
    return struct.pack("<II", 0, 8 + len(body)) + body


def test_the_cores_windows_tells():
    raw = b"".join(processor_core(2 * n, 2 * n + 1) for n in range(4))
    assert topology.parse_processor_cores(raw) == [[0, 1], [2, 3], [4, 5], [6, 7]]
    other = struct.pack("<II", 2, 16) + b"\0" * 8  # a cache, not a core: left out
    assert topology.parse_processor_cores(other + processor_core(0, 1)) == [[0, 1]]
    assert topology.parse_processor_cores(processor_core(3, group=1)) == [[67]]


def test_the_cores_linux_tells(tmp_path):
    for cpu, core in ((0, 0), (1, 1), (2, 0), (3, 1)):  # threads of a core far apart
        top = tmp_path / f"cpu{cpu}" / "topology"
        top.mkdir(parents=True)
        (top / "physical_package_id").write_text("0\n")
        (top / "core_id").write_text(f"{core}\n")
    (tmp_path / "cpu4").mkdir()  # offline: no topology
    assert sorted(topology._linux_cores(tmp_path)) == [[0, 2], [1, 3]]


def test_cores_are_used_only_when_they_hold_every_thread_once_and_one_has_two():
    assert topology.checked([[2, 3], [0, 1]], 4) == [[0, 1], [2, 3]]
    assert topology.checked([[0], [1], [2], [3]], 4) is None  # no two threads to a core
    assert topology.checked([[0, 1], [2, 3]], 6) is None  # threads not told
    assert topology.checked(None, 4) is None
    assert topology.physical_cores(4, platform="darwin") is None


def test_the_load_source_gives_every_physical_core_the_mean_of_its_threads(monkeypatch):
    monkeypatch.setattr(load, "SAMPLE_SECONDS", 0.005)
    monkeypatch.setattr(load, "_Cores", lambda: Scripted([10.0, 30.0, 80.0, 100.0]))
    monkeypatch.setattr(topology, "physical_cores", lambda threads: [[0, 2], [1, 3]])
    provider = load.LoadProvider()
    theme = show("cpu.pcore.*.load")  # (the cores alone keep the threads measured)
    try:
        assert wait_for(lambda: "cpu.pcore.2.load" in provider.read())
        got = provider.read()
        assert got["cpu.pcore.1.load"] == Reading("cpu.pcore.1.load", 45.0, "%", "Core 1")
        assert got["cpu.pcore.2.load"].value == 65.0 and got["cpu.core.4.load"].value == 100.0
    finally:
        provider.close()
    assert theme is not None

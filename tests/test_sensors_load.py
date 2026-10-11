"""The load source: CPU and GPU load ten times a second.

Windows is faked: ``GetSystemTimes`` and PDH are objects with the same methods,
the PDH arrays are real ctypes buffers filled the way Windows fills them.
"""

import ctypes
import gc
import logging
import sys
import threading
import time

import pytest
from fake_sensors import Rounds, Scripted, show, wait_for

from libre_panel.sensors import base, load
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider

# Instance names as PDH gives them: a card is named by its LUID, an engine counter
# also by the process (pid_...) that uses the engine.
BIG = "luid_0x00000000_0x0000AAAA"  # a card with memory of its own
SMALL = "luid_0x00000000_0x0000BBBB"  # the graphics of the processor
PDH_MORE_DATA = 0x800007D2
PDH_NO_DATA = 0x800007D5


@pytest.fixture(autouse=True)
def nothing_is_shown(monkeypatch):
    """Which keys themes read: only what a test says. And a quick pace."""
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    monkeypatch.setattr(load, "SAMPLE_SECONDS", 0.005)


def install(monkeypatch, cpu=None, gpu=None):
    """These samplers instead of the machine's; the list says which were asked for."""
    asked = []
    monkeypatch.setattr(load, "_cpu_sampler", lambda: asked.append("cpu") or cpu)
    monkeypatch.setattr(load, "_gpu_sampler", lambda: asked.append("gpu") or gpu)
    return asked


# -- the numbers -------------------------------------------------------------------


def test_cpu_load_between_two_samples():
    # (idle, kernel, user): the kernel time includes the idle time
    assert load.cpu_load((100, 300, 100), (150, 400, 200)) == pytest.approx(75.0)
    assert load.cpu_load((0, 0, 0), (100, 100, 0)) == 0.0  # all idle
    assert load.cpu_load((0, 0, 0), (0, 100, 100)) == 100.0  # no idle at all
    assert load.cpu_load((0, 0, 0), (300, 100, 0)) == 0.0  # counters that disagree
    assert load.cpu_load((1, 2, 3), (1, 2, 3)) is None  # no time has passed
    assert load.cpu_load(None, (1, 2, 3)) is None and load.cpu_load((1, 2, 3), None) is None


def test_the_card_with_most_memory_in_use_is_the_one_counted():
    memory = [(f"{BIG}_phys_0", 4.0e9), (f"{SMALL}_phys_0", 2.0e8), ("no_card_in_this_name", 9e12)]
    assert load.pick_adapter(memory) == BIG
    two_nodes = [(f"{SMALL}_phys_0", 3.0e8), (f"{SMALL}_phys_1", 3.0e8), (f"{BIG}_phys_0", 5.0e8)]
    assert load.pick_adapter(two_nodes) == SMALL  # a card's nodes add up
    assert load.pick_adapter([]) is None and load.pick_adapter([("x", 1.0)]) is None


def test_a_card_is_as_busy_as_its_busiest_engine():
    engines = [
        (f"pid_100_{BIG}_phys_0_eng_0_engtype_3D", 30.0),
        (f"pid_200_{BIG}_phys_0_eng_0_engtype_3D", 25.5),  # the same engine, another program
        (f"pid_100_{BIG}_phys_0_eng_1_engtype_3D", 40.0),
        (f"pid_300_{SMALL}_phys_0_eng_0_engtype_3D", 90.0),  # another card
    ]
    assert load.adapter_load(BIG, engines) == pytest.approx(55.5)
    assert load.adapter_load(SMALL, engines) == 90.0
    assert load.adapter_load(BIG, []) == 0.0
    over = [
        (f"pid_1_{BIG}_phys_0_eng_0_engtype_3D", 70.0),
        (f"pid_2_{BIG}_phys_0_eng_0_engtype_3D", 70.0),
    ]
    assert load.adapter_load(BIG, over) == 100.0  # programs add up past the limit


# -- GetSystemTimes ----------------------------------------------------------------


class FakeKernel32:
    """``GetSystemTimes`` with a script of (idle, kernel, user) samples in 100 ns;
    ``None`` is a call that fails, and so is every call after the script."""

    def __init__(self, *samples):
        self.samples = list(samples)

    def GetSystemTimes(self, idle, kernel, user):  # noqa: N802 - Windows' name
        sample = self.samples.pop(0) if self.samples else None
        if sample is None:
            return 0
        for out, value in zip((idle, kernel, user), sample, strict=True):
            out._obj.low, out._obj.high = value & 0xFFFFFFFF, value >> 32
        return 1


def test_the_processor_load_from_get_system_times():
    before = (5_000_000_000, 20_000_000_000, 6_000_000_000)  # past the 32 bits of a half
    after = (before[0] + 1_000_000, before[1] + 4_000_000, before[2] + 2_000_000)
    cpu = load._WindowsCpu(FakeKernel32(before, after))
    assert cpu.read() == pytest.approx(100 * (1 - 1 / 6))
    assert cpu.read() is None  # the call failed: no value, and no exception
    cpu.close()


def test_get_system_times_failing_at_first_costs_one_value():
    first, second = (0, 10, 10), (5, 20, 20)
    cpu = load._WindowsCpu(FakeKernel32(None, first, second))
    assert cpu.read() is None  # the first sample is there, but the one before it is not
    assert cpu.read() == pytest.approx(75.0)  # 5 idle of 20 in all


def test_windows_gets_its_own_cpu_sampler_and_falls_back_to_psutil(monkeypatch):
    kernel32 = FakeKernel32((1, 10, 10), (6, 20, 20))
    monkeypatch.setattr(load.ctypes, "WinDLL", lambda name: kernel32, raising=False)
    cpu = load._cpu_sampler("win32")
    assert isinstance(cpu, load._WindowsCpu) and cpu.read() == pytest.approx(75.0)

    def missing(name):
        raise OSError(name)

    monkeypatch.setattr(load.ctypes, "WinDLL", missing, raising=False)
    assert isinstance(load._cpu_sampler("win32"), load._PsutilCpu)


def test_psutil_gives_the_load_elsewhere():
    cpu = load._cpu_sampler("linux")
    assert isinstance(cpu, load._PsutilCpu)
    assert 0.0 <= cpu.read() <= 100.0
    cpu.close()


# -- PDH ----------------------------------------------------------------------------


class FakePdh:
    """The five PDH functions the source uses, over tables of counter instances
    ({instance: value}). The arrays are filled the way Windows fills them."""

    def __init__(self, memory, engines):
        self.memory, self.engines = memory, engines
        self.tables = {}
        self.paths = []
        self.closed = []
        self.collect_status = 0
        self.fail_second_call = False
        self.invalid = set()  # instances of programs that are just ending
        self.no_counter = ""  # a counter path (part of it) that does not exist here
        self._keep = []

    def PdhOpenQueryW(self, source, user_data, handle):  # noqa: N802
        assert source is None
        handle._obj.value = 1000
        return 0

    def PdhAddEnglishCounterW(self, query, path, user_data, handle):  # noqa: N802
        assert query.value == 1000
        if self.no_counter and self.no_counter in path:
            return 0xC0000BB8  # PDH_CSTATUS_NO_OBJECT
        self.paths.append(path)
        handle._obj.value = 2000 + len(self.paths)
        self.tables[handle._obj.value] = self.engines if "GPU Engine" in path else self.memory
        return 0

    def PdhCollectQueryData(self, query):  # noqa: N802
        return self.collect_status

    def PdhGetFormattedCounterArrayW(self, counter, fmt, size, count, buffer):  # noqa: N802
        assert fmt == 0x200  # PDH_FMT_DOUBLE
        table = self.tables[counter.value]
        if buffer is None:  # the first call asks how much room there must be
            size._obj.value = ctypes.sizeof(load._CounterItem) * len(table)
            count._obj.value = len(table)
            return PDH_MORE_DATA
        if self.fail_second_call:  # a program started in between
            return PDH_MORE_DATA
        items = (load._CounterItem * len(table)).from_buffer(buffer)
        for item, (name, value) in zip(items, table.items(), strict=True):
            item.name = name
            item.counter.status = 2 if name in self.invalid else 0
            item.counter.value = value
        self._keep.append(items)  # the names live as long as the array
        count._obj.value = len(table)
        return 0

    def PdhCloseQuery(self, query):  # noqa: N802
        self.closed.append(query.value)
        return 0


def gpu_tables():
    memory = {f"{BIG}_phys_0": 4.0e9, f"{SMALL}_phys_0": 2.0e8}
    engines = {
        f"pid_100_{BIG}_phys_0_eng_0_engtype_3D": 30.0,
        f"pid_200_{BIG}_phys_0_eng_0_engtype_3D": 25.5,
        f"pid_100_{BIG}_phys_0_eng_1_engtype_3D": 10.0,
        f"pid_300_{SMALL}_phys_0_eng_0_engtype_3D": 90.0,
    }
    return memory, engines


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_the_gpu_load_from_the_task_manager_counters():
    pdh = FakePdh(*gpu_tables())
    gpu = load._PdhGpu(pdh)
    assert pdh.paths == [
        r"\GPU Engine(*engtype_3D)\Utilization Percentage",
        r"\GPU Adapter Memory(*)\Dedicated Usage",
    ]
    assert gpu.read() == pytest.approx(55.5)  # engine 0 of the card with the memory
    pdh.engines[f"pid_200_{BIG}_phys_0_eng_0_engtype_3D"] = 0.0
    assert gpu.read() == pytest.approx(30.0)
    pdh.invalid.add(f"pid_100_{BIG}_phys_0_eng_0_engtype_3D")  # a program that is just ending
    assert gpu.read() == pytest.approx(10.0)
    gpu.close()
    assert pdh.closed == [1000]
    assert gpu.read() is None  # closed
    gpu.close()
    assert pdh.closed == [1000]


def test_the_card_is_chosen_again_every_minute():
    pdh = FakePdh(*gpu_tables())
    clock = Clock()
    gpu = load._PdhGpu(pdh, clock)
    assert gpu.read() == pytest.approx(55.5)
    pdh.memory[f"{SMALL}_phys_0"] = 9.0e9  # the other card fills up
    clock.now += 30
    assert gpu.read() == pytest.approx(55.5)  # not yet
    clock.now += 31
    assert gpu.read() == pytest.approx(90.0)


def test_pdh_without_an_answer_gives_no_value_instead_of_zero():
    pdh = FakePdh(*gpu_tables())
    gpu = load._PdhGpu(pdh)
    assert gpu.read() == pytest.approx(55.5)
    pdh.fail_second_call = True
    assert gpu.read() is None
    pdh.fail_second_call = False
    pdh.collect_status = PDH_NO_DATA
    assert gpu.read() is None
    pdh.collect_status = 0
    assert gpu.read() == pytest.approx(55.5)
    pdh.engines.clear()  # PDH lists no instance at all: no value, not 0
    assert gpu.read() is None
    pdh.engines[f"pid_1_{BIG}_phys_0_eng_0_engtype_3D"] = 0.0  # idle engines list 0
    assert gpu.read() == 0.0


def test_without_memory_in_use_there_is_no_card_to_count():
    _, engines = gpu_tables()
    assert load._PdhGpu(FakePdh({}, engines)).read() is None


def test_missing_counters_raise_after_closing_the_query():
    pdh = FakePdh(*gpu_tables())
    pdh.no_counter = "Adapter Memory"
    with pytest.raises(OSError, match="Adapter Memory"):
        load._PdhGpu(pdh)
    assert pdh.closed == [1000]  # the query is not left open


def test_only_windows_has_the_gpu_counters(monkeypatch):
    assert load._gpu_sampler("linux") is None


def test_windows_without_the_counters_fails_loudly_for_the_provider_to_say(monkeypatch):
    pdh = FakePdh(*gpu_tables())
    pdh.no_counter = "GPU Engine"
    monkeypatch.setattr(load, "_load_pdh", lambda: pdh)
    with pytest.raises(OSError, match="GPU Engine"):
        load._gpu_sampler("win32")
    assert pdh.closed == [1000]  # and the query is not left open

    def no_pdh():
        raise OSError("pdh.dll")

    monkeypatch.setattr(load, "_load_pdh", no_pdh)
    with pytest.raises(OSError, match="pdh.dll"):
        load._gpu_sampler("win32")


def test_a_machine_without_the_counters_says_so_once_and_tries_again(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=load.__name__)
    monkeypatch.setattr(load, "RETRY_SECONDS", 0.005)
    attempts, gpu = [], Scripted(55.5)

    def gpu_sampler():
        attempts.append(1)
        if len(attempts) < 3:
            return None  # not on this machine
        if len(attempts) < 5:
            raise OSError("no counters yet")  # the counters are not ready, right after login
        return gpu

    monkeypatch.setattr(load, "_cpu_sampler", lambda: Scripted(30.0))
    monkeypatch.setattr(load, "_gpu_sampler", gpu_sampler)
    theme = show("gpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        assert wait_for(lambda: "gpu.load" in provider.read())  # the fifth try worked
        assert len(attempts) == 5 and provider.read()["gpu.load"].value == 55.5
    finally:
        provider.close()
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert said == ["load: no GPU load from the performance counters (no counters yet)"]
    del theme


def test_what_is_missing_is_said_once_for_the_provider_not_for_each_thread(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=load.__name__)

    def no_counters():
        raise OSError("no counters")

    monkeypatch.setattr(load, "_gpu_sampler", no_counters)
    provider = load.LoadProvider()
    assert provider._make("gpu.load") is None and provider._make("gpu.load") is None
    levels = [r.levelno for r in caplog.records]
    assert levels == [logging.INFO, logging.DEBUG]

    def no_psutil():
        raise RuntimeError("no psutil")

    monkeypatch.setattr(load, "_cpu_sampler", no_psutil)
    assert provider._make("cpu.load") is None  # the other key has its own line
    assert "no CPU load from psutil" in caplog.records[-1].getMessage()


def test_windows_gets_its_gpu_sampler(monkeypatch):
    pdh = FakePdh(*gpu_tables())
    monkeypatch.setattr(load, "_load_pdh", lambda: pdh)
    gpu = load._gpu_sampler("win32")
    assert isinstance(gpu, load._PdhGpu) and gpu.read() == pytest.approx(55.5)


# -- the thread ---------------------------------------------------------------------


def test_nothing_is_measured_while_no_theme_reads_the_load(monkeypatch):
    asked = install(monkeypatch, Scripted(10.0), Scripted(20.0))
    provider = load.LoadProvider()
    assert provider.read() == {} and provider._thread is None
    theme = show("cpu.temp", "mem.load", "net.*")  # other keys
    assert provider.read() == {} and provider._thread is None and asked == []
    provider.close()
    del theme


def test_a_thread_measures_while_a_theme_reads_the_load(monkeypatch):
    cpu = Scripted(12.5, 40.0)
    asked = install(monkeypatch, cpu, Scripted(99.0))
    theme = show("cpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()  # this call starts the thread
        thread = provider._thread
        assert thread.daemon and thread.name == "sensors-load" and thread.is_alive()
        assert wait_for(lambda: "cpu.load" in provider.read())
        reading = provider.read()["cpu.load"]
        assert reading.key == "cpu.load" and reading.value in (12.5, 40.0)
        assert (reading.unit, reading.label) == ("%", "CPU load")  # as PsutilProvider's
        assert "gpu.load" not in provider.read() and asked == ["cpu"]
        assert provider._thread is thread  # read() does not start another
    finally:
        provider.close()
    assert not thread.is_alive() and cpu.closed.is_set()
    del theme


def test_gpu_load_comes_where_there_is_a_gpu_source(monkeypatch):
    cpu, gpu = Scripted(30.0), Scripted(55.5)
    install(monkeypatch, cpu, gpu)
    theme = show("gpu.*", "cpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        assert wait_for(lambda: len(provider.read()) == 2)
        reading = provider.read()["gpu.load"]
        assert (reading.value, reading.unit, reading.label) == (55.5, "%", "GPU load")
    finally:
        provider.close()
    assert cpu.closed.is_set() and gpu.closed.is_set()
    del theme


def test_only_what_a_theme_reads_is_measured(monkeypatch):
    cpu, gpu = Scripted(30.0), Scripted(55.5)
    asked = install(monkeypatch, cpu, gpu)
    theme = show("gpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        assert wait_for(lambda: "gpu.load" in provider.read())
        assert set(provider.read()) == {"gpu.load"} and asked == ["gpu"] and cpu.reads == 0
        theme2 = show("cpu.load")  # a second theme appears (a switch): the CPU joins in
        assert wait_for(lambda: "cpu.load" in provider.read())
        del theme2
        gc.collect()
        assert wait_for(lambda: "cpu.load" not in provider.read())  # and goes with it
        assert "gpu.load" in provider.read()
    finally:
        provider.close()
    del theme


def test_where_there_is_no_gpu_source_the_key_is_missing_and_asked_for_only_now_and_then(
    monkeypatch,
):
    cpu = Scripted(30.0)
    asked = install(monkeypatch, cpu, None)
    theme = show("cpu.load", "gpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        assert wait_for(lambda: cpu.reads >= 4)
        assert set(provider.read()) == {"cpu.load"}
        assert asked.count("gpu") == 1 and asked.count("cpu") == 1
    finally:
        provider.close()
    del theme


def test_a_new_sampler_gets_a_round_to_have_something_to_compare_with(monkeypatch):
    events = []

    class Probe(Scripted):
        def read(self):
            events.append("read")
            return super().read()

    monkeypatch.setattr(load, "_cpu_sampler", lambda: events.append("open") or Probe(5.0))
    monkeypatch.setattr(load, "_gpu_sampler", lambda: None)
    theme = show("cpu.load")
    provider = load.LoadProvider()
    provider._stop = Rounds(events)
    try:
        provider.read()
        assert wait_for(lambda: "read" in events)
    finally:
        provider.close()
    assert events[:3] == ["open", "wait", "read"]
    del theme


def test_samples_that_fail_do_not_stop_the_thread(monkeypatch):
    cpu = Scripted(RuntimeError("counter gone"), None, 61.0)
    install(monkeypatch, cpu)
    theme = show("cpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        assert wait_for(lambda: "cpu.load" in provider.read())
        assert provider.read()["cpu.load"].value == 61.0
    finally:
        provider.close()
    del theme


def test_values_are_kept_to_the_range(monkeypatch):
    install(monkeypatch, Scripted(112.0))
    theme = show("cpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        assert wait_for(lambda: "cpu.load" in provider.read())
        assert provider.read()["cpu.load"].value == 100.0
    finally:
        provider.close()
    del theme


def test_a_sampler_that_cannot_be_made_is_tried_again_only_now_and_then(monkeypatch):
    tries = []

    def broken():
        tries.append(1)
        raise RuntimeError("no way")

    monkeypatch.setattr(load, "_cpu_sampler", broken)
    theme = show("cpu.load")
    provider = load.LoadProvider()
    provider._stop = Rounds()
    try:
        assert provider.read() == {}
        assert wait_for(lambda: provider._stop.count >= 5)
        assert tries == [1] and provider.read() == {} and provider._thread.is_alive()
    finally:
        provider.close()
    del theme


def test_old_readings_are_dropped_not_shown_frozen(monkeypatch):
    cpu = Scripted(None)  # the sampler has nothing to say any more
    install(monkeypatch, cpu)
    theme = show("cpu.load")
    provider = load.LoadProvider()
    try:
        provider._store("cpu.load", 5.0, time.monotonic())
        assert provider.read()["cpu.load"].value == 5.0
        provider._store("cpu.load", 5.0, time.monotonic() - load.STALE_SECONDS - 1)
        assert provider.read() == {}
    finally:
        provider.close()
    del theme


def test_closing_ends_the_thread_and_it_stays_ended(monkeypatch):
    cpu = Scripted(30.0)
    install(monkeypatch, cpu)
    theme = show("cpu.load")
    provider = load.LoadProvider()
    provider.read()
    thread = provider._thread
    assert wait_for(lambda: "cpu.load" in provider.read())
    provider.close()
    assert not thread.is_alive() and cpu.closed.is_set()
    assert provider.read() == {} and provider._thread is thread  # no second thread
    provider.close()
    load.LoadProvider().close()  # one that never ran
    del theme


def test_the_thread_ends_when_nobody_looks_and_starts_again_on_demand(monkeypatch):
    first, second = Scripted(30.0), Scripted(70.0)
    samplers = [first, second]
    monkeypatch.setattr(load, "_cpu_sampler", lambda: samplers.pop(0))
    monkeypatch.setattr(load, "IDLE_SECONDS", 0.05)
    theme = show("cpu.load")
    provider = load.LoadProvider()
    try:
        provider.read()
        thread = provider._thread
        assert wait_for(lambda: "cpu.load" in provider.read())
        del theme
        gc.collect()  # the theme is gone
        assert wait_for(lambda: not thread.is_alive())
        assert first.closed.is_set() and provider.read() == {}
        theme = show("cpu.load")  # a theme that reads the load again
        provider.read()
        assert provider._thread is not thread
        assert wait_for(lambda: provider.read().get("cpu.load") is not None)
        assert provider.read()["cpu.load"].value == 70.0
    finally:
        provider.close()
    assert second.closed.is_set()
    del theme


# -- the hub -------------------------------------------------------------------------


class Fixed(SensorProvider):
    name = "fixed"

    def __init__(self, values):
        super().__init__()
        self.values = values

    def read(self):
        return {k: Reading(k, v, "%", "slow") for k, v in self.values.items()}


def test_the_load_source_wins_when_it_comes_first_and_follows_between_snapshots(monkeypatch):
    cpu = Scripted(77.0)
    cpu.gate = threading.Event()
    install(monkeypatch, cpu)
    theme = show("cpu.load")
    fast = load.LoadProvider()
    slow = Fixed({"cpu.load": 5.0, "mem.load": 40.0})
    hub = SensorHub([fast, slow])
    try:
        assert hub.every_frame
        assert hub.snapshot().value("cpu.load") == 5.0  # nothing measured yet: the next source
        cpu.gate.set()
        assert wait_for(lambda: hub.snapshot().value("cpu.load") == 77.0)
        assert hub.snapshot().value("mem.load") == 40.0
        cpu.values = [80.0]  # it moves on: the next frame has it, no snapshot needed
        assert wait_for(lambda: "cpu.load" in hub.fresh() and hub.fresh()["cpu.load"].value == 80.0)
    finally:
        hub.close()
    del theme


def test_a_source_before_it_keeps_its_keys(monkeypatch):
    install(monkeypatch, Scripted(77.0))
    theme = show("cpu.load")
    fast = load.LoadProvider()
    slow = Fixed({"cpu.load": 5.0})
    hub = SensorHub([slow, fast])  # LibreHardwareMonitor first, say
    try:
        fast.read()
        assert wait_for(lambda: "cpu.load" in fast.read())
        assert hub.snapshot().value("cpu.load") == 5.0
        assert "cpu.load" not in hub.fresh()
    finally:
        hub.close()
    del theme


def test_the_provider_is_built_in():
    provider = base.create_provider("load", {})
    assert isinstance(provider, load.LoadProvider) and provider.name == "load"
    assert provider.every_frame == "video"  # on the PNG path it keeps to the readings
    provider.close()
    assert base.available_providers()["load"] == "built in"


@pytest.mark.skipif(sys.platform != "win32", reason="GetSystemTimes and PDH are Windows'")
def test_the_real_counters_of_windows():
    cpu = load._cpu_sampler()
    assert isinstance(cpu, load._WindowsCpu)
    pause = threading.Event()
    pause.wait(0.15)
    value = cpu.read()
    assert value is not None and 0.0 <= value <= 100.0
    gpu = load._gpu_sampler()  # None where the counters do not exist (a virtual machine)
    if gpu is not None:
        pause.wait(0.15)
        value = gpu.read()
        assert value is None or 0.0 <= value <= 100.0
        gpu.close()


def test_between_readings_the_load_follows_only_in_video_mode():
    """On the PNG path every changed bar is one more picture to send: there the
    load keeps to the readings; a volume follows on every frame either way."""
    from libre_panel.sensors.base import Reading, SensorHub, SensorProvider

    class Source(SensorProvider):
        def __init__(self, key, every):
            super().__init__()
            self.key, self.every_frame, self.value = key, every, 0.0

        def read(self):
            self.value += 1
            return {self.key: Reading(self.key, self.value)}

    hub = SensorHub([Source("cpu.load", "video"), Source("audio.volume", True)])
    hub.snapshot()
    assert set(hub.fresh(video=True)) == {"cpu.load", "audio.volume"}
    assert set(hub.fresh(video=False)) == {"audio.volume"}

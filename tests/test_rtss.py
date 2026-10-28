"""Game frame rates from RivaTuner Statistics Server (sensors/rtss.py): the memory RTSS shares,
read as its SDK lays it out; samples of the last frame time while a program draws; the game
the game service found; and on Windows the real thing, a named file mapping (discussion #32)."""

import sys

import pytest

from libre_panel.config import default_providers
from libre_panel.sensors import presentmon, rtss
from libre_panel.sensors.base import _BUILTIN_PROVIDERS

ENTRY_SIZE = 4096  # (RTSS's entries are far longer than the fields read: the OSD's, the stats')
OFFSET = 0x100  # (its header too)


def table(*programs, signature=rtss.SIGNATURE, version=0x00020015):
    """RTSS's memory with ``programs``: (pid, path, time0, time1, frames, frame time in µs)."""
    data = bytearray(OFFSET + ENTRY_SIZE * (len(programs) + 1))
    data[: rtss.HEADER.size] = rtss.HEADER.pack(signature, version, ENTRY_SIZE, OFFSET,
                                                 len(programs) + 1)  # fmt: skip
    for n, (pid, path, time0, time1, frames, frame_us) in enumerate(programs):
        at = OFFSET + n * ENTRY_SIZE
        entry = rtss.ENTRY.pack(pid, path.encode("latin-1"), 0, time0, time1, frames, frame_us)
        data[at : at + rtss.ENTRY.size] = entry
    return bytes(data)  # (the last entry is free: pid 0)


class Memory:
    def __init__(self, data):
        self.data, self.closed = data, False

    def read(self, offset, size):
        if offset + size > len(self.data):
            raise ValueError("beyond")
        return self.data[offset : offset + size]

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_game():
    presentmon.set_target(None)
    yield
    presentmon.set_target(None)


def test_the_memory_is_read_as_rtss_lays_it_out():
    memory = Memory(table((4242, "C:\\Games\\Doom\\DOOMEternalx64vk.exe", 1000, 2000, 144, 6944),
                          (77, "D:/Other/game.exe", 0, 0, 0, 0)))  # fmt: skip
    found = rtss.entries(memory)
    assert [(e.pid, e.app, e.frames, e.frame_us) for e in found] == [
        (4242, "DOOMEternalx64vk.exe", 144, 6944),
        (77, "game.exe", 0, 0),
    ]  # (the free entry left out)
    assert rtss.entries(Memory(table((1, "a.exe", 0, 0, 0, 0), signature=0x44414544))) == []
    assert rtss.entries(Memory(table((1, "a.exe", 0, 0, 0, 0), version=0x00010005))) == []
    with pytest.raises(ValueError):  # (an entry beyond what is there: gone, looked for again)
        rtss.entries(Memory(table((1, "a.exe", 0, 0, 0, 0))[: OFFSET + 8]))


def drive(provider, frames, start=0.0, step=0.1):
    """Looks at RTSS's memory, one every ``step`` s: ``frames(n)`` is each look's programs."""
    now = start
    for n in range(frames.count):
        provider.poll(Memory(table(*frames(n))), now)
        now += step
    return now


class Frames:
    def __init__(self, count, programs):
        self.count, self.programs = count, programs

    def __call__(self, n):
        return self.programs(n)


def test_a_game_s_frame_times_become_its_readings():
    """The last frame time of each look while the game draws: 144 fps, as PresentMon would say
    it, with the round's figures; the program's name without .exe."""
    provider = rtss.RTSSProvider()
    # RTSS's period is a second: its end moves on every ten looks; the frame time each look
    frames = Frames(30, lambda n: [(10, "C:\\G\\Space Game.exe", 0, 1000 * (n // 10), 144,
                                    6900 + 30 * (n % 3))])  # fmt: skip
    now = drive(provider, frames)
    got = provider._counter.readings(now)
    assert got["game.app"].value == "Space Game"
    assert got["game.fps"].value == pytest.approx(144, abs=2)
    assert got["game.frametime"].value == pytest.approx(6.93, abs=0.05)
    assert got["game.low"].value == pytest.approx(1000 / 6.96, abs=1)
    assert "game.fps_avg" in got and "game.limiter" not in got  # (PresentMon's alone)


def test_a_game_capped_at_an_even_frame_time_still_counts_and_one_that_stops_does_not():
    """A frame cap keeps the frame time the same look after look; RTSS's period still moves on
    once a second, so the game draws. Once nothing of its entry moves for two seconds, it does
    not: no more samples, and its readings go."""
    provider = rtss.RTSSProvider()
    capped = Frames(30, lambda n: [(5, "capped.exe", 0, 1000 * (n // 10), 60, 16667)])
    now = drive(provider, capped)
    assert provider._counter.readings(now)["game.fps"].value == pytest.approx(60, abs=0.5)
    stalled = Frames(40, lambda n: [(5, "capped.exe", 0, 2000, 60, 16667)])  # (nothing moves)
    now = drive(provider, stalled, start=now)
    assert provider._counter.readings(now + 4) == {}


def test_the_game_the_game_service_found_counts():
    provider = rtss.RTSSProvider()
    presentmon.set_target("Game.exe")
    both = Frames(20, lambda n: [(1, "C:\\launcher.exe", 0, 100 * n, 300, 3333 + n),
                                 (2, "C:\\G\\game.exe", 0, 100 * n, 50, 20000 + n)])  # fmt: skip
    now = drive(provider, both)
    got = provider._counter.readings(now)
    assert got["game.app"].value == "game" and got["game.fps"].value == pytest.approx(50, abs=1)


def test_rtss_is_an_alternative_to_switch_on_not_a_default():
    """PresentMon stays the frame rate's source; RTSS is listed in Settings → Sensors to switch
    on, asked after PresentMon (its readings come first where both have them)."""
    assert "rtss" in _BUILTIN_PROVIDERS
    assert "rtss" not in default_providers("win32") and "presentmon" in default_providers("win32")
    provider = rtss.RTSSProvider(opener=lambda name: None)
    assert provider.status() == {"rivatuner": None}  # (nothing asked for game.* yet)
    assert rtss.open_memory() is None or sys.platform == "win32"  # (no RTSS but on Windows)


@pytest.mark.skipif(sys.platform != "win32", reason="RTSS's memory is a Windows file mapping")
def test_windows_shares_the_memory_as_rtss_does():
    """A named file mapping, as RTSS makes one: Windows' own calls open and read it."""
    import mmap

    name = f"LibrePanelTestRTSS{id(object())}"
    data = table((321, "C:\\Games\\Real\\real.exe", 5000, 6000, 120, 8333))
    shared = mmap.mmap(-1, len(data), tagname=name)
    try:
        shared.write(data)
        memory = rtss.open_memory(name)
        assert memory is not None
        try:
            (entry,) = rtss.entries(memory)
            assert (entry.pid, entry.app, entry.frames, entry.frame_us) == (321, "real.exe",
                                                                            120, 8333)  # fmt: skip
        finally:
            memory.close()
        assert rtss.open_memory(name + "-none") is None  # (none made where there is none)
    finally:
        shared.close()

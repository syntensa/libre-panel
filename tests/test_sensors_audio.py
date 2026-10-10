"""The audio source: volume and mute of the default output.

Windows is faked below the code under test: Core Audio objects are real tables of C
functions (so the code calls them the way it calls Windows', reference counts
included), and the tools of Linux and macOS are fake subprocess runs.
"""

import ctypes
import gc
import logging
import subprocess
import sys
import threading

import pytest
from fake_sensors import FakeSource, Rounds, show, wait_for

from libre_panel.sensors import audio, base

c_int32, c_uint32, c_void_p = ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p
POINTER = ctypes.POINTER
E_NOTFOUND = 0x80070490 - 2**32  # no such device, as a signed HRESULT
RPC_E_CHANGED_MODE = 0x80010106 - 2**32
LOGGER = audio.__name__


@pytest.fixture(autouse=True)
def nothing_is_shown(monkeypatch):
    """Which keys themes read: only what a test says. And quick paces."""
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    monkeypatch.setattr(audio, "RETRY_SECONDS", 0.005)


# -- the tools of Linux and macOS ----------------------------------------------------


def test_wpctl_prints_the_volume_as_a_fraction():
    assert audio.parse_wpctl("Volume: 0.37\n") == (pytest.approx(37.0), False)
    assert audio.parse_wpctl("Volume: 0.00 [MUTED]\n") == (0.0, True)
    assert audio.parse_wpctl("Volume: 1.50\n") == (pytest.approx(150.0), False)  # amplified
    assert audio.parse_wpctl("Could not connect to PipeWire\n") is None
    assert audio.parse_wpctl("") is None


def test_pactl_prints_percent_per_channel_and_the_mute_apart():
    volume = (
        "Volume: front-left: 24248 /  37% / -26.45 dB,   front-right: 26214 /  40% / -23.98 dB\n"
        "        balance 0.07\n"
    )
    assert audio.parse_pactl_volume(volume) == 37.0  # the first channel
    assert audio.parse_pactl_volume("Volume: mono: 65536 / 100% / 0.00 dB\n") == 100.0
    assert audio.parse_pactl_volume("Connection failure: Connection refused\n") is None
    assert audio.parse_pactl_mute("Mute: no\n") is False
    assert audio.parse_pactl_mute("Mute: yes\n") is True
    assert audio.parse_pactl_mute("") is None


def test_osascript_prints_a_record():
    line = "output volume:37, input volume:75, alert volume:100, output muted:false\n"
    assert audio.parse_osascript(line) == (37.0, False)
    assert audio.parse_osascript(line.replace("false", "true")) == (37.0, True)
    assert audio.parse_osascript("output volume:0, output muted:true") == (0.0, True)
    hdmi = (
        "output volume:missing value, input volume:75, alert volume:100, output muted:missing value"
    )
    assert audio.parse_osascript(hdmi) is None  # an output without a volume of its own
    assert audio.parse_osascript("") is None


class FakeTools:
    """``subprocess.run`` and ``shutil.which`` for the tools: which are installed, what
    each command prints (a missing command fails, an exception is raised)."""

    def __init__(self, monkeypatch, installed, prints):
        self.installed, self.prints, self.calls = installed, prints, []
        monkeypatch.setattr(audio.shutil, "which", self.which)
        monkeypatch.setattr(audio.subprocess, "run", self.run)

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.installed else None

    def run(self, command, **options):
        self.calls.append((list(command), options))
        printed = self.prints.get(tuple(command))
        if isinstance(printed, Exception):
            raise printed
        if printed is None:
            return subprocess.CompletedProcess(command, 1, "", "failed")
        return subprocess.CompletedProcess(command, 0, printed, "")

    @property
    def commands(self):
        return [call[0] for call in self.calls]


WPCTL = ("/usr/bin/wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
PACTL_VOLUME = ("/usr/bin/pactl", "get-sink-volume", "@DEFAULT_SINK@")
PACTL_MUTE = ("/usr/bin/pactl", "get-sink-mute", "@DEFAULT_SINK@")
OSASCRIPT = ("/usr/bin/osascript", "-e", "get volume settings")
PACTL_PRINTS = {
    PACTL_VOLUME: "Volume: front-left: 24248 /  37% / -26.45 dB\n",
    PACTL_MUTE: "Mute: yes\n",
}


def test_pipewire_is_asked_with_wpctl(monkeypatch):
    tools = FakeTools(monkeypatch, {"wpctl", "pactl"}, {WPCTL: "Volume: 0.37\n"})
    source = audio._open_source(lambda level, muted: None, "linux")
    assert source.sample() == (pytest.approx(37.0), False)
    assert tools.commands == [list(WPCTL)]  # pactl is not asked while wpctl answers
    options = tools.calls[0][1]
    assert options["env"]["LC_ALL"] == "C"  # the texts are English
    assert options["timeout"] == audio.COMMAND_TIMEOUT and options["capture_output"]
    assert source.interval == audio.LINUX_POLL_SECONDS == 0.25  # four times a second
    assert source.events is False and source.check_device() is False
    source.close()


def test_pulseaudio_is_asked_with_pactl_where_there_is_no_wpctl(monkeypatch):
    tools = FakeTools(monkeypatch, {"pactl"}, PACTL_PRINTS)
    source = audio._open_source(lambda level, muted: None, "linux")
    assert source.sample() == (37.0, True)
    assert tools.commands == [list(PACTL_VOLUME), list(PACTL_MUTE)]


def test_the_tool_that_answers_is_the_one_that_is_asked(monkeypatch):
    # wpctl is installed, but PipeWire is not what this system runs
    tools = FakeTools(monkeypatch, {"wpctl", "pactl"}, PACTL_PRINTS)
    source = audio._open_source(lambda level, muted: None, "linux")
    assert source.sample() == (37.0, True)
    assert tools.commands[0] == list(WPCTL)  # tried first, failed
    del tools.calls[:]
    assert source.sample() == (37.0, True)
    assert WPCTL not in [tuple(command) for command in tools.commands]  # and not again


def test_a_volume_without_a_mute_answer_is_not_muted(monkeypatch):
    FakeTools(monkeypatch, {"pactl"}, {PACTL_VOLUME: PACTL_PRINTS[PACTL_VOLUME]})
    source = audio._open_source(lambda level, muted: None, "linux")
    assert source.sample() == (37.0, False)


def test_macos_is_asked_with_osascript(monkeypatch):
    line = "output volume:37, input volume:75, alert volume:100, output muted:false\n"
    tools = FakeTools(monkeypatch, {"osascript"}, {OSASCRIPT: line})
    source = audio._open_source(lambda level, muted: None, "darwin")
    assert source.sample() == (37.0, False)
    assert tools.commands == [list(OSASCRIPT)]
    assert source.interval == audio.MACOS_POLL_SECONDS == 0.5  # twice a second


def test_without_the_tools_there_is_no_source(monkeypatch):
    FakeTools(monkeypatch, set(), {})
    with pytest.raises(OSError, match="needs wpctl or pactl"):
        audio._open_source(lambda level, muted: None, "linux")
    with pytest.raises(OSError, match="needs osascript"):
        audio._open_source(lambda level, muted: None, "darwin")
    with pytest.raises(OSError, match="needs wpctl or pactl"):  # the other Unixes
        audio._open_source(lambda level, muted: None, "freebsd14")


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("wpctl", 2), FileNotFoundError()])
def test_a_tool_that_hangs_or_vanishes_gives_no_value(monkeypatch, failure):
    FakeTools(monkeypatch, {"wpctl"}, {WPCTL: failure})
    source = audio._open_source(lambda level, muted: None, "linux")
    assert source.sample() is None


def test_a_tool_that_keeps_failing_is_asked_less_often(monkeypatch):
    tools = FakeTools(monkeypatch, {"wpctl"}, {})
    source = audio._open_source(lambda level, muted: None, "linux")
    for _ in range(audio.FAILURES_BEFORE_SLOW - 1):
        assert source.sample() is None
        assert source.interval == audio.LINUX_POLL_SECONDS
    assert source.sample() is None
    assert source.interval == audio.SLOW_POLL_SECONDS
    tools.prints[WPCTL] = "Volume: 0.50\n"  # the audio server is back
    assert source.sample() == (pytest.approx(50.0), False)
    assert source.interval == audio.LINUX_POLL_SECONDS


# -- Core Audio, faked below the code ----------------------------------------------------


class ComObject:
    """A COM object for the code under test: a real table of C functions, called the
    way Windows' objects are. ``refs`` counts the references handed out and not yet
    released: 0 at the end means nothing leaked. ``slots``: {slot number: (result
    type, argument types, function(this, ...))}; slot 2 (Release) is built in."""

    def __init__(self, name, slots):
        self.name = name
        self.refs = 0
        slots = {2: (c_uint32, (), self._release), **slots}
        size = max(slots) + 1
        self._callbacks = []
        addresses = [0] * size
        for slot, (result, arguments, function) in slots.items():
            callback = audio._FUNCTYPE(result, c_void_p, *arguments)(function)
            self._callbacks.append(callback)
            addresses[slot] = ctypes.cast(callback, c_void_p).value
        self._table = (c_void_p * size)(*addresses)
        self._object = (c_void_p * 1)(ctypes.addressof(self._table))
        self.address = ctypes.addressof(self._object)

    def _release(self, this):
        self.refs -= 1
        return max(self.refs, 0)

    def give(self, out):
        """Hand a new reference to the caller, who gave a ``POINTER(c_void_p)``."""
        self.refs += 1
        out[0] = self.address

    def give_to(self, handle):
        """The same for a ``c_void_p`` object."""
        self.refs += 1
        handle.value = self.address


class Device:
    """An output device: an IMMDevice (gives its id and its volume control) and an
    IAudioEndpointVolume (level, mute, change callbacks)."""

    def __init__(self, world, name, level, muted):
        self.world, self.name, self.level, self.muted = world, name, level, muted
        self.callback = None  # the address of the registered callback object
        self.registered = self.unregistered = self.activated = 0
        self.refuse_events = False
        self.fail_reads = False
        self.com = ComObject(
            f"{name} (IMMDevice)",
            {
                3: (c_int32, (POINTER(audio._Guid), c_uint32, c_void_p, POINTER(c_void_p)),
                    self._activate),
                5: (c_int32, (POINTER(ctypes.c_wchar_p),), self._get_id),
            },
        )  # fmt: skip
        self.volume = ComObject(
            f"{name} (IAudioEndpointVolume)",
            {
                3: (c_int32, (c_void_p,), self._register),
                4: (c_int32, (c_void_p,), self._unregister),
                9: (c_int32, (POINTER(ctypes.c_float),), self._get_level),
                15: (c_int32, (POINTER(c_int32),), self._get_mute),
            },
        )

    def _activate(self, this, iid, context, parameters, out):
        assert bytes(iid.contents) == bytes(
            audio._Guid.of("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        )
        self.activated += 1
        self.volume.give(out)
        return 0

    def _get_id(self, this, out):
        buffer = ctypes.create_unicode_buffer(self.name)
        self.world.strings[ctypes.addressof(buffer)] = buffer
        ctypes.cast(out, POINTER(c_void_p))[0] = ctypes.addressof(buffer)
        return 0

    def _register(self, this, callback):
        if self.refuse_events:
            return E_NOTFOUND
        self.callback = callback
        self.registered += 1
        return 0

    def _unregister(self, this, callback):
        assert callback == self.callback
        self.callback = None
        self.unregistered += 1
        return 0

    def _get_level(self, this, out):
        if self.fail_reads:
            return E_NOTFOUND
        out[0] = self.level
        return 0

    def _get_mute(self, this, out):
        out[0] = int(self.muted)
        return 0

    def notify(self, level, muted):
        """What Windows does when the volume changes: OnNotify of the registered callback."""
        data = audio._NotificationData(muted=int(muted), master_volume=level, channels=1)
        on_notify = audio._method(self.callback, 3, POINTER(audio._NotificationData))
        return on_notify(self.callback, ctypes.byref(data))

    def query(self, iid):
        """QueryInterface of the registered callback: (HRESULT, the pointer it gives)."""
        out = c_void_p()
        method = audio._method(self.callback, 0, POINTER(audio._Guid), POINTER(c_void_p))
        return method(
            self.callback, ctypes.byref(audio._Guid.of(iid)), ctypes.byref(out)
        ), out.value


class FakeOle32:
    def __init__(self, world):
        self.world = world
        self.init_result = 0  # S_OK
        self.inits, self.uninits = [], []

    def CoInitializeEx(self, reserved, mode):  # noqa: N802 - Windows' name
        self.inits.append((mode, threading.current_thread()))
        return self.init_result

    def CoUninitialize(self):  # noqa: N802
        self.uninits.append(threading.current_thread())

    def CoCreateInstance(self, clsid, outer, context, iid, out):  # noqa: N802
        assert bytes(clsid._obj) == bytes(audio._Guid.of("{BCDE0395-E52F-467C-8E3D-C4579291692E}"))
        assert bytes(iid._obj) == bytes(audio._Guid.of("{A95664D2-9614-4F35-A746-DE8DB63617E6}"))
        self.world.enumerator.give_to(out._obj)
        return 0

    def CoTaskMemFree(self, text):  # noqa: N802
        address = ctypes.cast(text, c_void_p).value
        assert address in self.world.strings, "freed memory Windows did not hand out"
        del self.world.strings[address]
        self.world.freed += 1


class World:
    """The audio side of a machine: output devices, one of them the default."""

    def __init__(self):
        self.devices = {}
        self.default = None
        self.strings = {}  # what GetId handed out and nobody freed yet
        self.freed = 0
        self.ole32 = FakeOle32(self)
        self.enumerator = ComObject(
            "enumerator",
            {4: (c_int32, (c_int32, c_int32, POINTER(c_void_p)), self._default_endpoint)},
        )

    def add(self, name, level=0.5, muted=False, default=True):
        self.devices[name] = Device(self, name, level, muted)
        if default:
            self.default = name
        return self.devices[name]

    def _default_endpoint(self, this, flow, role, out):
        assert (flow, role) == (0, 0)  # eRender, eConsole: the device sound plays on
        if self.default is None:
            return E_NOTFOUND
        self.devices[self.default].com.give(out)
        return 0

    @property
    def leaks(self):
        """What was handed out and not released, by object."""
        objects = [self.enumerator]
        for device in self.devices.values():
            objects += [device.com, device.volume]
        found = {obj.name: obj.refs for obj in objects if obj.refs}
        if self.strings:
            found["ids"] = len(self.strings)
        return found


def core_audio(world, on_change=lambda level, muted: None):
    return audio._CoreAudio(on_change, ole32=world.ole32)


def test_the_ids_of_core_audio_have_their_layout():
    assert ctypes.sizeof(audio._Guid) == 16 and ctypes.sizeof(audio._NotificationData) == 32
    # the well-known MMDeviceEnumerator class id, as bytes in memory
    clsid = bytes(audio._Guid.of(audio._CLSID_ENUMERATOR))
    assert clsid == bytes.fromhex("9503debc2fe57c468e3dc4579291692e")
    assert audio._IID_ENUMERATOR == "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    assert audio._IID_ENDPOINT_VOLUME == "{5CDF2C82-841E-4546-9722-0CF74078229A}"
    assert audio._IID_VOLUME_CALLBACK == "{657804FA-D6AD-4496-8A60-352752AF4F89}"
    assert audio._IID_UNKNOWN == "{00000000-0000-0000-C000-000000000046}"


def test_core_audio_gives_level_and_mute_of_the_default_device():
    world = World()
    speakers = world.add("speakers", level=0.37)
    source = core_audio(world)
    try:
        level, muted = source.sample()
        assert level == pytest.approx(37.0, abs=1e-3) and muted is False
        speakers.muted = True
        assert source.sample()[1] is True
        speakers.level = 1.0
        assert source.sample()[0] == 100.0
        assert source.events and source.interval == audio.EVENT_POLL_SECONDS == 0.25
        assert (world.ole32.inits[0][0], speakers.activated) == (0, 1)  # multi-threaded COM
    finally:
        source.close()
    assert world.leaks == {}  # every reference was released again
    assert len(world.ole32.uninits) == 1


def test_no_com_pointer_is_kept_by_the_checks_every_two_seconds():
    """SPUR II lost one IMMDevice reference every two seconds."""
    world = World()
    speakers = world.add("speakers")
    source = core_audio(world)
    try:
        assert world.leaks == {"enumerator": 1, "speakers (IAudioEndpointVolume)": 1}
        for _ in range(100):
            assert source.check_device() is False
        assert speakers.com.refs == 0 and speakers.activated == 1
        assert world.freed == 101  # and every device id was given back to Windows
    finally:
        source.close()
    assert world.leaks == {}


def test_a_new_default_device_is_followed_and_nothing_is_left_on_the_old_one():
    world = World()
    speakers = world.add("speakers", level=0.30)
    headset = world.add("headset", level=0.80, default=False)
    source = core_audio(world)
    try:
        assert source.sample()[0] == pytest.approx(30.0, abs=1e-3)
        world.default = "headset"  # a headset was plugged in
        assert source.check_device() is True
        assert speakers.callback is None and speakers.unregistered == 1  # no more news from it
        assert headset.callback == source._callback.pointer.value and headset.registered == 1
        assert source.sample()[0] == pytest.approx(80.0, abs=1e-3)
        assert source.events and source.check_device() is False  # nothing new
        assert world.leaks == {"enumerator": 1, "headset (IAudioEndpointVolume)": 1}
        world.default = None  # unplugged, nothing else to play on: keep looking
        assert source.check_device() is False
    finally:
        source.close()
    assert world.leaks == {} and headset.unregistered == 1


def test_changes_arrive_through_the_hand_built_callback():
    world = World()
    speakers = world.add("speakers")
    changes = []
    source = core_audio(world, lambda level, muted: changes.append((level, muted)))
    try:
        assert speakers.notify(0.25, False) == 0
        assert speakers.notify(1.5, True) == 0  # out of range: kept to 0-100
        assert speakers.notify(-0.5, False) == 0
        assert changes == [(pytest.approx(25.0), False), (100.0, True), (0.0, False)]
    finally:
        source.close()
    assert speakers.callback is None  # unregistered before it went away


def test_an_error_in_the_callback_never_reaches_windows():
    world = World()
    speakers = world.add("speakers")

    def broken(level, muted):
        raise RuntimeError("the panel is gone")

    source = core_audio(world, broken)
    try:
        assert speakers.notify(0.5, False) == 0  # S_OK, and no exception here either
    finally:
        source.close()


def test_the_callback_answers_for_its_two_interfaces_only():
    world = World()
    speakers = world.add("speakers")
    source = core_audio(world)
    try:
        callback = speakers.callback
        for iid in (audio._IID_UNKNOWN, audio._IID_VOLUME_CALLBACK):
            assert speakers.query(iid) == (0, callback)  # itself
        hr, pointer = speakers.query(audio._IID_ENUMERATOR)
        assert (hr, pointer) == (audio.E_NOINTERFACE, None)
        add_ref = audio._method(callback, 1, restype=c_uint32)
        assert add_ref(callback) == 1  # the counting is a pretence: the object lives on
        release = audio._method(callback, 2, restype=c_uint32)
        assert release(callback) == 1
    finally:
        source.close()


def test_core_audio_without_events_is_polled_quickly():
    world = World()
    speakers = world.add("speakers")
    speakers.refuse_events = True
    source = core_audio(world)
    try:
        assert source.events is False and source.interval == audio.POLL_SECONDS == 0.02
        assert source.sample() is not None
    finally:
        source.close()
    assert speakers.unregistered == 0 and world.leaks == {}


def test_there_may_be_no_output_device_and_nothing_is_left_behind():
    world = World()  # no device at all
    with pytest.raises(OSError, match="HRESULT 0x80070490"):
        core_audio(world)
    assert world.leaks == {} and len(world.ole32.uninits) == len(world.ole32.inits) == 1


def test_com_started_by_somebody_else_is_not_ended_by_us():
    world = World()
    world.add("speakers")
    world.ole32.init_result = RPC_E_CHANGED_MODE  # the thread joined another apartment
    source = core_audio(world)
    try:
        assert source.sample() is not None
    finally:
        source.close()
    assert world.ole32.uninits == [] and world.leaks == {}


def test_a_device_that_comes_back_is_connected_to_again():
    world = World()
    speakers = world.add("speakers", level=0.4)
    source = core_audio(world)
    try:
        assert source.check_device() is False  # nothing wrong: no reconnecting
        speakers.fail_reads = True  # unplugged: the interface is dead
        assert source.sample() is None
        speakers.fail_reads = False  # plugged in again, with the same id
        assert source.check_device() is True
        assert speakers.activated == 2 and source.sample()[0] == pytest.approx(40.0, abs=1e-3)
        assert source.check_device() is False
    finally:
        source.close()
    assert world.leaks == {}


# -- the provider ------------------------------------------------------------------------


def install(monkeypatch, source):
    """The provider's thread will make this source; the list says how often."""
    made = []

    def open_source(on_change):
        made.append(on_change)
        source.on_change = on_change
        return source

    monkeypatch.setattr(audio, "_open_source", open_source)
    return made


def test_nothing_is_read_while_no_theme_shows_the_volume(monkeypatch):
    made = install(monkeypatch, FakeSource((50.0, False)))
    provider = audio.AudioProvider()
    theme = show("cpu.load", "net.*")
    assert provider.read() == {} and provider._thread is None and made == []
    provider.close()
    del theme


def test_a_thread_reads_the_volume_while_a_theme_shows_it(monkeypatch):
    source = FakeSource((37.0, False))
    made = install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()  # this call starts the thread
        thread = provider._thread
        assert thread.daemon and thread.name == "sensors-audio" and thread.is_alive()
        assert wait_for(lambda: provider.read())
        volume, muted = provider.read()["audio.volume"], provider.read()["audio.muted"]
        assert (volume.value, volume.unit, volume.label) == (37.0, "%", "Volume")
        assert (muted.value, muted.unit, muted.label) == (0, "", "Muted")
        assert len(made) == 1 and provider._thread is thread
    finally:
        provider.close()
    assert not thread.is_alive()
    del theme


def test_the_volume_is_0_while_muted_and_back_after_unmuting(monkeypatch):
    source = FakeSource((37.0, True))
    install(monkeypatch, source)
    theme = show("audio.*")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: provider.read())
        assert provider.read()["audio.volume"].value == 0.0
        assert provider.read()["audio.muted"].value == 1
        source.states[:] = [(37.0, False)]  # unmuted: the level that was set
        assert wait_for(lambda: provider.read()["audio.muted"].value == 0)
        assert provider.read()["audio.volume"].value == 37.0
    finally:
        provider.close()
    del theme


def test_the_level_is_rounded_to_a_tenth(monkeypatch):
    install(monkeypatch, FakeSource((36.99999, False)))
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: provider.read())
        assert provider.read()["audio.volume"].value == 37.0
    finally:
        provider.close()
    del theme


def test_windows_changes_show_at_once_not_at_the_next_poll(monkeypatch):
    source = FakeSource((50.0, False), interval=3600)  # the poll is an hour away
    install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: provider.read())
        source.on_change(70.0, False)  # as the callback does, from Windows' thread
        assert provider.read()["audio.volume"].value == 70.0
        source.on_change(70.0, True)
        assert provider.read()["audio.volume"].value == 0.0
        assert provider.read()["audio.muted"].value == 1
    finally:
        provider.close()
    del theme


def test_the_default_device_is_looked_at_again_and_the_volume_follows(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    monkeypatch.setattr(audio, "DEVICE_CHECK_SECONDS", 0.005)
    source = FakeSource((50.0, False))
    source.events = True
    install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: source.device_checks >= 3)
        source.changed = True
        assert wait_for(lambda: not source.changed)
        assert wait_for(lambda: any("device changed" in r.getMessage() for r in caplog.records))
    finally:
        provider.close()
    changed = [r for r in caplog.records if "device changed" in r.getMessage()]
    assert len(changed) == 1 and "events" in changed[0].getMessage()
    del theme


def test_no_way_to_read_the_volume_gives_no_readings_and_one_warning(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    tries = []

    def nothing(on_change):
        tries.append(1)
        raise OSError("needs wpctl or pactl")

    monkeypatch.setattr(audio, "_open_source", nothing)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    provider._stop = Rounds()
    try:
        assert provider.read() == {}
        assert wait_for(lambda: len(tries) >= 4)  # it looks again now and then
        assert provider.read() == {} and provider._thread.is_alive()
    finally:
        provider.close()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "needs wpctl or pactl" in warnings[0]
    del theme


def test_a_source_that_appears_later_is_picked_up_and_said_so(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    source = FakeSource((20.0, False))
    state = {"available": False}

    def maybe(on_change):
        if not state["available"]:
            raise OSError("no output device")
        return source

    monkeypatch.setattr(audio, "_open_source", maybe)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: provider._warned)
        assert provider.read() == {}
        state["available"] = True  # a headset is plugged in
        assert wait_for(lambda: provider.read())
        assert provider.read()["audio.volume"].value == 20.0
    finally:
        provider.close()
    levels = [r.levelno for r in caplog.records]
    assert levels.count(logging.WARNING) == 1 and levels.count(logging.INFO) == 1
    del theme


def test_reads_that_fail_do_not_stop_the_thread(monkeypatch):
    source = FakeSource(RuntimeError("device invalidated"), None, (64.0, False))
    install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: provider.read())
        assert provider.read()["audio.volume"].value == 64.0
    finally:
        provider.close()
    del theme


def test_a_device_check_that_fails_does_not_stop_the_thread(monkeypatch):
    monkeypatch.setattr(audio, "DEVICE_CHECK_SECONDS", 0.005)
    source = FakeSource((50.0, False))

    def broken():
        source.device_checks += 1
        raise RuntimeError("no enumerator")

    source.check_device = broken
    install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: source.device_checks >= 3)
        assert provider.read()["audio.volume"].value == 50.0
    finally:
        provider.close()
    del theme


def test_readings_of_a_device_that_cannot_be_asked_any_more_are_dropped(monkeypatch):
    monkeypatch.setattr(audio, "STALE_SECONDS", 0.05)
    source = FakeSource((50.0, False), None)  # then the device is gone
    install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        assert wait_for(lambda: provider.read() == {} or provider._state is not None)
        assert wait_for(lambda: provider.read() == {})  # not shown frozen
    finally:
        provider.close()
    del theme


def test_closing_ends_the_thread_and_closes_the_source_in_it(monkeypatch):
    source = FakeSource((50.0, False))
    install(monkeypatch, source)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    provider.read()
    thread = provider._thread
    assert wait_for(lambda: provider.read())
    provider.close()
    assert not thread.is_alive()
    assert source.closed_in is thread  # COM belongs to the thread that started it
    assert provider.read() == {} and provider._thread is thread  # no second thread
    provider.close()
    audio.AudioProvider().close()  # one that never ran
    del theme


def test_the_thread_ends_when_nobody_looks_and_starts_again_on_demand(monkeypatch):
    first, second = FakeSource((10.0, False)), FakeSource((90.0, False))
    sources = [first, second]
    monkeypatch.setattr(audio, "_open_source", lambda on_change: sources.pop(0))
    monkeypatch.setattr(audio, "IDLE_SECONDS", 0.05)
    theme = show("audio.volume")
    provider = audio.AudioProvider()
    try:
        provider.read()
        thread = provider._thread
        assert wait_for(lambda: provider.read())
        del theme
        gc.collect()  # the theme is gone
        assert wait_for(lambda: not thread.is_alive())
        assert first.closed_in is thread and provider.read() == {}
        theme = show("audio.volume")
        provider.read()
        assert provider._thread is not thread
        assert wait_for(lambda: provider.read())
        assert provider.read()["audio.volume"].value == 90.0
    finally:
        provider.close()
    assert second.closed_in is provider._thread
    del theme


def test_core_audio_through_the_provider_from_start_to_end(monkeypatch):
    """The provider's thread makes, uses and ends the Core Audio source, and Windows'
    own change notices reach the readings while the safety poll is an hour away."""
    world = World()
    speakers = world.add("speakers", level=0.30)
    monkeypatch.setattr(audio, "EVENT_POLL_SECONDS", 3600)
    monkeypatch.setattr(audio, "_open_source", lambda on_change: core_audio(world, on_change))
    theme = show("audio.volume", "audio.muted")
    provider = audio.AudioProvider()
    try:
        provider.read()
        thread = provider._thread
        assert wait_for(lambda: provider.read())
        assert provider.read()["audio.volume"].value == 30.0
        speakers.notify(0.9, False)
        assert provider.read()["audio.volume"].value == 90.0
        speakers.notify(0.9, True)
        assert (provider.read()["audio.volume"].value, provider.read()["audio.muted"].value) == (
            0.0,
            1,
        )
    finally:
        provider.close()
    assert world.leaks == {}
    assert [t for _, t in world.ole32.inits] == world.ole32.uninits == [thread]  # one thread's COM
    del theme


def test_the_provider_is_built_in():
    provider = base.create_provider("audio", {})
    assert isinstance(provider, audio.AudioProvider) and provider.name == "audio"
    assert provider.every_frame is True
    provider.close()
    assert base.available_providers()["audio"] == "built in"


def test_importing_the_sources_stays_light():
    """Nothing slow or Windows-only at import time: no psutil, no wintypes, no PIL."""
    code = (
        "import sys; import libre_panel.sensors.load, libre_panel.sensors.audio; "
        "print(sorted({'psutil', 'ctypes.wintypes', 'PIL'} & set(sys.modules)))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


@pytest.mark.skipif(sys.platform != "win32", reason="Core Audio is Windows'")
def test_the_real_core_audio_of_windows():
    """No sound device (a CI runner) means no volume: that is allowed, a crash is not."""
    outcome = []

    def work():  # in a thread of its own, as the provider does it
        try:
            source = audio._CoreAudio(lambda level, muted: None)
        except OSError as exc:
            outcome.append(("no device", str(exc)))
            return
        try:
            outcome.append(("device", source.sample(), source.check_device()))
        finally:
            source.close()

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(10)
    assert outcome and not thread.is_alive()
    if outcome[0][0] == "device":
        state = outcome[0][1]
        assert state is None or (0.0 <= state[0] <= 100.0 and isinstance(state[1], bool))

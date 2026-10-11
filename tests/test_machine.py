"""What the services ask the computer, with the computer made up: processes, the
window in front, the microphone's registry keys, the uptime."""

import ctypes
from types import SimpleNamespace

import psutil

from libre_panel.services import machine
from libre_panel.services.machine import Machine, Program


class Process:
    def __init__(self, pid, name, exe, cwd=None):
        self.info = {"pid": pid, "name": name, "exe": exe}
        self._cwd = cwd

    def cwd(self):
        if self._cwd is None:
            raise psutil.AccessDenied(self.info["pid"])
        return self._cwd


def test_programs_are_listed_with_their_file_and_place(monkeypatch):
    processes = [
        Process(10, "Game.exe", "C:/Games/Game.exe"),
        Process(11, "systemd", "/usr/lib/systemd/systemd"),
        Process(12, "Protected", None),  # its path cannot be read: the name stays
        Process(13, "", None),  # nothing to go by
        Process(14, "Game.exe", "D:/Other/Game.exe"),  # the same name twice
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(processes))
    found = Machine("linux").programs()
    assert [(p.name, p.pid) for p in found] == [
        ("game.exe", 10),
        ("systemd", 11),
        ("protected", 12),
        ("game.exe", 14),
    ]
    assert found[0] == Program("game.exe", 10, "C:/Games/Game.exe")
    assert found[2].path == ""


def test_a_windows_program_under_wine_is_named_after_itself_and_found_by_its_folder(monkeypatch):
    processes = [
        Process(
            20, "Game.exe", "/steam/Proton/files/bin/wine64-preloader", "/lib/steamapps/common/G"
        ),
        Process(21, "services.exe", "/steam/Proton/files/bin/wine64-preloader"),  # cwd denied
        Process(22, "wineserver", "/steam/Proton/files/bin/wineserver", "/prefix"),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(processes))
    found = {p.pid: p for p in Machine("linux").programs()}
    assert found[20] == Program("game.exe", 20, "/lib/steamapps/common/G")
    assert found[21] == Program("services.exe", 21, "")
    assert found[22].name == "wineserver"


def test_the_real_list_has_this_process_in_it():
    names = [p.name for p in Machine().programs()]
    assert names and all(name == name.lower() for name in names)
    assert any("python" in name or "pytest" in name for name in names)


def test_which_window_is_in_front_is_known_on_windows_only():
    assert Machine("linux").foreground_pid() is None and not Machine("linux").has_foreground
    assert Machine("darwin").foreground_name() is None
    assert Machine("win32").has_foreground


class User32:
    """The two calls of user32 that tell which program owns the window in front."""

    def __init__(self, window, pid):
        self.window, self.pid = window, pid

    def GetForegroundWindow(self):  # noqa: N802 - the name Windows has
        return self.window

    def GetWindowThreadProcessId(self, window, pid):  # noqa: N802
        assert window == self.window
        pid._obj.value = self.pid  # what ctypes.byref(DWORD()) lets the call write to
        return 1


def test_the_window_in_front_belongs_to_a_process():
    assert machine.foreground_window_pid(User32(window=1234, pid=4321)) == 4321
    assert machine.foreground_window_pid(User32(window=0, pid=4321)) == 0  # none in front


def test_the_user32_calls_are_declared_before_they_are_used(monkeypatch):
    calls = {}

    class Library:
        def __init__(self, name):
            calls["name"] = name
            self.GetForegroundWindow = SimpleNamespace()
            self.GetWindowThreadProcessId = SimpleNamespace()

    monkeypatch.setattr(ctypes, "WinDLL", Library, raising=False)
    machine._user32.cache_clear()
    try:
        user32 = machine._user32()
    finally:
        machine._user32.cache_clear()
    assert calls["name"] == "user32"
    assert user32.GetForegroundWindow.argtypes == []
    assert len(user32.GetWindowThreadProcessId.argtypes) == 2  # 64-bit handles are not cut


def test_the_program_in_front_is_named_by_its_file(monkeypatch):
    on_windows = Machine("win32")
    monkeypatch.setattr(machine, "foreground_window_pid", lambda: 77)
    monkeypatch.setattr(
        psutil,
        "Process",
        lambda pid: SimpleNamespace(exe=lambda: "C:/Tools/Editor.EXE", name=lambda: "x"),
    )
    assert on_windows.foreground_pid() == 77
    assert on_windows.foreground_name() == "editor.exe"

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", gone)
    assert on_windows.foreground_name() is None
    monkeypatch.setattr(machine, "foreground_window_pid", lambda: 0)
    assert on_windows.foreground_name() is None


def test_the_uptime_comes_from_the_boot_time(monkeypatch):
    monkeypatch.setattr(psutil, "boot_time", lambda: machine.time.time() - 600)
    assert 599 < Machine().uptime_s() < 700
    monkeypatch.setattr(psutil, "boot_time", lambda: machine.time.time() + 5)  # a clock set back
    assert Machine().uptime_s() == 0.0

    def unknown():
        raise OSError("no boot time")

    monkeypatch.setattr(psutil, "boot_time", unknown)
    assert Machine().uptime_s() is None


# -- the microphone --------------------------------------------------------------------------------


class Handle:
    """What winreg.OpenKey returns: usable in a with statement."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Registry:
    """The microphone part of the registry: {key path: {value name: data}}. A key
    that is only the way to others (the one for all programs) has no values."""

    HKEY_CURRENT_USER = "user"

    def __init__(self, keys):
        self.keys = keys

    def _children(self, path):
        prefix = path + "\\"
        return sorted({k[len(prefix) :].split("\\")[0] for k in self.keys if k.startswith(prefix)})

    def OpenKey(self, hive, path):  # noqa: N802 - the names winreg has
        assert hive == "user"
        if path not in self.keys and not self._children(path):
            raise OSError("no such key")
        return Handle(path)

    def EnumKey(self, key, index):  # noqa: N802
        children = self._children(key.path)
        if index >= len(children):
            raise OSError("no more")
        return children[index]

    def QueryValueEx(self, key, name):  # noqa: N802
        try:
            return self.keys[key.path][name], 1
        except KeyError:
            raise OSError("no such value") from None


ROOT = machine.MICROPHONE_KEY


def recording(start=1, stop=0):
    return {"LastUsedTimeStart": start, "LastUsedTimeStop": stop}


def test_the_microphone_is_in_use_while_a_program_has_not_stopped_recording():
    keys = {
        f"{ROOT}\\Microsoft.WindowsCamera_8wekyb3d8bbwe": recording(start=5, stop=9),
        f"{ROOT}\\NonPackaged\\C:#Tools#Voice.exe": recording(start=5, stop=9),
    }
    assert machine.microphone_in_use_windows(Registry(keys)) is False
    keys[f"{ROOT}\\Microsoft.WindowsCamera_8wekyb3d8bbwe"] = recording(start=5, stop=0)
    assert machine.microphone_in_use_windows(Registry(keys)) is True


def test_a_classic_program_that_records_counts_too():
    keys = {
        f"{ROOT}\\Packaged.App_x": recording(start=5, stop=9),
        f"{ROOT}\\NonPackaged\\C:#Tools#Voice.exe": recording(start=5, stop=0),
    }
    assert machine.microphone_in_use_windows(Registry(keys)) is True


def test_a_program_that_never_recorded_does_not_count():
    keys = {f"{ROOT}\\Never.App_x": recording(start=0, stop=0), f"{ROOT}\\Odd.App_x": {}}
    assert machine.microphone_in_use_windows(Registry(keys)) is False


def test_without_the_registry_keys_nobody_records():
    assert machine.microphone_in_use_windows(Registry({})) is False
    assert Machine("linux").microphone_in_use() is False


def test_windows_asks_the_registry(monkeypatch):
    seen = []
    monkeypatch.setattr(machine, "microphone_in_use_windows", lambda: seen.append(1) or True)
    assert Machine("win32").microphone_in_use() is True and seen == [1]


def test_services_ask_for_the_computer_through_one_function():
    assert isinstance(machine.current(), Machine)

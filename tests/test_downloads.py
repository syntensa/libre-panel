"""Downloads in progress: Steam's log and manifests and the browsers' part files, in made-up
Steam and Downloads folders in a temp dir; the Downloads folder as the system names it (a fake
shell and a fake ``xdg-user-dir``); the service that publishes the state. Time is the
caller's, so a minute passes in a moment."""

import builtins
import ctypes
import io
import json
import os
import sqlite3
import subprocess

import pytest
from fake_sensors import wait_for
from fake_system import make_steam

from libre_panel.config import Config, ServicesConfig
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.plugins.host import ServiceHost
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.services import downloads
from libre_panel.services import steam as steam_module
from libre_panel.services.downloads import (
    DONE_S,
    HISTORY_S,
    MB,
    PLACES_S,
    Downloads,
    Tracker,
    downloads_folder,
    shell_downloads,
)

MANIFEST = (
    '"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"{flags}"\n'
    '\t"BytesToDownload"\t\t"{total}"\n\t"BytesDownloaded"\t\t"{loaded}"\n}}\n'
)
START = (
    "AppID {appid} update started : download {loaded}/{total}, store 0/0, reuse 0/0, "
    "delta 0/0, stage 0/{stage} "
)


@pytest.fixture
def dirs(tmp_path):
    """A Steam folder (with its log folder) and an empty Downloads folder."""
    root = make_steam(tmp_path / "Steam")
    (root / "logs").mkdir()
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return root, folder


def manifest(root, loaded, total=2_000_000_000, flags=1026, appid="777", name="Space Game"):
    text = MANIFEST.format(appid=appid, name=name, flags=flags, total=total, loaded=loaded)
    (root / "steamapps" / f"appmanifest_{appid}.acf").write_text(text, encoding="utf-8")


def log(root, *lines):
    with open(root / "logs" / "content_log.txt", "a", encoding="utf-8") as stream:
        for line in lines:
            stream.write(f"[2026-09-14 10:00:00] {line}\n")


def begin(appid="777", total=2_000_000_000):
    """The lines Steam writes when an update starts and begins to load."""
    return (
        f"AppID {appid} App update changed : Running Update,Preallocating,",
        START.format(appid=appid, loaded=0, total=total, stage=total + 100_000_000),
        f"AppID {appid} App update changed : Running Update,Downloading,Staging,",
    )


def sparse(path, size):
    """A file of this size that costs no disk."""
    with open(path, "wb") as stream:
        stream.truncate(size)


def grow(path, by):
    os.truncate(path, os.path.getsize(path) + by)


def tracker(dirs, **options):
    root, folder = dirs
    return Tracker(steam_path=str(root), folder=str(folder), **options)


class Run:
    """Time by hand: a second more at every step."""

    def __init__(self, track, rate=0.0, now=1000.0):
        self.track, self.rate, self.now = track, rate, now

    def step(self, seconds=1.0, rate=None):
        self.now += seconds
        return self.track.step(self.now, self.rate if rate is None else rate)

    def steps(self, count, **kw):
        state = None
        for _ in range(count):
            state = self.step(**kw)
        return state


# -- Steam ----------------------------------------------------------------------------------------


def test_a_steam_download_is_followed_from_its_log_and_its_manifest(dirs):
    root, _folder = dirs
    # history: a download that ended long ago does not show up when the look begins
    log(root, *begin("555", 9_000_000_000))
    log(root, "AppID 555 finished update, 1 mounted depots", "AppID 555 App update changed : None")
    manifest(root, 0)
    run = Run(tracker(dirs, browser=False))
    assert run.step() is None

    log(root, *begin())
    run.rate = 40 * MB
    state = run.steps(12)
    assert (state["source"], state["name"], state["phase"]) == (
        "steam",
        "Space Game",
        "downloading",
    )
    assert state["id"] == "steam:777" and state["total"] == 2_000_000_000 and state["more"] == 0
    assert 30 * MB < state["rate"] <= 40 * MB  # the speed is the network's
    assert state["estimated"] and state["bytes"] > 200 * MB  # no manifest figure: extrapolated
    assert state["remaining_s"] is not None

    manifest(root, 1_000_000_000)
    state = run.step()
    assert abs(state["bytes"] - 1_000_000_000) < 50 * MB and not state["estimated"]
    assert 20 < state["remaining_s"] < 30 and len(state["history"]) >= 10
    assert state["elapsed_s"] == pytest.approx(12.0)  # since the first line about it

    log(root, "AppID 777 App update changed : Running Update,")  # a transition says nothing
    log(root, "AppID 777 App update changed : Running Update,Verifying Staged,")
    state = run.step(rate=0.2 * MB)
    assert state["phase"] == "installing" and state["bytes"] == state["total"]
    assert state["rate"] == 0 and state["remaining_s"] is None

    log(root, "AppID 777 App update changed : Running Update,Committing,")
    log(root, "AppID 777 finished update, 1 mounted depots", "AppID 777 App update changed : None")
    manifest(root, 2_000_000_000, flags=4)
    state = run.step()
    assert state["phase"] == "done" and state["bytes"] == 2_000_000_000
    assert run.step(DONE_S - 2)["phase"] == "done"  # it stays a few seconds
    assert run.step(3) is None  # and goes


def test_a_steam_app_without_a_manifest_is_named_by_its_number(dirs):
    root, _folder = dirs
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    run.step()
    log(root, *begin("321"))
    assert run.step()["name"] == "App 321"
    manifest(root, 100, appid="321", name="Space™  Game®")
    assert run.step()["name"] == "Space Game"  # as the games service names them


def test_only_the_end_of_a_long_log_is_read_when_the_look_begins(dirs):
    root, _folder = dirs
    log(root, *begin("111"))  # a download that was never finished, long ago
    log(root, *("AppID 5 something else entirely" for _ in range(12_000)))
    log(root, *begin("222"))
    assert (root / "logs" / "content_log.txt").stat().st_size > 2 * downloads.TAIL_BYTES
    manifest(root, 10, appid="111", name="Old")
    manifest(root, 10, appid="222", name="New")
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    state = run.step()
    assert state["name"] == "New" and state["more"] == 0  # the other one is not even known


def test_what_the_log_calls_running_but_the_manifest_installed_is_a_left_over(dirs):
    root, _folder = dirs
    log(root, *begin("888"))
    manifest(root, 2_000_000_000, appid="888", flags=4)
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    assert run.step() is None  # found when the look begins: from before, or a Steam that ended

    log(root, *begin("999"))
    manifest(root, 1000, appid="999")
    assert run.step()["name"] == "Space Game"
    manifest(root, 1000, appid="999", flags=4)  # Steam installed it, or was closed
    assert run.step(10)["name"] == "Space Game"  # the log spoke a moment ago
    assert run.step(25) is None  # and has said nothing for 30 s


def test_a_steam_download_that_stands_still_is_not_shown(dirs):
    root, _folder = dirs
    manifest(root, 1000)
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    run.step()
    log(root, *begin())
    assert run.step() is not None
    run.rate = 0.0  # no data flows, and the log says nothing
    assert run.step(50) is not None
    assert run.step(15) is None
    run.rate = 40 * MB  # data flows again
    assert run.step() is not None


def test_a_steam_update_that_is_paused_is_shown_as_paused(dirs):
    root, _folder = dirs
    manifest(root, 1_000_000_000)
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    run.step()
    log(root, *begin())
    run.steps(3)
    log(root, "AppID 777 App update changed : Running Update,Suspended,")
    state = run.step(rate=0.0)
    assert state["phase"] == "paused" and state["rate"] == 0 and state["remaining_s"] is None


def test_a_new_log_is_read_from_its_beginning_and_a_line_when_it_is_whole(dirs):
    root, _folder = dirs
    path = root / "logs" / "content_log.txt"
    manifest(root, 1000)
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    log(root, *("AppID 5 a line that makes the file long enough " + "x" * 60 for _ in range(50)))
    run.step()
    first, second, third = begin()
    with open(path, "a", encoding="utf-8") as stream:  # a line Steam has not finished
        stream.write(first + "\n" + second + "\n[2026-09-14 10:00:00] AppID 777 App upd")
    assert run.step()["phase"] == "preparing"  # "Downloading" is not here yet
    with open(path, "a", encoding="utf-8") as stream:
        stream.write("ate changed : Running Update,Downloading,\n")
    assert run.step()["phase"] == "downloading"
    path.write_text("")  # Steam begins a new log
    log(root, *begin("999"))
    manifest(root, 1000, appid="999", name="Other Game")
    assert run.step()["more"] == 1  # the new log is read from its beginning: two downloads
    log(root, "AppID 777 App update changed : None")
    state = run.step()
    assert (state["name"], state["more"]) == ("Other Game", 0)


def test_a_log_that_cannot_be_read_is_no_failure(dirs):
    root, _folder = dirs
    (root / "logs" / "content_log.txt").mkdir()  # it has a size, but cannot be opened
    manifest(root, 1000)
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    assert run.steps(3) is None


def test_a_manifest_in_another_library_is_found(tmp_path):
    other = tmp_path / "Games"
    root = make_steam(tmp_path / "Steam", libraries=[other])
    (root / "logs").mkdir()
    (other / "steamapps").mkdir(parents=True)
    manifest(other, 1_000_000_000, name="Far Game")
    log(root, *begin())
    folder = tmp_path / "Downloads"
    run = Run(Tracker(steam_path=str(root), folder=str(folder), browser=False), rate=40 * MB)
    state = run.step()
    assert state["name"] == "Far Game" and abs(state["bytes"] - 1_000_000_000) < 50 * MB


def test_a_steam_download_of_unknown_size_counts_after_five_seconds_at_a_good_speed(dirs):
    root, _folder = dirs
    log(root, begin("321")[0], START.format(appid="321", loaded=0, total=0, stage=0))
    log(root, "AppID 321 App update changed : Running Update,Downloading,")
    run = Run(tracker(dirs, browser=False), rate=5 * MB)
    assert run.steps(5) is None  # it began 4 s ago: still small, as far as one can tell
    state = run.step()  # 5 s
    assert state["total"] is None and state["remaining_s"] is None
    assert state["name"] == "App 321" and state["bytes"] > 0


# -- the browser ----------------------------------------------------------------------------------


def test_a_browser_download_is_followed_by_the_growth_of_its_part_file(dirs):
    _root, folder = dirs
    sparse(folder / "small.pdf.part", 100_000)
    unconfirmed = folder / "Unconfirmed 4711.crdownload"
    sparse(unconfirmed, 5 * MB)
    run = Run(tracker(dirs, steam_on=False))
    run.step()
    state = None
    for _ in range(4):
        grow(unconfirmed, 20 * MB)
        state = run.step()
    assert state["source"] == "browser" and state["total"] is None
    assert state["remaining_s"] is None and state["phase"] == "downloading"
    assert state["name"] == "Unconfirmed 4711"
    assert 10 * MB < state["rate"] <= 21 * MB  # the speed is the file's growth
    assert state["bytes"] == 85 * MB
    before = state

    named = folder / "ubuntu.iso.crdownload"  # Chrome gives it its name while it loads
    os.rename(unconfirmed, named)
    grow(named, 20 * MB)
    state = run.step()
    assert state["name"] == "ubuntu.iso" and state["id"] == before["id"]  # the same download
    assert state["id"] == "browser:1" and str(folder) not in json.dumps(state)  # no path in it
    assert state["elapsed_s"] == before["elapsed_s"] + 1

    os.rename(named, folder / "ubuntu.iso")  # done
    state = run.step()
    assert (state["phase"], state["name"], state["rate"]) == ("done", "ubuntu.iso", 0)
    assert state["bytes"] == state["total"] == 105 * MB
    assert run.step(DONE_S + 1) is None  # the small one stays invisible


def test_a_download_that_is_gone_without_its_file_is_not_called_done(dirs):
    _root, folder = dirs
    part = folder / "cancelled.zip.part"
    sparse(part, 60 * MB)
    run = Run(tracker(dirs, steam_on=False))
    assert run.step()["name"] == "cancelled.zip"
    part.unlink()  # cancelled: no file where it should be
    assert run.step() is None


def test_a_part_file_that_does_not_grow_is_paused(dirs):
    _root, folder = dirs
    part = folder / "big.zip.part"
    sparse(part, 60 * MB)
    run = Run(tracker(dirs, steam_on=False))
    run.step()
    grow(part, 10 * MB)
    assert run.step()["phase"] == "downloading"
    state = run.step(19)
    assert state["phase"] == "downloading"
    state = run.step(2)  # 21 s since it last grew
    assert (state["phase"], state["rate"]) == ("paused", 0)
    grow(part, 10 * MB)
    assert run.step()["phase"] == "downloading"


def test_every_part_suffix_a_browser_uses_counts_in_any_case(dirs):
    _root, folder = dirs
    for name in ("a.crdownload", "b.PART", "c.partial", "d.download", "e.zip", "f.part.bak"):
        sparse(folder / name, 60 * MB)
    (folder / "g.part").mkdir()  # a folder is no download
    run = Run(tracker(dirs, steam_on=False))
    state = run.step()
    assert state["more"] == 2  # three of the six, and one of them is the state itself
    assert {state["name"]} <= {"a", "b", "c"}


def test_a_download_of_unknown_size_counts_after_five_seconds_at_a_good_speed_too(dirs):
    _root, folder = dirs
    part = folder / "slow.bin.part"
    sparse(part, 1 * MB)
    run = Run(tracker(dirs, steam_on=False))
    run.step()
    states = []
    for _ in range(7):
        grow(part, 2 * MB)
        states.append(run.step())
    assert states[:4] == [None] * 4  # under 5 s
    assert states[4]["name"] == "slow.bin" and states[4]["rate"] > MB


# -- what is shown --------------------------------------------------------------------------------


def test_small_downloads_are_not_shown_and_min_mb_says_how_small(dirs):
    _root, folder = dirs
    sparse(folder / "photo.jpg.part", 20 * MB)
    assert Run(tracker(dirs, steam_on=False)).step() is None  # the default is 50 MB
    assert Run(tracker(dirs, steam_on=False, min_mb=10)).step()["name"] == "photo.jpg"
    assert Run(tracker(dirs, steam_on=False, min_mb=0)).step()["name"] == "photo.jpg"
    root, _ = dirs
    manifest(root, 1000, total=30 * MB)
    log(root, *begin(total=30 * MB))
    assert Run(tracker(dirs, browser=False), rate=40 * MB).step() is None
    assert Run(tracker(dirs, browser=False, min_mb=20), rate=40 * MB).step() is not None


def test_steam_and_the_browser_can_each_be_left_out(dirs):
    root, folder = dirs
    manifest(root, 1000)
    log(root, *begin())
    sparse(folder / "big.zip.part", 60 * MB)
    both = Run(tracker(dirs), rate=40 * MB).step()
    assert both["more"] == 1
    assert Run(tracker(dirs, browser=False), rate=40 * MB).step()["source"] == "steam"
    only_browser = Run(tracker(dirs, steam_on=False), rate=40 * MB).step()
    assert (only_browser["source"], only_browser["more"]) == ("browser", 0)
    assert Run(tracker(dirs, steam_on=False, browser=False), rate=40 * MB).step() is None


def test_the_download_that_matters_most_is_the_state_and_the_others_are_counted(dirs):
    root, folder = dirs
    manifest(root, 1_000_000_000)
    log(root, *begin())
    part = folder / "big.zip.part"
    sparse(part, 5 * MB)
    run = Run(tracker(dirs), rate=40 * MB)
    run.step()
    state = None
    for _ in range(8):
        grow(part, 10 * MB)
        state = run.step()
    assert (state["source"], state["more"]) == ("steam", 1)  # the faster one
    log(root, "AppID 777 App update changed : None")  # Steam is done with it
    grow(part, 10 * MB)
    state = run.step()
    assert (state["source"], state["name"], state["more"]) == ("browser", "big.zip", 0)


def test_the_history_is_the_speed_of_the_last_three_minutes(dirs):
    root, _folder = dirs
    manifest(root, 1000)
    log(root, *begin())
    run = Run(tracker(dirs, browser=False), rate=40 * MB)
    run.step()
    states = [run.step() for _ in range(HISTORY_S + 20)]
    history = states[-1]["history"]
    assert len(history) == HISTORY_S == 180
    assert history[-1] == states[-1]["rate"] and all(0 <= v <= 40 * MB for v in history)
    assert history == sorted(history)  # it began slowly and settled
    log(root, "AppID 777 App update changed : None")
    assert run.step() is None
    log(root, *begin())
    assert len(run.step()["history"]) == 1  # a new download starts a new course


def test_the_state_is_plain_data(dirs):
    root, _folder = dirs
    manifest(root, 1000)
    log(root, *begin())
    state = Run(tracker(dirs, browser=False), rate=40 * MB).step()
    assert json.loads(json.dumps(state)) == state
    assert set(state) == {
        "id", "source", "name", "phase", "bytes", "total", "rate", "remaining_s", "elapsed_s",
        "estimated", "history", "more",
    }  # fmt: skip


def test_a_missing_steam_or_folder_is_no_error(dirs, tmp_path, monkeypatch):
    monkeypatch.setattr(steam_module, "find_root", lambda given="": None)
    said = []
    track = Tracker(folder=str(tmp_path / "nothing here"), report=said.append)
    assert Run(track).steps(3) is None and said == []  # nothing given, nothing to complain of
    given = Tracker(steam_path=str(tmp_path / "typo"), browser=False, report=said.append)
    assert Run(given).steps(3) is None
    assert len(said) == 1 and "typo" in said[0]  # said once


def test_the_places_are_looked_up_again_after_a_while(dirs, tmp_path):
    _root, folder = dirs
    asked = []
    other = tmp_path / "Other"
    other.mkdir()

    def find():
        asked.append(1)
        return folder if len(asked) == 1 else other

    run = Run(Tracker(steam_on=False, find_folder=find))
    sparse(folder / "a.zip.part", 60 * MB)
    sparse(other / "b.zip.part", 60 * MB)
    assert run.step()["name"] == "a.zip"
    run.step(PLACES_S - 10)
    assert len(asked) == 1
    state = run.step(20)  # the folder was moved in the meantime
    assert len(asked) == 2 and state["name"] == "b.zip"


# -- the Downloads folder -------------------------------------------------------------------------


class XdgUserDir:
    """``subprocess.run`` as ``xdg-user-dir`` would answer it."""

    def __init__(self, out="", code=0, error=None):
        self.out, self.code, self.error, self.calls = out, code, error, []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs.get("timeout")))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.code, self.out, "")


def test_on_linux_the_folder_is_what_xdg_user_dir_says(tmp_path):
    home = tmp_path / "home"
    run = XdgUserDir(f"{tmp_path / 'elsewhere' / 'Loads'}\n")
    assert downloads_folder("linux", home, run) == tmp_path / "elsewhere" / "Loads"
    assert run.calls == [(["xdg-user-dir", "DOWNLOAD"], 3)]  # it cannot hold the service for long


@pytest.mark.parametrize(
    "run",
    [
        XdgUserDir("HOME\n"),  # filled below: the answer is the home folder
        XdgUserDir(""),
        XdgUserDir("relative/Loads\n"),
        XdgUserDir("/somewhere\n", code=1),
        XdgUserDir(error=FileNotFoundError("xdg-user-dir")),
        XdgUserDir(error=subprocess.TimeoutExpired("xdg-user-dir", 3)),
        XdgUserDir(error=PermissionError("no")),
    ],
    ids=["home", "empty", "relative", "failed", "not-installed", "timeout", "denied"],
)
def test_without_an_answer_from_the_system_it_is_downloads_in_the_home_folder(run, tmp_path):
    home = tmp_path / "home"
    if run.out == "HOME\n":  # xdg-user-dir without a setting answers with the home folder
        run.out = f"{home}\n"
    assert downloads_folder("linux", home, run) == home / "Downloads"


def test_on_windows_the_folder_is_what_the_shell_says(tmp_path):
    home = tmp_path / "home"
    moved = tmp_path / "D" / "Loads"
    asked = []

    def shell():
        asked.append(1)
        return str(moved)

    run = XdgUserDir(error=AssertionError("xdg-user-dir on Windows"))
    assert downloads_folder("win32", home, run, shell) == moved and asked == [1]
    assert downloads_folder("win32", home, run, lambda: None) == home / "Downloads"

    def broken():
        raise OSError("no shell")

    assert downloads_folder("win32", home, run, broken) == home / "Downloads"
    assert run.calls == []


def test_on_another_system_it_is_downloads_in_the_home_folder(tmp_path):
    run = XdgUserDir(error=AssertionError("asked"))
    assert downloads_folder("freebsd14", tmp_path, run) == tmp_path / "Downloads"


class FakeShell:
    """shell32's SHGetKnownFolderPath over a made-up folder: it fills the pointer it is given."""

    def __init__(self, path, result=0, null=False):
        self.path, self.result, self.null = path, result, null
        self.asked, self.kept = [], []

    def SHGetKnownFolderPath(self, guid, flags, token, out):  # noqa: N802 - Windows' name
        folder = guid._obj
        self.asked.append(((folder.a, folder.b, folder.c, tuple(folder.d)), flags, token))
        if self.result == 0 and not self.null:
            text = ctypes.create_unicode_buffer(self.path)
            self.kept.append(text)  # Windows would own it until CoTaskMemFree
            out._obj.value = ctypes.addressof(text)
        return self.result


class FakeOle:
    def __init__(self):
        self.freed = []

    def CoTaskMemFree(self, pointer):  # noqa: N802
        self.freed.append(pointer.value)


def test_the_shell_is_asked_for_the_downloads_folder_and_the_answer_is_freed(tmp_path):
    shell, ole = FakeShell(str(tmp_path / "Loads")), FakeOle()
    assert shell_downloads(shell, ole) == str(tmp_path / "Loads")
    ((guid, flags, token),) = shell.asked
    assert guid == (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
    assert (flags, token) == (0, None)
    assert len(ole.freed) == 1 and ole.freed[0] is not None  # the memory Windows gave is freed


def test_the_shell_may_not_know(tmp_path):
    ole = FakeOle()
    assert shell_downloads(FakeShell("x", result=-2147024894), ole) is None  # an error code
    assert shell_downloads(FakeShell("x", null=True), ole) is None  # success, but no text
    assert ole.freed == []


def test_without_windows_there_is_no_shell_to_ask(monkeypatch):
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)  # as on Linux
    assert shell_downloads() is None


# -- what is read ---------------------------------------------------------------------------------


def test_the_browsers_history_and_the_downloaded_files_are_never_opened(
    dirs, tmp_path, monkeypatch
):
    root, folder = dirs
    profile = tmp_path / "browser profile"
    profile.mkdir()
    for name in ("History", "places.sqlite", "Cookies", "Login Data"):
        (profile / name).write_bytes(b"SQLite format 3\0")
        (folder / name).write_bytes(b"SQLite format 3\0")  # even if one lay in the folder
    (folder / "notes.txt").write_text("private")
    sparse(folder / "big.iso.part", 60 * MB)
    manifest(root, 1_000_000_000)
    log(root, *begin())
    opened = []
    real = builtins.open

    def spy(file, *args, **kwargs):
        if isinstance(file, str | os.PathLike):  # not a file number of something else's
            opened.append(os.fspath(file))
        return real(file, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("a database was opened")

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    run = Run(tracker(dirs), rate=40 * MB)
    for _ in range(3):
        assert run.step() is not None
    os.rename(folder / "big.iso.part", folder / "big.iso")
    log(root, "AppID 777 App update changed : None")
    assert run.step()["phase"] == "done"
    assert opened  # it did look at Steam's files
    here = {os.path.normpath(p) for p in opened}
    allowed = {
        os.path.normpath(root / "logs" / "content_log.txt"),
        os.path.normpath(root / "steamapps" / "libraryfolders.vdf"),
        os.path.normpath(root / "steamapps" / "appmanifest_777.acf"),
    }
    assert here <= allowed, here - allowed  # nothing of the Downloads folder or a profile


# -- the service ----------------------------------------------------------------------------------


def state_of(host):
    return Snapshot(readings=host.published()).state(downloads.KEY)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def bare_service(dirs, **options):
    """The service on a host, looking when it is told to (no thread)."""
    root, folder = dirs
    host = PluginHost()
    options = {
        "steam": True, "steam_path": str(root), "browser": True, "folder": str(folder),
        "min_mb": 50.0, **options,
    }  # fmt: skip
    clock = Clock()
    service = Downloads(ServiceHost(host, "downloads"), options, clock=clock)
    return host, service, clock


def test_the_service_publishes_the_state_with_the_network_speed_of_the_snapshot(dirs):
    root, folder = dirs
    host, service, clock = bare_service(dirs)
    manifest(root, 1_000_000_000)
    log(root, *begin())
    host.latest = Snapshot(readings={"net.down": Reading("net.down", 40.0 * MB, "B/s")})
    service.step()
    assert state_of(host) is not None
    for _ in range(10):
        clock.now += 1
        service.step()
    state = state_of(host)
    assert state["name"] == "Space Game" and 30 * MB < state["rate"] <= 40 * MB
    assert service.status()["download"] == "Space Game"
    assert service.status()["steam"] == str(root) and service.status()["folder"] == str(folder)
    log(root, "AppID 777 App update changed : None")
    clock.now += 1
    service.step()
    assert state_of(host) is None  # nothing downloads: null
    host.close()


def test_without_a_snapshot_or_a_network_reading_the_speed_is_zero(dirs):
    _root, folder = dirs
    host, service, clock = bare_service(dirs, steam=False)
    sparse(folder / "big.zip.part", 60 * MB)
    service.step()  # the panel has not drawn a frame yet
    assert state_of(host)["rate"] == 0
    host.latest = Snapshot(readings={"net.down": Reading("net.down", "fast")})  # not a number
    clock.now += 1
    service.step()
    assert state_of(host)["rate"] == 0
    host.close()


def test_a_look_that_fails_is_said_once_and_tried_again(dirs, caplog):
    _root, folder = dirs
    host, service, clock = bare_service(dirs, steam=False)
    sparse(folder / "big.zip.part", 60 * MB)
    service.step()
    assert state_of(host)["name"] == "big.zip"
    real = service.tracker.step

    def broken(now, rate):
        raise RuntimeError("the disk went away")

    service.tracker.step = broken
    with caplog.at_level("INFO"):
        for _ in range(3):
            clock.now += 1
            service.step()
        assert state_of(host) is None  # nothing wrong is shown
        assert caplog.text.count("failed") == 1 and "the disk went away" in caplog.text
        assert service.status()["error"] == "RuntimeError: the disk went away"
        service.tracker.step = real
        clock.now += 1
        service.step()
    assert state_of(host)["name"] == "big.zip"
    assert "works again" in caplog.text and service.status()["error"] is None
    host.close()


def test_a_look_that_ends_after_the_service_stopped_publishes_nothing(dirs):
    _root, folder = dirs
    host, service, clock = bare_service(dirs, steam=False)
    sparse(folder / "big.zip.part", 60 * MB)
    service.stop()  # nothing was started: it only says it is over
    service.step()
    assert state_of(host) is None
    host.close()


def test_the_service_runs_through_the_manager_and_publishes_while_it_is_there(dirs, monkeypatch):
    root, folder = dirs
    monkeypatch.setattr(downloads, "STEP_S", 0.02)  # a look every 20 ms instead of every second
    host = PluginHost()
    host.latest = Snapshot(readings={"net.down": Reading("net.down", 3.0 * MB, "B/s")})
    manager = ServiceManager(host)
    options = {"steam_path": str(root), "folder": str(folder), "min_mb": 50}
    manager.apply(Config(services=ServicesConfig(["downloads"], {"downloads": options})))
    try:
        assert manager.state == {"downloads": "running"}
        assert state_of(host) is None
        part = folder / "big.iso.part"
        sparse(part, 60 * MB)
        assert wait_for(lambda: state_of(host) is not None)
        found = state_of(host)
        assert (found["source"], found["name"], found["phase"]) == (
            "browser",
            "big.iso",
            "downloading",
        )
        assert found["total"] is None and found["bytes"] == 60 * MB
        part.unlink()
        assert wait_for(lambda: state_of(host) is None)
    finally:
        manager.stop()
        host.close()
    assert manager.state == {"downloads": "stopped"}


def test_the_options_have_defaults_that_look_nowhere_special():
    assert Downloads.options == {
        "steam": ("bool", True),
        "steam_path": ("string", ""),
        "browser": ("bool", True),
        "folder": ("string", ""),
        "min_mb": ("number", 50.0),
    }

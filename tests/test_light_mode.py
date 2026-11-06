"""Light mode (libre_panel.light, always on since 0.8.1): what only the app needs goes once it
has been closed for a minute (what the panel draws with stays), and the memory goes back; once
per time the app was open."""

import sys
import threading

import pytest
from test_editor_server import request

from libre_panel import light
from libre_panel.config import parse_config
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import find_theme, load_theme


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watch(last=None):
    state = {"last": last, "released": 0, "closed": 0, "marked": 0, "shed": 0, "unmarked": 0,
             "tidied": 0, "said": 0}  # fmt: skip

    def count(name):
        return lambda: state.__setitem__(name, state[name] + 1)

    clock = Clock()
    w = light.Watch(
        last_request=lambda: state["last"],
        release=count("released"),
        clock=clock,
        closed=count("closed"),
        mark=count("marked"),
        shed=count("shed"),
        unmark=count("unmarked"),
        tidy=count("tidied"),
        say=count("said"),
    )
    return w, state, clock


def test_the_apps_own_pictures_go_a_minute_after_it_was_closed():
    """Its pictures of the themes are the app's alone: they go once it has been closed for a
    minute, once per time it was open; a look later the caches let go of what the panel did
    not use, and the memory goes back."""
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S - 1
    w.look()
    assert state["closed"] == state["marked"] == 0  # (still open, or just closed)
    clock.now = 1000 + light.IDLE_S
    assert not w.look() and state["closed"] == state["marked"] == 1 and state["shed"] == 0
    clock.now += light.LOOK_S  # the caches noted what the panel draws with meanwhile
    assert w.look() and state["shed"] == 1 and state["released"] == 1
    clock.now += 600
    w.look()
    assert state["closed"] == state["shed"] == 1 and state["released"] == 1
    state["last"] = clock.now  # opened again, and closed
    clock.now += light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    w.look()
    assert state["closed"] == state["shed"] == 2 and state["released"] == 2


def test_the_memory_goes_a_minute_after_the_app_was_closed_and_once():
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S - 1
    assert not w.look() and state["released"] == 0  # (still open, or just closed)
    clock.now = 1000 + light.IDLE_S
    assert not w.look() and state["released"] == 0  # (the caches note what the panel uses)
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 1
    clock.now += 600
    assert not w.look() and state["released"] == 1  # once for the time it was open
    state["last"] = clock.now  # the app is opened again ...
    clock.now += light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 2  # ... and closed


def test_the_heap_is_tidied_at_each_look_while_the_app_stays_closed():
    """After the memory went back (the release tidies itself), each look while the app stays
    closed gives back what the panel's frames freed since; not while it is open, and again
    once it is closed again and its memory went back again."""
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 1 and state["tidied"] == 0
    for n in (1, 2, 3):
        clock.now += light.LOOK_S
        assert not w.look() and state["tidied"] == n
    state["last"] = clock.now  # opened again: nothing while it is open
    clock.now += light.LOOK_S
    w.look()
    assert state["tidied"] == 3
    clock.now = state["last"] + light.IDLE_S  # closed again: first its pictures go ...
    assert not w.look() and state["tidied"] == 3
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 2 and state["tidied"] == 3  # ... then the memory
    clock.now += light.LOOK_S
    assert not w.look() and state["tidied"] == 4


def test_the_log_says_what_the_process_holds_some_minutes_after_the_app_closed(caplog):
    """Once per time the app was closed, SAID_S after its memory went back (what stays with
    the app closed, to read in the log once the window is open again); not with it open; the
    line itself names the memory and what the load report counts."""
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    assert w.look() and state["said"] == 0  # (released now)
    while clock.now < 1000 + light.IDLE_S + light.LOOK_S + light.SAID_S - light.LOOK_S:
        clock.now += light.LOOK_S
        w.look()
    assert state["said"] == 0
    for _ in range(8):
        clock.now += light.LOOK_S
        w.look()
    assert state["said"] == 1  # (once)
    state["last"] = clock.now  # opened and closed again: said again, once
    for _ in range(round((light.IDLE_S + light.SAID_S) / light.LOOK_S) + 4):
        clock.now += light.LOOK_S
        w.look()
    assert state["said"] == 2
    with caplog.at_level("INFO", logger="libre_panel.light"):
        light.say_held()
    line = caplog.records[-1].getMessage()
    assert line.startswith("light mode: 3 minutes after the app closed, ")
    assert " MB" in line and "the main process holds: pictures" in line
    assert "all of Libre Panel" not in line  # (no way to its other processes given)
    memory = {"total": 90 * 2**20, "processes": [
        {"label": "main process", "count": 1, "private": 55 * 2**20},
        {"label": "engine", "count": 2, "private": 35 * 2**20}]}  # fmt: skip
    with caplog.at_level("INFO", logger="libre_panel.light"):
        light.say_held(lambda: memory)
    line = caplog.records[-1].getMessage()
    assert "; all of Libre Panel 90 MB: main process 55, engine ×2 35 (MB each); " in line

    def refused():
        raise OSError("refused")

    with caplog.at_level("INFO", logger="libre_panel.light"):
        light.say_held(refused)  # (the line goes on without them)
    assert caplog.records[-1].getMessage().startswith("light mode: 3 minutes after")


class Kernel32:
    """Windows' kernel32 as light.py asks it, its calls noted."""

    def __init__(self, calls):
        self.calls = calls
        outer = self

        class Call:
            def __init__(self, name):
                self.name, self.argtypes, self.restype = name, None, None

            def __call__(self, *args):
                outer.calls.append((self.name, args))
                return {"GetProcessHeap": 77, "GetCurrentProcess": -1}.get(self.name, 1)

        for name in ("HeapSetInformation", "GetProcessHeap", "HeapCompact",
                     "SetProcessWorkingSetSize", "GetCurrentProcess"):  # fmt: skip
            setattr(self, name, Call(name))


def test_on_windows_the_heaps_give_their_free_memory_back_at_each_look(monkeypatch):
    """Windows: the tidy has all of the process's heaps let go of what their caches hold
    (HeapOptimizeResources, version 1) and the process heap decommit its large free blocks
    (HeapCompact); the release does that too, then trims the working set."""
    calls = []
    kernel32 = Kernel32(calls)
    monkeypatch.setattr(light.ctypes, "WinDLL", lambda name: kernel32, raising=False)
    monkeypatch.setattr(light.ctypes, "windll", type("W", (), {"kernel32": kernel32}),
                        raising=False)  # fmt: skip
    light.tidy("win32")
    names = [name for name, _ in calls]
    assert names == ["HeapSetInformation", "GetProcessHeap", "HeapCompact"]
    heap, kind, info, size = calls[0][1]
    assert heap is None and kind == light.HEAP_OPTIMIZE_RESOURCES == 3 and size == 8
    optimize = info._obj  # (what ctypes.byref points at)
    assert (optimize.version, optimize.flags) == (1, 0)
    assert calls[2][1] == (77, 0)  # (the process heap)
    calls.clear()
    light.give_back("win32")
    names = [name for name, _ in calls]
    assert names[:3] == ["HeapSetInformation", "GetProcessHeap", "HeapCompact"]
    assert names[3:] == ["GetCurrentProcess", "SetProcessWorkingSetSize"]
    every = 2**64 - 1  # (SIZE_T -1 both: as many pages as can go)
    assert calls[4][1] == (-1, every, every)
    trim = kernel32.SetProcessWorkingSetSize
    assert len(trim.argtypes) == 3 and trim.argtypes[1] is light.ctypes.c_size_t

    def refused(*args):  # (an argument ctypes cannot pass: the memory stays, no error)
        raise light.ctypes.ArgumentError("argument 1: OverflowError: int too long to convert")

    kernel32.SetProcessWorkingSetSize = refused
    light.give_back("win32")

    def missing(name):  # (an older Windows, or no kernel32 to be had: nothing happens)
        raise OSError("not found")

    monkeypatch.setattr(light.ctypes, "WinDLL", missing, raising=False)
    light.tidy("win32")  # (no error)
    light.give_back("win32")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' working set")
def test_on_windows_the_release_empties_the_working_set_where_other_code_declared_kernel32():
    """The release takes the program's pages out of its working set (Task Manager's Memory
    column), also where other code declared the shared kernel32's GetCurrentProcess to give a
    HANDLE (the sensor service's own_memory does), whose value an undeclared argument cannot
    take: then it did nothing."""
    from ctypes import wintypes

    import psutil

    held = bytearray(64 << 20)  # touched: 64 MB in the working set, and kept
    shared = light.ctypes.windll.kernel32.GetCurrentProcess  # type: ignore[attr-defined]
    shared.restype = wintypes.HANDLE
    try:
        before = psutil.Process().memory_full_info().uss
        light.give_back("win32")
        after = psutil.Process().memory_full_info().uss
    finally:
        shared.restype = light.ctypes.c_int  # (ctypes' own)
    assert before - after >= 48 << 20, (before >> 20, after >> 20)
    assert held[-1] == 0  # (and the memory is the program's still)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own heaps")
def test_on_windows_the_free_memory_pictures_left_in_the_heap_goes_back():
    """Pictures made and freed between those that stay, as a frame's work and the app's window
    make them, leave the process heap's pages committed to the program; the tidy gives them
    back (most of them: what stays between the pictures kept is the heap's)."""
    from PIL import Image

    from libre_panel.load_report import c_heap

    made = [
        Image.new("RGBA", (60 + (k * 37) % 240, 40 + (k * 53) % 160), (k % 256, 9, 9, 255))
        for k in range(1500)
    ]
    kept = made[::5]  # (a fifth stays, among the others)
    del made
    held = c_heap("win32")
    light.tidy("win32")
    after = c_heap("win32")
    assert held is not None and after is not None and len(kept) == 300
    # the free memory the heap keeps committed: at most half of it stays, or little was left
    assert after["free"] <= max(held["free"] / 2, 16 << 20), (held, after)


def test_the_tidy_is_glibc_s_trim_on_linux_only(monkeypatch):
    calls = []

    class Glibc:
        gnu_get_libc_version = object()

        def malloc_trim(self, pad):
            calls.append(pad)
            return 1

    class Musl:  # (no glibc: no trim to ask for)
        pass

    monkeypatch.setattr(light.ctypes, "CDLL", lambda name: Glibc())
    light.tidy("win32")
    assert calls == []
    light.tidy("linux")
    assert calls == [0]
    monkeypatch.setattr(light.ctypes, "CDLL", lambda name: Musl())
    light.tidy("linux")
    assert calls == [0]

    def windows_cdll(name):  # (as Windows' ctypes takes a library named None)
        raise TypeError("argument of type 'NoneType' is not iterable")

    monkeypatch.setattr(light.ctypes, "CDLL", windows_cdll)
    light.tidy("linux")  # (no error)
    assert calls == [0]


def test_the_caches_keep_all_when_the_app_is_opened_again_before_they_let_go():
    w, state, clock = watch(last=1000.0)
    clock.now = 1000 + light.IDLE_S
    w.look()
    assert state["marked"] == 1
    state["last"] = clock.now + 5  # opened again
    clock.now += light.LOOK_S
    assert not w.look() and state["unmarked"] == 1 and state["shed"] == state["released"] == 0
    w.look()
    assert state["unmarked"] == 1


def test_an_app_never_opened_lets_go_a_minute_after_the_start_and_once():
    """Libre Panel started at login, its window never opened: what only the start touched
    goes as it goes for an app closed (the caches keep what the panel draws with), once."""
    w, state, clock = watch(last=None)
    clock.now = w.began + light.IDLE_S - 1
    assert not w.look() and state["closed"] == 0
    clock.now = w.began + light.IDLE_S
    assert not w.look() and state["closed"] == 1 and state["marked"] == 1
    clock.now += light.LOOK_S
    assert w.look() and state["shed"] == 1 and state["released"] == 1
    for _ in range(5):
        clock.now += 600
        assert not w.look()
    assert state["released"] == 1 and state["closed"] == 1
    state["last"] = clock.now  # (opened now: once closed again, it goes again)
    clock.now += light.IDLE_S
    w.look()
    clock.now += light.LOOK_S
    assert w.look() and state["released"] == 2


def test_the_caches_let_go_of_what_only_the_apps_pictures_drew_with_and_keep_the_panels():
    from libre_panel.render import kept, symbols

    snapshot = demo_snapshot(fixed_time=1_700_000_000)
    panel = Renderer(load_theme(find_theme("lumen")), preview=True)
    panel.render(snapshot)
    for other in ("arena", "almanac", "encore"):  # the app's pictures of other themes
        picture = Renderer(load_theme(find_theme(other)), preview=True)
        picture.render(snapshot)
        picture.close()
    held = kept.stats()
    light.mark()
    panel.render(snapshot)  # the panel draws on: what it draws with is noted
    light.shed()
    left = kept.stats()
    total = sum(row["bytes"] for row in held.values())
    assert sum(row["bytes"] for row in left.values()) < total / 2
    hits = kept.stats()
    panel.render(snapshot)  # ... and finds it all kept
    after = kept.stats()
    misses = sum(after[n]["misses"] - hits[n]["misses"] for n in after if n in hits)
    assert misses == 0
    panel.close()
    told = []
    light.release(lambda: told.append(True))  # the memory: also the caches that cannot tell
    assert told == [True] and len(symbols._sets) == 0
    light.give_back("win32")  # (no Windows here: it says so in the log, no error)


def test_a_cache_notes_what_is_used_and_lets_go_of_the_rest():
    from libre_panel.render.kept import Kept

    kept = Kept("test.shed", 1 << 20)
    for key in "abcd":
        kept.put(key, bytes(100))
    assert kept.shed() == 0 and len(kept) == 4  # (no mark: all stay)
    kept.mark()
    kept.get("a")
    kept.make("e", lambda: bytes(10))
    _ = kept["b"]
    assert kept.shed() > 0 and sorted(kept) == ["a", "b", "e"]
    kept.mark()
    kept.unmark()
    assert kept.shed() == 0 and len(kept) == 3


def test_an_old_config_s_switch_of_light_mode_is_let_be_and_goes_with_the_next_write(tmp_path):
    """0.8 to 0.8.1rc1 had a switch (``light_mode = false``): such a config.toml loads as any
    other, the switch said nothing, and the next write of the file takes it out (the comments
    stay); a layout that cannot lose it still takes the write asked for."""
    from libre_panel.config import edit_config

    assert not hasattr(parse_config({"light_mode": False}), "light_mode")
    path = tmp_path / "config.toml"
    path.write_text('# mine\ntheme = "lumen"\nlight_mode = false\n\n[device]\nbrightness = 50\n',
                    encoding="utf-8")  # fmt: skip
    edit_config(("value", "device.brightness", 70), path=path)
    assert path.read_text(encoding="utf-8") == (
        '# mine\ntheme = "lumen"\n\n[device]\nbrightness = 70\n'
    )
    path.write_text('theme = "lumen"\nlight_mode = false\n', encoding="utf-8")
    edit_config(("value", "theme", "arena"), path=path)
    assert path.read_text(encoding="utf-8") == 'theme = "arena"\n'
    path.write_text('theme = "lumen"\n', encoding="utf-8")  # (nothing to take out)
    edit_config(("value", "theme", "arena"), path=path)
    assert path.read_text(encoding="utf-8") == 'theme = "arena"\n'


def test_only_a_page_of_this_run_counts_as_the_app_being_open(isolated_home):
    from libre_panel.editor.server import make_server

    httpd = make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    state = httpd.editor_state
    try:
        assert state.last_request is None
        request(port, "GET", "/api/app", headers={"X-Libre-Panel": "w" * 43})  # an old tab
        assert state.last_request is None
        request(port, "GET", "/api/settings")
        assert state.last_request is not None
    finally:
        httpd.shutdown()
        httpd.server_close()
        state.close()


def test_the_app_lets_go_of_its_pictures_of_the_themes_and_of_the_communitys(isolated_home):
    from libre_panel.service import EditorService

    service = EditorService(port=0)
    service.start()
    try:
        state = service._server.editor_state
        state.previews[("lumen", 1, 480)] = b"\x89PNG" + bytes(1000)
        state.community.previews[("aurora", "preview.png")] = (0.0, b"\x89PNG" + bytes(1000))
        service.forget_pictures()
        assert state.previews == {} and state.community.previews == {}
    finally:
        service.stop()


def test_large_blocks_are_the_system_s_own_on_linux_in_the_app_and_its_engine(monkeypatch):
    """A block of a megabyte and more is mapped (glibc's M_MMAP_THRESHOLD, fixed), in the app as
    it starts and in the engine it starts (GLIBC_TUNABLES); what the environment says holds."""
    calls = []

    class Libc:
        gnu_get_libc_version = object()

        def mallopt(self, param, value):
            calls.append((param, value))
            return 1

    monkeypatch.setattr(light.ctypes, "CDLL", lambda name: Libc())
    for key in ("MALLOC_ARENA_MAX", "MALLOC_MMAP_THRESHOLD_", "GLIBC_TUNABLES"):
        monkeypatch.delenv(key, raising=False)
    assert light.fewer_heaps("linux") is True
    assert calls == [(-3, light.MAPPED_FROM), (-8, 2)]
    want = {"GLIBC_TUNABLES": f"glibc.malloc.mmap_threshold={light.MAPPED_FROM}"}
    assert light.mapped_blocks("linux") == want and light.mapped_blocks("win32") == {}
    calls.clear()
    assert light.fewer_heaps("win32") is False and not calls
    monkeypatch.setenv("GLIBC_TUNABLES", "glibc.malloc.arena_max=1")
    assert light.mapped_blocks("linux") == {}
    light.fewer_heaps("linux")
    assert calls == [(-8, 2)]  # (its own choice of the blocks holds)

    def windows_cdll(name):  # (as Windows' ctypes takes a library named None)
        raise TypeError("argument of type 'NoneType' is not iterable")

    monkeypatch.setattr(light.ctypes, "CDLL", windows_cdll)
    assert light.fewer_heaps("linux") is False  # (asked as on Linux, where there is no glibc)


def test_two_heaps_on_linux_as_the_app_starts(monkeypatch):
    """glibc's heaps per thread held a frame's memory each: two for all threads, set where the
    app starts (light.fewer_heaps), not as a side effect of importing Libre Panel."""
    assert light.fewer_heaps("win32") is False
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    assert light.fewer_heaps("linux") is False  # (one set in the environment holds)

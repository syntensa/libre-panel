"""The app's pictures in a process of their own while the app is open (editor/pictures.py):
the same pictures, byte for byte, as drawn in the app's process; what a job raised comes back
by its kind; the process ends (light mode) with its memory and starts again; one that ends
while it draws, or before it answers at all, leaves the pictures to the app's process."""

import os
import threading

import pictures_helper
import psutil
import pytest
from editor_token import token_of  # noqa: F401
from test_editor_server import request

from libre_panel.editor import pictures
from libre_panel.editor.pictures import PictureError, Pictures
from libre_panel.load_report import label_of, role_of
from libre_panel.render import fan
from libre_panel.sensors.base import Reading
from libre_panel.theme.model import find_theme

SMALL = {
    "format": "libre-panel-theme/1",
    "display": {"width": 320, "height": 160},
    "background": {"color": "#102030"},
    "widgets": [
        {"type": "text", "id": "t", "x": 10, "y": 10, "text": "Hallo", "size": 40},
        {"type": "graph", "id": "g", "x": 10, "y": 80, "w": 300, "h": 70, "sensor": "cpu.load"},
    ],
}


@pytest.fixture
def apart():
    drawn = Pictures(in_process=False)
    yield drawn
    drawn.stop()


def preview(theme="lumen", width=320):
    return (str(find_theme(theme)), None, width, None, "vivid", (), ("music", "weather"), 0, False)


def test_the_pictures_drawn_apart_are_those_drawn_in_the_apps_process(apart):
    here = Pictures(in_process=True)
    assert apart.ask("theme_preview", *preview()) == here.ask("theme_preview", *preview())
    assert apart.pid() not in (None, os.getpid())
    # Design's picture with live values and their history, and a test message on it
    live = ({"cpu.load": Reading("cpu.load", 42.0, "%")}, {"cpu.load": [10.0, 30.0, 20.0, 42.0]})
    render = (SMALL, None, (), fan.Choices(), "vivid", {}, live, "volume", None)
    answer = apart.ask("render", *render)
    assert answer == here.ask("render", *render) and answer["png"] and answer["boxes"]
    for job, args in (("module_previews", (None, None, "vivid")),
                      ("symbols_look", ("vivid",)), ("fan_look", ("libre", "theme"))):  # fmt: skip
        assert apart.ask(job, *args) == here.ask(job, *args), job


def test_what_a_job_raised_comes_back_by_its_kind(apart):
    here = Pictures(in_process=True)
    for drawn in (apart, here):
        with pytest.raises(PictureError) as raised:
            drawn.ask("theme_preview", "/no/such/theme", None, 320, None, "vivid", (), (), 0, False)
        assert raised.value.kind == "ThemeError"
        with pytest.raises(PictureError) as raised:
            drawn.ask("render", SMALL, None, (), fan.Choices(), "vivid", {}, None, "nope", None)
        assert raised.value.kind == "BadRequest" and "notice" in str(raised.value)
        with pytest.raises(PictureError) as raised:
            drawn.ask("fan_look", "no-such-look", "theme")
        assert raised.value.kind == "BadRequest"


def test_the_process_ends_with_its_memory_and_starts_again(apart):
    first = apart.ask("fan_look", "libre", "theme")
    pid = apart.pid()
    process = psutil.Process(pid)
    assert pid in pictures.picture_pids()
    apart.stop()
    assert apart.pid() is None and pid not in pictures.picture_pids()
    assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    assert apart.ask("fan_look", "libre", "theme") == first  # (a new one)
    assert apart.pid() not in (None, pid)


def test_a_process_that_ended_is_started_again(apart):
    first = apart.ask("fan_look", "libre", "theme")
    pid = apart.pid()
    psutil.Process(pid).kill()  # (as if it broke)
    psutil.Process(pid).wait(10)
    assert apart.ask("fan_look", "libre", "theme") == first  # (drawn here, or by a new one)
    assert apart.ask("fan_look", "libre", "theme") == first
    assert apart.pid() not in (None, pid)


def test_a_job_that_ends_the_process_is_not_drawn_in_the_apps(apart, monkeypatch):
    """What ended the pictures' process would end the panels' too: that picture fails, and the
    next one starts a new process."""
    monkeypatch.setattr(pictures, "child", pictures_helper.ends_while_drawing)
    for _ in range(3):
        with pytest.raises(PictureError) as raised:
            apart.ask("fan_look", "libre", "theme")
        assert raised.value.kind == "ProcessEnded"
    assert not apart._failed  # (each was going: a new one each time)


def test_a_process_that_ends_before_it_is_ready_leaves_the_pictures_to_the_apps(apart, monkeypatch):
    monkeypatch.setattr(pictures, "child", pictures_helper.ends_before_it_is_ready)
    here = Pictures(in_process=True).ask("fan_look", "libre", "theme")
    assert [apart.ask("fan_look", "libre", "theme") for _ in range(3)] == [here] * 3
    assert apart._failed  # (it never got going: drawn here from then on)


def test_a_process_that_does_not_get_going_is_not_started_again_and_again(apart, monkeypatch):
    started = []
    real = pictures.multiprocessing.get_context

    class Counting:
        def __init__(self, ctx):
            self.ctx = ctx

        def Pipe(self):  # noqa: N802 - the context's own name
            return self.ctx.Pipe()

        def Process(self, **kw):  # noqa: N802
            started.append(kw["target"])
            return self.ctx.Process(**kw)

    monkeypatch.setattr(pictures.multiprocessing, "get_context", lambda kind: Counting(real(kind)))
    monkeypatch.setattr(pictures, "child", pictures_helper.ends_at_once)
    here = Pictures(in_process=True).ask("symbols_look", "vivid")
    answers = [apart.ask("symbols_look", "vivid") for _ in range(4)]
    assert answers == [here] * 4  # each drawn here
    assert len(started) == pictures.START_TRIES  # and no more processes than that


def test_two_pictures_at_once_each_get_their_own(apart):
    sizes = (200, 260, 320, 380)
    got = {}

    def one(width):
        got[width] = apart.ask("theme_preview", *preview(width=width))

    threads = [threading.Thread(target=one, args=(w,)) for w in sizes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    here = Pictures(in_process=True)
    assert all(got[w] == here.ask("theme_preview", *preview(width=w)) for w in sizes)


def test_status_diagnose_load_calls_the_process_by_its_name():
    cmdline = ["LibrePanel.exe", "--multiprocessing-fork", "parent_pid=1"]
    assert role_of("LibrePanel.exe", cmdline, 77, 1, {}, None, frozenset({77})) == (
        "pictures",
        None,
    )
    assert role_of("LibrePanel.exe", cmdline, 78, 1, {}, None, frozenset({77}))[0] == "panel"
    assert label_of("pictures") == "the app's pictures"


def test_the_background_apps_pictures_are_drawn_apart_and_light_mode_ends_them(isolated_home):
    """The background app's editor draws its pictures apart (a plain editor here), and light
    mode's ``closed`` (EditorService.forget_pictures) ends the process."""
    from libre_panel.service import EditorService

    service = EditorService(port=0, controls=object())
    service.start()
    try:
        port = service._server.server_address[1]
        state = service._server.editor_state
        assert not state.pictures.in_process
        status, body = request(port, "GET", "/api/themes/libre-default/preview?w=240")
        assert status == 200 and body[:8] == b"\x89PNG\r\n\x1a\n"
        pid = state.pictures.pid()
        assert pid is not None and pid != os.getpid()
        service.forget_pictures()
        assert state.pictures.pid() is None and pid not in pictures.picture_pids()
        status, again = request(port, "GET", "/api/themes/libre-default/preview?w=240")
        assert status == 200 and again == body  # (drawn anew, the same)
    finally:
        service.stop()

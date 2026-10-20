"""While Windows ends the session (winsession.ending) Libre Panel starts no program: Windows
refuses it then, with a box of its own that names the program."""

import threading

import pytest

from libre_panel import app, winsession
from libre_panel.app import RunStatus
from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.h264 import Encoder


@pytest.fixture
def session_ends(monkeypatch):
    ending = threading.Event()
    monkeypatch.setattr(winsession, "ENDING", ending)
    ending.set()
    return ending


def test_no_ffmpeg_is_started_while_the_session_ends(session_ends):
    started = []
    with pytest.raises(DeviceError, match="ending the session"):
        Encoder(["ffmpeg"], (1920, 480), lambda data: None, popen=lambda *a, **k: started.append(a))
    assert started == []


class Panel(Display):
    def __init__(self):
        super().__init__(None)
        self.opened = 0

    def open(self):
        self.opened += 1

    def show(self, frame, region=None):
        pass


def test_the_panel_is_not_connected_again_on_the_way_out(session_ends):
    panel = Panel()
    link = app._Link(panel, 50, RunStatus())
    assert not link.ensure(100.0) and panel.opened == 0
    session_ends.clear()  # another program cancelled the end of the session
    assert link.ensure(100.0) and panel.opened == 1

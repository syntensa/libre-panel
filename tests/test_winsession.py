"""Windows: the background app stops cleanly before Windows ends the session."""

import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows session messages")

from test_service import FakeRegistry, wait_for, write_config  # noqa: E402

from libre_panel.autostart import Autostart  # noqa: E402
from libre_panel.service import BackgroundApp, running_instance  # noqa: E402
from libre_panel.tray import run_app  # noqa: E402


def send(hwnd, message, wparam):
    """What Windows does at the end of a session: a message that waits for the answer."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    return user32.SendMessageW(hwnd, message, wparam, 0)


def test_session_window_answers_and_stops_once():
    from libre_panel.winsession import WM_ENDSESSION, WM_QUERYENDSESSION, SessionEndWatcher

    calls = []
    watcher = SessionEndWatcher(lambda: calls.append(threading.current_thread().name)).start()
    try:
        assert send(watcher.hwnd, WM_QUERYENDSESSION, 0) == 1  # never blocks shutdown
        send(watcher.hwnd, WM_ENDSESSION, 0)  # another program cancelled the shutdown
        assert calls == []
        send(watcher.hwnd, WM_ENDSESSION, 1)
        send(watcher.hwnd, WM_ENDSESSION, 1)
        assert calls == ["session-end"]
    finally:
        watcher.stop()
    assert not watcher._thread.is_alive()


def test_background_app_has_stopped_when_windows_gets_its_answer(isolated_home):
    from libre_panel.winsession import WM_ENDSESSION

    write_config(isolated_home)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    result = []
    thread = threading.Thread(target=lambda: result.append(run_app(app, use_icon=False)))
    thread.start()
    try:
        assert wait_for(lambda: app.session_watcher is not None)
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        send(app.session_watcher.hwnd, WM_ENDSESSION, 1)  # returns once the app has stopped
        assert app.quit_requested.is_set() and not app.panel.running
        assert running_instance() is None
    finally:
        app.quit()
        thread.join(10)
    assert result == [0]

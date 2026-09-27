"""Windows: stop cleanly when the session ends (shut down, restart, sign out).

At the end of a session Windows sends WM_QUERYENDSESSION and WM_ENDSESSION to
every top-level window and may end the process as soon as WM_ENDSESSION has
been answered. Without a window of its own a program is simply cut off, and
the panel keeps whatever the last command left: in video mode a frozen stream
and a frame rate its own standby clip stutters at.

A hidden window answers WM_ENDSESSION only after Libre Panel has stopped (or
after a few seconds; Windows waits about five before it asks the user).
SetProcessShutdownParameters asks Windows to tell Libre Panel early, before
most other programs. The SPUR II service found both necessary on the 9.2".
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
# Applications may use 0x100-0x3FF; higher is told earlier.
EARLY_SHUTDOWN_LEVEL = 0x3FF


def shut_down_early() -> None:
    """Be among the first programs Windows tells about the end of the session."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.SetProcessShutdownParameters(EARLY_SHUTDOWN_LEVEL, 0):
        log.debug("SetProcessShutdownParameters failed (%d)", ctypes.get_last_error())


class SessionEndWatcher:
    """A hidden window that runs ``on_end`` when the Windows session ends.

    ``on_end`` runs on the window's thread and may block: Windows waits for
    the answer to WM_ENDSESSION before it ends the process.
    """

    def __init__(self, on_end: Callable[[], None]) -> None:
        if sys.platform != "win32":
            raise OSError("only on Windows")
        self.on_end = on_end
        self.hwnd: int | None = None
        self.ended = False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="session-end", daemon=True)

    def start(self) -> SessionEndWatcher:
        self._thread.start()
        if not self._ready.wait(5) or not self.hwnd:
            raise OSError("could not create the session window")
        return self

    def stop(self) -> None:
        if self.hwnd and self._thread.is_alive():
            import ctypes

            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(5)

    def _end(self) -> None:
        if self.ended:
            return
        self.ended = True
        try:
            self.on_end()
        except Exception:  # Windows is waiting; answer in any case
            log.exception("while stopping for the end of the session")

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        wndproc = ctypes.WINFUNCTYPE(
            lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = lresult
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def procedure(hwnd, message, wparam, lparam):
            if message == WM_QUERYENDSESSION:
                return 1  # Libre Panel never holds up the end of a session
            if message == WM_ENDSESSION:
                if wparam:  # the session really ends (not cancelled)
                    self._end()
                return 0
            if message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._procedure = wndproc(procedure)  # keep it alive as long as the window
        instance = kernel32.GetModuleHandleW(None)
        name = f"LibrePanelSession{id(self)}"
        window_class = WNDCLASSW(
            lpfnWndProc=self._procedure, hInstance=instance, lpszClassName=name
        )
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            log.warning("session window: RegisterClass failed (%d)", ctypes.get_last_error())
            self._ready.set()
            return
        # A hidden top-level window: message-only windows get no session messages.
        self.hwnd = user32.CreateWindowExW(
            0, name, "Libre Panel", 0, 0, 0, 0, 0, None, None, instance, None
        )
        self._ready.set()
        if not self.hwnd:
            log.warning("session window: CreateWindow failed (%d)", ctypes.get_last_error())
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnregisterClassW(name, instance)

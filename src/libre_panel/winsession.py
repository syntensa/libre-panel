"""Windows: stop cleanly when the session ends (shut down, restart, sign out), and sleep with
the computer.

At the end of a session Windows sends WM_QUERYENDSESSION and WM_ENDSESSION to
every top-level window and may end the process as soon as WM_ENDSESSION has
been answered. Without a window of its own a program is simply cut off, and
the panel keeps whatever the last command left: in video mode a frozen stream
and a frame rate its own standby clip stutters at.

A hidden window answers WM_ENDSESSION only after Libre Panel has stopped (or
after a few seconds; Windows waits about five before it asks the user).
SetProcessShutdownParameters asks Windows to tell Libre Panel early, before
most other programs. The SPUR II service found both necessary on the 9.2".

While the session ends, no program may be started: Windows refuses it, and says so in a
box of its own ("The application was unable to start correctly", 0xc0000142), on the way
out of Windows. :func:`ending` tells the parts of Libre Panel that start one (PresentMon).

The same window hears when the computer goes to sleep and wakes (WM_POWERBROADCAST; on a
computer with modern standby once it asked for it, RegisterSuspendResumeNotification): the
panel then goes dark and lights up again, as turing-smart-screen-python does. It hears too
when the session is locked and unlocked (WTSRegisterSessionNotification) and when the monitor
goes off and on (RegisterPowerSettingNotification, GUID_CONSOLE_DISPLAY_STATE), and tells
presence.py: the panels dim while the person is away.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

from libre_panel import presence

log = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004  # the computer goes to sleep
PBT_APMRESUMESUSPEND = 0x0007  # it woke, a person at it
PBT_APMRESUMEAUTOMATIC = 0x0012  # it woke (always sent)
PBT_POWERSETTINGCHANGE = 0x8013  # a power setting asked for changed: the monitor's state
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
# GUID_CONSOLE_DISPLAY_STATE: 0 the monitor is off, 1 on, 2 dimmed
CONSOLE_DISPLAY_STATE = "6fe69556-704a-47a0-8f24-c28d936fda47"
# Applications may use 0x100-0x3FF; higher is told earlier.
EARLY_SHUTDOWN_LEVEL = 0x3FF

# Set from the moment Windows asks whether the session may end, until it ends or another
# program cancels it
ENDING = threading.Event()


def ending() -> bool:
    """Whether the Windows session is ending (shut down, restart, sign out): start no program."""
    return ENDING.is_set()


def shut_down_early() -> None:
    """Be among the first programs Windows tells about the end of the session."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.SetProcessShutdownParameters(EARLY_SHUTDOWN_LEVEL, 0):
        log.debug("SetProcessShutdownParameters failed (%d)", ctypes.get_last_error())


class SessionEndWatcher:
    """A hidden window that runs ``on_end`` when the Windows session ends, and ``on_sleep``
    and ``on_wake`` when the computer goes to sleep and wakes.

    They run on the window's thread and may block: Windows waits for the answer to
    WM_ENDSESSION before it ends the process (and gives a sleep about two seconds).
    """

    def __init__(
        self,
        on_end: Callable[[], None],
        on_sleep: Callable[[], None] | None = None,
        on_wake: Callable[[], None] | None = None,
    ) -> None:
        if sys.platform != "win32":
            raise OSError("only on Windows")
        self.on_end = on_end
        self.on_sleep, self.on_wake = on_sleep, on_wake
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
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(5)

    def _power(self, event: int) -> None:
        """The computer goes to sleep or wakes."""
        callback = self.on_sleep if event == PBT_APMSUSPEND else self.on_wake
        if callback is None or event not in (
            PBT_APMSUSPEND, PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC
        ):  # fmt: skip
            return
        try:
            callback()
        except Exception:  # Windows is waiting; answer in any case
            log.exception("while the computer went to sleep or woke")

    @staticmethod
    def _session(event: int) -> None:
        """The session was locked or unlocked."""
        if event in (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK):
            presence.set_away(presence.LOCKED, event == WTS_SESSION_LOCK)

    @staticmethod
    def _display(state: int) -> None:
        """The monitor went off (0), on (1) or dimmed (2: still on)."""
        presence.set_away(presence.DISPLAY_OFF, state == 0)

    def _end(self) -> None:
        if self.ended:
            return
        self.ended = True
        try:
            self.on_end()
        except Exception:  # Windows is waiting; answer in any case
            log.exception("while stopping for the end of the session")

    def _setting(self, lparam: int) -> None:
        """A power setting changed: lparam points to a POWERBROADCAST_SETTING (its GUID, the
        length of its data, the data); the monitor's state is a DWORD."""
        import ctypes
        import uuid

        try:
            raw = ctypes.string_at(lparam, 20)  # GUID (16) and DataLength (4)
            if str(uuid.UUID(bytes_le=raw[:16])) != CONSOLE_DISPLAY_STATE:
                return
            length = int.from_bytes(raw[16:20], "little")
            data = ctypes.string_at(lparam + 20, min(length, 4))
            self._display(int.from_bytes(data, "little"))
        except (OSError, ValueError):  # never in Windows' way
            log.debug("a power setting that could not be read", exc_info=True)

    def _ask_for_presence(self, user32) -> None:
        """Ask Windows to tell the window about locking and the monitor (presence.py)."""
        import ctypes
        import uuid
        from ctypes import wintypes

        try:
            wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
            wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            if not wtsapi32.WTSRegisterSessionNotification(self.hwnd, 0):  # this session
                log.debug("no word of locking (%d)", ctypes.get_last_error())
        except (OSError, AttributeError) as exc:
            log.debug("no word of locking: %s", exc)
        try:
            guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(CONSOLE_DISPLAY_STATE).bytes_le)
            user32.RegisterPowerSettingNotification.argtypes = [
                wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD
            ]  # fmt: skip
            user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
            self._guid = guid  # (kept as long as the window)
            if not user32.RegisterPowerSettingNotification(self.hwnd, ctypes.byref(guid), 0):
                log.debug("no word of the monitor (%d)", ctypes.get_last_error())
        except (OSError, AttributeError) as exc:
            log.debug("no word of the monitor: %s", exc)

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
        # 64-bit handles: without argtypes ctypes passes them as 32-bit ints
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def procedure(hwnd, message, wparam, lparam):
            if message == WM_QUERYENDSESSION:
                ENDING.set()
                return 1  # Libre Panel never holds up the end of a session
            if message == WM_ENDSESSION:
                if wparam:  # the session really ends (not cancelled)
                    ENDING.set()
                    self._end()
                else:
                    ENDING.clear()
                return 0
            if message == WM_POWERBROADCAST:
                if wparam == PBT_POWERSETTINGCHANGE:
                    self._setting(lparam)
                else:
                    self._power(wparam)
                return 1  # (TRUE: Libre Panel never holds up a sleep)
            if message == WM_WTSSESSION_CHANGE:
                self._session(wparam)
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
        try:  # modern standby tells only who asks (Windows 8 and later)
            user32.RegisterSuspendResumeNotification.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            user32.RegisterSuspendResumeNotification.restype = wintypes.HANDLE
            user32.RegisterSuspendResumeNotification(self.hwnd, 0)  # DEVICE_NOTIFY_WINDOW_HANDLE
        except AttributeError:
            pass
        self._ask_for_presence(user32)
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnregisterClassW(name, instance)

"""What of this computer's screen a panel can show as a monitor (mirror.py), and how ffmpeg
captures it: the monitors and the windows, each with an id that names it again in another
process of the app, and the input ffmpeg reads its pictures from.

- **Windows**: the monitors as Windows lists them (EnumDisplayMonitors, in physical pixels: the
  listing is per-monitor DPI aware), named by their device (``\\\\.\\DISPLAY1``), a virtual one
  (Virtual Display Driver, extend.py) marked so; the windows a person sees (visible, not
  cloaked, not a tool window), by their handle. ffmpeg captures through Windows.Graphics.Capture
  (``gfxcapture``, ffmpeg 8.1: a monitor or a window by its handle, the cursor with it, on the
  graphics card), else a monitor through Desktop Duplication (``ddagrab``) or GDI (``gdigrab``,
  any ffmpeg, a window by its handle too).
- **Linux on X11**: the monitors ``xrandr --listmonitors`` names (a virtual one of extend.py
  among them), the windows of ``_NET_CLIENT_LIST`` (``xprop``); ffmpeg's ``x11grab`` captures a
  part of the screen or a window.
- **Linux on Wayland**: nothing of the screen is open to a program; the desktop shares it
  (wayland.py): a monitor or a window the person picks, or a monitor of its own. Its pictures
  come through PipeWire and GStreamer (``gst-launch-1.0``), raw, into ffmpeg's input.

A part of a monitor (``region``: x, y, width and height within it) is cropped as it is
captured. The pictures go on in ffmpeg: fitted into the panel's frame, at the panel's frames a
second (mirror.py, devices/h264.py).
"""

from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

RUN_TIMEOUT_S = 5.0
VIRTUAL_MARKS = ("virtual display",)  # a virtual monitor's adapter names itself so (lowercase)


@dataclass(frozen=True)
class Monitor:
    """A monitor of this computer: ``id`` names it again (a device on Windows, an output on
    X11), its place and size on the desktop in pixels, whether it is the main one and whether
    it is a virtual one (a monitor of its own for a panel, extend.py)."""

    id: str
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    virtual: bool = False


@dataclass(frozen=True)
class Window:
    """A window a person sees: ``id`` (its handle) names it again; its title and program."""

    id: str
    title: str
    program: str = ""


@dataclass(frozen=True)
class Capture:
    """How ffmpeg gets the pictures of a source: its input (``-f ... -i ...``), the filters
    that bring them to the processor (``hwdownload``) and crop them, and where a program feeds
    it raw frames (Wayland: ``feeder``, writing to ffmpeg's input; ``fds`` it is handed)."""

    inputs: tuple[str, ...]
    prefilter: str = ""
    feeder: tuple[str, ...] = ()
    fds: tuple[int, ...] = ()
    label: str = ""


class CaptureError(Exception):
    """A source that cannot be captured here, said for the person."""


def platform() -> str:
    """Where Libre Panel runs: ``windows``, ``x11``, ``wayland`` or ``other`` (macOS ...)."""
    if sys.platform == "win32":
        return "windows"
    if not sys.platform.startswith("linux"):
        return "other"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    return "x11" if os.environ.get("DISPLAY") else "other"


# -- ffmpeg ---------------------------------------------------------------------------------


@functools.lru_cache(maxsize=8)
def _filters(ffmpeg: str) -> frozenset[str]:
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                              text=True, timeout=RUN_TIMEOUT_S, **_quiet())  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = (line.split()[1] for line in done.stdout.splitlines() if len(line.split()) > 2)
    return frozenset(names)


@functools.lru_cache(maxsize=8)
def _devices(ffmpeg: str) -> frozenset[str]:
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-devices"], capture_output=True,
                              text=True, timeout=RUN_TIMEOUT_S, **_quiet())  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    found = (re.match(r"\s*D\S*\s+(\S+)", line) for line in done.stdout.splitlines())
    return frozenset(match.group(1) for match in found if match)


def ffmpeg_can(ffmpeg: str, name: str) -> bool:
    """Whether ``ffmpeg`` has the filter or input device ``name`` (gfxcapture, x11grab ...)."""
    return name in _filters(ffmpeg) or name in _devices(ffmpeg)


def _quiet() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _crop(region: tuple[int, int, int, int] | None, width: int, height: int) -> tuple[int, ...]:
    """``region`` within a monitor of width x height, kept inside it, even-sized (as an
    encoder takes it); the whole monitor without one."""
    if region is None:
        return (0, 0, width, height)
    x, y, w, h = (int(v) for v in region)
    x, y = max(0, min(x, width - 2)), max(0, min(y, height - 2))
    w, h = max(2, min(w, width - x)), max(2, min(h, height - y))
    return (x, y, w - w % 2, h - h % 2)


def monitor_capture(ffmpeg: str, monitor: Monitor, fps: int, cursor: bool = True,
                    region: tuple[int, int, int, int] | None = None,
                    where: str | None = None) -> Capture:  # fmt: skip
    """ffmpeg's input for ``monitor`` (or a ``region`` of it) at up to ``fps`` frames a second,
    as ``where`` (:func:`platform`) captures it."""
    where = where or platform()
    x, y, w, h = _crop(region, monitor.width, monitor.height)
    mouse = "1" if cursor else "0"
    if where == "windows":
        if ffmpeg_can(ffmpeg, "gfxcapture"):
            handle = _monitor_handle(monitor.id) if sys.platform == "win32" else None
            pick = f"hmonitor={handle:#x}" if handle else f"monitor_idx={_index_of(monitor)}"
            crop = (f":crop_left={x}:crop_top={y}:crop_right={monitor.width - x - w}"
                    f":crop_bottom={monitor.height - y - h}") if region else ""  # fmt: skip
            source = f"gfxcapture={pick}:max_framerate={fps}:capture_cursor={mouse}{crop}"
            return Capture(("-f", "lavfi", "-i", source), "hwdownload,format=bgra",
                           label=monitor.name)  # fmt: skip
        inputs = ("-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse,
                  "-offset_x", str(monitor.x + x), "-offset_y", str(monitor.y + y),
                  "-video_size", f"{w}x{h}", "-i", "desktop")  # fmt: skip
        return Capture(inputs, label=monitor.name)
    if where == "x11":
        display = os.environ.get("DISPLAY", ":0")
        return Capture(("-f", "x11grab", "-framerate", str(fps), "-draw_mouse", mouse,
                        "-video_size", f"{w}x{h}",
                        "-i", f"{display}+{monitor.x + x},{monitor.y + y}"),
                       label=monitor.name)  # fmt: skip
    raise CaptureError("the desktop shares its screen itself here (Wayland)")


def window_capture(ffmpeg: str, window: Window, fps: int, cursor: bool = True,
                   where: str | None = None) -> Capture:  # fmt: skip
    """ffmpeg's input for ``window`` at up to ``fps`` frames a second."""
    where = where or platform()
    mouse = "1" if cursor else "0"
    if where == "windows":
        handle = int(window.id, 0)
        if ffmpeg_can(ffmpeg, "gfxcapture"):
            source = f"gfxcapture=hwnd={handle:#x}:max_framerate={fps}:capture_cursor={mouse}"
            return Capture(("-f", "lavfi", "-i", source), "hwdownload,format=bgra",
                           label=window.title)  # fmt: skip
        return Capture(("-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse,
                        "-i", f"hwnd={handle:#x}"), label=window.title)  # fmt: skip
    if where == "x11":
        display = os.environ.get("DISPLAY", ":0")
        return Capture(("-f", "x11grab", "-framerate", str(fps), "-draw_mouse", mouse,
                        "-window_id", window.id, "-i", display), label=window.title)  # fmt: skip
    raise CaptureError("the desktop shares its windows itself here (Wayland)")


# -- what there is ----------------------------------------------------------------------------


def monitors(where: str | None = None) -> list[Monitor]:
    """The monitors of this computer, the main one first; [] where none can be listed (Wayland:
    the desktop lists them in its own dialog)."""
    where = where or platform()
    found: list[Monitor] = []
    try:
        if where == "windows":
            found = _windows_monitors()
        elif where == "x11":
            found = parse_xrandr(_run(["xrandr", "--listmonitors"]))
        elif where == "wayland":
            from libre_panel import wayland

            found = wayland.monitors()  # (GNOME's; elsewhere the desktop's dialog lists them)
    except (OSError, ValueError) as exc:
        log.info("capture: no monitors listed (%s)", exc)
    return sorted(found, key=lambda m: (not m.primary, m.virtual, m.x, m.y))


def windows(where: str | None = None) -> list[Window]:
    """The windows a person sees, as the desktop lists them; [] where none can be listed."""
    where = where or platform()
    try:
        if where == "windows":
            return _windows_windows()
        if where == "x11":
            return _x11_windows()
    except (OSError, ValueError) as exc:
        log.info("capture: no windows listed (%s)", exc)
    return []


def find_monitor(id: str, where: str | None = None) -> Monitor | None:
    return next((m for m in monitors(where) if m.id == id), None)


def find_window(id: str, where: str | None = None) -> Window | None:
    return next((w for w in windows(where) if w.id == id), None)


def _run(command: list[str]) -> str:
    program = shutil.which(command[0])
    if program is None:
        raise OSError(f"no {command[0]}")
    done = subprocess.run([program, *command[1:]], capture_output=True, text=True,
                          timeout=RUN_TIMEOUT_S, **_quiet())  # fmt: skip
    if done.returncode != 0:
        raise OSError(f"{command[0]}: {done.stderr.strip()[:200] or done.returncode}")
    return done.stdout


_XRANDR = re.compile(
    r"^\s*\d+:\s+(?P<flags>[+*]*)(?P<name>\S+)\s+(?P<w>\d+)/\d+x(?P<h>\d+)/\d+"
    r"\+(?P<x>-?\d+)\+(?P<y>-?\d+)(?:\s+(?P<outputs>.*))?$"
)


def parse_xrandr(text: str) -> list[Monitor]:
    """The monitors of ``xrandr --listmonitors``: `` 0: +*DP-1 2560/597x1440/336+0+0  DP-1``;
    one without an output of its own (``none``) is a virtual one (extend.py)."""
    found = []
    for line in text.splitlines():
        match = _XRANDR.match(line)
        if match is None:
            continue
        name = match["name"]
        outputs = (match["outputs"] or "").split()
        virtual = not outputs or outputs == ["none"] or name.lower().startswith("librepanel")
        found.append(Monitor(name, name, int(match["x"]), int(match["y"]), int(match["w"]),
                             int(match["h"]), "*" in match["flags"], virtual))  # fmt: skip
    return found


def _x11_windows() -> list[Window]:
    listed = _run(["xprop", "-root", "_NET_CLIENT_LIST"])
    found = []
    for handle in re.findall(r"0x[0-9a-fA-F]+", listed.split("=", 1)[-1]):
        try:
            said = _run(["xprop", "-id", handle, "_NET_WM_NAME", "WM_CLASS", "_NET_WM_STATE"])
        except OSError:
            continue
        if "_NET_WM_STATE_SKIP_TASKBAR" in said or "_NET_WM_STATE_HIDDEN" in said:
            continue
        title = re.search(r'_NET_WM_NAME\(UTF8_STRING\) = "(.*)"', said)
        program = re.search(r'WM_CLASS\(STRING\) = "[^"]*", "([^"]*)"', said)
        if title and title.group(1).strip():
            found.append(Window(handle, title.group(1), program.group(1) if program else ""))
    return found


# -- Windows ------------------------------------------------------------------------------------


def _user32() -> Any:
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


class _DpiAware:
    """Per-monitor DPI aware while listing, so places and sizes are physical pixels as ffmpeg
    captures them (the app itself may be DPI unaware: Windows would scale them)."""

    def __enter__(self) -> None:
        import ctypes

        user32 = _user32()
        setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
        self._before = None
        if setter is not None:
            setter.restype = ctypes.c_void_p
            setter.argtypes = (ctypes.c_void_p,)
            self._before = setter(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
            self._setter = setter

    def __exit__(self, *exc: object) -> None:
        if self._before:
            import ctypes

            self._setter(ctypes.c_void_p(self._before))


def _windows_monitors() -> list[Monitor]:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()

    class MonitorInfo(ctypes.Structure):  # MONITORINFOEXW
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD),
                    ("device", wintypes.WCHAR * 32)]  # fmt: skip

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)  # fmt: skip
    user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))
    found: list[Monitor] = []
    adapters = _windows_adapters()

    def each(handle: int, _dc: int, _rect: Any, _data: int) -> bool:
        info = MonitorInfo()
        info.size = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            box = info.monitor
            device = info.device
            adapter = adapters.get(device, "")
            number = re.search(r"(\d+)$", device)
            name = f"Display {number.group(1)}" if number else device
            virtual = any(mark in adapter.lower() for mark in VIRTUAL_MARKS)
            found.append(Monitor(device, name, box.left, box.top, box.right - box.left,
                                 box.bottom - box.top, bool(info.flags & 1), virtual))  # fmt: skip
        return True

    with _DpiAware():
        user32.EnumDisplayMonitors(None, None, callback(each), 0)
    return found


def _windows_adapters() -> dict[str, str]:
    """``{"\\\\.\\DISPLAY1": "the graphics adapter's name"}`` (a virtual one names itself)."""
    import ctypes
    from ctypes import wintypes

    class Device(ctypes.Structure):  # DISPLAY_DEVICEW
        _fields_ = [("size", wintypes.DWORD), ("name", wintypes.WCHAR * 32),
                    ("string", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                    ("id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]  # fmt: skip

    user32 = _user32()
    found = {}
    index = 0
    while True:
        device = Device()
        device.size = ctypes.sizeof(Device)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            return found
        found[device.name] = device.string
        index += 1


def _monitor_handle(device: str) -> int | None:
    """The HMONITOR of the monitor named ``device`` now (handles change as monitors come)."""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    handles: list[int] = []

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD),
                    ("device", wintypes.WCHAR * 32)]  # fmt: skip

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)  # fmt: skip
    user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))

    def each(handle: int, _dc: int, _rect: Any, _data: int) -> bool:
        info = MonitorInfo()
        info.size = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)) and info.device == device:
            handles.append(handle)
        return True

    user32.EnumDisplayMonitors(None, None, callback(each), 0)
    return handles[0] if handles else None


def _index_of(monitor: Monitor) -> int:
    number = re.search(r"(\d+)$", monitor.id)
    return max(0, int(number.group(1)) - 1) if number else 0


def _windows_windows() -> list[Window]:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    dwmapi = ctypes.WinDLL("dwmapi")
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    own = os.getpid()
    found: list[Window] = []

    def each(handle: int, _data: int) -> bool:
        if not user32.IsWindowVisible(handle) or user32.GetWindow(handle, 4):  # (GW_OWNER)
            return True
        style = user32.GetWindowLongW(handle, -20)  # GWL_EXSTYLE
        if style & 0x80:  # WS_EX_TOOLWINDOW
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:  # (another virtual desktop's, a suspended app's)
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, text, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value == own:
            return True
        found.append(Window(f"{handle:#x}", text.value, _program(pid.value)))
        return True

    user32.EnumWindows(callback(each), 0)
    return found


def _program(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:  # (gone, or not ours to look at)
        return ""

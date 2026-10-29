"""A monitor of the panel's own: the computer extends its desktop to it, windows go there as to
any monitor, and the panel shows it (mirror.py, ``virtual``). It is on the desktop while a panel
shows it, and taken off again after, so the mouse never wanders onto a monitor nobody sees.

- **Windows**: a monitor needs a display driver; the Virtual Display Driver (MIT, signed,
  VirtualDrivers/Virtual-Display-Driver) gives one. Setting it up (:func:`setup`) installs it
  with winget and puts the panel's size among its resolutions: its settings file
  (``vdd_settings.xml`` in ``C:\\VirtualDisplayDriver``) is the administrators', so Windows
  asks once; the driver then reads it again when told so through its pipe, which anyone may
  use. Showing it puts its monitor on the desktop at the panel's size, right of the others
  (ChangeDisplaySettingsEx, no rights needed); stopping takes it off.
- **Linux on X11**: a part of the screen beyond the monitors, as a monitor of its own
  (``xrandr --fb`` and ``--setmonitor ... none``): window managers that follow RandR's monitors
  (most do) put windows there and maximise them to it. Nothing is installed.
- **Linux on Wayland**: the desktop makes the monitor as the panel's capture starts and takes
  it away as it ends (wayland.py): GNOME (Mutter's virtual monitors) at the panel's size; KDE
  through the screen-sharing dialog's *New virtual output*, until Plasma 6.8 always 1920x1080
  (cropped to the panel). Nothing to set up.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from libre_panel import capture

log = logging.getLogger(__name__)

VDD_ID = "VirtualDrivers.Virtual-Display-Driver"
VDD_PIPE = r"\\.\pipe\MTTVirtualDisplayPipe"
VDD_FOLDER = Path(r"C:\VirtualDisplayDriver")
VDD_NAME = "virtual display driver"  # (its adapter's name, lowercase)
X11_NAME = "LibrePanel"  # the X11 monitor's name
SETUP_TIMEOUT_S = 600.0
REFRESH = 60


class ExtendError(Exception):
    """What keeps the panel's own monitor from being set up or shown, said for the person."""


def state(size: tuple[int, int] | None, where: str | None = None) -> dict[str, Any]:
    """For the app: whether a monitor of the panel's own can be had here (``how``: "vdd",
    "xrandr", "wayland", or "" where not), whether it is set up (``ready``; Windows: the driver
    is there and knows the panel's size), and what is missing (``missing``: "driver",
    "resolution", "xrandr", "")."""
    where = where or capture.platform()
    if where == "windows":
        installed = vdd_installed()
        sized = installed and size is not None and vdd_has_resolution(size)
        missing = "" if sized else "resolution" if installed else "driver"
        return {"how": "vdd", "ready": sized, "missing": missing}
    if where == "x11":
        found = shutil.which("xrandr") is not None
        return {"how": "xrandr", "ready": found, "missing": "" if found else "xrandr"}
    if where == "wayland":
        from libre_panel import wayland

        return {"how": "wayland", **wayland.virtual_state()}
    return {"how": "", "ready": False, "missing": ""}


def virtual_monitor(where: str | None = None) -> capture.Monitor | None:
    """The panel's own monitor while it is on the desktop (Windows, X11); None else."""
    where = where or capture.platform()
    return next((m for m in capture.monitors(where) if m.virtual), None)


def attach(size: tuple[int, int], where: str | None = None) -> None:
    """The panel's own monitor on the desktop, ``size`` large (ExtendError: why not)."""
    where = where or capture.platform()
    if where == "windows":
        _vdd_attach(size)
    elif where == "x11":
        _x11_attach(size)
    # (Wayland: the capture makes it)


def detach(where: str | None = None) -> None:
    """The panel's own monitor off the desktop again (nothing where there is none)."""
    where = where or capture.platform()
    try:
        if where == "windows":
            _vdd_detach()
        elif where == "x11":
            _x11_detach()
    except (OSError, ExtendError) as exc:
        log.warning("monitor: the panel's own monitor stays (%s)", exc)


# -- Windows: the Virtual Display Driver -------------------------------------------------------


def vdd_folder() -> Path:
    """Where the driver reads its settings (its VDDPATH, else its default)."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\MikeTheTech\VirtualDisplayDriver") as key:  # fmt: skip
                value, _kind = winreg.QueryValueEx(key, "VDDPATH")
                if value:
                    return Path(value)
        except OSError:
            pass
    return VDD_FOLDER


def vdd_installed() -> bool:
    """Whether the driver's adapter is there (its monitor on the desktop or not)."""
    if sys.platform != "win32":
        return False
    try:
        return any(VDD_NAME in name.lower() for name in capture._windows_adapters().values())
    except OSError:
        return False


def vdd_has_resolution(size: tuple[int, int], text: str | None = None) -> bool:
    """Whether the driver's settings name ``size`` among its resolutions."""
    if text is None:
        try:
            text = (vdd_folder() / "vdd_settings.xml").read_text(encoding="utf-8")
        except OSError:
            return False
    width, height = size
    found = re.findall(r"<resolution>\s*<width>\s*(\d+)\s*</width>\s*<height>\s*(\d+)\s*</height>",
                       text)  # fmt: skip
    return (str(width), str(height)) in found


def with_resolution(text: str, size: tuple[int, int], refresh: int = REFRESH) -> str:
    """The driver's settings with ``size`` among its resolutions (as it was where it is)."""
    if vdd_has_resolution(size, text):
        return text
    width, height = size
    inner = " " * 12
    entry = (f"\n        <resolution>\n{inner}<width>{width}</width>\n"
             f"{inner}<height>{height}</height>\n"
             f"{inner}<refresh_rate>{refresh}</refresh_rate>\n        </resolution>")  # fmt: skip
    if "</resolutions>" in text:  # (after the last, before the list's end and its indent)
        return re.sub(r"(\s*)</resolutions>", lambda m: f"{entry}{m.group(1)}</resolutions>",
                      text, count=1)  # fmt: skip
    if "</vdd_settings>" in text:
        block = f"    <resolutions>{entry}\n    </resolutions>\n"
        return text.replace("</vdd_settings>", block + "</vdd_settings>", 1)
    raise ExtendError("the driver's settings file is not what it should be")


def setup(size: tuple[int, int]) -> str:
    """Windows: the driver installed (winget) and the panel's size among its resolutions;
    Windows asks for the administrator's consent. What was done, said for the person."""
    if sys.platform != "win32":
        raise ExtendError("only on Windows")
    done = []
    if not vdd_installed():
        winget = shutil.which("winget")
        if winget is None:
            raise ExtendError("winget is missing: install the Virtual Display Driver from its "
                              "releases page, then set it up here again")  # fmt: skip
        result = subprocess.run(
            [winget, "install", "--id", VDD_ID, "-e", "--accept-package-agreements",
             "--accept-source-agreements", "--disable-interactivity"],
            capture_output=True, text=True, timeout=SETUP_TIMEOUT_S, **capture._quiet(),
        )  # fmt: skip
        if result.returncode != 0 and not vdd_installed():
            said = (result.stdout or result.stderr or "").strip().splitlines()
            raise ExtendError(f"winget: {said[-1] if said else result.returncode}")
        done.append("driver")
        time.sleep(2.0)  # (its adapter comes a moment later)
    if not vdd_has_resolution(size):
        _vdd_write_resolution(size)
        done.append("resolution")
    _vdd_reload()
    _vdd_detach()  # (the driver puts its monitor on the desktop: off until a panel shows it)
    return ", ".join(done) or "nothing"


_setting_up = threading.Lock()
_setup: dict[str, Any] = {"running": False, "said": "", "error": ""}


def start_setup(size: tuple[int, int]) -> None:
    """:func:`setup` in the background (winget takes minutes): :func:`setup_state` says how
    it goes. Nothing new while one runs."""
    if not _setting_up.acquire(blocking=False):
        return
    _setup.update(running=True, said="", error="")

    def work() -> None:
        try:
            said = setup(size)
            _setup.update(said=said, error="")
            log.info("monitor: the panel's own monitor set up (%s)", said)
        except (ExtendError, OSError, subprocess.SubprocessError) as exc:
            _setup.update(said="", error=str(exc))
            log.warning("monitor: the panel's own monitor was not set up (%s)", exc)
        finally:
            _setup["running"] = False
            _setting_up.release()

    threading.Thread(target=work, name="monitor-setup", daemon=True).start()


def setup_state() -> dict[str, Any]:
    """How the last setup went: ``running``, what it did (``said``) or why not (``error``)."""
    return dict(_setup)


def _vdd_write_resolution(size: tuple[int, int]) -> None:
    target = vdd_folder() / "vdd_settings.xml"
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtendError(f"the driver's settings cannot be read ({exc})") from exc
    changed = with_resolution(text, size)
    staged = Path(tempfile.gettempdir()) / "libre-panel-vdd_settings.xml"
    staged.write_text(changed, encoding="utf-8")
    try:
        target.write_text(changed, encoding="utf-8")  # (where the person may write it)
        return
    except OSError:
        pass
    _elevated_copy(staged, target)
    if not vdd_has_resolution(size):
        raise ExtendError("the panel's size did not get into the driver's settings")


def _elevated_copy(source: Path, target: Path) -> None:
    """``source`` over ``target`` as the administrator (Windows asks): the driver's settings."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):  # SHELLEXECUTEINFOW
        _fields_ = [("size", wintypes.DWORD), ("mask", wintypes.ULONG),
                    ("window", wintypes.HWND), ("verb", wintypes.LPCWSTR),
                    ("file", wintypes.LPCWSTR), ("parameters", wintypes.LPCWSTR),
                    ("directory", wintypes.LPCWSTR), ("show", ctypes.c_int),
                    ("app", wintypes.HINSTANCE), ("idlist", ctypes.c_void_p),
                    ("cls", wintypes.LPCWSTR), ("key", wintypes.HKEY),
                    ("hotkey", wintypes.DWORD), ("icon", wintypes.HANDLE),
                    ("process", wintypes.HANDLE)]  # fmt: skip

    command = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    info = ShellExecuteInfo()
    info.size = ctypes.sizeof(info)
    info.mask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.verb, info.file = "runas", command
    info.parameters = f'/c copy /y "{source}" "{target}"'
    info.show = 0
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ExtendError("Windows did not let the driver's settings be changed")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.WaitForSingleObject(info.process, 30_000)
    kernel32.CloseHandle(info.process)


def _vdd_reload() -> None:
    """The driver reads its settings again (its pipe: RELOAD_DRIVER)."""
    try:
        with open(VDD_PIPE, "r+b", buffering=0) as pipe:
            pipe.write("RELOAD_DRIVER".encode("utf-16-le"))
    except OSError as exc:
        raise ExtendError(f"the driver did not answer ({exc})") from exc
    time.sleep(1.5)


def _vdd_device() -> str | None:
    """The display device of the driver's monitor (``\\\\.\\DISPLAY7``), on the desktop or not."""
    found = capture._windows_adapters()
    return next((name for name, adapter in found.items() if VDD_NAME in adapter.lower()), None)


def _devmode() -> Any:
    import ctypes
    from ctypes import wintypes

    class DevMode(ctypes.Structure):  # DEVMODEW, the display part
        _fields_ = [("device", wintypes.WCHAR * 32), ("spec", wintypes.WORD),
                    ("driver", wintypes.WORD), ("size", wintypes.WORD),
                    ("extra", wintypes.WORD), ("fields", wintypes.DWORD),
                    ("x", wintypes.LONG), ("y", wintypes.LONG),
                    ("orientation", wintypes.DWORD), ("fixed", wintypes.DWORD),
                    ("color", ctypes.c_short), ("duplex", ctypes.c_short),
                    ("yres", ctypes.c_short), ("tt", ctypes.c_short),
                    ("collate", ctypes.c_short), ("form", wintypes.WCHAR * 32),
                    ("logpixels", wintypes.WORD), ("bits", wintypes.DWORD),
                    ("width", wintypes.DWORD), ("height", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("frequency", wintypes.DWORD),
                    ("icm", wintypes.DWORD * 8)]  # fmt: skip

    mode = DevMode()
    mode.size = ctypes.sizeof(DevMode)
    return mode


def _vdd_attach(size: tuple[int, int]) -> None:
    import ctypes

    device = _vdd_device()
    if device is None:
        raise ExtendError("the Virtual Display Driver is not set up")
    if not vdd_has_resolution(size):
        raise ExtendError("the driver does not know the panel's size yet: set it up again")
    user32 = capture._user32()
    others = [m for m in capture.monitors("windows") if m.id != device]
    right = max((m.x + m.width for m in others), default=0)
    mode = _devmode()
    mode.width, mode.height = size
    mode.x, mode.y = right, 0
    mode.fields = 0x00000020 | 0x00080000 | 0x00100000  # DM_POSITION, PELSWIDTH, PELSHEIGHT
    flags = 0x00000001 | 0x10000000  # CDS_UPDATEREGISTRY | CDS_NORESET
    said = user32.ChangeDisplaySettingsExW(device, ctypes.byref(mode), None, flags, None)
    if said != 0:
        raise ExtendError(f"Windows did not put the panel's monitor on the desktop ({said})")
    user32.ChangeDisplaySettingsExW(None, None, None, 0, None)  # (all of it at once)
    log.info("monitor: the panel's own monitor %s on the desktop, %dx%d", device, *size)


def _vdd_detach() -> None:
    import ctypes

    device = _vdd_device()
    if device is None:
        return
    user32 = capture._user32()
    mode = _devmode()
    mode.fields = 0x00000020 | 0x00080000 | 0x00100000
    flags = 0x00000001 | 0x10000000
    user32.ChangeDisplaySettingsExW(device, ctypes.byref(mode), None, flags, None)
    user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
    log.info("monitor: the panel's own monitor off the desktop")


# -- X11: xrandr ----------------------------------------------------------------------------------


_SCREEN = re.compile(r"current (\d+) x (\d+)")


def _xrandr(*args: str) -> str:
    return capture._run(["xrandr", *args])


def x11_plan(screen: tuple[int, int], monitors: list[capture.Monitor],
             size: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:  # fmt: skip
    """Where the panel's own monitor goes on X11: right of the others, at the top; the
    screen's size it needs. ``(screen size, (x, y))``."""
    real = [m for m in monitors if not m.virtual]
    right = max((m.x + m.width for m in real), default=0)
    bottom = max((m.y + m.height for m in real), default=0)
    width, height = size
    return (right + width, max(bottom, height, screen[1] if not real else 0)), (right, 0)


def _x11_attach(size: tuple[int, int]) -> None:
    _x11_detach()
    said = _xrandr("-q")
    match = _SCREEN.search(said)
    screen = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    (total_w, total_h), (x, y) = x11_plan(screen, capture.monitors("x11"), size)
    width, height = size
    try:
        if total_w > screen[0] or total_h > screen[1]:  # (room beyond the monitors)
            _xrandr("--fb", f"{max(total_w, screen[0])}x{max(total_h, screen[1])}")
        # (its millimetres: about 96 dots an inch, as a desktop monitor)
        _xrandr("--setmonitor", X11_NAME,
                f"{width}/{round(width * 0.2646)}x{height}/{round(height * 0.2646)}+{x}+{y}",
                "none")  # fmt: skip
    except OSError as exc:
        raise ExtendError(f"X11 did not make room for the panel's monitor ({exc})") from exc
    log.info("monitor: the panel's own monitor at %d,%d, %dx%d (X11)", x, y, width, height)


def _x11_detach() -> None:
    said = _xrandr("--listmonitors")
    if X11_NAME not in said:
        return
    _xrandr("--delmonitor", X11_NAME)
    real = [m for m in capture.parse_xrandr(said) if not m.virtual]
    if real:  # the screen as large as the monitors again
        width = max(m.x + m.width for m in real)
        height = max(m.y + m.height for m in real)
        try:
            _xrandr("--fb", f"{width}x{height}")
        except OSError as exc:
            log.info("monitor: the screen stays as large (%s)", exc)

"""A monitor of the panel's own: the computer extends its desktop to it, windows go there as to
any monitor, and the panel shows it (mirror.py, ``virtual``). It is on the desktop while a panel
shows it, and taken off again after, so the mouse never wanders onto a monitor nobody sees.

- **Windows**: a monitor needs a display driver; the Virtual Display Driver (MIT, signed,
  VirtualDrivers/Virtual-Display-Driver) gives one. Setting it up (:func:`setup`) installs the
  driver as the driver's own silent install does: its release's package and NefCon (MIT,
  nefarius/nefcon, which makes the device the driver is for), both downloaded and checked
  against their SHA-256, the driver's signer trusted and the device made, its settings
  (``vdd_settings.xml`` in ``C:\\VirtualDisplayDriver``) with the panel's size among its
  resolutions; all of it in one step with the administrator's rights, so Windows asks once.
  (winget's package of the driver is its control app alone, which installs the driver from its
  window.) The driver reads its settings again when told so through its pipe, which anyone may
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

import base64
import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from libre_panel import __version__, capture

log = logging.getLogger(__name__)

VDD_PIPE = r"\\.\pipe\MTTVirtualDisplayPipe"
VDD_FOLDER = Path(r"C:\VirtualDisplayDriver")
VDD_NAME = "virtual display driver"  # (its adapter's name, lowercase)
VDD_HARDWARE_ID = r"Root\MttVDD"  # the device its INF is for, made by NefCon
_RELEASES = "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/"
# The driver alone, for Windows' own machine (its x64 package is named x86), and its SHA-256
VDD_PACKAGES = {
    "AMD64": (_RELEASES + "VirtualDisplayDriver-x86.Driver.Only.zip",
              "e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a"),
    "ARM64": (_RELEASES + "VirtualDisplayDriver-ARM64.Driver.Only.zip",
              "dcd487161469c726bab60f174a7e7f70ee02afd437cad0d5c0e6755953de5a25"),
}  # fmt: skip
NEFCON = ("https://github.com/nefarius/nefcon/releases/download/v1.21.0/nefcon_v1.21.0.zip",
          "4c8e68f6b663598d77b7c3f76459b16fad00994e53ea8af0bd106a805dd5c18b")  # fmt: skip
NEFCON_FOLDERS = {"AMD64": "x64", "ARM64": "ARM64"}  # (its program for each machine)
DOWNLOAD_LIMIT = 16 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60.0
INSTALL_TIMEOUT_S = 300.0
REBOOT_REQUIRED = 3010  # (ERROR_SUCCESS_REBOOT_REQUIRED)
X11_NAME = "LibrePanel"  # the X11 monitor's name
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
    """Windows: the driver installed and the panel's size among its resolutions; Windows asks
    for the administrator's consent once. What was done, said for the person."""
    if sys.platform != "win32":
        raise ExtendError("only on Windows")
    done = []
    installed = not vdd_installed()
    if installed:
        restart = _vdd_install(size) == REBOOT_REQUIRED
        done.append("driver (Windows wants a restart)" if restart else "driver")
        if not _wait_for(vdd_installed, 20.0):  # (its adapter comes a moment later)
            raise ExtendError("the driver is installed, but Windows shows no display of it "
                              "yet: restart Windows, then set it up here again")  # fmt: skip
    if not vdd_has_resolution(size):
        _vdd_write_resolution(size)
        done.append("resolution")
    try:
        _vdd_reload(tries=5)
    except ExtendError:
        if not installed:
            raise
        log.info("monitor: the new driver read its settings as it started")
    _vdd_detach()  # (the driver puts its monitor on the desktop: off until a panel shows it)
    return ", ".join(done) or "nothing"


def _wait_for(ready: Any, seconds: float, step: float = 0.5) -> bool:
    """Whether ``ready()`` comes true within ``seconds``."""
    end = time.monotonic() + seconds
    while True:
        if ready():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(step)


def windows_machine() -> str:
    """Windows' own machine, "AMD64" or "ARM64" (not the one an emulated program sees)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            process, native = wintypes.USHORT(), wintypes.USHORT()
            kernel32 = ctypes.WinDLL("kernel32")
            if kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(process),
                                        ctypes.byref(native)):  # fmt: skip
                return "ARM64" if native.value == 0xAA64 else "AMD64"
        except (OSError, AttributeError):  # (before Windows 10 1709)
            pass
    return "ARM64" if platform.machine().upper() in ("ARM64", "AARCH64") else "AMD64"


def download(url: str, sha256: str, folder: Path) -> Path:
    """``url`` into ``folder``, if it is the file ``sha256`` names (ExtendError: why not)."""
    target = folder / url.rsplit("/", 1)[-1]
    request = urllib.request.Request(url, headers={"User-Agent": f"libre-panel/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as answer:
            data = answer.read(DOWNLOAD_LIMIT + 1)
    except OSError as exc:
        raise ExtendError(f"{target.name} could not be downloaded ({exc})") from exc
    if len(data) > DOWNLOAD_LIMIT or hashlib.sha256(data).hexdigest() != sha256:
        raise ExtendError(f"{target.name} is not the file it should be")
    target.write_bytes(data)
    return target


def package_settings(package: Path) -> str:
    """The driver's own settings, as its package brings them."""
    with zipfile.ZipFile(package) as archive:
        return archive.read("VirtualDisplayDriver/vdd_settings.xml").decode("utf-8-sig")


def _ps_quote(text: str) -> str:
    """``text`` as a PowerShell string that is taken as it is."""
    return "'" + text.replace("'", "''") + "'"


def install_script(nefcon: Path, nefcon_sha: str, nefcon_program: str, package: Path,
                   package_sha: str, settings: Path, settings_sha: str, target: Path,
                   said: Path) -> str:  # fmt: skip
    """What runs with the administrator's rights to install the driver (PowerShell). The files
    it is given lie where the person's other programs may change them, so it checks each again
    as it reads it and takes what runs or is installed from those bytes, into a folder that
    only administrators may change: the driver's files and NefCon from their packages, the
    settings with the panel's size. It trusts the driver's signer (the certificates of its
    catalog that are no authority, as the driver's own silent install does), writes the
    settings, makes the device and installs the driver on it, and says how it went in ``said``
    (NefCon's exit code, or what failed); its own exit code is NefCon's."""
    q = _ps_quote
    return f"""$ErrorActionPreference = 'Stop'
$work = $null
$code = 1
try {{
    Add-Type -AssemblyName System.IO.Compression
    function Read-Checked([string]$path, [string]$sha) {{
        $bytes = [IO.File]::ReadAllBytes($path)
        $hasher = [Security.Cryptography.SHA256]::Create()
        $hash = [BitConverter]::ToString($hasher.ComputeHash($bytes))
        $hash = $hash.Replace('-', '').ToLowerInvariant()
        if ($hash -ne $sha) {{ throw "$path is not the file that was downloaded" }}
        return ,$bytes
    }}
    function Read-Entry([byte[]]$zip, [string]$name) {{
        $stream = New-Object IO.MemoryStream -ArgumentList @(, $zip)
        $archive = New-Object IO.Compression.ZipArchive -ArgumentList @(, $stream)
        try {{
            $entry = $archive.GetEntry($name)
            if ($null -eq $entry) {{ throw "$name is not in its package" }}
            $out = New-Object IO.MemoryStream
            $in = $entry.Open()
            try {{ $in.CopyTo($out) }} finally {{ $in.Dispose() }}
            [byte[]]$data = $out.ToArray()
            return ,$data
        }} finally {{ $archive.Dispose() }}
    }}
    $nefconZip = Read-Checked {q(str(nefcon))} {q(nefcon_sha)}
    $driverZip = Read-Checked {q(str(package))} {q(package_sha)}
    $settings = Read-Checked {q(str(settings))} {q(settings_sha)}
    $work = Join-Path $env:ProgramData ('LibrePanel-vdd-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    $admins, $system = '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'
    & icacls.exe $work /inheritance:r /grant:r $admins $system | Out-Null
    if ($LASTEXITCODE -ne 0) {{ throw 'the folder for the driver could not be closed to others' }}
    $nefconExe = Join-Path $work 'nefconc.exe'
    [IO.File]::WriteAllBytes($nefconExe, (Read-Entry $nefconZip {q(nefcon_program)}))
    foreach ($name in 'MttVDD.inf', 'MttVDD.dll', 'mttvdd.cat') {{
        $bytes = Read-Entry $driverZip ('VirtualDisplayDriver/' + $name)
        [IO.File]::WriteAllBytes((Join-Path $work $name), $bytes)
    }}
    $x509 = 'System.Security.Cryptography.X509Certificates'
    $certificates = New-Object "$x509.X509Certificate2Collection"
    [byte[]]$catalog = Read-Entry $driverZip 'VirtualDisplayDriver/mttvdd.cat'
    $certificates.Import($catalog)
    $basic = [type]"$x509.X509BasicConstraintsExtension"
    $store = New-Object "$x509.X509Store" -ArgumentList 'TrustedPublisher', 'LocalMachine'
    $store.Open('ReadWrite')
    try {{
        foreach ($certificate in $certificates) {{
            $authority = $false
            foreach ($extension in $certificate.Extensions) {{
                $isAuthority = $extension -is $basic -and $extension.CertificateAuthority
                if ($isAuthority) {{ $authority = $true }}
            }}
            if (-not $authority) {{ $store.Add($certificate) }}
        }}
    }} finally {{ $store.Close() }}
    $target = {q(str(target))}
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $target 'vdd_settings.xml'), $settings)
    $inf = Join-Path $work 'MttVDD.inf'
    & $nefconExe install $inf {q(VDD_HARDWARE_ID)} --no-duplicates --remove-duplicates | Out-Null
    $code = $LASTEXITCODE
    [IO.File]::WriteAllText({q(str(said))}, [string]$code)
}} catch {{
    $code = 1
    $line = $_.InvocationInfo.ScriptLineNumber  # (where: for whoever looks into it)
    [IO.File]::WriteAllText({q(str(said))}, "error: $($_.Exception.Message) (line $line)")
}} finally {{
    if ($work) {{ Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }}
}}
exit $code
"""


def powershell(script: str) -> tuple[str, str]:
    """Windows' own PowerShell and its parameters to run ``script``: given encoded, so that
    nothing on the way reads it as anything else, and without a window."""
    program = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                           "WindowsPowerShell", "v1.0", "powershell.exe")  # fmt: skip
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return program, ("-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden "
                     f"-EncodedCommand {encoded}")  # fmt: skip


def _vdd_install(size: tuple[int, int]) -> int:
    """The driver installed, with the panel's size among its resolutions (one question of
    Windows): NefCon's exit code, 0 or :data:`REBOOT_REQUIRED` (ExtendError: why not)."""
    machine = windows_machine()
    with tempfile.TemporaryDirectory(prefix="libre-panel-vdd-") as scratch:
        folder = Path(scratch)
        package = download(*VDD_PACKAGES[machine], folder)
        nefcon = download(*NEFCON, folder)
        try:  # (settings that are there already are kept, with the panel's size)
            current = (vdd_folder() / "vdd_settings.xml").read_text(encoding="utf-8-sig")
        except OSError:
            current = package_settings(package)
        settings = folder / "vdd_settings.xml"
        settings.write_bytes(with_resolution(current, size).encode("utf-8"))
        said = folder / "said.txt"
        script = install_script(
            nefcon, NEFCON[1], f"{NEFCON_FOLDERS[machine]}/nefconc.exe", package,
            VDD_PACKAGES[machine][1], settings, hashlib.sha256(settings.read_bytes()).hexdigest(),
            vdd_folder(), said,
        )  # fmt: skip
        code = _run_elevated(*powershell(script), INSTALL_TIMEOUT_S)
        try:
            told = said.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            told = ""
    if code in (0, REBOOT_REQUIRED):
        log.info("monitor: the Virtual Display Driver installed (%s)", code)
        return code
    reason = told[len("error: ") :] if told.startswith("error: ") else f"NefCon said {code}"
    raise ExtendError(f"the driver was not installed ({reason})")


_setting_up = threading.Lock()
_setup: dict[str, Any] = {"running": False, "said": "", "error": ""}


def start_setup(size: tuple[int, int]) -> None:
    """:func:`setup` in the background (the downloads and the driver take a while):
    :func:`setup_state` says how it goes. Nothing new while one runs."""
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
        text = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError:  # (a driver installed without them: its own, from its package)
        with tempfile.TemporaryDirectory(prefix="libre-panel-vdd-") as scratch:
            text = package_settings(download(*VDD_PACKAGES[windows_machine()], Path(scratch)))
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
    """``source`` over ``target`` as the administrator (Windows asks): the driver's settings,
    its folder made if it is not there."""
    command = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    folder = target.parent
    _run_elevated(command, f'/c (if not exist "{folder}" mkdir "{folder}") & '
                           f'copy /y "{source}" "{target}"', 30.0)  # fmt: skip


def _run_elevated(program: str, parameters: str, timeout: float) -> int:
    """``program`` with ``parameters`` as the administrator, Windows asking the person first;
    its exit code once it has ended (ExtendError: refused, or it did not end in time)."""
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

    info = ShellExecuteInfo()
    info.size = ctypes.sizeof(info)
    info.mask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.verb, info.file, info.parameters = "runas", program, parameters
    info.show = 0
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        refused = ctypes.get_last_error() == 1223  # ERROR_CANCELLED: the person said no
        raise ExtendError("Windows was not given the permission" if refused
                          else "Windows did not start the setup")  # fmt: skip
    kernel32 = ctypes.WinDLL("kernel32")
    try:
        if kernel32.WaitForSingleObject(info.process, round(timeout * 1000)) != 0:
            raise ExtendError("the setup did not end in time")
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.process, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.process)


def _vdd_reload(tries: int = 1) -> None:
    """The driver reads its settings again (its pipe: RELOAD_DRIVER); a driver that has just
    started opens its pipe a moment later, so it is tried ``tries`` times, a second apart."""
    for attempt in range(tries):
        try:
            with open(VDD_PIPE, "r+b", buffering=0) as pipe:
                pipe.write("RELOAD_DRIVER".encode("utf-16-le"))
            break
        except OSError as exc:
            if attempt + 1 >= tries:
                raise ExtendError(f"the driver did not answer ({exc})") from exc
            time.sleep(1.0)
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

# PyInstaller build of the release folder:  pyinstaller packaging/pyinstaller/libre-panel.spec
#
# libre-panel(.exe)   the command-line program (all commands)
# LibrePanel.exe      Windows: the tray app without a console window
import importlib.util
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

HERE = Path(SPECPATH)  # noqa: F821 - provided by PyInstaller
ROOT = HERE.parent.parent

datas = collect_data_files("libre_panel") + copy_metadata("libre-panel")
binaries = []
# Sensor sources and drivers are loaded by name (entry points), which the
# import analysis cannot see.
hiddenimports = collect_submodules("libre_panel")
# Folder plugins (docs/PLUGINS.md) run on this bundled Python: include the
# standard library parts plugins commonly need, even where Libre Panel does not.
hiddenimports += [
    "asyncio",
    "concurrent.futures",
    "csv",
    "ctypes.wintypes",
    "gzip",
    "http.client",
    "sqlite3",
    "statistics",
    "urllib.request",
    "uuid",
    "xml.etree.ElementTree",
    "zipfile",
]
if sys.platform == "win32":
    hiddenimports += ["winreg", "zoneinfo", "tzdata"]
    datas += collect_data_files("tzdata")  # the world clock's time zones
    # what is playing: the media controls through the winrt packages, loaded on demand
    for package in ("winrt",):
        try:
            winrt_datas, winrt_binaries, winrt_hidden = collect_all(package)
        except Exception:  # not installed: the music module then says nothing plays
            continue
        datas += winrt_datas
        binaries += winrt_binaries
        hiddenimports += winrt_hidden
if sys.platform == "win32":
    # LibreHardwareMonitor inside Libre Panel (docs/CONFIGURATION.md): pythonnet starts the
    # .NET Framework (clr_loader brings the native part), and the .NET libraries are what
    # packaging/windows/fetch_lhm.py put in packaging/windows/lhm-libs, bundled as "lhm".
    # Without them (a local build that did not fetch) Libre Panel reads the web server only.
    for package in ("pythonnet", "clr_loader"):
        try:
            lhm_datas, lhm_binaries, lhm_hidden = collect_all(package)
        except Exception:  # not installed: pip install ".[lhm]"
            continue
        datas += lhm_datas
        binaries += lhm_binaries
        hiddenimports += lhm_hidden
        hiddenimports += ["clr", "_cffi_backend"] if package == "pythonnet" else []
    lhm_libs = ROOT / "packaging" / "windows" / "lhm-libs"
    if (lhm_libs / "LibreHardwareMonitorLib.dll").is_file():
        datas.append((str(lhm_libs), "lhm"))  # data, not binaries: they are .NET, not DLLs to scan
if sys.platform == "win32":
    # The app's window (libre_panel.window): pywebview's WinForms backend shows WebView2
    # through pythonnet, on the .NET Framework that LibreHardwareMonitor starts as well
    # (pythonnet and clr_loader are bundled above). pywebview's own PyInstaller hook brings
    # its WebView2 libraries and scripts; the backend is imported by name when the window
    # opens. Without pywebview (pip install ".[window]") the app opens in the browser.
    if importlib.util.find_spec("webview") is not None:
        hiddenimports += [
            "webview.platforms.winforms",
            "webview.platforms.edgechromium",
            "webview.platforms.win32",
            "webview.platforms.mshtml",
        ]
hiddenimports += ["paho.mqtt.client"]  # MQTT readings, imported when configured
usb_datas, usb_binaries, usb_hidden = collect_all("libusb_package")
datas += usb_datas
binaries += usb_binaries
hiddenimports += usb_hidden
# pystray picks its backend at runtime; bundle the one for this system.
if sys.platform == "win32":
    hiddenimports += ["pystray._win32"]
else:
    hiddenimports += ["pystray._xorg"]
icon = str(ROOT / "packaging" / "icons" / "libre-panel.ico") if sys.platform == "win32" else None


def program(script, name, console):
    analysis = Analysis(  # noqa: F821
        [str(HERE / script)], datas=datas, binaries=binaries, hiddenimports=hiddenimports
    )
    exe = EXE(  # noqa: F821
        PYZ(analysis.pure),  # noqa: F821
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=icon,
    )
    return [exe, analysis.binaries, analysis.datas]


parts = program("libre_panel_app.py", "libre-panel", console=True)
if sys.platform == "win32":
    parts += program("libre_panel_tray.py", "LibrePanel", console=False)

COLLECT(*parts, name="libre-panel")  # noqa: F821

# ffmpeg (all downloads), PresentMon and PawnIO's installer (Windows), which
# packaging/fetch_tools.py put in packaging/bundled-tools, go next to the program as "tools":
# the folder libre_panel.bundled looks in, and where a person who wants to swap one finds it.
# Without them (a local build that did not fetch) the video mode and the game frame rates use
# what the system has.
tools = ROOT / "packaging" / "bundled-tools"
if (tools / "SOURCES.txt").is_file():
    from libre_panel.bundled import TOOLS_FOLDER

    program_folder = Path(DISTPATH) / "libre-panel"  # noqa: F821 - provided by PyInstaller
    shutil.copytree(tools, program_folder / TOOLS_FOLDER, dirs_exist_ok=True)

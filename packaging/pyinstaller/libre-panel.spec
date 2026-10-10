# PyInstaller build of the release folder:  pyinstaller packaging/pyinstaller/libre-panel.spec
#
# libre-panel(.exe)   the command-line program (all commands)
# LibrePanel(.exe)    Windows: the tray app without a console window
#                     macOS: the main program of "Libre Panel.app" (menu bar only)
import sys
from importlib.metadata import version
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
hiddenimports += ["paho.mqtt.client"]  # MQTT readings, imported when configured
usb_datas, usb_binaries, usb_hidden = collect_all("libusb_package")
datas += usb_datas
binaries += usb_binaries
hiddenimports += usb_hidden
# pystray picks its backend at runtime; bundle the one for this system.
if sys.platform == "win32":
    hiddenimports += ["pystray._win32"]
elif sys.platform == "darwin":
    hiddenimports += ["pystray._darwin", "PyObjCTools.AppHelper"]
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


if sys.platform == "darwin":
    # The app's main program must come first: it is the one macOS starts.
    parts = program("libre_panel_tray.py", "LibrePanel", console=False)
    parts += program("libre_panel_app.py", "libre-panel", console=True)
else:
    parts = program("libre_panel_app.py", "libre-panel", console=True)
    if sys.platform == "win32":
        parts += program("libre_panel_tray.py", "LibrePanel", console=False)

folder = COLLECT(*parts, name="libre-panel")  # noqa: F821

if sys.platform == "darwin":
    BUNDLE(  # noqa: F821
        folder,
        name="Libre Panel.app",
        icon=str(ROOT / "packaging" / "icons" / "libre-panel.icns"),
        bundle_identifier="io.github.syntensa.libre-panel",
        version=version("libre-panel"),
        info_plist={
            "CFBundleDisplayName": "Libre Panel",
            "LSUIElement": True,  # lives in the menu bar, no Dock icon
            "NSHighResolutionCapable": True,
            "CFBundleLocalizations": ["en", "de"],
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "Libre Panel contributors, GPL-3.0-or-later",
        },
    )

# PyInstaller build of the release folder:  pyinstaller packaging/pyinstaller/libre-panel.spec
#
# libre-panel(.exe)   the command-line program (all commands)
# LibrePanel.exe      Windows: the tray app without a console window
# sensors\libre-panel-sensors.exe   Windows: the sensor service, a program of its own
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
if sys.platform.startswith("linux"):
    # The window of the AppImage and the archive runs in the system's Python, which has GTK
    # (window.py, gtk_window.py): Libre Panel's sources for it, with its texts and icon, in a
    # folder of their own (pysrc), where no package of the build lies half (compiled, without
    # its Python) for that Python to take
    SOURCE = ROOT / "src" / "libre_panel"
    for path in sorted(SOURCE.rglob("*")):
        if path.is_file() and (
            path.suffix == ".py"
            or path.parent.name == "locale"
            or path.relative_to(SOURCE).as_posix() == "editor/static/icon.png"
        ):
            datas.append((str(path), str(Path("pysrc/libre_panel") / path.parent.relative_to(SOURCE))))
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

if sys.platform == "win32":
    # The sensor service (libre_panel/sensorservice) runs as LocalSystem, so it is a program of
    # its own with only what it needs: Python, pythonnet and LibreHardwareMonitor's libraries,
    # no plugins, no renderer, no GUI. It goes into the folder "sensors" of the program's
    # folder, which libre-panel system-setup copies to Program Files for the portable zip.
    service_datas, service_binaries = [], []
    service_hidden = ["libre_panel.sensorservice.winservice", "winreg"]
    for package in ("pythonnet", "clr_loader"):
        try:
            found_datas, found_binaries, found_hidden = collect_all(package)
        except Exception:  # not installed: pip install ".[lhm]"
            continue
        service_datas += found_datas
        service_binaries += found_binaries
        service_hidden += found_hidden
        service_hidden += ["clr", "_cffi_backend"] if package == "pythonnet" else []
    if (lhm_libs / "LibreHardwareMonitorLib.dll").is_file():
        service_datas.append((str(lhm_libs), "lhm"))
    service = Analysis(  # noqa: F821
        [str(HERE / "libre_panel_sensors.py")],
        datas=service_datas,
        binaries=service_binaries,
        hiddenimports=service_hidden,
        # what the service never imports (a function of a module it uses may, for Libre Panel)
        excludes=[
            "libre_panel.plugins", "libre_panel.render", "libre_panel.editor",
            "libre_panel.theme", "libre_panel.devices", "libre_panel.services",
            "libre_panel.pages", "libre_panel.weather", "PIL", "numpy", "tkinter", "webview",
            "winrt", "pystray", "usb", "libusb_package", "serial", "paho",
        ],  # fmt: skip
    )
    service_exe = EXE(  # noqa: F821
        PYZ(service.pure),  # noqa: F821
        service.scripts,
        [],
        exclude_binaries=True,
        name="libre-panel-sensors",
        console=True,  # a service has no window; errors go to its log, not to a dialog
        icon=icon,
    )
    COLLECT(service_exe, service.binaries, service.datas, name="libre-panel-sensors")  # noqa: F821
    # COLLECT builds into DISTPATH only: move it into the program's folder
    built = Path(DISTPATH) / "libre-panel-sensors"  # noqa: F821
    target = Path(DISTPATH) / "libre-panel" / "sensors"  # noqa: F821
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(built), str(target))

    # .NET loads the programs' libraries also where Windows marked them as downloaded (a zip
    # unpacked by Explorer) and the mark cannot be taken off (libre_panel.unblock)
    from libre_panel.unblock import write_netfx_configs

    write_netfx_configs(Path(DISTPATH) / "libre-panel")  # noqa: F821

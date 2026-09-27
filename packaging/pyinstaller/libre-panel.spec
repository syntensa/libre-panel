# PyInstaller build of the release folder:  pyinstaller packaging/pyinstaller/libre-panel.spec
#
# libre-panel(.exe)   the command-line program (all commands)
# LibrePanel.exe      Windows only: the tray app without a console window
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
usb_datas, usb_binaries, usb_hidden = collect_all("libusb_package")
datas += usb_datas
binaries += usb_binaries
hiddenimports += usb_hidden
# pystray picks its backend at runtime; bundle the one for this system.
if sys.platform == "win32":
    hiddenimports += ["pystray._win32"]
elif sys.platform == "darwin":
    hiddenimports += ["pystray._darwin"]
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

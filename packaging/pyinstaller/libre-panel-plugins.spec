# PyInstaller build of the plugin host of the Rust app's downloads (docs/RUST.md, R10):
#   pyinstaller packaging/pyinstaller/libre-panel-plugins.spec
#
# plugin-host/libre-panel-plugins(.exe)   the Python plugins' program, which the Rust app starts
#                                         only where plugin folders are installed
#
# Plugins import what they need from libre_panel.plugins and draw with Pillow: Libre Panel's
# Python package goes in whole (its own parts are imported by name), with the standard library
# parts plugins commonly need, as the Python downloads carried them.
import re
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

HERE = Path(SPECPATH)  # noqa: F821 - provided by PyInstaller
ROOT = HERE.parent.parent

datas = collect_data_files("libre_panel") + copy_metadata("libre-panel")
hiddenimports = collect_submodules("libre_panel")
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
    datas += collect_data_files("tzdata")


def version_resource():
    """What the Windows program says of itself, as the other programs of the download do."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32 import versioninfo

    text = version("libre-panel")
    found = re.findall(r"\d+", text)[: 4 if re.fullmatch(r"[\d.]+", text) else 3]
    numbers = tuple(int(n) for n in (found + ["0"] * 4)[:4])
    strings = [
        ("CompanyName", "Libre Panel contributors"),
        ("FileDescription", "Libre Panel plugin host"),
        ("FileVersion", text),
        ("InternalName", "libre-panel-plugins"),
        ("LegalCopyright", "Libre Panel contributors, GPL-3.0-or-later"),
        ("OriginalFilename", "libre-panel-plugins.exe"),
        ("ProductName", "Libre Panel"),
        ("ProductVersion", text),
    ]
    return versioninfo.VSVersionInfo(
        ffi=versioninfo.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            versioninfo.StringFileInfo([versioninfo.StringTable(
                "040904B0", [versioninfo.StringStruct(key, value) for key, value in strings]
            )]),
            versioninfo.VarFileInfo([versioninfo.VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


analysis = Analysis(  # noqa: F821
    [str(HERE / "libre_panel_plugins.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    optimize=0,  # (plugins may use assert and docstrings)
)
pyz = PYZ(analysis.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="libre-panel-plugins",
    console=True,  # (its standard input and output are the app's way to it; no window shows)
    icon=str(ROOT / "packaging" / "icons" / "libre-panel.ico") if sys.platform == "win32" else None,
    version=version_resource(),
)
COLLECT(exe, analysis.binaries, analysis.datas, name="plugin-host")  # noqa: F821

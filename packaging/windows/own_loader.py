"""Whether PyInstaller's loader in this Python was compiled here, not the one PyInstaller ships
ready-made (release.yml compiles it from PyInstaller's source on the runner).

Every program of the Windows download starts with this loader. The one PyInstaller ships is the
same file in countless programs, malware among them, and antivirus programs that judge by
likeness took Libre Panel's programs for a trojan (Defender's ``Trojan:Win32/Bearfoos.A!ml``,
0.7.10). Run with the wheel PyInstaller ships for this version, it fails where a loader would
be that wheel's:

    python packaging/windows/own_loader.py pyinstaller-X-py3-none-win_amd64.whl
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

LOADERS = ("run.exe", "runw.exe")  # (libre-panel.exe and the sensor service; LibrePanel.exe)
FOLDER = "PyInstaller/bootloader/Windows-64bit-intel"


def main(wheel: str) -> int:
    import PyInstaller

    here = Path(PyInstaller.__file__).parent / "bootloader" / "Windows-64bit-intel"
    with zipfile.ZipFile(wheel) as shipped:
        for name in LOADERS:
            ours = here / name
            if not ours.is_file():
                print(f"error: no {name} in {here}")
                return 1
            mine = hashlib.sha256(ours.read_bytes()).hexdigest()
            theirs = hashlib.sha256(shipped.read(f"{FOLDER}/{name}")).hexdigest()
            if mine == theirs:
                print(f"error: {name} is the loader PyInstaller ships (sha256 {mine})")
                return 1
            print(f"{name}: compiled here, sha256 {mine} (the shipped one: {theirs})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

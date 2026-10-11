"""The programs that come with a download, in a ``tools`` folder next to Libre Panel.

The Windows setup and zip carry ffmpeg (the video mode), PresentMon (game frame rates) and
PawnIO's installer (the CPU's sensors); the Linux AppImage and tarball carry ffmpeg.
``packaging/fetch_tools.py`` fetches them when a download is built, ``tools/SOURCES.txt`` and
NOTICE.md say where they come from. A pip install has no such folder: it uses what the
system has, and ``video.ffmpeg`` or ``[sensors.presentmon] path`` name the rest.

Only a release build looks here, and only beside its own program: whoever may write there
may replace Libre Panel itself. Never the user's Downloads.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_FOLDER = "tools"  # in the downloads, next to the program's own files


def tools_folders() -> list[Path]:
    """The folders a download keeps its programs in, the one beside the program first. None
    for a pip install or a checkout, which carry no programs."""
    if not getattr(sys, "frozen", False):
        return []
    folders = [Path(sys.executable).resolve().parent / TOOLS_FOLDER]
    bundle = getattr(sys, "_MEIPASS", None)  # PyInstaller's own folder (_internal)
    if bundle and (inside := Path(bundle) / TOOLS_FOLDER) not in folders:
        folders.append(inside)
    return folders


def program_name(name: str) -> str:
    """The file name of a program here: ``ffmpeg`` is ``ffmpeg.exe`` on Windows."""
    return f"{name}.exe" if sys.platform == "win32" else name


def find(file_name: str) -> str | None:
    """The path of the file ``file_name`` that the download carries, or None."""
    for folder in tools_folders():
        path = folder / file_name
        if path.is_file():
            return str(path)
    return None

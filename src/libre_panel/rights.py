"""Running with administrator rights (Windows) or as root: what Libre Panel then holds back.

Started with the highest rights (the elevated start of autostart.py, or "Run as
administrator"), Libre Panel would hand them to whatever it loads or starts from a place that
programs without those rights can change. On Windows every program the same user runs may
write to the user's profile, where config.toml and the plugin folders lie; one of them could
name its own program as ``video.ffmpeg`` or put a plugin there, and get the rights at the
next start. So, while it has them, Libre Panel loads no plugin folders from such a place and
starts a program (ffmpeg, PresentMon, an AI agent's command line) only from a folder that only
administrators may change:

- Windows: Program Files and Windows (as the registry of the machine names them), and the
  folder of the download itself: whoever may write there may replace Libre Panel anyway
  (autostart.writable_by_user warns about that when the elevated start is set up);
- elsewhere, as root: a file or folder that, with every folder above it, belongs to root and
  that nobody else may write to (a folder anyone may write to with the sticky bit, as /tmp,
  keeps what is root's in it root's).

Without those rights nothing is held back. The sensor service reads the sensors that need the
rights (sensorservice/), so Libre Panel itself seldom needs them.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Where Windows keeps its own folders and the programs installed for every user. Read from the
# registry of the machine: the environment of the process is the user's, who may change it.
_WINDOWS_FOLDERS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion", "ProgramFilesDir"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion", "ProgramFilesDir (x86)"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion", "ProgramW6432Dir"),
    (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "SystemRoot"),
)


def elevated() -> bool:
    """Whether this program runs with administrator rights (Windows) or as root (elsewhere)."""
    if sys.platform == "win32":
        from libre_panel.autostart import is_admin

        return is_admin()
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def protected_folders() -> list[Path]:
    """Windows: the folders only administrators may change, Libre Panel's own among them when
    it is a download (see the module's text)."""
    folders: list[Path] = []
    try:
        import winreg
    except ImportError:
        winreg = None  # type: ignore[assignment]
    for key, name in _WINDOWS_FOLDERS if winreg is not None else ():
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as opened:
                value, _kind = winreg.QueryValueEx(opened, name)
        except OSError:
            continue
        if isinstance(value, str) and value:
            folders.append(Path(value))
    if getattr(sys, "frozen", False):
        from libre_panel import bundled

        folders.append(Path(sys.executable).resolve().parent)
        folders.extend(bundled.tools_folders())
    return folders


def _root_only(path: Path, owners: tuple[int, ...] = (0,)) -> bool:
    """Elsewhere: whether ``path`` and every folder above it belong to root (``owners``) and
    nobody else may write to them (but where the sticky bit keeps what is root's root's)."""
    try:
        for part in (path, *path.parents):
            info = part.stat()
            if info.st_uid not in owners:
                return False
            if info.st_mode & 0o022 and not (part != path and info.st_mode & stat.S_ISVTX):
                return False
    except OSError:
        return False
    return True


def _inside(path: Path, folders: list[Path]) -> bool:
    for folder in folders:
        try:
            if path.is_relative_to(folder.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def safe_place(path: str | Path) -> bool:
    """Whether a program or a folder at ``path`` may be used with the rights Libre Panel has:
    always without the highest rights; with them only where nobody without them can change it."""
    if not elevated():
        return True
    return _safe(path, sys.platform == "win32")


def _safe(path: str | Path, windows: bool, folders: list[Path] | None = None,
          owners: tuple[int, ...] = (0,)) -> bool:  # fmt: skip
    """:func:`safe_place` with the highest rights: on Windows inside ``folders`` (the
    protected ones), elsewhere belonging to ``owners`` (root) all the way up."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    if windows:
        return _inside(resolved, protected_folders() if folders is None else folders)
    return _root_only(resolved, owners)


def which(name: str) -> str | None:
    """``shutil.which``, but with the highest rights only a program from a safe place: the
    first one on PATH that is."""
    if not elevated():
        return shutil.which(name)
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        found = shutil.which(name, path=folder)
        if found and safe_place(found):
            return found
        if found:
            log.warning("%s in %s is not started: Libre Panel runs with the highest rights",
                        name, folder)  # fmt: skip
    return None

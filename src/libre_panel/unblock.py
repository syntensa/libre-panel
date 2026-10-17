"""The mark Windows puts on what comes from the internet, taken off Libre Panel's own files.

Windows marks a download as coming from the internet (an alternate data stream named
``Zone.Identifier``), and Explorer passes the mark on to every file it unpacks from a marked
zip. The .NET Framework will not load a library that carries it: pythonnet then cannot start
("Failed to resolve Python.Runtime.Loader.Initialize"), so the app's window, LibreHardwareMonitor
and the sensor service fail, all of them, in the portable download. Copies keep the mark too:
the sensor service that the setup copies to Program Files from the portable folder has it.

So the release build takes the mark off its own folder when it starts (what "Unblock" in a
file's properties does), and the setup off the copies it makes. Only the program's own files,
and only where the mark is: anything else is left as it is. Nothing happens elsewhere than on
Windows, or when Libre Panel runs from its sources.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

STREAM = "Zone.Identifier"
# a file of each program that .NET loads: where it has the mark, the folder has it
SENTINELS = (
    Path("_internal") / "pythonnet" / "runtime" / "Python.Runtime.dll",
    Path("_internal") / "clr_loader" / "ffi" / "dlls" / "amd64" / "ClrLoader.dll",
)


# Next to each program of the Windows build as "<program>.exe.config": the .NET Framework loads
# its libraries also where the mark could not be taken off (a folder Libre Panel may not change)
NETFX_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <runtime>
    <loadFromRemoteSources enabled="true" />
  </runtime>
</configuration>
"""
PROGRAMS = ("libre-panel.exe", "LibrePanel.exe", "sensors/libre-panel-sensors.exe")


def write_netfx_configs(folder: Path) -> list[Path]:
    """The ``.exe.config`` of each program of the build in ``folder`` (packaging); those made."""
    made = []
    for program in PROGRAMS:
        exe = folder / program
        if exe.is_file():
            config = exe.with_name(exe.name + ".config")
            config.write_text(NETFX_CONFIG, encoding="utf-8")
            made.append(config)
    return made


def marked(path: Path) -> bool:
    """Whether ``path`` carries the mark of the internet."""
    try:
        with open(f"{path}:{STREAM}", "rb"):
            return True
    except OSError:
        return False


def unblock_folder(folder: Path) -> int:
    """Take the mark off every file in ``folder`` that has it; how many there were. A file
    whose mark cannot be taken off (no right to change it) is left as it is."""
    taken = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                os.remove(os.path.join(root, name) + f":{STREAM}")
            except FileNotFoundError:
                continue  # (no mark: nearly every file)
            except OSError as exc:
                log.debug("the mark stays on %s: %s", name, exc)
                continue
            taken += 1
    return taken


def unblock_program(folder: Path | None = None, platform: str = sys.platform) -> int:
    """At the start of a release build on Windows: the mark off the program's folder, if it has
    it (one look at the files .NET loads, so a start without it costs nothing)."""
    if platform != "win32" or (folder is None and not getattr(sys, "frozen", False)):
        return 0
    folder = folder or Path(sys.executable).parent
    if not any(marked(folder / sentinel) for sentinel in SENTINELS):
        return 0
    taken = unblock_folder(folder)
    log.info("took the download mark off %d files in %s (else .NET would not load them)",
             taken, folder)  # fmt: skip
    return taken

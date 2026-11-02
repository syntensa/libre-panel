"""A display that writes frames to a PNG file. No hardware needed.

A relative ``device.output`` is inside the settings folder: a program started
by autostart or a double-click has no meaningful working directory (the system
folder on Windows).

With the highest rights (rights.py) the file is written only into a folder that nobody without
them can change (as root on Linux: root's own settings folder): config.toml, which names it,
and the settings folder of a Windows user are the user's own, which every program the user
runs may change (a link in the folder too), and the frame would be written with the rights
over whatever file they lead to. Elsewhere the frames are kept as without a file (the app's
live picture shows them).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PIL import Image

from libre_panel import rights
from libre_panel.config import config_dir
from libre_panel.devices.base import DeviceError, Display
from libre_panel.i18n import t

log = logging.getLogger(__name__)


def output_path(output: str | None) -> Path | None:
    if not output:
        return None
    path = Path(output).expanduser()
    return path if path.is_absolute() else config_dir() / path


def may_write(path: Path) -> bool:
    """Whether the frame may be written to ``path`` with the rights Libre Panel has: always
    without the highest rights; with them only where the folder (the nearest that is there)
    is a safe place (rights.safe_place)."""
    folder = path.parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return rights.safe_place(folder)


class VirtualDisplay(Display):
    name = "virtual"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.output = output_path(config.output)
        if self.output is not None and not may_write(self.output):
            log.warning("%s is not written: Libre Panel runs with the highest rights", self.output)
            self.output = None
        self.frame: Image.Image | None = None
        self.frames_shown = 0
        self.last_region: tuple[int, int, int, int] | None = None
        self.brightness = config.brightness

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, percent))

    def describe(self) -> str:
        return t("PNG file {path}", path=self.output) if self.output else t("virtual display")

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        self.frame = frame.copy()
        self.frames_shown += 1
        self.last_region = region
        if self.output is not None:
            tmp = self.output.with_name(self.output.name + ".tmp")
            try:
                self.output.parent.mkdir(parents=True, exist_ok=True)
                frame.save(tmp, format="PNG")
            except OSError as exc:  # e.g. no permission: retried like a missing panel
                raise DeviceError(
                    t("cannot write {path}: {error}", path=self.output, error=exc)
                ) from exc
            # Atomic, so viewers never see half a file. On Windows a viewer that
            # holds the file open blocks the rename; skip that frame instead of dying.
            for attempt in range(3):
                try:
                    os.replace(tmp, self.output)
                    break
                except PermissionError:
                    time.sleep(0.02 * (attempt + 1))

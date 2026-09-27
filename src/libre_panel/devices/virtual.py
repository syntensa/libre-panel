"""A display that writes frames to a PNG file. No hardware needed.

A relative ``device.output`` is inside the settings folder: a program started
by autostart or a double-click has no meaningful working directory (``/`` on
macOS, the system folder on Windows).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PIL import Image

from libre_panel.config import config_dir
from libre_panel.devices.base import DeviceError, Display
from libre_panel.i18n import t


def output_path(output: str | None) -> Path | None:
    if not output:
        return None
    path = Path(output).expanduser()
    return path if path.is_absolute() else config_dir() / path


class VirtualDisplay(Display):
    name = "virtual"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.output = output_path(config.output)
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

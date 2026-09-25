"""A display that writes frames to a PNG file. No hardware needed."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PIL import Image

from libre_panel.devices.base import Display


class VirtualDisplay(Display):
    name = "virtual"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.output = Path(config.output) if config.output else None
        self.frame: Image.Image | None = None
        self.frames_shown = 0
        self.last_region: tuple[int, int, int, int] | None = None
        self.brightness = config.brightness

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, percent))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        self.frame = frame.copy()
        self.frames_shown += 1
        self.last_region = region
        if self.output is not None:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.output.with_name(self.output.name + ".tmp")
            frame.save(tmp, format="PNG")
            # Atomic, so viewers never see half a file. On Windows a viewer that
            # holds the file open blocks the rename; skip that frame instead of dying.
            for attempt in range(3):
                try:
                    os.replace(tmp, self.output)
                    break
                except PermissionError:
                    time.sleep(0.02 * (attempt + 1))

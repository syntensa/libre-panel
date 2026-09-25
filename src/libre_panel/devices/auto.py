"""Default driver: use a connected panel, otherwise render to a PNG file."""

from __future__ import annotations

import logging

from PIL import Image

from libre_panel.devices.base import Display
from libre_panel.devices.turzx import TurzxDisplay
from libre_panel.devices.virtual import VirtualDisplay

log = logging.getLogger(__name__)


class AutoDisplay(Display):
    name = "auto"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._impl: Display | None = None

    def open(self) -> None:
        panel = TurzxDisplay(self.config)
        try:
            panel.open()
            self._impl = panel
        except Exception as exc:  # no panel, missing USB libraries, no permission, ...
            log.info("no panel in use (%s); rendering to %s instead", exc, self.config.output)
            self._impl = VirtualDisplay(self.config)
            self._impl.open()

    def set_brightness(self, percent: int) -> None:
        if self._impl:
            self._impl.set_brightness(percent)

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self._impl is None:
            self.open()
        self._impl.show(frame, region)

    def close(self) -> None:
        if self._impl is not None:
            self._impl.close()
            self._impl = None

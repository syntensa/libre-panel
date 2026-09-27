"""Default driver: use a connected panel, otherwise render to a PNG file.

While it writes PNG files it keeps looking for a panel, so a panel that is
plugged in later (or that the system enumerates after autostart) is picked
up without a restart.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PIL import Image

from libre_panel.devices.base import Display
from libre_panel.devices.turzx import TurzxDisplay
from libre_panel.devices.virtual import VirtualDisplay

log = logging.getLogger(__name__)


class AutoDisplay(Display):
    name = "auto"
    PROBE_INTERVAL_S = 5.0

    def __init__(self, config, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(config)
        self._impl: Display | None = None
        self._clock = clock
        self._next_probe = 0.0
        self._brightness: int | None = None
        self._last_error: Exception | None = None

    @property
    def using_panel(self) -> bool:
        return self._impl is not None and not isinstance(self._impl, VirtualDisplay)

    def _try_panel(self) -> Display | None:
        self._next_probe = self._clock() + self.PROBE_INTERVAL_S
        panel = TurzxDisplay(self.config)
        try:
            panel.open()
            if self._brightness is not None:
                panel.set_brightness(self._brightness)
        except Exception as exc:  # no panel, missing USB libraries, no permission, ...
            panel.close()
            self._last_error = exc
            return None
        return panel

    def open(self) -> None:
        panel = self._try_panel()
        if panel is not None:
            self._impl = panel
            return
        log.info(
            "no panel in use (%s); rendering to %s and looking for a panel every %d s",
            self._last_error,
            self.config.output,
            self.PROBE_INTERVAL_S,
        )
        self._impl = VirtualDisplay(self.config)
        self._impl.open()

    def set_brightness(self, percent: int) -> None:
        self._brightness = percent
        if self._impl:
            self._impl.set_brightness(percent)

    def describe(self) -> str:
        return self._impl.describe() if self._impl else "no panel yet"

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self._impl is None:
            self.open()
        elif not self.using_panel and self._clock() >= self._next_probe:
            panel = self._try_panel()
            if panel is not None:
                log.info("panel found: %s", panel.describe())
                self._impl.close()
                self._impl, region = panel, None  # the panel needs a full frame first
        self._impl.show(frame, region)

    def close(self) -> None:
        if self._impl is not None:
            self._impl.close()
            self._impl = None

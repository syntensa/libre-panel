"""Default driver: use a connected panel, otherwise render to a PNG file.

While it writes PNG files it keeps looking for a panel, so a panel that is
plugged in later (or that the system enumerates after autostart) is picked
up without a restart. A panel that was in use and is gone (unplugged) is not
replaced by the file: opening fails, and the panel loop waits for it as for
any panel (#13: the app said the panel was connected while it drew into the
file).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from PIL import Image

from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import PanelModel
from libre_panel.devices.turzx import TurzxDisplay
from libre_panel.devices.virtual import VirtualDisplay
from libre_panel.i18n import t

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
        self._had_panel = False  # a panel was in use: one that is gone is waited for

    @property
    def using_panel(self) -> bool:
        return self._impl is not None and not isinstance(self._impl, VirtualDisplay)

    @property
    def model(self) -> PanelModel | None:  # type: ignore[override]
        """The panel in use (None while frames go to the PNG file)."""
        return getattr(self._impl, "model", None) if self.using_panel else None

    def _try_panel(self) -> Display | None:
        self._next_probe = self._clock() + self.PROBE_INTERVAL_S
        panel = TurzxDisplay(self.config)
        panel.counters = self.counters
        try:
            panel.open()
            if self._brightness is not None:
                panel.set_brightness(self._brightness)
            if self.feed is not None:  # (a colour check under way)
                panel.set_feed(self.feed)
            if self.range is not None:
                panel.set_range(self.range)
        except Exception as exc:  # no panel, missing USB libraries, no permission, ...
            panel.close()
            self._last_error = exc
            return None
        return panel

    def open(self) -> None:
        panel = self._try_panel()
        if panel is not None:
            self._impl, self._had_panel = panel, True
            return
        if self._had_panel:
            raise DeviceError(str(self._last_error))  # (as the panel said it)
        log.info(
            "no panel in use (%s); rendering to %s and looking for a panel every %d s",
            self._last_error,
            self.config.output,
            self.PROBE_INTERVAL_S,
        )
        self._impl = VirtualDisplay(self.config)
        self._impl.counters = self.counters
        self._impl.open()

    @property
    def streaming(self) -> bool:
        return bool(self._impl and self._impl.streaming)

    @property
    def restarted(self) -> bool:
        return bool(self._impl and self._impl.restarted)

    @property
    def stream_fps(self) -> int:
        return self._impl.stream_fps if self._impl else 0

    @property
    def slow(self) -> bool:  # type: ignore[override]
        return bool(self._impl and self._impl.slow)

    def set_brightness(self, percent: int) -> None:
        self._brightness = percent
        if self._impl:
            self._impl.set_brightness(percent)

    def set_feed(self, pixels: str | None) -> None:
        self.feed = pixels
        if self._impl:
            self._impl.set_feed(pixels)

    def set_range(self, video_range: str | None) -> None:
        self.range = video_range
        if self._impl:
            self._impl.set_range(video_range)

    def screen_off(self) -> bool:
        return bool(self._impl and self._impl.screen_off())

    def describe(self) -> str:
        return self._impl.describe() if self._impl else t("no panel yet")

    def show_video(self, source: Any, size: tuple[int, int]) -> bool:
        return bool(self.using_panel and self._impl.show_video(source, size))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self._impl is None:
            self.open()
        elif not self.using_panel and self._clock() >= self._next_probe:
            panel = self._try_panel()
            if panel is not None:
                log.info("panel found: %s", panel.describe())
                self._impl.close()
                self._impl, region = panel, None  # the panel needs a full frame first
                self._had_panel = True
        self._impl.show(frame, region)

    def close(self) -> None:
        if self._impl is not None:
            self._impl.close()
            self._impl = None

"""TURZX / Turing Smart Screen driver: picks the protocol for the panel.

``device.model`` in the config selects the panel; with ``auto`` a connected
USB panel (VID 0x1CBE) is detected. Protocol status per family is listed by
``libre-panel models`` and in docs/HARDWARE.md.
"""

from __future__ import annotations

from PIL import Image

from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import PROTOCOLS, find_model
from libre_panel.i18n import t


class TurzxDisplay(Display):
    name = "turzx"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.model = find_model(config.model)
        self._impl: Display | None = None

    def open(self) -> None:
        protocol = self.model.protocol if self.model else "usb-turing"
        if protocol != "usb-turing":
            raise DeviceError(
                f"{self.model.label}: the {PROTOCOLS[protocol]} protocol is not implemented yet. "
                'Use driver = "virtual" meanwhile; see docs/HARDWARE.md for the plan.'
            )
        if self.config.video.mode == "on":
            from libre_panel.devices.turzx_video import TurzxVideoDisplay as Impl
        else:
            from libre_panel.devices.turzx_usb import TurzxUsbDisplay as Impl
        impl = Impl(self.config, self.model)
        impl.open()
        self._impl, self.model = impl, impl.model

    @property
    def streaming(self) -> bool:
        return bool(self._impl and self._impl.streaming)

    @property
    def restarted(self) -> bool:
        return bool(self._impl and self._impl.restarted)

    @property
    def stream_fps(self) -> int:
        return self._impl.stream_fps if self._impl else 0

    def set_brightness(self, percent: int) -> None:
        if self._impl:
            self._impl.set_brightness(percent)

    def describe(self) -> str:
        if self._impl is not None:
            return self._impl.describe()
        return self.model.label if self.model else t("TURZX panel")

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self._impl is None:
            raise DeviceError("panel not open")
        self._impl.show(frame, region)

    def close(self) -> None:
        if self._impl is not None:
            self._impl.close()
            self._impl = None

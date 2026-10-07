"""TURZX / Turing Smart Screen driver: picks the protocol for the panel.

``device.model`` in the config selects the panel; with ``auto`` a connected
USB panel (VID 0x1CBE) is detected, else a serial panel by its USB ids.
Protocol status per family is listed by ``libre-panel models`` and in
docs/HARDWARE.md.
"""

from __future__ import annotations

import logging

from PIL import Image

from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import PROTOCOLS, PanelModel, find_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

SERIAL_DRIVERS = {
    "serial-a": "libre_panel.devices.turing_rev_a:RevADisplay",
}


class TurzxDisplay(Display):
    name = "turzx"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.model = find_model(config.model)
        self._impl: Display | None = None

    def open(self) -> None:
        if self.model is None:
            impl = self._open_any()
        else:
            impl = self._driver(self.model)
            impl.open()
        self._impl, self.model = impl, impl.model

    def _driver(self, model: PanelModel | None, configured: bool = True) -> Display:
        protocol = model.protocol if model else "usb-turing"
        if protocol == "usb-turing":
            if self.config.video.mode == "on":
                from libre_panel.devices.turzx_video import TurzxVideoDisplay as Impl
            else:
                from libre_panel.devices.turzx_usb import TurzxUsbDisplay as Impl
            return Impl(self.config, model)
        target = SERIAL_DRIVERS.get(protocol)
        if target is None:
            raise DeviceError(
                f"{model.label}: the {PROTOCOLS[protocol]} protocol is not implemented yet. "
                'Use driver = "virtual" meanwhile; see docs/HARDWARE.md for the plan.'
            )
        if self.config.video.mode == "on":
            log.info("video mode is for TURZX USB panels; %s gets single frames", model.label)
        module_name, _, attr = target.partition(":")
        module = __import__(module_name, fromlist=[attr])
        return getattr(module, attr)(self.config, model, configured=configured)

    def _open_any(self) -> Display:
        """A USB panel, else a serial one (``device.model = "auto"``)."""
        from libre_panel.devices.serial_link import find_port

        usb = self._driver(None)
        try:
            usb.open()
            return usb
        except DeviceError as exc:
            usb.close()
            missing = exc
        port = getattr(self.config, "port", "")
        try:
            found = find_port(None, port)
        except DeviceError:
            if port:
                raise
            found = None  # no pyserial: the USB error says more
        if found is None:
            raise missing
        impl = self._driver(found.model, configured=False)
        impl.open()
        return impl

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

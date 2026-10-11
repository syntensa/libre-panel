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
    "serial-b": "libre_panel.devices.turing_rev_b:RevBDisplay",
    "serial-c": "libre_panel.devices.turing_rev_c:RevCDisplay",
    "serial-d": "libre_panel.devices.kipye_rev_d:RevDDisplay",
    "serial-weact": "libre_panel.devices.weact:WeActDisplay",
}


def video_mode(video, model: PanelModel | None) -> tuple[bool, list[str]]:
    """Whether a TURZX USB panel runs in video mode, and what keeps ``auto`` from it.

    ``"on"`` and ``"off"`` decide alone. ``"auto"`` needs a model the video mode
    is proven on, the name its start command carries (``local_clip``: never
    made up, it lands in the panel's settings) and ffmpeg.
    """
    if video.mode != "auto":
        return video.mode == "on", []
    if model is None:
        return False, []
    if not model.video:
        return False, [t("the video mode is not proven on the {panel} yet", panel=model.label)]
    missing = []
    if not video.local_clip:
        missing.append(t("video.local_clip is not set (see docs/CONFIGURATION.md, Video)"))
    from libre_panel.devices.h264 import find_ffmpeg

    try:
        find_ffmpeg(video.ffmpeg)
    except DeviceError:  # said without the path: doctor reports are shared
        if video.ffmpeg:
            missing.append(t("ffmpeg is not at the path in video.ffmpeg (config.toml)"))
        else:
            missing.append(
                t(
                    "the video mode needs ffmpeg: install it, or set video.ffmpeg in config.toml "
                    "to the ffmpeg program"
                )
            )
    return not missing, missing


class TurzxDisplay(Display):
    name = "turzx"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.model = find_model(config.model)
        self._impl: Display | None = None
        self._told = ""  # why "auto" left the video mode off, as last logged

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
            video = self.config.video
            probed = model
            if probed is None and video.mode == "auto":
                from libre_panel.devices.turzx_usb import present_model

                probed = present_model()
            on, missing = video_mode(video, probed)
            told = "; ".join(missing)
            if told and told != self._told:
                log.info("video mode off: %s", told)
            self._told = told
            if on:
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
        self.model = self._impl.model  # a rev. C panel learns its size from the first frame

    def close(self) -> None:
        if self._impl is not None:
            self._impl.close()
            self._impl = None

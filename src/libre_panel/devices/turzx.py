"""TURZX / Turing Smart Screen driver: picks the protocol for the panel.

``device.model`` in the config selects the panel; with ``auto`` a connected
USB panel (VID 0x1CBE) is detected, else a serial panel by its USB ids.
Protocol status per family is listed by ``libre-panel models`` and in
docs/HARDWARE.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PIL import Image

from libre_panel.config import DeviceConfig
from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import PROTOCOLS, PanelModel, find_model
from libre_panel.i18n import t

log = logging.getLogger(__name__)

# Why "auto" left the video mode off, as last logged: kept per process, for the default driver
# makes a new display each time it looks for a panel (every 5 s while one cannot be opened).
_told = ""

# What the vendor app calls the panel's standby clip. Only ever offered to the person, who
# confirms it: the video start command copies the name into the panel's settings.
VENDOR_CLIP = "usr/data/standby.h264"

SERIAL_DRIVERS = {
    "serial-a": "libre_panel.devices.turing_rev_a:RevADisplay",
    "serial-b": "libre_panel.devices.turing_rev_b:RevBDisplay",
    "serial-c": "libre_panel.devices.turing_rev_c:RevCDisplay",
    "serial-d": "libre_panel.devices.kipye_rev_d:RevDDisplay",
    "serial-weact": "libre_panel.devices.weact:WeActDisplay",
    "serial-jonsbo": "libre_panel.devices.jonsbo_ds916:Ds916Display",
    "serial-hongtai": "libre_panel.devices.hongtai:HongtaiDisplay",
}
# the drivers of the panels that are neither TURZX USB nor serial, made as the serial ones
OTHER_DRIVERS = {
    "usb-jonsbo": "libre_panel.devices.jonsbo_zc360:Zc360Display",
    "usb-ms9132": "libre_panel.devices.ms9132:Ms9132Display",  # (never found by auto)
    "usb-lianli": "libre_panel.devices.lianli_usb:LianLiDisplay",
    "usb-thermalright": "libre_panel.devices.thermalright:TrofeoDisplay",
    "usb-beada": "libre_panel.devices.beadapanel:BeadaDisplay",
    "usb-nzxt": "libre_panel.devices.nzxt_kraken:KrakenDisplay",
    "usb-thermaltake": "libre_panel.devices.thermaltake:ThermaltakeDisplay",
}
DRIVERS = {**SERIAL_DRIVERS, **OTHER_DRIVERS}


def other_usb_model() -> PanelModel | None:
    """The USB screen of another maker on the bus (Lian Li, Thermalright, BeadaPanel, an NZXT
    Kraken, Thermaltake), as ``auto`` would find it, in this order, without opening it; None
    for none."""
    from libre_panel.devices import beadapanel, lianli_usb, nzxt_kraken, thermalright, thermaltake

    for family in (lianli_usb, thermalright, beadapanel, nzxt_kraken, thermaltake):
        model = family.present_model()
        if model is not None:
            return model
    return None


def video_gaps(video, model: PanelModel | None, *, proven: bool = True) -> list[str]:
    """What the video mode lacks, as codes: ``"model"`` (not proven on this panel; it is then
    the only one), ``"clip"`` (``local_clip`` is not set), ``"ffmpeg"`` (not found) and
    ``"ffmpeg-path"`` (``video.ffmpeg`` names no program), ``"ffmpeg-rights"`` (it names one
    that Libre Panel does not start with the highest rights, and there is no other: rights.py).
    ``proven=False`` does not ask for a proven model, as ``mode = "on"`` does not."""
    if proven and model is not None and not model.video:
        return ["model"]
    gaps = [] if video.local_clip else ["clip"]
    from libre_panel.devices.h264 import FfmpegHeldBack, find_ffmpeg

    try:
        find_ffmpeg(video.ffmpeg)
    except FfmpegHeldBack:
        gaps.append("ffmpeg-rights")
    except DeviceError:
        gaps.append("ffmpeg-path" if video.ffmpeg else "ffmpeg")
    return gaps


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
    gaps = video_gaps(video, model)
    texts = {
        "model": t("the video mode is not proven on the {panel} yet", panel=model.label),
        "clip": t("video.local_clip is not set (see docs/CONFIGURATION.md, Video)"),
        # said without the path: doctor reports are shared
        "ffmpeg-path": t("ffmpeg is not at the path in video.ffmpeg (config.toml)"),
        "ffmpeg-rights": t(
            "with administrator rights Libre Panel does not start the ffmpeg in video.ffmpeg: "
            "it lies in a folder that other programs may change"
        ),
        "ffmpeg": t(
            "the video mode needs ffmpeg: the downloads of Libre Panel carry it; "
            "with pip install it, or set video.ffmpeg in config.toml to the ffmpeg program"
        ),
    }
    return not gaps, [texts[gap] for gap in gaps]


def clip_question(device: DeviceConfig, connected: Callable[[], PanelModel | None]) -> bool:
    """Whether the person is still to be asked for the standby clip's name: ``auto`` waits for
    nothing but that on a panel it is proven on (the one named in ``device.model``, else the one
    ``connected`` finds), and the person has not answered yet. The name is never made up; this
    only says that it is time to offer one. ``connected`` is called last: it may look at the
    USB bus."""
    video = device.video
    if device.driver not in ("auto", "turzx") or video.mode != "auto":
        return False
    if video.local_clip or video.clip_asked:
        return False
    model = find_model(device.model) or connected()
    return model is not None and video_gaps(video, model) == ["clip"]


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
        if self.feed is not None:  # (a colour check under way)
            impl.set_feed(self.feed)

    def _driver(self, model: PanelModel | None, configured: bool = True) -> Display:
        global _told
        protocol = model.protocol if model else "usb-turing"
        if protocol == "usb-turing":
            video = self.config.video
            probed = model
            if probed is None and video.mode == "auto":
                from libre_panel.devices.turzx_usb import present_model

                probed = present_model(self.config.beside)
            on, missing = video_mode(video, probed)
            told = "; ".join(missing)
            if told and told != _told:
                log.info("video mode off: %s", told)
            _told = told
            if on:
                from libre_panel.devices.turzx_video import TurzxVideoDisplay as Impl
            else:
                from libre_panel.devices.turzx_usb import TurzxUsbDisplay as Impl
            return self._counted(Impl(self.config, model))
        target = DRIVERS.get(protocol)
        if target is None:
            raise DeviceError(
                f"{model.label}: the {PROTOCOLS[protocol]} protocol is not implemented yet. "
                'Use driver = "virtual" meanwhile; see docs/HARDWARE.md for the plan.'
            )
        if self.config.video.mode == "on" and protocol in SERIAL_DRIVERS:
            log.info("video mode is for TURZX USB panels; %s gets single frames", model.label)
        module_name, _, attr = target.partition(":")
        module = __import__(module_name, fromlist=[attr])
        return self._counted(getattr(module, attr)(self.config, model, configured=configured))

    def _counted(self, impl: Display) -> Display:
        impl.counters = self.counters  # what it counts goes where the main loop looks
        return impl

    def _open_any(self) -> Display:
        """A USB panel, else another maker's USB screen (Lian Li, Thermalright, BeadaPanel, NZXT,
        Thermaltake), else a JONSBO ZC-360, else a serial one (``device.model = "auto"``)."""
        from libre_panel.devices import jonsbo_zc360
        from libre_panel.devices.serial_link import find_port

        usb = self._driver(None)
        try:
            usb.open()
            return usb
        except DeviceError as exc:
            usb.close()
            missing = exc
        other = other_usb_model()
        if other is not None:
            impl = self._driver(other, configured=False)
            try:
                impl.open()
                return impl
            except DeviceError as exc:  # (a screen of a kind it does not drive: look on)
                impl.close()
                missing = exc
        if jonsbo_zc360.all_free():
            impl = self._driver(find_model(jonsbo_zc360.MODEL_ID), configured=False)
            impl.open()
            return impl
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
    def slow(self) -> bool:  # type: ignore[override]
        """A USB serial panel: the one opened, else the one named (none while it is unknown)."""
        if self._impl is not None:
            return self._impl.slow
        return bool(self.model and self.model.protocol.startswith("serial"))

    @property
    def stream_fps(self) -> int:
        return self._impl.stream_fps if self._impl else 0

    def set_brightness(self, percent: int) -> None:
        if self._impl:
            self._impl.set_brightness(percent)

    def set_feed(self, pixels: str | None) -> None:
        self.feed = pixels
        if self._impl:
            self._impl.set_feed(pixels)

    def screen_off(self) -> bool:
        return bool(self._impl and self._impl.screen_off())

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

"""The frame every serial panel driver shares.

A driver for a serial panel family says how to ask the panel what it is,
how to set the brightness and the orientation, and how a rectangle of pixels
goes out. Finding the port, fitting frames of another size, sending only the
changed box and reconnecting after an error are the same for all of them.
"""

from __future__ import annotations

import logging

from PIL import Image, ImageOps

from libre_panel.devices.base import DeviceError, Display
from libre_panel.devices.models import PanelModel, orientation_of
from libre_panel.devices.serial_link import FoundPort, SerialLink, find_port
from libre_panel.i18n import t

log = logging.getLogger(__name__)


class SerialDisplay(Display):
    name = "serial"
    baud = 115200
    rtscts = True

    def __init__(self, config, model: PanelModel | None = None, configured: bool = True) -> None:
        super().__init__(config)
        self.model = model
        self.configured = configured and model is not None  # chosen, not detected
        self.link: SerialLink | None = None
        self.brightness: int | None = None
        self.orientation: str | None = None  # "portrait" or "landscape", set by the first frame

    # -- what a family says -----------------------------------------------------

    @property
    def family(self) -> str:
        """The family's name for messages, e.g. "Turing rev. A panel"."""
        return t("serial panel")

    def identify(self, found: FoundPort) -> PanelModel:
        """The model on the port; drivers whose panels say it ask here."""
        return found.model

    def send_brightness(self, percent: int) -> None:
        raise NotImplementedError

    def send_orientation(self, orientation: str, width: int, height: int) -> None:
        """Turn the panel to ``orientation``; ``width``x``height`` is its size then."""
        raise NotImplementedError

    def send_bitmap(self, x0: int, y0: int, x1: int, y1: int, image: Image.Image) -> None:
        """Pixels for the box from (x0, y0) to (x1, y1), the last pixel included."""
        raise NotImplementedError

    # -- the same for all ----------------------------------------------------------

    def open(self) -> None:
        found = find_port(self.model, getattr(self.config, "port", ""))
        if found is None:
            raise DeviceError(t("no {panel} found on a serial port", panel=self.family))
        self.link = SerialLink(found.device, self.baud, self.rtscts)
        self.orientation = None
        try:
            self.model = self.identify(found)
        except DeviceError:
            self.close()
            raise
        log.info("connected to %s on %s", self.model.label, found.device)

    def describe(self) -> str:
        return self.model.label if self.model else self.family

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent
        if self.link:
            self.send_brightness(max(0, min(100, percent)))

    def show(self, frame: Image.Image, region: tuple[int, int, int, int] | None = None) -> None:
        if self.link is None:
            raise DeviceError("panel not open")
        orientation = orientation_of(*frame.size)
        size = self.model.size(orientation) if self.model else frame.size
        if frame.size != size:  # a detected panel of another size than the theme
            corner = frame.getpixel((0, 0))
            frame = ImageOps.pad(frame, size, method=Image.Resampling.LANCZOS, color=corner)
            region = None
        if orientation != self.orientation:
            self.send_orientation(orientation, *size)
            self.orientation = orientation
            region = None  # a new orientation needs the whole frame
        x0, y0, x1, y1 = region or (0, 0, *size)
        if x1 <= x0 or y1 <= y0:
            return
        self.send_bitmap(x0, y0, x1 - 1, y1 - 1, frame.crop((x0, y0, x1, y1)))

    def close(self) -> None:
        if self.link is not None:
            self.link.close()
            self.link = None

    def write_chunks(self, data: bytes, size: int, prefix: bytes = b"") -> None:
        for start in range(0, len(data), size):
            self.link.write(prefix + data[start : start + size])

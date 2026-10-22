"""The theme a panel shows when config.toml names none: the panel's own default.

Lumen is made for the 1920x480 bars (the 8.8" and 9.2"); every other panel starts with
Libre Default. A default is only that: a ``theme`` in config.toml, or ``--theme``, always wins.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from libre_panel.config import DEFAULT_THEME
from libre_panel.devices.models import PanelModel, find_model

if TYPE_CHECKING:
    from libre_panel.config import Config, DeviceConfig

log = logging.getLogger(__name__)

BAR_THEME = "lumen"
BAR_RATIO = 3.5  # a bar is at least this many times as long as it is high
# The drivers that look for a TURZX USB panel (``auto`` tries it first, then a PNG file).
USB_DRIVERS = ("auto", "turzx")


def is_bar(model: PanelModel | None) -> bool:
    """Whether ``model`` is a bar: its landscape frame is 3.5 times as long as it is high or
    more (1920x480: the 8.8" and 9.2", whatever the bezel hides). The 12.3" (1920x720) is not."""
    if model is None:
        return False
    long, short = model.size("landscape")
    return long >= BAR_RATIO * short


def default_theme(model: PanelModel | None) -> str:
    """The theme a config without a ``theme`` shows on ``model``: Lumen on a bar, Libre Default
    on every other panel and where the panel is not known."""
    return BAR_THEME if is_bar(model) else DEFAULT_THEME


def panel_model(device: DeviceConfig, reported: PanelModel | None = None) -> PanelModel | None:
    """The panel a config is for, as far as it is known without opening anything: the
    configured ``device.model``; else ``reported``, the model a display said it is; else, with
    ``model = "auto"`` and a driver that looks for TURZX USB panels, the one on the USB bus
    (found by its ids, never opened). A serial port is not looked at: such a panel is known
    once it connects."""
    configured = find_model(device.model)
    if configured is not None or reported is not None:
        return configured or reported
    if device.model != "auto" or device.driver not in USB_DRIVERS:
        return None
    from libre_panel.devices import turzx_usb

    try:
        return turzx_usb.present_model(device.beside)
    except Exception:  # no USB library or backend: no panel is known
        log.debug("looking for a USB panel failed", exc_info=True)
        return None


def normal_theme(config: Config, running: str | None = None) -> str:
    """The theme of the panel's normal page: the one config.toml names; else the panel's
    default, which is ``running`` (what the running app worked out for the panel it drives)
    or, without it, worked out from the config alone."""
    if config.theme_given:
        return config.theme
    return running or default_theme(panel_model(config.device))

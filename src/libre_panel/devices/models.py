"""Catalog of known panels.

Sizes, native resolutions and USB identifiers are hardware facts collected
from public sources (vendor listings and the turing-smart-screen-python wiki);
SPUR II testing adds to this. ``driver`` says whether Libre Panel can drive the
panel yet. Native resolution is given in portrait, as the panels report it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

TURING = "Turing Smart Screen / TURZX"

# Protocol families. Panels in one family share a driver.
PROTOCOLS = {
    "serial-a": "Serial (CH552T), Turing rev. A",
    "serial-b": "Serial (CH552T), XuanFang rev. B",
    "serial-c": "Serial, Turing rev. C",
    "serial-d": "Serial, Kipye rev. D",
    "serial-weact": "Serial, WeAct Studio",
    "usb-turing": "USB bulk (VID 0x1CBE), Turing V1.x hardware",
}


@dataclass(frozen=True)
class PanelModel:
    id: str
    vendor: str
    name: str
    size_inch: float
    native_width: int
    native_height: int
    protocol: str
    shape: str = "rect"  # "rect" or "round"
    usb_ids: tuple[tuple[int, int], ...] = ()
    serial_numbers: tuple[str, ...] = ()
    # "supported": `libre-panel doctor` passed on this model.
    # "unverified": driver exists, protocol proven on the family, this model not confirmed yet.
    # "planned": no driver yet.
    driver: str = "planned"
    notes: str = ""

    def size(self, orientation: str) -> tuple[int, int]:
        """Frame size for ``portrait`` or ``landscape``."""
        short, long = sorted((self.native_width, self.native_height))
        return (long, short) if orientation == "landscape" else (short, long)

    @property
    def label(self) -> str:
        shape = " round" if self.shape == "round" else ""
        return f'{self.name} {self.size_inch:g}"{shape}'

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["label"] = self.label
        data["usb_ids"] = [f"{v:04x}:{p:04x}" for v, p in self.usb_ids]
        data["landscape"] = list(self.size("landscape"))
        data["portrait"] = list(self.size("portrait"))
        data["protocol_name"] = PROTOCOLS.get(self.protocol, self.protocol)
        return data


_REV_C_IDS = ((0x1A86, 0xCA21), (0x0525, 0xA4A7), (0x1D6B, 0x0121), (0x1D6B, 0x0106))

MODELS: tuple[PanelModel, ...] = (
    PanelModel(
        "turing-2.1",
        TURING,
        "Turing",
        2.1,
        480,
        480,
        "serial-c",
        shape="round",
        usb_ids=_REV_C_IDS,
        serial_numbers=("CT21INCH", "20080411"),
    ),
    PanelModel(
        "turing-2.8-usb",
        TURING,
        "Turing",
        2.8,
        480,
        480,
        "usb-turing",
        shape="round",
        usb_ids=((0x1CBE, 0x0028),),
        notes="V1.x hardware",
    ),
    PanelModel(
        "turing-3.5",
        TURING,
        "Turing",
        3.5,
        320,
        480,
        "serial-a",
        usb_ids=((0x1A86, 0x5722),),
        serial_numbers=("USB35INCHIPS", "USB35INCHIPSV2"),
    ),
    PanelModel(
        "turing-4.6-usb",
        TURING,
        "Turing",
        4.6,
        320,
        960,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0046),),
    ),
    PanelModel(
        "turing-5",
        TURING,
        "Turing",
        5,
        480,
        800,
        "serial-c",
        usb_ids=_REV_C_IDS,
        serial_numbers=("USB7INCH", "20080411"),
    ),
    PanelModel(
        "turing-5.2-usb",
        TURING,
        "Turing",
        5.2,
        720,
        1280,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0050),),
    ),
    PanelModel(
        "turing-8-usb",
        TURING,
        "Turing",
        8,
        800,
        1280,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0080),),
    ),
    PanelModel(
        "turing-8.8",
        TURING,
        "Turing",
        8.8,
        480,
        1920,
        "serial-c",
        usb_ids=_REV_C_IDS,
        serial_numbers=("CT88INCH", "20080411"),
        notes="V0.x hardware",
    ),
    PanelModel(
        "turing-8.8-usb",
        TURING,
        "Turing",
        8.8,
        480,
        1920,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0088),),
        notes="V1.x hardware",
    ),
    PanelModel(
        # The reference library lists 462x1920; the panel itself uses 480x1920
        # (verified on hardware by SPUR II: no edge of a test card is cut off).
        "turing-9.2-usb",
        TURING,
        "Turing",
        9.2,
        480,
        1920,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0092),),
        notes="protocol verified on hardware",
    ),
    PanelModel(
        "turing-12.3-usb",
        TURING,
        "Turing",
        12.3,
        720,
        1920,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0123),),
    ),
    PanelModel(
        "usbpcmonitor-3.5",
        "UsbPCMonitor",
        "UsbPCMonitor",
        3.5,
        320,
        480,
        "serial-a",
        usb_ids=((0x1A86, 0x5722),),
    ),
    PanelModel(
        "usbpcmonitor-5",
        "UsbPCMonitor",
        "UsbPCMonitor",
        5,
        480,
        800,
        "serial-a",
        usb_ids=((0x1A86, 0x5722),),
    ),
    PanelModel(
        "xuanfang-3.5",
        "XuanFang",
        "XuanFang rev. B / flagship",
        3.5,
        320,
        480,
        "serial-b",
        usb_ids=((0x1A86, 0x5722),),
        serial_numbers=("2017-2-25",),
    ),
    PanelModel(
        "kipye-3.5",
        "Kipye",
        "Kipye Qiye",
        3.5,
        320,
        480,
        "serial-d",
        usb_ids=((0x454D, 0x4E41),),
    ),
    PanelModel(
        "weact-3.5",
        "WeAct Studio",
        "WeAct Display FS V1",
        3.5,
        320,
        480,
        "serial-weact",
        usb_ids=((0x1A86, 0xFE0C),),
        notes="serial number starts with AB",
    ),
    PanelModel(
        "weact-0.96",
        "WeAct Studio",
        "WeAct Display FS V1",
        0.96,
        80,
        160,
        "serial-weact",
        usb_ids=((0x1A86, 0xFE0C),),
        notes="serial number starts with AD",
    ),
)

# Drivers that exist so far (see devices/turzx_usb.py); everything else is planned.
_DRIVER_STATUS = {"usb-turing": "unverified"}
# Models confirmed with `libre-panel doctor`; add a model here only with a passing report.
_CONFIRMED: set[str] = set()
MODELS = tuple(
    replace(
        m, driver="supported" if m.id in _CONFIRMED else _DRIVER_STATUS.get(m.protocol, m.driver)
    )
    for m in MODELS
)

_BY_ID = {m.id: m for m in MODELS}


def get_model(model_id: str) -> PanelModel:
    try:
        return _BY_ID[model_id]
    except KeyError:
        raise KeyError(f"unknown panel model {model_id!r}") from None


def find_model(model_id: str | None) -> PanelModel | None:
    return _BY_ID.get(model_id or "")


def models_for_usb(vid: int | None, pid: int | None, serial: str | None = None) -> list[PanelModel]:
    """Candidate models for a connected device.

    Several serial models share a USB bridge chip, so the result can hold more
    than one entry; a serial-number match narrows it down, and the driver's
    handshake settles the rest.
    """
    if vid is None or pid is None:
        return []
    matches = [m for m in MODELS if (vid, pid) in m.usb_ids]
    if serial:
        exact = [m for m in matches if serial in m.serial_numbers]
        if exact:
            return exact
    return matches


def orientation_of(width: int, height: int) -> str:
    return "landscape" if width > height else "portrait"


def model_for_size(width: int, height: int) -> PanelModel | None:
    """First Turing model whose native resolution matches, in either orientation."""
    wanted = tuple(sorted((width, height)))
    for model in MODELS:
        if tuple(sorted((model.native_width, model.native_height))) == wanted:
            return model
    return None

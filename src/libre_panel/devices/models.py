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

EDGES = ("top", "right", "bottom", "left")

# Protocol families. Panels in one family share a driver. All plug in with a USB
# cable; "USB serial" panels show up as a virtual COM port (USB CDC), the
# others as a USB device of their own.
PROTOCOLS = {
    "serial-a": "USB serial (CH552T), Turing rev. A",
    "serial-b": "USB serial (CH552T), XuanFang rev. B",
    "serial-c": "USB serial, Turing rev. C",
    "serial-d": "USB serial, Kipye rev. D",
    "serial-weact": "USB serial, WeAct Studio",
    "usb-turing": "USB bulk (VID 0x1CBE), Turing V1.x hardware",
    "usb-jonsbo": "USB bulk (VID 0x43A8), three screens driven as one",
    "serial-jonsbo": "USB serial (VID 0x33C3), whole JPEG pictures",
    "usb-ms9132": "USB bulk (MacroSilicon MS9132), a USB display chip",
    "serial-hongtai": "USB serial (VID 0x33C3, 2 Mbaud), Hongtai / Jungle Leopard JPEG frames",
    "usb-lianli": "USB bulk (VID 0x1CBE), Lian Li's TURZX-like protocol with JPEG frames",
    "usb-thermalright": "USB bulk or HID (VID 0x0416), Thermalright's Trofeo Vision, JPEG frames",
    "usb-beada": "USB bulk (VID 0x4E58), BeadaPanel's Panel-Link, 16-bit frames",
    "usb-nzxt": "USB bulk and HID (VID 0x1E71), an NZXT Kraken's LCD, frames through its memory",
    "usb-thermaltake": "USB HID (VID 0x264A), Thermaltake's LCD, JPEG frames",
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
    # "supported": `libre-panel doctor` passed on this model, or its owners report it runs.
    # "unverified": driver exists, protocol proven on hardware (SPUR II, or
    # turing-smart-screen-python for serial panels), this model not confirmed yet.
    # "planned": no driver yet.
    driver: str = "planned"
    notes: str = ""
    # What the panel cannot do, or does otherwise than a TURZX USB panel, for its owner to
    # know before it surprises them (Settings → Panel says it under its name); "" for none.
    limits: str = ""
    # Pixels of the framebuffer (native, portrait) behind the bezel:
    # top, right, bottom, left. Measured with the ruler card of `libre-panel doctor`.
    hidden: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Video mode (H.264) proven on this model: ``video.mode = "auto"`` uses it here.
    video: bool = False
    # How its H.264 decoder shows the video's values ([device] video_range "auto"): "limited"
    # (16-235, as a stream says by default), or "full" (0-255) for one that shows every stream
    # as full range, whatever the stream says
    video_range: str = "limited"
    # Whether ``video_range`` is what an owner saw on the panel (Check the colours' A/B), not
    # the default: where it is not, Start asks the person once at the first video
    video_range_seen: bool = False

    def size(self, orientation: str) -> tuple[int, int]:
        """Frame size for ``portrait`` or ``landscape``."""
        short, long = sorted((self.native_width, self.native_height))
        return (long, short) if orientation == "landscape" else (short, long)

    def hidden_edges(self, orientation: str) -> dict[str, int]:
        """The hidden strips at the edges of a frame in ``orientation``.

        Data for layouts and the editor's guide; nothing crops or moves a frame.
        Frames reach the framebuffer turned as the USB driver turns them: a
        landscape frame a quarter turn clockwise (its top lands on the
        framebuffer's right edge), a portrait frame half a turn.
        """
        top, right, bottom, left = self.hidden
        if orientation == "landscape":
            return {"top": right, "right": bottom, "bottom": left, "left": top}
        return {"top": bottom, "right": left, "bottom": top, "left": right}

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
        data["hidden"] = {o: self.hidden_edges(o) for o in ("landscape", "portrait")}
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
        "turing-1.6-usb",
        TURING,
        "Turing",
        1.6,
        400,
        400,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0016),),
        notes="V1.x hardware",
    ),
    PanelModel(
        "turing-2.1-usb",
        TURING,
        "Turing",
        2.1,
        480,
        480,
        "usb-turing",
        shape="round",
        usb_ids=((0x1CBE, 0x0021),),
        notes="V1.x hardware",
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
        # Some are sold as "UsbPCMonitor 5"" and sleep as CT21INCH, like the 2.1".
        serial_numbers=("USB7INCH", "CT21INCH", "20080411"),
        notes='also sold as UsbPCMonitor 5"',
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
        notes="V1.x hardware; verified on the hardware: runs, as its owners report",
    ),
    PanelModel(
        # The framebuffer is 480x1920, but the bezel hides its last 18 columns:
        # 1920x462 are visible, the reference library's size (measured with a
        # 1 px ruler on the 9.2"; in landscape the strip is at the top).
        "turing-9.2-usb",
        TURING,
        "Turing",
        9.2,
        480,
        1920,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0092),),
        notes="protocol verified on hardware",
        hidden=(0, 18, 0, 0),
        video=True,
        # as the 12.3": the owner's panel showed limited-range video under a grey veil and
        # full range right (the A/B comparison of Check the colours)
        video_range="full",
        video_range_seen=True,
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
        # an owner's panel showed limited-range video washed out (black grey) with the card's
        # conversion and with the processor's, the stream tagged limited, and right as stills:
        # its decoder takes every stream as full range
        video_range="full",
        video_range_seen=True,
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
        notes='USB id 1a86:5722; one with serial number CT21INCH is "turing-5"',
    ),
    PanelModel(
        "usbpcmonitor-7",
        "UsbPCMonitor",
        "UsbPCMonitor",
        7,
        600,
        1024,
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
    PanelModel(
        "jonsbo-zc360",
        "JONSBO",
        "ZC-360 fan screens",
        9.8,  # three 3.38" screens of 640x180 side by side: one picture of 1920x180
        180,
        1920,
        "usb-jonsbo",
        usb_ids=((0x43A8, 0x0E61),),
        notes='three 3.38" screens under the fans, each a USB device ("TURZX-338inch-r")',
    ),
    PanelModel(
        "jonsbo-zc360-screen",
        "JONSBO",
        "ZC-360 fan screen",
        3.38,
        180,
        640,
        "usb-jonsbo",
        # (no USB ids of its own: they are the ZC-360's, which "auto" finds as one panel; the
        # three as panels of their own are set up in the app, device.port says which screen)
        notes="one of the ZC-360's three screens under the fans as a panel of its own",
    ),
    PanelModel(
        "jonsbo-tf3-pump",
        "JONSBO",
        "TF3-360SC pump screen",
        3.5,
        480,
        640,
        "usb-turing",
        usb_ids=((0x1CBE, 0x0035),),
        # (its fans carry the ZC-360's three screens: a panel of their own beside it)
        notes="the screen on the pump of the TF3-360SC AIO, a TURZX USB panel inside",
    ),
    PanelModel(
        "jonsbo-ds916",
        "JONSBO",
        "DS916",
        9.16,
        462,
        1920,
        "serial-jonsbo",
        usb_ids=((0x33C3, 0xF101),),
        notes="a case screen with a controller of its own (not a TURZX one) that takes JPEG",
    ),
    PanelModel(
        "jonsbo-ds339",
        "JONSBO",
        "DS339",
        3.39,
        376,
        960,
        "usb-ms9132",
        usb_ids=((0x345F, 0x9132),),
        # (USB-to-HDMI adapters have the chip too: never opened unless the config names it)
        notes="a USB display chip (MS9132), not found by auto: choose the model",
    ),
    PanelModel(
        "vmax-4.6",
        "VMAX",
        "VMAX",
        4.6,
        320,
        960,
        "usb-ms9132",
        usb_ids=((0x345F, 0x9132),),
        notes="a USB display chip (MS9132), not found by auto: choose the model",
    ),
    PanelModel(
        "hongtai-480x960",
        "Hongtai",
        "Hongtai cooler screen",
        5,
        480,
        960,
        "serial-hongtai",
        usb_ids=((0x33C3, 0x7788),),
        notes="Jungle Leopard Chill Arc 360; MSI EZ Display, Thermaltake, ZOTAC and others",
    ),
    PanelModel(
        "hongtai-1920x462",
        "Hongtai",
        "Hongtai strip screen",
        9.16,
        462,
        1920,
        "serial-hongtai",
        usb_ids=((0x33C3, 0x7788),),
        notes="the strip of the Hongtai / Jungle Leopard family",
    ),
    PanelModel(
        "lianli-universal-8.8",
        "Lian Li",
        "Lian Li Universal Screen",
        8.8,
        480,
        1920,
        "usb-lianli",
        usb_ids=((0x1CBE, 0xA088),),
    ),
    PanelModel(
        "lianli-universal-9.2",
        "Lian Li",
        "Lian Li Universal Screen",
        9.2,
        464,
        1920,
        "usb-lianli",
        usb_ids=((0x1CBE, 0xA092),),
    ),
    PanelModel(
        "lianli-hydroshift2-lcd",
        "Lian Li",
        "Lian Li HydroShift II LCD",
        2.1,
        480,
        480,
        "usb-lianli",
        usb_ids=((0x1CBE, 0xA034),),
        notes="the LCD-C's screen; the LCD-S has 3.4\" of the same 480x480",
    ),
    PanelModel(
        "lianli-hydroshift2-oled",
        "Lian Li",
        "Lian Li HydroShift II OLED Curve",
        6.67,
        2288,  # (its framebuffer lies: landscape)
        1080,
        "usb-lianli",
        usb_ids=((0x1CBE, 0xA068),),
    ),
    PanelModel(
        "thermalright-trofeo-9.16",
        "Thermalright",
        "Thermalright Trofeo Vision",
        9.16,
        462,
        1920,
        "usb-thermalright",
        usb_ids=((0x0416, 0x5408),),
    ),
    PanelModel(
        "thermalright-trofeo-11.3",
        "Thermalright",
        "Thermalright Trofeo Vision",
        11.3,
        440,
        1920,
        "usb-thermalright",
        usb_ids=((0x0416, 0x5408),),
    ),
    PanelModel(
        "thermalright-trofeo-6.86",
        "Thermalright",
        "Thermalright Trofeo Vision",
        6.86,
        480,
        1280,
        "usb-thermalright",
        usb_ids=((0x0416, 0x5302),),
    ),
    # BeadaPanel: one USB id for all; the panel says which it is. The sizes are the diagonals
    # of the millimetres the vendor's Linux driver gives (the 2 and 2W are round), the 3's and
    # the 7's as the vendor announced them.
    PanelModel(
        "beadapanel-2",
        "BeadaPanel",
        "BeadaPanel 2",
        2.1,
        480,
        480,
        "usb-beada",
        shape="round",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-2w",
        "BeadaPanel",
        "BeadaPanel 2W",
        2.8,
        480,
        480,
        "usb-beada",
        shape="round",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-3",
        "BeadaPanel",
        "BeadaPanel 3",
        3.5,
        320,
        480,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-4",
        "BeadaPanel",
        "BeadaPanel 4",
        4.3,
        480,
        800,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-5",
        "BeadaPanel",
        "BeadaPanel 5",
        5,
        480,
        800,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-5s",
        "BeadaPanel",
        "BeadaPanel 5S",
        5,
        480,
        854,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-6",
        "BeadaPanel",
        "BeadaPanel 6",
        6.8,
        480,
        1280,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-7",
        "BeadaPanel",
        "BeadaPanel 7",
        7,
        480,
        800,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-7s",
        "BeadaPanel",
        "BeadaPanel 7S",
        7.8,
        400,
        1280,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-8",
        "BeadaPanel",
        "BeadaPanel 8",
        8.8,
        480,
        1920,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-9",
        "BeadaPanel",
        "BeadaPanel 9",
        9.16,
        462,
        1920,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "beadapanel-11",
        "BeadaPanel",
        "BeadaPanel 11",
        10.2,
        440,
        1920,
        "usb-beada",
        usb_ids=((0x4E58, 0x1001),),
    ),
    PanelModel(
        "nzxt-kraken-z",
        "NZXT",
        "NZXT Kraken Z",
        2.36,
        320,
        320,
        "usb-nzxt",
        shape="round",
        usb_ids=((0x1E71, 0x3008),),
    ),
    PanelModel(
        "nzxt-kraken-2023",
        "NZXT",
        "NZXT Kraken 2023",
        1.54,
        240,
        240,
        "usb-nzxt",
        shape="round",
        usb_ids=((0x1E71, 0x300E),),
    ),
    PanelModel(
        "nzxt-kraken-elite-2024",
        "NZXT",
        "NZXT Kraken Elite RGB 2024",
        2.72,
        640,
        640,
        "usb-nzxt",
        shape="round",
        usb_ids=((0x1E71, 0x3012),),
    ),
    PanelModel(
        "nzxt-kraken-plus-2024",
        "NZXT",
        "NZXT Kraken Plus 2024",
        1.54,
        240,
        240,
        "usb-nzxt",
        shape="round",
        usb_ids=((0x1E71, 0x3014),),
    ),
    PanelModel(
        "thermaltake-lcd-6",
        "Thermaltake",
        "Thermaltake LCD",
        6,
        720,
        1480,
        "usb-thermaltake",
        usb_ids=((0x264A, 0x2347),),
    ),
)

# Families with a driver (devices/turzx.py lists them); the rest is planned.
_DRIVER_STATUS = {
    protocol: "unverified"
    for protocol in (
        "usb-turing",
        "serial-a",
        "serial-b",
        "serial-c",
        "serial-d",
        "serial-weact",
        "usb-jonsbo",
        "serial-jonsbo",
        "usb-ms9132",
        "serial-hongtai",
        "usb-lianli",
        "usb-thermalright",
        "usb-beada",
        "usb-nzxt",
        "usb-thermaltake",
    )
}
# Models confirmed on the panel: with `libre-panel doctor`, or by an owner who reports it runs.
_CONFIRMED: set[str] = {
    "turing-9.2-usb",  # stills and 50 fps video, for hours on end; an owner's doctor report (#19)
    "turing-8.8-usb",  # "works wonderfully", an owner's report in the TURZX forum
    "turing-12.3-usb",  # recognised and running from the first start, on the maintainer's desk
    "turing-3.5",  # rev. A: runs, its screen turned off with the command Libre Panel sends
    "turing-8.8",  # rev. C: runs since 0.6.4, an owner's report
}
# What the panels whose drivers were written from other projects' notes cannot do (their
# drivers say why); each begins with what all of them share: nobody has run them yet.
_UNSEEN = "Not yet seen on the hardware."
_JPEG_SERIAL = (
    "Not yet seen on the hardware. It takes whole JPEG pictures over a USB serial port: a few a "
    "second, no smooth motion, and a busy picture loses detail. When Libre Panel quits, the "
    "panel shows its own animation again."
)
_MS9132 = (
    'Not yet seen on the hardware. "Detect automatically" does not find it (USB-to-HDMI '
    "adapters have the same chip): choose it by hand. On Windows it needs the maker's driver, "
    "which comes with the maker's program. It has no backlight control: dimming darkens the "
    "picture. No smooth motion."
)
_LIANLI = (
    "Not yet seen on the hardware. It takes whole JPEG pictures: no smooth motion. Close "
    "L-Connect while Libre Panel runs: two programs cannot drive it at once."
)
_TROFEO = (
    "Not yet seen on the hardware. It takes whole JPEG pictures: no smooth motion. It has no "
    "backlight control: dimming darkens the picture. Close Thermalright's program while Libre "
    "Panel runs. When Libre Panel quits, the panel shows its own picture again."
)
_BEADA = (
    "Not yet seen on the hardware. It takes whole pictures in 16-bit colour, a few a second: no "
    "smooth motion, and soft gradients show steps. Close the maker's program while Libre Panel "
    "runs: two programs cannot drive it at once."
)
_KRAKEN = (
    "Not yet seen on the hardware. Each picture goes through the cooler's picture memory: a few "
    "a second, no smooth motion. Close NZXT CAM while Libre Panel runs. When Libre Panel quits, "
    "the cooler shows the liquid's temperature again."
)
_LIMITS = {
    "turing-1.6-usb": _UNSEEN,
    "turing-2.1-usb": _UNSEEN,
    "jonsbo-tf3-pump": _UNSEEN,
    "jonsbo-zc360": (
        "Not yet seen on the hardware. Its three screens take their pictures one after the "
        "other, so what moves changes less often than on a single panel. No smooth motion."
    ),
    "jonsbo-zc360-screen": (
        "Not yet seen on the hardware. The ZC-360's three screens take their pictures one "
        "after the other, also as panels of their own, so what moves changes less often than "
        "on a single panel. No smooth motion."
    ),
    "jonsbo-ds916": (
        "Not yet seen on the hardware. It takes whole JPEG pictures: no smooth motion, and "
        "fine detail may soften. It has no backlight control: dimming darkens the picture. "
        "When Libre Panel quits, the panel shows its own animation again."
    ),
    "jonsbo-ds339": _MS9132,
    "vmax-4.6": _MS9132,
    "hongtai-480x960": _JPEG_SERIAL,
    "hongtai-1920x462": _JPEG_SERIAL,
    "lianli-universal-8.8": _LIANLI,
    "lianli-universal-9.2": _LIANLI,
    "lianli-hydroshift2-lcd": _LIANLI,
    "lianli-hydroshift2-oled": _LIANLI,
    **{m.id: _TROFEO for m in MODELS if m.protocol == "usb-thermalright"},
    **{m.id: _BEADA for m in MODELS if m.protocol == "usb-beada"},
    **{m.id: _KRAKEN for m in MODELS if m.protocol == "usb-nzxt"},
    "thermaltake-lcd-6": (
        "Not yet seen on the hardware. It takes whole JPEG pictures: no smooth motion. Its "
        "brightness goes into the picture: dimming darkens it. Close TT RGB Plus while Libre "
        "Panel runs. When Libre Panel quits, the panel shows its own screen again."
    ),
}
MODELS = tuple(
    replace(
        m,
        driver="supported" if m.id in _CONFIRMED else _DRIVER_STATUS.get(m.protocol, m.driver),
        limits=_LIMITS.get(m.id, m.limits),
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

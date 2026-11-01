"""How the PC is connected to the network: by cable (LAN) or Wi-Fi, how fast, and how strong
the Wi-Fi's signal is (``net.link``, ``net.link.speed``, ``net.wifi.signal``; the psutil source
reads them, every ten seconds).

The connection is the one the PC reaches the internet through (its default route), not every
adapter there is:

- **Linux**: the default route's interface (``/proc/net/route``); Wi-Fi where it has a
  ``wireless`` folder in ``/sys/class/net``, its signal from ``/proc/net/wireless`` (the link's
  quality, of 70) and its rate from ``iw dev <name> link`` where ``iw`` is installed; a cable's
  speed from ``/sys/class/net/<name>/speed``. Nothing is started but ``iw``.
- **Windows**: the adapter Windows would send to the internet by (``GetBestInterface``), its
  type and speed (``GetIfEntry``: IEEE 802.11 is Wi-Fi); the Wi-Fi's signal and rate from the
  WLAN service (``wlanapi``). No program is started.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LAN, WLAN = "lan", "wlan"


@dataclass(frozen=True)
class Link:
    kind: str  # LAN or WLAN
    speed: float | None = None  # Mbit/s
    signal: float | None = None  # %, Wi-Fi only
    name: str = ""  # the interface's


def speed_text(mbit: float) -> str:
    """A link's speed as one says it: 866 Mbit/s, 2.5 Gbit/s."""
    if mbit >= 1000:
        gbit = mbit / 1000
        return f"{gbit:.1f} Gbit/s".replace(".0 ", " ")
    return f"{mbit:.0f} Mbit/s"


def read() -> Link | None:
    """The connection now, None where it cannot be told (no network, another system)."""
    try:
        if sys.platform.startswith("linux"):
            return _linux()
        if sys.platform == "win32":
            return _windows()
    except Exception as exc:  # noqa: BLE001 - a reading that fails is no reading
        log.debug("network link: %s", exc)
    return None


# -- Linux --------------------------------------------------------------------------------------

_PROC = Path("/proc")
_SYS = Path("/sys/class/net")
_IW_RATE = re.compile(r"tx bitrate:\s*([\d.]+)\s*MBit/s", re.IGNORECASE)


def default_interface(route: str) -> str | None:
    """The interface of the default route in ``/proc/net/route``'s text (the lowest metric)."""
    best: tuple[int, str] | None = None
    for line in route.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 7 and fields[1] == "00000000":
            metric = int(fields[6]) if fields[6].isdigit() else 0
            if best is None or metric < best[0]:
                best = (metric, fields[0])
    return None if best is None else best[1]


def wireless_quality(text: str, name: str) -> float | None:
    """The link quality of ``name`` in ``/proc/net/wireless``'s text, in % (of 70)."""
    for line in text.splitlines()[2:]:
        head, _, rest = line.partition(":")
        if head.strip() == name:
            fields = rest.split()
            if len(fields) >= 2:
                quality = float(fields[1].rstrip("."))
                return max(0.0, min(100.0, quality * 100 / 70))
    return None


def _linux() -> Link | None:
    name = default_interface((_PROC / "net" / "route").read_text())
    if name is None:
        return None
    if (_SYS / name / "wireless").exists():
        try:
            signal = wireless_quality((_PROC / "net" / "wireless").read_text(), name)
        except OSError:
            signal = None
        return Link(WLAN, _iw_rate(name), signal, name)
    try:
        speed = float((_SYS / name / "speed").read_text().strip())
    except (OSError, ValueError):
        speed = None
    return Link(LAN, speed if speed and speed > 0 else None, None, name)


def _iw_rate(name: str) -> float | None:
    iw = shutil.which("iw")
    if iw is None:
        return None
    try:
        out = subprocess.run([iw, "dev", name, "link"], capture_output=True, text=True,
                             timeout=2, env={**os.environ, "LC_ALL": "C"}).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    found = _IW_RATE.search(out)
    return float(found.group(1)) if found else None


# -- Windows ------------------------------------------------------------------------------------

IF_TYPE_IEEE80211 = 71
_INTERNET = 0x08080808  # 8.8.8.8: only the route to it is looked up, nothing is sent


def _windows() -> Link | None:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi")
    index = wintypes.DWORD()
    if iphlpapi.GetBestInterface(wintypes.DWORD(_INTERNET), ctypes.byref(index)) != 0:
        return None

    class MibIfRow(ctypes.Structure):
        _fields_ = [
            ("wszName", wintypes.WCHAR * 256), ("dwIndex", wintypes.DWORD),
            ("dwType", wintypes.DWORD), ("dwMtu", wintypes.DWORD), ("dwSpeed", wintypes.DWORD),
            ("dwPhysAddrLen", wintypes.DWORD), ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", wintypes.DWORD), ("dwOperStatus", wintypes.DWORD),
            ("dwLastChange", wintypes.DWORD), ("dwInOctets", wintypes.DWORD),
            ("dwInUcastPkts", wintypes.DWORD), ("dwInNUcastPkts", wintypes.DWORD),
            ("dwInDiscards", wintypes.DWORD), ("dwInErrors", wintypes.DWORD),
            ("dwInUnknownProtos", wintypes.DWORD), ("dwOutOctets", wintypes.DWORD),
            ("dwOutUcastPkts", wintypes.DWORD), ("dwOutNUcastPkts", wintypes.DWORD),
            ("dwOutDiscards", wintypes.DWORD), ("dwOutErrors", wintypes.DWORD),
            ("dwOutQLen", wintypes.DWORD), ("dwDescrLen", wintypes.DWORD),
            ("bDescr", ctypes.c_ubyte * 256),
        ]  # fmt: skip

    row = MibIfRow()
    row.dwIndex = index.value
    if iphlpapi.GetIfEntry(ctypes.byref(row)) != 0:
        return None
    speed = row.dwSpeed / 1e6 if 0 < row.dwSpeed < 0xFFFFFFFF else None
    if row.dwType != IF_TYPE_IEEE80211:
        return Link(LAN, speed)
    signal, rate = _wlan()
    return Link(WLAN, rate or speed, signal)


def _wlan() -> tuple[float | None, float | None]:  # pragma: no cover - Windows only
    """The connected Wi-Fi's signal (%) and receive rate (Mbit/s), from the WLAN service."""
    import ctypes
    from ctypes import wintypes

    wlanapi = ctypes.WinDLL("wlanapi")

    class Guid(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]  # fmt: skip

    class InterfaceInfo(ctypes.Structure):
        _fields_ = [("InterfaceGuid", Guid), ("strInterfaceDescription", wintypes.WCHAR * 256),
                    ("isState", wintypes.DWORD)]  # fmt: skip

    class InterfaceList(ctypes.Structure):
        _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                    ("InterfaceInfo", InterfaceInfo * 1)]  # fmt: skip

    class Ssid(ctypes.Structure):
        _fields_ = [("uSSIDLength", wintypes.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]

    class Association(ctypes.Structure):
        _fields_ = [("dot11Ssid", Ssid), ("dot11BssType", wintypes.DWORD),
                    ("dot11Bssid", ctypes.c_ubyte * 6), ("dot11PhyType", wintypes.DWORD),
                    ("uDot11PhyIndex", wintypes.ULONG), ("wlanSignalQuality", wintypes.ULONG),
                    ("ulRxRate", wintypes.ULONG), ("ulTxRate", wintypes.ULONG)]  # fmt: skip

    class Connection(ctypes.Structure):
        _fields_ = [("isState", wintypes.DWORD), ("wlanConnectionMode", wintypes.DWORD),
                    ("strProfileName", wintypes.WCHAR * 256),
                    ("wlanAssociationAttributes", Association)]  # fmt: skip

    handle, version = wintypes.HANDLE(), wintypes.DWORD()
    if wlanapi.WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(handle)) != 0:
        return None, None
    listed = ctypes.POINTER(InterfaceList)()
    try:
        if wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(listed)) != 0:
            return None, None
        count = listed.contents.dwNumberOfItems
        infos = ctypes.cast(ctypes.byref(listed.contents.InterfaceInfo),
                            ctypes.POINTER(InterfaceInfo * count)).contents  # fmt: skip
        for info in infos:
            if info.isState != 1:  # wlan_interface_state_connected
                continue
            size, data = wintypes.DWORD(), ctypes.c_void_p()
            guid = ctypes.byref(info.InterfaceGuid)
            asked = (handle, guid, 7, None, ctypes.byref(size), ctypes.byref(data), None)
            if wlanapi.WlanQueryInterface(*asked) != 0:  # 7: wlan_intf_opcode_current_connection
                continue
            try:
                attrs = ctypes.cast(data, ctypes.POINTER(Connection)).contents
                association = attrs.wlanAssociationAttributes
                return float(association.wlanSignalQuality), association.ulRxRate / 1000
            finally:
                wlanapi.WlanFreeMemory(data)
        return None, None
    finally:
        if listed:
            wlanapi.WlanFreeMemory(listed)
        wlanapi.WlanCloseHandle(handle, None)

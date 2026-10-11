"""Devices: a message when a drive, a USB device or a game controller comes or goes, when a
controller's battery runs low, when a drive is nearly full and when an SSD runs hot (Windows).

Only *changes* are told: what is plugged in when the service starts is taken in silently,
or a controller that has been connected for hours would pop up at every start. The panel
itself and the hubs are no news, and a USB stick is told once, not as a drive and as a USB
device. Four changes at once (the computer woke up, a hub reset) are taken in silently too.

Windows tells when a device comes or goes (``CM_Register_Notification``); the service looks
then, and a slow poll catches what was missed. Where that does not work it polls every
couple of seconds. Everything only reads: see ``devices_windows``.
"""

from __future__ import annotations

import math
import re
import sys
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from libre_panel.i18n import t
from libre_panel.plugins import Service
from libre_panel.services import devices_windows as windows
from libre_panel.services.notices import WARNING, Kind

DEVICE = Kind("device", 1, 4.0)  # plugged in or out; a theme switches the kind off with toast.off
PLUGGED_S, UNPLUGGED_S = 4.0, 2.5  # how long a message stays
USB_ID = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}$")

# Seconds. Without Windows' notices drives and USB devices are looked at every DRIVE_TICK;
# with them, only to catch what was missed.
DRIVE_TICK = 2.0
USB_TICK = 2.0
FALLBACK_TICK = 10.0
IDLE_TICK = 1.0  # between two looks while nothing is going on (the controllers' batteries)
BUSY_TICK = 0.25  # while a new device settles
WAKE_SETTLE = 0.15  # a plugged device brings a dozen notices: gather them, then look once
CONTROLLER_WAIT = 2.0  # a wireless controller tells its battery a moment after it connects
CONTROLLER_MIN_AGE = 0.5  # one that does (or a wired one) is told after this long
USB_WAIT = 4.0  # a drive or a controller may come this long before the USB device it is
USB_MIN_AGE = 0.6  # a USB device is told at the earliest this long after it appeared...
USB_QUIET = 1.0  # ...and once Windows has said nothing new for this long
SAME_MOMENT = 0.2  # a drive told this close to a USB device is that device
MASS = 4  # this many changes at once: the computer woke up, a hub reset
STORAGE_TICK = 60.0
HEAT_HYSTERESIS = 5.0  # °C below the limit the warning is armed again
DEFAULT_HEAT_C = 70.0  # the limit of a drive that names none, if the options name none either

# Vendors of phones, by USB vendor id: such a device gets the phone and the maker's name.
PHONE_MAKERS = {
    "05AC": "Apple", "04E8": "Samsung", "18D1": "Google", "2717": "Xiaomi", "2A70": "OnePlus",
    "12D1": "Huawei", "22B8": "Motorola", "0FCE": "Sony", "1004": "LG", "0BB4": "HTC",
    "2D95": "Vivo", "22D9": "Oppo", "1EBF": "Fairphone", "19D2": "ZTE", "2A45": "Meizu",
}  # fmt: skip


def size_text(size: float) -> str:
    """A size as drives are sold (powers of 1000): 512 MB, 29.8 GB, 1.0 TB."""
    if size >= 1000**4:
        return f"{size / 1000**4:.1f} TB"
    if size >= 10 * 1000**3:
        return f"{size / 1000**3:.0f} GB"
    if size >= 1000**3:
        return f"{size / 1000**3:.1f} GB"
    return f"{size / 1000**2:.0f} MB"


def panel_ids() -> frozenset[str]:
    """``VID:PID`` (upper case) of every panel Libre Panel drives, so that the panel's own
    USB device never makes a message, whichever model is plugged in."""
    from libre_panel.devices.models import MODELS

    return frozenset(f"{vid:04X}:{pid:04X}" for model in MODELS for vid, pid in model.usb_ids)


def parse_ignored(raw: list[Any], report: Callable[[str], None]) -> frozenset[str]:
    """The ``usb_ignore`` option: ``"1234:abcd"`` each, as the set of ids in upper case."""
    found = set()
    for entry in raw:
        if isinstance(entry, str) and USB_ID.match(entry.strip()):
            found.add(entry.strip().upper())
        else:
            report(f"usb_ignore: {entry!r} is no USB id, write it as VID:PID, e.g. 1a86:7523")
    return frozenset(found)


# -- what happened ---------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """Something worth a message. ``what`` is one of ``drive-in``, ``drive-out``,
    ``controller-in``, ``controller-out``, ``controller-low``, ``usb-in``, ``usb-out``,
    ``space`` (a drive is nearly full) and ``heat`` (an SSD is hot); ``key`` names the
    device (the same device again replaces its message); ``data`` has the facts."""

    what: str
    key: str
    data: dict[str, Any] = field(default_factory=dict)


class Watcher:
    """Looks at the drives, controllers, USB devices and storage and says what changed.

    ``readers`` answer what is there (``devices_windows.Readers``, or a fake), ``emit`` is
    told each event. ``step(now)`` is one look; the service calls it from its thread, a test
    calls it with the time it likes. Nothing is told for what is there at the first look.
    """

    def __init__(
        self,
        readers: Any,
        options: dict[str, Any],
        emit: Callable[[Event], None],
        ignore: Collection[str] = frozenset(),
    ) -> None:
        self.readers, self.options, self.emit, self.ignore = readers, options, emit, ignore
        self.events = False  # Windows tells when devices come and go: the poll is only a net
        self.last_notice = -math.inf  # when Windows last told (set from its thread)
        self._drives: dict[str, dict[str, Any]] | None = None
        self._drives_due = 0.0
        self._controllers: dict[int, dict[str, Any]] | None = None
        self._connected: set[int] = set()  # controllers that were told as connected
        self._waiting: dict[int, float] = {}  # new controllers and since when they stand there
        self._weak: set[int] = set()  # controllers whose weak battery has been told
        self._usb: dict[str, dict[str, Any]] | None = None
        self._usb_due = 0.0
        self._usb_waiting: dict[str, tuple[float, dict[str, Any]]] = {}
        self._told = -math.inf  # when a drive or a controller was last told
        self._storage_due = 0.0
        self._full: set[str] = set()  # drives whose lack of space has been told
        self._hot: dict[str, str] = {}  # drives whose heat has been told: "warn" or "crit"

    @property
    def pause(self) -> float:
        """How long to wait before the next look."""
        return BUSY_TICK if self._waiting or self._usb_waiting else IDLE_TICK

    def hurry(self) -> None:
        """Windows says something changed: look at the drives and the USB devices now."""
        self._drives_due = self._usb_due = 0.0

    def step(self, now: float) -> None:
        o = self.options
        if o["drives"]:
            if now >= self._drives_due:
                self._drives_due = now + (FALLBACK_TICK if self.events else DRIVE_TICK)
                self._look_drives(self.readers.drives(), now)
        else:
            self._drives = None
        if o["controllers"]:
            self._look_controllers(self.readers.controllers(), now)
        else:
            self._controllers = None
        # USB last: a drive and a controller of this look are told by now, and the second
        # message of a stick (it is a drive too) drops away.
        if o["usb"]:
            if now >= self._usb_due:
                self._usb_due = now + (FALLBACK_TICK if self.events else USB_TICK)
                self._look_usb(self.readers.usb(self.ignore), now)
            self._deliver_usb(now)
        else:
            self._usb = None
            self._usb_waiting.clear()
        if o["storage"] and now >= self._storage_due:
            self._storage_due = now + STORAGE_TICK
            self._look_storage(self.readers.storage())

    def _tell(self, event: Event, now: float | None = None) -> None:
        if event.what.startswith(("drive", "controller")) and now is not None:
            self._told = now
        self.emit(event)

    # -- drives ------------------------------------------------------------------------

    def _look_drives(self, found: dict[str, dict[str, Any]], now: float) -> None:
        before, self._drives = self._drives, found
        if before is None:
            return
        came = [letter for letter in found if letter not in before]
        gone = [letter for letter in before if letter not in found]
        if len(came) + len(gone) >= MASS:
            return  # the computer woke up: taken in silently
        for letter in came:
            self._tell(
                Event("drive-in", f"drive:{letter}", {"letter": letter, **found[letter]}), now
            )
        for letter in gone:
            self._tell(
                Event("drive-out", f"drive:{letter}", {"letter": letter, **before[letter]}), now
            )

    # -- controllers ---------------------------------------------------------------------

    def _look_controllers(self, found: dict[int, dict[str, Any]], now: float) -> None:
        before, self._controllers = self._controllers, found
        if before is None:
            self._connected = set(found)
            # already weak at the start: not an event
            self._weak = {
                slot for slot, c in found.items() if c.get("level") is not None and c["level"] <= 1
            }
            return
        for slot in found:
            if slot not in before and slot not in self._connected:
                self._waiting.setdefault(slot, now)
        for slot in list(self._waiting):
            if slot not in found:
                del self._waiting[slot]  # gone again at once: nothing to tell
            elif now - self._waiting[slot] >= self._settle(found[slot]):
                del self._waiting[slot]
                self._connected.add(slot)
                self._tell(
                    Event("controller-in", f"controller:{slot}", {"slot": slot, **found[slot]}), now
                )
        for slot in list(self._connected):
            if slot not in found:
                self._connected.discard(slot)
                self._weak.discard(slot)
                self._tell(Event("controller-out", f"controller:{slot}", {"slot": slot}), now)
        for slot, c in found.items():
            if (
                slot not in self._connected
                or c.get("battery") not in (2, 3)
                or c.get("level") is None
            ):
                continue
            if c["level"] <= 1 and slot not in self._weak:
                self._weak.add(slot)
                self._tell(Event("controller-low", f"controller:{slot}", {"slot": slot, **c}), now)
            elif c["level"] >= 2:
                self._weak.discard(slot)

    def _settle(self, controller: dict[str, Any]) -> float:
        """How long a new controller must stand there before it is told. With Windows' notices a
        wired one, or a wireless one that has told its battery, is told after half a second;
        a wireless one that has not gets two seconds."""
        if not self.events:
            return CONTROLLER_WAIT
        ready = controller.get("battery") == windows.BATTERY_WIRED or (
            controller.get("battery") in (2, 3) and controller.get("level") is not None
        )
        return CONTROLLER_MIN_AGE if ready else CONTROLLER_WAIT

    # -- USB devices ---------------------------------------------------------------------

    def _look_usb(self, found: dict[str, dict[str, Any]], now: float) -> None:
        before, self._usb = self._usb, found
        if before is None:
            return
        came = [key for key in found if key not in before]
        gone = [key for key in before if key not in found]
        if len(came) + len(gone) >= MASS:
            self._usb_waiting.clear()  # the computer woke up, or a hub reset: silently
            return
        for key in came:
            self._usb_waiting[key] = (now, found[key])
        for key in gone:
            if self._usb_waiting.pop(key, None) is not None:
                continue  # gone again before it was told: nothing
            if now - self._told < USB_WAIT:
                continue  # the drive or the controller that it was has said so
            self._tell(Event("usb-out", f"usb:{key}", {"id": key, **before[key]}))

    def _deliver_usb(self, now: float) -> None:
        """Tell the USB devices that have settled. A device brings a dozen notices; it is
        there for good when Windows has been quiet for a second."""
        for key, (since, entry) in list(self._usb_waiting.items()):
            age = now - since
            if self.events:
                settled = age >= USB_WAIT or (
                    age >= USB_MIN_AGE and now - self.last_notice >= USB_QUIET
                )
            else:
                settled = age >= USB_WAIT
            if not settled:
                continue
            del self._usb_waiting[key]
            if self._usb is None or key not in self._usb:
                continue
            # A stick is a drive too: if the drive was told with it or since, that said it.
            if self._told >= since - (SAME_MOMENT if self.events else USB_TICK):
                continue
            self._tell(Event("usb-in", f"usb:{key}", {"id": key, **entry}))

    # -- storage -------------------------------------------------------------------------

    def _look_storage(self, found: dict[str, dict[str, Any]]) -> None:
        o = self.options
        self._full &= set(found)
        self._hot = {letter: level for letter, level in self._hot.items() if letter in found}
        for letter, drive in found.items():
            if not drive.get("total"):
                continue
            limit = max(o["min_free_gb"] * 1000**3, drive["total"] * o["min_free_percent"] / 100)
            if drive["free"] < limit:
                if letter not in self._full:
                    self._full.add(letter)
                    critical = drive["free"] < limit / 3
                    self._tell(
                        Event(
                            "space",
                            f"storage:{letter}",
                            {"letter": letter, "critical": critical, **drive},
                        )
                    )
            elif drive["free"] > limit * 1.2:  # so that a value at the limit does not flicker
                self._full.discard(letter)
            temp = drive.get("temp")
            if temp is None:
                continue
            own = o["ssd_warn_c"]
            hot_from = own if own > 0 else (drive.get("warn") or DEFAULT_HEAT_C)
            crit = drive.get("crit")
            if temp >= hot_from:
                level = "crit" if crit and temp >= crit else "warn"
                before = self._hot.get(letter)
                if before is None or (level == "crit" and before == "warn"):
                    self._hot[letter] = level
                    self._tell(
                        Event(
                            "heat",
                            f"ssdtemp:{letter}",
                            {
                                "letter": letter,
                                "critical": level == "crit",
                                "limit": hot_from,
                                **drive,
                            },
                        )  # fmt: skip
                    )
            elif temp < hot_from - HEAT_HYSTERESIS:
                self._hot.pop(letter, None)


# -- the messages ----------------------------------------------------------------------


def _share(part: float, whole: float) -> float:
    return max(0.0, min(1.0, part / whole)) if whole else 0.0


def _battery_text(controller: dict[str, Any]) -> str:
    """ "Wired" or the battery's state; empty where it is not known."""
    if controller.get("battery") == windows.BATTERY_WIRED:
        return t("Wired")
    states = {
        0: t("Battery empty"), 1: t("Battery low"), 2: t("Battery medium"), 3: t("Battery full"),
    }  # fmt: skip
    if controller.get("battery") in (2, 3):
        return states.get(controller.get("level"), "")
    return ""


def describe(event: Event) -> dict[str, Any]:
    """The message for an event: the arguments of ``host.notify``."""
    d = event.data
    base: dict[str, Any] = {"kind": DEVICE.name, "rank": DEVICE.rank, "key": event.key}
    letter = d.get("letter", "")
    match event.what:
        case "drive-in" | "drive-out":
            drive = d.get("name") or d.get("label") or t("Drive {letter}:", letter=letter)
            label = t("{bus} drive {letter}:", bus=d["bus"], letter=letter)
            if event.what == "drive-out":
                return {**base, "text": t("{name} removed", name=drive), "icon": "stick",
                        "seconds": UNPLUGGED_S, "payload": {"label": label}}  # fmt: skip
            text, payload = drive, {"label": label}
            if d["total"]:
                text = t("{name} · {free} free of {total}", name=drive,
                         free=size_text(d["free"]), total=size_text(d["total"]))  # fmt: skip
                payload["progress"] = _share(d["total"] - d["free"], d["total"])
            return {**base, "text": text, "icon": "stick", "seconds": PLUGGED_S, "payload": payload}
        case "controller-in" | "controller-out" | "controller-low":
            payload = {"label": t("Controller · player {number}", number=d["slot"] + 1)}
            if event.what == "controller-out":
                return {**base, "text": t("Disconnected"), "icon": "gamepad",
                        "seconds": UNPLUGGED_S, "payload": payload}  # fmt: skip
            if d.get("battery") in (2, 3) and d.get("level") is not None:
                payload["progress"] = d["level"] / 3
            if event.what == "controller-low":
                text = t("Battery empty") if d["level"] == 0 else t("Battery low")
                return {**base, "text": text, "icon": "gamepad", "rank": WARNING.rank,
                        "level": "error" if d["level"] == 0 else "warning",
                        "seconds": WARNING.seconds, "payload": payload}  # fmt: skip
            battery = _battery_text(d)
            text = f"{t('Connected')} · {battery}" if battery else t("Connected")
            return {
                **base,
                "text": text,
                "icon": "gamepad",
                "seconds": PLUGGED_S,
                "payload": payload,
            }
        case "usb-in" | "usb-out":
            maker = PHONE_MAKERS.get(d["vid"])
            label = t("Phone · {maker}", maker=maker) if maker else t("USB device")
            name = d.get("name") or f"{d['vid']}:{d['pid']}"
            if event.what == "usb-out":
                text, seconds = t("{name} disconnected", name=name), UNPLUGGED_S
            else:
                text, seconds = name, PLUGGED_S
            return {**base, "text": text, "icon": "phone" if maker else "usb", "seconds": seconds,
                    "payload": {"label": label}}  # fmt: skip
        case "space":
            name = f" · {d['name']}" if d.get("name") else ""
            return {
                "kind": WARNING.name, "rank": WARNING.rank, "key": event.key, "icon": "disk",
                "text": t("Only {free} free", free=size_text(d["free"])),
                "level": "error" if d["critical"] else "warning", "seconds": WARNING.seconds,
                "payload": {"label": t("Drive {letter}:", letter=letter) + name,
                            "progress": _share(d["total"] - d["free"], d["total"])},
            }  # fmt: skip
        case "heat":
            name = f" · {d['name']}" if d.get("name") else ""
            return {
                "kind": WARNING.name, "rank": WARNING.rank, "key": event.key, "icon": "ssd",
                "text": t("{temp} °C · limit {limit} °C", temp=f"{d['temp']:.0f}",
                          limit=f"{d['limit']:.0f}"),
                "level": "error" if d["critical"] else "warning", "seconds": WARNING.seconds,
                "payload": {"label": t("SSD {letter}:", letter=letter) + name,
                            "progress": _share(d["temp"], d.get("crit") or 90.0)},
            }  # fmt: skip
    raise ValueError(f"unknown device event {event.what!r}")


# -- the service -----------------------------------------------------------------------


class Devices(Service):
    """Messages for drives, USB devices and controllers that come and go, and for storage."""

    name = "devices"
    api = 1
    options = {
        "drives": ("bool", True),
        "usb": ("bool", True),
        "controllers": ("bool", True),
        "storage": ("bool", True),
        "usb_ignore": ("list", []),
        "min_free_gb": ("number", 25.0),
        "min_free_percent": ("number", 5.0),
        "ssd_warn_c": ("number", 0.0),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        readers: Any = None,
        notifier: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(host, options)
        self.clock = clock
        on_windows = sys.platform == "win32"
        self.readers = (
            readers if readers is not None else (windows.Readers() if on_windows else None)
        )
        self.notifier = (
            notifier if notifier is not None else (windows.ChangeNotifier() if on_windows else None)
        )
        mine = parse_ignored(options["usb_ignore"], lambda text: host.log.warning("%s", text))
        self.watcher = Watcher(self.readers, options, self.tell, panel_ids() | mine)
        self._wake = threading.Event()
        self._ended = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: str | None = None

    def start(self) -> None:
        if self.readers is None:
            self.host.log.warning("devices: this works on Windows only")
            return
        self._thread = threading.Thread(target=self._run, name="devices", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._ended.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(3.0)
            self._thread = None

    def tell(self, event: Event) -> None:
        self.host.notify(**describe(event))

    def _noticed(self) -> None:
        """Windows says a device came or went (in a thread of Windows)."""
        self.watcher.last_notice = self.clock()
        self._wake.set()

    def _run(self) -> None:
        notifier = self.notifier
        self.watcher.events = bool(notifier is not None and notifier.start(self._noticed))
        if not self.watcher.events:
            self.host.log.info("devices: no notices from Windows, looking every few seconds")
        try:
            while not self._ended.is_set():
                woken = self._wake.wait(self.watcher.pause)
                if self._ended.is_set():
                    break
                if woken:
                    if self._ended.wait(WAKE_SETTLE):  # a dozen notices for one device: gather them
                        break
                    self._wake.clear()
                    self.watcher.hurry()
                try:
                    self.watcher.step(self.clock())
                    self._report(None)
                except Exception as exc:  # one failed look must not end the service
                    self._report(f"{type(exc).__name__}: {exc}")
        finally:
            if notifier is not None:
                notifier.stop()

    def _report(self, failure: str | None) -> None:
        """Say a new failure once, and when it is over."""
        if failure != self._failure:
            if failure:
                self.host.log.warning("looking at devices failed: %s", failure)
            else:
                self.host.log.info("looking at devices works again")
            self._failure = failure

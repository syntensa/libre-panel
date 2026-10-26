"""Waveshare's USB monitors (experimental): the display draws the theme on its own card, and this
service sends it Libre Panel's readings for it, once a second; with the brightness and the
screen turned upside down where they are set. Nothing is written on the card
(devices/waveshare.py has no command that would).

``[services.waveshare]``::

    port = ""          # "": the display is looked for; else its port ("COM5", "/dev/ttyACM0")
    brightness = 0     # 1-100 sets it; 0 leaves it as it is
    flip = ""          # "": as it is; "normal" or "upside-down"
    units = false      # the values with their units, as Waveshare's program sends them
    every_s = 1.0
    theme = ""         # a theme on the card to show ("/data/theme/480x480/Space Suit.Theme")
    rotate = []        # themes on the card shown in turn, at random,
    rotate_min = 0     # each for so many minutes (0: no turns)

The display's themes name their values with the keys of Waveshare's own program, in English
(``Cpu_Temp``), and some with Chinese ones (``Cpu温度``): both are sent. Waveshare's program
writes its values with their units (``75%``, ``24RPM``, ``31℃``) and the weekday short
(``Mon``); here the numbers go alone unless ``units`` is on, since themes often show the units
themselves. Waveshare's program and this service cannot both have the display: the one that
opens its port first keeps it.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from libre_panel.devices import waveshare
from libre_panel.devices.base import DeviceError, leave_place, take_place
from libre_panel.i18n import format_date
from libre_panel.plugins import Service

log = logging.getLogger(__name__)

RETRY_S = 5.0  # how long a search that found nothing waits before the next
QUIET_S = 60.0  # and one whose port did not answer as the display does (it is asked seldom)
FLIPS = {"normal": False, "upside-down": True}


def whole(value: Any) -> str | None:
    return str(round(value)) if isinstance(value, int | float) else None


def tenth(value: Any) -> str | None:
    return f"{value:.1f}" if isinstance(value, int | float) else None


def kilo(value: Any) -> str | None:
    """Bytes a second as the display's KB/s: a tenth below 100."""
    if not isinstance(value, int | float):
        return None
    rate = value / 1024
    return f"{rate:.1f}" if rate < 100 else str(round(rate))


def text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


# The display's keys for Libre Panel's readings: (its English key, its Chinese key, the
# reading, how it is written, the unit Waveshare's program puts after it). "Cpu_Freq" is the
# processor's load: so Waveshare named it.
KEYS: tuple[tuple[str, str, str, Callable[[Any], str | None], str], ...] = (
    ("Cpu_Freq", "Cpu使用率", "cpu.load", whole, "%"),
    ("Cpu_Temp", "Cpu温度", "cpu.temp", whole, "℃"),
    ("Cpu_Clock", "Cpu频率", "cpu.freq", whole, "MHZ"),
    ("Cpu_Power", "Cpu功耗", "cpu.power", whole, "W"),
    ("Cpu_Model", "Cpu型号", "cpu.name", text, ""),
    ("Gpu_Load", "显卡利用率", "gpu.load", whole, "%"),
    ("GPU_Temp", "显卡温度", "gpu.temp", whole, "℃"),
    ("Gpu_Clock", "显卡频率", "gpu.freq", whole, "MHZ"),
    ("Gpu_Power", "显卡功耗", "gpu.power", whole, "W"),
    ("Gpu_Fan", "显卡风扇", "gpu.fan", whole, "RPM"),
    ("Gpu_Usedata", "已用显存", "gpu.mem.used", whole, "MB"),
    ("Gpu_Model", "显卡型号", "gpu.name", text, ""),
    ("Memory_Load", "内存利用率", "mem.load", whole, "%"),
    ("Memory_Used", "已用内存", "mem.used", tenth, "GB"),
    ("Memory_Available", "可用内存", "mem.free", tenth, "GB"),
    ("RAM_Model_0", "内存型号_0", "mem.name", text, ""),
    ("Download_Speed", "下载速度", "net.down", kilo, "KB/s"),
    ("Upload_Speed", "上传速度", "net.up", kilo, "KB/s"),
    ("vol", "音量", "audio.volume", whole, ""),
    ("temperature", "气温", "weather.temperature", whole, "℃"),
    ("weather", "天气", "weather.description", text, ""),
)
# Fans by what their name says (the sources name them by chip and header: fan.board.cpu_fan,
# fan.lpc.pump): the display's key, its Chinese key, and the word looked for
FANS = (("Cpu_Fan", "Cpu风扇", "cpu"), ("Pump_Fan", "水冷风扇", "pump"))
PARTITIONS = 8  # the drives' use: Libre Panel's disk.1 to disk.8, the display's 0 to 7
WANTED = (*(key[2] for key in KEYS), "fan.*", "disk.load",
          *(f"disk.{n}.load" for n in range(1, PARTITIONS + 1)))  # fmt: skip
# the clock as Waveshare's program writes it (the weekday short, in the app's language here):
# the display's key, its Chinese key where it has one, and the format
CLOCK = (("time", "时间", "%H:%M:%S"), ("date", "日期", "%Y-%m-%d"), ("week", "星期", "%a"),
         ("year", "", "%Y"), ("month", "", "%m"), ("day", "", "%d"), ("hour", "时", "%H"),
         ("minute", "分", "%M"), ("second", "秒", "%S"))  # fmt: skip


def fan(snapshot: Any, word: str) -> Any:
    """The first fan whose reading's key has ``word`` in it (``fan.cpu`` first), or None."""
    keys = sorted(getattr(snapshot, "readings", None) or {})
    for key in (f"fan.{word}", *keys):
        if key.startswith("fan.") and word in key.lower() and not key.endswith(".percent"):
            value = snapshot.value(key)
            if isinstance(value, int | float):
                return value
    return None


def values(snapshot: Any, now: datetime, units: bool = False) -> dict[str, str]:
    """What the display gets from ``snapshot`` at ``now``: each value under both of its keys,
    and only those Libre Panel has; with ``units``, each number with its unit."""
    out: dict[str, str] = {}

    def put(english: str, chinese: str, value: str | None, unit: str = "") -> None:
        if value is None:
            return
        value = value + unit if units else value
        out[english] = value
        if chinese:
            out[chinese] = value

    read = snapshot.value if snapshot is not None else (lambda key: None)
    for english, chinese, reading, how, unit in KEYS:
        put(english, chinese, how(read(reading)), unit)
    for english, chinese, word in FANS:
        put(english, chinese, whole(fan(snapshot, word)) if snapshot is not None else None, "RPM")
    for n in range(1, PARTITIONS + 1):
        load = read(f"disk.{n}.load")
        if n == 1 and load is None:
            load = read("disk.load")
        put(f"Partition_Usage_{n - 1}", f"分区利用率_{n - 1}", whole(load), "%")
    for english, chinese, fmt in CLOCK:
        put(english, chinese, format_date(now, fmt))
    return out


def kind_of(reason: str) -> str:
    """What stands in the way, for the card: ``busy`` (another program has the port),
    ``denied`` (no right to it), ``silent`` (a port that does not answer as the display does),
    else ``absent``."""
    lowered = reason.lower()
    if "access is denied" in lowered or "busy" in lowered or "resource temporarily" in lowered:
        return "busy"
    if "permission denied" in lowered:
        return "denied"
    if "did not answer" in lowered:
        return "silent"
    return "absent"


class Waveshare(Service):
    """Libre Panel's readings in the themes of a Waveshare USB monitor."""

    name = "waveshare"
    api = 1
    options = {
        "port": ("string", ""),
        "brightness": ("int", 0),
        "flip": ("string", ""),
        "units": ("bool", False),
        "every_s": ("number", 1.0),
        "theme": ("string", ""),
        "rotate": ("list", []),
        "rotate_min": ("number", 0.0),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        connect: Callable[[str], tuple[waveshare.Link, str, dict]] = waveshare.connect,
        now: Callable[[], datetime] = datetime.now,
        pick: Callable[[list[str]], str] = random.choice,
    ) -> None:
        super().__init__(host, options)
        self.port = str(options["port"]).strip()
        self.brightness = max(0, min(100, int(options["brightness"])))
        self.flip = FLIPS.get(str(options["flip"]).strip())
        self.units = bool(options["units"])
        self.every = max(0.25, float(options["every_s"]))
        self.theme = str(options["theme"]).strip()
        self.rotate = [str(path) for path in options["rotate"] if str(path).strip()]
        self.rotate_s = max(0.0, float(options["rotate_min"])) * 60
        self._connect, self._now, self._pick = connect, now, pick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "searching"}

    # -- the service ------------------------------------------------------------------------

    def start(self) -> None:
        self.host.want(*WANTED)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="waveshare", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=waveshare.ANSWER_S + 1)

    def summary(self) -> dict[str, Any]:
        """``state``: ``connected`` (with ``port``, ``inch``, ``size`` and ``version`` where the
        display says them, its ``themes`` as ``{id, name}`` and the ``theme`` this service last
        showed), ``searching``, ``absent``, ``busy``, ``denied`` or ``silent`` (with the
        ``reason``)."""
        with self._lock:
            return dict(self._state)

    # -- its thread ---------------------------------------------------------------------------

    def _say(self, **state: Any) -> None:
        with self._lock:
            self._state = state

    def _add(self, **fields: Any) -> None:
        with self._lock:
            self._state = {**self._state, **fields}

    def _run(self) -> None:
        said_why = ""
        while not self._stop.is_set():
            try:
                link, port, about = self._connect(self.port)
            except DeviceError as exc:
                reason = str(exc)
                kind = kind_of(reason)
                self._say(state=kind, reason=reason)
                if reason != said_why:
                    log.info("Waveshare USB monitor: %s", reason)
                    said_why = reason
                self._stop.wait(QUIET_S if kind == "silent" else RETRY_S)
                continue
            said_why = ""
            if not take_place(port):  # (a panel of Libre Panel has it after all)
                link.close()
                self._stop.wait(RETRY_S)
                continue
            try:
                self._feed(link, port, about)
            except DeviceError as exc:
                log.warning("Waveshare USB monitor on %s: %s", port, exc)
                self._say(state="absent", reason=str(exc))
            finally:
                link.close()
                leave_place(port)
            self._stop.wait(1.0)

    def _feed(self, link: waveshare.Link, port: str, about: dict) -> None:
        try:
            version = link.version()
        except DeviceError:
            version = ""  # (the handshake was answered: values may still be taken)
        width, height = about.get("screen_w") or "", about.get("screen_h") or ""
        size = f"{width}×{height}" if width and height else ""
        inch = about.get("screen_inch") or ""
        self._say(state="connected", port=port, inch=inch, size=size, version=version)
        log.info("Waveshare USB monitor on %s (%s)", port, version or "version not said")
        if self.brightness:
            link.set_brightness(self.brightness)
        if self.flip is not None:
            link.set_flipped(self.flip)
        try:
            themes = link.themes()
        except DeviceError:
            themes = []  # (an older firmware may not tell: the values go on all the same)
        self._add(themes=[{"id": path, "name": waveshare.theme_name(path)} for path in themes],
                  theme="")  # fmt: skip
        # a theme named that is not on the card (any more) is passed over
        known = (lambda path: path in themes) if themes else (lambda path: True)
        if self.theme and known(self.theme):
            self._show(link, self.theme)
        turns = [path for path in self.rotate if known(path)]
        next_turn = time.monotonic() + self.rotate_s
        while not self._stop.is_set():
            began = time.monotonic()
            if self.rotate_s and turns and began >= next_turn:
                current = self.summary().get("theme")
                self._show(link, self._pick([p for p in turns if p != current] or turns))
                next_turn = began + self.rotate_s
            link.send_values(values(self.host.snapshot(), self._now(), self.units))
            self._stop.wait(max(0.0, self.every - (time.monotonic() - began)))

    def _show(self, link: waveshare.Link, path: str) -> None:
        """A theme on the card on the display; the picture turned again as set (each theme
        brings its own orientation, which the display takes on as it loads it)."""
        link.show_theme(path)
        if self.flip is not None:
            link.set_flipped(self.flip)
        self._add(theme=path)
        log.info("Waveshare USB monitor: theme %s", waveshare.theme_name(path))

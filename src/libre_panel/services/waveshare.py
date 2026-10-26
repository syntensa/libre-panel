"""Waveshare's USB monitors (experimental): the display draws the theme on its own card, and this
service sends it Libre Panel's readings for it, once a second; with the brightness and the
screen turned upside down where they are set. Nothing is written on the card
(devices/waveshare.py has no command that would).

``[services.waveshare]``::

    port = ""          # "": the display is looked for; else its port ("COM5", "/dev/ttyACM0")
    brightness = 0     # 1-100 sets it; 0 leaves it as it is
    flip = ""          # "": as it is; "normal" or "upside-down"
    every_s = 1.0

The display's themes name their values with the keys of Waveshare's own program, in English
and in Chinese (``Cpu_Temp`` and ``Cpu温度``): both are sent, as numbers without their units
(the themes have those). Waveshare's program and this service cannot both have the display:
the one that opens its port first keeps it.
"""

from __future__ import annotations

import logging
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
    """Bytes a second as the display's KB/s."""
    return str(round(value / 1024)) if isinstance(value, int | float) else None


def text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


# The display's keys for Libre Panel's readings: (its English key, its Chinese key, the
# reading, how it is written). "Cpu_Freq" is the processor's load: so Waveshare named it.
KEYS: tuple[tuple[str, str, str, Callable[[Any], str | None]], ...] = (
    ("Cpu_Freq", "Cpu使用率", "cpu.load", whole),
    ("Cpu_Temp", "Cpu温度", "cpu.temp", whole),
    ("Cpu_Clock", "Cpu频率", "cpu.freq", whole),
    ("Cpu_Power", "Cpu功耗", "cpu.power", whole),
    ("Cpu_Fan", "Cpu风扇", "fan.cpu", whole),
    ("Cpu_Model", "Cpu型号", "cpu.name", text),
    ("Gpu_Load", "显卡利用率", "gpu.load", whole),
    ("GPU_Temp", "显卡温度", "gpu.temp", whole),
    ("Gpu_Clock", "显卡频率", "gpu.freq", whole),
    ("Gpu_Power", "显卡功耗", "gpu.power", whole),
    ("Gpu_Fan", "显卡风扇", "gpu.fan", whole),
    ("Gpu_Usedata", "已用显存", "gpu.mem.used", whole),  # MB
    ("Gpu_Model", "显卡型号", "gpu.name", text),
    ("Memory_Load", "内存利用率", "mem.load", whole),
    ("Memory_Used", "已用内存", "mem.used", tenth),  # GB
    ("Memory_Available", "可用内存", "mem.free", tenth),
    ("RAM_Model_0", "内存型号_0", "mem.name", text),
    ("Download_Speed", "下载速度", "net.down", kilo),
    ("Upload_Speed", "上传速度", "net.up", kilo),
    ("vol", "音量", "audio.volume", whole),
    ("temperature", "气温", "weather.temperature", whole),
    ("weather", "天气", "weather.description", text),
)
PARTITIONS = 8  # the drives' use: Libre Panel's disk.1 to disk.8, the display's 0 to 7
WANTED = (*(reading for _, _, reading, _ in KEYS), "disk.load",
          *(f"disk.{n}.load" for n in range(1, PARTITIONS + 1)))  # fmt: skip


def values(snapshot: Any, now: datetime) -> dict[str, str]:
    """What the display gets from ``snapshot`` at ``now``: each value under both of its keys,
    and only those Libre Panel has."""
    out: dict[str, str] = {}

    def put(english: str, chinese: str, value: str | None) -> None:
        if value is not None:
            out[english] = out[chinese] = value

    read = snapshot.value if snapshot is not None else (lambda key: None)
    for english, chinese, reading, how in KEYS:
        put(english, chinese, how(read(reading)))
    for n in range(1, PARTITIONS + 1):
        load = read(f"disk.{n}.load")
        if n == 1 and load is None:
            load = read("disk.load")
        put(f"Partition_Usage_{n - 1}", f"分区利用率_{n - 1}", whole(load))
    clock = {"time": "%H:%M:%S", "date": "%Y-%m-%d", "week": "%A", "hour": "%H",
             "minute": "%M", "second": "%S"}  # fmt: skip
    chinese = {"time": "时间", "date": "日期", "week": "星期", "hour": "时", "minute": "分",
               "second": "秒"}  # fmt: skip
    for key, fmt in clock.items():
        put(key, chinese[key], format_date(now, fmt))
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
        "every_s": ("number", 1.0),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        connect: Callable[[str], tuple[waveshare.Link, str, dict]] = waveshare.connect,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(host, options)
        self.port = str(options["port"]).strip()
        self.brightness = max(0, min(100, int(options["brightness"])))
        self.flip = FLIPS.get(str(options["flip"]).strip())
        self.every = max(0.25, float(options["every_s"]))
        self._connect, self._now = connect, now
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
        display says them), ``searching``, ``absent``, ``busy``, ``denied`` or ``silent`` (with
        the ``reason``)."""
        with self._lock:
            return dict(self._state)

    # -- its thread ---------------------------------------------------------------------------

    def _say(self, **state: Any) -> None:
        with self._lock:
            self._state = state

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
        while not self._stop.is_set():
            began = time.monotonic()
            link.send_values(values(self.host.snapshot(), self._now()))
            self._stop.wait(max(0.0, self.every - (time.monotonic() - began)))

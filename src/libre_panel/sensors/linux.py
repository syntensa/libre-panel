"""Linux sensors, read without root: the kernel's hwmon chips, the graphics card, the power.

- **Temperatures and fans** of every hwmon chip: the processor (k10temp, coretemp, zenpower),
  the mainboard's Super I/O chip (nct6775, it87, ...: ``fan.lpc.fan_1`` as Windows names it),
  the board's embedded controller (asus_ec_sensors), NVMe and SATA drives (nvme, drivetemp) and
  the memory modules (spd5118, jc42: ``mem.temp``). The keys are those PsutilProvider always
  gave (``temp.k10temp.tctl``); ``cpu.temp`` and ``mem.temp`` repeat the one that counts.
- **The processor's power** (``cpu.power``) from the energy counters of RAPL, where the user
  may read them (``libre-panel system-setup --linux`` can allow it), else from the chips that
  report it (zenpower, fam15h_power, amd_energy).
- **The graphics card** (``gpu.*``): AMD's amdgpu driver (temperature edge, junction and memory,
  fan, power, clocks, load, video memory), Intel's i915 and xe (power, clock, temperatures
  where the driver has them), NVIDIA's proprietary driver through NVML (``nvml.py``).

Reading is cheap: the files are found once, a rescan every few seconds finds chips that
appear (a module loaded by ``system-setup``, a USB cooler), and a reading is one tiny file.
What can take long or wake something (drives, memory on the SMBus) is read by a thread of its
own, and a card that is powered down to save energy is left alone.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from libre_panel.sensors import gpus, hwmon, nvml, rapl
from libre_panel.sensors.base import Reading, SensorProvider, wanted
from libre_panel.sensors.gpus import Card
from libre_panel.sensors.hwmon import Channel, Chip, Item

log = logging.getLogger(__name__)

ROOT = Path("/")
RESCAN_SECONDS = 10.0  # chips come and go: a module loaded, a cooler plugged in
SLOW_SECONDS = 5.0  # how often the drives' and the memory's temperatures are read
SLOW_FRESH = 30.0  # an older reading of those is dropped, not shown standing still
FIRST_PASS_SECONDS = 0.25  # the first reading waits this long for the slow ones
MIB = 1024 * 1024

# Where cpu.temp comes from: the chips in order, and in them the sensors in order ("" is a
# sensor without a label). Tdie is the temperature of the die where a chip gives Tctl as well,
# which on some processors has an offset added to it.
CPU_TEMP_CHIPS = ("coretemp", "k10temp", "zenpower", "zenpower3", "cpu_thermal", "cpu-thermal")
CPU_TEMP_ZONES = ("x86_pkg_temp", "acpitz")  # thermal zones: where no chip has a temperature
CPU_TEMP_LABELS = ("Package id 0", "Tdie", "Tctl", "")
POWER_CHIPS = ("zenpower", "zenpower3", "fam15h_power")  # chips with power channels of their own


@dataclass
class GpuChannels:
    """The channels of the main card's hwmon chip that make the ``gpu.*`` readings."""

    temp: Channel | None
    hotspot: Channel | None
    memory_temp: Channel | None
    fan: Channel | None
    pwm: Channel | None
    power: Channel | None
    energy: rapl.Joules | None  # a card that counts energy (Intel's) instead of telling power
    clock: Channel | None
    memory_clock: Channel | None


def gpu_channels(chip: Chip, clock: Callable[[], float] = time.monotonic) -> GpuChannels:
    temps, energy, clocks = chip.of("temp"), chip.of("energy"), chip.of("freq")
    return GpuChannels(
        chip.labelled("temp", "edge", "gpu", "pkg", "core", "") or (temps[0] if temps else None),
        chip.labelled("temp", "junction", "hotspot", "hot spot"),
        chip.labelled("temp", "mem", "vram", "memory"),
        next(iter(chip.of("fan")), None),
        next(iter(chip.of("pwm")), None),
        next(iter(chip.of("power")), None),
        rapl.Joules([rapl.Counter(energy[0].path)], clock) if energy else None,
        chip.labelled("freq", "sclk", "gfx") or (clocks[0] if clocks else None),
        chip.labelled("freq", "mclk", "mem"),
    )


@dataclass
class Plan:
    """What the last scan found: the chips with their keys, and where the ``cpu.*`` and
    ``gpu.*`` readings come from. Replaced as a whole, so that a reading never sees half of one."""

    signature: tuple[Any, ...] = ()
    chips: list[Chip] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    fast: list[Item] = field(default_factory=list)
    slow: list[Item] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # the slow ones' names (drive models)
    keys: dict[Channel, str] = field(default_factory=dict)
    cpu_temp: Item | None = None
    memory: list[Item] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    card: Card | None = None  # the card the gpu.* readings come from
    channels: GpuChannels | None = None  # its hwmon chip
    nvidia: list[tuple[Card, nvml.Device]] = field(default_factory=list)
    package_power: rapl.Joules | None = None  # the processor's power from RAPL ...
    socket_power: rapl.Joules | None = None  # ... or from amd_energy's sockets ...
    chip_power: list[Channel] = field(default_factory=list)  # ... or from a chip that tells it


def cpu_temp_item(items: list[Item]) -> Item | None:
    """The sensor behind cpu.temp: the first processor chip there is, and its package sensor."""
    for chip_name in (*CPU_TEMP_CHIPS, *CPU_TEMP_ZONES):
        found = [i for i in items if i.chip.name == chip_name and i.channel.kind == "temp"]
        if not found:
            continue
        for label in CPU_TEMP_LABELS:
            for item in found:
                if item.channel.label.lower() == label.lower():
                    return item
        return found[0]
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


class LinuxProvider(SensorProvider):
    """Temperatures, fans, power, clocks and video memory of this Linux PC; see the module."""

    name = "linux"

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        opener: Callable[[], nvml.Nvml | None] | None = None,
    ) -> None:
        super().__init__(options)
        self.root = ROOT if root is None else Path(root)
        self._clock = clock
        self._opener = opener or nvml.shared
        self._prefer = _text(self.options.get("gpu"))
        self._use_nvml = _text(self.options.get("nvidia", "auto")).lower() != "off"
        self._plan = Plan()
        self._scanned_at: float | None = None
        self._lock = threading.RLock()
        self._slow: dict[str, tuple[float, float]] = {}  # key -> (when read, value)
        self._slow_lock = threading.Lock()
        self._first = threading.Event()  # the slow thread has read once
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def follow_config(self, config: Any) -> None:
        """``gpu`` under ``[sensors.linux]`` (or, as everywhere else, under
        ``[sensors.librehardwaremonitor]``) picks the card by a part of its name."""
        tables = config.sensors.options
        prefer = _text(tables.get("linux", {}).get("gpu")) or _text(
            tables.get("librehardwaremonitor", {}).get("gpu")
        )
        if prefer != self._prefer:
            self._prefer = prefer
            self._scanned_at = None  # the card is chosen again at the next reading

    # -- finding what there is -------------------------------------------------------------

    @staticmethod
    def _adapters() -> list[Any]:
        """The cards by the names ``gpu.name`` gives them: their PCI slots tie a name to the
        folders of the kernel, so that the readings are those of the card that is named."""
        from libre_panel.sensors import hardware

        try:
            return list(hardware.scan().adapters)
        except Exception:  # noqa: BLE001 - names are not worth a failing reading
            log.debug("linux: no names of the graphics cards", exc_info=True)
            return []

    def scan(self, now: float | None = None) -> Plan:
        """Look at the chips and cards again; the plan is replaced if anything is new."""
        with self._lock:
            self._scanned_at = self._clock() if now is None else now
            chips = hwmon.scan(self.root)
            if not any(chip.of("temp") for chip in chips):
                chips += hwmon.thermal_zones(self.root)
            cards = gpus.find_cards(self.root)
            library = self._library(cards)
            counters = rapl.package_counters(self.root)
            signature = (
                tuple((str(chip.path), chip.name, len(chip.channels)) for chip in chips),
                tuple((card.slot, card.driver, card.hwmon) for card in cards),
                library is not None,
                self._prefer,
                tuple(counter.path for counter in counters),
            )
            if signature != self._plan.signature:
                self._plan = self._make_plan(signature, chips, cards, library, counters)
                self._start_slow()
            return self._plan

    def _make_plan(
        self,
        signature: tuple[Any, ...],
        chips: list[Chip],
        cards: list[Card],
        library: nvml.Nvml | None,
        counters: list[rapl.Counter],
    ) -> Plan:
        plan = Plan(signature=signature, chips=chips, cards=cards)
        plan.items = hwmon.name_items(chips)
        plan.fast = [item for item in plan.items if not item.chip.slow]
        plan.slow = [item for item in plan.items if item.chip.slow]
        plan.names = {item.key: self._slow_name(item) for item in plan.slow}
        plan.keys = {item.channel: item.key for item in plan.items}
        plan.cpu_temp = cpu_temp_item(plan.items)
        plan.memory = [
            item
            for item in plan.items
            if item.chip.kind == "memory" and item.channel.kind == "temp"
        ]
        plan.card = gpus.primary(cards, self._adapters(), self._prefer)
        if plan.card is not None and plan.card.hwmon:
            chip = next((c for c in chips if c.path.name == plan.card.hwmon), None)
            plan.channels = gpu_channels(chip, self._clock) if chip is not None else None
        if library is not None:
            plan.nvidia = self._nvidia_devices(cards, library)
        if counters:
            plan.package_power = rapl.Joules(counters, self._clock)
        sockets = [
            rapl.Counter(channel.path)
            for chip in chips
            if chip.name == "amd_energy"
            for channel in chip.of("energy")
            if channel.label.lower().startswith("esocket")
        ]
        if sockets:
            plan.socket_power = rapl.Joules(sockets, self._clock)
        plan.chip_power = [
            channel for chip in chips if chip.name in POWER_CHIPS for channel in chip.of("power")
        ]
        return plan

    def _library(self, cards: list[Card]) -> nvml.Nvml | None:
        """NVIDIA's library, asked for only where the proprietary driver runs a card that is on
        (starting the library would wake a card that sleeps)."""
        awake = [
            card
            for card in cards
            if card.vendor == gpus.NVIDIA and card.driver == "nvidia" and not gpus.suspended(card)
        ]
        if not self._use_nvml or not awake:
            return None
        try:
            return self._opener()
        except Exception:  # noqa: BLE001 - a library that fails is no NVIDIA card
            log.debug("linux: NVML did not start", exc_info=True)
            return None

    @staticmethod
    def _nvidia_devices(cards: list[Card], library: nvml.Nvml) -> list[tuple[Card, nvml.Device]]:
        found = []
        mine = [c for c in cards if c.vendor == gpus.NVIDIA and c.driver == "nvidia"]
        for index, card in enumerate(mine):
            device = library.by_slot(card.slot) if card.slot else None
            device = device or library.by_index(index)  # the library lists them in bus order
            if device is not None:
                found.append((card, device))
        return found

    @staticmethod
    def _slow_name(item: Item) -> str:
        """A drive is named by what it is, its model, as Windows does; the kernel tells it."""
        if item.chip.kind != "drive":
            return item.name
        model = hwmon.read_text(item.chip.base / "device" / "model") or hwmon.read_text(
            item.chip.base / "model"
        )
        if not model:
            return item.name
        label = item.channel.label
        return f"{model} {label}" if label and label.lower() != "composite" else model

    # -- the thread of the slow ones ---------------------------------------------------------

    def _start_slow(self) -> None:
        if not self._plan.slow or self._stop.is_set():
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._slow_loop, name="sensors-linux", daemon=True
            )
            self._thread.start()

    def _slow_loop(self) -> None:
        while not self._stop.is_set():
            self.read_slow()
            self._first.set()
            self._stop.wait(SLOW_SECONDS)

    def read_slow(self) -> None:
        """One round over the drives and memory modules (the thread makes one every few seconds).
        A drive in standby may wake up when it is asked, so SATA drives are read only while a
        theme shows them."""
        for item in list(self._plan.slow):
            if self._stop.is_set():
                return
            if item.chip.name == "drivetemp" and not wanted(item.key):
                value = None
            else:
                value = item.read()
            with self._slow_lock:
                if value is None:
                    self._slow.pop(item.key, None)
                else:
                    self._slow[item.key] = (self._clock(), value)

    # -- reading -----------------------------------------------------------------------------

    def read(self) -> dict[str, Reading]:
        now = self._clock()
        if self._scanned_at is None or now - self._scanned_at >= RESCAN_SECONDS:
            try:
                self.scan(now)
            except Exception:  # noqa: BLE001 - the next scan tries again
                log.debug("linux: scanning failed", exc_info=True)
        plan = self._plan
        if plan.slow and self._thread is not None and not self._first.is_set():
            self._first.wait(FIRST_PASS_SECONDS)
        out: dict[str, Reading] = {}
        steps = (self._chips, self._slow_readings, self._temperatures, self._power, self._graphics)
        for step in steps:
            try:
                step(plan, out, now)
            except Exception:  # noqa: BLE001 - one part failing leaves the others
                log.debug("linux: %s failed", step.__name__, exc_info=True)
        return out

    @staticmethod
    def _chips(plan: Plan, out: dict[str, Reading], now: float) -> None:
        """Every temperature and fan, each under the key of its own."""
        asleep = {card.hwmon for card in plan.cards if card.hwmon and gpus.suspended(card)}
        for item in plan.fast:
            if item.chip.path.name in asleep:  # asking a card that is off would switch it on
                continue
            value = item.read()
            if value is not None:
                out[item.key] = Reading(item.key, value, item.unit, item.name)

    def _slow_readings(self, plan: Plan, out: dict[str, Reading], now: float) -> None:
        """What the thread of the slow ones read last, unless that is too long ago."""
        with self._slow_lock:
            found = dict(self._slow)
        for item in plan.slow:
            stamp, value = found.get(item.key, (None, None))
            if stamp is not None and value is not None and now - stamp <= SLOW_FRESH:
                out[item.key] = Reading(item.key, value, item.unit, plan.names[item.key])

    @staticmethod
    def _alias(out: dict[str, Reading], key: str, label: str, source: str | None) -> None:
        """``key`` repeats the reading ``source`` under a friendlier name; lists show it once."""
        reading = out.get(source) if source else None
        if reading is not None:
            out[key] = Reading(key, reading.value, reading.unit, label, source)

    @staticmethod
    def _put(out: dict[str, Reading], key: str, value: float | None, unit: str, label: str) -> None:
        if value is not None:
            out[key] = Reading(key, value, unit, label)

    def _temperatures(self, plan: Plan, out: dict[str, Reading], now: float) -> None:
        if plan.cpu_temp is not None:
            self._alias(out, "cpu.temp", "CPU temperature", plan.cpu_temp.key)
        modules = [out[item.key] for item in plan.memory if item.key in out]
        if modules:
            top = max(modules, key=lambda reading: reading.value or 0.0)
            out["mem.temp"] = Reading("mem.temp", top.value, "°C", "Memory temperature", top.key)

    def _power(self, plan: Plan, out: dict[str, Reading], now: float) -> None:
        """The processor's power: RAPL, else the sockets of amd_energy, else a chip's own."""
        watts = plan.package_power.watts() if plan.package_power is not None else None
        if watts is None and plan.socket_power is not None:
            watts = plan.socket_power.watts()
        if watts is None and plan.chip_power:
            values = [channel.read() for channel in plan.chip_power]
            if all(value is not None for value in values):
                watts = sum(value for value in values if value is not None)
        self._put(out, "cpu.power", watts, "W", "CPU power")

    # -- the graphics card ---------------------------------------------------------------------

    def _graphics(self, plan: Plan, out: dict[str, Reading], now: float) -> None:
        card = plan.card
        nvidia = set()
        for number, (nvidia_card, device) in enumerate(plan.nvidia):
            nvidia.add(nvidia_card.slot)
            if gpus.suspended(nvidia_card):
                continue
            values = device.read()
            self._nvidia_raw(out, number, values)
            if nvidia_card == card:
                self._nvidia_main(out, number, values)
        if card is None or card.slot in nvidia or gpus.suspended(card):
            return
        if plan.channels is not None:
            self._hwmon_graphics(plan, out)
        if card.driver == "amdgpu":
            self._amd_graphics(card, out)
        if "gpu.freq" not in out:  # Intel's drivers keep the clock in the card's folder
            self._put(out, "gpu.freq", gpus.clock_mhz(card), "MHz", "GPU clock")

    def _hwmon_graphics(self, plan: Plan, out: dict[str, Reading]) -> None:
        channels = plan.channels
        if channels is None:
            return
        for key, label, channel in (
            ("gpu.temp", "GPU temperature", channels.temp),
            ("gpu.hotspot", "GPU hot spot", channels.hotspot),
            ("gpu.mem.temp", "GPU memory temperature", channels.memory_temp),
            ("gpu.fan", "GPU Fan", channels.fan),
        ):
            if channel is not None:
                self._alias(out, key, label, plan.keys.get(channel))
        if channels.pwm is not None:
            self._put(out, "gpu.fan.percent", channels.pwm.read(), "%", "GPU fan speed")
        watts = channels.power.read() if channels.power is not None else None
        if watts is None and channels.energy is not None:
            watts = channels.energy.watts()
        self._put(out, "gpu.power", watts, "W", "GPU power")
        if channels.clock is not None:
            self._put(out, "gpu.freq", channels.clock.read(), "MHz", "GPU clock")
        if channels.memory_clock is not None:
            self._put(out, "gpu.mem.freq", channels.memory_clock.read(), "MHz", "GPU memory clock")

    def _amd_graphics(self, card: Card, out: dict[str, Reading]) -> None:
        self._put(out, "gpu.load", gpus.busy_percent(card), "%", "GPU load")
        memory = gpus.video_memory(card)
        if memory is not None:
            used, total = memory
            self._put(out, "gpu.mem.used", used / MIB, "MB", "GPU memory used")
            self._put(out, "gpu.mem.total", total / MIB, "MB", "GPU memory")
            self._put(out, "gpu.mem.load", 100.0 * used / total, "%", "GPU memory")

    def _nvidia_raw(self, out: dict[str, Reading], number: int, values: dict[str, float]) -> None:
        """A card's temperature (and its fan, where the driver tells RPM) under keys of their
        own, as a hwmon chip's would be: ``temp.nvidia.gpu``, ``fan.nvidia.0``."""
        suffix = "" if number == 0 else f"_{number + 1}"
        self._put(out, f"temp.nvidia.gpu{suffix}", values.get("temp"), "°C", "GPU temperature")
        self._put(out, f"fan.nvidia.{number}", values.get("fan_rpm"), "RPM", "GPU Fan")

    def _nvidia_main(self, out: dict[str, Reading], number: int, values: dict[str, float]) -> None:
        suffix = "" if number == 0 else f"_{number + 1}"
        self._alias(out, "gpu.temp", "GPU temperature", f"temp.nvidia.gpu{suffix}")
        self._alias(out, "gpu.fan", "GPU Fan", f"fan.nvidia.{number}")
        self._put(out, "gpu.fan.percent", values.get("fan_percent"), "%", "GPU fan speed")
        self._put(out, "gpu.power", values.get("power"), "W", "GPU power")
        self._put(out, "gpu.load", values.get("load"), "%", "GPU load")
        self._put(out, "gpu.mem.load", values.get("mem_load"), "%", "GPU memory")
        self._put(out, "gpu.mem.used", values.get("mem_used"), "MB", "GPU memory used")
        self._put(out, "gpu.mem.total", values.get("mem_total"), "MB", "GPU memory")
        self._put(out, "gpu.freq", values.get("freq"), "MHz", "GPU clock")
        self._put(out, "gpu.mem.freq", values.get("mem_freq"), "MHz", "GPU memory clock")

    # -- what the app and `libre-panel sensors` say about it ------------------------------------

    def status(self) -> dict[str, Any]:
        plan = self._plan
        power = None
        if plan.package_power is not None:
            power = "RAPL"
        elif plan.socket_power is not None or plan.chip_power:
            power = "chip"
        return {
            "chips": len(plan.chips),
            "temperatures": sum(1 for i in plan.items if i.channel.kind == "temp"),
            "fans": sum(1 for i in plan.items if i.channel.kind == "fan"),
            "graphics": plan.card.driver if plan.card is not None else None,
            "nvidia driver": "NVML" if plan.nvidia else None,
            "cpu power": power,
        }

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)

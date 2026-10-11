"""Linux' hwmon interface: the temperatures, fans, power and clocks the kernel's drivers know.

The drivers for the processor (k10temp, coretemp, zenpower), the graphics card (amdgpu, xe,
i915), the mainboard's chips (nct6775, it87, asus_ec_sensors, ...), NVMe drives and memory
modules each register a "hwmon" device: a folder of small files (``temp1_input`` in thousandths
of a degree, ``fan1_input`` in RPM, ``power1_average`` in microwatts, ...). They are readable by
everyone, so nothing here needs administrator rights.

This module finds the chips, remembers where each reading is (a rescan costs a directory
listing, a reading one tiny file) and names the temperatures and fans as ``PsutilProvider``
always did (``temp.k10temp.tctl``, ``fan.amdgpu.0``), so keys that themes already use stay what
they were. The fans of the mainboard's Super I/O chip are named as Windows names them
(``fan.lpc.fan_1``), which is what the themes with fans look for.

Everything takes the root of the file system, so that tests can lay a machine out in a folder.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

HWMON = "sys/class/hwmon"
THERMAL = "sys/class/thermal"

# Names people know for the chips Linux reports.
CHIP_NAMES = {
    "coretemp": "CPU", "k10temp": "CPU", "zenpower": "CPU", "cpu_thermal": "CPU",
    "amdgpu": "GPU", "nouveau": "GPU", "radeon": "GPU", "nvme": "NVMe", "acpitz": "ACPI",
    "iwlwifi_1": "Wi-Fi", "pch_cannonlake": "Chipset", "thinkpad": "ThinkPad",
}  # fmt: skip

# What a chip is, by the name its driver gives it.
CPU_CHIPS = (
    "coretemp", "k10temp", "zenpower", "zenpower3", "cpu_thermal", "cpu-thermal", "amd_energy",
    "fam15h_power",
)  # fmt: skip
GPU_CHIPS = ("amdgpu", "radeon", "nouveau", "i915", "xe")
MEMORY_CHIPS = ("jc42", "spd5118")
DRIVE_CHIPS = ("nvme", "drivetemp")
# Super I/O chips and hardware monitors on the mainboard: Windows calls their place "lpc".
_BOARD_CHIPS = re.compile(
    r"^(?:nct\d{4}\w*|it8\d{3,4}\w*|w836\d+\w*|w8379\d\w*|f7\d{4}\w*|f8\d{3,4}\w*|"
    r"sch\d{4}\w*|smsc\w+|pc874\d\w*|dme1737|vt1211|vt8231|adt7\d{3}\w*|asc7621|"
    r"lm78|lm79|lm80|lm85|lm87|lm93)$"
)
# Chips whose files take long to read or may wake something up (drives, memory on the SMBus):
# the panel's own thread never waits for them, see LinuxProvider.
SLOW_CHIPS = ("nvme", "drivetemp", "jc42", "spd5118")

_FILE = re.compile(r"^(temp|fan|pwm|power|energy|freq)(\d+)(?:_(input|average|label))?$")
_HWMON_DIR = re.compile(r"^hwmon\d+$")
_TEMPERATURE_RANGE = (-60.0, 200.0)  # outside it a chip says "no sensor"


def kind_of(chip: str) -> str:
    """What a chip with this driver name is: cpu, gpu, memory, drive, board or other."""
    if chip in CPU_CHIPS:
        return "cpu"
    if chip in GPU_CHIPS:
        return "gpu"
    if chip in MEMORY_CHIPS:
        return "memory"
    if chip in DRIVE_CHIPS:
        return "drive"
    return "board" if _BOARD_CHIPS.match(chip) else "other"


def slug(key: str) -> str:
    return key.replace(" ", "_").lower()


def unique(key: str, name: str, used: dict[str, int]) -> tuple[str, str]:
    """``key`` and ``name``, numbered from the second sensor that has them on: Linux names the
    sensors of two NVMe drives (or two GPUs) alike."""
    used[key] = count = used.get(key, 0) + 1
    return (key, name) if count == 1 else (f"{key}_{count}", f"{name} {count}")


def read_text(path: Path) -> str | None:
    """The contents of a small file of /sys or /proc, stripped; None where it cannot be read
    (a sensor that fails answers with an error, not with text)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(4096).decode("utf-8", "replace").strip()
    except OSError:
        return None


def read_number(path: Path, base: int = 10) -> int | None:
    text = read_text(path)
    if not text:
        return None
    try:
        return int(text, base)
    except ValueError:
        return None


@dataclass(frozen=True)
class Channel:
    """One reading of a chip: a file and what its number means."""

    kind: str  # temp, fan, pwm, power, energy, freq
    number: int
    path: Path
    label: str = ""  # the chip's own word for it ("Tctl", "edge", "Composite")

    def read(self) -> float | None:
        """The value in the unit people use (°C, RPM, W, µJ, MHz; a pwm in percent); None
        when the file cannot be read or the number is what a chip gives for "no sensor"."""
        raw = read_number(self.path)
        if raw is None:
            return None
        if self.kind == "temp":
            value = raw / 1000.0
            return value if _TEMPERATURE_RANGE[0] < value < _TEMPERATURE_RANGE[1] else None
        if self.kind in ("power", "freq"):
            return raw / 1e6  # microwatts, hertz
        if self.kind == "pwm":
            return raw * 100.0 / 255.0
        return float(raw)  # fans in RPM, energy in µJ


@dataclass
class Chip:
    """A hwmon device: its folder, its driver's name and its channels."""

    path: Path
    name: str
    base: Path  # where the files are: the folder itself, or its "device" (older kernels)
    channels: tuple[Channel, ...] = ()

    def of(self, kind: str) -> list[Channel]:
        return [c for c in self.channels if c.kind == kind]

    def labelled(self, kind: str, *labels: str) -> Channel | None:
        """The first channel of ``kind`` that has one of ``labels``, tried in their order (""
        is a channel without a label)."""
        found = self.of(kind)
        for label in labels:
            for channel in found:
                if channel.label.lower() == label.lower():
                    return channel
        return None

    @property
    def kind(self) -> str:
        return kind_of(self.name)

    @property
    def slow(self) -> bool:
        return self.name in SLOW_CHIPS


def _channels(folder: Path) -> tuple[Channel, ...]:
    """The readings in a hwmon folder: ``tempN_input`` with its ``tempN_label``, ``fanN_input``,
    ``pwmN``, ``powerN_input`` (``_average`` where the driver has it: a graphics card's),
    ``energyN_input``, ``freqN_input``."""
    try:
        names = os.listdir(folder)
    except OSError:
        return ()
    labels: dict[tuple[str, int], str] = {}
    inputs: dict[tuple[str, int], str] = {}
    for name in names:
        found = _FILE.match(name)
        if not found:
            continue
        kind, number, suffix = found.group(1), int(found.group(2)), found.group(3)
        if suffix == "label":
            labels[kind, number] = name
        elif kind == "pwm" and suffix is None:
            inputs[kind, number] = name
        elif suffix == "input":
            inputs.setdefault((kind, number), name)
        elif suffix == "average" and kind == "power":
            inputs[kind, number] = name
    channels = []
    for (kind, number), name in sorted(inputs.items()):
        label = ""
        if (kind, number) in labels:
            label = read_text(folder / labels[kind, number]) or ""
        channels.append(Channel(kind, number, folder / name, label))
    return tuple(channels)


def scan(root: Path = Path("/")) -> list[Chip]:
    """The chips there are, by the names of their folders (hwmon0, hwmon1, hwmon10, hwmon2 ...:
    as text, which is the order the keys' numbering follows)."""
    folder = Path(root) / HWMON
    try:
        names = sorted(name for name in os.listdir(folder) if _HWMON_DIR.match(name))
    except OSError:
        return []
    chips = []
    for entry in names:
        path = folder / entry
        name = read_text(path / "name")
        if not name:
            continue
        base, channels = path, _channels(path)
        if not channels:  # CentOS and older kernels keep them one level down
            base, channels = path / "device", _channels(path / "device")
        chips.append(Chip(path, name, base, channels))
    return chips


def thermal_zones(root: Path = Path("/")) -> list[Chip]:
    """The thermal zones as chips named by their type (``x86_pkg_temp``, ``acpitz``): what there
    is to read where no driver registers a hwmon temperature."""
    folder = Path(root) / THERMAL
    try:
        names = sorted(name for name in os.listdir(folder) if name.startswith("thermal_zone"))
    except OSError:
        return []
    chips = []
    for entry in names:
        path = folder / entry
        kind = read_text(path / "type")
        if kind and (path / "temp").is_file():
            chips.append(Chip(path, kind, path, (Channel("temp", 1, path / "temp"),)))
    return chips


@dataclass(frozen=True)
class Item:
    """A temperature or fan that is published under a key of its own."""

    key: str
    name: str
    unit: str
    chip: Chip
    channel: Channel

    def read(self) -> float | None:
        value = self.channel.read()
        if value == 0.0 and self.channel.kind == "temp" and self.chip.kind == "board":
            return None  # a Super I/O input with nothing on it reads 0
        return value


def name_items(chips: Iterable[Chip]) -> list[Item]:
    """The key of every temperature and fan.

    Temperatures and fans are ``<temp|fan>.<chip>.<label>`` as PsutilProvider names them: the
    chip's label, or the place among the chips of that name when it has none, and ``_2`` from
    the second of one key; the order is psutil's (the files by their paths as text, the chips
    grouped by name as they come). The fans of a mainboard chip are ``fan.lpc.fan_<n>`` (a
    second chip ``lpc2``), named by their label or "Fan #<n>"."""
    chips = list(chips)
    items: list[Item] = []
    board = [chip for chip in chips if chip.kind == "board" and chip.of("fan")]
    where = {
        chip.path: "lpc" if place == 0 else f"lpc{place + 1}" for place, chip in enumerate(board)
    }
    for kind, unit in (("temp", "°C"), ("fan", "RPM")):
        entries = sorted(
            (
                (chip, channel)
                for chip in chips
                for channel in chip.of(kind)
                if not (kind == "fan" and chip.path in where)
            ),
            key=lambda pair: str(pair[0].base / f"{kind}{pair[1].number}"),
        )
        groups: dict[str, list[tuple[Chip, Channel]]] = {}
        for chip, channel in entries:
            groups.setdefault(chip.name, []).append((chip, channel))
        used: dict[str, int] = {}
        for chip_name, group in groups.items():
            for i, (chip, channel) in enumerate(group):
                label = channel.label or str(i)
                name = f"{CHIP_NAMES.get(chip_name, chip_name)} {channel.label or i + 1}"
                key, name = unique(slug(f"{kind}.{chip_name}.{label}"), name, used)
                items.append(Item(key, name, unit, chip, channel))
    for chip in board:
        for channel in chip.of("fan"):
            key = f"fan.{where[chip.path]}.fan_{channel.number}"
            items.append(Item(key, channel.label or f"Fan #{channel.number}", "RPM", chip, channel))
    return items

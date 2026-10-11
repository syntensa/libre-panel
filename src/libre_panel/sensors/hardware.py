"""The names of the processor, graphics card and memory: found by themselves, or the user's own.

This source is always on (``build_hub`` adds it) and publishes ``cpu.name``, ``gpu.name`` and
``mem.name`` for headings on a panel, and ``mem.speed``, the speed the memory runs at, where
the firmware tells it. The computer is asked once, with no administrator rights and nothing
to install, and the answer is kept; reading a snapshot costs a copy of a few small values.
The names are tidied for a panel (``hwnames``: "Ryzen 5 7600", not "AMD Ryzen 5 7600
6-Core Processor"), and text the user gives under ``[hardware]`` in config.toml is shown as
it is instead.

- Processor: Windows' registry (``ProcessorNameString``); Linux' ``/proc/cpuinfo`` (on ARM the
  board or chip it names); else what ``platform`` says.
- Graphics card: Windows' registry (the display adapters; software and remote ones do not
  count, and the one with the most memory of its own does); Linux' ``/sys/class/drm``, the
  card's PCI ids looked up in the system's ``pci.ids`` (and AMD's ``amdgpu.ids``), or the
  NVIDIA driver's own name for it.
- Memory: the modules in the firmware's SMBIOS table ("Kingston DDR5-5600 32 GB"; Windows,
  and Linux where the table may be read); else the size, as it is told by psutil ("32 GB").

Where this source has a name, it wins over the same reading of any other source
(LibreHardwareMonitor has ``cpu.name`` and ``gpu.name`` too): see ``SensorProvider.overrides``.
The names are the user's, so nothing here writes one to the log.
"""

from __future__ import annotations

import itertools
import logging
import platform
import re
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from libre_panel.sensors.base import Reading, SensorProvider
from libre_panel.sensors.hwnames import (
    installed_gb,
    memory_name,
    size_label,
    tidy_cpu,
    tidy_gpu,
    tidy_maker,
)
from libre_panel.sensors.smbios import (
    Module,
    linux_table,
    memory_modules,
    windows_table,
)

log = logging.getLogger(__name__)

PARTS = ("cpu", "gpu", "memory")  # what has a name; the editor's and config.toml's words
_KEYS = {"cpu": "cpu.name", "gpu": "gpu.name", "memory": "mem.name"}
_LABELS = {"cpu": "CPU", "gpu": "GPU", "memory": "Memory"}
NAME_KEYS = tuple(_KEYS.values())
SPEED_KEY = "mem.speed"  # megatransfers a second, from the memory modules' SMBIOS entries

_CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
# Adapters that draw in software or for somebody else's screen (remote sessions, virtual
# displays, a USB dock): none of them is the card in the computer.
_SOFTWARE_ADAPTERS = (
    "microsoft basic", "microsoft remote", "remote display", "hyper-v", "virtual",
    "indirect display", "standard vga", "displaylink", "mirror",
)  # fmt: skip

CPUINFO = Path("/proc/cpuinfo")
DRM = Path("/sys/class/drm")
NVIDIA = Path("/proc/driver/nvidia/gpus")
PCI_IDS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"))
AMDGPU_IDS = Path("/usr/share/libdrm/amdgpu.ids")
_NVIDIA_VENDOR = 0x10DE
_AMD_VENDOR = 0x1002
_ARM = re.compile(r"ARM(?:v\d|\b)", re.IGNORECASE)  # the name ARM kernels give every chip
# What platform.processor() says when it knows no more than the architecture.
_ARCHITECTURES = {
    "x86_64", "amd64", "x86", "i386", "i486", "i586", "i686", "arm", "arm64", "aarch64",
    "armv7l", "armv8l", "ppc64le", "ppc64", "s390x", "riscv64", "unknown",
}  # fmt: skip

T = TypeVar("T")


@dataclass(frozen=True)
class Adapter:
    """A graphics card (or chip), as the system names it."""

    name: str
    memory: int = 0  # bytes of its own; 0: not told
    discrete: bool = False  # known to be a card of its own where its memory is not told
    slot: str = field(default="", compare=False)  # its PCI address (Linux): where its sensors are


@dataclass(frozen=True)
class Found:
    """What this computer is made of, as the system names it: nothing tidied yet."""

    cpu: str = ""
    adapters: tuple[Adapter, ...] = ()
    modules: tuple[Module, ...] = ()
    memory: int = 0  # bytes the system leaves to programs (a little less than is installed)


# -- Windows -------------------------------------------------------------------------------


def _winreg() -> Any:
    import winreg  # only Windows has it

    return winreg


def _value(reg: Any, key: Any, name: str, default: Any = None) -> Any:
    try:
        return reg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def windows_cpu_name(reg: Any = None) -> str:
    """The processor's name as its firmware gives it; ``reg`` is ``winreg`` (or a stand-in)."""
    reg = _winreg() if reg is None else reg
    with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, _CPU_KEY) as key:
        name = _value(reg, key, "ProcessorNameString", "")
    return name if isinstance(name, str) else ""


def _memory_size(reg: Any, key: Any) -> int:
    """A display adapter's own memory in bytes: the 64-bit value, else the older one (which
    stops below 4 GB); a binary value is a little-endian number."""
    for name in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
        value = _value(reg, key, name)
        if isinstance(value, bytes | bytearray):
            value = int.from_bytes(value[:8], "little")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 0


def is_software(name: str) -> bool:
    """Whether a display adapter's name is one that draws in software or for a remote screen."""
    lower = name.lower()
    return any(word in lower for word in _SOFTWARE_ADAPTERS)


def windows_adapters(reg: Any = None) -> list[Adapter]:
    """The display adapters Windows has a driver for (the registry's display class), without
    the software and remote ones."""
    reg = _winreg() if reg is None else reg
    adapters = []
    with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as display:
        for index in itertools.count():
            try:
                sub = reg.EnumKey(display, index)
            except OSError:  # no more
                break
            if not sub.isdigit():  # "Configuration", "Properties"
                continue
            try:
                with reg.OpenKey(display, sub) as key:
                    name = _value(reg, key, "DriverDesc", "")
                    memory = _memory_size(reg, key)
            except OSError:
                continue
            if isinstance(name, str) and name.strip() and not is_software(name):
                adapters.append(Adapter(name.strip(), memory))
    return adapters


def windows_modules(kernel32: Any = None) -> list[Module]:
    """The memory modules in Windows' SMBIOS table."""
    return memory_modules(windows_table(kernel32))


# -- Linux ---------------------------------------------------------------------------------

_CPUINFO_KEYS = ("model name", "Model Name", "Model", "Hardware", "Processor", "cpu model", "cpu")


def linux_cpu_name(cpuinfo: Path = CPUINFO) -> str:
    """The processor's name from /proc/cpuinfo: its "model name". An ARM kernel gives that
    name to every chip ("ARMv7 Processor rev 4"); there the "Model" (the board) or the
    "Hardware" (the chip) tells more."""
    found: dict[str, str] = {}
    with cpuinfo.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            key, colon, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if colon and value and key in _CPUINFO_KEYS and key not in found:
                found[key] = value
                if key == "model name" and not _ARM.match(value):
                    break  # the usual case: the first processor says it
    name = found.get("model name") or found.get("Model Name") or ""
    board = found.get("Model") or found.get("Hardware") or ""
    if board and (not name or _ARM.match(name)):
        return board
    return name or found.get("Processor") or found.get("cpu model") or found.get("cpu") or ""


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _number(path: Path, base: int = 10) -> int | None:
    try:
        return int(_text(path), base)
    except ValueError:
        return None


def _bracketed(name: str) -> str:
    """The product's name in a pci.ids name: "AD104 [GeForce RTX 4070]" is a "GeForce RTX 4070"
    (the chip's code name comes first, the product's name is in brackets)."""
    found = re.findall(r"\[([^\[\]]+)\]", name)
    return found[-1].strip() if found else name.strip()


def _pci_name(lines: Iterable[str], vendor: int, device: int) -> str:
    """The product name of a PCI device in the lines of a pci.ids: the vendor's block, then
    its device lines (one tab); subsystems (two tabs) and the classes at the end are skipped."""
    want_vendor, want_device = f"{vendor:04x}", f"{device:04x}"
    inside = False
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        if not line.startswith("\t"):
            if inside:
                return ""  # the next vendor: this device is not listed
            inside = line[:4].lower() == want_vendor and line[4:5] == " "
        elif inside and not line.startswith("\t\t") and line[1:5].lower() == want_device:
            return _bracketed(line[5:])
    return ""


def pci_name(paths: Sequence[Path], vendor: int, device: int) -> str:
    """Looked up in the first of ``paths`` that has it."""
    for path in paths:
        try:
            with path.open(encoding="utf-8", errors="replace") as lines:
                name = _pci_name(lines, vendor, device)
        except OSError:
            continue
        if name:
            return name
    return ""


def amdgpu_name(path: Path, device: int, revision: int | None) -> str:
    """AMD's own list (libdrm's ``amdgpu.ids``: "744C,<tab>C8,<tab>AMD Radeon RX ...") says
    exactly which card a device id and revision are, where pci.ids lists several under one."""
    if revision is None:
        return ""
    try:
        with path.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                found = [part.strip() for part in line.split(",", 2)]
                if len(found) == 3 and not line.startswith("#"):
                    try:
                        same = (int(found[0], 16), int(found[1], 16)) == (device, revision)
                    except ValueError:
                        continue
                    if same:
                        return found[2]
    except OSError:
        pass
    return ""


def _nvidia_models(folder: Path) -> dict[str, str]:
    """The names the NVIDIA driver gives its cards, by their PCI address (its "Bus Location",
    else the name of the card's folder, which is the same)."""
    models = {}
    try:
        folders = sorted(folder.iterdir())
    except OSError:
        return models
    for gpu in folders:
        found = {}
        for line in _text(gpu / "information").splitlines():
            key, colon, value = line.partition(":")
            if colon and key.strip() in ("Model", "Bus Location"):
                found[key.strip()] = value.strip()
        if found.get("Model"):
            models[(found.get("Bus Location") or gpu.name).lower()] = found["Model"]
    return models


def _slot(device: Path) -> str:
    """A card's PCI address ("0000:01:00.0")."""
    for line in _text(device / "uevent").splitlines():
        if line.startswith("PCI_SLOT_NAME="):
            return line.partition("=")[2].strip().lower()
    return device.resolve().name.lower()


def linux_adapters(
    drm: Path = DRM,
    nvidia: Path = NVIDIA,
    pci_ids: Sequence[Path] = PCI_IDS,
    amdgpu_ids: Path = AMDGPU_IDS,
) -> list[Adapter]:
    """The cards the kernel has, named: by the NVIDIA driver, by AMD's list or by pci.ids.
    One whose name nothing knows is left out."""
    models = _nvidia_models(nvidia)
    adapters = []
    try:
        cards = sorted(drm.iterdir())
    except OSError:
        return adapters
    for card in cards:
        if not re.fullmatch(r"card\d+", card.name):  # not "card0-DP-1", the connectors
            continue
        device = card / "device"
        vendor, product = _number(device / "vendor", 16), _number(device / "device", 16)
        if vendor is None or product is None:
            continue
        name = ""
        if vendor == _NVIDIA_VENDOR:
            name = models.get(_slot(device), "")
            if not name and len(models) == 1:  # one name for one card, whatever the address
                name = next(iter(models.values()))
        elif vendor == _AMD_VENDOR:
            name = amdgpu_name(amdgpu_ids, product, _number(device / "revision", 16))
        name = name or pci_name(pci_ids, vendor, product)
        if name:
            memory = _number(device / "mem_info_vram_total") or 0  # amdgpu tells it
            adapters.append(Adapter(name, memory, vendor == _NVIDIA_VENDOR, _slot(device)))
    return adapters


def linux_modules(entries: Path | None = None) -> list[Module]:
    """The memory modules in Linux' SMBIOS files (none where the user may not read them)."""
    return memory_modules(linux_table() if entries is None else linux_table(entries))


# -- the computer --------------------------------------------------------------------------


def _try(read: Callable[[], T], default: T) -> T:
    try:
        return read()
    except Exception:  # noqa: BLE001 - a name is not worth a failing panel
        log.debug("hardware: %s failed", getattr(read, "__name__", "a reader"), exc_info=True)
        return default


def _last_resort() -> str:
    """What `platform` knows of the processor, unless that is only its architecture."""
    name = platform.processor().strip()
    return "" if name.lower() in _ARCHITECTURES else name


def _visible_memory() -> int:
    import psutil

    return int(psutil.virtual_memory().total)


def _scan_machine(system: str = sys.platform) -> Found:
    """Ask this computer. Each part on its own: one that cannot be read leaves the rest."""
    if system == "win32":
        cpu, adapters, modules = windows_cpu_name, windows_adapters, windows_modules
    else:
        cpu, adapters, modules = linux_cpu_name, linux_adapters, linux_modules
    return Found(
        _try(cpu, "") or _try(_last_resort, ""),
        tuple(_try(adapters, [])),
        tuple(_try(modules, [])),
        _try(_visible_memory, 0),
    )


_lock = threading.Lock()
_found: Found | None = None


def scan(refresh: bool = False) -> Found:
    """What this computer is made of. It is asked once, in the first call, and not again
    unless ``refresh`` says so (a card was changed while Libre Panel runs)."""
    global _found
    with _lock:
        if _found is None or refresh:
            _found = _scan_machine()
            log.debug(  # what was found, never what it is called: logs go into issues
                "hardware: processor %s, %d graphics card(s), %d memory module(s), memory %s",
                "named" if _found.cpu else "not named",
                len(_found.adapters),
                len(_found.modules),
                "seen" if _found.memory else "not seen",
            )
        return _found


def pick_adapter(adapters: Sequence[Adapter], prefer: str = "") -> Adapter | None:
    """The card to name: the one whose name has ``prefer`` (the ``gpu`` option of
    ``[sensors.librehardwaremonitor]``, which picks the card the readings come from), else a
    discrete one, else the one with the most memory of its own. ``load`` measures the card
    with the most memory in use, which is this one wherever a card of its own is built in."""
    wanted = prefer.lower().strip() if isinstance(prefer, str) else ""
    pool = [a for a in adapters if wanted and wanted in a.name.lower()] or list(adapters)
    return max(pool, key=lambda a: (a.discrete, a.memory), default=None)


def memory_label(found: Found) -> str:
    """The memory's name: from its modules ("Kingston DDR5-5600 32 GB"; the maker is left
    out when they differ), else its size as the system tells it ("32 GB"); empty if neither
    is known."""
    modules = found.modules
    if modules:
        makers = {tidy_maker(m.maker) for m in modules}
        kinds = [m.kind for m in modules if m.kind]
        speeds = [m.speed for m in modules if m.speed]
        return memory_name(
            makers.pop() if len(makers) == 1 else "",
            max(set(kinds), key=kinds.count) if kinds else "",
            min(speeds) if speeds else 0,
            sum(m.megabytes for m in modules) / 1024,
        )
    return size_label(installed_gb(found.memory)) if found.memory else ""


def memory_speed(found: Found) -> int:
    """The speed the memory runs at, in megatransfers a second: the slowest module's (it sets
    the pace of the others); 0 where no module tells it."""
    return min((m.speed for m in found.modules if m.speed), default=0)


def describe(found: Found, prefer_gpu: str = "") -> dict[str, str]:
    """The three names, tidied, by ``PARTS``; empty where nothing was found."""
    adapter = pick_adapter(found.adapters, prefer_gpu)
    return {
        "cpu": tidy_cpu(found.cpu),
        "gpu": tidy_gpu(adapter.name) if adapter else "",
        "memory": memory_label(found),
    }


def detected_names(prefer_gpu: str = "", refresh: bool = False) -> dict[str, str]:
    """What this computer is called by itself: ``{"cpu": ..., "gpu": ..., "memory": ...}``."""
    return describe(scan(refresh), prefer_gpu)


class HardwareProvider(SensorProvider):
    """``cpu.name``, ``gpu.name`` and ``mem.name``: what was found, or the user's own text
    (``[hardware]``), which replaces it; and ``mem.speed`` where the modules tell it."""

    name = "hardware"
    overrides = NAME_KEYS

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._custom = dict.fromkeys(PARTS, "")
        self._prefer = ""
        self._found = scan()
        self._readings = self._make()

    def _make(self) -> dict[str, Reading]:
        names = describe(self._found, self._prefer)
        readings = {}
        for part, key in _KEYS.items():
            text = self._custom[part] or names[part]
            if text:
                readings[key] = Reading(key, text, "", _LABELS[part])
        speed = memory_speed(self._found)
        if speed:
            readings[SPEED_KEY] = Reading(SPEED_KEY, float(speed), "MT/s", "Memory speed")
        return readings

    def follow_config(self, config: Any) -> None:
        hardware = config.hardware
        self._custom = {
            "cpu": hardware.cpu_name,
            "gpu": hardware.gpu_name,
            "memory": hardware.memory_name,
        }
        card = config.sensors.options.get("librehardwaremonitor", {}).get("gpu")
        self._prefer = card if isinstance(card, str) else ""
        self._readings = self._make()

    def read(self) -> dict[str, Reading]:
        found = _found or scan()  # without the lock: a refresh in the editor holds it a moment
        if found is not self._found:  # asked for again (a refresh in the editor)
            self._found = found
            self._readings = self._make()
        return dict(self._readings)

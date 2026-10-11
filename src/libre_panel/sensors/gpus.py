"""The graphics cards of a Linux PC: which there are, which one is "the" GPU, what the kernel
tells about each without root.

``/sys/class/drm/card*`` has one folder per card, its ``device`` the PCI device. AMD's amdgpu
driver puts the load (``gpu_busy_percent``) and the video memory (``mem_info_vram_*``) in it,
and a hwmon folder with temperatures, fan, power and clocks; Intel's i915 and xe drivers have a
hwmon folder with power and, on newer kernels, temperatures, and the clock in the card's folder.
NVIDIA's proprietary driver has none of it: see ``nvml``.

Everything takes the root of the file system, so that tests can lay a machine out in a folder.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from libre_panel.sensors.hwmon import read_number, read_text

DRM = "sys/class/drm"
AMD, NVIDIA, INTEL = 0x1002, 0x10DE, 0x8086
_CARD = re.compile(r"^card\d+$")
# Where the drivers of Intel's cards keep the clock in MHz, relative to the card's folder.
_INTEL_CLOCKS = (
    "gt_cur_freq_mhz",
    "gt/gt0/rps_cur_freq_mhz",
    "device/tile0/gt0/freq0/cur_freq",
)


@dataclass(frozen=True)
class Card:
    """A graphics card as the kernel lists it."""

    name: str  # "card0"
    folder: Path  # /sys/class/drm/card0
    slot: str  # its PCI address, "0000:03:00.0"
    vendor: int
    product: int
    driver: str  # "amdgpu", "nvidia", "i915", "xe", "nouveau", "radeon", or ""
    vram: int  # bytes of memory of its own, where the driver tells (AMD, Intel's i915)
    hwmon: str  # the name of its hwmon folder ("hwmon3"), "" if the driver has none

    @property
    def device(self) -> Path:
        return self.folder / "device"

    @property
    def rank(self) -> tuple[bool, int]:
        """How much it is "the" GPU: the same order the names are given in (hardware.py)."""
        return self.vendor == NVIDIA, self.vram


def _uevent(device: Path) -> dict[str, str]:
    found = {}
    for line in (read_text(device / "uevent") or "").splitlines():
        key, _, value = line.partition("=")
        if key in ("DRIVER", "PCI_SLOT_NAME"):
            found[key] = value.strip()
    return found


def find_cards(root: Path = Path("/")) -> list[Card]:
    """The cards with a PCI device behind them (a virtual display has none), in the order of
    their numbers."""
    folder = Path(root) / DRM
    try:
        names = sorted(name for name in os.listdir(folder) if _CARD.match(name))
    except OSError:
        return []
    cards = []
    for name in names:
        card = folder / name
        device = card / "device"
        vendor, product = read_number(device / "vendor", 16), read_number(device / "device", 16)
        if vendor is None or product is None:
            continue
        uevent = _uevent(device)
        vram = read_number(device / "mem_info_vram_total") or read_number(card / "lmem_total_bytes")
        try:
            hwmon = sorted(n for n in os.listdir(device / "hwmon") if n.startswith("hwmon"))
        except OSError:
            hwmon = []
        cards.append(
            Card(
                name,
                card,
                uevent.get("PCI_SLOT_NAME", "").lower(),
                vendor,
                product,
                uevent.get("DRIVER", ""),
                vram or 0,
                hwmon[0] if hwmon else "",
            )
        )
    return cards


def primary(
    cards: Iterable[Card], adapters: Iterable[object] = (), prefer: str = ""
) -> Card | None:
    """The card the ``gpu.*`` readings come from: the one whose name ``gpu.name`` gives. The
    names are those of ``hardware.scan`` (``adapters``, each with its ``slot``), and ``prefer``
    picks one by a part of its name; a card nobody knows the name of is judged by what
    ``hardware.pick_adapter`` would judge: an NVIDIA card first, then the most memory of its own."""
    from libre_panel.sensors.hardware import pick_adapter

    cards = list(cards)
    named = pick_adapter(list(adapters), prefer)  # type: ignore[arg-type]
    slot = getattr(named, "slot", "")
    if slot:
        for card in cards:
            if card.slot == slot:
                return card
    return max(cards, key=lambda card: card.rank, default=None)


def suspended(card: Card) -> bool:
    """Whether the card is powered down to save energy (a laptop's second card): reading its
    files would wake it up, so nobody asks it then."""
    return read_text(card.device / "power" / "runtime_status") == "suspended"


def busy_percent(card: Card) -> float | None:
    """The load of an AMD card (its busiest engine, from the driver's own counter)."""
    value = read_number(card.device / "gpu_busy_percent")
    return None if value is None else max(0.0, min(100.0, float(value)))


def video_memory(card: Card) -> tuple[int, int] | None:
    """``(used, total)`` bytes of an AMD card's own memory."""
    used = read_number(card.device / "mem_info_vram_used")
    total = read_number(card.device / "mem_info_vram_total")
    return None if used is None or not total else (used, total)


def clock_mhz(card: Card) -> float | None:
    """The clock of an Intel card, in MHz, where its driver tells."""
    for relative in _INTEL_CLOCKS:
        value = read_number(card.folder / relative)
        if value is not None:
            return float(value)
    return None

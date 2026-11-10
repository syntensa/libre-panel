//! The graphics cards of a Linux PC: which there are, which one is "the" GPU, what the kernel
//! tells about each without root (sensors/gpus.py).
//!
//! /sys/class/drm/card* has one folder per card, its `device` the PCI device. AMD's amdgpu
//! driver puts the load (`gpu_busy_percent`) and the video memory (`mem_info_vram_*`) in it,
//! and a hwmon folder with temperatures, fan, power and clocks; Intel's i915 and xe drivers
//! have a hwmon folder with power and, on newer kernels, temperatures, and the clock in the
//! card's folder. NVIDIA's proprietary driver has none of it: see nvml.
//!
//! Everything takes the root of the file system, so that tests can lay a machine out in a
//! folder.

use std::path::{Path, PathBuf};

use crate::hardware::{is_card, pick_adapter, Adapter};
use crate::sysfs::{names, read_number, read_text};

pub const DRM: &str = "sys/class/drm";
pub const AMD: i128 = 0x1002;
pub const NVIDIA: i128 = 0x10DE;
pub const INTEL: i128 = 0x8086;
/// Where the drivers of Intel's cards keep the clock in MHz, relative to the card's folder.
const INTEL_CLOCKS: [&str; 3] = [
    "gt_cur_freq_mhz",
    "gt/gt0/rps_cur_freq_mhz",
    "device/tile0/gt0/freq0/cur_freq",
];

/// A graphics card as the kernel lists it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Card {
    /// "card0"
    pub name: String,
    /// /sys/class/drm/card0
    pub folder: PathBuf,
    /// its PCI address, "0000:03:00.0"
    pub slot: String,
    pub vendor: i128,
    pub product: i128,
    /// "amdgpu", "nvidia", "i915", "xe", "nouveau", "radeon", or ""
    pub driver: String,
    /// bytes of memory of its own, where the driver tells (AMD, Intel's i915)
    pub vram: i128,
    /// the name of its hwmon folder ("hwmon3"), "" if the driver has none
    pub hwmon: String,
}

impl Card {
    pub fn device(&self) -> PathBuf {
        self.folder.join("device")
    }

    /// How much it is "the" GPU: the same order the names are given in (hardware).
    fn rank(&self) -> (bool, i128) {
        (self.vendor == NVIDIA, self.vram)
    }
}

/// The cards with a PCI device behind them (a virtual display has none), in the order of
/// their names.
pub fn find_cards(root: &Path) -> Vec<Card> {
    let folder = root.join(DRM);
    let mut cards = Vec::new();
    for name in names(&folder).unwrap_or_default() {
        if !is_card(name.strip_suffix('\n').unwrap_or(&name)) {
            continue;
        }
        let card = folder.join(&name);
        let device = card.join("device");
        let ids = (
            read_number(&device.join("vendor"), 16),
            read_number(&device.join("device"), 16),
        );
        let (Some(vendor), Some(product)) = ids else {
            continue;
        };
        let (mut driver, mut slot) = (String::new(), String::new());
        let uevent = read_text(&device.join("uevent")).unwrap_or_default();
        for line in lp_py::splitlines(&uevent) {
            let (key, value) = line.split_once('=').unwrap_or((line, ""));
            match key {
                "DRIVER" => driver = lp_py::strip(value).to_string(),
                "PCI_SLOT_NAME" => slot = lp_py::strip(value).to_string(),
                _ => {}
            }
        }
        let vram = read_number(&device.join("mem_info_vram_total"), 10)
            .filter(|bytes| *bytes != 0)
            .or_else(|| read_number(&card.join("lmem_total_bytes"), 10))
            .unwrap_or(0);
        let hwmon = names(&device.join("hwmon"))
            .unwrap_or_default()
            .into_iter()
            .find(|name| name.starts_with("hwmon"))
            .unwrap_or_default();
        cards.push(Card {
            name,
            folder: card,
            slot: lp_py::lower(&slot),
            vendor,
            product,
            driver,
            vram,
            hwmon,
        });
    }
    cards
}

/// The card the `gpu.*` readings come from: the one whose name `gpu.name` gives. The names
/// are those of the hardware source (`adapters`, each with its slot), and `prefer` picks one
/// by a part of its name; a card nobody knows the name of is judged by what the names are
/// judged by: an NVIDIA card first, then the most memory of its own.
pub fn primary<'a>(cards: &'a [Card], adapters: &[Adapter], prefer: &str) -> Option<&'a Card> {
    let slot = pick_adapter(adapters, prefer).map_or("", |named| named.slot.as_str());
    if !slot.is_empty() {
        if let Some(card) = cards.iter().find(|card| card.slot == slot) {
            return Some(card);
        }
    }
    let mut best: Option<&Card> = None;
    for card in cards {
        if best.is_none_or(|b| card.rank() > b.rank()) {
            best = Some(card);
        }
    }
    best
}

/// Whether the card is powered down to save energy (a laptop's second card): reading its
/// files would wake it up, so nobody asks it then.
pub fn suspended(card: &Card) -> bool {
    read_text(&card.device().join("power").join("runtime_status")).as_deref() == Some("suspended")
}

/// The load of an AMD card (its busiest engine, from the driver's own counter).
pub fn busy_percent(card: &Card) -> Option<f64> {
    let value = read_number(&card.device().join("gpu_busy_percent"), 10)?;
    Some((value as f64).clamp(0.0, 100.0))
}

/// (used, total) bytes of an AMD card's own memory.
pub fn video_memory(card: &Card) -> Option<(i128, i128)> {
    let used = read_number(&card.device().join("mem_info_vram_used"), 10)?;
    let total = read_number(&card.device().join("mem_info_vram_total"), 10)?;
    (total != 0).then_some((used, total))
}

/// The clock of an Intel card, in MHz, where its driver tells.
pub fn clock_mhz(card: &Card) -> Option<f64> {
    INTEL_CLOCKS
        .iter()
        .find_map(|relative| read_number(&card.folder.join(relative), 10))
        .map(|value| value as f64)
}

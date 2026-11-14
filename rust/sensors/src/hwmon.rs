//! Linux' hwmon interface: the temperatures, fans, power and clocks the kernel's drivers know
//! (sensors/hwmon.py).
//!
//! The drivers for the processor (k10temp, coretemp, zenpower), the graphics card (amdgpu,
//! xe, i915), the mainboard's chips (nct6775, it87, asus_ec_sensors, ...), NVMe drives and
//! memory modules each register a "hwmon" device: a folder of small files (`temp1_input` in
//! thousandths of a degree, `fan1_input` in RPM, `power1_average` in microwatts, ...). They
//! are readable by everyone, so nothing here needs administrator rights.
//!
//! This finds the chips, remembers where each reading is (a rescan costs a directory
//! listing, a reading one tiny file) and names the temperatures and fans as the psutil source
//! always did (`temp.k10temp.tctl`, `fan.amdgpu.0`), so keys that themes already use stay
//! what they were. The fans of the mainboard's Super I/O chip are named as Windows names
//! them (`fan.lpc.fan_1`), which is what the themes with fans look for.
//!
//! Everything takes the root of the file system, so that tests can lay a machine out in a
//! folder.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use indexmap::IndexMap;
use lp_py::regex::Regex;

use crate::sysfs::{names, read_number, read_text};

pub const HWMON: &str = "sys/class/hwmon";
pub const THERMAL: &str = "sys/class/thermal";

/// Names people know for the chips Linux reports.
fn chip_name(chip: &str) -> &str {
    match chip {
        "coretemp" | "k10temp" | "zenpower" | "cpu_thermal" => "CPU",
        "amdgpu" | "nouveau" | "radeon" => "GPU",
        "nvme" => "NVMe",
        "acpitz" => "ACPI",
        "iwlwifi_1" => "Wi-Fi",
        "pch_cannonlake" => "Chipset",
        "thinkpad" => "ThinkPad",
        other => other,
    }
}

// What a chip is, by the name its driver gives it.
const CPU_CHIPS: [&str; 8] = [
    "coretemp",
    "k10temp",
    "zenpower",
    "zenpower3",
    "cpu_thermal",
    "cpu-thermal",
    "amd_energy",
    "fam15h_power",
];
const GPU_CHIPS: [&str; 5] = ["amdgpu", "radeon", "nouveau", "i915", "xe"];
const MEMORY_CHIPS: [&str; 2] = ["jc42", "spd5118"];
const DRIVE_CHIPS: [&str; 2] = ["nvme", "drivetemp"];
/// Super I/O chips and hardware monitors on the mainboard: Windows calls their place "lpc".
const BOARD_CHIPS: &str = concat!(
    r"^(?:nct\d{4}\w*|it8\d{3,4}\w*|w836\d+\w*|w8379\d\w*|f7\d{4}\w*|f8\d{3,4}\w*|",
    r"sch\d{4}\w*|smsc\w+|pc874\d\w*|dme1737|vt1211|vt8231|adt7\d{3}\w*|asc7621|",
    r"lm78|lm79|lm80|lm85|lm87|lm93)$"
);
/// Chips whose files take long to read or may wake something up (drives, memory on the
/// SMBus): the panel's own thread never waits for them, see the Linux source.
const SLOW_CHIPS: [&str; 4] = ["nvme", "drivetemp", "jc42", "spd5118"];

const FILE: &str = r"^(temp|fan|pwm|power|energy|freq)(\d+)(?:_(input|average|label))?$";
const HWMON_DIR: &str = r"^hwmon\d+$";
/// outside it a chip says "no sensor"
const TEMPERATURE_RANGE: (f64, f64) = (-60.0, 200.0);

fn pattern(cell: &'static OnceLock<Option<Regex>>, text: &str) -> Option<&'static Regex> {
    cell.get_or_init(|| Regex::new(text, false).ok()).as_ref()
}

fn found(cell: &'static OnceLock<Option<Regex>>, text: &str, name: &str) -> Option<Vec<String>> {
    let chars: Vec<char> = name.chars().collect();
    let found = pattern(cell, text)?.matches(&chars)?;
    Some(
        (0..found.spans.len())
            .map(|n| found.group(&chars, n).unwrap_or_default())
            .collect(),
    )
}

/// What a chip with this driver name is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChipKind {
    Cpu,
    Gpu,
    Memory,
    Drive,
    Board,
    Other,
}

pub fn kind_of(chip: &str) -> ChipKind {
    static BOARD: OnceLock<Option<Regex>> = OnceLock::new();
    if CPU_CHIPS.contains(&chip) {
        ChipKind::Cpu
    } else if GPU_CHIPS.contains(&chip) {
        ChipKind::Gpu
    } else if MEMORY_CHIPS.contains(&chip) {
        ChipKind::Memory
    } else if DRIVE_CHIPS.contains(&chip) {
        ChipKind::Drive
    } else if found(&BOARD, BOARD_CHIPS, chip).is_some() {
        ChipKind::Board
    } else {
        ChipKind::Other
    }
}

pub fn slug(key: &str) -> String {
    lp_py::lower(&key.replace(' ', "_"))
}

/// `key` and `name`, numbered from the second sensor that has them on: Linux names the
/// sensors of two NVMe drives (or two GPUs) alike.
pub fn unique(key: String, name: String, used: &mut IndexMap<String, usize>) -> (String, String) {
    let count = used.entry(key.clone()).or_insert(0);
    *count += 1;
    match *count {
        1 => (key, name),
        count => (format!("{key}_{count}"), format!("{name} {count}")),
    }
}

/// One reading of a chip: a file and what its number means.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Channel {
    /// temp, fan, pwm, power, energy, freq
    pub kind: String,
    pub number: i128,
    pub path: PathBuf,
    /// the chip's own word for it ("Tctl", "edge", "Composite")
    pub label: String,
}

impl Channel {
    /// The value in the unit people use (°C, RPM, W, µJ, MHz; a pwm in percent); None when
    /// the file cannot be read or the number is what a chip gives for "no sensor".
    pub fn read(&self) -> Option<f64> {
        let raw = read_number(&self.path, 10)? as f64;
        match self.kind.as_str() {
            "temp" => {
                let value = raw / 1000.0;
                (TEMPERATURE_RANGE.0 < value && value < TEMPERATURE_RANGE.1).then_some(value)
            }
            "power" | "freq" => Some(raw / 1e6), // microwatts, hertz
            "pwm" => Some(raw * 100.0 / 255.0),
            _ => Some(raw), // fans in RPM, energy in µJ
        }
    }
}

/// A hwmon device: its folder, its driver's name and its channels.
#[derive(Clone, Debug, PartialEq)]
pub struct Chip {
    pub path: PathBuf,
    pub name: String,
    /// where the files are: the folder itself, or its "device" (older kernels)
    pub base: PathBuf,
    pub channels: Vec<Channel>,
}

impl Chip {
    pub fn of(&self, kind: &str) -> Vec<&Channel> {
        self.channels.iter().filter(|c| c.kind == kind).collect()
    }

    /// The first channel of `kind` that has one of `labels`, tried in their order ("" is a
    /// channel without a label).
    pub fn labelled(&self, kind: &str, labels: &[&str]) -> Option<&Channel> {
        let found = self.of(kind);
        for label in labels {
            let label = lp_py::lower(label);
            if let Some(channel) = found.iter().find(|c| lp_py::lower(&c.label) == label) {
                return Some(channel);
            }
        }
        None
    }

    pub fn kind(&self) -> ChipKind {
        kind_of(&self.name)
    }

    pub fn slow(&self) -> bool {
        SLOW_CHIPS.contains(&self.name.as_str())
    }

    /// The last part of its folder's name ("hwmon3").
    pub fn folder(&self) -> String {
        let name = self.path.file_name().map(|name| name.to_string_lossy());
        name.unwrap_or_default().into_owned()
    }
}

/// The readings in a hwmon folder: `tempN_input` with its `tempN_label`, `fanN_input`,
/// `pwmN`, `powerN_input` (`_average` where the driver has it: a graphics card's),
/// `energyN_input`, `freqN_input`.
fn channels(folder: &Path) -> Vec<Channel> {
    static PARTS: OnceLock<Option<Regex>> = OnceLock::new();
    let mut labels: BTreeMap<(String, i128), String> = BTreeMap::new();
    let mut inputs: BTreeMap<(String, i128), String> = BTreeMap::new();
    for name in names(folder).unwrap_or_default() {
        let Some(parts) = found(&PARTS, FILE, &name) else {
            continue;
        };
        let Some(number) = lp_py::int_of_digits(&parts[2]) else {
            continue;
        };
        let (kind, suffix) = (parts[1].clone(), parts[3].as_str());
        let place = (kind.clone(), number);
        if suffix == "label" {
            labels.insert(place, name);
        } else if (kind == "pwm" && suffix.is_empty()) || (suffix == "average" && kind == "power") {
            inputs.insert(place, name);
        } else if suffix == "input" {
            inputs.entry(place).or_insert(name);
        }
    }
    inputs
        .into_iter()
        .map(|((kind, number), name)| {
            let label = labels
                .get(&(kind.clone(), number))
                .and_then(|label| read_text(&folder.join(label)))
                .unwrap_or_default();
            Channel {
                kind,
                number,
                path: folder.join(name),
                label,
            }
        })
        .collect()
}

/// The chips there are, by the names of their folders (hwmon0, hwmon1, hwmon10, hwmon2 ...:
/// as text, which is the order the keys' numbering follows).
pub fn scan(root: &Path) -> Vec<Chip> {
    static DIR: OnceLock<Option<Regex>> = OnceLock::new();
    let folder = root.join(HWMON);
    let mut chips = Vec::new();
    for entry in names(&folder).unwrap_or_default() {
        if found(&DIR, HWMON_DIR, &entry).is_none() {
            continue;
        }
        let path = folder.join(&entry);
        let Some(name) = read_text(&path.join("name")).filter(|name| !name.is_empty()) else {
            continue;
        };
        let (mut base, mut found) = (path.clone(), channels(&path));
        if found.is_empty() {
            // CentOS and older kernels keep them one level down
            base = path.join("device");
            found = channels(&base);
        }
        chips.push(Chip {
            path,
            name,
            base,
            channels: found,
        });
    }
    chips
}

/// The thermal zones as chips named by their type (`x86_pkg_temp`, `acpitz`): what there is
/// to read where no driver registers a hwmon temperature.
pub fn thermal_zones(root: &Path) -> Vec<Chip> {
    let folder = root.join(THERMAL);
    let mut chips = Vec::new();
    for entry in names(&folder).unwrap_or_default() {
        if !entry.starts_with("thermal_zone") {
            continue;
        }
        let path = folder.join(&entry);
        let kind = read_text(&path.join("type")).unwrap_or_default();
        if !kind.is_empty() && path.join("temp").is_file() {
            chips.push(Chip {
                name: kind,
                base: path.clone(),
                channels: vec![Channel {
                    kind: "temp".into(),
                    number: 1,
                    path: path.join("temp"),
                    label: String::new(),
                }],
                path,
            });
        }
    }
    chips
}

/// A temperature or fan that is published under a key of its own.
#[derive(Clone, Debug, PartialEq)]
pub struct Item {
    pub key: String,
    pub name: String,
    pub unit: &'static str,
    /// its chip's place among the chips the items were named of
    pub chip: usize,
    pub channel: Channel,
    /// a Super I/O input: with nothing on it, it reads 0
    board: bool,
}

impl Item {
    pub fn read(&self) -> Option<f64> {
        let value = self.channel.read()?;
        if value == 0.0 && self.channel.kind == "temp" && self.board {
            return None;
        }
        Some(value)
    }
}

/// The key of every temperature and fan.
///
/// Temperatures and fans are `<temp|fan>.<chip>.<label>` as the psutil source names them:
/// the chip's label, or the place among the chips of that name when it has none, and `_2`
/// from the second of one key; the order is psutil's (the files by their paths as text, the
/// chips grouped by name as they come). The fans of a mainboard chip are `fan.lpc.fan_<n>`
/// (a second chip `lpc2`), named by their label or "Fan #<n>".
pub fn name_items(chips: &[Chip]) -> Vec<Item> {
    let mut items = Vec::new();
    let board: Vec<usize> = (0..chips.len())
        .filter(|n| chips[*n].kind() == ChipKind::Board && !chips[*n].of("fan").is_empty())
        .collect();
    // (by the chip's folder, as Python's dict has them: two chips of one folder are one place)
    let mut places: IndexMap<&Path, String> = IndexMap::new();
    for (place, n) in board.iter().enumerate() {
        let name = match place {
            0 => "lpc".to_string(),
            place => format!("lpc{}", place + 1),
        };
        places.insert(chips[*n].path.as_path(), name);
    }
    for (kind, unit) in [("temp", "°C"), ("fan", "RPM")] {
        let mut entries: Vec<(String, usize, &Channel)> = Vec::new();
        for (n, chip) in chips.iter().enumerate() {
            if kind == "fan" && places.contains_key(chip.path.as_path()) {
                continue;
            }
            for channel in chip.of(kind) {
                let file = chip.base.join(format!("{kind}{}", channel.number));
                entries.push((file.to_string_lossy().into_owned(), n, channel));
            }
        }
        entries.sort_by(|a, b| a.0.chars().cmp(b.0.chars()));
        let mut groups: IndexMap<&str, Vec<(usize, &Channel)>> = IndexMap::new();
        for (_, n, channel) in entries {
            let group = groups.entry(chips[n].name.as_str()).or_default();
            group.push((n, channel));
        }
        let mut used = IndexMap::new();
        for (name, group) in groups {
            for (i, (n, channel)) in group.into_iter().enumerate() {
                let (label, shown) = match channel.label.as_str() {
                    "" => (i.to_string(), (i + 1).to_string()),
                    label => (label.to_string(), label.to_string()),
                };
                let (key, name) = unique(
                    slug(&format!("{kind}.{name}.{label}")),
                    format!("{} {shown}", chip_name(name)),
                    &mut used,
                );
                items.push(Item {
                    key,
                    name,
                    unit,
                    chip: n,
                    channel: channel.clone(),
                    board: chips[n].kind() == ChipKind::Board,
                });
            }
        }
    }
    for n in board {
        let chip = &chips[n];
        for channel in chip.of("fan") {
            let name = match channel.label.as_str() {
                "" => format!("Fan #{}", channel.number),
                label => label.to_string(),
            };
            items.push(Item {
                key: format!("fan.{}.fan_{}", places[chip.path.as_path()], channel.number),
                name,
                unit: "RPM",
                chip: n,
                channel: channel.clone(),
                board: true,
            });
        }
    }
    items
}

//! The names of the processor, graphics card and memory: found by themselves, or the user's
//! own (sensors/hardware.py).
//!
//! This source is always on and gives `cpu.name`, `gpu.name` and `mem.name` for headings on a
//! panel, and `mem.speed`, the speed the memory runs at, where the firmware tells it. The
//! computer is asked once, with no administrator rights and nothing to install, and the
//! answer is kept. The names are tidied for a panel (hwnames: "Ryzen 5 7600", not "AMD Ryzen
//! 5 7600 6-Core Processor"), and text the user gives under `[hardware]` in config.toml is
//! shown as it is instead.
//!
//! - Processor: Windows' registry (`ProcessorNameString`); Linux' /proc/cpuinfo (on ARM the
//!   board or chip it names).
//! - Graphics card: Windows' registry (the display adapters; software and remote ones do
//!   not count, and the one with the most memory of its own does); Linux' /sys/class/drm,
//!   the card's PCI ids looked up in the system's pci.ids (and AMD's amdgpu.ids), or the
//!   NVIDIA driver's own name for it.
//! - Memory: the modules in the firmware's SMBIOS table ("Kingston DDR5-5600 32 GB";
//!   Windows, and Linux where the table may be read); else the size ("32 GB").
//!
//! Where this source has a name, it wins over the same reading of any other source. The
//! names are the user's, so nothing here writes one to the log.

use std::path::{Path, PathBuf};

use lp_py::Value;
use lp_render::Reading;

use crate::hwnames::{installed_gb, memory_name, size_label, tidy_cpu, tidy_gpu, tidy_maker};
use crate::lhm::Readings;
use crate::smbios::{self, Module};
use crate::sysfs::{int_of, names, whole_text};

/// the readings this source gives whoever else has them
pub const NAME_KEYS: [&str; 3] = ["cpu.name", "gpu.name", "mem.name"];
/// megatransfers a second, from the memory modules' SMBIOS entries
pub const SPEED_KEY: &str = "mem.speed";

/// Adapters that draw in software or for somebody else's screen (remote sessions, virtual
/// displays, a USB dock): none of them is the card in the computer.
const SOFTWARE_ADAPTERS: [&str; 9] = [
    "microsoft basic",
    "microsoft remote",
    "remote display",
    "hyper-v",
    "virtual",
    "indirect display",
    "standard vga",
    "displaylink",
    "mirror",
];

pub const CPUINFO: &str = "/proc/cpuinfo";
pub const DRM: &str = "/sys/class/drm";
pub const NVIDIA: &str = "/proc/driver/nvidia/gpus";
pub const PCI_IDS: [&str; 2] = ["/usr/share/hwdata/pci.ids", "/usr/share/misc/pci.ids"];
pub const AMDGPU_IDS: &str = "/usr/share/libdrm/amdgpu.ids";
const NVIDIA_VENDOR: i128 = 0x10DE;
const AMD_VENDOR: i128 = 0x1002;
/// What the system says of the processor when it knows no more than the architecture.
const ARCHITECTURES: [&str; 17] = [
    "x86_64", "amd64", "x86", "i386", "i486", "i586", "i686", "arm", "arm64", "aarch64", "armv7l",
    "armv8l", "ppc64le", "ppc64", "s390x", "riscv64", "unknown",
];

/// A graphics card (or chip), as the system names it.
#[derive(Clone, Debug, Default)]
pub struct Adapter {
    pub name: String,
    /// bytes of its own; 0: not told
    pub memory: u64,
    /// known to be a card of its own where its memory is not told
    pub discrete: bool,
    /// its PCI address (Linux): where its sensors are
    pub slot: String,
}

/// What this computer is made of, as the system names it: nothing tidied yet.
#[derive(Clone, Debug, Default)]
pub struct Found {
    pub cpu: String,
    pub adapters: Vec<Adapter>,
    pub modules: Vec<Module>,
    /// bytes the system leaves to programs (a little less than is installed)
    pub memory: u64,
}

/// Whether a display adapter's name is one that draws in software or for a remote screen.
pub fn is_software(name: &str) -> bool {
    let lower = lp_py::lower(name);
    SOFTWARE_ADAPTERS.iter().any(|word| lower.contains(word))
}

// -- Linux -------------------------------------------------------------------------------

/// The lines of a text file as Python's text mode gives them (ended by \n, \r\n or \r).
fn file_lines(text: &str) -> impl Iterator<Item = &str> {
    text.split('\n')
        .flat_map(|line| line.strip_suffix('\r').unwrap_or(line).split('\r'))
}

/// `ARM(?:v\d|\b)` at the start, in any case: the name ARM kernels give every chip.
fn is_arm(name: &str) -> bool {
    let mut chars = name.chars();
    let head: String = chars.by_ref().take(3).collect();
    if !head.eq_ignore_ascii_case("arm") || head.chars().count() < 3 {
        return false;
    }
    match chars.next() {
        None => true,
        Some('v' | 'V') if chars.next().is_some_and(|ch| lp_py::decimal(ch).is_some()) => true,
        Some(next) => !(lp_py::regex::isalnum(next) || next == '_'),
    }
}

/// The processor's name from /proc/cpuinfo: its "model name". An ARM kernel gives that name
/// to every chip ("ARMv7 Processor rev 4"); there the "Model" (the board) or the "Hardware"
/// (the chip) tells more. None where the file cannot be read.
pub fn linux_cpu_name(cpuinfo: &Path) -> Option<String> {
    const KEYS: [&str; 7] = [
        "model name",
        "Model Name",
        "Model",
        "Hardware",
        "Processor",
        "cpu model",
        "cpu",
    ];
    let data = std::fs::read(cpuinfo).ok()?;
    let text = String::from_utf8_lossy(&data);
    let mut found: Vec<(&str, &str)> = Vec::new();
    for line in file_lines(&text) {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let (key, value) = (lp_py::strip(key), lp_py::strip(value));
        if !value.is_empty() && KEYS.contains(&key) && !found.iter().any(|(k, _)| *k == key) {
            found.push((key, value));
            if key == "model name" && !is_arm(value) {
                break; // the usual case: the first processor says it
            }
        }
    }
    let get = |key: &str| {
        found
            .iter()
            .find(|(k, _)| *k == key)
            .map(|(_, value)| *value)
    };
    let name = get("model name").or(get("Model Name")).unwrap_or("");
    let board = get("Model").or(get("Hardware")).unwrap_or("");
    if !board.is_empty() && (name.is_empty() || is_arm(name)) {
        return Some(board.to_string());
    }
    let name = [get("Processor"), get("cpu model"), get("cpu")]
        .into_iter()
        .fold(Some(name).filter(|n| !n.is_empty()), Option::or);
    Some(name.unwrap_or("").to_string())
}

fn number(path: &Path, base: u32) -> Option<i128> {
    int_of(&whole_text(path), base)
}

/// The product's name in a pci.ids name: "AD104 [GeForce RTX 4070]" is a "GeForce RTX 4070"
/// (the chip's code name comes first, the product's name is in brackets).
fn bracketed(name: &str) -> String {
    let chars: Vec<char> = name.chars().collect();
    let mut last: Option<(usize, usize)> = None;
    let mut at = 0;
    while at < chars.len() {
        if chars[at] == '[' {
            let inner = chars[at + 1..]
                .iter()
                .take_while(|ch| !matches!(ch, '[' | ']'))
                .count();
            let end = at + 1 + inner;
            if inner > 0 && chars.get(end) == Some(&']') {
                last = Some((at + 1, end));
                at = end + 1;
                continue;
            }
        }
        at += 1;
    }
    let text: String = match last {
        Some((from, to)) => chars[from..to].iter().collect(),
        None => name.to_string(),
    };
    lp_py::strip(&text).to_string()
}

/// The first `count` characters of a line from its character `from`.
fn cut(line: &str, from: usize, count: usize) -> String {
    line.chars().skip(from).take(count).collect()
}

/// The product name of a PCI device in the text of a pci.ids: the vendor's block, then its
/// device lines (one tab); subsystems (two tabs) and the classes at the end are skipped.
fn pci_name_in(text: &str, vendor: i128, device: i128) -> String {
    let (want_vendor, want_device) = (format!("{vendor:04x}"), format!("{device:04x}"));
    let mut inside = false;
    for line in file_lines(text) {
        if lp_py::strip(line).is_empty() || line.starts_with('#') {
            continue;
        }
        if !line.starts_with('\t') {
            if inside {
                return String::new(); // the next vendor: this device is not listed
            }
            inside = lp_py::lower(&cut(line, 0, 4)) == want_vendor && cut(line, 4, 1) == " ";
        } else if inside
            && !line.starts_with("\t\t")
            && lp_py::lower(&cut(line, 1, 4)) == want_device
        {
            return bracketed(&line.chars().skip(5).collect::<String>());
        }
    }
    String::new()
}

/// Looked up in the first of `paths` that has it.
pub fn pci_name(paths: &[PathBuf], vendor: i128, device: i128) -> String {
    for path in paths {
        let Ok(data) = std::fs::read(path) else {
            continue;
        };
        let name = pci_name_in(&String::from_utf8_lossy(&data), vendor, device);
        if !name.is_empty() {
            return name;
        }
    }
    String::new()
}

/// AMD's own list (libdrm's amdgpu.ids: "744C,<tab>C8,<tab>AMD Radeon RX ...") says exactly
/// which card a device id and revision are, where pci.ids lists several under one.
pub fn amdgpu_name(path: &Path, device: i128, revision: Option<i128>) -> String {
    let (Some(revision), Ok(data)) = (revision, std::fs::read(path)) else {
        return String::new();
    };
    let text = String::from_utf8_lossy(&data);
    for line in file_lines(&text) {
        let parts: Vec<&str> = line.splitn(3, ',').map(lp_py::strip).collect();
        if parts.len() == 3 && !line.starts_with('#') {
            let ids = (int_of(parts[0], 16), int_of(parts[1], 16));
            if ids == (Some(device), Some(revision)) {
                return parts[2].to_string();
            }
        }
    }
    String::new()
}

/// The names the NVIDIA driver gives its cards, by their PCI address (its "Bus Location",
/// else the name of the card's folder, which is the same).
fn nvidia_models(folder: &Path) -> Vec<(String, String)> {
    let mut models: Vec<(String, String)> = Vec::new();
    for gpu in names(folder).unwrap_or_default() {
        let (mut model, mut place) = (None, None);
        let text = whole_text(&folder.join(&gpu).join("information"));
        for line in lp_py::splitlines(&text) {
            if let Some((key, value)) = line.split_once(':') {
                match lp_py::strip(key) {
                    "Model" => model = Some(lp_py::strip(value)),
                    "Bus Location" => place = Some(lp_py::strip(value)),
                    _ => {}
                }
            }
        }
        if let Some(model) = model.filter(|model| !model.is_empty()) {
            let place = lp_py::lower(place.filter(|p| !p.is_empty()).unwrap_or(&gpu));
            models.retain(|(known, _)| *known != place);
            models.push((place, model.to_string()));
        }
    }
    models
}

/// A card's PCI address ("0000:01:00.0").
fn slot(device: &Path) -> String {
    let text = whole_text(&device.join("uevent"));
    for line in lp_py::splitlines(&text) {
        if let Some(value) = line.strip_prefix("PCI_SLOT_NAME=") {
            return lp_py::lower(lp_py::strip(value));
        }
    }
    let resolved = std::fs::canonicalize(device).unwrap_or_else(|_| device.to_path_buf());
    let name = resolved.file_name().map(|name| name.to_string_lossy());
    lp_py::lower(&name.unwrap_or_default())
}

/// `card\d+`, whole: a card, not "card0-DP-1", one of its connectors.
pub fn is_card(name: &str) -> bool {
    name.strip_prefix("card").is_some_and(|digits| {
        !digits.is_empty() && digits.chars().all(|ch| lp_py::decimal(ch).is_some())
    })
}

/// The cards the kernel has, named: by the NVIDIA driver, by AMD's list or by pci.ids. One
/// whose name nothing knows is left out.
pub fn linux_adapters(
    drm: &Path,
    nvidia: &Path,
    pci_ids: &[PathBuf],
    amdgpu_ids: &Path,
) -> Vec<Adapter> {
    let models = nvidia_models(nvidia);
    let mut adapters = Vec::new();
    for card in names(drm).unwrap_or_default() {
        if !is_card(&card) {
            continue;
        }
        let device = drm.join(&card).join("device");
        let ids = (
            number(&device.join("vendor"), 16),
            number(&device.join("device"), 16),
        );
        let (Some(vendor), Some(product)) = ids else {
            continue;
        };
        let mut name = String::new();
        if vendor == NVIDIA_VENDOR {
            let place = slot(&device);
            name = match models.iter().find(|(known, _)| *known == place) {
                Some((_, model)) => model.clone(),
                // one name for one card, whatever the address
                None if models.len() == 1 => models[0].1.clone(),
                None => String::new(),
            };
        } else if vendor == AMD_VENDOR {
            name = amdgpu_name(amdgpu_ids, product, number(&device.join("revision"), 16));
        }
        if name.is_empty() {
            name = pci_name(pci_ids, vendor, product);
        }
        if !name.is_empty() {
            // (amdgpu tells its memory)
            let memory = number(&device.join("mem_info_vram_total"), 10).unwrap_or(0);
            adapters.push(Adapter {
                name,
                memory: u64::try_from(memory).unwrap_or(0),
                discrete: vendor == NVIDIA_VENDOR,
                slot: slot(&device),
            });
        }
    }
    adapters
}

// -- Windows -----------------------------------------------------------------------------

#[cfg(windows)]
mod windows {
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegEnumKeyExW, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_LOCAL_MACHINE,
        KEY_READ, REG_BINARY, REG_DWORD, REG_EXPAND_SZ, REG_QWORD, REG_SZ,
    };

    use super::{is_software, Adapter};

    const CPU_KEY: &str = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0";
    const DISPLAY_CLASS: &str =
        r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}";

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// An open key of the registry, closed with the value.
    struct Key(HKEY);

    impl Key {
        fn open(parent: HKEY, path: &str) -> Option<Key> {
            let path = wide(path);
            let mut opened: HKEY = std::ptr::null_mut();
            // SAFETY: the path ends with a zero; the handle is written on success only.
            let failed =
                unsafe { RegOpenKeyExW(parent, path.as_ptr(), 0, KEY_READ, &mut opened) != 0 };
            (!failed).then_some(Key(opened))
        }

        /// A value's type and bytes; none where there is no such value.
        fn value(&self, name: &str) -> Option<(u32, Vec<u8>)> {
            let name = wide(name);
            let (mut kind, mut size) = (0u32, 0u32);
            // SAFETY: asked first for the size, then into a buffer of that size.
            unsafe {
                let (none, name) = (std::ptr::null_mut(), name.as_ptr());
                let nothing = std::ptr::null_mut();
                if RegQueryValueExW(self.0, name, none, &mut kind, nothing, &mut size) != 0 {
                    return None;
                }
                let mut data = vec![0u8; size as usize];
                let to = data.as_mut_ptr();
                if RegQueryValueExW(self.0, name, none, &mut kind, to, &mut size) != 0 {
                    return None;
                }
                data.truncate(size as usize);
                Some((kind, data))
            }
        }

        /// A value that is text, up to its first zero.
        fn text(&self, name: &str) -> Option<String> {
            let (kind, data) = self.value(name)?;
            if kind != REG_SZ && kind != REG_EXPAND_SZ {
                return None;
            }
            let units: Vec<u16> = data
                .chunks_exact(2)
                .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
                .take_while(|unit| *unit != 0)
                .collect();
            Some(String::from_utf16_lossy(&units))
        }

        /// The names of the keys in this one.
        fn children(&self) -> Vec<String> {
            let mut found = Vec::new();
            for index in 0.. {
                let mut name = [0u16; 256];
                let mut length = name.len() as u32;
                // SAFETY: the buffer is of the length given; the rest is not asked for.
                let failed = unsafe {
                    let (to, none) = (name.as_mut_ptr(), std::ptr::null_mut());
                    let (class, written) = (std::ptr::null_mut(), std::ptr::null_mut());
                    RegEnumKeyExW(
                        self.0,
                        index,
                        to,
                        &mut length,
                        none,
                        class,
                        std::ptr::null_mut(),
                        written,
                    ) != 0
                };
                if failed {
                    break; // no more
                }
                found.push(String::from_utf16_lossy(&name[..length as usize]));
            }
            found
        }
    }

    impl Drop for Key {
        fn drop(&mut self) {
            // SAFETY: the handle was opened by this value and is closed once.
            unsafe {
                RegCloseKey(self.0);
            }
        }
    }

    /// The processor's name as its firmware gives it.
    pub fn cpu_name() -> String {
        Key::open(HKEY_LOCAL_MACHINE, CPU_KEY)
            .and_then(|key| key.text("ProcessorNameString"))
            .unwrap_or_default()
    }

    /// A display adapter's own memory in bytes: the 64-bit value, else the older one (which
    /// stops below 4 GB); a binary value is a little-endian number.
    fn memory_size(key: &Key) -> u64 {
        for name in [
            "HardwareInformation.qwMemorySize",
            "HardwareInformation.MemorySize",
        ] {
            let size = match key.value(name) {
                Some((REG_BINARY | REG_DWORD | REG_QWORD, data)) => {
                    let mut bytes = [0u8; 8];
                    let count = data.len().min(8);
                    bytes[..count].copy_from_slice(&data[..count]);
                    u64::from_le_bytes(bytes)
                }
                _ => 0,
            };
            if size > 0 {
                return size;
            }
        }
        0
    }

    /// The display adapters Windows has a driver for (the registry's display class), without
    /// the software and remote ones.
    pub fn adapters() -> Vec<Adapter> {
        let Some(display) = Key::open(HKEY_LOCAL_MACHINE, DISPLAY_CLASS) else {
            return Vec::new();
        };
        let mut adapters = Vec::new();
        for sub in display.children() {
            if sub.is_empty() || !sub.chars().all(lp_py::isdigit) {
                continue; // "Configuration", "Properties"
            }
            let Some(key) = Key::open(display.0, &sub) else {
                continue;
            };
            let name = key.text("DriverDesc").unwrap_or_default();
            let name = lp_py::strip(&name);
            if !name.is_empty() && !is_software(name) {
                adapters.push(Adapter {
                    name: name.to_string(),
                    memory: memory_size(&key),
                    ..Adapter::default()
                });
            }
        }
        adapters
    }

    /// The memory Windows sees, in bytes.
    pub fn visible_memory() -> u64 {
        use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
        // SAFETY: the structure is this frame's, with its length set.
        unsafe {
            let mut status: MEMORYSTATUSEX = std::mem::zeroed();
            status.dwLength = size_of::<MEMORYSTATUSEX>() as u32;
            if GlobalMemoryStatusEx(&mut status) != 0 {
                return status.ullTotalPhys;
            }
        }
        0
    }
}

// -- the computer ------------------------------------------------------------------------

/// What the system knows of the processor beyond /proc/cpuinfo or the registry, unless that
/// is only its architecture.
fn last_resort() -> String {
    #[cfg(windows)]
    let name = std::env::var("PROCESSOR_IDENTIFIER").unwrap_or_default();
    #[cfg(not(windows))]
    let name = std::process::Command::new("uname")
        .arg("-p")
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .map(|done| String::from_utf8_lossy(&done.stdout).into_owned())
        .unwrap_or_default();
    let name = lp_py::strip(&name);
    if ARCHITECTURES.contains(&lp_py::lower(name).as_str()) {
        String::new()
    } else {
        name.to_string()
    }
}

/// The memory the system leaves to programs, in bytes; 0 where it does not tell.
#[cfg(not(windows))]
fn visible_memory() -> u64 {
    let text = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    text.lines()
        .find_map(|line| line.strip_prefix("MemTotal:"))
        .and_then(|rest| rest.split_whitespace().next()?.parse::<u64>().ok())
        .map_or(0, |kilobytes| kilobytes * 1024)
}

/// Ask this computer. Each part on its own: one that cannot be read leaves the rest.
fn scan_machine() -> Found {
    #[cfg(windows)]
    let (cpu, adapters, modules, memory) = (
        windows::cpu_name(),
        windows::adapters(),
        smbios::windows_table().map_or(Vec::new(), |table| smbios::memory_modules(&table)),
        windows::visible_memory(),
    );
    #[cfg(not(windows))]
    let (cpu, adapters, modules, memory) = (
        linux_cpu_name(Path::new(CPUINFO)).unwrap_or_default(),
        linux_adapters(
            Path::new(DRM),
            Path::new(NVIDIA),
            &PCI_IDS.map(PathBuf::from),
            Path::new(AMDGPU_IDS),
        ),
        smbios::memory_modules(&smbios::linux_table(Path::new(smbios::ENTRIES))),
        visible_memory(),
    );
    Found {
        cpu: if cpu.is_empty() { last_resort() } else { cpu },
        adapters,
        modules,
        memory,
    }
}

static FOUND: std::sync::Mutex<Option<std::sync::Arc<Found>>> = std::sync::Mutex::new(None);

/// What this computer is made of. It is asked once, in the first call, and not again unless
/// [`scan_again`] says so.
pub fn scan() -> std::sync::Arc<Found> {
    let mut found = FOUND
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    std::sync::Arc::clone(found.get_or_insert_with(|| std::sync::Arc::new(scan_machine())))
}

/// scan(refresh=True): the computer asked again (a card was changed while Libre Panel runs).
pub fn scan_again() -> std::sync::Arc<Found> {
    let fresh = std::sync::Arc::new(scan_machine());
    let mut found = FOUND
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *found = Some(std::sync::Arc::clone(&fresh));
    fresh
}

/// The card to name: the one whose name has `prefer` (the `gpu` option of
/// `[sensors.librehardwaremonitor]`, which picks the card the readings come from), else a
/// discrete one, else the one with the most memory of its own (the first of equals).
pub fn pick_adapter<'a>(adapters: &'a [Adapter], prefer: &str) -> Option<&'a Adapter> {
    let wanted = lp_py::lower(prefer);
    let wanted = lp_py::strip(&wanted);
    let named: Vec<&Adapter> = adapters
        .iter()
        .filter(|a| !wanted.is_empty() && lp_py::lower(&a.name).contains(wanted))
        .collect();
    let pool = if named.is_empty() {
        adapters.iter().collect()
    } else {
        named
    };
    let mut best: Option<&Adapter> = None;
    for adapter in pool {
        if best.is_none_or(|b| (adapter.discrete, adapter.memory) > (b.discrete, b.memory)) {
            best = Some(adapter);
        }
    }
    best
}

/// The memory's name: from its modules ("Kingston DDR5-5600 32 GB"; the maker is left out
/// when they differ), else its size as the system tells it ("32 GB"); empty if neither is
/// known. Of kinds as frequent as each other the one that comes first counts.
pub fn memory_label(found: &Found) -> String {
    let modules = &found.modules;
    if modules.is_empty() {
        return match found.memory {
            0 => String::new(),
            bytes => size_label(installed_gb(bytes) as f64),
        };
    }
    let makers: Vec<String> = modules.iter().map(|m| tidy_maker(&m.maker)).collect();
    let maker = match makers.iter().all(|maker| *maker == makers[0]) {
        true => makers[0].as_str(),
        false => "",
    };
    let kinds: Vec<&str> = modules
        .iter()
        .map(|m| m.kind.as_str())
        .filter(|kind| !kind.is_empty())
        .collect();
    let count = |kind: &str| kinds.iter().filter(|k| **k == kind).count();
    let mut kind = "";
    for candidate in &kinds {
        if kind.is_empty() || count(candidate) > count(kind) {
            kind = candidate;
        }
    }
    let megabytes: u64 = modules.iter().map(|m| m.megabytes as u64).sum();
    memory_name(
        maker,
        kind,
        memory_speed(found) as i64,
        megabytes as f64 / 1024.0,
    )
}

/// The speed the memory runs at, in megatransfers a second: the slowest module's (it sets the
/// pace of the others); 0 where no module tells it.
pub fn memory_speed(found: &Found) -> u16 {
    found
        .modules
        .iter()
        .map(|m| m.speed)
        .filter(|speed| *speed != 0)
        .min()
        .unwrap_or(0)
}

/// The three names, tidied: the processor's, the card's, the memory's; empty where nothing
/// was found.
pub fn describe(found: &Found, prefer_gpu: &str) -> [String; 3] {
    let adapter = pick_adapter(&found.adapters, prefer_gpu);
    [
        tidy_cpu(&found.cpu),
        adapter.map_or(String::new(), |adapter| tidy_gpu(&adapter.name)),
        memory_label(found),
    ]
}

/// `cpu.name`, `gpu.name` and `mem.name`: what was found, or the user's own text
/// (`[hardware]`), which replaces it; and `mem.speed` where the modules tell it.
pub struct HardwareSource {
    readings: Readings,
}

impl HardwareSource {
    /// `custom`: `[hardware]`'s cpu_name, gpu_name and memory_name; `prefer`: the `gpu` of
    /// `[sensors.librehardwaremonitor]`.
    pub fn new(custom: [&str; 3], prefer: &str) -> HardwareSource {
        HardwareSource::of(&scan(), custom, prefer)
    }

    pub fn of(found: &Found, custom: [&str; 3], prefer: &str) -> HardwareSource {
        let names = describe(found, prefer);
        let mut readings = Readings::new();
        for (n, (key, label)) in NAME_KEYS.iter().zip(["CPU", "GPU", "Memory"]).enumerate() {
            let text = if custom[n].is_empty() {
                names[n].as_str()
            } else {
                custom[n]
            };
            if !text.is_empty() {
                readings.insert(
                    key.to_string(),
                    Reading {
                        label: label.into(),
                        ..Reading::of(Value::Str(text.into()))
                    },
                );
            }
        }
        let speed = memory_speed(found);
        if speed != 0 {
            readings.insert(
                SPEED_KEY.into(),
                Reading {
                    unit: "MT/s".into(),
                    label: "Memory speed".into(),
                    ..Reading::of(Value::Float(speed as f64))
                },
            );
        }
        HardwareSource { readings }
    }

    pub fn read(&self) -> Readings {
        self.readings.clone()
    }
}

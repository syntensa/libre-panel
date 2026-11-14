//! The memory modules in the firmware's SMBIOS table, for the memory's name
//! (sensors/smbios.py).
//!
//! The table has one "type 17" structure per memory slot: who made the module, what kind of
//! memory it is, how fast it runs and how big it is. Windows hands the table over whole
//! (GetSystemFirmwareTable: no WMI, no program, no administrator rights); Linux has a file
//! per structure under /sys/firmware/dmi/entries, which most systems let only root read.
//!
//! Of a structure only what names the kind of memory is taken: serial numbers, asset tags
//! and part numbers (they name one module) stay where they are.

use std::path::Path;

/// the structure type of a memory slot
pub const MEMORY_DEVICE: u8 = 17;
pub const END_OF_TABLE: u8 = 127;
pub const ENTRIES: &str = "/sys/firmware/dmi/entries";

/// The memory types (byte 0x12 of a slot) that say something about the memory.
fn memory_type(id: u8) -> &'static str {
    match id {
        0x0F => "SDRAM",
        0x12 => "DDR",
        0x13 | 0x14 => "DDR2",
        0x18 => "DDR3",
        0x1A => "DDR4",
        0x1B => "LPDDR",
        0x1C => "LPDDR2",
        0x1D => "LPDDR3",
        0x1E => "LPDDR4",
        0x20 => "HBM",
        0x21 => "HBM2",
        0x22 => "DDR5",
        0x23 => "LPDDR5",
        0x24 => "HBM3",
        _ => "",
    }
}

/// a "logical non-volatile device"
const NON_VOLATILE_TYPE: u8 = 0x1F;
/// bit 12 of the type detail
const NON_VOLATILE_DETAIL: u16 = 0x1000;

/// One memory module that is in its slot.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Module {
    pub megabytes: u32,
    /// "DDR5"
    pub kind: String,
    /// megatransfers a second it runs at; 0: not told
    pub speed: u16,
    /// as the firmware spells it
    pub maker: String,
}

/// A structure: its type, its fixed part, its strings.
pub type Structure<'a> = (u8, &'a [u8], Vec<&'a [u8]>);

/// The structures of a table, each as (type, its fixed part, its strings). A structure is its
/// fixed part, then its strings (each ends with a zero byte), then one more zero.
pub fn structures(data: &[u8]) -> Vec<Structure<'_>> {
    let mut found = Vec::new();
    let mut pos = 0;
    while pos + 4 <= data.len() {
        let (kind, length) = (data[pos], data[pos + 1] as usize);
        if length < 4 || pos + length > data.len() {
            break; // not a table, or cut off
        }
        let start = pos + length;
        let stop = data[start..]
            .windows(2)
            .position(|pair| pair == [0, 0])
            .map(|at| start + at);
        let text = &data[start..stop.unwrap_or(data.len())];
        let strings = if text.is_empty() {
            Vec::new()
        } else {
            text.split(|byte| *byte == 0).collect()
        };
        found.push((kind, &data[pos..start], strings));
        match stop {
            Some(stop) if kind != END_OF_TABLE => pos = stop + 2,
            _ => break,
        }
    }
    found
}

/// String number `index` (counted from 1; 0: none).
fn text(strings: &[&[u8]], index: u8) -> String {
    match strings.get((index as usize).wrapping_sub(1)) {
        Some(bytes) if index >= 1 => lp_py::strip(&String::from_utf8_lossy(bytes)).to_string(),
        _ => String::new(),
    }
}

fn u16_at(body: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([body[at], body[at + 1]])
}

fn module(body: &[u8], strings: &[&[u8]]) -> Option<Module> {
    if body.len() < 0x15 {
        return None; // SMBIOS 2.1 is the first to say how big a module is
    }
    let size = u16_at(body, 0x0C);
    if size == 0 || size == 0xFFFF {
        return None; // nothing in the slot, or not told
    }
    let megabytes = if size == 0x7FFF {
        // 32 GB or more: the size is in the extended field
        if body.len() < 0x20 {
            return None;
        }
        u32::from_le_bytes([body[0x1C], body[0x1D], body[0x1E], body[0x1F]]) & 0x7FFF_FFFF
    } else if size & 0x8000 != 0 {
        ((size & 0x7FFF) / 1024) as u32 // counted in kilobytes
    } else {
        size as u32
    };
    let kind = body[0x12];
    let detail = u16_at(body, 0x13);
    if megabytes == 0 || kind == NON_VOLATILE_TYPE || detail & NON_VOLATILE_DETAIL != 0 {
        return None; // memory that keeps its data off is no working memory
    }
    let mut speeds = Vec::new();
    if body.len() >= 0x22 {
        speeds.push(u16_at(body, 0x20)); // SMBIOS 2.7: the speed it runs at comes first
    }
    if body.len() >= 0x17 {
        speeds.push(u16_at(body, 0x15)); // SMBIOS 2.3: the speed it is made for
    }
    let speed = speeds
        .into_iter()
        .find(|speed| *speed != 0 && *speed != 0xFFFF)
        .unwrap_or(0);
    let maker = if body.len() > 0x17 {
        text(strings, body[0x17])
    } else {
        String::new()
    };
    Some(Module {
        megabytes,
        kind: memory_type(kind).to_string(),
        speed,
        maker,
    })
}

/// The modules in a table (or in the structures read from Linux's files).
pub fn memory_modules(data: &[u8]) -> Vec<Module> {
    structures(data)
        .into_iter()
        .filter(|(kind, ..)| *kind == MEMORY_DEVICE)
        .filter_map(|(_, body, strings)| module(body, &strings))
        .collect()
}

/// The SMBIOS table Windows has (the firmware table `RSMB`); none without one.
#[cfg(windows)]
pub fn windows_table() -> Option<Vec<u8>> {
    use windows_sys::Win32::System::SystemInformation::GetSystemFirmwareTable;
    let signature = u32::from_be_bytes(*b"RSMB");
    // SAFETY: asked first for the size, then into a buffer of that size.
    let raw = unsafe {
        let size = GetSystemFirmwareTable(signature, 0, std::ptr::null_mut(), 0);
        if size == 0 {
            return None;
        }
        let mut buffer = vec![0u8; size as usize];
        let got = GetSystemFirmwareTable(signature, 0, buffer.as_mut_ptr(), size);
        if !(8..=size).contains(&got) {
            return None;
        }
        buffer.truncate(got as usize);
        buffer
    };
    // RawSMBIOSData: four bytes about the version, the length of the table, the table
    let length = u32::from_le_bytes([raw[4], raw[5], raw[6], raw[7]]) as usize;
    Some(raw[8..raw.len().min(8usize.saturating_add(length))].to_vec())
}

/// The memory structures of /sys/firmware/dmi/entries, one after the other; what the user
/// may not read (usually all of it) is left out.
pub fn linux_table(entries: &Path) -> Vec<u8> {
    let mut data = Vec::new();
    let prefix = format!("{MEMORY_DEVICE}-");
    for name in crate::sysfs::names(entries).unwrap_or_default() {
        if name.starts_with(&prefix) {
            if let Ok(raw) = std::fs::read(entries.join(&name).join("raw")) {
                data.extend(raw);
            }
        }
    }
    data
}

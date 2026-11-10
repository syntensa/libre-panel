//! Which threads of the processor belong to one core (sensors/topology.py): the physical
//! cores, as Windows and Linux tell them, so that a list of the cores shows eight cores of
//! two threads each rather than sixteen thin bars.
//!
//! A core's threads are given as their places in the list of the processor's threads, the
//! order the load source measures them in. Where that cannot be told for sure (more than 64
//! threads on Windows, threads that are offline on Linux, macOS) there are no cores, and the
//! lists show the threads as before.

use std::path::Path;

use indexmap::IndexMap;
use num_traits::ToPrimitive;

/// LOGICAL_PROCESSOR_RELATIONSHIP's RelationProcessorCore
const RELATION_PROCESSOR_CORE: u32 = 0;

fn u16_at(raw: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(raw.get(at..at + 2)?.try_into().ok()?))
}

fn u32_at(raw: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(raw.get(at..at + 4)?.try_into().ok()?))
}

fn u64_at(raw: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(raw.get(at..at + 8)?.try_into().ok()?))
}

/// parse_processor_cores: the cores in what GetLogicalProcessorInformationEx
/// (RelationProcessorCore) wrote: one SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX after another,
/// each a PROCESSOR_RELATIONSHIP with its GROUP_AFFINITY masks (a thread is group * 64 + its
/// bit). None where a record is cut short (Python raises there, and no cores are told).
pub fn parse_processor_cores(raw: &[u8]) -> Option<Vec<Vec<usize>>> {
    let mut cores = Vec::new();
    let mut at = 0;
    while at + 8 <= raw.len() {
        let (relation, size) = (u32_at(raw, at)?, u32_at(raw, at + 4)? as usize);
        if size < 8 || at + size > raw.len() {
            break;
        }
        if relation == RELATION_PROCESSOR_CORE {
            // PROCESSOR_RELATIONSHIP: Flags, EfficiencyClass, Reserved[20], GroupCount, then
            // GroupMask[GroupCount] (KAFFINITY Mask, WORD Group, WORD Reserved[3]) from 24 on
            let groups = u16_at(raw, at + 8 + 22)? as usize;
            let mut threads = Vec::new();
            for g in 0..groups {
                let place = at + 8 + 24 + 16 * g;
                let (mask, group) = (u64_at(raw, place)?, u16_at(raw, place + 8)? as usize);
                threads.extend(
                    (0..64)
                        .filter(|bit| mask >> bit & 1 == 1)
                        .map(|bit| group * 64 + bit),
                );
            }
            threads.sort_unstable();
            cores.push(threads);
        }
        at += size;
    }
    Some(cores)
}

#[cfg(windows)]
fn system_cores() -> Option<Vec<Vec<usize>>> {
    use windows_sys::Win32::System::SystemInformation::{
        GetLogicalProcessorInformationEx, RelationProcessorCore,
    };
    // SAFETY: the system writes at most `size` bytes into a buffer of that many (in u64s, so
    // that its records are aligned).
    unsafe {
        let mut size = 0u32;
        // (how large the answer is)
        GetLogicalProcessorInformationEx(RelationProcessorCore, std::ptr::null_mut(), &mut size);
        if size == 0 {
            return None;
        }
        let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
        if GetLogicalProcessorInformationEx(
            RelationProcessorCore,
            buffer.as_mut_ptr().cast(),
            &mut size,
        ) == 0
        {
            return None;
        }
        let bytes = std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), size as usize);
        parse_processor_cores(bytes)
    }
}

/// linux_cores: the threads of each core from sysfs (package and core id of every thread
/// that is online), as their places among the online threads, in the order the folder gives
/// its threads.
pub fn linux_cores(root: &Path) -> Option<Vec<Vec<usize>>> {
    let mut found: IndexMap<(String, String), Vec<u64>> = IndexMap::new();
    let mut numbers = Vec::new();
    for entry in std::fs::read_dir(root).ok()?.flatten() {
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        // ("cpu[0-9]*": cpu, then a digit, then anything)
        let Some(rest) = name.strip_prefix("cpu") else {
            continue;
        };
        if !rest.starts_with(|c: char| c.is_ascii_digit()) {
            continue;
        }
        // (int(): white space around it and underscores between digits are taken, as Python
        // takes them; a name that is no number is no thread)
        let Some(number) = lp_py::int_of_str(rest).and_then(|n| n.to_u64()) else {
            continue;
        };
        let read = |what: &str| {
            std::fs::read_to_string(entry.path().join("topology").join(what))
                .ok()
                .map(|text| lp_py::strip(&text).to_string())
        };
        // (an offline thread has no topology)
        let (Some(package), Some(core)) = (read("physical_package_id"), read("core_id")) else {
            continue;
        };
        numbers.push(number);
        found.entry((package, core)).or_default().push(number);
    }
    if numbers.is_empty() {
        return None;
    }
    let mut sorted = numbers.clone();
    sorted.sort_unstable();
    let place = |number: u64| sorted.iter().position(|n| *n == number).unwrap_or(0);
    Some(
        found
            .into_values()
            .map(|threads| {
                let mut places: Vec<usize> = threads.into_iter().map(place).collect();
                places.sort_unstable();
                places
            })
            .collect(),
    )
}

#[cfg(target_os = "linux")]
fn system_cores() -> Option<Vec<Vec<usize>>> {
    linux_cores(Path::new("/sys/devices/system/cpu"))
}

#[cfg(not(any(windows, target_os = "linux")))]
fn system_cores() -> Option<Vec<Vec<usize>>> {
    None
}

/// checked: `cores` if they hold each of the `threads` once and one has more than one, in the
/// order of their first thread.
pub fn checked(cores: Option<Vec<Vec<usize>>>, threads: usize) -> Option<Vec<Vec<usize>>> {
    let mut cores = cores.filter(|cores| !cores.is_empty())?;
    let mut every: Vec<usize> = cores.iter().flatten().copied().collect();
    every.sort_unstable();
    if every != (0..threads).collect::<Vec<_>>() || cores.iter().all(|core| core.len() == 1) {
        return None;
    }
    cores.sort_by_key(|core| core.first().copied());
    Some(cores)
}

/// physical_cores: the cores of this processor as lists of their threads' places (0 to
/// `threads` - 1), in the order of their first thread; none where a core has only one thread
/// (nothing to group) or the cores cannot be told for sure.
pub fn physical_cores(threads: usize) -> Option<Vec<Vec<usize>>> {
    checked(system_cores(), threads)
}

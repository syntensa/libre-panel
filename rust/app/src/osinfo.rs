//! More of what the system tells of the computer, as psutil tells it to
//! sensors/psutil_provider.py: its drives and how full they are, the chips' temperatures and
//! fans (Linux), the battery, and its programs with their processor time and memory.

/// A mounted drive (psutil's sdiskpart).
#[derive(Clone, Debug, PartialEq)]
pub struct Partition {
    pub device: String,
    pub mountpoint: String,
    pub fstype: String,
    pub opts: String,
}

/// How full a drive is, in bytes, and the share used of what a user may use, to a tenth.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Usage {
    pub total: f64,
    pub used: f64,
    pub free: f64,
    pub percent: f64,
}

/// A chip's reading: its label ("" where it has none) and its value.
pub type Entries = Vec<(String, f64)>;

/// psutil's sbattery: the charge in percent, the seconds left (-1 unknown, -2 on mains), and
/// whether it is plugged in (none: not told).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Battery {
    pub percent: f64,
    pub secsleft: i64,
    pub plugged: Option<bool>,
}

/// A program that runs: its id, its name, the processor's seconds it has used, and its share
/// of the memory in percent.
#[derive(Clone, Debug, PartialEq)]
pub struct Process {
    pub pid: u64,
    pub name: String,
    pub cpu_seconds: f64,
    pub memory_percent: f64,
}

/// usage_percent(used, total, round_=1) as psutil works it out.
pub fn usage_percent(used: f64, total: f64) -> f64 {
    if total <= 0.0 {
        return 0.0;
    }
    lp_py::round_digits(used / total * 100.0, 1)
}

/// The address this computer uses towards the internet (no packet is sent).
pub fn local_ip() -> String {
    let probe = || -> std::io::Result<String> {
        let socket = std::net::UdpSocket::bind("0.0.0.0:0")?;
        socket.connect("192.0.2.1:9")?; // TEST-NET: routing only
        Ok(socket.local_addr()?.ip().to_string())
    };
    probe().unwrap_or_default()
}

pub use imp::*;

#[cfg(target_os = "linux")]
mod imp {
    use std::collections::BTreeSet;
    use std::path::{Path, PathBuf};

    use indexmap::IndexMap;

    use super::{usage_percent, Battery, Entries, Partition, Process, Usage};

    /// A small file's text; none where it cannot be read.
    fn cat(path: &Path) -> Option<String> {
        std::fs::read(path)
            .ok()
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
    }

    /// getmntent's escapes in a field of the mounts' table: \040 and the like.
    fn unescaped(field: &str) -> String {
        let bytes = field.as_bytes();
        let mut out = Vec::with_capacity(bytes.len());
        let mut at = 0;
        while at < bytes.len() {
            let octal = |n: usize| bytes.get(at + n).filter(|b| (b'0'..=b'7').contains(b));
            if bytes[at] == b'\\' {
                if let (Some(a), Some(b), Some(c)) = (octal(1), octal(2), octal(3)) {
                    let code =
                        u32::from(a - b'0') * 64 + u32::from(b - b'0') * 8 + u32::from(c - b'0');
                    out.push(code as u8);
                    at += 4;
                    continue;
                }
                if bytes.get(at + 1) == Some(&b'\\') {
                    out.push(b'\\');
                    at += 2;
                    continue;
                }
            }
            out.push(bytes[at]);
            at += 1;
        }
        String::from_utf8_lossy(&out).into_owned()
    }

    /// disk_partitions(all=False): the mounted drives whose file system is one of a device.
    pub fn partitions() -> Vec<Partition> {
        let mut fstypes = BTreeSet::new();
        for line in cat(Path::new("/proc/filesystems"))
            .unwrap_or_default()
            .lines()
        {
            let line = lp_py::strip(line);
            if !line.starts_with("nodev") {
                fstypes.insert(line.to_string());
            } else if line.split('\t').nth(1) == Some("zfs") {
                fstypes.insert("zfs".to_string());
            }
        }
        let mounts = if Path::new("/etc/mtab").is_file() {
            "/etc/mtab"
        } else {
            "/proc/self/mounts"
        };
        let mut found = Vec::new();
        for line in cat(Path::new(mounts)).unwrap_or_default().lines() {
            let mut fields = line.split([' ', '\t']).filter(|field| !field.is_empty());
            let (Some(device), Some(mountpoint), Some(fstype), Some(opts)) =
                (fields.next(), fields.next(), fields.next(), fields.next())
            else {
                continue;
            };
            if device.starts_with('#') {
                continue;
            }
            let device = unescaped(device);
            let device = if device == "none" {
                String::new()
            } else {
                device
            };
            if device.is_empty() || !fstypes.contains(fstype) {
                continue;
            }
            found.push(Partition {
                device,
                mountpoint: unescaped(mountpoint),
                fstype: fstype.to_string(),
                opts: unescaped(opts),
            });
        }
        found
    }

    /// disk_usage(path).
    pub fn usage(path: &str) -> Option<Usage> {
        let path = std::ffi::CString::new(path).ok()?;
        // SAFETY: statvfs writes into this frame's own structure.
        let stat = unsafe {
            let mut stat: libc::statvfs = std::mem::zeroed();
            if libc::statvfs(path.as_ptr(), &mut stat) != 0 {
                return None;
            }
            stat
        };
        let block = stat.f_frsize as f64;
        let total = stat.f_blocks as f64 * block;
        let used = total - stat.f_bfree as f64 * block;
        let free = stat.f_bavail as f64 * block;
        Some(Usage {
            total,
            used,
            free,
            percent: usage_percent(used, used + free),
        })
    }

    /// Whether `path` is where a file system is mounted (os.path.ismount).
    pub fn is_mount(path: &str) -> bool {
        use std::os::unix::fs::MetadataExt;
        let here = Path::new(path);
        let (Ok(own), Ok(parent)) = (
            std::fs::symlink_metadata(here),
            std::fs::symlink_metadata(here.join("..")),
        ) else {
            return false;
        };
        !own.file_type().is_symlink() && (own.dev() != parent.dev() || own.ino() == parent.ino())
    }

    /// The paths in the chips' folders whose names begin `kind` and have an underscore, up to
    /// it: `/sys/class/hwmon/hwmon2/temp1`, sorted, each once.
    fn bases(kind: &str, inside: &str) -> BTreeSet<String> {
        let mut found = BTreeSet::new();
        for chip in std::fs::read_dir("/sys/class/hwmon")
            .into_iter()
            .flatten()
            .flatten()
        {
            let Ok(name) = chip.file_name().into_string() else {
                continue;
            };
            if !name.starts_with("hwmon") {
                continue;
            }
            let folder: PathBuf = chip.path().join(inside);
            for file in std::fs::read_dir(&folder).into_iter().flatten().flatten() {
                let Ok(file) = file.file_name().into_string() else {
                    continue;
                };
                if file.starts_with(kind) && file[kind.len()..].contains('_') {
                    let whole = format!("{}/{file}", folder.display());
                    // (the path up to its first underscore, as x.split('_')[0] has it)
                    found.insert(whole.split('_').next().unwrap_or("").to_string());
                }
            }
        }
        found
    }

    fn chip_of(base: &str) -> Option<String> {
        let folder = Path::new(base).parent()?;
        Some(lp_py::strip(&cat(&folder.join("name"))?).to_string())
    }

    fn label_of(base: &str) -> String {
        cat(Path::new(&format!("{base}_label")))
            .map(|text| lp_py::strip(&text).to_string())
            .unwrap_or_default()
    }

    /// sensors_temperatures(): each chip's temperatures in °C, in the order of their files.
    pub fn temperatures() -> IndexMap<String, Entries> {
        let mut found: IndexMap<String, Entries> = IndexMap::new();
        let mut all = bases("temp", "");
        all.extend(bases("temp", "device"));
        for base in &all {
            let current = cat(Path::new(&format!("{base}_input")))
                .and_then(|text| lp_py::float_of_str(lp_py::strip(&text)));
            let (Some(current), Some(chip)) = (current, chip_of(base)) else {
                continue;
            };
            found
                .entry(chip)
                .or_default()
                .push((label_of(base), current / 1000.0));
        }
        if all.is_empty() {
            // (no chips: the thermal zones)
            let mut zones: Vec<PathBuf> = std::fs::read_dir("/sys/class/thermal")
                .into_iter()
                .flatten()
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .is_some_and(|name| name.starts_with("thermal_zone"))
                })
                .collect();
            zones.sort();
            for zone in zones {
                let current = cat(&zone.join("temp"))
                    .and_then(|text| lp_py::float_of_str(lp_py::strip(&text)));
                let kind = cat(&zone.join("type")).map(|text| lp_py::strip(&text).to_string());
                if let (Some(current), Some(kind)) = (current, kind) {
                    found
                        .entry(kind)
                        .or_default()
                        .push((String::new(), current / 1000.0));
                }
            }
        }
        found
    }

    /// sensors_fans(): each chip's fans in RPM.
    pub fn fans() -> IndexMap<String, Entries> {
        let mut found: IndexMap<String, Entries> = IndexMap::new();
        let mut all = bases("fan", "");
        if all.is_empty() {
            all = bases("fan", "device");
        }
        for base in &all {
            let Some(text) = cat(Path::new(&format!("{base}_input"))) else {
                continue;
            };
            let (Some(current), Some(chip)) = (
                lp_py::int_of_str(&text).and_then(|n| lp_py::num::int_to_float(&n).ok()),
                chip_of(base),
            ) else {
                continue;
            };
            found
                .entry(chip)
                .or_default()
                .push((label_of(base), current));
        }
        found
    }

    /// sensors_battery(): the first battery's charge, time left and whether mains is in.
    pub fn battery() -> Option<Battery> {
        let supply = Path::new("/sys/class/power_supply");
        let first = std::fs::read_dir(supply)
            .ok()?
            .flatten()
            .filter_map(|entry| entry.file_name().into_string().ok())
            .filter(|name| name.starts_with("BAT") || lp_py::lower(name).contains("battery"))
            .min()?;
        let root = supply.join(first);
        // (the first of the files that is there, as a number where it is one)
        let number = |paths: &[PathBuf]| -> Option<f64> {
            let text = paths.iter().find_map(|path| cat(path))?;
            lp_py::int_of_str(&text).and_then(|n| lp_py::num::int_to_float(&n).ok())
        };
        let energy_now = number(&[root.join("energy_now"), root.join("charge_now")]);
        let power_now = number(&[root.join("power_now"), root.join("current_now")]);
        let energy_full = number(&[root.join("energy_full"), root.join("charge_full")]);
        let time_to_empty = number(&[root.join("time_to_empty_now")]);
        let percent = match (energy_full, energy_now) {
            (Some(full), Some(now)) => {
                if full == 0.0 {
                    0.0
                } else {
                    100.0 * now / full
                }
            }
            _ => number(&[root.join("capacity")])?,
        };
        let plugged = match number(&[supply.join("AC0/online"), supply.join("AC/online")]) {
            Some(online) => Some(online == 1.0),
            None => {
                let status = cat(&root.join("status")).unwrap_or_default();
                match lp_py::lower(lp_py::strip(&status)).as_str() {
                    "discharging" => Some(false),
                    "charging" | "full" => Some(true),
                    _ => None,
                }
            }
        };
        let secsleft = if plugged == Some(true) {
            -2
        } else if let (Some(now), Some(power)) = (energy_now, power_now) {
            if power == 0.0 {
                -1
            } else {
                (now / power.abs() * 3600.0) as i64
            }
        } else if let Some(minutes) = time_to_empty {
            let seconds = (minutes * 60.0) as i64;
            if seconds < 0 {
                -1
            } else {
                seconds
            }
        } else {
            -1
        };
        Some(Battery {
            percent,
            secsleft,
            plugged,
        })
    }

    /// The programs that run (psutil's process_iter with name, memory_percent, cpu_times).
    pub fn processes() -> Vec<Process> {
        // SAFETY: sysconf reads two numbers of the system.
        let (ticks, page) = unsafe {
            (
                libc::sysconf(libc::_SC_CLK_TCK) as f64,
                libc::sysconf(libc::_SC_PAGESIZE) as f64,
            )
        };
        let total = cat(Path::new("/proc/meminfo"))
            .and_then(|text| {
                let line = text.lines().find(|line| line.starts_with("MemTotal:"))?;
                line.split_ascii_whitespace().nth(1)?.parse::<f64>().ok()
            })
            .map_or(0.0, |kb| kb * 1024.0);
        let mut found = Vec::new();
        for entry in std::fs::read_dir("/proc").into_iter().flatten().flatten() {
            let Some(pid) = entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse::<u64>().ok())
            else {
                continue;
            };
            let folder = entry.path();
            let Some(stat) = cat(&folder.join("stat")) else {
                continue;
            };
            // ("pid (name) state ...": the name may hold brackets and spaces)
            let (Some(open), Some(close)) = (stat.find('('), stat.rfind(')')) else {
                continue;
            };
            let mut name = stat[open + 1..close].to_string();
            let fields: Vec<&str> = stat[close + 1..].split_ascii_whitespace().collect();
            let ticks_of = |n: usize| fields.get(n).and_then(|f| f.parse::<f64>().ok());
            let (Some(user), Some(system)) = (ticks_of(11), ticks_of(12)) else {
                continue;
            };
            if name.chars().count() >= 15 {
                // (the kernel cuts a name at 15 letters: the program's own word for it)
                let line = std::fs::read(folder.join("cmdline")).unwrap_or_default();
                let first = line.split(|b| *b == 0).next().unwrap_or_default();
                let first = String::from_utf8_lossy(first);
                let base = first.rsplit('/').next().unwrap_or("");
                if base.starts_with(&name) {
                    name = base.to_string();
                }
            }
            let pages = cat(&folder.join("statm"))
                .and_then(|text| text.split_ascii_whitespace().nth(1)?.parse::<f64>().ok())
                .unwrap_or(0.0);
            found.push(Process {
                pid,
                name,
                cpu_seconds: (user + system) / ticks,
                memory_percent: if total > 0.0 {
                    pages * page / total * 100.0
                } else {
                    0.0
                },
            });
        }
        found
    }

    /// swap_memory().percent: none where it cannot be told (it is in the memory's numbers).
    pub fn swap_percent() -> Option<f64> {
        None
    }
}

#[cfg(windows)]
mod imp {
    use indexmap::IndexMap;
    use windows_sys::Wdk::System::SystemInformation::{
        NtQuerySystemInformation, SystemProcessInformation,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        GetDiskFreeSpaceExW, GetDriveTypeW, GetLogicalDriveStringsW, GetVolumeInformationW,
    };
    use windows_sys::Win32::System::Performance::{
        PdhAddEnglishCounterW, PdhCloseQuery, PdhCollectQueryData, PdhGetFormattedCounterValue,
        PdhOpenQueryW, PDH_FMT_COUNTERVALUE, PDH_FMT_DOUBLE,
    };
    use windows_sys::Win32::System::Power::{GetSystemPowerStatus, SYSTEM_POWER_STATUS};
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};

    use super::{usage_percent, Battery, Entries, Partition, Process, Usage};

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// disk_partitions(all=False): the drives with a letter that have a file system.
    pub fn partitions() -> Vec<Partition> {
        const REMOVABLE: u32 = 2;
        const FIXED: u32 = 3;
        const REMOTE: u32 = 4;
        const CDROM: u32 = 5;
        const RAMDISK: u32 = 6;
        const FILE_READ_ONLY_VOLUME: u32 = 0x0008_0000;
        let mut letters = [0u16; 512];
        // SAFETY: the drives' names are written into a buffer of the size given.
        let length =
            unsafe { GetLogicalDriveStringsW(letters.len() as u32 - 1, letters.as_mut_ptr()) };
        let mut found = Vec::new();
        for drive in letters[..length as usize].split(|unit| *unit == 0) {
            if drive.is_empty() {
                continue;
            }
            let name = String::from_utf16_lossy(drive);
            let root = wide(&name);
            // SAFETY: the root is NUL-ended; the names are written into buffers of the sizes
            // given.
            let (kind, fstype, flags) = unsafe {
                let kind = GetDriveTypeW(root.as_ptr());
                let mut fstype = [0u16; 261];
                let mut flags = 0u32;
                let known = GetVolumeInformationW(
                    root.as_ptr(),
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &mut flags,
                    fstype.as_mut_ptr(),
                    fstype.len() as u32,
                ) != 0;
                let end = fstype.iter().position(|unit| *unit == 0).unwrap_or(0);
                let fstype = if known {
                    String::from_utf16_lossy(&fstype[..end])
                } else {
                    String::new()
                };
                (kind, fstype, if known { flags } else { 0 })
            };
            let what = match kind {
                REMOVABLE => "removable",
                FIXED => "fixed",
                REMOTE => "remote",
                CDROM => "cdrom",
                RAMDISK => "ramdisk",
                _ => continue, // (unknown, or no drive there)
            };
            let access = if fstype.is_empty() {
                ""
            } else if flags & FILE_READ_ONLY_VOLUME != 0 {
                "ro"
            } else {
                "rw"
            };
            let opts = if access.is_empty() {
                what.to_string()
            } else {
                format!("{access},{what}")
            };
            found.push(Partition {
                device: name.clone(),
                mountpoint: name,
                fstype,
                opts,
            });
        }
        found
    }

    /// disk_usage(path).
    pub fn usage(path: &str) -> Option<Usage> {
        let (mut for_caller, mut total, mut free) = (0u64, 0u64, 0u64);
        // SAFETY: three numbers of this frame are written.
        if unsafe {
            GetDiskFreeSpaceExW(wide(path).as_ptr(), &mut for_caller, &mut total, &mut free)
        } == 0
        {
            return None;
        }
        let used = total.saturating_sub(free) as f64;
        Some(Usage {
            total: total as f64,
            used,
            free: for_caller as f64,
            percent: usage_percent(used, total as f64),
        })
    }

    pub fn is_mount(_path: &str) -> bool {
        false
    }

    /// Windows tells no temperatures and fans without a driver (the sensor service has them).
    pub fn temperatures() -> IndexMap<String, Entries> {
        IndexMap::new()
    }

    pub fn fans() -> IndexMap<String, Entries> {
        IndexMap::new()
    }

    /// sensors_battery().
    pub fn battery() -> Option<Battery> {
        // SAFETY: the status is written into this frame's own structure.
        let status = unsafe {
            let mut status: SYSTEM_POWER_STATUS = std::mem::zeroed();
            if GetSystemPowerStatus(&mut status) == 0 {
                return None;
            }
            status
        };
        // (128: no battery; 255: not known)
        if status.BatteryFlag & 128 != 0 || status.BatteryFlag == 255 {
            return None;
        }
        let plugged = status.ACLineStatus == 1;
        let secsleft = if plugged {
            -2
        } else if status.BatteryLifeTime == u32::MAX {
            -1
        } else {
            i64::from(status.BatteryLifeTime)
        };
        Some(Battery {
            percent: f64::from(status.BatteryLifePercent),
            secsleft,
            plugged: Some(plugged),
        })
    }

    /// A text of the system's (UNICODE_STRING).
    #[repr(C)]
    struct Text {
        length: u16,
        maximum: u16,
        buffer: *const u16,
    }

    /// The start of SYSTEM_PROCESS_INFORMATION: one program in the system's list of them.
    #[repr(C)]
    struct Entry {
        next: u32,
        threads: u32,
        private_working_set: i64,
        hard_faults: u32,
        threads_high: u32,
        cycles: u64,
        created: i64,
        user: i64,
        kernel: i64,
        name: Text,
        priority: i32,
        pid: usize,
        parent: usize,
        handles: u32,
        session: u32,
        key: usize,
        peak_virtual: usize,
        virtual_size: usize,
        page_faults: u32,
        peak_working_set: usize,
        working_set: usize,
    }

    /// The programs that run, from the system's list of them.
    pub fn processes() -> Vec<Process> {
        // SAFETY: the list is written into a buffer of the size given (in u64s, so that its
        // entries are aligned) and read entry by entry within what was written; a name is
        // read inside the same buffer's lifetime.
        unsafe {
            let mut status: MEMORYSTATUSEX = std::mem::zeroed();
            status.dwLength = size_of::<MEMORYSTATUSEX>() as u32;
            let total = if GlobalMemoryStatusEx(&mut status) != 0 {
                status.ullTotalPhys as f64
            } else {
                0.0
            };
            let mut size = 1u32 << 20;
            let mut buffer: Vec<u64> = Vec::new();
            let mut written = 0u32;
            for _ in 0..6 {
                buffer = vec![0u64; (size as usize).div_ceil(8)];
                let code = NtQuerySystemInformation(
                    SystemProcessInformation,
                    buffer.as_mut_ptr().cast(),
                    size,
                    &mut written,
                );
                if code >= 0 {
                    break;
                }
                written = 0;
                size *= 2; // (the list is longer than the buffer)
            }
            let mut found = Vec::new();
            let start = buffer.as_ptr().cast::<u8>();
            let mut at = 0usize;
            while written as usize >= at + size_of::<Entry>() {
                let entry = &*start.add(at).cast::<Entry>();
                let name = if entry.pid == 0 {
                    "System Idle Process".to_string()
                } else if entry.name.buffer.is_null() {
                    "System".to_string()
                } else {
                    String::from_utf16_lossy(std::slice::from_raw_parts(
                        entry.name.buffer,
                        usize::from(entry.name.length) / 2,
                    ))
                };
                found.push(Process {
                    pid: entry.pid as u64,
                    name,
                    cpu_seconds: (entry.user + entry.kernel) as f64 / 1e7,
                    memory_percent: if total > 0.0 {
                        entry.working_set as f64 / total * 100.0
                    } else {
                        0.0
                    },
                });
                if entry.next == 0 {
                    break;
                }
                at += entry.next as usize;
            }
            found
        }
    }

    /// swap_memory().percent: the page file's use, from the counter Windows keeps of it.
    pub fn swap_percent() -> Option<f64> {
        // SAFETY: the query and its counter are PDH's, closed before this returns; the value
        // is written into this frame's own structure.
        unsafe {
            let mut query = std::ptr::null_mut();
            if PdhOpenQueryW(std::ptr::null(), 0, &mut query) != 0 {
                return None;
            }
            let mut counter = std::ptr::null_mut();
            let path = wide(r"\Paging File(_Total)\% Usage");
            let mut found = None;
            if PdhAddEnglishCounterW(query, path.as_ptr(), 0, &mut counter) == 0
                && PdhCollectQueryData(query) == 0
            {
                let mut value: PDH_FMT_COUNTERVALUE = std::mem::zeroed();
                if PdhGetFormattedCounterValue(
                    counter,
                    PDH_FMT_DOUBLE,
                    std::ptr::null_mut(),
                    &mut value,
                ) == 0
                {
                    found = Some(lp_py::round_digits(value.Anonymous.doubleValue, 1));
                }
            }
            PdhCloseQuery(query);
            found
        }
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
mod imp {
    use indexmap::IndexMap;

    use super::{Battery, Entries, Partition, Process, Usage};

    pub fn partitions() -> Vec<Partition> {
        Vec::new()
    }

    pub fn usage(_path: &str) -> Option<Usage> {
        None
    }

    pub fn is_mount(_path: &str) -> bool {
        false
    }

    pub fn temperatures() -> IndexMap<String, Entries> {
        IndexMap::new()
    }

    pub fn fans() -> IndexMap<String, Entries> {
        IndexMap::new()
    }

    pub fn battery() -> Option<Battery> {
        None
    }

    pub fn processes() -> Vec<Process> {
        Vec::new()
    }

    pub fn swap_percent() -> Option<f64> {
        None
    }
}

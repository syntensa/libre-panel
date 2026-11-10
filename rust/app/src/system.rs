//! The computer's own readings as the system gives them (what sensors/psutil_provider.py
//! asks psutil for): on Linux from the kernel's files, on Windows from its API.

pub use imp::*;

#[cfg(not(windows))]
mod imp {
    use indexmap::IndexMap;

    fn file(path: &str) -> Option<String> {
        std::fs::read_to_string(path).ok()
    }

    /// The processor's busy time and whole time since the computer started, in the kernel's
    /// ticks (psutil's cpu_times: the guests' time is in the users' already).
    pub fn cpu_times() -> Option<(f64, f64)> {
        let stat = file("/proc/stat")?;
        let fields: Vec<f64> = stat
            .lines()
            .next()?
            .split_whitespace()
            .skip(1)
            .filter_map(|field| field.parse().ok())
            .collect();
        if fields.len() < 4 {
            return None;
        }
        let whole: f64 = fields.iter().take(8).sum();
        let idle = fields[3] + fields.get(4).copied().unwrap_or(0.0);
        Some((whole - idle, whole))
    }

    /// /proc/meminfo's numbers by name, in bytes.
    pub fn meminfo() -> IndexMap<String, f64> {
        file("/proc/meminfo")
            .unwrap_or_default()
            .lines()
            .filter_map(|line| {
                let (name, rest) = line.split_once(':')?;
                let kb: f64 = rest.split_whitespace().next()?.parse().ok()?;
                Some((name.to_string(), kb * 1024.0))
            })
            .collect()
    }

    /// The bytes all network interfaces received and sent.
    pub fn net_bytes() -> Option<(f64, f64)> {
        let text = file("/proc/net/dev")?;
        let (mut down, mut up) = (0.0, 0.0);
        for line in text.lines().skip(2) {
            let (_, rest) = line.split_once(':')?;
            let fields: Vec<f64> = rest
                .split_whitespace()
                .filter_map(|field| field.parse().ok())
                .collect();
            down += fields.first().copied().unwrap_or(0.0);
            up += fields.get(8).copied().unwrap_or(0.0);
        }
        Some((down, up))
    }

    /// The bytes the disks read and wrote: whole disks, as /sys/block has them, no partitions.
    pub fn disk_bytes() -> Option<(f64, f64)> {
        let text = file("/proc/diskstats")?;
        let (mut read, mut written) = (0.0, 0.0);
        for line in text.lines() {
            let fields: Vec<&str> = line.split_whitespace().collect();
            let Some(name) = fields.get(2) else { continue };
            if name.starts_with("loop") || name.starts_with("ram") {
                continue;
            }
            if !std::path::Path::new("/sys/block").join(name).exists() {
                continue;
            }
            let sectors = |n: usize| fields.get(n).and_then(|f| f.parse::<f64>().ok());
            read += sectors(5).unwrap_or(0.0) * 512.0;
            written += sectors(9).unwrap_or(0.0) * 512.0;
        }
        Some((read, written))
    }

    /// The processor's clock in MHz: the mean of its cores'.
    pub fn cpu_freq() -> Option<f64> {
        let mut clocks = Vec::new();
        for entry in std::fs::read_dir("/sys/devices/system/cpu").ok()?.flatten() {
            let path = entry.path().join("cpufreq").join("scaling_cur_freq");
            if let Some(khz) = std::fs::read_to_string(path)
                .ok()
                .and_then(|text| text.trim().parse::<f64>().ok())
            {
                clocks.push(khz / 1000.0);
            }
        }
        if clocks.is_empty() {
            clocks = file("/proc/cpuinfo")?
                .lines()
                .filter(|line| line.starts_with("cpu MHz"))
                .filter_map(|line| line.split_once(':')?.1.trim().parse().ok())
                .collect();
        }
        (!clocks.is_empty()).then(|| clocks.iter().sum::<f64>() / clocks.len() as f64)
    }

    /// How long the computer runs, in seconds.
    pub fn uptime() -> Option<f64> {
        file("/proc/uptime")?
            .split_whitespace()
            .next()?
            .parse()
            .ok()
    }
}

#[cfg(windows)]
mod imp {
    use std::ffi::c_void;

    use indexmap::IndexMap;
    use windows_sys::Win32::Foundation::{CloseHandle, FILETIME, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::NetworkManagement::IpHelper::{
        GetAdaptersAddresses, GetIfEntry2, IP_ADAPTER_ADDRESSES_LH, MIB_IF_ROW2,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateFileW, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    };
    use windows_sys::Win32::System::Ioctl::{DISK_PERFORMANCE, IOCTL_DISK_PERFORMANCE};
    use windows_sys::Win32::System::Power::CallNtPowerInformation;
    use windows_sys::Win32::System::SystemInformation::{
        GetTickCount64, GlobalMemoryStatusEx, MEMORYSTATUSEX,
    };
    use windows_sys::Win32::System::Threading::GetSystemTimes;
    use windows_sys::Win32::System::IO::DeviceIoControl;

    fn ticks(time: &FILETIME) -> f64 {
        ((u64::from(time.dwHighDateTime) << 32) | u64::from(time.dwLowDateTime)) as f64
    }

    /// The processor's busy time and whole time since the computer started (GetSystemTimes:
    /// the kernel's time has the idle time in it).
    pub fn cpu_times() -> Option<(f64, f64)> {
        let zero = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        let (mut idle, mut kernel, mut user) = (zero, zero, zero);
        if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
            return None;
        }
        let whole = ticks(&kernel) + ticks(&user);
        Some((whole - ticks(&idle), whole))
    }

    /// The memory's numbers under the names Linux gives them, in bytes (no swap: psutil
    /// reads the page file's use from a performance counter).
    pub fn meminfo() -> IndexMap<String, f64> {
        let mut status: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
        status.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
        let mut found = IndexMap::new();
        if unsafe { GlobalMemoryStatusEx(&mut status) } != 0 {
            found.insert("MemTotal".to_string(), status.ullTotalPhys as f64);
            found.insert("MemAvailable".to_string(), status.ullAvailPhys as f64);
        }
        found
    }

    /// The bytes all network adapters received and sent (each adapter once, as
    /// GetAdaptersAddresses lists them; its counters from GetIfEntry2).
    pub fn net_bytes() -> Option<(f64, f64)> {
        const AF_UNSPEC: u32 = 0;
        const ERROR_BUFFER_OVERFLOW: u32 = 111;
        let mut size: u32 = 16 * 1024;
        let mut buffer: Vec<u64> = Vec::new();
        for _ in 0..4 {
            buffer = vec![0u64; (size as usize).div_ceil(8)];
            let first = buffer.as_mut_ptr().cast::<IP_ADAPTER_ADDRESSES_LH>();
            let code =
                unsafe { GetAdaptersAddresses(AF_UNSPEC, 0, std::ptr::null(), first, &mut size) };
            if code == 0 {
                break;
            }
            if code != ERROR_BUFFER_OVERFLOW {
                return None;
            }
        }
        let (mut down, mut up) = (0.0, 0.0);
        let mut adapter = buffer.as_ptr().cast::<IP_ADAPTER_ADDRESSES_LH>();
        while !adapter.is_null() {
            let mut row: MIB_IF_ROW2 = unsafe { std::mem::zeroed() };
            row.InterfaceLuid = unsafe { (*adapter).Luid };
            if unsafe { GetIfEntry2(&mut row) } == 0 {
                down += row.InOctets as f64;
                up += row.OutOctets as f64;
            }
            adapter = unsafe { (*adapter).Next };
        }
        Some((down, up))
    }

    /// The bytes the disks read and wrote (each physical drive's own counters).
    pub fn disk_bytes() -> Option<(f64, f64)> {
        let (mut read, mut written, mut any) = (0.0, 0.0, false);
        for n in 0..32 {
            let name: Vec<u16> = format!("\\\\.\\PhysicalDrive{n}\0")
                .encode_utf16()
                .collect();
            let drive = unsafe {
                CreateFileW(
                    name.as_ptr(),
                    0,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    std::ptr::null(),
                    OPEN_EXISTING,
                    0,
                    std::ptr::null_mut(),
                )
            };
            if drive == INVALID_HANDLE_VALUE {
                continue;
            }
            let mut counted: DISK_PERFORMANCE = unsafe { std::mem::zeroed() };
            let mut returned = 0u32;
            let done = unsafe {
                DeviceIoControl(
                    drive,
                    IOCTL_DISK_PERFORMANCE,
                    std::ptr::null(),
                    0,
                    (&mut counted as *mut DISK_PERFORMANCE).cast::<c_void>(),
                    std::mem::size_of::<DISK_PERFORMANCE>() as u32,
                    &mut returned,
                    std::ptr::null_mut(),
                )
            };
            unsafe { CloseHandle(drive) };
            if done != 0 {
                read += counted.BytesRead as f64;
                written += counted.BytesWritten as f64;
                any = true;
            }
        }
        any.then_some((read, written))
    }

    /// What CallNtPowerInformation says of a processor (PROCESSOR_POWER_INFORMATION).
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct ProcessorPower {
        number: u32,
        max_mhz: u32,
        current_mhz: u32,
        mhz_limit: u32,
        max_idle_state: u32,
        current_idle_state: u32,
    }

    /// The processor's clock in MHz, as the first processor says it.
    pub fn cpu_freq() -> Option<f64> {
        const PROCESSOR_INFORMATION: i32 = 11;
        let threads = std::thread::available_parallelism().map_or(1, |n| n.get());
        let mut said = vec![ProcessorPower::default(); threads.max(1)];
        let size = (said.len() * std::mem::size_of::<ProcessorPower>()) as u32;
        let status = unsafe {
            CallNtPowerInformation(
                PROCESSOR_INFORMATION,
                std::ptr::null(),
                0,
                said.as_mut_ptr().cast::<c_void>(),
                size,
            )
        };
        (status == 0 && said[0].current_mhz > 0).then(|| f64::from(said[0].current_mhz))
    }

    /// How long the computer runs, in seconds.
    pub fn uptime() -> Option<f64> {
        Some(unsafe { GetTickCount64() } as f64 / 1000.0)
    }
}

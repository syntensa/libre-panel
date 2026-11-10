//! What the services ask the computer: which programs run, which one is in front, whether
//! the microphone is in use, how long the system has been up (services/machine.py).
//!
//! Nothing here touches a program: it only asks for names and paths. The services take a
//! `Machine`, so tests bring one made up.

use crate::registry::{Entry, Hive, Registry, View};

const MICROPHONE_KEY: &str =
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Program {
    /// the file name in lower case: "game.exe"
    pub name: String,
    pub pid: i64,
    /// where it lies, or the folder it works in (Wine); "" if unknown
    pub path: String,
}

/// The computer a service runs on.
pub trait Machine: Send + Sync {
    /// Only Windows can say which window is in front without a desktop toolkit.
    fn has_foreground(&self) -> bool {
        false
    }

    /// Every program that runs and can be read, once per process.
    fn programs(&self) -> Vec<Program>;

    /// The process of the window in front (0: none); None where that is not known.
    fn foreground_pid(&self) -> Option<i64> {
        None
    }

    /// The file name, in lower case, of the program whose window is in front.
    fn foreground_name(&self) -> Option<String> {
        None
    }

    /// Whether a program is recording right now (Windows; elsewhere false).
    fn microphone_in_use(&self) -> bool {
        false
    }

    /// Seconds since the process `pid` started, None if it cannot be told.
    fn running_s(&self, pid: i64) -> Option<f64>;

    /// Seconds since the system started, None if it cannot be told.
    fn uptime_s(&self) -> Option<f64>;
}

/// A program as the services take it, of what the system says of a process: the file's name
/// (else the process's), and for a Windows program under Wine or Proton the process's name
/// and the folder it works in (`folder` is asked only then).
pub fn program_of(
    pid: i64,
    name: &str,
    exe: &str,
    folder: impl FnOnce() -> String,
) -> Option<Program> {
    let file = exe.rsplit(std::path::MAIN_SEPARATOR).next().unwrap_or("");
    let mut shown = if file.is_empty() { name } else { file };
    let mut path = exe.to_string();
    if lp_py::lower(shown).starts_with("wine") {
        if !name.is_empty() {
            shown = name;
        }
        path = folder();
    }
    (!shown.is_empty()).then(|| Program {
        name: lp_py::lower(shown),
        pid,
        path,
    })
}

/// microphone_in_use_windows: Windows keeps, for every program that uses the microphone,
/// when it began and ended (`LastUsedTimeStop` is 0 while it records). Only reads.
pub fn microphone_in_use(registry: &dyn Registry) -> bool {
    let keys = |key: &str| {
        let found = registry.subkeys(Hive::CurrentUser, View::Own, key);
        found.unwrap_or_default()
    };
    let records = |key: &str| {
        let value = |name: &str| registry.value(Hive::CurrentUser, View::Own, key, name);
        let (Some(begun), Some(ended)) = (value("LastUsedTimeStart"), value("LastUsedTimeStop"))
        else {
            return false;
        };
        let began = match begun {
            Entry::Number(begun) => begun != 0,
            Entry::Text(begun) => !begun.is_empty(),
            Entry::Other => true,
        };
        began && ended == Entry::Number(0)
    };
    keys(MICROPHONE_KEY).iter().any(|name| {
        let key = format!("{MICROPHONE_KEY}\\{name}");
        match name.as_str() {
            // classic programs: one key each, named after the path
            "NonPackaged" => keys(&key)
                .iter()
                .any(|sub| records(&format!("{key}\\{sub}"))),
            _ => records(&key),
        }
    })
}

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64())
}

/// This computer.
pub struct Computer;

#[cfg(target_os = "linux")]
mod system {
    use super::{now, program_of, Program};

    fn text(path: &str) -> Option<String> {
        std::fs::read(path)
            .ok()
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
    }

    /// psutil's readlink: what follows a zero is dropped, and " (deleted)" of a file that is
    /// there.
    fn link(path: &str) -> Option<String> {
        let target = std::fs::read_link(path).ok()?;
        let target = target.to_string_lossy();
        let target = target.split('\0').next().unwrap_or("");
        match target.strip_suffix(" (deleted)") {
            Some(plain) if !std::path::Path::new(target).exists() => Some(plain.to_string()),
            _ => Some(target.to_string()),
        }
    }

    /// The name and the fields after it of /proc/<pid>/stat ("pid (name) state ...": the
    /// name may hold brackets and spaces).
    fn stat(pid: i64) -> Option<(String, Vec<String>)> {
        let stat = text(&format!("/proc/{pid}/stat"))?;
        let (open, close) = (stat.find('(')?, stat.rfind(')')?);
        let fields = stat[close + 1..]
            .split_ascii_whitespace()
            .map(str::to_string);
        Some((stat[open + 1..close].to_string(), fields.collect()))
    }

    /// The process's name: the kernel cuts one at 15 letters, so a longer one is the
    /// program's own word for it where that begins with it.
    fn name(pid: i64) -> Option<String> {
        let (name, _) = stat(pid)?;
        if name.chars().count() < 15 {
            return Some(name);
        }
        let line = std::fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
        let first = String::from_utf8_lossy(line.split(|byte| *byte == 0).next().unwrap_or(&[]));
        let base = first.rsplit('/').next().unwrap_or("");
        Some(if base.starts_with(&name) {
            base.to_string()
        } else {
            name
        })
    }

    fn boot() -> Option<f64> {
        let stat = text("/proc/stat")?;
        let line = stat.lines().find(|line| line.starts_with("btime"))?;
        line.split_ascii_whitespace().nth(1)?.parse().ok()
    }

    pub fn programs() -> Vec<Program> {
        let mut pids: Vec<i64> = (std::fs::read_dir("/proc").into_iter().flatten().flatten())
            .filter_map(|entry| entry.file_name().to_str()?.parse().ok())
            .collect();
        pids.sort_unstable();
        let mut found = Vec::new();
        for pid in pids {
            let Some(name) = name(pid) else {
                continue; // (gone since the list was read)
            };
            let exe = link(&format!("/proc/{pid}/exe")).unwrap_or_default();
            let folder = || link(&format!("/proc/{pid}/cwd")).unwrap_or_default();
            found.extend(program_of(pid, &name, &exe, folder));
        }
        found
    }

    pub fn running_s(pid: i64) -> Option<f64> {
        // SAFETY: sysconf reads a number of the system.
        let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) } as f64;
        let started = stat(pid)?.1.get(19)?.parse::<f64>().ok()? / ticks + boot()?;
        Some((now() - started).max(0.0))
    }

    pub fn uptime_s() -> Option<f64> {
        Some((now() - boot()?).max(0.0))
    }

    pub fn foreground_pid() -> Option<i64> {
        None
    }

    pub fn foreground_name() -> Option<String> {
        None
    }
}

#[cfg(windows)]
mod system {
    use windows_sys::Wdk::System::SystemInformation::{
        NtQuerySystemInformation, SystemProcessInformation, SYSTEM_INFORMATION_CLASS,
    };
    use windows_sys::Win32::Foundation::{CloseHandle, FILETIME, HANDLE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Storage::FileSystem::QueryDosDeviceW;
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;
    use windows_sys::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowThreadProcessId,
    };

    use super::{now, program_of, Program};

    /// SystemProcessIdInformation: a process's program by its id, without opening it.
    const PROCESS_ID_INFORMATION: SYSTEM_INFORMATION_CLASS = 88;
    /// hundreds of nanoseconds from 1601 to 1970
    const EPOCH: f64 = 11_644_473_600.0;

    struct Opened(HANDLE);

    impl Drop for Opened {
        fn drop(&mut self) {
            // SAFETY: the handle was opened here and is closed once.
            unsafe { CloseHandle(self.0) };
        }
    }

    /// A text of the system's (UNICODE_STRING).
    #[repr(C)]
    struct Text {
        length: u16,
        maximum: u16,
        buffer: *mut u16,
    }

    /// SYSTEM_PROCESS_ID_INFORMATION.
    #[repr(C)]
    struct Named {
        pid: usize,
        name: Text,
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
    }

    /// A path of the system's own ("\Device\HarddiskVolume3\...") with its drive's letter, as
    /// psutil's convert_dos_path writes it.
    fn lettered(path: &str) -> String {
        let parts: Vec<&str> = path.split('\\').collect();
        let device = parts[..parts.len().min(3)].join("\\");
        let rest = &path[device.len()..];
        for letter in 'A'..='Z' {
            let drive: Vec<u16> = format!("{letter}:").encode_utf16().chain(Some(0)).collect();
            let mut target = [0u16; 512];
            // SAFETY: the drive's name ends with a zero; its device is written into a buffer
            // of the length given.
            let length = unsafe {
                QueryDosDeviceW(drive.as_ptr(), target.as_mut_ptr(), target.len() as u32)
            };
            let end = target.iter().position(|unit| *unit == 0).unwrap_or(0);
            if length != 0 && String::from_utf16_lossy(&target[..end]) == device {
                return format!("{letter}:{rest}");
            }
        }
        rest.to_string()
    }

    /// Where the program of a process lies ("" for the system's own, a bare name for its
    /// kinds of memory); None where the system does not say.
    fn exe(pid: i64) -> Option<String> {
        if pid == 0 {
            return None;
        }
        let mut room = vec![0u16; 0x8000];
        let mut named = Named {
            pid: pid as usize,
            name: Text {
                length: 0,
                maximum: 0xfffe,
                buffer: room.as_mut_ptr(),
            },
        };
        // SAFETY: the name is written into `room`, whose length in bytes `maximum` does not
        // go beyond, and read within what was written.
        let status = unsafe {
            NtQuerySystemInformation(
                PROCESS_ID_INFORMATION,
                std::ptr::from_mut(&mut named).cast(),
                size_of::<Named>() as u32,
                std::ptr::null_mut(),
            )
        };
        if status < 0 {
            return None;
        }
        let length = (usize::from(named.name.length) / 2).min(room.len());
        let path = String::from_utf16_lossy(&room[..length]);
        Some(match path.starts_with('\\') {
            true => lettered(&path),
            false => path,
        })
    }

    /// The ids of the processes in the system's list of them.
    fn listed() -> Vec<i64> {
        let mut found = Vec::new();
        // SAFETY: the snapshot is read entry by entry into a structure of the size it is told
        // of, and closed before this returns.
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if snapshot == INVALID_HANDLE_VALUE {
                return found;
            }
            let snapshot = Opened(snapshot);
            let mut entry: PROCESSENTRY32W = std::mem::zeroed();
            entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
            let mut more = Process32FirstW(snapshot.0, &mut entry);
            while more != 0 {
                found.push(i64::from(entry.th32ProcessID));
                more = Process32NextW(snapshot.0, &mut entry);
            }
        }
        found
    }

    fn file(path: &str) -> &str {
        path.rsplit(['\\', '/']).next().unwrap_or("")
    }

    /// psutil's name of a process: the file of its program, and the system's two by name.
    fn name(pid: i64, exe: Option<&str>) -> Option<String> {
        match pid {
            0 => Some("System Idle Process".to_string()),
            4 => Some("System".to_string()),
            _ => exe.map(|exe| file(exe).to_string()),
        }
    }

    pub fn programs() -> Vec<Program> {
        let mut pids = listed();
        pids.sort_unstable();
        pids.dedup();
        let programs = pids.into_iter().filter_map(|pid| {
            let exe = exe(pid);
            let name = name(pid, exe.as_deref()).unwrap_or_default();
            program_of(pid, &name, &exe.unwrap_or_default(), String::new)
        });
        programs.collect()
    }

    /// When a process started, from the system's list of them (seconds since 1970).
    fn created(pid: i64) -> Option<f64> {
        let mut size = 1u32 << 20;
        // SAFETY: the list is written into a buffer of the size given (in u64s, so that its
        // entries are aligned) and read entry by entry within what was written.
        unsafe {
            for _ in 0..6 {
                let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
                let mut written = 0u32;
                let code = NtQuerySystemInformation(
                    SystemProcessInformation,
                    buffer.as_mut_ptr().cast(),
                    size,
                    &mut written,
                );
                if code < 0 {
                    size *= 2; // (the list is longer than the buffer)
                    continue;
                }
                let start = buffer.as_ptr().cast::<u8>();
                let mut at = 0usize;
                while written as usize >= at + size_of::<Entry>() {
                    let entry = &*start.add(at).cast::<Entry>();
                    if entry.pid as i64 == pid {
                        return Some(entry.created as f64 / 1e7 - EPOCH);
                    }
                    if entry.next == 0 {
                        break;
                    }
                    at += entry.next as usize;
                }
                return None;
            }
        }
        None
    }

    pub fn running_s(pid: i64) -> Option<f64> {
        if pid == 0 || pid == 4 {
            return uptime_s(); // (the system's own: since it started)
        }
        // SAFETY: a process is opened only to ask when it started; the four times are
        // written into the four places given.
        let asked = unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid as u32);
            (!handle.is_null())
                .then(|| Opened(handle))
                .and_then(|process| {
                    let mut times = [FILETIME {
                        dwLowDateTime: 0,
                        dwHighDateTime: 0,
                    }; 4];
                    let [made, ended, kernel, user] = &mut times;
                    let read = GetProcessTimes(process.0, made, ended, kernel, user);
                    let ticks = u64::from(times[0].dwHighDateTime) << 32
                        | u64::from(times[0].dwLowDateTime);
                    (read != 0).then_some(ticks as f64 / 1e7 - EPOCH)
                })
        };
        let started = asked.or_else(|| created(pid))?;
        Some((now() - started).max(0.0))
    }

    pub fn uptime_s() -> Option<f64> {
        // SAFETY: a call without arguments.
        Some(unsafe { GetTickCount64() } as f64 / 1000.0)
    }

    pub fn foreground_pid() -> Option<i64> {
        // SAFETY: the window in front and the process it belongs to, written to `pid`.
        unsafe {
            let window = GetForegroundWindow();
            if window.is_null() {
                return Some(0);
            }
            let mut pid = 0u32;
            GetWindowThreadProcessId(window, &mut pid);
            Some(i64::from(pid))
        }
    }

    pub fn foreground_name() -> Option<String> {
        let pid = foreground_pid().filter(|pid| *pid != 0)?;
        let exe = exe(pid)?;
        let shown = match file(&exe) {
            "" => name(pid, Some(&exe))?,
            file => file.to_string(),
        };
        Some(lp_py::lower(&shown))
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
mod system {
    use super::Program;

    pub fn programs() -> Vec<Program> {
        Vec::new()
    }

    pub fn running_s(_pid: i64) -> Option<f64> {
        None
    }

    pub fn uptime_s() -> Option<f64> {
        None
    }

    pub fn foreground_pid() -> Option<i64> {
        None
    }

    pub fn foreground_name() -> Option<String> {
        None
    }
}

impl Machine for Computer {
    fn has_foreground(&self) -> bool {
        cfg!(windows)
    }

    fn programs(&self) -> Vec<Program> {
        system::programs()
    }

    fn foreground_pid(&self) -> Option<i64> {
        system::foreground_pid()
    }

    fn foreground_name(&self) -> Option<String> {
        system::foreground_name()
    }

    fn microphone_in_use(&self) -> bool {
        crate::registry::system().is_some_and(|registry| microphone_in_use(&*registry))
    }

    fn running_s(&self, pid: i64) -> Option<f64> {
        system::running_s(pid)
    }

    fn uptime_s(&self) -> Option<f64> {
        system::uptime_s()
    }
}

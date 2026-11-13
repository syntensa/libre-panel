//! What Libre Panel costs this PC, and where (load_report.py): *Status → Diagnose → Load*
//! (`GET /api/load`), and the same lines in a report. Libre Panel is this process (the panels'
//! loops, the sensors, the app's server, the tray, the shortcuts), an engine or an ffmpeg for
//! each panel in the video mode, PresentMon while a game's frame rate is read, the window
//! while it is open (with WebView2's processes on Windows, WebKit's on Linux) and, on
//! Windows, the sensor service. [`Sampler`] measures them: each one's processor time over the
//! last [`WINDOW_S`], its memory, and the threads of this process by their names.
//!
//! - **Memory, two ways.** The private memory first (psutil's USS: on Linux the private pages
//!   the kernel counts, on Windows the pages of the working set that are not shared: the Task
//!   Manager's Memory column), measured every [`PRIVATE_S`] (it costs); beside it the working
//!   set, every sample. A process that does not let it be read (the sensor service, as the
//!   system) has none; the total is then of those that do.
//! - **Per PC and per core.** One whole core counts 100 %, the PC per core divided by the
//!   logical processors.
//! - **The sensor service** is the process the Service Control Manager names for
//!   `LibrePanelSensors`, else the one of its program; the report says which.
//! - **What the main process holds**, as far as it can tell from within: the C heap's bytes in
//!   use and free, its threads, on Windows what it has committed. (Python's report counted its
//!   objects and modules there; a Rust program has none.)
//! - **Only while asked.** The thread samples only while somebody asked within [`IDLE_S`],
//!   then ends. A request never waits for a sample: it gets the last one.

use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use lp_py::{Dict, Value};
use lp_sensors::service::LhmStatus;

/// the processor time is measured over this long: the card asks this often
pub const WINDOW_S: f64 = 2.0;
/// nobody asked for this long: the thread ends (and the report is old)
pub const IDLE_S: f64 = 15.0;
/// the sensor service, not found, is looked for again after this long
const LOOK_S: f64 = 30.0;
/// the window's process (window.json) is looked for this often
const WINDOW_LOOK_S: f64 = 10.0;
/// the tree of processes is walked this often; in between those found are measured
const TREE_S: f64 = 6.0;
/// threads named in the text; the rest go with "other"
const TEXT_THREADS: usize = 10;
/// each process's private memory is measured this often
const PRIVATE_S: f64 = 6.0;
/// what the main process holds is counted this often
const INSIDE_S: f64 = 30.0;
const OTHER: &str = "other";
const SERVICE_EXE: &str = "libre-panel-sensors.exe";

/// What is known of a process at once: its program's name, its parent, its processor seconds
/// so far, its working set and when it started (seconds since 1970).
#[derive(Clone, Debug)]
struct Facts {
    name: String,
    cpu: f64,
    rss: u64,
    born: f64,
}

fn wall() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

#[cfg(target_os = "linux")]
mod os {
    use super::Facts;
    use std::path::Path;

    fn cat(path: &Path) -> Option<String> {
        std::fs::read_to_string(path).ok()
    }

    fn ticks() -> f64 {
        // SAFETY: sysconf reads a number of the system.
        unsafe { libc::sysconf(libc::_SC_CLK_TCK) as f64 }
    }

    fn page() -> u64 {
        // SAFETY: as above.
        unsafe { libc::sysconf(libc::_SC_PAGESIZE) as u64 }
    }

    fn boot_time() -> f64 {
        cat(Path::new("/proc/stat"))
            .and_then(|text| {
                let line = text.lines().find(|line| line.starts_with("btime "))?;
                line[6..].trim().parse::<f64>().ok()
            })
            .unwrap_or(0.0)
    }

    /// (name, the fields after it) of a /proc/.../stat
    fn stat(path: &Path) -> Option<(String, Vec<String>)> {
        let text = cat(path)?;
        let (open, close) = (text.find('(')?, text.rfind(')')?);
        let fields = text[close + 1..]
            .split_ascii_whitespace()
            .map(str::to_string)
            .collect();
        Some((text[open + 1..close].to_string(), fields))
    }

    pub fn cpu_count() -> usize {
        // SAFETY: as above.
        let n = unsafe { libc::sysconf(libc::_SC_NPROCESSORS_ONLN) };
        usize::try_from(n).unwrap_or(1)
    }

    pub fn facts(pid: u32) -> Option<Facts> {
        let folder = Path::new("/proc").join(pid.to_string());
        let (mut name, fields) = stat(&folder.join("stat"))?;
        if fields.first().is_some_and(|state| state == "Z") {
            return None; // (it has ended and waits to be asked how: no process of the app's any more)
        }
        let field = |n: usize| fields.get(n).and_then(|f| f.parse::<f64>().ok());
        let (user, system, start) = (field(11)?, field(12)?, field(19)?);
        let rss = cat(&folder.join("statm"))
            .and_then(|text| text.split_ascii_whitespace().nth(1)?.parse::<u64>().ok())
            .unwrap_or(0)
            * page();
        if name.chars().count() >= 15 {
            // (the kernel cuts a name at 15 letters: the program's own word for it)
            let base = cmdline(pid).into_iter().next().unwrap_or_default();
            let base = base.rsplit('/').next().unwrap_or("").to_string();
            if base.starts_with(&name) {
                name = base;
            }
        }
        Some(Facts {
            name,
            cpu: (user + system) / ticks(),
            rss,
            born: boot_time() + start / ticks(),
        })
    }

    pub fn cmdline(pid: u32) -> Vec<String> {
        let line = std::fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
        line.split(|b| *b == 0)
            .filter(|part| !part.is_empty())
            .map(|part| String::from_utf8_lossy(part).into_owned())
            .collect()
    }

    /// psutil's uss: the private pages, clean and dirty (and huge ones)
    pub fn private(pid: u32) -> Option<u64> {
        let text = cat(&Path::new("/proc")
            .join(pid.to_string())
            .join("smaps_rollup"))
        .or_else(|| cat(&Path::new("/proc").join(pid.to_string()).join("smaps")))?;
        let mut total = 0u64;
        for line in text.lines() {
            let wanted = ["Private_Clean:", "Private_Dirty:", "Private_Hugetlb:"];
            if let Some(key) = wanted.iter().find(|key| line.starts_with(**key)) {
                let kb = line[key.len()..]
                    .split_ascii_whitespace()
                    .next()
                    .and_then(|n| n.parse::<u64>().ok())?;
                total += kb * 1024;
            }
        }
        Some(total)
    }

    /// (process, parent) of every process
    pub fn tree() -> Vec<(u32, u32)> {
        let mut found = Vec::new();
        for entry in std::fs::read_dir("/proc").into_iter().flatten().flatten() {
            let Some(pid) = entry
                .file_name()
                .to_str()
                .and_then(|n| n.parse::<u32>().ok())
            else {
                continue;
            };
            if let Some((_, fields)) = stat(&entry.path().join("stat")) {
                if let Some(parent) = fields.get(1).and_then(|f| f.parse::<u32>().ok()) {
                    found.push((pid, parent));
                }
            }
        }
        found
    }

    /// this process's threads: (id, name, processor seconds so far); a thread nobody named has
    /// the program's name, so none
    pub fn threads() -> Vec<(u64, String, f64)> {
        let own = stat(Path::new("/proc/self/stat")).map(|(name, _)| name);
        let mut found = Vec::new();
        for entry in std::fs::read_dir("/proc/self/task")
            .into_iter()
            .flatten()
            .flatten()
        {
            let Some(tid) = entry
                .file_name()
                .to_str()
                .and_then(|n| n.parse::<u64>().ok())
            else {
                continue;
            };
            let Some((name, fields)) = stat(&entry.path().join("stat")) else {
                continue;
            };
            let field = |n: usize| fields.get(n).and_then(|f| f.parse::<f64>().ok());
            let seconds = (field(11).unwrap_or(0.0) + field(12).unwrap_or(0.0)) / ticks();
            let name = if Some(&name) == own.as_ref() {
                String::new()
            } else {
                name
            };
            found.push((tid, name, seconds));
        }
        found
    }

    /// glibc's heaps: (in use, free)
    pub fn heap() -> Option<(u64, u64)> {
        #[repr(C)]
        struct MallInfo2 {
            arena: usize,
            ordblks: usize,
            smblks: usize,
            hblks: usize,
            hblkhd: usize,
            usmblks: usize,
            fsmblks: usize,
            uordblks: usize,
            fordblks: usize,
            keepcost: usize,
        }
        // SAFETY: dlsym asks for a symbol of glibc by its name; mallinfo2 (glibc 2.33 and
        // later) takes nothing and gives the struct above.
        unsafe {
            let symbol = libc::dlsym(libc::RTLD_DEFAULT, c"mallinfo2".as_ptr());
            if symbol.is_null() {
                return None;
            }
            let mallinfo2: extern "C" fn() -> MallInfo2 = std::mem::transmute(symbol);
            let info = mallinfo2();
            Some(((info.uordblks + info.hblkhd) as u64, info.fordblks as u64))
        }
    }

    pub fn commit() -> Option<u64> {
        None
    }

    pub fn program_pid(_name: &str) -> Option<u32> {
        None
    }
}

#[cfg(windows)]
mod os {
    use super::Facts;
    use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, FILETIME, HANDLE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, Thread32First, Thread32Next,
        PROCESSENTRY32W, TH32CS_SNAPPROCESS, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Memory::{GetProcessHeap, HeapSummary, HEAP_SUMMARY};
    use windows_sys::Win32::System::ProcessStatus::{
        GetProcessMemoryInfo, QueryWorkingSet, PROCESS_MEMORY_COUNTERS, PROCESS_MEMORY_COUNTERS_EX,
    };
    use windows_sys::Win32::System::Threading::{
        GetActiveProcessorCount, GetCurrentProcess, GetCurrentProcessId, GetProcessTimes,
        GetThreadDescription, GetThreadTimes, OpenProcess, OpenThread, PROCESS_QUERY_INFORMATION,
        PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ, THREAD_QUERY_LIMITED_INFORMATION,
    };

    const ALL_PROCESSOR_GROUPS: u16 = 0xffff;

    fn seconds(time: &FILETIME) -> f64 {
        ((u64::from(time.dwHighDateTime) << 32) | u64::from(time.dwLowDateTime)) as f64 / 1e7
    }

    struct Handle(HANDLE);

    impl Drop for Handle {
        fn drop(&mut self) {
            // SAFETY: a handle this program opened, closed once.
            unsafe { CloseHandle(self.0) };
        }
    }

    fn open(pid: u32, rights: u32) -> Option<Handle> {
        // SAFETY: OpenProcess opens a process by its id; null where it may not.
        let handle = unsafe { OpenProcess(rights, 0, pid) };
        (!handle.is_null()).then_some(Handle(handle))
    }

    fn snapshot(kind: u32) -> Option<Handle> {
        // SAFETY: a snapshot of the system's processes or threads.
        let handle = unsafe { CreateToolhelp32Snapshot(kind, 0) };
        (handle as isize != -1 && !handle.is_null()).then_some(Handle(handle))
    }

    fn processes() -> Vec<(u32, u32, String)> {
        let mut found = Vec::new();
        let Some(snap) = snapshot(TH32CS_SNAPPROCESS) else {
            return found;
        };
        // SAFETY: the entry's size is set as the calls want.
        unsafe {
            let mut entry: PROCESSENTRY32W = std::mem::zeroed();
            entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
            let mut more = Process32FirstW(snap.0, &mut entry) != 0;
            while more {
                let end = entry.szExeFile.iter().position(|c| *c == 0).unwrap_or(260);
                let name = String::from_utf16_lossy(&entry.szExeFile[..end]);
                found.push((entry.th32ProcessID, entry.th32ParentProcessID, name));
                more = Process32NextW(snap.0, &mut entry) != 0;
            }
        }
        found
    }

    pub fn cpu_count() -> usize {
        // SAFETY: a number of the system.
        (unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) }) as usize
    }

    pub fn facts(pid: u32) -> Option<Facts> {
        let (_, _, name) = processes().into_iter().find(|(p, _, _)| *p == pid)?;
        let process = open(pid, PROCESS_QUERY_LIMITED_INFORMATION)?;
        // SAFETY: the times and counters are written into structs of the sizes given.
        unsafe {
            let (mut born, mut exit, mut kernel, mut user) =
                std::mem::zeroed::<(FILETIME, FILETIME, FILETIME, FILETIME)>();
            if GetProcessTimes(process.0, &mut born, &mut exit, &mut kernel, &mut user) == 0 {
                return None;
            }
            let mut counters: PROCESS_MEMORY_COUNTERS = std::mem::zeroed();
            counters.cb = size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
            let rss = if GetProcessMemoryInfo(process.0, &mut counters, counters.cb) != 0 {
                counters.WorkingSetSize as u64
            } else {
                0
            };
            Some(Facts {
                name,
                cpu: seconds(&kernel) + seconds(&user),
                rss,
                // (since 1601: 11644473600 seconds before 1970)
                born: seconds(&born) - 11_644_473_600.0,
            })
        }
    }

    pub fn cmdline(_pid: u32) -> Vec<String> {
        Vec::new()
    }

    /// psutil's uss on Windows: the pages of the working set that are not shared
    pub fn private(pid: u32) -> Option<u64> {
        let process = open(pid, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)?;
        let page = 4096u64;
        let mut words: Vec<usize> = vec![0; 1 << 16];
        // SAFETY: the buffer is of the size given; grown while the working set does not fit.
        unsafe {
            loop {
                let bytes = (words.len() * size_of::<usize>()) as u32;
                if QueryWorkingSet(process.0, words.as_mut_ptr().cast(), bytes) != 0 {
                    break;
                }
                let wanted = words[0] + 64;
                if words.len() > wanted || wanted > 1 << 26 {
                    return None;
                }
                words.resize(wanted * 2, 0);
            }
        }
        let count = words[0].min(words.len() - 1);
        // (each entry's bit 8: the page is shared)
        let private = words[1..=count].iter().filter(|w| *w & 0x100 == 0).count();
        Some(private as u64 * page)
    }

    pub fn tree() -> Vec<(u32, u32)> {
        processes()
            .into_iter()
            .map(|(p, parent, _)| (p, parent))
            .collect()
    }

    pub fn threads() -> Vec<(u64, String, f64)> {
        let mut found = Vec::new();
        let Some(snap) = snapshot(TH32CS_SNAPTHREAD) else {
            return found;
        };
        // SAFETY: as above; each thread opened only to ask its name and times.
        unsafe {
            let me = GetCurrentProcessId();
            let mut entry: THREADENTRY32 = std::mem::zeroed();
            entry.dwSize = size_of::<THREADENTRY32>() as u32;
            let mut more = Thread32First(snap.0, &mut entry) != 0;
            while more {
                if entry.th32OwnerProcessID == me {
                    let tid = entry.th32ThreadID;
                    let thread = OpenThread(THREAD_QUERY_LIMITED_INFORMATION, 0, tid);
                    if !thread.is_null() {
                        let thread = Handle(thread);
                        let mut name = String::new();
                        let mut text: windows_sys::core::PWSTR = std::ptr::null_mut();
                        if GetThreadDescription(thread.0, &mut text) >= 0 && !text.is_null() {
                            let mut end = 0;
                            while *text.add(end) != 0 {
                                end += 1;
                            }
                            name = String::from_utf16_lossy(std::slice::from_raw_parts(text, end));
                            LocalFree(text.cast());
                        }
                        let (mut born, mut exit, mut kernel, mut user) =
                            std::mem::zeroed::<(FILETIME, FILETIME, FILETIME, FILETIME)>();
                        let spent = if GetThreadTimes(
                            thread.0,
                            &mut born,
                            &mut exit,
                            &mut kernel,
                            &mut user,
                        ) != 0
                        {
                            seconds(&kernel) + seconds(&user)
                        } else {
                            0.0
                        };
                        found.push((u64::from(tid), name, spent));
                    }
                }
                more = Thread32Next(snap.0, &mut entry) != 0;
            }
        }
        found
    }

    /// the process heap's (allocated, committed but free)
    pub fn heap() -> Option<(u64, u64)> {
        // SAFETY: the summary is of the size given.
        unsafe {
            let mut summary: HEAP_SUMMARY = std::mem::zeroed();
            summary.cb = size_of::<HEAP_SUMMARY>() as u32;
            if HeapSummary(GetProcessHeap(), 0, &mut summary) == 0 {
                return None;
            }
            let used = summary.cbAllocated as u64;
            Some((used, (summary.cbCommitted as u64).saturating_sub(used)))
        }
    }

    /// what this process has committed (the Task Manager's Commit size)
    pub fn commit() -> Option<u64> {
        // SAFETY: the counters are of the size given.
        unsafe {
            let mut counters: PROCESS_MEMORY_COUNTERS_EX = std::mem::zeroed();
            counters.cb = size_of::<PROCESS_MEMORY_COUNTERS_EX>() as u32;
            let done =
                GetProcessMemoryInfo(GetCurrentProcess(), (&raw mut counters).cast(), counters.cb);
            (done != 0).then_some(counters.PrivateUsage as u64)
        }
    }

    pub fn program_pid(name: &str) -> Option<u32> {
        processes()
            .into_iter()
            .find(|(_, _, exe)| exe.to_lowercase() == name)
            .map(|(pid, _, _)| pid)
    }

    pub fn program_name(pid: u32) -> String {
        processes()
            .into_iter()
            .find(|(p, _, _)| *p == pid)
            .map(|(_, _, name)| name)
            .unwrap_or_default()
    }
}

/// The name of the program a process runs ("" where it is gone or may not be looked at).
#[cfg(windows)]
pub fn program_name(pid: u32) -> String {
    os::program_name(pid)
}

#[cfg(not(any(target_os = "linux", windows)))]
mod os {
    use super::Facts;
    pub fn cpu_count() -> usize {
        std::thread::available_parallelism().map_or(1, |n| n.get())
    }
    pub fn facts(_pid: u32) -> Option<Facts> {
        None
    }
    pub fn cmdline(_pid: u32) -> Vec<String> {
        Vec::new()
    }
    pub fn private(_pid: u32) -> Option<u64> {
        None
    }
    pub fn tree() -> Vec<(u32, u32)> {
        Vec::new()
    }
    pub fn threads() -> Vec<(u64, String, f64)> {
        Vec::new()
    }
    pub fn heap() -> Option<(u64, u64)> {
        None
    }
    pub fn commit() -> Option<u64> {
        None
    }
    pub fn program_pid(_name: &str) -> Option<u32> {
        None
    }
}

/// Where the sensor service is: its process (none: none), how that was found ("service
/// manager" or "program name") and its state ("running", "not running", "not installed").
#[derive(Clone, Debug)]
struct ServiceFound {
    pid: Option<u32>,
    how: &'static str,
    state: &'static str,
}

/// find_service: the sensor service's process; none off Windows, where there is none.
fn find_service() -> Option<ServiceFound> {
    #[cfg(windows)]
    {
        use lp_sensors::service::SERVICE_NAME;
        match lp_sensors::winlink::service_status(SERVICE_NAME) {
            Ok(None) => {
                return Some(ServiceFound {
                    pid: None,
                    how: "",
                    state: "not installed",
                })
            }
            Ok(Some((_, pid))) if pid != 0 => {
                return Some(ServiceFound {
                    pid: Some(pid),
                    how: "service manager",
                    state: "running",
                })
            }
            Ok(Some(_)) => {
                return Some(ServiceFound {
                    pid: None,
                    how: "",
                    state: "not running",
                })
            }
            Err(_) => {} // it cannot be asked: the program's name it is
        }
        Some(match os::program_pid(SERVICE_EXE) {
            Some(pid) => ServiceFound {
                pid: Some(pid),
                how: "program name",
                state: "running",
            },
            None => ServiceFound {
                pid: None,
                how: "",
                state: "not running",
            },
        })
    }
    #[cfg(not(windows))]
    {
        let _ = (SERVICE_EXE, os::program_pid);
        None
    }
}

/// role_of: what a process under this one is, by its id, its program's name and its command
/// line: "engine", "ffmpeg", "presentmon", "window", "webview" or "other".
fn role_of(name: &str, cmdline: &[String], pid: u32, window: Option<u32>) -> &'static str {
    if Some(pid) == window {
        return "window";
    }
    let low = name.to_lowercase();
    if low.starts_with("libre-panel-engine") {
        return "engine";
    }
    if low.starts_with("ffmpeg") {
        return "ffmpeg";
    }
    if low.starts_with("presentmon") {
        return "presentmon";
    }
    if low.starts_with("msedgewebview2") || low.starts_with("webkit") {
        return "webview";
    }
    let libre = cmdline
        .iter()
        .take(3)
        .any(|part| part.to_lowercase().contains("libre"));
    if cmdline.iter().skip(1).any(|part| part == "window") && libre {
        return "window";
    }
    OTHER
}

/// label_of: a row's name in the text (English, for an issue).
fn label_of(role: &str, panel: Option<i64>, name: &str) -> String {
    match role {
        "main" => "main process".into(),
        "panel" => panel.map_or("panel".into(), |n| format!("panel {n}")),
        "pictures" => "the app's pictures".into(),
        "engine" => "engine".into(),
        "ffmpeg" => panel.map_or("ffmpeg".into(), |n| format!("ffmpeg (panel {n})")),
        "presentmon" => "PresentMon".into(),
        "window" => "window".into(),
        "webview" => if cfg!(windows) { "WebView2" } else { "WebKit" }.into(),
        "service" => "sensor service".into(),
        _ if !name.is_empty() => format!("other: {name}"),
        _ => OTHER.into(),
    }
}

fn order_of(role: &str) -> u8 {
    match role {
        "main" => 0,
        "panel" => 1,
        "engine" => 2,
        "ffmpeg" => 3,
        "presentmon" => 4,
        "window" => 5,
        "pictures" => 6,
        "webview" => 7,
        "service" => 8,
        _ => 9,
    }
}

/// round(x, 2) as Python's
fn round2(x: f64) -> f64 {
    format!("{x:.2}").parse().unwrap_or(x)
}

type Key = (u32, u64);

fn key_of(pid: u32, born: f64) -> Key {
    (pid, born.to_bits())
}

#[derive(Clone)]
struct Measured {
    key: Key,
    pid: u32,
    role: &'static str,
    name: String,
    cpu: f64,
    rss: u64,
    born: f64,
    private: Option<u64>,
}

struct Sample {
    at: f64,
    wall: f64,
    cpu: HashMap<Key, f64>,
    threads: HashMap<u64, f64>,
}

/// What a round of LibreHardwareMonitor's took, for the report.
fn round_report(done: Option<&lp_sensors::protocol::RoundTime>) -> Value {
    let Some(done) = done else {
        return Value::None;
    };
    let mut parts: Vec<&lp_sensors::protocol::PartTime> = done.parts.iter().collect();
    parts.sort_by(|a, b| b.seconds.total_cmp(&a.seconds));
    let mut told = Dict::default();
    told.insert("ms".into(), Value::Float(round2(done.seconds() * 1000.0)));
    told.insert("full".into(), Value::Bool(done.full));
    let parts = parts
        .into_iter()
        .map(|part| {
            let mut each = Dict::default();
            each.insert("kind".into(), Value::Str(part.kind.clone()));
            each.insert("name".into(), Value::Str(part.name.clone()));
            each.insert("ms".into(), Value::Float(round2(part.seconds * 1000.0)));
            Value::Dict(each)
        })
        .collect();
    told.insert("parts".into(), Value::List(parts));
    Value::Dict(told)
}

fn bytes_or_none(value: Option<u64>) -> Value {
    value.map_or(Value::None, |n| Value::int(n as i64))
}

/// sensors_report: what LibreHardwareMonitor's rounds take, from the status of the source that
/// reads it (none: it is not read).
fn sensors_report(status: Option<&LhmStatus>) -> Value {
    let Some(status) = status else {
        return Value::None;
    };
    let mut told = Dict::default();
    told.insert("mode".into(), Value::Str(status.mode.clone()));
    told.insert("version".into(), Value::Str(status.version.clone()));
    told.insert("error".into(), Value::Str(status.error.clone()));
    told.insert("reported".into(), Value::Bool(status.timing.is_some()));
    let timing = status.timing.as_ref();
    told.insert(
        "last".into(),
        round_report(timing.and_then(|t| t.last.as_ref())),
    );
    told.insert(
        "full".into(),
        round_report(timing.and_then(|t| t.full.as_ref())),
    );
    let memory = status.memory.as_ref().map_or(Value::None, |memory| {
        let mut said = Dict::default();
        said.insert("private".into(), bytes_or_none(memory.private));
        said.insert("working_set".into(), bytes_or_none(memory.working_set));
        said.insert("managed".into(), bytes_or_none(memory.managed));
        Value::Dict(said)
    });
    told.insert("memory".into(), memory);
    Value::Dict(told)
}

/// The ways to the rest of the app: where the window wrote itself down, and how
/// LibreHardwareMonitor is read.
pub struct Ways {
    pub window: Box<dyn Fn() -> Option<u32> + Send + Sync>,
    pub lhm: Box<dyn Fn() -> Option<LhmStatus> + Send + Sync>,
}

#[derive(Default)]
struct Inner {
    inside: Option<Value>,
    inside_at: Option<f64>,
    roles: HashMap<Key, (&'static str, String)>,
    before: Option<Sample>,
    service_pid: Option<u32>,
    service_found: Option<ServiceFound>,
    service_looked: Option<f64>,
    window_pid: Option<u32>,
    window_looked: Option<f64>,
    tree: Vec<u32>,
    tree_at: Option<f64>,
    private: HashMap<Key, (f64, Option<u64>)>,
    costs: std::collections::VecDeque<f64>,
    latest: Option<Dict>,
    latest_at: Option<f64>,
    asked: Option<f64>,
    running: bool,
}

/// LoadSampler: samples Libre Panel's processes and this process's threads in a thread of its
/// own, while somebody asks ([`Sampler::report`]).
pub struct Sampler {
    began: Instant,
    pub cpus: usize,
    ways: Ways,
    inner: Mutex<Inner>,
    fresh: Condvar,
}

impl Sampler {
    pub fn new(ways: Ways) -> Arc<Sampler> {
        Arc::new(Sampler {
            began: Instant::now(),
            cpus: os::cpu_count().max(1),
            ways,
            inner: Mutex::new(Inner::default()),
            fresh: Condvar::new(),
        })
    }

    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// report: the last sample's report (`ready` false before there is one), at once; the
    /// thread samples on for IDLE_S (and starts, if it had ended).
    pub fn report(self: &Arc<Self>) -> Value {
        let now = self.clock();
        let (latest, at) = {
            let mut inner = self.lock();
            inner.asked = Some(now);
            if !inner.running {
                inner.running = true;
                let me = Arc::clone(self);
                let started = std::thread::Builder::new()
                    .name("load-report".into())
                    .spawn(move || me.run());
                if started.is_err() {
                    inner.running = false;
                }
            }
            (inner.latest.clone(), inner.latest_at)
        };
        match (latest, at) {
            (Some(mut latest), Some(at)) if now - at <= IDLE_S => {
                latest.insert("age_s".into(), Value::Float(round2((now - at).max(0.0))));
                Value::Dict(latest)
            }
            _ => {
                let mut told = Dict::default();
                told.insert("ready".into(), Value::Bool(false));
                told.insert("cpus".into(), Value::int(self.cpus as i64));
                told.insert("window_s".into(), Value::Float(WINDOW_S));
                Value::Dict(told)
            }
        }
    }

    /// text: the report as text: the last one, or the first that comes within `wait`
    /// seconds; "" when none does.
    pub fn text(self: &Arc<Self>, wait: f64) -> String {
        let text_of = |report: &Value| match report {
            Value::Dict(told) if told.get("ready") == Some(&Value::Bool(true)) => told
                .get("text")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            _ => String::new(),
        };
        let report = self.report();
        if !text_of(&report).is_empty() || wait <= 0.0 {
            return text_of(&report);
        }
        let asked = self.clock();
        let inner = self.lock();
        let _ = self
            .fresh
            .wait_timeout_while(inner, Duration::from_secs_f64(wait), |inner| {
                !inner.latest_at.is_some_and(|at| at >= asked)
            });
        text_of(&self.report())
    }

    fn run(self: Arc<Self>) {
        loop {
            self.sample();
            std::thread::sleep(Duration::from_secs_f64(WINDOW_S));
            let mut inner = self.lock();
            if inner
                .asked
                .is_none_or(|asked| self.clock() - asked > IDLE_S)
            {
                inner.running = false; // nobody looks: it ends, the next ask starts it
                return;
            }
        }
    }

    /// sample: measure once; the report of the time since the sample before (none for the
    /// first, or the first after a pause).
    fn sample(&self) {
        let started = Instant::now();
        let (now, wall) = (self.clock(), wall());
        let mut measured = self.measure_all(now);
        self.private_of(&mut measured, now);
        let threads = os::threads();
        let cost = started.elapsed().as_secs_f64();
        let mut inner = self.lock();
        inner.costs.push_back(cost);
        if inner.costs.len() > 6 {
            inner.costs.pop_front();
        }
        drop(inner);
        self.inside_now(now);
        let sample = Sample {
            at: now,
            wall,
            cpu: measured.iter().map(|m| (m.key, m.cpu)).collect(),
            threads: threads.iter().map(|(tid, _, s)| (*tid, *s)).collect(),
        };
        let mut inner = self.lock();
        let before = inner.before.replace(sample);
        let Some(before) = before else {
            return;
        };
        if !(0.0 < now - before.at && now - before.at <= IDLE_S) {
            return;
        }
        let cost = lp_py::sum_floats(inner.costs.iter().copied()) / inner.costs.len() as f64;
        let after = inner.before.take();
        drop(inner);
        let Some(after) = after else {
            return;
        };
        let names: HashMap<u64, String> = threads
            .into_iter()
            .map(|(tid, name, _)| (tid, name))
            .collect();
        let report = self.make_report(&before, &after, &measured, cost, &names);
        let mut inner = self.lock();
        inner.before = Some(after);
        inner.latest = Some(report);
        inner.latest_at = Some(now);
        drop(inner);
        self.fresh.notify_all();
    }

    fn measure_all(&self, now: f64) -> Vec<Measured> {
        let window = self.window_now(now);
        let me = std::process::id();
        let mut out = Vec::new();
        if let Some(facts) = os::facts(me) {
            out.push(self.measured(me, &facts, "main", String::new()));
        }
        let mut gone = Vec::new();
        for child in self.children(now, window) {
            match os::facts(child) {
                Some(facts) => {
                    let (role, name) = self.role(child, &facts, window);
                    out.push(self.measured(child, &facts, role, name));
                }
                None => gone.push(child),
            }
        }
        if !gone.is_empty() {
            self.lock().tree.retain(|pid| !gone.contains(pid));
        }
        if let Some(pid) = self.service_now(now) {
            match os::facts(pid) {
                Some(facts) => out.push(self.measured(pid, &facts, "service", String::new())),
                None => {
                    let mut inner = self.lock();
                    inner.service_pid = None;
                    inner.service_looked = None;
                }
            }
        }
        out
    }

    fn measured(&self, pid: u32, facts: &Facts, role: &'static str, name: String) -> Measured {
        Measured {
            key: key_of(pid, facts.born),
            pid,
            role,
            name,
            cpu: facts.cpu,
            rss: facts.rss,
            born: facts.born,
            private: None,
        }
    }

    /// what this process holds: counted anew every INSIDE_S, in between the last count
    fn inside_now(&self, now: f64) {
        {
            let mut inner = self.lock();
            if inner
                .inside_at
                .is_some_and(|at| (0.0..INSIDE_S).contains(&(now - at)))
            {
                return;
            }
            inner.inside_at = Some(now);
        }
        let started = Instant::now();
        let mut held = Dict::default();
        held.insert(
            "heap".into(),
            os::heap().map_or(Value::None, |(in_use, free)| {
                let mut heap = Dict::default();
                heap.insert("in_use".into(), Value::int(in_use as i64));
                heap.insert("free".into(), Value::int(free as i64));
                Value::Dict(heap)
            }),
        );
        held.insert("threads".into(), Value::int(os::threads().len() as i64));
        if let Some(commit) = os::commit() {
            held.insert("commit".into(), Value::int(commit as i64));
        }
        let ms = (started.elapsed().as_secs_f64() * 10000.0).round() / 10.0;
        held.insert("ms".into(), Value::Float(ms));
        self.lock().inside = Some(Value::Dict(held));
    }

    /// the processes under this one, and the window's with its own where another start opened
    /// it: walked anew every TREE_S
    fn children(&self, now: f64, window: Option<u32>) -> Vec<u32> {
        {
            let inner = self.lock();
            if inner
                .tree_at
                .is_some_and(|at| (0.0..TREE_S).contains(&(now - at)))
            {
                return inner.tree.clone();
            }
        }
        let all = os::tree();
        let under = |root: u32| {
            let mut found = Vec::new();
            let mut todo = vec![root];
            while let Some(parent) = todo.pop() {
                for (pid, of) in &all {
                    if *of == parent && *pid != parent && !found.contains(pid) {
                        found.push(*pid);
                        todo.push(*pid);
                    }
                }
            }
            found
        };
        let me = std::process::id();
        let mut children = under(me);
        if let Some(window) = window.filter(|w| *w != me && !children.contains(w)) {
            if all.iter().any(|(pid, _)| *pid == window) {
                children.push(window);
                children.extend(under(window));
            }
        }
        let mut inner = self.lock();
        inner.tree_at = Some(now);
        inner.tree = children.clone();
        children
    }

    /// each process's private memory: measured anew every PRIVATE_S, in between the last one
    fn private_of(&self, measured: &mut [Measured], now: f64) {
        let kept = std::mem::take(&mut self.lock().private);
        let mut fresh = HashMap::new();
        for m in measured.iter_mut() {
            let known = match kept.get(&m.key) {
                Some(known) if (0.0..PRIVATE_S).contains(&(now - known.0)) => *known,
                _ => (now, os::private(m.pid)),
            };
            fresh.insert(m.key, known);
            m.private = known.1;
        }
        self.lock().private = fresh; // (those that ended are forgotten)
    }

    fn role(&self, pid: u32, facts: &Facts, window: Option<u32>) -> (&'static str, String) {
        let key = key_of(pid, facts.born);
        if let Some(known) = self.lock().roles.get(&key) {
            return known.clone();
        }
        let role = role_of(&facts.name, &os::cmdline(pid), pid, window);
        let name = if role == OTHER {
            facts.name.clone()
        } else {
            String::new()
        };
        let mut inner = self.lock();
        if inner.roles.len() > 512 {
            inner.roles.clear(); // ffmpeg's that came and went
        }
        inner.roles.insert(key, (role, name.clone()));
        (role, name)
    }

    fn window_now(&self, now: f64) -> Option<u32> {
        let look = {
            let inner = self.lock();
            inner
                .window_looked
                .is_none_or(|at| now - at >= WINDOW_LOOK_S)
        };
        if look {
            let pid = (self.ways.window)();
            let mut inner = self.lock();
            inner.window_looked = Some(now);
            inner.window_pid = pid;
        }
        self.lock().window_pid
    }

    /// the sensor service's process: kept once found, looked for again while it is not (at
    /// most every LOOK_S)
    fn service_now(&self, now: f64) -> Option<u32> {
        {
            let inner = self.lock();
            if inner.service_pid.is_some() {
                return inner.service_pid;
            }
            if inner.service_looked.is_some_and(|at| now - at < LOOK_S) {
                return None;
            }
        }
        let found = find_service();
        let mut inner = self.lock();
        inner.service_looked = Some(now);
        inner.service_pid = found.as_ref().and_then(|found| found.pid);
        inner.service_found = found;
        inner.service_pid
    }

    fn shares(&self, row: &mut Dict) {
        let cpu = match row.get("cpu_core") {
            Some(Value::Float(cpu)) => Some(*cpu),
            _ => None,
        };
        row.insert(
            "cpu_core".into(),
            cpu.map_or(Value::None, |cpu| Value::Float(round2(cpu))),
        );
        row.insert(
            "cpu_pc".into(),
            cpu.map_or(Value::None, |cpu| {
                Value::Float(round2(cpu / self.cpus as f64))
            }),
        );
    }

    fn make_report(
        &self,
        before: &Sample,
        now: &Sample,
        measured: &[Measured],
        cost: f64,
        names: &HashMap<u64, String>,
    ) -> Dict {
        let elapsed = now.at - before.at;
        let core = |seconds: f64| seconds.max(0.0) / elapsed * 100.0;
        let mut rows: indexmap::IndexMap<(&'static str, String), Dict> = indexmap::IndexMap::new();
        let mut main_cpu = None;
        for m in measured {
            let mut then = before.cpu.get(&m.key).copied();
            if then.is_none() && m.born >= before.wall - 0.5 {
                then = Some(0.0); // it started since: all its time is in the window
            }
            let share = then.map(|then| core(m.cpu - then));
            if m.role == "main" {
                main_cpu = share;
            }
            let row = rows.entry((m.role, m.name.clone())).or_insert_with(|| {
                let mut row = Dict::default();
                row.insert("role".into(), Value::from(m.role));
                row.insert("panel".into(), Value::None);
                row.insert("name".into(), Value::Str(m.name.clone()));
                row.insert("count".into(), Value::int(0));
                row.insert("cpu_core".into(), Value::None);
                row.insert("private".into(), Value::None);
                row.insert("rss".into(), Value::int(0));
                row
            });
            let count = int_in(row, "count");
            row.insert("count".into(), Value::int(count + 1));
            let rss = int_in(row, "rss");
            row.insert("rss".into(), Value::int(rss + m.rss as i64));
            if let Some(private) = m.private {
                let before = int_in(row, "private");
                row.insert("private".into(), Value::int(before + private as i64));
            }
            if let Some(share) = share {
                let before = match row.get("cpu_core") {
                    Some(Value::Float(cpu)) => *cpu,
                    _ => 0.0,
                };
                row.insert("cpu_core".into(), Value::Float(before + share));
            }
        }
        let mut processes: Vec<Dict> = rows.into_values().collect();
        processes
            .sort_by_key(|row| order_of(row.get("role").and_then(Value::as_str).unwrap_or("")));
        let sensors = sensors_report((self.ways.lhm)().as_ref());
        let said_private = match &sensors {
            Value::Dict(sensors) => match sensors.get("memory") {
                Some(Value::Dict(memory)) => match memory.get("private") {
                    Some(Value::Int(n)) if *n != 0.into() => Some(Value::Int(n.clone())),
                    _ => None,
                },
                _ => None,
            },
            _ => None,
        };
        let service = self.lock().service_found.clone();
        let (mut cpu_total, mut private_total, mut rss_total) = (0.0, 0i64, 0i64);
        for row in &mut processes {
            let role = row
                .get("role")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if role == "service" && row.get("private") == Some(&Value::None) {
                if let Some(said) = &said_private {
                    row.insert("private".into(), said.clone()); // (Windows does not let it be read)
                    row.insert("private_by".into(), Value::from("itself"));
                }
            }
            let name = row
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            row.insert("label".into(), Value::Str(label_of(&role, None, &name)));
            if role == "service" {
                if let Some(found) = &service {
                    row.insert("found_by".into(), Value::from(found.how));
                }
            }
            self.shares(row);
            if let Some(Value::Float(cpu)) = row.get("cpu_core") {
                cpu_total += cpu;
            }
            private_total += int_in(row, "private");
            rss_total += int_in(row, "rss");
        }
        let mut total = Dict::default();
        total.insert("cpu_core".into(), Value::Float(cpu_total));
        total.insert("private".into(), Value::int(private_total));
        total.insert("rss".into(), Value::int(rss_total));
        self.shares(&mut total);
        let mut report = Dict::default();
        report.insert("ready".into(), Value::Bool(true));
        report.insert("cpus".into(), Value::int(self.cpus as i64));
        report.insert("window_s".into(), Value::Float(round2(elapsed)));
        report.insert("sample_ms".into(), Value::Float(round2(cost * 1000.0)));
        report.insert("total".into(), Value::Dict(total));
        report.insert(
            "processes".into(),
            Value::List(processes.into_iter().map(Value::Dict).collect()),
        );
        report.insert(
            "threads".into(),
            self.threads_report(before, now, &core, main_cpu, names),
        );
        report.insert(
            "service".into(),
            service.map_or(Value::None, |found| {
                let mut said = Dict::default();
                said.insert("state".into(), Value::from(found.state));
                said.insert("found_by".into(), Value::from(found.how));
                Value::Dict(said)
            }),
        );
        report.insert("sensors".into(), sensors);
        report.insert(
            "inside".into(),
            self.lock().inside.clone().unwrap_or(Value::None),
        );
        let text = report_text(&report);
        report.insert("text".into(), Value::Str(text));
        report
    }

    fn threads_report(
        &self,
        before: &Sample,
        now: &Sample,
        core: &dyn Fn(f64) -> f64,
        main: Option<f64>,
        names: &HashMap<u64, String>,
    ) -> Value {
        let mut spent: indexmap::IndexMap<String, f64> = indexmap::IndexMap::new();
        let mut count: HashMap<String, i64> = HashMap::new();
        for (tid, seconds) in &now.threads {
            let name = match names.get(tid) {
                Some(name) if !name.is_empty() => name.clone(),
                _ => OTHER.to_string(), // (a thread nobody named)
            };
            let share = core(seconds - before.threads.get(tid).copied().unwrap_or(0.0));
            *spent.entry(name.clone()).or_insert(0.0) += share;
            *count.entry(name).or_insert(0) += 1;
        }
        if let Some(main) = main {
            // what the threads that ended meanwhile spent
            let ended = (main - lp_py::sum_floats(spent.values().copied())).max(0.0);
            *spent.entry(OTHER.to_string()).or_insert(0.0) += ended;
            count.entry(OTHER.to_string()).or_insert(0);
        }
        let mut out: Vec<(String, i64, f64)> = spent
            .into_iter()
            .map(|(name, share)| {
                let n = count.get(&name).copied().unwrap_or(0);
                (name, n, round2(share))
            })
            .collect();
        out.sort_by(|a, b| {
            (a.0 == OTHER)
                .cmp(&(b.0 == OTHER))
                .then(b.2.total_cmp(&a.2))
                .then(a.0.cmp(&b.0))
        });
        let cpus = self.cpus as f64;
        Value::List(
            out.into_iter()
                .map(|(name, n, share)| {
                    let mut each = Dict::default();
                    each.insert("name".into(), Value::Str(name));
                    each.insert("count".into(), Value::int(n));
                    each.insert("cpu_core".into(), Value::Float(share));
                    each.insert("cpu_pc".into(), Value::Float(round2(share / cpus)));
                    Value::Dict(each)
                })
                .collect(),
        )
    }
}

fn int_in(row: &Dict, key: &str) -> i64 {
    match row.get(key) {
        Some(Value::Int(n)) => i64::try_from(n).unwrap_or(0),
        _ => 0,
    }
}

fn float_in(row: &Dict, key: &str) -> Option<f64> {
    match row.get(key) {
        Some(Value::Float(f)) => Some(*f),
        Some(Value::Int(n)) => i64::try_from(n).ok().map(|n| n as f64),
        _ => None,
    }
}

// -- the text (for a report, and "Copy as text") --------------------------------------------

fn pct(value: Option<f64>) -> String {
    value.map_or("–".into(), |v| format!("{v:.1} %"))
}

fn mb(value: Option<i64>) -> String {
    value.map_or("–".into(), |v| {
        format!("{:.0} MB", v as f64 / 1024f64.powi(2))
    })
}

fn bytes_in(row: &Dict, key: &str) -> Option<i64> {
    match row.get(key) {
        Some(Value::Int(n)) => i64::try_from(n).ok(),
        _ => None,
    }
}

fn parts_text(done: &Dict) -> String {
    const MOST: usize = 5;
    let parts = done.get("parts").and_then(Value::as_list).unwrap_or(&[]);
    let said: Vec<String> = parts
        .iter()
        .take(MOST)
        .filter_map(Value::as_dict)
        .map(|p| {
            let kind = p.get("kind").and_then(Value::as_str).unwrap_or("");
            format!("{kind} {:.1}", float_in(p, "ms").unwrap_or(0.0))
        })
        .collect();
    let more = if parts.len() > MOST {
        format!(", {} more", parts.len() - MOST)
    } else {
        String::new()
    };
    let ms = float_in(done, "ms").unwrap_or(0.0);
    if said.is_empty() {
        format!("{ms:.1} ms (nothing read)")
    } else {
        format!("{ms:.1} ms ({}{more})", said.join(", "))
    }
}

fn sensors_text(sensors: &Value) -> Vec<String> {
    let Value::Dict(sensors) = sensors else {
        return Vec::new();
    };
    let text = |key: &str| {
        sensors
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    };
    let mode = text("mode");
    if mode == "webserver" {
        return vec![
            "LibreHardwareMonitor: read from its web server (its own program, not counted)".into(),
        ];
    }
    let mut place = match mode.as_str() {
        "service" => "the sensor service".to_string(),
        "inprocess" => "inside Libre Panel".to_string(),
        other => other.to_string(),
    };
    if mode == "service" && !text("version").is_empty() {
        place += &format!(" {}", text("version"));
    }
    let head = format!("LibreHardwareMonitor rounds, {place}:");
    if !text("error").is_empty() {
        return vec![format!("{head} {}", text("error"))];
    }
    if sensors.get("reported") != Some(&Value::Bool(true)) {
        return vec![format!("{head} not reported by this service version")];
    }
    let mut lines = vec![head.clone()];
    if let Some(Value::Dict(memory)) = sensors.get("memory") {
        lines.push(format!(
            "  the service holds: private {}, working set {}, .NET's heap {} (as it says)",
            mb(bytes_in(memory, "private")),
            mb(bytes_in(memory, "working_set")),
            mb(bytes_in(memory, "managed")),
        ));
    }
    let last = sensors.get("last").and_then(Value::as_dict);
    if let Some(last) = last {
        lines.push(format!("  last round: {}", parts_text(last)));
    }
    let last_full = last
        .and_then(|last| last.get("full"))
        .is_some_and(Value::truthy);
    if let Some(full) = sensors.get("full").and_then(Value::as_dict) {
        if !last_full {
            lines.push(format!("  all hardware: {}", parts_text(full)));
        }
    }
    if lines.len() > 1 {
        lines
    } else {
        vec![format!("{head} none yet")]
    }
}

/// What the main process holds, in a line (none: it was not counted).
fn inside_text(held: &Value) -> Vec<String> {
    let Value::Dict(held) = held else {
        return Vec::new();
    };
    let mut said = Vec::new();
    if let Some(Value::Dict(heap)) = held.get("heap") {
        said.push(format!(
            "C heap {} in use, {} free",
            mb(bytes_in(heap, "in_use")),
            mb(bytes_in(heap, "free"))
        ));
    }
    if let Some(commit) = bytes_in(held, "commit") {
        said.push(format!("committed {}", mb(Some(commit))));
    }
    said.push(format!("threads {}", int_in(held, "threads")));
    vec![format!(
        "the main process holds: {}; counted in {:.0} ms",
        said.join(", "),
        float_in(held, "ms").unwrap_or(0.0)
    )]
}

const FOUND: [(&str, &str); 2] = [
    (
        "service manager",
        "the process the Service Control Manager names",
    ),
    ("program name", "found by its program's name"),
];

/// report_text: the report in a few lines of English, for an issue: no names, no folders, no
/// ids.
fn report_text(report: &Dict) -> String {
    let cpus = int_in(report, "cpus");
    let window = float_in(report, "window_s").unwrap_or(0.0);
    let mut lines = vec![
        format!(
            "Libre Panel load over {window:.1} s, {cpus} logical processors (PC: of the whole PC, \
             as the Task Manager counts; core: of one core; private: memory as the Task \
             Manager's Memory column shows it; working set: with what is shared)"
        ),
        format!(
            "{:<22}{:>8}{:>9}{:>10}{:>13}",
            "", "PC", "core", "private", "working set"
        ),
    ];
    let row = |label: &str, data: &Dict| {
        format!(
            "{label:<22}{:>8}{:>9}{:>10}{:>13}",
            pct(float_in(data, "cpu_pc")),
            pct(float_in(data, "cpu_core")),
            mb(bytes_in(data, "private")),
            mb(bytes_in(data, "rss")),
        )
    };
    if let Some(Value::Dict(total)) = report.get("total") {
        lines.push(row("Libre Panel, total", total));
    }
    let processes: Vec<&Dict> = report
        .get("processes")
        .and_then(Value::as_list)
        .unwrap_or(&[])
        .iter()
        .filter_map(Value::as_dict)
        .collect();
    for process in &processes {
        let mut label = process
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let count = int_in(process, "count");
        if count > 1 {
            label += &format!(" ×{count}");
        }
        let short: String = label.chars().take(21).collect();
        lines.push(row(&short, process));
    }
    if let Some(Value::Dict(service)) = report.get("service") {
        let role = |p: &&Dict| p.get("role").and_then(Value::as_str) == Some("service");
        if processes.iter().any(role) {
            let by = service
                .get("found_by")
                .and_then(Value::as_str)
                .unwrap_or("");
            let mut how = FOUND
                .iter()
                .find(|(key, _)| *key == by)
                .map_or(by.to_string(), |(_, said)| said.to_string());
            let itself = processes
                .iter()
                .any(|p| p.get("private_by").and_then(Value::as_str) == Some("itself"));
            if itself {
                how += "; its private memory as it says itself";
            }
            lines.push(format!("(the sensor service: {how})"));
        } else {
            let state = service.get("state").and_then(Value::as_str).unwrap_or("");
            lines.push(format!("sensor service: {state}"));
        }
    }
    let threads: Vec<&Dict> = report
        .get("threads")
        .and_then(Value::as_list)
        .unwrap_or(&[])
        .iter()
        .filter_map(Value::as_dict)
        .collect();
    if !threads.is_empty() {
        let name = |t: &Dict| {
            t.get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        };
        let named: Vec<&&Dict> = threads
            .iter()
            .filter(|t| name(t) != OTHER)
            .take(TEXT_THREADS)
            .collect();
        let all = lp_py::sum_floats(
            threads
                .iter()
                .map(|t| float_in(t, "cpu_core").unwrap_or(0.0)),
        );
        let some = lp_py::sum_floats(named.iter().map(|t| float_in(t, "cpu_core").unwrap_or(0.0)));
        let mut said: Vec<String> = named
            .iter()
            .map(|t| format!("{} {:.1}", name(t), float_in(t, "cpu_core").unwrap_or(0.0)))
            .collect();
        said.push(format!("{OTHER} {:.1}", (all - some).max(0.0)));
        lines.push(format!(
            "threads of the main process, % of one core: {}",
            said.join(", ")
        ));
    }
    lines.extend(inside_text(report.get("inside").unwrap_or(&Value::None)));
    lines.extend(sensors_text(report.get("sensors").unwrap_or(&Value::None)));
    lines.push(format!(
        "this measurement took {:.1} ms",
        float_in(report, "sample_ms").unwrap_or(0.0)
    ));
    lines.join("\n") + "\n"
}

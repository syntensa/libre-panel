//! CPU, core and GPU load ten times a second, with no LibreHardwareMonitor and no admin rights
//! (sensors/load.py).
//!
//! Other sources give the load once a second or only when the panel asks, so bars and graphs
//! run behind what the computer does. This one measures in a background thread ten times a
//! second and is read on every frame. It stands before the other sources in `[sensors]
//! providers`: the first source with a value for a key wins.
//!
//! - `cpu.load`: on Windows `GetSystemTimes` (idle time against total time between two
//!   samples), on Linux the kernel's times of the processor as psutil's `cpu_percent` counts
//!   them.
//! - `cpu.core.N.load`: every core's load over the last 0.3 s (one sample alone flickers).
//! - `cpu.pcore.N.load`: every physical core's, the mean of its threads (topology.rs).
//! - `gpu.load` (Windows, any vendor): the performance counters that Task Manager shows, read
//!   through PDH. There is one 3D engine counter per process and engine; they are summed per
//!   engine, and the card's load is its busiest engine. The card is the one with the most
//!   dedicated memory in use, chosen again every minute. (Linux's, from the kernel and NVML,
//!   comes with the source `linux`.)
//!
//! The thread runs only while a theme on show reads one of the keys, and ends when the
//! source closes. Where a key cannot be measured it is simply missing and the next source
//! supplies it; the log says so once, and the thread tries again every minute.

use std::collections::VecDeque;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;
use crate::service::{wanted, Wanted};
use crate::topology;

/// ten samples a second
pub const SAMPLE_SECONDS: f64 = 0.1;
/// the graphics card is chosen again every minute
pub const ADAPTER_SECONDS: f64 = 60.0;
/// a reading older than this is dropped, not shown frozen
pub const STALE_SECONDS: f64 = 2.0;
/// nobody looked for this long: the thread ends, and starts on demand
pub const IDLE_SECONDS: f64 = 60.0;
/// a sampler that could not be made is tried again after this long
pub const RETRY_SECONDS: f64 = 60.0;
/// a core's load is its load over this long
pub const CORE_SECONDS: f64 = 0.3;
/// every core's load: cpu.core.1.load, cpu.core.2.load ...
pub const CORES: &str = "cpu.core.";
/// every physical core's (its threads'), measured with them
pub const PCORES: &str = "cpu.pcore.";
const KEYS: [&str; 3] = ["cpu.load", "gpu.load", CORES];

fn label(key: &str) -> &'static str {
    if key == "gpu.load" {
        "GPU load"
    } else {
        "CPU load"
    }
}

fn no_sampler(key: &str) -> &'static str {
    match key {
        "cpu.load" => "no CPU load from the system",
        "gpu.load" => "no GPU load from the performance counters",
        _ => "no load per core from the system",
    }
}

// -- what the numbers mean ----------------------------------------------------------------------

/// cpu_load: the load in percent between two `GetSystemTimes` samples (idle, kernel, user).
pub fn cpu_load(before: Option<(u64, u64, u64)>, after: Option<(u64, u64, u64)>) -> Option<f64> {
    let (before, after) = (before?, after?);
    let idle = after.0 as f64 - before.0 as f64;
    // (the kernel time includes the idle time)
    let total = (after.1 as i128 - before.1 as i128) + (after.2 as i128 - before.2 as i128);
    if total <= 0 {
        return None;
    }
    Some((100.0 * (1.0 - idle / total as f64)).clamp(0.0, 100.0))
}

/// `luid_0x00000000_0x0000D2E4`: what names the card in a PDH instance name.
fn luid(name: &str) -> Option<&str> {
    let start = name.find("luid_")?;
    name[start..].split("_phys").next()
}

/// pick_adapter: the card with the most dedicated memory in use, from `Dedicated Usage`
/// instances.
pub fn pick_adapter(memory: &[(String, f64)]) -> Option<String> {
    let mut used: IndexMap<&str, f64> = IndexMap::new();
    for (name, value) in memory {
        if let Some(luid) = luid(name).filter(|luid| !luid.is_empty()) {
            *used.entry(luid).or_insert(0.0) += value;
        }
    }
    let mut best: Option<(&str, f64)> = None;
    for (luid, value) in used {
        // (max() keeps the first of equals; a value that is no number never wins over one)
        if best.is_none_or(|(_, most)| value > most) {
            best = Some((luid, value));
        }
    }
    best.map(|(luid, _)| luid.to_string())
}

/// adapter_load: a card's load: its busiest engine, each engine summed over the processes
/// using it.
pub fn adapter_load(adapter: &str, engines: &[(String, f64)]) -> f64 {
    let mut per_engine: IndexMap<&str, f64> = IndexMap::new();
    for (name, value) in engines {
        if luid(name) == Some(adapter) {
            // (without the process: ..._phys_0_eng_0_...)
            let engine = &name[name.find("luid_").unwrap_or(0)..];
            *per_engine.entry(engine).or_insert(0.0) += value;
        }
    }
    let mut busiest: Option<f64> = None;
    for value in per_engine.into_values() {
        if busiest.is_none_or(|most| value > most) {
            busiest = Some(value);
        }
    }
    0.0f64.max(100.0f64.min(busiest.unwrap_or(0.0)))
}

/// Every core's load over the last `CORE_SECONDS`, from its (busy, total) times at each
/// sample.
#[derive(Default)]
pub struct Cores {
    past: VecDeque<(f64, Vec<(f64, f64)>)>,
}

impl Cores {
    /// The first sample: nothing to compare with yet.
    pub fn new(now: f64, sample: Vec<(f64, f64)>) -> Cores {
        Cores {
            past: VecDeque::from([(now, sample)]),
        }
    }

    /// The loads at `now`, of the cores' times `sample`; none when a core came or went.
    pub fn read(&mut self, now: f64, sample: Vec<(f64, f64)>) -> Option<Vec<f64>> {
        self.past.push_back((now, sample));
        // (the oldest sample that is at least CORE_SECONDS old stays)
        while self.past.len() > 2 && now - self.past[1].0 >= CORE_SECONDS {
            self.past.pop_front();
        }
        let (before, sample) = (&self.past[0].1, &self.past[self.past.len() - 1].1);
        if before.len() != sample.len() {
            // a core more or less: start again
            let last = self.past.pop_back()?;
            self.past.clear();
            self.past.push_back(last);
            return None;
        }
        Some(
            before
                .iter()
                .zip(sample)
                .map(|((busy0, total0), (busy1, total1))| {
                    let spent = total1 - total0;
                    if spent > 0.0 {
                        100.0 * (busy1 - busy0) / spent
                    } else {
                        0.0
                    }
                })
                .collect(),
        )
    }
}

/// A core's busy and total time from the kernel's times of it (a line of /proc/stat after
/// its name: user nice system idle iowait irq softirq steal guest guest_nice), as psutil's
/// `cpu_percent` counts them.
pub fn busy_of(times: &[f64]) -> (f64, f64) {
    let at = |n: usize| times.get(n).copied().unwrap_or(0.0);
    let total = lp_py::sum_floats(times.iter().copied()) - at(8) - at(9);
    (total - at(3) - at(4), total)
}

// -- the samplers -------------------------------------------------------------------------------

/// What one sample gave.
enum Sample {
    One(f64),
    Each(Vec<f64>),
}

trait Sampler: Send {
    fn read(&mut self) -> Option<Sample>;
}

#[cfg(target_os = "linux")]
mod system {
    use super::busy_of;

    /// The times of the processor as a whole and of each thread, from /proc/stat.
    fn stat() -> Option<(Vec<f64>, Vec<Vec<f64>>)> {
        let text = std::fs::read_to_string("/proc/stat").ok()?;
        let mut whole = None;
        let mut each = Vec::new();
        for line in text.lines() {
            let mut parts = line.split_ascii_whitespace();
            let Some(name) = parts.next().filter(|name| name.starts_with("cpu")) else {
                continue;
            };
            let times: Vec<f64> = parts.filter_map(|part| part.parse().ok()).collect();
            if name == "cpu" {
                whole = Some(times);
            } else {
                each.push(times);
            }
        }
        Some((whole?, each))
    }

    /// The processor's (busy, total) time.
    pub fn whole() -> Option<(f64, f64)> {
        stat().map(|(whole, _)| busy_of(&whole))
    }

    /// Each thread's (busy, total) time.
    pub fn cores() -> Option<Vec<(f64, f64)>> {
        let (_, each) = stat()?;
        (!each.is_empty()).then(|| each.iter().map(|times| busy_of(times)).collect())
    }
}

#[cfg(windows)]
mod system {
    use windows_sys::Wdk::System::SystemInformation::{
        NtQuerySystemInformation, SystemProcessorPerformanceInformation,
    };
    use windows_sys::Win32::Foundation::FILETIME;
    use windows_sys::Win32::System::Threading::GetSystemTimes;
    use windows_sys::Win32::System::WindowsProgramming::SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION;

    /// (idle, kernel, user) of the processor as a whole, in 100 ns.
    pub fn times() -> Option<(u64, u64, u64)> {
        let zero = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        let (mut idle, mut kernel, mut user) = (zero, zero, zero);
        // SAFETY: three FILETIMEs of this frame are written.
        if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
            return None;
        }
        let whole = |t: FILETIME| (u64::from(t.dwHighDateTime) << 32) | u64::from(t.dwLowDateTime);
        Some((whole(idle), whole(kernel), whole(user)))
    }

    /// Each thread's (busy, total) time, as psutil's times of it count: user, system (the
    /// kernel's without the idle), idle, interrupt and dpc.
    pub fn cores() -> Option<Vec<(f64, f64)>> {
        let count = std::thread::available_parallelism().map_or(64, |n| n.get().max(64));
        // SAFETY: the buffer holds `count` structures of plain numbers, of the size given;
        // only the part the system says it wrote is read.
        unsafe {
            let mut buffer: Vec<SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION> =
                vec![std::mem::zeroed(); count];
            let size = size_of_val(buffer.as_slice()) as u32;
            let mut written = 0u32;
            let status = NtQuerySystemInformation(
                SystemProcessorPerformanceInformation,
                buffer.as_mut_ptr().cast(),
                size,
                &mut written,
            );
            if status < 0 {
                return None;
            }
            let each = size_of::<SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION>();
            buffer.truncate(written as usize / each);
            if buffer.is_empty() {
                return None;
            }
            Some(
                buffer
                    .iter()
                    .map(|one| {
                        let (idle, kernel, user) = (
                            one.IdleTime as f64,
                            one.KernelTime as f64,
                            one.UserTime as f64,
                        );
                        let (dpc, interrupt) = (one.Reserved1[0] as f64, one.Reserved1[1] as f64);
                        let total = user + (kernel - idle) + idle + interrupt + dpc;
                        (total - idle, total)
                    })
                    .collect(),
            )
        }
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
mod system {
    pub fn whole() -> Option<(f64, f64)> {
        None
    }

    pub fn cores() -> Option<Vec<(f64, f64)>> {
        None
    }
}

/// Each thread's (busy, total) time now, as psutil's times of it count; none where the
/// system does not tell.
pub fn core_times() -> Option<Vec<(f64, f64)>> {
    system::cores()
}

/// The processor's load between two calls.
struct CpuSampler {
    #[cfg(windows)]
    last: Option<(u64, u64, u64)>,
    #[cfg(not(windows))]
    last: (f64, f64),
}

impl CpuSampler {
    fn new() -> Result<CpuSampler, String> {
        #[cfg(windows)]
        {
            let last = system::times();
            if last.is_none() {
                return Err("GetSystemTimes failed".into());
            }
            Ok(CpuSampler { last })
        }
        #[cfg(not(windows))]
        {
            let last = system::whole().ok_or("the processor's times cannot be read")?;
            Ok(CpuSampler { last })
        }
    }
}

impl Sampler for CpuSampler {
    #[cfg(windows)]
    fn read(&mut self) -> Option<Sample> {
        let now = system::times();
        let last = std::mem::replace(&mut self.last, now);
        cpu_load(last, now).map(Sample::One)
    }

    #[cfg(not(windows))]
    fn read(&mut self) -> Option<Sample> {
        // (psutil's cpu_percent: the share of the time that passed, to a tenth)
        let now = system::whole()?;
        let (busy0, total0) = std::mem::replace(&mut self.last, now);
        let spent = now.1 - total0;
        let load = if spent > 0.0 {
            lp_py::round_digits((now.0 - busy0) / spent * 100.0, 1)
        } else {
            0.0
        };
        Some(Sample::One(load))
    }
}

struct CoreSampler {
    cores: Cores,
    began: Instant,
}

impl CoreSampler {
    fn new() -> Result<CoreSampler, String> {
        let sample = system::cores().ok_or("the cores' times cannot be read")?;
        Ok(CoreSampler {
            cores: Cores::new(0.0, sample),
            began: Instant::now(),
        })
    }
}

impl Sampler for CoreSampler {
    fn read(&mut self) -> Option<Sample> {
        let sample = system::cores()?;
        self.cores
            .read(self.began.elapsed().as_secs_f64(), sample)
            .map(Sample::Each)
    }
}

#[cfg(windows)]
mod pdh {
    use std::time::Instant;

    use windows_sys::Win32::System::Performance::{
        PdhAddEnglishCounterW, PdhCloseQuery, PdhCollectQueryData, PdhGetFormattedCounterArrayW,
        PdhOpenQueryW, PDH_FMT_COUNTERVALUE_ITEM_W, PDH_FMT_DOUBLE, PDH_HCOUNTER, PDH_HQUERY,
        PDH_MORE_DATA,
    };

    use super::{adapter_load, pick_adapter, Sample, Sampler, ADAPTER_SECONDS};

    const ENGINE_COUNTER: &str = r"\GPU Engine(*engtype_3D)\Utilization Percentage";
    const MEMORY_COUNTER: &str = r"\GPU Adapter Memory(*)\Dedicated Usage";

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// The load of the graphics card in use, from the counters Task Manager shows.
    pub struct Gpu {
        query: PDH_HQUERY,
        engines: PDH_HCOUNTER,
        memory: PDH_HCOUNTER,
        adapter: Option<String>,
        chosen_at: Option<Instant>,
    }

    // SAFETY: the query and its counters are used by the one thread that owns the sampler.
    unsafe impl Send for Gpu {}

    impl Gpu {
        /// An error when the counters do not exist (older Windows, some virtual machines).
        pub fn new() -> Result<Gpu, String> {
            // SAFETY: the handles are written by PDH and closed with the query.
            unsafe {
                let mut query: PDH_HQUERY = std::ptr::null_mut();
                let status = PdhOpenQueryW(std::ptr::null(), 0, &mut query);
                if status != 0 {
                    return Err(format!("PdhOpenQuery failed ({status:#x})"));
                }
                let mut counters = [std::ptr::null_mut(); 2];
                for (n, path) in [ENGINE_COUNTER, MEMORY_COUNTER].into_iter().enumerate() {
                    let status =
                        PdhAddEnglishCounterW(query, wide(path).as_ptr(), 0, &mut counters[n]);
                    if status != 0 {
                        PdhCloseQuery(query);
                        return Err(format!("PdhAddEnglishCounter {path} failed ({status:#x})"));
                    }
                }
                PdhCollectQueryData(query); // a load needs two samples
                Ok(Gpu {
                    query,
                    engines: counters[0],
                    memory: counters[1],
                    adapter: None,
                    chosen_at: None,
                })
            }
        }

        /// Every instance of a wildcard counter with its value; none if PDH has none now.
        fn instances(&self, counter: PDH_HCOUNTER) -> Option<Vec<(String, f64)>> {
            // SAFETY: the buffer is as large as PDH asked for (in u64s, so that its items are
            // aligned); `count` items of it are read, each name a NUL-ended text inside it.
            unsafe {
                let (mut size, mut count) = (0u32, 0u32);
                let status = PdhGetFormattedCounterArrayW(
                    counter,
                    PDH_FMT_DOUBLE,
                    &mut size,
                    &mut count,
                    std::ptr::null_mut(),
                );
                if status != PDH_MORE_DATA || size == 0 {
                    return None;
                }
                let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
                let items = buffer.as_mut_ptr().cast::<PDH_FMT_COUNTERVALUE_ITEM_W>();
                let status = PdhGetFormattedCounterArrayW(
                    counter,
                    PDH_FMT_DOUBLE,
                    &mut size,
                    &mut count,
                    items,
                );
                if status != 0 {
                    return None; // the processes changed between the two calls: the next sample
                }
                let mut found = Vec::with_capacity(count as usize);
                for n in 0..count as usize {
                    let item = &*items.add(n);
                    // (status 0 and 1 are valid values; a process that is just ending reports
                    // another)
                    if item.FmtValue.CStatus > 1 {
                        continue;
                    }
                    let mut name = String::new();
                    if !item.szName.is_null() {
                        let mut length = 0;
                        while *item.szName.add(length) != 0 {
                            length += 1;
                        }
                        name = String::from_utf16_lossy(std::slice::from_raw_parts(
                            item.szName,
                            length,
                        ));
                    }
                    found.push((name, item.FmtValue.Anonymous.doubleValue));
                }
                Some(found)
            }
        }
    }

    impl Sampler for Gpu {
        fn read(&mut self) -> Option<Sample> {
            // SAFETY: the query is open until this sampler is dropped.
            if unsafe { PdhCollectQueryData(self.query) } != 0 {
                return None;
            }
            let again = self
                .chosen_at
                .is_none_or(|at| at.elapsed().as_secs_f64() > ADAPTER_SECONDS);
            if self.adapter.is_none() || again {
                let memory = self.instances(self.memory).unwrap_or_default();
                if let Some(adapter) = pick_adapter(&memory) {
                    self.adapter = Some(adapter);
                    self.chosen_at = Some(Instant::now());
                }
            }
            let adapter = self.adapter.as_deref()?;
            let engines = self.instances(self.engines)?;
            Some(Sample::One(adapter_load(adapter, &engines)))
        }
    }

    impl Drop for Gpu {
        fn drop(&mut self) {
            // SAFETY: the query is closed once, here.
            unsafe {
                PdhCloseQuery(self.query);
            }
        }
    }
}

/// A new sampler for `key`: none where this machine cannot have one, an error where it should
/// and does not.
fn open(key: &str) -> Result<Option<Box<dyn Sampler>>, String> {
    Ok(Some(match key {
        "cpu.load" => Box::new(CpuSampler::new()?),
        CORES => Box::new(CoreSampler::new()?),
        _ => {
            #[cfg(windows)]
            {
                Box::new(pdh::Gpu::new()?)
            }
            #[cfg(not(windows))]
            {
                return Ok(None);
            }
        }
    }))
}

fn measure(sampler: &mut dyn Sampler) -> Option<Sample> {
    let clamp = |v: f64| 0.0f64.max(100.0f64.min(v));
    Some(match sampler.read()? {
        Sample::One(value) => Sample::One(clamp(value)),
        Sample::Each(values) => Sample::Each(values.into_iter().map(clamp).collect()),
    })
}

/// The key a reading is measured under (every core's under `CORES`).
fn family(key: &str) -> &str {
    if key.starts_with(CORES) || key.starts_with(PCORES) {
        CORES
    } else {
        key
    }
}

// -- the source ---------------------------------------------------------------------------------

struct Shared {
    /// key -> (when measured, reading)
    values: Mutex<IndexMap<String, (Instant, Reading)>>,
    /// (stop, the thread runs)
    flags: Mutex<(bool, bool)>,
    changed: Condvar,
    notes: Mutex<Vec<String>>,
    wanted: Wanted,
}

impl Shared {
    /// Whether a theme on show reads `key`'s readings (the cores' with their threads').
    fn wants(&self, key: &str) -> bool {
        let keys = self.wanted.keys();
        wanted(&keys, key) || (key == CORES && wanted(&keys, PCORES))
    }

    fn note(&self, line: String) {
        if let Ok(mut notes) = self.notes.lock() {
            notes.push(line);
        }
    }
}

fn reading(value: f64, label: String) -> Reading {
    Reading {
        unit: "%".into(),
        label,
        ..Reading::of(Value::Float(value))
    }
}

fn store(
    shared: &Shared,
    key: &str,
    sample: Sample,
    at: Instant,
    cores: &mut Option<(usize, Option<Vec<Vec<usize>>>)>,
) {
    let Ok(mut values) = shared.values.lock() else {
        return;
    };
    match sample {
        Sample::One(value) => {
            values.insert(key.to_string(), (at, reading(value, label(key).into())));
        }
        Sample::Each(each) => {
            // every core's, named as the computer's own readings name them
            for (n, load) in each.iter().enumerate() {
                let n = n + 1;
                values.insert(
                    format!("{CORES}{n}.load"),
                    (at, reading(*load, format!("Core {n}"))),
                );
            }
            // the physical cores of the threads measured (asked once)
            if cores
                .as_ref()
                .is_none_or(|(threads, _)| *threads != each.len())
            {
                *cores = Some((each.len(), topology::physical_cores(each.len())));
            }
            let Some((_, Some(physical))) = cores else {
                return;
            };
            for (n, threads) in physical.iter().enumerate() {
                let n = n + 1;
                let mean =
                    lp_py::sum_floats(threads.iter().map(|t| each[*t])) / threads.len() as f64;
                values.insert(
                    format!("{PCORES}{n}.load"),
                    (at, reading(mean, format!("Core {n}"))),
                );
            }
        }
    }
}

fn run(shared: &Shared) {
    let mut samplers: IndexMap<&str, Box<dyn Sampler>> = IndexMap::new();
    // keys without a sampler, and when to try again
    let mut retry_at: IndexMap<&str, Instant> = IndexMap::new();
    let mut said: Vec<&str> = Vec::new();
    let mut cores = None;
    let mut looked = Instant::now();
    loop {
        if shared.flags.lock().map(|flags| flags.0).unwrap_or(true) {
            break;
        }
        let now = Instant::now();
        let shown: Vec<&str> = KEYS.into_iter().filter(|key| shared.wants(key)).collect();
        if !shown.is_empty() {
            looked = now;
        } else if now.duration_since(looked).as_secs_f64() > IDLE_SECONDS {
            break;
        }
        for key in &shown {
            if retry_at.get(key).is_some_and(|at| now < *at) {
                continue;
            }
            let Some(sampler) = samplers.get_mut(key) else {
                match open(key) {
                    // (it has nothing to compare with yet)
                    Ok(Some(sampler)) => {
                        samplers.insert(key, sampler);
                    }
                    found => {
                        if let Err(why) = found {
                            if !said.contains(key) {
                                said.push(key);
                                shared.note(format!("load: {} ({why})", no_sampler(key)));
                            }
                        }
                        retry_at.insert(key, now + Duration::from_secs_f64(RETRY_SECONDS));
                    }
                }
                continue;
            };
            if let Some(sample) = measure(sampler.as_mut()) {
                store(shared, key, sample, now, &mut cores);
            }
        }
        if let Ok(mut values) = shared.values.lock() {
            // what no theme shows any more goes
            values.retain(|key, _| shown.contains(&family(key)));
        }
        let Ok(flags) = shared.flags.lock() else {
            break;
        };
        let wait = Duration::from_secs_f64(SAMPLE_SECONDS);
        if shared
            .changed
            .wait_timeout_while(flags, wait, |flags| !flags.0)
            .map(|(flags, _)| flags.0)
            .unwrap_or(true)
        {
            break;
        }
    }
    if let Ok(mut values) = shared.values.lock() {
        values.clear();
    }
    if let Ok(mut flags) = shared.flags.lock() {
        flags.1 = false;
    }
}

/// `cpu.load`, `cpu.core.N.load` and `gpu.load`, measured ten times a second while a theme
/// shows them.
pub struct LoadSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl LoadSource {
    pub fn new(wanted: Wanted) -> LoadSource {
        LoadSource {
            shared: Arc::new(Shared {
                values: Mutex::new(IndexMap::new()),
                flags: Mutex::new((false, false)),
                changed: Condvar::new(),
                notes: Mutex::new(Vec::new()),
                wanted,
            }),
            thread: None,
        }
    }

    /// The loads measured last (none older than `STALE_SECONDS`); the thread is started where
    /// a theme reads one and it does not run.
    pub fn read(&mut self) -> Readings {
        let runs = self
            .shared
            .flags
            .lock()
            .map(|flags| flags.1)
            .unwrap_or(false);
        if !runs {
            let stopped = self
                .shared
                .flags
                .lock()
                .map(|flags| flags.0)
                .unwrap_or(true);
            if stopped || !KEYS.iter().any(|key| self.shared.wants(key)) {
                return Readings::new();
            }
            if let Some(ended) = self.thread.take() {
                let _ = ended.join();
            }
            if let Ok(mut flags) = self.shared.flags.lock() {
                flags.1 = true;
            }
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-load".into())
                .spawn(move || run(&shared))
                .ok();
        }
        let Ok(values) = self.shared.values.lock() else {
            return Readings::new();
        };
        values
            .iter()
            .filter(|(_, (at, _))| at.elapsed().as_secs_f64() <= STALE_SECONDS)
            .map(|(key, (_, reading))| (key.clone(), reading.clone()))
            .collect()
    }

    /// What the source noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .notes
            .lock()
            .map(|mut notes| std::mem::take(&mut *notes))
            .unwrap_or_default()
    }

    pub fn close(&mut self) {
        if let Ok(mut flags) = self.shared.flags.lock() {
            flags.0 = true;
        }
        self.shared.changed.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for LoadSource {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_load_is_measured_while_a_theme_reads_it() {
        let wanted = Wanted::default();
        let mut source = LoadSource::new(wanted.clone());
        assert!(source.read().is_empty()); // (nobody reads it: no thread)
        wanted.set(["cpu.load".to_string(), "cpu.core.*".to_string()]);
        let end = Instant::now() + Duration::from_secs(10);
        let mut readings = source.read();
        while !(readings.contains_key("cpu.load") && readings.contains_key("cpu.core.1.load"))
            && Instant::now() < end
        {
            std::thread::sleep(Duration::from_millis(50));
            readings = source.read();
        }
        if cfg!(any(target_os = "linux", windows)) {
            let load = &readings["cpu.load"];
            assert!(matches!(load.value, Value::Float(f) if (0.0..=100.0).contains(&f)));
            assert_eq!((load.unit.as_str(), load.label.as_str()), ("%", "CPU load"));
            assert_eq!(readings["cpu.core.1.load"].label, "Core 1");
            assert!(!readings.contains_key("gpu.load") || cfg!(windows));
        }
        // (no theme reads the cores any more: their readings go)
        wanted.set(["cpu.load".to_string()]);
        let end = Instant::now() + Duration::from_secs(10);
        while source.read().contains_key("cpu.core.1.load") && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert!(!source.read().contains_key("cpu.core.1.load"));
        source.close();
        assert!(source.read().is_empty());
    }
}

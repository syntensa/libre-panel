//! The readings a frame is drawn of: the sources that are read (sensors/base.py's SensorHub
//! and its history) and the computer's own, as sensors/psutil_provider.py reads them on Linux:
//! the processor's load, clock and threads, the memory, the system's disk, the network's and
//! the disks' rates, how long the computer runs and the processor's temperature.

use std::collections::VecDeque;
use std::time::Instant;

use indexmap::IndexMap;
use lp_py::Value;
use lp_render::{Moment, Reading, Snapshot};

/// the numbers a sensor's history holds (a graph's ten minutes)
pub const HISTORY_LENGTH: usize = 600;
const GIB: f64 = 1024.0 * 1024.0 * 1024.0;

pub type Readings = IndexMap<String, Reading>;

/// Something readings come from (SensorProvider).
pub trait Source {
    fn name(&self) -> &'static str;
    /// The readings of now, each put under its key.
    fn read(&mut self, out: &mut Readings);
}

fn put(out: &mut Readings, key: &str, value: f64, unit: &str, label: &str) {
    out.insert(
        key.to_string(),
        Reading {
            unit: unit.to_string(),
            label: label.to_string(),
            ..Reading::of(Value::Float(value))
        },
    );
}

/// round(x, 1) of a share in percent, as psutil gives it.
fn percent(part: f64, whole: f64) -> f64 {
    if whole <= 0.0 {
        return 0.0;
    }
    (part / whole * 1000.0).round() / 10.0
}

/// Collects the readings of all sources and keeps each number's history for the graphs
/// (SensorHub). A source earlier in the list wins where two have the same key.
pub struct Hub {
    sources: Vec<Box<dyn Source>>,
    history: IndexMap<String, VecDeque<f64>>,
}

impl Hub {
    pub fn new(sources: Vec<Box<dyn Source>>) -> Hub {
        Hub {
            sources,
            history: IndexMap::new(),
        }
    }

    /// The sources config.toml names, as far as this build has them; the names of those it
    /// has not.
    pub fn of(names: &[String], demo: bool) -> (Hub, Vec<String>) {
        let mut sources: Vec<Box<dyn Source>> = Vec::new();
        let mut missing = Vec::new();
        if demo {
            sources.push(Box::new(Demo::new()));
        } else {
            for name in names {
                match name.as_str() {
                    "psutil" => sources.push(Box::new(System::new())),
                    other => missing.push(other.to_string()),
                }
            }
        }
        (Hub::new(sources), missing)
    }

    /// The sources that are read, by their names.
    pub fn names(&self) -> Vec<&'static str> {
        self.sources.iter().map(|source| source.name()).collect()
    }

    /// snapshot: every source read once, the numbers among the readings added to their
    /// histories, at the moment `now`.
    pub fn snapshot(&mut self, now: Moment, fold: bool) -> Snapshot {
        let mut readings = Readings::new();
        for source in &mut self.sources {
            let mut found = Readings::new();
            source.read(&mut found);
            for (key, reading) in found {
                readings.entry(key).or_insert(reading);
            }
        }
        for (key, reading) in &readings {
            let number = match &reading.value {
                Value::Float(f) => Some(*f),
                Value::Int(n) => lp_py::num::int_to_float(n).ok(),
                _ => None,
            };
            if let Some(number) = number {
                let series = self.history.entry(key.clone()).or_default();
                if series.len() == HISTORY_LENGTH {
                    series.pop_front();
                }
                series.push_back(number);
            }
        }
        Snapshot {
            history: self
                .history
                .iter()
                .map(|(key, series)| (key.clone(), series.iter().copied().collect()))
                .collect(),
            readings,
            now,
            fold,
            images: IndexMap::new(),
        }
    }
}

/// Readings made up, moving slowly, for a look at a theme without a computer's own
/// (`--demo`).
pub struct Demo {
    began: Instant,
}

impl Demo {
    pub fn new() -> Demo {
        Demo {
            began: Instant::now(),
        }
    }
}

impl Source for Demo {
    fn name(&self) -> &'static str {
        "demo"
    }

    fn read(&mut self, out: &mut Readings) {
        let t = self.began.elapsed().as_secs_f64();
        let wave = |period: f64, low: f64, high: f64| {
            low + (high - low) * (0.5 + 0.5 * (t * std::f64::consts::TAU / period).sin())
        };
        put(out, "cpu.load", wave(23.0, 8.0, 74.0), "%", "CPU load");
        put(
            out,
            "cpu.temp",
            wave(41.0, 44.0, 71.0),
            "°C",
            "CPU temperature",
        );
        put(
            out,
            "cpu.freq",
            wave(17.0, 2800.0, 4900.0),
            "MHz",
            "CPU clock",
        );
        put(out, "cpu.cores", 16.0, "", "CPU threads");
        put(out, "gpu.load", wave(31.0, 3.0, 96.0), "%", "GPU load");
        put(
            out,
            "gpu.temp",
            wave(53.0, 41.0, 76.0),
            "°C",
            "GPU temperature",
        );
        put(out, "mem.load", wave(67.0, 38.0, 61.0), "%", "Memory usage");
        put(
            out,
            "mem.used",
            wave(67.0, 12.1, 19.6),
            "GiB",
            "Memory used",
        );
        put(out, "mem.total", 32.0, "GiB", "Memory total");
        put(
            out,
            "mem.free",
            32.0 - wave(67.0, 12.1, 19.6),
            "GiB",
            "Memory free",
        );
        put(out, "disk.load", 57.3, "%", "Disk usage");
        put(out, "disk.used", 533.0, "GiB", "Disk used");
        put(out, "disk.total", 931.0, "GiB", "Disk total");
        put(
            out,
            "net.down",
            wave(13.0, 2.0e4, 4.8e7),
            "B/s",
            "Network download",
        );
        put(
            out,
            "net.up",
            wave(19.0, 8.0e3, 6.0e6),
            "B/s",
            "Network upload",
        );
        put(out, "sys.uptime", 93_784.0 + t, "s", "Uptime");
    }
}

/// The computer's own readings (PsutilProvider), read from the kernel's files on Linux.
pub struct System {
    /// the processor's busy and whole time when they were last read
    cpu: Option<(f64, f64)>,
    /// the bytes received and sent, read and written, and when
    rates: Option<(Instant, [f64; 4])>,
}

impl System {
    pub fn new() -> System {
        let mut system = System {
            cpu: None,
            rates: None,
        };
        system.cpu = cpu_times(); // (the counter primed: the first reading is since now)
        system
    }
}

fn file(path: &str) -> Option<String> {
    std::fs::read_to_string(path).ok()
}

/// The processor's busy time and whole time since the computer started, in the kernel's
/// ticks (psutil's cpu_times: the guests' time is in the users' already).
fn cpu_times() -> Option<(f64, f64)> {
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
fn meminfo() -> IndexMap<String, f64> {
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
fn net_bytes() -> Option<(f64, f64)> {
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
fn disk_bytes() -> Option<(f64, f64)> {
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
fn cpu_freq() -> Option<f64> {
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

/// The processor's temperature: the first of the chips that measure it, its package's where
/// it names one (the provider's _CPU_CHIPS and _CPU_LABELS).
fn cpu_temp() -> Option<f64> {
    const CHIPS: [&str; 5] = ["coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"];
    const LABELS: [&str; 3] = ["Package id 0", "Tctl", "Tdie"];
    let mut chips: Vec<(String, std::path::PathBuf)> = std::fs::read_dir("/sys/class/hwmon")
        .ok()?
        .flatten()
        .filter_map(|entry| {
            let name = std::fs::read_to_string(entry.path().join("name")).ok()?;
            Some((name.trim().to_string(), entry.path()))
        })
        .collect();
    chips.sort();
    for wanted in CHIPS {
        let Some((_, folder)) = chips.iter().find(|(name, _)| name == wanted) else {
            continue;
        };
        let mut inputs: Vec<(String, f64)> = Vec::new();
        let mut names: Vec<String> = std::fs::read_dir(folder)
            .ok()?
            .flatten()
            .filter_map(|entry| entry.file_name().into_string().ok())
            .filter(|name| name.starts_with("temp") && name.ends_with("_input"))
            .collect();
        names.sort();
        for name in names {
            let read = |file: &str| std::fs::read_to_string(folder.join(file)).ok();
            let Some(value) = read(&name).and_then(|text| text.trim().parse::<f64>().ok()) else {
                continue;
            };
            let label = read(&name.replace("_input", "_label")).unwrap_or_default();
            inputs.push((label.trim().to_string(), value / 1000.0));
        }
        if inputs.is_empty() {
            continue;
        }
        let chosen = LABELS
            .iter()
            .find_map(|label| inputs.iter().find(|(name, _)| name == label))
            .unwrap_or(&inputs[0]);
        return Some(chosen.1);
    }
    None
}

/// The system's disk: its bytes used and in all, and the share used of what a user may use
/// (psutil's disk_usage).
#[cfg(unix)]
fn disk_usage(path: &str) -> Option<(f64, f64, f64)> {
    let path = std::ffi::CString::new(path).ok()?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(path.as_ptr(), &mut stat) } != 0 {
        return None;
    }
    let block = stat.f_frsize as f64;
    let total = stat.f_blocks as f64 * block;
    let used = total - stat.f_bfree as f64 * block;
    let for_user = used + stat.f_bavail as f64 * block;
    Some((used, total, percent(used, for_user)))
}

#[cfg(not(unix))]
fn disk_usage(_path: &str) -> Option<(f64, f64, f64)> {
    None
}

impl Source for System {
    fn name(&self) -> &'static str {
        "psutil"
    }

    fn read(&mut self, out: &mut Readings) {
        if let Some((busy, whole)) = cpu_times() {
            let load = match self.cpu {
                Some((busy0, whole0)) => percent(busy - busy0, whole - whole0),
                None => 0.0,
            };
            self.cpu = Some((busy, whole));
            put(out, "cpu.load", load.clamp(0.0, 100.0), "%", "CPU load");
        }
        if let Some(clock) = cpu_freq() {
            put(out, "cpu.freq", clock, "MHz", "CPU clock");
        }
        let threads = std::thread::available_parallelism().map_or(0, |n| n.get());
        put(out, "cpu.cores", threads as f64, "", "CPU threads");

        let mem = meminfo();
        let of = |name: &str| mem.get(name).copied().unwrap_or(0.0);
        if of("MemTotal") > 0.0 {
            let (total, free) = (of("MemTotal"), of("MemAvailable"));
            put(
                out,
                "mem.load",
                percent(total - free, total),
                "%",
                "Memory usage",
            );
            put(out, "mem.used", (total - free) / GIB, "GiB", "Memory used");
            put(out, "mem.total", total / GIB, "GiB", "Memory total");
            put(out, "mem.free", free / GIB, "GiB", "Memory free");
            let swap = percent(of("SwapTotal") - of("SwapFree"), of("SwapTotal"));
            put(out, "swap.load", swap, "%", "Swap usage");
        }
        if let Some((used, total, load)) = disk_usage("/") {
            put(out, "disk.load", load, "%", "Disk usage");
            put(out, "disk.used", used / GIB, "GiB", "Disk used");
            put(out, "disk.total", total / GIB, "GiB", "Disk total");
        }

        let now = Instant::now();
        let (net, disk) = (net_bytes(), disk_bytes());
        let counters = [
            net.map_or(f64::NAN, |n| n.0),
            net.map_or(f64::NAN, |n| n.1),
            disk.map_or(f64::NAN, |d| d.0),
            disk.map_or(f64::NAN, |d| d.1),
        ];
        if let Some((then, before)) = self.rates {
            let dt = now.duration_since(then).as_secs_f64().max(1e-3);
            let keys = [
                ("net.down", "Network download"),
                ("net.up", "Network upload"),
                ("disk.read", "Disk read"),
                ("disk.write", "Disk write"),
            ];
            for (n, (key, label)) in keys.iter().enumerate() {
                let rate = (counters[n] - before[n]) / dt;
                if rate.is_finite() {
                    put(out, key, rate.max(0.0), "B/s", label);
                }
            }
        }
        self.rates = Some((now, counters));

        if let Some(temp) = cpu_temp() {
            put(out, "cpu.temp", temp, "°C", "CPU temperature");
        }
        let uptime = file("/proc/uptime")
            .and_then(|text| text.split_whitespace().next()?.parse::<f64>().ok());
        if let Some(uptime) = uptime {
            put(out, "sys.uptime", uptime, "s", "Uptime");
        }
    }
}

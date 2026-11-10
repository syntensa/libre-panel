//! The computer's own readings (sensors/psutil_provider.py's PsutilProvider), under the names
//! it gives them: the processor's load, clock and threads and every core's load, the memory
//! and the swap, the system's disk and every drive, the network's and the disks' rates, the
//! address, how the PC is connected and what it received and sent today, the chips'
//! temperatures and fans (Linux), the battery, how long the computer runs, and, only while a
//! theme shows them, the programs that use the most processor time and memory and the time a
//! connection to a host takes (`net.ping`).

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use indexmap::IndexMap;
use lp_py::toml::{Table, Toml};
use lp_py::Value;
use lp_render::Reading;
use lp_sensors::netlink;
use lp_sensors::service::{wanted, Wanted};

use crate::osinfo::{self, Entries};
use crate::sensors::{Readings, Source};
use crate::system;

const GIB: f64 = 1024.0 * 1024.0 * 1024.0;
/// Preferred temperature chips, in order, for cpu.temp and gpu.temp on Linux.
const CPU_CHIPS: [&str; 5] = ["coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"];
const CPU_LABELS: [&str; 4] = ["Package id 0", "Tctl", "Tdie", ""];
const GPU_CHIPS: [&str; 3] = ["amdgpu", "nouveau", "radeon"];
/// File systems that are not drives a user thinks of (Linux).
const PSEUDO_FS: [&str; 11] = [
    "squashfs",
    "tmpfs",
    "devtmpfs",
    "overlay",
    "proc",
    "sysfs",
    "autofs",
    "ramfs",
    "nsfs",
    "efivarfs",
    "fuse.portal",
];
/// top processes kept, per sort
const TOP: usize = 8;
const PING_EVERY: f64 = 5.0;
/// net.ping.lost: the share of so many last tries that got no answer
const PING_TRIES: usize = 20;
/// the most the ping connects to
const PING_HOSTS: usize = 4;
const PROCESSES_EVERY: f64 = 2.0;
/// how often the connection is looked at (net.link)
const LINK_EVERY_S: f64 = 10.0;

/// Names people know for the chips Linux reports (hwmon.py's CHIP_NAMES).
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

fn slug(key: &str) -> String {
    lp_py::lower(&key.replace(' ', "_"))
}

/// unique: `key` and `name`, numbered from the second sensor that has them on: Linux names
/// the sensors of two NVMe drives (or two GPUs) alike.
fn unique(key: String, name: String, used: &mut IndexMap<String, usize>) -> (String, String) {
    let count = used.entry(key.clone()).or_insert(0);
    *count += 1;
    if *count == 1 {
        (key, name)
    } else {
        (format!("{key}_{count}"), format!("{name} {count}"))
    }
}

fn reading(value: Value, unit: &str, label: &str) -> Reading {
    Reading {
        unit: unit.to_string(),
        label: label.to_string(),
        ..Reading::of(value)
    }
}

fn put(out: &mut Readings, key: &str, value: f64, unit: &str, label: &str) {
    out.insert(key.to_string(), reading(Value::Float(value), unit, label));
}

fn put_text(out: &mut Readings, key: &str, value: &str, label: &str) {
    out.insert(
        key.to_string(),
        reading(Value::Str(value.to_string()), "", label),
    );
}

/// round(x, 1) of a share in percent, as psutil gives it.
fn share(part: f64, whole: f64) -> f64 {
    if whole <= 0.0 {
        return 0.0;
    }
    lp_py::round_digits(part / whole * 100.0, 1)
}

/// link_readings: how the PC is connected, as readings: `net.link` ("LAN", "Wi-Fi"),
/// `net.link.kind` ("lan", "wlan"), `net.link.speed` (Mbit/s), `net.wifi.signal` (%) and
/// `net.link.text` ("Wi-Fi · 866 Mbit/s").
pub fn link_readings(link: Option<&netlink::Link>) -> Readings {
    let mut out = Readings::new();
    let Some(link) = link else {
        return out;
    };
    let said = if link.kind == netlink::WLAN {
        lp_i18n::t("Wi-Fi")
    } else {
        lp_i18n::t("LAN")
    };
    put_text(&mut out, "net.link", &said, "Connection");
    put_text(&mut out, "net.link.kind", link.kind, "Connection kind");
    let mut text = said.clone();
    if let Some(speed) = link.speed.filter(|speed| *speed != 0.0) {
        put(&mut out, "net.link.speed", speed, "Mbit/s", "Link speed");
        text = format!("{said} · {}", netlink::speed_text(speed));
    }
    put_text(&mut out, "net.link.text", &text, "Connection");
    if let Some(signal) = link.signal {
        put(&mut out, "net.wifi.signal", signal, "%", "Wi-Fi signal");
    }
    out
}

/// 'chrome.exe' -> 'chrome'.
fn program_name(name: &str) -> &str {
    if lp_py::lower(name).ends_with(".exe") {
        let cut = name.chars().count() - 4;
        let end = name
            .char_indices()
            .nth(cut)
            .map_or(name.len(), |(at, _)| at);
        &name[..end]
    } else {
        name
    }
}

/// The readings of the programs that use the most processor time since the last count, and
/// the most memory: one row per program (browsers have many).
pub fn top_processes(rows: &[(String, f64, f64)]) -> Readings {
    let mut merged: IndexMap<&str, [f64; 2]> = IndexMap::new();
    for (name, cpu, mem) in rows {
        let total = merged.entry(name.as_str()).or_insert([0.0, 0.0]);
        total[0] += cpu;
        total[1] += mem;
    }
    let mut out = Readings::new();
    for (sort, index) in [("cpu", 0), ("mem", 1)] {
        let mut top: Vec<(&str, [f64; 2])> = merged.iter().map(|(n, v)| (*n, *v)).collect();
        // (the most first; equals as they came)
        top.sort_by(|a, b| {
            b.1[index]
                .partial_cmp(&a.1[index])
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        for (n, (name, values)) in top.into_iter().take(TOP).enumerate() {
            let key = format!("proc.{sort}.{}", n + 1);
            let label = program_name(name);
            put_text(&mut out, &format!("{key}.name"), label, label);
            put(
                &mut out,
                &format!("{key}.value"),
                lp_py::round_digits(values[index], 1),
                "%",
                label,
            );
        }
    }
    out
}

/// (host, port) of what the ping connects to: "example.org", "1.1.1.1:443", "[::1]:53".
pub fn ping_target(target: &str) -> (String, String) {
    let (mut host, mut port) = match target.rfind(':') {
        Some(at) => (&target[..at], &target[at + 1..]),
        None => ("", target),
    };
    if host.is_empty() || port.contains(']') {
        // no port given ("example.org", "[::1]")
        (host, port) = (target, "443");
    }
    (host.trim_matches(['[', ']']).to_string(), port.to_string())
}

/// What the background threads hold, and whether they run.
#[derive(Default)]
struct Background {
    proc: Mutex<Readings>,
    ping: Mutex<Readings>,
    proc_runs: AtomicBool,
    ping_runs: AtomicBool,
    stop: AtomicBool,
}

impl Background {
    /// Waits `seconds`, or until the source is closed: true then.
    fn wait(&self, seconds: f64) -> bool {
        let end = Instant::now() + Duration::from_secs_f64(seconds);
        while Instant::now() < end {
            if self.stop.load(Ordering::Relaxed) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        self.stop.load(Ordering::Relaxed)
    }
}

/// Whether a background thread runs, and what it holds.
type Runs = fn(&Background) -> &AtomicBool;
type Held = fn(&Background) -> &Mutex<Readings>;

/// _keep: `work` done every `every` seconds in a thread of its own while a theme reads a key
/// with `prefix`; nobody looked for a minute: it ends, and starts again on demand.
fn keep(
    background: &Arc<Background>,
    wanted_keys: &Wanted,
    prefix: &'static str,
    every: f64,
    mut work: impl FnMut() -> Readings + Send + 'static,
) {
    let (runs, held): (Runs, Held) = if prefix == "proc." {
        (|b| &b.proc_runs, |b| &b.proc)
    } else {
        (|b| &b.ping_runs, |b| &b.ping)
    };
    if runs(background).swap(true, Ordering::SeqCst) {
        return;
    }
    let (shared, wanted_keys) = (background.clone(), wanted_keys.clone());
    let spawned = std::thread::Builder::new()
        .name(format!("sensors-{}", prefix.trim_end_matches('.')))
        .spawn(move || {
            let mut idle = 0.0;
            loop {
                if shared.stop.load(Ordering::Relaxed) {
                    break;
                }
                if wanted(&wanted_keys.keys(), prefix) {
                    idle = 0.0;
                    let found = work();
                    if let Ok(mut kept) = held(&shared).lock() {
                        *kept = found;
                    }
                } else {
                    idle += every;
                    if idle > 60.0 {
                        if let Ok(mut kept) = held(&shared).lock() {
                            kept.clear();
                        }
                        break;
                    }
                }
                if shared.wait(every) {
                    break;
                }
            }
            runs(&shared).store(false, Ordering::SeqCst);
        });
    if spawned.is_err() {
        runs(background).store(false, Ordering::SeqCst);
    }
}

/// The computer's own readings.
pub struct System {
    wanted: Wanted,
    /// the system's disk (`[sensors.psutil] disk`)
    disk_path: String,
    /// false where another source reads the kernel's chips: the same files
    pub hwmon: bool,
    /// the processor's busy and whole time when they were last read, and each core's
    cpu: Option<(f64, f64)>,
    cores: Option<Vec<(f64, f64)>>,
    /// the bytes received and sent, read and written, and when
    rates: Option<(Instant, [f64; 4])>,
    /// (mount point, name) of every drive, and when they were looked for
    partitions: Vec<(String, String)>,
    partitions_at: Option<Instant>,
    ip: String,
    ip_at: Option<Instant>,
    link: Readings,
    link_at: Option<Instant>,
    /// today, and the network's counters at midnight (or at the start)
    day: Option<(i64, u32, u32)>,
    day_start: (f64, f64),
    /// what the ping connects to: up to four of host:port (net.ping, net.ping.2 ...)
    ping_targets: Vec<String>,
    background: Arc<Background>,
}

fn system_disk() -> String {
    if cfg!(windows) {
        return format!(
            "{}\\",
            std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into())
        );
    }
    // Fedora Atomic (Bazzite, Silverblue, Kinoite; composefs) mounts a read-only image of a
    // few MB at "/", always full; the disk itself is at /sysroot.
    let tiny = osinfo::usage("/").is_some_and(|usage| usage.total < GIB);
    if osinfo::is_mount("/sysroot") && tiny {
        return "/sysroot".into();
    }
    "/".into()
}

/// os.path.normcase.
fn normcase(path: &str) -> String {
    if cfg!(windows) {
        lp_py::lower(&path.replace('/', "\\"))
    } else {
        path.to_string()
    }
}

/// The local date of now.
fn today() -> (i64, u32, u32) {
    let since = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64());
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    lp_py::zones::fromtimestamp(zone, since)
        .map_or((1970, 1, 1), |(now, _)| (now.year, now.month, now.day))
}

impl System {
    /// The source as `[sensors.psutil]` asks for it; `wanted`: the keys the themes read.
    pub fn new(options: Option<&Table>, wanted: Wanted) -> System {
        let text = |key: &str| match options.and_then(|table| table.get(key)) {
            Some(Toml::Str(text)) => Some(text.clone()),
            _ => None,
        };
        let mut targets: Vec<String> = match options.and_then(|table| table.get("ping")) {
            Some(Toml::Str(one)) => vec![one.clone()],
            Some(Toml::Array(many)) => many
                .iter()
                .filter_map(|item| match item {
                    Toml::Str(text) => Some(text.clone()),
                    _ => None,
                })
                .collect(),
            _ => vec!["1.1.1.1:443".into()],
        };
        targets = targets
            .iter()
            .map(|target| lp_py::strip(target).to_string())
            .filter(|target| !target.is_empty())
            .take(PING_HOSTS)
            .collect();
        if targets.is_empty() {
            targets.push("1.1.1.1:443".into());
        }
        System {
            wanted,
            disk_path: text("disk").unwrap_or_else(system_disk),
            hwmon: true,
            cpu: system::cpu_times(), // (the counters primed: the first reading is since now)
            cores: lp_sensors::load::core_times(),
            rates: None,
            partitions: Vec::new(),
            partitions_at: None,
            ip: String::new(),
            ip_at: None,
            link: Readings::new(),
            link_at: None,
            day: None,
            day_start: (0.0, 0.0),
            ping_targets: targets,
            background: Arc::new(Background::default()),
        }
    }

    fn wants(&self, prefix: &str) -> bool {
        wanted(&self.wanted.keys(), prefix)
    }

    /// The network's and the disks' rates; what the network received and sent in all.
    fn rates(&mut self, out: &mut Readings) -> Option<(f64, f64)> {
        let now = Instant::now();
        let (net, disk) = (system::net_bytes(), system::disk_bytes());
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
        net
    }

    /// Every chip's temperatures as temp.<chip>.<label>, and the processor's and the graphics
    /// card's own among them.
    fn temperatures(out: &mut Readings, temps: &IndexMap<String, Entries>) {
        let mut keys: IndexMap<(&str, usize), String> = IndexMap::new();
        let mut used = IndexMap::new();
        for (chip, entries) in temps {
            for (i, (label, current)) in entries.iter().enumerate() {
                let (said, numbered) = if label.is_empty() {
                    (i.to_string(), (i + 1).to_string())
                } else {
                    (label.clone(), label.clone())
                };
                let name = format!("{} {numbered}", chip_name(chip));
                let (key, name) = unique(slug(&format!("temp.{chip}.{said}")), name, &mut used);
                put(out, &key, *current, "°C", &name);
                keys.insert((chip.as_str(), i), key);
            }
        }
        let mut alias = |key: &str, chip: &str, index: usize, label: &str| {
            let (Some(entries), Some(origin)) = (temps.get(chip), keys.get(&(chip, index))) else {
                return;
            };
            out.insert(
                key.to_string(),
                Reading {
                    origin: origin.clone(),
                    ..reading(Value::Float(entries[index].1), "°C", label)
                },
            );
        };
        for chip in CPU_CHIPS {
            let Some(entries) = temps.get(chip).filter(|entries| !entries.is_empty()) else {
                continue;
            };
            // (the last sensor with a label is the one the label names)
            let place = |label: &str| entries.iter().rposition(|(said, _)| said == label);
            let index = CPU_LABELS
                .iter()
                .find_map(|label| place(label))
                .unwrap_or(0);
            alias("cpu.temp", chip, index, "CPU temperature");
            break;
        }
        for chip in GPU_CHIPS {
            if temps.get(chip).is_some_and(|entries| !entries.is_empty()) {
                alias("gpu.temp", chip, 0, "GPU temperature");
                break;
            }
        }
    }

    fn fans(out: &mut Readings, fans: &IndexMap<String, Entries>) {
        let mut used = IndexMap::new();
        for (chip, entries) in fans {
            for (i, (label, current)) in entries.iter().enumerate() {
                let (said, numbered) = if label.is_empty() {
                    (i.to_string(), (i + 1).to_string())
                } else {
                    (label.clone(), label.clone())
                };
                let name = format!("{} {numbered}", chip_name(chip));
                let (key, name) = unique(slug(&format!("fan.{chip}.{said}")), name, &mut used);
                put(out, &key, *current, "RPM", &name);
            }
        }
    }

    /// disk.<n>.name|load|used|free|total for every drive, the system's first.
    fn drives(&mut self, out: &mut Readings) {
        let old = self
            .partitions_at
            .is_none_or(|at| at.elapsed().as_secs_f64() > 30.0);
        if old || self.partitions.is_empty() {
            self.partitions = find_partitions(&osinfo::partitions(), &self.disk_path);
            self.partitions_at = Some(Instant::now());
        }
        for (n, (mount, name)) in self.partitions.iter().enumerate() {
            let Some(usage) = osinfo::usage(mount) else {
                continue;
            };
            let key = |what: &str| format!("disk.{}.{what}", n + 1);
            put_text(out, &key("name"), name, name);
            put(out, &key("load"), usage.percent, "%", name);
            put(out, &key("used"), usage.used / GIB, "GiB", name);
            put(out, &key("free"), usage.free / GIB, "GiB", name);
            put(out, &key("total"), usage.total / GIB, "GiB", name);
        }
    }

    fn cores(&mut self, out: &mut Readings) {
        let Some(now) = lp_sensors::load::core_times() else {
            return;
        };
        let before = self.cores.replace(now.clone());
        for (n, (busy, total)) in now.iter().enumerate() {
            // (psutil's cpu_percent per core: since the last call, to a tenth; 0 at first)
            let load = match before.as_ref().and_then(|before| before.get(n)) {
                Some((busy0, total0)) if before.as_ref().is_some_and(|b| b.len() == now.len()) => {
                    share(busy - busy0, total - total0).clamp(0.0, 100.0)
                }
                _ => 0.0,
            };
            let n = n + 1;
            put(
                out,
                &format!("cpu.core.{n}.load"),
                load,
                "%",
                &format!("Core {n}"),
            );
        }
    }

    fn network(&mut self, out: &mut Readings, net: Option<(f64, f64)>) {
        if self
            .ip_at
            .is_none_or(|at| at.elapsed().as_secs_f64() > 30.0)
        {
            // (addresses change rarely)
            self.ip = osinfo::local_ip();
            self.ip_at = Some(Instant::now());
        }
        if !self.ip.is_empty() {
            put_text(out, "net.ip", &self.ip, "IP address");
        }
        if self.wants("net.link") || self.wants("net.wifi") {
            // (only while a theme shows it)
            if self
                .link_at
                .is_none_or(|at| at.elapsed().as_secs_f64() >= LINK_EVERY_S)
            {
                self.link = link_readings(netlink::read().as_ref());
                self.link_at = Some(Instant::now());
            }
            out.extend(self.link.clone());
        }
        let Some(net) = net else {
            return;
        };
        let day = today();
        if self.day != Some(day) {
            // the first reading, or midnight
            self.day = Some(day);
            self.day_start = net;
        }
        put(
            out,
            "net.today.down",
            (net.0 - self.day_start.0).max(0.0),
            "B",
            "Received today",
        );
        put(
            out,
            "net.today.up",
            (net.1 - self.day_start.1).max(0.0),
            "B",
            "Sent today",
        );
    }

    fn battery(out: &mut Readings) {
        let Some(battery) = osinfo::battery() else {
            return;
        };
        put(out, "battery.load", battery.percent, "%", "Battery");
        if let Some(plugged) = battery.plugged {
            out.insert(
                "battery.plugged".into(),
                reading(Value::int(i64::from(plugged)), "", "Plugged in"),
            );
            let state = if !plugged {
                "On battery"
            } else if battery.percent >= 99.5 {
                "Charged"
            } else {
                "Charging"
            };
            put_text(out, "battery.state", state, "Battery");
        }
        if battery.secsleft >= 0 && battery.plugged != Some(true) {
            put(
                out,
                "battery.left",
                battery.secsleft as f64,
                "s",
                "Battery time left",
            );
        }
    }
}

/// _find_partitions: (mount point, name) of the drives a user thinks of, the system's first.
pub fn find_partitions(parts: &[osinfo::Partition], disk_path: &str) -> Vec<(String, String)> {
    let mut found: Vec<(String, String)> = Vec::new();
    let mut seen: Vec<&str> = Vec::new();
    let system = normcase(disk_path);
    for part in parts {
        let options: Vec<&str> = part.opts.split(',').collect();
        if PSEUDO_FS.contains(&part.fstype.as_str())
            || options.contains(&"cdrom")
            || part.fstype.is_empty()
        {
            continue;
        }
        if !cfg!(windows)
            && options.contains(&"ro")
            && part.mountpoint != "/"
            && part.mountpoint != disk_path
        {
            continue; // read-only images, recovery
        }
        if ["/snap", "/boot", "/System/Volumes"]
            .iter()
            .any(|start| part.mountpoint.starts_with(start))
        {
            continue;
        }
        if seen.contains(&part.device.as_str()) {
            continue;
        }
        seen.push(&part.device);
        let mut name = if cfg!(windows) {
            part.mountpoint.trim_end_matches('\\').to_string()
        } else {
            part.mountpoint.clone()
        };
        if name == "/sysroot" {
            name = "/".into(); // the system's disk (see system_disk)
        }
        if name.is_empty() {
            name = part.mountpoint.clone();
        }
        found.push((part.mountpoint.clone(), name));
    }
    // the system drive first
    found.sort_by_key(|(mount, _)| normcase(mount) != system);
    found
}

/// The programs' rows: each one's name, its share of the processor since the last count and
/// of the memory. `seen`: every program's processor seconds and when, by its id.
fn process_rows(seen: &mut IndexMap<u64, (f64, Instant)>) -> Vec<(String, f64, f64)> {
    let cores = std::thread::available_parallelism().map_or(1, |n| n.get()) as f64;
    let mut rows = Vec::new();
    let mut now_seen = IndexMap::new();
    for process in osinfo::processes() {
        let now = Instant::now();
        now_seen.insert(process.pid, (process.cpu_seconds, now));
        let mut cpu = 0.0;
        if let Some((spent, then)) = seen.get(&process.pid) {
            let passed = now.duration_since(*then).as_secs_f64();
            if passed > 0.0 {
                cpu = (process.cpu_seconds - spent).max(0.0) / passed / cores * 100.0;
            }
        }
        let idle = matches!(
            lp_py::lower(&process.name).as_str(),
            "system idle process" | "idle"
        );
        if process.name.is_empty() || process.pid == 0 || idle {
            continue;
        }
        rows.push((process.name, cpu.min(100.0), process.memory_percent));
    }
    *seen = now_seen;
    rows
}

/// Each target's time to connect: `net.ping` for the first, `net.ping.2` and on for the
/// others, each with the share of its last tries that got no answer and its host.
fn ping(targets: &[String], tries: &mut [VecDeque<bool>]) -> Readings {
    use std::net::{TcpStream, ToSocketAddrs};
    let mut out = Readings::new();
    for (n, target) in targets.iter().enumerate() {
        let (host, port) = ping_target(target);
        let start = Instant::now();
        let reached = lp_py::int_of_str(&port)
            .and_then(|port| u16::try_from(port).ok())
            .and_then(|port| (host.as_str(), port).to_socket_addrs().ok())
            .is_some_and(|mut addresses| {
                addresses.any(|address| {
                    TcpStream::connect_timeout(&address, Duration::from_secs(2)).is_ok()
                })
            });
        let ms = reached.then(|| start.elapsed().as_secs_f64() * 1000.0);
        let kept = &mut tries[n];
        if kept.len() == PING_TRIES {
            kept.pop_front();
        }
        kept.push_back(ms.is_some());
        let lost = kept.iter().filter(|answered| !**answered).count() as f64;
        let lost = lp_py::round(100.0 * lost / kept.len() as f64) as i64;
        let key = if n == 0 {
            "net.ping".to_string()
        } else {
            format!("net.ping.{}", n + 1)
        };
        let (value, label) = match ms {
            Some(ms) => (
                Value::Float(lp_py::round_digits(ms, 1)),
                format!("Ping {host}"),
            ),
            None => (Value::None, "Ping".to_string()),
        };
        out.insert(key.clone(), reading(value, "ms", &label));
        out.insert(
            format!("{key}.lost"),
            reading(Value::int(lost), "%", "Ping lost"),
        );
        put_text(&mut out, &format!("{key}.host"), &host, "Ping host");
    }
    out
}

impl Source for System {
    fn name(&self) -> &'static str {
        "psutil"
    }

    /// The threads that read the programs and the ping get the time of their first round
    /// (a host that does not answer takes its two seconds).
    fn wait_ready(&mut self, seconds: f64) {
        let end = Instant::now() + Duration::from_secs_f64(seconds);
        let waiting = |runs: &AtomicBool, held: &Mutex<Readings>| {
            runs.load(Ordering::SeqCst) && held.lock().is_ok_and(|held| held.is_empty())
        };
        let shared = &self.background;
        while Instant::now() < end
            && (waiting(&shared.proc_runs, &shared.proc)
                || waiting(&shared.ping_runs, &shared.ping))
        {
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    fn read(&mut self, out: &mut Readings) {
        if let Some((busy, whole)) = system::cpu_times() {
            let load = match self.cpu {
                Some((busy0, whole0)) => share(busy - busy0, whole - whole0),
                None => 0.0,
            };
            self.cpu = Some((busy, whole));
            put(out, "cpu.load", load.clamp(0.0, 100.0), "%", "CPU load");
        }
        if let Some(clock) = system::cpu_freq() {
            put(out, "cpu.freq", clock, "MHz", "CPU clock");
        }
        let threads = std::thread::available_parallelism().map_or(0, |n| n.get());
        put(out, "cpu.cores", threads as f64, "", "CPU threads");

        let mem = system::meminfo();
        let of = |name: &str| mem.get(name).copied().unwrap_or(0.0);
        if of("MemTotal") > 0.0 {
            let (total, free) = (of("MemTotal"), of("MemAvailable"));
            put(
                out,
                "mem.load",
                share(total - free, total),
                "%",
                "Memory usage",
            );
            put(out, "mem.used", (total - free) / GIB, "GiB", "Memory used");
            put(out, "mem.total", total / GIB, "GiB", "Memory total");
            // what programs can still take without the system swapping: used and free add up
            put(out, "mem.free", free / GIB, "GiB", "Memory free");
        }
        let swap = if mem.contains_key("SwapTotal") {
            share(of("SwapTotal") - of("SwapFree"), of("SwapTotal"))
        } else {
            osinfo::swap_percent().unwrap_or(0.0)
        };
        put(out, "swap.load", swap, "%", "Swap usage");
        if let Some(usage) = osinfo::usage(&self.disk_path) {
            put(out, "disk.load", usage.percent, "%", "Disk usage");
            put(out, "disk.used", usage.used / GIB, "GiB", "Disk used");
            put(out, "disk.total", usage.total / GIB, "GiB", "Disk total");
        }
        let net = self.rates(out);
        if self.hwmon {
            System::temperatures(out, &osinfo::temperatures());
            System::fans(out, &osinfo::fans());
        }
        self.cores(out);
        self.drives(out);
        self.network(out, net);
        if self.wants("proc.") {
            let mut seen = IndexMap::new();
            keep(
                &self.background,
                &self.wanted,
                "proc.",
                PROCESSES_EVERY,
                move || top_processes(&process_rows(&mut seen)),
            );
            if let Ok(kept) = self.background.proc.lock() {
                out.extend(kept.clone());
            }
        }
        if self.wants("net.ping") {
            let targets = self.ping_targets.clone();
            let mut tries = vec![VecDeque::with_capacity(PING_TRIES); targets.len()];
            keep(
                &self.background,
                &self.wanted,
                "net.ping",
                PING_EVERY,
                move || ping(&targets, &mut tries),
            );
            if let Ok(kept) = self.background.ping.lock() {
                out.extend(kept.clone());
            }
        }
        System::battery(out);
        if let Some(uptime) = system::uptime() {
            put(out, "sys.uptime", uptime, "s", "Uptime");
        }
    }
}

impl Drop for System {
    fn drop(&mut self) {
        self.background.stop.store(true, Ordering::Relaxed);
    }
}

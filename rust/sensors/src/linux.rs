//! Linux sensors, read without root: the kernel's hwmon chips, the graphics card, the power
//! (sensors/linux.py).
//!
//! - **Temperatures and fans** of every hwmon chip: the processor (k10temp, coretemp,
//!   zenpower), the mainboard's Super I/O chip (nct6775, it87, ...: `fan.lpc.fan_1` as
//!   Windows names it), the board's embedded controller (asus_ec_sensors), NVMe and SATA
//!   drives (nvme, drivetemp) and the memory modules (spd5118, jc42: `mem.temp`). The keys
//!   are those the psutil source always gave (`temp.k10temp.tctl`); `cpu.temp` and
//!   `mem.temp` repeat the one that counts.
//! - **The processor's power** (`cpu.power`) from the energy counters of RAPL, where the user
//!   may read them (`libre-panel system-setup --linux` can allow it), else from the chips
//!   that report it (zenpower, fam15h_power, amd_energy).
//! - **The graphics card** (`gpu.*`): AMD's amdgpu driver (temperature edge, junction and
//!   memory, fan, power, clocks, load, video memory), Intel's i915 and xe (power, clock,
//!   temperatures where the driver has them), NVIDIA's proprietary driver through NVML.
//!
//! Reading is cheap: the files are found once, a rescan every few seconds finds chips that
//! appear (a module loaded by `system-setup`, a USB cooler), and a reading is one tiny file.
//! What can take long or wake something (drives, memory on the SMBus) is read by a thread of
//! its own, and a card that is powered down to save energy is left alone.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_py::Value;
use lp_render::Reading;

use crate::gpus::{self, Card};
use crate::hardware::Adapter;
use crate::hwmon::{self, Channel, Chip, ChipKind, Item};
use crate::lhm::Readings;
use crate::nvml::{self, Nvml};
use crate::rapl::{self, Joules};
use crate::service::{wanted, Wanted};
use crate::sysfs::read_text;

/// chips come and go: a module loaded, a cooler plugged in
pub const RESCAN_SECONDS: f64 = 10.0;
/// how often the drives' and the memory's temperatures are read
pub const SLOW_SECONDS: f64 = 5.0;
/// an older reading of those is dropped, not shown standing still
pub const SLOW_FRESH: f64 = 30.0;
/// the first reading waits this long for the slow ones
pub const FIRST_PASS_SECONDS: f64 = 0.25;
const MIB: f64 = 1024.0 * 1024.0;

// Where cpu.temp comes from: the chips in order, and in them the sensors in order ("" is a
// sensor without a label). Tdie is the temperature of the die where a chip gives Tctl as
// well, which on some processors has an offset added to it. After the chips the thermal
// zones: where no chip has a temperature.
const CPU_TEMP_CHIPS: [&str; 8] = [
    "coretemp",
    "k10temp",
    "zenpower",
    "zenpower3",
    "cpu_thermal",
    "cpu-thermal",
    "x86_pkg_temp",
    "acpitz",
];
const CPU_TEMP_LABELS: [&str; 4] = ["Package id 0", "Tdie", "Tctl", ""];
/// chips with power channels of their own
const POWER_CHIPS: [&str; 3] = ["zenpower", "zenpower3", "fam15h_power"];

/// The channels of the main card's hwmon chip that make the `gpu.*` readings.
#[derive(Clone, Debug)]
struct GpuChannels {
    temp: Option<Channel>,
    hotspot: Option<Channel>,
    memory_temp: Option<Channel>,
    fan: Option<Channel>,
    pwm: Option<Channel>,
    power: Option<Channel>,
    /// a card that counts energy (Intel's) instead of telling power
    energy: Option<Joules>,
    clock: Option<Channel>,
    memory_clock: Option<Channel>,
}

fn gpu_channels(chip: &Chip) -> GpuChannels {
    let first = |kind: &str| chip.of(kind).first().map(|channel| (*channel).clone());
    let labelled = |kind: &str, labels: &[&str]| chip.labelled(kind, labels).cloned();
    GpuChannels {
        temp: labelled("temp", &["edge", "gpu", "pkg", "core", ""]).or_else(|| first("temp")),
        hotspot: labelled("temp", &["junction", "hotspot", "hot spot"]),
        memory_temp: labelled("temp", &["mem", "vram", "memory"]),
        fan: first("fan"),
        pwm: first("pwm"),
        power: first("power"),
        energy: first("energy").map(|energy| {
            Joules::new(vec![rapl::Counter {
                path: energy.path,
                wrap: 0,
            }])
        }),
        clock: labelled("freq", &["sclk", "gfx"]).or_else(|| first("freq")),
        memory_clock: labelled("freq", &["mclk", "mem"]),
    }
}

/// What a scan looks at to see whether anything is new.
#[derive(Clone, Debug, Default, PartialEq)]
struct Signature {
    chips: Vec<(PathBuf, String, usize)>,
    cards: Vec<(String, String, String)>,
    library: bool,
    prefer: String,
    counters: Vec<PathBuf>,
}

/// What the last scan found: the chips with their keys, and where the `cpu.*` and `gpu.*`
/// readings come from. Replaced as a whole, so that a reading never sees half of one.
#[derive(Default)]
struct Plan {
    signature: Option<Signature>,
    chips: Vec<Chip>,
    items: Vec<Item>,
    /// places in `items`
    fast: Vec<usize>,
    slow: Vec<usize>,
    /// the slow ones' names (drive models)
    names: HashMap<String, String>,
    keys: HashMap<Channel, String>,
    cpu_temp: Option<usize>,
    memory: Vec<usize>,
    cards: Vec<Card>,
    /// the card the gpu.* readings come from, as its place in `cards`
    card: Option<usize>,
    /// its hwmon chip
    channels: Option<GpuChannels>,
    nvidia: Vec<(usize, nvml::Device)>,
    /// the processor's power from RAPL ...
    package_power: Option<Joules>,
    /// ... or from amd_energy's sockets ...
    socket_power: Option<Joules>,
    /// ... or from a chip that tells it
    chip_power: Vec<Channel>,
}

/// The sensor behind cpu.temp: the first processor chip there is, and its package sensor.
fn cpu_temp_item(items: &[Item], chips: &[Chip]) -> Option<usize> {
    for chip_name in CPU_TEMP_CHIPS {
        let found: Vec<usize> = (0..items.len())
            .filter(|n| chips[items[*n].chip].name == chip_name && items[*n].channel.kind == "temp")
            .collect();
        if found.is_empty() {
            continue;
        }
        for label in CPU_TEMP_LABELS {
            let label = lp_py::lower(label);
            let labelled = found
                .iter()
                .find(|n| lp_py::lower(&items[**n].channel.label) == label);
            if let Some(n) = labelled {
                return Some(*n);
            }
        }
        return Some(found[0]);
    }
    None
}

/// What the app and `libre-panel sensors` say about the source.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Status {
    pub chips: usize,
    pub temperatures: usize,
    pub fans: usize,
    /// the driver of the card the gpu.* readings come from
    pub graphics: Option<String>,
    /// NVIDIA's library answers
    pub nvml: bool,
    /// "RAPL", "chip" or nothing
    pub cpu_power: Option<&'static str>,
}

/// What the thread of the slow ones and the source share.
struct Shared {
    /// the slow items of the plan, each with whether its chip is drivetemp
    items: Mutex<Vec<(Item, bool)>>,
    /// key -> (when read, value)
    found: Mutex<HashMap<String, (f64, f64)>>,
    /// the slow thread has read once
    first: (Mutex<bool>, Condvar),
    stop: (Mutex<bool>, Condvar),
    wanted: Wanted,
    began: Instant,
}

impl Shared {
    fn stopped(&self) -> bool {
        self.stop.0.lock().map(|stop| *stop).unwrap_or(true)
    }

    /// One round over the drives and memory modules (the thread makes one every few
    /// seconds). A drive in standby may wake up when it is asked, so SATA drives are read
    /// only while a theme shows them.
    fn read_slow(&self) {
        let items = self.items.lock().map(|items| items.clone());
        for (item, drive) in items.unwrap_or_default() {
            if self.stopped() {
                return;
            }
            let value = match drive && !wanted(&self.wanted.keys(), &item.key) {
                true => None,
                false => item.read(),
            };
            if let Ok(mut found) = self.found.lock() {
                match value {
                    None => found.remove(&item.key),
                    Some(value) => {
                        found.insert(item.key, (self.began.elapsed().as_secs_f64(), value))
                    }
                };
            }
        }
    }

    fn run(&self) {
        while !self.stopped() {
            self.read_slow();
            if let Ok(mut first) = self.first.0.lock() {
                *first = true;
            }
            self.first.1.notify_all();
            let Ok(stop) = self.stop.0.lock() else {
                return;
            };
            let wait = Duration::from_secs_f64(SLOW_SECONDS);
            if self
                .stop
                .1
                .wait_timeout_while(stop, wait, |stop| !*stop)
                .is_ok_and(|(stop, _)| *stop)
            {
                return;
            }
        }
    }
}

/// Gives NVIDIA's library, started; none where there is none.
pub type Opener = Box<dyn Fn() -> Option<Arc<Nvml>> + Send>;
/// Gives the cards by the names `gpu.name` gives them: their PCI slots tie a name to the
/// folders of the kernel, so that the readings are those of the card that is named.
pub type Adapters = Box<dyn Fn() -> Vec<Adapter> + Send>;

/// Temperatures, fans, power, clocks and video memory of this Linux PC; see the module.
pub struct LinuxSource {
    root: PathBuf,
    opener: Opener,
    adapters: Adapters,
    prefer: String,
    use_nvml: bool,
    plan: Plan,
    scanned_at: Option<f64>,
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl LinuxSource {
    /// `prefer`: `gpu` under `[sensors.linux]` (or, as everywhere else, under
    /// `[sensors.librehardwaremonitor]`) picks the card by a part of its name; `nvidia`:
    /// `[sensors.linux] nvidia`, "off" for no NVML.
    pub fn new(prefer: &str, nvidia: &str, wanted: Wanted) -> LinuxSource {
        let adapters = || crate::hardware::scan().adapters.clone();
        LinuxSource::at(
            Path::new("/"),
            prefer,
            nvidia,
            wanted,
            Box::new(nvml::shared),
            Box::new(adapters),
        )
    }

    /// The source of a machine laid out in a folder, with a library and names of its own.
    pub fn at(
        root: &Path,
        prefer: &str,
        nvidia: &str,
        wanted: Wanted,
        opener: Opener,
        adapters: Adapters,
    ) -> LinuxSource {
        LinuxSource {
            root: root.to_path_buf(),
            opener,
            adapters,
            prefer: lp_py::strip(prefer).to_string(),
            use_nvml: lp_py::lower(lp_py::strip(nvidia)) != "off",
            plan: Plan::default(),
            scanned_at: None,
            shared: Arc::new(Shared {
                items: Mutex::new(Vec::new()),
                found: Mutex::new(HashMap::new()),
                first: (Mutex::new(false), Condvar::new()),
                stop: (Mutex::new(false), Condvar::new()),
                wanted,
                began: Instant::now(),
            }),
            thread: None,
        }
    }

    // -- finding what there is ---------------------------------------------------------

    /// Look at the chips and cards again; the plan is replaced if anything is new.
    pub fn scan(&mut self, now: f64) {
        self.scanned_at = Some(now);
        let mut chips = hwmon::scan(&self.root);
        if !chips.iter().any(|chip| !chip.of("temp").is_empty()) {
            chips.extend(hwmon::thermal_zones(&self.root));
        }
        let cards = gpus::find_cards(&self.root);
        let library = self.library(&cards);
        let counters = rapl::package_counters(&self.root);
        let signature = Signature {
            chips: chips
                .iter()
                .map(|chip| (chip.path.clone(), chip.name.clone(), chip.channels.len()))
                .collect(),
            cards: cards
                .iter()
                .map(|card| (card.slot.clone(), card.driver.clone(), card.hwmon.clone()))
                .collect(),
            library: library.is_some(),
            prefer: self.prefer.clone(),
            counters: counters
                .iter()
                .map(|counter| counter.path.clone())
                .collect(),
        };
        if self.plan.signature.as_ref() != Some(&signature) {
            self.plan = self.make_plan(signature, chips, cards, library, counters);
            self.start_slow();
        }
    }

    fn make_plan(
        &self,
        signature: Signature,
        chips: Vec<Chip>,
        cards: Vec<Card>,
        library: Option<Arc<Nvml>>,
        counters: Vec<rapl::Counter>,
    ) -> Plan {
        let items = hwmon::name_items(&chips);
        let places = |slow: bool| -> Vec<usize> {
            (0..items.len())
                .filter(|n| chips[items[*n].chip].slow() == slow)
                .collect()
        };
        let (fast, slow) = (places(false), places(true));
        let names = slow
            .iter()
            .map(|n| (items[*n].key.clone(), slow_name(&items[*n], &chips)))
            .collect();
        let keys = items
            .iter()
            .map(|item| (item.channel.clone(), item.key.clone()))
            .collect();
        let memory = (0..items.len())
            .filter(|n| {
                chips[items[*n].chip].kind() == ChipKind::Memory && items[*n].channel.kind == "temp"
            })
            .collect();
        let card = gpus::primary(&cards, &(self.adapters)(), &self.prefer)
            .and_then(|best| cards.iter().position(|card| std::ptr::eq(card, best)));
        let channels = card
            .map(|n| cards[n].hwmon.as_str())
            .filter(|folder| !folder.is_empty())
            .and_then(|folder| chips.iter().find(|chip| chip.folder() == folder))
            .map(gpu_channels);
        let mut nvidia = Vec::new();
        if let Some(library) = &library {
            let mine = (0..cards.len())
                .filter(|n| cards[*n].vendor == gpus::NVIDIA && cards[*n].driver == "nvidia");
            for (index, n) in mine.enumerate() {
                let by_slot = match cards[n].slot.as_str() {
                    "" => None,
                    slot => library.by_slot(slot),
                };
                // (the library lists them in bus order)
                if let Some(device) = by_slot.or_else(|| library.by_index(index as u32)) {
                    nvidia.push((n, device));
                }
            }
        }
        let sockets: Vec<rapl::Counter> = chips
            .iter()
            .filter(|chip| chip.name == "amd_energy")
            .flat_map(|chip| chip.of("energy"))
            .filter(|channel| lp_py::lower(&channel.label).starts_with("esocket"))
            .map(|channel| rapl::Counter {
                path: channel.path.clone(),
                wrap: 0,
            })
            .collect();
        let chip_power = chips
            .iter()
            .filter(|chip| POWER_CHIPS.contains(&chip.name.as_str()))
            .flat_map(|chip| chip.of("power"))
            .cloned()
            .collect();
        Plan {
            signature: Some(signature),
            cpu_temp: cpu_temp_item(&items, &chips),
            fast,
            slow,
            names,
            keys,
            memory,
            card,
            channels,
            nvidia,
            package_power: (!counters.is_empty()).then(|| Joules::new(counters)),
            socket_power: (!sockets.is_empty()).then(|| Joules::new(sockets)),
            chip_power,
            chips,
            items,
            cards,
        }
    }

    /// NVIDIA's library, asked for only where the proprietary driver runs a card that is on
    /// (starting the library would wake a card that sleeps).
    fn library(&self, cards: &[Card]) -> Option<Arc<Nvml>> {
        let awake = cards.iter().any(|card| {
            card.vendor == gpus::NVIDIA && card.driver == "nvidia" && !gpus::suspended(card)
        });
        if !self.use_nvml || !awake {
            return None;
        }
        (self.opener)()
    }

    // -- the thread of the slow ones ---------------------------------------------------

    fn start_slow(&mut self) {
        let slow: Vec<(Item, bool)> = self
            .plan
            .slow
            .iter()
            .map(|n| {
                let item = self.plan.items[*n].clone();
                let drive = self.plan.chips[item.chip].name == "drivetemp";
                (item, drive)
            })
            .collect();
        let none = slow.is_empty();
        if let Ok(mut items) = self.shared.items.lock() {
            *items = slow;
        }
        if none || self.shared.stopped() {
            return;
        }
        if self
            .thread
            .as_ref()
            .is_none_or(|thread| thread.is_finished())
        {
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-linux".into())
                .spawn(move || shared.run())
                .ok();
        }
    }

    // -- reading -----------------------------------------------------------------------

    pub fn read(&mut self) -> Readings {
        let now = self.shared.began.elapsed().as_secs_f64();
        self.read_at(now)
    }

    /// The readings at `now`, seconds on the source's clock.
    pub fn read_at(&mut self, now: f64) -> Readings {
        if self.scanned_at.is_none_or(|at| now - at >= RESCAN_SECONDS) {
            self.scan(now);
        }
        if !self.plan.slow.is_empty() && self.thread.is_some() {
            if let Ok(first) = self.shared.first.0.lock() {
                let wait = Duration::from_secs_f64(FIRST_PASS_SECONDS);
                let _ = self
                    .shared
                    .first
                    .1
                    .wait_timeout_while(first, wait, |first| !*first);
            }
        }
        let mut out = Readings::new();
        self.chips(&mut out);
        self.slow_readings(&mut out, now);
        self.temperatures(&mut out);
        self.power(&mut out, now);
        self.graphics(&mut out, now);
        out
    }

    /// Every temperature and fan, each under the key of its own.
    fn chips(&self, out: &mut Readings) {
        let plan = &self.plan;
        // (asking a card that is off would switch it on)
        let asleep: Vec<&str> = plan
            .cards
            .iter()
            .filter(|card| !card.hwmon.is_empty() && gpus::suspended(card))
            .map(|card| card.hwmon.as_str())
            .collect();
        for n in &plan.fast {
            let item = &plan.items[*n];
            if asleep.contains(&plan.chips[item.chip].folder().as_str()) {
                continue;
            }
            put(out, &item.key, item.read(), item.unit, &item.name);
        }
    }

    /// What the thread of the slow ones read last, unless that is too long ago.
    fn slow_readings(&self, out: &mut Readings, now: f64) {
        let found = self.shared.found.lock().map(|found| found.clone());
        let found = found.unwrap_or_default();
        for n in &self.plan.slow {
            let item = &self.plan.items[*n];
            if let Some((stamp, value)) = found.get(&item.key) {
                if now - stamp <= SLOW_FRESH {
                    put(
                        out,
                        &item.key,
                        Some(*value),
                        item.unit,
                        &self.plan.names[&item.key],
                    );
                }
            }
        }
    }

    fn temperatures(&self, out: &mut Readings) {
        let plan = &self.plan;
        if let Some(n) = plan.cpu_temp {
            alias(out, "cpu.temp", "CPU temperature", Some(&plan.items[n].key));
        }
        let mut top: Option<(&str, f64)> = None;
        for n in &plan.memory {
            let key = plan.items[*n].key.as_str();
            if let Some(Value::Float(value)) = out.get(key).map(|reading| &reading.value) {
                if top.is_none_or(|(_, best)| *value > best) {
                    top = Some((key, *value));
                }
            }
        }
        if let Some((key, value)) = top {
            let reading = Reading {
                unit: "°C".into(),
                label: "Memory temperature".into(),
                origin: key.to_string(),
                ..Reading::of(Value::Float(value))
            };
            out.insert("mem.temp".into(), reading);
        }
    }

    /// The processor's power: RAPL, else the sockets of amd_energy, else a chip's own.
    fn power(&mut self, out: &mut Readings, now: f64) {
        let plan = &mut self.plan;
        let mut watts = plan
            .package_power
            .as_mut()
            .and_then(|power| power.watts(now));
        if watts.is_none() {
            watts = plan
                .socket_power
                .as_mut()
                .and_then(|power| power.watts(now));
        }
        if watts.is_none() && !plan.chip_power.is_empty() {
            let values: Option<Vec<f64>> = plan.chip_power.iter().map(Channel::read).collect();
            watts = values.map(|values| values.into_iter().fold(0.0, |sum, value| sum + value));
        }
        put(out, "cpu.power", watts, "W", "CPU power");
    }

    // -- the graphics card -------------------------------------------------------------

    fn graphics(&mut self, out: &mut Readings, now: f64) {
        let plan = &mut self.plan;
        let mut nvidia: Vec<&str> = Vec::new();
        for (number, (n, device)) in plan.nvidia.iter().enumerate() {
            nvidia.push(plan.cards[*n].slot.as_str());
            if gpus::suspended(&plan.cards[*n]) {
                continue;
            }
            let values = device.read();
            nvidia_raw(out, number, &values);
            if Some(*n) == plan.card {
                nvidia_main(out, number, &values);
            }
        }
        let Some(card) = plan.card.map(|n| &plan.cards[n]) else {
            return;
        };
        if nvidia.contains(&card.slot.as_str()) || gpus::suspended(card) {
            return;
        }
        if let Some(channels) = &mut plan.channels {
            hwmon_graphics(channels, &plan.keys, out, now);
        }
        if card.driver == "amdgpu" {
            put(out, "gpu.load", gpus::busy_percent(card), "%", "GPU load");
            if let Some((used, total)) = gpus::video_memory(card) {
                let (used, total) = (used as f64, total as f64);
                put(
                    out,
                    "gpu.mem.used",
                    Some(used / MIB),
                    "MB",
                    "GPU memory used",
                );
                put(out, "gpu.mem.total", Some(total / MIB), "MB", "GPU memory");
                put(
                    out,
                    "gpu.mem.load",
                    Some(100.0 * used / total),
                    "%",
                    "GPU memory",
                );
            }
        }
        if !out.contains_key("gpu.freq") {
            // Intel's drivers keep the clock in the card's folder
            put(out, "gpu.freq", gpus::clock_mhz(card), "MHz", "GPU clock");
        }
    }

    pub fn status(&self) -> Status {
        let plan = &self.plan;
        let count = |kind: &str| plan.items.iter().filter(|i| i.channel.kind == kind).count();
        Status {
            chips: plan.chips.len(),
            temperatures: count("temp"),
            fans: count("fan"),
            graphics: plan.card.map(|n| plan.cards[n].driver.clone()),
            nvml: !plan.nvidia.is_empty(),
            cpu_power: if plan.package_power.is_some() {
                Some("RAPL")
            } else if plan.socket_power.is_some() || !plan.chip_power.is_empty() {
                Some("chip")
            } else {
                None
            },
        }
    }

    pub fn close(&mut self) {
        if let Ok(mut stop) = self.shared.stop.0.lock() {
            *stop = true;
        }
        self.shared.stop.1.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for LinuxSource {
    fn drop(&mut self) {
        self.close();
    }
}

/// A drive is named by what it is, its model, as Windows does; the kernel tells it.
fn slow_name(item: &Item, chips: &[Chip]) -> String {
    let chip = &chips[item.chip];
    if chip.kind() != ChipKind::Drive {
        return item.name.clone();
    }
    let text = |path: PathBuf| read_text(&path).filter(|text| !text.is_empty());
    let model =
        text(chip.base.join("device").join("model")).or_else(|| text(chip.base.join("model")));
    let Some(model) = model else {
        return item.name.clone();
    };
    let label = &item.channel.label;
    if !label.is_empty() && lp_py::lower(label) != "composite" {
        format!("{model} {label}")
    } else {
        model
    }
}

fn put(out: &mut Readings, key: &str, value: Option<f64>, unit: &str, label: &str) {
    if let Some(value) = value {
        let reading = Reading {
            unit: unit.into(),
            label: label.into(),
            ..Reading::of(Value::Float(value))
        };
        out.insert(key.to_string(), reading);
    }
}

/// `key` repeats the reading `source` under a friendlier name; lists show it once.
fn alias(out: &mut Readings, key: &str, label: &str, source: Option<&String>) {
    let Some(source) = source else {
        return;
    };
    if let Some(reading) = out.get(source) {
        let reading = Reading {
            unit: reading.unit.clone(),
            label: label.into(),
            origin: source.clone(),
            ..Reading::of(reading.value.clone())
        };
        out.insert(key.to_string(), reading);
    }
}

fn hwmon_graphics(
    channels: &mut GpuChannels,
    keys: &HashMap<Channel, String>,
    out: &mut Readings,
    now: f64,
) {
    for (key, label, channel) in [
        ("gpu.temp", "GPU temperature", &channels.temp),
        ("gpu.hotspot", "GPU hot spot", &channels.hotspot),
        (
            "gpu.mem.temp",
            "GPU memory temperature",
            &channels.memory_temp,
        ),
        ("gpu.fan", "GPU Fan", &channels.fan),
    ] {
        if let Some(channel) = channel {
            alias(out, key, label, keys.get(channel));
        }
    }
    let read = |channel: &Option<Channel>| channel.as_ref().and_then(Channel::read);
    if channels.pwm.is_some() {
        put(
            out,
            "gpu.fan.percent",
            read(&channels.pwm),
            "%",
            "GPU fan speed",
        );
    }
    let mut watts = read(&channels.power);
    if watts.is_none() {
        watts = channels
            .energy
            .as_mut()
            .and_then(|energy| energy.watts(now));
    }
    put(out, "gpu.power", watts, "W", "GPU power");
    put(out, "gpu.freq", read(&channels.clock), "MHz", "GPU clock");
    let clock = read(&channels.memory_clock);
    put(out, "gpu.mem.freq", clock, "MHz", "GPU memory clock");
}

fn suffix(number: usize) -> String {
    match number {
        0 => String::new(),
        number => format!("_{}", number + 1),
    }
}

/// A card's temperature (and its fan, where the driver tells RPM) under keys of their own,
/// as a hwmon chip's would be: `temp.nvidia.gpu`, `fan.nvidia.0`.
fn nvidia_raw(out: &mut Readings, number: usize, values: &nvml::Values) {
    let key = format!("temp.nvidia.gpu{}", suffix(number));
    put(out, &key, values.temp, "°C", "GPU temperature");
    let key = format!("fan.nvidia.{number}");
    put(out, &key, values.fan_rpm, "RPM", "GPU Fan");
}

fn nvidia_main(out: &mut Readings, number: usize, values: &nvml::Values) {
    let source = format!("temp.nvidia.gpu{}", suffix(number));
    alias(out, "gpu.temp", "GPU temperature", Some(&source));
    alias(
        out,
        "gpu.fan",
        "GPU Fan",
        Some(&format!("fan.nvidia.{number}")),
    );
    put(
        out,
        "gpu.fan.percent",
        values.fan_percent,
        "%",
        "GPU fan speed",
    );
    put(out, "gpu.power", values.power, "W", "GPU power");
    put(out, "gpu.load", values.load, "%", "GPU load");
    put(out, "gpu.mem.load", values.mem_load, "%", "GPU memory");
    put(
        out,
        "gpu.mem.used",
        values.mem_used,
        "MB",
        "GPU memory used",
    );
    put(out, "gpu.mem.total", values.mem_total, "MB", "GPU memory");
    put(out, "gpu.freq", values.freq, "MHz", "GPU clock");
    put(
        out,
        "gpu.mem.freq",
        values.mem_freq,
        "MHz",
        "GPU memory clock",
    );
}

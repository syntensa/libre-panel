//! The readings a frame is drawn of: the sources that are read (sensors/base.py's SensorHub
//! and its history) and the computer's own, under the names sensors/psutil_provider.py gives
//! them: the processor's load, clock and threads, the memory, the system's disk, the
//! network's and the disks' rates, how long the computer runs and the processor's
//! temperature (system.rs reads them from the system).

use std::collections::VecDeque;
use std::time::Instant;

use indexmap::IndexMap;
use lp_config::SensorsConfig;
use lp_py::toml::{Table, Toml};
use lp_py::Value;
use lp_render::{Moment, Reading, Snapshot};
use lp_sensors::service::Wanted;

/// the numbers a sensor's history holds (a graph's ten minutes)
pub const HISTORY_LENGTH: usize = 600;

pub type Readings = IndexMap<String, Reading>;

/// When a source's readings follow on every frame, not only with each snapshot.
#[derive(Clone, Copy, PartialEq)]
pub enum Every {
    No,
    /// its reading is cheap and its values must follow at once
    Frame,
    /// on every frame only where the panel shows video; without it each changed value would
    /// be one more picture sent
    Video,
}

/// Something readings come from (SensorProvider).
pub trait Source {
    fn name(&self) -> &'static str;
    /// The readings of now, each put under its key.
    fn read(&mut self, out: &mut Readings);
    /// What the source noted for the log since it was last asked.
    fn notes(&mut self) -> Vec<String> {
        Vec::new()
    }
    /// Waits until the source's first readings are in (or it gave up), `seconds` at most.
    fn wait_ready(&mut self, _seconds: f64) {}
    /// How LibreHardwareMonitor is read, where the source is the one that reads it.
    fn lhm_status(&self) -> Option<lp_sensors::service::LhmStatus> {
        None
    }
    /// Whether its readings follow on every frame.
    fn every_frame(&self) -> Every {
        Every::No
    }
}

/// The volume and the mute of the default output device, and the microphone's
/// (sensors/audio.py).
struct Audio(lp_sensors::audio::AudioSource);

impl Source for Audio {
    fn name(&self) -> &'static str {
        "audio"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }

    fn every_frame(&self) -> Every {
        Every::Frame
    }
}

/// The processor's, its cores' and the graphics card's load, ten times a second
/// (sensors/load.py).
struct Load(lp_sensors::load::LoadSource);

impl Source for Load {
    fn name(&self) -> &'static str {
        "load"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }

    fn every_frame(&self) -> Every {
        Every::Video
    }
}

/// LibreHardwareMonitor's readings (Windows): through Libre Panel's sensor service, or from
/// the web server of a LibreHardwareMonitor that runs by itself.
struct Lhm(lp_sensors::provider::Provider);

impl Lhm {
    /// `[sensors.librehardwaremonitor]` as the provider takes it; what is no such value is
    /// said and left at its default.
    fn new(table: Option<&Table>, wanted: &Wanted) -> (Lhm, Vec<String>) {
        use lp_sensors::provider::{LhmOptions, Provider, System as Own};
        let mut options = LhmOptions::default();
        let mut notes = Vec::new();
        for (key, value) in table.into_iter().flatten() {
            let number = match value {
                Toml::Float(f) => Some(*f),
                Toml::Int(n) => lp_py::num::int_to_float(n).ok(),
                _ => None,
            };
            match (key.as_str(), value, number) {
                ("url", Toml::Str(url), _) => options.url = url.clone(),
                ("gpu", Toml::Str(gpu), _) => options.gpu = Some(gpu.clone()),
                ("mode", Toml::Str(mode), _) => options.mode = mode.clone(),
                ("poll_seconds", _, Some(seconds)) => options.poll_seconds = seconds,
                ("stale_seconds", _, Some(seconds)) => options.stale_seconds = seconds,
                ("dll_dir", ..) => {} // (the Python app's, for LibreHardwareMonitor inside it)
                _ => notes.push(format!(
                    "[sensors.librehardwaremonitor] {key}: not a value this source takes"
                )),
            }
        }
        let provider = Provider::new(options, wanted.clone(), Own::own());
        (Lhm(provider), notes)
    }
}

impl Source for Lhm {
    fn name(&self) -> &'static str {
        "librehardwaremonitor"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }

    fn wait_ready(&mut self, seconds: f64) {
        self.0.wait_ready(seconds);
    }

    fn lhm_status(&self) -> Option<lp_sensors::service::LhmStatus> {
        Some(self.0.status())
    }

    // A new reading shows in the next frame, not at the next snapshot: three clocks of a
    // second each (the service, its client, the snapshot) made it late and uneven.
    fn every_frame(&self) -> Every {
        Every::Frame
    }
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

/// Collects the readings of all sources and keeps each number's history for the graphs
/// (SensorHub). A source earlier in the list wins where two have the same key.
pub struct Hub {
    sources: Vec<Box<dyn Source>>,
    history: IndexMap<String, VecDeque<f64>>,
    /// the keys the theme on show reads: a source may read only what is shown
    wanted: Wanted,
    notes: Vec<String>,
    /// which source each key of the last snapshot came from
    owners: IndexMap<String, usize>,
}

impl Hub {
    pub fn new(sources: Vec<Box<dyn Source>>) -> Hub {
        Hub {
            sources,
            history: IndexMap::new(),
            wanted: Wanted::default(),
            notes: Vec::new(),
            owners: IndexMap::new(),
        }
    }

    /// fresh: between snapshots, the latest values of the sources that follow on every frame,
    /// for the keys they supplied (or that nobody supplied); no history. Without `video`
    /// those that follow on every frame only in video mode are left out.
    pub fn fresh(&mut self, video: bool) -> Readings {
        let mut readings = Readings::new();
        for (n, source) in self.sources.iter_mut().enumerate() {
            match source.every_frame() {
                Every::No => continue,
                Every::Video if !video => continue,
                _ => {}
            }
            let mut found = Readings::new();
            source.read(&mut found);
            for (key, reading) in found {
                if self.owners.get(&key).is_none_or(|owner| *owner == n) {
                    readings.entry(key).or_insert(reading);
                }
            }
        }
        readings
    }

    /// Whether any source wants to be read on every frame.
    pub fn every_frame(&self) -> bool {
        self.sources
            .iter()
            .any(|source| source.every_frame() != Every::No)
    }

    /// The keys (and patterns) the theme on show reads.
    pub fn want(&self, keys: impl IntoIterator<Item = String>) {
        self.wanted.set(keys);
    }

    /// Sources that start in the background get the moment they need for their first
    /// readings.
    pub fn wait_ready(&mut self, seconds: f64) {
        for source in &mut self.sources {
            source.wait_ready(seconds);
        }
    }

    /// How LibreHardwareMonitor is read, where a source reads it.
    pub fn lhm_status(&self) -> Option<lp_sensors::service::LhmStatus> {
        self.sources.iter().find_map(|source| source.lhm_status())
    }

    /// What the sources noted for the log since they were last asked.
    pub fn notes(&mut self) -> Vec<String> {
        let mut notes = std::mem::take(&mut self.notes);
        for source in &mut self.sources {
            notes.extend(source.notes());
        }
        notes
    }

    /// The sources config.toml names, as far as this build has them; the names of those it
    /// has not.
    pub fn of(sensors: &SensorsConfig, demo: bool) -> (Hub, Vec<String>) {
        let mut sources: Vec<Box<dyn Source>> = Vec::new();
        let mut missing = Vec::new();
        let mut notes = Vec::new();
        let wanted = Wanted::default();
        if demo {
            sources.push(Box::new(Demo::new()));
        } else {
            for name in &sensors.providers {
                match name.as_str() {
                    "psutil" => sources.push(Box::new(crate::psutil::System::new(
                        sensors.options.get(name),
                        wanted.clone(),
                    ))),
                    "load" => sources.push(Box::new(Load(lp_sensors::load::LoadSource::new(
                        wanted.clone(),
                    )))),
                    "audio" => sources.push(Box::new(Audio(lp_sensors::audio::AudioSource::new(
                        wanted.clone(),
                    )))),
                    "librehardwaremonitor" => {
                        let (source, said) = Lhm::new(sensors.options.get(name), &wanted);
                        sources.push(Box::new(source));
                        notes.extend(said);
                    }
                    other => missing.push(other.to_string()),
                }
            }
        }
        let hub = Hub {
            wanted,
            notes,
            ..Hub::new(sources)
        };
        (hub, missing)
    }

    /// The sources that are read, by their names.
    pub fn names(&self) -> Vec<&'static str> {
        self.sources.iter().map(|source| source.name()).collect()
    }

    /// snapshot: every source read once, the numbers among the readings added to their
    /// histories, at the moment `now`.
    pub fn snapshot(&mut self, now: Moment, fold: bool) -> Snapshot {
        let mut readings = Readings::new();
        let mut owners = IndexMap::new();
        for (n, source) in self.sources.iter_mut().enumerate() {
            let mut found = Readings::new();
            source.read(&mut found);
            for (key, reading) in found {
                if !readings.contains_key(&key) {
                    owners.insert(key.clone(), n);
                    readings.insert(key, reading);
                }
            }
        }
        self.owners = owners;
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
